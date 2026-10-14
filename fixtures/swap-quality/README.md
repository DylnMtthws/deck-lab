This challenge uses four supplied reference documents (two commanders, affordable and high-power lists), the supplied public catalog, and explicitly authored missing-card text snapshots. Cases and expected outcomes were written independently of the functional matcher. This is an authored rubric, not expert-scored validation or measured game strength.

Run from the repository root:

```sh
PYTHONPATH=src python scripts/evaluate_swap_quality.py --adapter functional_candidates --output /tmp/swap-quality-report.json
```

The adapter calls the actual `functional_improvement` function. An unmatched pair is flagged because improvement is unestablished; it does not invent a strategic rejection. An accepted pair is a quality candidate with narrow functional evidence, never a demonstrated overall upgrade. The two positive cases (Manalith → Arcane Signet and Cancel → Counterspell) are counterfactual pair challenges; their outgoing card need not be in the supplied baseline. The mana case is contextual to commander-color use: Arcane Signet cannot supply colors outside commander identity. Darksteel Ingot → Arcane Signet remains a separate adverse tradeoff case because it loses indestructible. Preference and budget enforcement need separate generator/service tests.

KPIs require zero demonstrated-upgrade labels, explicit rejection/flagging/tradeoff or uncertainty for adverse and ambiguous cases, evidence on accepted positive cases, and no mutation of source documents. Blanket rejection and blanket acceptance fail. The corpus deliberately includes true tradeoffs so success does not require treating every unusual swap as bad.
