#!/usr/bin/env python3
"""invariants -- the gated invariant registry (SCHEMAS §7).

Permanent, executable rules promoted from confirmed bug-classes + DANGER_MAP.
Each: statement / at / why / guard_test / status (GUARDED|UNGUARDED) / dp_class.
Seeded from the verify-pass defects (their fix is the invariant) + the 531/532
CSRF work.

Gate (--check):
  * every GUARDED invariant's guard_test file must exist in the tree (else the
    guard is a phantom -> rc!=0)
  * every UNGUARDED invariant is reported (audit must write a RED guard for it)

Usage: python3 invariants.py [--out FILE] [--root TREE]   |   --check
"""
import argparse
import json
import os
import sys

SCHEMA = 1

INVARIANTS = {
    "I0001": dict(
        statement="resume_site_keepers must never exist (nested-playwright deadlock)",
        at="bulk_downloader/runner.py", why="nested sync/async playwright deadlock",
        guard_test="tests/test_v3_43_79_resume_site_keepers.py", status="GUARDED", dp=None),
    "I0002": dict(
        statement="settings secret masking must recurse into dict/list values",
        at="bulk_downloader/app_settings_center.py",
        why="VR-P02: accounts[].password leaked raw on effective endpoints",
        guard_test="tests/test_v3_66_521_security_hotfix.py", status="GUARDED", dp="DP-07"),
    "I0003": dict(
        statement="IP safety classifier must use ipaddress.is_global as the allowlist test",
        at="bulk_downloader/provider_resolve_impl/_common.py",
        why="VR-P15: denylist style omitted CGNAT 100.64.0.0/10 (SSRF)",
        guard_test="tests/test_v3_66_524_security_hardening.py", status="GUARDED", dp="DP-11"),
    "I0004": dict(
        statement="numeric validators must reject non-finite (NaN/inf) before range checks",
        at="bulk_downloader/site_editor.py",
        why="VR-P08/P12: NaN evaded bounds -> live-config corruption; inf -> 500",
        guard_test="tests/test_v3_66_523_input_validation.py", status="GUARDED", dp="DP-03"),
    "I0005": dict(
        statement="every /cockpit/api/ write the cockpit issues must send a CSRF token",
        at="tools/cockpit_console.py",
        why="v3.66.532: api() sent an empty token after the 531 gate covered /cockpit/api/",
        guard_test="tests/test_phc1_cockpit_csrf_send.py", status="GUARDED", dp=None),
    "I0006": dict(
        statement="the global CSRF/origin guard must cover /api/ and /cockpit/api/",
        at="bulk_downloader/app.py",
        why="PHC-1 B1: 28 cockpit writes were auth-gated but CSRF-unguarded",
        guard_test="tests/test_phc1_csrf_coverage.py", status="GUARDED", dp=None),
    "I0007": dict(
        statement="apprise .add() must pass tag= when notify() filters on tag",
        at="bulk_downloader/notify_apprise.py",
        why="VR-P01: untagged add() -> notify(tag=event) matched 0 services",
        guard_test="tests/test_v3_66_521_security_hotfix.py", status="GUARDED", dp="DP-14"),
    "I0008": dict(
        statement="the KV secret-keyword set must equal or consult the URL-query SoT",
        at="bulk_downloader/capture_artifact_redact.py",
        why="VR-P03: narrower KV set let OAuth #code=/otp= survive the floor",
        guard_test="tests/test_v3_66_521_security_hotfix.py", status="GUARDED", dp="DP-08"),
    "I0009": dict(
        statement="user-controlled SQL identifiers (table/column) must be allowlisted",
        at="bulk_downloader/batch_ops.py",
        why="VR-P10: f-string table interpolation from request body",
        guard_test="tests/test_v3_66_524_security_hardening.py", status="GUARDED", dp="DP-10"),
    "I0010": dict(
        statement="redaction scanner must fail-closed on non-dict/list/str leaves (bytes/set)",
        at="bulk_downloader/capture_artifact_redact.py",
        why="VR-THD: scanner passed bytes/set through unscanned (fail-open)",
        guard_test="tests/test_v3_66_529_redact_nonjson_types.py", status="GUARDED", dp="DP-09"),
}


def build(out):
    json.dump({"schema": SCHEMA, "invariants": INVARIANTS}, open(out, "w"),
              indent=2, sort_keys=True)
    guarded = sum(1 for v in INVARIANTS.values() if v["status"] == "GUARDED")
    return {"total": len(INVARIANTS), "guarded": guarded,
            "unguarded": len(INVARIANTS) - guarded}


def check(out, root):
    if not os.path.exists(out):
        print("invariants --check: INVARIANTS.json missing (run without --check first)")
        return 1
    inv = json.load(open(out))["invariants"]
    phantom = []
    unguarded = []
    for iid, v in sorted(inv.items()):
        if v["status"] == "GUARDED":
            gt = (v.get("guard_test") or "").split("::")[0]
            if gt and not os.path.exists(os.path.join(root, gt)):
                phantom.append((iid, gt))
        else:
            unguarded.append(iid)
    print(f"invariants --check: total={len(inv)} guarded={sum(1 for v in inv.values() if v['status']=='GUARDED')} "
          f"phantom_guard={len(phantom)} unguarded={len(unguarded)}")
    if phantom:
        print("  GUARDED invariants whose guard_test is absent (phantom):")
        for iid, gt in phantom:
            print(f"    {iid} -> {gt}")
    if unguarded:
        print("  UNGUARDED (audit must add a RED guard):", unguarded)
    return 0 if not phantom else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/home/claude/review/artifacts/INVARIANTS.json")
    ap.add_argument("--root", default="/home/claude/work")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(check(a.out, a.root))
    print("invariants:", json.dumps(build(a.out)))


if __name__ == "__main__":
    main()
