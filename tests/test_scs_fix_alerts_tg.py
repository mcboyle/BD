"""scs-fix (bughunt sweep spa-cockpit-settings, finder bd-worker-S52-A): backend halves.

F4  an alert rule id holding "/" saved ok:true but POST /api/alerts/rules/<id>/remove
    never routed to it (404 forever, the rule kept evaluating). save_rule now refuses
    such an id (400); "?" ids stay legal and remove through the encoded path.
F5a list_rules never set `builtin`, so the SPA showed Remove on built-ins, which
    answered ok:false silently. Defaults without an override are now builtin=True.
F5c the SPA's single-line allowlist field says "comma-separated", but parse_allowlist
    took only the first token of each line: "111,222" parsed to ZERO chat ids -- the
    Telegram card's "0 allowlisted" was that true count (save -> configure() sets
    allowlist_size), and the bot answered no one.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"


def _new_client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init

    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _rule(rid):
    return {"id": rid, "metric": "bd_job_failures_1h", "op": ">=", "threshold": 2}


# ── F4: rule ids that can never be removed ────────────────────────────


def test_f4_slash_rule_id_is_refused_at_save():
    c, H = _new_client()
    r = c.post("/api/alerts/rules", json=_rule("scs/x"), headers=H)
    assert r.status_code == 400, (
        "BD-SCS-F4-SLASH-ID-SAVED",
        r.status_code,
        r.get_json(),
    )
    assert "/" in r.get_json()["error"]
    ids = [x["id"] for x in c.get("/api/alerts/rules").get_json()["rules"]]
    assert "scs/x" not in ids, ("BD-SCS-F4-SLASH-ID-LISTED", ids)


def test_f4_question_mark_rule_id_removes_through_the_encoded_path():
    """Control: the refusal is "/" only. A "?" id saves, and the SPA's encoded remove
    path (encodeURIComponent -> %3F) deletes it."""
    c, H = _new_client()
    assert (
        c.post("/api/alerts/rules", json=_rule("scs?x"), headers=H).get_json()["ok"]
        is True
    )
    r = c.post("/api/alerts/rules/scs%3Fx/remove", headers=H)
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()
    ids = [x["id"] for x in c.get("/api/alerts/rules").get_json()["rules"]]
    assert "scs?x" not in ids


def test_f4_engine_refuses_slash_ids_only():
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_init

    db_init()
    assert ae.save_rule(_rule("a/b")) is None
    assert ae.save_rule(_rule("/")) is None
    assert ae.save_rule(_rule("a?b#c%d")) == "a?b#c%d"


# ── F5a: built-in flag ─────────────────────────────────────────────────


def test_f5a_defaults_are_builtin_custom_and_overridden_are_not():
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_init

    db_init()
    ae.save_rule(_rule("scs_custom"))
    ae.save_rule({**_rule("high_failure_rate"), "metric": "bd_failure_rate_1h"})
    by_id = {r["id"]: r for r in ae.list_rules()}
    defaults = [d["id"] for d in ae.DEFAULT_RULES]
    assert by_id["disk_low"]["builtin"] is True, (
        "BD-SCS-F5A-NO-BUILTIN",
        by_id["disk_low"],
    )
    assert all(
        by_id[d]["builtin"] is True for d in defaults if d != "high_failure_rate"
    )
    # an overridden default keeps Remove (it drops the override) ...
    assert by_id["high_failure_rate"]["builtin"] is False
    assert ae.delete_rule("high_failure_rate") is True
    # ... and is builtin again once the override is gone
    assert {r["id"]: r for r in ae.list_rules()}["high_failure_rate"]["builtin"] is True
    assert by_id["scs_custom"]["builtin"] is False


def test_f5a_a_stored_builtin_key_cannot_hide_a_custom_rule():
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader.db import db_init

    db_init()
    ae.save_rule({**_rule("scs_spoof"), "builtin": True})
    assert {r["id"]: r for r in ae.list_rules()}["scs_spoof"]["builtin"] is False


# ── F5c: comma-separated allowlist ─────────────────────────────────────


def test_f5c_comma_separated_allowlist_parses_every_id():
    from bulk_downloader import tg_bot as t

    assert t.parse_allowlist("111,222") == [111, 222], "BD-SCS-F5C-COMMA-DROPPED"
    assert t.parse_allowlist("111, 222 ,\n333 Matt") == [111, 222, 333]
    assert t.parse_allowlist("-1001234567890,12345 phone") == [-1001234567890, 12345]
    # unchanged forms
    assert t.parse_allowlist("12345 Matt\n# c, d\nbogus,67890") == [12345, 67890]
    assert t.parse_allowlist(",,") == []


def test_f5c_saved_comma_allowlist_reaches_the_bot():
    """S52-A repro: save "111,222" from the SPA, read the card's source /api/tg/status."""
    c, H = _new_client()
    r = c.post("/api/tg/settings", json={"tg_bot_allowlist": "111,222"}, headers=H)
    assert r.status_code == 200, r.get_json()
    s = c.get("/api/tg/status").get_json()
    assert s.get("available") is True, s
    assert s["allowlist_size"] == 2, ("BD-SCS-F5C-STATUS-COUNT", s)
