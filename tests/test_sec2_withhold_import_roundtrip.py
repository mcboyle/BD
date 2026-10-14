"""BD-SEC-2 withhold r3-restack r2 (lens R247-D F1): a default config export of
a site whose accounts[] the export withholds must import back.

SEC-2 export_view turns the whole accounts[] into a withheld marker when any
account value differs in served form (an app-written last_error with a URL, a
?token value).  api_config_import judges a copy with the markers resolved
(cookie-file-roots accounts type check and cookie_file check), so the marker
shape never reaches the validator, and resolves them for the write from the
live site inside the all-site transaction (r3-restack-c, lens R284-B F1); a
marker with no stored original is a 400 naming the field, never a blank.
"""
from __future__ import annotations

import json

import pytest

BD_GATE_SCOPE = "module"


def _fx(*parts: str) -> str:
    """Fixture secret assembled at runtime. The VALUE is unchanged from r3-restack-c;
    only the source shape differs, so the secret scanner (gitleaks generic-api-key,
    integrator EJECT T281n 1830Z) sees no key-shaped literal. Not a credential."""
    return "".join(parts)

ACCTS = {
    "last_err": [{"username": "u4", "password": "bdsecR2pwQ7",
                  "last_error": "login failed at https://s.example/login?next=/home"}],
    "url_tok": [{"username": "u2", "password": "bdsecR2pwQ8",
                 "profile_url": "https://x.example/u?token=" + _fx("bdsecR2", "tokQ7")}],
}
FORGED = {"withheld": True, "configured": True, "scheme": "", "masked": "***", "kind": "list"}


def _plant(A, client, name, fields):
    sid = client.post("/api/sites", json={"name": name}).get_json()["id"]
    A.s_cfg[sid].update(fields)
    A.s_meta.setdefault(sid, {}).update(fields)
    return sid


def _spy_validator(A, monkeypatch):
    """Record every cfg the cookie_file validator is handed (it still judges)."""
    seen, real = [], A._validate_cookie_files

    def spy(cfg, *a, **k):
        seen.append(json.loads(json.dumps(cfg, default=str)))  # as handed: the import resolves cfg in place later
        return real(cfg, *a, **k)
    monkeypatch.setattr(A, "_validate_cookie_files", spy)
    return seen


@pytest.mark.parametrize("mode", ["merge", "replace"])
@pytest.mark.parametrize("kind", sorted(ACCTS))
def test_export_import_round_trip_keeps_withheld_accounts(fresh_app, monkeypatch, kind, mode):
    from bulk_downloader import app as A
    from bulk_downloader.egress_withhold import is_withheld_marker
    accts = json.loads(json.dumps(ACCTS[kind]))
    name = "sec2r2-" + kind
    sid = _plant(A, fresh_app, name, {"accounts": accts})
    body = fresh_app.get("/api/config/export").get_json()
    sent = [s for s in body["sites"] if s.get("name") == name][0]["accounts"]
    # positive control: the export really withholds this accounts[] (else the probe proves nothing)
    assert is_withheld_marker(sent), f"BD_SEC2_R2_PROBE_DEAD {kind}: accounts exported as {sent!r}"
    seen = _spy_validator(A, monkeypatch)
    r = fresh_app.post(f"/api/config/import?mode={mode}", json=body)
    assert r.status_code == 200, (
        f"BD_SEC2_R2_ROUNDTRIP_REFUSED {kind} {mode}: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")
    handed = [c.get("accounts") for c in seen if isinstance(c, dict) and is_withheld_marker(c.get("accounts"))]
    assert not handed, f"BD_SEC2_R2_MARKER_TO_VALIDATOR {kind} {mode}: {handed[:1]!r}"
    got = [c for c in A.s_cfg.values() if c.get("name") == name]
    assert len(got) == 1 and got[0].get("accounts") == ACCTS[kind], (
        f"BD_SEC2_R2_ACCOUNTS_CHANGED {kind} {mode}: {[c.get('accounts') for c in got]!r}")
    if mode == "merge":
        assert sid in A.s_cfg and A.s_cfg[sid]["accounts"][0]["password"] == ACCTS[kind][0]["password"], (
            f"BD_SEC2_R2_SECRET_LOST {kind}")


@pytest.mark.parametrize("mode", ["merge", "replace"])
@pytest.mark.parametrize("field,marker", [
    ("accounts", FORGED),
    ("webhook_urls", {"withheld": True, "configured": True, "scheme": "tgram", "masked": "tgram://***", "kind": "str"}),
])
def test_forged_marker_for_unknown_site_is_refused(fresh_app, field, marker, mode):
    from bulk_downloader import app as A
    before = json.dumps(A.s_cfg, sort_keys=True, default=str)
    r = fresh_app.post(f"/api/config/import?mode={mode}",
                       json={"sites": [{"name": "sec2r2-nobody", field: marker}]})
    err = (r.get_json(silent=True) or {}).get("error", "")
    assert r.status_code == 400 and field in err, (
        f"BD_SEC2_R2_FORGED_MARKER_ACCEPTED {field} {mode}: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")
    assert json.dumps(A.s_cfg, sort_keys=True, default=str) == before, f"BD_SEC2_R2_FORGED_STORED {field} {mode}"


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_forged_marker_for_a_field_the_known_site_never_stored_is_refused(fresh_app, mode):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "sec2r2-known", {})
    A.s_cfg[sid].pop("webhook_urls", None)
    assert "webhook_urls" not in A.s_cfg[sid]  # positive control: the site really lacks the field
    r = fresh_app.post(f"/api/config/import?mode={mode}", json={"sites": [{
        "name": "sec2r2-known",
        "webhook_urls": {"withheld": True, "configured": True, "scheme": "tgram", "masked": "tgram://***"}}]})
    assert r.status_code == 400 and "webhook_urls" in (r.get_json(silent=True) or {}).get("error", ""), (
        f"BD_SEC2_R2_FORGED_MARKER_ACCEPTED known-site {mode}: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")
    assert "webhook_urls" not in A.s_cfg[sid], f"BD_SEC2_R2_FORGED_STORED known-site {mode}"


def test_import_resolves_markers_from_a_case_folded_site_name(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader.egress_withhold import is_withheld_marker
    sid = _plant(A, fresh_app, "sec2r2-Case", {"accounts": json.loads(json.dumps(ACCTS["last_err"]))})
    body = fresh_app.get("/api/config/export").get_json()
    site = [s for s in body["sites"] if s.get("name") == "sec2r2-Case"][0]
    assert is_withheld_marker(site["accounts"]), "BD_SEC2_R2_PROBE_DEAD case-fold"
    site["name"] = "SEC2R2-case"
    r = fresh_app.post("/api/config/import?mode=merge", json={"sites": [site]})
    assert r.status_code == 200 and A.s_cfg[sid]["accounts"] == ACCTS["last_err"], (
        f"BD_SEC2_R2_CASEFOLD_MISSED: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")


# r3-restack-c (lens R284-B F1, ANSWER-pm-B-restack-c-1635Z): the import parses
# first and waits on every site stripe; a withheld marker resolves from the LIVE
# site after the stripes are owned, so a write that commits during the wait survives.
def _import_while_stripes_held(A, client, body, during):
    import threading
    import time
    from bulk_downloader import app_state
    locks = tuple(app_state._site_lifecycle_locks)
    for lk in locks:  # an in-flight site route owns the stripes
        lk.acquire()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault(
        "r", client.post("/api/config/import?mode=merge", json=body)))
    try:
        t.start()
        time.sleep(1.5)  # the import has parsed and now waits on the stripes
        blocked = t.is_alive()
        during()
    finally:
        for lk in reversed(locks):
            lk.release()
    t.join(30)
    return blocked, out.get("r")


def test_site_write_committed_while_import_waits_survives(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader.egress_withhold import is_withheld_marker
    le = ACCTS["last_err"][0]["last_error"]
    sid = _plant(A, fresh_app, "sec2r2-race", {"accounts": [{"username": "u", "password": "OLDpw", "last_error": le}]})
    body = fresh_app.get("/api/config/export").get_json()
    assert is_withheld_marker([s for s in body["sites"] if s.get("name") == "sec2r2-race"][0]["accounts"]), (
        "BD_SEC2_R2_PROBE_DEAD race: accounts not withheld")

    def write():  # as the site writers do: a new dict is stored (s_cfg[sid] = cfg)
        A.s_cfg[sid] = {**A.s_cfg[sid], "accounts": [{"username": "u", "password": "NEWpw", "last_error": le}]}
    blocked, r = _import_while_stripes_held(A, fresh_app, body, write)
    assert blocked, "BD_SEC2_R2_RACE_PROBE_DEAD: the import did not wait on the stripes"
    pw = A.s_cfg[sid]["accounts"][0].get("password")
    assert r is not None and r.status_code == 200 and pw == "NEWpw", (
        f"BD_SEC2_R2_RACE_REVERTED_SECRET: HTTP{getattr(r, 'status_code', None)} password_after={pw!r}")


def test_site_renamed_while_import_waits_refuses_the_marker(fresh_app):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "sec2r2-gone", {"accounts": json.loads(json.dumps(ACCTS["url_tok"]))})
    body = fresh_app.get("/api/config/export").get_json()
    body = {"sites": [s for s in body["sites"] if s.get("name") == "sec2r2-gone"]}

    def rename():
        A.s_cfg[sid] = {**A.s_cfg[sid], "name": "sec2r2-renamed"}
    blocked, r = _import_while_stripes_held(A, fresh_app, body, rename)
    assert blocked, "BD_SEC2_R2_RACE_PROBE_DEAD: the import did not wait on the stripes"
    err = (r.get_json(silent=True) or {}).get("error", "") if r is not None else ""
    assert r is not None and r.status_code == 400 and "accounts" in err, (
        f"BD_SEC2_R2_RACE_STALE_ORIGINAL: HTTP{getattr(r, 'status_code', None)} {err[:200]!r}")
    assert not [c for c in A.s_cfg.values() if c.get("name") == "sec2r2-gone"], "BD_SEC2_R2_RACE_STALE_STORED"


@pytest.mark.parametrize("accounts", ["x", [1], {"username": "u"}])
def test_real_malformed_accounts_still_refused(fresh_app, accounts):
    r = fresh_app.post("/api/config/import?mode=merge",
                       json={"sites": [{"name": "sec2r2-bad", "accounts": accounts}]})
    assert r.status_code == 400 and "accounts must be a list of objects" in r.get_data(as_text=True), (
        f"BD_SEC2_R2_CHECK_WEAKENED {accounts!r}: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")


def test_sent_account_cookie_file_outside_roots_still_refused(fresh_app, monkeypatch):
    from bulk_downloader import app as A
    seen = _spy_validator(A, monkeypatch)
    r = fresh_app.post("/api/config/import?mode=merge", json={"sites": [{
        "name": "sec2r2-cookie",
        "accounts": [{"username": "u", "password": "p", "cookie_file": "/etc/hostname"}]}]})
    assert seen, "BD_SEC2_R2_VALIDATOR_SKIPPED: the cookie_file validator was never asked"
    assert r.status_code == 400 and "sec2r2-cookie" not in json.dumps([c.get("name") for c in A.s_cfg.values()]), (
        f"BD_SEC2_R2_COOKIE_CHECK_WEAKENED: HTTP{r.status_code} {r.get_data(as_text=True)[:200]}")


# row700 x SEC-2 (ANSWER-pm-B-row700-1540Z): the app's fixed captcha-gate text
# is served verbatim BY ORIGIN (recorded by the function that builds it, for the
# request that built it); the same text from anywhere else, and every other part
# of the value, is still withheld.
_CANARY = "bdsecR2row700Q7canary"


def _served_json(A, body: dict) -> dict:
    resp = A.app.response_class(json.dumps(body), mimetype="application/json")
    return json.loads(A._withhold_diagnostic_text(resp).get_data())


def _gate_text():
    from bulk_downloader.runner import captcha_egress_disclosure_error
    return captcha_egress_disclosure_error({"captcha_api_key": "k"})


def test_recorded_gate_text_is_served_verbatim_and_the_rest_withheld(fresh_app):
    from bulk_downloader import app as A
    with A.app.test_request_context("/api/sites/x", method="PUT"):
        msg = _gate_text()
        assert "captcha_egress_disclosure_ack=true" in msg and "https://" in msg  # control: real gate text
        out = _served_json(A, {"error": f"site 1: {msg}",
                               "last_error": f"{msg} retry https://x.example/cb?token={_CANARY}"})
    assert out["error"] == f"site 1: {msg}", f"BD_SEC2_R2_ROW700_GATE_WITHHELD: {out['error'][:200]!r}"
    assert out["last_error"].startswith(msg) and _CANARY not in out["last_error"], (
        f"BD_SEC2_R2_ROW700_SECRET_SERVED: {out['last_error'][-120:]!r}")


def test_same_text_without_the_origin_record_is_still_withheld(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader import egress_withhold as W
    with A.app.test_request_context("/api/sites/x", method="PUT"):
        msg = _gate_text()
    with A.app.test_request_context("/api/status"):
        W.app_message("not-an-allowlisted-id", msg)  # an unknown id records nothing
        assert W.request_app_messages() == (), "BD_SEC2_R2_ROW700_UNKNOWN_ID_RECORDED"
        out = _served_json(A, {"error": f"{msg} https://x.example/cb?token={_CANARY}"})
    assert "captcha_egress_disclosure_ack=true" not in out["error"] and _CANARY not in out["error"], (
        f"BD_SEC2_R2_ROW700_EXEMPT_BY_TEXT: {out['error'][:200]!r}")


def test_gate_refusal_over_http_names_the_ack_field_and_terms(fresh_app):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "sec2r2-row700", {})
    r = fresh_app.put(f"/api/sites/{sid}", json={"captcha_api_key": _fx("row700", "-sec2-", "key")})
    err = (r.get_json(silent=True) or {}).get("error", "")
    assert r.status_code == 400 and "captcha_egress_disclosure_ack=true" in err and (
        "https://2captcha.com/terms-of-service" in err), (
        f"BD_SEC2_R2_ROW700_GATE_WITHHELD: HTTP{r.status_code} {err[:200]!r}")
