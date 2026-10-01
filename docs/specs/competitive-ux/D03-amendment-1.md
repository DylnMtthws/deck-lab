# D03 — Amendment 1 (from the orchestrator, after visual review of 7f7170f)

Accepted, and good work: tabs, the persisted tab, section routing, the Card tab (image, title and cost, rules box, meta row with role select and feedback icons, Why/Replace), the Deck tab curve with counts and average, zones, the Tools odds card and sample-hand thumbnails, and the documented test changes.

Three visual fixes, seen in the 1440 px screenshots:

1. **Your verdict is broken into one cramped row.** The segmented control, the note textarea (squeezed to about 40 px wide, with text wrapping a letter or two per line) and the privacy line sit side by side. It must stack vertically, full width:
   - `.dl-segments` Good/Mixed/Bad;
   - then a full-width textarea, 3 rows, 13 px, styled like `.dl-field`, gap `--s-2`;
   - then the privacy line at 12 px `--faint`.

   The Tools tab must be readable at 1280 px too.
2. **Tournament evidence window select is oversized.** It is a 16 px full-width select. Make it a compact inline `.dl-select` at 13 px, e.g. "Window [Last 30 days ▾]" on one row with the "Based on N recorded lists" line under it in 12 px `--muted`.
3. **Tags "Manage" is misaligned** (indented). Left-align it as a ghost small button directly under the chips, or on the eyebrow row's right side.

## Additional acceptance criteria (add to `tests/test_design_rail.py`)
- AC-10 `test_verdict_section_stacks_vertically_full_width`: in the harness or by computed layout, the textarea width is ≥ 90 % of the section width, and segmented, textarea and help text appear in that vertical order.
- AC-11 `test_evidence_window_select_is_compact`: computed font-size is 13 px, and it is not full width.

Visual DoD: `rail-deck` and `rail-tools` clean at 1440 and 1280, and the orchestrator re-reviews the screenshots.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
