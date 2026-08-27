"""MOD-7 cut 1 (v3.66.790) -- Nuitka packaging config + command builder.

Cut 1 is deliberately compile-free: it ships ONE source of truth for the
packaging inputs (entry point, data files, hidden imports, excludes) and a
command builder over it. The actual --standalone compile is cut 2, on a build
host. These tests exercise only config integrity + command construction, so
they are sandbox-safe (no gcc, no disk-heavy compile).

The config's whole value is that the packaging inputs are DERIVED and CHECKED
against the live tree, not hand-listed and silently stale -- the same denominator
discipline as the rest of the toolchain. So the tests assert the config SEES the
things a naive static analysis would miss:

  * the dynamic-import targets (importlib.import_module / spec_from_file_location)
    that Nuitka's static follower cannot see -> must be in hidden_imports
  * the non-.py data roots the app serves (static / locales / vendor /
    frontend/dist) -> must be in data_dirs, and must EXIST
  * the entry point the systemd unit actually launches (downloader_ui.py)
"""
import os
import re
import shlex

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(mod):
    from importlib import import_module
    return import_module(mod)


# --------------------------------------------------------------------------
# packaging_config -- the single source of truth
# --------------------------------------------------------------------------

def test_packaging_config_importable_and_shaped():
    pc = _load("tools.packaging_config")
    cfg = pc.CONFIG
    for key in ("entry_point", "product_name", "data_dirs",
                "hidden_imports", "excludes"):
        assert key in cfg, f"packaging_config.CONFIG missing {key!r}"
    assert isinstance(cfg["data_dirs"], (list, tuple))
    assert isinstance(cfg["hidden_imports"], (list, tuple))
    assert isinstance(cfg["excludes"], (list, tuple))


def test_entry_point_is_the_launched_one_and_exists():
    pc = _load("tools.packaging_config")
    ep = pc.CONFIG["entry_point"]
    # the systemd unit runs downloader_ui.py (install_service.sh ExecStart)
    assert os.path.basename(ep) == "downloader_ui.py"
    assert os.path.isfile(os.path.join(REPO, ep)), f"entry point missing: {ep}"


def test_data_dirs_all_exist_in_tree():
    """A data root that does not exist would silently ship nothing -- a check
    whose denominator excludes the missing thing reports clean. Assert every
    declared data dir is real."""
    pc = _load("tools.packaging_config")
    # BUILD ARTIFACTS CANNOT BE ASSERTED PRESENT IN A SOURCE CHECKOUT.
    # frontend/dist is gitignored and produced by `npm run build`, which neither
    # a fresh worktree nor CI performs. Asserting it exists made this gate fail
    # on every band wide enough to select it, in a tree where its absence is
    # CORRECT -- the mirror of the fail-open defect: a gate refusing legitimately
    # green work. The packaging claim it protects is still checked, by
    # test_data_dirs_cover_the_served_roots, which reads the DECLARATION rather
    # than the filesystem.
    _BUILD_ARTIFACTS = {"frontend/dist"}
    declared = [spec[0] if isinstance(spec, (list, tuple)) else spec
                for spec in pc.CONFIG["data_dirs"]]
    tracked = [d for d in declared if d not in _BUILD_ARTIFACTS]
    # The exclusion must not be able to empty the population it filters, and the
    # artifact set must be exactly what we think it is -- a new gitignored dir
    # must not join it silently.
    assert tracked, "every declared data dir was excluded as a build artifact"
    assert _BUILD_ARTIFACTS <= set(declared), (
        f"a declared build artifact vanished from packaging_config: "
        f"{_BUILD_ARTIFACTS - set(declared)}")
    for src in tracked:
        assert os.path.isdir(os.path.join(REPO, src)), \
            f"declared data dir does not exist: {src}"


def test_data_dirs_cover_the_served_roots():
    """The app serves static/, locales/, vendor/ and the SPA at frontend/dist.
    Any of these missing from the bundle is a runtime 404/500 in the frozen
    binary, invisible until someone runs it."""
    pc = _load("tools.packaging_config")
    srcs = {(s[0] if isinstance(s, (list, tuple)) else s).rstrip("/")
            for s in pc.CONFIG["data_dirs"]}
    for needed in ("bulk_downloader/static", "bulk_downloader/locales",
                   "bulk_downloader/vendor", "frontend/dist"):
        assert needed in srcs, f"served root not bundled: {needed}"


def test_hidden_imports_cover_dynamic_import_targets():
    """Nuitka's static import follower cannot see importlib.import_module(...)
    or spec_from_file_location. The config must carry those targets explicitly,
    or the frozen binary ImportErrors at first dynamic dispatch. This test
    re-derives the set from source and asserts the config is a superset."""
    pc = _load("tools.packaging_config")
    declared = set(pc.CONFIG["hidden_imports"])

    # re-derive import_module("literal") targets across the package
    lit = re.compile(r"import_module\(\s*['\"]([A-Za-z0-9_.]+)['\"]")
    found = set()
    pkg = os.path.join(REPO, "bulk_downloader")
    for dp, dns, fns in os.walk(pkg):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            body = open(os.path.join(dp, fn), errors="replace").read()
            for m in lit.finditer(body):
                t = m.group(1)
                if t.startswith("bulk_downloader") and ".." not in t:
                    found.add(t)

    assert found, "sanity: expected to find dynamic import_module targets"
    missing = found - declared
    assert not missing, f"dynamic-import targets absent from hidden_imports: {sorted(missing)}"


def test_provider_resolve_submodules_are_hidden_imports():
    """provider_resolve_impl dispatches to per-provider submodules by name; a
    static follower rooted at the app may miss the ones only reached via
    dispatch. Every real submodule there must be a declared hidden import."""
    pc = _load("tools.packaging_config")
    declared = set(pc.CONFIG["hidden_imports"])
    pri = os.path.join(REPO, "bulk_downloader", "provider_resolve_impl")
    subs = {f"bulk_downloader.provider_resolve_impl.{fn[:-3]}"
            for fn in os.listdir(pri)
            if fn.endswith(".py") and fn != "__init__.py"}
    missing = subs - declared
    assert not missing, f"provider submodules not hidden-imported: {sorted(missing)}"


# --------------------------------------------------------------------------
# build_nuitka -- the command builder over the config
# --------------------------------------------------------------------------

def test_build_command_is_pure_and_writes_nothing(tmp_path):
    bn = _load("tools.build_nuitka")
    argv = bn.build_command(mode="onefile", output_dir=str(tmp_path))
    assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
    # a command builder must not touch disk
    assert not list(tmp_path.iterdir()), "build_command wrote to disk"


def test_build_command_invokes_nuitka_on_the_entry_point():
    bn = _load("tools.build_nuitka")
    argv = bn.build_command(mode="standalone", output_dir="/tmp/out")
    joined = " ".join(argv)
    assert "nuitka" in joined.lower()
    assert argv[-1].endswith("downloader_ui.py"), \
        "entry point must be the final positional arg"
    # the interpreter runs nuitka as a module: python -m nuitka
    assert "-m" in argv and "nuitka" in argv


def test_mode_flags_are_mutually_correct():
    bn = _load("tools.build_nuitka")
    one = " ".join(bn.build_command(mode="onefile", output_dir="/tmp/o"))
    std = " ".join(bn.build_command(mode="standalone", output_dir="/tmp/o"))
    assert "--onefile" in one
    assert "--standalone" in std
    assert "--onefile" not in std
    with pytest.raises((ValueError, KeyError)):
        bn.build_command(mode="bogus", output_dir="/tmp/o")


def test_data_dirs_become_include_data_dir_flags():
    bn = _load("tools.build_nuitka")
    pc = _load("tools.packaging_config")
    argv = bn.build_command(mode="standalone", output_dir="/tmp/o")
    # every data dir shows up as a nuitka --include-data-dir=SRC=DST
    n = sum(1 for a in argv if a.startswith("--include-data-dir="))
    assert n == len(pc.CONFIG["data_dirs"]), \
        f"expected {len(pc.CONFIG['data_dirs'])} data-dir flags, got {n}"


def test_hidden_imports_become_include_module_or_package_flags():
    bn = _load("tools.build_nuitka")
    pc = _load("tools.packaging_config")
    argv = bn.build_command(mode="standalone", output_dir="/tmp/o")
    # each hidden import appears in an --include-module= or --include-package= flag
    flag_targets = set()
    for a in argv:
        for pfx in ("--include-module=", "--include-package="):
            if a.startswith(pfx):
                flag_targets.add(a[len(pfx):])
    for hi in pc.CONFIG["hidden_imports"]:
        assert hi in flag_targets, f"hidden import not passed to nuitka: {hi}"


def test_output_dir_is_honored():
    bn = _load("tools.build_nuitka")
    argv = bn.build_command(mode="standalone", output_dir="/tmp/custom_out")
    assert any(a == "--output-dir=/tmp/custom_out" for a in argv), \
        "output_dir must map to --output-dir="


def test_command_is_shell_safe():
    """No arg should need shell quoting surprises -- the builder returns a list
    for exec, never a string to be re-split."""
    bn = _load("tools.build_nuitka")
    argv = bn.build_command(mode="onefile", output_dir="/tmp/o")
    # round-trips through shlex without change (no embedded spaces/quotes)
    assert shlex.split(" ".join(shlex.quote(a) for a in argv)) == argv
