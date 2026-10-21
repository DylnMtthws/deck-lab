# Strategy review development contract v1

This is a separate development extension, with no heldout split. Cases and labels
were written and frozen before its scorer. Evaluation data and candidate code are
separate files, but the same AI implementation agent authored this extension.
Independent evaluation authorship and human/expert review remain UNFULFILLED.
Do not call this an independent benchmark or use it as release certification.

Official primary links verify bounded mechanical facts. Scenario premises are
explicit inputs; strategic preference is not labeled as an official ruling.
No copied primer text, expert endorsements or new licensed strategy material.

Receipts are keyed by case ID (or a list), with case_id, answer, status,
claims [{id, value: true/false/null, start, end}], preserved
[{id, start, end}], topics [{id, start, end}], and telemetry
{latency_ms, cost_usd, input_tokens, output_tokens}. Offsets are Python Unicode
character offsets into the exact final answer; end is exclusive. Every recorded
observation must point to nonblank answer text. These are adapter/reviewer
observations, never substring-inferred semantic grades. Reusing a span for
several observations is allowed; dishonest structured annotations cannot be
caught by this deterministic evaluator. A separate human audit is still needed.
Never manufacture structured receipts from labels or report test fixtures as
actual assistant answers. Prose outside annotated spans remains unaudited.

Report conditional claim truth, factual errors and coverage separately; a
missing or null probe is an abstention, never a correct answer. Usefulness loss
is the fraction of prior useful explanation IDs absent from preserved spans.
It is an annotation-based preservation proxy, not measured strategic quality.
Fulfillment requires completed status, all probes correct, all requested topics,
and all preservation IDs. Unknown IDs are unreviewed, not credited. Duplicate
IDs and malformed telemetry/spans fail validation. Missing resource telemetry
stays null. Report p50/p95 latency, cost and coverage with no combined score.
Compare latency only on paired case IDs, with quality regressions shown next to
speed differences. No model or network calls are made by the evaluator.
