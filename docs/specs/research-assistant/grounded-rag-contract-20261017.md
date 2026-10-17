# Grounded retrieval implementation contracts

## Module ownership

Coordinator owns `assistant_deck_facts.py`, `assistant_engine_graph.py`,
`assistant_context_rank.py`, integration in existing tools/runtime/review and
candidate selection, and their tests. The intended Claude Code Opus 5.5 task owns
`assistant_rules.py`, `assistant_rulings_import.py`, their offline preparation
CLIs and targeted tests. The coordinator drafted the rules implementation while authentication was
restored. Claude Code Opus 5.5 now owns its adversarial tests and corrections.
A separate Opus 5.5 session reviews the coordinator integration read-only.

Claude should act as a skeptical senior AI engineer at integration checkpoints.
Reviews must identify evidence gaps, false mechanical implications, stale-source
paths, bounds, owner isolation and integration defects. Approval is not assumed.
Do not run dev servers, full suites, browsers or e2e tests. Run only targeted tests
for files touched. No nested agents. No access to credentials or infrastructure.

## Public interfaces

All module outputs use Pydantic models with `extra=forbid` and a version string.
Functions do not mutate the deck, call remote services or decide legality.

`build_deck_facts(document, query="") -> DeckFacts` scans the complete current
library and commanders before bounding output. Per-feature support identifies
canonical Oracle ID, name, quantity and exact source clause. Fingerprints include
revision, zones, quantities and source text. Unknown/multiface coverage remains
visible. A zero detected count never proves absence.

`build_engine_graph(document) -> EngineGraph` returns bounded sourced edges with
provider identity, commander identity, relationship and conditions. Edges are
static inferred relationships, never battlefield availability or proof of a win.
`engine_loss_score(graph, outgoing, incoming=None) -> int` is a ranking preference,
not a veto. Relationship conditions must survive whenever their links are included; omitted links must be disclosed.

`rank_card_context(document, card, focus, graph=None) -> ContextRank` returns
separate requested-function matches, engine relationships and known prerequisites.
It does not use co-occurrence as proof of synergy, infer unknown prices, or veto
exploration merely because a generic role target is filled.

`retrieve_rules(db_path, query, *, cards=(), limit=4) -> RulesEvidence` performs
read-only local retrieval. Return `available`, `no_matches` or `unavailable`
explicitly. Each hit has `kind` (`rule` or `card_ruling`), stable source identifier,
exact text, source URL if known, version/date, hash and matched card identity when
applicable. Keep text <=1800 characters per hit, report truncation, and bound query
to 300 characters, card scope to <=16 unique Oracle IDs, hits to <=6. Rule-number
lookups and concept queries preserve relevant parent/exception clauses. Missing
rulings are not evidence that no ruling exists. Do not label mtgapi/Scryfall
records as independently authenticated official sources.

`ingest_rules(db_path, text, *, source_url, version) -> dict` is an offline explicit
operation. Require an HTTPS Wizards source URL, content hash and nonempty version;
reject ambiguous/repeated body sections rather than mixing editions or table of
contents. Replace the rules snapshot atomically after successful parsing; retain
the old snapshot on malformed input. No network calls in the runtime. Store compact
rule records and a local FTS index. Import existing `card_rulings` read-only.

The prep CLI accepts an explicit text file, source URL, version and database path.
No downloads or paid API calls. Rule refresh/deployment is separate from retrieval.

## Integration boundaries

The runtime receives a compact fresh deck-facts and engine summary per turn.
New read tools expose focused full-deck facts and rules retrieval without paging
through every card. `prepare_improvement` includes both contexts. Card details
include identifier resolution and relevant sourced rules/rulings. Replacement
construction prefers preserving engine relationships and prioritizes focus matches;
semantic review receives those facts within its existing input budget. Existing
constraints, revision checks and exact citations remain authoritative.

Reserve the last existing model call for a final answer without executing new
tools. If a model ignores that instruction, produce an evidence-based bounded
fallback rather than claiming an experiment was requested or prepared.

Live QA context-delivery contract: pin the most recent four distinct consulted
Oracle identities (catalog ID fallback) independently of tool history. Preserve
cost unknowns, source provenance, upstream truncation and explicit local text
clipping. A repeated lookup refreshes that identity without displacing another
card. At the 16,000-character conversation bound, omit supplementary retrieval
with a disclosure before discarding consulted printed facts. These facts do not
certify live source freshness or legality. Full receipts stay in persisted history.
