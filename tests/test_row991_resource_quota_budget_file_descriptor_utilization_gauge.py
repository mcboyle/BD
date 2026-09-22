"""Row 991 -- Resource Quota Budget & File Descriptor Utilization Gauge.

THE ROW'S REASON (measured on base 14895ffd, not assumed): the process fingerprint
`bulk_downloader.dev_suite.introspection.process_info()` reports `open_fds` as a RAW COUNT with
no budget beside it. A raw count is not actionable -- 900 open descriptors is healthy under a
1,048,576 soft limit and fatal under 1,024 -- so nothing in the running process can say how much
of the descriptor quota is spent or how much headroom is left. `tools/stress_probe.py` has the
same shape (`_PROC.num_fds()`), and a repo-wide probe for a quota/utilization gauge finds NONE:

    grep -rniE "resource_quota|quota_budget|fd_utilization|RLIMIT_NOFILE" --include=*.py \
        bulk_downloader/ tools/          ->  0 hits on base
    positive control, SAME probe shape:  "turnstile" -> 294 hits, "slow_query" -> 46 hits

So the probe can say yes; on this capability it says NONE.

The first test below is deliberately importable ON BASE: it fails with an AssertionError naming
the missing budget, not with an ImportError about a module this row has not written yet. The
gauge's own unit tests import the new module lazily so that the row-reason failure is the
headline RED and each other failure still names the capability it wants.
"""
import importlib

import bulk_downloader.dev_suite as ds

BD_GATE_SCOPE = "repo-wide"

_BUDGET_KEYS = ("fd_soft_limit", "fd_hard_limit", "fd_utilization_pct", "fd_quota_status")


def _gauge():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module("bulk_downloader.fd_quota_gauge")


# ── 1. the row's reason, asserted on a surface that exists on base ────────────────

def test_process_info_reports_the_fd_budget_and_not_only_a_raw_count():
    info = ds.process_info()
    # positive control inside the test: the surface really is reachable and really does count
    # descriptors, so a missing budget key below is a missing BUDGET and not a dead probe.
    assert info["pid"] > 0
    assert info["open_fds"] is None or info["open_fds"] >= 3
    missing = [k for k in _BUDGET_KEYS if k not in info]
    assert not missing, (
        "process_info() reports a raw descriptor count with no quota budget beside it: "
        f"missing {missing}. open_fds={info.get('open_fds')} cannot be read as healthy or "
        "near-exhaustion without the soft limit and the utilization it implies.")


def test_process_info_budget_agrees_with_the_gauge_and_keeps_its_old_shape():
    info = ds.process_info()
    snap = _gauge().fd_quota_snapshot()
    assert info["fd_soft_limit"] == snap["soft_limit"]
    assert info["fd_quota_status"] == snap["status"]
    # CALLER CENSUS (DONE.md): the /api/dev/process route and tests/test_dev_suite.py read these.
    for old in ("pid", "ppid", "cwd", "uptime_seconds", "thread_count", "open_fds", "rss_mb"):
        assert old in info, f"process_info() dropped a key an existing caller reads: {old}"


# ── 2. the gauge itself: the arithmetic a raw count cannot do ─────────────────────

def test_snapshot_reports_utilization_headroom_and_a_status_against_the_real_rlimit():
    import resource
    snap = _gauge().fd_quota_snapshot()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    assert snap["soft_limit"] == soft and snap["hard_limit"] == hard
    assert snap["open_fds"] >= 3                      # stdin/stdout/stderr at minimum
    assert snap["headroom"] == soft - snap["open_fds"]
    assert 0.0 <= snap["utilization_pct"] <= 100.0
    assert snap["status"] in ("ok", "warn", "critical")


def test_utilization_is_computed_not_guessed():
    g = _gauge()
    assert g.utilization_pct(open_fds=256, soft_limit=1024) == 25.0
    assert g.utilization_pct(open_fds=1024, soft_limit=1024) == 100.0
    assert g.utilization_pct(open_fds=0, soft_limit=1024) == 0.0


def test_status_escalates_at_the_documented_thresholds():
    g = _gauge()
    assert g.quota_status(74.9) == "ok"
    assert g.quota_status(g.WARN_PCT) == "warn"
    assert g.quota_status(89.9) == "warn"
    assert g.quota_status(g.CRITICAL_PCT) == "critical"
    assert g.quota_status(100.0) == "critical"
    assert g.WARN_PCT < g.CRITICAL_PCT


def test_an_unlimited_soft_limit_is_not_a_division_by_zero_and_not_a_false_critical():
    import resource
    g = _gauge()
    assert g.utilization_pct(open_fds=512, soft_limit=resource.RLIM_INFINITY) == 0.0
    assert g.utilization_pct(open_fds=512, soft_limit=0) == 0.0
    assert g.quota_status(g.utilization_pct(open_fds=512, soft_limit=resource.RLIM_INFINITY)) == "ok"
    snap = g.fd_quota_snapshot(soft_limit=resource.RLIM_INFINITY, hard_limit=resource.RLIM_INFINITY)
    assert snap["unlimited"] is True and snap["headroom"] is None and snap["status"] == "ok"


def test_the_gauge_never_raises_when_proc_is_unreadable_it_reports_unknown():
    """NEGATIVE CONTROL for the counter: a gauge that cannot count must say so, not report 0 --
    0 open descriptors would read as perfect health and is never true of a live process."""
    g = _gauge()
    snap = g.fd_quota_snapshot(open_fds=None)
    assert snap["open_fds"] is None
    assert snap["utilization_pct"] is None
    assert snap["headroom"] is None
    assert snap["status"] == "unknown"


def test_counting_is_measured_against_a_descriptor_this_test_opens():
    """The count is a real measurement: open a file and the gauge must see one more."""
    g = _gauge()
    before = g.count_open_fds()
    with open(__file__, "rb") as fh:
        during = g.count_open_fds()
        assert fh.fileno() >= 0
    after = g.count_open_fds()
    assert during == before + 1, (before, during, after)
    assert after == before


# -- 4. the check must not fail open on its OWN failure path ----------------------
#
# Correctness lens bd-review-correctness-B8-B, 2026-09-22T09:28Z, finding A (HIGH): the first
# cut of this row caught a getrlimit failure into (None, None), and is_unlimited(None) was True,
# so an unread budget came back as "unlimited, 0.0% used, ok" -- the gauge reported health about
# a number it had never obtained. Mutants M1 (count_open_fds returns 0 on failure) and M7 (the
# getrlimit-failure branch yields (1, 1)) both survived the suite for the same reason: nothing
# here ever made a MEASUREMENT fail; the old unknown test passed open_fds=None as an argument.
# These tests break the measurement itself.


def test_an_unread_limit_is_unknown_and_unknown_is_not_unlimited():
    g = _gauge()
    assert g.is_unlimited(None) is False, (
        "a limit that was never read was reported as 'no limit at all', which is how the gauge "
        "came to answer 'ok' about a budget it had not obtained")
    assert g.limit_is_unknown(None) is True
    assert g.utilization_pct(open_fds=512, soft_limit=None) is None
    assert g.headroom(open_fds=512, soft_limit=None) is None
    assert g.quota_status(g.utilization_pct(open_fds=512, soft_limit=None)) == "unknown"


def test_a_getrlimit_that_raises_produces_unknown_not_health(monkeypatch):
    """M7. The failure is forced in the kernel call, not passed in as an argument."""
    g = _gauge()
    import resource as _resource

    def _boom(*_a, **_k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(_resource, "getrlimit", _boom)
    snap = g.fd_quota_snapshot(open_fds=512)
    assert snap["soft_limit"] is None and snap["hard_limit"] is None
    assert snap["unlimited"] is False, "an unreadable limit is not an absent limit"
    assert snap["limit_known"] is False
    assert snap["utilization_pct"] is None
    assert snap["headroom"] is None
    assert snap["status"] == "unknown", (
        f"the gauge reported {snap['status']!r} for a budget it could not read")
    # And the gauge still does not raise: the status route it feeds must stay up.


def test_a_counter_that_raises_produces_unknown_not_zero(monkeypatch):
    """M1. Forces os.listdir to fail, which is the only thing that exercises the except branch."""
    g = _gauge()
    import os as _os

    real_listdir = _os.listdir

    def _boom(path, *a, **k):
        if str(path).startswith("/proc/self/fd"):
            raise PermissionError(13, "Permission denied")
        return real_listdir(path, *a, **k)

    monkeypatch.setattr(_os, "listdir", _boom)
    assert g.count_open_fds() is None, (
        "an uncountable /proc reported a number; 0 open descriptors reads as perfect health and "
        "is impossible for a live process")
    snap = g.fd_quota_snapshot()
    assert snap["open_fds"] is None and snap["status"] == "unknown"


def test_process_info_reports_unknown_when_the_budget_cannot_be_read(monkeypatch):
    """Through the REAL caller -- the /api/dev fingerprint -- which is where the lens measured
    'fd_soft_limit=None fd_utilization_pct=0.0 fd_quota_status=ok'."""
    from bulk_downloader.dev_suite import introspection
    import resource as _resource

    def _boom(*_a, **_k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(_resource, "getrlimit", _boom)
    info = introspection.process_info()
    assert info["fd_soft_limit"] is None
    assert info["fd_utilization_pct"] is None, (
        f"process_info() published {info['fd_utilization_pct']!r}% utilization of a quota it "
        f"never read")
    assert info["fd_quota_status"] == "unknown"


def test_positive_control_the_same_probes_still_report_real_states():
    """Rule 7 beside the four negatives above: the gauge is not simply stuck on 'unknown'."""
    g = _gauge()
    assert g.quota_status(g.utilization_pct(open_fds=999, soft_limit=1000)) == "critical"
    assert g.quota_status(g.utilization_pct(open_fds=10, soft_limit=1000)) == "ok"
    snap = g.fd_quota_snapshot(open_fds=750, soft_limit=1000, hard_limit=2000)
    assert (snap["status"], snap["utilization_pct"], snap["headroom"]) == ("warn", 75.0, 250)
    assert snap["limit_known"] is True and snap["unlimited"] is False
