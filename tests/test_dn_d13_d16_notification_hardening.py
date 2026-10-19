"""deep-notifications D13-D16 (O1789 bughunt, FINDINGS.md S24-S29).

D13 per-site hooks._dispatch and global webhooks.fire forward a failure message
    carrying a signed query (?token=...&sig=...) to the receiver unredacted.
D14 NotificationDispatcher: a failed send is never retried; a failed batch is lost.
D15 _flush_batch joins item titles/bodies with "\\n" unescaped: a filename forges a line.
D16 tg_bot._http_get_json/_http_post_json log url[:60], which carries bot<TOKEN>.

No network: sinks are monkeypatched at the module seam each function calls.
Every assertion message starts with DN-D<n>.
"""
import json
import logging
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import pytest

from bulk_downloader import hooks, notify_apprise as na, tg_bot, webhooks
from bulk_downloader.capture_artifact_redact import redact_value

BD_GATE_SCOPE = "module"

SECRET = "SIGNEDSECRET42"
FAIL_MSG = (f"HTTP 403 fetching https://cdn.example.com/v/a.mp4"
            f"?token={SECRET}&sig=abcdef0123456789&expires=1700000000")


# ── D13 ───────────────────────────────────────────────────────────────

def test_d13_control_redact_value_masks_the_failure_message():
    masked = redact_value(FAIL_MSG)
    assert SECRET not in masked, f"DN-D13 control: redact_value kept the token: {masked!r}"
    assert "cdn.example.com/v/a.mp4" in masked, f"DN-D13 control: host/path lost: {masked!r}"


def test_d13_site_hook_payload_message_is_redacted(monkeypatch):
    sent = []
    monkeypatch.setattr(hooks, "_send_site_webhook",
                        lambda url, payload: (sent.append(json.loads(json.dumps(payload, default=str)))
                                              or (True, "ok", 1)))
    filename = "2024-01-01_Title_1080p_XyZ_AbCdEfGhIjKlMnOp.mp4"
    job = {"url": "https://site.example/watch/1", "filename": filename,
           "message": FAIL_MSG, "error": FAIL_MSG}
    vars = hooks.build_vars({"name": "s"}, "failed", job)
    hooks._dispatch("failed", {"webhook_urls": "http://127.0.0.1:9/h"}, vars, job)
    assert len(sent) == 1, f"DN-D13 setup: webhook not sent ({len(sent)} calls)"
    body = json.dumps(sent[0])
    assert SECRET not in body, f"DN-D13: signed token reached the site webhook receiver: {body[:400]}"
    assert sent[0]["message"] == redact_value(FAIL_MSG), f"DN-D13: message not redact_value'd: {sent[0]['message']!r}"
    assert sent[0]["job"]["error"] == redact_value(FAIL_MSG), f"DN-D13: job.error not redacted"
    assert sent[0]["filename"] == filename, f"DN-D13: non-message field altered: {sent[0]['filename']!r}"
    assert vars["message"] == FAIL_MSG and job["message"] == FAIL_MSG, "DN-D13: caller dicts mutated"


@pytest.fixture
def _clean_queue():
    webhooks._ensure_tables()
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("DELETE FROM webhook_queue")
        cx.execute("DELETE FROM webhook_subscriptions")
    yield


def test_d13_global_webhook_fire_message_is_redacted(_clean_queue):
    sid = webhooks.add_subscription(url="http://127.0.0.1:9/h", events=["download.failed"], secret="")
    assert isinstance(sid, int), "DN-D13 setup: add_subscription refused"
    payload = {"site_id": 1, "url": "https://site.example/watch/1",
               "filename": "a.mp4", "message": FAIL_MSG[:300], "ts": 0}
    webhooks.fire("download.failed", payload)
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        rows = [r[0] for r in cx.execute("SELECT payload FROM webhook_queue").fetchall()]
    assert len(rows) == 1, f"DN-D13 setup: {len(rows)} queued rows"
    assert SECRET not in rows[0], f"DN-D13: signed token queued for the global webhook receiver: {rows[0][:400]}"
    assert json.loads(rows[0])["message"] == redact_value(FAIL_MSG[:300]), "DN-D13: queued message not redact_value'd"
    assert payload["message"] == FAIL_MSG[:300], "DN-D13: caller payload mutated"


# ── D14 / D15 ─────────────────────────────────────────────────────────

class _Send:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, urls, title, body, tag=None, **k):
        self.calls.append({"title": title, "body": body, "tag": tag})
        ok = self.outcomes.pop(0) if self.outcomes else True
        r = na.SendResult()
        if ok:
            r.sent_count = 1
        else:
            r.failed_count = 1
            r.errors.append(("__send__", "apprise_returned_false"))
        return r


def _dispatcher(monkeypatch, outcomes, policy=None):
    fake = _Send(outcomes)
    monkeypatch.setattr(na, "send", fake)
    d = na.NotificationDispatcher()
    monkeypatch.setattr(d, "_maybe_start_timer", lambda: None)
    d.configure(urls=["json://127.0.0.1:9/x"], enabled=True, policy_overrides=policy or {})
    return d, fake


def test_d14_per_event_failed_send_is_retried_once(monkeypatch):
    d, fake = _dispatcher(monkeypatch, [False, True])
    d.notify(na.EVENT_CAPTCHA, "captcha", "solve me")
    assert len(fake.calls) == 2, f"DN-D14: per-event failed send resent {len(fake.calls) - 1} times (want 1)"


def test_d14_per_event_retry_is_bounded(monkeypatch, caplog):
    d, fake = _dispatcher(monkeypatch, [False, False, False])
    with caplog.at_level(logging.INFO, logger=na.log.name):
        d.notify(na.EVENT_CAPTCHA, "captcha", "solve me")
    assert len(fake.calls) == 2, f"DN-D14: per-event retry unbounded ({len(fake.calls)} calls)"
    assert "apprise: retry failed, dropped" in caplog.text, f"DN-D14: no drop diagnostic: {caplog.text!r}"


def test_d14_failed_batch_is_requeued_once_then_dropped(monkeypatch, caplog):
    d, fake = _dispatcher(monkeypatch, [False, True],
                          policy={na.EVENT_DOWNLOAD_DONE: ("batched", 2, 60.0)})
    d.notify(na.EVENT_DOWNLOAD_DONE, "a.mp4", "done")
    d.notify(na.EVENT_DOWNLOAD_DONE, "b.mp4", "done")
    assert len(fake.calls) == 1, f"DN-D14 setup: batch not flushed ({len(fake.calls)} calls)"
    pending = d._pending.get(na.EVENT_DOWNLOAD_DONE)
    assert pending is not None and len(pending.items) == 2, (
        f"DN-D14: failed batch lost ({0 if pending is None else len(pending.items)} pending after)")
    d.flush_all()
    assert len(fake.calls) == 2 and "a.mp4" in fake.calls[1]["body"], "DN-D14: re-queued batch not resent"
    assert not d._pending, "DN-D14: delivered batch still pending"
    # second failure of a re-queued batch drops it: bounded
    d2, fake2 = _dispatcher(monkeypatch, [False, False, False],
                            policy={na.EVENT_DOWNLOAD_DONE: ("batched", 2, 60.0)})
    d2.notify(na.EVENT_DOWNLOAD_DONE, "a.mp4", "done")
    d2.notify(na.EVENT_DOWNLOAD_DONE, "b.mp4", "done")
    with caplog.at_level(logging.INFO, logger=na.log.name):
        d2.flush_all()
        d2.flush_all()
    assert len(fake2.calls) == 2, f"DN-D14: batch retry unbounded ({len(fake2.calls)} calls)"
    assert not d2._pending, "DN-D14: twice-failed batch still pending"
    assert "apprise: batch retry failed, dropped" in caplog.text, f"DN-D14: no drop diagnostic: {caplog.text!r}"


def test_d15_batched_filename_cannot_forge_a_line(monkeypatch):
    d, fake = _dispatcher(monkeypatch, [True],
                          policy={na.EVENT_DOWNLOAD_DONE: ("batched", 2, 60.0)})
    d.notify(na.EVENT_DOWNLOAD_DONE, "evil.mp4\n- forged.mp4: done", "done")
    d.notify(na.EVENT_DOWNLOAD_DONE, "b.mp4", "done\r\n- forged2")
    assert len(fake.calls) == 1, "DN-D15 setup: batch not flushed"
    lines = fake.calls[0]["body"].split("\n")
    assert len(lines) == 2, f"DN-D15: 2-item batch rendered {len(lines)} lines: {lines!r}"
    assert all(l.startswith("- ") for l in lines) and "\r" not in fake.calls[0]["body"], f"DN-D15: {lines!r}"


# ── D16 ───────────────────────────────────────────────────────────────

TOKEN = "123456789:AAHsecretSECRETsecretSECRETsecret0123"


class _Client:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return SimpleNamespace(status_code=401, json=lambda: {})

    def post(self, url, json=None):
        return SimpleNamespace(status_code=401, json=lambda: {})


@pytest.fixture
def _fake_httpx(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=_Client))
    from bulk_downloader import ssrf_transport
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: None)
    monkeypatch.setattr(tg_bot, "_is_httpx_available", lambda: True)


@pytest.mark.parametrize("verb", ["GET", "POST"])
def test_d16_tg_api_url_token_not_logged(_fake_httpx, caplog, verb):
    url = f"{tg_bot._TG_API_BASE}/bot{TOKEN}/getUpdates"
    with caplog.at_level(logging.DEBUG, logger=tg_bot.log.name):
        if verb == "GET":
            assert tg_bot._http_get_json(url) is None
        else:
            assert tg_bot._http_post_json(url, {"a": 1}) is None
    assert f"tg_bot: {verb} " in caplog.text, f"DN-D16 control: debug line not captured: {caplog.text!r}"
    secret = TOKEN.split(":", 1)[1]
    assert secret[:8] not in caplog.text and TOKEN.split(":")[0] not in caplog.text, (
        f"DN-D16: bot token reached the log: {caplog.text!r}")
    assert "/bot<redacted>/getUpdates" in caplog.text, f"DN-D16: masked url missing: {caplog.text!r}"
