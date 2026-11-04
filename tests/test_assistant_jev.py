import json

import httpx
import pytest

from sabermetrics.assistant_jev import run_trial, shadow_candidates, trial_request


def response_for(request):
    answers = {}
    for key, question in request["questions"].items():
        if question["type"] == "score":
            # Score is on the ordered level scale, not a probability.
            answers[key] = {
                "type": "score",
                "score": 2.4,
                "confidence": 0.7,
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.6, "3": 0.4},
            }
        else:
            answers[key] = {
                "type": "choice",
                "choice": "not_established",
                "confidence": 0.9,
                "probabilities": {
                    "supports": 0.0,
                    "contradicts": 0.0,
                    "not_established": 1.0,
                },
            }
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 200, "output_tokens": 30},
    }


def test_no_credential_and_disabled_shadow_never_call_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SABER_ASSISTANT_JEV_SHADOW", raising=False)
    monkeypatch.setattr(
        httpx.Client, "post", lambda *a, **k: pytest.fail("Unexpected network")
    )
    assert shadow_candidates("draw", [])["status"] == "disabled"
    assert run_trial(trial_request("draw", []))["reason"] == "missing_credential"


def test_independent_questions_batched_without_private_metadata():
    request = trial_request(
        "draw",
        [
            {
                "name": "Source",
                "oracle_text": "{T}: Draw a card.",
                "owner_id": "never-send",
                "credentials": "never-send",
            }
        ],
        [{"claim": "It draws.", "source": "Draw a card.", "category": "printed_fact"}],
    )
    assert set(request["questions"]) == {"fit_0", "friction_0", "claim_0"}
    assert "never-send" not in json.dumps(request)


def test_one_request_preserves_usage_distribution_and_unknown_price(monkeypatch):
    monkeypatch.delenv("TYPESAFE_INPUT_USD_PER_MILLION", raising=False)
    request = trial_request(
        "draw", [{"name": "Source", "oracle_text": "{T}: Draw a card."}]
    )
    calls = []

    def handle(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer private-fixture-key"
        return httpx.Response(200, json=response_for(request))

    result = run_trial(
        request, api_key="private-fixture-key", transport=httpx.MockTransport(handle)
    )
    assert len(calls) == 1
    assert result["status"] == "judged"
    assert result["answers"]["fit_0"]["score"] == 2.4
    assert result["configured_cost_usd"] is None
    assert result["usage"]["input_tokens"] == 200
    assert "private-fixture-key" not in json.dumps(result)


@pytest.mark.parametrize(
    "failure", ["timeout", "http", "missing", "probabilities", "nan", "choice"]
)
def test_failures_are_unknown_without_retry_or_payload_leaks(failure):
    request = trial_request(
        "draw",
        [{"name": "Source"}],
        [{"claim": "A claim", "source": "A source", "category": "printed_fact"}],
    )
    calls = []

    def handle(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("private response")
        if failure == "http":
            return httpx.Response(401, text="private response")
        data = response_for(request)
        if failure == "missing":
            data["answers"].pop("fit_0")
        elif failure == "probabilities":
            data["answers"]["fit_0"]["probabilities"] = {"0": 1.0}
        elif failure == "nan":
            data["answers"]["fit_0"]["score"] = "NaN"
        else:
            data["answers"]["claim_0"]["choice"] = "verified"
        return httpx.Response(200, json=data)

    result = run_trial(request, api_key="key", transport=httpx.MockTransport(handle))
    assert len(calls) == 1
    assert result["status"] == "unavailable"
    assert "private response" not in json.dumps(result)
    assert result["configured_cost_usd"] is None
