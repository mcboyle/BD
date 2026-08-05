import json

from bulk_downloader import ai_boot_readiness as readiness


CFG = {
    "enabled": True,
    "provider": "ollama",
    "endpoint": "http://localhost:11434",
    "model_text": "text",
    "model_vision": "vision",
}


class ScriptedProbe:
    def __init__(self, *, fail_lists=0, fail_vision=False, cpu_vision=False,
                 gpu_available=True):
        self.fail_lists = fail_lists
        self.fail_vision = fail_vision
        self.cpu_vision = cpu_vision
        self.gpu_available = gpu_available
        self.events = []

    def list_models(self):
        self.events.append("list")
        if self.fail_lists:
            self.fail_lists -= 1
            raise readiness.ProbeFailure("ollama_unreachable", "booting")
        return ["text:latest", "vision:latest"]

    def gpu(self):
        self.events.append("gpu")
        if not self.gpu_available:
            return {"available": False, "devices": [], "error": "driver unavailable"}
        return {"available": True, "devices": ["Tesla T4"]}

    def warm_text(self, model):
        self.events.append("warm_text")

    def warm_vision(self, model):
        self.events.append("warm_vision")
        if self.fail_vision:
            raise readiness.ProbeFailure("vision_warm_failed", "vision failed")

    def resident_models(self):
        self.events.append("ps")
        return [
            {"name": "text:latest", "size": 100, "size_vram": 100},
            {"name": "vision:latest", "size": 200,
             "size_vram": 0 if self.cpu_vision else 200},
        ]

    def resident_for(self, model, entries):
        bare = model.removesuffix(":latest")
        return next((e for e in entries if e["name"].removesuffix(":latest") == bare), None)


def _factory(probe):
    return lambda endpoint, timeout=120.0: probe


def test_disabled_and_cloud_providers_are_not_applicable(tmp_path):
    for cfg in ({**CFG, "enabled": False}, {**CFG, "provider": "openai"}):
        path = tmp_path / (cfg["provider"] + ".json")
        code = readiness.run(cfg, state_path=path, probe_factory=lambda *a, **k: None,
                             retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
        assert code == 0
        assert json.loads(path.read_text())["state"] == "not_applicable"


def test_success_warms_text_then_vision_and_requires_gpu_residency(tmp_path):
    probe = ScriptedProbe()
    path = tmp_path / "ready.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 0
    assert probe.events == ["list", "gpu", "warm_text", "warm_vision", "ps"]
    assert body["state"] == "ready"
    assert body["models"]["text"]["gpu_ratio"] == 1.0
    assert body["models"]["vision"]["size_vram"] == 200


def test_transient_startup_retries_then_recovers(tmp_path):
    probe = ScriptedProbe(fail_lists=1)
    sleeps = []
    path = tmp_path / "retry.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(1,), sleep=sleeps.append,
                         now=lambda: 1_000.0, boot_id="boot-a")
    assert code == 0
    assert sleeps == [1]
    assert json.loads(path.read_text())["attempt"] == 2


def test_retry_exhaustion_persists_degraded(tmp_path):
    probe = ScriptedProbe(fail_lists=2)
    path = tmp_path / "exhausted.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(1,), sleep=lambda seconds: None,
                         now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["state"] == "degraded"
    assert body["attempt"] == 2
    assert body["error_code"] == "ollama_unreachable"


def test_retry_exhaustion_preserves_earlier_text_ready_fallback(tmp_path):
    probe = ScriptedProbe(fail_vision=True)
    list_calls = 0

    def list_then_fail():
        nonlocal list_calls
        probe.events.append("list")
        list_calls += 1
        if list_calls == 2:
            raise readiness.ProbeFailure("ollama_unreachable", "booting")
        return ["text:latest", "vision:latest"]

    probe.list_models = list_then_fail
    path = tmp_path / "fallback-exhausted.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(1,), sleep=lambda seconds: None,
                         now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["state"] == "degraded"
    assert body["attempt"] == 2
    assert body["error_code"] == "ollama_unreachable"
    assert body["models"]["text"]["state"] == "ready"


def test_missing_vision_model_is_marked_missing(tmp_path):
    probe = ScriptedProbe()
    probe.list_models = lambda: ["text:latest"]
    path = tmp_path / "missing.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "model_missing"
    assert body["models"]["vision"]["state"] == "missing"


def test_gpu_absence_is_degraded_before_warming(tmp_path):
    probe = ScriptedProbe(gpu_available=False)
    path = tmp_path / "gpu.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "gpu_unavailable"
    assert "warm_text" not in probe.events and "warm_vision" not in probe.events


def test_vision_failure_rewarms_text_last_and_exits_degraded(tmp_path):
    probe = ScriptedProbe(fail_vision=True)
    path = tmp_path / "degraded.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert probe.events[-2:] == ["warm_text", "ps"]
    assert body["state"] == "degraded"
    assert body["error_code"] == "vision_warm_failed"
    assert body["models"]["text"]["state"] == "ready"
    assert body["models"]["vision"]["state"] == "failed"


def test_cpu_only_vision_is_degraded_even_with_nvidia_smi(tmp_path):
    probe = ScriptedProbe(cpu_vision=True)
    path = tmp_path / "cpu.json"
    code = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a")
    body = json.loads(path.read_text())
    assert code == 1
    assert body["error_code"] == "vision_not_gpu_backed"
    assert body["models"]["vision"]["state"] == "cpu_only"


def test_later_invocation_replaces_degraded_with_ready(tmp_path):
    path = tmp_path / "recover.json"
    bad = ScriptedProbe(fail_vision=True)
    good = ScriptedProbe()
    assert readiness.run(CFG, state_path=path, probe_factory=_factory(bad),
                         retry_delays=(), now=lambda: 1_000.0, boot_id="boot-a") == 1
    assert readiness.run(CFG, state_path=path, probe_factory=_factory(good),
                         retry_delays=(), now=lambda: 1_001.0, boot_id="boot-a") == 0
    assert json.loads(path.read_text())["state"] == "ready"


def test_invalid_config_is_persisted_without_constructing_probe(tmp_path):
    path = tmp_path / "invalid.json"
    code = readiness.run(
        {**CFG, "endpoint": "file:///tmp/ollama.sock"},
        state_path=path,
        probe_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("probe must not be constructed")
        ),
        retry_delays=(),
        now=lambda: 1_000.0,
        boot_id="boot-a",
    )
    assert code == 1
    assert json.loads(path.read_text())["error_code"] == "invalid_config"


def test_early_exit_statuses_strip_endpoint_credentials(tmp_path):
    http_endpoint = (
        "http://credential-user:credential-pass@localhost:11434/private"
        "?token=credential-query"
    )
    file_endpoint = (
        "file://credential-user:credential-pass@localhost/private"
        "?token=credential-query"
    )
    cases = (
        ({**CFG, "enabled": False, "endpoint": http_endpoint}, 0,
         "http://localhost:11434/private"),
        ({**CFG, "provider": "openai", "endpoint": http_endpoint}, 0,
         "http://localhost:11434/private"),
        ({**CFG, "endpoint": file_endpoint}, 1, "file://localhost/private"),
    )

    for index, (cfg, expected_code, expected_endpoint) in enumerate(cases):
        path = tmp_path / f"early-exit-{index}.json"
        code = readiness.run(
            cfg,
            state_path=path,
            probe_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("probe must not be constructed")
            ),
            retry_delays=(),
            now=lambda: 1_000.0,
            boot_id="boot-a",
        )
        document = path.read_text()
        body = json.loads(document)
        assert code == expected_code
        assert body["endpoint"] == expected_endpoint
        for secret in ("credential-user", "credential-pass", "credential-query"):
            assert secret not in document


# --------------------------------------------------------------------------- #
# @874 -- a reader sampling mid-run must not see the LAST run's verdict         #
# --------------------------------------------------------------------------- #
# Nothing was written between process start and the END of attempt 1. On the box
# the readiness unit is Type=simple / Restart=on-failure / RestartSec=60, so a
# new run's attempt-1 window is exactly when a reader is most likely to sample
# -- and what it saw was the PREVIOUS run's terminal `degraded` document,
# verbatim and indistinguishable from a finished failure. That is what produced
# the wrong "Audit #3 reproduced" verdict.

def test_in_flight_marker_precedes_the_first_probe_call(tmp_path):
    """A document must exist, and say it is not final, BEFORE attempt 1 probes.

    The spy reads the file at the moment of the first outbound call, which is
    the earliest point a real reader could sample.
    """
    path = tmp_path / "state.json"
    probe = ScriptedProbe()
    seen = {}
    original = probe.list_models

    def spy():
        seen["exists"] = path.exists()
        seen["doc"] = json.loads(path.read_text()) if path.exists() else None
        return original()

    probe.list_models = spy
    readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                  retry_delays=(), now=lambda: 1000.0, boot_id="boot-a")
    assert seen["exists"] is True, (
        "no document existed while attempt 1 was in flight, so a reader had "
        "nothing but the previous run's terminal record to go on")
    assert seen["doc"]["final"] is False, seen["doc"]


def test_a_new_run_does_not_leave_the_previous_failure_readable(tmp_path):
    """The box's actual shape: a terminal degraded doc from the prior restart,
    same boot_id, well inside the 600s stale window, while a NEW run is live."""
    from bulk_downloader import ai_boot_status as status
    path = tmp_path / "state.json"

    # 1. a prior run fails to exhaustion. Asserted, not assumed -- without this
    #    precondition the test could pass because nothing was there at all.
    readiness.run(CFG, state_path=path, probe_factory=_factory(ScriptedProbe(fail_lists=9)),
                  retry_delays=(0,), sleep=lambda _s: None,
                  now=lambda: 1000.0, boot_id="boot-a")
    assert json.loads(path.read_text())["state"] == "degraded"

    # 2. a new, succeeding run starts against the SAME path and boot id
    probe = ScriptedProbe()
    captured = {}
    original = probe.list_models

    def spy():
        captured["doc"] = status.read_status(path, now=1100.0, boot_id="boot-a")
        return original()

    probe.list_models = spy
    readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                  retry_delays=(), now=lambda: 1100.0, boot_id="boot-a")
    assert captured["doc"]["state"] != "degraded", (
        "a reader mid-run saw the previous process's terminal failure: %r"
        % (captured["doc"],))
    assert captured["doc"]["final"] is False, captured["doc"]


def test_a_marker_exists_before_the_probe_is_even_constructed(tmp_path):
    """The pre-attempt-1 write is BEFORE probe_factory, and this is what makes
    that ordering observable.

    A mutation battery caught the gap: removing that write, or marking it
    final=True, left the suite green -- because the per-attempt heartbeat
    inside the loop writes a moment later and masks it. The two writes overlap
    everywhere EXCEPT the factory window, so only a slow factory can tell them
    apart. ScriptedProbe's factory is instant, so no existing test could.
    """
    path = tmp_path / "state.json"
    seen = {}

    def slow_factory(endpoint, timeout=None):
        seen["exists"] = path.exists()
        seen["doc"] = json.loads(path.read_text()) if path.exists() else None
        return ScriptedProbe()

    readiness.run(CFG, state_path=path, probe_factory=slow_factory,
                  retry_delays=(), now=lambda: 1000.0, boot_id="boot-a")
    assert seen["exists"] is True, (
        "no document existed while the probe was being CONSTRUCTED. A slow "
        "factory is dead time in which a reader sees the previous run's "
        "verdict.")
    assert seen["doc"]["final"] is False, seen["doc"]
    assert seen["doc"]["state"] == "retrying", seen["doc"]


def test_the_heartbeat_refreshes_the_marker_on_every_attempt(tmp_path):
    """The heartbeat is what keeps IN_FLIGHT_TTL_SECONDS honest over a long run.

    Without it, the newest in-flight document during attempt N is the attempt
    N-1 FAILURE write -- which is also final=False, so finality alone cannot
    tell them apart. The attempt NUMBER can, and that is what this asserts.

    TTL and heartbeat ship together or neither ships: a TTL with no refresh
    grades a slow live run abandoned; a heartbeat with no TTL lets a dead run
    read live forever.
    """
    path = tmp_path / "state.json"
    probe = ScriptedProbe(fail_lists=1)          # attempt 1 fails, attempt 2 succeeds
    seen = []
    original = probe.list_models

    def spy():
        seen.append(json.loads(path.read_text()) if path.exists() else None)
        return original()

    probe.list_models = spy
    rc = readiness.run(CFG, state_path=path, probe_factory=_factory(probe),
                       retry_delays=(0,), sleep=lambda _s: None,
                       now=lambda: 1000.0, boot_id="boot-a")
    assert rc == 0 and len(seen) == 2, (rc, seen)
    assert seen[1]["attempt"] == 2, (
        "during attempt 2 the newest document still described attempt %r -- "
        "the marker is not being refreshed, so a long run goes silent and the "
        "TTL will grade it abandoned: %r" % (seen[1].get("attempt"), seen[1]))
    assert seen[1]["final"] is False, seen[1]


def test_a_successful_run_leaves_a_document_marked_final(tmp_path):
    """The terminal verdict must NOT wear the in-flight marker.

    A `ready` document written with final=False would expire to
    "stale/abandoned" after the TTL, so a perfectly healthy system would report
    stale five minutes after converging. Nothing asserted this until a mutant
    marked the ready write non-final and the suite stayed green.
    """
    from bulk_downloader import ai_boot_status as status
    path = tmp_path / "state.json"
    rc = readiness.run(CFG, state_path=path, probe_factory=_factory(ScriptedProbe()),
                       retry_delays=(), now=lambda: 1000.0, boot_id="boot-a")
    assert rc == 0
    doc = json.loads(path.read_text())
    assert doc["state"] == "ready" and doc["final"] is True, doc
    # and it must survive well past the in-flight TTL unchanged
    out = status.read_status(path, now=1000.0 + 400, boot_id="boot-a")
    assert out["state"] == "ready", (
        "a converged, healthy system reported %r once the in-flight TTL "
        "elapsed" % (out.get("state"),))
