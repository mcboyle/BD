"""Tests for scripts/deploy.sh (F0.1 operator deploy).

Zero-arg test functions per run_tests.py conventions; repo root via __file__.
The real script is driven over a scratch install dir with a shimmed `curl` on
PATH and a no-op restart (BD_RESTART_CMD=true), so no real service is touched.
Backend check is skipped (no venv off-host).
"""
import hashlib
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "deploy.sh"


def _make_zip(dirpath):
    """A tiny real release-shaped zip; returns (zip_path, sha256)."""
    z = os.path.join(dirpath, "rel.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("marker.txt", "deployed\n")
        zf.writestr("bulk_downloader/__init__.py", "__version__='3.66.214'\n")
    sha = hashlib.sha256(Path(z).read_bytes()).hexdigest()
    return z, sha


def _fake_curl(binroot, version):
    """Write a fake `curl` that prints a health JSON with the given version."""
    os.makedirs(binroot, exist_ok=True)
    p = os.path.join(binroot, "curl")
    with open(p, "w") as f:
        f.write('#!/usr/bin/env bash\n')
        f.write('printf \'{"ok":true,"db_ok":true,"version":"%s"}\'\n' % version)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return binroot


def _run(args, binroot):
    env = dict(os.environ)
    env["PATH"] = binroot + os.pathsep + env.get("PATH", "")
    env["BD_RESTART_CMD"] = "true"        # no-op restart
    return subprocess.run(["bash", str(SCRIPT)] + args, env=env,
                          capture_output=True, text=True, timeout=60)


def test_script_parses_clean():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_happy_path_exits_zero_and_confirms_version():
    work = tempfile.mkdtemp(prefix="bd_dep_")
    instdir = os.path.join(work, "install"); os.makedirs(instdir)
    binroot = os.path.join(work, "bin")
    zip_path, sha = _make_zip(work)
    _fake_curl(binroot, "3.66.214")
    r = _run(["--zip", zip_path, "--expect", "3.66.214", "--sha", sha,
              "--dir", instdir, "--health-url", "http://x/api/health",
              "--timeout", "5", "--interval", "1", "--skip-backend-check"], binroot)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DEPLOY OK" in r.stdout
    # overlay actually landed
    assert Path(instdir, "marker.txt").is_file()


def test_sha_mismatch_exits_nonzero_before_unzip():
    work = tempfile.mkdtemp(prefix="bd_dep_")
    instdir = os.path.join(work, "install"); os.makedirs(instdir)
    binroot = os.path.join(work, "bin")
    zip_path, _sha = _make_zip(work)
    _fake_curl(binroot, "3.66.214")
    r = _run(["--zip", zip_path, "--expect", "3.66.214",
              "--sha", "0" * 64, "--dir", instdir,
              "--health-url", "http://x/api/health",
              "--skip-backend-check"], binroot)
    assert r.returncode != 0
    assert "sha256 mismatch" in r.stderr
    # nothing was unzipped
    assert not Path(instdir, "marker.txt").exists()


def test_version_mismatch_exits_nonzero():
    work = tempfile.mkdtemp(prefix="bd_dep_")
    instdir = os.path.join(work, "install"); os.makedirs(instdir)
    binroot = os.path.join(work, "bin")
    zip_path, sha = _make_zip(work)
    _fake_curl(binroot, "3.66.999")       # service reports the WRONG version
    r = _run(["--zip", zip_path, "--expect", "3.66.214", "--sha", sha,
              "--dir", instdir, "--health-url", "http://x/api/health",
              "--timeout", "2", "--interval", "1", "--skip-backend-check"], binroot)
    assert r.returncode != 0
    assert "health gate" in r.stderr


def test_pycache_sweep_clears_dirs_and_stray_pyc():
    work = tempfile.mkdtemp(prefix="bd_dep_")
    instdir = os.path.join(work, "install"); os.makedirs(instdir)
    # seed stale bytecode the overlay would otherwise leave behind
    pc = os.path.join(instdir, "bulk_downloader", "__pycache__")
    os.makedirs(pc)
    Path(pc, "x.cpython-311.pyc").write_text("stale")
    Path(instdir, "stray.pyc").write_text("stale")
    binroot = os.path.join(work, "bin")
    zip_path, sha = _make_zip(work)
    _fake_curl(binroot, "3.66.214")
    r = _run(["--zip", zip_path, "--expect", "3.66.214", "--sha", sha,
              "--dir", instdir, "--health-url", "http://x/api/health",
              "--timeout", "5", "--interval", "1", "--skip-backend-check"], binroot)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not Path(pc).exists(), "__pycache__ dir not swept"
    assert not Path(instdir, "stray.pyc").exists(), "stray .pyc not swept"


def test_missing_required_flags_fail_fast():
    work = tempfile.mkdtemp(prefix="bd_dep_")
    binroot = os.path.join(work, "bin"); _fake_curl(binroot, "3.66.214")
    r = _run(["--expect", "3.66.214"], binroot)     # no --zip
    assert r.returncode != 0
    assert "--zip is required" in r.stderr
