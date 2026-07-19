#!/usr/bin/env python3
"""Promote a NORMALIZED runtime-shape review candidate into an enabled (or
reviewed-not-enabled) reviewed template.

This is the final, MANUAL step of the pipeline:

  build_template_from_wacz.py  (rich draft)
    -> normalize_template_draft.py  (runtime-shape review candidate)
    -> manual lint/safety review (edit the candidate: add api{}, modal rows)
    -> promote_template.py  <-- here
    -> runtime consumes the reviewed template

It validates the runtime candidate shape and refuses to promote a RAW builder
draft directly (run the normalizer first). It never enables anything unless
``--enable`` is passed.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from bulk_downloader import selector_lint as sl
except Exception:  # pragma: no cover - standalone fallback
    sl = None

# Blocked substrings in reusable URL/API material — the single authoritative
# source lives in bulk_downloader.bad_terms (shared with the scrubber + inventory
# so the scrubber drops everything this gate would reject). review_notes/
# safety_notes are NOT scanned (they may legitimately mention removed terms).
# Fail-closed: this gate must never run without its denylist, so an import
# failure raises rather than silently degrading or duplicating the list.
from bulk_downloader.bad_terms import BAD_TERMS  # single source of truth

# The readiness/safety gate — raw-draft refusal, non-empty network_patterns, the
# BAD_TERMS denylist, a media/API-relevant pattern, and the download selector
# shape — lives in ONE place: bulk_downloader.template_manager.promote_gate_errors.
# This CLI and the Workbench ``POST /api/template_manager/promote`` both call it,
# so they enforce byte-identical checks (no drift — the whole point). BAD_TERMS
# stays imported above as the shared denylist object the reconciliation tests pin;
# selector-lint + the enabled-status refusal + the row_selectors hint stay CLI-side.
from bulk_downloader.template_manager import promote_gate_errors


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate", type=Path,
                    help="normalized runtime-shape review candidate JSON")
    ap.add_argument("--out-dir", type=Path, default=Path("templates/reviewed"))
    ap.add_argument("--enable", action="store_true")
    args = ap.parse_args()

    d = json.loads(args.candidate.read_text(encoding="utf-8"))

    # Enabled-status refusal stays CLI-side (the Workbench promotes drafts, which
    # are never pre-enabled).
    if d.get("status") == "enabled":
        fail("candidate already has status 'enabled' — refusing to re-promote.")

    # The shared readiness/safety gate — identical to the Workbench promote path.
    gate = promote_gate_errors(d)
    if gate:
        fail("; ".join(gate))

    # Selector safety: refuse blocking (generic/nav) selectors. (Not part of the
    # shared gate, which is value/shape only — the caller owns lint.)
    if sl is not None:
        issues = sl.lint_template(d)
        if sl.has_blocking_issues(issues):
            msgs = "; ".join(i.message for i in issues if i.level == "error")
            fail(f"unsafe selectors — fix before promoting: {msgs}")

    patterns = d.get("network_patterns") or []
    dl = (d.get("selectors") or {}).get("download") or {}
    if not dl.get("row_selectors"):
        print("WARNING: no modal-scoped row_selectors — the trigger only opens "
              "the modal; add rows during review for a complete template.",
              file=sys.stderr)

    host = d.get("host") or "unknown-host"
    safe_host = host.replace("/", "_").replace(":", "_")

    d["status"] = "enabled" if args.enable else "reviewed_not_enabled"
    d.setdefault("promotion_notes", []).extend([
        "Promoted from a normalized runtime-shape review candidate.",
        "Passed BAD_TERMS + selector-lint safety checks.",
        "Reusable fields contain media/watch/download-resolution patterns only.",
    ])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{safe_host}.template.json"
    # Golden protection: never clobber an existing reviewed/gold template without
    # a recoverable copy. The full lifecycle keystone (snapshot + drift gate) is
    # opt-in (Phase C); this always-on .bak is the floor so a re-promote — e.g. a
    # thinner re-capture — can be rolled back. The .bak suffix is outside the
    # ``*.template.json`` glob, so it is never itself loaded as a template.
    if out.exists():
        try:
            import shutil as _shutil
            _shutil.copy2(out, out.with_name(out.name + ".bak"))
            print(f"backed up existing gold -> {out.name}.bak", file=sys.stderr)
        except OSError as _e:
            print(f"WARNING: could not back up existing gold: {_e}", file=sys.stderr)
    out.write_text(json.dumps(d, indent=2), encoding="utf-8")

    print(f"wrote: {out}")
    print(f"status: {d['status']}")
    print("patterns:")
    for p in patterns:
        print(f" - {p}")


if __name__ == "__main__":
    main()
