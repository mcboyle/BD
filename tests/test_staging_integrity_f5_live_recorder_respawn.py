"""staging-integrity F5 -- a restart mid-recording must record, not silently finish.

watch() fixed rec.output_path once; after a restart _load_state() re-arms the
watch as pending and the respawn handed ffmpeg/streamlink that SAME path. Run
as _spawn_recording runs them (stdio DEVNULL, no -y/--force) both exit 0
without writing (ev/s32: existing path -> 21 B, rc 0), and
_check_recording_health mapped rc 0/1 -> "finished", so the rest of the live
session was lost with no error.

The fake backend below has exactly that measured contract: an existing output
is left untouched and the process exits 0; an absent one is written.

Uses setup_method (not autouse fixtures) per tests/test_v3_43_62_live_recorder.py.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# An ordinary module test: its subject is the module under test, not the
# tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

NO_NEW_BYTES = "no new bytes"
PRIOR = b"PRIOR-RECORDING-BYTES"
FRESH = b"\x47" * 188 * 8   # a few MPEG-TS packets


class _Egress:
    proxy_url = None

    def subprocess_env(self):
        return dict(os.environ)

    def close(self):
        pass


class _Proc:
    def __init__(self, rc):
        self.pid = 4242
        self._rc = rc

    def poll(self):
        return self._rc


class TestF5RespawnAfterRestart:
    def setup_method(self):
        from bulk_downloader import live_recorder as lr
        lr._reset_for_tests()
        lr._reset_backend_cache_for_tests()
        self.tmp = tempfile.mkdtemp(prefix="f5-respawn-")
        self.out = str(Path(self.tmp) / "out")
        self.lr = lr
        self._saved = {n: getattr(lr, n) for n in
                       ("is_available", "preferred_backend", "_is_room_live",
                        "_disk_check")}
        self._saved_popen = lr.subprocess.Popen
        lr.is_available = lambda: True
        lr.preferred_backend = lambda: "streamlink"
        lr._is_room_live = lambda *a, **k: True
        lr._disk_check = None
        self.argv = []

    def teardown_method(self):
        lr = self.lr
        for n, v in self._saved.items():
            setattr(lr, n, v)
        lr.subprocess.Popen = self._saved_popen
        lr._reset_for_tests()
        lr._reset_backend_cache_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _install_backend(self, write=True, rc=0):
        """streamlink as measured in ev/s32: never overwrites an existing -o."""
        lr = self.lr

        def popen(cmd, **kwargs):
            self.argv.append(list(cmd))
            out = cmd[cmd.index("-o") + 1]
            if write and not os.path.exists(out):
                Path(out).write_bytes(FRESH)
            return _Proc(rc)

        lr.subprocess.Popen = popen
        lr._egress_prepare = lambda site: _Egress()

    def _restart_mid_recording(self):
        """watch -> recording with bytes on disk -> process restart."""
        lr = self.lr
        lr.init(self.tmp)
        res = lr.watch("https://chaturbate.com/somemodel/", self.out)
        assert res["ok"], res
        rid, prior_path = res["recording_id"], res["output_path"]
        with lr._lock:
            rec = lr._recordings[rid]
            rec.state = "recording"
            rec.bytes_written = len(PRIOR)
        Path(prior_path).write_bytes(PRIOR)
        lr._save_state()
        lr._reset_for_tests()
        lr.init(self.tmp)
        rec = lr._recordings[rid]
        assert rec.state == "pending"
        assert rec.output_path == prior_path   # what the respawn inherits
        return rid, rec, prior_path

    def test_respawn_after_restart_records_into_a_fresh_path(self):
        lr = self.lr
        rid, rec, prior_path = self._restart_mid_recording()
        self._install_backend(write=True, rc=0)
        lr._process_recording(rid, rec)
        assert rec.state == "recording", rec.last_error
        spawned = self.argv[-1][self.argv[-1].index("-o") + 1]
        assert spawned != prior_path, (
            f"F5: respawn reused the existing output {prior_path}; the backend "
            f"exits 0 without writing it")
        assert Path(prior_path).read_bytes() == PRIOR, "prior recording clobbered"
        assert rec.output_path == spawned
        assert rec.bytes_written == 0, (
            "stale bytes_written from the previous file would read the new "
            "file as not growing and finalize it")
        lr._check_recording_health(rid, rec)
        assert rec.state == "finished", rec.last_error
        assert rec.bytes_written == len(FRESH)

    def test_exit_0_without_new_bytes_is_not_finished(self):
        lr = self.lr
        rid, rec, _prior = self._restart_mid_recording()
        self._install_backend(write=False, rc=0)
        lr._process_recording(rid, rec)
        assert rec.state == "recording", rec.last_error
        lr._check_recording_health(rid, rec)
        assert rec.state == "failed" and NO_NEW_BYTES in (rec.last_error or ""), (
            f"F5: rc 0 with no new bytes reported state={rec.state!r} "
            f"last_error={rec.last_error!r}; want failed / {NO_NEW_BYTES!r}")

    def test_neg_control_exit_0_with_new_bytes_is_finished(self):
        lr = self.lr
        lr.init(self.tmp)
        res = lr.watch("https://chaturbate.com/othermodel/", self.out)
        rid = res["recording_id"]
        rec = lr._recordings[rid]
        self._install_backend(write=True, rc=1)
        lr._process_recording(rid, rec)
        assert self.argv[-1][self.argv[-1].index("-o") + 1] == res["output_path"]
        lr._check_recording_health(rid, rec)
        assert rec.state == "finished" and NO_NEW_BYTES not in (rec.last_error or ""), (
            f"NEG-CONTROL: a backend that wrote {len(FRESH)} B and exited 1 "
            f"must stay finished; got state={rec.state!r} "
            f"last_error={rec.last_error!r}")
