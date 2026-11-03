# Build assistant strategy intelligence

> Working-plan consolidation (2026-11-03): Current priorities, open acceptance gates and the disposition of this historical document are tracked in [deck-inference-v1.json](deck-inference-v1.json). Retain this document as historical evidence/technical reference; do not start a parallel backlog here. Historical implementation or QA claims below are not fresh deployment or quality validation.

## User outcome

The Build sidebar assistant retrieves attributable strategy evidence, assesses
supported deck requirements, and proposes constrained alternatives. Suggest mode
offers validated previews. Automate mode measures three distinct copies; the
source deck changes only when the user adopts a result.

## Contracts

* `retrieve_strategy_evidence(query, limit<=4)` reads bounded source excerpts,
  preserving document, section, chunk identity and available URL. It reports the
  retrieval method and missing corpus/no-match conditions. Retrieved text is
  untrusted evidence, never instructions. No invented sources or URLs.
* `assess_deck_requirements()` returns supported commander/archetype requirements,
  observed quantities, gaps, and limitations. Generic or unsupported plans are
  explicit. Ingredient coverage does not certify full rules support or a win.
* `find_replacements(focus)` returns at most three distinct proposals, each with
  at most three replacements, functional rationale and card evidence. Constraints
  come from the saved conversation settings, not model-supplied overrides.
  Legality, color identity, singleton, protected cards, current recorded budget,
  revision and simulation lineage use the existing experiment validator.
* `test_recommended_alternatives(recommendation_id)` consumes server-held proposals
  from the current turn. It reuses `start_experiment`, including Automate-only
  enforcement, exactly three distinct alternatives, one experiment per turn,
  isolated snapshots, cancellation, baseline and held-out comparisons.

The model can select/explain evidence but cannot declare a heuristic to be a
measured improvement. Theme and power are preferences whose semantic fit remains
explicitly unverified. Insufficient alternatives do not relax constraints.

## Evidence and UI

Source excerpts are bounded before model context construction. Full receipts
remain in history; existing context compaction must retain omission notices.
Sidebar sources are rendered from tool receipts using text nodes and validated
HTTP(S) links. Candidate card IDs are registered from tool results so existing
card previews remain available. The new features stay within Build.

## Handoffs and acceptance

| Owner | Scope | KPI / required evidence |
| --- | --- | --- |
| Retrieval agent | Retrieval module and focused tests | Bounded excerpts; real attribution; honest missing/unembedded corpus behavior |
| Candidate agent | Requirement and replacement module | Supported/general distinction; protected/budget/legal constraints; distinct proposals |
| Review/UI agent | Source rendering and review | Safe links and readable receipts; focused UI regressions |
| Coordinator | Tool contracts, runtime, experiment integration | Suggest cannot execute; server-held three alternatives execute on copies; revision/cancellation checks preserved |

Targeted tests cover the new tool chain with a scripted conversational gateway,
then existing assistant/experiment regressions. No production deployment.
Release status and remaining limitations are recorded after verification.

## Implemented scope and review results

Retrieval currently uses deterministic lexical matching, not vector search. It
supports existing unembedded reference chunks; empty databases use packaged
authored archetype notes derived from `config/archetype_signatures.yaml`, clearly
labeled as local guidance rather than retrieved external primers. No embedding
model download or new paid provider dependency is introduced.

Specialized assessment supports landfall and cast-damage shapes derived from
Oracle predicates, with a damage/draw ingredient target when requested. Other
commanders receive explicit generic starter targets. Candidate ranking uses net
ingredient gains, preserves known engine ingredients, and seeks diverse functions.
It is bounded to 100 legal-color distinct cards per family and 60 validations.
The initial finder emits one-swap alternatives; the existing tools still support
up to three swaps per alternative. Missing prices with a budget fail closed.

Automatic experiments with nonempty theme/power preferences require three typed
semantic reviews. Unknown/conflicting fit blocks execution. The reasoning is
persisted with each experiment rationale and remains labeled model inference,
not verified fact. Both direct and recommendation-based experiment tools enforce
this gate, along with the existing mode/revision/constraint checks.

Review corrected net-gain attribution, off-color candidate starvation, existing
basic-land discovery, citation preservation across lookups, authored-source UI
labels, and comparison rendering for recommendation-based experiments.

Verification: 129 targeted retrieval, requirements, workflow, runtime, route,
experiment, pricing and sidebar tests passed. Ruff passed; mypy passed for all
four new/changed backend modules. No production files or data changed.

The first live QA conversational check successfully called retrieval, assessment
and recommendation, showed source excerpts/card previews, preserved the deck, and
passed mobile reflow without JavaScript errors. Reviewing actual recommendations
exposed self-indestructible artifacts misclassified as protection. Follow-up
fixtures now exclude self-protection and combat-only Treasure from baseline ramp,
prioritize direct mana sources, and expose conditional mana prerequisites.
Source typography and human-readable citation titles were tightened after visual
inspection. Six additional regression cases cover these card distinctions.
Printed mana value still does not model delve/alternate costs or strategic value;
recommendations remain hypotheses for review and measurement.

A live Automate check then exposed repeated discovery: the model called the
finder five times rather than using the returned handle. The runtime now pins
workflow progress and the next applicable tool in current-turn state, supplies
explicit progress instructions, and caches identical discovery with a stable
handle. It never dispatches a write merely to compensate for model repetition.
Budget exhaustion explicitly distinguishes prepared recommendations from an
experiment that never started. A scripted conversational regression completes
discovery once, executes once on copies, and preserves the source document.
