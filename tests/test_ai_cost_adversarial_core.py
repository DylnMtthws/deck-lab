"""Regression tests for defects found by the adversarial core review."""

from types import SimpleNamespace

import httpx
import pytest

from sabermetrics.ai_cost_control import (
    Authority,
    BudgetConfig,
    BudgetDenied,
    bind_turn,
)
from sabermetrics.cedh.provider_deepseek import DeepSeekGateway


def authority(tmp_path, **kw):
    return Authority(
        str(tmp_path / "adversarial.db"),
        BudgetConfig(
            prices=(("model", 1, 1),),
            credentials=(("main", "deepseek", "MAIN_REVIEW_KEY"),),
            authorized=True,
            **kw,
        ),
        local=True,
    )


def test_two_active_same_user_turns_both_spend(tmp_path):
    a = authority(tmp_path)
    a.begin_turn("main", "u", "deck1", "o")
    with pytest.raises(BudgetDenied):
        a.begin_turn("main", "u", "deck2", "o")


def test_no_threshold_alert_even_at_full_exposure(tmp_path):
    a = authority(tmp_path, monthly=100)
    t = a.begin_turn("main", "u", "k", "o")
    a.admit_attempt(t, "o", "model", 50, 50)
    assert {e["threshold"] for e in a.alerts()} >= {50, 80, 95}


@pytest.mark.parametrize("extra", [{"n": 10}, {"best_of": 10}, {"stream": True}])
def test_deepseek_multiple_choices_not_in_reservation(tmp_path, extra):
    a = authority(tmp_path)
    t = a.begin_turn("main", "u", "k", "o")
    sent = []

    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": []})

    gateway = object.__new__(DeepSeekGateway)
    gateway._model_id = "model"
    gateway._credential_name = "MAIN_REVIEW_KEY"
    gateway._settings = SimpleNamespace(
        model=SimpleNamespace(credential_env="HF_TOKEN", max_retries=0)
    )
    payload = {"model": "model", "messages": [], "max_tokens": 100, **extra}
    with httpx.Client(
        base_url="https://example.invalid", transport=httpx.MockTransport(transport)
    ) as c:
        gateway._client = c
        with bind_turn(a, t, "o"), pytest.raises(BudgetDenied):
            gateway._post(payload)
    assert not sent
    assert not a.status()["attempts"]


def test_anthropic_negative_cache_metadata_refunds(tmp_path):
    from sabermetrics.reasoning.client import AnthropicClient

    model = "claude-haiku-4-5"
    a = Authority(
        str(tmp_path / "anthropic.db"),
        BudgetConfig(
            prices=((model, 1, 1),),
            credentials=(("main", "anthropic", "MAIN_ANTHROPIC"),),
            authorized=True,
        ),
        local=True,
    )
    t = a.begin_turn("main", "u", "k", "o")
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=0,
            cache_read_input_tokens=-100,
            cache_creation_input_tokens=0,
        ),
        content=[],
        id="fake",
    )
    client = object.__new__(AnthropicClient)
    client._credential_name = "MAIN_ANTHROPIC"
    client._client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: response)
    )
    client.get_monthly_spend = lambda: 0
    client._log_cost = lambda *args: None
    with bind_turn(a, t, "o"):
        client.call_with_cache(
            model=model,
            system="system",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=10,
        )
    attempt = next(iter(a.status()["attempts"].values()))
    assert attempt["bound"] > 0
    assert attempt["charge"] == attempt["bound"]
    assert not attempt["settled"]


def test_provider_caps_actual_output_before_reservation(tmp_path):
    import json

    a = authority(tmp_path)
    tid = a.begin_turn("main", "u", "output-cap", "owner")
    sent = []
    gateway = object.__new__(DeepSeekGateway)
    gateway._model_id = "model"
    gateway._credential_name = "MAIN_REVIEW_KEY"
    gateway._settings = SimpleNamespace(
        model=SimpleNamespace(
            credential_env="HF_TOKEN", max_retries=0, timeout_seconds=60
        )
    )

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"usage": {"prompt_tokens": 100, "completion_tokens": 5}}
        )

    with httpx.Client(
        base_url="https://example.invalid", transport=httpx.MockTransport(transport)
    ) as client:
        gateway._client = client
        payload = {"model": "model", "messages": [], "max_tokens": 2048}
        with bind_turn(a, tid, "owner"):
            gateway._post(payload)
    assert sent[0]["max_tokens"] == 2000
    assert payload["max_tokens"] == 2048
    attempt = next(iter(a.status()["attempts"].values()))
    assert attempt["charge"] == 105
    assert (
        attempt["bound"] == len(json.dumps(sent[0], ensure_ascii=False).encode()) + 2000
    )
