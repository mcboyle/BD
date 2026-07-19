"""MOD-7 cut 2 (v3.66.791) -- Nuitka eval harness: measure + decide.

Cut 2's binding output is a size/startup/correctness evaluation of a full
--standalone compile. That compile is disk- and time-heavy (several GB transient
+ 30-90 min), so it runs on a BUILD HOST, not in the sandbox -- the same
instrument-here / measure-on-the-real-host split as capture.sh. This module is
the instrument: pure measurement + decision functions that the build host feeds
a real dist, plus a run_eval that boots the compiled binary and probes it.

These tests exercise ONLY the pure logic (size rollup, startup-command
construction, correctness probe spec, and the adopt/retain verdict against
thresholds), all sandbox-safe. The one part that needs a real compiled artifact
-- run_eval actually launching the binary -- is asserted to return an HONEST
"cannot evaluate" when no dist is present, never a false PASS (the
gate-degrades-to-skip footgun: a harness with no artifact must say UNKNOWN, not
green).
"""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    from importlib import import_module
    return import_module("tools.nuitka_eval")


# --------------------------------------------------------------------------
# size measurement
# --------------------------------------------------------------------------

def test_measure_size_rolls_up_a_tree(tmp_path):
    ne = _load()
    (tmp_path / "a").write_bytes(b"x" * 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b").write_bytes(b"y" * 2000)
    n = ne.measure_size(str(tmp_path))
    assert n == 3000, f"expected 3000 bytes, got {n}"


def test_measure_size_missing_dir_is_unknown(tmp_path):
    """A size over a non-existent dist is not zero -- zero would read as 'tiny
    binary, great'. It must be None/unknown."""
    ne = _load()
    assert ne.measure_size(str(tmp_path / "does_not_exist")) is None


def test_human_size_is_readable():
    ne = _load()
    assert ne.human_size(0) == "0 B"
    assert ne.human_size(1536).endswith("KB")
    assert ne.human_size(5 * 1024 * 1024).endswith("MB")


# --------------------------------------------------------------------------
# startup command over a standalone dist
# --------------------------------------------------------------------------

def test_startup_command_targets_the_dist_binary(tmp_path):
    ne = _load()
    # a standalone dist looks like <name>.dist/<name>.bin (Linux)
    dist = tmp_path / "downloader_ui.dist"
    dist.mkdir()
    binp = dist / "downloader_ui.bin"
    binp.write_bytes(b"\x7fELF")
    binp.chmod(0o755)
    argv = ne.startup_command(str(dist), port=5599)
    assert isinstance(argv, list)
    assert argv[0] == str(binp), "must launch the compiled binary, not python"
    # the chosen port reaches the app through its documented env knob (BD_PORT)
    assert ne.startup_env(port=5599).get("BD_PORT") == "5599"
    assert ne.startup_env(port=5599).get("BD_HOST") == "127.0.0.1"

def test_startup_command_missing_binary_raises(tmp_path):
    ne = _load()
    with pytest.raises((FileNotFoundError, ValueError)):
        ne.startup_command(str(tmp_path / "absent.dist"), port=5599)


# --------------------------------------------------------------------------
# correctness probes
# --------------------------------------------------------------------------

def test_probes_are_a_real_spec():
    ne = _load()
    probes = ne.correctness_probes("http://127.0.0.1:5599")
    assert probes, "must define at least one correctness probe"
    for p in probes:
        assert p["url"].startswith("http://127.0.0.1:5599")
        assert "expect_status" in p
        # each probe names WHAT it proves, so a failure is diagnosable
        assert p.get("name")


def test_probes_cover_spa_and_an_api():
    """Correctness is not 'it started' -- the frozen binary must serve the SPA
    and answer a real API, since those exercise the bundled data dirs and the
    dynamic-import backends the config declares."""
    ne = _load()
    urls = " ".join(p["url"] for p in ne.correctness_probes("http://h:1"))
    assert "/api/" in urls, "no API probe -- startup alone is not correctness"


# --------------------------------------------------------------------------
# verdict / decision framework
# --------------------------------------------------------------------------

def test_verdict_adopt_when_beats_thresholds():
    ne = _load()
    v = ne.verdict(size_bytes=40 * 1024 * 1024, startup_s=1.2,
                   correctness_pass=True,
                   baseline=ne.PYINSTALLER_BASELINE)
    assert v["decision"] in ("adopt", "adopt-candidate")
    assert v["correctness_pass"] is True


def test_verdict_retain_when_correctness_fails():
    """Correctness is a HARD gate: a smaller/faster binary that doesn't serve
    correctly is not adoptable, full stop."""
    ne = _load()
    v = ne.verdict(size_bytes=1 * 1024 * 1024, startup_s=0.1,
                   correctness_pass=False, baseline=ne.PYINSTALLER_BASELINE)
    assert v["decision"] == "retain-pyinstaller"


def test_verdict_retain_when_no_material_win():
    """The roadmap's own read is the value case is weak for BD's I/O-bound use.
    So a Nuitka build that merely ties pyinstaller retains pyinstaller -- parity
    is not a reason to add a second packager + toolchain."""
    ne = _load()
    base = ne.PYINSTALLER_BASELINE
    v = ne.verdict(size_bytes=base["size_bytes"], startup_s=base["startup_s"],
                   correctness_pass=True, baseline=base)
    assert v["decision"] == "retain-pyinstaller"


def test_verdict_is_unknown_on_missing_measurements():
    ne = _load()
    v = ne.verdict(size_bytes=None, startup_s=None, correctness_pass=None,
                   baseline=ne.PYINSTALLER_BASELINE)
    assert v["decision"] == "unknown"


# --------------------------------------------------------------------------
# run_eval honesty: no dist -> UNKNOWN, never a false green
# --------------------------------------------------------------------------

def test_run_eval_without_dist_is_unknown_not_pass(tmp_path):
    ne = _load()
    res = ne.run_eval(str(tmp_path / "no_such.dist"))
    assert res["decision"] == "unknown"
    assert res.get("reason"), "must state WHY it could not evaluate"
    # it must not claim any measurement it didn't make
    assert res.get("size_bytes") is None
    assert res.get("startup_s") is None


def test_run_eval_end_to_end_against_a_fake_dist(tmp_path):
    """Exercise the RUNTIME path -- boot, cold-start poll, correctness probes,
    verdict -- without a real Nuitka compile. A fake .dist whose 'binary' is a
    tiny script that serves / (200) and /api/health (200) stands in for the
    compiled artifact, so the harness's boot+probe logic (the part that
    otherwise only runs on the build host) is covered in-sandbox."""
    ne = _load()
    import socket

    # pick a free port
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    dist = tmp_path / "downloader_ui.dist"
    dist.mkdir()
    binp = dist / "downloader_ui.bin"
    binp.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'ok')\n"
        "    def log_message(self, *a): pass\n"
        "port = int(os.environ.get('BD_PORT', '5599'))\n"
        "HTTPServer(('127.0.0.1', port), H).serve_forever()\n"
    )
    binp.chmod(0o755)
    # give the dist some bytes so the size measurement is non-trivial
    (dist / "libpython.so").write_bytes(b"\0" * 4096)

    res = ne.run_eval(str(dist), port=port, boot_timeout=15.0)
    # it actually booted and probed: a real decision, correctness passed
    assert res["decision"] in ("adopt-candidate", "retain-pyinstaller")
    assert res["correctness_pass"] is True
    assert res["startup_s"] is not None and res["startup_s"] >= 0
    assert res["size_bytes"] >= 4096


def test_runbook_names_the_build_host_steps():
    ne = _load()
    rb = ne.runbook()
    for token in ("build_nuitka", "--standalone", "nuitka_eval", "build host"):
        assert token in rb, f"runbook missing {token!r}"
