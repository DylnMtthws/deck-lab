# Forge rules-engine adapter

Runs scripted rules scenarios inside the official **Forge 2.0.15** release
(`forge-gui-desktop-2.0.15-jar-with-dependencies.jar`, source tag
`forge-2.0.15` = commit `4ec5f1a2c32fa90ecb983a72b9eb47aa5c5d7676`). Nothing is
built from Forge source; a ~1,400-line bridge is compiled with `javac` against
the release jar.

| File | Role |
|---|---|
| `ForgeBridge.java` | Injects state with `forge.game.GameState` (the `.pzl` puzzle loader) and drives actions through Forge's own paths: `ComputerUtil.handlePlayingSpellAbility` → `CostPayment` → `MagicStack.add`, `MagicStack.resolveStack`, `GameAction.checkStateEffects` + `addAllTriggeredAbilitiesToStack` (the same loop as `PhaseHandler.checkStateBasedEffects`), and `PhaseHandler.mainLoopStep` for turns. Writes one JSON receipt line per scenario to stdout; Forge's own output goes to stderr. |
| `forge_adapter.py` | Validates requests, compiles the bridge when needed, runs one JVM per call (or per batch), records provenance (jar SHA-256, card-script SHA-256 and text), measured wall time and peak RSS. Also the entry point for the harness contract (`--contract rules-engine-adapter.v1 capabilities|run`). |
| `harness_contract.py` | `rules-engine-adapter.v1` for `scripts/benchmark_rules_engines.py`: fixture scenario → bridge request, Forge result → `rules-engine-receipt.v1` (no timings in the receipt; provenance goes to `forge-logs/*.forge.json`). |
| `run_forge_scenarios.py` + `scenarios.json` | 15 bridge scenarios (Dark Ritual paid/unpaid, Vivi + Curiosity with 3 opponents, Vivi activation limits, Bolt/Counterspell, Brainstorm order, commander tax, Sauron amass, turn advance) checked against hand-derived Oracle/CR expectations that are never sent to Forge. |
| `run_fixture_batch.py` | All harness fixture rules scenarios in ONE JVM, judged with the harness's own `validate_receipt` / `evaluate` and cross-checked against its `ReferenceKernel`. |
| `test_forge_adapter.py` | JVM-free tests: validation, auth gate, malformed/foreign bridge output, translation, normalization, harness validation of converted receipts. |

## What the bridge decides (and records)

Legality verdicts come from Forge predicates; each rejection names the
predicate in `reason_source`. The bridge only:

* looks objects up by name/zone/controller;
* applies choices the scenario fixes (mana colours via
  `specifyManaCombo`/`chooseColor`, optional triggers, trigger targets,
  put-back order, ring-bearer) through `PlayerControllerAi` overrides, each
  recorded in `decisions`;
* restricts casting to paying from the mana pool (the harness model gets mana
  from explicit `activate_mana` actions), tested with Forge's
  `ManaPool.payManaCostFromPool`, and fails loudly if payment tapped anything.

Every controller passes priority and declares no attackers, so unscripted
AI play cannot leak into a scenario.

## Running

Forge runs are heavy-slot work: the adapter refuses to start the JVM unless
`/tmp/decklab-mechanics-v3/forge-run-authorized` exists
(`FORGE_ADAPTER_AUTH_FILE` overrides the path). Default heap `-Xmx768m`
(`FORGE_BRIDGE_HEAP`), SerialGC, `user.home` redirected to
`/tmp/decklab-mechanics-v3/forge-home`.

```sh
PY=/Users/dylan/Projects/mtg/deck-lab/app/.venv/bin/python
$PY -I scripts/rules_engine_adapters/forge/run_forge_scenarios.py      # 1 JVM
$PY -I scripts/rules_engine_adapters/forge/run_fixture_batch.py        # 1 JVM
$PY -m pytest scripts/rules_engine_adapters/forge/test_forge_adapter.py -q   # no JVM

# Harness end-to-end (one JVM per scenario per repeat):
SABER_FORGE_HOME=/tmp/decklab-mechanics-v3/engines/forge \
SABER_FORGE_JAVA_HOME=/tmp/decklab-mechanics-v3/engines/jdk/jdk-17.0.20.1+1/Contents/Home \
SABER_FORGE_BRIDGE="$PY -I scripts/rules_engine_adapters/forge/forge_adapter.py --contract rules-engine-adapter.v1" \
$PY scripts/benchmark_rules_engines.py --engines reference,forge --repeat 1
```
