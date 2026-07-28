"""A malformed VPN tunnel must not take the whole inventory down with it.

Observed on the deploy box, at every boot:

    [vpn-runtime] load/register failed: tunnel config missing required field: name
      ! VPN runtime registration errors: ['tunnel config missing required field: name']

vpn_config.load() validated tunnels inside a list comprehension, so the FIRST
bad record aborted the whole file. One tunnel missing `name` therefore made
every OTHER tunnel invisible, left `_state` half-mutated (global_settings
already assigned, tunnels not), and left `_loaded` False. Three separate
failures follow from that one abort and each is pinned below:

  * the operator's other tunnels vanish from the running system;
  * `vpn_kill_switch.set_auto_recover()` is never reached in vpn_runtime.init(),
    because the raise happens two statements earlier -- so a configured
    auto-recover setting is silently ignored;
  * any later save() serialises the now-EMPTY tunnel list back over the
    operator's file, destroying the records that failed to parse.

The fix is quarantine-and-report, NOT repair. BD must not invent a `name` for
a record the operator wrote (that is silent repair of operator config), and it
must not drop the record either (that is data loss). It isolates the bad
record, keeps the good ones, reports precisely which record and why, and
writes the bad record back verbatim so the operator can fix it.

The second half of this file pins the reason the bad file existed at all:
BD's OWN raw-store editor accepted and wrote it. `_validate_shape` checked
only that each tunnel had a string `tunnel_id`, while the loader requires four
fields -- so the write gate's denominator did not contain the load gate's
subject (CLAUDE.md 0). The editor wrote the file, THEN failed to reload it,
and reported `write failed` -- a false statement, since the write had already
succeeded and the deployment was now broken at every subsequent boot.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest


class _EnvSaver:
    def __init__(self):
        self._saved = []

    def setenv(self, k, v):
        self._saved.append((k, os.environ.get(k)))
        os.environ[k] = str(v)

    def restore(self):
        while self._saved:
            k, v = self._saved.pop()
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Base:
    def setup_method(self):
        self._envsaver = _EnvSaver()
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="vpn-quarantine-"))
        self._path = self._tmp_dir / "tunnels.json"
        self._envsaver.setenv("BD_VPN_CONFIG_PATH", str(self._path))
        from bulk_downloader import vpn_config
        vpn_config._reset_for_tests()

    def teardown_method(self):
        from bulk_downloader import vpn_config
        vpn_config._reset_for_tests()
        self._envsaver.restore()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # A file holding one good tunnel and one that is missing `name` -- the
    # exact shape the box boots with.
    def _write_mixed_file(self):
        self._path.write_text(json.dumps({
            "schema_version": 1,
            "global_settings": {"kill_switch_auto_recover": False},
            "tunnels": [
                {"tunnel_id": "tun-good", "name": "Mullvad NYC",
                 "provider": "mullvad", "backend": "wireguard",
                 "config": {"endpoint": "1.2.3.4:51820"}},
                {"tunnel_id": "tun-noname", "provider": "mullvad",
                 "backend": "wireguard",
                 "config": {"endpoint": "5.6.7.8:51820"}},
            ],
        }, indent=2), encoding="utf-8")


class TestLoadQuarantine(_Base):

    def test_one_malformed_tunnel_does_not_hide_the_valid_ones(self):
        """The blast radius of a bad RECORD must be that record."""
        from bulk_downloader import vpn_config
        self._write_mixed_file()
        vpn_config.load()  # must not raise
        ids = [t["tunnel_id"] for t in vpn_config.list_tunnel_configs()]
        assert ids == ["tun-good"], (
            f"expected the valid tunnel to survive a sibling's validation "
            f"failure, got {ids}"
        )

    def test_load_reports_the_offending_record_instead_of_hiding_it(self):
        """Quarantine without a report would be silent data suppression.

        The old message named neither the tunnel nor the file, so the operator
        could not find the record to fix. Both must be present.
        """
        from bulk_downloader import vpn_config
        self._write_mixed_file()
        vpn_config.load()
        errors = vpn_config.load_errors()
        assert len(errors) == 1, f"expected exactly one load error, got {errors}"
        err = errors[0]
        assert err["tunnel_id"] == "tun-noname"
        assert "name" in err["error"]
        assert str(self._path) == err["path"], (
            "the error must name the file the operator has to edit"
        )

    def test_a_clean_file_reports_no_load_errors(self):
        """Negative control: a gate that always fires is as bad as one that never does."""
        from bulk_downloader import vpn_config
        self._path.write_text(json.dumps({
            "schema_version": 1,
            "tunnels": [{"tunnel_id": "tun-ok", "name": "OK",
                         "provider": "generic", "backend": "wireguard"}],
        }), encoding="utf-8")
        vpn_config.load()
        assert vpn_config.load_errors() == []
        assert [t["tunnel_id"] for t in vpn_config.list_tunnel_configs()] == ["tun-ok"]

    def test_save_does_not_destroy_the_record_it_could_not_parse(self):
        """The quarantined record stays on disk, verbatim.

        Dropping it would be BD silently deleting operator config -- the same
        class of harm as the whole-file abort, just quieter.
        """
        from bulk_downloader import vpn_config
        self._write_mixed_file()
        vpn_config.load()
        vpn_config.save()
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        ids = [t.get("tunnel_id") for t in on_disk["tunnels"]]
        assert "tun-noname" in ids, (
            f"the unparseable record was deleted by save(); on disk: {ids}"
        )
        bad = [t for t in on_disk["tunnels"] if t.get("tunnel_id") == "tun-noname"][0]
        assert bad.get("config", {}).get("endpoint") == "5.6.7.8:51820", (
            "the quarantined record must be written back verbatim, not normalised"
        )

    def test_quarantined_records_are_not_registered_live(self):
        """Isolated means isolated: it must not reach vpn.py."""
        from bulk_downloader import vpn_config, vpn
        vpn._reset_for_tests()
        self._write_mixed_file()
        vpn_config.load()
        count, errors = vpn_config.register_loaded_tunnels()
        live = {t.tunnel_id for t in vpn.list_tunnels()}
        assert "tun-noname" not in live
        assert "tun-good" in live
        # and the boot line must still carry the fault, not swallow it
        assert any("tun-noname" in str(e) for e in errors), (
            f"register_loaded_tunnels must surface the quarantined record so "
            f"app.py prints it at boot; got {errors}"
        )

    def test_auto_recover_setting_is_applied_despite_a_bad_tunnel(self):
        """vpn_runtime.init() reached set_auto_recover only if load() returned.

        This is the security-relevant consequence of the abort: a malformed
        tunnel silently discarded the operator's kill-switch preference.
        """
        from bulk_downloader import vpn_config, vpn_kill_switch, vpn_runtime
        vpn_runtime._reset_for_tests()
        vpn_kill_switch.set_auto_recover(True)
        self._write_mixed_file()  # global_settings.kill_switch_auto_recover = False
        vpn_runtime.init({"sites": []}, start_monitors=False)
        assert vpn_kill_switch.get_auto_recover() is False, (
            "the operator's kill_switch_auto_recover=False was not applied; "
            "load() raised before vpn_runtime.init() could read it"
        )


class TestQuarantineFieldsAreNotConfigKeys(_Base):
    """tools/config_surface_inventory excludes these names; prove it may.

    That scanner finds candidate VPN config keys with a regex for
    `"<lowercase>"` followed by `:`, which also matches the fields of the
    quarantine REPORT that load() builds. Those names are excluded there via
    _NOT_STORE_KEYS. An exclusion nobody checks is how a real config key goes
    missing from the parity ledger, so the claim is verified here against a
    real saved document rather than asserted in a comment.
    """

    def test_report_field_names_are_never_persisted_to_the_store(self):
        from bulk_downloader import vpn_config
        from tools.config_surface_inventory import _NOT_STORE_KEYS
        self._write_mixed_file()
        vpn_config.load()
        assert vpn_config.load_errors(), "expected a quarantined record to exist"
        vpn_config.add_tunnel_config({
            "tunnel_id": "tun-x", "name": "X",
            "provider": "generic", "backend": "wireguard"})

        document = json.loads(self._path.read_text(encoding="utf-8"))

        def every_key(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from every_key(v)
            elif isinstance(node, list):
                for v in node:
                    yield from every_key(v)

        persisted = set(every_key(document))
        leaked = sorted(_NOT_STORE_KEYS & persisted)
        assert not leaked, (
            f"{leaked} IS persisted in the store document, so excluding it "
            f"from tools/config_surface_inventory hides a real config key "
            f"from the GUI-parity ledger"
        )


class TestRawStoreEditorMatchesTheLoader(_Base):
    """The write gate must reject exactly what the load gate cannot read."""

    def _client(self):
        import flask
        from bulk_downloader import app_store_raw_editor as raw
        app = flask.Flask(__name__)
        raw.register_routes(app)
        return app.test_client()

    def test_editor_refuses_a_tunnel_the_loader_would_reject(self):
        from bulk_downloader import vpn_config
        self._path.write_text(json.dumps({
            "schema_version": 1, "global_settings": {},
            "tunnels": [{"tunnel_id": "tun-good", "name": "Good",
                         "provider": "mullvad", "backend": "wireguard"}],
        }, indent=2), encoding="utf-8")
        vpn_config.load()
        before = hashlib.sha256(self._path.read_bytes()).hexdigest()

        bad = {"schema_version": 1, "global_settings": {},
               "tunnels": [{"tunnel_id": "tun-good", "provider": "mullvad",
                            "backend": "wireguard"}]}
        resp = self._client().post("/api/settings/store-raw",
                                   json={"store": "vpn",
                                         "text": json.dumps(bad)})
        assert resp.status_code == 400, (
            f"a payload the loader cannot read must be refused BEFORE the "
            f"write, got HTTP {resp.status_code}: {resp.get_json()}"
        )
        after = hashlib.sha256(self._path.read_bytes()).hexdigest()
        assert before == after, (
            "the file was modified by a request that was reported as failed"
        )
        assert "name" in str(resp.get_json().get("error", "")), (
            "the refusal must name the missing field so the operator can fix it"
        )

    def test_editor_still_accepts_a_well_formed_payload(self):
        """Negative control -- the tightened gate must not block real edits."""
        from bulk_downloader import vpn_config
        vpn_config.load()
        good = {"schema_version": 1, "global_settings": {},
                "tunnels": [{"tunnel_id": "tun-new", "name": "New",
                             "provider": "generic", "backend": "openvpn"}]}
        resp = self._client().post("/api/settings/store-raw",
                                   json={"store": "vpn",
                                         "text": json.dumps(good)})
        assert resp.status_code == 200, resp.get_json()
        assert [t["tunnel_id"] for t in vpn_config.list_tunnel_configs()] == ["tun-new"]


class TestStatusSurfacesLoadErrors(_Base):
    """The fault must be visible over HTTP, not only in one boot stderr line.

    Without this, a config-load failure is indistinguishable over the API from
    "no tunnels configured" -- which is precisely why L30 reported an honest
    but useless WARN, and why the seeder cannot tell a safe host from a broken
    one.
    """

    def test_vpn_status_reports_config_load_errors(self):
        import flask
        from bulk_downloader import vpn_config, app_vpn_api
        self._write_mixed_file()
        vpn_config.load()
        app = flask.Flask(__name__)
        app_vpn_api.register_routes(app)
        body = app.test_client().get("/api/vpn/status").get_json()
        assert "config_load_errors" in body, (
            "/api/vpn/status must expose config load failures; without it a "
            "broken tunnels.json looks identical to an empty one"
        )
        assert any(e.get("tunnel_id") == "tun-noname"
                   for e in body["config_load_errors"]), body["config_load_errors"]

    def test_vpn_status_reports_an_empty_list_when_config_is_clean(self):
        import flask
        from bulk_downloader import vpn_config, app_vpn_api
        vpn_config.load()
        app = flask.Flask(__name__)
        app_vpn_api.register_routes(app)
        body = app.test_client().get("/api/vpn/status").get_json()
        assert body.get("config_load_errors") == []
