"""Deployment model prices stay finite, nonnegative and ledger-visible."""

import pytest

from sabermetrics.cedh.cost_ledger import estimate_cost
from sabermetrics.cedh.settings import load_cedh_settings

ENV_NAMES = (
    "CEDH_MODEL_INPUT_PRICE",
    "CEDH_MODEL_OUTPUT_PRICE",
    "CEDH_MODEL_CACHED_INPUT_PRICE",
)


def test_valid_deployment_prices_feed_shared_cost_estimate(tmp_path, monkeypatch):
    for name, value in zip(ENV_NAMES, ("0.2", "0.6", "0.2"), strict=True):
        monkeypatch.setenv(name, value)
    settings = load_cedh_settings(tmp_path / "missing.yaml")
    assert settings.model.pricing.input == 0.2
    assert settings.model.pricing.output == 0.6
    assert settings.model.pricing.cached_input == 0.2
    assert (
        estimate_cost(
            settings.model.pricing,
            input_tokens=1_000_000,
            cached_input_tokens=0,
            output_tokens=1_000_000,
            reasoning_tokens=0,
        )
        == 0.8
    )


@pytest.mark.parametrize("env_name", ENV_NAMES)
@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "not-a-price", ""])
def test_invalid_deployment_prices_are_rejected(tmp_path, monkeypatch, env_name, value):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValueError, match=env_name):
        load_cedh_settings(tmp_path / "missing.yaml")


def test_no_override_preserves_configured_prices(tmp_path, monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "cedh.yaml"
    config.write_text(
        "model:\n  pricing:\n    input: 1.0\n    output: 2.0\n    cached_input: 0.5\n"
    )
    pricing = load_cedh_settings(config).model.pricing
    assert (pricing.input, pricing.output, pricing.cached_input) == (1.0, 2.0, 0.5)
