# D06 — Amendment 1 (from the orchestrator, after visual review of 6234949)

Accepted: zone header chrome, card lift, drag states, stack depth and "+N", hover/focus peek, FLIP toggle. Good work. Two additions:

## 1. New zones land in free space, not on top of other zones
Today a zone created without `x`/`y` cascades to `(120 + 40n, 160 + 30n)` (`DeckDocumentRepo._apply_command`, `create_zone`), so new piles overlap existing ones. A real mat puts a new pile in open space.

**Server.** When `create_zone` has no `x`/`y`, place the zone at the first free position:
- scan a grid in 24 px steps, left→right then top→bottom, starting at `(18, 18)`, inside the canvas (`deck_presentations.canvas_width`/`canvas_height`, defaulting to 1600×900);
- a position is free when the new zone's rect does not intersect any existing zone rect (use stored `width`/`height`, or 360×240 when null) or the command zone at `(18, 18, 170×240)`, with a 16 px gutter;
- if nothing fits, fall back to the old cascade.

Explicit `x`/`y` are still honoured exactly.

**Client.** `deck-lab-considering.js` currently sends a hard-coded `x: 200, y: 200`. Remove those two fields so it gets free placement. This is the only change allowed in that file.

## 2. Playmat text on the type scale
The zoom label (`#zoom-label`, 10 px mono) and any other playmat text below 11 px must use the scale: zoom label 12 px tabular mono; count chips 12 px.

## Additional allowed files
- `src/sabermetrics/deck_documents.py`: ONLY the `create_zone` branch, plus one private helper for placement.
- `src/sabermetrics/ui/static/deck-lab-considering.js`: ONLY removing `x`/`y` from its `create_zone` command.

## Additional acceptance criteria (add to `tests/test_design_playmat.py`)
- AC-9 `test_create_zone_without_position_lands_in_free_space`: create 3 zones without x/y; no two zone rects (incl. Unsorted and the command zone) intersect.
- AC-10 `test_create_zone_with_explicit_position_is_honoured`
- AC-11 `test_considering_zone_created_without_fixed_coordinates` (harness: the create_zone command has no x/y)
- AC-12 `test_playmat_text_sizes_within_scale` (computed sizes of the zoom label and zone count chips are ≥ 11 px)

Visual DoD unchanged: `playmat` and `playmat-hover-stack` clean at 1440 and 1280, with no playmat text below 11 px.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
