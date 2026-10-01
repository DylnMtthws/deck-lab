# D07 — Amendment 1 (from the orchestrator, after visual review of 24bd972)

Accepted: the shortcuts table with grouped key caps, the comment dialog, Research query preservation, applied/unsupported chips, and the documented test changes.

One fix that DESIGN-SPEC §2.9 called out explicitly: **the close button's tooltip ("Close keyboard…") still overlaps the dialog's top border.** It renders above the button and is clipped by, and collides with, the dialog edge.

- For dialog close buttons inside `.dl-dialog` heads (the shortcuts and comment dialogs), position the tooltip BELOW the button (scoped to `.dl-dialog .dl-dialog-head [data-dl-tip]`), so it stays inside the dialog.
- Alternatively, remove `data-dl-tip` from those close buttons, keeping their `aria-label`.

Do not change the global tooltip primitive.

## Additional acceptance criterion
- AC-7 `test_dialog_close_tooltip_does_not_overlap_dialog_edge`: either no `data-dl-tip` on the dialog close buttons, OR the CSS rule places those tooltips below (assert the scoped selector exists in `deck-lab-dialogs.css`).

Visual DoD: `hotkeys` screenshot shows no tooltip crossing the dialog border.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
