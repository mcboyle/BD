"""Resolve the stash conflict in row 373 by KEEPING BOTH SIDES.

`git stash` left `<<<<<<< Updated upstream` / `>>>>>>> Stashed changes` markers
in two TypeScript files, so `tsc -b` failed and cascaded into 21 failures across
8 frontend-dependent test files -- which is why this row looked like it broke
CSRF and route wiring. It broke neither; it did not compile.

Upstream is row 363's merged fields (quality_preference, min_resolution,
log_network); the stashed side is this row's `login_trigger`. Both are wanted,
so the resolution is the union, in upstream-then-stashed order.
"""
import pathlib, re

W = pathlib.Path("/home/mboyle/bd-codex-wt/row373")
PAT = re.compile(
    r"^<<<<<<< [^\n]*\n(?P<up>.*?)^=======\n(?P<st>.*?)^>>>>>>> [^\n]*\n",
    re.S | re.M)

for rel in ("frontend/src/components/AddSiteWizard.tsx",
            "frontend/src/lib/api-types.ts"):
    p = W / rel
    s = p.read_text(encoding="utf-8")
    n = len(PAT.findall(s))
    if not n:
        print(f"{rel}: no conflict"); continue
    resolved = PAT.sub(lambda m: m.group("up") + m.group("st"), s)
    left = re.findall(r"^(?:<<<<<<<|=======|>>>>>>>)", resolved, re.M)
    assert not left, f"{rel}: {len(left)} marker(s) survived"
    p.write_text(resolved, encoding="utf-8")
    print(f"{rel}: resolved {n} conflict(s), keeping both sides")

# prove no marker remains anywhere in the worktree's tracked frontend source
bad = []
for f in (W / "frontend/src").rglob("*.ts*"):
    t = f.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^(?:<<<<<<<|>>>>>>>)", t, re.M):
        bad.append(f.name)
print("files still carrying markers:", bad or "none")
