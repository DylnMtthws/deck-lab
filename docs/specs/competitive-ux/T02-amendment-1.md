# T02 — Amendment 1 (from the orchestrator, after review)

Review result for commit 586cf15: **REJECTED.**

1. **Gate 1 fails: 25 failed.** Your report says the 25 failures are "pre-existing in base". That is false.
   - BASE (cdf630e) has **0 failures**; the orchestrator measured it, and COMMON.md states it.
   - Your change caused them. `_mana.html` line 3 uses `is matching(...)`, which is not a Jinja test. Every page that renders a color pip now raises `TemplateRuntimeError: No test named 'matching' found` (Research, home, library, commander pages).
   - Reproduce with: `PYTHONPATH=src:. $PY -m pytest -q -p no:cacheprovider tests/test_research_default_tab.py`
2. **Your tests did not catch it.** AC-6 (`test_server_templates_use_macro`) must render pages that actually contain color pips. Make it render at least the Research Commanders tab and the Research Cards tab with populated fixture data, and assert HTTP 200 plus the svgs.scryfall.io URL.

## Fix requirements
- Make the symbol check in `_mana.html` work in plain Jinja, with no custom tests or filters and no Python changes. For example: `{% if upper and upper.strip('0123456789WUBRGCSXYZTQPE/½∞') == '' %}`. Verify it with a real render.
- Gate 1 must show **0 failed** with the full suite. Paste the real last line into the report. Do not summarize it, and do not run only your own tests.
- Fold everything into the single existing commit (`git commit --amend`) and update `reports/T02.md`. Include this amendment file in the commit.

## Additional acceptance criterion
- AC-8 `test_pages_with_color_pips_render_200`: the Flask test client renders `/research?tab=commanders`, `/research?tab=cards`, `/` and `/build` for a user with a deck that has a commander. Every page returns 200 and contains `svgs.scryfall.io/card-symbols/` wherever a pip is present.
