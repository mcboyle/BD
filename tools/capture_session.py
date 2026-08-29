#!/usr/bin/env python3
"""capture_session.py — operator CLI for A-T1/A-T2 session capture.

Drives a live browsing session capture and writes a WACZ-compatible
archive. This is the **live leg** of A-T1/A-T2: it needs a real browser
(Playwright Chromium, or the operator's system Chrome via CDP) and an
interactive authenticated session, so it runs on the operator's machine,
not in the sandbox. The capture/redaction/export logic it calls is fully
unit-tested in bulk_downloader.{session_capture,dom_capture,wacz_export};
this script is the thin wiring + operator ergonomics around it.

Usage:
    python3 tools/capture_session.py --url https://members.example.com \\
        --out capture.wacz [--system-chrome] \\
        [--body-cap-mib 1] [--chunk-events 10000]

Workflow:
    1. Launch (bundled Chromium, or attach to system Chrome with
       --system-chrome for TLS-fingerprint-coherent capture — defeats
       TLS-fingerprinting anti-bot at the capture stage).
    2. Operator logs in and performs the demonstration (login + N
       downloads) in the opened browser.
    3. Press ENTER in this terminal to stop capture.
    4. The capture is redacted at capture time and written as a WACZ. A
       digest self-check runs before exit.

Capture-time redaction is ALWAYS ON in the release — there is no flag here
to disable it, so a released build cannot write an unredacted WACZ. A raw
(unredacted) capture for local troubleshooting is available ONLY when the
separate dev package ``bd_dev_inspect`` is installed alongside and
``BD_CAPTURE_RAW=1`` is set; this script soft-imports that package if present
(absent in the release → no effect). A raw capture is stamped ``_UNREDACTED``.

Posture: capture + redact + export only. Records signed/short-lived URLs
with signing params redacted; never replays or reconstructs them.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

# Repo-root bootstrap so `bulk_downloader` imports whether run from the
# repo root or from tools/ (mirrors tools/ct1_synth.py).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _emit_capture_event(event: str, payload: dict) -> None:
    """Fire a capture-lifecycle hook (E1: capture.started / capture.done).

    Best-effort + fully isolated: a missing plugins module or a throwing
    consumer must never break a capture run. ``plugins.emit`` already isolates
    each consumer; this wrapper additionally guards the import so the producer
    is robust even if the plugin subsystem is unavailable.
    """
    try:
        from bulk_downloader import plugins as _pl
        _pl.emit(event, payload)
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A-T1/A-T2 session capture")
    p.add_argument("--url", required=True, help="Starting page URL")
    p.add_argument("--out", required=True, help="Output .wacz path")
    p.add_argument("--system-chrome", action="store_true",
                   help="Attach to the operator's installed Chrome via CDP "
                        "(matches operator TLS fingerprint) instead of "
                        "bundled Chromium")
    p.add_argument("--body-cap-mib", type=int, default=1,
                   help="Per-response body capture cap in MiB (default 1)")
    p.add_argument("--chunk-events", type=int, default=10000,
                   help="Event count per capture chunk (default 10000)")
    # Plan-C ergonomics (all posture-clean — they change where the browser
    # PROFILE lives and where the operator lands, never what gets captured or
    # redacted; the redaction seam below is untouched):
    p.add_argument("--profile-dir", default=None,
                   help="Persist the browser profile at this dir "
                        "(launch_persistent_context). Cookies/session persist "
                        "between captures so the login form mostly does not "
                        "reappear; a password manager or Chrome's own autofill "
                        "set up once in this profile persists too. Omit for the "
                        "old behaviour (fresh throwaway browser each run).")
    p.add_argument("--autofill", action="store_true",
                   help="Enable Chromium's native password autofill in the "
                        "persistent profile (--password-store=basic). Only "
                        "meaningful with --profile-dir. The operator's saved "
                        "passwords fill the login form; the operator still "
                        "submits by hand. No credential is read or stored by "
                        "this tool.")
    p.add_argument("--title", default=None,
                   help="Logical title name for this capture. On a SECOND (or "
                        "later) capture of the same --title, the tool navigates "
                        "straight back to the page URL captured the first time "
                        "(query-stripped) instead of --url, so the diff pair "
                        "starts from the same page. First capture of a title "
                        "records its page URL; --url is used when no record "
                        "exists yet.")
    p.add_argument("--url-memory-file", default="capture_url_memory.json",
                   help="Local JSON file holding the title -> last page URL map "
                        "for --title navigation (default ./capture_url_memory.json). "
                        "Page URLs only, query-stripped; no signing material.")
    # Non-interactive finish (cockpit / noVNC path). When there is no terminal
    # to press ENTER in, the browser stays open and the tool waits for a
    # sentinel file the operator drops from a second shell. These flags only
    # control WHEN capture stops and whether the WACZ is written — never what
    # is captured or redacted (the redaction seam is untouched).
    p.add_argument("--max-seconds", type=int, default=1500,
                   help="Non-interactive only: auto-save after this many seconds "
                        "if no FINISH/CANCEL sentinel appears (default 1500, kept "
                        "below the cockpit runner's 1800s kill so the capture is "
                        "saved gracefully instead of being killed mid-write).")
    p.add_argument("--finish-file", default=None,
                   help="Non-interactive only: explicit path to the FINISH "
                        "sentinel (default <out_dir>/FINISH, next to --out).")
    p.add_argument("--no-hud", action="store_true",
                   help="Disable the decorative capture HUD for this run. The "
                        "HUD is ON by default: a render-only, closed-Shadow-DOM "
                        "status panel mounted via the page's isolated world "
                        "(CSP-immune) and never recorded into the WACZ. Also "
                        "disabled globally via BD_HUD_OVERLAY=0.")
    return p


def _hud_enabled(args, env=None) -> bool:
    """Resolve whether the decorative HUD mounts for this capture.

    DEFAULT ON. Disabled per-capture by ``--no-hud`` (the GUI checkbox unticked)
    or globally by ``BD_HUD_OVERLAY=0`` in the environment. ``BD_HUD_OVERLAY``
    unset, or any value other than ``"0"``, leaves the HUD on.

    v3.66.316 (CLI->GUI parity, guard cut): the global_config store key
    ``hud_overlay`` (bool) overrides the env seed when set, so a Settings write
    takes effect on the next capture. Read at call time, store > env seed >
    default; lazy import, fail-safe to the env path. The per-capture ``--no-hud``
    flag always wins regardless.
    """
    env = os.environ if env is None else env
    store_on = None
    try:
        from bulk_downloader import global_config as _gc
        _sv = _gc.get("hud_overlay", None)
        if _sv is not None:
            store_on = bool(_sv)
    except Exception:
        pass
    hud_on = store_on if store_on is not None else (env.get("BD_HUD_OVERLAY", "1") != "0")
    return hud_on and not getattr(args, "no_hud", False)


# ── Plan-C layer 3: per-title page-URL memory (query-stripped) ──────
def _strip_query(url: str) -> str:
    """Page URL with query and fragment removed — mirrors session_capture's
    _request_key rule (signing/query params are exactly what varies and must
    never be stored or echoed)."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        s = urlsplit(url)
        return urlunsplit((s.scheme, s.netloc, s.path, "", ""))
    except Exception:
        return url.split("?", 1)[0]


def _load_url_memory(path: str) -> dict:
    import json
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _remember_url(path: str, title: str, page_url: str) -> None:
    """Record title -> query-stripped page URL atomically (.tmp then replace)."""
    import json
    import os
    mem = _load_url_memory(path)
    mem[title] = _strip_query(page_url)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ── non-interactive finish: sentinel-file wait (cockpit / noVNC path) ──────
# A cockpit-launched capture runs as a subprocess with no controlling terminal
# (stdin is not a TTY), so the interactive ``input()`` below would get EOF and
# save instantly — the browser would flash open and closed before the operator
# could log in. In that case we keep the browser alive and poll for a small
# sentinel the operator drops from a second shell when done interacting in the
# noVNC pane:  ``touch <out_dir>/FINISH`` to save, ``.../CANCEL`` to discard.
# SIGTERM/SIGINT also finish gracefully. Bounded by --max-seconds so an
# abandoned capture still saves before the cockpit runner's 1800s kill.
_FINISH = {"signalled": False}


def _install_finish_signals() -> None:
    def _handler(signum, frame):  # pragma: no cover - signal delivery path
        _FINISH["signalled"] = True
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not main thread / unsupported — fall back to sentinel only


def _wait_for_finish(out_dir: Path, max_wait: float, finish_file=None,
                     on_tick=None) -> str:
    """Poll for a finish/cancel sentinel (or a SIGTERM/SIGINT, or timeout).

    Returns one of ``"finish"`` | ``"cancel"`` | ``"signal"`` | ``"timeout"``.
    Pure and poll-based so it is unit-testable without a real browser. If
    ``on_tick`` is given it is invoked once per poll iteration (best-effort,
    exceptions swallowed) — capture_session uses it to drain buffered DOM
    events off the page while the operator drives the session.
    """
    finish = Path(finish_file) if finish_file else (out_dir / "FINISH")
    # CANCEL is the sibling of the finish sentinel, so a per-capture
    # --finish-file also scopes the discard signal (no shared CANCEL race for
    # concurrent captures in one dir). Default (no --finish-file): out_dir/CANCEL.
    cancel = Path(finish_file).with_suffix(".CANCEL") if finish_file else (out_dir / "CANCEL")
    deadline = time.time() + max(1.0, float(max_wait))
    while time.time() < deadline:
        if _FINISH["signalled"]:
            return "signal"
        try:
            if finish.exists():
                return "finish"
            if cancel.exists():
                return "cancel"
        except OSError:
            pass
        if on_tick is not None:
            try:
                on_tick()
            except Exception:
                pass
        time.sleep(1.0)
    return "timeout"


def _await_noninteractive_finish(args, start_url: str, raw_mode: bool,
                                 on_tick=None) -> bool:
    """Print operator instructions, wait for the finish/cancel sentinel, and
    clean the sentinels up. Returns True iff the operator chose to discard.

    ``on_tick`` (if given) is passed through to :func:`_wait_for_finish` and
    fires once per poll second — capture_session uses it to drain buffered DOM
    events while the operator drives the noVNC session."""
    out_dir = Path(args.out).parent
    finish = Path(args.finish_file) if args.finish_file else (out_dir / "FINISH")
    cancel = (Path(args.finish_file).with_suffix(".CANCEL")
              if args.finish_file else (out_dir / "CANCEL"))
    _install_finish_signals()
    bar = "=" * 68
    print(
        "\n" + bar +
        f"\nCapturing {start_url} "
        f"({'RAW - UNREDACTED, contains credentials' if raw_mode else 'redacted'})."
        "\nNo terminal attached (cockpit / noVNC capture) — the browser will"
        "\nSTAY OPEN. Interact in the noVNC pane: log in, press play, open the"
        "\nquality/download menu. When finished, from a SECOND SSH shell run:"
        f"\n    touch {finish}        # save the capture"
        f"\n    touch {cancel}        # discard it"
        f"\n(Auto-saves after {int(args.max_seconds)}s if neither appears.)\n" + bar,
        flush=True,
    )
    reason = _wait_for_finish(out_dir, args.max_seconds, args.finish_file,
                              on_tick=on_tick)
    # Clean up sentinels so a stale file can't trigger the next capture.
    for s in (finish, cancel):
        try:
            s.unlink()
        except OSError:
            pass
    if reason == "cancel":
        print("CANCEL sentinel seen — discarding capture.", flush=True)
        return True
    print(f"Finishing capture ({reason}); writing WACZ...", flush=True)
    return False


# ── held-open robustness (A/B/C) ───────────────────────────────────────────
# Anti-bot sites (akamai / cloudflare) close or swap the page on automation
# detection, and a login redirect can dump the session on a host page instead
# of the requested --url. Without recovery the held-open loop just polls a dead
# or wrong-page session until finish, saving a near-empty WACZ. These helpers
# let the per-tick path keep the session alive and drivable. The DECISION logic
# below is pure (unit-tested); the live page reopen / re-nav it drives is
# best-effort and exercised only on a real host (stash).

def _live_pages(ctx):
    """Non-closed pages in the context (best-effort; tolerates a bad ctx)."""
    out = []
    try:
        for pg in list(getattr(ctx, "pages", []) or []):
            try:
                if not pg.is_closed():
                    out.append(pg)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _recovery_decision(live_count: int, goto_seen: bool) -> str:
    """Pure: what the held-open tick should do this iteration.

    ``"reopen"``  — the live browser died (anti-bot close): open a fresh page
                    and re-navigate to start_url so the session survives.
    ``"renav"``   — the operator asked (GOTO) to return to start_url after a
                    login redirect.
    ``"none"``    — a live page exists and no return was requested.
    A dead browser takes priority: reopening lands on start_url anyway, which
    also satisfies a pending GOTO.
    """
    if live_count <= 0:
        return "reopen"
    if goto_seen:
        return "renav"
    return "none"


def _goto_sentinel_path(out_dir, finish_file=None) -> Path:
    """The GOTO ("return to start_url") sentinel, scoped exactly like the
    FINISH/CANCEL pair so a per-capture --finish-file isolates it too."""
    if finish_file:
        return Path(finish_file).with_suffix(".GOTO")
    return Path(out_dir) / "GOTO"


def _consume_goto(out_dir, finish_file=None) -> bool:
    """True iff a GOTO sentinel was present; removes it so it fires once."""
    p = _goto_sentinel_path(out_dir, finish_file)
    try:
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
            return True
    except OSError:
        pass
    return False


# Anti-bot interstitial markers (matched case-insensitively against the page
# TITLE). Detection only — no challenge is solved or evaded; we merely wait for
# the operator / browser to clear it before treating the page as the content.
_CHALLENGE_TITLE_MARKERS = (
    "just a moment", "checking your browser", "attention required",
    "access denied", "verify you are human", "verifying you are human",
    "ddos protection", "cf-chl", "reference #", "one more step",
)


def _challenge_in_title(title) -> bool:
    if not title:
        return False
    t = str(title).strip().lower()
    return any(m in t for m in _CHALLENGE_TITLE_MARKERS)


def _maybe_recover(ctx, *, start_url, out_dir, finish_file, cur):
    """Best-effort held-open recovery, run once per poll tick.

    Reopens a page + re-navigates to start_url when the live browser died, or
    re-navigates to start_url when the operator dropped a GOTO sentinel. ``cur``
    is a one-element list holding the page used for the end-of-session
    cookie/storage snapshot; recovery updates it so the snapshot follows a
    reopened page. NEVER raises — a failure leaves the session untouched.
    """
    try:
        goto = _consume_goto(out_dir, finish_file)
        live = _live_pages(ctx)
        action = _recovery_decision(len(live), goto)
        if action == "reopen":
            try:
                pg = ctx.new_page()        # auto-wired via ctx.on("page")
                cur[0] = pg
                _goto_or_continue_if_usable(pg, start_url)
            except Exception:
                pass
        elif action == "renav":
            pg = live[-1]
            cur[0] = pg
            try:
                _goto_or_continue_if_usable(pg, start_url)
            except Exception:
                pass
    except Exception:
        pass


def _challenge_wait_seconds() -> float:
    """Seconds to wait for an anti-bot interstitial to clear (default 20).

    Zero-cost on a normal site: the title check below returns immediately when
    no challenge marker is present, so only a real interstitial incurs the wait.
    Tunable / disable-able via BD_CHALLENGE_WAIT_S=0.

    v3.66.314 (CLI->GUI parity, guard cut): the global_config store key
    ``challenge_wait_s`` overrides the env seed when set, so a Settings write
    takes effect on the next capture. Read at call time, store > env seed >
    default; stored as a STR (float()-parsed here, matching env semantics and
    avoiding the int-vs-float type_mismatch edge — mirrors honeypot_score_threshold).
    Lazy import; any failure falls back to the env->default path unchanged.
    """
    raw = os.environ.get("BD_CHALLENGE_WAIT_S", "20")
    try:
        from bulk_downloader import global_config as _gc
        _sv = _gc.get("challenge_wait_s", None)
        if _sv not in (None, ""):
            raw = str(_sv)
    except Exception:
        pass
    try:
        return float(raw or 0)
    except Exception:
        return 20.0


def _settle_challenge_handoff(page):
    """P3-T12-CALLSITE: route the held-open capture runner's anti-bot *settle*
    step through the canonical challenge seam
    (``bulk_downloader.session_capture.handle_challenge_on_page``) -- detect a
    cloudflare/akamai interstitial, give it the configured passive budget to
    self-clear, and on timeout route to MANUAL operator handoff. Detection +
    passive self-clear + handoff ONLY: this never interacts with the challenge
    widget (no automation ever touches it).

    Budget parity with the retired local poller: ``passive_budget_s`` is
    ``_challenge_wait_seconds()`` (global_config store > BD_CHALLENGE_WAIT_S env
    seed > default 20), so a Settings write still takes effect on the next
    capture. On ``operator_action_required`` the neutral
    ``operator_instructions()`` are surfaced to stderr -- visible in the noVNC
    capture terminal -- and the existing held-open operator loop is the resume
    opportunity (the detector gates resume via the seam). Best-effort: any
    failure here must never crash the capture.

    Returns the ``ChallengeHandler`` (or None on a degraded import / error) for
    callers / tests that want to inspect the reached state.
    """
    try:
        from bulk_downloader.session_capture import handle_challenge_on_page
        from bulk_downloader import challenge_handling as _ch
    except Exception:
        return None

    def _log(event):
        try:
            sys.stderr.write("[challenge] " + json.dumps(event, default=str) + "\n")
        except Exception:
            pass

    try:
        handler = handle_challenge_on_page(
            page, passive_budget_s=_challenge_wait_seconds(), log_fn=_log,
        )
    except Exception:
        return None

    try:
        if getattr(handler, "state", None) == _ch.OPERATOR_ACTION_REQUIRED:
            sys.stderr.write(
                "\n[challenge] " + handler.operator_instructions() + "\n"
            )
    except Exception:
        pass
    return handler


def _attach_recorders(ctx, initial_page, capture, *, redact: bool = True):
    """Attach BOTH recorders — network (CDP) + rrweb DOM — to every page the
    context opens, not just the first.

    Reptyle and similar SPAs bounce login through an oauth redirect and/or open
    playback in a new tab/popup. The old single-page binding left the recorders
    watching the abandoned initial page, so login/play/download that happened in
    a spawned page produced 0 dom events / 0 segments. Wiring a ``context.on
    ("page", ...)`` listener (plus the initial page) records the redirect
    target, popped playback tabs, and the download modal too.

    Each wire is best-effort: a single bad page must never crash the capture.
    Pages are de-duplicated so a page seen twice is wired once. Returns the set
    of wired page objects (for tests / callers that want to inspect coverage).
    """
    from bulk_downloader.session_capture import capture_via_cdp
    from bulk_downloader.dom_recorder import attach_dom_recorder
    from bulk_downloader.affordance_learning import (
        attach_page_activity_marker,
        attach_page_network_buffer,
    )

    wired = set()

    def _wire_page(pg):
        if pg in wired:
            return
        wired.add(pg)
        try:
            # Live actions follow the tab the operator most recently used,
            # including playback/listing popups instead of the stale launch tab.
            attach_page_activity_marker(pg)
        except Exception:
            pass
        try:
            # Row 363: exact-Page, bounded/redacted response metadata survives
            # the initial navigation, unlike listeners attached when Learn is
            # clicked later.  It stays separate from the all-tab CDP log.
            attach_page_network_buffer(pg, capture)
        except Exception:
            pass
        try:
            capture_via_cdp(pg, capture, redact=redact)
        except Exception:
            pass
        try:
            attach_dom_recorder(pg, capture, redact=redact)
        except Exception:
            pass

    # New tabs / popups opened after launch (oauth redirect target, popped
    # playback window, download-modal tab, ...).
    try:
        ctx.on("page", _wire_page)
    except Exception:
        pass
    # The initial page — unchanged behaviour for the common single-tab case.
    _wire_page(initial_page)
    return wired


# Opt-in override for the interactive-capture navigation wait condition.
# UNSET (the default) keeps the navigation byte-identical to the pre-toggle
# behaviour. See ``_resolve_capture_wait_until``.
_CAPTURE_WAIT_UNTIL_VALUES = ("load", "domcontentloaded", "commit")


def _headed_browser_args(extra_args):
    """Launch args + context kwargs so the headed capture browser FILLS the
    Xvfb framebuffer instead of opening as a small default-size window on a
    large black desktop (v3.66.268). ``--start-maximized`` maximizes the
    window; ``no_viewport`` (applied to the context / persistent launch) lets
    the page track the maximized window so the noVNC pane shows a full-size
    browser. Returns ``(browser_args, context_kwargs)``.
    """
    return (["--start-maximized", *list(extra_args)], {"no_viewport": True})


def _normalize_pick(rec, now_ms):
    """Normalise one observational pick from EITHER transport into
    ``{"descriptor", "ts"}`` (or ``None`` to drop it).

    The picker (``dom_overlay.picker_script``) hands back a ``{"d": <descriptor>,
    "ts": <epoch-ms>}`` record via the Playwright binding when it is present, and
    buffers the identical record in-page for the pump to drain when the binding
    has not survived a cross-origin document swap (#2b). ``ts`` is stamped in the
    page on the same epoch-ms clock as ``network_log`` (JS ``Date.now()`` vs
    ``int(time.time()*1000)``), so a drained pick still correlates. A legacy bare
    descriptor (no ``"d"``) is accepted and stamped with the local clock. Pure +
    defensive: bad input yields ``None`` and never raises into the capture loop.
    """
    if not isinstance(rec, dict):
        return None
    if "d" in rec:                                  # picker {d, ts} record
        desc = rec.get("d")
        if not isinstance(desc, dict):
            return None                             # malformed — drop it
        ts = rec.get("ts")
        ts = int(ts) if isinstance(ts, (int, float)) else now_ms()
    else:
        desc = rec                                  # legacy bare descriptor
        ts = now_ms()
    if not isinstance(desc, dict):
        return None
    return {"descriptor": desc, "ts": ts}


def _resolve_capture_wait_until():
    """Return the operator-selected ``wait_until`` for capture navigation, or
    ``None`` to mean "unchanged" (a bare ``page.goto(start_url)``).

    Driven by the ``BD_CAPTURE_WAIT_UNTIL`` env var, opt-in / default-OFF:
      • unset / blank / unrecognised → ``None`` → ``_goto_or_continue_if_usable``
        calls ``page.goto(start_url)`` with NO ``wait_until`` kwarg, byte-for-byte
        the pre-toggle path (Playwright's implicit ``"load"``).
      • one of ``{load, domcontentloaded, commit}`` → that value is passed
        explicitly.

    Why opt-in and not a default flip: the DOM recorder is armed immediately
    after navigation (``_pump_dom``). Resolving the goto at ``domcontentloaded``
    arms it *earlier* in the page lifecycle on heavy SPAs — the same class that
    historically SIGTRAPped the renderer when the recorder armed at
    document-start (v3.66.165). So weakening the wait condition is an operator
    decision per capture, to be justified by ``tools/nav_probe.py`` evidence on
    the specific target first, not a blanket behaviour change.
    """
    val = (os.environ.get("BD_CAPTURE_WAIT_UNTIL") or "").strip().lower()
    # v3.66.308 (CLI→GUI parity): the global_config store key ``capture_wait_until``
    # overrides the env seed when set, so a Settings write takes effect on the
    # next capture. Read at call time; lazy import, fail back to env→default.
    try:
        from bulk_downloader import global_config as _gc
        _sv = _gc.get("capture_wait_until", None)
        if _sv:
            val = str(_sv).strip().lower()
    except Exception:
        pass
    return val if val in _CAPTURE_WAIT_UNTIL_VALUES else None


def _goto_or_continue_if_usable(page, start_url) -> bool:
    """D2(a): navigate to ``start_url`` and tolerate a ``load`` timeout when the
    DOM is already usable.

    Why: ``_attach_recorders`` wires a network recorder (``capture_via_cdp``)
    before navigation. On a heavy SPA it holds requests open, so the default
    ``wait_until="load"`` can time out even though the DOM is fully parsed. This
    is an INTERACTIVE capture — the operator drives the page afterward — so a
    ``load`` timeout is not fatal *as long as the page is usable*.

    Navigation wait condition: by default a bare ``page.goto(start_url)``
    (Playwright's implicit ``"load"``), unchanged. An operator may opt in to a
    weaker condition via ``BD_CAPTURE_WAIT_UNTIL`` (see
    ``_resolve_capture_wait_until``); when set, that value is passed to ``goto``.
    The timeout-tolerant branch below still applies either way (defence in depth).

    Behaviour:
      • normal success → returns True (default: the original
        ``page.goto(start_url)`` with the implicit ``wait_until="load"``).
      • Playwright ``TimeoutError`` AND the page is usable (``document.readyState``
        is ``interactive``/``complete`` and ``document.body`` exists) → log a
        clear, non-success WARNING and return False (continue).
      • Playwright ``TimeoutError`` AND the page is NOT usable → re-raise the
        original ``TimeoutError``.
      • any non-timeout exception → propagates unchanged (never swallowed).

    Returns True on a normal load, False on a tolerated-timeout continue.
    """
    from playwright.sync_api import TimeoutError as _PWTimeout
    _wait_until = _resolve_capture_wait_until()
    try:
        if _wait_until is None:
            page.goto(start_url)      # default: byte-identical to pre-toggle path
        else:
            page.goto(start_url, wait_until=_wait_until)  # operator opt-in
        return True
    except _PWTimeout:
        # Probe usability. Any failure to read these leaves them at the
        # not-usable defaults, which forces a re-raise.
        readystate = None
        body_len = -1
        try:
            readystate = page.evaluate("document.readyState")
            body_len = page.evaluate(
                "(document.body && document.body.innerText)"
                " ? document.body.innerText.length : -1")
        except Exception:
            pass
        usable = (readystate in ("interactive", "complete")
                  and isinstance(body_len, int) and body_len >= 0)
        if not usable:
            # The page never became usable — surface the real failure.
            raise
        try:
            title = page.evaluate("document.title")
        except Exception:
            title = ""
        sys.stderr.write(
            f"  WARNING: navigation to {start_url} did not reach 'load' before "
            f"the timeout (readyState={readystate!r}, body innerText length="
            f"{body_len}, title={title!r}). The recorders are attached and the "
            f"DOM is usable, so continuing the interactive capture. "
            f"This is a CONTROLLED continue, NOT a normal page load.\n")
        return False


def run(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    # Imports deferred so --help works without Playwright installed.
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("ERROR: Playwright is required for live capture "
              "(pip install playwright && playwright install chromium).",
              file=sys.stderr)
        return 2

    from bulk_downloader.dom_capture import DomCapture
    from bulk_downloader.session_capture import capture_via_cdp
    from bulk_downloader.wacz_export import write_wacz, verify_wacz_bytes

    # Capture-time redaction is always on in the release. A raw (unredacted)
    # capture is available ONLY via the separate dev package bd_dev_inspect,
    # which installs a pass-through redactor into the capture seam when
    # BD_CAPTURE_RAW is set. The release does not ship that package, so this
    # import fails and the capture stays redacted. (The redact=True flag below
    # still applies; the dev package swaps what "redact" routes through.)
    raw_mode = False
    try:
        import bd_dev_inspect
        raw_mode = bd_dev_inspect.enable_raw_capture()
    except ImportError:
        pass

    redact = True
    capture = DomCapture(url=args.url, redact=redact)

    # Plan-C layer 3: if this --title was captured before, start from that
    # page (query-stripped) so the diff pair begins on the same page.
    start_url = args.url
    nav_note = ""
    if args.title:
        remembered = _load_url_memory(args.url_memory_file).get(args.title)
        if remembered:
            start_url = remembered
            nav_note = (f"  [title '{args.title}' seen before — navigating to the "
                        f"remembered page instead of --url]\n")

    # E1: a capture run is starting (target resolved). capture.started fires
    # exactly once per run here, before the browser launches.
    _emit_capture_event("capture.started",
                        {"url": start_url, "ts": int(time.time() * 1000)})

    # Plan-C layers 1+2: a persistent profile (when --profile-dir is given)
    # makes the login session and any password-manager/autofill setup survive
    # between captures. This is the SAME launch_persistent_context pattern the
    # manual-login path already uses; it changes only the browser profile, not
    # the capture or redaction. Native autofill is opt-in via --autofill and is
    # only wired here, where a human is present — never in headless contexts.
    autofill_args = []
    if args.autofill:
        # Mirrors login.py's takeover-browser autofill opt-in. Playwright
        # Chromium disables autofill under CDP by default; these re-enable it.
        autofill_args = [
            "--password-store=basic",
            "--enable-features=AutofillEnableAccountWalletStorage,PasswordManagerEnabled",
        ]
        if not args.profile_dir:
            print("  NOTE: --autofill has little effect without --profile-dir "
                  "(saved passwords won't persist across runs).", file=sys.stderr)

    from bulk_downloader import cloak as _cloak
    pw = None
    browser = None          # set only for the throwaway (non-persistent) path
    ctx = None
    try:
        # v3.66.268: maximize the headed capture window + let the page track it
        # (no_viewport) so the noVNC pane shows a full-size browser, not a small
        # window on a black Xvfb desktop. See _headed_browser_args.
        launch_args, ctx_kw = _headed_browser_args(autofill_args)
        if args.profile_dir:
            # v3.66.141: persistent capture profile via the shared cloak
            # wrapper (honours the configured backend; channel→bundled retry).
            persist_extra = {}
            if args.system_chrome:
                persist_extra["channel"] = "chrome"
            try:
                ctx, pw, backend = _cloak.open_persistent_context(
                    user_data_dir=args.profile_dir, headless=False,
                    args=launch_args, **persist_extra, **ctx_kw)
            except Exception as e:
                # Drop the channel and retry on bundled Chromium if a
                # system-Chrome persistent launch fails.
                persist_extra.pop("channel", None)
                print(f"  persistent launch failed ({str(e)[:80]}); "
                      f"retrying with bundled Chromium", file=sys.stderr)
                ctx, pw, backend = _cloak.open_persistent_context(
                    user_data_dir=args.profile_dir, headless=False,
                    args=launch_args, **persist_extra, **ctx_kw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            launch_extra = {}
            if args.system_chrome:
                launch_extra["channel"] = "chrome"
            browser, pw, backend = _cloak.launch_browser(
                headless=False, args=launch_args, **launch_extra)
            ctx = browser.new_context(**ctx_kw)
            page = ctx.new_page()
        _cloak.log_choice("capture", backend,
                          "persistent" if args.profile_dir else "non-persistent")

        # v3.66.158.3: attach BOTH recorders (network CDP + rrweb DOM) to EVERY
        # page the context opens, not just the first — see _attach_recorders.
        wired = _attach_recorders(ctx, page, capture, redact=redact)
        _cloak.log_choice("DOM capture", backend,
                          "recording on the capture session's browser (+ new tabs)")

        # v3.66.165: the DOM recorder is armed AFTER load and drained on an
        # interval (not armed at document-start — that SIGTRAPped the renderer
        # on heavy SPAs). _pump_dom arms each wired page (idempotent) and pulls
        # its buffered rrweb events into the capture. It is called right after
        # navigation, once per second while the operator drives the session
        # (both the TTY and noVNC waits), and a final time at stop — so events
        # are not lost when a page navigates (which resets the in-page buffer).
        from bulk_downloader.dom_recorder import arm_dom_recorder, drain_dom_events

        # F2.7-live (230): default-ON decorative HUD, re-mounted on every pump
        # tick so it SURVIVES navigation — the page nav that resets the rrweb
        # in-page buffer also wipes the HUD, exactly like the DOM events below.
        # ON by default; off per-capture via --no-hud, or globally via
        # BD_HUD_OVERLAY=0. Injected over page.evaluate (main world via CDP
        # Runtime.evaluate, which is not governed by the page's script-src CSP
        # -- see dom_overlay.inject_overlay), never add_script_tag.
        # The HUD is render-only and must NEVER take down a capture.
        _hud_on = _hud_enabled(args)
        # Track-F Wave A — the observational element inspector / action recorder
        # rides with the HUD. It is strictly passive: an in-page capture-phase
        # listener (dom_overlay.picker_script) hands back a DESCRIPTOR of each
        # element the operator clicks; we resolve it (inspect_pick) and correlate
        # it with the network log. It NEVER drives the page.
        _inspect_on = _hud_on
        _picks = []          # [{descriptor, ts}] on the shared _now_ms() clock
        _exposed = set()     # pages already given the __bd_inspect_pick binding
        _picker_js = ""
        if _hud_on:
            try:
                from bulk_downloader.dom_overlay import (
                    inject_overlay as _inject_hud, picker_script as _picker_script)
                from bulk_downloader.dom_recorder import get_status as _rec_status
                from bulk_downloader import inspect_pick as _inspect
                from bulk_downloader.session_capture import _now_ms as _click_now
                _picker_js = _picker_script()
            except Exception:
                _hud_on = False     # render half unavailable -> degrade silently
                _inspect_on = False
        _hud_warned = [False]

        def _record_pick(rec):
            # Normalise one pick from EITHER transport — the Playwright binding
            # (live, nav-safe) or the in-page buffer drain (survives cross-origin
            # document swaps where the binding does not) — and append it.
            # Passive: recording only — must NEVER disturb the capture.
            try:
                p = _normalize_pick(rec, _click_now)
                if p is not None:
                    _picks.append(p)
                    if len(_picks) > 300:
                        del _picks[:-300]
            except Exception:
                pass

        def _on_pick(source, rec):
            # Playwright binding callback: (source_dict, arg). The in-page
            # listener is passive (no preventDefault), so the operator's own
            # click still reaches the site — we only record WHAT they pointed
            # at, stamped on the same clock as network_log so it correlates.
            _record_pick(rec)

        def _pump_dom():
            # Compute the capture snapshot once per tick (cheaper than the old
            # per-page call) and, when the inspector is on, resolve the action
            # timeline + finish-readout from it. All best-effort: a failure here
            # must never disturb arm/drain or take down the capture.
            _cap = None
            _timeline = []
            _verify = None
            if _hud_on:
                try:
                    _cap = capture.to_capture_dict()
                except Exception:
                    _cap = None
            if _cap is not None and _inspect_on:
                try:
                    _timeline = _inspect.correlate_timeline(_picks, _cap.get("network_log"))
                    # (B) rrweb cross-check: count rrweb MouseInteraction CLICK
                    # events (source==2, data.type==2). Undercount -> 0 -> no
                    # false warning; the listener (A) is the primary resolver.
                    _clicks = sum(
                        1 for _e in (_cap.get("dom_log") or [])
                        if isinstance(_e, dict) and _e.get("source") == 2
                        and isinstance(_e.get("data"), dict) and _e["data"].get("type") == 2)
                    _verify = _inspect.verify_summary(_timeline, _cap, recorded_clicks=_clicks)
                except Exception:
                    _timeline = []
                    _verify = None
            # C3 (v3.66.290): mirror the live action timeline + verify readout
            # to the SPA the same cross-process way the pick bridge works.
            # Structure-only (correlate_timeline / verify_summary emit no values),
            # so it is safe to surface. Best-effort: a write failure must never
            # disturb the capture (mirrors the HUD/arm/drain contract).
            if _inspect_on:
                try:
                    from bulk_downloader import element_pick as _ep_inspect
                    _ep_inspect.write_inspect_state(
                        Path(args.out).parent,
                        {"actions": _timeline, "verify": _verify,
                         "rec": _inspect_on})
                except Exception:
                    pass
            for _pg in list(wired):
                try:
                    arm_dom_recorder(_pg)
                except Exception:
                    pass
                try:
                    drain_dom_events(_pg, capture)
                except Exception:
                    pass
                if _hud_on:
                    # Install the observational picker once per page. The binding
                    # is live + nav-safe SAME-origin, but does NOT reliably survive
                    # a cross-origin document swap (#2b) — so the picker also buffers
                    # picks in-page, and the pump drains that buffer below. Both
                    # paths are passive — they never call preventDefault, so the
                    # operator's click still drives the site.
                    if _inspect_on and _pg not in _exposed:
                        try:
                            _pg.expose_binding("__bd_inspect_pick", _on_pick)
                            # Re-install the listener at document-start on EVERY
                            # navigation (cross-origin included) so post-nav clicks
                            # are caught even before the next pump tick. When the
                            # binding has not survived the cross-origin document
                            # swap, the picker buffers picks in-page instead.
                            _pg.add_init_script(_picker_js)
                            _exposed.add(_pg)
                        except Exception:
                            pass
                    if _inspect_on and _picker_js:
                        try:
                            _pg.evaluate(_picker_js)        # arm the live document
                        except Exception:
                            pass
                        try:
                            # Drain the in-page pick buffer — the transport that
                            # survives a cross-origin swap, where the binding is
                            # gone. Read-and-clear; feed the same recorder as the
                            # binding so resolution/redaction are identical.
                            _drained = _pg.evaluate(
                                "(function(){var a=window.__bd_picks||[];"
                                "window.__bd_picks=[];return a;})()")
                            if isinstance(_drained, list):
                                for _r in _drained:
                                    _record_pick(_r)
                        except Exception:
                            pass
                    try:
                        if not _inject_hud(_pg, _cap if _cap is not None else capture,
                                           _rec_status(), actions=_timeline,
                                           verify=_verify, rec=_inspect_on) \
                                and not _hud_warned[0]:
                            _hud_warned[0] = True
                            sys.stderr.write(
                                "[hud] overlay not shown (closed page / no body)\n")
                    except Exception:
                        pass  # the HUD must NEVER take down a capture

            # Active element-pick bridge (noVNC workflow): when the operator has
            # armed a pick via POST /cockpit/api/captures/pick, inject the
            # one-shot active listener into the live pages and drain the resolved
            # selector to PICK_RESULT.json. Unlike the OBSERVATIONAL picker above
            # (passive, records every click), this is single-shot and cancels the
            # click's default action, so it stays OUT of the passive recording
            # path. Best-effort: a failure here must never disturb the capture.
            try:
                from bulk_downloader import element_pick as _ep
                _ep.maybe_arm_and_collect(list(wired), Path(args.out).parent)
                # F2.7c: service a one-shot live-DOM-excerpt request the same
                # cross-process way (DOM_REQUEST -> scrubbed outerHTML excerpt
                # -> DOM_RESULT.json). Best-effort: must never disturb capture.
                _ep.maybe_collect_dom(list(wired), Path(args.out).parent)
            except Exception:
                pass

            # Row 363: service GUI learning/corroboration/crawl requests on the
            # same held-open authenticated page.  The request is nonce-bound;
            # the capture side writes a terminal result on success/failure or
            # suppresses it when a cancellation tombstone wins the race.
            # Best-effort here: discovery must never take down the operator's
            # Capture session.
            try:
                from bulk_downloader import affordance_learning as _al
                # ``wired`` is a set and therefore has no recency ordering.
                # Put the capture's authoritative current page last because
                # the learner deliberately chooses the last usable page.
                _current = cur[0]
                _learning_pages = [p for p in wired if p is not _current]
                _learning_pages.append(_current)
                _al.maybe_service_live_request(
                    _learning_pages, capture, Path(args.out).parent)
            except Exception:
                pass

        _goto_or_continue_if_usable(page, start_url)
        _settle_challenge_handoff(page)  # C: detect/handoff an anti-bot interstitial via the canonical seam
        sys.stderr.write(nav_note)
        cur = [page]  # page for the end-of-session snapshot (A/B may swap it)
        _pump_dom()  # arm the initial page post-load + first snapshot + mount HUD
        # ── stop trigger ──────────────────────────────────────────────
        # TTY (operator terminal): press ENTER, as before. No TTY (cockpit /
        # noVNC subprocess — stdin is EOF): keep the browser open and wait for
        # a sentinel file or signal instead, so the operator can actually drive
        # the session in the noVNC pane before the capture is saved.
        cancelled = False
        if sys.stdin.isatty():
            print(f"Capturing {start_url} "
                  f"({'RAW — UNREDACTED, contains credentials' if raw_mode else 'redacted'}). "
                  f"Log in if prompted, press play and download in the browser, "
                  f"then press ENTER here to save the capture.")
            # Drain DOM events once per second while waiting for ENTER, so a
            # mid-session navigation (which resets the in-page buffer) does not
            # lose events. select() lets us poll stdin without a background
            # thread (Playwright's sync API is single-threaded per context).
            import select
            try:
                while True:
                    _pump_dom()
                    try:
                        ready, _, _ = select.select([sys.stdin], [], [], 1.0)
                    except Exception:
                        # stdin not selectable here — degrade to a blocking read.
                        input()
                        break
                    if ready:
                        sys.stdin.readline()
                        break
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            _out_dir = Path(args.out).parent

            def _tick():
                # per-second held-open work: drain DOM, then A/B recovery
                # (reopen a dead page / GOTO re-nav). Recovery is best-effort.
                _pump_dom()
                _maybe_recover(ctx, start_url=start_url, out_dir=_out_dir,
                               finish_file=args.finish_file, cur=cur)

            cancelled = _await_noninteractive_finish(args, start_url, raw_mode,
                                                     on_tick=_tick)
        if not cancelled:
            _pump_dom()  # final drain: flush any events still buffered in-page
            _snap = cur[0]  # may be a reopened/renavigated page (A/B recovery)
            # Snapshot cookies/storage at stop.
            try:
                capture.set_cookies(_snap.context.cookies())
            except Exception:
                pass
            # localStorage / sessionStorage snapshot. Values are redacted
            # sink-side in DomCapture.snapshot_storage (keys + structure only on
            # disk under redaction), so even raw page storage cannot persist a
            # token/secret. Best-effort, like cookies above.
            try:
                _ls = _snap.evaluate(
                    "() => { const o = {}; for (let i = 0; i < localStorage.length; i++)"
                    " { const k = localStorage.key(i); o[k] = localStorage.getItem(k); }"
                    " return o; }")
                _ss = _snap.evaluate(
                    "() => { const o = {}; for (let i = 0; i < sessionStorage.length; i++)"
                    " { const k = sessionStorage.key(i); o[k] = sessionStorage.getItem(k); }"
                    " return o; }")
                capture.snapshot_storage(local=_ls, session=_ss)
            except Exception:
                pass
            # Plan-C layer 3: record where we ended up (query-stripped) so the
            # next capture of this title starts from the same page.
            if args.title:
                try:
                    _remember_url(args.url_memory_file, args.title,
                                  getattr(_snap, "url", None) or start_url)
                except Exception as e:
                    print(f"  (could not record title URL: {str(e)[:80]})",
                          file=sys.stderr)
        # Persistent contexts are closed directly; throwaway browsers via browser.
    finally:
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    if cancelled:
        print("Capture discarded (CANCEL sentinel) — no WACZ written.")
        return 0
    # Track-F Wave A: resolve the observational action->effect timeline one
    # last time against the FULL network_log and persist it into the capture
    # (to_capture_dict -> WACZ) for later template review. Selectors are
    # structure; effects are request kinds/counts; excerpts are redacted — no
    # values are stored. Best-effort: never blocks the save.
    if _inspect_on and _picks:
        try:
            for _e in _inspect.correlate_timeline(_picks, capture.network_log):
                capture.record_action(_e)
        except Exception:
            pass
    # Compute the capture dict once. It now carries E-T1's
    # `fingerprint_detection` (see session_capture.to_capture_dict). Surface
    # the risk to the operator at capture time — detect-and-surface means the
    # operator should hear "this site fingerprints you" immediately, not only
    # when they later open the WACZ. ASCII only (project no-emoji hygiene).
    cap_dict = capture.to_capture_dict()
    fd = cap_dict.get("fingerprint_detection") or {}
    if fd.get("fingerprinting_detected"):
        print(f"WARNING: {fd.get('summary', 'fingerprinting detected')}",
              file=sys.stderr)

    path = write_wacz(cap_dict, args.out)
    # E1: the capture run finished and the WACZ artifact was written.
    # capture.done fires exactly once per run here, paired with capture.started.
    _emit_capture_event("capture.done",
                        {"url": start_url,
                         "network_count": cap_dict.get("network_log_count", 0),
                         "ts": int(time.time() * 1000)})
    with open(path, "rb") as f:
        result = verify_wacz_bytes(f.read())
    status = "OK" if result["ok"] else f"DIGEST ERRORS: {result['errors']}"
    print(f"Wrote {path} ({result['resources']} resources) — {status}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
