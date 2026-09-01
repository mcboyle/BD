# Harness fail-open audit -- 2026-09-01

Produced by the `harness-fail-open-audit` workflow (41 agents, every finding put
through an adversarial skeptic whose default answer was REFUTED). It hunted five
shapes across the ~129 executable bd-* scripts, which NO CI covers.

31 filings CONFIRMED, 27 distinct after merging four duplicate pairs.

Row 476 says the harness is load-bearing and almost untested. This is the
measurement behind it. Ranked by blast radius, then by how often the script runs.

READ THE DANGER NOTES. Several one-line fixes change fleet-wide behaviour or
start refusing work that currently passes; the audit says which.

FIXED IMMEDIATELY on 2026-09-01 (see the end of this file): the two in the merge
path, and the swallowed tar failure.

---

TIER 0 — DO THESE FIRST (highest value per minute in the hours you have)
- bd-endgame2.sh:45 `8080` -> `5555`, and bd-endgame2.sh:21 add the untracked tar. The endgame's only health gate is fail-open on the exact host and hour you will run it, and step 2 destroys untracked work.
- bd-rebase-all.sh:63-70 stash tell (defeated in 122/122 worktrees right now; fires on the next rebase).
- bd-verify-cut.sh:197 add `attribute.log` to the suffix list (one word; it is the merge gate).
- bd-clean-residue.sh:31 `|| true` -> refuse-and-exit (right behind the three above).

27 distinct defects from 31 filings (4 duplicate pairs merged, both filings named).

=== TIER 1 — DESTROYS WORK ===

1. bd-endgame2.sh:13-31 — archive is diff+status only; untracked bytes never saved, then rm -rf. Already destroyed rowa-4's RED test + 2 mutant specs, permanently. You are about to run this script.
   ONE-LINE: after line 21 add
   `git -C "$w" ls-files -o --exclude-standard | grep -v 'venv\|node_modules' | tar -czf "$AR/worktree-diffs/$n.untracked.tar.gz" -C "$w" -T - 2>/dev/null`
   The missing CLOSED-on-main check is real work; cheapest disarm is to delete lines 27-34 and let bd-worktree-archive.sh do removals.

2. bd-clean-residue.sh:31/36/45 (filings 22 + 26, same chain) — `tar ... || true` swallows failure, the durability gate proves only the manifest, then rm -rf takes the only copies. Live trigger today is the vanish/change-while-reading race (76 keep-files touched in the last hour).
   ONE-LINE: `|| true` -> `|| { echo "$H: tar FAILED -- refusing to delete"; exit 4; }`
   REAL WORK: the `-maxdepth 2` / `-size -50M` keeplist (excludes ~52k keep-pattern files) and the missing archive readback — copy bd-worktree-archive.sh:61-64.
   DANGER: dropping the maxdepth/size filters balloons the archive by ~52k files; do not do that blind at 3am.

3. bd-watchdog.sh:15-16 — anchored `^bash (/home/mboyle/)?$s` misses `./x.sh` and `/bin/bash ./x.sh`, so a duplicate bd-night is spawned; bd-night has no singleton lock and bd-codex-cut.sh:11 `rm -rf "$W"` destroys the first dispatch's live worktree. Runs every 120s.
   ONE-LINE: `grep -cE "^(/bin/)?bash (\./|/home/mboyle/)?$s"`
   DANGER: this changes fleet-wide restart behaviour, and it does NOT fix bd-autorebase.sh (its argv is `sleep 86400` after `exec`, so the 108 leaked sleepers continue). Verify the count on each of the six names before leaving it running.

4. bd-worktree-archive.sh:36-39 — untracked files outside six directories are invisible (frontend/, project-knowledge/, repo root), then `git worktree remove --force || rm -rf`. Latent: 0 rows currently both finished and blind-carrying, but row369 carries 2 unshipped frontend components and is one rebase away.
   ONE-LINE: `untracked=$(git -C "$W" status --porcelain 2>/dev/null | grep -vE '^\?\? (venv|frontend/node_modules)' | grep -cE '^\?\? ')`
   (the bare `^\?\? ` count alone is >=1 in all 140 trees because of the venv/node_modules symlinks — it would keep everything forever.)

=== TIER 2 — WRONG VERDICT (by how often the script runs) ===

5. bd-rebase-all.sh:63-70 — the empty-stash tell greps a SHARED refs/stash for a constant string; 429 stale `bd-rebase-all` entries exist and the tell is defeated in all 122 worktrees today. Runs on every rebase. Clobbers the worktree with a prior run's content while logging "re-based"; recoverable only from refs/candidate-safety and the reflog (that pin is why this is wrong-verdict, not destroys-work).
   TWO LINES: capture `S0=$(git -C "$W" rev-parse -q refs/stash)` before the push and require the ref to have CHANGED, instead of the grep.
   DANGER: flips which of two rebase paths every one of 122 worktrees takes. Dry-run one row before turning it loose.

6. bd-checkpoint-write:70-73 (filings 21 + 29) — `_HRE` anchored on absolute path + `\.sh`; misses every watchdog-launched relative-argv loop and every extensionless one. Runs every 10 min via bd-checkpoint.timer, and its output is what a fresh post-compaction agent is told to trust.
   ONE-LINE: `_HRE='^(/bin/|/usr/bin/)?bash (\./|/home/mboyle/(bd-persist/harness/)?)?bd-[a-z0-9-]+\.sh'`
   DANGER: do NOT also drop the `\.sh` — bd-checkpoint-write is itself extensionless bash and would self-match. Covering extensionless loops needs the real fix (reuse bd-ps.sh's /proc+basename+self-exclusion).

7. bd-checkpoint-write:25-32 — unchecked `git fetch`; every "on origin/main" number is then read from a stale ref and printed under "MEASURED AT WRITE TIME". Same 10-min cadence, timer can fire pre-network at boot.
   ONE-LINE: `git -C "$R" fetch -q origin 2>/dev/null || STALE=" (STALE -- fetch FAILED; figures below are from the last successful fetch)"` and append `$STALE` to the `main` line.

8. bd-rebase:97 + 102-105 — `grep -c ... || echo 0` yields the two-line string `0\n0` in exactly the case the guard exists for, so the dropped-register-row refusal can never fire. 11 instances already in the log; 0 refusals ever. Runs on every row.
   ONE-LINE (apply at 53, 97 and 107): `NOWROW=$( (grep -cE "^\| $r \|" "$f" 2>/dev/null || echo 0) | head -1 )`

9. bd-rebase:55 — bd-rebase-all's status and log are discarded, and success is re-derived from a nonzero diff vs MAIN, which a never-rebased stale tree satisfies. Three failure paths report `ok`, and line 110 then wipes the retry counter that bd-night uses to park a row.
   ONE-LINE: after line 55, `git -C "$W" merge-base --is-ancestor "$MAIN" HEAD || { say "row $r: NOT on main after rebase -- manual"; manual=$((manual+1)); continue; }`

10. bd-verify-cut.sh:195-197 — `$TAG-attribute.log` is the one per-tag verdict artifact missing from the stale-clear loop and every write to it is `>>`, so a reused tag excuses a node this cut broke and satisfies the denominator guard with a previous attempt's counts. Runs on every verify; tags are reused with no attempt counter.
    ONE-LINE: add `attribute.log` to the suffix list at line 197 (it inherits the loop's refuse-on-failure semantics for free).

11. bd-fleet-deploy.sh:108 — the already-at-version path greps a tracked file and returns ok: no health read, no commit compare. It is the DOMINANT branch after any successful deploy, and it is the one path the self-heal machinery can never reach.
    ONE-LINE: before `_verdict ok`, probe health over ssh reusing `$HPORT` and require 200, else fall through to the deploy.
    DANGER: do not "fix" it by deleting the early return — that makes every pass full-deploy and restart all six hosts.

12. bd-land:99-108 — containment loop feeds on `--diff-filter=AM`, so deletions and renamed new paths are never blob-compared; a deletion-only cut checks zero files and still prints LANDED. Already partially wrong on 14 real merges. Runs on every land, and the verdict gates the ff-merge plus force-push of every sibling.
    ONE-LINE stopgap (fails closed): before the loop,
    `[ "$(git -C "$REPO" diff --name-only --diff-filter=AM "$MB" "$SHA"|wc -l)" = "$(git -C "$REPO" diff --name-only "$MB" "$SHA"|wc -l)" ] || die "changed set includes D/R paths -- containment UNMEASURED"`
    DANGER: this will start refusing the ~6% of cuts that carry deletions; those become hand-verified lands. The real fix (assert absence for D paths, `--no-renames`, nonzero-denominator assert) is real work.

13. bd-row-audit.py:239 — C6 re-runs the identical diff that produced its own input list, so it is a tautology and can never fire. Pre-dispatch gate, runs on every row.
    ONE-LINE exists (`mb` -> `"origin/main"`) and is proven RED/GREEN.
    DANGER: that is precisely the change that was reverted to stop a false refusal when main advances past the candidate's base. Restoring it will refuse correct rows. The correct fix is blob comparison (`hash-object` in the worktree vs `origin/main:f`), which is small real work — do that, not the revert.

14. bd-row-audit.py:133-136 — the empty-diff fallback decides "already merged" by grepping commit messages for the row's slug. Misreports 5 rows today (fail-closed), and 3 rows are one worktree-reset away from a silent exit-0 PASS. Same frequency as 13.
    REAL WORK: replace the grep with blob containment for the files the row touched.

15. bd-register-merge.py:65-70 — membership by row ID only, so a squatted ID silently drops the worker's row and bd-integrate-row.sh then stamps CLOSED on the SQUATTER at a version that lacks its work. Runs on every integrate; 4 workers have filed row 250, 3 filed 255.
    ONE GUARD (small): refuse with a distinct exit when `n in nw_rows and wk_rows[n].strip() != nw_rows[n].strip()`.
    DANGER: it will start refusing integrates that currently pass silently — correct, but it will surface a collision backlog in your last hours.

16. bd-preflight.sh:111-114 — the truthful rc exists only as a log line; the script's last command is `tail -3`, so a fully RED band and the explicit `99` UNKNOWN both exit 0. Manual, per candidate.
    TWO ONE-LINE EDITS: line 111 -> `RC=$?; echo "PREFLIGHT_RC=$RC"`, then after line 114 add `exit $RC`. (The brace group is redirected, not a subshell, so RC survives — verified.)

17. bd-preflight.sh:40 — `[ ! -s "$p" ]` is true for a missing patch as well as a legitimately empty register-only one, so a mistyped path is reported as a register-only row and the cut runs green and empty.
    ONE-LINE: before line 40, `[ -e "$p" ] || { echo "PREFLIGHT_RC=93 (patch path MISSING: $p)"; exit 93; }`

18. bd-clean-vms.sh:66-78 + 83 (filings 20 + 25) — the remote payload's own argv contains its case patterns, so the loop kills its own shell mid-sweep: agents after it survive, the tmux step never runs, and `${n:-0}` renders "0 signalled, tmux sessions cleared". Line 83's probe self-counts, flooring a clean host at agents=2. Endgame/cleanup only — but that is this hour, and A6 deploys and the authoritative suite depend on a quiet fleet.
    ONE-LINE each: `[ "$pid" = "$$" ] && continue` at the top of the remote loop; `pgrep -c -f 'c[o]dex'` at line 83 (the bracket trick is valid here — the pattern is a regex while the wrapper carries the bracketed literal).
    DANGER: this is a six-host kill loop you cannot rehearse cheaply. Test it with `kill` replaced by `echo` on one host first.

=== TIER 3 — WASTES TIME ===

19. bd-att-guard.sh:16-20 — clears a CUMULATIVE counter on a tail-3 read of an APPENDED log. bd-night already declines to charge collisions, so 100% of its 232 deletions were wrong; row 371 failed QA 15 times in one night and was never parked. Runs every 60s.
    ONE-LINE: line 20 `rm -f "$f"` -> `:` . Do NOT disable it with an `exit 0` at the top — bd-watchdog.sh:12 lists it and will respawn it every 2 minutes.

20. bd-watchdog.sh:10 (filings 18 + 27) — the armed line claims codex-pool, codex-refill and width-restore, none of which the loop checks, and omits four it does. Published to FINISH.log as a coverage contract; runs continuously.
    ONE-LINE: rewrite the string to match the line-12 list verbatim. (Whether those three should also be guarded is a separate decision.)

21. bd-denom-preflight:84/90/103/117 — RC=2 is sticky, so a measured RED behind an anchorcheck UNKNOWN is reported upward as "could not be measured". Both states abort, so nothing ships wrong; it just points you at the wrong lane. Runs on every verify.
    ONE-LINE x4: `[ "$RC" -eq 2 ] || RC=1` -> `RC=1` (RED should dominate UNKNOWN).

22. bd-land:137-140 — greps bd-running's output for the absolute worktree path, but bd-running:46 truncates at `%.110s`; with the standard 35-char prefix the probe goes blind for any cut slug >= 51 chars, and real branches have 69-char slugs. Cost is a silently wasted ~25-min verify; step 1's SHA-exact match refuses the voided evidence later.
    ONE-LINE: bd-running:46 `%.110s` -> `%.300s`.
    REAL WORK (small): read the `$ART/.worktree-<hash>.lock/holder` record bd-verify-cut.sh:167-191 already writes, instead of grepping argv text.

23. bd-supervise.sh:29/34-35 — retired but armed, and doubly broken: `head -60` misses 7 of 8 slugs that merged at positions 208-256, and `set -o pipefail` + SIGPIPE on the 793KB register kills the CLOSED check for every row. A launch today relaunches drains for 8 rows indefinitely, each claiming fresh version numbers for landed work.
    ONE-LINE: `exit 1` with a "RETIRED -- use bd-night.sh" message at the top. Do not repair it; NIGHT_POLICY.md's recovery section still names it, so fix that reference too.

24. bd-fleet-audit-cmd.sh:6 — probes :8080; BD is on :5555. Eight of eight healthy hosts read as unhealthy; on test5 it reads Open WebUI's 200.
    ONE-LINE: `8080` -> `5555` (or derive it as bd-fleet-deploy.sh:51-53 does).

25. bd-vm-bringup.sh:9-11 — the line-9 loop exits only on READY, so the line-10 refusal can never fire; INCOMPLETE and a missing verdict both become one silent sleep-60 forever. (The stale-READY and credential-leak escalations in the filing were refuted — provision.log is truncated with `>`, and line 25 gates credentials per-host.)
    ONE-LINE: `while ! grep -qE "VERDICT: (READY|INCOMPLETE)" ...`

26. bd-register-open-row.py:58 (+ bd-deref-register.py, bd-depipe-register.py) — no exit-code discipline anywhere; all four refusal paths and an empty argv return 0. No caller currently gates on it, so the realized cost is low.
    ONE-LINE each: track a failure flag and `sys.exit(1)`.

27. bd-batch-rows.py:79-83 — unchecked `git diff` returncode makes an unmeasurable row look file-disjoint from everything and pack with CAP-1 others; failure means MAXIMUM batching. Dormant today (0 of 123 worktrees can trigger it) and undetectable when it fires.
    ONE-LINE: check `returncode` and `return None`, matching the author's own None-means-ship-alone contract.

=== DANGEROUS TO FIX QUICKLY (do not touch these unattended) ===
bd-clean-vms remote kill loop (#18) · bd-land containment filter (#12) · bd-row-audit C6 (#13, the one-liner re-arms the false refusal it was patched away from) · bd-watchdog matcher (#3) · bd-checkpoint-write `\.sh` half (#6) · bd-fleet-deploy early return (#11) · bd-rebase-all stash tell (#5, dry-run one row) · bd-clean-residue keeplist widening (#2) · bd-register-merge collision guard (#15).
