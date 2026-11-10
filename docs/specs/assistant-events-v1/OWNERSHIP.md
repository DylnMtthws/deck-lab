# Implementation ownership — 2026-11-10

Baseline 454614ff; branch feat/assistant-events-v1-20261110. No deployments or paid benchmarks.

- Coordinator: assistant_events.py, assistant_event_store.py, assistant_store.py, assistant_jobs.py, ui/assistant_routes.py, assistant_runtime.py; event/store/route integration tests and contract docs.
- UX: ui/static/deck-lab-assistant-sidebar.js, ui/static/deck-lab-assistant-sidebar.css, ui/templates/deck_lab/builder.html, tests/test_build_assistant_sidebar_ui.py, tests/test_assistant_events_ui.py.
- Quality: assistant_obligations.py, assistant_publishability.py, assistant_quality_evaluation.py, prompts/assistant_answer_synthesis.txt, tests/test_assistant_obligations.py, tests/test_assistant_publishability.py, fixtures/assistant-events-v1 quality cases; quality report. All runtime/store integrations delivered as instructions to coordinator.
- Speed: assistant_latency.py, assistant_judgment_cache.py, tests/test_assistant_latency.py, tests/test_assistant_judgment_cache.py; call graph and timing report. Runtime/provider integrations delivered as instructions to coordinator.

No other files without coordinator reassignment. No two writers per file. Native child agents do not spawn descendants. All agents: no servers, browsers, full suites, e2e/Playwright; targeted offline tests only. Full-suite check is coordinator-only after heavy-job check. Reviewer tasks are read-only.
