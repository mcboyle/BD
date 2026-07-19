"""dd-replay -- snapshot-replay harness for the deep_detect fixture corpus.

Walks `tests/fixtures/deep_detect/<name>/`, runs `deep_detect()` on each
fixture's `input.html` with the `meta.json` parameters, and compares the
result against `expected.json`. Reports unified diffs for any mismatches.

Layout per fixture:
    tests/fixtures/deep_detect/<NN_name>/
        input.html          page HTML (or manifest text)
        meta.json           {"description": "...", "base_url": "...",
                             "prefer_resolution": "..."?}
        expected.json       normalized deep_detect() report
                            (auto-generated; commit alongside input)

Usage:
    python3 tools/dd-replay.py                  # run, fail on mismatch
    python3 tools/dd-replay.py --update         # rewrite expected.json
    python3 tools/dd-replay.py --only NAME      # filter by fixture name
    python3 tools/dd-replay.py --list           # list fixtures + summary
    python3 tools/dd-replay.py -q | --quiet     # only print failures

Exit codes:
    0 — all fixtures match (or --update succeeded)
    1 — at least one mismatch
    2 — usage / I/O error

Added: v3.66.13 (P2). Reused by `bdctl dd-diff` (P10) and the
heuristic-strength score audit (P11) — keep the normalization in
`_normalize_report` consistent with what those tools assume.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "deep_detect"

# Add repo root to sys.path so `from bulk_downloader.deep_detect import ...`
# resolves whether you run from the repo root or from anywhere else.
sys.path.insert(0, str(REPO_ROOT))


def _normalize_report(report: dict) -> dict:
    """Strip / canonicalize anything non-deterministic in the report.

    deep_detect's offline path is already deterministic in v3.66.13
    (no timestamps, no random IDs, dict iteration order is insertion-
    order which is stable per-input). This function exists so future
    additions to the report schema can be filtered without rewriting
    every expected.json.

    Current normalizations:
      * None — placeholder. Add fields here when they start carrying
        run-specific data (e.g. timing, request IDs).
    """
    return report


def _load_fixture(fixture_dir: Path) -> tuple[str, dict]:
    """Return (html, meta) for a fixture. Raises FileNotFoundError if
    either input.html or meta.json is missing."""
    html = (fixture_dir / "input.html").read_text()
    meta = json.loads((fixture_dir / "meta.json").read_text())
    return html, meta


def _run_fixture(fixture_dir: Path) -> dict:
    """Run deep_detect on a single fixture and return the normalized
    report dict. Lazy import so --list / --help don't pay the import
    cost."""
    from bulk_downloader.deep_detect import deep_detect

    html, meta = _load_fixture(fixture_dir)
    kwargs: dict[str, Any] = {"base_url": meta.get("base_url", "")}
    if "prefer_resolution" in meta:
        kwargs["prefer_resolution"] = meta["prefer_resolution"]
    return _normalize_report(deep_detect(html, **kwargs))


def _diff(expected: dict, actual: dict, fixture_name: str) -> str:
    """Return a unified diff between two dicts (rendered as JSON).
    Empty string if equal."""
    e = json.dumps(expected, indent=2, sort_keys=True).splitlines(
        keepends=True)
    a = json.dumps(actual, indent=2, sort_keys=True).splitlines(
        keepends=True)
    if e == a:
        return ""
    return "".join(difflib.unified_diff(
        e, a,
        fromfile=f"{fixture_name}/expected.json",
        tofile=f"{fixture_name}/actual",
        n=3,
    ))


def _list_fixtures(only: str | None = None) -> list[Path]:
    if not CORPUS_DIR.is_dir():
        return []
    fixtures = sorted(d for d in CORPUS_DIR.iterdir()
                      if d.is_dir() and (d / "meta.json").exists())
    if only:
        fixtures = [d for d in fixtures if only in d.name]
    return fixtures


def cmd_list(quiet: bool, only: str | None) -> int:
    fixtures = _list_fixtures(only)
    if not fixtures:
        sys.stderr.write(f"No fixtures found in {CORPUS_DIR}\n")
        return 2
    for d in fixtures:
        meta = json.loads((d / "meta.json").read_text())
        desc = meta.get("description", "")
        has_expected = (d / "expected.json").exists()
        marker = " " if has_expected else "*"
        print(f"  {marker} {d.name:30s} {desc}")
    if any(not (d / "expected.json").exists() for d in fixtures):
        print()
        print("  (* = no expected.json — run with --update to create)")
    return 0


def cmd_update(quiet: bool, only: str | None) -> int:
    fixtures = _list_fixtures(only)
    if not fixtures:
        sys.stderr.write(f"No fixtures found in {CORPUS_DIR}\n")
        return 2
    for d in fixtures:
        try:
            report = _run_fixture(d)
        except Exception as e:
            sys.stderr.write(f"  ERROR {d.name}: {e}\n")
            return 2
        out_path = d / "expected.json"
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True)
                            + "\n")
        if not quiet:
            print(f"  WROTE {d.name}/expected.json")
    if not quiet:
        print(f"\nupdated {len(fixtures)} fixture(s)")
    return 0


def cmd_run(quiet: bool, only: str | None) -> int:
    fixtures = _list_fixtures(only)
    if not fixtures:
        sys.stderr.write(f"No fixtures found in {CORPUS_DIR}\n")
        return 2
    failed: list[str] = []
    skipped: list[str] = []
    for d in fixtures:
        expected_path = d / "expected.json"
        if not expected_path.exists():
            skipped.append(d.name)
            if not quiet:
                print(f"  SKIP  {d.name} (no expected.json; run --update)")
            continue
        try:
            actual = _run_fixture(d)
        except Exception as e:
            failed.append(d.name)
            print(f"  FAIL  {d.name}: {e}")
            continue
        expected = json.loads(expected_path.read_text())
        diff = _diff(expected, actual, d.name)
        if diff:
            failed.append(d.name)
            print(f"  FAIL  {d.name}")
            sys.stdout.write(diff)
        elif not quiet:
            print(f"  OK    {d.name}")
    n_total = len(fixtures)
    n_ok = n_total - len(failed) - len(skipped)
    summary = f"\n{n_ok}/{n_total} OK"
    if failed:
        summary += f", {len(failed)} FAILED"
    if skipped:
        summary += f", {len(skipped)} SKIPPED"
    print(summary)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--update", action="store_true",
                   help="rewrite expected.json from current deep_detect output")
    p.add_argument("--list", action="store_true",
                   help="list fixtures + descriptions")
    p.add_argument("--only", default=None,
                   help="filter fixtures by name substring")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="only print failures / changes")
    args = p.parse_args(argv)

    if args.list:
        return cmd_list(args.quiet, args.only)
    if args.update:
        return cmd_update(args.quiet, args.only)
    return cmd_run(args.quiet, args.only)


if __name__ == "__main__":
    sys.exit(main())
