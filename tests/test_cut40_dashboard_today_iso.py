"""Three dashboard "today" counters compared a wall clock to a date.

THE DEFECT. `runner_util.py:57` writes the job's display stamp as
``_ts() -> datetime.now().strftime("%H:%M:%S")`` -- a bare wall-clock time with
no date in it. Three day-window filters read that field and test it against a
date prefix:

    today_iso = _t.strftime("%Y-%m-%d")        # "2026-07-31"
    ...
    ts = j.get("ts","") or ""
    if ts.startswith(today_iso):

``"19:32:40".startswith("2026-07-31")`` is False for every possible pair of
values, so all three counters are structurally 0 -- always, on every host, for
every job. The sites are `app.py:_dashboard_snapshot()` (the SSE
``event: dashboard`` payload), `app_dashboard.py:api_dashboard()`
(``GET /api/dashboard``, which `bdctl status` prints), and
`app_dashboard.py:api_dashboard_v2()` (``GET /api/dashboard/v2``, whose ONE
filtered line drives BOTH the top-level ``today.{done,failed}`` and every
``by_site[].today_done``).

This is the identical defect CUT #31 fixed for ``done_today_count`` at
`app_queue.py:228`. That cut added the date-comparable sibling ``ts_iso``
(runner.py:1658 live, runner_queue.py:106 on restart rehydrate) and repointed
only its own consumer, leaving three of the four consumers on the display
field -- so the queue tab and the dashboard now disagree about the same jobs.

WHY THE EXISTING BAND IS BLIND. Every dashboard test in the tree asserts
``today.* == 0`` in COLD state with no runners registered
(test_d3_u2_v2_endpoints.py, tests/test_api.py). That is true on the broken and
the fixed tree alike: the denominator structurally excludes the only state that
tells them apart -- CLAUDE.md section 0. The named band is a blast-radius band,
not a detection band, which is why this file has to exist.

WHY THE FAKE RUNNER PINS ITS EXACT SURFACE. `app.py:3907` and
`app_dashboard.py:195` both wrap their per-runner job loop in
``try: ... except Exception:`` (``continue`` / ``pass``). A fake missing one
attribute raises, is SWALLOWED, and the counter reads 0 on the FIXED tree too --
a mislabelled RED that can never go green. Only `app_dashboard.py:61` (the T2
site) has no swallow, so only T2's ok-assertion can separate "bad fake" from
"real defect". For T1, T3 and T4 the ONLY proof the fake is adequate is the
measured PASS on the fixed tree; a RED-only run proves nothing there.

SCOPE, stated so nobody reads more into a green run than it carries. This cut
repoints three CONSUMERS. It does not fix the producer gap: three terminal
paths (app_sites_queue.py manual mark and bulk mark, runner_queue.py's
already-on-disk enqueue) drive a job to ``done`` without ever writing
``ts_iso``, so those jobs still will not count. G4 pins that boundary as an
executable fact rather than a comment, and it is the only guard that catches
the seductive wrong fix ``j.get("ts_iso","") or today_iso``.

TIMEZONE, an inherited risk this cut propagates to three more surfaces:
runner.py stamps ``ts_iso`` LOCAL, runner_queue.py copies sqlite ``ts_updated``
which db.py stamps UTC, and all three repointed consumers compare against a
LOCAL ``time.strftime("%Y-%m-%d")``. On a non-UTC host a rehydrated job can be
counted on the wrong day near midnight. Nothing here can see it -- the fixtures
stamp LOCAL and the endpoints read LOCAL, so this file is silent on that, not
clean. Unlike CUT #31's T3, no test here reads the sqlite queue table, so the
UTC/LOCAL mix is out of this file's reach; do not copy CUT #31's
equality-instead-of-startswith workaround, it addresses a different confound.
The residual in-test cry-wolf -- a fixture stamped on one local day evaluated
on the next -- is removed by `_guard_midnight`, which skips ONLY when the roll
is proven to have happened and re-raises otherwise.
"""
from __future__ import annotations

import ast
import re
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytestmark = pytest.mark.bd_module_wipe

_URL = "https://example.invalid/cut40.mp4"


# ── fakes and helpers ───────────────────────────────────────────────────────

class _DashRunner:
    """Stand-in with the exact surface the three dashboard producers touch.

    Every member is dereferenced by the code under test, and every one of them
    is inside a swallowing ``try``:
      _lock                      app.py:3908, app_dashboard.py:61, :196
      jobs                       app.py:3909, app_dashboard.py:62, :197
      state()                    app.py:3906, app_dashboard.py:59, :211
      cookies                    app_dashboard.py:88 (cookies_expiry_info)
      is_rate_limited()          app_dashboard.py:100, app.py _m2_attention
      _current_throughput_bps()  app_dashboard.py:111
      _recent_per_min            read by the snapshot's rate block
      active_worker_count()      read by the snapshot's worker block
    Drop any one and the counter reads 0 for the WRONG reason on both trees.
    """

    def __init__(self, jobs):
        self._lock = threading.Lock()
        self.jobs = jobs
        self._recent_per_min = 0
        self.cookies = []

    def state(self):
        return "idle"

    def is_rate_limited(self):
        return False

    def _current_throughput_bps(self):
        return 0.0

    def active_worker_count(self):
        return 0


def _register(sid, jobs):
    """Register a fake runner in the live app_state; returns a cleanup call."""
    from bulk_downloader import app_state as st
    st.s_cfg[sid] = {"name": sid}
    st.runners[sid] = _DashRunner(jobs)

    def _cleanup():
        st.runners.pop(sid, None)
        st.s_cfg.pop(sid, None)
    return _cleanup


def _job(status, day_iso):
    """A job dict shaped byte-identically to what runner.py:1648-1659 writes."""
    return {"status": status, "message": "",
            "ts": time.strftime("%H:%M:%S"),
            "ts_iso": day_iso + "T" + time.strftime("%H:%M:%S")}


def _snapshot():
    from bulk_downloader import app as a
    return a._dashboard_snapshot()


def _get(path):
    from bulk_downloader import app as a
    return a.app.test_client().get(path)


def _by_site(body, sid):
    """The single by_site entry for `sid`.

    Selected by site_id, never by index: an un-isolated run can carry a fleet
    of pre-existing runners and an index lookup would then fail for the wrong
    reason.
    """
    hits = [e for e in (body.get("by_site") or []) if e.get("site_id") == sid]
    assert len(hits) == 1, f"expected exactly one by_site entry for {sid!r}: {hits!r}"
    return hits[0]


def _guard_midnight(fn):
    """Run `fn(today_iso)`; treat a proven local-date roll as a skip.

    The only cry-wolf available to this file is the sub-second window where the
    fixture is stamped on one local day and the endpoint evaluates on the next.
    This removes it WITHOUT making the test blind: it skips only when the roll
    is proven to have happened, and re-raises otherwise.
    """
    today = time.strftime("%Y-%m-%d")
    try:
        fn(today)
    except AssertionError:
        if time.strftime("%Y-%m-%d") != today:
            pytest.skip("local date rolled mid-test; the day window is ambiguous")
        raise


@pytest.fixture
def runner_db(clean_workdir):
    """Isolated BD home WITH the sqlite tables created.

    SiteRunner hits the queue table unconditionally on construct, so without
    db_init() the test dies on `no such table: queue` -- a failure for the
    wrong reason that proves nothing about the defect.
    """
    from bulk_downloader.db import db_init
    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    return clean_workdir


# ── T1: the SSE snapshot must count today's jobs (RED) ──────────────────────

def test_dashboard_snapshot_counts_today():
    """RED. app.py:3912 reads the display `ts`, so `today` is all zeros.

    The failure is an AssertionError carrying the defect's own signature
    (three zeros), not an exception and not a 500. That it can go GREEN at all
    is what proves the fake is adequate, because app.py:3907's
    `except Exception: continue` would swallow an inadequate one.
    """
    cleanup = _register("cut40_t1", {
        _URL: _job("done", time.strftime("%Y-%m-%d")),
        _URL + "?2": _job("failed", time.strftime("%Y-%m-%d")),
        _URL + "?3": _job("needs_review", time.strftime("%Y-%m-%d")),
    })
    try:
        def _check(_today):
            today = _snapshot().get("today") or {}
            assert today.get("done") == 1, today
            assert today.get("failed") == 1, today
            assert today.get("needs_review") == 1, today
        _guard_midnight(_check)
    finally:
        cleanup()


# ── T2: GET /api/dashboard must count today's jobs (RED) ────────────────────

def test_api_dashboard_counts_today():
    """RED. app_dashboard.py:66. This is the ONE site with no swallow around
    the job loop, so an inadequate fake yields a 500 here and the ok-assertion
    fails first with a distinguishable message."""
    cleanup = _register("cut40_t2", {
        _URL: _job("done", time.strftime("%Y-%m-%d")),
        _URL + "?2": _job("failed", time.strftime("%Y-%m-%d")),
        _URL + "?3": _job("needs_review", time.strftime("%Y-%m-%d")),
    })
    try:
        def _check(_today):
            r = _get("/api/dashboard")
            assert r.status_code == 200, r.status_code
            body = r.get_json() or {}
            assert body.get("ok") is True, body
            today = body.get("today") or {}
            assert today.get("done") == 1, today
            assert today.get("failed") == 1, today
            assert today.get("needs_review") == 1, today
        _guard_midnight(_check)
    finally:
        cleanup()


# ── T3: GET /api/dashboard/v2 top-level today (RED) ─────────────────────────

def test_api_dashboard_v2_counts_today():
    """RED. app_dashboard.py:203, first of the two outputs that line drives.

    NOT "the same construction as T2": app_dashboard.py:195-215 wraps the v2
    per-runner block in `try: with runner._lock: ... except Exception: pass`,
    so an inadequate fake is SWALLOWED here exactly as at app.py:3907 and the
    endpoint still answers 200 + ok True + today all-zero -- byte-identical to
    the defect. The ok-assertion cannot separate them; only the measured PASS
    on the fixed tree can.

    `done` and `failed` are asserted individually rather than by dict equality
    because the v2 today block has a different shape (`running`, not
    `needs_review`) and must not fail on an unrelated shape change.
    """
    cleanup = _register("cut40_t3", {
        _URL: _job("done", time.strftime("%Y-%m-%d")),
        _URL + "?2": _job("failed", time.strftime("%Y-%m-%d")),
    })
    try:
        def _check(_today):
            r = _get("/api/dashboard/v2")
            assert r.status_code == 200, r.status_code
            body = r.get_json() or {}
            assert body.get("ok") is True, body
            today = body.get("today") or {}
            assert today.get("done") == 1, today
            assert today.get("failed") == 1, today
        _guard_midnight(_check)
    finally:
        cleanup()


# ── T4: the SAME line's second output, by_site[].today_done (RED) ───────────

def test_api_dashboard_v2_by_site_today_done():
    """RED, and mandatory separately from T3.

    One source line feeds two independent outputs; a fix verified only by T3
    would leave BySiteList's per-site "today" column unproven. Same swallow
    caveat as T3: the entry EXISTING with the right site_id proves the runner
    registered and by_site was built, but not that the fake is adequate --
    only the fixed-tree PASS does that.
    """
    cleanup = _register("cut40_t4", {
        _URL: _job("done", time.strftime("%Y-%m-%d")),
    })
    try:
        def _check(_today):
            r = _get("/api/dashboard/v2")
            assert r.status_code == 200, r.status_code
            body = r.get_json() or {}
            assert body.get("ok") is True, body
            entry = _by_site(body, "cut40_t4")
            assert entry.get("today_done") == 1, entry
        _guard_midnight(_check)
    finally:
        cleanup()


# ── T5: no day-window filter may read the display field (RED, structural) ───

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own_nodes(scope):
    """Every node belonging to `scope` itself, not to a nested scope."""
    out = []

    def _walk(n):
        for child in ast.iter_child_nodes(n):
            if isinstance(child, _SCOPES):
                continue
            out.append(child)
            _walk(child)
    _walk(scope)
    return out


def _bound_names(node):
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    return []


def _lookup_keys(tree):
    """Constant string keys this subtree reads from a mapping.

    Covers both `x.get("k")` and `x["k"]` so a rewrite of the access style
    cannot walk out of the gate's denominator.
    """
    keys = set()
    for c in ast.walk(tree):
        if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "get" and c.args
                and isinstance(c.args[0], ast.Constant)
                and isinstance(c.args[0].value, str)):
            keys.add(c.args[0].value)
        elif (isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Constant)
                and isinstance(c.slice.value, str)):
            keys.add(c.slice.value)
    return keys


def _reads_display_ts(tree):
    keys = _lookup_keys(tree)
    return "ts" in keys and "ts_iso" not in keys


def _day_window_violations(root: Path):
    """(violations, unparseable) over every .py under `root`.

    INSTRUMENT: ast.parse over the whole package -- the denominator is every
    .py file, not one directory and not a grep.

    PREDICATE, derived rather than asserted in three places, because each
    hardcoded assumption was a blind spot:
      * the day-window variable is DERIVED per scope (any name bound from an
        expression carrying a "%Y-%m-%d" literal, plus a parameter named
        today/day) instead of hardcoding the identifier `today_iso` -- a
        broken copy that named its variable `_today` would otherwise be
        invisible.
      * bindings are FLOW-SENSITIVE: at a `startswith` site the LAST binding at
        or above that line wins. A scope that renders the display `ts` and then
        rebinds from `ts_iso` before filtering is CORRECT, and a
        union-of-all-bindings predicate would fire on it -- a gate that fires
        on correct input gets switched off (CLAUDE.md section 0, inverse).
      * a NON-Name receiver (inline `j.get("ts","").startswith(...)`, a
        subscript, a walrus) is judged on its own subtree, so the three
        binding-free ways to write the same bug are inside the denominator.

    Deliberately NOT a count assertion ("exactly 4 day filters"): that would
    fire on any legitimate fifth consumer.
    """
    violations = []
    unparseable = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            unparseable.append(f"{path}: {e}")
            continue
        rel = path.relative_to(root.parent)
        for scope in [tree] + [n for n in ast.walk(tree) if isinstance(n, _SCOPES)]:
            nodes = _own_nodes(scope)
            day_names = set()
            bindings = []          # (lineno, order, name, reads_display)
            for i, n in enumerate(nodes):
                if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    val = getattr(n, "value", None)
                    if val is None:
                        continue
                    names = _bound_names(n)
                    if any(isinstance(c, ast.Constant)
                           and isinstance(c.value, str) and "%Y-%m-%d" in c.value
                           for c in ast.walk(val)):
                        day_names.update(names)
                    if _lookup_keys(val) & {"ts", "ts_iso"}:
                        for nm in names:
                            bindings.append((n.lineno, i, nm, _reads_display_ts(val)))
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = scope.args
                for arg in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)):
                    low = arg.arg.lower()
                    if "today" in low or "day" in low:
                        day_names.add(arg.arg)
            if not day_names:
                continue
            scope_name = getattr(scope, "name", "<module>")
            for n in nodes:
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "startswith" and len(n.args) == 1
                        and isinstance(n.args[0], ast.Name)
                        and n.args[0].id in day_names):
                    continue
                recv = n.func.value
                bad = False
                if isinstance(recv, ast.Name):
                    prior = sorted([b for b in bindings
                                    if b[2] == recv.id and b[0] <= n.lineno])
                    if prior and prior[-1][3]:
                        bad = True
                else:
                    bad = _reads_display_ts(recv)
                if bad:
                    violations.append(f"{rel}:{n.lineno} in {scope_name}()")
    return sorted(set(violations)), unparseable


def test_no_day_window_filter_reads_the_display_ts():
    """RED (structural anti-drift gate).

    On pristine it names exactly the three sites the behavioural tests prove
    broken, and nothing else. Its job after the fix is to stop a FOURTH copy
    of the pattern landing silently -- the behavioural tests cannot see a copy
    that nothing calls yet.
    """
    root = _REPO / "bulk_downloader"
    assert root.is_dir(), f"cannot find the package to scan: {root}"
    violations, unparseable = _day_window_violations(root)
    assert not unparseable, (
        "files the gate could not parse -- UNKNOWN is a third state and it "
        f"fails: {unparseable}")
    assert not violations, (
        "day-window filter(s) reading the display `ts` (HH:MM:SS) instead of "
        "the date-comparable `ts_iso`; each of these is structurally always "
        f"zero:\n  " + "\n  ".join(violations))


# ── G1: yesterday must not count (soundness guard, green on both trees) ─────

def test_yesterday_does_not_count():
    """SOUNDNESS GUARD -- green on pristine too, NOT counted as RED.

    Without it, "drop the date filter" satisfies T1-T4 while trading always-0
    for always-wrong: an unbounded lifetime total labelled "today", on the
    SPA's most-looked-at surface.
    """
    y = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    cleanup = _register("cut40_g1", {
        _URL: _job("done", y),
        _URL + "?2": _job("failed", y),
        _URL + "?3": _job("needs_review", y),
    })
    try:
        snap = _snapshot().get("today") or {}
        assert snap.get("done") == 0 and snap.get("failed") == 0 \
            and snap.get("needs_review") == 0, snap
        b1 = (_get("/api/dashboard").get_json() or {}).get("today") or {}
        assert b1.get("done") == 0 and b1.get("failed") == 0 \
            and b1.get("needs_review") == 0, b1
        v2 = _get("/api/dashboard/v2").get_json() or {}
        t2 = v2.get("today") or {}
        assert t2.get("done") == 0 and t2.get("failed") == 0, t2
        assert _by_site(v2, "cut40_g1").get("today_done") == 0, v2
    finally:
        cleanup()


# ── G2: the display field must keep its HH:MM:SS shape (guard) ─────────────

def test_display_ts_stays_hhmmss(runner_db):
    """REGRESSION GUARD -- green on both trees.

    This cut adds a SIBLING read. It must not widen `ts`, which is the
    human-readable value the queue UI renders.
    """
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("cut40_g2", {"name": "cut40_g2"})
    r._update_job(_URL, "done", "ok")
    ts = r.jobs[_URL].get("ts", "")
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", ts), (
        f"display ts={ts!r} is no longer HH:MM:SS")


# ── G3: cold state stays zero (guard) ──────────────────────────────────────

def test_empty_fleet_stays_zero():
    """REGRESSION GUARD -- mirrors the existing cold-state assertions so the
    cut cannot "fix" the counters by making them unconditionally non-zero.

    Honestly the weakest guard here: no mutation flips it and nothing else.
    Kept for coverage, not for mutation strength.
    """
    from bulk_downloader import app_state as st
    assert not st.runners, f"expected an empty fleet, got {list(st.runners)}"
    snap = _snapshot().get("today") or {}
    assert snap.get("done") == 0 and snap.get("failed") == 0, snap
    t2 = (_get("/api/dashboard/v2").get_json() or {}).get("today") or {}
    assert t2.get("done") == 0 and t2.get("failed") == 0, t2


# ── G4: a job with no ts_iso must count zero (boundary pin) ────────────────

def test_job_with_no_ts_iso_counts_zero():
    """BOUNDARY PIN for the out-of-scope producer gap -- green on both trees.

    Shape is exactly what a manual mark leaves behind: status done, a display
    `ts`, and NO ts_iso key at all. Those jobs still will not count after this
    cut, and that is a separate item.

    This is the ONLY guard that catches the seductive wrong fix
    `j.get("ts_iso","") or today_iso` -- "the key is missing on some paths, so
    default it to today" makes every ts_iso-less job, including ones marked
    done weeks ago, count as completed today forever. Every fixture in T1-T4
    and G1 HAS a ts_iso, so nothing else here objects to it.
    """
    cleanup = _register("cut40_g4", {
        _URL: {"status": "done", "message": "", "ts": time.strftime("%H:%M:%S")},
    })
    try:
        snap = _snapshot().get("today") or {}
        assert snap.get("done") == 0, snap
        b1 = (_get("/api/dashboard").get_json() or {}).get("today") or {}
        assert b1.get("done") == 0, b1
        v2 = _get("/api/dashboard/v2").get_json() or {}
        assert (v2.get("today") or {}).get("done") == 0, v2
        assert _by_site(v2, "cut40_g4").get("today_done") == 0, v2
    finally:
        cleanup()


# ── CUT #42: the status SETS were correct and completely unguarded ──────────

def test_each_consumer_counts_exactly_the_statuses_its_label_claims():
    """The four day-window consumers deliberately count DIFFERENT status sets:

        app.py:3912          done, failed, needs_review  -> today.{...}
        app_dashboard.py:66  done, failed, needs_review  -> today.{...}
        app_dashboard.py:203 done, failed ONLY           -> today.{...} + by_site
        app_queue.py:228     done ONLY                   -> done_today_count

    That divergence is INTENTIONAL and was verified against the rendered SPA
    labels: `done_today_count` is a counter labelled "Done today", so folding
    needs_review into it would make the figure contradict its own caption. This
    test does NOT ask them to agree.

    What was missing is any pin at all. Every existing dashboard assertion runs
    in COLD state with no runners registered, where all four read 0 on any
    status set -- so a change to one consumer's set would have shipped
    silently. The fixture below carries a today-stamped job in every status,
    which is the only state that can tell the sets apart.
    """
    jobs = {
        _URL + "#d": _job("done", date.today().isoformat()),
        _URL + "#f": _job("failed", date.today().isoformat()),
        _URL + "#r": _job("needs_review", date.today().isoformat()),
    }
    cleanup = _register("cut42_sets", jobs)
    try:
        def _check(today_iso):
            snap = _snapshot().get("today") or {}
            assert snap.get("done") == 1 and snap.get("failed") == 1 \
                and snap.get("needs_review") == 1, snap

            b1 = (_get("/api/dashboard").get_json() or {}).get("today") or {}
            assert b1.get("done") == 1 and b1.get("failed") == 1 \
                and b1.get("needs_review") == 1, b1

            v2 = _get("/api/dashboard/v2").get_json() or {}
            t2 = v2.get("today") or {}
            assert t2.get("done") == 1 and t2.get("failed") == 1, t2
            assert "needs_review" not in t2, (
                "v2's today gained needs_review; its SPA label does not claim it")
            assert _by_site(v2, "cut42_sets").get("today_done") == 1, v2

            q = _get("/api/queue/v2").get_json() or {}
            assert q.get("done_today_count") == 1, (
                "done_today_count must count ONLY done -- it is rendered under "
                f"the label 'Done today': {q.get('done_today_count')!r}")
        _guard_midnight(_check)
    finally:
        cleanup()
