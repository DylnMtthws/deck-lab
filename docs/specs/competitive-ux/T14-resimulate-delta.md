# T14 — Re-simulate an edited deck and show the change

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
Depends on T10 (decks imported from candidates keep `source_kind="candidate"`, `source_id`, and roles).

## Why
The goldfish simulator is something no deck site has. Today it runs only on a fresh candidate. Once a
user edits the deck, they lose the numbers. Users should be able to re-run it on their edited list and
see the delta, or an explicit "not simulated" with the reason.

## Constraints that apply with extra force
- `absence_is_visible`: no simulator, an unsupported commander, or a list the simulator rejects must each render as `Not simulated` plus the reason. Never a neutral score.
- `deck_sha256` rules (CLAUDE.md `no_collection_in_the_engine`): when you build a `DeckCandidate` from a deck document, compute the hash ONLY through the existing hashing function used for candidates. Do not add fields to it.
- The simulator client is obtained ONLY through the existing factory (`cedh/factory.py` `build_simulator_client`). No new network code.
- The simulator call must not block a web request thread for its full duration. Reuse the existing build job executor pattern (`cedh_routes._BUILD_EXECUTOR` with the jobs table) or an equivalent bounded executor, with job status JSON.

## Allowed files
- `src/sabermetrics/deck_simulation.py` (new): converts a deck document to a `DeckCandidate`, runs the simulation through the factory client, and stores the result.
- `scripts/setup_db.py`: one new table, `deck_document_simulations(id, deck_id, owner_id, deck_sha256, revision, status, result_json, reason, created_at)`, with an index on `(deck_id, created_at)`.
- `src/sabermetrics/db.py`: a small repo class for that table.
- `src/sabermetrics/ui/builder_routes.py`: `POST /api/decks/<id>/simulate` (returns 202 + a status URL) and `GET /api/decks/<id>/simulations/latest`.
- `src/sabermetrics/ui/static/deck-lab-simulate.js` (new)
- `src/sabermetrics/ui/templates/deck_lab/builder.html`: only the script tag.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_resimulate_delta.py` (new), `tests/resimulate_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T14.md` (new)

## Requirements
1. **Conversion.** Build a `DeckCandidate` from the document:
   - Commanders come from the commander entries.
   - The library comes from library-zone entries only (private zones excluded). Each entry's role is mapped back to cEDH roles, using the inverse of the T10 mapping. Ambiguous or missing roles map to a neutral role allowed by `CandidateCard`. Document the choice.
   - Provenance comes from the source candidate if there is one; otherwise it is minimal and clearly marked as a user-edited deck.
   - If the deck is not exactly 100 cards or not legal (`document["validation"]["legal"]`), do not call the simulator. Store and return `Not simulated: deck must be a legal 100-card list (<first issue>)`.
2. **Baseline.** The baseline is the source candidate's stored `simulation_json` when the deck came from a candidate, otherwise the previous successful `deck_document_simulations` row. If neither exists, there is no delta and the UI says `No earlier simulation to compare`.
3. **Delta.** Use `SimulationResult.headline()` (an `AssemblyPoint`). Show the headline metric before → after, with its unit, using the metric names the result model actually has. Read `cedh/simulator.py`; do not invent metric names. Also show the change in `unseen_card_count` when present.
4. **Staleness.** The latest simulation records the `revision` and `deck_sha256` it ran on. If the deck's current hash differs, show `Out of date: the list changed since this run` and a `Re-run` button.
5. **UI.** In `railSection("simulation", "Goldfish simulation")`:
   - A `Run simulation` button, a status line (`role="status"`) driven by polling the job until done (same backoff rules as T10), the result, and the delta block.
   - Absence states are rendered exactly with the strings above.
   - Only owners can run it; shared pages see the latest stored result read-only.

## Acceptance criteria → required tests (`tests/test_resimulate_delta.py`)
Use a fake `SimulatorClient` injected the way existing cEDH tests do.
- AC-1 `test_conversion_uses_library_zones_and_existing_hash`: the hash equals the existing candidate hash function's output for the same list. Assert that the function used is the existing one, by import identity or by comparing against a candidate produced by the builder for that list.
- AC-2 `test_illegal_or_wrong_size_deck_not_simulated_with_reason`
- AC-3 `test_unsupported_commander_not_simulated_with_reason` (the fake client's `supported_commander_keys` excludes it)
- AC-4 `test_run_job_stores_result_and_latest_endpoint_returns_it`
- AC-5 `test_delta_against_source_candidate_baseline`
- AC-6 `test_delta_against_previous_run_when_no_candidate`
- AC-7 `test_no_baseline_message`
- AC-8 `test_stale_result_detected_after_edit`
- AC-9 `test_simulation_does_not_run_on_request_thread` (the route returns 202 before the fake client's simulate is called; use an event or latch)
- AC-10 `test_ui_states_running_result_delta_stale_and_absence` (harness)
- AC-11 `test_shared_view_read_only` (Flask test client plus harness: no Run button)
