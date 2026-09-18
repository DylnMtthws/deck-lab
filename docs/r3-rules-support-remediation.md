# Rules-support remediation: spec and record

What was decided after the ten-agent investigation (`r3-rules-support-synthesis.md`)
and the review of it, what was built, and what remains the owner's to rule on.
Written before the work; the "Result" section at the end is filled in after.

## The two rulings

**Ruling B — plan-shape decomposition: NO.** The two-step shape is frozen
through R3 and is now a test rather than a comment. Three independent
re-measurements found that every decomposition that adds no newly authored
text scores exactly what the single query scores (1/10; the synthesis's own
`clarified_ask` clause rule scores 0/10 because its fragments name cards), and
that semantically meaningless splits outscore principled ones (three equal
word-thirds: 4/10). The mechanism is query diversity plus effective budget,
not clause comprehension, so any gain from decomposition comes from text a
human writes with the key open. Decomposition is R4 planner scope, measured by
G3 against this frozen control — the model's contribution, isolated, which is
what spec §10.1's two-mode rig exists for.

**Ruling A — held-out set: YES, differently shaped, and not yet.** Twenty
single-proposition questions (one required rule each, no `sufficient_any_of`),
authored offline by the owner, sealed by hash commitment before any run, and
scored per proposition. Not ten: the Clopper-Pearson 95% interval on 1/10 is
[0.003, 0.445], and against a 0.10 baseline n=10 needs ≥7/10 before a difference
is significant. Not yet: three instrument defects below make any cross-corpus
number uninterpretable, and the corpus is about to change again.

## Why "not yet": three defects that need no judgement about any answer

Each acceptance condition below comes from the document, the model card or
arithmetic, and would be correct if every question passed.

### D1. Citations are wrong on a quarter of the index

Measured against the live 871-chunk index:

| | old chunker (366d1f3) | new chunker (fd4bafd) |
|---|---:|---:|
| chunks | 400 | 871 |
| Glossary/credits chunks cited as `CR 905.4a` | 1 | 83 |
| chunks that begin at rule X and are cited as rule Y | 0 | 131 |
| **unambiguously wrong citations** | **1 (0%)** | **214 (25%)** |

Rule 905.4a is *"Conspiracy cards with hidden agenda are put into the command
zone face down."* The Glossary entry for Evoke — the passage the rules-009
investigation was chasing — is now retrievable and cites that rule.
`sources.py:375` emits `rules:{section}` verbatim, so the string reaches the
envelope. The chunk that now contains 707.5 is cited `CR 707.2c`.

Cause: `_split_by_size` sets `current_section` on every rule match but only
flushes when `target_chars` is reached, so an accumulated run is labelled with
the **last** rule in it; text with no rule numbers (the Glossary, the credits)
inherits whatever label was current. The instrument is blind to this by design —
`rules_support.py` matches quote text, not labels — and the test written about
the Glossary path (`test_text_with_no_rule_numbers_is_still_bounded`) asserted
only that it split, not what it was called.

**Fix.** Rewrite `_split_by_size` so that:
- the rule-number splitter accepts the period form (`707.3.` as well as
  `707.2c`), which the synthesis asked for and which did not land;
- the rule number stays in the chunk body (a reader of a cited passage sees the
  number; `quote_variants` already matches both forms);
- a chunk is labelled with the rule its text **begins** with, and a
  continuation piece produced by the ceiling keeps that rule's label;
- the Glossary and credits are chunked separately, labelled `Glossary: <first
  term>` and `Credits`, never with a rule number.

**Acceptance.** A test over the pinned document: zero chunks whose leading rule
number differs from their label; zero chunks labelled with a rule number whose
content is glossary or credits; zero chunks over the encoder window (kept).
And a build-time refusal in `build_rules_index.py` that runs these checks
**before** `index_chunks()` — the one existing check fires after the generation
is already committed and active, so it can refuse the manifest but not the index.

### D2. The retrieval bound has the wrong unit

`rules_lookup.limit` counts chunks. The chunker fix halved chunk size, so at
`limit: 6` the old index returned ~12,600 characters per question and the new
one ~6,300. Scored against the same key, the fix reads as net negative (22/40
propositions → 20/40) at limit 6 and as real and large (14/40 → 20/40) at a
fixed 6,300 characters. There is no budget-invariant statement of the effect
because the metric declares no budget, and a held-out set sealed against this
bound inherits the confound.

**Fix.** `RulesLookupStep` gains `char_budget: int | None`. When set, the
executor takes ranked rows in order until the next would exceed the budget
(always at least one), and reports what it dropped in `Coverage`. The ten rules
plans set `char_budget: 6300` — the current operating point, chosen to equal
what the current plans return, not fitted to any rank — and raise `limit` to 12
so the chunk cap cannot bind before the budget. The shape test pins both.

This changes the plans, and is disclosed as such: the resulting number is a new
measurement under a bound whose unit can be compared across chunkings.

### D3. The freeze cannot tell two corpora apart

`FrozenBaseline` has no rules-index field and `evaluation_inputs_sha256` has no
corpus term, so a re-chunk with unchanged plans and labels produces the same
filename and `freeze_baseline.py` silently overwrites. The "new measurement, not
a comparison" rule lives in a `limitations` string that nothing reads and that
was already rewritten in place after freezing.

**Fix.** `evaluation_inputs_sha256` includes the rules index `generation_id`
read from the checked-in manifest (`fixtures/research/rules_index.json`), version
bumped to v3. `FrozenBaseline` records `rules_index_generation_id`,
`rules_index_chunk_count` and `rules_index_chunker_sha256`. Older baselines load
unchanged with those fields absent.

## Also in scope

- **Shape contract.** `tests/test_r3_plans.py` asserts exactly one
  `RulesLookupStep` per rules plan with `limit == 12` and `char_budget == 6300`.
  The header of `rules.yaml` records the ruling and discloses that the plans'
  `card_search.top_k` was cut from 50 to 3 in 893fbb under a rank-derived
  audit (`docs/r3-breadth-audit.md`) — the rules category's recall bound is
  rank-derived; its lookup queries are not.
- **Anti-cheat, two extensions.** `ir.py` refuses any `rules_lookup.question`
  containing a rule-number literal (`\d{3}\.\d+[a-z]?`), at parse time, beside
  the Oracle-id refusal. `plans.py` gains `plans_quoting_the_key`: a rules plan
  whose lookup text, note or intent shares a ≥6-word contiguous span with that
  question's `quoted_evidence` is reported in the scorecard and refuses an
  authoritative run. Measured margin: legitimate paraphrase peaks at 5 words,
  key-fitted queries score 34–51.
- **Per-proposition reporting.** `gates.rules_support` reports
  `propositions_total` / `propositions_covered` (required rules plus groups)
  and `chars_returned` per question alongside the per-question pass count.
- **Held-out plumbing.** `run_g2.py --questions DIR --rules-support-labels PATH`
  (both imply `--measured`; a held-out run is never the gate).
  `scripts/seal_heldout.py seal|verify` writes and checks
  `fixtures/research/heldout_commitment.yaml` — sha256 of the sealed file, its
  question count, the document `content_sha256` and the index `generation_id`.
  Hash commitment is the only seal available in a repo every agent can read; it
  proves the key predates the run, not that it is independent.
- **Label writer guard.** `write_rules_support_labels.py` refuses to overwrite a
  label file containing nested groups without `--force`; re-running it today
  silently reverts the four transcriptions.
- **Prose corrections.** `RESEARCH_ASSISTANT_SPEC.md` R3 section (stale 11/3/3
  diagnosis, "single flat disjunction", "2 to 4"); `diagnose_rules_support.py`'s
  "genuine gap … no window is wide enough" sentence (falsified: that bucket
  halves at depth 200 and the aggregate reaches 7/10 across the bound);
  `g2_blockers.yaml` records ruling B; the synthesis gets an annotation, not an
  edit, where lines 47 and 49 justify label decisions by observed rank — that is
  the one direction of contamination that must never happen.

## Not done here, and why

- **Paired old-vs-new corpus comparison as a checked-in script.** Valuable
  (it produced D2) but not needed to act on D2; the throwaway lives in the
  workflow transcript.
- **Owner-authored held-out questions.** By construction not mine to write.
- **The label rulings.** Positions, for the owner to say yes or no to:
  - *202.3 across rules-001/002/005* — require it in all three. rules-001's
    exclusion rests on a chunk-adjacency claim the critic verified as false.
  - *Chain of authority* — stop at the rule that carries scoreable content;
    demote 109.2b in rules-010 (112.1/112.3 carry it) and write that as policy.
  - *Co-located requirements* — a rule that cannot independently fail is not a
    requirement; move rules-005's 202.3a and rules-008's 605.3a to near-misses.
  - *rules-003* — drop 104.3e, add a group over {603.2b, 503.1a}.
  - Re-issue as a new label-set id after ruling.

## Result

Filled in after the re-index and re-run. See the end of this file.
