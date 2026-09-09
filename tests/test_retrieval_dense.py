"""Dense retrieval is local, normalized, bounded, and fail-closed."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from numpy.typing import NDArray

import sabermetrics.substrate.dense as dense_module
from sabermetrics.substrate.dense import (
    DenseEncoder,
    DenseEncodingError,
    DenseIndex,
    DenseIndexAbsentError,
    DenseIndexCorruptError,
    DenseModelAbsentError,
    DenseModelMismatchError,
    DenseQueryError,
    LocalSentenceTransformerEncoder,
    build_dense_index,
)
from sabermetrics.substrate.settings import EmbeddingModelSettings

CARD_A = "00000000-0000-0000-0000-000000000001"
CARD_B = "00000000-0000-0000-0000-000000000002"
CARD_C = "00000000-0000-0000-0000-000000000003"


class FakeEncoder:
    """Deterministic protocol implementation with observable input."""

    def __init__(
        self,
        rows: Sequence[Sequence[float]],
        *,
        dimensions: int = 2,
    ) -> None:
        self._rows = np.asarray(rows)
        self._dimensions = dimensions
        self.calls: list[tuple[tuple[str, ...], int]] = []

    @property
    def dimensions(self) -> int:
        """Return the configured fake output dimensions."""
        return self._dimensions

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int,
    ) -> NDArray[np.float32]:
        """Return the next deterministic rows and record literal input."""
        self.calls.append((tuple(texts), batch_size))
        return np.asarray(self._rows, dtype=np.float32)


def _settings(tmp_path: Path, *, dimensions: int = 2) -> EmbeddingModelSettings:
    return EmbeddingModelSettings(
        model_id="BAAI/bge-small-en-v1.5",
        revision="a" * 40,
        local_dir=tmp_path / "model",
        dimensions=dimensions,
        normalize=True,
        query_prefix="Represent this sentence for searching relevant passages: ",
        batch_size=7,
        device="cpu",
    )


def _write_vectors(path: Path, rows: Sequence[Sequence[float]]) -> None:
    np.save(path, np.asarray(rows, dtype=np.float32), allow_pickle=False)


def _open(
    path: Path,
    oracle_ids: Sequence[str],
    query_rows: Sequence[Sequence[float]],
    settings: EmbeddingModelSettings,
) -> tuple[DenseIndex, FakeEncoder]:
    encoder = FakeEncoder(query_rows, dimensions=settings.dimensions)
    return (
        DenseIndex(path, oracle_ids, encoder=encoder, settings=settings),
        encoder,
    )


def test_build_writes_normalized_float32_vectors_atomically(tmp_path, monkeypatch):
    path = tmp_path / "card-vectors.npy"
    encoder = FakeEncoder([[3.0, 4.0], [0.0, -2.0]])
    settings = _settings(tmp_path)
    replacements: list[tuple[Path, Path, bool]] = []
    real_replace = dense_module.os.replace

    def observe_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        replacements.append((source_path, destination_path, destination_path.exists()))
        assert source_path.parent == path.parent
        assert isinstance(np.load(source_path, mmap_mode="r"), np.memmap)
        real_replace(source, destination)

    monkeypatch.setattr(dense_module.os, "replace", observe_replace)
    build_dense_index(
        path,
        ["first document", "second document"],
        encoder=encoder,
        settings=settings,
    )

    vectors = np.load(path)
    assert replacements == [(replacements[0][0], path, False)]
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(vectors, [[0.6, 0.8], [0.0, -1.0]])
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0])
    assert encoder.calls == [
        (("first document", "second document"), settings.batch_size)
    ]
    assert not list(tmp_path.glob(".card-vectors.npy.*"))


def test_failed_atomic_write_preserves_previous_index(tmp_path, monkeypatch):
    path = tmp_path / "card-vectors.npy"
    path.write_bytes(b"previous complete index")

    def fail_after_partial_write(handle, matrix, *, allow_pickle):
        handle.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(dense_module.np, "save", fail_after_partial_write)
    with pytest.raises(OSError, match="disk full"):
        build_dense_index(
            path,
            ["document"],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=_settings(tmp_path),
        )

    assert path.read_bytes() == b"previous complete index"
    assert not list(tmp_path.glob(".card-vectors.npy.*"))


def test_index_is_memory_mapped_and_preserves_vector_offset_identity(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[0.0, 1.0], [1.0, 0.0]])
    settings = _settings(tmp_path)
    index, _ = _open(path, [CARD_B, CARD_A], [[1.0, 0.0]], settings)

    hits = index.search("artifact acceleration", top_k=2)

    assert isinstance(index.vectors, np.memmap)
    assert index.vectors.mode == "r"
    assert index.oracle_ids == (CARD_B, CARD_A)
    assert [hit.oracle_id for hit in hits] == [CARD_A, CARD_B]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 0.0])


def test_query_uses_bge_prefix_and_normalizes_before_cosine_dot_product(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0], [0.0, 1.0]])
    settings = _settings(tmp_path)
    index, encoder = _open(path, [CARD_A, CARD_B], [[3.0, 4.0]], settings)

    hits = index.search("mana acceleration", top_k=2)

    assert encoder.calls == [
        (
            (
                "Represent this sentence for searching relevant passages: "
                "mana acceleration",
            ),
            settings.batch_size,
        )
    ]
    assert [hit.score for hit in hits] == pytest.approx([0.8, 0.6])
    assert [hit.oracle_id for hit in hits] == [CARD_B, CARD_A]


def test_candidate_allowlist_is_applied_before_top_k(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]])
    settings = _settings(tmp_path)
    index, _ = _open(
        path,
        [CARD_A, CARD_B, CARD_C],
        [[1.0, 0.0]],
        settings,
    )

    hits = index.search(
        "fast mana",
        candidate_oracle_ids={CARD_B, CARD_C},
        top_k=1,
    )

    assert hits[0].oracle_id == CARD_B
    assert index.search("fast mana", candidate_oracle_ids=set(), top_k=1) == ()


def test_complete_score_ties_are_broken_by_oracle_id(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0], [1.0, 0.0]])
    settings = _settings(tmp_path)
    index, _ = _open(path, [CARD_B, CARD_A], [[1.0, 0.0]], settings)

    hits = index.search("same score", top_k=2)

    assert [hit.oracle_id for hit in hits] == [CARD_A, CARD_B]


def test_malicious_query_is_only_literal_prefixed_encoder_input(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    index, encoder = _open(path, [CARD_A], [[1.0, 0.0]], settings)
    query = '"; DROP TABLE card_document; -- ../../models'

    assert index.search(query, top_k=1)[0].oracle_id == CARD_A
    assert encoder.calls[0][0] == (settings.query_prefix + query,)
    assert path.is_file()


@pytest.mark.parametrize("query", ["", " ", "\n\t"])
def test_empty_query_is_rejected_without_encoding(tmp_path, query):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    index, encoder = _open(path, [CARD_A], [[1.0, 0.0]], settings)

    with pytest.raises(DenseQueryError, match="must not be empty"):
        index.search(query, top_k=1)
    assert encoder.calls == []


def test_query_length_is_bounded_before_encoding(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    index, encoder = _open(path, [CARD_A], [[1.0, 0.0]], settings)

    with pytest.raises(DenseQueryError, match="exceeds 500"):
        index.search("x" * 501, top_k=1)
    assert encoder.calls == []


def test_fake_satisfies_encoder_protocol():
    assert isinstance(FakeEncoder([[1.0, 0.0]]), DenseEncoder)


def test_encoder_and_stored_dimensions_are_validated(tmp_path):
    settings = _settings(tmp_path)
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])

    with pytest.raises(DenseModelMismatchError, match="encoder dimensions"):
        build_dense_index(
            tmp_path / "unused.npy",
            ["document"],
            encoder=FakeEncoder([[1.0, 0.0]], dimensions=3),
            settings=settings,
        )

    _write_vectors(path, [[1.0, 0.0, 0.0]])
    with pytest.raises(DenseModelMismatchError, match="index dimensions"):
        DenseIndex(
            path,
            [CARD_A],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=settings,
        )


@pytest.mark.parametrize(
    "rows, message",
    [
        ([[np.nan, 1.0]], "non-finite"),
        ([[np.inf, 1.0]], "non-finite"),
        ([[0.0, 0.0]], "zero"),
        ([[1.0, 0.0, 0.0]], "shape"),
    ],
)
def test_bad_document_encoder_output_is_rejected(tmp_path, rows, message):
    with pytest.raises(DenseEncodingError, match=message):
        build_dense_index(
            tmp_path / "vectors.npy",
            ["document"],
            encoder=FakeEncoder(rows),
            settings=_settings(tmp_path),
        )


@pytest.mark.parametrize(
    "rows, message",
    [
        ([[np.nan, 1.0]], "non-finite"),
        ([[0.0, 0.0]], "zero"),
        ([[1.0, 0.0, 0.0]], "shape"),
    ],
)
def test_bad_query_encoder_output_is_rejected(tmp_path, rows, message):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    index, _ = _open(path, [CARD_A], rows, settings)

    with pytest.raises(DenseEncodingError, match=message):
        index.search("query", top_k=1)


@pytest.mark.parametrize(
    "rows, error, message",
    [
        ([[np.nan, 1.0]], DenseIndexCorruptError, "non-finite"),
        ([[0.0, 0.0]], DenseIndexCorruptError, "zero"),
        ([[2.0, 0.0]], DenseIndexCorruptError, "not normalized"),
    ],
)
def test_bad_stored_vectors_are_rejected(tmp_path, rows, error, message):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, rows)
    with pytest.raises(error, match=message):
        DenseIndex(
            path,
            [CARD_A],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=_settings(tmp_path),
        )


def test_vector_count_and_dtype_are_validated(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    with pytest.raises(DenseIndexCorruptError, match="vector count"):
        DenseIndex(
            path,
            [CARD_A, CARD_B],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=settings,
        )

    np.save(path, np.asarray([[1.0, 0.0]], dtype=np.float64))
    with pytest.raises(DenseIndexCorruptError, match="float32"):
        DenseIndex(
            path,
            [CARD_A],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=settings,
        )


def test_missing_and_corrupt_indexes_have_explicit_errors(tmp_path):
    settings = _settings(tmp_path)
    missing = tmp_path / "missing.npy"
    with pytest.raises(DenseIndexAbsentError, match="missing"):
        DenseIndex(
            missing,
            [CARD_A],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=settings,
        )

    corrupt = tmp_path / "corrupt.npy"
    corrupt.write_bytes(b"not a numpy file")
    with pytest.raises(DenseIndexCorruptError, match="cannot open"):
        DenseIndex(
            corrupt,
            [CARD_A],
            encoder=FakeEncoder([[1.0, 0.0]]),
            settings=settings,
        )


def test_unknown_allowlist_identity_is_a_snapshot_error(tmp_path):
    path = tmp_path / "vectors.npy"
    _write_vectors(path, [[1.0, 0.0]])
    settings = _settings(tmp_path)
    index, _ = _open(path, [CARD_A], [[1.0, 0.0]], settings)

    with pytest.raises(DenseIndexCorruptError, match="absent"):
        index.search("query", candidate_oracle_ids={CARD_B}, top_k=1)


def test_local_sentence_transformer_is_lazy_and_strictly_offline(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    settings = _settings(tmp_path)
    constructor_calls: list[tuple[str, dict[str, object]]] = []

    class FakeSentenceTransformer:
        def __init__(self, path: str, **kwargs: object) -> None:
            constructor_calls.append((path, kwargs))

        def get_sentence_embedding_dimension(self) -> int:
            return 2

        def encode(self, texts: list[str], **kwargs: object) -> NDArray[np.float32]:
            assert texts == ["document"]
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

    monkeypatch.setattr(
        dense_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    encoder = LocalSentenceTransformerEncoder(settings)
    assert constructor_calls == []

    assert encoder.encode(["document"], batch_size=3).shape == (1, 2)
    assert constructor_calls == [
        (
            str(model_dir.resolve()),
            {
                "device": "cpu",
                "local_files_only": True,
                "trust_remote_code": False,
            },
        )
    ]


def test_absent_local_model_has_an_explicit_error(tmp_path):
    encoder = LocalSentenceTransformerEncoder(_settings(tmp_path))

    with pytest.raises(DenseModelAbsentError, match="directory missing"):
        _ = encoder.dimensions


def test_local_model_dimension_mismatch_is_explicit(tmp_path, monkeypatch):
    (tmp_path / "model").mkdir()

    class WrongDimensionModel:
        def __init__(self, path: str, **kwargs: object) -> None:
            pass

        def get_sentence_embedding_dimension(self) -> int:
            return 3

    monkeypatch.setattr(
        dense_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(SentenceTransformer=WrongDimensionModel),
    )
    encoder = LocalSentenceTransformerEncoder(_settings(tmp_path))

    with pytest.raises(DenseModelMismatchError, match="configured dimensions"):
        _ = encoder.dimensions
