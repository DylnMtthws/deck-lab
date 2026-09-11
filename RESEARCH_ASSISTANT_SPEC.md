# Research Assistant — product and build specification

_Authored 2026-09-08. Branch `research-assistant`, worktree
`~/Projects/deck_lab/worktrees/research-assistant`, based on `deck-lab-refactor`
at f6f0bdd._

**This document supersedes [`RESEARCH_ASSISTANT_PLAN.md`](RESEARCH_ASSISTANT_PLAN.md)
as the authoritative statement of what is being built and in what order.** The
plan remains the reference for substrate detail — the mechanic tag format, the
retrieval fusion design, the Query IR, and the gate definitions — and those parts
are adopted here unchanged. Three of its product decisions are reversed, and one
of its self-descriptions is retired; §2 says which and why.

---

## 1. The promise

> **Understand your deck, investigate options, and test your ideas.**

The unit of value is not an answer. It is a **completed loop**:

```
question ──▶ evidence ──▶ experiment ──▶ decision ──▶ saved explanation
                 │                           │
                 └──────── re-enter ─────────┘
```

The player keeps authorship at every step. The assistant orchestrates the
corpus, the rules layer, the simulator and (later) the solver; it does not make
the change. Every capability it orchestrates is **also reachable by an ordinary
button** — the assistant is an accelerant on the Deck Lab, not a gate in front of
it.

Success is judged by three player outcomes, not by model quality:

1. A deck question is resolved faster than by hand.
2. The player can **restate what the result establishes** — and what it does not.
3. The player comes back to test another idea.

### 1.1 Naming — resolved

Deck Lab already has a top-level **Research** section (`/research`, commanders /
cards / metagame browse, `research_routes.py`, `research.py`). Shipping a
"Research Assistant" beside it produces two things called research that are not
the same thing.

**Decision (2026-09-08): the Deck Lab refactor's shipped taxonomy wins.** The
primary nav stays exactly as `deck_lab/base.html` renders it today — **Research**
and **Build**, in the header, the mobile drawer and the account popover — and
nothing is renamed. The assistant is **Ask** in every player-facing label.
"Research Assistant" is retained internally as the package (`assistant/`) and
document name.

Consequences, so the labels do not have to be rediscovered later:

- **Ask is not a nav item and has no page of its own.** It is a dock, opened from
  wherever the player already is, always bound to a context (§6.1). Adding a
  third nav link would imply a destination, and a destination invites exactly the
  behaviour §6.4 forbids — scrolling old messages to recover an experiment.
- Threads are reached through the deck or the research context they belong to,
  never through a global inbox.
- The four starting actions in §6.2 are phrased as questions because that is the
  dock's whole affordance.

---

## 2. What changes from the previous plan

| # | Previous plan | This spec | Why |
|---|---|---|---|
| 1 | UI is out of scope; A9, after ~13 weeks of headless work | **Thin UI pilot at R4, ~week 5–6**, with two actions | The plan's own thesis is that the substrate carries the intelligence. That is testable at two actions as well as at ten, and the *other* untested hypothesis — that players want this shape of help — cannot be tested headlessly at all. Thirteen weeks is too long to hold both risks open. |
| 2 | A8 "diff proposals" is the INTELLIGENT milestone, before UI | **Automatic diff proposals move to R8**, after variant comparison | Player-selected alternatives and player-authored variants serve the authorship goal directly, are far easier to evaluate, and do not require the deterministic candidate-generation and scoring machinery A8 assumes. An auto-proposed swap the player did not ask for is also the hardest thing in the product to prove is good. |
| 3 | Gate G4 measures citation *coverage* | **Two separate rigs**: a correctness rig and a usefulness rig, and citation *support* is measured, not just citation *presence* | A cited source can fail to support the claim attached to it. Coverage is a structural invariant (it should be 100% and a miss is a bug); support is a quality measurement and needs adjudication. Conflating them makes a passing scorecard uninformative. |
| 4 | "It is not a card evaluator. It does not say a card is good." | **Reasoned assessments of fit are in scope**, as a typed, visually distinct, citation-bearing tier | A feature restricted to reciting facts leaves most of the value behind, and it is the thing `bristly_billy_beane`'s reasoning is uniquely good at. §4 defines the guardrails that make this safe, and §4.3 shows it is already inside the `llm_may` charter rather than a change to it. |

Everything else from the plan is adopted: the mechanic tag corpus and its
precision discipline, hybrid retrieval with a local reranker, the typed
`ResearchPlan` IR with provenance-bearing result envelopes, oracle-id-only card
references, the golden question set, and the G1/G2/G3 gates.

---

## 3. Capabilities, in release order

| Capability | Example question | Output | Release |
|---|---|---|---|
| **Deck-aware discovery** | "Find instant-speed answers in my colors that also advance my engine." | Bounded card shortlist, the conditions each card matched, stated tradeoffs | R4 (first) |
| **Explain cards and interactions** | "Why would this card belong here?" | Mechanical explanation, relevant rules, dependencies, conflicts | R4 (first) |
| **Focused deck analysis** | "What should I investigate in this list?" | ~3 actionable findings, each linked to specific cards and evidence | R5 (first) |
| **Goldfish studies** | "Run 30,000 games against this assembly objective." | Assembly probability by turn, censoring, model coverage, saved report | R6 (supported-deck pilot) |
| **Variant comparison** | "Did these three changes improve consistency?" | Paired measurement of two player-authored lists, and what it cannot evaluate | R7 (next) |
| **Automatic diff proposals** | "What would you change?" | Previewable changeset, never auto-applied | R8 |
| **Solver decision studies** | "How does attempting now vs. waiting change across these scenarios?" | Conditional comparisons, sensitivity to declared assumptions | R9 (experimental) |

The assistant has **no deck-generation tool**. Generating a candidate from a
strategy pack stays where it is, on the deterministic `cedh/` path, reached by
its own button.

---

## 4. The epistemic contract

This is the core of the design and the thing that distinguishes the product from
a chatbot with a Magic prompt.

### 4.1 Four tiers, typed and visually distinct

Every assertion the assistant renders carries exactly one tier. The renderer
styles each tier differently and drops any assertion whose tier requirements are
unmet.

| Tier | What it is | Required citation | Renderer treatment |
|---|---|---|---|
| **Fact** | Card text, type, mana cost, colour identity, legality, rules text, rulings | `corpus:<snapshot_hash>` or `rules:<CR section>` or `ruling:<oracle_id>/<date>` | Plain, no hedging |
| **Field evidence** | What recorded tournament lists actually contain | `field:<commander>/<window>/n=<denominator>` — denominator, window and event-size floor are **required struct fields**, not prose | Always rendered with its denominator inline |
| **Measurement** | Simulator output | `sim:<simulation_input_sha256>` plus the honesty envelope (§5.2) | Never rendered without coverage and censoring |
| **Interpretation** | The model's reasoned assessment of fit | ≥1 citation drawn from the tiers above, and a `confidence` enum | Visually marked as interpretation, attributable to the assistant |

An interpretation with zero supporting citations is dropped by the renderer. That
is a structural rule, not a prompt instruction.

### 4.2 The shape of a good interpretation

The target register, from the product brief:

> "This card supports your recursion plan, but its activation competes with the
> mana you need for your commander. Its tournament inclusion supports
> investigating it; that alone doesn't establish that it improves this list."

Three things are happening there and all three are required by the schema:

- A **mechanical claim** tied to card text (Fact).
- A **stated tension**, not just a benefit.
- An **explicit statement of what the evidence does and does not establish**.

`InterpretationAssertion` therefore carries `claim`, `tension` (nullable but
prompted for), `supports` (citation ids), and `does_not_establish` (required,
non-empty). An interpretation that cannot name its own limit does not ship,
for the same reason a tag with an empty `limitations` field does not ship.

### 4.3 Why this is inside the charter

`CLAUDE.md`'s `llm_may_not` forbids: deciding legality, inventing cards or oracle
text, bypassing candidate constraints, creating simulator mechanics, silently
repairing a malformed simulator result, and **selecting from the whole card
corpus without deterministic narrowing**. None of those is fit assessment.
`llm_may` already permits "compare deterministic alternatives" and "explain
recommendations."

So the guardrail that matters is the last `llm_may_not` clause, and it is
preserved exactly: **every card the assistant assesses arrived from a query
result set as an `oracle_id`.** The model never reaches the corpus. It reasons
about a bounded, deterministically retrieved set, which is what "compare
deterministic alternatives" describes.

**ADR-029** records this: the assistant may render Interpretation-tier
assertions, subject to §4.1's citation requirement and §4.2's schema, and the
previous plan's "not a card evaluator" self-description is retired.

### 4.4 What must never appear

Inherited from the charter and enforced structurally, not by prompt wording:

- **No card name outside a result set.** The field is an `oracle_id` checked
  against the executor's outputs before rendering.
- **No rate without its denominator, window and event-size floor**
  (`popularity_is_not_quality`).
- **No invented neutral score.** Absence renders as absence
  (`absence_is_visible`).
- **No card price and no owned-cards input, anywhere** (ADR-025). The assistant
  inherits this without exception: no price citation kind exists, no retrieval
  filter reads a price column, and the prompts forbid mentioning cost. Note the
  legacy `card_fit.txt` passes `Price: ${price}` into the prompt — that is one of
  the generator assumptions §8.1 removes.

---

## 5. Experiments: what a measurement establishes

### 5.1 Capability is stated at the question level, not as a score

A single "82% supported" badge conceals the thing the player needs. The
assistant returns a `CapabilityStatement` before it returns a number:

```
can_measure:
  - "this assembly line" (objective id, from the pack)
  - "these mana and card-flow effects" (modeled_cards, by effect class)
cannot_measure:
  - "this deck's opponent-dependent value engine"  [reason: opponent_trigger]
  - "the proposed replacement's relevant ability"   [reason: unauthored]
```

The reason codes come straight from `coverage.inert_by_reason` in
`cedh-simulation-result.v3`: `interaction`, `opponent_trigger`,
`opponent_permanent`, `timing_only`, `no_object_in_model`, plus
`unauthored_cards`.

**Today those are counts, not lists.** Naming the specific affected cards — which
is what makes the statement actionable — requires **D3** in
[`docs/upstream-dependencies.md`](docs/upstream-dependencies.md), the structured
per-card inert/unauthored list (`result.v4`). Until D3 lands, R6 ships the
by-reason counts and the pack's declared `known_blind_spots`, and says plainly
that it can report how many cards are unmodeled but not which. That is a smaller
claim honestly made, and it is the correct behaviour regardless of whether D3
ever lands.

### 5.2 Acceptance is not representation

The simulator has **one installed registry pack**,
`kinnan-midrange-goldfish@1.0.0`, supporting one commander oracle id. It also has
a reserved generic execution mode, `derived-generic@1.0.0`
(`data/derived-generic.defaults.toml`), which is explicitly *not* a registry pack
and cannot be selected accidentally.

The generic mode declares its own blind spot in the file:

> `"only inherited card-set patterns can terminate a run"`

That sentence is the whole risk. A deck submitted under generic execution can be
**accepted, executed, and return a completely censored result** — not because the
deck is slow, but because the model has no terminal state for its plan. A player
reading "0% assembled by turn 12" would draw exactly the wrong conclusion.

Therefore:

- **R6 is Kinnan-scoped.** Only the authored pack's commander gets a goldfish
  study in the first release.
- Generic execution is not offered to players in R6. When it is offered (R7+), a
  run whose censoring exceeds a configured threshold **with no matched inherited
  pattern** renders as `NotRepresented`, not as a probability. This is
  `absence_is_visible` applied to a result that technically validated.
- The `strategy_pack.derived` boolean in `result.v3` is surfaced in every study
  report header, in words.

### 5.3 More games is precision, not realism

The study header is fixed copy:

> **Goldfish study · 30,000 games · Deck version 12**
> Objective: reach the declared assembly state.
> Results: assembly probability by turn, games that never assembled within the
> horizon, and effects the model could not evaluate.

And, beside the game count:

> More trials make this estimate more precise **within the model**. They do not
> make omitted interactions more realistic.

The objective and the coverage are given more visual weight than the game count.

### 5.4 30,000 games is feasible; the numbers, and what they are not

| Measurement | Value | Source |
|---|---|---|
| Service cap on games per request | `SIM_MAX_GAMES=60000`; above it, 400 | `STATE.md` |
| Deck Lab client today | `games=20000`, `objective_turn=3`, `timeout_seconds=330.0` | `cedh/simulator.py`, `config/cedh.yaml` |
| 20,000-game single run | **0.68 s** | Native GHA `ubuntu-latest` x64, seed 1, **two threads, idle runner**, manual run `33906314023` |
| Same, emulated linux/amd64 on the arm64 Mac mini | 2.641 s | `STATE.md` |
| 30,000-game **full 98-ablation sweep** | 297.61 s | Same runner |
| Same sweep, emulated on the Mac mini | 1,115.99 s | `STATE.md` |

Raising the request to 30,000 games is a **parameter change**, not new
machinery. Two cautions carry into the spec:

1. **Neither timing is a production latency or a concurrency measurement.** They
   are single runs on idle machines. The interactive tier is `shared-cpu-2x` with
   `SIM_MAX_CONCURRENT=1`; excess requests receive 429. The load test that would
   size this is specified in the simulator's `docs/hosting-cost-model.md` and
   **has not been run**. R6 therefore treats a study as an async job (§7.3) from
   the first commit, and does not assume sub-second response.
2. **The client's 330 s timeout exceeds the service's `SIM_TIMEOUT_SECONDS=300`.**
   That ordering is correct — the client should outlast the server so it receives
   the server's own error rather than inventing one — but it means a study's
   worst case is a five-minute server-side kill, which the job model must render
   as a failure, not a pending state.

Full card-ablation sweeps are a different workload entirely: they run on the
separate `sim-sweep` batch tier, are gated server-side by `SIM_ALLOW_SWEEP=1`
plus a bearer token (403/401 otherwise), and `ablate` is capped at
`SIM_MAX_ABLATIONS=8` on the interactive tier. **The assistant never requests a
sweep.** If sweep-backed analysis is wanted later it is an admin-triggered batch
job with its own budget, not a chat turn.

### 5.5 Removal is not replacement

An ablation removes a card's *effect*. A swap replaces a card with another card.
These answer different questions and the second one is what players ask.

- Existing leave-one-out ablations are a **diagnostic** — "how much does the
  model's outcome depend on this card being present" — and are labelled as such.
- **A swap requires both lists executed.** That is R7, and it is why R7 is a
  phase rather than a flag on R6.
- Paired comparison needs a validated method. The simulator already supports
  common random numbers with a measured 28× variance reduction on the sweep path;
  R7's acceptance criterion is that the paired interval is validated against
  independently seeded arms before any comparison is shown to a player.

### 5.6 Studies are objects with a lifetime

Each saved study records: the list (by `deck_sha256`), the deck document revision,
the settings (`games`, `objective_turn`, pack id/version/`content_sha256`,
`simulation_input_sha256`), the full result envelope, and **the player's recorded
decision**.

After a relevant edit — determined by comparing `deck_sha256`, since it covers
the list and nothing else (ADR-025) — the report is labelled **"Applies to an
earlier list"** and offers a rerun. It is never silently updated and never
silently shown as current.

The provenance plumbing for this is nearly free: `deck_documents.py` already
carries an event log, monotonic `revision`, `expected_revision` optimistic
concurrency, and `mutation_id` on `apply_commands`. Recording the originating
`research_turn_id` on a deck entry is one column, and it is what lets the deck
answer *"why is this card here?"* six weeks later.

### 5.7 A hard input constraint, stated up front

The simulator requires **exactly 99 library cards** plus 1–2 commanders
(`coverage.library_cards` is `const: 99`). A work-in-progress deck document of 63
cards **cannot be simulated at all**.

This is not an edge case; it is the normal state of a deck in the builder. The
"Test a variant" action is therefore disabled with a stated reason — *"a study
needs a complete 100-card list; this one has 64"* — rather than failing after the
player waits. Discovery, explanation and analysis all work on incomplete lists
and are unaffected.

---

## 6. The interface

### 6.1 Shape — resolved against the refactor's existing idioms

**Decision (2026-09-08): mobile is in scope, and the dock reuses the refactor's
`.dl-add-panel` pattern rather than inventing a new one.**

`CLAUDE.md`'s `ui_scope: "Desktop web UI ... No mobile."` is **stale**. The
refactor ships a real breakpoint at `@media (max-width: 767px)` in
`deck-lab.css` with substantial rules, plus `dl-drawer` / `dl-menu-button`,
`dl-bottom-action`, `desktop-only` opt-outs, `viewport-fit=cover` and
`env(safe-area-inset-bottom)`. R0 corrects the charter line (§7.5).

An earlier draft of this section put the dock "beside the existing
`dl-stats-rail`." **That was wrong on inspection and is corrected here**, because
the refactor's own CSS rules it out twice: the builder is a fixed two-column grid
(`grid-template-columns: minmax(0,1fr) 292px`), so a third rail has no column;
and at the mobile breakpoint `.dl-stats-rail{display:none}`, so a dock parented
to it would vanish exactly where the brief wants a full-screen view.

The refactor already contains the right pattern, twice:

| Existing | Desktop | At `max-width: 767px` |
|---|---|---|
| `.dl-add-panel` (Add cards) | 360px slide-over, `top:70px`, `translateX(-101%)` → `.open` | `top:0; width:100%; z-index:120` — **full screen** |
| `.dl-filters` (Research) | 250px sticky sidebar | bottom sheet, `max-height:84vh`, `border-radius:18px 18px 0 0` |

So:

- **Builder:** Ask is a slide-over **mirroring `.dl-add-panel` on the right**
  (`right:0`, `border-left`, `translateX(101%)` → `.open`), overlaying the stats
  rail rather than competing with it for grid space. It reuses the existing
  open/close mechanism verbatim — `classList.toggle("open", …)` plus the
  `aria-hidden` flip and focus move, as `deck-lab-builder.js` already does for
  `#add-panel`. Substantial studies expand into a full report view.
- **Mobile:** the same element, full-screen via the same breakpoint rule that
  `.dl-add-panel` already uses. The deck title stays in the panel header so deck
  context is never lost. **Not** the `.dl-filters` bottom sheet — 84vh is fine
  for a filter list and cramped for a conversation with charts.
- **Research pages:** the same dock, same full-screen mobile treatment,
  pre-bound to the current commander, active filters and evidence window.

#### Required behaviours (approved 2026-09-08)

Three are integration details that will be bugs if missed, and the fourth is the
one that reuse does **not** give you for free:

1. **Mutual exclusion with Add cards.** Both are ~360px overlays. Opening one
   closes the other; below ~1100px they are never both open. This is a shared
   open/close controller, not two independent toggles — two independent toggles
   is how you get both panels open at 900px with the deck invisible behind them.
2. **Feedback-launcher suppression.** `deck-lab.css` line 136 already hides
   `.feedback-launcher` under `:has(.dl-add-panel.open)`, `:has(.dl-filters.open)`,
   `:has(.dl-drawer.open)` and `:has(dialog[open])`. The Ask panel's selector
   joins that list, or the launcher floats over the dock.
3. **Accessible focus handling.** `deck-lab-builder.js` currently does the
   minimum for `#add-panel` — `classList.toggle("open", …)`, an `aria-hidden`
   flip, and a focus move to the search input. That is the floor, not the
   ceiling, and a conversational panel needs more than a search box does:
   - **Focus trap while open.** Tab and Shift+Tab cycle within the panel.
   - **Escape closes**, and **focus returns to the control that opened it** —
     the toolbar Ask button, the bulk-actions button, or the row action, as
     appropriate. Losing focus to `<body>` on close is the most common form of
     this bug.
   - **`aria-hidden` is not sufficient on its own.** An `aria-hidden="true"`
     subtree that is still in the tab order is reachable by keyboard and
     invisible to a screen reader simultaneously, which is worse than either.
     Pair it with `inert` on the panel when closed, or make the closed state
     genuinely unfocusable.
   - **Results are announced.** The results region is a polite live region, as
     `#table-view` already is (`aria-live="polite"`), so an answer arriving
     after a long job is not silent for a screen-reader user.
   - **Reduced motion.** The `.2s` slide inherits `deck-lab.css`'s existing
     `prefers-reduced-motion` block; confirm it does rather than assuming it.

#### Verify the mobile behaviour; do not assume one CSS rule completes it

Mirroring `.dl-add-panel` makes the full-screen treatment **cheap**, not
**automatic**. The following are known not to be covered by the mirrored rule and
must be checked on a real device or an emulated viewport before R4 is called
done:

| To verify | Why it is not free |
|---|---|
| `.dl-bottom-action` with two buttons | It currently styles a single `.dl-button` at `width:100%`. Adding Ask beside Add cards needs a two-up rule, or the second button wraps |
| Panel height under the mobile keyboard | A conversation has a text input at the bottom. `100vh` and the on-screen keyboard interact badly on iOS; `dvh` or a visual-viewport listener may be needed. The Add cards panel never had a bottom-anchored input, so this path is untested |
| Safe-area insets | `.dl-bottom-action` already uses `env(safe-area-inset-bottom)`; the dock's own composer needs the same or it sits under the home indicator |
| Scroll containment | The panel scrolls its transcript; the page behind must not scroll with it |
| `.dl-bulk-controls` is `desktop-only` | "Ask about selection" has no mobile home in the current markup — §6.2 routes it to the row action sheet, which is new work, not a mirrored rule |
| The 767px boundary itself | A 768–1100px tablet gets the desktop overlay *and* rule 1's mutual exclusion. That band is the one nobody looks at |

Treat the mirrored CSS as the starting point that removes the layout work, and
budget explicit device verification inside R4 rather than after it.

### 6.2 Discoverable starting actions

**Find cards · Explain selection · Analyze deck · Compare versions**

These are buttons. Each has an identical conversational form. Where they live,
in the refactor's existing furniture:

| Entry point | Placement |
|---|---|
| Builder, desktop | An **Ask** button in `.dl-builder-toolbar`, beside `Add cards` |
| Builder, mobile | A second button in `.dl-bottom-action` — which today holds one full-width button and needs a two-up rule |
| Card selection | **Ask about selection** in `.dl-bulk-controls`, beside the existing bulk-move control. Note that group is `desktop-only`; on mobile the selection question is offered from the row action sheet instead |
| Research pages | An **Ask** button in the research toolbar, carrying the active commander, filters and window |
| Deck list | **Analyze deck**, per §6.5 |

### 6.3 Results are usable objects, not prose

| Result kind | Affordances |
|---|---|
| Card results | The builder's ordinary consider / add controls, into a chosen zone |
| Rules answers | Expandable source excerpts with CR section ids |
| Findings | Highlight the affected entries in the deck list |
| Study reports | Charts, comparison tables, the capability statement, the honesty header |
| Saved notes | The player's own recorded reason for a change |

### 6.4 Things the assistant must never require

- Copying the deck into a prompt.
- Restating the commander.
- Scrolling old messages to recover an experiment.

Long jobs survive navigation and return to the same deck (§7.3).

### 6.5 "Analyze deck" specifically

A button beside the deck; the same action available in conversation.

1. It uses **the current saved list**. No questionnaire.
2. It shows **its understanding of the deck's plan as an editable assumption**,
   at the top, before the findings. The player can correct it and re-run. This is
   the single most important element on the screen: it is where a wrong analysis
   becomes visibly wrong instead of confidently wrong.
3. It returns **~3 high-value findings**. Each answers, in this order:
   1. What did you find?
   2. Why does it matter *here*?
   3. What evidence supports it?
   4. What can I do next?
4. Next-step actions: **Show affected cards · Find alternatives · Inspect
   interaction · Test a variant**.

Two prohibitions, both of which are how this feature fails:

- **Analysis is not an obligation to find fault.** *"I couldn't establish a
  problem from the available evidence"* is a valid and shippable result. The eval
  set includes healthy decks precisely to measure whether the assistant
  manufactures findings on them.
- **Do not restate the sidebar.** Curve, colour counts and type distribution are
  already on screen. A finding that only reports one of them is spam and is
  counted as such in the usefulness rig.

For a supported list, a goldfish study is attached. For an unsupported list, the
research analysis completes in full and names **precisely which experiment is
unavailable and why** — which is a `CapabilityStatement`, not an error.

---

## 7. Architecture

### 7.1 Ownership

| Owner | Responsibility |
|---|---|
| **Deck Lab** | Context, permissions, jobs, saved reports, all UI |
| **The model** | Translate questions into bounded tool operations; explain their outputs |
| **The simulator** | Its own calculations, its own honesty fields |
| **The solver** | Its own calculations, its own declared field and expiry |

The model plans and narrates. It does not retrieve, rank, score, or compute.

### 7.2 Packages

```
src/sabermetrics/
  mechanics/    pure functions: card text -> mechanic tags. stdlib + re only.
  substrate/    the indexed knowledge layer. reads mtg_v1, writes local indexes.
  assistant/    query IR, executor, planner, narrator, threads, eval harness.
```

Import rules, asserted by a test over the import graph in the style of the
existing `cedh_no_legacy_ingestion` check, adopted from the plan §3.1 unchanged.
`assistant/` sits **beside** `cedh/`, borrowing `model_gateway`, `cost_ledger` and
`simulator`; `cedh/` may not import `assistant/`.

Module promotion from `analytics/` (plan §3.2) is adopted with its completed
audit: four pure modules move with deprecated re-export shims. `role_tagger.py`
stays behind because it reads configuration and performs scored classification;
`cvar.py`,
`empirical_valuation.py`, `card_win_equity.py`, `cluster_valuation.py` and
`synergy_matrix.py` stay behind because they encode the budget/casual objective.

### 7.3 Jobs

Reuse the existing `BuildJobsRepo` + `ThreadPoolExecutor` pattern from
`cedh_routes.py` verbatim. No broker, no SSE, no second process — *locality over
distribution*.

- `research_thread` (owner, title, optional `deck_id`, unread flag)
- `research_turn` (role, content, structured payload, citations, cost)
- `research_task` (status: `queued → clarifying → planning → searching →
  simulating → writing → done | failed`, progress notes, result)

**Progress notes are the product, not decoration.** *"searching corpus: phyrexian
mana in cost… 23 found… profiling 42 recorded Vivi lists…"* makes a wait legible
and makes a wrong answer traceable to the wrong query.

One in-flight task per user; a global queue-depth cap; visible queue position.

**Two different constraints bind at two different phases, and an earlier draft
conflated them.** They have different fixes, so they are separated here:

| Phase | What binds | Why |
|---|---|---|
| **R4–R5** | The web tier: one `shared-cpu-1x` machine running Flask, SQLite, a CPU embedding model **and** a cross-encoder reranker in-process | The reranker is the expensive resident. Nothing in R4 touches the simulator, because `sim_study` does not exist until R6 |
| **R6+** | `SIM_MAX_CONCURRENT=1` on the interactive simulator tier; excess receives 429 | One study in flight across all users, on a separate machine |

The R6 constraint is the harder one and it is a **queue** problem, not a sizing
problem — which is why studies are async jobs with a visible queue position from
their first commit (§5.4). The R4 constraint is a **memory and CPU residency**
problem on the web tier, and it is what sizes the pilot audience (§9, R4).

**`SIM_MAX_CONCURRENT=1` is not evidence that the R4–R5 residency constraint is
addressed.** It is a limit on a different machine, on a workload that does not
exist until R6. Serializing simulations says nothing about whether a
cross-encoder and an embedding model fit in a `shared-cpu-1x` alongside Flask and
SQLite while three testers ask questions. The residency question is answered by
measuring resident memory and p95 latency for a concurrent question on the web
tier — which is R4 acceptance work, not something inherited from the simulator's
configuration.

A killed worker leaves a task recoverable, not lying.

### 7.4 Caching and budget

Plan and result cached on `(clarified_intent_hash, corpus_snapshot_hash,
tag_version, prompt_version)`, extending the gateway's existing `cache_key`, which
deliberately excludes the model id so a model swap is visible rather than hidden.
A repeated question is free.

Budget: a per-user monthly research-question cap, configurable and not surfaced
as a number, **alongside** the existing global
`settings.llm.monthly_cost_ceiling_usd` hard stop, which remains the only hard
stop (ADR-024). Research questions are an unbounded surface in a way manual deck
builds are not.

### 7.5 Stale charter lines, to fix in R0

Five lines of record are stale and each would mislead someone implementing
against this spec. All are corrected in R0. (An earlier draft of this section
listed three and named one of them wrongly; the audit that produced the table
below checked each against the code.)

| Stale line | Reality | Why it matters here |
|---|---|---|
| `CLAUDE.md` `cloud_alignment`: "simulator over private HTTP with **cedh-simulation-result.v2** validation" | `cedh/simulator.py` pins **v3** and vendors `contracts/cedh-simulation-result.v3.schema.json` | §5.1's capability statement reads `coverage.inert_by_reason`, which exists in v3 and **not** in v2. A reader of the stale line would conclude the field is unavailable and design around its absence |
| `cedh/simulator.py:223` `_parse_http_result` docstring: "Validate and adapt a **v2** service document" | The function validates v3 | Same, at the point of use. **Correction:** an earlier draft called this function `_adapt`; no such symbol exists in the repo |
| `cedh/simulator.py:119` `SimulationResult` class docstring: "A validated `cedh-simulation-result.**v1**` document" | Its own `schema_id` literal nine lines below says v3 | A fourth stale string, missed by the earlier draft. A class whose docstring and its own field literal disagree is worse than either being wrong alone |
| `CLAUDE.md:418`: "Full text for **ADR-001..027** is in `design.md` Section 11" | `design.md` §11 holds **ADR-001..014 only**. ADR-015..027 exist solely as one-line rows in CLAUDE.md's own table | Anyone following the pointer to read the rationale for a settled decision finds nothing. Worse: `docs/project_plan/sabermetrics_v2_spec.md` §3 defines its **own** ADR-015..020 for entirely different decisions, so the numbers collide |
| `CLAUDE.md` `ui_scope`: "Desktop web UI ... **No mobile.**" | The refactor ships `@media (max-width: 767px)` with substantial rules, a mobile drawer, `dl-bottom-action`, `desktop-only` opt-outs and safe-area insets | §6.1 resolves mobile as in scope. Leaving the charter contradicting the shipped CSS means the next person to read it builds the wrong thing, or reverts the right one |

Correcting a charter line is a deliberate act, not housekeeping: the `ui_scope`
edit is recorded with its reason so it reads as the refactor's decision being
written down, not as scope quietly widening.

---

## 8. What to preserve from the legacy reasoning

The most valuable thing in `bristly_billy_beane`'s reasoning layer is its ability
to explain **why a mechanic matters in this particular deck**. Concretely,
`reasoning/prompts/profile_synthesis.txt` already distinguishes:

- **Engine vs. dependent output** — cards that *feed* an engine from cards that
  merely *resemble its outputs*. Its worked example (a lifelink creature drains
  life but does not increase Aura count, so it is a **false synergy**) is exactly
  the reasoning the assistant needs and exactly what "find more draw" cannot
  express.
- **Value inversion** — commander abilities that change normal card evaluation,
  typed as stat / keyword / cost / quantity inversions, each with
  `desired_characteristics` **and** `undesired_characteristics`.
- **Mechanical support vs. superficial text similarity.**
- **Strategic dependencies and conflicts** — including anti-synergies among
  popular cards.

That vocabulary becomes the assistant's **vocabulary for investigation**. It is
the difference between:

> "Find more draw."

and

> "Find draw that works with my casting restrictions and doesn't depend on
> opponents."

### 8.1 Reuse, and what must be stripped

**Reuse:** mechanic detection (`effective_cost.py`, `oracle_keywords.py`,
`oracle_patterns.py`, `theme_patterns.py`, `role_tagger.py`), the retrieval and
`reference_layer/` infrastructure (built, idle, and exactly right for the rules
category), the strategic concepts above, and the evidence infrastructure in
`cedh/evidence.py`.

**Audit and rewrite the prompts for research.** They are generator prompts and
carry generator assumptions that must not survive the move:

| In the legacy prompts | Disposition |
|---|---|
| `card_fit.txt`'s `fit_score` 1–10 and its calibration rubric | **Removed.** A numeric fit score is a ranking, and ranking is deterministic and not the model's job. Interpretation-tier prose (§4.2) replaces it. |
| `Price: ${price}` and `Average Deck Price` | **Removed.** ADR-025, no exceptions. |
| WotC bracket power level 1–5 | **Removed.** ADR-019: cEDH only. |
| `cwe_score`, `cooccurrence_avg` | **Removed** from the assistant. These are the casual-objective valuations §7.2 leaves behind. |
| EDHREC inclusion % as a bare number | **Replaced** by Field-evidence-tier assertions with required denominator, window and event-size floor. |
| Value inversions, engine dependencies, false-synergy warnings, anti-synergies | **Kept**, restructured into typed Interpretation assertions with `does_not_establish` required. |

**Keep outside the assistant:** the generator's selection loop, budget
optimization, and all numerical card-fit scoring.

---

## 9. Phases

Sizes are order-of-magnitude for one person working with an agent, and are the
least reliable numbers in this document.

```
R0 ──▶ R1 ──▶ R2 ──▶ R3 ──▶ R4 ──▶ R5 ──▶ R6 ──▶ R7 ──▶ R8 ──▶ R9
1w    1w    1.5w   1.5w   1.5w    2w    1.5w    2w     2w    (exp)
             G1     G2     G3+G4
                     ▲      ▲
              the gate that  first release,
              matters        in players' hands (~wk 6.5)
```

### R0 — Foundations, both measuring rigs, ADRs · ~1 week

1. Package skeletons; import-graph and `mechanics/`-purity tests from day one.
2. Promote the five modules with deprecated re-export shims.
3. **Golden question set** (`assistant/eval/questions/*.yaml`), ~80 at R0 growing
   to ~150, each carrying the ask, a clarified variant, `required_oracle_ids`,
   `forbidden_oracle_ids`, `expected_absences`, category, difficulty, **and the
   labeller's identity and date**. Categories per the plan §4/A0, plus a new
   **healthy-deck** category (§6.5) that must produce no manufactured finding.
4. **Both rigs stood up** (§10), reporting an all-zeros scorecard.
5. Fix the three stale charter lines (§7.5): the two v2/v3 references, and
   `CLAUDE.md`'s `ui_scope`.
6. **ADR-029** (Interpretation tier is permitted; §4.3) and **ADR-030**
   (assistant sits beside `cedh/`; query-planner rule; `assistant_may` /
   `assistant_may_not`).
7. **Propagate the naming decision** (§1.1) before any label exists to migrate:
   the package is `assistant/`, the player-facing word is **Ask**, and the
   refactor's Research / Build nav is untouched.
8. **Fix D0a, the missing-absence bug** (§14.1) — warnings on the
   `gateway is None` early returns in `lab.py:_summarise_evidence` and `_explain`;
   persisted build-time warnings passed to `candidate.html`; an `{% else %}` on
   the explanation block. The candidate page deliberately does not reconstruct
   current `modes`: those could misdescribe a historical build after provisioning
   changes.
   This is in R0 rather than R4 because the assistant inherits the pattern, and
   building R4 on a silent-absence precedent propagates it.

**Acceptance:** tests green; legacy imports unbroken; golden set validates; both
rigs run and report zeros; **with no model credential set, a candidate page states
the absence of a narrative rather than omitting the section** — asserted by a
test, since that is the invariant R4 depends on.

#### R0 definition of done

Every line is a command that exits 0 or non-zero. **`python scripts/check_r0.py`
runs all of them and is the single gate**; `--verbose` prints each step. A box is
ticked only when that command passes on a clean tree.

- [x] **Regression parity.** `ruff check src tests`, `black --check src tests`,
      `mypy src`, `pytest -q` all pass, and the suite is at or above the
      pre-R0 baseline of **1288 passed / 31 skipped**. The count is pinned in
      `scripts/check_r0.py`, so a test deleted to make the gate pass fails it.
- [x] **D0a — absence is visible.** `pytest -q tests/test_cedh_lab.py -k AbsenceOfAModel`
      and the two `test_cedh_ui.py` absence tests pass. Verified by reverting the
      fix and confirming all five fail.
- [x] **Package boundaries.** `pytest -q tests/test_package_boundaries.py` — 16
      tests, including four evasion cases (aliased submodule, dynamic
      `import_module`, `__import__`, lazy in-function import) and a
      false-positive guard on `pipelines_new`.
- [x] **`mechanics/` is pure.** Asserted three ways: stdlib-only imports, no
      `sabermetrics` import, and no filesystem call anywhere in the package.
- [x] **Promotion without breakage.** Four modules promoted, shims at every old
      path, and **no consumer file edited**. The suite proves it: 15 files import
      these modules and none was touched.
- [x] **Stale charter lines corrected.** `pytest -q tests/test_research_assistant_charter.py`
      binds each doc claim to the code that decides it, rather than to a literal
      string, so the next drift fails rather than rots.
- [x] **Golden question set.** 80 questions load and validate against a
      schema, with labeller and date recorded per question.
- [x] **Eval rigs.** `python -m sabermetrics.assistant.eval --mode substrate`
      emits a scorecard whose quality metrics are `0.0` and whose invariants read
      `not_measured` over zero assertions — **not** 100% coverage over nothing,
      which would be an invented number.

**R0 is complete as of 2026-09-08; `python scripts/check_r0.py` is the evidence.**
Two things about this checklist are deliberate:

**One task here cannot be made machine-checkable, and pretending otherwise would
be the worst outcome.** A golden question's `required_oracle_ids` are a human
judgement about what a good answer contains. The schema can check the field
exists, is a list, and resolves to real cards; it cannot check the judgement. The
honest substitute is the one §10.3 already states — record who labelled each
question and when, carry a `contested` flag, and treat a contested label as
data rather than as something to resolve silently.

**The gate asserts a floor, not equality.** `pytest` must report *at least* 1288
passed; adding tests is progress, and pinning equality would punish it. Skips are
pinned at exactly 31, because a test that starts skipping is a test that stopped
running and that is precisely the failure a count is for.

### R1 — Mechanic tags, first two families · ~1 week · no LLM

Narrowed from the plan's five families so the pilot can happen. Ship `cost:*` and
`mana:*` complete; the remaining families (`draw:*`, `interact:*`, `win:*`)
continue in the background and land through R2–R5.

Tag format, precision discipline (≥0.95 on fixtures, ≥20 hand-labelled fixtures,
a **required non-empty `limitations` field**), `matched_span` non-optional, and
the coverage report of untagged cards grouped by type line: all adopted from the
plan §4/A1 unchanged.

**Acceptance:** ≥25 tags shipped across two families; every tag ≥0.95 precision;
coverage report printed; rebuild deterministic and content-hashed.

#### R1 definition of done

Every line is a command that exits 0 or non-zero. **`python scripts/check_r1.py`
runs all of them and is the single gate**; `--verbose` prints each step, and
`--skip-r0` omits re-running R0 first. R1 sits on top of R0, so the gate runs the
R0 gate before its own — a green R1 over a red R0 measures the wrong thing.

- [x] **Regression parity.** `ruff check src tests`, `black --check src tests`,
      `mypy src`, `pytest -q`, at or above the post-R0 floor of **1410 passed /
      31 skipped**, pinned in `scripts/check_r1.py`.
- [x] **≥25 tags across two complete families.** Read from the shipped registry,
      not from a file count, and both `cost:*` and `mana:*` must be non-empty.
- [x] **Every tag ≥0.95 precision on real oracle text.**
      `sabermetrics tags verify` recomputes precision and recall from
      `fixtures/mechanics/tag_cards.json` and fails on any gap between the figure
      a definition **declares** and the figure it **measures**.
- [x] **Coverage report.** `sabermetrics tags coverage` prints the untagged set
      grouped by card type — the analogue of the simulator's inert table.
- [x] **Deterministic and content-hashed.** Two dry-run builds of the same
      library against the same snapshot must print the same `content` hash.

**R1 is complete as of 2026-09-09; `python scripts/check_r1.py` is the
evidence.** The shipped registry contains 28 tags (17 cost, 11 mana). Its 581
hand-labelled fixture assignments cover 175 unique cards with real oracle text;
all 28 definitions measured 1.0 precision and 1.0 recall on those fixtures. A
full 34,551-card development snapshot produced 6,664 tag rows across 4,583 cards
and reported the remaining 29,968 cards as untagged. The completion run reported
1,410 passed and 31 skipped tests.

Seven decisions were taken while building this and each would otherwise have to
be rediscovered from the code:

**Tag definitions are Python data in `mechanics/`, not YAML in `config/`.**
`mechanics/` may not touch the filesystem, which is the rule that kept
`role_tagger` out of it, so a YAML tag library would have had to live in
`substrate/` and the predicates would have separated from the family that owns
them. The definitions are frozen dataclasses instead: one file per family, as the
plan asks, checked in and versioned.

**The purity rule was narrowed, on purpose, to let `mechanics/` import itself.**
R0 shipped the package as four independent modules, so "imports nothing from
`sabermetrics`" and "imports nothing from a sibling package" were the same rule
and were written as the former. One predicate algebra shared by two family files
makes them different rules, and the one always meant is the latter. The exemption
is segment-bounded and its own test proves it does not spare a
`sabermetrics.mechanics_v2`; the three invariants the rule exists for — no sibling
package, no third-party import, no filesystem — are unchanged.

**`confidence` is measured fixture precision, and it is named for that.** It is
not a per-card probability that a tag is correct. A definition declares the
number; `tests/test_mechanic_tags.py` recomputes it from the fixture cards and
fails on drift, so it cannot become a claim nobody checks.

**`matched_span` is non-optional structurally, not by convention.**
`always_yields_span` walks the predicate at construction and refuses any tag that
could match without producing a span — a bare mana-value bound, or a lone
negation. A tag row that asserts a mechanic with nothing to point at is an
unattributable claim, which is what a tag exists not to be.

**Reminder text is masked before matching, and masking preserves offsets.** The
cascade reminder contains "without paying its mana cost"; the convoke reminder
contains "help cast this spell". Parenthesised runs are overwritten with spaces
rather than deleted, so a span taken from masked text still indexes the real
printed text a player would see.

**Tags stay atomic; compositions belong to the query layer.** The plan asks for
"mana rock (net-positive vs. net-neutral)". That comparison is arithmetic between
a printed cost and a printed production, and baking it into a tag would put a
derived judgement inside `mechanics/`. It ships instead as
`mana:adds_two_or_more` joined with a mana-value filter at query time, and both
tags' `limitations` say so, so the composition reads as intended rather than
missing.

**Near-miss quality of negative fixtures is a human judgement and is not faked.**
Whether a given negative is a near miss for a given predicate is the same kind of
claim as a golden question's `required_oracle_ids`, which §10.3 is explicit about
not machine-checking. The suite checks the shape of the pool as a whole — that
most negative fixtures carry some shipped tag, so the pool is adjacent cards
rather than vanilla filler — and leaves the per-tag judgement with the author,
recorded in each tag's `limitations`.

### R2 — Retrieval substrate · ~1.5 weeks · no LLM · **GATE G1**

Lexical (SQLite FTS5, replacing the current `LIKE '%q%'` in `research.py`),
structured (pushed down to SQL), dense (`bge-small-en-v1.5`, numpy memmap, no
vector DB), and a local cross-encoder rerank over the fused top ~200. RRF fusion
with weights in config, tuned against the golden set.

The reranker is the single most important substitution of local ML for LLM
capability in this design: it is the reranking the model would otherwise be asked
to do, done better, deterministically, and for free.

**G1:** recall@50 per retriever and fused over `required_oracle_ids`. Target
≥0.90 fused. **Below 0.80: stop and fix the substrate.** No model fixes
retrieval.

#### R2 implementation contract

**One atomic source observation produces one immutable bundle.** A production
build reads snapshot identity, `card_any_medium`, `card_face`, and Commander
legality on one Postgres connection in one read-only, repeatable-read
transaction. Every statement passes `assert_v1_only`; no statement names
`mtg_internal`, and the narrow `mtg_v1.card` view is never used. A canonical
corpus hash covers card, face, and legality facts but excludes capture time.
The same frozen rows feed the mechanic-tag build, FTS catalog, and dense
encoder; none may reopen Postgres independently.

A bundle is a content-addressed directory beneath the configured artifact root:

```
<root>/
  CURRENT
  <bundle_sha256>/
    manifest.json
    catalog.sqlite
    card-vectors.npy
```

`catalog.sqlite` is standalone and read-only at query time. It contains one
row per Oracle card, normalized type and tag relations, Commander legality,
deterministic row/vector offsets ordered by `oracle_id`, and FTS5 over the
versioned canonical card document (name, type line, mana cost, all face text).
`card-vectors.npy` is normalized float32 and opened as a numpy memmap. The
manifest binds the corpus hash, tag-library and tag-row hashes, document
template version, retrieval-config hash, exact embedding/reranker model IDs and
40-character revisions, dimensions, dtype, normalization, and every artifact
file digest. Capture/build timestamps are provenance, not bundle identity.

Builds write a sibling scratch directory, validate SQLite integrity, vector
shape/finiteness/norms, manifest identity, and every file digest, then rename
the completed directory. `CURRENT` changes by `os.replace` only after that
validation. A failed build leaves the old pointer untouched. A reader validates
the pointer and manifest before opening any file; an absent or corrupt bundle is
an explicit unavailable result, never an empty search and never a fallthrough
to the legacy card table.

**The retrieval facade is the only card-search entry point.** It accepts the
strict `CardSearchQuery`, translates it to parameterized catalog predicates,
and executes this fixed sequence:

1. SQL computes the eligible Oracle-ID set for color identity (`subset`,
   `exact`, or `intersects`), required/excluded types and mechanic tags,
   any-of mechanic tags, mana value bounds, Commander legality, and an optional
   caller allowlist.
2. FTS5 and dense search each rank only that same eligible set, with bounded
   pools from `config/research.yaml`. Plain text is compiled to quoted FTS
   tokens; raw FTS syntax is never accepted.
3. Weighted RRF fuses lexical and dense ranks. Stage scores are retained as
   provenance but do not enter the RRF formula.
4. The pinned local cross-encoder reranks only the configured top pool
   (tuned to 300), in bounded batches. No model loader may contact a hub at
   query time.
5. A second configured RRF combines the pre-rerank hybrid order with the
   cross-encoder order. This is deliberate, measured guardrail behavior: the
   cross-encoder may promote a semantically strong candidate but may not erase
   strong deterministic retrieval on a disjunctive mechanic query.
6. Results are hydrated by Oracle ID from the bundle and returned with every
   stage rank/score, truncation flags, availability, and complete bundle
   provenance. Ties have an explicit final `oracle_id` ordering.

FTS/BM25 weights, RRF weights and `k`, pool sizes, result bound, model IDs,
model revisions, query prefix, dimensions, normalization, batch sizes, and
cross-encoder maximum length are configuration, not code. Model files are
provisioned separately. Missing files, wrong revisions, wrong dimensions,
non-finite vectors/scores, and zero vectors fail visibly.

**The live reference index migrates as one generation.** Inspection disproved
the earlier idle-index assumption: `main.py` exposes retrieval directly, and
the legacy profiler reaches it through `reference_layer.evidence`. R2 therefore
re-indexes reference chunks too. Reference embeddings live in generation-keyed
rows with model ID, immutable revision, document-template version, dimensions,
dtype, normalization and content hash recorded once per generation. A complete
new generation is written and validated before one singleton active-generation
pointer changes in the same SQLite transaction. The retriever reads one active
generation only and refuses stale MiniLM, mixed-model, partial, or unlabelled
rows. Reverting is another complete generation build and pointer swap.

**The existing Research card page becomes a consumer, not a second
retriever.** `ResearchRepo.cards` obtains ranked Oracle IDs from the facade.
It may hydrate image/rarity/printing fields from the legacy SQLite card table
for presentation only, preserving retrieval order; those fields cannot filter,
score, break ties, or enter the assistant contract. The old `%LIKE%` card
search is removed. An unavailable active bundle is rendered as unavailable
rather than silently returning legacy-ranked results.

#### G1 and the R2 gate

G1 labels are a separate, versioned artifact mapped from discovery questions
whose answer is card retrieval alone. Agent-drafted mappings remain `draft` and
cannot produce an authoritative score. Dylan must verify the query, every
required/forbidden Oracle ID, and the intended structured filters; the label
then records `owner_verified`, the labeller, date, and review note.

An authoritative run requires all of the following and refuses to print a G1
claim if any is absent:

- an active, validated bundle sourced from `mtg_v1.card_any_medium`, with at
  least 30,000 cards and every labelled Oracle ID resolved;
- the exact pinned local BGE embedding and reranker revisions from config;
- one observation for every owner-verified G1 label; and
- rankings at 50 for lexical, dense, pre-rerank RRF, and the final reranked
  fused pipeline.

The scorecard reports per-question and aggregate macro/micro recall, forbidden
hits, truncation, corpus/bundle/model/config/label hashes, and elapsed time.
For naming, `rrf` is the pre-rerank diagnostic and `fused` is the final
post-rerank pipeline output to which the existing ≥0.90 target applies. A fused
macro recall below 0.90 fails R2; below 0.80 additionally carries
`stop_and_fix`, because proceeding to a model planner would hide a substrate
failure rather than repair it.

`python scripts/check_r2.py` is the single completion gate. By default it
re-runs R1, lint, format, types, package-boundary tests, all portable retrieval
tests, the full suite with a passed-test floor and exact skip count, validates
the active bundle, runs authoritative G1, and writes the scorecard. A
`--portable` mode may prove code and fixture checks in CI without local model
files, but it prints **not G1 / not R2 complete** and cannot be the evidence
used to tick the boxes below.

#### R2 definition of done

- [x] Strict query/filter/result/provenance and retrieval configuration models;
      pinned model revisions; no price or collection field.
- [x] Snapshot-consistent corpus export; immutable catalog/artifact validation;
      safe FTS5, structured filters, dense memmap search, weighted RRF, bounded
      cross-encoder; portable tests use fake encoders/scorers.
- [x] One builder creates and atomically activates a complete bundle from one
      frozen corpus observation; one facade runs the whole pipeline.
- [ ] Reference chunks are rebuilt under a validated BGE generation and every
      live consumer reads only the active generation.
- [x] `ResearchRepo.cards` uses facade-ranked Oracle IDs and contains no legacy
      `%LIKE%` card-ranking path.
- [ ] All G1 labels are owner-verified; the full `mtg_v1` bundle and pinned
      local models produce a recorded authoritative fused recall@50 ≥0.90.
- [ ] `python scripts/check_r2.py` passes in authoritative mode, the R0→R1→R2
      chain stays green, and this section records the measured hashes, counts,
      recall, and completion date.

**Implementation checkpoint, 2026-09-09.** `python scripts/check_r2.py
--portable --skip-r1` passes: lint, format, types, package boundaries, 141
portable retrieval tests, and the full suite at 1,563 passed / 31 skipped. The
reference generation code and its rollback/mixed-generation tests are green,
but its checklist remains open until the real reference corpus is encoded and
activated. Both pinned local model snapshots are provisioned and
revision-attested. A real-model, 34,551-card Scryfall development bundle
(`cf24c1ef96c4174b68757203aeecdf4cf326c49382d3b00aacad779ffb7008ff`,
`card-document.v2`) measured **0.9583 fused macro recall@50**, 29/31 micro
recall, and zero forbidden hits after canonicalizing 43 synthetic fixture IDs;
this is useful tuning evidence but explicitly **not G1**. Authoritative G1 still
refuses to run, correctly: no Postgres DSN is configured in this worktree, the
active development bundle says `scryfall:oracle_cards` rather than
`mtg_v1.card_any_medium`, and all 12 labels remain explicitly `draft` pending
Dylan's review (`fixtures/research/g1_label_review.json`).

### R3 — Query IR and deterministic executor · ~1.5 weeks · no LLM · **GATE G2**

The `ResearchPlan` schema, its validator, and one handler per step kind, each
returning a provenance-bearing envelope.

R3 ships a **reduced step set**: `card_search`, `tag_filter`, `rules_lookup`,
`deck_profile`, and the set ops. `field_stats` moves to R5 because it is shaped by
the **D1** corpus census; `sim_study` moves to R6; `combo_lookup` waits on the
**D4** decision.

**Hand-write a plan for every golden question.** That is the phase's real
deliverable.

**G2:** ≥80% of golden questions answered with full `required_oracle_ids` recall
and zero `forbidden_oracle_ids`, by hand-written plans and **no model**.
**Below 70%: stop — the tool vocabulary is wrong, and a planner would only
obscure that.**

#### R3 implementation contract

**The plan is the interface, and it may not name its own answer.**
`assistant/ir.py` defines `ResearchPlan` as a frozen, `extra="forbid"`,
`schema_version`-pinned DAG over exactly seven step kinds. A kind the design
names but R3 does not implement raises `DeferredStepKindError` naming the phase
that owns it (`field_stats` → R5, `sim_study` → R6, `combo_lookup` → D4,
`deck_diff` → R5); a kind that does not exist raises `UnknownStepKindError`
naming the closed set. Those are two different refusals because they mean two
different things.

Set-operation inputs must name a step **declared earlier in the tuple**, so
dangling references and cycles are impossible by position rather than by a
graph walk. A zero-step plan is legal — 26 of the 80 golden questions need one —
but only if it declares `clarification_required` or at least one
`stated_absences` entry, so an accidentally empty plan can never read as an
answer.

The load-bearing rule is that **a plan may not contain an Oracle id anywhere**,
and `filters.allowed_oracle_ids` must be empty. Restricting a search to a deck
is `scope: "deck"`, and the executor materializes the ids from the resolved
context. Without that rule a hand-written plan could score perfect recall by
naming the answer, and G2 would measure the plan author's access to the answer
key. The complementary hazard — a card *name* in free-text query text — cannot
be refused structurally, because describing a mechanic is exactly what query
text is for; it is measured instead, and the scorecard publishes every plan
that supplies a required card name its own question does not.

**Every result envelope carries its provenance as required fields with no
defaults**, so a result cannot be constructed, and therefore cannot be
rendered, without them. `StepResult` requires `CorpusProvenance` (bundle,
corpus view, row count, corpus hash, document version, retrieval-config hash,
tag library/content hashes and tag row count), `Coverage`, a typed
`FieldEvidence`, the `RetrievalAvailability` the ranking stages reported, an
`AssertionTier`, and a `ResultOrdering`. Absence is the second arm of a union:
`StepNotRun` carries a reason from a closed set, and a set operation over a
step that did not run returns `StepNotRun` naming it rather than a set over the
survivors.

Two coverage flags are deliberately **not** merged. `truncated` means this
step's own bound dropped rows, which is normal for any ranked top-k;
`set_input_incomplete` means an input to a set operation was cut off, which is
the one that can make a set result wrong. Merging them would leave the
dangerous flag permanently on and therefore ignored. A narrowing operation
drops nothing: `intersect` returning 4 of 13 is the operation working, not
truncation, and reports `dropped = 0`.

`ordering` exists because a recall-at-k window is only meaningful over a
ranking. A `tag_filter` returns Oracle-id order, and slicing that at fifty
measures the alphabet, so the scorer evaluates a non-ranked answer over its
whole returned set and records which window each question used.

**There is no simulator facet on `deck_profile`.** Reading a saved
`cedh-simulation-result.v3` document without going through the simulator client
loses the deck-identity check and the `fixture:` version marker, so a
development fixture would render as a measurement of the deck in front of it.
`sim_study` is R6's, with the honesty envelope attached.

**Deck context.** `cedh:kinnan-basalt-fixture` and `cedh:kinnan-healthy-baseline`
resolved to nothing before R3. `assistant/context.py` resolves both by reading
`config/cedh_packs/*.yaml` as data — `cedh.packs` is not on ADR-030's borrow
list, and the duplication is cross-checked by a test that imports both readers
and asserts they agree. The healthy baseline is a **declared alias** of the same
pack, carrying `baseline_of`, because the Kinnan pack is the only authored list
and inventing a second one would not make the no-finding measurement real.
The context publishes `pack_sha256`, which identifies the *configuration*; it
deliberately publishes no deck hash, because `deck_sha256` is a cross-repository
contract pinned by golden vectors in three implementations and a fourth would
re-create the bug that contract replaced. That the two aliases name the same
100 cards is asserted directly on the sorted Oracle ids.

#### G2 and the R3 gate

The golden questions label their answers with the cEDH fixture's uuid5 ids; a
full corpus carries canonical Oracle ids. `scripts/map_deck_context_ids.py`
writes the bijection to `fixtures/research/deck_context_id_map.json` with
`status: awaiting_owner_review`, mirroring `g1_label_review.json`. Translation
back into label space is **positional**: an unmapped corpus card becomes a
`corpus:<id>` sentinel and keeps its rank, because filtering unmapped ids out
would let a card ranked 200th survive into a top-50 window and inflate recall.

`correctness_scorecard` reports the **mean** of per-question recall, which is a
different number from the one G2 asks for: a run finding three of every four
required cards scores 0.75 and answers zero questions. `assistant/eval/g2.py`
computes the count, and embeds the R0 scorecard unchanged beside it rather than
editing an R0 contract. Required and forbidden ids are checked over the **same**
window, with `forbidden_hit_anywhere` reported alongside so the fix cannot read
as a loosening.

The gate denominator is named on the scorecard, not assumed: 47 of the 80
questions carry `required_oracle_ids`, and the other 33 are listed under
`not_applicable_ids` rather than counted as free passes (`set() <= anything` is
true) or as failures (an undefined metric is not a failure). Three further
gates are reported with an explicit measurement status — `absence` and
`clarification` as `declared_not_measured`, because with no planner both read a
hand-authored declaration back out of a hand-authored artefact, and `no_finding`
as `not_measured`, because with no narrator `finding_count` is structurally zero
and 0/6 manufactured findings is arithmetic rather than evidence. Every key of
the embedded quality block carries the same status, so one document cannot
report a gate as passing and unmeasured at once.

`authoritative_g2` refuses to make a production claim on: a contested golden
set, a draft plan, an unreviewed id map, an observation set that is not exactly
one per question, a corpus that is not `mtg_v1.card_any_medium`, a corpus below
the 30,000-card floor, an unpinned local model, a label that does not resolve in
the corpus, or a `rules_lookup` with no reference index. `scripts/run_g2.py`
mirrors `run_g1.py`'s exit discipline: 0 pass, 1 measured below target, 2
refused to measure.

#### R3 definition of done

- [x] Versioned `ResearchPlan` IR with a validator that refuses unknown and
      deferred step kinds by name, dangling and forward input references, empty
      plans that declare nothing, unshipped mechanic tags, over-long queries,
      and any Oracle-id literal or `allowed_oracle_ids` entry.
- [x] Provenance-bearing envelopes whose honesty fields have no defaults;
      typed absence as the second arm of a union; truncation and
      set-input-incompleteness carried separately.
- [x] One handler per R3 step kind — `card_search`, `tag_filter`,
      `rules_lookup`, `deck_profile`, `union`, `intersect`, `difference` — with
      deck scope injected by the executor and never authored by the plan.
- [x] Both golden context ids resolve; the healthy baseline is a declared alias
      cross-checked against the pack registry.
- [x] A hand-written plan for every one of the 80 golden questions, each
      carrying author, date, `review_status`, a review note, and a rationale.
- [x] G2 metric, its named denominator, the three declared-or-unmeasured gates,
      the deferred map, and all nine authoritative refusals, with tests.
- [ ] Every hand-written plan and the id map are owner-verified, and the golden
      set is no longer `contested`.
- [ ] The reference index is built, so `rules_lookup` returns rules rather than
      a typed absence.
- [ ] `python scripts/check_r3.py` passes in authoritative mode against an
      `mtg_v1.card_any_medium` bundle, and this section records the measured
      hashes, counts, pass rate, and completion date.

**Implementation checkpoint, 2026-09-10.** `python scripts/check_r3.py
--portable --skip-r2` passes: lint, format, types, package boundaries, the R3
tests, and the full suite. All 80 hand-written plans load, cover the golden set
exactly, and name no card their question does not.

**Two golden labels were corrected on owner adjudication, and the plans did not
change.** `deck-local-010` forbade Delighted Halfling while its own
`clarified_ask` asks for non-Human creature mana sources, which it is; the
exclusion is gone and re-enumerating the deck against the ask added Enduring
Vitality and Gene Pollinator, which the draft had omitted. `metagame-006`
required Force of Will, which *defines* its cohort rather than answering it;
required is now the co-occurring zero-mana counterspells, and the question
carries a new expected absence because the co-inclusion counts backing them need
field statistics deferred to R5 — it is partially answerable, and says so. In
both cases the label moved to the plan rather than the plan to the label, which
is the only direction that keeps G2 measuring the substrate.

A **development-bundle** run then measured **47/47** on the retrieval gate
against bundle `cf24c1ef96c4174b68757203aeecdf4cf326c49382d3b00aacad779ffb7008ff`
(`scryfall:oracle_cards`, 34,551 rows, corpus `62a6198c…`), plans `23b3992e…`,
id map `46ed17e6…`, under the pinned `bge-small-en-v1.5` and `bge-reranker-base`
revisions. **It is explicitly not G2**, exactly as R2's development-bundle
0.9583 was explicitly not G1: `authoritative_g2` refuses on four independent
grounds — the golden set is still `contested`, all 80 plans are `draft`, the id
map is `awaiting_owner_review`, and the bundle is not `mtg_v1.card_any_medium`.
A measured run never stamps a gate `status` at all; it reports
`status: not_authoritative` and a separate `measured_pass_rate`.

**Creature and land subtypes are now expressible (2026-09-10).** R3 originally
shipped with the part of a type line after the dash unreadable: the catalog
carried card types only. Two documented defects followed from that one gap.
`deck-local-010` asks for **non-Human** creature mana sources and its plan could
not say "non-Human" — it passed on the Kinnan list only because every mana dork
in that list happens to be non-Human, so the restriction was vacuous by luck
rather than expressed. `deck-local-001` could not see Island, Tropical Island or
Breeding Pool at all, because a land taps for blue by virtue of its **subtype**
and that ability is printed nowhere; its plan recovered them by naming the type
line in free text, which is a workaround and not a predicate.

`card_subtype` is now a catalog relation, `CardFilters` carries
`required_subtypes` / `excluded_subtypes`, and both plans use them. The catalog
schema is `retrieval-catalog.v2`; a v1 bundle is refused by version rather than
read as though a subtype filter matched nothing, and the development bundle was
rebuilt (`c9174872…`, same corpus hash `62a6198c…`, since the card facts did not
change). The acceptance condition is `tests/test_research_subtypes.py`, which
builds a deck containing a **Human mana dork whose printed ability is identical
to Llanowar Elves** — same tags, same types, same mana value, same oracle text,
differing only in subtype — runs deck-local-010's plan shape over it, and
asserts the Human is dropped while all four qualifying non-Humans are kept. A
companion test runs the same plan *without* the subtype filter and asserts the
Human comes back, so the filter is demonstrably load-bearing rather than
decorative.

**A perfect rate is the least informative number on the scorecard, so four
things are published beside it.**

- **Discovery is scored separately.** 16 of the 47 questions print every
  required card's name in their own ask, so finding them is a lookup the asker
  requested. `gates.discovery` re-scores the remaining 31 — the questions that
  actually require finding a card nobody named — and reports 31/31. The two
  rates can diverge, and a test asserts they do when the underlying results
  differ.
- **Breadth is published per question.** `gates.retrieval.breadth` reports
  returned rows per required card. No threshold is applied, because where "too
  wide" begins is a judgement; the ratios are published and the widest are
  named so a reader makes it. The widest answers were then **audited** — see
  `docs/r3-breadth-audit.md`. Median breadth is now 3.0 and the maximum 27.5,
  down from 50.0 in both, with recall unchanged at 47/47.
- **An unranked answer larger than the recall window counts as a failure.** A
  set with no best-first order cannot be truncated to a top k, so scoring one
  whole let a deck-dump plan pass 14 of 47 questions without retrieving
  anything. Three plans failed under the rule and were narrowed rather than the
  rule relaxed.
- **Three gates are not the measurement they appear to be.** `absence` and
  `clarification` are `declared_not_measured` — a person authored both sides —
  and `no_finding` is `not_measured`, because R3 has no narrator and a
  manufactured-finding rate of zero over zero findings is arithmetic.
  `rules_lookup` reports `step_availability: false` throughout, which
  demonstrates fallback behaviour and not rules retrieval.

**The widest answers were audited, and it found more than width
(2026-09-10).** `docs/r3-breadth-audit.md` judged 15 of the 47 scored questions
— the widest — against their asks rather than against their labels. **None of
the fifteen had a width justified by its ask.** Two classes of finding came out
of it, and they are different kinds of work:

*Plan problems, now fixed.* The `top_k=50` default was authoring the answer in
eleven plans that never set a bound. Bounds are now derived per question from
the shape of its answer — a named card gets 3, a question with printed
comparanda gets 3 to 5, a population known to be small under its filters gets
its size. Two query texts recruited their own noise and were cut. One plan was
missing the colour filter every sibling carries. Two plans now exclude
`mana:land_to_battlefield`, which is exactly the fetchlands and is derived from
asks that say "creature". `mechanic-006` became a `tag_filter` over a new
`cost:transmute` tag: it had the one correct card at rank 5 of 2,892 behind four
tutor-flavoured cards with no transmute at all, so the ranker was not carrying
it and the question is a population, not a ranking.

*One finding worth more than the rest.* `mechanic-005`'s query asked for "a
Simic instant **with convoke** that searches the library for a creature card".
Convoke is a property only the labelled answer has — answer-fitting through a
property rather than through a name, which the plan-naming check cannot see.
With it, Chord of Calling ranks **1** of 1,429 eligible. Removing it and
describing the mechanic plainly, it ranks **8**; on a wordier accurate
description, **18**. That question's earlier pass was an artefact. It still
passes, honestly, at a bound of 10 — and the substrate ranking the canonical
answer eighth on an accurate description of what it does is a retrieval result
worth knowing before anyone reads 47/47 as a statement about retrieval.

*Label gaps, which are the owner's.* The audit found **five of fifteen** audited
questions have a short or mis-scoped label, and two draft review notes assert
something factually wrong about a card. They cluster in exactly the four
questions where retrieval is doing real work. Section A of the audit lists them;
they are not fixed here, because a label is a judgement and correcting one to
match a plan is the direction that makes G2 meaningless. One of them —
`mechanic-005`'s missing **March of Burgeoning Life**, which qualifies and was
never returned — is a recall miss **the gate is structurally unable to see**,
because recall is scored against a label written by the same authorship as the
plans.

**Owner adjudications, and the corrected baseline (2026-09-10).** Nine rulings
are recorded in `fixtures/research/adjudications.yaml` as adjudication set
`2026-09-10.a`, which every scorecard now names. The set explicitly **does not
ratify** the golden set: every question stays `contested` and every plan stays
`draft`.

Two of the rulings changed the metric rather than a label, and both were
changes the labels alone could not express:

- **A singular request is not a demand for exhaustive coverage.** "Find a tutor"
  is answered by any one qualifying card, so `mechanic-005` carries
  `satisfied_by_any_of` rather than `required_oracle_ids`. Turning alternatives
  into mandates would score the difference between "an option" and "all options"
  as a failure. Coverage is still measured — `alternative_coverage` reports 2 of
  3 — and exhaustive recall moved to its own question, `mechanic-013`, where
  completeness *is* the ask and a miss *is* a failure.
- **A question whose evidence does not exist yet leaves the denominator.**
  `metagame-010` asks which lands "appear most often", which is a frequency
  claim; membership and frequency are separate evaluations and only the first is
  available. It is preserved as written and reported `unscored_pending: R5`,
  with the denominator change stated rather than absorbed.

**The corrected baseline is 47/48 = 0.9792, and the point of it is the one that
fails.** `deck-local-009` misses Invasion of Ikoria. Its bounds were set on
2026-09-10 after inspecting where the *then*-required cards ranked; the label
gained a card afterwards, and a bound fitted to one answer key did not survive
the correction. It is left failing. Widening it now would be fitting the plan to
the answer a second time, and the failure is more informative than the pass
would be: **bounds chosen after looking at answer ranks are development tuning,
and only questions written before their answers are known can show whether they
generalise.** `mechanic-013` is the first such question, and it passes with
March of Burgeoning Life at rank 29 — which also shows that `mechanic-005`'s
bound of 10, not the substrate, is what hides that card.

`combo-004` gained a second kind of label: `counterexample_oracle_ids`. Invasion
of Ikoria and Dizzy Spell are plausible traps that a restriction check must
reject — Ikoria searches for a **non-Human** creature and Kinnan is a Human
Druid; Dizzy Spell's transmute searches at its own mana value of one. They are
recorded rather than forbidden, because retrieving a trap is acceptable and only
presenting one as an answer is not. No R3 step performs that check:
`CardFilters` cannot express a second-order fetch restriction, so it is
narration work and R3 does not do it.

A **face-text regression check** (`tests/test_research_face_text.py`) now pins a
substrate asymmetry that has already misled two readers. 1,243 of the 34,551
corpus rows publish no card-level `oracle_text`, and for **891** of them the
text is there on the faces; the other 352 are genuinely textless. Anything
reading that column directly reads a blank card for all 891. Retrieval does not:
the canonical document carries face text, and so does the tag build. An earlier
audit inferred from the blank column that Invasion of Ikoria "ranked on name and
type line alone"; that inference was **wrong**, and both halves are now
asserted so it cannot recur. The asymmetry also had exactly one live consumer on
the Ask path — `RetrievalHit.oracle_text`, which a reader and later a narrator
see — and it now reads the face-aware `CatalogRecord.canonical_oracle_text`. The
rest of the audit came back clean: `cedh/` reads no card text at all, and legacy
ingestion flattens faces into its column at write time.

#### The reference index, and what having one does not establish

`scripts/provision_rules_source.py` fetches the Comprehensive Rules **once**, as
a source artefact: one pinned URL rather than a fallback list, the exact bytes
the publisher served, and a `source.json` recording the URL, the HTTP status,
the retrieval timestamp, the byte count, the content hash, and the effective
date **read from the document's own text**. That last distinction is not
pedantry — the file named `MagicCompRules 20260819.txt` states an effective date
of **2026-08-07**, so a version inferred from the URL would have been wrong. A
`--check-current` flag reports whether the publisher lists a different file and
deliberately does **not** follow it: discovering a new version and silently
indexing it would make the corpus depend on the day the build ran.

`scripts/build_rules_index.py` builds offline from that artefact and refuses
unless the bytes still hash to what was fetched. Three defects surfaced while
building, each of which would have produced an index nobody could reproduce:

- **The archive did not match its own hash.** Writing a decoded string in text
  mode keeps the document's CRLFs; reading it back through universal newlines
  collapses them. The served bytes are now archived verbatim and the
  normalisation the parser needs is an explicit derived file with its own hash,
  so what was hashed and what was parsed are both recorded.
- **The table of contents was being chunked.** It lists every section by title
  in the same `NNN. Title` form the body uses, so the parser emitted 147 chunks
  whose entire content was a section title — and a one-word chunk beats real
  rules text on a one-word query. It was also the source of the corpus's only
  content-addressed id collision. The boundary is now structural, not a length
  threshold: the contents contains no numbered rule line and the body begins
  with one.
- **A rebuild left the superseded chunks live.** `index_chunks` merges into the
  standing corpus and never deletes, which is right for adding a document and
  wrong for rebuilding one — the stale table-of-contents chunks were re-embedded
  into the new generation and kept winning queries after the parser stopped
  emitting them.

Rebuilding now reproduces the generation id, the chunk ids and the manifest
byte-for-byte. The manifest is checked in at `fixtures/research/rules_index.json`
so the repository states which rules document the Ask path answers from, and the
scorecard names it by effective date and source hash. `run_g2.py` refuses if the
active generation is not the one the manifest describes, chunked runs refuse if
two chunks consulted different generations, and `authoritative_g2` refuses
outright if a plan asks for a `rules_lookup` and no index is bound — without one
those ten questions measure fallback behaviour rather than the plan.

Binding the index immediately falsified something. Every rules plan also stated
`rules_index_not_built`, which was true and stopped being true; the runner grants
a stated absence only to a plan whose run actually failed that way, so the
declaration was refused the moment the lookup succeeded. The declarations are
gone. On a machine with no index the typed absence still reaches the envelope
from the run, which is the only thing that can know.

**What the index does not establish.** `gates.rules_support` reports what is
observable — how many passages each rules question retrieved, which sections
they came from, and whether every passage carried its citation and document. It
reports `not_measured` for the thing that actually matters: no question labels
which rules sections answer it, so whether a returned passage *supports* the
answer is a judgement nobody has made. An index is a prerequisite for answering
a rules question, not an answer to one.

#### Frozen baselines

`fixtures/research/baseline/` holds preserved results, each carrying an
`evaluation_inputs_sha256` over everything that can move a number — required and
alternative and counterexample labels, pending markers, the plan IR — and
nothing that cannot. Review notes and rationales are excluded on purpose: a hash
that broke on prose would be re-frozen reflexively and would stop meaning
anything. A file is named for its adjudication set **and** its input hash,
because those are different axes; the day the rules index landed, ten plans
changed without any label moving, and one file per set would have overwritten
the earlier measurement with no record that it described a different system.
`tests/test_research_baseline_freeze.py` fails when the working tree has no
frozen result, which is the honest form of the staleness check: older baselines
stay true about the inputs they name, and what must not happen is the current
tree having no preserved number at all.

Freezing also exposed a **wrong denominator**. Applicability in both the
retrieval and composite gates read only `required_oracle_ids`, which since the
adjudications is the wrong question: `mechanic-005` is scored through
`satisfied_by_any_of` and sat in *both* the applicable and not-applicable lists,
while `metagame-010` is scoreable but pending and sat in *neither*. The two
counts still summed to 82. Both gates now partition into scored / pending /
nothing-to-score and publish the sum so the arithmetic can be checked rather
than trusted, and `metagame-010` is no longer silently counted as a composite
pass.

The combo-004 ruling also became mechanical rather than documentary.
`CorrectnessObservation` gained `recommended_oracle_ids`, and
`gates.counterexample` scores **that** field and never `returned_oracle_ids` —
retrieving Invasion of Ikoria is acceptable, recommending it is the defect, and
the retrieved count is reported beside the gate so the two cannot be conflated.
The field is `None` rather than empty in R3, because no narrator runs and a
clean zero would be arithmetic; the gate reports `not_measured` and lists
combo-004 as unmeasured rather than passed.

`scripts/run_g2.py --chunk N` executes the plans in sequential subprocesses so
the reranker's working set is released between chunks; the parent never opens a
facade. The chunks are execution only. Every chunk must report the same bundle
identity, the union of their question ids must be the whole set with no
duplicate, and the scorecard is computed **once** over the merged observations —
chunk scores are never averaged, because G2 counts questions and a mean of
per-chunk rates is a different number. Verified equivalent to the unchunked
merge on every correctness field.

### R4 — UI pilot · ~1.5 weeks · **first LLM · GATES G3 + G4 · FIRST RELEASE**

**Audience, resolved (2026-09-08): the owner plus up to three testers**, behind
`SABER_RESEARCH_ASSISTANT=1`, off by default and independent of
`SABER_DECK_LAB_REDESIGN` — the same env-gated rollout shape the refactor already
uses for `SABER_DECK_LAB_BUILDER` / `_RESEARCH` / `_PLAYMAT`.

Three testers rather than one, because R4 exists to test a **product hypothesis**
and a sample of one cannot falsify it; and not the whole tester list, because the
web tier holds a CPU embedding model and a cross-encoder reranker resident
alongside Flask and SQLite on one `shared-cpu-1x` machine (§7.3). Note that
`SIM_MAX_CONCURRENT=1` does **not** constrain R4 — R4 has no `sim_study` step.
That constraint arrives at R6, and it is a queue problem there rather than a
sizing one.

**Two actions only: Find cards, Explain selection.** The dock — the right-hand
slide-over mirroring `.dl-add-panel`, full-screen at the 767px breakpoint (§6.1)
— the deck binding, the four result-tier renderers, the citation contract, the
job model, progress notes, and thread persistence. No analysis, no studies, no
comparison.

The planner is the plan §4/A5 design at reduced scope — closed tag vocabulary in
a cached system prompt, strict schema, decomposition into narrow calls, plan
memory, self-consistency by union, deterministic validation with cheap repair.

**G3** (bake-off against the R3 hand-written baseline across ≥3 cheap models with
one frontier model as the **control**, so a mediocre score is attributable) and
**G4** (citation coverage 100%, cards-outside-result-set 0, bare rates 0) are both
measured here, on the reduced step set. **Publish the gap whatever it is.**

**This phase is blocked on D0c** — the provider decision is made (HuggingFace
router, `deepseek-ai/DeepSeek-V4-Flash:deepinfra`, `HF_TOKEN`, no code changes),
but the secret is not yet set on `dylnmtthws-decklab` and no live call has been
verified. R0–R3 are unaffected. **The gate is a passing
`scripts/smoke_model_gateway.py`, not the decision** — a decision does not
un-gate a release; a measured call does. This is the only remaining P0.

**Acceptance:** a tester resolves a real card-search question in the builder, adds
a result to a deck, and the thread is still there tomorrow — **on a phone as well
as a laptop**, since §6.1 puts mobile in scope and the dock inherits the
breakpoint for free. Usefulness rig (§10.2) runs its first sessions here.

### R5 — Field intelligence and Analyze deck · ~2 weeks

`deck_tag_profile`, `card_field_stats`, `archetype_cluster`, the `field_stats`
step, and the §6.5 analysis flow with saved findings and the editable plan
assumption.

Every field query returns denominator, window, event-size floor and coverage as
**required fields**. A thin-sample commander returns a stated absence, never a
percentage over n=3. Thresholds ship as **configuration** so the **D1** census can
set them without a code change.

**D6 constraint, which applies regardless of whether D6 lands:** EDHTop16 sends no
card quantities, and the missing cards are deduplicated basics correlated with
colour identity (mono-colour averages 9.1 missing, five-colour 0.1). Mechanic tag
profiles are unaffected because Commander is singleton. **Mana-base comparison is
affected with a direction**, so R5 excludes basics from every field-derived count
and restricts any land comparison to `is_complete = true` with a stated
denominator.

### R6 — Goldfish study pilot, Kinnan-scoped · ~1.5 weeks

The `sim_study` step, the `CapabilityStatement`, the study report view, saved
studies with decisions, and staleness labelling by `deck_sha256`.

Scoped per §5.2 to the one authored pack. Generic execution is not offered.
`games` becomes a study parameter (30,000 within `SIM_MAX_GAMES=60000`), the run
is an async job from the first commit, and the 300 s server-side kill renders as
a failure.

**Acceptance:** a player runs a study, reads the coverage before the number, saves
a decision, edits the deck, and sees the report correctly labelled "applies to an
earlier list."

### R7 — Variant comparison · ~2 weeks

Two player-authored lists, one objective, compatible execution settings, both
executed. Paired interval validated against independently seeded arms **before**
any comparison is shown. Every comparison carries what it cannot evaluate,
beside the finding — the removed-interaction case from the brief is the canonical
example and should be in the fixtures.

Generic execution may be offered here, subject to §5.2's `NotRepresented` rule.

### R8 — Automatic diff proposals · ~2 weeks

Moved from A8. Deterministic candidate generation, deterministic scoring, the
model groups and narrates and may choose among near-ties with a stated reason.
Output is a previewable `deck_documents` changeset with a `mutation_id`, never
auto-applied, every swap carrying its evidence and its originating
`research_turn_id`.

### R9 — Solver decision studies · experimental, unscheduled

`edh_solver` is **not a dependency for R0–R8** and no work order is opened against
it. It is Milestone 0: one synthetic vertical, uncalibrated opponent clocks, and
a node-locked comparison of `attempt_now` vs. `wait_one_turn` under a **declared**
field with an expiry.

Its honest current question is narrow, and the eventual good experience —
*"explore how this decision changes with turn order, available protection, and
different opponent assumptions,"* shown side by side, with the cells where the
preferred action **changes sign** called out — is genuinely instructive. Position
it as preparation and retrospective analysis.

Two limits to carry into any integration: whole-deck goldfish results **cannot**
substitute for held-hand or midgame evaluation (the simulator refuses held hands,
midgame states and seat overrides over HTTP; `--hands` and `--grid` are CLI-only),
and no layer claims a GTO certificate for a four-player pod — that is a
definitional limit, not a compute limit.

---

## 10. Evaluation: two rigs, deliberately separate

### 10.1 Correctness rig — machine, every phase

Two modes, and the distinction is the point: **substrate mode** (hand-written
plans, no model) and **end-to-end mode** (model-planned). The difference between
the two scores is the model's contribution, isolated. It is the only way to answer
"would a better model help?" without guessing.

| Metric | What it catches | Kind |
|---|---|---|
| recall@k on `required_oracle_ids` | substrate failing to find the answer | quality |
| `forbidden_oracle_ids` hit rate | substrate returning wrong things confidently | quality |
| citation **coverage** | unsourced prose | **invariant — must be 100%** |
| cards-named-outside-result-set | hallucination | **invariant — must be 0** |
| bare-rate count | denominators dropped | **invariant — must be 0** |
| absence-stated rate on out-of-scope | improvisation instead of honesty | quality |
| manufactured-finding rate on healthy decks | analysis treated as an obligation | quality |
| clarification appropriateness | asking when it should answer, and vice versa | quality |
| cost per question, p50/p95 | the economics of the bet | operational |
| latency p50/p95 | whether it is usable | operational |

The three invariants are **structural**. A non-zero result is a bug, not a tuning
target.

### 10.2 Usefulness rig — human, from R4 onward

The plan had no equivalent, and this is the second reversal in §2.

| Measured | How |
|---|---|
| **Citation support**, not presence | Sample N assertions per release; adjudicate whether the cited source actually supports the claim. Frontier-model judge, with a human-audited subsample, because an LLM judge grading its own family is a known failure mode. Report support rate separately from coverage. |
| **Rules accuracy** | Golden rules questions adjudicated against the CR text by a person, not by string overlap. |
| **Retrieval relevance** | Beyond recall on required ids: are the *other* returned cards defensible? Graded on a sample. |
| **Comprehension** | After a study, ask the tester to state in their own words what the result establishes and what it does not. This is the metric that catches a beautiful, misleading report — and no automated check can. |
| **Task time and return rate** | Time to resolve a stated deck question, vs. their own baseline. Whether they come back with a second question. |

The rigs report separately and neither substitutes for the other. A release can
have a perfect correctness scorecard and fail the usefulness rig; that outcome is
informative and is the reason for the split.

### 10.3 The golden set is a judgment, not a fact

`required_oracle_ids` are hand-verified by the owner. The set records **who
labelled each question and when**, and questions whose labels are contested are
**marked rather than silently resolved**. An eval set that quietly encodes one
person's opinion as ground truth produces a system that is confidently wrong in
exactly that person's blind spots.

---

## 11. Cost

Per research question, with the R4 techniques applied:

| Component | Calls | Rough cost, cheap model |
|---|---|---|
| Clarification (CLASSIFY, tiny schema) | 0–1 | ~$0.001 |
| Plan (3× self-consistency, cached system prompt) | 3 | ~$0.004 |
| Tool execution | 3–8 | $0 — server-side |
| Narration over ~10–15k tokens of results | 1 | ~$0.01–0.02 |
| **Total** | | **~$0.02–0.03** |
| Cache hit on a repeated question | | **$0** |

Against `annual_cost_ceiling_usd: 100`, that is roughly 3,000–5,000 questions per
year before caching, and deck-building questions cluster hard around popular
commanders, so caching should move that materially.

Two cautions. The pricing in `config/cedh.yaml` carries its own warning — those
are recorded DeepInfra list prices, and the router bills what the provider charges
on the day. And the G3 frontier control costs perhaps 10× per question over ~150
questions a handful of times: a few dollars total, and the cheapest information in
this document.

Simulator compute is a separate line item on a separate machine
(`docs/hosting-cost-model.md`), budgeted outside the LLM-only annual target.

---

## 12. Dependencies

Full work orders are in [`docs/upstream-dependencies.md`](docs/upstream-dependencies.md).
Re-prioritized against this spec's ordering:

| # | Repo | Item | Now gates | Priority |
|---|---|---|---|---|
| **D0c** | deployment | **Decision made** (HF router, `HF_TOKEN`). Remaining: set the Fly secret and pass `scripts/smoke_model_gateway.py` | **R4 — the first release** | **P0** |
| ~~D0b~~ | deployment | Provider and model choice — **resolved 2026-09-08**, no code changes (§14.1) | nothing | — |
| ~~D0a~~ | this repo | Silent model absence on the candidate page — **not a dependency; scheduled into R0** (§14.1) | nothing | — |
| **D3** | `commander_simulator` | Per-card inert/unauthored list (`result.v4`) | R6's card-level capability statement (§5.1) | **P1** |
| **D1** | `ingestion_pipeline_mtg` | Corpus depth census + recurring nightly | R5 thresholds | P1 |
| **D2** | this repo | Resolve the two-corpus fork | R2, R5 integration | P1 |
| **D4** | `ingestion_pipeline_mtg` | Combo corpus (Commander Spellbook) | `combo_lookup`; R8 candidates | P1 |
| **D5** | `ingestion_pipeline_mtg` | Card rulings in `mtg_v1` | `rules_lookup` completeness | P2 |
| **D6** | `ingestion_pipeline_mtg` | Moxfield adapter — real quantities | R5/R8 mana-base analysis | P2 |
| **D7** | `commander_simulator` | Pack discovery via `/healthz` at scale | R6/R7 `sim_study` | P2 |
| **D8** | `ingestion_pipeline_mtg` | `card_name_index` as a view | minor dedup | P3 |

Changes from the previous ordering: **D0 is now the gate on the first release
rather than on a late phase**, which is the direct cost of moving the UI earlier
and is worth paying. **D3 rises to P1** because §5.1's capability statement is a
headline feature of R6 rather than a narration detail. Nothing blocks R0–R3.

### 12.1 The two carried-over decisions, now resolved

Both were left open by the plan. Both are closed here (2026-09-08) on the
recommendation the plan itself made; neither is decided by the refactor, so both
are flagged as reversible if the owner disagrees.

**Combo corpus (D4) — defer out of the first release; ingest into `mtg_v1` when
it lands. Never local.**

`combo_lookup` is not in R3's reduced step set and not in R4's two actions, so
D4 gates nothing before R5. Deferring it costs the first release nothing.
Building it *locally* to move faster is the tempting wrong answer: it re-creates
the exact duplicate-ingestion problem **D2** exists to close, and it would put a
second card corpus behind the assistant while D2 is trying to remove one.
Decision point: **before R5 closes**, since combo completions are one of the
stronger sources of R5 findings and the whole of R8's candidate generation.

**Embedding model swap — one model, `bge-small-en-v1.5`, re-index both indexes,
at R2.**

MiniLM-L6 → bge-small changes the `reference_layer` index as well as the new card
index. Running two encoders would mean two notions of similarity inside one
product, which surfaces as a rules answer and a card result disagreeing about
what "similar" means, with no way for a player to tell why. The cost of doing
both at once is one re-index; the cost of doing them separately is a class of bug
that is very hard to attribute. R2 already rebuilds the card index, so it is the
cheapest moment.

**Verification result (R2, 2026-09-09): the reference index is live.**
`main.py` constructs `ReferenceRetriever` directly, while
`reference_layer.evidence` constructs it for the legacy profiler through
`reasoning/profiler.py`. The old index records neither model identity nor a
generation, and both MiniLM-L6 and bge-small emit 384 dimensions, so shape
checking alone cannot distinguish stale and current rows.

This does not reopen the one-model decision; it determines the migration
mechanism. R2 builds a complete generation keyed by model ID, immutable
revision, document-template version, dimensions, normalization and reference
content hash, validates every row, and atomically switches one active-generation
pointer. Existing consumers move to that reader in the same change. A partial
in-place rewrite of `reference_chunks.embedding` is forbidden because it can
serve a same-shaped mixture with no observable error.

---

## 13. Prior art, and where the opportunity actually is

Card search, combo discovery and playtesting are **established expectations**, not
differentiators. Archidekt ships an integrated playtester
([FAQ](https://archidekt.com/faq)); Commander Spellbook finds existing and
potential combos from a list
([find-my-combos](https://commanderspellbook.com/find-my-combos/)); DeckFlow
documents AI-assisted analysis workflows
([deck analysis](https://www.deckflow.gg/help/deck-analysis)).

Deck Lab's opportunity is not any one of those capabilities. It is
**connecting research to reproducible experiments and remembered decisions** —
the loop in §1, with §5.6's saved studies and §5.1's honest capability statements
as the parts nobody else ships. That is also the part that is hardest to copy,
because it requires the simulator's honesty fields and the deck document's
command log to already exist. They do.

---

## 14. Decisions taken

All four questions this spec opened were closed by the owner on **2026-09-08**
and **approved as recorded**. Recorded here with the basis for each, because two
were settled by the refactor's shipped code and two were not — and a reader six
weeks from now should be able to tell which is which before relying on them.

**Do not reopen these without new evidence that contradicts their stated
assumptions.** Where an assumption is named below as *to verify*, verifying it is
part of the phase that depends on it; a failed verification is exactly the new
evidence that would justify reopening.

| # | Decision | Basis | Where |
|---|---|---|---|
| 1 | **Nav stays Research / Build, unchanged. The assistant is "Ask".** Not a nav item; a context-bound dock with no page of its own | **Refactor.** `deck_lab/base.html` already ships that taxonomy in the header, drawer and account popover | §1.1 |
| 2 | **Mobile is in scope.** Dock mirrors `.dl-add-panel` on the right; full-screen at the existing 767px breakpoint. `CLAUDE.md`'s `ui_scope` is stale and R0 corrects it | **Refactor.** `deck-lab.css` ships `@media (max-width: 767px)` with substantial rules, a drawer, `dl-bottom-action`, `desktop-only` opt-outs and safe-area insets | §6.1, §7.5 |
| 3 | **R4 pilot: owner plus up to three testers**, behind `SABER_RESEARCH_ASSISTANT=1`. **Explicitly reversible** | **Recommendation.** The refactor supplies the env-gated rollout shape but not the number. Three because one cannot falsify a product hypothesis; not more because of web-tier residency | §9 R4 |
| 4a | **D4 combo corpus: defer past the first release; ingest into `mtg_v1` when it lands, never local.** Decide before R5 closes. **Explicitly reversible** | **Recommendation**, matching the plan's own | §12.1 |
| 4b | **Embedding swap: one model (`bge-small-en-v1.5`), re-index both indexes, at R2. Explicitly reversible**, and conditional on verifying `reference_layer` has no live consumer first | **Recommendation**, matching the plan's own | §12.1 |

**Reversibility is a property of the mechanism, not a promise.** Decisions 3, 4a
and 4b are reversible because each is a configuration or a rebuild rather than a
migration:

- **3** is one env var. Widening the pilot is `SABER_RESEARCH_ASSISTANT=1` on more
  accounts; narrowing it is removing it. No data changes either way.
- **4a** is reversible *because* it is a deferral. The irreversible move is the
  one it rules out — ingesting the combo corpus locally, which would create a
  second card corpus behind the assistant and is why "never local" is stated as a
  constraint rather than a preference.
- **4b** is reversible by re-indexing back, at the cost of one rebuild. What makes
  it cheap is the timing, not the model choice — see the verification below.

Two of these corrected something the first draft of this spec had wrong, and both
corrections came from reading the refactor's CSS rather than from the decision
itself:

- The dock cannot sit **beside** `.dl-stats-rail`. The builder is a fixed
  two-column grid with no third column, and the rail is `display:none` at the
  mobile breakpoint — so a dock parented to it would have vanished exactly where
  the brief wants a full-screen view. It is a right-hand overlay instead (§6.1).
- `SIM_MAX_CONCURRENT=1` does **not** constrain the R4 pilot, because R4 has no
  `sim_study` step. That constraint arrives at R6. The two were conflated; §7.3
  now separates them, since they have different fixes — a queue at R6, and
  process residency at R4.

### 14.1 D0 — the one real blocker

**D0 splits into two things that were filed as one, and only the second is a
decision.**

#### D0a — the missing-absence bug. Not a decision; fix it now, independent of D0

The work order asked whether the model outage on the 2026-09-05 acceptance run
degraded *visibly* or *silently*. **Answered from the code: partly silently, and
production is in the silent case.**

| Path | `gateway is None` | Warning appended? |
|---|---|---|
| `lab.py:_intent` | line 267 | **Yes** — but only when `raw_intent` is set. An explicit `pack_id` returns at line 256 before the gateway check, and with one supported pack that is the UI's normal path |
| `lab.py:_summarise_evidence` | line 314 | **No** — silent `return None` |
| `lab.py:_explain` | line 342 | **No** — silent `return None` |

And `templates/cedh/candidate.html:164` is `{% if explanation %}` **with no
`{% else %}`**, while `cedh_routes.py:candidate` does not pass `modes` to the
template at all. So in production today — `HF_TOKEN` unset, Kinnan pack chosen
explicitly — the candidate page renders **no explanation, no evidence summary and
no warning**. The only signal is `LabModes.notices` on `lab.html`, which is a
different page, seen before the build, and not carried onto the result.

That violates `absence_is_visible`. Note the contrast that makes it a bug rather
than a design: when a credential *is* present and the provider *fails*,
`lab.py:_call` appends a warning and `candidate.html:19` renders it. The
degradation path is correct; the never-configured path is the one that is silent.

**Fix, in this repo, with no dependency on D0:** append a warning on the
`gateway is None` early return in `_summarise_evidence` and `_explain`; persist
that warning with the build and pass it to `candidate.html`; give the explanation
block an `{% else %}` that states the absence. Do not infer a historical build's
mode from the current environment: provisioning a credential later must not
retroactively claim the old build used it. Scheduled into **R0**, because the
Research Assistant inherits this exact pattern — an assistant whose model is
unavailable must say so, and shipping R4 on top of a silent-absence precedent
would propagate it.

#### D0b — the provider decision. Yours, and it gates the first release

**The exact unresolved decision:** production has no model credential, and the
incumbent configuration routes through an intermediary whose token name is now
misleading. `config/cedh.yaml` sets `provider: deepinfra`, `base_url:
https://router.huggingface.co/v1`, `model_id:
deepseek-ai/DeepSeek-V4-Flash:deepinfra`, `credential_env: HF_TOKEN`. So the
model is served *by* DeepInfra but reached *through* the HuggingFace router, and
the credential is an HF token. Nothing is set on `dylnmtthws-decklab`.

**Recommendation: keep the HuggingFace router and provision `HF_TOKEN`.** Do not
switch to a direct DeepInfra account for R4.

Evidence:

- **The `:deepinfra` suffix is router syntax.** `provider_deepseek.py:115`
  `_assert_pinned` requires a provider suffix on the model id and refuses to
  start without one unless `require_pinned_provider: false`. Going direct makes
  the suffix meaningless and forces either a config override that weakens the pin
  or a rework of the check. That is real work bought for no R4 benefit.
- **G3 needs several models under one credential.** The R4 gate is a bake-off
  across ≥3 cheap models plus one frontier control (§9, R4). A router gives that
  for one credential and one adapter; direct accounts mean three signups before
  the gate can run.
- **The ceiling is already real.** `config/settings.yaml` sets
  `monthly_cost_ceiling_usd: 15.0`, and `lab.py:_call` catches
  `LLMCostCeilingExceeded` and surfaces it as a warning rather than swallowing
  it. Requirement 5 of the work order is substantially satisfied already.
- **Switching later is an adapter change, not a rewrite** — that is what ADR-021's
  provider-neutral gateway bought. The cost of being wrong here is low, which is
  itself an argument for the cheaper path now.

Caveat to carry: the pricing in `config/cedh.yaml` is recorded DeepInfra list
pricing and the file says so — the router bills what the provider charges on the
day. Until one real call lands a `cost_log` row, §11's cost model is unvalidated
arithmetic.

> **DECIDED 2026-09-08.** The owner has a HuggingFace account with billing
> enabled. **D0b is resolved as recommended: keep the HuggingFace router, keep
> `deepseek-ai/DeepSeek-V4-Flash:deepinfra`, keep `credential_env: HF_TOKEN`.
> No code changes.**
>
> What remains is provisioning and verification, not a decision — see §14.2.

#### D0c — provisioning and verification. Not a decision; an owner action

Three steps, and the second is one command:

1. **Set the secret.** `fly secrets set HF_TOKEN=… --app dylnmtthws-decklab`.
   The recorded production secrets are `CEDH_SIMULATOR_URL`, `MTG_V1_DSN` and
   `SABER_SECRET_KEY`; this makes four. Setting a secret restarts the app.
2. **Run `scripts/smoke_model_gateway.py`** (§14.2). It walks D0's five
   acceptance criteria in order and prints the measured tokens, cost and latency.
3. **Record the measured cost** against §11's estimate. Until one real call lands
   a `cost_log` row, the whole cost model is unvalidated arithmetic — that is
   stated in §11 and this is what removes the caveat.

**R4 stays gated until step 2 passes.** The decision being made does not
un-gate the release; a verified live call does.

### 14.2 D0 verification tooling — built and offline-verified

`scripts/smoke_model_gateway.py` closes D0's acceptance in one command. It walks
the five criteria in the order the work order states them:

| Step | Proves | Verified offline |
|---|---|---|
| 1 | The credential named by `model.credential_env` is present | Yes — refuses with the config path when unset |
| 2 | The gateway constructs, running `_assert_pinned` | Yes — `:cheapest` and a bare id both raise `ModelConfigurationError`; the configured id is accepted |
| 3 | One live call returns a **validated Pydantic instance** | **Needs the real token.** The gateway raises rather than partially accepting, so reaching the assertion *is* the proof |
| 4 | A `cost_log` row lands with model, call type and non-zero tokens | Yes — row insert, count delta and `_latest_row` exercised against a scratch DB |
| 5 | The ceiling **raises** rather than being logged and ignored | Yes — `check_ceiling(15.0)` passes, `check_ceiling(0.0)` raises `LLMCostCeilingExceeded` |

Three design choices worth stating, since each is a way the check could have been
weaker than it looks:

- **The schema is two fields, one of them a bounded int.** A single free-text
  field would validate even if the provider ignored the schema entirely, so it
  would prove nothing about structured output.
- **Zero tokens is an explicit failure.** A call can succeed while usage
  accounting silently returns zero, and every cost figure downstream would then
  read zero. The script fails rather than reporting a free call.
- **A missing ledger file is an explicit failure.** `CostLedger.record` swallows
  write failures by design — accounting must not fail a build — which means a
  missing row is silent in production. The script checks the file exists *before*
  spending money, and asserts the count delta afterwards.

It writes one row to the ledger, so `--db` defaults to `data/sabermetrics.db` and
should be pointed at a scratch database unless the production ledger should
record the check.

### 14.3 D3 — resolved from the code; a default is set either way

**Resolved: R6 ships without D3, and D3 remains worth doing.** Not a blocker.

From the code, the split is exact. `cedh-simulation-result.v3` gives
`coverage.modeled_cards`, `inert_cards`, `unauthored_cards` as **integers**, plus
`coverage.inert_by_reason` across a closed set of five categories — `interaction`,
`opponent_trigger`, `opponent_permanent`, `timing_only`, `no_object_in_model` —
and `strategy_pack.known_blind_spots` as prose. Deck Lab already validates v3 and
`simulator.py` already reads all of it.

So R6's `CapabilityStatement` (§5.1) can say, today and with no upstream work:

> *"31 of 99 cards were invisible to this model: 12 because the model has no
> opponents, 9 because their effect has no object in the model, 10 unauthored.
> The per-card list is not available from this simulator version."*

What it cannot say is **which** card. That is the actionable half, and it is
`result.v4`.

**The remaining choice is upstream's, not this spec's:** whether
`commander_simulator` publishes v4 with `coverage.inert_cards[]`,
`unauthored_cards[]` and `misclassified_cards[]`. Two things make the case
stronger than "nice to have":

- The data demonstrably exists inside the simulator — it prints an inert table in
  the human run report, which is how `Hullbreaker Horror`'s known misclassification
  is documented at all. v4 is a serialization change, not new analysis.
- A known misclassification that appears in a human report and not in the machine
  contract is **visible to a person and invisible to a consumer**. That asymmetry
  is the argument.

**Default if D3 never lands:** R6 renders the counts and the by-reason breakdown,
states that the per-card list is unavailable, and **does not parse warning
strings** to reconstruct it. The handoff rules that out explicitly and it is
right to: a display that breaks when a warning is reworded is a contract nobody
agreed to.

### 14.4 D1 — census specified and executable; numbers need the live database

**Done as far as the repository allows.** Two artifacts:

- **[`docs/d1-corpus-census.md`](docs/d1-corpus-census.md)** — scope (six
  measures, and what is deliberately excluded), counting method with the reason
  each choice changes the answer, the figures already on record, and seven
  numbered uncertainties.
- **`scripts/d1_census.py`** — eight read-only queries in one
  `REPEATABLE READ READ ONLY` snapshot. Every query passes `assert_v1_only`
  (verified, including a negative control on `mtg_internal`). It warns if the
  connected role is not `mtg_consumer`, and **refuses to run without a DSN**
  rather than falling back to fixtures, because a census of a fixture measures
  the fixture.

**The blocker is access, and it is the correct blocker.** This worktree has no
`MTG_V1_DSN`, no `psql` and no `flyctl` — a feature branch should not hold
production database credentials. Running the script is a task for whoever does.

Three method choices worth surfacing, because each changes the number:

- **Group by `deck.commander_identity`, never by name.** It is the sorted-oracle_id
  key, so a partner pair is one identity. Grouping by name splits
  Thrasios/Tymna from Tymna/Thrasios into two populations that each look half as
  popular as the real one.
- **`unresolved:` identities are counted separately, never merged.** Folding them
  in inflates the identity count with names rather than commanders; dropping them
  silently hides a resolution problem worth seeing.
- **Two floors are measured, not one:** ≥30 decks, and ≥30 decks *across ≥5
  events*. Thirty decks from a single tournament is one metagame snapshot, not a
  trend, and only the second floor can tell them apart.

What is already known without running it: `card_any_medium` at 34,570 rows,
**200 tournaments from a single source in one manual import**, and Kinnan at
n=635 with 42 incomplete. The single source is the finding most likely to matter
— with one `source`, archetype clustering inherits EDHTop16's coverage biases
entirely, and the census will show that rather than fix it. And the corpus does
not currently refresh: `NIGHTLY_ENABLED` is unset, so these numbers will age
silently. Enabling the nightly needs explicit owner approval and is deliberately
not bundled into running a census.
