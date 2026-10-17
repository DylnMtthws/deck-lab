"""Complete-library evidence with bounded presentation and explicit coverage."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.deck_evidence import _library_entries


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardEvidence(FactModel):
    entry_id: str
    card_id: str
    oracle_id: str
    name: str
    quantity: int = Field(ge=1)
    source_quote: str
    quote_truncated: bool = False


class FeatureEvidence(FactModel):
    feature: str
    detected_quantity: int = Field(ge=0)
    cards: list[CardEvidence]
    omitted_cards: int = Field(ge=0)
    coverage: Literal["bounded_text_patterns"] = "bounded_text_patterns"


class DeckFacts(FactModel):
    version: Literal["assistant-deck-facts.v1"] = "assistant-deck-facts.v1"
    fingerprint: str
    revision: int
    library_entries_scanned: int
    library_quantity: int
    missing_text_cards: int
    unsupported_multiface_cards: int
    commanders: list[CardEvidence]
    commanders_omitted: int
    features: list[FeatureEvidence]
    matches: list[CardEvidence]
    matches_omitted: int
    scope: str = (
        "Every current-library entry scanned before output limits. Detected counts "
        "are incomplete pattern matches, not optimal targets or battlefield state. "
        "Zero detected does not establish absence; inspect actual text before claiming a gap."
    )


def graveyard_stock_quote(card: dict[str, Any]) -> str | None:
    """Recognize explicit own-library graveyard routes, including triggered mill."""
    if card.get("card_faces") or "//" in str(card.get("type_line") or ""):
        return None
    raw = str(card.get("oracle_text") or "")
    for clause in re.split(r"\n|(?<=\.)\s+", raw):
        # A triggered imperative 'mill' applies to its controller unless an
        # explicit other subject is given. Unparsed opponent/target scope abstains.
        if re.search(
            r"\b(?:you )?mill (?:a|one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+|x|half|that many)\b",
            clause,
            re.IGNORECASE,
        ) and not re.search(
            r"opponent|target player|each player|that player|they mill|whenever you mill|if you (?:would )?mill|when you mill|player mills|controller mills",
            clause,
            re.IGNORECASE,
        ):
            return clause.strip()
        if re.search(
            r"\bput\b[^.]*\b(?:top|rest|them|those cards)\b[^.]*\binto your graveyard\b",
            clause,
            re.IGNORECASE,
        ):
            return clause.strip()
    return None


def feature_quotes(card: dict[str, Any]) -> dict[str, str]:
    """Return sourced positive matches; unsupported shapes never become negatives."""
    from sabermetrics.assistant_strategy import draw_routes, strategy_capabilities

    if card.get("card_faces") or "//" in str(card.get("type_line") or ""):
        return {}
    raw = str(card.get("oracle_text") or "")
    result: dict[str, str] = {}
    stock = graveyard_stock_quote(card)
    if stock:
        result["graveyard_stock"] = stock
    for capability in ("sacrifice_outlet", "nonland_tap_mana", "graveyard_access"):
        if capability in strategy_capabilities(card):
            patterns = {
                "sacrifice_outlet": r"(?:^|[.\n])([^:\n.]*sacrifice (?:another |a |an )(?:creature|artifact)[^:\n.]*:[^.\n]*)",
                "nonland_tap_mana": r"(?:^|\n)(\{T\}: Add [^.\n]*)",
                "graveyard_access": r"\b((?:return|cast|play)\b[^.\n]*\bfrom your graveyard\b[^.\n]*)",
            }
            match = re.search(patterns[capability], raw, re.IGNORECASE)
            if match:
                result[capability] = match[1].strip()
    for route in draw_routes(card):
        result.setdefault("draw", route.source_quote)
    for pattern, feature in (
        (r"[^\n.]*\b(?:dies|die)\b[^\n.]*\bdraw\b[^\n.]*", "death_draw"),
        (r"[^\n.]*triggers?[^\n.]*additional time[^\n.]*", "trigger_multiplier"),
        (
            r"[^\n.]*\b(?:hexproof|indestructible|protection from)\b[^\n.]*",
            "protection",
        ),
        (r"[^\n.]*\b(?:sacrifice|sacrificing)\b[^\n.]*", "sacrifice_cost_or_effect"),
    ):
        match = re.search(pattern, raw, re.IGNORECASE)
        if match and not (
            feature == "protection"
            and (
                re.search(
                    r"\blose\b|can't have|opponents? control", match[0], re.IGNORECASE
                )
                or not re.search(
                    r"target (?:creature|permanent)|(?:creatures|permanents) you control|(?:equipped|enchanted) creature",
                    match[0],
                    re.IGNORECASE,
                )
            )
        ):
            result[feature] = match[0].strip()
    return result


def _card_evidence(card: dict[str, Any], quote: str) -> CardEvidence:
    return CardEvidence(
        entry_id=str(card.get("id") or ""),
        card_id=str(card.get("card_id") or ""),
        oracle_id=str(card.get("oracle_id") or ""),
        name=str(card.get("name") or ""),
        quantity=int(card.get("quantity", 1)),
        source_quote=quote[:1000],
        quote_truncated=len(quote) > 1000,
    )


def build_deck_facts(document: dict[str, Any], query: str = "") -> DeckFacts:
    """Scan the full current library, then bound source examples and query hits."""
    if len(query) > 300:
        raise ValueError("query maximum is 300 characters")
    library = sorted(
        _library_entries(document),
        key=lambda c: (str(c.get("name")), str(c.get("oracle_id"))),
    )
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    groups: dict[str, list[CardEvidence]] = {
        key: []
        for key in (
            "graveyard_stock",
            "sacrifice_outlet",
            "nonland_tap_mana",
            "graveyard_access",
            "draw",
            "death_draw",
            "trigger_multiplier",
            "protection",
            "sacrifice_cost_or_effect",
        )
    }
    query_terms = set(re.findall(r"\w+", query.lower())) - {
        "a",
        "an",
        "the",
        "my",
        "deck",
        "cards",
        "card",
        "for",
        "and",
        "or",
        "this",
        "that",
        "with",
        "which",
        "what",
        "support",
        "engine",
        "find",
    }
    if query_terms & {"mill", "graveyard", "reanimation", "stock", "filling"}:
        query_terms |= {"mill", "graveyard"}
    matching = []
    missing = multiface = 0
    for card in library:
        text = str(card.get("oracle_text") or "")
        missing += int(
            not text.strip()
            and "basic land" not in str(card.get("type_line") or "").lower()
        )
        multiface += int(
            bool(card.get("card_faces")) or "//" in str(card.get("type_line") or "")
        )
        for feature, quote in feature_quotes(card).items():
            groups[feature].append(_card_evidence(card, quote))
        words = set(re.findall(r"\w+", (str(card.get("name")) + " " + text).lower()))
        hits = len(query_terms & words)
        if hits:
            matching.append((hits, _card_evidence(card, text)))
    canonical = {
        "revision": document.get("revision", 0),
        "zones": document.get("zones", []),
        "cards": [
            {
                key: c.get(key)
                for key in (
                    "id",
                    "card_id",
                    "oracle_id",
                    "name",
                    "quantity",
                    "is_commander",
                    "zone_id",
                    "zone_name",
                    "oracle_text",
                    "type_line",
                    "card_faces",
                )
            }
            for c in sorted(document.get("entries", []), key=lambda c: str(c.get("id")))
        ],
    }
    matching.sort(key=lambda item: (-item[0], item[1].name, item[1].oracle_id))
    return DeckFacts(
        fingerprint=hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str).encode()
        ).hexdigest(),
        revision=int(document.get("revision", 0)),
        library_entries_scanned=len(library),
        library_quantity=sum(int(c.get("quantity", 1)) for c in library),
        missing_text_cards=missing,
        unsupported_multiface_cards=multiface,
        commanders=[
            _card_evidence(c, str(c.get("oracle_text") or "")) for c in commanders[:4]
        ],
        commanders_omitted=max(0, len(commanders) - 4),
        features=[
            FeatureEvidence(
                feature=key,
                detected_quantity=sum(c.quantity for c in rows),
                cards=rows[:8],
                omitted_cards=max(0, len(rows) - 8),
            )
            for key, rows in groups.items()
        ],
        matches=[card for _, card in matching[:12]],
        matches_omitted=max(0, len(matching) - 12),
    )


def summarize_deck_facts(facts: DeckFacts) -> dict[str, Any]:
    """Compact all feature counts and representative identities for every turn."""
    return {
        "version": facts.version,
        "fingerprint": facts.fingerprint,
        "revision": facts.revision,
        "library_entries_scanned": facts.library_entries_scanned,
        "missing_text_cards": facts.missing_text_cards,
        "unsupported_multiface_cards": facts.unsupported_multiface_cards,
        "commanders": [c.name for c in facts.commanders],
        "features": [
            {
                "feature": f.feature,
                "detected_quantity": f.detected_quantity,
                "examples": [c.name for c in f.cards[:4]],
                "examples_omitted": max(0, len(f.cards) - 4) + f.omitted_cards,
            }
            for f in facts.features
        ],
        "scope": facts.scope,
    }


def deck_facts_view(facts: DeckFacts) -> dict[str, Any]:
    """Fit source examples into the tool envelope while retaining every count."""
    view = facts.model_dump()
    for c in view["commanders"]:
        c["quote_truncated"] |= len(c["source_quote"]) > 240
        c["source_quote"] = c["source_quote"][:240]
    for feature in view["features"]:
        feature["omitted_cards"] += max(0, len(feature["cards"]) - 2)
        feature["cards"] = feature["cards"][:2]
        for c in feature["cards"]:
            c["quote_truncated"] |= len(c["source_quote"]) > 160
            c["source_quote"] = c["source_quote"][:160]
    view["matches_omitted"] += max(0, len(view["matches"]) - 4)
    view["matches"] = view["matches"][:4]
    for c in view["matches"]:
        c["quote_truncated"] |= len(c["source_quote"]) > 240
        c["source_quote"] = c["source_quote"][:240]
    while len(json.dumps(view, ensure_ascii=False)) > 7500:
        if view["matches"]:
            view["matches"].pop()
            view["matches_omitted"] += 1
        else:
            populated = [f for f in view["features"] if f["cards"]]
            if not populated:
                break
            feature = max(populated, key=lambda f: len(json.dumps(f["cards"])))
            feature["cards"].pop()
            feature["omitted_cards"] += 1
    return view


def scoped_rule_cards(document: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Scope rules to commanders and whole-name matches in the actual library."""
    commanders = [c for c in document.get("entries", []) if c.get("is_commander")]
    named = [
        c
        for c in _library_entries(document)
        if c.get("name")
        and re.search(
            r"(?<!\w)" + re.escape(str(c["name"])) + r"(?!\w)", query, re.IGNORECASE
        )
    ]
    unique = {
        str(c.get("oracle_id") or c.get("card_id") or c.get("id")): c
        for c in commanders + named
    }
    return list(unique.values())[:16]


LAND_COUNT_NOTE = (
    "Quantity-aware counts cover the full main-deck library, excluding commanders "
    "and sideboard. Back-land faces are counted separately; imported type lines do "
    "not establish that every back face can be played from hand. Do not call this a "
    "verified MDFC count. No colored-source count is implied."
)


def library_composition(document: dict[str, Any]) -> dict[str, Any]:
    """Quantity-aware main-library composition shared by inspect_deck and runtime.

    Excludes commanders and non-library zones through the canonical
    ``_library_entries``. Front-face lands and land back faces are separate.
    """
    from collections import Counter

    from sabermetrics.deck_evidence import _card_roles

    curve: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    front = back = total = 0
    for entry in _library_entries(document):
        quantity = int(entry.get("quantity") or 1)
        total += quantity
        mana = entry.get("mana_value")
        curve[str(mana) if mana is not None else "unknown"] += quantity
        for role in _card_roles(entry):
            roles[role] += quantity
        faces = str(entry.get("type_line") or "").split("//", 1)
        if "Land" in faces[0]:
            front += quantity
        elif len(faces) == 2 and "Land" in faces[1]:
            back += quantity
    return {
        "library_card_count": total,
        "front_land_count": front,
        "land_back_face_count": back,
        "land_count_note": LAND_COUNT_NOTE,
        "mana_curve": dict(curve),
        "role_counts": dict(roles),
    }
