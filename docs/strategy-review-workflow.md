# Attributed strategy review and development measurements

`scripts/strategy_review_tools.py` adds an offline workflow alongside existing
ingestion. It does not change the running assistant, old benchmark, frozen
heldout files, embeddings, or packaged source text. It uses no model APIs.

The packaged inventory currently contains 81 source sections/manifests: 14
official rules, one brief strategy quotation, 64 authored guidance entries,
one user-supplied primer, and one wiki source. Most strategy guidance is
explicitly AI-authored; legacy entries without author/review metadata remain
unknown. The user-supplied primer does not establish its author's identity or
expertise. The mechanical AI source review is not human review. Official rules
verify mechanics, not strategic quality. No verified expert strategy reviews
were supplied or added in this task.

## Review queue and import

Run from the repository root with the existing Python environment:

```sh
python scripts/strategy_review_tools.py inventory
python scripts/strategy_review_tools.py queue --manifest /path/source.json
python scripts/strategy_review_tools.py queue --manifest /path/source.json \
  --review /path/review.json --trusted-reviewers /path/trusted-reviewers.json
python scripts/strategy_review_tools.py import --manifest /path/source.json \
  --review /path/review.json --trusted-reviewers /path/trusted-reviewers.json \
  --db-path /path/qa.db
```

The source uses the existing `assistant_source_import.source_arguments` contract:
exact inline `text` or adjacent `content_file`, `sha256` of UTF-8 content,
`source_key`, `document`, `version`, `author`, `permission`, `permission_basis`,
and existing source metadata. The new workflow additionally requires explicit
`authorship`: `human`, `ai`, `mixed`, or `not_recorded`. Do not infer authorship
from review status. A human can review AI-authored text; that does not make its
authorship human. Open licenses require their license URL; author permission
and user authorization must actually exist. Permission declarations and
signatures do not themselves verify legal rights. No downloader is included.

Each review JSON is an envelope with `payload` and ASCII-armored SSH `signature`.
The payload has exactly these required fields (additional signed fields, such
as findings and evidence links, are allowed):

```json
{
  "reviewer_id": "operator-assigned-stable-identity",
  "reviewed_at": "2026-10-21",
  "scope": "nonland_mana",
  "decision": "approved",
  "manifest_sha256": "SHA256_FROM_QUEUE_OUTPUT"
}
```

Decisions are `approved`, `changes_requested`, or `rejected`. The date is ISO
calendar format. The hash binds the **whole source manifest**, including its
permission, author, content hash, version, source URL and section. Changing any
of them requires review of the new manifest. Existing exact chunk offsets and
source identity remain managed by the original ingestion module.

The reviewer signs canonical JSON bytes from
`strategy_review_workflow.canonical(payload)` (sorted keys, compact separators,
UTF-8, Unicode preserved, no trailing newline). With an existing reviewer key:

```sh
ssh-keygen -Y sign -f /path/to/reviewer_key \
  -n deck-lab-strategy-review-v1 /path/to/canonical-payload.json
```

Place the resulting `.sig` contents in the envelope's `signature`. Private keys
never enter source manifests or review receipts. Verification uses the local
`ssh-keygen`, an ephemeral allowed-signers file and a five-second timeout.

`trusted-reviewers.json` is an **operator-owned** mapping keyed by reviewer ID.
Each record needs `principal`, `public_key` (one SSH public-key line), and
`actor_kind` (`human` or `ai`). For scoped expert review, it also needs
`expert_scopes` (list of scope strings) and an attributable `expertise_basis`.
The operator must validate the person's identity and expertise before adding
the record. No example human identities, endorsements, or credentials are
provided. Source manifests cannot supply their own trusted identity registry.

Unknown/unsigned identities stay queued. Signed AI reviews remain AI reviews.
Signed human reviews without matching recorded expertise remain human reviews.
Only verified, approved human reviews can use the reviewed import command;
AI reviews and change requests cannot activate a reviewed source. Expert status
is scoped and requires the operator's trusted expertise record. This is not
independent verification of that expertise. The original importer still exists
and accepts legacy status text; it does not provide these guarantees.

The importer stores append-only, hash-addressed signed receipts in
`strategy_signed_reviews` and links each exact source's chunks to the receipt,
reviewer and scope in `provenance_json`. Another section with the same source
lineage/version is not promoted. Import never refreshes embeddings. Chunk
ingestion precedes receipt persistence; if the latter fails, the chunks remain
explicitly `signed_review_pending_receipt`, not expert-reviewed. Re-run import
after fixing the write failure. Use an operator-controlled QA database. This
workflow does not add revocation handling or prevent a legacy writer from
overwriting provenance; retain signed receipts and reverify trust when auditing.

## Development suite and quality/latency comparison

The separate frozen suite is in
`docs/evaluations/strategy-review-development-20261021`. It has ten development
cases for source versus Kinnan mana, commander absence, restricted resources,
immediate versus future output, cheap creature versus artifact development,
land untapping, mana storage, payoff prerequisites, and retaining useful
contribution/drawback explanations when a separate caution already exists.

Official primary sources were checked through bounded public reads:

- [Ikoria release notes, Kinnan](https://magic.wizards.com/en/news/feature/ikoria-lair-behemoths-and-commander-2020-edition-release-notes-2020-04-10)
- [Lord of the Rings release notes, Delighted Halfling](https://magic.wizards.com/en/news/feature/the-lord-of-the-rings-tales-of-middle-earth-release-notes)
- [March of the Machine release notes, Omnath and Portent Tracker](https://magic.wizards.com/en/news/feature/march-of-the-machine-release-notes)

Evidence paraphrases and explicit scenario premises are separate from labels.
The source verification is mechanical, not expert strategy endorsement or a
claim about card win rates. Historical notes are not a current legality check.
The suite's `RUBRIC.md` defines annotations and limitations. Files were frozen
before scorer implementation. The same AI authored the new data and scorer:
**independent evaluation authorship and expert review remain unfulfilled**.
An independent evaluation author must review or replace this development
extension before it can be presented as independent benchmark evidence.

```sh
python scripts/strategy_review_tools.py evaluate
python scripts/strategy_review_tools.py evaluate --receipts /path/actual.json
python scripts/strategy_review_tools.py evaluate --receipts /path/candidate.json \
  --baseline-receipts /path/baseline.json
```

No-receipt output is explicitly `no_actual_receipts`, with null accuracy,
latency and cost, zero coverage/fulfillment, and full explanation omission.
Capture actual final answer text and actual telemetry; annotate claims,
preservation and requested-topic spans against that exact text. The scorer
never derives observations from labels or answer substrings. Unknown IDs
receive no credit, absent claims reduce coverage, and free prose outside spans
remains unaudited. Source snapshots and supplied conditions verify expected
mechanics; strategic topic coverage is only an annotation proxy.

Latency comparisons pair identical frozen case IDs and show quality regressions
beside speed deltas. No combined score hides lost explanations or facts.
Missing costs stay null, not zero. The workflow makes no extra model calls and
requires no embeddings, but **no live latency reduction or quality improvement
has been measured**. Test-only synthetic receipts validate the evaluator, not
the assistant. The existing private runtime artifact uses different prompts;
it is not relabeled as a run of these new cases. Runtime fixes and a real
before/after capture remain work for the owning implementation task.

## Receipt binding and offline audit

Trusted registry records are schema-checked before assessment. `actor_kind` must
be `human` or `ai`; expertise scopes must be a JSON list of nonblank strings,
matched exactly without substring matching. A supplied `expertise_basis`, and
any record with expertise scopes, requires a nonblank string basis. Plain human
records may omit both expertise fields. Malformed records raise `ValueError`;
AI identities remain AI identities even with recorded expertise.

Each actual evaluation receipt must carry `suite_manifest_sha256`, captured
with the answer from the exact bytes of the development `manifest.json` in use
at capture time. Missing or mismatched digests raise `ValueError` before scoring.
Older unbound receipts must be recaptured; the evaluator never backfills the
current digest into them. Empty runs still report the evaluated suite digest
and retain `no_actual_receipts` status. A digest binds a claimed capture to a
suite; it does not authenticate the capture itself.

New signed import receipts retain the complete `source_manifest` object inside
the hash-addressed canonical receipt. Its canonical bytes reproduce the signed
manifest digest, including file-backed source metadata. No original source file
is needed for the subsequent audit:

```python
from pathlib import Path
from sabermetrics.strategy_review_workflow import audit_receipt

result = audit_receipt(Path("/path/qa.db"), receipt_sha256, trusted_reviewers)
```

This Python API opens the database read-only, checks the receipt hash, canonical
manifest digest and SSH signature against the **caller-supplied** trusted
registry, and checks ordered chunk offsets, chunk hashes, receipt links and the
reconstructed source's UTF-8 content hash. It never reads `content_file`, follows
URLs, loads embeddings, or calls models. Each audit addresses one receipt, with
limits of 8 MB for receipt/provenance data, 1 MB for source content and 1,000
chunks. Missing, altered or oversized data raises `ValueError`. Existing receipts
without a retained manifest return `legacy_unauditable`; nothing fills in a
manifest retroactively. Success is `binding_verified`, not an expert endorsement:
the audit does not promote provenance, certify accuracy, validate legal rights,
or independently establish the reviewer's expertise. Embedded trust/assessment
snapshots are historical records, not authority for the audit.
