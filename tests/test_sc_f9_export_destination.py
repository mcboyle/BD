"""sc-f9: where a scheduled export may write, and what its retention may remove.

Scheduler/cron sweep F9 + lens D173 A3 (O1789). Three rules, each beside a
control that shows the probe can answer the other way:
  1 a destination resolves (realpath) under the export root or a folder of the
    scheduled_export_allowed_dirs setting, else it is refused and nothing is
    created; destinations that predate the rule are allowlisted once on upgrade.
    r3 (PM 18:32Z): the app never disables a schedule over its destination -- a
    run whose destination is not allowed is skipped, the row is not written,
    and it runs again once the folder is allowed or the setting is readable;
  2 retention_count >= 1, and retention never removes the export just written;
  3 retention unlinks regular files that carry this schedule's exact name only.
"""
import os
import time

import pytest

from bulk_downloader import scheduled_exports as se

BD_GATE_SCOPE = "module"


KEY = "scheduled_export_allowed_dirs"


def _forget_config():
    import sys
    from bulk_downloader import global_config as gc
    gc._cached, gc._cached_mtime = None, 0.0
    kernel = sys.modules.get("bulk_downloader.app_kernel")
    if kernel is not None:
        kernel._app_cfg.pop(KEY, None)          # the app's own copy outlives a test


@pytest.fixture
def store(clean_workdir):
    from bulk_downloader import db
    from bulk_downloader.migrations import apply_pending
    db.db_init()
    apply_pending(backup_first=False)
    se._ensure_table()
    _forget_config()
    yield db
    _forget_config()


def _allowlist():
    from bulk_downloader import global_config as gc
    return gc.get(KEY, "<unset>")


def _set_allowlist(text):
    from bulk_downloader import global_config as gc
    assert gc.set_config({KEY: text})


@pytest.fixture
def root(store):
    # Spelled out here, not read from the module: the test states the rule.
    home = os.environ.get("BD_HOME") or os.environ.get("BD_INSTALL_DIR") or os.getcwd()
    path = os.path.realpath(os.path.join(home, "exports"))
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture
def outside(tmp_path_factory):
    return os.path.realpath(tmp_path_factory.mktemp("outside-the-root"))


def _client():
    from bulk_downloader import app as A
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


def _add(dest, **kw):
    return se.add_schedule(label="t", format=kw.pop("format", "csv"), destination=dest, cadence_hours=24, **kw)


def _legacy_row(db, dest, retention=10, fmt="csv"):
    """A row as an older build stored it: no check on destination or retention."""
    with db.db_conn() as cx:
        return cx.execute(
            "INSERT INTO scheduled_exports(label, format, destination, cadence_hours, filter_json, retention_count,"
            " created_at, next_run_ts) VALUES ('legacy', ?, ?, 24, '{}', ?, ?, 0)",
            (fmt, dest, retention, time.time())).lastrowid


def _raw(sid):
    """The stored row, every column, as bytes-on-disk say (not the list API)."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        return dict(cx.execute("SELECT * FROM scheduled_exports WHERE id = ?", (sid,)).fetchone())


def _skipped(out):
    return [d["id"] for d in out.get("skipped_destination_not_allowed", [])]


def _restart():
    """What a new process starts with: no cached settings, nothing remembered."""
    _forget_config()
    for state in ("_skip_logged", "_pending", "_pending_logged"):
        getattr(se, state, set()).clear()


def _row(sid):
    return [s for s in se.list_schedules() if s["id"] == sid][0]


def _names(path):
    return sorted(os.listdir(path))


# ── 1: the destination must resolve under the export root ─────────────
def test_1_a_destination_under_the_root_is_accepted_and_created(root):
    dest = os.path.join(root, "weekly", "csv")
    sid = _add(dest)
    assert sid is not None and os.path.isdir(dest)
    assert _row(sid)["destination"] == dest and _row(sid).get("destination_allowed") is True
    assert _add(root) is not None                       # the root itself


def test_1_a_destination_outside_the_root_is_refused_and_nothing_is_created(root, outside):
    dest = os.path.join(outside, "new", "nested")
    assert _add(dest) is None
    assert _names(outside) == []                        # no makedirs outside the root
    assert se.list_schedules() == []


@pytest.mark.parametrize("shape", ["dotdot", "sibling_prefix", "link_out", "link_out_nested", "relative"])
def test_1_a_destination_that_only_looks_like_it_is_under_the_root_is_refused(root, outside, shape):
    if shape == "dotdot":
        dest = os.path.join(root, "..", "escaped")
        created = os.path.join(os.path.dirname(root), "escaped")
    elif shape == "sibling_prefix":
        dest = created = root + "-evil"
    elif shape == "relative":
        dest, created = "elsewhere", os.path.join(os.getcwd(), "elsewhere")
    else:
        os.symlink(outside, os.path.join(root, "link"))
        dest = os.path.join(root, "link") if shape == "link_out" else os.path.join(root, "link", "sub")
        created = os.path.join(outside, "sub")
    assert _add(dest) is None
    assert not os.path.exists(created) or shape == "link_out"
    assert _names(outside) == []


def test_1_api_answers_400_and_creates_nothing(root, outside):
    c, h = _client()
    dest = os.path.join(outside, "via-api")
    r = c.post("/api/scheduled_exports/add", headers=h, json={"format": "csv", "destination": dest, "cadence_hours": 24})
    assert r.status_code == 400 and root in r.get_json()["error"]
    assert _names(outside) == []
    ok = c.post("/api/scheduled_exports/add", headers=h,
                json={"format": "csv", "destination": os.path.join(root, "via-api"), "cadence_hours": 24})
    assert ok.status_code == 200 and os.path.isdir(os.path.join(root, "via-api"))       # control


def test_1_upgrade_allowlists_existing_destinations_once_and_disables_nothing(store, root, outside):
    old_dest = os.path.join(outside, "old-export")
    legacy = _legacy_row(store, old_dest)
    good = _add(os.path.join(root, "good"))             # the first call after "upgrade" runs the seed
    assert _allowlist() == old_dest
    out = se.run_due_exports()
    assert _skipped(out) == [] and (out["checked"], out["ran"]) == (2, 2)
    assert _row(legacy)["enabled"] == 1 and _row(legacy).get("destination_allowed") is True
    assert len(_names(old_dest)) == 1                   # the old schedule kept exporting where it always did
    assert _row(good)["last_run_ok"] == 1
    # once: a row that appears out of root AFTER the seed is not allowlisted; its runs are skipped, the row untouched
    late_dest = os.path.join(outside, "late")
    late = _legacy_row(store, late_dest)
    before = _raw(late)
    out = se.run_due_exports(force=True)
    assert _skipped(out) == [late] and (out["checked"], out["ran"], out["errors"]) == (2, 2, 0)
    assert _allowlist() == old_dest and not os.path.exists(late_dest)
    assert _raw(late) == before and before["enabled"] == 1
    row = _row(late)
    assert row.get("destination_allowed") is False and row.get("last_status") == "destination not allowed"
    assert "last_status" not in _row(legacy)            # control: an allowed row carries no such status


def test_1_a_folder_removed_from_the_allowlist_skips_runs_and_never_writes_the_row(store, root, outside, capsys):
    dest = os.path.join(outside, "nas")
    _set_allowlist(outside)
    sid = _add(dest)
    assert sid is not None and se.run_due_exports()["ran"] == 1       # control: allowed, it runs
    files, before = _names(dest), _raw(sid)
    _set_allowlist("")
    capsys.readouterr()
    for _ in range(3):
        out = se.run_due_exports(force=True)
        assert _skipped(out) == [sid] and (out["checked"], out["ran"], out["errors"]) == (0, 0, 0)
    assert _names(dest) == files                                      # nothing written, nothing removed
    assert _raw(sid) == before and before["enabled"] == 1             # not disabled, not marked failed
    assert _row(sid)["last_status"] == "destination not allowed"
    assert capsys.readouterr().err.count(f"schedule {sid} skipped, destination not allowed") == 1   # logged once
    _set_allowlist(outside)                                           # allowed again: it runs, nobody re-enables it
    out = se.run_due_exports(force=True)
    assert _skipped(out) == [] and out["ran"] == 1 and len(_names(dest)) == len(files) + 1
    assert "last_status" not in _row(sid)
    _set_allowlist("")                                                # removed a second time: said again, once
    capsys.readouterr()
    se.run_due_exports(force=True)
    se.run_due_exports(force=True)
    assert capsys.readouterr().err.count(f"schedule {sid} skipped, destination not allowed") == 1


def _break_setting(how, monkeypatch):
    """Make the allowlist setting unreadable/unwritable; returns the undo."""
    from bulk_downloader import global_config as gc
    saved = open("app_config.json", "rb").read() if os.path.exists("app_config.json") else None
    if how == "set_config_false":
        monkeypatch.setattr(gc, "set_config", lambda updates: False)
    elif how == "corrupt":
        with open("app_config.json", "w") as fh:
            fh.write("{ not json")
    elif how == "chmod":
        if saved is None:
            with open("app_config.json", "w") as fh:
                fh.write("{}")
            saved = b"{}"
        os.chmod("app_config.json", 0)
        assert os.geteuid() != 0, "chmod 0 proves nothing as root"
    elif how == "missing":
        if saved is not None:
            os.remove("app_config.json")
    elif how == "wrong_type":
        with open("app_config.json", "w") as fh:
            fh.write('{"%s": ["/"]}' % KEY)
    _restart()

    def undo():
        monkeypatch.undo()
        if os.path.exists("app_config.json"):
            os.chmod("app_config.json", 0o600)
        if how != "set_config_false":
            if saved is None:
                os.remove("app_config.json")
            else:
                with open("app_config.json", "wb") as fh:
                    fh.write(saved)
        _restart()
    return undo


@pytest.mark.parametrize("failure", ["set_config_false", "corrupt", "chmod"])
def test_1_a_failed_upgrade_pass_disables_nothing_and_the_schedule_runs_after_recovery(
        store, root, outside, monkeypatch, failure):
    """Lens D201 R1: the seed could not save the setting, and the pass that
    followed it disabled every schedule the seed was there to keep, for good."""
    old_dest = os.path.join(outside, "old-export")
    legacy = _legacy_row(store, old_dest)
    before = _raw(legacy)
    undo = _break_setting(failure, monkeypatch)
    for _ in range(3):                                   # three starts while it is broken
        _restart()
        out = se.run_due_exports()
        assert _raw(legacy)["enabled"] == 1, "a failed allowlist seed disabled the schedule it exists to keep"
        assert _skipped(out) == [legacy] and (out["ran"], out["errors"]) == (0, 0)
    assert _raw(legacy) == before and not os.path.exists(old_dest)    # refused meanwhile, row not written
    undo()
    out = se.run_due_exports()                           # still due: it was never advanced
    assert _allowlist() == old_dest
    assert _raw(legacy)["enabled"] == 1 and (out["checked"], out["ran"]) == (1, 1)
    assert len(_names(old_dest)) == 1


@pytest.mark.parametrize("name", ["legacy ", " legacy", "leg\nacy", "leg\racy\n", "leg\u2028acy", "#legacy", '"legacy"', " "])
def test_1_the_seed_admits_a_destination_the_line_format_would_change(store, root, outside, name):
    """Lens R14 R2: the seed wrote a destination with outer whitespace or a line
    break as a line the reader strips or splits, set its marker, and the same
    pass disabled the schedule it had just seeded."""
    old_dest = os.path.join(outside, name)
    legacy = _legacy_row(store, old_dest)
    for _ in range(3):
        _restart()
        out = se.run_due_exports(force=True)
        assert _raw(legacy)["enabled"] == 1 and _skipped(out) == [], f"seeded as {_allowlist()!r}, then not admitted"
        assert (out["checked"], out["ran"]) == (1, 1)
    assert len(_names(old_dest)) == 3                    # it exports where it always did, that exact folder
    assert _row(legacy)["destination_allowed"] is True
    # exactly that folder: the neighbour the stripped line would have named is not allowed
    assert _add(os.path.join(outside, "legacy-neighbour")) is None
    if name.strip() and name.strip() != name:
        assert _add(os.path.join(outside, name.strip())) is None


@pytest.mark.parametrize("shape", ["non_utf8_folder", "control_char", "nul", "too_long"])
def test_1_one_destination_the_store_cannot_hold_does_not_stop_the_seed_for_the_others(
        store, root, outside, monkeypatch, capsys, shape):
    """Lens R24 R4 / PM r4: a legacy destination that resolves into a folder
    whose name is not UTF-8 made the one settings save fail, so NO legacy
    schedule was seeded and every one stopped exporting, at every pass. The
    seed is per destination: 1 bad beside 3 good -> the 3 good ones run, the
    bad one is skipped, logged, listed "destination not storable", not disabled."""
    good_dests = [os.path.join(outside, n) for n in ("plain-a", "plain b", "plain-c")]
    good = [_legacy_row(store, good_dests[0])]
    storable = runs = True
    if shape == "non_utf8_folder":
        os.mkdir(os.fsencode(outside) + b"/bad\xff")
        bad_dest, storable = os.path.join(outside, "link"), False
        os.symlink(os.fsencode(outside) + b"/bad\xff", bad_dest)
    elif shape == "control_char":
        bad_dest = os.path.join(outside, "esc\x1bname")
    elif shape == "nul":
        bad_dest, runs = os.path.join(outside, "nul\x00name"), False          # no such path can exist
    else:
        bad_dest, runs = os.path.join(outside, "n" * 300), False              # one component over NAME_MAX
    bad = _legacy_row(store, bad_dest)                   # the bad one sits in the middle of the list
    good += [_legacy_row(store, d) for d in good_dests[1:]]
    capsys.readouterr()
    for n in (1, 2, 3):
        _restart()
        out = se.run_due_exports(force=True)
        assert not set(good) & set(_skipped(out)), \
            f"one {shape} destination stopped the seed for the others (allowlist {_allowlist()!r})"
        assert [len(_names(d)) for d in good_dests] == [n, n, n]
        assert _raw(bad)["enabled"] == 1 and all(_raw(g)["enabled"] == 1 for g in good)
        assert (bad in _skipped(out)) is (not storable or shape == "nul")
        if storable and runs:
            assert out["ran"] == 4
    assert _allowlist().splitlines()[:3] == [good_dests[0], good_dests[1], good_dests[2]] if not storable else True
    status = _row(bad).get("last_status")
    if not storable:
        assert status == "destination not storable" and _row(bad)["destination_allowed"] is False
        assert capsys.readouterr().err.count(f"schedule {bad}: destination not storable, not seeded") == 1   # seed ran once
        assert len(_allowlist().splitlines()) == 3       # the three good ones, nothing for the bad one, no growth
    elif shape == "nul":
        assert status == "destination not allowed"
    else:
        assert status is None
    assert all("last_status" not in _row(g) for g in good)


def _marker():
    return se._meta_get("allowlist_seeded")


@pytest.mark.parametrize("k", [1, 3, 5])
@pytest.mark.parametrize("fault", ["once", "always"])
def test_1_a_failed_save_of_one_line_leaves_it_pending_and_every_later_pass_retries_it(
        store, root, outside, monkeypatch, fault, k):
    """Lens R37 R5 / PM r5 (a-d): the save of line k of n returns False. r4 wrote
    the schedule off as "not storable" and set the marker, so one failed write
    stopped an encodable legacy schedule for good. Now the line is PENDING:
    fault once  -> the next pass stores it and only then is the marker set;
    fault always -> no marker, the schedule still exports, status says pending."""
    from bulk_downloader import global_config as gc
    n = 5
    dests = [os.path.join(outside, f"d{i}") for i in range(1, n + 1)]
    ids = [_legacy_row(store, d) for d in dests]
    target, others = dests[k - 1], [d for d in dests if d != dests[k - 1]]
    real_set, faults = gc.set_config, []

    def set_config(update):
        if target in update.get(KEY, "").splitlines() and (fault == "always" or not faults):
            faults.append(update)
            return False
        return real_set(update)

    monkeypatch.setattr(gc, "set_config", set_config)
    _restart()
    out = se.run_due_exports(force=True)
    assert len(faults) == 1 and _marker() is None, "a pass that left a line unsaved set the seeded marker"
    assert se.pending_ids() == {ids[k - 1]} and se.not_storable_ids() == set()
    assert out["ran"] == n and _skipped(out) == [], f"the schedule with the pending line did not export: {out}"
    assert [len(_names(d)) for d in dests] == [1] * n and _allowlist().splitlines() == others
    assert all(_raw(i)["enabled"] == 1 for i in ids)
    if fault == "once":
        if k != 3:                                           # k == 3: the next pass of the SAME process
            _restart()
        out = se.run_due_exports(force=True)                 # the next pass: stored, and only now the marker
        assert _marker() is not None and se.pending_ids() == set() and out["ran"] == n and _skipped(out) == []
        assert sorted(_allowlist().splitlines()) == sorted(dests) and _allowlist().splitlines()[-1] == target
        assert all("last_status" not in _row(i) for i in ids)
        calls = []
        monkeypatch.setattr(gc, "set_config", lambda u: calls.append(u) or real_set(u))
        _restart()
        assert se.run_due_exports(force=True)["ran"] == n and calls == []      # seeded: never again
        assert [len(_names(d)) for d in dests] == [3] * n and len(_allowlist().splitlines()) == n
        return
    for passes in (2, 3):
        _restart()
        out = se.run_due_exports(force=True)
        assert _marker() is None and out["ran"] == n and _skipped(out) == []
        assert [len(_names(d)) for d in dests] == [passes] * n
        assert _allowlist().splitlines() == others, "a line that keeps failing made the allowlist grow"
        row = _row(ids[k - 1])                               # the list call is a pass too: still pending
        assert row["last_status"] == "seed pending, retry" and row["destination_allowed"] is True and row["enabled"] == 1
        assert all("last_status" not in _row(i) for i in ids if i != ids[k - 1])
    assert _add(os.path.join(target, "new")) is None         # pending admits its own legacy schedule, not a new one
    monkeypatch.setattr(gc, "set_config", real_set)          # the store heals
    _restart()
    assert se.run_due_exports(force=True)["ran"] == n
    assert _marker() is not None and se.pending_ids() == set() and sorted(_allowlist().splitlines()) == sorted(dests)
    assert "last_status" not in _row(ids[k - 1])


def test_1_a_pending_line_is_dropped_when_another_process_finished_the_seed(store, root, outside, monkeypatch):
    """Pending is this process's memory of an unfinished seed. Once the marker is there the setting alone decides:
    a folder the operator has since removed is not kept alive by a stale pending entry."""
    from bulk_downloader import global_config as gc
    dest = os.path.join(outside, "d1")
    sid = _legacy_row(store, dest)
    real_set = gc.set_config
    monkeypatch.setattr(gc, "set_config", lambda u: dest not in u.get(KEY, "") and real_set(u))
    _restart()
    assert se.run_due_exports(force=True)["ran"] == 1 and se.pending_ids() == {sid}
    monkeypatch.setattr(gc, "set_config", real_set)
    with store.db_conn() as cx:                              # what the other process wrote
        cx.execute("INSERT OR REPLACE INTO scheduled_exports_meta(key, value) VALUES ('allowlist_seeded', '1')")
    out = se.run_due_exports(force=True)
    assert se.pending_ids() == set() and _skipped(out) == [sid] and out["ran"] == 0 and len(_names(dest)) == 1
    assert _row(sid)["last_status"] == "destination not allowed" and _raw(sid)["enabled"] == 1


def test_1_the_seed_does_not_write_over_a_setting_that_is_not_text(store, root, outside):
    import json
    old_dest = os.path.join(outside, "old-export")
    legacy = _legacy_row(store, old_dest)
    stored = json.dumps({KEY: ["/srv/typed-by-hand"], "other": 1})
    with open("app_config.json", "w") as fh:
        fh.write(stored)
    for _ in range(3):
        _restart()
        out = se.run_due_exports(force=True)
        assert _skipped(out) == [legacy] and _raw(legacy)["enabled"] == 1
        assert open("app_config.json").read() == stored, "the seed replaced a setting it could not read as text"
    _restart()
    _set_allowlist("")                                   # the operator repairs it: the seed then goes through
    out = se.run_due_exports(force=True)
    assert _skipped(out) == [] and out["ran"] == 1 and _allowlist() == old_dest


@pytest.mark.parametrize("failure", ["corrupt", "chmod", "missing", "wrong_type"])
def test_1_an_unreadable_setting_after_a_good_seed_skips_runs_and_disables_nothing(
        store, root, outside, monkeypatch, capsys, failure):
    """Lens R14 R3: one start with app_config.json corrupt, unreadable or gone
    read the allowlist as empty and disabled every allowlisted schedule for good."""
    old_dest = os.path.join(outside, "old-export")
    legacy = _legacy_row(store, old_dest)
    inside = _add(os.path.join(root, "inside"))
    assert se.run_due_exports()["ran"] == 2 and _allowlist() == old_dest          # control: seeded, both export
    before = _raw(legacy)
    undo = _break_setting(failure, monkeypatch)
    capsys.readouterr()
    for _ in range(3):                                   # restart loop: three cold starts on the broken setting
        _restart()
        out = se.run_due_exports(force=True)
        assert _raw(legacy)["enabled"] == 1 and _raw(inside)["enabled"] == 1
        assert _skipped(out) == [legacy]                 # fail closed: nothing is written outside the root
        assert (out["checked"], out["ran"], out["errors"]) == (1, 1, 0)           # the in-root export still runs
    assert _raw(legacy) == before and len(_names(old_dest)) == 1
    err = capsys.readouterr().err
    assert ("setting could not be read" in err) is (failure in ("corrupt", "chmod"))
    undo()
    assert _allowlist() == old_dest                      # the setting was not re-seeded or rewritten meanwhile
    out = se.run_due_exports(force=True)
    assert _skipped(out) == [] and (out["checked"], out["ran"]) == (2, 2) and len(_names(old_dest)) == 2


def test_1_the_seed_never_runs_twice_and_never_puts_back_a_removed_folder(store, root, outside):
    old_dest = os.path.join(outside, "old-export")
    legacy = _legacy_row(store, old_dest)
    assert se.run_due_exports()["ran"] == 1 and _allowlist() == old_dest
    _set_allowlist("/srv/operator-choice")               # the operator replaces the seeded value
    for _ in range(3):
        _restart()
        se.list_schedules()
        out = se.run_due_exports(force=True)
        assert _allowlist() == "/srv/operator-choice" and _skipped(out) == [legacy]
        assert _raw(legacy)["enabled"] == 1


def test_1_the_seed_adds_to_a_setting_it_finds_and_replaces_nothing(store, root, outside):
    kept = "# nas\n/srv/operator-choice"
    _set_allowlist(kept + "\n\n")
    old_dest = os.path.join(outside, "old export")
    legacy = _legacy_row(store, old_dest)
    assert se.run_due_exports()["ran"] == 1 and _raw(legacy)["enabled"] == 1
    assert _allowlist() == kept + "\n" + old_dest


def test_1_a_relative_allowlist_entry_admits_nothing_even_where_it_would_resolve(store, root):
    _set_allowlist("nas")                                # cwd/nas, if a relative entry were honoured
    dest = os.path.join(os.getcwd(), "nas", "x")
    assert _add(dest) is None and not os.path.exists(os.path.join(os.getcwd(), "nas"))
    _set_allowlist('"nas"')                              # nor as a quoted line
    assert _add(dest) is None
    _set_allowlist(os.path.join(os.getcwd(), "nas"))
    assert _add(dest) is not None                        # control: the same folder, written absolute


@pytest.mark.parametrize("text,allowed", [
    ("{outside}", True), ("# nas\n\n  {outside}  \n", True), ("{outside}/sub", False),
    ("relative/dir", False), ("", False), ("{outside}-other", False)])
def test_1_the_allowlist_setting_admits_exactly_its_absolute_folders(store, root, outside, text, allowed):
    _set_allowlist(text.replace("{outside}", outside))
    dest = os.path.join(outside, "x")
    assert (_add(dest) is not None) is allowed
    assert os.path.isdir(dest) is allowed


def test_1_the_allowlist_is_edited_through_the_settings_api(store, root, outside):
    c, h = _client()
    dest = os.path.join(outside, "via-settings")
    body = {"format": "csv", "destination": dest, "cadence_hours": 24}
    assert c.post("/api/scheduled_exports/add", headers=h, json=body).status_code == 400      # unlisted: refused
    assert c.post("/api/global_config", headers=h, json={KEY: [outside]}).status_code == 400  # wrong type: refused
    assert _allowlist() == "<unset>"
    assert c.post("/api/global_config", headers=h, json={KEY: outside}).status_code == 200
    assert _allowlist() == outside and c.get("/api/global_config").get_json()[KEY] == outside
    assert c.post("/api/scheduled_exports/add", headers=h, json=body).status_code == 200
    assert os.path.isdir(dest)


def test_1_a_destination_that_became_a_link_out_of_the_root_is_refused_at_run_time(store, root, outside):
    dest = os.path.join(root, "moved")
    sid = _add(dest)
    os.rmdir(dest)
    os.symlink(outside, dest)
    res = se._run_one({"id": sid, "format": "csv", "destination": dest, "filter_json": "{}"})
    assert res["ok"] is False and "outside the export root" in res["error"]
    se._apply_retention({"id": sid, "format": "csv", "destination": dest, "retention_count": 1})
    assert _names(outside) == []


# ── 2: retention_count >= 1; the export just written always survives ──
@pytest.mark.parametrize("bad", [0, -1, -100, "many", True, 2.5, None])
def test_2_a_retention_count_below_one_is_refused(root, bad):
    assert _add(os.path.join(root, "r"), retention_count=bad) is None
    assert se.list_schedules() == []
    assert _add(os.path.join(root, "r"), retention_count=1) is not None        # control


@pytest.mark.parametrize("bad,status", [(0, 400), (-100, 400), ("many", 400), (3, 200)])
def test_2_api_refuses_a_retention_count_below_one(root, bad, status):
    c, h = _client()
    r = c.post("/api/scheduled_exports/add", headers=h, json={
        "format": "csv", "destination": os.path.join(root, "api-r"), "cadence_hours": 24, "retention_count": bad})
    assert r.status_code == status


@pytest.mark.parametrize("stored", [-100, 0, 1])
def test_2_retention_never_removes_the_export_just_written(store, root, stored):
    dest = os.path.join(root, "kept")
    os.makedirs(dest)
    sid = _legacy_row(store, dest, retention=stored)
    for _ in range(3):
        out = se.run_due_exports(force=True)
        written = out["results"][0]["file"]
        assert out["ran"] == 1 and os.path.isfile(written), f"retention_count={stored} removed {written}"
    # a stored 0 keeps meaning the default 10; -100 and 1 rotate older runs down to one
    assert len(_names(dest)) == (3 if stored == 0 else 1) and os.path.basename(written) in _names(dest)
    assert _row(sid)["last_run_ok"] == 1


def test_2_the_export_just_written_survives_an_older_export_with_a_later_mtime(store, root):
    dest = os.path.join(root, "clock")
    sid = _add(dest, retention_count=1)
    stale = os.path.join(dest, f"bd-csv-{sid}-20200101-000000-{'0' * 32}.csv")
    with open(stale, "w") as fh:
        fh.write("old")
    future = time.time() + 86400
    os.utime(stale, (future, future))                    # a clock step, or a restored backup
    out = se.run_due_exports(force=True)
    assert _names(dest) == [os.path.basename(out["results"][0]["file"])]


# ── 3: retention removes regular files with this schedule's exact name ─
def test_3_retention_touches_only_this_schedules_own_regular_files(store, root, outside):
    dest = os.path.join(root, "shared")
    sid = _add(dest, retention_count=2)
    own = [f"bd-csv-{sid}-2020010{d}-000000-{'0' * 31}{d}.csv" for d in (1, 2, 3)] + [f"bd-csv-{sid}-20191231-235959.csv"]
    for i, name in enumerate(own):
        with open(os.path.join(dest, name), "w") as fh:
            fh.write("old")
        os.utime(os.path.join(dest, name), (1_000_000 + i, 1_000_000 + i))
    target = os.path.join(outside, "target.csv")
    with open(target, "w") as fh:
        fh.write("outside")
    bystanders = {
        f"bd-csv-{sid}-20200104-000000-{'a' * 32}.csv": "dir",             # own name, but a directory
        f"bd-csv-{sid}-20200105-000000-{'b' * 32}.csv": "link",            # own name, but a symlink
        f"bd-csv-{sid}-dirlink": "dirlink",
        f"bd-csv-{sid}0-20200101-000000-{'c' * 32}.csv": "file",           # schedule <sid>0, not <sid>
        f"bd-json-{sid}-20200101-000000-{'d' * 32}.json": "file",          # another format
        f"bd-csv-{sid}-notes.csv": "file",                                 # own prefix, not an export name
        f"bd-csv-{sid}-20200101-000000-{'e' * 32}.csv.bak": "file",
        "keep.txt": "file",
    }
    for name, kind in bystanders.items():
        path = os.path.join(dest, name)
        if kind == "dir":
            os.makedirs(path)
            with open(os.path.join(path, "inside.txt"), "w") as fh:
                fh.write("x")
        elif kind == "link":
            os.symlink(target, path)
        elif kind == "dirlink":
            os.symlink(outside, path)
        else:
            with open(path, "w") as fh:
                fh.write("mine, not the scheduler's")
    out = se.run_due_exports(force=True)
    written = os.path.basename(out["results"][0]["file"])
    left = _names(dest)
    assert sorted(set(left) - set(bystanders)) == sorted([written, own[3]])     # 2 kept: the new one + newest old
    assert set(bystanders) <= set(left)                                         # every bystander is still there
    assert os.path.isfile(os.path.join(dest, list(bystanders)[0], "inside.txt"))
    assert os.path.islink(os.path.join(dest, list(bystanders)[1])) and open(target).read() == "outside"
    assert _names(outside) == ["target.csv"]


def test_3_an_eol_export_directory_is_never_removed_by_retention(store, root):
    dest = os.path.join(root, "eol")
    os.makedirs(dest)
    sched = {"id": 7, "format": "eol", "destination": dest, "retention_count": 1}
    for d in (1, 2, 3):
        os.makedirs(os.path.join(dest, f"bd-eol-7-2020010{d}-000000-{'0' * 32}", "tables"))
    se._apply_retention(sched)
    assert len(_names(dest)) == 3


def _own_files(sid, folder, n=3):
    os.makedirs(folder, exist_ok=True)
    for d in range(1, n + 1):
        with open(os.path.join(folder, f"bd-csv-{sid}-2020010{d}-000000-{'0' * 32}.csv"), "w") as fh:
            fh.write("not the scheduler's to remove")
    return _names(folder)


def test_3_retention_rejudges_a_destination_whose_parent_became_a_link_out_of_the_root(store, root, outside):
    dest = os.path.join(root, "parent", "sub")
    sid = _add(dest, retention_count=1)
    assert len(_own_files(sid, dest)) == 3
    se._apply_retention({"id": sid, "format": "csv", "destination": dest, "retention_count": 1})
    assert len(_names(dest)) == 1                        # control: in the root, it rotates
    for name in _names(dest):
        os.remove(os.path.join(dest, name))
    os.rmdir(dest)
    os.rmdir(os.path.join(root, "parent"))
    os.symlink(outside, os.path.join(root, "parent"))    # root/parent -> outside; dest's last part is a real dir
    victims = _own_files(sid, os.path.join(outside, "sub"))
    se._apply_retention({"id": sid, "format": "csv", "destination": dest, "retention_count": 1})
    assert _names(os.path.join(outside, "sub")) == victims


def test_3_retention_does_not_follow_a_destination_swapped_for_a_link_after_it_was_judged(
        store, root, outside, monkeypatch):
    dest = os.path.join(root, "swapped")
    sid = _add(dest, retention_count=1)
    victims = _own_files(sid, outside)
    os.rmdir(dest)
    os.symlink(outside, dest)
    monkeypatch.setattr(se, "resolve_destination", lambda d: dest)   # the judge's answer from before the swap
    se._apply_retention({"id": sid, "format": "csv", "destination": dest, "retention_count": 1})
    assert _names(outside) == victims
    monkeypatch.undo()
    os.remove(dest)
    _own_files(sid, dest)
    monkeypatch.setattr(se, "resolve_destination", lambda d: dest)
    se._apply_retention({"id": sid, "format": "csv", "destination": dest, "retention_count": 1})
    assert len(_names(dest)) == 1                        # control: the same stub on a real directory rotates
