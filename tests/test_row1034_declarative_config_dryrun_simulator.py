"""row1034 -- declarative configuration dry-run simulator & plan visualizer.

PREMISE (measured at base bc1544b75, probe in DONE.md): the `.env` editor already has a
VALIDATE gate (app_envfile_editor.validate_envfile_updates -> accepted/rejected/warnings)
but nothing that answers "what would this submission CHANGE?". The repo has plans for
backup, rebalance, dedup and routes (api_rebalance_plan, dedup_preview.plan) -- none for
configuration. This suite pins the missing half RED-first:

  * bulk_downloader.config_dryrun.plan_envfile_updates(updates, saved=..., effective=...)
    -- a PURE function: per key an action (create / update / noop / unset), the from/to
    pair, whether the key is rejected by the validate gate, and whether a restart is
    needed. It performs NO write and does not touch os.environ or the filesystem.
  * render_plan_text(plan) -- the visualizer: one `+ / ~ / - / =` line per key, a
    terraform-style summary line, and NO value for a key the validator rejected.
  * POST /api/settings/envfile/plan -- the same plan over HTTP, writing nothing.

Sandbox-valid: stdlib + Flask; does not boot app.py. Zero-arg test functions.
"""
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from flask import Flask  # noqa: E402
from bulk_downloader import app_envfile_editor as EE  # noqa: E402
from bulk_downloader import _envfile as EF  # noqa: E402

# This suite judges one module pair (config_dryrun + the editor blueprint), not the tree.
BD_GATE_SCOPE = "module"

BD_GATE_SCOPE = "module"


def test_row1034_positive_control_envfile_editor_baseline():
    """Positive control (Rule 7): proves test runner can say YES on baseline envfile editor capabilities."""
    assert hasattr(EE, "validate_envfile_updates")
    assert callable(EE.validate_envfile_updates)
    res = EE.validate_envfile_updates({})
    assert isinstance(res, dict)
    assert "accepted" in res and "rejected" in res


def _app():
    app = Flask(__name__)
    EE.register_routes(app)
    return app


def _dryrun():
    """The capability under test. Reported as a MISSING CAPABILITY, not an ImportError."""
    assert importlib.util.find_spec("bulk_downloader.config_dryrun") is not None, (
        "no configuration dry-run simulator: bulk_downloader/config_dryrun.py does not "
        "exist; the .env editor can validate a submission but cannot say what it would "
        "change (row1034)")
    return importlib.import_module("bulk_downloader.config_dryrun")


def _a_writable_key():
    """A real editor key, re-derived from _envfile (never a hard-coded name)."""
    for meta in EF.EDITOR_KEYS:
        if meta["kind"] == "port":
            return meta["name"]
    return EF.EDITOR_KEYS[0]["name"]


def _by_name(plan, name):
    return [a for a in plan["actions"] if a["name"] == name][0]


def test_row1034_a_new_key_plans_as_create_and_an_existing_one_as_update():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "8099"}, saved={}, effective={})
    assert _by_name(plan, key)["action"] == "create", plan
    assert _by_name(plan, key)["from"] is None and _by_name(plan, key)["to"] == "8099"

    plan2 = M.plan_envfile_updates({key: "8099"}, saved={key: "8080"}, effective={})
    assert _by_name(plan2, key)["action"] == "update", plan2
    assert _by_name(plan2, key)["from"] == "8080"


def test_row1034_an_unchanged_value_is_a_noop_and_needs_no_restart():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "8080"}, saved={key: "8080"}, effective={key: "8080"})
    action = _by_name(plan, key)
    assert action["action"] == "noop", plan
    assert action["restart_required"] is False, action
    assert plan["restart_required"] is False, plan
    assert plan["counts"]["noop"] == 1 and plan["counts"]["update"] == 0, plan


def test_row1034_a_value_the_validator_rejects_is_planned_as_rejected_not_applied():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "not-a-port"}, saved={}, effective={})
    action = _by_name(plan, key)
    assert action["rejected"] is True and action["reason"], action
    assert action["action"] == "rejected", action
    assert plan["ok"] is False and plan["counts"]["rejected"] == 1, plan


def test_row1034_an_unknown_key_is_rejected_by_the_plan_too():
    M = _dryrun()
    plan = M.plan_envfile_updates({"BD_NOT_A_REAL_EDITOR_KEY": "1"}, saved={}, effective={})
    action = _by_name(plan, "BD_NOT_A_REAL_EDITOR_KEY")
    assert action["rejected"] is True, plan
    assert plan["ok"] is False, plan


def test_row1034_the_plan_is_pure_it_writes_nothing_and_leaves_the_environ_alone():
    M = _dryrun()
    key = _a_writable_key()
    before = dict(os.environ)
    envfile = EF.resolve_envfile_path()
    existed = envfile.exists()
    stamp = envfile.stat().st_mtime_ns if existed else None
    M.plan_envfile_updates({key: "8099"}, saved={key: "8080"}, effective={key: "8080"})
    assert dict(os.environ) == before, "the planner mutated os.environ"
    assert envfile.exists() == existed, "the planner created or removed the .env"
    if existed:
        assert envfile.stat().st_mtime_ns == stamp, "the planner wrote to the .env"


def test_row1034_render_plan_text_marks_each_action_and_hides_a_rejected_value():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "not-a-port"}, saved={}, effective={})
    text = M.render_plan_text(plan)
    assert key in text
    assert "not-a-port" not in text, "a rejected value must not be shown as if it applied"
    assert "1 to reject" in text or "reject" in text.lower(), text

    ok = M.plan_envfile_updates({key: "8099"}, saved={key: "8080"}, effective={key: "8080"})
    ok_text = M.render_plan_text(ok)
    assert ok_text.splitlines()[0].startswith("Configuration plan"), ok_text
    assert "~ %s: 8080 -> 8099" % key in ok_text, ok_text
    assert "restart" in ok_text.lower(), ok_text


def test_row1034_the_plan_endpoint_returns_the_plan_and_writes_nothing():
    key = _a_writable_key()
    client = _app().test_client()
    envfile = EF.resolve_envfile_path()
    existed = envfile.exists()
    stamp = envfile.stat().st_mtime_ns if existed else None
    r = client.post("/api/settings/envfile/plan", json={"updates": {key: "8099"}})
    assert r.status_code == 200, (
        "POST /api/settings/envfile/plan is not served (%s): the editor can validate a "
        "submission but cannot show its plan (row1034)" % r.status_code)
    body = json.loads(r.data)
    assert body["actions"] and body["actions"][0]["name"] == key, body
    assert body["written"] is False and "restart_required" in body, body
    assert envfile.exists() == existed and (
        not existed or envfile.stat().st_mtime_ns == stamp), "the plan endpoint wrote"


def test_row1034_the_plan_endpoint_adds_exactly_one_route_to_the_editor_blueprint():
    app = _app()
    rules = {str(r.rule) for r in app.url_map.iter_rules()}
    assert "/api/settings/envfile/plan" in rules, sorted(rules)
    assert "/api/settings/envfile" in rules, "the existing editor routes must survive"


def test_row1034_none_submission_accepted_key_plans_as_update_to_empty_matching_apply(tmp_path, monkeypatch):
    """Verify None submission on an accepted key plans as update to "" matching validate + write."""
    M = _dryrun()
    key = "BD_KB_DIR"
    saved = {key: "/prior/kb"}
    submission = {key: None}

    # 1. Real validator outcome
    validation = EE.validate_envfile_updates(submission)
    assert key in validation["accepted"]
    assert validation["accepted"][key] == ""

    # 2. Plan outcome
    plan = M.plan_envfile_updates(submission, saved=saved, effective=saved)
    action = _by_name(plan, key)
    assert action["action"] == "update"
    assert action["from"] == "/prior/kb"
    assert action["to"] == ""
    assert action["rejected"] is False
    assert plan["ok"] is True
    assert plan["counts"]["update"] == 1
    assert plan["counts"]["unset"] == 0

    # 3. Text rendering matches apply expectation
    text = M.render_plan_text(plan)
    assert f"~ {key}: /prior/kb -> " in text
    assert "(unset)" not in text

    # 4. Verify actual _write_envfile behavior on a real temp file
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}=/prior/kb\n", encoding="utf-8")
    monkeypatch.setattr(EF, "resolve_envfile_path", lambda: env_file)
    client = _app().test_client()
    r = client.post("/api/settings/envfile", json={"updates": submission})
    assert r.status_code == 200
    assert f"{key}=" in env_file.read_text(encoding="utf-8")


def test_row1034_none_submission_foundation_key_plans_as_rejected_matching_apply(tmp_path, monkeypatch):
    """Verify None submission on a foundation key plans as rejected matching validate + write refusal."""
    M = _dryrun()
    key = "BD_REPO"
    saved = {key: "/home/mboyle/BulkDownloader"}
    submission = {key: None}

    # 1. Real validator outcome
    validation = EE.validate_envfile_updates(submission)
    assert key in validation["rejected"]
    expected_reason = validation["rejected"][key]

    # 2. Plan outcome
    plan = M.plan_envfile_updates(submission, saved=saved, effective=saved)
    action = _by_name(plan, key)
    assert action["action"] == "rejected"
    assert action["rejected"] is True
    assert action["reason"] == expected_reason
    assert plan["ok"] is False
    assert plan["counts"]["rejected"] == 1

    # 3. Text rendering hides value and states rejection reason
    text = M.render_plan_text(plan)
    assert "REJECTED" in text
    assert expected_reason in text

    # 4. Verify actual endpoint rejects HTTP 400 with same reason
    env_file = tmp_path / ".env"
    env_file.write_text(f"{key}={saved[key]}\n", encoding="utf-8")
    monkeypatch.setattr(EF, "resolve_envfile_path", lambda: env_file)
    client = _app().test_client()
    r = client.post("/api/settings/envfile", json={"updates": submission})
    assert r.status_code == 400
    data = json.loads(r.data)
    assert key in data["rejected"]
    assert data["rejected"][key] == expected_reason


def _plan_client(tmp_path, monkeypatch, envfile_text=""):
    env_file = tmp_path / ".env"
    env_file.write_text(envfile_text, encoding="utf-8")
    monkeypatch.setattr(EF, "resolve_envfile_path", lambda: env_file)
    return _app().test_client()


def test_row1034_plan_never_discloses_a_value_for_a_name_outside_the_allow_list(tmp_path, monkeypatch):
    """E1 (HIGH): the plan echoed os.environ[name] / the .env value for any submitted
    name, so the settings API became a read-any-env-var oracle. RULING-0027: the plan
    never returns an environment value at all, only whether the key is set."""
    _dryrun()
    env_secret, file_secret = "s3cret-env-value-1034", "s3cret-dotenv-value-1034"
    monkeypatch.setenv("BD_PROBE_SECRET_1034", env_secret)
    client = _plan_client(tmp_path, monkeypatch, f"OTHER_TOKEN_1034={file_secret}\n")
    r = client.post("/api/settings/envfile/plan", json={"updates": {
        "BD_PROBE_SECRET_1034": "x", "OTHER_TOKEN_1034": "y", "PATH": "z"}})
    text = r.get_data(as_text=True)
    for leaked in (env_secret, file_secret, os.environ["PATH"]):
        assert leaked not in text, "plan endpoint disclosed a value it must not read: %r" % leaked
    for a in json.loads(text)["actions"]:
        assert a["action"] == "rejected", a
        assert a["from"] is None and a["to"] is None and a["effective_set"] is None, a
        assert "effective" not in a, a


def test_row1034_a_rejected_editor_key_shows_no_saved_or_effective_value():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "not-a-port"}, saved={key: "8080"}, effective={key: "8081"})
    action = _by_name(plan, key)
    assert action["action"] == "rejected"
    assert (action["from"], action["to"], action["effective_set"]) == (None, None, None), action


def test_row1034_a_noop_on_a_drifted_effective_value_still_needs_a_restart():
    """E2: restart is decided against os.environ (effective), not the saved .env."""
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "8080"}, saved={key: "8080"}, effective={key: "9090"})
    action = _by_name(plan, key)
    assert action["action"] == "noop"
    assert action["restart_required"] is True, action
    assert plan["restart_required"] is True, plan


def test_row1034_an_update_already_effective_needs_no_restart():
    M = _dryrun()
    key = _a_writable_key()
    plan = M.plan_envfile_updates({key: "8099"}, saved={key: "8080"}, effective={key: "8099"})
    action = _by_name(plan, key)
    assert action["action"] == "update" and action["from"] == "8080"
    assert action["restart_required"] is False, action
    assert plan["restart_required"] is False, plan


def test_row1034_the_plan_endpoint_decides_restart_from_the_process_environment(tmp_path, monkeypatch):
    _dryrun()
    key = _a_writable_key()
    client = _plan_client(tmp_path, monkeypatch, f"{key}=8080\n")
    monkeypatch.setenv(key, "8099")
    body = json.loads(client.post("/api/settings/envfile/plan", json={"updates": {key: "8099"}}).data)
    assert _by_name(body, key)["action"] == "update" and body["restart_required"] is False, body
    assert _by_name(body, key)["effective_set"] is True, body
    assert "8099" not in json.dumps([a.get("effective") for a in body["actions"]]), body
    monkeypatch.setenv(key, "8080")
    body = json.loads(client.post("/api/settings/envfile/plan", json={"updates": {key: "8099"}}).data)
    assert body["restart_required"] is True, body


def test_row1034_a_non_object_json_body_is_a_400_not_a_500(tmp_path, monkeypatch):
    """E3: a JSON list body crashed body.get() with a 500."""
    _dryrun()
    client = _plan_client(tmp_path, monkeypatch)
    for url in ("/api/settings/envfile/plan", "/api/settings/envfile"):
        for payload in ([1, 2], "just-a-string", 7):
            r = client.post(url, json=payload)
            assert r.status_code == 400, (url, payload, r.status_code)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == ""


def test_row1034_an_accepted_key_reports_whether_it_is_set_never_its_environment_value(tmp_path, monkeypatch):
    """RULING-0027: even for an allow-listed key the plan says set / not set, not the value."""
    _dryrun()
    key = _a_writable_key()
    client = _plan_client(tmp_path, monkeypatch)
    monkeypatch.setenv(key, "54321")
    r = client.post("/api/settings/envfile/plan", json={"updates": {key: "8099"}})
    assert "54321" not in r.get_data(as_text=True), "plan disclosed the process environment value"
    action = _by_name(json.loads(r.data), key)
    assert action["effective_set"] is True and action["restart_required"] is True, action
    monkeypatch.delenv(key)
    action = _by_name(json.loads(client.post("/api/settings/envfile/plan", json={"updates": {key: "8099"}}).data), key)
    assert action["effective_set"] is False, action
