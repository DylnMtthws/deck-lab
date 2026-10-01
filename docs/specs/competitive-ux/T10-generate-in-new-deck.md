# T10 — Generate a deck from the new-deck flow, with real progress

Agent: HIGH (Cursor, Grok 4.7). Read `docs/specs/competitive-ux/COMMON.md` first; it is part of this spec.
No dependency on T00.

## Why
Deck Lab's differentiator is deterministic, evidence-grounded generation. In the redesigned UI it is
unreachable:
- The `/build` "New deck" dialog only creates an empty deck.
- The generator lives on the legacy `/lab?generator=1` page, behind a 3-second meta refresh with no progress.
- `POST /build/import/candidate/<id>` exists (`builder_routes.py`), but nothing links to it.

## Allowed files
- `src/sabermetrics/ui/templates/deck_lab/library.html`: the New deck dialog only.
- `src/sabermetrics/ui/templates/deck_lab/commander.html`: the "Start a build" area only.
- `src/sabermetrics/ui/static/deck-lab-generate.js` (new)
- `src/sabermetrics/ui/builder_routes.py`: new read-only endpoints, plus fixes inside `import_candidate` handling.
- `src/sabermetrics/deck_documents.py`: ONLY `import_candidate`, plus private helpers it calls.
- `src/sabermetrics/ui/cedh_routes.py`: ONLY additive changes to the JSON job payload (`_job_payload`). No behaviour change to the HTML routes.
- `src/sabermetrics/ui/static/deck-lab.css`: append-only, at the end of the file.
- `tests/test_generate_in_new_deck.py` (new), `tests/generate_flow_harness.mjs` (new)
- `docs/specs/competitive-ux/reports/T10.md` (new)

## Requirements
1. **Entry points.** The New deck dialog on `/build` gains a choice, as radio buttons, with the same name and commander fields as today:
   - `Empty deck`: the default, and today's behaviour.
   - `Generate from a strategy pack`: shows a pack `<select data-generate-pack>`.
     - Populate it from a new JSON endpoint `GET /api/generate/packs?commander=<card id or empty>`. It returns the supported packs (use `build_default_lab(...).pack_summaries()`), filtered to packs whose commander matches the chosen commander when one is chosen.
     - If none match, show `No strategy pack supports this commander yet.` and disable Generate. Unsupported is shown as unsupported, never hidden (CLAUDE.md `absence_is_visible`).
   - The commander page's "Start a build" area gets a secondary button `Generate a list` that opens the same flow, with that commander preselected.
2. **Run.** Submitting Generate POSTs JSON to the existing `POST /lab/build` (`pack_id`, optional `raw_intent` from an optional textarea `What should this deck focus on?`, and default constraints) with the CSRF header. It receives the 202 response with `status_url`.
3. **Progress.** Replace the dialog body with a progress view `[data-generate-progress]` (`role="status"`, `aria-live="polite"`) listing the steps `Queued → Building → Simulating → Explaining → Done`, mapped from the job `status` values. Read `_execute_build_job` and the jobs repo for the real status strings, and map each one explicitly.
   - Poll `status_url` (`.json`) with backoff: 1 s, then 2 s, capped at 4 s. Stop after 5 minutes with a timeout message and a link to the job page.
   - The current step is highlighted; completed steps are ticked.
   - On `failed`, show `error_code`/`error_detail` in plain words and a Retry button. Never show a spinner forever.
   - Additive payload: in `_job_payload`, add `"steps"`, the ordered list of statuses the job has passed through, ONLY if the jobs table already records that. Otherwise do not invent it; derive progress from the current status only.
4. **Land in the builder.** When the job is `done`, POST `/build/import/candidate/<candidate_id>` (form or JSON with CSRF) and navigate to the resulting deck (`/build/deck/<id>`).
5. **`import_candidate` must produce a usable deck.** Verify against a real candidate produced by the fixture pipeline (see `tests/test_cedh_lab.py` / `test_cedh_ui.py` for how candidates are made in tests):
   - Every library card from the candidate appears with the right quantity. Check which key the stored `candidate_json` actually uses for the 99, `library` (wire format, `cedh/wire.py`) or `cards` (`DeckCandidate`), and handle the real one. A test must prove 99 + commander(s) = 100 after import.
   - Each entry's `role` is set from the candidate card's role through this explicit mapping into the builder's role keys:
     - `acceleration`→`ramp`
     - `tutor`→`tutor`
     - `interaction`→`removal`
     - `protection`→`protection`
     - `card_advantage`→`draw`
     - `win_package`→`wincon`
     - `land`→`land` (if present)
     - anything else→`other`

     Read `cedh/domain.py` `ROLES` for the full set and extend the mapping so every value is covered. Unknown values map to `other`.
   - The import is idempotent per owner and candidate (the `UNIQUE(owner_id, source_kind, source_id)` constraint). A second import returns the same deck id rather than erroring.
6. The legacy `/lab` pages keep working unchanged.

## Acceptance criteria → required tests (`tests/test_generate_in_new_deck.py`)
- AC-1 `test_new_deck_dialog_offers_empty_and_generate` (Flask test client renders `/build`)
- AC-2 `test_packs_endpoint_filters_by_commander_and_reports_unsupported`
- AC-3 `test_commander_page_has_generate_button_with_preselected_commander`
- AC-4 `test_import_candidate_yields_100_cards_with_mapped_roles` (fixture candidate)
- AC-5 `test_import_candidate_is_idempotent`
- AC-6 `test_role_mapping_covers_every_cedh_role` (iterate `cedh.domain.ROLES`)
- AC-7 `test_progress_view_maps_each_job_status` (harness: a fake fetch returns the status sequence; DOM steps update)
- AC-8 `test_polling_backoff_and_timeout_message` (harness, fake timers)
- AC-9 `test_failed_job_shows_error_and_retry` (harness)
- AC-10 `test_done_job_imports_candidate_and_navigates` (harness asserts the POST to `/build/import/candidate/<id>` and `location.assign`)
- AC-11 `test_end_to_end_fixture_generation_to_builder` (Flask test client; run the job synchronously by monkeypatching the executor in fixture mode, as existing cEDH tests do; then import; GET the builder page returns 200 and the deck has 100 cards)
- AC-12 `test_legacy_lab_routes_unchanged`: the existing `/lab` tests still pass; name any you relied on in the report.
