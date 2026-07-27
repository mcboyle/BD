"""capture.sh must supply a display before it runs the live suite.

L2 (headed-browser-launch) opens a VISIBLE Chromium -- headless=False is a
DANGER_MAP invariant, so the check exists to prove the interactive-login path
works on the deployment. Without an X server it WARNs, correctly: no display is
an environment fact, not a code defect.

The gap this closes is a handoff, not a bug in either script.
scripts/provision_test_host.sh already starts Xvfb and exports DISPLAY -- but
that export dies with the provisioner's process. capture.sh runs later, in a
different shell, and had ZERO references to DISPLAY, so L2 warned even on a
correctly provisioned box unless the operator happened to export DISPLAY by
hand. The capability was provisioned and then not handed over.

This is PROVISION, not seeding: it supplies a real X server so a real headed
browser really launches. Nothing about L2's assertion is weakened -- if the
browser cannot start, L2 still fails.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_SH = REPO_ROOT / "capture.sh"
FRAGMENT = REPO_ROOT / "scripts" / "lib" / "system_deps.sh"

_LIVE_LANE = "live_tests.run"


def _capture_source() -> str:
    return CAPTURE_SH.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop whole-line and trailing `#` comments.

    Prose must never satisfy or trip these gates: a comment mentioning DISPLAY
    is documentation, not provisioning. CLAUDE.md 0 counts an over-sensitive
    gate as a soundness bug too, so the stripper also keeps a `#` inside a
    quoted string from truncating a real command.
    """
    out = []
    for line in text.splitlines():
        cleaned, quote = [], None
        for ch in line:
            if quote:
                cleaned.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                cleaned.append(ch)
                continue
            if ch == "#":
                break
            cleaned.append(ch)
        out.append("".join(cleaned))
    return "\n".join(out)


def test_capture_establishes_a_display_before_the_live_suite():
    """The display must be provisioned upstream of the live lane.

    Ordering is the subject: a display established after the live suite has
    already run provisions nothing. Compares CODE positions, so moving the
    block below the lane fails even though both strings still occur.
    """
    code = _strip_comments(_capture_source())

    display_at = code.find("bd_start_display")
    lane_at = code.find(_LIVE_LANE)

    assert display_at != -1, (
        "capture.sh never calls bd_start_display -- L2 will WARN on a "
        "provisioned box because the provisioner's DISPLAY export does not "
        "survive its own process"
    )
    assert lane_at != -1, "capture.sh no longer runs the live lane -- anchor is stale"
    assert display_at < lane_at, (
        f"bd_start_display is called at offset {display_at}, after the live "
        f"lane at {lane_at}; a display established after the checks have run "
        f"provisions nothing"
    )


def test_capture_exports_display_rather_than_only_computing_it():
    """A local variable does not reach the live suite's subprocess.

    bd_start_display echoes the display; only `export DISPLAY=` puts it in the
    environment the live checks inherit. Assigning without exporting would
    satisfy a naive token search while changing nothing.
    """
    code = _strip_comments(_capture_source())
    assert re.search(r"\bexport\s+DISPLAY=", code), (
        "capture.sh computes a display but never exports DISPLAY, so the live "
        "suite's subprocess does not inherit it"
    )


def test_capture_sources_the_shared_fragment_for_the_helper():
    """bd_start_display must come from the single source of truth.

    Re-implementing an Xvfb launch inline would reintroduce the duplicate this
    project already paid for once: three copies of the system-dependency logic
    that drift, with the copy nobody updated being the one the box runs.
    """
    code = _strip_comments(_capture_source())
    assert "scripts/lib/system_deps.sh" in code, (
        "capture.sh must source scripts/lib/system_deps.sh rather than "
        "re-implementing a display launch inline"
    )


def test_capture_does_not_fail_when_no_display_can_be_provided():
    """Absence of a display must stay a WARN, never a hard capture failure.

    A headless box with no Xvfb is a legitimate deployment. L2 already reports
    that honestly, and #31 established that a live WARN is informational. If
    capture.sh aborted when Xvfb was missing, this change would convert an
    honest warning into a broken capture -- strictly worse than the status quo.
    """
    code = _strip_comments(_capture_source())
    window = code[code.find("bd_start_display"):]
    window = window[:window.find(_LIVE_LANE)] if _LIVE_LANE in window else window
    assert not re.search(r"^\s*exit\s+[1-9]", window, re.M), (
        "the display block can exit non-zero; a missing display must degrade "
        "to a warning, not abort the capture"
    )


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_bd_start_display_really_yields_a_usable_display():
    """Behavioural: the helper must produce a display something can connect to.

    A structural test proves capture.sh calls the helper; it cannot prove the
    helper works. This runs it for real on an unused display number and then
    confirms an independent client can open that display, so a helper that
    echoed a value without starting a server would fail here.
    """
    display_num = 71
    lock = Path(f"/tmp/.X{display_num}-lock")
    if lock.exists():
        pytest.skip(f":{display_num} is already in use on this host")

    script = (
        f'set -u; cd "{REPO_ROOT}"; . scripts/lib/system_deps.sh; '
        f'bd_start_display :{display_num}'
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60
    )
    try:
        assert proc.returncode == 0, (
            f"bd_start_display failed: rc={proc.returncode} stderr={proc.stderr}"
        )
        assert proc.stdout.strip() == f":{display_num}", (
            f"expected the display on stdout, got {proc.stdout!r}"
        )
        # Independent confirmation: the socket a client would connect to.
        assert Path(f"/tmp/.X11-unix/X{display_num}").exists(), (
            "bd_start_display returned success but no X socket exists -- it "
            "reported a display nothing is serving"
        )
    finally:
        # Never leave a server behind; a leaked Xvfb would poison later runs.
        pid = ""
        if lock.exists():
            pid = lock.read_text(encoding="utf-8", errors="ignore").strip()
        if pid.isdigit():
            subprocess.run(["kill", pid], capture_output=True)
        for stale in (lock, Path(f"/tmp/.X11-unix/X{display_num}")):
            try:
                stale.unlink()
            except OSError:
                pass


def test_the_fragment_is_the_only_place_that_launches_xvfb():
    """Anti-drift: no consumer may spawn Xvfb behind the helper's back.

    Scans code, not comments, so documenting Xvfb stays free.
    """
    offenders = []
    for path in (CAPTURE_SH, REPO_ROOT / "scripts" / "provision_test_host.sh",
                 REPO_ROOT / "install_linux.sh"):
        if not path.exists():
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        if re.search(r"^\s*(setsid\s+)?Xvfb\s", code, re.M):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} launch Xvfb directly; the launch belongs in "
        f"{FRAGMENT.name} so idempotency and probing live in one place"
    )
