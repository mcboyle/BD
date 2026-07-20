#!/usr/bin/env python3
"""Re-examine the RUNS-DEGRADED bucket post-port with REAL output. A tool is
genuinely RUNS-DEGRADED only if its output is empty, all-zeros, "not found", or
about a sandbox path -- otherwise (real counts / real findings about this tree)
it RUNS. Read-only; never runs a mutating-named tool."""
from __future__ import annotations
import json, os, re, subprocess

verdict = {r["tool"]: r for r in json.load(open("/tmp/toolchain_verdict.json"))}
MUT = re.compile(r'-(boot|install|venv|mirror|deploy|provision|mkbdsuite|optpack|prestage|seed|stage|bump|cut|release|autofix|selfheal|mutation|sbcap|rev|apply|writeback|repair|regen|snapshot|freeze|record|reboot|kb-sync|pk-sync|pk-cleanup)\b')
# degraded signals in real output
DEGRADED = re.compile(
    r'/home/claude|/mnt/project|/mnt/user-data|/tmp/prestaged'          # sandbox path
    r'|CANNOT.?EVALUATE|not found|no such|NOT FOUND'                     # absent
    r'|\b0 (file|files|module|modules|tool|tools|control|surface|route|routes|finding|findings|doc|docs|key|keys|violation)', # empty denom
    re.I)
STRIP = re.compile(r'\x1b\[[0-9;]*m')

def firstlines(out, n=3):
    lines = [STRIP.sub('', l) for l in out.splitlines() if l.strip()]
    return " | ".join(lines[:n])[:120]

promoted, kept = [], []
for name, r in verdict.items():
    if r["class"] != "RUNS-DEGRADED":
        continue
    if MUT.search(name):
        kept.append((name, "mutating -- left as is")); continue
    try:
        p = subprocess.run(["python3", f"toolchain/bin/{name}"], capture_output=True,
                           text=True, timeout=20,
                           env={**os.environ, "BD_HOME": "/tmp/bd_home", "BD_DISABLE_KEEPALIVE": "1"})
        out = (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        kept.append((name, f"re-run {type(e).__name__}")); continue
    fl = firstlines(out)
    if out.strip() and not DEGRADED.search(out):
        # real output, no degraded signal -> RUNS
        verdict[name]["class"] = "RUNS"
        verdict[name]["why"] = f"post-port: produces real output about this tree ({fl[:70]})"
        promoted.append((name, fl))
    else:
        verdict[name]["why"] = f"exit {p.returncode}: {fl[:80]}" if 'p' in dir() else r["why"]
        kept.append((name, fl))

json.dump(list(verdict.values()), open("/tmp/toolchain_verdict.json", "w"), indent=0)
print(f"== promoted RUNS-DEGRADED -> RUNS: {len(promoted)} ==")
for n, fl in promoted: print(f"  {n:26s} {fl[:74]}")
print(f"\n== kept RUNS-DEGRADED: {len(kept)} ==")
for n, fl in kept: print(f"  {n:26s} {fl[:74]}")
