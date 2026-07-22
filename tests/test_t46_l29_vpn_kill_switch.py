"""T46 (Tier 4) — L-29 VPN kill-switch live test.

The original L-29 calls for actively toggling the kill-switch and
verifying DNS leak path blocking. That requires root, a real VPN
session, and is explicitly deferred per the DEV_BACKLOG note.

This test exercises the READ-ONLY inspector version: registration,
disruptive=False contract, and the three verdict branches (PASS,
WARN, FAIL) against patched kill-switch state.
"""
import contextlib

import live_tests.checks as checks  # noqa: F401 — register checks
import live_tests.harness as h

from bulk_downloader import vpn_kill_switch as ks


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


class _StubContext(h.Context):
    """Minimal stub for L-29 — the check uses ctx.log() but does no
    HTTP or DB access (it reads the in-process vpn_kill_switch module
    state directly)."""

    def __init__(self):
        super().__init__("http://localhost:1", "/tmp")


class _ServiceStateContext(_StubContext):
    def get(self, path, timeout=15):
        assert path == "/api/vpn/kill_switch/state"
        return True, 200, {
            "ok": True,
            "states": [{"tunnel_id": "service-tun", "killed": True}],
            "auto_recover": False,
        }, 1.0


def test_l29_reads_running_service_state_not_test_process_module():
    with _patch_kill_switch(list_states=[], auto_recover=True):
        level, detail = _get_test("L29").fn(_ServiceStateContext())
    assert level == h.FAIL
    assert "service-tun" in detail


@contextlib.contextmanager
def _patch_kill_switch(*, list_states=None, auto_recover=None,
                        list_raises=False, auto_raises=False):
    """Patch the kill-switch read API for one test."""
    orig_list = ks.list_kill_states
    orig_auto = ks.get_auto_recover

    def fake_list():
        if list_raises:
            raise RuntimeError("simulated failure")
        return list_states if list_states is not None else []

    def fake_auto():
        if auto_raises:
            raise RuntimeError("simulated failure")
        return auto_recover if auto_recover is not None else True

    ks.list_kill_states = fake_list
    ks.get_auto_recover = fake_auto
    try:
        yield
    finally:
        ks.list_kill_states = orig_list
        ks.get_auto_recover = orig_auto


# ── Registration ────────────────────────────────────────────────────

def test_l29_is_registered():
    assert _get_test("L29") is not None


def test_l29_is_not_disruptive():
    """L-29 read-only version is disruptive=False. The
    actively-disruptive network-toggle version stays deferred."""
    assert _get_test("L29").disruptive is False


def test_l29_returns_valid_tuple():
    with _patch_kill_switch(list_states=[], auto_recover=True):
        res = _get_test("L29").fn(_StubContext())
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] in _LEVELS


# ── PASS branch ─────────────────────────────────────────────────────

def test_l29_pass_when_no_kill_states():
    with _patch_kill_switch(list_states=[], auto_recover=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.PASS
    assert "0 active kills" in detail


def test_l29_pass_with_inactive_states():
    """States may exist (history) without any being currently active."""
    with _patch_kill_switch(
            list_states=[{"tunnel_id": "t1", "killed": False}],
            auto_recover=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.PASS
    assert "1 state record" in detail


def test_l29_pass_reports_auto_recover_setting():
    with _patch_kill_switch(list_states=[], auto_recover=False):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.PASS
    assert "auto_recover=False" in detail


# ── FAIL branch ─────────────────────────────────────────────────────

def test_l29_fail_when_active_kill_state():
    with _patch_kill_switch(
            list_states=[{"tunnel_id": "tun1", "killed": True}],
            auto_recover=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.FAIL
    assert "tun1" in detail


def test_l29_fail_when_list_states_raises():
    with _patch_kill_switch(list_raises=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.FAIL
    assert "list_kill_states" in detail


def test_l29_fail_when_list_states_returns_non_list():
    # Patch to return a dict instead of list
    orig_list = ks.list_kill_states
    ks.list_kill_states = lambda: {"not": "a list"}
    try:
        level, detail = _get_test("L29").fn(_StubContext())
    finally:
        ks.list_kill_states = orig_list
    assert level == h.FAIL


# ── WARN branch ─────────────────────────────────────────────────────

def test_l29_warn_when_auto_recover_raises():
    """list_kill_states works but get_auto_recover doesn't — partial
    inspection is WARN, not FAIL."""
    with _patch_kill_switch(list_states=[], auto_raises=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.WARN
    assert "auto-recover" in detail


# ── Documentation / deferral note ──────────────────────────────────

def test_l29_pass_detail_mentions_deferred_active_probe():
    """The PASS verdict must explicitly note that the read-only check
    is NOT the full active-network-leak probe — so an operator
    doesn't mistake green here for proof the kill switch actually
    blocks leaks under load."""
    with _patch_kill_switch(list_states=[], auto_recover=True):
        level, detail = _get_test("L29").fn(_StubContext())
    assert level == h.PASS
    # Must reference that the disruptive probe is deferred
    assert ("deferred" in detail.lower()
            or "read-only" in detail.lower())
