import json
from dataclasses import asdict

import pytest

from bulk_downloader import phoenix_catalog as pc

BD_GATE_SCOPE = "module"


@pytest.fixture
def catalog(monkeypatch):
    entries = {
        "me": {
            "name": "Boundary Brand",
            "network": "",
            "host_patterns": ["me.com"],
            "scene_url_hints": ["/scene/"],
        }
    }
    monkeypatch.setattr(pc, "CATALOG", entries)
    monkeypatch.setattr(pc, "_extra_cache", {})
    assert pc.merged_catalog() == entries
    assert len(pc.merged_catalog()) == 1
    return entries


@pytest.mark.parametrize("host", [
    "awesome.company.com", "notme.com", "me.com.evil.test", "me.computer.test",
])
def test_substring_without_domain_boundary_does_not_match(catalog, host):
    assert "me.com" in host
    assert pc._etld1(host) != pc._etld1("me.com")
    result = pc.lookup_url(f"https://{host}/plain")
    assert result is None, f"cross-domain substring routed {host}: {result!r}"


@pytest.mark.parametrize("host,confidence", [
    ("me.com", 1.0), ("cdn.me.com", 0.95), ("images.cdn.me.com", 0.95),
])
def test_exact_host_and_dot_delimited_subdomains_match(catalog, host, confidence):
    result = pc.lookup_url(f"https://{host}/plain")
    assert result is not None
    assert asdict(result) == {
        "brand_id": "me", "name": "Boundary Brand", "network": "",
        "network_display": "", "confidence": confidence, "matched_pattern": "me.com",
    }


def test_suffix_fallback_still_runs_when_etld_branch_does_not_match(catalog):
    catalog["me"]["host_patterns"] = ["com"]
    assert pc._etld1("cdn.me.com") != pc._etld1("com")
    result = pc.lookup_url("https://cdn.me.com/plain")
    assert result is not None
    assert result.brand_id == "me"
    assert result.matched_pattern == "com"
    assert result.confidence == 0.6


def test_untouched_exact_match_is_byte_identical(catalog):
    result = pc.lookup_url("https://me.com/scene/42")
    assert result is not None
    actual = json.dumps(asdict(result), sort_keys=True, separators=(",", ":")).encode()
    expected = (
        b'{"brand_id":"me","confidence":1.0,"matched_pattern":"me.com",'
        b'"name":"Boundary Brand","network":"","network_display":""}'
    )
    assert actual == expected
