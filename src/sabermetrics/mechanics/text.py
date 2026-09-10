"""Pure oracle-text helpers shared by the mechanic tag predicates.

stdlib and ``re`` only, like everything else in this package. Every function
here is a total function of its argument, so a predicate that uses one behaves
identically in a unit test, a batch tag rebuild and a request.

The one non-obvious contract is :func:`mask_reminder_text`, which **preserves
offsets**. A tag's ``matched_span`` has to point back into the card's real
oracle text — the text a player would see — so masking must not shift indices.
Deleting the parenthetical would; overwriting it with spaces does not.
"""

from __future__ import annotations

import re

#: An innermost parenthesised run. Applied repeatedly, this also clears nested
#: parentheses, which oracle text uses rarely but does use (Un-set cards, and a
#: handful of reminder texts that quote a symbol in parentheses).
_INNERMOST_PARENS = re.compile(r"\([^()]*\)")

#: Mana symbols as printed: ``{2}``, ``{U}``, ``{W/U}``, ``{U/P}``, ``{X}``.
_MANA_SYMBOL = re.compile(r"\{([^{}]+)\}")


def mask_reminder_text(text: str) -> str:
    """Blank out parenthesised reminder text, preserving every offset.

    Reminder text restates rules the card does not itself impose, and it is the
    single largest source of false positives in oracle-text matching: the
    reminder for cascade contains "without paying its mana cost", the reminder
    for foretell contains "pay {2}", and the reminder for convoke contains "help
    cast this spell". A predicate that matches those is matching a rules gloss,
    not the card.

    Args:
        text: Oracle text as published.

    Returns:
        The same string, with every parenthesised run replaced by spaces of
        equal length. ``len(result) == len(text)`` always, so a match offset in
        the result indexes the same character in ``text``.
    """
    masked = text
    while True:
        replaced = _INNERMOST_PARENS.sub(lambda m: " " * (m.end() - m.start()), masked)
        if replaced == masked:
            return replaced
        masked = replaced


def mana_symbols(mana_cost: str | None) -> tuple[str, ...]:
    """Split a printed mana cost into its symbols, without the braces.

    Args:
        mana_cost: A cost like ``"{1}{U/P}{U}"``. ``None`` and ``""`` are legal
            inputs — ``mtg_v1`` publishes an empty card-level cost for lands and
            for modal double-faced cards, whose costs live on the faces.

    Returns:
        Upper-cased symbols in printed order, e.g. ``("1", "U/P", "U")``.
    """
    if not mana_cost:
        return ()
    return tuple(match.group(1).upper() for match in _MANA_SYMBOL.finditer(mana_cost))


def is_phyrexian(symbol: str) -> bool:
    """Whether a mana symbol may be paid with life.

    Args:
        symbol: One symbol without braces, as returned by :func:`mana_symbols`.

    Returns:
        True for Phyrexian symbols (``U/P``, ``P``, and the hybrid-Phyrexian
        ``2/U/P`` shape), False otherwise.
    """
    parts = symbol.upper().split("/")
    return "P" in parts


def type_line_subtypes(type_line: str | None) -> tuple[str, ...]:
    """Every subtype printed on a type line, across all its faces.

    A type line is ``"<supertypes and card types> — <subtypes>"``. Everything
    before the dash is what :data:`CARD_TYPES` already covers; everything after
    it is the part nothing in the substrate could previously read, which is why
    "non-Human creature" and "a land with the Island type" were both
    inexpressible as filters.

    Split on whitespace, which is correct for every creature, artifact,
    enchantment and land subtype: ``"Urza's Mine"`` really is the two subtypes
    ``Urza's`` and ``Mine``. Plane subtypes are multi-word and are therefore
    over-split; no filter in this system selects a plane, and reporting a plane
    as several tokens is preferable to silently dropping the whole field.

    Args:
        type_line: A printed type line, possibly with ``//`` faces. ``None``
            and ``""`` are legal — ``mtg_v1`` publishes neither for some rows.

    Returns:
        Subtypes in first-seen order, deduplicated, with original casing.
    """
    if not type_line:
        return ()
    found: dict[str, None] = {}
    for face in type_line.split("//"):
        # Both dashes appear in the wild: Scryfall prints an em dash, and a
        # hand-authored fixture is as likely to use a hyphen.
        for separator in ("—", "–", " - "):
            if separator in face:
                for token in face.split(separator, 1)[1].split():
                    found.setdefault(token.strip(), None)
                break
    return tuple(found)
