"""D3 follow-up U14 contract — log-diff side-by-side.

Covers:
  - GET /api/queue/v2/job_log_diff exists and rejects GET-less paths
    + missing-param paths cleanly (no 500).
  - Endpoint returns ok=True even when one side resolves to unknown
    site_id (partial result is still useful).
  - The diff is computed server-side via difflib.unified_diff so
    the SPA doesn't bundle a diff library.
  - URL parser splits on first colon (URLs contain colons themselves).
  - LogDiff route file exists and is mounted in App.tsx at /logs/diff.
  - LogDiffResponse + JobLogDiffSide types exist in api-types.
  - JobErrorModal renders the Compare flow (Compare with… / Compare
    now / Cancel pick).
  - Compare pick uses sessionStorage with the bd-log-diff-pick key.
  - LogDiff side-by-side panes scroll-sync via paired listeners.
  - Diff lines colorize +/-/@@ via the canonical unified_diff prefixes.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell):
    route handlers now live in app_<group>.py, so source-level handler-body
    assertions read the aggregate rather than app.py alone."""
    pkg = _REPO / "bulk_downloader"
    parts = [(pkg / "app.py").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)


def _frontend_file(*parts: str) -> Path:
    return _REPO / "frontend" / "src" / Path(*parts)


# ── Backend ─────────────────────────────────────────────────────────


def test_u14_diff_route_registered():
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    assert "/api/queue/v2/job_log_diff" in rules, (
        "GET /api/queue/v2/job_log_diff missing"
    )


def test_u14_diff_rejects_missing_params():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/queue/v2/job_log_diff")
    assert r.status_code == 400


def test_u14_diff_handles_unknown_sites_without_500():
    """When either a or b refers to a missing site/url, the response
    must still be 200 with ok=True and the affected side reporting
    ok=false in its own block. Partial-result tolerance."""
    from bulk_downloader import app as a
    r = a.app.test_client().get(
        "/api/queue/v2/job_log_diff?a=nope:https://x/y&b=nope:https://x/z"
    )
    assert r.status_code == 200, "must not 500 on unknown side"
    body = r.get_json()
    assert body and body.get("ok") is True
    assert body["a"]["ok"] is False
    assert body["b"]["ok"] is False
    # And the diff list is present (likely empty).
    assert isinstance(body.get("diff"), list)


def test_u14_diff_uses_difflib_unified_diff():
    """Server-side diff means the SPA doesn't bundle a diff library."""
    src = _APP_SRC()
    handler = re.search(
        r"def api_queue_v2_job_log_diff\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert handler, "api_queue_v2_job_log_diff handler not found"
    body = handler.group(0)
    assert "from difflib import unified_diff" in body, (
        "diff endpoint must use difflib.unified_diff"
    )
    assert "unified_diff(" in body


def test_u14_diff_parser_splits_on_first_colon():
    """URLs contain colons (http://, ports). The 'site_id:url' parser
    must split on the FIRST colon only."""
    src = _APP_SRC()
    parser = re.search(
        r"def _diff_parse_target\b.*?(?=\n@app\.route|\ndef \w)",
        src,
        re.DOTALL,
    )
    assert parser, "_diff_parse_target helper not found"
    body = parser.group(0)
    # Find-first-colon idiom: index then slice.
    assert "index(" in body and "[:ix]" in body and "[ix + 1:]" in body, (
        "_diff_parse_target must split on the FIRST colon"
    )


def test_u14_diff_event_messages_truncated_to_500():
    src = _APP_SRC()
    handler = re.search(
        r"def _diff_collect_one\b.*?(?=\n@app\.route|\ndef \w|\ndef _diff)",
        src,
        re.DOTALL,
    )
    assert handler
    body = handler.group(0)
    # Same shape the single-side job_log uses.
    assert "[:500]" in body, "diff event messages must be truncated to 500 chars"


# ── Frontend ────────────────────────────────────────────────────────


def test_u14_log_diff_route_present():
    f = _frontend_file("routes", "LogDiff.tsx")
    assert f.is_file(), f"missing {f}"


def test_u14_log_diff_mounted_in_app_tsx():
    src = _frontend_file("App.tsx").read_text(encoding="utf-8")
    assert "LogDiff" in src and 'path="/logs/diff"' in src, (
        "App.tsx must mount LogDiff at /logs/diff"
    )


def test_u14_log_diff_types_present():
    src = _frontend_file("lib", "api-types.ts").read_text(encoding="utf-8")
    for t in ("JobLogDiffResponse", "JobLogDiffSide"):
        assert f"export interface {t}" in src, (
            f"api-types.ts must export {t}"
        )


def test_u14_log_diff_uses_endpoint():
    src = _frontend_file("routes", "LogDiff.tsx").read_text(encoding="utf-8")
    assert "/api/queue/v2/job_log_diff" in src, (
        "LogDiff must call /api/queue/v2/job_log_diff"
    )


def test_u14_log_diff_scroll_sync_paired():
    """Both panes must subscribe to each other's scroll events, with
    a lock to prevent the inverse handler from bouncing."""
    src = _frontend_file("routes", "LogDiff.tsx").read_text(encoding="utf-8")
    assert 'addEventListener("scroll"' in src
    assert "requestAnimationFrame" in src, (
        "scroll sync should release the lock on next frame to avoid bounce"
    )


def test_u14_log_diff_colors_unified_diff_prefixes():
    """The diff renderer must colorize +/-/@@ per the standard
    unified_diff prefixes."""
    src = _frontend_file("routes", "LogDiff.tsx").read_text(encoding="utf-8")
    assert "diffLineClass" in src, "diff line classifier helper missing"
    for prefix in ('"+"', '"-"', '"@@"'):
        assert prefix in src, f"diff colorizer must handle the {prefix} prefix"


# ── JobErrorModal Compare flow ──────────────────────────────────────


def test_u14_job_error_modal_compare_flow_present():
    src = _frontend_file("components", "JobErrorModal.tsx").read_text(encoding="utf-8")
    assert "Compare with" in src, "JobErrorModal must expose Compare with… button"
    assert "Compare now" in src, "JobErrorModal must expose Compare now action"
    assert "Cancel pick" in src, "JobErrorModal must expose Cancel pick action"


def test_u14_compare_pick_uses_sessionstorage_key():
    src = _frontend_file("components", "JobErrorModal.tsx").read_text(encoding="utf-8")
    assert '"bd-log-diff-pick"' in src, (
        "Compare pick must use the bd-log-diff-pick key"
    )
    # And sessionStorage, NOT localStorage — comparison picks are
    # per-tab, not per-device.
    assert "sessionStorage.getItem" in src and "sessionStorage.setItem" in src, (
        "Compare pick must use sessionStorage (per-tab), not localStorage"
    )


def test_u14_compare_pick_validates_shape():
    """A future Compare pick that has a corrupt sessionStorage blob
    (manual edit, version mismatch) must not crash the modal."""
    src = _frontend_file("components", "JobErrorModal.tsx").read_text(encoding="utf-8")
    reader = re.search(
        r"function readPick\(\)[^{]*\{(.*?)\n\}",
        src,
        re.DOTALL,
    )
    assert reader, "readPick helper not found"
    body = reader.group(1)
    assert "typeof parsed.site_id" in body
    assert "typeof parsed.url" in body
    assert "typeof parsed.filename" in body


def test_u14_compare_now_navigates_to_logs_diff():
    src = _frontend_file("components", "JobErrorModal.tsx").read_text(encoding="utf-8")
    assert "/logs/diff?a=" in src, (
        "Compare now must navigate to /logs/diff?a=…&b=…"
    )
    assert "useNavigate" in src, (
        "JobErrorModal must import useNavigate for the Compare now flow"
    )


def test_u14_compare_pick_cleared_after_navigate():
    """After navigation, the pick must be cleared — otherwise the
    next modal open shows a stale Compare banner."""
    src = _frontend_file("components", "JobErrorModal.tsx").read_text(encoding="utf-8")
    # The body sits between `const handleCompareNow = () => {` and a
    # `\n  };` matching the outer indentation. Anchor on that.
    m = re.search(
        r"const handleCompareNow\s*=\s*\(\s*\)\s*=>\s*\{(.*?)\n  \};",
        src,
        re.DOTALL,
    )
    assert m, "handleCompareNow body not found"
    body = m.group(1)
    assert "writePick(null)" in body, (
        "handleCompareNow must clear the pick before navigating"
    )
    write_at = body.find("writePick(null)")
    nav_at = body.find("navigate(")
    assert nav_at >= 0, "handleCompareNow must call navigate()"
    assert write_at < nav_at, (
        "writePick(null) must come before navigate()"
    )
