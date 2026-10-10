# Goldfish Lab QA milestone — 2026-10-10

## Product intent and delivery boundary

Deck Lab connects deck creation, five solo diagnostics, and small measured revisions. The five checks are opening hands, development through turn five, first commander removal, creature wipe after turn five, and conversion to a win by turn seven. A comparable revision permits at most three replacements, keeps the same commander and settings, and compares with both its parent and original baseline.

This milestone is an experimental QA slice, not the completed vision or a win-rate forecast. Production deployment is not authorized. Research Assistant live AI calls and generator integration are not implemented in this slice. QA makes no external model calls.

## Implemented

- Four user-supplied fixtures, each 99 library cards plus commander; affordable Sauron's 35 sideboard cards remain separate. Budget labels come from the user, not verified market prices.
- Private reference import, owner-only diagnostics, immutable deck/settings snapshots, queued execution, stored history, comparison and fresh-seed trials.
- Three-replacement validation including omission-of-parent protection; stale-deck and incompatible-setting checks.
- Five report panels, trace evidence, explicit partial/unknown coverage and deterministic inspection guidance.
- Simulator-owned, checksum-verified Python zipapp from commander-simulator commit 43fe7f80af9c870048f315cae7600f0273b446d7. No game mechanics are reimplemented in the web service.
- Authored London mulligans, some mana/draw/damage effects, commander tax and bounded disruption scenarios. Unimplemented cards are unavailable; their absence can distort every partial metric.

## Measured acceptance

App regression: 2,024 passed, 31 skipped. Targeted app checks: 46 passed before packaging; 15 transport/route/fixture checks repeated after zipapp packaging. Simulator mechanics: 26 passed. Full mypy: 147 source files clean; full Ruff clean; JavaScript syntax clean.

All four real lists imported into an isolated synthetic database using public card facts, and each completed 200 trials. Card copies with at least some modeled effects were 46/100 affordable Vivi, 31/100 high-power Vivi, 34/100 affordable Sauron and 28/100 high-power Sauron. Other cards may be partly supported. First four scenarios are partial; finish is unsupported on these lists. These results do not justify deck-quality or shopping recommendations.

## Handoffs and KPIs

| Assignee | Bounded task and KPI | Evidence / unmet requirement |
| --- | --- | --- |
| OpenCode Spark 1.3 free | Four plan records and deterministic coverage-aware guidance; targeted tests pass | Delivered plan module and 12 tests. Coordinator corrected wrong Sauron epithet and added partial-coverage regressions. |
| Claude Code Sonnet 5 | Owner-scoped immutable experiment repository with three-swap validation | Delivered repository and 18 passing tests. Coordinator bounded history to 50 rows. |
| Codex Luna 6 | Exact reference fixtures and accessible five-panel UI | Four 100-card fixtures, sideboard35, three fixture tests; UI delivered. Coordinator fixed numeric revision submission and added seed/trial controls. |
| Codex Sol 6.1 | Simulator-owned versioned mechanics artifact with repeatability and truthful unknowns | Source commit above; 26 targeted tests passed. Broad mechanics KPI remains unmet: only 28–46 card copies with modeled effects per reference deck. |
| Codex Sol 6.1 independent challenge | Find false-positive casts/wins and disruption accounting errors | Found illegal creature-target cantrips, empty-library losses, lethal-trigger ordering and wipe denominator issues; implementer fixed and tested them. |
| Claude Code Opus 5.5 | Independent senior QA gate with prioritized correctness/security findings | Initial review caught coverage/validation deficiencies. Final review retained separately with release evidence; coordinator handles findings before QA. |
| Fable 5.1 | Requested skeptical review | Unmet: model unavailable in configured providers. Opus and independent Sol reviews were used transparently; neither is represented as Fable. |
| Coordinator | Independently test integration, retain QA isolation and verify exact release | Full regression/lint/type evidence above; deployment identity and live acceptance are recorded in infrastructure QA receipts. |

## Next implementation gates

1. Expand public card definitions and trace-based mechanics tests for these four fixtures; handle alternate/modal faces, tutors, Breach/Oracle routes, combat, amass/Ring effects and legal priority sequencing.
2. Define strategy-specific engine-online, commander-independent action, wipe recovery and frozen-board finish predicates. A positive finish requires a replayable legal witness. Unknown remains distinct from failure.
3. Validate pilot decisions against primer examples and curated expert traces. Pure goldfish and scripted opponent-trigger environments must remain distinct; Sauron's opponent-cast triggers cannot be assumed in strict goldfish.
4. Add protected interaction/theme/budget constraints before generator optimization; compare against explicit baselines and report uncertainty. Re-evaluate promising revisions on fresh seeds to avoid optimizing to one trial set.
5. Connect Research Assistant to structured evidence and legal candidate proposals; permit at most three applied replacements per comparable step. Keep advice, user edits and automatic optimization distinct. Live AI remains disabled on QA until its environment policy is explicitly changed.
6. Run authenticated browser acceptance and live QA HTTP checks; production only after owner review of the exact QA candidate.

## Primer sources

- High-power Sauron: https://moxfield.com/decks/WDJsYexc9EaHELI7vXeHjQ/primer
- High-power Vivi: https://moxfield.com/decks/nBKRWuYyckqPxpCmWBjIBg/primer

Primer intent informs plan notes; it is not evidence that the simulator executed a line.

## Senior review disposition

Opus final gate: GO with conditions for the QA-only experimental slice; no cross-owner leak or three-swap bypass found. Coordinator follow-up fixed queue-capacity leaks on unexpected database exceptions, deck-deletion snapshot retention, home-page coverage disclosure, copy-weighted coverage labeling and misleading automatic-parent text. A dedicated `SABER_DECK_LAB_GOLDFISH` flag defaults off; only QA enables it. Added regression checks; 66 affected app tests pass, including deck documents.

Known restriction: after more than three replacements, restore a comparable revision or import a separate deck to begin a separate history. The user explicitly requires at most three replacements per experiment, so this milestone does not silently waive the limit. Full account deletion retention follows existing application limitations; wider rollout requires an account-retention audit. Original-baseline comparisons may span more than three cumulative changes through individually compliant revisions.

## Live QA corrections

Initial QA checks exposed root-owned fixture directories inherited from the private build context's umask. The image now copies public fixtures with app-user ownership so the non-root service can read them. The renderer and comparison adapter also accept the simulator's actual `value/numerator/denominator/ci_low/ci_high` proportion schema; a regression verifies 40% to 55% produces a +15 percentage-point comparison. Raw engine results remain unchanged.

Coverage correction from live review: the engine's `modeled_cards` counts cards with any authored definition, including partial definitions. The UI therefore says "have modeled effects; some are partial," rather than claiming complete coverage. The unsupported-card list includes partial definitions as well as unavailable cards; its count is distinct definitions rather than card copies.
