#!/usr/bin/env python3
"""build_release.py — PT10. Deterministic release-zip builder.

Replaces the ad-hoc "zip up the project, hope it's complete" workflow
with a script that:

  1. Reads the version from bulk_downloader/__init__.py::__version__
     and confirms CHANGELOG.md's top entry matches.
  2. Builds the release zip from the source tree using the SAME
     exclusion list as dev_suite._manifest_excluded() (so the builder
     and verifier share one definition; if either drifts, the gate
     below catches it).
  3. Runs dev_suite.zip_manifest_check() against the built zip — fails
     the build if it reports any drift.
  4. Extracts the zip into a scratch directory and runs the test suite
     from the extracted copy — the operating-instructions rule. Only
     emits the final zip if `Failed: 0`.
  5. Writes the zip to the cwd (or --out DIR) with the canonical name
     BulkDownloader_v<X_Y_Z>.zip.

Deterministic means: same source tree -> byte-identical zip (modulo
zip metadata that can't be controlled, like extra-field timestamps;
the actual file content + ordering is stable).

Usage:
    python tools/build_release.py                  # default: cwd output
    python tools/build_release.py --out /tmp/      # to a specific dir
    python tools/build_release.py --skip-tests     # zip + verify only,
                                                   # for debugging the
                                                   # builder itself
    python tools/build_release.py --quick          # zip + verify; tests
                                                   # become an exhortation
                                                   # rather than a gate
                                                   # (use only between
                                                   # releases, never on
                                                   # an actual cut)

Exit codes:
    0 — zip emitted, all gates passed
    1 — zip built but a gate failed (verifier drift, test failures)
    2 — couldn't even build (missing source, version mismatch, IO error)

This script is the canonical release builder. Future release cuts go
through it. PT14 archive bookkeeping happens AFTER this script emits
its zip — the operator copies the result into the rollback archive
manually (or with `python tools/rollback.py --archive X.Y.Z --from <path>`).
"""

import argparse
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _repo_root() -> Path:
    """The project root. This script lives at <root>/tools/."""
    return Path(__file__).resolve().parent.parent


def _read_version(root: Path) -> str:
    init_py = root / "bulk_downloader" / "__init__.py"
    if not init_py.is_file():
        print(f"FAIL: {init_py} missing", file=sys.stderr)
        sys.exit(2)
    for line in init_py.read_text(encoding="utf-8").splitlines():
        m = re.match(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", line)
        if m:
            return m.group(1)
    print(f"FAIL: no __version__ in {init_py}", file=sys.stderr)
    sys.exit(2)


def write_build_info(dest_dir, sha=None) -> dict:
    """B1.3 (post-365): write build_info.json {sha, built_at} into dest_dir and
    return the payload. `sha` defaults to a 12-char build stamp derived from the
    UTC build time. The same value is exported as VITE_BUILD_STAMP by the caller
    so the frontend bundle and the backend agree on one build identity, which
    /api/health then exposes (FE-loaded stamp vs backend sha is a meaningful
    version-truth compare, unlike package.json 0.1.0 vs backend 3.66.x)."""
    import datetime as _dt
    import hashlib as _hl
    built_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not sha:
        sha = _hl.sha256(built_at.encode("utf-8")).hexdigest()[:12]
    info = {"sha": sha, "built_at": built_at}
    p = Path(dest_dir) / "build_info.json"
    p.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def _verify_changelog(root: Path, version: str) -> None:
    """Confirm CHANGELOG.md's top entry matches `version`. Matches the
    contract test, but caught earlier."""
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        print(f"FAIL: {changelog} missing", file=sys.stderr)
        sys.exit(2)
    text = changelog.read_text(encoding="utf-8")
    # First non-comment header line should be `## vX.Y.Z`.
    pat = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\b", re.MULTILINE)
    m = pat.search(text)
    if not m:
        print("FAIL: CHANGELOG.md has no ## vX.Y.Z headers",
              file=sys.stderr)
        sys.exit(2)
    top = m.group(1)
    if top != version:
        print(
            f"FAIL: __version__ is {version} but CHANGELOG top entry "
            f"is v{top} -- bump CHANGELOG first", file=sys.stderr)
        sys.exit(2)


def _load_exclusions(root: Path):
    """Import dev_suite's exclusion filter so the builder and the
    verifier share ONE definition. If we duplicated the list here, the
    two would drift silently.

    v3.66.736 -- this used to `sys.path.insert(0, root)` and import, and
    NEVER undo either. Run against a temp tree (which is exactly what
    tools/precut_check.py::_tree_files does), it left that tree on sys.path
    AND cached `bulk_downloader` -> the temp tree's STUB __init__ in
    sys.modules for the rest of the interpreter. Every later import of
    bulk_downloader then resolved to the stub -- which is why version-pin and
    settings suites FAILED IN THE BAND but PASSED STANDALONE (@730): the band
    shares one interpreter, and whoever ran precut_check first poisoned it.

    The path is popped and the module cache is restored to exactly what it was.
    That is safe for the two callables we return: `_manifest_excluded` is pure
    (module-level constants) and `zip_manifest_check` only imports `zipfile`
    at call time -- and dropping a module from sys.modules does not destroy the
    module object while a function's __globals__ still references it.
    """
    root_s = str(root)
    inserted = root_s not in sys.path
    if inserted:
        sys.path.insert(0, root_s)
    # Snapshot every bulk_downloader* module already cached, so we can put the
    # cache back byte-for-byte -- both evicting what WE caused to be imported
    # and re-binding anything our import may have replaced.
    saved = {k: v for k, v in sys.modules.items()
             if k == "bulk_downloader" or k.startswith("bulk_downloader.")}
    try:
        from bulk_downloader.dev_suite import (
            _manifest_excluded, zip_manifest_check)
    except ImportError as e:
        print(f"FAIL: can't import dev_suite: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        if inserted:
            try:
                sys.path.remove(root_s)
            except ValueError:
                pass
        for k in [k for k in sys.modules
                  if k == "bulk_downloader" or k.startswith("bulk_downloader.")]:
            if k not in saved:
                del sys.modules[k]
        sys.modules.update(saved)
    return _manifest_excluded, zip_manifest_check


def _walk_tree(root: Path, excluded) -> list[Path]:
    """Every file under `root` not excluded by the dev_suite filter,
    sorted for determinism."""
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        rel_posix = str(rel).replace("\\", "/")
        if excluded(rel_posix):
            continue
        out.append(p)
    out.sort(key=lambda q: str(q.relative_to(root)).replace("\\", "/"))
    return out


def _stamp_state_json(raw: bytes, *, version: str, file_count: int,
                      zip_name: str, root=None) -> bytes:
    """F0.2: patch the in-zip STATE.json with build-derived facts so
    ``verify_release``'s count + built_version gates are satisfied on the
    FIRST build (no two-pass STATE edit + rebuild).

    Sets ``built_version`` (== ``__version__``), ``zip.file_count`` (== the
    total member count, which is ``len(files)`` since STATE.json is itself one
    of those members), and ``zip.name``/``zip.file`` (== the output zip name).
    ``live_version`` is deliberately left alone — it legitimately lags an
    undeployed cut. On any parse failure the bytes are returned unchanged so a
    malformed STATE can never break the build (the verifier still gates)."""
    try:
        d = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw
    if not isinstance(d, dict):
        return raw
    d["built_version"] = version
    z = d.get("zip")
    if isinstance(z, dict):
        z["file_count"] = file_count
        z["name"] = zip_name
        if "file" in z:
            z["file"] = zip_name
        # F0.2 cannot stamp a valid full-zip sha256 here: that value is computed
        # over a zip that already contains this STATE.json, so it can never match.
        # Null it so the in-zip copy makes no false claim and bd-state cleanly
        # SKIPS its sha gate on the work-tree fallback. The canonical pin (with the
        # real sha) lives in the session pack.
        z["sha256"] = None
    # Refresh the declared guard SHAs from the ACTUAL built tree so the in-zip
    # STATE is internally honest (a guard-changing cut otherwise embeds a stale
    # guard pin that verify_release's guard-vs-zip check would flag). Recompute
    # only the keys STATE already declares; missing files are left as-is.
    if root is not None:
        import hashlib as _hl
        from pathlib import Path as _P
        root = _P(root)
        g = d.get("guards")
        gf = d.get("guards_full_sha256")
        if isinstance(g, dict):
            for k in list(g):
                fp = root / k
                if fp.is_file():
                    full = _hl.sha256(fp.read_bytes()).hexdigest()
                    g[k] = full[:8]
                    if isinstance(gf, dict) and k in gf:
                        gf[k] = full
    return (json.dumps(d, indent=2) + "\n").encode("utf-8")


def _build_zip(root: Path, files: list[Path], dest: Path,
               version: str) -> None:
    """Write `dest` as a deterministic zip of `files` relative to
    `root`. Flat layout (no top-level prefix) matches the canonical
    v3.63.8 release shape.

    The in-zip STATE.json is stamped in place (F0.2) with the live member
    count + built_version + zip name, so the first build is already
    self-consistent for the verifier — see :func:`_stamp_state_json`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a tempfile in the same dir, then atomic-rename.
    fd, tmp = tempfile.mkstemp(prefix=".build_release.", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        with zipfile.ZipFile(tmp_path, "w",
                             compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for p in files:
                rel = str(p.relative_to(root)).replace("\\", "/")
                # zipfile.write() preserves mtime by default which makes
                # builds non-deterministic. Build the ZipInfo manually
                # with a fixed date_time so two builds of the same tree
                # produce the same bytes.
                info = zipfile.ZipInfo(filename=rel,
                                       date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                # Preserve executable bit on .sh files and Python scripts
                # — matters because the sandbox mount strips exec bits
                # but the release should ship them executable on Linux.
                mode = 0o644
                if (rel.endswith(".sh")
                        or rel.endswith(".py") and (p.stat().st_mode
                                                    & stat.S_IXUSR)):
                    mode = 0o755
                info.create_system = 3
                info.external_attr = (mode << 16)
                if rel == "STATE.json":
                    # F0.2: stamp STATE in its natural sorted position (no
                    # reordering). The total member count is len(files) and is
                    # known up front; STATE.json is one of those members, so
                    # file_count == namelist length on the first build.
                    data = _stamp_state_json(
                        p.read_bytes(), version=version,
                        file_count=len(files), zip_name=dest.name, root=root)
                    zf.writestr(info, data)
                else:
                    with p.open("rb") as src:
                        zf.writestr(info, src.read())
        os.replace(tmp_path, dest)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _run_extracted_suite(zip_path: Path) -> tuple[bool, str]:
    """Extract `zip_path` into a fresh tempdir and run the suite there.
    Returns (passed, summary_line)."""
    with tempfile.TemporaryDirectory(prefix="bd_release_") as td:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(td)
        extracted = Path(td)
        # Quick sanity check: the zip should have unpacked a populated
        # tree with bulk_downloader/__init__.py at the root.
        if not (extracted / "bulk_downloader" / "__init__.py").is_file():
            return False, ("extracted tree missing "
                           "bulk_downloader/__init__.py")
        # Run the suite. BD_DISABLE_KEEPALIVE=1 per operating
        # instructions. Capture stdout so the summary line can be
        # extracted regardless of whether tests pass.
        env = os.environ.copy()
        env["BD_DISABLE_KEEPALIVE"] = "1"
        # Use the same python that's running us.
        try:
            r = subprocess.run(
                [sys.executable, "run_tests.py"],
                cwd=str(extracted),
                env=env,
                capture_output=True,
                text=True,
                timeout=60 * 60,  # 1h ceiling
            )
        except subprocess.TimeoutExpired:
            return False, "suite timed out after 1h"
        except OSError as e:
            return False, f"could not invoke runner: {e}"
        # Extract the totals line.
        summary = ""
        for line in r.stdout.splitlines():
            if "Total:" in line and "Passed:" in line and "Failed:" in line:
                summary = line.strip()
        if not summary:
            summary = (r.stdout.splitlines() or ["(no output)"])[-1]
        passed = (r.returncode == 0)
        return passed, summary


def _run_catalog_check(root: Path) -> int:
    """Invoke `tools/build_endpoint_catalog.py --check` as a subprocess
    and return its exit code.

    Subprocess (not in-process import) so that a future syntax error
    or import-time failure in the catalog builder doesn't break
    build_release.py's own startup. The catalog builder also imports
    the full Flask app, which we'd rather not pull into this script's
    address space just for a gate check — keeps build_release.py
    fast to import for `--help` and other quick paths.
    """
    cmd = [sys.executable, str(root / "tools" / "build_endpoint_catalog.py"),
           "--check"]
    try:
        # The check inherits the current environment. BD_DISABLE_KEEPALIVE
        # is set internally by the catalog builder, so we don't need to
        # pre-set it here.
        result = subprocess.run(cmd, cwd=str(root))
    except FileNotFoundError:
        # The catalog builder is missing entirely — treat as a hard
        # build failure rather than a soft skip. If somebody deliberately
        # removes it they should also remove this gate.
        print(f"FAIL: {cmd[1]} not found", file=sys.stderr)
        return 2
    return result.returncode


def _run_function_index_check(root: Path) -> int:
    """Invoke `tools/build_function_index.py --check` as a subprocess
    and return its exit code.

    Subprocess for the same reasons as _run_catalog_check — isolates
    a hypothetical broken builder from this script's own import path.
    Unlike the catalog check, the function index builder is pure-AST
    and doesn't touch the Flask app, so it's noticeably faster (~0.2s
    vs ~1-2s).
    """
    cmd = [sys.executable, str(root / "tools" / "build_function_index.py"),
           "--check"]
    try:
        result = subprocess.run(cmd, cwd=str(root))
    except FileNotFoundError:
        print(f"FAIL: {cmd[1]} not found", file=sys.stderr)
        return 2
    return result.returncode


def _run_dependency_graph_check(root: Path) -> int:
    """Invoke `tools/dependency_graph.py --check` as a subprocess and
    return its exit code.

    Subprocess for the same reasons as the catalog/function-index checks
    — isolates a hypothetical broken builder from this script's import
    path. Pure-AST (no Flask import), so it is in the cheap
    function-index class (~0.3s), independent of the catalog gate.
    """
    cmd = [sys.executable, str(root / "tools" / "dependency_graph.py"),
           "--check"]
    try:
        result = subprocess.run(cmd, cwd=str(root))
    except FileNotFoundError:
        print(f"FAIL: {cmd[1]} not found", file=sys.stderr)
        return 2
    return result.returncode


def _run_route_count_check(root: Path) -> int:
    """Invoke `tools/check_route_counts.py --check` as a subprocess (G12).

    Cross-checks blueprint route-decorator counts against the parity inventory
    and the test pins; fails if any drift. Catches the v3.66.176 class (a route
    added without updating the count pin / inventory in the same change) at
    build start instead of only in the post-deploy full suite. Subprocess for
    the same isolation reasons as the catalog/function-index gates; Flask-free,
    fast (~static parse)."""
    cmd = [sys.executable, str(root / "tools" / "check_route_counts.py"), "--check"]
    try:
        result = subprocess.run(cmd, cwd=str(root))
    except Exception as e:  # pragma: no cover
        print(f"  Route counts   : error ({e})", file=sys.stderr)
        return 2
    return result.returncode


def _run_capture_model_golden_check(root: Path) -> int:
    """Invoke `tools/capture_model_golden.py --check` as a subprocess (B0).

    The capture-model characterization golden pins the three capture readers'
    derived output on a fixed synthetic capture. Failing the build on drift makes
    a Phase-B reroute (B1/B2) that silently changes derived behaviour impossible
    to ship unnoticed. Subprocess for the same builder-isolation reason as the
    other gates; imports Flask-free, fast.
    """
    cmd = [sys.executable, str(root / "tools" / "capture_model_golden.py"), "--check"]
    try:
        result = subprocess.run(cmd, cwd=str(root))
    except FileNotFoundError:
        print(f"FAIL: {cmd[1]} not found", file=sys.stderr)
        return 2
    return result.returncode


# Sentinel returned by _run_kb_lint when no KB dir is configured —
# distinct from "errors found" so build_release.py can soft-skip
# rather than fail.
_KB_LINT_SKIPPED = -1


def _resolve_lint_kb_allow() -> list:
    """KB-lint --allow-missing-ref names, resolved store > env seed > default.

    v3.66.316 (CLI->GUI parity, guard cut): the global_config store key
    ``lint_kb_allow`` (comma-list) overrides the BD_LINT_KB_ALLOW env seed when
    set on the build host. Read at call time; lazy import, fail-safe to env.
    Returns a (possibly empty) list of trimmed names.
    """
    raw = ""
    try:
        from bulk_downloader import global_config as _gc
        _sv = _gc.get("lint_kb_allow", None)
        if _sv not in (None, ""):
            raw = str(_sv).strip()
    except Exception:
        pass
    if not raw:
        raw = os.environ.get("BD_LINT_KB_ALLOW", "").strip()
    return [n.strip() for n in raw.split(",") if n.strip()]


def _run_kb_lint(root: Path) -> tuple[int, str]:
    """Invoke `tools/lint_kb.py` against the KB working dir specified
    by the BD_KB_DIR env var.

    Returns (rc, message):
        (0, "clean (N warns)")            — no errors found
        (_KB_LINT_SKIPPED, reason)        — no KB dir configured /
                                            missing canonical files
        (1, "N errors — see stderr")      — errors found, build fails

    The gate is conditional because the canonical KB (PROJECT_STATE,
    DANGER_MAP, etc.) lives in project knowledge / the operator's
    working tree, NOT in the repo. So at zip-build time on a sandbox
    or CI, the KB isn't necessarily reachable — we soft-skip in that
    case rather than failing every build.

    To turn the gate ON, set BD_KB_DIR=/path/to/kb-working-dir
    before invoking build_release.py.
    """
    kb_dir = os.environ.get("BD_KB_DIR", "").strip()
    if not kb_dir:
        return _KB_LINT_SKIPPED, "BD_KB_DIR not set"
    kb_path = Path(kb_dir)
    if not kb_path.is_dir():
        return _KB_LINT_SKIPPED, f"BD_KB_DIR={kb_dir} not a directory"
    # Sanity: require KB_WORKFLOW.md (the lint reads its banned-
    # phrases block from there; without it the check is meaningless).
    if not (kb_path / "KB_WORKFLOW.md").is_file():
        return _KB_LINT_SKIPPED, (
            f"BD_KB_DIR={kb_dir} doesn't contain KB_WORKFLOW.md"
        )
    cmd = [sys.executable, str(root / "tools" / "lint_kb.py"),
           "--kb-dir", str(kb_path)]
    # Forward BD_LINT_KB_ALLOW (comma-separated list) as repeated
    # --allow-missing-ref args. Convenience for operators who
    # always need a specific suppression set.
    for name in _resolve_lint_kb_allow():
        cmd.extend(["--allow-missing-ref", name])
    try:
        result = subprocess.run(cmd, cwd=str(root))
    except FileNotFoundError:
        return _KB_LINT_SKIPPED, "tools/lint_kb.py not found"
    if result.returncode == 0:
        return 0, "clean"
    return 1, "lint reported errors (see above)"


# ── D5a (v3.64.x): optional SPA prebuild ───────────────────────────
#
# By default the release zip does NOT include `frontend/dist/` — the
# operator runs `npm install && npm run build` on stash post-deploy
# (OPEN_THREADS 1b). When `--prebuild-spa` is set, the builder runs
# the npm steps BEFORE walking the tree, so dist ends up in the zip
# and the operator can skip the build step at deploy time.
#
# The exclusion list (dev_suite._manifest_excluded) does NOT exclude
# `frontend/dist/`, so once dist exists at build time, it ships
# automatically. The opt-in flag is the only new mechanism.
#
# Failure modes handled explicitly:
#   - npm not installed         → fail with a clear "install Node" message
#   - npm install / build fails → fail with the npm output for diagnosis
#   - build succeeds but index.html missing → fail (silent build break)
#
# Default off preserves the v3.63.10/3.64.0 contract: a release zip
# built without the flag is byte-identical (modulo the version) to
# what the script produced before D5a.


def _run_version_pin_gate(root: Path, version: str) -> int:
    """Wave 5 hygiene gate: fail the build if a test pins __version__ to a
    value other than the release version (the slice4 trap). Runs
    scan_version_pins.py --tests-only (the runtime-stray scan is intentionally
    suppressed — it is informational and floods on historical version refs in
    comments). Subprocess so the standalone tool stays un-imported."""
    cmd = [sys.executable, str(root / "tools" / "scan_version_pins.py"),
           "--root", str(root), "--expect", version, "--tests-only",
           # test_release_hygiene_gates.py is a fixture-bearing file: its teeth
           # test embeds a deliberate non-release literal (__version__ ==
           # "3.66.168") to prove the scanner flags a mismatch. Without this the
           # version-pin gate self-collides on the scanner's own test and aborts
           # the build. --ignore is documented for exactly this case.
           "--ignore", "test_release_hygiene_gates"]
    try:
        return subprocess.run(cmd, cwd=str(root)).returncode
    except OSError as e:
        print(f"  Version pins   : gate error ({e})", file=sys.stderr)
        return 1


def _run_frontend_required_gate(root: Path, zip_path: Path) -> int:
    """Wave 5 hygiene gate: assert the critical built-SPA frontend set is
    present and non-empty in the release zip (the 165 frontend-drop class)."""
    cmd = [sys.executable, str(root / "tools" / "check_frontend_present.py"),
           "--candidate", str(zip_path), "--required"]
    try:
        return subprocess.run(cmd, cwd=str(root)).returncode
    except OSError as e:
        print(f"  Frontend gate  : error ({e})", file=sys.stderr)
        return 1


def _run_baseline_gates(root: Path, zip_path: Path, baseline: Path,
                        approved: list) -> int:
    """Wave 5 hygiene gate (opt-in via --baseline): compare the candidate zip
    against a previous release. Fails on an unapproved frontend file
    dropped/changed, on a forbidden artifact, or on a CHANGELOG/version
    mismatch. Soft-skipped by the caller when no baseline is given."""
    rc = 0
    fe = [sys.executable, str(root / "tools" / "check_frontend_present.py"),
          "--baseline", str(baseline), "--candidate", str(zip_path)]
    if approved:
        fe += ["--approved", *approved]
    try:
        if subprocess.run(fe, cwd=str(root)).returncode != 0:
            rc = 1
    except OSError as e:
        print(f"  Frontend regr. : error ({e})", file=sys.stderr)
        rc = 1
    df = [sys.executable, str(root / "tools" / "diff_release_zips.py"),
          "--old", str(baseline), "--new", str(zip_path)]
    try:
        if subprocess.run(df, cwd=str(root)).returncode != 0:
            rc = 1
    except OSError as e:
        print(f"  Release diff   : error ({e})", file=sys.stderr)
        rc = 1
    return rc


def _prebuild_spa(root: Path) -> int:
    """Run `npm install && npm run build` in `<root>/frontend/`.
    Returns 0 on success, non-zero on any failure (with the failure
    already printed to stderr).
    """
    frontend = root / "frontend"
    if not frontend.is_dir():
        print(f"FAIL: --prebuild-spa requires {frontend}/ — not found",
              file=sys.stderr)
        return 2
    package_json = frontend / "package.json"
    if not package_json.is_file():
        print(f"FAIL: --prebuild-spa requires {package_json} — not found",
              file=sys.stderr)
        return 2
    # Is npm even installed? `shutil.which` is the portable check.
    npm_bin = shutil.which("npm")
    if not npm_bin:
        print("FAIL: --prebuild-spa requires npm on PATH — install Node.js "
              "18+ (https://nodejs.org/) and retry", file=sys.stderr)
        return 2
    print(f"  Prebuild SPA   : using {npm_bin}")
    # Use `npm install` (NOT `npm ci`) — matches OPEN_THREADS 1b's
    # rationale: new deps may have landed since the last lockfile,
    # so install is the safer default. The build is deterministic
    # downstream of dist regardless.
    print(f"  Prebuild SPA   : npm install ...")
    try:
        r = subprocess.run([npm_bin, "install"], cwd=str(frontend))
    except OSError as e:
        print(f"FAIL: npm install could not spawn: {e}", file=sys.stderr)
        return 2
    if r.returncode != 0:
        print(f"FAIL: npm install exited {r.returncode} — see output above",
              file=sys.stderr)
        return 1
    print(f"  Prebuild SPA   : npm run build ...")
    try:
        r = subprocess.run([npm_bin, "run", "build"], cwd=str(frontend))
    except OSError as e:
        print(f"FAIL: npm run build could not spawn: {e}", file=sys.stderr)
        return 2
    if r.returncode != 0:
        print(f"FAIL: npm run build exited {r.returncode} — see output above",
              file=sys.stderr)
        return 1
    # Sanity: the build succeeded according to npm, but did it
    # actually produce dist/index.html?
    dist_index = frontend / "dist" / "index.html"
    if not dist_index.is_file():
        print(f"FAIL: npm run build returned 0 but {dist_index} is missing "
              f"— silent build break, do NOT ship this zip", file=sys.stderr)
        return 1
    dist_size = sum(
        p.stat().st_size for p in (frontend / "dist").rglob("*") if p.is_file())
    if dist_size >= 1024:
        size_str = f"{dist_size / 1024:.0f} KB"
    else:
        size_str = f"{dist_size} bytes"
    print(f"  Prebuild SPA   : dist/index.html present "
          f"({size_str} total)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path.cwd(),
                    help="output directory (default: cwd)")
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip the extracted-suite gate (debug only)")
    ap.add_argument("--quick", action="store_true",
                    help="run-tests becomes informational, not gating")
    ap.add_argument("--prebuild-spa", action="store_true",
                    help=("run `npm install && npm run build` in frontend/ "
                          "before zipping, so the release ships with "
                          "frontend/dist/ included. Default off: the "
                          "operator runs the SPA build on the deploy "
                          "target (OPEN_THREADS 1b). Useful for "
                          "single-step deploys where Node.js may not be "
                          "available on the target."))
    ap.add_argument("--baseline", type=Path, default=None,
                    help=("previous release zip to compare against. When given, "
                          "runs the frontend-regression + release-diff hygiene "
                          "gates (fail on dropped/changed frontend, forbidden "
                          "artifacts, or CHANGELOG mismatch). Soft-skipped when "
                          "absent."))
    ap.add_argument("--approved-frontend", nargs="*", default=[],
                    help=("frontend paths whose change/removal vs --baseline is "
                          "approved (passed through to check_frontend_present)."))
    args = ap.parse_args()

    root = _repo_root()
    print(f"  Repo root      : {root}")

    version = _read_version(root)
    print(f"  Version        : {version}")

    _verify_changelog(root, version)
    print(f"  CHANGELOG      : top entry matches v{version}")

    # KB_PLAN_v2 B.2 catalog gate. Run `build_endpoint_catalog.py
    # --check` in-process and fail the build on drift. The check
    # imports bulk_downloader.app once (~1-2s); cheap relative to the
    # extracted-suite gate below. We do this BEFORE zipping so a
    # stale catalog doesn't get baked into the release zip — the
    # suite drift test in test_endpoint_catalog_in_sync.py would
    # catch it too, but failing here is faster (no zip + extract +
    # test-discover round trip) and the error message is more
    # actionable.
    print(f"  Endpoint catalog: checking ...")
    _ec_rc = _run_catalog_check(root)
    if _ec_rc != 0:
        print(f"FAIL: ENDPOINT_CATALOG.md is stale. "
              f"Run `python tools/build_endpoint_catalog.py` and "
              f"recommit before retrying the release build.",
              file=sys.stderr)
        return 1
    print(f"  Endpoint catalog: in sync")

    # KB_PLAN_v2 B.1 function-index gate. Pure-AST regen (~0.2-0.5s),
    # no Flask import — the gate is cheap and independent of the
    # catalog gate above.
    print(f"  Function index : checking ...")
    _fi_rc = _run_function_index_check(root)
    if _fi_rc != 0:
        print(f"FAIL: FUNCTION_INDEX.md is stale. "
              f"Run `python tools/build_function_index.py` and "
              f"recommit before retrying the release build.",
              file=sys.stderr)
        return 1
    print(f"  Function index : in sync")

    # A3 dependency-graph gate. Pure-AST regen (~0.3s), no Flask import —
    # same class as the function-index gate, independent of the catalog.
    # Fails the build on DEPENDENCY_GRAPH.{json,md} drift so a stale
    # coupling graph never bakes into the zip.
    print(f"  Dependency graph: checking ...")
    _dg_rc = _run_dependency_graph_check(root)
    if _dg_rc != 0:
        print(f"FAIL: DEPENDENCY_GRAPH.* is stale. "
              f"Run `python tools/dependency_graph.py` and "
              f"recommit before retrying the release build.",
              file=sys.stderr)
        return 1
    print(f"  Dependency graph: in sync")

    # G12 route-count / inventory-freshness gate. Cross-checks blueprint route
    # counts against reports/gui_parity_inventory.* and the test pins; fails the
    # build on any drift — the exact class that shipped in v3.66.176 and was only
    # caught post-deploy by the full suite. Flask-free; fast.
    print(f"  Route counts   : checking ...")
    _rcnt_rc = _run_route_count_check(root)
    if _rcnt_rc != 0:
        print(f"FAIL: route-count drift (source vs inventory vs test-pin). "
              f"Regenerate reports/gui_parity_inventory.* (tools/gui_parity_inventory.py) "
              f"and/or re-pin tests/test_wave2_backlog.py so all three agree, then retry.",
              file=sys.stderr)
        return 1
    print(f"  Route counts   : in sync")

    # B0 capture-model golden gate. Guards Phase-B convergence: any reroute of a
    # capture reader/normalizer that changes derived output drifts the golden and
    # fails the build. Pure synthetic; Flask-free; fast.
    print(f"  Capture model   : checking ...")
    _cmg_rc = _run_capture_model_golden_check(root)
    if _cmg_rc != 0:
        print(f"FAIL: capture-model golden drift. A reader/normalizer change altered "
              f"derived behaviour. Re-verify the reroute, then "
              f"`python tools/capture_model_golden.py --write` and recommit if intended.",
              file=sys.stderr)
        return 1
    print(f"  Capture model   : in sync")

    # Wave 5 hygiene gate: version-pin consistency (test pins must match the
    # release version). Pre-zip so the slice4 trap fails at build start, not
    # after a wasted zip+extract+suite round trip.
    print(f"  Version pins   : checking ...")
    _vp_rc = _run_version_pin_gate(root, version)
    if _vp_rc != 0:
        print(f"FAIL: a test pins __version__ to a non-release value. "
              f"Update the pin (e.g. tests/test_settings_center_slice4.py) "
              f"to v{version} and retry.", file=sys.stderr)
        return 1
    print(f"  Version pins   : in sync")

    # KB_PLAN_v2 C.1 KB lint gate. The canonical KB (Layer-2 files
    # like PROJECT_STATE.md, DANGER_MAP.md) doesn't live in the
    # repo — it lives in the operator's project knowledge / kb
    # working dir. So the gate is conditional: if a KB working dir
    # is configured via the BD_KB_DIR environment variable and the
    # canonical files are present there, fail the build on lint
    # errors. Otherwise soft-skip with a clear note.
    print(f"  KB lint        : checking ...")
    _kb_rc, _kb_msg = _run_kb_lint(root)
    if _kb_rc == 0:
        print(f"  KB lint        : {_kb_msg}")
    elif _kb_rc == _KB_LINT_SKIPPED:
        # Soft-skip — the KB working dir isn't available at this
        # invocation site. The release can still ship.
        print(f"  KB lint        : skipped ({_kb_msg})")
    else:
        print(f"FAIL: KB lint reported errors. Fix them or run "
              f"`python tools/lint_kb.py --kb-dir $BD_KB_DIR` "
              f"to inspect.", file=sys.stderr)
        print(f"        {_kb_msg}", file=sys.stderr)
        return 1

    excluded, verifier = _load_exclusions(root)

    # B1.3: stamp build identity into the tree BEFORE the walk so build_info.json
    # ships in the zip (and lands in the install dir for /api/health to read).
    # The same stamp is exported as VITE_BUILD_STAMP so a frontend build (now or
    # later) bakes the matching value into the bundle.
    _bi = write_build_info(root)
    os.environ["VITE_BUILD_STAMP"] = _bi["sha"]
    print(f"  Build identity : {_bi['sha']} @ {_bi['built_at']}")

    # D5a: optional SPA prebuild. Must happen BEFORE _walk_tree so the
    # produced dist/ is captured in the zip. Failure here aborts the
    # build outright — no point zipping a half-built frontend.
    if args.prebuild_spa:
        rc = _prebuild_spa(root)
        if rc != 0:
            return rc
    else:
        print(f"  Prebuild SPA   : skipped (default; "
              f"build on deploy target — see OPEN_THREADS 1b)")

    files = _walk_tree(root, excluded)
    print(f"  Files to zip   : {len(files)}")
    if not files:
        print("FAIL: no files to zip — repo root looks wrong",
              file=sys.stderr)
        return 2

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"BulkDownloader_v{version.replace('.', '_')}.zip"
    zip_path = out_dir / zip_name
    if zip_path.exists():
        # Overwrite — the operator may be rebuilding. The previous
        # contents are in version control or in the rollback archive.
        zip_path.unlink()

    print(f"  Writing        : {zip_path}")
    _build_zip(root, files, zip_path, version)
    print(f"  Built          : {zip_path.stat().st_size / (1024 * 1024):.2f} MB")

    # Verifier gate.
    print(f"  Verifying      : ...")
    report = verifier(str(zip_path))
    if not report.get("ok"):
        print(f"FAIL: zip drift vs source tree", file=sys.stderr)
        missing = report.get("missing_from_zip", [])
        extra = report.get("extra_in_zip", [])
        for m in missing[:10]:
            print(f"    MISSING  {m}", file=sys.stderr)
        if len(missing) > 10:
            print(f"    ... +{len(missing) - 10} more", file=sys.stderr)
        for x in extra[:10]:
            print(f"    EXTRA    {x}", file=sys.stderr)
        if len(extra) > 10:
            print(f"    ... +{len(extra) - 10} more", file=sys.stderr)
        return 1
    print(f"  Verifier       : {report.get('verdict', 'ok')}"
          f" ({report.get('zip_file_count', '?')} files)")

    # Posture gate (v3.66.59): the raw-capture inspection capability must
    # NEVER ship. The manifest exclusion already keeps bd_dev_inspect.py out,
    # but this asserts it explicitly against the built artifact — by name and
    # by the pass-through class DEFINITION (not mere references to the env
    # var, which legitimately appear in the shipped soft-import/docstring) —
    # so a future manifest regression, or a renamed copy, can't silently ship
    # a redaction-disable capability. Fails the build hard if found.
    import zipfile as _zf
    _FORBIDDEN_NAMES = ("bd_dev_inspect.py",)
    # Marker built from fragments so this gate's OWN source does not contain
    # the contiguous string it searches for (else it would flag
    # build_release.py itself). Matches the pass-through class DEFINITION,
    # which lives only in bd_dev_inspect.py (excluded) or a renamed copy.
    _FORBIDDEN_MARKERS = (("class _Pass" + "ThroughRedactor").encode(),)
    with _zf.ZipFile(zip_path) as _z:
        _names = [n for n in _z.namelist() if not n.endswith("/")]
        _bad_name = [n for n in _names
                     if n.split("/")[-1] in _FORBIDDEN_NAMES]
        _bad_content = []
        for n in _names:
            if not n.endswith(".py"):
                continue
            try:
                blob = _z.read(n)
            except Exception:
                continue
            if any(mk in blob for mk in _FORBIDDEN_MARKERS):
                _bad_content.append(n)
    if _bad_name or _bad_content:
        print("FAIL: raw-capture capability leaked into the release zip",
              file=sys.stderr)
        for n in _bad_name:
            print(f"    FORBIDDEN FILE     {n}", file=sys.stderr)
        for n in _bad_content:
            print(f"    FORBIDDEN MARKER   {n}", file=sys.stderr)
        return 1
    print("  Posture gate   : raw-capture capability absent from zip")

    # Wave 5 hygiene gate: critical frontend present in the built zip.
    print(f"  Frontend gate  : checking ...")
    if _run_frontend_required_gate(root, zip_path) != 0:
        print(f"FAIL: critical frontend missing/empty in the release zip "
              f"(see check_frontend_present output above).", file=sys.stderr)
        return 1
    print(f"  Frontend gate  : critical SPA files present")

    # Wave 5 hygiene gate (opt-in): regression + diff vs a previous release.
    if args.baseline:
        if not args.baseline.is_file():
            print(f"FAIL: --baseline {args.baseline} not found", file=sys.stderr)
            return 2
        print(f"  Baseline gates : comparing vs {args.baseline.name} ...")
        if _run_baseline_gates(root, zip_path, args.baseline,
                               args.approved_frontend) != 0:
            print(f"FAIL: baseline hygiene gate (frontend regression / release "
                  f"diff) reported a problem above.", file=sys.stderr)
            return 1
        print(f"  Baseline gates : no frontend drop / forbidden artifact / "
              f"changelog mismatch")
    else:
        print(f"  Baseline gates : skipped (no --baseline given)")

    # Extracted-suite gate.
    if args.skip_tests:
        print(f"  Suite          : SKIPPED (--skip-tests)")
    else:
        print(f"  Suite (from extracted zip): running ...")
        passed, summary = _run_extracted_suite(zip_path)
        print(f"  Suite          : {summary}")
        if not passed and not args.quick:
            print(f"FAIL: extracted-suite gate did not pass",
                  file=sys.stderr)
            return 1
        if not passed and args.quick:
            print(f"  WARNING: suite did not pass but --quick was set; "
                  f"emitting zip anyway. DO NOT release this.")

    print()
    print(f"OK: release zip ready at {zip_path}")
    print()
    print(f"Next steps:")
    print(f"  - Upload {zip_name} to stash")
    print(f"  - cd ~/BulkDownloader && unzip -o ~/{zip_name}")
    print(f"  - sudo systemctl restart bulkdownloader")
    print(f"  - ./capture.sh")
    print(f"  - python tools/rollback.py --archive {version} "
          f"--from {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
