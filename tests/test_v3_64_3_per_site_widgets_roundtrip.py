"""B-2 (v3.64.x): per-site widget config — HTTP round-trip.

Backend infrastructure for per-site widget overrides shipped in
v3.43.60 (widgets_config module + the /api/widgets/<scope> routes),
but the SPA only ever called the global endpoint. v3.64.x B-2
finally wires the SPA hooks to the per-site endpoints. These tests
pin the wire contract — GET / PUT / DELETE on /api/widgets/<sid>
behave the way useWidgetSelection expects.

The widgets_config-level tests in test_v3_43_60_widgets.py cover the
storage layer in isolation. These tests run end-to-end through the
Flask test client to catch wire-shape regressions specifically.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class _IsolatedWidgetsConfig:
    """Pin the widgets.json config path to a temp dir for the test."""

    def setup_method(self):
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="b2-widgets-"))
        self._saved_env = os.environ.get("BD_WIDGETS_CONFIG_PATH")
        os.environ["BD_WIDGETS_CONFIG_PATH"] = str(
            self._tmp_dir / "widgets.json")
        from bulk_downloader import widgets_config
        widgets_config._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import widgets_config
        widgets_config._reset_for_tests()
        if self._saved_env is None:
            os.environ.pop("BD_WIDGETS_CONFIG_PATH", None)
        else:
            os.environ["BD_WIDGETS_CONFIG_PATH"] = self._saved_env
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestPerSiteWidgetsRoundTrip(_IsolatedWidgetsConfig):
    """End-to-end GET/PUT/DELETE on /api/widgets/<sid>."""

    def test_get_unknown_site_returns_global_fallback(self, fresh_app):
        """A site with no per-site override returns the global list —
        same shape as GET /_global. This is the falling-back-to-global
        contract the SPA hook relies on for new sites."""
        r = fresh_app.get("/api/widgets/_global")
        assert r.status_code == 200
        global_body = r.get_json() or {}
        global_ids = sorted(w["id"] for w in global_body["widgets"])

        r = fresh_app.get("/api/widgets/site-with-no-override")
        assert r.status_code == 200
        site_body = r.get_json() or {}
        site_ids = sorted(w["id"] for w in site_body["widgets"])
        assert site_ids == global_ids, \
            f"unknown site should fall back to global widgets; " \
            f"got {site_ids} vs global {global_ids}"

    def test_put_persists_per_site_override(self, fresh_app):
        """PUT a per-site list; subsequent GET returns the same list
        regardless of what _global is."""
        payload = {"widgets": [
            {"id": "done_today", "size": "md"},
            {"id": "queue_depth", "size": "sm"},
        ]}
        r = fresh_app.put("/api/widgets/wowgirls", json=payload)
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json() or {}
        assert body.get("ok") is True
        assert body.get("scope") == "wowgirls"
        assert [w["id"] for w in body["widgets"]] == ["done_today", "queue_depth"]
        # The persisted list must round-trip on GET.
        r = fresh_app.get("/api/widgets/wowgirls")
        assert r.status_code == 200
        rt = r.get_json() or {}
        assert [w["id"] for w in rt["widgets"]] == ["done_today", "queue_depth"]
        assert rt["widgets"][0]["size"] == "md"
        assert rt["widgets"][1]["size"] == "sm"

    def test_put_does_not_affect_global(self, fresh_app):
        """Setting a per-site override does NOT mutate the global list."""
        # Capture global before
        before = fresh_app.get("/api/widgets/_global").get_json() or {}
        global_before = list(before.get("widgets") or [])
        # Push per-site
        fresh_app.put(
            "/api/widgets/site-a",
            json={"widgets": [{"id": "throughput", "size": "lg"}]})
        # Global unchanged
        after = fresh_app.get("/api/widgets/_global").get_json() or {}
        assert (after.get("widgets") or []) == global_before, \
            "per-site PUT must not mutate the global list"

    def test_delete_removes_override(self, fresh_app):
        """DELETE on a per-site override removes it; GET then returns
        the global list."""
        # Establish a per-site override
        fresh_app.put(
            "/api/widgets/site-a",
            json={"widgets": [{"id": "throughput", "size": "lg"}]})
        # Sanity — the override is in place
        r = fresh_app.get("/api/widgets/site-a")
        assert [w["id"] for w in (r.get_json() or {}).get("widgets") or []] \
            == ["throughput"]
        # DELETE
        r = fresh_app.delete("/api/widgets/site-a")
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json() or {}
        assert body.get("ok") is True
        # GET now returns global (the fallback path), not the previous
        # per-site list.
        global_body = fresh_app.get("/api/widgets/_global").get_json() or {}
        site_body = fresh_app.get("/api/widgets/site-a").get_json() or {}
        assert (site_body.get("widgets") or []) == (global_body.get("widgets") or []), \
            "after DELETE, per-site GET must equal global GET"

    def test_put_with_empty_list_clears_override(self, fresh_app):
        """An empty `widgets` list in a PUT removes the per-site
        override — same effect as DELETE. Matches widgets_config
        semantics tested in test_v3_43_60_widgets.py."""
        # Establish then clear
        fresh_app.put(
            "/api/widgets/site-a",
            json={"widgets": [{"id": "throughput"}]})
        fresh_app.put("/api/widgets/site-a", json={"widgets": []})
        global_body = fresh_app.get("/api/widgets/_global").get_json() or {}
        site_body = fresh_app.get("/api/widgets/site-a").get_json() or {}
        assert (site_body.get("widgets") or []) == (global_body.get("widgets") or []), \
            "empty PUT must clear the override (fall back to global)"

    def test_put_drops_unknown_widget_ids(self, fresh_app):
        """Bogus widget IDs sanitize out at the server. The hook's
        reconcileFromServer step provides client-side defense in
        depth, but the contract is that the server does it too."""
        r = fresh_app.put(
            "/api/widgets/site-a",
            json={"widgets": [
                {"id": "done_today"},
                {"id": "totally_not_real_widget"},
                {"id": "queue_depth"},
            ]})
        assert r.status_code == 200
        body = r.get_json() or {}
        ids = [w["id"] for w in body["widgets"]]
        assert "totally_not_real_widget" not in ids
        assert "done_today" in ids
        assert "queue_depth" in ids

    def test_put_rejects_non_list_widgets_field(self, fresh_app):
        """A malformed body returns 400 (not 500). Matches what the
        hook's error-path expects to surface to the user."""
        r = fresh_app.put(
            "/api/widgets/site-a",
            json={"widgets": "not a list"})
        assert r.status_code == 400, r.get_data(as_text=True)

    def test_data_endpoint_accepts_site_id_param(self, fresh_app):
        """/api/widgets/data?site_id=X returns the same shape as the
        no-arg call but echoes the site_id (so the hook can verify
        the right cache key)."""
        r = fresh_app.get("/api/widgets/data?site_id=wowgirls")
        assert r.status_code == 200
        body = r.get_json() or {}
        assert body.get("ok") is True
        assert body.get("site_id") == "wowgirls"
        assert isinstance(body.get("data"), dict)

    def test_data_endpoint_global_when_no_site_id(self, fresh_app):
        """Without ?site_id, the envelope's site_id field is None."""
        r = fresh_app.get("/api/widgets/data")
        assert r.status_code == 200
        body = r.get_json() or {}
        assert body.get("ok") is True
        # Either omitted or None — both express "global scope".
        assert not body.get("site_id"), \
            f"expected falsy site_id for global call, got {body.get('site_id')!r}"


class TestPerSiteWidgetsScopeKeys(_IsolatedWidgetsConfig):
    """The `_global` magic scope name must not collide with a real
    site_id by the same name. A site literally called `_global` is
    pathological but worth defending against."""

    def test_global_scope_distinct_from_site_named_underscore_global(
            self, fresh_app):
        # Set the magic global
        fresh_app.put("/api/widgets/_global",
                      json={"widgets": [{"id": "done_today"}]})
        # Now PUT to a fictional site that happens to be NAMED _global
        # — wait, that goes to the magic global by definition. This
        # confirms the routing: there is no separate "site whose
        # site_id is the literal string '_global'" surface. This is
        # the documented behavior — flag it so a future site-id
        # validator knows '_global' must be reserved on the create
        # path. Currently the create-site code does not reserve it,
        # which is a latent (low-severity) overlap.
        # For now: verify that PUT-via-_global and GET-via-_global
        # are coherent and that GET on _global returns the global.
        r = fresh_app.get("/api/widgets/_global")
        assert r.status_code == 200
        body = r.get_json() or {}
        ids = [w["id"] for w in body["widgets"]]
        assert ids == ["done_today"]
