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
