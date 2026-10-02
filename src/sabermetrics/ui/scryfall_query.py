"""Scryfall-style syntax parser for the Research card search.

Tokenises on whitespace, respects double-quoted values, and maps recognised
syntax onto the same ``card_filters`` dict used by the form-based filter UI.

All filter values use the same keys and shapes as the form-based filters in
``research_routes._load_index_state``, so the parsed output can be merged
directly into the existing ``card_filters`` dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SUPERTYPES_LOWER = frozenset(
    {
        "basic",
        "legendary",
        "snow",
        "world",
        "ongoing",
        "host",
        "elite",
        "token",
    }
)
CARD_TYPES_LOWER = frozenset(
    {
        "artifact",
        "battle",
        "conspiracy",
        "creature",
        "dungeon",
        "enchantment",
        "instant",
        "kindred",
        "land",
        "phenomenon",
        "plane",
        "planeswalker",
        "scheme",
        "sorcery",
        "tribal",
        "vanguard",
    }
)
RARITY_ALIASES: dict[str, str] = {
    "c": "common",
    "common": "common",
    "u": "uncommon",
    "uncommon": "uncommon",
    "r": "rare",
    "rare": "rare",
    "m": "mythic",
    "mythic": "mythic",
}
COLOR_LETTERS = frozenset("wubrgc")

_CANONICAL_SUPERTYPES = (
    "Basic",
    "Legendary",
    "Snow",
    "World",
    "Ongoing",
    "Host",
    "Elite",
    "Token",
)
_CANONICAL_CARD_TYPES = (
    "Artifact",
    "Battle",
    "Conspiracy",
    "Creature",
    "Dungeon",
    "Enchantment",
    "Instant",
    "Kindred",
    "Land",
    "Phenomenon",
    "Plane",
    "Planeswalker",
    "Scheme",
    "Sorcery",
    "Tribal",
    "Vanguard",
)


@dataclass(frozen=True)
class ParsedQuery:
    """Result of parsing a Scryfall-style query string.

    Attributes:
        filters: Filter keys/values matching the shapes used by
            ``research_routes._load_index_state``'s ``card_filters`` dict.
        name_terms: Bare words / quoted phrases that are name search terms.
        unsupported: Syntax terms that the parser recognised but cannot map to
            an existing filter (e.g. ``is:commander``, ``set:mh3``, ``or``).
        applied_terms: The original token strings that were successfully parsed
            into filters, in the order they appeared. Used for the
            "Applied from search" notice in the UI.
    """

    filters: dict[str, Any] = field(default_factory=dict)
    name_terms: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    applied_terms: tuple[str, ...] = ()


#: Matches the start of a recognised syntax prefix (e.g. ``t:``, ``o:``).
_SYNTAX_PREFIX = (
    r"(?:t|type|o|oracle|c|id|r|rarity|is|f|set|e|mv|cmc|pow|power|tou|toughness)"
)

#: Tokenise raw whitespace-separated tokens, handling quoted strings.
#: Single-quoted and double-quoted values stay intact as one unit.
_RAW_TOKEN_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\S+")

_COMPARATOR_RE = re.compile(
    r"^(mv|cmc|pow|power|tou|toughness)([<>=:]{1,2})(\d+)$",
    re.IGNORECASE,
)

_PER_ACCUMULATE = frozenset({"oracle_text"})


def _tokenize(text: str) -> list[str]:
    """Split *text* on whitespace, coalescing prefix+quoted sequences.

    Scryfall syntax like ``o:"draw a card"`` appears after the raw regex
    split as three pieces (``o:"draw``, ``a``, ``card"``). This function
    recombines them into a single token ``o:draw a card``.

    Standalone quoted strings such as ``"Sol Ring"`` are returned unquoted
    (the quotes are stripped).
    """
    raw = _RAW_TOKEN_RE.findall(text)
    out: list[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        # Fully standalone quoted string --- unquote.
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
            out.append(tok[1:-1])
            i += 1
            continue
        # Does this token look like a prefix + a quoted value?
        # e.g. ``o:"draw`` (opening quote only, more tokens follow)
        # or   ``o:"whenever"`` (self-contained single token)
        if '"' in tok or "'" in tok:
            colon_pos = tok.find(":")
            if colon_pos > 0:
                prefix = tok[: colon_pos + 1]
                if re.match(rf"^{_SYNTAX_PREFIX}:", prefix, re.IGNORECASE):
                    rest = tok[colon_pos + 1 :]
                    # Self-contained:  o:"whenever"
                    if rest.startswith('"') and rest.endswith('"'):
                        out.append(f"{prefix}{rest[1:-1]}")
                        i += 1
                        continue
                    if rest.startswith("'") and rest.endswith("'"):
                        out.append(f"{prefix}{rest[1:-1]}")
                        i += 1
                        continue
                    # Opening quote only --- coalesce subsequent tokens.
                    rest = rest.lstrip('"').lstrip("'")
                    parts = [rest]
                    i += 1
                    while i < len(raw) and not (
                        raw[i].endswith('"') or raw[i].endswith("'")
                    ):
                        parts.append(raw[i])
                        i += 1
                    if i < len(raw):
                        closing = raw[i]
                        if closing.endswith(('"', "'")):
                            parts.append(closing[:-1])
                        else:
                            parts.append(closing)
                        i += 1
                    out.append(f"{prefix}{' '.join(parts)}")
                    continue
        out.append(tok)
        i += 1
    return out


def _unquote(token: str) -> str:
    """Strip one layer of matching quotes from *token*."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1]
    return token


def parse_query(q: str) -> ParsedQuery:
    """Parse a Scryfall-style query string into structured filters.

    Args:
        q: Raw query string, e.g. ``'t:instant o:"draw a card" mv<=2'``

    Returns:
        A :class:`ParsedQuery` with filters mapped to the same keys/shapes as
        ``research_routes._load_index_state`` ``card_filters``.
    """
    tokens = _tokenize(q.strip())
    if not tokens:
        return ParsedQuery()

    filters: dict[str, Any] = {}
    name_terms: list[str] = []
    unsupported: list[str] = []
    applied: dict[str, str] = {}

    for raw_token in tokens:
        token = raw_token.strip()
        if not token:
            continue
        term = _unquote(token)

        result = _parse_one(term)
        if result is None:
            name_terms.append(term)
            continue

        key, value = result
        if key == "__unsupported__":
            unsupported.append(term)
        else:
            _accumulate_filter(filters, key, value)
            applied[key] = raw_token

    return ParsedQuery(
        filters=filters,
        name_terms=tuple(name_terms),
        unsupported=tuple(unsupported),
        applied_terms=tuple(applied[key] for key in sorted(applied)),
    )


def _parse_one(term: str) -> tuple[str, Any] | None:
    """Parse *term* as a single Scryfall syntax term.

    Returns ``None`` if the term is a bare word (not recognised syntax).
    Returns ``(key, value)`` if parsed, or ``("__unsupported__", term)`` if
    the term looks like syntax but cannot be mapped to an existing filter.
    """
    stripped = term.lstrip("-")
    is_negated = len(stripped) < len(term)

    # t:<value> / type:<value>
    m = re.match(r"^(?:t|type):(.+)$", stripped, re.IGNORECASE)
    if m:
        return _parse_type_filter(m.group(1), is_negated)

    # o:<value> / oracle:<value>
    m = re.match(r"^(?:o|oracle):(.+)$", stripped, re.IGNORECASE)
    if m:
        return ("oracle_text", m.group(1))

    # c:<letters> / c=<letters> / id:<letters> / id=<letters>
    m = re.match(r"^(c|id)([:=])([wubrgc]+)$", stripped, re.IGNORECASE)
    if m:
        return _parse_color_filter(
            m.group(1).lower(), m.group(2), m.group(3), is_negated
        )

    # mv / cmc / pow / power / tou / toughness with comparator
    result = _parse_numeric_comparator(term)
    if result is not None:
        field_lower, op, num_val = result
        if field_lower in ("mv", "cmc"):
            return _mana_bounds(op, num_val)
        elif field_lower in ("pow", "power"):
            return _power_bounds(op, num_val)
        elif field_lower in ("tou", "toughness"):
            return _toughness_bounds(op, num_val)

    # r:<value> / rarity:<value>
    m = re.match(r"^(?:r|rarity):(.+)$", stripped, re.IGNORECASE)
    if m:
        alias = m.group(1).strip().lower()
        if alias in RARITY_ALIASES:
            return ("rarity", RARITY_ALIASES[alias])
        return ("__unsupported__", term)

    # is: keyword
    if re.match(r"^is:.+$", stripped, re.IGNORECASE):
        return ("__unsupported__", term)

    # f: (format)
    if re.match(r"^f:.+$", stripped, re.IGNORECASE):
        return ("__unsupported__", term)

    # set: / e: (set code)
    if re.match(r"^(?:set|e):.+$", stripped, re.IGNORECASE):
        return ("__unsupported__", term)

    # Negated term that doesn't match known syntax
    if is_negated and re.match(r"^-\w", term):
        rest = term[1:]
        if not re.match(
            r"^(?:t|type|o|oracle|c|id|r|rarity|mv|cmc|pow|power|tou|toughness)\b",
            rest,
            re.IGNORECASE,
        ):
            return ("__unsupported__", term)

    # Boolean operators and parentheses
    if term.lower() in ("or", "and", "not"):
        return ("__unsupported__", term)
    if term in ("(", ")"):
        return ("__unsupported__", term)

    # Incomplete syntax prefix
    if re.match(
        r"^(?:t|type|o|oracle|c|id|r|rarity|is|f|set|e|mv|cmc|pow|power|tou|toughness)[:=]?$",
        term,
        re.IGNORECASE,
    ):
        return ("__unsupported__", term)

    return None


def _parse_type_filter(value: str, is_negated: bool) -> tuple[str, Any]:
    """Map a type/supertype/subtype term to a filter key and value."""
    val_lower = value.lower()
    op = "not" if is_negated else "is"

    for st in _CANONICAL_SUPERTYPES:
        if st.lower() == val_lower:
            return ("super_type", {"value": st, "op": op})

    for ct in _CANONICAL_CARD_TYPES:
        if ct.lower() == val_lower:
            return ("card_type", {"value": ct, "op": op})

    return ("sub_type", {"value": value, "op": op})


def _parse_color_filter(
    prefix: str,
    separator: str,
    letters: str,
    is_negated: bool,
) -> tuple[str, Any]:
    """Map a color/identity term to a filter key and value.

    Mapping rules (also cited in the spec and report):
    - ``c:WUG`` → ``color_mode="include"`` (Scryfall default is OR-match)
    - ``c=WUG`` → ``color_mode="exactly"`` (Scryfall ``c=`` is exact)
    - ``id:WUG`` → ``color_mode="include"`` (no identity filter exists;
      colours are the closest proxy)
    - ``id=WUG`` → ``color_mode="include"`` (same)
    - Negated (``-c:W``) → ``color_mode="exclude"``
    """
    colors = [c.upper() for c in letters.lower() if c in COLOR_LETTERS]
    if not colors:
        return ("__unsupported__", f"{prefix}:{letters}")

    if is_negated:
        mode = "exclude"
    elif prefix == "c" and separator == "=":
        mode = "exactly"
    elif prefix == "c" and separator == ":":
        mode = "include"
    else:
        mode = "include"

    return ("colors", {"colors": colors, "mode": mode})


def _parse_numeric_comparator(
    term: str,
) -> tuple[str, str, int] | None:
    """Parse a numeric comparator like ``mv<=3`` or ``pow>=2``.

    Returns ``(field, op, value)`` or ``None``.
    The separator ``:`` is treated as ``=``.
    """
    m = _COMPARATOR_RE.match(term)
    if m:
        op_raw = m.group(2)
        if op_raw == ":":
            op_raw = "="
        return (m.group(1).lower(), op_raw, int(m.group(3)))
    return None


def _mana_bounds(op: str, num_value: int) -> tuple[str, Any]:
    lo, hi = _bounds_from_op(op, num_value)
    return ("mana", {"mana_min_bound": lo, "mana_max_bound": hi})


def _power_bounds(op: str, num_value: int) -> tuple[str, Any]:
    lo, hi = _bounds_from_op(op, num_value)
    return ("power", {"power_min_bound": lo, "power_max_bound": hi})


def _toughness_bounds(op: str, num_value: int) -> tuple[str, Any]:
    lo, hi = _bounds_from_op(op, num_value)
    return ("toughness", {"toughness_min_bound": lo, "toughness_max_bound": hi})


def _bounds_from_op(op: str, num_value: int) -> tuple[int | None, int | None]:
    """Convert a comparator into (lo, hi) bounds.

    Strict ``<N`` becomes max ``N-1``; strict ``>N`` becomes min ``N+1``.
    """
    if op == "=":
        return (num_value, num_value)
    elif op == "<":
        return (None, num_value - 1)
    elif op == "<=":
        return (None, num_value)
    elif op == ">":
        return (num_value + 1, None)
    elif op == ">=":
        return (num_value, None)
    return (None, None)


def _accumulate_filter(filters: dict[str, Any], key: str, value: Any) -> None:
    """Merge *value* into *filters* under *key*, accumulating if needed.

    For keys in ``_PER_ACCUMULATE`` (e.g. ``oracle_text``), repeated values
    are joined with a single space.
    """
    if key in _PER_ACCUMULATE:
        existing = filters.get(key, "")
        filters[key] = f"{existing} {value}" if existing else value
    elif key == "mana":
        filters["mana_min_bound"] = value["mana_min_bound"]
        filters["mana_max_bound"] = value["mana_max_bound"]
    elif key == "power":
        filters["power_min_bound"] = value["power_min_bound"]
        filters["power_max_bound"] = value["power_max_bound"]
    elif key == "toughness":
        filters["toughness_min_bound"] = value["toughness_min_bound"]
        filters["toughness_max_bound"] = value["toughness_max_bound"]
    elif key == "super_type":
        filters["super_type"] = value["value"]
        filters["super_op"] = value["op"]
    elif key == "card_type":
        filters["card_type"] = value["value"]
        filters["type_op"] = value["op"]
    elif key == "sub_type":
        filters["sub_type"] = value["value"]
        filters["sub_op"] = value["op"]
    elif key == "colors":
        filters["colors"] = value["colors"]
        filters["color_mode"] = value["mode"]
    elif key == "rarity":
        filters["rarity"] = value
    else:
        filters[key] = value
