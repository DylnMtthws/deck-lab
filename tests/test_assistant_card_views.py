from sabermetrics.assistant_card_views import build_card_views, retrieve_card_views


def test_source_and_inference_stay_distinct_and_hashes_track_facts_and_revision():
    card = {
        "name": "Example",
        "oracle_id": "id",
        "oracle_text": "{1}, Sacrifice a creature: Draw a card.",
        "mana_cost": "{2}",
        "type_line": "Artifact",
        "source_version": "v1",
    }
    view = build_card_views(card)
    assert view == build_card_views(dict(card))
    assert (
        view["views"]["printed_effect"]["facts"]["oracle_text"] == card["oracle_text"]
    )
    assert view["views"]["prerequisite"]["evidence_class"] == "generated_inference"
    assert (
        "activation_cost:{1}, Sacrifice a creature"
        in view["views"]["prerequisite"]["draw_routes"][0]["prerequisites"]
    )
    changed = build_card_views({**card, "source_version": "v2"})
    assert all(
        view["views"][key]["view_hash"] != changed["views"][key]["view_hash"]
        for key in view["views"]
    )
    assert retrieve_card_views([card], "draw")["results"]
    assert retrieve_card_views([card], "unrelated")["results"] == []


def test_unsupported_multiface_keeps_text_but_does_not_infer_roles():
    card = {
        "name": "A // B",
        "type_line": "Creature // Sorcery",
        "card_faces": [{"oracle_text": "Draw a card."}],
    }
    view = build_card_views(card)
    assert view["views"]["printed_effect"]["facts"]["card_faces"] == card["card_faces"]
    assert view["views"]["functional_role"]["roles"] == []
    assert view["views"]["prerequisite"]["draw_routes"] == []


class Encoder:
    _model_name = "pinned-test-encoder@v1"

    def __init__(self):
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return (
            [1.0, 0.0]
            if "sacrifice" in text.casefold() or text == "resource conversion"
            else [0.0, 1.0]
        )


def test_offline_vectors_are_separate_and_semantic_retrieval_encodes_only_query():
    import json

    from sabermetrics.assistant_card_views import prepare_card_view_vectors

    cards = [
        {
            "name": "Example",
            "oracle_id": "id",
            "oracle_text": "{1}, Sacrifice a creature: Draw a card.",
            "type_line": "Artifact",
        }
    ]
    encoder = Encoder()
    artifact = json.loads(json.dumps(prepare_card_view_vectors(cards, encoder=encoder)))
    assert {row["view"] for row in artifact["records"]} == {
        "printed_effect",
        "functional_role",
        "prerequisite",
    }
    assert len({row["view_hash"] for row in artifact["records"]}) == 3
    encoder.calls.clear()
    result = retrieve_card_views(
        cards, "resource conversion", vector_artifact=artifact, encoder=encoder
    )
    assert result["mode"] == "hybrid"
    assert result["results"][0]["view_cosines"]["prerequisite"] == 1.0
    assert "semantic:prerequisite" in result["results"][0]["matched_channels"]
    assert encoder.calls == ["resource conversion"]
    # No artifacts are recreated at query time; mismatched facts abstain.
    changed = [{**cards[0], "source_version": "new"}]
    result = retrieve_card_views(
        changed, "resource conversion", vector_artifact=artifact, encoder=encoder
    )
    assert result["mode"] == "lexical"
    assert result["results"] == []
    artifact["embedding_model"] = "stale"
    encoder.calls.clear()
    result = retrieve_card_views(
        cards, "draw", vector_artifact=artifact, encoder=encoder
    )
    assert result["mode"] == "lexical" and result["results"]
    assert encoder.calls == []


def test_invalid_vectors_cannot_promote_results():
    from sabermetrics.assistant_card_views import prepare_card_view_vectors

    cards = [
        {
            "name": "Example",
            "oracle_text": "Sacrifice a creature: Draw a card.",
            "type_line": "Artifact",
        }
    ]
    encoder = Encoder()
    artifact = prepare_card_view_vectors(cards, encoder=encoder)
    for record in artifact["records"]:
        record["vector"] = [float("nan"), 0.0]
    assert (
        retrieve_card_views(
            cards, "resource conversion", vector_artifact=artifact, encoder=encoder
        )["results"]
        == []
    )


def test_completeness_layout_and_power_are_bound_into_every_view():
    original = {
        "oracle_id": "o",
        "name": "Example",
        "type_line": "Creature",
        "oracle_text": "Sacrifice a creature: Draw a card.",
        "power": "1",
        "layout": "normal",
    }
    baseline = build_card_views(original)
    assert baseline["views"]["functional_role"]["roles"]
    for change in (
        {"oracle_text_truncated": True},
        {"layout": "transform"},
        {"power": "99"},
    ):
        changed = build_card_views({**original, **change})
        assert all(
            changed["views"][view]["view_hash"] != baseline["views"][view]["view_hash"]
            for view in baseline["views"]
        )
        if "power" not in change:
            assert changed["views"]["functional_role"]["roles"] == []
            assert changed["views"]["prerequisite"]["draw_routes"] == []
