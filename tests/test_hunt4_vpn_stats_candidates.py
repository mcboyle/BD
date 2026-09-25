"""VPN profile selection respects the supplied candidate set."""

BD_GATE_SCOPE = "module"


def test_empty_candidate_set_cannot_select_profile(monkeypatch):
    from bulk_downloader import vpn_stats

    report = [{
        "vpn_profile": "profile-a", "site_id": "site-a",
        "attempts": 5, "success_rate": 100.0,
    }]
    monkeypatch.setattr(vpn_stats, "profile_report", lambda **_: report)
    monkeypatch.setattr(vpn_stats, "current_blacklist", list)

    assert vpn_stats.best_profile_for("site-a", candidate_profiles=["profile-a"])["vpn_profile"] == "profile-a"
    assert vpn_stats.best_profile_for("site-a", candidate_profiles=[]) is None

    # lens (B1): the only product caller (app_vpn.py) passes no candidate set;
    # the default None must stay "unrestricted", not "filter by None".
    assert vpn_stats.best_profile_for("site-a")["vpn_profile"] == "profile-a"
