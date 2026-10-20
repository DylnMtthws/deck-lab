import json

from sabermetrics.assistant_card_views import prepare_card_view_vectors
from sabermetrics.assistant_retrieval_context import (
    enrich_shortlist,
    recall_view_scores,
)
from tests.test_assistant_card_views import Encoder


def test_semantic_only_view_candidate_is_available_before_shortlist(
    monkeypatch, tmp_path
):
    import sabermetrics.reference_layer.strategy_corpus as corpus

    cards = {
        "id": {
            "oracle_id": "id",
            "name": "Example",
            "type_line": "Artifact",
            "oracle_text": "Sacrifice a creature: Draw a card.",
        }
    }
    encoder = Encoder()
    artifact = prepare_card_view_vectors(list(cards.values()), encoder=encoder)
    path = tmp_path / "views.json"
    path.write_text(json.dumps(artifact))
    monkeypatch.setenv("SABER_ASSISTANT_CARD_VIEW_VECTORS", str(path))
    monkeypatch.setattr(corpus, "local_embedding_service", lambda: encoder)
    scores, receipt = recall_view_scores(cards, "resource conversion")
    assert scores["id"] == 1
    assert receipt["status"] == "available"
    assert receipt["indexed_eligible_cards"] == 1
    # New candidates can only come from the caller's current legal pool.
    assert recall_view_scores({}, "resource conversion")[0] == {}
    changed = {"id": {**cards["id"], "oracle_text": "Destroy target creature."}}
    assert recall_view_scores(changed, "resource conversion")[0] == {}


def test_unconfigured_or_oversized_artifact_keeps_lexical_fallback(
    monkeypatch, tmp_path
):
    from sabermetrics.assistant_retrieval_context import MAX_ARTIFACT_BYTES

    monkeypatch.delenv("SABER_ASSISTANT_CARD_VIEW_VECTORS", raising=False)
    cards = [
        {
            "oracle_id": "id",
            "name": "Draw",
            "oracle_text": "Draw a card.",
            "type_line": "Sorcery",
        }
    ]
    assert enrich_shortlist(cards, "draw")["mode"] == "lexical"
    assert cards[0]["retrieval_views"]["functional_roles"]
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(MAX_ARTIFACT_BYTES + 1)
    monkeypatch.setenv("SABER_ASSISTANT_CARD_VIEW_VECTORS", str(path))
    assert recall_view_scores({"id": cards[0]}, "draw")[1]["status"] == "unavailable"


def test_semantic_nomination_is_independent_of_sixty_four_card_batches(
    monkeypatch, tmp_path
):
    import sabermetrics.reference_layer.strategy_corpus as corpus

    cards = {
        str(i): {
            "oracle_id": str(i),
            "name": "Example " + str(i),
            "type_line": "Artifact",
            "oracle_text": "Sacrifice a creature: Draw a card.",
        }
        for i in range(100)
    }
    encoder = Encoder()
    file = tmp_path / "batch-views.json"
    file.write_text(
        json.dumps(prepare_card_view_vectors(list(cards.values()), encoder=encoder))
    )
    monkeypatch.setenv("SABER_ASSISTANT_CARD_VIEW_VECTORS", str(file))
    monkeypatch.setattr(corpus, "local_embedding_service", lambda: encoder)
    first, _ = recall_view_scores(cards, "resource conversion")
    second, _ = recall_view_scores(
        dict(reversed(list(cards.items()))), "resource conversion"
    )
    assert len(first) == 100
    assert first == second
