"""Tests for lifecycle_automation (A5 sub-wave 1): toggle substrate + state machine.

Load-bearing assertions:
  * every automation DEFAULTS OFF (the safety floor);
  * the download-affecting mutators (auto_quarantine, auto_repair) stay OFF
    even with their toggle ON while the keystone is absent — the structural
    enforcement of the policy's "no auto-* until backup exists" ordering;
  * the state machine matches template_registry's reality (only `enabled` is
    usable; `quarantined` is not) and rejects illegal transitions.

Toggle state is simulated by reassigning module functions in the test body
(save/restore) rather than fixture machinery — same robustness choice as the
C-T2 body tests (a plain assignment behaves identically under pytest and the
custom run_tests.py runner).
"""
import contextlib

from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _toggles(on_set, keystone):
    """Simulate: toggles in `on_set` read True, all others False; keystone
    availability = `keystone`. Restores originals afterward."""
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: keystone
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


class TestTogglesDefaultOff:
    def test_all_toggles_default_off(self):
        # With nothing turned on, every automation resolves OFF.
        with _toggles(on_set=set(), keystone=False):
            for name in la.AUTOMATION_TOGGLES:
                assert la.is_enabled(name) is False, name

    def test_unknown_toggle_is_off(self):
        assert la.is_enabled("does_not_exist") is False

    def test_nonmutating_toggle_on_is_enabled_without_keystone(self):
        # L1-L3 don't affect downloads → toggle alone enables them.
        with _toggles(on_set={"drift_sweep", "validation_gate", "auto_flag"},
                      keystone=False):
            assert la.is_enabled("drift_sweep") is True
            assert la.is_enabled("validation_gate") is True
            assert la.is_enabled("auto_flag") is True

    def test_mutators_stay_off_without_keystone_even_if_toggled_on(self):
        # The whole point: toggle ON but keystone absent → still OFF.
        with _toggles(on_set={"auto_quarantine", "auto_repair"}, keystone=False):
            assert la.is_enabled("auto_quarantine") is False
            assert la.is_enabled("auto_repair") is False

    def test_mutators_enable_only_with_toggle_and_keystone(self):
        with _toggles(on_set={"auto_quarantine", "auto_repair"}, keystone=True):
            assert la.is_enabled("auto_quarantine") is True
            assert la.is_enabled("auto_repair") is True

    def test_keystone_alone_does_not_enable_mutators(self):
        # keystone present but toggle off → still OFF.
        with _toggles(on_set=set(), keystone=True):
            assert la.is_enabled("auto_quarantine") is False

    def test_live_default_is_off(self):
        # Against the real global_config (keys unset) the live default is OFF.
        # Post-keystone (sub-wave 2) keystone_available() is now True, so the
        # default-OFF guarantee for the mutators rests on the TOGGLE being unset
        # — verify that directly rather than relying on keystone absence.
        assert la.is_enabled("auto_quarantine") is False
        assert la.is_enabled("auto_repair") is False
        assert la._read_toggle("automation.auto_quarantine_enabled") is False

    def test_toggle_status_snapshot_shape(self):
        with _toggles(on_set={"auto_quarantine"}, keystone=False):
            snap = la.toggle_status()
        aq = snap["auto_quarantine"]
        assert aq["toggle_on"] is True          # raw toggle
        assert aq["effective"] is False         # keystone-gated off
        assert aq["keystone_gated"] is True


class TestStateMachine:
    def test_only_enabled_is_usable(self):
        assert la.is_usable(la.STATUS_ENABLED) is True
        for s in (la.STATUS_DISABLED, la.STATUS_QUARANTINED,
                  la.STATUS_REVIEWED, la.STATUS_CANDIDATE, None):
            assert la.is_usable(s) is False, s

    def test_quarantined_is_not_usable(self):
        # Mirrors template_registry: a quarantined template is never matched.
        assert la.is_usable(la.STATUS_QUARANTINED) is False

    def test_legal_transitions(self):
        assert la.can_transition(la.STATUS_ENABLED, la.STATUS_QUARANTINED)
        assert la.can_transition(la.STATUS_ENABLED, la.STATUS_DISABLED)
        assert la.can_transition(la.STATUS_QUARANTINED, la.STATUS_REVIEWED)
        assert la.can_transition(la.STATUS_REVIEWED, la.STATUS_ENABLED)
        assert la.can_transition(la.STATUS_DISABLED, la.STATUS_ENABLED)

    def test_noop_transition_is_legal(self):
        assert la.can_transition(la.STATUS_ENABLED, la.STATUS_ENABLED)

    def test_illegal_transitions_rejected(self):
        # Recovery from quarantine must go through review, never straight to enabled.
        assert la.can_transition(la.STATUS_QUARANTINED, la.STATUS_ENABLED) is False
        assert la.can_transition(la.STATUS_CANDIDATE, la.STATUS_ENABLED) is False
        assert la.can_transition(None, la.STATUS_ENABLED) is False

    def test_assert_transition_raises_on_illegal(self):
        raised = False
        try:
            la.assert_transition(la.STATUS_QUARANTINED, la.STATUS_ENABLED)
        except ValueError:
            raised = True
        assert raised, "illegal transition should raise ValueError"

    def test_assert_transition_passes_on_legal(self):
        la.assert_transition(la.STATUS_ENABLED, la.STATUS_QUARANTINED)  # no raise

    def test_needs_review_is_flag_not_status(self):
        # needs_review must NOT be a usability status — it's advisory.
        assert la.NEEDS_REVIEW_FLAG == "needs_review"
        assert la.NEEDS_REVIEW_FLAG not in la.ALL_STATUSES
