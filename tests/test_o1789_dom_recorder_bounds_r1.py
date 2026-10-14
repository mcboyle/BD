"""dom-recorder-bounds r1 (O1789 page-bridge F6 F7 F8).

F6: drain_dom_events had no Python-side bound; the in-page 200k cap is the
    page's to break, so forged events grew dom_log (and RSS) without limit.
    Now: per-drain, per-capture event and per-capture byte bounds, the excess
    counted as dropped.
F7: a page-chosen __bd_dom_dropped ("x", Infinity, NaN) raised after
    _DRAIN_JS had already cleared the page buffer (whole batch lost), and a
    negative one drove the total below zero. Now coerced to 0, batch kept.
F8: the capture runs as a subprocess, so get_status in the Flask process saw
    only its own zero counters. Now the capture side writes a state file under
    BD_HOME and get_status / the /api/data/dom_recorder_status provider read it.

Every assert message carries a DRB_ diagnostic.
"""

import json
import math
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from bulk_downloader import app_data_layer as DL
from bulk_downloader import dom_recorder as DR
from bulk_downloader.dom_capture import DomCapture

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_EV = {"type": 3, "timestamp": 1, "data": {"source": 2, "type": 2}}


class _Page:
    def __init__(self, ret):
        self.ret = ret

    def evaluate(self, js, *a):
        return self.ret


class _Cap:
    def __init__(self):
        self.dom_log = []

    def record_dom_event(self, **kw):
        self.dom_log.append(kw)


def _reset(monkeypatch):
    for name, value in (
        ("_DOM_DROPPED_TOTAL", 0),
        ("_DOM_DROPPED_WARNED", True),
        ("_ARM_FAIL_STREAK", 0),
        ("_ARM_WARNED", True),
    ):
        monkeypatch.setattr(DR, name, value)


def _drain(events, dropped=0, cap=None):
    cap = cap if cap is not None else _Cap()
    n = DR.drain_dom_events(_Page({"events": events, "dropped": dropped}), cap)
    return n, cap


def _run_child(home, body):
    """Run ``body`` in a second Python process (as the capture subprocess is),
    BD_HOME = ``home``, cwd a different directory under it; returns
    (rc, stdout)."""
    run_dir = home / "cwd"
    run_dir.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "LC_ALL": "C",
        "BD_HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = f"import sys\nsys.path.insert(0, {str(_REPO)!r})\n" + textwrap.dedent(body)
    r = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=str(run_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,  # 2x the measured ~6 s RSS child, 60 s floor (v3_66_1222)
        stdin=subprocess.DEVNULL,
    )
    return r.returncode, r.stdout


# ── F6 ──────────────────────────────────────────────────────────────────────
def test_f6_one_drain_keeps_at_most_the_per_drain_bound(monkeypatch):
    _reset(monkeypatch)
    over = 20_000 + 7
    n, cap = _drain([_EV] * over)
    total = DR.get_status()["dom_events_dropped"]
    ok = n == 20_000 and len(cap.dom_log) == 20_000 and total == 7
    assert ok, f"DRB_F6_DRAIN_UNBOUNDED ingested={n} dropped={total}"


def test_f6_per_capture_event_bound_counts_the_excess_dropped(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(DR, "_DOM_LOG_MAX_EVENTS", 50, raising=False)
    cap = _Cap()
    got = [_drain([_EV] * 30, cap=cap)[0] for _ in range(3)]
    total = DR.get_status()["dom_events_dropped"]
    ok = got == [30, 20, 0] and len(cap.dom_log) == 50 and total == 40
    assert ok, (
        f"DRB_F6_LOG_UNBOUNDED per_drain={got} kept={len(cap.dom_log)} dropped={total}"
    )
    # a second capture has its own budget
    n2, cap2 = _drain([_EV] * 10)
    assert n2 == 10 and len(cap2.dom_log) == 10, f"DRB_F6_BUDGET_SHARED n={n2}"


class _NoLogCap:
    """A capture that keeps its events somewhere other than a ``dom_log`` list."""

    def __init__(self):
        self.kept = 0

    def record_dom_event(self, **kw):
        self.kept += 1


def test_f6_event_bound_holds_for_a_capture_without_a_dom_log_list(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(DR, "_DOM_LOG_MAX_EVENTS", 50, raising=False)
    cap = _NoLogCap()
    got = [_drain([_EV] * 30, cap=cap)[0] for _ in range(3)]
    total = DR.get_status()["dom_events_dropped"]
    ok = got == [30, 20, 0] and cap.kept == 50 and total == 40
    assert ok, f"DRB_F6_TALLY_UNBOUNDED per_drain={got} kept={cap.kept} dropped={total}"


def test_f6_per_capture_byte_bound_counts_the_excess_dropped(monkeypatch):
    _reset(monkeypatch)
    big = {
        "type": 3,
        "timestamp": 1,
        "data": {"source": 0, "texts": [{"id": 1, "value": "x" * 2048}]},
    }
    size = len(json.dumps(big, separators=(",", ":")))
    monkeypatch.setattr(DR, "_DOM_LOG_MAX_BYTES", size * 10 + size // 2, raising=False)
    cap = _Cap()
    n_a, _ = _drain([big] * 8, cap=cap)
    n_b, _ = _drain([big] * 8, cap=cap)
    total = DR.get_status()["dom_events_dropped"]
    ok = (n_a, n_b) == (8, 2) and len(cap.dom_log) == 10 and total == 6
    assert ok, (
        f"DRB_F6_BYTES_UNBOUNDED drains={(n_a, n_b)} kept={len(cap.dom_log)} dropped={total}"
    )


def test_f6_forged_flood_keeps_the_capture_rss_bounded(tmp_path):
    """recorder_followup_probe S23 on the real DomCapture(redact=True), in a
    child process so maxrss is the capture's own."""
    rc, out = _run_child(
        tmp_path,
        """
        import json, resource
        from bulk_downloader import dom_recorder as DR
        from bulk_downloader.dom_capture import DomCapture
        def mb(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
        class Pg:
            def __init__(self, b): self.b = b
            def evaluate(self, js): return {"events": self.b, "dropped": 0}
        cap = DomCapture(url="https://site.test/", redact=True)
        small = {"type": 3, "timestamp": 1, "data": {"source": 2, "type": 2}}
        n0 = DR.drain_dom_events(Pg([small] * 1000), cap)
        r0 = mb()
        blob = "x" * 2048
        for tick in range(6):
            batch = [{"type": 3, "timestamp": i, "data": {"source": 0, "texts": [{"id": i, "value": blob + str(i)}]}}
                     for i in range(50_000)]
            DR.drain_dom_events(Pg(batch), cap)
            del batch
        print(json.dumps({"control": n0, "r0": r0, "r1": mb(), "kept": len(cap.dom_log)}))
    """,
    )
    assert rc == 0 and out.strip(), f"DRB_F6_RSS_CHILD rc={rc}"
    m = json.loads(out.strip().splitlines()[-1])
    assert m["control"] == 1000, f"DRB_F6_RSS_CONTROL ingested={m['control']}"
    grew = m["r1"] - m["r0"]
    assert grew < 400 and m["kept"] < 60_000, (
        f"DRB_F6_RSS_UNBOUNDED grew_mb={grew} kept={m['kept']}"
    )


def test_f6_a_real_session_is_untouched(monkeypatch):
    _reset(monkeypatch)
    cap = DomCapture(url="https://site.test/", redact=True)
    n, _ = _drain([_EV] * 1000, cap=cap)
    total = DR.get_status()["dom_events_dropped"]
    assert n == 1000 and len(cap.dom_log) == 1000 and total == 0, (
        f"DRB_F6_CONTROL n={n} dropped={total}"
    )


# ── F7 ──────────────────────────────────────────────────────────────────────
def test_f7_hostile_dropped_values_keep_the_batch_and_count_zero(monkeypatch):
    _reset(monkeypatch)
    bad = []
    for d in ("x", math.inf, math.nan, -math.inf, -1e9, -3, None, [], {}, True, "7"):
        before = DR._DOM_DROPPED_TOTAL
        try:
            n, cap = _drain([_EV] * 5, dropped=d)
        except Exception as e:  # noqa: BLE001
            bad.append((repr(d), type(e).__name__))
            continue
        after = DR._DOM_DROPPED_TOTAL
        if n != 5 or len(cap.dom_log) != 5 or after != before:
            bad.append((repr(d), n, after - before))
    assert not bad, f"DRB_F7_DROPPED_PARSE {bad}"
    # positive control: an honest count still counts
    _drain([_EV], dropped=3)
    _drain([_EV], dropped=2.0)
    assert DR._DOM_DROPPED_TOTAL == 5, f"DRB_F7_CONTROL total={DR._DOM_DROPPED_TOTAL}"


def test_f7_non_list_events_are_an_empty_batch(monkeypatch):
    _reset(monkeypatch)
    got = []
    for events in ("abc", {"type": 3}, 5):
        try:
            got.append(_drain(events)[0])
        except Exception as e:  # noqa: BLE001
            got.append(type(e).__name__)
    assert got == [0, 0, 0], f"DRB_F7_EVENTS_SHAPE {got}"


# ── F8 ──────────────────────────────────────────────────────────────────────
_CHILD_CAPTURE = """
    from bulk_downloader import dom_recorder as DR
    class Fail:
        def evaluate(self, js): raise RuntimeError("torn down")
    class Pg:
        def __init__(self, r): self.r = r
        def evaluate(self, js): return self.r
    DR.attach_dom_recorder(None, None)
    for _ in range(5):
        DR.arm_dom_recorder(Fail())
    DR.drain_dom_events(Pg({"events": [], "dropped": 7}), object())
    print(DR.get_status()["dom_events_dropped"])
"""


def test_f8_status_in_another_process_shows_the_capture_counters(monkeypatch, tmp_path):
    _reset(monkeypatch)
    rc, out = _run_child(tmp_path, _CHILD_CAPTURE)
    assert rc == 0 and out.strip() == "7", (
        f"DRB_F8_CHILD rc={rc} out={out.strip()[:40]!r}"
    )
    st = DR.get_status()
    seen = (st["dom_events_dropped"], st["arm_fail_streak"])
    assert seen == (7, 5), f"DRB_F8_STATUS_PER_PROCESS dropped/streak={seen}"
    api = DL.collect_dom_recorder_status()
    shown = (
        api.get("dom_events_dropped"),
        api.get("arm_fail_streak"),
        api.get("health"),
    )
    assert shown == (7, 5, "degraded"), f"DRB_F8_API_PER_PROCESS {shown}"
    assert st.get("counters_source") == "capture", (
        f"DRB_F8_SOURCE {st.get('counters_source')!r}"
    )


def test_f8_an_arm_streak_alone_reaches_status_and_clears(monkeypatch, tmp_path):
    _reset(monkeypatch)
    arm = """
        from bulk_downloader import dom_recorder as DR
        class Fail:
            def evaluate(self, js): raise RuntimeError("torn down")
        class Armed:
            def evaluate(self, js): return True
        DR.attach_dom_recorder(None, None)
        for _ in range(5):
            DR.arm_dom_recorder(Fail())
        if {ok}:
            DR.arm_dom_recorder(Armed())
    """
    rc, _ = _run_child(tmp_path, arm.replace("{ok}", "False"))
    first = (
        rc,
        DR.get_status()["arm_fail_streak"],
        DL.collect_dom_recorder_status().get("health"),
    )
    rc2, _ = _run_child(tmp_path, arm.replace("{ok}", "True"))
    second = (
        rc2,
        DR.get_status()["arm_fail_streak"],
        DL.collect_dom_recorder_status().get("health"),
    )
    seen = (first, second)
    assert seen == ((0, 5, "degraded"), (0, 0, "ok")), f"DRB_F8_ARM_STREAK {seen}"


def test_f8_a_new_capture_process_starts_from_zero(monkeypatch, tmp_path):
    _reset(monkeypatch)
    rc, _ = _run_child(tmp_path, _CHILD_CAPTURE)
    rc2, _ = _run_child(
        tmp_path,
        """
        from bulk_downloader import dom_recorder as DR
        DR.attach_dom_recorder(None, None)
    """,
    )
    st = DR.get_status()
    seen = (rc, rc2, st["dom_events_dropped"], st["arm_fail_streak"])
    assert seen == (0, 0, 0, 0), f"DRB_F8_STALE_STATE {seen}"


def test_f8_a_damaged_state_file_reads_as_zero(monkeypatch, tmp_path):
    _reset(monkeypatch)
    state = tmp_path / ".dom_recorder_state.json"
    rows = []
    head = '{"dom_events_dropped": 9'
    oversized = (
        head
        + " " * (getattr(DR, "_STATE_MAX_BYTES", 64 * 1024) + 1 - len(head) - 1)
        + "}"
    )
    for body in (
        '{"dom_events_dropped": "x", "arm_fail_streak": -4, "updated": "y"}',
        '{"dom_events_dropped": 1e999}',
        "[1, 2]",
        "not json",
        oversized,
    ):
        state.write_text(body, encoding="utf-8")
        try:
            st = DR.get_status()
            api = DL.collect_dom_recorder_status()
            rows.append(
                (
                    st["dom_events_dropped"],
                    st["arm_fail_streak"],
                    api.get("health"),
                    st.get("counters_source"),
                )
            )
        except Exception as e:  # noqa: BLE001
            rows.append(type(e).__name__)
    # a dict is the capture's (bad values read as 0); anything else is no state
    want = [(0, 0, "ok", "capture")] * 2 + [(0, 0, "ok", "process")] * 3
    assert rows == want, f"DRB_F8_STATE_PARSE {rows}"
