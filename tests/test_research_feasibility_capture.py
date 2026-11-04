"""Synthetic AI-authored development fixtures; no independent expertise claims."""

import json

import pytest

from sabermetrics.assistant_expertise import digest
from sabermetrics.research_feasibility_capture import (
    Attempt,
    Manifest,
    capture,
    main,
    replay,
)


def fixture():
    data = {
        "version": "research-feasibility-manifest.v1",
        "run_id": "anchor",
        "frozen_at": "2026-11-03T00:00:00Z",
        "protocol_sha256": "a" * 64,
        "evidence_kind": "development_diagnostic",
        "lane": "single_arm_feasibility",
        "arm_configurations": {},
        "systems_level_setting_differences": [],
        "cost_cap_usd": 1.0,
        "slots": [
            {
                "id": "s1",
                "pair_id": "p1",
                "case_id": "anchor",
                "case_manifest_sha256": "b" * 64,
                "source_snapshot_sha256": "c" * 64,
                "arm": "candidate",
                "sequence_position": 1,
                "provider_id": "offline-fixture",
                "effective_model_settings": {
                    "temperature": 0.0,
                    "max_output_tokens": 2048,
                    "timeout_seconds": 25.0,
                    "reasoning_effort": None,
                    "seed": None,
                },
                "cache_state": "cold",
                "identity": {
                    "revision": "d" * 40,
                    "model_id": "fixture",
                    "config_sha256": "e" * 64,
                    "corpus_sha256": "c" * 64,
                    "contributor_ids": ["ai-fixture"],
                },
            }
        ],
    }
    data["arm_configurations"] = {
        "candidate": {
            k: data["slots"][0][k]
            for k in (
                "identity",
                "provider_id",
                "effective_model_settings",
                "cache_state",
            )
        }
    }
    data["arm_configurations"]["candidate"]["max_retries"] = 1
    manifest = Manifest.model_validate(data)
    attempt = dict(
        id="a1",
        manifest_sha256=digest(manifest),
        slot_id="s1",
        slot_sha256=digest(manifest.slots[0]),
        retry_index=0,
        started_at="2026-11-03T00:01:00Z",
        finished_seconds=3.0,
        terminal_status="timeout",
        error_class="TimeoutError",
        stage_times={"provider": 3.0},
        tokens={"input": 12, "output": None, "reasoning": None},
        configured_cost_usd=None,
        cleanup_status="unknown",
        fragments=[
            {"kind": "source", "published_seconds": 1.0, "text": "source quote"}
        ],
        evidence_receipts=[{"source": "fixture", "text": "source quote"}],
    )
    return manifest, attempt


def test_failures_and_retries_retained_without_truth_credit():
    manifest, first = fixture()
    second = dict(
        first,
        id="a2",
        retry_index=1,
        started_at="2026-11-03T00:01:03Z",
        terminal_status="delivered",
        error_class=None,
        answer={"text": "Unreviewed explanation", "citations": []},
    )
    second["fragments"] = first["fragments"] + [
        {
            "kind": "explanation",
            "published_seconds": 2.0,
            "text": "Unreviewed explanation",
        }
    ]
    result = capture(manifest, [Attempt.model_validate(a) for a in (first, second)])
    assert replay(result) == {**result, "verification": "structural_only"}
    body = result["capture"]
    assert body["counts"] == dict(
        scheduled=1,
        attempted_slots=1,
        attempts=2,
        delivered=1,
        delivered_within_budget=1,
        protocol_violation_attempts=0,
        unassessed=2,
        unattempted_slots=0,
        delivery_denominator_slots=1,
        missing_token_values=4,
        unknown_cost_attempts=2,
    )
    assert body["attempts"][0]["error_class"] == "TimeoutError"
    assert body["attempts"][0]["finished_seconds"] == 3
    assert body["attempts"][0]["first_explanation_fragment_seconds"] is None
    assert body["attempts"][1]["first_explanation_fragment_seconds"] == 2
    assert all(
        a["quality"] is None and a["useful_explanation_seconds"] is None
        for a in body["attempts"]
    )
    assert body["attempts"][1]["answer_sha256"] == digest(second["answer"])
    assert body["attempts"][0]["evidence_receipt_sha256"] == digest(
        first["evidence_receipts"]
    )
    assert len(body["answer_export"]) == 1
    assert body["qualification"]["compatible_run"] is False


@pytest.mark.parametrize(
    "field",
    [
        "protocol_sha256",
        "case_manifest_sha256",
        "source_snapshot_sha256",
        "revision",
        "model_id",
        "effective_model_settings",
    ],
)
def test_frozen_identity_mismatch(field):
    manifest, attempt = fixture()
    data = manifest.model_dump()
    if field == "protocol_sha256":
        data[field] = "f" * 64
    elif field in ("revision", "model_id"):
        data["slots"][0]["identity"][field] = "f" * 40
    else:
        data["slots"][0][field] = (
            {} if field == "effective_model_settings" else "f" * 64
        )
    with pytest.raises(ValueError):
        capture(Manifest.model_validate(data), [Attempt.model_validate(attempt)])


@pytest.mark.parametrize("rehash", [False, True])
def test_tamper_detection_even_if_envelope_rehashed(rehash):
    manifest, attempt = fixture()
    result = capture(manifest, [Attempt.model_validate(attempt)])
    result["capture"]["attempts"][0]["evidence_receipts"][0]["text"] = "edited"
    if rehash:
        result["capture_sha256"] = digest(result["capture"])
    assert replay(result) == {"status": "invalid"}


@pytest.mark.parametrize(
    "change",
    [
        dict(retry_index=1),
        dict(slot_id="unknown"),
        dict(started_at="2026-11-02T00:00:00Z"),
    ],
)
def test_invalid_attempt_references(change):
    manifest, attempt = fixture()
    with pytest.raises(ValueError):
        capture(manifest, [Attempt.model_validate(dict(attempt, **change))])


def test_chronology_and_failure_contracts():
    _, attempt = fixture()
    for change in (
        dict(finished_seconds=0.5),
        dict(error_class=None),
        dict(terminal_status="delivered"),
        dict(finished_seconds=float("nan")),
    ):
        with pytest.raises(ValueError):
            Attempt.model_validate(dict(attempt, **change))


def test_empty_schedule_capture_and_cli_no_write_default(tmp_path):
    manifest, _ = fixture()
    frozen = tmp_path / "manifest.json"
    attempts = tmp_path / "attempts.json"
    frozen.write_text(manifest.model_dump_json())
    attempts.write_text("[]")
    args = ["--manifest", str(frozen), "--attempts", str(attempts)]
    assert main(args) == 0
    assert len(list(tmp_path.iterdir())) == 2
    output = tmp_path / "capture.json"
    assert main(args + ["--output", str(output)]) == 0
    assert (
        replay(json.loads(output.read_text()))["capture"]["counts"]["unattempted_slots"]
        == 1
    )
    original = output.read_bytes()
    assert main(args + ["--output", str(output)]) == 1
    assert output.read_bytes() == original
    assert main(["--replay", str(output)]) == 0


def test_duplicate_attempts_and_derived_count_tamper():
    manifest, raw = fixture()
    attempt = Attempt.model_validate(raw)
    with pytest.raises(ValueError, match="chronology|Duplicate"):
        capture(manifest, [attempt, attempt])
    result = capture(manifest, [attempt])
    result["capture"]["counts"]["delivered"] = 1
    result["capture_sha256"] = digest(result["capture"])
    assert replay(result) == {"status": "invalid"}


def test_delivered_cannot_be_retried_and_overlap_rejected():
    m, raw = fixture()
    delivered = dict(
        raw,
        terminal_status="delivered",
        error_class=None,
        answer={"text": "Final differs from fragment", "citations": []},
    )
    later = dict(raw, id="a2", retry_index=1, started_at="2026-11-03T00:02:00Z")
    with pytest.raises(ValueError, match="delivered"):
        capture(m, [Attempt.model_validate(a) for a in (delivered, later)])
    later["started_at"] = raw["started_at"]
    with pytest.raises(ValueError, match="chronology"):
        capture(m, [Attempt.model_validate(a) for a in (raw, later)])


def test_external_anchors_detect_self_consistent_deletion():
    m, raw = fixture()
    original = capture(m, [Attempt.model_validate(raw)])
    deleted = capture(m, [])
    assert replay(deleted)["verification"] == "structural_only"
    anchors = dict(
        expected_manifest_sha256=digest(m),
        expected_capture_sha256=original["capture_sha256"],
    )
    assert replay(original, **anchors)["verification"] == "externally_pinned_envelope"
    assert replay(deleted, **anchors) == {"status": "invalid"}
    assert replay(original, expected_protocol_sha256="f" * 64) == {"status": "invalid"}


@pytest.mark.parametrize(
    "value", [None, [], {}, {"capture": [], "capture_sha256": "x"}]
)
def test_malformed_replay(value):
    assert replay(value) == {"status": "invalid"}


def test_missing_telemetry_and_cost_cap():
    m, raw = fixture()
    body = capture(m, [Attempt.model_validate(raw)])["capture"]
    assert body["cost"]["cap_status"] == "unknown"
    assert body["attempts"][0]["missing_telemetry"] == [
        "output",
        "reasoning",
        "configured_cost_usd",
    ]
    for cost, status in [(0.2, "unknown"), (2.0, "fail")]:
        body = capture(
            m, [Attempt.model_validate(dict(raw, configured_cost_usd=cost))]
        )["capture"]
        assert body["cost"]["cap_status"] == status
    raw["tokens"].pop("reasoning")
    with pytest.raises(ValueError):
        Attempt.model_validate(raw)


@pytest.mark.parametrize("error", ["Authorization: Bearer secret", "x/y", "x" * 65])
def test_sanitized_errors(error):
    _, raw = fixture()
    with pytest.raises(ValueError):
        Attempt.model_validate(dict(raw, error_class=error))


@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": "secret"},
        {"authorization": "secret"},
        {"temperature": 3.0},
        {"max_output_tokens": -1},
    ],
)
def test_settings_allowlist(settings):
    m, _ = fixture()
    data = m.model_dump()
    data["slots"][0]["effective_model_settings"].update(settings)
    with pytest.raises(ValueError):
        Manifest.model_validate(data)


def comparison():
    import copy

    m, _ = fixture()
    data = m.model_dump()
    data["lane"] = "paired_comparison"
    base = copy.deepcopy(data["arm_configurations"]["candidate"])
    base["identity"]["revision"] = "f" * 40
    data["arm_configurations"]["baseline"] = base
    first = data["slots"][0]
    data["slots"] = []
    for i, arm in enumerate(["candidate", "baseline", "baseline", "candidate"], 1):
        slot = copy.deepcopy(first)
        slot.update(
            id=f"s{i}",
            pair_id=f"p{(i + 1) // 2}",
            sequence_position=i,
            arm=arm,
            identity=copy.deepcopy(data["arm_configurations"][arm]["identity"]),
        )
        data["slots"].append(slot)
    return data


def test_balanced_comparison_and_frozen_conditions():
    import copy

    data = comparison()
    Manifest.model_validate(data)
    mutations = [
        lambda d: d["slots"].pop(),
        lambda d: d["slots"][0].update(sequence_position=9),
        lambda d: d["slots"][1].update(provider_id="other"),
        lambda d: d["slots"][1].update(cache_state="warm"),
        lambda d: d["slots"][1]["effective_model_settings"].update(
            max_output_tokens=100
        ),
        lambda d: d["slots"][2].update(source_snapshot_sha256="f" * 64),
        lambda d: d["slots"][2]["identity"].update(revision="a" * 40),
    ]
    for mutation in mutations:
        changed = copy.deepcopy(data)
        mutation(changed)
        with pytest.raises(ValueError):
            Manifest.model_validate(changed)
    data["slots"][1]["effective_model_settings"]["max_output_tokens"] = 100
    data["slots"][2]["effective_model_settings"]["max_output_tokens"] = 100
    data["arm_configurations"]["baseline"]["effective_model_settings"][
        "max_output_tokens"
    ] = 100
    data["systems_level_setting_differences"] = ["max_output_tokens"]
    Manifest.model_validate(data)


def test_identical_builds_and_unbalanced_order_rejected():
    import copy

    data = comparison()
    data["arm_configurations"]["baseline"] = copy.deepcopy(
        data["arm_configurations"]["candidate"]
    )
    for slot in data["slots"]:
        slot["identity"] = copy.deepcopy(
            data["arm_configurations"][slot["arm"]]["identity"]
        )
    with pytest.raises(ValueError, match="distinct"):
        Manifest.model_validate(data)
    data = comparison()
    data["slots"][2]["arm"] = "candidate"
    data["slots"][3]["arm"] = "baseline"
    for slot in data["slots"]:
        slot["identity"] = copy.deepcopy(
            data["arm_configurations"][slot["arm"]]["identity"]
        )
    with pytest.raises(ValueError, match="balanced"):
        Manifest.model_validate(data)
    data = comparison()
    data["slots"][1]["identity"]["model_id"] = "other"
    data["slots"][2]["identity"]["model_id"] = "other"
    data["arm_configurations"]["baseline"]["identity"]["model_id"] = "other"
    with pytest.raises(ValueError, match="provider/model"):
        Manifest.model_validate(data)


def test_recursive_input_and_cli_parse_error(tmp_path, capsys):
    recursive = {}
    recursive["loop"] = recursive
    assert replay({"capture": recursive, "capture_sha256": "a" * 64}) == {
        "status": "invalid"
    }
    path = tmp_path / "deep.json"
    path.write_text("[" * 2000 + "]" * 2000)
    assert main(["--replay", str(path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "invalid"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_id", "other"),
        ("cache_state", "warm"),
        ("temperature", 1.0),
        ("max_output_tokens", 4096),
    ],
)
def test_arm_pins_across_pairs(field, value):
    data = comparison()
    data["systems_level_setting_differences"] = ["max_output_tokens"]
    for slot in data["slots"][2:]:
        target = (
            slot["effective_model_settings"]
            if field in ("temperature", "max_output_tokens")
            else slot
        )
        target[field] = value
    with pytest.raises(ValueError, match="arm configuration"):
        Manifest.model_validate(data)


def test_budget_observations_transport_and_deadline():
    m, raw = fixture()
    data = m.model_dump()
    data["arm_configurations"]["candidate"].update(
        max_retries=0, overall_observation_deadline_seconds=10
    )
    m = Manifest.model_validate(data)
    raw.update(manifest_sha256=digest(m), configured_cost_usd=0.1)
    second = dict(
        raw,
        id="a2",
        retry_index=1,
        started_at="2026-11-03T00:01:03Z",
        finished_seconds=900,
        terminal_status="delivered",
        error_class=None,
        answer={"text": "late", "citations": []},
        configured_cost_usd=0.2,
    )
    body = capture(m, [Attempt.model_validate(raw), Attempt.model_validate(second)])[
        "capture"
    ]
    assert body["counts"]["delivered"] == 1
    assert body["counts"]["delivered_within_budget"] == 0
    assert body["attempts"][1]["protocol_violations"] == [
        "retry_budget_exceeded",
        "overall_observation_deadline_exceeded",
    ]
    assert body["attempts"][0]["timeout_consistency"] == "unknown"
    data["cost_cap_usd"] = 0.3
    m = Manifest.model_validate(data)
    for a in (raw, second):
        a["manifest_sha256"] = digest(m)
    assert (
        capture(m, [Attempt.model_validate(raw), Attempt.model_validate(second)])[
            "capture"
        ]["cost"]["cap_status"]
        == "pass"
    )
    data["arm_configurations"]["candidate"].update(
        overall_observation_deadline_seconds=None
    )
    m = Manifest.model_validate(data)
    second.update(manifest_sha256=digest(m), retry_index=0)
    body = capture(m, [Attempt.model_validate(second)])["capture"]
    assert body["counts"]["delivered_within_budget"] == 1


def test_duration_and_stage_label_bounds():
    _, raw = fixture()
    for change in (
        {"finished_seconds": 1e308},
        {"stage_times": {"Authorization Bearer key": 1}},
    ):
        with pytest.raises(ValueError):
            Attempt.model_validate(dict(raw, **change))


def test_capture_cli_anchors(tmp_path):
    m, raw = fixture()
    mp, ap = tmp_path / "manifest.json", tmp_path / "attempts.json"
    mp.write_text(m.model_dump_json())
    ap.write_text(json.dumps([raw]))
    base = ["--manifest", str(mp), "--attempts", str(ap)]
    assert main(base + ["--expected-manifest-sha256", "f" * 64]) == 1
    assert main(base + ["--expected-protocol-sha256", "f" * 64]) == 1
    assert main(base + ["--expected-capture-sha256", "f" * 64]) == 1
    assert (
        main(
            base
            + [
                "--expected-manifest-sha256",
                digest(m),
                "--expected-protocol-sha256",
                m.protocol_sha256,
            ]
        )
        == 0
    )


def test_single_arm_repeat_pins_and_partial_cost():
    import copy

    m, raw = fixture()
    data = m.model_dump()
    repeat = copy.deepcopy(data["slots"][0])
    repeat.update(id="s2", pair_id="p2", sequence_position=2)
    data["slots"].append(repeat)
    repeat["effective_model_settings"]["temperature"] = 1
    with pytest.raises(ValueError, match="arm configuration"):
        Manifest.model_validate(data)
    repeat["effective_model_settings"]["temperature"] = 0
    data["cost_cap_usd"] = 0.3
    m = Manifest.model_validate(data)
    raw.update(
        manifest_sha256=digest(m),
        terminal_status="delivered",
        error_class=None,
        answer={"text": "observed", "citations": []},
        configured_cost_usd=0.1,
    )
    cost = capture(m, [Attempt.model_validate(raw)])["capture"]["cost"]
    assert cost["spent_so_far_cap_status"] == "pass"
    assert cost["cap_status"] == "unknown"
    raw["configured_cost_usd"] = 0.4
    assert (
        capture(m, [Attempt.model_validate(raw)])["capture"]["cost"]["cap_status"]
        == "fail"
    )


@pytest.mark.parametrize(
    "field,value", [("max_retries", 50), ("overall_observation_deadline_seconds", 3600)]
)
def test_paired_budget_confound_requires_fixed_declaration(field, value):
    data = comparison()
    data["arm_configurations"]["candidate"].update(
        max_retries=50, overall_observation_deadline_seconds=3600
    )
    data["arm_configurations"]["baseline"].update(
        max_retries=0, overall_observation_deadline_seconds=5
    )
    with pytest.raises(ValueError, match="Undeclared"):
        Manifest.model_validate(data)
    data["systems_level_setting_differences"] = [field]
    with pytest.raises(ValueError, match="Undeclared"):
        Manifest.model_validate(data)
    data["systems_level_setting_differences"] = [
        "max_retries",
        "overall_observation_deadline_seconds",
    ]
    m = Manifest.model_validate(data)
    body = capture(m, [])["capture"]
    assert (
        body["systems_level_confound_disclosure"]["fixed_declared_differences"]
        == data["systems_level_setting_differences"]
    )
    assert m.arm_configurations["baseline"].max_retries == 0
    assert m.arm_configurations["candidate"].model_dump()[field] == value


@pytest.mark.parametrize("duration,complete", [(9, False), (10, True), (11, True)])
@pytest.mark.parametrize("cost,status", [(None, "unknown"), (0.1, "pass"), (2, "fail")])
def test_deadline_closes_failed_slot_without_excess_retries(
    duration, complete, cost, status
):
    m, raw = fixture()
    data = m.model_dump()
    data["arm_configurations"]["candidate"].update(
        max_retries=3, overall_observation_deadline_seconds=10
    )
    m = Manifest.model_validate(data)
    raw.update(
        manifest_sha256=digest(m),
        terminal_status="failed",
        finished_seconds=duration,
        configured_cost_usd=cost,
    )
    body = capture(m, [Attempt.model_validate(raw)])["capture"]
    assert body["cost"]["schedule_complete"] is complete
    assert body["cost"]["cap_status"] == (
        status if complete or status == "fail" else "unknown"
    )
    assert body["attempts"][0]["protocol_violations"] == (
        ["overall_observation_deadline_exceeded"] if duration > 10 else []
    )
    assert body["counts"]["delivered"] == 0


@pytest.mark.parametrize("status", ["failed", "cancelled", "delivered"])
def test_timeout_provenance_only_for_timeouts(status):
    _, raw = fixture()
    raw.update(terminal_status=status, timeout_provenance="ProviderTimeout")
    if status == "delivered":
        raw.update(error_class=None, answer={"text": "answer", "citations": []})
    with pytest.raises(ValueError, match="provenance"):
        Attempt.model_validate(raw)


@pytest.mark.parametrize("field", ["error_class", "timeout_provenance"])
@pytest.mark.parametrize(
    "label", ["sk_live_abc123", "sk_test_abc", "sk_proj_abc", "rk_live_abc"]
)
def test_known_key_prefix_labels_rejected(field, label):
    _, raw = fixture()
    with pytest.raises(ValueError):
        Attempt.model_validate(dict(raw, **{field: label}))


@pytest.mark.parametrize("flag", ["manifest", "capture", "protocol"])
def test_empty_anchors_invalid_capture_and_replay(flag, tmp_path):
    m, raw = fixture()
    result = capture(m, [Attempt.model_validate(raw)])
    assert replay(result, **{f"expected_{flag}_sha256": ""}) == {"status": "invalid"}
    mp, ap, rp = (
        tmp_path / name for name in ("manifest.json", "attempts.json", "capture.json")
    )
    mp.write_text(m.model_dump_json())
    ap.write_text(json.dumps([raw]))
    rp.write_text(json.dumps(result))
    assert (
        main(
            [
                "--manifest",
                str(mp),
                "--attempts",
                str(ap),
                f"--expected-{flag}-sha256",
                "",
            ]
        )
        == 1
    )
    assert main(["--replay", str(rp), f"--expected-{flag}-sha256", ""]) == 1


def test_per_arm_and_pair_counts_preserve_failed_partial_pairs():
    m = Manifest.model_validate(comparison())
    _, raw = fixture()
    attempts = []
    for i, slot in enumerate(m.slots[:3]):
        a = dict(
            raw,
            id=f"a{i}",
            manifest_sha256=digest(m),
            slot_id=slot.id,
            slot_sha256=digest(slot),
            started_at=f"2026-11-03T00:0{i + 1}:00Z",
        )
        if i != 1:
            a.update(
                terminal_status="delivered",
                error_class=None,
                answer={"text": "unassessed", "citations": []},
            )
        attempts.append(Attempt.model_validate(a))
    body = capture(m, attempts)["capture"]
    assert body["counts"]["delivered"] == 2
    assert body["pair_counts"]["delivered_pairs"] == 0
    assert body["pair_counts"]["partial_pairs"] == 2
    assert body["per_arm_counts"]["candidate"]["delivered_slots"] == 1
    assert body["per_arm_counts"]["baseline"]["attempted_slots"] == 2
    data = m.model_dump()
    for config in data["arm_configurations"].values():
        config["max_retries"] = 0
    m = Manifest.model_validate(data)
    attempts = [
        Attempt.model_validate(dict(a.model_dump(), manifest_sha256=digest(m)))
        for a in attempts
    ]
    body = capture(m, attempts)["capture"]
    assert body["pair_counts"]["completed_pairs"] == 1
    assert body["pair_counts"]["failed_pairs"] == 1
    assert body["pair_counts"]["partial_pairs"] == 1
