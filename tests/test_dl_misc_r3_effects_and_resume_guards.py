"""dl-misc r3 -- the two items left owed by dl-misc r2 (download-engine sweep).

A  app._do_action_all (/api/pause_all, /api/resume_all, /api/start_all)
   counted every runner whose method did not raise as ``applied_to``. The
   methods are state-gated and return quietly on a runner they leave alone,
   so two idle sites and one running reported ``applied_to: 3`` for a pause
   that stopped one pool. Same "attempt counted as effect" class as row 451
   (_pause_every_runner) and dl-runner #5 (/api/runners/resume_all).
B  resume.reconcile_with_disk did ``int(checkpoint.get("total") or 0)``
   unguarded, and incomplete_chunks caught no OverflowError. json.load
   accepts ``Infinity``/``NaN``, so a corrupt sidecar raised out of the
   resume readers instead of being reported not resumable / skipped.

Each defect test has a control beside it that passes on the old code too, so
a pass is not just a probe that can never say yes.
"""
from __future__ import annotations

import json

import pytest


BD_GATE_SCOPE = "module"


# ── A: _do_action_all counts effects ─────────────────────────────────────


class _Gated:
    """A runner with SiteRunner's state gates: pause() acts only on a
    running pool, start()/resume() arm only a non-running one."""

    def __init__(self, state, arms=True):
        self._state = state
        self.arms = arms
        self.calls = 0

    def pause(self):
        self.calls += 1
        if self._state != "running":
            return
        self._state = "paused"

    def start(self):
        self.calls += 1
        if self._state == "running" or not self.arms:
            return
        self._state = "running"

    resume = start


def _post(fresh_app, monkeypatch, route, pairs):
    from bulk_downloader import app as app_module
    from bulk_downloader.app_state import runners

    monkeypatch.setattr(app_module, "_check_csrf", lambda: None)
    monkeypatch.setattr(app_module, "_rate_check", lambda action: True)
    runners.clear()
    runners.update(pairs)
    resp = fresh_app.post(route)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_pause_all_does_not_count_idle_runners(fresh_app, monkeypatch):
    rs = {"idle1": _Gated("idle"), "idle2": _Gated("idle"),
          "run": _Gated("running")}
    body = _post(fresh_app, monkeypatch, "/api/pause_all", rs)
    assert all(r.calls == 1 for r in rs.values()), "fixture: pause not called"
    assert body["applied_to"] == 1, (
        "DL-MISC-R3-A: pause_all applied_to=%r but only one runner was "
        "running" % body["applied_to"])
    assert body["unchanged"] == [{"sid": "idle1", "state": "idle"},
                                 {"sid": "idle2", "state": "idle"}], body
    assert body["total_sites"] == 3 and body["ok"] is True


def test_pause_all_does_not_count_a_real_idle_site_runner(
        fresh_app, monkeypatch):
    from bulk_downloader.runner import SiteRunner

    idle = SiteRunner("dlm3_idle", {"name": "dlm3_idle"})
    assert idle._state != "running", "fixture: a new runner is running"
    body = _post(fresh_app, monkeypatch, "/api/pause_all", {"dlm3_idle": idle})
    assert body["applied_to"] == 0, (
        "DL-MISC-R3-A: pause_all counted a real idle SiteRunner as paused "
        "(applied_to=%r, state %r)" % (body["applied_to"], idle._state))


def test_pause_all_counts_every_running_runner(fresh_app, monkeypatch):
    """Control: pausing running pools is still counted, nothing unchanged."""
    rs = {"a": _Gated("running"), "b": _Gated("running")}
    body = _post(fresh_app, monkeypatch, "/api/pause_all", rs)
    assert body["applied_to"] == 2 and body.get("unchanged", []) == []
    assert all(r._state == "paused" for r in rs.values())


def test_resume_all_does_not_count_a_runner_left_paused(fresh_app, monkeypatch):
    rs = {"stuck": _Gated("paused", arms=False), "ok": _Gated("paused"),
          "busy": _Gated("running")}
    body = _post(fresh_app, monkeypatch, "/api/resume_all", rs)
    assert body["applied_to"] == 1, (
        "DL-MISC-R3-A: resume_all applied_to=%r but only one runner was "
        "armed" % body["applied_to"])
    assert body["unchanged"] == [{"sid": "stuck", "state": "paused"},
                                 {"sid": "busy", "state": "running"}], body


def test_start_all_does_not_count_a_runner_that_stayed_idle(
        fresh_app, monkeypatch):
    rs = {"idle": _Gated("idle", arms=False), "go": _Gated("idle")}
    body = _post(fresh_app, monkeypatch, "/api/start_all", rs)
    assert body["applied_to"] == 1, (
        "DL-MISC-R3-A: start_all applied_to=%r but only one runner "
        "started" % body["applied_to"])
    assert body["unchanged"] == [{"sid": "idle", "state": "idle"}], body


def test_resume_all_counts_runners_it_armed(fresh_app, monkeypatch):
    """Control: every paused runner it arms is counted."""
    rs = {"a": _Gated("paused"), "b": _Gated("paused")}
    body = _post(fresh_app, monkeypatch, "/api/resume_all", rs)
    assert body["applied_to"] == 2 and body.get("unchanged", []) == []


# ── B: resume readers survive a corrupt sidecar ──────────────────────────


def _sidecar(tmp_path, total_json, part_bytes=10):
    from bulk_downloader import resume

    final = tmp_path / "video.mp4"
    resume.part_path(final).write_bytes(b"x" * part_bytes)
    resume.sidecar_path(final).write_text(
        '{"format_version": %d, "url": "https://example.test/v.mp4", '
        '"total": %s, "chunks": [{"start": 0, "end": 9, "done_bytes": 4}]}'
        % (resume.FORMAT_VERSION, total_json), encoding="utf-8")
    return final


@pytest.mark.parametrize("total_json",
                         ["Infinity", "NaN", '"abc"', "[10]", '{"n": 10}'])
def test_reconcile_with_disk_rejects_a_corrupt_total(tmp_path, total_json):
    from bulk_downloader import resume

    final = _sidecar(tmp_path, total_json)
    cp = resume.load(final)
    assert cp is not None, "fixture: load() refused the sidecar outright"
    try:
        ok = resume.reconcile_with_disk(cp, final)
    except Exception as e:  # noqa: BLE001 -- any raise is the defect
        pytest.fail("DL-MISC-R3-B: reconcile_with_disk raised %s on total=%s"
                    % (type(e).__name__, total_json))
    assert ok is False, "corrupt total %s reconciled as resumable" % total_json


def test_reconcile_with_disk_accepts_a_sound_checkpoint(tmp_path):
    """Control: an integer total matching the .part still reconciles."""
    from bulk_downloader import resume

    final = _sidecar(tmp_path, "10")
    assert resume.reconcile_with_disk(resume.load(final), final) is True
    assert resume.reconcile_with_disk(resume.load(_sidecar(
        tmp_path, "11")), final) is False


def test_incomplete_chunks_skips_an_infinite_chunk():
    from bulk_downloader import resume

    cp = json.loads('{"chunks": ['
                    '{"start": 0, "end": Infinity, "done_bytes": 0},'
                    '{"start": 0, "end": 9, "done_bytes": Infinity},'
                    '{"start": 10, "end": 19, "done_bytes": 3}]}')
    try:
        out = resume.incomplete_chunks(cp)
    except OverflowError:
        pytest.fail("DL-MISC-R3-B: incomplete_chunks raised OverflowError "
                    "on an Infinity chunk")
    assert out == [{"start": 10, "end": 19, "done_bytes": 3}], out


def test_incomplete_chunks_still_lists_unfinished_chunks():
    """Control: finished chunks drop out, unfinished ones stay."""
    from bulk_downloader import resume

    cp = {"chunks": [{"start": 0, "end": 9, "done_bytes": 10},
                     {"start": 10, "end": 19, "done_bytes": 3}]}
    assert resume.incomplete_chunks(cp) == [cp["chunks"][1]]
