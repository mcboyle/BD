"""Row 744: dry-run wording names the declared-repin exception without writing."""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys
from pathlib import Path
BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]; TOOL = ROOT / "toolchain/bin/bd-guard-declare"
def test_drift_dry_run_names_declared_repin_and_never_writes_manifest(tmp_path):
    manifest = json.loads((ROOT / "guards.json").read_text()); guard = next(iter(manifest["guards"]))
    shutil.copy2(ROOT / "guards.json", tmp_path / "guards.json"); target = tmp_path / guard; target.parent.mkdir(parents=True); shutil.copy2(ROOT / guard, target); target.write_bytes(target.read_bytes() + b"\n# drift\n")
    before = hashlib.sha256((tmp_path / "guards.json").read_bytes()).hexdigest()
    r = subprocess.run([sys.executable, str(TOOL), "--file", guard, "--root", str(tmp_path)], text=True, capture_output=True, timeout=30); out = r.stdout + r.stderr
    assert (r.returncode == 0 and "brief" in out.lower() and "row 744" in out.lower()
            and "does not write guards.json itself" in out.lower()
            and "does NOT write guards.json" not in out), out
    assert hashlib.sha256((tmp_path / "guards.json").read_bytes()).hexdigest() == before
def test_every_current_claim_mentions_declared_repin_exception():
    text = TOOL.read_text(); claims = [line for line in text.splitlines() if "does not write" in line.lower()]
    assert len(claims) >= 3 and all("CENSUS" in line or "census" in line or "row 744" in line for line in claims), claims
