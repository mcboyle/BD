"""Current live and stream SPA contract.

Proves the 5 live/stream endpoint families remain SPA-wired:

  - 4 live families wired via the new hooks/useLive.ts (FULL /api/ literals);
    status/recordings are useQuery (status polls via refetchInterval),
    watch/unwatch are useMutation (never auto-fire, B-tier confirm at the
    LiveSection).
  - /api/stream/token/<hid> was already used in routes/Library.tsx but written
    as a `"/api/stream/token/" + hid` CONCAT the static scanner never credited;
    T9a literalized it to `/api/stream/token/${hid}` (the concat form is gone).
  - LiveSection is imported and rendered in Library.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

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


def _fn_body(text, name):
    """Slice an exported function body from `export function NAME` to the next
    top-level `export function` (or EOF). Good enough to scope a hint check."""
    m = re.search(r"export function " + re.escape(name) + r"\b", text)
    assert m, f"{name} not found"
    rest = text[m.end():]
    nxt = re.search(r"\nexport function ", rest)
    return rest[: nxt.start()] if nxt else rest


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
