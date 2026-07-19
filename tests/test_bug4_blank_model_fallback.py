"""BUG-4 -- a blank/whitespace AI model should fall back to the provider default,
not get sent to the backend as a literal model name (which hangs to the timeout).

ai_provider resolves `self.model_text = model_text or default_models["text"]`.
Python truthiness means an EMPTY string already falls back correctly -- but a
WHITESPACE-only string ("  ") is truthy, so it survives the `or` and is sent to
Ollama as the model name, producing the ~30s "blank default text model" hang the
operator saw.

Fix: strip before the fallback -- `(model_text or "").strip() or default`.
"""
import os
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import ai_provider

_DEF_T = ai_provider.OllamaProvider.default_models["text"]
_DEF_V = ai_provider.OllamaProvider.default_models["vision"]


def _mk(**kw):
    return ai_provider.make_provider("ollama", endpoint="http://localhost:11434", **kw)


def test_empty_text_model_falls_back():
    # already worked pre-fix; guards the contract
    p = _mk(model_text="")
    assert p.model_text == _DEF_T, f"empty -> default; got {p.model_text!r}"


def test_whitespace_text_model_falls_back():
    p = _mk(model_text="   ")
    assert p.model_text == _DEF_T, f"BUG-4: whitespace model must fall back, got {p.model_text!r}"


def test_whitespace_vision_model_falls_back():
    p = _mk(model_vision="\t ")
    assert p.model_vision == _DEF_V, f"BUG-4: whitespace vision must fall back, got {p.model_vision!r}"


def test_real_model_is_preserved():
    # a genuine model name must NOT be clobbered
    p = _mk(model_text="llama3.1:8b")
    assert p.model_text == "llama3.1:8b", f"real model clobbered: {p.model_text!r}"


def test_padded_real_model_is_trimmed_not_defaulted():
    p = _mk(model_text="  llama3.1:8b  ")
    assert p.model_text == "llama3.1:8b", f"padded real model mishandled: {p.model_text!r}"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
