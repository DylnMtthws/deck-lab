# T05 — Pinned deck status bar

Agent: LOW (DeepSeek V4 Flash). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T00 (`window.DeckLabBuilder`; contract in `T00-builder-extension-api.md`).

## Why
Moxfield keeps a bar pinned to the bottom with the card count, counts by type, legality and bracket.
You never scroll to find out whether the deck is legal or how many lands it has.

## Allowed files
- `src/sabermetrics/ui/static/deck-lab-status.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`:
  - Add `<footer class="dl-status-bar" data-status-bar aria-label="Deck status"></footer>` as the LAST child of `<div class="dl-builder" ...>`.
  - Add the script tag after `deck-lab-builder.js` in `{% block scripts %}`. This applies in shared mode too; the bar is read-only anyway.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_status_bar.py` (new), `tests/status_bar_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T05.md` (new)

## Requirements
`deck-lab-status.js` renders into `[data-status-bar]` on every `deck-lab:render` and once at
`deck-lab:ready`. It exposes `window.DeckLabStatus = { typeBucket(typeLine), summarize(state) }` for
tests.

1. `typeBucket(typeLine)` returns the FIRST of these whose word appears in the type line before any `—`/`-` (case-insensitive): `Land`, `Creature`, `Planeswalker`, `Battle`, `Instant`, `Sorcery`, `Artifact`, `Enchantment`. Otherwise it returns `Other`. Examples:
   - "Artifact Creature — Golem" → Creature
   - "Land Creature — Forest Dryad" → Land
   - "Legendary Enchantment Artifact" → Artifact
   - "Kindred Instant — Elf" → Instant
2. `summarize(state)` returns:
   ```js
   { total, target, legal, issueCount,
     types: [{name, count}],
     roles: [{role, count}] }
   ```
   - `total` = `state.validation.total_count` and `target` = 100.
   - `legal` = `state.validation.legal`.
   - `issueCount` = `state.validation.issues.length` plus the number of entries that have entry-level issues (`state.validation.entry_issues`; inspect its shape in `DeckDocumentRepo.validate`).
   - `types`: counted over non-commander entries in library zones only. Private zones (same list as `deck-lab-export.js`) are excluded. Counts are quantity-weighted. Only non-zero buckets, in the fixed order above.
   - `roles`: same entry set. Entries with no role count as `"none"`. Sorted by count descending, then role name.
3. Bar content, left to right:
   - Count: `N/100`, with class `is-invalid` when N ≠ 100.
   - A legality chip `[data-status-legality]`:
     - When legal: text `Legal` and class `is-legal`.
     - Otherwise: a `<button type="button">` with text `K issues` (`1 issue` when K = 1). Clicking it toggles a list `[data-status-issues]` of the deck-level issue messages, plus one item per entry issue as `<Card name>: <message>`.
     - Clicking an entry item calls `DeckLabBuilder.focusEntry(entryId)`.
   - The type counts, each as `<span data-status-type="Creature">Creature 32</span>`.
   - The top 5 roles, as `data-status-role` spans. Human labels: title-case the role key and replace `_` with a space; `"none"` becomes `No role`.
4. Layout: `position: sticky; bottom: 0`, one line, horizontally scrollable on overflow, using existing CSS variables for colors. Below 767px the bar is hidden, because the mobile bottom action bar occupies that space.
5. It must not change the existing `[data-deck-count]` in the toolbar.

## Acceptance criteria → required tests (`tests/test_status_bar.py`)
- AC-1 `test_type_bucket_precedence_table` (at least 10 cases, including the 4 examples above and `Other`)
- AC-2 `test_summarize_excludes_commanders_and_private_zones_and_weights_quantity`
- AC-3 `test_summarize_roles_sorted_with_none_bucket`
- AC-4 `test_bar_renders_count_types_roles_and_legal_chip` (harness: legal fixture)
- AC-5 `test_issue_button_lists_issues_and_focuses_entry` (harness: illegal fixture with one deck issue and one entry issue; clicking the entry item calls `focusEntry` with that id)
- AC-6 `test_bar_updates_on_render_event`
- AC-7 `test_builder_page_has_status_bar_container_and_script` (Flask test client: owner page and shared page)
