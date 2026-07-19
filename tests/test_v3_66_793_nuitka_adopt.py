"""MOD-7 adopt (v3.66.793) -- bless the measured-winning Nuitka configuration.

The build-host eval decided it: Nuitka onefile (trimmed) is 117.6 MB / 3.08s vs
PyInstaller onefile 148.3 MB / 4.31s -- a win on both, plus source protection.
Adoption means the DEFAULT config produces that winning build, and the build
tool is declared like any other:

  * scipy + pywt join EXCLUDES -- proven safe on stash (correctness PASS with
    them excluded; BD imports neither directly, they are transitive via the
    perceptual-dedup path) and they save ~127 MB.
  * the redundant --follow-imports is dropped -- Nuitka itself warned it is
    implied by --standalone/--onefile ("need not be specified").
  * nuitka is declared in requirements-dev.txt next to pyinstaller (both are
    build-the-binary tools; neither is a runtime import).
"""
import os
import re


def _load(mod):
    from importlib import import_module
    return import_module(mod)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_scipy_pywt_are_excluded():
    """The measured-safe trim is the default now (saves ~127 MB, correctness
    held on stash)."""
    pc = _load("tools.packaging_config")
    ex = set(pc.CONFIG["excludes"])
    assert "scipy" in ex, "scipy must be excluded (proven safe + saves ~127MB)"
    assert "pywt" in ex, "pywt must be excluded (proven safe + saves ~127MB)"


def test_build_command_carries_the_excludes():
    bn = _load("tools.build_nuitka")
    argv = bn.build_command(mode="standalone", output_dir="/tmp/o")
    for ex in ("scipy", "pywt"):
        assert ("--nofollow-import-to=%s" % ex) in argv, \
            "%s exclude not passed to nuitka" % ex


def test_follow_imports_is_not_passed():
    """--follow-imports is implied by --standalone/--onefile; Nuitka warns when
    it is given explicitly. Adoption drops the redundant flag."""
    bn = _load("tools.build_nuitka")
    for mode in ("standalone", "onefile"):
        argv = bn.build_command(mode=mode, output_dir="/tmp/o")
        assert "--follow-imports" not in argv, \
            "%s command still passes redundant --follow-imports" % mode


def test_scipy_pywt_are_not_direct_bd_imports():
    """Guard the premise of excluding them: if BD ever adds a DIRECT top-level
    import of scipy/pywt, excluding them would break the frozen binary and this
    test fails, forcing a re-decision."""
    hard = re.compile(r"^(?:from|import)\s+(?:scipy|pywt)\b", re.M)
    pkg = os.path.join(REPO, "bulk_downloader")
    offenders = []
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            if fn.endswith(".py"):
                body = open(os.path.join(dp, fn), errors="replace").read()
                if hard.search(body):
                    offenders.append(os.path.relpath(os.path.join(dp, fn), REPO))
    assert not offenders, \
        "scipy/pywt now DIRECTLY imported (top-level) in %s -- excluding them " \
        "would break the frozen binary; re-decide the exclude" % offenders


def test_nuitka_declared_in_dev_requirements():
    """Nuitka is the adopted packaging tool -- declared in the build/dev layer
    next to pyinstaller (a build tool, not a runtime import)."""
    dev = open(os.path.join(REPO, "requirements-dev.txt"), errors="replace").read().lower()
    assert re.search(r"^\s*nuitka\b", dev, re.M), \
        "nuitka not declared in requirements-dev.txt"


def test_nuitka_stays_out_of_runtime_requirements():
    """It is a BUILD tool -- it must NOT leak into the runtime requirements."""
    rt = open(os.path.join(REPO, "requirements.txt"), errors="replace").read().lower()
    assert not re.search(r"^\s*nuitka\b", rt, re.M), \
        "nuitka must not be in runtime requirements.txt (it's a build tool)"
