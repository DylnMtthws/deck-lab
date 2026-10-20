"""Versioned local rule snapshots and attributed card rulings; no runtime network."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

WIZARDS_HOSTS = frozenset(
    {"media.wizards.com", "magic.wizards.com", "www.magic.wizards.com"}
)
MAX_SOURCE_BYTES = 8_000_000
MAX_RULES = 20_000
MAX_RULE_CHARS = 20_000
MAX_HIT_CHARS = 1_200
MAX_QUERY_CHARS = 300
MAX_SCOPED_CARDS = 16
MAX_HITS = 6
# card_rulings.source prefix written by assistant_rulings_import.
SNAPSHOT_NAMESPACE = "scryfall-bulk"
# Cards whose Oracle text seeds concepts; later scope only contributes rulings.
CONCEPT_CARDS = 4

# Rule identifiers are case-insensitive in user text ("702.19C", "CR702.19c").
_RULE_REF = re.compile(r"(?<![\d.])(\d{3}\.\d+[a-z]?)(?![\w])", re.IGNORECASE)
_RULE_START = re.compile(r"^(\d{3}\.\d+[a-z]?)\.?[ \t]+", re.MULTILINE)
# A section heading ("2. Parts of a Card", "200. General") or the trailing
# glossary/credits ends the preceding rule body.
_SECTION_END = re.compile(
    r"\n(?:\d{1,3}\.[ \t]|Glossary[ \t]*$|Credits[ \t]*$)", re.MULTILINE
)
_HEADER = re.compile(r"^\d{3}\.\d+\.?\s+([A-Za-z][A-Za-z ,'\-]{1,50})$")
_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "what",
        "how",
        "can",
        "you",
        "your",
        "does",
        "have",
        "from",
        "into",
        "are",
        "but",
        "not",
        "was",
        "were",
        "will",
        "would",
        "could",
        "should",
        "when",
        "which",
        "who",
        "why",
        "its",
        "it's",
        "my",
        "mine",
        "our",
        "any",
        "all",
        "also",
        "than",
        "then",
        "them",
        "they",
        "there",
        "their",
        "about",
        "after",
        "before",
        "under",
        "over",
        "cut",
        "add",
        "run",
        "swap",
        "replace",
        "better",
        "worse",
        "good",
        "bad",
        "best",
    ]
)
# Frequent in Comprehensive Rules prose; they count, but cannot carry a match.
_GENERIC = frozenset(
    [
        "turn",
        "turns",
        "game",
        "player",
        "players",
        "control",
        "controls",
        "controller",
        "cost",
        "costs",
        "ability",
        "abilities",
        "spell",
        "spells",
        "card",
        "cards",
        "creature",
        "creatures",
        "permanent",
        "permanents",
        "commander",
        "effect",
        "effects",
        "time",
        "way",
        "work",
        "works",
        "use",
        "get",
        "make",
        "play",
        "comes",
        "deck",
        "rule",
        "rules",
    ]
)
# Keyword-action names that are everyday verbs; FTS handles them instead of a
# header match, which would otherwise fire on "should I play this".
_COMMON_ACTIONS = frozenset(
    [
        "activate",
        "attach",
        "cast",
        "counter",
        "create",
        "destroy",
        "discard",
        "double",
        "exchange",
        "exile",
        "play",
        "reveal",
        "sacrifice",
        "search",
        "shuffle",
        "vote",
    ]
) | {"tap and untap"}
_TAP_TERMS = ("tap", "haste", "sickness")
_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\b(?:un)?tap|\{t\}|summoning|sickness|haste", _TAP_TERMS),
    (r"\bamass|\barmy", ("amass", "army")),
    (r"\bsacrific|\bdies\b", ("sacrifice", "dies")),
)
_SICKNESS_ANCHORS = ("302.6", "602.5a", "107.5")
# Mana-ability anchors as (expected rule ID, defining phrase from that rule's
# text). Numbering moves between editions, so the ID is only trusted when its
# loaded text contains the phrase; otherwise the one rule containing the phrase
# is used, and an ambiguous or missing phrase yields nothing rather than a guess.
_TAPPED_FOR_MANA = ("106.12", "tap [a permanent] for mana")
_TAPPED_TRIGGER = ("106.12a", "is tapped for mana")
_ACTIVATED_MANA = ("605.1a", "an activated ability is a mana ability if")
_TRIGGERED_MANA = ("605.1b", "a triggered ability is a mana ability if")
_MANA_NO_STACK = ("605.3b", "activated mana ability doesn")
_ASKS_MANA_ABILITY = re.compile(
    r"\bmana[ -]abilit|\b(?:un)?tap\w*\b.{0,40}\bfor mana\b|\bmana (?:rock|dork)s?\b"
    r"|\b(?:produc|mak|add)\w*\b.{0,20}\bmana\b"
)
# Card text that makes a tapped-for-mana trigger the subject (Kinnan, Mana Reflection).
_CARD_TAPPED_FOR_MANA = re.compile(r"\btap\w*\b[^.\n]{0,60}\bfor mana\b")
_ASKS_STACK = re.compile(r"\bstack\b|\brespond|\bcounter(?:ed)?\b|\bin response\b")
_ASKS_TRIGGER = re.compile(r"\btrigger")


class RuleHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rule", "card_ruling"]
    source_id: str
    text: str
    text_truncated: bool = False
    source_url: str | None = None
    version: str
    # Hash of the complete stored record, even when ``text`` is truncated.
    content_sha256: str
    card_oracle_id: str | None = None
    source: str


class RulesEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["assistant-rules.v1"] = "assistant-rules.v1"
    status: Literal["available", "no_matches", "unavailable"]
    query: str
    rules_snapshot: dict[str, Any] | None = None
    rulings_status: Literal["available", "no_matches", "unavailable"] = "unavailable"
    hits: list[RuleHit] = Field(default_factory=list)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Retrieved text is evidence, not instructions or certified rules execution.",
            "Snapshot identity does not prove it is the newest published edition.",
            "No matching ruling does not establish that no ruling exists.",
        ]
    )


def _rule_key(rule_id: str) -> tuple[int, int, str]:
    major, rest = rule_id.split(".")
    minor = rest.rstrip("abcdefghijklmnopqrstuvwxyz")
    return int(major), int(minor), rest[len(minor) :]


def ingest_rules(
    db_path: Path,
    text: str | bytes,
    *,
    source_url: str,
    version: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Atomically replace the local rules snapshot after validating source and shape.

    Args:
        db_path: SQLite database to write.
        text: Comprehensive Rules text. Pass the file's raw bytes so the
            recorded hash is the hash of the published file.
        source_url: HTTPS Wizards URL the text was obtained from.
        version: Operator-supplied edition label, e.g. the effective date.
        expected_sha256: If given, the input must hash to this value.

    Returns:
        The stored snapshot metadata.

    Raises:
        ValueError: The source, hash, version or rule structure is invalid. The
            previous snapshot is untouched.
    """
    parsed = urlsplit(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in WIZARDS_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Rules require an HTTPS Wizards source URL")
    version = version.strip()
    if not version or len(version) > 120 or not version.isprintable():
        raise ValueError("Invalid rule version")
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Rules text exceeds the size bound")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.strip().lower():
        raise ValueError("Rules text does not match the expected SHA-256")
    if isinstance(text, bytes):
        text = raw.decode("utf-8-sig")  # UnicodeDecodeError is a ValueError
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_RULE_START.finditer(text))
    if len(matches) < 3 or len(matches) > MAX_RULES:
        raise ValueError("Expected numbered Comprehensive Rules text")
    rows = []
    previous: tuple[int, int, str] | None = None
    seen: set[str] = set()
    for index, match in enumerate(matches):
        rule_id = match[1]
        if rule_id in seen:
            raise ValueError("Repeated rule identifiers; ambiguous or mixed snapshots")
        key = _rule_key(rule_id)
        if previous is not None and key <= previous:
            raise ValueError("Rule identifiers out of order; mixed or reordered text")
        seen.add(rule_id)
        previous = key
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = _SECTION_END.split(text[match.start() : end].strip(), maxsplit=1)[0]
        body = body.strip()
        if len(body) > MAX_RULE_CHARS or len(body) < len(rule_id) + 5:
            raise ValueError(f"Invalid rule section size at {rule_id}")
        rows.append((rule_id, body, hashlib.sha256(body.encode()).hexdigest()))
    metadata = {
        "source_url": source_url,
        "source_version": version,
        "content_sha256": digest,
        "rules_count": len(rows),
    }
    # Autocommit mode with explicit BEGIN/COMMIT so the schema creation and the
    # replacement are one transaction; any failure rolls back to the old snapshot.
    with closing(sqlite3.connect(db_path, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assistant_rules (rule_id TEXT PRIMARY KEY, text TEXT NOT NULL, content_sha256 TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS assistant_rules_fts USING fts5(rule_id UNINDEXED, text)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS assistant_rules_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_url TEXT NOT NULL, source_version TEXT NOT NULL, content_sha256 TEXT NOT NULL, rules_count INTEGER NOT NULL)"
            )
            conn.execute("DELETE FROM assistant_rules")
            conn.execute("DELETE FROM assistant_rules_fts")
            conn.executemany("INSERT INTO assistant_rules VALUES (?,?,?)", rows)
            conn.executemany(
                "INSERT INTO assistant_rules_fts VALUES (?,?)",
                [(r[0], r[1]) for r in rows],
            )
            conn.execute(
                "INSERT OR REPLACE INTO assistant_rules_metadata VALUES (1,?,?,?,?)",
                tuple(metadata.values()),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return metadata


def _card_names(card: dict[str, Any]) -> list[str]:
    name = str(card.get("name") or "").strip()
    if not name:
        return []
    names = [name, *(face.strip() for face in name.split("//"))]
    # "Lathril, Blade of the Elves" is routinely shortened to "Lathril".
    names += [n.split(",")[0].strip() for n in names if "," in n]
    return sorted({n.lower() for n in names if len(n) >= 3}, key=len, reverse=True)


def _scope_cards(cards: Any) -> list[dict[str, Any]]:
    """Deduplicate the card scope by Oracle ID (or name) and enforce its bound."""
    scoped: dict[str, dict[str, Any]] = {}
    for card in list(cards):
        if not isinstance(card, dict):
            continue
        key = (
            str(card.get("oracle_id") or "").strip()
            or "name:" + str(card.get("name") or "").strip().lower()
        )
        if key != "name:":
            scoped.setdefault(key, card)
    if len(scoped) > MAX_SCOPED_CARDS:
        raise ValueError("Rules card scope maximum is 16 unique cards")
    return list(scoped.values())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _query_terms(query: str, cards: list[dict[str, Any]]) -> list[str]:
    """Content words of the query in order, with scoped card names removed.

    Card-name words ("Sol Ring", "Blade of the Elves") are identity, not rules
    concepts; left in, they match unrelated rules such as the Ring tempting you.
    """
    text = query.lower()
    for card in cards:
        for name in _card_names(card):
            text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text)
    words = re.findall(r"\b[a-z]{3,}\b", text)
    return list(dict.fromkeys(w for w in words if w not in _STOPWORDS))[:12]


def _expansions(text: str) -> list[str]:
    terms: list[str] = []
    for trigger, words in _EXPANSIONS:
        if re.search(trigger, text):
            terms += words
    return list(dict.fromkeys(terms))


def _has_tap_cost_creature(cards: list[dict[str, Any]]) -> bool:
    for card in cards[:CONCEPT_CARDS]:
        if "creature" not in str(card.get("type_line") or "").lower():
            continue
        for line in str(card.get("oracle_text") or "").lower().splitlines():
            if ":" in line and re.search(r"\{(?:t|q)\}", line.split(":", 1)[0]):
                return True
    return False


def _fold(text: str) -> str:
    """Lowercase with typographic apostrophes folded, for phrase matching."""
    return text.lower().replace("’", "'")


def _mana_anchors(
    lowered: str, card_text: str, card_mode: bool
) -> list[tuple[str, str]]:
    """Mana-ability anchors that apply to the query and concept cards, in order."""
    card_tapped = bool(_CARD_TAPPED_FOR_MANA.search(card_text))
    asks = bool(_ASKS_MANA_ABILITY.search(lowered))
    # A tapped-for-mana card makes "mana" or "trigger" questions (and a plain
    # lookup of that card) about mana abilities; Sol Ring's "{T}: Add" does not.
    if not (
        asks or (card_tapped and (card_mode or re.search(r"\bmana\b|trigger", lowered)))
    ):
        return []
    anchors = [_TAPPED_FOR_MANA]
    trigger = card_tapped or bool(_ASKS_TRIGGER.search(lowered))
    if trigger:
        anchors += [_TRIGGERED_MANA, _TAPPED_TRIGGER, _ACTIVATED_MANA]
    else:
        anchors += [_ACTIVATED_MANA, _TRIGGERED_MANA]
    if _ASKS_STACK.search(lowered):
        anchors.insert(1, _MANA_NO_STACK)
    return anchors


def _interleave(*groups: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Alternate anchor groups so one concept cannot crowd out another."""
    out: list[sqlite3.Row] = []
    for index in range(max((len(g) for g in groups), default=0)):
        out += [g[index] for g in groups if index < len(g)]
    return out


def _header_name(text: str) -> str | None:
    match = _HEADER.match(text.strip())
    return match[1].strip().lower() if match and "\n" not in text else None


class _Snapshot:
    """Read helpers over one open read-only connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.integrity_failures: list[str] = []

    def get(self, rule_id: str) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM assistant_rules WHERE rule_id=?", (rule_id.lower(),)
        ).fetchone()
        return row if row is None or self.verified(row) else None

    def children(self, rule_id: str, limit: int = 2) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM assistant_rules WHERE rule_id GLOB ? ORDER BY rule_id LIMIT ?",
            (rule_id + "[a-z]", limit),
        ).fetchall()
        return [r for r in rows if self.verified(r)]

    def anchor(self, anchor: tuple[str, str]) -> sqlite3.Row | None:
        """The rule defining ``anchor``'s phrase in this edition, or None."""
        rule_id, phrase = anchor
        row = self.get(rule_id)
        if row is not None and phrase in _fold(row["text"]):
            return row
        # instr over a folded copy: the phrase is data, never SQL or a pattern.
        rows = self.conn.execute(
            "SELECT * FROM assistant_rules WHERE instr(lower(replace(text, '’', '''')), ?) > 0 ORDER BY rule_id LIMIT 2",
            (phrase,),
        ).fetchall()
        if len(rows) != 1:
            return None
        return rows[0] if self.verified(rows[0]) else None

    def verified(self, row: sqlite3.Row) -> bool:
        if hashlib.sha256(row["text"].encode()).hexdigest() == row["content_sha256"]:
            return True
        self.integrity_failures.append(row["rule_id"])
        return False

    def expand(self, row: sqlite3.Row, *, refs: int = 2) -> list[sqlite3.Row]:
        """The rule itself (or a header's subrules), its parent and its references."""
        if _header_name(row["text"]):
            return self.children(row["rule_id"])
        out = [row]
        parent = re.sub(r"[a-z]$", "", row["rule_id"])
        if parent != row["rule_id"]:
            parent_row = self.get(parent)
            if parent_row is not None and not _header_name(parent_row["text"]):
                out.append(parent_row)
        cited = [
            r.lower()
            for r in dict.fromkeys(_RULE_REF.findall(row["text"]))
            if r.lower() not in (row["rule_id"], parent)
        ]
        for ref in cited[:refs]:
            ref_row = self.get(ref)
            if ref_row is not None:
                out += (
                    self.children(ref, 1)
                    if _header_name(ref_row["text"])
                    else [ref_row]
                )
        return out


def _rule_rows(
    snap: _Snapshot, query: str, cards: list[dict[str, Any]], result: RulesEvidence
) -> list[sqlite3.Row]:
    """Ordered candidate rule rows: explicit IDs, anchors, keywords, then concepts."""
    lowered = query.lower()
    concept_cards = cards[:CONCEPT_CARDS]
    card_text = " ".join(str(c.get("oracle_text") or "") for c in concept_cards).lower()
    terms = _query_terms(query, cards)
    specific = [t for t in terms if t not in _GENERIC]
    # With no rules words left after removing card names (e.g. a card-details
    # lookup by name), the scoped cards' own text supplies the concepts.
    card_mode = not specific
    # Summoning sickness restricts the creature's own {T}/{Q} costs, not tapping
    # other creatures as a cost; surface the loaded edition's text for both.
    asks_tap = re.search(r"summon|sick|haste|\b(?:un)?tap|\{t\}", lowered)
    tap_creature = _has_tap_cost_creature(cards)
    expansion = _expansions(lowered + (" " + card_text if card_mode else ""))
    if not (asks_tap or tap_creature):
        # A mana rock's "{T}:" is not a question about summoning sickness.
        expansion = [t for t in expansion if t not in _TAP_TERMS]
    primary: list[sqlite3.Row] = []

    for rule_id in dict.fromkeys(r.lower() for r in _RULE_REF.findall(query)):
        row = snap.get(rule_id)
        if row is None:
            result.limitations.append(
                f"Requested rule {rule_id} is not in the loaded snapshot; numbering varies by edition."
            )
        else:
            primary += snap.expand(row)

    mana_rows = [
        row
        for row in (
            snap.anchor(a) for a in _mana_anchors(lowered, card_text, card_mode)
        )
        if row is not None
    ]
    sickness_rows: list[sqlite3.Row] = []
    if (asks_tap and (tap_creature or re.search(r"summon|sick|haste", lowered))) or (
        card_mode and tap_creature
    ):
        for rule_id in _SICKNESS_ANCHORS:
            row = snap.get(rule_id)
            if row is not None:
                # With mana anchors also due, the anchors alone; their parents
                # and references would crowd the mana rules out of the limit.
                sickness_rows += [row] if mana_rows else snap.expand(row, refs=1)
    primary += _interleave(sickness_rows, mana_rows)

    # Keyword sections ("701.x Amass", "702.x Trample") by header, so numbering
    # changes between editions do not matter.
    headers = snap.conn.execute(
        "SELECT * FROM assistant_rules WHERE (rule_id GLOB '701.*' OR rule_id GLOB '702.*') AND length(text) <= 80 ORDER BY rule_id"
    ).fetchall()
    haystacks = [lowered] + ([card_text] if card_mode else [])
    keyword_ids: list[str] = []
    for haystack in haystacks:
        for row in headers:
            name = _header_name(row["text"])
            if (
                name
                and name not in _COMMON_ACTIONS
                and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", haystack)
                and row["rule_id"] not in keyword_ids
            ):
                keyword_ids.append(row["rule_id"])
    for rule_id in keyword_ids[:2]:
        primary += snap.children(rule_id, 3)

    fts_terms = list(dict.fromkeys(terms + expansion))[:20]
    ranked: list[tuple[float, int, sqlite3.Row]] = []
    if fts_terms:
        rows = snap.conn.execute(
            "SELECT r.* FROM assistant_rules_fts f JOIN assistant_rules r ON r.rule_id=f.rule_id WHERE assistant_rules_fts MATCH ? ORDER BY bm25(assistant_rules_fts), r.rule_id LIMIT 40",
            (" OR ".join('"' + t + '"' for t in fts_terms),),
        ).fetchall()
        threshold = 3 if len(specific) >= 2 else 2
        # A row needs a specific query word, or with none, a concept expansion.
        required = set(specific) if specific else set(expansion) - set(terms)
        for position, row in enumerate(rows):
            if _header_name(row["text"]):
                continue
            words = _tokens(row["text"])
            score = (
                2 * len(set(specific) & words)
                + len((set(terms) - set(specific)) & words)
                + len((set(expansion) - set(terms)) & words)
            )
            if not required & words:
                continue  # generic words alone are not a match
            if score >= threshold:
                ranked.append((-score, position, row))
    ranked.sort(key=lambda item: item[:2])
    concept_rows = [row for _, _, row in ranked if snap.verified(row)]
    if concept_rows:
        primary += snap.expand(concept_rows[0], refs=1)
    return primary + concept_rows[1:]


def _ruling_rows(
    conn: sqlite3.Connection, cards: list[dict[str, Any]]
) -> list[sqlite3.Row] | None:
    """Stored rulings for scoped Oracle IDs; None when the table is unusable."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(card_rulings)")}
    if not {"id", "card_oracle_id", "ruling_text"} <= columns:
        return None
    optional = ", ".join(
        col if col in columns else f"NULL AS {col}" for col in ("ruling_date", "source")
    )
    rows: list[sqlite3.Row] = []
    for card in cards:
        if card.get("oracle_id"):
            rows += conn.execute(
                f"SELECT id, card_oracle_id, ruling_text, {optional} FROM card_rulings WHERE card_oracle_id=? AND trim(coalesce(ruling_text,''))<>'' ORDER BY ruling_date DESC, id LIMIT 8",
                (str(card["oracle_id"]),),
            ).fetchall()
    return rows


def retrieve_rules(
    db_path: Path,
    query: str,
    *,
    cards: Any = (),
    limit: int = 4,
    deadline_seconds: float | None = None,
) -> RulesEvidence:
    """Retrieve local rules, their parents/references and scoped stored rulings.

    Read-only, local and deterministic: no network or model call. Absence is
    reported as ``no_matches`` or ``unavailable``, never as proof that no rule
    or ruling applies.

    Args:
        db_path: SQLite database holding an ingested snapshot and card_rulings.
        query: 1–300 characters of question text; rule numbers are honored.
        cards: Up to 16 unique card dicts (oracle_id, name, type_line,
            oracle_text). The first four seed concepts; all scope rulings.
        limit: Maximum hits, 1–6.

    Returns:
        Bounded evidence with explicit rule and ruling availability.

    Raises:
        ValueError: The query, card scope or limit is out of bounds.
    """
    if not query.strip() or len(query) > MAX_QUERY_CHARS or not 1 <= limit <= MAX_HITS:
        raise ValueError("Rules query must contain 1–300 characters; limit 1–6")
    if deadline_seconds is not None and not 0.01 <= deadline_seconds <= 1.0:
        raise ValueError("Rules deadline must be 0.01–1 second")
    deadline = (
        time.monotonic() + deadline_seconds if deadline_seconds is not None else None
    )
    scoped = _scope_cards(cards)
    result = RulesEvidence(status="unavailable", query=query)
    try:
        conn = sqlite3.connect(
            Path(db_path).resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=min(0.1, deadline_seconds) if deadline_seconds is not None else 5.0,
        )
        if deadline is not None:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    except sqlite3.Error:
        return result
    conn.row_factory = sqlite3.Row
    rule_hits: list[RuleHit] = []
    rulings: list[sqlite3.Row] = []
    snapshots: dict[str, dict[str, Any]] = {}
    with closing(conn):
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
        except sqlite3.Error:
            result.limitations.append("The local database could not be read.")
            return result
        if {
            "assistant_rules",
            "assistant_rules_metadata",
            "assistant_rules_fts",
        } <= tables:
            try:
                rule_hits = _collect_rule_hits(conn, query, scoped, limit, result)
            except sqlite3.Error:
                result.status = "unavailable"
                rule_hits = []
                result.limitations.append(
                    "The local rules query failed; rule evidence is unavailable, not absent."
                )
        if "card_rulings" in tables:
            if not any(c.get("oracle_id") for c in scoped):
                result.limitations.append(
                    "No card with an Oracle ID was in scope; card rulings were not searched."
                )
            else:
                try:
                    found = _ruling_rows(conn, scoped)
                except sqlite3.Error:
                    found = None
                if found is None:
                    result.limitations.append(
                        "The stored card rulings could not be read; ruling evidence is unavailable, not absent."
                    )
                else:
                    try:
                        snapshots = _ruling_snapshots(conn, tables, result)
                    except sqlite3.Error:
                        snapshots = {}
                    # Snapshot-namespace rows are cited only with their snapshot.
                    usable = [
                        r
                        for r in found
                        if _namespace(r["source"]) is None
                        or _namespace(r["source"]) in snapshots
                    ]
                    rulings = usable
                    if usable:
                        result.rulings_status = "available"
                    elif found:
                        result.limitations.append(
                            "Stored rulings without a consistent snapshot record were withheld; ruling evidence is unavailable, not absent."
                        )
                    else:
                        result.rulings_status = "no_matches"
    ruling_hits = _ruling_hits(rulings, query, scoped, snapshots)
    if ruling_hits and rule_hits:
        # Reserve room for scoped rulings without letting them displace the only
        # rule when limit is 1.
        reserve = min(len(ruling_hits), 2, limit - 1)
    else:
        reserve = min(len(ruling_hits), limit)
    result.hits = rule_hits[: limit - reserve] + ruling_hits[:reserve]
    if result.hits:
        result.status = "available"
    elif result.status == "no_matches":
        result.limitations.append(
            "No matching rule text does not establish that no rule applies."
        )
    return result


def _collect_rule_hits(
    conn: sqlite3.Connection,
    query: str,
    cards: list[dict[str, Any]],
    limit: int,
    result: RulesEvidence,
) -> list[RuleHit]:
    meta = conn.execute(
        "SELECT * FROM assistant_rules_metadata WHERE singleton=1"
    ).fetchone()
    if meta is None:
        return []
    snapshot = {k: v for k, v in dict(meta).items() if k != "singleton"}
    result.rules_snapshot = snapshot
    stored = conn.execute("SELECT COUNT(*) FROM assistant_rules").fetchone()[0]
    if stored != snapshot.get("rules_count"):
        result.limitations.append(
            "The rules snapshot is inconsistent with its recorded rule count; it was not used."
        )
        return []
    result.status = "no_matches"
    snap = _Snapshot(conn)
    hits: list[RuleHit] = []
    seen: set[str] = set()
    for row in _rule_rows(snap, query, cards, result):
        if row["rule_id"] in seen:
            continue
        seen.add(row["rule_id"])
        text = row["text"]
        hits.append(
            RuleHit(
                kind="rule",
                source_id="CR " + row["rule_id"],
                text=text[:MAX_HIT_CHARS],
                text_truncated=len(text) > MAX_HIT_CHARS,
                source_url=snapshot["source_url"],
                version=snapshot["source_version"],
                content_sha256=row["content_sha256"],
                source="Wizards text snapshot; imported source identity",
            )
        )
        if len(hits) >= limit:
            break
    if snap.integrity_failures:
        result.limitations.append(
            "Stored rule text failed its hash check and was withheld: "
            + ", ".join(sorted(set(snap.integrity_failures))[:6])
        )
    return hits


def _namespace(source: Any) -> str | None:
    """The importer namespace of a stored ruling source, if it has one."""
    text = str(source or "")
    return SNAPSHOT_NAMESPACE if text.startswith(SNAPSHOT_NAMESPACE + ":") else None


def _ruling_snapshots(
    conn: sqlite3.Connection, tables: set[str], result: RulesEvidence
) -> dict[str, dict[str, Any]]:
    """Snapshot records whose stored row count still matches their rows."""
    if "card_rulings_snapshots" not in tables:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT namespace, source_url, source_version, content_sha256, rows_count FROM card_rulings_snapshots WHERE namespace=?",
        (SNAPSHOT_NAMESPACE,),
    ):
        stored = conn.execute(
            "SELECT COUNT(*) FROM card_rulings WHERE substr(source, 1, ?) = ?",
            (len(SNAPSHOT_NAMESPACE) + 1, SNAPSHOT_NAMESPACE + ":"),
        ).fetchone()[0]
        if stored == row["rows_count"]:
            out[row["namespace"]] = dict(row)
        else:
            result.limitations.append(
                "The Scryfall rulings snapshot is inconsistent with its recorded row count; its rulings were not used."
            )
    return out


def _ruling_hits(
    rows: list[sqlite3.Row],
    query: str,
    cards: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
) -> list[RuleHit]:
    terms = set(_query_terms(query, cards)) | set(_expansions(query.lower()))
    order = {str(c.get("oracle_id")): i for i, c in enumerate(cards)}
    ranked = sorted(
        rows,
        key=lambda r: (
            -len(terms & _tokens(str(r["ruling_text"]))),
            order.get(str(r["card_oracle_id"]), len(order)),
        ),
    )  # stable: newest-first order from SQL breaks remaining ties
    hits = []
    for row in ranked:
        text = str(row["ruling_text"])
        source = str(row["source"] or "unspecified")
        source_url = source if urlsplit(source).scheme == "https" else None
        snapshot = snapshots.get(_namespace(source) or "")
        if snapshot is not None:
            origin = source.split(":", 1)[1]
            source_url = snapshot["source_url"]
            source = (
                f"Scryfall bulk rulings {snapshot['source_version']} "
                f"(sha256 {snapshot['content_sha256'][:12]}); original source "
                f"labeled '{origin}' by Scryfall"
            )
        hits.append(
            RuleHit(
                kind="card_ruling",
                source_id="ruling:" + str(row["id"]),
                text=text[:MAX_HIT_CHARS],
                text_truncated=len(text) > MAX_HIT_CHARS,
                source_url=source_url,
                version=str(row["ruling_date"] or "undated"),
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                card_oracle_id=str(row["card_oracle_id"]),
                source=source
                + "; stored ruling, source authentication not independently verified",
            )
        )
    return hits
