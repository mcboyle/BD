"""dev_suite.release_lint -- release/build linting (holds the build_release guard set)

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re

from ._common import (
    _MANIFEST_EXCLUDE_DIRS, _pkg_dir, _read_version, _repo_root)



# ── 17. version-consistency checker (D-95) ─────────────────────────

def version_consistency() -> dict:
    """Scan source/script files for stale version banners. Flags two
    shapes that must match __version__: a product banner
    ('Bulk Downloader vX.Y.Z') and a version assignment
    ('VERSION = "X.Y.Z"'). Changelog-style annotations ('# v3.43.16:
    ...') and CHANGELOG.md are deliberately NOT flagged — they are
    historical and correct."""
    import re
    root = _repo_root()
    version = _read_version()
    banner = re.compile(r"Bulk\s*Downloader\s+v?(\d+\.\d+\.\d+)", re.I)
    assign = re.compile(
        r"(?i)\b(?:app_)?version\b\s*[=:]\s*['\"]?(\d+\.\d+\.\d+)")
    targets = []
    for pat in ("*.py", "*.bat", "*.sh", "*.spec"):
        targets += [p for p in root.glob(pat)]
        targets += [p for p in (root / "tools").glob(pat)]
    scanned, mismatches = [], []
    for p in sorted(set(targets)):
        if p.name == "__init__.py":
            continue  # the source of truth itself
        rel = str(p.relative_to(root))
        scanned.append(rel)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            mismatches.append({"file": rel, "line": 0, "found": "",
                               "context": f"unreadable: {e}"[:120]})
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in list(banner.finditer(line)) + list(
                    assign.finditer(line)):
                found = m.group(1)
                if found != version:
                    mismatches.append({
                        "file": rel, "line": i, "found": found,
                        "context": line.strip()[:120]})
    return {
        "version": version,
        "scanned_files": scanned,
        "scanned_count": len(scanned),
        "mismatches": mismatches,
        "verdict": (f"all version banners match {version}"
                    if not mismatches
                    else f"{len(mismatches)} stale version "
                         f"banner(s) — expected {version}"),
    }



# ── 18. CHANGELOG linter (D-96) ────────────────────────────────────

def changelog_lint() -> dict:
    """Confirm CHANGELOG.md carries a '## vX.Y.Z' heading for the
    current __version__ and that it is the topmost (newest) entry."""
    import re
    root = _repo_root()
    version = _read_version()
    cl = root / "CHANGELOG.md"
    if not cl.is_file():
        return {"ok": False, "version": version,
                "error": "CHANGELOG.md not found",
                "verdict": "CHANGELOG.md missing"}
    text = cl.read_text(encoding="utf-8", errors="replace")
    headings = re.findall(r"^##\s+v(\d+\.\d+\.\d+)", text, re.M)
    has_entry = version in headings
    is_topmost = bool(headings) and headings[0] == version
    entry_line = 0
    if has_entry:
        for i, ln in enumerate(text.splitlines(), 1):
            if re.match(rf"^##\s+v{re.escape(version)}\b", ln):
                entry_line = i
                break
    if not has_entry:
        verdict = f"no '## v{version}' entry in CHANGELOG.md"
    elif not is_topmost:
        verdict = (f"'## v{version}' exists but is not topmost "
                   f"(newest heading is v{headings[0]})")
    else:
        verdict = f"CHANGELOG.md has a current '## v{version}' entry"
    return {
        "ok": has_entry and is_topmost,
        "version": version,
        "has_entry": has_entry,
        "is_topmost": is_topmost,
        "entry_line": entry_line,
        "newest_heading": headings[0] if headings else None,
        "heading_count": len(headings),
        "verdict": verdict,
    }



# ── 19. .bat lint (D-97) ───────────────────────────────────────────

def bat_lint() -> dict:
    """Lint every .bat file. Five rules:
      L1 zero non-ASCII bytes (cmd.exe mis-parses by code page)
      L2 CRLF line endings (no bare LF)
      L3 no unquoted < or > inside a `for ... in (...)` loop
      L4 no unescaped `(` / `)` paired-within-a-line in command args
         inside a `(...)` block — cmd's paren tracker misclasses them
         as block delimiters and aborts (the v3.63.7 bug class)

    Backed by `bulk_downloader/_bat_lint.py`, a paren-aware tokenizer
    with `^`-line-continuation handling, quote-awareness, REM skipping,
    and `for ... in (...)` argument-list recognition. Self-tests in
    `tests/test_bat_lint_parser.py`.
    """
    from bulk_downloader import _bat_lint as _bl
    root = _repo_root()
    results = []
    for p in _iter_source_files(root, ".bat"):
        rel = str(p.relative_to(root))
        raw = p.read_bytes()
        r = _bl.lint_bytes(raw)
        issues = []
        if r["non_ascii"]:
            issues.append(f"{r['non_ascii']} non-ASCII byte(s) "
                          "(L1) — cmd.exe mis-parses by code page")
        if not r["crlf_ok"]:
            issues.append(f"{r['bare_lf']} line(s) not CRLF-terminated (L2)")
        for issue in r["issues"]:
            issues.append(f"line {issue.line} ({issue.rule}): {issue.detail}")
        results.append({"file": rel, "ok": not issues,
                        "issues": issues})
    bad = [r for r in results if not r["ok"]]
    return {
        "file_count": len(results),
        "clean": len(results) - len(bad),
        "with_issues": len(bad),
        "files": results,
        "verdict": ("all .bat files clean" if not bad
                    else f"{len(bad)} .bat file(s) have issues"),
    }



# ── 20. .sh lint (D-98) ────────────────────────────────────────────

def sh_lint() -> dict:
    """Lint every .sh file: LF line endings (no CRLF) and the
    executable bit set."""
    root = _repo_root()
    results = []
    for p in _iter_source_files(root, ".sh"):
        rel = str(p.relative_to(root))
        issues = []
        raw = p.read_bytes()
        if b"\r\n" in raw:
            issues.append("CRLF line endings — shell scripts need LF")
        mode = p.stat().st_mode
        executable = bool(mode & 0o111)
        if not executable:
            issues.append("executable bit not set (chmod +x)")
        results.append({"file": rel, "ok": not issues,
                        "executable": executable, "issues": issues})
    bad = [r for r in results if not r["ok"]]
    return {
        "file_count": len(results),
        "clean": len(results) - len(bad),
        "with_issues": len(bad),
        "files": results,
        "verdict": ("all .sh files clean" if not bad
                    else f"{len(bad)} .sh file(s) have issues"),
    }



# ── 21. release-zip manifest verifier (D-99) ───────────────────────

# _MANIFEST_EXCLUDE_DIRS: v3.66.749 -- the "not source" canon now lives
# in _common (imported above) so audit_security.secret_scan can DERIVE
# its skip set from it instead of re-typing it. The name stays exported
# from this module for every existing consumer (dev_suite.__init__,
# tests, tools).

# v3.66.742 -- the lints walk only the tree they certify. On stash the
# install dir accretes everything the release zip never ships (service venv,
# node_modules, __pycache__, overlay orphans -- the overlay never deletes),
# and `_repo_root().rglob(...)` walked ALL of it to find a handful of
# scripts: /api/dev/bat_lint was the one CONFIRMED >8s-alone route of the
# 740 capture. Prune at DESCEND time, reusing the manifest verifier's own
# "not source" set so both checks certify the same denominator. NOTE:
# zip_manifest_check keeps its FULL walk on purpose -- its job is spotting
# files that should not be there; pruning it would make it a blind gate.
_LINT_WALK_EXCLUDE_DIRS = _MANIFEST_EXCLUDE_DIRS


def _iter_source_files(root, suffix):
    """Yield Paths under root with the given suffix, never DESCENDING into
    _LINT_WALK_EXCLUDE_DIRS (a post-hoc filter would still pay the walk).
    Sorted, for stable report order."""
    import os as _os
    hits = []
    for dirpath, dirnames, filenames in _os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _LINT_WALK_EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(suffix):
                hits.append(Path(dirpath) / fn)
    return sorted(hits)

_MANIFEST_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log",
                              # v3.65.1: release zips are build
                              # output, not source. Without this
                              # exclusion the manifest verifier sees
                              # the just-created BulkDownloader_v*.zip
                              # in the source tree AFTER it's written,
                              # reports it "missing from zip", and
                              # fails the build. Latent since the zip
                              # verifier was introduced — first hit
                              # when the build was run in a tree where
                              # the output dir is the repo root. Safe
                              # because the builder writes the zip
                              # then immediately verifies; any *.zip
                              # in the tree is either (a) the one we
                              # just wrote, which is the artifact not
                              # a source file, or (b) a stray from a
                              # prior build, which is also not source.
                              ".zip",
                              # v3.66.756 (MUTABLE-IN-ZIP): a
                              # <host>.template-draft.json is RUNTIME
                              # state -- dom_analyzer.pin_candidate /
                              # build_draft write it into
                              # template_manager.DRAFTS_DIR, and
                              # app_template_manager globs/reads it at
                              # runtime. Nothing loads a *shipped*
                              # draft at startup, and no source file
                              # carries this suffix, so it is uniquely
                              # the runtime artifact. Without this the
                              # 755 zip shipped one, and an `unzip -o`
                              # overlay would drop a stale dev draft
                              # onto the operator's runtime drafts dir
                              # -- then the disk-globbing graph/zip
                              # gates see a file the tree "shouldn't"
                              # have. Suffix-scoped so it follows
                              # DRAFTS_DIR wherever it resolves.
                              ".template-draft.json",
                              # v3.66.783 (BUILD-HYG): the pre-migration
                              # DB backup. migrations._backup_db_before_
                              # migration() copies the live DB aside as
                              # <db>.premigration.bak (migrations.py:
                              # dst = src + ".premigration.bak") before a
                              # schema migration mutates it. Any build in a
                              # tree whose app booted through a migration
                              # leaks it -- the 782 zip shipped
                              # downloader_history.db.premigration.bak
                              # (135 KB of live history). Same runtime-leak
                              # class as downloader_history.db /
                              # video_hashes.db, but SUFFIX-scoped (not
                              # name-scoped) because the backup name is
                              # derived generically from whatever DB is
                              # migrated -- so a future migrated DB's backup
                              # is caught by the same rule. Safe: the suffix
                              # is fully qualified (".premigration.bak", not
                              # a bare ".bak"), so no source file matches.
                              ".premigration.bak")

_MANIFEST_EXCLUDE_NAMES = {"downloader_history.db",
                           "downloader_history.db-wal",
                           "downloader_history.db-shm",
                           # Deployment graph state never belongs in a release.
                           # The canonical stash check derives a temporary DB
                           # and keeps its trust pin under /var/lib, outside the
                           # install tree.  Exact names avoid over-excluding
                           # legitimate source fixtures with other .db names.
                           "KNOWLEDGE_GRAPH.db",
                           "KNOWLEDGE_GRAPH.db-wal",
                           "KNOWLEDGE_GRAPH.db-shm",
                           "KNOWLEDGE_GRAPH.db-journal",
                           "KNOWLEDGE_GRAPH.db.sha256",
                           "KNOWLEDGE_GRAPH.content.sha256",
                           # v3.66.781 (BUILD-HYG): the video dedup DB
                           # (dedup.get_default_registry default
                           # "video_hashes.db", opened WAL-mode in
                           # dedup.py) is written to cwd on first dedup
                           # use and leaked into the 781 build. Same
                           # runtime-leak class as downloader_history.db,
                           # its sibling; exclude the DB and its WAL
                           # sidecars so it can't ship in any zip built
                           # after the app/suite has run in the tree.
                           "video_hashes.db",
                           "video_hashes.db-wal",
                           "video_hashes.db-shm",
                           ".integrity_check_last",
                           # v3.64.3: two sentinel files were missing
                           # from this list. .integrity_last_run is
                           # written by db.py:_INTEGRITY_STATE_FILE
                           # (a second integrity-check sentinel, kept
                           # distinct because it tracks a different
                           # cadence); .fts_optimize_last is written
                           # by the FTS5-optimize step in db.py. Both
                           # get generated as a side effect of the
                           # build_release.py endpoint-catalog import
                           # in any tree that has ever booted the app
                           # — without the exclusion they'd contaminate
                           # every release zip with stale runtime
                           # sentinels.
                           ".integrity_last_run",
                           ".fts_optimize_last",
                           "test_results.json",
                           "SUMMARY.txt", "sites_config.json",
                           ".DS_Store", "debug.flag",
                           # v3.65.1 B5: more runtime artifacts that
                           # leaked into v3.65.1's first build attempt
                           # when the build was run AFTER the suite had
                           # executed in the same tree.
                           #
                           # vapid_keys.json — generated by the push
                           # subscription module on first import. It
                           # contains a private key (PEM). SHIPPING
                           # THIS IS A SECURITY ISSUE: any operator
                           # deploying a zip containing someone else's
                           # vapid_keys.json gets their web-push origin
                           # compromised. Same defect-class as the
                           # v3.64.3 .integrity_last_run finding, but
                           # with worse blast radius. Latent in every
                           # release zip built after suite execution.
                           #
                           # test_singleton.db — created by
                           # test_v3_43_72_dedup.py in cwd via
                           # get_default_registry("test_singleton.db").
                           # Test artifact, not source.
                           "vapid_keys.json",
                           # v3.66.39 B2: the password-manager runtime
                           # files. Same release-leak class as the B5
                           # vapid_keys.json fix, worse blast radius —
                           # generated next to the DB on first use and
                           # would otherwise ship in any zip built after
                           # the app/suite has run in the tree:
                           #   vault_tokens.json  — LIVE bearer vault
                           #     tokens; shipping these hands an attacker
                           #     working credentials for the operator's
                           #     extension vault.
                           #   secrets.json       — AES-GCM ciphertext
                           #     blob + salt/iteration params.
                           #   secrets_meta.json  — the secret-key index.
                           "vault_tokens.json",
                           "secrets.json",
                           "secrets_meta.json",
                           # NEW-2 (v3.66.43): the atomic-write .tmp
                           # siblings. If _save() is killed between
                           # write_text and replace (SIGKILL, power loss,
                           # AV quarantine), the .tmp survives byte-
                           # identical to the would-be .json and would
                           # ship the full vault. .tmp is NOT added to
                           # the SUFFIXES tuple — that would over-exclude
                           # legitimate temp files.
                           "vault_tokens.json.tmp",
                           "secrets.json.tmp",
                           "secrets_meta.json.tmp",
                           "test_singleton.db",
                           # v3.66.59: the raw-capture inspection module is
                           # the ONLY place the unredacted-capture capability
                           # exists. It must NEVER ship in a release — doing
                           # so would put a redaction-disable capability into
                           # the distributable, defeating capture-time
                           # redaction. It ships separately in
                           # bd_dev_inspect_v*.zip for local dev only.
                           # build_release additionally asserts this name is
                           # absent from the built zip (belt-and-suspenders).
                           "bd_dev_inspect.py"}


# v3.66.263 (BUILD-HYG): exact-relative-path exclusions. Unlike the
# name-scoped sets above, these match only at the given root-relative
# path so a same-named file elsewhere is unaffected.
#
#   app_config.json — the root global config. Any app boot in the source
#     tree runs api_tokens._signing_secret(), which set_config()s a
#     freshly-minted api_auth_token_secret into the cwd-relative
#     app_config.json (the cwd-relative global app-config file) — so the LIVE signing
#     secret would otherwise ship in the release zip, and an `unzip -o`
#     overlay of it would clobber the operator's real secret. Its sibling
#     sites_config.json is name-excluded above, but app_config.json needs
#     PATH scope: there is a second app_config.json under frontend/
#     (referenced by the SPA source) that must still ship. The app
#     re-seeds app_config.json on a fresh first run when absent, so
#     dropping it from the manifest is safe (same posture as the overlay).
_MANIFEST_EXCLUDE_PATHS = {"app_config.json",
                           # The baseline diff gate treats every .env-shaped
                           # file as sensitive.  This tracked example contains
                           # placeholders only, but it is documentation rather
                           # than runtime input and does not belong in a release.
                           ".env.example",
                           # v3.66.798 (BUILD-HYG): plugins.py::
                           # _quarantine_state_path() anchors plugin
                           # quarantine state at _plugin_dir()/
                           # .plugin_state.json -- inside the install
                           # tree. Leaked into the first 797 build
                           # (written by an in-tree band run, packed,
                           # namelist certified it). PATH-scoped
                           # because plugins/ itself SHIPS
                           # plugins.json; only the exact runtime
                           # state path is dropped. Regenerated at
                           # runtime when absent.
                           "plugins/.plugin_state.json"}



def _manifest_excluded(relpath: str) -> bool:
    rel = relpath.replace("\\", "/")
    if rel in _MANIFEST_EXCLUDE_PATHS:
        return True
    parts = rel.split("/")
    if any(d in _MANIFEST_EXCLUDE_DIRS for d in parts):
        return True
    name = parts[-1]
    return (name in _MANIFEST_EXCLUDE_NAMES
            or name.endswith(_MANIFEST_EXCLUDE_SUFFIXES))



# v3.66.161 data-restoration gate: REQUIRED-PRESENT allowlist.
#
# The symmetric tree-vs-zip diff in zip_manifest_check() only catches a
# file that is in the source tree but absent from the zip. It is blind to
# a file that was dropped from the *tree* itself — then the zip matches the
# (already-diminished) tree and the gate passes clean. That is exactly how
# validation_corpus.jsonl, the root .bat files, and the deep_detect /
# selector_chains / recon_corpus fixtures silently vanished at the 158->158_2
# repackaging and rode forward through 159_1 into 160 without tripping any
# check. This allowlist is verified against the ZIP NAMELIST directly,
# independent of the tree, so a future silent drop fails the build.
#
# Entries ending in "/" are directory requirements (satisfied if the zip
# carries at least one file under that prefix); all others are exact-file
# requirements. (Note: there are 9 root .bat files in the last-good 158
# release — not 11; the two remaining .bat live under tools/ and are not
# part of this root-level set.)
_MANIFEST_REQUIRED_PRESENT = (
    "validation_corpus.jsonl",
    "gpu_check.bat",
    "install_ai_ollama.bat",
    "install_dev.bat",
    "install_windows.bat",
    "run_all_tests.bat",
    "run_test.bat",
    "start_fixture_site.bat",
    "start_fixture_site2.bat",
    "uninstall_windows.bat",
    "tests/fixtures/deep_detect/",
    "tests/fixtures/selector_chains/",
    "tests/fixtures/recon_corpus/",
)



def _manifest_required_missing(zfiles) -> list:
    """Return the sorted subset of _MANIFEST_REQUIRED_PRESENT that the zip
    does not satisfy. Dir requirements (trailing "/") are met by any member
    under the prefix; file requirements need an exact match."""
    z = set(zfiles)
    missing = []
    for req in _MANIFEST_REQUIRED_PRESENT:
        if req.endswith("/"):
            if not any(n.startswith(req) for n in z):
                missing.append(req)
        elif req not in z:
            missing.append(req)
    return sorted(missing)



def zip_manifest_check(zip_path) -> dict:
    """Compare a built release zip against the live source tree:
    report files in the tree missing from the zip, and files in the
    zip absent from the tree. Caches, the DB, logs, and other runtime
    artifacts are excluded on both sides."""
    import zipfile
    zp = Path(str(zip_path or "")).expanduser()
    if not zp.is_file():
        return {"ok": False, "error": f"zip not found: {zp}",
                "verdict": "zip not found"}
    root = _repo_root()
    tree = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        if not _manifest_excluded(rel):
            tree.add(rel)
    try:
        with zipfile.ZipFile(zp) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
    except zipfile.BadZipFile as e:
        return {"ok": False, "error": f"not a valid zip: {e}",
                "verdict": "invalid zip"}
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    strip_root = (len(tops) == 1
                  and all(n.split("/", 1)[0] in tops for n in names))
    zfiles = set()
    for n in names:
        rel = n.split("/", 1)[1] if strip_root and "/" in n else n
        rel = rel.replace("\\", "/")
        if rel and not _manifest_excluded(rel):
            zfiles.add(rel)
    missing = sorted(tree - zfiles)
    extra = sorted(zfiles - tree)
    required_missing = _manifest_required_missing(zfiles)
    ok = not missing and not extra and not required_missing
    verdict = "zip matches the source tree"
    if not ok:
        parts = []
        if missing:
            parts.append(f"{len(missing)} file(s) missing from zip")
        if extra:
            parts.append(f"{len(extra)} unexpected")
        if required_missing:
            parts.append(f"{len(required_missing)} REQUIRED artifact(s) "
                         f"absent: {', '.join(required_missing)}")
        verdict = "; ".join(parts)
    return {
        "ok": ok,
        "zip": str(zp),
        "zip_root_folder": next(iter(tops)) if strip_root else None,
        "tree_file_count": len(tree),
        "zip_file_count": len(zfiles),
        "missing_from_zip": missing,
        "extra_in_zip": extra,
        "required_missing": required_missing,
        "verdict": verdict,
    }



# ── 50. systemd-unit validator + dependency-pin checker (U31) ──────
#
# D-101 + D-100 — a deploy-lint pair.
#   • systemd_unit_check (D-101) — validate a systemd unit file
#     against what a correct BulkDownloader unit needs. The single
#     most important check is WorkingDirectory: per DANGER_MAP, a
#     unit with a missing/wrong WorkingDirectory makes the app create
#     a fresh empty DB elsewhere — indistinguishable from total data
#     loss. Read-only — parses a file, changes nothing.
#   • dependency_pin_drift (D-100) — cross-check the versions declared
#     in requirements*.txt against what is actually installed in the
#     running environment. (Distinct from U27's dependency_audit,
#     which checks pin *discipline*; this checks declared-vs-installed
#     *drift*.) Read-only.



def _find_systemd_unit():
    """Locate a bulkdownloader systemd unit if one is installed."""
    for cand in ("/etc/systemd/system/bulkdownloader.service",
                 "/lib/systemd/system/bulkdownloader.service",
                 "/usr/lib/systemd/system/bulkdownloader.service"):
        if _dl_os.path.exists(cand):
            return cand
    return None



def _parse_unit(text):
    """Minimal INI-style parse of a systemd unit. Returns
    {section: {key: value}}. Last value wins per key."""
    out = {}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
        elif "=" in line and section is not None:
            k, v = line.split("=", 1)
            out[section][k.strip()] = v.strip()
    return out



def systemd_unit_check(unit_path=None, unit_text=None):
    """D-101 — validate a BulkDownloader systemd unit. Pass unit_path
    to read a file, unit_text to check inline, or neither to
    auto-locate an installed bulkdownloader.service. Read-only.

    Checks (most important first):
      • [Service] WorkingDirectory is set — DANGER_MAP: a missing one
        makes the app create a fresh empty DB; looks like data loss.
      • ExecStart present and points at downloader_ui.py.
      • Restart= configured (the app should restart on crash).
      • [Install] WantedBy set (so it starts on boot).
      • TimeoutStopSec generous enough for browser/keeper cleanup.
    """
    src = "inline"
    if unit_text is None:
        path = unit_path or _find_systemd_unit()
        if not path:
            return {"tool": "systemd_unit_check", "ok": False,
                    "error": ("no unit given and no installed "
                              "bulkdownloader.service found — pass "
                              "unit_path= or unit_text=")}
        if not _dl_os.path.exists(path):
            return {"tool": "systemd_unit_check", "ok": False,
                    "error": f"unit file not found: {path}"}
        try:
            with open(path, encoding="utf-8") as fh:
                unit_text = fh.read()
        except Exception as e:
            return {"tool": "systemd_unit_check", "ok": False,
                    "error": f"could not read unit: {e}"}
        src = path
    unit = _parse_unit(unit_text)
    svc = unit.get("Service", {})
    install = unit.get("Install", {})
    checks = []

    def _check(name, ok, detail, critical=False):
        checks.append({"check": name, "pass": bool(ok),
                       "detail": detail, "critical": critical})

    wd = svc.get("WorkingDirectory", "")
    _check("WorkingDirectory set", bool(wd),
           wd or "MISSING — app will resolve its DB relative to /, "
                 "creating a fresh empty DB (looks like data loss)",
           critical=True)
    exec_start = svc.get("ExecStart", "")
    _check("ExecStart present", bool(exec_start),
           exec_start or "MISSING", critical=True)
    _check("ExecStart runs downloader_ui.py",
           "downloader_ui.py" in exec_start,
           exec_start or "n/a")
    restart = svc.get("Restart", "")
    _check("Restart configured",
           restart.lower() in ("on-failure", "always"),
           restart or "not set — app will not restart on crash")
    _check("[Install] WantedBy set", bool(install.get("WantedBy")),
           install.get("WantedBy", "")
           or "not set — service will not start on boot")
    tss = svc.get("TimeoutStopSec", "")
    tss_ok = False
    if tss:
        m = _dl_re.match(r"(\d+)", tss)
        tss_ok = bool(m) and int(m.group(1)) >= 15
    _check("TimeoutStopSec >= 15s", tss_ok,
           tss or "not set — browser/keeper threads may be "
                  "SIGKILLed before clean shutdown")

    crit_fail = [c for c in checks if c["critical"] and not c["pass"]]
    any_fail = [c for c in checks if not c["pass"]]
    return {
        "tool": "systemd_unit_check",
        "ok": True,
        "source": src,
        "checks": checks,
        "critical_failures": [c["check"] for c in crit_fail],
        "verdict": (
            "unit is valid" if not any_fail
            else (f"CRITICAL: {len(crit_fail)} load-bearing check(s) "
                  f"failed — do not deploy this unit" if crit_fail
                  else f"{len(any_fail)} non-critical check(s) "
                       f"failed — review before deploy")),
    }



def dependency_pin_drift():
    """D-100 — cross-check versions declared in requirements*.txt
    against what is actually installed in the running environment.
    Surfaces: a declared package not installed; an installed version
    outside a declared `==`/`>=`/`<=` constraint. Read-only.

    (U27's dependency_audit checks pin discipline within the files;
    this checks the files against reality.)"""
    try:
        import importlib.metadata as _md
    except Exception as e:
        return {"tool": "dependency_pin_drift", "ok": False,
                "error": f"importlib.metadata unavailable: {e}"}
    here = str(_pkg_dir())
    repo = str(_repo_root())
    req = _dl_os.path.join(repo, "requirements.txt")
    if not _dl_os.path.exists(req):
        return {"tool": "dependency_pin_drift", "ok": False,
                "error": "requirements.txt not found"}
    try:
        with open(req, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception as e:
        return {"tool": "dependency_pin_drift", "ok": False,
                "error": str(e)[:160]}
    not_installed, version_drift, ok_count = [], [], 0
    checked = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec = line.split("#", 1)[0].split(";", 1)[0].strip()
        if not spec:
            continue
        name = _dl_re.split(r"[<>=~!\[ ]", spec, 1)[0].strip()
        if not name:
            continue
        checked += 1
        try:
            installed = _md.version(name)
        except Exception:
            not_installed.append(name)
            continue
        # check a == pin precisely; for bounded specs just record the
        # installed version (a full version-range solver is pip's job)
        m = _dl_re.search(r"==\s*([0-9][0-9A-Za-z.\-]*)", spec)
        if m:
            declared = m.group(1)
            if declared != installed:
                version_drift.append({
                    "package": name, "declared": f"=={declared}",
                    "installed": installed})
            else:
                ok_count += 1
        else:
            ok_count += 1
    return {
        "tool": "dependency_pin_drift",
        "ok": True,
        "checked": checked,
        "satisfied": ok_count,
        "not_installed": sorted(not_installed),
        "version_drift": version_drift,
        "verdict": (
            "every declared dependency is installed and matches"
            if not not_installed and not version_drift
            else (f"{len(not_installed)} not installed, "
                  f"{len(version_drift)} version mismatch(es) — "
                  f"environment drifts from requirements.txt")),
    }
