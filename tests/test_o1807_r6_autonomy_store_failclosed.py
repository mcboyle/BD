"""O1807 R6 — a corrupt autonomy store fails CLOSED (refuse, keep the file).

Before: autonomy_guardrails._load_pending / autonomy_trust._load swallowed a JSON error
and returned an empty store, so a decayed site read as BASELINE (fully trusted), the
blast-radius/backlog caps opened, and the next write replaced the corrupt file.
Each class is a corrupt EXISTING file: truncated JSON, wrong top-level type, or an
unreadable file (chmod 000). The control tests prove a VALID store is honoured.
"""
import json
import os

import pytest

from _cockpit_tasks import remove_test_governance
from tools import autonomy_apply as aap
from tools import autonomy_guardrails as agr
from tools import autonomy_oracle as ao
from tools import autonomy_policy as ap
from tools import autonomy_trust as atr
from tools.cockpit_core import tasks_root

BD_GATE_SCOPE = "module"

_TRUST_OK = json.dumps({"s": {"trust": 0.2}})
_PENDING_OK = json.dumps(
    {"pending": {"c1": {"change_id": "c1", "site": "other", "reviewed": False}}})
_PERM = None  # class marker: write the VALID store, then chmod 000

_TRUST_BAD = {"truncated": '{"s": {"trust": 0.2', "list": "[1,2]", "permission": _PERM}
_PENDING_BAD = {"truncated": '{"pending": {"c1": ', "list": "[]",
                "pending-list": '{"pending": []}', "null": "null", "permission": _PERM}


def _fresh():
    remove_test_governance(tasks_root())


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def corrupt():
    """Write a corrupt store; yields a reader of its bytes (perms restored first)."""
    made = []

    def make(p, bad, valid):
        if bad is _PERM and os.getuid() == 0:
            pytest.skip("chmod 000 does not stop uid 0")
        _write(p, valid if bad is _PERM else bad)
        if bad is _PERM:
            os.chmod(p, 0)
        made.append(p)

        def read_bytes():
            os.chmod(p, 0o644)
            return p.read_text(encoding="utf-8")
        return (valid if bad is _PERM else bad), read_bytes

    _fresh()
    yield make
    for p in made:
        if p.exists():
            os.chmod(p, 0o644)
    _fresh()


class TestTrustStoreFailClosed:
    def test_valid_trust_store_is_honoured(self):
        _fresh()
        _write(atr._trust_path(), _TRUST_OK)
        assert atr.effective_trust("s") == 0.2
        _fresh()

    @pytest.mark.parametrize("bad", list(_TRUST_BAD.values()), ids=list(_TRUST_BAD))
    def test_corrupt_trust_store_is_not_baseline(self, corrupt, bad):
        corrupt(atr._trust_path(), bad, _TRUST_OK)
        assert atr.effective_trust("s") == 0.0
        assert atr.trust_eligible("s") is False

    @pytest.mark.parametrize("bad", list(_TRUST_BAD.values()), ids=list(_TRUST_BAD))
    def test_corrupt_trust_store_writers_refuse_and_keep_file(self, corrupt, bad):
        want, read_bytes = corrupt(atr._trust_path(), bad, _TRUST_OK)
        r = atr.reset_trust("t", 0.9, "mboyle", "probe")
        assert r["ok"] is False and "trust store unreadable" in r["error"]
        d = atr.decay_trust("s", held_out=[], evidence_ts=None)
        assert d["ok"] is False and d["decreased"] is False
        assert read_bytes() == want

    def test_decay_all_on_corrupt_store_is_not_ok(self, corrupt, monkeypatch):
        want, read_bytes = corrupt(atr._trust_path(), _TRUST_BAD["truncated"], _TRUST_OK)
        monkeypatch.setattr(ao, "_all_sites", lambda: ["a", "b"])
        r = atr.decay_all_trust()
        assert r["ok"] is False and r["decayed"] == 2
        assert read_bytes() == want


class TestPendingStoreFailClosed:
    def test_valid_pending_store_is_honoured(self):
        _fresh()
        _write(agr._pending_path(), _PENDING_OK)
        assert agr.blast_radius_ok("s")["ok"] is False  # in-flight row seen
        assert agr.backlog_ok()["outstanding"] == 1
        _fresh()

    @pytest.mark.parametrize("bad", list(_PENDING_BAD.values()), ids=list(_PENDING_BAD))
    def test_corrupt_pending_store_closes_caps(self, corrupt, bad):
        corrupt(agr._pending_path(), bad, _PENDING_OK)
        br, bl = agr.blast_radius_ok("s"), agr.backlog_ok()
        assert br["ok"] is False and "pending review store unreadable" in br["error"]
        assert bl["ok"] is False and "pending review store unreadable" in bl["error"]

    @pytest.mark.parametrize("bad", list(_PENDING_BAD.values()), ids=list(_PENDING_BAD))
    def test_corrupt_pending_store_writers_refuse_and_keep_file(self, corrupt, bad):
        want, read_bytes = corrupt(agr._pending_path(), bad, _PENDING_OK)
        for r in (agr.register_pending("c2", "C", "s", "system"),
                  agr.mark_reviewed("c1", "accept", "mboyle"),
                  agr.sweep_review_windows("system")):
            assert r["ok"] is False and r.get("frozen") is True, r
            assert "pending review store unreadable" in r.get("reason", ""), r
        assert ap.is_frozen()
        assert read_bytes() == want


_KIND = "o1807_r6_grant_only"
_INITIAL = {"s1": {"v": 0}, "s2": {"v": 0}, "s3": {"v": 0}}


@pytest.fixture
def kind():
    """A grant-only Class-C kind (gate never reads the freeze, like queue_hk) over an
    in-memory per-site state; yields (state, applier calls)."""
    state = {s: dict(v) for s, v in _INITIAL.items()}
    calls = []

    def applier(site, after):
        calls.append(site)
        state[site] = dict(after)

    def reverser(ref, before):
        state[ref.split("::", 1)[1]] = dict(before)

    aap.register_apply_kind(_KIND, gate=lambda s: True,
                            current=lambda s: dict(state[s]),
                            proposer=lambda s: {"v": state[s]["v"] + 1},
                            applier=applier, reverser=reverser)
    return state, calls


class TestApplyForKindFailClosed:
    def test_healthy_store_applies_with_review_window(self, kind):
        state, calls = kind
        _fresh()
        r = aap.apply_for_kind("s1", _KIND, by="system")
        assert r["ok"] is True and r["deadline"], r
        assert calls == ["s1"] and state["s1"]["v"] == 1
        assert {s: state[s] for s in ("s2", "s3")} == {s: _INITIAL[s] for s in ("s2", "s3")}
        assert r["change_id"] in json.loads(agr._pending_path().read_text())["pending"]
        assert not ap.is_frozen()
        _fresh()

    @pytest.mark.parametrize("bad", list(_PENDING_BAD.values()), ids=list(_PENDING_BAD))
    def test_corrupt_pending_store_applies_nothing(self, corrupt, kind, bad):
        state, calls = kind
        want, read_bytes = corrupt(agr._pending_path(), bad, _PENDING_OK)
        r = aap.apply_for_kind("s1", _KIND, by="system")
        assert r["ok"] is False and "pending review store unreadable" in r["error"], r
        assert calls == [] and state == _INITIAL
        assert agr.list_changes() == []
        assert ap.is_frozen()
        assert read_bytes() == want

    def test_corrupt_pending_store_loop_applies_nothing(self, corrupt, kind):
        state, calls = kind
        corrupt(agr._pending_path(), _PENDING_BAD["list"], _PENDING_OK)
        r = aap.apply_all(_KIND, by="system", sites=["s1", "s2", "s3"])
        assert r["applied"] == [] and r["applied_count"] == 0, r
        assert calls == []

    def test_register_refusal_after_apply_rolls_back(self, kind):
        """The store goes bad between the pre-check and register_pending."""
        state, calls = kind
        _fresh()
        applier = aap._APPLY_KINDS[_KIND]["applier"]

        def applier_then_corrupt(site, after):
            applier(site, after)
            _write(agr._pending_path(), _PENDING_BAD["truncated"])
        aap._APPLY_KINDS[_KIND]["applier"] = applier_then_corrupt
        r = aap.apply_for_kind("s1", _KIND, by="system")
        assert r["ok"] is False and r["reverted"] is True, r
        assert "register_pending refused" in r["error"]
        assert calls == ["s1"] and state == _INITIAL
        assert agr.change_record(r["change_id"])["rolled_back"] is True
        _fresh()

    def test_register_raise_after_apply_rolls_back(self, kind, monkeypatch):
        state, calls = kind
        _fresh()

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(agr, "register_pending", boom)
        r = aap.apply_for_kind("s1", _KIND, by="system")
        assert r["ok"] is False and r["reverted"] is True, r
        assert "OSError: disk full" in r["error"]
        assert calls == ["s1"] and state == _INITIAL
        _fresh()

    def test_frozen_applies_nothing_for_grant_only_kind(self, kind):
        state, calls = kind
        _fresh()
        ap.freeze("mboyle", "probe")
        r = aap.apply_all(_KIND, by="system", sites=["s1", "s2"])
        assert r["applied"] == [] and calls == [], r
        assert aap.apply_for_kind("s1", _KIND)["reason"] == "automation frozen"
        assert state == _INITIAL
        _fresh()
