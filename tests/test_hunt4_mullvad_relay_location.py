"""Malformed relay locations cannot discard otherwise valid relays."""

BD_GATE_SCOPE = "module"


def test_malformed_location_skips_only_its_relay():
    from bulk_downloader.vpn_providers import mullvad

    valid = {
        "hostname": "us-nyc-001",
        "location": {"country": "us", "city": "New York"},
        "pubkey": "A" * 43 + "=",
        "ipv4_addr_in": "203.0.113.1",
        "port": 51820,
    }
    assert [r["id"] for r in mullvad._parse_relay_list([valid])] == ["us-nyc-001"]

    malformed = {**valid, "hostname": "us-nyc-002", "location": "us"}
    bad_country = {**valid, "hostname": "us-nyc-003", "location": {"country": 123}}
    assert [r["id"] for r in mullvad._parse_relay_list([malformed, bad_country, valid])] == ["us-nyc-001"]
