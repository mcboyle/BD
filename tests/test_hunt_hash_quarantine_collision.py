BD_GATE_SCOPE = "module"

import threading
from types import SimpleNamespace

from bulk_downloader import container_repair, runner_integrity


class _Runner(runner_integrity.IntegrityMixin):
    site_id = "site"
    def __init__(self):
        self.config = {"name": "site"}
        self.status = None
        self._lock = threading.RLock()
        self.jobs = {}

    def _update_job(self, _url, status, *_args, **_kwargs):
        self.status = status

    def log_event(self, *_args, **_kwargs):
        pass


def test_hash_failure_keeps_prior_quarantine_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_integrity, "db_log", lambda *_args: None)
    quarantine = tmp_path / "_failed"
    quarantine.mkdir()
    prior = quarantine / "clip.mp4"
    prior.write_bytes(b"older failure")
    second = quarantine / "clip.1.mp4"
    second.write_bytes(b"second failure")
    current = tmp_path / "clip.mp4"
    current.write_bytes(b"new failure")
    runner = _Runner()

    assert runner._verify_hash_or_quarantine("page", "md5", "0" * 32, current, "clip.mp4", 11) is False
    assert runner.status == "failed"
    assert prior.read_bytes() == b"older failure"
    assert second.read_bytes() == b"second failure"
    assert not current.exists()
    assert (quarantine / "clip.2.mp4").read_bytes() == b"new failure"


def test_hash_failure_uses_original_name_when_unoccupied(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_integrity, "db_log", lambda *_args: None)
    current = tmp_path / "clip.mp4"
    current.write_bytes(b"new failure")
    runner = _Runner()

    assert runner._verify_hash_or_quarantine("page", "md5", "0" * 32, current, "clip.mp4", 11) is False
    assert (tmp_path / "_failed" / "clip.mp4").read_bytes() == b"new failure"


def test_media_integrity_failure_keeps_prior_quarantine_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_integrity, "db_log", lambda *_args: None)
    monkeypatch.setattr(runner_integrity, "verify_media_integrity", lambda *_args: (False, "bad media"))
    monkeypatch.setattr(container_repair, "repair", lambda *_args: SimpleNamespace(recovered=False))
    quarantine = tmp_path / "_failed"
    quarantine.mkdir()
    prior = quarantine / "clip.mp4"
    prior.write_bytes(b"older failure")
    current = tmp_path / "clip.mp4"
    current.write_bytes(b"new failure")
    runner = _Runner()

    assert runner._verify_integrity_or_quarantine("page", current, "clip.mp4", 11) == (False, False, "bad media")
    assert runner.status == "failed"
    assert prior.read_bytes() == b"older failure"
    assert not current.exists()
    assert any(p.read_bytes() == b"new failure" for p in quarantine.iterdir() if p != prior)


def test_quarantine_needs_no_hard_link_support(monkeypatch, tmp_path):
    from bulk_downloader import runner_integrity

    def no_link(*_a, **_k):
        raise PermissionError("filesystem does not support hard links")

    monkeypatch.setattr(runner_integrity.os, "link", no_link)
    (tmp_path / "_failed").mkdir()
    (tmp_path / "_failed" / "clip.mp4").write_bytes(b"old")
    bad = tmp_path / "clip.mp4"
    bad.write_bytes(b"new")
    target = runner_integrity._quarantine_failure(bad)
    assert not bad.exists()
    assert target.read_bytes() == b"new"
    assert (tmp_path / "_failed" / "clip.mp4").read_bytes() == b"old"
