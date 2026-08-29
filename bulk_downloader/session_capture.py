"""Session capture (A-T1) — minimum-viable capture.

Records a browsing session's network traffic + cookies into the
**existing bd-recon capture format** (the one ``netlog_classify`` already
consumes and the ``recon_corpus`` fixtures use) rather than inventing a
parallel HAR schema — this avoids the format-lock-in risk the roadmap
flags and means an A-T1 capture flows straight through the media
classifier.

Two layers:

  * :class:`SessionCapture` — accumulates ``network_log`` entries + page
    context, with **capture-time credential redaction on by default**
    (via :mod:`bulk_downloader.capture_redact`), so secrets never reach
    the in-memory capture or disk. ``to_capture_dict()`` emits the
    recon-format dict.
  * :func:`feed_cdp_event` — maps raw Chrome DevTools Protocol
    ``Network.*`` events into a SessionCapture (joining request→response
    by ``requestId``, tracking redirects). Pure/testable with synthetic
    event dicts.
  * :func:`capture_via_cdp` — thin live driver that wires a Playwright
    page's CDP session into ``feed_cdp_event``. Exercised against a real
    browser in the live-tests framework, not the unit suite.

:func:`diff_captures` produces a human-readable diff between two captures
of the same action — the A-T1 definition-of-done item and the seed for
C-T1 (invariants vs parameters).

Posture: capture + redaction + diff only — detect-and-surface-risk. This
module does not replay, reassemble, or evade anything. Signed/short-lived
URLs are recorded (with their signing query params redacted) and left for
``netlog_classify`` to *describe*, never to reconstruct.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .capture_bodies import content_type_of, redact_body, should_capture_body
from .capture_redactor import active_redactor
from .fp_detect import detect_fingerprinting
# P3-T12-WIRE: the @386 challenge-handling framework (manual handoff + passive
# self-clear ONLY -- never solving). Wired to the live capture seam below.
from .challenge_handling import ChallengeHandler

RECON_CAPTURE_VERSION = 1

# The network_log entry keys produced here, matching the existing
# bd-recon shape consumed by netlog_classify.
_ENTRY_KEYS = (
    "timestamp", "iso", "type", "method", "url",
    "request_headers", "request_body",
    "response_status", "response_status_text", "response_headers",
    "response_body", "response_body_truncated",
    "response_body_skipped_reason", "duration_ms", "error", "seq",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: Optional[int] = None) -> str:
    ts = (ms / 1000) if ms is not None else time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class SessionCapture:
    """Accumulates a bd-recon-format capture with capture-time redaction.

    Parameters
    ----------
    url : str, optional
        The top-level page URL; also seeds origin/host/pathname/search.
    redact : bool
        Capture-time credential redaction. **Default True** — secrets are
        replaced with :data:`PLACEHOLDER` as each event is recorded, so a
        SessionCapture never holds raw credentials. Set False only for an
        ephemeral in-memory capture that is never persisted.
    """

    def __init__(self, *, url: Optional[str] = None, redact: bool = True):
        self.redact = redact
        self._seq = 0
        self.network_log: List[Dict[str, Any]] = []
        self._pending: Dict[str, Dict[str, Any]] = {}
        # WebSocket/SSE capture (additive, metadata-only by default). Frame
        # payloads are NOT captured unless capture_ws_payloads is explicitly
        # enabled — and even then they are scrubbed + length-capped. This keeps
        # high-volume, token-bearing frame bodies off disk by default.
        self.websocket_log: List[Dict[str, Any]] = []
        self._ws: Dict[str, Dict[str, Any]] = {}
        self.capture_ws_payloads: bool = False
        self.page_context: Dict[str, Any] = {
            "capture_version": RECON_CAPTURE_VERSION,
            "captured_at": _iso(),
            "session_start": _now_ms(),
            "url": url,
            "origin": None,
            "host": None,
            "pathname": None,
            "search": None,
            "title": None,
            "user_agent": None,
            "cookies": None,
        }
        if url:
            self._seed_url(url)

    def _seed_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
            self.page_context["origin"] = (
                f"{parts.scheme}://{parts.netloc}" if parts.scheme else None)
            self.page_context["host"] = parts.netloc or None
            self.page_context["pathname"] = parts.path or None
            self.page_context["search"] = (
                f"?{parts.query}" if parts.query else None)
        except Exception:
            pass

    # ── page context ──────────────────────────────────────────────
    def set_page_context(self, **fields) -> None:
        """Set top-level page-context fields (title, user_agent, etc.)."""
        for k, v in fields.items():
            self.page_context[k] = v
        if "url" in fields and fields["url"]:
            self._seed_url(fields["url"])

    def set_cookies(self, cookies: Any) -> None:
        """Record cookies. Under redaction (default) the value is dropped
        to the placeholder — cookies are pure credentials."""
        self.page_context["cookies"] = (
            active_redactor().cookies(cookies) if self.redact else cookies)

    # ── network ───────────────────────────────────────────────────
    def record_network(
        self, *,
        type: str = "xhr",
        method: str = "GET",
        url: str = "",
        request_headers: Any = None,
        request_body: Any = None,
        response_status: Optional[int] = None,
        response_status_text: Optional[str] = None,
        response_headers: Any = None,
        response_body: Any = None,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        timestamp: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Append one network_log entry. When ``self.redact`` is True
        (default), the URL query, headers, and bodies are run through the
        capture_redact primitives BEFORE storage."""
        ts = timestamp if timestamp is not None else _now_ms()
        if self.redact:
            # Route scrubbing through the active redactor. In the release this
            # is always the real Redactor (capture_redactor.active_redactor),
            # so this is byte-for-byte the prior direct-call behaviour; the
            # dev-only raw-inspection package can swap in a pass-through. The
            # call order and the use of the *scrubbed* response_headers for
            # the Content-Type body gate are preserved exactly.
            r = active_redactor()
            url = r.query(url)
            request_headers = r.headers(request_headers)
            response_headers = r.headers(response_headers)
            # Request bodies carry POST credentials and are never retained —
            # always a length marker, regardless of the body-capture flag.
            request_body = r.request_body(request_body)
            # Response bodies are the C-T2 provenance source. redact_body is a
            # drop-in for body_marker: with BD_CAPTURE_BODIES unset (default)
            # it returns the identical length marker, so the live capture path
            # is byte-for-byte unchanged unless the operator opts in. When on,
            # it retains redacted text/JSON only. Content-Type drives the
            # text/JSON eligibility gate and survives scrub_headers above.
            response_body = r.response_body(
                response_body, content_type_of(response_headers))
        entry = {
            "timestamp": ts,
            "iso": _iso(ts),
            "type": type,
            "method": method,
            "url": url,
            "request_headers": request_headers,
            "request_body": request_body,
            "response_status": response_status,
            "response_status_text": response_status_text,
            "response_headers": response_headers,
            "response_body": response_body,
            "response_body_truncated": False,
            "response_body_skipped_reason": None,
            "duration_ms": duration_ms,
            "error": error,
            "seq": self._seq,
        }
        self._seq += 1
        self.network_log.append(entry)
        return entry

    # ── WebSocket capture (additive, metadata-only by default) ────────
    def record_ws_created(self, *, request_id: str, url: str,
                          ts: Optional[int] = None) -> Dict[str, Any]:
        """Open a WebSocket connection record. The URL is redacted (query
        scrubbed) under redaction, exactly like network entries. The record is
        appended to ``websocket_log`` immediately so a never-closed connection
        is still visible; later frames/handshake/close mutate it in place."""
        u = active_redactor().query(url) if (self.redact and url) else url
        conn = {
            "request_id": request_id,
            "url": u,
            "created_ms": ts,
            "handshake_status": None,
            "handshake_headers": None,
            "closed_ms": None,
            "frame_count": 0,
            "frames_capped": False,
            "frames": [],
            "seq": self._seq,
        }
        self._seq += 1
        self._ws[request_id] = conn
        self.websocket_log.append(conn)
        return conn

    def record_ws_handshake(self, *, request_id: str,
                            status: Optional[int] = None,
                            headers: Any = None,
                            ts: Optional[int] = None) -> None:
        conn = self._ws.get(request_id)
        if conn is None:
            return
        conn["handshake_status"] = status
        conn["handshake_headers"] = active_redactor().headers(headers) if self.redact else headers

    def record_ws_frame(self, *, request_id: str, direction: str,
                        opcode: Optional[int] = None,
                        payload_len: Optional[int] = None,
                        ts: Optional[int] = None,
                        payload: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Record one frame's METADATA (direction, opcode, length, ts). The
        payload is captured only if ``capture_ws_payloads`` is explicitly on,
        the opcode is text (1), and redaction allows — and even then it is
        scrubbed and length-capped. Default: no payload bytes on disk.
        Per-connection frame count is capped (``_WS_MAX_FRAMES``)."""
        conn = self._ws.get(request_id)
        if conn is None:
            return None
        conn["frame_count"] += 1
        if conn["frame_count"] > _WS_MAX_FRAMES:
            conn["frames_capped"] = True
            return None
        frame = {
            "dir": direction,            # "sent" | "received"
            "opcode": opcode,            # 1=text, 2=binary, 8=close, 9/10=ping/pong
            "len": payload_len,
            "ts": ts,
        }
        if (self.capture_ws_payloads and payload is not None and opcode == 1):
            body = payload[:_WS_MAX_PAYLOAD]
            # Opt-in only; obeys the same body gate + redaction as response
            # bodies (active_redactor().response_body), so nothing raw is kept.
            frame["payload"] = (active_redactor().response_body(body, "text/plain")
                                if self.redact else body)
            frame["payload_truncated"] = len(payload) > _WS_MAX_PAYLOAD
        conn["frames"].append(frame)
        return frame

    def record_ws_closed(self, *, request_id: str,
                         ts: Optional[int] = None) -> None:
        conn = self._ws.get(request_id)
        if conn is not None:
            conn["closed_ms"] = ts


    def to_capture_dict(self) -> Dict[str, Any]:
        """Emit the full bd-recon-format capture dict.

        Also attaches ``fingerprint_detection`` (E-T1): a presence-only risk
        assessment computed from the capture's own response data — which
        anti-bot vendor(s) fingerprinted this session, any echoed JA3/JA4
        headers, and challenge responses. This is detect-and-surface only; it
        reads what the site already returned and emits nothing runnable. The
        field is additive — the capture-dict shape checks are membership
        (`k in d`), so a new key is safe.
        """
        out = dict(self.page_context)
        out["network_log"] = list(self.network_log)
        out["network_log_count"] = len(self.network_log)
        if self.websocket_log:
            out["websocket_log"] = list(self.websocket_log)
            out["websocket_log_count"] = len(self.websocket_log)
        out["fingerprint_detection"] = detect_fingerprinting(out)
        # If a dev raw-inspection redactor is active (never in the release),
        # stamp the capture loudly so a raw capture can never be mistaken for
        # a shareable/redacted one. Inert in production: the real redactor has
        # unredacted=False, so this branch is not taken.
        if getattr(active_redactor(), "unredacted", False):
            out["_UNREDACTED"] = True
            out["_warning"] = (
                "RAW CAPTURE — credentials and signing material are NOT "
                "redacted. Do not share, upload, or commit as a test fixture.")
        return out


# ── CDP event mapping ─────────────────────────────────────────────

_CDP_REQUEST = "Network.requestWillBeSent"
_CDP_RESPONSE = "Network.responseReceived"
_CDP_FINISHED = "Network.loadingFinished"
_CDP_FAILED = "Network.loadingFailed"
# WebSocket CDP events ride the SAME (already-enabled) Network domain — adding
# them needs no new domain/permission. Metadata-only by default.
_CDP_WS_CREATED = "Network.webSocketCreated"
_CDP_WS_HS_RESP = "Network.webSocketHandshakeResponseReceived"
_CDP_WS_FRAME_SENT = "Network.webSocketFrameSent"
_CDP_WS_FRAME_RECV = "Network.webSocketFrameReceived"
_CDP_WS_FRAME_ERR = "Network.webSocketFrameError"
_CDP_WS_CLOSED = "Network.webSocketClosed"

# Bounds (storage/perf): cap frames per connection; cap any opt-in payload.
_WS_MAX_FRAMES = 1000
_WS_MAX_PAYLOAD = 2048


def feed_cdp_event(capture: SessionCapture, method: str,
                   params: Dict[str, Any],
                   body_fetcher=None) -> None:
    """Route one raw CDP ``Network.*`` event into ``capture``.

    Joins request→response by ``requestId`` and emits a network_log entry
    on ``loadingFinished`` / ``loadingFailed``. A ``requestWillBeSent``
    carrying a ``redirectResponse`` finalizes the prior leg first, so
    redirect chains produce one entry per hop (type ``redirect``).

    ``body_fetcher`` (optional): a callable ``rid -> Optional[str]`` that
    fetches a completed response body over CDP. Passed only to the normal
    ``loadingFinished`` finalize (redirect/error legs have no useful body).
    When None — the default and the unit-test path — no body is fetched and
    ``response_body`` stays None, i.e. behaviour is byte-for-byte the old
    metadata-only capture.
    """
    rid = params.get("requestId")
    if method == _CDP_REQUEST:
        # A redirect arrives as a new requestWillBeSent on the same id
        # with a redirectResponse for the previous leg — finalize it.
        redirect = params.get("redirectResponse")
        if redirect and rid in capture._pending:
            _finalize(capture, rid, response=redirect,
                      ts_ms=_ts_ms(params), type_override="redirect")
        req = params.get("request", {}) or {}
        capture._pending[rid] = {
            "method": req.get("method", "GET"),
            "url": req.get("url", ""),
            "request_headers": _hdr_list(req.get("headers")),
            "request_body": req.get("postData"),
            "type": (params.get("type") or "xhr").lower(),
            "start_ms": _ts_ms(params),
            "response": None,
        }
    elif method == _CDP_RESPONSE:
        p = capture._pending.get(rid)
        if p is not None:
            p["response"] = params.get("response", {}) or {}
    elif method == _CDP_FINISHED:
        if rid in capture._pending:
            _finalize(capture, rid, ts_ms=_ts_ms(params),
                      body_fetcher=body_fetcher)
    elif method == _CDP_FAILED:
        if rid in capture._pending:
            _finalize(capture, rid, ts_ms=_ts_ms(params),
                      error=params.get("errorText") or "failed")
    # ── WebSocket frames (same Network domain) ────────────────────
    elif method == _CDP_WS_CREATED:
        capture.record_ws_created(request_id=rid,
                                  url=params.get("url", ""),
                                  ts=_ts_ms(params))
    elif method == _CDP_WS_HS_RESP:
        resp = params.get("response", {}) or {}
        capture.record_ws_handshake(request_id=rid,
                                    status=resp.get("status"),
                                    headers=_hdr_list(resp.get("headers")),
                                    ts=_ts_ms(params))
    elif method in (_CDP_WS_FRAME_SENT, _CDP_WS_FRAME_RECV):
        r = params.get("response", {}) or {}
        payload = r.get("payloadData")
        plen = len(payload) if isinstance(payload, str) else r.get("payloadLength")
        capture.record_ws_frame(
            request_id=rid,
            direction=("sent" if method == _CDP_WS_FRAME_SENT else "received"),
            opcode=r.get("opcode"), payload_len=plen,
            ts=_ts_ms(params), payload=payload)
    elif method == _CDP_WS_CLOSED:
        capture.record_ws_closed(request_id=rid, ts=_ts_ms(params))


def _finalize(capture, rid, *, response=None, ts_ms=None,
              error=None, type_override=None, body_fetcher=None):
    p = capture._pending.get(rid)
    if p is None:
        return
    resp = response if response is not None else (p.get("response") or {})
    start = p.get("start_ms")
    dur = (ts_ms - start) if (ts_ms is not None and start is not None) else None
    resp_headers = _hdr_list(resp.get("headers"))
    # Fetch the response body ONLY when a fetcher is supplied (live CDP
    # driver), this is a normal finalize (not a redirect/error leg), and the
    # body-capture policy says yes for this content-type (flag on + text/JSON).
    # should_capture_body gates out binary/video so stream bytes are never
    # pulled. The fetched body is handed to record_network, which redacts it
    # at capture time (signing material scrubbed). Off/None → stays None →
    # identical to the old metadata-only path.
    response_body = None
    if (body_fetcher is not None and error is None and type_override is None
            and should_capture_body(content_type_of(resp_headers))):
        try:
            response_body = body_fetcher(rid)
        except Exception:
            response_body = None
    capture.record_network(
        type=type_override or p.get("type", "xhr"),
        method=p.get("method", "GET"),
        url=p.get("url", ""),
        request_headers=p.get("request_headers"),
        request_body=p.get("request_body"),
        response_status=resp.get("status"),
        response_status_text=resp.get("statusText"),
        response_headers=resp_headers,
        response_body=response_body,
        duration_ms=dur,
        error=error,
        timestamp=p.get("start_ms"),
    )
    # For a redirect leg we keep the id alive (the new leg replaced the
    # pending entry already in feed_cdp_event); otherwise drop it.
    if type_override != "redirect":
        capture._pending.pop(rid, None)


def _hdr_list(headers) -> Optional[list]:
    """CDP headers arrive as a flat dict; convert to the HAR-style
    name/value list the recon format and scrub_headers expect."""
    if not headers:
        return [] if headers == {} else None
    if isinstance(headers, dict):
        return [{"name": k, "value": v} for k, v in headers.items()]
    return headers


def _ts_ms(params) -> Optional[int]:
    # CDP timestamps are monotonic seconds (float); wallTime is epoch s.
    wt = params.get("wallTime")
    if isinstance(wt, (int, float)):
        return int(wt * 1000)
    ts = params.get("timestamp")
    if isinstance(ts, (int, float)):
        return int(ts * 1000)
    return None


def capture_via_cdp(page, capture: Optional[SessionCapture] = None,
                    *, redact: bool = True) -> SessionCapture:
    """Live driver: attach a CDP session to a Playwright ``page`` and feed
    its ``Network.*`` events into a SessionCapture.

    Not exercised by the unit suite (needs a real browser) — the mapping
    logic it delegates to (:func:`feed_cdp_event`) is unit-tested with
    synthetic events. Caller is responsible for navigating/driving the
    page; this only wires the listeners.
    """
    if capture is None:
        capture = SessionCapture(url=getattr(page, "url", None), redact=redact)
    client = page.context.new_cdp_session(page)
    client.send("Network.enable")

    def _fetch_body(rid):
        """Fetch a completed response body over CDP. Returns the body text,
        or None on any error or for a base64 (binary) body — so a failed or
        binary fetch never crashes the capture and never retains stream
        bytes. Only ever invoked by _finalize after should_capture_body has
        already confirmed flag-on + a text/JSON content-type."""
        try:
            res = client.send("Network.getResponseBody", {"requestId": rid})
        except Exception:
            return None
        if isinstance(res, dict) and not res.get("base64Encoded"):
            return res.get("body")
        return None

    for ev in (_CDP_REQUEST, _CDP_RESPONSE, _CDP_FINISHED, _CDP_FAILED,
               _CDP_WS_CREATED, _CDP_WS_HS_RESP, _CDP_WS_FRAME_SENT,
               _CDP_WS_FRAME_RECV, _CDP_WS_FRAME_ERR, _CDP_WS_CLOSED):
        # Playwright's CDP session emits events under their FULL
        # domain-qualified name ("Network.requestWillBeSent"), so register
        # with `ev` itself — stripping the "Network." prefix here means the
        # listeners never fire and network_log stays empty.
        client.on(ev, lambda params, _ev=ev: feed_cdp_event(
            capture, _ev, params, body_fetcher=_fetch_body))
    return capture


# ── diff (A-T1 DoD + C-T1 seed) ───────────────────────────────────

def _request_key(entry: Dict[str, Any]) -> str:
    """Stable key for matching the 'same' request across two captures:
    method + host + path (query and signing params deliberately excluded,
    since those are exactly what varies between sessions)."""
    url = entry.get("url") or ""
    try:
        parts = urlsplit(url)
        loc = f"{parts.netloc}{parts.path}"
    except Exception:
        loc = url.split("?", 1)[0]
    return f"{(entry.get('method') or 'GET').upper()} {loc}"


def diff_captures(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Human-readable structural diff between two captures of the same
    action. Matches requests by method+host+path and classifies each:

      * ``invariant`` — present in both with identical full URL (query
        included). These are the fixed parts of the flow.
      * ``varying``   — present in both, same method+host+path but the
        query/params differ. These are the per-session parameters
        (the C-T1 'parameters vs invariants' split).
      * ``only_in_a`` / ``only_in_b`` — requests unique to one capture.

    Also reports differing top-level page-context fields. Operates on the
    *redacted* captures, so it never surfaces secrets.
    """
    def index(cap):
        idx: Dict[str, List[dict]] = {}
        for e in (cap.get("network_log") or []):
            idx.setdefault(_request_key(e), []).append(e)
        return idx

    ia, ib = index(a), index(b)
    invariant, varying, only_a, only_b = [], [], [], []

    for key in sorted(set(ia) | set(ib)):
        la, lb = ia.get(key, []), ib.get(key, [])
        if la and not lb:
            only_a.append(key)
        elif lb and not la:
            only_b.append(key)
        else:
            urls_a = {e.get("url") for e in la}
            urls_b = {e.get("url") for e in lb}
            if urls_a == urls_b:
                invariant.append(key)
            else:
                varying.append({
                    "request": key,
                    "url_in_a": sorted(u for u in urls_a if u),
                    "url_in_b": sorted(u for u in urls_b if u),
                })

    ctx_keys = ("url", "origin", "host", "pathname", "search",
                "title", "user_agent")
    ctx_diff = {
        k: {"a": a.get(k), "b": b.get(k)}
        for k in ctx_keys if a.get(k) != b.get(k)
    }

    return {
        "invariant": invariant,
        "varying": varying,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "page_context_diff": ctx_diff,
        "summary": (
            f"{len(invariant)} invariant, {len(varying)} varying, "
            f"{len(only_a)} only-in-A, {len(only_b)} only-in-B request(s)"
        ),
    }

# ── P3-T12-WIRE: challenge handling at the live capture seam ──────────────────
#
# Wire the v3.66.386 ``challenge_handling`` framework (manual handoff + passive
# self-clear) to a real capture page. The boundary is held HARD: this code only
# DETECTS a challenge, lets the site's OWN challenge clear on its own while the
# normal browser waits, ROUTES an un-clearing challenge to an authenticated human
# in the noVNC session, and resumes ONLY after the detector confirms the challenge
# is gone. It never solves, clicks, fills, types, evaluates, auto-submits, calls a
# solver, replays/persists a challenge response, changes fingerprint/evasion, or
# claims automation solved anything (see ChallengeHandler.solved, always False).
#
# Like ``capture_via_cdp`` / ``dom_recorder``, the live page wrapper
# (``handle_challenge_on_page``) is not exercised by the unit suite; the pure
# lifecycle driver (``drive_challenge_handling``) and the read-only observation
# builder (``observe_page_for_challenge``) are unit-tested with synthetic
# observation / tick / clock callbacks.

# Default passive-wait budget (seconds). The caller (the held-open capture runner)
# may override per-call -- e.g. from the existing BD_CHALLENGE_WAIT_S / Settings
# seed -- so this module stays free of any tools-layer import. A non-positive
# budget skips the passive wait and routes straight to manual handoff (a detected
# challenge never auto-proceeds).
DEFAULT_PASSIVE_BUDGET_S = 20.0


def observe_page_for_challenge(page) -> Dict[str, Any]:
    """Build a ``{text, title, url, markers}`` observation from a live Playwright
    ``page`` for challenge DETECTION. **Strictly read-only**: it reads the page
    title and the body's inner text and the current URL only -- it never executes
    challenge scripts and never touches a challenge widget. Any read that raises
    degrades to an empty value, so a flaky page read can neither crash the capture
    nor falsely trigger a challenge.
    """
    obs: Dict[str, Any] = {"text": "", "title": "", "url": "", "markers": []}
    try:
        t = page.title()
        obs["title"] = t if isinstance(t, str) else ("" if t is None else str(t))
    except Exception:
        pass
    try:
        # read-only body text; enough for the keyword/marker detector. We do NOT
        # run challenge JS or interact with any element to obtain it.
        txt = page.inner_text("body")
        obs["text"] = txt if isinstance(txt, str) else ("" if txt is None else str(txt))
    except Exception:
        pass
    try:
        u = getattr(page, "url", "")
        obs["url"] = u if isinstance(u, str) else ("" if u is None else str(u))
    except Exception:
        pass
    return obs


def drive_challenge_handling(observe_fn, *, tick_fn=None,
                             passive_budget_s: float = DEFAULT_PASSIVE_BUDGET_S,
                             poll_interval_s: float = 1.0,
                             artifact_fn=None, log_fn=None,
                             clock=None) -> ChallengeHandler:
    """Detector-gated pause / passive self-clear / manual-handoff driver.

    Pure and unit-testable. ``observe_fn()`` returns a fresh observation dict;
    ``tick_fn(seconds)`` runs ONE real browser page-load wait tick (the live
    wrapper supplies ``page.wait_for_timeout``; tests pass a clock-advancing
    stub); ``clock()`` is the time source (defaults to ``time.monotonic``).
    ``artifact_fn()`` (optional) supplies the current capture artifact for a
    REDACTED evidence bundle; ``log_fn(event)`` (optional) receives structured,
    secret-free log events. **No widget is ever interacted with** -- between waits
    this only re-runs DETECTION.

    Returns the ``ChallengeHandler`` in its reached state:
      * inert (``state is None``)        -- no challenge; caller proceeds normally;
      * ``challenge_cleared_observed``   -- the site's challenge self-cleared and
        the detector CONFIRMED it gone -> resumable;
      * ``operator_action_required``     -- the passive budget elapsed (or was 0)
        with the challenge still present; a redacted evidence bundle + neutral
        noVNC instructions were surfaced via ``log_fn``; the run is PAUSED awaiting
        a human. Resume stays gated by ``operator_complete()`` + ``can_resume()``.
    """
    if clock is None:
        clock = time.monotonic

    handler = ChallengeHandler(observe_fn())
    if not handler.is_active():
        return handler  # zero-cost normal-site path: a single cheap observation

    if log_fn is not None:
        try:
            log_fn(handler.to_log_event())
        except Exception:
            pass

    budget = max(0.0, float(passive_budget_s))
    if budget > 0.0:
        handler.begin_passive_wait()
        step = max(0.001, float(poll_interval_s))
        deadline = clock() + budget
        while clock() < deadline:
            if tick_fn is not None:
                try:
                    tick_fn(step)
                except Exception:
                    pass
            # re-run DETECTION on a fresh observation; advance to a resumable
            # state ONLY if the detector now confirms the challenge is gone.
            fresh = observe_fn()
            if not observation_is_conclusive(fresh):
                # Row 122: a page we could not READ is UNKNOWN, not cleared. The
                # observation builder degrades every failed read to "", and an
                # empty observation carries no markers, so the detector would
                # answer "absent" and resume a run over a page nobody saw. Keep
                # waiting instead; the budget still ends in an operator handoff.
                continue
            if handler.observe(fresh):
                if log_fn is not None:
                    try:
                        log_fn(handler.to_log_event())
                    except Exception:
                        pass
                # Row 122: the explicit detector-cleared / resume EVENT, filed
                # once, by the detector path that actually observed the clear.
                emit_challenge_resume_event(handler, fresh, log_fn=log_fn)
                return handler  # challenge_cleared_observed (resumable)
        handler.mark_passive_timeout()

    # Still present (or no passive budget): route to a HUMAN. Never solve.
    handler.require_manual_handoff()
    artifact = None
    if artifact_fn is not None:
        try:
            artifact = artifact_fn()
        except Exception:
            artifact = None
    handoff = handler.hand_off_to_operator(artifact)
    if log_fn is not None:
        try:
            log_fn({**handler.to_log_event(), "handoff": handoff})
        except Exception:
            pass
    return handler


def handle_challenge_on_page(page, *, passive_budget_s: Optional[float] = None,
                             poll_interval_s: float = 1.0,
                             artifact_fn=None, log_fn=None,
                             clock=None) -> ChallengeHandler:
    """Thin LIVE seam: detect/handle a challenge on a real Playwright ``page`` via
    the pure :func:`drive_challenge_handling` driver. Builds the read-only
    observation callback and a real page-load wait tick from ``page``; performs no
    widget interaction itself. Not exercised by the unit suite (needs a real
    browser); the logic it delegates to is unit-tested with synthetic callbacks.

    The caller (the held-open capture runner) surfaces the returned handler's
    ``operator_instructions()`` in noVNC when it is paused for a human, and gates
    the actual resume on ``operator_complete()`` + ``can_resume()``.
    """
    if passive_budget_s is None:
        passive_budget_s = DEFAULT_PASSIVE_BUDGET_S

    def _observe():
        return observe_page_for_challenge(page)

    def _tick(seconds):
        # the site's OWN challenge is given the normal browser's page-load wait to
        # clear on its own; we never drive or interact with the challenge.
        try:
            page.wait_for_timeout(max(0.0, float(seconds)) * 1000.0)
        except Exception:
            pass

    return drive_challenge_handling(
        _observe, tick_fn=_tick, passive_budget_s=passive_budget_s,
        poll_interval_s=poll_interval_s, artifact_fn=artifact_fn,
        log_fn=log_fn, clock=clock,
    )


# ── P3-T12-CALLSITE (backlog row 122): the explicit detector-cleared / resume
#    EVENT ───────────────────────────────────────────────────────────────────
#
# Detect, pause and handoff were already observed live -- a human completed a
# real challenge in the noVNC browser -- but the held-open runner then DISCARDED
# the handler, so there was no first-class record of the one fact that authorises
# the run to continue: THE DETECTOR CONFIRMED THE CHALLENGE IS GONE. Reading that
# fact off later authenticated traffic would be asserting over a subject the
# instrument never saw, so the event is emitted by the detector path itself or
# not at all.
#
# The event is deliberately THREE-VALUED, because the two-valued version is a
# fail-open. ``observe_page_for_challenge`` degrades every failed read to an
# empty string, and an empty observation carries no challenge markers, so the
# keyword detector answers "absent" -- which the passive loop used to accept as a
# confirmed clear. A page that could not be READ is UNKNOWN, and UNKNOWN never
# resumes (CLAUDE.md A7: unavailable measurement returns UNKNOWN, not OK).
#
# What this adds is a record and a refusal. It still never interacts with a
# widget, never claims automation cleared anything (``solved`` stays False), and
# carries no raw challenge material -- only the decision, the reached state, the
# routing labels and a reason.

CHALLENGE_RESUME_EVENT = "challenge_resume"     # distinct from "challenge_handling"
RESUME_DECISION_RESUMED = "resumed"             # detector confirmed gone -> resume
RESUME_DECISION_BLOCKED = "blocked"             # still challenged -> stay paused
RESUME_DECISION_UNKNOWN = "unknown"             # cannot tell -> stay paused

# The states the framework reaches only AFTER the detector confirmed absence.
# Used solely as a fallback when a caller passes a duck-typed handler with no
# ``can_resume``; the handler's own gate is authoritative when it has one, and
# ``test_row122_challenge_resume_event`` pins this set against the framework's.
_RESUMABLE_STATES = frozenset({"challenge_cleared_observed",
                               "operator_handoff_complete"})

_RESUME_EVENT_ATTR = "_bd_challenge_resume_event"
_MISSING = object()


def observation_is_conclusive(observation: Any) -> bool:
    """True only when the observation carries something the detector can judge.

    ``observe_page_for_challenge`` returns empty strings for every read that
    raised, so an all-empty observation is indistinguishable from a clean page to
    a keyword detector. It is not evidence of absence -- it is the absence of
    evidence, and this predicate is what keeps the two apart.
    """
    if not isinstance(observation, dict):
        return False
    for key in ("text", "title"):
        value = observation.get(key)
        if isinstance(value, str) and value.strip():
            return True
    markers = observation.get("markers")
    if isinstance(markers, (list, tuple, set, frozenset)):
        for marker in markers:
            if str(marker).strip():
                return True
    return False


def _resume_event(decision: str, handler, state, detector_cleared,
                  reason: str) -> Dict[str, Any]:
    try:
        challenge_type = str(handler.challenge_type)
    except Exception:
        challenge_type = "unknown"
    labels: List[str] = []
    try:
        raw_labels = handler.to_log_event().get("labels")
        if isinstance(raw_labels, (list, tuple)):
            labels = [str(x) for x in raw_labels]
    except Exception:
        labels = []
    return {
        "event": CHALLENGE_RESUME_EVENT,
        "decision": decision,
        # tri-state on purpose: True / False / None(UNKNOWN). Never defaulted.
        "detector_cleared": detector_cleared,
        "resume_permitted": decision == RESUME_DECISION_RESUMED,
        "state": state,
        "challenge_type": challenge_type,
        "labels": labels,
        "reason": reason,
        "solved": False,                 # automation never clears a challenge
        "raw_challenge_material": False,  # asserted: decision + state only
    }


def challenge_resume_event(handler, fresh_observation=None
                           ) -> Optional[Dict[str, Any]]:
    """Build the explicit detector-cleared / resume event for ``handler``.

    Returns ``None`` -- no event at all -- when no challenge was ever detected;
    a run over a clean site must stay exactly as quiet as it was before. A
    handler that is missing entirely (a degraded seam) is UNKNOWN, not OK.

    ``fresh_observation`` is the observation the decision is made over. Passing
    ``None`` means "no fresh reading was taken", and the decision then rests on
    the handler's own detector-gated state; passing an observation the detector
    could not read yields UNKNOWN rather than a clear.
    """
    if handler is None:
        return _resume_event(RESUME_DECISION_UNKNOWN, None, None, None,
                             "detector_unavailable")
    state = getattr(handler, "state", _MISSING)
    if state is _MISSING:
        return _resume_event(RESUME_DECISION_UNKNOWN, handler, None, None,
                             "detector_state_unavailable")
    if state is None:
        return None                      # inert: no challenge, so no event
    if fresh_observation is not None and not observation_is_conclusive(fresh_observation):
        return _resume_event(RESUME_DECISION_UNKNOWN, handler, state, None,
                             "observation_inconclusive")
    try:
        permitted = bool(handler.can_resume(fresh_observation))
    except Exception:
        permitted = state in _RESUMABLE_STATES
    if permitted:
        return _resume_event(RESUME_DECISION_RESUMED, handler, state, True,
                             "detector_confirmed_challenge_gone")
    return _resume_event(RESUME_DECISION_BLOCKED, handler, state, False,
                         "paused_in_" + str(state))


def recorded_resume_event(handler) -> Optional[Dict[str, Any]]:
    """The resume event already recorded on ``handler``, or None."""
    if handler is None:
        return None
    return getattr(handler, _RESUME_EVENT_ATTR, None)


def emit_challenge_resume_event(handler, fresh_observation=None, log_fn=None
                                ) -> Optional[Dict[str, Any]]:
    """Record the resume decision for this handler and hand it to ``log_fn``.

    ONLY A ``resumed`` DECISION IS TERMINAL, and only a terminal decision claims
    the once-per-run slot: the passive-clear path in the driver and the held-open
    call site cannot both file the same clear, and a second call after a clear
    returns ``None``. A ``blocked`` or ``unknown`` decision is a reading of a run
    that is still paused, so it must NOT latch -- latching it would leave the
    human's later clear permanently unreportable, which is the very fail-open
    this row exists to refuse. Never raises.
    """
    if recorded_resume_event(handler) is not None:
        return None
    event = challenge_resume_event(handler, fresh_observation)
    if event is None:
        return None
    if handler is not None and event["decision"] == RESUME_DECISION_RESUMED:
        try:
            setattr(handler, _RESUME_EVENT_ATTR, event)
        except Exception:
            pass
    if log_fn is not None:
        try:
            log_fn(event)
        except Exception:
            pass
    return event


def emit_resume_when_cleared(handler, observe_fn, log_fn=None
                             ) -> Optional[Dict[str, Any]]:
    """Poll a paused run and emit the resume event WHEN, and only when, the
    detector confirms the challenge is gone.

    This is the operator path made reachable: after a human handles the challenge
    in the noVNC browser, the held-open loop calls this once per tick, it re-runs
    DETECTION on a fresh read of the same page, and only a conclusive, cleared
    reading advances the handler and files the event. Nothing here interacts with
    the page beyond the read-only observation the caller supplies.

    Zero cost on a clean run: an inert handler returns before ``observe_fn`` is
    ever called, so a capture with no challenge never pays for this path. Returns
    the event on the tick that resumed, otherwise ``None``. Never raises.
    """
    if handler is None:
        return None
    state = getattr(handler, "state", None)
    if state is None:
        return None                      # inert: never even observe the page
    if recorded_resume_event(handler) is not None:
        return None                      # already filed: exactly once per run
    try:
        fresh = observe_fn()
    except Exception:
        return None                      # nothing observed -> nothing claimed
    if not observation_is_conclusive(fresh):
        return None                      # UNKNOWN is not a clear and not an event
    if state not in _RESUMABLE_STATES:
        # An operator signal is not proof either: the framework re-runs the
        # detector and refuses the transition while the challenge is present.
        try:
            if not bool(handler.operator_complete(fresh)):
                return None
        except Exception:
            return None
    return emit_challenge_resume_event(handler, fresh, log_fn=log_fn)
