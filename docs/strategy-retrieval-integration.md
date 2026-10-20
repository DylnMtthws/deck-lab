# Strategy retrieval integration contract

The worker supplied the retrieval/corpus interfaces below. Coordinator integration
now connects these to runtime planning, typed tools and candidate selection.
Deployment, seeding and measured live results are recorded separately in QA receipts.

## Compiled plan and runner

```python
from sabermetrics.assistant_retrieval_plan import compile_retrieval_plan, run_retrieval_plan

plan = compile_retrieval_plan(current_request)
receipt = run_retrieval_plan(
    local_reference_db, plan, commander=current_commander_name,
    check_current=check_owner_revision_and_cancellation,
    cards=current_legal_shortlist,       # optional, at most 64 supplied current records per batch
    card_view_vectors=loaded_artifact,    # optional JSON dict, loaded once by caller
)
```

`compile_retrieval_plan(request: str) -> dict` accepts 1–4000 characters. Its
JSON-serializable v1 result includes request/plan hashes, bounded literal tasks,
queries, task focus and evidence class. Queries cap at 320 characters; the
`query_truncated` flag records omissions. Literal cues select prerequisites,
drawbacks, comparison and rules; discovery is always present. There are at most
five tasks. This is a conservative attention compiler, not complete intent
classification. It must not establish requested card counts, legal actions,
preferences, measurement results, or authorization.

`run_retrieval_plan` accepts only an unchanged canonical plan. The callback runs
before every task and the optional card-view read; exceptions propagate. Root
must supply its existing owner/revision/cancellation checks and retain its legal,
cost, tool-call and action guards. This adapter grants no new authority. Related
strategy tasks share a bounded pool and select distinct aspect-matching passages;
uncovered aspects remain explicit. Production tool payloads serialize sources
once and reference them from task metadata. Rules use a separate official_rules-only
read and cannot fall back to authored strategy. `retrieval_reads` is at most two.
Task focus guides downstream synthesis; shared strategy results do not assert
independent evidence coverage for each task. Caller should disclose shortfalls.
No task asserts that an official snapshot is current or sufficient to prove an
interaction. Results preserve existing source identities and uncertainty.

The existing `retrieve_strategy_evidence` entry point remains compatible, adding
optional `source_kinds`. Ranking fuses BM25/cosine rank channels with existing
coverage/commander signals; source-backed results are capped at two passages per
document URL/title. Activated versions only compete. Exact duplicated passages
within a source version are suppressed. `passage_bounds` gives absolute source
character offsets for the exact <=600-character excerpt. `parent_context` adds
<=1000 contiguous characters from the same source ID/version, with independent
bounds. It never borrows adjacent text from another version/document. Legacy rows
without metadata retain lexical retrieval and their existing citation shape.

## Card views and optional offline vectors

`build_card_views(card)` returns versioned printed-effect, functional-role and
prerequisite projections with separate view hashes and a source hash. Printed
facts preserve supplied text and provenance; roles/prerequisites are explicitly
generated inference. Existing bounded recognizers supply roles and draw-route
prerequisites; they do not cover all effects or certify execution. Multifaces keep
their supplied facts while unsupported inference abstains.

`prepare_card_view_vectors(cards, encoder=None)` is an explicit offline operation.
It encodes three distinct view texts per card in batches of 16, retaining the
pinned model identity, view/source/text hashes and projection version. To prepare
an artifact when authorized, with a pre-cached model configured:

```sh
PYTHONPATH=src /Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python \
  scripts/prepare_assistant_card_views.py --cards public-card-facts.json \
  --output card-view-vectors.json
```

No such operation was run during implementation. No new dependency is required.
The existing local encoder loader never downloads models. Operator-supplied
encoders in tests implement `_model_name` and `embed`, optionally `embed_many`.
`retrieve_card_views(cards, query, vector_artifact=artifact)` embeds only the
query once, never cards or the corpus. It validates each view/model/text/source
version and vector shape/finite norm, unions semantic and lexical matches and
returns per-view cosine/matched-channel evidence. Missing, stale or unavailable
vectors preserve lexical retrieval. The shared local encoder caches query vectors;
caller should load the JSON artifact once per session. Production recall batches
the current eligible catalog before shortlist limits and uses all semantic matches
from each batch, then retains the top 24 positive scores across the complete union,
with deterministic identity tie breaks. Cosines are uncalibrated ranking signals;
there is no universal relevance threshold. Empty generated projections cannot
nominate candidates. Results describe discovery
relevance, never strategic quality. Runner passes the optional artifact through.

## Corpus and offline import

The seed adds 48 original passages across 24 themes to existing packaged sources.
Every added passage identifies Deck Lab/OpenAI Codex AI authorship, date/version,
original source class, thematic commander anchor and limitations. Two distinct
passages per theme separate setup/function from drawbacks/comparison. These are
unreviewed original heuristics; commander-specific mechanics still require fresh
card evidence. Primary links point to the existing versioned official rules
snapshot with their limited scope stated; they are not external primer attribution
or endorsements of the strategic assessment.

`scripts/seed_strategy_corpus.py --db-path ...` explicitly imports the bundle;
`--embed` remains an opt-in offline operation. Runtime retrieval does not seed.
External primers were not imported, scraped or labeled permissioned/reviewed.
Public accessibility alone does not supply permission to copy a corpus. To import
operator-provided licensed text offline:

```sh
PYTHONPATH=src python scripts/ingest_strategy_corpus.py --db-path local.db \
  --text-file licensed.txt --document 'Actual source title' \
  --source-url 'https://actual-source.example/revision' --version 'actual-revision' \
  --permission 'Actual license/permission with attribution requirements' \
  --author 'Actual author' --source-date YYYY-MM-DD \
  --source-kind licensed_strategy --source-class licensed_strategy \
  --source-key stable-source-section --limitations 'Actual scope and limitations'
```

The Python ingestion API additionally accepts `primary_links` with URL/version
and source-class fields. Wiki imports use `wiki_secondary` and require URL,
revision and source date. Permission is an operator assertion, not a legal
verification; no wiki text is bundled. Existing immutable-version, activation,
historical archive and renamed-source guards remain intact.

Focused tests use temporary SQLite databases and deterministic fake encoders.
They establish behavior of these contracts, not live embedding quality, external
permissions, expert strategy accuracy or end-to-end answer quality.
