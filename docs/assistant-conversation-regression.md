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
Evidence scope is independent of task kind: a comparison may still request new
candidates. Only an explicitly quoted named-cards-only scope enables the fast
named-facts synthesis path; broader scopes preserve candidate retrieval.
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
Bounded source identities also persist for local in-deck reads that have no tool
event, so those facts remain available on subsequent turns.

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

## Deck support and strategic regression splits

Functional search now builds one current-library/commander support index per
query. Before the shortlist limit, candidates receive sourced sacrifice-fodder,
counter, recursion, potential mana and commander-engine ingredient signals.
Sideboards, self-only counter sources and tokens for nontoken requirements do
not establish support. Exact casting costs and activation clauses remain
separate from hand-card accounting. Fixed draw-discard activations are filtering;
sacrificing a permanent is a separate resource cost. Unsupported, conditional,
variable or multiface accounting abstains. Ingredient links never prove a loop.
Compact named support survives tool-history eviction; the renderer adds a
support/awkwardness explanation, which still passes through the source reviewer.

Review refreshes discussed ledger identities locally, retains source provenance
and faces, and includes the document's actual validation fields. A missing,
truncated, budget-omitted or unresolved source leaves the review unverified.
Printed facts, computed deck facts, rules interactions and strategic judgments
remain distinct. The runtime records review-evidence, review and tool timings
separately from existing planner/synthesis observations.

`tests/fixtures/assistant_strategic_eval.json` freezes development and held-out
synthetic mechanical scenarios. The held-out split was consumed for the October
20 release; it is a regression suite after that point. Future tuning requires a
new untouched split. Run either split without model/network calls:

```sh
python scripts/evaluate_assistant_strategy.py --split development --report evaluation/strategy-dev.json
python scripts/evaluate_assistant_strategy.py --split heldout --report evaluation/strategy-heldout.json
```

Reports separate candidate recall, labeled pairwise recommendation ordering,
factual-profile errors, retrieval/ranking latency and model usage/cost. These are
controlled-corpus metrics; end-to-end answer latency and independent prose/rules
quality remain unmeasured unless a live capture/review supplies them.

The optional Jev experiment follows the official [System One API](https://docs.typesafe.ai/api/system-one),
[Score semantics](https://docs.typesafe.ai/primitives/score) and
[citation cookbook](https://docs.typesafe.ai/cookbooks/citation_check).
It batches fit/setup scores and evidence-bound claim Choices without sending
expected labels. Raw distributions, selected model, usage and timing are retained.
Scores are ordered level positions, not probabilities. No score certifies rules
or permits a deck edit. Unavailable credentials/providers/schema, omitted inputs
and unknown price are reported explicitly; absent cost estimates are never zero.

An operator can run the paid comparison with `--live-jev` and a server-side
`TYPESAFE_API_KEY`, or `--key-file ~/.config/jev/typesafe-api-key` after local
`~/.local/bin/jev-setup`. Never put a key in arguments or source. The application
shadow is disabled by default; explicit `SABER_ASSISTANT_JEV_SHADOW=1` enables one
bounded request per functional query using the server environment credential.
It cannot reorder candidates or replace the factual reviewer. Requests have an
8-second IO timeout, no retries, at most 64 questions and a 28 KB payload cap.
Optional `TYPESAFE_INPUT_USD_PER_MILLION` is a configured input-price estimate,
not an invoice. The experimental usage is reported in its receipt separately
from the existing model gateway ledger.

### Interim research updates

Long turns publish stage updates before provider calls: request/deck checking,
comparison, then evidence review. Functional search also publishes up to three
source-derived cost/support/friction summaries. These are heuristic samples,
not final rankings or verified rules. Updates remain visible outside collapsed
research activity and use the existing session polling, without additional model
calls. After 30 seconds in one stage, the status explicitly says there are no new
findings yet; it does not manufacture timed discoveries or imply a completion
percentage. Stop remains available and the final answer retains its review status.

### Selection before prose and focused claim review

Production discovery turns now request `CandidateSelection` instead of a full
answer. It contains retrieved IDs, bounded fit/conditions assessments and explicit
shortfalls. Code validates identity, novelty, deck membership, group membership,
counts and assessment coverage before rendering names and mana costs. One bounded
repair can return only another selection; it cannot rewrite an answer or run tools.
An honest shortfall is rendered as incomplete rather than padded with weak cards.
Evidence is bounded by whole records and omitted candidate IDs are disclosed.

The selected discovery answer receives one batched `ClaimReviewDecision` call.
Each recommendation is a claim unit retaining its costs, fit, support and conditions
together, so pronouns and caveats cannot be detached. Related evidence indices guide
attention; exact complete source records remain shared once. Every unit requires a
judgment and exact citations; absent judgments/citations or missing rules evidence
remain unverified. Supported strategy means a qualified heuristic assessment, not
expert certification or measured improvement. Corrections use the existing exact
span/quote, protected measurement, number and card-name checks. Review failure never
promotes a claim. Ordinary answers and coordinated/empirical workflows retain their
existing synthesis/review path. Tests can explicitly enable `selection_first` with
injected gateways; production enables it by default for eligible discovery turns.
The claim-review schema requires exactly the number of supplied claim units;
duplicate/unknown IDs and missing coverage still fail closed in application code.
Focused review has a separate 4096-token output allowance with no transport or
schema retries; the ordinary reviewer keeps its previous budget. Selection
assessments target short complete sentences. Explicit no-repeat instructions take
precedence over ranking language, and quota-satisfied shortfalls are not rendered.
The focused reviewer uses a bounded 75-second request deadline to accommodate its
complete claim batch. This prevents the old 45-second deadline from abandoning
larger reviews; it adds no retries and does not change the ordinary reviewer.

The focused review wire contract (`assistant-claim-review.v2`) uses one required
property per claim and compact typed judgments. The model selects evidence IDs
instead of copying source quotes; code resolves IDs to exact complete source
lines and applies the existing citation/span guards. Corrections name a short
exact claim span, so valid surrounding prose is preserved. Full Oracle/rules
records remain in the input catalog. Generic deck inventory is narrowed to named
rows with omissions disclosed; global inventory/ranking assertions remain
unverified. This reduces generated JSON and prevents long quote copying from
exhausting the output budget. Canonical receipts retain readable categories,
exact quotes, per-claim coverage and uncertainty.
The v3 contract separates a known factual correction from any unverified remainder
in the same recommendation. A supported Oracle correction can be applied while
unknown rules or fit assertions keep the unit unverified. An invalid evidence ID
quarantines its claim without discarding other independent valid corrections.

### Ability compatibility and bounded finalist evidence

The next increment uses `assistant-suitability.v2` and conversation prompt v31.
`assistant_compatibility.py` replaces broad ingredient-category support credit
with bounded matches between the candidate's requirement and the provider's
actual source clause. Supported shapes include creature/artifact token costs,
a small explicit tribe vocabulary, external matching counter sources, self-death
versus other-creature death, and unrestricted creature return to the battlefield.
Compound sacrifice restrictions, nontoken supply, mismatched counters/targets,
unresolved if/unless conditions, incomplete text and multiface cards remain unknown. A source
must be in the current library or command zone. Matching ingredients never prove
availability, sustainable supply, a loop or measured improvement.

Each record preserves casting and activation costs separately, printed initial
resource clauses, and the need to repay costs/replenish resources for further
uses. Matching more dependencies cannot accumulate extra support bonuses.
Existing commander relationships remain bounded contextual preferences; they
cannot supply missing prerequisite evidence. One fixed-mana comparison is attached
for up to eight shortlisted cards, retaining both full ability quotes and casting
costs. It does not claim equivalent effects or strict superiority. Selection sees
these comparisons and exact support-card records; deterministic rendering also
includes activation costs so a short model note cannot silently omit them.

After selection, the existing reviewer receives at most two additional local
rules/rulings lookups for the first three finalists. Each lookup has a 350 ms SQL
progress deadline and a short lock timeout. These are database-query bounds, not
a hard wall-clock guarantee for Python processing or the overall answer. Six
distinct rule/ruling hits are retained, including original source identities,
versions, hashes and excerpt flags in the stage receipt. Existing evidence is
reused, fresh complete text takes precedence over prior excerpts, and omitted
queries/finalists/hits are disclosed. There is no additional model round, remote
retrieval, or Jev dependency. Missing evidence remains unverified.

The frozen calibration artifacts live in
`docs/evaluations/assistant-compatibility-20261020/`. `cases.json` contains 24
supplied-text mechanical scenarios (16 development, eight initially reserved).
The development ordering checks improved from 7/8 to 8/8; the reserved ordering
checks remained 4/4. Expected candidate retrieval remained 48/48 across the two
splits; the 24 declared profile checks remained correct. These are authored
regressions, not independent expert labels; the reserved split is now consumed.

`deck-pilot.json` separately freezes 24 retrieval requests across 12 diagnostic
commander shells, six strategy families and a 367-card public snapshot subset.
These shells are not optimized player decks; quantities are normalized to one.
The capture script records literal results and source-tree/corpus hashes without
model calls. `deck-comparison.json` records rank movement and retrieval timings,
while expert preference, broad factual accuracy, full-catalog recall and
end-to-end answer latency stay null. Rank movement does not establish a quality
gain. The original baseline code was exported from commit
`824b174e7f681954ba2b6b266582a67998ca895d`; no baseline was fabricated from the
candidate implementation. Full captures are retained in the operator's private
`assistant-compatibility-20261020` artifact directory.

This increment has not been deployed to QA or production. Live paired answer
collection and independent expert calibration remain separate release evidence.

Validation: 3,464 full-suite tests passed, 34 optional tests skipped; 200 source
files passed type checking. The final evidence metadata also passed 32 targeted
checks. Existing sklearn convergence warnings remain unchanged.

### Grounded search contracts and verification follow-through

Conversation v32, selection v2 and focused review v5 address the two live QA
failures captured on `2437930`. A conservative literal request compiler derives
supported positive function/type constraints from the user's own request or a
grounded list quote, independently of a planner's generated labels. For example,
“mana-producing creatures” requires the mana function, creature type and a
printed own mana-production effect. Cost reduction and granted abilities do not
satisfy that last bounded check. Explicit low casting cost sets a preference;
it does not invent a numeric ceiling or confuse the number of requested cards
with a mana bound. Existing required numeric preferences are preserved.
Ambiguous multiple objectives, type alternatives and exceptions stay with the
existing broader preference/review machinery rather than being guessed.

Both planned list execution and the functional tool enforce these contracts.
Selection also filters older/restored and ordinary-search records with the same
role/type checks before showing them to the model, discloses filtered identities,
and rechecks selected IDs against the contract. A completely empty eligible pool
returns an explicit incomplete response without spending a selection model call.
Empty groups with a specific shortfall are valid structured selections; final
list completeness remains a separate check. One selection repair remains the
limit, with structural validation hints but no raw provider response in feedback.

For loop/return/counter claims, finalist evidence prioritizes the loaded rule
snapshot's counter/zone-change definitions. Numbered anchors must match their
expected defining phrase, or resolve uniquely by that phrase in the loaded
edition; numbering alone never establishes evidence. The phrases were checked
against the official [Comprehensive Rules linked by Wizards](https://magic.wizards.com/en/rules).
This changes retrieval attention, not rules execution or source freshness.
There are still at most two local lookups and one answer reviewer call. Bare card
and rule headers cannot be selected as proof IDs for a mechanical claim.

Exact-span/source/number/name/protected-part correction guards still apply.
Corrections are validated independently so a rejected edit cannot discard a valid
edit on another claim. Claim-level uncertainty and rejected corrections remain
explicit. After review, canonical discovery rows with missing or unresolved
judgments are narrowed to the actual card identity/cost, complete bounded Oracle
text when available, and an application-owned uncertainty statement. An unsupported
model assertion is not left visible beneath a generic warning. Narrowed notes
also replace the original disputed manifest notes, preventing their reuse as
recommendation rationale. Supported rows and protected measurements remain intact.
This fallback intentionally returns less advice when the evidence cannot support
it; it is not a substitute for expert-rated strategic quality.

Adversarial cases include the exact Kinnan request shape, planner function drift,
soft versus numeric limits, card counts, multi-role/disjunctive/negative requests,
context-only type mentions, restored unsuitable records, empty pools, competing
valid/invalid corrections, and opposing-creature deaths versus own sacrifice
outlets. The implementation remains bounded and does not claim a complete natural
language parser, comprehensive ability grammar or MTG rules engine.

Short Oracle facts such as keyword lines receive exact adjacent source context
when needed to meet the citation minimum. The evidence packet exposes the full
resolved citation text; headers alone remain ineligible, and the quote-length
and exact-source guards are unchanged. This avoids treating a supplied short
printed fact as uncitable.
