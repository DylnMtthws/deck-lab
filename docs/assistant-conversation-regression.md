# Research conversation regression gate

The frozen Marchesa scenario in
`tests/fixtures/assistant_marchesa_conversation.json` covers three connected
turns: draw discovery, preference refinement, and a focused Elegy Acolyte
comparison. It contains paraphrased requests and relevant public card mechanics,
not an account identifier or a private deck list.

## Runtime behavior

The existing planning call classifies discovery/comparison separately from
filling slots or swapping cards. Available slots do not establish a requested
addition count. Preference updates require exact quotes from supplied user turns;
later statements supersede earlier ones. These interpretations are scoped to the
conversation and grant no authority to alter a deck.

The provider planning schema requires a quote from the current user request,
its intent and deliverables, and separate preference assessment slots. An
unexpressed dimension is explicit null; it does not erase inherited memory.
Operational card-type and draw-control values are enums. Only preferences
quoting the current request are flattened into persisted updates. Quoted
provenance does not establish that the interpretation is semantically correct.

Reserved synthesis uses a small answer schema without tool definitions. Once
named-card comparisons resolve their focused sources, the next call uses that
schema directly. Empty replies cannot become successful answers through a
checker caveat. Malformed replies preserve confirmed source excerpts with an
incomplete label. Focused card names take priority over discovery candidates
when retaining and compacting sources.

Named cards already in the deck are refreshed and exposed to synthesis and
checking before their lookup is considered resolved. A name in the library
alone does not establish that the model received its printed facts. Extra
functional reads in the same batch are skipped after the two-query recall
bound is met; explicit card lookups and revision/cancellation checks remain.
Provider-facing free-text fields use length bounds rather than an unanchored
nonblank regex. Code checks meaningful nonblank text and current-request quote
provenance, including short user messages. Live provider behavior must be
checked separately from Pydantic's schema validation.

The existing answer checker also returns a typed fulfillment decision. An
explicit nonanswer can use one remaining primary call for a tool-free repair;
it does not receive a second checker call or a larger budget. Repaired prose is
incomplete and explicitly unchecked. Invalid repairs and exhausted capacity
preserve confirmed source excerpts instead of reporting a successful answer.
Partial fulfillment remains incomplete. These bounded model judgments and
field-presence constraints do not replace independent semantic assessment.

Production request interpretation now occupies a narrow first-call contract:
current request, recent user statements, saved preferences, commander facts and
capacity. It excludes historical assistant/tool transcripts and execution-tool
definitions. Its answer/use_tools route is executable; planning announcements
cannot terminate a research request. Execution keeps the full tool contract;
answer-only synthesis uses its own compact prompt. All phases share the same
five primary-call allowance. General research may spend one call planning before
tool selection; named facts can still resolve during that first pass. Requests
requiring actual measurements retain operational tool routing rather than
terminating on printed card facts. Planner decisions confer no action authority.
An empirical request without a successful current-turn operational receipt stays
incomplete, even if the model or checker declares it finished. A completed lookup
that finds no saved results can support an honest no-results answer. Receipt
existence does not certify metric validity or freshness.

Named-card comparisons prioritize their card facts before further discovery.
Excess tool batches reserve the next call for an answer-only schema. Duplicate
reads reuse checked receipts and trigger synthesis. The five conversation-call,
eight tool-call, cost, cancellation and revision protections remain in force.
If synthesis still fails its budget, a clearly labeled incomplete source extract
preserves confirmed facts; this is not a successful strategic assessment.

Previously retrieved card identities are refreshed from the current local fact
store, with current commander annotations recomputed. Old experiments and action
handles are never restored as current results. Full sources reach the checker
independently of shortened model context; excerpts remain explicitly marked.

`search_card_functions` separates capability queries from literal name lookup.
It reuses the shared legal catalog and generator-derived lexical/vector recall,
bounded associations, printed draw routes and current deck ingredients. Filters
precede ranking; scores mean discovery relevance, not quality or simulation.
Vector/cohort unavailability is explicit. Required optionality excludes detected
mandatory or unclassified routes; preferred optionality ranks rather than
excludes. Clause coverage remains partial. An unspecified mana ceiling is null;
the runtime corrects an accidental zero unless the user requested zero/free
cards. The correction is recorded with returned search results.
Base card types and preferred draw events are typed; `any` means no type filter.
Discovery verifies explicitly named examples and moves to synthesis after two
distinct successful nonempty functional queries. Rich search profiles are reduced
to a bounded shortlist with printed text and route conditions before envelope
compaction; returned cards must not disappear as one oversized metadata field.

## Capturing and scoring

Use `assistant_conversation_eval.capture_turn` to adapt each actual `run_turn`
result and externally measured elapsed time. Supply previous captured turns,
relevant actual initial card facts and independently checked deck integrity.
Never populate capture fields from expected answers. The adapter retains public
source text for review and strips account/deck identifiers and preference quotes.
The scorer checks the captured request digest against the frozen case prompt;
missing or mismatched provenance fails the deterministic gate.

Export the typed input contracts with:

```sh
python scripts/evaluate_assistant_conversation.py --schema evaluation/schema.json
```

Score a complete capture and answer-hash-bound skeptical reviews with:

```sh
python scripts/evaluate_assistant_conversation.py \
  --run evaluation/run.json --reviews evaluation/reviews.json \
  --report evaluation/report.json
```

Optional `--baseline` and `--baseline-reviews` report per-case regressions.
The CLI exits nonzero for failures, missing turns or missing reviews. Correctness
and usefulness require independent review; metadata and keyword matches cannot
certify prose quality. Controlled test outputs never establish live quality.
The provisional 180-second ceiling detects severe latency regressions; it is
not a target user experience or a measured p95. Qualified broad MTG expertise
continues to use the separate blinded expertise evaluation policy.

CI runs hermetic scorer/runtime tests and exports the input schemas. It does
not spend model tokens or claim that a release passed live expert review.
