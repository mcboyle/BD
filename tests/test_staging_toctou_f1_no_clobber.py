"""staging-toctou-f1 -- a second job is handed a finished download's name and overwrites it.

MEASURED on origin/main 2b7ca835 (HIGH-F1, staging-integrity sweep):

    job A holds X.part, finishes, ``os.replace(X.part, X)``, releases its claim
    job B ``reserve(X)``: ``X.exists()`` said False a moment earlier, so B goes
          on to ``claim(X)``, finds no owner, and is handed X itself
    job B promotes with ``os.replace`` over A's file -- A's bytes are gone and
          both history rows say ``done`` at X

The natural race (two barrier-started threads, no injected hook) lost A's file
in 500 of 500 trials. The segmented-failure branch made it worse: a failed
stream job unlinked whatever sat at its final name, including a file another
job had promoted there.

WHAT THIS GATE ASSERTS
  1. claim() re-checks the FINAL name once the ``.part`` claim is held, so a
     second job is handed ``X_1`` and A's bytes survive -- forced interleave
     and the 500-trial natural race (A-LOST 0).
  2. The promote never overwrites: an occupied final name diverts the bytes
     to the next free name, on the hardlink path AND on the no-hardlink
     fallback, and the history row names where the bytes actually are.
  3. The segmented-failure branch deletes only a file this job wrote.
  NEGATIVE CONTROLS: an uncontended job still lands at X; a failed segmented
  job still removes its own partial output.

The interleaving is forced through the module's own seams (``Path.exists`` on
the candidate, ``staging_claim.claim``/``reserve``), never by sleeps.
"""
from __future__ import annotations

import errno
import logging
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bulk_downloader import runner_transport as transport
from bulk_downloader import staging_claim as sc
from bulk_downloader.runner_transport import TransportMixin

BD_GATE_SCOPE = "module"

DIAG = "STAGING-TOCTOU-F1"
A_BYTES = b"A" * 4096
B_BYTES = b"B" * 100
ID_A = sc.job_identity("https://a.example.invalid/scene/a1")
ID_B = sc.job_identity("https://a.example.invalid/scene/b2")


def _leftover_claims(d):
    return sorted(p.name for p in Path(d).iterdir() if p.name.endswith(sc.OWNER_SUFFIX))


def _promote(staging, final, identity):
    """What the transport does with a finished ``.part``: the production promote."""
    n = Path(staging).stat().st_size
    return TransportMixin._promote_or_abort(staging, final, n, n, identity=identity)


# ── 1. claim() re-checks the final name once the claim is held ───────────────

def _s01(tmp_path, mode):
    X = tmp_path / "Scene.mp4"
    _, st_a = sc.reserve(X, ID_A)
    st_a.write_bytes(A_BYTES)
    fired = []

    def promote_a():
        os.replace(st_a, X)
        fired.append(sc.release(st_a, ID_A))

    if mode == "control":
        promote_a()
        f_b, st_b = sc.reserve(X, ID_B)
    else:
        real_exists = Path.exists

        def hooked(self, *a, **k):
            r = real_exists(self, *a, **k)
            if self == X and not fired:      # B's probe answered "absent"
                promote_a()
            return r

        Path.exists = hooked
        try:
            f_b, st_b = sc.reserve(X, ID_B)
        finally:
            Path.exists = real_exists
    assert fired == [True], f"precondition: A did not promote and release ({fired})"
    st_b.write_bytes(B_BYTES)
    landed = _promote(st_b, f_b, ID_B)
    return X, f_b, landed


@pytest.mark.parametrize("mode", ["race", "control"])
def test_s01_second_job_never_gets_a_finished_name(tmp_path, mode):
    X, f_b, landed = _s01(tmp_path, mode)
    assert X.read_bytes() == A_BYTES, (
        f"{DIAG} A-LOST ({mode}): job B was handed {f_b.name} and promoted over "
        f"A's finished file; X now holds {len(X.read_bytes())} bytes")
    assert f_b.name == "Scene_1.mp4" and landed == f_b, (f_b, landed)
    assert landed.read_bytes() == B_BYTES
    assert _leftover_claims(tmp_path) == []


def _natural_race(root, trials, hold):
    lost = other_name = refused = 0
    for i in range(trials):
        d = root / str(i)
        d.mkdir()
        X = d / "Scene.mp4"
        _, st_a = sc.reserve(X, ID_A)
        st_a.write_bytes(A_BYTES)
        bar = threading.Barrier(2)
        out = {}

        def run_a():
            bar.wait()
            if not hold:
                os.replace(st_a, X)
                sc.release(st_a, ID_A)

        def run_b():
            bar.wait()
            try:
                out["b"] = sc.reserve(X, ID_B)
            except Exception as e:  # noqa: BLE001 -- counted, never swallowed
                out["e"] = e

        ts = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        if "e" in out:
            refused += 1
            continue
        f_b, st_b = out["b"]
        if f_b == X:
            st_b.write_bytes(B_BYTES)
            os.replace(st_b, f_b)            # BASE's promote, kept raw on purpose
            sc.release(st_b, ID_B)
            if X.read_bytes()[:1] == b"B":
                lost += 1
        else:
            other_name += 1
    return lost, other_name, refused


@pytest.mark.parametrize("hold", [False, True], ids=["race", "control-hold"])
def test_s01b_natural_race_loses_nothing(tmp_path, hold):
    trials = 500
    lost, other_name, refused = _natural_race(tmp_path, trials, hold)
    assert lost == 0, (
        f"{DIAG} natural race: A's finished file overwritten in {lost} of {trials} "
        f"trials (B-got-other-name {other_name}, B-refused {refused})")
    assert other_name == trials and refused == 0, (other_name, refused)


def test_a_claim_over_an_occupied_final_name_is_refused_and_dropped(tmp_path):
    X = tmp_path / "Taken.mp4"
    X.write_bytes(A_BYTES)
    with pytest.raises(sc.StagingClaimedByAnotherJob) as ei:
        sc.claim(X, ID_B)
    assert "already holds a finished file" in str(ei.value)
    assert _leftover_claims(tmp_path) == [], "the refused claim must not be left behind"
    assert X.read_bytes() == A_BYTES


# ── 2. the promote never overwrites ──────────────────────────────────────────

def _held_then_occupied(tmp_path, name="Scene.mp4"):
    """B holds X.part with finished bytes; a file then appears at X."""
    X = tmp_path / name
    f_b, st_b = sc.reserve(X, ID_B)
    assert f_b == X, "precondition: the uncontended reservation is X itself"
    st_b.write_bytes(B_BYTES)
    X.write_bytes(A_BYTES)
    return X, st_b


def _assert_diverted(tmp_path, X, st_b, landed):
    assert X.read_bytes() == A_BYTES, (
        f"{DIAG} promote overwrote an existing final file: X holds "
        f"{len(X.read_bytes())} bytes, expected A's {len(A_BYTES)}")
    assert landed == tmp_path / "Scene_1.mp4", landed
    assert landed.read_bytes() == B_BYTES
    assert not st_b.exists(), "the staging file outlived its promote"
    assert _leftover_claims(tmp_path) == []
    assert transport._take_diverted_promote(ID_B, X) == landed, (
        "the diverted path was not handed back for the history row")


def test_promote_diverts_instead_of_overwriting(tmp_path):
    X, st_b = _held_then_occupied(tmp_path)
    landed = _promote(st_b, X, ID_B)
    _assert_diverted(tmp_path, X, st_b, landed)


def test_promote_without_hardlinks_still_never_overwrites(tmp_path, monkeypatch):
    calls = []

    def _no_link(src, dst):
        calls.append(dst)
        raise OSError(errno.EPERM, "Operation not permitted (test: no hardlinks)")

    monkeypatch.setattr(sc, "_hardlink", _no_link, raising=False)
    X, st_b = _held_then_occupied(tmp_path)
    landed = _promote(st_b, X, ID_B)
    _assert_diverted(tmp_path, X, st_b, landed)
    assert calls, "precondition: the no-hardlink fallback was not exercised"

    # Control on the same fallback: an uncontended promote lands at X.
    Y = tmp_path / "Free.mp4"
    _, st_y = sc.reserve(Y, ID_A)
    st_y.write_bytes(A_BYTES)
    assert _promote(st_y, Y, ID_A) == Y and Y.read_bytes() == A_BYTES
    assert not st_y.exists()


def test_uncontended_promote_lands_at_the_reserved_name(tmp_path):
    X = tmp_path / "Scene.mp4"
    f_b, st_b = sc.reserve(X, ID_B)
    st_b.write_bytes(B_BYTES)
    assert _promote(st_b, f_b, ID_B) == X
    assert X.read_bytes() == B_BYTES and not st_b.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Scene.mp4"]


def test_a_crash_between_link_and_unlink_is_finished_not_duplicated(tmp_path):
    X = tmp_path / "Scene.mp4"
    _, st_b = sc.reserve(X, ID_B)
    st_b.write_bytes(B_BYTES)
    os.link(st_b, X)                       # the crash: linked, staging not yet unlinked
    landed = _promote(st_b, X, ID_B)
    assert landed == X and X.read_bytes() == B_BYTES
    assert not st_b.exists(), f"{DIAG}: the half-finished promote left its staging file"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Scene.mp4"]


# ── _do_download: history names the real path; the segmented branch ─────────

PAGE_URL = "https://scenes.example.invalid/video/toctou-f1/"


class _Runner(TransportMixin):
    def __init__(self, tmp_path):
        self.site_id = "toctouf1"
        self.config = {"name": "ToctouF1", "use_http_dl": True, "verify_hash": False,
                       "verify_integrity": True, "skip_if_exists": False,
                       "learned": {"download": {"row_selectors": ["video source"],
                                                "url_attribute": "src"}}}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("staging-toctou-f1")
        self.dl_dir = tmp_path
        self.hls_calls = []
        self.hls_effect = None

    def _probe_for_higher_tier(self, url, **kw):
        return url

    def _build_mirror_urls(self, url):
        return []

    def _history_title_fields(self, _url):
        return {"title": "Toctou Scene"}

    def _verify_integrity_or_quarantine(self, page_url, final_path, filename, downloaded_size):
        return True, False, ""

    def _probe_video_height(self, _path):
        return 0                       # height unknown: no tier rename

    def _embed_metadata_if_mp4(self, *a, **kw):
        pass

    def _size_on_disk_after_tagging(self, _path, size):
        return size

    def _update_job(self, url, status, msg="", **kw):
        self.jobs[url] = {"status": status, "msg": msg, **kw}

    def log_event(self, *a, **kw):
        pass

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.hls_calls.append(Path(output_path))
        if self.hls_effect:
            self.hls_effect(Path(output_path))
        return _hls.DownloadResult(ok=False, error="ffmpeg_failed",
                                   error_detail="test: stream failed")


def _drive(tmp_path, monkeypatch, media_url, runner=None):
    rows = []
    monkeypatch.setattr(transport, "db_log", lambda *a, **k: rows.append((a, k)))
    runner = runner or _Runner(tmp_path)
    loc = MagicMock()
    loc.get_attribute.side_effect = lambda a: media_url if a == "src" else None
    best = {"locator": loc, "score": 0, "_via_learned": True,
            "_learned_sel": "video source", "text": media_url}
    page = MagicMock()
    page.url = PAGE_URL
    runner._do_download(page, MagicMock(), PAGE_URL, best, tmp_path, "auto")
    return runner, rows


def _rows_with_status(rows, status):
    return [(a, k) for a, k in rows if len(a) > 3 and a[3] == status]


def test_history_names_the_path_the_bytes_were_promoted_to(tmp_path, monkeypatch):
    seen = {}

    def _http(_page_url, _page, _ctx, _file_url, _attempts, final_path):
        # Another writer lands a finished file at the reserved name mid-transfer;
        # the transfer then finishes and runs the production promote.
        final_path.write_bytes(A_BYTES)
        stage = sc.staging_path_for(final_path)
        stage.write_bytes(B_BYTES)
        seen["reserved"] = final_path
        _promote(stage, final_path, sc.job_identity(PAGE_URL))
        return len(B_BYTES), len(B_BYTES)

    runner = _Runner(tmp_path)
    runner._run_http_attempts_with_resume = _http
    runner, rows = _drive(tmp_path, monkeypatch, "https://cdn.example.invalid/v/toctou.mp4", runner)
    X = seen["reserved"]
    X1 = X.with_name(f"{X.stem}_1{X.suffix}")
    assert X.read_bytes() == A_BYTES, f"{DIAG}: the transfer overwrote the file at {X.name}"
    assert X1.read_bytes() == B_BYTES
    done = _rows_with_status(rows, "done")
    assert len(done) == 1, f"precondition: exactly one done row, got {rows}"
    a, k = done[0]
    assert a[4] == X1.name and k.get("file_path") == str(X1), (
        f"{DIAG}: history names {a[4]!r} / {k.get('file_path')!r} but the bytes are at {X1}")
    assert runner.jobs[PAGE_URL]["filename"] == X1.name


def test_history_control_uncontended_names_the_reserved_path(tmp_path, monkeypatch):
    seen = {}

    def _http(_page_url, _page, _ctx, _file_url, _attempts, final_path):
        stage = sc.staging_path_for(final_path)
        stage.write_bytes(B_BYTES)
        seen["reserved"] = final_path
        _promote(stage, final_path, sc.job_identity(PAGE_URL))
        return len(B_BYTES), len(B_BYTES)

    runner = _Runner(tmp_path)
    runner._run_http_attempts_with_resume = _http
    runner, rows = _drive(tmp_path, monkeypatch, "https://cdn.example.invalid/v/toctou.mp4", runner)
    X = seen["reserved"]
    a, k = _rows_with_status(rows, "done")[0]
    assert a[4] == X.name and k.get("file_path") == str(X) and X.read_bytes() == B_BYTES


MANIFEST = "https://cdn.example.invalid/hls/toctou/master.m3u8"


def test_segmented_failure_after_the_race_keeps_a_file(tmp_path, monkeypatch):
    """A promotes X between B's probe and B's claim; B's stream then fails."""
    real_claim = sc.claim
    fired = []

    def _claim(final_path, identity, **kw):
        if not fired:
            fired.append(Path(final_path))
            Path(final_path).write_bytes(A_BYTES)   # A's promote + release, just now
        return real_claim(final_path, identity, **kw)

    monkeypatch.setattr(sc, "claim", _claim)
    runner, rows = _drive(tmp_path, monkeypatch, MANIFEST)
    assert fired and runner.hls_calls, f"precondition: race not driven ({fired}, {runner.hls_calls})"
    X = fired[0]
    assert X.exists() and X.read_bytes() == A_BYTES, (
        f"{DIAG}: a failed segmented job deleted A's finished file {X.name} "
        f"(it was handed {runner.hls_calls[0].name})")
    assert runner.hls_calls[0] != X
    assert _rows_with_status(rows, "needs_review")


def test_segmented_failure_never_unlinks_a_file_it_did_not_write(tmp_path, monkeypatch):
    """The name was free at reserve time; a file appeared before the stream began."""
    real_reserve = sc.reserve
    occupied = []

    def _reserve(final_path, identity):
        got = real_reserve(final_path, identity)
        got[0].write_bytes(A_BYTES)
        occupied.append(got[0])
        return got

    monkeypatch.setattr(sc, "reserve", _reserve)
    runner, _rows = _drive(tmp_path, monkeypatch, MANIFEST)
    assert occupied and runner.hls_calls == occupied, (occupied, runner.hls_calls)
    assert occupied[0].exists() and occupied[0].read_bytes() == A_BYTES, (
        f"{DIAG}: the segmented-failure branch unlinked {occupied[0].name}, "
        "which this job never wrote")


def test_segmented_failure_control_removes_its_own_partial_output(tmp_path, monkeypatch):
    runner = _Runner(tmp_path)
    runner.hls_effect = lambda out: out.write_bytes(b"partial-mux")
    runner, rows = _drive(tmp_path, monkeypatch, MANIFEST, runner)
    assert runner.hls_calls, "precondition: the segmented branch did not run"
    assert not runner.hls_calls[0].exists(), "own partial output must still be removed"
    assert _rows_with_status(rows, "needs_review")
    assert _leftover_claims(tmp_path) == []


def test_segmented_failure_never_unlinks_after_losing_its_claim(tmp_path, monkeypatch):
    """The name was free when the stream began; this job's claim was then lost
    and another job's finished file landed at X before the stream failed."""
    landed = []

    def _lose_claim_then_a_lands(out):
        assert sc.release(sc.staging_path_for(out), sc.job_identity(PAGE_URL))
        out.write_bytes(A_BYTES)
        landed.append(out)

    runner = _Runner(tmp_path)
    runner.hls_effect = _lose_claim_then_a_lands
    runner, _rows = _drive(tmp_path, monkeypatch, MANIFEST, runner)
    assert landed, "precondition: the segmented branch did not run"
    assert landed[0].exists() and landed[0].read_bytes() == A_BYTES, (
        f"{DIAG}: the segmented-failure branch unlinked {landed[0].name} "
        "after this job no longer held its claim")

def _hls_spawn_probe(monkeypatch):
    from bulk_downloader import hls_downloader as hls
    spawned = []

    def _no_spawn(cmd, **kw):
        spawned.append(cmd)
        raise RuntimeError("probe: ffmpeg spawn reached")

    monkeypatch.setattr(hls, "_find_ffmpeg", lambda: "/probe/ffmpeg")
    monkeypatch.setattr(hls.subprocess, "Popen", _no_spawn)
    return hls, spawned


def test_hls_download_refuses_an_existing_output_instead_of_removing_it(tmp_path, monkeypatch):
    hls, spawned = _hls_spawn_probe(monkeypatch)
    out = tmp_path / "X.mp4"
    out.write_bytes(b"A-finished-bytes")
    res = hls.download(MANIFEST, str(out))
    assert out.exists() and out.read_bytes() == b"A-finished-bytes", (
        f"{DIAG}: hls_downloader.download removed a file it did not create at {out}")
    assert res.ok is False and res.error == "output_exists", (
        f"{DIAG}: expected output_exists refusal, got {res.error!r}")
    assert spawned == [], f"{DIAG}: ffmpeg spawned over an existing output"


def test_hls_download_control_free_output_reaches_ffmpeg(tmp_path, monkeypatch):
    hls, spawned = _hls_spawn_probe(monkeypatch)
    out = tmp_path / "X.mp4"
    res = hls.download(MANIFEST, str(out))
    assert len(spawned) == 1 and res.error == "ffmpeg_spawn_failed", res.error
