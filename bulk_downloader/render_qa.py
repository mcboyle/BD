"""Phase 9.8 -- render/route QA brain (deterministic).

The self-maintaining route QA logic, derived from source (App.tsx + settings
schema) rather than hand-maintained lists. The Playwright render loop, montage
assembly, and screenshot capture live in the dev harness
(`render_harness/spa_render_all.py`, `bd-render --all`); this module holds the
parts that are pure and unit-testable: enumeration, probe substitution, the drift
guard, per-view quality analysis, domain-ownership validation, and the HTML index.

Reads App.tsx / settings schema READ-ONLY; performs no I/O of its own.
"""

import re
from typing import Any, Dict, List, Set

# probe values substituted for :params when resolving a route to a URL.
_PARAM_PROBES = {"siteId": "1", "id": "1", "jobId": "1", "templateId": "1"}

MOCK_DATA_MODES = ["empty", "populated", "error", "stale", "warning-heavy"]

_ROUTE_RE = re.compile(r'path="([^"]*)"')
_SECTION_BLOCK_RE = re.compile(
    r"SETTINGS_SECTIONS[^=]*=\s*\[(.*?)\]", re.DOTALL)


def enumerate_routes(app_tsx_text: str) -> List[str]:
    """All route paths from App.tsx, excluding the `*` splat. Order preserved."""
    out: List[str] = []
    for m in _ROUTE_RE.finditer(app_tsx_text or ""):
        p = m.group(1)
        if p == "*":
            continue
        out.append(p)
    return out


def resolve_url(route: str, probes: Dict[str, str] = None) -> str:
    """Substitute probe values for `:param` segments to get a fetchable URL."""
    probes = probes or _PARAM_PROBES

    def _sub(seg: str) -> str:
        if seg.startswith(":"):
            return probes.get(seg[1:], "1")
        return seg

    parts = (route or "/").split("/")
    return "/".join(_sub(s) for s in parts) or "/"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s


def enumerate_settings_sections(schema_text: str) -> List[str]:
    """Parse the string entries of SETTINGS_SECTIONS from settingsSchema.ts."""
    m = _SECTION_BLOCK_RE.search(schema_text or "")
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))


def settings_anchor_urls(sections: List[str]) -> List[str]:
    return [f"/settings#{slugify(s)}" for s in sections]


def expected_manifest_routes(app_tsx_text: str) -> Set[str]:
    return set(enumerate_routes(app_tsx_text))


def drift_check(expected_routes, manifest_routes) -> Dict[str, Any]:
    """Drift guard: every expected (App.tsx) route must appear in the manifest."""
    exp = set(expected_routes)
    man = set(manifest_routes)
    missing = sorted(exp - man)
    extra = sorted(man - exp)
    return {"ok": not missing, "missing": missing, "extra": extra}


def analyze_view(record: Dict[str, Any]) -> List[str]:
    """Per-view QA flags from a render record. Pure: takes the measured values,
    returns a sorted flag list. (The harness measures; this judges.)"""
    flags: List[str] = []
    if record.get("console_errors", 0):
        flags.append("console_error")
    if record.get("page_errors", 0):
        flags.append("page_error")
    if record.get("failed_requests", 0):
        flags.append("failed_request")
    if record.get("unhandled_rejections", 0):
        flags.append("unhandled_rejection")
    if record.get("horizontal_overflow_px", 0):
        flags.append("horizontal_overflow")
    if not record.get("h1"):
        flags.append("missing_h1")
    if not record.get("purpose_present", True):
        flags.append("missing_purpose")
    if not record.get("primary_action_present", True):
        flags.append("missing_primary_action")
    if record.get("empty_state") and not record.get("empty_state_next_action", True):
        flags.append("missing_empty_state_action")
    if record.get("repeated_full_width_warnings", 0) > 1:
        flags.append("repeated_full_width_warnings")
    if record.get("disabled_without_reason", 0):
        flags.append("disabled_without_reason")
    return sorted(flags)


def domain_for(url: str) -> str:
    u = url or "/"
    if u.startswith("/cockpit"):
        return "cockpit"
    if u.startswith("/dashboard"):
        return "dashboard"
    return "spa"


def domain_ownership_flags(entry: Dict[str, Any]) -> List[str]:
    """Flag domain-ownership violations on a manifest entry."""
    flags: List[str] = []
    domain = entry.get("domain") or domain_for(entry.get("url", "/"))
    wcap = entry.get("write_capability", "read-only")
    if domain == "dashboard" and wcap in ("gated-write", "dangerous-action-present"):
        flags.append("dashboard_with_write")
    if domain == "cockpit" and not entry.get("governance_purpose", True):
        flags.append("cockpit_missing_governance_purpose")
    if wcap == "dangerous-action-present" and not entry.get("has_warning_treatment", True):
        flags.append("action_without_warning")
    if domain == "spa" and entry.get("duplicates_cockpit_governance"):
        flags.append("spa_duplicates_cockpit")
    return sorted(flags)


def build_index_html(manifest: List[Dict[str, Any]]) -> str:
    """Deterministic HTML index: a grid row per manifest entry, failed checks
    highlighted. No external assets; safe to write to render_all/index.html."""
    rows = []
    for e in manifest:
        flags = e.get("flags", [])
        cls = "bad" if flags or e.get("status") != "ok" else "ok"
        rows.append(
            f'<tr class="{cls}"><td>{e.get("route","")}</td>'
            f'<td>{e.get("theme","")}</td><td>{e.get("status","")}</td>'
            f'<td>{", ".join(flags)}</td>'
            f'<td>{e.get("diff_score", 0)}</td></tr>'
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Render QA</title>"
        "<style>table{border-collapse:collapse}td{border:1px solid #ccc;padding:4px}"
        ".bad{background:#fee}.ok{background:#efe}</style></head><body>"
        "<h1>Render QA index</h1>"
        "<table><tr><th>route</th><th>theme</th><th>status</th>"
        "<th>flags</th><th>diff</th></tr>"
        + "".join(rows) +
        "</table></body></html>"
    )
