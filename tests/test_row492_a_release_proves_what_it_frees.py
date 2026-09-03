"""Rows 492, 523, 489, 506 and 507: the release half of a staging claim's life.

staging_claim is a module built on identity -- claim() refuses a foreign claim,
reserve() diverts around one -- and its single state-MUTATING exit had none.
`release(staging_path)` took no identity, read no claim, and unlinked the owner
file unconditionally. Its docstring called a leaked claim inert, which is true
only for the caller that owns it.

Five measured consequences, one contract:

  492  release() frees a claim it cannot prove it owns.
  523  _do_download reserves once and releases at only some of its exits, so a
       claim outlives the job that took it with nothing able to see it.
  489  the browser fallback releases a claim its .part OUTLIVES -- the bytes
       stay, unowned, and the next job rendering that name adopts them.
  506  a leaked .owner is invisible to every sweep in the product:
       cleanup_helpers._PARTIAL_EXTS is a five-extension denominator that does
       not include it, so GET /api/cleanup/summary can never report one.
  507  force_cleanup counts a cleanup it did not perform.

Each test asserts its preconditions before its verdict, because every one of
these is a verdict reached over a state nobody measured.
"""
from __future__ import annotations

import pathlib
import threading

import pytest

from bulk_downloader import staging_claim as sc

BD_GATE_SCOPE = "repo-wide"

_JOB_A = "https://example.test/scene-a"
_JOB_B = "https://example.test/scene-b"


def _ident(url: str) -> str:
    return sc.job_identity(url)


# ── 492: a release must prove what it frees ────────────────────────────────

def test_release_refuses_a_claim_another_job_owns(tmp_path):
    final = tmp_path / "Contested.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert owner.is_file(), "precondition: job A holds the claim"

    freed = sc.release(staging, _ident(_JOB_B))

    assert freed is False, "job B was told it freed a claim it does not own"
    assert owner.is_file(), (
        "job B unlinked job A's claim; the next reserve() would hand A's "
        "staging path to a third job while A is still writing into it")
    assert sc._read_owner_identity(owner) == _ident(_JOB_A)


def test_release_frees_its_own_claim(tmp_path):
    """POSITIVE CONTROL: the ordinary path must still work, or every completed
    download leaks a claim and the fix is worse than the defect."""
    final = tmp_path / "Mine.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert owner.is_file()

    assert sc.release(staging, _ident(_JOB_A)) is True
    assert not owner.exists()


def test_release_is_idempotent_when_there_is_no_claim(tmp_path):
    final = tmp_path / "Absent.mp4"
    staging = sc.staging_path_for(final)
    assert not sc.owner_path_for(staging).exists()
    assert sc.release(staging, _ident(_JOB_A)) is True


def test_a_forced_release_is_explicit_and_still_reports_what_it_did(tmp_path):
    """The operator-driven sweep genuinely may free a foreign claim -- that is
    what delete_orphan is for -- but it must SAY so rather than inherit the
    old unconditional behaviour by omission."""
    final = tmp_path / "Operator.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    assert sc.release(staging, _ident(_JOB_B), force=True) is True
    assert not owner.exists()


def test_release_without_an_identity_is_refused(tmp_path):
    """A caller that cannot name itself cannot prove ownership. UNKNOWN is a
    failing state, never permission (A2)."""
    final = tmp_path / "Anonymous.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    owner = sc.owner_path_for(staging)
    with pytest.raises((TypeError, ValueError)):
        sc.release(staging)
    assert owner.is_file(), "the anonymous call freed the claim anyway"


# ── 489: a claim may not be released while its .part still exists ──────────

def test_release_refuses_while_its_own_part_still_holds_bytes(tmp_path):
    """release's own docstring states the precondition -- drop a claim once its
    .part is GONE. It never checked. The browser fallback releases after
    _http_download raised, a path that removes neither the bytes nor the claim,
    so the .part outlives its claim and the next job adopts it."""
    final = tmp_path / "Outlives.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(b"\xaa" * 1024)
    owner = sc.owner_path_for(staging)

    freed = sc.release(staging, _ident(_JOB_A))

    assert freed is False, (
        "the claim was dropped while its .part still holds 1024 bytes; those "
        "bytes are now unowned and the next job rendering this name adopts them")
    assert owner.is_file()
    assert staging.stat().st_size == 1024, "the bytes were altered"


def test_release_succeeds_once_the_part_is_gone(tmp_path):
    """POSITIVE CONTROL for the rule above."""
    final = tmp_path / "Promoted.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    staging.write_bytes(b"\xbb" * 16)
    staging.unlink()
    assert sc.release(staging, _ident(_JOB_A)) is True
    assert not sc.owner_path_for(staging).exists()


# ── 506: a leaked claim must be visible to the sweeps ─────────────────────

def test_a_leaked_claim_is_visible_to_the_partial_sweep():
    from bulk_downloader import cleanup_helpers

    exts = cleanup_helpers._PARTIAL_EXTS
    assert exts, "the partial-file denominator is empty"
    assert sc.OWNER_SUFFIX in exts, (
        f"the sweep denominator is {sorted(exts)}, which cannot see a leaked "
        f"{sc.OWNER_SUFFIX}; GET /api/cleanup/summary can therefore never "
        "report one, and nothing else in the product looks for it either")


def test_the_partial_sweep_still_sees_ordinary_partials():
    """NEGATIVE CONTROL: widening the denominator must not narrow it."""
    from bulk_downloader import cleanup_helpers

    for ext in (".part", ".ytdl", ".download", ".crdownload", ".tmp"):
        assert ext in cleanup_helpers._PARTIAL_EXTS, ext


# ── 507: force cleanup counts filesystem effects, not attempts ─────────────

class _BulkDeleteRunner:
    def __init__(self, download_dir, jobs):
        self.config = {"download_dir": str(download_dir)}
        self.jobs = jobs
        self._lock = threading.RLock()
        self.events = []

    def log_event(self, kind, message, **kwargs):
        self.events.append((kind, message, kwargs))


def _bulk_delete_client(monkeypatch, runner):
    from flask import Flask
    from bulk_downloader import app_sites_queue, db, sse_broker

    app = Flask("row507-force-cleanup")
    app.config["TESTING"] = True
    app.register_blueprint(app_sites_queue.sites_bp)
    monkeypatch.setattr(app_sites_queue, "_app_runners",
                        lambda: {"row507": runner})
    monkeypatch.setattr(db, "queue_bulk_delete",
                        lambda _sid, urls: len(urls))
    monkeypatch.setattr(sse_broker, "publish", lambda *_a, **_k: None)
    return app.test_client()


def _partial_artifacts(final_path):
    from bulk_downloader import resume

    part = sc.staging_path_for(final_path)
    return (
        part,
        part.with_suffix(part.suffix + ".meta"),
        resume.sidecar_path(final_path),
        sc.owner_path_for(part),
    )


def test_force_cleanup_removes_all_partial_artifacts_and_counts_each_effect(
    tmp_path, monkeypatch,
):
    process_dir = tmp_path / "process-cwd"
    download_dir = tmp_path / "site-downloads"
    process_dir.mkdir()
    download_dir.mkdir()
    monkeypatch.chdir(process_dir)

    urls = [f"https://example.test/row507/{index}" for index in range(3)]
    jobs = {}
    expected = []
    for index, url in enumerate(urls):
        filename = f"queued-{index}.mp4"
        assert "/" not in filename and "\\" not in filename
        jobs[url] = {"filename": filename, "status": "pending"}
        final_path = download_dir / filename
        part = sc.claim(final_path, sc.job_identity(url))
        part.write_bytes(bytes([index + 1]) * 17)
        artifacts = _partial_artifacts(final_path)
        artifacts[1].write_text("validator", encoding="utf-8")
        artifacts[2].write_text("checkpoint", encoding="utf-8")
        expected.extend(artifacts)

    runner = _BulkDeleteRunner(download_dir, jobs)
    client = _bulk_delete_client(monkeypatch, runner)

    # Preconditions: the denominator is independently constructed from three
    # jobs x four promised artifacts, and none resolves in the process CWD.
    assert len(urls) == 3
    assert len(expected) == 12 and len(set(expected)) == 12
    assert all(path.is_file() for path in expected)
    assert len([path for path in expected if path.is_file()]) == 12
    cwd_names = [process_dir / path.name for path in expected]
    assert len([path for path in cwd_names if path.exists()]) == 0
    assert all(sum(name.count(sep) for sep in ("/", "\\")) == 0
               for name in (jobs[url]["filename"] for url in urls))

    response = client.post(
        "/api/sites/row507/jobs/bulk_delete",
        json={"urls": urls, "force_cleanup": True},
    )
    body = response.get_json()
    remaining = [path for path in expected if path.exists()]
    removed = len(expected) - len(remaining)
    event_lines = [message for kind, message, _extra in runner.events
                   if kind == "queue_op"]

    assert response.status_code == 200
    assert body["affected"] == 3
    assert len(event_lines) == 1, runner.events
    assert removed > 0, (
        f"force cleanup removed 0 of 12 artifacts; response reported "
        f"{body.get('cleanup_count')}, event={event_lines}, remaining={remaining}")
    assert remaining == []
    assert body["cleanup_count"] == removed == 12
    assert event_lines == ["Bulk deleted 3/3 URLs (+ 12 partial-file cleanups)"]


def test_bulk_delete_without_force_cleanup_has_no_cleanup_verdict(
    tmp_path, monkeypatch,
):
    download_dir = tmp_path / "site-downloads"
    download_dir.mkdir()
    url = "https://example.test/row507/no-force"
    filename = "not-forced.mp4"
    final_path = download_dir / filename
    part = sc.claim(final_path, sc.job_identity(url))
    part.write_bytes(b"partial")
    artifacts = _partial_artifacts(final_path)
    artifacts[1].write_text("validator", encoding="utf-8")
    artifacts[2].write_text("checkpoint", encoding="utf-8")
    assert len(artifacts) == 4 and all(path.is_file() for path in artifacts)

    runner = _BulkDeleteRunner(
        download_dir, {url: {"filename": filename, "status": "pending"}})
    client = _bulk_delete_client(monkeypatch, runner)
    response = client.post(
        "/api/sites/row507/jobs/bulk_delete", json={"urls": [url]})
    body = response.get_json()

    assert response.status_code == 200
    assert all(path.is_file() for path in artifacts)
    assert len([path for path in artifacts if path.is_file()]) == 4
    assert "cleanup_count" not in body, (
        "force_cleanup=false reported a cleanup verdict even though cleanup "
        "was not requested")
    assert [message for kind, message, _extra in runner.events
            if kind == "queue_op"] == ["Bulk deleted 1/1 URLs"]


def test_absent_partial_artifacts_report_zero_cleanups(tmp_path, monkeypatch):
    download_dir = tmp_path / "site-downloads"
    download_dir.mkdir()
    url = "https://example.test/row507/never-started"
    filename = "never-started.mp4"
    artifacts = _partial_artifacts(download_dir / filename)
    assert len(artifacts) == 4
    assert len([path for path in artifacts if path.exists()]) == 0

    runner = _BulkDeleteRunner(
        download_dir, {url: {"filename": filename, "status": "pending"}})
    client = _bulk_delete_client(monkeypatch, runner)
    response = client.post(
        "/api/sites/row507/jobs/bulk_delete",
        json={"urls": [url], "force_cleanup": True},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["affected"] == 1
    assert body["cleanup_count"] == 0, (
        "a nonempty display filename was counted as a cleanup even though "
        "all four artifact paths were genuinely absent")
    assert [message for kind, message, _extra in runner.events
            if kind == "queue_op"] == ["Bulk deleted 1/1 URLs"]


def test_an_unmeasurable_part_is_not_counted_or_left_ownerless(
    tmp_path, monkeypatch,
):
    download_dir = tmp_path / "site-downloads"
    download_dir.mkdir()
    url = "https://example.test/row507/unmeasurable"
    filename = "unmeasurable.mp4"
    final_path = download_dir / filename
    part = sc.claim(final_path, sc.job_identity(url))
    part.write_bytes(b"must remain claimed")
    owner = sc.owner_path_for(part)
    assert part.is_file() and owner.is_file()
    assert len([path for path in (part, owner) if path.is_file()]) == 2

    real_lstat = pathlib.Path.lstat

    def _part_cannot_be_measured(path):
        if path == part:
            raise PermissionError("fixture refuses part metadata")
        return real_lstat(path)

    monkeypatch.setattr(pathlib.Path, "lstat", _part_cannot_be_measured)
    runner = _BulkDeleteRunner(
        download_dir, {url: {"filename": filename, "status": "pending"}})
    client = _bulk_delete_client(monkeypatch, runner)
    response = client.post(
        "/api/sites/row507/jobs/bulk_delete",
        json={"urls": [url], "force_cleanup": True},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["cleanup_count"] == 0, (
        "an unmeasurable part was reported cleaned even though its bytes remain")
    assert part.is_file()
    assert owner.is_file(), (
        "the claim was released while the part could not be measured, leaving "
        "the surviving bytes ownerless")


def test_row507_transform_control_only_imports_the_route():
    from bulk_downloader import app_sites_queue

    assert app_sites_queue is not None


# ── Row 533: a mint that cannot make good on itself must not survive ───────
#
# claim() publishes the .owner FIRST and moves the foreign bytes SECOND, so any
# failure between the two left a claim standing over bytes it had not cleared.
# claim() is idempotent for one identity, so the very next attempt by the SAME
# job took the reclaim branch -- which never re-measures -- resumed over another
# scene's bytes, and promoted the splice as done under the right title. That is
# the 2026-08-29 corruption, manufactured by the fix written to prevent it.
#
# The reachable trigger needs no error at all: a SIGKILL or a deploy restart in
# the window between _create_owner and os.replace leaves exactly this state.

def test_a_failed_set_aside_leaves_no_claim_behind(tmp_path, monkeypatch):
    """RED against the fix as first shipped."""
    final = tmp_path / "Interrupted.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(b"\xaa" * 4096)
    owner = sc.owner_path_for(staging)
    assert not owner.exists(), "precondition: the bytes are ownerless"

    def boom(_path):
        raise sc.StagingUnavailable("synthetic: the rename could not be made")

    monkeypatch.setattr(sc, "_set_aside_unowned_bytes", boom)
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, _ident(_JOB_A))

    assert not owner.exists(), (
        "the claim survived a set-aside it could not perform, so the next "
        "attempt by this same job takes the reclaim branch -- which never "
        "re-measures -- and resumes over 4096 foreign bytes")
    assert staging.read_bytes() == b"\xaa" * 4096, (
        "the foreign bytes were altered by a call that failed")


def test_the_retry_after_a_failed_set_aside_still_refuses_to_adopt(tmp_path, monkeypatch):
    """The consequence, end to end: because the mint unwound, the RETRY is a
    fresh mint and measures the bytes again instead of inheriting a claim."""
    final = tmp_path / "Interrupted.mp4"
    staging = sc.staging_path_for(final)
    staging.write_bytes(b"\xaa" * 4096)

    calls = {"n": 0}
    real = sc._set_aside_unowned_bytes

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sc.StagingUnavailable("synthetic: transient rename failure")
        return real(path)

    monkeypatch.setattr(sc, "_set_aside_unowned_bytes", flaky)
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, _ident(_JOB_A))

    got = sc.claim(final, _ident(_JOB_A))
    assert calls["n"] == 2, (
        "the retry took the reclaim branch instead of minting again, so the "
        "foreign bytes were never measured a second time")
    assert not got.exists() or got.read_bytes() == b"", (
        "the retry adopted the foreign bytes")


def test_an_ordinary_mint_is_unaffected(tmp_path):
    """POSITIVE CONTROL: the unwind must not fire on the happy path."""
    final = tmp_path / "Clean.mp4"
    staging = sc.claim(final, _ident(_JOB_A))
    assert sc.owner_path_for(staging).is_file(), (
        "the unwind removed a claim that was minted successfully")
