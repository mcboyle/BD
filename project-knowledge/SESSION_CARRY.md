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

### 15.16 | Session close 2026-08-03, b1ab382 (v3.66.842) -- SUPERSEDES 15.14's open set

Ten PRs merged (#120-#129), ten cuts (v3.66.833-842). Two box captures, both
PASS. This section is the current state; 15.14 and 15.15 are history.

BOX STATE, measured -- not inferred:

  - capture PASS at v3.66.840, 2026-08-03T00:18:04:
    14518 total / 14433 passed / 0 failed / 0 errors / 85 skipped,
    live 36 pass / 0 warn / 0 fail. Graph pin [2b] OK. /api/health
    reported sha a7f0f92 + version 3.66.840, so the RUNNING PROCESS was
    verified on the merged tree, not merely the checkout.
  - the arithmetic reconciles: 14424 -> 14433 is +9 passed on +9
    collected (14509 -> 14518), exactly 839's 4 and 840's 5 new tests.
    Nothing was skipped into passing.
  - v3.66.841 and 842 are NOT yet captured.

ITEM #7 IS NOW MEASURED, and the capture's own words correct this
register's mechanism. Count: 58 bdseed history rows remain after
teardown. The reason teardown cannot remove them, verbatim from
05a_live_seed.log: history is APPEND-ONLY -- db_log() is its only writer
and db_prune(), which deletes by AGE not by marker, its only deleter --
so no marker-matched teardown over the HTTP API can remove the row a
COMPLETED seeded download leaves. Its library_record row and the
downloaded file under the seeded site's download_dir are the same class.
CONSEQUENCE FOR THE FIX: "add a clear to teardown" is not available over
the HTTP surface. The options are a direct DB path, or accepting that
this residue is age-pruned and making the reporting say so. The tool
already reports it rather than hiding it, which is the right behaviour.

TWO THINGS THE CAPTURE COULD NOT SETTLE, and both stay UNVERIFIED:

  - v3.66.840's real-world effect. Seeding drives the FIXTURE site, which
    never routes to jsonapi/vixen/dl8/aylo, so zero extractor markers is
    the expected result whether or not the fix works. It needs a real
    site download. Watch history.message for jsonapi= / vixen= / dl8= /
    aylo= / library_extractor= appearing for the first time.
  - v3.66.839's seed-failure reporting. Seeding SUCCEEDED, so the failure
    branch never ran. Operationally good; the improvement is still
    untested in the wild.

REGISTER CONSOLIDATION IS COMPLETE. TASK_TRACKER (v3.66.841) and
CODEX_HANDOFF (v3.66.842) are retired, each with a stays-retired gate and
a tombstone in BD_TOOLCHAIN_REFERENCE.md. There is now ONE register
(this file) and ONE agent-facing contract (CLAUDE.md). Their open items
were absorbed into 15.15 BEFORE any deletion.

THE OPEN SET at b1ab382 -- unchanged from 15.14 except where noted:

  - 15.9   check_stale_locks has no real subject; DELETE recommended.
           Band 318. Cheapest real fix on the list.
  - 15.8   census coverage counts rows it never examined. Band 154.
           Sequence BEFORE #7.
  - Audit #3  cold Ollama warms only model_text. Band 414.
  - 10-C   import-graph predicate blind to `from bulk_downloader import X`
           (~240 edges). Band 202. Must precede 10-A/10-B.
  - #7     seeded history residue -- NOW MEASURED at 58 rows, and the
           fix shape is narrower than filed (see above).
  - 15.11  qB/JD library rows. Needs the directory fact from the box.
  - s4#4   premise dead; real observation is repo-root .db-wal/-shm
           residue, writer unbisected.
  - s5     /home/claude residue, ~324 edits. Operator scoping decision.
  - item 12  Library panel's 31 `missing` rows -- needs the discriminator
           measurement before it can be scoped.
  - 10-A/10-B  standing deferral, gated on 10-C.
  - 15.15's absorbed rows: 11 from TASK_TRACKER (EXIT-3, OPV-F3.1,
    CAP-ROBUST, JW-TMPL, LOGIN-NSTEP, P3-T12-CALLSITE, RPTYL, FR-A6.2,
    FR-A6.3, 2c-DATA, CORPUS-DISPOSITION) and ~23 CODEX task groups. All
    stale-dated; re-derive before acting.

OPERATOR ACTIONS still outstanding: deploy 841+842 and capture; the four
read-only box measurements in 15.14; `git worktree prune -v` (18 trees,
6 prunable); `git checkout -B main origin/main` in the deploy checkout;
and the archive sequence (decide B4's 533 raw wacz AND its 91
mid-transaction .db copies -> consolidate with a verified manifest ->
THEN rotate the orphan bundle).

PROCESS DEBT FOUND TWICE IN THREE CUTS, recorded because it recurred: a
band that omitted a file surfaced the miss ONE CUT LATE, both times.
v3.66.839's fixed-width window failed 840's band; v3.66.841's stale
mutation anchor failed 842's band. CLAUDE.md section 2a already states
the rule; these are the two instances that prove it is not theoretical.

### 15.17 | v3.66.842 IS CAPTURED -- SUPERSEDES 15.16's box state and item #7

15.16 was written before its own PR merged and before the 842 capture
existed. Two of its statements are now superseded and one is corrected:
it says "Ten PRs merged (#120-#129)" -- #130 also merged, as 8221d64 --
and it says "v3.66.841 and 842 are NOT yet captured". They are. Its open
set survives intact except where noted below.

THE 842 CAPTURE, measured from the bundle -- not inferred:

  - PASS at v3.66.842, run 2026-08-03T02:02:11:
    14479 total / 14394 passed / 0 failed / 0 errors / 85 skipped,
    live 36 pass / 0 warn / 0 fail / 1 n/a.
  - /api/health reported sha 8221d64c3e5f + version 3.66.842. That is
    the merged main tip, so the RUNNING PROCESS was verified on the
    current tree -- not an intermediate, and not merely the checkout.
  - graph pin [2b] OK against a re-pinned hash (1bc3a28a6302bb91),
    gate exit 0. The operator re-pinned after the deploy; without that
    step step [2b] would have reported drift.
  - parity inventory 1245 items; GET / 200, routes 1002, SPA served.
  - both JUnit XMLs report failures=0 errors=0 directly. The strings
    "Traceback" and "ERROR:" DO appear in the bundle, inside captured
    stdout of PASSING tests -- a grep for them is not a result count.

THE TWO COUNTS THAT MOVED, both reconciled to exhaustion. Recording the
method as well as the answer, because a count that drops and is waved at
is the shape section 0 warns about:

  - tests 14518 -> 14479 (-39). Diffed the COLLECTED testcase ids out of
    both bundles' XMLs, not the totals: 46 gone, 7 new. The 46 are
    test_tasktracker_gen (13), test_v3_66_754_tracker_decided_against
    (11), test_tasktracker_sync (7), test_v3_66_721_tasktracker_audit
    (6), test_codex_handoff_defers_to_claude_md (6),
    test_tasktracker_status (3) -- the six retired files and NOTHING
    else. The 7 are the two stays-retired gates. No test silently
    stopped being collected, which the totals alone could not show.
  - graph files 1155 -> 1153 (-2). l0_extract's denominator is PROD at
    tools/l0_extract.py:33-37 -- bulk_downloader/*.py, tools/*.py,
    frontend/src/*.ts,tsx. Exactly two deletions fall inside it
    (tools/tasktracker_gen.py, tools/tasktracker_sync.py). The six
    deleted test files and project-knowledge/tasktracker_gen.py are
    OUTSIDE that denominator and correctly do not move it.

ITEM #7 -- RE-SCOPED AGAIN, and smaller than 15.16 leaves it. Residue is
now 62 rows, up from 58: +4 across one capture run. More useful than the
count: the honest-reporting half of the fix IS ALREADY SHIPPED. The
append-only / db_prune-deletes-by-AGE explanation that 15.16 quotes
"verbatim from 05a_live_seed.log" is not the capture describing a gap --
it is tools/live_seed.py:979-980 doing its job, landed in PR #71. 15.16
lists "making the reporting say so" as one of two options; that option
is DONE and is not work. What remains is a single operator decision:
add a direct-DB clear path, or accept age-pruning. There is no reporting
work left in item #7.

STILL UNVERIFIED -- the 842 capture did NOT settle either, and neither
moved:

  - v3.66.840's real-world effect. Grepped the whole bundle for
    jsonapi= / vixen= / dl8= / aylo= / library_extractor=: zero hits.
    That is the EXPECTED result whether or not the fix works, because
    seeding drives the fixture site and it never routes to an extractor.
    This capture is SILENT on 840, not evidence against it. Do not
    record a second clean capture as accumulating confidence.
  - v3.66.839's seed-failure reporting. Seeding succeeded again (every
    result ok:true), so the failure branch has still never executed.

OPERATOR ACTIONS: "deploy 841+842 and capture" is DONE. Still
outstanding, unchanged from 15.16: the four read-only box measurements
in 15.14; `git worktree prune -v` (18 trees, 6 prunable);
`git checkout -B main origin/main` in the deploy checkout; and the
archive sequence (decide B4's 533 raw wacz AND its 91 mid-transaction
.db copies -> consolidate with a verified manifest -> THEN rotate the
orphan bundle).

A CAPTURE BUNDLE STILL CANNOT IDENTIFY ITS OWN COMMIT, and this session
paid for it twice: two uploads arrived together and were separable only
by reading 02_SUMMARY.txt and 09_http_smoke.log. 01_sysinfo.log carries
the OS and the CHANGELOG head but no SHA. 09_http_smoke.log carries the
sha via /api/health, but ONLY when the service stage ran. Adding
`git rev-parse HEAD` to 01_sysinfo.log fixes it -- a .sh change that
rides any future capture.sh cut. Nothing else records this.

### 15.18 | Backlog items 1-9 investigated by a 38-agent pass -- SUPERSEDES 15.16/15.17's open set

2026-08-03, overnight, from f92bdfc. Nine filed items were investigated in
parallel, each plan then attacked by three independent adversarial verifiers
(denominator / blast-radius / evidence). FIVE of the nine plans had a FATAL or
MAJOR defect that its own author did not see. That ratio is the finding: a
plan produced by a careful reading of source is not the same thing as a
correct plan, and the cheap way to learn the difference is to have someone try
to break it before it ships rather than after.

SHIPPED:

  - v3.66.843 (f92bdfc, PR #133) -- 10-C. See below; the count was wrong in
    the register and the DEFECT WAS IN THE WRONG FILE.
  - v3.66.844 (PR #134) -- 15.9, check_stale_locks deleted.
  - a doc cut (9d85cde, PR #132) correcting CLAUDE.md section 4's axis-6
    table, which was measured at seven and is nine.

THE AXIS-6 TABLE WAS STALE THE SESSION IT WAS WRITTEN. v3.66.833 measured
seven and wrote "all 51 grep candidates classified, zero unknowns". Then
v3.66.841 and v3.66.842 EACH ADDED A MEMBER -- test_task_tracker_stays_retired
and test_codex_handoff_stays_retired, both running the byte-identical
`git ls-files -z -- '*.py' '*.sh'` pathspec that the table already classified
"yes" for test_deploy_manifest_stays_retired. Nobody re-derived. Two
independent agents in this pass found it separately while deriving unrelated
bands, which is how it surfaced at all.

  The more useful half: a TENTH gate is axis-6 and the table's own recipe
  CANNOT FIND IT. tests/test_pin_index_in_sync.py imports and reloads
  tools.build_pin_index (:48-50), which globs (root/"tests").glob("*.py") at
  :151 and :211. Adding a test file staleness-fails that gate, but nothing in
  the test's source matches `ls-files|rglob|--collect-only` -- the enumeration
  lives one import away. Section 2a already recorded the CONSEQUENCE (three
  untracked RED files inflated test_files_scanned by 3) without ever naming
  the gate. The recipe is a starting point, not a denominator.

10-C, SHIPPED, AND EVERY INHERITED FACT ABOUT IT WAS WRONG:

  - the defect was NOT in tools/decomp/import_graph_gate.py as filed. That
    gate has no import predicate of its own; it funnels every mode through
    tools/dependency_graph.py:_internal_imports, which is where the blindness
    lived.
  - the count was NOT ~240. It is 246. The inherited figure is reproducible
    and it is the policy-B measurement -- dropping the 6 edges whose alias is
    a name the package __init__ re-exports rather than a submodule. A figure
    that is merely SMALLER reads as a rounding error rather than as the answer
    to a different question, which is why the 6 are enumerated separately in
    the commit body.
  - _internal_imports has ONE call site but TWO consumers. `prov = sorted(imps
    - {rp})` at :245 lands in blueprints[key]["providers"] at :253, so the
    blueprint sub-graph moved too: 9 of 160 records, ~38% of the
    DEPENDENCY_GRAPH.md diff. The original plan's expectation list omitted it
    entirely; a blast-radius verifier measured it.
  - THE PART WORTH KEEPING. The originally-planned RED test could not
    distinguish the intended fix from routing the alias through _node(), which
    the plan's own prose forbade. Four stems exist in BOTH packages
    (llm_readiness, multi_site_benchmark, temporal_benchmark,
    validation_corpus) and _node tests bd_mods FIRST, so a _node-routed
    implementation silently mis-targets -- but produces a BYTE-IDENTICAL
    246-edge result on today's tree, because no live site trips the collision.
    A verifier measured the fixture passing that mutant 5/5. The shipped
    fixture now builds the collision deliberately; re-measured, the mutant
    dies 1-failed/4-passed on that one assertion and the other four stay blind
    to it. The count could never have caught this. Only the fixture can.

FOUR ITEMS ARE HELD, AND TWO OF THEM ARE HELD BECAUSE THE ITEM WAS WRONG:

  - #7 -- PREMISE REFUTED, and the refutation is already written in this
    codebase. "db_prune() is history's only deleter, so no marker-matched
    teardown over the HTTP API can remove them" is FALSE:
    batch_ops.bulk_delete deletes by id over POST /api/batch/delete, and
    db.py:988-992 already records the retraction. An end-to-end probe removed
    exactly the seeded rows and spared a non-seeded one. THE OPERATOR'S CHOSEN
    DESIGN (a direct-DB clear) WAS SELECTED ON THE STRENGTH OF THE FALSE
    CLAUSE, so it needs re-deciding rather than executing: the HTTP path
    exists, works, and does not require live_seed to grow a DB write path.
    15.17 repeated the false clause as measured fact. It is not.
  - Audit#3 -- the investigator CONFIRMED the premise (warmup() at
    ai_provider.py:350 really does warm only model_text) and the evidence
    verifier still said DO NOT CUT, which is the interesting shape. A shipped
    both-model boot warm ALREADY EXISTS: ollama_boot_probe.py warm_text /
    warm_vision plus ai_boot_readiness.py (warms both, verifies VRAM residency
    via /api/ps, 6 retries), wired as systemd unit bulkdownloader-ai-ready at
    install_service.sh:271. So the real question is not "add a vision warm" --
    it is WHY THAT UNIT DID NOT LEAVE THE VISION MODEL WARM on the box. Its
    _attempt raises gpu_unavailable BEFORE any warm if nvidia-smi fails, which
    is the restart loop the audit itself recorded. Independently
    disqualifying: the proposed warm runs INSIDE L18's own 90s-walled request,
    so the 180s load merely moves from generate into warmup and L18 still
    fails; and the binding timeout is the 60s _call_model cap, not the 90s
    join, so the 120-vs-300s question posed to the operator is not the binding
    number. One journalctl plus /api/ps after a reboot settles it.
  - s4#4 -- the bisect is REAL and CONFIRMED BY RUNNING, and the proposed fix
    is disqualified. Mechanism: subprocess children spawned with cwd=<repo
    root> that import bulk_downloader.app, whose MODULE BODY runs db_init()
    unconditionally at app.py:80. Confirmed by running:
    tests/test_v3_66_302_gui_parity_reconcile.py spawns
    tools/gui_parity_inventory.py with cwd=_REPO and no env override, four
    times per file run. BUT the evidence verifier measured a larger class the
    fix leaves armed: module-scope bulk_downloader.app imports at pytest
    COLLECTION time across 34 test files, so `--collect-only` from a bare cwd
    deposits the whole DB before any fixture runs. The proposed tool-level fix
    also rebinds db.DB_PATH GLOBALLY inside _routes_from_app(), which 10 test
    files execute in-process -- the rebinding survives fixture teardown,
    defeats clean_workdir, and produces order-dependent cross-file
    contamination that would surface on the box under --dist loadfile and not
    in a one-file-at-a-time band. Redesign before any cut.
  - item12 -- PARTIALLY CONFIRMED and NOT CUTTABLE without one answer only
    Matt has. The panel's "missing" number comes from the HISTORY table via
    library_final.audit(), and library.py:669 (the mechanism the item names)
    IS NOT REACHABLE FROM THE SPA AT ALL. A THIRD producer the item never
    mentioned: app_widgets_api._collect_library_data:234's site-scoped inline
    SQL, which is what the Dashboard "Missing files" KPI actually renders and
    which OVERRIDES the :647 value. The blocking unknown is not a query --
    it is WHAT STRING WAS IN THE PANEL'S "Download dir to audit" BOX, since
    mechanism A counts a row missing whenever its bare basename yields zero
    hits in the audited directory. Nothing in the repo, the register or any
    log records it. Second unknown, equally unresolved: whether anyone ever
    read the panel number at all -- this file labels the claim "derived by
    reading" at :1203 and :1556 and no capture artifact, log line or
    screenshot carrying an observed 31 was found.

s5 -- MEASURED, no cut (read-only by instruction). 391 tracked files / 1525
occurrences of /home/claude; bulk_downloader/ contains ZERO. The three
historical figures are now reconciled rather than merely listed: 393/1541 is
the 07-29 tree minus SESSION_CARRY's own hit; 1534 is a real repeated
measurement across 08-01/02 commits and THE COUNT OSCILLATES -- it does not
monotonically shrink, so a smaller number is not evidence of progress;
~324/147 is Python hit-files 322 + mirrors 145 at 08-02, off by a consistent
+2. Correction to the scoping input: the "executable = 772" figure is
PYTHON-ONLY. The shell bucket adds ~277 command-line occurrences including
toolchain/bin/bd:7 (`. /home/claude/bdenv.sh` -- the suite entrypoint) and
bd-install:99 (`rm -rf /home/claude/work/*`), so corrected executable is
~1062, of which genuine default-path VALUES are 212. Roughly half of
everything is mirror duplication (370 distinct authoritative exec sites). Two
traps for whoever scopes it: toolchain/bin/bd matches neither `bd-*` nor
`*.py` so it is outside the mirror gate's pairing rule AND outside the obvious
RED-test population; and tests/test_generated_artifact_workflow.py:195
POSITIVELY PINS /home/claude in scripts/build_release.sh, so a blanket sweep
turns it red.

15.8 -- PLANNED IN FULL, NOT CUT. Premise CONFIRMED, both reproductions
measured. Design: one loop two outputs -- size_drift_scan() in library_final.py
with list_size_drift becoming a projection, so the drift figure and the
coverage figure can never be about different passes. Measured: NO import edge
added (the census already imports library_final). Four MAJOR objections must
be folded in before it ships, and they are the reason it was not rushed at the
end of a long session: (1) the headline RED assertion `'rows examined : 4 of
5' in text` also matches the NEW sweep line, because both are 4-of-5 on that
fixture -- use whole-line assertions; (2) NOTHING anywhere asserts the
completeness line is ever PRINTED, so a mutant deleting it passes the entire
band -- add an all-resolved third shape; (3) sweep_examined_rows is a per-ROW
union while SWEEP TRUNCATIONS/RESIDUE are per (row,directory) PAIR, so one row
in two dirs renders "examined 1 of 1" above "RESIDUE : 2"; (4) TARGETS is 22,
not 23.

NEW ITEMS FOUND WHILE VERIFYING OTHERS -- filed, not fixed:

  - dev_suite tempdir_clean (housekeeping.py:79, :150-156) DELETES any *.lock
    in the system temp dir older than 1h with no BD scoping, while its
    docstring at :117-119 claims it is "scoped strictly to ... never touches
    anything else". Since no BD producer exists (see 15.9), its only possible
    targets are OTHER PROGRAMS' lock files. Reachable at POST
    /api/dev/tempdir_clean, dev-mode + CSRF gated, dry_run defaults True.
  - check_orphan_tempfiles does NOT cover what 15.9's rationale claimed. It
    uses a non-recursive base.glob(), while crash_recovery.py:141 rglobs
    *.part precisely because those nest, so a nested .part is missed. The
    sentence "check_orphan_tempfiles already covers BD's real temp artifacts"
    should not be reused.
  - ARCHITECTURE_INVENTORY.md is tracked, was already stale at 1273 edges
    against a 1371 tree, sits in NO regen chain and NO CI check, and v3.66.843
    widened the gap to 1617. Nothing catches it.
  - tools/dependency_graph.py --selftest FAILS on pristine and did before this
    session: selftest() hardcodes `nbp == 10` while the tree has 160
    blueprints. tests/test_dependency_graph_in_sync.py derives its expectation
    from source and therefore passes, so no gate sees it.

OPERATOR ACTIONS, unchanged from 15.17 except the first: deploy 843+844 and
capture -- and RE-PIN THE KNOWLEDGE_GRAPH CONTENT HASH FIRST, because both
cuts touch bulk_downloader/ or tools/ and step [2b] will otherwise report
drift that capture_verdict.py escalates to a whole-capture FAIL. No
frontend/dist rebuild is needed for either. Still outstanding: the four
read-only box measurements (item 12's discriminator is now known to need the
audited-directory STRING, not a query); `git worktree prune -v`;
`git checkout -B main origin/main` in the deploy checkout; and the archive
sequence.

### 15.19 | v3.66.844 captured PASS; 15.9 confirmed on the box; a capture blind spot found

Filed 2026-08-03 from the capture bundle plus one operator probe. This section
adds to 15.18's open set rather than superseding it; nothing in 15.18 changed.

THE CAPTURE, measured from the bundle:

  - PASS at v3.66.844, run 2026-08-03T09:34:41: 14486 total / 14401 passed /
    0 failed / 0 errors / 85 skipped; live 36 pass / 0 warn / 0 fail.
  - /api/health reported sha ff7b5cb3050b + version 3.66.844 -- the merged
    tip, so the running PROCESS was verified, not merely the checkout.
  - graph pin [2b] OK against a re-pinned hash (5339f1d69d34660b), exit 0.

  Count delta +7 (14479 -> 14486), reconciled on COLLECTED TESTCASE IDS out of
  both bundles' XMLs rather than on totals:
    +5  test_import_graph_sees_package_form  (v3.66.843, 10-C)
    +4  test_stale_locks_check_is_gone       (v3.66.844, 15.9)
    -2  test_v3_66_642_selftest_battery      (15.9 removed exactly the two
        parametrised cases for the deleted check)
  Nothing dropped silently.

15.9 IS CONFIRMED ON THE BOX, and the confirmation did NOT come from the
capture. Operator ran `curl -fsS localhost:5555/api/selftest | grep -i -A3
lock` against the live service: the only matches were table-name substrings
(achievements_unlocked, content_blocklist, cookie_relogin_log, fed_url_locks),
and the WARN string "stale lock file(s)" was absent. That grep's denominator
DOES contain the subject -- it would have matched the WARN text -- so the
negative is valid. The check is gone from a live response returning real
content.

THE BLIND SPOT, which is worth more than the item that exposed it.
`capture.sh` NEVER CALLS /api/selftest -- `grep -n selftest capture.sh`
returns nothing. So no capture bundle can confirm or refute ANY selftest
change, and every selftest check on the box sits permanently outside the
capture's denominator. The original 3-WARN evidence for 15.9 came from a
manual GET the operator ran on 2026-08-01, never from a capture; that was not
noticed at the time and reads in the register as if it were capture evidence.

  HOW IT BIT, recorded because the shape is the recurring one: this session
  grepped the 844 bundle for "stale lock", got silence, and reported the WARN
  as gone. The claim was true, but the evidence was worthless -- a denominator
  that structurally cannot contain the subject, reporting clean. Section 0, in
  a verification rather than in code. The tell was available before the claim:
  nothing in the bundle should have been expected to carry selftest output.
  Corrected within the session, but only because the next step happened to
  probe the endpoint directly.

  FOLLOW-UP ITEM, not yet cut: add a selftest stage to capture.sh so the
  battery has capture coverage at all. Until then, treat every selftest check
  as UNCAPTURED and say so rather than inferring from bundle silence. This
  also means 15.9's own regression protection on the box is the unit test
  (test_stale_locks_check_is_gone), not the capture.

UNCHANGED, both still open and neither moved by this capture:

  - v3.66.840's real-world effect: still zero extractor markers
    (jsonapi=/vixen=/dl8=/aylo=/library_extractor=) anywhere in the bundle.
    That is the EXPECTED result either way, because seeding drives the fixture
    site and it never routes to an extractor. THREE consecutive captures have
    now been silent on 840. Silence is accumulating, confidence is not -- do
    not let the count of clean captures read as evidence.
  - item #7 residue: 64 rows, from 62 at v3.66.842 and 58 at v3.66.840. Still
    growing. #7 remains blocked on the operator decision recorded in 15.18,
    whose premise (direct-DB clear) was REFUTED -- batch_ops.bulk_delete over
    POST /api/batch/delete is a working deleter, so the HTTP design needs an
    explicit yes before any code.

### 15.20 | Consolidation 2026-08-03 -- SUPERSEDES 15.18's open set

Filed after two multi-agent passes (38 agents, then 16) and four merged cuts.
This section is the current state. 15.18's table is superseded; 15.19's
capture facts stand.

SHIPPED THIS SESSION, all merged and CI-green:

  - v3.66.843 (#133) 10-C: the import graph could not see
    `from bulk_downloader import X`. TRUE count was 246 edges, not the
    inherited ~240 -- the smaller figure is the module-only variant that
    drops the 6 bulk_downloader/__init__.py edges. 1371 -> 1617.
  - v3.66.844 (#134) 15.9: check_stale_locks deleted. CONFIRMED on the box
    by a live GET /api/selftest (see 15.19).
  - v3.66.845 (#137) 15.8: census coverage counts what it COMPARED.
  - v3.66.846 (#138) 15.11: qB/JD completions record the largest media file.
    Import edge runner_integrations -> library declared in-cut, 1617 -> 1618.
  - Doc cut (#132): CLAUDE.md section 4's axis-6 table corrected from seven
    members to nine, plus a tenth (test_pin_index_in_sync) whose enumerator
    lives one import away and which the table's own grep recipe cannot find.

15.8 SHIPPED ITS SECOND DRAFT, and the first one was defective. A mutation
pass ran 26 mutants against the committed WIP and measured 10 ESCAPING its
4-test battery. Three defects, all fixed before merge, recorded because the
shape recurs: the reason histogram was summed in (row x directory) units and
printed "absent 15" for a 5-row population; `query_failed` was set and read by
nobody, so a failed DB read rendered as a resolution failure the tool never
measured; and the new coverage line made a PRE-EXISTING sweep double-count
READ as reconciled. That last is the most instructive -- the cut did not
introduce the inconsistency, it made it look settled.
  Also: an assertion written to pin `uncompared_rows` was a TAUTOLOGY
  (`uncompared_rows == sum(site_states)` holds for any implementation, because
  the former is DEFINED as the latter). Assert against the population, which
  is an independent quantity.

#7 -- THE OPERATOR'S DESIGN DECISION IS VERIFIED, and the item is READY.
The premise that sent it here was refuted in 15.18 (db_prune is NOT history's
only deleter). The operator then chose the HTTP design on the grounds that
batch_ops.bulk_delete maintains history_fts while a hand-rolled sqlite DELETE
would not. THAT REASONING WAS CHECKED AND HOLDS: batch_ops.py:190-200 calls
_db.db_fts_forget(cx, deleted) once per batch, passing row dicts harvested
BEFORE the delete (batch_ops.py:159 `_matching_rows`, SELECT *), so the FTS
rows carry their pre-delete values. Verified by read + the existing
tests/test_fts_external_content_delete.py + a live end-to-end probe.

  AMENDMENT 1 IS WORSE THAN FILED: tools/live_seed.py main() returns 0 on
  FOUR distinct teardown failures, not one, and TWO of them print nothing at
  all -- history unreadable (prints RESIDUE UNKNOWN, exit 0); /api/status
  unreadable so no marked site is found (exit 0, stderr EMPTY); site DELETE
  returning 500 (exit 0, stderr EMPTY); rows remaining (prints RESIDUE, exit
  0). And capture.sh:858's `live_seed: ` grep is structurally unable to see
  any of it: :841 runs the SEED with `>` (truncating) and the grep sits in
  that `if`'s else-branch, while :747 runs the TEARDOWN with `>>` from
  cleanup_live_seed 125 lines later.

  FOUR MAJORS TO FIX BEFORE CUTTING, all found by adversarial review:
    (a) R2/R3 are specified against a two-read model of the history GET, but
        clear_seeded_history performs a MINIMUM of three per run, and R2's
        asserted number comes from the wrong one.
    (b) R5 does not test its own name -- a mutant deleting the post-clear
        re-read survives it.
    (c) THE ONE WORTH READING TWICE. The spec engineers the new RESIDUE_NOTE
        to retain the words "append-only" and "prune" SOLELY so the existing
        assertion at tests/test_live_seed_starts_and_settles.py:607 stays
        green -- while the new note says the OPPOSITE (the row CAN be
        removed). It also leaves untouched that test's name
        (test_teardown_reports_the_history_row_it_cannot_remove), its
        docstring, and the module docstring at :30-34, all three of which
        assert the premise db.py:988-992 already retracted. That is a gate
        passing for the wrong reason BY CONSTRUCTION, and it would hand the
        retracted claim to the next reader as authority from a test file.
        The correct fix updates the assertion, the test name and all three
        docstrings. Do NOT preserve a keyword to satisfy a gate whose subject
        has changed.
    (d) The band omits every gate the cut's own release chores move --
        test_settings_center_slice4, test_versync_gate,
        test_release_hygiene_gates, test_scan_version_pins_fixture.

  Residue is 64 rows and grows ~2-4 per capture (58 at 840, 62 at 842, 64 at
  844). Before the first real clear, run on the box:
    SELECT id, site_name, filename, library_id, substr(url,1,60)
      FROM history WHERE url LIKE '%bdseed%';

s4#4 -- AUTHORIZED by the operator as TWO cuts, sequenced after 15.8 (done).
NOT yet started. Two FATALs must be fixed during implementation:
  - cut 1's gate is INCOMPLETE: app.py:1812-1824 `_migrations.apply_pending()`
    is a SEVENTH module-scope DB writer, far below the :79-140 region the
    design gates. Applying the specced edits does NOT make its own RED green.
  - the cut-1 RED is structurally blind to the keepalive-gated writers,
    because tests/conftest.py:195 forces BD_DISABLE_KEEPALIVE=1 into
    os.environ before every test body and the RED copies os.environ into its
    child.
  RE-DERIVED, and the inherited figures were both wrong: collection-time app
  importers are 30 (29 direct + 1 transitive via downloader_ui), not 34;
  in-process executors of build()/_routes_from_app() are 9, not 10. Why the
  inherited numbers differed is UNKNOWN -- possibly a different tree state or
  predicate. The design does not depend on the count.
  MEASURED: one app import from a bare cwd deposits 352,256 bytes of DB-class
  residue plus three sentinels, app_config.json, logs/ and live_recordings/.
  The boot block is SIX module-scope DB touchers at app.py:80-140.
  The sentinel must be a sys ATTRIBUTE, not a BD_-prefixed env var: a new BD_
  name enters the config ledger and bands test_gui_parity (CLAUDE.md s4), and
  children spawned by tests must not inherit the skip.

item 12 -- INVESTIGATION ONLY per operator decision; DO NOT CUT YET.
The item is REAL and much larger than filed. There are not three producers of
a library "missing" figure -- there are EIGHT across THREE tables, and the
enumeration is ALREADY PROVEN NON-EXHAUSTIVE: its own verifier found a ninth
(cleanup_helpers.py:133-163 find_missing_metadata, running the identical
population query) that the stated AST predicate structurally could not reach.
Treat EIGHT as a floor, not a count.

  THE FINDING THAT MAKES THE ORIGINAL QUESTION MOOT: the producers DIVERGE,
  and equal numbers are NOT agreement. On one concrete input, two producers
  both return 2 while their row sets are DISJOINT. So "which surface was Matt
  reading" was never answerable AND never necessary -- knowing the surface
  would still not identify WHICH 31 rows.
  Two producers silently SATURATE and never say so, so three different
  numbers describe one fact. audit() carries TWO DIFFERENT caps in ONE
  returned dict: "missing" at 500 and "size_drift" at 1000, because it passes
  neither limit through.

  TWO DEFECTS FOUND HERE THAT NOTHING IN THIS REGISTER HAD EVER FILED, both
  the same root cause cut 25b already fixed for two siblings IN THE SAME
  MODULE and left behind -- a bare basename resolved CWD-relative:
    - library_final.regen_nfos_from_history:474 does Path(fn).exists(). On the
      box the function is a TOTAL NO-OP: it reports every row as missing_files
      and writes ZERO NFO sidecars. Read-only, so it has cost only silence.
    - bitrot.verify_one:155 does Path(row['final_filename']).exists() on a
      bare basename, judges every sampled row "missing", and _record_issue
      (kind='missing') WRITES A FALSE ROW into integrity_issues.
      bg_scheduler.py:252 runs this nightly.
    CONTESTED, and recorded as contested: a verifier called the claim that
    bitrot.verify_one is reachable in production FATAL-wrong as stated,
    arguing _candidates (bitrot.py:98) selects WHERE sha256 != '' and
    run_scan (:211-218) narrows further. Whether the nightly job actually
    reaches verify_one is UNKNOWN and must be settled before any cut. The
    write side effect makes this the highest-urgency unknown in the item.

  FOUR READ-ONLY BOX QUERIES settle the rest, and none needs the panel or the
  audited-directory string the item was blocked on:
    SELECT COUNT(*) FROM library WHERE file_exists=0;                  -- (1)
    SELECT COUNT(*) FROM library WHERE file_path NOT LIKE '/%';        -- (2)
    SELECT COUNT(*) FROM history WHERE status='done' AND filename!=''; -- (3)
    SELECT COUNT(*) FROM integrity_issues WHERE kind='missing';        -- (4)
  Query 1 discriminates the 31: if it returns 31 the number is the
  library-flag mechanism; if not it is the history mechanism and the
  directory string was never needed. Query 4 sizes the false integrity rows.

Audit #3 -- STILL HELD, premise effectively refuted (15.18). A both-model warm
ALREADY ships: ollama_boot_probe.py warm_text/warm_vision plus
ai_boot_readiness.py, wired as systemd unit bulkdownloader-ai-ready
(install_service.sh:271). The open question is why it did not leave the vision
model warm. LIKELY CAUSE, from source: its _attempt raises gpu_unavailable
BEFORE any warm when nvidia-smi fails -- but a CPU-warm model is still warm,
so the warm should not be gated on the GPU probe at all. One journalctl plus
/api/ps after a boot settles it. Do NOT add an in-request warm: measured, it
runs INSIDE L18's own 90s wall, so the 180s load merely moves from generate
into warmup and L18 still fails.

s5 -- RE-MEASURED at this HEAD; operator has NOT yet chosen a scope.
  391 files / 1529 occurrences of "/home/claude"; ZERO in bulk_downloader/.
  Corrections to figures this register has carried:
    - "executable = 772, python-only" undercounts: the predicate was
      .py-suffix, blind to shebang-typed extensionless scripts (toolchain/bin
      holds many).
    - "genuine default-path VALUES ~212" could NOT be reproduced with any
      predicate tried. Closest approximations were 193 argparse defaults and
      127 ALL-CAPS constant assignments, neither matching. Treat 212 as
      UNVERIFIED, not as confirmed or refuted.
    - "roughly half is mirror duplication" does NOT hold: true duplication is
      ~34% of the repo total (66% of project-knowledge's own count).
    - the pk mirror drift gate is blind to FIVE currently-identical pairs,
      not four.
  test_generated_artifact_workflow.py:195 POSITIVELY pins /home/claude in
  scripts/build_release.sh, so a blanket sweep turns it red.

PROCESS, three items earned this session and worth keeping:

  - A CHECKPOINT COMMIT IS FINE; A CHECKPOINT THAT READS AS VERIFIED IS NOT.
    Both 15.8 and 15.11 were committed mid-band to survive an ephemeral
    container, with commit messages stating plainly that the band had not run,
    then squashed away. The 15.8 checkpoint turned out to contain three real
    defects -- had it been written as a finished cut, the PR body would have
    claimed a verification that had not happened.
  - A SPEC'S BAND IS AN INPUT, NOT AN ANSWER. 15.11's spec stated two band
    derivations; re-running its own greps returned 12 files where it said 6
    and 10 where it said 9. The load-bearing miss was different in kind:
    _bd_runner_src() CONCATENATES runner.py with every runner_*.py, so 24 test
    files read a runner_integrations.py edit without ever naming the module.
    Measured over the real 746958-char concatenation. Final band was 44 files
    / 563 passed against the spec's 30.
  - SUBAGENT OUTPUT IS DATA, INCLUDING A SUBAGENT'S OWN CORRECTIONS. The 15.8
    reviewer's PATCH 1 had a major defect of its own (it made the
    operator-facing uncompared count WORSE), which only a second adversarial
    pass caught. The reviewer also reported that its OWN first fix had the
    same bug one level down and said so. Read what an agent returned; a
    result that is present is not a result that is correct.

### 15.21 | item 12 is CLOSED on box evidence, and it was never a library defect

Filed 2026-08-03 from five read-only probes the operator ran on test4 against
downloader_history.db (sqlite3 mode=ro, no writes). This closes item 12,
settles two standing UNKNOWNs, and REFUTES two claims this register has been
carrying as fact.

THE MEASUREMENTS, in the order they were taken:

    library file_exists=0                    27
    library file_path NOT LIKE '/%'          27
    intersection                             27      (missing-only 0, ghost-only 0)
    library total                            43
    history status='done' AND filename!=''   51
    integrity_issues total                    0
    history url LIKE '%bdseed%'              66
    ghosts WITH an absolute twin by basename  0
    ghosts from bdseed                       27      (NOT from bdseed: 0)

ITEM 12 IS CLOSED. The rows the Library panel reports as "missing" are not
missing files. They are rows whose file_path was never recorded as absolute,
so nothing can resolve them on disk and file_exists=0 follows mechanically --
and ALL 27 of them were written by the capture's own bdseed seeding
(joined library.history_id -> history.url, every one matching '%bdseed%';
non-bdseed ghosts = ZERO, enumerated by name, not merely counted). There is
no library loss on this box. The panel has been displaying test residue.

  The "31" NEVER REPRODUCED. It is 27. The register's own suspicion that 31
  was a conflation was correct, but the number it doubted (27) is the true
  one -- so the entry was right for the wrong reason.

  EQUAL COUNTS WERE CHECKED, NOT ASSUMED. file_exists=0 and non-absolute-path
  are both 27, which is exactly the shape the item-12 investigation warned
  about: it had found two producers returning 2 over DISJOINT sets. The
  intersection was measured (27, with both one-sided differences ZERO) before
  concluding they are one population.

TWO CLAIMS IN THIS REGISTER ARE REFUTED BY THESE NUMBERS:

  - "prefer resolve-and-merge over delete -- each ghost has a scanned twin"
    (15.11's ghost-row paragraph). ZERO of the 27 have an absolute twin by
    basename. The repair strategy that guidance implies has no basis, and it
    would have shaped the fix. Deleted from the plan, not softened.
  - "bitrot.verify_one WRITES false integrity_issues rows, run nightly by
    bg_scheduler.py:252" (15.20, filed as the item's highest-urgency unknown
    because of the write side effect). The integrity_issues table EXISTS and
    holds ZERO rows. Nothing has been written, ever. The verifier that called
    this claim FATAL-wrong as stated was right and the agent that filed it
    was wrong -- recorded because 15.20 explicitly left it contested and this
    is the settlement.
    Its sibling defect is UNAFFECTED and still real:
    library_final.regen_nfos_from_history:474 resolves a bare basename
    CWD-relative and is a total no-op on the box. Read-only, so it has cost
    only silence.

15.11's GHOST-ROW UNKNOWN IS SETTLED at 27, all seeded. That figure had been
carried unverified since the item was filed.

CONSEQUENCE FOR #7, and it is a real change of shape rather than a detail.
Item 12 needs no fix of its own; it collapses into #7. But #7's spec
recommends RECORDING the library twins rather than deleting them, and that
recommendation was made without knowing what the twins were. They are 27
rows, 63% of the entire library table, and 100% of the Library panel's
"missing" count. Deleting them clears a false operator-facing signal;
recording them preserves it. OPERATOR DECISION, not an implementation
detail -- do not let the spec's default stand unexamined.

WHAT THE 8-PRODUCER DIVERGENCE FINDING STILL MEANS. 15.20 records that item
12's investigation found eight producers of a "missing" figure across three
tables, that the enumeration was already proven non-exhaustive, and that two
producers returned equal counts over disjoint sets. NONE of that is refuted
here -- the divergence is a real source-level defect and remains OPEN. What
is closed is the motivating case: the specific number on the operator's panel
had a single, mundane cause, and knowing which of the eight surfaces he was
reading would never have revealed it. The divergence should be re-filed on
its own merits, at its own priority, rather than inherited as urgent because
of a symptom that turned out to be seeded data.

METHOD NOTE, because it nearly went the other way. The first probe handed to
the operator ended with an unconditional `echo "(blank above = bitrot has
never run)"` -- a line that printed its conclusion whether or not the command
succeeded, and it DID print on a box with no sqlite3 CLI installed. A verdict
whose evidence cannot fail to appear is not a verdict. The replacement
derived every conclusion from an actual result and distinguished
table-absent from table-empty, which is what made the bitrot settlement
trustworthy rather than a second guess.

### 15.22 | HANDOFF, 2026-08-03 close at e3cb953 (v3.66.846)

Written as the durable record of a long session. If you are starting fresh,
read CLAUDE.md in full, then 15.20, 15.21 and this section. 15.18's open set
is superseded; 15.19's capture facts and 15.21's box measurements stand.

STATE, measured not inferred:

  - main e3cb953, v3.66.846, working tree clean, no open PRs.
  - The box captured PASS at v3.66.846 (run 2026-08-03T14:29:28): 14509 total
    / 14424 passed / 0 failed / 0 errors / 85 skipped; live 36/0/0; graph pin
    OK against a re-pinned 2e0f1b1a; /api/health sha d0b8cc91a3a4 -- the
    merged tip, so the running PROCESS was verified.
  - Count delta +23 from v3.66.844 reconciled on COLLECTED TESTCASE IDS: +12
    census (15.8), +11 qB/JD (15.11), zero gone.

SHIPPED THIS SESSION: v3.66.843 (10-C), 844 (15.9), 845 (15.8), 846 (15.11),
plus two doc cuts (CLAUDE.md axis-6 table; registers 15.17/15.19/15.20/15.21).
Eight PRs merged, #132 through #139.

WHAT A GREEN CAPTURE IS STRUCTURALLY SILENT ABOUT. Three things now, and the
list grows -- treat it as a class, not a list:
  - the SELFTEST battery: capture.sh never calls /api/selftest (15.19). Any
    selftest change is uncaptured; confirm it with a live GET.
  - the CENSUS tool: capture.sh never runs census_file_size_drift.py, so
    15.8's whole visible effect is invisible to a capture. Run it directly on
    the box; that also answers the last of the legacy-residue questions.
  - any qB/JD path: seeding drives the FIXTURE site, which never routes
    through those bridges, so 15.11 is unexercised. Zero qB/JD library rows is
    the expected result whether or not it works.
  - and still: v3.66.840's extractor markers, silent for four consecutive
    captures for the same reason.

THE OPEN SET, with the exact next action for each:

  #7  seeded history residue -- READY TO CUT, design verified.
      Residue 66 rows and growing ~2/capture. The HTTP design via POST
      /api/batch/delete is CONFIRMED correct: batch_ops.py:190-200 maintains
      history_fts, checked by read + the existing FTS test + a live probe.
      Four amendments are mandatory and are recorded in 15.20; the one that
      matters most is (c), the spec's attempt to keep a stale assertion green
      by retaining keywords while asserting their opposite.
      DECISION OUTSTANDING, and 15.21 changed it: the spec defaults to
      RECORDING the 27 library twins rather than deleting them. Those twins
      are 100% of the Library panel's "missing" count and 63% of the library
      table, and all 27 are seeded. Deleting clears a false operator-facing
      signal. Do not let the default stand unexamined.

  s4#4  repo-root .db-wal writer -- AUTHORIZED as two cuts, not started.
      Two FATALs must be fixed during implementation: app.py:1812-1824
      _migrations.apply_pending() is a SEVENTH module-scope DB writer below
      the gated region, so the specced edits do NOT make their own RED green;
      and the RED is structurally blind to keepalive-gated writers because
      tests/conftest.py:195 forces BD_DISABLE_KEEPALIVE=1 and the RED copies
      os.environ into its child. Re-derived counts: 30 collection-time app
      importers (not 34), 9 in-process executors (not 10). The sentinel must
      be a sys ATTRIBUTE, not a BD_-prefixed env var -- a new BD_ name enters
      the config ledger and bands test_gui_parity.

  item 12  CLOSED as filed (15.21). What REMAINS open and should be re-filed
      on its own merits at its own priority: the eight-producer divergence,
      the proven non-exhaustiveness of that enumeration, and audit()'s two
      different caps (missing 500, size_drift 1000) in one returned dict.
      Also still real: library_final.regen_nfos_from_history:474 resolves a
      bare basename CWD-relative and is a total no-op on the box.

  Audit #3  HELD. A both-model warm already ships (ollama_boot_probe.py +
      ai_boot_readiness.py, systemd unit bulkdownloader-ai-ready,
      install_service.sh:271). One journalctl plus /api/ps after a boot
      settles why it did not leave the vision model warm. LIKELY FIX, from
      source: its _attempt raises gpu_unavailable BEFORE any warm when
      nvidia-smi fails, but a CPU-warm model is still warm -- the warm should
      not be gated on the GPU probe. Do NOT add an in-request warm: measured,
      it runs inside L18's own 90s wall and does not fix the capture.

  s5  /home/claude residue -- 391 files / 1529 occurrences, zero in
      bulk_downloader/. Operator scope decision outstanding. Three inherited
      figures corrected in 15.20; "~212 genuine default-path values" is
      UNVERIFIED because no predicate tried could reproduce it.
      test_generated_artifact_workflow.py:195 POSITIVELY pins /home/claude in
      scripts/build_release.sh, so a blanket sweep turns it red.

  Small, unclaimed: add `git rev-parse HEAD` to 01_sysinfo.log so capture
  bundles self-identify (two uploads arrived together this session and were
  separable only by 02_SUMMARY.txt and 09_http_smoke.log). Rides any
  capture.sh cut. And add a selftest stage to capture.sh (see the silence
  class above).

OPERATOR-SIDE, unchanged and still outstanding: `git worktree prune -v` (18
trees, 6 prunable); `git checkout -B main origin/main` in the deploy checkout;
and the archive sequence (decide B4's 533 raw wacz AND its 91 mid-transaction
.db copies -> consolidate with a verified manifest -> THEN rotate the orphan
bundle).

WHAT IS NOT PERSISTED, stated so nobody hunts for it. Two multi-agent runs
(38 agents, then 16) produced implementation-ready specs with exact code for
#7 and s4#4. Those outputs lived in the container's /tmp and are GONE with it.
Everything DECISION-CRITICAL from them is in 15.20, 15.21 and this section --
the premises, the amendments, the measured figures, the file:line anchors.
The exact code is not, and re-deriving it means re-running the investigation.
That is the honest cost, and it is the right trade: a 40K-character generated
spec committed to project-knowledge would become a second document an agent
reads before acting, which CLAUDE.md section 8 names as the defect it is.

PROCESS EARNED THIS SESSION, beyond 15.20's three:

  - A VERDICT WHOSE EVIDENCE CANNOT FAIL TO APPEAR IS NOT A VERDICT. A probe
    handed to the operator ended with an unconditional
    `echo "(blank above = bitrot has never run)"`, and it printed on a box
    with no sqlite3 CLI installed. The conclusion was decoupled from the
    measurement. The replacement derived every line from an actual result and
    distinguished table-absent from table-empty -- which is the only reason
    the bitrot settlement in 15.21 is trustworthy.
  - EQUAL COUNTS ARE NOT AGREEMENT, and this session had both the warning and
    the instance. item 12's own investigation found two producers returning 2
    over DISJOINT sets; days later two figures both read 27 and the temptation
    to call them one population was strong. The intersection was measured
    (27, both one-sided differences zero) BEFORE the claim was made.
  - A SPEC'S BAND IS AN INPUT, NOT AN ANSWER -- 15.11's stated derivations
    returned 12 where the spec said 6 and 10 where it said 9, and the
    load-bearing miss was structural: _bd_runner_src() concatenates every
    runner_*.py, so 24 test files read a runner_integrations.py edit without
    naming the module. Final band 44 files against the spec's 30.

### 15.23 | Four operator decisions, 2026-08-03 -- recorded before the work

These were taken interactively after 15.21 changed what one of them was about.
Recorded as their own section because a decision that exists only in a
conversation is a decision that did not happen.

  1. #7's LIBRARY TWINS: DELETE THEM. Teardown clears the history rows AND
     their library twins. This REVERSES the spec's default of recording-only,
     and the reversal is 15.21's doing: those twins are 100% of the Library
     panel's "missing" count, 63% of the library table, and every one is
     bdseed residue -- non-bdseed ghosts were enumerated BY NAME at zero. The
     panel goes clean and stays clean. The 27 existing rows go with the first
     real run.
       IMPLEMENTATION FACT, read from source rather than assumed: the delete
       path exists and is HTTP-reachable. library.library_delete(library_id,
       also_delete_file=False) at library.py:412, exposed as
       DELETE /api/library/<int:lid> (app_library.py:138-143, JSON body
       {"delete_file": bool}). ORDER MATTERS -- library rows carry history_id,
       so delete the twins BEFORE the history rows or they are left dangling.
       History deletion stays POST /api/batch/delete, whose FTS maintenance is
       the reason the HTTP design was chosen (15.20).

  2. NEXT CUT IS #7. Design verified, four amendments mandatory (15.20).

  3. s5 SCOPE CHOSEN: toolchain/bin (131 files including the bare `bd`) PLUS
     toolchain/bdenv.sh and toolchain/install_bdsuite.sh -- the entrypoint's
     own dependencies -- PLUS the gated mirrors, PLUS manual sync of the pairs
     the mirror drift gate cannot see. Not a blanket sweep:
     test_generated_artifact_workflow.py:195 POSITIVELY pins /home/claude in
     scripts/build_release.sh and would turn red.

  4. ITEM 12's DIVERGENCE FINDING: re-file fresh at NORMAL priority. It is a
     real source-level defect and is not refuted -- eight producers across
     three tables, an enumeration already proven non-exhaustive, two producers
     returning equal counts over disjoint sets, and audit() carrying two
     different caps (missing 500, size_drift 1000) in one returned dict. What
     is gone is the urgency: the symptom that made it look like a crisis was
     seeded data. Schedule it on its merits, do not inherit it as a fire.

### 15.24 | The unshipped specs are now in the repo -- and why that is a compromise

AMENDED 2026-08-03 at v3.66.847 -- THERE ARE NOW THREE, NOT FOUR. This
section listed four files and named 07-seeded-history-clear.md among them.
#7 shipped as v3.66.847, so condition 4 below fired and that file was DELETED
in the same branch that landed the cut (`git diff --name-status` shows
`D project-knowledge/pending-specs/07-seeded-history-clear.md`). The
condition was honoured, not waived. The list as it stands:

    s4-4-repo-root-db-residue.md      s4#4,    READY_WITH_CHANGES
    item12-missing-producers.md       item 12, READY_WITH_CHANGES
    s5-home-claude-residue.md         s5,      READY

Measured at the branch tip rather than quoted: `ls
project-knowledge/pending-specs/` returns exactly those three names. Note
what the original text became the moment #7 merged -- a register entry
naming a file that is not in the tree, i.e. the wrong denominator handed to
the next reader, which is the class CLAUDE.md section 1 is about. The
amendment is inline rather than appended as a new section so nobody reads
the stale list first.

The specs for cuts that ALREADY SHIPPED (15.8, 15.11, 15.9, 10-C, and now #7)
were
deliberately NOT saved. A spec whose cut is merged has no forward value and
would be pure staleness.

THIS IS A COMPROMISE AND SHOULD BE READ AS ONE. CLAUDE.md section 8 says a
second agent-facing document is the defect, not a resource, and it says so
because CODEX_HANDOFF once shipped 14 commands against a venv that does not
exist here while CLAUDE.md said otherwise, and a session followed the wrong
one. These files are the same SHAPE as that failure. Four things keep them on
the right side of the line, and all four must hold:

  1. Each carries a header stating it is NOT a contract, that CLAUDE.md wins
     any disagreement, and that SESSION_CARRY is the authoritative register.
  2. Each states it was adversarially reviewed and did NOT pass clean, with
     its surviving objections recorded in 15.20 rather than in the file.
  3. Every DECISION-CRITICAL fact in them is ALREADY in 15.20/15.21/15.23.
     The files add the exact code and nothing else. If a future reader treats
     one as authority and it contradicts the register, THE REGISTER IS RIGHT.
  4. THEY ARE DELETED WHEN THEIR ITEM SHIPS. This is not optional hygiene; it
     is the condition that keeps them from becoming what they resemble. A cut
     that lands #7 and leaves 07-seeded-history-clear.md in the tree has done
     half its job.

If a later session finds these files with no matching open item in the
register, that is the defect -- delete them rather than reasoning from them.

METHOD NOTE, and it is the second instance in one session. The scan that
cleared these files for credential-shaped literals was first written with an
unconditional `echo "(blank = no credential-shaped literals)"` after a grep
that ERRORED with "No such file or directory" -- the reassurance printed over
a scan that examined zero bytes. 15.22 records the identical mistake from
earlier the same day. The replacement asserts a non-empty denominator before
scanning and reports the file and line counts it actually examined. Twice in
one session, in a session about exactly this, is the argument for making the
denominator assertion a reflex rather than a remedy.

### 15.25 | Branch close 2026-08-03 at 5c46360 -- three features, one squash

Written from measurements taken on this branch tip. Every number below is one
this session ran, or one it is explicitly attributing to a lane report; where
nothing was measured the entry says UNKNOWN. Nothing here has been on the box.

The branch is claude/bulkdownloader-handoff-j9o59v, tip 5c46360, version
3.66.848 (read from the interpreter, not from CHANGELOG). It carries THREE
features that squash into ONE commit by operator decision -- a deliberate
departure from CLAUDE.md section 2's one-feature-per-cut rule, taken with the
blast radius understood rather than by accident.

WHAT SHIPPED

  1. v3.66.847 -- the seeded-history clear.
     `tools/live_seed.py --teardown --clear-history` deletes the bdseed
     history residue over POST /api/batch/delete, and deletes the library
     twins over DELETE /api/library/<lid> FIRST. The order is not a
     preference: library rows carry history_id with no enforced foreign key,
     so deleting history first leaves the twins dangling (15.23 decision 1).
     Anchors, so nobody re-derives them:
       tools/live_seed.py:1206   clear_seeded_history()
       tools/live_seed.py:1089   _twin_scan()
       tools/live_seed.py:1325-1326  the ownership predicate (site_name)
       tools/live_seed.py:1055   the find (GET /api/history?q=bdseed)
       tools/live_seed.py:1498   final twin verification, guarded on
                                 `not dry_run and targeted_ids`
       tools/live_seed.py:1937-1938  _EXIT_CLEAR_INCOMPLETE=4,
                                 _EXIT_CLEAR_UNKNOWN=5
       tools/live_seed.py:1941   _teardown_exit_code()
       tools/live_seed.py:1808   _report_residue()
       bulk_downloader/batch_ops.py:200  db_fts_forget, the reason the HTTP
                                 design was chosen (15.20)
       bulk_downloader/library.py:412    library_delete()
       bulk_downloader/app_library.py:138-143  DELETE /api/library/<int:lid>
       capture.sh:743-777        cleanup_live_seed(), the wiring
       capture.sh:754            BD_SEED_CLEAR_HISTORY, default 0
       reports/config_gui_manifest.json:84  the ledger entry, display-only
     Off by default because capture.sh runs teardown UNATTENDED and the clear
     predicate is the marker across ALL history, not this run's nonce.

  2. lxml AND cssselect declared in requirements.txt -- census CORRECTED.
     THE ORIGINAL ENTRY HERE WAS WRONG, and it was wrong in the specific way
     CLAUDE.md section 1 warns about. It was derived BY GREP, and like the
     playwright census in that section it failed in BOTH directions at once.
     Re-derived with ast.parse over every tracked file the gate reads as
     Python -- 2577 after the third repair (item f below) widened the
     denominator from the 2108 that `git ls-files -- '*.py'` returns; all
     parsed, zero SyntaxError -- reading Import
     and ImportFrom nodes and skipping relative imports. COUNTS BELOW ARE OVER
     THAT 2577-FILE DENOMINATOR -- v3.66.849 correction: the first version of
     this entry stated the 2108-file instrument and then counted a smaller
     set under it ("All three" lxml, "Two" cssselect in requirements.txt and
     the CHANGELOG), parenthesising the tests/ importer out of the total while
     keeping a root-level one in. Four and three:
       lxml       bulk_downloader/accessibility.py:185   (ARIA audit)
                  bulk_downloader/selector_playground.py:56 (XPath eval)
                  bulk_downloader/synthetic_tests.py:96  (synthetic selector
                                                          check)
                  tests/test_v3_66_320_synthetic_json_path.py:110  (a test)
       cssselect  bulk_downloader/selector_playground.py:67
                  audit_templates.py:26                  (REPO ROOT, not the
                                                          app package)
                  tests/test_v3_66_320_synthetic_json_path.py:111  (a test)
     Three lxml sites and two cssselect sites are non-test; those fail open.
     Note what the old shape hid: over a bulk_downloader/-only denominator
     cssselect has ONE importer, not two -- audit_templates.py is root-level.
     Neither "two" nor "three" was true of a denominator anyone had stated.
     FALSE NEGATIVE: synthetic_tests.py:96 -- a real fail-open lxml importer
     that appeared in NO prose on this branch. It returns
     {"ok": false, "error": "lxml not installed"} and the caller carries on.
     FALSE POSITIVE: bulk_downloader/diagnostics_bundle.py:130 was cited here
     and in requirements.txt as an importer. IT IS NOT ONE. "lxml" there is a
     string inside a tuple of optional-dependency NAMES that
     _capture_versions() feeds to __import__ for a version report -- there is
     no import node on that line. Grep sees it; AST does not. The citation has
     been DELETED from requirements.txt rather than reworded: the observation
     it carried (reporting availability and declaring an install floor are
     different jobs) is true, but attaching it to a line that is not an import
     teaches the next reader a false fact out of a file that looks
     authoritative. That site is correct as written and was left alone.
     WHY cssselect IS DECLARED (`cssselect>=1.2,<2.0`), not waved off as
     optional. Both importers fail open and both lose a capability silently:
       - selector_playground.py:67 sets _HAS_CSSSELECT, which is_available()
         reports verbatim; without it the playground falls back to
         _css_to_xpath_simple(), explicitly only "the common cases the
         operator actually types".
       - audit_templates.py:26 sets _have_cssselect; check_selector() then
         SKIPS the cssselect.parse() probe. v3.66.849 correction: "every
         syntactically INVALID selector reads as valid" was FALSE. Read the
         function (audit_templates.py:32-58) -- four structural checks run
         before the probe and still run without cssselect: non-string type,
         empty/whitespace, [] and () balance, and quote balance inside each
         attribute selector. Only the grammar probe is lost, so what stops
         being rejected is the bracket- and quote-balanced but grammatically
         invalid subset. MEASURED over 13 probe selectors: 7 flip to valid
         ("div >> p", "a[href=]", "::", "1abc", "div:", ">div", "*|"), 4 stay
         caught structurally, 2 ("a:nth-child(2n+)", "a::pseudo-nope")
         cssselect accepts anyway so they were never caught either way.
         Section 0 shape in shipped code, over a proper subset -- which is
         still worth declaring cssselect for, and is what the evidence says.
     And it is why declaring lxml alone was not enough: lxml's
     element.cssselect() is implemented BY cssselect and raises ImportError
     without it -- MEASURED here before installing, "cssselect does not seem to
     be installed" -- and that is the PRIMARY path at synthetic_tests.py:96.
     lxml exposes it as its `cssselect` extra, so the lxml pin does not pull it
     in. Floor 1.2 (2022-10-27, first py3-only release); 1.5.0 (2026-07-27)
     installed into venv and the import proven, together with
     lxml.html.fromstring(...).cssselect() and
     selector_playground.is_available() -> cssselect True.
     CONSTRAINED BY A TEST, which it previously was not. Nothing in the tree
     read the real requirements.txt for these names: deleting the lxml line
     left the whole band green, so the declaration shipped unconstrained.
     tests/test_v3_66_653_dep_freshness.py now carries EIGHT cases (it shipped
     with three; v3.66.849 added five after two read-only verifiers took the
     first version apart). The AST walk is one pass over all 2577 tracked
     Python files,
     sliced into two scopes that PARTITION the tree, each with its own
     manifest expectation:
       app     (bulk_downloader/, 565 files, 3632 import nodes)
               30 third-party names: 20 declared, 10 waived.
       outside (everything else, 2012 files, 15734 nodes)
               29 third-party names: 14 declared, 15 waived (werkzeug, PIL,
               psycopg, atheris, hypothesis, markdown, paho, bd_dev_inspect,
               plus the seven found by item f: aiosmtpd, freezegun, jedi,
               mitmproxy, prometheus_client, pytesseract, pyzbar).
     Four things about that gate worth carrying:
       - The denominator is asserted BEFORE the verdict. Five mutations that
         empty or blind the scan (outside subject filter matching no file,
         resolver resolving nothing, requirements glob reading no manifest,
         packages_distributions returning nothing, tests/ prefix matching no
         path) all FAIL on a named denominator assertion instead of reporting
         "all declared" over nothing. Mutants validated with ast.parse first
         and the file restored byte-identically (sha256 checked).
       - IT WAS ITSELF A SECTION 0 DEFECT, and that is the point worth
         carrying, not the fix. The first version scanned bulk_downloader/
         ONLY and said so nowhere a failure could show it, so tests/, tools/,
         toolchain/, bin/, scripts/, live_tests/, docs/ and
         project-knowledge/ were structurally outside its subject. It reported
         clean over `requests`, hard-imported by two tracked test files and
         declared in no manifest. That is the THIRD gate on this branch
         written to catch a section 0 defect that shipped with one:
         tools/check_requirements.py exited 0 on a file parsing to zero names,
         scripts/deploy.sh step [10] warned about BD_HOME only when BD_HOME
         was exported while capture.sh DEFAULTS it, and now this. Assume the
         next one has the same shape and go looking.
       - The scope is not merely wider, it is SAID. Both halves print their
         own scope and the shared blind spot (the predicate is ast.Import /
         ast.ImportFrom, so __import__, importlib.import_module and
         pytest.importorskip are invisible) in the failure message.
       - IT IS NOW AXIS-6, and the earlier entry here claiming otherwise is
         superseded. Imports resolve against the importing file's OWN
         directory -- that is what keeps tests/conftest.py, tests/_env.py and
         tests/capture_lanes.py from reading as PyPI distributions -- so
         adding or renaming a tests/ file moves what it measures. Band this
         file on any cut that adds or renames a tracked PYTHON file -- which
         after item f means a .py OR an extensionless python-shebang script.
         CLAUDE.md
         section 4's axis-6 table does not list it; it is an operator file and
         was not edited for this.
     RED proven four ways before the pins were trusted: remove the lxml line
     (2 tests fail, and the failure names all three importers including
     synthetic_tests.py); remove the cssselect line (2 fail); MIGRATE a pin to
     requirements-optional.txt (only the core-manifest test fails, which is
     correct -- scripts/deploy.sh step [5] resolves requirements.txt alone, per
     tools/check_requirements.py:56); and the three empty-scan mutations above.
     NOT in the CHANGELOG until now. The original lxml work wrote its census
     into requirements.txt and into commit ac93b44's message but never into
     CHANGELOG.md, so there was nothing there to correct -- the v3.66.848 entry
     has been EXTENDED rather than fixed. Worth noting because the handoff that
     scoped this correction listed CHANGELOG as one of three sites carrying the
     wrong census; it was two.
     v3.66.849 FOLLOW-ONS, all five re-measured before acting rather than
     inherited from the verifier reports that raised them:
       a) `requests` WAS a real undeclared import, and the fix is
          pytest.importorskip in the two importers, NOT a requirements-dev.txt
          declaration. tests/test_v3_66_550_weather_ssrf.py and
          tests/test_webhooks_subscription_ssrf.py hard-imported it inside
          helper functions. MEASURED with requests blocked on sys.path:
          7 failed / 2 passed; control (same directory, same command, blocker
          removed) 9 passed. Reproduced here before the fix; after it, the same
          blocked run is 2 skipped with the reason named, and the control is
          still 9 passed. WHY NOT DECLARE IT: both files exist to patch
          requests.head / requests.get / requests.post on the module that
          site_weather.probe_http and webhooks._deliver_one soft-import, and
          those functions return {"ok": False, "error": "requests not
          installed"} without it -- so on a core-only install the guard under
          test cannot execute and the honest verdict is SKIP, which says so.
          Declaring it in requirements-dev.txt would also have forced it off
          _UNDECLARED_BY_DESIGN (the reverse-direction test forbids a declared
          name staying waived), converting "BD does not require requests" into
          "BD requires requests, in the dev manifest" -- a claim nobody made.
       b) THE WAIVER REASON FOR requests WAS STRONGER THAN ITS EVIDENCE. It
          said "transitive under several declared distributions". MEASURED
          from installed metadata: EXACTLY ONE, and only through the
          posture-sensitive optional manifest --
          requirements-cloak.txt's cloakbrowser[geoip] -> geoip2>=4.0 ->
          requests>=2.24.0,<3.0.0. The other installed dists that name
          requests (psutil, pytest, markdown-it-py) name it only under a
          'dev' / 'testing' extra BD never asks for, so nothing in
          requirements.txt pulls it in, and install_linux.sh's cloak step is
          NON-FATAL by design. A waiver whose stated reason is stronger than
          its evidence is how a real gap survives review.
       c) THE FIRST-PARTY HEURISTIC COULD SILENTLY REMOVE A REAL NAME. The old
          _first_party_names took a global BAG of stems from the repo root and
          tools/, so one tracked tools/<distname>.py would have made that
          distribution first-party for EVERY importer in the tree. Resolution
          is now per-importer (repo root, tools/, and the importing file's own
          directory) and every removal is RECORDED, then checked against two
          independent signals that a suppressed name is really a distribution:
          declared in a manifest, or a top-level module of something installed
          from outside this checkout. MEASURED at this tip: 95 names
          suppressed, 70 installed top-levels seen, ZERO shadow hits -- the
          hazard is latent, not live. Proven to FIRE by adding a real tracked
          tools/lxml.py: the shadow report named the file and all four lxml
          importers that had left the subject, and the core-manifest test
          caught it independently. File removed; git status clean.
       d) `venv/` LIVES INSIDE THE REPO, so "installed under _REPO" is not
          "installed from this repo" -- the first version of the shadow check
          used that test and every site-packages distribution was filtered
          out, leaving the map empty. Its own denominator assertion caught it
          and said UNKNOWN rather than reporting clean, which is the only
          reason it was noticed within the session. The test is now a
          site-packages / dist-packages path component.
       e) THE PER-SCOPE COUNTS AND THE PER-SCOPE SLICE NOW COME FROM ONE
          PREDICATE. They were two (the census keyed scope off _in_app, the
          slice off _SCOPES), so a broken subject filter could leave a healthy
          file count describing a set the verdict never ran over. The census
          now asks every scope predicate and requires exactly one to match, so
          "the scopes stopped partitioning the tree" is its own named failure.
       f) THE SECOND REPAIR DID NOT FINISH THE JOB, and a reviewer found the
          SAME section 0 shape a third time. Three items, all RED-proven on
          the pristine tree before anything was changed:
          DENOMINATOR. The scan was `git ls-files -- '*.py'` while
          _SCOPE_NOTE["outside"] claimed toolchain/ and project-knowledge/
          outright. MEASURED: 3423 tracked files, 2108 ending .py, and 469
          MORE that carry a python shebang and NO extension -- 234 under
          toolchain/, 235 under project-knowledge/, none anywhere else. So the
          glob reached 6 of toolchain/'s 240 Python files (2.5%) and 40 of
          project-knowledge/'s 275 (14.5%) while the note claimed both
          directories. CLAUDE.md section 8 names the toolchain/bin bd-* suite
          as its own POPULATION; a .py glob structurally cannot see it. RED:
          `import zeep` appended to tracked toolchain/bin/bd-guardcheck left
          the gate GREEN (15 passed). Option (i) was taken over narrowing the
          claim -- _tracked_py now reads the first line of every tracked
          non-.py file and a python shebang makes it Python (2577 scanned) --
          because the claim was the true one and the denominator was the
          defect. The same probe is now RED and names the file.
          SEVEN REAL FINDINGS, which is why widening was the right call:
          aiosmtpd, freezegun, jedi, mitmproxy, prometheus_client, pytesseract
          and pyzbar were all undeclared and all invisible. Waived, not
          declared, each with a reason read at its import site. They live in
          bd-opv / bd-lsp / bd-proxy, which exist as TWO identical tracked
          copies (toolchain/bin/<x> and project-knowledge/<x>; md5 verified
          equal), so each name reports two import sites for one script -- do
          not read that as two importers. Six are try/except -> SKIP; jedi is
          installed by bd-lsp itself from /home/claude/lsp_kit/wheels and every
          subcommand returns 1 out of cmd_setup while it is missing. Three need
          a system package (tesseract, libzbar, a proxy) as well as a wheel.
          OVER-SENSITIVITY, twice, which CLAUDE.md section 0 counts equal to a
          false clean. (1) _declared_names never followed requirements-dev.txt's
          first directive `-r requirements.txt` -- parse_requirement_line
          returns None for it -- so 15 core pins read as ABSENT from the dev
          manifest (measured 15 -> 0 after the fix). A tests-only import of any
          of them would have been told to duplicate a pin the dev install
          already delivers. Includes are followed now, with a cycle guard and
          an assertion rather than a skip when the target resolves outside the
          repo or does not exist. (2) _DIST_ALIASES was a hand-written
          three-entry map, so `import xdist` failed as "declared in no
          requirements*.txt" while pytest-xdist is pinned in requirements.txt.
          RED: that import injected into a tracked tests/ file gave 1 failed /
          14 passed; after both fixes, 15 passed. One injection, both fixes --
          without the include-follow the name lands in the dev-manifest
          assertion instead of the undeclared one.
          THE ALIAS MAP IS NOW DERIVED from
          importlib.metadata.packages_distributions() with the hand list
          applied LAST as an override. COVERS every top-level name an installed
          distribution provides (70 top-levels here; 11 aliases result).
          CANNOT cover: a distribution not installed in the running
          environment contributes nothing -- pillow and yt-dlp are both absent
          from this container's metadata, which is precisely why those two
          entries stay hand-written; a name provided by MORE than one
          distribution is left unmapped rather than guessed (exactly one here,
          jaraco, from three jaraco.* dists); and the derived half is a
          property of the ENVIRONMENT, not the tree, so it may only ADD and can
          differ between this container and the box. Never delete a hand entry
          because a derivation happened to cover it here.
          THREE MINORS, fixed by telling the truth rather than by adding
          machinery, and said here because each is a limit somebody will
          otherwise re-discover. test_tests_only_imports_are_declared_in_the_
          dev_manifest's verdict runs over ONE name (pytest), declared in both
          manifests, so it cannot fail today -- it is a live check for the next
          test-only import, not evidence about this tree.
          _installed_from_the_repo_itself is a CONJUNCTION (no site-packages
          component AND under _REPO), so the test order the reviewer flagged is
          immaterial -- the truth table is identical either way -- but the
          conjunction does not recognise a PEP 660 editable install, whose
          locate_file("") returns the site-packages directory. Left unrepaired
          ON PURPOSE: no BD distribution is installed here at all (no
          bulk_downloader key, no __editable__* file, none of 67 dists), so the
          branch cannot be exercised and a fix would ship untested; if it ever
          happens the shadow test reports bulk_downloader shadowing itself,
          which is loud. And an `if TYPE_CHECKING:` import fails the gate while
          none of the three offered remedies fits it, so both failure messages
          now name that case and point at a string annotation. TYPE_CHECKING
          blocks were NOT excluded from the walk: that would remove names from
          the subject to repair a case the tree does not have (measured: zero).
       Also corrected: one more denominator slip found while re-measuring (a)
       -- "imported only from tests/" was first judged against the OUTSIDE
       slice, which made bs4, httpx, lxml, mutagen, openpyxl, curl_cffi and
       cloakbrowser look test-only (they have no non-tests importer outside
       bulk_downloader/) and demanded seven runtime pins move to the dev
       manifest. Judged over the whole tree it is one name, pytest, and it is
       already in requirements-dev.txt.

  3. v3.66.848 -- scripts/deploy.sh as the git deploy path.
     It previously drove the retired zip overlay (--zip, a sha256 gate over a
     release archive, unzip -o). It now runs git fetch + git reset --hard and
     then closes the four gaps a file move never closes (CLAUDE.md section 7).
     Step map, from `grep -n '^# .. \[' scripts/deploy.sh`:
       [0]:130 preconditions   [1]:176 fetch      [2]:187 show
       [3]:201 live-edit gate  [4]:245 reset      [5]:262 requirements
       [6]:294 frontend        [7]:343 graph pin  [8]:390 stop
       [9]:403 bytecode sweep  [10]:422 parity    [11]:498 start
       [12]:506 health gate    [13]:568 summary
     `tools/check_requirements.py` (new, 133 lines) extracts the
     requirements-resolution check that was inlined in
     scripts/cloud-setup.sh's heredoc. Two callers now:
     scripts/deploy.sh:270 and :283, scripts/cloud-setup.sh:585.
     SELF-MODIFICATION CAVEAT, now documented at step [4] in the script.
     deploy.sh is one of the files `git reset --hard` replaces, but the running
     bash keeps reading the fd it opened at exec time and git renames a NEW
     inode over the path, so steps [4] through [13] execute the PRE-reset copy.
     An improvement to any post-reset step lands ONE DEPLOY LATE, and a green
     run of the script is not evidence that the step changes that run just
     delivered are correct -- nothing below [4] was exercised at the new
     version. MEASURED 2026-08-03 with a two-commit reproduction whose only
     difference was a line after the reset: it printed the OLD text while grep
     on the same file showed the NEW text, and `ls -i` went 1992621 -> 1992622
     across the reset. Not restructured; re-exec'ing the post-reset copy would
     change which code the operator authorized to run, which is worse than
     lateness.

THE MEASURED NUMBERS

  Mutation, cut #7 (live_seed):
    - first battery: 2/13 caught, then 13/13 after the post-review hardening.
    - a later 104-mutant pass over live_seed found 23 escapes. TWELVE of them
      survived re-derivation against the fixed tip; the other eleven did not
      (they were about code that had already changed). All 12 are now closed by
      tests -- 13 mutants, 13 RED on their target test and on no other, 0
      anchor failures, 0 invalid, each proven RED with the mutant applied AND
      green on the pristine file. No source change was needed for any of
      them: every escape was a missing test, not a missing writer.
      The one that flipped the exit code was M22 (see below).
  Mutation, scripts/deploy.sh:
    - 9 escapes, closed 12/12 caught, 0 invalid, deploy.sh restored
      byte-identically. One-line source change (step [12]'s $rcode); the rest
      were tests.

  Band, four lanes run on this tip. All four PASS, all exit 0 unpiped, all on
  venv/bin/python. Cited from the lane reports rather than asserted:
    lane                      files   result
    live-seed-and-capture       49    724 passed, 0 failed, 0 skipped
    deploy-and-requirements     29    424 passed, 1 skipped, 0 failed
    gates-and-enumerators       38    388 passed, 0 failed, 0 skipped
    batch-library-fts           33    506 passed, 0 failed, 0 skipped
    TOTAL                             2042 passed, 1 skipped, 0 failed
  The file column sums to 149 LANE MEMBERSHIPS, not 149 distinct files -- the
  lanes overlap and were not de-duplicated across each other. Do not read 149
  as a file count. The single skip is declared in-test
  (tests/test_cloud_setup_truthfulness.py:262, "BD_REPO_CANDIDATES removed
  entirely"), not an environmental wave-away.
  The gates lane established one fact worth keeping: this branch adds NO new
  test file (`git diff --diff-filter=A e8ec5b1 HEAD -- tests/` is empty; the
  four tests/ entries are all M), so the count-ratchets can only be moved by
  content edits, not by a count change. They were banded anyway.

  Already measured on this tip, recorded so nobody re-runs them:
    bd-regen-order      exit 0, ZERO artifact drift
    bd-guardcheck       7 ok / 0 drifted / 0 missing (non-zero denominator)
    import_graph_gate   PASS at 1618 edges
    version             3.66.848

THE FIVE SECTION-0 DEFECTS FOUND IN THIS CUT'S OWN CODE

The pattern is the point. Three were defects in shipped-this-branch source;
two were defects in the HARNESS that was supposed to catch them. All five are
the same shape: a check whose denominator structurally excluded its subject.

  1. _twin_scan's blind guard tested the SCAN TOTAL.
     library.library_browse wraps its whole query in
     `except Exception: return [], None` (library.py:363-364), so an
     unreadable library is byte-identical over this API to a library with no
     twins. The first version tested `rows_scanned == 0` over the scan total
     -- which page one had already made non-zero. A blind CONTINUATION page
     was therefore INVISIBLE to it: the scan ended scan_complete, called
     itself conclusive, wrote twins.remaining = 0, emitted no warning, and a
     real twin was left dangling. The observation is now PER PAGE
     (report["blind_pages"] records the after_id of every empty page, and the
     warning names it), and an honest last page carrying rows with no cursor
     stays conclusive -- the over-sensitive direction was closed too.

  2. The final twin verification could run over an EMPTY subject.
     tools/live_seed.py:1498. With no targeted ids, _twin_scan reads every
     page, matches nothing BY CONSTRUCTION, calls itself conclusive and
     reports "zero twins remain" with exit 0. The `and targeted_ids` guard
     was present in the source from the original implementation -- what was
     missing was any test pinning it. Mutant M22 deleted it and ESCAPED the
     battery; it was the only survivor that flipped the exit code. Closed by
     a test, not by a source change. Recorded here because an unpinned guard
     and an absent guard are the same thing to the next person who edits it.

  3. tools/check_requirements.py exited 0 on a file parsing to ZERO names.
     This file exists specifically because `pip check` reports clean over a
     denominator that structurally excludes an uninstalled requirement -- and
     the replacement rebuilt the same defect one level up. `unresolved([])` is
     `[]`, so "every entry resolves" came out true over an empty denominator.
     MEASURED on the pristine file: empty.txt -> exit 0 silent;
     comments-and-option-lines-only -> exit 0 silent. Now exit 2
     (UNEVALUABLE) with the condition named on stderr and stdout left empty.
     Exit 2 is not a softer exit 0. Both callers already treated 2 as failure
     and were left alone. Same shape as bd-guardcheck's "0 ok, 0 drifted, 7
     missing, exit 0" before v3.66.818.

  4. deploy step [10]'s BD_HOME warning was gated on BD_HOME being EXPORTED.
     capture.sh:55 DEFAULTS it (`BD_HOME="${BD_HOME:-$HOME/BulkDownloader}"`),
     so gating on `[ -n "$BD_HOME" ]` asked a different question and reported
     clean in the DEFAULT case -- which is the common case, and the one the
     warning exists for. scripts/deploy.sh:484 now computes
     `capture_home="${BD_HOME:-$HOME/BulkDownloader}"` the way capture.sh
     does, compares that, and says so when the directory does not exist at
     all.

  5. Two deploy escapes were HARNESS defects, not absent tests.
     - the curl shim answered every URL identically, so /api/health and /
       could never disagree -- which is the only condition step [12]'s
       root-URL confirmation exists to detect. ROOT_CODE now discriminates by
       URL; unset, every response is what it was before.
     - the fake venv python answered exit 0 to every `-c`, so step [10]'s
       `json.load` read-back was unobservable and a truncated inventory
       sailed through to "ALREADY CURRENT -- VERIFIED". `-c` is now delegated
       to REAL_PY (the interpreter running pytest, passed explicitly, never a
       bare python3).
     Two of the first-draft replacement assertions had the same disease and
     were caught before merge: they anchored on output ("dist") that step [6]
     prints on every skipped build, so they could never fail. Both now anchor
     on stderr and on site-specific wording.

WHAT ONLY THE BOX CAN ANSWER -- all three are UNKNOWN, not guessed

  (a) Do the accumulated bdseed history rows carry the marker in site_name?
      This is the one that decides whether the first armed run does anything.
      The clear FINDS rows with GET /api/history?q=bdseed (live_seed.py:1055),
      and db.db_search LIKEs over url / filename / message ONLY
      (db.py:1179, :1209 -- `url LIKE ? OR filename LIKE ? OR message LIKE ?`;
      site_name is NOT in that list). It then AUTHORIZES deletion on
      site_name (live_seed.py:1325-1326). Two different fields. If the
      accumulated rows do not carry the marker in site_name, every one of
      them is counted `unowned`, NOTHING is deleted, the tool prints CLEAR
      SKIPPED and exits 4. That is the safe direction by design -- under-
      deleting beats over-deleting -- but it means the first armed run may
      legitimately do nothing, and that must not be read as a defect.
      GIT CANNOT RECONSTRUCT THIS. The repository's FIRST commit (860d8be,
      2026-07-29) is also the first commit touching tools/live_seed.py, so
      there is no earlier tree showing what site_name the seeder wrote when
      the oldest rows were created.
      Residue was 66 rows at v3.66.846 (15.22) and 64 at v3.66.844, growing
      ~2-4 per capture; the count today is UNKNOWN. The read-only query that
      settles it, already filed in 15.20:
        SELECT id, site_name, filename, library_id, substr(url,1,60)
          FROM history WHERE url LIKE '%bdseed%';

  (b) Does BD_HOME equal the install dir on the box?
      tools/gui_parity_inventory.py:914 defaults --outdir to the RELATIVE
      "reports", resolved against CWD (:921-923). capture.sh:417 reads
      PARITY_JSON="$BD_HOME/reports/gui_parity_inventory.json". Those are the
      same file only if BD_HOME is the directory the regen ran in. If they
      differ, deploy.sh's step [10] refreshes a copy the suite never reads,
      and the stale one it does read fails the ENTIRE suite -- the v3.66.818
      failure mode (CLAUDE.md section 7). deploy.sh:484-495 now WARNS when it
      cannot establish they agree; whether they agree on test4 is UNKNOWN
      here and one `echo "${BD_HOME:-unset}"` on the box settles it.

  (c) Is lxml present on the box?
      UNKNOWN. It is present in this container (measured, lxml 6.1.1) and is
      now declared, so a fresh install gets it -- but a deploy is
      `git reset --hard`, which does not install anything. If it is absent,
      deploy.sh step [5] is the thing that will say so, because
      check_requirements.py parses requirements.txt rather than asking pip
      what is installed. Nothing has been run on the box to check.

WHAT IS STILL UNVERIFIED IN-CONTAINER

  - The deploy SAFETY/ORDERING mutation battery is 33 mutants and only FOUR
    were judged. That battery was force-reported with M01-M04 done, covering
    the sudo boundary and the stopped-service window. The other 29 were never
    run to a verdict -- they are UNKNOWN, not passed, and the battery must not
    be cited as evidence about the 29.
  - The deploy harness still cannot observe service ORDERING. The curl shim
    does not read simulated service state, so a mutant that starts the
    service before the bytecode sweep, or health-gates a service it never
    stopped, produces the same shim responses as correct ordering. Fixing the
    URL discrimination (defect 5 above) did not fix this; it is a different
    blind spot in the same harness.
  - Nothing in this branch has run on the box. Container green is evidence
    toward the cut, never evidence the box is green (CLAUDE.md section 7).
    In particular tests/test_v3_43_80_modules.py passed here WITHOUT GTK
    typelibs and with DISPLAY unset, which is a measurement, not a claim that
    the section 5 trap is gone.

### 15.26 | Method retrospective for the v3.66.847/848 branch -- where the cost went

15.25 records WHAT shipped. This section records what the shipping COST and
which of that cost was avoidable, because the operator asked for it and because
a session that reports only its output teaches the next one nothing about its
own efficiency. Merged as c8ce9ab. Eleven workflows, roughly 7.5M subagent
tokens, nineteen commits.

The durable rules extracted from this are in CLAUDE.md -- new section 2b
(running work across several agents), the fix-reproduces-the-defect paragraph
in section 0, the `*.py`-is-not-the-Python-files bullet in section 1, the
mutation paragraphs in section 6, and the CI-is-not-test-evidence bullet in
section 7. What follows is the accounting behind them.

WHAT THE ADVERSARIAL WORK WAS WORTH, stated first so the cost has a
denominator. Mutation testing found defects no band could have:

  - `_twin_scan(client, set())` -- running the final twin verification over an
    EMPTY subject -- passed all 238 tests while turning a real failure (exit 4,
    TWIN ERROR, one twin dangling) into a clean success (exit 0, "0 remain,
    re-measured not inferred"). Nothing else in the session would have caught
    it.
  - Hardcoding either /api/batch/delete payload `limit` to 1 escaped a 39-test
    battery. `_build_query` appends LIMIT after the IN clause, so an undersized
    limit SILENTLY drops ids: the clear would under-delete and report success.
  - Cut #7's first battery scored 2/13 caught. After closing them, 13/13.

  A green band was present at every moment those defects existed. The band
  covers the denominator that already exists; a new code path is by definition
  outside it. That is section 2a's rule, and this is its strongest instance.

WHERE THE COST WENT, and how much of it was avoidable.

  1. STALE WORKTREES -- the single largest waste, and entirely avoidable.
     Four subagent worktrees came up at the session's base commit. Two mutation
     batteries reimplemented the feature and scored their own code; two more
     measured a pre-fix commit and reported defects already closed. That is
     four batteries' work, of which roughly two were wholly unusable and two
     needed re-derivation before anything could be acted on. The fix is three
     lines of preflight (CLAUDE.md 2b) and it worked the moment it was applied.
     It should have been in the FIRST workflow's prompt, not the fifth.

  2. THE lxml CENSUS, DERIVED THREE TIMES. First by grep (wrong in both
     directions), then by AST (correct set, wrong denominator statement), then
     re-measured after the denominator grew to include extensionless scripts.
     Two of those three passes were avoidable by using AST first -- which
     CLAUDE.md section 1 already told me to do, and which I read that morning.

  3. THE DECLARATION GATE: FOUR ROUNDS FOR SCOPE THAT WAS NOT ASKED FOR. The
     operator asked to declare lxml. A critic then found nothing constrained
     the declaration, so a gate was added -- and the gate needed three repairs,
     each finding the defect class it existed to catch. The gate is genuinely
     better than no gate, but it consumed roughly a third of the session's
     agent budget for a test that did not exist that morning, and it was
     scope the operator never requested. NEXT TIME: when a critic finds a gap
     that widens scope, price it and say so before building it.

  4. BANDS RE-RUN AGAINST A MOVING TREE. Several workflows wrote to the tree
     concurrently, so most band numbers described a commit that was no longer
     the tip by the time they were read, and the final band had to be run
     again from scratch. Serialising the two workflows that touched
     tools/live_seed.py, or freezing the tree between phases, would have made
     one band run stand.

  5. ONE SELF-INFLICTED RED BRANCH TIP. A `git add -A` in a regen commit swept
     a concurrent workflow's uncommitted RED battery onto the branch while its
     implementation was still uncommitted. HEAD then failed its own guard
     tests until it was noticed. Explicit paths, always.

WHAT WAS ACCURATE, and what was not.

  ACCURATE, and worth repeating: every claim that came with a pasted command
  and its real output survived scrutiny. The end-to-end probe against the REAL
  Flask app with a real sqlite database -- once through test_client, once over
  a real TCP socket with live_seed's own urllib -- is what turned a pile of
  FakeClient agreement into evidence about the application. Do that earlier
  next time; it was run by the ninth workflow and could have been the second.

  NOT ACCURATE, mine, in order of embarrassment:
    - the grep census, written into requirements.txt, the CHANGELOG and this
      register before AST caught it;
    - "the wrong census is in three places" (it was two -- the CHANGELOG never
      carried it), a claim I passed to an agent as fact and which it corrected;
    - "a genuinely empty library still reports a conclusive zero", asserted in
      a brief as a premise to preserve, and false on HEAD as well;
    - the `git add -A` above;
    - characterising deploy.sh as the mitigation for the lxml install gap,
      which is circular for the deploy that DELIVERS deploy.sh.
  In every case an agent caught it, because the agents were asked for evidence
  rather than for agreement. That is the argument for the adversarial shape
  even when it is expensive.

THE CHEAPEST THING THAT WOULD HAVE HELPED MOST: state the commit in every
report. Four bands, three batteries and five lenses each measured a different
tip, and reconciling which finding applied to which tree cost more than any
single defect in the branch.

### 15.27 | The tools already existed -- addendum to 15.26

Asked afterwards what would have made the session faster, the answer turned out
not to be a technique. It was: READ `toolchain/bin` FIRST. There are ~249 bd-*
tools and the session used four of them.

MEASURED, 2026-08-03, `tools/live_seed.py`:

    grep -rl live_seed tests/*.py                    -> 17 suites
    bd-band-derive --file tools/live_seed.py         -> 25 suites

A strict superset: 8 files the grep could not see, 0 dropped. The band derived
by hand in ELEVEN separate workflows was NARROWER than one command, every time.
That is an accuracy defect, not merely wasted effort -- a narrow band is how a
regression goes green.

The tool unions four signals; `grep -rl` is one. The other three are a
filename-stem glob, the curated TOUCHED_FILE_TO_TEST.md map, and declared
COUNT-COUPLING (a test that exercises a module without importing it or naming
it). Its docstring states the module-consumer signal exists because that gap
"forced a by-hand `grep -rl <module> tests/` on every cut since. Now
mechanized."

WHY IT WAS NOT USED, which is the part worth fixing: CLAUDE.md never named it.
Section 4 said "derive it with `grep -rl`, don't guess" -- so the session did
exactly what the contract instructed, and the contract instructed the weaker
method. Section 4 now names the tool and carries the table above; section 8 now
says to look in toolchain/bin before hand-rolling, with the four tools this
document already depends on.

THE DEEPER FINDING. `bd-mutation-test`'s docstring has recorded the
detector-with-the-bug-it-hunts shape since v3.66.737 -- "the tool built to hunt
gate-blindness was itself a blind gate" -- which is the same lesson section 0
gained today from rediscovering it five times. The repo already knew. The
knowledge was in a tool docstring that nothing indexed, which is a storage
problem, not a knowledge problem. When you find a lesson, check whether some
tool learned it first.

AND THE TOOL HAS THE DEFECT SECTION 0 OPENS WITH. `bd-band-derive --file
CLAUDE.md` reports `changed source (0)` and a one-suite band, because it does
not count `.md` as source. Section 0's very first example is a band tool that
"didn't count .tsx/.ts as source, so it reported changed source (0) on a real
frontend cut." Same tool, same shape, a different extension, still live. Filed
here rather than fixed: it is a real defect and it is not this cut's subject.
The hand-derived doc band (the four gates that read CLAUDE.md, the five that
read this register, the enumerators a tracked .md edit moves) ran 235 passed /
1 skipped, plus the tool's one suite at 14 passed -- so the wider band was the
binding one, exactly as "floor, not ceiling" says.

### 15.28 | Session close 2026-08-04 at 030f16a -- four merges, and a freshness audit

STATE, measured not inferred:

  main 030f16a, version 3.66.848, working tree clean, no open PRs, and `main`
  the only branch local or remote.
  Merged this session, in order:
    c8ce9ab (#143)  cut #7 + lxml/cssselect declarations + scripts/deploy.sh
    1869cf9 (#144)  the method lessons into CLAUDE.md; CI tests; bd-band-derive
                    fixed; bd-mutate added
    939a37d (#145)  bd-bandcheck wired into bd-band; bd-claim + the pre-commit
                    hook; the prose-only ratchet
    030f16a (#147)  this section, the pyyaml declaration, and a section 2b
                    prose repair. (#146 was a DUPLICATE of #145, closed
                    unmerged -- see the git error below.)

  THIS HEADER WAS WRONG FOR ONE COMMIT. It first read "close at 939a37d",
  written before the commit that carries it merged, so the register named a tip
  that was already one behind. Caught by asking whether compaction was safe and
  CHECKING rather than recalling. A session-close section states the tip it
  closes AT, so it can only be correct if written or corrected after the merge.

A NEW FAILURE MODE FOR SECTION 2b, worth more than the typo above. After #145
merged, `git fetch --prune` deleted the REMOTE branch but the LOCAL branch of
the same name SURVIVED, still pointing at the pre-squash commit 1602ed9. Two
mistakes then compounded: the session-close commit was made on local `main`,
and `git push -u origin claude/...` pushed the surviving stale LOCAL branch
rather than that work. GitHub diffed 1602ed9 against its merge base and
re-presented all of #145's content as a new PR (#146), while the actual commit
never left local main.

  WHAT CAUGHT IT: the stop hook reporting an unpushed commit on `main`. Nothing
  in the repo would have.
  WHAT MADE THE REPAIR SAFE: section 7's two-dot check.
  `git diff --stat origin/main origin/<branch>` was EMPTY, proving the stale
  branch carried nothing main lacked, and only then --force-with-lease. That
  check exists so a force-push cannot destroy unmerged work, and this is the
  first time it has been load-bearing rather than ceremonial.
  THE HABIT THAT PREVENTS IT: `git branch -D <name>` alongside the post-merge
  `git fetch --prune`. Pruning collects the remote ref; it does not touch the
  local branch, and a local branch at a pre-squash commit is a loaded gun.

  THE BOX CAPTURED #143 AS PASS at 2026-08-03T23:32: 14573 total / 14488 passed
  / 0 failed / 0 errors / 85 skipped; live 36/0/0; graph check-hash OK
  (211cb5a3be3ea80e); /api/health sha c8ce9abbaff7 -- the merged squash, so the
  running PROCESS was verified and not merely the tree. Count delta from
  v3.66.846 is +64 total / +64 passed with skips unchanged at 85.

  CUT #7 BEHAVED ON THE BOX EXACTLY AS SPECIFIED, which is worth recording
  because it was designed against a stub: residue reported
  history_rows_found=68, history_rows=68, cleared=false, TEARDOWN-EXIT=0. The
  two names being EQUAL with cleared=false is the deviation-(a) semantics
  working -- they diverge only when a clear runs. The RESIDUE line cited the
  db.py:988-992 retraction rather than the append-only claim, so deviation (c)
  is live. And the clear stayed DISARMED through an unattended run, which was
  amendment 6's whole job.

FRESHNESS AUDIT, run because a session that ends without one hands the next one
a tree it cannot trust. Every line below is a command's real output.

  BOOTSTRAP -- NO FORK. The panel's pasted setup text and
  scripts/cloud-bootstrap.sh are BYTE-IDENTICAL, sha256 5ceb75b1be77d60e both
  sides, `diff` exit 0. This is the highest-risk artifact in the project (the
  only provisioning text outside the repo's reach, and it forked three commits
  and 91 lines once before), so it is checked by diffing, never by looking.

  ENV BOX -- MATCHES. CLAUDE.md section 5's five panel variables are exactly the
  five the operator has set and exactly the five live in this session:
  BD_HOME=/tmp/bd_home, BD_REPO=/home/user/BD, BD_SKIP_ARCHB=1,
  BD_SKIP_BROWSERS=1, BD_DISABLE_KEEPALIVE=1.

  DOCS -- CLEAN. bd-doc-truth exit 0, "0 stale doc claim(s)", no stale
  file-path claims.

  .claude-env-report.md -- STALE, and now says so. bd-env-report-check exit 1:
  the report claims version 3.66.818 / commit cee4be70f8e7 against a tree at
  3.66.848 / 939a37d65d36, thirty releases apart, written 2026-07-28. It is
  gitignored, so it survives `git clean -fd` and cannot be fixed in a commit; a
  STALE banner was prepended in place instead. A fresh container regenerates it
  and never sees the banner. CLAUDE.md section 7 already says to check this file
  before believing any row in it -- this is that warning coming true.

  pyyaml -- WAS INSTALLED BY HAND, NOW DECLARED (requirements-dev.txt). A
  session installed it to validate a ci.yml edit and nearly shipped that edit on
  an indentation eyeball instead. MEASURED: the only installed distribution
  naming pyyaml is markdown-it-py, gated behind `extra == "rtd"` which BD never
  requests -- so nothing guaranteed it and a container rebuild would lose it
  silently. No runtime declaration is owed: an AST walk finds zero tracked
  importers.

  lxml and cssselect are declared (#143) and PRESENT ON THE BOX -- proven by the
  capture, which has zero <failure> and zero <error> elements across both XMLs,
  so TestAccessibility and TestSynthetic passed rather than being absent.

TOOLS THAT NOW EXIST AND ARE WIRED, so nobody rebuilds them:

  bd-mutate      one mutation harness. Encodes the four ways five hand-rolled
                 ones were wrong in a single session (non-unique anchor,
                 non-parsing mutant scored as an escape, stale bytecode,
                 baseline never green). Exit 2 = the battery has NO VERDICT,
                 which is not a softer 1.
  bd-claim       declare files you are editing; .githooks/pre-commit refuses a
                 commit that would sweep another live process's work. Armed by
                 scripts/cloud-setup.sh; opt-in on the box
                 (`git config core.hooksPath .githooks`).
  bd-bandcheck   now CALLED by bd-band, so an unsafe band is refused at the door
                 instead of timing out 200s later.

  All three run their --selftest inside tests/test_toolchain_534.py, which is
  what makes them wired rather than described.

FILED, NOT FIXED -- two tools carry zip-era /home/claude paths and are unusable
here. This is s5 residue wearing a different hat, and it is a separate subject:

  bd-band       band_env() hardcodes PYTHONPATH=/tmp/prestaged_site_packages and
                PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright
                (:52-53). MEASURED: tests/test_contracts.py gives "Passed: 4 |
                Failed: 10" under bd-band and 14 passed under pytest, and
                origin/main reproduces the identical 4/10 -- pre-existing. Its
                REFUSAL path (the part wired this session) is unaffected.
  bd-factcheck  defaults its doc to /home/claude/STATE.json, absent here. It
                exits 2 rather than passing over an unreadable denominator --
                "REFUSING a clean verdict: 0 of 1 docs could be read" -- which
                is the correct behaviour and a good example of it.

STILL OPEN, unchanged by any of this:

  site_name on the box  THE ONE THAT DECIDES WHETHER #7 DOES ANYTHING. The clear
      FINDS rows by q=bdseed (db_search LIKEs url/filename/message) but
      AUTHORISES deletion on site_name -- different fields. Residue is 68 rows
      and grows ~2/capture. If they do not match, the first armed run deletes
      nothing and exits 4. No container can answer it and git cannot
      reconstruct it: the repository's first commit introduced live_seed.py.
  BD_HOME vs the install dir  decides whether deploy.sh's parity regen writes
      where the suite reads (the v3.66.818 whole-suite failure).
  s4#4, s5, item 12's producer divergence, Audit #3  -- see 15.20/15.22/15.23.
  Small: `git rev-parse HEAD` into 01_sysinfo.log (checked this bundle; still
      absent, so capture bundles still cannot self-identify) and a selftest
      stage for capture.sh.
  166 bd-* tools remain prose-only. The ratchet stops that number growing;
      wiring or retiring them is its own work.
