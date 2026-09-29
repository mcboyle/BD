"""IA-03: install_linux.sh must not tell a fresh install its web UI still works.

Since the Phase 1 root flip the SPA *is* / (and /m, /m2 are 302 shims to it),
so a missing frontend/dist makes / answer 503 X-BD-M2-Status: not-built.
spare8's fresh install printed "The existing UIs at / and /m are unaffected"
while every UI URL was a 503. The claim is checked here against the app itself.
"""
from __future__ import annotations

import re
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_START = "# D3 U1: Frontend SPA build"
_END = "# ── GUI-parity inventory regen"


def _frontend_step() -> str:
    text = (_REPO / "install_linux.sh").read_text(encoding="utf-8")
    assert text.count(_START) == 1 and text.count(_END) == 1, (
        "UNKNOWN: install_linux.sh's frontend-step anchors are not unique")
    return text[text.index(_START):text.index(_END)]


def test_the_app_serves_503_at_root_and_redirects_m_there_without_dist(monkeypatch, tmp_path):
    from bulk_downloader import app as a
    monkeypatch.setattr(a, "_M2_DIST_ROOT", tmp_path / "absent" / "frontend" / "dist")
    client = a.app.test_client()
    root = client.get("/")
    assert root.status_code == 503
    assert root.headers.get("X-BD-M2-Status") == "not-built"
    for shim in ("/m", "/m2"):
        r = client.get(shim)
        assert r.status_code == 302 and r.headers["Location"].rstrip("/") in ("", "http://localhost"), (
            shim, r.status_code, r.headers.get("Location"))


def test_the_installer_never_claims_root_or_m_survive_a_missing_build():
    step = _frontend_step()
    claims = re.findall(r".*(?:/ and /m|/m and /) are unaffected.*", step)
    assert not claims, (
        "install_linux.sh says / and /m are unaffected by a missing SPA build, "
        f"but / is the SPA and serves 503: {claims}")


def test_every_no_build_branch_names_root_as_the_503():
    step = _frontend_step()
    names_root = re.findall(r'echo "  The web UI at / \(/m and /m2 redirect there\)', step)
    # Node below the floor, npm ci failed, build failed, other exit, Node absent.
    assert len(names_root) >= 5, (
        f"{len(names_root)} of the 5 no-build branches tell the operator that / is down")
