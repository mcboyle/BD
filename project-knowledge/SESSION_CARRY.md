# SESSION_CARRY -- state of the 2026-07-28/29 session

ASCII-only.

## What this file is, and what it is not

This is **session state**: what was done, what was found, what is open, and what
is still unverified. It is written to survive a context compaction and, if the
operator chooses, a change of session.

It states **no environment facts**. The interpreter, the deploy model, the band
rules, the guard pins and the layout live in `CLAUDE.md`, which outranks this
file, for the reason `tests/test_codex_handoff_defers_to_claude_md.py` exists:
when two agent-facing docs both assert the environment they are free to
disagree, and they did.

It is also a **register**, and `CLAUDE.md` section 1 applies to it: re-derive
every status here from source before acting on it. Numbers in this file were
measured at the time stamped below and will drift.

## Provenance -- check this before believing any row

    generated            2026-07-29 (refreshed after PR #55)
    against version      3.66.818   (bulk_downloader/__init__.py:33)
    against origin/main  f337bdc
    live-check registry  37
    guard pins           7 ok, 0 drifted, 0 missing
    working tree         clean at time of writing

If the tree has moved past `f337bdc`, treat every finding below as a claim to
re-derive, not a fact to inherit. A document that cannot be dated is
indistinguishable from one written against another tree.

Sections 1-9 were written against `d38590d` and describe how the session got
here; sections 10-13 were added or rewritten at `f337bdc` and supersede them
wherever they disagree. Section 11 is the current state.

---

## 1 | Where the work stands

Eight pull requests merged to `main` this session. The first five:

| PR | merge SHA | subject |
| --- | --- | --- |
| #48 | `71555bd` | the seeded live checks could not observe, could not submit, and reported on the wrong site |
| #49 | `9472557` | the capture holds its own vault, and restores the operator's on the way out |
| #50 | `f0cb243` | the vault teardown was armed 109 lines after the thing it tears down |
| #51 | `df7fff8` | repair the box after the capture-vault cut, and three defects it exposed |
| #52 | `d38590d` | cuts A-D: make the live checks exercisable, and fix a VPN config path that was deleting tunnels |

`#52` was a squash merge. The working branch still carries the six pre-squash
commits (`34437d5 bed47fd 0d4fb35 e1324be 1130a15 35f09b7`); their content is
identical to `d38590d`. A merged PR is finished -- follow-up work restarts the
branch from `origin/main` rather than stacking on merged history.

Diff of the branch against `main` at the time of writing: 23 files, +3725/-92.

## 2 | The four cuts in #52, and what each one actually fixed

Marked by evidence class. `[measured]` means the failure was reproduced or the
fix observed running. `[structural]` means it was established by reading the
code path and no runtime observation was taken.

**Cut A -- the seeder placed URLs and nothing ever started them.** `[measured]`
`tools/live_seed.py` queued work and returned. Nothing drained the queue, so
every check that counts completed downloads reported "no completed downloads"
regardless of how well BD worked. Added `start_seeded_site()`,
`wait_for_settle()`, `start_and_settle()`, `TERMINAL_QUEUE_STATUSES`,
`_seeded_history()`, `_report_residue()`, and `--start` / `--start-timeout`,
which `capture.sh` now passes.

**Cut B -- L6 never saw the seeded site.** `[measured]` The `auth_health` table
has no writer on the login path; the sweep that populates it fires at service
start, so a site logged in afterwards never gets a row. The seeder now POSTs
`/api/auth_health/check/<sid>` after the login poll. Separately, L6's own
denominator was wrong: it asserted over all sites rather than configured ones,
so it could report on sites it had no business judging. Restricted via
`_login_sites(ctx)`, and it now WARNs when the list is unreadable rather than
passing over an empty set.

**Cut C -- five shell sites installed Playwright browsers, carrying four
different engine lists.** `[measured]` Added `bd_playwright_engines
<core|extra|all>` to `scripts/lib/system_deps.sh` -- already the declared single
source of truth for system packages -- and repointed all five sites at it.

**Cut D -- L30 had no subject.** `[structural for the seeding, measured for the
data-loss fix]` Added a synthetic VPN tunnel (`SEED_TUNNEL_NAME`,
`vpn_tunnel_config()`, `seed_vpn_tunnel()`, `_marked_tunnel_ids()`) so L30 has
something to observe. The constraint was that the tunnel be inert with respect
to egress; it is.

While building it, a live data-loss hazard surfaced and was fixed. `[measured
on pristine source]` BD wrote a `tunnels.json` it then refused to read: a record
missing `name` made `load()` raise, and the next `save()` wrote the survivors --
which was the empty list. Reproduced before the fix:

    ON DISK BEFORE : ['operator-good', 'operator-broken']
    load() RAISED  : ValueError: tunnel config missing required field: name
    calling save() ...
    ON DISK AFTER  : []
    OPERATOR TUNNELS LOST: 2 of 2

After the fix, faulty records are quarantined rather than dropped:

    load() OK -> registered: ['operator-good']
    AFTER save(): ['operator-good', 'operator-broken']
    LOST: 0

The rule that produced this shape: **do not silently repair operator config --
quarantine and report.**

**Also in #52 -- the seed set handed BD URLs it structurally cannot consume.**
`[measured]` BD navigates a page and scrapes it for a download link; it never
fetches media directly. Handed raw media it reported `Page.goto: Download is
starting`; handed a bare manifest, `No download button found`. `_SEED_PATHS` is
now `/scene/2`, `/hlspage/2`, `/scene/2` -- two pages plus a deliberate repeat
for the dedup check -- and `tools/fixture_site.py` gained an `/hlspage/<int:sid>`
route serving a page whose download link is an `.m3u8`. `/scene/2` was chosen
because scenes 0 and 1 advertise below the default `min_resolution` of 1080 and
park at `needs_review`, which is not a terminal state.

## 3 | Standing security constraints (do not regress these)

- **The capture-vault password is never defaulted in source.** A constant would
  ship every install a known unlock, and it is immediately exploitable:
  `MasterPasswordBackend.unlock()` accepts any password on a vault with no
  ciphertexts.
- **Nothing secret may be written inside `$OUT`.** `capture.sh` tars the whole
  of `$OUT` and the operator ships that bundle to third parties. The capture
  vault therefore lives outside it, under `/tmp`.
- **The password reaches `curl` on stdin, never in argv.** `/proc` publishes a
  process's command line to every user on the box. Only the HTTP status is
  recorded, never the response body.
- **The seeder never contacts a real site.** Every seeded write goes through
  BD's HTTP API -- never raw SQL, never a direct file write. This is asserted by
  `tools/fixture_site.py`'s docstring and by the contract in
  `tests/test_live_seed.py`.

## 4 | Open, tracked

- **#3 -- the started-not-serving defect at `install_service.sh` step [4].**
  Same class as the one fixed in `capture.sh`: it polls `systemctl is-active`,
  then sleeps a fixed interval before the vault unlock. A service restart
  returns on *started*, not on *serving*, so the unlock can fire before the
  socket binds and degrades to `HTTP 000` plus a WARN. Not reproducible without
  a real systemd.
- **#4 -- the test suite writes into the working tree.** Band runs create
  `plugins/ackgate.py`, `plugins/handdropped.py`,
  `plugins/plugins.registry.json` and modify `plugins/plugins.json`. While that
  is true, `git status` is not a reliable signal of what a change touched.

## 5 | Reported, not fixed -- each needs re-derivation before action

- `DELETE /api/sites/<sid>` leaves the `auth_health` row behind, so
  `Maintenance.tsx` lists sites that no longer exist.
- Seeded downloads leave `history` residue. History is append-only and teardown
  does not remove rows; `_report_residue()` reports rather than deletes,
  deliberately.
- The Phase B login fallback records an event advertising a manual takeover it
  can never open: `start_manual_login()` returns early while
  `_login_thread.is_alive()` (`bulk_downloader/runner_auth.py:177`, and again at
  `:331`), and Phase B runs inside that thread.
- `cookies_expiry_info` misreads Playwright's `-1` session sentinel. Session
  cookies carry no `Expires` and are the default for Flask, Django, PHP and
  Rails, so this is not an edge case.
- Step [4] is inconsistent about origin: `127.0.0.1` in one place, `localhost`
  in another.
- **`/home/claude` paths.** Measured 2026-07-29 over tracked files:
  **393 files, 1541 occurrences** (77 `.py`, 36 `.md`, 13 `.sh`, plus
  `toolchain/bin/bd-*` extensionless scripts). An earlier figure of "~132 tools"
  came from a narrower, tool-only denominator that has **not** been re-derived
  against this one -- do not reconcile the two numbers without first deciding
  which population the question is about. The root-resolution decision already
  made: `$BD_HOME`, then script-derivation, then fail -- with verification, never
  a silent fallback.

## 6 | What has NOT been verified

State this plainly rather than letting it be assumed:

- **Nothing in #52 has been observed running on the box.** Every green in this
  session is sandbox green. The operator was deploying and running `./capture.sh`
  when this file was written.
- **The audit HAS now reported** -- see section 9. It ran against `35f09b7`,
  whose content equals `d38590d`, so its findings are **not** stale and do not
  need wholesale re-derivation. (An earlier note in this session predicted they
  would be. That prediction was wrong; the audit read the current content.)
- `/hlspage/2` has never been exercised end to end by BD itself. It is proven
  served, proven to carry a segmented download link, and proven that the link
  returns a real `#EXTM3U` manifest -- by `tests/test_live_seed_urls_are_servable.py`
  -- but "BD completes a download from it" is untested.
- Whether L30 flips depends on faults in the operator's real `tunnels.json`
  beyond the nameless record, which has never been seen from here.

### Expectations to check the capture against

REVISED after the audit (section 9). L6 and L8 should flip. **L11 is now
expected to be a coin flip, not a pass** -- see 9.2, item 7. L14 is plausible
but for the wrong reason (9.2, marginal list). L12 is plausible but rests on the
untested `/hlspage/2` path. L4 flips only if the provisioning run succeeded.
**L30 may FAIL rather than pass** if any tunnel on the box is configured
`enabled: false` -- see 9.2, item 5; this became reachable *because* of Cut D.
**L7 will still WARN, and that is correct** -- do not chase it.

Read the tarball in this order: the `capture-vault unlock: HTTP` line, then
`05a_live_seed.log` for whether `--start` actually drained the queue, then
L6 / L8 / L11 / L12 / L14 / L30. Worth grabbing separately on the box:

    journalctl -u bulkdownloader -n 100 | grep -iE 'vpn-config|quarantin|vpn-runtime'

## 7 | Mistakes made this session -- do not repeat them

Recorded because this codebase's failure mode is inheriting confident wrong
answers. Every one of these produced a green that meant nothing.

1. **A gate that inspected a variable instead of resolving it.** The `$OUT`
   isolation gate read the drop-in's `BD_SECRETS_FILE` line, whose value was a
   shell variable. A mutation that placed the vault inside `$OUT` passed 21 of
   21. Fixed by resolving the assignment chain.
2. **A gate that checked existence instead of ordering.** The EXIT-trap gate
   asserted the trap was present; it was, 109 lines after the thing it tears
   down. Fixed by comparing byte offsets.
3. **Verifying the wrong predicate and calling it verification.** I checked
   `gui_exposure` when the predicate is `runtime_tunable`, and wrote that "the
   green is legitimate rather than blind". It was blind, and it broke the
   operator's box with seven unit failures.
4. **Band derivation by guess.** For `secrets_store` I banded 27 files where the
   correct band was 46 -- overlap of **one**. For `app.py`, grep found 96
   importers and AST found 223; 194 were invisible to the grep.
5. **Two mutations that silently did not apply**, because I guessed at code
   shapes that did not exist (an `echo` form where the code used a bash array; a
   sed pattern with no match). The resulting "passed" was evidence of nothing.
   Always confirm the mutation landed before reading its result.
6. **A scanner that walked out of the repository.** Cut C used `rglob("*.sh")`
   and descended into sibling agent worktrees. Repointed at `git ls-files`, with
   an empty-denominator guard. The inverse error was live in Cut D at the same
   time: a filter meant to exclude one directory dropped its entire tree.
7. **A stash timeout stranded a fix.** A two-minute timeout landed between
   `git stash push` and `git stash pop`, leaving the file pristine while
   `git status` looked plausible. Caught only by checking whether the fix was
   actually in the file. Use a trap-guarded copy, not stash.
8. **An AST predicate that missed a form.** Mine did not match
   `from . import cookie_health` and returned zero modules; I nearly refuted a
   correct finding on the strength of it. The instrument fixes the denominator;
   the predicate fixes the subject -- and I got the second one wrong.

Standing operational note: **never amend a published merge commit** to satisfy a
hook. A stop-hook signature complaint on a GitHub-generated squash merge is the
hook's problem, not the commit's.

## 9 | The adversarial audit, 2026-07-29

Full verbatim report: `project-knowledge/AUDIT_2026_07_29.md`. 55 agents across
four dimensions, 50 candidate findings, **47 surviving adversarial refutation**.
Read-only; the tree was untouched.

**It is not stale.** It ran against `35f09b7`, whose content equals `d38590d`.
Its findings apply to `main` as it stands.

Its own framing, worth keeping: none of the 47 explains the FAIL in the bundle
from earlier that night. One of them (item 12) made that FAIL harder to read.
Several predict the *next* one.

### 9.1 | Independently re-derived before being acted on

Four claims were re-measured rather than inherited. All four held:

| claim | measurement |
| --- | --- |
| `sites_config.json` accumulating test sites | 7 entries, all named `E2E Test Site`; untracked |
| `LIVE_IDS` short of the registry | registry 37, `capture.sh` 35, missing `L36` and `L37` |
| L11 chooses between two marked sites | both `SEED_SITE_NAME` and `SEED_LOGIN_SITE_NAME` carry `bdseed`; `checks.py:1004` takes `seeded[0]` |
| that choice is nondeterministic | Flask `DefaultJSONProvider.sort_keys` is `True` and BD does not override it; ids are `uuid.uuid4().hex[:8]` (`app.py:4157`) -- so `seeded[0]` is the min of two random hex ids |

**A note on the fourth, because the failure was mine.** My first registry probe
imported `live_tests.harness` without `live_tests.checks`. The checks register
via decorator at import, so the registry was empty and the probe reported `0`
registered and `[]` missing -- it would have refuted a correct finding. An empty
denominator is a failure signal, not a pass. Section 0, in my own instrument,
on the very check I was using to audit other people's instruments.

### 9.2 | The findings that change what to expect from a capture

Numbered as in the full report.

- **#1 -- `tests/test_e2e_smoke.py` writes a permanent site into the operator's
  real `sites_config.json` on every suite run.** The only finding that mutates
  production state rather than misreporting it. `app.py:1167` is
  `SITES_FILE = Path("sites_config.json")`, CWD-relative and not governed by
  `BD_INSTALL_DIR`; `setUpClass` POSTs the site and `tearDownClass` never deletes
  it. Every site-count denominator in a bundle is inflated by it, and at four or
  more sites the SPA collapses idle sites by default -- so it changes the
  operator's UI. **This runs during every capture.** Related to tracked task #4
  but strictly worse: #4 is tree dirt, this is operator data.
- **#4 -- `LIVE_IDS` is 35 against a 37-check registry, and the completeness
  gate is computed from `LIVE_IDS` itself.** `EXPECTED_LIVE_TESTS` is derived by
  `awk` over the same string passed to `--only`, so it is tautological with
  respect to catalog additions. L36 (`m2-spa-bundle-served`) and L37
  (`deployed-version-coherent`) **have never run.** L36's failure branches are
  exactly the stale or half-built `frontend/dist/` class that CLAUDE.md section 7
  names as the one thing a git deploy silently does not deliver. Best
  cost-to-prevention ratio in the set: append the two ids, derive the expected
  count from `harness.registry()`.
- **#5 -- L30 FAILs on an operator-*disabled* VPN tunnel, and Cut D is what made
  that reachable.** `register_loaded_tunnels()` deliberately skips
  `enabled=false`; `vpn_config_render()` never emits the field; L30 treats
  `registered_live` as an invariant. Inert until a tunnel was seeded. The same
  check has the opposite hole -- it iterates the config list, so a live-but-
  unconfigured tunnel is structurally invisible, reproduced by deleting a tunnel
  through the raw settings editor. Do not fix that by narrowing the docstring;
  `/api/vpn/tunnels` already carries the missing side.
- **#7 -- L11 selects the wrong seeded site about half the time**, which
  silently voids the #48 fix. It lands on the login site, which by construction
  has no queue, and reports `WARN: site 'X' has no completed downloads yet` --
  verbatim the failure `_pipeline_setup`'s own docstring claims to have closed.
  The fix narrowed the denominator from all sites to marked sites; the predicate
  still does not identify the subject. **L12 and L14 are not affected** --
  `_pipeline_setup` has exactly one caller.
- **#2 -- L34 budgets a 72s wall inside a 60s timeout.** `capture.sh` passes
  `--per-check-timeout 90` so captures are safe, but
  `tools/install_livecheck_timer.sh` hardcodes 60. If that timer is installed on
  the box, L34 FAILs on every unattended fire, forever, on a healthy app, with
  journalctl as its only output channel.
- **#3 -- L18's vision call exceeds every wall on a cold Ollama** (measured
  180.2s). `warmup()` warms the text model only; L18 is the first check to need
  the vision model. L17 pings `/api/tags`, returns in 0.0s, PASSes "backend
  reachable" and leaves the model cold -- section 0 exactly. **Dormant if
  `ai_enabled` is false**, which is what this repo's `app_config.json` says.
  Settle from the box's config, not from the repo's.

### 9.3 | The shape of the rest

Tier 2 (16 items) is checks that report the opposite of, or nothing about, their
subject: L13 asserts the inverse of the truth and cannot do otherwise; L8 and L9
both PASS on a jar containing zero cookies; L23 certifies "N table counts match"
when zero comparisons happened; L15 returns PASS once a `queue` object merely
exists; L31/32/33 report "no unbounded growth" from a one-sample window.

Tier 3 is two systemic clusters. **#19**: `bd-band-derive` arms the regen gates
on a `bulk_downloader/` prefix rather than each gate's own denominator, so seven
classes of change do not band the gate that catches them -- two of them
(`test_frontend_secret_keys_in_sync`, `test_templates_list_identity`) are
*also* not covered by `bd-regen-order`, which section 2 makes mandatory.
**#20**: five gates in `tests/test_capture_vault_is_isolated.py` cannot fire,
each with an executed mutation that passes. That file is one I wrote this
session, and two of its gates are ones I had already repaired once (see section
7, items 1 and 2) -- the repairs were narrower than the properties they claim.

Tier 4 is bundle diagnostics, and there is a "negative results" section
recording what was checked and found sound, so nobody re-derives it.

### 9.4 | What the audit could not settle without the box

Ten items, listed at the end of the full report. The ones that gate a decision:
whether `ai_enabled` is true on the box (#3), whether `bd-livecheck.service` is
installed (#2), whether any tunnel is configured `enabled: false` (#5), and how
many `E2E Test Site` entries the box's own `sites_config.json` has accumulated
(#1). **Do not let anyone close these from the tree.**

### 9.5 | Suggested order, if the operator authorizes

The audit's own recommendation, unmodified: #4, then #1, then #2, then the #20
gate cluster as one test-only cut with the recorded mutations as RED-first
evidence, then the small predicate fixes (#6, #10, #17) -- each with its fixture
rewritten in the same cut, or they stay green.

No work has been authorized on any of these.

## 10 | The capture from the box, 2026-07-29T00:23Z

**This section supersedes the expectations in section 6.** It is the first
observation of any of this session's work actually running on `test4`.

    CAPTURE VERDICT: PASS
    unit  13785 pass / 0 fail / 0 error / 85 skip
    live  28 pass / 7 warn / 0 fail   (35 of 37 run)

The seven unit failures that #51 was cut to repair are gone.

### 10.1 | Confirmed working on the box

- **`capture-vault unlock: HTTP 200`** (`04_service_status.log:35`), with
  `20-capture-vault.conf` present in the drop-in list. #49/#50/#51 land.
- **Cut A.** `start_and_settle` reported `settled: true`, both URLs terminal,
  **12.1s across 7 polls**. Before this, nothing started the queue at all.
- **Cut B.** `auth_state: "ok"`, `auth_health.status: "green"`. L6, L8 and L9
  all PASS.
- L4 still WARNs on firefox/webkit, correctly -- the provisioning run that
  installs the extra engines was not re-done.

### 10.2 | Cut D never executed, and my own gate could not see that

`capture.sh:773` invokes the seeder as
`--seed --start --start-timeout 180 --login --count 3`. There is **no
`--vpn-tunnel`**, and `--seed` does not imply it (`live_seed.py:1036` branches
on `args.vpn_tunnel` separately). The synthetic tunnel was therefore never
created, and L30's `no VPN tunnels configured -- nothing to verify` is
**truthful and correct**.

`tests/test_l30_seeded_tunnel_integration.py` is 353 lines and 10 tests --
inertness, restart survival, teardown, quarantine, refusal on a broken config.
`grep -- "--vpn-tunnel" tests/ capture.sh` returns **nothing**. Not one test
asserts that anything ever *calls* the seeder.

This is section 0, committed in the same session that wrote section 0 into this
file. The capability was built and thoroughly gated; the invocation was never
wired and nothing was watching the wire. **Fix requires both**: add the flag to
`capture.sh`, and add a gate whose denominator is the invocation rather than the
function.

### 10.3 | The completed downloads wrote no file

`05a_live_seed.log` records, for both URLs:
`"status": "done", "message": "Clicked (no dl dir)", "filename": ""`.

`runner.py:3618-3623`: with no `download_dir` the runner clicks the link,
sleeps, marks the job `done`, and writes a history row with size 0. It never
fetches. `queue_site_config()` omits `download_dir` deliberately and the
docstring's reasoning is sound -- the seeder is an HTTP client, `--base-url` may
name a service with a different BD_HOME, so any path computed in the seeder is a
guess about another machine's filesystem -- but the default resolves to `None`,
so the consequence is that **no seeded download can ever produce a file**.

Downstream, and both correct as reported:

- L12 `2 completed download(s) but none via the HLS path` -- no HLS fetch
  occurred, only a click.
- L14 `none recorded as dedup-skipped` -- the duplicate was collapsed at intake
  (`"dupes": 1` in the seed log) and never reached the dedup path.

This was not in the audit. It is the same class as the seed-path defect fixed in
#52: the URLs became consumable, and the *site* is still not configured to
consume them.

### 10.4 | The audit's L11 prediction hit on the first run

L11 WARNed on site `25eaca3d`. The seed log shows `seed_login` created
`25eaca3d` and `seed_queue` created `c32e69fb`. `25eaca3d` sorts before
`c32e69fb`, so `seeded[0]` selected the login site -- which by construction has
no queue. Audit finding #7, reproduced on the box, first attempt.

### 10.5 | The four open box questions, now answered

| question | answer | consequence |
| --- | --- | --- |
| `E2E Test Site` count in `sites_config.json` | **7** | identical to the sandbox; audit #1 confirmed on the box |
| `ai_enabled` | **true** | audit #3 is a live risk. L18 passed at 4768ms only because the model was warm; cold measures 180.2s against a 90s wall |
| `bd-livecheck.service` | **not-found** | audit #2's blast radius is nil |
| VPN tunnels configured | **zero** (per L30) | the disabled-tunnel FAIL of audit #5 cannot fire yet |

### 10.6 | The seven WARNs, and which are defects

`L4` incomplete engine install (correct, provisioning not re-run). `L7` no Phase
B fallback ever needed (correct, and expected to stay). `L11` wrong site
selected (**defect**, audit #7). `L12` no HLS fetch (**defect**, 10.3).
`L14` no dedup skip (**defect**, 10.3). `L28` queue empty (correct -- `--start`
drained it, which is the Cut A fix working). `L30` no tunnels (**defect**,
10.2).

Four defects, none of which fail a capture, all of which make a live check
report on something other than its subject.

## 11 | If the operator grants access to the box

Raised and deferred this session. `CLAUDE.md` section 9 currently says the
operator deploys and runs the suite himself and that no state on the box may be
claimed unless told. That line was written for a world with no access to it; if
access is granted it needs replacing with an explicit scope. The proposed scope
was: **measure freely, mutate only with per-action authorization** -- the box
holds live VPN tunnels, site credentials and history, which is exactly the data
the Cut D fix was written to protect.

What becomes measurable there and is not measurable from here: `./capture.sh`
end to end against a real systemd and the real `tunnels.json`; tracked task #3;
the four deploy gaps in `CLAUDE.md` section 7; which of the two Playwright
browser pools BD actually runs; and full-suite runs.

---

## 11 | State at `f337bdc` -- this section supersedes the ones above

Three further pull requests merged after `d38590d`:

| PR | merge SHA | subject |
| --- | --- | --- |
| #53 | `12340fa` | make three live checks able to observe their subjects, and record what the box showed |
| #54 | `fac4c89` | L36 asserted a contract the frontend was deliberately re-rooted away from |
| #55 | `f337bdc` | give the seeded downloads a real video, and give "could not observe it" its own verdict |

### 11.1 | What is now proven ON THE BOX, not just in a sandbox

Everything below was observed running on `test4`, which is what the earlier
sections could not claim.

- **The capture verdicts PASS.** 13811 unit pass / 0 fail / 85 skip; live
  **30 pass / 7 warn / 0 fail across 37 checks**, up from 28/7 across 35.
- **All 37 live checks now run.** `capture.sh` prints
  `registry reports 37 check(s); requesting 37`, and `06_registry_count.err` is
  empty. L36 and L37 had never executed in any capture before this.
- **L36 PASSES** -- `the SPA bundle is served (937 bytes of HTML, 4 asset
  ref(s); spot-checked /assets/index-C-VpsJOv.js -> 200); /m2/queue -> 302
  /queue`.
- **L11 PASSES for the first time ever**, with a real file:
  `"status": "done", "message": "Saved: scene_002.mp4 [ok]"` and
  `-rw-r--r-- 9421 scene_002.mp4` in `~/Downloads`. The fixture serves 8192
  bytes; BD's `_embed_metadata_if_mp4` writes the rest, so the metadata path is
  exercised too.
- **L12 reports N/A**, not WARN, and the summary carries the fourth bucket:
  `1 pass | 0 warn | 0 fail | 1 n/a  (2 run)`.
- **L30 PASSED once**, in a manual run against a briefly-clean VPN config --
  `1 VPN tunnel(s) configured; IDs unique` -- proving the Cut A wiring works.
  It reverts to a WARN whenever the leak in 11.3 puts a bad record back.
- **The capture vault unlocks**: `capture-vault unlock: HTTP 200`.

### 11.2 | The L11 chain, because the shape matters more than the fix

L11 took five layers, each hiding the next, and not one of them was ever a
defect in BD's download pipeline:

1. seeded URLs 404'd,
2. then they were raw media BD cannot navigate to,
3. then nothing ever started the queue,
4. then the site had no `download_dir`, so files were clicked and discarded,
5. then the bytes served were not a decodable video.

Every fix pushed the seeded job further down the real pipeline and revealed the
next stop. The lesson to carry: when a live check reports "no completed
downloads", the prior should be that the harness cannot produce one, not that
the product cannot perform one.

### 11.3 | Open: the test suite writes the operator's real VPN config

Tracked as #10, and the most consequential open item. Confirmed by
instrumentation -- a pytest plugin wrapping `vpn_config.save()` and recording
the nodeid whenever the resolved path was the real user config:

    test   : tests/test_v3_66_729_body_contract_fixtures.py::
             test_no_control_sends_a_body_its_endpoint_refuses
    path   : ~/.config/bulk-downloader/vpn/tunnels.json
    env    : BD_VPN_CONFIG_PATH=<unset>
    tunnels: ['tun-ccc']
    global : {'leak_test_interval_s': 1, 'kill_switch_auto_recover': False, ...}
    stack  : app_vpn_api.py:391 vpn_settings_update
               -> update_global_settings(**data) -> vpn_config.py:435 save()

`PUT /api/vpn/settings` accepts the body-contract probe's synthetic payload and
persists it. On the box that left `leak_test_interval_s = 1` (default 1800), so
VPN leak tests run every second rather than every 30 minutes, and
`kill_switch_auto_recover = false` (default true). `save()` also serialises
module-global `_state["tunnels"]`, which still held the `tun-ccc` fixture from
`tests/test_v3_66_507_bucket3b_store_raw.py:221`, so a malformed test tunnel
(missing `name` and `backend`) is written into the operator's file, quarantines
on load, and blocks `--vpn-tunnel` seeding on every capture.

**Three targeted reproductions failed before instrumentation** -- the three VPN
store-raw files, all 19 files importing `vpn_config`, and a bare
`import bulk_downloader.app`. All reported UNCHANGED. Stopping there would have
produced a confident "no leak found". The trigger is cross-test: module state
from one test plus an HTTP write from another. **Do not attempt to reproduce
this by reading; use the probe.**

### 11.4 | Still open

- **#3** started-not-serving at `install_service.sh` step [4].
- **#4** the suite writes into the working tree (`plugins/plugins.json` was
  reverted rather than committed on three separate cuts this session).
- **#7** seeded history rows survive teardown and dedup-poison the next run.
  Measured: a second seeded run reported `skipped_duplicate` against the prior
  capture's rows and downloaded nothing. Manual clear is
  `DELETE FROM history WHERE url LIKE '%bdseed%'` plus
  `INSERT INTO history_fts(history_fts) VALUES('rebuild')` -- `history_fts` is
  external-content and has no triggers.
- **#10** the VPN config leak above.
- **capture.sh reporting**: it prints `seeding declined or failed` when only one
  of three seeding modes declined, then `tail -3` of the log -- which showed
  `}`, `}`, `]` while the actual reason sat on line 1.
- **Audit #1** `sites_config.json` leak. Operator chose the real fix in
  `app.py`. Same class as #10.
- **Audit #3** cold Ollama: `ai_enabled` is true on the box, `warmup()` warms
  only the text model, and L18 measures 180.2s cold against a 90s wall. It
  passed at 4785ms only because the model was warm. The first capture after a
  reboot fails on a healthy backend.
- **Audit #23** confirmed live: `graph content pin: MISSING -- UNKNOWN --
  optional check not armed`, `graph-gate exit: 0`.

## 12 | Mistakes made after `d38590d`

Section 7 covers the earlier ones. These are new, and the first is the one most
likely to recur.

1. **Committing onto squash-merged history, twice.** After a squash merge the
   branch's commits are not in `main`'s history, so pushing on top leaves the PR
   `mergeable_state: "dirty"` and CI never starts. The failure is nasty because
   `get_check_runs` then returns `total_count: 0`, which is indistinguishable
   from "CI hasn't started yet" -- I nearly waited on a run that could never
   exist. **Before any commit following a merge:**
   `git fetch origin main && git checkout -B <branch> origin/main`.
   Check `mergeable_state`, not just check runs.
2. **Adding L36 to `LIVE_IDS` on an inherited description.** The audit called
   its failure branches the stale-`frontend/dist` class; I did not re-derive
   that against the source. L36 in fact asserted `/m2/`-prefixed asset paths
   that v3.66.203 deliberately removed, so it could not pass on any correct
   deployment. Had it reached a full capture it would have verdicted FAIL on a
   healthy box. Verify-then-act exists for exactly this.
3. **A gate of mine that matched prose instead of code**, twice: an invocation
   reader that matched four comments mentioning the seeder's path, and a
   branch-order check that matched the comment quoting its own marker.
4. **A blind gate inside the gate for the N/A cut.** Removing `NA` from
   `_LEVELS` passed 10/10, because every test called the check directly while
   the allow-list is enforced in `run_all`. Found by mutation, not by reading.
5. **An empty-registry probe.** Importing `live_tests.harness` without
   `live_tests.checks` returned zero registered checks, and would have refuted
   a correct finding. The decorator registers at import of `checks`.

## 13 | What to do first in a new session

1. Re-derive everything in section 11 -- it is a register, not an authority.
2. **#10 first.** It is the only open item still changing operator state, and
   it changes VPN settings and credentials-adjacent config.
3. Then audit #1, whose fix shape the operator already chose.
4. Do not start `bd-mutate` before those two: a generic mutator generating
   shallow mutations would become another gate that cannot fail, and this
   session found five real ones the hard way.

## 14 | Filed 2026-07-31 -- carries its own provenance

    generated            2026-07-31
    against version      3.66.824 (this cut)
    against origin/main  00cf5c3 (PR #100, before this cut)
    guard pins           7 ok, 0 drifted, 0 missing

Sections 1-13 keep their own (older) stamp and the header's warning still
applies to them. This section supersedes nothing above; it only adds.

### 14.1 | CLOSED -- the ts_iso PRODUCER gap (shipped v3.66.825)

**This entry was written 2026-07-31 as "Open, tracked" and was stale within a
day.** Shipped in v3.66.825 (PR #102, squashed to 17f8e16). Left in place
rather than deleted because the detail below is still the right map of the
defect -- but do NOT act on it as open work.

WHAT SHIPPED, and it differs from what this entry predicted: the register said
THREE producer sites; four independent sweeps found FOUR. The missed one is
`runner_teach.py:351`, which sets `ts` to "" and then calls `_update_job` with
`_memory_already_updated=True` -- the flag that SKIPS the central ts_iso stamp
at runner.py:1658. The three named below were all real, so the correction ran
in one direction only.

The value now comes from one helper, `runner_util._ts_iso()`. CUT #40's G4
stayed green throughout, which is the signal the fix went in the producer and
not the consumer.

The original entry follows, for the map only:

Deliberately filed rather than fixed: it is a different subject from the cut it
was found in, and CUT #40 scoped it out on purpose.

Three terminal paths drive a job to `done` writing only the **display** `ts`
(`HH:MM:SS`, from `runner_util._ts()`) and never `ts_iso`:

- `bulk_downloader/app_sites_queue.py:545`   `api_jobs_mark()`
- `bulk_downloader/app_sites_queue.py:589`   `api_jobs_bulk_mark()`
- `bulk_downloader/runner_queue.py:303`      `load_urls()`, the `pre_done` arm

All four day-window CONSUMERS filter on `ts_iso` and are correct
(`app.py:3912`, `app_dashboard.py:66`, `app_dashboard.py:203`,
`app_queue.py:228`). So a job finished through one of those three paths is
counted by none of them.

Effect is an **under-count, not always-0**. Do not restate it as "always 0":
that was #40a's causal clause and it was never true.

**The seductive wrong fix is already guarded.** Writing
`j.get("ts_iso","") or today_iso` in a consumer would make undated jobs count
as today's, and `test_cut40_dashboard_today_iso.py::test_job_with_no_ts_iso_counts_zero`
(G4) fails on exactly that. The fix belongs in the PRODUCERS.

Unmeasured and adjacent, do not fold in without its own derivation: `runner.py`
stamps `ts_iso` LOCAL while `runner_queue.py` copies SQLite `ts_updated` (UTC),
and all four consumers compare a LOCAL `%Y-%m-%d`. A rehydrated job can land on
the wrong day near midnight on a non-UTC host. No test in the tree sees this.

### 14.2 | Closed this session -- do not re-open without re-deriving

- **#36** auth_health orphans: closed at v3.66.820. Both site-delete paths reap
  (`app_sites_id_core.py:436`, `:923`); the other two `queue_delete_site`
  callers are queue-REPLACE, where no reap belongs. Section 5 above still lists
  this as reported-not-fixed -- that row is STALE, this one supersedes it.
- **#38-F1** installer browser reach: closed at v3.66.820 by `9e46526`.
  Verified by executing the de-escalation path as an unprivileged user.
- **#40a** day-window consumers: 0 defective sites. The `HH:MM:SS` field does
  not exist in any DB table.
- **#43** audit-route docstring: shipped in PR #100.
- **#35** csrf meta premise: shipped in this cut, and it was NOT at capture
  step 3 -- that inline block was already clean. Nothing automated invoked
  `tools/diag_csrf_bootstrap.py`; it was a manual operator tool.

### 14.3 | Open, filed 2026-08-01 -- decided "file, do not fix yet"

Both were measured during the v3.66.825 day-window work and both are real. The
operator chose to record rather than cut. Re-derive before acting (section 1);
neither figure below was measured on the box.

**(a) Legacy history.file_size rows read as size drift.**

Rows written before v3.66.820 recorded a PRE-tag size. The producer half of
task #25 was fixed at 9e46526 (every path writing MP4 atoms now re-stats after
tagging, pinned by 12 tests incl. a real mutagen write, 1442 -> 2675, +1233),
but there is NO backfill anywhere in the tree: `history.file_size` is never
UPDATEd. Eight `UPDATE history` sites exist -- library_id (x3), filename (x2),
retention_excluded, status/message, history_tags.tag -- and none touches it.
`library.record_completion`, named in library.py's own module docstring as the
forward path, does not exist.

Before v3.66.825 this was invisible because `list_size_drift` could not resolve
a recorded basename at all and skipped every production row. Now that #25b
makes resolution work, those legacy rows surface as POSITIVE drift deltas.

Direction is the discriminator and it is load-bearing: a truncated or altered
download shows a NEGATIVE delta and sorts first; the legacy atom residue is
POSITIVE and roughly the size of the embedded atoms (~1.2 KB in the measured
fixture). Do NOT "fix" this with a tolerance -- a tolerance wide enough to hide
the residue also hides a real truncation of the same magnitude, which blinds
the check it is meant to protect.

The shape a fix should take, if one is wanted: an OPT-IN bd-* operator tool
that re-stats `history.file_size` for done rows whose file resolves on disk,
--dry-run first, printing a count. Never an automatic migration -- history is
append-only by design and a deploy must not silently rewrite shipped rows.

UNMEASURED: how many such rows exist on the box. That is the number that
decides whether this matters at all, and it cannot be seen from a container.

**(b) A fifth operator surface still carries raw UTC.**

`bulk_downloader/app_sites_queue.py:883` returns
`"ts": r.get("ts_updated","") or r.get("ts_added","")` from the paginated queue
endpoint. It is NOT a `ts_iso` reader, so the v3.66.825 clock fix
(`runner_util._utc_iso_to_local_iso`, applied at `runner_queue.py`'s restore
copy) deliberately did not touch it.

Consequence: the same sqlite column now feeds two operator surfaces on two
clocks. The in-memory job shape fills `ts` with a LOCAL `HH:MM:SS` via `_ts()`;
this endpoint fills the same key with a raw UTC ISO stamp. They disagree by the
host's UTC offset on any non-UTC box.

DO NOT fix this by reflex. The denominator investigation in this same programme
established that a suspected cross-surface inconsistency can be correct per
surface -- the four day-window consumers count different status sets and every
one matches its own rendered SPA label, so "making them agree" would have been
a wasted cut that broke a caption. Establish what the SPA actually renders this
field as, and whether anything sorts or diffs on it, BEFORE changing it.

**(c) test4's timezone -- MEASURED 2026-08-01, item CLOSED.**

`timedatectl` on the box reports `Etc/UTC (UTC, +0000)`, NTP active. So the
UTC/LOCAL clock clash fixed at v3.66.825 was DORMANT here and its historical
impact on this deployment is ZERO. The fix stands on its own merits -- code
should not depend on the host zone -- but do not credit it with repairing live
miscounting, because there was none to repair.

Recorded in CLAUDE.md with the other durable box runtime facts; this file is a
register and that is an environment fact. The durable consequence is there too:
a UTC box cannot reproduce timezone defects, so a green capture is SILENT about
that class and its tests must force TZ.
