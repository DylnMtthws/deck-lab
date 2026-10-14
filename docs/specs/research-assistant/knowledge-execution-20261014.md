# MTG knowledge and execution architecture

## Scope

Build's existing Chat, Analytics and Goldfish share a bounded knowledge/tool
layer. Suggestions remain previews; automatic improvement creates exactly three
copies with at most three replacements each. Production is outside this release.

1. Canonical facts: imported Oracle identities, faces, rulings, legality and
   price observations retain source, date, version and missing-data status.
2. Strategy retrieval: provenance-preserving ingestion, lexical BM25 plus
   compatible semantic vectors, rank fusion and contextual relevance ordering.
   Missing embedding configuration is explicit; no synthetic semantic vectors.
3. Mechanics: typed conditional capabilities, relationship evidence, supported
   archetype requirements and diverse constrained candidate ranking. Inference
   is separate from native execution support and rules verification.
4. Planning: the existing structured tool workflow consumes these records;
   current-turn progress and immutable recommendation handles remain bounded.
5. Measurement: use the existing native simulator and held-out comparisons;
   never infer a winner from non-comparable coverage or missing mechanics.
6. Memory/evaluation: owner-scoped preferences and past experiment receipts,
   current content freshness, and executable labeled regression cases with
   denominators, failure details and measured timing.

## Parallel handoffs

| Partner | Owned area | Acceptance evidence |
| --- | --- | --- |
| Codex Sol 6.1, canonical facts | Snapshot import and card evidence | Atomic validation; identity matching; faces/rulings/dates preserved; unknown freshness honest |
| Codex Sol 6.1, retrieval | Ingestion and hybrid source retrieval | Exact bounded excerpts and URL attribution; irrelevant abstention; incompatible vectors rejected; usable lexical fallback |
| Codex Sol 6.1, mechanics | Capability relationships and candidates | Conditional resource/protection negative cases; engine preservation; diversity; explicit unknowns |
| Claude Code Opus 5.5 | Memory and executable evaluations | Ownership, freshness, bounds, non-comparable results; empty/failing evaluations exit nonzero |
| Coordinator | Tool/runtime integration, review, QA | Existing limits and cancellation preserved; integrated tests; QA release identity; production unchanged |

Each handoff reports tests, failures and remaining limitations. No partner may
start another agent, dev server, full suite or browser. Heavy jobs run serially.

## Release gates

Run focused regressions, lint/type checks for touched modules, an executable
evaluation with real denominators, and a QA smoke of actual tool receipts. Check
source version, retrieval mode, capability uncertainty and experiment history in
the assistant context. Retain the previous native coverage warning; this work
does not certify all MTG cards or predict multiplayer win rates. State separately
which live corpus, embeddings and rules snapshots are actually installed.

## Implementation and operation

`prepare_improvement(focus)` performs the bounded read plan and retains the
existing stable recommendation handle. It returns the focus-specific assessment
from candidate discovery. `card_details` adds canonical snapshot provenance,
rulings, selected-printing price scope, and conditional capability relationships.
`research_memory` reads saved preferences and previous experiments without
mutating sessions, expiring runs or adopting any alternative.

Canonical facts are imported offline with `scripts/import_assistant_card_facts.py`.
Exact printing catalogs and Oracle-keyed catalogs are both supported; the original
source printing identity and raw snapshot hash remain separate from local IDs.
An undated or older snapshot cannot replace dated current evidence implicitly.
Rulings and text truncation are explicit. Missing snapshots retain catalog-only
status, not invented observation dates or live legality certification.

Build QA with `SABER_STRATEGY_SEMANTIC=1` to include FastEmbed and the pinned ONNX
model. `scripts/seed_strategy_corpus.py --db-path PATH --embed` explicitly installs
the starter corpus on a selected database; there is no startup migration into
production. The model runs locally with one CPU thread. Runtime does not download
models. Embeddings are checked against both source content and model assets.
New source imports retain historical versions but activate only the selected
source lineage version; `--archive` retains historical evidence without activation.

The packaged corpus contains selected September 25, 2026 official rules, a brief
attributed strategy quotation and twelve original Deck Lab strategy notes. The
supplied Moxfield primers returned HTTP 403 during this work and are not ingested.
Authored notes remain labeled authored guidance; they are not tournament evidence.
The importer supports supplied text with explicit permission, URL, version and
commander/archetype metadata so the library can expand without changing retrieval.

Semantic similarity and weighted reranking are relevance signals, not calibrated
confidence. Capability relations use `inferred` plus `oracle_text_match`; no
substring predicate is presented as verified rules execution. Native coverage
remains independently reported by the simulator. This release does not author
every missing native mechanic or certify multiplayer win rates.
