# TOOL_INDEX -- the tools that are the daily workflow

## What this document is

BD's tool denominator is over six hundred files across four populations. A
document that lists all of them is a document nobody reads, and an index nobody
reads is the same thing as no index: the recorded cost of rediscovering existing
tooling in this repository is ninety minutes and eight defects re-found that the
existing tool had already fixed.

So this indexes the tools that carry the lane -- start a cut, gate it, verify
it, land it, prove it shipped, deploy it -- and points mechanically at the rest.
It extends the A8 routing table in `CLAUDE.md`, which is deliberately small; A8
routes a question to an owner, this says how the owner is invoked, what it
returns, and what it will do to you if you invoke it wrong.

It is a starting point, not a complete denominator. A8's rule still binds:
before creating a file at any path, prove that exact name is unused in BOTH the
repository and the operator harness, and read an existing caller before
assuming an interface.

## How the documented set was chosen

Three sources, unioned:

1. **Reference count.** The operator harness map at
   `/home/mboyle/bd-persist/workers/1457/HARNESS_MAP.txt` records, per harness
   file, how many other files name it. Everything at or above roughly fifteen
   inbound references is in the lane by construction: it is called by the lane.
2. **Named in the contract.** Every tool `CLAUDE.md` names by role -- the gates
   in A3 and A5, the routing table in A8, the deploy path in A6.
3. **Safety boundaries.** A tool whose whole job is to refuse something
   dangerous is in the lane even with one caller, because its value is realised
   at the moment it is invoked by hand.

Everything else is reachable through the mechanical enumeration below.

## Populations, measured

Measured on host `test5`, in the worktree for `cut/1479-the-toolchain-has-an-index`,
at base commit `0b150ad1d6b48e3006227defbe3738ccc9126a72`,
tree `905492772198f6d230fb9261595903c601fc9ff1`, 2026-09-02T14:41Z.
These are volatile. Re-derive rather than quote.

| Population | Count | Re-derive with |
| --- | --- | --- |
| `toolchain/bin` operational tools (tracked, extensionless) | 256 | `git ls-files toolchain/bin \| wc -l` |
| `tools/*.py` build/analysis scripts (top level) | 228 | `ls tools/*.py \| wc -l` |
| `tools/**/*.py` in subdirectories | 20 | `git ls-files 'tools/*.py' \| wc -l` minus the above |
| `scripts/` install/deploy/service entry points | 20 files, 9 of them `.sh` | `ls scripts/` |
| Operator harness `/home/mboyle/bd-*` (depth 1) | 222 entries: 177 files, 45 directories | `find /home/mboyle -maxdepth 1 -name 'bd-*' \| wc -l` |
| ...of those files, executable | 142 | `find /home/mboyle -maxdepth 1 -name 'bd-*' -type f -executable \| wc -l` |

Note the two denominators that disagree on purpose: `ls toolchain/bin` returns
257 in the integrator's own checkout because `__pycache__` is present and
untracked. A glob is a denominator choice; say which one you used.
