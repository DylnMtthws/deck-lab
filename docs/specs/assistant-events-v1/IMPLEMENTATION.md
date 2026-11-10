# Approved assistant implementation status

Branch feat/assistant-events-v1-20261110, based on 454614ff. Local changes only.

Implemented: durable bounded journal and paged snapshots; additive migration and
non-journaling-writer rebaseline; exact run targeting; shared-deadline restart;
explicit lifecycle/fulfillment/evidence separation; activity/timer/recovery UI;
source-rendered relevant early findings; planner obligations without a new call;
content-free latency hooks; bounded exact committed-review reuse seam.

Final prose is retained with review limitations. The legacy runtime does not
supply exact final-prose-to-claim mappings, so final answer blocks remain
unverified and inline citation relationships are not fabricated. Existing
workspace evidence remains inspectable. Expert-source acquisition and stronger
model escalation remain outside this implementation.

Offline checks are deterministic policy/contract/integration evidence. They do
not establish actual p50/p95, better strategic usefulness, or cost improvements.
Paid paired evaluation and QA/production deployment remain separately authorized
steps. No server, browser, external data ingestion or paid model experiment was
started by this implementation.

Validation recorded in `validation/checks.json`: the full offline suite passed
5232 tests (28 skipped, 12 explicitly excluded live/model/Postgres/simulator
cases). Final executor-rejection and UI refinements then passed 84 focused tests.
Full-source mypy/Ruff/Black, JavaScript syntax and whitespace checks pass. Existing
hermetic intelligence and development/heldout strategy regression scripts pass;
reports are retained in `validation/`. These do not establish real-user quality,
provider latency or spend improvements. Claude's independent review is pending.

The public projection uses a conservative 5-MiB admission guard (below the
approved 7.5-MiB ceiling) to guarantee 64 bounded pages despite indivisible
entity packing. Oversize findings produce an explicit unavailable placeholder,
not clipped conditions or a false first-useful-content milestone.
