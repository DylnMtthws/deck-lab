"""Query IR, executor, planner, narrator, threads, and the eval harness.

Sits **beside** :mod:`sabermetrics.cedh`, not inside it. It borrows the model
gateway, the cost ledger and the simulator client; it does not join the
deterministic generation path, and ``cedh/`` may not import it (ADR-030).

Two rules define what the model is allowed to be:

* **The model plans and narrates. It does not retrieve, rank, score or
  compute.** Every card in every answer is an ``oracle_id`` that came out of a
  query result set, so corpus-wide hallucination is prevented by construction
  rather than by prompt wording.
* **Every rendered assertion carries its tier and its citations.** Fact, field
  evidence, measurement, interpretation — see ADR-029. An interpretation with
  no supporting citation is dropped by the renderer.

May import ``mechanics``, ``substrate``, ``cedh.model_gateway``,
``cedh.cost_ledger``, ``cedh.simulator``, ``deck_documents`` and
``reference_layer``. May **not** import ``pipeline``, ``reasoning``,
``ingestion``, ``analytics``, or any vendor model SDK.

Two consequences of that list are easy to miss and are therefore stated:

* ``cedh.packs`` is **not** on it, so :mod:`sabermetrics.assistant.context`
  reads ``config/cedh_packs/*.yaml`` as data rather than importing the pack
  registry. The duplication is deliberate and is cross-checked by a test that
  imports both readers and asserts they agree.
* ``reference_layer.evidence`` reaches into ``db``, ``analytics`` and
  ``ingestion``. Only ``reference_layer.retriever`` and
  ``reference_layer.indexer`` are in scope.

Enforced by ``tests/test_package_boundaries.py``.
"""

from sabermetrics.assistant.context import (
    DeckContext,
    DeckContextRegistry,
    known_context_ids,
    resolve_deck_context,
)
from sabermetrics.assistant.envelope import (
    Coverage,
    PlanRun,
    StepNotRun,
    StepOutcome,
    StepResult,
)
from sabermetrics.assistant.executor import ResearchExecutor
from sabermetrics.assistant.ir import (
    PLAN_SCHEMA_VERSION,
    DeferredStepKindError,
    PlanValidationError,
    ResearchPlan,
    UnknownStepKindError,
    parse_plan,
    validate_plan,
)
from sabermetrics.assistant.sources import (
    AbsentRulesSource,
    BundleCardSource,
    CardSource,
    RulesSource,
    open_card_source,
)

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "AbsentRulesSource",
    "BundleCardSource",
    "CardSource",
    "Coverage",
    "DeckContext",
    "DeckContextRegistry",
    "DeferredStepKindError",
    "PlanRun",
    "PlanValidationError",
    "ResearchExecutor",
    "ResearchPlan",
    "RulesSource",
    "StepNotRun",
    "StepOutcome",
    "StepResult",
    "UnknownStepKindError",
    "known_context_ids",
    "open_card_source",
    "parse_plan",
    "resolve_deck_context",
    "validate_plan",
]
