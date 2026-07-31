"""install_linux.sh must download the browsers where the SERVICE user looks,
and must derive its "Installed" claim from disk rather than from an exit status.

WHAT WAS WRONG.  ``install_linux.sh`` writes PER-USER state -- the Playwright
browser registry and, separately, CloakBrowser's -- into whatever ``$HOME`` it
happens to be running under, and then reported success from the download
command's own exit status alone.  ``install_service.sh`` deliberately writes
``User=${SUDO_USER:-$(whoami)}`` into the systemd unit ("yt-dlp + Playwright
running as root is a security smell"), so an elevated install puts the engines
in ``/root/.cache/ms-playwright`` -- mode 0700 on a stock Ubuntu -- while the
account that actually runs the app resolves a different, empty registry.  The
operator was told "Installed: chromium" and "Install complete."  That is a claim
whose denominator (the installing user's HOME) structurally excludes its subject
(the service user's HOME): CLAUDE.md section 0.

HOW THESE TESTS REACH THAT.  They do NOT grep install_linux.sh and they do NOT
need real root.  The elevation branch resolves ``id``, ``whoami``, ``runuser``
and ``sudo`` through PATH -- bash has no ``id``/``whoami`` builtin -- so a probe
that puts fakes for all four on PATH exercises the REAL branching in the REAL
file as any user.  A ``geteuid()``-gated skip was rejected: a skip that reports
green is exactly the gate that cannot see its subject.

The probe is a UNIQUE-ANCHOR-DELIMITED slice of the real ``install_linux.sh``,
spliced and executed -- the same shape as
``tests/test_provision_test_host.py``'s ``_build_install_linux_system_tier_probe``.
Both anchors are asserted unique; a non-unique anchor fails as UNKNOWN and never
passes.

CRY-WOLF FLOORS.  Two tests here exist to stop the fix from over-firing, because
a gate that cries wolf gets switched off and that is a soundness bug in its own
right: ``test_an_unprivileged_install_is_unchanged`` (the no-sudo operator path
this script's header contract protects) and
``test_the_reach_check_does_not_fire_when_only_an_optional_engine_is_absent``
(a webkit download lost to a flaky mirror must never read as "BD cannot
capture").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_LINUX = REPO / "install_linux.sh"
BASH = shutil.which("bash") or "/bin/bash"
REAL_PY = sys.executable

# Slice anchors.  Both are asserted count == 1 before every splice; see
# test_the_probe_slice_anchors_are_unique.
SLICE_START = "# ── Playwright browsers "
SLICE_END = "# ── Vendored DOM-capture assets"

# The closing banner is OUTSIDE the browser slice, so it gets its own two-line
# slice with its own anchors.
BANNER_START = 'echo "  Install complete."'
BANNER_END = 'echo "  Start the app:  ./start_linux.sh"'

# The probe never puts /usr/sbin on PATH, so `command -v runuser` finds only a
# stub this module wrote.  That is what makes the runuser / sudo branch choice a
# property of the test rather than of the host.
_BASE_PATH = "/usr/bin:/bin"


def _read_install_linux() -> str:
    return INSTALL_LINUX.read_text(encoding="utf-8")


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


_FAKE_ID = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$BD_ID_LOG"
if [ "${1:-}" = "-u" ]; then
    printf '%s\\n' "${FAKE_UID:-0}"
    exit 0
fi
exec /usr/bin/id "$@"
"""

_FAKE_WHOAMI = """#!/usr/bin/env bash
printf '%s\\n' "${FAKE_WHOAMI:-root}"
"""

# Mimics util-linux runuser(1) as MEASURED on this host: it resets
# HOME/SHELL/USER/LOGNAME and PRESERVES everything else -- including
# XDG_CACHE_HOME and PLAYWRIGHT_BROWSERS_PATH.
_FAKE_RUNUSER = """#!/usr/bin/env bash
printf 'RUNUSER %s\\n' "$*" >> "$BD_PROBE_LOG"
if [ -n "${FAKE_DEESCALATION_FAILS:-}" ]; then
    echo "runuser: user ${2:-?} does not exist" >&2
    exit 1
fi
_u="$2"
shift 3
export HOME="/home/$_u" USER="$_u" LOGNAME="$_u"
exec "$@"
"""

# Mimics sudo with `Defaults env_reset` as MEASURED on this host (sudo 1.9.x):
# it STRIPS XDG_CACHE_HOME *and* PLAYWRIGHT_BROWSERS_PATH.  That divergence from
# runuser is the whole reason the installer re-states PLAYWRIGHT_BROWSERS_PATH on
# the env(1) command line instead of inheriting it.
_FAKE_SUDO = """#!/usr/bin/env bash
printf 'SUDO %s\\n' "$*" >> "$BD_PROBE_LOG"
if [ -n "${FAKE_DEESCALATION_FAILS:-}" ]; then
    echo "sudo: unknown user ${2:-?}" >&2
    exit 1
fi
_u="$2"
shift 4
unset XDG_CACHE_HOME PLAYWRIGHT_BROWSERS_PATH
export HOME="/home/$_u" USER="$_u" LOGNAME="$_u"
exec "$@"
"""

# The fake interpreter.  It records the environment that actually reached it --
# that recording IS the subject of RED_1..RED_4 -- and answers `--dry-run` the
# way playwright 1.61.0 does, one "Install location:" line per ARTEFACT, with
# the chromium trio (chromium / ffmpeg / headless shell) that a bare
# `--dry-run chromium` really emits.
#
# FIXTURE HAZARD, hit during design and worth stating: a fake that exits 0 for
# EVERY argv also stubs out the Python parser, which makes the reach check
# report `ok` over nothing -- a false GREEN caused by the fixture.  This fake
# therefore execs the REAL interpreter for any `-c` argv unless the test is
# explicitly asking for the silent-success case (BD_PROBE_MODE=silent), which is
# the fixture for exactly that failure.
_FAKE_VPYTHON = """#!/usr/bin/env bash
printf '%s|HOME=%s|XDG=%s|USER=%s|PBP=%s\\n' \\
    "$*" "${HOME:-}" "${XDG_CACHE_HOME:-}" "${USER:-}" \\
    "${PLAYWRIGHT_BROWSERS_PATH:-}" >> "$BD_PROBE_LOG"

if [ "${BD_PROBE_MODE:-}" = "silent" ]; then
    exit 0
fi

_dry=""
for _a in "$@"; do
    if [ "$_a" = "--dry-run" ]; then _dry=1; fi
done

if [ -n "$_dry" ]; then
    _label="Install location:"
    if [ "${BD_PROBE_MODE:-}" = "reworded" ]; then _label="Target directory:"; fi
    for _a in "$@"; do
        case "$_a" in
            chromium)
                printf '  %s    %s\\n' "$_label" "$BD_PROBE_CACHE/chromium-1228"
                printf '  %s    %s\\n' "$_label" "$BD_PROBE_CACHE/ffmpeg-1011"
                printf '  %s    %s\\n' "$_label" \\
                    "$BD_PROBE_CACHE/chromium_headless_shell-1228"
                ;;
            firefox)
                printf '  %s    %s\\n' "$_label" "$BD_PROBE_CACHE/firefox-1532"
                ;;
            webkit)
                printf '  %s    %s\\n' "$_label" "$BD_PROBE_CACHE/webkit-2311"
                ;;
        esac
    done
    exit 0
fi

if [ "${1:-}" = "-c" ]; then
    exec "$BD_REAL_PY" "$@"
fi
exit 0
"""

_ENGINE_FRAGMENT = """bd_playwright_engines() {
    case "$1" in
        core)  printf '%s\\n' "chromium" ;;
        extra) printf '%s\\n' "firefox webkit" ;;
        all)   printf '%s\\n' "chromium firefox webkit" ;;
    esac
}
"""


class ProbeRun:
    """One executed slice of the real install_linux.sh, plus what it recorded."""

    def __init__(
        self,
        completed: subprocess.CompletedProcess[str],
        log: list[str],
        id_log: list[str],
        probe: Path,
    ) -> None:
        self.completed = completed
        self.log = log
        self.id_log = id_log
        self.probe = probe

    @property
    def stdout(self) -> str:
        return self.completed.stdout

    def entry(self, argv: str) -> str:
        """The single VPYTHON log entry whose argv is exactly ``argv``."""
        hits = [line for line in self.log if line.split("|", 1)[0] == argv]
        assert len(hits) == 1, (
            f"expected exactly one VPYTHON invocation with argv {argv!r}, got "
            f"{len(hits)}. Full log:\n" + "\n".join(self.log) + self._context()
        )
        return hits[0]

    def field(self, argv: str, name: str) -> str:
        entry = self.entry(argv)
        for part in entry.split("|")[1:]:
            key, _, value = part.partition("=")
            if key == name:
                return value
        raise AssertionError(f"{name} not present in log entry {entry!r}")

    def _context(self) -> str:
        return (
            f"\n\nrc={self.completed.returncode}\n"
            f"stdout:\n{self.completed.stdout[-4000:]}\n"
            f"stderr:\n{self.completed.stderr[-4000:]}\n"
        )

    def context(self) -> str:
        return self._context()


def _slice_browser_section(source: str) -> str:
    assert source.count(SLICE_START) == 1, (
        f"UNKNOWN: {SLICE_START!r} is not a unique anchor in install_linux.sh "
        f"(count={source.count(SLICE_START)}), so this probe cannot decide "
        "where the browser section begins. Move the anchor rather than "
        "widening it -- a slice taken on a guessed boundary produces evidence "
        "about a file that does not exist."
    )
    assert source.count(SLICE_END) == 1, (
        f"UNKNOWN: {SLICE_END!r} is not a unique anchor in install_linux.sh "
        f"(count={source.count(SLICE_END)}), so this probe cannot decide where "
        "the browser section ends."
    )
    start = source.index(SLICE_START)
    end = source.index(SLICE_END)
    assert start < end, (
        "UNKNOWN: the browser-section anchors are out of order in "
        "install_linux.sh, so the slice would be empty."
    )
    return source[start:end]


def _run_browser_section(
    tmp_path: Path,
    *,
    fake_uid: str = "0",
    sudo_user: str | None = "bdops",
    whoami: str = "root",
    caller_home: str | None = None,
    xdg_cache_home: str | None = None,
    browsers_path: str | None = None,
    tool: str = "runuser",
    de_escalation_fails: bool = False,
    tmpdir_missing: bool = False,
    mode: str = "",
    present_dirs: tuple[str, ...] = (
        "chromium-1228",
        "ffmpeg-1011",
        "chromium_headless_shell-1228",
    ),
) -> ProbeRun:
    work = tmp_path / f"probe-{len(list(tmp_path.iterdir()))}"
    (work / "bin").mkdir(parents=True)
    stub_bin = work / "bin"

    cache = work / "ms-playwright"
    cache.mkdir()
    for name in present_dirs:
        (cache / name).mkdir()

    log = work / "vpython.log"
    id_log = work / "id.log"
    log.touch()
    id_log.touch()

    vpython = stub_bin / "fake-vpython"
    _write_stub(vpython, _FAKE_VPYTHON)
    _write_stub(stub_bin / "id", _FAKE_ID)
    _write_stub(stub_bin / "whoami", _FAKE_WHOAMI)
    if tool == "runuser":
        _write_stub(stub_bin / "runuser", _FAKE_RUNUSER)
    elif tool == "sudo":
        _write_stub(stub_bin / "sudo", _FAKE_SUDO)
    else:  # pragma: no cover - guarded by the assert below
        raise AssertionError(f"unknown de-escalation tool {tool!r}")

    body = _slice_browser_section(_read_install_linux())
    home = caller_home if caller_home is not None else "/root"
    probe = work / "probe.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "set -o pipefail\n"
        f'INSTALL_DIR="{work}"\n'
        f'VPYTHON="{vpython}"\n'
        + _ENGINE_FRAGMENT
        + body,
        encoding="utf-8",
    )
    probe.chmod(0o755)

    parsed = subprocess.run(
        [BASH, "-n", str(probe)], capture_output=True, text=True, timeout=60
    )
    assert parsed.returncode == 0, (
        "UNKNOWN: the generated install_linux.sh browser probe does not parse, "
        "so nothing below could be measured. bash -n said:\n" + parsed.stderr
    )

    env = {
        "PATH": f"{stub_bin}{os.pathsep}{_BASE_PATH}",
        "HOME": home,
        "TMPDIR": str(work / "no-such-tmp") if tmpdir_missing else str(work),
        "BD_PROBE_LOG": str(log),
        "BD_ID_LOG": str(id_log),
        "BD_PROBE_CACHE": str(cache),
        "BD_REAL_PY": REAL_PY,
        "BD_PROBE_MODE": mode,
        "FAKE_UID": fake_uid,
        "FAKE_WHOAMI": whoami,
        "LANG": "C.UTF-8",
    }
    if sudo_user is not None:
        env["SUDO_USER"] = sudo_user
    if xdg_cache_home is not None:
        env["XDG_CACHE_HOME"] = xdg_cache_home
    if browsers_path is not None:
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
    if de_escalation_fails:
        env["FAKE_DEESCALATION_FAILS"] = "1"

    completed = subprocess.run(
        [BASH, str(probe)],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return ProbeRun(
        completed,
        log.read_text(encoding="utf-8").splitlines(),
        id_log.read_text(encoding="utf-8").splitlines(),
        probe,
    )


_CORE = "-m playwright install chromium"
_EXTRA = "-m playwright install firefox webkit"
_CLOAK = "-m cloakbrowser install"
_CLOAK_INFO = "-m cloakbrowser info"


# --------------------------------------------------------------------------
# RED 1-4: the downloads must cross the user boundary, with the right ENV.
# --------------------------------------------------------------------------


def test_the_core_playwright_install_runs_as_the_service_user(
    tmp_path: Path,
) -> None:
    """KILLS: the core (chromium) download landing in the installing user's HOME.

    chromium is the engine BD actually launches -- every capture, login and
    download goes through it -- so this is the arm where the defect costs the
    operator the whole application.
    """
    run = _run_browser_section(tmp_path)
    assert run.field(_CORE, "HOME") == "/home/bdops", (
        "the core Playwright download did not run as the service user: "
        f"{run.entry(_CORE)!r}. Playwright's registry is "
        "$XDG_CACHE_HOME/ms-playwright or ~/.cache/ms-playwright, so a HOME of "
        "/root puts the engines somewhere the systemd User= account cannot "
        "even traverse." + run.context()
    )
    assert run.field(_CORE, "USER") == "bdops", run.entry(_CORE) + run.context()


def test_the_extra_playwright_install_runs_as_the_service_user(
    tmp_path: Path,
) -> None:
    """KILLS: de-escalating only the first of the two `playwright install` sites.

    The two downloads are separate ``if`` blocks with separate messages -- that
    split is deliberate, so an optional webkit failure is not graded like losing
    chromium -- and a fix could plausibly reach only the first.  Live check L4
    stats all three engines from the SERVICE user's registry, so leaving these
    in root's cache leaves a correctly provisioned host WARNing forever.
    """
    run = _run_browser_section(tmp_path)
    assert run.field(_EXTRA, "HOME") == "/home/bdops", (
        f"the optional-engine download stayed root: {run.entry(_EXTRA)!r}"
        + run.context()
    )
    assert run.field(_EXTRA, "USER") == "bdops", run.entry(_EXTRA) + run.context()


def test_the_cloakbrowser_install_runs_as_the_service_user(tmp_path: Path) -> None:
    """KILLS: fixing Playwright's per-user cache and not CloakBrowser's.

    ``cloakbrowser`` resolves its browser directory as ``Path.home() /
    ".cloakbrowser"`` -- identical exposure, same HOME.  One file giving two
    answers to the same question is the drift this cut exists to remove.  The
    ``info`` report is asserted too: a report about root's CloakBrowser printed
    directly under a line that installed to the service user's would be a
    denominator excluding its subject, inside the fix for that defect.
    """
    run = _run_browser_section(tmp_path)
    assert run.field(_CLOAK, "HOME") == "/home/bdops", (
        f"the CloakBrowser pre-download stayed root: {run.entry(_CLOAK)!r}"
        + run.context()
    )
    assert run.field(_CLOAK_INFO, "HOME") == "/home/bdops", (
        "`cloakbrowser info` reported on a different user's state than the "
        f"install it describes: {run.entry(_CLOAK_INFO)!r}" + run.context()
    )


def test_de_escalation_drops_the_installing_users_xdg_cache_home(
    tmp_path: Path,
) -> None:
    """KILLS: switching the user while carrying root's XDG_CACHE_HOME.

    MEASURED on this host: ``XDG_CACHE_HOME=/root/.cache runuser -u ubuntu --
    env`` still shows ``XDG_CACHE_HOME=/root/.cache`` -- runuser resets only
    HOME/SHELL/USER/LOGNAME.  Playwright prefers XDG_CACHE_HOME over
    ``os.homedir()``, so a user switch that inherits it sends the engines to
    root's cache ANYWAY: a fix that appears to work and does not.  HOME alone
    cannot see this, which is why this assertion is about XDG specifically.
    """
    run = _run_browser_section(tmp_path, xdg_cache_home="/root/.cache")
    assert run.field(_CORE, "XDG") == "", (
        "the installing user's XDG_CACHE_HOME survived the switch to the "
        f"service user, so Playwright still resolves it: {run.entry(_CORE)!r}"
        + run.context()
    )
    assert run.field(_CORE, "HOME") == "/home/bdops", (
        run.entry(_CORE) + run.context()
    )


# --------------------------------------------------------------------------
# RED 5-8: the "Installed" claim must be derived from disk, in the service
# user's own namespace, and must not over-fire.
# --------------------------------------------------------------------------


def test_the_installer_derives_the_browser_verdict_from_disk(
    tmp_path: Path,
) -> None:
    """KILLS: reporting "Installed: chromium" from the download's exit status.

    The fixture answers ``--dry-run`` with three real-shaped install locations
    that do not exist on disk.  Pristine never invokes ``--dry-run`` at all and
    prints "Installed: chromium" regardless.
    """
    run = _run_browser_section(tmp_path, present_dirs=())
    assert "is NOT on disk" in run.stdout, (
        "the installer did not check whether the engines are on disk where the "
        "service user's Playwright looks for them; it reported success from an "
        "exit status." + run.context()
    )
    assert any("--dry-run" in line for line in run.log), (
        "no --dry-run probe was issued at all" + run.context()
    )


def test_the_reach_check_covers_the_headless_shell_not_just_chromium(
    tmp_path: Path,
) -> None:
    """KILLS: the obvious wrong implementation -- stat'ing ``executable_path``.

    MEASURED against playwright 1.61.0 on an empty HOME:
    ``p.chromium.executable_path`` -> ``.../chromium-1228/chrome-linux64/chrome``
    while ``launch(headless=True)`` fails naming
    ``.../chromium_headless_shell-1228/chrome-headless-shell-linux64/...``.
    DIFFERENT DIRECTORIES.  A check that stats executable_path reports the
    browser PRESENT on a tree where headless launch cannot start -- the fourth
    instance CLAUDE.md section 0 lists.  Live check L4 stats exactly that path,
    which is why L4 is not a substitute for this step.

    The fixture therefore has chromium-1228 and ffmpeg-1011 present and ONLY the
    headless shell missing, which is invisible to any narrower denominator.
    """
    run = _run_browser_section(
        tmp_path, present_dirs=("chromium-1228", "ffmpeg-1011")
    )
    assert "is NOT on disk" in run.stdout, (
        "a missing headless shell was not reported: the reach check's "
        "denominator does not contain the artefact headless launch needs."
        + run.context()
    )
    assert "chromium_headless_shell-1228" in run.stdout, (
        "the verdict did not NAME the missing headless shell, so the operator "
        "cannot act on it." + run.context()
    )


def test_an_unparseable_dry_run_reports_unknown_not_ok(tmp_path: Path) -> None:
    """KILLS: treating "no locations parsed" as a pass.

    ``Install location:`` is CLI output and an upgrade could reword it; the
    dry-run file is also written by the caller and read by the service user, so
    a restrictive umask can make it unreadable.  Either way the check has not
    reached its subject.  UNKNOWN is a third state and it fails.
    """
    run = _run_browser_section(tmp_path, mode="reworded")
    assert "UNKNOWN" in run.stdout, (
        "a dry-run whose output could not be parsed was not reported as "
        "UNKNOWN" + run.context()
    )
    assert "Verified:" not in run.stdout, (
        "the installer certified an install it never examined -- OK over an "
        "empty denominator." + run.context()
    )


def test_the_reach_check_does_not_fire_when_only_an_optional_engine_is_absent(
    tmp_path: Path,
) -> None:
    """CRY-WOLF FLOOR.  A missing webkit must not read as "BD cannot capture".

    ``$_pw_extra`` is graded optional by the script itself, precisely so a
    download lost to a flaky mirror never costs the operator chromium.  A reach
    check over ``all`` would print "BD cannot capture, log in or download" on a
    host that is completely fine, and a gate that cries wolf gets switched off.
    """
    run = _run_browser_section(tmp_path)
    assert "Verified:" in run.stdout, (
        "a host with the complete chromium trio was not reported reachable"
        + run.context()
    )
    assert "is NOT on disk" not in run.stdout, (
        "the reach check fired on a healthy host because it verified the "
        "OPTIONAL engines too." + run.context()
    )
    verdict = run.stdout[run.stdout.index("Verified:") :]
    assert "firefox" not in verdict and "webkit" not in verdict, (
        "the reachability verdict named an optional engine, so an optional "
        f"download failure would be graded like losing chromium: {verdict!r}"
        + run.context()
    )


# --------------------------------------------------------------------------
# RED 9-10: PLAYWRIGHT_BROWSERS_PATH must survive BOTH de-escalation branches.
# --------------------------------------------------------------------------


def test_a_machine_wide_browser_pool_survives_the_sudo_branch(
    tmp_path: Path,
) -> None:
    """KILLS: forwarding PLAYWRIGHT_BROWSERS_PATH in only one branch.

    MEASURED on this host: ``runuser -u X -- env`` KEEPS
    PLAYWRIGHT_BROWSERS_PATH, while ``sudo -u X -H -- env`` STRIPS it
    (``Defaults env_reset``).  So on a host with a machine-wide pool and no
    runuser on root's PATH, a fix that merely switches user installs into
    ``~/.cache/ms-playwright`` while the deployment resolves the pool -- the
    IDENTICAL wrong-cache defect, newly created.  Worse, the reach check crosses
    the same boundary, so it would resolve the same wrong registry and report
    "ok": a verification whose denominator excludes its subject, inside the fix
    for exactly that.  BD's own scripts/cloud-setup.sh provisions this shape.
    """
    run = _run_browser_section(
        tmp_path, tool="sudo", browsers_path="/opt/pw-browsers"
    )
    assert any(line.startswith("SUDO ") for line in run.log), (
        "the sudo branch was never taken, so this test measured nothing about "
        "it. Log:\n" + "\n".join(run.log) + run.context()
    )
    assert run.field(_CORE, "PBP") == "/opt/pw-browsers", (
        "the machine-wide browser pool was stripped on the way to the service "
        f"user: {run.entry(_CORE)!r}. The engines would be downloaded into a "
        "per-user cache the deployment does not resolve." + run.context()
    )


def test_a_machine_wide_browser_pool_survives_the_runuser_branch(
    tmp_path: Path,
) -> None:
    """KILLS: dropping PLAYWRIGHT_BROWSERS_PATH outright while de-escalating.

    Same subject as the sudo arm, other branch.  On pristine there is no
    de-escalation at all, so the assertion that a RUNUSER line exists is what
    stops this from passing vacuously on the unfixed file.
    """
    run = _run_browser_section(tmp_path, browsers_path="/opt/pw-browsers")
    assert any(line.startswith("RUNUSER ") for line in run.log), (
        "the runuser branch was never taken. Log:\n"
        + "\n".join(run.log)
        + run.context()
    )
    assert run.field(_CORE, "PBP") == "/opt/pw-browsers", (
        f"the pool did not reach the download: {run.entry(_CORE)!r}"
        + run.context()
    )


# --------------------------------------------------------------------------
# RED 11-12: the verdict is graded on CONTENT, never on a bare exit status.
# --------------------------------------------------------------------------


def test_a_failed_de_escalation_reports_unknown_not_missing(
    tmp_path: Path,
) -> None:
    """KILLS: grading the reach verdict from ``$?`` alone.

    MEASURED: ``runuser -u <nonexistent>`` and ``sudo -u <nonexistent>`` BOTH
    exit 1 -- the same status the location parser uses for "missing".  Reading
    the status alone therefore turns "de-escalation never reached the subject"
    into a DEFINITE "chromium is NOT on disk", printed over an empty list.  That
    is an UNKNOWN reported as a verdict, with zero evidence behind it.
    """
    run = _run_browser_section(tmp_path, de_escalation_fails=True)
    assert "UNKNOWN" in run.stdout, (
        "a de-escalation that never reached the service user was not reported "
        "as UNKNOWN" + run.context()
    )
    assert "is NOT on disk" not in run.stdout, (
        "the installer asserted a definite MISSING verdict over an empty "
        "list, because it graded the exit status rather than the output."
        + run.context()
    )


def test_an_unreadable_dry_run_file_reports_unknown_not_ok(tmp_path: Path) -> None:
    """KILLS: treating "the dry-run output could not be READ" as a pass.

    The dry-run transcript is written by the caller and read back across the
    user boundary, so a restrictive umask -- or, as here, a TMPDIR that does not
    exist -- leaves the parser with nothing to read.  That is the check failing
    to reach its subject, which is UNKNOWN, not OK.  This is a different arm of
    the parser from the "parsed zero locations" case above and needs its own
    guard.
    """
    run = _run_browser_section(tmp_path, tmpdir_missing=True)
    assert "UNKNOWN" in run.stdout, (
        "a dry-run transcript that could not be read was not reported as "
        "UNKNOWN" + run.context()
    )
    assert "Verified:" not in run.stdout, (
        "the installer certified an install whose transcript it never read."
        + run.context()
    )


def test_a_silent_success_reports_unknown_not_verified(tmp_path: Path) -> None:
    """KILLS: dropping the non-empty-output requirement from the OK arm.

    An interpreter that exits 0 printing nothing must not produce
    "Verified: ... resolves chromium on disk at" followed by nothing.  OK over
    an empty denominator is the failure this whole cut is about.
    """
    run = _run_browser_section(tmp_path, mode="silent")
    assert "UNKNOWN" in run.stdout, (
        "an empty, zero-exit reach probe was not reported as UNKNOWN"
        + run.context()
    )
    assert "Verified:" not in run.stdout, (
        "the installer printed a reachability verdict over an empty location "
        "list." + run.context()
    )


# --------------------------------------------------------------------------
# RED 13: the closing banner must repeat the browser verdict, not restate it.
# --------------------------------------------------------------------------


def _run_banner(tmp_path: Path, *, reach: str) -> subprocess.CompletedProcess[str]:
    source = _read_install_linux()
    assert source.count(BANNER_START) == 1, (
        f"UNKNOWN: {BANNER_START!r} is not unique in install_linux.sh, so the "
        "banner slice cannot be taken."
    )
    assert source.count(BANNER_END) == 1, (
        f"UNKNOWN: {BANNER_END!r} is not unique in install_linux.sh, so the "
        "banner slice cannot be taken."
    )
    start = source.index(BANNER_START)
    end = source.index(BANNER_END) + len(BANNER_END)
    assert start < end, "UNKNOWN: the banner anchors are out of order."

    work = tmp_path / f"banner-{reach}"
    work.mkdir()
    probe = work / "banner.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        '_bd_run_user="bdops"\n'
        f'_pw_reach="{reach}"\n' + source[start:end] + "\n",
        encoding="utf-8",
    )
    parsed = subprocess.run(
        [BASH, "-n", str(probe)], capture_output=True, text=True, timeout=60
    )
    assert parsed.returncode == 0, (
        "UNKNOWN: the generated banner probe does not parse:\n" + parsed.stderr
    )
    return subprocess.run(
        [BASH, str(probe)], capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize(
    ("reach", "expected"),
    (
        pytest.param("ok", "reachable by bdops", id="ok"),
        pytest.param("missing", "NOT reachable by bdops", id="missing"),
        pytest.param("unknown", "UNVERIFIED", id="unknown"),
    ),
)
def test_the_closing_banner_repeats_the_browser_verdict(
    tmp_path: Path, reach: str, expected: str
) -> None:
    """KILLS: an "Install complete." banner that survives a browserless install.

    The banner is the last thing the operator reads and it used to be
    unconditional, so it stayed intact on a host where no browser had been
    installed at all.
    """
    completed = _run_banner(tmp_path, reach=reach)
    assert completed.returncode == 0, completed.stderr
    assert expected in completed.stdout, (
        f"the closing banner did not carry the {reach!r} browser verdict; it "
        f"printed:\n{completed.stdout}\n{completed.stderr}"
    )


def test_the_unknown_banner_does_not_point_at_a_message_that_was_not_printed(
    tmp_path: Path,
) -> None:
    """CRY-WOLF FLOOR (wording).  ``_pw_reach`` is "unknown" on TWO paths.

    One prints an UNKNOWN block; the other -- ``bd_playwright_engines``
    unavailable, reachable via the documented ``BD_SKIP_SYSTEM_DEPS=1`` opt-out
    -- installs nothing and prints no UNKNOWN at all.  A banner that says "see
    the UNKNOWN above" on that path sends the operator looking for a message
    that does not exist.
    """
    completed = _run_banner(tmp_path, reach="unknown")
    assert "the UNKNOWN above" not in completed.stdout, (
        "the banner pointed the operator at an UNKNOWN block that is not "
        "printed on the bd_playwright_engines-unavailable path: "
        f"{completed.stdout!r}"
    )


# --------------------------------------------------------------------------
# Cry-wolf floor and anti-vacuity guards.
# --------------------------------------------------------------------------


def test_an_unprivileged_install_is_unchanged(tmp_path: Path) -> None:
    """CRY-WOLF FLOOR.  This script's headline contract is the no-sudo operator.

    install_linux.sh's own header says it "must stay runnable by an ordinary
    unprivileged user with no sudo rights".  A de-escalation helper that fires
    when not elevated would route that operator's install through runuser (which
    they cannot execute) or sudo (which may prompt or be absent), and would drop
    their OWN XDG_CACHE_HOME out from under them.

    This is the one test here that passes on the pristine file: it proves the
    fix does not regress the path that was never broken.
    """
    run = _run_browser_section(
        tmp_path,
        fake_uid="1000",
        sudo_user=None,
        whoami="matt",
        caller_home="/home/matt",
        xdg_cache_home="/home/matt/.cache",
    )
    assert not [line for line in run.log if line.startswith(("RUNUSER ", "SUDO "))], (
        "an unprivileged install attempted to de-escalate. Log:\n"
        + "\n".join(run.log)
        + run.context()
    )
    assert run.field(_CORE, "HOME") == "/home/matt", (
        run.entry(_CORE) + run.context()
    )
    assert run.field(_CORE, "XDG") == "/home/matt/.cache", (
        "the unprivileged operator's own XDG_CACHE_HOME was dropped out from "
        f"under them: {run.entry(_CORE)!r}" + run.context()
    )


def test_the_probe_harness_reaches_its_subject(tmp_path: Path) -> None:
    """ANTI-VACUITY, on both the pristine and the fixed file.

    Without this, a PATH that failed to take -- or a slice that ran nothing --
    would make every assertion in this module pass over an empty log while
    reporting green.
    """
    run = _run_browser_section(tmp_path)
    parsed = subprocess.run(
        [BASH, "-n", str(run.probe)], capture_output=True, text=True, timeout=60
    )
    assert parsed.returncode == 0, parsed.stderr
    installs = [line for line in run.log if line.startswith("-m playwright install")]
    assert installs, (
        "the spliced browser section issued no `playwright install` at all, so "
        "this module measured nothing." + run.context()
    )


def test_the_installer_consults_the_effective_uid_before_de_escalating(
    tmp_path: Path,
) -> None:
    """KILLS: a helper that de-escalates without asking whether it is elevated.

    Separated from the anti-vacuity guard above deliberately: the pristine
    browser section invokes ``id`` nowhere at all (its only ``sudo`` is inside
    an ``echo``), so this assertion is RED on pristine and belongs with the REDs
    rather than with the regression guards.
    """
    run = _run_browser_section(tmp_path)
    assert any(entry.strip() == "-u" for entry in run.id_log), (
        "nothing in the browser section asked for the effective uid, so the "
        "no-op-when-unprivileged contract is not enforced by the code that "
        f"claims it. id log: {run.id_log!r}" + run.context()
    )


def test_the_probe_slice_anchors_are_unique() -> None:
    """The slice is only evidence if its boundaries are."""
    source = _read_install_linux()
    for anchor in (SLICE_START, SLICE_END, BANNER_START, BANNER_END):
        assert source.count(anchor) == 1, (
            f"UNKNOWN: {anchor!r} occurs {source.count(anchor)} times in "
            "install_linux.sh. Every probe in this module slices on it, so a "
            "non-unique anchor makes all of them evidence about a location "
            "nobody chose."
        )
