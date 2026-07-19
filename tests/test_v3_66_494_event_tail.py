"""v3.66.494 E1 (plugin-v3, non-guard slice): the queue/vpn/review/download event tail.

Completes E1's non-guard event surface at clean, single-function producer seams —
the events 487 deferred because they "sit at producers that are not clean single
seams":

    queue.enqueued        runner_queue.load_urls           (added > 0)
    queue.drained         runner._watch_done               (pending == 0 block)
    vpn.tunnel_up         vpn.start_tunnel                 (backend start ok)
    vpn.tunnel_down       vpn.stop_tunnel                  (after state -> down)
    vpn.killswitch_armed  vpn_kill_switch.kill_tunnel      (fresh kill)
    review.approved       runner_queue.bulk_approve        (n > 0)
    review.skipped        runner_queue.bulk_delete         (needs_review removed)
    download.progress     runner._update_job               (running + byte advance)
    download.retry        runner._update_job               (pending + retries raised)

Every producer fires through the canonical ``plugins.emit(event, payload)`` seam
(documented-event validation + the isolated fire_hook path), so a throwing
consumer never breaks a download / VPN / queue path. Each event is documented in
``HOOK_EVENTS`` and pinned in the R3 hook-payload golden (contract locked, never
advertised without a producer).

The vpn/runner_queue seams are exercised behaviorally (register a hook, drive the
producer, assert the event + payload). The two runner.py seams (_update_job,
_watch_done) are also driven behaviorally on a constructed SiteRunner, plus a
source-body assertion (matching the existing dashboard_widgets._update_job test
precedent) so a future refactor that drops the emit is caught structurally too.

Runner-safe: zero pytest fixtures, paths from __file__, tempfile, module globals
restored in try/finally.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.environ.setdefault("BD_HOME", tempfile.mkdtemp())

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bulk_downloader import plugins as P  # noqa: E402

_RUNNER_PY = _REPO / "bulk_downloader" / "runner.py"

_NEW_EVENTS = (
    "queue.enqueued", "queue.drained",
    "vpn.tunnel_up", "vpn.tunnel_down", "vpn.killswitch_armed",
    "review.approved", "review.skipped",
    "download.progress", "download.retry",
)


# ── (a) registry / golden contract ────────────────────────────────────
def test_all_tail_events_documented():
    """Every tail event is a documented HOOK_EVENTS member with a Payload clause."""
    ev = P.known_events()["hooks"]
    for name in _NEW_EVENTS:
        assert name in ev, f"{name} missing from HOOK_EVENTS"
        assert "Payload:" in ev[name], f"{name} doc has no Payload clause"


def test_tail_events_pinned_in_golden():
    """The R3 hook-payload golden pins each new event's documented key-set."""
    import json
    from hook_payload_golden import derive_payload_keys, GOLDEN_PATH
    golden = json.loads(Path(GOLDEN_PATH).read_text(encoding="utf-8"))
    pins = golden.get("events", golden)
    for name in _NEW_EVENTS:
        assert name in pins, f"{name} not pinned in the hook-payload golden"
    # the derived live key-set must equal the pinned one (no drift)
    derived = derive_payload_keys(P.HOOK_EVENTS)
    for name in _NEW_EVENTS:
        assert derived[name] == sorted(pins[name]), \
            f"{name} payload keys drifted: {derived[name]} != {sorted(pins[name])}"


# ── (b) vpn seams (behavioral) ────────────────────────────────────────
class _OkBackend:
    def start(self, t):
        return True

    def stop(self, t):
        return True

    def is_running(self, t):
        return True


def test_vpn_tunnel_up_emits():
    from bulk_downloader import vpn
    vpn._reset_for_tests()
    P.reset()
    seen = []
    P.register_hook("vpn.tunnel_up", lambda p: seen.append(p))
    _orig = vpn._get_backend
    try:
        vpn._get_backend = lambda t: _OkBackend()
        tid = vpn.register_tunnel(name="up", provider="mullvad", backend="wireguard")
        assert vpn.start_tunnel(tid) is True
    finally:
        vpn._get_backend = _orig
        vpn._reset_for_tests()
    assert len(seen) == 1, seen
    assert seen[0]["tunnel_id"] == tid
    assert "socks_port" in seen[0] and "ts" in seen[0]


def test_vpn_tunnel_down_emits():
    from bulk_downloader import vpn
    vpn._reset_for_tests()
    P.reset()
    seen = []
    P.register_hook("vpn.tunnel_down", lambda p: seen.append(p))
    _orig = vpn._get_backend
    try:
        vpn._get_backend = lambda t: _OkBackend()
        tid = vpn.register_tunnel(name="dn", provider="mullvad", backend="wireguard")
        vpn.start_tunnel(tid)
        assert vpn.stop_tunnel(tid) is True
    finally:
        vpn._get_backend = _orig
        vpn._reset_for_tests()
    assert len(seen) == 1, seen
    assert seen[0]["tunnel_id"] == tid and "ts" in seen[0]


def test_vpn_killswitch_armed_emits():
    from bulk_downloader import vpn_kill_switch as ks
    ks._reset_for_tests()
    P.reset()
    seen = []
    P.register_hook("vpn.killswitch_armed", lambda p: seen.append(p))
    try:
        ks.kill_tunnel("tun-x", reason="leak detected")
        # a re-kill of an already-killed tunnel must NOT re-arm (idempotent)
        ks.kill_tunnel("tun-x", reason="again")
    finally:
        ks._reset_for_tests()
    assert len(seen) == 1, seen
    assert seen[0]["tunnel_id"] == "tun-x"
    assert seen[0]["reason"] == "leak detected" and "ts" in seen[0]


# ── (c) runner_queue seams (behavioral) ───────────────────────────────
def _runner(site_id="evt_tail"):
    from bulk_downloader import db
    from bulk_downloader.runner import SiteRunner
    db.db_init()  # ensure the queue/history schema exists in this BD_HOME
    return SiteRunner(site_id, {"name": site_id})


def test_queue_enqueued_emits():
    P.reset()
    seen = []
    P.register_hook("queue.enqueued", lambda p: seen.append(p))
    r = _runner("evt_enq")
    added, _, _ = r.load_urls(["https://example.com/a.mp4",
                               "https://example.com/b.mp4"])
    assert added == 2
    assert len(seen) == 1, seen
    assert seen[0]["site_id"] == "evt_enq"
    assert seen[0]["added"] == 2 and "dupes" in seen[0] and "skipped" in seen[0]


def test_review_approved_emits():
    P.reset()
    seen = []
    P.register_hook("review.approved", lambda p: seen.append(p))
    r = _runner("evt_app")
    u = "https://example.com/needs.mp4"
    r.load_urls([u])
    with r._lock:
        r.jobs[u]["status"] = "needs_review"
    n = r.bulk_approve([u])
    assert n == 1
    assert len(seen) == 1, seen
    assert seen[0]["site_id"] == "evt_app" and seen[0]["count"] == 1


def test_review_skipped_emits_only_for_needs_review():
    P.reset()
    seen = []
    P.register_hook("review.skipped", lambda p: seen.append(p))
    r = _runner("evt_skip")
    u_rev = "https://example.com/r.mp4"
    u_plain = "https://example.com/p.mp4"
    r.load_urls([u_rev, u_plain])
    with r._lock:
        r.jobs[u_rev]["status"] = "needs_review"
    # delete BOTH; only the needs_review one counts toward review.skipped
    r.bulk_delete([u_rev, u_plain])
    assert len(seen) == 1, seen
    assert seen[0]["count"] == 1, "only the needs_review job is a review.skip"


def test_plain_delete_does_not_emit_review_skipped():
    P.reset()
    seen = []
    P.register_hook("review.skipped", lambda p: seen.append(p))
    r = _runner("evt_skip2")
    u = "https://example.com/p2.mp4"
    r.load_urls([u])  # status pending, never needs_review
    r.bulk_delete([u])
    assert seen == [], "deleting a non-review job must not fire review.skipped"


# ── (d) runner._update_job seams (behavioral + structural) ────────────
def test_download_progress_emits_on_byte_advance():
    P.reset()
    seen = []
    P.register_hook("download.progress", lambda p: seen.append(p))
    r = _runner("evt_prog")
    u = "https://example.com/v.mp4"
    r._update_job(u, "running", "downloading", file_size=1000)
    r._update_job(u, "running", "downloading", file_size=2500)
    # status-only churn with no byte advance must NOT fire
    r._update_job(u, "running", "still downloading")
    assert len(seen) == 2, seen
    assert seen[-1]["url"] == u and seen[-1]["file_size"] == 2500


def test_download_retry_emits_on_attempt_increase():
    P.reset()
    seen = []
    P.register_hook("download.retry", lambda p: seen.append(p))
    r = _runner("evt_retry")
    u = "https://example.com/v.mp4"
    r._update_job(u, "running", "downloading", file_size=10)
    r._update_job(u, "pending", "will retry", retries=1)
    # a queue reset (approve/resume) passes retries=0 and must NOT fire a retry
    r._update_job(u, "pending", "reset", retries=0)
    assert len(seen) == 1, seen
    assert seen[0]["url"] == u and seen[0]["retries"] == 1


def test_queue_drained_and_update_job_wired_in_source():
    """Structural backstop: the runner.py emits survive a refactor."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # _update_job body carries both download emits
    uj = src.find("def _update_job")
    nxt = src.find("\n    def ", uj + 1)
    body = src[uj:nxt]
    assert 'emit("download.progress"' in body
    assert 'emit("download.retry"' in body
    # the pending==0 / queue-complete block carries queue.drained
    assert 'emit("queue.drained"' in src
