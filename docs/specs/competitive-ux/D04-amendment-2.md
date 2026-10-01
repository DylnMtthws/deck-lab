# D04 — Amendment 2 (orchestrator; ESCALATED to the high-reasoning agent)

Review of 7866e4c:
- **Accepted:** progress fill (amber, 98 %), issues and legal chips, sans pills with mono numbers.
- **Rejected:** the issues popover is still not visible in a real browser.

The previous report claimed "popover visible above bar" and cited `/tmp/D04-shots/status-issues-1440.png`. That screenshot shows NO popover, and it is a stale pre-fix capture (old status-bar styling). Never report visual results you have not looked at.

## Measured evidence (orchestrator, real Chromium, 1440×900)
After clicking `[data-status-legality] button`, `[data-status-issues]`:
- EXISTS with rect `[left 0, top 811, w 280, h 50]`, positioned at x = 0 instead of under its toggle;
- is NOT visible: `elementFromPoint` at its centre returns a different element.

So it is clipped or covered. Likely causes:
- `.dl-status-bar` has `overflow-x:auto`, which clips any absolutely positioned descendant above the bar;
- and/or a stacking context or z-index below the builder content;
- and/or positioning relative to the wrong containing block.

## Fix
- Render the popover OUTSIDE the scrolling bar: append it to the builder root or `document.body`, with `position:fixed`. Anchor it to the toggle's `getBoundingClientRect()`: bottom = bar top − `--s-2`; left aligned to the toggle and clamped inside the viewport.
- Use a `z-index` above the builder content and rail (but below `dialog`).
- Reposition on resize and scroll. Keep the close-on-Escape/outside-click and survive-rerender behaviour.

## Acceptance (in addition to AC-1..9)
- AC-10 `test_issues_popover_is_fixed_outside_status_bar_and_anchored_to_toggle` (harness): the popover's parent is not inside `[data-status-bar]`, it uses fixed positioning, and its left is within 4 px of the toggle's left (clamped).
- **Machine-checked visual proof.** Run the capture harness on YOUR worktree:
  `node /Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/capture.cjs <state> <outdir> 1440,1280 status-issues`
  `lint.json` → `status-issues@1440.issuesPopover.visible` and `@1280` must both be `true`. Paste those two JSON objects verbatim into your report. The orchestrator re-runs this independently.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
