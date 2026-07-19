#!/usr/bin/env python3
"""legacy_parity -- the legacy-UI migration ratchet gate.

Measures the API surface the legacy shell (templates/index.html +
static/*.js) calls that the D3 SPA (frontend/src) does NOT, and gates it
against a committed baseline so the gap can only shrink, never grow.

Companion to docs/LEGACY_MIGRATION_PLAN.md. Same philosophy as
tools/nav_reachability.py: lives as a TEST (tests/test_legacy_parity.py),
deliberately NOT inside tools/build_release.py (that file is a release guard).

Modes
-----
  (default)           human report: counts, families, full legacy-only list
  --json              machine-readable report on stdout
  --check             GATE: exit 1 if any legacy-only endpoint is NOT in the
                      committed baseline (a regression -- somebody added a new
                      legacy-only call). Endpoints migrated since the baseline
                      are reported as ratchet-available progress, exit 0.
  --write-baseline    ratchet: rewrite reports/legacy_parity_baseline.json to
                      the CURRENT legacy-only set (run after a tranche lands,
                      eyeball the shrink, commit). Refuses to GROW the baseline
                      unless --allow-grow is also passed.
  --report            also write reports/legacy_parity.md (human snapshot)

Exit codes: 0 OK / gate pass; 1 gate fail or grow-refused; 2 usage/IO error.

Scanner notes (deterministic, literal-based -- same philosophy as
gui_parity_inventory): finds '/api/...' and '/cockpit/api/...' string
literals; template params (${...}) collapse to {x}; anything after the first
{x} collapses too, so /api/sites/${sid}/edit and /api/sites/${eid} are ONE
endpoint /api/sites/{x}. Known limit: endpoints built by string concatenation
from a non-literal base are invisible (project rule: SPA wiring must use full
/api/... literals, so the SPA side is reliable by convention; the legacy side
over-counting is acceptable -- it only makes the gate stricter).

Stdlib-only (chain-CLI convention; plain python3 runs it).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "reports" / "legacy_parity_baseline.json"
REPORT_MD_PATH = REPO / "reports" / "legacy_parity.md"

LEGACY_GLOBS = ["bulk_downloader/static/*.js"]
LEGACY_FILES = ["bulk_downloader/templates/index.html"]
SPA_GLOBS = ["frontend/src/**/*.tsx", "frontend/src/**/*.ts"]

_EP_RE = re.compile(r"['\"`](/(?:cockpit/)?api/[A-Za-z0-9_\-/{$]*)")
# NOTE: _EP_RE's class has no '}' so a template literal like /api/sites/${sid}
# captures TRUNCATED as '/api/sites/${sid' -- collapse from the '${' onward.
_PARAM_RE = re.compile(r"\$\{.*")
_TAIL_RE = re.compile(r"/\{x\}.*")


def _collapse(raw: str) -> str:
    ep = _PARAM_RE.sub("{x}", raw).rstrip("/")
    ep = _TAIL_RE.sub("/{x}", ep)
    if ep.endswith("{x}") and not ep.endswith("/{x}"):
        # glued suffix interpolation (e.g. /api/widgets/data${scopeQuery} --
        # a querystring, not a path segment): the endpoint is the prefix.
        ep = ep[:-len("{x}")].rstrip("/")
    return ep


def _scan_files(files) -> set:
    eps = set()
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for m in _EP_RE.finditer(text):
            eps.add(_collapse(m.group(1)))
    return eps


_LEGACY_SURVIVORS = {"sw.js", "manifest.json"}  # retained by the SPA (push/PWA), not legacy


def _legacy_files():
    out = []
    for g in LEGACY_GLOBS:
        out.extend(sorted(p for p in REPO.glob(g)
                          if p.name not in _LEGACY_SURVIVORS))
    for f in LEGACY_FILES:
        p = REPO / f
        if p.exists():
            out.append(p)
    return out


def _spa_files():
    out = []
    for g in SPA_GLOBS:
        out.extend(sorted(REPO.glob(g)))
    return out


def family(ep: str) -> str:
    if ep.startswith("/cockpit/api/"):
        return "cockpit"
    parts = ep.split("/")
    return parts[2] if len(parts) > 2 else ep


def measure() -> dict:
    legacy = _scan_files(_legacy_files())
    spa = _scan_files(_spa_files())
    legacy_only = sorted(legacy - spa)
    fams: dict = {}
    for ep in legacy_only:
        fams.setdefault(family(ep), []).append(ep)
    return {
        "legacy_total": len(legacy),
        "spa_total": len(spa),
        "legacy_only_count": len(legacy_only),
        "family_count": len(fams),
        "legacy_only": legacy_only,
        "families": {k: sorted(v) for k, v in sorted(fams.items())},
    }


def load_baseline() -> list | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        data = json.loads(BASELINE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return sorted(data.get("legacy_only", []))


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def write_baseline(res: dict) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "legacy_parity ratchet baseline -- the gate fails on any "
                "legacy-only endpoint NOT in this list. Shrink via "
                "--write-baseline after each migration tranche; never "
                "hand-grow it.",
        "legacy_only_count": res["legacy_only_count"],
        "legacy_only": res["legacy_only"],
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def write_report_md(res: dict, baseline) -> None:
    lines = ["# legacy_parity report", ""]
    lines.append(f"- legacy endpoints (literal scan): **{res['legacy_total']}**")
    lines.append(f"- SPA endpoints (literal scan): **{res['spa_total']}**")
    lines.append(f"- **legacy-only: {res['legacy_only_count']}** across "
                 f"{res['family_count']} URL families")
    if baseline is not None:
        migrated = sorted(set(baseline) - set(res["legacy_only"]))
        lines.append(f"- baseline: {len(baseline)} | migrated since baseline: "
                     f"{len(migrated)}")
    lines.append("")
    for fam, eps in res["families"].items():
        lines.append(f"## {fam} ({len(eps)})")
        for ep in eps:
            lines.append(f"- `{ep}`")
        lines.append("")
    REPORT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD_PATH.write_text("\n".join(lines))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--allow-grow", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)

    res = measure()
    baseline = load_baseline()

    if a.write_baseline:
        if baseline is not None and res["legacy_only_count"] > len(baseline) \
                and not a.allow_grow:
            grown = sorted(set(res["legacy_only"]) - set(baseline))
            print(f"REFUSED: baseline would GROW {len(baseline)} -> "
                  f"{res['legacy_only_count']} (new legacy-only: "
                  f"{', '.join(grown[:5])}{' ...' if len(grown) > 5 else ''}). "
                  f"Pass --allow-grow only with an explicit operator decision.")
            return 1
        write_baseline(res)
        print(f"baseline written: {res['legacy_only_count']} endpoints -> "
              f"{_rel(BASELINE_PATH)}")
        if a.report:
            write_report_md(res, res["legacy_only"])
        return 0

    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"legacy endpoints: {res['legacy_total']} | "
              f"spa endpoints: {res['spa_total']}")
        print(f"legacy-only: {res['legacy_only_count']} across "
              f"{res['family_count']} families")
        if not a.check:
            for fam, eps in res["families"].items():
                print(f"  {fam}: {len(eps)}")

    if a.report:
        write_report_md(res, baseline)
        print(f"report -> {_rel(REPORT_MD_PATH)}")

    if a.check:
        if baseline is None:
            print("CHECK FAIL: no baseline at "
                  f"{_rel(BASELINE_PATH)} (run --write-baseline "
                  "once and commit it).")
            return 1
        current = set(res["legacy_only"])
        base = set(baseline)
        regressions = sorted(current - base)
        migrated = sorted(base - current)
        if migrated:
            print(f"progress: {len(migrated)} endpoint(s) migrated since "
                  f"baseline -- ratchet available (--write-baseline).")
        if regressions:
            print(f"CHECK FAIL: {len(regressions)} NEW legacy-only "
                  f"endpoint(s) not in baseline:")
            for ep in regressions:
                print(f"  + {ep}")
            print("Either wire the SPA call too, or (operator decision) "
                  "ratchet the baseline with --write-baseline --allow-grow.")
            return 1
        print(f"CHECK PASS: legacy-only {res['legacy_only_count']} <= "
              f"baseline {len(base)}; no new legacy-only endpoints.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
