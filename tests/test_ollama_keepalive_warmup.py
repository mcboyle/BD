"""Cut 0.3 (CAP-1): the Ollama text roundtrip times out on cold-load (open L19).
Ollama unloads a model after ~5m idle, so the next call re-loads (30-60s) and
trips the timeout. Fix: set keep_alive on the request so the model stays loaded
between calls, and expose warmup() to pre-load it before the first real call."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from bulk_downloader import ai_provider


def _ollama():
    p = ai_provider.OllamaProvider(endpoint="http://127.0.0.1:11434",
                                   model_text="m", model_vision="mv")
    cap = {}

    def stub(url, body, headers, timeout):
        cap["url"] = url
        cap["body"] = body
        cap["timeout"] = timeout
        return (True, 200, {"response": "ok"}, 5)

    p._http_post = stub
    return p, cap


def test_ollama_generate_sets_keep_alive():
    p, cap = _ollama()
    p.generate("hi")
    assert "keep_alive" in cap["body"], \
        "ollama generate must set keep_alive (avoids re-cold-load between calls)"
    assert cap["body"]["keep_alive"], "keep_alive must be non-empty"


def test_ollama_exposes_warmup_that_loads_and_holds():
    p, cap = _ollama()
    assert hasattr(p, "warmup"), "OllamaProvider must expose warmup()"
    p.warmup()
    assert cap.get("url", "").endswith("/api/generate"), "warmup must hit /api/generate"
    assert cap["body"].get("keep_alive"), \
        "warmup must set keep_alive to load AND hold the model"


def test_aiassist_warmup_delegates_to_provider():
    from bulk_downloader import aiassist
    called = {}

    class FakeProv:
        def warmup(self, timeout=120.0):
            called["yes"] = True
            return True

    orig = aiassist._get_provider
    try:
        aiassist._get_provider = lambda: FakeProv()
        aiassist.warmup()
    finally:
        aiassist._get_provider = orig
    assert called.get("yes"), "aiassist.warmup() must delegate to the provider's warmup()"
