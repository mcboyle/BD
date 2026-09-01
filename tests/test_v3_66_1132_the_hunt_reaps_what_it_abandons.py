"""bd-wedge-hunt must not leave a remote pytest master running when it gives up.

BACKLOG 146. Five orphaned masters were found across the fleet at the end of the
~19h hunt on 2026-08-14, the oldest 41614s (11.6 HOURS), each a master plus up
to 48 idle workers. Load is this bug's dominant covariate -- reduced-size arms
are 0/73 against 15 of ~620 on full -- so an unreaped master progressively
corrupts the very measurement the hunt exists to take, in the direction that
INCREASES the apparent rate over time.

ROW 146's DIAGNOSIS WAS WRONG, AND THE CORRECTION IS THE POINT. It says the hunt
"captures forensics and sends SIGINT, but a master livelocked per row 145 is not
in a state that SIGINT unwinds, so the run simply stays". Read the code: the
WEDGE-CONFIRMED path sends SIGINT, waits `sigint_grace`, re-runs forensics, and
THEN sends `kill -9` to the process GROUP and the pid. That path is correct and
is not where the orphans came from. Diagnosing the one correct path as the
defect would have produced a fix that changed nothing.

THE ORPHANS COME FROM THE PATHS THAT ABANDON A RUN WITHOUT KILLING IT. Measured
by reading every terminal branch of the monitor loop at v3.66.1131:

  * INTERRUPT. `main` catches KeyboardInterrupt, sets STOP, and RETURNS. The
    host threads are `daemon=True`, so the interpreter kills them at exit: the
    remote run is never killed AND its row is never written. That is the whole
    of the five observed orphans, and it is why `rows.jsonl` contains ZERO
    abandoned rows -- they were not mis-recorded, they were never recorded.
  * --hours. Says "letting in-flight samples finish" and does the opposite: the
    monitor loop is `while not STOP.is_set()`, so it exits on the next tick and
    the row falls through to `setdefault("state", "COMPLETED")`. A run that was
    abandoned mid-flight is recorded as COMPLETED with no `pytest_exit`, which
    is a FALSE NEGATIVE in the wedge denominator. It never fired during the
    2026-08-14 hunt -- measured: 686 of 686 COMPLETED rows carry a real
    `pytest_exit` -- so no preserved row is contaminated. The defect is live
    regardless.
  * CAPPED. Kills the master pid ONLY. The wedge path three lines above kills
    the process GROUP. Same job, two branches, one of them leaves up to 48
    workers behind.
  * UNKNOWN. Records "the run was NOT killed and may still be there" and does
    not try. Honest, and still a leak.

WHY A STRUCTURAL TEST RATHER THAN A LIVE ONE. Driving the real abandon paths
needs a remote host, an ssh round trip and a wedged pytest master -- none of
which belongs in a band. These assertions read the tool's own source, which is
the same instrument `test_bd_ready_preflight.py` uses for the same reason, and
they are paired with a direct behavioural test of the one piece that CAN be
isolated: the command builder.

WHAT THIS FILE CANNOT SEE, stated because a gate that cannot say so is worse
than none: it does not prove a reap SUCCEEDS on a live host, only that every
abandon path issues one and that the command targets the group. A remote kill
that silently fails is outside its denominator.
"""

from __future__ import annotations

import ast
import builtins
import errno
import importlib.machinery
import importlib.util
import json
import math
import os
import pathlib
import re
import select
import shlex
import signal
import stat
import subprocess
import tempfile
import sys
import threading
import time

import pytest

# Its subject is one tool's source and one pure function in it, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# EVERY WALL-CLOCK BUDGET IN THIS FILE IS DERIVED FROM A MEASUREMENT.
# Backlog row 230, the half that v3.66.1222 left open.
#
# A BUDGET HAS TO CLEAR TWO HAZARDS AND THEY PULL IN OPPOSITE DIRECTIONS.
#   CEILING. A budget at or above the pytest-timeout bound governing its item
#     can never fire, so its own `except subprocess.TimeoutExpired` is dead
#     code and what runs instead is pytest-timeout killing the process. That
#     is what v3.66.1219 and v3.66.1222 fixed elsewhere. MEASURED HERE, on this
#     file, at this commit: of 127 constant budget sites, ZERO are at or above
#     240. This file never had that hazard, and saying so is part of the fix.
#   FLOOR. A budget below the real cost of the work fires on CORRECT work, and
#     at the assertion that is indistinguishable from the defect it exists to
#     catch. That hazard is LIVE here and it is the one row 230 recorded.
#     MEASURED on test5, one test per process, load 2.8-4.1:
#     test_partial_or_duplicate_release_frame_never_execs waits 6.993s against
#     a SEVEN SECOND budget -- a ratio of 1.00x. 39 of the 83 sites a passing
#     run reaches sat under 3x. The 2026-08-24 band saw four of them fire.
#
# SO THE RULE IS TWO-SIDED, and both sides are measured rather than assumed:
#
#     budget = max(prior_constant, _MIN_BUDGET_S,
#                  ceil(measured * _CONTENTION_FACTOR))
#     assert budget <= _GOVERNING_BOUND_S - _ITEM_RESERVE_S
#
# THE FLOOR IS ABSOLUTE AND THE FACTOR IS MULTIPLICATIVE, AND THAT IS NOT
# BELT-AND-BRACES -- the two failures measured on 2026-08-25 need different
# arithmetic. A wait dominated by REAL WORK stretches roughly in proportion to
# oversubscription, which is what the 4.13x above measures and what a factor
# describes. A wait dominated by SCHEDULING does not: the same afternoon,
# tests/test_v3_66_1209's `test_capture_reexecs_itself_when_handed_ignored_stop
# _signals` measured 1.26-1.80s across four matched runs and then consumed a
# NINETY SECOND budget under `-n 24` at load 10-17. That is 88 seconds of
# ABSOLUTE delay on 1.5 seconds of work, and no multiplier small enough to be
# useful describes it: 1.5s x 6 is 9s, while the budget it actually blew was
# already 60x its own cost. A cheap site is protected by an absolute floor or
# by nothing. (Stated plainly because it bounds the claim: this rule would
# have prevented every failure in THIS file -- 6.993s derives to 42s and the
# largest stretch anyone has measured is 4.13x -- and would NOT have prevented
# 1209's, whose budget was already 60x. A budget cannot tell slow from stuck;
# that one needs a diagnosis, not a number.)
#
# _CONTENTION_FACTOR IS MEASURED, NOT CHOSEN. Row 230's own reproduction shape
# -- three concurrent copies of this module, which is how the 2026-08-24
# failure was first seen -- was re-run at this commit. Per call site, the
# largest stretch from the idle-ish baseline to the contended run was 4.13x
# (median 1.00x, p90 1.16x, n=63). That maximum is RIGHT-CENSORED: a site that
# crossed its budget under contention recorded the budget, not its true cost,
# so the tail is longer than 4.13x, not shorter. 6.0 leaves ~1.5x over the
# largest stretch anyone has actually seen and clears the 3x floor the failing
# sites did not.
#
# THE PRIOR CONSTANT IS A FLOOR, NEVER A CEILING. A derivation may only ADD
# headroom. It never takes away headroom a previous author chose, because a
# baseline taken from n=1 observations is not strong enough to justify
# shrinking a bound that guards a real production contract.
#
# RAISING THESE COSTS NOTHING ON A PASSING RUN. `proc.wait(timeout=B)` returns
# the moment the child exits; B is a ceiling, not a sleep. Every site in this
# file converts expiry into a failure -- there is no site that expects to
# expire -- so a larger B changes only how long a genuinely stuck run burns
# before it fails, and every item's total stays far under the governing bound.
#
# THE TABLE IS KEYED PER CALL SITE. v3.66.1222 found its baseline wrong by 8x
# because `_MEASURED_S` was keyed by TOOL when the cost is a property of the
# INVOCATION: one `bd-mutation-test` key described a 2s selftest and a 201s
# row. The same shape is here -- `subprocess.run` costs 0.13s at one site and
# 6.99s at another, and `_w1_await_fifo` spans 0.000s to 4.398s across its call
# sites -- so a per-callee key would be the same mistake. Keys are
# (function, callee, ordinal) and deliberately NOT line numbers, so an
# unrelated edit does not churn the table.
#
# WHERE A SHARED DEFAULT SURVIVED, THE SPREAD WAS MEASURED FIRST.
# `_w1_wait_for_gate` runs 0.304-0.792s across 24 call sites and
# `_w1_release_fifo` is under a millisecond at all 8, so one key describes them
# honestly. `_w1_await_fifo` (0.000-4.398s) and `_w1_wait_for_exit`
# (0.712-7.123s, and 7.123s against its 20s watchdog is 2.8x) are NOT narrow,
# so their shared default is retired and every call site names its own.
#
# THE CLEANUP REAP IS THE ONE THING A PASSING RUN CANNOT MEASURE. Every
# `finally` here ends with `if proc.poll() is None: proc.kill(); proc.wait(...)`
# and on a passing run the body already collected the child, so the branch is
# never taken. Its cost is therefore measured DIRECTLY rather than guessed:
# 200 SIGKILL-and-reap cycles on a 5-process group at load 5.7-6.6 gave a
# maximum of 1.5ms, and the one such site the contended run did reach recorded
# 3.5ms. `_CLEANUP_REAP_S` is that measurement.
#
# AND THE BASELINE POLICES ITSELF, because nothing but the run knows how long
# the run takes and 1222's table was wrong by 8x precisely because nobody
# re-measured it. `_w1_police` asserts every completed wait against its own
# recorded cost, so a site that outgrows its baseline says so while there is
# still headroom instead of by crossing its budget under load. v3.66.1219's
# first over-sensitivity control was VACUOUS -- it compared baselines only to
# each other, so restating 168s as 7s stayed green. This one compares a
# baseline to the clock.
# ---------------------------------------------------------------------------

_GOVERNING_BOUND_S = 240.0
_ITEM_RESERVE_S = 30.0
_CONTENTION_FACTOR = 6.0
_MIN_BUDGET_S = 30.0
_WARN_FACTOR = 3.0
_WARN_FLOOR_S = 30.0
#: Above this many seconds, a wait is long enough that scheduling noise cannot
#: explain it, so the recorded baseline is compared WITHOUT the warning floor.
#: 5s sits above every sub-second site in the table and below the 6.8s-6.9s
#: cluster that made this file's budgets a knife edge.
_POLICE_ABSOLUTE_S = 5.0
_CLEANUP_REAP_S = 0.0035

# key -> (measured seconds, prior constant this replaced)
_MEASURED_S = {
    "_capture_outer_receipt/run":                                                (0.1318, 10),
    "_w1_readlink_when_installed/default":                                       (0.0054, 5.0),
    "_w1_release_fifo/default":                                                  (0.0002, 10.0),
    "_w1_run_registration_probe/run":                                            (0.1093, 5),
    "_w1_wait_for_exit/communicate":                                             (0.0314, 5),
    "_w1_wait_for_gate/default":                                                 (0.7916, 5.0),
    "_w1_wait_for_path/default":                                                 (3.1513, 5.0),
    "fixture_marker_waits_for_content/fifo":                                     (0.1000, 0),
    "fixture_marker_waits_for_content/wait":                                     (0.1000, 0),
    "_w1_budget_boundary/reap-wait":                                             (0.0035, 5),
    "hunt_fixture_reaps_the_exact_process_group_it_spawned/wait":                (0.0035, 5),
    "hunt_fixture_reaps_the_exact_process_group_it_spawned/wait-2":              (0.0035, 5),
    "hunt_fixture_does_not_report_a_self_exited_child_as_leaked/wait":            (0.1000, 5),
    "a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/readlink":  (0.0054, 10.0),
    "a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/readlink-2":(0.0003, 10.0),
    "a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/wait":      (0.0033, 5),
    "a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/wait-2":    (0.0638, 5),
    "a_registrar_that_succeeds_still_gets_waited_for_and_recorded/wait":         (2.8743, 40.0),
    "a_registrar_that_succeeds_still_gets_waited_for_and_recorded/wait-2":       (0.0035, 10),
    "an_absence_claim_is_immune_to_a_live_neighbour_group/wait":                 (0.0160, 0),
    "an_absence_claim_is_immune_to_a_live_neighbour_group/wait-2":               (0.0035, 0),
    "a_non_descendant_group_member_fails_the_probe_closed_not_open/wait":       (0.0158, 0),
    "a_slow_settlement_never_downgrades_a_decided_cancellation/wait":           (6.8895, 0),
    "_w1_observation_timeout/run":                                               (0.0095, 0),
    "a_slow_owner_observation_never_downgrades_a_correct_run/run":              (7.9826, 0),
    "the_recorded_observation_cost_matches_a_real_observation/wait":            (0.2650, 0),
    "the_recorded_observation_cost_matches_a_real_observation/wait-2":          (0.0035, 0),
    "the_observation_floor_still_bounds_an_observation_that_will_not_answer/run": (6.3104, 0),
    "a_slow_settlement_never_downgrades_a_decided_cancellation/wait-2":         (0.0035, 0),
    "abort_timeout_retains_inert_gate_under_one_budget/exit":                    (5.4935, 20.0),
    "abort_timeout_retains_inert_gate_under_one_budget/wait":                    (0.0035, 5),
    "cancellation_after_relay_before_gate_settles_the_acquired_owner/fifo":      (0.2102, 5),
    "cancellation_after_relay_before_gate_settles_the_acquired_owner/wait":      (1.2187, 15),
    "cancellation_during_delayed_ready_preserves_primary/wait":                  (2.3226, 6),
    "cancellation_during_delayed_ready_preserves_primary/wait-2":                (0.0035, 5),
    "cancellation_during_gate_settlement_wait_preserves_primary/exit":           (0.7124, 20.0),
    "cancellation_during_gate_settlement_wait_preserves_primary/fifo":           (4.6094, 10),
    "cancellation_during_gate_settlement_wait_preserves_primary/wait":           (0.0035, 5),
    "cancellation_during_group_observer_forbids_registration/fifo":              (1.8703, 5.0),
    "cancellation_during_group_observer_forbids_registration/wait":              (2.3724, 8),
    "cancellation_during_group_observer_forbids_registration/wait-2":            (0.0035, 5),
    "cancellation_during_pre_register_observation_never_registers/wait":         (2.3225, 6),
    "cancellation_during_pre_register_observation_never_registers/wait-2":       (0.0035, 5),
    "cancellation_during_reconciliation_retains_primary_and_reaps_owner/fifo":   (5.5805, 10),
    "cancellation_during_reconciliation_retains_primary_and_reaps_owner/wait":   (1.3186, 12),
    "cancellation_during_reconciliation_retains_primary_and_reaps_owner/wait-2": (0.0035, 5),
    "cancellation_during_terminal_reader_reconciles_exact_id_once/fifo":         (4.1464, 10),
    "cancellation_during_terminal_reader_reconciles_exact_id_once/wait":         (2.8746, 10),
    "cancellation_during_terminal_reader_reconciles_exact_id_once/wait-2":       (0.0035, 5),
    "cancellation_during_terminal_relay_wait_reconciles_once/fifo":              (4.4142, 10),
    "cancellation_during_terminal_relay_wait_reconciles_once/wait":              (1.9212, 10),
    "cancellation_during_terminal_relay_wait_reconciles_once/wait-2":            (0.0035, 5),
    "completed_owner_forged_ready_receipt_never_grants_census_authority/fifo":   (4.0418, 5.0),
    "completed_owner_forged_ready_receipt_never_grants_census_authority/wait":   (0.0642, 8),
    "completed_owner_forged_ready_receipt_never_grants_census_authority/wait-2": (0.0035, 5),
    "completed_owner_ready_receipt_censuses_live_descendant/fifo":               (4.3981, 5.0),
    "completed_owner_ready_receipt_censuses_live_descendant/fifo-2":             (0.1033, 5.0),
    "completed_owner_ready_receipt_censuses_live_descendant/wait":               (0.4154, 8),
    "completed_owner_ready_receipt_censuses_live_descendant/wait-2":             (0.0035, 5),
    "completed_owner_ready_receipt_remains_census_authority/fifo":               (3.8111, 5.0),
    "completed_owner_ready_receipt_remains_census_authority/wait":               (1.9217, 8),
    "completed_owner_ready_receipt_remains_census_authority/wait-2":             (0.0035, 5),
    "cooperative_registered_cancellation_returns_primary_status/run":            (7.1382, 10),
    "delayed_extra_ready_frame_never_reaches_the_registrar/fifo":                (0.0329, 5.0),
    "delayed_extra_ready_frame_never_reaches_the_registrar/wait":                (2.3722, 6),
    "delayed_extra_ready_frame_never_reaches_the_registrar/wait-2":              (0.0035, 5),
    "descendant_census_is_itself_a_named_checked_owner/run":                     (5.6979, 8),
    "every_authority_helper_is_named_and_checked_waited/run":                    (5.7907, 8),
    "exit_guard_settles_a_post_setsid_owner_after_nounset/communicate":          (1.9372, 15),
    "exit_guard_settles_a_post_setsid_owner_after_nounset/fifo":                 (0.3418, 5.0),
    "exit_guard_settles_a_post_setsid_owner_after_nounset/wait":                 (0.0035, 5),
    "failed_registration_never_releases_the_workload/wait":                      (3.7783, 8),
    "failed_registration_never_releases_the_workload/wait-2":                    (0.0035, 5),
    "failed_workload_wait_reconciles_registered_id/run":                         (6.1023, 7),
    "gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/fifo":     (3.3633, 5.0),
    "gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/wait":     (3.5269, 6),
    "gate_control_is_anonymous_and_registrar_inherits_no_authority_fd/wait-2":   (0.0035, 5),
    "gate_exec_failure_is_named_registered_handoff_failure/run":                 (6.825, 7),
    "gate_ready_is_exact_terminal_admission/communicate":                        (0.0035, 5),
    "gate_ready_is_exact_terminal_admission/exit":                               (2.6868, 20.0),
    "gate_ready_is_exact_terminal_admission/fifo":                               (0.0042, 30.0),
    "gate_ready_is_exact_terminal_admission/fifo-2":                             (0.0011, 30.0),
    "gate_ready_is_exact_terminal_admission/marker":                             (0.2352, 15.0),
    "gate_receipt_admission_rejects_wrong_provenance/run":                       (4.4416, 6),
    "handoff_eof_is_not_exec_success/communicate":                               (0.0035, 5),
    "handoff_eof_is_not_exec_success/exit":                                      (6.9759, 20.0),
    "handoff_timeout_retains_registered_id_under_one_budget/exit":               (6.329, 20.0),
    "handoff_timeout_retains_registered_id_under_one_budget/wait":               (0.0035, 5),
    "invalid_exec_ok_reconciles_registered_id_without_waiting_live_group/wait":  (6.238, 8),
    "invalid_exec_ok_reconciles_registered_id_without_waiting_live_group/wait-2":(0.0035, 5),
    "live_duplicate_ready_frame_never_reaches_the_registrar/run":                (2.9017, 6),
    "live_wrong_ready_frame_never_reaches_the_registrar/run":                    (0.9896, 15),
    "malformed_owner_ready_reaps_its_term_resistant_group/run":                  (2.7919, 8),
    "monotonic_clock_rollback_fails_closed_without_extending_budget/fifo":       (0.2184, 5.0),
    "monotonic_clock_rollback_fails_closed_without_extending_budget/wait":       (0.0641, 5),
    "monotonic_clock_rollback_fails_closed_without_extending_budget/wait-2":     (0.0035, 5),
    "nul_bearing_ready_frame_never_reaches_the_registrar/run":                   (2.824, 6),
    "nul_bearing_terminal_frame_is_not_exec_success/communicate":                (0.0035, 5),
    "nul_bearing_terminal_frame_is_not_exec_success/exit":                       (6.6857, 20.0),
    "one_second_lifecycle_cap_remains_truthfully_unknown/run":                   (1.791, 7),
    "partial_coproc_setup_settles_every_acquired_owner/run":                     (2.1835, 8),
    # ROW 325: 8.2455s with the measured 9s partial-frame expiry control.
    "partial_handoff_frame_does_not_restart_the_protocol_budget/exit":           (8.2455, 20.0),
    "partial_handoff_frame_does_not_restart_the_protocol_budget/fifo":           (4.0875, 10),
    "partial_handoff_frame_does_not_restart_the_protocol_budget/wait":           (0.0035, 5),
    "partial_handoff_frame_does_not_restart_the_protocol_budget/wait-2":         (0.0035, 5),
    "partial_or_duplicate_release_frame_never_execs/run":                        (6.993, 7),
    "post_ready_protocol_budget_is_positive_and_reaches_terminal_frame/run":     (6.9112, 10),
    "pre_observation_cancellation_settles_only_in_the_top_shell/run":            (4.6779, 8),
    "pre_ready_descendant_refuses_registration/wait":                            (4.3299, 6),
    "pre_ready_descendant_refuses_registration/wait-2":                          (0.0035, 5),
    "pytest_pid_publish_failure_is_settled_without_partial_target/run":          (2.0661, 8),
    "real_release_sigpipe_is_contained_and_enters_registered_failure/fifo":      (3.9774, 5.0),
    "real_release_sigpipe_is_contained_and_enters_registered_failure/wait":      (4.1293, 8),
    "real_release_sigpipe_is_contained_and_enters_registered_failure/wait-2":    (0.0035, 5),
    "reap_cmd_actually_kills_a_real_process_GROUP/run":                          (0.4965, 60),
    "reap_cmd_actually_kills_a_real_process_GROUP/wait":                         (0.0001, 10),
    "reap_cmd_can_still_report_a_survivor/run":                                  (0.1412, 60),
    "reconciliation_term_resistance_stays_inside_total_budget/fifo":             (5.7632, 10),
    "reconciliation_term_resistance_stays_inside_total_budget/wait":             (2.1214, 7),
    "reconciliation_term_resistance_stays_inside_total_budget/wait-2":           (0.0035, 5),
    "registrar_success_requires_one_exact_job_id_before_release/run":            (6.0278, 7),
    "registration_cleanup_timeout_is_internal_and_names_retained_group/run":     (5.2738, 7),
    "registration_failure_never_signals_after_original_child_disappears/fifo":   (0.0001, 5.0),
    "registration_failure_never_signals_after_original_child_disappears/wait":   (3.4765, 6),
    "registration_failure_never_signals_after_original_child_disappears/wait-2": (0.0035, 5),
    "registration_failure_never_signals_changed_process_identity/wait":          (4.329, 6),
    "registration_failure_never_signals_changed_process_identity/wait-2":        (0.0013, 5),
    "registration_failure_never_signals_changed_process_identity/wait-3":        (0.0035, 5),
    "registration_receipt_drift_before_go_refuses_release/wait":                 (6.8905, 7),
    "registration_receipt_drift_before_go_refuses_release/wait-2":               (0.0035, 5),
    "registration_release_failure_is_bounded_and_classified/wait":               (6.4874, 8),
    "registration_release_failure_is_bounded_and_classified/wait-2":             (0.0035, 5),
    "release_sigpipe_handler_is_scoped_and_restored_after_write/run":            (5.7169, 8),
    "release_write_error_is_resolved_only_by_gate_status/communicate":           (0.0035, 5),
    "release_write_error_is_resolved_only_by_gate_status/exit":                  (7.1227, 20.0),
    "runner_cancellation_closes_gate_and_does_not_start_workload/wait":          (2.3726, 7),
    "runner_cancellation_closes_gate_and_does_not_start_workload/wait-2":        (0.0035, 5),
    "signal_receipt_refuses_starttime_drift_before_pidfd_signal/fifo":           (0.0897, 5.0),
    "signal_receipt_refuses_starttime_drift_before_pidfd_signal/run":            (0.1335, 10),
    "signal_receipt_refuses_starttime_drift_before_pidfd_signal/wait":           (0.0074, 5),
    "successful_registration_releases_exact_command_and_status/wait":            (1.6197, 8),
    "successful_registration_releases_exact_command_and_status/wait-2":          (0.0035, 5),
    "term_resistant_observer_stays_inside_gate_budget/fifo":                     (1.6607, 10),
    # ROW 325: this site now exercises the shipped 10s forward deadline rather
    # than the old fixture-only 2s clock. Measured on test5 at load 26.2.
    "term_resistant_observer_stays_inside_gate_budget/wait":                    (10.6006, 7),
    "term_resistant_observer_stays_inside_gate_budget/wait-2":                   (0.0035, 5),
    "terminal_frame_without_eof_never_enters_an_unbounded_child_wait/exit":      (6.2437, 20.0),
    "terminal_frame_without_eof_never_enters_an_unbounded_child_wait/wait":      (0.0035, 5),
    "terminal_relay_wait_failure_reconciles_registered_id/run":                  (2.3323, 7),
    "the_presence_probe_costs_the_group_and_not_the_host/wait":                 (0.0157, 0),
    "the_settlement_deadline_still_bounds_a_gate_that_will_not_settle/wait":    (0.4157, 0),
    "the_settlement_deadline_still_bounds_a_gate_that_will_not_settle/wait-2":  (0.0035, 0),
    "the_budget_boundary_followed_the_tests_into_this_file/wait":            (0.0530, 0),
    "the_cheap_presence_probe_agrees_with_the_complete_census/wait":             (0.0155, 5),
    "the_group_census_never_forks_and_so_cannot_count_itself/wait":              (0.0154, 5),
    "unreadable_group_absence_probe_never_grants_status_91/run":                 (4.7896, 6),
    "vanished_gate_leader_with_live_descendant_is_retained_unknown/fifo":        (1.6766, 5.0),
    "vanished_gate_leader_with_live_descendant_is_retained_unknown/wait":        (1.0181, 6),
    "vanished_gate_leader_with_live_descendant_is_retained_unknown/wait-2":      (0.0035, 5),
    "wedge_hunt_does_not_wait_on_a_job_it_could_not_register/wait":              (1.7706, 6),
    "wedge_hunt_does_not_wait_on_a_job_it_could_not_register/wait-2":            (0.0035, 5),
    "withheld_owner_ready_reaps_post_setsid_term_resistant_group/fifo":          (0.2936, 5),
    "withheld_owner_ready_reaps_post_setsid_term_resistant_group/wait":          (3.1245, 8),
    "withheld_owner_ready_reaps_post_setsid_term_resistant_group/wait-2":        (0.0035, 5),
    "zero_owner_kill_grace_is_rejected_before_timeout_launch/run":               (0.3019, 7),
}


# EVERY SPAWN OF THE BUILT RUNNER PINS ITS WORKING DIRECTORY. The production
# RUNNER template shells out to `git rev-parse --short HEAD`, so a spawn that
# inherits the pytest worker's directory is asserting over ambient state -- the
# exact isolation A7 names alongside HOME, TMPDIR and module globals. One band
# spawn returned rc=93 with `fatal: not a git repository`; that event is
# recorded as an open flake on row 241 and is NOT claimed to be fixed here.
# What is fixed is that the directory is now DETERMINISTIC instead of inherited.
_W1_SPAWN_CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class _Budget(float):
    """A budget that remembers which call site it was derived for.

    It is a float everywhere it is used -- `subprocess` does arithmetic on it,
    `select.select` takes it -- and it carries its site so the instrumented
    boundary below can police the elapsed time without every one of 148 call
    sites having to be restructured into a wrapper.
    """

    __slots__ = ("site",)

    def __new__(cls, value, site):
        self = float.__new__(cls, value)
        self.site = site
        return self


def _w1_budget_s(site):
    """The wall-clock budget for ONE call site, derived from its measurement."""
    measured, prior = _MEASURED_S[site]
    derived = math.ceil(measured * _CONTENTION_FACTOR)
    # THE FLOOR IS ABSOLUTE AND THE FACTOR IS MULTIPLICATIVE, because the two
    # failure shapes measured on 2026-08-25 need different arithmetic. See the
    # note above _MEASURED_S.
    value = float(max(prior, _MIN_BUDGET_S, derived))
    # THE CEILING, ASSERTED RATHER THAN ASSUMED. Row 230 asks for a budget
    # "provably below the governing pytest timeout"; this is where it is proved,
    # per budget, at the moment it is handed out.
    assert value <= _GOVERNING_BOUND_S - _ITEM_RESERVE_S, (
        "budget %.0fs for site %r is not subordinate to the %.0fs bound "
        "governing its item (reserve %.0fs). A budget that cannot fire before "
        "the bound has a dead except clause and pytest-timeout kills the "
        "worker instead." % (value, site, _GOVERNING_BOUND_S, _ITEM_RESERVE_S))
    return _Budget(value, site)


def _w1_warn_s(site):
    measured, _prior = _MEASURED_S[site]
    return max(_WARN_FLOOR_S, measured * _WARN_FACTOR)


_W1_POLICED: list = []


def _w1_police(timeout, elapsed):
    """Assert a COMPLETED wait stayed inside its recorded cost.

    Only the success path reaches here. On expiry the TimeoutExpired must
    propagate untouched -- policing there would replace the real failure with
    this one and mask exactly what the budget exists to report.
    """
    site = getattr(timeout, "site", None)
    if site is None:
        return
    # A control that never runs is decoration. The counter is what the
    # precondition test below reads to prove the instrument is live.
    _W1_POLICED.append(site)
    limit = _w1_warn_s(site)
    measured, _prior = _MEASURED_S[site]
    # THE WARNING MUST NOT INHERIT THE BUDGET'S SCHEDULING FLOOR.
    #
    # `_w1_warn_s` is max(30s, measured x 3). The 30s term is there so a
    # sub-second site does not warn every time the box hiccups -- correct for
    # NOISE, and blinding for the case this assertion exists to catch. Measured
    # at v3.66.1226: restating a site's baseline from 6.8905s to 0.5s -- a 14x
    # understatement, the v3.66.1219 vacuity shape -- left this gate GREEN,
    # because 6.89s is under 30s no matter what the table claims. The mutant
    # ESCAPED.
    #
    # So a second, unfloored comparison runs whenever the elapsed time is large
    # in ABSOLUTE terms. Below _POLICE_ABSOLUTE_S the floor still suppresses
    # noise; above it, a wait that exceeds its recorded baseline by the stated
    # margin is a stale table entry and says so. A restated 0.5s baseline now
    # gives 1.5s against a real 6.89s and fails.
    if elapsed > _POLICE_ABSOLUTE_S and measured > 0:
        assert elapsed <= measured * _WARN_FACTOR, (
            "site %r took %.2fs against a recorded baseline of %.4fs -- %.1fx, "
            "past the %.1fx margin, on a wait long enough (>%.0fs) that "
            "scheduling noise does not explain it. The TABLE is stale or wrong; "
            "re-measure the site. This check deliberately ignores the %.0fs "
            "warning floor, which exists to silence sub-second noise and "
            "otherwise hides exactly this."
            % (site, elapsed, measured, elapsed / measured, _WARN_FACTOR,
               _POLICE_ABSOLUTE_S, _WARN_FLOOR_S))
    assert elapsed <= limit, (
        "site %r took %.2fs against a recorded baseline of %.4fs (limit "
        "%.2fs = max(%.1fs, %.1fx)). Re-measure it on an idle host and update "
        "_MEASURED_S; do not widen _CONTENTION_FACTOR to hide it."
        % (site, elapsed, measured, limit, _WARN_FLOOR_S, _WARN_FACTOR))


@pytest.fixture(autouse=True)
def _w1_budget_boundary(monkeypatch):
    """Instrument the wait boundary for the life of one item.

    CLAUDE.md A7: to ask what a resource costs, instrument the resource
    boundary. `subprocess.run` delegates to `Popen.communicate`, so wrapping
    `run`, `wait` and `communicate` with a re-entrancy guard records exactly
    one elapsed time per source call site.
    """
    depth = {"n": 0}
    real_run = subprocess.run
    real_popen_init = subprocess.Popen.__init__
    real_wait = subprocess.Popen.wait
    real_communicate = subprocess.Popen.communicate
    spawned = []
    cleanup = {"tracked": 0, "reaped": 0, "settled": 0, "unknown": 0}

    def tracked_init(process, *args, **kwargs):
        real_popen_init(process, *args, **kwargs)
        try:
            receipt = _w1_proc_receipt(process.pid)
        except (FileNotFoundError, ProcessLookupError, OSError):
            receipt = None
        spawned.append((process, receipt))

    def timed(orig, get_timeout):
        def wrapper(*args, **kwargs):
            if depth["n"]:
                return orig(*args, **kwargs)
            depth["n"] = 1
            started = time.monotonic()
            try:
                result = orig(*args, **kwargs)
            finally:
                depth["n"] = 0
            _w1_police(get_timeout(args, kwargs), time.monotonic() - started)
            return result
        return wrapper

    monkeypatch.setattr(subprocess, "run",
                        timed(real_run, lambda a, k: k.get("timeout")))
    monkeypatch.setattr(subprocess.Popen, "__init__", tracked_init)
    monkeypatch.setattr(subprocess.Popen, "wait",
                        timed(real_wait,
                              lambda a, k: k.get("timeout",
                                                 a[1] if len(a) > 1 else None)))
    monkeypatch.setattr(subprocess.Popen, "communicate",
                        timed(real_communicate,
                              lambda a, k: k.get("timeout",
                                                 a[2] if len(a) > 2 else None)))
    try:
        yield cleanup
    finally:
        cleanup["tracked"] = len(spawned)
        for process, receipt in spawned:
            if process.poll() is not None:
                cleanup["settled"] += 1
                continue

            owns_group = receipt is not None and receipt[1] == process.pid
            try:
                if owns_group:
                    os.killpg(receipt[1], signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                process.wait(timeout=_w1_budget_s(
                    "_w1_budget_boundary/reap-wait"))
            except (subprocess.TimeoutExpired, OSError):
                cleanup["unknown"] += 1
                continue

            group_gone = True
            if owns_group:
                try:
                    os.killpg(receipt[1], 0)
                except ProcessLookupError:
                    group_gone = True
                except OSError as exc:
                    group_gone = exc.errno == errno.ESRCH
                else:
                    group_gone = False
            if process.poll() is not None and group_gone:
                cleanup["reaped"] += 1
            else:
                cleanup["unknown"] += 1

        assert cleanup["unknown"] == 0, (
            "HUNT FIXTURE CLEANUP UNKNOWN: "
            f"tracked={cleanup['tracked']} reaped={cleanup['reaped']} "
            f"settled={cleanup['settled']} unknown={cleanup['unknown']}"
        )
        assert cleanup["tracked"] == cleanup["reaped"] + cleanup["settled"], (
            "hunt fixture cleanup denominator did not reconcile: "
            f"{cleanup!r}"
        )


def _w1_exact_receipt_is_present(receipt):
    try:
        return _w1_proc_receipt(receipt[0]) == receipt
    except (FileNotFoundError, ProcessLookupError):
        return False


def test_hunt_fixture_reaps_the_exact_process_group_it_spawned():
    """RED for row 468: fixture teardown used to abandon this live group."""
    inner_patch = pytest.MonkeyPatch()
    boundary = _w1_budget_boundary.__wrapped__(inner_patch)
    cleanup = next(boundary)
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import time; print('READY', flush=True); time.sleep(300)"],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    receipt = None
    try:
        assert proc.stdout.readline() == "READY\n", (
            "the abandoned-child fixture never reached its live marker"
        )
        receipt = _w1_proc_receipt(proc.pid)
        assert receipt[1] == proc.pid, (
            "the fixture did not create the independent process group it claims"
        )
        assert _w1_exact_receipt_is_present(receipt), (
            "the exact child did not exist before fixture teardown"
        )

        next(boundary, None)
        survivors = int(_w1_exact_receipt_is_present(receipt))
        assert survivors == 0, (
            "HUNT FIXTURE ABANDONED ITS LIVE CHILD: "
            f"pid={proc.pid} survivors={survivors} reaped=0"
        )
        assert cleanup["tracked"] == 1
        assert cleanup["reaped"] == 1, (
            "fixture teardown did not report the exact nonzero reap count"
        )
        assert cleanup["unknown"] == 0
    finally:
        if receipt is not None and _w1_exact_receipt_is_present(receipt):
            os.killpg(proc.pid, signal.SIGKILL)
        try:
            proc.wait(timeout=_w1_budget_s(
                "hunt_fixture_reaps_the_exact_process_group_it_spawned/wait"))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=_w1_budget_s(
                "hunt_fixture_reaps_the_exact_process_group_it_spawned/wait-2"))
        inner_patch.undo()


def test_hunt_fixture_does_not_report_a_self_exited_child_as_leaked():
    """Negative control: settled children stay out of the reaped denominator."""
    inner_patch = pytest.MonkeyPatch()
    boundary = _w1_budget_boundary.__wrapped__(inner_patch)
    cleanup = next(boundary)
    proc = subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(0)"],
        start_new_session=True,
    )
    try:
        assert proc.wait(timeout=_w1_budget_s(
            "hunt_fixture_does_not_report_a_self_exited_child_as_leaked/wait")) == 0
        assert proc.returncode == 0, "the negative-control child did not self-exit"
        assert not pathlib.Path("/proc", str(proc.pid), "stat").exists()
        next(boundary, None)
        assert cleanup["tracked"] == 1
        assert cleanup["reaped"] == 0
        assert cleanup["settled"] == 1
        assert cleanup["unknown"] == 0
    finally:
        inner_patch.undo()


HUNT = REPO / "toolchain" / "bin" / "bd-wedge-hunt"


def _load():
    """Import the extensionless, python-shebang tool as a module.

    `git ls-files -- '*.py'` cannot see this file and neither can a plain
    import; CLAUDE.md section 1 is about exactly this population.
    """
    spec = importlib.util.spec_from_loader(
        "bd_wedge_hunt_under_test",
        importlib.machinery.SourceFileLoader("bd_wedge_hunt_under_test", str(HUNT)),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source() -> str:
    return HUNT.read_text(encoding="utf-8")


def _string_constants() -> list[str]:
    """Every string LITERAL in the tool, with comments structurally excluded.

    A comment is inside the denominator of every gate that reads source text,
    and CLAUDE.md section 0 records four separate times an assertion in this
    repo could not tell prose from code -- including one where the comment
    written to explain a removal re-created the thing it described. This file
    is full of prose naming the very markers it asserts on, so it must not
    grep raw source. Comments never reach the AST, so reading literals out of
    it fixes the denominator for free.
    """
    return [n.value for n in ast.walk(ast.parse(_source()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_the_tool_exists_and_parses():
    """PRECONDITION. Without it, every assertion below is vacuous on a typo."""
    assert HUNT.is_file(), f"no bd-wedge-hunt at {HUNT}"
    ast.parse(_source())


def test_it_imports_without_side_effects():
    """PRECONDITION for the behavioural test: the module must be importable."""
    mod = _load()
    assert hasattr(mod, "STOP"), "module loaded but does not look like the hunt"


def test_a_reap_command_builder_exists_and_targets_the_process_GROUP():
    """The one piece testable in isolation, asserted behaviourally.

    A master is `setsid`-launched, so its workers share its process group. A
    kill aimed at the pid alone leaves up to 48 workers running -- which is the
    CAPPED path's bug. The builder must aim at the group.
    """
    mod = _load()
    assert hasattr(mod, "reap_cmd"), (
        "bd-wedge-hunt has no reap_cmd(). Every path that abandons a remote run "
        "needs ONE shared way to kill it; four branches hand-rolling their own "
        "is how the CAPPED path ended up killing the master and leaving its 48 "
        "workers. Backlog 146."
    )
    cmd = mod.reap_cmd("/private/run/runner.receipt")
    assert "/private/run/runner.receipt" in cmd, (
        "the builder ignored the durable receipt it was given")
    assert "pidfd_send_signal" in cmd and "owned_census" in cmd, (
        "reap_cmd does not bind signals to exact process identities and census "
        "the whole owned context")
    assert "SIGTERM" in cmd and "SIGKILL" in cmd and "monotonic_ns" in cmd


def _capture_outer_receipt(mod, proc, root: pathlib.Path, run_id: str):
    receipt = root / "runner.receipt"
    captured = subprocess.run(
        [os.environ.get("PYTHON", "python3"), "-c",
         mod.PROCESS_GUARD_PROGRAM, "capture", str(proc.pid), str(receipt),
         run_id, str(root), str(os.getpid())],
        capture_output=True, text=True, timeout=_w1_budget_s("_capture_outer_receipt/run"),
    )
    assert captured.returncode == 0, (captured.stdout, captured.stderr)
    assert "RECEIPT-OK" in captured.stdout and receipt.is_file()
    return receipt


def test_reap_cmd_actually_kills_a_real_process_GROUP(tmp_path):
    """THE SEAM, DRIVEN. A structural test cannot tell a correct kill from a
    plausible-looking one, and this is how that mattered:

    the first version of `reap_cmd` used `kill -0` for its liveness check and
    reported REAP-SURVIVED after successfully killing all five processes of a
    real tree -- because `kill -0` succeeds on a ZOMBIE, and a master whose
    parent has not wait()ed yet is exactly that. A gate firing on correct work
    gets switched off (section 0), so this arm exists to keep the verdict
    honest, not only the kill.

    Shape matched to production: `setsid` so the tree has its own process group,
    which is what the hunt's runner does.
    """
    mod = _load()
    run_id = os.urandom(16).hex()
    env = dict(os.environ)
    env["BD_WEDGE_RUN_ID"] = run_id
    proc = subprocess.Popen(
        ["bash", "-c", "sleep 300 & sleep 300 & sleep 300 & sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env)
    try:
        receipt = _capture_outer_receipt(mod, proc, tmp_path, run_id)
        pgid = os.getpgid(proc.pid)

        def live_in_group():
            r = subprocess.run(["ps", "-eo", "pgid=,pid=,stat="],
                               capture_output=True, text=True)
            rows = [l.split() for l in r.stdout.splitlines() if l.split()]
            return [x for x in rows
                    if x[0] == str(pgid) and not x[2].startswith("Z")]

        before = live_in_group()
        # PRECONDITION: assert the fixture built the shape before judging it.
        # Without this, "nothing survived" and "nothing was ever there" are the
        # same green -- CLAUDE.md section 6.
        assert len(before) >= 4, (
            f"the fixture did not build a process group to kill (found "
            f"{len(before)}); this test would otherwise pass vacuously")

        out = subprocess.run(["bash", "-c", mod.reap_cmd(str(receipt), term_grace=0.05)],
                             capture_output=True, text=True, timeout=_w1_budget_s("reap_cmd_actually_kills_a_real_process_GROUP/run"))
        after = live_in_group()

        assert not after, (
            f"{len(after)} process(es) survived the reap. Killing the master "
            "alone leaves its workers -- the CAPPED path's bug, backlog 146.")
        assert out.stdout.startswith("REAP-OK ") \
            and "census=ABSENT" in out.stdout \
            and "failures=NONE" in out.stdout, (
            f"the group WAS killed but reap_cmd reported {out.stdout.strip()!r}. "
            "A false SURVIVED is a gate firing on correct work: `kill -0` "
            "succeeds on a zombie, so the liveness probe must read the process "
            "STATE and treat Z as gone.")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=_w1_budget_s("reap_cmd_actually_kills_a_real_process_GROUP/wait"))
        except subprocess.TimeoutExpired:
            pass


def test_reap_cmd_can_still_report_a_survivor(tmp_path):
    """A drifted receipt is UNKNOWN and cannot authorize a signal."""
    mod = _load()
    run_id = os.urandom(16).hex()
    env = dict(os.environ)
    env["BD_WEDGE_RUN_ID"] = run_id
    live = subprocess.Popen(
        ["bash", "-c", "trap '' TERM; sleep 300"],
        start_new_session=True, env=env)
    try:
        receipt = _capture_outer_receipt(mod, live, tmp_path, run_id)
        row = json.loads(receipt.read_text(encoding="ascii"))
        row["starttime"] += 1
        receipt.write_text(json.dumps(row), encoding="ascii")
        out = subprocess.run(["bash", "-c", mod.reap_cmd(str(receipt), term_grace=0.05)],
                             capture_output=True, text=True, timeout=_w1_budget_s("reap_cmd_can_still_report_a_survivor/run"))
        assert out.returncode != 0 and "REAP-UNKNOWN" in out.stdout
        assert live.poll() is None, (
            "receipt drift signalled the unrelated/recycled identity")
    finally:
        live.kill()
        live.wait()


def test_wedge_interrupt_uses_the_saved_gate_receipt_not_a_bare_pid():
    """The evidence-flush SIGINT is subject to the same reuse rule as reap."""
    mod = _load()
    source = _source()
    assert '"kill -INT %s 2>/dev/null; echo INT-sent"' not in source
    assert hasattr(mod, "signal_receipt_cmd")
    command = mod.signal_receipt_cmd("123:7:123:7:9001", "INT")
    assert "pidfd_send_signal" in command and "123:7:123:7:9001" in command
    assert 'getattr(signal, "SIG" + argv[2])' in command
    assert command.endswith(" INT")


def test_signal_receipt_refuses_starttime_drift_before_pidfd_signal(tmp_path):
    """A stale five-field token cannot authorize the actual signal transport."""
    mod = _load()
    signal_marker = tmp_path / "drifted-receipt-signalled"
    entered, _unused_release, entered_fd = _w1_fifo_barrier(
        tmp_path, "signal-receipt")
    program = (
        "import pathlib, signal\n"
        "marker = pathlib.Path(%r)\n"
        "signal.signal(signal.SIGHUP, lambda *_: "
        "marker.write_text('signalled\\n', encoding='ascii'))\n"
        "with open(%r, 'w', encoding='ascii') as stream:\n"
        "    stream.write('handler-ready\\n')\n"
        "while True:\n"
        "    signal.pause()\n"
    ) % (str(signal_marker), str(entered))
    proc = subprocess.Popen(
        [os.environ.get("PYTHON", "python3"), "-c", program],
        start_new_session=True,
    )
    try:
        assert _w1_await_fifo(entered_fd, site="signal_receipt_refuses_starttime_drift_before_pidfd_signal/fifo") == "handler-ready\n"
        raw = pathlib.Path("/proc", str(proc.pid), "stat").read_text(
            encoding="ascii")
        head, tail_text = raw.rsplit(") ", 1)
        tail = tail_text.split()
        current = (int(head.split(" (", 1)[0]), int(tail[1]),
                   int(tail[2]), int(tail[3]), int(tail[19]))
        assert current[0] == proc.pid and current[0] == current[2] == current[3]
        drifted = (*current[:4], current[4] + 1)
        assert not signal_marker.exists()

        out = subprocess.run(
            ["bash", "-c", mod.signal_receipt_cmd(
                ":".join(map(str, drifted)), "HUP")],
            capture_output=True, text=True, timeout=_w1_budget_s("signal_receipt_refuses_starttime_drift_before_pidfd_signal/run"),
        )

        assert (out.returncode != 0 and "SIGNAL-UNKNOWN" in out.stdout
                and proc.poll() is None and not signal_marker.exists()), (
            "DRIFTED-OUTER-RECEIPT-AUTHORIZED-SIGNAL", out.stdout,
            out.stderr, proc.poll(), signal_marker.exists())
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
        proc.wait(timeout=_w1_budget_s("signal_receipt_refuses_starttime_drift_before_pidfd_signal/wait"))


def test_pidfd_open_race_after_owned_census_is_already_absent(monkeypatch):
    """A process disappearing between census and pidfd_open is not a leak.

    The reaper has already bound the row to an exact receipt.  ESRCH at the
    kernel handle acquisition seam means that identity no longer exists; it
    must not turn a successful concurrent exit into REAP-UNKNOWN.
    """
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    guard_os = namespace["os"]

    def vanished_before_pidfd(_pid, _flags):
        raise ProcessLookupError

    monkeypatch.setattr(guard_os, "pidfd_open", vanished_before_pidfd)
    failures = namespace["signal_exact"]([{"pid": 987654321}], signal.SIGTERM)

    assert failures == [], (
        "PIDFD-OPEN-ESRCH-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)

    closed: list[int] = []
    monkeypatch.setattr(guard_os, "pidfd_open", lambda _pid, _flags: 17)
    monkeypatch.setattr(namespace["signal"], "pidfd_send_signal",
                        lambda _fd, _sig: (_ for _ in ()).throw(
                            ProcessLookupError()))
    monkeypatch.setattr(guard_os, "close", closed.append)
    namespace["exact_current"] = lambda saved: ("MATCH", saved)

    failures = namespace["signal_exact"]([{
        "pid": 987654321, "ppid": 1, "pgid": 987654321,
        "sid": 987654321, "starttime": 44,
    }], signal.SIGTERM)

    assert failures == [], (
        "PIDFD-SEND-ESRCH-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)
    assert closed == [17], "successful pidfd acquisition was not closed"

    monkeypatch.setattr(
        guard_os, "pidfd_open",
        lambda _pid, _flags: (_ for _ in ()).throw(PermissionError()))
    failures = namespace["signal_exact"]([{"pid": 987654321}], signal.SIGTERM)
    assert failures == ["pidfd-open-987654321-PermissionError"], (
        "NON-ABSENCE-PIDFD-ERROR-WAS-LAUNDERED-AS-SUCCESS", failures)


def test_pid_reuse_after_pidfd_open_is_the_owned_identity_already_absent(
        monkeypatch):
    """A pidfd pins the censused process, not a later PID reuse.

    If procfs reports DRIFT after that kernel handle was acquired, the owned
    identity necessarily exited.  The replacement must not be signalled and
    its reuse must not turn completed cleanup into REAP-UNKNOWN.
    """
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    sent: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []
    monkeypatch.setattr(namespace["os"], "pidfd_open",
                        lambda _pid, _flags: 23)
    monkeypatch.setattr(namespace["os"], "close", closed.append)
    monkeypatch.setattr(namespace["signal"], "pidfd_send_signal",
                        lambda fd, sig: sent.append((fd, sig)))
    namespace["exact_current"] = lambda _saved: ("DRIFT", {
        "pid": 4242, "ppid": 1, "pgid": 4242,
        "sid": 4242, "starttime": 9002,
    })

    failures = namespace["signal_exact"]([{
        "pid": 4242, "ppid": 1, "pgid": 4242,
        "sid": 4242, "starttime": 9001,
    }], signal.SIGKILL)

    assert failures == [], (
        "POST-PIDFD-REUSE-MISCLASSIFIED-AS-CLEANUP-FAILURE", failures)
    assert sent == [], "a replacement process was signalled after PID reuse"
    assert closed == [23], "the pidfd was not settled after observed reuse"


def test_signal_exact_close_failure_does_not_skip_later_owned_identity(
        monkeypatch):
    """A failed pidfd settlement is evidence, not permission to stop draining."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    first = {"pid": 101, "ppid": 1, "pgid": 101,
             "sid": 101, "starttime": 1001}
    second = {"pid": 202, "ppid": 1, "pgid": 202,
              "sid": 202, "starttime": 2002}
    fds = iter([41, 42])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []

    def close_with_first_fault(fd):
        closed.append(fd)
        if fd == 41:
            raise OSError("injected close fault")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda fd, sig: signalled.append((fd, sig)))
        guard_patch.setattr(namespace["os"], "close", close_with_first_fault)
        namespace["exact_current"] = lambda saved: ("MATCH", saved)

        failures = namespace["signal_exact"]([first, second], signal.SIGTERM)

    assert signalled == [(41, signal.SIGTERM), (42, signal.SIGTERM)]
    assert closed == [41, 42]
    assert failures == ["pidfd-close-101-OSError"]


def test_reap_continues_to_kill_and_final_census_after_term_pidfd_close_failure(
        monkeypatch, capsys):
    """A TERM close fault cannot bypass escalation or the authoritative census."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    saved = {
        "version": 1, "role": "runner", "run_id": "a" * 32,
        "root": "/srv/bd-wedge/run", "cwd_hex": "2f", "cmdline_hex": "00",
        "fds": [], "pid": 303, "ppid": 1, "pgid": 303,
        "sid": 303, "starttime": 3003,
    }
    census_results = iter([
        ([saved], False),
        ([saved], False),
        ([], False),
    ])
    census_calls: list[int] = []
    fds = iter([51, 52])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []
    waits: list[float] = []

    def controlled_census(_saved):
        census_calls.append(len(census_calls) + 1)
        return next(census_results)

    def close_with_term_fault(fd):
        closed.append(fd)
        if fd == 51:
            raise OSError("injected TERM close fault")

    with monkeypatch.context() as guard_patch:
        namespace["load_receipt"] = lambda _path: saved
        namespace["exact_current"] = lambda current: ("MATCH", current)
        namespace["owned_census"] = controlled_census
        namespace["wait_interval"] = (
            lambda seconds: waits.append(seconds) or int(seconds * 1_000_000))
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda fd, sig: signalled.append((fd, sig)))
        guard_patch.setattr(namespace["os"], "close", close_with_term_fault)

        with pytest.raises(SystemExit) as exited:
            namespace["reap"](["reap", "/receipt", "0.25"])

    reported = capsys.readouterr().out
    assert exited.value.code == 4
    assert signalled == [(51, signal.SIGTERM), (52, signal.SIGKILL)]
    assert closed == [51, 52]
    assert waits == [0.25]
    assert census_calls == [1, 2, 3]
    assert ("REAP-UNKNOWN " in reported
            and "term=FAILED" in reported
            and "kill=SENT" in reported
            and "census=UNKNOWN" in reported
            and "failures=pidfd-close-303-OSError" in reported), reported


@pytest.mark.parametrize("cancel_at", ["close", "signal"])
def test_signal_exact_preserves_baseexception_cancellation_primary(
        monkeypatch, cancel_at):
    """Close settlement records ordinary faults without swallowing cancellation."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    first = {"pid": 404, "ppid": 1, "pgid": 404,
             "sid": 404, "starttime": 4004}
    second = {"pid": 505, "ppid": 1, "pgid": 505,
              "sid": 505, "starttime": 5005}
    primary = KeyboardInterrupt("injected cancellation")
    fds = iter([61, 62])
    signalled: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []

    def signal_with_optional_cancel(fd, sig):
        signalled.append((fd, sig))
        if cancel_at == "signal":
            raise primary

    def close_with_optional_cancel(fd):
        closed.append(fd)
        if cancel_at == "close":
            raise primary
        raise OSError("secondary close fault")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open",
                            lambda _pid, _flags: next(fds))
        guard_patch.setattr(namespace["signal"], "pidfd_send_signal",
                            signal_with_optional_cancel)
        guard_patch.setattr(namespace["os"], "close", close_with_optional_cancel)
        namespace["exact_current"] = lambda saved: ("MATCH", saved)

        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_exact"]([first, second], signal.SIGTERM)

    assert raised.value is primary
    assert signalled == [(61, signal.SIGTERM)]
    assert closed == [61]


@pytest.mark.parametrize(
    "secondary",
    [OSError("close fault"), SystemExit(91)],
    ids=["ordinary-close", "close-cancellation"],
)
def test_process_guard_owned_close_preserves_the_active_primary(
        monkeypatch, secondary):
    """An owned-fd close cannot replace cancellation already in flight."""
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    primary = KeyboardInterrupt("first cancellation")

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))

        def fail_then_close():
            try:
                raise primary
            finally:
                namespace["close_preserving_primary"](71, "capture-temp")

        with pytest.raises(KeyboardInterrupt) as raised:
            fail_then_close()

    assert raised.value is primary
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert "capture-temp" in notes and type(secondary).__name__ in notes, notes


def test_every_process_guard_fd_owner_uses_first_primary_close_settlement():
    """All embedded descriptor owners route through the proven close helper."""
    mod = _load()

    def helper_calls(program, function):
        tree = ast.parse(program)
        owner = next(node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == function)
        return [node for node in ast.walk(owner)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "close_preserving_primary"]

    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "signal_exact")) == 1
    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "capture")) == 2
    assert len(helper_calls(mod.PROCESS_GUARD_PROGRAM, "signal_receipt")) == 1
    reader_tree = ast.parse(mod.REGISTRATION_CHANNEL_READER_PROGRAM)
    assert sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "close_preserving_primary"
        for node in ast.walk(reader_tree)
    ) == 1


def _embedded_process_guard_namespace():
    mod = _load()
    definitions = mod.PROCESS_GUARD_PROGRAM.split(
        "\ntry:\n    action = sys.argv[1]", 1)[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, "<process-guard-definitions>", "exec"), namespace)
    return namespace


def _assert_close_secondary_note(primary, label):
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert label in notes and "SystemExit" in notes, notes


def test_signal_exact_double_cancellation_keeps_signal_primary(monkeypatch):
    namespace = _embedded_process_guard_namespace()
    saved = {"pid": 601, "ppid": 1, "pgid": 601,
             "sid": 601, "starttime": 6001}
    primary = KeyboardInterrupt("signal cancellation")
    secondary = SystemExit(91)
    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "pidfd_open", lambda *_a: 71)
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))
        namespace["exact_current"] = lambda current: ("MATCH", current)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_exact"]([saved], signal.SIGTERM)
    assert raised.value is primary
    _assert_close_secondary_note(primary, "pidfd-close-601")


def test_process_guard_capture_temp_close_fault_keeps_write_cancellation_primary(
        monkeypatch, tmp_path):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("receipt write cancellation")
    secondary = SystemExit(92)
    real_close = os.close
    closed = []

    def close_then_cancel(fd):
        closed.append(fd)
        real_close(fd)
        raise secondary

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(
            namespace["os"], "write",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(namespace["os"], "close", close_then_cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["capture"]([
                "capture", str(os.getpid()), str(tmp_path / "receipt.json"),
                "a" * 32, str(tmp_path), "0",
            ])
    assert raised.value is primary and len(closed) == 1
    _assert_close_secondary_note(primary, "capture-temp")


def test_process_guard_capture_directory_close_fault_keeps_fsync_primary(
        monkeypatch, tmp_path):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("directory fsync cancellation")
    secondary = SystemExit(93)
    real_fsync = os.fsync
    real_close = os.close
    fsyncs = []
    closes = []

    def fsync_then_cancel(fd):
        fsyncs.append(fd)
        if len(fsyncs) == 2:
            raise primary
        return real_fsync(fd)

    def close_directory_then_cancel(fd):
        closes.append(fd)
        real_close(fd)
        if len(closes) == 2:
            raise secondary

    with monkeypatch.context() as guard_patch:
        guard_patch.setattr(namespace["os"], "fsync", fsync_then_cancel)
        guard_patch.setattr(namespace["os"], "close", close_directory_then_cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["capture"]([
                "capture", str(os.getpid()), str(tmp_path / "receipt.json"),
                "b" * 32, str(tmp_path), "0",
            ])
    assert raised.value is primary and len(fsyncs) == 2 and len(closes) == 2
    _assert_close_secondary_note(primary, "capture-directory")


def test_process_guard_signal_receipt_close_fault_keeps_send_primary(monkeypatch):
    namespace = _embedded_process_guard_namespace()
    primary = KeyboardInterrupt("receipt signal cancellation")
    secondary = SystemExit(94)
    saved = {"pid": 701, "ppid": 1, "pgid": 701,
             "sid": 701, "starttime": 7001}
    with monkeypatch.context() as guard_patch:
        namespace["exact_current"] = lambda current: ("MATCH", current)
        guard_patch.setattr(namespace["os"], "pidfd_open", lambda *_a: 81)
        guard_patch.setattr(
            namespace["signal"], "pidfd_send_signal",
            lambda *_a: (_ for _ in ()).throw(primary))
        guard_patch.setattr(
            namespace["os"], "close",
            lambda _fd: (_ for _ in ()).throw(secondary))
        receipt = ":".join(str(saved[key]) for key in
                           ("pid", "ppid", "pgid", "sid", "starttime"))
        with pytest.raises(KeyboardInterrupt) as raised:
            namespace["signal_receipt"](["signal", receipt, "TERM"])
    assert raised.value is primary
    _assert_close_secondary_note(primary, "receipt-signal")


def test_registration_channel_reader_close_fault_keeps_read_primary(monkeypatch):
    mod = _load()
    primary = KeyboardInterrupt("registration read cancellation")
    secondary = SystemExit(95)
    with monkeypatch.context() as reader_patch:
        reader_patch.setattr(sys, "argv", ["reader", "/fifo", "1", "ready", "1"])
        reader_patch.setattr(os, "open", lambda *_a, **_k: 91)
        reader_patch.setattr(select, "select", lambda *_a, **_k: ([91], [], []))
        reader_patch.setattr(
            os, "read", lambda *_a: (_ for _ in ()).throw(primary))
        reader_patch.setattr(
            os, "close", lambda _fd: (_ for _ in ()).throw(secondary))
        with pytest.raises(KeyboardInterrupt) as raised:
            exec(compile(
                mod.REGISTRATION_CHANNEL_READER_PROGRAM,
                "<registration-channel-reader>", "exec"), {})
    assert raised.value is primary
    _assert_close_secondary_note(primary, "registration-channel")


def test_reap_requires_exact_ssh_status_for_reap_ok(monkeypatch, capsys):
    """A transport failure cannot authenticate a success token in stdout."""
    mod = _load()
    monkeypatch.setattr(
        mod, "ssh",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "reap", 255,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "connection lost"))

    verdict = mod.reap(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "test")
    reported = capsys.readouterr().out

    assert verdict != "REAP-OK", (
        "FAILED-SSH-TRANSACTION-LAUNDERED-REAP-OK", verdict)
    assert "REAP-UNKNOWN" in reported, (
        "SSH-STATUS-TOKEN-AMBIGUITY-WAS-NOT-REPORTED", reported)


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        pytest.param(
            0,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="zero-cannot-authenticate-survived"),
        pytest.param(
            5,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="survived-status-cannot-authenticate-ok"),
        pytest.param(
            4,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="unknown-status-cannot-authenticate-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="conflicting-terminal-lines"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-OK",
            id="matching-early-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT failures=NONE\n",
            "REAP-OK",
            id="matching-final-ok"),
        pytest.param(
            4,
            "REAP-UNKNOWN receipt=9:1:9:9:4 current=UNKNOWN term=NOT-SENT "
            "grace_us=0 kill=NOT-SENT census=UNKNOWN wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="matching-unknown"),
        pytest.param(
            5,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-SURVIVED",
            id="matching-survived"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4\n",
            "REAP-UNKNOWN",
            id="truncated-terminal-line"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="late-truncated-final-ok"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="duplicate-identical-terminal-lines"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n"
            "PROCESS-GUARD-UNKNOWN error=OSError\n",
            "REAP-UNKNOWN",
            id="reap-plus-process-guard-terminal"),
        pytest.param(
            0,
            "prefix REAP-OK receipt=9:1:9:9:4 term=SKIPPED grace_us=0 "
            "kill=SKIPPED census=ABSENT wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="embedded-terminal-token"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=PRESENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="ok-verdict-census-mismatch"),
        pytest.param(
            0,
            "REAP-OK receipt=9:1:9:9:4 current=DRIFT term=NOT-SENT "
            "grace_us=0 kill=NOT-SENT census=UNKNOWN wait=NOT-PARENT\n",
            "REAP-UNKNOWN",
            id="ok-verdict-current-mismatch"),
        pytest.param(
            5,
            "REAP-SURVIVED receipt=9:1:9:9:4 term=SENT grace_us=1000 "
            "kill=SENT census=ABSENT wait=NOT-PARENT failures=NONE\n",
            "REAP-UNKNOWN",
            id="survived-verdict-census-mismatch"),
    ],
)
def test_reap_rejects_mismatched_terminal_status_and_token(
        monkeypatch, returncode, stdout, expected):
    """Only complete terminal lines paired with their protocol status survive."""
    mod = _load()
    monkeypatch.setattr(
        mod, "ssh",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            "reap", returncode, stdout, ""))

    verdict = mod.reap(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "test")

    assert verdict == expected


def test_ambiguous_launch_result_records_and_reaps_predicted_receipt(
        monkeypatch):
    """Transport loss after fork cannot be reported as definite refusal."""
    mod = _load()

    def fake_ssh(_addr, command, timeout):
        if command == "nproc":
            return subprocess.CompletedProcess(command, 0, "24\n", "")
        assert "git rev-parse --short HEAD" in command
        return subprocess.CompletedProcess(command, 0, "deadbeef\n0\n", "")

    predicted = "/srv/bd-wedge/run/runner.receipt"
    monkeypatch.setattr(mod, "ssh", fake_ssh)
    settlements: list[tuple[str, str, str]] = []

    def fake_settle(addr, receipt, run_id):
        settlements.append((addr, receipt, run_id))
        return {
            "verdict": "REAP-OK",
            "terminal": "receipt-observed",
            "observations": ["PROCESS-GUARD-UNKNOWN", "RECEIPT-MATCH"],
        }

    assert hasattr(mod, "settle_ambiguous_launch"), (
        "ambiguous launch has no bounded receipt settlement seam")
    monkeypatch.setattr(mod, "settle_ambiguous_launch", fake_settle)
    for launch_rc, launch_out in ((255, ""), (0, "reply without marker")):
        settlements.clear()
        monkeypatch.setattr(
            mod, "launch", lambda *_args, rc=launch_rc, out=launch_out: {
                "rc": rc,
                "out": out,
                "err": "connection lost after remote fork",
                "run_id": "a" * 32,
                "runner_receipt": predicted,
            })
        row = mod.run_one(
            "test2", "10.0.70.95", ("base", [], True, {}, "note"), 1,
            object())

        assert row["state"] == "UNKNOWN", row
        assert row["runner_receipt"] == predicted
        assert row["run_nonce"] == "a" * 32
        assert row["reap"] == "REAP-OK"
        assert row["launch_settlement"]["terminal"] == "receipt-observed"
        assert settlements == [("10.0.70.95", predicted, "a" * 32)]


def test_ambiguous_launch_settlement_waits_for_matching_receipt_before_reap(
        monkeypatch):
    """A runner may publish its receipt after the ssh reply is lost."""
    mod = _load()
    receipt = "/srv/bd-wedge/run/runner.receipt"
    run_id = "b" * 32
    replies = iter([
        subprocess.CompletedProcess("probe", 4,
                                    "PROCESS-GUARD-UNKNOWN error=FileNotFoundError\n", ""),
        subprocess.CompletedProcess(
            "probe", 0,
            "RECEIPT-MATCH receipt=9:1:9:9:4 current=9:1:9:9:4 "
            f"run_id={run_id}\n", ""),
    ])
    monkeypatch.setattr(mod, "ssh", lambda *_args, **_kwargs: next(replies))
    reaps: list[tuple[str, str, str, float]] = []

    def fake_reap(addr, path, why, *, timeout):
        reaps.append((addr, path, why, timeout))
        return "REAP-OK"

    monkeypatch.setattr(mod, "reap", fake_reap)
    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", receipt, run_id, timeout_s=1.0, poll_s=0.001)

    assert settled["verdict"] == "REAP-OK"
    assert settled["terminal"] == "receipt-observed"
    assert [item["state"] for item in settled["observations"]] == [
        "PROCESS-GUARD-UNKNOWN", "RECEIPT-MATCH"]
    assert len(reaps) == 1
    assert reaps[0][:3] == (
        "10.0.70.95", receipt, "ambiguous launch result")
    assert 0 < reaps[0][3] <= 1.0, (
        "the reap received a subprocess allowance outside its settlement: %r"
        % (reaps,))


class _Row284BudgetConsumingTransport:
    """Consume the timeout handed to the reached transport boundary.

    ``Event.wait`` models a connected subprocess that stays silent for its
    entire allowance.  The release event exists only to settle a deliberately
    over-budget RED/control invocation without leaving a thread behind; there
    is no scheduling sleep in this fixture.
    """

    def __init__(self, run_id, forced_reap_budget=None):
        self.run_id = run_id
        self.forced_reap_budget = forced_reap_budget
        self.release = threading.Event()
        self.reap_entered = threading.Event()
        self.calls = []

    def __call__(self, _addr, command, timeout):
        action = "reap" if " reap " in command else "probe"
        self.calls.append((action, float(timeout)))
        if action == "probe":
            return subprocess.CompletedProcess(
                command, 0,
                "RECEIPT-MATCH receipt=9:1:9:9:4 current=9:1:9:9:4 "
                f"run_id={self.run_id}\n", "")
        self.reap_entered.set()
        consumed_budget = (
            float(self.forced_reap_budget)
            if self.forced_reap_budget is not None else float(timeout))
        self.release.wait(consumed_budget)
        return subprocess.CompletedProcess(command, 124, "", "reap timed out")


def _measure_row284_settlement(mod, monkeypatch, *, forced_reap_budget=None):
    """Return a real wall-clock measurement of one reached settlement reap."""
    governing_bound = 0.05
    assertion_ceiling = 0.20
    run_id = "2" * 32
    transport = _Row284BudgetConsumingTransport(
        run_id, forced_reap_budget=forced_reap_budget)
    monkeypatch.setattr(mod, "ssh", transport)
    result = {}
    failure = {}

    def settle():
        try:
            result.update(mod.settle_ambiguous_launch(
                "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", run_id,
                **{"timeout_s": governing_bound, "poll_s": governing_bound}))
        except BaseException as exc:  # preserve the worker's exact failure
            failure["exception"] = exc

    worker = threading.Thread(target=settle, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(assertion_ceiling)
    exceeded = worker.is_alive()
    elapsed = time.monotonic() - started
    # Settle the RED and the deliberate negative control promptly.  A correct
    # run has already returned because its reap allowance was the remaining
    # fraction of governing_bound.
    transport.release.set()
    worker.join(1.0)
    assert not worker.is_alive(), "fixture could not settle its transport owner"
    if failure:
        raise failure["exception"]
    return {
        "governing_bound": governing_bound,
        "assertion_ceiling": assertion_ceiling,
        "elapsed": elapsed,
        "exceeded": exceeded,
        "calls": transport.calls,
        "result": result,
        "reap_entered": transport.reap_entered.is_set(),
    }


def _assert_row284_actual_elapsed_bound(measured):
    assert measured["calls"] and measured["calls"][0][0] == "probe", (
        "precondition: the matching receipt probe did not run")
    assert measured["reap_entered"], (
        "precondition: the ambiguous launch never entered its reap")
    assert [action for action, _budget in measured["calls"]] == [
        "probe", "reap"], (
        "precondition: expected exactly one probe and one reap, got %r"
        % (measured["calls"],))
    assert not measured["exceeded"], (
        "row 284 actual elapsed exceeded the governing settlement bound: "
        "the %.3fs item was still inside its one reached reap after %.3fs "
        "(reap subprocess budget %.3fs)" % (
            measured["governing_bound"], measured["elapsed"],
            measured["calls"][1][1]))
    assert measured["elapsed"] <= measured["assertion_ceiling"], measured
    assert measured["result"]["terminal"] == "receipt-observed", measured


def test_row_284_ambiguous_launch_reap_stays_inside_its_actual_elapsed_bound(
        monkeypatch):
    """F30: the five-second settlement must not start a sixty-second reap."""
    mod = _load()
    assert mod.settle_ambiguous_launch.__kwdefaults__["timeout_s"] == 5.0, (
        "precondition: this is no longer the nominal five-second settlement")

    measured = _measure_row284_settlement(mod, monkeypatch)
    _assert_row284_actual_elapsed_bound(measured)
    assert all(budget <= measured["governing_bound"]
               for _action, budget in measured["calls"]), (
        "a settlement subprocess received a budget outside its %.3fs item: %r"
        % (measured["governing_bound"], measured["calls"]))


def test_row_284_elapsed_gate_rejects_an_oversized_reap_budget(monkeypatch):
    """Negative control: a larger reap allowance must fail on wall elapsed."""
    mod = _load()
    measured = _measure_row284_settlement(
        mod, monkeypatch, forced_reap_budget=0.40)
    assert measured["reap_entered"]
    assert len(measured["calls"]) == 2
    with pytest.raises(
            AssertionError,
            match="row 284 actual elapsed exceeded the governing settlement bound"):
        _assert_row284_actual_elapsed_bound(measured)


def test_ambiguous_launch_settlement_refuses_a_different_run_nonce(monkeypatch):
    """The predicted pathname alone never authorizes cleanup."""
    mod = _load()
    receipt = "/srv/bd-wedge/run/runner.receipt"
    expected = "b" * 32
    observed = "c" * 32
    reply = subprocess.CompletedProcess(
        "probe", 0,
        "RECEIPT-MATCH receipt=9:1:9:9:4 current=9:1:9:9:4 "
        f"run_id={observed}\n", "")
    monkeypatch.setattr(mod, "ssh", lambda *_args, **_kwargs: reply)
    monkeypatch.setattr(
        mod, "reap",
        lambda *_args: pytest.fail("mismatched nonce authorized a reap"))

    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", receipt, expected, timeout_s=1.0, poll_s=0.001)

    assert settled["verdict"] == "REAP-UNKNOWN"
    assert settled["terminal"] == "receipt-run-id-mismatch"
    assert settled["observations"][-1]["state"] == "RECEIPT-MATCH"


def test_ambiguous_launch_settlement_deadline_remains_unknown(monkeypatch):
    """No receipt by the bounded deadline proves neither launch nor cleanup."""
    mod = _load()
    reply = subprocess.CompletedProcess(
        "probe", 4, "PROCESS-GUARD-UNKNOWN error=FileNotFoundError\n", "")
    probes: list[str] = []

    def fake_ssh(_addr, command, **_kwargs):
        probes.append(command)
        return reply

    monkeypatch.setattr(mod, "ssh", fake_ssh)
    monkeypatch.setattr(
        mod, "reap",
        lambda *_args: pytest.fail("missing receipt authorized a reap"))
    started = time.monotonic()
    settled = mod.settle_ambiguous_launch(
        "10.0.70.95", "/srv/bd-wedge/run/runner.receipt", "d" * 32,
        timeout_s=0.01, poll_s=0.002)

    assert time.monotonic() - started < 1.0, "settlement deadline was not bounded"
    assert probes, "deadline result was vacuous: no receipt probe ran"
    assert settled["verdict"] == "REAP-UNKNOWN"
    assert settled["terminal"] == "receipt-deadline"
    assert all(item["state"] == "PROCESS-GUARD-UNKNOWN"
               for item in settled["observations"])


def test_every_abandon_path_reaps():
    """No terminal branch may leave a remote master running.

    Read as SOURCE STRUCTURE rather than per-line: the abandon branches are
    multi-line and a line-scoped check cannot see a reap three lines below the
    state it is judging (CLAUDE.md section 0's shell-construct trap, in Python).
    """
    src = _source()
    tree = ast.parse(src)
    consts = _string_constants()

    # The branches that END a run. Each is identified by the state it records,
    # asserted over LITERALS so a comment naming a state cannot satisfy it.
    for state in ("CAPPED", "UNKNOWN", "ABANDONED"):
        assert state in consts, (
            f"no branch records state {state!r} as a string literal. If the "
            "monitor loop can end a run without recording a distinguishable "
            "state, an abandoned sample is indistinguishable from a completed "
            "one -- which is how a false COMPLETED enters the wedge "
            "denominator. Backlog 146."
        )

    # Every reap must go through the shared builder, so there is exactly one
    # definition of "kill it properly" to get right.
    #
    # EXEMPT reap_cmd's OWN BODY BY STRUCTURE, NOT BY A SUBSTRING. The first
    # draft filtered constants that did not contain the word "reap", which
    # flagged reap_cmd itself -- the one place the kill is supposed to live.
    # Walking to the FunctionDef and excluding its subtree asks the question
    # that was actually meant.
    builder = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "reap_cmd"),
                   None)
    assert builder is not None, "reap_cmd is not a module-level function"
    exempt = {id(n) for n in ast.walk(builder)}

    # THE RUNNER TEMPLATE IS A SECOND EXEMPTION, AND THE COVERAGE MOVES RATHER
    # THAN DISAPPEARS -- the replacement assertion below is strictly stronger
    # than the ban it replaces. Row 212 gave the runner a registration-failure
    # branch that must reap the group it just launched. It cannot route that
    # through `reap_cmd`: reap_cmd builds an SSH command that probes a pid it
    # did not create and reports REAP-OK/REAP-SURVIVED to a monitor, while the
    # runner is already ON the host, owns the pid, and has no channel to report
    # to -- registration failing is exactly why nothing else knows the pid
    # exists. Shipping the remote verdict protocol into the runner text would
    # be a worse answer than this exemption.
    #
    # Exempted BY STRUCTURE (the RUNNER Assign node's subtree), never by a
    # substring: a substring exemption would also excuse a hand-rolled kill
    # anywhere else that happened to mention the word.
    runner_assign = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "RUNNER" for t in n.targets)),
        None)
    assert runner_assign is not None, (
        "RUNNER is no longer a module-level assignment; this exemption is now "
        "aimed at nothing and the check below cannot see its subject")
    exempt |= {id(n) for n in ast.walk(runner_assign)}

    # THE RE-CONSTRAINT. Registration failure happens before the anonymous gate
    # releases the workload, so that branch owns exactly one inert direct child
    # and must have NO numeric group-signal sink. EOF + ABORTED + checked wait is
    # stronger than trying to close a recyclable receipt-to-kill window.
    runner_text = _load().RUNNER
    assert not re.search(r'kill\s+-9\s+-"\$PYTEST_PGID"', runner_text), (
        "registration failure restored a numeric process-group signal sink; "
        "the PGID can be reused between a receipt and that action")
    executable_runner = "\n".join(
        line for line in runner_text.splitlines()
        if not line.lstrip().startswith("#"))
    alternate_signal_sinks = (
        r"(?m)^\s*(?:(?:builtin|command)\s+)?(?:kill|pkill)\b",
        r"(?m)^\s*/(?:usr/)?bin/kill\b",
        r"\bos\.(?:kill|killpg)\s*\(",
        r"\bsignal\.pidfd_send_signal\s*\(",
    )
    stop_start = executable_runner.index("registration_owner_stop() {{")
    stop_end = executable_runner.index(
        "\n}}\n\nregistration_promote_spawn_group_receipt() {{", stop_start)
    stop_end += len("\n}}\n")
    stop_body = executable_runner[stop_start:stop_end]
    assert "registration_promote_spawn_group_receipt" not in stop_body, (
        "the single signal-capability slice includes an adjacent owner helper")
    signal_lines = [
        line for line in executable_runner.splitlines()
        if any(re.search(pattern, line) for pattern in alternate_signal_sinks)
    ]
    assert len(signal_lines) == 2 and all(
        line in stop_body for line in signal_lines
    ), "a signal sink escaped the single timeout-owner stop capability"
    forbidden_targets = (
        "PYTEST_PID", "PYTEST_PGID", "PYTEST_GATE_PID",
        "W1_TERMINAL_RELAY_PID", "W1_JOB_ID",
    )
    assert all(not any(target in line for target in forbidden_targets)
               for line in signal_lines), (
        "the timeout-owner signal capability accepts a gate/receipt/job id")
    outside_stop = executable_runner[:stop_start] + executable_runner[stop_end:]
    stop_targets = re.findall(
        r'^\s*registration_owner_stop "\$(W1_[A-Z_]+)"',
        outside_stop, re.MULTILINE)
    assert stop_targets and set(stop_targets) == {
        "W1_SPAWN_PID", "W1_COLLECT_PID", "W1_TIMER_PID",
        "W1_ACTIVE_OWNER_PID",
    }, "an unowned identity reaches registration_owner_stop"
    assert "W1_READY_SECONDS=10" in runner_text, (
        "READY admission has no explicit finite deadline")
    assert "W1_GATE_SECONDS=10" in runner_text, (
        "abort/handoff has no explicit finite deadline")
    assert 'registration_read_frame "$W1_READY_SECONDS"' in runner_text
    assert "registration_read_terminal" in runner_text
    assert "registration_status_is_quiet_and_open" not in runner_text
    assert "registration_status_is_eof" not in runner_text
    assert 'kill "$W1_REGISTRAR_PID"' not in runner_text
    assert "registration_checked_gate_wait" in runner_text
    assert runner_text.count("registration_checked_child_wait() {{") == 1
    assert ('wait -n -p W1_RACE_WAITED_PID "$W1_CHILD_PID" '
            '"$W1_TIMER_PID"') in runner_text
    assert "READY v1 pid=" in _load().REGISTRATION_GATE_PROGRAM
    assert "ABORTED v1 reason=" in _load().REGISTRATION_GATE_PROGRAM
    assert "EXEC-OK v1" in _load().registration_workload_shim("/x", "true")
    assert "REGISTER-GATE-ABORT" in runner_text

    hand_rolled = sorted(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "kill -9" in n.value and id(n) not in exempt
    )
    assert not hand_rolled, (
        "a hand-rolled `kill -9` survives outside reap_cmd, at line(s) "
        f"{hand_rolled}. Four branches with four kill spellings is what let the "
        "CAPPED path kill only the master and orphan its 48 workers. Route it "
        "through reap_cmd."
    )


def test_an_abandoned_run_is_not_recorded_as_COMPLETED():
    """A false COMPLETED is a false NEGATIVE in the wedge rate.

    The monitor loop is `while not STOP.is_set()`, so a STOP set by --hours or
    by an interrupt drops it out on the next tick with no `pytest_exit`. If the
    row then defaults to COMPLETED, an abandoned sample silently joins the
    denominator as a non-wedge.
    """
    consts = _string_constants()
    assert "ABANDONED" in consts, (
        "no ABANDONED state is ever recorded as a literal. The row still "
        "defaults to COMPLETED with no guard for the abandoned case, so a run "
        "dropped by STOP -- which has no pytest_exit -- is counted as a "
        "completed non-wedge, a false negative in the wedge rate."
    )
    assert "pytest_exit" in consts, "the completion marker vanished"

    # The ABANDONED branch must be GUARDED on the run not having finished, not
    # written unconditionally: an unconditional ABANDONED would mark every
    # completed sample abandoned, which passes the literal check above and
    # destroys the data. Over-sensitivity is a soundness bug (section 0).
    tree = ast.parse(_source())
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        body_src = "\n".join(ast.unparse(b) for b in node.body)
        if "ABANDONED" in body_src and "pytest_exit" in test_src:
            guarded = True
    assert guarded, (
        "the ABANDONED state is not guarded on the absence of pytest_exit. It "
        "must fire only for a run that never finished; marking a completed "
        "sample abandoned would be the same defect pointing the other way."
    )


@pytest.mark.parametrize(("status", "state"), [
    (91, "REGISTRATION_REFUSED"),
    (92, "REGISTRATION_UNKNOWN"),
    (93, "REGISTERED_FAILURE"),
    (94, "REGISTRATION_SETUP_FAILURE"),
])
def test_registration_runner_exit_is_classified_before_completed_default(
        status, state):
    """The outer reader must not call a registration failure COMPLETED."""
    mod = _load()
    row = {}

    mod.record_runner_exit(row, str(status), "/remote/run/private-root")

    assert row["pytest_exit"] == str(status)
    assert row["state"] == state, (
        f"runner status {status} was laundered into the COMPLETED default")
    assert row["state"] != "COMPLETED"
    assert row["registration_artifacts"] == {
        "jobid": "/remote/run/private-root/jobid",
        "error": "/remote/run/private-root/jobid.err",
        "owners": "/remote/run/private-root/registration-owners.log",
        "runner_receipt": "/remote/run/private-root/runner.receipt",
        "gate_receipt": "/remote/run/private-root/gate.receipt",
        "authority_fds": (
            "/remote/run/private-root/registration-authority-fds.log"),
    }


@pytest.mark.parametrize("status", [0, 1, 75, 90])
def test_nonregistration_runner_exit_keeps_ordinary_completion_policy(status):
    mod = _load()
    row = {}

    mod.record_runner_exit(row, str(status), "/remote/run/control")

    assert row == {"pytest_exit": str(status)}


def test_the_interrupt_handler_does_not_promise_what_it_does_not_do():
    """CLAUDE.md section 10: the verdict line is the least-tested output.

    Two messages here were false. The interrupt said in-flight samples are NOT
    killed -- true, and a leak. `--hours` said it was 'letting in-flight samples
    finish', which the loop's own STOP predicate makes impossible. A message
    that misdescribes the behaviour beside it is how row 146 got its wrong
    diagnosis in the first place.
    """
    # Over LITERALS, not raw source. This test's own explanatory prose quotes
    # both retired phrases, and the first draft grepped the file -- it passed
    # only because the comment wrap happened to split one of them across a
    # newline. That is luck, not a check.
    said = " || ".join(_string_constants())
    assert "in-flight samples are NOT killed" not in said, (
        "the interrupt handler still advertises that it leaks. It should reap "
        "in-flight runs and join its threads so their rows are written."
    )
    assert "letting in-flight samples finish" not in said, (
        "--hours still claims to let in-flight samples finish. The monitor loop "
        "is `while not STOP.is_set()`, so they are abandoned on the next tick."
    )


def test_the_interrupt_joins_its_threads_so_rows_are_written():
    """Daemon threads die at interpreter exit, taking their unwritten rows.

    That is why `rows.jsonl` held ZERO abandoned rows after an interrupted 19h
    hunt: the samples were not mis-recorded, they were never recorded at all.
    """
    tree = ast.parse(_source())
    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and isinstance(n.type, ast.Name)
                and n.type.id == "KeyboardInterrupt"]
    assert handlers, "no KeyboardInterrupt handler found -- has main() changed?"

    # ASK FOR A THREAD JOIN, NOT FOR THE SUBSTRING "join". The first version of
    # this assertion was `"join" in ast.unparse(handler)`, and a mutation
    # battery escaped it immediately: the handler's own warning line contains
    # `", ".join(alive)`, so the str method satisfied a check written about
    # Thread.join. CLAUDE.md section 1 -- a predicate over the wrong part of
    # the syntax is worse than a grep, because it looks rigorous.
    #
    # The shape required: a `for` loop over the threads whose body joins the
    # loop variable.
    def _joins_its_loop_var(handler: ast.ExceptHandler) -> bool:
        for node in ast.walk(handler):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            var = node.target.id
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "join"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == var):
                    return True
        return False

    assert any(_joins_its_loop_var(h) for h in handlers), (
        "the KeyboardInterrupt handler does not join its host threads. They are "
        "daemon=True, so the interpreter kills them at exit: the remote run is "
        "never reaped AND its row is never written. That is the whole of "
        "backlog 146's five orphaned masters, and it is why rows.jsonl held "
        "ZERO abandoned rows after an interrupted 19h hunt. Join them (bounded) "
        "so each thread can reap and record."
    )


# ---------------------------------------------------------------------------
# W1 -- BACKLOG 212. The runner must not wait on a job it could not register.
#
# The abandon paths above are about a run the MONITOR gives up on. This battery is
# about the other end of the same leak: the remote runner script itself. Its
# registration step reads
#
#     python3 .../bd-jobs register ... > "$RUNDIR/jobid" 2>... || \
#         echo "REGISTER-FAILED" > "$RUNDIR/jobid"
#     wait "$PYTEST_PID"
#
# so a registrar that fails -- a torn write, a full disk, an unwritable
# JOBS_DIR, row 212's whole subject -- is SWALLOWED, and the runner then waits
# for the full pytest run it just started. The result is a live pytest master
# and up to 48 workers on a fleet host that `bd-jobs list` cannot see and
# `bd-jobs reap` will never reach, for the entire duration of the run. The
# monitor's own reaping cannot help: it reaps by the row it knows about, and
# the operator's registry is precisely what is missing.
#
# WHY THE PRODUCTION TEMPLATE AND A REAL BASH. The subject is shell text. A
# copy of it in this file would be a test of the copy, and a structural check
# over `mod.RUNNER` cannot tell `kill` from `kill` in a comment or prove the
# child actually died. So: format the REAL `mod.RUNNER`, point `$HOME` at a
# fake checkout whose only content is a stub `bd-jobs`, and run it under real
# bash. Exactly one boundary is stubbed -- the registrar's exit status.
#
# WHAT THIS BATTERY CANNOT SEE: it drives the runner LOCALLY. The ssh transport,
# `setsid nohup`, and the remote host's environment are outside it.
# ---------------------------------------------------------------------------

W1_REGISTER_FAILURE_CODE = 73    # the stub registrar's distinctive exit
W1_RUNNER_FAILURE_CODE = "91"    # what the runner must record for itself
W1_RETAINED_FAILURE_CODE = "92"  # cleanup timed out with an exact retained id
W1_SETUP_FAILURE_CODE = "94"     # setup owners settled UNSUCCESSFULLY,
                                 # which is a PROVED settlement, not an
                                 # unknown one -- see the gate-ready
                                 # admission controls below
W1_RELEASE_FAILURE_CODE = "93"   # registration landed but gate release failed
W1_WORKLOAD_CODE = 7             # the success control's workload exit
W1_STUB_MARKER = "STUB-REGISTRAR-REACHED"
W1_RUNNER_BOUND = 40.0           # every wait in this battery is bounded


def _w1_fake_home(tmp_path, *, code: int, stdout: str = "", sleep: float = 0.0):
    """A fake `$HOME` whose only inhabitant is a stub `bd-jobs`.

    The runner invokes `python3 "$HOME/BulkDownloader/toolchain/bin/bd-jobs"`,
    so the stub is Python, not shell, and needs no exec bit. `sleep` exists so
    the caller can observe the launched process group WHILE it is alive: a
    reap assertion with no proven live group before it is the empty-iterable
    green CLAUDE.md section 7 names.
    """
    home = tmp_path / "fakehome"
    binp = home / "BulkDownloader" / "toolchain" / "bin"
    binp.mkdir(parents=True)
    (binp / "bd-jobs").write_text(
        "import os, pathlib, signal, sys, time\n"
        "marker = os.environ.get('W1_REGISTRAR_MARKER')\n"
        "if marker:\n"
        "    pathlib.Path(marker).write_text('invoked', encoding='utf-8')\n"
        "argv_log = os.environ.get('W1_STUB_ARGV_LOG')\n"
        "if argv_log:\n"
        "    with pathlib.Path(argv_log).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "pid_marker = os.environ.get('W1_REGISTRAR_PID_MARKER')\n"
        "if pid_marker:\n"
        "    pathlib.Path(pid_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "is_register = len(sys.argv) > 1 and sys.argv[1] == 'register'\n"
        "is_reap = len(sys.argv) > 1 and sys.argv[1] == 'reap'\n"
        "registered_pid_file = os.environ.get('W1_REGISTERED_PID_FILE')\n"
        "if is_register and registered_pid_file:\n"
        "    registered_pid = sys.argv[sys.argv.index('--pid') + 1]\n"
        "    pathlib.Path(registered_pid_file).write_text(registered_pid, encoding='utf-8')\n"
        "register_entered = os.environ.get('W1_REGISTER_ENTERED_FIFO')\n"
        "register_release = os.environ.get('W1_REGISTER_RELEASE_FIFO')\n"
        "if is_register and register_entered and register_release:\n"
        "    with open(register_entered, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('register-entered\\n')\n"
        "    with open(register_release, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n"
        "reap_pid_marker = os.environ.get('W1_REAP_PID_MARKER')\n"
        "if is_reap and reap_pid_marker:\n"
        "    pathlib.Path(reap_pid_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'reap' and "
        "os.environ.get('W1_REAP_IGNORE_TERM'):\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "reap_child_marker = os.environ.get('W1_REAP_CHILD_PID_MARKER')\n"
        "if is_reap and reap_child_marker:\n"
        "    child_ready_read, child_ready_write = os.pipe()\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        os.close(child_ready_read)\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        pathlib.Path(reap_child_marker).write_text(str(os.getpid()), encoding='utf-8')\n"
        "        os.write(child_ready_write, b'1')\n"
        "        os.close(child_ready_write)\n"
        "        time.sleep(300)\n"
        "        raise SystemExit(0)\n"
        "    os.close(child_ready_write)\n"
        "    os.read(child_ready_read, 1)\n"
        "    os.close(child_ready_read)\n"
        "reap_entered = os.environ.get('W1_REAP_ENTERED_FIFO')\n"
        "reap_release = os.environ.get('W1_REAP_RELEASE_FIFO')\n"
        "if is_reap and reap_entered and reap_release:\n"
        "    with open(reap_entered, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('reap-entered\\n')\n"
        "    with open(reap_release, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n"
        "if is_reap and os.environ.get('W1_REAP_IGNORE_TERM'):\n"
        "    time.sleep(float(os.environ.get('W1_REAP_IGNORE_TERM', '300')))\n"
        "if is_reap and os.environ.get('W1_REAP_KILL_REGISTERED') and registered_pid_file:\n"
        "    registered_pid = int(pathlib.Path(registered_pid_file).read_text())\n"
        "    try:\n"
        "        os.killpg(registered_pid, signal.SIGKILL)\n"
        "    except ProcessLookupError:\n"
        "        pass\n"
        "fd_report = os.environ.get('W1_REGISTRAR_FD_REPORT')\n"
        "if fd_report:\n"
        "    rows = []\n"
        "    for item in pathlib.Path('/proc/self/fd').iterdir():\n"
        "        try:\n"
        "            rows.append(item.name + '=' + os.readlink(item))\n"
        "        except OSError:\n"
        "            pass\n"
        "    fd_report_path = pathlib.Path(fd_report)\n"
        "    fd_report_temp = fd_report_path.with_name(\n"
        "        fd_report_path.name + '.tmp.%d' % os.getpid())\n"
        "    fd_report_temp.write_text('\\n'.join(rows) + '\\n', encoding='utf-8')\n"
        "    os.replace(fd_report_temp, fd_report_path)\n"
        "sys.stderr.write({marker!r} + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep({sleep!r})\n"
        "if os.environ.get('W1_REGISTRAR_KILL_REGISTERED_PID'):\n"
        "    pid = int(sys.argv[sys.argv.index('--pid') + 1])\n"
        "    os.kill(pid, 9)\n"
        "    deadline = time.monotonic() + 2.0\n"
        "    while pathlib.Path('/proc', str(pid)).exists() and "
        "time.monotonic() < deadline:\n"
        "        time.sleep(0.01)\n"
        "sys.stdout.write({stdout!r})\n"
        "sys.exit({code!r})\n".format(
            marker=W1_STUB_MARKER, sleep=float(sleep), stdout=stdout, code=int(code)),
        encoding="utf-8")
    return home


def _w1_stat_pgid_and_state(pid) -> tuple[str, str] | None:
    """(pgid, state) for `pid` from /proc, or None if it is gone.

    The comm field is parenthesised and may itself contain spaces and
    parentheses, so the split is anchored on the LAST ')' rather than on
    whitespace. After it the fields are state, ppid, pgrp.
    """
    try:
        with open("/proc/%s/stat" % pid, "rb") as handle:
            raw = handle.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    close = raw.rfind(b")")
    if close == -1:
        return None
    rest = raw[close + 2:].split()
    if len(rest) < 3:
        return None
    return rest[2].decode("ascii", "replace"), rest[0].decode("ascii", "replace")


def _w1_live_in_group(pgid: int) -> list[str]:
    """Live (non-zombie) pids sharing `pgid`. A zombie is gone for our purpose.

    READ /proc DIRECTLY; DO NOT FORK `ps`. The forking version had two defects
    and this fixes both, measured 2026-08-24 on test5 at 852 processes:

    (1) IT COUNTED ITS OWN INSTRUMENT. `subprocess.run(["ps", ...])` puts the
        `ps` child in the CALLER'S process group, so censusing a group that
        contains the caller returned the caller plus the `ps` that was doing
        the counting -- a pid already gone by the time the list was read.
        Reproduced exactly: ps reported ['1640295', '1640296'] where 1640295
        was the caller and 1640296 was the ps itself; the /proc census
        reported ['1640295']. That is CLAUDE.md A7's shape -- an instrument
        inside its own denominator -- and it is a correctness bug regardless
        of how fast it runs.

    (2) ITS COST SCALED WITH THE WHOLE HOST, NOT WITH THE SUBJECT. A full
        table dump plus fork/exec plus text parsing measured 114.9 ms per
        call against 40.4 ms for this /proc read, and `_w1_wait_for_gate`
        called it every 10 ms. That is why the same module took 414-431s here
        and 97-119s on a 4-core CI runner with ~100 processes: the census was
        charging every poll for processes that have nothing to do with the
        test. See backlog row 231.

    The RESULT is unchanged: same pgid, zombies excluded, pids as strings.
    """
    target = str(pgid)
    live: list[str] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        observed = _w1_stat_pgid_and_state(entry)
        if observed is None:
            continue
        if observed[0] == target and observed[1] != "Z":
            live.append(entry)
    return live


def _w1_children_of(pid) -> list[str]:
    """Direct children of `pid`, read from /proc/<pid>/task/<tid>/children.

    O(children), never O(host). The kernel publishes one `children` file per
    THREAD, so every task directory is read; a single-threaded process has
    exactly one. A missing directory means the process is gone, and the empty
    list is the same answer a walk would reach.
    """
    root = "/proc/%s/task" % pid
    kids: list[str] = []
    try:
        tids = os.listdir(root)
    except (FileNotFoundError, ProcessLookupError, PermissionError,
            NotADirectoryError):
        return kids
    for tid in tids:
        try:
            with open("%s/%s/children" % (root, tid), "rb") as handle:
                raw = handle.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        kids.extend(entry.decode("ascii", "replace") for entry in raw.split())
    return kids


def _w1_walk_group_has_at_least(pgid: int, wanted: int) -> bool:
    """The complete-walk threshold answer. Correct at the cost of the host.

    Kept as the fallback for the one case descent cannot serve -- a leader
    that is not observable in its own group leaves the descent no root.
    """
    target = str(pgid)
    seen = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        observed = _w1_stat_pgid_and_state(entry)
        if observed is None:
            continue
        if observed[0] == target and observed[1] != "Z":
            seen += 1
            if seen >= wanted:
                return True
    return False


def _w1_group_has_at_least(pgid: int, wanted: int) -> bool:
    """Cheap PRESENCE probe for a polling loop. NEVER use it to prove absence.

    ROW 231, THE HALF v3.66.1213 LEFT. That cut stopped the census forking
    `ps`, but the probe it added still fell through to a WALK OF THE WHOLE
    /proc whenever the leader alone did not satisfy `wanted` -- and that is
    exactly the state a poll loop sits in while it waits. `_w1_wait_for_gate`
    polls every 10ms, so `minimum_live=2` charged one full host walk per 10ms
    for the entire wait. v3.66.1224 measured a full walk at 39.3ms median over
    856 pids; replacing a fork with a walk of the same denominator is the same
    defect wearing different clothes, and this is the version that stops
    paying for processes that have nothing to do with the test.

    THE ANSWER IS NOW REACHED BY DESCENT: the leader, then its descendants via
    /proc/<pid>/task/<tid>/children, stopping the moment `wanted` members with
    the exact pgid have been seen. Cost is O(group), not O(host).

    AND THE BOUNDARY IS STATED RATHER THAN HIDDEN. Descent sees a member only
    if it is a descendant of the leader. Every group in this module is created
    by bash job control -- one forked leader, and everything after it inherits
    -- so the two sets coincide, and
    `test_the_cheap_presence_probe_agrees_with_the_complete_census` proves it
    on planted groups of one, two and three. A member placed in the group from
    OUTSIDE that subtree is invisible here, which is a FALSE NEGATIVE: the
    caller polls again and finally raises its own distinctive assertion. That
    is fail-closed, it is asserted by
    `test_a_non_descendant_group_member_fails_the_probe_closed_not_open`, and
    it is why this function must never be asked whether a group is EMPTY.
    Absence needs the complete denominator and every absence assertion in this
    file uses `_w1_live_in_group`.
    """
    if wanted <= 0:
        return True
    target = str(pgid)
    leader = _w1_stat_pgid_and_state(pgid)
    if leader is None or leader[0] != target or leader[1] == "Z":
        # NO ROOT FOR THE DESCENT. A leader that is gone, reparented out of
        # its own group, or a zombie cannot enumerate anything, so the only
        # complete answer left is the walk. This is bounded by the caller's
        # own deadline and is not the polling steady state.
        return _w1_walk_group_has_at_least(pgid, wanted)
    seen = 1
    if seen >= wanted:
        return True
    pending = [target]
    visited = {target}
    while pending:
        for kid in _w1_children_of(pending.pop()):
            if kid in visited:
                continue
            visited.add(kid)
            # DESCEND THROUGH a child even when its own pgid does not match:
            # a grandchild can be in the group while its parent has left it.
            pending.append(kid)
            observed = _w1_stat_pgid_and_state(kid)
            if observed is not None and observed[0] == target \
                    and observed[1] != "Z":
                seen += 1
                if seen >= wanted:
                    return True
    return False


def _w1_pid_is_live(pid: int) -> bool:
    """A present zombie is already terminal and cannot perform a side effect."""
    try:
        state = _w1_proc_observation(pid)[-1]
    except (FileNotFoundError, ProcessLookupError, ValueError, AssertionError):
        return False
    return state not in {"Z", "X"}


def _w1_build_runner(mod, tmp_path, workload_body: str, *, reap_seconds=None,
                     forward_expiry_is_subject=False, ready_seconds=None,
                     proc_stat_path=None, gate_prelude=None,
                     pytest_pid_override=None, gate_program=None,
                     terminal_relay_program=None,
                     channel_reader_program=None,
                     timeout_owner_program=None,
                     owner_kill_grace_us=None,
                     missing_setup_fd=None, missing_setup_pid=None,
                     registrar_seconds=None, cleanup_seconds=None,
                     observation_seconds=None,
                     reconcile_seconds=None, checked_wait_probe=None,
                     monotonic_samples=None,
                     cancel_before_observation=False,
                     cancel_registered_failure=False,
                     after_relay_acquire_barrier=None,
                     abnormal_owner_fifo=None,
                     after_terminal_owner_ready_barrier=None,
                     mutate_terminal_owner_ready=False,
                     handoff_deadline_probe=None,
                     before_release_write_barrier=None,
                     after_release_pipe_probe=None,
                     owned_group_census_override=None,
                     before_group_receipt_recheck_fifo=None,
                     after_group_receipt_recheck_fifo=None,
                     authority_fd_report=None):
    """Format the PRODUCTION template around a workload we can watch."""
    rundir = tmp_path / "rundir"
    workload = tmp_path / "workload.sh"
    workload.write_text(workload_body, encoding="utf-8")
    cmd = "bash " + shlex.quote(str(workload))
    body = mod.RUNNER.format(
        rundir=shlex.quote(str(rundir)),
        run_id=shlex.quote(os.urandom(16).hex()),
        cmd=cmd,
        purpose=shlex.quote("row212-w1"),
        origin=shlex.quote("pytest-w1"),
        cmdq=shlex.quote(cmd),
        registration_probe=shlex.quote(
            getattr(mod, "REGISTRATION_PROBE_PROGRAM", "")),
        registration_gate=shlex.quote(
            (getattr(mod, "REGISTRATION_GATE_PROGRAM", "")
             if gate_program is None else gate_program)),
        registration_bootstrap=shlex.quote(
            getattr(mod, "REGISTRATION_GATE_BOOTSTRAP_PROGRAM", "")),
        registration_terminal_relay=shlex.quote(
            (getattr(mod, "REGISTRATION_TERMINAL_RELAY_PROGRAM", "")
             if terminal_relay_program is None else terminal_relay_program)),
        registration_timeout_owner=shlex.quote(
            (getattr(mod, "REGISTRATION_TIMEOUT_OWNER_PROGRAM", "")
             if timeout_owner_program is None else timeout_owner_program)),
        registration_channel_reader=shlex.quote(
            (getattr(mod, "REGISTRATION_CHANNEL_READER_PROGRAM", "")
             if channel_reader_program is None else channel_reader_program)),
        process_guard=shlex.quote(
            getattr(mod, "PROCESS_GUARD_PROGRAM", "")),
        workload_shim=shlex.quote(
            mod.registration_workload_shim(str(rundir), cmd)),
    )
    if forward_expiry_is_subject:
        assert reap_seconds is not None, (
            "a fixture declared forward-deadline expiry as its subject but "
            "provided no shortened deadline")
    if reap_seconds is not None:
        anchor = "W1_GATE_SECONDS=10"
        assert body.count(anchor) == 1, (
            "the production runner has no single finite gate-protocol deadline")
        shipped = _w1_runner_deadline_constants()["W1_GATE_SECONDS"]
        # ROW 325. Fifty-five sites inherited this old speed knob, usually at
        # three seconds, although expiry was their subject at only four. Under
        # xdist the short clock fired before an otherwise-correct fixture could
        # reach its transition. An ordinary request is therefore clamped to
        # the production value; only an explicit expiry control may shorten it.
        effective = reap_seconds if forward_expiry_is_subject else shipped
        body = body.replace(anchor, "W1_GATE_SECONDS=%d" % effective)
    if ready_seconds is not None:
        ready_anchor = "W1_READY_SECONDS=10"
        assert body.count(ready_anchor) == 1, (
            "the production runner has no finite READY-owner deadline")
        body = body.replace(
            ready_anchor, "W1_READY_SECONDS=%d" % ready_seconds)
    if cleanup_seconds is not None:
        # THE SETTLEMENT DEADLINE, DRIVEN SEPARATELY FROM THE FORWARD ONE.
        # Before v3.66.1230 `reap_seconds` moved both, so a test that wanted
        # the FORWARD gate protocol to give up in 3s also gave every
        # settlement path 3s, and a settlement path that runs out of time
        # replaces a decided status with 92. Only a test whose SUBJECT is the
        # cleanup timeout should set this.
        cleanup_anchor = "W1_CLEANUP_SECONDS=%d" % (
            _w1_runner_deadline_constants()["W1_CLEANUP_SECONDS"])
        assert body.count(cleanup_anchor) == 1, (
            "the production runner has no single finite settlement deadline")
        body = body.replace(
            cleanup_anchor, "W1_CLEANUP_SECONDS=%d" % cleanup_seconds)
    if observation_seconds is not None:
        # THE OWNER-OBSERVATION FLOOR, DRIVEN SEPARATELY FROM EVERY
        # FORWARD DEADLINE. Before v3.66.1241 an owner OBSERVATION -- the
        # fd observer, the process observer, the group observer and the
        # descendant census that follows every owned helper -- was bounded
        # by WHAT REMAINED of the active forward deadline, so a test that
        # wanted the gate protocol to give up in 3s also told a helper it
        # had about a second and a half to ANSWER A QUESTION. Expiry there
        # is a FALSE VERDICT: the observation returns UNKNOWN and a
        # correct run settles into retained uncertainty. Only a test whose
        # SUBJECT is an observation expiring should set this.
        observation_anchor = "W1_OWNER_OBSERVATION_SECONDS=%d" % (
            _w1_runner_deadline_constants()["W1_OWNER_OBSERVATION_SECONDS"])
        assert body.count(observation_anchor) == 1, (
            "the production runner has no single owner-observation floor")
        body = body.replace(
            observation_anchor,
            "W1_OWNER_OBSERVATION_SECONDS=%d" % observation_seconds)
    if registrar_seconds is not None:
        registrar_anchor = "W1_REGISTRAR_SECONDS=30"
        assert body.count(registrar_anchor) == 1, (
            "the production runner has no finite registrar-owner deadline")
        body = body.replace(
            registrar_anchor, "W1_REGISTRAR_SECONDS=%d" % registrar_seconds)
    if reconcile_seconds is not None:
        reconcile_anchor = "W1_RECONCILE_SECONDS=10"
        assert body.count(reconcile_anchor) == 1, (
            "the production runner has no finite reconciliation deadline")
        body = body.replace(
            reconcile_anchor, "W1_RECONCILE_SECONDS=%d" % reconcile_seconds)
    if owner_kill_grace_us is not None:
        grace_anchor = "W1_OWNER_KILL_GRACE_US=100000"
        assert body.count(grace_anchor) == 1, (
            "the production runner has no unique positive owner KILL grace")
        body = body.replace(
            grace_anchor,
            "W1_OWNER_KILL_GRACE_US=%d" % int(owner_kill_grace_us),
        )
    if proc_stat_path is not None:
        anchor = '"/proc/$PYTEST_PID/stat"'
        assert body.count(anchor) >= 1, (
            "the production runner has no process-observation path to drive")
        body = body.replace(anchor, shlex.quote(str(proc_stat_path)), 1)
    if gate_prelude is not None:
        anchor = "coproc W1_REGISTRATION_GATE {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no single blocked registration gate; "
            "the group-settlement schedule is unreachable")
        body = body.replace(anchor, anchor + gate_prelude.rstrip() + "\n", 1)
    if pytest_pid_override is not None:
        anchors = ("PYTEST_PID=$PYTEST_GATE_PID",
                   "PYTEST_PID=$W1_REGISTRATION_GATE_PID", "PYTEST_PID=$!")
        found = [anchor for anchor in anchors if body.count(anchor) == 1]
        assert len(found) == 1, (
            "the production runner has no unique launched-child pid assignment")
        body = body.replace(
            found[0], "PYTEST_PID=%d" % int(pytest_pid_override), 1)
    if missing_setup_fd is not None:
        aliases = {
            "read": "gate_read",
            "write": "gate_write",
        }
        missing_setup_fd = aliases.get(missing_setup_fd, missing_setup_fd)
        fd_specs = {
            "gate_read": ("W1_GATE_READ_FD", "<&-"),
            "gate_write": ("W1_GATE_WRITE_FD", ">&-"),
            "terminal_read": ("W1_TERMINAL_READ_FD", "<&-"),
            "terminal_write": ("W1_TERMINAL_PARENT_WRITE_FD", ">&-"),
        }
        assert missing_setup_fd in fd_specs
        name, operator = fd_specs[missing_setup_fd]
        anchor = "PYTEST_PID=$PYTEST_GATE_PID"
        assert body.count(anchor) == 1
        body = body.replace(
            anchor,
            'eval "exec $%s%s"\n%s=""\n' % (name, operator, name) + anchor,
            1,
        )
    if missing_setup_pid is not None:
        assert missing_setup_pid in {"gate", "relay"}
        name = ("PYTEST_GATE_PID" if missing_setup_pid == "gate"
                else "W1_TERMINAL_RELAY_PID")
        anchor = "PYTEST_PID=$PYTEST_GATE_PID"
        assert body.count(anchor) == 1
        body = body.replace(
            anchor,
            "builtin printf '%s\\n' \"$PYTEST_GATE_PID\" > "
            "\"$RUNDIR/injected-gate.pid\"\n"
            "builtin printf '%s\\n' \"$W1_TERMINAL_RELAY_PID\" > "
            "\"$RUNDIR/injected-relay.pid\"\n"
            "%s=\"\"\n%s" % ("%s", "%s", name, anchor),
            1,
        )
    if checked_wait_probe is not None:
        anchor = "registration_checked_gate_wait() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique checked-wait owner to probe")
        body = body.replace(
            anchor,
            anchor + "    builtin printf 'entered\\n' >> %s\n" %
            shlex.quote(str(checked_wait_probe)),
            1,
        )
    if owned_group_census_override is not None:
        assert owned_group_census_override in {
            "ABSENT", "UNKNOWN", "AUXILIARY-ABSENT"}
        anchor = "registration_owned_group_census() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique owned-group census boundary")
        if owned_group_census_override == "AUXILIARY-ABSENT":
            injected = (
                '    if [ "$1" != gate ]; then\n'
                "        W1_OWNED_GROUP_STATUS=ABSENT\n"
                "        return 0\n"
                "    fi\n"
            )
        else:
            injected = "    W1_OWNED_GROUP_STATUS=%s\n    return 0\n" % (
                owned_group_census_override)
        body = body.replace(anchor, anchor + injected, 1)
    if after_group_receipt_recheck_fifo is not None:
        # A barrier AFTER the deciding probe, not merely before it. Releasing
        # the pre-probe barrier only proves the runner was WOKEN; it does not
        # prove it reached the probe, and under load the gate can exit in the
        # gap. That gap is exactly how the ABSENT control failed on a loaded
        # 48-core host and on a 2-core CI runner. Reading this fifo proves the
        # receipt recheck has already run.
        anchor = (
            "    W1_OWNER_FDS_BY_PID[$PYTEST_GATE_PID]=UNKNOWN\n"
            "fi\n")
        assert body.count(anchor) == 1, (
            "the production runner has no unique gate receipt-recheck exit")
        body = body.replace(
            anchor,
            anchor + "builtin printf 'gate-receipt-recheck-done\\n' > %s\n" %
            shlex.quote(str(after_group_receipt_recheck_fifo)),
            1,
        )
    if before_group_receipt_recheck_fifo is not None:
        anchor = (
            'registration_cancel_checkpoint "after-gate-acquire"\n\n'
            "W1_GATE_GROUP_READY_AT_ACQUIRE=0")
        assert body.count(anchor) == 1, (
            "the production runner has no unique gate-receipt recheck boundary")
        body = body.replace(
            anchor,
            'registration_cancel_checkpoint "after-gate-acquire"\n'
            "IFS= read -r W1_TEST_GATE_POST_SETSID < %s\n\n"
            "W1_GATE_GROUP_READY_AT_ACQUIRE=0" %
            shlex.quote(str(before_group_receipt_recheck_fifo)),
            1,
        )
    if cancel_before_observation:
        anchor = "registration_observation_matches_original() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique identity boundary to inject")
        body = body.replace(
            anchor, anchor + "    W1_CANCEL_STATUS=130\n", 1)
    if cancel_registered_failure:
        anchor = "registration_fail_registered() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no unique registered-failure funnel")
        body = body.replace(
            anchor, anchor + "    W1_CANCEL_STATUS=130\n", 1)
    if after_relay_acquire_barrier is not None:
        entered, release = after_relay_acquire_barrier
        anchor = 'registration_cancel_checkpoint "after-relay-acquire"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique post-relay authority boundary")
        body = body.replace(
            anchor,
            "registration_process_receipt \"$W1_TERMINAL_RELAY_PID\"\n"
            "builtin printf '%s\\n' \"$W1_PROCESS_RECEIPT\" > "
            "\"$RUNDIR/injected-relay.receipt\"\n"
            "builtin printf 'relay-acquired\\n' > %s\n"
            "IFS= read -r W1_TEST_RELAY_RELEASE < %s\n%s" % (
                "%s", shlex.quote(str(entered)),
                shlex.quote(str(release)), anchor),
            1,
        )
    if abnormal_owner_fifo is not None:
        anchor = (
            'W1_ACTIVE_OWNER_PID="$W1_SPAWN_PID"\n'
            '    W1_ACTIVE_OWNER_GROUP_READY=0\n'
            '    W1_ACTIVE_OWNER_RECEIPT=UNKNOWN')
        assert body.count(anchor) == 1, (
            "the production owner has no unique post-acquisition boundary")
        body = body.replace(
            anchor,
            anchor
            + "\n    if [ \"$W1_SPAWN_ROLE\" = ready-reader ]; then\n"
              "        IFS= read -r W1_TEST_OWNER_ACQUIRED < %s\n"
              "        : \"$W1_INJECTED_EXIT_UNSET\"\n"
              "    fi" %
              shlex.quote(str(abnormal_owner_fifo)),
            1,
        )
    if after_terminal_owner_ready_barrier is not None:
        entered, release, owner_pid_path = after_terminal_owner_ready_barrier
        anchor = (
            "    registration_promote_spawn_group_receipt || :\n"
            "    IFS= read -r -t \"$W1_SPAWN_TIMEOUT\" W1_SPAWN_EXTRA \\\n")
        assert body.count(anchor) == 1, (
            "the production owner has no unique post-READY promotion boundary")
        ready_mutation = (
            "        W1_TEST_READY_CLAIM=\"${W1_SPAWN_READY_LINE#OWNER-READY v2 receipt=}\"\n"
            "        W1_TEST_READY_CLAIM=\"${W1_TEST_READY_CLAIM% fds=0,1,2}\"\n"
            "        W1_TEST_READY_START=\"${W1_TEST_READY_CLAIM##*:}\"\n"
            "        W1_TEST_READY_CLAIM=\"${W1_TEST_READY_CLAIM%:*}:$((W1_TEST_READY_START + 1))\"\n"
            "        W1_SPAWN_READY_LINE=\"OWNER-READY v2 receipt=$W1_TEST_READY_CLAIM fds=0,1,2\"\n"
            if mutate_terminal_owner_ready else "")
        injection = (
            '    if [ "$W1_SPAWN_ROLE" = terminal-reader ]; then\n'
            + ready_mutation
            + "        builtin printf '%s\\n' \"$W1_SPAWN_PID\" > %s\n"
            "        builtin printf 'owner-ready-read\\n' > %s\n"
            "        IFS= read -r W1_TEST_OWNER_RELEASE < %s\n"
            "    fi\n" % (
                "%s", shlex.quote(str(owner_pid_path)),
                shlex.quote(str(entered)), shlex.quote(str(release))))
        body = body.replace(anchor, injection + anchor, 1)
    if handoff_deadline_probe is not None:
        before = 'registration_cancel_checkpoint "pre-release"'
        after = "registration_read_terminal\nW1_HANDOFF_FRAME=\"$W1_FRAME\""
        assert body.count(before) == 1 and body.count(after) == 1, (
            "the production handoff has no unique deadline snapshot boundaries")
        body = body.replace(
            before,
            before + "\nbuiltin printf 'pre=%s\\n' "
            '"$W1_ACTIVE_DEADLINE_US" > ' +
            shlex.quote(str(handoff_deadline_probe)),
            1,
        )
        body = body.replace(
            after,
            "builtin printf 'post=%s\\n' \"$W1_ACTIVE_DEADLINE_US\" >> "
            + shlex.quote(str(handoff_deadline_probe)) + "\n" + after,
            1,
        )
    if before_release_write_barrier is not None:
        entered, release = before_release_write_barrier
        anchor = 'registration_cancel_checkpoint "pre-release"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique pre-release authority boundary")
        body = body.replace(
            anchor,
            anchor
            + "\nbuiltin printf 'release-write-entered\\n' > %s\n"
              "IFS= read -r W1_TEST_RELEASE_WRITE < %s" % (
                  shlex.quote(str(entered)), shlex.quote(str(release))),
            1,
        )
    if after_release_pipe_probe is not None:
        anchor = 'registration_cancel_checkpoint "post-release-write"'
        assert body.count(anchor) == 1, (
            "the production runner has no unique release PIPE restoration edge")
        body = body.replace(
            anchor,
            "trap -p PIPE > %s\n%s" % (
                shlex.quote(str(after_release_pipe_probe)), anchor),
            1,
        )
    if authority_fd_report is not None:
        # Record the shell-assigned descriptors only after READY has handed the
        # status side to the terminal channel.  A fixed descriptor number is an
        # ambient host assumption: the interpreter may already hold it open.
        anchor = (
            "registration_handover_terminal_reader\n"
            'if [ "$W1_READY_OK" -eq 0 ]; then\n')
        assert body.count(anchor) == 1, (
            "the production runner has no unique authority-fd handover edge")
        report_path = pathlib.Path(authority_fd_report)
        report_tmp = report_path.with_name(report_path.name + ".tmp")
        report_command = (
            "if ! builtin printf 'release=%%s\\nstatus=%%s\\n' "
            '"$W1_GATE_WRITE_FD" "$W1_GATE_READ_FD" > %s \\\n'
            "        || ! mv %s %s; then\n"
            "    exit 95\n"
            "fi\n" % (
                shlex.quote(str(report_tmp)),
                shlex.quote(str(report_tmp)),
                shlex.quote(str(report_path)),
            )
        )
        body = body.replace(
            anchor,
            "registration_handover_terminal_reader\n"
            + report_command
            + 'if [ "$W1_READY_OK" -eq 0 ]; then\n',
            1,
        )
    if monotonic_samples is not None:
        samples = [int(value) for value in monotonic_samples]
        assert samples
        anchor = "registration_monotonic_sample() {\n"
        assert body.count(anchor) == 1, (
            "the production runner has no injectable monotonic sample boundary")
        end = "}\n\nregistration_now_us() {\n"
        start_at = body.index(anchor)
        end_at = body.index(end, start_at)
        cases = "\n".join(
            "        %d) W1_CLOCK_SAMPLE_US=%d ;;" % (index, value)
            for index, value in enumerate(samples)
        )
        replacement = (
            "W1_CLOCK_SAMPLE_INDEX=0\n"
            "registration_monotonic_sample() {\n"
            "    case \"$W1_CLOCK_SAMPLE_INDEX\" in\n%s\n"
            "        *) W1_CLOCK_SAMPLE_US=%d ;;\n"
            "    esac\n"
            "    W1_CLOCK_SAMPLE_INDEX=$((W1_CLOCK_SAMPLE_INDEX + 1))\n"
            "}\n\nregistration_now_us() {\n" % (cases, samples[-1])
        )
        body = body[:start_at] + replacement + body[end_at + len(end):]
    script = tmp_path / "runner.sh"
    script.write_text(body, encoding="utf-8")
    return script, rundir


def _w1_adversarial_gate_program(*, ready=None, terminal=None,
                                 terminal_bytes=None, delay_before_terminal=0.0,
                                 terminal_suffix=None, delay_before_suffix=0.0,
                                 terminal_suffix_entered=None,
                                 terminal_suffix_release=None,
                                 hold=0.0, status=0, extra_ready=None,
                                 delay_before_ready=0.0,
                                 delay_before_extra_ready=0.0,
                                 nul_ready=False, before_ready_marker=None,
                                 ready_release=None, ready_written_marker=None,
                                 extra_ready_release=None):
    """A real pipe peer for protocol-boundary schedules, not a runner mock."""
    assert (terminal is None) != (terminal_bytes is None)
    ready_expr = ("'READY v1 pid=%d' % os.getpid()"
                  if ready is None else repr(ready))
    ready_stmt = (
        "os.write(1, b'READY\\x00 v1 pid=%d\\n' % os.getpid())"
        if nul_ready else "emit(1, %s)" % ready_expr
    )
    terminal_stmt = (
        "emit(3, %r)" % terminal if terminal is not None
        else "os.write(3, %r)" % bytes(terminal_bytes)
    )
    if terminal_suffix is None:
        suffix_stmt = ""
    elif (terminal_suffix_entered is not None
          and terminal_suffix_release is not None):
        suffix_stmt = (
            "with open(%r, 'w', encoding='utf-8') as stream:\n"
            "    stream.write('partial-terminal-written\\n')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n"
            "os.write(3, %r)\n" % (
                str(terminal_suffix_entered), str(terminal_suffix_release),
                bytes(terminal_suffix))
        )
    else:
        suffix_stmt = "time.sleep(%r)\nos.write(3, %r)\n" % (
            float(delay_before_suffix), bytes(terminal_suffix))
    if extra_ready is None:
        extra_ready_stmt = ""
    elif ready_written_marker is not None and extra_ready_release is not None:
        extra_ready_stmt = (
            "pathlib.Path(%r).write_text('ready-written')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n"
            "try:\n"
            "    emit(1, %r)\n"
            "except BrokenPipeError:\n"
            "    pass\n" %
            (str(ready_written_marker), str(extra_ready_release), extra_ready)
        )
    else:
        extra_ready_stmt = (
            "time.sleep(%r)\nemit(1, %r)\n" %
            (float(delay_before_extra_ready), extra_ready)
        )
    before_ready_stmt = ""
    if before_ready_marker is not None and ready_release is not None:
        before_ready_stmt = (
            "pathlib.Path(%r).write_text('before-ready')\n"
            "with open(%r, 'r', encoding='utf-8') as stream:\n"
            "    stream.readline()\n" %
            (str(before_ready_marker), str(ready_release))
        )
    return (
        "import os, pathlib, sys, time\n"
        "def emit(fd, frame):\n"
        "    data = (frame + '\\n').encode('ascii')\n"
        "    while data:\n"
        "        written = os.write(fd, data)\n"
        "        data = data[written:]\n"
        "%s"
        "time.sleep(%r)\n"
        "%s\n"
        "%s"
        "os.close(1)\n"
        "release = bytearray()\n"
        "while True:\n"
        "    chunk = os.read(0, 4096)\n"
        "    if not chunk:\n"
        "        break\n"
        "    release.extend(chunk)\n"
        "time.sleep(%r)\n"
        "%s\n"
        "%s"
        "time.sleep(%r)\n"
        "raise SystemExit(%d)\n"
        % (before_ready_stmt, float(delay_before_ready), ready_stmt, extra_ready_stmt,
           float(delay_before_terminal), terminal_stmt,
           suffix_stmt, float(hold), int(status))
    )


def _w1_slow_settlement_gate_program(marker, seconds):
    """A gate that settles CORRECTLY, and not INSTANTLY.

    The production gate answers a closed release pipe by emitting
    `ABORTED v1 reason=release-eof` and exiting. This one does exactly that
    after a bounded delay, so the runner's SETTLEMENT deadline -- and nothing
    else -- decides whether the settlement completes. The marker proves the
    delay was entered, so a run that never reached this branch cannot pass for
    either verdict.

    `seconds` is a FORCED-BRANCH constant, not a budget: it is chosen to sit
    above the 3s the pre-v3.66.1230 runner gave settlement and well under the
    10s it gives now, which is the whole point of the pair of tests below.
    """
    return (
        "import os, sys, time\n"
        "def emit(fd, frame):\n"
        "    os.write(fd, (frame + '\\n').encode('ascii', 'strict'))\n"
        "if len(sys.argv) != 2:\n"
        "    emit(3, 'ABORTED v1 reason=bad-shim-argv')\n"
        "    raise SystemExit(95)\n"
        "emit(1, 'READY v1 pid=%d' % os.getpid())\n"
        "os.close(1)\n"
        "release = bytearray()\n"
        "while True:\n"
        "    chunk = os.read(0, 4096)\n"
        "    if not chunk:\n"
        "        break\n"
        "    release.extend(chunk)\n"
        "with open(" + repr(str(marker)) + ", 'w') as handle:\n"
        "    handle.write('settlement-entered\\n')\n"
        "time.sleep(" + repr(float(seconds)) + ")\n"
        "emit(3, 'ABORTED v1 reason=release-eof')\n"
        "raise SystemExit(0)\n"
    )


#: How long the planted gate takes to settle in the pair of tests below. It is
#: deterministically ABOVE the 3 seconds the shared constant used to leave for
#: settlement and deterministically BELOW the 10 the runner now gives it, so
#: neither arm depends on host speed to reach its branch.
_W1_SLOW_SETTLEMENT_S = 5.0


def _w1_cancel_a_registrar_bound_runner(tmp_path, mod, *, gate_program,
                                        cleanup_seconds=None):
    """Drive the runner to the point where only settlement remains, then INT.

    The registrar stub sleeps for five minutes, so no job id is ever recorded
    and `registration_settle_cancel` -- not `registration_fail_registered` --
    is the path that classifies the run. That is the exact shape whose
    misclassification v3.66.1226 recorded and left open.
    """
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, registrar_seconds=3, cleanup_seconds=cleanup_seconds,
        gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n", sleep=300))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    return marker, registrar, rundir, proc


def _w1_pre_ready_descendant_gate_program():
    """Fork one hostile descendant before presenting an otherwise exact READY."""
    return (
        "import os, signal, sys, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(0)\n"
        "    os.close(1)\n"
        "    os.close(3)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "    time.sleep(300)\n"
        "    raise SystemExit(0)\n"
        "os.write(1, ('READY v1 pid=%d\\n' % os.getpid()).encode('ascii'))\n"
        "os.close(1)\n"
        "while os.read(0, 4096):\n"
        "    pass\n"
        "os.write(3, b'ABORTED v1 reason=release-eof\\n')\n"
        "raise SystemExit(0)\n"
    )


def _w1_kill_group(pgid: int) -> None:
    """Kill a group, refusing to aim at our own. Never raises."""
    try:
        if pgid <= 0 or pgid == os.getpgid(0):
            return
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _w1_proc_receipt(pid: int, raw: str | None = None) -> tuple[int, int, int]:
    """Return the hand-derived stable receipt: pid, process group, start time."""
    if raw is None:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
    tail = raw[raw.rindex(") ") + 2:].split()
    assert len(tail) > 19, f"short /proc stat fixture for pid {pid}: {raw!r}"
    return pid, int(tail[2]), int(tail[19])


def _w1_proc_observation(
        pid: int, raw: str | None = None) -> tuple[int, int, int, int, str]:
    """Hand-derive pid, ppid, pgrp, starttime and state from one stat row."""
    if raw is None:
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(encoding="utf-8")
    head, delimiter, tail_text = raw.rpartition(") ")
    assert delimiter and " (" in head, f"malformed /proc stat fixture: {raw!r}"
    observed_pid = int(head.split(" (", 1)[0])
    tail = tail_text.split()
    assert len(tail) > 19, f"short /proc stat fixture for pid {pid}: {raw!r}"
    return observed_pid, int(tail[1]), int(tail[2]), int(tail[19]), tail[0]


def _w1_change_proc_starttime(raw: str) -> str:
    """Model PID reuse by changing only field 22 of one complete stat row."""
    split_at = raw.rindex(") ") + 2
    tail = raw[split_at:].split()
    assert len(tail) > 19, f"short /proc stat fixture: {raw!r}"
    tail[19] = str(int(tail[19]) + 1)
    return raw[:split_at] + " ".join(tail) + "\n"


def _w1_change_proc_field(raw: str, *, ppid=None, pgrp=None, starttime=None,
                          state=None) -> str:
    """Change explicit stat fields without borrowing the production parser."""
    split_at = raw.rindex(") ") + 2
    tail = raw[split_at:].split()
    assert len(tail) > 19, f"short /proc stat fixture: {raw!r}"
    if state is not None:
        tail[0] = str(state)
    if ppid is not None:
        tail[1] = str(ppid)
    if pgrp is not None:
        tail[2] = str(pgrp)
    if starttime is not None:
        tail[19] = str(starttime)
    return raw[:split_at] + " ".join(tail) + "\n"


def _w1_stat_row(pid: int, *, ppid: int, pgrp: int, starttime: int,
                 state: str = "S", comm: str = "worker") -> str:
    """One hand-positioned Linux stat row for parser boundary tests."""
    tail = [
        state, str(ppid), str(pgrp), str(pgrp), "0", "-1", "4194304",
        "1", "2", "3", "4", "5", "6", "7", "8", "20", "0",
        "1", "0", str(starttime), "4096", "10", "0",
    ]
    assert tail[1] == str(ppid) and tail[2] == str(pgrp)
    assert tail[19] == str(starttime)
    return "%d (%s) %s\n" % (pid, comm, " ".join(tail))


def _w1_readlink_when_installed(pid: int, fd: int, *, timeout=_w1_budget_s("_w1_readlink_when_installed/default")) -> str:
    """Read /proc/<pid>/fd/<fd> only once the gate has actually installed it.

    ROW 222'S SHAPE, ONE LEVEL UP. `_w1_wait_for_gate` proves a PARSEABLE PID
    and a LIVE GROUP. It does not prove the gate has finished installing its
    descriptors, and a reader that assumes it did is making the same
    existence-is-not-content mistake this cut exists to remove -- here it is
    pid-is-published is not descriptors-are-open.

    THIS WAS LATENT UNTIL THE PUBLICATION BECAME ATOMIC, which is why it is
    fixed in the same cut. `echo "$PYTEST_PID" > pytest.pid` created the file
    and wrote it in two steps, so `_w1_wait_for_gate`'s `.isdigit()` guard
    rejected the empty file and slept another tick. That accidental delay was
    load-bearing: it gave the gate time to open fd 3. Publishing atomically
    makes the pid readable on the FIRST observation and hands the reader a gate
    that may not have got there yet -- measured on CI as
    `FileNotFoundError: /proc/<pid>/fd/3`. Removing an accidental delay is not
    a regression to paper over with a sleep; the missing assertion is the bug.
    """
    path = "/proc/%d/fd/%d" % (pid, fd)
    deadline = time.monotonic() + timeout
    while True:
        try:
            return os.readlink(path)
        except FileNotFoundError:
            if not _w1_pid_is_live(pid):
                raise AssertionError(
                    "the gate exited before installing fd %d; %s never appeared"
                    % (fd, path))
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "the gate stayed alive but never installed fd %d within "
                    "%.1fs (%s)" % (fd, timeout, path))
            time.sleep(0.005)


def _w1_wait_for_gate(rundir, *, minimum_live=1, timeout=_w1_budget_s("_w1_wait_for_gate/default")):
    """Return the real launched gate pid after proving its group is live."""
    deadline = time.time() + timeout
    pid = -1
    live: list[str] = []
    while time.time() < deadline:
        pidfile = rundir / "pytest.pid"
        if pidfile.is_file() and pidfile.read_text().strip().isdigit():
            pid = int(pidfile.read_text().strip())
            # PROBE CHEAPLY, CENSUS ONCE. The presence probe is leader-first and
            # stops at the threshold, so a poll that finds nothing costs one
            # /proc walk instead of a fork; the COMPLETE list is then taken once,
            # because five callers read it and a truncated list would be a
            # different answer, not a faster one.
            if _w1_group_has_at_least(pid, minimum_live):
                live = _w1_live_in_group(pid)
                if len(live) >= minimum_live:
                    return pid, live
        time.sleep(0.01)
    raise AssertionError(
        "the runner never established the required real child-led group: "
        f"pid={pid}, live={live!r}")


def _w1_wait_for_exit(proc, rundir, *, site, forbidden=None):
    """Collect the runner from durable state, with wall time only as a guard.

    Receipt checks and owned-process censuses make elapsed time host- and
    scheduler-dependent.  The runner's durable ``exitcode`` is the semantic
    completion signal; a fixed short ``wait`` is not.

    THE EMERGENCY WATCHDOG IS NOW PER CALL SITE. The 20.0s shared default this
    replaced covered call sites measuring 0.712s to 7.123s, and 7.123s against
    20s is 2.83x -- inside the band that fired on correct work on 2026-08-24.
    It is a monotonic deadline rather than a subprocess timeout, so the
    instrumented boundary cannot see it and this polices itself.
    """
    watchdog = _w1_budget_s(site)
    started = time.monotonic()
    deadline = started + float(watchdog)
    exitcode = rundir / "exitcode"
    while time.monotonic() < deadline:
        if forbidden is not None and forbidden.exists():
            raise AssertionError(
                "terminal bytes without authority entered checked child wait")
        if exitcode.is_file() or proc.poll() is not None:
            proc.communicate(timeout=_w1_budget_s("_w1_wait_for_exit/communicate"))
            _w1_police(watchdog, time.monotonic() - started)
            return proc.returncode
        time.sleep(0.01)
    raise AssertionError(
        "runner produced neither a durable exit record nor the forbidden "
        "checked-wait transition before the emergency watchdog of %.1fs at "
        "site %r" % (float(watchdog), site))


def _w1_wait_for_exit_or_forbidden_checked_wait(
        proc, rundir, checked_wait_probe, *, site):
    """Reject a forbidden checked wait while awaiting durable completion."""
    assert not checked_wait_probe.exists(), (
        "forbidden checked wait occurred before the oracle started")
    return _w1_wait_for_exit(
        proc, rundir, site=site, forbidden=checked_wait_probe)


def _w1_signal_probe(tmp_path, *, result=0, passthrough=False,
                     capture_receipt=False):
    """Intercept only the production group SIGKILL; leave liveness real."""
    signal_log = tmp_path / "runner-signal-attempts"
    receipt_log = tmp_path / "runner-signal-receipts"
    bash_env = tmp_path / "probe-runner-signal.bash"
    function_body = (
        "kill() {\n"
        "    if [ \"$1\" = \"-9\" ] || [[ \"${1-}\" =~ ^[0-9]+$ ]]; then\n"
        "        printf '%s\\n' \"$*\" >> \"$W1_SIGNAL_LOG\"\n"
        + ("        target=${2:-$1}\n"
           "        target=${target#-}\n"
           "        cat \"/proc/$target/stat\" >> \"$W1_SIGNAL_RECEIPT_LOG\"\n"
           if capture_receipt else "")
        + ("        builtin kill \"$@\"\n"
           "        return $?\n" if passthrough else
           "        return %d\n" % int(result))
        + "    fi\n"
          "    builtin kill \"$@\"\n"
          "}\n"
          "export -f kill\n"
    )
    bash_env.write_text(function_body, encoding="utf-8")
    if capture_receipt:
        assert not receipt_log.exists()
    return bash_env, signal_log


def _w1_process_probe_drift(tmp_path, field: str, *, after_calls: int,
                            mutate_group: bool = True,
                            one_shot: bool = False):
    """Change one production observer field after N exact real observations."""
    assert field in {"ppid", "pgrp", "starttime", "state"}
    counter = tmp_path / "process-probe-count"
    bash_env = tmp_path / "process-probe-drift.bash"
    mutations = {
        "ppid": "ppid=$((ppid + 1))",
        "pgrp": "pgrp=$((pgrp + 1))",
        "starttime": "start=$((start + 1))",
        "state": "state=Z",
    }
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        local out rc count payload receipt state pid ppid pgrp start\n"
        "        out=$(command python3 \"$@\"); rc=$?\n"
        "        count=$(cat %s 2>/dev/null || echo 0)\n"
        "        echo $((count + 1)) > %s\n"
        "        if [ \"$count\" %s %d ] && [[ \"$out\" == OBSERVED\\|*\\|* ]]; then\n"
        "            payload=${out#OBSERVED|}\n"
        "            receipt=${payload%%|*}\n"
        "            state=${payload##*|}\n"
        "            IFS=: read -r pid ppid pgrp start <<< \"$receipt\"\n"
        "            %s\n"
        "            builtin printf 'OBSERVED|%%s:%%s:%%s:%%s|%%s\\n' "
        "\"$pid\" \"$ppid\" \"$pgrp\" \"$start\" \"$state\"\n"
        "        else\n"
        "            builtin printf '%%s\\n' \"$out\"\n"
        "        fi\n"
        "        return \"$rc\"\n"
        "    elif [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'group' ]; then\n"
        "        local out rc payload pid ppid pgrp start state\n"
        "        out=$(command python3 \"$@\"); rc=$?\n"
        "        if [[ \"$out\" == PRESENT\\|* ]] "
        "&& [[ \"${out#PRESENT|}\" != *,* ]]; then\n"
        "            payload=${out#PRESENT|}\n"
        "            IFS=: read -r pid ppid pgrp start state <<< \"$payload\"\n"
        "            %s\n"
        "            builtin printf 'PRESENT|%%s:%%s:%%s:%%s:%%s\\n' "
        "\"$pid\" \"$ppid\" \"$pgrp\" \"$start\" \"$state\"\n"
        "        else\n"
        "            builtin printf '%%s\\n' \"$out\"\n"
        "        fi\n"
        "        return \"$rc\"\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(counter)), shlex.quote(str(counter)),
            "-eq" if one_shot else "-ge", int(after_calls), mutations[field],
            mutations[field] if mutate_group else ":"),
        encoding="utf-8",
    )
    return bash_env, counter


def _w1_block_process_probe(tmp_path, *, on_call: int):
    """Block one real process-observer call until the test releases it."""
    counter = tmp_path / "process-probe-count"
    entered = tmp_path / "process-probe-entered"
    release = tmp_path / "process-probe-release"
    os.mkfifo(release)
    bash_env = tmp_path / "process-probe-block.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        local count token\n"
        "        count=$(cat %s 2>/dev/null || echo 0)\n"
        "        echo $((count + 1)) > %s\n"
        "        if [ \"$count\" -eq %d ]; then\n"
        "            : > %s\n"
        "            IFS= read -r token < %s\n"
        "        fi\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(counter)), shlex.quote(str(counter)),
            int(on_call) - 1, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered, release


def _w1_hung_process_probe(tmp_path):
    """Make one observer and its child ignore TERM behind an exact barrier."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "hung-process-probe")
    helper_pid = tmp_path / "hung-process-probe.pid"
    child_pid = tmp_path / "hung-process-probe-child.pid"
    bash_env = tmp_path / "hung-process-probe.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'process' ]; then\n"
        "        trap '' TERM\n"
        "        builtin printf '%%s\\n' \"$BASHPID\" > %s\n"
        "        sleep 300 &\n"
        "        local W1_TEST_CHILD=$!\n"
        "        builtin printf '%%s\\n' \"$W1_TEST_CHILD\" > %s\n"
        "        builtin printf 'observer-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_RELEASE < %s\n"
        "        builtin wait \"$W1_TEST_CHILD\"\n"
        "        return $?\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(str(helper_pid)), shlex.quote(str(child_pid)),
            shlex.quote(str(entered)), shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release, helper_pid, child_pid


def _w1_block_probe_fifo(tmp_path, mode: str):
    """Hold one real production observer at a FIFO-defined authority boundary."""
    assert mode in {"process", "group"}
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "%s-observer" % mode)
    bash_env = tmp_path / ("block-%s-observer.bash" % mode)
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${W1_TEST_PROBE_USED:-0}\" -eq 0 ] "
        "&& [ \"${1-}\" = '-c' ] && [ \"${3-}\" = %s ]; then\n"
        "        W1_TEST_PROBE_USED=1\n"
        "        builtin printf '%s-observer-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_PROBE_RELEASE < %s\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n" % (
            shlex.quote(mode), mode, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_run_registration_probe(mod, mode: str, *args: object, env=None):
    """Drive the production observer without borrowing its parser in tests."""
    return subprocess.run(
        [os.environ.get("PYTHON", "python3"), "-c",
         mod.REGISTRATION_PROBE_PROGRAM, mode, *map(str, args)],
        text=True, capture_output=True, env=env,
        timeout=_w1_budget_s("_w1_run_registration_probe/run"), check=False,
    )


def _w1_wait_for_path(
        path: pathlib.Path, *, content: str | None = None,
        timeout=_w1_budget_s("_w1_wait_for_path/default"),
) -> None | int | str:
    """Wait for existence, a parseable integer, or non-empty text.

    ``Path.write_text`` opens with truncation before it writes its payload, so
    existence is only a sufficient condition for callers that never consume
    the marker.  Content callers receive the value observed by this wait and
    therefore cannot reintroduce an unchecked second read.
    """
    expected = {
        None: None,
        "integer": "an integer",
        "text": "non-empty text",
    }
    if content not in expected:
        raise ValueError("unknown fixture marker content condition %r" % content)

    deadline = time.monotonic() + timeout
    appeared = False
    while True:
        if path.exists():
            appeared = True
            if content is None:
                return None
            try:
                payload = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                pass
            else:
                if content == "text" and payload:
                    return payload
                if content == "integer":
                    try:
                        return int(payload)
                    except ValueError:
                        pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))

    # Classify a marker that appeared at the deadline as a publication defect,
    # even if the last in-budget observation narrowly preceded its creation.
    appeared = appeared or path.exists()
    if not appeared:
        raise AssertionError(
            f"timed out waiting for fixture marker {path}: never appeared")
    raise AssertionError(
        "timed out waiting for fixture marker %s: appeared but never became "
        "readable as %s" % (path, expected[content]))


def _w1_delay_path_write(tmp_path, target: pathlib.Path):
    """Pause a real Path.write_text after open but before its payload write."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "delayed-path-write")
    shim = tmp_path / "delayed-path-write-shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import pathlib\n"
        "_target = %r\n"
        "_entered = %r\n"
        "_release = %r\n"
        "_original_write_text = pathlib.Path.write_text\n"
        "def _delayed_write_text(self, data, encoding=None, errors=None, newline=None):\n"
        "    candidate = str(self)\n"
        "    if candidate == _target or candidate.startswith(_target + '.tmp.'):\n"
        "        with self.open('w', encoding=encoding, errors=errors, newline=newline) as stream:\n"
        "            with open(_entered, 'w', encoding='ascii') as marker:\n"
        "                marker.write('payload-write-opened\\n')\n"
        "            with open(_release, 'r', encoding='ascii') as barrier:\n"
        "                barrier.readline()\n"
        "            return stream.write(data)\n"
        "    return _original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)\n"
        "pathlib.Path.write_text = _delayed_write_text\n" % (
            str(target), str(entered), str(release)),
        encoding="utf-8",
    )
    return shim, entered_fd, release


def _w1_prepend_pythonpath(env, path: pathlib.Path) -> None:
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(path) + (
        os.pathsep + inherited if inherited else "")


def test_fixture_marker_waits_for_content(tmp_path, monkeypatch):
    """An open marker is not ready until its delayed payload is visible."""
    target = tmp_path / "integer-marker"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, target)
    env = dict(os.environ)
    _w1_prepend_pythonpath(env, write_shim)
    writer = subprocess.Popen(
        [sys.executable, "-c",
         "import pathlib; pathlib.Path(%r).write_text('731', encoding='ascii')"
         % str(target)],
        env=env,
    )
    observed_empty = []
    try:
        assert _w1_await_fifo(
            payload_entered_fd,
            site="fixture_marker_waits_for_content/fifo",
        ) == "payload-write-opened\n"
        assert target.exists() and target.read_text(encoding="ascii") == "", (
            "the delayed writer did not expose the create-before-payload race")

        real_read_text = pathlib.Path.read_text

        def release_after_observing_empty(path, *args, **kwargs):
            payload = real_read_text(path, *args, **kwargs)
            if path == target and not observed_empty:
                assert payload == "", (
                    "the content wait did not inspect the held-open empty file")
                observed_empty.append(payload)
                _w1_release_fifo(payload_release)
            return payload

        monkeypatch.setattr(pathlib.Path, "read_text",
                            release_after_observing_empty)
        marker = _w1_wait_for_path(target, content="integer")

        assert observed_empty == [""]
        assert marker == 731
    finally:
        if not observed_empty:
            _w1_release_fifo(payload_release)
        os.close(payload_entered_fd)
        writer.wait(timeout=_w1_budget_s(
            "fixture_marker_waits_for_content/wait"))


def test_fixture_marker_timeout_distinguishes_absent_from_unreadable(
        tmp_path, monkeypatch):
    missing = tmp_path / "missing-marker"
    with monkeypatch.context() as clock:
        ticks = iter((0.0, _GOVERNING_BOUND_S))
        clock.setattr(time, "monotonic", lambda: next(ticks))
        with pytest.raises(AssertionError, match="never appeared") as absent:
            _w1_wait_for_path(missing, content="integer")
    assert "appeared but never became readable" not in str(absent.value)

    empty = tmp_path / "empty-marker"
    empty.write_text("", encoding="ascii")
    with monkeypatch.context() as clock:
        ticks = iter((0.0, _GOVERNING_BOUND_S))
        clock.setattr(time, "monotonic", lambda: next(ticks))
        with pytest.raises(
                AssertionError,
                match="appeared but never became readable as an integer",
        ) as unreadable:
            _w1_wait_for_path(empty, content="integer")
    assert "never appeared" not in str(unreadable.value)


def _w1_delay_shell_publish(tmp_path, target: pathlib.Path):
    """Pause old redirection or new rename at the same publication boundary."""
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "delayed-shell-publish")
    bash_env = tmp_path / "delayed-shell-publish.bash"
    bash_env.write_text(
        "_w1_test_hold_publish() {\n"
        "    builtin printf 'publish-boundary-entered\\n' > %s\n"
        "    IFS= read -r W1_TEST_PUBLISH_RELEASED < %s\n"
        "}\n"
        "echo() {\n"
        "    if [ \"/proc/$$/fd/1\" -ef %s ]; then\n"
        "        _w1_test_hold_publish\n"
        "    fi\n"
        "    builtin echo \"$@\"\n"
        "}\n"
        "mv() {\n"
        "    local W1_TEST_LAST_ARG=${!#}\n"
        "    if [ \"$W1_TEST_LAST_ARG\" = %s ]; then\n"
        "        _w1_test_hold_publish\n"
        "    fi\n"
        "    command mv \"$@\"\n"
        "}\n"
        "export -f _w1_test_hold_publish echo mv\n" % (
            shlex.quote(str(entered)), shlex.quote(str(release)),
            shlex.quote(str(target)), shlex.quote(str(target))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_fail_shell_publish(tmp_path, target: pathlib.Path):
    """Fail only the rename that would publish the selected target."""
    bash_env = tmp_path / "fail-shell-publish.bash"
    bash_env.write_text(
        "mv() {\n"
        "    local W1_TEST_LAST_ARG=${!#}\n"
        "    if [ \"$W1_TEST_LAST_ARG\" = %s ]; then\n"
        "        return 73\n"
        "    fi\n"
        "    command mv \"$@\"\n"
        "}\n"
        "export -f mv\n" % shlex.quote(str(target)),
        encoding="utf-8",
    )
    return bash_env


def _w1_fifo_barrier(tmp_path, name: str):
    """Create a scheduler-independent entered/release handshake."""
    entered = tmp_path / (name + "-entered")
    release = tmp_path / (name + "-release")
    os.mkfifo(entered)
    os.mkfifo(release)
    entered_fd = os.open(entered, os.O_RDONLY | os.O_NONBLOCK)
    return entered, release, entered_fd


def _w1_release_fifo(path, payload: str = "go\n", *,
                     timeout: float = _w1_budget_s("_w1_release_fifo/default")) -> None:
    """Release a runner blocked on `read < path`, WITHOUT risking a hang.

    A plain open-for-write on a fifo blocks until a reader arrives, so a runner
    that never reached the injected barrier would turn a failing assertion into
    a hung test. O_NONBLOCK turns "no reader yet" into ENXIO, which this retries
    to a deadline and then REPORTS -- an unreached barrier is a fixture failure
    with a name, not a timeout with none.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError as exc:
            if exc.errno != errno.ENXIO:
                raise
            assert time.monotonic() < deadline, (
                "the runner never reached the injected barrier at %s, so the "
                "precondition this control forces was never applied" % path)
            time.sleep(0.01)
    try:
        os.write(fd, payload.encode("ascii"))
    finally:
        os.close(fd)


def _w1_await_fifo(fd: int, *, site: str) -> str:
    """Wait for one fixture barrier frame under a PER-CALL-SITE budget.

    The 5.0s shared default this replaced was one key over 24 call sites whose
    measured cost spans 0.000s to 4.398s -- the same shape v3.66.1222 found
    wrong by 8x in its own table -- so every caller names its own site. The
    deadline is a select() timeout rather than a subprocess one, so the
    instrumented boundary cannot see it and this polices itself.
    """
    budget = _w1_budget_s(site)
    started = time.monotonic()
    readable, _, _ = select.select([fd], [], [], budget)
    assert readable, (
        "timed out waiting for deterministic fixture barrier at site %r after "
        "%.1fs" % (site, float(budget)))
    _w1_police(budget, time.monotonic() - started)
    payload = os.read(fd, 4096).decode("utf-8")
    assert payload, "fixture barrier reached EOF without an entered record"
    return payload


def _w1_gate_wait_barrier(tmp_path):
    """Block the first pre-release checked gate wait in the real runner."""
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "gate-wait")
    bash_env = tmp_path / "gate-wait-barrier.bash"
    bash_env.write_text(
        "wait() {\n"
        "    local W1_TEST_WAIT_TARGET=${3-}\n"
        "    [ \"${1-}\" = '-n' ] && W1_TEST_WAIT_TARGET=${4-}\n"
        "    if [ \"${W1_TEST_GATE_WAIT_USED:-0}\" -eq 0 ] "
        "&& [ \"${W1_RELEASE_WRITE_COUNT:-0}\" -eq 0 ] "
        "&& [ \"$W1_TEST_WAIT_TARGET\" = \"${PYTEST_GATE_PID:-missing}\" ]; then\n"
        "        W1_TEST_GATE_WAIT_USED=1\n"
        "        builtin printf 'gate-wait-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_GATE_WAIT_RELEASE < %s\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n" % (
            shlex.quote(str(entered)), shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_checked_child_wait_barrier(tmp_path, role: str):
    """Hold the real relay or workload checked wait at its saved PID."""
    assert role in {"terminal-relay", "workload"}
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, role + "-wait")
    bash_env = tmp_path / (role + "-wait-barrier.bash")
    if role == "terminal-relay":
        predicate = (
            "[ \"${1-}\" = '-n' ] && "
            "[ \"${4-}\" = \"${W1_TERMINAL_RELAY_PID:-missing}\" ]")
    else:
        predicate = (
            "[ \"${1-}\" = '-p' ] && "
            "[ \"${3-}\" = \"${PYTEST_GATE_PID:-missing}\" ] && "
            "[ \"${W1_TERMINAL_CLASS:-}\" = EXEC_OK ]")
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"${W1_TEST_WAIT_USED:-0}\" -eq 0 ] && %s; then\n"
        "        W1_TEST_WAIT_USED=1\n"
        "        builtin printf '%s-wait-entered\\n' > %s\n"
        "        IFS= read -r W1_TEST_WAIT_RELEASE < %s\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n" % (
            predicate, role, shlex.quote(str(entered)),
            shlex.quote(str(release))),
        encoding="utf-8",
    )
    return bash_env, entered_fd, release


def _w1_terminal_reader_barrier(mod, tmp_path):
    """Prefix the production byte reader with an exact terminal-only barrier."""
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "terminal-reader")
    used = tmp_path / "terminal-reader-used"
    prefix = (
        "import os, pathlib, sys\n"
        "if (len(sys.argv) > 3 and sys.argv[3] == 'terminal' "
        "and not pathlib.Path(%r).exists()):\n"
        "    pathlib.Path(%r).write_text('used', encoding='utf-8')\n"
        "    with open(%r, 'w', encoding='utf-8') as stream:\n"
        "        stream.write('terminal-reader-entered\\n')\n"
        "    with open(%r, 'r', encoding='utf-8') as stream:\n"
        "        stream.readline()\n" % (
            str(used), str(used), str(entered), str(release))
    )
    return prefix + mod.REGISTRATION_CHANNEL_READER_PROGRAM, entered_fd, release


def _w1_owner_records(rundir: pathlib.Path) -> list[dict[str, str]]:
    """Parse the runner's append-only owner ledger without inferring outcomes."""
    records = []
    for line in (rundir / "registration-owners.log").read_text(
            encoding="utf-8").splitlines():
        fields = line.split()
        assert fields and fields[0] == "OWNER", line
        record = {}
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            assert separator and key and value, line
            record[key] = value
        records.append(record)
    return records


def test_registration_probe_binds_complete_identity_and_last_parenthesis(tmp_path):
    """The production parser owns pid/ppid/pgrp/starttime, not just a number."""
    mod = _load()
    path = tmp_path / "stat"
    cases = [
        ("plain", 411, 73, 411, 9001, "S"),
        ("worker ) name with spaces", 412, 74, 412, 9002, "R"),
        ("worker (nested) ) name", 413, 75, 413, 9003, "Z"),
    ]
    for comm, pid, ppid, pgrp, starttime, state in cases:
        path.write_text(_w1_stat_row(
            pid, ppid=ppid, pgrp=pgrp, starttime=starttime,
            state=state, comm=comm), encoding="utf-8")
        result = _w1_run_registration_probe(mod, "process", pid, path)
        assert result.returncode == 0, (
            f"production observer rejected {comm!r}: {result.stderr!r}")
        assert result.stdout.strip() == (
            "OBSERVED|%d:%d:%d:%d|%s" %
            (pid, ppid, pgrp, starttime, state)), (
            "observer did not split at the final `) ` or mapped a Linux stat "
            f"field incorrectly: {result.stdout!r}")


def test_registration_probe_exposes_pgrp_and_starttime_changes_separately(tmp_path):
    """Both recyclable group membership and process birth are load-bearing."""
    mod = _load()
    pid = 514
    path = tmp_path / "stat"
    original = _w1_stat_row(pid, ppid=88, pgrp=pid, starttime=12000)
    schedules = [
        (original, "OBSERVED|514:88:514:12000|S"),
        (_w1_change_proc_field(original, pgrp=515),
         "OBSERVED|514:88:515:12000|S"),
        (_w1_change_proc_field(original, starttime=12001),
         "OBSERVED|514:88:514:12001|S"),
    ]
    for raw, expected in schedules:
        path.write_text(raw, encoding="utf-8")
        result = _w1_run_registration_probe(mod, "process", pid, path)
        assert (result.returncode, result.stdout.strip()) == (0, expected)


def test_registration_probe_distinguishes_absent_malformed_and_unknown(tmp_path):
    mod = _load()
    missing = tmp_path / "missing"
    malformed = tmp_path / "malformed"
    unreadable_shape = tmp_path / "directory"
    malformed.write_text("123 (short) S 1\n", encoding="utf-8")
    unreadable_shape.mkdir()
    cases = [
        (missing, "ABSENT"),
        (malformed, "MALFORMED"),
        (unreadable_shape, "UNKNOWN"),
    ]
    for path, expected in cases:
        result = _w1_run_registration_probe(mod, "process", 123, path)
        assert (result.returncode, result.stdout.strip()) == (0, expected)


def test_group_probe_never_promotes_an_incomplete_census_to_present(tmp_path):
    """One matching member cannot prove sole ownership if any row is unknown."""
    mod = _load()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    member = proc_root / "601"
    member.mkdir()
    (member / "stat").write_text(
        _w1_stat_row(601, ppid=77, pgrp=601, starttime=9001),
        encoding="utf-8",
    )
    incomplete = proc_root / "602"
    incomplete.mkdir()
    (incomplete / "stat").mkdir()

    result = _w1_run_registration_probe(mod, "group", 601, proc_root)

    assert result.returncode == 0
    assert result.stdout.strip() == "UNKNOWN", (
        "a matching member was promoted to PRESENT even though another numeric "
        "census row could not be classified"
    )


def test_group_probe_treats_processlookup_during_census_as_vanished(tmp_path):
    """ESRCH is a completed disappearance, not ambient census uncertainty."""
    mod = _load()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    vanished = proc_root / "602"
    vanished.mkdir()
    vanished_stat = vanished / "stat"
    vanished_stat.write_text(
        _w1_stat_row(602, ppid=77, pgrp=602, starttime=9002),
        encoding="utf-8",
    )

    hook = tmp_path / "lookup-hook"
    hook.mkdir()
    raised = tmp_path / "processlookup-raised"
    (hook / "sitecustomize.py").write_text(
        "import errno, os, pathlib\n"
        "_real_read_text = pathlib.Path.read_text\n"
        "def _row325_read_text(self, *args, **kwargs):\n"
        "    if self == pathlib.Path(os.environ['W1_PROCESSLOOKUP_STAT']):\n"
        "        pathlib.Path(os.environ['W1_PROCESSLOOKUP_RAISED']).write_text(\n"
        "            'raised\\n', encoding='ascii')\n"
        "        raise ProcessLookupError(errno.ESRCH, 'vanished during census')\n"
        "    return _real_read_text(self, *args, **kwargs)\n"
        "pathlib.Path.read_text = _row325_read_text\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["W1_PROCESSLOOKUP_STAT"] = str(vanished_stat)
    env["W1_PROCESSLOOKUP_RAISED"] = str(raised)
    _w1_prepend_pythonpath(env, hook)

    result = _w1_run_registration_probe(
        mod, "group", 601, proc_root, env=env)

    assert raised.read_text(encoding="ascii") == "raised\n", (
        "the forced process-disappearance schedule never ran")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ABSENT", (
        "an unrelated process that vanished with ESRCH poisoned the complete "
        "target-group absence census: %r" % result.stdout)


def test_registration_gate_precedes_registration_and_workload_release():
    """Source-order control for the race-closing direct-child gate."""
    mod = _load()
    runner = mod.RUNNER
    relay = runner.index("coproc W1_TERMINAL_RELAY {")
    gate = runner.index("coproc W1_REGISTRATION_GATE {")
    receipt = runner.index(
        'PYTEST_ORIGINAL_RECEIPT="$W1_INITIAL_RECEIPT"', gate)
    register = runner.index('"$HOME/BulkDownloader/toolchain/bin/bd-jobs" register')
    release = runner.index("W1_GATE_RELEASE_TOKEN")
    assert runner.index("trap 'registration_on_exit $?' EXIT") < relay < gate
    assert runner.index("trap 'registration_on_cancel 130' INT") < relay
    assert relay < gate < receipt < register < release
    assert 'registration_cancel_checkpoint "after-relay-acquire"' in runner
    assert 'registration_cancel_checkpoint "after-gate-acquire"' in runner
    assert "PYTEST_PID=$PYTEST_GATE_PID" in runner
    assert "PYTEST_ORIGINAL_RECEIPT" in runner
    assert "READY v1" in mod.REGISTRATION_GATE_PROGRAM
    assert "ABORTED v1" in mod.REGISTRATION_GATE_PROGRAM
    assert "EXEC-FAIL v1" in mod.REGISTRATION_GATE_PROGRAM
    shim = mod.registration_workload_shim("/tmp/run", "echo exact-command")
    assert "EXEC-OK v1" in shim and shim.rstrip().endswith("exec echo exact-command")


def test_registration_state_machine_has_one_total_deadline_and_failure_funnel():
    """Owner budgets and registered cleanup have one structural authority."""
    runner = _load().RUNNER
    assert runner.count("W1_LIFECYCLE_DEADLINE_US=$((") == 1
    assert "W1_FORWARD_DEADLINE_US=$((W1_LIFECYCLE_DEADLINE_US" in runner
    assert "registration_cap_deadline" in runner
    assert "registration_fail_registered()" in runner
    assert runner.count("registration_finish 93") == 1, (
        "a registered abnormal edge bypasses the single reconciliation funnel")
    assert "registration_checked_terminal_relay_wait()" in runner
    assert "registration_checked_child_wait() {{" in runner
    relay_wait = runner.split(
        "registration_checked_terminal_relay_wait() {{", 1)[1].split(
            "\n}}", 1)[0]
    assert "registration_checked_child_wait terminal-relay \\" in relay_wait
    assert '"$W1_TERMINAL_RELAY_PID" 0' in relay_wait
    assert ('wait -n -p W1_RACE_WAITED_PID "$W1_CHILD_PID" '
            '"$W1_TIMER_PID"') in runner
    assert "W1_FRAME_CLASS" in runner and '"$W1_TERMINAL_CLASS"' in runner


def test_zero_owner_kill_grace_is_rejected_before_timeout_launch(tmp_path):
    """A TERM-only timeout is not a bounded owner for resistant children."""
    mod = _load()
    timeout_log = tmp_path / "timeout-argv"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    timeout_wrapper = fake_bin / "timeout"
    timeout_wrapper.write_text(
        "#!/bin/bash\n"
        "builtin printf '%s\\n' \"$*\" >> \"$W1_TIMEOUT_ARGV_LOG\"\n"
        "exec /usr/bin/timeout \"$@\"\n",
        encoding="utf-8",
    )
    timeout_wrapper.chmod(0o755)
    script, _rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit 0\n",
        reap_seconds=1, owner_kill_grace_us=0,
    )
    assert "W1_OWNER_KILL_GRACE_US=0" in script.read_text(encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["W1_TIMEOUT_ARGV_LOG"] = str(timeout_log)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("zero_owner_kill_grace_is_rejected_before_timeout_launch/run"),
    )

    assert not timeout_log.exists(), (
        "ZERO-KILL-GRACE-REACHED-TIMEOUT-OWNER: "
        + timeout_log.read_text(encoding="utf-8", errors="replace")
        if timeout_log.exists() else
        "ZERO-KILL-GRACE-REACHED-TIMEOUT-OWNER"
    )
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


def test_direct_owner_stop_has_positive_monotonic_grace_before_conditional_kill():
    """GNU timeout grace cannot stand in for the direct teardown path."""
    runner = _load().RUNNER
    start = runner.index("registration_owner_stop() {{")
    body = runner[start:runner.index("\n}}", start)]
    assert "builtin kill -TERM" in body and "builtin kill -KILL" in body
    assert "registration_owner_term_grace" in body, (
        "DIRECT-OWNER-KILL-HAS-NO-POSITIVE-GRACE")
    term = body.index("builtin kill -TERM")
    grace = body.index("registration_owner_term_grace")
    kill = body.index("builtin kill -KILL")

    assert term < grace < kill, (
        "DIRECT-OWNER-KILL-HAS-NO-POSITIVE-GRACE")
    assert "registration_now_us" in body
    assert "W1_OWNER_TERM_AT_US" in body and "W1_OWNER_KILL_AT_US" in body
    assert "registration_process_receipt_matches" in body
    assert "PROCESS-RESTORATION-UNKNOWN" in body
    rollback = body.index(
        'W1_OWNER_GRACE_US="UNAVAILABLE-$W1_CLOCK_STATE"')
    assert grace < rollback < kill, (
        "CLOCK-FAILURE-REFUSED-IDENTITY-BOUND-KILL-CONTINUATION")
    grace_body = runner.split("registration_owner_term_grace() {{", 1)[1].split(
        "\n}}", 1)[0]
    assert "sleep " not in grace_body, (
        "the direct grace spawned an unowned sleeper")
    assert "W1_GRACE_TARGET_US" in grace_body


def test_timeout_owner_launch_keeps_a_positive_kill_after_escalation():
    """The wrapper must hard-stop a TERM-resistant command after its grace."""
    runner = _load().RUNNER
    start = runner.index("registration_owner_spawn() {{")
    body = runner[start:runner.index("\n}}", start)]
    assert 'timeout --kill-after="$W1_SPAWN_KILL_AFTER" \\' in body, (
        "TIMEOUT-OWNER-LOST-KILL-AFTER-ESCALATION")
    assert "W1_SPAWN_KILL_AFTER=\"$W1_OWNER_KILL_AFTER\"" in body


def test_owner_group_promotion_requires_full_launch_identity():
    """READY bytes cannot weaken PPID/PGID/SID/starttime ownership checks."""
    runner = _load().RUNNER
    start = runner.index("registration_promote_spawn_group_receipt() {{")
    body = runner[start:runner.index("\n}}", start)]
    same_start = runner.index("registration_receipt_same_process() {{")
    same_body = runner[same_start:runner.index("\n}}", same_start)]
    required = (
        'registration_receipt_same_process',
        'W1_PROMOTE_FIELDS[1]}}" = "$W1_SPAWN_PARENT_PID"',
        'W1_PROMOTE_FIELDS[2]}}" = "$W1_PROMOTE_PID"',
        'W1_PROMOTE_FIELDS[3]}}" = "$W1_PROMOTE_PID"',
    )
    assert all(token in body for token in required), (
        "OWNER-PROMOTION-TRUSTED-INCOMPLETE-RECEIPT")
    assert 'W1_SAME_OLD[4]}}" = "${{W1_SAME_NEW[4]}}"' in same_body, (
        "OWNER-PROMOTION-TRUSTED-INCOMPLETE-RECEIPT")


def test_completed_owner_ready_receipt_remains_census_authority(tmp_path):
    """A fast helper may exit after READY but before its parent promotes it.

    The authenticated READY receipt must remain sufficient to census the
    helper's owned process group.  Treating this ordinary completion race as
    UNKNOWN makes the same exact runner alternate between 91 and 92.
    """
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "terminal-owner-ready")
    owner_pid_path = tmp_path / "terminal-owner.pid"
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, site="completed_owner_ready_receipt_remains_census_authority/fifo") == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, (
            "fixture did not force the post-READY completion race", owner_state)
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=_w1_budget_s("completed_owner_ready_receipt_remains_census_authority/wait"))

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "1", records
        assert records[0]["descendants"] == "ABSENT", records
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "frame=ABORTED v1 reason=release-eof" in evidence
        assert rc == int(W1_RUNNER_FAILURE_CODE)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("completed_owner_ready_receipt_remains_census_authority/wait-2"))


def test_completed_owner_forged_ready_receipt_never_grants_census_authority(
        tmp_path):
    """The terminal-state fallback remains bound to the launch start time."""
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "forged-terminal-owner-ready")
    owner_pid_path = tmp_path / "forged-terminal-owner.pid"
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
        mutate_terminal_owner_ready=True,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, site="completed_owner_forged_ready_receipt_never_grants_census_authority/fifo") == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, owner_state
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=_w1_budget_s("completed_owner_forged_ready_receipt_never_grants_census_authority/wait"))

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "0", records
        assert records[0]["descendants"] == "UNKNOWN", records
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("completed_owner_forged_ready_receipt_never_grants_census_authority/wait-2"))


def test_completed_owner_ready_receipt_censuses_live_descendant(tmp_path):
    """Fallback authority cannot launder a surviving group into ABSENT."""
    mod = _load()
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "descendant-terminal-owner-ready")
    owner_pid_path = tmp_path / "descendant-terminal-owner.pid"
    descendant_pid_path = tmp_path / "terminal-owner-descendant.pid"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, descendant_pid_path)
    marker = tmp_path / "workload-started"
    print_anchor = 'print("S:" + state)\n'
    assert mod.REGISTRATION_CHANNEL_READER_PROGRAM.count(print_anchor) == 1
    descendant_program = (
        "if mode == 'terminal':\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        import pathlib, signal\n"
        "        descendant_pid_path = pathlib.Path(%r)\n"
        "        descendant_pid_temp = descendant_pid_path.with_name(\n"
        "            descendant_pid_path.name + '.tmp.%%d' %% os.getpid())\n"
        "        descendant_pid_temp.write_text(str(os.getpid()), encoding='ascii')\n"
        "        os.replace(descendant_pid_temp, descendant_pid_path)\n"
        "        for inherited_fd in (0, 1, 2):\n"
        "            try:\n"
        "                os.close(inherited_fd)\n"
        "            except OSError:\n"
        "                pass\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        while True:\n"
        "            signal.pause()\n"
    ) % str(descendant_pid_path)
    reader_program = mod.REGISTRATION_CHANNEL_READER_PROGRAM.replace(
        print_anchor, descendant_program + print_anchor, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, channel_reader_program=reader_program,
        after_terminal_owner_ready_barrier=(
            entered, release, owner_pid_path),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    owner_pid = descendant_pid = -1
    try:
        assert _w1_await_fifo(entered_fd, site="completed_owner_ready_receipt_censuses_live_descendant/fifo") == "owner-ready-read\n"
        owner_pid = int(owner_pid_path.read_text(encoding="ascii"))
        assert _w1_await_fifo(payload_entered_fd, site="completed_owner_ready_receipt_censuses_live_descendant/fifo-2") == "payload-write-opened\n"
        try:
            assert not descendant_pid_path.exists(), (
                "descendant pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        descendant_pid = _w1_wait_for_path(
            descendant_pid_path, content="integer")
        assert _w1_pid_is_live(descendant_pid)
        assert os.getpgid(descendant_pid) == owner_pid
        deadline = time.monotonic() + 5
        owner_state = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                owner_state = _w1_proc_observation(owner_pid)[-1]
            except (FileNotFoundError, ProcessLookupError):
                owner_state = "ABSENT"
            if owner_state in {"Z", "X", "ABSENT"}:
                break
            time.sleep(0.01)
        assert owner_state in {"Z", "X", "ABSENT"}, (
            "fixture did not force completed-owner descendant census",
            owner_state)
        with release.open("w", encoding="ascii") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=_w1_budget_s("completed_owner_ready_receipt_censuses_live_descendant/wait"))

        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(records) == 1, records
        assert records[0]["group_ready"] == "1", records
        assert records[0]["descendants"] == "PRESENT", records
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert _w1_pid_is_live(descendant_pid)
        assert not marker.exists()
    finally:
        os.close(entered_fd)
        os.close(payload_entered_fd)
        if owner_pid > 0:
            _w1_kill_group(owner_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("completed_owner_ready_receipt_censuses_live_descendant/wait-2"))


def test_one_second_lifecycle_cap_remains_truthfully_unknown(tmp_path):
    """The short-cap control cannot borrow later-path definite evidence."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=1, forward_expiry_is_subject=True,
        cleanup_seconds=1, observation_seconds=0,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("one_second_lifecycle_cap_remains_truthfully_unknown/run"))

    assert not registrar.exists() and not marker.exists(), (
        "N58B-SHORT-CAP-CROSSED-LATE-AUTHORITY")
    assert not (rundir / "jobid").exists()
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "initial-observation-unavailable" in evidence
    records = [record for record in _w1_owner_records(rundir)
               if record["role"] == "process-observer"]
    assert len(records) == 1 and records[0]["wait_ok"] == "0", records
    assert records[0]["descendants"] == "UNKNOWN"
    assert records[0]["stop"] == "SPAWN-FAILED"
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


def test_every_authority_helper_is_named_and_checked_waited(tmp_path):
    """The ordinary path leaves no ambient reader/observer/publisher owner."""
    mod = _load()
    owner_program = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM
    assert "live_fds = []" in owner_program
    assert "OWNER-READY v2 receipt=%s fds=%s" in owner_program
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("every_authority_helper_is_named_and_checked_waited/run"))

    assert result.returncode == 0, (
        "OWNER-BOOTSTRAP-RETAINED-UNDECLARED-FD: "
        f"runner returned {result.returncode}; stderr={result.stderr!r}")
    records = _w1_owner_records(rundir)
    roles = {record["role"] for record in records}
    assert {
        "ready-reader", "process-observer", "group-observer", "registrar",
        "terminal-reader", "terminal-relay", "workload",
        "gate-fd-observer", "relay-fd-observer",
    } <= roles
    for record in records:
        assert record["owner_pid"].isdigit(), record
        assert record["waited_pid"] == record["owner_pid"], record
        assert record["wait_ok"] == "1", record
        assert record["descendants"] in {"ABSENT", "NOT-APPLICABLE"}, record
        if (record["role"] in {
                "ready-reader", "process-observer", "group-observer",
                "registrar", "terminal-reader", "reconciliation",
                "owner-stop-grace",
        } or record["role"].endswith("-census")):
            assert record.get("fds") == "0,1,2", (
                "OWNER-BOOTSTRAP-RETAINED-UNDECLARED-FD", record)
            receipt = record.get("receipt", "").split(":")
            assert len(receipt) == 5 and all(value.isdigit() for value in receipt), (
                "owner ledger omitted its exact pid/ppid/pgid/sid/start receipt",
                record,
            )

    relay = [record for record in records
             if record["role"] == "terminal-relay"]
    assert len(relay) == 1 and relay[0].get("group_ready") == "1", relay

    fd_rows = {}
    for line in (rundir / "registration-authority-fds.log").read_text(
            encoding="ascii").splitlines():
        prefix, payload = line.split(" FDS|", 1)
        role = prefix.split("role=", 1)[1]
        _pid, encoded = payload.split("|", 1)
        fd_rows[role] = {
            int(fd): os.fsdecode(bytes.fromhex(target))
            for fd, target in (field.split(":", 1)
                               for field in encoded.split(","))
        }
    assert set(fd_rows) == {"gate", "relay"}, fd_rows
    assert set(fd_rows["gate"]) == {0, 2, 3}
    assert set(fd_rows["relay"]) == {0, 1, 2}
    assert fd_rows["gate"][3] == fd_rows["relay"][0]
    assert fd_rows["gate"][0] not in fd_rows["relay"].values()
    assert fd_rows["relay"][1] not in fd_rows["gate"].values()


def test_descendant_census_is_itself_a_named_checked_owner(tmp_path):
    """A post-wait /proc scan cannot be an unbudgeted ambient shell loop."""
    mod = _load()
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("descendant_census_is_itself_a_named_checked_owner/run"))

    assert result.returncode == 0
    records = _w1_owner_records(rundir)
    census_roles = {record["role"] for record in records
                    if record["role"].endswith("-census")}
    assert {
        "ready-reader-census", "process-observer-census",
        "group-observer-census", "registrar-census",
        "terminal-reader-census", "terminal-relay-census",
    } <= census_roles
    for record in records:
        if record["role"].endswith("-census"):
            assert record["wait_ok"] == "1", record
            assert record["waited_pid"] == record["owner_pid"], record
            assert record["descendants"] == "NOT-APPLICABLE", record
    assert "registration_builtin_group_census" not in mod.RUNNER
    assert "No such file or directory" not in result.stderr


def test_successful_registration_releases_exact_command_and_status(tmp_path):
    """Commit releases the same pid/pgid/starttime and preserves workload rc."""
    mod = _load()
    registrar_marker = tmp_path / "registrar-started"
    workload_receipt = tmp_path / "workload-receipt"
    workload_marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\n"
        "cat /proc/$$/stat > %s\n"
        "touch %s\n"
        "exit %d\n" % (
            shlex.quote(str(workload_receipt)),
            shlex.quote(str(workload_marker)), W1_WORKLOAD_CODE),
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n", sleep=1.0))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    pgid = -1
    try:
        pgid, live = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar_marker)
        before = _w1_proc_observation(pgid)
        assert before[:3] == (pgid, proc.pid, pgid)
        assert len(live) == 1 and not workload_marker.exists(), (
            "the command ran while registration was still undecided")
        rc = proc.wait(timeout=_w1_budget_s("successful_registration_releases_exact_command_and_status/wait"))
        assert workload_marker.exists() and workload_receipt.is_file()
        after = _w1_proc_observation(
            pgid, workload_receipt.read_text(encoding="utf-8"))
        assert after[:4] == before[:4], (
            "release did not exec the registered direct child in place: "
            f"before={before!r}, after={after!r}")
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
        assert rc == 0
    finally:
        _w1_kill_group(pgid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("successful_registration_releases_exact_command_and_status/wait-2"))


def test_vanished_gate_leader_with_live_descendant_is_retained_unknown(tmp_path):
    """Collecting the direct child is not proof that its original group is gone."""
    mod = _load()
    marker = tmp_path / "workload-started"
    child_marker = tmp_path / "gate-child"
    write_shim, payload_entered_fd, payload_release = _w1_delay_path_write(
        tmp_path, child_marker)
    gate_post_setsid = tmp_path / "gate-post-setsid"
    os.mkfifo(gate_post_setsid)
    gate_program = (
        "import os, pathlib, time\n"
        "def emit(fd, frame):\n"
        "    os.write(fd, (frame + '\\n').encode('ascii'))\n"
        "with open(%r, 'w', encoding='ascii') as stream:\n"
        "    stream.write('gate-post-setsid\\n')\n"
        "emit(1, 'READY v1 pid=%%d' %% os.getpid())\n"
        "os.close(1)\n"
        "while os.read(0, 4096):\n"
        "    pass\n"
        "ready_read, ready_write = os.pipe()\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(ready_read)\n"
        "    os.close(3)\n"
        "    child_marker = pathlib.Path(%r)\n"
        "    child_marker_temp = child_marker.with_name(\n"
        "        child_marker.name + '.tmp.%%d' %% os.getpid())\n"
        "    child_marker_temp.write_text(str(os.getpid()))\n"
        "    os.replace(child_marker_temp, child_marker)\n"
        "    os.write(ready_write, b'1')\n"
        "    os.close(ready_write)\n"
        "    time.sleep(30)\n"
        "    raise SystemExit(0)\n"
        "os.close(ready_write)\n"
        "if os.read(ready_read, 1) != b'1':\n"
        "    raise SystemExit(97)\n"
        "os.close(ready_read)\n"
        "emit(3, 'ABORTED v1 reason=release-eof')\n"
        "raise SystemExit(0)\n" % (str(gate_post_setsid), str(child_marker))
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        owned_group_census_override="AUXILIARY-ABSENT",
        before_group_receipt_recheck_fifo=gate_post_setsid,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    _w1_prepend_pythonpath(env, write_shim)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(payload_entered_fd, site="vanished_gate_leader_with_live_descendant_is_retained_unknown/fifo") == "payload-write-opened\n"
        try:
            assert not child_marker.exists(), (
                "gate-child pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(payload_release)
        assert proc.wait(timeout=_w1_budget_s("vanished_gate_leader_with_live_descendant_is_retained_unknown/wait")) == int(W1_RETAINED_FAILURE_CODE)
        child_pid = _w1_wait_for_path(child_marker, content="integer")
        assert child_pid in {int(pid) for pid in _w1_live_in_group(gate_pid)}
        assert not marker.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "group=PRESENT" in err and "wait=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
    finally:
        os.close(payload_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("vanished_gate_leader_with_live_descendant_is_retained_unknown/wait-2"))


def test_unreadable_group_absence_probe_never_grants_status_91(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    bash_env = tmp_path / "unknown-group-probe.bash"
    bash_env.write_text(
        "python3() {\n"
        "    if [ \"${1-}\" = '-c' ] && [ \"${3-}\" = 'group' ]; then\n"
        "        builtin printf 'UNKNOWN\\n'\n"
        "        return 0\n"
        "    fi\n"
        "    command python3 \"$@\"\n"
        "}\n"
        "export -f python3\n",
        encoding="utf-8",
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("unreadable_group_absence_probe_never_grants_status_91/run"))
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)
    assert not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "wait=0" in err and "group=UNKNOWN" in err
    assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE


def test_registration_failure_never_signals_changed_process_identity(tmp_path):
    """An untrusted pre-receipt foreign group is neither registered nor acted on."""
    mod = _load()
    foreign = subprocess.Popen(
        ["setsid", "sleep", "300"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=False)
    workload_marker = tmp_path / "workload-started"
    registrar_marker = tmp_path / "registrar-started"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(workload_marker)),
        reap_seconds=3, pytest_pid_override=foreign.pid,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar_marker)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = proc.wait(timeout=_w1_budget_s("registration_failure_never_signals_changed_process_identity/wait"))
        assert rc == int(W1_RETAINED_FAILURE_CODE)
        assert foreign.poll() is None and os.getpgid(foreign.pid) == foreign.pid
        assert not registrar_marker.exists(), (
            "the runner registered a PID whose PPID was not the runner")
        assert not workload_marker.exists() and not signal_log.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "initial-not-direct-child" in err
        assert "release_writes=0" in err
    finally:
        _w1_kill_group(foreign.pid)
        foreign.wait(timeout=_w1_budget_s("registration_failure_never_signals_changed_process_identity/wait-2"))
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("registration_failure_never_signals_changed_process_identity/wait-3"))


def test_wedge_hunt_does_not_wait_on_a_job_it_could_not_register(tmp_path):
    """Registrar refusal never directs a numeric signal at the inert gate."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    syscall_log = tmp_path / "registration-signal-syscalls"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nsleep 300\n" % shlex.quote(str(marker)),
        reap_seconds=3,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.5))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["strace", "-qq", "-e", "trace=kill,tgkill,tkill",
         "-o", str(syscall_log), "bash", str(script)], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        assert gate_pid > 0 and os.getpgid(gate_pid) == gate_pid
        assert live == [str(gate_pid)] and not marker.exists()
        rc = proc.wait(timeout=_w1_budget_s("wedge_hunt_does_not_wait_on_a_job_it_could_not_register/wait"))
        assert not marker.exists() and not signal_log.exists()
        syscalls = syscall_log.read_text(encoding="utf-8")
        assert not re.search(
            r"\bkill\(\s*-%d\s*," % gate_pid, syscalls), (
            "registration failure signalled the pre-id target group")
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "frame=ABORTED v1 reason=release-eof" in err
        assert "wait=0" in err and "release_writes=0" in err
        assert (rundir / "exitcode").read_text().strip() == W1_RUNNER_FAILURE_CODE
        assert not _w1_live_in_group(gate_pid)
        assert rc == int(W1_RUNNER_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("wedge_hunt_does_not_wait_on_a_job_it_could_not_register/wait-2"))


# The explicit dynamic catcher name is retained alongside the legacy node id.
test_pre_registration_target_is_never_signalled = (
    test_wedge_hunt_does_not_wait_on_a_job_it_could_not_register)


# The four ways a READY frame can be inadmissible: absent, partial, malformed,
# and duplicate. Each must be refused before the registrar, under EITHER
# settlement outcome below -- that half of the contract is not what was wrong.
_W1_INADMISSIBLE_READY_PREAMBLES = [
    "exit 97",
    "printf 'READY'; exit 97",
    "printf 'WRONG v1\\n'; exit 97",
    "printf 'READY v1 pid=%s\\nEXTRA v1\\n' \"$BASHPID\"; exit 97",
]


@pytest.mark.parametrize("preamble", _W1_INADMISSIBLE_READY_PREAMBLES)
@pytest.mark.parametrize("settlement", ["ABSENT", "UNKNOWN"])
def test_gate_ready_is_exact_terminal_admission(tmp_path, preamble, settlement):
    """Inadmissible READY never reaches the registrar, and the status it
    reports is decided by a precondition this test FORCES rather than races.

    WHY THIS IS PARAMETRIZED TWICE. Refusing the frame and classifying the
    settlement are two different claims, and only the first one was ever
    tested. `registration_settle_abort 94 92 "ready-refused"` resolves to a
    PROVED setup failure (94) or a RETAINED unknown (92) according to whether
    the runner could prove its gate group had settled -- and both answers are
    correct for what each run could prove.

    THE RACE THIS REPLACES, measured 2026-08-23 on seven hosts. Every
    parametrization used a prelude that exits immediately, so the gate child
    and the runner's own group-receipt probe at
    `bd-wedge-hunt:W1_GATE_GROUP_READY_AT_ACQUIRE` raced. Gate still alive at
    the probe -> group_ready=1 -> the census runs -> ABSENT -> 94. Gate already
    a zombie or reaped -> `registration_child_group_is_leader` refuses ->
    group_ready=0 -> THE GATE-ROLE CENSUS IS NEVER CALLED, because the `&&` at
    `bd-wedge-hunt:2314` in `registration_checked_child_wait` short-circuits
    (`registration_checked_gate_wait` only delegates to it) -> the status stays
    at its initialised UNKNOWN -> 92. The census function has three call sites
    and the other two still run; it is the gate one that is skipped. The test
    demanded 92 unconditionally, so three of twenty-eight outcomes across the
    fleet failed on test3 and test4 for being right. Thirty low-load
    repetitions afterwards -- 120 parameter outcomes -- all returned 92, which
    is exactly why a green rerun could not dispose of it.

    WHY FORCING THE CENSUS WOULD NOT HAVE WORKED, and why these controls drive
    the barrier instead: on the runs that produced 92 the census function is
    never entered, so an injected census verdict is unobservable there.
    `descendants=UNKNOWN` in those preserved records is an initialised value,
    not a measurement. The deciding precondition is the group receipt, so that
    is what gets held still.

    NEITHER BRANCH IS ALLOWED TO LAUNDER THE OTHER. The status assertion is
    exact per branch, the forced precondition is asserted from the runner's own
    durable record, and each branch asserts the liveness state it built.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_pidfile = tmp_path / "gate-coproc.pid"
    recheck = tmp_path / "before-group-receipt-recheck"
    probed = tmp_path / "after-group-receipt-recheck"
    hold = tmp_path / "gate-hold"
    os.mkfifo(recheck)
    os.mkfifo(probed)
    # Opened BEFORE the runner starts, and non-blocking, so the runner's write
    # can never block on a missing reader and this test can never deadlock on
    # a barrier the runner reached first.
    probed_fd = os.open(probed, os.O_RDONLY | os.O_NONBLOCK)

    # $BASHPID inside the coproc body IS the pid the runner probes.
    publish = (
        'printf \'%%s\\n\' "$BASHPID" > %s\n'
        'mv %s %s\n' % (
            shlex.quote(str(gate_pidfile) + ".tmp"),
            shlex.quote(str(gate_pidfile) + ".tmp"),
            shlex.quote(str(gate_pidfile))))
    # BOTH branches build the SAME gate and hold it on the SAME fifo. The only
    # difference between a 94 and a 92 is the ORDER of the two releases below,
    # which is exactly the difference the fleet was racing -- stating it as an
    # ordering rather than as two different fixtures is what makes the contrast
    # exact instead of merely plausible.
    os.mkfifo(hold)
    prelude = publish + "IFS= read -r _ < %s\n" % shlex.quote(str(hold))
    prelude += preamble

    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_prelude=prelude,
        before_group_receipt_recheck_fifo=recheck,
        after_group_receipt_recheck_fifo=probed,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    # EXACT PER BRANCH. `status in {92, 94}` would accept the very race this
    # control exists to remove, so each forced precondition names one status.
    expected_code = (W1_SETUP_FAILURE_CODE if settlement == "ABSENT"
                     else W1_RETAINED_FAILURE_CODE)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid = _w1_wait_for_path(
            gate_pidfile, content="integer",
            timeout=_w1_budget_s(
                "gate_ready_is_exact_terminal_admission/marker"))
        assert gate_pid > 0, gate_pid

        # SHARED PRECONDITION, asserted before either branch relies on it: the
        # gate is blocked on its hold fifo, so it is alive and is its own group
        # leader -- which is what `registration_child_group_is_leader` requires.
        # Nothing here is a timing argument: the child cannot exit until this
        # test opens that fifo.
        assert _w1_pid_is_live(gate_pid), (
            "the gate child was not alive at the hold barrier, so neither "
            "settlement precondition could be built from a known state")
        assert os.getpgid(gate_pid) == gate_pid, (
            "the gate child is not its own group leader")

        if settlement == "ABSENT":
            # Probe first, THEN let the gate exit -- and WAIT for the probe to
            # actually finish before letting it.
            #
            # MEASURED, not assumed. An earlier draft released these two
            # barriers back to back, reasoning that a released runner probes a
            # still-blocked gate. Releasing the first barrier only proves the
            # runner was WOKEN. Under 48 burners this host produced
            # group_ready=0 on 3 of 4 ABSENT cases, and a 2-core CI runner
            # produced the same 92-instead-of-94 -- the gate won the gap and
            # exited before the probe read /proc. The mutation battery had
            # already reported the swapped-order mutant as ESCAPED; it was a
            # true positive about this exact gap, not an artefact.
            _w1_release_fifo(recheck)
            assert "gate-receipt-recheck-done" in _w1_await_fifo(
                probed_fd, site="gate_ready_is_exact_terminal_admission/fifo"), (
                "the runner never reported completing its gate receipt "
                "recheck, so the ABSENT precondition was not forced")
            _w1_release_fifo(hold)
        else:
            # Let the gate exit FIRST, and prove it reached the state that
            # makes the probe refuse, before releasing the probe.
            _w1_release_fifo(hold)
            # A SETTLED TERMINAL STATE, named exactly rather than inferred
            # from "not live". Measured here: bash reaps the coproc child in
            # its own SIGCHLD handling even while the runner script is blocked
            # at the barrier, so the stable observation is REAPED (the stat
            # file is gone) rather than the Z this first assumed. Both are
            # terminal and both are what `registration_process_receipt` fails
            # on, which is what makes the probe refuse -- but only one of them
            # actually occurs, and the assertion says which.
            #
            # This is a bounded wait for a state TRANSITION, not a sleep, and
            # it is LOAD-BEARING: released without it the gate is still waking
            # from its fifo read and is observably alive. A mutation battery
            # that deletes this loop goes RED on the assertion below -- an
            # earlier draft polled `_w1_pid_is_live` before releasing `hold` at
            # all, and that mutant ESCAPED because the gate had already exited
            # on its own by then. The loop was doing nothing and the control
            # was relying on the very race it claims to remove.
            deadline = time.monotonic() + 15.0
            state = None
            while time.monotonic() < deadline:
                try:
                    state = _w1_proc_observation(gate_pid)[-1]
                except (FileNotFoundError, ProcessLookupError, ValueError,
                        AssertionError):
                    state = "REAPED"
                if state in {"Z", "REAPED"}:
                    break
                time.sleep(0.01)
            assert state in {"Z", "REAPED"}, (
                "the gate child never reached a settled terminal state, so "
                "this control did not build the UNKNOWN precondition and the "
                "receipt probe would still be racing: state=%r" % (state,))
            _w1_release_fifo(recheck)
            assert "gate-receipt-recheck-done" in _w1_await_fifo(
                probed_fd, site="gate_ready_is_exact_terminal_admission/fifo-2"), (
                "the runner never reported completing its gate receipt "
                "recheck, so the UNKNOWN precondition was not forced")

        observed = _w1_wait_for_exit(proc, rundir, site="gate_ready_is_exact_terminal_admission/exit")
        if observed != int(expected_code):
            # Say WHICH failure this is. A starved host cannot spawn the census
            # or deadline owners the runner needs, records
            # stop=DEADLINE-SPAWN-FAILED or no gate row at all, and honestly
            # reports UNKNOWN -- which is the runner being right about not
            # knowing, not the status policy being wrong. Measured under 64
            # burners with concurrent -n 24 lanes; the capture serial lane runs
            # this module at -n 0, where it is deterministic. Backlog row 221.
            try:
                owners = (rundir / "registration-owners.log").read_text(
                    encoding="utf-8")
            except OSError:
                owners = "<no registration-owners.log>"
            gate = [l for l in owners.splitlines()
                    if l.startswith("OWNER role=gate ")]
            raise AssertionError(
                "gate-ready settlement status %s, expected %s for forced "
                "%s.\ngate owner rows: %s\nIf that row says "
                "stop=DEADLINE-SPAWN-FAILED, or there is no gate row at all, "
                "this host could not spawn the runner's own measurement "
                "owners and the runner reported UNKNOWN correctly -- see "
                "backlog row 221, not a policy mismatch."
                % (observed, expected_code, settlement, gate or "NONE"))
    finally:
        os.close(probed_fd)
        if gate_pid > 0:
            _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=_w1_budget_s("gate_ready_is_exact_terminal_admission/communicate"))

    # The half of the contract that was always right: no inadmissible frame
    # reaches the registrar or the workload, under either settlement.
    assert not registrar.exists() and not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "release_writes=0" in err

    # The forced precondition, read back from the runner's OWN durable record
    # rather than assumed from the fixture that forced it.
    owners = (rundir / "registration-owners.log").read_text(encoding="utf-8")
    gate_rows = [line for line in owners.splitlines()
                 if line.startswith("OWNER role=gate ")]
    assert len(gate_rows) == 1, owners
    expected_ready = "group_ready=1" if settlement == "ABSENT" else "group_ready=0"
    assert expected_ready in gate_rows[0], (
        "the forced settlement precondition did not take effect", gate_rows[0])
    assert "group=%s" % settlement in err, err

    # NEGATIVE CONTROL on the 94 branch. `registration_finish` downgrades a
    # requested 94 to 92 when the owners were not all proved settled, and it
    # SAYS SO. Its absence is what makes this 94 a proved settlement rather
    # than a status that survived by not being checked.
    if settlement == "ABSENT":
        assert "SETUP-CLASSIFICATION-DOWNGRADE" not in err, err
    else:
        assert "wait_ok=0" in gate_rows[0], gate_rows[0]

    assert (rundir / "exitcode").read_text().strip() == expected_code


def test_live_wrong_ready_frame_never_reaches_the_registrar(tmp_path):
    """A live, well-shaped child does not launder a malformed READY frame."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        ready="WRONG v1", terminal="ABORTED v1 reason=release-eof")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("live_wrong_ready_frame_never_reaches_the_registrar/run"))
    assert not registrar.exists() and not marker.exists(), (
        "WRONG-READY-REACHED-REGISTRAR", registrar.exists(), marker.exists(),
        result.returncode, result.stdout, result.stderr)
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "frame=WRONG v1" in err
    assert result.returncode == 94


def test_live_duplicate_ready_frame_never_reaches_the_registrar(tmp_path):
    """A second buffered admission frame is a protocol failure, not READY."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        extra_ready="EXTRA v1", terminal="ABORTED v1 reason=release-eof")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("live_duplicate_ready_frame_never_reaches_the_registrar/run"))
    assert not registrar.exists() and not marker.exists()
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "phase=ready" in err and "release_writes=0" in err
    assert result.returncode == 94


def test_delayed_extra_ready_frame_never_reaches_the_registrar(tmp_path):
    """Registration waits for exact READY-channel EOF, not a quiet instant."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    ready_written = tmp_path / "ready-written"
    release_extra = tmp_path / "release-extra"
    reader_entered, reader_release, reader_entered_fd = _w1_fifo_barrier(
        tmp_path, "ready-reader-consumed")
    os.mkfifo(release_extra)
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof",
        extra_ready="EXTRA v1", ready_written_marker=ready_written,
        extra_ready_release=release_extra)
    reader_anchor = "            data.extend(chunk)\n"
    assert mod.REGISTRATION_CHANNEL_READER_PROGRAM.count(reader_anchor) == 1
    reader_program = mod.REGISTRATION_CHANNEL_READER_PROGRAM.replace(
        reader_anchor,
        reader_anchor
        + "            if (mode == 'ready' and data.endswith(b'\\n')\n"
        + "                    and b'EXTRA v1' not in data):\n"
        + "                with open(%r, 'w', encoding='ascii') as stream:\n"
          % str(reader_entered)
        + "                    stream.write('ready-consumed\\n')\n"
        + "                with open(%r, 'r', encoding='ascii') as stream:\n"
          % str(reader_release)
        + "                    stream.readline()\n",
        1,
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
        channel_reader_program=reader_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(reader_entered_fd, site="delayed_extra_ready_frame_never_reaches_the_registrar/fifo") == "ready-consumed\n"
        _w1_wait_for_path(ready_written)
        assert not registrar.exists(), (
            "exact READY bytes without admission EOF reached the registrar")
        with reader_release.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        with release_extra.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=_w1_budget_s("delayed_extra_ready_frame_never_reaches_the_registrar/wait"))
        assert not registrar.exists() and not marker.exists(), (
            "exact READY bytes without admission EOF reached the registrar")
        assert (rundir / "exitcode").read_text().strip() == "94"
        assert rc == 94
    finally:
        os.close(reader_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("delayed_extra_ready_frame_never_reaches_the_registrar/wait-2"))


def test_nul_bearing_ready_frame_never_reaches_the_registrar(tmp_path):
    """Bash string normalization cannot turn byte-invalid READY into authority."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof", nul_ready=True)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("nul_bearing_ready_frame_never_reaches_the_registrar/run"))
    assert not registrar.exists() and not marker.exists()
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "frame_hex=" in evidence and "00" in evidence
    assert result.returncode == 94


def test_pre_ready_descendant_refuses_registration(tmp_path):
    """READY authority requires the direct-child gate to be the sole member."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=_w1_pre_ready_descendant_gate_program(),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir, minimum_live=2)
        assert len(live) >= 2
        rc = proc.wait(timeout=_w1_budget_s("pre_ready_descendant_refuses_registration/wait"))
        assert not registrar.exists() and not marker.exists()
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "initial-group-not-sole" in evidence
        assert (rundir / "exitcode").read_text().strip() == (
            W1_RETAINED_FAILURE_CODE)
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("pre_ready_descendant_refuses_registration/wait-2"))


def test_release_sigpipe_handler_is_scoped_and_restored_after_write(tmp_path):
    """The containment handler must not become ambient runner signal policy."""
    mod = _load()
    pipe_state = tmp_path / "post-release-pipe-trap"
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit %d\n" % W1_WORKLOAD_CODE,
        reap_seconds=3,
        after_release_pipe_probe=pipe_state,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("release_sigpipe_handler_is_scoped_and_restored_after_write/run"))

    assert result.returncode == 0
    assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE)
    assert pipe_state.is_file()
    assert pipe_state.read_text(encoding="utf-8") == "", (
        "RELEASE-SIGPIPE-HANDLER-LEAKED-PAST-WRITE")


def test_terminal_relay_wait_failure_reconciles_registered_id(tmp_path):
    """Terminal bytes are not authoritative until their named relay is reaped."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    failing_relay = (
        "import os\n"
        "while True:\n"
        "    chunk = os.read(0, 4096)\n"
        "    if not chunk:\n"
        "        break\n"
        "    while chunk:\n"
        "        written = os.write(1, chunk)\n"
        "        chunk = chunk[written:]\n"
        "raise SystemExit(9)\n"
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reconcile_seconds=3, terminal_relay_program=failing_relay,
        owned_group_census_override="ABSENT",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("terminal_relay_wait_failure_reconciles_registered_id/run"))
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2 and calls[1] == "reap --id stubhost-4242"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "relay_wait=9" in evidence
    assert marker.exists()
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


def test_failed_workload_wait_reconciles_registered_id(tmp_path):
    """A wait ownership failure cannot abandon the registered EXEC-OK child."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    bash_env = tmp_path / "fail-workload-wait.bash"
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"${1-}\" = '-p' ] && [ \"${3-}\" = \"$PYTEST_GATE_PID\" ]; then\n"
        "        builtin printf 'synthetic workload wait failure\\n' >&2\n"
        "        return 127\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n",
        encoding="utf-8",
    )
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n" %
        (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reconcile_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("failed_workload_wait_reconciles_registered_id/run"))
    wait_evidence = (
        rundir / "registration-workload-wait.err").read_text(encoding="utf-8")
    assert "synthetic workload wait failure" in wait_evidence
    calls = argv_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2 and calls[0].startswith("register ")
    assert calls[1] == "reap --id stubhost-4242"
    assert marker.exists()
    assert result.returncode == int(W1_RELEASE_FAILURE_CODE)


@pytest.mark.parametrize("terminal,status", [
    ("EXEC-FAIL v1 errno=5", 96),
    ("ABORTED v1 reason=synthetic", 0),
])
def test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait(
        tmp_path, terminal, status):
    """A terminal-looking line is not authority to bare-wait a live gate."""
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    gate_program = _w1_adversarial_gate_program(
        terminal=terminal, hold=30, status=status)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, forward_expiry_is_subject=True,
        gate_program=gate_program,
        checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log, site="terminal_frame_without_eof_never_enters_an_unbounded_child_wait/exit")
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        assert not (rundir / "registration-wait.err").exists(), (
            "the runner entered child wait without terminal EOF authority")
        assert not checked_wait_log.exists()
        assert _w1_live_in_group(gate_pid), (
            "the hold-open gate did not exercise the pre-wait boundary")
        readers = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(readers) == 1 and readers[0]["status"] == "0", readers
        assert readers[0]["wait_ok"] == "1"
        assert readers[0]["descendants"] == "ABSENT"
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("terminal_frame_without_eof_never_enters_an_unbounded_child_wait/wait"))


def test_handoff_timeout_retains_registered_id_under_one_budget(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, forward_expiry_is_subject=True,
        checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_GATE_WITHHOLD_STATUS"] = "30"
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log, site="handoff_timeout_retains_registered_id_under_one_budget/exit")
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "writes=1 sigpipe=0 frame_rc=142" in protocol
        assert "frame= frame_hex= eof=0" in protocol
        assert not checked_wait_log.exists()
        readers = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-reader"]
        assert len(readers) == 1 and readers[0]["status"] == "0", readers
        assert readers[0]["wait_ok"] == "1"
        assert readers[0]["descendants"] == "ABSENT"
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("handoff_timeout_retains_registered_id_under_one_budget/wait"))


def test_nul_bearing_terminal_frame_is_not_exec_success(tmp_path):
    """Terminal classification is over exact bytes, never Bash strings."""
    mod = _load()
    marker = tmp_path / "workload-started"
    argv_log = tmp_path / "stub-argv"
    gate_program = _w1_adversarial_gate_program(
        terminal=None, terminal_bytes=b"EXEC-\x00OK v1\n", status=0)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, reconcile_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["W1_STUB_ARGV_LOG"] = str(argv_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        rc = _w1_wait_for_exit(proc, rundir, site="nul_bearing_terminal_frame_is_not_exec_success/exit")
        assert rc == int(W1_RELEASE_FAILURE_CODE)
        assert not marker.exists()
        assert (rundir / "jobid").read_text().strip() == "stubhost-4242"
        calls = argv_log.read_text(encoding="utf-8").splitlines()
        assert calls[-1] == "reap --id stubhost-4242"
        protocol = (rundir / "registration-gate.protocol").read_text()
        assert "frame_hex=" in protocol and "00" in protocol
        terminal = (rundir / "registration-terminal-reader.out").read_text()
        assert "C:INVALID" in terminal and (
            "H:455845432d004f4b2076310a" in terminal)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=_w1_budget_s("nul_bearing_terminal_frame_is_not_exec_success/communicate"))


def test_abort_timeout_retains_inert_gate_under_one_budget(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    checked_wait_log = tmp_path / "checked-wait-entered"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, cleanup_seconds=3, checked_wait_probe=checked_wait_log,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_GATE_WITHHOLD_ABORT"] = "30"
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        rc = _w1_wait_for_exit_or_forbidden_checked_wait(
            proc, rundir, checked_wait_log, site="abort_timeout_retains_inert_gate_under_one_budget/exit")
        assert not marker.exists() and not signal_log.exists()
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "context=registrar-refused" in err
        assert "frame_rc=" in err and "wait=UNKNOWN" in err
        assert "release_writes=0" in err and "receipt=" in err
        terminal_readers = [record for record in _w1_owner_records(rundir)
                            if record["role"] == "terminal-reader"]
        assert len(terminal_readers) == 1, (
            "R12-N250-ZERO-BUDGET-SKIPPED-TERMINAL-OWNER")
        assert terminal_readers[0]["wait_ok"] == "1", terminal_readers
        assert terminal_readers[0]["descendants"] == "ABSENT", terminal_readers
        assert not checked_wait_log.exists(), (
            "abort settlement entered child wait before terminal protocol evidence")
        assert _w1_live_in_group(gate_pid), (
            "the withhold fixture retained no inert gate at classification")
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("abort_timeout_retains_inert_gate_under_one_budget/wait"))


def test_registration_cleanup_timeout_is_internal_and_names_retained_group(
        tmp_path):
    """ABORTED cannot become 91 when checked wait says 'not a child'."""
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    bash_env = tmp_path / "reject-gate-wait.bash"
    bash_env.write_text(
        "wait() {\n"
        "    if [ \"$1\" = '-n' ] && [ \"${4-}\" = \"$PYTEST_GATE_PID\" ]; then\n"
        "        builtin printf 'synthetic not-a-child\\n' >&2\n"
        "        return 127\n"
        "    fi\n"
        "    builtin wait \"$@\"\n"
        "}\n"
        "export -f wait\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["BASH_ENV"] = str(bash_env)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("registration_cleanup_timeout_is_internal_and_names_retained_group/run"))
    assert not marker.exists()
    assert "synthetic not-a-child" in (
        rundir / "registration-wait.err").read_text(encoding="utf-8")
    err = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "frame=ABORTED v1 reason=release-eof" in err
    assert "wait=UNKNOWN" in err and "group=UNKNOWN" in err
    gate_records = [record for record in _w1_owner_records(rundir)
                    if record["role"] == "gate"]
    assert len(gate_records) == 1 and gate_records[0]["wait_ok"] == "0"
    assert gate_records[0]["descendants"] == "UNKNOWN"
    assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE)


_W1_OBSERVATION_SUFFIX = "/registration-gate-fd-observer.out"


def _w1_run_with_a_slow_gate_fd_observation(tmp_path, delay_s, *, site):
    """Drive a runner whose gate fd OBSERVATION answers correctly but late.

    `ready_seconds=2` is what makes the remainder small at the moment the
    observation spawns -- the same shape the split exposed, where the forward
    phase had already spent most of its deadline before anything asked a
    question. Nothing else about the run is unusual: the workload is the
    ordinary success control's.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    observed = tmp_path / "observation-delay"
    owner = _w1_slow_observation_owner(
        mod, suffix=_W1_OBSERVATION_SUFFIX, delay_s=delay_s, marker=observed)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\nexit %d\n"
        % (shlex.quote(str(marker)), W1_WORKLOAD_CODE),
        reap_seconds=3, ready_seconds=2, timeout_owner_program=owner)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=_w1_budget_s(site))
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8") \
        if (rundir / "jobid.err").exists() else ""
    records = [record for record in _w1_owner_records(rundir)
               if record["role"] == "gate-fd-observer"]
    return result, evidence, records, observed, registrar, marker


_W1_OBSERVATION_TIMEOUT_FN = "registration_remaining_observation_timeout"


def _w1_runner_function_source(mod, name):
    """One shell function, lifted out of the FORMATTED production runner.

    Formatted, not raw: the template's `{{`/`}}` escapes are not shell, and a
    gate that read them would be judging a program that never runs.
    """
    body = mod.RUNNER.format(
        rundir="'/nonexistent'", run_id="'probe'", cmd="true",
        purpose="'probe'", origin="'probe'", cmdq="'true'",
        registration_probe="''", registration_gate="''",
        registration_bootstrap="''", registration_terminal_relay="''",
        registration_timeout_owner="''", registration_channel_reader="''",
        process_guard="''", workload_shim="''")
    opening = "%s() {\n" % name
    assert body.count(opening) == 1, (
        "the runner declares %r %d times, so this probe has no unique subject"
        % (name, body.count(opening)))
    start = body.index(opening)
    end = body.index("\n}\n", start) + len("\n}\n")
    source = body[start:end]
    assert source.count("\n}\n") == 1 and len(source) > 200, source
    return source


def _w1_observation_timeout(mod, *, active_in_us, lifecycle_in_us,
                            floor_s, reserve_us=0, grace_us=100000,
                            initialized=1):
    """Drive the real shell function with planted clock state.

    The seam, not the whole runner: every branch of the floor/ceiling/refusal
    arithmetic is reachable here in milliseconds, and none of them is
    reachable from a full runner run inside a test's budget.
    """
    source = _w1_runner_function_source(mod, _W1_OBSERVATION_TIMEOUT_FN)
    script = "\n".join([
        "set -u",
        "W1_NOW_US=1000000000",
        "W1_ACTIVE_DEADLINE_US=$((W1_NOW_US + %d))" % int(active_in_us),
        "W1_LIFECYCLE_DEADLINE_US=$((W1_NOW_US + %d))" % int(lifecycle_in_us),
        "W1_OWNER_KILL_GRACE_US=%d" % int(grace_us),
        "W1_OWNER_OBSERVATION_SECONDS=%d" % int(floor_s),
        "W1_LIFECYCLE_INITIALIZED=%d" % int(initialized),
        "W1_OWNER_TIMEOUT=UNSET",
        "W1_OWNER_KILL_AFTER=UNSET",
        "registration_now_us() { W1_NOW_US=$W1_NOW_US; }",
        source,
        "if %s %d; then" % (_W1_OBSERVATION_TIMEOUT_FN, int(reserve_us)),
        '    echo "OK $W1_OWNER_TIMEOUT $W1_OWNER_KILL_AFTER"',
        "else",
        '    echo "REFUSED $W1_OWNER_TIMEOUT"',
        "fi",
        "",
    ])
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    result = subprocess.run(
        ["bash", "-c", script], text=True, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=_w1_budget_s("_w1_observation_timeout/run"))
    assert result.returncode == 0, (result.returncode, result.stderr)
    row = result.stdout.split()
    assert row and row[0] in {"OK", "REFUSED"}, result.stdout
    return row


def test_an_owner_observation_takes_its_floor_when_the_phase_is_nearly_spent():
    """THE FLOOR BRANCH, at the seam, with no clock of its own.

    RED on the parent product: there is no observation timeout at all, so
    `_w1_runner_function_source` refuses by name. With the fix, a phase with a
    tenth of a second left still hands an observation its whole floor -- which
    is the entire point, because the question the observation asks does not
    get cheaper as the deadline it happens to run under gets closer.
    """
    mod = _load()
    floor = _w1_runner_deadline_constants()["W1_OWNER_OBSERVATION_SECONDS"]
    lean = _w1_observation_timeout(
        mod, active_in_us=100_000, lifecycle_in_us=60_000_000, floor_s=floor)
    assert lean[0] == "OK", lean
    assert float(lean[1]) == float(floor), (
        "an observation with %.1fs of forward deadline left was given %ss "
        "rather than its %ds floor, so its bound is still the forward phase's "
        "leftovers and expiry there is a false verdict"
        % (0.1, lean[1], floor))
    assert float(lean[2]) == 0.1, lean
    # AND THE FLOOR IS A FLOOR, NOT A VALUE: a phase with room to spare must
    # still hand out the larger remainder, or an observation could be cut
    # SHORTER than the deadline it runs under.
    roomy = _w1_observation_timeout(
        mod, active_in_us=30_000_000, lifecycle_in_us=60_000_000,
        floor_s=floor)
    assert roomy[0] == "OK" and float(roomy[1]) > float(floor), roomy


def test_an_owner_observation_never_outlives_the_lifecycle_deadline():
    """THE CEILING BRANCH. The total lifecycle cap stays absolute.

    A floor that could reach past the run's own lifecycle deadline would buy
    one false UNKNOWN by making the lifecycle cap untrue, which is the shape
    v3.66.1219 removed from test budgets. Unreachable from a whole-runner test
    inside any sane budget, and one assertion away here.
    """
    mod = _load()
    floor = _w1_runner_deadline_constants()["W1_OWNER_OBSERVATION_SECONDS"]
    row = _w1_observation_timeout(
        mod, active_in_us=100_000, lifecycle_in_us=1_100_000, floor_s=floor)
    assert row[0] == "OK", row
    assert float(row[1]) == 1.0, (
        "an observation whose floor is %ds was allowed %ss against a lifecycle "
        "deadline 1.1s away, so the total cap no longer caps it"
        % (floor, row[1]))
    exhausted = _w1_observation_timeout(
        mod, active_in_us=100_000, lifecycle_in_us=50_000, floor_s=floor)
    assert exhausted[0] == "REFUSED", (
        "an observation was started with the lifecycle deadline already inside "
        "the kill grace: %r" % (exhausted,))


def test_a_zero_observation_floor_reproduces_the_forward_remainder_exactly():
    """THE OVER-SENSITIVITY CONTROL FOR THE KNOB, and the parity proof.

    Two tests declare `observation_seconds=0` because their subject IS an
    observation being refused. That declaration is only honest if a zero floor
    behaves exactly as the parent did -- remainder minus the kill grace minus
    the caller's reserve, and a refusal when that is not positive.
    """
    mod = _load()
    row = _w1_observation_timeout(
        mod, active_in_us=5_000_000, lifecycle_in_us=60_000_000, floor_s=0,
        reserve_us=1_000_000)
    assert row[0] == "OK", row
    assert float(row[1]) == 3.9, (
        "a zero floor did not reproduce remainder(5.0s) - grace(0.1s) - "
        "reserve(1.0s): %r" % (row,))
    refused = _w1_observation_timeout(
        mod, active_in_us=1_000_000, lifecycle_in_us=60_000_000, floor_s=0,
        reserve_us=1_000_000)
    assert refused[0] == "REFUSED", (
        "a zero floor did not refuse a phase that cannot pay for the "
        "observation, so the two declared subjects no longer have a subject: "
        "%r" % (refused,))
    uninitialised = _w1_observation_timeout(
        mod, active_in_us=5_000_000, lifecycle_in_us=60_000_000, floor_s=3,
        initialized=0)
    assert uninitialised[0] == "REFUSED", (
        "an observation was bounded before the lifecycle clock existed")


def test_the_recorded_observation_cost_matches_a_real_observation():
    """THE MEASUREMENT IS PINNED TO THE HOST, not only to the arithmetic.

    `_W1_OBSERVATION_MEASURED_S` is the floor rule's only empirical input, and
    a rule of the form `floor >= measured x margin` gets EASIER as the measured
    term shrinks -- v3.66.1219's vacuity shape, and the escape v3.66.1226's
    first battery lost. So one complete observation is run here exactly as
    `registration_owner_spawn` runs it and the table entry is compared to the
    clock in BOTH directions.
    """
    mod = _load()
    owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM
    probe = mod.REGISTRATION_PROBE_PROGRAM
    assert "os.setsid()" in owner and "OWNER-READY v2" in owner, (
        "the timeout owner is not the shipped one, so this measurement is "
        "about something else")
    out = tmp_observation_out = None
    with tempfile.TemporaryDirectory() as scratch:
        out = os.path.join(scratch, "observation.out")
        err = os.path.join(scratch, "observation.err")
        pgid = os.getpgid(0)
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        started = time.monotonic()
        proc = subprocess.Popen(
            ["python3", "-c", owner, str(os.getpid()), "", out, err,
             "timeout", "--kill-after=0.100000", "30.000000",
             "python3", "-c", probe, "group", str(pgid), "/proc"],
            stdout=subprocess.PIPE, text=True, env=env)
        try:
            ready = proc.stdout.readline()
            rc = proc.wait(timeout=_w1_budget_s(
                "the_recorded_observation_cost_matches_a_real_observation/wait"))
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=_w1_budget_s(
                    "the_recorded_observation_cost_matches_a_real_observation"
                    "/wait-2"))
        elapsed = time.monotonic() - started
        assert ready.startswith("OWNER-READY v2 receipt="), (
            "the observation never announced itself, so the elapsed time below "
            "is not an observation's cost: %r" % (ready,))
        assert rc == 0, (rc, pathlib.Path(err).read_text(encoding="utf-8"))
        answer = pathlib.Path(out).read_text(encoding="utf-8").strip()
        assert answer.startswith("PRESENT|"), (
            "the observation did not answer the question it was asked: %r"
            % (answer,))
    assert elapsed <= _W1_OBSERVATION_MEASURED_S * _W1_RUNNER_STRETCH_FACTOR, (
        "one observation took %.4fs against a recorded cost of %.4fs and a "
        "%.1fx contention margin -- the table is stale, and the shipped floor "
        "is derived from it" % (elapsed, _W1_OBSERVATION_MEASURED_S,
                                _W1_RUNNER_STRETCH_FACTOR))
    assert _W1_OBSERVATION_MEASURED_S * _WARN_FACTOR >= elapsed, (
        "the recorded observation cost %.4fs is more than %.1fx below a real "
        "observation's %.4fs on this host. A floor rule of the form "
        "`floor >= measured x margin` gets EASIER as the measurement shrinks, "
        "so an understated cost silently retires the rule"
        % (_W1_OBSERVATION_MEASURED_S, _WARN_FACTOR, elapsed))


def test_a_slow_owner_observation_never_downgrades_a_correct_run(tmp_path):
    """AN OBSERVATION IS A QUESTION, AND ITS BOUND IS NOT THE FORWARD DEADLINE.

    RED, replayed against the parent `bd-wedge-hunt` at b489289: the gate fd
    observation is handed what remains of a 2s READY deadline -- about a second
    and a half -- so an observation that answers correctly in 2.5s is TERMed,
    `registration_authority_fd_observation` returns 1, and a run that was going
    to succeed aborts with `REGISTER-GATE-SETUP-FAILED phase=authority-fds`
    and the retained-uncertainty code. That is one of the two field failures
    the split's matched experiment captured, reproduced deterministically
    instead of by load.

    THE PRECONDITION IS ASSERTED BEFORE THE VERDICT. The injected delay writes
    the elapsed time it actually slept, so a run that reached the abort because
    the observation never ran at all cannot be read as this defect. A5: the
    distinctive diagnostic is asserted first, and the ordinary outcome
    assertions follow it.
    """
    delay_s = 2.5
    (result, evidence, records, observed, registrar,
     marker) = _w1_run_with_a_slow_gate_fd_observation(
        tmp_path, delay_s,
        site="a_slow_owner_observation_never_downgrades_a_correct_run/run")
    # THE DISTINCTIVE DIAGNOSTIC COMES FIRST. On the parent the observation is
    # not merely cut short, it is REFUSED before it starts -- what remains of a
    # spent forward deadline does not even cover the caller's reserve -- so the
    # delay marker below can never exist there. A precondition asserted first
    # would replace the failure this node exists to report with "the delay
    # never ran", which is CLAUDE.md A5's laundering shape exactly.
    assert "phase=authority-fds" not in evidence, (
        "a gate fd OBSERVATION that only needed %.1fs to answer was bounded by "
        "what remained of the FORWARD deadline, returned UNKNOWN, and "
        "downgraded a run that was going to succeed to %s. An observation's "
        "expiry is a false verdict, not a subject: %r"
        % (delay_s, W1_RETAINED_FAILURE_CODE, evidence))
    # AND THE GREEN IS NOT VACUOUS: the observation really did take longer than
    # the forward phase had left, so the run above was carried by the floor.
    assert observed.exists(), (
        "the run succeeded without the injected observation delay ever "
        "running, so this node proves nothing: stderr=%r" % (result.stderr,))
    slept = [float(row) for row in
             observed.read_text(encoding="ascii").split()]
    assert len(slept) == 1 and slept[0] >= delay_s, (
        "the gate fd observation did not actually take %.1fs: %r"
        % (delay_s, slept))
    assert len(records) == 1 and records[0]["wait_ok"] == "1", records
    assert records[0]["descendants"] == "ABSENT", records
    assert result.returncode == 0, (
        "runner returned %d; stderr=%r" % (result.returncode, result.stderr))
    assert registrar.exists() and marker.exists(), (
        "the run did not reach registration and workload after the slow "
        "observation was allowed to answer")


def test_the_observation_floor_still_bounds_an_observation_that_will_not_answer(
        tmp_path):
    """THE OVER-SENSITIVITY CONTROL, and the proof the refusal fires here.

    Without it, a change that simply stopped bounding observations would pass
    the node above. The delay is longer than the shipped floor, so the runner
    must still end the observation and still report the phase by name -- and
    the injected sleep must NOT have completed, which is what separates "the
    floor cut it off" from "it answered and something else failed".
    """
    floor = _w1_runner_deadline_constants()["W1_OWNER_OBSERVATION_SECONDS"]
    delay_s = float(floor) * 2.0
    (result, evidence, records, observed, registrar,
     marker) = _w1_run_with_a_slow_gate_fd_observation(
        tmp_path, delay_s,
        site="the_observation_floor_still_bounds_an_observation_that_will"
             "_not_answer/run")
    assert len(records) == 1, (
        "the gate fd observation never spawned, so nothing here is about a "
        "bound at all: %r" % (records,))
    assert not observed.exists(), (
        "the %.1fs observation ran to completion against a %ds floor, so the "
        "floor did not bound it and this control proves nothing"
        % (delay_s, floor))
    assert "phase=authority-fds" in evidence, (
        "an observation that never answered was not reported by its phase, so "
        "the floor has stopped bounding observations altogether: %r"
        % (evidence,))
    assert result.returncode == int(W1_RETAINED_FAILURE_CODE), (
        "runner returned %d; stderr=%r" % (result.returncode, result.stderr))
    assert not registrar.exists() and not marker.exists(), (
        "an unanswered observation still crossed into registration")


def test_term_resistant_observer_stays_inside_gate_budget(tmp_path):
    """An observer is an owned subprocess and cannot outlive the phase budget."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    (bash_env, entered_fd, _release, helper_pid_path,
     child_pid_path) = _w1_hung_process_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=2, cleanup_seconds=2, observation_seconds=0,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        assert _w1_await_fifo(entered_fd, site="term_resistant_observer_stays_inside_gate_budget/fifo") == "observer-entered\n"
        helper_pid = int(helper_pid_path.read_text().strip())
        child_pid = int(child_pid_path.read_text().strip())
        assert _w1_pid_is_live(helper_pid) and _w1_pid_is_live(child_pid)
        try:
            rc = proc.wait(timeout=_w1_budget_s("term_resistant_observer_stays_inside_gate_budget/wait"))
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "TERM-resistant observation escaped its owner") from exc
        assert not _w1_pid_is_live(helper_pid)
        assert not _w1_pid_is_live(child_pid)
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "process-observer"]
        assert len(records) == 1 and records[0]["wait_ok"] == "1", records
        assert records[0]["descendants"] == "ABSENT", records
        assert not registrar.exists() and not marker.exists()
        assert "initial-observation-unavailable" in (
            rundir / "jobid.err").read_text(encoding="utf-8")
        assert rc == int(W1_RETAINED_FAILURE_CODE)
    finally:
        os.close(entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=_w1_budget_s("term_resistant_observer_stays_inside_gate_budget/wait-2"))


def test_monotonic_clock_rollback_fails_closed_without_extending_budget(tmp_path):
    """A decreasing injected sample expires authority; it never gains time."""
    mod = _load()
    marker = tmp_path / "workload-started"
    gate_program = _w1_pre_ready_descendant_gate_program()
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=1, gate_program=gate_program,
        monotonic_samples=[10_000_000, 9_000_000, 8_000_000],
    )
    pytest_pid_path = rundir / "pytest.pid"
    bash_env, publish_entered_fd, publish_release = _w1_delay_shell_publish(
        tmp_path, pytest_pid_path)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    gate_pid = -1
    try:
        # Rollback deliberately expires authority as soon as the controller
        # samples the clock.  The gate may therefore be fully reaped before a
        # scheduler-delayed test process can observe it live.  Its durable PID
        # is the non-racy proof that the real gate existed; post-exit absence
        # proves the rollback path did not abandon the owned group.
        assert _w1_await_fifo(publish_entered_fd, site="monotonic_clock_rollback_fails_closed_without_extending_budget/fifo") == (
            "publish-boundary-entered\n")
        try:
            assert not pytest_pid_path.exists(), (
                "pytest pid became visible before its payload was complete")
        finally:
            _w1_release_fifo(publish_release)
        gate_pid = _w1_wait_for_path(
            pytest_pid_path, content="integer")
        rc = proc.wait(timeout=_w1_budget_s("monotonic_clock_rollback_fails_closed_without_extending_budget/wait"))
        assert not marker.exists()
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "clock=ROLLBACK" in evidence
        assert not _w1_live_in_group(gate_pid), (
            "rollback returned while its real gate group remained live")
        assert rc in {
            int(W1_RETAINED_FAILURE_CODE), int(W1_RELEASE_FAILURE_CODE)}
    finally:
        os.close(publish_entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("monotonic_clock_rollback_fails_closed_without_extending_budget/wait-2"))


def test_cancellation_after_relay_before_gate_settles_the_acquired_owner(
        tmp_path):
    """The first acquired child is already behind INT and EXIT guards."""
    mod = _load()
    marker = tmp_path / "workload-started"
    entered, release, entered_fd = _w1_fifo_barrier(
        tmp_path, "after-relay-acquire")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=8,
        after_relay_acquire_barrier=(entered, release),
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    relay_receipt = None
    try:
        assert _w1_await_fifo(entered_fd, site="cancellation_after_relay_before_gate_settles_the_acquired_owner/fifo") == "relay-acquired\n"
        relay_receipt_path = rundir / "injected-relay.receipt"
        relay_receipt = tuple(int(value) for value in
                              relay_receipt_path.read_text().split(":"))
        assert len(relay_receipt) == 5
        relay_pid, relay_ppid, relay_pgid, relay_sid, relay_start = relay_receipt
        assert relay_ppid == proc.pid and relay_pid == relay_pgid
        assert _w1_pid_is_live(relay_pid)
        os.kill(proc.pid, signal.SIGINT)
        with open(release, "w", encoding="ascii") as stream:
            stream.write("continue\n")
        assert proc.wait(timeout=_w1_budget_s("cancellation_after_relay_before_gate_settles_the_acquired_owner/wait")) == int(W1_RETAINED_FAILURE_CODE)
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "terminal-relay"]
        assert len(records) == 1, records
        assert records[0]["waited_pid"] == str(relay_pid)
        assert records[0]["wait_ok"] == "1"
        assert records[0]["descendants"] == "ABSENT"
        gate_records = [record for record in _w1_owner_records(rundir)
                        if record["role"] == "gate"]
        assert len(gate_records) == 1
        assert gate_records[0]["owner_pid"] == "MISSING"
        assert gate_records[0]["stop"] == "PID-MISSING"
        assert not _w1_pid_is_live(relay_pid) and not marker.exists()
    finally:
        os.close(entered_fd)
        if relay_receipt is not None:
            relay_pid, _ppid, relay_pgid, _sid, relay_start = relay_receipt
            try:
                current = _w1_proc_receipt(relay_pid)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                current = None
            if current == (relay_pid, relay_pgid, relay_start):
                _w1_kill_group(relay_pgid)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))


def test_exit_guard_settles_a_post_setsid_owner_after_nounset(tmp_path):
    """An unexpected shell exit drains the exact active owner before return."""
    mod = _load()
    acquired = tmp_path / "abnormal-owner-acquired"
    owner_receipt_path = tmp_path / "abnormal-owner.receipt"
    owner_entered, owner_release, owner_entered_fd = _w1_fifo_barrier(
        tmp_path, "abnormal-owner")
    os.mkfifo(acquired)
    anchor = "os.setsid()\n"
    malicious = (
        "import pathlib, signal\n"
        + "raw = pathlib.Path('/proc/self/stat').read_text(encoding='ascii')\n"
        + "tail = raw.rsplit(') ', 1)[1].split()\n"
        + "pathlib.Path(%r).write_text('%%d:%%d:%%d:%%d:%%s' %% "
          "(os.getpid(), os.getppid(), os.getpgrp(), os.getsid(0), tail[19]), "
          "encoding='ascii')\n" % str(owner_receipt_path)
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(owner_entered)
        + "    stream.write('owner-receipt-stable\\n')\n"
        + "with open(%r, 'r', encoding='ascii') as stream:\n" % str(owner_release)
        + "    stream.readline()\n"
        + "os.close(1)\n"
        + "os.close(2)\n"
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(acquired)
        + "    stream.write(str(os.getpid()) + '\\n')\n"
        + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "while True:\n"
        + "    signal.pause()\n"
    )
    injection = (
        anchor
        + "if stdout_path.endswith('/registration-ready-reader.out'):\n"
        + "".join("    " + line + "\n" for line in malicious.splitlines())
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path, "#!/bin/bash\nexit 0\n",
        reap_seconds=6,
        timeout_owner_program=timeout_owner,
        abnormal_owner_fifo=acquired,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))

    owner_pidfd = -1
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert _w1_await_fifo(owner_entered_fd, site="exit_guard_settles_a_post_setsid_owner_after_nounset/fifo") == "owner-receipt-stable\n"
        saved = tuple(int(value) for value in owner_receipt_path.read_text(
            encoding="ascii").split(":"))
        assert len(saved) == 5 and saved[0] == saved[2] == saved[3]
        pid = saved[0]
        raw = pathlib.Path("/proc", str(pid), "stat").read_text(
            encoding="ascii")
        head, tail_text = raw.rsplit(") ", 1)
        tail = tail_text.split()
        current = (int(head.split(" (", 1)[0]), int(tail[1]),
                   int(tail[2]), int(tail[3]), int(tail[19]))
        assert current == saved, "the injected owner identity drifted before release"
        owner_pidfd = os.pidfd_open(pid)
        with open(owner_release, "w", encoding="ascii") as stream:
            stream.write("trigger-nounset\n")
        try:
            _stdout, _stderr = proc.communicate(timeout=_w1_budget_s("exit_guard_settles_a_post_setsid_owner_after_nounset/communicate"))
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER") from exc

        evidence = ((rundir / "jobid.err").read_text(encoding="utf-8")
                    if (rundir / "jobid.err").is_file() else "")
        assert "EXIT-GUARD" in evidence and "restored=1" in evidence, (
            "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER")
        assert proc.returncode != 0
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "ready-reader"]
        assert len(records) == 1, (
            "EXIT-GUARD-DID-NOT-SETTLE-ACTIVE-OWNER", records)
        record = records[0]
        assert record["waited_pid"] == record["owner_pid"]
        assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
        assert record["stop"] == "TERM-GRACE-KILL-GROUP"
        assert not _w1_pid_is_live(int(record["owner_pid"]))
    finally:
        os.close(owner_entered_fd)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=_w1_budget_s("exit_guard_settles_a_post_setsid_owner_after_nounset/wait"))
        # The pidfd was acquired while the complete five-field receipt was
        # still stable.  It remains an exact failure-safe capability after the
        # shell exits and Linux reparents an intentionally abandoned owner.
        if (owner_pidfd >= 0
                and not select.select([owner_pidfd], [], [], 0)[0]):
            signal.pidfd_send_signal(owner_pidfd, signal.SIGKILL, None, 0)
            assert select.select([owner_pidfd], [], [], 5)[0], (
                "failure-safe pidfd cleanup did not settle the injected owner")
        if owner_pidfd >= 0:
            os.close(owner_pidfd)


def test_cancellation_during_delayed_ready_preserves_primary(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    before_ready = tmp_path / "before-ready"
    release_ready = tmp_path / "release-ready"
    os.mkfifo(release_ready)
    gate_program = _w1_adversarial_gate_program(
        terminal="ABORTED v1 reason=release-eof",
        before_ready_marker=before_ready, ready_release=release_ready)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, gate_program=gate_program,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(before_ready)
        os.kill(proc.pid, signal.SIGINT)
        with release_ready.open("w", encoding="utf-8") as stream:
            stream.write("release\n")
        rc = proc.wait(timeout=_w1_budget_s("cancellation_during_delayed_ready_preserves_primary/wait"))
        assert not registrar.exists() and not marker.exists(), (
            "N234-CANCEL-TRAP-CROSSED-LATE-AUTHORITY")
        assert (rundir / "exitcode").read_text().strip() == "130"
        assert "REGISTER-CANCELLED" in (
            rundir / "jobid.err").read_text(encoding="utf-8")
        assert rc == 130
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("cancellation_during_delayed_ready_preserves_primary/wait-2"))


def test_cancellation_during_pre_register_observation_never_registers(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, observer_entered, observer_release = _w1_block_process_probe(
        tmp_path, on_call=2)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(observer_entered)
        os.kill(proc.pid, signal.SIGINT)
        with observer_release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        rc = proc.wait(timeout=_w1_budget_s("cancellation_during_pre_register_observation_never_registers/wait"))
        assert not registrar.exists() and not marker.exists(), (
            "N244-CANCELLED-OBSERVER-CROSSED-REGISTRAR")
        assert (rundir / "exitcode").read_text().strip() == "130"
        assert rc == 130
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("cancellation_during_pre_register_observation_never_registers/wait-2"))


def test_cancellation_during_group_observer_forbids_registration(tmp_path):
    """The sole-group authority owner cannot cross into registrar after INT."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, entered_fd, release = _w1_block_probe_fifo(tmp_path, "group")
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["BASH_ENV"] = str(bash_env)
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, live = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="cancellation_during_group_observer_forbids_registration/fifo") == "group-observer-entered\n"
        assert live == [str(gate_pid)]
        assert not registrar.exists() and not marker.exists()
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert not registrar.exists() and not marker.exists()
        assert proc.wait(timeout=_w1_budget_s("cancellation_during_group_observer_forbids_registration/wait")) == 130
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("cancellation_during_group_observer_forbids_registration/wait-2"))


def test_pre_observation_cancellation_settles_only_in_the_top_shell(tmp_path):
    """A latched cancel cannot let a command-substitution child finalize files."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, cancel_before_observation=True,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("pre_observation_cancellation_settles_only_in_the_top_shell/run"))

    assert not registrar.exists() and not marker.exists()
    assert result.returncode == 130
    assert (rundir / "exitcode").read_text().strip() == "130"
    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert evidence.count("REGISTER-CANCELLED") == 1, evidence
    gate_records = [record for record in _w1_owner_records(rundir)
                    if record["role"] == "gate"]
    assert len(gate_records) == 1, gate_records
    runner = mod.RUNNER
    assert 'W1_CURRENT_OBSERVATION="$(' not in runner
    assert 'PYTEST_INITIAL_OBSERVATION="$(' not in runner
    assert 'PYTEST_INITIAL_GROUP="$(' not in runner
    assert '[ "$BASHPID" = "$$" ] || return 1' in runner


def test_cancellation_during_gate_settlement_wait_preserves_primary(tmp_path):
    """A signal inside definite-refusal settlement cannot be laundered to 91."""
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, entered_fd, release = _w1_gate_wait_barrier(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=W1_REGISTER_FAILURE_CODE, sleep=0.1))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        assert _w1_await_fifo(entered_fd, site="cancellation_during_gate_settlement_wait_preserves_primary/fifo") == "gate-wait-entered\n"
        assert registrar.exists() and not marker.exists()
        os.kill(proc.pid, signal.SIGINT)
        with release.open("w", encoding="utf-8") as stream:
            stream.write("continue\n")
        assert _w1_wait_for_exit(proc, rundir, site="cancellation_during_gate_settlement_wait_preserves_primary/exit") == 130
        assert not marker.exists() and not _w1_live_in_group(gate_pid)
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "primary_cancel=130" in evidence
        gate_records = [record for record in _w1_owner_records(rundir)
                        if record["role"] == "gate"]
        assert gate_records and gate_records[-1]["wait_ok"] == "1"
    finally:
        os.close(entered_fd)
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("cancellation_during_gate_settlement_wait_preserves_primary/wait"))


def test_runner_cancellation_closes_gate_and_does_not_start_workload(tmp_path):
    mod = _load()
    marker = tmp_path / "workload-started"
    registrar = tmp_path / "registrar-started"
    bash_env, signal_log = _w1_signal_probe(tmp_path)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, registrar_seconds=3,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n", sleep=300))
    env["W1_REGISTRAR_MARKER"] = str(registrar)
    env["BASH_ENV"] = str(bash_env)
    env["W1_SIGNAL_LOG"] = str(signal_log)
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        signalled = time.monotonic()
        os.kill(proc.pid, signal.SIGINT)
        rc = proc.wait(timeout=_w1_budget_s("runner_cancellation_closes_gate_and_does_not_start_workload/wait"))
        settled_in = time.monotonic() - signalled
        assert rc == 130
        # THE RECORDED SETTLEMENT COST, COMPARED TO THE CLOCK RATHER THAN TO
        # ITSELF. This is the exact shape `_W1_SETTLEMENT_MEASURED_S` was taken
        # from -- SIGINT delivered to runner exited, with a registrar that never
        # returns -- so it is the one place a restated baseline can be caught by
        # a real run. v3.66.1219's first over-sensitivity control compared
        # baselines only to each other and stayed green while a 168s cost was
        # written down as 7s.
        # THE MARGIN IS THE DEADLINE'S OWN ARITHMETIC, not the tighter warning
        # one. 1226 measured a 4.13x RIGHT-CENSORED contention stretch, so a 3x
        # margin here would fire on correct work under `-n 24` -- which is the
        # defect class this whole row descends from. 6x is what the deadline is
        # derived with, so a baseline this check accepts is a baseline the
        # deadline is safe for. RESIDUAL, same as 1226 recorded for budgets: a
        # scheduling stall of the 1209 magnitude would still cross it.
        assert settled_in <= _W1_SETTLEMENT_MEASURED_S * _W1_RUNNER_STRETCH_FACTOR, (
            "settlement took %.3fs against a recorded baseline of %.4fs "
            "(%.1fx, past the %.1fx margin the deadline is derived with). The "
            "deadline derived from that baseline is now too tight; re-measure "
            "it rather than widening the margin."
            % (settled_in, _W1_SETTLEMENT_MEASURED_S,
               settled_in / _W1_SETTLEMENT_MEASURED_S,
               _W1_RUNNER_STRETCH_FACTOR))
        assert not marker.exists() and not signal_log.exists()
        assert (rundir / "exitcode").read_text().strip() == "130"
        err = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "REGISTER-CANCELLED status=130" in err
        assert "frame=ABORTED v1 reason=release-eof" in err and "wait=0" in err
        assert not _w1_live_in_group(gate_pid)
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s("runner_cancellation_closes_gate_and_does_not_start_workload/wait-2"))


def test_the_settlement_deadline_still_bounds_a_gate_that_will_not_settle(
        tmp_path):
    """THE OVER-SENSITIVITY CONTROL. The fix must not delete the deadline.

    Same planted gate, same signal, same everything -- except that this run
    DECLARES a settlement deadline below the gate's delay. The runner must
    still give up and report the retained-uncertainty code, which is what
    proves the deadline is live rather than removed. Without this arm, a
    change that simply stopped bounding settlement would pass the test above.
    """
    mod = _load()
    settled = tmp_path / "gate-settlement-entered"
    marker, registrar, rundir, proc = _w1_cancel_a_registrar_bound_runner(
        tmp_path, mod,
        gate_program=_w1_slow_settlement_gate_program(
            settled, _W1_SLOW_SETTLEMENT_S),
        cleanup_seconds=1)
    gate_pid = -1
    try:
        gate_pid, _ = _w1_wait_for_gate(rundir)
        _w1_wait_for_path(registrar)
        os.kill(proc.pid, signal.SIGINT)
        rc = proc.wait(timeout=_w1_budget_s(
            "the_settlement_deadline_still_bounds_a_gate_that_will_not_settle/wait"))
        assert settled.is_file(), (
            "the planted gate never entered its settlement delay")
        evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
        assert "REGISTER-CANCELLED status=130" in evidence, evidence
        assert rc == int(W1_RETAINED_FAILURE_CODE), (
            "the settlement deadline no longer bounds an unsettleable gate, so "
            "the runner would wait on it for as long as it takes")
        assert (rundir / "exitcode").read_text().strip() == W1_RETAINED_FAILURE_CODE
        assert not marker.exists()
    finally:
        _w1_kill_group(gate_pid)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_w1_budget_s(
                "the_settlement_deadline_still_bounds_a_gate_that_will_not_settle/wait-2"))


def test_the_group_census_never_forks_and_so_cannot_count_itself(tmp_path):
    """ROW 231/249, both arms forced rather than timed.

    ARM 1 -- SNAPSHOT VOLATILITY. A real peer is live when /proc is listed and
    gone before the result is re-statted. That is a valid snapshot, not proof
    that the census forked an instrument into its own denominator.

    ARM 2 -- NO FORK AT ALL. Every subprocess launcher records then raises. The
    real census fires zero launchers; the former `ps` implementation is replayed
    as a negative control and fires exactly one. Thus a rewrite back to `ps`
    still goes RED without treating an unrelated process exit as the defect.
    """
    own_group = os.getpgid(0)

    # ARM 1: plant a real group member, take the census, then make that exact
    # member vanish before the test re-stats the snapshot.
    probe = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline()"],
                             stdin=subprocess.PIPE, text=True)
    try:
        assert _w1_stat_pgid_and_state(probe.pid)[0] == str(own_group), (
            "precondition: an ordinary child shares the caller's process group, "
            "which is why a census that forks counts its own instrument")
        reported = _w1_live_in_group(own_group)
        assert str(os.getpid()) in reported, (
            "precondition: this process is in its own group and must be reported")
        assert str(probe.pid) in reported, (
            "the census must see a real live member of the group")
        probe.stdin.close()
        probe.wait(timeout=_w1_budget_s(
            "the_group_census_never_forks_and_so_cannot_count_itself/wait"))
        assert _w1_stat_pgid_and_state(probe.pid) is None, (
            "precondition: the planted group member did not vanish after the "
            "census snapshot")
        assert str(probe.pid) in reported, (
            "the valid snapshot lost the exact member that was live when read")
    finally:
        if probe.poll() is None:
            probe.kill()
            probe.wait(timeout=_w1_budget_s(
                "the_group_census_never_forks_and_so_cannot_count_itself/wait"))

    # A reaped child is a zombie or gone; either way it is not live.
    assert str(probe.pid) not in _w1_live_in_group(own_group)

    # ARM 2: no subprocess launch of any kind. This is the exact former
    # implementation, retained locally as the negative control for the defect.
    def _forking_census(pgid):
        result = subprocess.run(["ps", "-eo", "pgid=,pid=,stat="],
                                capture_output=True, text=True)
        rows = [line.split() for line in result.stdout.splitlines()
                if line.split()]
        return [row[1] for row in rows
                if row[0] == str(pgid) and not row[2].startswith("Z")]

    import unittest.mock as _mock
    fired = []

    def _banned(launcher):
        def _fail(*args, **kwargs):
            fired.append(launcher)
            raise AssertionError(
                "the group census launched a subprocess; its cost then scales "
                "with the whole host and it counts its own instrument (row 231)")
        return _fail

    with _mock.patch.object(subprocess, "run", _banned("subprocess.run")), \
            _mock.patch.object(subprocess, "Popen", _banned("subprocess.Popen")), \
            _mock.patch.object(os, "popen", _banned("os.popen")):
        assert str(os.getpid()) in _w1_live_in_group(own_group)
        assert _w1_group_has_at_least(own_group, 1)
        assert fired == [], (
            "the shipped census fired a subprocess launcher: %r" % fired)
        with pytest.raises(AssertionError, match="counts its own instrument"):
            _forking_census(own_group)
        assert fired == ["subprocess.run"], (
            "the known-bad forking census did not fire the tripwire exactly once: "
            "%r" % fired)


def test_group_census_transform_control_imports_without_asserting_no_fork():
    """Mutation control: collection/import alone is not a census verdict."""
    importlib.import_module(__name__)


#: A group leader that grows a CHILD and a GRANDCHILD on demand. Descent has
#: to be transitive to see the third member, and a one-member group cannot
#: tell a transitive probe from a one-level one.
_W1_GROWABLE_GROUP_LEADER = (
    "import os, sys, time\n"
    "os.setpgrp()\n"
    "with open(%r, 'w') as handle:\n"
    "    handle.write(str(os.getpgid(0)))\n"
    "if sys.stdin.readline():\n"
    "    if os.fork() == 0:\n"
    "        os.fork()\n"
    "        time.sleep(300)\n"
    "        os._exit(0)\n"
    "sys.stdin.readline()\n"
)


def test_the_cheap_presence_probe_agrees_with_the_complete_census(tmp_path):
    """The fast path must not be a different answer from the slow one.

    A probe that stops early is only safe if it agrees with the full census on
    the THRESHOLD question. Both directions are checked against a real planted
    group, and the absence direction is checked against a group that is gone --
    the case the probe must never be used to decide, asserted here so the
    distinction is recorded rather than assumed.
    """
    entered = tmp_path / "probe-alive"
    child = subprocess.Popen(
        [sys.executable, "-c", _W1_GROWABLE_GROUP_LEADER % str(entered)],
        stdin=subprocess.PIPE, text=True)
    try:
        pgid = _w1_wait_for_path(entered, content="integer")
        assert pgid == child.pid, "precondition: the child leads its own group"

        census = _w1_live_in_group(pgid)
        assert census == [str(child.pid)], census
        assert _w1_group_has_at_least(pgid, 1) is True
        assert _w1_group_has_at_least(pgid, 2) is False, (
            "the probe claimed more members than the census found")
        assert _w1_group_has_at_least(pgid, 0) is True

        # A CHILD AND A GRANDCHILD, because v3.66.1230 reaches the answer by
        # DESCENT and a one-member group cannot tell a one-level probe from a
        # transitive one. The complete census is the oracle at every size.
        child.stdin.write("grow\n")
        child.stdin.flush()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(_w1_live_in_group(pgid)) < 3:
            time.sleep(0.01)
        grown = sorted(_w1_live_in_group(pgid), key=int)
        assert len(grown) == 3, (
            "precondition: the planted group never reached three members: %r"
            % (grown,))
        assert _w1_children_of(pgid) != [], (
            "precondition: the leader has a child to descend into")
        assert len(_w1_children_of(pgid)) == 1, (
            "precondition: the third member is a GRANDCHILD, so descent has "
            "to be transitive to find it: %r" % (_w1_children_of(pgid),))
        for wanted in (1, 2, 3):
            assert _w1_group_has_at_least(pgid, wanted) is True, wanted
        assert _w1_group_has_at_least(pgid, 4) is False, (
            "the probe claimed a fourth member the census cannot see")
    finally:
        # THE DESCENDANTS OUTLIVE THEIR LEADER unless the whole group is
        # signalled, and a leaked pair would make the absence arm below assert
        # the opposite of what it means to.
        _w1_kill_group(child.pid)
        child.stdin.close()
        child.wait(timeout=_w1_budget_s("the_cheap_presence_probe_agrees_with_the_complete_census/wait"))

    # Group gone: the COMPLETE census is what proves absence, and the probe
    # must agree here even though it is not the instrument for that question.
    drain = time.monotonic() + 5.0
    while time.monotonic() < drain and _w1_live_in_group(child.pid):
        time.sleep(0.01)
    assert _w1_live_in_group(child.pid) == []
    assert _w1_group_has_at_least(child.pid, 1) is False


def test_the_presence_probe_costs_the_group_and_not_the_host(tmp_path):
    """ROW 231, PART A. The poll loop must not charge for the whole host.

    v3.66.1213 stopped the census forking `ps`, and that was the correctness
    half. THE COST HALF SURVIVED: the presence probe fell through to a walk of
    every pid on the host whenever the leader alone did not satisfy `wanted`,
    which is precisely the state `_w1_wait_for_gate` sits in while it waits at
    10ms intervals for the second member to appear. v3.66.1224 measured one
    full walk at 39.3ms median over 856 pids. Replacing a fork with a walk of
    the same denominator is the same defect wearing different clothes.

    THE ASSERTION IS A COUNT, NOT A CLOCK, so it cannot be flaky and cannot
    pass on a fast host. It counts the per-pid `/proc/<pid>/stat` reads the
    probe performs and compares them to the size of the process table, which
    is asserted large first so the bound means something.
    """
    entered = tmp_path / "probe-alive"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys; os.setpgrp(); open(%r,'w').write(str(os.getpgid(0)));"
         " sys.stdin.readline()" % str(entered)],
        stdin=subprocess.PIPE, text=True)
    reads = []
    real = _w1_stat_pgid_and_state

    def counting(pid):
        reads.append(str(pid))
        return real(pid)

    try:
        pgid = _w1_wait_for_path(entered, content="integer")
        assert pgid == child.pid, "precondition: the child leads its own group"
        table = [p for p in os.listdir("/proc") if p.isdigit()]
        assert len(table) >= 50, (
            "only %d processes on this host, so a bound of 'much less than "
            "the table' proves nothing" % len(table))
        assert _w1_live_in_group(pgid) == [str(child.pid)], (
            "precondition: the planted group has exactly one member")

        module = sys.modules[__name__]
        polls = 20
        try:
            setattr(module, "_w1_stat_pgid_and_state", counting)
            for _ in range(polls):
                # The UNSATISFIED threshold: exactly what a poll loop asks
                # while it waits, and the state the old probe answered by
                # walking every pid on the box.
                assert _w1_group_has_at_least(pgid, 2) is False
        finally:
            setattr(module, "_w1_stat_pgid_and_state", real)

        assert reads, "the probe read nothing, so it was not the subject"
        ceiling = polls * 8
        assert len(reads) <= ceiling, (
            "%d per-pid /proc reads over %d polls against a %d-process table: "
            "the probe still costs the HOST rather than the GROUP, so this "
            "module's wall time is a claim about how busy the box is (row 231)"
            % (len(reads), polls, len(table)))
        assert len(reads) >= polls, (
            "fewer reads than polls means the probe short-circuited without "
            "observing anything, and the ceiling above would be vacuous")
    finally:
        child.stdin.close()
        child.wait(timeout=_w1_budget_s(
            "the_presence_probe_costs_the_group_and_not_the_host/wait"))


def test_a_non_descendant_group_member_fails_the_probe_closed_not_open(
        tmp_path):
    """THE STATED BOUNDARY OF THE FAST PATH, asserted rather than assumed.

    Descent from the leader sees a member only if it is IN the leader's
    subtree. Every group this module drives is created by bash job control, so
    the two sets coincide -- but a process placed in the group from OUTSIDE
    that subtree is invisible to the probe. This plants exactly that shape and
    proves the disagreement is a FALSE NEGATIVE: the complete census still
    reports both members, and the probe under-reports. A caller therefore
    polls again and finally raises its own assertion, which is fail-closed; a
    probe that over-reported would let an absence claim pass on a live group.
    """
    ready = tmp_path / "leader-pgid"
    leader = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys; os.setpgrp(); open(%r,'w').write(str(os.getpgid(0)));"
         " sys.stdin.readline()" % str(ready)],
        stdin=subprocess.PIPE, text=True)
    joined = None
    try:
        pgid = _w1_wait_for_path(ready, content="integer")
        assert pgid == leader.pid
        # A SIBLING of the leader, put into the leader's group by its own
        # parent. It is in the group and it is not in the subtree.
        joined = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.readline()"],
            stdin=subprocess.PIPE, text=True,
            process_group=pgid)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if sorted(_w1_live_in_group(pgid)) == sorted(
                    [str(leader.pid), str(joined.pid)]):
                break
            time.sleep(0.01)
        census = sorted(_w1_live_in_group(pgid), key=int)
        assert census == sorted([str(leader.pid), str(joined.pid)], key=int), (
            "precondition: the planted outsider never joined the group: %r"
            % (census,))
        assert str(joined.pid) not in _w1_children_of(pgid), (
            "precondition: the outsider is not a child of the leader")

        assert _w1_group_has_at_least(pgid, 1) is True
        assert _w1_group_has_at_least(pgid, 2) is False, (
            "the probe claimed to see a member outside the leader's subtree; "
            "if it can do that its cost is back to the size of the host")
    finally:
        for proc in (joined, leader):
            if proc is None:
                continue
            proc.stdin.close()
            proc.wait(timeout=_w1_budget_s(
                "a_non_descendant_group_member_fails_the_probe_closed_not_open/wait"))


def test_an_absence_claim_is_immune_to_a_live_neighbour_group(tmp_path):
    """ROW 231, PART B. THE PROOF THAT LET THIS MODULE BE SPLIT.

    The row asks for it in exactly these terms: "tests that assert group
    ABSENCE must be proven immune to a neighbour's pids before any split, and
    that proof is the work, not the assumption." Once two halves of this family
    run on different workers at the same time, every
    `assert not _w1_live_in_group(gate_pid)` is made while somebody else's
    group is alive on the same host.

    THE MECHANISM, not a probability argument: the census filters on an EXACT
    pgid, and a pgid IS a live pid, so two concurrently live groups cannot
    share one. This plants a NEIGHBOUR that is alive, three members deep, and
    asserts that the absence claim about a different, dead group is unmoved --
    with the neighbour's liveness asserted at the moment of the claim, so an
    empty answer cannot come from an empty host.

    WHAT THIS DOES NOT PROVE, recorded rather than implied: pid RECYCLING. If
    the kernel reissues a dead group's pgid to a new group leader, the census
    would count it. `pid_max` is 4194304 and allocation is sequential, so reuse
    inside one assertion's window needs a full wraparound; that is a bound on
    the hazard and not a mechanism against it, and the backlog row carries it.
    """
    ready_a = tmp_path / "neighbour-pgid"
    neighbour = subprocess.Popen(
        [sys.executable, "-c", _W1_GROWABLE_GROUP_LEADER % str(ready_a)],
        stdin=subprocess.PIPE, text=True)
    ready_b = tmp_path / "subject-pgid"
    subject = subprocess.Popen(
        [sys.executable, "-c", _W1_GROWABLE_GROUP_LEADER % str(ready_b)],
        stdin=subprocess.PIPE, text=True)
    try:
        neighbour_pgid = _w1_wait_for_path(
            ready_a, content="integer")
        subject_pgid = _w1_wait_for_path(ready_b, content="integer")
        assert neighbour_pgid != subject_pgid, (
            "precondition: two live groups cannot share a pgid, and these two "
            "did -- the fixture is not building what this test is about")

        neighbour.stdin.write("grow\n")
        neighbour.stdin.flush()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline \
                and len(_w1_live_in_group(neighbour_pgid)) < 3:
            time.sleep(0.01)
        assert len(_w1_live_in_group(neighbour_pgid)) == 3, (
            "precondition: the neighbour group never reached three members")

        # The subject group dies. This is the moment every absence assertion in
        # this family is made at, with a neighbour alive beside it.
        _w1_kill_group(subject_pgid)
        subject.stdin.close()
        subject.wait(timeout=_w1_budget_s(
            "an_absence_claim_is_immune_to_a_live_neighbour_group/wait"))
        drain = time.monotonic() + 5.0
        while time.monotonic() < drain and _w1_live_in_group(subject_pgid):
            time.sleep(0.01)

        assert _w1_live_in_group(subject_pgid) == [], (
            "an absence claim saw a neighbour's pids")
        assert _w1_group_has_at_least(subject_pgid, 1) is False
        assert len(_w1_live_in_group(neighbour_pgid)) == 3, (
            "the neighbour died during the claim, so the empty answer above "
            "could have come from an empty host rather than from filtering")
    finally:
        _w1_kill_group(neighbour_pgid)
        neighbour.stdin.close()
        neighbour.wait(timeout=_w1_budget_s(
            "an_absence_claim_is_immune_to_a_live_neighbour_group/wait"))
        if subject.poll() is None:
            subject.kill()
            subject.wait(timeout=_w1_budget_s(
                "an_absence_claim_is_immune_to_a_live_neighbour_group/wait-2"))


def test_a_descriptor_wait_is_required_because_a_live_pid_proves_nothing(tmp_path):
    """RED CONTROL for _w1_readlink_when_installed, forced rather than sampled.

    A child is held LIVE at a point where it has NOT yet opened fd 3. At that
    exact instant the bare `os.readlink` the test used to call raises -- the CI
    failure, reproduced deterministically rather than waited for -- and the
    waiting form then returns the real link once the child installs it. The
    negative control proves the wait does not hang on a dead gate and names its
    own reason instead of timing out anonymously.
    """
    alive = tmp_path / "late-fd-alive"
    program = (
        "import os, sys\n"
        "open(%r, 'w').write('alive\\n')\n"
        "sys.stdin.readline()\n"
        "r, w = os.pipe()\n"
        "os.dup2(r, 3)\n"
        "sys.stdout.write('installed\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.readline()\n"
    ) % str(alive)
    child = subprocess.Popen(
        [sys.executable, "-c", program], text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        alive_payload = _w1_wait_for_path(alive, content="text")
        assert alive_payload == "alive\n"
        assert _w1_pid_is_live(child.pid), "precondition: the child must be live"
        assert not os.path.lexists("/proc/%d/fd/3" % child.pid), (
            "precondition: the child must NOT have installed fd 3 yet")

        # THE DEFECT, at the exact instant the old code could observe it.
        with pytest.raises(FileNotFoundError):
            os.readlink("/proc/%d/fd/3" % child.pid)

        child.stdin.write("go\n")
        child.stdin.flush()
        link = _w1_readlink_when_installed(child.pid, 3, timeout=_w1_budget_s("a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/readlink"))
        assert link.startswith("pipe:["), link
        assert child.stdout.readline() == "installed\n"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=_w1_budget_s("a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/wait"))

    # NEGATIVE CONTROL: a dead gate must fail fast with its distinctive reason,
    # not burn the whole timeout and not report a descriptor it never had.
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    dead.wait(timeout=_w1_budget_s("a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/wait-2"))
    started = time.monotonic()
    with pytest.raises(AssertionError, match="exited before installing fd"):
        _w1_readlink_when_installed(dead.pid, 3, timeout=_w1_budget_s("a_descriptor_wait_is_required_because_a_live_pid_proves_nothing/readlink-2"))
    assert time.monotonic() - started < 5.0, (
        "the wait burned its timeout on a process that was already gone")


def test_pytest_pid_publish_failure_is_settled_without_partial_target(tmp_path):
    """A failed rename cannot publish partial authority or continue the run."""
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3,
    )
    pytest_pid_path = rundir / "pytest.pid"
    bash_env = _w1_fail_shell_publish(tmp_path, pytest_pid_path)
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(
        tmp_path, code=0, stdout="stubhost-4242\n"))
    env["BASH_ENV"] = str(bash_env)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("pytest_pid_publish_failure_is_settled_without_partial_target/run"))

    evidence = (rundir / "jobid.err").read_text(encoding="utf-8")
    assert "REGISTER-GATE-SETUP-FAILED phase=pytest-pid-publish" in evidence
    assert "context=pytest-pid-publish" in evidence
    assert not pytest_pid_path.exists()
    assert not list(rundir.glob("pytest.pid.tmp.*"))
    assert not marker.exists()
    expected = {W1_SETUP_FAILURE_CODE, W1_RETAINED_FAILURE_CODE}
    assert (rundir / "exitcode").read_text().strip() in expected
    assert result.returncode in {int(code) for code in expected}


def test_malformed_owner_ready_reaps_its_term_resistant_group(tmp_path):
    """A post-setsid framing failure still owns, stops, waits, and censuses."""
    mod = _load()
    claim = tmp_path / "malformed-owner-claimed"
    descendant = tmp_path / "malformed-owner-descendant"
    marker = tmp_path / "workload-started"
    anchor = "os.setsid()\n"
    assert mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.count(anchor) == 1
    injection = (
        anchor
        + "claim = %r\n" % str(claim)
        + "try:\n"
        + "    claim_fd = os.open(claim, os.O_WRONLY | os.O_CREAT | "
          "os.O_EXCL, 0o600)\n"
        + "except FileExistsError:\n"
        + "    pass\n"
        + "else:\n"
        + "    os.close(claim_fd)\n"
        + "    import signal\n"
        + "    read_fd, write_fd = os.pipe()\n"
        + "    child = os.fork()\n"
        + "    if child == 0:\n"
        + "        os.close(read_fd)\n"
        + "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "        with open(%r, 'w', encoding='ascii') as stream:\n"
          % str(descendant)
        + "            stream.write(str(os.getpid()))\n"
        + "        os.write(write_fd, b'1')\n"
        + "        os.close(write_fd)\n"
        + "        while True:\n"
        + "            signal.pause()\n"
        + "    os.close(write_fd)\n"
        + "    if os.read(read_fd, 1) != b'1':\n"
        + "        raise SystemExit(94)\n"
        + "    os.close(read_fd)\n"
        + "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "    frame = ('OWNER-READY v1 pid=%d\\nEXTRA v1\\n' % "
          "os.getpid()).encode('ascii')\n"
        + "    os.write(1, frame)\n"
        + "    os.close(1)\n"
        + "    while True:\n"
        + "        signal.pause()\n"
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, ready_seconds=2, timeout_owner_program=timeout_owner,
    )
    registrar = tmp_path / "registrar-started"
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    env["W1_REGISTRAR_MARKER"] = str(registrar)

    result = subprocess.run(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_w1_budget_s("malformed_owner_ready_reaps_its_term_resistant_group/run"))

    assert claim.exists() and descendant.exists(), (
        "the malformed post-setsid owner schedule never reached its descendant")
    descendant_pid = int(descendant.read_text(encoding="ascii"))
    records = [record for record in _w1_owner_records(rundir)
               if record["role"] == "ready-reader"]
    assert len(records) == 1, records
    record = records[0]
    assert record["group_ready"] == "1"
    assert record["stop"] == "TERM-GRACE-KILL-GROUP"
    assert record["receipt"].split(":", 1)[0] == record["owner_pid"]
    assert int(record["grace_us"]) > 0
    assert int(record["term_at_us"]) < int(record["kill_at_us"])
    assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
    assert not _w1_pid_is_live(descendant_pid)
    assert not _w1_live_in_group(int(record["owner_pid"]))
    assert not registrar.exists() and not marker.exists()
    assert result.returncode in {92, 94}


def test_withheld_owner_ready_reaps_post_setsid_term_resistant_group(tmp_path):
    """READY framing is not the source of already-acquired group ownership."""
    mod = _load()
    parent_receipt_path = tmp_path / "no-ready-owner.receipt"
    child_receipt_path = tmp_path / "no-ready-child.receipt"
    marker = tmp_path / "workload-started"
    entered, release, entered_fd = _w1_fifo_barrier(tmp_path, "no-owner-ready")
    anchor = "os.setsid()\n"
    assert mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.count(anchor) == 1
    malicious = (
        "import pathlib, signal\n"
        + "def persist_receipt(path):\n"
        + "    raw = pathlib.Path('/proc/self/stat').read_text(encoding='ascii')\n"
        + "    tail = raw.rsplit(') ', 1)[1].split()\n"
        + "    value = '%d:%d:%d:%d:%s' % (os.getpid(), os.getppid(), "
          "os.getpgrp(), os.getsid(0), tail[19])\n"
        + "    pathlib.Path(path).write_text(value, encoding='ascii')\n"
        + "persist_receipt(%r)\n" % str(parent_receipt_path)
        + "read_fd, write_fd = os.pipe()\n"
        + "child = os.fork()\n"
        + "if child == 0:\n"
        + "    os.close(read_fd)\n"
        + "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "    persist_receipt(%r)\n" % str(child_receipt_path)
        + "    os.write(write_fd, b'1')\n"
        + "    os.close(write_fd)\n"
        + "    while True:\n"
        + "        signal.pause()\n"
        + "os.close(write_fd)\n"
        + "if os.read(read_fd, 1) != b'1':\n"
        + "    raise SystemExit(94)\n"
        + "os.close(read_fd)\n"
        + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        + "with open(%r, 'w', encoding='ascii') as stream:\n" % str(entered)
        + "    stream.write('owner-and-child-live\\n')\n"
        + "with open(%r, 'r', encoding='ascii') as stream:\n" % str(release)
        + "    stream.readline()\n"
        + "while True:\n"
        + "    signal.pause()\n"
    )
    # Only the initial READY reader owns this injected schedule.  The generic
    # descendant-census helper must still run the real bootstrap; otherwise
    # the fixture replaces the very observer whose result it is asserting and
    # can only produce UNKNOWN.
    injection = (
        anchor
        + "if stdout_path.endswith('/registration-ready-reader.out'):\n"
        + "".join("    " + line + "\n" for line in malicious.splitlines())
    )
    timeout_owner = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM.replace(
        anchor, injection, 1)
    script, rundir = _w1_build_runner(
        mod, tmp_path,
        "#!/bin/bash\ntouch %s\n" % shlex.quote(str(marker)),
        reap_seconds=3, ready_seconds=2, timeout_owner_program=timeout_owner,
    )
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-1\n"))
    proc = subprocess.Popen(
        ["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    parent_receipt = child_receipt = None

    def read_receipt(path):
        values = tuple(int(value) for value in path.read_text(
            encoding="ascii").split(":"))
        assert len(values) == 5
        return values

    try:
        assert _w1_await_fifo(entered_fd, site="withheld_owner_ready_reaps_post_setsid_term_resistant_group/fifo") == "owner-and-child-live\n"
        parent_receipt = read_receipt(parent_receipt_path)
        child_receipt = read_receipt(child_receipt_path)
        parent_pid, _parent_ppid, parent_pgid, parent_sid, _parent_start = (
            parent_receipt)
        child_pid, child_ppid, child_pgid, child_sid, _child_start = child_receipt
        assert parent_pid == parent_pgid == parent_sid
        assert child_ppid == parent_pid
        assert child_pgid == parent_pgid and child_sid == parent_sid
        assert _w1_pid_is_live(parent_pid) and _w1_pid_is_live(child_pid)
        with open(release, "w", encoding="ascii") as stream:
            stream.write("release\n")
        try:
            rc = proc.wait(timeout=_w1_budget_s("withheld_owner_ready_reaps_post_setsid_term_resistant_group/wait"))
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                "NO-READY-GROUP-AUTHORITY-DOWNGRADED-TO-BARE-PID") from exc
        records = [record for record in _w1_owner_records(rundir)
                   if record["role"] == "ready-reader"]
        assert len(records) == 1, records
        record = records[0]
        assert record["group_ready"] == "1", (
            "NO-READY-GROUP-AUTHORITY-DOWNGRADED-TO-BARE-PID", record)
        assert record["stop"] == "TERM-GRACE-KILL-GROUP", (
            "NO-READY-POST-SETSID-DESCENDANT-SURVIVED", record)
        assert record["wait_ok"] == "1" and record["descendants"] == "ABSENT"
        assert record["receipt"] == ":".join(map(str, parent_receipt))
        assert not _w1_pid_is_live(parent_pid)
        assert not _w1_pid_is_live(child_pid), (
            "NO-READY-POST-SETSID-DESCENDANT-SURVIVED")
        assert not marker.exists()
        assert rc in {92, 94}
    finally:
        os.close(entered_fd)
        if child_receipt is not None:
            child_pid, _ppid, parent_pgid, _sid, child_start = child_receipt
            try:
                current = _w1_proc_receipt(child_pid)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                current = None
            if current == (child_pid, parent_pgid, child_start):
                _w1_kill_group(parent_pgid)
        if proc.poll() is None:
            _w1_kill_group(os.getpgid(proc.pid))
            proc.kill()
            proc.wait(timeout=_w1_budget_s("withheld_owner_ready_reaps_post_setsid_term_resistant_group/wait-2"))


def test_a_registrar_that_succeeds_still_gets_waited_for_and_recorded(tmp_path):
    """OVER-SENSITIVITY CONTROL for W1, and it is not optional.

    "Registration failure is terminal" is one bad edit away from "the runner
    stops waiting", which would destroy every sample the hunt exists to take:
    no `exitcode`, no `epoch_end`, no pytest status, every row ABANDONED. So
    the same production template, the same real bash, one difference -- the
    stub registrar returns 0 -- must still reach `wait` and record the
    WORKLOAD's status, not the runner's.
    """
    mod = _load()
    marker = tmp_path / "workload-started"
    script, rundir = _w1_build_runner(
        mod, tmp_path, (
            "#!/bin/bash\n"
            "touch %s\n"
            "sleep 1\n"
            "exit %d\n" % (shlex.quote(str(marker)), W1_WORKLOAD_CODE)),
        owned_group_census_override="ABSENT")
    env = dict(os.environ)
    env["HOME"] = str(_w1_fake_home(tmp_path, code=0, stdout="stubhost-4242\n"))

    proc = subprocess.Popen(["bash", str(script)], env=env, text=True, cwd=_W1_SPAWN_CWD,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        rc = proc.wait(timeout=_w1_budget_s("a_registrar_that_succeeds_still_gets_waited_for_and_recorded/wait"))
    except subprocess.TimeoutExpired:
        _w1_kill_group(os.getpgid(proc.pid))
        proc.kill()
        proc.wait(timeout=_w1_budget_s("a_registrar_that_succeeds_still_gets_waited_for_and_recorded/wait-2"))
        raise AssertionError(
            "the runner never finished a SUCCESSFUL run within "
            f"{W1_RUNNER_BOUND:.0f}s")
    finally:
        try:
            _w1_kill_group(os.getpgid(proc.pid))
        except (ProcessLookupError, OSError):
            pass

    assert marker.exists(), "the workload never ran; the success path proves nothing"
    assert rc == 0, (
        f"a successful registration made the runner exit {rc}. The runner's "
        "own status must stay 0 on the normal path; the sample's status lives "
        "in $RUNDIR/exitcode.")
    assert (rundir / "jobid").read_text().strip() == "stubhost-4242", (
        "the registrar's job id was not recorded, so the monitor cannot name "
        "the job it launched")
    assert "REGISTER-FAILED" not in (rundir / "jobid").read_text(), (
        "a successful registration was recorded as a failure")
    assert (rundir / "exitcode").read_text().strip() == str(W1_WORKLOAD_CODE), (
        "the runner did not wait for its workload and record the workload's "
        f"own status ({W1_WORKLOAD_CODE}). Making registration failure terminal "
        "must not make the success path stop waiting -- that would empty the "
        "wedge denominator entirely.")
    assert (rundir / "epoch_end").is_file(), (
        "epoch_end is missing on the success path; the sample has no duration")


# ===========================================================================
# ROW 230: the budgets above are a CONTRACT, and these are the gates on it.
# ===========================================================================


#: Every file this module's tests live in. A split moves TESTS and leaves
#: the code that BINDS their names behind, so every population question
#: below has to be asked of the family and not of one file -- backlog row
#: 232 is an entire row about a census that judged its own file and called
#: it the population.
_W1_FAMILY_GLOB = "test_v3_66_1132_*.py"


def _w1_family_sources():
    """The parsed source of every file in this module's family.

    Both halves are proved nonzero here: a glob that matched only this
    file would make every population assertion downstream a statement
    about half the tests, and it would say nothing while doing it.
    """
    here = pathlib.Path(__file__).resolve()
    paths = sorted(here.parent.glob(_W1_FAMILY_GLOB))
    assert here in paths, (
        "the family glob %r does not match this file, so it cannot be the "
        "population" % _W1_FAMILY_GLOB)
    assert len(paths) >= 2, (
        "the family glob %r matched only %d file(s); the split left no "
        "sibling and every census below would be judging one file"
        % (_W1_FAMILY_GLOB, len(paths)))
    return [(path, ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))) for path in paths]


def test_every_name_this_family_uses_is_bound_in_the_file_that_uses_it():
    """THE GATE THE SPLIT ITSELF NEEDED, and it is here because it was.

    Moving a test moves the names it USES and leaves the names that BIND
    them behind. A missing import is a NameError at CALL time, so
    `--collect-only` is green, every `-k` subset that misses the test is
    green, and the first thing that reports it is a full run seven minutes
    long. That is exactly what happened when this split was first built:
    `select` and `stat` were used by two moved tests and imported only by
    the origin, and nothing before the whole-family run said so.

    A resolver, not a grep: every module-level free name loaded anywhere
    in a family file must be bound by that file -- by an import, a def, a
    top-level assignment, a function-local import, a parameter, or
    builtins.
    """
    files = _w1_family_sources()
    checked = 0
    offenders = {}
    for path, tree in files:
        bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            elif isinstance(node, ast.AnnAssign) \
                    and isinstance(node.target, ast.Name):
                bound.add(node.target.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
        for func in tree.body:
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            checked += 1
            local = set()
            for sub in ast.walk(func):
                if isinstance(sub, ast.arg):
                    local.add(sub.arg)
                elif isinstance(sub, ast.Name) \
                        and isinstance(sub.ctx, (ast.Store, ast.Del)):
                    local.add(sub.id)
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)):
                    local.add(sub.name)
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    local.add(sub.name)
                elif isinstance(sub, ast.Import):
                    for alias in sub.names:
                        local.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(sub, ast.ImportFrom):
                    for alias in sub.names:
                        local.add(alias.asname or alias.name)
                elif isinstance(sub, ast.comprehension):
                    for target in ast.walk(sub.target):
                        if isinstance(target, ast.Name):
                            local.add(target.id)
                elif isinstance(sub, ast.withitem) \
                        and sub.optional_vars is not None:
                    for target in ast.walk(sub.optional_vars):
                        if isinstance(target, ast.Name):
                            local.add(target.id)
            for sub in ast.walk(func):
                if isinstance(sub, ast.Name) \
                        and isinstance(sub.ctx, ast.Load) \
                        and sub.id not in bound and sub.id not in local:
                    offenders.setdefault(
                        (path.name, sub.id), []).append(func.name)
            for dec in func.decorator_list:
                for sub in ast.walk(dec):
                    if isinstance(sub, ast.Name) and sub.id not in bound:
                        offenders.setdefault(
                            (path.name, sub.id), []).append(func.name)
    assert checked > 100, (
        "only %d function(s) resolved across %d family file(s); this gate "
        "has lost its population" % (checked, len(files)))
    assert not offenders, (
        "name(s) used in a family file that nothing in that file binds -- a "
        "NameError waiting for a full run to find it: %r"
        % {k: v[:2] for k, v in sorted(offenders.items())})


def _w1_budget_census():
    """Every wall-clock budget declared anywhere in THIS file.

    An AST census, never a grep: backlog row 196 is an entire row about
    textual-proxy gates, and this file is full of prose that names the very
    identifiers it asserts on. Parsing sees keyword arguments and parameter
    defaults and nothing else.

    The denominator is WIDER than the v3.66.1222 ratchet's, deliberately. That
    gate sees CONSTANT budgets and says so; measuring this file found two real
    budgets it cannot see -- `watchdog=20.0`, whose name was outside the
    ratchet's set, and `proc.wait(timeout=W1_RUNNER_BOUND)`, whose value is a
    named constant rather than a literal. Both are counted here.
    """
    names = {"timeout", "timeout_s", "budget_s", "watchdog"}
    derived, other = [], []

    def classify(callee, lineno, arg, value):
        """derived / forwarded / not-a-budget, for one budget expression."""
        if isinstance(value, ast.Call) \
                and ast.unparse(value.func) == "_w1_budget_s":
            target = value.args[0]
            if isinstance(target, ast.Constant):
                derived.append((callee, target.value))
            # else: derived from a `site` PARAMETER, so the literal key lives
            # at the caller and is counted there. Not a straggler.
            return
        if isinstance(value, ast.Name) and value.id in (arg, "budget", "watchdog"):
            return                       # forwarded, or derived one line above
        other.append((lineno, callee, arg, ast.unparse(value)))

    for _path, tree in _w1_family_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for kw in node.keywords:
                    if kw.arg == "site" \
                            and isinstance(kw.value, ast.Constant):
                        derived.append((callee, kw.value.value))
                    elif kw.arg in names:
                        classify(callee, node.lineno, kw.arg, kw.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                pos = a.posonlyargs + a.args
                pairs = list(zip(pos[len(pos) - len(a.defaults):],
                                 a.defaults))
                pairs += [(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults)
                          if d is not None]
                for arg, d in pairs:
                    if arg.arg in names:
                        classify("DEFAULT:" + node.name, node.lineno,
                                 arg.arg, d)
    return derived, other


# The sites this file deliberately does NOT derive, recorded rather than
# filtered, because a classifier that is wrong once is worse than an honest
# list. `settle_ambiguous_launch(timeout_s=...)` is the PRODUCT's own deadline
# parameter under test with a monkeypatched transport -- 0.01s is chosen to
# force the deadline branch, so deriving it from a measurement would delete
# the test. Same class as the ratchet's "the product must clamp this" entries.
_W1_NOT_A_BUDGET = {
    ("mod.settle_ambiguous_launch", "timeout_s", "1.0"),
    ("mod.settle_ambiguous_launch", "timeout_s", "0.01"),
}


def test_every_wall_clock_budget_in_this_file_is_derived():
    """THE DENOMINATOR. A gate must see the subject it claims to judge.

    RED on the parent: at v3.66.1222 this file declared 127 constant budgets
    and derived none of them.
    """
    derived, other = _w1_budget_census()
    assert len(derived) > 100, (
        "only %d derived budget sites found; the census stopped seeing them "
        "and every assertion below would be vacuous" % len(derived))
    stragglers = [o for o in other
                  if (o[1], o[2], o[3]) not in _W1_NOT_A_BUDGET]
    assert not stragglers, (
        "wall-clock budget(s) in this file are still written down instead of "
        "derived from a measurement. Add the call site to _MEASURED_S with a "
        "measured baseline and call _w1_budget_s(...):\n  %r" % (stragglers,))
    assert len(other) == 3, (
        "the recorded not-a-budget population changed: %r" % (other,))


def test_the_table_and_the_call_sites_are_the_same_population():
    """No key without a site, no site without a key.

    An entry nothing uses is a number nobody re-measures; a site whose key is
    absent is a KeyError at run time in whichever branch happens to reach it,
    which is a failure with no diagnosis.
    """
    derived, _other = _w1_budget_census()
    used = {key for _callee, key in derived}
    assert used, "no call site resolves through _w1_budget_s"
    missing = sorted(used - set(_MEASURED_S))
    assert not missing, "call sites name keys absent from _MEASURED_S: %r" % missing
    unused = sorted(set(_MEASURED_S) - used)
    assert not unused, "_MEASURED_S carries keys no call site uses: %r" % unused


def test_no_budget_can_outlive_the_bound_that_governs_its_item():
    """THE CEILING, over the whole file rather than one budget at a time.

    `_w1_budget_s` asserts its own value at every hand-out; this proves the
    stronger claim the row asks for -- that an ITEM cannot reach the bound
    even if every budget it declares expires in turn. The per-function sum is
    a deliberate over-estimate: only one failure path can raise, so the real
    worst case is smaller.
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"),
                     filename=__file__)
    per_function = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        total = 0.0
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) \
                    and ast.unparse(inner.func) == "_w1_budget_s" \
                    and inner.args and isinstance(inner.args[0], ast.Constant):
                total += float(_w1_budget_s(inner.args[0].value))
        if total:
            per_function[node.name] = total
    assert len(per_function) > 40, (
        "only %d functions carry a derived budget; the walk collapsed"
        % len(per_function))
    ceiling = _GOVERNING_BOUND_S - _ITEM_RESERVE_S
    over = {f: t for f, t in per_function.items() if t > ceiling}
    assert not over, (
        "function(s) whose declared budgets could sum past the %.0fs ceiling "
        "(%.0fs bound less %.0fs reserve): %r"
        % (ceiling, _GOVERNING_BOUND_S, _ITEM_RESERVE_S, over))


def test_every_budget_clears_its_measured_cost_by_the_stated_margin():
    """THE FLOOR. The hazard this cut exists to remove.

    MEASURED at the parent commit: 39 of the 83 sites a passing run reaches
    carried a budget under 3x their own cost, and three sat at 1.0x --
    test_partial_or_duplicate_release_frame_never_execs waited 6.993s against
    a 7 second budget. A budget that close fires on CORRECT work under load,
    and at the assertion that is indistinguishable from the defect it exists
    to catch.
    """
    assert _MEASURED_S, "the table is empty, so this gate constrains nothing"
    tight = {}
    for site, (measured, _prior) in _MEASURED_S.items():
        if measured <= 0:
            continue
        ratio = float(_w1_budget_s(site)) / measured
        if ratio < _WARN_FACTOR:
            tight[site] = round(ratio, 2)
    assert not tight, (
        "budget(s) within %.1fx of their own measured cost, which is where "
        "the 2026-08-24 failures were: %r" % (_WARN_FACTOR, tight))
    # ... and the margin is not manufactured by an empty numerator.
    real = [s for s, (m, _p) in _MEASURED_S.items() if m >= 1.0]
    assert len(real) > 30, (
        "only %d sites carry a baseline of a second or more; the table has "
        "lost the expensive sites this row is about" % len(real))


def test_the_warning_fires_before_the_budget_on_every_expensive_site():
    """A control that can never fire first is a monument -- but a control that
    fires too early IS THE DEFECT THIS ROW IS ABOUT, so the two are balanced
    deliberately rather than by picking a comfortable number.

    `_WARN_FLOOR_S` equals `_MIN_BUDGET_S`, so on a site whose budget is the
    floor the warning cannot precede the timeout: the budget IS the control
    there, and no NEW failure mode is introduced for a wait that costs
    milliseconds. Where the warning must work is the expensive sites -- the
    6-7 second waits that fired on correct work on 2026-08-24 -- and there it
    is strictly earlier, by construction (3x vs 6x) and by assertion here.
    """
    expensive = [s for s, (m, _p) in _MEASURED_S.items() if m >= 5.0]
    assert len(expensive) >= 8, (
        "only %d sites carry a baseline of 5s or more, so this gate has lost "
        "the population row 230 is about" % len(expensive))
    late = {s: (_w1_warn_s(s), float(_w1_budget_s(s)))
            for s in expensive if _w1_warn_s(s) >= float(_w1_budget_s(s))}
    assert not late, (
        "expensive site(s) whose warning cannot precede their own timeout, so "
        "drift there would be announced by a crossed budget under load rather "
        "than by this assertion: %r" % late)


def test_the_boundary_instrument_is_actually_installed(tmp_path):
    """PRECONDITION for every claim _w1_police makes.

    Asked of the RESOURCE BOUNDARY rather than of the source: run a real
    subprocess through a real budget and assert the instrument saw it.
    """
    before = len(_W1_POLICED)
    proc = subprocess.Popen(["bash", "-c", "sleep 0.05"])
    assert proc.wait(timeout=_w1_budget_s("_w1_wait_for_gate/default")) == 0
    assert len(_W1_POLICED) == before + 1, (
        "the wait boundary is not instrumented, so every self-policing "
        "assertion in this module is decoration (%d -> %d)"
        % (before, len(_W1_POLICED)))
    assert _W1_POLICED[-1] == "_w1_wait_for_gate/default"


def test_a_site_that_outgrows_its_baseline_says_so(monkeypatch, tmp_path):
    """THE OVER-SENSITIVITY CONTROL, and it compares a baseline to the CLOCK.

    v3.66.1219's first attempt at this compared baselines only to each other,
    so restating 168s as 7s stayed green -- vacuous. This one runs a real
    subprocess that really takes about 0.3s and asserts the policing fires,
    BY ITS DISTINCTIVE MESSAGE, when the recorded baseline says it should
    have taken a millisecond. The negative arm restores the true baseline and
    asserts the same wait passes, so the RED cannot come from anything else.
    """
    site = "_w1_wait_for_gate/default"
    real = _MEASURED_S[site]

    def run_one():
        proc = subprocess.Popen(["bash", "-c", "sleep 0.3"])
        return proc.wait(timeout=_w1_budget_s(site))

    # THE FLOOR IS LOWERED FOR BOTH ARMS, so the RECORDED BASELINE is the only
    # thing that differs between them. `_WARN_FLOOR_S` exists to stop a
    # millisecond baseline turning scheduler noise into a failure, and at 5s it
    # would swallow a 0.3s subject whatever the baseline said -- which would
    # make this control prove nothing rather than prove the floor works.
    monkeypatch.setattr(sys.modules[__name__], "_WARN_FLOOR_S", 0.0)

    # NEGATIVE ARM FIRST, so a green positive arm cannot be an accident of a
    # broken fixture: with the real baseline the same wait is fine.
    assert _w1_warn_s(site) > 0.3, "the negative arm has no headroom to lose"
    assert run_one() == 0

    monkeypatch.setitem(_MEASURED_S, site, (0.001, real[1]))
    with pytest.raises(AssertionError) as caught:
        run_one()
    assert "against a recorded baseline of" in str(caught.value), (
        "the wait failed for some other reason, so this proves nothing: %s"
        % caught.value)
    assert site in str(caught.value)

    monkeypatch.undo()
    assert _MEASURED_S[site] == real
    assert run_one() == 0, (
        "the policing did not restore, so it would now fail unrelated tests")


def test_each_knob_is_constrained_independently_of_the_other():
    """THE TWO KNOBS MASK EACH OTHER, AND A MUTATION BATTERY IS HOW WE KNOW.

    At v3.66.1226 a battery ran five mutants against the gates in this file and
    FOUR ESCAPED. Two of them were the knobs themselves:

      * dropping the contention factor from six to one stayed GREEN, because
        the thirty-second floor rescued every ratio.
      * dropping the absolute floor to zero stayed GREEN, because the factor
        rescued every ratio instead.

    Neither knob was actually pinned by anything. The cause is structural: every
    other gate here reads `_w1_budget_s`, which returns `max(prior, floor,
    derived)` -- a COMBINED value in which either input can hide the loss of the
    other. Deriving the expectation from the artifact under test is the exact
    shape CLAUDE.md A7 forbids, and this file's whole subject is instruments that
    cannot see their own subject. This gate reads the two terms SEPARATELY.
    """
    assert _MEASURED_S, "the table is empty, so this gate constrains nothing"

    # THE MULTIPLICATIVE TERM ALONE must clear the margin, with no help from the
    # floor. That is what makes it a contention factor rather than decoration.
    assert _CONTENTION_FACTOR >= _WARN_FACTOR, (
        "the contention factor %.1f is below the %.1f margin the warning uses, "
        "so a site whose prior constant happens to be small would ship a budget "
        "this file already calls too tight"
        % (_CONTENTION_FACTOR, _WARN_FACTOR))
    thin = {
        site: (measured, math.ceil(measured * _CONTENTION_FACTOR))
        for site, (measured, _prior) in _MEASURED_S.items()
        if measured > 0
        and math.ceil(measured * _CONTENTION_FACTOR) < measured * _WARN_FACTOR
    }
    assert not thin, (
        "the DERIVED term alone fails the margin at %d site(s) -- the floor is "
        "carrying them, and a floor is not a contention model: %r"
        % (len(thin), sorted(thin)[:5]))

    # THE FLOOR ALONE must cover a measured SCHEDULING stall, with no help from
    # the factor. v3.66.1223's full suite measured
    # test_v3_66_1209 ... reexecs_itself_when_handed_ignored_stop_signals
    # consuming a 90s budget for work that costs 1.26-1.80s serially. No
    # multiplier expresses 88 seconds of delay on 1.5 seconds of work; that is
    # why an absolute floor exists, and why it is not free to move.
    assert _MIN_BUDGET_S >= 30.0, (
        "the absolute floor is %.1fs. It exists because a 1.5s test was measured "
        "consuming 90s of wall clock under load on 2026-08-25, which a "
        "multiplicative factor cannot describe. Lowering it re-opens the shape "
        "row 230 was filed for." % _MIN_BUDGET_S)


def test_the_handout_assertion_is_live_and_not_decoration():
    """NEGATIVE CONTROL for the per-budget ceiling.

    `_w1_budget_s` asserts every value it hands out is subordinate to the bound.
    Nothing proved that assertion could still FIRE: no site in the table is
    anywhere near the ceiling, so deleting the assert changed no observable
    behaviour and a mutation that removed it ESCAPED at v3.66.1226.

    An assertion that cannot be shown to fire is indistinguishable from a
    comment. This drives one through it.
    """
    site = "__handout_control__"
    over = _GOVERNING_BOUND_S  # any value above the ceiling will do
    _MEASURED_S[site] = (over, over)
    try:
        with pytest.raises(AssertionError) as excinfo:
            _w1_budget_s(site)
    finally:
        del _MEASURED_S[site]
    assert "subordinate" in str(excinfo.value), str(excinfo.value)

    # And the same call must SUCCEED for an ordinary site, so the raise above is
    # about the ceiling rather than about the helper being broken.
    ordinary = sorted(_MEASURED_S)[0]
    assert float(_w1_budget_s(ordinary)) <= _GOVERNING_BOUND_S - _ITEM_RESERVE_S


def test_the_governing_bound_agrees_with_the_frozen_one():
    """One declared source, not two numbers that drift apart.

    v3.66.1222 froze the bound in project-knowledge/BUDGET_RATCHET.json. This
    file states it locally so no test pays a JSON read per budget; the two
    must not disagree, and this is where that is proved rather than assumed.
    """
    ratchet = REPO / "project-knowledge" / "BUDGET_RATCHET.json"
    assert ratchet.is_file(), "the frozen bound is missing at %s" % ratchet
    frozen = json.loads(ratchet.read_text(encoding="utf-8"))["governing_bound_s"]
    assert float(frozen) == _GOVERNING_BOUND_S, (
        "this file derives its budgets from a %.0fs bound while the frozen "
        "population uses %.0fs" % (_GOVERNING_BOUND_S, float(frozen)))


def test_no_budget_in_this_file_is_at_or_above_the_bound():
    """The hazard v3.66.1222 fixed elsewhere, asserted here rather than assumed.

    MEASURED at the parent: zero of this file's 127 constant budgets were at
    or above 240, so the dead-`except` half of row 230 was already absent
    here. Deriving the budgets UPWARD is exactly the move that could
    reintroduce it, so it is checked at every hand-out and again in bulk.
    """
    ceiling = _GOVERNING_BOUND_S - _ITEM_RESERVE_S
    over = {s: float(_w1_budget_s(s)) for s in _MEASURED_S
            if float(_w1_budget_s(s)) >= _GOVERNING_BOUND_S}
    assert not over, "budget(s) at or above the governing bound: %r" % over
    near = {s: float(_w1_budget_s(s)) for s in _MEASURED_S
            if float(_w1_budget_s(s)) > ceiling}
    assert not near, "budget(s) past the reserve line %.0fs: %r" % (ceiling, near)


# ===========================================================================
# ROW 231 PART C -- THE RUNNER'S OWN DEADLINES ANSWER TO THE SAME RULE.
#
# v3.66.1226 made every wall-clock budget IN THIS FILE clear both hazards. The
# constants below are not test budgets: they are PRODUCT deadlines inside
# `bd-wedge-hunt` that this file's fixture drives. The rule is the same and the
# governing bound is different. A budget answers to pytest-timeout's 240s; a
# runner deadline answers to the runner's own lifecycle -- forward phases are
# capped at W1_TOTAL_SECONDS - W1_CLEANUP_RESERVE_SECONDS and settlement at
# W1_TOTAL_SECONDS, so the RESERVE is what governs a settlement value.
#
# WHAT THE MEASUREMENT SAID, and it is not what the row assumed. Two
# perturbation arms were run on this module at 2a2fc85 on test5 (161 nodes,
# ~700-process table, load 1.3-2.0):
#   * settlement 3s -> 10s: 161 pass, 407.76s -> 429.33s, and only FOUR nodes
#     move by more than half a second.
#   * every forward gate deadline +3s: 158 pass / 3 fail, 429.33s -> 444.43s,
#     and only FIVE nodes move by more than half a second.
# So the 6.7-8.3s cluster across 35 nodes is NOT a fixed elapsed wait on either
# deadline family: a deadline that always elapsed would have moved every one of
# them. Those two families together account for ~35s of a ~408s module. The
# remainder is the production runner's own startup and settle, which a
# test-only change cannot reach -- recorded here because row 231's acceptance
# asks for the attribution, and the honest attribution is a negative result.
# ===========================================================================

#: SIGINT delivered -> runner exited, for the shape this cut is about: a
#: registrar that never returns, so `registration_settle_cancel` is the path
#: that classifies the run. MEASURED directly, because a settlement that
#: SUCCEEDS is invisible inside a node's duration -- see the changelog for
#: host, load, n and spread.
_W1_SETTLEMENT_MEASURED_S = 2.4737
#: Named separately from `_CONTENTION_FACTOR` so that moving one cannot
#: silently move the other, and set to the same 6.0 for the same measured
#: reason: v3.66.1226 ran three concurrent copies of this module and recorded a
#: maximum per-site stretch of 4.13x, right-censored.
_W1_RUNNER_STRETCH_FACTOR = 6.0
#: Headroom between a settlement deadline and the reserve that caps it, so the
#: constant is not dead on arrival the way a budget at or above
#: pytest-timeout's bound is.
_W1_RUNNER_RESERVE_S = 5.0

#: ONE COMPLETE OWNER OBSERVATION, measured as the runner actually drives
#: it: coproc `python3 <timeout_owner>`, the OWNER-READY handshake read,
#: `timeout(1)`, the `python3 <probe> group` payload and the owner wait.
#: test5, 24 serial repetitions under twelve procfs-walking contention
#: generators, 1217-process table, load 8.65: median 0.3000s, max 0.3600s.
#: An idle-er sample (n=30, 1098 processes, load 2.05) gave 0.2900s median,
#: so contention barely moves the primitive itself -- what moved was the
#: REMAINDER it was handed, which is the whole point of the floor.
_W1_OBSERVATION_MEASURED_S = 0.3000
#: The remainder caught live in `ps` on this host while the split arm was
#: failing: `timeout --kill-after=0.100000 1.510000 python3 -c ...`. It is
#: an OBSERVED false-UNKNOWN, so the floor has to clear it as a matter of
#: fact and not only as a matter of arithmetic. Pinned separately from the
#: measurement above so collapsing either term cannot hide the other.
_W1_OBSERVATION_FALSE_UNKNOWN_REMAINDER_S = 1.51
#: Headroom between the observation floor and the window that governs it.
#: An observation may run its whole floor past the forward boundary, so
#: the reserve has to cover the floor AND the settlement deadline; this is
#: what is left over, and a cut that spends it has to move it here first.
_W1_OBSERVATION_RESERVE_HEADROOM_S = 2.0

_W1_RUNNER_DEADLINE_NAMES = (
    "W1_READY_SECONDS", "W1_GATE_SECONDS", "W1_CLEANUP_SECONDS",
    "W1_REGISTRAR_SECONDS", "W1_RECONCILE_SECONDS", "W1_TOTAL_SECONDS",
    "W1_CLEANUP_RESERVE_SECONDS", "W1_OWNER_OBSERVATION_SECONDS",
)

#: The runner deadlines this file's fixture may drive, and which one of them
#: bounds SETTLEMENT rather than a forward phase.
_W1_RUNNER_DEADLINE_KNOBS = (
    "reap_seconds", "ready_seconds", "registrar_seconds",
    "reconcile_seconds", "cleanup_seconds", "observation_seconds",
)
_W1_SETTLEMENT_KNOB = "cleanup_seconds"
_W1_OBSERVATION_KNOB = "observation_seconds"
_W1_FORWARD_KNOB = "reap_seconds"

#: The partial-frame barrier's real arrival under the acceptance load. The
#: first value is the prior loaded baseline; the second is row 325's 48/48-core
#: spike. The control's nine-second deadline is the integral ceiling above
#: both observations while remaining stricter than production's ten seconds.
_W1_PARTIAL_FRAME_LOADED_WAIT_S = (4.0875, 5.1862)

#: Every test allowed to shorten the shipped forward gate deadline, with the
#: reason its EXPIRY is part of that test's subject.  Ordinary lifecycle tests
#: use the production value: shortening their clock makes scheduler admission
#: decide their verdict before the behaviour they assert can run.
_W1_FORWARD_EXPIRY_IS_THE_SUBJECT = (
    ("test_partial_handoff_frame_does_not_restart_the_protocol_budget",
     "The partial frame is held open past the forward deadline so the test can "
     "prove that receiving more bytes did not restart that one deadline."),
    ("test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait",
     "The terminal-looking frame deliberately withholds EOF; expiry proves the "
     "runner never converts incomplete authority into an unbounded child wait."),
    ("test_handoff_timeout_retains_registered_id_under_one_budget",
     "The gate withholds its terminal status and the test asserts the exact "
     "registered failure produced when the one handoff deadline expires."),
    ("test_one_second_lifecycle_cap_remains_truthfully_unknown",
     "The one-second cap is the explicit subject and must expire before later "
     "authority can turn unavailable initial evidence into a definite result."),
)

#: Every test allowed to shorten the OWNER-OBSERVATION floor, with the
#: reason. MEASURED, not guessed: these are exactly the two nodes that
#: failed when the floor was added and nothing else changed, and both fail
#: for a reason that IS their subject rather than for a timing accident.
_W1_OBSERVATION_EXPIRY_IS_THE_SUBJECT = (
    ("test_one_second_lifecycle_cap_remains_truthfully_unknown",
     "The ONE SECOND cap is the subject, and the node asserts the process"
     " observer's record reads stop=SPAWN-FAILED -- that is the cap"
     " refusing to start an observation it cannot afford. An observation"
     " floor exempt from the cap would be a lifecycle the cap does not"
     " actually cap. MEASURED: with the shipped floor the observer starts"
     " and the record reads wait_ok=1."),
    ("test_term_resistant_observer_stays_inside_gate_budget",
     "The subject is an observation that ignores TERM being ended by its"
     " own bound, and the node asserts both the observer and its descendant"
     " are dead before the runner returns. A floor that did not bound that"
     " owner would leave those exact processes alive."),
)

#: Every test allowed to shorten the SETTLEMENT deadline, with the reason.
#: MEASURED, not guessed: these are exactly the nodes that moved when the
#: settlement deadline was separated from the forward one and left at its
#: shipped 10s. Static reading would have got this wrong in both directions --
#: several tests that ASSERT 92 did not move at all, because their retained
#: status comes from a settle path that finishes inside the deadline.
_W1_SETTLEMENT_EXPIRY_IS_THE_SUBJECT = (
    ("test_abort_timeout_retains_inert_gate_under_one_budget",
     "W1_GATE_WITHHOLD_ABORT holds the gate for 30s and the test's own name "
     "carries the claim: the runner classifies under ONE budget and leaves "
     "the inert gate alive. Settlement has to expire for that to be "
     "observable at all. MEASURED 5.90s -> 13.22s."),
    ("test_real_release_sigpipe_is_contained_and_enters_registered_failure",
     "The release peer is SIGKILLed mid-write, so the terminal descriptor "
     "outlives the gate and settlement can only end by deadline. Here the "
     "expiry is incidental to the subject rather than the subject itself, and "
     "the prior effective value is kept because the shipped one reaches the "
     "same verdict more slowly. MEASURED 8.22s -> 15.16s."),
    ("test_one_second_lifecycle_cap_remains_truthfully_unknown",
     "The ONE SECOND cap is the subject. A settlement phase exempt from it "
     "would be a lifecycle the cap does not actually cap. MEASURED 1.90s -> "
     "3.59s."),
    ("test_term_resistant_observer_stays_inside_gate_budget",
     "The observer ignores TERM and the test asserts its exact owner record "
     "has settled and both owned pids are absent before the runner returns, "
     "which requires the deadline to end the wait."),
    ("test_the_settlement_deadline_still_bounds_a_gate_that_will_not_settle",
     "The over-sensitivity control for this cut: it exists to prove the "
     "settlement deadline still fires, so its expiry IS its subject."),
)


def _w1_slow_observation_owner(mod, *, suffix, delay_s, marker):
    """A timeout owner that answers CORRECTLY but LATE, for ONE observation.

    The delay is placed after `os.setsid()` and before the OWNER-READY
    write, which is exactly the leg the spawning shell bounds with
    `read -r -t "$W1_SPAWN_TIMEOUT"`. So the injected number is the
    observation's cost as the runner measures it, with no clock of its own.
    """
    anchor = "os.setsid()\n"
    program = mod.REGISTRATION_TIMEOUT_OWNER_PROGRAM
    assert program.count(anchor) == 1, (
        "the timeout owner has no unique post-fork session boundary")
    injected = (
        anchor
        + "if stdout_path.endswith(%r):\n" % suffix
        + "    import time as _w1_time\n"
        + "    _w1_at = _w1_time.monotonic()\n"
        + "    _w1_time.sleep(%r)\n" % float(delay_s)
        + "    with open(%r, 'a', encoding='ascii') as _w1_stream:\n"
        % str(marker)
        + "        _w1_stream.write('%f\\n' % "
          "(_w1_time.monotonic() - _w1_at))\n"
    )
    return program.replace(anchor, injected, 1)


def _w1_runner_source_without_comments():
    """The shipped runner's CODE. Its prose is not part of any denominator.

    CLAUDE.md A7: a gate that scans source text has that text's comments and
    examples inside its denominator, and the comment block this cut adds to
    `bd-wedge-hunt` necessarily names the identifiers the gate asserts on.
    """
    lines = [line for line in HUNT.read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    text = "\n".join(lines)
    assert "registration_begin_cleanup_deadline" in text, (
        "the runner source was read but carries no settlement deadline at "
        "all, so this gate has lost its subject")
    return text


def _w1_runner_deadline_constants():
    """Each deadline constant the runner declares, parsed from the script."""
    text = _w1_runner_source_without_comments()
    found = {}
    for name in _W1_RUNNER_DEADLINE_NAMES:
        matches = re.findall(r"^%s=([0-9]+)$" % re.escape(name), text,
                             re.MULTILINE)
        assert len(matches) == 1, (
            "expected exactly one %s declaration in %s, found %d"
            % (name, HUNT, len(matches)))
        found[name] = int(matches[0])
    return found


def _w1_runner_deadline_sites(*, effective_only=False):
    """(enclosing test, knob, value) for every fixture-fed runner deadline.

    An AST census, never a grep: this file's prose names every one of these
    identifiers, and backlog row 196 is an entire row about textual-proxy
    gates.
    """
    sites = []
    for _path, tree in _w1_family_sources():
        tops = sorted(
            (node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
            key=lambda node: node.lineno)

        def owner(lineno, tops=tops):
            found = "<module>"
            for node in tops:
                if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                    found = node.name
            return found

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            forward_opt_in = keywords.get("forward_expiry_is_subject")
            forward_is_subject = (
                isinstance(forward_opt_in, ast.Constant)
                and forward_opt_in.value is True
            )
            for keyword in node.keywords:
                if keyword.arg not in _W1_RUNNER_DEADLINE_KNOBS:
                    continue
                if (effective_only and keyword.arg == _W1_FORWARD_KNOB
                        and not forward_is_subject):
                    # The builder clamps an inherited ordinary request to the
                    # shipped deadline. It is not a fixture-fed override.
                    continue
                if not isinstance(keyword.value, ast.Constant):
                    continue   # forwarded from a parameter; the caller has it
                sites.append((owner(node.lineno), keyword.arg,
                              keyword.value.value))
    assert sites, (
        "no fixture-fed runner deadline found at all, so every assertion that "
        "reads this census would be vacuous")
    return sites


def test_no_settlement_path_is_bounded_by_the_forward_deadline():
    """THE SPLIT ITSELF, asserted over the runner's complete population.

    RED on the parent: at 2a2fc85 all six settlement paths read
    `$W1_GATE_SECONDS`, the same constant the forward gate protocol uses, so a
    caller that legitimately shortened the forward deadline silently shortened
    settlement as well.

    BOTH HALVES ARE PROVED NONZERO. A gate that only checked "no settlement
    site reads the forward constant" would also pass on a runner that had no
    settlement sites left at all.
    """
    text = _w1_runner_source_without_comments()
    settlement = re.findall(
        r'registration_begin_cleanup_deadline "\$\{?(\w+)', text)
    forward = re.findall(
        r'registration_begin_gate_deadline "\$\{?[0-9:\-]*\$?\{?(\w+)', text)
    assert len(settlement) >= 6, (
        "only %d settlement-deadline sites found; the population this gate "
        "judges has gone missing" % len(settlement))
    assert forward, "no forward gate-deadline site found"
    assert "W1_GATE_SECONDS" not in settlement, (
        "a settlement path is bounded by the FORWARD deadline again, so a "
        "caller that shortens the gate protocol shortens settlement too and a "
        "DECIDED status can be downgraded to %s: %r"
        % (W1_RETAINED_FAILURE_CODE, settlement))
    assert "W1_GATE_SECONDS" in forward, (
        "the forward gate deadline is no longer used by any forward phase, so "
        "the assertion above proves nothing: %r" % (forward,))
    assert settlement.count("W1_CLEANUP_SECONDS") == 6, (
        "expected the six settlement paths to share one settlement deadline, "
        "found %r" % (settlement,))
    assert settlement.count("W1_RECONCILE_SECONDS") == 1, (
        "reconciliation had its own deadline before this cut and must keep "
        "it: %r" % (settlement,))


def test_the_shipped_settlement_deadline_clears_both_hazards():
    """THE TWO-SIDED RULE, applied to a PRODUCT deadline.

    CEILING. A settlement deadline at or above the cleanup reserve is capped
    by the lifecycle deadline the moment settlement begins at the forward
    boundary, so it can never be the thing that decides -- the same dead-code
    hazard v3.66.1219 and v3.66.1222 removed from test budgets.

    FLOOR. A settlement deadline below the measured cost of settling fires on
    correct work, and at the assertion that is indistinguishable from the
    defect it exists to catch. That is the hazard this cut actually hit.

    THE TWO TERMS ARE READ SEPARATELY AND NOT THROUGH ONE COMBINED VALUE.
    v3.66.1226's first battery lost two mutants to exactly that shape: every
    gate read `max(prior, floor, derived)`, so either input could hide the
    loss of the other.
    """
    constants = _w1_runner_deadline_constants()
    value = constants["W1_CLEANUP_SECONDS"]
    reserve = constants["W1_CLEANUP_RESERVE_SECONDS"]
    total = constants["W1_TOTAL_SECONDS"]
    assert 0 < reserve < total, (
        "the runner's cleanup reserve is not a proper part of its total "
        "lifecycle, so neither term below has a meaning: %r" % (constants,))
    assert value <= reserve - _W1_RUNNER_RESERVE_S, (
        "the settlement deadline %ds is not subordinate to the %ds cleanup "
        "reserve that caps it (reserve %ds). A deadline the lifecycle cap "
        "always beats cannot decide anything."
        % (value, reserve, _W1_RUNNER_RESERVE_S))
    assert _W1_SETTLEMENT_MEASURED_S > 0, (
        "the settlement cost is unmeasured, so the floor below is UNKNOWN "
        "rather than satisfied")
    assert value >= _W1_SETTLEMENT_MEASURED_S * _W1_RUNNER_STRETCH_FACTOR, (
        "the settlement deadline %ds does not clear its measured cost %.4fs by "
        "the stated %.1fx contention margin, so it can fire on a run that was "
        "going to succeed -- and settlement that runs out of time replaces a "
        "decided status with %s"
        % (value, _W1_SETTLEMENT_MEASURED_S, _W1_RUNNER_STRETCH_FACTOR,
           W1_RETAINED_FAILURE_CODE))
    # THE FORWARD DEADLINE IS NOT CONSTRAINED BY THE FLOOR ABOVE, and saying
    # so is part of the fix: its expiry is a legitimate subject, which is
    # exactly why it must not be the constant settlement reads.
    assert constants["W1_GATE_SECONDS"] <= total - reserve, (
        "the forward gate deadline outlives the forward window that caps it")

    # EACH KNOB IS PINNED ON ITS OWN. v3.66.1226's first battery lost two
    # mutants because every gate read a COMBINED value, so collapsing either
    # term left the other carrying the result. Both comparisons above take one
    # term each, and these two make each term unable to go to zero quietly.
    assert _W1_RUNNER_STRETCH_FACTOR >= _WARN_FACTOR, (
        "the runner contention factor %.1f is below the %.1f margin this file "
        "already calls the minimum credible stretch, so the floor above would "
        "accept a deadline equal to its own measured cost"
        % (_W1_RUNNER_STRETCH_FACTOR, _WARN_FACTOR))
    assert _W1_RUNNER_RESERVE_S >= reserve * 0.2, (
        "the ceiling keeps %.1fs of headroom under a %ds bound, which is less "
        "than a fifth of it -- a deadline that close to its cap is decided by "
        "the cap and not by itself" % (_W1_RUNNER_RESERVE_S, reserve))


def _w1_runner_owner_spawn_sites():
    """(kind, flags, role-expression) for every owner spawn in the runner.

    Line continuations are folded first, because every one of these call
    sites wraps. Comments are already gone: this cut's own prose in
    `bd-wedge-hunt` names `--observation` several times and A7 says that
    text is inside a text-scanning gate's denominator.
    """
    text = _w1_runner_source_without_comments()
    folded = re.sub(r"\\\n\s*", " ", text)
    sites = []
    for match in re.finditer(r"registration_owner_(run|spawn)\b([^\n]*)",
                             folded):
        kind, rest = match.group(1), match.group(2)
        if rest.startswith("()"):
            continue                      # the definition, not a call site
        tokens = rest.split()
        flags = []
        while tokens and tokens[0].startswith("--"):
            flags.append(tokens.pop(0))
        if not tokens:
            continue
        role = tokens[0]
        if "W1_RUN_SPAWN_FLAGS" in role:
            continue                      # the forwarding site inside run
        sites.append((kind, tuple(flags), role))
    assert sites, (
        "no owner spawn site found at all, so every assertion that reads "
        "this census would be vacuous")
    return sites


def _w1_site_is_an_observation(role):
    """A spawn whose payload asks a QUESTION rather than performing a WAIT."""
    return "observer" in role or "census" in role


def test_no_owner_observation_is_bounded_by_the_forward_deadline():
    """THE SPLIT ITSELF, asserted over the runner's complete spawn population.

    RED on the parent: at b489289 every owner spawn -- the fd observer, the
    process observer, the group observer and the descendant census that
    `registration_owner_collect` runs after EVERY owned helper -- took its
    `timeout` bound from `registration_remaining_owner_timeout`, which is
    what remains of the ACTIVE FORWARD deadline. Expiry on a forward wait
    is the subject; expiry on an observation is a false verdict.

    BOTH HALVES ARE PROVED NONZERO. A gate that only checked "no
    observation reads the forward remainder" would also pass on a runner
    with no observations left, and one that only checked "the observation
    timeout exists" would pass on a runner where nothing called it.
    """
    sites = _w1_runner_owner_spawn_sites()
    observations = [site for site in sites
                    if _w1_site_is_an_observation(site[2])]
    waits = [site for site in sites
             if not _w1_site_is_an_observation(site[2])]
    assert len(observations) >= 4, (
        "the observation population this gate judges has gone missing: %r"
        % (sites,))
    assert len(waits) >= 2, (
        "the forward-wait population this gate contrasts against has gone "
        "missing: %r" % (sites,))
    undeclared = [site for site in observations
                  if "--observation" not in site[1]]
    assert not undeclared, (
        "an owner OBSERVATION is bounded by what remains of the FORWARD "
        "deadline again, so a caller that shortens the gate protocol also "
        "tells a helper how long it may take to ANSWER, and the helper's "
        "expiry becomes UNKNOWN on a run that was going to succeed: %r"
        % (undeclared,))
    overreach = [site for site in waits if "--observation" in site[1]]
    assert not overreach, (
        "a forward WAIT was given the observation floor, so a deadline "
        "whose expiry is some test's subject can now outlive it: %r"
        % (overreach,))
    text = _w1_runner_source_without_comments()
    assert text.count("registration_remaining_observation_timeout") == 2, (
        "the observation timeout is not declared exactly once and called "
        "exactly once, so the flags asserted above may reach nothing")
    assert "registration_remaining_owner_timeout" in text, (
        "the forward owner timeout is gone entirely, so the contrast this "
        "gate draws proves nothing")


def test_the_owner_spawn_still_describes_two_costs_with_two_keys():
    """THE OVERCORRECTION GATE, and it exists because the mutant ESCAPED.

    MEASURED, not argued: applying the observation floor to EVERY owner spawn
    -- including the `*-deadline` timers whose expiry IS the deadline
    mechanism, and the `*-reader` waits whose expiry is several tests'
    subject -- leaves this whole family GREEN. 180 passed in 246.40s at
    `-n 12 --dist loadfile` with that mutant applied. The timers' expiry moves
    by about the floor, and every wall-clock assertion in the family has more
    slack than that, so there is no behavioural catcher to find. This gate is
    therefore structural on purpose, and it asks the SEAM rather than the file:
    `registration_owner_spawn` must still route a declared observation and an
    undeclared wait through DIFFERENT timeouts.

    A7: the function body is parsed out of the FORMATTED runner, so the
    comment block that necessarily names both identifiers is not in the
    denominator.
    """
    mod = _load()
    body = _w1_runner_function_source(mod, "registration_owner_spawn")
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))
    assert code.count("W1_SPAWN_OBSERVATION") >= 2, (
        "the spawn no longer reads its own observation declaration, so the "
        "flag the call sites pass reaches nothing: %r" % (code,))
    assert code.count("registration_remaining_observation_timeout") == 1, (
        "the observation floor is not reached from the spawn exactly once, "
        "so a declared observation is bounded by something else: %r" % (code,))
    assert code.count("registration_remaining_owner_timeout") == 1, (
        "registration_owner_spawn no longer bounds an UNDECLARED spawn by the "
        "forward remainder, so the *-deadline timers whose expiry IS the "
        "deadline mechanism, and the *-reader waits whose expiry is several "
        "tests' subject, now outlive the deadline they implement. One key "
        "describing two costs is the defect this cut removed, and applying "
        "the floor everywhere is the same defect facing the other way: %r"
        % (code,))


def test_the_shipped_observation_floor_clears_both_hazards():
    """THE TWO-SIDED RULE, applied to the owner-observation floor.

    CEILING. An observation may run its whole floor PAST the forward
    boundary, so the cleanup reserve has to cover the floor and the
    settlement deadline together. A floor that does not leave settlement
    its declared budget buys one false UNKNOWN by creating another.

    FLOOR. A bound below the measured cost of observing fires on correct
    work, and at the assertion that is indistinguishable from the defect
    the observation exists to report.

    EACH TERM IS READ ON ITS OWN. v3.66.1226's first battery lost two
    mutants to gates that read one combined `max(...)`, so either input
    could hide the loss of the other.
    """
    constants = _w1_runner_deadline_constants()
    value = constants["W1_OWNER_OBSERVATION_SECONDS"]
    reserve = constants["W1_CLEANUP_RESERVE_SECONDS"]
    cleanup = constants["W1_CLEANUP_SECONDS"]
    total = constants["W1_TOTAL_SECONDS"]
    assert 0 < cleanup < reserve < total, (
        "the runner's settlement deadline and cleanup reserve are not a "
        "proper part of its lifecycle, so neither term below has a "
        "meaning: %r" % (constants,))
    assert value + cleanup <= reserve, (
        "an observation may overrun the forward boundary by its whole %ds "
        "floor, and %ds of floor plus the %ds settlement deadline does not "
        "fit in the %ds cleanup reserve -- so honouring the floor would "
        "cut settlement short and trade one false %s for another"
        % (value, value, cleanup, reserve, W1_RETAINED_FAILURE_CODE))
    assert reserve - cleanup - value >= _W1_OBSERVATION_RESERVE_HEADROOM_S, (
        "the floor leaves %.1fs of the cleanup reserve unspent, less than "
        "the %.1fs this file records as its headroom; a constant that "
        "close to its cap is decided by the cap"
        % (reserve - cleanup - value, _W1_OBSERVATION_RESERVE_HEADROOM_S))
    assert _W1_OBSERVATION_MEASURED_S > 0, (
        "the observation cost is unmeasured, so the floor below is UNKNOWN "
        "rather than satisfied")
    assert value >= _W1_OBSERVATION_MEASURED_S * _W1_RUNNER_STRETCH_FACTOR, (
        "the observation floor %ds does not clear its measured cost %.4fs "
        "by the stated %.1fx contention margin, so it can expire on a "
        "helper that was going to answer -- and an observation that "
        "expires is UNKNOWN, which settles a correct run at %s"
        % (value, _W1_OBSERVATION_MEASURED_S, _W1_RUNNER_STRETCH_FACTOR,
           W1_RETAINED_FAILURE_CODE))
    assert value > _W1_OBSERVATION_FALSE_UNKNOWN_REMAINDER_S, (
        "the observation floor %ds does not clear the %.2fs remainder that "
        "was OBSERVED producing a false UNKNOWN on this host, so the "
        "arithmetic above is satisfied by a value the facts refute"
        % (value, _W1_OBSERVATION_FALSE_UNKNOWN_REMAINDER_S))
    assert _W1_RUNNER_STRETCH_FACTOR >= _WARN_FACTOR, (
        "the runner contention factor %.1f is below the %.1f margin this "
        "file already calls the minimum credible stretch"
        % (_W1_RUNNER_STRETCH_FACTOR, _WARN_FACTOR))
    assert _W1_OBSERVATION_RESERVE_HEADROOM_S > 0, (
        "the headroom term went to zero, so the ceiling above accepts a "
        "floor that exactly exhausts the reserve")


def test_only_a_declared_subject_shortens_the_observation_floor():
    """A test may shorten the observation floor ONLY where that is its point.

    The census is over the tree, the declared set is written down here, and
    both halves are asserted nonzero. Before v3.66.1241 no site named the
    floor at all because there was none, and every `reap_seconds` site set
    it by accident -- which is the defect, not the fix.
    """
    sites = _w1_runner_deadline_sites()
    others = [site for site in sites if site[1] != _W1_OBSERVATION_KNOB]
    assert len(others) > 20, (
        "the deadline population this gate contrasts against has gone "
        "missing: %d site(s)" % len(others))
    declared = {name for name, _why in _W1_OBSERVATION_EXPIRY_IS_THE_SUBJECT}
    assert len(declared) == len(_W1_OBSERVATION_EXPIRY_IS_THE_SUBJECT), (
        "the declared observation set names a test twice")
    shortened = {test for test, knob, _value in sites
                 if knob == _W1_OBSERVATION_KNOB}
    assert shortened, (
        "no test drives the observation floor at all, so the knob is dead "
        "and the controls proving the floor still bounds an observation "
        "have gone with it")
    assert shortened == declared, (
        "the observation floor is shortened by test(s) that do not declare "
        "why (%r), or declared by entries no site uses (%r)"
        % (sorted(shortened - declared), sorted(declared - shortened)))
    for _name, why in _W1_OBSERVATION_EXPIRY_IS_THE_SUBJECT:
        assert len(why) > 40, (
            "a declared observation site carries no real reason: %r" % why)
    shipped = _w1_runner_deadline_constants()["W1_OWNER_OBSERVATION_SECONDS"]
    over = [(test, value) for test, knob, value in sites
            if knob == _W1_OBSERVATION_KNOB and value >= shipped]
    assert not over, (
        "a declared observation site is not actually shorter than the "
        "shipped floor, so it declares an intent it does not have: %r"
        % (over,))


def test_only_a_declared_subject_shortens_the_forward_deadline():
    """A scheduler-sensitive fixture never replaces the shipped gate clock.

    The split made these two files concurrent with their band neighbours, but
    55 builder sites still replaced the production ten-second forward deadline
    with one to eight seconds.  Expiry was the subject at only the four sites
    declared above.  Everywhere else the short clock could decide first and
    produce an unrelated missing marker, partial protocol, retained-uncertainty
    status, or an unreachable FIFO timeout.  This census constrains the shared
    cause rather than naming whichever test lost the schedule on one run.
    """
    sites = _w1_runner_deadline_sites(effective_only=True)
    forward = [(test, value) for test, knob, value in sites
               if knob == _W1_FORWARD_KNOB]
    others = [site for site in sites if site[1] != _W1_FORWARD_KNOB]
    assert len(forward) >= len(_W1_FORWARD_EXPIRY_IS_THE_SUBJECT), (
        "the shortened-forward population this gate judges has gone missing: "
        "%r" % (forward,))
    assert len(others) > 20, (
        "the other deadline population this gate contrasts against has gone "
        "missing: %d site(s)" % len(others))
    declared = {name for name, _why in _W1_FORWARD_EXPIRY_IS_THE_SUBJECT}
    assert len(declared) == len(_W1_FORWARD_EXPIRY_IS_THE_SUBJECT), (
        "the declared forward-expiry set names a test twice")
    shortened = {test for test, _value in forward}
    assert shortened == declared, (
        "the forward deadline is shortened by test/helper sites whose subject "
        "is not its expiry (%r), or declared by entries no site uses (%r)"
        % (sorted(shortened - declared), sorted(declared - shortened)))
    for _name, why in _W1_FORWARD_EXPIRY_IS_THE_SUBJECT:
        assert len(why) > 40, (
            "a declared forward-expiry site carries no real reason: %r" % why)
    shipped = _w1_runner_deadline_constants()["W1_GATE_SECONDS"]
    over = [(test, value) for test, value in forward if value >= shipped]
    assert not over, (
        "a declared forward-expiry site is not actually shorter than the "
        "shipped deadline, so it declares an intent it does not have: %r"
        % (over,))
    partial = dict(forward)[
        "test_partial_handoff_frame_does_not_restart_the_protocol_budget"]
    assert partial == shipped - 1, (
        "the partial-frame control is no longer the largest integral bound "
        "that remains stricter than production: %r" % partial)
    assert partial >= math.ceil(
        max(_W1_PARTIAL_FRAME_LOADED_WAIT_S) * 1.5), (
        "the partial-frame expiry no longer carries 50%% headroom over its "
        "largest loaded wait: deadline=%r samples=%r"
        % (partial, _W1_PARTIAL_FRAME_LOADED_WAIT_S))


def test_an_ordinary_short_forward_request_uses_the_shipped_deadline(tmp_path):
    """The central clamp is behavioural, and its expiry opt-in remains live."""
    mod = _load()
    shipped = _w1_runner_deadline_constants()["W1_GATE_SECONDS"]
    assert shipped > 1, (
        "the negative arm is not shorter than the production deadline")

    ordinary_root = tmp_path / "ordinary"
    ordinary_root.mkdir()
    ordinary, _ = _w1_build_runner(
        mod, ordinary_root, "#!/bin/bash\nexit 0\n", reap_seconds=1)
    ordinary_text = ordinary.read_text(encoding="utf-8")
    assert ordinary_text.count("W1_GATE_SECONDS=%d" % shipped) == 1, (
        "an ordinary fixture request shortened the production forward clock")
    assert "W1_GATE_SECONDS=1\n" not in ordinary_text

    expiry_root = tmp_path / "expiry-control"
    expiry_root.mkdir()
    explicit_expiry_control = True
    expiry, _ = _w1_build_runner(
        mod, expiry_root, "#!/bin/bash\nexit 0\n", reap_seconds=1,
        forward_expiry_is_subject=explicit_expiry_control)
    expiry_text = expiry.read_text(encoding="utf-8")
    assert expiry_text.count("W1_GATE_SECONDS=1\n") == 1, (
        "the explicit expiry control did not receive its short deadline, so "
        "the population declaration could be decoration")
    assert "W1_GATE_SECONDS=%d\n" % shipped not in expiry_text


def test_only_a_declared_subject_shortens_the_settlement_deadline():
    """A test may drive settlement small ONLY where that expiry is its point.

    THE DEFECT THIS PREVENTS RECURRING is not "a small number": it is a small
    number arrived at BY ACCIDENT. Before this cut no site named the
    settlement deadline at all and every `reap_seconds` site set it anyway.
    The census is over the tree, the declared set is written down here, and
    both halves are asserted nonzero.
    """
    sites = _w1_runner_deadline_sites()
    forward = [site for site in sites if site[1] != _W1_SETTLEMENT_KNOB]
    assert len(forward) > 20, (
        "the forward-deadline population this gate contrasts against has gone "
        "missing: %d site(s)" % len(forward))
    declared = {name for name, _why in _W1_SETTLEMENT_EXPIRY_IS_THE_SUBJECT}
    assert len(declared) == len(_W1_SETTLEMENT_EXPIRY_IS_THE_SUBJECT), (
        "the declared settlement set names a test twice")
    shortened = {test for test, knob, _value in sites
                 if knob == _W1_SETTLEMENT_KNOB}
    assert shortened, (
        "no test drives the settlement deadline at all, so the knob is dead "
        "and the control proving the deadline still fires has gone with it")
    assert shortened == declared, (
        "settlement is shortened by test(s) that do not declare why (%r), or "
        "declared by entries no site uses (%r)"
        % (sorted(shortened - declared), sorted(declared - shortened)))
    for _name, why in _W1_SETTLEMENT_EXPIRY_IS_THE_SUBJECT:
        assert len(why) > 40, (
            "a declared settlement site carries no real reason: %r" % why)
    shipped = _w1_runner_deadline_constants()["W1_CLEANUP_SECONDS"]
    over = [(test, value) for test, knob, value in sites
            if knob == _W1_SETTLEMENT_KNOB and value >= shipped]
    assert not over, (
        "a declared settlement site is not actually shorter than the shipped "
        "deadline, so it declares an intent it does not have: %r" % (over,))
