# Completeness critique of the rules-support answer key

Written by the final agent of the labelling panel, over all ten finished
labels and the thirty adversarial reviews that shaped them. It read the pinned
Comprehensive Rules and ran the chunker; it was denied `.research-dev/`, the
review packet, the index database and the index manifest, like every other
agent in the panel.

**It is preserved verbatim, including where it is unflattering about the
schema it was reviewing.** Its central finding — that `sufficient_any_of` is a
single flat disjunction and that several scored rules cannot independently fail
because they share a chunk with another scored rule — is now a mechanical check
(`scripts/audit_rules_support_labels.py`), not a paragraph somebody has to
remember to read.

---

I verified independently against the pinned document only: all 61 `quoted_evidence` quotes, every cited anchor, the uniqueness claims each key rests on, and — because several keys reason about chunk boundaries — I ran `DocumentChunker().chunk_comprehensive_rules()` over `normalized.txt` to see where each quote actually lands. I did not open `.research-dev/`, `docs/r3-owner-review.md`, `data/research-indexes/`, `fixtures/research/rules_index.json`, or any scorecard/observation/g2 file.

## What is solid

The Magic is correct in all ten. I attacked each answer and could not flip one. Mechanically the keys are in far better shape than the review rounds suggest: **all 61 quoted_evidence quotes are verbatim substrings of the pinned document, all clear the 40-character floor, and all but one are unique document-wide.** The exception is `rules-008`'s 605.3a quote (183 chars, 2 occurrences), which is deliberately non-unique. No fabricated rule numbers survive anywhere. `quote_variants()` strips leading rule numbers, so `rules-010`'s number-prefixed quotes ("115.2. Only permanents…") match fine despite being the only key that kept them.

## The systematic defect: `sufficient_any_of` is a single flat disjunction, and four keys encode two groups in it

`support_verdict` computes `satisfied = not missing_required and (not sufficient_any_of or bool(covered_alternatives))`. One group. But **rules-004, rules-006, rules-009 and rules-010 each pack two independent either/or groups into that one list** and put "one from EACH group" in `sufficient_support` prose that no code reads. In every case, satisfying the easy group twice passes the label while a proposition the key itself declares mandatory goes unestablished.

Worse, in three labels the collapse is not merely possible but **automatic**, because the alternate shares a chunk with a required rule:

- **rules-009 is the worst label in the set.** Its Group A alternate 603.3a and its required 603.3b both live in chunk `CR 603.3b`. So covering the required rule *always* covers an alternate, the disjunction is *always* satisfied, and the Group B rules (115.1d / 115.1 / 603.3d) can never be reached. The clarified ask is "who orders … **and when targets are selected**," and the entire second half is unscorable. rules-009 reduces to a two-chunk test: `CR 702.74a` and `CR 603.3b`.
- **rules-010**: alternate 608.3a and required 608.2n are both in chunk `CR 608.3e`. Same automatic satisfaction, so P3 ("the effect now happens for you" — 608.2c/109.5) is never actually required.
- **rules-008**: alternate 605.1 and required 605.1a are both in chunk `CR 605.3b`. The exclusivity requirement — the key's own carefully argued point that 605.1a's "if" cannot prove a negative — is inert.
- **rules-006** is the one live case: nothing is co-located, so a set returning 707.5 + 614.12a + 614.1c genuinely passes with no availability rule at all. That set never establishes that the simultaneously entering artifact is absent from the battlefield, which *is* the question.

**rules-003** also has an inert any-of (603.7 and 603.7a sit in chunk `CR 603.7c` with required 603.7b), but there it is harmless — the proposition is effectively required anyway.

## rules-008's shared-quote trick backfires in a second way

The key deliberately narrowed the 605.3a quote to the 183-character span shared with 117.1d so either citation counts. I confirmed the span occurs exactly twice. But **117.1d and required 117.1b are in the same chunk (`CR 117.2c`)** — so covering 117.1b automatically "covers" 605.3a. Combined with the 605.1/605.1a collision above, `rules-008`'s apparent 3-required-plus-any-of structure collapses to **two chunks: `CR 117.2c` and `CR 605.3b`**. It is one of the two loosest labels in the set, not one of the stricter ones. It will also report `required_covered: ['605.3a']` when what came back was 117.1d — misleading provenance in the verdict.

## Effective strictness is not calibrated (2x spread)

Minimum distinct chunks a passage set must contain to pass:

| label | min chunks | notes |
|---|---|---|
| rules-005, rules-008, rules-009 | **2** | 005: 202.3 and 202.3a share chunk `CR 202.3d`, so its 3-rule conjunction is free. 008/009 loose by accident. |
| rules-001, rules-002, rules-004, rules-006 | 3 | rules-004 is 2 required + any 1 of **seven** commonplace chunks |
| rules-003, rules-007, rules-010 | **4** | rules-010 needs `CR 109.4b`, `CR 112`, `CR 115.1e`, `CR 608.3e` |

Any aggregate over these ten mixes a two-chunk bar with a four-chunk bar. rules-005's three required rules cost two chunks; rules-010's four cost four.

## Cross-question inconsistency: 202.3 (rules-001 vs rules-002 vs rules-005) — real and consequential

This is the pair you flagged and it does not survive scrutiny. 202.3 ("mana value = total amount of mana in its mana cost") is:

- **required in rules-002** (argued irreplaceable: 118.9c never links cost to value);
- **required in rules-005**, whose rationale explicitly repudiates retrieval-robustness arguments — "the key is supposed to encode sufficiency";
- **explicitly rejected from both required and any-of in rules-001**, on exactly the retrieval-robustness argument rules-005 rejects, plus the claim that 202.3 and 202.3g "sit twenty lines apart in the same block."

That last claim is false in practice. **202.3 is in chunk `CR 202.3d`; 202.3g is in chunk `CR 202.3g` — different chunks.** Meanwhile 202.3 and 202.3a (rules-005) *are* in the same chunk. So the base rule is required exactly where requiring it is free, and omitted exactly where it would discriminate. A retriever returning `CR 202.3g` and never `CR 202.3d` gets full credit on rules-001's mana-value half while failing rules-002 and rules-005 on the identical underlying proposition.

There is a defensible substantive asymmetry available (a one-symbol cost makes "contributes 1" nearly self-executing; {3}{U}{U} needs summation), but **no key states it** — rules-001 argues from chunk adjacency that is factually wrong. Either require 202.3 in rules-001, or state the single-symbol argument explicitly and note that rules-005 then over-specifies.

## Cross-question consistency: rules-007 vs rules-008 (mana abilities) — content consistent, bar is not

The rules content here is *correct and consistent*, and better than the reviews imply. rules-008 requires an exclusivity rule (605.1/605.5) because 605.1a is a sufficiency statement that cannot prove a negative; rules-007 does not, because it is proving a positive from 605.1b's matching "if". That asymmetry is right. rules-007 requires a form rule (603.1/113.3c) while rules-008 does not, which is right because rules-008's ask stipulates "activation" while rules-007's ask *is* the classification. 106.12 is required in rules-007 and a near miss in rules-008 — correct in both.

The inconsistency is the bar: **rules-007 demands four distinct chunks (`CR 106.6a`, `CR 605.3b`, `CR 605.5b`, plus one of `CR 603.2d`/`CR 113.6a`); rules-008 demands two.** Same rules neighbourhood, same card family, 2x difference. Both keys are internally reasonable; scored side by side they are not comparable.

## Conjunctions that would fail a genuinely good answer

- **rules-010 is the most over-demanding.** Four conjuncts, two of which (112.2 and 608.2n) carry the *same* proposition, P5. A set returning 109.2b + 112.2 + 608.2c + 110.2b answers the clarified ask completely and fails on 115.2 *and* 608.2n. Two of three reviewers called 608.2n over-specified and the key overrode both. Combined with its inert any-of, rules-010 is simultaneously the strictest and the loosest label — strict where it should not be, loose where it should not be.
- **rules-003's 104.3e is the likeliest single false negative.** It is a nine-word sentence ("An effect may state that a player loses the game.") with essentially zero differential content, and it forces retrieval of chunk `CR 104.3h` — a chunk about *ending the game* — for a question about Pact of Negation. The key itself had to rewrite its pass criterion into a purely *negative* one because the rule carries nothing. Requiring a fourth, topically distant chunk for a proposition nobody would get wrong is the classic case the over-specification lens exists to catch.
- **rules-003 also has a live sufficiency gap on the other side.** Its ask names "when it triggers," but 603.7b only gives *frequency* ("the next time its trigger event occurs"). The rule that makes "at the beginning of your next upkeep" a game event is 603.2b, which lives in chunk `CR 603.2d` and is required nowhere. Two reviewers flagged this; the key declined. That is a judgement call a human should ratify, not an error — but it means rules-003 is simultaneously over-demanding (104.3e) and under-demanding (603.2b), which is the worst combination.
- **rules-004 is the loosest label** — 2 required plus any 1 of 7, where 113.9 already nearly carries the whole "not a spell" half by itself. It will pass almost any plausible retrieval.

## Mechanical blockers before these can be loaded

`near_miss_rules` is typed as `list[NearMiss]` (rule / why≥20 / quote≥40) and `near_miss_quotes_required` defaults `True`. These ten sets deliver near misses as **prose strings**. Three concrete failures:

1. **rules-005 and rules-009 contain a near-miss entry naming a rule that is also scored.** rules-005's list has an entry beginning `"202.3 is required, but…"` while 202.3 is in `required_rules`; rules-009's has `"603.3a is in sufficient_any_of, NOT here…"` while 603.3a is in `sufficient_any_of`. `RulesSupportLabel.every_scored_rule_is_quoted` raises `{traps} both answer and do not` on exactly this. These are meta-commentary that leaked into the list.
2. **Compound entries** that cannot be one `NearMiss`: rules-001's `"601.2f / 601.2h"`, rules-004's `"602.2b + 601.2i"` and `"702.29a (cycling), 702.53a (transmute), 702.77a (reinforce)"`, rules-009's `"405.2 and 608.1"`.
3. **rules-001's near misses are paraphrases, not quotes.** I tested them: 119.4, 202.2d, 106.9, 118.7f and 702.150a (which contains a literal `...` ellipsis) are all **not verbatim**. A mistranscribed near-miss quote is reported as `near_misses_absent` — "the matcher looked and the trap was not retrieved" — for a check that never ran. rules-004, rules-005 and rules-006 have the same pattern in places (rules-005's 712.3a fragment fails on case alone). rules-002, rules-003, rules-007, rules-008, rules-009 and rules-010 mostly embed real verbatim sentences.

Also: none of the ten supplies the required `verification` field (`proposed_unreviewed` | `adversarially_reconciled`, no default).

## Weakest label sets, ranked

1. **rules-009** — its any-of is structurally guaranteed to be satisfied by a required rule, so half its own clarified ask ("when targets are selected") is unscorable. Two-chunk bar. Plus a scored-rule/near-miss collision.
2. **rules-010** — over-demanding conjunction (two conjuncts on one proposition, 115.2 contested by two of three reviewers) *and* an inert any-of that drops its declared-mandatory P3.
3. **rules-008** — clever shared-quote fix defeats itself; required 605.3a and the whole any-of are both inert. Effectively a two-chunk label presenting as a four-rule one.
4. **rules-003** — over-specified on 104.3e (distant chunk, near-zero content), under-specified on 603.2b ("when it triggers" has no carrier).
5. **rules-001** — the 202.3 decision is inconsistent with rules-002 and rules-005 and is defended with a chunk-adjacency claim I verified to be false; near misses are paraphrases.
6. **rules-006** — the one *live* two-group collapse: a passing set can omit the availability half entirely.
7. **rules-004** — loosest bar in the set (any 1 of 7); the confidence is already marked `medium` and the key admits the grouping problem.

`rules-002`, `rules-005` and `rules-007` are the three I would ratify with the fewest changes. rules-002 in particular is the cleanest: every quote verbatim and unique, a live two-member any-of that I confirmed spans two different chunks (`CR 601.2i` vs `CR 601.2b`), and no group collapse.

## What a human most needs to look at by hand

**rules-009 and rules-010 first** — both have a *provably* unscorable mandatory proposition, and neither is fixable inside the schema. They need either a nested-group schema change or a hard decision to promote one member of the orphaned group into `required_rules`.

**rules-008 second** — decide whether the shared-quote mechanism is acceptable given it now makes two of three required rules unfailable and reports a rule as covered that was not retrieved.

**The 202.3 question across rules-001 / rules-002 / rules-005** — needs one ruling applied to all three, not three independent judgement calls. The chunk evidence says the current split is backwards.

**rules-003's 104.3e and 603.2b** — a paired call: dropping 104.3e and adding 603.2b (or an any-of over {603.2b, 503.1a}) would make the label both less over-demanding and more sufficient.

Finally: because `sufficient_any_of` is a flat single disjunction, **five of the ten labels (003, 008, 009, 010, and the rules-005 202.3/202.3a pair) contain at least one scored rule that cannot independently fail.** That should be checked mechanically before ratification — a rule that cannot fail is not a requirement, and reporting it as `required_covered` overstates what was measured.