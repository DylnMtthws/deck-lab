# D01 — Amendment 1 (from the orchestrator, after visual review of 6796e90)

Accepted, and good work: the header (Saved chip, icon undo/redo, Export dropdown), the selection bar, the View popover, the export menu with icons and descriptions, the deck-options menu, and the documented test changes.

Two layout fixes, both seen in screenshots at 1440 and 1280 px:

1. **The search icon overlaps the placeholder.** The magnifier sits on top of the "A" of "Add or find a card". Give the input left padding equal to icon inset + icon width + `--s-2`, or lay the icon and input out as flex siblings. It must not overlap at any width.
2. **The View button floats in the middle of the toolbar.** Per DESIGN-SPEC §2.2 the order is: search field, then the View button immediately after it (gap `--s-2`), then a flexible spacer, then the hint, then the deck-options button. Nothing else may sit between the search and View. (The "+N considering" badge is moving to the status bar in D04, so leave it where it is; D04 removes it from the toolbar.)

## Additional acceptance criteria (add to `tests/test_design_toolbar.py`, Node harness or computed layout)
- AC-11 `test_search_icon_does_not_overlap_input_text`: the icon's right edge is ≤ the input's text-start x (bounding rect + padding-left).
- AC-12 `test_view_button_immediately_follows_search_field`: in DOM order, View is the next control after the search combobox, and its left edge sits within 16 px of the field's right edge.

Visual DoD: `list`, `list-selection`, `view-options` and `export-menu` clean (no `bar:`/`covered:`) at 1440 and 1280.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
