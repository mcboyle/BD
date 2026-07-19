"""Phase 9.4 -- LLM cache by input hash (RED-first).

Identical input hits the cache; a changed prompt/model/schema misses; cached
output is re-validated against the schema on read; raw input/secrets are never
persisted; a cache hit never bypasses review; the cache is clearable.
"""

import json

from bulk_downloader import llm_cache
from bulk_downloader.llm_cache import cache_key, clear as cache_clear
from bulk_downloader.llm_exec import LLMCallSpec, execute


class _Fake:
    def __init__(self, ok=True, text="ok", error="", error_kind=""):
        self.ok = ok; self.text = text; self.error = error
        self.error_kind = error_kind; self.provider = "ollama"
        self.model = "bd-text-small"; self.latency_ms = 2


def _counting(text, sink):
    def _c(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
        sink.append(1)
        return _Fake(text=text)
    return _c


def _spec(**kw):
    base = dict(task_id="classify", prompt_id="classify_role", prompt_version="1",
                input="role of button", schema=None, use_cache=True,
                provider="ollama", model="bd-text-small")
    base.update(kw)
    return LLMCallSpec(**base)


# ── key composition ──────────────────────────────────────────────────────
def test_cache_key_components_change_key():
    k = cache_key(_spec())
    assert k == cache_key(_spec())                       # deterministic
    assert k != cache_key(_spec(prompt_version="2"))     # prompt version
    assert k != cache_key(_spec(model="other-model"))    # model
    assert k != cache_key(_spec(schema_version="9"))     # schema version
    assert k != cache_key(_spec(input="different input"))  # input
    assert k != cache_key(_spec(config_hash="cfg2"))     # config hash
    assert k != cache_key(_spec(preproc_version="p2"))   # preprocessor version


# ── hit / miss through execute ───────────────────────────────────────────
def test_identical_input_hits_cache():
    cache_clear()
    sink = []
    r1 = execute(_spec(), _call=_counting("first", sink))
    r2 = execute(_spec(), _call=_counting("SHOULD-NOT-RUN", sink))
    assert r1.via == "model"
    assert r2.via == "cache" and r2.cache_hit is True
    assert r2.value == r1.value
    assert len(sink) == 1                                # model called once


def test_changed_prompt_misses():
    cache_clear()
    sink = []
    execute(_spec(prompt_version="1"), _call=_counting("v1", sink))
    r = execute(_spec(prompt_version="2"), _call=_counting("v2", sink))
    assert r.via == "model"
    assert len(sink) == 2


def test_changed_model_misses():
    cache_clear()
    sink = []
    execute(_spec(model="a"), _call=_counting("x", sink))
    r = execute(_spec(model="b"), _call=_counting("y", sink))
    assert r.via == "model"
    assert len(sink) == 2


# ── safety rules ─────────────────────────────────────────────────────────
def test_cache_hit_preserves_review_required():
    cache_clear()
    sink = []
    execute(_spec(review_required=True), _call=_counting("x", sink))
    r = execute(_spec(review_required=True), _call=_counting("y", sink))
    assert r.via == "cache"
    assert r.review_required is True       # gate NOT bypassed
    assert r.advisory is True


def test_cached_output_revalidated_bad_cache_not_served():
    cache_clear()
    schema = {"type": "object", "required": ["role"]}
    s = _spec(schema=schema)
    llm_cache.set(cache_key(s), {"bad": "value"})   # poison with an invalid value
    sink = []
    r = execute(_spec(schema=schema), _call=_counting('{"role":"download"}', sink))
    assert r.via == "model"                # invalid cached value rejected
    assert r.value == {"role": "download"}
    assert len(sink) == 1


def test_cache_stores_no_raw_input():
    cache_clear()
    sink = []
    distinctive = "ZZUNIQUEINPUTTOKEN42"
    execute(_spec(input=distinctive), _call=_counting("out", sink))
    dump = json.dumps(llm_cache._store)
    assert distinctive not in dump          # raw input never persisted
    k = cache_key(_spec(input=distinctive))
    assert all(c in "0123456789abcdef" for c in k)   # key is a hash, not the input


def test_cache_clearable():
    cache_clear()
    sink = []
    execute(_spec(), _call=_counting("x", sink))
    assert llm_cache.size() >= 1
    cache_clear()
    assert llm_cache.size() == 0


def test_no_cache_when_disabled():
    cache_clear()
    sink = []
    execute(_spec(use_cache=False), _call=_counting("x", sink))
    execute(_spec(use_cache=False), _call=_counting("y", sink))
    assert len(sink) == 2
    assert llm_cache.size() == 0


def test_stats_track_hits_and_misses():
    cache_clear()
    sink = []
    execute(_spec(), _call=_counting("x", sink))   # miss + set
    execute(_spec(), _call=_counting("y", sink))   # hit
    st = llm_cache.stats()
    assert st["hits"] >= 1 and st["sets"] >= 1
