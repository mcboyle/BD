"""Row 741: a failed relogin is filed by WHO refused it and WHY -- by TYPE.

Row 710 split ``auto_relogin_refused`` (we refused) from ``auto_relogin_fail``
(the site refused) because the two lead to opposite remedies.  It did so with a
substring test on the detail string, which collapsed two more opposite
remedies into one event -- a cap that could not be MEASURED (repair the
attempt store) and a cap that was REACHED (wait for the day to roll) -- and
misfiled a site reply that merely CONTAINS the marker phrase as our own
refusal.  The keeper callback now returns a typed ``SelfRefusal`` naming the
event, and ``relogin_event_type`` reads the type: no phrase is consulted.

BD_GATE_SCOPE: module -- the subject is the keeper's relogin event vocabulary
and the one real callback ``app._start_session_keepers`` registers.
"""
# CI-SHARD-CLAIM row-741 application-safety -- listed beside row667/row740
# in .github/workflows/ci.yml and in the 939 gate's _DECLARED set.
import ast
import importlib
import inspect

BD_GATE_SCOPE = "module"

_CAP_PHRASE = "daily login attempt cap"
_KEEPER_CFG = {"keep_alive_enabled": True, "username": "fixture-user",
               "password": "fixture-password"}


def _drive_keeper(monkeypatch, db, session_keeper, site_id, outcome):
    """Run one heartbeat-fail -> relogin cycle whose callback returns OUTCOME.

    Returns the ordered event types the keeper filed for SITE_ID.  The
    fixture's shape is proven before any verdict: a drive that filed nothing
    would let every count-zero assertion pass vacuously.
    """
    keeper = session_keeper.SessionKeeper(
        site_id, 0, dict(_KEEPER_CFG), lambda *_a, **_k: outcome)
    monkeypatch.setattr(
        keeper, "_heartbeat",
        lambda: (session_keeper.DEAD, "fixture heartbeat failure"))
    keeper._run_one_check()
    with db.db_conn() as cx:
        events = [row["event_type"] for row in cx.execute(
            "SELECT event_type FROM session_history WHERE site_id=? "
            "ORDER BY id ASC", (site_id,)).fetchall()]
    assert events.count("heartbeat_fail") == 1, (
        f"precondition: the drive must file exactly one heartbeat_fail; {events}")
    return events


def _real_keeper_callback(monkeypatch, app, login, session_keeper):
    """Capture the ONE real adapter app registers, with the site never reached
    unless a test installs its own ``login.do_login``."""
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {"row741-site": dict(_KEEPER_CFG)})
    registered = []
    monkeypatch.setattr(
        session_keeper, "start_keeper",
        lambda site_id, account_idx, cfg, callback: registered.append(
            (site_id, account_idx, cfg, callback)))
    app._start_session_keepers()
    assert len(registered) == 1, (
        "precondition: exactly one keeper callback must be registered; "
        f"got {len(registered)}")
    site_id, account_idx, cfg, callback = registered[0]
    return lambda: callback(site_id, account_idx, cfg)


def _fresh_db(monkeypatch, tmp_path, name):
    db = importlib.import_module("bulk_downloader.db")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / name))
    db.db_init()
    return db


def test_cap_unavailable_and_cap_reached_are_distinct_events(
        monkeypatch, tmp_path):
    """Repair-the-store and wait-for-the-day must not share an event type."""
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    db = _fresh_db(monkeypatch, tmp_path, "row741-distinct.db")
    monkeypatch.setattr(
        login, "do_login",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("row741: a refused relogin contacted the site")))
    call = _real_keeper_callback(monkeypatch, app, login, session_keeper)

    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: {"status": "UNKNOWN", "count": None,
                         "reason": "fixture store unavailable"})
    unavailable = call()
    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: {"status": "OK", "count": 3, "reason": None})
    reached = call()
    assert unavailable[0] is False and reached[0] is False, (unavailable, reached)
    assert _CAP_PHRASE in str(unavailable[1]) and _CAP_PHRASE in str(reached[1]), (
        "precondition: both refusals carry the row-710 marker phrase, which is "
        f"exactly why a substring classifier cannot tell them apart: "
        f"{unavailable[1]!r} / {reached[1]!r}")

    ev_unavailable = _drive_keeper(
        monkeypatch, db, session_keeper, "row741-unavailable", unavailable)
    ev_reached = _drive_keeper(
        monkeypatch, db, session_keeper, "row741-reached", reached)
    relogin_unavailable = [e for e in ev_unavailable if e.startswith("auto_relogin")]
    relogin_reached = [e for e in ev_reached if e.startswith("auto_relogin")]
    assert len(relogin_unavailable) == 1 and len(relogin_reached) == 1, (
        ev_unavailable, ev_reached)
    assert relogin_reached == [session_keeper.RELOGIN_REFUSED_EVENT], (
        f"a reached cap must file as the wait-for-the-day event: {ev_reached}")
    assert relogin_unavailable != relogin_reached, (
        "row741: cap-unavailable (repair the store) and cap-reached (wait for "
        "the day) were filed under ONE event type "
        f"{relogin_reached[0]!r}: opposite remedies collapsed")
    expected = getattr(session_keeper, "RELOGIN_CAP_UNAVAILABLE_EVENT", None)
    assert relogin_unavailable == [expected], (
        f"an unmeasurable cap must file as {expected!r}: {ev_unavailable}")


def test_a_site_reply_containing_the_cap_phrase_is_a_site_failure(
        monkeypatch, tmp_path):
    """The site's own words, wrapped as ``login failed: ...``, are the site's."""
    app = importlib.import_module("bulk_downloader.app")
    login = importlib.import_module("bulk_downloader.login")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    db = _fresh_db(monkeypatch, tmp_path, "row741-site-reply.db")
    site_words = "daily login attempt cap reached (3/3) -- try tomorrow"
    contacts = []
    monkeypatch.setattr(
        login, "do_login",
        lambda *a, **k: contacts.append(a) or (False, site_words, []))
    monkeypatch.setattr(
        session_keeper, "login_attempts_for_day",
        lambda site_id: {"status": "OK", "count": 0, "reason": None})
    monkeypatch.setattr(
        session_keeper, "reserve_login_attempt",
        lambda *a, **k: {"granted": True, "status": "OK", "count": 1,
                         "cap": 3, "reason": None})
    call = _real_keeper_callback(monkeypatch, app, login, session_keeper)
    outcome = call()
    assert len(contacts) == 1, "precondition: the site must have been contacted"
    assert outcome[0] is False and _CAP_PHRASE in str(outcome[1]), (
        "precondition: the site reply must carry the marker phrase: "
        f"{outcome!r}")

    events = _drive_keeper(
        monkeypatch, db, session_keeper, "row741-site-reply", outcome)
    assert events.count(session_keeper.RELOGIN_REFUSED_EVENT) == 0, (
        "row741: a site reply that merely CONTAINS the cap phrase was filed "
        f"as our own refusal: {events}")
    assert events.count("auto_relogin_fail") == 1, events


def test_relogin_event_type_decides_by_type_not_by_phrase():
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    classify = session_keeper.relogin_event_type
    tree = ast.parse(inspect.getsource(classify))
    substring_ops = [
        node for node in ast.walk(tree)
        if (isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops))
        or (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"find", "startswith", "endswith",
                                   "search", "match", "index"})]
    assert substring_ops == [], (
        "row741: relogin_event_type still classifies by substring: "
        f"{[ast.dump(n)[:80] for n in substring_ops]}")
    # Behavioural half: the marker phrase in a PLAIN string is not a refusal.
    for text in ("daily login attempt cap reached (3/3)",
                 "login failed: daily login attempt cap reached", "", None):
        assert classify(text) == "auto_relogin_fail", (text, classify(text))
    typed = session_keeper.SelfRefusal(
        session_keeper.RELOGIN_CAP_UNAVAILABLE_EVENT, "store went away")
    assert classify(typed) == session_keeper.RELOGIN_CAP_UNAVAILABLE_EVENT
    assert typed == "store went away" and isinstance(typed, str), (
        "a typed refusal must still be the detail string every reader expects")


def test_every_typed_refusal_is_counted_as_a_refusal_and_documented(
        monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    kinds = session_keeper.RELOGIN_SELF_REFUSAL_EVENTS
    assert len(kinds) == 3 and len(set(kinds)) == 3, kinds
    doc = db.session_event_record.__doc__ or ""
    for event in kinds:
        assert event not in db._SESSION_FAILURE_EVENTS, event
        assert event not in db._SESSION_SUCCESS_EVENTS, event
        assert event in db._SESSION_REFUSAL_EVENTS, (
            f"{event!r} would be invisible to the cockpit's refusal counter")
        assert f"'{event}'" in doc, f"session_event_record does not document {event!r}"
    _fresh_db(monkeypatch, tmp_path, "row741-counted.db")
    for event in kinds:
        _drive_keeper(monkeypatch, db, session_keeper, f"row741-{event}",
                      (False, session_keeper.SelfRefusal(event, "fixture")))
    per_site = db.db_session_failure_clusters(lookback_days=1)["per_site"]
    assert len(per_site) == 3, per_site
    for event in kinds:
        counters = per_site[f"row741-{event}"]
        assert counters["failures"] == 1, (event, counters)   # the heartbeat_fail only
        assert counters["auto_relogin_refused"] == 1, (event, counters)


def test_negative_control_a_substring_classifier_is_caught(
        monkeypatch, tmp_path):
    """Reinstall the row-710 phrase test and prove the site-reply gate trips."""
    session_keeper = importlib.import_module("bulk_downloader.session_keeper")
    db = _fresh_db(monkeypatch, tmp_path, "row741-control.db")
    monkeypatch.setattr(
        session_keeper, "relogin_event_type",
        lambda detail: (session_keeper.RELOGIN_REFUSED_EVENT
                        if _CAP_PHRASE in str(detail or "")
                        else "auto_relogin_fail"))
    outcome = (False, "login failed: daily login attempt cap reached (3/3)")
    events = _drive_keeper(
        monkeypatch, db, session_keeper, "row741-control", outcome)
    misfiled = events.count(session_keeper.RELOGIN_REFUSED_EVENT)
    assert misfiled == 1, (
        "the control must reproduce exactly one misfiled site reply; "
        f"got {misfiled}: {events}")
    assert events.count("auto_relogin_fail") == 0, events
