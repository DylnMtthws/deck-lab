import copy

import pytest

from sabermetrics.assistant_experiment_reuse import measurement_identity, reuse_decision


def report():
    return {
        "settings": {
            "engine_version": "v1",
            "policy": "p",
            "effects_sha256": "effects",
            "strategy_pack_sha256": "pack",
            "settings_fingerprint": "profile",
            "seed": 1,
        },
        "coverage": {"cards": [{"slot": 0, "status": "supported"}]},
    }


@pytest.mark.parametrize(
    "changed",
    [
        "engine_version",
        "policy",
        "effects_sha256",
        "strategy_pack_sha256",
        "settings_fingerprint",
        "seed",
        "coverage",
    ],
)
def test_any_provenance_drift_blocks_reuse(changed):
    old = report()
    new = copy.deepcopy(old)
    if changed == "coverage":
        new["coverage"]["cards"][0]["status"] = "partial"
    else:
        new["settings"][changed] = "different"
    decision = reuse_decision(
        measurement_identity(old),
        measurement_identity(new),
        same_deck=True,
        completed=True,
    )
    assert not decision["eligible"]


def test_exact_identity_is_descriptive_and_requires_current_deck_and_completion():
    identity = measurement_identity(report())
    assert reuse_decision(identity, identity, same_deck=True, completed=True)[
        "eligible"
    ]
    assert not reuse_decision(identity, identity, same_deck=False, completed=True)[
        "eligible"
    ]
    assert not reuse_decision(identity, identity, same_deck=True, completed=False)[
        "eligible"
    ]


def test_missing_provenance_is_not_equal_provenance():
    assert measurement_identity({"settings": {}, "coverage": {}}) is None
    assert not reuse_decision(None, None, same_deck=True, completed=True)["eligible"]
