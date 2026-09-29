"""dl95-filthykings-1 (HIGH): Stop / Cancel must not let a browser download land.

Measured on test2 (v1711/1712): filthykings b920e6d0 site Stop 03:07:05Z froze the tracked
HTTP .part (2.18 / 1.48 GB), yet 292751 "Doing Things Differently" landed complete (2.44 GB,
metadata_tagged 03:09:15Z) and 292398 (2.04 GB) at 03:09:40Z; nubiles-porn c1f4c6b5 the same
(Stop 02:41:33Z -> 2.17 GB file 02:45:00Z).  Only the browser arm (Playwright save_as)
writes straight to the final name, and nothing on that arm reads the site stop event or the
job's "stopped" status: the bytes are renamed into place and recorded "done".

The HTTP legs (chunk gates, the HTTP-failed -> browser re-click fallback) belong to
dl95-porndig-3.  This file pins the browser arm itself: a Stop or a Cancel that arrives while
the browser download is in flight leaves no file at the final name and no "done".
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

JOB = "https://members.filthykings.com/en/video/filthykings/Doing-Things-Differently/292751"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"\x11" * 4096


class _Download:
    url = "https://cdn.filthykings.invalid/292751_2160.mp4"
    suggested_filename = "292751_2160.mp4"

    def __init__(self, during_save=None):
        self.during_save = during_save
        self.cancelled = False

    def save_as(self, path):
        # The browser moves the whole file; Stop/Cancel lands while it is moving.
        Path(path).write_bytes(PAYLOAD[:16])
        if self.during_save:
            self.during_save()
        with open(path, "ab") as fh:
            fh.write(PAYLOAD[16:])

    def cancel(self):
        self.cancelled = True


class _Page:
    url = JOB

    def __init__(self, dl):
        self.dl = dl

    def evaluate(self, _script):
        return None

    @contextmanager
    def expect_download(self, *, timeout):
        yield type("_Info", (), {"value": self.dl})()

    def title(self):
        return "292751"


class _Locator:
    def get_attribute(self, _name):
        return None

    def click(self):
        pass


def _runner(monkeypatch):
    from bulk_downloader import runner_browser, runner_transport as transport

    class _R(transport.TransportMixin):
        _pw_save = runner_browser.BrowserMixin._pw_save

        def __init__(self):
            self.site_id = "dl95fk1"
            self.config = {"name": "dl95fk1", "use_http_dl": False,
                           "verify_hash": False, "verify_integrity": False}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self.jobs = {JOB: {"status": "running", "message": "Clicking [4K]"}}
            self.status, self.failures, self.logged = [], [], []

        def _screenshot(self, *_a, **_k):
            return ""

        def _update_job(self, url, state, message="", **_k):
            self.status.append((state, message))
            with self._lock:
                self.jobs.setdefault(url, {}).update(status=state, message=message)

        def _handle_failure(self, _url, message, **_k):
            self.failures.append(message)

        def _size_on_disk_after_tagging(self, _path, size):
            return size

    r = _R()
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log",
                        lambda *a, **_k: r.logged.append(a[3] if len(a) > 3 else "?"))
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _i: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    return r


def _run(r, dl, dest):
    r._do_download(_Page(dl), object(), JOB,
                   {"locator": _Locator(), "score": 2160, "size": 0, "text": "4K",
                    "_all_candidates": []}, dest, "2160p")


def _landed(dest):
    return sorted(p.name for p in dest.rglob("*") if p.is_file())


def test_dl95_filthykings_1_site_stop_during_browser_download_lands_nothing(tmp_path, monkeypatch):
    r = _runner(monkeypatch)

    def site_stop():  # SiteRunner.stop(): the event, and the job row reads "stopped"
        r._stop.set()
        r.jobs[JOB].update(status="stopped", message="Stopped")

    _run(r, _Download(site_stop), tmp_path)
    assert not _landed(tmp_path) and "done" not in [s for s, _m in r.status] + r.logged, (
        f"DL95_FK1_STOPPED_BROWSER_DOWNLOAD_LANDED: files={_landed(tmp_path)} "
        f"status={r.status} logged={r.logged}")


def test_dl95_filthykings_1_cancel_during_browser_download_lands_nothing(tmp_path, monkeypatch):
    r = _runner(monkeypatch)

    def cancel():  # app_queue api_queue_v2_cancel's exact call
        r._update_job(JOB, "stopped", "Cancelled by user")

    _run(r, _Download(cancel), tmp_path)
    assert not _landed(tmp_path) and "done" not in [s for s, _m in r.status] + r.logged, (
        f"DL95_FK1_CANCELLED_BROWSER_DOWNLOAD_LANDED: files={_landed(tmp_path)} "
        f"status={r.status} logged={r.logged}")
    assert r.jobs[JOB]["status"] == "stopped", r.jobs[JOB]


def test_dl95_filthykings_1_control_browser_download_without_stop_lands(tmp_path, monkeypatch):
    r = _runner(monkeypatch)
    _run(r, _Download(), tmp_path)
    files = _landed(tmp_path)
    assert len(files) == 1 and (tmp_path / files[0]).read_bytes() == PAYLOAD, files
    assert "done" in [s for s, _m in r.status], r.status
