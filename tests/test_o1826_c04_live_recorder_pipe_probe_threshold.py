"""O1826 C04 -- live_recorder: unread stderr PIPE, unbounded per-tick probes,
hardcoded low-disk floor (O1778 findings M103, M101, M102).

M103: the long-running streamlink/ffmpeg child got ``stderr=subprocess.PIPE``
and nothing in the module ever reads it. Once a multi-hour stream has written
about 64 KiB of warnings the child blocks on its next stderr write and the
recording silently stops growing. The RED child floods stderr first and then
appends to its output file; with an unread pipe the file never grows.

M101: every scheduler tick probed EVERY pending room with a blocking
``streamlink --json`` (20 s timeout each), in dict order, interleaved with
the health checks of active recordings -- N pending rooms delayed those
health checks by up to N x 20 s. The tick now health-checks every active
recording first and probes at most 3 pending rooms, least recently probed
first, so every pending room is still reached within ceil(N / 3) ticks --
including rooms pending for days when a burst of new watches arrives.

M102: the low-disk refusal compared against a hardcoded 5 GB although the
module docstring says it reads the per-site ``disk_threshold_gb``. s_cfg is
keyed by 8-hex site ids, not by the live label, so the site is matched by
its login_url host; the tests seed s_cfg in that real layout.

Every module-level rebinding goes through monkeypatch (restored per test);
child processes are reaped in the fixture teardown.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from bulk_downloader import app_state, live_recorder

BD_GATE_SCOPE = "module"

# Floods stderr (16x the 64 KiB Linux pipe buffer) BEFORE writing any output,
# then appends to the output file for ~20 s.
_FLOOD_CHILD = (
    "import sys, time\n"
    "sys.stderr.buffer.write(b'W' * (1 << 20))\n"
    "sys.stderr.flush()\n"
    "with open(sys.argv[1], 'ab', buffering=0) as fh:\n"
    "    for _ in range(400):\n"
    "        fh.write(b'x' * 4096)\n"
    "        time.sleep(0.05)\n"
)

_SLEEP_CHILD = "import time; time.sleep(30)"


class _Egress:
    """Stands in for PreparedHttpProxy."""

    proxy_url = None

    def __init__(self) -> None:
        self.close_calls = 0

    def subprocess_env(self) -> dict:
        return dict(os.environ)

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture
def lr(tmp_path, monkeypatch):
    live_recorder._reset_for_tests()
    live_recorder._reset_backend_cache_for_tests()
    live_recorder.init(str(tmp_path / "state"))
    monkeypatch.setattr(live_recorder, "_disk_check", None)
    monkeypatch.setattr(live_recorder, "_push_notifier", None)
    monkeypatch.setattr(live_recorder, "preferred_backend", lambda: "streamlink")
    yield live_recorder
    with live_recorder._lock:
        procs = list(live_recorder._subprocesses.values())
    for proc in procs:
        proc.kill()
        proc.wait(timeout=10)
    live_recorder._reset_for_tests()
    live_recorder._reset_backend_cache_for_tests()


def _rec(lr, tmp_path: Path, rid: str, room: str = "somemodel",
         state: str = "pending"):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    rec = lr.Recording(
        recording_id=rid,
        site="chaturbate",
        room=room,
        url=f"https://chaturbate.com/{room}",
        output_path=str(out / f"{room}.ts"),
        started_at=0.0,
        state=state,
    )
    with lr._lock:
        lr._recordings[rid] = rec
    return rec


def _child_cmd(monkeypatch, lr, argv_tail):
    monkeypatch.setattr(
        lr, "_build_cmd",
        lambda backend, rec, proxy_url=None: [sys.executable, *argv_tail(rec)])


# ─── M103: child stderr is never an unread pipe ─────────────────────────


def test_m103_stderr_flood_does_not_stall_the_recording(lr, tmp_path, monkeypatch):
    script = tmp_path / "flood_child.py"
    script.write_text(_FLOOD_CHILD)
    _child_cmd(monkeypatch, lr, lambda rec: [str(script), rec.output_path])
    rec = _rec(lr, tmp_path, "rid-m103")

    lr._spawn_recording("rid-m103", rec, prepared_egress=_Egress())
    assert rec.state == "recording", rec.last_error
    proc = lr._subprocesses["rid-m103"]

    deadline = time.monotonic() + 10.0
    first = 0
    while time.monotonic() < deadline and proc.poll() is None:
        first = lr._safe_file_size(rec.output_path)
        if first:
            break
        time.sleep(0.1)
    assert first > 0, (
        "M103: recorder child wrote 1 MiB to stderr and its output file did not "
        "grow in 10 s -- stderr is an unread PIPE (child blocked at 64 KiB); "
        f"Popen.stderr={proc.stderr!r} poll={proc.poll()!r}")
    time.sleep(0.5)
    assert lr._safe_file_size(rec.output_path) > first, (
        "M103: output file stopped growing after the stderr flood")
    assert proc.stderr is None, (
        f"M103: recorder child stderr is a pipe nobody reads: {proc.stderr!r}")


# ─── M101: per-tick probe cost is bounded ───────────────────────────────


def test_m101_tick_health_checks_first_and_probes_at_most_three(
        lr, tmp_path, monkeypatch):
    events: list[tuple[str, str]] = []
    _rec(lr, tmp_path, "rid-active", room="activeroom", state="recording")
    rooms = [f"room{i:02d}" for i in range(10)]
    for room in rooms:
        _rec(lr, tmp_path, f"rid-{room}", room=room)
    egresses: list[_Egress] = []

    def _egress(site):
        egresses.append(_Egress())
        return egresses[-1]

    monkeypatch.setattr(lr, "_check_recording_health",
                        lambda rid, rec: events.append(("health", rid)))
    monkeypatch.setattr(lr, "_egress_prepare", _egress)
    monkeypatch.setattr(lr, "_is_room_live",
                        lambda site, room, url, prepared_egress=None:
                        events.append(("probe", room)) or False)

    ticks = []
    for _ in range(4):
        events.clear()
        lr._scheduler_tick()
        ticks.append(list(events))

    probes_per_tick = [sum(1 for kind, _ in t if kind == "probe") for t in ticks]
    assert probes_per_tick == [3, 3, 3, 3], (
        "M101: one scheduler tick probed %r pending rooms (blocking 20 s "
        "streamlink --json each); want at most 3 per tick" % probes_per_tick)
    for t in ticks:
        assert t[0] == ("health", "rid-active"), (
            "M101: active recording health check ran after pending probes: %r" % t)
        assert [e for e in t if e[0] == "health"] == [("health", "rid-active")]
    probed = [room for t in ticks for kind, room in t if kind == "probe"]
    assert probed == rooms + ["room00", "room01"], (
        "M101: pending probes not least-recently-probed-first: %r" % probed)
    assert len(egresses) == 12
    assert all(e.close_calls == 1 for e in egresses)


def test_m101_new_watches_do_not_starve_long_pending_rooms(
        lr, tmp_path, monkeypatch):
    old = [f"oldroom{i}" for i in range(4)]
    new = [f"newroom{i}" for i in range(3)]
    for room in old:
        _rec(lr, tmp_path, f"rid-{room}", room=room).poll_count = 1000
    for room in new:
        _rec(lr, tmp_path, f"rid-{room}", room=room)
    probed: list[list[str]] = []
    monkeypatch.setattr(lr, "_egress_prepare", lambda site: _Egress())
    monkeypatch.setattr(lr, "_is_room_live",
                        lambda site, room, url, prepared_egress=None:
                        probed[-1].append(room) or False)
    for _ in range(3):
        probed.append([])
        lr._scheduler_tick()
    assert [len(t) for t in probed] == [3, 3, 3], probed
    assert sorted({r for t in probed for r in t}) == sorted(old + new), (
        "M101: 7 pending rooms not all probed within ceil(7/3)=3 ticks -- "
        "long-pending rooms starved: %r" % probed)
    with lr._lock:
        lr._recordings["rid-oldroom0"].state = "finished"
    lr._scheduler_tick()
    assert "rid-oldroom0" not in lr._last_probe_tick, (
        "M101: probe bookkeeping kept a rid that left the pending set")
    assert sorted(lr._last_probe_tick) == sorted(
        f"rid-{r}" for r in old[1:] + new)


def test_m101_negative_control_few_pending_rooms_all_probed(
        lr, tmp_path, monkeypatch):
    probed: list[str] = []
    for room in ("roomaa", "roombb"):
        _rec(lr, tmp_path, f"rid-{room}", room=room)
    monkeypatch.setattr(lr, "_egress_prepare", lambda site: _Egress())
    monkeypatch.setattr(lr, "_is_room_live",
                        lambda site, room, url, prepared_egress=None:
                        probed.append(room) or False)
    lr._scheduler_tick()
    assert probed == ["roomaa", "roombb"]


# ─── M102: the per-site disk_threshold_gb is read ───────────────────────
#
# s_cfg is keyed by 8-hex site ids (uuid4().hex[:8]) and every entry is
# rebuilt from CFG_FIELDS, so the only URL a site config carries is its
# login_url. Recording.site is the canonical host label ("chaturbate").


def _site_cfg(login_url, **extra):
    """An s_cfg entry as the boot loader / site writers leave it."""
    cfg = {"name": "Site", "login_url": login_url, "username": "",
           "download_dir": "/srv/media", "wait": 4, "delay": 3,
           "max_concurrent": 2, "disk_threshold_gb": 2.0}
    cfg.update(extra)
    return cfg


def _disk_case(lr, tmp_path, monkeypatch, s_cfg, free_gb, rid):
    monkeypatch.setattr(app_state, "s_cfg", s_cfg)
    checked: list[str] = []
    monkeypatch.setattr(lr, "_disk_check",
                        lambda out_dir: checked.append(out_dir) or free_gb)
    pushes: list[tuple[str, str]] = []
    monkeypatch.setattr(lr, "_push_notifier",
                        lambda title, body: pushes.append((title, body)))
    _child_cmd(monkeypatch, lr, lambda rec: ["-c", _SLEEP_CHILD])
    rec = _rec(lr, tmp_path, rid)
    egress = _Egress()
    lr._spawn_recording(rid, rec, prepared_egress=egress)
    assert len(checked) == 1, "disk_check hook did not fire exactly once"
    return rec, egress, pushes


def test_m102_site_threshold_50_refuses_with_10_gb_free(lr, tmp_path, monkeypatch):
    s_cfg = {
        "0b698210": _site_cfg("https://chaturbate.com/auth/login/",
                              name="Chaturbate", disk_threshold_gb=50),
        "720a7025": _site_cfg("https://www.stripchat.com/login",
                              disk_threshold_gb=1),
    }
    rec, egress, pushes = _disk_case(
        lr, tmp_path, monkeypatch, s_cfg, 10.0, "rid-m102")
    assert rec.site == "chaturbate"
    assert rec.state == "failed", (
        "M102: configured site 0b698210 (login_url chaturbate.com, "
        "disk_threshold_gb=50) with 10.0 GB free started recording "
        "(state=%r) -- the per-site threshold is not resolved from the "
        "8-hex-keyed s_cfg" % rec.state)
    assert rec.last_error == "low_disk: 10.0GB free (threshold 50GB)"
    assert lr._subprocesses == {}
    assert egress.close_calls == 1
    assert pushes == [("Live recording skipped",
                       "chaturbate/somemodel: only 10.0GB free (threshold 50GB)")]


@pytest.mark.parametrize("login_url", [
    "https://www.chaturbate.com/auth/login/",
    "HTTPS://Chaturbate.com:443/auth/login/",
    "http://chaturbate.com",
], ids=["www", "case-port", "bare-host"])
def test_m102_login_url_host_forms_match(lr, tmp_path, monkeypatch, login_url):
    s_cfg = {"8bc7023a": _site_cfg(login_url, disk_threshold_gb=50)}
    rec, _, _ = _disk_case(lr, tmp_path, monkeypatch, s_cfg, 10.0, "rid-host")
    assert rec.last_error == "low_disk: 10.0GB free (threshold 50GB)", (
        "M102: login_url %r not matched to site 'chaturbate': state=%r"
        % (login_url, rec.state))


def test_m102_matched_site_boot_default_2_gb_starts_with_4_gb_free(
        lr, tmp_path, monkeypatch):
    # A matched site with no explicit setting carries the boot-loader default
    # (app_kernel DEFAULTS disk_threshold_gb=2.0, the value workers use);
    # the site's own value governs, not the 5 GB unmatched-site floor.
    s_cfg = {"0b698210": _site_cfg("https://chaturbate.com/auth/login/")}
    rec, egress, _ = _disk_case(
        lr, tmp_path, monkeypatch, s_cfg, 4.0, "rid-m102b")
    assert rec.state == "recording", (
        "M102: matched site disk_threshold_gb=2.0 with 4.0 GB free was "
        "refused: %r" % rec.last_error)
    assert egress.close_calls == 0


def test_m102_several_matching_sites_use_the_highest_threshold(
        lr, tmp_path, monkeypatch):
    s_cfg = {
        "0b698210": _site_cfg("https://chaturbate.com/auth/login/",
                              disk_threshold_gb=8),
        "720a7025": _site_cfg("https://www.chaturbate.com/",
                              disk_threshold_gb=20),
        "8bc7023a": _site_cfg("https://chaturbate.com/", disk_threshold_gb="nan"),
    }
    rec, _, _ = _disk_case(lr, tmp_path, monkeypatch, s_cfg, 10.0, "rid-max")
    assert rec.last_error == "low_disk: 10.0GB free (threshold 20GB)", (
        rec.state, rec.last_error)


@pytest.mark.parametrize("s_cfg", [
    {},                                                    # nothing configured
    {"720a7025": _site_cfg("https://stripchat.com/login",  # other site only
                           disk_threshold_gb=50)},
    {"chaturbate": {"disk_threshold_gb": 50}},             # label-keyed: never written
    {"0b698210": {"name": "Chaturbate",                    # no login_url
                  "disk_threshold_gb": 50}},
    {"0b698210": {"login_url": "https://chaturbate.com/"}},  # matched, no key
    {"0b698210": _site_cfg("https://chaturbate.com/",      # non-finite would
                           disk_threshold_gb="nan")},      # disable the gate
    {"0b698210": _site_cfg("https://chaturbate.com/",
                           disk_threshold_gb="junk")},     # non-numeric
], ids=["empty", "other-site", "label-key", "no-login-url", "no-key", "nan",
        "junk"])
@pytest.mark.parametrize("free_gb,want", [(4.0, "failed"), (6.0, "recording")],
                         ids=["4gb", "6gb"])
def test_m102_negative_control_default_5_gb_path_unchanged(
        lr, tmp_path, monkeypatch, s_cfg, free_gb, want):
    rec, egress, pushes = _disk_case(
        lr, tmp_path, monkeypatch, s_cfg, free_gb, "rid-neg")
    assert rec.state == want, (rec.state, rec.last_error)
    if want == "failed":
        assert rec.last_error == "low_disk: 4.0GB free (threshold 5GB)", (
            rec.last_error)
        assert egress.close_calls == 1
        assert len(pushes) == 1
    else:
        assert egress.close_calls == 0
        assert pushes == []
