"""L33 calls a process count an orphan count, so a healthy download reads as a leak.

THE DEFECT. `bulk_downloader/perf_lab.py:58` is the whole measurement:

    out = {"chromium": 0, "ffmpeg": 0, "total_procs": 0}
    for entry in os.listdir("/proc"):
        ...
        comm = open(f"/proc/{entry}/comm").read().strip().lower()
        if "chrom" in comm or "headless" in comm:
            out["chromium"] += 1

That is a substring match on `comm` over EVERY process on the box. It reads no
PPid, so nothing establishes that a process is orphaned; it is scoped to no user
and no process tree, so nothing establishes that BD launched it. The word
"orphan" is nevertheless asserted four layers above it -- in perf_lab's own
docstring ("a proxy for leaked Playwright contexts"), in
`dev_suite/perf_metrics.py:26` leak_scan's docstring ("orphan Chromium/ffmpeg
processes"), in leak_scan's finding text at `:37` ("possible leaked Playwright
contexts", fired at `chromium > 8`), and in L33's registered name
`no-leaked-chromium` plus all three of its return strings.

A live Chromium is a process tree, not a process. MEASURED HERE, in this
container, with one real headless browser open:

    IDLE                            0 processes match the predicate
    ONE browser open, one blank page 6 match  (comm 'chrome-headless' x6)
    all 6 are descendants of the launching python pid

Six for one blank page. The deploy host measured idle 0 and PEAK 22 during a
real download, which is a browser plus its renderer/GPU/zygote children doing
exactly what they are supposed to do. leak_scan's threshold is 8, so a healthy
fetch produces `22 Chromium processes alive -- possible leaked Playwright
contexts`, and L33 returns

    WARN  orphan Chromium count rose 0->22 over N samples --
          Playwright contexts may be leaking

about a working download. CLAUDE.md section 0's inverse: over-sensitivity is a
soundness bug, because a gate that cries wolf gets switched off, and then the
thing it guarded is unguarded.

TWO FURTHER THINGS THE MEASUREMENT SETTLED, both of which changed this cut.

1. `comm` IS TRUNCATED, AND WHICH CLAUSE MATCHES DEPENDS ON THE BACKEND. An
   earlier version of this paragraph said `"headless" in comm` is the clause
   that matches and `"chrom"` is not. That is REVERSED on the backend BD
   actually resolves. Measured:

     cloakbrowser (the DEFAULT whenever importable)  comm 'chrome'          only "chrom"
       its crash handler                             comm 'chrome_crashpad' only "chrom"
     playwright headless-shell, 1228 pool            comm 'chrome-headless' both
     playwright, 1194 pool                           comm 'headless_shell'  only "headless"

   comm truncates at 15 (TASK_COMM_LEN 16 including the NUL), so 'chrome-headless'
   and 'chrome_crashpad' are both cut short while 'chrome' is not. comm and
   cmdline are therefore different instruments, and this file says which one each
   predicate reads. Asserting one backend's answer generally is the same
   headless-shell-versus-chromium confusion CLAUDE.md section 0 records a capture
   check making -- committed here twice, once in the code and once in this
   explanation of it.

2. A CORRECT CLOSE LEAVES ZOMBIES, AND THE OBVIOUS FIX WOULD FLAG THEM.
   Immediately after `browser.close()` on a browser that was shut down properly:

       CLOSED + 0.0s   2 match; ppid=1; state='Z (zombie)'; NOT descendants
       CLOSED + 0.25s  2 match; same
       CLOSED + 1.0s   2 match; same
       CLOSED + 3.0s   0 match

   Reparented to init and awaiting reap. So "a browser process that is not a
   descendant of the running app" -- the natural definition of orphaned, and the
   one this cut was originally scoped around -- fires TWICE ON EVERY HEALTHY
   BROWSER CLOSE. Shipping that would have replaced a gate that cries wolf with
   a different gate that cries wolf, which is the same defect wearing the fix's
   clothes. A zombie holds no browser, no port, no profile lock and no memory
   beyond its process-table entry; it is not a leak. `State: Z` in
   /proc/<pid>/status excludes it exactly and cheaply.

3. THE CRASH HANDLER IS ALIVE AT ppid=1 BY DESIGN, AND THE FIRST VERSION OF THIS
   CUT CALLED IT A LEAK. `chrome_crashpad_handler` reparents itself to init and
   stays in state S for the monitored browser's whole lifetime -- it has to, in
   order to outlive and report a browser crash. So it satisfies EVERY clause
   above: browser-ish comm, not a zombie, not a descendant, parent gone.

   REPRODUCED TWICE on the backend BD actually resolves (cloakbrowser 0.5.2, full
   `chrome` binary):

       BASELINE                 chromium=0                 ORPHAN=0
       DURING A HEALTHY LAUNCH  chromium=8  live=6         ORPHAN=2
           pid=18161 comm='chrome_crashpad' ppid=1 state=S
           pid=18165 comm='chrome_crashpad' ppid=1 state=S
       (0 -> 2 -> 0 across the launch, so they belong to it)

   This shipped. It is the defect this whole file exists to remove, reintroduced
   by the removal, and it was missed for a specific and instructive reason: the
   measurement was taken on the playwright/headless-shell backend, where crashpad
   appears only as a short-lived ZOMBIE and the state check excluded it
   correctly. cloakbrowser is the default whenever it is importable. And the gate
   could not see it: `_BROWSER_COMM` was 'chrome-headless' and the file contained
   no occurrence of 'crashpad' at all, so the fixture's denominator structurally
   excluded the subject -- section 0, inside the verification of a section 0 fix.

THE PREDICATE, therefore, and only this:

    orphan  =  comm matches a browser
           AND it is not the crash handler   (alive at ppid=1 by design)
           AND state is not Z                (not awaiting reap)
           AND not a descendant of the running app
           AND its parent is gone            (ppid 1, or a pid no longer present)

The last clause exists because "not a descendant" is not the same as "orphaned":
tools/capture_session.py and tools/nav_probe.py are standalone operator CLIs
whose browsers have a live owner. Those land in `chromium_foreign`. The five
buckets -- live, zombie, crashpad, foreign, orphan -- partition the raw count, so
nothing can fall out of the denominator unnoticed.

The descendant walk is what does the real work: a live download's children are
descendants by construction, so they are excluded structurally rather than by a
threshold that has to be guessed. Browsers left behind by a PREVIOUS app
instance -- a service restart that did not clean up -- are not descendants of
the current pid and are correctly counted, which is a leak class the old number
could not distinguish from a busy download.

WHAT THIS DELIBERATELY DOES NOT DO: it does not require the process to be one BD
launched. That was the original design and the measurement refuted it. BD
launches browsers BOTH ways -- `launch_persistent_context` with a BD-owned
profile (login_impl/manual.py:249, login_impl/replay.py:251) AND plain
`launch()` (cloak.py:486), whose profile is an ephemeral
`/tmp/playwright_chromiumdev_profile-XXXXXX`. How far the flag propagates is
also backend-dependent: measured 1 of 17 processes on playwright/headless-shell
(top-level only, renderers carry `--type=renderer` instead) but 8 of 8 on the
default cloakbrowser backend. So requiring a BD-owned profile path would MISS
every orphan from the plain-launch path -- a false negative, and a leak detector that stops crying
wolf by going blind is strictly worse than the noisy one it replaces. The
profile path is therefore reported as DETAIL to help the operator identify what
leaked, and is never a filter.

The cost of that choice, stated rather than assumed away: on a host running some
other headless Chromium, that browser would be counted. The deploy target is a
headless single-purpose box, so this is accepted; it is written down here so the
next reader does not have to rediscover it.

AND UNKNOWN IS NOT ZERO. `_child_process_count` returns `{}` when /proc is
unavailable, and L33 currently converts that into a PASS: at
`live_tests/checks.py:2415` an empty `processes` dict plus the endpoint's own
"no leak signals" verdict returns 0 orphans. That was added so L33 would stop
WARNing forever on Windows, which is a real problem, but the cure asserts a
measurement that was never taken. Not measurable is NA -- not exercisable here
-- which is its own verdict and does not gate the deploy, rather than a PASS
that certifies the absence of something nobody looked for.

RED-first: R1 through R6 fail on pristine source.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import perf_lab  # noqa: E402
from live_tests import checks, harness  # noqa: E402

# The real value measured in this container, not a plausible-looking one. 15
# chars, because that is where Linux truncates comm.
_BROWSER_COMM = "chrome-headless"


# ── a fake /proc, because the real one cannot be posed ───────────────────────

def _fake_proc(tmp_path, procs) -> Path:
    """Build a directory that looks enough like /proc to drive the real walk.

    procs = [(pid, comm, ppid, state, cmdline)]

    A real directory tree, not a monkeypatched os.listdir: the code under test
    does `os.listdir`, `open(comm)`, and (after this cut) `open(status)`, so
    giving it real files exercises the real I/O and the real parsing, including
    the truncation and the whitespace. What it CANNOT prove is anything about
    how the kernel populates those files -- so the values here are transcribed
    from the live measurement in this file's docstring rather than invented.
    """
    root = tmp_path / "proc"
    root.mkdir()
    for pid, comm, ppid, state, cmdline in procs:
        d = root / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n", encoding="utf-8")
        (d / "status").write_text(
            f"Name:\t{comm}\nState:\t{state}\nTgid:\t{pid}\nPid:\t{pid}\n"
            f"PPid:\t{ppid}\nUid:\t1000\t1000\t1000\t1000\n",
            encoding="utf-8")
        (d / "cmdline").write_bytes(
            b"\0".join(a.encode() for a in cmdline.split(" ") if a) + b"\0")
    # /proc also contains non-numeric entries; the walk must skip them.
    (root / "self").mkdir()
    (root / "meminfo").write_text("MemTotal: 1 kB\n", encoding="utf-8")
    return root


_APP_PID = 500
_DRIVER_PID = 600          # playwright's node driver: BD -> driver -> browser
_BROWSER_PID = 700


def _live_browser_tree(children=20):
    """One live browser under the running app, with its normal child fan-out.

    Shaped from the live measurement: the browser is NOT a direct child of the
    app (Playwright interposes its node driver), which is why the predicate has
    to walk the PPid chain rather than compare a single PPid.
    """
    rows = [
        (_APP_PID, "python3", 1, "S", "venv/bin/python -m bulk_downloader"),
        (_DRIVER_PID, "node", _APP_PID, "S", "node playwright/cli.js run-driver"),
        (_BROWSER_PID, _BROWSER_COMM, _DRIVER_PID, "S",
         "/opt/pw-browsers/chromium/chrome-headless-shell --headless "
         "--user-data-dir=/tmp/playwright_chromiumdev_profile-Yh7g7y"),
    ]
    for i in range(children):
        rows.append((_BROWSER_PID + 1 + i, _BROWSER_COMM, _BROWSER_PID, "S",
                     "/opt/pw-browsers/chromium/chrome-headless-shell "
                     "--type=renderer"))
    return rows


def _zombies(n=2):
    """What a CORRECT close leaves for up to ~3s. ppid=1, state Z."""
    return [(900 + i, _BROWSER_COMM, 1, "Z (zombie)", "")
            for i in range(n)]


_CRASHPAD_PID = 820

# MEASURED on BD's DEFAULT backend, which is what this file's first version got
# wrong. `chrome_crashpad_handler` is 23 characters, so comm truncates to 15.
_CRASHPAD_COMM = "chrome_crashpad"


def _crashpad_handlers(n=2, parent=_BROWSER_PID):
    """The crash handler a healthy browser launch forks, and it is NOT a leak.

    THE FALSE POSITIVE THIS FILE SHIPPED. `chrome_crashpad_handler` deliberately
    reparents itself to init and stays ALIVE (state S, ppid 1) for the browser's
    entire lifetime -- that is its whole purpose: it must outlive a browser crash
    in order to report it. So it is a browser-comm process, not a zombie, and not
    a descendant of the app: it satisfies every clause of the orphan predicate
    while being the opposite of a leak.

    REPRODUCED, twice, on the backend BD actually resolves (cloakbrowser 0.5.2,
    full `chrome` binary):

        BASELINE                  chromium=0                  ORPHAN=0
        DURING A HEALTHY LAUNCH   chromium=8  live=6          ORPHAN=2
            pid=18161 comm='chrome_crashpad' ppid=1 udd=None
            pid=18165 comm='chrome_crashpad' ppid=1 udd=None

    Baseline 0 -> 2 -> 0 on teardown proves they belong to that launch.

    WHY THE FIRST VERSION MISSED IT: it was measured against the
    playwright/headless-shell backend, where crashpad appears only as a
    short-lived ZOMBIE and the state check excluded it correctly. cloakbrowser is
    the default whenever it is importable, and there the handlers persist. The
    gate could not see it either -- _BROWSER_COMM was 'chrome-headless' and the
    file contained no occurrence of 'crashpad' at all, so the fixture's
    denominator structurally excluded the subject. Section 0, inside the
    verification of a section 0 fix.
    """
    return [(_CRASHPAD_PID + i, _CRASHPAD_COMM, 1, "S",
             "/root/.cloakbrowser/chromium-146.0.7680.177.5/"
             "chrome_crashpad_handler --monitor-self "
             "--monitor-self-annotation=ptype=crashpad-handler")
            for i in range(n)]


_CLI_PID = 850


def _foreign_cli_browser():
    """A browser launched by a standalone operator CLI, whose launcher is ALIVE.

    tools/capture_session.py and tools/nav_probe.py both do this. Not a
    descendant of the app, and not orphaned either.
    """
    return [
        (_CLI_PID, "python3", 1, "S", "venv/bin/python tools/capture_session.py"),
        (_CLI_PID + 1, _BROWSER_COMM, _CLI_PID, "S",
         "/opt/pw-browsers/chromium/chrome-headless-shell --headless"),
    ]


def _true_orphans(n=1):
    """A browser whose launcher is gone but which is still RUNNING -- the only
    thing that is actually a leak."""
    return [(950 + i, _BROWSER_COMM, 1, "S",
             "/opt/pw-browsers/chromium/chrome-headless-shell --headless "
             "--user-data-dir=/home/mboyle/BulkDownloader/profiles/wowgirls")
            for i in range(n)]


_APP_ROW = (_APP_PID, "python3", 1, "S", "venv/bin/python -m bulk_downloader")


def _count(tmp_path, procs):
    """The app's own process is always present in a real /proc.

    Two fixtures below originally posed only orphans, with no app row -- and the
    implementation correctly answered UNKNOWN, because descent cannot be
    established from a pid that is not in the table. That is a deliberate guard
    (without it, a /proc from another namespace would make every live browser
    read as an orphan), and it is pinned by
    test_an_invisible_app_pid_reports_unknown below. So the app row is added
    here rather than the guard being weakened to accommodate a fixture that
    could not occur.
    """
    if not any(p[0] == _APP_PID for p in procs):
        procs = [_APP_ROW] + list(procs)
    return perf_lab._child_process_count(
        proc_root=str(_fake_proc(tmp_path, procs)), self_pid=_APP_PID)


# ── canaries ─────────────────────────────────────────────────────────────────

def test_the_fixture_actually_matches_the_current_predicate(tmp_path):
    """CANARY, and it runs FIRST for a reason.

    Every assertion below is about how many processes are classified. If the
    fake table did not match the predicate at all, every count would be 0 and
    the whole file would pass while measuring nothing. So assert the OLD,
    unambiguous key sees the whole population before trusting any refinement of
    it.
    """
    rows = _live_browser_tree() + _zombies() + _true_orphans()
    got = _count(tmp_path, rows)
    assert got.get("chromium") == 21 + 2 + 1, (
        f"the fixture's browser processes are not being matched at all: "
        f"{got!r}. 21 live (1 browser + 20 renderers) + 2 zombies + 1 orphan = "
        f"24 comm matches. If this is 0 the fake /proc is not shaped like the "
        f"real one and nothing else in this file means anything.")


def test_the_walk_skips_non_numeric_entries(tmp_path):
    """/proc holds meminfo, self, sys ... The walk must not try to read them as
    pids, and must not count them."""
    got = _count(tmp_path, _live_browser_tree(children=0))
    assert got.get("total_procs") == 3, (
        f"total_procs={got.get('total_procs')}; expected the 3 numeric entries "
        f"only (app, driver, browser). The fake /proc also contains 'self' and "
        f"'meminfo'.")


# ── R1: a live download is not a leak ────────────────────────────────────────

def test_a_live_browser_and_its_children_are_not_orphans(tmp_path):
    """R1 -- THE DEFECT, in the exact shape the deploy host measured.

    21 processes: one browser plus 20 renderers, all descendants of the running
    app. Zero of them is orphaned. Today `chromium` reports 21, leak_scan fires
    its `> 8` finding, and L33 says "Playwright contexts may be leaking" about a
    working download.
    """
    got = _count(tmp_path, _live_browser_tree(children=20))
    assert got.get("chromium_orphan") == 0, (
        f"a live browser with 20 renderer children reported "
        f"{got.get('chromium_orphan')!r} orphans: {got!r}. Every one of those "
        f"processes is a descendant of the running app -- that is what an "
        f"in-use browser looks like. The deploy host measured peak 22 during a "
        f"real download and L33 called it a leak.")


def test_the_live_count_is_reported_so_the_number_is_explicable(tmp_path):
    """The operator has to be able to see WHY the orphan count is what it is.

    Reporting only the orphan total turns a 22-process reading into an
    unexplained 0, which is correct but unaccountable. Live and zombie counts
    are what make the total add up.
    """
    got = _count(tmp_path, _live_browser_tree(children=20) + _zombies(2)
                 + _foreign_cli_browser() + _true_orphans(1))
    assert got.get("chromium_live") == 21, f"chromium_live: {got!r}"
    assert got.get("chromium_zombie") == 2, f"chromium_zombie: {got!r}"
    assert got.get("chromium_foreign") == 1, f"chromium_foreign: {got!r}"
    assert got.get("chromium_orphan") == 1, f"chromium_orphan: {got!r}"
    assert (got["chromium_live"] + got["chromium_zombie"]
            + got["chromium_crashpad"] + got["chromium_foreign"]
            + got["chromium_orphan"]) == got["chromium"], (
        f"the five buckets must partition the raw comm-match count, or a "
        f"process has silently fallen out of the denominator: {got!r}")


def test_a_live_crashpad_handler_is_not_an_orphan(tmp_path):
    """THE FALSE POSITIVE THIS FILE SHIPPED, and it is the defect this whole cut
    exists to remove, reintroduced by the cut.

    A healthy launch on BD's default backend produced ORPHAN=2, both
    chrome_crashpad handlers at ppid=1 in state S. Reproduced twice. The
    predicate was right about every clause and wrong about the subject: crashpad
    reparents to init BY DESIGN so it can outlive and report a browser crash.
    """
    got = _count(tmp_path, _live_browser_tree(children=6)
                 + _crashpad_handlers(2))
    assert got.get("chromium_orphan") == 0, (
        f"reported {got.get('chromium_orphan')!r} orphans for two crashpad "
        f"handlers: {got!r}. They are ppid=1 and state S because the crash "
        f"handler must survive the browser it monitors -- that is the design, "
        f"not a leak. Measured: a healthy launch went 0 -> 2 -> 0.")
    assert got.get("chromium_crashpad") == 2, (
        f"the crashpad handlers must stay VISIBLE in their own bucket, not be "
        f"dropped: {got!r}. Silently excluding them shrinks the denominator to "
        f"hide a case instead of classifying it.")


def test_a_crashpad_handler_still_counts_in_the_raw_total(tmp_path):
    """Backwards compatibility. `chromium` is the raw comm-match count and 12
    files read it; crashpad matched it before this cut and must keep doing so."""
    got = _count(tmp_path, _live_browser_tree(children=1)
                 + _crashpad_handlers(2))
    assert got.get("chromium") == 2 + 2, (
        f"crashpad dropped out of the raw count: {got!r}")


def test_an_operator_cli_browser_is_not_an_orphan(tmp_path):
    """CAUGHT BY THE VERIFICATION FAN-OUT, not by me, and it would have shipped.

    tools/capture_session.py (its __main__ at :1100) and tools/nav_probe.py
    (:506) are standalone operator CLIs -- separate processes that launch their
    own browsers. Such a browser is NOT a descendant of the Flask app, so the
    descendant test alone classifies it as an orphan. It is not one: its launcher
    is sitting right there, alive and holding it.

    An operator running a capture session by hand while the live checks sampled
    would have seen a phantom leak -- the same false-positive class this cut
    exists to remove, reintroduced one layer down. The fix is that an orphan's
    PARENT MUST BE GONE, which is what the word means and is true independently
    of who launched the process.

    capture.sh itself never invokes capture_session.py (checked: only
    live_tests.run at :911), so this is a by-hand hazard rather than a
    capture-run one -- but it is a real one, and it is now the difference
    between two buckets rather than a wrong answer.
    """
    got = _count(tmp_path, _live_browser_tree(children=2)
                 + _foreign_cli_browser())
    assert got.get("chromium_orphan") == 0, (
        f"an operator CLI's browser was counted as an orphan: {got!r}. Its "
        f"parent (the capture_session python at pid {_CLI_PID}) is alive in the "
        f"table, so nothing about it is orphaned.")
    assert got.get("chromium_foreign") == 1, (
        f"the CLI's browser must still be VISIBLE, in its own bucket, not "
        f"dropped: {got!r}. Silently excluding it would shrink the denominator "
        f"to hide a case rather than classify it.")


# ── R2: the zombie trap ──────────────────────────────────────────────────────

def test_a_zombie_left_by_a_correct_close_is_not_an_orphan(tmp_path):
    """R2 -- THE TRAP THIS CUT NEARLY WALKED INTO.

    Measured: `browser.close()` leaves 2 processes at ppid=1 in state Z for up
    to ~3 seconds. They are not descendants of the app, so the natural
    definition of orphaned flags them -- on every healthy close. A zombie holds
    no browser, no port and no profile lock; it is a process-table entry waiting
    to be reaped.
    """
    got = _count(tmp_path, _live_browser_tree(children=2) + _zombies(2))
    assert got.get("chromium_orphan") == 0, (
        f"reported {got.get('chromium_orphan')!r} orphans for two ZOMBIES: "
        f"{got!r}. State 'Z' at ppid=1 is what a correct browser teardown looks "
        f"like for its first ~3 seconds. Counting them means L33 fires after "
        f"every successful download instead of before every leak.")


def test_a_still_running_process_at_ppid_one_IS_an_orphan(tmp_path):
    """R3 -- and the assertion that stops R2 being satisfied by ignoring ppid=1
    altogether.

    Excluding everything at ppid=1 would pass the zombie test and blind the
    check completely, because ppid=1 is precisely where a reparented orphan
    lives. State is the discriminator, not parentage alone.
    """
    got = _count(tmp_path, _live_browser_tree(children=2) + _true_orphans(3))
    assert got.get("chromium_orphan") == 3, (
        f"reported {got.get('chromium_orphan')!r} orphans: {got!r}. Three "
        f"browser processes are RUNNING (state S) with their launcher gone "
        f"(ppid=1). That is the leak this check is named for.")


def test_orphans_are_named_not_just_counted(tmp_path):
    """A count tells the operator there is a leak; it does not tell them what
    leaked. The profile path is the one field that identifies the site, and it
    is reported as DETAIL -- never used as a filter, because the plain-launch
    path has only an ephemeral /tmp profile and filtering on it would miss those
    orphans entirely.
    """
    got = _count(tmp_path, _true_orphans(1))
    detail = got.get("orphan_detail")
    assert isinstance(detail, list) and detail, f"no orphan_detail: {got!r}"
    first = detail[0]
    assert first.get("pid") == 950, f"{detail!r}"
    assert "wowgirls" in (first.get("user_data_dir") or ""), (
        f"the orphan's profile path was not reported: {detail!r}. It is the "
        f"only field that says WHICH browser leaked.")


def test_orphan_detail_is_bounded(tmp_path):
    """leak_scan's response is served over HTTP and read by a check. A runaway
    leak must not turn the payload into a thousand-entry dump."""
    got = _count(tmp_path, _true_orphans(200))
    assert got.get("chromium_orphan") == 200, f"{got!r}"
    assert len(got.get("orphan_detail") or []) <= 20, (
        f"orphan_detail carried {len(got.get('orphan_detail') or [])} entries; "
        f"it must be capped.")


# ── R4: unknown is not zero ──────────────────────────────────────────────────

def test_an_unreadable_proc_reports_nothing_rather_than_zero(tmp_path):
    """R4 -- section 0 directly, at the source.

    With no /proc there is no measurement, and the function must not manufacture
    one. The existing contract for that is an EMPTY dict, which every current
    caller already handles, so it is preserved rather than changed to a dict of
    zeros -- the important property is that no `chromium_orphan: 0` is invented.
    L33 turning this into NA rather than PASS is asserted separately in
    test_l33_is_not_exercisable_when_the_platform_cannot_answer; between them the
    two cover "unknown never reads as clean" at both ends.
    """
    got = perf_lab._child_process_count(
        proc_root=str(tmp_path / "does-not-exist"), self_pid=_APP_PID)
    assert got == {}, (
        f"an unreadable /proc produced {got!r}; expected the empty dict the "
        f"existing callers handle.")
    assert "chromium_orphan" not in got, (
        "an unreadable /proc must not report an orphan count at all -- zero is "
        "a claim about something nobody looked at.")


def test_an_unclassifiable_browser_reports_unknown_not_zero(tmp_path):
    """The partial case, which is the one that actually bites.

    /proc exists and lists a browser, but its `status` cannot be read, so its
    parentage is unknown. A count built from the readable subset would be a
    number with a hole in it, presented as a measurement.
    """
    root = _fake_proc(tmp_path, [_APP_ROW] + _live_browser_tree(children=1)[1:])
    # Strip the browser's status: /proc entries can be unreadable per-file.
    (root / str(_BROWSER_PID) / "status").unlink()
    got = perf_lab._child_process_count(proc_root=str(root),
                                        self_pid=_APP_PID)
    assert got.get("chromium") >= 1, f"the browser was not even matched: {got!r}"
    assert got.get("chromium_orphan", "MISSING") is None, (
        f"a browser whose parentage could not be read produced "
        f"chromium_orphan={got.get('chromium_orphan')!r}: {got!r}. Unknown "
        f"parentage means unknown orphanhood.")


def test_an_invisible_app_pid_reports_unknown(tmp_path):
    """The guard that makes the two fixtures above realistic.

    If the app's own pid is not in the process table -- a /proc from another
    namespace, or a caller passing a self_pid that does not exist -- then nothing
    can be shown to descend from it, and EVERY live browser would read as an
    orphan. That is the worst possible failure mode for this check: it would
    report a maximal leak precisely when it understands least.
    """
    root = _fake_proc(tmp_path, _live_browser_tree(children=3)[1:])  # no app row
    got = perf_lab._child_process_count(proc_root=str(root), self_pid=_APP_PID)
    assert got.get("chromium") == 4, f"fixture not matched: {got!r}"
    assert got.get("chromium_orphan", "MISSING") is None, (
        f"with the app's pid absent from the table, chromium_orphan was "
        f"{got.get('chromium_orphan')!r}: {got!r}. Descent is unprovable here, "
        f"so the four live browser processes would have been called orphans.")


def test_a_pid_that_vanishes_mid_walk_does_not_break_the_scan(tmp_path):
    """/proc is live: a pid can exit between listdir and read. An exception here
    would take out leak_scan's whole response, so the surviving processes must
    still be counted.
    """
    root = _fake_proc(tmp_path, _live_browser_tree(children=2)
                      + _true_orphans(1))
    # A pid directory with no readable comm/status is exactly what a race
    # leaves behind.
    ghost = root / "4242"
    ghost.mkdir()
    got = perf_lab._child_process_count(proc_root=str(root),
                                        self_pid=_APP_PID)
    assert got.get("chromium_orphan") == 1, (
        f"a pid directory with no readable comm derailed the scan: {got!r}")


# ── the old keys must survive: 12 files read them ────────────────────────────

def test_the_existing_keys_are_unchanged(tmp_path):
    """`chromium`, `ffmpeg` and `total_procs` are read by perf_lab's own report,
    dev_suite/perf_metrics.leak_scan, and the existing tests. Adding keys is
    safe; changing the meaning of an existing one is not, so `chromium` stays
    the raw comm-match count over every state.
    """
    rows = _live_browser_tree(children=2) + _zombies(1) + _true_orphans(1)
    rows.append((800, "ffmpeg", _APP_PID, "S", "ffmpeg -i x.m3u8 out.mp4"))
    got = _count(tmp_path, rows)
    assert got.get("chromium") == 3 + 1 + 1, f"{got!r}"
    assert got.get("ffmpeg") == 1, f"{got!r}"
    assert isinstance(got.get("total_procs"), int) and got["total_procs"] > 0


def test_the_default_arguments_still_read_the_real_proc():
    """The production call sites pass nothing (perf_lab.py:153,
    dev_suite/perf_metrics.py:34). The new parameters must be optional, and the
    default must be the real /proc and the real pid -- not a leftover test
    value.
    """
    got = perf_lab._child_process_count()
    assert isinstance(got, dict) and got, "the no-argument call returned nothing"
    assert got.get("total_procs", 0) > 1, (
        f"the default proc_root is not reading the real /proc: {got!r}")
    # This process is a descendant of itself, so anything it launched is live,
    # never orphaned. Nothing here should be classified as an orphan of us.
    assert got.get("chromium_orphan") is not None, (
        f"chromium_orphan is None on a host that has /proc: {got!r}")


# ── L33 must read the orphan signal, and say so when it cannot ───────────────

class _Ctx(harness.Context):
    """Real Context, HTTP stubbed. Subclassed rather than reimplemented -- an
    earlier cut here reimplemented ro_db instead of subclassing and the fake
    diverged from the harness it was standing in for."""

    def __init__(self, body):
        super().__init__("http://ctx.invalid", "/tmp", disruptive=False)
        self._body = body
        self.messages: list[str] = []

    def log(self, msg):
        self.messages.append(str(msg))

    def get(self, path, timeout=15):
        return True, 200, dict(self._body), 1.0


@pytest.fixture(autouse=True)
def _no_sampling_sleep(monkeypatch):
    """L33 samples through _sample_over_time, which sleeps _SAMPLE_GAP_S between
    polls. The GAP is not the subject here -- the classification is -- so the
    gap is collapsed while the sample COUNT is left alone, because a
    single-sample run would hide the growth branch this file also asserts on.
    """
    monkeypatch.setattr(checks, "_SAMPLE_GAP_S", 0.0)


def _leak_body(**kw):
    base = {"processes": {"chromium": 0, "ffmpeg": 0, "total_procs": 100,
                          "chromium_live": 0, "chromium_zombie": 0,
                          "chromium_orphan": 0, "orphan_detail": []},
            "findings": [], "verdict": "no leak signals",
            "rate_limit_files": [], "screenshots": {"files": 0, "bytes": 0}}
    base["processes"].update(kw)
    return base


def test_l33_passes_while_a_download_is_running(tmp_path):
    """R5 -- the end the operator sees.

    22 browser processes, all live. Today: WARN "Playwright contexts may be
    leaking". Required: PASS, because nothing is orphaned.
    """
    body = _leak_body(chromium=22, chromium_live=22, chromium_orphan=0)
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.PASS, (
        f"L33 returned {level}: {detail!r} for 22 LIVE browser processes. That "
        f"is one browser and its renderers during a download -- the reading the "
        f"deploy host produced, and the reason this check was reporting a leak "
        f"on a working fetch.")
    assert "leak" not in detail.lower() or "no" in detail.lower(), (
        f"the PASS text still talks about leaking: {detail!r}")


def test_l33_warns_on_a_real_orphan(tmp_path):
    """The signal must survive. Three running browsers with no launcher is a
    leak and must not be swallowed by the fix for the false positive."""
    body = _leak_body(chromium=25, chromium_live=22, chromium_orphan=3,
                      orphan_detail=[{"pid": 950, "comm": _BROWSER_COMM,
                                      "ppid": 1, "user_data_dir": None}])
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level in (harness.WARN, harness.FAIL), (
        f"L33 returned {level}: {detail!r} despite 3 orphaned browsers.")
    assert "3" in detail, f"the orphan count is not in the verdict: {detail!r}"


def test_l33_is_not_exercisable_when_the_platform_cannot_answer(tmp_path):
    """R6 -- checks.py:2415 currently converts an empty `processes` dict plus
    the endpoint's own "no leak signals" verdict into a PASS.

    That branch exists because /proc does not exist on Windows and L33 WARN'd
    forever. The problem is real; asserting a measurement nobody took is not the
    fix. NA -- not exercisable here -- is the honest verdict, and it does not
    gate the deploy: run_all's exit code keys on FAIL alone.
    """
    body = {"processes": {}, "findings": [], "verdict": "no leak signals",
            "rate_limit_files": [], "screenshots": {"files": 0, "bytes": 0}}
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.NA, (
        f"L33 returned {level}: {detail!r} with an EMPTY processes dict. The "
        f"endpoint could not enumerate processes at all, so no orphan count was "
        f"taken. Trusting the endpoint's own 'no leak signals' verdict here "
        f"reports OK for a question that was never asked.")


def test_l33_does_not_treat_a_missing_orphan_key_as_zero(tmp_path):
    """A deploy where the app is older than this cut serves a `processes` dict
    with no `chromium_orphan` key. Reading a missing key as 0 would make L33
    certify an app that cannot answer -- the classic stale-deploy blind spot,
    and worse here because the payload looks populated.
    """
    body = {"processes": {"chromium": 22, "ffmpeg": 0, "total_procs": 100},
            "findings": [], "verdict": "no leak signals",
            "rate_limit_files": [], "screenshots": {"files": 0, "bytes": 0}}
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.NA, (
        f"L33 returned {level}: {detail!r} for a payload with no "
        f"chromium_orphan key. 22 raw matches with no orphan breakdown is an "
        f"app that predates the orphan measurement; the honest answer is that "
        f"this deploy cannot be asked, not that it is clean.")


def test_l33_reports_unknown_when_the_orphan_count_is_explicitly_none(tmp_path):
    """The /proc-unavailable case travelling over the wire as JSON null."""
    body = _leak_body(chromium=0, chromium_orphan=None)
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.NA, (
        f"L33 returned {level}: {detail!r} for chromium_orphan=null")


# ── the two unknowns are DIFFERENT unknowns ──────────────────────────────────
#
# Added because two mutations survived. Removing L33's classifiability check
# entirely left every assertion above green, because a missing orphan key ALSO
# reaches NA by a second route: _orphans returns None, no samples accumulate, and
# the empty-samples branch returns NA. Two independent paths to the same verdict
# meant the guard could be deleted without any test noticing.
#
# The redundancy is not the problem -- the verdict is right either way. The
# problem is that the two cases are DIFFERENT DIAGNOSES and were collapsing into
# one message. "This deploy predates the orphan measurement" tells the operator
# to redeploy; "the endpoint served no process table" tells them the platform
# cannot answer; "could not sample" tells them neither. Asserting the message
# makes the guard load-bearing and gives the operator the actionable half.

def test_an_old_deploy_is_diagnosed_as_an_old_deploy():
    """A payload with raw counts but no classification: the app is older than
    this cut. The verdict must say so, not just shrug."""
    body = {"processes": {"chromium": 22, "ffmpeg": 0, "total_procs": 100},
            "findings": [], "verdict": "no leak signals",
            "rate_limit_files": [], "screenshots": {"files": 0, "bytes": 0}}
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.NA, f"{level}: {detail!r}"
    assert "predates" in detail.lower(), (
        f"the verdict was {detail!r}. 22 raw matches with no orphan breakdown "
        f"is specifically a deploy that predates the classification, and the "
        f"operator can fix that by redeploying -- but only if told.")


def test_a_platform_without_proc_is_diagnosed_as_such():
    """An empty process table: /proc does not exist here. Different cause,
    different remedy, so it must be a different message."""
    body = {"processes": {}, "findings": [], "verdict": "no leak signals",
            "rate_limit_files": [], "screenshots": {"files": 0, "bytes": 0}}
    level, detail = checks.l33_no_leaked_chromium(_Ctx(body))
    assert level == harness.NA, f"{level}: {detail!r}"
    assert "process table" in detail.lower(), (
        f"the verdict was {detail!r}; expected it to name the missing process "
        f"table rather than reporting a generic sampling failure.")
    assert "predates" not in detail.lower(), (
        f"an empty process table was misdiagnosed as an old deploy: {detail!r}")


_UNKNOWN_STATES = [
    # (label, body, expected level, a fragment the verdict must contain)
    ("endpoint down", None, harness.WARN, "did not answer"),
    ("no process table at all",
     {"processes": {}, "findings": [], "verdict": "no leak signals"},
     harness.NA, "process table"),
    ("deploy predates the classification",
     {"processes": {"chromium": 22, "ffmpeg": 0, "total_procs": 100},
      "findings": [], "verdict": "no leak signals"},
     harness.NA, "predates"),
    ("measured, parentage unknown",
     {"processes": {"chromium": 22, "chromium_orphan": None},
      "findings": [], "verdict": "no leak signals"},
     harness.NA, "null"),
    ("unclassifiable but the endpoint is flagging something",
     {"processes": {}, "findings": ["something is leaking"],
      "verdict": "leak detected"},
     harness.WARN, "UNKNOWN"),
]


@pytest.mark.parametrize("label,body,want_level,fragment", _UNKNOWN_STATES,
                         ids=[s[0] for s in _UNKNOWN_STATES])
def test_the_ways_of_not_knowing_stay_distinct(label, body, want_level,
                                               fragment):
    """FIVE STATES THAT USED TO BE TWO, and every collapse between them was a
    bug I introduced and then had to back out.

    The old code merged "no process table" into PASS by trusting the endpoint's
    own verdict. My first fix merged "endpoint down" into NA, which would have
    silenced the one signal L31 and L32 both give -- caught because an existing
    test asserted WARN for all three checks together. My second merged
    "measured, parentage unknown" into "endpoint down", because a present-but-
    null key looked like a classification and then produced no samples.

    They need different verdicts because they need different actions: redeploy,
    ignore (wrong platform), look at the app, or investigate a flagged leak. A
    parametrised table makes each one load-bearing instead of incidental.
    """
    class _Down(_Ctx):
        def get(self, path, timeout=15):
            return False, None, "unreachable", 1.0

    ctx = _Down({}) if body is None else _Ctx(body)
    level, detail = checks.l33_no_leaked_chromium(ctx)
    assert level == want_level, (
        f"[{label}] L33 returned {level}, expected {want_level}: {detail!r}")
    assert fragment.lower() in detail.lower(), (
        f"[{label}] the verdict does not say which kind of not-knowing this "
        f"is (looking for {fragment!r}): {detail!r}")


def test_l33_never_reads_the_raw_process_count_as_an_orphan_count():
    """STRUCTURAL, because the behavioural route to this is masked.

    The raw `chromium` key used to be the last fallback in L33's key list, and
    it is the entire defect: it answers "how many browser processes exist",
    not "how many are orphaned". Mutating that fallback back in survives every
    behavioural assertion here, because the classifiability check returns NA
    before the fallback list is ever consulted -- so the mutation is real, the
    tests are right, and nothing observes it.

    Asserted over the AST of the check: `"chromium"` as an exact string constant
    must not appear. `"chromium_orphan"` is a different constant and is
    unaffected -- which is precisely why this is an AST assertion and not a
    substring search over the source text.
    """
    import ast
    src = (ROOT / "live_tests" / "checks.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef)
               and n.name == "l33_no_leaked_chromium"), None)
    assert fn is not None, "l33_no_leaked_chromium not found"
    bare = [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and n.value == "chromium"]
    assert not bare, (
        f"l33_no_leaked_chromium references the raw process-count key "
        f"\"chromium\" at line(s) {bare}. That key is a substring match on comm "
        f"over every process on the box -- 22 during a healthy download -- and "
        f"reading it as an orphan count is the defect this cut removes.")


# ── leak_scan's own finding text must stop claiming leakage from a raw count ──

def test_leak_scan_does_not_call_a_live_browser_a_leaked_context(monkeypatch):
    """`perf_metrics.py:37` fires `N Chromium processes alive -- possible leaked
    Playwright contexts` at `chromium > 8`. 22 live processes is one browser
    doing its job, so that finding is wrong on every real download, and it is
    what makes leak_scan's own `verdict` non-empty.

    The process dict is INJECTED as a plain value rather than by pointing the
    real walk at a fake /proc. The first draft did the latter, and it passed on
    pristine source for the wrong reason: the pristine
    `_child_process_count` has no proc_root parameter, the TypeError was
    swallowed by leak_scan's own `except Exception` at perf_metrics.py:43,
    `processes` became {} and no finding was emitted. A vacuous green, caught
    only because the RED run reported 1 passed where it should have reported 0.
    Injecting the dict makes the subject leak_scan's THRESHOLD LOGIC, which is
    what this assertion is actually about.
    """
    from bulk_downloader.dev_suite import perf_metrics
    monkeypatch.setattr(perf_lab, "_child_process_count", lambda **kw: {
        "chromium": 21, "ffmpeg": 0, "total_procs": 100,
        "chromium_live": 21, "chromium_zombie": 0, "chromium_orphan": 0,
        "orphan_detail": [],
    })
    out = perf_metrics.leak_scan()
    assert out.get("processes", {}).get("chromium") == 21, (
        f"the injected process dict did not reach leak_scan -- it was swallowed "
        f"by the except branch, so this assertion would pass vacuously: "
        f"{out.get('processes')!r}")
    browser_findings = [f for f in out.get("findings") or []
                        if "browser" in f.lower() or "chromium" in f.lower()
                        or "playwright" in f.lower()]
    assert not browser_findings, (
        f"leak_scan reported {browser_findings!r} for 21 LIVE browser "
        f"processes under the running app. The threshold of 8 is a guess about "
        f"how many processes one browser has; the browser's own child fan-out "
        f"measured 6 for a blank page and 22 under a download. The orphan count "
        f"is 0 and that is the number the finding must key on.")


def test_leak_scan_still_reports_a_real_orphan(monkeypatch):
    """The finding must survive for the case it was meant for. Removing the
    threshold entirely would satisfy the assertion above and blind leak_scan."""
    from bulk_downloader.dev_suite import perf_metrics
    monkeypatch.setattr(perf_lab, "_child_process_count", lambda **kw: {
        "chromium": 24, "ffmpeg": 0, "total_procs": 100,
        "chromium_live": 21, "chromium_zombie": 0, "chromium_orphan": 3,
        "orphan_detail": [{"pid": 950, "comm": _BROWSER_COMM, "ppid": 1,
                           "user_data_dir": None}],
    })
    out = perf_metrics.leak_scan()
    browser_findings = [f for f in out.get("findings") or []
                        if "browser" in f.lower() or "chromium" in f.lower()
                        or "playwright" in f.lower()]
    assert browser_findings, (
        f"leak_scan reported no browser finding despite 3 orphaned browsers: "
        f"{out!r}")
    assert "3" in browser_findings[0], (
        f"the finding does not name the orphan count: {browser_findings!r}")
