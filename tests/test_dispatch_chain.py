"""U5 — dispatch-chain tracer (D-11) + dispatch dry-run (D-20).

Covers the two read-only tools that make runner.py::_process_one's
dispatch chain observable, plus a PIN GUARD: test_process_one_order_*
re-reads _process_one's real source and asserts the documented branch
sequence still holds. If anyone reorders the dispatch chain (violating
INV-002), that guard fails loudly instead of the tools silently lying.
"""
import types

from bulk_downloader import dev_suite as ds


def _runner(cfg):
    """A minimal stand-in for app.runners[sid] — only .config matters."""
    return {"s1": types.SimpleNamespace(config=cfg)}


# ── dispatch_chain (D-11) ─────────────────────────────────────────

def test_dispatch_chain_has_eleven_ordered_branches():
    r = ds.dispatch_chain()
    assert r["branch_count"] == 11   # v3.66.692: +plugin_extractor branch
    steps = [c["step"] for c in r["chain"]]
    assert steps == list(range(1, 12))
    assert r["chain"][0]["branch"] == "retry_backoff"
    assert r["chain"][-1]["branch"] == "playwright_teach"
    assert r["chain"][-1]["kind"] == "fallthrough"


def test_dispatch_chain_self_verifies_against_source():
    # The tool re-reads _process_one and confirms the chain is intact.
    r = ds.dispatch_chain()
    assert r["chain_verified"] is True, r["verify_detail"]


def test_dispatch_chain_annotates_enabled_branches_for_site():
    r = ds.dispatch_chain(site_id="s1", runners=_runner({"backend": "jd"}))
    by_branch = {c["branch"]: c for c in r["chain"]}
    assert by_branch["jdownloader"]["enabled_for_site"] is True
    assert by_branch["qbittorrent"]["enabled_for_site"] is False
    # runtime gates carry no config_key -> no enabled_for_site key
    assert "enabled_for_site" not in by_branch["cookie_relogin"]


def test_dispatch_chain_no_site_has_no_enabled_annotations():
    r = ds.dispatch_chain()
    assert all("enabled_for_site" not in c for c in r["chain"])
    assert r["site_config_source"] == "no site_id given"


# ── dispatch_dry_run (D-20) ───────────────────────────────────────

def test_dry_run_requires_a_url():
    out = ds.dispatch_dry_run("")
    assert out["ok"] is False and "url" in out["error"]


def test_dry_run_magnet_routes_to_qbittorrent():
    out = ds.dispatch_dry_run("magnet:?xt=urn:btih:deadbeef")
    assert out["routed_to"] == "qbittorrent"
    assert out["downloaded"] is False


def test_dry_run_torrent_file_routes_to_qbittorrent():
    out = ds.dispatch_dry_run("https://tracker.example/x.torrent")
    assert out["routed_to"] == "qbittorrent"


def test_dry_run_plain_url_no_config_falls_to_teach():
    out = ds.dispatch_dry_run("https://example.invalid/scene/7")
    assert out["routed_to"] == "playwright_teach"
    assert out["config_resolved"] is False


def test_dry_run_jd_backend_routes_to_jdownloader():
    out = ds.dispatch_dry_run("https://example.invalid/v/1",
                              site_id="s1", runners=_runner({"backend": "jd"}))
    assert out["routed_to"] == "jdownloader"
    assert out["config_resolved"] is True


def test_dry_run_qb_backend_routes_to_qbittorrent():
    out = ds.dispatch_dry_run("https://example.invalid/v/1", site_id="s1",
                              runners=_runner({"backend": "qbittorrent"}))
    assert out["routed_to"] == "qbittorrent"


def test_dry_run_jsonapi_needs_both_flag_and_url():
    on = ds.dispatch_dry_run(
        "https://example.invalid/v/1", site_id="s1",
        runners=_runner({"use_jsonapi": True,
                         "jsonapi_url": "https://a/heresphere"}))
    assert on["routed_to"] == "jsonapi_extractor"
    # flag set but url empty -> not eligible, falls through
    off = ds.dispatch_dry_run("https://example.invalid/v/1", site_id="s1",
                              runners=_runner({"use_jsonapi": True}))
    assert off["routed_to"] == "playwright_teach"


def test_dry_run_torrent_url_overrides_teach_backend():
    # _use_qb = explicit-backend OR torrent-url: a magnet auto-routes
    # to qB even on a teach-backend site.
    out = ds.dispatch_dry_run("magnet:?xt=urn:btih:ff", site_id="s1",
                              runners=_runner({"backend": "teach"}))
    assert out["routed_to"] == "qbittorrent"


def test_dry_run_reports_every_branch_and_never_downloads():
    out = ds.dispatch_dry_run("https://example.invalid/v/1", site_id="s1",
                              runners=_runner({"backend": "teach"}))
    branches = [s["branch"] for s in out["steps"]]
    assert branches == [e["branch"] for e in ds._DISPATCH_CHAIN]
    # runtime gates are honestly marked not-evaluated
    gates = {s["branch"]: s for s in out["steps"]}
    assert gates["auto_teach"]["evaluated"] is False
    assert gates["stash_dedup"]["evaluated"] is False
    assert out["downloaded"] is False


# ── PIN GUARD — _process_one dispatch order (INV-002) ─────────────

def test_process_one_dispatch_markers_present_and_ordered():
    """Re-read _process_one's real source and assert every documented
    branch marker appears, in order. A reorder of the dispatch chain
    breaks this test — that is the intended early-warning."""
    src = ds._read_process_one_source()
    assert src, "could not read runner.py::_process_one"
    last = -1
    for entry in ds._DISPATCH_CHAIN:
        idx = src.find(entry["marker"])
        assert idx >= 0, (
            f"branch {entry['branch']!r} marker {entry['marker']!r} "
            "missing from _process_one — dispatch chain changed")
        assert idx > last, (
            f"branch {entry['branch']!r} is out of dispatch order "
            "vs the documented chain")
        last = idx


def test_verify_chain_against_source_agrees():
    ok, detail, positions = ds._verify_chain_against_source()
    assert ok is True, detail
    assert len(positions) == 11   # v3.66.692: +plugin_extractor branch


# ── routes ────────────────────────────────────────────────────────

def test_dispatch_chain_route(fresh_app):
    resp = fresh_app.get("/api/dev/dispatch_chain")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["branch_count"] == 11   # v3.66.692: +plugin_extractor branch
    assert body["chain_verified"] is True


def test_dispatch_dryrun_route(fresh_app):
    resp = fresh_app.get(
        "/api/dev/dispatch_dryrun?url=magnet:?xt=urn:btih:abc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["routed_to"] == "qbittorrent"
    assert body["downloaded"] is False


def test_dispatch_dryrun_route_missing_url(fresh_app):
    resp = fresh_app.get("/api/dev/dispatch_dryrun")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False
