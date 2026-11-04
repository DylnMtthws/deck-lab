# Independent assistant quality benchmark v1

Integration hook: `run(actualresults, split="development")` from
`sabermetrics.assistant_quality_evaluation`. `actualresults(case)` receives a
JSON-compatible public case dictionary and returns a JSON-compatible receipt.
No candidate implementation is imported by the benchmark. No paid model calls.

Receipt (all fields optional, omissions reduce coverage/completion):

```json
{
  "case_id": "dev-01",
  "status": "completed",
  "candidates": ["exact card name"],
  "recommendations": [{"card": "exact card name", "rationale": "text"}],
  "claims": [{"id": "probe-1", "value": true}],
  "packages": [{"cards": ["A", "B"], "coherent": false}],
  "cuts": [{"card": "exact card name", "rationale": "text"}],
  "addressed_topics": ["cost", "setup", "tradeoff"],
  "answer": "optional captured actual QA prose",
  "telemetry": {"latency_ms": 10, "cost_usd": 0, "input_tokens": 0, "output_tokens": 0}
}
```

Names are exact and case-sensitive; duplicates do not increase recall. Probe IDs
refer to public `claim_questions`; true/false means the assertion is true/false,
null is abstention. Do not fill claims by matching answer substrings. A capture
adapter may emit only actual observed fields. Unstructured prose remains
unreviewed, never silently certified as factual. Structured probe accuracy is
not an exhaustive audit of all prose claims. `addressed_topics` is self-reported
coverage, not semantic verification. Rationales must be present for completion;
the deterministic scorer does not certify their meaning.

Use `load_cases(split)` without loading labels. Evaluate development immediately;
heldout scoring requires `final_code_freeze="<candidate content/commit hash>"` as
an explicit attestation that candidate code is frozen. Do not inspect
`heldout.labels.json` before that point. Local files are not an access-control
boundary. Label hashes and the manifest freeze benchmark content, not candidate
code or author independence cryptographically.

CLI: `python3 scripts/evaluate_assistant_quality.py --split development`
validates the freeze and lists public cases without reading labels. Add
`--receipts capture.json` (object keyed by case ID, or list of receipts) to score.
`--callback package.module:actualresults` captures actual responses in process;
use `--capture-output capture.json` to retain receipts. Callback exceptions become
failed receipts. No synthetic reference answer is ever reported as an actual run.
Heldout scoring also requires `--final-code-freeze HASH`.

Metrics remain separate: candidate recall, bounded graded recommendation
usefulness, structured factual error and abstention/coverage, package coherence
agreement, cut quality/protected-cut violations, completion, and latency/cost.
No combined score or release threshold is supplied. Unknown judgments stay
unknown and are excluded from graded denominators with coverage shown.
