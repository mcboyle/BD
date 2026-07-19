#!/usr/bin/env python3
"""nav_probe.py — read-only NAV diagnostic for the capture navigation blocker.

WHY THIS EXISTS
---------------
Interactive capture (``tools/capture_session.py``) wires a network recorder
(``session_capture.capture_via_cdp``) onto the page *before* navigating. On a
heavy SPA that recorder holds sub-resource requests open, so Playwright's default
``page.goto(url)`` (``wait_until="load"``) can time out even though the DOM is
already parsed and usable. ``_goto_or_continue_if_usable`` (D2(a)) tolerates that
specific case today by catching the ``TimeoutError`` and continuing if the DOM is
usable. The staged "N1" proposal is to navigate with a *weaker* wait condition
(``domcontentloaded`` / ``commit`` + self-poll) so the goto resolves cleanly
instead of always paying a full ``load`` timeout first.

That guard change must NOT land on assumption. This tool gathers the **stash
evidence** that says which wait condition actually reaches a usable DOM on a
representative failing URL, so the fix (and its declared guard-SHA change) is
driven by data, not by a guess. It is the first, *non-guard* step of the gated
chain: nav_probe (here) -> NAV fix (edits the guard, separate cut) -> F2.7 live.

VERDICTS (per F2_7_LIVE_WIRING_SPEC §D)
---------------------------------------
  LOAD_OK              ``wait_until="load"`` reaches a usable DOM. NAV is not a
                       blocker for this URL; no wait_until change is warranted.
  N1_DCL               ``load`` times out (unusable) but ``domcontentloaded``
                       reaches a usable DOM. Fix: goto(wait_until=DCL).
  N3_COMMIT_SELFPOLL   ``load`` and ``domcontentloaded`` both time out unusable,
                       but ``commit`` + a bounded readyState self-poll reaches a
                       usable DOM. Fix: goto(wait_until=commit) + poll-until-usable.
  UNREACHABLE          No strategy reaches a usable DOM, OR the first navigation
                       fails with a transport-level error (net::ERR_*). NAV is
                       NOT the blocker — investigate auth / cert / redirect / DNS.
                       F2.7-live stays parked.

The usability test is byte-for-byte the one in ``_goto_or_continue_if_usable``:
``document.readyState in ("interactive","complete")`` AND
``document.body.innerText.length >= 0``.

F2 / SAFETY POSTURE
-------------------
Strictly read-only. Navigates, reads ``readyState`` / body *length* / page title
/ current URL / error class only. It NEVER captures or prints page body text,
NEVER writes a file, NEVER persists cookies/secrets, and NEVER drives the page
beyond the navigation itself. The optional recorder attach uses a throwaway,
redacted ``DomCapture`` sink purely to reproduce the request-holding pressure;
its contents are discarded.

SANDBOX NOTE
------------
This is exercised on stash (real browser, real target). In the sandbox there is
no network and no live target, so only the pure decision logic (``classify`` and
``attempt_strategy``) is unit-tested on fakes (``tests/test_nav_probe.py``).
"""

import argparse
import json
import sys
import time

# --- usability contract (mirrors _goto_or_continue_if_usable exactly) ---------
_USABLE_READY = ("interactive", "complete")

# Substrings that mark a transport-level navigation failure (NOT a load timeout).
# Their presence on the first attempt means NAV is not the blocker.
_NET_ERROR_MARKERS = (
    "net::ERR_",
    "NS_ERROR_",
    "ERR_CONNECTION",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CERT",
    "ERR_SSL",
    "ERR_TOO_MANY_REDIRECTS",
    "ERR_ABORTED",
    "ERR_TIMED_OUT",  # connection-level timeout, distinct from a wait_until timeout
    "ERR_ADDRESS_UNREACHABLE",
)

# Strategy ladder, weakest-last is wrong — we go strongest-first so the SIMPLEST
# adequate fix wins: a clean ``load`` beats DCL beats commit+poll.
STRATEGIES = ("load", "domcontentloaded", "commit")

_VERDICT_FIX = {
    "LOAD_OK": "none — load reaches a usable DOM; do not change wait_until",
    "N1_DCL": "goto(wait_until='domcontentloaded') in _goto_or_continue_if_usable",
    "N3_COMMIT_SELFPOLL":
        "goto(wait_until='commit') + bounded readyState self-poll "
        "in _goto_or_continue_if_usable",
    "UNREACHABLE":
        "NAV is not the blocker — investigate transport (auth/cert/redirect/DNS); "
        "F2.7-live stays parked",
}


def is_usable(readystate, body_len):
    """The readyState/body usability predicate, identical to the guard's D2(a)
    test (``_goto_or_continue_if_usable``). NB: this is intentionally URL-blind to
    stay byte-identical to the guard; the attempt layer adds an off-about:blank
    gate on top so a non-committed navigation can't read as usable."""
    return (readystate in _USABLE_READY
            and isinstance(body_len, int)
            and body_len >= 0)


def _navigated_off_blank(current_url):
    """A fresh Playwright page is ``about:blank`` with readyState 'complete' and
    body length 0 — which would satisfy ``is_usable`` even though nothing loaded.
    Require the page to have actually navigated to a real document first."""
    if not current_url:
        return False
    u = current_url.lower()
    return not (u == "about:blank" or u.startswith("about:")
                or u.startswith("chrome-error:") or u == "")


def _usable_for_target(readystate, body_len, current_url):
    return is_usable(readystate, body_len) and _navigated_off_blank(current_url)


def _is_net_error(err_text):
    if not err_text:
        return False
    return any(m in err_text for m in _NET_ERROR_MARKERS)


def _is_timeout_error(err_text):
    return bool(err_text) and ("Timeout" in err_text or "TimeoutError" in err_text)


def attempt_strategy(strategy, goto_fn, read_state_fn, *,
                     timeout_ms, poll_ms=0, sleep_fn=time.sleep,
                     poll_interval_ms=250):
    """Run ONE navigation strategy and report its outcome.

    Outcome semantics (the part that makes the verdict mean something):
      * 'usable' requires the wait condition to have **resolved** — i.e. for
        ``load``/``domcontentloaded`` the ``goto_fn`` RETURNED NORMALLY (no
        timeout) AND the DOM is usable AND we are off about:blank. A goto that
        *times out* never counts as 'usable' for that strategy even if the DOM is
        incidentally usable, because the whole point is to learn which wait
        condition resolves cleanly (that is the fix to land).
      * ``commit`` is the exception: ``goto(commit)`` resolves almost immediately
        (response received), so after a normal commit we self-poll readyState up
        to ``poll_ms`` and call it 'usable' when the DOM becomes usable.
      * 'net_error' on the first attempt is decisive (transport failure).

    Injected callables make this fully testable without Playwright:
      * ``goto_fn(strategy, timeout_ms)`` -> navigates; returns normally on a
        resolved wait, raises an exception whose str contains 'Timeout' for a
        wait_until timeout or a 'net::ERR_*' marker for a transport failure.
      * ``read_state_fn()`` -> ``(readystate, body_len, title, current_url)``;
        also polled during the commit self-poll.

    Returns a dict:
      strategy, outcome ('usable'|'timeout_unusable'|'net_error'),
      resolved (bool), readyState, body_len, title, current_url, error,
      elapsed_ms, polls
    """
    rec = {"strategy": strategy, "outcome": None, "resolved": False,
           "readyState": None, "body_len": -1, "title": "", "current_url": "",
           "error": "", "elapsed_ms": 0, "polls": 0}
    t0 = time.monotonic()
    try:
        goto_fn(strategy, timeout_ms)
        resolved = True
        goto_err = ""
    except Exception as exc:  # noqa: BLE001 — we classify the message, not the type
        resolved = False
        goto_err = type(exc).__name__ + ": " + str(exc)
    rec["resolved"] = resolved

    # A transport-level error is decisive on its own — no point probing readiness.
    if not resolved and _is_net_error(goto_err):
        rec["outcome"] = "net_error"
        rec["error"] = goto_err[:300]
        rec["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return rec

    readystate, body_len, title, current_url = _safe_read(read_state_fn)
    rec.update(readyState=readystate, body_len=body_len,
               title=(title or "")[:200], current_url=(current_url or "")[:500])

    # load / domcontentloaded: 'usable' ONLY if the wait condition resolved.
    if strategy != "commit":
        if resolved and _usable_for_target(readystate, body_len, current_url):
            rec["outcome"] = "usable"
        else:
            rec["outcome"] = "timeout_unusable"
            if goto_err:
                rec["error"] = goto_err[:300]
        rec["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return rec

    # commit: usable now, or self-poll until usable / deadline.
    if _usable_for_target(readystate, body_len, current_url):
        rec["outcome"] = "usable"
        rec["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return rec
    if poll_ms > 0:
        deadline = t0 + (poll_ms / 1000.0)
        step = max(poll_interval_ms, 1) / 1000.0
        while time.monotonic() < deadline:
            sleep_fn(step)
            rec["polls"] += 1
            readystate, body_len, title, current_url = _safe_read(read_state_fn)
            rec.update(readyState=readystate, body_len=body_len,
                       title=(title or "")[:200],
                       current_url=(current_url or "")[:500])
            if _usable_for_target(readystate, body_len, current_url):
                rec["outcome"] = "usable"
                rec["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
                return rec

    rec["outcome"] = "timeout_unusable"
    if goto_err:
        rec["error"] = goto_err[:300]
    rec["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return rec


def _safe_read(read_state_fn):
    """Never let a failed evaluate() crash the probe — fall back to not-usable."""
    try:
        rs, bl, ti, cu = read_state_fn()
    except Exception:
        return None, -1, "", ""
    if not isinstance(bl, int):
        bl = -1
    return rs, bl, ti, cu


def classify(attempts):
    """Map an ordered list of attempt records to a verdict.

    Order is assumed: load, then domcontentloaded, then commit (any may be
    absent if a prior attempt already produced a usable DOM or a net_error).
    """
    by_strategy = {a["strategy"]: a for a in attempts}

    # Decisive transport failure on the FIRST attempt -> not a nav-wait problem.
    if attempts and attempts[0]["outcome"] == "net_error":
        return "UNREACHABLE"

    load = by_strategy.get("load")
    if load and load["outcome"] == "usable":
        return "LOAD_OK"

    dcl = by_strategy.get("domcontentloaded")
    if dcl and dcl["outcome"] == "usable":
        return "N1_DCL"

    commit = by_strategy.get("commit")
    if commit and commit["outcome"] == "usable":
        return "N3_COMMIT_SELFPOLL"

    return "UNREACHABLE"


def signals_from(attempts):
    """Pull the most-informative readiness/error signals for the report."""
    last = attempts[-1] if attempts else {}
    net_err = ""
    for a in attempts:
        if a.get("outcome") == "net_error":
            net_err = a.get("error", "")
            break
    return {
        "readyState": last.get("readyState"),
        "body_len": last.get("body_len", -1),
        "title": last.get("title", ""),
        "final_url": last.get("current_url", ""),
        "net_error": net_err,
    }


# --- Playwright driving layer (stash-only; not unit-tested) -------------------

def _bd_version():
    try:
        from bulk_downloader import __version__
        return __version__
    except Exception:
        return "unknown"


def probe(url, *, timeout_ms=30000, poll_ms=15000, chrome=None,
          headed=False, system_chrome=False, attach_recorder=True,
          profile_dir=None):
    """Drive a real browser through the strategy ladder and return the report.

    Each strategy runs on a FRESH page/context so attached recorders and prior
    navigation state do not bleed between attempts. Stash-only.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        print("ERROR: Playwright is required (pip install playwright && "
              "playwright install chromium).", file=sys.stderr)
        return None, 2

    report = {
        "tool": "nav_probe",
        "bd_version": _bd_version(),
        "url": url,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "recorder_attached": False,
        "backend": None,
        "timeout_ms": timeout_ms,
        "poll_ms": poll_ms,
        "attempts": [],
    }

    attempts = []
    pw = closer = None
    try:
        pw, backend, new_ctx, closer = _launch(chrome, headed, system_chrome)
        report["backend"] = backend
    except Exception as exc:  # noqa: BLE001
        print("ERROR: could not launch a browser: %s" % (str(exc)[:200],),
              file=sys.stderr)
        return None, 2

    try:
        for strategy in STRATEGIES:
            ctx = new_ctx()
            page = ctx.new_page()
            if attach_recorder:
                report["recorder_attached"] = _try_attach_recorder(ctx, page, url)

            def _goto(strat, t_ms, _page=page, _url=url):
                _page.goto(_url, wait_until=strat, timeout=t_ms)

            def _read(_page=page):
                rs = _page.evaluate("document.readyState")
                bl = _page.evaluate(
                    "(document.body && document.body.innerText)"
                    " ? document.body.innerText.length : -1")
                ti = _page.evaluate("document.title")
                cu = _page.url
                return rs, bl, ti, cu

            rec = attempt_strategy(
                strategy, _goto, _read,
                timeout_ms=timeout_ms,
                poll_ms=(poll_ms if strategy == "commit" else 0))
            attempts.append(rec)
            try:
                ctx.close()
            except Exception:
                pass
            if rec["outcome"] == "usable":
                break
            if strategy == "load" and rec["outcome"] == "net_error":
                break  # transport failure -> UNREACHABLE, no point continuing
    finally:
        try:
            if closer:
                closer()
        except Exception:
            pass

    report["attempts"] = attempts
    report["verdict"] = classify(attempts)
    report["recommended_fix"] = _VERDICT_FIX[report["verdict"]]
    report["signals"] = signals_from(attempts)
    return report, _exit_code(report["verdict"])


def _launch(chrome, headed, system_chrome):
    """Prefer the BD cloak wrapper (honours the configured backend on stash);
    fall back to a plain Playwright chromium with an explicit executable_path."""
    headless = not headed
    try:
        from bulk_downloader import cloak as _cloak
        launch_extra = {}
        if system_chrome:
            launch_extra["channel"] = "chrome"
        browser, pw, backend = _cloak.launch_browser(
            headless=headless, args=[], **launch_extra)

        def new_ctx():
            return browser.new_context()

        def closer():
            try:
                browser.close()
            finally:
                pw.stop()

        return pw, backend, new_ctx, closer
    except Exception:
        # Bare Playwright path (sandbox / no cloak). executable_path required —
        # the default headless-shell is not installed.
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        launch_kw = {"headless": headless}
        if chrome:
            launch_kw["executable_path"] = chrome
        browser = pw.chromium.launch(**launch_kw)

        def new_ctx():
            return browser.new_context()

        def closer():
            try:
                browser.close()
            finally:
                pw.stop()

        return pw, "playwright", new_ctx, closer


def _try_attach_recorder(ctx, page, url):
    """Reproduce the request-holding pressure with a throwaway redacted sink.

    Returns True if the network recorder wired; contents are discarded. Best
    effort — a failure here simply means the probe runs without recorder
    pressure (and says so), never a crash.
    """
    try:
        from bulk_downloader.dom_capture import DomCapture
        from tools.capture_session import _attach_recorders
        sink = DomCapture(url=url, redact=True)
        _attach_recorders(ctx, page, sink, redact=True)
        return True
    except Exception:
        return False


def _exit_code(verdict):
    # 0  -> a wait condition reaches a usable DOM (or no blocker at all)
    # 3  -> UNREACHABLE (nav is not the blocker; needs operator investigation)
    return 0 if verdict in ("LOAD_OK", "N1_DCL", "N3_COMMIT_SELFPOLL") else 3


def _print_human(report):
    v = report["verdict"]
    sig = report["signals"]
    out = sys.stderr
    out.write("\n=== nav_probe verdict: %s ===\n" % v)
    out.write("  url               : %s\n" % report["url"])
    out.write("  backend           : %s\n" % report.get("backend"))
    out.write("  recorder attached : %s\n" % report.get("recorder_attached"))
    out.write("  recommended fix   : %s\n" % report["recommended_fix"])
    out.write("  final readyState  : %r  body_len=%s  title=%r\n"
              % (sig.get("readyState"), sig.get("body_len"), sig.get("title")))
    if sig.get("net_error"):
        out.write("  transport error   : %s\n" % sig["net_error"])
    out.write("  attempts:\n")
    for a in report["attempts"]:
        out.write("    - %-16s outcome=%-16s readyState=%-12r "
                  "body_len=%-5s polls=%s elapsed_ms=%s%s\n"
                  % (a["strategy"], a["outcome"], a["readyState"],
                     a["body_len"], a["polls"], a["elapsed_ms"],
                     ("  err=" + a["error"]) if a.get("error") else ""))
    out.write("\n")


def _build_parser():
    p = argparse.ArgumentParser(
        prog="nav_probe.py",
        description="Read-only NAV diagnostic: classify why a capture URL fails "
                    "to reach a usable DOM (LOAD_OK / N1_DCL / N3_COMMIT_SELFPOLL "
                    "/ UNREACHABLE). Drives a real browser; stash-only.")
    p.add_argument("url", help="the failing capture URL to probe")
    p.add_argument("--timeout-ms", type=int, default=30000,
                   help="per-strategy wait_until timeout in ms (default 30000)")
    p.add_argument("--poll-ms", type=int, default=15000,
                   help="commit self-poll budget in ms (default 15000)")
    p.add_argument("--chrome", default=None,
                   help="explicit Chromium executable_path (bare-Playwright path)")
    p.add_argument("--headed", action="store_true",
                   help="run headed (default headless; the load-timeout symptom "
                        "is recorder-driven, not display-driven)")
    p.add_argument("--system-chrome", action="store_true",
                   help="use the system Chrome channel via the cloak wrapper")
    p.add_argument("--no-recorder", action="store_true",
                   help="do NOT attach the capture recorder (skip reproducing "
                        "the request-holding pressure)")
    p.add_argument("--profile-dir", default=None,
                   help="reserved; persistent-profile probing is not yet wired")
    p.add_argument("--json", action="store_true",
                   help="print the full JSON report to stdout")
    return p


def run(argv=None):
    args = _build_parser().parse_args(argv)
    report, code = probe(
        args.url,
        timeout_ms=args.timeout_ms,
        poll_ms=args.poll_ms,
        chrome=args.chrome,
        headed=args.headed,
        system_chrome=args.system_chrome,
        attach_recorder=not args.no_recorder,
        profile_dir=args.profile_dir)
    if report is None:
        return code
    _print_human(report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(run())
