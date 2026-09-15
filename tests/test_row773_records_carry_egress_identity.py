"""Row 773: every history and login record carries its egress identity.

test2 downloaded through 174.178.147.233 (CLI) while its browser/CF path
left through 75.165.3.230; the IP-signed CDN answered 474 and nothing in
the history or login records said WHICH egress the failing request used, so
attribution among IP / credential / browser stalled (SITE_RUNBOOK.md
"EGRESS RESOLVED"; BACKLOG_CROSSREF_REPORT.md row 722). Product half only:
a record always carries an IP literal or the exact token "UNKNOWN"; the
environment (one residential egress, row 722) stays deferred.
"""
from __future__ import annotations

import importlib
import ipaddress
import json
import sqlite3
from pathlib import Path

import pytest

# Subject: the two record writers (history + session_history login rows),
# the schema/migration that carries the column, and the producers that
# observe an egress. That is a property of the tree, not of one module.
BD_GATE_SCOPE = "repo-wide"

UNKNOWN = "UNKNOWN"
NO_EGRESS_IDENTITY = "record carries no egress identity"


def _carries_egress_identity(value) -> None:
    """The row-773 invariant: an IP literal, or exactly "UNKNOWN"."""
    if value == UNKNOWN:
        return
    try:
        ipaddress.ip_address(value)
    except (TypeError, ValueError):
        pytest.fail(f"{NO_EGRESS_IDENTITY}: egress_ip={value!r}")


@pytest.fixture(autouse=True)
def _empty_egress_registry(monkeypatch):
    """Each test starts with no observation and no site binding. The registry
    is process-global by design; swapping the dicts (not clearing them)
    leaves nothing to restore and no test-only reset in production code."""
    try:
        ident = importlib.import_module("bulk_downloader.egress_identity")
    except ImportError:
        return  # the base tree has no registry: nothing to empty, RED stays RED
    monkeypatch.setattr(ident, "_observed", {})
    monkeypatch.setattr(ident, "_carrier_of_site", {})


@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    db = importlib.import_module("bulk_downloader.db")
    path = tmp_path / "row773.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.db_init()
    return db, path


def _history_rows(path: Path) -> list[dict]:
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "egress_ip" in cols, (
        f"{NO_EGRESS_IDENTITY}: history columns={sorted(cols)}")
    return [dict(r) for r in cx.execute("SELECT * FROM history ORDER BY id")]


def _login_rows(path: Path) -> list[dict]:
    cx = sqlite3.connect(path)
    rows = [json.loads(d) for (d,) in cx.execute(
        "SELECT detail FROM session_history WHERE event_type='login_attempt' "
        "ORDER BY id")]
    for detail in rows:
        assert "egress_ip" in detail, (
            f"{NO_EGRESS_IDENTITY}: login detail keys={sorted(detail)}")
    return rows


# -- history -----------------------------------------------------------------

def test_a_history_record_without_a_measurement_says_unknown(fresh_db):
    db, path = fresh_db
    db.db_log("row773", "Row 773", "https://x/1", "failed", message="474")
    rows = _history_rows(path)
    assert len(rows) == 1
    _carries_egress_identity(rows[0]["egress_ip"])
    assert rows[0]["egress_ip"] == UNKNOWN


def test_a_history_record_stamps_the_measured_egress_ip(fresh_db):
    """db_log resolves the stamp from the site's bound carrier; a caller
    passes nothing. A carrier with an unparseable measurement stays UNKNOWN."""
    db, path = fresh_db
    ident = importlib.import_module("bulk_downloader.egress_identity")
    ident.observe_egress_ip("socks5://127.0.0.1:43773", "104.234.212.140")
    ident.observe_egress_ip("http://proxy.example:3128", "not an ip")
    ident.bind_site_carrier("row773", "socks5://127.0.0.1:43773")
    db.db_log("row773", "Row 773", "https://x/1", "done", "f.mp4", 10)
    ident.bind_site_carrier("row773", "http://proxy.example:3128")
    db.db_log("row773", "Row 773", "https://x/2", "failed", message="474")
    ident.bind_site_carrier("other", "socks5://127.0.0.1:43773")
    db.db_log("row773", "Row 773", "https://x/3", "failed", message="474")
    rows = _history_rows(path)
    assert [r["egress_ip"] for r in rows] == [
        "104.234.212.140", UNKNOWN, UNKNOWN]
    for r in rows:
        _carries_egress_identity(r["egress_ip"])


def test_an_existing_database_gains_egress_ip_through_migrations(fresh_db):
    db, path = fresh_db
    m = importlib.import_module("bulk_downloader.migrations")
    cx = sqlite3.connect(path)
    cx.execute("ALTER TABLE history DROP COLUMN egress_ip")
    cx.commit()
    before = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    cx.close()
    assert "egress_ip" not in before, "fixture did not reach the pre-773 shape"
    m.apply_pending(backup_first=False)
    after = {r[1] for r in sqlite3.connect(path).execute(
        "PRAGMA table_info(history)")}
    assert "egress_ip" in after


# -- login attempts ------------------------------------------------------------

def test_a_login_record_carries_egress_identity(fresh_db):
    db, path = fresh_db
    sk = importlib.import_module("bulk_downloader.session_keeper")
    ident = importlib.import_module("bulk_downloader.egress_identity")
    granted = sk.reserve_login_attempt("row773", "runner_auth.login_async", 5)
    assert granted["granted"] is True
    ident.observe_egress_ip("socks5://127.0.0.1:43773", "75.165.3.230")
    ident.bind_site_carrier("row773", "socks5://127.0.0.1:43773")
    # record_login_attempt is the keeper's own writer; it enters the same
    # reservation and so carries the same identity.
    sk.record_login_attempt("row773", "app._do_login_for_keeper")
    rows = _login_rows(path)
    assert [r["egress_ip"] for r in rows] == [UNKNOWN, "75.165.3.230"]
    for r in rows:
        _carries_egress_identity(r["egress_ip"])
    # The existing attribution survives the added key.
    assert [r["source"] for r in rows] == [
        "runner_auth.login_async", "app._do_login_for_keeper"]


# -- producers -------------------------------------------------------------

def test_the_ipv4_leak_probe_observes_the_carrier_egress(monkeypatch):
    """run_probe("ipv4", port) is what the monitor and the UI call."""
    leak = importlib.import_module("bulk_downloader.vpn_leak_tests")
    ident = importlib.import_module("bulk_downloader.egress_identity")
    monkeypatch.setattr(leak, "_http_get_json_field",
                        lambda url, proxy_url, fields: "198.51.100.7")
    result = leak.run_probe(leak.ProbeId.IPV4.value, 43773)
    assert result.passed is True
    assert ident.egress_ip_for("socks5://127.0.0.1:43773") == "198.51.100.7"
    assert ident.egress_ip_for("socks5://127.0.0.1:1") == UNKNOWN


def test_the_tunnel_health_pass_observes_the_tunnel_exit(monkeypatch):
    """_run_health_pass -> _record_health: the tunnel's public_ip is the
    exit of the socks carrier its clients are built with."""
    vpn = importlib.import_module("bulk_downloader.vpn")
    ident = importlib.import_module("bulk_downloader.egress_identity")
    t = vpn.Tunnel("t773", "row773", "generic", "wireguard",
                   socks_port=43773, state="up")
    monkeypatch.setattr(vpn, "_tunnels", {"t773": t})
    monkeypatch.setattr(vpn, "check_health",
                        lambda tid: {"ok": True, "public_ip": "203.0.113.9"})
    vpn._run_health_pass()
    assert t.public_ip == "203.0.113.9"
    assert ident.egress_ip_for("socks5://127.0.0.1:43773") == "203.0.113.9"


# -- choosers: the two places a carrier is picked bind it to the site ------

def test_the_download_client_binds_its_carrier_to_the_site():
    eg = importlib.import_module("bulk_downloader.download_egress")
    ident = importlib.import_module("bulk_downloader.egress_identity")
    ident.observe_egress_ip("socks5://127.0.0.1:43773", "198.51.100.7")
    ident.observe_egress_ip("http://proxy.example:3128", "192.0.2.4")
    socks = lambda site_id: "socks5://127.0.0.1:43773" if site_id == "tun" else None
    assert eg.effective_download_proxy(None, "tun", socks) == "socks5://127.0.0.1:43773"
    assert ident.egress_ip_for_site("tun") == "198.51.100.7"
    assert eg.effective_download_proxy("http://proxy.example:3128", "tun", socks) \
        == "http://proxy.example:3128"
    assert ident.egress_ip_for_site("tun") == "192.0.2.4"
    assert eg.effective_download_proxy(None, "clear", socks) is None
    assert ident.egress_ip_for_site("clear") == UNKNOWN
    assert eg.effective_download_proxy(None, "tun", None) is None
    assert ident.egress_ip_for_site("tun") == UNKNOWN
    assert ident.egress_ip_for_site("never-chosen") == UNKNOWN


def test_the_browser_binds_the_tunnel_to_the_site(monkeypatch):
    """The login runs in the browser: playwright_proxy_for_site is its
    chooser, so a login record carries the tunnel's exit."""
    vr = importlib.import_module("bulk_downloader.vpn_runtime")
    ident = importlib.import_module("bulk_downloader.egress_identity")
    ident.observe_egress_ip("socks5://127.0.0.1:43773", "198.51.100.7")
    monkeypatch.setattr(
        vr, "get_socks_url_for_site",
        lambda site_id: "socks5://127.0.0.1:43773" if site_id == "tun" else None)
    assert vr.playwright_proxy_for_site("tun") == {"server": "socks5://127.0.0.1:43773"}
    assert ident.egress_ip_for_site("tun") == "198.51.100.7"
    assert vr.playwright_proxy_for_site("tun") is not None
    assert vr.playwright_proxy_for_site("clear") is None
    assert ident.egress_ip_for_site("clear") == UNKNOWN


def test_the_postgres_mirror_carries_the_column():
    """pg_backend._PG_SCHEMA: "Column sets track db.py"."""
    src = (Path(__file__).resolve().parent.parent / "bulk_downloader"
           / "pg_backend.py").read_text(encoding="utf-8")
    hist = [s for s in src.split("CREATE TABLE") if "history(" in s]
    assert hist and "egress_ip" in hist[0], (
        f"{NO_EGRESS_IDENTITY}: the PG mirror's history table")


# -- controls ------------------------------------------------------------------

def test_the_invariant_rejects_a_record_with_no_identity():
    with pytest.raises(pytest.fail.Exception) as ei:
        _carries_egress_identity(None)
    assert NO_EGRESS_IDENTITY in str(ei.value)
    with pytest.raises(pytest.fail.Exception):
        _carries_egress_identity("")


def test_the_fixture_wrote_exactly_the_records_it_judges(fresh_db):
    db, path = fresh_db
    sk = importlib.import_module("bulk_downloader.session_keeper")
    db.db_log("row773", "Row 773", "https://x/1", "failed", message="474")
    db.db_log("row773", "Row 773", "https://x/2", "done", "f.mp4", 10)
    sk.reserve_login_attempt("row773", "runner_auth.login_async", 5)
    n_history = len(_history_rows(path))
    n_login = len(_login_rows(path))
    assert (n_history, n_login) == (2, 1)
    assert n_history + n_login == 3
