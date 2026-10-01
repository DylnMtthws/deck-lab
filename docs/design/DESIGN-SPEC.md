# Deck Lab builder — design specification (v1)

Owner decision (2026-10-01): **Direction A ("focused workbench") is the structure. Direction B's stacks become a display mode. The playmat stays, and keeps every interaction that makes it feel like real cards on a real mat.**

Visual references are mock-ups rendered from `docs/design/builder-mockups/`:
- `a-focused.html?state=default|interact|view` (structure, list, rail, status bar)
- `b-visual.html` (stacks)

Rendered screenshots, absolute paths readable from any worktree, in `/Users/dylan/Projects/mtg/deck-lab/worktrees/competitive-ux/docs/design/builder-mockups/shots/`:

| Screenshot | Shows |
|---|---|
| `A1-default.png` | Hover row, Card tab |
| `A2-interact.png` | Selection bar, issues popover, Tools tab |
| `A3-view.png` | View popover, Deck tab |
| `A4-1280.png` | Default at 1280 px |
| `B1-default.png` | Stacks |
| `B2-1280.png` | Stacks at 1280 px |

The proposed tokens and primitives live in `docs/design/builder-mockups/foundations.css`.

This document is the contract. If a mock-up and this text disagree, this text wins.

---

## 1. Foundations

### 1.1 Tokens
Add these to `:root` in `deck-lab.css`, keeping every existing token:

```
--panel-3:#232b45; --line-soft:#1d2440; --faint:#6b7394;
--brand-soft:rgba(233,69,96,.14); --success-soft:rgba(87,211,155,.14); --warning-soft:rgba(240,189,103,.14);
--t-11:11px; --t-12:12px; --t-13:13px; --t-14:14px; --t-16:16px; --t-20:20px; --t-28:28px;
--s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px;
--r-sm:6px; --r-md:9px; --r-lg:13px; --r-pill:999px;
--row-compact:36px; --row-comfy:44px;
--shadow-pop:0 12px 32px rgba(0,0,0,.45),0 0 0 1px var(--line);
--shadow-card:0 2px 6px rgba(0,0,0,.45); --shadow-card-lift:0 10px 24px rgba(0,0,0,.55);
--focus:0 0 0 2px var(--ink),0 0 0 4px rgba(233,69,96,.55);
--ease:cubic-bezier(.2,.7,.2,1); --dur-fast:120ms; --dur:180ms;
```

### 1.2 Type
- The only allowed sizes in builder chrome are **11, 12, 13, 14, 16, 20 and 28 px**. No text below 11 px anywhere in the builder.
- Body and list rows are 14 px. Controls and secondary text are 13 px. Meta, chips and the status bar are 12 px.
- Eyebrow labels are 11 px, weight 600, uppercase, `letter-spacing:.08em`, **sans** (not mono), colour `--faint`.
- Panel titles are 16 px/600. Metrics are 20 px/600. Hero numbers are 28 px/600.
- Use mono (`--mono`) ONLY for counts, quantities and key caps, always with `font-variant-numeric: tabular-nums`. No mono-caps labels.

### 1.3 Spacing, radius, heights
- Spacing uses `--s-*` only.
- Buttons are 32 px high (small: 26 px). Fields are 34 px. List rows are `--row-compact`, or `--row-comfy` when density is comfortable.

### 1.4 Primitives
Extend the existing `dl-` primitives. There must be exactly ONE canonical class per primitive.
- `.dl-button` with modifiers `.is-primary`, `.is-ghost`, `.is-sm`.
- `.dl-icon-button`: 32×32, 16 px stroke icon, colour `--muted`, hover `--panel-2`/`--body`, `.is-on` → `--brand-text`. It always has an `aria-label` and a tooltip (`data-dl-tip`).
- `.dl-field` and `.dl-select`: styled; never browser-default.
- `.dl-segments`, plus a `.dl-chip` set (`.is-ok`, `.is-warn`, `.is-brand`) with `.dl-dot`.
- `.dl-kbd`.
- `.dl-popover`: panel, `--r-lg`, `--shadow-pop`, padding `--s-3`. It closes on Escape and on outside click, and returns focus to its opener.
- `.dl-tabs` and `.dl-tab`.
- `.dl-eyebrow`.

**No browser-default form control may be visible in the builder.** The capture lint enforces this.

### 1.5 Icons
Create `static/deck-lab-icons.js`, which exposes `window.DeckLabIcons.svg(name, {size})`. It returns an `<svg>` element: `viewBox 0 0 24 24`, stroke `currentColor`, width 1.7, round caps, no fill.

The names are: `back undo redo search sliders more pin pin-off thumb-up thumb-down comment chevron-down x check plus minus grid stack spread layers alert filter copy download external image`.

There is no emoji anywhere in the builder UI.

### 1.6 Motion
- Hover and focus transitions use `--dur-fast`. Layout and lift transitions use `--dur`.
- Everything respects `prefers-reduced-motion: reduce`, which means no transforms or animation.

### 1.7 Focus
Every interactive element shows `box-shadow: var(--focus)` on `:focus-visible`.

---

## 2. Regions (Direction A)

### 2.1 Header (`.dl-workspace-header`)
- **Left:**
  - back icon button
  - brand mark
  - breadcrumb: "Build" in `--muted`, a "/" in `--faint`, then the deck title at 14 px/600, still click-to-rename
  - save-state chip: dot plus text, with "Saved" in `--success`, "Saving…" in `--muted`, and "Retry save" as a `.is-warn` button
  - a 1 px divider
  - **undo and redo icon buttons** (`data-undo`, `data-redo`). These replace the old text labels. When disabled they show at 40 % opacity, and their tooltips name the action.
- **Right:**
  - view switch `.dl-segments` (Playmat | Decklist)
  - **Export** `.dl-button` with a chevron, opening the export menu (§2.9)
  - account menu
- The separate "Playmat…" header button is removed. Surface settings move to the View popover in playmat view (§2.2).

### 2.2 Toolbar (`.dl-builder-toolbar`, marked `data-lint-bar`)
One row, 52 px, with nothing clipped or overlapping at 1280 px or 1440 px.

1. **Search field.** About 420 px wide at 1440 px, and it may shrink to a 280 px minimum. It holds:
   - a search icon and the placeholder "Add or find a card";
   - a **destination pill** "to <zone>", a button that opens a zone menu (replacing the "Add to" select);
   - the `/` key cap.

   The existing combobox and results list behaviour is unchanged.
2. **View button** (`data-view-options`), a ghost button with the sliders icon. Its label is "View · <Group> · <Sort>" in Decklist view and "Mat · <Surface>" in Playmat view. It opens a `.dl-popover`:
   - **Decklist:**
     - Display `.dl-segments` with `data-display` = `text` (List), `stacks`, `grid` and `spoiler`
     - Group by `.dl-select` (Zone, Type, Role)
     - Sort by `.dl-select` (Manual, Name, Mana value)
     - Density `.dl-segments` (Compact, Comfy)
   - **Playmat:**
     - Surface (the existing surfaces and uploaded playmats, reusing the current picker dialog content)
     - Zone outlines toggle and Dim inactive zones toggle (the existing presentation flags)
     - Fit button

   All of these keep using the existing `update_view` and `update_presentation` commands.
3. Spacer.
4. Hint text "Press ? for shortcuts" in 12 px `--faint`, with a `.dl-kbd`. It is hidden below 1360 px.
5. **Deck options** icon button (••• , `more` icon). It opens a menu with Tags…, New zone, Commanders…, Compare to meta (if enabled), Share link, Delete deck. **Tags and New zone leave the toolbar.**

**Selection mode.** When at least one card is selected, the toolbar contents are REPLACED by the selection bar:
- a `.dl-chip.is-brand` "N selected"
- "Move to" `.dl-select` with the zones
- "Move to Considering", or "Move to deck" when all selected cards are in a private zone (keep T04's logic)
- "Set role" `.dl-select`
- "Remove" ghost button
- a spacer
- "Clear" ghost button with an `esc` key cap

With 0 selected, the selection bar is not rendered. That replaces the always-visible `[data-bulk-controls]` and its red buttons.

**The card count leaves the toolbar.** It lives in the status bar only. The `[data-deck-count]` element may remain in the DOM, visually hidden, for existing tests.

### 2.3 Decklist: List display (`data-display=text`)
- **Column grid:** `28px 52px minmax(200px,1fr) 96px minmax(160px,260px) 96px`. The columns are checkbox, quantity, card, cost, type, and actions.
- **Header row:** 30 px, 11 px eyebrow style, labels "Qty", "Card", "Cost", "Type".
- **Rows:** fixed height (`--row-compact` or `--row-comfy`) with `--r-sm` radius. Hover background is `--panel`, and selected background is `--brand-soft`.
- **Shown only on row hover or focus-within** (but always shown while the row is selected):
  - the checkbox, as a custom 16 px box;
  - the quantity −/+ steppers, as 18 px tiles;
  - the feedback icon buttons (thumb-up, thumb-down, comment), 26 px each.

  A feedback icon that has a value stays visible with `.is-on`. The comment icon shows a 6 px brand dot when a comment exists.
- The **Zone and Role columns are removed** from rows:
  - **Grouped by Role or Type:** a zone chip appears after the name only for non-library zones (for example "Considering").
  - **Grouped by Zone:** a muted role chip appears after the name.
  - Role editing lives in the card panel (§2.6), the selection bar, and the existing `m` and role hotkeys.
- **Group header:** 32 px, with:
  - a caret (collapsible, keeping the existing collapse persistence);
  - the name at 13 px/600;
  - the count in `--faint`;
  - a 72×4 px share bar for that group's share of the library.

  The Commander group is first and uses the name chip "Commander". The private-zone group (Considering and others) shows the count suffix "· not counted".
- Clicking a row selects it in the card panel. Double-clicking opens the existing image dialog. The T00 `deck-lab:entry-hover` and `deck-lab:focus-entry` events must keep firing.
- Mana costs use `DeckLabMana` at 1.05 em.

### 2.4 Decklist: Stacks display (`data-display=stacks`, new)
From mock-up B.
- Columns are 172 px wide, wrap, and have gaps of `--s-5` vertically and `--s-4` horizontally.
- One column per group (respecting Group by). The column header is a colour swatch, then the name at 13 px/600, then the count.
- Cards are card images, 488:680, radius 8 px, overlapping so each card shows its title bar (about 28 px). The last card in a column is fully visible.
- Hovering or focusing a card lifts it (`translateX(10px)`, `--shadow-card-lift`, 2 px brand outline, `--dur`) and updates the card panel.
- There is a quantity badge (`N×`) for quantities above 1, and a small thumb badge when feedback is set.
- Click focuses the card in the panel. ⌘ or Ctrl-click toggles selection. Double-click opens the image dialog.
- **Drag a card to another column:**
  - grouped by Zone → `move_entry`;
  - grouped by Role → `set_role`;
  - grouped by Type → not allowed (`not-allowed` cursor, no drop).

  Reuse the builder's existing drag preview and drop-state classes.
- Keyboard: arrow keys move focus within and across columns, and Enter focuses the card in the panel.
- Stacks REPLACES the old Grid in the default Display order (List, Stacks, Grid, Spoiler). Grid remains available.

### 2.5 Grid and Spoiler displays
- **Grid:** keep the current behaviour and adopt the tokens.
- **Spoiler: fix the bug.** Inline mana symbols in rules text must be 1.05 em, never image-sized. The capture lint `bigSym` must be 0. Rules text is 13 px/1.55.

### 2.6 Right rail: tabs (`[data-rail-tabs]`)
The rail is 368 px at 1440 px, and may narrow to 320 px at 1280 px. Its header holds `.dl-tabs` with **Card | Deck | Tools** (`data-rail-tab=card|deck|tools`), followed by a spacer, the existing pin control as an icon button (`pin`/`pin-off`), and the existing collapse toggle.

The last-chosen tab persists in `localStorage` per deck.

- **Extension API:** `DeckLabBuilder.railSection(id, title, {tab})`, where `tab` is one of `'card'|'deck'|'tools'` and defaults to `'deck'`, returns a section inside that tab's pane. Existing callers must be updated to pass the correct tab (see the mapping below).
- **Card tab** (T08 card panel, restyled):
  1. The image at 88 % width, centred, with 4.5 %/3.2 % radius and a shadow.
  2. A title row: name at 16 px/600 on the left, cost symbols on the right.
  3. The type line at 13 px `--muted`.
  4. A rules box: `--ink-deep` background, `--r-md` radius, 13 px/1.55.
  5. A meta row: chips (copies, zone, role, where the role chip is a `.dl-select`-styled dropdown to change the role), plus the feedback icon buttons on the right of the same row.
  6. Validation issues for the entry, as `.dl-chip.is-warn` rows.
  7. `[data-card-panel-slot=evidence]` with the T12 "Why this card?" section and T13 "Replace with…", restyled to the tokens.
- **Deck tab:**
  - Mana curve: bars with the count above each bar and the mana value below, eyebrow "Mana curve · avg X.X". The active filter bin uses `--brand`, and the click-to-filter behaviour from T06 is kept.
  - Color requirements: each row has the symbol, a 6 px bar in that colour, and "NN% · **N** src". There is one muted explainer line.
  - Zones: label on the left, mono count on the right; clicking a zone row focuses that zone (existing behaviour).
  - Tags: chips plus a "Manage" ghost button.
  - Tournament evidence (T12) and Goldfish simulation (T14), restyled.
- **Tools tab:**
  - **Draw odds** card (`--ink-deep`, `--r-lg`): a 28 px/600 result ("87.4%"), then the sentence "to see at least [n] [category] in [seen] cards", where the brackets are compact inline `.dl-select`/number fields. Under it, a muted line "K <category> in N cards".
  - **Sample hand:** "Draw 7" (primary small), "Draw a card" (small), then card thumbnails at 62 px wide, overlapping by 18 px, each focusable and focusing its entry. Then a muted summary, e.g. "2 lands · 1 rock". Keep T06's state persistence.
  - **Your verdict:** `.dl-segments` (Good / Mixed / Bad) plus a styled textarea, keeping T07's debounce.
  - Compare to meta (T13) button.
- **Tab mapping for existing `railSection` ids:** `pips`→deck, `odds`→tools, `sample-hand`→tools, `feedback`→tools, `evidence`→deck, `simulation`→deck.

### 2.7 Status bar (`[data-status-bar]`, also `data-lint-bar`)
36 px tall, `--panel` background, 12 px text.
- **Left:**
  - count: mono "87" plus a `--faint` "/100";
  - an 80×4 px progress track, filled `--warning` below 100, `--success` at 100, and `--brand` above 100;
  - legality chip: `.is-ok` "Legal", or a `.is-warn` button "N issues" that opens a `.dl-popover` ABOVE the bar. Each issue row in it shows a kind chip plus text, and clicking an entry issue calls `focusEntry`.
- Then a divider, then the type pills: "Creature **5**", with the label `--muted` and the number `--body` at weight 500.
- **Right:** "+N considering" chip (T04), then "Verdict: Mixed" chip (or "Verdict: not set"), which opens the Tools tab.
- The bar must **never cover other controls.** In Playmat view, the zoom control sits above it (bottom offset = bar height + `--s-3`). The capture lint `underStatus` must be 0.

### 2.8 Playmat (preserve and enrich)
**These interactions must keep working.** They are covered by existing tests, which must stay green and must not be weakened:
- free zone placement by dragging the zone bar, plus zone layering;
- per-zone layouts Spread, Stack (fan) and Grid, through the existing `set_zone_layout` command;
- dragging cards between zones, and from the search results onto a zone;
- click to select a card, and ⌘/Ctrl-click for multi-select;
- the zone menu: rename, select all, sort by mana value, delete;
- pan, zoom and Fit, with saved viewport persistence;
- surfaces and uploaded playmat images;
- basic-land quantity badges and legality marks on cards.

**Restyle:**
- Zone window header: 36 px, name at 13 px/600 sans (not mono caps), count `.dl-chip`, icon buttons (`grid`, `stack`/`spread`, `more`) from the icon set with tooltips.
- Zone body: `--panel` at 86 % opacity over the surface, `--r-lg` radius, `--line-soft` border. The active zone gets a 1.5 px `--brand` outline.

**Real-card feel (new, visual only):**
- **Cards:** 4.5 %/3.2 % radius and `--shadow-card`. On hover, a card lifts by `translateY(-4px)` and takes `--shadow-card-lift` (`--dur`).
- **Dragging:**
  - the source card dims to 40 %;
  - the drag preview is the card image rotated 3° with `--shadow-card-lift`;
  - a valid drop zone glows (`--brand-soft` inset plus a brand outline).
- **Stack (fan) zones:**
  - The physical depth is visible: each of up to 6 cards below the top is offset 3 px down and right with its edge showing; beyond 6, a `+N` badge appears.
  - **Peek:** hovering the stack for 300 ms, or focusing it, fans the top 5 cards out horizontally (`translateX(i × 26px)`, `--dur`). Leaving collapses them.
  - Peeking is purely visual. The stored layout does not change.
- **Unstacking:** dragging any visible card out of a stack moves it to the drop zone, as it does today. While peeking, a hovered card under the cursor can be dragged directly.
- **Spread ↔ Stack toggle:** the change animates cards between positions with a FLIP transition (`--dur`). There is no animation under reduced motion.

### 2.9 Menus and dialogs
- **Export menu** (`.dl-popover`, `role=menu`): each item has an icon, a label at 13 px, and a description at 12 px `--faint`:
  - Copy list — "Moxfield, MTGO, plain text"
  - Copy for Archidekt — "With [Commander] tags"
  - Download .txt
  - Buy deck — "Opens Mana Pool"

  Status feedback appears inline in the menu.
- **Keyboard shortcuts dialog:** a two-column table. On the left, the action at 13 px. On the right, the key caps, with alternatives grouped as `⌘ Z` / `Ctrl Z` and separated by a `--faint` "or". The tooltip must not overlap the dialog border.
- **Comment dialog:** title "Comment on <card name>", a styled textarea, and Save (primary) and Cancel.

### 2.10 Research (Cards tab)
- **Bug:** the search field must keep showing the user's full query `q` after a syntax search.
- Show applied terms as removable `.dl-chip`s. For example, "t:instant ×", where removing a chip removes that term from `q` and reloads.
- Show unsupported terms as `.dl-chip.is-warn` with the title "Not supported yet".
- Both chip rows sit under the result count. There are no plain-sentence notices.

---

## 3. Visual definition of done (every design task)

Tests passing is necessary but NOT sufficient. A design task is done only when:

1. **The capture lint is clean.** Run the seeded review server and the capture harness against YOUR worktree:
   ```
   PY=/Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python
   PYTHONPATH=src:. $PY /Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/seed_and_serve.py --root . --port <your port> --state /tmp/<task>-state.json &
   node /Users/dylan/Projects/mtg/deck-lab/worktrees/.cux/visual/capture.cjs /tmp/<task>-state.json /tmp/<task>-shots 1440,1280
   ```
   For every scenario your task touches, at both widths, `summary.json` must show:
   - no `OVERFLOW`, no `bar:`, no `covered:`, no `native:`, no `bigSym:` and no `underStatus:`;
   - `offScale` only for sizes outside the builder chrome. No size below 11 px anywhere you touched.

   Kill your server by its PID when you are done.
2. **Behaviour is preserved.** Gate 1, 2 and 3 from COMMON.md pass, including `black --check`.
3. **Existing tests that assert REMOVED markup** (for example the old bulk-controls bar, the text undo labels, or the zone column) may be updated, but only to assert the new equivalent element and behaviour. List every such test change in your report as `test::name — old assertion → new assertion — why`. Deleting a behavioural assertion is not allowed.
4. **Report.** Your report lists the `summary.json` lines for your scenarios and the screenshot paths.
5. **Orchestrator review.** The orchestrator then reviews the screenshots against this spec and the mock-ups. "Looks off-spec" is a valid rejection reason.
