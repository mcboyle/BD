#!/usr/bin/env python3
"""Toolchain portability harness (Part B Phase 3, read-only DATA COLLECTION).

For each bd-* tool it collects the SIGNALS needed to classify portability against
a clean clone -- it does NOT decide RUNS on exit 0 (the mission's cardinal rule:
exit 0 on an empty/wrong denominator is RUNS-DEGRADED). Safety first: tools whose
name marks them mutating/heavy (install/boot/venv/mirror/seed/stage/deploy/...)
are NEVER executed beyond --help; they are classified by reading.

Signals per tool: does --help work; which tree-root / mode flags it exposes;
whether the source hardcodes a sandbox path; and, for read-only tools, the UNPIPED
exit + output signature of a no-arg run and a run pointed at THIS tree (--tree .).

Output: JSON lines to stdout (one object per tool). Classification is a later,
judged pass over this data -- this only measures.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, glob

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "toolchain", "bin")
TREE = os.path.dirname(BIN).replace("/toolchain", "")  # repo root
SANDBOX = re.compile(r'/home/claude|/tmp/prestaged|/mnt/project|/tmp/tools_bin|/tmp/media|/mnt/user-data|/mnt/skills|prestaged_site_packages|/home/claude/work')
# tools that MUTATE the tree / infra or are heavy — never execute beyond --help.
MUTATING = re.compile(r'-(boot|install|venv|mirror|deploy|provision|mkbdsuite|optpack|prestage|seed|stage|bump|cut|release|autofix|selfheal|mutation|sbcap|rev|apply|writeback|repair|regen|snapshot|freeze|record|reboot|kb-sync|pk-sync|pk-cleanup)\b')
TREEFLAG = re.compile(r'--(work|tree|root|repo)\b')
MODEFLAG = re.compile(r'--(check|dry-run|json|selftest|report|list|status)\b')

def run(cmd, timeout):
    """Run unpiped; return (exit, combined_output_tail)."""
    try:
        p = subprocess.run(cmd, cwd=TREE, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "BD_HOME": "/tmp/bd_home", "BD_DISABLE_KEEPALIVE": "1"})
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    except Exception as e:
        return f"ERR:{type(e).__name__}", ""

def sig(out):
    """A compact signature of a run's output."""
    if not out.strip():
        return "empty"
    s = []
    if SANDBOX.search(out): s.append("sandbox-path")
    if re.search(r'\b/home/user/BD\b|bulk_downloader|this tree', out): s.append("this-tree")
    if re.search(r'ABSENT|not found|No such|CANNOT|Traceback|ModuleNotFound|Error', out): s.append("error/absent")
    if re.search(r'\b\d{2,}\b.*(edge|tool|file|route|finding|module|doc)', out, re.I): s.append("counts")
    return ",".join(s) or "output"

def main():
    tools = sorted(t for t in glob.glob(f"{BIN}/bd-*") if os.path.isfile(t))
    for t in tools:
        name = os.path.basename(t)
        src = open(t, encoding="utf-8", errors="replace").read()
        rec = {"tool": name,
               "hardcodes_sandbox_path": bool(SANDBOX.search(src)),
               "mutating_by_name": bool(MUTATING.search(name)),
               "purpose": next((l.strip().strip('"').strip("#").strip()
                                for l in src.splitlines()[1:8]
                                if l.strip() and not l.strip().startswith(("#!", "import", "from", '"""', "'''"))), "")[:90]}
        # --help is safe for argparse tools
        he, ho = run(["python3", t, "--help"], 10)
        rec["help_exit"] = he
        rec["has_tree_flag"] = bool(TREEFLAG.search(ho) or TREEFLAG.search(src))
        rec["has_mode_flag"] = bool(MODEFLAG.search(ho) or MODEFLAG.search(src))
        if rec["mutating_by_name"]:
            rec["runnable"] = "no (mutating/heavy -- read to classify)"
        else:
            # read-only: no-arg run, then tree-pointed run if a tree flag exists
            ne, no = run(["python3", t], 25)
            rec["noarg_exit"], rec["noarg_sig"] = ne, sig(no)
            if rec["has_tree_flag"]:
                te, to = run(["python3", t, "--tree", TREE], 25)
                rec["tree_exit"], rec["tree_sig"] = te, sig(to)
        print(json.dumps(rec))

if __name__ == "__main__":
    main()
