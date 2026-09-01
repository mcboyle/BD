#!/usr/bin/env python3
"""Resolve the three conflict-prone files the INTEGRATOR owns, not the worker.

Called only after a re-base pop leaves conflicts. These three are not judgement
calls -- the integrator applies exactly these rules later anyway:

  IMPROVEMENT_BACKLOG.md  -> take MAIN's population, re-attach THIS row's line
                             by identity (bd-register-merge.py's rule).
  ci.yml, shard-gate test -> UNION both sides; both entries belong
                             (bd-union-resolve.py's rule, read from that file).

Anything else is left conflicted on purpose: a real content clash needs a human.
"""
import ast, pathlib, re, subprocess, sys

W, ROW = pathlib.Path(sys.argv[1]), sys.argv[2]
R = "/home/mboyle/BulkDownloader"
CONFLICT = re.compile(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", re.S)

src = pathlib.Path("/home/mboyle/bd-union-resolve.py").read_text(encoding="utf-8")
UNION_OK = set()
for node in ast.walk(ast.parse(src)):
    if (isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "UNION_OK" for t in node.targets)
            and isinstance(node.value, ast.Set)):
        UNION_OK = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
if not UNION_OK:
    sys.exit("cannot read UNION_OK -- refusing to guess")

unmerged = subprocess.run(["git", "-C", str(W), "diff", "--name-only", "--diff-filter=U"],
                          capture_output=True, text=True).stdout.split()
REG = "project-knowledge/IMPROVEMENT_BACKLOG.md"
for rel in unmerged:
    f = W / rel
    if rel == REG:
        text = f.read_text(encoding="utf-8")
        line = next((l for l in text.split("\n") if l.startswith(f"| {ROW} |")), None)
        main = subprocess.run(["git", "-C", R, "show", f"origin/main:{rel}"],
                              capture_output=True, text=True).stdout
        if line is None or f"| {ROW} |" in main:
            print(f"  {rel}: row {ROW} absent or already on main -- left for the integrator")
            continue
        rows = list(re.finditer(r"^\| \d+ \|", main, re.M))
        end = main.index("\n", rows[-1].start())
        f.write_text(main[:end + 1] + line + "\n" + main[end + 1:], encoding="utf-8")
        subprocess.run(["git", "-C", str(W), "add", rel], capture_output=True)
        print(f"  {rel}: took main's population, re-attached row {ROW} by identity")
    elif rel in UNION_OK:
        text = f.read_text(encoding="utf-8")
        n = len(CONFLICT.findall(text))
        text = CONFLICT.sub(lambda m: m.group(1) + m.group(2), text)
        if "<<<<<<<" in text:
            print(f"  {rel}: markers remain after union -- left conflicted")
            continue
        f.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(W), "add", rel], capture_output=True)
        print(f"  {rel}: {n} conflict(s) union-resolved")
    else:
        print(f"  {rel}: REAL content conflict -- left for a human")
