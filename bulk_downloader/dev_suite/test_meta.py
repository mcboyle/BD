"""dev_suite.test_meta -- test infrastructure

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
    _pkg_dir, _repo_root)



# ── 45. test-meta cluster (U26: D-77 + D-69 + D-74) ────────────────
#
# Three read-only test-introspection tools.
#   • guard_test_status (D-77) — from a test_results.json artifact,
#     report how the guard/pin tests fared (the regression tripwires).
#   • test_coverage_map (D-69) — which bulk_downloader/*.py modules
#     have a matching tests/test_*.py file and which do not.
#   • test_run_diff (D-74) — diff the in-GUI runner's recent run
#     history (dev_tools.recent_runs): outcome + wall-clock per run.
#
# SCOPE NOTE — D-71 (test-timing report) is NOT in this unit. The
# backlog grouped it here assuming a shared timing artifact, but
# neither test_results.json nor dev_tools._runs records per-test or
# per-file timings — there is nothing to read. test_run_diff reports
# the only timing that IS captured: whole-run wall-clock. Per-test
# timing would need the runner to emit it first (a run_tests.py
# change), so D-71 is deferred rather than faked. See the U26 note.

# Guard/pin test files — regression tripwires. A test file counts as
# a guard if its name carries one of these markers.
_GUARD_TEST_MARKERS = ("guard", "_pin", "pin_", "invariant", "bounded",
                        "robustness", "_dropdown")



def _is_guard_test_file(filename):
    low = str(filename).lower()
    return any(m in low for m in _GUARD_TEST_MARKERS)



def _load_test_results(path=None):
    """Load a run_tests.py --json artifact. Returns (data, error)."""
    import os as _os
    import json as _json
    candidate = path or "test_results.json"
    if not _os.path.exists(candidate):
        return None, (f"no test-results artifact at '{candidate}' — "
                      f"run: python run_tests.py --json")
    try:
        with open(candidate, encoding="utf-8") as fh:
            return _json.load(fh), None
    except Exception as e:
        return None, f"could not read '{candidate}': {e}"



def guard_test_status(results_path=None):
    """D-77 — from a test_results.json artifact, report how the guard /
    pin tests did. Guard tests fail loudly when a fixed bug is
    reintroduced; this isolates their status from the wider suite.
    Read-only. Needs a prior `run_tests.py --json` artifact."""
    data, err = _load_test_results(results_path)
    if err:
        return {"tool": "guard_test_status", "ok": False, "error": err}
    failures = data.get("failures") or []
    skips = data.get("skips") or []
    guard_fails = [f for f in failures
                   if _is_guard_test_file(f.get("file", ""))]
    guard_skips = [s for s in skips
                   if _is_guard_test_file(s.get("file", ""))]
    guard_fail_files = sorted({f.get("file") for f in guard_fails})
    return {
        "tool": "guard_test_status",
        "ok": True,
        "artifact_version": data.get("version"),
        "artifact_timestamp": data.get("timestamp"),
        "suite_ok": bool(data.get("ok")),
        "guard_failures": guard_fails,
        "guard_skips": guard_skips,
        "guard_fail_files": guard_fail_files,
        "verdict": ("all guard tests holding"
                    if not guard_fails
                    else f"{len(guard_fails)} guard test(s) FAILING in "
                         f"{len(guard_fail_files)} file(s) — a fixed "
                         f"bug may have been reintroduced"),
    }



def test_coverage_map():
    """D-69 — map every bulk_downloader/*.py module against the
    tests/ directory: which modules have a matching test file and
    which have none. A heuristic name match, not line coverage.
    Read-only."""
    import os as _os
    here = str(_pkg_dir())
    repo = str(_repo_root())
    pkg_dir = here
    tests_dir = _os.path.join(repo, "tests")
    try:
        modules = sorted(
            f[:-3] for f in _os.listdir(pkg_dir)
            if f.endswith(".py") and not f.startswith("_"))
    except Exception as e:
        return {"tool": "test_coverage_map", "ok": False,
                "error": f"cannot list package: {e}"}
    try:
        test_files = [f for f in _os.listdir(tests_dir)
                      if f.startswith("test_") and f.endswith(".py")]
    except Exception as e:
        return {"tool": "test_coverage_map", "ok": False,
                "error": f"cannot list tests/: {e}"}
    test_blob = " ".join(t.lower() for t in test_files)
    covered, uncovered = [], []
    for mod in modules:
        # a module is "covered" if its name appears in any test
        # filename (test_<mod>.py, or test_<mod>_*.py, or the module
        # name embedded in a broader test file name)
        if mod.lower() in test_blob:
            covered.append(mod)
        else:
            uncovered.append(mod)
    return {
        "tool": "test_coverage_map",
        "ok": True,
        "module_count": len(modules),
        "test_file_count": len(test_files),
        "covered_modules": covered,
        "uncovered_modules": uncovered,
        "coverage_ratio": (round(len(covered) / len(modules), 3)
                           if modules else 0.0),
        "note": ("name-match heuristic — a module with no test file "
                 "named after it may still be exercised indirectly"),
    }



def test_run_diff(limit=10):
    """D-74 — diff the in-GUI test runner's recent run history: each
    run's target, outcome, and wall-clock duration, plus the delta
    between the two most recent. Read-only.

    This is also the only test-timing data that exists (whole-run
    wall-clock); per-test timing is not captured anywhere — see the
    U26 scope note on D-71."""
    try:
        from bulk_downloader import dev_tools as _dt
    except Exception as e:
        return {"tool": "test_run_diff", "ok": False,
                "error": f"dev_tools unavailable: {e}"}
    try:
        limit = max(2, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 10
    try:
        runs = _dt.recent_runs(limit=limit)
    except Exception as e:
        return {"tool": "test_run_diff", "ok": False,
                "error": f"recent_runs failed: {e}"}
    summarized = []
    for r in runs:
        started = r.get("started")
        finished = r.get("finished")
        dur = (round(finished - started, 2)
               if (started and finished) else None)
        summarized.append({
            "run_id": r.get("run_id"),
            "target": r.get("target"),
            "state": r.get("state"),
            "returncode": r.get("returncode"),
            "duration_seconds": dur,
        })
    diff = None
    if len(summarized) >= 2:
        # recent_runs is newest-first
        new, prev = summarized[0], summarized[1]
        state_changed = new["state"] != prev["state"]
        dd = None
        if (new["duration_seconds"] is not None
                and prev["duration_seconds"] is not None):
            dd = round(new["duration_seconds"]
                       - prev["duration_seconds"], 2)
        diff = {
            "newest": new["run_id"], "previous": prev["run_id"],
            "state_changed": state_changed,
            "newest_state": new["state"],
            "previous_state": prev["state"],
            "duration_delta_seconds": dd,
        }
    return {
        "tool": "test_run_diff",
        "ok": True,
        "run_count": len(summarized),
        "runs": summarized,
        "latest_diff": diff,
        "verdict": ("no run history yet — start a run from the dev "
                    "test runner" if not summarized
                    else (f"{len(summarized)} run(s) in history"
                          + (" — state changed since last run"
                             if diff and diff["state_changed"]
                             else ""))),
    }



# ── 51. parametrize fan-out counter + flaky-test detector (T1) ─────
#
# Two read-only test-introspection tools, extending the U26 cluster.
#   • parametrize_fanout (D-73) — statically scans tests/*.py and
#     counts how @pytest.mark.parametrize fans test METHODS into
#     counted cases. The custom run_tests.py expands @parametrize
#     ONLY on class methods (lesson C6) — so a module-level
#     parametrized function is a bug (called with no args). This tool
#     reports the real fan-out and flags any module-level misuse.
#   • flaky_test_detector (D-70) — diffs the failures across several
#     stored test_results.json artifacts; a test that fails in some
#     runs but not others is flaky. Needs a directory of artifacts
#     from prior `run_tests.py --json` runs.
# Both read-only.


def _count_parametrize_cases(decorator_node):
    """Given an ast decorator node for @pytest.mark.parametrize,
    return how many cases it declares (len of the values list), or
    None if it cannot be determined statically."""
    import ast as _ast
    if not isinstance(decorator_node, _ast.Call):
        return None
    # parametrize(argnames, [ (..), (..), ... ])  -> 2nd positional
    if len(decorator_node.args) < 2:
        return None
    values = decorator_node.args[1]
    if isinstance(values, (_ast.List, _ast.Tuple)):
        return len(values.elts)
    return None



def parametrize_fanout(tests_dir="tests"):
    """D-73 — scan tests/*.py for @pytest.mark.parametrize and report
    how it fans test methods into counted cases.

    The custom runner expands @parametrize only on CLASS METHODS; a
    parametrized module-level function is a defect. This reports the
    per-file fan-out and flags module-level misuse. Read-only static
    analysis — it does not run any test.
    """
    import ast as _ast
    import os as _os
    if not _os.path.isdir(tests_dir):
        return {"tool": "parametrize_fanout", "ok": False,
                "error": f"no tests directory at '{tests_dir}'"}
    per_file = []
    total_decorators = 0
    total_extra_cases = 0
    module_level_misuse = []
    for fn in sorted(_os.listdir(tests_dir)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        path = _os.path.join(tests_dir, fn)
        try:
            tree = _ast.parse(open(path, encoding="utf-8").read())
        except Exception as e:
            per_file.append({"file": fn, "error": str(e)[:120]})
            continue
        file_decos = 0
        file_cases = 0

        def _scan(node, in_class):
            nonlocal file_decos, file_cases
            for child in _ast.iter_child_nodes(node):
                if isinstance(child, _ast.ClassDef):
                    _scan(child, True)
                elif isinstance(child, _ast.FunctionDef):
                    for deco in child.decorator_list:
                        name = ""
                        d = deco.func if isinstance(deco, _ast.Call) \
                            else deco
                        if isinstance(d, _ast.Attribute):
                            name = d.attr
                        elif isinstance(d, _ast.Name):
                            name = d.id
                        if name != "parametrize":
                            continue
                        file_decos += 1
                        n = _count_parametrize_cases(deco)
                        if n is not None:
                            # n cases replace 1 method -> n-1 extra
                            file_cases += max(0, n - 1)
                        if not in_class:
                            module_level_misuse.append(
                                f"{fn}::{child.name}")
                else:
                    # do not descend into non-class/func nodes for
                    # nested funcs we still want module scope correct
                    pass

        _scan(tree, False)
        if file_decos:
            per_file.append({"file": fn,
                             "parametrize_decorators": file_decos,
                             "extra_cases": file_cases})
            total_decorators += file_decos
            total_extra_cases += file_cases
    return {
        "tool": "parametrize_fanout",
        "ok": True,
        "files_using_parametrize": len(
            [p for p in per_file if p.get("parametrize_decorators")]),
        "total_parametrize_decorators": total_decorators,
        "total_extra_cases_from_fanout": total_extra_cases,
        "module_level_misuse": module_level_misuse,
        "per_file": per_file,
        "note": ("the custom runner expands @parametrize only on "
                 "class methods; module_level_misuse lists "
                 "parametrized module-level functions, which the "
                 "runner calls with no args (a defect)"),
    }



def flaky_test_detector(artifacts_dir="test_artifacts"):
    """D-70 — diff failures across several stored test_results.json
    artifacts. A test that fails in some runs but passes in others is
    flaky. Point this at a directory holding multiple artifacts from
    prior `run_tests.py --json` runs.

    Read-only. With fewer than 2 artifacts there is nothing to diff.
    """
    import os as _os
    import json as _json
    if not _os.path.isdir(artifacts_dir):
        return {"tool": "flaky_test_detector", "ok": False,
                "error": (f"no artifacts directory at "
                          f"'{artifacts_dir}' — collect several "
                          f"run_tests.py --json results there first")}
    runs = []
    for fn in sorted(_os.listdir(artifacts_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(_os.path.join(artifacts_dir, fn),
                      encoding="utf-8") as fh:
                data = _json.load(fh)
        except Exception:
            continue
        fails = set()
        for f in (data.get("failures") or []):
            key = f.get("test") or f.get("name") or f.get("file")
            if key:
                fails.add(str(key))
        runs.append({"file": fn, "failed": fails,
                     "total": data.get("total")})
    if len(runs) < 2:
        return {"tool": "flaky_test_detector", "ok": False,
                "error": (f"need >=2 result artifacts to detect "
                          f"flakiness; found {len(runs)} in "
                          f"'{artifacts_dir}'")}
    # a test is flaky if it appears in SOME runs' failures but not all
    all_failed = set()
    for r in runs:
        all_failed |= r["failed"]
    flaky, always_fail = [], []
    for test in sorted(all_failed):
        hit = sum(1 for r in runs if test in r["failed"])
        if hit == len(runs):
            always_fail.append(test)
        else:
            flaky.append({"test": test, "failed_in": hit,
                          "of_runs": len(runs)})
    return {
        "tool": "flaky_test_detector",
        "ok": True,
        "runs_compared": len(runs),
        "flaky_tests": flaky,
        "consistently_failing": always_fail,
        "verdict": (f"{len(flaky)} flaky test(s) across "
                    f"{len(runs)} run(s)"
                    if flaky else
                    f"no flaky tests across {len(runs)} run(s)"),
    }



# ── 52. fixture-site controller (T2: D-72) ─────────────────────────
#
# D-72 — start / stop the tools/fixture_site*.py mock sites in-process
# for testing, instead of launching them by hand from a second
# terminal. An (A) state-changing tool: it spawns a server thread, so
# its routes are POST + CSRF + dev-gated.
#
# IMPORT-CLEAN: _FIXTURE_SERVERS is just an empty dict at import time
# (no work, no thread). A werkzeug server thread is created ONLY when
# fixture_site_start() is called — same discipline as perf_lab's load
# injector (DANGER_MAP: dev modules do no work at import).
#
# The two fixture apps:
#   site1 -> tools/fixture_site.py::make_app   (default port 8899)
#   site2 -> tools/fixture_site2.py::make_app  (default port 8898)

# name -> {server, thread, port, started}. Empty at import.
_FIXTURE_SERVERS: dict = {}


_FIXTURE_DEFS = {
    "site1": {"module": "fixture_site", "port": 8899},
    "site2": {"module": "fixture_site2", "port": 8898},
}



def _load_fixture_app(module_name):
    """Import tools/<module_name>.py and return its make_app(). The
    fixture modules live in tools/ and have no bulk_downloader
    dependency, so they are loaded by file path."""
    import importlib.util as _ilu
    import os as _os
    repo = str(_repo_root())
    path = _os.path.join(repo, "tools", module_name + ".py")
    if not _os.path.exists(path):
        return None, f"fixture module not found: {path}"
    try:
        spec = _ilu.spec_from_file_location(
            f"_bd_fixture_{module_name}", path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return None, f"could not load {module_name}: {e}"
    if not hasattr(mod, "make_app"):
        return None, f"{module_name} has no make_app()"
    return mod, None



def fixture_site_start(name="site1", port=None):
    """D-72 (A) — start a fixture mock site on a background thread.
    `name` is 'site1' or 'site2'. Idempotent: starting an
    already-running fixture just reports its state."""
    import threading as _th
    if name not in _FIXTURE_DEFS:
        return {"tool": "fixture_site_start", "ok": False,
                "error": (f"unknown fixture '{name}' — "
                          f"choose from {list(_FIXTURE_DEFS)}")}
    existing = _FIXTURE_SERVERS.get(name)
    if existing and existing.get("started"):
        return {"tool": "fixture_site_start", "ok": True,
                "name": name, "port": existing["port"],
                "already_running": True,
                "url": f"http://127.0.0.1:{existing['port']}"}
    spec = _FIXTURE_DEFS[name]
    use_port = int(port or spec["port"])
    mod, err = _load_fixture_app(spec["module"])
    if err:
        return {"tool": "fixture_site_start", "ok": False,
                "error": err}
    try:
        app = mod.make_app()
    except Exception as e:
        return {"tool": "fixture_site_start", "ok": False,
                "error": f"make_app() failed: {e}"}
    # a werkzeug server we can shut down cleanly
    try:
        from werkzeug.serving import make_server as _mk
        server = _mk("127.0.0.1", use_port, app, threaded=True)
    except Exception as e:
        return {"tool": "fixture_site_start", "ok": False,
                "error": (f"could not bind 127.0.0.1:{use_port} "
                          f"({e}) — port in use?")}
    thread = _th.Thread(target=server.serve_forever, daemon=True,
                        name=f"bd-fixture-{name}")
    thread.start()
    _FIXTURE_SERVERS[name] = {"server": server, "thread": thread,
                              "port": use_port, "started": True}
    return {"tool": "fixture_site_start", "ok": True, "name": name,
            "port": use_port, "already_running": False,
            "url": f"http://127.0.0.1:{use_port}"}



def fixture_site_stop(name="site1"):
    """D-72 (A) — stop a running fixture mock site. Idempotent:
    stopping one that is not running is a no-op success."""
    if name not in _FIXTURE_DEFS:
        return {"tool": "fixture_site_stop", "ok": False,
                "error": f"unknown fixture '{name}'"}
    entry = _FIXTURE_SERVERS.get(name)
    if not entry or not entry.get("started"):
        return {"tool": "fixture_site_stop", "ok": True,
                "name": name, "was_running": False}
    try:
        entry["server"].shutdown()
    except Exception as e:
        return {"tool": "fixture_site_stop", "ok": False,
                "error": f"shutdown failed: {e}"}
    entry["thread"].join(timeout=5)
    _FIXTURE_SERVERS.pop(name, None)
    return {"tool": "fixture_site_stop", "ok": True, "name": name,
            "was_running": True}



def fixture_site_status():
    """D-72 — read-only: which fixture sites are running."""
    sites = []
    for name, spec in _FIXTURE_DEFS.items():
        entry = _FIXTURE_SERVERS.get(name)
        running = bool(entry and entry.get("started")
                       and entry["thread"].is_alive())
        sites.append({
            "name": name,
            "module": spec["module"],
            "default_port": spec["port"],
            "running": running,
            "port": entry["port"] if running else None,
            "url": (f"http://127.0.0.1:{entry['port']}"
                    if running else None),
        })
    return {"tool": "fixture_site_status", "ok": True,
            "fixtures": sites,
            "running_count": sum(1 for s in sites if s["running"])}



# ── T42 / D-75 — golden-file manager ───────────────────────────────
#
# Read-only inspector. The regenerate side is a CLI script
# (tools/regenerate_goldens.py) — NOT an HTTP route, because
# regenerating goldens mutates test inputs.

def _goldens_dir(repo_root=None):
    """Where golden files live. Conventionally tests/fixtures/golden/
    under the repo root."""
    root = Path(repo_root) if repo_root else _repo_root()
    return root / "tests" / "fixtures" / "golden"



def golden_file_manager(repo_root=None):
    """T42 / D-75 — inventory golden files + diff each against the
    current candidate value, if a sibling .current file is provided.

    Layout convention:
      tests/fixtures/golden/<name>.golden       — the committed baseline
      tests/fixtures/golden/<name>.current      — (optional) current value
                                                  the test produces; if
                                                  present, we diff
      tests/fixtures/golden/<name>.golden.meta  — (optional) JSON metadata
                                                  (purpose, owner, last
                                                  regenerated ts)

    Read-only — never writes. The regenerate path is a separate CLI
    script the operator runs explicitly.

    Returns {tool, ok, goldens_dir, total, items[], drift_count, verdict}.
    """
    import hashlib as _hashlib
    import json as _json
    out = {
        "tool": "golden_file_manager",
        "ok": True,
        "goldens_dir": "",
        "total": 0,
        "items": [],
        "drift_count": 0,
        "verdict": "",
    }
    gdir = _goldens_dir(repo_root)
    out["goldens_dir"] = str(gdir)
    if not gdir.is_dir():
        # Not an error — the operator may not have created any
        # goldens yet. Report the convention so they know where.
        out["verdict"] = (f"no goldens dir at {gdir}; "
                          f"create it and add .golden files")
        return out
    for p in sorted(gdir.iterdir()):
        if not p.is_file():
            continue
        if not p.name.endswith(".golden"):
            # Skip .current and .meta sidecars in the top loop
            continue
        stem = p.name[: -len(".golden")]
        try:
            golden_bytes = p.read_bytes()
        except Exception as e:
            out["items"].append({
                "name": stem,
                "ok": False,
                "error": str(e)[:200],
            })
            continue
        golden_hash = _hashlib.sha256(golden_bytes).hexdigest()
        entry = {
            "name": stem,
            "ok": True,
            "golden_bytes": len(golden_bytes),
            "golden_sha256": golden_hash,
            "has_current": False,
            "drift": False,
        }
        current = gdir / f"{stem}.current"
        if current.is_file():
            try:
                current_bytes = current.read_bytes()
            except Exception:
                current_bytes = None
            if current_bytes is not None:
                entry["has_current"] = True
                entry["current_bytes"] = len(current_bytes)
                entry["current_sha256"] = (
                    _hashlib.sha256(current_bytes).hexdigest())
                if entry["current_sha256"] != entry["golden_sha256"]:
                    entry["drift"] = True
                    out["drift_count"] += 1
        meta = gdir / f"{stem}.golden.meta"
        if meta.is_file():
            try:
                entry["meta"] = _json.loads(
                    meta.read_text(encoding="utf-8"))
            except Exception:
                entry["meta"] = {"error": "meta file is not valid JSON"}
        out["items"].append(entry)
    out["total"] = len(out["items"])
    out["verdict"] = (
        f"{out['total']} golden file(s); "
        f"{out['drift_count']} drift(s) from current value(s)")
    return out



# ── 68. test-timing report (T50 / D-71) ────────────────────────────
#
# Reads the run_tests.py --json artifact and surfaces the slowest
# tests + per-file totals. Reads ONLY; no mutation. Schema gate:
# rejects artifacts older than schema_version 2 (which is the
# version where per-test `duration_seconds` was added — see T49).
#
# Old-artifact rejection is deliberate: the failure mode we want to
# avoid is silently returning a slowest-N list that's actually just
# "tests we have a duration for, rest defaulted to zero". An
# explicit refusal makes the operator re-run with the new runner.

def test_timing(results_path=None, top=30):
    """T50/D-71 — slowest tests + per-file totals from a
    `run_tests.py --json` artifact (schema_version >= 2).

    Returns: {tool, ok, schema_version, artifact_version,
              artifact_timestamp, total_tests,
              slowest: [{file, test, duration_seconds, status}, ...],
              by_file: [{file, count, total_seconds,
                         avg_seconds, max_seconds}, ...],
              verdict}.

    On missing/old artifact: ok=False with an explanatory error;
    no exception raised.
    """
    data, err = _load_test_results(results_path)
    if err:
        return {"tool": "test_timing", "ok": False, "error": err}
    schema = data.get("schema_version")
    if not isinstance(schema, int) or schema < 2:
        return {
            "tool": "test_timing", "ok": False,
            "error": ("artifact schema too old (need schema_version "
                      f">= 2, got {schema!r}) — re-run "
                      "`python run_tests.py --json` with the v3.63.1+ "
                      "runner to capture per-test durations"),
        }
    # Defensive: top must be a positive int. Mirror the defaulting
    # behaviour the route uses (so calling the function directly
    # from a notebook or test is equally forgiving).
    try:
        top_int = int(top)
        if top_int < 1:
            top_int = 30
    except (TypeError, ValueError):
        top_int = 30

    tests = data.get("tests") or []
    if not isinstance(tests, list):
        tests = []

    # Slowest-N (descending by duration).
    def _dur(rec):
        try:
            return float(rec.get("duration_seconds", 0.0))
        except (TypeError, ValueError):
            return 0.0
    slowest = sorted(tests, key=_dur, reverse=True)[:top_int]
    slowest_out = [{
        "file": r.get("file", ""),
        "test": r.get("test", ""),
        "duration_seconds": round(_dur(r), 4),
        "status": r.get("status", ""),
    } for r in slowest]

    # Per-file aggregation.
    by_file_acc = {}
    for r in tests:
        f = r.get("file", "")
        d = _dur(r)
        rec = by_file_acc.setdefault(
            f, {"file": f, "count": 0, "total_seconds": 0.0,
                "max_seconds": 0.0})
        rec["count"] += 1
        rec["total_seconds"] += d
        if d > rec["max_seconds"]:
            rec["max_seconds"] = d
    by_file = []
    for rec in by_file_acc.values():
        avg = (rec["total_seconds"] / rec["count"]
               if rec["count"] > 0 else 0.0)
        by_file.append({
            "file": rec["file"],
            "count": rec["count"],
            "total_seconds": round(rec["total_seconds"], 4),
            "avg_seconds": round(avg, 4),
            "max_seconds": round(rec["max_seconds"], 4),
        })
    by_file.sort(key=lambda r: r["total_seconds"], reverse=True)

    if not tests:
        verdict = ("artifact contained no test records — was it "
                   "written by an older runner?")
    else:
        verdict = (f"{len(tests)} tests timed; slowest "
                   f"{slowest_out[0]['duration_seconds']}s "
                   f"({slowest_out[0]['test']}); "
                   f"slowest file totals {by_file[0]['total_seconds']}s "
                   f"({by_file[0]['file']})")

    return {
        "tool": "test_timing",
        "ok": True,
        "schema_version": schema,
        "artifact_version": data.get("version"),
        "artifact_timestamp": data.get("timestamp"),
        "total_tests": len(tests),
        "slowest": slowest_out,
        "by_file": by_file,
        "verdict": verdict,
    }
