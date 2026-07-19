"""Cut 7 (7.2) — site onboarding automation, automated UP TO the enable.

`tools/site_onboard.py` drives the EXISTING detect -> dry-run -> promote_check ->
promote sequence for a new host and STOPS at the enable checkpoint: it stages
the host as ``reviewed_not_enabled`` plus a review bundle, and **never enables a
first-time host** (the mandatory approval checkpoint in AUTOMATION_POLICY). The
LLM does candidate detection/classification upstream; the orchestrator only
drives endpoints, which the tests inject as a fake client (no network).

Central invariant under test: a first-time (non-approved) host is NEVER promoted
with ``enable=True`` — the orchestrator refuses and stages for review instead.

RED on pristine 376: `tools/site_onboard.py` does not exist.
"""


class FakeClient:
    """Records every call; returns scripted, all-OK results by default."""

    def __init__(self, *, dry_run_ok=True, promote_check_ok=True):
        self.calls = []
        self._dry_run_ok = dry_run_ok
        self._promote_check_ok = promote_check_ok

    def detect(self, host):
        self.calls.append(("detect", host, {}))
        return {"ok": True, "candidates": [{"selector": ".dl", "kind": "download"}]}

    def dry_run(self, host):
        self.calls.append(("dry_run", host, {}))
        return {"ok": self._dry_run_ok, "errors": [] if self._dry_run_ok else ["selector miss"]}

    def promote_check(self, host):
        self.calls.append(("promote_check", host, {}))
        return {"ok": self._promote_check_ok,
                "errors": [] if self._promote_check_ok else ["gate failed"]}

    def promote(self, host, *, enable):
        self.calls.append(("promote", host, {"enable": enable}))
        return {"ok": True, "enabled": enable,
                "status": "enabled" if enable else "reviewed_not_enabled"}

    def status(self, host):
        self.calls.append(("status", host, {}))
        return {"host": host, "status": "reviewed_not_enabled",
                "login_flow": "site-provided", "captures": ["video"]}

    def promote_enables(self):
        return [c for c in self.calls if c[0] == "promote" and c[2].get("enable") is True]


def test_first_time_host_stages_reviewed_not_enabled():
    from tools import site_onboard as so
    c = FakeClient()
    out = so.onboard("newsite.example", client=c, approved_hosts=frozenset())
    assert out["result"] == "reviewed_not_enabled"
    assert out["enabled"] is False


def test_never_enables_a_first_time_host_even_when_enable_requested():
    # The central assertion: enable=True for a non-approved host must NOT result
    # in any promote(enable=True) call.
    from tools import site_onboard as so
    c = FakeClient()
    out = so.onboard("newsite.example", client=c,
                     approved_hosts=frozenset(), enable=True)
    assert c.promote_enables() == []          # never enabled
    assert out["enabled"] is False
    assert out["result"] == "reviewed_not_enabled"
    assert out.get("refused_enable") is True  # surfaced for the operator


def test_emits_a_review_bundle():
    from tools import site_onboard as so
    c = FakeClient()
    out = so.onboard("newsite.example", client=c, approved_hosts=frozenset())
    b = out["bundle"]
    # The bundle carries the evidence the human approval needs.
    assert "detect" in b and "dry_run" in b and "promote_check" in b
    assert isinstance(out.get("summary"), str) and out["summary"]


def test_drops_to_review_on_dry_run_failure():
    from tools import site_onboard as so
    c = FakeClient(dry_run_ok=False)
    out = so.onboard("newsite.example", client=c, approved_hosts=frozenset())
    assert out["result"] == "review"
    # never promoted at all when the dry-run failed
    assert all(call[0] != "promote" for call in c.calls)


def test_drops_to_review_on_promote_check_failure():
    from tools import site_onboard as so
    c = FakeClient(promote_check_ok=False)
    out = so.onboard("newsite.example", client=c, approved_hosts=frozenset())
    assert out["result"] == "review"
    assert all(call[0] != "promote" for call in c.calls)


def test_summary_is_plain_english_and_mentions_no_enable():
    from tools import site_onboard as so
    c = FakeClient()
    out = so.onboard("newsite.example", client=c, approved_hosts=frozenset())
    s = out["summary"].lower()
    assert "newsite.example" in s
    # makes the not-enabled state explicit so approval is a quick read
    assert "not" in s and "enabl" in s
