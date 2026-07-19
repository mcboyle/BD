"""build_nuitka -- construct the Nuitka compile command from packaging_config.

MOD-7 cut 1. This is a COMMAND BUILDER, not a compiler. `build_command(...)`
returns an argv list (never a shell string) and touches nothing on disk; the
actual compile is cut 2 on a build host. Splitting build from run keeps cut 1
sandbox-safe (no gcc, no disk-heavy artifact) while still letting the exact
command be unit-tested and reviewed.

The command is derived entirely from tools.packaging_config.CONFIG, so the
entry point, data roots, and hidden imports cannot drift between the config and
the command. Run modes:

  standalone -- a directory bundle (--standalone). Cut 2's eval target.
  onefile    -- a single self-extracting binary (--onefile). Needs patchelf on
                Linux (shipped in bd_nuitka_pack).

Usage:
  python -m tools.build_nuitka --mode standalone --output-dir dist_nuitka --print
  python -m tools.build_nuitka --mode onefile   --output-dir dist_nuitka --run
"""
import argparse
import os
import subprocess
import sys

from tools import packaging_config as pc

_MODES = {
    "standalone": ["--standalone"],
    "onefile": ["--onefile"],
}


def build_command(mode="standalone", output_dir="dist_nuitka",
                  python=None, config=None):
    """Return the Nuitka argv list for `mode`. Pure: no disk, no exec.

    Raises ValueError on an unknown mode (never silently defaults -- a wrong
    mode should fail loudly, not build the wrong artifact)."""
    if mode not in _MODES:
        raise ValueError("unknown mode %r (want one of %s)"
                         % (mode, ", ".join(sorted(_MODES))))
    cfg = config or pc.CONFIG
    py = python or sys.executable

    argv = [py, "-m", "nuitka"]
    argv += _MODES[mode]

    # assume yes to Nuitka's download prompts is unsafe in a locked build; the
    # pack provides everything offline, so we DO allow plugin autodetect but
    # never a network fetch. --assume-yes-for-downloads is intentionally omitted.
    argv += [
        "--output-dir=%s" % output_dir,
        "--product-name=%s" % cfg["product_name"],
        "--company-name=%s" % cfg["product_name"],
        # NB: --follow-imports is NOT passed -- standalone/onefile mode implies
        # it, and Nuitka warns when it is given explicitly ("need not be
        # specified"). The static graph is still fully walked; the dynamic
        # targets the follower cannot see are added as --include-* below.
    ]

    # data dirs -> --include-data-dir=SRC=DST
    for spec in cfg["data_dirs"]:
        src, dst = (spec if isinstance(spec, (list, tuple)) else (spec, spec))
        argv.append("--include-data-dir=%s=%s" % (src, dst))

    # hidden imports -> --include-package= for packages, --include-module= for
    # leaf modules. A dotted target that names a real package dir is a package.
    repo = cfg.get("repo", ".")
    for hi in cfg["hidden_imports"]:
        rel = hi.replace(".", os.sep)
        is_pkg = os.path.isdir(os.path.join(repo, rel)) or \
            os.path.isfile(os.path.join(repo, rel, "__init__.py"))
        argv.append(("--include-package=%s" if is_pkg
                     else "--include-module=%s") % hi)

    # excludes -> --nofollow-import-to=
    for ex in cfg["excludes"]:
        argv.append("--nofollow-import-to=%s" % ex)

    # entry point LAST (Nuitka's positional).
    argv.append(cfg["entry_point"])
    return argv


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_nuitka")
    ap.add_argument("--mode", choices=sorted(_MODES), default="standalone")
    ap.add_argument("--output-dir", default="dist_nuitka")
    ap.add_argument("--print", action="store_true",
                    help="print the command and exit (default if neither "
                         "--print nor --run given)")
    ap.add_argument("--run", action="store_true",
                    help="actually invoke Nuitka (cut 2 / build host only -- "
                         "heavy, needs gcc + patchelf)")
    a = ap.parse_args(argv)

    try:
        cmd = build_command(mode=a.mode, output_dir=a.output_dir)
    except ValueError as e:
        print("build_nuitka: %s" % e, file=sys.stderr)
        return 2

    if not a.run:
        # default + --print: show it, run nothing.
        print(" ".join(cmd))
        return 0

    print("build_nuitka: invoking Nuitka (%s) ..." % a.mode, file=sys.stderr)
    return subprocess.call(cmd, cwd=pc.CONFIG.get("repo", "."))


if __name__ == "__main__":
    sys.exit(main())
