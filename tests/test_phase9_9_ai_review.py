"""Phase 9.9 -- optional AI screenshot review (RED-first, mock-first)."""

import tempfile

from bulk_downloader import ai_review


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "qwen2.5vl:7b"; self.latency_ms = 9


def _call_ok(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(text='{"findings":["header clipped on mobile"],"severity":"low"}')


def _call_offline(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(ok=False, error="net", error_kind="network")


def _call_garbage(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake(text="the screenshot looks fine I think")


_IMG = ["iVBORw0KGgo="]


def test_disabled_by_default():
    sink = []
    out = ai_review.review_screenshots(_IMG)   # enabled defaults False
    assert out["ran"] is False
    assert out["reason"] == "disabled"


def test_text_only_model_rejected():
    out = ai_review.review_screenshots(_IMG, enabled=True, model="qwen2.5:0.5b",
                                       _call=_call_ok)
    assert out["ran"] is False
    assert out["reason"] == "text-only-model-rejected"


def test_vision_model_schema_valid_review():
    out = ai_review.review_screenshots(_IMG, enabled=True, model="qwen2.5vl:7b",
                                       _call=_call_ok)
    assert out["ran"] is True
    assert out["review"]["severity"] == "low"
    assert out["advisory"] is True


def test_provider_offline_does_not_crash():
    out = ai_review.review_screenshots(_IMG, enabled=True, model="qwen2.5vl:7b",
                                       _call=_call_offline)
    assert out["advisory"] is True
    assert out["review"] is None          # offline -> no review, but no exception


def test_invalid_review_does_not_fail():
    out = ai_review.review_screenshots(_IMG, enabled=True, model="qwen2.5vl:7b",
                                       _call=_call_garbage)
    assert out["advisory"] is True
    assert out["review"] is None          # unparseable -> advisory none, non-fatal


def test_review_saved_separately_as_advisory():
    out = ai_review.review_screenshots(_IMG, enabled=True, model="qwen2.5vl:7b",
                                       _call=_call_ok)
    d = tempfile.mkdtemp()
    paths = ai_review.save_review(out, d)
    md = open(paths["md"]).read()
    assert "ADVISORY" in md
    assert "header clipped on mobile" in md
    import json
    j = json.load(open(paths["json"]))
    assert j["advisory"] is True
