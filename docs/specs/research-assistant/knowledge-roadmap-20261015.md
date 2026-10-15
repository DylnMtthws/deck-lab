# Research Assistant: intelligence without model training

Continue the QA Build assistant in `feat/goldfish-lab-20261010`. The older
`research-assistant` branch is a historical headless prototype, not the deployed
sidebar. The QA release at the start of this work was `28bf601`. This document
extends the October 14 knowledge/execution design; it does not certify expertise.

## The architecture

The base model translates a goal, chooses bounded tools, compares returned facts,
and explains the result. External memory supplies MTG knowledge. Deterministic
code owns identities, constraints, statistics, provenance and experiment validity.
Model weights do not change. Embedding a corpus is indexing, not fine-tuning.

The useful unit of reasoning is a deck-specific evidence packet, not a pile of
similar paragraphs. Each recommendation should connect the user's objective,
commander triggers, incoming and outgoing Oracle facts, resources and setup,
strategy evidence, field associations and any comparable experiment. If one
link is missing, identify the missing link rather than smooth it over in prose.

| Layer | Already present on QA | Next useful work |
| --- | --- | --- |
| Canonical facts | Catalog IDs, versioned faces/rulings imports | Expand beyond two enriched snapshots; expose snapshot coverage and source dates |
| Hybrid retrieval | BM25, pinned local ONNX vectors, version checks, bounded source excerpts | Larger permissioned corpus, query decomposition, separate rule/strategy retrieval |
| Mechanical relationships | Conditional capability predicates, cast-trigger evidence, authored interaction graphs | Expand tested relations and prerequisite vocabulary; preserve unknown effects |
| Sourced combos | Bounded Commander Spellbook catalog and near-completions | Refresh lineage, expand coverage, separate source claims from executable mechanics |
| Behavioral evidence | Commander-conditioned list inclusion | Card associations with baseline adjustment, adequate support and explicit denominators |
| Verification | Oracle facts, constrained previews, partial replay, native goldfish | Source-support checks and independent rules comparisons where execution is available |
| Episodic memory | Owner/deck preferences and experiment receipts | Freshness-aware reuse of successful query plans; retain failed hypotheses with their scope |
| Measurement | Labeled regressions and bounded held-out experiment checks | Independent unseen retrieval and recommendation benchmark, stratified by commander/archetype |

## This increment: association discovery

`research_card_associations(oracle_id, window_days, limit)` is a read-only typed
assistant tool. Its anchor must occur in the owner's current library. Existing
owner/revision/cancellation checks run before dispatch. Only public recorded
lists enter the statistics; private saved deck documents are never the corpus.

Reuse the exact existing commander/partner/date/event-floor cohort. Count each
recorded deck once, each Oracle card once per deck, excluding commander slots,
zero-quantity cards and basic lands. Quantities and alternative printings cannot
inflate support. Partner queries require both commanders. Counts do not imply
independent pilots or events; this remaining dependence is disclosed.

For cohort size N, anchor lists A, candidate lists B, and joint lists J:

* Baseline inclusion is B/N; observed conditional inclusion is J/A.
* Adjusted conditional inclusion is (J + 5 × B/N)/(A + 5).
* Adjusted lift is adjusted conditional inclusion divided by B/N.

The five pseudo-lists shrink toward ordinary cohort inclusion. This is a
transparent discovery heuristic, not calibrated certainty or a statistical
significance test. The support floors (N ≥ 10, A ≥ 5, J ≥ 5) are initial safeguards,
not evidence of reliability. Return only positive adjusted associations. Rank by
adjusted lift, joint count, then Oracle ID. An ubiquitous staple has lift one;
a rare two-list coincidence fails the support floor. Include counts, both
conditional/baseline denominators, window endpoints, applied floor and omissions.

At most 2,000 cohort lists and eight returned associations. Oversized cohorts
abstain rather than silently sampling; ask for a narrower window. A SQLite
progress deadline bounds expensive queries. One read transaction keeps all
counts consistent. The aggregate digest identifies returned statistical inputs,
not the full corpus or freshness. Refresh age remains unknown unless independently
established. This tool neither injects candidates into previews nor authorizes
experiments; inspect facts and validate a proposed swap through existing tools.

## Next increments, in order

1. **Measure and expand the knowledge corpus.** Inventory actual documents,
   chunks, enriched Oracle cards, embeddings, model version and refresh dates.
   Prioritize current official rules and rulings, then permissioned primers and
   attributed strategy notes. Wiki articles are secondary explanations: preserve
   page/revision/date/URL and resolve entities to Oracle IDs or CR sections.
   Wiki authority never overrides current official Oracle/rules. Avoid live
   scraping during a chat; blocked sources remain missing. Do not treat copied
   authored notes as independent corroboration. Enforce source permissions and
   bounded excerpts with the existing offline importer.
2. **Compile retrieval plans.** Decompose a request into mechanism, setup,
   interaction, deck weakness and candidate-comparison subqueries. Retrieve
   rules, strategy and card evidence separately; fuse ranks, deduplicate source
   versions and limit per-document dominance. Embed card functional text and
   prerequisite descriptions as separate views so similar names or theme words
   cannot dominate mechanical similarity. Apply actual color/legality constraints
   in code before actionable proposals. Query expansion widens discovery only;
   it must not relax the original constraints. Cache on source/model/prompt
   versions, deck content, objective and preferences, not conversation text alone.
3. **Join an evidence graph.** Nodes: Oracle cards, abilities, resources, zones,
   triggers, outcomes, CR sections, source passages and source combo variants.
   Edges: produces, consumes, triggers-on, protects, tutors-for, substitutes-for,
   source-claims-combo, and statistically-co-included. Attach conditions,
   snapshot identity and evidence class to every edge. Bound traversal to one or
   two hops around current deck ingredients. Statistical edges nominate leads;
   mechanical edges explain a possible interaction; rules and execution determine
   what is actually established. Never relabel co-occurrence as a verified combo.
4. **Assemble and verify recommendation packets.** For each pair, retain the
   original contribution, proposed gain, lost roles/engines, enabling resources,
   timing and counterexamples. Batch independent relevance and source-support
   judgments over compact evidence using Jev when configured. A judge selects
   evidence or marks a claim supported/contradicted/unknown; it does not decide
   legality or authorize edits. Disagreement triggers a targeted fact lookup.
   Jev's local credential was absent during this increment; no runtime dependency
   or paid call was introduced. The existing conversational review remains.
5. **Learn through external memory.** Save accepted query plans and comparison
   packets with outcomes and snapshot versions, separately from user preferences.
   Retrieve a few relevant examples; re-execute against current facts. Adoption
   is a user preference signal, not proof of strength. Retire disproven hypotheses;
   do not reinforce the assistant's own unsupported prose as factual knowledge.

## Acceptance and evaluation

Keep retrieval, inference and execution scorecards separate. Passing the existing
small visible suite is regression protection, not broad MTG expertise.

* Retrieval: independently authored questions, required evidence recall@k,
  source correctness, answer leakage/name-lookup fraction, paraphrase and rare
  mechanic subsets. Split by source and commander to limit memorized templates.
* Association: exact synthetic count fixtures, duplicate-printing/list traps,
  partner/window/floor isolation, ubiquitous staples and rare-pair negatives.
  Evaluate temporal stability and held-out list retrieval before using lift as
  a ranking input to candidate generation. Do not optimize win claims on lists.
* Reasoning: recommendations preserve important engine functions, account for
  cards/mana and prerequisites, retrieve contradictory evidence, and abstain
  where mechanics are unknown. Score useful alternatives as well as errors.
* Grounding: every factual claim traces to an actual returned fact or passage;
  percentages keep denominators/windows. Semantic confidence is not proof.
* Experiments: objective-specific comparisons require comparable modeled
  coverage and held-out validation. Suppress unsupported scores. Multiplayer
  win rate cannot be inferred from an unopposed goldfish.
* Operations: report latency/cost per turn, context bytes and omitted evidence,
  snapshot coverage and refresh failures. Keep one local embedding model with
  bounded threads; no new graph database or service is necessary at this scale.

Release this as a QA candidate with focused regressions, the executable labeled
suite, image smokes and an authenticated tool check. Production promotion stays
subject to the existing owner review process.
