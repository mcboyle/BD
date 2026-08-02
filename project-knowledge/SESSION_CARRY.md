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

RE-VERIFIED 2026-08-01 at e4a0e4b: both items below are STILL OPEN. #3's poll
was improved (15s loop, failed-fast) but still watches `systemctl is-active`
-- *started*, not *serving* -- so the class stands. #4 re-confirmed:
`plugins/ackgate.py` is neither tracked nor gitignored (`git check-ignore`
exits 1).

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

## 5 | Reported, not fixed -- RE-VERIFIED 2026-08-01 at e4a0e4b, per item

- CLOSED at v3.66.820: `DELETE /api/sites/<sid>` leaving the `auth_health` row
  behind. Pinned by tests/test_v3_66_820_auth_health_reaped_on_site_delete.py.
- CLOSED BY DESIGN: seeded downloads leave `history` residue. History is
  append-only; live_seed.py's RESIDUE_NOTE documents it, and 14.3(a)'s census
  measured the accumulated population (31 rows, all fixture, all atom-shaped).
- STILL OPEN: the Phase B login fallback records an event advertising a manual
  takeover it can never open. `start_manual_login()` returns early while
  `_login_thread.is_alive()` -- re-read today, verbatim at
  `bulk_downloader/runner_auth.py:177` and `:331`, and Phase B runs inside
  that thread.
- STILL OPEN: `cookies_expiry_info` misreads Playwright's `-1` session
  sentinel. Re-read today (`bulk_downloader/cookies.py:134`): `-1` is truthy,
  so `if not exp` misses it and `exp < now` counts the cookie EXPIRED -- the
  wrong bucket, since `-1` means a session cookie. Session cookies are the
  default for Flask, Django, PHP and Rails, so this is not an edge case.
- CLOSED (by later edits, exact cut untraced): the step [4] origin
  inconsistency. Measured today: neither `127.0.0.1` nor `localhost` occurs in
  install_service.sh at all.
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

### 14.3 | SUPERSEDED HEADING -- both items are now settled (2026-08-01)

Originally "Open, filed -- decided file, do not fix yet". Since then:
(a) CLOSED, measured on the box at v3.66.831 -- see its entry below;
(b) ADDRESSED at v3.66.829 by pinning the value rather than changing it --
see its entry below. Both bodies are kept because their reasoning and re-open
triggers are load-bearing.

**(a) Legacy history.file_size rows read as size drift -- MEASURED 2026-08-01
on test4 at v3.66.831. CLOSED: no action warranted, and the figure is
reproducible from the tool.**

    per-site pass  0 truncations, 0 residue, 0 of 31 rows examined
    whole-history  /home/mboyle/Downloads  trunc 0  residue 27  [deployment default]
                   /home/mboyle/d          trunc 0  residue 0   [d9f19e92]
    SWEEP residue bytes min/median/max : 13 / 1242 / 1242
    SWEEP residue over 64KB (NOT atom-shaped) : 0

EVERY ONE IS ATOM-SHAPED. Max delta 1242 bytes against the producer fix's own
measured atom of +1233 (1442 -> 2675, 9e46526) -- the same size class, and the
over-64KB honesty check returns zero, so nothing larger is hiding in the
population. ZERO TRUNCATIONS: nothing on disk is smaller than recorded, so
there is no silent data loss here.

AND THEY ARE NOT THE OPERATOR'S LIBRARY. All 27 resolve under the DEPLOYMENT
DEFAULT download dir, and all 31 orphan rows are named `bdseed fixture site`
-- live_seed.py residue. The one configured site contributes 0 rows and 0
drift. A re-stat would rewrite 27 fixture rows by ~1.2 KB each.

SO THE OPT-IN RE-STAT TOOL SHOULD NOT BE BUILT. That is now a measured
decision, not the assumed one this entry carried at v3.66.826.

RE-OPEN TRIGGER, unchanged: if BD is ever pointed at a library carrying
pre-v3.66.820 ORGANIC history, re-run
`venv/bin/python tools/census_file_size_drift.py` and read the SWEEP figures.
Do not re-derive the answer from this entry.

HOW THIS WAS GOT WRONG TWICE BEFORE, kept because the shape recurs: v3.66.826
read "0 of 31 examined" as ZERO and closed the item -- that is UNKNOWN, and
the per-site pass never asked about those rows at all. v3.66.831 then found
the distribution lines were gated on the PER-SITE residue, so the two figures
that decide the re-stat were unreachable in exactly the run that had residue.
Both were the same defect: a reading taken over a denominator that excluded
its subject.

The original filing follows.

**(a-original) Legacy history.file_size rows read as size drift.**

Rows written before v3.66.820 recorded a PRE-tag size. The producer half of
task #25 was fixed at 9e46526 (every path writing MP4 atoms now re-stats after
tagging, pinned by 12 tests incl. a real mutagen write, 1442 -> 2675, +1233),
but there is NO backfill anywhere in the tree: `history.file_size` is never
UPDATEd. SEVEN `UPDATE history` sites exist -- library_id (x3: library.py:175,
411, 614), filename (x2: batch_ops.py:239, storage_rebalance.py:228),
retention_excluded (retention.py:77), status/message (batch_ops.py:142) -- and
none touches it. CORRECTED at v3.66.827: this entry said EIGHT and counted
`history_tags.tag` (tags.py:251), which is a DIFFERENT TABLE. Instrument: AST
(`ast.Constant` strings plus `ast.JoinedStr` literal parts) over
`git ls-files -- bulk_downloader/*.py`, docstring nodes structurally excluded.
Predicate: `UPDATE\s+history\b` -- the trailing `\b` is what excludes
`history_tags`, since `_` is a word character. The figure is now re-derived at
test time by `test_the_update_history_figure_is_re_derived_not_quoted`, so it
cannot be quoted wrong again.
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

MEASURED 2026-08-01 on test4, and the verdict written here was WRONG.
DOWNGRADED at v3.66.827 from "CLOSED, the population is ZERO" to **OPEN,
UNMEASURED ON THIS HOST**. What the run produced was:

    done rows with a recorded size : 31
    sites in config                : 1
    rows examined                  : 0 of 31
    rows whose site_id is not in sites_config : 31

**0 of 31 examined is UNKNOWN, not zero.** The population was never measured;
it was never LOOKED AT. This entry read a coverage line -- printed precisely
so a 0/0 split could not be mistaken for a clean library -- and then made
exactly that mistake one level up. Section 0 applied to a register: the
denominator excluded the subject and the closure reported clean.

THE TOOL WAS ALSO BLIND, and that is the reason the coverage line said 0.
`tools/census_file_size_drift.py` walked configured sites and called
`list_size_drift(dd, site_id=sid)`, so a row whose site_id is not in
sites_config is examined at NO point. Fixed at v3.66.827: the census now also
runs the WHOLE-HISTORY sweep -- `list_size_drift(dd, site_id=None)`, which is
the call the panel makes -- against every resolvable download dir including
the deployment default, and reports it ALONGSIDE the per-site figures. A probe
built a database where the old per-site pass printed 0 drift while the panel's
own call returned 3, including a real -9899 truncation.

RE-OPEN TRIGGER -- what must be run before this item can be graded again:

    venv/bin/python tools/census_file_size_drift.py     # on test4, v3.66.827+

and read the SWEEP block, not only the per-site split. The item may be closed
only if the sweep examines a non-zero number of rows AND reports no positive
residue. If the sweep is still 0 rows examined, the verdict is UNKNOWN and the
item stays open -- do not convert "examined nothing" into "found nothing"
a second time.

What the run DID establish, and this part stands: all 23 orphan site_ids are
named `bdseed fixture site`. They are `tools/live_seed.py` residue, which that
tool documents at lines 82-85 as structurally unremovable -- history is
append-only, `db_log()` is its only writer and `db_prune()` (by AGE, not by
marker) its only deleter, so teardown cannot clear the rows. `_RUN_NONCE`
gives each capture run a fresh site_id, which is why there are 23. The one
configured site (`d9f19e92`, "wow") has ZERO history rows and its download_dir
holds zero files. Note that the second half of that sentence is exactly why
the sweep may STILL return 0 on this host -- but 0-because-swept and
0-because-not-looked-at are different results and only the tool can tell them
apart now. Since v3.66.827 the report also prints the orphan site_ids and
site_names, so this finding is reproducible from the tool rather than from an
ad-hoc query nobody can re-run.

The defect class is real by construction either way: `history.file_size` is
never UPDATEd (7 sites, above), so a pre-v3.66.820 row on a real library
surfaces as positive drift. Nothing measured here bears on that.

NOT A SIDE FINDING -- THIS WAS THE DEFECT. `Library.tsx:497` calls the audit
with `{ download_dir }` and no `site_id`, and `library_final.audit()` takes
`site_id: Optional[str] = None`, so the panel spans every history row. This
entry recorded that as harmless noise ("it reports the 31 fixture rows as
`missing`"). It is the same denominator mismatch that made the census blind,
written down one paragraph after the closure it invalidates, and graded
correct. Derived by reading Library.tsx and app_library.py; the 0-vs-3
divergence was later reproduced by probe.

**(b) A fifth operator surface carries raw UTC -- ADDRESSED at v3.66.829
(PR #110, d3011f1). The value was NOT converted, and must not be.**

THE REMEDY INVERTED WHEN IT WAS INVESTIGATED, which is why the caution below
was right. That field is the delta-poll cursor for the endpoint's own `since`
parameter, and `queue_changed_since` (db.py:1792) compares the cursor directly
against the UTC `ts_updated` column with a bare `>`. Localising it gives the
cursor a different clock than the column it filters: measured to over-return
west of UTC and to SILENTLY DROP ROWS east of UTC. v3.66.829 therefore added a
comment at the return site plus a round-trip regression test under forced TZ in
both directions -- and changed no value. The line is now **893**, not 883; the
cut's own comment moved it. Cite the `api_queue` handler, not a line number.

NOTE ALSO: the pinned contract has NO LIVE CALLER. Nothing in frontend/src,
tools/ or toolchain/ calls the endpoint with a `since` parameter, and its only
in-repo consumer reads status/message/filename and never `ts`. The pin exists
so a future session does not "fix" the field.

The original filing is kept below because its reasoning is what produced the
correct outcome.

`bulk_downloader/app_sites_queue.py:893` returns
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

## 15 | Handoff written 2026-08-01 -- carries its own provenance

    generated            2026-08-01
    against version      3.66.830   (refreshed after PR #111)
    against origin/main  ec4af12
    guard pins           7 ok, 0 drifted, 0 missing
    box                  test4, Etc/UTC, NTP active (measured via timedatectl)

Sections 1-14 keep their own stamps. This section supersedes nothing; it records
what existed only in a session transcript and would otherwise be lost when the
container is reclaimed.

### 15.1 | The capture reconciliation -- SUPERSEDED by two later captures

Kept for method only. The v3.66.825 capture reconciled EXACTLY against the
prediction below (14449/14364/85, cut42 skipped=0), and a v3.66.831 capture
then reconciled exactly again (14476/14391/85, +27 accounted test-by-test,
parallel 1458 unchanged). Box-verified through v3.66.831; the .832 delta was
band-verified on the box (22 passed + probe import) and its full-capture
expectation is 14477/14392/85, parallel unchanged, graph pin d942220c (armed).

BASELINE, from the v3.66.824 capture bundle (run 2026-07-31T22:58:00):

    verdict     PASS -- unit 14340 pass/0 fail/0 error/85 skip; live 36 pass/0 warn/0 fail
    collected   14425
    parallel    1458 passed              in 121.81s  (2:01)
    serial      12882 passed, 85 skipped in 2176.39s (36:16)
    graph       check-hash OK, graph-gate exit 0
    csrf diag   status 200, body 937 bytes, Set-Cookie redacted
                (name + value_len 43 + flags, NO value -- the leak fix holding
                 in a bundle that gets shipped)

PREDICTION for v3.66.825, derived not guessed (`--collect-only` on this tree
returns 14449; the delta is the 24 tests the cut added: 9 cut41 + 6 cut42 +
8 cut25b + 1 cut40 status-set pin):

    collected   14449   (+24)
    passed      14364   (+24)
    failed      0       errors 0
    skipped     85      UNCHANGED  <-- the number that matters
    parallel    1458    UNCHANGED  (all 24 new tests classify SERIAL; verified
                                    with `-m capture_parallel --collect-only`)
    serial      12906 passed + 85 skipped

WHY `skipped` IS THE SIGNAL. The cut42 clock tests self-skip when `time.tzset`
is unavailable. A rise above 85 means the host cannot force TZ, so the UTC/LOCAL
fix is UNVERIFIED on the machine it was written for -- the environment's
denominator excluding the subject, section 0 one level up. On Linux tzset IS
available, so a skip there is real signal, not noise.

The serial lane is 36 of the ~40 minutes. Raising `--workers` will not shorten
it: the serial lane is hardcoded `-n 0` and no flag widens it.

### 15.2 | The legacy file_size census -- SUPERSEDED, now a tracked tool

    venv/bin/python tools/census_file_size_drift.py

That tool (v3.66.826) replaces the snippet this section used to carry. Run it
from the install root; it is read-only and refuses rather than guessing.

THE SNIPPET THAT USED TO BE HERE WAS WRONG, and how it passed its own test is
the part worth keeping. It parsed sites_config.json as
`json.load(...).get("sites", {})`. The file is a FLAT {site_id: cfg} mapping --
app.py:1276 writes `{sid: dict(cfg)}` and app.py:1334 iterates `data.items()`
-- so that lookup could only ever return empty. On the box it exited 2 with
"contains no sites".

This section claimed it was "tested against a synthetic library before being
handed over". It was. The synthetic fixture was hand-built in the SAME wrong
shape, so it confirmed the author's assumption instead of the app's behaviour
-- section 0 applied to a fixture: the denominator excluded the subject and the
check reported clean. tests/test_census_file_size_drift.py now writes its
fixture by driving app.py's own `_save_sites_config`, making the app the oracle
so this cannot recur.

Three further defects in the old snippet, all fixed in the tool:

  - it deduped sites by download_dir, but `list_size_drift` filters by
    site_id, so a second site sharing a directory was never examined at all
  - `list_size_drift` swallows every DB error and returns [], so a failed read
    reported a clean library; row counts are now taken independently
  - the row LIMIT could truncate invisibly, because the rows it returns are
    only the DRIFTING ones -- len(rows) can never reveal that the cap bit

The tool also prints COVERAGE (examined / unknown / orphaned), which is what
turned the box result from a meaningless "0 truncations, 0 residue" into the
finding recorded in 14.3(a): 0 of 31 rows examined.

THE SPLIT IS THE DECISION, not the total. delta<0 is a file SMALLER than
recorded -- a genuine truncation, worth investigating whatever is decided about
the residue. delta>0 is the atom residue, ~1-2 KB, and is the ONLY population a
one-shot re-stat should touch. The "over 64KB" line is the honesty check: an
atom write is kilobytes, so a large positive delta is not residue and must not
be swept up by a re-stat that assumes it is.

A TIMESTAMP QUERY IS THE WRONG INSTRUMENT here, and this is worth stating
because it is the obvious first idea. The producer fix is commit 9e46526,
2026-07-31 10:35:55 UTC -- but what matters is when the BOX was running that
code, not when the commit merged, and nothing in the repo records the deploy
time per version. The mismatch itself answers the question exactly; a date
answers a neighbouring one.

### 15.3 | Known-unfixed -- ALL THREE ENTRIES WERE SHIPPED, 2026-08-01

This section was headed "do not discover these again" and listed three items as
deliberately unfixed. All three were closed within hours of it being written,
and the section was NOT updated by the cuts that closed them, because each was
correctly scoped to its own subject. That is section 1's failure mode operating
on this very file: a document goes stale silently and is then read as authority.
Re-derive before believing any row here, including this one.

- GATE-INTERNAL MUTATION ESCAPES in
  tests/test_cut35_csrf_meta_premise_retired_in_tools.py -- **FIXED at
  v3.66.830 (PR #111, ec4af12).** Both verdicts rested on a list being EMPTY,
  which cannot tell "nothing is wrong" from "the scan stopped looking". Each
  now runs through a named helper with a POSITIVE CONTROL driving the SAME
  helper the verdict does; a control over a re-implementation certifies the
  copy, and that was measured -- with controls in place but the assertions
  still using inline copies, both mutations still escaped. Before/after
  measured on the branch: both ESCAPED on pristine, both CAUGHT after.
  The filed "no test can be its own meta-test" objection was answered, not
  waived: the controls assert nothing about the file's text and add no layer,
  they are the two-sided form the gate already used on two other arms.
  STILL OPEN, and it is the honest residue: a positive control only proves the
  instrument sees what the author thought to plant. If the probe's severity
  ladder grows a new failing grade, _DEFECT_GRADE_RE goes blind and no control
  notices. Deriving that set from tools/_probe_lib is the fix and is a separate
  cut, because it adds an import edge.
- diag_d2_fresh_bd_home.py's `body_sha256` list membership -- **FIXED at
  v3.66.828 (PR #109, 62911c3).** The guard is BEHAVIOURAL: it drives the real
  _diff_probes with two probe dicts differing only in body_sha256 and asserts a
  structural difference is reported. A source scan asserting the literal
  appears in the structural list would have been the presence-not-behaviour
  class that test_capture_csrf_diag_redacts_cookies records as having survived
  mutation five times here -- and would also pass on the string in a comment.
- app_sites_queue.py's raw-UTC `ts` -- **ADDRESSED at v3.66.829 (PR #110,
  d3011f1), by pinning it rather than changing it.** See 14.3(b): the reflexive
  fix is measurably harmful. The line is now 893, not 883.

### 15.4 | Environment defects found, NOT repaired

- COMMIT SIGNING -- THIS ENTRY WAS WRONG AND IS WITHDRAWN, 2026-08-01.
  It claimed "git produces no signature and STILL EXITS 0, so every commit from
  a cloud session is unsigned", and proposed unsetting gpgsign in
  scripts/cloud-setup.sh. MEASURED: commits ARE signed. `git cat-file commit`
  shows a real ed25519 SSH signature. Acting on the entry would have DELETED
  working signatures.

  What was actually inferred rather than measured: the entry reasoned from
  `user.signingkey` pointing at a ZERO-BYTE file at /home/claude/... (while
  $HOME is /root) to the conclusion that signing must fail. It never ran a
  commit and looked. The missing fact is `gpg.ssh.program=/tmp/code-sign`, a
  symlink to /opt/env-runner/environment-manager -- the harness's own signing
  helper. It supplies the key, so the empty .pub is irrelevant and ssh-keygen
  is not needed (it is NOT INSTALLED here, which is also why local verification
  cannot work).

  SO A LOCAL `%G?` OF E OR B PROVES NOTHING ABOUT VALIDITY on this host: git's
  verify path shells out to ssh-keygen, which is absent. CLAUDE.md section 7
  already says this for GitHub's web-flow key; it is true of our own signatures
  too, for a different reason.

  WHAT THE STOP HOOK IS ACTUALLY FLAGGING is GitHub's own squash-merge commits
  (author noreply@github.com). CLAUDE.md section 7 is explicit that those are
  published history on the default branch and must NEVER be `--amend
  --reset-author`ed. The hook's suggestion is wrong for that class of commit.

  STILL GENUINELY UNKNOWN, and only the operator can settle it: whether GitHub
  marks our authored commits Verified. That needs the signing key registered on
  the account; it cannot be read from a session. Look at any PR's commit list.
  Do not re-derive it from local %G?.
- THE ORPHAN BRANCHES -- RECONCILED AND DELETED, 2026-08-01. The remote now
  carries `main` only, verified by ls-remote after the operator ran the
  deletion (session credentials cannot delete refs: HTTP 403, measured).

  WHAT THE RECONCILIATION FOUND. The 21 no-base branches were not 21
  independent trees but ONE CONNECTED FAMILY -- they all shared history with
  each other; only main (the post-zip-migration line) was disjoint. Exactly
  two tips were maximal (claude/bulkdownloader-env-verify-bdtfic,
  claude/bulkdownloader-preflight-setup-bh5n4z); all 19 others were ancestors
  of one of them, verified 19/19 with merge-base --is-ancestor. That collapsed
  the examination from 21 deep reads to one.

  ALL 22 (the family plus the merged handoff-68xjky, A=0) were verdicted
  NOTHING_UNIQUE, and every verdict was adversarially attacked and SUSTAINED.
  The A-files were content main DELIBERATELY REMOVED, proven by blob identity:
  the generated scrapers cache untracked at #60, and the PRE-security-fix
  tools/diag_csrf_bootstrap.py blob from before #95's bd_session-leak fix.
  The M-file residue (all 29 not-in-main-history files read, 708 branch-only
  lines) was uniformly the DEFECT SIDE of shipped fixes -- the `done > 0` L11,
  the self-referential capture.sh count, the CWD-relative SITES_FILE, the
  undecodable ftyp+free mp4 builder. The branches were regression material,
  not archive.

  RECOVERY: the full family (24 refs incl. main) is in the operator's bundle
  at ~/bd-orphans-2026-08-01.bundle on the deploy box, `git bundle verify`ed
  "records a complete history". Deletion was sha-guarded: examined refs ==
  remote refs == bundle refs, three-way, before the push.

  ONE UNEXAMINED LINE, stated so it is not rediscovered as a mystery:
  preflight-setup-bh5n4z was force-pushed at some point (test4's stale
  tracking ref remembered b4f0c80; the examined and bundled tip is 713dc77).
  The pre-force line was ALREADY unreachable on GitHub before the deletion
  and exists only in test4's local object store. It was never examined and
  carries no verdict.

  THE ORIGINAL TRAP, kept because it is the method lesson: an early sweep
  marked 21 branches "SAFE (adds nothing)" because the three-dot diff ERRORED
  with "no merge base" and the error output was counted as zero changed
  files. The reconciliation avoided it by construction -- two-dot tree diffs,
  every exit code captured unpiped, failed comparison graded UNKNOWN never
  zero -- and the same rule was re-applied at deletion time (a moved ref
  would have been skipped, not deleted).

### 15.5 | The squash-merge trap is CLOSED -- and how that was established

GitHub's "Automatically delete head branches" was enabled 2026-08-01 and is
VERIFIED, twice: PR #104 (a throwaway branch with an empty commit, created so
it carried no pre-setting history to confound the result) and PR #105 (which
collected claude/bulkdownloader-handoff-kcnbvx, a branch that PREDATED the
setting). CLAUDE.md section 7 carries the detail and the cases where the
prove-then-force-with-lease fallback still applies.

The reusable lesson is the method, not the setting. Checking the branch listing
after the setting was flipped would have proven NOTHING while looking exactly
like proof -- the setting acts only on future merges, so the listing is
byte-identical either way. Causing a merge and observing was the only check
whose denominator contained its subject.

### 15.6 | What shipped 2026-08-01 after this handoff, and what is genuinely left

Five cuts landed after section 15 was written. Re-derive before acting; this
row is a claim like any other.

| ver | PR | merge | subject |
| --- | --- | --- | --- |
| 3.66.826 | #107 | 00d6e35 | a census that could not see the library reported it clean |
| 3.66.827 | #108 | 66cf813 | the census asked per site; the panel asks about everything |
| 3.66.828 | #109 | 62911c3 | a field promoted to structural, promotion untested |
| 3.66.829 | #110 | d3011f1 | the queue ts is a cursor, not a display value |
| 3.66.830 | #111 | ec4af12 | an empty list certified that the scan had stopped looking |

THE ONE RECURRING DEFECT ACROSS ALL OF THEM, worth more than the individual
fixes: four separate pieces of work measured something ADJACENT to their
subject and graded it correct.

- v3.66.826's census shipped with a docstring claiming its figures matched the
  Library panel. They did not -- the panel calls audit() with no site_id, the
  census asked per site. Probe: census 0, panel 3, including a real truncation.
- v3.66.826's supporting box evidence came from an ad-hoc diag script that
  called os.path.exists() on a BARE BASENAME, resolving against the CWD -- the
  exact cut25b defect the programme had just fixed elsewhere. Its exists=False
  was read as "the files are gone". It meant "not in the current directory".
- item B's RED proof mutated the cursor INSIDE THE TEST, which establishes the
  test's own premise and says nothing about whether the pin catches the
  production defect. Redone against the production line.
- item C's second mutation anchor DID NOT EXIST on the pristine file (it is a
  constant that cut introduced), so a before/after table would have been half
  fabricated. The pristine inline form had to be found and mutated instead.

WHAT WAS LEFT at ec4af12 -- ALL FIVE SINCE RESOLVED. Superseded by 15.7.

1. 14.3(a): CLOSED at #115, measured on the box (27 atom-shaped fixture rows).
2. Box verification: a v3.66.831 capture reconciled exactly (14476/14391/85);
   .832 band-verified on the box; graph pin re-armed twice.
3. _DEFECT_GRADE_RE ladder: SHIPPED at v3.66.832 (#116), FAILING_GRADES
   single-sourced in _probe_lib, cry-wolf proven clean.
4. Commit signing: the entry was WRONG and is withdrawn in 15.4 -- signing
   works via gpg.ssh.program=/tmp/code-sign.
5. Orphan branches: reconciled, bundled, DELETED by the operator; remote is
   main-only. See 15.4.

### 15.7 | Session close 2026-08-01, e4a0e4b (v3.66.832) -- the OPEN set

Eleven PRs merged this session (#107-#117). Every item this session filed or
inherited from its own queue is closed. What remains open in the WHOLE
register, each re-verified against source today (see sections 4 and 5 for the
evidence):

  OPEN, real, unscheduled:
  - s4#3  install_service.sh polls is-active, not serving (improved, class
          stands)
  - s4#4  band runs write into the working tree (plugins/ackgate.py et al,
          neither tracked nor ignored)
  - s5    Phase B manual-takeover early-return (runner_auth.py:177/:331)
  - s5    cookies_expiry_info counts the -1 session sentinel as EXPIRED
          (cookies.py:134)
  - s5    /home/claude path residue (393 files / 1541 occurrences at last
          measure; large, dormant, decision framework already recorded)

  OPEN, deliberate deferrals (operator decisions, not defects):
  - import-graph gate walks only bulk_downloader/ and tools/, so tests/->
    edges are invisible to it (stated in v3.66.832's CHANGELOG). Widening
    makes every future test cut carry a re-freeze -- standing-cost call.
  - the pre-force line b4f0c80 of the deleted preflight branch: unexamined,
    no verdict, exists only in test4's object store.
  - Library panel shows the 31 fixture rows as `missing` (grows 1-2 per
    capture run). Correct behaviour, cosmetic noise; derived by reading.

  PENDING on the box, routine:
  - next full ./capture.sh covers v3.66.832; expect 14477/14392/85,
    parallel 1458, graph pin d942220c already armed.
  - 3 stale >6h lock files flagged by the census selftest; remove only after
    confirming no process holds them.
  - the orphan-family bundle ~/bd-orphans-2026-08-01.bundle is the SOLE copy
    of the deleted branches -- give it whatever backup rotation other bundles
    get.

  PARALLEL PROGRAM, not this queue: CODEX_HANDOFF.md's ledger (paused at its
  Analysis Task 4). Statuses are a register -- re-derive before acting.

### 15.8 | Census coverage counts rows it never examined -- OPEN

Filed 2026-08-01, measured at 922126f (v3.66.833) in the cloud container --
the probe results below are CONTAINER measurements, not box state.

THE DEFECT, one sentence: `tools/census_file_size_drift.py` counts coverage
by DB row count (`covered += n` at :226, `n` from `_done_row_counts`,
:140/:193/:208), not by what `list_size_drift` actually compared -- while
`bulk_downloader/library_final.py:336-345` silently drops any row whose
recorded basename does not resolve to exactly one on-disk file (`continue`
at :341), plus a second uncompared-but-counted drop at :345 when a resolved
path cannot be stat'ed -- so dropped rows are reported as examined, and the
whole-history sweep block (:324-336) prints drift counts with no
examined-of-total figure at all. A section-0 defect: the denominator
excludes part of its subject and the tool reports clean.

MEASURED, not reasoned -- both are container probes on synthetic data:

  - a 5-row synthetic DB with one unresolvable row printed
    `COVERAGE  rows examined : 5 of 5` (:305-306) and `complete -- every
    done row was examined` (:321-322) while the unresolvable row was never
    compared;
  - a box-shaped probe printed `SWEEP TRUNCATIONS (delta<0) : 0` and
    `SWEEP RESIDUE     (delta>0) : 0` (:335-336) over a directory where 0
    of 5 rows resolved -- indistinguishable from swept-and-clean.

WHY THE REGISTER'S CLOSED ENTRIES DO NOT COVER THIS. 14.3(a) (this file
:717-719: "Legacy history.file_size rows read as size drift -- MEASURED
2026-08-01 on test4 at v3.66.831. CLOSED: no action warranted, and the
figure is reproducible from the tool.") and 15.2 (this file :959: "The
legacy file_size census -- SUPERSEDED, now a tracked tool") closed the
question "what does the legacy drift figure mean" -- a different question
from "does the tool's coverage accounting count rows it never examined".
Neither entry looked at the resolver's drop path.

STATUS: OPEN, unscheduled. Note for the eventual fix: an honest coverage
count most likely needs library_final's resolver internals
(`_basename_index` :173, `_resolve_recorded` :197), which would add a
tests-visible import edge -- if the fix imports new symbols across modules,
the import-graph baseline re-freeze belongs in the SAME cut (CLAUDE.md
section 4).

UNKNOWN, stated as such: box impact. Whether test4's v3.66.831 sweep (27
residue rows, this file :722-725) examined all rows or silently dropped
some cannot be answered from the container, because the tool prints no
examined-of-total figure for the sweep.

### 15.9 | stale_locks warns about a subject BD never writes -- MEASURED

Filed 2026-08-01 at 922126f (v3.66.833). Container measurements plus box
evidence provided by the operator (GET /api/selftest output from test4,
2026-08-01). The warn under investigation: "3 stale lock file(s) older
than 6h (a crashed process may not have released them)".

VERDICT: the check has no real subject. Three independent layers, each
sufficient on its own:

  - NO PRODUCER. Nothing in bulk_downloader/, tools/, toolchain/ or
    scripts/ writes a `*.lock` file. Every `.lock` literal in the tree is
    the check itself (selftest.py:445/:455), the /tmp housekeeping
    consumers (dev_suite/housekeeping.py:79, app_dev_lint.py:124,
    app_dev_maint.py:58), a Plex API field (plex_deep.py:316), or a
    threading.Lock. storage_tier's real lock is an O_EXCL placeholder at
    dest_path itself (storage_tier.py:209-210), removed within the same
    move -- never `.lock`-suffixed. housekeeping.py:80's comment (".lock
    files BD's storage_tier creates as O_EXCL") documents the phantom
    convention as if real; it is wrong.
  - WRONG DENOMINATOR. check_stale_locks (selftest.py:444-469) rglobs
    `*.lock` over download_dirs + captures_root (selftest.py:650-654), and
    captures_root falls back to PROJECT_ROOT -- the whole install tree,
    venv/ and node_modules/ included -- whenever the `capture_store_root`
    app-config key is absent or not an absolute existing dir
    (dom_analyzer.py:80-94, app.py:57-64). The box's app_config.json
    carries no capture_store_root (operator-run grep, no match), so the
    fallback denominator is live there.
  - THE 3 BOX HITS ARE VENDOR FILES. All three are `yarn.lock` npm
    manifests -- dependency manifests, not process locks -- under
    .worktrees/{pytest-architecture-repair,pytest-e2e-diagnostic,
    release-819}/frontend/node_modules/combined-stream/, i.e. inside
    stale agent worktrees. tests/test_gitignore_rules_actually_match.py:63
    names this exact trap ("Ephemeral agent worktrees live under the
    repository root; rglob descends into them"); the selftest fell into
    the hole a sibling test had already labeled.

STATUS: verdict complete; FIX OPEN, unscheduled. The fix is a runtime
change to selftest.py needing its own authorized cut: delete the check,
or re-point it at an artifact BD actually writes (check_orphan_tempfiles
already covers BD's real temp artifacts). Operator-side, `git worktree
remove` of the three stale worktrees quiets the warn with no code change.

### 15.10 | The four-snapshot archive triage -- MEASURED, verdicts filed

Filed 2026-08-01. All figures operator-measured on test4 (deep inventory
`~/archive_inventory.sh` v2 -- every class found by whole-tree walk, never
a fixed-path probe -- plus five targeted probes; outputs pasted to the
session). Nothing below was executed; every action is the operator's.

The v1 inventory probed expected paths ("$d/templates" etc.) and reported
zeros a nested layout made truthful and useless -- section 0 in shell
form. v2 figures superseded it in every class.

  BulkDownloader 1 (328M) -- a curated redacted-capture corpus wearing a
  source tree. 239 `*.redacted.wacz` under from_v2/ and from_last14/;
  source is near-main (4 absent names, all explained: deploy_manifest.py
  + its test retired deliberately; diag_csrf_bootstrap.py one-off;
  player_recognition.PATCHED.py an operator hand-patch worth diffing
  before discard). KEEP the corpus + the PATCHED file; the tree around
  them is redundant.

  BulkDownloader 2 (3.0G) -- the pre-git session-handoff archive, NOT a
  release history (exactly one shipped release zip:
  BulkDownloader_v3_66_805.zip). Unique: the audit_state_v3_66_805
  witness battery (44 .py absent from main), "project files/" tools, the
  corpus + recordings packs. Four duplicate zip pairs CONFIRMED identical
  by sha256, including bdsuite_v3_66_810.zip == bdsuite_v3_66_805.zip --
  byte-identical under two different version labels, so one label is
  false; do not trust bdsuite zip names against their content. The big
  packs (pack_A/B/C/E, ~1.6G) are nested DEPENDENCY KITS (chromium,
  node, webproxy) -- rebuildable downloads, not operator data. KEEP
  witnesses/tools/corpus/recordings; duplicates and kit packs are
  deletable at the operator's discretion.

  BulkDownloader 3 (18M) -- ONE FILE MATTERS: "Proton
  Pass_export_2026-07-19_1784474629.xlsx", a password-manager export in
  plaintext, in an unencrypted snapshot dir. Secure or destroy it FIRST;
  after that B3 holds nothing unique (0 absent .py names; 92 older
  revisions; 4 PWA icons) and is deletable. NOTE: the inventory's
  credential patterns did NOT flag this file -- no pattern matches
  password-manager exports; a future sweep should add one
  (*Pass_export*, *keepass*, *bitwarden*, *1password*, *.kdbx).

  BulkDownloader 4 (11G) -- the Windows-era everything-folder. Real
  value: the capture/template store (~13k files) and 784 redacted wacz.
  Open decisions: 533 RAW wacz (may carry session material -- redact or
  secure); 91 `.db` sitting beside 90 `.db-journal` -- copies taken
  mid-transaction, which roll back on open, so none is a clean backup as
  it sits. Rebuildable bulk: cockpit_tasks/ Windows venvs
  (venv/Lib/site-packages) and PyInstaller dists
  (dist/BulkDownloader/_internal/), .worktrees/, 571 `.old` files.

CROSS-CUTTING: the keep-set should end up in ONE verified operator
bundle, as the branch family did (15.4), not four ad-hoc trees -- the
snapshots jointly hold ~350 cookie-class and ~130 secret-class files
(v2 counts, vendor-excluded) plus the items named above.

STATUS: triage MEASURED and complete; execution (secure the Proton Pass
export, dedup B2, decide raw wacz + dirty dbs, consolidate) is operator
work, not a cut. Item 4 of the 2026-08-01 queue closes with this entry.

### 15.11 | qB/JD library rows: a directory has no absolute FILE path -- OPEN

Filed 2026-08-02 alongside v3.66.837, which fixed the eight live
db_log done-sites that DO hold an absolute file path. These two do not,
and the rule for them is a product decision, not a mechanical fix.

THE SHAPE. The qBittorrent and JDownloader bridges record completion
from a transfer-state dict whose `st['filename']` is a bare NAME
(qb_bridge.py:514, jd_bridge.py:483 -- never a path), and which may name
a torrent DIRECTORY containing many files rather than a single file.
v3.66.837's contract -- record a library row only when an absolute path
is available -- means these sites currently record NO row. That is
deliberate: a wrong row is worse than no row. But it is a gap, not a
resolution, and it LOSES VISIBILITY that existed before: library.py:669
surfaces file_exists=0 rows as "missing", so a qB download used to show
up as a reconcilable missing row and now shows up nowhere until a scan
walks dl_dir.

CORRECTION, measured 2026-08-02 after this entry was first written: an
absolute path IS mechanically available at both sites. `dl_dir` is in
lexical scope at each db_log call (_try_qb_download and _try_jd_download
both bind it; it is the same value passed to client.submit(...,
dest_dir=dl_dir)). So the open question is NOT "is a path available" --
it is only WHICH FILE a directory-valued download should name. An
earlier draft of this entry said the item might "collapse into pass the
path"; it does not. It collapses into "join dl_dir", which is easier.

THE OPTIONS, and what each breaks:

  (a) record the DIRECTORY as file_path. Cheapest. But library rows
      then mix files and directories in a column every consumer treats
      as a file -- storage_tier, retention, the Plex/TPDB writers and
      library_final's resolver all assume a file, and file_exists /
      file_size become meaningless for those rows.
  (b) record the LARGEST MEDIA FILE inside the directory. Matches what
      a user means by "the download", and keeps every consumer's
      assumption true. Needs a scan at record time, a media-extension
      predicate, and a tie-break rule; multi-episode torrents get one
      row where arguably they want several.
  (c) record NOTHING and let scan() pick the files up on its backward
      pass. Correct rows, no invented rule, at the cost of the forward
      path never covering torrent downloads -- the library lags until a
      scan runs. THIS IS TODAY'S BEHAVIOUR by omission, not by choice.

UNVERIFIED, and it decides between them: whether `st['filename']` is
in practice ever a directory on this operator's sites, or always a
single file. Nobody has measured it. If it is always a file, the whole
item collapses into "pass the path" and joins v3.66.837's pattern.

ALSO OPEN, recorded here so it is not lost: the count of pre-existing
ghost rows on the box. v3.66.837 is fix-forward and touched no existing
row. The settling query, to be run on test4:

    SELECT COUNT(*) FROM library WHERE file_path NOT LIKE '/%';

The "27" figure quoted in earlier queue text is UNVERIFIED and is
probably a conflation with 14.3(a)'s history-residue count; the upper
bound implied by that section is 31. Measure before any backfill, and
prefer resolve-and-merge over delete -- each ghost has a scanned twin.

### 15.12 | Six extractor completion paths cannot execute -- OPEN, pre-existing

Filed 2026-08-02 from v3.66.837's adversarial review. NOT introduced by
that cut; it changes what the cut's own commit message claimed, and it
is the more consequential finding of the two.

THE DEFECT. runner_extractors.py assigns
`output_filename = safe_dest(rendered)` at :992, :1235, :1512, :1708,
:2222 and :2231, where `rendered` is a str (from
resolve_filename_template, or a `title_root + ext` fallback).
detect.py:537-538 is `def safe_dest(path): if not path.exists()` -- a
Path method. Measured: `safe_dest('Scene Title.mp4')` raises
AttributeError: 'str' object has no attribute 'exists'. No assignment
is inside a try. All five entry points (runner.py:2991, :3023, :3463,
:3482, :3499) wrap the call in `except Exception` and fall through, so
the jsonapi, vixen, dl8, aylo and both library-extractor arms never
reach their db_log at all. Those download paths cannot complete today.

WHY IT WAS MISSED, and the rule it illustrates. v3.66.837 asserted
these six sites were "live, MEASURED not assumed". What was measured
was the CALL GRAPH -- each function is called from _process_one in the
worker loop -- which is a different question from whether execution
reaches the line. CLAUDE.md section 1 names exactly this: a
verification can answer a different question than the item asks, and
be true and useless. The earlier "probably dead code" read was right,
for a reason nobody had established.

NOT PROVEN, and it decides the item: whether these paths have EVER
completed a download. The AttributeError was reproduced with
production-shaped input and the swallow-sites were derived by AST, but
no one drove _try_jsonapi_extractor end-to-end against a real page. If
the operator has ever seen a jsonapi_done / vixen_done / dl8_done /
aylo_done event in the live log, this entry is wrong and must be
re-derived. That check is the cheapest next step and it lives on the
box, not here.

NOTHING TESTS IT. tests/test_v3_43_68_jsonapi.py:488 asserts only
hasattr(SiteRunner, "_try_jsonapi_extractor"). A gate whose denominator
is "the method exists" cannot see a method that always raises.

### 15.13 | Session close 2026-08-02, 014f894 (v3.66.838) -- SUPERSEDES 15.7's open set

15.7 advertises itself as "what remains open in the WHOLE register" and is
therefore the section a fresh session reads as authority. It is now wrong in
BOTH directions: it lists three items that have shipped, and it predates five
filed items including 15.12. 15.7 is a dated, commit-anchored session close
(e4a0e4b, v3.66.832) and its body is NOT edited here -- rewriting it would
destroy the provenance its own heading claims. This section supersedes it.

SHIPPED SINCE 15.7 was written, so no longer open:

  - s4#3  install_service.sh polled is-active, not serving -- FIXED v3.66.836.
  - s5    Phase B manual-takeover early-return -- this was TWO defects, not
          one. The dropped on_done contract is FIXED v3.66.834; the takeover
          refusing its own calling thread is FIXED v3.66.835. Conflating them
          was the trap; two test files now keep them apart.
  - s5    cookies_expiry_info graded the -1 session sentinel EXPIRED -- FIXED
          v3.66.833.

THE OPEN SET at 014f894, superseding 15.7's:

  OPEN, real, unscheduled:
  - 15.12  six runner_extractors completion paths cannot execute
           (safe_dest(str) raises; all five entry points swallow it). LARGEST
           open defect. EVIDENCE STATE, measured 2026-08-02 and stated
           precisely because two different claims keep getting conflated:
           "CANNOT work as written" is PROVEN -- safe_dest('Scene Title.mp4')
           raises AttributeError (reproduced twice), _try_library_extractor
           was driven end to end and crashed before its db_log, and every
           caller wraps in `except Exception` so it degrades silently.
           "Has NEVER worked on this box" is INFERRED, not proven: 41 done
           rows exist (so the query sees its subject) and all 41 are
           other/transport with zero in any of the five extractor buckets;
           535 log_events fired with zero of the seven *_done kinds. That
           FAILED TO REFUTE the item -- one jsonapi_done or one `jsonapi=`
           marker would have killed it -- but the box has sites_loaded=1, so
           zero is also what an ineligible site produces. Do not upgrade the
           inference to a proof; the code proof is the one that matters and
           it stands alone.
  - 15.9   check_stale_locks has no real subject; fix unscheduled.
  - 15.8   census coverage counts rows it never examined.
  - 15.11  qB/JD library rows -- dl_dir IS in scope, so only WHICH FILE a
           directory-valued download names is open.
  - s5     /home/claude path residue. Re-measured 2026-08-02: ~324 file edits
           including 147 mirrors. The earlier "393 files / 1541 occurrences"
           and a later 1534 have never been reconciled; re-derive before
           acting on any of the three figures.
  - Audit #3  cold Ollama: ai_provider.warmup() preloads only model_text, so
           the first capture after a reboot fails on a healthy backend.
  - #7     seeded rows. RE-SCOPED, and the item as filed is stale: the
           dedup-POISONING it names was closed by the per-process run nonce
           (live_seed.py:89 + seeded_url), and db_find_url_in_history is an
           exact-URL match that cannot cross runs. What remains is unbounded
           RESIDUE accumulating in history on the box, contaminating exactly
           the denominators 15.8 is about. Three places still state the
           stale rationale as fact.

  CORRECTED, s4#4 -- THE PREMISE IS DEAD. 15.7 says band runs write
  plugins/ackgate.py into the tree, "neither tracked nor ignored". Measured
  2026-08-02: that does not reproduce. What IS observed is repo-root
  .db-wal/-shm residue, and the writing test has not been bisected. Do not
  inherit the plugins/ackgate.py wording; re-derive.

  NEW, found while costing the backlog (not previously filed):
  - the import-graph gate's predicate is blind to the
    `from bulk_downloader import X` form, missing ~240 edges. Widening the
    gate's WALK to tests/ without first fixing the PREDICATE would admit
    tests/ half-blind, so the predicate fix must land first.

  DEFERRALS, still deliberate:
  - import-graph gate walks only bulk_downloader/ and tools/ (see the new
    predicate item above -- it changes the order, not the decision).
  - b4f0c80 -- CLOSED 2026-08-02, hours after this section was written, by
    the box session it asked for. It is NOT unexamined and NOT at risk:
    it carries the tag `archive/preflight-preforce`, so it survives gc,
    and its content ("feat: provisioner installs shellcheck, so the parse
    gates can actually run") is ALREADY IN MAIN -- the `lint` group at
    scripts/lib/system_deps.sh:256, included in `all` at :280, consumed by
    cloud-setup.sh:401 as $LINT_PKGS, with the inline apt_i that drifted
    now gone. It shipped in 860d8be (#71). NOTHING_UNIQUE.
    Two things this got wrong are worth keeping, because both are the
    register misreading itself. (a) "the sole item that can become
    permanently unrecoverable" was false -- a tag pins it; the only
    residual exposure is that the tag lives on one host. (b) section 15.6
    had ALREADY recorded this verdict ("b4f0c80 -- NOTHING_UNIQUE, its
    content shipped at 860d8be (#71)") while 15.7 carried it as an open
    deferral. The register disagreed with itself and the open side was
    inherited forward twice. Re-derive before filing an item as open,
    including from this file.
  - Library panel shows 31 fixture rows as `missing`. Which query produces
    the 31 has NOT been discriminated; two different mechanisms would imply
    different fixes and different bands. Measure before choosing.

  OPERATOR ACTIONS (15.10 and box hygiene): consolidate the B1/B2/B4 archive
  keep-set into one verified bundle (B3 is deletable now its plaintext
  password-manager export was destroyed); decide B4's 533 raw wacz and its
  91 mid-transaction .db copies BEFORE consolidating; rotate the orphan
  bundle AFTER consolidation, or rotation targets the wrong artifact.

  WORKTREES -- RE-SCOPED 2026-08-02, measured. "Three stale agent
  worktrees" was wrong by six times. `git worktree list` on the box shows
  EIGHTEEN entries: the deploy checkout itself, eleven under .worktrees/
  (five on codex/* branches, several pinned at f523fe2, a commit predating
  this session's work), one stray at ~/pytest-fixture-diag-vm6-20260726-01,
  and SIX under /tmp/ already marked `prunable`. `git worktree prune -v`
  clears the six with no risk; the rest are a per-entry operator decision.
  This also re-scopes 15.9: the three yarn.lock files its selftest WARNs
  about live in three of these, so pruning shrinks the phantom denominator
  without touching selftest.py.
  Noted while measuring, not a defect: the deploy checkout reports the
  branch `claude/bulkdownloader-preflight-setup-bh5n4z` rather than main.
  Content is correct and `git reset --hard origin/main` still does the
  right thing; only the branch name in `git status` there misleads.
  `git checkout -B main origin/main` on the next deploy settles it.

TWO CITATIONS IN THIS REGISTER WERE WRONG, and both are corrected here rather
than left to mislead. Each names a genuinely CLOSED item, so the risk was not
a re-opened defect but a reader deleting the wrong guard:

  - #10, the VPN config leak, is CLOSED-VERIFIED -- driven end to end, not
    reasoned. Cite conftest.py:345-381 (the autouse fixture and its raise at
    :368), NOT the bare :304, which lands in the explanatory comment block.
    A reader who opens :304 expecting a guard finds prose.
  - Audit #1, the sites_config.json leak, is CLOSED-VERIFIED for the reported
    vector -- but NOT by the mechanism previously named. It is closed by
    app.py:1173-1183 plus the autouse chdir at conftest.py:197. It is NOT
    closed by "BD_INSTALL_DIR keying at conftest.py ~:521". The distinction
    matters: the chdir is what covers the BD_INSTALL_DIR-unset case, so a
    session that trusted the old wording could delete conftest.py:197
    believing install-dir keying covered it. It does not.

STILL UNMEASURED, recorded so it is not mistaken for closed: whether any
sites_config write site reaches _save_sites_config outside the autouse chdir
window with BD_INSTALL_DIR unset. No unsafe instance was found; the
enumeration was not exhaustive. Those are different statements.

### 15.14 | Session close 2026-08-02, a7f0f92 (v3.66.840) -- SUPERSEDES 15.13's open set

Seven PRs merged (#120-#126), eight cuts (v3.66.833-840). One box capture
PASSED clean at v3.66.838 (14424 pass / 0 fail / 85 skip, live 36/0/0,
graph pin OK, /api/health reporting sha 014f894 -- so the running process
was verified on the merged tree, not just the checkout).

SHIPPED THIS SESSION, no longer open:

  - 833  cookies_expiry_info graded the -1 session sentinel EXPIRED.
  - 834  login_async dropped on_done on two guard paths (60s stall then a
         false "Auto re-login failed"). NOTE the cut introduced and then
         fixed a false-SUCCESS regression; see 2a in CLAUDE.md.
  - 835  the Phase B takeover refused its own calling thread.
  - 836  install_service.sh certified RUNNING from is-active alone.
  - 837  every completed download minted a permanent library ghost row.
  - 838  three box failures the container bands could not see.
  - 839  the capture printed "}" instead of the seed-failure reason.
  - 840  five extractor download backends could never complete (15.12).
  - 15.12 CLOSES with 840. b4f0c80 CLOSED (see 15.13). s4#3, the Phase B
         pair and the -1 sentinel CLOSED (see 15.13).

THE OPEN SET at a7f0f92:

  OPEN, real, unscheduled -- in the order the costing pass ranked them:
  - 15.9   check_stale_locks has no real subject. DELETE is the recommended
           variant; band 318. Cheaper now: its three phantom WARNs live in
           worktrees that `git worktree prune` removes.
  - 15.8   census coverage counts rows it never examined. Band 154.
           Sequence BEFORE #7 -- its honest numbers are the instrument that
           shows whether #7's clear worked.
  - Audit #3  cold Ollama: warmup() preloads only model_text, so the first
           capture after a reboot fails on a healthy backend. Band 414.
           Three operator decisions ride along. A timeout-only change is
           REJECTED-measured: the binding wall is inside the app.
  - 10-C   the import-graph predicate is blind to the
           `from bulk_downloader import X` form (~240 missed edges). Band
           202. MUST land before any widening of the walk (10-A/10-B).
  - #7     seeded history residue. RE-SCOPED: the dedup-poisoning it named
           was closed by the per-process run nonce; unbounded residue
           remains and contaminates 15.8's denominators. Band 298.
  - 15.11  qB/JD library rows. dl_dir IS in lexical scope at both sites, so
           only WHICH FILE a directory-valued download names is open.
           Option (a) record-the-directory is REJECTED-measured: four
           consumer breakages, and it reintroduces 837's ghost class.
  - s4#4   PREMISE DEAD (see 15.13). What is actually observed is repo-root
           .db-wal/-shm residue; the writing test has NOT been bisected.
  - s5     /home/claude residue. ~324 file edits incl. 147 mirrors.
           Blocked on an operator scoping decision, not on measurement.
           Three different figures (393/1541, 1534, ~324) have never been
           reconciled -- re-derive, do not inherit any of them.

  OPEN, needs a measurement first (cheap, unblocks the item):
  - item 12  the Library panel's 31 `missing` fixture rows. WHICH query
           produces the 31 has not been discriminated; two mechanisms imply
           different fixes and different bands. Measure before choosing.

  DEFERRALS, still deliberate:
  - 10-A/10-B  widening the import-graph walk to tests/, or a separate
           tests/-baseline. Either way 10-C first. Standing cost: a
           re-freeze on roughly a quarter of future commits.

  OPERATOR ACTIONS -- the full set, in dependency order. These are not
  cuts; several are cheap MEASUREMENTS that unblock filed items, and they
  are listed first because a code item cannot start without them.

  Box measurements that unblock filed items (all read-only):
  - item 12's discriminator: WHICH query returns the 31 `missing` fixture
    rows. Two mechanisms imply different fixes and different bands, so
    this decides the item's shape and cost. Nothing can be cut until it
    is run.
  - 15.11's fact: whether qB/JD `st['filename']` is ever a DIRECTORY in
    practice -- a remote-daemon preference (qBittorrent's
    torrent_content_layout, JDownloader's package-subfolder setting) that
    is unreadable from the repo. If it is always a file, 15.11 collapses
    from a product decision into "join dl_dir".
  - the ghost-row count, for 15.8/15.11's fix-forward boundary:
    `SELECT COUNT(*) FROM library WHERE file_path NOT LIKE '/%'`. The
    "27" in older text is unverified and probably conflates 14.3(a).
  - whether v3.66.840 restores completions: after the next deploy, watch
    for `jsonapi=` / `vixen=` / `dl8=` / `aylo=` / `library_extractor=`
    markers appearing in history.message for the first time. Zero forever
    means those sites never route to an extractor; non-zero is the
    real-world confirmation the archaeology could not give.

  Box hygiene:
  - `git worktree prune -v` (six /tmp entries, risk-free), then per-entry
    decisions on the other twelve. See 15.13 for the count and shape.
    This also removes 15.9's three phantom yarn.lock WARNs.
  - `git checkout -B main origin/main` in the deploy checkout -- it
    reports a stale agent branch name. Content is correct; only the name
    in `git status` misleads.
  - deploy v3.66.839 + 840 and capture. 840 changes runtime behaviour;
    839 changes capture.sh's own failure reporting, so the next capture
    exercises both.

  Archive (15.10), strictly ordered -- each depends on the one above:
  - decide B4's 533 raw wacz (redact or secure) AND its 91
    mid-transaction .db copies (not clean backups as they sit).
  - THEN consolidate the B1/B2/B4 keep-set into one verified bundle
    (`sha256sum -c` the manifest before deleting any source). B3 is
    deletable -- its plaintext password-manager export was destroyed
    2026-08-02.
  - THEN rotate the orphan bundle. Rotating before consolidation targets
    the wrong artifact.

  FILED HERE BECAUSE NOTHING ELSE RECORDS IT -- a small code item, not an
  operator one, surfaced while triaging two capture uploads: the capture
  bundle cannot identify its own commit. 01_sysinfo.log carries the OS
  and the CHANGELOG head but no SHA, so two uploads from different trees
  were distinguishable only by content-hashing 02_SUMMARY.txt.
  `09_http_smoke.log` does carry the sha via /api/health, but only when
  the service stage ran. Adding `git rev-parse HEAD` to 01_sysinfo.log
  makes every future bundle self-identifying; it is a .sh change and
  rides any cut that touches capture.sh.

  UNVERIFIED, carried forward and not to be mistaken for closed:
  - whether any sites_config write site reaches _save_sites_config outside
    the autouse chdir window with BD_INSTALL_DIR unset. No unsafe instance
    found; the enumeration was not exhaustive. Different statements.
  - the pre-existing ghost-row count on the box. 15.8/15.11's settling
    query is `SELECT COUNT(*) FROM library WHERE file_path NOT LIKE '/%'`.
    The "27" figure is unverified and probably conflates 14.3(a).
  - whether 840 actually restores completions in practice. If any site
    routes to jsonapi/vixen/dl8/aylo or the library extractor, history
    should begin carrying `jsonapi=`-style markers for the first time.
    That is the real-world confirmation the archaeology could not give.

PROCESS FAILURES THIS SESSION are recorded in CLAUDE.md section 2a rather
than here, because they are contract, not history: regen after the last
source edit; untracked files from other cuts contaminate the regen; the
git-ls-files gates cannot see untracked test files so their pre-merge pass
proves nothing; `git fetch --prune` must accompany every post-merge reset
and a refspec-scoped prune collects nothing else; harnesses must
discriminate the exception they hunt and cut on structure not width;
subagent output is data, not evidence; a green band is not the absence of
a regression; and say which question you measured.

CAPTURE STATE: the box was captured GREEN at v3.66.838. Cuts 839 and 840
are NOT yet captured. 840 changes runtime behaviour (runner_extractors),
839 changes capture.sh's own reporting -- so the next capture exercises
both and is the one that matters.

### 15.15 | Absorbed: TASK_TRACKER and CODEX_HANDOFF open items -- ONE register from here

Filed 2026-08-02. Until now three registers ran in parallel and this file
referenced TASK_TRACKER zero times and CODEX_HANDOFF once. Everything still
open in either is copied below so this file is the single place to look.
The source files are RETIRED IN A LATER CUT, not here -- they carry 13 and 4
tracked dependents respectively, so removal is its own work. Their 283
completed rows are not reproduced: git history holds them, and a completed
row is not a thing anyone needs to read again.

STALENESS WARNING, and it is the whole point of section 1. TASK_TRACKER was
reconciled 2026-07-22 against v3.66.817. This file is written at v3.66.840 --
23 versions and three weeks later. Two of its rows are time-bound and their
clocks have simply elapsed. Treat every row below as a CANDIDATE to
re-derive, not as a confirmed open item. This session already caught the
register disagreeing with itself twice.

FROM TASK_TRACKER -- "awaiting operator", 11 rows, verbatim scope:

  - EXIT-3     PostgreSQL cutover exit-criterion: full suite green post-
               cutover plus an operator soak >= 2 weeks. Correctly blocked
               fail-closed as of the tracker's last reconcile: source had 59
               non-sqlite tables / 2196 rows against a 6-table / 2-row
               target, preflight_cutover returned ok=false, cutover_engaged
               =false, and no soak clock ever started. NOTE FOR RE-DERIVATION:
               CI now runs a green `postgres-integration` check; establish
               whether that changes this row's status before acting on it.
  - OPV-F3.1   Saved-search enqueue lane over seven uninterrupted days. Clock
               started 2026-07-23T01:37:15Z, so its window ENDED 2026-07-30.
               Either the evidence was collected and the row is closable, or
               the clock lapsed and it restarts. Elapsed, not open-forever.
  - CAP-ROBUST Held-open capture anti-bot resilience. Arms A (page-death
               recovery) and B (operator re-nav) PASSED live. Arm C --
               challenge settle/resume acceptance -- pending.
  - JW-TMPL    Auto-template for signed JWPlayer behind akamai/cloudflare.
               Sandbox-verified on a real WACZ; blocked on live capture with
               ultrafilms credentials. NOTE: the blocker is "site Ultra
               missing password", which is the SAME condition the box's
               startup selftest still prints on every restart. One credential
               fill may clear this row.
  - LOGIN-NSTEP Cross-origin N-step login flow. Code LIVE and unit-tested
               offline; the live cross-origin browser drive is operator-verify.
  - P3-T12-CALLSITE Held-open runner routes challenge settle through the
               session_capture seam. Detect/pause/handoff observed live and a
               human completed a real CAPTCHA; what is missing is an explicit
               detector-cleared/resume event. The row was deliberately NOT
               closed by inferring resume from later authenticated activity --
               that restraint is the right call and should survive re-derivation.
  - RPTYL      Reptyle authenticated capture. The capture COMPLETED (redacted
               WACZ, two resources, zero residual secret findings) but the
               row's LEGACY close criterion is api_patterns >= 1 and the run
               produced api_pattern_count = 0. Do not close it by relaxing the
               criterion silently; either meet it or retire the criterion
               explicitly.
  - FR-A6.2    Builder enrichment slice 2 (non-CF / non-video.js breadth).
               Recognizers already implemented and synthetically tested; what
               is missing is real guided-capture corpus evidence.
  - FR-A6.3    Builder enrichment slice 3, remainder. Same shape as A6.2.
  - 2c-DATA    Reptyle selector re-capture. Re-capture COMPLETE; five
               recaptured selector kinds still need review and test against
               the live DOM.
  - CORPUS-DISPOSITION  445 draft-review-required corpus items. All 445 are
               TRIAGED and retained review-required -- explicitly NOT an OPV
               failure. What remains is per-template review before any future
               promotion; none is eligible for automatic enablement. The
               deterministic index lives at
               .superpowers/sdd/corpus-disposition-review-buckets.{md,json}
               (445 semantic review, 378 selector review, 8 gate-error
               buckets) and is DATA, not a register -- it stays where it is.

FROM CODEX_HANDOFF -- 11 of 34 task groups complete, the rest open:

  - Analysis Task 4 (reachability): CODE complete and gated; re-freeze and
    review outstanding. Its frozen review packages
    (.superpowers/sdd/review-analysis-task-4.diff and -derived.diff) NO
    LONGER EXIST, so the recorded "verify these hashes before resuming" step
    cannot be run. Resuming means RE-FREEZING from the current tree. The old
    hashes identify what the packages were; they cannot validate anything.
  - Analysis Tasks 5-7: pending.
  - Governance / gate, Tasks 1-8: pending.
  - Audit / knowledge / hygiene / static-KB, Tasks 1-11: pending.

  Its design decisions worth keeping (fail-closed on ambiguous semantic
  facts; Task 3's scope/execution model; explicit bounds with secret
  redaction and atomic path-identity-checked output; Task 4's six separate
  evidence categories) are properties of the analysis, not of the retired
  environment, and should be carried into whatever picks that work up.

RELATION TO THIS SESSION'S QUEUE: none of the above overlaps the eight items
in 15.14. They are a separate, older, and largely operator- or
capture-bound body of work. Ranking them against 15.14's items is not
possible without re-deriving them first, which is a session's work on its own.
