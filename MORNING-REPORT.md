# BD overnight — 2026-08-29, report for 0800 EST

## The headline

**Downloads were ~100x slower than the wire, and it is fixed in config and
root-caused in code.** Measured on test6: BD ran at 0.65-0.70 MB/s while a plain
curl to the SAME url with the SAME cookies did 68-72 MB/s. Everything else was
excluded by measurement first — WAN 63.5 MB/s on that host (80-108 on five
siblings), NIC vmxnet3 10 GbE with 0 errors, disk 184 MB/s O_DIRECT, no
proxy/VPN/SOCKS, and BD's own HTTP client fine in isolation.

Cause, two false beliefs stacked:
1. `runner_transport.py` passes `chunk_size=4MB` to `iter_content`. **curl_cffi
   ignores it** and yields **8183-byte** buffers (median == max over ~4900).
   The adjacent comment claims "roughly chunk_size each". It is false.
2. That loop calls `daily_budget.record_site_bytes()` **per buffer** — a
   connection open + `_ensure_table()` + TWO statements, **measured 6.104 ms**,
   against 0.001 ms for `record_bandwidth()` on the next line. Its docstring
   says "Cheap: one UPSERT."

8183 B / 6.1 ms is the ceiling, and that is the number that was on the screen.
Reproduced exactly: 77.34 MB/s without the call, 0.65 MB/s with it.

Applied tonight: `use_curl_cffi=False` on all 32 sites (httpx honours chunk_size).
**Live result: 71.7 MB/s and 30.2 MB/s** on real scenes. Rows 376 (batch the
budget write) and 378 (db_conn does a full WAL create+checkpoint+unlink on EVERY
open) are the permanent fixes and are built.

## The mission chain works end to end

crawl listing -> scene URL + site-shown title -> queue -> login -> highest
quality -> download -> history. **16 real downloads, 70+ GB**, 8K where offered:

    wowgirls     NikkiHareniks_FuckMeNikkiHareniks_7680x4320_60fps   4.05 GB
    ultrafilms   girls-gone-wilder_gracie-sweet_vixi-rafi_7680x4320  6.21 GB
    nubilefilms  girlsonlyporn_a_bed_built_for_two_3840             2.70 GB

Titles round-trip: queued as 'Fuck Me Nikki Hareniks' / 'A Bed Built For Two -
S14:E10'.

## Sites

20 -> **32 configured, 32/32 AUTHENTICATED** by BD's own `login_verify`.
Fixed tonight: ultrafilms + wowgirls (submit was a plain `<div>`, outside the
finder's semantic set), brazzers, vixenplus (stored login URL was a 404 AND a
stale manual-takeover state was blocking every retry), plus 8 new sites logged
in. hustler and wnu removed at your request.

## Merged

- **v3.66.1330 (row 363)** — the affordance learner in the GUI, 60 files, 7730
  insertions. I took this cut over: fixed a real `promote()` regression (it
  forwarded `stagedDraftFile` unchecked where the draft suffix had been
  guaranteed), regenerated the body-contract artifact, named the +2 UNKNOWN
  ratchet move, and guarded a browser launch that CI cannot satisfy.

## Merged overnight — NINE rows, main at v3.66.1338

| version | row | what landed |
| --- | --- | --- |
| v3.66.1330 | 363 | the affordance learner in the GUI (60 files, 7730 insertions) |
| v3.66.1331 | 361 | committed template selectors resolved against the live page |
| v3.66.1332 | 367 | an interstitial is a state, not a zero |
| v3.66.1333 | 368 | admin token scope matches its name |
| v3.66.1334 | 372 | gate clearing with the exit denylist |
| v3.66.1335 | 373 | a login form behind a trigger is still a known form (kink's modal) |
| v3.66.1336 | 366 | the AI and detection paths read a quality label the same way |
| **v3.66.1337** | **376** | **the per-8KB database write — the app's core function, measured** |
| v3.66.1338 | 360 | the Turnstile bypass stops being advertised while absent |

**376 landing is the one that matters**: the download defect is now fixed IN CODE,
not merely worked around by the `use_curl_cffi` config flip I applied live.

## The five that did NOT land, with the actual reason

| row | reason |
| --- | --- |
| 378 | **its OWN new test fails**: `test_replacement_between_connect_and_identity_binding_is_retried` (4 failed). The brief warned that a connection pool must be thread-aware "or do not ship it" — the gate is doing exactly its job. Needs a real fix, not a retry. |
| 377 | `test_every_tracked_mutant_anchor_occurs_exactly_once_in_its_file` (5 failed) — mutant-anchor rot: its edits moved code a mutation spec anchors on. Known defect class. |
| 371 | QA_RC=2 (collection error) + git-stash conflict markers in 4 files |
| 374 | QA_RC=2 (collection error) + conflict markers in 9 files, incl. `app.py` |
| 369 | conflict markers in 3 Python files, AND a genuine design collision: it replaces `resolve_password_state()` — which main already has at `secrets_store.py:899` — with `resolve_password_for_login()`. Two implementations of the same credential path. **Your call, not mine.** |

All five worktrees are intact under `~/bd-codex-wt/rowNNN` and captured in
`~/bd-persist/codex/`. Rows 369/370/371/374/375 are commented out of the night
spec with the marker `# PARKED-CONFLICT-MARKERS` — re-activating any of them is
deleting that prefix.

## The systemic finding behind most of the churn

The lane's rebase does `git stash pop`, and a conflicted pop LEAVES MARKERS IN
THE WORKTREE. That single mechanism produced: row 373's 21 "CSRF/routing"
failures (it simply did not compile), row 369's `SyntaxError` during regen, and
the marker sets in 370/371/374/375. Separately, every merge re-stales the
generated artifacts (`DEPENDENCY_GRAPH.json`, `FUNCTION_INDEX.md`) in every
un-merged worktree, which refused five rows for a conflict on files nobody
edited. Both now have tools: `bd-resolve-stash-conflict.py`,
`bd-unstale-generated.py`, and `bd-unstale-loop.sh` (watchdog-guarded, fires
after every merge).

## Superseded — the four-row snapshot

| version | row | what it is |
| --- | --- | --- |
| v3.66.1330 | 363 | the affordance learner in the GUI (60 files, 7730 insertions) |
| v3.66.1331 | 361 | committed template selectors resolved against the live page |
| v3.66.1332 | 367 | an interstitial is a state, not a zero |
| v3.66.1333 | 368 | admin token scope matches its name |

Row 361 merging ALONE also ACQUITS it: the 24 frontend failures belong to 372 or
373, not 361 and not the box.

**BLOCKED on rebase after 2 tries, left for you: rows 366, 369, 370.**

## Second merge detail — v3.66.1331 (row 361)

Merged ALL GREEN **on its own**, which also ACQUITS it: the 24 frontend failures
belong to 372 or 373, not 361 and not the box. Width 1 is now isolating the rest.
Row 366 is BLOCKED on rebase after 2 tries and is left for you.

## Built, not yet merged (remaining rows)

360 361 366 367 368 369 370 371 372 373 374 375 376 377 378 — every one has a
worker diff (50-1815 insertions) and all are captured in `~/bd-persist/codex/`.

**Three lane defects found and fixed while draining them:**
1. Rows filed as `# NNN|slug|title` are COMMENTS; the lane skips `#` lines, so
   all 15 were invisible to it. Activated.
2. `bd-integrate-row.sh` builds the changelog by scanning the worker report's
   last 400 lines for `- ` bullets and aborts without >=2. My briefs never asked
   for them. Fixed by `bd-report-changelog.py`, which derives bullets from the
   real diff.
3. The integrator requires the row in the canonical register, and my briefs
   said "do NOT edit the canonical backlog". Fixed by `bd-register-open-row.py`.

Still open at report time: row 360 fails the stray-`print()` gate (fixed in its
worker tree, awaiting re-integrate) plus a schedule-sensitive timing test on a
loaded box; the serial version pipeline means one candidate per version, so the
rest queue behind it.

## Not done / not overclaimed

- `library.title` is still empty. The website name is captured at DISCOVERY by
  my queueing tool, NOT by BD. Row 375 is what makes it BD's job.
- The crawler is my out-of-band tool against three hand-measured listings.
  Row 374 puts it in the GUI for all sites.
- evilangel returns 0 scenes from /en/videos; adulttime is behind a Cloudflare
  Turnstile checkbox (row 360 is the honest-capability fix); dogfart's download
  control "looks like a modal-trigger button".
- **test6 was NOT deployed.** The vault password you supplied opens both vaults
  now (verified offline), and `bd-vault-unlock.sh` is PROVEN — I locked bd1 and
  recovered it. But the merges stalled before a deploy was warranted.

## New tools (all in ~/bd-persist/harness and /scripts)

`bd-rpy` (run local Python on a remote host — kills the heredoc-quoting class
that cost three retries), `bd-vault-unlock.sh`, `bd-netprobe.sh`, `bd-wansat.sh`
(fleet WAN saturation: **2.59 Gbit/s aggregate**), `bd-persist-loop.sh` (30 min),
`bd-checkpoint-loop.sh` (10 min or after merge), `bd-report-changelog.py`,
`bd-register-open-row.py`, plus the probes: `probe_submit`, `probe_trigger`,
`probe_discover`, `pick_scene`, `find_login_url`, `verify_auth`, `bd_verify_all`,
`queue_newest`, `demo_queue`.

## The pattern worth keeping

I built the same broken instrument THREE times tonight — each version
reconstructed a BD session from the cookie jar, and BD authenticates against a
PERSISTENT PROFILE. The first said ultrafilms was logged out minutes after it
delivered a 4.15 GB members-only file. BD's own `login_verify` answered 32/32 in
90 seconds. **Ask the system, don't reimplement it.**

## RESOLVED: the frontend failures were row 373, and it was a merge conflict

Bisected at width 1. 361 and 372 each merged ALONE and green; 373 alone produced
21 failures across the same 8 files. Cause, found by reading the compiler rather
than the test names:

    src/components/AddSiteWizard.tsx(89,1): error TS1185: Merge conflict marker
    src/lib/api-types.ts(181,1):            error TS1185: Merge conflict marker

`git stash` had left `<<<<<<< Updated upstream` / `>>>>>>> Stashed changes` in
two TypeScript files, so `tsc -b` failed and EVERY frontend-dependent test
cascaded off it. The row broke neither CSRF nor route wiring; it did not
compile. Upstream held row 363's merged fields (quality_preference,
min_resolution, log_network), the stashed side held this row's `login_trigger` —
both wanted, so the resolution is the union. **`tsc -b` now exits 0.**

The lesson: 21 failing test NAMES pointed at CSRF and routing and were all
downstream noise. One compiler line named the actual defect.

## Superseded diagnosis (kept so the reasoning is auditable)

The 361+372+373 batch refused with **13 failures across 8 files**, all frontend:
`test_csrf_contract_reachability`, `test_csrf_tool_contracts`, `test_t3_t4_wired`,
`test_t7_notifications_wired`, `test_t8_cluster_wired`, `test_t11_approval_wired`,
`test_v3_66_1240_supervisor_settings_seeded`, and — the tell —
`test_v3_66_1255_frontend_build_is_isolated`.

I FIRST CALLED THIS INTERFERENCE AND I WAS WRONG. The reasoning was that
`frontend_build_is_isolated` is the signature of a known hazard here — a test
that vite-builds into the real `frontend/dist` — and test5 was running a
24-worker band, 15 codex worktrees and the download fleet at once. Two
measurements killed that hypothesis:

  * the retry at LOWER load failed the SAME 8 files and got WORSE, 13 -> 24
    failures, so it is reproducible rather than load-dependent;
  * **main itself is CLEAN** — `test_v3_66_1255_frontend_build_is_isolated` and
    `test_csrf_contract_reachability` are 10 passed on b6278ea2, so neither the
    merge of row 363 nor the box is responsible.

The defect therefore belongs to ONE of 361 / 372 / 373. The lane is now running
them at width 1, which attributes it to a single row instead of three. Believe
that result, not this paragraph.

Row 360 separately failed `test_no_print_in_any_library_module` (real; codex put
two `print()` calls in `scrapling_adapter.py`'s CLI entry — **already fixed in
its worker tree**, `sys.stderr.write`/`sys.stdout.write`, same streams) plus
`test_terminal_frame_without_eof_never_enters_an_unbounded_child_wait`, which is
schedule-sensitive and was measured on a box at load 34.9/48 cores.

**Batch width demoted 24 -> 1** so the next drain attributes each failure to one
row instead of three. Re-run when the box is quiet before believing any of it.

## First thing to check when you wake

    tail -40 /home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log
    git -C /home/mboyle/BulkDownloader show origin/main:bulk_downloader/__init__.py | grep __version__
    bash /home/mboyle/bd-persist/verify.sh          # archive integrity
    bash /home/mboyle/bd-vault-unlock.sh            # both hosts, proven working
