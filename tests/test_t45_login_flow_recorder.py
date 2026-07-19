"""T45 (Tier 4) — D-27 login-flow recorder.

Thin registry layer over macro_recorder. Tests focus on:
  • Round-trip save → list → get → delete
  • Tag invariant (only login_flow-tagged macros visible)
  • Tag-protection on delete (refuses to delete unrelated macros)
"""
import contextlib
import json
import tempfile
import os
import sys
from pathlib import Path

from bulk_downloader import dev_suite as ds
from bulk_downloader import login_flow_recorder as lfr


@contextlib.contextmanager
def _isolated_install_dir():
    """macro_recorder uses INSTALL_DIR for storage; INSTALL_DIR is
    captured at module import time, so we must re-import to redirect.
    Mirrors test_i18n_phase191.py."""
    tmp = tempfile.mkdtemp(prefix="bd-lfr-test-")
    saved_env = os.environ.get("BD_INSTALL_DIR")
    os.environ["BD_INSTALL_DIR"] = tmp
    # Drop and re-import any bulk_downloader.* modules that snapshot
    # INSTALL_DIR
    saved_mods = {}
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            saved_mods[m] = sys.modules.pop(m)
    try:
        # Re-import the modules we care about under the new
        # INSTALL_DIR
        from bulk_downloader import macro_recorder as _mr  # noqa
        from bulk_downloader import login_flow_recorder as _lfr  # noqa
        from bulk_downloader import dev_suite as _ds  # noqa
        yield Path(tmp)
    finally:
        # Restore module state
        for m in list(sys.modules):
            if m.startswith("bulk_downloader"):
                del sys.modules[m]
        sys.modules.update(saved_mods)
        if saved_env is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = saved_env


# Sample action sequences
_LOGIN_ACTIONS = [
    {"kind": "click", "selector": "#login-button"},
    {"kind": "type", "selector": "#username", "text": "alice"},
    {"kind": "type", "selector": "#password", "text": "secret"},
    {"kind": "click", "selector": "#submit"},
]


# ── Round trip ──────────────────────────────────────────────────────

def test_record_then_get(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        r = lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        assert r["ok"] is True
        bundle = lfr2.get_login_flow("siteA", "main")
        assert bundle is not None
        assert bundle["site_id"] == "siteA"
        assert bundle["name"] == "main"
        assert len(bundle["actions"]) == 4


def test_record_adds_login_flow_tag(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        bundle = lfr2.get_login_flow("siteA", "main")
        tags = bundle["metadata"]["tags"]
        assert "login_flow" in tags


def test_record_with_description(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS,
                                description="primary login")
        bundle = lfr2.get_login_flow("siteA", "main")
        assert bundle["metadata"]["description"] == "primary login"


def test_record_invalid_actions_rejected(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        r = lfr2.record_login_flow("siteA", "main",
                                    [{"kind": "bogus"}])
        assert r["ok"] is False


def test_record_invalid_name_rejected(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        # `!` is not in [a-z0-9_-] — macro_recorder._normalize_name
        # will reject it
        r = lfr2.record_login_flow("siteA", "bad!name",
                                    _LOGIN_ACTIONS)
        assert r["ok"] is False


# ── List ────────────────────────────────────────────────────────────

def test_list_empty(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        assert lfr2.list_login_flows() == []


def test_list_after_records(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        lfr2.record_login_flow("siteA", "alt", _LOGIN_ACTIONS)
        lfr2.record_login_flow("siteB", "main", _LOGIN_ACTIONS)
        all_flows = lfr2.list_login_flows()
        assert len(all_flows) == 3


def test_list_filtered_by_site(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        lfr2.record_login_flow("siteB", "main", _LOGIN_ACTIONS)
        only_a = lfr2.list_login_flows(site_id="siteA")
        assert len(only_a) == 1
        assert only_a[0]["site_id"] == "siteA"


def test_list_only_includes_login_flow_tagged_macros(clean_workdir):
    """A macro recorded directly via macro_recorder (without the
    login_flow tag) must NOT appear in list_login_flows."""
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import macro_recorder as mr2
        # Save one login flow
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        # Save a non-login macro
        mr2.record_macro("siteA", "other", _LOGIN_ACTIONS,
                         tags=["scroll"])
        flows = lfr2.list_login_flows()
        names = [f["name"] for f in flows]
        assert "main" in names
        assert "other" not in names


# ── Get tag enforcement ─────────────────────────────────────────────

def test_get_refuses_non_login_flow(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import macro_recorder as mr2
        mr2.record_macro("siteA", "scroll-macro", _LOGIN_ACTIONS,
                         tags=["scroll"])
        # macro_recorder.get_macro returns it; login_flow_recorder
        # MUST NOT
        assert mr2.get_macro("siteA", "scroll-macro") is not None
        assert lfr2.get_login_flow("siteA", "scroll-macro") is None


def test_get_unknown_returns_none(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        assert lfr2.get_login_flow("siteA", "never-saved") is None


# ── Delete tag protection ──────────────────────────────────────────

def test_delete_login_flow(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        r = lfr2.delete_login_flow("siteA", "main")
        assert r["ok"] is True
        assert r["removed"] is True
        assert lfr2.get_login_flow("siteA", "main") is None


def test_delete_unknown_idempotent(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        r = lfr2.delete_login_flow("siteA", "never-existed")
        assert r["ok"] is True
        assert r["removed"] is False


def test_delete_refuses_non_login_flow(clean_workdir):
    """Tag protection: deleting via the login-flow surface MUST refuse
    to remove a macro that isn't actually a login flow."""
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import macro_recorder as mr2
        mr2.record_macro("siteA", "scroll-macro", _LOGIN_ACTIONS,
                         tags=["scroll"])
        r = lfr2.delete_login_flow("siteA", "scroll-macro")
        assert r["ok"] is False
        assert "not a login flow" in r["error"]
        # Macro is still present
        assert mr2.get_macro("siteA", "scroll-macro") is not None


# ── mark_replay_result ──────────────────────────────────────────────

def test_mark_replay_result_for_login_flow(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        ok = lfr2.mark_replay_result("siteA", "main", True,
                                       message="all green")
        assert ok is True
        bundle = lfr2.get_login_flow("siteA", "main")
        assert bundle["metadata"]["last_replay_ok"] is True
        assert bundle["metadata"]["last_replay_message"] == "all green"


def test_mark_replay_result_unknown_returns_false(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        ok = lfr2.mark_replay_result("siteA", "missing", True)
        assert ok is False


# ── dev_suite inspector ────────────────────────────────────────────

def test_login_flows_status_empty(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import dev_suite as ds2
        r = ds2.login_flows_status()
        assert r["ok"] is True
        assert r["total"] == 0
        assert "0 login flow" in r["verdict"]


def test_login_flows_status_with_flows(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import dev_suite as ds2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        lfr2.record_login_flow("siteB", "main", _LOGIN_ACTIONS)
        r = ds2.login_flows_status()
        assert r["total"] == 2
        assert "siteA=1" in r["verdict"]
        assert "siteB=1" in r["verdict"]


def test_login_flows_status_site_filter(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import dev_suite as ds2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        lfr2.record_login_flow("siteB", "main", _LOGIN_ACTIONS)
        r = ds2.login_flows_status(site_id="siteA")
        assert r["total"] == 1


def test_login_flow_tag_accessor(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        assert lfr2.login_flow_tag() == "login_flow"


def test_login_flows_status_is_json_serializable(clean_workdir):
    with _isolated_install_dir():
        from bulk_downloader import login_flow_recorder as lfr2
        from bulk_downloader import dev_suite as ds2
        lfr2.record_login_flow("siteA", "main", _LOGIN_ACTIONS)
        r = ds2.login_flows_status()
        json.dumps(r, default=str)
