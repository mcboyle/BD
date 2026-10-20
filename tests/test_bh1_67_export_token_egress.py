"""BH1-67 export-endpoints-token-egress (RULING pm-B export-token-egress).

/api/export/csv, /api/export/json and /api/activity/v2/export.csv served the
history url and message as stored: a ?token= / userinfo secret reached any
client. They now serve the display URL (db.queue_display_url) and the
withheld message (egress_withhold.withhold_text), CSV-neutralised after; the
raw form only under the history export's own include_tokens gate (a client
on this machine). queue_display_url also masks the userinfo of a formula-led
``=//u:pw@host`` and of the scheme-less ``u:pw@host/p`` form.
"""
from __future__ import annotations

import csv
import io
import json
from urllib.parse import unquote

import pytest

BD_GATE_SCOPE = "module"

URL_CANARY = "SECRETCANARYbh67url"
MSG_CANARY = "SECRETCANARYbh67msg"
LEAD_CANARY = "SECRETCANARYbh67lead"
JOB = f"https://r.example/dl/a.mp4?token={URL_CANARY}&page=2"
LEAD_JOB = f"=//u:{LEAD_CANARY}@r.example/b.mp4"
MESSAGE = f"fetch failed for https://cdn.example/x?sig={MSG_CANARY}"
CANARIES = (URL_CANARY, MSG_CANARY, LEAD_CANARY)
REMOTE = {"REMOTE_ADDR": "10.0.0.5"}
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}

ENDPOINTS = {
    "export-csv": ("/api/export/csv", CANARIES),
    "export-json": ("/api/export/json", CANARIES),
    "activity-csv": ("/api/activity/v2/export.csv?window=all", (MSG_CANARY,)),
}
# What include_tokens serves raw.  A JSON body's "message" stays withheld even
# then: app._withhold_diagnostic_text (BD-SEC-2) withholds it on every JSON response.
RAW = {"export-csv": CANARIES, "export-json": (URL_CANARY, LEAD_CANARY),
       "activity-csv": (MSG_CANARY,)}


def _has(text: str, canary: str) -> bool:
    for _ in range(6):
        if canary in text:
            return True
        text, prev = unquote(text), text
        if text == prev:
            break
    return canary in text


def _plant(client) -> str:
    from bulk_downloader.db import db_log
    sid = client.post("/api/sites", json={"name": "bh67"}).get_json()["id"]
    db_log(sid, "bh67", JOB, "failed", message=MESSAGE)
    db_log(sid, "bh67", LEAD_JOB, "failed", message="plain bh67 note")
    return sid


def _path(name: str, raw: bool) -> str:
    p = ENDPOINTS[name][0]
    return p + ("&" if "?" in p else "?") + "include_tokens=1" if raw else p


def _get(client, path: str, env) -> str:
    r = client.get(path, environ_base=env)
    assert r.status_code == 200, f"BD_BH67_EXPORT_PROBE_BLIND: {path} {env} -> {r.status_code}"
    text = r.get_data(as_text=True)
    assert "bh67" in text, f"BD_BH67_EXPORT_PROBE_BLIND: planted rows absent on {path}"
    return text


@pytest.mark.parametrize("env", [REMOTE, LOCAL], ids=["remote", "loopback"])
@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_export_withholds_tokens_by_default(fresh_app, name, env):
    _plant(fresh_app)
    text = _get(fresh_app, _path(name, raw=False), env)
    leaks = [c for c in ENDPOINTS[name][1] if _has(text, c)]
    assert not leaks, f"BD_BH67_EXPORT_TOKEN_LEAK: {name} {env['REMOTE_ADDR']} served {leaks}"


@pytest.mark.parametrize("name", sorted(ENDPOINTS))
def test_export_raw_only_on_the_local_opt_in(fresh_app, name):
    _plant(fresh_app)
    path = _path(name, raw=True)
    text = _get(fresh_app, path, LOCAL)
    blind = [c for c in RAW[name] if not _has(text, c)]
    assert not blind, f"BD_BH67_EXPORT_PROBE_BLIND: local include_tokens lacks {blind}"
    r = fresh_app.get(path, environ_base=REMOTE)
    body = r.get_data(as_text=True)
    assert r.status_code == 403 and not any(_has(body, c) for c in CANARIES), (
        f"BD_BH67_EXPORT_TOKEN_LEAK: include_tokens from a remote client -> {r.status_code}")


def test_export_csv_masks_then_neutralises_a_formula_led_url(fresh_app):
    _plant(fresh_app)
    rows = list(csv.DictReader(io.StringIO(_get(fresh_app, "/api/export/csv", REMOTE))))
    led = [r["url"] for r in rows if "r.example/b.mp4" in r["url"]]
    assert led, "BD_BH67_EXPORT_PROBE_BLIND: formula-led row absent"
    assert led[0].startswith("'=//") and LEAD_CANARY not in led[0], (
        f"BD_BH67_EXPORT_FORMULA_URL: {led[0]!r}")
    data = json.loads(_get(fresh_app, "/api/export/json", REMOTE))
    assert any(r.get("url") == "=//***@r.example/b.mp4" for r in data), (
        f"BD_BH67_EXPORT_FORMULA_URL: json {[r.get('url') for r in data]!r}")


USERINFO_FORMS = {
    "formula-lead": ("=//u:{c}@r.example/p", "=//***@r.example/p"),
    "formula-lead-plus": ("+//{c}@r.example/p", "+//***@r.example/p"),
    "scheme-less": ("u:{c}@r.example/p", "***@r.example/p"),
    "scheme-less-port": ("u:{c}@r.example:8443/p?sig=1", "***@r.example:8443/p?sig=***"),
}


@pytest.mark.parametrize("form", sorted(USERINFO_FORMS))
def test_display_url_masks_formula_led_and_scheme_less_userinfo(form):
    from bulk_downloader.db import queue_display_url, queue_url_masked
    raw, want = USERINFO_FORMS[form]
    shown = queue_display_url(raw.format(c="SECRETCANARYbh67ui"))
    assert shown == want, f"BD_BH67_USERINFO_LEAK: {form} -> {shown!r}"
    assert queue_display_url(shown) == shown and queue_url_masked(shown), (
        f"BD_BH67_USERINFO_NOT_IDEMPOTENT: {form} -> {shown!r}")


@pytest.mark.parametrize("value", ["=//r.example/p", "u@r.example/p", "r.example/p:8",
                                   "not a url", "=SUM(A1)", "-1"])
def test_display_url_keeps_values_without_userinfo_secret(value):
    from bulk_downloader.db import queue_display_url
    assert queue_display_url(value) == value, (
        f"BD_BH67_USERINFO_OVERMASK: {value!r} -> {queue_display_url(value)!r}")
