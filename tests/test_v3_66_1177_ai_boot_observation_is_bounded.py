"""v3.66.1177: capture observes the independent AI boot companion honestly."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BOOT_ID = "boot-current"
NOW = 1_100.0
PHASES = ["list", "gpu", "warm_text", "warm_vision", "ps"]


def _observer():
    try:
        return importlib.import_module("bulk_downloader.ai_boot_observation")
    except ModuleNotFoundError as exc:
        pytest.fail("bulk_downloader.ai_boot_observation is absent (expected RED): %s" % exc)


def _config(*, enabled=True, provider="ollama"):
    return {
        "observed": True,
        "source": "file",
        "enabled": enabled,
        "provider": provider,
        "endpoint": "http://localhost:11434",
        "model_text": "text",
        "model_vision": "vision",
    }


def _unit(**updates):
    unit = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "SubState": "dead",
        "Result": "success",
        "ExecMainStatus": 0,
        "NRestarts": 0,
        "InvocationID": "invocation-current",
    }
    unit.update(updates)
    return unit


def _status(state="ready", **updates):
    status = {
        "schema_version": 1,
        "state": state,
        "final": True,
        "boot_id": BOOT_ID,
        "updated_epoch": 1_000.0,
        "attempt": 1,
        "phases": list(PHASES),
        "gpu": {"available": True, "devices": ["GPU-0"]},
        "models": {
            "text": {"name": "text", "state": "ready", "resident": True,
                     "size": 100, "size_vram": 100, "gpu_ratio": 1.0},
            "vision": {"name": "vision", "state": "ready", "resident": True,
                       "size": 200, "size_vram": 200, "gpu_ratio": 1.0},
        },
        "error_code": "",
    }
    status.update(updates)
    return status


def _classify(*, config=None, unit=None, status=None, restart_limit=3):
    return _observer().classify(
        config=_config() if config is None else config,
        unit=_unit() if unit is None else unit,
        status=_status() if status is None else status,
        now=NOW,
        boot_id=BOOT_ID,
        restart_limit=restart_limit,
    )


def _assert_hold(result, reason):
    assert result["exit_code"] != 0, result
    assert result["verdict"] in {"HOLD", "RETRYING"}, result
    assert reason in json.dumps(result).lower(), result


def test_success_accepts_a_completed_inactive_dead_oneshot_with_runtime_proof():
    result = _classify()
    assert result["exit_code"] == 0
    assert result["verdict"] == "READY"
    assert result["observed"] == {
        "config": True, "unit": True, "status": True,
        "text_runtime": True, "vision_runtime": True, "gpu_runtime": True,
    }
    assert result["phases"] == PHASES


@pytest.mark.parametrize("config", [
    _config(enabled=False),
    _config(provider="openai"),
])
def test_not_applicable_requires_observed_config_and_matching_final_status(config):
    status = _status("not_applicable", phases=[], models={})
    result = _classify(config=config, status=status)
    assert result["exit_code"] == 0
    assert result["verdict"] == "NOT_APPLICABLE"
    assert result["observed"]["config"] is True
    assert result["observed"]["status"] is True


@pytest.mark.parametrize(("config", "status", "reason"), [
    (None, _status("not_applicable", phases=[], models={}), "config"),
    (_config(enabled=False), None, "status"),
    (_config(enabled=False), "not-json-object", "malformed"),
])
def test_absence_or_malformed_evidence_cannot_become_not_applicable(config, status, reason):
    result = _observer().classify(
        config=config, unit=_unit(), status=status, now=NOW, boot_id=BOOT_ID,
        restart_limit=3,
    )
    _assert_hold(result, reason)


@pytest.mark.parametrize(("status", "reason"), [
    (_status("stale", stale_reason="expired"), "stale"),
    (_status(boot_id="boot-previous"), "boot"),
    (_status("degraded", error_code="vision_warm_failed"), "degraded"),
    (_status(models={}), "model"),
])
def test_stale_prior_boot_degraded_or_missing_model_evidence_fails(status, reason):
    _assert_hold(_classify(status=status), reason)


@pytest.mark.parametrize(("role", "field", "value"), [
    ("text", "size_vram", 0),
    ("vision", "size_vram", 0),
    ("text", "gpu_ratio", 0.0),
    ("vision", "gpu_ratio", "1.0"),
])
def test_gpu_inventory_cannot_replace_positive_model_runtime_proof(role, field, value):
    status = _status()
    status["models"][role][field] = value
    _assert_hold(_classify(status=status), role)


@pytest.mark.parametrize("restarts", [None, True, -1, "3"])
def test_restart_data_must_be_a_nonnegative_integer(restarts):
    unit = _unit()
    if restarts is None:
        unit.pop("NRestarts")
    else:
        unit["NRestarts"] = restarts
    _assert_hold(_classify(unit=unit), "restart")


def test_retry_contention_is_visible_and_has_a_hard_boundary():
    retrying = _status("retrying", final=False, phases=["list"], models={})
    at_limit = _classify(unit=_unit(ActiveState="activating", SubState="auto-restart",
                                    Result="exit-code", NRestarts=3),
                         status=retrying, restart_limit=3)
    assert at_limit["verdict"] == "RETRYING" and at_limit["exit_code"] != 0
    assert at_limit["restart_count"] == 3 and at_limit["restart_limit"] == 3

    over = _classify(unit=_unit(ActiveState="activating", SubState="auto-restart",
                                Result="exit-code", NRestarts=4),
                     status=retrying, restart_limit=3)
    assert over["verdict"] == "HOLD" and over["exit_code"] != 0
    assert "contention" in json.dumps(over).lower()


def test_ready_requires_the_exact_text_then_vision_runtime_sequence():
    for phases in (
        ["gpu", "list", "warm_text", "warm_vision", "ps"],
        ["list", "gpu", "warm_vision", "warm_text", "ps"],
        ["list", "gpu", "warm_text", "warm_vision"],
        ["list", "gpu", "warm_text", "warm_text", "warm_vision", "ps"],
    ):
        _assert_hold(_classify(status=_status(phases=phases)), "phase")


def test_capture_has_an_independent_gated_ai_boot_artifact_stage():
    capture = (ROOT / "capture.sh").read_text(encoding="utf-8")
    assert "bulk_downloader.ai_boot_observation" in capture
    assert '05_ai_boot_observation.json' in capture
    assert "AI_BOOT_EXIT=$?" in capture
    assert '--stage-exit "ai-boot-observation=$AI_BOOT_EXIT"' in capture


def test_main_service_remains_independent_of_the_ai_companion():
    install = (ROOT / "install_service.sh").read_text(encoding="utf-8")
    main = install[install.index("sudo tee \"$UNIT_PATH\""):
                   install.index("sudo tee \"$AI_UNIT_PATH\"")]
    assert "bulkdownloader-ai-ready" not in main
    assert "ai_boot_readiness" not in main
    assert "ollama.service" not in main
    assert "Requires=" not in main


class _Clock:
    def __init__(self, epoch=1_000.0):
        self.epoch = epoch
        self.sleeps = []

    def now(self):
        return self.epoch

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.epoch += seconds


def _sequence(values):
    values = iter(values)
    last = None

    def read(*_args, **_kwargs):
        nonlocal last
        try:
            last = next(values)
        except StopIteration:
            pass
        return last

    return read


def _observe(*, statuses, units=None, configs=None, timeout=30, interval=1,
             max_samples=10, clock=None):
    observer = _observer()
    clock = clock or _Clock()
    units = units or [_unit()] * len(statuses)
    configs = configs or [_config()] * len(statuses)
    return observer.observe(
        timeout=timeout,
        interval=interval,
        max_samples=max_samples,
        restart_limit=3,
        now=clock.now,
        sleep=clock.sleep,
        config_reader=_sequence(configs),
        unit_reader=_sequence(units),
        status_reader=_sequence(statuses),
        boot_id_reader=lambda: BOOT_ID,
    ), clock


def test_observer_polls_missing_then_retrying_until_fresh_ready():
    retrying = _status("retrying", final=False, phases=["list"], models={})
    result, clock = _observe(
        statuses=[None, retrying, _status()],
        units=[
            _unit(ActiveState="activating", SubState="start", Result="success"),
            _unit(ActiveState="activating", SubState="auto-restart", NRestarts=1),
            _unit(NRestarts=1),
        ],
    )
    assert result["verdict"] == "READY" and result["exit_code"] == 0
    assert result["sample_count"] == 3
    assert [sample["verdict"] for sample in result["samples"]] == [
        "RETRYING", "RETRYING", "READY",
    ]
    assert len(clock.sleeps) == 2


def test_prior_degraded_is_polled_when_the_unit_is_starting_a_new_invocation():
    prior = _status("degraded", error_code="ollama_unreachable", final=True)
    retrying = _status("retrying", final=False, phases=["list"], models={})
    result, _clock = _observe(
        statuses=[prior, retrying, _status()],
        units=[
            _unit(ActiveState="activating", SubState="auto-restart", NRestarts=2),
            _unit(ActiveState="activating", SubState="start", NRestarts=2),
            _unit(NRestarts=2),
        ],
    )
    assert result["verdict"] == "READY"
    assert result["sample_count"] == 3


@pytest.mark.parametrize("statuses", [
    [None] * 20,
    [_status("retrying", final=False, phases=["list"], models={})] * 20,
])
def test_perpetual_startup_unknown_or_retrying_stops_at_the_deadline(statuses):
    units = [_unit(ActiveState="activating", SubState="auto-restart",
                   Result="exit-code", NRestarts=index)
             for index in range(len(statuses))]
    result, clock = _observe(statuses=statuses, units=units, timeout=3,
                             interval=1, max_samples=20)
    assert result["exit_code"] != 0 and result["verdict"] == "HOLD"
    assert "deadline" in json.dumps(result).lower()
    assert result["sample_count"] <= 4
    assert sum(clock.sleeps) <= 3


def test_current_terminal_degraded_fails_immediately_without_polling():
    degraded = _status("degraded", error_code="vision_warm_failed", final=True)
    result, clock = _observe(statuses=[degraded, _status()], units=[_unit(), _unit()])
    assert result["exit_code"] != 0 and result["verdict"] == "HOLD"
    assert result["sample_count"] == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("configs", [
    ["not-an-object"],
    [ValueError("malformed app_config.json")],
])
def test_real_observer_cannot_default_malformed_config_to_not_applicable(configs):
    value = configs[0]

    def read_config():
        if isinstance(value, Exception):
            raise value
        return value

    observer = _observer()
    clock = _Clock()
    result = observer.observe(
        timeout=0, interval=1, max_samples=1, now=clock.now, sleep=clock.sleep,
        config_reader=read_config, unit_reader=lambda: _unit(),
        status_reader=lambda *_a, **_k: _status("not_applicable", phases=[], models={}),
        boot_id_reader=lambda: BOOT_ID,
    )
    assert result["exit_code"] != 0
    assert result["verdict"] != "NOT_APPLICABLE"
    assert "config" in json.dumps(result).lower()


def test_absent_config_is_an_explicit_first_run_default_provenance():
    config = _config(enabled=False)
    config["source"] = "absent_defaults"
    result, _clock = _observe(
        statuses=[_status("not_applicable", phases=[], models={})],
        configs=[config],
    )
    assert result["exit_code"] == 0 and result["verdict"] == "NOT_APPLICABLE"
    assert result["config"]["source"] == "absent_defaults"
    assert result["config"]["observed"] is True


def test_real_config_snapshot_absent_path_uses_exact_effective_defaults(tmp_path):
    observer = _observer()
    path = tmp_path / "app_config.json"

    result = observer._config_snapshot(path)

    expected = observer.load_effective_config({})
    assert result == {**expected, "observed": True, "source": "absent_defaults"}
    assert not path.exists()


@pytest.mark.parametrize("contents", ["{broken", "[]", "null"])
def test_real_config_snapshot_rejects_malformed_or_nonobject_files(tmp_path, contents):
    path = tmp_path / "app_config.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError):
        _observer()._config_snapshot(path)


def test_real_config_snapshot_rejects_read_errors(tmp_path, monkeypatch):
    path = tmp_path / "app_config.json"
    path.write_text("{}", encoding="utf-8")
    real_read_text = Path.read_text

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("denied")
        return real_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(ValueError):
        _observer()._config_snapshot(path)


@pytest.mark.parametrize("contents, expected_verdict", [
    (None, "NOT_APPLICABLE"),
    ("{broken", "HOLD"),
])
def test_observe_uses_real_config_reader_without_defaulting_malformed_files(
        tmp_path, contents, expected_verdict):
    observer = _observer()
    path = tmp_path / "app_config.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    clock = _Clock()

    result = observer.observe(
        timeout=0, interval=1, max_samples=1, now=clock.now, sleep=clock.sleep,
        config_reader=lambda: observer._config_snapshot(path),
        unit_reader=lambda: _unit(),
        status_reader=lambda *_a, **_k: _status(
            "not_applicable", phases=[], models={}),
        boot_id_reader=lambda: BOOT_ID,
    )

    assert result["verdict"] == expected_verdict
    assert (result["exit_code"] == 0) is (expected_verdict == "NOT_APPLICABLE")
    if expected_verdict == "NOT_APPLICABLE":
        assert result["config"]["source"] == "absent_defaults"
    else:
        assert "config_reader" in result["acquisition_errors"]


def test_same_boot_final_ready_is_rejected_when_its_timestamp_is_old():
    old = _status(updated_epoch=NOW - 10_000)
    _assert_hold(_classify(status=old), "stale")


def test_observation_exposes_only_sanitized_config_evidence():
    config = _config()
    config["endpoint"] = "http://user:password@localhost:11434/api?token=secret"
    result, _clock = _observe(statuses=[_status()], configs=[config])
    encoded = json.dumps(result)
    assert result["config"]["endpoint"] == "http://localhost:11434/api"
    for secret in ("user", "password", "token=secret"):
        assert secret not in encoded


def test_interval_is_positive_and_the_sample_denominator_is_bounded():
    observer = _observer()
    assert observer.MIN_INTERVAL > 0
    assert observer.DEFAULT_MAX_SAMPLES > 1
    clock = _Clock()
    retrying = _status("retrying", final=False, phases=["list"], models={})
    result, clock = _observe(statuses=[retrying] * 50, units=[
        _unit(ActiveState="activating", SubState="auto-restart", NRestarts=1)
    ] * 50, timeout=100, interval=0, max_samples=3, clock=clock)
    assert result["sample_count"] == 3
    assert all(seconds >= observer.MIN_INTERVAL for seconds in clock.sleeps)
    assert "sample" in json.dumps(result).lower() and result["exit_code"] != 0


def test_timeout_summary_reconciles_first_last_and_delta_restart_counts():
    retrying = _status("retrying", final=False, phases=["list"], models={})
    result, _clock = _observe(
        statuses=[retrying] * 5,
        units=[_unit(ActiveState="activating", SubState="auto-restart",
                     NRestarts=value) for value in (1, 2, 3, 4, 5)],
        timeout=2, interval=1, max_samples=5,
    )
    assert result["first_restart_count"] == 1
    assert result["last_restart_count"] == 3
    assert result["restart_delta"] == 2
    assert result["sample_count"] == 3


def test_main_writes_parseable_output_atomically_and_returns_observer_code(
        tmp_path, monkeypatch):
    observer = _observer()
    output = tmp_path / "observation.json"
    replacements = []
    real_replace = observer.os.replace

    monkeypatch.setattr(observer, "observe", lambda **_kwargs: {
        "exit_code": 2, "verdict": "HOLD", "reason": "fixture",
        "samples": [], "sample_count": 0,
    })

    def record_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(observer.os, "replace", record_replace)
    rc = observer.main(["--output", str(output), "--timeout", "0",
                        "--interval", "1", "--max-samples", "1"])
    assert rc == 2
    assert json.loads(output.read_text())["verdict"] == "HOLD"
    assert len(replacements) == 1
    assert replacements[0][1] == output
    assert replacements[0][0] != output
    assert not replacements[0][0].exists()


@pytest.mark.parametrize("schema", [None, True, 2, "1"])
@pytest.mark.parametrize("state", ["ready", "not_applicable", "retrying"])
def test_unsupported_status_schema_cannot_emit_any_accepted_verdict(schema, state):
    config = _config(enabled=state != "not_applicable")
    if state == "not_applicable":
        status = _status(state, phases=[], models={})
    elif state == "retrying":
        status = _status(state, final=False, phases=["list"], models={})
    else:
        status = _status(state)
    if schema is None:
        status.pop("schema_version")
    else:
        status["schema_version"] = schema
    result = _classify(config=config, status=status)
    assert result["verdict"] == "HOLD", result
    assert result["exit_code"] != 0
    assert "schema" in json.dumps(result).lower()


def test_future_status_timestamp_is_bounded_by_an_explicit_five_second_skew():
    observer = _observer()
    assert observer.MAX_FUTURE_CLOCK_SKEW_SECONDS == 5

    within = _classify(status=_status(updated_epoch=NOW + 5))
    assert within["verdict"] == "READY" and within["exit_code"] == 0

    beyond = _classify(status=_status(updated_epoch=NOW + 5.001))
    assert beyond["verdict"] == "HOLD" and beyond["exit_code"] != 0
    assert "future" in json.dumps(beyond).lower()


@pytest.mark.parametrize("invocation_id", [None, "", "   ", True, 7])
def test_success_requires_a_nonempty_systemd_invocation_identity(invocation_id):
    unit = _unit()
    if invocation_id is None:
        unit.pop("InvocationID")
    else:
        unit["InvocationID"] = invocation_id
    result = _classify(unit=unit)
    assert result["verdict"] == "HOLD", result
    assert result["exit_code"] != 0
    assert "invocation" in json.dumps(result).lower()


@pytest.mark.parametrize("reader_name", ["unit_reader", "status_reader", "boot_id_reader"])
def test_throwing_observation_readers_become_structured_hold_evidence(reader_name):
    observer = _observer()
    clock = _Clock()

    def boom(*_args, **_kwargs):
        raise RuntimeError("reader exploded")

    readers = {
        "config_reader": lambda: _config(),
        "unit_reader": lambda: _unit(),
        "status_reader": lambda *_a, **_k: _status(),
        "boot_id_reader": lambda: BOOT_ID,
    }
    readers[reader_name] = boom
    result = observer.observe(
        timeout=0, interval=1, max_samples=1, now=clock.now,
        sleep=clock.sleep, restart_limit=3, **readers,
    )
    assert result["verdict"] == "HOLD" and result["exit_code"] != 0
    assert result["sample_count"] == 1
    assert "reader" in json.dumps(result).lower()


def test_main_still_writes_atomic_hold_artifact_if_observation_raises(
        tmp_path, monkeypatch):
    observer = _observer()
    output = tmp_path / "observer-crash.json"
    monkeypatch.setattr(
        observer, "observe",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("observer exploded")),
    )
    rc = observer.main(["--output", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert rc != 0 and payload["exit_code"] == rc
    assert payload["verdict"] == "HOLD"
    assert "observer" in json.dumps(payload).lower()


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1.0, 10**9])
@pytest.mark.parametrize("interval", [float("nan"), float("inf"), -1.0, 10**9])
def test_nonfinite_negative_or_huge_numeric_bounds_cannot_create_unbounded_waits(
        timeout, interval):
    observer = _observer()
    assert 0 < observer.MAX_TIMEOUT_SECONDS <= 300
    assert observer.MIN_INTERVAL > 0
    assert observer.MIN_INTERVAL <= observer.MAX_INTERVAL_SECONDS <= 30
    clock = _Clock()
    retrying = _status("retrying", final=False, phases=["list"], models={})
    result, clock = _observe(
        statuses=[retrying] * 3,
        units=[_unit(ActiveState="activating", SubState="auto-restart",
                     NRestarts=1)] * 3,
        timeout=timeout, interval=interval, max_samples=2, clock=clock,
    )
    assert result["verdict"] == "HOLD" and result["exit_code"] != 0
    assert result["sample_count"] <= 2
    assert all(observer.MIN_INTERVAL <= seconds <= observer.MAX_INTERVAL_SECONDS
               for seconds in clock.sleeps)
    assert result["elapsed_seconds"] <= observer.MAX_TIMEOUT_SECONDS


@pytest.mark.parametrize("boot_identity", ["unknown", "", "   ", None, True, 7])
@pytest.mark.parametrize("state", ["ready", "not_applicable"])
def test_unproved_boot_identity_cannot_self_match_into_an_accepted_state(
        boot_identity, state):
    observer = _observer()
    config = _config(enabled=state == "ready")
    status = (_status() if state == "ready"
              else _status("not_applicable", phases=[], models={}))
    status["boot_id"] = boot_identity
    result = observer.classify(
        config=config, unit=_unit(), status=status, now=NOW,
        boot_id=boot_identity, restart_limit=3,
    )
    assert result["verdict"] == "HOLD" and result["exit_code"] != 0
    assert "boot" in json.dumps(result).lower()


@pytest.mark.parametrize("updated_epoch", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_status_timestamp_is_malformed_not_fresh(updated_epoch):
    result = _classify(status=_status(updated_epoch=updated_epoch))
    assert result["verdict"] == "HOLD" and result["exit_code"] != 0
    assert "timestamp" in json.dumps(result).lower()
