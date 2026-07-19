"""v3.66.716 -- remove the decoys and the shadow. Two operator decisions, executed.

DECISION 1 -- the four non-dotted autonomy keys are DECOYS, delete them.

    auto_promote / auto_quarantine / auto_refresh / auto_repair

They are DECLARED in GLOBAL_CONFIG_SCHEMA with safety=True, so a POST is accepted and
PERSISTS. Nothing reads them. `lifecycle_automation.is_enabled(name)` maps the short
toggle NAME through AUTOMATION_TOGGLES to the DOTTED key and `_read_toggle` reads only
that. So an operator who sets `auto_refresh: true` -- the obvious name, declared,
safety-flagged, 200 OK, persisted -- gets NOTHING. The feature stays off.

That is precisely the failure the comment DIRECTLY ABOVE the schema warns about:

    "get()/get_config() do a FLAT lookup. A nested block in app_config.json (e.g.
     {"automation": {"auto_refresh": true}}) means code reading get("auto_refresh")
     silently gets the default -- the feature is silently OFF (the 266 footgun)."

The schema was carrying the footgun it was written to prevent. Deleted: thanks to the
709 write contract, a POST to them now 400s LOUDLY instead of lying with a 200.

DECISION 2 -- /api/sched_exports is a SHADOW, remove it.

app_sched_exports.py registers 4 routes that nothing calls. app_scheduled_exports.py is
the family the SPA actually uses (useGovernance.ts). The bare string "sched_exports" in
that hook is a react-query CACHE KEY, not a URL -- which is what fooled both my prose and
my first matcher into calling the shadow "live".

bg_scheduler is NOT affected: it imports the LIBRARY (`scheduled_exports`), never the
blueprint.
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DECOYS = ("auto_promote", "auto_quarantine", "auto_refresh", "auto_repair")


def _client():
    from bulk_downloader.app import app

    return app.test_client()


def _csrf(c):
    j = c.get("/api/csrf").get_json() or {}
    t = j.get("csrf_token") or j.get("token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


@pytest.mark.parametrize("key", DECOYS)
def test_decoy_key_is_gone_from_the_schema(key):
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA

    assert key not in GLOBAL_CONFIG_SCHEMA, (
        "%s is declared and writable but NOTHING reads it -- is_enabled() reads the "
        "DOTTED key. A declared, safety-flagged key that silently does nothing is worse "
        "than no key." % key)


@pytest.mark.parametrize("key", DECOYS)
def test_writing_a_decoy_now_fails_loudly(key):
    """709's contract turns the silent lie into a 400. Setting a key that does nothing
    must not return 200."""
    c = _client()
    r = c.post("/api/global_config", data=json.dumps({key: True}), headers=_csrf(c))
    assert r.status_code == 400, (
        "POST %s returned %s -- it must not be accepted, it does nothing"
        % (key, r.status_code))


def test_the_real_dotted_toggles_still_work():
    """Guard against over-deletion: the keys the runtime ACTUALLY reads must survive."""
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA
    from bulk_downloader import lifecycle_automation as la

    for name, key in la.AUTOMATION_TOGGLES.items():
        assert key in GLOBAL_CONFIG_SCHEMA, "%s (%s) lost its declaration" % (name, key)
    c = _client()
    r = c.post("/api/global_config",
               data=json.dumps({"automation.auto_refresh_enabled": True}),
               headers=_csrf(c))
    assert r.status_code == 200
    got = (c.get("/api/global_config").get_json() or {})
    assert got.get("automation.auto_refresh_enabled") is True


def test_shadow_export_routes_are_gone():
    from bulk_downloader.app import app

    rules = {str(r.rule) for r in app.url_map.iter_rules()}
    shadow = sorted(r for r in rules if r.startswith("/api/sched_exports"))
    assert not shadow, "the shadowed export family is still registered: %s" % shadow


def test_the_real_export_family_survives():
    from bulk_downloader.app import app

    rules = {str(r.rule) for r in app.url_map.iter_rules()}
    for r in ("/api/scheduled_exports/list", "/api/scheduled_exports/add",
              "/api/scheduled_exports/run_now"):
        assert r in rules, "%s went missing -- the SPA calls this one" % r


def test_scheduler_still_drives_exports():
    """bg_scheduler imports the LIBRARY, not the blueprint. Removing the shadow
    blueprint must not touch the background run_due job."""
    src = open(os.path.join(ROOT, "bulk_downloader", "bg_scheduler.py"),
               encoding="utf-8").read()
    assert "scheduled_exports" in src and "run_due_exports" in src
    assert "app_sched_exports" not in src


def test_parity_debt_fell_by_the_four_decoys():
    from tools import config_surface_inventory as csi

    base = json.load(open(os.path.join(ROOT, "reports", "config_parity_baseline.json"),
                          encoding="utf-8"))
    d = csi.build(ROOT)
    assert d["counts"]["open_runtime_tunable"] == base["open_count"], "re-pin the baseline"
    assert not [k for k in base.get("open", []) if k in DECOYS], (
        "the decoys are still counted as open parity debt")
    assert base["open_count"] <= 7, (
        "open debt is %d; deleting the 4 decoys should take 11 -> 7"
        % base["open_count"])
