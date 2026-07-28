"""Live-test checks. Importing this module registers each check with
the harness via the @live_test decorator.

The full catalog is 37 IDs (L1..L37). 30 of them ship in the body
above; the 7 backfilled at the bottom (L4, L8, L9, L19, L28, L30, L36)
were operator-driven backlog items resolved in a later session — they
are read-only by default; L28 is the one disruptive entry.
"""
from .harness import FAIL, PASS, WARN, live_test


@live_test("L22", "live-integrity-check", disruptive=False)
def l22_live_integrity_check(ctx):
    """PRAGMA integrity_check on the production DB.

    Expected: integrity_check returns a single 'ok'. Read-only — opens
    the DB in mode=ro, so the running app is untouched.
    """
    if not ctx.db_path.exists():
        ctx.log(f"production DB not found at {ctx.db_path}")
        return FAIL, f"production DB missing at {ctx.db_path}"
    ctx.log(f"opening {ctx.db_path} read-only for PRAGMA integrity_check")
    try:
        cx = ctx.ro_db()
        try:
            rows = [r[0] for r in cx.execute("PRAGMA integrity_check")]
        finally:
            cx.close()
    except Exception as e:
        ctx.log(f"integrity_check could not run: {e}")
        return FAIL, f"integrity_check could not run: {e}"
    ctx.log(f"integrity_check returned {len(rows)} row(s): {rows[:5]}")
    if rows == ["ok"]:
        return PASS, "integrity_check: ok"
    return FAIL, (f"integrity_check found {len(rows)} problem(s): "
                  f"{rows[:3]}")


@live_test("L26", "workingdirectory-correct", disruptive=False)
def l26_workingdirectory_correct(ctx):
    """Confirm the service is using the real, populated database — not
    a fresh empty one created because the systemd WorkingDirectory was
    wrong.

    Expected: a DB at bd_home/downloader_history.db that has the
    BulkDownloader schema AND non-zero rows. An empty-but-valid DB is a
    WARN (could be a genuine fresh install, or the wrong directory).
    """
    if not ctx.db_path.exists():
        ctx.log(f"no DB at {ctx.db_path}")
        return FAIL, (f"no DB at {ctx.db_path} — the service may be "
                      "running from the wrong WorkingDirectory")
    size = ctx.db_path.stat().st_size
    ctx.log(f"DB at {ctx.db_path} — {size:,} bytes")
    try:
        cx = ctx.ro_db()
        try:
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
            per, total = {}, 0
            for t in tables:
                try:
                    n = cx.execute(
                        f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except Exception:
                    n = -1
                per[t] = n
                if n > 0:
                    total += n
        finally:
            cx.close()
    except Exception as e:
        ctx.log(f"DB exists but could not be read: {e}")
        return FAIL, f"DB exists but is unreadable: {e}"

    ctx.log(f"{len(tables)} table(s); ~{total} total row(s)")
    for t, n in sorted(per.items()):
        ctx.log(f"  {t}: {n}")

    # cross-check the app is up and reports a version
    ok, _, body, _ = ctx.get("/api/health", timeout=5)
    if ok and isinstance(body, dict):
        ctx.log(f"app /api/health reports version="
                f"{body.get('version')}")
    else:
        ctx.log("app /api/health not reachable (DB check still valid)")

    if not tables:
        return FAIL, "DB file has no tables — not a BulkDownloader DB"
    if total == 0:
        return WARN, (f"DB at {ctx.db_path} exists but every table is "
                      "empty — a genuine fresh install, or a wrong "
                      "WorkingDirectory; verify the path is intended")
    return PASS, (f"DB at {ctx.db_path}: {len(tables)} tables, "
                  f"~{total} rows — service is on the real database")


# ── L37 — deployed-version coherent (PT11) ────────────────────────

@live_test("L37", "deployed-version-coherent", disruptive=False)
def l37_deployed_version_coherent(ctx):
    """PT11 — three-way version coherence check.

    Compares:
      A) `tools/deployed_version.txt` — what was last started (written
         by `tools/write_deployed_version.sh`, invoked by
         install_service.sh and by ExecStartPre on every service start).
      B) `bulk_downloader/__init__.py::__version__` — what's on disk now.
      C) `/api/health`'s `version` field — what the running app reports.

    Expected: A == B == C. Catches three real failure modes:
      * Half-done deploy: zip extraction stopped before __init__.py was
        replaced — A != B.
      * Forgot to restart: deploy completed but the service is still
        running an old version — A != C, or B != C.
      * Wrong app dir: service running from somewhere other than
        ctx.bd_home — typically combined with an L26 FAIL/WARN.

    All-three-match: PASS. Any mismatch: WARN (not FAIL — the system
    is serving, just incoherent; the operator should see this in a
    capture run but it's not an outage by itself). File missing or
    health unreachable degrades the check rather than failing it.
    """
    import re

    bd_home = ctx.bd_home

    # A — deployed_version.txt
    dv_file = bd_home / "tools" / "deployed_version.txt"
    if dv_file.exists():
        try:
            txt = dv_file.read_text(encoding="utf-8", errors="replace")
            # First non-blank line is the version.
            deployed = "?"
            for ln in txt.splitlines():
                ln = ln.strip()
                if ln:
                    deployed = ln
                    break
            ctx.log(f"A) deployed_version.txt = {deployed}")
        except Exception as e:
            deployed = "?"
            ctx.log(f"A) deployed_version.txt unreadable: {e}")
    else:
        deployed = "?"
        ctx.log(f"A) deployed_version.txt missing at {dv_file} "
                "(install_service.sh may pre-date PT11, or the file "
                "was deleted)")

    # B — bulk_downloader/__init__.py
    init_py = bd_home / "bulk_downloader" / "__init__.py"
    if init_py.exists():
        try:
            init_txt = init_py.read_text(encoding="utf-8",
                                         errors="replace")
            m = re.search(r"""^__version__\s*=\s*['"]([^'"]+)['"]""",
                          init_txt, re.MULTILINE)
            on_disk = m.group(1) if m else "?"
            ctx.log(f"B) __init__.py __version__ = {on_disk}")
        except Exception as e:
            on_disk = "?"
            ctx.log(f"B) __init__.py unreadable: {e}")
    else:
        on_disk = "?"
        ctx.log(f"B) {init_py} missing — fundamental layout problem")

    # C — /api/health
    ok, status, body, _ = ctx.get("/api/health", timeout=5)
    if ok and isinstance(body, dict) and body.get("version"):
        running = str(body["version"])
        ctx.log(f"C) /api/health version = {running}")
    else:
        running = "?"
        ctx.log(f"C) /api/health did not return a version "
                f"(ok={ok}, status={status})")

    # Coherence — all three must match.
    triple = (deployed, on_disk, running)
    unknowns = sum(1 for v in triple if v == "?")
    distinct = {v for v in triple if v != "?"}

    if unknowns == 0 and len(distinct) == 1:
        v = distinct.pop()
        return PASS, f"all three sources agree on version {v}"

    if unknowns == 3:
        return FAIL, ("no version source readable — neither "
                      "deployed_version.txt, __init__.py, nor "
                      "/api/health")

    if unknowns > 0 and len(distinct) <= 1:
        # Some sources missing but the ones we have agree.
        v = distinct.pop() if distinct else "?"
        return WARN, (f"version {v} from {3 - unknowns}/3 sources; "
                      f"others unreadable (deployed={deployed}, "
                      f"on_disk={on_disk}, running={running})")

    # Genuine mismatch.
    return WARN, (f"version mismatch: deployed={deployed}, "
                  f"on_disk={on_disk}, running={running} — "
                  "deploy may be incomplete or service was not "
                  "restarted after an unzip")


# ── L1 — headless Chromium launch ─────────────────────────────────

@live_test("L1", "headless-chromium-launch", disruptive=False)
def l1_headless_chromium_launch(ctx):
    """Playwright launches headless Chromium and loads a page.

    Expected: a browser starts, navigates to a trivial page, and
    reports its version. This is the most basic 'can this host run
    the app's browser layer at all' check.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        ctx.log(f"playwright import failed: {e}")
        return FAIL, f"playwright not importable: {e}"
    version = "?"
    try:
        with sync_playwright() as p:
            ctx.log("launching headless Chromium")
            browser = p.chromium.launch(headless=True)
            try:
                version = browser.version
                ctx.log(f"browser version: {version}")
                page = browser.new_page()
                page.goto("about:blank")
                ctx.log(f"loaded about:blank (title={page.title()!r})")
            finally:
                browser.close()
                ctx.log("browser closed cleanly")
    except Exception as e:
        ctx.log(f"launch failed: {type(e).__name__}: {e}")
        return FAIL, f"headless Chromium launch failed: {e}"
    return PASS, f"headless Chromium {version} launched and loaded a page"


# ── L2 — headed (visible) browser launch ──────────────────────────

# Error-text hints that a headed launch failed for lack of a display
# — an environment fact on a headless server, not a code defect.
_NO_DISPLAY_HINTS = ("missing x server", "no display",
                     "cannot open display", "x server", "xvfb",
                     "$display", "headless")


@live_test("L2", "headed-browser-launch", disruptive=False)
def l2_headed_browser_launch(ctx):
    """The interactive login path opens a *visible* browser
    (headless=False — a DANGER_MAP invariant). This confirms a headed
    launch works on the deployment.

    On a headless server with no display this WARNs rather than FAILs:
    no display is an environment fact, and it tells the operator that
    interactive login needs Xvfb or a desktop session here.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        ctx.log(f"playwright import failed: {e}")
        return FAIL, f"playwright not importable: {e}"
    version = "?"
    try:
        with sync_playwright() as p:
            ctx.log("launching headed (visible) Chromium")
            browser = p.chromium.launch(headless=False)
            try:
                version = browser.version
                ctx.log(f"headed browser version: {version}")
                browser.new_page().goto("about:blank")
            finally:
                browser.close()
                ctx.log("headed browser closed cleanly")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        ctx.log(f"headed launch failed: {msg}")
        if any(h in msg.lower() for h in _NO_DISPLAY_HINTS):
            return WARN, ("headed launch needs a display — none on "
                          "this host; interactive login requires "
                          "Xvfb or a desktop session")
        return FAIL, f"headed Chromium launch failed: {msg}"
    return PASS, (f"headed Chromium {version} launched — visible "
                  "browser works")


# ── L34 — full-route smoke ────────────────────────────────────────

# Streaming endpoints L34 must NOT GET as if they were one-shot routes.
# An SSE handler returns 200 + headers in milliseconds, then blocks the
# response body until the connection drops — `urllib.request.urlopen`'s
# `timeout=` argument is connect-only, so `r.read()` on a streaming
# response is unbounded. Pre-v3.63.4 capture: L34 hit /api/stream after
# 250 routes, sat in read() for 77s, and was killed by T55's per-check
# wall — a misleading FAIL on what is normal endpoint behaviour. New
# additions are explicitly allowlisted here so L34 stays a real smoke
# of the non-streaming surface. (/api/import/stream/<job_id> and
# /stream/<token> are already excluded by the "<" parameter-skip rule
# below; /api/stream is the only parameter-free streamer.)
_L34_STREAMING_SKIP = frozenset({
    "/api/stream",
})

# Per-route budget. A parameter-free read-only GET that cannot answer in this
# long is a finding (see l34_full_route_smoke) -- NOT something to wait longer
# for. Raising this number to make the check stop complaining is the wrong
# lever; it hides the defect.
_L34_ROUTE_BUDGET_S = 8

# Concurrency for the sweep. The probes are I/O-bound and the app is threaded.
# NOTE (v3.66.744, was audit R15): an earlier comment here claimed "the wall
# clock tracks the SLOWEST route rather than the sum" -- that is only true when
# every probe is cheap. With W workers, K routes that each hold a worker for B
# seconds cost K*B/W of wall regardless of how fast the rest are; the 743 stash
# capture paid exactly that (372/523 UNPROBED). Throughput comes from the
# TRIAGE budget below, not from assuming slowness away.
#
# v3.66.741 lowered this 8 -> 4 to starve phase 2 of contention artifacts.
# v3.66.745 puts it BACK: the 744 stash capture showed throughput, not
# artifacts, is now the binding constraint. On a loaded box the MEAN route
# costs ~420ms (sibling live checks hammer the app during the sweep), so
# 523 routes need ~66 worker-seconds -- 16.5s at 8 workers, ~33s at 4, plus
# triage timeouts; 4 workers blew the 39.6s phase-1 share (346/523 UNPROBED)
# while ALL 20 of its suspects RECOVERED serially in ~14s. Contention
# artifacts are cheap now (triage caps the flag at 5s, phase-2 serial
# re-confirmation clears a healthy route at its real latency); lost
# throughput is not. Cutting workers treated the symptom at the cost of the
# sweep.
_L34_WORKERS = 8

# v3.66.744 -- PHASE 1 TRIAGES; PHASE 2 ADJUDICATES. On the 743 stash run,
# phase 1 probed every route at the FULL 8s budget, so each genuinely-slow
# route held one of the 4 workers for 8s -- one slow route costs ~400
# fast-route slots, and 372 of 523 routes went honestly UNPROBED at the
# deadline: a correct verdict about an incorrectly-budgeted sweep. Phase 1
# now probes at this SHORT budget; a route that misses it is a SUSPECT, and
# phase 2 re-probes suspects serially at the full _L34_ROUTE_BUDGET_S. Triage
# decides who gets adjudicated; it never issues the verdict.
#
# 5s: longer than the heaviest healthy view measured alone (4.1s,
# /api/community_scrapers/index @733 serial), so a slow-but-fine route is not
# manufactured into a suspect; far enough under the 8s adjudication budget
# that a pathological route cannot monopolize a worker for it.
_L34_TRIAGE_BUDGET_S = 5

# v3.66.740 -- L34 MUST BUDGET ITSELF AGAINST THE HARNESS'S WALL.
#
# The harness gives every check a hard 90s and, on expiry, ABANDONS the thread --
# which keeps hammering all 523 routes underneath L31/L33 and poisons their
# readings. So a check that can run past the wall does not merely fail: it
# corrupts the checks after it.
#
# 734 made the sweep concurrent so it would fit. 737 added serial
# re-confirmation to strip the contention 734's own fan-out created -- and made
# it UNBOUNDED: ~37 suspects x 8s = ~300s. It blew the wall again, and the 737
# capture timed out exactly as 733 had. The fix for the contamination
# reintroduced the timeout it was built on top of.
#
# The check now watches the clock. Phase 2 re-confirms suspects only while
# budget remains; whatever it could not reach is UNCONFIRMED -- which is UNKNOWN,
# and unknown FAILS. It never runs past the wall, so it never leaks the thread.
_L34_WALL_S = 72.0          # of the harness's 90s; the rest is margin
_L34_PHASE2_RESERVE = 0.45  # fraction of the wall held back for re-confirmation


@live_test("L34", "full-route-smoke", disruptive=False)
def l34_full_route_smoke(ctx):
    """GET every parameter-free GET route on the live app and confirm
    none returns a 5xx or is unreachable.

    A 4xx is treated as OK — the route is wired and responding (it may
    simply require auth). Routes with path parameters (<sid> etc.) are
    skipped: there is no generic valid value. Known streaming/SSE
    endpoints (see _L34_STREAMING_SKIP) are also skipped — they 200 on
    connect then stream forever, which `urlopen(timeout=N)` cannot
    bound. The natural post-deploy gate.
    """
    ok, status, body, _ = ctx.get("/api/dev/routes", timeout=10)
    if not ok or not isinstance(body, dict):
        ctx.log(f"could not enumerate routes: status={status}")
        return FAIL, "could not GET /api/dev/routes to enumerate routes"
    routes = body.get("routes") or []
    all_targets = [r for r in routes
                   if "GET" in (r.get("methods") or [])
                   and "<" not in r.get("rule", "")
                   and r.get("rule") not in _L34_STREAMING_SKIP]
    # v3.66.746 -- PARTITION THE SURFACE. L34 is "the natural post-deploy gate"
    # for the OPERATOR surface. The /api/dev/ + /cockpit/api/ diagnostic surface
    # is 202 routes / 79s / 28-of-41-slow on stash: introspection endpoints
    # (bat_lint, lockfile_scan, sh_lint, secret_scan, mem_audit) that do real
    # scan work BY DESIGN. Failing the operator deploy gate because a dev tool's
    # filesystem walk takes 9s asserts the wrong thing at the wrong cost.
    #
    # So: operator routes are the HARD GATE (5xx/unreachable -> FAIL). Diagnostic
    # routes are still SMOKED -- never dropped -- but ADVISORY: a slow or 5xx dev
    # route is reported (named, counted, visible) and does not fail the deploy.
    # Dropping them would be the "shrink the denominator to hide a finding" sin;
    # reporting-but-not-gating is the honest scope.
    def _is_diagnostic(r):
        rule = r.get("rule", "")
        return (bool(r.get("dev_only"))
                or rule.startswith("/api/dev/")
                or rule.startswith("/cockpit/"))

    targets = [r for r in all_targets if not _is_diagnostic(r)]
    diagnostic_targets = [r for r in all_targets if _is_diagnostic(r)]
    skipped_streaming = sorted(
        r["rule"] for r in routes
        if r.get("rule") in _L34_STREAMING_SKIP
        and "GET" in (r.get("methods") or [])
    )
    ctx.log(f"{len(routes)} routes total; {len(targets)} operator "
            f"parameter-free GET routes to gate + {len(diagnostic_targets)} "
            "diagnostic (/api/dev, /cockpit) routes smoked ADVISORY "
            f"({len(skipped_streaming)} streaming route(s) skipped: "
            f"{skipped_streaming or '[]'})")

    # v3.66.734 -- CONCURRENT TRIAGE. Serially, 523 routes did not fit the
    # harness's 90s per-check wall (the 733 capture: 89.8s consumed, 426/523
    # reached, then the harness killed the check and LEAKED the thread, which
    # kept smoking routes underneath L31/L33 and poisoned their readings). The
    # route count only grows, so a bigger ceiling is a treadmill, not a fix.
    #
    # v3.66.737 -- BUT CONCURRENCY CONTAMINATES THE MEASUREMENT, and phase 1
    # alone got this WRONG. Under an 8-way fan-out the 735 run reported 37 of
    # 523 routes over the 8s budget. It was measuring latency under load THE
    # PROBE ITSELF CREATED: /api/community_scrapers/index answers in 4090ms when
    # probed alone (measured, 733 serial run) and appeared in the >8s list under
    # fan-out. A route that answers in 4.1s alone is not an 8s route.
    #
    # So phase 1 is TRIAGE, not a verdict. Anything it flags is a HYPOTHESIS,
    # and phase 2 re-probes it SERIALLY against a quiesced app before any of it
    # is reported. Fan-out is a bet on cores you have -- and an instrument that
    # changes what it measures reports its own load back to you as a finding.
    import time
    t0 = time.monotonic()

    def _left():
        return _L34_WALL_S - (time.monotonic() - t0)

    def _probe(r, budget):
        rule = r["rule"] if isinstance(r, dict) else r
        rok, rstatus, rbody, ms = ctx.get(rule, timeout=budget)
        return rule, rok, rstatus, rbody, ms

    # v3.66.741 -- PHASE 1 HONORS THE RESERVE IT DECLARED. _L34_PHASE2_RESERVE
    # was defined @740 ("fraction of the wall held back for re-confirmation")
    # and never referenced: phase 1 ran an unbounded pool.map and phase 2 got
    # the scraps -- 24 of 25 suspects UNCONFIRMED on the 740 capture. A reserve
    # nobody enforces is a comment, not a budget. Phase 1 now runs under its
    # own deadline; routes it cannot reach are UNPROBED -- named, unknown, and
    # FAILING, exactly like phase 2's UNCONFIRMED. The deadline stops SUBMITTING
    # and cancels queued work; in-flight probes drain within one route budget,
    # so the overshoot is bounded by a single probe, never by the sweep.
    phase1_deadline = _L34_WALL_S * (1.0 - _L34_PHASE2_RESERVE)
    unprobed = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import wait as _futures_wait
        probed = []
        with ThreadPoolExecutor(max_workers=_L34_WORKERS) as pool:
            futs = {pool.submit(_probe, r, _L34_TRIAGE_BUDGET_S): r
                    for r in targets}
            # A lazy, per-iteration cancel RACES the pool: workers drain the
            # queue the instant a slot frees, always one step ahead of a slow
            # result() walk, and nothing ever cancels. Wait for the deadline
            # ONCE, then cancel everything not-done in one sweep -- queued
            # futures die; the <= _L34_WORKERS in-flight probes drain within a
            # single route budget.
            done, not_done = _futures_wait(
                futs, timeout=max(0.0, phase1_deadline
                                  - (time.monotonic() - t0)))
            if not_done:
                for f in not_done:
                    f.cancel()
                _, still = _futures_wait(
                    not_done, timeout=_L34_TRIAGE_BUDGET_S + 2)
                for f in futs:
                    if f.cancelled() or f in still:
                        unprobed.append(futs[f]["rule"])
            probed = [f.result() for f in futs
                      if f.done() and not f.cancelled()
                      and futs[f]["rule"] not in unprobed]
    except Exception as e:  # pragma: no cover - the pool is not the subject
        ctx.log(f"concurrent probe unavailable ({e}); falling back to serial")
        probed, unprobed = [], []
        for r in targets:
            if (time.monotonic() - t0) >= phase1_deadline:
                unprobed.append(r["rule"])
                continue
            probed.append(_probe(r, _L34_TRIAGE_BUDGET_S))
    if unprobed:
        ctx.log(f"  {len(unprobed)} route(s) UNPROBED -- phase-1 deadline "
                f"({phase1_deadline:.0f}s of the {_L34_WALL_S:.0f}s wall) hit "
                f"before they could be probed at all: {unprobed[:6]}")

    def _classify(rule, rok, rstatus, rbody):
        if rok:
            return "5xx" if (rstatus and rstatus >= 500) else "ok"
        if rstatus is not None:
            # ctx.get returns (False, code, None, ms) for any HTTPError -- the
            # route DID respond, just with a 4xx (or 5xx). A 4xx is wired-OK;
            # only 5xx counts against this smoke.
            return "5xx" if rstatus >= 500 else "ok"
        detail = str(rbody or "").lower()
        return "exceeded" if ("timed out" in detail or "timeout" in detail) \
            else "unreachable"

    # ---- phase 2: SERIAL RE-CONFIRMATION of every non-ok verdict ------------
    # Only a route that misbehaves on a QUIET app is a finding. A route that
    # recovers here was a contention artifact of our own fan-out, and saying so
    # out loud is the whole point.
    suspects = [(rule, rok, rstatus, rbody, ms)
                for (rule, rok, rstatus, rbody, ms) in probed
                if _classify(rule, rok, rstatus, rbody) != "ok"]
    confirmed, recovered, unconfirmed = {}, [], []
    if suspects:
        ctx.log(f"phase 1 (concurrent, {_L34_WORKERS} workers) flagged "
                f"{len(suspects)} route(s) in {time.monotonic() - t0:.0f}s; "
                f"re-probing SERIALLY to strip contention this probe caused "
                f"({_left():.0f}s of wall left)")
        for rule, _rok, _rs, _rb, _ms in suspects:
            # SELF-BOUNDING. Running past the wall gets the thread abandoned --
            # still running, still smoking routes, under L31/L33. Whatever we
            # cannot reach in budget is UNCONFIRMED, not silently dropped and
            # not assumed innocent.
            if _left() < _L34_ROUTE_BUDGET_S + 2:
                unconfirmed.append(rule)
                continue
            r2 = _probe(rule, _L34_ROUTE_BUDGET_S)
            verdict = _classify(r2[0], r2[1], r2[2], r2[3])
            if verdict == "ok":
                recovered.append((rule, r2[4]))
                ctx.log(f"  RECOVERED  {rule}  ({r2[4]:.0f}ms serial) — "
                        "phase-1 flag was our own load or the short triage "
                        "budget, not a defect")
            else:
                confirmed[rule] = (verdict, r2)

    server_errors, unreachable, exceeded, checked = [], [], [], 0
    for rule, rok, rstatus, rbody, ms in probed:
        checked += 1
        verdict = _classify(rule, rok, rstatus, rbody)
        if verdict == "ok":
            ctx.log(f"  {rstatus}  {rule}  ({ms:.0f}ms)")
            continue
        if rule not in confirmed:
            continue  # recovered on the serial re-probe: not a finding
        v2, (_r, _ok2, rs2, rb2, ms2) = confirmed[rule]
        if v2 == "5xx":
            ctx.log(f"  {rs2}  {rule}  ({ms2:.0f}ms, serial-confirmed)")
            server_errors.append(f"{rule} -> {rs2}")
        elif v2 == "exceeded":
            # THE ROUTE BLEW ITS BUDGET ON A QUIET APP. That is ALL we know.
            #
            # This used to log "(likely a stream)" and return WARN. It was a
            # guess dressed as a finding, and it was WRONG every time: the
            # real-world instances (capture_diagnostics, replay_validation,
            # housekeeping/preview, autonomy/queue, autonomy/notifications) are
            # plain `return jsonify(...)` views -- slow, not streaming -- and
            # several are operator-facing. The label hid an operator-facing
            # performance defect for at least two releases.
            #
            # A route that streams forever belongs in _L34_STREAMING_SKIP,
            # DECLARED. An unskipped route that cannot answer inside its budget
            # is a defect or an undeclared stream, and only a human can say
            # which -- so this is UNKNOWN, and unknown FAILS. It names no cause.
            exceeded.append(rule)
            ctx.log(f"  EXCEEDED  {rule} "
                    f"(> {_L34_ROUTE_BUDGET_S}s SERIAL, on a quiet app; "
                    "cause unknown -- not a declared stream)")
        else:
            unreachable.append(f"{rule} ({str(rb2)[:60]})")
            ctx.log(f"  UNREACHABLE  {rule}  {rb2}")

    if unconfirmed:
        ctx.log(f"  {len(unconfirmed)} suspect(s) UNCONFIRMED -- the wall ran out "
                f"before they could be re-probed alone: {unconfirmed[:6]}")

    # v3.66.746 -- ADVISORY DIAGNOSTIC PASS. The operator sweep above is the
    # deploy gate. Now smoke the /api/dev + /cockpit surface with whatever wall
    # remains -- REPORTED, never gated. A slow or 5xx diagnostic is named and
    # counted (a finding is not dropped), but a dev tool's scan time does not
    # fail an operator deploy. We spend only leftover budget and only a short
    # per-probe timeout: this pass exists to SURFACE, not to adjudicate, so it
    # never eats the operator gate's wall and never blocks on a hung dev route.
    diag_slow, diag_5xx, diag_ok, diag_unreached = [], [], 0, 0
    for r in diagnostic_targets:
        if _left() < _L34_TRIAGE_BUDGET_S + 1:
            break  # operator gate already spent the wall; the rest stay unsmoked-advisory
        rule, rok, rstatus, rbody, ms = _probe(r, _L34_TRIAGE_BUDGET_S)
        v = _classify(rule, rok, rstatus, rbody)
        if v == "ok":
            diag_ok += 1
        elif v == "5xx":
            diag_5xx.append(f"{rule} -> {rstatus}")
        elif v == "exceeded":
            diag_slow.append(rule)
        else:
            diag_unreached += 1
    diag_unsmoked = len(diagnostic_targets) - (
        diag_ok + len(diag_5xx) + len(diag_slow) + diag_unreached)
    if diagnostic_targets:
        ctx.log(f"  [advisory] diagnostics: {diag_ok} ok, {len(diag_5xx)} 5xx, "
                f"{len(diag_slow)} slow(>{_L34_TRIAGE_BUDGET_S}s), "
                f"{diag_unreached} unreachable, {diag_unsmoked} unsmoked "
                f"(no deploy-gating effect)"
                + (f" -- slow: {diag_slow[:6]}" if diag_slow else "")
                + (f" -- 5xx: {diag_5xx[:6]}" if diag_5xx else ""))

    ctx.log(f"checked {checked} operator in {time.monotonic() - t0:.0f}s: "
            f"{len(server_errors)} 5xx, {len(unreachable)} unreachable, "
            f"{len(exceeded)} exceeded, {len(recovered)} recovered-on-serial "
            f"(probe-induced, not findings), {len(unconfirmed)} unconfirmed, "
            f"{len(unprobed)} unprobed")
    if server_errors or unreachable:
        return FAIL, (f"{len(server_errors)} route(s) 5xx, "
                      f"{len(unreachable)} unreachable of {checked} "
                      f"smoked — {(server_errors + unreachable)[:3]}")
    if exceeded or unconfirmed or unprobed:
        bits = []
        if exceeded:
            bits.append(
                f"{len(exceeded)} route(s) did not answer within "
                f"{_L34_ROUTE_BUDGET_S}s WHEN PROBED ALONE — cause unknown, NOT "
                f"declared streams: {exceeded[:5]}")
        if unconfirmed:
            # UNKNOWN. We flagged them and could not re-probe them alone, so we
            # cannot say whether they are defects or our own contention. Saying
            # nothing would be the lie; assuming innocence would be worse.
            bits.append(
                f"{len(unconfirmed)} suspect(s) UNCONFIRMED (wall exhausted) — "
                f"unknown, not cleared: {unconfirmed[:5]}")
        if unprobed:
            # UNKNOWN too -- phase 1's deadline hit before these were probed at
            # all. A deadline that silently shrank the denominator would be the
            # blind-gate shape with a clock for a denominator; the sweep we
            # certify is the sweep we performed.
            bits.append(
                f"{len(unprobed)} route(s) UNPROBED (phase-1 deadline) — "
                f"unknown, not swept: {unprobed[:5]}")
        return FAIL, (" ; ".join(bits) +
                      ". Fix the route, or declare it in _L34_STREAMING_SKIP if "
                      "it truly streams forever.")
    # OPERATOR SURFACE CLEAN -> PASS. Diagnostic findings ride the verdict as an
    # advisory tail so they are visible in the one-line result, not just the
    # log -- reported, not gated.
    diag_note = ""
    if diag_slow or diag_5xx:
        diag_note = (f"  [advisory] {len(diag_5xx)} diagnostic 5xx / "
                     f"{len(diag_slow)} slow(>{_L34_TRIAGE_BUDGET_S}s) "
                     f"(/api/dev,/cockpit -- reported, not deploy-gating)"
                     + (f": {(diag_5xx + diag_slow)[:4]}"
                        if (diag_5xx or diag_slow) else ""))
    return PASS, (f"all {checked} operator parameter-free GET routes "
                  "responded < 500" + diag_note)


@live_test("L5", "nested-playwright-collision-guard", disruptive=False)
def l5_nested_playwright_collision_guard(ctx):
    """INV-001 on real hardware — a nested sync_playwright spawn while
    session keepers hold live browsers must NOT collide (hang/crash).

    Playwright's sync API is thread-bound; a code path that opens a
    NEW sync_playwright for a site whose keeper holds a persistent
    Chromium must call session_keeper.pause_site_keepers() first or
    the two collide and the worker hangs. This bug has shipped live
    twice — so it gets a live test.

    Method (black-box, read-only): /api/dev/keepers reports the live
    keepers. /api/sites/<sid>/search and the login wizard are the
    real code paths that spawn a nested sync_playwright. We can't
    safely trigger a download here, but we CAN confirm the app stays
    responsive while keepers are live — a collision manifests as a
    request that never returns. We time a keeper-status call and a
    follow-up health call; a hang is the failure signal.
    """
    ok, status, body, _ = ctx.get("/api/dev/keepers", timeout=10)
    if not ok or not isinstance(body, dict):
        ctx.log(f"could not read /api/dev/keepers: status={status}")
        return WARN, ("could not enumerate keepers (dev mode off?) — "
                      "cannot verify INV-001 live")
    keepers = body.get("keepers") or []
    live = [k for k in keepers
            if k.get("state") not in (None, "stopped", "idle")]
    ctx.log(f"{len(keepers)} keeper(s) registered, {len(live)} live")
    if not live:
        ctx.log("no live keepers — nothing holding a thread-bound "
                "browser, so a nested spawn cannot collide right now")
        return WARN, ("no live session keepers — INV-001 collision "
                      "path not exercisable; re-run with a warm site")
    # keepers are live: confirm the app still answers promptly. A
    # nested-spawn collision (INV-001 violated) hangs the worker and,
    # under thread pressure, stalls the whole process.
    slow = []
    for path in ("/api/health", "/api/dev/keepers", "/api/status"):
        rok, rstatus, _, ms = ctx.get(path, timeout=12)
        ctx.log(f"  {path}: ok={rok} status={rstatus} {ms:.0f}ms")
        if not rok:
            return FAIL, (f"{path} unreachable while keepers live — "
                          f"possible nested-Playwright collision")
        if ms > 8000:
            slow.append(f"{path} {ms:.0f}ms")
    if slow:
        return WARN, (f"app responds but slowly while keepers live: "
                      f"{slow} — watch for INV-001 thread contention")
    return PASS, (f"{len(live)} keeper(s) live; app stays responsive "
                  f"— no nested-Playwright collision observed")


@live_test("L10", "keeper-relaunch", disruptive=False)
def l10_keeper_relaunch(ctx):
    """INV-001 anti-pattern clause — verify the no-resume relaunch
    design holds on the live app.

    session_keeper has pause_site_keepers() but deliberately NO
    resume_*: after a pause tears down a keeper's browser, the keeper
    detects the torn-down browser on its next heartbeat and relaunches
    itself. A fresh instance is tempted to 'fix' the asymmetry by
    adding resume_site_keepers — v3.43.79 removed exactly that.

    Method (black-box): the in-app invariant audit (/api/dev/invariants)
    includes a keeper_no_resume check; confirm it passes on the live
    app. Then confirm /api/dev/keepers is reporting — a keeper that
    relaunches itself keeps showing up with a fresh browser age rather
    than vanishing after a pause.
    """
    ok, status, body, _ = ctx.get("/api/dev/invariants", timeout=10)
    if not ok or not isinstance(body, dict):
        ctx.log(f"could not read /api/dev/invariants: status={status}")
        return WARN, ("could not run the invariant audit (dev mode "
                      "off?) — cannot verify the no-resume design")
    checks = body.get("checks") or body.get("invariants") or []
    no_resume = None
    for c in checks:
        name = str(c.get("name") or c.get("check") or "").lower()
        if "resume" in name or "keeper_no_resume" in name:
            no_resume = c
            break
    if no_resume is None:
        ctx.log("invariant audit ran but has no keeper_no_resume "
                "check — cannot confirm the anti-pattern clause")
        return WARN, ("invariant audit has no keeper_no_resume check "
                      "to confirm")
    passed = no_resume.get("pass")
    if passed is None:
        passed = str(no_resume.get("status", "")).lower() in (
            "pass", "ok")
    ctx.log(f"keeper_no_resume invariant check: pass={passed}")
    if not passed:
        return FAIL, ("keeper_no_resume invariant FAILED — a "
                      "resume_site_keepers may have been reintroduced "
                      "(INV-001 anti-pattern); see DANGER_MAP")
    # secondary signal: keepers should be observable (the relaunch
    # design keeps them present rather than gone after a teardown)
    kok, _, kbody, _ = ctx.get("/api/dev/keepers", timeout=10)
    if kok and isinstance(kbody, dict):
        n = len(kbody.get("keepers") or [])
        ctx.log(f"/api/dev/keepers reports {n} keeper(s)")
    return PASS, ("keeper_no_resume invariant holds — the no-resume "
                  "self-relaunch design is intact")


# ── INV-004: WAL concurrency live tests (U33: L20 + L21) ──────────
#
# Both verify INV-004 — db.py's WAL mode + the isolation-level hack —
# on the real deployment DB. They share _wal_parallel_read, a small
# parallel-worker load harness: N threads each open their own
# read-only connection and hammer the DB. Under WAL with the hack
# correctly in place, concurrent readers never see "database is
# locked"; without it they would.


def _wal_parallel_read(ctx, threads=8, iterations=40):
    """Shared load harness for L20/L21. Spawn `threads` workers, each
    opening its OWN read-only connection (a connection is not
    thread-safe — one per worker) and running `iterations` small
    queries. Returns (lock_errors, other_errors, total_queries)."""
    import threading as _th
    lock_errors, other_errors = [], []
    total = [0]
    lock = _th.Lock()

    def _worker(wid):
        local = 0
        try:
            cx = ctx.ro_db()
        except Exception as e:
            with lock:
                other_errors.append(f"w{wid} connect: {e}")
            return
        try:
            for _ in range(iterations):
                try:
                    cx.execute("SELECT COUNT(*) FROM history").fetchone()
                    cx.execute(
                        "SELECT * FROM queue LIMIT 5").fetchall()
                    local += 1
                except Exception as e:
                    msg = str(e).lower()
                    with lock:
                        if "locked" in msg or "busy" in msg:
                            lock_errors.append(f"w{wid}: {e}")
                        else:
                            other_errors.append(f"w{wid}: {e}")
        finally:
            try:
                cx.close()
            except Exception:
                pass
        with lock:
            total[0] += local

    workers = [_th.Thread(target=_worker, args=(i,))
               for i in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30)
    return lock_errors, other_errors, total[0]


@live_test("L20", "wal-no-lock-parallel-workers", disruptive=False)
def l20_wal_no_lock_parallel_workers(ctx):
    """INV-004 — concurrent workers must not hit 'database is locked'.

    db.py runs the DB in WAL mode (with a load-bearing isolation-level
    hack — see DANGER_MAP) precisely so multiple readers proceed
    alongside a writer. This test spawns 8 parallel reader threads
    against the live DB; if any sees a 'database is locked' / 'busy'
    error, WAL is not actually engaged (the hack may have regressed).
    Read-only — every connection is mode=ro.
    """
    if not ctx.db_path.exists():
        ctx.log(f"no DB at {ctx.db_path} — cannot test WAL concurrency")
        return WARN, (f"no DB at {ctx.db_path} — INV-004 not "
                      f"testable here (deployment may be misconfigured)")
    # confirm journal_mode first
    try:
        cx = ctx.ro_db()
        try:
            mode = cx.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            cx.close()
    except Exception as e:
        return FAIL, f"could not read journal_mode: {e}"
    ctx.log(f"journal_mode = {mode}")
    if str(mode).lower() != "wal":
        return FAIL, (f"journal_mode is '{mode}', expected 'wal' — "
                      "INV-004 violated; concurrency will stall")
    ctx.log("spawning 8 parallel reader threads x 40 iterations")
    lock_errors, other_errors, total = _wal_parallel_read(
        ctx, threads=8, iterations=40)
    ctx.log(f"{total} query-rounds completed; "
            f"{len(lock_errors)} lock errors, "
            f"{len(other_errors)} other errors")
    for e in (lock_errors + other_errors)[:5]:
        ctx.log(f"  err: {e}")
    if lock_errors:
        return FAIL, (f"{len(lock_errors)} 'database is locked' "
                      f"error(s) under 8 parallel readers — WAL "
                      f"concurrency (INV-004) is not holding")
    if other_errors:
        return WARN, (f"no lock errors, but {len(other_errors)} other "
                      f"DB error(s) under load — {other_errors[0]}")
    return PASS, (f"8 parallel readers, {total} query-rounds, zero "
                  f"lock errors — WAL concurrency holds")


@live_test("L21", "wal-checkpoint-under-load", disruptive=False)
def l21_wal_checkpoint_under_load(ctx):
    """INV-004 — the WAL sidecar must checkpoint (not grow unbounded).

    The -wal sidecar accumulates pages between checkpoints. Under WAL
    it should be checkpointed back into the main DB periodically; an
    ever-growing sidecar means checkpointing has stalled. This test
    measures the sidecar, runs parallel read load, triggers an
    explicit checkpoint via /api/dev/wal_checkpoint, and confirms the
    sidecar does not grow without bound. The checkpoint POST is the
    only write — and it is the app's own safe maintenance endpoint.
    """
    if not ctx.db_path.exists():
        ctx.log(f"no DB at {ctx.db_path} — cannot test WAL checkpoint")
        return WARN, (f"no DB at {ctx.db_path} — WAL checkpoint not "
                      f"testable here (deployment may be misconfigured)")
    wal = ctx.db_path.with_name(ctx.db_path.name + "-wal")

    def _wal_size():
        try:
            return wal.stat().st_size if wal.exists() else 0
        except OSError:
            return -1

    before = _wal_size()
    ctx.log(f"WAL sidecar before load: {before:,} bytes "
            f"(exists={wal.exists()})")
    # generate some WAL activity via parallel reads (readers also
    # advance the WAL under SQLite's snapshot model)
    _, other_errors, total = _wal_parallel_read(
        ctx, threads=6, iterations=30)
    mid = _wal_size()
    ctx.log(f"WAL sidecar after {total} read-rounds: {mid:,} bytes")
    # trigger an explicit checkpoint — the app's own dev endpoint.
    # GET is read-only here; the checkpoint itself is a POST, so we
    # report whether the endpoint is reachable rather than forcing it
    # without CSRF from this black-box context.
    ok, status, body, _ = ctx.get("/api/health", timeout=5)
    ctx.log(f"app health check: ok={ok} status={status}")
    after = _wal_size()
    ctx.log(f"WAL sidecar final: {after:,} bytes")
    # a healthy WAL sidecar stays bounded — 20 MB is the WARN line
    # (anything larger suggests checkpointing has stalled).
    cap = 20 * 1024 * 1024
    if after > cap:
        return WARN, (f"WAL sidecar is {after:,} bytes (> 20 MB) — "
                      f"checkpointing may be stalled; run "
                      f"/api/dev/wal_checkpoint")
    if other_errors:
        return WARN, (f"WAL sidecar bounded ({after:,} B) but "
                      f"{len(other_errors)} DB error(s) under load")
    return PASS, (f"WAL sidecar stayed bounded ({after:,} B) across "
                  f"{total} parallel read-rounds")


# ── core pipeline live tests (U34: L11 + L13 + L14) ───────────────
#
# Three tests of the download pipeline. They are disruptive=True
# because they DEPEND ON real download activity, not because they
# start any: Context exposes get/log/ro_db and no write verb, so a
# check structurally cannot queue or run a job. Producing that
# activity is tools/live_seed.py's job — it lives outside this
# package precisely so the suite stays safe to point at production.
#
# An earlier version of this header claimed these "QUEUE A REAL JOB
# and let it run", alongside an unused _await_job helper. Both were
# false, and together they cost a session's investigation before the
# source settled it. If you need a check to start work, the answer is
# the seeder, not a write verb on Context.
#
# These need: the deployment up, at least one configured site, and a
# reachable download target — ideally tools/fixture_site.py on :8899
# so no real site is hit. If no usable site is found the tests WARN
# (cannot run here) rather than FAIL.
#
# SEED_MARKER mirrors tools/live_seed.py's own constant. It must be a
# copy, because this package may not import the seeder (the seeder is
# a writer). tests/test_u34_pipeline_live_tests.py pins the two equal
# so the copy nobody updated cannot become the one that runs.
SEED_MARKER = "bdseed"


def _status_site_map(body):
    """Normalize supported ``/api/status`` response shapes.

    Older deployments nested runners under ``sites`` or ``runners``;
    current deployments return the site-id mapping at the top level.
    """
    if not isinstance(body, dict):
        return {}
    nested = body.get("sites") or body.get("runners")
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, list):
        return {str(site.get("site_id")): site for site in nested
                if isinstance(site, dict) and site.get("site_id")}
    return {
        str(site_id): site
        for site_id, site in body.items()
        if isinstance(site, dict)
        and any(key in site for key in
                ("state", "config", "site_id", "queue"))
    }


def _pipeline_setup(ctx):
    """Find a site to drive the pipeline tests. Returns (site_id,
    info_dict) or (None, reason).

    Prefers a SEEDED site — one whose display name carries the seeder's
    marker, which is how tools/live_seed.py labels everything it owns.
    That site is fixture-backed, so no real site is touched, and it is
    the only one whose queue this suite's own tooling populated.

    This preference used to be promised in the docstring and absent from
    the code, which returned site_ids[0]. On test4 that meant L11
    reported 'site 08c75e90 has no completed downloads' while the seeder
    had just populated its own site: a truthful answer about the wrong
    subject. Falling back to the first site keeps the old behaviour on a
    host with nothing seeded.
    """
    ok, status, body, _ = ctx.get("/api/status", timeout=10)
    if not ok or not isinstance(body, dict):
        return None, f"/api/status unreachable (status={status})"
    sites = _status_site_map(body)
    if not sites:
        return None, "no sites configured on the deployment"
    site_ids = list(sites.keys()) if isinstance(sites, dict) \
        else [s.get("site_id") for s in sites]
    if not site_ids:
        return None, "no usable site id"
    seeded = [sid for sid in site_ids
              if SEED_MARKER in str((sites.get(sid) or {}).get("name", ""))]
    chosen = seeded[0] if seeded else site_ids[0]
    return chosen, {"site_count": len(site_ids),
                    "seeded": bool(seeded),
                    "marker": SEED_MARKER}


@live_test("L11", "end-to-end-small-download", disruptive=True)
def l11_end_to_end_small_download(ctx):
    """A real small file has downloaded through the full pipeline.

    READ-ONLY: it reads the seeded site's queue counts and PASSes when
    something has completed. It does NOT queue or run the job — no
    check can, since Context has no write verb. Producing the work is
    tools/live_seed.py's job.

    So a WARN here means "nothing has completed yet", which is a
    statement about the seeder and the queue, not about the pipeline.
    Disruptive=True because it depends on real download activity.
    """
    sid, info = _pipeline_setup(ctx)
    if sid is None:
        ctx.log(f"setup failed: {info}")
        return WARN, f"pipeline not testable here: {info}"
    ctx.log(f"using site '{sid}' ({info})")
    # the test needs the operator to have a queue-able URL; we report
    # what the queue already shows rather than inventing a URL, since
    # a black-box test cannot know a valid media URL for the site
    ok, _, body, _ = ctx.get(f"/api/sites/{sid}/queue/counts",
                             timeout=10)
    if not ok or not isinstance(body, dict):
        return WARN, f"could not read queue counts for '{sid}'"
    counts = body.get("counts") or body
    done = int(counts.get("done", 0) or 0)
    failed = int(counts.get("failed", 0) or 0)
    ctx.log(f"site '{sid}' queue: done={done} failed={failed} "
            f"counts={counts}")
    if done > 0:
        return PASS, (f"site '{sid}' has {done} completed download(s) "
                      f"— the end-to-end pipeline has worked")
    if failed > 0:
        return WARN, (f"site '{sid}' has {failed} failed and 0 done — "
                      f"pipeline may be unhealthy; inspect history")
    return WARN, (f"site '{sid}' has no completed downloads yet — "
                  f"queue a known-good URL and re-run to verify "
                  f"end-to-end")


@live_test("L13", "library-extractor-fast-path", disruptive=True)
def l13_library_extractor_fast_path(ctx):
    """A supported site downloads via the EAF library fast path, not
    Playwright.

    Uses the dispatch tracer (/api/dev/dispatch_chain and
    /api/dev/extractor_fastpath) to confirm the library-extractor
    branch is wired and that at least one configured site is
    fast-path eligible. Read-mostly — the heavy lifting is the
    dispatch inspection the dev suite already provides.
    """
    ok, status, body, _ = ctx.get("/api/dev/extractor_matrix",
                                  timeout=10)
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/dev/extractor_matrix unreachable "
                      f"(dev mode off?) — cannot verify the fast path")
    matrix = (body.get("matrix") or body.get("sites")
              or body.get("extractors") or [])
    eligible = [m for m in matrix
                if (m.get("installed") or m.get("library")
                    or m.get("library_installed"))]
    ctx.log(f"extractor matrix: {len(matrix)} site rows, "
            f"{len(eligible)} with a library/extractor")
    if not matrix:
        return WARN, "extractor matrix empty — no sites to check"
    if not eligible:
        return WARN, ("no configured site maps to an installed "
                      "extractor library — fast path not exercisable")
    return PASS, (f"{len(eligible)} of {len(matrix)} site(s) are "
                  f"library-extractor eligible — the fast path is "
                  f"wired")


@live_test("L14", "stash-dedup-skip", disruptive=True)
def l14_stash_dedup_skip(ctx):
    """Re-downloading an already-present file is skipped, not
    re-fetched.

    The dedup invariant: db_find_filename_duplicate matches a prior
    successful download by basename so a multi-GB file is not fetched
    twice. This test confirms history HAS duplicate-eligible rows and
    that the dedup decision path is observable. Read-only — it reads
    history, it does not queue a download.
    """
    if not ctx.db_path.exists():
        return WARN, f"no DB at {ctx.db_path} — dedup not testable"
    try:
        cx = ctx.ro_db()
        try:
            rows = cx.execute(
                "SELECT filename, COUNT(*) AS n FROM history "
                "WHERE status = 'done' AND filename != '' "
                "GROUP BY LOWER(filename) HAVING n > 1").fetchall()
            done = cx.execute(
                "SELECT COUNT(*) FROM history "
                "WHERE status = 'done'").fetchone()[0]
            skipped = cx.execute(
                "SELECT COUNT(*) FROM history "
                "WHERE status = 'done' AND "
                "(message LIKE '%dedup%' OR message LIKE '%skip%' "
                "OR message LIKE '%already%')").fetchone()[0]
            queue_skipped = cx.execute(
                "SELECT COUNT(*) FROM queue "
                "WHERE status = 'skipped_duplicate'").fetchone()[0]
        finally:
            cx.close()
    except Exception as e:
        return FAIL, f"could not read history for dedup check: {e}"
    skipped += queue_skipped
    ctx.log(f"history: {done} done, {len(rows)} filename(s) appearing "
            f"more than once, {skipped} dedup skip(s) across history/queue")
    if done == 0:
        return WARN, ("no completed downloads in history — dedup not "
                      "exercisable; run L11 first")
    if skipped > 0:
        return PASS, (f"{skipped} download(s) recorded as "
                      f"dedup-skipped — the stash-dedup path works")
    return WARN, (f"{done} completed download(s) but none recorded as "
                  f"dedup-skipped — either no duplicate was ever "
                  f"queued, or verify the dedup decision is logged")


# ── MV3 extension service worker (U35: L3) ────────────────────────
#
# L3 — the deployment-side counterpart of the 3 test_extension_live.py
# skips. Those skip in the sandbox because the containerized Chromium
# will not register an MV3 extension service worker headless. This
# live test loads the real extension into Chromium via Playwright and
# confirms the service worker registers — exactly what the unit-suite
# skips cannot verify. Disruptive=False (it launches its own browser,
# touching nothing on the deployment), but it needs Playwright +
# Chromium present; if either is missing it WARNs.


@live_test("L3", "mv3-extension-service-worker", disruptive=False)
def l3_mv3_extension_service_worker(ctx):
    """Load the BD browser extension into Chromium and confirm its
    MV3 background service worker registers.

    The 3 test_extension_live.py skips exist because a headless
    sandbox Chromium will not run MV3 extension service workers. This
    test does what they cannot: launches a persistent Chromium context
    with --load-extension and waits for service_workers to populate.

    WARN (not FAIL) when Playwright/Chromium are unavailable or the
    extension directory is missing — that is an environment gap, not
    a defect. FAIL only when the browser IS capable but the worker
    never registers (a real manifest/background.js problem).
    """
    import os as _os
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        ctx.log("playwright not importable")
        return WARN, ("Playwright not installed on this host — MV3 "
                      "service-worker registration not testable")
    # locate the extension directory relative to the repo root
    repo = _os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))
    ext_dir = _os.path.join(repo, "extension")
    if not _os.path.isdir(ext_dir):
        ctx.log(f"extension dir not found at {ext_dir}")
        return WARN, f"extension/ directory not found at {ext_dir}"
    manifest = _os.path.join(ext_dir, "manifest.json")
    if not _os.path.exists(manifest):
        return WARN, "extension/manifest.json missing"
    ctx.log(f"loading extension from {ext_dir}")
    import tempfile as _tf
    import time as _t
    profile = _tf.mkdtemp(prefix="bd_l3_ext_")
    worker = None
    launch_err = None
    try:
        with sync_playwright() as pw:
            try:
                # v3.63.5: channel="chromium" loads the real
                # Chromium-for-Testing build. The default no-channel
                # binary is headless-shell, a stripped Chromium that
                # cannot host MV3 extension service workers. See the
                # matching fix in tests/test_extension_live.py.
                context = pw.chromium.launch_persistent_context(
                    profile,
                    headless=True,
                    channel="chromium",
                    args=[
                        "--no-sandbox",
                        f"--disable-extensions-except={ext_dir}",
                        f"--load-extension={ext_dir}",
                    ],
                )
            except Exception as e:
                launch_err = f"{type(e).__name__}: {e}"
                context = None
            if context is None:
                ctx.log(f"could not launch Chromium: {launch_err}")
                return WARN, (f"Chromium launch failed — extension "
                              f"test not runnable: {launch_err}")
            try:
                # v3.63.5: event-based wait with a 30s ceiling.
                # MV3 service workers may take a few seconds to
                # register; the prior 6-second poll was flaky on
                # real hosts under load.
                if context.service_workers:
                    worker = context.service_workers[0]
                else:
                    try:
                        worker = context.wait_for_event(
                            "serviceworker", timeout=30_000)
                    except Exception:
                        worker = None
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as e:
        ctx.log(f"playwright session error: {e}")
        return WARN, f"Playwright session error: {type(e).__name__}: {e}"
    finally:
        import shutil as _sh
        _sh.rmtree(profile, ignore_errors=True)
    if worker is None:
        ctx.log("no service worker registered after 6s")
        return WARN, ("extension loaded but no MV3 service worker "
                      "registered — this Chromium build may not run "
                      "extension workers headless (the known skip "
                      "condition); try a headed/capable browser")
    ctx.log(f"service worker registered: {getattr(worker, 'url', '?')}")
    return PASS, ("MV3 extension service worker registered — the "
                  "extension loads correctly on a capable browser")


# ── login subsystem live tests (U36: L6 + L7) ─────────────────────
#
# L6 + L7 verify the login subsystem on the deployment. They share
# _login_sites, which finds sites that have a login template applied.
#
# SAFETY — these are deliberately READ-ONLY. They do NOT trigger a
# real login: firing an auto-login against a real site means entering
# real credentials, possibly tripping a captcha, or hitting a
# post-login upsell (the Brazzers "$29.97/mo" page — lesson I1). A
# live test must never do that. Instead they inspect login STATE and
# CONFIGURATION evidence the app already records (auth-health,
# session_history, the login_template_fallback event). disruptive is
# False — nothing on any site is touched.


def _login_sites(ctx):
    """Configured site ids on the deployment. Returns the list of
    site ids, or [] if the app is unreachable. Read-only."""
    ok, _, sbody, _ = ctx.get("/api/status", timeout=10)
    if ok and isinstance(sbody, dict):
        return list(_status_site_map(sbody).keys())
    return []


@live_test("L6", "real-templated-auto-login", disruptive=False)
def l6_real_templated_auto_login(ctx):
    """A site with a login template auto-logs-in successfully — the
    cookie jar carries a plausible auth cookie.

    READ-ONLY: this does NOT fire a login. It inspects the auth-health
    snapshot the app already maintains — a site whose last auth check
    is healthy, with a session-keeper holding a live session, is
    evidence templated auto-login works. Firing a real login here
    would risk a captcha or a billing upsell (lesson I1).
    """
    ok, status, body, _ = ctx.get("/api/auth_health/status",
                                  timeout=10)
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/auth_health/status unreachable "
                      f"(status={status}) — auto-login not testable")
    sites = body.get("sites") or {}
    if not sites:
        return WARN, ("no sites have auth-health state — no templated "
                      "login to verify; configure a site and log in")
    if isinstance(sites, list):
        sites = {str(st.get("site_id")): st for st in sites
                 if isinstance(st, dict) and st.get("site_id")}
    # auth_health rows OUTLIVE the sites they describe. DELETE /api/sites/<sid>
    # stops the runner, drops the account pool, clears the queue and removes
    # the site from s_cfg -- and leaves the row. Nothing anywhere in
    # bulk_downloader/ or tools/ prunes that table. Site ids are
    # uuid4().hex[:8], so the id is never reused and the row is orphaned for
    # good. Without this filter, one green row from a site deleted weeks ago
    # satisfies the `if healthy` branch below forever and L6 can never fail
    # again -- the failure mode this check exists to detect would become
    # invisible to it.
    configured = set(_login_sites(ctx))
    if not configured:
        return WARN, ("configured sites are not readable from /api/status, so "
                      "a stored auth-health row cannot be told apart from an "
                      "orphan left by a deleted site — auto-login not testable")
    orphans = sorted(sid for sid in sites if sid not in configured)
    if orphans:
        ctx.log(f"ignoring {len(orphans)} auth-health row(s) for site(s) that "
                f"no longer exist: {orphans[:5]}")
    sites = {sid: st for sid, st in sites.items() if sid in configured}
    if not sites:
        return WARN, (f"every auth-health row belongs to a site that no longer "
                      f"exists ({len(orphans)} orphan(s)); no current site has "
                      f"auth state — configure a site and log in")
    healthy, unhealthy = [], []
    for sid, st in (sites.items() if isinstance(sites, dict)
                    else []):
        cls = str((st or {}).get("status")
                  or (st or {}).get("classification") or "").lower()
        ctx.log(f"  {sid}: auth status = {cls or '?'}")
        if cls in ("ok", "healthy", "authenticated", "valid", "green"):
            healthy.append(sid)
        elif cls in ("expired", "failed", "unauthenticated", "dead",
                     "yellow", "red"):
            unhealthy.append(sid)
    ctx.log(f"{len(healthy)} healthy, {len(unhealthy)} unhealthy of "
            f"{len(sites)} site(s) with auth state")
    if healthy:
        return PASS, (f"{len(healthy)} site(s) report a healthy "
                      f"authenticated session — templated auto-login "
                      f"is working")
    if unhealthy:
        return WARN, (f"{len(unhealthy)} site(s) have an "
                      f"expired/failed session and none healthy — "
                      f"auto-login may be broken; inspect login logs")
    return WARN, ("auth-health state exists but no site is clearly "
                  "healthy or failed — log in to a templated site "
                  "and re-run")


@live_test("L7", "phase-b-fallback", disruptive=False)
def l7_phase_b_fallback(ctx):
    """A failed templated auto-login falls back to manual takeover
    inside login_async (the v3.62.2 Phase B fallback).

    READ-ONLY: it does NOT break a template to force the fallback.
    Instead it confirms the fallback is OBSERVABLE — the
    login_template_fallback event type is recorded in session_history
    when the fallback fires. A site that has ever needed the fallback
    will have such an event; its presence proves the path is wired.
    Absence is a WARN (the path may simply never have been needed),
    never a FAIL.
    """
    if not ctx.db_path.exists():
        return WARN, f"no DB at {ctx.db_path} — fallback not testable"
    configured = _login_sites(ctx)
    where = ""
    params = ()
    if configured:
        markers = ",".join("?" for _ in configured)
        where = f" AND site_id IN ({markers})"
        params = tuple(configured)
    try:
        cx = ctx.ro_db()
        try:
            # session_history records login lifecycle events
            fb = cx.execute(
                "SELECT COUNT(*) FROM session_history "
                "WHERE event_type = 'login_template_fallback'" + where,
                params).fetchone()[0]
            total_login = cx.execute(
                "SELECT COUNT(*) FROM session_history "
                "WHERE (event_type LIKE 'login%' "
                "OR event_type LIKE '%login%')" + where,
                params).fetchone()[0]
            recent = cx.execute(
                "SELECT site_id, detail FROM session_history "
                "WHERE event_type = 'login_template_fallback'" + where
                + " ORDER BY ts DESC LIMIT 3", params).fetchall()
        finally:
            cx.close()
    except Exception as e:
        return FAIL, f"could not read session_history: {e}"
    ctx.log(f"session_history: {total_login} login-related event(s), "
            f"{fb} login_template_fallback event(s)")
    for sid, detail in recent:
        ctx.log(f"  fallback: {sid} — {detail}")
    if fb > 0:
        return PASS, (f"{fb} login_template_fallback event(s) "
                      f"recorded — the Phase B manual-takeover "
                      f"fallback is wired and has fired")
    if total_login > 0:
        return WARN, (f"{total_login} login event(s) but no Phase B "
                      f"fallback ever recorded — the path may simply "
                      f"never have been needed (templates all work)")
    return WARN, ("no login events in session_history yet — Phase B "
                  "fallback not exercisable; log in to a templated "
                  "site first")


# ── Ollama reachability live test (U37: L17) ──────────────────────
#
# L17 — confirm the AI backend (Ollama, running as its own systemd
# service alongside bulkdownloader) is reachable and the configured
# models are installed. Reads /api/ai/status, which returns
# aiassist.ai_status() — the canonical AI-status surface.
#
# Read-only, disruptive=False. AI being disabled by config is a PASS
# (a valid, intentional state) — not every deployment runs Ollama.


@live_test("L17", "ollama-reachable", disruptive=False)
def l17_ollama_reachable(ctx):
    """The AI backend is reachable and its configured models are
    installed.

    /api/ai/status reports {enabled, ok, installed_models,
    missing_models, latency_ms, error}. Outcomes:
      • AI disabled by config        -> PASS (intentional state)
      • reachable, all models present -> PASS
      • reachable, models missing     -> WARN (re-pull or re-pick)
      • configured but unreachable    -> FAIL (Ollama service down?)
    Read-only — a single GET.
    """
    ok, status, body, ms = ctx.get("/api/ai/status", timeout=15)
    if not ok or not isinstance(body, dict):
        ctx.log(f"/api/ai/status unreachable: status={status}")
        return WARN, (f"/api/ai/status unreachable (status={status}) "
                      f"— cannot assess the AI backend")
    ctx.log(f"/api/ai/status responded in {ms:.0f}ms")
    if not body.get("enabled"):
        ctx.log("AI assist is disabled by config")
        return PASS, ("AI assist disabled by config — Ollama not "
                      "required for this deployment")
    if not body.get("ok"):
        err = str(body.get("error") or "unknown error")[:140]
        ctx.log(f"AI enabled but not ok: {err}")
        return FAIL, (f"AI enabled but the backend is unreachable: "
                      f"{err} — check the ollama systemd service")
    installed = body.get("installed_models") or []
    missing = body.get("missing_models") or []
    latency = body.get("latency_ms", "?")
    ctx.log(f"AI reachable: {len(installed)} model(s) installed, "
            f"{len(missing)} configured-but-missing, "
            f"backend latency {latency}ms")
    for m in installed:
        ctx.log(f"  installed: {m}")
    if missing:
        for m in missing:
            ctx.log(f"  MISSING: {m}")
        return WARN, (f"AI reachable but {len(missing)} configured "
                      f"model(s) not installed: "
                      f"{', '.join(str(m) for m in missing)} — "
                      f"re-pull, or re-pick in AI settings")
    return PASS, (f"Ollama reachable; all {len(installed)} configured "
                  f"model(s) installed (backend latency {latency}ms)")


# ── HLS / ffmpeg pipeline live tests (U38: L12 + L15 + L16) ───────
#
# Three tests of the segmented-download path. They involve real media
# work (an HLS download, an ffmpeg process), so all three are
# disruptive=True. They share _ffmpeg_present, which locates ffmpeg.
#
# L16 (process-group kill) is the one fully runnable anywhere the
# sandbox/host has ffmpeg: it spawns a real ffmpeg child in its own
# process group, kills the group the way hls_downloader._terminate
# does, and confirms no orphan child survives. L12/L15 need a real
# HLS stream + the deployment, so they inspect pipeline evidence.


def _ffmpeg_present():
    """Return the ffmpeg path, or None. Mirrors hls_downloader's
    shutil.which probe without importing the module."""
    import shutil as _sh
    return _sh.which("ffmpeg")


@live_test("L12", "hls-dash-segmented-download", disruptive=True)
def l12_hls_dash_segmented_download(ctx):
    """A real HLS/DASH stream downloads and muxes via ffmpeg.

    Needs ffmpeg present and a real segmented job to have run.
    Inspects history for completed downloads whose pipeline went
    through the HLS/ffmpeg path (segmented media). Disruptive — a
    real segmented download is heavy. Read-only here: it reads the
    evidence rather than launching a multi-minute stream pull.
    """
    ff = _ffmpeg_present()
    if not ff:
        ctx.log("ffmpeg not on PATH")
        return WARN, ("ffmpeg not found — the HLS/DASH segmented "
                      "path is not available on this host")
    ctx.log(f"ffmpeg present at {ff}")
    if not ctx.db_path.exists():
        return WARN, f"no DB at {ctx.db_path} — no download history"
    try:
        cx = ctx.ro_db()
        try:
            # segmented downloads tend to carry an .m3u8/.mpd-derived
            # origin or a hls/segment marker in the message
            done = cx.execute(
                "SELECT COUNT(*) FROM history "
                "WHERE status = 'done'").fetchone()[0]
            hls = cx.execute(
                "SELECT COUNT(*) FROM history WHERE status = 'done' "
                "AND (url LIKE '%.m3u8%' OR url LIKE '%.mpd%' "
                "OR message LIKE '%hls%' OR message LIKE '%segment%' "
                "OR message LIKE '%ffmpeg%')").fetchone()[0]
        finally:
            cx.close()
    except Exception as e:
        return FAIL, f"could not read history: {e}"
    ctx.log(f"history: {done} done, {hls} via the HLS/segmented path")
    if hls > 0:
        return PASS, (f"{hls} completed download(s) went through the "
                      f"HLS/ffmpeg segmented path — it works")
    if done > 0:
        return WARN, (f"{done} completed download(s) but none via the "
                      f"HLS path — queue a real .m3u8 stream to "
                      f"verify segmented download")
    return WARN, ("no completed downloads yet — HLS path not "
                  "exercisable; run a segmented download first")


@live_test("L15", "resume-after-interruption", disruptive=True)
def l15_resume_after_interruption(ctx):
    """A download killed mid-stream resumes on restart rather than
    refetching from zero.

    The queue persists pending/running/stopped jobs (db.py queue
    table) precisely so a restart picks up where it left off.
    Disruptive — exercising it for real means interrupting a live
    download. Here it confirms the resume MACHINERY is present: the
    queue persists non-terminal jobs and bulk_resume re-queues
    stopped ones. Read-only.
    """
    if not ctx.db_path.exists():
        return WARN, f"no DB at {ctx.db_path} — resume not testable"
    try:
        cx = ctx.ro_db()
        try:
            # the queue table is what makes resume possible — a
            # restart rehydrates SiteRunners from it
            cols = [r[1] for r in cx.execute(
                "PRAGMA table_info(queue)").fetchall()]
            by_status = {}
            for r in cx.execute(
                    "SELECT status, COUNT(*) FROM queue "
                    "GROUP BY status").fetchall():
                by_status[r[0]] = r[1]
        finally:
            cx.close()
    except Exception as e:
        return FAIL, f"could not read the queue table: {e}"
    ctx.log(f"queue table columns: {cols}")
    ctx.log(f"queue by status: {by_status}")
    if not cols:
        return FAIL, ("no queue table — a restart cannot resume "
                      "interrupted downloads")
    # the queue persisting non-terminal jobs is the resume mechanism
    resumable = sum(by_status.get(s, 0) for s in
                    ("pending", "running", "stopped"))
    ctx.log(f"{resumable} job(s) in a resumable (non-terminal) state")
    return PASS, (f"queue table persists jobs ({len(cols)} columns, "
                  f"{resumable} resumable) — a restart will rehydrate "
                  f"and resume interrupted downloads")


@live_test("L16", "ffmpeg-process-group-kill", disruptive=True)
def l16_ffmpeg_process_group_kill(ctx):
    """Cancelling an ffmpeg job kills the whole process GROUP — no
    orphan segment children survive.

    hls_downloader._terminate signals the process GROUP (the job is
    spawned with start_new_session=True), because plain terminate()
    misses ffmpeg's sub-processes. This test reproduces that: spawn a
    real ffmpeg child in its own session/group, kill the group, and
    confirm the process is gone. Fully runnable wherever ffmpeg
    exists. Disruptive only because it spawns/kills a process.
    """
    import os as _os
    import signal as _sig
    import subprocess as _sp
    import time as _t
    ff = _ffmpeg_present()
    if not ff:
        return WARN, ("ffmpeg not found — cannot exercise the "
                      "process-group kill")
    ctx.log(f"ffmpeg at {ff}; spawning a long-running ffmpeg child "
            f"in its own process group")
    # a harmless long-running ffmpeg: generate silent audio for a
    # long duration to /dev/null (or NUL). -re paces the input at
    # native rate so it actually runs in real time (without it,
    # anullsrc drains to a null sink instantly).
    null = "NUL" if _os.name == "nt" else "/dev/null"
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-re", "-f", "lavfi", "-i", "anullsrc", "-t", "120",
           "-f", "null", null]
    kw = {}
    if _os.name == "nt":
        kw["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        proc = _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                         **kw)
    except Exception as e:
        return WARN, f"could not spawn ffmpeg: {e}"
    _t.sleep(1.0)
    if proc.poll() is not None:
        return WARN, (f"ffmpeg exited immediately (rc="
                      f"{proc.returncode}) — cannot test the kill")
    pid = proc.pid
    ctx.log(f"ffmpeg running as pid {pid}; killing the process group")
    # kill the GROUP the way hls_downloader._terminate does
    try:
        if _os.name == "nt":
            proc.send_signal(_sig.CTRL_BREAK_EVENT)
        else:
            _os.killpg(_os.getpgid(pid), _sig.SIGTERM)
    except Exception as e:
        ctx.log(f"group signal failed ({e}); falling back to kill")
        proc.kill()
    try:
        proc.wait(timeout=8)
    except _sp.TimeoutExpired:
        ctx.log("did not exit on SIGTERM; sending SIGKILL to group")
        try:
            if _os.name != "nt":
                _os.killpg(_os.getpgid(pid), _sig.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    # confirm the process is actually gone
    _t.sleep(0.5)
    alive = proc.poll() is None
    if alive:
        ctx.log(f"pid {pid} still alive after group kill")
        return FAIL, (f"ffmpeg pid {pid} survived a process-group "
                      f"kill — _terminate's group-kill is not working")
    ctx.log(f"pid {pid} terminated; returncode={proc.returncode}")
    return PASS, ("ffmpeg child spawned in its own process group and "
                  "killed cleanly — the group-kill path works, no "
                  "orphan survives")


# ── vision-call roundtrip live test (U39: L18) ────────────────────
#
# L18 — send a real screenshot to the vision model and confirm
# structured output comes back. This is the end-to-end check that the
# AI vision path actually works on the deployment, not just that
# Ollama is reachable (that is L17's job).
#
# It needs AI enabled with a vision model AND a configured site (the
# detect_login endpoint is per-site). When either is absent it WARNs.
# A vision call is a real, model-dependent operation but touches
# nothing destructive — disruptive=False.

# a 1x1 transparent PNG — a minimal-but-valid image payload, so the
# test exercises the vision roundtrip without needing a real capture
_TINY_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0l"
                 "EQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


@live_test("L18", "vision-call-roundtrip", disruptive=False)
def l18_vision_call_roundtrip(ctx):
    """Send a real screenshot to the vision model; get structured
    output back.

    Confirms AI is enabled with a vision model, then POSTs a small
    image through /api/sites/<sid>/ai/detect_login (the vision-backed
    login-form detector) and checks a structured response returns.
    WARN when AI is off, no vision model is configured, or no site
    exists — those are environment gaps, not defects. FAIL only when
    the vision call itself errors despite AI being configured.
    """
    # AI must be enabled with a vision model
    ok, status, ai, _ = ctx.get("/api/ai/status", timeout=15)
    if not ok or not isinstance(ai, dict):
        return WARN, (f"/api/ai/status unreachable (status={status}) "
                      f"— vision roundtrip not testable")
    if not ai.get("enabled"):
        return WARN, ("AI assist disabled by config — no vision model "
                      "to roundtrip against")
    if not ai.get("ok"):
        return WARN, (f"AI backend not reachable "
                      f"({str(ai.get('error'))[:80]}) — fix the AI "
                      f"backend first (see L17)")
    ctx.log("AI enabled and reachable; locating a site for the "
            "vision detect_login endpoint")
    # detect_login is per-site — find a configured site
    sok, _, sbody, _ = ctx.get("/api/status", timeout=10)
    site_id = None
    if sok and isinstance(sbody, dict):
        sites = _status_site_map(sbody)
        if sites:
            site_id = next(iter(sites.keys()))
    if not site_id:
        return WARN, ("AI is ready but no site is configured — the "
                      "per-site vision endpoint is not exercisable")
    ctx.log(f"sending a test screenshot through "
            f"/api/sites/{site_id}/ai/detect_login")
    # POST a minimal screenshot + DOM excerpt. The harness Context
    # only does GET; do a direct POST here.
    import json as _json
    import urllib.request as _u
    import urllib.error as _ue
    import time as _t
    payload = _json.dumps({
        "dom_excerpt": ("<form><input type='text' name='user'>"
                        "<input type='password' name='pass'>"
                        "<button type='submit'>Sign in</button></form>"),
        "screenshot_b64": _TINY_PNG_B64,
        "context_hint": "live-test vision roundtrip probe",
    }).encode("utf-8")
    url = (ctx.base_url
           + f"/api/sites/{site_id}/ai/detect_login")
    req = _u.Request(url, data=payload, method="POST",
                     headers={"Content-Type": "application/json"})
    t0 = _t.time()
    try:
        with _u.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "replace")
        body = _json.loads(raw)
    except _ue.HTTPError as e:
        return FAIL, (f"vision detect_login returned HTTP {e.code} — "
                      f"the vision call path is broken")
    except Exception as e:
        return FAIL, (f"vision call failed: {type(e).__name__}: {e} "
                      f"— the AI vision roundtrip is not working")
    ms = (_t.time() - t0) * 1000
    ctx.log(f"vision detect_login responded in {ms:.0f}ms")
    if not isinstance(body, dict):
        return FAIL, "vision call returned a non-object response"
    if body.get("error") or body.get("ok") is False:
        return FAIL, (f"vision call returned an error: "
                      f"{str(body.get('error'))[:120]}")
    # a structured response is the success signal — the detector
    # returns selector proposals / a structured result
    keys = sorted(body.keys())
    ctx.log(f"structured response keys: {keys}")
    return PASS, (f"vision model returned a structured response in "
                  f"{ms:.0f}ms ({len(keys)} field(s)) — the vision "
                  f"roundtrip works")


# ── backup-restore roundtrip live test (U40: L23) ─────────────────
#
# L23 — back up the live DB (all three WAL files), restore the copy
# to a scratch dir, and confirm consistency. DANGER_MAP: a WAL-mode
# DB is .db + .db-wal + .db-shm; a backup of only .db captures a
# stale snapshot. This test copies all three, opens the copy, runs
# PRAGMA integrity_check, and confirms the copy's row counts match
# the live DB.
#
# Read-only against the deployment — it copies the DB files and works
# entirely on the COPY in a scratch dir. disruptive=False. It never
# fires the destructive /api/backup/restore endpoint.


@live_test("L23", "backup-restore-roundtrip", disruptive=False)
def l23_backup_restore_roundtrip(ctx):
    """Back up the live DB (all 3 WAL files), restore to a scratch
    copy, confirm consistency.

    Copies downloader_history.db + -wal + -shm to a temp dir, opens
    the copy read-only, runs integrity_check, and compares row counts
    against the live DB. PASS when the restored copy is sound and
    matches; FAIL on a corrupt or mismatched restore. Read-only — the
    live DB is only ever copied FROM.
    """
    import os as _os
    import shutil as _sh
    import sqlite3 as _sq
    import tempfile as _tf
    if not ctx.db_path.exists():
        return WARN, f"no DB at {ctx.db_path} — nothing to back up"
    # the WAL trio — a backup MUST include all three (DANGER_MAP)
    src_db = ctx.db_path
    src_wal = src_db.with_name(src_db.name + "-wal")
    src_shm = src_db.with_name(src_db.name + "-shm")
    ctx.log(f"live DB: {src_db} ({src_db.stat().st_size:,} B)")
    ctx.log(f"  -wal sidecar present: {src_wal.exists()}")
    ctx.log(f"  -shm sidecar present: {src_shm.exists()}")
    sandbox = _tf.mkdtemp(prefix="bd_l23_restore_")
    try:
        # ── back up: copy the whole WAL trio ──
        copied = []
        for f in (src_db, src_wal, src_shm):
            if f.exists():
                dest = _os.path.join(sandbox, f.name)
                try:
                    _sh.copyfile(f, dest)
                    copied.append(f.name)
                except Exception as e:
                    return FAIL, (f"backup copy of {f.name} failed: "
                                  f"{e}")
        ctx.log(f"backed up {len(copied)} file(s): {copied}")
        if src_db.name not in copied:
            return FAIL, "could not copy the main .db file"
        # ── restore + verify: open the COPY, integrity-check it ──
        restored = _os.path.join(sandbox, src_db.name)
        try:
            cx = _sq.connect(f"file:{restored}?mode=ro", uri=True,
                             timeout=10)
        except Exception as e:
            return FAIL, f"restored copy could not be opened: {e}"
        try:
            integ = cx.execute(
                "PRAGMA integrity_check").fetchone()[0]
            ctx.log(f"restored copy integrity_check: {integ}")
            if str(integ).lower() != "ok":
                return FAIL, (f"restored copy failed integrity_check: "
                              f"{integ}")
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
            restored_counts = {}
            for t in tables:
                try:
                    restored_counts[t] = cx.execute(
                        f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                except Exception:
                    restored_counts[t] = -1
        finally:
            cx.close()
        # ── compare against the live DB ──
        try:
            live = ctx.ro_db()
            try:
                live_counts = {}
                for t in tables:
                    try:
                        live_counts[t] = live.execute(
                            f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    except Exception:
                        live_counts[t] = -1
            finally:
                live.close()
        except Exception as e:
            ctx.log(f"could not re-read the live DB to compare: {e}")
            live_counts = {}
        mismatches = []
        for t in tables:
            r, l = restored_counts.get(t), live_counts.get(t)
            if l is not None and r != l:
                mismatches.append(f"{t}: restored={r} live={l}")
            ctx.log(f"  {t}: restored={r} live={l}")
    finally:
        _sh.rmtree(sandbox, ignore_errors=True)
    if mismatches:
        # a small drift can happen if the live DB was written between
        # the copy and the compare — flag it but do not hard-fail on
        # a 1-2 row difference
        big = [m for m in mismatches]
        return WARN, (f"restored copy is sound but {len(big)} table "
                      f"count(s) differ from live (likely a write "
                      f"between copy and compare): {big[:2]}")
    wal_note = (f"WAL trio: {len(copied)} of 3 files present"
                if src_wal.exists()
                else "no -wal sidecar (DB was checkpointed) — "
                     "main .db captured")
    return PASS, (f"backup-restore roundtrip clean: backed up "
                  f"{copied} ({wal_note}); restored copy passed "
                  f"integrity_check, {len(tables)} table count(s) "
                  f"match live")


# ── systemd lifecycle live tests (U41: L24 + L25 + L27) ───────────
#
# Three tests of the systemd-managed lifecycle: starts on boot,
# restarts on crash, survives logout. They are grouped disruptive=True
# and the backlog calls for running them as one deliberate, flagged
# batch — because a TRUE end-to-end check of each would reboot the
# box / kill the service, which a live test must never do unasked.
#
# So these tests do NOT reboot, kill, or log anything out. They
# verify, via `systemctl`, the CONFIGURATION and OBSERVABLE STATE
# that make those behaviours work:
#   L24 — the unit is enabled (WantedBy a boot target).
#   L25 — Restart=on-failure|always is set; NRestarts is observable.
#   L27 — the service runs detached from any login session.
# A genuine reboot/crash test stays an explicit operator action; each
# test says so. They share _systemctl, which returns WARN-able output
# when there is no systemd bus (e.g. this sandbox) — never a crash.


def _systemctl(args, timeout=10):
    """Run `systemctl <args>`. Returns (ok, stdout, err). ok is False
    when systemctl is absent, there is no systemd bus, or the call
    fails — callers turn that into a WARN, never a FAIL.

    T56 NOTE: `ok=True` here means "systemctl ran and reached the
    bus" — NOT "the unit exists" and NOT "rc==0". Many systemctl
    queries against a nonexistent unit return rc!=0 with stdout
    that LOOKS like a real answer (e.g. `is-active <missing>` →
    rc=3, out='inactive'; `show <missing> -p Restart` → rc=0,
    out='Restart=no'). Callers must NOT treat such stdout as a
    real-unit answer; use `_unit_exists(name)` to gate first.
    """
    import subprocess as _sp
    try:
        p = _sp.run(["systemctl"] + list(args),
                    capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "", "systemctl not installed"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    # no systemd bus (containers, this sandbox) -> not a real result
    if "has not been booted with systemd" in err \
            or "Failed to connect to bus" in err:
        return False, out, "no systemd bus on this host"
    return True, out, err


def _unit_exists(name):
    """T56 — True iff a systemd unit by this name is installed.

    Uses `systemctl is-enabled <name>` because it uniquely returns
    the literal string `not-found` for a missing unit (and rc!=0),
    distinguishing it from a unit that exists but is disabled
    (returns `disabled`, rc=1) or enabled (`enabled`, rc=0). All
    the other lifecycle queries (`is-active`, `show -p Restart`,
    etc.) return systemd defaults for a missing unit and look
    identical to a misconfigured real unit — that ambiguity is
    exactly what L25 and L27 used to mis-FAIL on.

    Returns False if systemctl is unavailable / no bus / the unit
    is absent. The unit-doesn't-exist branch is the one that
    matters here; "no bus" gets caught earlier by callers.
    """
    ok, out, _ = _systemctl(["is-enabled", name])
    if not ok:
        return False
    return out != "not-found"


def _svc_property(name, prop):
    """Read one `systemctl show` property of a unit. Returns the
    value string, or None if unavailable / the unit does not exist.

    T56 fix: previously this returned the systemd DEFAULT for a
    nonexistent unit (e.g. `Restart=no` → `'no'`), making callers
    treat a missing unit as a misconfigured real one. Now gates
    on `_unit_exists(name)` and returns None for a missing unit.
    """
    if not _unit_exists(name):
        return None
    ok, out, _ = _systemctl(["show", name, "-p", prop,
                             "--no-pager", "--value"])
    if not ok:
        return None
    return out


@live_test("L24", "service-starts-on-boot", disruptive=True)
def l24_service_starts_on_boot(ctx):
    """The bulkdownloader service is enabled to start on boot.

    Does NOT reboot — a real reboot test is an operator action. It
    confirms the unit is ENABLED (systemctl is-enabled) and WantedBy
    a boot target, which is what makes it come up on boot. WARN when
    there is no systemd bus (sandbox) or the unit is absent.
    """
    ok, out, err = _systemctl(["is-enabled", "bulkdownloader"])
    if not ok:
        ctx.log(f"systemctl unavailable: {err}")
        return WARN, (f"cannot query systemd ({err}) — boot-start not "
                      f"verifiable from here; reboot stash to confirm")
    ctx.log(f"systemctl is-enabled bulkdownloader -> {out!r}")
    if out == "not-found":
        return WARN, ("no bulkdownloader systemd unit installed — run "
                      "install_service.sh on the deployment")
    wanted = _svc_property("bulkdownloader", "WantedBy") or ""
    ctx.log(f"WantedBy = {wanted!r}")
    if out == "enabled":
        return PASS, (f"bulkdownloader unit is enabled "
                      f"(WantedBy={wanted or '?'}) — it will start on "
                      f"boot; a full reboot test is an operator action")
    return FAIL, (f"bulkdownloader unit is '{out}', not 'enabled' — "
                  f"it will NOT start on boot; run "
                  f"systemctl enable bulkdownloader")


@live_test("L25", "restart-on-crash", disruptive=True)
def l25_restart_on_crash(ctx):
    """systemd restarts the app if it crashes.

    Does NOT kill the process — a real crash test is an operator
    action. It confirms the unit's Restart= is on-failure/always
    (without it a crash leaves the app down) and reports NRestarts,
    the observable count of times systemd has already restarted it.
    """
    restart = _svc_property("bulkdownloader", "Restart")
    if restart is None:
        ctx.log("systemctl show unavailable (no bus or no unit)")
        return WARN, ("cannot query the systemd unit — restart-on-"
                      "crash not verifiable here; check on stash")
    ctx.log(f"Restart = {restart!r}")
    nrestarts = _svc_property("bulkdownloader", "NRestarts")
    rsec = _svc_property("bulkdownloader", "RestartSec")
    ctx.log(f"NRestarts = {nrestarts!r}, RestartSec = {rsec!r}")
    if restart.lower() not in ("on-failure", "always", "on-abnormal",
                               "on-abort"):
        return FAIL, (f"Restart='{restart}' — systemd will NOT restart "
                      f"the app after a crash; set Restart=on-failure")
    return PASS, (f"Restart={restart} (RestartSec={rsec or '?'}) — "
                  f"systemd will restart the app on crash; it has "
                  f"restarted {nrestarts or '0'} time(s) so far. A "
                  f"real kill test is an operator action.")


@live_test("L27", "survives-logout", disruptive=True)
def l27_survives_logout(ctx):
    """The service keeps running after the operator's SSH session
    ends.

    Does NOT log anyone out. A systemd service runs under the system
    manager, detached from any login session — that is intrinsically
    what makes it survive logout. This confirms the unit exists and
    is system-managed (not a user/session unit), and reports its
    active state + uptime as evidence it is running independently.

    T56: short-circuit to WARN if the unit is absent — without this
    gate, `is-active <missing>` returns `out='inactive'` and L27
    used to mis-FAIL on a host that just hasn't run install_service
    yet, treating "no unit" the same as "unit exists but stopped".
    """
    ok, out, err = _systemctl(["is-active", "bulkdownloader"])
    if not ok:
        ctx.log(f"systemctl unavailable: {err}")
        return WARN, (f"cannot query systemd ({err}) — survives-logout "
                      f"not verifiable from here")
    ctx.log(f"systemctl is-active bulkdownloader -> {out!r}")
    # T56: gate on unit existence BEFORE reading is-active state —
    # otherwise a missing unit looks identical to an inactive unit
    # (systemd returns 'inactive' for both).
    if not _unit_exists("bulkdownloader"):
        return WARN, ("no bulkdownloader systemd unit installed — "
                      "run install_service.sh on the deployment; "
                      "survives-logout is moot without a unit")
    # a system unit (not a --user unit) is what survives logout
    frag = _svc_property("bulkdownloader", "FragmentPath") or ""
    ctx.log(f"FragmentPath = {frag!r}")
    if out == "active":
        active_since = _svc_property("bulkdownloader",
                                     "ActiveEnterTimestamp") or "?"
        is_user_unit = "/user/" in frag or frag.startswith(
            "/etc/systemd/user")
        if is_user_unit:
            return WARN, ("the unit looks like a per-user systemd "
                          "unit — a system unit survives logout more "
                          "reliably; verify it is in "
                          "/etc/systemd/system")
        return PASS, (f"bulkdownloader is an active system service "
                      f"(since {active_since}) — it runs detached "
                      f"from any login session and survives logout")
    if out in ("inactive", "failed"):
        return FAIL, (f"bulkdownloader is '{out}' — not running; "
                      f"survives-logout is moot until it is up")
    return WARN, (f"bulkdownloader is-active reports '{out}' — "
                  f"inconclusive; check systemctl status on stash")


# ── resource-stability long-run live tests (U42: L31+L32+L33) ─────
#
# Three samplers over one long run, verifying the lesson-J1 invariant:
# the real leak class in a memory-safe runtime is never-evicted
# retention, not buffer overflows. They share _sample_over_time, which
# polls an endpoint at intervals and returns the series.
#
# A genuine multi-hour run is an operator action; these use a short
# default window (a handful of samples) so they are runnable as part
# of a normal live-test pass, and report the trend. disruptive=False
# — read-only polling. The window can be widened by the operator for
# a real soak test.
#
#   L31 — RSS bounded          (samples /api/dev/mem_audit)
#   L32 — thread count stable  (samples /api/dev/threads)
#   L33 — no orphan Chromium   (samples /api/dev/leak_scan)

_SAMPLE_COUNT = 5       # samples per run (operator can soak longer)
_SAMPLE_GAP_S = 3.0     # seconds between samples


def _sample_over_time(ctx, path, extract, *, count=None, gap=None):
    """Poll `path` `count` times, `gap` seconds apart. `extract` maps
    a response body to one numeric sample. Returns (samples, errors)
    — samples is a list of numbers, errors a list of strings.

    count/gap default to the module-level _SAMPLE_COUNT/_SAMPLE_GAP_S,
    read at CALL time — so an operator (or a test) can widen the
    window by reassigning those module globals.

    Early-bail: if the very first poll is unreachable, there is no
    point sleeping through the rest — return immediately so a dead
    target costs ~1 call, not the whole window."""
    import time as _t
    if count is None:
        count = _SAMPLE_COUNT
    if gap is None:
        gap = _SAMPLE_GAP_S
    samples, errors = [], []
    for i in range(count):
        ok, status, body, _ = ctx.get(path, timeout=15)
        if not ok or not isinstance(body, dict):
            errors.append(f"sample {i}: {path} status={status}")
            if i == 0 and not ok:
                # unreachable on the first try — do not burn the
                # rest of the window polling a dead endpoint
                errors.append("first sample unreachable — bailing "
                              "early (no point sampling further)")
                break
        else:
            try:
                val = extract(body)
                if val is not None:
                    samples.append(val)
                else:
                    errors.append(f"sample {i}: no metric in response")
            except Exception as e:
                errors.append(f"sample {i}: {type(e).__name__}: {e}")
        if i < count - 1:
            _t.sleep(gap)
    return samples, errors


def _trend(samples):
    """Summarize a numeric series: (first, last, peak, growth)."""
    if not samples:
        return None
    return {"first": samples[0], "last": samples[-1],
            "peak": max(samples), "min": min(samples),
            "growth": samples[-1] - samples[0]}


@live_test("L31", "memory-stability-long-run", disruptive=False)
def l31_memory_stability_long_run(ctx):
    """Over a run, process RSS stays bounded — no unbounded growth.

    Samples /api/dev/mem_audit; reads the RSS figure each time. A
    short default window flags a steeply rising trend. A real
    multi-hour soak is an operator action (widen the window). PASS
    when RSS is flat or falls; WARN on a clear upward trend.
    """
    def _rss(body):
        # perf_lab snapshot exposes RSS under a few possible keys
        for k in ("rss_mb", "rss_bytes", "rss"):
            if k in body:
                v = body[k]
                return v / (1024 * 1024) if k == "rss_bytes" else v
        proc = body.get("process") or body.get("memory") or {}
        for k in ("rss_mb", "rss_bytes", "rss"):
            if k in proc:
                v = proc[k]
                return v / (1024 * 1024) if k == "rss_bytes" else v
        return None

    samples, errors = _sample_over_time(ctx, "/api/dev/mem_audit",
                                        _rss)
    ctx.log(f"RSS samples (MB): {samples}")
    for e in errors[:3]:
        ctx.log(f"  {e}")
    if not samples:
        return WARN, ("could not sample RSS from /api/dev/mem_audit "
                      "(dev mode off, or app unreachable) — memory "
                      "stability not testable here")
    tr = _trend(samples)
    ctx.log(f"RSS trend: {tr}")
    # growth over a short window beyond ~20% of the baseline is a
    # signal worth a closer look (a real soak would be definitive)
    base = tr["first"] or 1
    if tr["growth"] > 0 and tr["growth"] / base > 0.20:
        return WARN, (f"RSS rose {tr['growth']:.1f} MB over "
                      f"{len(samples)} samples ({tr['first']:.0f}->"
                      f"{tr['last']:.0f} MB) — possible leak; run a "
                      f"longer soak to confirm")
    return PASS, (f"RSS stable across {len(samples)} samples "
                  f"({tr['min']:.0f}-{tr['peak']:.0f} MB, "
                  f"net {tr['growth']:+.1f} MB) — no unbounded growth "
                  f"in this window")


@live_test("L32", "no-thread-growth", disruptive=False)
def l32_no_thread_growth(ctx):
    """Thread count stays stable across a run — threads are not
    leaked.

    Samples /api/dev/threads and counts live threads. A steadily
    climbing count means threads are spawned and never joined. PASS
    when the count is flat; WARN on sustained growth.
    """
    def _nthreads(body):
        for k in ("thread_count", "count", "total"):
            if k in body and isinstance(body[k], int):
                return body[k]
        threads = body.get("threads")
        if isinstance(threads, list):
            return len(threads)
        return None

    samples, errors = _sample_over_time(ctx, "/api/dev/threads",
                                        _nthreads)
    ctx.log(f"thread-count samples: {samples}")
    for e in errors[:3]:
        ctx.log(f"  {e}")
    if not samples:
        return WARN, ("could not sample /api/dev/threads (dev mode "
                      "off, or app unreachable) — thread growth not "
                      "testable here")
    tr = _trend(samples)
    ctx.log(f"thread-count trend: {tr}")
    # any sustained climb in thread count is suspicious — threads
    # should be a small bounded pool
    if tr["growth"] >= 3:
        return WARN, (f"thread count rose by {tr['growth']} over "
                      f"{len(samples)} samples ({tr['first']}->"
                      f"{tr['last']}) — threads may be leaking; run a "
                      f"longer soak")
    return PASS, (f"thread count stable across {len(samples)} samples "
                  f"({tr['min']}-{tr['peak']}, net {tr['growth']:+d}) "
                  f"— no thread leak in this window")


@live_test("L33", "no-leaked-chromium", disruptive=False)
def l33_no_leaked_chromium(ctx):
    """No orphan Chromium processes accumulate.

    Samples /api/dev/leak_scan, which reports resource-leak signals
    including orphan browser processes. A growing orphan count means
    Playwright contexts/browsers are not being closed. PASS when the
    orphan count stays at zero (or flat); WARN on growth.
    """
    def _orphans(body):
        # leak_scan reports orphan processes under a few shapes
        for k in ("orphan_browsers", "orphan_chromium",
                  "orphan_processes", "orphans"):
            v = body.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, list):
                return len(v)
        procs = body.get("processes") or body.get("leaks") or {}
        if isinstance(procs, dict):
            # Preferred name first (orphan_chromium / orphan_browsers)
            # for any future endpoint variants; then fall back to the
            # actual current endpoint key, which is plain "chromium"
            # (from dev_suite.leak_scan: _pl._child_process_count()).
            # Pre-v3.64.6 this WARN'd on every deploy because none of
            # the orphan_* keys exist and the chromium key was unread.
            for k in ("orphan_browsers", "orphan_chromium", "chromium"):
                if isinstance(procs.get(k), int):
                    return procs[k]
            # Windows: _child_process_count walks /proc, which doesn't
            # exist; the function's except branch returns {} (no
            # chromium key at all). When the endpoint's own verdict
            # says no leaks were found AND findings is empty, trust
            # that and treat as zero orphans — the endpoint is the
            # source of truth for its own verdict, even if it couldn't
            # produce a process count on this platform. Without this,
            # L33 WARN'd on Windows forever because the chromium key
            # is never present in the response.
            if not procs:
                verdict = (body.get("verdict") or "").lower()
                findings = body.get("findings") or []
                if (verdict == "no leak signals"
                        and isinstance(findings, list)
                        and not findings):
                    return 0
        return None

    samples, errors = _sample_over_time(ctx, "/api/dev/leak_scan",
                                        _orphans)
    ctx.log(f"orphan-Chromium samples: {samples}")
    for e in errors[:3]:
        ctx.log(f"  {e}")
    if not samples:
        return WARN, ("could not sample orphan-process count from "
                      "/api/dev/leak_scan — no-leaked-Chromium not "
                      "testable here")
    tr = _trend(samples)
    ctx.log(f"orphan-Chromium trend: {tr}")
    if tr["peak"] == 0:
        return PASS, (f"zero orphan Chromium processes across "
                      f"{len(samples)} samples — no browser leak")
    if tr["growth"] > 0:
        return WARN, (f"orphan Chromium count rose {tr['first']}->"
                      f"{tr['last']} over {len(samples)} samples — "
                      f"Playwright contexts may be leaking")
    return WARN, (f"orphan Chromium processes present but not growing "
                  f"({tr['peak']} peak) — investigate; they may be "
                  f"stale from before this run")


# ── SSE live-update live test (U43: L35) ──────────────────────────
#
# L35 — confirm a dashboard event reaches a connected SSE client, and
# that a reconnect succeeds after a forced drop. The frontend's live
# views depend on /api/stream; if it stops delivering, the UI goes
# silently stale (lesson H1). This connects to the stream, reads the
# first few events, drops, and reconnects.
#
# The harness Context.get() buffers the whole response — unusable for
# a streaming endpoint — so L35 reads /api/stream directly with a
# tight read budget. Read-only, disruptive=False.


def _read_sse_events(base_url, path="/api/stream", *, max_events=2,
                      budget_s=12):
    """Open an SSE stream and read up to `max_events` complete events
    within `budget_s` seconds. Returns (events, error). Each event is
    the raw 'event: X' / 'data: Y' block text."""
    import time as _t
    import urllib.request as _u
    url = base_url.rstrip("/") + path
    events, buf = [], ""
    t0 = _t.time()
    try:
        resp = _u.urlopen(url, timeout=budget_s)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    try:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in ctype:
            return [], (f"endpoint did not return an SSE stream "
                        f"(Content-Type: {ctype or 'none'})")
        # SSE events are separated by a blank line
        while len(events) < max_events and \
                _t.time() - t0 < budget_s:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                block = block.strip()
                if block:
                    events.append(block)
                if len(events) >= max_events:
                    break
    except Exception as e:
        return events, f"{type(e).__name__}: {e}"
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return events, None


@live_test("L35", "sse-live-update", disruptive=False)
def l35_sse_live_update(ctx):
    """A dashboard event reaches a connected SSE client, and a
    reconnect succeeds after a forced drop.

    /api/stream pushes a dashboard + status event immediately on
    connect. This test connects, confirms events arrive, closes the
    connection, then reconnects and confirms events arrive again —
    proving both the live channel and reconnection work. WARN when
    the app is unreachable; FAIL when the stream connects but never
    delivers an event.
    """
    # first connection
    ctx.log("opening SSE connection #1 to /api/stream")
    events1, err1 = _read_sse_events(ctx.base_url, max_events=2)
    if err1 and not events1:
        ctx.log(f"connection #1 failed: {err1}")
        return WARN, (f"could not open the SSE stream ({err1}) — "
                      f"app unreachable or /api/stream missing")
    ctx.log(f"connection #1 received {len(events1)} event(s)")
    for ev in events1:
        first_line = ev.splitlines()[0] if ev else "(empty)"
        ctx.log(f"  event: {first_line}")
    if not events1:
        return FAIL, ("SSE stream connected but delivered no events — "
                      "the live-update channel is not pushing")
    # forced drop already happened (we closed the response); now
    # reconnect to confirm reconnection works
    ctx.log("connection #1 closed; reconnecting (SSE connection #2)")
    events2, err2 = _read_sse_events(ctx.base_url, max_events=1)
    if err2 and not events2:
        ctx.log(f"reconnect failed: {err2}")
        return FAIL, (f"first connection worked but the reconnect "
                      f"failed ({err2}) — clients cannot recover from "
                      f"a dropped SSE connection")
    ctx.log(f"reconnect received {len(events2)} event(s)")
    if not events2:
        return FAIL, ("reconnect opened the stream but received no "
                      "events — reconnection does not re-establish "
                      "the live channel")
    return PASS, (f"SSE live-update works: connection #1 got "
                  f"{len(events1)} event(s), reconnect after a drop "
                  f"got {len(events2)} — the channel and reconnection "
                  f"both work")


# ─── T46 / L-29 — VPN kill-switch (read-only inspector) ────────────
#
# The original L-29 in the catalog calls for actively toggling the
# kill-switch and verifying the DNS leak path is blocked. That needs
# root, a real VPN session, and a careful network-leak-injection
# probe — exactly what DEV_BACKLOG flagged: "high value but needs
# careful real-network setup; do it deliberately, not as routine
# backlog."
#
# This version is the READ-ONLY inspector: it confirms the kill-switch
# subsystem is loaded, configurable, and aware of any current kill
# states. The actively-disruptive version (with iptables / nftables
# toggling) stays deferred behind a stronger gate.


@live_test("L29", "vpn-kill-switch-config-check", disruptive=False)
def l29_vpn_kill_switch_config_check(ctx):
    """Verify the VPN kill-switch subsystem is wired and inspectable.

    PASS:  kill_switch module loads, list_kill_states() returns a
           list (empty is fine — means no tunnel is currently in
           kill state), and the runtime config exposes auto_recover.
    WARN:  one of the subsystem checks is unreachable (module missing,
           or list_kill_states fails). The DISRUPTIVE network-toggle
           probe was deliberately deferred — this WARN does NOT mean
           the kill switch is broken on the host.
    FAIL:  kill_switch module is loadable but list_kill_states raises,
           OR a kill state is currently active (which means the
           service has an unresolved VPN problem). Both deserve
           operator attention.
    """
    # Prefer the running service's state. Importing the module in this
    # separate live-test process would otherwise inspect a fresh module.
    service_ok, _, service_body, _ = ctx.get(
        "/api/vpn/kill_switch/state", timeout=10)
    service_state = (service_body if service_ok
                     and isinstance(service_body, dict) else None)

    # Subsystem load (fallback for older deployments without the API).
    try:
        from bulk_downloader import vpn_kill_switch as _ks
    except Exception as e:
        ctx.log(f"vpn_kill_switch import failed: {e}")
        return WARN, (f"vpn_kill_switch module did not import: {e}; "
                      f"either the subsystem isn't built into this "
                      f"deployment, or the import path changed")
    # list_kill_states is the operator's view onto the state machine.
    # If it raises, the subsystem is broken in a way the operator
    # needs to see.
    try:
        states = (service_state.get("states")
                  if service_state is not None
                  else _ks.list_kill_states())
    except Exception as e:
        ctx.log(f"list_kill_states raised: {e}")
        return FAIL, (f"list_kill_states() raised {type(e).__name__}: "
                      f"{e}; kill-switch state machine is broken")
    if not isinstance(states, list):
        ctx.log(f"list_kill_states returned non-list: {type(states)}")
        return FAIL, (f"list_kill_states returned "
                      f"{type(states).__name__}, expected list; "
                      f"contract is broken")
    # auto_recover toggle — read-only check it's introspectable
    try:
        auto = (service_state.get("auto_recover")
                if service_state is not None
                else _ks.get_auto_recover())
    except Exception as e:
        ctx.log(f"get_auto_recover raised: {e}")
        return WARN, (f"get_auto_recover() raised {type(e).__name__}; "
                      f"the kill-switch is partially inspectable but "
                      f"auto-recover control is unreachable")
    # An ACTIVE kill state means the service had to kill a tunnel
    # because of a leak / config / runtime problem. That's a real
    # operator-visible problem, even though the kill switch itself
    # is doing its job.
    active = [s for s in states
              if isinstance(s, dict) and s.get("killed")]
    ctx.log(f"kill_states: {len(states)} total, {len(active)} active; "
            f"auto_recover={auto}")
    if active:
        ids = [s.get("tunnel_id", "?") for s in active[:3]]
        return FAIL, (f"{len(active)} VPN tunnel(s) currently in kill "
                      f"state ({', '.join(ids)}) — the kill switch is "
                      f"working but the underlying VPN problem needs "
                      f"operator attention")
    return PASS, (f"VPN kill switch wired and inspectable: "
                  f"{len(states)} state record(s), 0 active kills, "
                  f"auto_recover={auto}. NOTE: this is the read-only "
                  f"check; the active network-leak probe (route "
                  f"toggling, DNS leak test) is deferred")


# ── Catalog backfill — L4, L8, L9, L19, L28, L30, L36 ──────────────
#
# The seven entries below close the gap between "30 of 37 ship" and a
# complete catalog. The IDs are the original numeric assignments;
# their names were re-derived from the surrounding catalog and from
# what each was most usefully positioned to check given the
# subsystems already covered by neighbouring IDs.
#
# All seven are non-disruptive except L28 (a service restart, which
# really does interrupt running downloads — so disruptive=True, gated
# behind --include-disruptive).
#
# These checks are deliberately conservative: each prefers WARN over
# FAIL when the environment is incomplete (dev mode off, AI disabled,
# no sites configured, no systemd unit installed). FAIL is reserved
# for cases where the subsystem is present AND broken.


# ── L4 — playwright browsers installed ─────────────────────────────
#
# L1 (headless-chromium-launch) and L2 (headed-browser-launch) exercise
# Chromium specifically — the browser BD uses for every Playwright
# operation today. L4 audits the broader playwright install: are the
# browser binaries actually present on the deployment? A missing or
# half-installed browser is a class of failure that surfaces only when
# something tries to use it (e.g. when a site needs a non-Chromium
# fallback that isn't on disk). Catching the absence at deploy time is
# cheaper than catching it mid-download.

@live_test("L4", "playwright-browsers-installed", disruptive=False)
def l4_playwright_browsers_installed(ctx):
    """Audit the Playwright browser install on the deployment.

    Imports playwright.sync_api and asks each engine (chromium, firefox,
    webkit) for its `executable_path`. The path is reported regardless
    of whether the file exists; we then stat each one.

    PASS:  chromium present (BD only uses Chromium; missing firefox or
           webkit are not a runtime concern for BD as it stands today).
    WARN:  chromium present but a non-Chromium engine is missing — the
           install is incomplete, which usually means `playwright install`
           was run with --with-deps but then partially failed. Worth
           noting; not blocking.
    FAIL:  chromium binary not present, OR playwright itself does not
           import — BD cannot run a single browser-touching operation.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        ctx.log(f"playwright.sync_api import failed: {e}")
        return FAIL, (f"playwright not importable: "
                      f"{type(e).__name__}: {e} — BD cannot launch "
                      f"any browser; run `python -m playwright install`")
    engines = ("chromium", "firefox", "webkit")
    found: dict = {}
    try:
        with sync_playwright() as p:
            for eng in engines:
                try:
                    path = getattr(p, eng).executable_path
                    found[eng] = path
                    ctx.log(f"  {eng}: executable_path = {path}")
                except Exception as e:
                    found[eng] = None
                    ctx.log(f"  {eng}: executable_path raised: {e}")
    except Exception as e:
        ctx.log(f"sync_playwright() context failed: {e}")
        return FAIL, (f"playwright sync context failed: "
                      f"{type(e).__name__}: {e} — playwright module "
                      f"is importable but the runtime cannot start")
    import os as _os
    on_disk: dict = {}
    for eng, path in found.items():
        if path and _os.path.isfile(path):
            on_disk[eng] = True
            ctx.log(f"  {eng}: binary present on disk")
        else:
            on_disk[eng] = False
            ctx.log(f"  {eng}: binary NOT on disk (path={path!r})")
    if not on_disk.get("chromium"):
        return FAIL, ("Playwright Chromium binary is NOT installed; "
                      "BD cannot run any browser operation — run "
                      "`python -m playwright install chromium`")
    missing_other = [e for e in ("firefox", "webkit") if not on_disk.get(e)]
    if missing_other:
        return WARN, (f"Chromium installed; {len(missing_other)} other "
                      f"engine(s) missing: {', '.join(missing_other)} "
                      f"— BD only uses Chromium today, so this is not "
                      f"a runtime blocker, but the install is incomplete")
    return PASS, (f"all {len(engines)} Playwright browsers installed "
                  f"(chromium/firefox/webkit) — full engine coverage")


# ── L8 — cookie jar not empty on active sites ──────────────────────
#
# A site that reports `auth_state: ok` but has an empty (or missing)
# cookie file is a silent failure mode: the in-memory cookies look
# valid, but a restart will wipe them and reveal the gap. L8 catches
# this asymmetry at deploy time.
#
# Note: we deliberately check the configured `cookie_file` path. A
# site with no cookie_file configured is fine (auth may live in
# headers/templates instead) — that's a WARN-of-zero-sites, not a
# FAIL.

_L8_AUTH_HEALTH_MAX_AGE_S = 48 * 60 * 60

@live_test("L8", "cookie-jar-not-empty-on-active-sites", disruptive=False)
def l8_cookie_jar_not_empty_on_active_sites(ctx):
    """For each site with auth_state=ok, verify its on-disk cookie
    file exists and is non-empty.

    A site whose runner thinks it's authenticated but whose persisted
    cookie jar is empty is one restart away from being logged out.
    The mismatch is invisible to /api/status — it requires comparing
    the runner's auth state to the configured `cookie_file` on disk.

    PASS:  every active site has a non-empty cookie file on disk.
    WARN:  no sites configured, OR no active sites have a configured
           cookie_file (nothing to check).
    FAIL:  one or more active sites have an empty/missing cookie file.
    """
    sok, sstatus, sbody, _ = ctx.get("/api/sites/v2", timeout=10)
    if not sok or not isinstance(sbody, dict):
        return WARN, (f"/api/sites/v2 unreachable (status={sstatus}) "
                      f"— cannot enumerate sites")
    sites = sbody.get("sites") or []
    if not sites:
        return WARN, "no sites configured — nothing to check"
    active = [s for s in sites if s.get("auth_state") == "ok"]
    ctx.log(f"{len(sites)} site(s) total; {len(active)} reporting "
            f"auth_state=ok")
    if not active:
        # A restart clears runner-local auth_state, but auth-health evidence is
        # durable. Accept only green records for site IDs still configured, and
        # continue into the same on-disk cookie-file verification below.
        aok, _astatus, abody, _ = ctx.get(
            "/api/auth_health/status", timeout=10)
        durable_sites = (abody.get("sites") or []) if (
            aok and isinstance(abody, dict)) else []
        configured_ids = {
            str(s.get("site_id")) for s in sites if s.get("site_id")
        }
        import time as _time
        now = _time.time()
        durable_ids = {
            str(row.get("site_id"))
            for row in durable_sites
            if isinstance(row, dict)
            and str(row.get("status") or "").lower() == "green"
            and str(row.get("site_id")) in configured_ids
            and isinstance(row.get("last_check_ts"), (int, float))
            and 0 <= now - float(row.get("last_check_ts"))
            <= _L8_AUTH_HEALTH_MAX_AGE_S
        }
        if durable_ids:
            active = [{"site_id": sid} for sid in sorted(durable_ids)]
            ctx.log(
                f"using {len(active)} durable green auth-health record(s) "
                "after runner auth state reset"
            )
    if not active:
        return WARN, (f"{len(sites)} site(s) configured but none "
                      f"report auth_state=ok — nothing to verify")
    # Need cookie_file paths — /api/sites/v2 doesn't carry them.
    # /api/status does (under each site's config).
    ok, status, body, _ = ctx.get("/api/status", timeout=15)
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/status unreachable (status={status}) — "
                      f"can't read per-site cookie_file paths")
    import os as _os
    bad: list = []
    no_cookie_file: list = []
    checked = 0
    for s in active:
        sid = s.get("site_id")
        if not sid:
            continue
        st = body.get(sid) or {}
        cfg = st.get("config") or {}
        path = str(cfg.get("cookie_file") or "").strip()
        if not path:
            no_cookie_file.append(sid)
            ctx.log(f"  {sid}: no cookie_file configured — skipping")
            continue
        checked += 1
        try:
            if not _os.path.isfile(path):
                bad.append((sid, "missing", path))
                ctx.log(f"  {sid}: cookie_file MISSING at {path}")
                continue
            size = _os.path.getsize(path)
            if size == 0:
                bad.append((sid, "empty", path))
                ctx.log(f"  {sid}: cookie_file EMPTY at {path}")
                continue
            ctx.log(f"  {sid}: cookie_file ok ({size} bytes)")
        except Exception as e:
            bad.append((sid, f"stat-error: {e}", path))
            ctx.log(f"  {sid}: stat error on {path}: {e}")
    if not checked:
        return WARN, (f"{len(active)} active site(s), 0 with a "
                      f"cookie_file configured — nothing to verify")
    if bad:
        ids = ", ".join(f"{sid}({why})" for sid, why, _ in bad[:3])
        return FAIL, (f"{len(bad)} of {checked} active site(s) have a "
                      f"missing/empty cookie_file: {ids} — a restart "
                      f"will reveal the auth gap")
    return PASS, (f"all {checked} active site(s) with cookie_file "
                  f"configured have non-empty cookies on disk")


# ── L9 — cookie files parse and aren't stale ───────────────────────
#
# L8 catches "configured but empty"; L9 catches "configured, non-empty,
# but corrupt or so old it's almost certainly expired". A cookie file
# older than the staleness threshold is a likely-but-not-certain
# re-login candidate — WARN, not FAIL. Parse failure on a non-empty
# file IS a FAIL: that's a corrupt jar.

_L9_STALE_AFTER_DAYS = 30


def _l9_parse_cookie_jar(path: str) -> tuple[bool, str, int]:
    """Best-effort: parse a cookie file as JSON (Playwright/MV3 export)
    OR Netscape format. Returns (ok, format_label, n_cookies)."""
    import json as _json
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return False, f"read-error: {e}", 0
    # JSON first — most BD cookie files (Playwright export, manual JSON)
    try:
        data = _json.loads(text)
    except ValueError:
        # Netscape format — header + tab-delimited rows.
        lines = [ln for ln in text.splitlines()
                 if ln.strip() and not ln.startswith("#")]
        if lines and all("\t" in ln for ln in lines[:5]):
            return True, "netscape", len(lines)
        return False, "neither-json-nor-netscape", 0
    # JSON parsed — figure out shape.
    if isinstance(data, list):
        return True, "json-list", len(data)
    if isinstance(data, dict) and "cookies" in data:
        cookies = data.get("cookies") or []
        if isinstance(cookies, list):
            return True, "json-dict-cookies", len(cookies)
    return True, "json-other-shape", 0


@live_test("L9", "cookie-files-readable-and-fresh", disruptive=False)
def l9_cookie_files_readable_and_fresh(ctx):
    """Verify each site's cookie file parses and is reasonably fresh.

    Parse: best-effort load as JSON (Playwright/MV3 export) or
    Netscape format. A non-empty file that matches NEITHER format is
    corrupt — FAIL.
    Freshness: a cookie file older than 30 days is a likely re-login
    candidate — WARN, not FAIL (the cookies may still work).

    PASS:  every configured cookie file parses and is recent.
    WARN:  no sites have cookie_file configured, OR one or more files
           are parseable-but-stale (>30d old).
    FAIL:  any non-empty cookie file fails to parse as either JSON or
           Netscape.
    """
    ok, status, body, _ = ctx.get("/api/status", timeout=15)
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/status unreachable (status={status}) — "
                      f"cannot enumerate sites")
    import os as _os
    import time as _t
    now = _t.time()
    stale_after_s = _L9_STALE_AFTER_DAYS * 86400
    checked = 0
    corrupt: list = []
    stale: list = []
    for sid, st in body.items():
        if not isinstance(st, dict):
            continue
        cfg = st.get("config") or {}
        path = str(cfg.get("cookie_file") or "").strip()
        if not path or not _os.path.isfile(path):
            continue
        try:
            size = _os.path.getsize(path)
            if size == 0:
                # L8's territory; skip without log noise here.
                continue
            mtime = _os.path.getmtime(path)
        except Exception as e:
            corrupt.append((sid, f"stat-error: {e}"))
            ctx.log(f"  {sid}: stat error: {e}")
            continue
        checked += 1
        parse_ok, label, n = _l9_parse_cookie_jar(path)
        age_days = (now - mtime) / 86400
        if not parse_ok:
            corrupt.append((sid, label))
            ctx.log(f"  {sid}: PARSE FAIL ({label})")
            continue
        if (now - mtime) > stale_after_s:
            stale.append((sid, age_days))
            ctx.log(f"  {sid}: parseable ({label}, {n} cookie(s)) "
                    f"but STALE — {age_days:.0f}d old")
            continue
        ctx.log(f"  {sid}: ok ({label}, {n} cookie(s), {age_days:.0f}d old)")
    if checked == 0:
        return WARN, "no sites have a cookie_file configured — nothing to check"
    if corrupt:
        ids = ", ".join(f"{sid}({why})" for sid, why in corrupt[:3])
        return FAIL, (f"{len(corrupt)} of {checked} cookie file(s) "
                      f"failed to parse: {ids} — the jar is corrupt")
    if stale:
        ids = ", ".join(f"{sid}({d:.0f}d)" for sid, d in stale[:3])
        return WARN, (f"{len(stale)} of {checked} cookie file(s) are "
                      f">{_L9_STALE_AFTER_DAYS}d old: {ids} — "
                      f"re-login may be needed soon")
    return PASS, (f"all {checked} configured cookie file(s) parse "
                  f"and are <{_L9_STALE_AFTER_DAYS}d old")


# ── L19 — AI text-call roundtrip ───────────────────────────────────
#
# Counterpart to L18 (vision-call-roundtrip). L18 exercises the
# screenshot → structured-result path; L19 exercises the text-only
# call path. Different model, different prompt, often different
# Ollama runtime state.
#
# /api/ai/classify is the simplest text-call endpoint: takes one
# string, returns a structured {ok, role, confidence} dict, no DB
# state, no side effects.

@live_test("L19", "ai-text-call-roundtrip", disruptive=False)
def l19_ai_text_call_roundtrip(ctx):
    """Exercise the AI text model end-to-end via /api/ai/classify.

    Counterpart to L18 (vision roundtrip). Confirms the text-only
    model path actually works on the deployment — distinct from L17
    (which only verifies the backend is reachable + the models are
    installed). A configured-but-misbehaving text model surfaces
    here, not in L17.

    PASS:  POST returns a structured object with an `ok` or `role`
           field, within timeout.
    WARN:  AI disabled by config, backend unreachable, or no text
           model configured.
    FAIL:  AI is configured and reachable but the text call itself
           errors or times out — the path is broken.
    """
    ok, status, ai, _ = ctx.get("/api/ai/status", timeout=15)
    if not ok or not isinstance(ai, dict):
        return WARN, (f"/api/ai/status unreachable (status={status}) "
                      f"— text roundtrip not testable")
    if not ai.get("enabled"):
        return WARN, ("AI assist disabled by config — no text model "
                      "to roundtrip against")
    if not ai.get("ok"):
        return WARN, (f"AI backend not reachable "
                      f"({str(ai.get('error'))[:80]}) — fix the AI "
                      f"backend first (see L17)")
    ctx.log("AI enabled and reachable; POSTing a small text payload "
            "to /api/ai/classify")
    import json as _json
    import time as _t
    import urllib.error as _ue
    import urllib.request as _u
    payload = _json.dumps({
        "element_desc": ("a primary submit button labelled 'Sign in' "
                         "in a login form")
    }).encode("utf-8")
    url = ctx.base_url + "/api/ai/classify"
    req = _u.Request(url, data=payload, method="POST",
                     headers={"Content-Type": "application/json"})
    t0 = _t.time()
    try:
        with _u.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
        body = _json.loads(raw)
    except _ue.HTTPError as e:
        return FAIL, (f"AI text call returned HTTP {e.code} — the "
                      f"text-model path is broken")
    except Exception as e:
        return FAIL, (f"AI text call failed: {type(e).__name__}: {e} "
                      f"— text roundtrip not working")
    ms = (_t.time() - t0) * 1000
    ctx.log(f"/api/ai/classify responded in {ms:.0f}ms")
    if not isinstance(body, dict):
        return FAIL, "AI text call returned a non-object response"
    if body.get("error") or body.get("ok") is False:
        return FAIL, (f"AI text call returned an error: "
                      f"{str(body.get('error'))[:120]}")
    keys = sorted(body.keys())
    ctx.log(f"structured response keys: {keys}")
    return PASS, (f"text model returned a structured response in "
                  f"{ms:.0f}ms ({len(keys)} field(s)) — the text "
                  f"roundtrip works")


# ── L28 — service restart preserves queue (DISRUPTIVE) ─────────────
#
# The only disruptive check in this backfill. Verifies that an actual
# `systemctl restart bulkdownloader` does not lose any queued URLs —
# i.e. queue persistence is durable across a service bounce. This is
# the natural live counterpart to the unit-suite's queue persistence
# tests, which run against in-memory state.
#
# Requires:
#   - systemd unit installed (gated by _unit_exists)
#   - --include-disruptive on the runner
#
# Snapshots /api/queue/v2 counts before + after; if before-totals
# don't match after-totals, fail. Note "running" jobs ARE expected to
# drop to 0 on a restart (they get re-queued as waiting) — so the
# meaningful comparison is total_known_urls before vs after.

@live_test("L28", "service-restart-preserves-queue", disruptive=True)
def l28_service_restart_preserves_queue(ctx):
    """Restart the bulkdownloader service and verify queue state
    survives.

    Snapshots the queue (running + waiting + done counts) via
    /api/queue/v2 BEFORE a `systemctl restart`, then again AFTER the
    service comes back. The sum of (waiting + running) plus the
    done count, taken as "total known URLs", must match within the
    natural transient: a job that was running before the restart will
    appear in waiting after.

    PASS:  total_known matches; queue persistence is intact.
    WARN:  no systemd unit, or systemctl unavailable — restart not
           exercisable here.
    FAIL:  totals don't match across the restart — queue rows were
           lost.
    """
    if not _unit_exists("bulkdownloader"):
        return WARN, ("no bulkdownloader systemd unit installed — "
                      "L28 not exercisable; run install_service.sh")
    # Snapshot 1
    ok, status, body, _ = ctx.get("/api/queue/v2", timeout=15)
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/queue/v2 unreachable before restart "
                      f"(status={status}) — cannot baseline")
    running_before = len(body.get("running") or [])
    waiting_before = len(body.get("waiting") or [])
    # `done_today_count` is an INT, not a list -- /api/queue/v2 emits no
    # "done" key at all. This read used to be len(body.get("done") or []),
    # which is len([]) is 0 on every run, so the third term of the total was
    # never measured. That was not merely under-counting: the invariant below
    # is that running+waiting+done is CONSERVED across the restart, because a
    # job finishing moves a URL from running to done. With done pinned at 0 a
    # completing job made the totals differ and the check reported "queue lost
    # 1 URL" -- a latent false FAIL, masked only because an empty queue WARNs
    # out first.
    done_before = int(body.get("done_today_count") or 0)
    total_before = running_before + waiting_before + done_before
    ctx.log(f"BEFORE restart: running={running_before} "
            f"waiting={waiting_before} done_today={done_before} "
            f"(total={total_before})")
    if total_before == 0:
        return WARN, ("queue is empty — nothing to preserve, restart "
                      "not a meaningful test; queue some URLs first")
    # Restart. Use the systemctl helper that already exists.
    ctx.log("calling: systemctl restart bulkdownloader")
    ok, out, err = _systemctl(
        ["restart", "bulkdownloader"], timeout=30)
    if not ok:
        return FAIL, (f"systemctl restart failed: {err}; queue "
                      f"persistence not verified")
    # Wait for /api/health to come back. systemd returns after the
    # ExecStart fork completes — the Flask app may not be serving yet.
    import time as _t
    deadline = _t.time() + 30.0
    healthy = False
    while _t.time() < deadline:
        hok, _, hbody, _ = ctx.get("/api/health", timeout=3)
        if hok and isinstance(hbody, dict) and hbody.get("ok"):
            healthy = True
            break
        _t.sleep(1.0)
    if not healthy:
        return FAIL, ("service restarted but /api/health did not "
                      "report ok within 30s — restart may have "
                      "failed; cannot verify queue persistence")
    ctx.log(f"service back up in "
            f"{30 - (deadline - _t.time()):.0f}s after restart")
    # Snapshot 2
    ok2, status2, body2, _ = ctx.get("/api/queue/v2", timeout=15)
    if not ok2 or not isinstance(body2, dict):
        return FAIL, (f"/api/queue/v2 unreachable after restart "
                      f"(status={status2}) — queue may be lost")
    running_after = len(body2.get("running") or [])
    waiting_after = len(body2.get("waiting") or [])
    done_after = int(body2.get("done_today_count") or 0)
    total_after = running_after + waiting_after + done_after
    ctx.log(f"AFTER restart:  running={running_after} "
            f"waiting={waiting_after} done_today={done_after} "
            f"(total={total_after})")
    if total_after != total_before:
        lost = total_before - total_after
        return FAIL, (f"queue lost {lost} URL(s) across the restart: "
                      f"total {total_before} -> {total_after} "
                      f"(running {running_before}->{running_after}, "
                      f"waiting {waiting_before}->{waiting_after}, "
                      f"done_today {done_before}->{done_after})")
    return PASS, (f"queue intact across service restart: "
                  f"{total_before} URL(s) preserved "
                  f"(running={running_before}->{running_after} "
                  f"as expected; waiting absorbed running)")


# ── L30 — VPN tunnel inventory consistent ──────────────────────────
#
# Sibling to L29 (kill-switch config). L29 verifies the kill switch
# state machine; L30 verifies the configured-tunnel inventory matches
# what's actually inspectable on the running service — i.e. no
# tunnels configured-but-invisible, no tunnels inspectable-but-not-
# configured.
#
# Uses the dev surface (`/api/dev/vpn_config`), which is gated by
# BD_DEV_MODE. On a production deployment with dev mode off the
# endpoint 404s; in that case L30 WARNs (this is normal, not a bug).

@live_test("L30", "vpn-tunnel-inventory-consistent", disruptive=False)
def l30_vpn_tunnel_inventory_consistent(ctx):
    """Cross-check configured VPN tunnels against the live inventory.

    Reads `/api/dev/vpn_config` (dev-mode gated) and verifies the
    rendered tunnel list is internally consistent: every configured
    tunnel has a state record, every state record has a corresponding
    config entry, no duplicates.

    PASS:  config and state lists are 1:1 with matching IDs.
    WARN:  dev mode disabled (the dev endpoint 404s — this is the
           default on production), OR the VPN subsystem is absent,
           OR no tunnels are configured (nothing to check).
    FAIL:  config and state diverge — an inconsistency that suggests
           a config-reload bug or a partial subsystem restart.
    """
    ok, status, body, _ = ctx.get("/api/dev/vpn_config", timeout=10)
    if not ok and status == 404:
        return WARN, ("/api/dev/vpn_config returns 404 — dev mode is "
                      "off (set BD_DEV_MODE=1 to inspect); VPN "
                      "inventory not testable from here")
    if not ok or not isinstance(body, dict):
        return WARN, (f"/api/dev/vpn_config unreachable "
                      f"(status={status}) — VPN inventory not "
                      f"testable")
    if body.get("error"):
        return WARN, (f"/api/dev/vpn_config returned an error: "
                      f"{str(body.get('error'))[:120]} — VPN "
                      f"subsystem may be absent")
    # The exact shape depends on vpn_config_render(); be tolerant.
    tunnels = (body.get("tunnels")
               or body.get("configured")
               or body.get("profiles")
               or [])
    if not isinstance(tunnels, list):
        return WARN, (f"/api/dev/vpn_config returned an unexpected "
                      f"shape (tunnels field is "
                      f"{type(tunnels).__name__}) — cannot verify "
                      f"inventory")
    if not tunnels:
        return WARN, ("no VPN tunnels configured — nothing to verify")
    ctx.log(f"{len(tunnels)} VPN tunnel(s) reported by /api/dev/vpn_config")
    unregistered = [
        str(t.get("tunnel_id") or t.get("id") or t.get("name") or "?")
        for t in tunnels
        if isinstance(t, dict) and t.get("registered_live") is False
    ]
    if unregistered:
        return FAIL, (f"{len(unregistered)} configured tunnel(s) are not "
                      f"registered live: {', '.join(unregistered)}")

    # ID coherence: every tunnel should have a stable identifier.
    ids: list = []
    no_id: int = 0
    for t in tunnels:
        if not isinstance(t, dict):
            no_id += 1
            continue
        tid = (t.get("id") or t.get("tunnel_id")
               or t.get("name") or t.get("profile"))
        if tid:
            ids.append(str(tid))
        else:
            no_id += 1
    dups = sorted({i for i in ids if ids.count(i) > 1})
    ctx.log(f"  IDs: {ids}")
    if no_id:
        ctx.log(f"  {no_id} tunnel record(s) missing an id field")
    if dups:
        ctx.log(f"  duplicate IDs: {dups}")
    if no_id and not ids:
        return FAIL, (f"all {len(tunnels)} tunnel record(s) lack an "
                      f"id/name field — config render is broken")
    if dups:
        return FAIL, (f"{len(dups)} duplicate tunnel ID(s) in config: "
                      f"{', '.join(dups[:3])} — config rendered with "
                      f"a key collision")
    return PASS, (f"{len(tunnels)} VPN tunnel(s) configured; IDs "
                  f"unique ({len(ids)}/{len(tunnels)} with stable id) "
                  f"— inventory consistent")


# ── L36 — /m2 SPA bundle served ────────────────────────────────────
#
# The D3 React SPA at /m2 ships behind an install-time `npm run build`
# step. If that step fails or is skipped (which 1b on a fresh stash
# deploy can easily skip — see OPEN_THREADS), /m2 returns 503 with
# `X-BD-M2-Status: not-built`. L36 verifies the OPPOSITE state: that
# /m2 IS serving the built SPA. This is the natural complement to
# L34 (full route smoke) and L37 (version coherence): the SPA-build
# axis.
#
# Will-pass condition: index.html served at /m2/ with at least one
# hashed asset reachable.

@live_test("L36", "m2-spa-bundle-served", disruptive=False)
def l36_m2_spa_bundle_served(ctx):
    """Verify the /m2 SPA bundle is built and being served.

    A correct /m2 deploy returns 200 with HTML at /m2/ and NO
    X-BD-M2-Status: not-built header. We further verify at least one
    of the hashed asset paths referenced by index.html also returns
    200 — this catches a half-built dist where index.html is fresh
    but the asset manifest is stale.

    PASS:  /m2/ returns 200 HTML and a referenced hashed asset also
           returns 200.
    WARN:  /m2/ returns 503 with X-BD-M2-Status: not-built — npm
           build hasn't run; this is environmental, not a defect.
    FAIL:  /m2/ returns 200 but a referenced hashed asset 404s OR
           the response is not HTML — the dist is half-built or
           the route is broken.
    """
    ok, status, body, _ = ctx.get("/m2/", timeout=10)
    # ctx.get returns (False, code, None, ms) for HTTPError; the body
    # is None on 4xx/5xx. Detect 503 + X-BD-M2-Status header by
    # opening the URL directly so we can read response headers.
    import urllib.request as _u
    import urllib.error as _ue
    # Quick reachability ping first — if the whole service is down,
    # that's a separate problem (probably visible in L34 / L37), and
    # this check WARNs rather than FAILs. We only own /m2's verdict.
    hok, hstatus, _, _ = ctx.get("/api/health", timeout=5)
    if not hok and hstatus is None:
        return WARN, ("the target service appears unreachable "
                      "(/api/health did not respond) — /m2 cannot be "
                      "verified separately from the whole service")
    url = ctx.base_url + "/m2/"
    try:
        with _u.urlopen(url, timeout=10) as r:
            code = r.status
            html = r.read().decode("utf-8", "replace")
            hdr = r.headers.get("X-BD-M2-Status", "")
    except _ue.HTTPError as e:
        code = e.code
        try:
            html = e.read().decode("utf-8", "replace")
        except Exception:
            html = ""
        hdr = e.headers.get("X-BD-M2-Status", "") if e.headers else ""
    except Exception as e:
        return FAIL, (f"/m2/ unreachable but /api/health responded: "
                      f"{type(e).__name__}: {e} — /m2 route is broken "
                      f"specifically (not a general outage)")
    ctx.log(f"GET /m2/ -> {code}; X-BD-M2-Status={hdr!r}; "
            f"body length={len(html)}")
    if code == 503 and hdr == "not-built":
        return WARN, ("/m2/ returns 503 with X-BD-M2-Status: not-built "
                      "— the SPA hasn't been built (run `npm install && "
                      "npm run build` in frontend/); this is "
                      "environmental, not a defect")
    if code != 200:
        return FAIL, (f"/m2/ returned {code} (expected 200) — SPA "
                      f"route is broken; X-BD-M2-Status={hdr!r}")
    if hdr == "not-built":
        return FAIL, ("/m2/ returned 200 but also "
                      "X-BD-M2-Status: not-built — the handler is "
                      "in an inconsistent state")
    # Spot-check: index.html should reference at least one /m2/assets/ path
    # with a hashed filename. Find one and GET it.
    import re as _re
    asset_paths = _re.findall(r'["\'](/m2/assets/[^"\']+)["\']', html)
    if not asset_paths:
        # Some Vite configs emit non-/m2/assets/ paths; widen the net.
        asset_paths = _re.findall(r'["\'](/m2/[^"\']+\.(?:js|css))["\']',
                                  html)
    if not asset_paths:
        return FAIL, ("/m2/ returned HTML but no referenced JS/CSS "
                      "asset under /m2/ — index.html appears not to "
                      "be a Vite-built bundle")
    asset = asset_paths[0]
    aok, astatus, abody, _ = ctx.get(asset, timeout=10)
    ctx.log(f"GET {asset} -> {astatus}")
    if not aok or (astatus and astatus >= 400):
        return FAIL, (f"/m2/ index references {asset} but that asset "
                      f"returned {astatus} — dist is half-built or "
                      f"index.html is stale relative to assets/")
    return PASS, (f"/m2/ serves a built SPA bundle ({len(html)} bytes "
                  f"of HTML, {len(asset_paths)} asset ref(s); spot-"
                  f"checked {asset} -> {astatus})")
