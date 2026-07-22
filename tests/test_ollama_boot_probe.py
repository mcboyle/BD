from types import SimpleNamespace

from bulk_downloader import llm_readiness
from bulk_downloader.ollama_boot_probe import OllamaBootProbe


class FakeHttp:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, url, payload, timeout))
        if url.endswith("/api/tags"):
            return {"models": [{"name": "text:latest"}, {"name": "vision:latest"}]}
        if url.endswith("/api/ps"):
            return {"models": [
                {"name": "text:latest", "size": 100, "size_vram": 100},
                {"name": "vision:latest", "size": 200, "size_vram": 180},
            ]}
        if url.endswith("/api/generate"):
            return {"response": "ok"}
        raise AssertionError(url)


def test_model_listing_warms_text_then_vision_with_fixed_payloads():
    http = FakeHttp()
    probe = OllamaBootProbe("http://localhost:11434/", request_json=http)
    assert probe.list_models() == ["text:latest", "vision:latest"]
    probe.warm_text("text")
    probe.warm_vision("vision")
    generates = [call[2] for call in http.calls if call[1].endswith("/api/generate")]
    assert generates[0] == {
        "model": "text",
        "prompt": llm_readiness.PROBE_PROMPT,
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_predict": 1, "temperature": 0},
    }
    assert generates[1]["model"] == "vision"
    assert generates[1]["prompt"] == llm_readiness.VISION_PROBE_PROMPT
    assert generates[1]["images"] == [llm_readiness.TINY_PNG]


def test_residency_matches_latest_and_reports_vram():
    probe = OllamaBootProbe("http://localhost:11434", request_json=FakeHttp())
    entries = probe.resident_models()
    text = probe.resident_for("text", entries)
    vision = probe.resident_for("vision:latest", entries)
    assert text == {"name": "text:latest", "size": 100, "size_vram": 100}
    assert vision["size_vram"] == 180
    assert probe.resident_for("missing", entries) is None


def test_gpu_probe_uses_service_user_visible_nvidia_smi():
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="Tesla T4\n", stderr="")

    probe = OllamaBootProbe(
        "http://localhost:11434",
        request_json=FakeHttp(),
        which=lambda name: "/usr/bin/nvidia-smi",
        run=run,
    )
    assert probe.gpu() == {"available": True, "devices": ["Tesla T4"]}
    assert calls[0][0] == [
        "/usr/bin/nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"
    ]


def test_gpu_probe_fails_open_to_structured_unavailable():
    probe = OllamaBootProbe(
        "http://localhost:11434",
        request_json=FakeHttp(),
        which=lambda name: None,
    )
    assert probe.gpu() == {"available": False, "devices": [], "error": "nvidia-smi not found"}


def test_operation_failures_use_stable_codes():
    def broken(method, url, payload, timeout):
        raise TimeoutError("not ready")

    probe = OllamaBootProbe("http://localhost:11434", request_json=broken)
    operations = (
        (probe.list_models, (), "ollama_unreachable"),
        (probe.warm_text, ("text",), "text_warm_failed"),
        (probe.warm_vision, ("vision",), "vision_warm_failed"),
        (probe.resident_models, (), "residency_probe_failed"),
    )
    for operation, args, expected in operations:
        try:
            operation(*args)
        except Exception as exc:
            assert exc.code == expected
        else:
            raise AssertionError(f"{operation.__name__} did not fail")


def test_operation_failure_hides_transport_credentials_and_content():
    secret = "https://operator:api-key@example.test/api?token=secret user-content"

    def broken(method, url, payload, timeout):
        raise RuntimeError(secret)

    probe = OllamaBootProbe("http://localhost:11434", request_json=broken)
    try:
        probe.list_models()
    except Exception as exc:
        message = str(exc)
        assert "operator" not in message
        assert "api-key" not in message
        assert "secret" not in message
        assert "user-content" not in message
    else:
        raise AssertionError("list_models did not fail")
