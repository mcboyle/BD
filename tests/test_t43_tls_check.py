"""T43 (Tier 4) — D-47 TLS / cert checker.

Read-only TLS cert inspection. Caller must pass hosts OR
site_configs explicitly — never auto-scans. Per-host timeout is
bounded.

Tests focus on the host-derivation logic, the SAN matching, and the
graceful-failure path. We do NOT actually open sockets in unit tests
— a single test exercises that path with an unreachable port to
verify the error path returns the expected shape.
"""
import json

from bulk_downloader import dev_suite as ds


# ── _san_matches ────────────────────────────────────────────────────

def test_san_exact_match():
    assert ds._san_matches("example.com", "example.com") is True


def test_san_case_insensitive():
    assert ds._san_matches("Example.COM", "example.com") is True


def test_san_wildcard_one_label():
    assert ds._san_matches("api.example.com", "*.example.com") is True


def test_san_wildcard_two_labels_does_not_match():
    # *.example.com must NOT match foo.bar.example.com (RFC 6125)
    assert ds._san_matches("foo.bar.example.com",
                            "*.example.com") is False


def test_san_wildcard_does_not_match_apex():
    # *.example.com does not match example.com itself
    assert ds._san_matches("example.com", "*.example.com") is False


def test_san_mismatch():
    assert ds._san_matches("example.com", "other.com") is False


def test_san_empty_inputs():
    assert ds._san_matches("", "example.com") is False
    assert ds._san_matches("example.com", "") is False


# ── _extract_hosts_from_site_configs ────────────────────────────────

def test_extract_hosts_from_login_url():
    cfg = {"siteA": {"login_url": "https://login.example.com/path"}}
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert hosts == ["login.example.com"]


def test_extract_hosts_dedupes():
    cfg = {
        "siteA": {"login_url": "https://api.example.com/a"},
        "siteB": {"login_url": "https://api.example.com/b"},
    }
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert hosts == ["api.example.com"]


def test_extract_hosts_skips_http_only():
    # We only inspect HTTPS hosts; plain HTTP has no cert to check
    cfg = {"siteA": {"login_url": "http://insecure.example.com/"}}
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert hosts == []


def test_extract_hosts_handles_missing_url():
    cfg = {"siteA": {"download_dir": "/tmp"}}  # no URL key
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert hosts == []


def test_extract_hosts_skips_non_dict_cfg():
    cfg = {"siteA": None, "siteB": "weird"}
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert hosts == []


def test_extract_hosts_handles_multiple_url_fields():
    cfg = {"siteA": {"login_url": "https://login.example.com/",
                     "home_url": "https://home.example.com/"}}
    hosts = ds._extract_hosts_from_site_configs(cfg)
    assert "login.example.com" in hosts
    assert "home.example.com" in hosts


# ── tls_cert_check public surface ──────────────────────────────────

def test_tls_check_no_hosts_no_sites(clean_workdir):
    r = ds.tls_cert_check()
    assert r["ok"] is True
    assert r["hosts_checked"] == []
    assert "no hosts" in r["verdict"]


def test_tls_check_rejects_non_list_hosts(clean_workdir):
    r = ds.tls_cert_check(hosts="example.com")  # string, not list
    assert r["ok"] is False
    assert "list" in r["verdict"]


def test_tls_check_unreachable_host(clean_workdir):
    # 127.0.0.1:1 — guaranteed not listening
    r = ds.tls_cert_check(hosts=["127.0.0.1"], port=1, timeout=0.5)
    assert r["ok"] is True
    assert len(r["results"]) == 1
    assert r["results"][0]["ok"] is False
    assert "error" in r["results"][0]


def test_tls_check_derives_from_site_configs(clean_workdir):
    # No real network call — point at an unreachable port; the goal
    # here is to verify the host derivation works through the route.
    site_configs = {
        "siteA": {"login_url": "https://127.0.0.1/foo"},
    }
    r = ds.tls_cert_check(site_configs=site_configs, port=1,
                          timeout=0.5)
    assert "127.0.0.1" in r["hosts_checked"]


def test_tls_check_timeout_bounded(clean_workdir):
    # Timeout >30 should be clamped down
    r = ds.tls_cert_check(hosts=["127.0.0.1"], port=1, timeout=999)
    # Should still return — clamp to 5.0 internally
    assert r["ok"] is True


def test_tls_check_verdict_summary(clean_workdir):
    r = ds.tls_cert_check(hosts=["127.0.0.1", "127.0.0.2"],
                           port=1, timeout=0.5)
    # All hosts fail (port 1 not listening) → verdict mentions counts
    assert "0/2" in r["verdict"] or "0 / 2" in r["verdict"] or "fail" in r["verdict"]


def test_tls_check_is_json_serializable(clean_workdir):
    r = ds.tls_cert_check(hosts=["127.0.0.1"], port=1, timeout=0.3)
    json.dumps(r)
