# R3 wide-answer audit — synthesised action list

15 of the 80 plans were judged (the widest answers, covering 15 of the 47 scored questions). Verdicts: 11 `bound_problem`/`wrong_answer_shape`, 4 `mixed`, **0 where the width was justified by the ask**.

**Headline:** 5 of 15 audited questions (33%) have a short or mis-scoped label. Two draft review notes assert something factually wrong about a card. Both findings bear directly on the open DoD item "the golden set is no longer `contested`" — a one-in-three label-defect rate in the audited sample is not a ratifiable golden set.

**Second headline:** the label defects cluster in exactly the four questions where retrieval is doing real work (metagame-010, combo-004, deck-local-009, mechanic-005), and the over-width clusters in the eleven where the asker typed the card name. Where the label is trivially right, the plan is over-wide; where the plan is working, the label is short. Both halves say the same thing: 47/47 is measuring the easy half.

---

## A. LABEL GAPS — need an owner ruling (I cannot fix these)

### A1. `metagame-010` — the label looks pack-derived, and this is the batch's only genuine discovery question · **highest priority**
`required_oracle_ids` is exactly {Boseiju Who Endures, Otawara} — which is exactly the set of these lands present in `config/cedh_packs/kinnan_basalt.yaml` — while the ask is about the recorded *field*. Under the plan's **own** stated definition of interactive lands (leg 2: "activated ability that sacrifices this land to destroy target nonbasic land"), these all qualify literally, are colourless so they pass the `[U,G]` subset filter, are commander-legal, and **were all returned**:

> Wasteland, Strip Mine, Demolition Field, Volatile Fault, Field of Ruin, Ghost Quarter, Tectonic Edge, Dust Bowl

Wasteland and Strip Mine are the two canonical answers to "which interactive lands" in any cEDH context. Ruling needed, one of:
- **(a)** expand the key to that set — the question then genuinely measures discovery; or
- **(b)** the key means "the two the Kinnan *field* plays most", in which case record that this question's retrieval half **cannot be scored until R5 supplies counts**, and stop reading its pass as retrieval evidence.

This is the trap `metagame-004`'s own review note already names ("one list is not a cohort"), applied to a label instead of an answer.

### A2. `combo-004` — truth set is 6, not 4; and the review note is wrong about a card
- **Add:** **Finale of Devastation** (r5) and **Nature's Rhythm** (r4). Both print "search your library … for a creature card with mana value X or less and put it onto the battlefield" — verbatim the template of the required Chord of Calling — and both reach Kinnan at mana value 2. The plan's review note already suspects this; it is correct.
- **Do NOT add Invasion of Ikoria, and correct the note.** It reads "search your library and/or graveyard for a **non-Human** creature card with mana value X or less". Kinnan, Bonder Prodigy is a **Human** Druid. The note's "fetches a creature of mana value three or less" misreads both the subtype restriction and the X bound. **Dizzy Spell** (r3) is the same trap: transmute at its own mana value 1, so it reaches neither the mv-2 Kinnan nor the mv-3 Monolith.
- Both traps sit inside the returned window unmarked. The clarified ask's "under each tutor's actual restrictions" exists to catch exactly this, and **no step in the plan performs that check** — `CardFilters` cannot express a second-order fetch restriction. That is narration work, and the review note should say so rather than assert Invasion qualifies.

### A3. `deck-local-009` — label short by 2 of 5
- **Add:** **Nature's Rhythm** (r3) and **Invasion of Ikoria** (r9). Note the deliberate asymmetry with A2: here the ask is "puts a creature from library onto the battlefield" with no requirement to reach Kinnan, so Ikoria's non-Human clause is irrelevant and it qualifies. Same card, gap in one question and trap in the other.
- **Retrieval fragility worth recording:** Invasion of Ikoria's `oracle_text` column is null (transform layout; text lives on faces), so it ranked on name and type line alone. Any DFC is currently near-invisible to the dense/lexical legs.

### A4. `mechanic-005` — one returned gap, one *unreturned* gap, one class ruling
- **Add: Mask of the Mimic** (returned at r2). `{U}` instant, "Search your library for a card with the same name as target nontoken creature, put that card onto the battlefield." An instant-speed tutor that puts a creature into play; only the sacrifice and name-matching are extra.
- **Add: March of Burgeoning Life** — `{X}{G}` instant, same effect — **and it was not returned in the top 50.** This is a real recall miss, and **the gate is structurally unable to see it** because the label requires only Chord of Calling. It is the audit's single best piece of evidence that recall measured against a same-authorship label is self-confirming.
- **Ruling needed on the dig-not-search class:** Collected Company (r21), Kindred Summons (r14), Summoning Trap (not returned) put creatures into play at instant speed but do not search. Out under the clarified ask ("searches for a creature"); arguable under the raw ask ("a tutor that can put a creature into play at instant speed").
- With a corrected label this stops being a one-card question and its breadth number becomes meaningful.

### A5. `mechanic-006` — scope ruling + note correction (label is correct as scoped)
- **Scope ruling:** within `color_identity [U,G]`, Drift of Phantasms is the complete and correct answer. Corpus-wide, the label is short by **Perplex** and **Dimir Machinations**, both mana-value-3 transmute cards that fetch Basalt Monolith. Pack-scoped or corpus-scoped — say which, once, for the category.
- **Correct the review note before ratification:** it says the discriminator is "transmute cost three versus one". The forbidden Dizzy Spell *also* has Transmute {1}{U}{U}. The discriminator is the card's own **mana value** (3 vs 1), which is what the `mana_value` filter actually does.

---

## B. PLAN PROBLEMS — concrete changes, no ruling needed

Recall is preserved by construction in every item below: in each case the deepest true member's observed rank is at or above the proposed bound.

### B1. Bounds left at the `CardSearchQuery` default (11 questions)
The default is `top_k=50`. These plans never set one, so the default is deciding the answer width. Per-question, derived from the answer's shape (not from a threshold):

| Question | Step(s) | Now | To | Why that number |
|---|---|---|---|---|
| `metagame-001` | `named_rock` | 50 | 1 (3 for margin) | Asker supplied the name; card at r1; note itself says "a name lookup, not retrieval evidence" |
| `metagame-002` | `rhystic`, `remora` | 50 each | 1 each | Both legs rank their target first; union becomes exactly the 2 named enchantments |
| `metagame-005` | `named_counter` | 50 | 1 (3 for margin) | Same shape as -001 |
| `metagame-007` | `named_outlet` | 50 | 1 | Thrasios at r1; r2–50 are Merfolk Wizard subtype bleed |
| `metagame-009` | `ring`, `sphinx` | 50 each | 1 each | Widest in batch — 100 rows for a two-card comparison |
| `metagame-011` | `diamond`, `chrome` | 50 each | 1 each | Both legs rank their target first |
| `rules-001` | Mental Misstep step | 50 | 1–3 | Named card at r1 |
| `rules-004` | `boseiju` | 50 | 1–3 | Named card at r1 |
| `rules-008` | Basalt Monolith step | 50 | **3** | Deliberate window: Grim Monolith (r2) and Mana Vault (r3) print the same doesn't-untap template and are the comparanda a narrator wants |
| `rules-009` | Endurance step | 50 | **3–5** | Deliberate window: Walker of the Grove (r3), Cloudthresher (r4) print the same evoke-plus-ETB shape |
| `combo-008` | `mass_untap` | 50 | 3 | Dramatic Reversal r1 exact, Reset r2 adjacent; only 12 of 50 rows mention untapping and 15 are bounce spells |

**Reject the `eligible_population` defence where it appears** (`rules-008`'s note, and `rules-001`'s "a one-card haystack proves nothing"). Those notes are sound about the *haystack* and silent about the *bound*: `rules-008` keeps its honest 377-card eligible population, `rules-009` its 3,595, `rules-001` its 896 — lowering `top_k` does not shrink the haystack. Keep the haystack, which is what makes r1 meaningful; cut the window.

### B2. Query text that recruits its own noise (2 changes)
- **`metagame-010` `land_denial`:** delete the clause **"or that makes its controller search for a basic land instead"**. Presumably aimed at Ghost Quarter's rider; it instead recruits ~21 of 82 rows as pure basic-land fetching (all five Panoramas, Evolving Wilds, Terramorphic Expanse, Fabled Passage, Prismatic Vista, Warped Landscape, Escape Tunnel, Terminal Moraine, Shire Terrace, Hobbit Hole, Myriad Landscape, Blighted Woodland, Brokers Hideout, …). Ghost Quarter, Demolition Field, Volatile Fault and Field of Ruin all still match on their destroy clause — recall unaffected, a quarter of the answer gone.
- **`mechanic-005` `instant_tutor`:** delete **"with convoke"**. Convoke is a property of the labelled *answer*, not of the ask — mild answer-fitting — and it imports eight convoke non-tutors (Unexpected Assistance, Pause for Reflection, Complete the Circuit, Transcendent Message, Artistic Refusal, Gather Courage, Meeting of Minds, Pack's Favor). Then `top_k` 50 → ~10.

### B3. A missing filter (1 change)
- **`metagame-005`:** add `filters.color_identity: [U, G]`, `color_mode: subset`. Every other Kinnan-scoped plan carries it; this one does not, which is why a Kinnan question answers with **Flawless Maneuver at rank 2** and returns Obscuring Haze, Deflecting Swat, Deadly Rollick, Guardians' Pledge, Mogg Salvage, Sivvi's Ruse — cards the asker cannot play. Do this even after cutting `top_k`, so the query text is safe to reuse.

### B4. `excluded_tags: [mana:land_to_battlefield]` — a shipped predicate that fixes two questions (2 changes)
The tag is defined at `src/sabermetrics/mechanics/tags/mana.py:175` as "searches its controller's library for a land and puts that card onto the battlefield" — i.e. exactly the fetchlands.
- **`combo-004` `tutors` leg:** removes the 7 fetchlands, lifting the required **Trophy Mage from r19 to ~r14**, which lets `top_k: 15` hold all six true members (breadth 12.5 → ~2.5, `eligible_population` unchanged at the honest 100). This one is not optional — a bound alone cannot fix `combo-004`, because a required card is stranded at r19.
- **`deck-local-009`:** ask-derived (the clarified ask says "a creature card"), removes 7 of the top 18 rows, touches no true member.

### B5. Bounds proportionate to a deck-scoped population (2 questions)
- **`deck-local-009`:** the plan file's own header states the rule it breaks — "a deck-scoped search states a bound proportionate to its question … these plans ask for ten or twenty" — yet both legs use `top_k: 50` against a **100-card deck**, returning 56 rows (56% of the list) with both legs truncated, so the union carries `set_input_incomplete` for a five-card answer. Deepest true member is r9. Set `tutor_to_battlefield` to **10** and `top_of_library` to **5** (exactly one card in this deck prints that template — the commander — and the leg returns 50). With B4: ~12–15 rows containing all five, breadth 18.67 → ~3.
- **`metagame-010`:** under `[U,G]` subset the entire true population of `channel_lands` is **two** cards (the other three Kamigawa channel lands are off-identity), so 48 of its 50 rows are guaranteed filler. Set `channel_lands` to **5**, `land_denial` to **15**. With B2 that lands near 17 rows, almost all genuinely interactive.

### B6. A plan that under-serves its own clarified ask (1 question)
- **`rules-004`:** the clarified ask has two halves — "is channel casting" and "what interaction can respond". The `card_search` population is filtered to `required_types: [Land]` + `color_identity: [G]`, so **no Stifle-class ability counter (e.g. Green Slime) can be in the returned set by construction.** Either drop that half from the clarified ask, or give it its own step with its own population. Today the plan silently under-serves its stated ask while scoring a pass.

### B7. Replace a ranked step with a structured one (1 question, blocked on a small build)
- **`mechanic-006`:** 49 of 50 rows are noise against an eligible population of 2,892, and the one correct card sits at **r5 behind four tutor-flavoured cards with no transmute at all** (Intuition, Repurposing Bay, Phyrexian Portal, Long-Term Plans). The ranker is not carrying this question. Ship a **`cost:transmute`** tag — "transmute" is already in `mechanics/oracle_keywords.py:195`, and `_keyword_cost_tag` in `mechanics/tags/cost.py:72` already builds this exact shape for convoke, delve and improvise — then replace the `card_search` with a `tag_filter` carrying `required_tags: ('cost:transmute',)` and `mana_value_min/max: 3.0`. That returns exactly one card with no ranking involved. **Until the tag ships, `top_k: 5` is the floor that still passes** — and the fact that 5 is the floor *is* the diagnostic.

---

## C. ACCEPTABLE WIDTH

**None.** Not one of the 15 audited answers had a width justified by its ask. `metagame-010` is the only genuine population question in the batch, and its width is still wrong for the reasons in A1/B2/B5. Two answers earn a *deliberate* window wider than 1 — `rules-008` at 3 and `rules-009` at 3–5 — because printed comparanda sit at r2–r4 and a narrator wants them; that is an ask-derived window, not a threshold.

---

## D. PATTERNS (act on these once, not fifteen times)

**D1. The `top_k=50` default is authoring the answer.** 11 of 15 wide answers are plans that never set a bound. This is a plan-authoring habit, not a retrieval failure. Per the owner's ruling, no universal cutoff: the bound is derived from the shape of the answer — named card → 1–3; declared comparanda → 3–5; a true population known to be small under the filters → its size; a genuine open population → wide and say so.

**D2. There is no exact-name resolution, and 9 of 15 questions need one.** `resolve_names()` ships at `substrate/catalog.py:439`, is exposed at `assistant/sources.py:120` and `:188`, and is **unreachable from `ir.py`'s seven step kinds**. So "the asker named it" is a ranked guess that happens to land. The near-misses inside the returned windows are the evidence it will not always land: **Boseiju, Who Shelters All** (`rules-004` r11), **Archetype of Endurance** (`rules-009` r16), **Minor Misstep** (`rules-001`), **Fierce Retribution** (`metagame-005` r11, matched on the name token "Fierce"), **Basalt Golem / Requiem Monolith / Darksteel Monolith** (`metagame-001`). Recommend a `card_lookup` step kind in the R4 IR revision; reserve `card_search` for questions that describe a mechanic. An exact printed name is a resolution, not a retrieval.

**D3. Lexical name-token bleed is the dominant noise family, and it is invisible at k=1.** `metagame-011` (83 rows on "Mox"/"Diamond"/"Chrome"), `metagame-009` (~15 rows on the token "Ring"), `metagame-002` (Rhystic Circle, Rhystic Deluge, eight `Mystic*` cards), `metagame-001` ("Monolith"/"Basalt"). Harmless once B1 lands — but it means **none of these query texts is safe to reuse in a wide step**, and it is a standing argument for D2.

**D4. Do not reach for the new `required_subtypes` here — it makes three of these worse.** In `metagame-009` the noise *is* Sphinx (~25 creatures), so `required_subtypes: [Sphinx]` would preserve every wrong row and prune only correct non-Sphinx ones. Same in `metagame-007` for `[Merfolk, Wizard]`. In `metagame-010`, `excluded_subtypes` cannot cut the Cave/Desert filler without also cutting **Volatile Fault**, which is a Cave. The subtype capability is real and load-bearing where it landed (`deck-local-010`, `deck-local-001`); it is not the fix for width.

**D5. `answer_step` points at the ranked search while the substantive answer is a stated absence.** True for all six audited `metagame` questions (`field_statistics_deferred_to_r5`) and all four `rules` questions (`rules_index_not_built`). Two consequences:
- *Presentation:* a question titled "How often do recorded Kinnan lists play Basalt Monolith?" currently renders fifty mana rocks, which reads as an inclusion list and brushes against `popularity_is_not_quality`. Render the absence first and the resolved identity as one row.
- *Scorecard:* the 10 rules questions carry 10 of the 47 passes, and every one of those passes is a name lookup finding a name the asker typed, with the rules half unavailable. Recommend marking them **retrieval-uninformative** on the scorecard, alongside the existing discovery split — the same move, one level finer.

**D6. Answer-fitting leaks through properties, not just names.** The existing check publishes any plan that supplies a required card *name* its question does not. `mechanic-005`'s "with convoke" is not a name, so the check misses it, and it is exactly the same failure. Worth measuring: does the query text name a property that only the labelled answer has?

**D7. Two of the audited review notes are factually wrong** (`combo-004` on Invasion of Ikoria, `mechanic-006` on the transmute discriminator). All 80 plans are `review_status: draft` and ratification is an open DoD item. Notes are what the owner will read *instead of* re-deriving the card text; correct these before ratifying, and treat "the note asserts a fact about a card" as a thing that needs checking, not just the plan body.

---

## E. Suggested order

1. **A1** — `metagame-010` ruling. Blocks whether "discovery 31/31" means anything.
2. **A2–A5** — the four remaining label rulings, plus the two note corrections in A2/A5 and D7. All owner-only.
3. **B4** — `combo-004` `excluded_tags`. The one plan problem where a bound alone cannot fix it (required card at r19).
4. **B2, B3, B6** — the query-text and filter fixes, which change *what is retrieved*, and are independent of any bound.
5. **B1, B5** — the bounds. Mechanical, ~13 line edits, zero recall risk.
6. **B7** — ship `cost:transmute`; small, and it converts a ranked answer into a structured one.
7. **D2** — `card_lookup` step kind, into the R4 IR revision.

---

## F. What this audit does NOT establish

- **It covers 15 of 80 plans, 15 of 47 scored questions** — selected for width. It says nothing about the other 65 plans, and the label-defect rate found here (5/15) is a rate *in the widest answers*, not a measured rate over the golden set.
- **It can only see what was returned.** Misses are caught by luck. `March of Burgeoning Life` surfaced only because one auditor enumerated the template by hand. There is no systematic false-negative measurement in this audit, and none in G2 either — recall is scored against a label produced by the same authorship as the plans.
- **It is one judgement per question, unadjudicated.** The items filed as rulings (dig-not-search in A4, corpus-vs-pack in A5, field-vs-pack in A1) are filed as rulings precisely because an auditor cannot settle them.
- **It does not measure precision, and returned-per-required is still not precision.** In the 5 questions with short labels the ratio's denominator is wrong, which is the owner's stated reason for treating it as diagnostic. Applying it as a threshold would have "failed" `metagame-010`, whose real defect is its key.
- **It says nothing about answer *correctness* on the substantive half.** `rules_lookup` returns typed absence throughout and `field_stats` is R5. Every recommendation here changes what *renders*, not what is *known*.
- **It re-ran nothing.** Every rank cited is read off the audited batch against bundle `cf24c1ef…` / corpus `62a6198c…` under the pinned `bge-small-en-v1.5` and `bge-reranker-base` revisions. A bundle rebuild, a reranker change or a corpus swap moves the ranks, and the bounds in B1/B5 must be re-derived rather than inherited.
- **None of this moves the gate.** Every recommendation preserves recall by construction, so applying all of B leaves retrieval at 47/47 and discovery at 31/31. The number is not what improves; the answers are. Conversely, the label rulings in A *will* move the number in both directions, and that is the point — a corrected key is what makes 47/47 worth reporting.
