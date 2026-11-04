from sabermetrics.assistant_review_gap import corrective_scope


def test_unsafe_or_unproven_scope_keeps_every_record_and_packet():
    sources = [
        "Card: Engine\nOracle text:\nUse its ability.",
        "Card: Other Engine\nOracle text:\nA cross-card prerequisite.",
        "Rule source: counterevidence\nAn exception applies.",
        "Unknown source kind\nCommander support.",
    ]
    context = {
        "recommendation_packets": [{"name": "Other Engine"}],
        "scope": "best across the deck",
    }
    indices, returned, diagnostics = corrective_scope(sources, context)
    assert indices == set(range(len(sources)))
    assert returned is context
    assert diagnostics["status"] == "full_fallback"
    assert (
        diagnostics["evidence_bytes_before"]
        == diagnostics["evidence_bytes_after"]
        == sum(len(s.encode()) for s in sources)
    )
    assert (
        diagnostics["research_context_bytes_before"]
        == diagnostics["research_context_bytes_after"]
    )
