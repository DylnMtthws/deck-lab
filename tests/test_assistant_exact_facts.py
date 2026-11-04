import pytest

from sabermetrics.assistant_claim_fragments import reviewed_claim_text, text_hash
from sabermetrics.assistant_exact_facts import METHOD, exact_cost_facts

NAMES = ["Mystic Archaeologist", "Azure Mage", "Roil Cartographer"]
COSTS = ["{3}{U}{U}", "{3}{U}", "{T}, Pay six {E}"]
ABILITIES = [
    "{3}{U}{U}: Draw two cards.",
    "{3}{U}: Draw a card.",
    "{T}, Pay six {E}: Draw three cards.",
]


def check(
    claim="Activation cost: {3}{U}.",
    oracle="{3}{U}: Draw a card.",
    name="Azure Mage",
    source_name="Azure Mage",
    extra="",
):
    parts = ["Candidates", f"Draw: {name} ({{1}}{{U}}) — {claim}"]
    sources = [
        f"Card: {source_name}\nMana cost: {{3}}{{U}}\nOracle text:\n{oracle}{extra}"
    ]
    return parts, sources, exact_cost_facts(parts, sources)[1]


@pytest.mark.parametrize("name,cost,ability", list(zip(NAMES, COSTS, ABILITIES)))
def test_qa_costs_are_literal_facts_with_full_ability_citations(name, cost, ability):
    parts, sources, receipt = check(f"Activation cost: {cost}.", ability, name, name)
    [judgment] = receipt["claim_judgments"]
    assert judgment["verdict"] == "supported"
    assert judgment["method"] == receipt["method"] == METHOD
    assert reviewed_claim_text(receipt, judgment, parts) == f"Activation cost: {cost}."
    assert judgment["citations"] == [{"source_index": 0, "quote": ability}]
    assert (
        judgment["source_sha256"]
        == receipt["source_sha256"][0]
        == text_hash(sources[0])
    )
    assert receipt["fulfillment"] == "unknown"
    assert receipt["evidence_bytes_sent"] == 0
    assert "review_model" not in receipt
    changed = parts[:]
    changed[1] += " Changed."
    assert reviewed_claim_text(receipt, judgment, changed) == ""


@pytest.mark.parametrize(
    "oracle",
    [
        "{3}{U}{U}: Draw a card.",  # substring/prefix cannot prove the complete cost
        "{3}{U}, {T}: Draw a card.",
        "{3}{U}, Sacrifice another creature: Draw a card.",
        "Mana cost: {3}{U}.",
        "Cycling {3}{U} ({3}{U}, Discard this card: Draw a card.)",
        'Enchanted creature has "{3}{U}: Draw a card."',
        "Other creatures have “{3}{U}: Draw a card.”",
        '{T}: Target creature gains "{3}{U}: Draw a card."',
        "If you control a Wizard, {3}{U}: Draw a card.",
        "{3}{U}: Draw a card. Activate only as a sorcery.",
        "{3}{U}: If you control a Wizard, draw a card.",
        "{3}{U}: Draw a card",
        "{3}{U}: Draw a card…",
        "{3}{U}: Draw a card.\n{1}: Gain 1 life.",
        "{3}{U}: Draw a card.\n{3}{U}: Gain 1 life.",
        "{3}{U}: Draw a card.\nactivate only once each turn.",
        "Unknown",
    ],
)
def test_uncertain_or_nonliteral_abilities_abstain(oracle):
    assert not check(oracle=oracle)[2]["claim_judgments"]


@pytest.mark.parametrize(
    "claim",
    [
        "Activation cost: {3}.",
        "Activation cost: {3}{U}, without other resources.",
        "Activation cost: {3}{U}. It outperforms other engines.",
        "Activation costs {3}{U}.",
        "Mana cost: {3}{U}.",
        "Pay {3}{U} to draw a card.",
        "Draws a card only after paying {3}{U}, without tapping.",
        "Net activation cost: {3}{U}.",
        "Currently payable activation cost: {3}{U}.",
        "Activation cost: {3}{U} for repeatable draw.",
    ],
)
def test_only_exact_application_clause_is_admitted(claim):
    assert not check(claim=claim)[2]["claim_judgments"]


@pytest.mark.parametrize(
    "extra",
    [
        "\nCard faces (printed fields): Face: Other\nOracle text:\n{3}{U}: Draw a card.",
        "\nFace: Other",
        "\nExcerpt; omissions unknown",
        "\ntruncated",
    ],
)
def test_faces_and_truncation_abstain(extra):
    assert not check(extra=extra)[2]["claim_judgments"]


def test_wrong_card_missing_duplicate_sources_and_ambiguous_prefix_abstain():
    assert not check(source_name="Mystic Archaeologist")[2]["claim_judgments"]
    assert not check(name="Azure Mage // Other", source_name="Azure Mage // Other")[2][
        "claim_judgments"
    ]
    parts, sources, _ = check()
    assert not exact_cost_facts(parts, [])[1]["claim_judgments"]
    assert not exact_cost_facts(parts, sources * 2)[1]["claim_judgments"]
    assert not exact_cost_facts(parts, sources, {1})[1]["claim_judgments"]
    assert not exact_cost_facts(
        ["Candidates", "Azure Mage — Activation cost: {3}{U}."], sources
    )[1]["claim_judgments"]
    excerpt = sources[0].replace(
        "Oracle text:", "Oracle text (excerpt; omissions unknown):"
    )
    assert not exact_cost_facts(parts, [excerpt])[1]["claim_judgments"]


def test_other_printed_fields_never_supply_the_cost_and_full_record_is_retained():
    _, _, receipt = check(
        oracle="{1}: Draw a card.",
        extra='\nPrinted characteristics: {"cost": "{3}{U}"}',
    )
    assert not receipt["claim_judgments"]
    _, sources, receipt = check(
        extra='\nPrinted characteristics: {}\nSource identity/provenance: {"id": "abc"}'
    )
    assert len(receipt["claim_judgments"]) == 1
    assert receipt["source_sha256"] == [text_hash(sources[0])]


def test_roil_cost_alongside_complete_landfall_trigger_is_supported():
    oracle = (
        "Landfall — Whenever a land you control enters, you get {E} (an energy counter).\n"
        "{T}, Pay six {E}: Draw three cards."
    )
    _, _, receipt = check(
        "Activation cost: {T}, Pay six {E}.",
        oracle,
        "Roil Cartographer",
        "Roil Cartographer",
    )
    assert len(receipt["claim_judgments"]) == 1
    assert receipt["claim_judgments"][0]["citations"][0]["quote"] == ABILITIES[2]


@pytest.mark.parametrize(
    "oracle",
    [
        "Level up {1}\nLEVEL 2–3\n{3}{U}: Draw a card.",
        "As long as this is a creature, it has the following ability.\n{3}{U}: Draw a card.",
        "Creatures you control gain the following ability.\n{3}{U}: Draw a card.",
        "Whenever this attacks, it gains the following ability.\n{3}{U}: Draw a card.",
    ],
)
def test_keyword_or_multiline_grants_abstain(oracle):
    assert not check(oracle=oracle)[2]["claim_judgments"]
