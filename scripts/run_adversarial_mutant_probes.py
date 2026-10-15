from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "tests" / "mutants" / "swarm_adversarial_mutation_probing.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

def run_catcher(catcher_node: str) -> tuple[int, str]:
    cmd = [PYTHON, "-m", "pytest", catcher_node, "-q", "--tb=short"]
    res = subprocess.run(cmd, check=False, cwd=str(REPO_ROOT), capture_output=True, text=True)
    combined = (res.stdout + "\n" + res.stderr).strip()
    return (res.returncode, combined)

def main() -> int:
    start_time = time.time()
    print("=== Adversarial Synthetic Mutation Probing Runner ===")
    print(f"Loading spec: {SPEC_PATH}")
    if not SPEC_PATH.exists():
        print(f"Error: Spec file not found: {SPEC_PATH}")
        return 1
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    mutants = spec.get("mutants", [])
    print(f"Auditing {len(mutants)} declared synthetic mutants...")
    print("\n[Baseline Check] Running catcher suite without mutations...")
    baseline_rc, baseline_out = run_catcher("tests/test_adversarial_probes.py")
    if baseline_rc != 0:
        print(f"Baseline FAILED (rc={baseline_rc}):\n{baseline_out}")
        return 1
    print("Baseline PASS (rc=0, clean execution).")
    results = []
    for mutant in mutants:
        label = mutant["label"]
        rel_file = mutant["file"]
        old_regex = mutant["old_regex"]
        new_val = mutant["new"]
        catcher = mutant["catcher"]
        target_file = REPO_ROOT / rel_file
        original_content = target_file.read_text(encoding="utf-8")
        if not re.search(old_regex, original_content):
            print(f"ERROR: Pattern '{old_regex}' not found in {rel_file}")
            results.append((label, False, "Pattern not found"))
            continue
        mutated_content = re.sub(old_regex, new_val, original_content, count=1)
        try:
            target_file.write_text(mutated_content, encoding="utf-8")
            rc, output = run_catcher(catcher)
            if rc != 0:
                diag_line = "KILLED (rc!=0)"
                for line in output.splitlines():
                    if "AssertionError" in line or "FAILED" in line or "Error" in line:
                        diag_line = line.strip()
                        break
                print(f"[KILLED] {label} -> {diag_line}")
                results.append((label, True, diag_line))
            else:
                print(f"[SURVIVED] {label} -> Test passed on mutated code!")
                results.append((label, False, "SURVIVED (rc=0)"))
        finally:
            target_file.write_text(original_content, encoding="utf-8")
    duration = time.time() - start_time
    total = len(results)
    killed = sum((1 for _, ok, _ in results if ok))
    survived = total - killed
    kill_rate = killed / total * 100.0 if total > 0 else 0.0
    print("\n=== Synthetic Mutant Probing Summary ===")
    print(f"Total Mutants Evaluated : {total}")
    print(f"Mutants Killed          : {killed} ({kill_rate:.1f}%)")
    print(f"Mutants Survived        : {survived}")
    print(f"Execution Duration      : {duration:.2f}s")
    if killed == total:
        print("\nALL DECLARED SYNTHETIC MUTANTS CAUGHT & KILLED (100.0% Kill Rate)")
        return 0
    else:
        print("\nSOME MUTANTS SURVIVED - NON-BLOCKING COVERAGE DEBT (Rule 86 Protocol F)")
        return 1
if __name__ == "__main__":
    sys.exit(main())
