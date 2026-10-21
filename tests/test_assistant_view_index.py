"""Bounded synthetic acceptance tests; no model download or training."""

import json
import sqlite3
from pathlib import Path

import pytest

from sabermetrics.assistant_card_views import build_card_views
from sabermetrics.assistant_knowledge_refresh import iter_card_file
from sabermetrics.assistant_retrieval_context import (
    MAX_ARTIFACT_BYTES,
    recall_view_scores,
)
from sabermetrics.assistant_view_index import ViewQuerySession, refresh_view_index
from tests.test_assistant_card_views import Encoder


def cards(count=1, *, wide=False):
    for i in range(count):
        yield {
            "oracle_id": f"{i:05}" + ("x" * 600 if wide else ""),
            "name": f"Example {i}",
            "type_line": "Artifact",
            "oracle_text": "Sacrifice a creature: Draw a card.",
        }


def activate(monkeypatch, path, encoder):
    import sabermetrics.reference_layer.strategy_corpus as corpus

    monkeypatch.setenv("SABER_ASSISTANT_CARD_VIEW_VECTORS", str(path))
    monkeypatch.setattr(corpus, "local_embedding_service", lambda: encoder)


def test_refresh_cannot_replace_an_unrelated_database(tmp_path):
    path = tmp_path / "precious.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE irreplaceable_records (value TEXT)")
        db.execute("INSERT INTO irreplaceable_records VALUES ('keep')")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="owned card view index"):
        refresh_view_index(cards(), path, encoder=Encoder())
    assert path.read_bytes() == original


@pytest.mark.parametrize("malformed", ["not-a-blob", None, 42])
def test_storage_type_corruption_abstains_and_is_reencoded(
    tmp_path, monkeypatch, malformed
):
    path = tmp_path / "views.sqlite"
    encoder = Encoder()
    current = list(cards())
    refresh_view_index(current, path, encoder=encoder)
    activate(monkeypatch, path, encoder)
    with sqlite3.connect(path) as db:
        if malformed is None:
            # The schema's NOT NULL constraint already rejects this corruption.
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE vectors SET vector=NULL")
            return
        db.execute("UPDATE vectors SET vector=?", (malformed,))
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            0
        ]
        == {}
    )
    repaired = refresh_view_index(current, path, encoder=encoder)
    assert repaired["counts"]["encoded_rows"] == 3
    assert repaired["counts"]["reused_rows"] == 0
    assert recall_view_scores(
        {c["oracle_id"]: c for c in current}, "resource conversion"
    )[0]


def test_full_scale_beyond_legacy_limits_streams_and_encodes_query_once(
    tmp_path, monkeypatch
):
    path = tmp_path / "views.sqlite"
    encoder = Encoder()
    first = refresh_view_index(cards(3400, wide=True), path, encoder=encoder)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM vectors").fetchone()[0] > 10000
    assert path.stat().st_size > MAX_ARTIFACT_BYTES
    current = {card["oracle_id"]: card for card in cards(3400, wide=True)}
    activate(monkeypatch, path, encoder)
    encoder.calls.clear()
    # SQLite must never take the whole-artifact JSON loading path.
    monkeypatch.setattr(
        Path, "read_bytes", lambda *a, **k: pytest.fail("whole artifact read")
    )
    scores, receipt = recall_view_scores(current, "resource conversion")
    assert encoder.calls == ["resource conversion"]
    assert len(scores) == 24
    assert list(scores) == sorted(current)[:24]
    assert receipt["indexed_eligible_cards"] == 3400
    assert receipt["generation"] == first["generation"]
    encoder.calls.clear()
    repeated = refresh_view_index(cards(3400, wide=True), path, encoder=encoder)
    assert repeated["unchanged"]
    assert repeated["counts"]["encoded_rows"] == 0
    assert repeated["counts"]["reused_rows"] == 10200
    assert not encoder.calls


def test_incremental_permutation_fact_model_and_dimension_guards(tmp_path, monkeypatch):
    path = tmp_path / "views.sqlite"
    current = list(cards(90))
    encoder = Encoder()
    first = refresh_view_index(current, path, encoder=encoder)
    reversed_generation = refresh_view_index(reversed(current), path, encoder=encoder)
    assert first["generation"] == reversed_generation["generation"]
    activate(monkeypatch, path, encoder)
    a, _ = recall_view_scores(
        {c["oracle_id"]: c for c in current}, "resource conversion"
    )
    b, _ = recall_view_scores(
        {c["oracle_id"]: c for c in reversed(current)}, "resource conversion"
    )
    assert a == b
    changed = {**current[0], "power": "99"}
    assert (
        recall_view_scores({changed["oracle_id"]: changed}, "resource conversion")[0]
        == {}
    )
    current[0] = changed
    encoder.calls.clear()
    rebuilt = refresh_view_index(current, path, encoder=encoder)
    assert rebuilt["counts"]["stale"] == 1
    assert rebuilt["counts"]["encoded_rows"] == 3
    assert rebuilt["counts"]["reused_rows"] == 267
    encoder._model_name = "other-model"
    encoder.calls.clear()
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            0
        ]
        == {}
    )
    assert encoder.calls == []
    encoder._model_name = "pinned-test-encoder@v1"
    encoder.embed = lambda text: [1, 0, 0]
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            1
        ]["status"]
        == "unavailable"
    )


def test_corrupt_hash_vector_and_text_rows_abstain_and_repair(tmp_path, monkeypatch):
    path = tmp_path / "views.sqlite"
    encoder = Encoder()
    current = list(cards())
    refresh_view_index(current, path, encoder=encoder)
    activate(monkeypatch, path, encoder)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE vectors SET vector=zeroblob(8)")
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            0
        ]
        == {}
    )
    repaired = refresh_view_index(current, path, encoder=encoder)
    assert repaired["counts"]["encoded_rows"] == 3
    with sqlite3.connect(path) as db:
        db.execute("UPDATE vectors SET text_sha256='corrupt'")
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            0
        ]
        == {}
    )
    refresh_view_index(current, path, encoder=encoder)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET payload='{}'")
    assert (
        recall_view_scores({c["oracle_id"]: c for c in current}, "resource conversion")[
            1
        ]["status"]
        == "unavailable"
    )


def test_interruption_never_publishes_and_open_reader_keeps_old_generation(tmp_path):
    path = tmp_path / "views.sqlite"
    encoder = Encoder()
    original = list(cards())
    first = refresh_view_index(original, path, encoder=encoder)
    reader = ViewQuerySession(path, "resource conversion", encoder=encoder)
    old_bytes = path.read_bytes()

    def interrupted():
        yield next(cards())
        raise KeyboardInterrupt("simulated cancellation")

    with pytest.raises(KeyboardInterrupt):
        refresh_view_index(interrupted(), path, encoder=encoder)
    assert path.read_bytes() == old_bytes
    assert not list(tmp_path.glob("*.tmp"))
    changed = [{**original[0], "source_version": "new"}]
    second = refresh_view_index(changed, path, encoder=encoder)
    assert second["previous_generation"] == first["generation"]
    assert second["generation"] != first["generation"]
    assert reader.similarities(build_card_views(original[0]))
    assert reader.similarities(build_card_views(changed[0])) == {}
    reader.close()
    with pytest.raises(ValueError):
        refresh_view_index([*original, *original], path, encoder=encoder)


def test_empty_generated_views_cannot_nominate_and_eligibility_is_authoritative(
    tmp_path, monkeypatch
):
    class EmptyEncoder(Encoder):
        def embed(self, text):
            return [1, 0] if text in {"unrelated", "[]", ""} else [0, 1]

    path = tmp_path / "views.sqlite"
    encoder = EmptyEncoder()
    current = {
        "o": {
            "oracle_id": "o",
            "name": "Example",
            "oracle_text": "Flying",
            "layout": "transform",
        }
    }
    receipt = refresh_view_index(current.values(), path, encoder=encoder)
    assert receipt["counts"]["encoded_rows"] == 1
    assert receipt["counts"]["unsupported"] == 1
    activate(monkeypatch, path, encoder)
    assert recall_view_scores(current, "unrelated")[0] == {}
    assert recall_view_scores({}, "Example")[0] == {}


@pytest.mark.parametrize(
    "contents",
    ['[{"name":"a"},]', '[{"name":"a"}] trailing', '[{"name":', '{"data":[]}'],
)
def test_streaming_array_rejects_invalid_inputs(tmp_path, contents):
    path = tmp_path / "cards.json"
    path.write_text(contents)
    with pytest.raises(ValueError):
        list(iter_card_file(path))


def test_streaming_card_inputs_and_record_bound(tmp_path):
    from sabermetrics.assistant_view_index import MAX_TEXT_BYTES

    path = tmp_path / "cards.json"
    values = list(cards(100))
    path.write_text(json.dumps(values, ensure_ascii=False))
    assert list(iter_card_file(path)) == values
    path.write_text('[{"name":"' + "x" * (MAX_TEXT_BYTES + 1))
    with pytest.raises(ValueError, match="bound"):
        list(iter_card_file(path))
