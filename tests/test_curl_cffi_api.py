"""Contract tests for runner.py's curl_cffi usage (v3.63.10).

Bug shape (v3.63.9). Both the single-stream HTTP downloader and the
parallel-chunk HTTP downloader called `cffi_requests.stream("GET", ...)`.
curl_cffi never exposed a module-level `stream` function (only
`Session().stream()` and module-level `request(stream=True, ...)`),
so every direct HTTP download raised `AttributeError` and fell back
to the browser path. Functional but ~5-10x slower and triggered the
15-min worker-hung watchdog under load on real sites.

These tests do two things:

1. Source-level pin. Grep `runner.py` for the bug shape (`<name>.stream("GET", ...)`
   on a curl_cffi binding) and assert it's not there. The httpx
   fallback's `httpx.stream("GET", ...)` IS valid and is exempted.

2. Runtime check. If curl_cffi is installed, verify the module
   exposes `request` (the API we use) and does NOT expose `stream`
   (the API we mistakenly used). A future curl_cffi version that
   adds module-level `stream` is harmless — but a version that
   drops `request` would silently break the same path, and this
   test would catch it.
"""

# H622 slice A. An ordinary module test: its subject is the module under
# test, not the tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PY = REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.404: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


# ── Source-level pin ───────────────────────────────────────────────

def test_runner_does_not_call_module_level_cffi_stream():
    """The shape `<cffi-binding>.stream("GET", ...)` must not appear
    in runner.py. v3.63.9 had two such occurrences (line ~9117 and
    ~9574); v3.63.10 replaces both with `.request(..., stream=True)`."""
    text = _bd_runner_src()
    # All names runner.py binds curl_cffi.requests to. Add to this
    # list if a new alias appears — the bug returns the same way
    # under any alias.
    cffi_names = ["cffi_requests", "_cffi", "cr"]
    for name in cffi_names:
        # Match `<name>.stream(` followed by a quoted method string
        # ("GET" / "POST" / etc). Excludes incidental .stream that
        # might appear in unrelated APIs.
        pattern = re.compile(
            rf"\b{re.escape(name)}\.stream\s*\(\s*['\"](?:GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS)",
        )
        m = pattern.search(text)
        assert m is None, (
            f"runner.py contains `{name}.stream(\"METHOD\", ...)` — "
            f"the v3.63.9 bug. curl_cffi has no module-level "
            f"`stream` function; use `{name}.request(\"METHOD\", "
            f"url, stream=True, ...)` instead. Match at offset "
            f"{m.start() if m else -1}."
        )


def test_runner_uses_request_stream_true_for_cffi():
    """Positive shape pin: the shared download helper must pass the cffi
    binding to h3_transport.request("GET", ..., stream=True, ...).
    Removing the streaming request must fail rather than pass on absence."""
    text = _bd_runner_src()
    # The transport wrapper takes the cffi binding before the method.
    pattern = re.compile(
        r"\bh3_transport\.request\s*\(\s*(?:cffi_requests|_cffi|cr)\s*,\s*['\"]GET['\"]"
        r"[^)]{0,400}?stream\s*=\s*True",
        re.DOTALL,
    )
    matches = pattern.findall(text)
    assert len(matches) >= 1, (
        "runner.py is missing the expected curl_cffi streaming-call "
        "shape `h3_transport.request(<binding>, \"GET\", ..., stream=True, ...)`."
    )


# ── Runtime check (skip if curl_cffi not installed) ────────────────

def test_curl_cffi_module_has_request_function():
    """The installed curl_cffi must expose `request` as a module-level
    function. If a future version renames or removes this, runner.py
    breaks the same way v3.63.9 did and this test fires the alarm."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        pytest.skip("curl_cffi not installed in this environment")
    assert hasattr(cffi_requests, "request"), (
        "curl_cffi.requests no longer exposes `request`. runner.py's "
        "HTTP download path uses `curl_cffi.requests.request(\"GET\", "
        "url, stream=True, ...)` — find the new replacement in the "
        "curl_cffi changelog and update runner.py + this test."
    )


def test_curl_cffi_session_has_stream_method():
    """Defensive: if module-level `stream` is ever re-added AND the
    Session.stream method is dropped, the present test catches the
    less-obvious half. Session.stream is the documented streaming API.
    """
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        pytest.skip("curl_cffi not installed in this environment")
    assert hasattr(cffi_requests.Session, "stream"), (
        "curl_cffi.Session.stream is missing — the documented "
        "streaming API surface has shifted. Audit runner.py's "
        "HTTP-path code paths against the current curl_cffi docs."
    )
