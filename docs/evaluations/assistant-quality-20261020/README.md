# Independent assistant quality benchmark, 2026-10-20

32 AI-authored cases, frozen before any candidate implementation evaluation.
These are constrained strategic judgments, not human/expert-certified labels.
No assistant selection, suitability, or claim-review implementation was read.
Only the permitted fixture's `catalog` fields were used; its expected outputs
and cases were not consulted. No model APIs, agents, installs, browser sessions,
or full test suites were used.

The integration contract is in `CONTRACT.md`. Public cases carry prompt,
synthetic deck shell, candidate pool, proposed package and explicit package goal,
atomic factual question, requested deliverables, and exact evidence snapshots.
This version measures prioritization/retention and cuts within a bounded pool,
not discovery from an unrestricted card catalog or complete Commander legality.
Every pool card is in the shell, so membership cannot identify the preferred
recommendation. Callbacks must prioritize/retain one and cut a different one.

Development contains 16 cases across four archetypes, four cases each:

- Sacrifice/recursion: controlled outlet versus payoff; counter setup; mandatory
  upkeep trigger versus optional activation; preserving outlet/counter roles.
- Spellslinger: copying versus casting; cantrip economy; interaction breadth;
  token payoff versus draw payoff.
- Mana activation: rock mana versus draw cash-in; cast and activation setup;
  consumed resources; incomplete repeatable-ramp/draw packages.
- Lands: mandatory/optional draw; mana versus cards; extra land-play permission
  versus putting a land onto battlefield; missing attacker/equip target.

Heldout has 16 cases across tokens, graveyard, control, and combat. Case IDs,
archetypes, and card evidence IDs are disjoint between splits. General rules
are shared. Both partitions derive card facts from the same parent public
catalog; this is not an independent-source-document or independent-author split.
Do not read `heldout.labels.json` until the candidate's final code freeze. The
runner enforces an attestation gate, not filesystem access control. This README
and the public heldout cases intentionally omit expected answers.

The final pre-candidate freeze is revision 3. Early authoring revisions clarified
package goals and balanced factual probes. Harness calibration subsequently
revealed a membership shortcut during input audit; revision 3 removed it before
any candidate run. No labels were tuned against candidate outputs. Manifest
hashes bind cases, labels, evidence, and contract. Code/tests and this narrative
are outside the label freeze. Preserve the manifest hash with every report.

Evidence and uncertainty:

- Card fields are exact copies of the permitted cached catalog, with source-file
  hashes, Oracle IDs where present, and explicit provenance. They are not newly
  verified against primary Oracle records. Missing primary verification remains
  unverified; do not report current Oracle certification.
- `rules.evidence.json` contains short exact excerpts fetched directly from the
  official Wizards September 25, 2026 Comprehensive Rules text and its complete
  source hash. These support cost/trigger distinctions but do not establish
  that every cached card record is current.
- Every strategic label has a rationale and an uncertainty statement. Scores
  express the stipulated shell goal, not universal rankings. Out-of-rubric
  alternatives are unknown and require independent review, without auto credit.
- Package coherence is relative to each explicit package goal. A coherent payoff
  pairing does not assert infinite, free, or autonomous operation. Package goals
  requiring autonomy state that requirement explicitly.

Metrics are never combined: candidate recall; recommendation usefulness;
structured factual errors with answered/expected counts; package assessment
accuracy and coverage; cut quality and protected-cut violations; structural
completion; observed latency, tokens and cost. Per-case and per-archetype views
retain the errors. Missing telemetry is not zero; unknown semantic alternatives
are surfaced. Prose answers remain available for review but are not scored by
keyword overlap. Topic coverage is self-reported and does not certify meaning.
Callback wall time measures the local invocation (or file lookup in captured
mode); `telemetry.latency_ms` must represent original actual request latency.

This benchmark provides bounded signals for strategic selection, precise factual
review, independent evaluation, mechanics, resource reporting, and archetype
cohorts. It does not establish live retrieval-correction behavior, memory
retention/observability, vector quality, corpus review quality, tournament win
rate, or broad deployment readiness. Those need separate evidence and tests.

Run without invoking a candidate:

```sh
python3 scripts/evaluate_assistant_quality.py --split development
```

Score captured observations (no paid model calls):

```sh
python3 scripts/evaluate_assistant_quality.py --receipts actual.json --output report.json
```

Wire an existing candidate adapter:

```python
from sabermetrics.assistant_quality_evaluation import run
report = run(actualresults, split="development")
# Only after final candidate code freeze:
# report = run(actualresults, split="heldout", final_code_freeze=candidate_hash)
```

Harness tests use explicitly labeled calibration receipts, not actual QA answers.
No candidate result or measured assistant improvement is claimed by this work.
