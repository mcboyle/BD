"""O1826 c38 (H14): `eligibility_overview` must count `participation_eligible_sites` from the
`evaluate_site` rows, not report a hard-coded 0. A real per-(site, kind) grant plus a
registered apply kind (reverser) let the real `evaluate_site` return True for one site;
the rollup (and the compact status built on it) must then report 1. Oracle tier, evidence
freshness and the Class C level are pinned at their read seams so only the grant and the
apply path decide participation."""

import datetime as _dt

import pytest

from tools import autonomy_eligibility as el
from tools import autonomy_grant as G
from tools import autonomy_guardrails as agr
from tools import autonomy_oracle as ao
from tools import autonomy_policy as ap

BD_GATE_SCOPE = "module"
KIND = "live_site_config"
SITE = "c38-granted.example"
OTHER = "c38-bystander.example"


@pytest.fixture
def gates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "cockpit_tasks"))
    fresh = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        ao,
        "oracle_verdict",
        lambda s, candidate=None, held_out=None: {
            "tier": 3,
            "tier_name": "tier3",
            "hard_failures": None,
        },
    )
    monkeypatch.setattr(el, "_evidence_ts_for", lambda s: fresh)
    monkeypatch.setattr(
        ap, "can_autonomously", lambda c: {"allowed": True, "reason": "c38"}
    )
    monkeypatch.setattr(agr, "_REVERSERS", dict(agr._REVERSERS))
    agr.register_reverser(KIND, lambda target_ref, before: None)
    assert el.apply_path_exists(KIND) is True
    assert ap.is_frozen() is False


def test_rollup_counts_granted_site(gates):
    G.grant_site(SITE, kind=KIND, by="op", reason="c38")
    rows = {s: el.evaluate_site(s)["participation_eligible"] for s in (SITE, OTHER)}
    assert rows == {SITE: True, OTHER: False}
    ov = el.eligibility_overview([SITE, OTHER])
    assert ov["participation_eligible_sites"] == 1, (
        "C38-ROLLUP-HARDCODED: overview reports "
        f"{ov['participation_eligible_sites']} while evaluate_site rows say {rows}"
    )
    assert "No site is participation-eligible" not in ov["_note"]


def test_status_counts_granted_site(gates, monkeypatch):
    G.grant_site(SITE, kind=KIND, by="op", reason="c38")
    monkeypatch.setattr(ao, "_all_sites", lambda: [SITE, OTHER])
    assert el.eligibility_status()["participation_eligible_sites"] == 1


def test_no_grant_rollup_is_zero(gates, monkeypatch):
    monkeypatch.setattr(ao, "_all_sites", lambda: [SITE, OTHER])
    assert el.evaluate_site(SITE)["participation_eligible"] is False
    assert el.eligibility_overview([SITE, OTHER])["participation_eligible_sites"] == 0
    assert el.eligibility_status()["participation_eligible_sites"] == 0


BYSTANDERS = ["c38-b1.example", "c38-b2.example", "c38-b3.example"]
GRANTED = ["c38-g1.example", "c38-g2.example"]


def _grant_past_cap():
    """Three ungranted evidence-qualified sites fill the considered cap ahead of two
    granted ones, so both eligible sites sit in `over_cap_excluded`."""
    sites = BYSTANDERS + GRANTED
    for s in GRANTED:
        G.grant_site(s, kind=KIND, by="op", reason="c38")
    rows = {s: el.evaluate_site(s)["participation_eligible"] for s in sites}
    assert rows == {s: s in GRANTED for s in sites}
    assert len(BYSTANDERS) == el.MAX_ELIGIBLE_SITES
    return sites


def test_overview_counts_every_eligible_site_past_cap(gates):
    sites = _grant_past_cap()
    ov = el.eligibility_overview(sites)
    assert ov["considered_for_experimentation"] == BYSTANDERS
    assert ov["over_cap_excluded"] == GRANTED
    assert ov["participation_eligible_sites"] == 2, (
        "C38-ROLLUP-MULTI-OR-CAPPED: overview reports "
        f"{ov['participation_eligible_sites']} for eligible {GRANTED} outside the cap"
    )


def test_status_counts_every_eligible_site_past_cap(gates, monkeypatch):
    sites = _grant_past_cap()
    monkeypatch.setattr(ao, "_all_sites", lambda: sites)
    st = el.eligibility_status()
    assert st["considered_count"] == el.MAX_ELIGIBLE_SITES
    assert st["participation_eligible_sites"] == 2, (
        "C38-STATUS-MULTI-OR-CAPPED: status reports "
        f"{st['participation_eligible_sites']} for eligible {GRANTED} outside the cap"
    )


FOUR = ["c38-f1.example", "c38-f2.example", "c38-f3.example", "c38-f4.example"]


def test_count_is_not_capped_at_max_eligible_sites(gates, monkeypatch):
    """MAX_ELIGIBLE_SITES caps the considered set only; the count of eligible rows does
    not stop at the cap."""
    assert len(FOUR) == el.MAX_ELIGIBLE_SITES + 1
    for s in FOUR:
        G.grant_site(s, kind=KIND, by="op", reason="c38")
    rows = {s: el.evaluate_site(s)["participation_eligible"] for s in FOUR}
    assert rows == {s: True for s in FOUR}
    ov = el.eligibility_overview(FOUR)
    assert ov["participation_eligible_sites"] == 4, (
        "C38-ROLLUP-CAPPED-AT-MAX: overview reports "
        f"{ov['participation_eligible_sites']} for 4 eligible rows {rows}"
    )
    monkeypatch.setattr(ao, "_all_sites", lambda: FOUR)
    st = el.eligibility_status()
    assert st["participation_eligible_sites"] == 4, (
        "C38-STATUS-CAPPED-AT-MAX: status reports "
        f"{st['participation_eligible_sites']} for 4 eligible rows {rows}"
    )
