# D04 — Amendment 1 (from the orchestrator, after visual review of e7c7c98)

Accepted:
- the considering and verdict chips on the right;
- the zoom offset (lint `underStatus` is 0);
- moving the old T05 status rules out of `deck-lab.css` into your stylesheet. This was outside your allowed files, but it's sensible consolidation of your own area: WAIVED.

Four fixes, seen in real-browser screenshots (1440 and 1280 px):

1. **The progress fill is invisible.** `.dl-status-progress-fill` is created as an inline element, so its inline `width` and its `height:100%` don't apply, and the 80 px track renders empty (it should be about 98 % amber at 98/100). Make the fill `display:block`, or a block element, with its width set to the percentage.
2. **"1 issue" is an underlined link-style button.** Per DESIGN-SPEC §2.7 it is a `.dl-chip.is-warn` button with a `.dl-dot`, reading "1 issue"/"N issues". "Legal" is a `.dl-chip.is-ok` with a dot. No underline.
3. **The issues popover does not appear in a real browser.** In the capture harness, clicking the issues button left no popover visible. A likely cause is that the status bar re-renders (on `deck-lab:render`) and destroys the popover, or that the outside-click handler closes it on the same click. The popover must stay open until Escape, an outside click, or a second click on the toggle, and it must survive re-renders while open. Verify with the REAL capture: the `status-issues` scenario screenshot must show the popover above the bar. (The harness clicks `[data-status-legality] button`.)
4. **Typography.** Pills are all monospace. Per §2.7, the label is sans (`--sans`, `--muted`), the number is mono, tabular, `--body`, weight 500, and the count reads mono "98" plus `--faint` "/100".

## Additional acceptance criteria (add to `tests/test_design_status_bar.py`)
- AC-6 `test_progress_fill_is_block_with_percentage_width`: computed `display` is not inline, and the inline width is 98 % for 98/100.
- AC-7 `test_issue_toggle_is_warn_chip_and_legal_is_ok_chip`
- AC-8 `test_issues_popover_survives_status_rerender_while_open` (harness: open, dispatch `deck-lab:render`, popover still present)
- AC-9 `test_pill_label_sans_and_number_mono`

Visual DoD: `status-issues` (popover visible) and `playmat` clean at 1440 and 1280. Attach the screenshot paths in your report.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
