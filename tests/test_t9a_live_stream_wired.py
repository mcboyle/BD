"""T9a live + stream tranche — migration pins (v3.66.212).

Proves the 5 legacy-only live/stream families are now SPA-wired (they drop
out of the legacy_parity legacy-only set computed from frontend/src):

  - 4 live families wired via the new hooks/useLive.ts (FULL /api/ literals);
    status/recordings are useQuery (status polls via refetchInterval),
    watch/unwatch are useMutation (never auto-fire, B-tier confirm at the
    LiveSection).
  - /api/stream/token/<hid> was already used in routes/Library.tsx but written
    as a `"/api/stream/token/" + hid` CONCAT the static scanner never credited;
    T9a literalized it to `/api/stream/token/${hid}` (the concat form is gone).
  - LiveSection is imported and rendered in Library.
  - Ratchet committed 10 -> 5. Pinned as a CEILING (<= 5) not an equality, so
    T9b (push -> 1) does not re-break it — the stale-magnitude-floor lesson
    from the T7 `== 34` fix.

RED on pristine v3.66.211 (live unwired; stream a concat; no useLive hook;
no LiveSection mount; baseline 10).

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

LIVE_ENDPOINTS = [
    "/api/live/status",
    "/api/live/recordings",
    "/api/live/watch",
    "/api/live/unwatch",
]
# normalized (param -> *) form of the stream family
STREAM_NORM = "/api/stream/token/*"
T9A_FAMILIES_NORM = [re.sub(r"\{[^}]+\}", "*", e) for e in LIVE_ENDPOINTS] + [STREAM_NORM]


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(s):
    return {re.sub(r"\{[^}]+\}", "*", e) for e in s}


def _fn_body(text, name):
    """Slice an exported function body from `export function NAME` to the next
    top-level `export function` (or EOF). Good enough to scope a hint check."""
    m = re.search(r"export function " + re.escape(name) + r"\b", text)
    assert m, f"{name} not found"
    rest = text[m.end():]
    nxt = re.search(r"\nexport function ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_all_5_t9a_endpoints_are_spa_wired():
    """None of the 5 live/stream families may remain in the legacy-only set."""
    lp = _load_legacy_parity()
    legacy_only = _norm(set(lp.measure()["legacy_only"]))
    still = [ep for ep in T9A_FAMILIES_NORM if ep in legacy_only]
    assert not still, "T9a families still legacy-only: " + repr(still)


def test_t9a_live_literals_present_in_hook():
    """FULL /api/ literals — the scanner cannot credit concatenated bases."""
    hook = (SRC / "hooks" / "useLive.ts").read_text(encoding="utf-8")
    for ep in LIVE_ENDPOINTS:
        assert f'"{ep}"' in hook or f"`{ep}" in hook, \
            f"{ep} not a full literal in useLive.ts"


def test_t9a_stream_literalized_in_library():
    """Stream token is a FULL template literal now, not the old concat."""
    lib = (SRC / "routes" / "Library.tsx").read_text(encoding="utf-8")
    assert "`/api/stream/token/${" in lib, "stream token not a template literal"
    assert '"/api/stream/token/" +' not in lib, "old concat form still present"


def test_t9a_status_polls_watch_unwatch_are_mutations():
    """status/recordings are queries (status polls); writes are mutations."""
    hook = (SRC / "hooks" / "useLive.ts").read_text(encoding="utf-8")
    status = _fn_body(hook, "useLiveStatus")
    assert re.search(r"\buseQuery\b", status), "status must be a query"
    assert "refetchInterval" in status, "status must poll (refetchInterval)"
    for w in ("useLiveWatch", "useLiveUnwatch"):
        body = _fn_body(hook, w)
        assert "useMutation" in body, f"{w} must be a mutation (never auto-fire)"


def test_t9a_section_mounted_in_library():
    """LiveSection is imported and rendered in the Library route."""
    lib = (SRC / "routes" / "Library.tsx").read_text(encoding="utf-8")
    assert "from \"@/components/sections/LiveSection\"" in lib, \
        "LiveSection not imported"
    assert "<LiveSection" in lib, "LiveSection not rendered"


def test_t9a_ratchet_committed_ceiling():
    """Ratchet committed 10 -> 5; pinned as a ceiling so T9b won't re-break it.

    (Stale-magnitude-floor lesson from the T7 `== 34` fix: equality pins on a
    monotonic-down ratchet break on the next tranche. Assert the invariant —
    the 5 families are gone from the committed baseline — plus a <= ceiling.)
    """
    base = json.loads((REPO / "reports" / "legacy_parity_baseline.json").read_text())
    assert base["legacy_only_count"] <= 5, base["legacy_only_count"]
    committed = _norm(set(base["legacy_only"]))
    still = [ep for ep in T9A_FAMILIES_NORM if ep in committed]
    assert not still, "T9a families still in committed baseline: " + repr(still)
