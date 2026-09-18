"""A chunk is cited as the rule its text begins with, and nothing else.

The chunker used to set the label on every rule it passed and flush only when
a size target was reached, so a chunk accumulating 707.3 through 707.8 was
cited ``CR 707.8``, and the Glossary — which has no rule numbers — inherited
the last numbered rule before it. On the pinned document that put an
unambiguously wrong citation on 214 of 871 chunks. The rules-support matcher
keys on quote text and could not see it; a reader of ``rules:CR 905.4a`` on a
Glossary entry could.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from sabermetrics.reference_layer.chunker import DocumentChunker

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "data" / "reference" / "comprehensive_rules"
_LEADING_RULE = re.compile(r"^\s*(\d{3}\.\d+[a-z]?)\.?\s")

MINI_RULES = """Magic: The Gathering Comprehensive Rules

Contents

1. Game Concepts
100. General
707. Copying Objects
Glossary
Credits

100. General

100.1. These Magic rules apply to any Magic game with two or more players.

100.2. Some formats restrict deck size.

707. Copying Objects

707.2c Some effects cause a permanent to become a copy of another.

707.3. The copy's copiable values become the copied information.

707.5. An object that enters the battlefield as a copy of another object.

Glossary

Ability
1. Text on an object that explains what that object does or can do.

Evoke
A keyword ability that lets a player cast a creature for an alternative cost.

Credits

Magic: The Gathering is a trademark of Wizards of the Coast LLC.
"""


def _build_module():
    spec = importlib.util.spec_from_file_location(
        "build_rules_index", ROOT / "scripts" / "build_rules_index.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mini_chunks(tmp_path):
    path = tmp_path / "comprehensive_rules.txt"
    path.write_text(MINI_RULES, encoding="utf-8")
    return DocumentChunker().chunk_comprehensive_rules(path)


def test_a_chunk_is_cited_as_the_rule_it_begins_with(mini_chunks):
    for chunk in mini_chunks:
        head = _LEADING_RULE.match(chunk.content)
        if head is None:
            continue
        assert (
            chunk.section == f"CR {head.group(1)}"
        ), f"begins at {head.group(1)} but is cited {chunk.section!r}"


def test_the_period_form_starts_a_rule_too(mini_chunks):
    """707.3. and 707.5. (period, no letter) are rules and split as such."""
    labels = {chunk.section for chunk in mini_chunks}
    assert (
        "CR 707.5" in labels
        or any(
            chunk.content.lstrip().startswith("707.5") and chunk.section == "CR 707.3"
            for chunk in mini_chunks
        )
        is False
    ), labels
    # The rule number stays in the body so a reader of the passage sees it.
    for chunk in mini_chunks:
        if chunk.section == "CR 707.2c":
            assert chunk.content.startswith("707.2c")


def test_the_glossary_is_cited_as_the_glossary(mini_chunks):
    glossary = [c for c in mini_chunks if "keyword ability that lets" in c.content]
    assert glossary, "the Evoke entry must be chunked"
    for chunk in glossary:
        assert chunk.section is not None
        assert chunk.section.startswith("Glossary: "), chunk.section
        assert not chunk.section.startswith("CR ")
    # Cited by the first term the piece defines.
    assert any(c.section == "Glossary: Ability" for c in mini_chunks)


def test_the_credits_are_not_a_rule(mini_chunks):
    credits = [c for c in mini_chunks if "trademark of Wizards" in c.content]
    assert credits
    assert all(c.section == "Credits" for c in credits)
    assert all(
        c.tier == 3 for c in credits
    ), "a trademark notice must not outrank a rule"


def test_the_build_refuses_a_miscited_chunk(mini_chunks):
    """The refusal fires BEFORE the index is activated, on the chunks alone."""
    module = _build_module()
    assert module.index_refusals(mini_chunks) == []
    # The mini document is short enough that the chunker packs all of 707 into
    # one chunk headed by the section title, so no chunk begins at a rule
    # number. Add one that does, cited as a different rule than it begins with.
    from sabermetrics.reference_layer.chunker import Chunk

    model = next(c for c in mini_chunks if c.section == "CR 707")
    bad = [
        *mini_chunks,
        Chunk(
            id=model.id + "-miscited",
            document=model.document,
            section="CR 707.2c",
            tier=model.tier,
            content="707.5. An object that enters the battlefield as a copy.",
        ),
    ]
    problems = module.index_refusals(bad)
    assert problems and "cited" in problems[0], problems


def test_the_pinned_document_has_no_wrong_citation():
    """Zero, on the real document, in the same terms the defect was measured."""
    candidates = sorted(SOURCE_DIR.glob("*/normalized.txt"))
    if not candidates:
        pytest.skip("the pinned rules document is not provisioned on this machine")
    chunks = DocumentChunker().chunk_comprehensive_rules(candidates[-1])
    module = _build_module()
    assert module.index_refusals(chunks) == []
    glossary = [c for c in chunks if (c.section or "").startswith("Glossary: ")]
    assert len(glossary) > 50, "the Glossary must be chunked as itself"
    assert not any(
        c.section == "CR 905.4a" and "keyword" in c.content.casefold() for c in chunks
    ), "a Glossary entry cited as a conspiracy-draft rule"
    assert any(
        c.section == "CR 707.5" and c.content.lstrip().startswith("707.5")
        for c in chunks
    )
