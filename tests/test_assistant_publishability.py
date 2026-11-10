"""Early findings require actual source fields and exact scoped relevance."""

from copy import deepcopy

from sabermetrics.assistant_obligations import build_obligations
from sabermetrics.assistant_publishability import early_findings


def card_receipt():
    return {
        "status": "canonical_snapshot",
        "card": {
            "id": "catalog-id",
            "oracle_id": "oracle-id",
            "name": "Basalt Monolith",
            "oracle_text": "Basalt Monolith doesn't untap during your untap step.\n{T}: Add {C}{C}{C}.\n{3}: Untap Basalt Monolith.",
            "scryfall_uri": "https://example.test/card",
        },
        "provenance": {
            "source": "scryfall_snapshot",
            "version": "2026-11",
            "content_hash": "a" * 64,
        },
        "truncation": {"characters": 0, "items": 0},
    }


def test_exact_named_card_source_renders_literal_text_with_limitations():
    obligations = build_obligations("What does Basalt Monolith say?", 2)
    source = card_receipt()
    source["card"]["oracle_text"] += "\n<script>untrusted</script>"
    findings = early_findings("card_details", source, obligations)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.text.endswith("<script>untrusted</script>")
    assert finding.sources[0].relevant_excerpt == source["card"]["oracle_text"]
    assert finding.deck_revision == 2
    assert finding.review_state == "source_rendered"
    assert finding.limitations
    assert finding.sources[0].freshness == "not_verified_current"


def test_card_identity_overlap_and_unrelated_subjects_are_not_relevance():
    for request in ("Compare mana rocks", "Explain Basalt Monoliths"):
        assert (
            early_findings(
                "card_details", card_receipt(), build_obligations(request, 0)
            )
            == []
        )


def test_truncated_identity_mismatch_and_generated_prose_cannot_publish():
    obligations = build_obligations("Explain Basalt Monolith", 0)
    for patch in (
        {"truncation": {"characters": 1}},
        {"status": "identity_mismatch"},
        {"provenance": {}},
        {"card": {"name": "Basalt Monolith"}},
    ):
        source = {**card_receipt(), **patch}
        assert early_findings("card_details", source, obligations) == []
    assert (
        early_findings(
            "answer_synthesis", {"content": "A verified infinite combo"}, obligations
        )
        == []
    )


def test_missing_source_url_is_visible_and_unsafe_protocol_is_removed():
    obligations = build_obligations("Explain Basalt Monolith", 0)
    for url in (
        "javascript:alert(1)",
        "https://user:secret@example.test",
        "file:///tmp/source",
    ):
        source = card_receipt()
        source["card"]["scryfall_uri"] = url
        assert (
            early_findings("card_details", source, obligations)[0].sources[0].source_url
            is None
        )


def test_rules_require_exact_query_source_identity_version_and_hash():
    request = "What does rule 302.6 say?"
    obligations = build_obligations(request, 1)
    hit = {
        "kind": "rule",
        "source_id": "CR 302.6",
        "text": "A creature's activated ability with the tap symbol has conditions.",
        "version": "2026-11-01",
        "content_sha256": "b" * 64,
        "text_truncated": True,
        "source_url": "https://magic.wizards.com/en/rules",
    }
    source = {"status": "available", "query": request, "hits": [hit]}
    findings = early_findings("retrieve_rules", source, obligations)
    assert len(findings) == 1
    assert findings[0].sources[0].truncated
    assert any("truncated" in line for line in findings[0].limitations)
    assert (
        early_findings(
            "retrieve_rules", {**source, "query": "different question"}, obligations
        )
        == []
    )
    for key in ("source_id", "version", "content_sha256"):
        missing = deepcopy(source)
        missing["hits"][0].pop(key)
        assert early_findings("retrieve_rules", missing, obligations) == []


def test_discovery_candidates_are_unranked_and_bound_to_plan_revision_and_query():
    request = "Find three repeatable draw creatures"
    query = "repeatable draw creatures"
    task = {
        "kind": "discover",
        "current_request_quote": request,
        "discovery_lists": [
            {
                "label": "draw",
                "query": query,
                "request_quote": request,
                "requested_count": 3,
            }
        ],
    }
    obligations = build_obligations(request, 7, task=task)
    source = {
        "source": "Shared Commander-legal local catalog; function-first union recall",
        "effective_search": {"query": query},
        "cards": [{"id": "x", "name": "A candidate"}],
        "revision": 7,
    }
    finding = early_findings("search_card_functions", source, obligations)[0]
    assert finding.type == "unranked_candidate"
    assert finding.review_state == "retrieved_unranked"
    assert "eligibility/fit not yet reviewed" in finding.text
    assert (
        early_findings("search_card_functions", {**source, "revision": 8}, obligations)
        == []
    )
    assert (
        early_findings(
            "search_card_functions",
            {**source, "effective_search": {"query": "unrelated mana"}},
            obligations,
        )
        == []
    )
    assert (
        early_findings("search_card_functions", source, build_obligations(request, 7))
        == []
    )


def test_oversized_evidence_is_withheld_not_silently_clipped():
    source = card_receipt()
    source["card"]["oracle_text"] = "condition " * 2000
    assert (
        early_findings(
            "card_details", source, build_obligations("Basalt Monolith text", 0)
        )
        == []
    )


def test_multiface_card_abstains_instead_of_hiding_back_face_conditions():
    source = card_receipt()
    source["card"]["card_faces"] = [
        {"oracle_text": "Front condition"},
        {"oracle_text": "Back condition"},
    ]
    assert (
        early_findings(
            "card_details", source, build_obligations("Explain Basalt Monolith", 0)
        )
        == []
    )
