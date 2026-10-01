# D06 — Amendment 2 (from the orchestrator, after visual review of 6f96786)

Free placement improved: new zones no longer overlap each other. But **"Considering" still overlaps "Unsorted"**. The server places zones using STORED width/height, which is null for auto-sized zones, so it assumes 360×240. In the browser, Unsorted renders about 720 px wide from its content (`dynamicWidth`/`zoneWidth` in `renderPlaymat`). The server cannot know rendered sizes, so placement must split:

1. **Client (UI paths): place using RENDERED rects.**
   - Add `DeckLabBuilder.freeZonePosition(width, height)` to the T00 API object in `deck-lab-builder.js` (the one API addition allowed).
   - It uses the actual rendered `.dl-mat-zone` and command-zone rects, converted to mat coordinates (undo pan and zoom), with a 16 px gutter and a 24 px scan in canvas bounds.
   - It returns `{x, y}`, or `null` if the playmat is not rendered.
   - The New zone dialog's create path and `deck-lab-considering.js` call it, and send explicit `x`/`y` when it returns a position; otherwise they omit them.
   - Use 360×240 as the new zone's size.
2. **Server fallback (no x/y supplied): place BELOW everything.**
   - Replace the size-guessing scan with: `x = 18`, `y = max(zone.y + (zone.height or 240)) + 24` over existing zones (and the command zone's bottom at 258).
   - This can never overlap horizontally wide auto-sized zones.
   - If `y` exceeds the canvas height, grow `deck_presentations.canvas_height` to fit (`y + 264`), in the same transaction.

## Allowed files (in addition to the original and amendment 1)
- `deck-lab-builder.js`: the API object (`freeZonePosition` only), plus the New zone dialog save path.
- `deck-lab-considering.js`: calling `freeZonePosition` when available.

## Acceptance criteria (replace AC-9; keep AC-10..12)
- AC-9a `test_ui_new_zone_lands_in_free_rendered_space` (harness): with rendered zones including a wide spread zone, the New zone command carries x/y whose 360×240 rect does not intersect any rendered zone rect, gutter included.
- AC-9b `test_considering_uses_free_zone_position_when_available` (harness)
- AC-9c `test_server_fallback_places_new_zone_below_all_and_grows_canvas`: Python; zones created without x/y have y below every existing zone's bottom, and the canvas height grows when needed.

Visual DoD: on the review server (whose seed creates zones WITHOUT coordinates, i.e. the server fallback), the `playmat` screenshot shows no overlapping zones.

## Completion
COMMON.md definition of done; FULL suite plus black; `git commit --amend`; include this file; update the report.
