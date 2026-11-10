# Quality policy implementation

Scope: offline policy, prompt, and diagnostic changes. No corpus refresh,
provider calls, stronger-model escalation, Jev expansion or semantic win claims.

## Coordinator integration

1. At admission call `build_obligations(request, revision, constraints=...)` and
   persist `AnswerObligations.model_dump()` with the run. It contains the full
   current request (up to the coordinator-enforced 16000-character cumulative steering bound) and hash, deck revision, version, and auditable items. Its
   only initial inferred requirement is none; the original request stays binding.
2. When the existing current-request planner returns a validated ResearchTask,
   call again with `task=task` and the same run/request/version. This is the one
   allowed refinement, not a new model call. A missing/ungrounded current quote
   does not create old-request obligations. Persist before synthesis and include
   under `answer_obligations` in synthesis input. The prompt treats this as a
   checklist and forbids inventing facts to satisfy it.
3. Obligation provenance distinguishes current request, validated planner
   interpretation, saved constraints and inferred explanation dimensions. Only
   exclusions/protected IDs are marked hard saved restrictions. Priorities and
   other preference hints are soft. Original complete constraints remain in the
   existing run input; compact descriptions are not their replacement.
4. For a successful **actual tool receipt**, call
   `early_findings(call.name, data, obligations, arguments=call.arguments.model_dump())`.
   Never call it on user/model-supplied text claiming to be a receipt. Findings
   are strict Pydantic objects; persist `.model_dump()` through the existing
   workspace path. `sources` contains captured source metadata. URLs may be null;
   the renderer must keep quote text literal, not HTML. Source freshness remains
   unknown. No result grants mutation authority.
5. Named exact-card facts publish only when complete text and recognized source
   provenance exist and the whole printed name occurs in the current request.
   This deliberately abstains on nicknames, ambiguous pronouns and discovery
   candidates not yet named. Rules publish only for an exact scoped query and
   source ID/version/hash. Functional/name search publishes only unranked names
   when a validated discovery query matches; this does not establish eligibility
   or strategic fit. Truncated rule excerpts retain a visible warning. Truncated
   card facts and oversized text are withheld rather than stripped of conditions.
6. At finalization, preserve existing factual-review receipts. Use
   `assess_fulfillment` when **explicit per-obligation coverage receipts** exist.
   Do not infer receipt coverage from prose keywords or the presence of citations.
   Legacy complete alone does not certify a finer checklist and returns unknown
   while retaining the legacy verdict; nonanswer maps to partial/missing answer.
   Coordinator may preserve the approved legacy fulfillment mapping separately
   rather than silently treating unassessed new obligations as satisfied.

## Evidence and evaluation

`diagnose_retrieval_stages(required_ids, hand_plan_ids, model_plan_ids,
synthesis_fulfillment=...)` separates frozen-label retrieval/corpus gaps,
planning gaps and synthesis/fulfillment gaps. It reads explicit structured IDs
only. A preferred-card label can itself be wrong; diagnosis is an investigation
pointer, not proof of architectural cause or strategic quality.

`fixtures/assistant-events-v1/quality-cases.json` is frozen by its SHA-256
manifest. Seven policy cases cover Marchesa, Kinnan, Tayam, Zaxara, insufficient
measurements, rules text and follow-up scope. Cases are AI-authored, have no
expert adjudication, and contain no paid run receipts or heldout labels. They
are not an independent answer-quality benchmark. The existing independent
benchmark and its heldout freeze gate remain intact.

Expert strategy enrichment still uses existing provenance/import review. This
change performs no acquisition and adds no AI-authored guidance as expert source.

## Validation

Targeted checks: tests/test_assistant_obligations.py and
 tests/test_assistant_publishability.py. They exercise unsupported planning
quotes, explicit count/condition obligations, saved constraint provenance,
per-obligation fulfillment, source/version/hash/query requirements, missing and
hostile URLs, truncated source rejection, unrelated early findings, unranked
candidate disclosure and frozen fixture integrity.

No measured model-quality, latency, cost or source-freshness improvement is
claimed by these deterministic tests.

### Existing planner hook

The single refinement point is the existing `if task is None and
step.research_task is not None:` block in `assistant_runtime.run_turn`, immediately
after `task, disagreements = reconcile_task(step.research_task, current_request)`.
Use that reconciled `task`, not the preliminary RequestPlan task before scope
recovery. Later `state["research_task"] = research_progress(...)` is a progress
view, not the validated task. Keep obligations separately in
`state["answer_obligations"]` and persist via the coordinator event callback.
The planner is not called again for this update.
