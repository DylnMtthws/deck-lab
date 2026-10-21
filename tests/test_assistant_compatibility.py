"""Adversarial supplied-text shapes; none asserts current official Oracle text."""

import pytest

from sabermetrics.assistant_compatibility import (
    ability_compatibility,
    compare_candidates,
    fixed_mana,
)
from sabermetrics.assistant_rules import ingest_rules, retrieve_rules
from sabermetrics.assistant_suitability import candidate_suitability, deck_support_index
from sabermetrics.assistant_targeted_evidence import finalist_evidence
from tests.test_assistant_rules import SYNTHETIC_RULES, URL


def card(name, text, types="Creature — Elf", **fields):
    return dict(
        id=name,
        oracle_id=name,
        name=name,
        oracle_text=text,
        type_line=types,
        zone_name="Library",
        **fields,
    )


def match(text, provider, types="Creature — Elf"):
    return ability_compatibility(card("Candidate", text, types), [provider])


@pytest.mark.parametrize(
    "provider",
    [
        "Create a 1/1 Soldier creature token.",
        "Create a 1/1 Soldier creature token. Elves you control get +1/+1.",
        "Create a token that's a copy of target Elf creature.",
        "Target player may create a 1/1 Elf creature token.",
    ],
)
def test_tribe_match_uses_created_token_not_other_source_words(provider):
    result = match("Sacrifice another Elf: Draw a card.", card("Source", provider))
    assert not result["requirements"][0]["providers"]


@pytest.mark.parametrize(
    "source,types",
    [
        ("Put a +1/+1 counter on this creature.", "Creature"),
        ("Put a charge counter on target creature.", "Creature"),
        ("Put a +1/+1 counter on target artifact.", "Creature"),
        ("Put a +1/+1 counter on target creature an opponent controls.", "Creature"),
        ("Put a +1/+1 counter on target creature with flying.", "Creature"),
        ("Proliferate.", "Creature"),
    ],
)
def test_incompatible_counter_sources_cannot_earn_credit(source, types):
    result = match(
        "Remove a +1/+1 counter from this creature: Draw a card.",
        card("Source", source),
        types,
    )
    assert result["requirements"][0]["status"] == "unknown"


def test_matching_counter_source_has_both_exact_clauses_and_uncertainty():
    source = card("Donor", "{2}: Put a +1/+1 counter on target creature you control.")
    result = match("Remove a +1/+1 counter from this creature: Draw a card.", source)
    requirement = result["requirements"][0]
    assert requirement["providers"][0]["source_quote"] == source["oracle_text"]
    assert requirement["candidate_quote"].startswith("Remove")
    assert "repeatability remain unverified" in requirement["conditions"]


def test_artifact_cost_uses_treasure_not_generic_creature():
    cost = "Sacrifice an artifact: Draw a card."
    assert match(cost, card("Treasure", "Create a Treasure token."))["requirements"][0][
        "providers"
    ]
    assert not match(cost, card("Soldier", "Create a 1/1 Soldier creature token."))[
        "requirements"
    ][0]["providers"]


def test_self_death_does_not_get_fodder_credit():
    result = match(
        "When this creature dies, draw a card.",
        card("Tokens", "Create a 1/1 Elf creature token."),
    )
    assert [r["dimension"] for r in result["requirements"]] == ["sacrifice"]
    assert not result["requirements"][0]["providers"]


@pytest.mark.parametrize(
    "text",
    [
        "Return target creature card from your graveyard to your hand.",
        "Return target creature card with mana value 3 or less from your graveyard to the battlefield.",
    ],
)
def test_return_to_hand_or_restricted_return_cannot_refund_source(text):
    result = match("Sacrifice this creature: Draw a card.", card("Recursion", text))
    assert not result["requirements"][0]["providers"]
    assert "recasting" in result["abilities"][0]["further_uses"]


def test_conditional_provider_is_a_lead_not_ranking_credit():
    result = match(
        "Sacrifice another creature: Draw a card.",
        card(
            "Conditional",
            "If you control an artifact, create a 1/1 Elf creature token.",
        ),
    )
    row = result["requirements"][0]
    assert not row["providers"] and row["conditional_leads"]


@pytest.mark.parametrize(
    "fields", [{"oracle_text_truncated": True}, {"card_faces": [{"name": "Back"}]}]
)
def test_incomplete_provider_is_never_credited(fields):
    assert not match(
        "Sacrifice another creature: Draw a card.",
        card("Source", "Create a 1/1 Elf creature token.", **fields),
    )["requirements"][0]["providers"]


@pytest.mark.parametrize(
    "cost,expected",
    [
        ("{2}{U}, {T}", 3),
        ("Pay 2 life", 0),
        ("{X}{U}", None),
        ("{2/U}", None),
        ("Pay any amount of mana", None),
    ],
)
def test_fixed_mana_abstains_on_variable_or_alternative_symbols(cost, expected):
    assert fixed_mana(cost) == expected


@pytest.mark.parametrize(
    "cost,expected",
    [
        ("{0}", 0),
        ("{12}{W}{U}{B}{R}{G}{C}", 18),
        (" {2}{u}, {T} ", 3),
        ("{Q}", 0),
        ("{1}, Sacrifice another creature", 1),
        ("{T}, Sacrifice an artifact", 0),
        ("{R}, {T}, Discard a card", 1),
        ("Discard two cards", 0),
        ("{2}, Pay 2 life", 2),
        ("{T}, Remove a charge counter from this artifact", 0),
        ("Remove a +1/+1 counter from this creature", 0),
    ],
)
def test_fixed_mana_full_supported_cost_grammar(cost, expected):
    assert fixed_mana(cost) == expected


@pytest.mark.parametrize(
    "cost",
    [
        "",
        " ",
        "{T},",
        ",{1}",
        "{1},, {T}",
        "LEVEL 1-4 {T}",
        "Level up {1}{G} ({1}{G}",
        "{1}{G} trailing text",
        "prefix {2}",
        "{2",
        "{2}}",
        "{{2}}",
        "{S}",
        "{U/P}",
        "{∞}",
        "{١}",
        'Creatures you control have "{T}',
        '"{T}',
        "“{T}",
        "{T} if you control an Elf",
        "{T}, Activate only as a sorcery",
        "Sacrifice another creature if you control an artifact",
        "Discard a card with mana value {2}",
        "Pay X life",
        "{1}, Sacrifice any number of creatures",
        "Remove a level counter from this creature",
    ],
)
def test_fixed_mana_rejects_every_unsupported_residual(cost):
    assert fixed_mana(cost) is None


TREE_SPEAKER_TEXT = (
    "Level up {1}{G} ({1}{G}: Put a level counter on this. "
    "Level up only as a sorcery.)\nLEVEL 1-4\n1/2\n{T}: Add {G}{G}.\n"
    'LEVEL 5+\n1/4\nElves you control have "{T}: Add {G}{G}."'
)


@pytest.mark.parametrize(
    "text,fields",
    [
        (TREE_SPEAKER_TEXT, {}),
        ("LEVEL 1–4\n{T}: Add {G}{G}.", {}),
        ("LEVEL 5+\nSacrifice another creature: Draw a card.", {}),
        ("{T}: Add {G}{G}.", {"layout": "leveler"}),
        ("{T}: Add {G}.", {"type_line": "Enchantment — Class"}),
        ("{1}{G}: Level 2\nCreate a 1/1 Elf creature token.", {}),
    ],
)
def test_level_class_resource_features_abstain_without_changing_source(text, fields):
    candidate = card("Scoped", text)
    candidate.update(fields)
    before = dict(candidate)
    result = ability_compatibility(
        candidate, [card("Tokens", "Create a Treasure token.")]
    )
    assert result["status"] == "unknown"
    assert result["abilities"] == result["requirements"] == []
    assert candidate == before and candidate["oracle_text"] == text


@pytest.mark.parametrize(
    "text",
    [
        'Creatures you control have "{T}: Draw a card."',
        "Enchanted creature has “{T}: Draw a card.”",
        "{T}: Draw a card. Activate only as a sorcery.",
        "{T}: Draw a card.\nActivate only once each turn.",
        "{T}: If you control an artifact, draw a card.",
    ],
)
def test_scoped_activations_do_not_get_fixed_mana_comparisons(text):
    cards = [card("Scoped", text), card("Ordinary", "{2}: Draw a card.")]
    for c in cards:
        c["suitability"] = {"ability_compatibility": ability_compatibility(c, [])}
    assert (
        cards[0]["suitability"]["ability_compatibility"]["abilities"][0][
            "fixed_mana_per_activation"
        ]
        is None
    )
    compare_candidates(cards)
    assert all("functional_comparison" not in c for c in cards)


def test_provider_scope_changes_cannot_reuse_warmed_resource_credit():
    candidate = card("Candidate", "Sacrifice another creature: Draw a card.")
    source = card("Source", "Create a 1/1 Elf creature token.")
    assert ability_compatibility(candidate, [source])["requirements"][0]["providers"]
    for fields in (
        {"oracle_text": "LEVEL 1-4\nCreate a 1/1 Elf creature token."},
        {"oracle_text": "Create a 1/1 Elf creature token.", "layout": "leveler"},
        {"layout": "normal", "type_line": "Enchantment — Class"},
    ):
        source.update(fields)
        assert not ability_compatibility(candidate, [source])["requirements"][0][
            "providers"
        ]
    source.update(type_line="Creature", layout="normal")
    restored = ability_compatibility(candidate, [source])
    assert restored["requirements"][0]["providers"]
    restored["requirements"][0]["providers"].clear()
    assert ability_compatibility(candidate, [source])["requirements"][0]["providers"]
    source["oracle_text"] = 'Creatures have "{T}: Create a 1/1 Elf creature token."'
    row = ability_compatibility(candidate, [source])["requirements"][0]
    assert not row["providers"] and row["conditional_leads"]


def test_recomparison_clears_stale_credit_after_source_mutation():
    cards = [card("Low", "{T}: Draw a card."), card("High", "{2}: Draw a card.")]
    for c in cards:
        c["suitability"] = {"ability_compatibility": ability_compatibility(c, [])}
    compare_candidates(cards)
    assert all("functional_comparison" in c for c in cards)
    cards[0]["oracle_text"] = TREE_SPEAKER_TEXT
    cards[0]["suitability"]["ability_compatibility"] = ability_compatibility(
        cards[0], []
    )
    compare_candidates(cards)
    assert all("functional_comparison" not in c for c in cards)
    assert [c["id"] for c in cards] == ["Low", "High"]


def test_comparison_preserves_full_cost_and_does_not_claim_dominance():
    cards = [
        card("Low", "{1}, Sacrifice another creature: Draw a card."),
        card("High", "{3}: Draw a card."),
    ]
    index = deck_support_index({"entries": []})
    for c in cards:
        c["suitability"] = candidate_suitability(c, index)
    compare_candidates(cards)
    comparison = cards[0]["functional_comparison"]
    assert comparison["fixed_activation_mana_difference"] == -2
    assert "Sacrifice" in comparison["own_activation_cost"]
    assert "No strict superiority" in comparison["scope"]


def test_finalist_retrieval_is_bounded_local_and_preserves_missing_evidence(
    tmp_path, monkeypatch
):
    from sabermetrics import assistant_targeted_evidence as module

    calls = []
    original = module.retrieve_rules

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "retrieve_rules", record)
    cards = [
        card(
            str(i),
            "{T}, Remove a charge counter from this artifact, Sacrifice another creature: Draw a card.",
            "Artifact",
        )
        for i in range(7)
    ]
    rules, receipt = finalist_evidence(
        tmp_path / "missing.db", cards, {"status": "unavailable", "hits": []}
    )
    assert len(calls) == 2
    assert all(c[1]["deadline_seconds"] == 0.35 for c in calls)
    assert receipt["finalist_count"] == 3 and receipt["finalists_omitted"] == 4
    assert rules["status"] == "unavailable" and not rules["hits"]
    assert all(q["status"] == "unavailable" for q in receipt["queries"])


def test_targeted_retrieval_retains_source_hashes_and_full_text(tmp_path):
    path = tmp_path / "rules.db"
    ingest_rules(path, SYNTHETIC_RULES, source_url=URL, version="synthetic")
    rules, receipt = finalist_evidence(
        path, [card("Draw", "{T}: Draw a card.")], {"hits": [], "status": "unavailable"}
    )
    assert receipt["lookup_count"] == 1 and rules["hits"]
    assert all(
        h["content_sha256"] and h["version"] == "synthetic" for h in rules["hits"]
    )
    assert any("tap" in h["text"].lower() for h in rules["hits"])
    with pytest.raises(ValueError, match="deadline"):
        retrieve_rules(path, "tap", deadline_seconds=10)


def test_runtime_finalist_evidence_reaches_reviewer_without_new_model_round(
    prepared, monkeypatch
):
    import json
    from types import SimpleNamespace

    from sabermetrics import assistant_targeted_evidence
    from sabermetrics.assistant_runtime import run_turn
    from tests.test_assistant_runtime import Gateway, call, step
    from tests.test_assistant_selection import selection

    service, path, owner, deck, _, _ = prepared
    monkeypatch.setattr("sabermetrics.assistant_runtime.MAX_TOOL_CALLS", 1)
    observed = []

    def retrieve(db_path, cards, existing, support_cards=None, claims=None):
        observed.extend(cards)
        return {
            "hits": [
                {
                    "source_id": "synthetic-finalist-rule",
                    "text": "Synthetic evidence: an ingredient is not a repeatable loop.",
                    "text_truncated": False,
                }
            ]
        }, {"stage": "finalist_evidence", "lookup_count": 1, "elapsed_seconds": 0}

    monkeypatch.setattr(assistant_targeted_evidence, "finalist_evidence", retrieve)

    class Reviewer:
        requests = []

        def generate(self, request):
            self.requests.append(request)
            payload = json.loads(request.user)
            assert "Synthetic evidence" in request.user
            value = {"f": "unknown"}
            for claim in payload["claims"]:
                value[claim["claim_id"]] = dict(
                    k="strategy",
                    v="unverified",
                    e=[],
                    fix=None,
                    why="Unverified synthetic setup.",
                    u=True,
                )
            return SimpleNamespace(value=request.schema.model_validate(value))

    reviewer = Reviewer()
    gateway = Gateway(
        [
            step(call("search_cards", {"query": "Incoming"})),
            selection(("incoming-0", "incoming-1")).model_dump(),
        ]
    )
    result = run_turn(
        path,
        owner,
        deck,
        [{"role": "user", "content": "Give me two additional cards."}],
        gateway=gateway,
        answer_review_gateway=reviewer,
        experiment_service=service,
        selection_first=True,
    )
    assert len(gateway.requests) == 2 and len(reviewer.requests) == 1
    assert [c["id"] for c in observed] == ["incoming-0", "incoming-1"]
    assert any(
        s["stage"] == "finalist_evidence"
        for s in result["research"]["runtime"]["stages"]
    )
    assert result["answer_review"]["status"] == "unverified"


pytest_plugins = ["tests.test_assistant_experiments"]


@pytest.mark.parametrize(
    "restriction",
    ["artifact creature", "tapped creature", "creature with flying", "legendary Elf"],
)
def test_compound_sacrifice_restrictions_stay_unknown(restriction):
    result = match(
        f"Sacrifice another {restriction}: Draw a card.",
        card("Source", "Create a Treasure token. Create a 1/1 Elf creature token."),
    )
    assert result["requirements"] and not result["requirements"][0]["providers"]


def test_initial_counters_do_not_establish_replenishment():
    result = match(
        "This creature enters with a +1/+1 counter on it.\nRemove a +1/+1 counter from this creature: Draw a card.",
        card("Proliferator", "Proliferate."),
    )
    assert result["requirements"][0]["status"] == "unknown"
    assert result["abilities"][0]["activation_cost"].startswith("Remove")


def test_warmed_matches_follow_exact_source_requirement_type_and_completeness():
    source = card("Source", "Create a 1/1 Elf creature token.")
    creature = card("Candidate", "Sacrifice another creature: Draw a card.")
    assert ability_compatibility(creature, [source])["requirements"][0]["providers"]
    artifact = {**creature, "oracle_text": "Sacrifice an artifact: Draw a card."}
    assert not ability_compatibility(artifact, [source])["requirements"][0]["providers"]
    source["oracle_text"] = (
        "If you control an artifact, create a 1/1 Elf creature token."
    )
    conditional = ability_compatibility(creature, [source])["requirements"][0]
    assert not conditional["providers"] and conditional["conditional_leads"]
    source["oracle_text"] = "Create a Treasure token."
    assert ability_compatibility(artifact, [source])["requirements"][0]["providers"]
    source["oracle_text_truncated"] = True
    assert not ability_compatibility(artifact, [source])["requirements"][0]["providers"]
    counter = card(
        "Counter", "Remove a charge counter from this creature: Draw a card."
    )
    donor = card("Donor", "Put a charge counter on target creature.")
    assert ability_compatibility(counter, [donor])["requirements"][0]["providers"]
    counter["type_line"] = "Artifact"
    assert not ability_compatibility(counter, [donor])["requirements"][0]["providers"]


def test_matching_cache_cannot_leak_mutable_provider_records_or_identity():
    candidate = card("Candidate", "Sacrifice another creature: Draw a card.")
    source = card("Source", "Create a 1/1 Elf creature token.")
    first = ability_compatibility(candidate, [source])
    first["requirements"][0]["providers"][0]["source_quote"] = "invented text"
    source.update(name="Renamed", oracle_id="new-identity")
    second = ability_compatibility(candidate, [source])["requirements"][0]["providers"][
        0
    ]
    assert second == {
        "name": "Renamed",
        "oracle_id": "new-identity",
        "source_quote": source["oracle_text"],
    }


def test_pure_matching_caches_are_bounded_and_match_uncached_judgments():
    from sabermetrics.assistant_compatibility import _provider_matches, _tokens

    for helper, args, bound in (
        (_tokens, ("Create a 1/1 Elf creature token.", "elf"), 8192),
        (
            _provider_matches,
            (
                "Put a charge counter on target creature.",
                "counters",
                "charge",
                "Creature",
            ),
            16384,
        ),
        (fixed_mana, ("{X}{U}",), 2048),
    ):
        assert helper(*args) == helper.__wrapped__(*args)
        assert helper(*args) == helper.__wrapped__(*args)
        assert helper.cache_info().maxsize == bound
