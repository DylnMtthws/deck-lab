# T04 — Amendment 1 (from the orchestrator, after review)

Review result for commit at head of cux/T04: **ONE BUG: the module never loads in a real browser.** Everything else is accepted: the command shapes match `_apply_command`, the selection wiring works, and late-load init works.

**Bug.** `builder.html` adds the script as a Jinja string expression:
`{{ '<script src="' + url_for(...) + '" defer></script>' if not shared }}`.
- Flask autoescapes `{{ }}`, so the page contains the text `&lt;script src=&#34;/static/deck-lab-considering.js&#34;...&gt;`, not a `<script>` element. The orchestrator rendered it to confirm.
- AC-7 passed only because it searched for the filename as a substring.

## Fix
Use a literal tag inside a block conditional, placed after `deck-lab-builder.js`'s tag inside `{% block scripts %}`:

```
{% if not shared %}<script src="{{ url_for('static', filename='deck-lab-considering.js') }}" defer></script>{% endif %}
```

## Strengthen AC-7 (replace your current assertions)
- AC-7 `test_builder_page_includes_script_after_builder` must match a REAL script element with a regex such as `r'<script src="[^"]*/deck-lab-considering\.js" defer></script>'`. It must assert that it appears after the `deck-lab-builder.js` script element, and that `&lt;script` does not appear anywhere in the page. On the shared page the element is absent.

## Completion
COMMON.md definition of done. The base has ZERO failing tests; run the FULL suite. `git commit --amend` into the single commit, including this file, and update the report.
