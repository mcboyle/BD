import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "toolchain" / "bin" / "bd-versync"
BPI = REPO_ROOT / "tools" / "build_pin_index.py"
PK_MIRROR = REPO_ROOT / "project-knowledge" / "bd-versync"


def _run(tree):
    return subprocess.run([sys.executable, str(TOOL), "--tree", str(tree)],
                          capture_output=True, text=True, timeout=120)


def _tree(version="3.66.700", slice4_pin="3.66.700", changelog="3.66.700",
          extra=None, with_bpi=True):
    d = tempfile.mkdtemp(prefix="versync_gate_")
    os.makedirs(os.path.join(d, "bulk_downloader"))
    with open(os.path.join(d, "bulk_downloader", "__init__.py"), "w") as f:
        f.write('__version__ = "%s"\n' % version)
    os.makedirs(os.path.join(d, "tests"))
    if slice4_pin is not None:
        with open(os.path.join(d, "tests", "test_settings_center_slice4.py"), "w") as f:
            f.write('from bulk_downloader import __version__\n'
                    'def test_v():\n    assert __version__ == "%s"\n' % slice4_pin)
    if changelog is not None:
        with open(os.path.join(d, "CHANGELOG.md"), "w") as f:
            f.write("## v%s\nseed\n" % changelog)
    if with_bpi:
        os.makedirs(os.path.join(d, "tools"))
        shutil.copy(str(BPI), os.path.join(d, "tools", "build_pin_index.py"))
    for rel, body in (extra or {}).items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
    return d


def test_absent_tree_is_cannot_evaluate_not_a_green_verdict():
    r = subprocess.run([sys.executable, str(TOOL), "--tree", "/no/such/tree/xyz"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "CANNOT-EVALUATE" in r.stderr
    assert "VERSION CONSISTENT" not in r.stdout


def test_tree_without_a_bulk_downloader_package_is_cannot_evaluate():
    d = tempfile.mkdtemp(prefix="versync_empty_")
    try:
        r = _run(d)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "CANNOT-EVALUATE" in r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_missing_pin_index_tool_is_cannot_evaluate():
    d = _tree(with_bpi=False)
    try:
        r = _run(d)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "CANNOT-EVALUATE" in r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unparseable_test_file_is_cannot_evaluate_not_clean():
    d = _tree(extra={"tests/test_broken.py": "def f(:\n    return\n"})
    try:
        r = _run(d)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "CANNOT-EVALUATE" in r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_synthetic_version_string_in_prose_is_not_a_stray_pin():
    d = _tree(extra={"tests/test_deployish.py":
                     'def test_health():\n'
                     '    assert "/api/health version==3.66.214 confirmed" in "x"\n'})
    try:
        r = _run(d)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "VERSION CONSISTENT" in r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tree_with_no_version_pin_is_rejected():
    d = _tree(slice4_pin=None)
    try:
        r = _run(d)
        assert r.returncode != 0, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_sole_version_pin_must_live_in_the_slice4_file():
    d = _tree(slice4_pin=None, extra={
        "tests/test_elsewhere.py":
            'from bulk_downloader import __version__\n'
            'def test_v():\n    assert __version__ == "3.66.700"\n'})
    try:
        r = _run(d)
        assert r.returncode != 0, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_attribute_form_stray_pin_is_caught():
    d = _tree(extra={
        "tests/test_attr_stray.py":
            'import bulk_downloader\n'
            'def test_v():\n'
            '    assert bulk_downloader.__version__ == "3.66.688"\n'})
    try:
        r = _run(d)
        assert r.returncode != 0, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_name_form_stray_pin_is_caught():
    d = _tree(extra={
        "tests/test_stray.py":
            'from bulk_downloader import __version__\n'
            'def test_v():\n    assert __version__ == "3.66.688"\n'})
    try:
        r = _run(d)
        assert r.returncode != 0, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_wrong_valued_slice4_pin_is_caught():
    d = _tree(slice4_pin="3.66.699")
    try:
        r = _run(d)
        assert r.returncode != 0, (r.stdout, r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_gate_generalises_beyond_3_66():
    d = _tree(version="3.67.0", slice4_pin="3.67.0", changelog="3.67.0")
    try:
        r = _run(d)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "VERSION CONSISTENT" in r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_coherent_tree_passes():
    d = _tree()
    try:
        r = _run(d)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "VERSION CONSISTENT" in r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pk_mirror_matches_toolchain_copy():
    a = hashlib.sha256(TOOL.read_bytes()).hexdigest()
    b = hashlib.sha256(PK_MIRROR.read_bytes()).hexdigest()
    assert a == b, "project-knowledge/bd-versync drifted from toolchain/bin/bd-versync"
