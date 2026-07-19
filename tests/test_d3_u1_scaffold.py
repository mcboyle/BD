"""D3 U1 scaffold contract tests.

Pins the structural shape of the v3.64.0-prep React SPA scaffold:

  1. frontend/package.json exists with the locked dependency set.
  2. frontend/vite.config.ts mounts at base="/m2/" — must match
     the Flask serve_m2_spa() route prefix or every asset 404s.
  3. /m2 endpoints return 503 with X-BD-M2-Status: not-built when
     frontend/dist/ is absent (the "Node missing" surface).
  4. The /m2 handler is pure module-level (no module-level work
     that would run during every test import).
  5. install_linux.sh and install_windows.bat have non-fatal Node
     detection blocks; install completes if Node is missing.

These tests pin behaviour, not implementation. If a future refactor
keeps the contracts but moves files, fine. If a refactor drops the
503 message or changes the mount point unilaterally, this catches it.
"""
from __future__ import annotations

import json
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

# Repo root — sibling of bulk_downloader/.
_REPO = Path(__file__).resolve().parent.parent


# ── Frontend tree shape ────────────────────────────────────────────────


def test_d3_u1_frontend_dir_exists():
    """frontend/ at repo root — the SPA source tree."""
    assert (_REPO / "frontend").is_dir(), \
        "frontend/ missing — D3 scaffold has been removed"


def test_d3_u1_package_json_has_locked_deps():
    """package.json declares the locked stack from the design narrative.

    The narrative pins React 18 (not 19), React Router (not TanStack
    Router), TanStack Query, Tailwind, Vite. If any of those vanish
    we're off-spec; if extras land we want to know it was deliberate.
    """
    pkg_path = _REPO / "frontend" / "package.json"
    assert pkg_path.is_file(), "frontend/package.json missing"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    required = [
        "react", "react-dom", "react-router-dom",
        "@tanstack/react-query",
        "@radix-ui/react-dialog", "@radix-ui/react-tabs",
        "@radix-ui/react-dropdown-menu",
        "sonner", "recharts", "cmdk",
        "clsx", "tailwind-merge", "class-variance-authority",
        "lucide-react",
    ]
    for r in required:
        assert r in deps, f"frontend/package.json: required dep '{r}' missing"

    # React must be 18.x, not 19 — design narrative explicitly chose
    # 18 for ecosystem maturity at the time of writing.
    react_ver = deps["react"]
    assert react_ver.startswith("^18.") or react_ver.startswith("18."), \
        f"react pin is {react_ver!r}, expected ^18.x (per D3 narrative)"

    dev_deps = pkg.get("devDependencies", {})
    for r in ["vite", "@vitejs/plugin-react", "typescript",
              "tailwindcss", "postcss", "autoprefixer", "vitest"]:
        assert r in dev_deps, f"frontend/package.json: required devDep '{r}' missing"


def test_d3_u1_vite_base_matches_flask_route():
    """vite.config.ts base must be "/" — same as Flask serve_spa_root().

    RE-EXPRESSED at v3.66.203 (Phase 1 root flip): the SPA moved from
    /m2 to the site root. If base diverges from the mount, the built
    SPA's asset URLs won't match where Flask serves from. This is the
    single coupling point between the build and the mount.
    """
    vite_cfg = (_REPO / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert re.search(r'base:\s*["\']/["\']', vite_cfg), \
        "vite.config.ts: base != \"/\" — must match the Flask root mount"


def test_d3_u1_shadcn_components_prestaged():
    """U1 pre-stages 10 shadcn primitives. Catches accidental deletion.

    Per operator lock-in: pre-stage now (not per-unit). If U3+ removes
    one because it found a better primitive, fine — update this test
    deliberately. Silent drop = test failure.
    """
    ui_dir = _REPO / "frontend" / "src" / "components" / "ui"
    assert ui_dir.is_dir(), "frontend/src/components/ui/ missing"
    expected = [
        "button.tsx", "card.tsx", "input.tsx", "badge.tsx",
        "skeleton.tsx", "dialog.tsx", "tabs.tsx",
        "dropdown-menu.tsx", "command.tsx",
    ]
    # NB: Toast intentionally NOT in this list — sonner ships its own
    # <Toaster /> and toast(), so no shadcn wrapper. Documented in
    # frontend/src/components/ui/README.md.
    for name in expected:
        assert (ui_dir / name).is_file(), \
            f"frontend/src/components/ui/{name} missing (D3 U1 pre-stage)"


def test_d3_u1_design_tokens_bound_to_css_vars():
    """Tailwind config uses CSS var(--token) indirection so dark mode is a swap.

    Per UI_TOKENS.md and design narrative: the dark-mode pairing is
    free if defined day-one. Tailwind config must reference CSS vars,
    not hardcode hex values — otherwise dark mode requires a
    cross-codebase class rename.
    """
    tw_cfg = (_REPO / "frontend" / "tailwind.config.js").read_text(encoding="utf-8")
    # Look for var(--…) bindings in the colors block.
    assert "var(--bg)" in tw_cfg, \
        "tailwind.config.js: colors should reference CSS vars, not hardcoded hex"
    assert "var(--primary)" in tw_cfg
    assert "var(--hairline)" in tw_cfg

    index_css = (_REPO / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
    # Both light defaults and a .dark override must be defined.
    assert ":root" in index_css, "index.css: :root token block missing"
    assert ".dark" in index_css, \
        "index.css: .dark token override missing — dark mode would be a rewrite"


# ── Flask /m2 mount ────────────────────────────────────────────────────


def test_d3_u1_m2_route_503_when_dist_missing(monkeypatch, tmp_path):
    """GET / (SPA root) returns 503 with the 'not-built' header when dist absent.

    The handler reads `_M2_DIST_ROOT`, which is computed at import time
    from `__file__` — i.e. it points at the real repo's frontend/dist
    regardless of cwd. A test environment where the operator has run
    `npm run build` (which is true on stash after a deploy, per
    OPEN_THREADS 1b) would otherwise serve the SPA and fail this
    assertion. Monkey-patch the module attribute to a guaranteed-absent
    path so the 503 code path is genuinely exercised. The X-BD-M2-Status
    header lets a future health probe distinguish this from a generic
    503 without parsing the body.
    """
    from bulk_downloader import app as a
    # Point at a path under tmp_path that demonstrably doesn't exist.
    # `tmp_path` itself exists; the deeper subdir does not.
    fake_dist = tmp_path / "definitely-no-dist-here" / "frontend" / "dist"
    assert not fake_dist.exists(), "test setup: fake_dist should not exist"
    monkeypatch.setattr(a, "_M2_DIST_ROOT", fake_dist)
    c = a.app.test_client()
    # RE-EXPRESSED at v3.66.203 (Phase 1 root flip): the not-built 503
    # surface moved to the root SPA mount; /m2 is a 302 shim now.
    for path in ["/", "/sites", "/queue/123"]:
        r = c.get(path)
        assert r.status_code == 503, \
            f"{path}: expected 503 when frontend/dist missing, got {r.status_code}"
        assert r.headers.get("X-BD-M2-Status") == "not-built", \
            f"{path}: missing X-BD-M2-Status: not-built header"
        # Body must name both installers so the operator doesn't have
        # to guess which OS path to take.
        body = r.get_data(as_text=True)
        assert "install_linux.sh" in body
        assert "install_windows.bat" in body
        assert "npm" in body  # manual fallback documented


def test_d3_u1_root_routes_unaffected_by_m2():
    """Coexistence, re-expressed at v3.66.334 (Phase 4 legacy deletion):
    / serves the SPA; the legacy shell AND its /legacy route were removed
    outright, so /legacy is now a clean 404 (reserved prefix, no route),
    while /m + /m2 remain 302 shims. If a future change shadows any of
    these, this catches it.
    """
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/")
    assert r.status_code in (200, 503), f"/ broke: {r.status_code}"
    r = c.get("/legacy")
    assert r.status_code == 404, f"/legacy should be removed (404): {r.status_code}"
    r = c.get("/m")
    assert r.status_code == 302, f"/m shim broke: {r.status_code}"
    r = c.get("/m2")
    assert r.status_code == 302, f"/m2 shim broke: {r.status_code}"


def test_d3_u1_m2_handler_no_module_level_work():
    """Importing bulk_downloader must not trigger frontend/dist scans.

    The app.py invariant: nothing module-level can do work that runs
    during every test import. _M2_DIST_ROOT is a Path() (cheap), and
    is.dir() only happens inside the handler. Verify the module-level
    name resolves to a Path without side effects.
    """
    from bulk_downloader import app as a
    assert hasattr(a, "_M2_DIST_ROOT")
    # Should be a Path object, not a result of an is_dir/exists check.
    from pathlib import Path as P
    assert isinstance(a._M2_DIST_ROOT, P)


# ── Installer Node detection ───────────────────────────────────────────


def test_d3_u1_install_linux_has_node_detection():
    """install_linux.sh has the non-fatal Node detection block."""
    sh = (_REPO / "install_linux.sh").read_text(encoding="utf-8")
    # Must check for node AND npm, must mention frontend, must be
    # non-fatal (no `exit 1` on missing node — `set -e` is also not
    # enabled in this script anyway).
    assert "command -v npm" in sh
    assert "command -v node" in sh
    assert "frontend" in sh
    # The "skip with warning" branch must be present — distinguishes
    # this from a hard-fail implementation.
    assert "SKIPPING frontend build" in sh, \
        "install_linux.sh: missing SKIPPING-with-warning branch (must be non-fatal)"


def test_d3_u1_install_windows_has_node_detection():
    """install_windows.bat has the [8/8] frontend build step, non-fatal.

    Also pins: step counters are /8 (not /7), and the new block uses
    `where node` not a hardcoded path. Bat-lint cleanliness is
    enforced by the existing _bat_lint tests; this test just confirms
    the block exists.
    """
    bat = (_REPO / "install_windows.bat").read_text(encoding="utf-8", errors="replace")
    assert "[8/8] Building D3 frontend" in bat, \
        "install_windows.bat: step [8/8] missing"
    # Every earlier step must be /8 now (not /7).
    for n in range(1, 8):
        assert f"[{n}/8]" in bat, \
            f"install_windows.bat: step [{n}/8] header missing (still /7?)"
        assert f"[{n}/7]" not in bat, \
            f"install_windows.bat: stale [{n}/7] header left behind"
    # Detection uses `where node`, not a hardcoded path.
    assert "where node" in bat
    # Non-fatal: a `goto :after_frontend` skip-target must exist.
    assert ":after_frontend" in bat


def test_d3_u1_install_windows_bat_crlf_ascii():
    """The new step must keep CRLF and ASCII (NEVER list constraint)."""
    raw = (_REPO / "install_windows.bat").read_bytes()
    # CRLF: every \n must be preceded by \r.
    bare_lfs = [
        i for i in range(len(raw))
        if raw[i:i+1] == b"\n" and (i == 0 or raw[i-1:i] != b"\r")
    ]
    assert not bare_lfs, \
        f"install_windows.bat: {len(bare_lfs)} bare-LF line(s) found"
    # ASCII: no byte > 0x7F.
    non_ascii = [i for i, b in enumerate(raw) if b > 0x7F]
    assert not non_ascii, \
        f"install_windows.bat: {len(non_ascii)} non-ASCII byte(s) at offsets {non_ascii[:5]}"
