"""F2.3 — Screenshot triage UI (needs_review tab) SPA wiring pins.

Rides migration T11's *UI affordance* pattern but is a DISTINCT surface: a
global needs_review triage tab over ``/api/history?status=needs_review`` that
renders each stalled job's screenshot (the FIRST SPA surface to render
``/screenshots/<path>``) beside its message, with three operator actions
wired to EXISTING, already-tested endpoints — NO new routes, zero new write
paths:

  approve -> POST /api/sites/<sid>/bulk_approve {urls:[url]}
             (operator override: bypass the min_resolution threshold + re-download)
  retry   -> POST /api/sites/<sid>/retry_one    {url}
             (re-queue the stalled job)
  skip    -> POST /api/sites/<sid>/jobs/mark    {url, status:"failed"}
             (dismiss; freezes auto-retry so the scanner stops bumping it)

PLAN CORRECTION (re-derived from source, NOT the capability-roadmap prose):
the roadmap said "actions = the EXISTING approve/retry/skip endpoints (T11
ports them)". T11 did NOT port these — it ported the auto-submit/post-reveal
*challenge* gate (approve/DECLINE, keyed). The approve/retry/skip this tab
needs map to queue endpoints that PREDATE T11 and are already pinned for
behaviour by test_v3_43_23_quick_wins / test_v3_49_phase2 / test_d3_u5_queue.
So F2.3 depends on T11 only for the affordance pattern, and the roadmap's
"no new routes / zero new write paths" holds for real. (The handoff prose's
"over the cockpit review-candidates queue" crossed two unrelated 'review'
surfaces — /cockpit/api/review-candidates is the TEMPLATE-draft queue. The
authoritative source is /history rows carrying a screenshot.)

RED on pristine v3.66.264 (proven before implementing):
  * test_action_endpoints_spa_wired           — bulk_approve + jobs/mark read
    spa_wired=False until useNeedsReview.ts lands the full /api/ literals.
    (retry_one is ALREADY spa_wired via JobErrorModal -> regression-only.)
  * test_useneedsreview_full_literals_present  — the hook does not exist yet.
  * test_needsreview_route_registered          — NeedsReview.tsx absent +
    App.tsx does not mount /needs-review + the palette has no entry yet.
  * test_screenshot_src_uses_serving_route      — NeedsReview.tsx absent, so
    no safe /screenshots/ construction exists yet (this is the FIRST SPA
    surface to render screenshots; the pin lands with the route).

GREEN throughout (guardrails + the backend contract F2.3 leans on):
  * test_no_raw_mutating_fetch_to_action_paths — the three POSTs must ride
    apiPost (X-CSRF-Token), never a raw fetch().
  * test_action_endpoints_registered_and_contract — the three route
    decorators are present in app.py source and jobs/mark still accepts
    {failed, done} (the approve/skip semantics the SPA depends on).

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

import spa_population  # noqa: E402  (needs the sys.path insert above)

pytestmark = pytest.mark.bd_module_wipe

SPA = REPO / "frontend" / "src"

# The three backend endpoints F2.3 wires (normalised spelling: path params
# -> '*', method dropped — matches gui_parity_inventory._norm_ep).
ACTION_ENDPOINTS = [
    "/api/sites/*/bulk_approve",
    "/api/sites/*/retry_one",
    "/api/sites/*/jobs/mark",
]

# bulk_approve + jobs/mark are unwired on pristine 264 (the RED gate);
# retry_one is already wired (JobErrorModal) so it is a regression pin.
RED_ENDPOINTS = [
    "/api/sites/*/bulk_approve",
    "/api/sites/*/jobs/mark",
]

# The exact FULL /api/ literals the hook must carry for scanner credit
# (template literals with ${sid}, NOT a concatenated base var).
REQUIRED_LITERALS = [
    "/api/sites/${sid}/bulk_approve",
    "/api/sites/${sid}/retry_one",
    "/api/sites/${sid}/jobs/mark",
]


def _load_inventory():
    spec = importlib.util.spec_from_file_location(
        "gui_parity_inventory", REPO / "tools" / "gui_parity_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── RED: SPA wiring ──────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_action_endpoints_spa_wired():
    """bulk_approve + jobs/mark must read spa_wired=True in the parity
    inventory once useNeedsReview.ts carries the full /api/ literals. RED on
    pristine 264 (the SPA references neither), GREEN after the hook lands.
    retry_one is already wired (JobErrorModal); it must STAY wired."""
    inv = _load_inventory()
    items = inv.build(str(REPO))["items"]
    want = set(ACTION_ENDPOINTS)
    by_ep = {}
    for it in items:
        n = inv._norm_ep(it.get("command_or_endpoint") or "")
        if n in want:
            if not by_ep.get(n) or it.get("spa_wired"):
                by_ep[n] = it
    # RED gate: the two newly-wired endpoints.
    unwired = [w for w in RED_ENDPOINTS
               if not (by_ep.get(w) and by_ep[w].get("spa_wired"))]
    assert not unwired, (
        "F2.3 action endpoints not spa_wired in the inventory "
        "(useNeedsReview.ts must carry the full /api/ literals): "
        + repr(unwired))
    # Regression: retry_one stays wired.
    ro = by_ep.get("/api/sites/*/retry_one")
    assert ro and ro.get("spa_wired"), (
        "retry_one regressed to unwired — JobErrorModal/useNeedsReview must "
        "keep the full /api/ literal")


def test_useneedsreview_full_literals_present():
    """The hook must carry FULL /api/ template literals (scanner credit) —
    not a concatenated base var. RED on pristine 264 (file absent)."""
    hook = SPA / "hooks" / "useNeedsReview.ts"
    assert hook.exists(), "frontend/src/hooks/useNeedsReview.ts does not exist"
    text = hook.read_text(encoding="utf-8", errors="replace")
    missing = [lit for lit in REQUIRED_LITERALS if lit not in text]
    assert not missing, (
        "full /api/ literals missing from useNeedsReview.ts: " + repr(missing))


def test_needsreview_route_registered():
    """NeedsReview.tsx must exist, App.tsx must mount /needs-review, and the
    Command Palette must offer a way to reach it. RED on pristine 264."""
    route = SPA / "routes" / "NeedsReview.tsx"
    assert route.exists(), "frontend/src/routes/NeedsReview.tsx does not exist"

    app_tsx = (SPA / "App.tsx").read_text(encoding="utf-8", errors="replace")
    assert "/needs-review" in app_tsx, "App.tsx does not mount /needs-review"
    assert "NeedsReview" in app_tsx, "App.tsx does not import NeedsReview"

    palette = (SPA / "components" / "CommandPalette.tsx").read_text(
        encoding="utf-8", errors="replace")
    assert "/needs-review" in palette, (
        "CommandPalette.tsx has no entry to reach /needs-review")


# ── GREEN guardrails ─────────────────────────────────────────────────

_ACTION_FETCH_RE = re.compile(
    r"fetch\([^;]{0,400}?(bulk_approve|retry_one|jobs/mark)"
    r"[^;]{0,400}?method:\s*[\"'](POST|PUT|PATCH|DELETE)[\"']",
    re.S,
)


def test_no_raw_mutating_fetch_to_action_paths():
    """The three action POSTs must ride apiPost (which injects
    X-CSRF-Token), never a raw fetch() — a raw mutating fetch to these paths
    would 403 on a real cookie session and route around the wrapper.

    POPULATION: PRODUCT-ONLY (row 232). This asks about DEPLOYED behaviour --
    what ships to a browser -- and a Vitest spec never ships: it stubs global
    fetch and asserts against the stub, so a raw mutating fetch inside one is a
    NEGATIVE CONTROL, not a vulnerability. That exact shape broke CI on a
    correct cut at v3.66.1218 for the sibling scanner in test_t5_t6_wired.py;
    this file carried the identical defect, unfixed, until row 232.
    require_both_halves keeps the narrowing honest -- an empty product half
    would report no offenders and pass over anything at all."""
    src_dir = SPA
    selected, excluded = spa_population.select(src_dir)
    spa_population.require_both_halves(
        selected, excluded, "test_no_raw_mutating_fetch_to_action_paths")
    offenders = []
    for rel in selected:
        f = src_dir / rel
        if _ACTION_FETCH_RE.search(f.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(f.relative_to(REPO)))
    assert not offenders, (
        "raw state-changing fetch() to an action path (must use apiPost): "
        + repr(offenders))


def test_screenshot_src_uses_serving_route():
    """F2.3 is the first SPA surface to render /screenshots/<path>. The <img>
    src must go through the serving route built from site_id + the file
    BASENAME (so a stored directory component is never blindly trusted), and
    must never use a file:// URL. Pins the CWE-22-safe construction that
    mirrors the serve_ss boundary check + the AI-reanalyze precedent
    (SCREENSHOTS_DIR / sid / Path(ss_path).name)."""
    rp = SPA / "routes" / "NeedsReview.tsx"
    route = rp.read_text(encoding="utf-8", errors="replace") if rp.exists() else ""
    assert "/screenshots/${" in route, (
        "screenshot src must use the /screenshots/ serving route as a "
        "template literal")
    # basename extraction — never trust the stored directory component.
    assert '.split("/").pop()' in route or ".split('/').pop()" in route, (
        "screenshot src must build from the file basename "
        "(.split('/').pop()), not the raw stored path")
    # No file:// URL in actual CODE. Strip line + block comments first so an
    # incidental "file://" mention in a comment doesn't false-trip the guard.
    code = re.sub(r"/\*.*?\*/", "", route, flags=re.S)
    code = "\n".join(
        ln for ln in code.splitlines() if not ln.lstrip().startswith("//"))
    assert "file://" not in code, "screenshot src must not use a file:// URL"


def test_action_endpoints_registered_and_contract():
    """The three endpoints the SPA calls must exist in app.py source (so the
    targets can't silently disappear), and jobs/mark must still accept the
    {done, failed} statuses the approve/skip actions depend on."""
    app_src = _APP_SRC()
    for dec in (
        '"/api/sites/<sid>/bulk_approve"',
        '"/api/sites/<sid>/retry_one"',
        '"/api/sites/<sid>/jobs/mark"',
    ):
        assert dec in app_src, "missing route decorator: " + dec

    # jobs/mark allowed-status contract (the approve->done / skip->failed
    # semantics). Locate the handler and assert its allowed set.
    i = app_src.index('def api_jobs_mark(sid):')
    body = app_src[i:i + 1200]
    m = re.search(r"allowed\s*=\s*\{([^}]*)\}", body)
    assert m, "could not find jobs/mark allowed-status set"
    allowed = m.group(1)
    assert '"done"' in allowed and '"failed"' in allowed, (
        "jobs/mark allowed set must include done + failed (approve/skip "
        "semantics): " + allowed)
