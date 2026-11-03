# Research Assistant retrieval roadmap

> Working-plan consolidation (2026-11-03): Current priorities, open acceptance gates and the disposition of this historical document are tracked in [deck-inference-v1.json](deck-inference-v1.json). Retain this document as historical evidence/technical reference; do not start a parallel backlog here. Historical implementation or QA claims below are not fresh deployment or quality validation.

The Research Assistant needs evidence that explains the current deck, relevant
rules and commander interactions, then ranks alternatives against the request.
The October 16 QA exercise returned four partial answers and eight failures.
Those exploratory results motivate the order below; they do not establish an
independent expertise qualification or measured benefits for these proposals.

## Current implementation scope

The owner selected options 1–4 on October 17: complete current-deck evidence,
rules with card rulings, commander engine relationships, and contextual ranking.
These extend the existing local card vectors and hybrid strategy retrieval.
Exact legality, constraints and adoption remain owned by existing code. Reserve
the last conversation call for an answer so retrieved evidence reaches the user.

DigitalOcean remains the deployment target. Prepare public indexes offline and
serve them inside the existing application. No additional paid service or model
training is needed for this phase. The shared Droplet needs disk headroom before
additional deployment artifacts are staged.

## Potential additions in priority order

Options 5–24 remain potential additions, not implementation commitments.

| Original option | Capability | Intended use and important condition |
| --- | --- | --- |
| 1 | Complete current-deck facts | Scan every library entry, retain matching cards and report incomplete detection. Selected. |
| 2 | Rules and card rulings | Retrieve source text, relevant definitions and exceptions with snapshot identity. Selected. |
| 3 | Commander engine relationships | Explain support links and what proposed cuts lose. Selected. |
| 4 | Contextual candidate ranking | Rank within the legal candidate pool by requested function, engine fit and dependencies. Selected. |
| 5 | Search fusion | Improve fusion of exact, lexical, semantic and relationship rankings while retaining independent channels. |
| 6 | Mechanical query expansion | Translate player language into multiple relevant mechanical searches without changing user intent. |
| 7 | Ability clause representations | Separate costs, effects, triggers, targets, restrictions and timing; retain original text. |
| 8 | Passage and parent retrieval | Retrieve precise passages together with surrounding context and referenced exceptions. |
| 9 | Functional substitutes | Compare timing, cost, repeatability and dependencies within the requested function. |
| 10 | Complete packages | Retrieve shared interactions, prerequisites and failure conditions before choosing joint cuts. |
| 11 | Expert-reviewed examples | Retrieve analogous reviewed decisions; keep historical advice separate from current facts. |
| 12 | Comparable complete decks | Match strategy, commander and preferences; use composition as evidence rather than copying popularity. |
| 13 | Conditional co-occurrence | Retain cohort, denominator, support, time window and uncertainty; associations are discovery leads. |
| 14 | Dependent searches | Decompose complex interactions into a bounded sequence of evidence lookups. |
| 15 | Corrective retrieval | Investigate contradictions between detector output and actual card text. |
| 16 | Counterevidence | Search for missing prerequisites, timing restrictions and reasons an interaction fails. |
| 17 | Experiment retrieval | Reuse results only with matching deck and experiment identity; preserve simulator limitations. |
| 18 | User memory | Retrieve owner-scoped preferences and rejected options; preferences are not game facts. |
| 19 | Change impact retrieval | Retrieve engine links, access routes and dependencies affected by every changed card. |
| 20 | Alternative diversity | Offer materially different useful choices rather than near-duplicates. |
| 21 | Multiple card representations | Compare Oracle, functional and prerequisite embeddings; generated descriptions remain inference. |
| 22 | Hypothetical document retrieval | Use generated effect descriptions to retrieve real sources; generated queries are never evidence. |
| 23 | Hierarchical guide retrieval | Search summaries then source sections once the strategy-guide corpus warrants it. |
| 24 | Global graph summaries | Explore broad archetype communities after local advice quality improves. |

## Evaluation and operational constraints

Use the existing twelve cases as development regressions and add unseen cases.
Measure answer completion, rules accuracy, useful discoveries, coherent packages,
cut quality, latency and cost separately. Detector zero means no supported match,
not proof that the deck lacks the function. A retrieved citation is not proof of
its interpretation. Generated annotations and user text cannot become official
rule text. New indexes carry hashes, source versions and explicit missing states.

## Research references

- [Contextual retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- [GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/)
- [Corrective RAG](https://arxiv.org/abs/2401.15884)
- [HyDE](https://arxiv.org/abs/2212.10496)
- [RAPTOR](https://arxiv.org/abs/2401.18059)
