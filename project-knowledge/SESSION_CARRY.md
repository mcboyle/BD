# SESSION_CARRY -- carried state, 2026-07-28 onward (newest section wins)

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

## Provenance -- PER SECTION, not for the file

**This file is append-structured, and there is no single date at which it is
true.** Every `15.x` section states the tip it was measured at, in its own
heading or first paragraph, and the HIGHEST-NUMBERED section supersedes the ones
before it. Read that section first; it names what the earlier ones no longer
describe.

The block below dates **sections 1-13 only**. It used to sit here unqualified,
claiming `3.66.818`/`f337bdc` for the whole document while the content ran sixty
versions past it -- so a reader obeying the header would discard the fresher
material underneath it. A single hand-maintained date on an append-only file is
guaranteed to go stale, which is why this is now scoped rather than refreshed:
refreshing it would only reset the clock on the same defect.

    sections 1-13 generated  2026-07-29 (refreshed after PR #55)
    against version          3.66.818   (bulk_downloader/__init__.py:33)
    against origin/main      f337bdc
    live-check registry      37
    guard pins               7 ok, 0 drifted, 0 missing
    working tree             clean at time of writing

Treat every finding below as a claim to re-derive, not a fact to inherit. A
document that cannot be dated is indistinguishable from one written against
another tree -- and one that CAN be dated is still only true of that tree.

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

NOT A SIDE FINDING -- THIS WAS THE DEFECT. `frontend/src/routes/Library.tsx:497` calls the audit
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
  - s4#3  CLOSED at v3.66.913 (15.45). Was "install_service.sh polls
          is-active, not serving (improved, class stands)". The class does NOT
          stand: the /api/health probe landed at @836 and
          test_install_service_waits_for_serving.py pins it, 7 passing.
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

### 15.35 | THREE numbering schemes reconciled into one ordered list -- and five items were already closed

Written 2026-08-05 at `ea524f7` (v3.66.885 merged). The operator supplied
seventeen screenshots of THREE different sessions, each carrying its own open
list in its own numbering. They do not reconcile with each other and none
reconciles with this register, so this section makes one canonical list and
records what was measured against the tree rather than transcribed.

    S1  "BulkDownloader handoff..."      ~875/876   Items 1-19, Batch A/B,
                                                    Queue 7a/8b/8c/7b/9
    S2  "BulkDownloader v3.66.82..."     ~82x       12 items in four groups
    S3  "BulkDownloader discrimi..."     CURRENT    matches this register

**S3 IS CANONICAL.** Independent corroboration rather than assertion: S3 reports
"18 of 93 suites still fail" under `bd-band`, and this session measured 75/93
green on its own 93-file run. Same number, two sessions, no shared measurement.
S1 and S2 are historical and should not be worked from.

**FIVE ITEMS THOSE LISTS CALL OPEN ARE CLOSED. Measured, not read:**

| the list said | measured at ea524f7 |
| --- | --- |
| S1 #6: the CLAUDE.md section 6 line about an interrupted `bd-mutate` not restoring is "confirmed not present" | PRESENT -- section 6 carries it, with the SIGTERM/SIGKILL split |
| S1 #2 / Item 7: `test_pk_mirrors_do_not_drift` cannot fire -- `pytest.fail()` unstubbed, and its `SOURCE_DIRS` loop `break`s on first match | BOTH FIXED. The stub defines `fail` (`run_tests_core.py:138`), and `break` survives only inside docstrings that explain its removal |
| S2 #4: `cookies_expiry_info` misreads the `-1` session sentinel | FIXED. The non-positive branch counts a session cookie, and the comment names the old truthiness bug by mechanism |
| S1 #8: `bd-band` carries three `/home/claude` paths | ZERO, in the tool and in its project-knowledge mirror |
| S1 #8: `test_contracts` gives 4/10 under `bd-band` | 14/14 since v3.66.885, matching bare pytest exactly |

**THE "CONFIRMED OPEN" CLAIM IN THIS SECTION WAS WRONG. RETRACTED at v3.66.887,
and the way it was wrong is the section's own subject.** It read: "S2 #2, band
runs writing into the working tree -- all three of `plugins/ackgate.py`,
`plugins/handdropped.py` and `plugins/registry.json` are tracked=no AND
ignored=no." Every one of those three words was true and the conclusion did not
follow. `git check-ignore` answers about a RULE, not a FILE; none of the three
files exists in this container, and no band run here ever created one. The probe
could not see its subject and returned a confident "still open" -- CLAUDE.md
section 0, committed to the register by the session that had just written a
section about it.

MEASURED at v3.66.887, and S2 #2 is substantially CLOSED:

  * `.bd_last_band.json` HAS an ignore rule (`.gitignore:51`), landed at @868.
  * `plugins.registry.json` -- the path `plugins.py:1745` actually writes -- is
    ignored (`.gitignore:87`). `plugins/registry.json`, the path the retracted
    probe asked about, is a DIFFERENT path that nothing writes.
  * the repo plugins dir is guarded by `tests/conftest.py:651`
    `_never_write_the_repo_plugins_dir`, which redirects it to a sandbox, and by
    a dedicated gate, `test_no_test_writes_the_repo_plugins_dir.py`. Both green.
  * nothing in the tree creates `ackgate.py` or `handdropped.py` at the repo
    root; they are fixture names the guard sandboxes.

S2's observation was true when made and has been fixed since. **A stale item and
a live one are indistinguishable until something is run** -- which is the whole
argument for verify-then-act, and the reason a "confirmation" needs the same
instrument discipline as a closure. The higher-stakes verdict is not always the
closure.

S3's zero-collect item DID reproduce in this session's own band output -- a
helper module with zero tests graded FAIL. That one is genuinely open, and it
was observed rather than inferred.

**ANCHORS THAT HAVE DRIFTED.** Every one of these was cited with a `file:line`
that no longer points at its subject. Section 1 applies: re-derive before
working any of them.

| cited | actual |
| --- | --- |
| `runner_auth.py:177/:331` | `start_manual_login` at :379; `is_alive()` at :189 and :407 |
| `cookies.py:134` | `cookies_expiry_info` at :152 (and fixed) |
| `bd-claim`, "9 `os.getpid()` references" | 7 |
| `library_final.regen_nfos_from_history:474` | the def is at :468 |
| toolchain budget "239/239" | 240, raised at @882 as acknowledged debt |

**AND THE `/home/claude` COUNT IS THREE DIFFERENT DENOMINATORS, NOT A TREND.**
S1 says 391 files, S2 says 393 files / 1,541 occurrences, and this session
measures 1103 occurrences across 248 TRACKED files. `/home/claude` is outside
the repo and per-container, so a container figure and a tracked-source figure
are not comparable in either direction, and neither is comparable across two
containers. Anyone reading these as progress is reading noise.

**BOX -- THIS REGISTER CAN SETTLE WHAT THOSE SESSIONS COULD NOT.** S2 says the
last capture is v3.66.878; S3 says 882 and that it has "no box evidence for
883". The operator supplied the 883 bundle to THIS session: **PASS**, deployed
sha `f863c49369bb`, 14703 total / 14618 passed / 0 failed / 0 errors / 85
skipped, live 36 pass / 0 warn / 0 fail, graph check-hash OK, run
2026-08-05T22:30:22. So v3.66.883 IS captured and the gap is **two** cuts
(884, 885), not three.

**A CORRECTION THIS SESSION OWES.** It told the operator that `capture.sh` uses
real pytest and therefore "does not exercise" `run_tests_core`, so v3.66.885
needed no capture. Too strong, and S3 had it right: capture does not USE that
runner, but its suite contains roughly nineteen tests that import and assert on
it -- `test_pytest_runner_boundary`, `test_capture_execution_lanes`,
`test_harness_retry_timeout` and the flake-registry suites among them. A
capture DOES yield box evidence about 885.

THE CANONICAL ORDERED LIST is in 15.36.

### 15.37 | Session close 2026-08-06 at bfe4ac7 (v3.66.889) -- SUPERSEDES 15.36's statuses

Six cuts merged in one session. This section carries the deltas; 15.36 remains
the canonical list SHAPE but four of its statuses are now wrong, corrected here.

WHAT MERGED

| cut | commit | subject |
| --- | --- | --- |
| 884 | `6262b19` | bd-freshcheck's shallow-clone false STALE + the ci.yml comment |
| 885 | `ea524f7` | run_tests_core fixture resolution (items B and B2) |
| 886 | `60d7f7d` | three numbering schemes reconciled into 15.35/15.36 |
| 887 | `0e86884` | tier 1 executed, plus a retraction (below) |
| 888 | `883808f` | build_session_pack's two final gates both failed open |
| 889 | `bfe4ac7` | import-graph gate widened to tests/, with the band-derive trap closed |

STATUS DELTAS AGAINST 15.36 -- these four are CLOSED and its text is stale:

  * **item 10** -- CLOSED, and it was already closed before this session. The
    line describes a defect fixed at v3.66.874 (`8498558`). VERIFIED: the
    `final` marker is stamped (`ai_boot_status.py:104`), the in-flight write
    precedes attempt 1, and `read_status` grades both `no_finality_marker` and
    `abandoned`. **The residual is DELIBERATE, not an oversight**: a SIGKILLed
    writer reads `retrying` for up to 300s, and the 874 entry states the
    trade-off ("TTL and heartbeat ship together or neither ships") and sizes
    300 from a MEASURED ~108s run. Narrowing it with a PID liveness check is a
    NEW proposal needing operator sign-off, not this item.
  * **item 13** -- CLOSED at 888, and it was not the item 15.36 described. See
    the changelog: bd-state is live (build_release.py still ships a zip
    containing STATE.json); the defect was both of build_session_pack's final
    gates failing open, each citing the other as its excuse.
  * **item 20** -- CLOSED at 889. Baseline 1618 -> 3750 edges, 506 -> 1491
    source keys, 985 under tests/.
  * **items 5b + 6** -- already closed before this session; 15.35 carries the
    retraction of the false "confirmed open".

**A NEW OBLIGATION ON EVERY FUTURE CUT, created by 889.** `bd-band-derive` now
fires the `import edges` regen flag for a **tests/** change, not just
`bulk_downloader/`. Verified immediately after the merge:
`bd-band-derive --files tests/test_contracts.py --json` -> `regen_flags:
['import edges']`. So **any cut adding a test file that imports a product or
tool module owes `import_graph_gate.py --update` in the SAME cut.` That is new
since 2026-08-06 and no older register section mentions it.

NEXT ACTION, fully scoped -- capture.sh, and it is TWO cuts, not one

The operator gave GO for item 2 (register) = "Item 9" (older scheme). **A
complete, adversarially-reviewed spec already exists in-repo at
`project-knowledge/pending-specs/recon-queue-items-1-9.md` -- read it before
re-deriving anything.** It says verbatim "TWO CUTS, TWO RED TESTS ... do not
batch these", and the operator confirmed that split after this session's first
framing (one cut) was found to contradict it.

  * **CUT 1 -- commit identity into `01_sysinfo.log`.** ~20 lines of shell,
    inserted INSIDE the existing brace block after the `--- date ---` pair.
    THE TRAP, MEASURED IN THE SPEC: a bare `git rev-parse HEAD` walks UP, so if
    BD_HOME sits inside another repo the stage emits a confident sha for a
    DIFFERENT tree -- worse than today's honest silence. The snippet needs the
    explicit MISMATCH branch comparing `--show-toplevel` against `$PWD`.
    **It must NOT be wired to `--stage-exit`**: a non-git tree would then fail
    the release gate for a reason no code change can fix.
    `capture.sh:8` records the version-bump exemption for capture.sh-only edits.
  * **CUT 2 -- the `/api/selftest` stage.** Adds `tools/selftest_verdict.py`, a
    new `--stage-exit`, and a new way for the release gate to fail; wants its
    own band and its own box run. The load-bearing line is the non-empty
    denominator `(ok+warn+fail) >= 1` -- without it `{"error":"endpoint not
    found"}` behind an HTTP 200 yields a reassuring log that makes the blind
    spot INVISIBLE. Not capture.sh-only, so the bump exemption probably does
    not reach it; the spec does not resolve that.

BLAST RADIUS, RE-DERIVED 2026-08-06 (not inherited): `bd-band-derive --file
capture.sh` = **100 suites**, a floor. On top: the ten axis-6 gates (both cuts
add a test file), `PIN_INDEX` regen, and now the **import-graph re-freeze**
above. Cut 2 additionally moves the GUI-parity denominator, because
`gui_parity_inventory.py` globs `tools/*.py`.

THE SHARP CONSTRAINT, and it bites CUT 2 only:
`tests/test_provision_test_host.py:798-800` asserts the comment-stripped
`capture.sh` contains **zero** lines starting with `#`. `_strip_shell_comments`
carries quote state across lines, so a `#` inside a multi-line quoted PYTHON
program survives stripping and turns it red. Cut 1 is pure shell and safe; this
is why cut 2's JSON parsing belongs in a `tools/` file, never an inline
`python -c`. Cut 1's RED harness DOES reach its subject:
`test_capture_shell_runtime.py:28` cuts at `# -- [2b/9]`, so step [1] is in
probe range; cut 2's stage near line 1035 is not, and must be extracted on
structure between the `[7/9]` and `[8/9]` banners.

METHOD -- THREE OF THIS SESSION'S OWN ERRORS, all instrument-caught, none
review-caught. Recorded because the pattern is the finding:

  1. **A false CONFIRMATION merged into the register** (retracted at 887). A
     `git check-ignore` probe was run against three paths that do not exist in
     the container; it answers about a RULE, not a FILE, and "ignored=no" was
     read as "the defect is live". Section 0, written by the session that had
     just written a section about section 0. **A confirmation needs the same
     instrument discipline as a closure** -- the higher-stakes verdict is not
     always the closure.
  2. **A fixture that never reached the gate under test** (888). The state was
     schema-INCOMPLETE, so after the repair `main()` exited at the schema gate
     and never reached bd-state; the case passed for the wrong reason and FOUR
     of five mutants escaped. `bd-mutate` found it; reading did not.
  3. **A test matched by its own filename** (889). The assertion read
     `"import_graph" in " ".join(band)` and passed on pristine source because
     the test FILE's name contains `import_graph`, so bd-band-derive's
     filename-stem signal pulled it into its own band. Found by reading the
     tool's raw JSON instead of trusting the green.

BOX. v3.66.884 CAPTURED PASS at `6262b19e0a08`: 14710 total / 14625 passed / 0
failed / 0 errors / 85 skipped, live 36/0/0, graph pin `b5a1e5d5...`. **The
delta reconciles exactly** -- 883 was 14703/14618 and 884 added seven tests, so
the box moved +7/+7 with nothing unexplained. A capture of 885/886 was running
at session close; expect 14720 / 14635 / 85. 887-889 have no box evidence, and
889 changes a gate the capture exercises, so it wants one.

### 15.36 | The canonical open list, 1-33, at ea524f7 -- STATUS SUPERSEDED by 15.51

The LIST below still stands as the enumeration. Its STATUS column does
not: a re-derivation at 28cc9de (15.51) measured 23 of these items and
found FIFTEEN already closed, two mis-scoped and five genuinely open.
Read 15.51 before acting on any row here.

Ordered by what unblocks what, not by age. Re-derive each from source before
working it (section 1); several of the anchors above were stale within days.

BLOCKED ON THE OPERATOR (1-3)

  1. **CANNOT-EVALUATE, permanently -- and that is a verdict, not a gap.**
     7b, name the twelve retired tools. Unrecoverable from the tree; retiring
     the wrong tool is not repairable. Re-affirmed at v3.66.959. Section 0's
     third state: this is not open work waiting on effort, and carrying it as
     open forever is what makes an open list stop meaning anything.
  2. **CLOSED at v3.66.892 (`ef9f253`, #196), recorded at v3.66.960.** "a
     capture bundle can finally say which tree it graded". Three independent
     confirmations, none of them a reading: `capture.sh:298 emit_commit_identity`
     exists and handles the UNKNOWN and MISMATCH branches;
     `tests/test_v3_66_892_capture_commit_identity.py` is 7 tests, all passing;
     and the operator's 2026-08-08 bundle carries `commit : 51ac1eb...` /
     `branch : main` in `01_sysinfo.log`, a sha verified an ancestor of `main`
     (exit 0, authoritative). Item 19's other half shipped with it --
     `capture.sh:1080` is the `[7b/9] Live selftest battery` stage and the
     bundle contains `07b_selftest.{log,json,err}`. The entry sat open as a
     release gate "needing explicit GO" for eleven weeks after the GO was
     evidently given and the work shipped.
  3. **CLOSED at v3.66.952-954 in three tiers**, and the item's premise was
     wrong twice over. It said a sweep "turns red a test that positively pins
     the string" -- singular; measured, at least TEN test files pin it, and
     several exist IN ORDER TO assert the path is retired, so a blanket sweep
     would have deleted their subject. Re-measured population: 188 carriers /
     829 lines (374 executable, 455 prose), not the register's 246, which was
     measured at 28cc9de. Tier 0 froze the population, tier 1 fixed a live
     defect (bdenv.sh clobbering a correct PLAYWRIGHT_BROWSERS_PATH), tier 2
     widened the pk-mirror gate that could not see 20 of its own subjects. The
     zip-era retirement is item 38.

WORK, NOT BLOCKED (4-19)

  4. **CLOSED at v3.66.911 (15.44).** Was: "`bd-band`'s three remaining root
     causes". It was FOUR, the feature list was 4 of 7, and "18 of 93" did not
     reproduce -- re-derived at @907 over `git ls-files -- 'tests/*.py'` (1239
     files), 17 files use an unstubbed feature and 13 of 16 real suites failed.
     Original text follows; do not work from it. The `shell_source` group is the sharpest: that helper
     landed at @880, so the suites guarding the last three cuts cannot run
     under `bd-band` at all.
  5. **Batch A** -- `bd-parband` attributes a verdict to a suite it never ran
     (a bad path falls through to a broad run); `.bd_last_band.json` has no
     ignore rule. Small, confirmed open.
  6. **CLOSED 2026-08-06, and the v3.66.886 "re-confirmation" is RETRACTED**
     -- see 15.35. `.bd_last_band.json` and `plugins.registry.json` both have
     ignore rules (`.gitignore:51`, `:87`); the repo plugins dir is redirected
     to a sandbox by `tests/conftest.py:651` and gated by
     `test_no_test_writes_the_repo_plugins_dir.py`, both green. The retracted
     probe asked `git check-ignore` about three paths that do not exist in
     this container, which answers about a rule and not a file.
  7. **Zero-collect classification** -- `bd-band` grades a zero-test helper
     module FAIL, and the derive sweeps helper modules into bands.
     Reproduced in this session's own run.
  8. **CLOSED at v3.66.959: all four already refuse.** `bd-opv`, `bd-equiv`
     and `bd-fullsuite` declare a CANNOT-EVALUATE path; `bd-env-report-check`
     was MEASURED rather than grepped -- run with no report present it exits
     **2** with "UNKNOWN: no report at ...; nothing here describes the
     environment you are in". A grep for the refuse path had reported it
     missing, because the tool words it "UNKNOWN:" and the pattern did not
     match: section 1's grep-is-not-a-denominator, inside this adjudication.
  9. **`bd-claim` is inert from a shell** -- a CLI invocation dies and reaps its
     own claim, so it only works agent-to-agent.
 10. **`ai_boot_readiness.json` has no in-flight marker** -- a mid-run read is
     indistinguishable from terminal failure.
 11. **CLOSED at v3.66.960: the subject is gone, and the specified mechanism
     was never needed.** The item authorized two cuts to add a `sys`-attribute
     sentinel gating app.py's boot block. @926 removed the module-scope writers
     outright instead, which is strictly better than gating them, so the
     sentinel does not exist and should not be built. MEASURED at v3.66.960 --
     one `import bulk_downloader.app` from a bare cwd, run with
     `BD_DISABLE_KEEPALIVE` POPPED as well as set:

     | | spec's measurement | now |
     | --- | --- | --- |
     | DB-class residue | 352,256 bytes | **0 bytes, 0 files** |
     | total residue | + 3 sentinels, config, logs, live_recordings | 714 bytes, 3 files |

     Both of the item's named FATALs were resolved by @926 rather than during
     the specced implementation: `migrations.apply_pending()`, the seventh
     module-scope writer below the gated region, is in 15.50's list of four
     found by TRACING; and the RED's blindness to keepalive-gated writers is
     CLAUDE.md section 0's `os.environ`-copying-harness paragraph, written from
     that same cut. 15.68 then measured 0 inside-repo connects across the full
     suite in BOTH parallel and serial, and item 36 reproduced and closed the
     recurrence. What remains is 714 bytes of NON-database residue, which was
     never this item's title and is now item 40.
 12. **THE DECIDING QUESTION IS ANSWERED at v3.66.967: NO operator surface
     shows two producers under one label, so 12 is a RENAME, not a product
     call -- and the three-producer table this entry used to carry was wrong
     about which producer is on screen.** The two caps closed at @915 and
     `regen_nfos_from_history` at @916; 12(c)'s saturation disclosure shipped
     at @957.

     **THERE ARE FOUR PRODUCERS, NOT THREE, AND THE ONE THE OPERATOR ACTUALLY
     SEES WAS NOT IN THE TABLE.** Measured at HEAD:

     | producer | table | predicate | operator surface | label |
     | --- | --- | --- | --- | --- |
     | `bulk_downloader/app_widgets_api.py:234` `_collect_library_data` | `library` | `COUNT` of `file_exists=0`, uncapped | Home + SiteDetail widget | **"Missing files"** |
     | `bulk_downloader/library_final.py:306` `missing_from_disk_scan` | `history` | `status='done'`, then STATS the file live | Library route only | **"missing from disk"** |
     | `bulk_downloader/cleanup_helpers.py:133` `find_missing_metadata` | `history` | rows with no NFO sidecar | command palette only | `missing_nfo` |
     | `bulk_downloader/library.py:737` `library_missing` | `library` | `SELECT *` of `file_exists=0`, `LIMIT 500` | **NONE -- zero callers** | -- |

     This entry previously said `library_missing` reaches the UI "via its own
     endpoint". It reaches no UI at all. `git grep` over every tracked file
     finds `/api/library/missing` in its own route definition, in one comment
     at `bulk_downloader/app.py:5635`, in `tests/route_map_baseline.txt` and in
     four generated catalogs -- and in **no caller**: not the SPA, not
     `bulk_downloader/command_palette.py`, not the extension. The
     `file_exists=0` predicate IS on screen, but through
     `_collect_library_data` -- a fourth code path with a different SHAPE
     (count, not list) and no cap. The register named the dead one and missed
     the live one.

     **WHY THERE IS NO COLLISION: the widget hosts and the audit panel are
     disjoint routes.** `WIDGETS_BY_ID` and `KPICard` are rendered only by
     `routes/Home.tsx`, `routes/SiteDetail.tsx` and `components/WidgetPicker.tsx`
     under `frontend/src`; `routes/Library.tsx` imports neither and is the sole
     renderer of `audit.data.missing`. So "Missing files" and "missing from
     disk" cannot appear on one screen, and the two label strings differ. By
     this item's own decision rule -- *"if none collide it is a naming problem
     and the fix is a rename"* -- **12 is a rename cut, and the only thing it
     waits on is the operator's wording.**

     Confusable ACROSS routes is still real, and the tree already contains a
     developer making exactly that mistake: `widgetCatalog.test.tsx` under
     `frontend/src/lib` seeds `lib_missing_extra: "on disk"` for the widget fed
     by the CACHED index flag -- borrowing the other producer's words for a
     value that never stats a file.

     **THE RENAME SHIPPED AT v3.66.970**, on the operator's wording. The widget
     is now "Index flagged missing" (desc: "cached index, not a live check")
     and the Library route's live figure names the directory it walked, because
     "0 because everything is present" and "0 because nothing resolved" are the
     same glyph without it.

     The cut found a second defect the item had not named: **`lib_missing_extra`
     had TWO independently-maintained producers** in `app_widgets_api.py`, a
     dict-literal entry and an `out[...]` assignment, each carrying its own copy
     of the string. That is S0's drifting denominator at a two-copy radius, and
     the copy nobody updates is the one that ships. Both now route through one
     `_missing_extra()`, which also carries the SCOPE -- the same widget renders
     global on Home and site-scoped on SiteDetail, and the number alone cannot
     say which. The scope is on the ZERO case too, deliberately: "all present"
     for one site reads identically to "all present" everywhere, and that is the
     reading an operator would over-trust.

     The guarding test is AST over BOTH syntactic forms with a non-empty
     denominator assertion, because a predicate seeing only the dict literal
     would find one write site and certify agreement across a set of one.

     **RETIRED AT v3.66.972, closing the item.** `/api/library/missing` and
     `library.library_missing()` are gone, on the operator's decision: straight
     removal plus a stays-retired guard, matching how `codex_handoff`,
     `deploy_manifest`, `task_tracker` and `sandbox_home` were retired. Zero
     callers anywhere in the tree -- the only references were its own
     definition, its route, and two stale comments in `app.py`, all removed.
     Its unreported `LIMIT 500` was the same class 12(c) fixed for `audit()`,
     but adding a saturation flag to an endpoint nobody calls is the wrong
     repair; removing the producer is. Route map 1002 -> 1001 lines,
     `_BASELINE_SHA` re-pinned in the same cut.

     **THE GUARD READS COMMENT-STRIPPED SOURCE, and that was load-bearing on
     this very cut.** `app.py` carried two comments naming the route and the
     view. Section 0 records four cases in one session where an assertion could
     not tell prose from code -- including the comment that spelled a prefixed
     name in order to say it had been removed, putting it straight back into
     the ledger and failing the gate the rename had just fixed (CI caught that,
     not review). Measured here: with stripping, the RED run flagged
     `app_library.py` ONLY; a raw-text version would also have flagged `app.py`
     and forced the comments to be deleted to pass rather than because they
     were stale. The comments were removed anyway -- they documented a route
     that no longer exists -- but the gate did not demand it, which is the
     property that lets someone write "we retired /api/library/missing because
     ..." next year without resurrecting it.

     Four tests, three proven RED. The fourth is the over-sensitivity guard and
     is green both ways by design: three of the four "missing" producers are
     LIVE and must survive, so a cut that removed the word everywhere would
     satisfy the other three and delete working features.

     **DO NOT QUOTE A PRODUCER COUNT.** Three predicates have produced 8-of-3,
     19-of-4 and 23-of-5 over different denominators. The subject is the
     divergence, not the count.

 13. **CLOSED at v3.66.959 as MIS-SCOPED.** `bd-state` has three invocation
     sites, not one: `build_session_pack.py`, `bd-boot:268` and
     `bd-coretest:179` (which exercises it twice, clean->PASS and a forged
     FAULT). The premise "reachable only through build_session_pack.py" is
     false. Kept rather than deleted, per items 24 and 34: the wrongness is
     the record. ORIGINAL TEXT: "Item 15 -- bd-state reachable only through
     build_session_pack.py."
 14. **Phase B manual-takeover early-return** -- `start_manual_login` returns
     while the login thread is alive and Phase B runs inside that thread, so the
     advertised takeover can never open. ANCHORS DRIFTED -- re-derive.
 15. **CLOSED at v3.66.913 (15.45).** Was: "polls started, not serving --
     the loop watches `systemctl is-active`, so the vault unlock can fire
     before the socket binds. Improved, but the class stands." It does not
     stand. The probe IS the fix (@836: /api/health, 15s, three states with an
     honest `unknown` when curl is absent), and BOTH halves are pinned --
     test_install_service_waits_for_serving.py for this file,
     test_capture_step4_waits_for_serving.py for capture.sh step [4].
 16. **CLOSED at v3.66.959: the subject is gone.** Measured over `git
     ls-files project-knowledge` typing on the SHEBANG rather than the
     extension: **zero** tracked extensionless runnable files remain there.
     The three survivors went with the mirror retirement. The spec-rework
     blocker is moot because there is nothing left for the rewritten spec to
     act on.
 17. **CLOSED at v3.66.969, and BOTH earlier framings of it were wrong.** The
     item asked whether a mid-session container restart fires SessionStart, and
     @965 re-scoped it to "the state lives inside the container, so a restart
     may take the record with it -- the real work is moving the state somewhere
     a restart preserves". Neither was the mechanism.

     **THE HOOK DESTROYED ITS OWN EVIDENCE.** `.claude/hooks/session-start.sh`
     wrote the boot record with a TRUNCATING redirect and no comparison, and
     the hook fires on resume. So:

         container restarts -> session resumes -> SessionStart fires
                            -> hook overwrites the baseline with the NEW boot
                            -> bd-restart-check compares new-against-new -> OK

     Measured live at v3.66.968 in this container, which is what surfaced it:
     uptime **6 minutes**, `$HOME/.bd_boot_state` written at the boot minute
     with `source=resume`, its boot id EQUAL to the current one, and the tool
     returning `OK, exit 0`. The state file had not been lost to the restart at
     all -- it had been REWRITTEN by the hook that runs immediately after one.
     The tool's own comment says the mid-session read is *"the only moment the
     reading is unambiguous"*; that is correct, and a resume gives that moment
     zero width.

     So the empirical question the item was waiting on is ALSO answered, by the
     same reading: `source=resume` written at the boot minute means SessionStart
     **does** follow a container restart. The reading was obtainable all along
     -- it was sitting in the state file, and nothing read it because the tool
     only ever compared two boot ids for equality.

     **THE FIX.** The hook now reads the prior record BEFORE truncating and
     carries the previous boot forward when it DIFFERS (positional lines 4-5, so
     a pre-@969 three-line record still parses -- a reader that raised on the
     short form would make the first run after this landed indistinguishable
     from "the hook never ran"). `bd-restart-check` surfaces the transition in
     its OK detail. It stays **exit 0, deliberately**: a transition the hook
     already carried forward is not a live unrepaired restart -- the hook ran,
     so the repair path fired -- and reporting it as exit 1 would overload a
     code whose documented meaning is "and the hook has NOT run since", sending
     a reader to re-provision an environment that already reconverged.

     Only when it differs, for the same reason: recording unconditionally would
     make every ordinary session read as a restarted container, which is
     section 0's over-sensitivity failure.

     **WHAT THE TESTS COST, and the seam is the part worth keeping.** 7 tests,
     2 proven RED on pristine source, 5 green-both-ways by design (the
     over-sensitivity and backward-compatibility guards, which exist to forbid
     a bad fix rather than to prove a good one). Then a seam appeared that no
     per-test reading would find: the writer test reads the state file's lines
     directly and the reader test hands `classify()` a dict it built itself, so
     **`recorded()`'s parsing of the two new lines sat between them with nothing
     driving it**. A mutation dropping those keys breaks the feature completely
     and would have escaped both. Closed with an end-to-end test through one
     process boundary, and the battery proves it was load-bearing:

         4 mutants, 4 CAUGHT, 0 escaped, 0 invalid   (baseline GREEN)
           hook: record a transition when the boot is UNCHANGED
           hook: drop the preservation entirely (the original defect)
           tool: recorded() stops parsing the preserved boot   <-- the seam
           tool: classify() reads the field but never surfaces it

     The question that found it was "what do these two tests SHARE", not "what
     does each cover".

     **AND THE BATTERY'S OWN GUARD WAS WRONG FIRST.** The extended selftest
     forbade the bare word "restart" in the no-transition case, and failed two
     CORRECT cases whose details legitimately contain it -- the unevaluable
     branch explaining that UNKNOWN is *"not 'no restart'"*, and the restarted
     branch itself. A predicate ranging over cases it does not mean, inside the
     check written to prevent exactly that. Repaired to forbid the transition
     CLAIM rather than the word.

 18. **CLOSED, and it was closed before anyone noticed.** Re-measured at
     v3.66.956 by RUNNING the tool, not reading it: a manifest of
     `pytest>=99.0` exits 1 and `pytest>=8.0` exits 0, so the specifier IS
     compared. `tools/check_requirements.py:146-150` builds `Requirement(line)`
     and asserts `specifier.contains(have)`, and raises `Unevaluable` when
     `packaging` is absent rather than falling back to a name-only answer.
     CLAUDE.md section 5 already recorded the fix; this inventory did not, so
     the entry sat open for releases while the code was correct -- section 1's
     own lesson, inside the register that states it. ORIGINAL TEXT: "Venv
     specifier drift -- check_requirements.py calls version(name) and discards
     it, in all three recovery paths. Still THEORETICAL: @883's band failures
     were a runner defect and @885 confirmed it."
 19. **Item 17** -- `git rev-parse HEAD` into `01_sysinfo.log`, plus a selftest
     stage for `capture.sh`. Re-confirmed ABSENT against the 883 bundle.
     Rides item 2.

DELIBERATE DEFERRALS -- OPERATOR DECISIONS, NOT DEFECTS (20-22)

 20. **CLOSED at v3.66.889, recorded at v3.66.959.** The gate walks
     `tests/` -- `import_graph_gate.py:124` is `os.walk(root / "tests")`, and
     its own comment records the widening: tests/ added 2132 edges from 1234
     files, 57% of the real internal import surface. CLAUDE.md section 4 has
     said so since @889; this inventory did not, which is the same
     bookkeeping gap item 18 had.
 21. **The pre-force line `b4f0c80`** of the deleted preflight branch --
     unexamined, no verdict, exists only in the box's object store.
 22. **CLOSED 2026-08-06, verified correct behaviour.** The library panel's
     `missing` rows are `status='done'` history rows whose file is genuinely
     gone, and `_resolve_recorded` deliberately keeps `unknown` and
     `ambiguous` OUT of `absent` (`library_final.py:218-235`) so a
     first-match guess cannot manufacture a row. Not a defect; no code. The
     "31, growing 1-2 per capture" figure is box-local history state and was
     NOT verified -- do not inherit it as a measurement.

BOX-ROUTINE (23-25)

 23. **CLOSED at v3.66.959 as OBSOLETE.** The 885/886 gap is long overtaken:
     the box has since captured PASS at v3.66.941 (15.61) and v3.66.950
     (15.68), each reconciling exactly. The reconciliation METHOD the entry
     describes is the durable part and is worth keeping. ORIGINAL TEXT: The
     capture gap is 885 and 886. v3.66.884 IS captured, PASS at
     `6262b19e0a08`: 14710 total / 14625 passed / 0 failed / 0 errors / 85
     skipped, live 36/0/0, graph pin re-armed to `b5a1e5d5...`. **The delta
     reconciles exactly** -- 883 was 14703/14618, 884 added seven tests, and
     the box moved +7/+7 with nothing unexplained in either direction, which
     is the check worth doing on any capture. 885 adds ten more, so the next
     capture should read 14720 / 14635 / 85; a mismatch is signal.
 24. **CLOSED 2026-08-06 as OBSOLETE. Do not act on the original wording.**
     The census selftest's stale-lock check was DELETED at v3.66.844
     (`selftest.py:622`) because nothing in the tree writes a `*.lock` and its
     rglob only ever found vendored manifests. Probed on the box: **one** hit,
     not three -- `venv/lib/python3.12/site-packages/setuptools/_vendor/.lock`,
     setuptools' own vendored lock inside the gitignored venv. Deleting it
     would have reached into site-packages. **The predicted identity was also
     wrong** -- this session expected npm `yarn.lock` files, per @844's
     recorded reason; the disposition survived that error and the file name
     did not, which is why the probe was run instead of the item being closed
     on the comment alone.
 25. **`~/bd-orphans-2026-08-01.bundle` VERIFIED intact 2026-08-06**; the
     copy-into-rotation is the only step left and its destination is the
     operator's call. `git bundle verify` exit 0, "records a complete
     history", 20M, sha256
     `a86a8fc4a31a4e1e5367910733f37e34e62a263951fad61e92a00c53eeccde8a`.
     It carries **22 non-`main` refs** (24 lines, `origin/main` listed twice),
     which reconciles exactly with CLAUDE.md section 7's "21 no-merge-base
     branches plus a stale merged handoff branch", and `origin/main` itself at
     `2fdc0b0` as of 2026-08-01. Verified BEFORE trusting it, because a sole
     copy that cannot be cloned from is not a backup.

STANDALONE REGISTER FINDINGS, STILL OPEN (26-28)

 26. **15.8** -- census coverage counts rows it never examined.
 27. **15.11** -- qB/JD library rows: a directory has no absolute FILE path.
     Explicitly a PRODUCT decision, not a mechanical fix.
 28. **15.12** -- six extractor completion paths cannot execute. Pre-existing.

YOURS, NOT MINE (29-30)

 29. **CLOSED at v3.66.977 on the operator's own restore test.** Run on the
     box 2026-08-09: `git bundle create ~/d/bd-archive.bundle --all` produced
     7723 objects / 23.07 MiB, and the acceptance test -- fetch into a FRESH
     empty repo -- returned **RESTORABLE**, recovering `main`, `origin/main`,
     `origin/HEAD` and the `archive/preflight-preforce` tag. That is the real
     criterion, not `git bundle verify`, which section 7 records passing on a
     bundle that then failed to fetch.

     The purge step was NOT separately evidenced, and it does not gate the
     criterion: `git bundle --all` packs git objects only, so venvs,
     `node_modules`, `__pycache__` and `frontend/dist` were never candidates
     for it. If disk reclamation is wanted it is its own task, not this one.

     The original entry follows, kept because its acceptance-criterion argument
     is the reusable part.

     **The archive sequence -- FIRST CLAUSE ALREADY DONE, and the acceptance
     criterion must change before anyone works it.** 15.68 records the
     database recovery complete: 108 files, all `integrity_check = ok`. So the
     ordering constraint that made this item special is DISCHARGED; the
     remaining two steps -- purge rebuildable bulk, consolidate into one
     bundle -- have no dependency on it and are box-bound.

     **"ONE VERIFIED BUNDLE" MUST MEAN RESTORABLE, NOT VERIFIED.** CLAUDE.md
     section 7 records `git bundle verify` reporting *"records a complete
     history"* and *"is okay"* for a bundle that then FAILED to fetch into a
     fresh empty repo. Certifying this archive with `verify` would use the
     check already caught lying. The criterion is the restore test:

         T=$(mktemp -d) && git init -q "$T" && \
           git -C "$T" fetch "$BUNDLE" 'refs/*:refs/restored/*' && echo RESTORABLE
 30. **CLOSED at v3.66.932 (`48707ad`), recorded at v3.66.959.**
     `.githooks/pre-push` exists, is TRACKED, and refuses a force-push that
     would discard unmerged work -- exactly section 7's two-dot diff. 15.52
     measured "no pre-push exists" and was right when written; the cut landed
     three weeks later and nothing updated the entry.

PARALLEL PROGRAM -- OLDER, LARGELY OPERATOR- OR CAPTURE-BOUND (31-32)

 31. **15.15 / TASK_TRACKER -- EIGHT rows, not eleven. Re-derived at
     v3.66.965.** Three resolved without touching the box, and one row's note
     was wrong:

     - **EXIT-3 STANDS, and 15.15's open question is now ANSWERED: a green CI
       `postgres-integration` does NOT move it.** That job exercises the mod3
       code paths; it says nothing about production dual-write or a soak
       clock. Measured by running the preflight -- `preflight_cutover()`
       returns `ok=False` with four reasons (dual-write not enabled,
       shadow-read not enabled, *"shadow-read has compared 0 statement(s):
       zero comparisons is NOT evidence of agreement, it is an empty
       denominator"*, postgres not reachable) and `cutover_engaged()` is
       False. Correctly blocked fail-closed, and the refusal states section
       0's rule in the product's own words.
     - **OPV-F3.1 is LAPSED, not open.** Its seven-day window closed
       2026-07-30; that is 10 days before this re-derivation. It either closes
       on evidence already collected or the clock restarts -- an operator
       decision about an expired window, which is not the same thing as an
       open task.
     - **CORPUS-DISPOSITION CLOSED as mis-filed.** Its own text says all 445
       are triaged, retained review-required, explicitly not an OPV failure,
       and that the index is *"DATA, not a register"*. It was never a task.
       The index survives at `.superpowers/sdd/corpus-disposition-review-
       buckets.{md,json}` -- verified present.
     - **JW-TMPL's note was WRONG and is corrected.** 15.15 said the blocker
       was *"site Ultra missing password"*, the same condition *"the box's
       startup selftest still prints on every restart"*, and that *"one
       credential fill may clear this row"*. Measured across all five
       2026-08-08/09 captures: the message is `login: SKIPPED -- site 'wow' is
       missing password`, it lives in `04_service_boot.log` and NOT in the
       startup selftest (whose 12 checks are 11 ok / 1 warn and include no
       credential check at all). Site **wow**, not Ultra. Filling wow's
       password clears that boot warning and does nothing for JW-TMPL, whose
       real blocker -- a live capture with ultrafilms credentials -- is
       unchanged and operator-bound. Two sites conflated into one shortcut.

     STILL OPEN, all live-capture or operator-bound: CAP-ROBUST arm C,
     JW-TMPL, LOGIN-NSTEP, P3-T12-CALLSITE, RPTYL (do not close by relaxing
     `api_patterns >= 1` silently), FR-A6.2, FR-A6.3, 2c-DATA.
 32. **CLOSED at v3.66.965 as OBSOLETE, on operator decision.** 23
     CODEX_HANDOFF groups, and MEASURED: `.superpowers/sdd/` holds **six files
     total** -- the corpus-disposition index, three live-telemetry reports and
     the wacz report. There is nothing for Analysis Tasks 5-7, nothing for
     Governance/gate 1-8, nothing for Audit/knowledge/hygiene/static-KB 1-11.
     Twenty-two of the twenty-three groups have no code, no artifact and no
     gate; Analysis Task 4's frozen review packages are confirmed ABSENT, as
     15.15 recorded. They describe intended work inherited from a document
     that was retired at v3.66.842 and an ENVIRONMENT that no longer exists.

     Closed rather than re-scoped because carrying 22 unbuilt groups as OPEN
     work implies someone intends to do them, and that is the shape that made
     this register untrustworthy -- an open list nobody is on stops being
     read. Reconstructing intent for each would mean mining the git history of
     a deleted file.

     **THE DESIGN DECISIONS ARE THE PART WORTH KEEPING, and 15.15 says why:
     they are "properties of the analysis, not of the retired environment".**
     Carried here so closing the item does not lose them: fail-closed on
     ambiguous semantic facts; Task 3's scope/execution model; explicit bounds
     with secret redaction and atomic path-identity-checked output; Task 4's
     six separate evidence categories. Anything picking that work up should
     inherit these rather than the task list.

ONGOING, NOT A FINISH LINE (33)

 33. **CLOSED at v3.66.1039 on operator decision -- and closing it did NOT
     delete the gate.** Two ratchets were conflated under this number and only
     one is retired:

     * `_TOOL_BUDGET`, the TOTAL tool count, is GONE. It existed to make adding
       a tool cost retiring one, and the operator does not want that pressure.
       Removed rather than raised to a number nobody would hit, because a gate
       waived every time is already switched off and section 0 calls
       over-sensitivity a soundness bug: a gate that cries wolf gets ignored,
       including on the day it is right. Its non-empty-denominator half
       survives as `test_the_toolchain_subject_has_not_collapsed`.
     * `_PROSE_ONLY_BASELINE` (184), the pool of tools NOTHING INVOKES, is
       KEPT. That is rot, not accounting -- a tool nothing runs is a tool that
       does not run -- and it is worth knowing whether the suite holds 239
       tools or 2390. `test_unwired_bd_tools_do_not_multiply` is now a standing
       gate rather than open work.

     The item is closed because it was a ratchet with no finish line, which is
     what made it unclosable. ORIGINAL TEXT:

     **The prose-only pool** -- baseline 184, of a population measured at
     238 (v3.66.1029: ls, git ls-files, and test_toolchain_534's
     _TOOL_BUDGET all agree; this line said 240 for releases while all
     three said otherwise -- measure at decision time, per section 1).
     A ratchet, not a target.

ADDED v3.66.944 (34-35), v3.66.951 (36). All were prose notes elsewhere before
they were numbered, and item 34 is here specifically because a prose note is not
a record -- see 15.62's closing paragraph.

 36. **CLOSED at v3.66.958: the MECHANISM is reproduced exactly, the CALLER
     is unrecoverable, and a SIXTH population was found and is already fixed.**

     Reproduced byte-for-byte. One `selector_drift.status_all()` call with
     repo-root cwd and `BD_INSTALL_DIR` unset creates `downloader_history.db`
     at **12288 bytes with exactly one table, `selector_drift`** -- the
     register's own measurement of the stray file. Stack:
     `selector_drift.py:172 status_all` -> `:41 _ensure_table` ->
     `db.py:613 db_conn` -> `db.py:558 _open_history_conn`.

     FIFTH POPULATION RULED OUT, which is what this item said would close it.
     `bd-sweep --selftests` with a `.pth` connect wrapper armed: **175 of 175
     allowlisted tools ran, 0 repo-root connects, no database created.**
     Denominator stated: 238 tools in `bin`, 63 not runnable
     (operational/never-exec), and a `--selftest` need not reach a tool's real
     DB path -- a floor, not a proof. The wrapper was proven in three
     directions first (records a repo connect, silent on tmp and `:memory:`,
     survives `PYTHONPATH` stripping).

     `drift_repair.scheduled_drift_repair` is also out: it checks its toggle
     BEFORE touching the DB, so the daily sweep never reaches `_ensure_table`.

     **THE SIXTH POPULATION IS THE PROVISIONER, AND NOBODY HAD SWEPT IT
     BECAUSE IT RUNS BEFORE A SESSION.** Every earlier sweep covered things
     that run INSIDE one. The 2026-08-05 container carried its own repo-root
     `downloader_history.db` -- 217088 bytes, 17 tables, `schema_migrations`
     at 9 rows, with a `.premigration.bak` written 26ms earlier by
     `migrations.py:214` -- timestamped `21:52:45`, inside `cloud-setup.sh`'s
     window (`21:49:21` -> `21:53:08`), on a snapshot based at **v3.66.883,
     before @926 removed the module-scope writers**. Re-measured at HEAD, the
     provisioner's own version probe creates NOTHING with the keepalive flag
     set and unset alike: @926's fix holds. Recorded as a closed finding.

     **WHAT CANNOT BE ESTABLISHED, and why the item closes anyway.** The
     2026-08-08 17:13:40 file's caller died with its container. CLAUDE.md
     section 5's ad-hoc-probe warning is now STRONGLY supported -- any bare
     `python -c` importing the module from the repo root does exactly this --
     but supported is not proven and it is not upgraded to a finding. Held
     open, it could never close: a probe run outside the suite is unobservable
     after the fact.

     **NO GUARD WAS BUILT, DELIBERATELY.** The obvious fix -- refuse a DB path
     resolving inside the repo -- BREAKS THE BOX, which runs the service from
     its own checkout at `/home/mboyle/BulkDownloader`, where "inside the repo"
     and "the install dir" are the same directory. It would fire on production
     and be switched off: section 0's over-sensitivity failure, shipped on
     purpose. The residual risk is a discipline issue, not a code defect.

     ORIGINAL TEXT: "An unattributed writer put a database in the repo root" -- measured
     2026-08-08 17:13:40, `downloader_history.db`, 12288 bytes, containing ONE
     table: `selector_drift`. Gitignored (`.gitignore:20`), so nothing warned.
     It is item 11's operator-visible harm recurring hours AFTER @942 shipped,
     which is why it is numbered rather than waved off.

     **FOUR CANDIDATES RULED OUT BY MEASUREMENT, so nobody re-walks them.** Each
     used a `sqlite3.connect` wrapper proven in both directions first (records a
     relative connect from the repo root; silent on tmp and `:memory:`), with the
     plugin's load confirmed in every process:

     | swept | result |
     | --- | --- |
     | full suite, parallel (`-n 4 --dist loadfile`), 14956 passed | 0 |
     | full 113-file band, SERIAL, one process, 1460 passed | 0 |
     | `tools/gui_parity_inventory.py` | 0 |
     | full `bd-regen-order` chain | 0 |
     | bd-freshcheck, bd-guardcheck, check_requirements, bd-kb-sync, bd-env-report-check | 0 |

     Both configurations were run deliberately: @942's mechanism is a background
     thread firing after a test restores cwd, and worker lifetimes differ
     between serial and `--dist loadfile`. Reporting only the parallel run would
     have been a denominator excluding the shape most likely to reproduce.

     **STILL UNATTRIBUTED.** `selector_drift` reaches the database through
     `_db.db_conn()`, so it is the same `_resolve_db_path()` path -- the caller
     is what is unknown, not the mechanism. CLAUDE.md section 5 warns that an
     ad-hoc probe importing `bulk_downloader` from the repo root does exactly
     this, and several were run that day; that is the most probable explanation
     and there is NO evidence for it, so it is recorded as a hypothesis and not
     as a finding. Unknown is a third state.

     **WHAT WOULD CLOSE IT:** sweep the `toolchain/bin` population the way the
     suite was swept. A `.pth` in the venv's site-packages installs a connect
     wrapper into EVERY interpreter regardless of PYTHONPATH stripping, which is
     the only injection that survives the tools that strip it (`bd-regen-order`
     does, for documented reasons). Evidence preserved at `/tmp/stray_1713.db`
     for this session only -- the container is ephemeral, so re-derive rather
     than expecting the file.

 37. **CLOSED at v3.66.955.** The register-promise gate -- a finding is a numbered item in this
     inventory or it does not exist. Named in 15.68's open set WITHOUT a
     number, which is the exact prose form 15.62 said must stop; numbered here
     because the gate's own direction A rejects an unnumbered promise, and
     excusing its own item would have been the first thing it certified
     falsely. CLOSED at v3.66.955 --
     `tests/test_register_promises_resolve.py`, both directions.

 38. **CLOSED at v3.66.961.** The 20 carriers were THREE subjects, not one,
     separated by reading what each zip line does: 11 files where the zip IS
     the tool (retired), 7 live tools carrying a dead glob (branch deleted,
     tool kept), and the live release stage (re-pointed, pin moved with it).
     **Class B contained bd-guardcheck and bd-band-derive, which CLAUDE.md
     sections 2 and 4 MANDATE** -- retiring on the item's original wording
     would have deleted them. REACHABILITY COULD NOT ANSWER THE QUESTION: only
     4 of 245 tools are reachable from any lane, because the toolchain is
     operator-invoked by design, so a lane scan structurally excludes its real
     callers. bd-coretest's test_handoff/test_zipcheck probes reached two of
     the retired tools through `os.path.join(BIN, ...)`, invisible to any
     import graph, and moved in the same cut; _TOOL_BUDGET went 240 -> 235.
     `tests/test_zip_era_tools_stay_retired.py` guards the eleven, because
     item 16 is what happens when a retirement has no gate. ORIGINAL TEXT: The
     zip-era retirement -- 20 tracked files whose executable
     `/home/claude` references describe the zip install workflow the git
     deploy abolished (CLAUDE.md section 7). `bd-install` unzips
     `BulkDownloader_v*.zip` and `rm -rf`s a work tree; `bd-status`
     health-checks nine sandbox paths; `install_bulkdl_kits.sh` alone carries
     **92 executable lines**, a quarter of the whole population. None of
     `bd-install`, `bd-status`, `bd-boot` appears in `ci.yml`, `capture.sh` or
     `scripts/*.sh`. Together they are ~45% of the executable `/home/claude`
     population. RETIRE THE FILES, do not rewrite their paths -- rewriting
     yields a working installer for an abolished workflow. Split out of item 3
     on operator decision at v3.66.954 because it is a retirement call at item
     33's scale, not a path fix. PRECONDITION: a per-tool reachability
     measurement first; "appears in no lane" is not "nothing calls it".

 39. **CLOSED at v3.66.962.** All twenty retired. The precondition -- a
     per-file inbound-citation check -- found essentially no real consumers:
     of the direct path citations, 14 were entries in @952's own tier-0
     allowlist, 2 in test_desandbox's carrier list, 4 were prose in stale
     pending-specs, and the two REAL ones were @953's bdenv test (which
     asserted both copies) and a gitleaks baseline entry. **Nothing imported
     or executed a project-knowledge copy.** The gitleaks entry was dropped
     only after verifying the TWIN is baselined for the same rule and line --
     dropping it otherwise would have armed a real finding. Every coupling
     fired on deletion exactly as scoped and none was a surprise, which is
     what the scoping pass bought. `_KNOWN_DUPLICATES` is empty and its
     staleness test retired with it, per that test's own instruction; the
     forbidding assertion stays, because it still stops a NEW duplicate.
     project-knowledge/ now holds 16 executables, none of them a copy.
     ORIGINAL TEXT: The twenty byte-identical project-knowledge duplicates -- frozen in
     `_KNOWN_DUPLICATES` at v3.66.954 so no NEW one can appear, and the list
     may only shrink. They became visible only when that cut widened the
     gate's denominator past `toolchain/bin/*` and its predicate past
     basename-matching. Retiring them is cheap per file but each needs its
     inbound-citation check first, which is what `bd-scan.py` needed. The
     baseline is machine-readable, so this item is a plan rather than a
     finding at risk of being lost.

 40. **CLOSED at v3.66.963, and the cause was not the one the item named.**
     The write is gone: a bare import now seeds `path_allowlist` in memory and
     `boot_once()` persists it. What made this two attempts is that deferring
     the write BROKE API TOKEN AUTH -- 403 -> 401 on
     `test_no_mintable_scope_can_reach_an_admin_route` -- and the cause was
     not WHEN the write happened but WHICH WRITER did it:

     `global_config.set_config()` and app.py's `_save_app_config()` write the
     SAME `app_config.json`. `_save_app_config()` writes app.py's in-memory
     `_app_cfg` WHOLESALE -- a snapshot taken at import -- so any key another
     writer persisted since is ERASED. Measured: set_config writes
     `api_auth_token_secret`, `_save_app_config()` runs, the key is gone from
     disk, the next `_signing_secret()` mints a fresh secret, and every
     already-issued token fails verification. The fix routes the deferred seed
     through `set_config()`, which does a read-modify-write.

     The security property is untouched: `_validate_path()` reads the
     IN-MEMORY allowlist, so the v3.47.8 (#80) narrowing applies from the seed
     line onward regardless of persistence -- asserted in both directions.
     **The lost-update hazard in `_save_app_config()` predates this cut and is
     item 41**; deferring the seed only made it reachable.

     THE PROCESS LESSON, because it nearly shipped twice: an earlier draft
     bound the drain's logger to a name unbound in `boot_once()`'s scope and
     BOTH tests passed -- neither called `boot_once()`. Deferring work into a
     function nothing in the band invokes is how a fix ships broken. There is
     now a test that calls it. ORIGINAL TEXT: A bare `import
     bulk_downloader.app` still writes three files to the
     cwd** -- `app_config.json` (443 b), `logs/bulk_downloader.log` (177 b) and,
     with `BD_DISABLE_KEEPALIVE` unset, `state/heartbeat.json` (94 b): 714
     bytes total, measured at v3.66.960 in a tmp cwd with `BD_INSTALL_DIR`
     unset. Split out of item 11 rather than folded into its closure, because
     it is a DIFFERENT and much smaller class: none of it is database residue,
     none of it can corrupt operator history, and item 11's title was the
     `.db-wal` writer. **Whether these three are deliberate is UNKNOWN and was
     not investigated** -- config bootstrap and log setup on import are
     plausibly intended, and `state/heartbeat.json` appears only when the
     keepalive thread runs. Numbered so the measurement is not lost, not
     because it is established as a defect.

 41. **CLOSED at v3.66.964, and it was TWO defects, not one.** Reading both
     writers side by side showed `set_config()` chmods **0600 before the
     rename** (F-COREBD11-01, because the file may hold tokens) and
     `_save_app_config()` did not. So the same function lost BOTH things
     set_config was careful about. Measured before the fix:

         after set_config      : mode 0o600  secret=SENTINEL
         after _save_app_config: mode 0o644  secret=None

     The key loss is LIVE: any path calling `_save_app_config()` after a token
     is minted drops `api_auth_token_secret` and invalidates every issued API
     token -- the 403 -> 401 that took item 40 two attempts. The mode loss is
     the same shape: a security property one writer establishes and the other
     silently undoes, leaving a signing secret world-readable on a multi-user
     host. Fixed together: read-modify-write with DISK FIRST and `_app_cfg`
     overlaid on top -- set_config's own merge direction, so the two writers
     finally agree -- plus the 0600.

     **THE BATTERY CAUGHT MY OWN TEST.** The merge-direction mutant escaped
     first time because the probe set a key that was NOT on disk: both merge
     directions then produce the same answer and the assertion cannot see the
     difference. Making disk and memory disagree on the SAME key closed it.
     3 mutants, 3 caught. ORIGINAL TEXT: `_save_app_config()` is a lost update against every other
     `app_config.json` writer.** It writes app.py's in-memory `_app_cfg`
     WHOLESALE, and that dict is a snapshot from import;
     `global_config.set_config()` does a read-modify-write on the same file.
     Any key set_config persisted after app.py's import -- notably
     `api_auth_token_secret` from `api_tokens._signing_secret()` -- is erased
     the next time `_save_app_config()` runs. MEASURED at v3.66.963:
     set_config writes a sentinel, `_save_app_config()` runs, the sentinel is
     gone. Predates item 40, which merely made it reachable and is why that
     item took two attempts. **Latent, not theoretical**: any live path that
     calls `_save_app_config()` after a token is minted drops the signing
     secret and invalidates every issued token. NOT fixed here -- widening
     item 40 into a second subject is the mis-filing item 12 is a monument to.
     The shape of a fix is a read-modify-write in `_save_app_config()` itself,
     or routing it through `set_config()`.

 45. **CLOSED at v3.66.979 -- a regression I shipped at @977, caught on the
     BOX, and the band derivation is what failed.**

     Two tests in `test_v3_66_661_healthcheck_ytdlp_shape` failed on the box,
     and the message named the real defect rather than the assertion:

         assert '90' in 'yt-dlp 2025.01.01 is behind 2026.7.4 - update available'

     **That `2026.7.4` came from the LIVE PyPI index, during a unit test.** @977
     made `_check_ytdlp()` call `latest_version()`, which fetched; every
     existing test that mocked only `status_dict` silently got live data, and
     the suite acquired a network dependency nobody asked for.

     **MEASURED, and it is what should have stopped me: no other probe in
     `healthcheck.py` touches the network.** ffmpeg, chromium, loopback and disk
     are all local. The boot selftest was network-free by design and @977 broke
     that invariant.

     **HOW IT ESCAPED THE BAND, which is the part worth keeping.** I derived the
     band with `ls tests/ | grep -iE "healthcheck|selftest|ytdlp|doctor" |
     head -8`. There are **15** matches. `test_v3_66_661_healthcheck_ytdlp_shape`
     -- the one file named for the function I was changing -- sorts **tenth**.
     A `head -8` truncated my own denominator, in a band derivation, in a
     session whose recurring subject is denominators. The band ran 32 files and
     237 tests and was green over a set that structurally excluded the subject.
     Section 4 says bd-band-derive's output is a floor; it says nothing about
     the floor being silently cut off by a display limit I added myself.

     FIX: `latest_version()` never fetches unless explicitly asked, refuses
     outright under `BD_TEST_MODE` (so the suite is hermetic even if a future
     caller forgets), and persists to a `BD_HOME`-anchored cache so a value
     fetched by the update path is still there at the next boot. The probe reads
     cache-only: cold cache is UNKNOWN, a warm one gives a real answer with no
     network. `maybe_update()` refreshes the cache, since that path already
     reached the network.

     `test_v3_66_661`'s two cases pinned the OLD age-based contract and were
     updated rather than deleted -- the contract change is the point of @977 --
     and both now pin `latest_version` explicitly, because leaving it unmocked
     is precisely what reached the live index.

 44. **CLOSED at v3.66.983 (see 15.72) -- bd-wacz-corpus met the real corpus and
     was wrong, twice.** @973's tool was validated only against synthetic fixtures and the
     Drive corpus's `t_<hex>_<name>` naming. The box's 1251-file / 4.04 GB
     corpus uses neither, and the mismatch was not cosmetic.

     MEASURED from the operator's full file list:

     | | |
     | --- | ---: |
     | files / bytes | 1251 / 4.04 GB |
     | raw (no marker) | 538 |
     | `.redacted` | 601 |
     | `.scrubbed` | 176 |
     | `.redacted.scrubbed` | 46 |
     | carrying a copy suffix | 216 |

     **`_is_redacted` tested `endswith(".redacted.wacz")`, so 197 of the 601
     redacted files -- 33% -- were classified as RAW captures**, because
     `x.redacted (2).wacz` ends in `(2).wacz`. Copy suffixes appear in two forms
     and ten variants: `(2)` x143, `(dup1)` x40, `(3)` x19, `(1)` x4, `(dup2)`
     x3, `(4)` x3, singletons to `(8)`. And `.scrubbed` was a whole population
     the tool had no concept of.

     On 25 real names the tool reported `sites=22 merge_candidates=0` and
     `STATUS OK` -- a clean-looking answer over a denominator it could not
     parse, which is section 0 inside the tool written to apply section 0.
     **Synthetic fixtures could not have caught this**; only the real names did.

     FIXED at @978: classifier rewritten over all three markers and both copy-
     suffix forms. Re-run over the real 1251 names: 713 derivatives (was 404),
     538 raw, 545 source bases, **392 families** (was 0 merge candidates), 647
     derivatives with a raw source present, 66 orphaned. The 538 is a genuine
     cross-check -- an independent grep predicate reached the same number.

     **`--dupes` added, and it reports TWO things that must never be merged.**
     Size-collision stage one, sha256 only within colliding groups: measured 429
     groups covering 1000 of 1251 files and **1.73 GB reclaimable (43% of the
     corpus)**, of which 420 groups / 1.71 GB are >1MB where a coincidental
     byte-count match is implausible. Only 0.55 GB is explained by copy
     suffixes, so most duplication is the same content under different names --
     which no filename-based dedup would find.

     The second output is the one that matters more: **a derivative
     byte-identical to its SOURCE is a no-op redaction, not reclaimable disk.**
     @971 established that path could pass binary members through untouched, so
     counting it as a saving would invite deleting the evidence that redaction
     never happened. It is a FINDING (exit 1) and is excluded from the
     reclaimable total. `noop_derivatives` counts derivative FILES, not groups:
     one source with three identical derivatives is three failed scrubs.

     **HOST GROUPING SHIPPED at v3.66.981 (`--hosts`); the item stays OPEN on a
     BOX MEASUREMENT.** Three tiers, filename-first with the archive as the
     authority, every group labelled with the method that produced it. Tier 1 is
     `dom_analyzer._parse_capture_host` IMPORTED, not copied -- and run against
     `_base()`, not the raw stem: MEASURED here, the parser anchors on a
     date-ish tail and a derivative marker or copy suffix sits exactly where
     that tail has to be, so `<name>.redacted (2)` returns None while `_base()`
     of it returns the host. Two of six real-shaped names resolve only after the
     strip. Tier 2 is `pages/pages.jsonl`; measured on a BD-built archive, the
     redactor scrubs the query and leaves the netloc, so a `.redacted` capture
     still answers. Tier 3 groups under the stem, keyed APART from the resolved
     hosts so a stem that looks like a host cannot be laundered into a measured
     group. Battery: 8 mutants, 8 caught.

     Three deviations from the sketch, each with a reason: `hostname` rather
     than `netloc`, because netloc keeps `user:pass@` and `:port` -- one site
     behind a port would form a second group and the printed label would become
     a place a credential lives; merge candidacy counts distinct SOURCES rather
     than files, because a raw capture and its `.redacted` twin are one capture
     in two forms; and only an archive that could not be OPENED is a finding,
     because firing on the hundreds of site-signal-free names the mode exists to
     describe is the over-sensitivity failure section 0 calls a soundness bug.

     **THE BOX MEASUREMENT, 2026-08-09, `/home/mboyle/BulkDownloader/captures`.**
     This is what closes the item: the tool has now met the corpus.

     | | |
     | --- | ---: |
     | examined | 742 |
     | by_method filename / archive / unknown | 39 / 698 / 5 |
     | archives_opened | 703 |
     | hosts resolved | 153 |
     | merge candidates | 79 |
     | unknown_reasons | `{unreadable: 5}` |

     `by_method` sums to 742 and the groups account for all 742 across 158
     buckets -- the non-empty-denominator assertion holding on real data rather
     than on a fixture.

     **THE FORK CAME OUT CLEAN: `no_pages_jsonl` is ZERO.** Every readable
     archive in the corpus carries the page record, so the tier-2b that would
     have read `datapackage.json:mainPageURL` for foreign archives is NOT owed.
     Declining to build it on speculation at @981 was correct, and it is now
     measured rather than argued. The five unknowns are all archives that could
     not be OPENED, which is the correct answer for an unreadable archive, not a
     gap in the tiering.

     **MY PUBLISHED PREDICTION WAS HALF WRONG, AND THE WRONG HALF IS THE
     LESSON.** Before the run I predicted `by_method.filename` would be ~0,
     from measuring `_parse_capture_host` against 19 real names and getting
     0/19. It is **39**. The 19 came off a single alphabetical page of a file
     listing -- the w/x/y tail -- and I read a non-random sample as the corpus.
     Three of the five unreadables are `t_<hex>_*` export-convention names,
     proving that population existed the whole time and my sample simply never
     reached it. The other half of the prediction, `archives_opened ~=
     examined`, was 703 = 742 - 39: true by construction, so it confirmed
     nothing. **A sample from one screen is not a denominator** -- section 1's
     rule, at the level above the code.

     **ONE THING FOR THE OPERATOR, NOT A DEFECT.** `._banb.redacted.wacz` is
     among the five unreadables and the `._` prefix is a macOS AppleDouble
     resource stub, not a capture -- evidence some of the corpus travelled
     through a Mac. It is correctly reported as unreadable; whether such files
     belong in the denominator at all is a call only Matt can make, so nothing
     silently excludes them.

 43. **CLOSED at v3.66.974 -- the WACZ corpus tools.** Operator asked for
     three things off the Drive corpus: close an item, build master templates
     from multi-capture sites, and mine the captures for capture-mechanism
     defects. The third landed first at v3.66.971 (capture_scrub's content-
     sniffing gap). This item tracks the other two.

     **A CONSTRAINT WORTH RECORDING, because it will recur:** the archives
     cannot be pulled into a cloud container. The Drive MCP returns file bytes
     as base64 INTO THE MODEL CONTEXT, and the smallest file in the corpus is
     1.09 MB -- roughly 1.5 M characters. There is no ranged read and the
     container cannot authenticate to Drive directly. So corpus analysis runs
     ON THE BOX, where the 1658 files already live; the container's job is to
     build the tools, not to run them over real data. Metadata-only analysis IS
     possible from here and produced the site/player grouping that shaped these
     tools.

     **SHIPPED at v3.66.973: `bd-wacz-corpus`**, four selectable read-only
     analyses over one denominator (`--group`, `--pairs`, `--health`,
     `--scrub`, `--all`). All four rather than one because the operator asked
     to compare their reliability rather than trust a single answer. Every mode
     states the denominator it counted over, an empty corpus is UNKNOWN (exit
     2) rather than "0 problems", and `--scrub` counts a member it could not
     READ separately from one read and found clean -- the distinction the three
     TEXT_EXT allowlists collapsed at @859 over 228 contaminated files.
     `--scrub` says in its own output that it is a SCREEN, not the canonical
     floor (`capture_artifact_redact.scan_floor_secrets` takes a parsed capture,
     not archive bytes), so a clean screen shortlists rather than clears.
     Battery: 5 mutants, 5 caught, including a deliberate re-introduction of the
     @859 name-based allowlist.

     **A DEFECT THE TESTS CAUGHT IN THE TOOL'S OWN FALLBACK.** For a filename
     not matching `t_<hex>_<name>`, the docstring promised grouping "under
     itself"; the code ran the site regex over the whole stem instead, so every
     such file collapsed into one bucket named `t`. Measured, not reasoned --
     it surfaced as `{'t': 4}` on first run. The fixtures were also wrong
     (4-hex ids where the real corpus uses 16), so the test was judging naming
     the tool will never meet. Both repaired: the code now honours its contract
     and the fixtures are representative.

     **SHIPPED at v3.66.974: `bd-template-merge`**, closing the item's second
     half. Built to the decision below. N captures of one site -> one master template, entries
     FREQUENCY-RANKED with an explicit support count ("4/4 captures" vs "1/4")
     so nothing is silently dropped and a reviewer can see which selectors are
     load-bearing; output written as a draft into `templates/drafts` so the
     existing `normalize_template_draft -> promote_template -> templates/reviewed`
     pipeline handles it unchanged and `gold_merge_guard`
     (`tools/build_template_from_wacz.py:1555`) still applies. Union and
     intersection were both rejected: union cannot tell a one-off from a
     universal, intersection silently drops a site's rarer page shape.
     Measured at @973: no N-way merge exists anywhere in `tools/` -- the only
     merge functions are within-capture (`_merge_supplemental_media`,
     `_merge_supplemental_api`).

     **A MUTANT ESCAPED FIRST, AND THE FIXTURE WAS WHY.** The test proving the
     highest-support value takes the canonical slot fed the MAJORITY value in
     the first draft -- so "rank by support" and "take the first seen" chose the
     same winner and the assertion could not tell them apart. A test whose two
     candidate rules agree on its fixture proves neither, and the battery said
     so: `winner picked by first-seen` came back ESCAPED on a suite that was
     otherwise 6/6. Repaired by feeding the 1-of-3 value FIRST, which makes the
     rules disagree; re-run is 7 caught, 0 escaped. Note which instrument found
     it -- not review, and not the ten passing tests.

 42. **CLOSED at v3.66.968 -- the gate is widened, both citations are fixed,
     and THE CENTRAL CLAIM THIS ITEM WAS FILED WITH WAS WRONG.** Filed one cut
     earlier at v3.66.967, and the wrongness is kept because it is the lesson.

     The real defect was real: `bd-freshcheck`'s anchor regex alternated over
     `py|sh|json|md|txt|yml`, so a `file:line` citation into a `.tsx` or `.ts`
     file was never PARSED. The gate reported `227/227 resolve` while two
     frontend citations in the gated documents sat outside its denominator --
     CLAUDE.md section 0's OPENING example (a tool that does not count
     `.tsx`/`.ts` as source), live in a second tool years after the first.

     **WHAT WAS WRONG: the predicted trap does not exist.** This item asserted
     that widening the extension alone "converts 2 invisible anchors into 2
     FAILURES" because "both citations are written as bare basenames, and the
     resolver checks paths against `git ls-files`, so the correct one would
     fail for its FORM". Measured before touching anything -- widen the regex
     in a loaded copy of the module and re-run `check_anchors` against the
     real tree:

         shipped regex : OK -- 227/227 resolve and are in range
         widened regex : OK -- 229/229 resolve and are in range

     Zero failures. `check_anchors` has resolved bare basenames since it was
     written (`by_base`), and reports AMBIGUOUS rather than guessing when one
     matches several tracked files -- its own docstring records that an early
     draft called 81 of 143 anchors broken for exactly that reason and that
     the fix was basename resolution. **The claim was written by someone who
     had read the regex and not the function.** CLAUDE.md section 1's "read
     the callee before you call it", inside a finding about denominators.

     Both citations were repaired anyway, because one of them needed it for a
     reason the gate cannot see: the entry at this file's line 7284 named line
     **1156** for `RegenNfosResult`, which now begins at **1164**. In range
     either way, so the widened gate passes it -- an anchor gate checks that a
     line EXISTS, never that it says what the sentence claims. That half is
     still a reader's job, exactly as `bd-freshcheck` prints on every run. The
     other citation, at line 858, was re-read and is still exactly right. Both
     are now repo-relative rather than bare basenames, which removes a
     dependence on basename uniqueness that nothing asserts.

     **The basename-ambiguity paragraph, kept for the same reason.** It first
     claimed "many same-named frontend files"; measured, `frontend/src` has
     **zero** duplicate basenames and the repo has **29**. So a basename
     resolver is unambiguous for these citations today and the objection is
     that the property is unowned, not violated. That correction was made
     before the @967 commit; the larger one above was not caught until the fix
     was built. Two confident sentences in one item, both false, both found
     only by running something.

     RED first: `tests/test_v3_66_968_anchor_gate_sees_frontend_citations.py`
     drives `check_anchors` over a fixture repo carrying a tracked `.tsx` and
     both gating documents. Three tests, all proven failing on pristine source
     with *"found ZERO anchors -- the check saw nothing"*. The load-bearing one
     is the out-of-range anchor asserting **STALE specifically, not merely
     "not OK"** -- UNKNOWN also satisfies `!= OK`, and UNKNOWN is precisely
     what the blind gate returns, so the loose comparison would have passed on
     the defect it was written to detect. The over-sensitive direction is
     asserted beside it: a valid frontend anchor must NOT be reported, or a
     "fix" that fails every frontend citation would pass a one-sided test and
     destroy the gate.

 34. **CLOSED at v3.66.945.** Root-caused, fixed, and the four failures are
     gone from the same 114-file band (1462 passed). The title below was wrong
     in every particular and is kept because the wrongness is the lesson: not
     SSRF, not VPN, and "order-dependent" described a symptom.
     `test_v3_66_940_*::test_every_declared_key_can_be_seeded` seeds every
     declared editor key from a `.env` with placeholder values -- BD_INSTALL_DIR
     is index 3, so the value is the string **`"v3"`**, relative --
     `load_envfile()` writes it with `os.environ[k] = v`, monkeypatch never
     RECORDED that write so `undo()` cannot remove it, and it then
     self-propagates because every later monkeypatch undo RESTORES `v3` instead
     of deleting the key. `_resolve_db_path()` joins it onto the next test's tmp
     cwd; the parent does not exist; sqlite3 says `unable to open database
     file`. See 15.63. ORIGINAL TEXT:

     **Four order-dependent band failures, pre-existing** -- webhooks-SSRF x3
     and vpn-quarantine x1, which fail in a multi-file band and pass
     file-at-a-time. PROVEN pre-existing on pristine `8e2b017` with an
     identical 130-suite list during the @943 session, so they are not that
     cut's residue and not a regression. NOT root-caused: the mechanism is
     unknown, and "order-dependent" is a description rather than a diagnosis.
     Container-only reading -- the box's `./capture.sh` runs `--dist loadfile`
     and has been green across both captures, so whatever the interaction is,
     it does not reproduce under the lane the operator actually runs. Start by
     bisecting the band list rather than by reading the four files.

     RE-MEASURED at v3.66.944 on a SECOND, different band (113 suites derived
     from `CLAUDE.md` + the register, not the 130 from @943) -- **4 failed /
     1456 passed / 1 skipped, identical on the cut and on a pristine tree in
     the same directory with `git status --porcelain | wc -l` reading 0.** The
     same two files pass 15/15 in ISOLATION. So the interaction is real,
     reproduces across unrelated band compositions, and is not any one cut's
     residue.

     **AND THE FAILURE MODE IS NOW NAMED, which it was not before:**

         db.py:558  cx = sqlite3.connect(path or _resolve_db_path(), ...)
         sqlite3.OperationalError: unable to open database file
         [webhooks] schema init failed: unable to open database file

     That is a CWD/relative-path failure, not an SSRF or VPN one -- the same
     class as item 11, where `_resolve_db_path()` returns a bare relative name
     when `BD_INSTALL_DIR` is unset and `sqlite3.connect` resolves it against
     whatever cwd exists at call time. A prior test's fixture chdir'ing into a
     `tmp_path` that is then torn down would produce exactly this. **Start
     there**: the four tests are the victims, not the subject, and their names
     have been misdirecting every reading of this item.
 35. **CLOSED at v3.66.947, and the item recorded the symptom without the
     cause.** It was not an oversight: the artifact COULD NOT be wired in.
     Seeding an unchanged tree produced a different FILE every run
     (`d1065b4a405b7e4c -> 2981568f6e6042c5 -> 56e52a5667ce9942`), differing
     only in a wall-clock `generated` field, and CI's check is bd-regen-order
     followed by `git status --porcelain` -- so a chain entry would have failed
     every pull request. **That is the defect CLAUDE.md s0 names in its own
     text**, still live in the artifact whose gate @944 had just written. Fixed
     by preserving `generated` when the `files` mapping is unchanged (attest
     over content, not bytes) rather than removing it, because `bd-boot` reads
     it to decide which of two manifests is FRESHER -- and a reseed over
     identical content used to make a stale KB look newer than a fresh one.
     STATIC_KB is now the chain's LAST step; two consecutive regens leave the
     manifest byte-identical. ORIGINAL TEXT:

     **`STATIC_KB_MANIFEST.json` is not in `bd-regen-order`** -- @944 reseeded
     it and gated its membership, but nothing REGENERATES it, so the same
     staleness recurs the next time a `project-knowledge/` file is added or
     removed; the gate will catch it, one cut late, as a red band rather than
     as an automatic refresh. Adding it to the regen chain is the durable fix
     and was deliberately not folded into @944 (a regen-order change moves
     CI's generated-artifact sync check, which is a different blast radius).
     Note the ordering constraint it would have to respect: the reseed must
     follow the last `project-knowledge/` edit, the same way regen must follow
     the last source edit.

 46. **CLOSED at v3.66.1034.** `CLOAKBROWSER_AUTO_UPDATE=false` is set in
     `pytest_configure`, and it is ASSERTED rather than trusted -- a mutant
     flipping it to "true" escaped the first battery because nothing in the
     band imports cloakbrowser. Measured with the socket recorder armed: unset
     gave 1 packet-sending connect to 151.101.0.223, "false" gave 0. ORIGINAL
     TEXT:

     **The test suite makes live PyPI calls from a dependency's daemon thread.**
     MEASURED at v3.66.1031 by the stage-1 socket recorder, on test5, across a
     full `-n 16` run: five attempts to `151.101.*:443`, from a thread named
     `_check_wrapper_update`. It is `cloakbrowser`'s, not ours -- a daemon
     thread started on import that GETs the package's own PyPI JSON **once per
     process**, so once per xdist worker, landing on whichever test happens to
     be running. Named by function rather than `path:NN` because it lives under
     `site-packages` and `venv/` is untracked, where an anchor can never
     resolve.

     This is @977's class -- a live PyPI call inside unit tests -- surviving in
     a dependency, which is why no gate over OUR tree could ever have seen it,
     and why item 45's fix did not touch it. It also means the suite's result
     depends on pypi.org being reachable, on a box whose whole point is to be
     the gate.

     Opt-out exists and is one variable: `CLOAKBROWSER_AUTO_UPDATE=false`, or
     setting `CLOAKBROWSER_DOWNLOAD_URL`. NOT applied in @1031 on purpose --
     `CLOAKBROWSER_*` names are already in the config-surface inventory
     (`CLOAKBROWSER_BINARY_PATH` is a ledgered unprefixed key), so adding one is
     a `test_gui_parity` question rather than a drive-by. Decide where it goes:
     `tests/conftest.py`, `capture.sh`, or the provisioner.

 47. **CLOSED at v3.66.1034.** The payload is derived from the real cwd via
     `_traversal_payload()` so it escapes at any depth, and an absent
     path-typed flag now SKIPS. Both halves are mutation-covered; the first
     attempt at the depth test recomputed the arithmetic instead of calling it,
     and a mutant pinning the depth to a literal 4 escaped until the derivation
     was extracted into a function the test calls. ORIGINAL TEXT:

     **`test_path_typed_flag_rejects_traversal` is vacuous, and fails on a false
     premise when it is not.** `tests/test_v3_66_717_exec_bridge.py`. It reads
     as a path traversal being ACCEPTED (200, not 400) and it is **not a
     vulnerability** -- `tool_bridge`'s path validation does `realpath` +
     containment and is correct in both directions. MEASURED at v3.66.1031 with
     `_ALLOWED_PATH_ROOTS = ('/home/mboyle/BulkDownloader', '/tmp')`:

         cwd <=3 deep under /tmp -> realpath('../../../../etc/passwd') = /etc/passwd
                                 -> REFUSED, correctly
         cwd >=4 deep under /tmp -> = /tmp/.../etc/passwd, still inside a root
                                 -> ACCEPTED, correctly

     The test hard-codes four `..`, so whether the payload escapes depends on
     the depth of pytest's `tmp_path`, which varies with worker and test name.
     That is the failing half. The other half is worse: the only path-typed
     flag in the allowlist is ffprobe's `input`, and `_build_allowlist` creates
     that entry only when `shutil.which("ffprobe")` resolves -- so where ffprobe
     is absent the loop finds nothing and the test **returns having asserted
     nothing**, green. A security test that passes vacuously is section 0 in a
     test file.

     Fix direction: derive the payload from the actual cwd depth (or use an
     absolute `/etc/passwd`), and assert ffprobe's presence rather than walking
     silently past it -- an explicit skip says "not measured", a vacuous return
     says "refused", and only one of those is true.

 48. **ROOT-CAUSED at v3.66.1034, PARTIALLY fixed, STILL OPEN.**

     THE MECHANISM, measured. 14 tracked test files delete `bulk_downloader.*`
     from `sys.modules` and never restore it. conftest's three session-scoped
     guards each patch an attribute on a module imported ONCE at session start,
     so after any such wipe the next import builds a fresh module and the patch
     is orphaned -- dead for the rest of that worker process, with plugin tests
     then writing into the repository's own `plugins/` directory.

     HOW IT WAS FOUND, because the method is the transferable part. Not by
     reading: by replaying one worker's real 232-file chain through concurrent
     prefix ladders (34 rungs at once on 86 cores, then 16 more). The result was
     a clean monotonic step -- every prefix <=183 ok, every prefix >=184 broken
     -- which named `test_v3_66_1021_log_reinit_replaces.py` exactly. Its own
     `restored_logger` fixture cites CLAUDE.md on state leaking across files
     while leaking `sys.modules` two functions above. Minimal repro: 2 files,
     1.4 seconds, down from a 12-minute nondeterministic suite.

     WHY IT ROTATES. `--dist loadfile` assigns files to workers DYNAMICALLY, so
     which victims land downstream of a leaker changes with timing every run.
     Fewer workers is worse because chains are longer. MACHINE LOAD dominates
     the count -- pre-fix samples were 1-8 on a quiet box and 18-29 under four
     concurrent suites, which is why any single sample is uninterpretable and
     three of them misled this session in three different directions.

     THE FIX, and its limit. The guards now register their patches and are
     re-asserted when the module OBJECT identity changes -- never when the
     attribute merely differs, so a test steering a guarded name is left alone.
     A/B at n=4 per condition, same host, same concurrency: pre-fix
     18/21/29/21, post-fix 5/30/7/19; mean 22.25 -> 15.25 and the distinct
     union 73 -> 56. NOT significant at that spread, and not claimed as such.
     What is decisive is mechanistic: `test_no_test_writes_the_repo_plugins_dir`
     fails in every pre-fix sample and no post-fix one.

     WHY IT STAYS OPEN. Post-fix runs still rotate 5-30, and a controlled
     2-file experiment shows the plugin victims fail after a leaker with AND
     without the fix -- identical sets, 0 unique either way. So the leakers
     damage something BEYOND the three registered guards, and that second
     mechanism is unfound. A ratchet pins the leaker population at 14 so the
     class cannot grow while it is hunted.

     ORIGINAL TEXT:

     **The full suite on test5 does not run clean on pristine `main`, and the
     failing set ROTATES between runs.** Five full runs at v3.66.1031, same
     host, same directory:

         pristine e5cece7  -n 16 -> 13 failed
         @1031             -n 16 -> 1, then 7, then 8   (three runs)
         pristine e5cece7  -n 4  -> 16 failed
         @1031             -n 4  -> 35 failed

     Every file sampled from the "new" failures passes when run together in
     isolation (23 tests, 5 files, green), so these are co-batching artifacts:
     `--dist loadfile` sequences more files per worker as `-n` falls, and adding
     any test file reshuffles the assignment. **Fewer workers is WORSE**, which
     is the opposite of the intuition that parallelism causes flakiness.

     This is not what CLAUDE.md section 5 leads a reader to expect. That section
     records the sanctioned sweep as "14 failed, all the documented
     container-only set, item 34's four order-dependent failures ABSENT --
     @945's fix holding at full denominator". On test5 the documented `-n 4`
     form gives **16 failures on pristine main**, and they are mostly not that
     set (`test_e2e_smoke` passes here; this is the box, not a container).

     What is NOT known, and is the work: how many are genuinely order-dependent
     versus environmental on this host, and whether any is a real defect hiding
     in the rotation. `test_v3_66_717_exec_bridge` was in all five runs and is
     now item 47 -- one member identified, the rest unclassified. Until that
     pass is done, a full-suite run on test5 cannot be read as a gate, which
     matters because the box IS the gate.

### 15.38 | The tier plan -- 15.36's list re-ordered by size and speed

15.36 orders the open items by what unblocks what. This is the OTHER axis --
size and wall-clock -- which is what you want when picking the next CUT rather
than the next dependency. Operator-approved 2026-08-06 and worked top-down:
**tiers 1 and 2 are both COMPLETE**, all eight rows, shipped the same day.

It is written down because it was not. The plan lived only in a conversation
and had to be recovered from the operator's screenshots to be acted on. A plan
that must be screenshotted back to its author was never persistent, and this
register is where that is supposed to stop happening.

TIER 1 -- no cut, or nearly none. ALL DONE at v3.66.887.

| # | item | disposition |
| --- | --- | --- |
| 25 | orphan bundle backup rotation | VERIFIED intact; the copy-into-rotation is the operator's call |
| 24 | three stale >6h lock files | CLOSED OBSOLETE -- the box probe found ONE, inside site-packages |
| 22 | library panel's 31 `missing` rows | CLOSED -- already correct behaviour, no code |

TIER 2 -- small, container-only, well-specified. ALL FIVE DONE.

| # | item | disposition |
| --- | --- | --- |
| 19 | `rev-parse` into `01_sysinfo.log`, plus the selftest stage | CLOSED as TWO cuts, v3.66.892 and v3.66.893 -- see 15.39 |
| 5b+6 | band artifacts untracked AND un-ignored | CLOSED -- both ignore rules already existed; v3.66.886's "re-confirmation" is retracted at 887 |
| 10 | `ai_boot_readiness.json` in-flight marker | CLOSED -- fixed at v3.66.874, verified this session |
| 13 | `bd-state` reachable only via `build_session_pack` | CLOSED at v3.66.888 |
| 20 | widen the import-graph gate to `tests/` edges | CLOSED at v3.66.889 |

TIER 3 -- moderate, well-specified. **ALL SIX DONE at v3.66.913.**
5a+7 (7 at @898), 18 (@896), 9 (@872), 26 (@845), 8 (pinned at @912, 15.44),
15 (fixed @836, found already pinned -- 15.45). TIERS 1, 2 AND 3 ARE COMPLETE.

TIER 4 -- large. All open: **4, 14, 28, 11, 12, 16**.

TIER 5 -- cannot be sized from here, or blocked: **1, 2, 3** (operator) *
**27** (a product decision) * **17** (needs an observation that has not
occurred) * **21** (box object store only, unreachable from a container) *
**23** (a capture run) * **31, 32** (the parallel program -- very large,
operator- and capture-bound) * **33** (a ratchet, never "done").

**Tier 3 and tier 4 statuses are INHERITED, not re-measured.** Only what this
session touched was verified. Section 1 applies before working any of them --
several of 15.36's own statuses were stale within days, and four of them were
wrong by the end of the session that wrote them.

THE TWO RECOMMENDATIONS, with this session's closures folded in.

  * **Throughput: `8 -> 5a+7`.** As first written the chain was
    `8 -> 5a+7 -> 5b+6 -> 10` -- four cuts closing six numbered items. Half of
    it closed the same day (5b+6 by ignore rules that already existed, 10 at
    v3.66.874), so it is now **two cuts closing three items** (8, 5a, 7), still
    container-only, still no capture needed, still no operator decision. **Do
    not inherit the four-cut form**; correcting it is why this section exists.
  * **Impact: `4` alone.** The only item that unblocks what CLAUDE.md section 4
    mandates on every future cut, and the only one whose acceptance is a single
    number measurable in advance -- 18 of 93 suites cannot run under `bd-band`
    today, target 0. The `shell_source` group is the sharpest of its three root
    causes: that helper landed at @880, so the suites guarding the three most
    recent cuts are exactly the ones `bd-band` cannot run.

THE CAVEAT, and it OVERRIDES size. **Hold 15 and 14 back regardless of how
small they look.** Both change live box behaviour -- a service startup path and
a login thread -- and a small diff on either is not a cheap one. Neither is
bounded by its line count, and neither can be judged from a container.

### 15.76 | The queue after v3.66.989, in order

Recorded 2026-08-09 at `bf3b1b2`. Items 1-3 were the operator's standing
program; item 4 was added after a measured incident described below. Item 0 was
MISSING FROM THE FIRST DRAFT OF THIS SECTION and the operator caught it -- it is
recorded as a FINDING at 15.74 and 15.75, and was absent from the QUEUE, which is
the part a future session reads to know what to do. A finding that is not in the
queue is an item that quietly stops existing.

**0. A -- cross-host grouping. BLOCKED ON A MEASUREMENT, not on design.**
`--hosts` and `--templates` group by exact hostname, so `auth.X` and `app.X` are
two "sites". Five of the seven sites the operator screenshotted span a login host
and a content host, so this inflates the site count, produces green verdicts for
login halves, and hides the pairing that matters.

THE RUNTIME ALREADY SUPPORTS WHAT IS NEEDED AND NOTHING WRITES IT.
`template_registry` matches on `match.hosts` aliases AND on
`match.sibling_domain` -- probe-verified: `auth.wowgirls.com` resolves to a
`venus.wowgirls.com` template, and an unrelated `auth.bangbros.com` is correctly
refused. `sibling_domain` appears in exactly ONE file repo-wide, the matcher.
The capability is real and unreachable.

DO NOT KEY ON eTLD+1. The in-repo helper is last-two-labels, and BOTH failing
hosts are already in the corpus: `www.bbc.co.uk` becomes a site called `co.uk`,
and every App Engine tenant merges into `appspot.com`
(`shaka-player-demo.appspot.com`). `app_secrets.py` already records abandoning
that helper for exactly this reason.

THE DESIGN THE EVIDENCE SUPPORTS: keep exact-host grouping as ground truth and
never silently re-key; ADD cross-host candidates, LABELLED, the same discipline
`--hosts` already applies by naming each group's resolution method; and DERIVE
the pairing from evidence rather than from the name. BD's capture convention is
`{host}_{siteid}_{YYYYMMDD}`, so `auth.reptyle.com_0b60f1ec_...` and
`app.reptyle.com_0b60f1ec_...` would share a siteid -- the operator's OWN
grouping, recorded at capture time, which sidesteps `co.uk` and `appspot.com`
with no domain guessing and no PSL.

THE ONE COMMAND THAT UNBLOCKS IT, still unrun:

```bash
find ~/BulkDownloader/captures -name '*_*_2*.wacz' -printf '%f\\n' \\
  | sed -E 's/^([^_]+)_([0-9a-f]{6,})_.*/\\2 \\1/' | sort -u \\
  | awk '{a[$1]=a[$1]" "$2} END{for(k in a) print k, a[k]}' | awk 'NF>2'
```

Rows mean siteid pairs hosts and cut A is evidence-based. NO rows means fall
back to eTLD+1-as-CANDIDATE with a public-suffix denylist, and say so in the
output rather than presenting a guess as a derivation.

**1. C -- `text=/Download/i` can go green on a heading.** DIAGNOSED, not built.
`tools/build_template_from_wacz.py` emits the hint from
`elif _has(r'>\s*Download\s*<', html)`, which matches ANY element whose text is
"Download" -- `<h3>Download</h3>` included -- and the emitted selector is
unscoped, so at runtime it still resolves to the heading even when a real button
exists on the page. Measured on a VIP4K reconstruction: promotion_ready True on
a trigger that clicks a title.

Fix shape, and both halves are needed: gate emission on an INTERACTIVE element
carrying the text, AND emit a scoped hint rather than a bare one. Measured, the
linter accepts `a:has-text("Download")`, `button:has-text("Download")` and the
comma form for both `trigger` and `row` roles -- blocking False, zero issues --
so the scoped form is available. Cost to state honestly: a site whose download
control is a `<div>`/`<span>` with a click handler loses its hint and reads
not_green, which is more honest than a false green but IS a loss.

**2. D -- the repo's own honeypot screen is never called for download
selectors.** `bulk_downloader/template_extractor_impl/login_extract.py:116-140`
(`_login_is_honeypot`) checks inline style / tabindex / aria-hidden / parent
styles on a DOM tag, and the template path never uses it for the download side.
It cannot live in `template_normalize`, which sees only strings; it has to go
where the DOM still exists -- `template_extractor_impl/candidates.py:501-505`
emits `row_selectors` with zero visibility screening. State its limit when
built: it sees INLINE hiding only, so class- or stylesheet-hidden decoys stay
invisible to it.

D got sharper at v3.66.989. `app.reptyle.com` -- the site the operator
CONFIRMED has a real Standard/High/Ultra quality modal -- ships
`rows in template: []`. What it still drops is
`span.theo-primary-color.theo-settings-control...` at 24/62, a THEOplayer
settings control correctly refused. Its actual modal buttons were never
extracted as row selectors at all, so this is an EXTRACTOR gap rather than a
normalizer one, and step 4 stays unmodelled on the operator's clearest example.

**3. E -- no post-login interstitial step is modelled.** The "No Thanks.
Continue to Members Area" shape sits between login and content and nothing in
the template schema represents it.

**4. `capture.sh` cannot tell a polluted run from a real one.** ADDED after an
incident on 2026-08-09 that cost a whole capture, and the shape is section 0's:

    89 failed, 12744 passed     (BD_INSTALL_DIR exported in the shell)
    12833 passed, exit 0        (same tree, same commit, variable unset)
    12744 + 89 == 12833         -- every failure was the variable, nothing else

An agent's probe instructions said `export BD_INSTALL_DIR="$(mktemp -d)"`, the
operator ran it in the interactive shell, and `./capture.sh` inherited it. Every
test that does not isolate its own database then resolved to ONE shared tmpdir
while ~88 xdist workers created and populated it concurrently. Signature:
`sqlite3.OperationalError: database is locked` x13, a UNIQUE constraint
violation on `tags.name`, counts inflated by a constant (`assert 10 == 1`), and
`test_selector_health_empty_clean_install` / `test_edge_selector_health_no_sites`
failing because another worker had seeded the database they assert is empty.

Every one of those 89 failures was TRUE about the database it examined, and
useless -- a run reporting faithfully over a denominator that was not the
subject. `capture.sh` reported it as a normal red.

Proposed guard: refuse at the top of `capture.sh` when `BD_INSTALL_DIR` is set
and does not resolve to the repo root. Safe on the box SPECIFICALLY because
there the install dir IS the repo, so any other value is always wrong for a
capture -- this is not a gate that will fire in production and get switched off.
Needs operator authorization (runtime/build change); granted 2026-08-09, queued
behind C/D/E.

The probe form that does not leak, for whoever writes the next set of
instructions:

```bash
BD_INSTALL_DIR="$(mktemp -d)" venv/bin/python - <<'PY'   # scoped to one command
```

never `export` in a shell the suite is later launched from.

**Review followups not in the main queue, recorded so they are not lost.**

- The affordance audit trail is UNREACHABLE. `_map_selectors` records
  "kept row selector by download affordance" in the draft's `warnings`, and no
  reviewer surface displays it: `template_manager._describe` returns
  `lint_warnings` only, the SPA renders lint only, and
  `tools/normalize_template_draft.py:47` prints a COUNT rather than the text.
  That warning is the stated payment for the honeypot resistance @989 trades
  away, so this is the followup that matters most.
- `selector_lint._SCOPED_RE` (`:49-51`) counts `download` as SCOPING, so naming
  a nav decoy `a.navbar-item.download-app` switches the chrome linter OFF at the
  same moment @989's rule switches ON. Measured; predates @989; needs its own
  corpus pass.
- A pre-@988 template already on disk carrying a stringified selector is
  detected and repaired by NOTHING -- measured, it promotes cleanly
  (`promote_gate_errors` [], lint []). @988 fixed the producer only.
- `gate_selector_blocked.by_cause.other` is 40 of 81 at v3.66.989, the largest
  bucket: sites whose raw download leaves SURVIVED normalize but feed no gate
  clause. The rollup's two named causes account for less than half of it.
- `www.bbc.co.uk` is the corpus's only `single_witness` -- green on a `.drawer`
  row seen in 1 of 6 captures. Re-capture before trusting it.


### 15.95 | SESSION CLOSE 2026-08-13 at 283588d (v3.66.1111) -- four cuts, the backlog's last blocked row closes, and a defect thought fixed turns out to be alive on ONE HOST

Close at `283588d`, already on `main` when this was written. Named per the @939
trap: a section naming its own branch tip goes red on `main` after the squash
destroys it, where no band reaches.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31

#### WHAT SHIPPED: v3.66.1108 - v3.66.1111

    1108  no assertion may be FALSE for every input (backlog 26, slice 2)
    1109  backlog 27: both mechanizations measured and REFUSED, with evidence
    1110  backlog 13 CLOSES -- the agent key is scoped, the broad key retired
    1111  a wedged capture stage is bounded and named (backlog 102)

Backlog 4 OPEN -> 3 (26, 27, 102), plus a NEW row 104. All three remaining are
open deliberately: 26's row sits above the slice it shipped, 27 carries the
refusal, 102 narrowed from "no mechanism" to one question.

BOX GATE RECORDED WITH IT: all four hosts PASS at 283588d.

    test5  unit 15894 pass / 0 fail / 0 error / 6 skip   live 36 pass / 0 warn
    test4  unit 15894 pass / 0 fail / 0 error / 6 skip   live 36 pass / 0 warn
    test6  unit 15893 pass / 0 fail / 0 error / 7 skip   live 32 pass / 4 warn
    test7  unit 15894 pass / 0 fail / 0 error / 6 skip   live 30 pass / 6 warn

Live WARNs are informational and do not fail the verdict -- they mean a
capability was not exercisable (no tunnels configured, AI assist off), not that
one is broken. All four exited 0. The unit count moved 15869 -> 15894 with this
session's new test files; test6's 15893/7-skip is one test skipped where the
others ran it, which is the ordinary skip variance this fleet shows and not a
failure.

#### THE FINDING WITH THE LONGEST REACH: THE WEDGE IS ALIVE, AND IT IS ONE HOST

Backlog 102's wedge was last seen at f154aef and the hope was that @1095's
eviction guard or @1100's httpx pre-import had killed it. **It reproduces at
HEAD.** Caught three times: `[gwNN] node down: Not properly terminated` at 99%,
then 726 seconds of silence at a 1-minute load average of 0.27.

py-spy under sudo, taken BEFORE anything was killed, settles the mechanism.
**It is a LIVELOCK, not a deadlock.** xdist's `loop_once` is `while 1:` around
`queue.get(timeout=2.0)` and leaves that loop ONLY when `self._active_nodes`
empties; the `--locals` dump caught it mid-spin with `remaining: 1.9999989`.
48 receiver threads sat idle in execnet `read` and ONE zombie child hung unreaped
off the master. Left alone it would still be running.

**AND EVERY WEDGE IS ON test6.** Full-suite arm only, which is the only arm that
has ever wedged: test6 2 of 6, test4 0 of 10, test7 0 of 10 -- one-sided Fisher
p = 0.046. A host audit found the software stack byte-identical (kernel
6.8.0-137, Ubuntu 24.04.4, Python 3.12.3, pytest 9.1.1, xdist 3.8.0, execnet
2.1.2, same commit) and exactly ONE structural difference: **test6's root is
EXT4 where test4 and test7 are XFS**, and /tmp is on / everywhere. CPU differs
three ways and so isolates nothing. The historical 2-of-6 was ALSO on test6.

A within-host control is running as this is written: `full-tmpfs` is the same
command with TMPDIR on a 64G tmpfs, alternating against plain `full` on test6,
so the filesystem is the only difference between two arms on one machine. Read
`~/bd-wedge-2026-08-14/rows.jsonl` for the verdict -- and do NOT pool it with
`rows-phase1-final.jsonl`, which is a different commit and a different arm mix.

#### WHAT THE ARMS RULED OUT, WHICH IS WORTH AS MUCH AS WHAT THEY FOUND

The capture lane is 0 of 25, and the 800 deselected tests run ALONE are 0 of 24.
So "a leaky test among the 800" does not reproduce, and capture.sh's
configuration remains the measured-good one. **NO CHANGE TO --workers is
warranted and none was made.**

#### THREE INSTRUMENTS I BUILT WERE WRONG BEFORE THEY WERE RIGHT

  * **A PROBE INVENTED SEVEN HAZARDS OUT OF ITS OWN DENOMINATOR.** Hunting
    fd inheritance, it collected EVERY pipe on a worker's fds -- including pipes
    the worker made for its own children -- so a multiprocessing
    `resource_tracker` holding its own parent pipe scored as a third-party
    holder. An execnet channel is a pipe held by BOTH a worker and its master.
    Counted correctly: 96 channels per host, **zero** third-party holders across
    **976 samples** on three hosts. The hypothesis is dead and it was my probe
    that made it look alive.

    The same watcher retired a second hypothesis. Peak ZOMBIE counts are
    comparable on all three -- 29 on test4, 33 on test6, 28 on test7 -- so
    zombie churn is normal at `-n 48` and is NOT what distinguishes the host
    that wedges. The unreaped zombie sitting under a wedged master is therefore
    a CONSEQUENCE of the worker dying, not a cause of it.
  * **THE FIRST ROW-26 GATE WAS BLIND.** Folding only WHOLE assert tests it
    could decide **1 of 32127** assertions -- 0.003% -- because @1098's live
    instance was a CLAUSE inside a larger expression. It would have reported
    clean over a subject it structurally could not see. Measuring the
    denominator is what caught it; the green result did not.
  * **MY SIGINT-BEFORE-SIGKILL "IMPROVEMENT" WAS INERT.** Added so a wedged
    master would flush its block-buffered stdout, it changed nothing --
    `SigIgn: 0x1001007` on the master, SIGINT ignored, because a process
    backgrounded with `&` from a NON-INTERACTIVE shell inherits SIGINT and
    SIGQUIT as SIG_IGN. CLAUDE.md section 6 records that exact trap for
    bd-mutate. Fix is `trap - INT QUIT` in the child before exec; NOT yet
    applied, because applying it costs the in-flight tmpfs samples.

#### BACKLOG 13 CLOSES, AND BOTH DIRECTIONS WERE PROVEN

Final state on test4/test6/test7: exactly TWO keys -- `mboyle-laptop`
unrestricted, and the agent key carrying `from="10.0.70.164",restrict,pty`.
`pty` is added BACK deliberately: `restrict` alone disables it and interactive
sessions are how the fleet is driven.

Proven rather than asserted from the file: the agent key authenticates on a NEW
connection with the control master bypassed; pointing `from=` at 192.0.2.1 makes
sshd REFUSE that same key and restoring it makes it work again, so the clause is
ENFORCED; a remote forward fails at setup and a local forward carries no traffic
while commands and pty still succeed; and the retired `mboyle@test4` key is
refused on all three.

**A METHOD TRAP COST A WRONG READING FIRST.** A command-line `-i` ADDS to the
config's `IdentityFile` rather than replacing it, so the first negative test
reported "the broad key still works" when what answered was the agent key the
config offers. Test a refusal with `-F /dev/null`.

Ordering was load-bearing: test5's ssh config was pointed at the agent key with
`IdentitiesOnly yes` BEFORE any key was removed, so the sweeps polling those
hosts never lost a connection -- 29 samples in flight across the cutover, zero
interrupted. `mboyle-laptop` was ADDED to test7 first, being absent there.

#### BACKLOG 27 SHIPS NOTHING, AND THAT IS THE RESULT

Both mechanizations were built and measured. **The declaration route is section 0
in its literal form: its denominator excludes this row's own two worked
examples.** @1087 put its control in a file declaring `BD_GATE_SCOPE = "module"`;
@1088 added NO test file at all, its control going into `tests/test_perf_lab.py`,
line 394 of the frozen baseline, which may only SHRINK. 0 of 2, permanently.

The derived-predicate route is worse than imprecise: its false-positive rate is
not a number. Three independent implementations of one English sentence returned
outside-bands of 28-64, 42-110, and a claimed 53, no two agreeing -- and it is
wrong in BOTH directions, refusing 2 of the 8 cuts that DID write a control.

**An independent verification pass corrected the numbers this session first
produced**, including that the nine repo-wide files counted included this
session's own unmerged 1108 -- the most favourable possible sample for both
predicates. Read row 27 before re-proposing either route.

#### WHAT A TASK LIST CANNOT SEE, FOUND BY AUDITING THE HOSTS

`/tmp/bd-testrun-*`: **418 dirs / 5.1G on test4, 413 / 11G on test6, 342 / 3.0G
on test7**. tests/_tmproot.py reclaims its root in `pytest_sessionfinish`, and a
session hook cannot survive SIGKILL -- so every killed run leaks up to 49 roots,
one per worker. My own wedge hunt made most of them. Now backlog row 104, with
the note that the obvious fix (atexit, or a signal handler) cannot catch SIGKILL
either and would convert a reliable leak into a rare one.

#### LIVE STATE ON THE FLEET THAT NO TRACKED FILE RECORDS

  * `~/.bd-tools-venv` + `~/.local/bin/py-spy` on test4/6/7 (21M each), installed
    for the wedge forensics. ptrace_scope is 1, so py-spy needs sudo.
  * A **64G tmpfs mounted at /mnt/bdtmpfs on test6**, for the filesystem arm. It
    consumes RAM only as used and does NOT survive a reboot.
  * `~/cwatch.sh` and `~/bd-channelwatch.log` on all three. The loops were
    STOPPED at the end of this session after 976 samples; the logs are kept as
    the evidence for the two retired hypotheses above. Nothing restarts them.
  * `~/.ssh/authorized_keys.pre-row13` on all three, and the fingerprints of
    every removed key in `~/bd-session-2026-08-13/row13_key_inventory_before.txt`.
  * `bd-wedge-hunt` lives in `~/bd-wedge-2026-08-14/` and is TRACKED NOWHERE.

### 15.94 | SESSION CLOSE 2026-08-13 at 2af66a0 (v3.66.1106) -- fifteen cuts, and the two worst defects were mine and in the class I had just closed

Close at `2af66a0`, the squash of #391, already on `main` when this was written.
Named per the @939 trap: a section naming its own branch tip goes red on `main`
after the squash destroys it, where no band reaches.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31

BOX GATE: all four hosts PASS at 2af66a0 -- unit 15869 pass / 0 fail / 0
error / 6 skip on every host, live 36/36/32/30, and 00_tree_drift.txt is
present and EMPTY on all four (so @1092's postflight check ran and
answered, rather than being absent). Bundles at ~/captures-1106/.

#### WHAT SHIPPED: v3.66.1092 - v3.66.1106

    1092  a tree that changes DURING a capture grades INVALID, not FAIL (row 100)
    1093  the "rotating" vpn test was a fixture that never built its shape (row 25)
    1094  provisioning INSTALLS postgres instead of refusing to (row 97, PARTIAL)
    1095  a patch.dict(sys.modules) can no longer silently evict a module (row 101)
    1096  _check_csrf resolves the CSRF key late, so mint and check agree (92/93)
    1097  the eviction gate stops depending on stdlib import order  [MY DEFECT]
    1098  no assertion may be true for every input (a decidable slice of row 26)
    1099  capture output is keyed by run id, so rounds stop overwriting (row 5)
    1100  pre-import httpx so a sys.modules patch cannot evict its tree
    1101  backlog 103's premise does not reproduce -- corrected, not implemented
    1102  persist bd-ci-verdict and bd-ci-wait into toolchain/bin
    1103  the capture lane's wedge rate is measured; CLAUDE.md had it wrong
    1104  persist bd-cut-preflight and bd-sweep-run into toolchain/bin
    1105  backlog 103 closes NOT REAL -- mechanism verified, observation unverifiable
    1106  the pre-commit battery sees stray files and orphan runs (row 35, PARTIAL)

Backlog 13 OPEN -> 4 (13, 26, 27, 102). Rows 100, 25, 97, 101, 92, 93, 5, 103,
35 closed; 26 and 102 amended with measurements.

#### THE FINDING WITH THE LONGEST REACH: A CENSUS CANNOT ANSWER A SCHEDULE-DEPENDENT QUESTION

Row 101 asked for a gate against `patch.dict(sys.modules, ...)` evictions. The
audit shipped with @1095 measured ALL 28 SITES SERIALLY AND FOUND ZERO. I
validated the probe against a positive control first, so the zero was real -- and
it still proved nothing, because whether a module is "first imported inside the
block" depends on what an earlier test already imported, and `--dist loadfile`
decides that. So @1095 shipped a RUNTIME guard rather than a static gate, and
said in its own docstring that the zero was not evidence of safety.

Four cuts later, under `-n 8`, that guard fired on
`test_returns_none_when_httpx_missing` and reported **FIFTY modules evicted** --
the whole `httpx._*` tree plus `click.*`, `idna.*`, `http.client`,
`urllib.request`. Fifty times the scale of the httpcore case that started the
row. A static gate would have reported the serial zero as a pass and shipped.

**Generalise it: when a hazard's occurrence depends on scheduling, a census
measures one schedule. The instrument has to run every time.**

#### OPERATIONAL, FOR WHOEVER SEES IT NEXT

`tests/_sys_modules_guard.py` is armed on all four hosts. **It fires at some
worker counts and not others.** A capture or band that goes red naming
`SysModulesEviction` is THE GUARD WORKING, not a regression. The remedy is in
the exception text: import the evicted module at `tests/conftest.py` scope so it
is in every later snapshot. @1100 did exactly that for httpx plus three modules
the codec and import machinery load lazily. Its `ALLOWED` set is EMPTY on
purpose -- an allowlist weakens the check for every future site; an import fixes
the condition.

#### MY OWN DEFECTS, WORST FIRST

  * **I DELETED EVIDENCE ON AN UNVERIFIED PREMISE.** Removing the orphaned
    pre-@1099 `/tmp/bd_capture` on four hosts, my script printed
    `"they match -> the /tmp copy is redundant"` as a LITERAL BANNER between two
    `cat` calls, having never compared them. They did not match: on test4 that
    directory held the @1097 re-capture -- the run that confirmed the fix on the
    host that had failed -- and it was never preserved. The verdict line survives
    in the job output; the bundle does not. Same defect as `assert ... or True`,
    four cuts after I shipped a gate against it, except it caused a DESTRUCTIVE
    ACTION rather than a wrong report. **A conclusion printed in the register of
    a measurement is worse when something irreversible follows it.**

  * **I REINTRODUCED BACKLOG 25'S CLASS IN THE CUT THAT CLOSED BACKLOG 101.**
    Three tests in @1095 borrowed stdlib names the rest of the suite also owns --
    `wave`, `colorsys`, `sunau` -- so they exercised the eviction path only when
    nothing earlier on the worker had imported them. test4 went red at 1096 while
    three hosts passed the same commit. With all three names pre-imported, 3 of 8
    failed: worse than the failure showed. In the file whose entire subject is
    schedule-dependent module state, hours after fixing the identical class in
    someone else's fixture.

  * **I SHIPPED A VACUOUS ASSERTION** -- `assert "aifc" not in sys.modules or
    True` -- past review, a mutation battery, a 517-file band, twelve CI checks
    and four captures. Nothing was looking. Finding it produced @1098, whose
    census then found five more, of which two were suppressing assertions that
    are FALSE rather than merely redundant.

  * **STAGED-VERSUS-TRACKED BIT ME THREE TIMES** in one session -- section 2a,
    documented, read, and re-broken: `_sys_modules_guard` read as an undeclared
    PyPI distribution, the @1098 gate reported "untracked" from its own shard,
    and the dependency gate went red. Each fixed by `git add` alone. It is now a
    preflight check (@1106) precisely because discipline did not hold.

  * **A SELF-MATCHING GREP reported four phantom browser orphans** -- my own `ps`
    pattern matching itself. Trap #1 of the previous handoff, again. Re-checked
    by `comm` rather than command-line text: zero.

  * **A TARBALL PREDICATE INVENTED FOUR WEDGES.** Counting capture runs, my check
    extracted `10_VERDICT.txt` from a tarball; `captures-1082` stores bundles
    UNPACKED, so it reported four missing verdicts. Predicate-vs-denominator, in
    my own instrument, on the day I catalogued them.

  * **I OVERCLAIMED A STATISTIC BY FIVE ORDERS OF MAGNITUDE.** I told the
    operator 31 clean capture runs made the wedge rate "effectively ruled out at
    ~1e-6". That treated the full suite's 2/6 POINT ESTIMATE as the true rate.
    Correct comparison: Fisher exact one-sided, **p ~= 0.023**. Lower at the 5%
    level and no further; the lane's 95% CI is [0.000, 0.112] and the full
    suite's is [0.043, 0.777], because six samples is a very wide interval.

  * **I GOT A CALLEE'S CONTRACT WRONG AFTER CITING THE RULE TWICE.**
    `p_orphans` returned a string where `classify` expects
    `(total, examined, unverifiable, unit)`; the tool's selftest crashed on the
    unpack. Read the callee before calling it.

#### TWO ROWS I WROTE THAT DID NOT SURVIVE RE-DERIVATION

Row 103 claimed worker chains are written at SESSION END, so a wedged run
records nothing. `note_file` has always appended PER TEST and reopens the file
per write, with a docstring saying exactly why. Measured: 8 chains for 8 workers,
then 48 for 48. The fix I had budgeted a cost measurement for described work
already done. Closed NOT REAL at @1105, with the trigger to revisit named.

Row 102's rate question is answered -- 31 preserved host-runs at 48 workers, ZERO
without a verdict, every worker count READ from the run's own context line rather
than assumed. What died with it was the obvious mechanism: the capture lane is
NOT a small deselected subset. `--collect-only -m capture_parallel` collects
15075 of 15869 tests. **CLAUDE.md said "176 files / 1458 tests" and had done for
278 releases** -- wrong by an order of magnitude, and it invited exactly the
inference I made and had to retract. Corrected @1103. The row stays open on the
MECHANISM.

**Both rows were written by me the previous night. A plausible mechanism recorded
at 2am reads as a measurement by morning.**

#### THE INSTRUMENTS ARE NOW TRACKED, AND ONE PAID FOR ITSELF IMMEDIATELY

Every PR this session was gated on tools that existed only in an agent scratch
directory about to be deleted. Four are now in `toolchain/bin` (@1102, @1104),
each re-validated FROM ITS NEW LOCATION rather than the scratch copy.
`bd-cut-preflight`'s own selftest then went red at @1106 when the battery's shape
grew from 14 checks to 16 -- its counts are a ratchet -- which is the clearest
argument for having rescued it two cuts earlier.

Two more from the same directory were deliberately NOT tracked: they were not
exercised this session, and a tool under `toolchain/bin` inherits authority it
has not earned. They are in `~/bd-session-2026-08-13/probes/` with the reason.

#### WHAT ROW 100 BOUGHT, ON ITS FIRST REAL FAILURE

When test4 went red at 1096, `00_tree_drift.txt` was 0 bytes on all four hosts.
Triage started from "this is a real failure" instead of "is this even a real
failure" -- which is exactly the question that cost the 1082 round.

### 15.93 | SESSION CLOSE 2026-08-13 at 8e8dbc3 (v3.66.1089) -- six cuts from one capture failure, and every defect was found by RUNNING something

Close at `8e8dbc3`, the squash of #374, already on `main` when this
was written. Named per the @939 trap: a section naming its own branch tip goes
red on `main` after the squash destroys it, where no band reaches.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31

#### WHAT SHIPPED: v3.66.1084 - v3.66.1089

    1084  submit() raises QBError when the listing probe fails in a way httpx did not name
    1085  a patch.dict(sys.modules) can no longer split the httpcore module identity
    1086  three findings that lived only in prose become rows a test can read
    1087  bd-jobs reports whether a job is DOING anything, not only that it exists (backlog 3)
    1088  a type without __name__ no longer crashes the interpreter census
    1089  the overnight sweep census, as rows a test can read (backlog 25, 27, 102, 103)

Backlog: 3 closed; 95's remainder closed; 13, 25, 27 amended; 100, 101, 102, 103
opened. 14 rows OPEN.

#### THE THREAD: EVERY DEFECT WAS FOUND BY RUNNING SOMETHING, AND REVIEW FOUND NONE

Six cuts, two of them real product defects, and not one was found by reading
code:

  * the CAPTURE found @1084 and @1085 -- one unit failure on test6 whose
    traceback named `httpcore.ConnectError` where `httpx.ConnectError` belonged;
  * the FULL SUITE found the undeclared dependency @1085 introduced, which the
    33-file derived band could not see, because a conftest change has the blast
    radius of the whole suite and a band is a floor;
  * CI found that @1085's gate hardcoded `venv/bin/python`, which exists on the
    box and in the container and NOT on a GitHub runner -- three environments,
    not two;
  * the OVERNIGHT SWEEP found @1088, a real crash in `perf_lab` that had been
    wearing a flaky test's costume;
  * TYPING THE COMMAND IN ONCE found that `bd-jobs run` mangles a single-quoted
    command, in the first minute after @1087 gave it a log to say so with;
  * the BACKLOG FORMAT GATE refused @1086 for putting an evidence marker in an
    OPEN row's status cell.

That is CLAUDE.md section 1's "audit beats recollection" landing six times in
one session, and section 10's "run the check and paste the real output" being
the only thing that ever worked.

#### "A FLAKY TEST" NAMED THREE UNRELATED MECHANISMS, AND ONE WAS A PRODUCT BUG

The sweep's whole value. Seventeen whole-suite samples at f154aef across two
48-core hosts produced three failure populations that a naive quarantine would
have treated identically:

  * `test_perf_lab.py`, seven tests at once on BOTH hosts -- NOT flaky.
    `_interpreter_stats` walks `gc.get_objects()` and asserted every object's
    type has `__name__`; h11's sentinel metaclass refuses it. Section 0 exactly:
    the denominator was right and the predicate was wrong. Fixed @1088.
  * `test_t14_vpn_probe_egress::test_probe_no_tunnels`, 3 of 11 -- genuinely
    schedule-dependent, and the only real quarantine candidate.
  * `test_v3_66_729_body_contract_fixtures` -- an artefact of MEASURING TWICE.
    Its probe rows accumulate across runs and the next probe reads them, so two
    back-to-back suite runs are two different experiments. Reproduced
    independently on test5 with only a COMMENT changed between the runs.

Backlog 25 literally asks to "quarantine or annotate known-rotating tests".
Doing that by NAME would have hidden the first and mislabelled the third.

#### THE WEDGE, WHICH IS THE FINDING WITH THE LONGEST TAIL

Two of six samples at `-n 48` ended with `[gwNN] node down: Not properly
terminated` at `[ 99%]`, the master then writing nothing for 462s and 255s at a
1-minute load average of 0.06, holding a zombie child it never reaped. Different
workers each time. Eleven of eleven completed at `-n 16`.

`capture.sh --workers=$(nproc)` IS `-n 48` on these boxes. The capture parallel
lane runs a DESELECTED SUBSET, so its rate is unmeasured, and rows 102/103 say
so rather than assuming. They also say explicitly NOT to lower `--workers` until
that is measured: changing a gate's shape on a guess is how gates stop meaning
anything.

And the diagnostic was absent exactly where it was needed: the per-worker chain
files are written at SESSION END, so the wedged run recorded SIX chains for
FORTY-EIGHT workers and the dead worker's was not among them -- while the run's
own footer told the reader to replay it with `bd-ladder --chain`. The
incrementally-written pytest log was the only thing that survived, and its last
line named the worker.

#### PROCESS FAILURES OF MINE, RECORDED SO THEY ARE NOT REPEATED

  * I KILLED MY OWN SSH SHELL with `awk '$2=="bash" && /sweep_runner.sh/'` --
    the invoking shell's command line contains the script text, so the pattern
    matched itself. Trap #1 in the previous session's handoff, hit within two
    hours of reading it. The second time I needed to kill something I listed
    the PIDs first, which is what stopped me destroying a legitimate run.
  * MY SWEEP RUNNER OVERWROTE ITS OWN MARKERS on relaunch, because the filename
    carries the worker count and commit but no run id -- backlog 5's exact
    subject, reproduced in a tool written the same night by someone who had
    read the row.
  * MY FIRST REPRODUCTION OF @1085 DID NOT REPRODUCE, and the reason was the
    finding: evicting httpx AND httpcore together is self-healing. Only a
    surviving cache over evicted classes splits. Had I written the gate from
    the theory instead of running both arms, it would have asserted a green
    over an experiment that could not fail.
  * I WROTE "~500k objects" IN A COST COMMENT I HAD NOT MEASURED, then measured
    it (7857 in that probe) and corrected it before committing. Section 1's rule
    is that a number needs its denominator in the same sentence; a plausible one
    invented for a comment is the same defect at smaller scale.

### 15.92 | SESSION CLOSE 2026-08-13 at 203833e (v3.66.1080) -- nine cuts, and every one found its own defect

Close at `203833e`, the squash of #365, already on `main` when this was written.
Named per the @939 trap: a section naming its own branch tip goes red on `main`
after the squash destroys it, where no band reaches.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31

#### WHAT SHIPPED: v3.66.1072 - v3.66.1080

    1072  an undeclared repo-wide gate is now a red test (backlog 46)
    1073  CLAUDE.md says what is true, and drops the cloud container
    1074  bd-jobs reaches the host you named, and says which thing went wrong
    1075  bd-fleet separates the tree from the service
    1076  a fixture value that collided with a real hostname turned test6 red
    1077  three claims in CLAUDE.md that were false, one of them inverted
    1078  bd-fleet's litter column counted two globs, not the directory
    1079  capture.sh refuses a tree it cannot measure against (backlog 98)
    1080  the suite reclaims what it allocates under /tmp (backlog 95)

Backlog: 46, 91, 95 (partial), 98 closed; 99 opened. 13 rows OPEN.

#### THE THREAD RUNNING THROUGH ALL NINE: THE INSTRUMENT WAS THE DEFECT

Not one of these was a product bug. Every one was a measuring device reporting
something other than what it measured, and in six cases the device had been
believed for months:

  * the gate against a suite falling out of every CI shard had itself fallen
    out of every shard, in the cut that created it, and had never run (@1072);
  * CLAUDE.md's front matter said nothing branches on a hostname while
    `bd-jobs` branches on one -- produced by a `*.py`/`*.sh` glob that cannot
    see the extensionless script that does it, which is the denominator failure
    documented 250 lines below the claim (@1073);
  * `bd-jobs` reported "deploy it there first" for a host it never reached
    (@1074);
  * `bd-fleet`'s version column read the TREE while the header implied the
    service (@1075), and its litter column counted two globs while the largest
    leak family matched neither -- a 5.3x undercount that had been the source
    of backlog 95's figures (@1078);
  * a capture graded a green suite FAIL because four uncommitted files drifted
    the graph pin, twice, and nothing said so at the start (@1079).

#### AND THE FIXES KEPT REPRODUCING THE SHAPE THEY WERE FIXING

Recorded because CLAUDE.md section 0 says this is the highest-yield rule on the
page, and it earned that again:

  * a monitor written to watch captures asked "is pytest running?" -- true for
    one of thirteen capture steps -- and reported a healthy host as dead;
  * a leak census asked "does this file clean up?" by searching whole files,
    then enumerated /tmp with a command that listed two families twice,
    inflating the total 19% and reordering the ranking it existed to produce;
  * a test asserting the probe reads `deployed_version.txt` was satisfied by
    the COMMENT saying so, and a mutant walked through it;
  * a gate for the tmpdir leak ran pytest inside pytest with its probe outside
    `tests/`, so conftest never loaded, both arms behaved identically, AND the
    assertions globbed the already-redirected directory;
  * `bd-fleet`'s own selftest asserted a literal note string and failed a
    correct change;
  * two new tests named `test6` as a fixture host and went red on the one box
    with that hostname -- green on test5, green in CI.

Every one was caught by a machine (mutation battery, capture, full suite,
selftest), none by review.

#### THE ONE THAT WORKED

@1072's `BD_GATE_SCOPE` policy refused the tree at @1080 because the new gate
declared itself repo-wide and had not been added to `_DECLARED` or a shard --
one cut after shipping, catching its own author at the exact step the policy
exists for. That is what a gate is supposed to feel like.

#### STATE AT CLOSE

Fleet is at **3.66.1075** and was deliberately NOT deployed past it: the
operator asked for ship-and-merge only while away. **1076-1080 are merged and
undeployed.** 1076 fixes the two test6 capture failures, so test6's capture is
expected red until it is deployed and re-run.

Captures at 1075: test5/test4/test7 PASS (15779/0/5), test6 FAIL on those two
tests only.

/tmp on the four hosts still carries 13000-18000 entries each. The suite no
longer adds to it; the backlog is `bd-gc`'s and needs an operator decision,
which is why the box-only gate self-skips and fails loudly when armed.

### 15.91 | SESSION CLOSE 2026-08-12 at adf8c3e (v3.66.1068) -- item 48 root-caused, re-derived and CLOSED

Close at `adf8c3e`, the squash of #353, already on `main` when this was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31
CLOSED: 48

#### ITEM 48 IS CLOSED, AND THE NUMBER WAS NEVER WHAT ANYONE THOUGHT

Every count attached to this item -- 14, 13, 11, 10 -- was a reading of a STATIC
text heuristic that over-reports BY DESIGN. Re-derived at RUNTIME with
`bd-modwatch` in per-file mode at v3.66.1068, the answer is **three**, and two of
those drop exactly `bulk_downloader.push`, which **zero** files bind at import
time. So the real population was ONE file.

  tests/test_v3_66_1034_guards_survive_a_module_wipe.py  dropped=263 swapped=5
  tests/test_mod1_c6_effective_mode_readout.py           dropped=1   (harmless)
  tests/test_v3_43_60_captcha_relay.py                   dropped=1   (harmless)

THE ONE THAT MATTERED WAS THE RATCHET ITSELF, which is why nothing ever pointed
at it: `_module_wipe_leakers()` read RAW text, so 1034's own regex source
literal and an assertion message made it score as restoring while it deleted 263
modules and restored none. Fixed at v3.66.1067 (`tests/python_source.py`, the
Python counterpart to `shell_source.py`), and the census went 13 -> 14 with the
+1 being the file becoming visible to itself.

THE FIX, v3.66.1069: a module-scoped save/restore in 1034. The wipe STAYS -- the
file exists to prove the conftest guards survive one -- but the blast radius is
now the file rather than the xdist worker. Same shape v3.66.1049 used for
test_v3_66_1021.

MEASURED, before and after:

  1034 then 780                        7 failed / 12 passed  ->  19 passed
  1034 then the 8-file victim set      --                    ->  154 passed
  runtime leakers                      3                     ->  2, both harmless
  census / budget                      14 / 14               ->  13 / 13

#### THREE INSTRUMENTS HAD TO BE REPAIRED BEFORE THE DEFECT COULD BE SEEN

That is the transferable part. The defect was one file and about twenty lines;
what took the time was that every instrument pointed at it was wrong:

1. **The census could not see its own file** (@1067) -- it read prose as code.
2. **bd-modwatch answered a different question depending on argv** (@1068) --
   named files collapsed into ONE co-batched group while `--all` measured per
   file, and the verdict said "file(s)" either way. Backlog row 22's
   "bd-modwatch reports 0" was a BATCH answer compared against a PER-FILE
   question; that evidence is void.
3. **capture.sh cannot see this class at all** (@1058, unchanged) -- its lanes
   do not co-batch, so a green capture was never evidence in either direction.

A wrong instrument does not merely fail to find the defect; it produces a
confident number that everything downstream inherits. Two figures were retracted
on this item before the third was measured.

#### WHAT REMAINS

31 is operator-bound and untouched. The backlog carries the rest, with the
priority order written into it rather than into a second document.

### 15.90 | SESSION CLOSE 2026-08-12 at a5571be (v3.66.1056) -- sixteen cuts, a fleet that grew to four, and one retraction

Close at `a5571be`, the squash of #341, already on `main` when this was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 48

Nothing in the numbered inventory closed this session. 48 was PARTIALLY fixed at
v3.66.1049 -- one leaker's second mechanism, at the leaker rather than at the
victim -- and the class is open. 31 is operator-bound and was untouched.

#### THIS SECTION IS SIXTEEN CUTS LATE, AND NOTHING WENT RED

15.89 closed at v3.66.1040. v3.66.1041-1056 then shipped with no machine-visible
close, and every gate stayed green throughout: `bd-freshcheck`'s register check
asks whether the newest close names an ANCESTOR of HEAD, and 15.89's `b2ea078`
still is one. So the check was answering its own question correctly the whole
time; it simply never asks whether the newest close is RECENT.

That is section 1's rule about this register turned on the register itself -- a
deferral that lives only in prose has not been deferred -- and the reason it
surfaced at all is that the operator asked for the open list, not that anything
detected it. A future session should not read a green `bd-freshcheck` as
evidence that the close is current.

#### WHAT THE SIXTEEN CUTS DID

- **The fleet went from two boxes to four**, and the contract's own first
  sentence was wrong about it. `CLAUDE.md` opened with "single deployment
  target: headless host `test4`" for the whole life of the file, which was
  false from the second box onward -- through four. Corrected at v3.66.1051.
  Nothing in the tree could contradict it: no `.py` or `.sh` resolves or
  branches on a hostname, so a wrong host count breaks no test and fails no
  gate.
- **The clean-host bring-up proof was taken on .84** and is recorded in
  `docs/repo/FRESH_HOST_BRINGUP.md`. Provisioner VERDICT READY (311s, 21 rows
  OK, 0 WARN), then capture PASS (unit 15656/0/0/26, live 29/0/7), with
  `git status --porcelain` at 0 lines throughout and ZERO hand-fixes, re-run on
  a stable tree to byte-identical counts. The first run of it was confounded by
  an rsync into the corpus WHILE the capture ran, which is why the second run
  exists -- change one variable at a time applies to the tree under measurement,
  not only to flags.
- **The improvement backlog became tracked and machine-visible** at
  `project-knowledge/IMPROVEMENT_BACKLOG.md`, gated by
  `tests/test_v3_66_1052_the_backlog_is_machine_visible.py` (v3.66.1052). It had
  lived in an untracked file in the operator's home, which is why it kept being
  lost. Its ids are its OWN namespace and are NOT the ITEM LEDGER's.
- **`bd-jobs` shipped with a separator bug its own first live use found**
  (v3.66.1041). `argparse.REMAINDER` keeps the `--`, so `run --host X -- sleep
  90` sent `bash -c "-- sleep 90"`. Eleven tests and a green self-test passed
  either side of the join; nothing asked what string reached the shell.
- **Launched work became bounded and killable** (v3.66.1054). `bd-run` gained
  `--max-seconds` with the cap DELEGATED to coreutils `timeout` rather than
  implemented in-tool, so `bd-run` holds no process-signalling call of its own;
  `bd-jobs` gained `start_new_session=True` and now verifies a pid is its own
  group leader before signalling the group, saying so when it is not.
- **`streamlink` was installed by nothing** (v3.66.1048) -- it is
  `live_recorder.py`'s PREFERRED backend and no manifest carried it. Fixed in
  `scripts/lib/system_deps.sh`, the single source of truth.
- **The kill switch's auto-cycle thread hung full-suite runs** (v3.66.1050); it
  now returns before mutating state when `BD_DISABLE_KEEPALIVE` is set.

#### THE RETRACTION, AND IT IS THE MOST IMPORTANT LINE HERE

A claim made and shipped in prose during this session -- that the v3.66.1034
text ratchet and the runtime orphaners were DISJOINT sets (13 against 10, zero
overlap), and that the ratchet was therefore blind -- is **RETRACTED**, at
v3.66.1055. `toolchain/bin/bd-modwatch` was built to re-derive it and could not
reproduce the ten-orphaner list at all: it reports 0 for those same files in a
two-file harness.

**Neither number is settled.** The disagreement between a full-suite probe and
bd-modwatch's two-file harness is UNRESOLVED and is recorded in the tool's own
docstring. Do not quote "10 orphaners", or "11", as fact -- backlog row 22 and
ledger item 48 both rest on it, and re-deriving is the first step of that work,
not the fixing.

#### WHAT WAS MEASURED ABOUT THE SUITE ITSELF

- **The full-suite failure count is NOISE, not a verdict.** Five samples per
  host at one commit and one `-n 32`: test4 gave 18/10/11/7/6 and test7 gave
  13/20/4/15/5. The distributions OVERLAP, so the count is not a property of the
  machine. After v3.66.1049 and v3.66.1050 the same arms gave 1/3/9/7/0 and
  5/12/0/2/6. Never conclude from one run.
- **A dead xdist worker hangs the suite unboundedly and `pytest-timeout`
  structurally cannot catch it** -- the timeout is enforced INSIDE the worker
  that died. Seen three times as `[gwN] node down` at ~99%, then load 0.00 for
  44 minutes with nothing written. This is why `bd-run --max-seconds` delegates
  to `timeout`, which signals the process GROUP.
- **A percentage is not progress.** Two hosts sat at "99%" for 44 minutes having
  produced nothing, and the percentage was the only reason it read as slow
  rather than as broken. Section 10 gained the rule.

#### OPERATIONAL, AND NOT DERIVABLE FROM THE TREE

- **test6's disk was merged**: a 98G root plus a 1.9T XFS `/home` became a
  single 2.0T ext4 root. Verified afterwards -- repo present, 924 corpus files,
  fstab entry commented, service active, health 200, `db_ok: true`. **A reboot
  after that change is UNTESTED.**
- **The capture corpus survives neither a rebuild nor `deploy.sh`.** Two hosts
  silently had ZERO files in `captures/` while the analytics routes reported an
  empty store with no warning. It was propagated by hand; nothing stops it
  recurring. That is backlog row 89.
- The three unmount attempts during the disk merge all ABORTED safely, and two
  of the three blockers were self-inflicted: the operator's re-login during the
  window, and the polling loop of the agent doing the work holding a cwd inside
  the filesystem it was trying to unmount.

#### WHAT IS STILL OPEN

The tracked backlog carries 13 OPEN rows -- 3, 5, 13, 22, 25, 26, 27, 33, 34,
35, 46, 54, 89 -- re-derived at `a5571be` against
`project-knowledge/IMPROVEMENT_BACKLOG.md` rather than quoted. The ledger
carries 31 and 48. Two shapes are worth naming because they recur:

1. **22 and 48 are the same subject from two ends**, and both currently rest on
   the retracted figure above. Re-derive before fixing.
2. **46 -- an undeclared repo-wide gate -- has now failed FOUR times** (944,
   947, 1031, 1034), twice by sessions that had just read the warning about the
   first two. A gate CI does not run is a gate that does not exist, and
   `_DECLARED` is hand-pinned, so it cannot notice a gate nobody declared.


### 15.89 | SESSION CLOSE 2026-08-11 at b2ea078 (v3.66.1040) -- batch D, and item 33 retired rather than raised

Close at `b2ea078`, the squash of #324, already on `main` when this was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 48
CLOSED: 33

Item 33 is closed by DECISION, not by the pool shrinking -- it was a ratchet
with no finish line, which is what made it unclosable. Two gates were conflated
under it and only one is retired: `_TOOL_BUDGET` (total tool count) is gone,
`_PROSE_ONLY_BASELINE` (tools nothing invokes) is kept as a standing gate. See
the inventory entry.

THE LEDGER GATE ADDED AT @1035 CAUGHT THIS SECTION'S OWN PREDECESSOR. Closing
33 made 15.88's `OPEN: 31, 33, 48` stale, and
`test_no_ledger_declares_open_an_item_the_inventory_has_closed` failed naming
15.88 and item 33 -- one cut after being written, on a case nobody planned. That
is the whole argument for gating a promise rather than trusting prose to stay
current.

#### BATCH D -- work on another host is now discoverable and killable

`toolchain/bin/bd-jobs`. The incident it exists for, from 15.88: a sampler
launched over ssh from .164 against .85 outlived its killed local task by
EIGHTY-EIGHT MINUTES, kept spawning rounds, and its `.pyc` writes broke a
deploy at step 9 -- which had already stopped the unit, leaving test4's service
down. Nothing recorded the work existed, so nothing could find it.

Three verbs over one registry, written ON THE HOST WHERE THE WORK RUNS because
that is the host that can see it: `run` launches and registers in the same
remote shell that knows the pid (a dropped connection must not leave work with
no record); `list` separates live from stale; `reap` kills by pid; `orphans`
reports pytest with no registry entry and NEVER kills it, because an
unregistered run is more likely the operator's than an agent's.

**PID REUSE IS GUARDED, and it is the part worth reviewing.** A pid is not an
identity -- the kernel recycles them -- so a stale entry pointing at a recycled
pid would have this tool kill an innocent process with full confidence, on
three hosts, with passwordless root available. Every entry records the process
start time from `/proc/<pid>/stat` field 22, and reap refuses any entry whose
start time no longer matches or that carries none at all. The field is parsed
from the LAST `)` rather than by splitting on spaces: field 2 is the executable
name in parentheses and can contain spaces, so a naive split shifts every later
field and would compare the wrong number while looking correct.

#### WHAT IS STILL OPEN

1. **Item 48's second mechanism.** The guard fix repairs the guard; the plugin
   victims still fail after a leaker WITH and WITHOUT it, identical sets. What
   else the wipe breaks is unfound.
2. **Item 31**, the eight operator-bound rows.
3. **The candidate workflow has never been exercised** -- no tip has been run
   on `.85` before a merge, though `deploy_fleet.sh` and the preflight now make
   it cheap.
4. **`.249`'s clean-host role is void** -- inhabited, venv, service enabled. The
   bring-up proof cannot be retaken without a reimage.
5. Batches B and E from the review backlog; 15.86's queue item 4 and the
   drift-axis gold-join defect, both still untouched.
6. `.85` carries a duplicate `NOPASSWD: ALL` sudoers entry; `streamlink` is
   absent after a clean provision.

### 15.88 | SESSION CLOSE 2026-08-11 at a08c0ad (v3.66.1035) -- the first full-fleet session, and the three defects its own review found

Close at `a08c0ad`, the squash of #319 and already on `main` when this was
written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33, 48
CLOSED: 46, 47

15.87's ledger read `OPEN: 31, 33, 46, 47, 48` and went stale the moment 46 and
47 closed. Every existing check passed -- direction A resolves the numbers,
direction B accepts "closed in the inventory" as accounted for -- so the newest
close, the thing a fresh session reads to learn the open set, was simply wrong
and nothing could see it. `test_no_ledger_declares_open_an_item_the_inventory
_has_closed` now catches exactly that; it was proven RED against 15.87 before
this section existed.

#### WHAT THIS SESSION WAS

The first worked entirely on the operator's own hardware: `.164` test5
(`7b4ea932c297`), `.85` test4 (`102b31c04e7b`), `.249` test6 (`1d60f39bd8d6`).
194 cores, ~1.5TB RAM. Five cuts, @1031 through @1035, 86 test-run logs.

Closed: 46 (a dependency's PyPI thread), 47 (a vacuous traversal test), and
15.86's item 4 (bare Ubuntu -> green capture, zero hand-fixes, on `.249`).
Root-caused but NOT closed: 48.

#### THE TECHNIQUE WORTH KEEPING

**Parallel prefix ladders.** Item 48's culprit was found by replaying one
worker's REAL 232-file chain as 34 concurrent prefix probes -- each serial
in-process, all independent -- giving a clean monotonic step at 183 ok / 184
BROKEN. Sequential bisection would have been eight rounds of four minutes. The
leak reproduces in 2 files and 1.4 seconds once named.

**Instrument the resource, never read for it.** Three findings came this way
and none could have come from reading: a dependency's daemon thread calling
PyPI, the exact test after which a guard died, and the SOCK_DGRAM/SOCK_STREAM
split that turned "130 outbound calls" into 15 real ones.

#### THE THREE DEFECTS THE REVIEW FOUND, ALL SHIPPED BY THIS SESSION

1. **The socket recorder leaked a directory per run, forever.** `arm()` did an
   unconditional mkdir while writes were conditional: 744 empty directories
   under /tmp after ONE session, on every host, growing with every band and
   capture. Fixed at @1035 -- lazy mkdir, so a clean run's footprint is
   nothing, plus count-bounded retention. **Creating a path is a promise to
   remove it**, and nothing in the contract said so.

2. **Two new gates were never wired into CI.** `test_v3_66_1034` and
   `test_v3_66_1031` existed only locally, so a new leaker would land, CI would
   go green, and the ratchet would fire for nobody. This is 15.86's own
   observation about `_DECLARED` being hand-pinned -- 944 and 947 were never
   added -- repeated by the session that had just read it. Fixed with an
   `isolation` shard and three `_DECLARED` entries. **A gate CI does not run is
   a gate that does not exist.**

3. **The newest ledger was stale**, above.

#### METHOD COSTS -- and the one that matters most

**Round one of the review was recollection and produced plausible items. Round
two was `ls`, `rg` and `ps` and produced six real defects, three of them live
in `main`.** Audit beats memory, and the difference was not effort.

- **A single full-suite sample is uninterpretable.** MACHINE LOAD dominates:
  1-8 failures on a quiet box, 18-29 under four concurrent suites, same tree.
  Three single samples (19, 27, 51) were each read as signal and each was
  noise; one A/B at n=4 settled it. Any historical claim resting on one sample
  is suspect.
- **A filter at capture time destroys the evidence.** Piping a run through
  `grep` cost two 12-minute reruns, and `grep -c "exit=[^0]"` inverted a
  verdict so a totally successful bring-up reported failure. CLAUDE.md warns
  about `head`; the class is every filter.
- **`ast.parse` is not name resolution**, and the contract says so. Two
  `NameError`s shipped past a parse check anyway.
- **`pkill -f` matched its own command line** and killed the shell -- the
  documented trap, walked into.
- **A killed task does not reap SSH-launched work.** A sampler survived 88
  minutes past its task being killed, kept spawning remote pytest rounds, and
  its `.pyc` writes broke a deploy at step 9 -- which had already STOPPED the
  service, leaving `.85` down. A failed deploy is not a no-op.
- **`bd-mutate` scored 4 of 10 on the first pass** against tests written that
  hour, including an over-sensitivity control that set its sentinel in the same
  body the fixture runs before, and a unit test that recomputed a derivation
  instead of calling it. Three rounds to reach 9/9.

#### WHAT IS STILL OPEN

1. **Item 48's second mechanism.** The guard fix repairs the guard -- proven,
   `test_no_test_writes_the_repo_plugins_dir` fails in every pre-fix sample and
   no post-fix one -- but a controlled 2-file experiment shows the plugin
   victims fail after a leaker WITH and WITHOUT it, identical sets. The leakers
   damage something beyond the three registered guards.
2. **`capture.sh` cannot see this defect class.** `.249` passed capture at
   15547/0 while carrying all 14 leakers, because the lane split does not
   reproduce `pytest tests/` co-batching. A green capture has never been
   evidence about item 48, in either direction.
3. **The candidate workflow has still never been exercised** -- no tip has been
   run on `.85` before a merge.
4. **`.249`'s clean-host role is void**: inhabited, venv, service enabled. The
   bring-up proof cannot be retaken without a reimage.
5. Items 31 and 33; 15.86's queue item 4 and the drift-axis gold-join defect,
   both untouched.
6. `.85` carries a duplicate `NOPASSWD: ALL` sudoers entry; `streamlink` is
   absent after a clean provision, so the live-recording lane is unexercised on
   a fresh host.

### 15.87 | SESSION CLOSE 2026-08-11 at e5cece7 (v3.66.1031) -- stage 1 of the socket guard, and the three findings its first harvest bought

Close at `e5cece7`, the squash of #315 and already on `main` when this was
written -- never this cut's own branch tip, which the squash destroys.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33, 46, 47, 48
CLOSED:

Items **46, 47 and 48 are opened by this session** and filed in the 15.36
inventory, on operator instruction. Nothing is closed. 33's denominator is
re-derived at 238 below.

READING: **test5**, machine-id(sha256/12) `7b4ea932c297`, 86 cores, clone NOT
shallow, venv 3.12.3. Branched from `e5cece7` (v3.66.1030), already on `main`.
Everything below is this host; nothing was run on test4 and nothing here is a
claim about it.

#### THE CUT (@1031)

The operator's v3.66.980 decision, built: an autouse recorder that REPORTS
non-loopback connects and blocks nothing. Stage 2 (enforce, with an opt-out
marker) is deliberately not in this cut -- the estimate it would have been
written against was "21 files might call out", a grep over string literals.

`tests/_socket_record.py` wraps `socket.socket.connect`/`connect_ex`.
`create_connection` lands there, so urllib, http.client, requests and asyncio
`sock_connect` are all covered by the one wrapper. That was DEMONSTRATED against
the motivating defect rather than reasoned from the call chain: with the hook
armed, `ytdlp_updater.latest_version(allow_fetch=True)` recorded exactly one
attempt, `151.101.192.223:443`, via `http/client.py`. The subject is in the
denominator.

Three design points that are not obvious and cost something to get right:

- **No environment variable at all.** The run token reaches xdist workers
  through `pytest_configure_node`/`workerinput`. An env var would be inherited
  by every subprocess the suite spawns AND would join the surface
  `test_gui_parity` grades. Note while checking that: **CLAUDE.md section 4
  says that scan "matches on the `BD_` prefix". It does not, and has not since
  v3.66.713** -- the test's own comment records the prefix-blindness being
  fixed, and an unprefixed key must now be ledgered display-only. The stale
  sentence would have sent a future author to pick an unprefixed name for
  exactly the wrong reason.
- **Per-run sink directory, keyed by the master pid.** The obvious design --
  one shared directory cleared at session start -- lets a nested pytest child
  (164 test files spawn subprocesses) wipe its parent's records mid-run. Separate
  directories need no clearing.
- **`summarize()` defaults to the ARMED directory, not `sink_dir()`.** Caught by
  this file's own loopback test passing while reading a different directory:
  an empty summary, green, over a denominator that never held the records. It
  would have stayed green with the classifier inverted.

RED-first, and the honest version: the pre-implementation RED was a COLLECTION
ERROR (module absent), which proves the module is missing and not that any
assertion constrains anything. The per-assertion proof is the battery ->
**10 caught, 0 escaped, 0 invalid**, baseline proven GREEN first, including a
mutant that reverts the `summarize()` fix above.

Band: `bd-band-derive` gave 10 files; the axis-6 members and the version-pin
gates were added on top for 25 files, green at 249 tests. `bd-regen-order`
clean, run after the last source edit and again after the last edit of all.
Import-graph re-freeze was a **no-op** -- the gate does not count a
test->test-helper edge, so `bd-band-derive`'s flag was conservative there.

**One gate went RED for the documented reason and it is worth repeating:**
`test_v3_66_653_dep_freshness` read `_socket_record` as an undeclared
third-party distribution, because its resolver checks `git ls-files` and the new
file was UNTRACKED. `git add` of the explicit paths fixed it. Section 2a's
"gates cannot see untracked files", firing in the RED direction rather than the
usual silent-pass one.

#### THE HARVEST -- what stage 2 actually has to deal with

Full suite, `-n 16 --dist loadfile --timeout=240 -p no:randomly`:
**130 non-loopback attempts from 124 tests across 29 files.** But the headline
number is the wrong one to act on, and splitting it is the finding:

| | count |
| --- | --- |
| SOCK_DGRAM route lookups that send NO packet | **115** |
| SOCK_STREAM, genuinely on the wire | **15** |

115 of them are `_lan_ip_guess` (`app.py:4999`) connecting a UDP socket to
8.8.8.8:53 to ask the routing table which source address it would pick.
`app.py:4991` says so in as many words: no packet is sent. **A stage 2 that
blocks these breaks LAN-IP discovery and buys nothing**, and a stage-2 author
reading "130 outbound calls" would be reading a list of 130 when the tree has
15. The recorder records `type` and `sends_packets` for exactly this reason --
added AFTER the first harvest, because the first harvest could not tell the two
apart.

The 15 that do send packets, all to :443:

| test file | via |
| --- | --- |
| `test_secret_display_never.py` | `community_scrapers.py:260:fetch_index` |
| `test_v3_43_60_vpn_backends.py` | `vpn_providers/pia.py:203:_pia_token` |
| `test_v3_43_64_mp4_metadata.py` | `mp4_metadata.py:317:fetch_cover` |
| `test_v3_43_65_tier_probe.py` | `tier_probe.py:314:probe_higher_tiers` |
| `test_v3_66_729_body_contract_fixtures.py` (x3) | `app_scrape_listing.py:69`, `app_template.py:286` |
| 8 more | ambient, see below |

#### ITEM 46 -- THE SUITE STILL CALLS PyPI, FROM A DEPENDENCY'S DAEMON THREAD

Five of those attempts go to `151.101.*:443` (PyPI) and belonged to no test at
all until the recorder grew thread attribution mid-cut. Named, they come from
thread `_check_wrapper_update`: **`cloakbrowser`**, a third-party package in
`requirements`, spawns a daemon thread on import that GETs
`https://pypi.org/pypi/cloakbrowser/json` **once per process** --
so once per xdist worker, landing on whichever test happens to be running.

It lives in `cloakbrowser/download.py` under `site-packages`, in
`_check_wrapper_update`, armed from `_maybe_trigger_update_check`. Named by
function and WITHOUT a line anchor on purpose: `venv/` is untracked, so a
`path:NN` citation into it can never resolve and the anchor gate reports it
BROKEN -- which is what it did to the first draft of this paragraph, correctly
(section 4's rule, earned again). It is opt-out:
`CLOAKBROWSER_AUTO_UPDATE=false`, or setting `CLOAKBROWSER_DOWNLOAD_URL`.

This is @977's exact class -- a live PyPI call inside unit tests -- surviving in
a dependency rather than in our code, which is why no gate over our tree could
ever have seen it. The remedy is one environment variable, NOT built here
because `CLOAKBROWSER_*` names are already in the config-surface inventory
(`CLOAKBROWSER_BINARY_PATH` is a ledgered unprefixed key) and adding one is a
`test_gui_parity` question, not a drive-by.

**Attribution is why this is actionable.** On the first harvest 9 of 16
packet-senders had NO test attached, because the nodeid is thread-local and set
on the main thread. Rows nobody can attribute are the rows stage 2 cannot act
on. The fallback labels them `ambient` rather than `test` -- an approximation,
and it says which it is.

#### ITEM 47 -- `test_path_typed_flag_rejects_traversal` IS VACUOUS, AND THE CODE IS FINE

`tests/test_v3_66_717_exec_bridge.py` failed in all four full runs, including on
pristine `main`. It reads as a path traversal being ACCEPTED (200, not 400).
**It is not a vulnerability.** Measured:

```
_ALLOWED_PATH_ROOTS = ('/home/mboyle/BulkDownloader', '/tmp')
cwd <=3 deep under /tmp : realpath('../../../../etc/passwd') = /etc/passwd        -> REFUSED (correct)
cwd >=4 deep under /tmp : realpath(...)                      = /tmp/.../etc/passwd -> ACCEPTED (correct)
```

`tool_bridge`'s path validation does `realpath` + containment and is right in
both cases. The TEST hard-codes four `..` and so depends on the depth of
pytest's `tmp_path`, which varies with worker and test name. Two defects in one:
it fails on a false premise when it fires, and it returns **vacuously** when
`ffprobe` is absent (the only path-typed flag lives on the ffprobe entry, which
`_build_allowlist` only creates if `shutil.which` resolves it) -- so in isolation
it passes while proving nothing. Fix direction: derive the payload from the
actual cwd depth or use an absolute `/etc/passwd`, and assert ffprobe's presence
rather than skipping past it silently.

#### ITEM 48 -- THE FULL SUITE ON test5 DOES NOT RUN CLEAN, AND THE FAILING SET ROTATES

SIX full runs this cut -- the first version of this line said five, over a table
listing six, and the count is the whole evidence for the instability it claims:

| tree | workers | failed |
| --- | --- | --- |
| pristine `e5cece7` | 16 | 13 |
| @1031 | 16 | 1, then 7, then 8 (three runs) |
| pristine `e5cece7` | 4 | 16 |
| @1031 | 4 | 35 |

Every file sampled from the "new" failures passes when run together in
isolation with the cut present (23 tests, 5 files, green), so these are
**co-batching artifacts, not breakage**: `--dist loadfile` puts more files in
sequence per worker as `-n` falls, and adding any test file reshuffles the
assignment. The cut is not the cause -- it produced the LOWEST count of any run
-- but note honestly that with one sample per cell and a set this unstable, "the
cut adds no failures" rests on the isolation runs, not on the totals.

**This supersedes what CLAUDE.md section 5 leads a reader to expect.** That
section records the sanctioned sweep as "14 failed, all the documented
container-only set, item 34's four order-dependent failures ABSENT -- @945's fix
holding at full denominator". On test5 the documented `-n 4` form gives 16
failures on pristine main, and they are mostly NOT that container set (no
`test_e2e_smoke`, which passes here). Two variables differ from the recorded
measurement -- host and worker count -- and this cut isolated the second: fewer
workers is WORSE, not better. What is not yet known is how many are genuinely
order-dependent versus environmental on this host; that wants its own pass and
a number.

#### HOST EVENTS -- test5, on operator action

- **GPU fixed by the operator** (told, then verified here): Tesla T4, driver
  580.173.02, CUDA 13.0, with two `llama-server` processes resident at 11249MiB
  of 15360MiB. `ollama` active, `/api/version` 0.32.9. This supersedes 15.86's
  "installed via install_ai_ollama.sh (exit 0, **CPU mode**)".
- `tools/deployed_version.txt` reads 3.66.1030, started 2026-08-11T15:55:17Z --
  the PROCESS, not the tree. test5's tree was at `e5cece7` (v3.66.1030) at
  session start.

#### STILL OPEN

Unchanged from 15.86 items 1-5 (match.hosts bridge, item 31's eight rows, item
33 at denominator 238 re-derived three ways this cut, the clean-host bring-up
proof, the post-@1026 test5 capture), plus from here:

6. **Stage 2 of the socket guard** -- enforce with an opt-out marker, against
   the 15 packet-senders above and NOT the 115 route lookups. The child-process
   blind spot (164 files) wants a `sitecustomize.py` decision.
7. **Items 46, 47 and 48** -- the PyPI-calling dependency thread, the vacuous
   traversal test, and the rotating full-suite failure population. 48 is the
   one that blocks reading a test5 full run as a gate.

### 15.86 | SESSION CLOSE 2026-08-11 at b92c971 (v3.66.1029) -- first test5 session: five cuts, and the collector no gate could see

Close at `b92c971`, the squash of #314, already on `main` when this
was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33
CLOSED: 44

Nothing this session opens or closes a NUMBERED item. Item 33's inventory text
is corrected in place this cut (240 -> 238, measured three ways below); the
correction is not a close.

#### THE FIRST TEST5 CAPTURE, PER-CUT -- test5, machine-id(sha256/12) 7b4ea932c297, at 6728dc8 (v3.66.1025)

FAIL exit=1: unit 15507/2/0/25, live 31 pass/4 warn/1 fail. Graph pin OK --
NOT the stale-pin bookkeeping case. Every failure and skip named:

| finding | cause | class |
| --- | --- | --- |
| test_toolchain_534 tool-smoke + test_v3_66_949 pytest-timeout | requirements-test.txt never installed on the host | provisioner gap (fixed @1028) |
| live L17 FAIL + L18/L19 WARN | ollama absent while migrated app_config has ai_enabled=true | runbook gap (fixed @1029); installed on operator grant |
| live L6 WARN | migrated test4 auth-state rows (July last_check stamps), site d9f19e92 yellow | migrated state + cookie age, NOT a regression -- none of @1023-1025 touches login |
| live L30 WARN | migrated tunnels.json genuinely holds zero tunnels | accurate config state |
| 25 skips | 19 no-PG-DSN, 3 dev-package, 1 netns, 1 dead-branch, 2 parametrized floors -- but see below | every skip named; the count moved 5 -> 25 because the PG suites migrated from container |

Per-cut: @1025 VERIFIED (host identity block present; a capture now says which
box). @1024 held everywhere exercised, and running its runbook on a real fresh
host found four gaps (below). @1023 fixed the one collector its guard test
reads, and the CLASS was open -- the finding this session is named for.

Note test4's tool-smoke passes only because someone hand-installed pyflakes
there; the provisioner gap was invisible on the proven box, which is the
copy-nobody-updated shape again (S0/S8).

#### THE COLLECTOR THE GATE COULD NOT SEE (@1026)

`GET /api/data/capture_diagnostics` COLD measured **17.36s against L34's 8s
serial gate** on test5's real 2.5GB store, with budget_s=5 exhausted and
useless. Mechanism, workflow-verified (6 agents, adversarial synthesis; all
five claims CONFIRMED):

- collect() checks its deadline only BETWEEN files; ONE diagnose of the
  newest <=25MB .wacz = **16.1s** (2nd: 18.8s; 3rd: **37.9s at 2.0MB** --
  size does not predict regex cost). Cross-checked on test4
  (102b31c04e7b): 14.5 / 16.9 / 35.98s -- same files, near-identical cost.
- A diagnostics "file" is two full zip parses + whole-dom-log HTML
  serialization + the recognizer batteries + a sha256 of the archive.
  @1023's "overrun is ONE FILE, 0.233s" measured capture_analytics' JSON
  parse -- ~2 orders of magnitude off for THIS collector.
- capture.sh restarts the service at step [4], so L34 always probes an
  EMPTY cache: the failure was deterministic on both boxes, not the
  @1015-era coin flip. The bomb armed when the *.redacted.wacz files
  entered the newest-50 mtime window late 2026-08-10 -- AFTER test4's
  last green capture.
- Falsy-zero deadline guards in capture_diagnostics.py:283 AND
  replay_validator.py:170 (`if budget_s` -- 0 = UNBOUNDED, measured >10min).
- _cached had no lock: L34's phase-1 + serial probes each ran the full
  ~17s compute concurrently.
- A degenerate budget (0.001s) fits the gate only by emptying the report:
  NO budget value alone can fix an uninterruptible per-file cost.

@1026 (#311): `is not None` guards in both files; opt-in isolate=True runs
each diagnose in a child killed at the deadline (_KILL_GRACE_S=1.5, kills
counted as killed_in_flight, budget_exhausted labelled); _cached single-flight
per key, timestamp post-compute. RED-first 8 of 12 proven failing for stated
reasons; band 326 passed (41 files); mutation battery 9 caught / 0 escaped.
A 22-agent pre-merge adversarial review then confirmed three of its own
findings into the cut (the wiring test's budget_s blind spot, the
remaining-vs-fresh-budget blind spot, the child cwd -- each now a test, the
two testable ones as battery mutants) and surfaced a PRE-EXISTING defect it
initially misattributed to the cut: the drift axis's default gold join NEVER
fires from diagnose() -- build_template nests host at source.host while
_default_gold reads top-level -- measured identical from repo cwd on the real
store. Filed separately with the fix direction and the subdomain-join
question; @1026's child-cwd fix makes the isolated path follow the moment it
lands. Measured after, same store: **5.058s wall, 46 real rows (was 1),
1 kill**.

#### SUPERSESSIONS of 15.85 claims

- "capture_diagnostics took 6181ms serial -- under 8s at 77%": was true at
  e7d3b5e and is ~2.8x stale; the corpus rework armed the expensive files
  afterward. The figure now lives only as history.
- "The CI generated-artifact denominator -- six enumerated, seven produced":
  CLOSED at @1027 on operator GO. STATIC_KB_MANIFEST.json enters ci.yml's
  generated=() array. Re-derivation note: the sharper mechanism is that
  test_v3_66_939's _DECLARED set is hand-pinned (deliberately, and correctly),
  so a suite never DECLARED is invisible to the one gate watching for dropped
  suites -- 944/947 were never added.
- "Two order-dependent test files ... both real": needs reconciling with
  CLAUDE.md section 5's v3.66.1024 measurement, which reclassifies both as
  BD_INSTALL_DIR-leak artifacts (exported: 4F/2F -- popped: 115P/11P). This
  session's evidence: test_v3_66_820... passed inside @1026's 41-file
  co-band (popped), 326/326. What was NOT re-run: either file alone-whole
  with the variable popped. The two texts are consistent under the leak
  reading; 15.85's "order-dependent" framing is the one to retire.
- "main at 0db578f is UNVERIFIED on the box": superseded -- 6728dc8 captured
  on test5 this session; the failures were host gaps + the @1026 defect, all
  dispositioned above.

#### HOST EVENTS -- test5 (7b4ea932c297), all on explicit operator grants

- requirements-test.txt installed by hand (pyflakes, pytest-timeout,
  psycopg); both manifests grade exit 0; durable fix is @1028.
- ollama + qwen2.5:7b + qwen2.5vl:7b installed via install_ai_ollama.sh
  (exit 0, CPU mode); /api/ai/status ok=true; L17's condition cleared.
- The operator's 2.5GB capture corpus (924 files) rsync'd ~/captures ->
  repo captures/ (mtimes preserved, byte-total verified, gitignored) --
  the collectors' searched_dirs are repo-relative, so at ~/captures the
  store was structurally invisible and every heavy route measured an EMPTY
  denominator. On test4 the corpus lives repo-relative, which is why no
  runbook row existed: migration never had to move it before.
- install_remote_teach.sh applied: all four bd-* units active, noVNC on
  :6080. Its bd-xvfb collided with the provisioner's RAW `Xvfb :99`
  (PID-owned, not unit-managed); resolved by killing the raw process and
  starting the unit -- the durable owner survives reboot. The collision is
  a real seam between provision_test_host.sh and install_remote_teach.sh;
  documented in the runbook @1029 rather than papered.
- /var/run/reboot-required never appeared: no reboot.
- test4 remains deployed at 3.66.1023 (process; tree 0547799) -- two cuts
  behind main at session start, more after these cuts. Deploys are the
  operator's.

#### THE CUTS

| cut | what |
| --- | --- |
| @1026 (#311) | the heavy-collector kill bound, both falsy-zeros, single-flight cache |
| @1027 (#312) | STATIC_KB_MANIFEST.json enters ci.yml's generated=() -- the seventh output |
| @1028 (#313) | provision_test_host.sh installs + resolution-checks requirements-test.txt |
| @1029 (#314) | FRESH_HOST_BRINGUP.md: corpus migration row (repo-relative!), test-manifest step, ollama step, remote-teach + the :99 seam |
| @1030 (#315) | this section; item 33 inventory text 240 -> 238; CLAUDE.md section 8's tool count corrected to follow its own measure-at-decision-time rule |

#### METHOD COSTS -- what an agent should know

- My own discovery-cost hypothesis was WRONG (guessed ~11s; measured
  0.03s) -- the workflow's independent probes corrected it before it
  reached a commit. The expensive thing was the single diagnose, not the
  walk. Fan-out verification earns its tokens exactly here.
- The route-vs-direct wall difference (17.36 vs 16.26s) is ~1s of server
  overhead; cache_age_s on a fresh entry equalled the compute wall because
  the timestamp was set at compute START -- fixed in @1026, and worth
  knowing when reading old captures' cache_age values.
- One workflow reviewer evidence-error was itself caught by the synthesis
  verifier: a "zero grep hits" claim refuted by a prose mention in the
  @1023 test's docstring. Subagent output is data, not evidence (2b), and
  the verify tier is what makes it usable.
- Item 33's denominator measured three ways at ac687c0: ls 238, git
  ls-files 238, _TOOL_BUDGET 238 (test_toolchain_534.py:1084). CLAUDE.md
  section 8 said 240; corrected this cut.

#### WHAT IS STILL OPEN

1. **Queue item 4**, the match.hosts bridge -- still blocked on the operator
   reviewing `bd-wacz-corpus --hosts`.
2. **Item 31**, the eight operator-bound rows (15.36 item 31 has the list).
3. **Item 33**, the prose-only pool ratchet, denominator now 238.
4. **A clean-host end-to-end proof of the fixed bring-up** (bare Ubuntu ->
   green capture, zero hand-fixes) -- operator offered a box; worth taking
   after @1028/@1029 deploy.
5. **A fresh capture on test5 at the post-@1026 tree** -- the fix is
   measured at the collector and the route; the capture-lane proof runs
   after deploy.

### 15.85 | SESSION CLOSE 2026-08-10 at 0db578f (v3.66.1022) -- seven cuts, four box captures, and two defects only the box could find

Close at `0db578f`, the squash of #306, already on `main` when this was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33
CLOSED: 44

Unchanged from 15.82: nothing this session opens or closes a NUMBERED item. The
queue it worked is 15.83's, which is not the numbered inventory.

#### THE QUEUE, FINAL STATE

| 15.83 item | state |
| --- | --- |
| 1. capture of @1015 | DONE -- and the first tarball was of @1013; see below |
| 2. item E, the interstitial | DONE, both halves (@1016 runtime, @1017 builder) |
| 3. the eleven registrable-domain copies | DONE (@1018), ratchet now ZERO |
| 4. the `match.hosts` bridge | STILL BLOCKED on the operator reviewing `bd-wacz-corpus --hosts` |
| 5. logger handler accumulation | DONE (@1021) |
| 6. item 31 | untouched |

#### THE CUTS

| cut | what |
| --- | --- |
| @1016 | the post-login interstitial: two declared scopes, one shared loop |
| @1017 | a CAPTURED template can express its interstitial |
| @1018 | the eleven last-two-labels copies, drained; ratchet 11 -> 0 |
| @1019 | re-freeze the TEMPLATES identity baseline for the gamma_kosmos split |
| @1020 | three residues in already-merged work |
| @1021 | log._init appended to a global it did not own |
| @1022 | a test wrote into the TRACKED corpus |

#### THE BOX FOUND TWO THINGS NO BAND COULD, AND ONE OF THEM HAS NO PASS/FAIL LINE

**1. A gate no derivable band reaches.** @1016's gamma_kosmos split drifted
`tools/decomp/templates_snapshot_baseline.json`. Measured: `bd-band-derive
--file bulk_downloader/site_templates/_data_players.py` does NOT return
`tests/test_templates_list_identity.py`, and neither of @1016/@1017's bands (424
and 67 files) contained it. The gate's subject IS that file's content, but it
reaches it ONE IMPORT AWAY, via `spec_from_file_location` on
`tools/decomp/templates_snapshot.py` -- invisible to a grep of the test's own
source and to the module-consumer signal alike.

**This is the SECOND instance of a class CLAUDE.md section 4 records once**, for
`test_pin_index_in_sync`, whose enumeration also lives one import away. Two
instances is a class, not a one-off. There is no automatic predicate for it --
the enumeration is not in the test file at all -- so the practical rule is: a
cut that edits a data list which some frozen baseline hashes must band that
baseline's gate by hand.

**2. A SKIP COUNT, which no verdict line shows.** @1018's
`test_the_rate_limit_key_is_the_registrable_domain` did
`getattr(R, "_extract_domain", None)` and skipped when that came back None. It
is a `@staticmethod` on `DomainRateLimiter`, never a module attribute, so the
getattr ALWAYS returned None and both behavioural assertions never ran. The
capture at `e7d3b5e` is the whole evidence: skips went **4 -> 5** the moment
@1018 landed, and the fifth is that test -- with a stated reason ("nested")
that is also wrong.

Nothing else would have caught it. It is green in every band, green in CI,
green on the box, and reported as a pass. **Read the skip count across
captures, not just the fail count**, and treat any NEW skip as unexplained
until named.

#### THE CAPTURE SEQUENCE, AND THE ONE THAT MEASURED THE WRONG TREE

| capture | commit | result |
| --- | --- | --- |
| 1 | `fe88b5a` (@1013) | FAIL -- L34 `capture_analytics` EXCEEDED >8s serial |
| 2 | `213fa81` (@1017) | FAIL -- 2 unit: templates identity + the corpus race |
| 3 | `e7d3b5e` (@1019) | **PASS** -- unit 15495/0, live 36/0 |

**Capture 1 was of @1013, not @1015**, because the deploy had been REFUSED at
step 3 on a local modification to `STATIC_KB_MANIFEST.json` and nobody noticed
before running it. So `fe88b5a` now stands at three captures, **2 FAIL / 1
PASS on identical source** -- stronger evidence for @1015 than @1015's own
commit message could cite. **Check `02_SUMMARY.txt`'s version line before
reading any capture**; the verdict line does not say which tree it measured.

**@1015 MAY STILL NOT BE ENOUGH, and capture 3 does not settle it.**
`_HEAVY_BUDGET_S = 20` (`app_data_layer.py:92`) against `_L34_ROUTE_BUDGET_S = 8`
(`live_tests/checks.py:311`): the wall-time bound is 2.5x L34's budget, so it
guarantees the route TERMINATES, not that it answers in time. What should have
fixed capture 1 is `max_bytes=25MB` skipping an oversized capture JSON. The
sibling `capture_diagnostics`, which has had all three bounds all along, took
**6181ms serial** -- under 8s at 77% of it. A pass here is not headroom.

#### MEASUREMENTS WORTH NOT RE-DERIVING

- **The interstitial cost, in BOTH shapes.** 15.83 said five login-wall
  selectors cost "up to 15s PER URL", flagged READ FROM SOURCE. Measured against
  a real chromium on a page where none match: the shipped Gamma value is ONE
  comma-joined line -> 1 locator -> **3.00s**; the same five as five lines ->
  **15.01s**. `runner.py` splits on NEWLINES. So the split is cost-NEUTRAL for
  Gamma (its win is correctness) and the 3s-per-line saving lands for CAPTURED
  templates, which emit one per line. Quoting the wrong figure overstates it.
- **The auto-submit stall: 551s -> 15s.** A form that auto-submits on password
  fill can land on the WALL, which is not `success_url`, so do_login's early
  return does not fire -- and the wall has no login form, so `_submit_login`
  walks its whole fallback list. The pristine log reaches method 8 of 9
  (Tab+Enter) before anything takes.
- **The logger leak is QUADRATIC, not linear.** Handlers 2N (which 15.83 had),
  logger filters N, **handler filters N(N+1)** -- 56 at seven wipe cycles --
  because the filter loop decorated every handler ON THE LOGGER rather than the
  two just installed. One `.info()` printed **28 lines** across those cycles.
  After @1021: flat at 2/1/2, and seven calls print seven lines.
- **The corpus race is CONCURRENCY-dependent, not order-dependent.**
  Polluter-then-victim in one process PASSES, because the `finally:` restores
  first. It needs genuine overlap, which is why it surfaces on an 88-worker box
  and essentially never in a container -- and why re-running would never have
  proved it fixed.
- **`main` was carrying a stale generated artifact and CI cannot see it.**
  `STATIC_KB_MANIFEST.json` at `987e960` recorded `SESSION_CARRY.md` 4317 bytes
  short -- exactly 15.83's own length, regenerated before the section was
  written. `ci.yml:86-89` enumerates SIX artifacts; `bd-regen-order` has SEVEN
  tracked outputs, and this is the one missing. The job runs the regen that
  updates the file and then checks a denominator excluding its output. Instance
  fixed by @1016; **the CLASS is still open** and is a build change needing the
  operator.

#### DEFECTS IN THIS SESSION'S OWN WORK -- five, none caught by review

Every one was caught by RUNNING something, and three by a check failing on
correct code.

1. **An AST census over-matched and reported an offender that never existed.**
   @1016's "exactly one dismissal loop" predicate asked only for a loop
   containing `wait_for` + `click` + `locator`, and flagged `_process_one`'s
   download TRIGGER loop -- a different thing that happens to click a selector
   it waited for. The instrument was right; the SUBJECT was wrong. Narrowed to
   a splitlines-driven loop and proved on a known positive AND a known negative.
2. **A test that passed on BOTH sides.** @1020's first e2e for the auto-submit
   stall asserted `ok is True` and passed on pristine -- because `_submit_login`
   eventually clicks something and @1016's post-submit dismissal clears the wall
   anyway. Section 6: a test that passes in both states is not a test. The
   defect is the WALK, so the assertion became whether `_submit_login` is
   entered at all: control flow, not a clock.
3. **The prose-vs-code trap, inside the cut about residues.** A test asserting
   the fixed function no longer contains `getattr(R, "_extract_domain")` FAILED,
   because that function's own docstring quotes the call in order to explain
   what was wrong; `ast.unparse` renders docstrings as ordinary strings.
   Section 0's "explaining a removal by naming the removed thing recreates it",
   committed minutes after writing that sentence down.
4. **A ratchet nothing checked.** @1018's mutation battery escaped on "raise
   @1013's ceiling from 0 back to 11" -- the behaviour was constrained by an
   independent census, the RATCHET was not. @1013's docstring says "Never raise
   it" in prose; that is now mechanical, read from the assert NODE. The first
   version of THAT predicate matched `assert len(files) > 1000` as well, because
   it never checked the OPERATOR, and reported the ceiling as 1000.
5. **gitleaks failed the `gates` job on the test written to prove tokens never
   leak.** `token=abcdef...` (16 hex) scored generic-api-key at entropy 4.0.
   Section 7 already says corpus values must be zero-entropy repeats; it also
   says this cannot be fixed forward, and it could not -- the commit had to be
   amended. The repo's pre-push hook then correctly refused the force-push (its
   two-dot diff is non-empty for ANY unmerged branch, so it is a false positive
   for an amend of one's own tip); the safe move is
   `git diff origin/<branch> HEAD` to prove nothing is lost, THEN
   `BD_SKIP_PREPUSH_CHECK=1`.

#### METHOD COSTS

- **A 10-minute command timeout killed a mutation battery and left the mutant
  on disk.** Section 6's SIGTERM case, live: `runner.py` matched the mutated
  sha256 exactly, and @875's journal recovered it. **Run batteries
  BACKGROUNDED with no command cap.** Every unattended way a battery dies is a
  way it dies dirty.
- **`bd-band-derive` missed 8 of the 11 axis-6 gates** for a cut adding a test
  file. Its own docstring calls itself a floor; that is the size of the gap.
  `bd-bandcheck` separately caught the `test_phases_195_199` + `test_cut8_
  schedules` leak co-band on a band the tool itself derived.
- **A `downloader_history.db` (225 KB) appeared in the repo root**, item 36's
  signature, written during a pytest BAND rather than an ad-hoc probe -- every
  hand-rolled probe this session scoped `BD_INSTALL_DIR`, and the band runs
  relied on conftest. Gitignored, so `git status` stayed clean and nothing
  warned. Removed.
- **Two order-dependent test files were found and NOT fixed**, both proven
  pre-existing by changing one variable in the same directory:
  `tests/test_provision_test_host.py` gives `4 failed, 117 passed, 13.86s`
  byte-identically with and without @1021, and
  `tests/test_v3_66_820_auth_health_reaped_on_site_delete.py` fails 2 when its
  file runs whole. Neither is this session's; both are real.

#### WHAT IS STILL OPEN

1. **Queue item 4**, the `match.hosts` bridge, blocked on the operator running
   `bd-wacz-corpus --hosts` and confirming the families.
2. **Item 31**, the large parallel program.
3. **The CI generated-artifact denominator** -- six enumerated, seven produced.
   A build change; needs authorization.
4. **The two order-dependent files above.**
5. **`main` at `0db578f` is UNVERIFIED on the box.** The last capture was of
   `e7d3b5e`, three cuts back.

### 15.84 | Item E is BUILT (v3.66.1016-1017), and five measurements the queue did not have

**Not a session close** -- no ITEM LEDGER; 15.82's (OPEN 31, 33 / CLOSED 44)
stands. Deliberately not titled a close: section 4 requires a close section to
name a commit ALREADY on `main`, and everything here lives on an unmerged
branch whose tip the squash destroys.

**ITEM E IS DONE, both halves, in PR #303.** @1016 is the runtime (two declared
scopes, one shared loop, `do_login` firing the wall once); @1017 is the builder
(`_dismiss_selectors` -> `selectors["dismiss"]` ->
`capture_login_wire.apply_draft_dismiss_selectors`). Queue items 2 is closed;
1 was answered rather than worked (below); 3, 5 and 6 are untouched.

#### THE CAPTURE SENT ON 2026-08-10 WAS OF v3.66.1013, NOT v3.66.1015

`01_sysinfo.log` says `fe88b5a`; `02_SUMMARY.txt` says 3.66.1013. @1014 and
@1015 merged at 18:26Z and the capture ran at 18:43Z on a box that had not been
updated -- the deploy was attempted afterwards and REFUSED at step 3 on a local
modification to `STATIC_KB_MANIFEST.json`. So the tally at `fe88b5a` is now
**three captures, 2 FAIL / 1 PASS on identical source**, which is stronger
evidence for @1015 than the two captures its own commit message could cite.

The discriminating line, cold and serial, sweep complete, graph pin OK:

    EXCEEDED  /api/data/capture_analytics (> 8s SERIAL, on a quiet app)
    checked 264 operator in 67s: 0 5xx, 0 unreachable, 1 exceeded,
    181 recovered-on-serial, 0 unconfirmed, 0 unprobed

**@1015 MAY NOT BE ENOUGH, AND THE NUMBERS SAY WHY.** `_HEAVY_BUDGET_S = 20`
(`app_data_layer.py:92`) against `_L34_ROUTE_BUDGET_S = 8`
(`live_tests/checks.py:311`) -- the wall-time bound is **2.5x L34's budget**, so
it guarantees the route TERMINATES, not that it answers in time. The mechanism
that should actually fix this failure is `max_bytes=25MB` skipping the oversized
capture JSON, which is the cost @1015's own message names. Corroborating datum
from the same capture: the sibling `capture_diagnostics`, which has had all
three bounds all along, took **6181ms serial** -- under 8s, at 77% of it. Expect
a pass, not a comfortable one, and do not read one as headroom.

#### THE INTERSTITIAL COST FIGURE, MEASURED -- 15.83 WAS HALF RIGHT

15.83 said five login-wall selectors cost "up to 15s PER URL" and flagged it
READ FROM SOURCE. Against a real chromium on a page where none of them match:

| shape | locators | per URL |
| --- | --- | --- |
| the shipped Gamma value (ONE comma-joined line) | 1 | **3.00s** |
| the same five as five lines (a captured template's shape) | 5 | **15.01s** |

`runner.py` splits on NEWLINES. So the hand-written one-liner has always cost
3.00s and the split is **cost-neutral for Gamma** -- its win is purely
correctness. The 3s-per-line saving is real and lands for CAPTURED templates,
which emit one selector per line. Two different shapes, one figure, and quoting
the wrong one overstates the cut.

#### `main` CARRIES A STALE GENERATED ARTIFACT AND CI CANNOT SEE IT

`STATIC_KB_MANIFEST.json` at `987e960` records `SESSION_CARRY.md` at 637944
bytes while the SAME commit's file is 642261 -- the 4317 bytes of 15.83 itself.
Section 2a's "regen AFTER the last source edit", landed on main.

**CI structurally cannot catch it.** `ci.yml:86-89` enumerates six artifacts:

    ROUTE_INDEX.json ENDPOINT_CATALOG.md DEPENDENCY_GRAPH.json
    DEPENDENCY_GRAPH.md FUNCTION_INDEX.md PIN_INDEX.json

`bd-regen-order` has SEVEN tracked outputs; `STATIC_KB_MANIFEST.json` is the
one missing. The job runs the regen that updates the file and then checks a
denominator that excludes it -- section 0, in the release machinery. @1016 fixes
this instance incidentally; the CLASS recurs until the list is derived from the
chain rather than hand-kept. **Not fixed here: changing a CI job is a build
change and needs the operator.**

#### THE LOGGER LEAK IS QUADRATIC, NOT LINEAR (queue item 5, MEASURED not built)

Measured at `987e960`, 7 wipe cycles in one process:

| inits | handlers | logger filters | handler filters |
| --- | --- | --- | --- |
| 1 | 2 | 1 | 2 |
| 7 | **14** | 7 | **56** |

Handlers 2N confirms 15.83's 0 -> 14. Handler FILTERS are **N(N+1)**, which
nothing recorded, and the visible cost is worse than either: one `.info()` call
printed **28 lines** across the 7 cycles, because every surviving StreamHandler
re-emits it. Multiple RotatingFileHandlers also rotate the same file
independently.

**THE ONE-LINE FIX 15.83 PREDICTS WOULD BREAK A TEST'S SUBJECT.**
`tests/test_v3_66_942_integrity_check_path_survives_a_cwd_change.py`
deliberately clears `_INITIALIZED` to FORCE a re-init, and saves/restores
`root.handlers` around it. A guard that makes `_init()` a no-op when the logger
already has handlers destroys exactly what that test exists to exercise. So the
fix must make `_init()` REMOVE and close the handlers a previous incarnation
installed (tag them) rather than refuse to run -- and note that the 942 test
closes the handlers it did not save, so the interaction needs checking rather
than assuming.

#### METHOD, FOUR THINGS THAT COST TIME

- **`bd-band-derive` missed 8 of the 11 axis-6 gates** for a cut adding a test
  file. Its own docstring calls itself a floor; this is the size of the gap.
  `bd-bandcheck` separately caught the `test_phases_195_199` + `test_cut8_
  schedules` leak co-band that section 4 names, on a band derived by the tool.
- **A 10-minute command timeout killed a mutation battery and left the mutant on
  disk** -- section 6's SIGTERM case, and every unattended way a battery dies.
  `runner.py` matched the mutated sha exactly; @875's journal recovered it.
  Run batteries BACKGROUNDED with no command cap.
- **gitleaks failed the `gates` job on the test written to prove tokens never
  leak.** `token=abcdef...` (16 hex) scored generic-api-key at entropy 4.0.
  Section 7 already says corpus values must be zero-entropy repeats; it also
  says this cannot be fixed forward, and it could not -- the commit was amended.
  The repo's pre-push hook then correctly refused the force-push (its two-dot
  diff is non-empty for any unmerged branch), which is a FALSE POSITIVE for an
  amend of one's own tip; proved nothing was lost with
  `git diff origin/<branch> HEAD` before using `BD_SKIP_PREPUSH_CHECK=1`.
- **My own AST census was wrong first**, over-matching `_process_one`'s download
  TRIGGER loop. The instrument was right and the subject was wrong -- section 1,
  in a cut written by someone who had just read it.

### 15.83 | THE WORK QUEUE after v3.66.1015, in the operator's stated order

**Not a session close** -- no ITEM LEDGER; 15.82's ledger (OPEN 31, 33 /
CLOSED 44) stands unchanged. This exists because a fresh session should not have
to re-derive the order from prose, and because the operator set it explicitly on
2026-08-10.

**THE BOX IS GREEN AT v3.66.1013 (`fe88b5a`)**, first fully clean capture of the
sequence:

    PASS - unit 15393 pass/0 fail/0 error/4 skip; live 36 pass/0 warn/0 fail

Two captures before it read FAIL on `graph exit=1` ALONE, with unit and live
both clean -- the graph content pin lives under `/var/lib/`, outside the repo,
so `git reset --hard` never delivers it and a new module makes it stale.
`scripts/deploy.sh` re-pins automatically; a by-hand deploy does not. **If a
capture fails on graph exit only, that is bookkeeping, not a defect.**

#### THE QUEUE

1. **A capture of v3.66.1015.** @1015 is unverified on the box by construction:
   it was measured in-container and the route it fixes passed its last two
   captures ON A WARM CACHE. See the note below on what to look for.
2. **Item E -- the interstitial.** The last feature in 15.74's A-H program, and
   its design is DECIDED, so it needs no further discussion:
   - captured templates learn to emit `dismiss_selectors`;
   - login-wall selectors fire ONCE in `do_login`;
   - per-page selectors (cookie / age / consent) keep firing per URL;
   - templates declare which is which.
   The runtime consumer already exists -- `runner.py:3332` reads
   `dismiss_selectors`, one selector per line, silent on misses, and the Gamma
   brands use it by hand today via `site_templates/_data_players.py`. What is
   missing is only that a CAPTURED template cannot produce that value. Note the
   per-URL cost that motivated splitting them: each selector waits up to 3s for
   an element that may not exist, with no early exit, so five login-wall
   selectors cost up to 15s PER URL once past the wall. **That figure is READ
   FROM SOURCE, not measured** -- measure it before quoting it as a saving.
3. **The eleven remaining registrable-domain copies.** @1013 migrated the two
   that gate a fetch; the rest are correctness-only. Ratcheted at 11 by
   `tests/test_v3_66_1013_registrable_domain.py`, which can only shrink. They
   live in `extractors_aylo`/`vixen`/`dl8`, `phoenix_catalog`,
   `candidate_filter`, `extension_vault`, `host_enumerator`,
   `login_templates_data` (x2), `rate_limit`, and `tools/player_struct_embed`.
   Each wants its own band.
4. **The `match.hosts` bridge for item A -- AFTER the operator reviews the
   families.** @1014 ships the CANDIDATE tier only, deliberately. The bridge is
   15.74's design: write the login host into the CONTENT template's
   `match.hosts`, a runtime tier the matcher already supports and nothing
   writes. Do NOT merge cross-host drafts; bd-template-merge's single-host guard
   is correct. Run `bd-wacz-corpus --hosts` on the box to see the proposals.
5. **The logger handler accumulation.** `log._init()` adds to
   `logging.getLogger("bulk_downloader")`, a stdlib global that survives
   `bd_module_wipe`, and never clears: 0 -> 14 over one file, identical before
   and after @1008 so it predates that cut. Likely one line plus a
   session-scoped handler-count assertion of the shape used to prove @1008
   leaked nothing.
6. **Item 31**, the large parallel program (15.15 / TASK_TRACKER, eight rows).

#### WHAT TO LOOK FOR IN THE NEXT CAPTURE

`/api/data/capture_analytics` is the subject. It FAILED capture 7 and PASSED
capture 8 **at the same commit** -- the difference was `_HEAVY_TTL_S = 600`, a
response cache, not the code. So a PASS alone does not confirm @1015: it
confirms either the fix or a warm cache, and those are indistinguishable from
the verdict line. The discriminating evidence is in `06_live_results/L34.log`,
which the capture has carried since @1009 -- look for the route's serial timing
rather than the verdict.

#### THREE GATES BAND A REGISTER EDIT, AND ONLY TWO ARE WRITTEN DOWN

`bd-freshcheck`, `test_toolchain_534` -- both named in CLAUDE.md section 4 --
and `tests/test_register_promises_resolve.py`, which is named nowhere and
failed three tests on the box at @1012 for a title that promised a ledger it did
not carry. Band all three.

### 15.82 | SESSION CLOSE 2026-08-10 at 8f17c5d (v3.66.1014) -- seven cuts, four box captures, and the gate that started finding things

Close at `8f17c5d`, the squash of #299, already on `main` when this was written.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33
CLOSED: 44

**44 was already closed in the inventory at v3.66.983 and 15.70's ledger still
listed it OPEN.** Both tests pass either way -- `closed_in_inventory` accounts
for it -- so nothing was failing, and a reader got two answers. Declared here so
the ledger and the inventory agree. 33 remains "a ratchet, not a target"; 31 is
the large parallel program and is untouched by this session.

#### THE CUTS

| cut | what |
| --- | --- |
| @1008 | two tests asserting over ambient process state they never established |
| @1009 | the capture bundles `live_tests/results/`; a changelog entry that shipped twice |
| @1010 | L34's triage budget is what phase 1 can AFFORD, not a constant |
| @1011 | the real-Postgres MOD3 modules stop sharing one `history` table |
| @1012 | two operator routes that could not answer in 8s on a quiet app |
| @1013 | one correct registrable-domain rule; the two same-site predicates use it |
| @1014 | a labelled CANDIDATE `site_families` tier over the exact-host buckets |

#### THE ARC IS THE POINT, AND IT ONLY WORKS IN THAT ORDER

L34 had been failing every capture on **nothing**: `0 5xx, 0 unreachable, 0
exceeded, 47 recovered-on-serial, 92 unprobed`. @1010 made it able to sweep all
264 operator routes, and the very next capture adjudicated two of them as
genuinely slow -- routes that had been inside the 92 nobody probed. @1012 fixed
those at the cause, and the capture after that was `36 live pass / 0 fail`.

**A gate that cannot finish its sweep reports the wrong thing twice**: it fails
on its own budget, and it hides what it never reached. Neither symptom looks
like the other, and the second is invisible until the first is fixed.

#### WHAT THE BOX FOUND THAT NO CONTAINER DID

- **The "one in three" MOD3 flake was 10-in-10.** One capture in three failed
  `test_agreeing_stores_compare_and_match`; reproduced in-container, pristine
  failed **10 of 10** runs under concurrency and isolated failed **0 of 10**.
  Systematic, not stochastic -- the box's 73-worker interleaving dodged it.
  **A failure that "passes on retry" has a reproduction rate, and until it is
  measured nobody knows whether it is 1-in-3 or 10-in-10.**
- **A register gate no band names.** @1013's capture failed three tests in
  `tests/test_register_promises_resolve.py` because 15.81 was titled a session
  close and carried no ITEM LEDGER. Section 4 says a doc edit bands the
  freshness gates; `bd-freshcheck` and `test_toolchain_534` were derived and
  both green. **Editing this register bands THREE gates, and only two are
  written down anywhere.**

#### DEFECTS IN THIS SESSION'S OWN GATES -- six, none caught by review

Recorded together because the pattern is the finding: every one was caught by
running the check, and several by it passing when it could not have.

1. **@1013's census certified four unedited files.** The routing predicate
   accepted a call to `ensure_schema` -- also a function on the product's
   `pg_backend`, already called by all four modules. Noticed only because it
   went green BEFORE the edits existed.
2. **@1013's census then failed CORRECT code.** The migrated functions'
   docstrings necessarily describe the pattern they removed, and the census
   unparsed each node whole. Section 0's "explaining a removal by naming the
   removed thing recreates it", fifth recorded instance. Now strips docstrings
   and asserts both directions.
3. **@1013's ratchet counted the canonical module**, whose documented fallback
   legitimately uses that shape -- making the ratchet unsatisfiable.
4. **@1012's fixture stubbed FIVE finders and asserted five** while `summary()`
   calls SIX; the sixth would have run against the real filesystem while the
   test claimed to have replaced every finder.
5. **@1014's escape needed two rounds.** Deleting the no-registrant guard left
   the band green; the first closing test ALSO escaped, because it used an empty
   host which a DIFFERENT guard drops -- so it exercised the wrong one of two
   guards that both produce the right answer.
6. **@1011's mutation escape was hidden by residue.** "ensure_schema never
   called" scored green because every schema the suite names already existed
   from an earlier run. On a fresh database it is an immediate hard failure.

#### MEASUREMENTS WORTH NOT RE-DERIVING

- **Thirteen registrable-domain copies, not nine.** A name grep found nine; an
  AST census on the SHAPE found 13 over 2164 tracked `.py` files. Two gated a
  fetch and are migrated; **eleven remain, ratcheted** in
  `tests/test_v3_66_1013_registrable_domain.py`.
- **The naive rule was a scope escape, not a cosmetic error.** Measured on
  shipped code: `victim.co.uk` vs `attacker.co.uk` -> same site; two unrelated
  `github.io` pages -> same site.
- **The siteid pairing for item A is REFUTED by the real corpus.**
  `{host}_{siteid}_{YYYYMMDD}` appears on 2 sources of roughly 600
  (`auth.reptyle.com_0b60f1ec_...`, `pexels.com_1a820331_...`); the rest are
  nicknames. It exists and is far too rare to key on.
- **CLAUDE.md's CI-budget note is STALE.** It says the `gates` job's budget is
  breached and the decision open. `ci.yml:212` records the rule ("81 tests, 52s
  -- keep it under a minute; if it grows past that, SPLIT") and @939 DID split
  it. Measured 2026-08-10: `gates` 39s, shards 49-78s, all inside budget. The
  decision was made and executed; the contract did not hear about it.

#### 15.74'S SEVEN FINDINGS, FINAL STATE

A addressed by @1014's candidate tier (the BRIDGE -- writing the login host into
the content template's `match.hosts` -- is deliberately NOT built, because it
should follow the operator confirming the proposed families). B closed @989,
C @1002, D @1006, G @988, H @987. **E is the only one still open**, and its
design is decided: captured templates emit `dismiss_selectors`, login-wall
selectors fire once in `do_login`, per-page selectors keep firing per URL,
templates declare which is which.

#### THE BACKLOG, IN THE ORDER IT SHOULD BE TAKEN

1. **E** -- the interstitial, above. The last feature in the A-H program.
2. **The eleven remaining registrable-domain copies.** Correctness-only, none
   security-relevant the way the two were; each wants its own band.
3. **The `match.hosts` bridge for A**, after the operator reviews the families.
4. **Logger handler accumulation** -- `log._init()` adds to a stdlib global that
   survives `bd_module_wipe` and never clears: 0 -> 14 over one file, identical
   before and after @1008. Fix is likely one line plus a session-scoped
   handler-count assertion.
5. **Item 31**, the large parallel program.

### 15.81 | v3.66.1008-1013: three box captures, the 10-in-10 flake, and 15.74's seven findings RE-DERIVED

**Not a session close** -- no ITEM LEDGER; the standing open set (31, 33, 44)
is unchanged and nothing here opens or closes a numbered item.

THE FIRST VERSION OF THIS SECTION WAS TITLED "Session close", AND THE BOX CAUGHT
IT. `tests/test_register_promises_resolve.py` requires a close section to carry
a machine-readable ITEM LEDGER; three of its tests went red in the v3.66.1012
capture on a register that was otherwise correct. Neither CI nor the derived
band reached it: editing the register bands `bd-freshcheck` and
`test_toolchain_534`, which is what section 4 says and what was run -- and
ALSO this suite, which nothing said. A title is an assertion here, and calling
something a close promises a ledger.

Anchored at `342001e`, a squash already on `main` when this was written, per the
section 4 rule that naming your own branch tip passes every pre-merge check and
then goes red on `main`.

#### THE CAPTURES SAID PASS, AND THE PROGRESSION IS ONE CAPTURE PER CUT

| capture | commit | unit | live |
| --- | --- | --- | --- |
| 1 | `d2fa6bb` (1008) | 15312 pass / 1 fail | L34 FAIL |
| 2 | `ab9cbcb` (1009) | 15331 pass / 0 fail | L34 FAIL |
| 3 | `342001e` (1010) | 15340 pass / 0 fail | 36 pass / 0 fail -- **PASS** |

Box skips are now TWO environmental (`netns` needs root; the self-retiring
`BD_REPO_CANDIDATES` dead-branch guard) plus two deliberate parametrize skips
added by @1010, each naming its reason. All 43 MOD3 tests RAN -- the operator's
`MOD3_PG_TEST_DSN` is live on the box.

L34 at @1010, from its own log now that the capture carries it:

    phase 1 budget: 40s x 8 workers = 317 worker-seconds for 264 operator
      route(s) -> triage 1.20s each (ceiling 5s, floor 1.0s)
    phase 1 flagged 154 route(s) in 26s ... (46s of wall left)
    checked 264 operator in 66s: 0 5xx, 0 unreachable, 0 exceeded,
      154 recovered-on-serial, 0 unconfirmed, 0 unprobed

172 -> 264 swept, 92 -> 0 unprobed, phase 1 43s -> 26s. Suspects rose 47 -> 154
exactly as intended: a flag is not a finding, and phase 2 cleared all 154.

#### THE ONE-IN-THREE FLAKE WAS 10-IN-10, AND THAT IS THE LESSON

Capture 1's lone unit failure was
`test_v3_66_801_mod3_shadow_read::test_agreeing_stores_compare_and_match`
(`{'compared': 2, 'matched': 0, 'diverged': 2}`), passing in the other two. Read
as a flake, that earns a shrug. Reproduced in-container against a live cluster,
same command, same directory, ONE variable changed:

    pristine (shared public.history)   10 of 10 runs FAILED (3-4 each)
    isolated                            0 of 10 runs failed (38 passed each)

Systematic, not stochastic -- the box's 73-worker interleaving simply dodged it
most of the time, and three different modules fail on pristine. **A failure that
"passes on retry" has a reproduction rate, and until you measure it you do not
know whether it is 1-in-3 or 10-in-10.** Fixed at @1011 by a schema per module.

#### AN OPEN FINDING, RECORDED AND NOT FIXED

`bulk_downloader/log.py` `_init()` ADDS handlers to
`logging.getLogger("bulk_downloader")` -- a stdlib global that survives
`bd_module_wipe` -- and never clears them. Measured over one wiped file: **0 ->
14 handlers**, identical before and after @1008's repair, so @1008 leaks none and
the accumulation predates it. Each is a `RotatingFileHandler` holding an open fd
plus a `StreamHandler`, so a long worker doubles-and-redoubles what it writes.
Not chased because nothing depends on it today and it is a different subject
from the cut that found it. Whoever takes it: the fix is almost certainly to
clear `root.handlers` at the top of `_init()`, and the test is a session-scoped
handler-count assertion of the shape used to prove @1008 leaked nothing.

#### 15.74'S SEVEN FINDINGS, RE-DERIVED FROM SOURCE 2026-08-10

Re-derived rather than read off, because that is what section 1 requires and
because three of these closed in cuts whose numbers do not appear in 15.74.

| item | subject | status |
| --- | --- | --- |
| A | grouping splits every site | **OPEN, blocked on the operator** |
| B | modal-scoping discards download panels | CLOSED @989 |
| C | `text=/Download/i` green on a heading | CLOSED @1002 |
| D | honeypot scorer never called | CLOSED @1006 |
| E | no post-login interstitial step | **OPEN, needs re-specification** |
| G | merge corrupts list-valued selectors | CLOSED @988 |
| H | `_gate_support` raw vs normalized | CLOSED @987 |

The measurements behind each verdict:

- **B.** `_is_modal_scoped` still answers False for `.download-block a.dl` and
  `div.grid a.dl` -- the finding's own four probes reproduce exactly. It was not
  fixed by widening that predicate: `template_normalize.py` grew an
  `elif _is_download_affordance(rs)` branch beside it, and that returns True for
  all four. **Grepping `_is_modal_scoped` alone would have reported B still
  open.** 7 tests pin it.
- **G.** `bd-template-merge` still json-dumps non-scalar leaves -- the finding's
  literal mechanism is still there -- but `_decode_ranked` now puts the ORIGINAL
  values back, so the dump is a voting key rather than what gets written. Same
  shape as B: the reported symptom's code survives and the defect does not.
- **H.** `_gate_support` moved to `toolchain/bin/bd-wacz-corpus:522` and its
  signature is now `(normed_merged, normed_singles, raw_drafts, ...)`. It reads
  both, deliberately. 20 tests pin it.
- **D.** AST census of honeypot imports on the template path:
  `template_extractor_impl/candidates.py` 1, `build_template_from_wacz.py` 1,
  `template_normalize.py` 0. The finding's "zero across all of them" is gone.
- **A. THE FIRST VERSION OF THIS BULLET WAS WRONG, AND IT IS THE BEST EXAMPLE
  ON THIS PAGE OF WHY.** It read "`_place_by_host` no longer exists ANYWHERE
  (0 occurrences)" and concluded the finding's function was gone. The probe was
  `grep -rn '_place_by_host' --include=*.py`, and **`toolchain/bin/bd-wacz-corpus`
  is EXTENSIONLESS** -- the exact population section 1 says a `*.py` glob cannot
  see. Re-measured with no extension filter: the function is alive at
  `bd-wacz-corpus:267`, called at `:323` (`mode_hosts`) and `:912`.

  Three things make this worth the space rather than a silent edit. The bullet
  was written to demonstrate careful re-derivation and shipped an uncorroborated
  grep. Its own closing sentence warned that "a name-based check closes it
  wrongly in the other direction" -- it WAS that check, failing in exactly the
  direction it named. And the CHANGELOG line quoting `_place_by_host` sat two
  greps away the whole time, so a second predicate would have caught it
  instantly. **Two independent predicates, or say the count is a floor.**

  The finding itself: grouping is still by exact host, in three tiers
  (`filename` / `archive` / `unknown`), so A is OPEN and unchanged.

- **A, WHAT TO ACTUALLY BUILD -- already designed, and 15.81 missed it on the
  first pass.** 15.74's queue carries a refined plan: do NOT re-key
  `_place_by_host`; add a labelled `site_families` CANDIDATE tier over the
  existing exact-host buckets; keep exact host as the merge unit, because
  `bd-template-merge`'s single-host guard is CORRECT for drafts; and bridge a
  family into one template not by merging cross-host drafts but by **writing the
  login host into the content template's `match.hosts`** -- a runtime tier the
  matcher already supports and nothing writes. What is still missing is only the
  family MEMBERSHIP. The filename convention already carries what a pairing
  would key on (`{host}_{siteid}_{YYYYMMDD}`), so the open question is whether
  the siteid already pairs login and content hosts across the real corpus --
  answerable from capture filenames alone, and **not answerable in a container**:
  only `tests/capture_corpus_synthetic` exists here.
- **E.** The interstitial vocabulary DOES exist, and not where 15.74 looked:
  `site_templates/_data_players.py` carries
  `a:has-text('No Thanks. Continue')` / `a:has-text('Continue to Members Area')`
  for the Gamma brands, hand-written, with auto-dismiss described in the
  template's own description field. So BD can already dismiss that wall for
  those sites. What does not exist is the SCHEMA modelling it, so a captured
  template cannot express one. E was refused earlier this session for exactly
  the reason that survives re-derivation: which consumer owns the step is
  unspecified -- `do_login` post-submit, `_process_one` template-dismiss, or
  promotion-to-config are three different cuts, and the Gamma precedent is
  evidence for the third rather than a settled answer.

**So A and E are what stand between the captures and a proven template
pipeline.** Neither is blocked on engineering: A needs an operator who can say
which login host belongs to which content host, E needs a decision about which
consumer performs the dismissal.

### 15.80 | v3.66.998-1006: the serial lane went 129 -> 23, the 85 skips went to 1

Close at `55190d4` (already on `main`; per section 4 a close section must never
name an unmerged branch tip -- the squash destroys it and `main` goes red where
no band reaches). **THE WHOLE PROGRAM BELOW IS UNMERGED IN PR #291.**

**GITHUB ACTIONS STOPPED RUNNING AT 2026-08-10T03:52:33Z and nothing here can
fix it.** Four pushes produced zero check runs; `get_status` returns
`state: pending, total_count: 0`. `ci.yml` is `on: pull_request` with no type
filter, so `opened` and `synchronize` both apply. Most likely Actions minutes
exhausted. The standing merge grant needs band green AND CI green, so nothing
was merged. CI's own gates were run locally as substitute evidence -- that is
evidence, NOT the merge condition.

**THE NUMBERS.** Serial lane **129 -> 23 files**; 268.4s of the box's measured
903s remains serial. Skips **85 -> 1**. Separately, `build_snapshot` forked
`git rev-parse` ONCE PER TRACKED FILE -- 3224 forks / 6.66s became 3 / 0.47s,
and the three most expensive serial files went 140.3s -> 40.7s in-container.

**FIVE CORRECTIONS, EACH OVERTURNING SOMETHING WRITTEN EARLIER.**

1. **`BD_INSTALL_DIR=$(mktemp -d)` ON A WHOLE PYTEST RUN IS ITSELF A BUG.**
   Section 5 is right that an ad-hoc PROBE must prefix it; a pytest run is a
   different thing. `db._resolve_db_path()` prefers it over cwd, so one value
   makes every test in the run share ONE SQLite DB and defeats conftest's
   per-test isolation. Fresh per invocation, SHARED within it. It manufactured
   six MOD3 failures, reconstructed to the digit (an "empty" source migrating
   exactly the 7 rows two earlier files inserted), and it is the same mechanism
   that cost 89 false failures in the 2026-08-09 capture. `capture.sh` now
   refuses it outright (@1003).
2. **15.79's coverage_map refutation is itself refuted.** `grep -cE 'worker|child'`
   gives exactly **18** and **19** for `coverage_map`/`semantic_diff` -- the
   record's figures DO reproduce. The earlier verifier tried ten predicates and
   not that one, and I banked its conclusion.
3. **CLAUDE.md does NOT record psycopg as undeclared.** `grep psycopg CLAUDE.md`
   finds nothing; only `test_v3_66_653_dep_freshness.py` carried that note, in
   BOTH waiver dicts.
4. **@1000 shipped a corpus that was 7 TRACKED OF 27.** `.gitignore` carries
   `*.wacz` and `*.har`. Locally the untracked files are on disk, so it passed
   here and would have failed in CI. My pre-commit `git check-ignore` tested
   `capA.json` -- an extension that is not one of the two ignored ones. THE
   DENOMINATOR EXCLUDED THE SUBJECT, in the verification of a section 0 fix,
   hours after shipping a tool built to refuse exactly that.
5. **@1003's own test would have deleted the deploy tree's `__pycache__`.** It
   ran the REAL `capture.sh` past the guard; `capture.sh:402` does
   `rm -rf "$OUT"` and `:412` sweeps `__pycache__` under `$BD_HOME`, which on
   the box is the live checkout. It passed here only because this container has
   no venv beside `capture.sh` -- green for the wrong reason, destructive on the
   box. Fixed at @1004 to truncate a COPY after the guard.

**THE RUNNER RULE IS PRECISE NOW, NOT MERELY ABSOLUTE (@1004).** It stays
absolute deliberately: moving it below the allowlist would make the invariant
enforceable only at review time, and the allowlist is GENERATED, so an omission
would be regenerated away. `runner_import_hazard` is an AST predicate seeing
static imports however aliased, loader-capable calls carrying a runner-naming
constant (including `monkeypatch.setattr` on a dotted path, which the old
quote-anchored regex could not see -- so the rule is WIDER in one real
direction), and fail-closed indirection. **This is not the narrowing refuted at
@986/@990:** the promotions rest on the PROCESS BOUNDARY, not on the
import-inertness measurement. An argv/heredoc literal was outside the rule's
stated mechanism even when the import-time mutation was real, because the child
mutates its own state and exits. If `run_tests_core` regressed tomorrow, not one
promoted file would be affected.

**THE ESCAPE HAD A LIVE INSTANCE (@998).** `spec_from_file_location(...) +
exec_module` classified PARALLEL while loading the runner in-process, and
`test_harness_retry_timeout.py` was doing exactly that in the parallel lane
while leaking `BD_TEST_FILE_TIMEOUT`. A promotion gate that could not see its
own subject had already let one through.

**NEW TOOL: `bd-leakprobe` (@994, budget 237 -> 238).** Diffs os.environ /
sys.modules / cwd across a session, one fresh interpreter per file,
floor-subtracted against files already in the parallel lane. It REFUSES rather
than guesses: a planted canary must be DETECTED before any verdict, and an empty
file list, a zero-length control set or a control that did not run are each NO
VERDICT (exit 2). **An adversarial review then refuted its own verdict** -- a
first-party prefix filter hid planted non-modules at dotted stdlib names while
`--selftest` said SENSITIVE, because the canary only ever planted a BARE name.
The root cause was neither: `_ModuleWithDeprecations` IS a `ModuleType`
subclass, and the plugin compared `type(v).__name__` to the literal "module".
TWO successive repairs were built on that misreading before anyone printed the
object's type. **Generalised: a canary must cover every branch the VERDICT
distinguishes.**

**SKIPS, BY CATEGORY.** 65 capture -> 0 via a committed SYNTHETIC corpus whose
every site-specific marker was mutation-probed (break the marker, the
recognizer's verdict flips) so none passes vacuously; 15 MOD3 -> 0 via Postgres
in `cloud-setup.sh` plus `psycopg[binary]` in `requirements-test.txt`; 3
`bd_dev_inspect` -> 0 by provisioning the dev seam INTO THE VENV, never the repo
(`release_lint` calls it "the ONLY place the unredacted-capture capability
exists"), which means **those three still skip in CI**, the stated cost. netns
is environment-correct (root-gated). The survivor is
`test_cloud_setup_truthfulness.py:262`, a self-retiring dead-branch guard whose
subject was removed -- making it run means re-introducing the bug.

**ITEM E WAS REFUSED, WITH EVIDENCE, AND THAT IS THE RIGHT OUTCOME.** 15.76 says
"nothing in the template schema represents it"; `dismiss_selectors` ships today
in `site_templates/_data_players.py`, is pinned by tests, is copied into site
config by the apply path, and is consumed per content URL by the runner. What IS
true: zero dismiss vocabulary in the WACZ pipeline, and `do_login` dismisses
nothing between submit and its success_url check. Building it needs a runtime
consumer decision the register never made, and the corpus's "No thanks" marks
both true interstitials and ordinary upsell modals. **Re-specify before
building.**

**ITEM A IS STILL BLOCKED** on the siteid-pairing check only the operator can
run. C, D and the capture guard are done; A and E are the residue, both for
reasons that are decisions rather than effort.

### 15.79 | v3.66.996: the lane went 87 -> 62, and the biggest win was not a lane change at all

Close at `4a4b10a`. Three cuts in PR #289. Lane on `main`: **1217 parallel /
62 serial**. The number that matters more: the three most expensive serial
files went **140.30s -> 40.70s in-container** for a product-code fix that has
nothing to do with lane placement.

**THE SERIAL LANE'S REAL SHAPE, measured from the box XML at v3.66.992
(1246 tests, 903.0s, 0 failures).** Per-file attribution, `classname` split at
index 1 -- taking `[-1]` gives the CLASS for class-based tests and inflated the
file count 87 -> 120:

    refuted BY NAME     17 files   511.8s   58%
    pattern (wave 3)    26 files   150.5s   17%
    runner-import       21 files   135.6s   15%
    snippet             23 files    81.1s    9%

Three files are 45% of the whole lane: `test_fuzz_harness_frontend` 178.4s,
`test_differential_oracle_frontend` 117.5s, `test_coverage_map_frontend` 96.3s.

**15.78's PREDICTION IS REFUTED, AND ITS BASELINE NEVER EXISTED.** It said
"the serial lane was ~12 minutes at 129 files... expect LESS than a 32% drop".
Measured at 87 files: **903s**, i.e. UP. Removing 33% of the files cannot make
it slower, so the `~12 minutes` was never measured -- a tilde figure inherited
as a baseline. No delta is claimed against it. 903s is the number.

**`build_snapshot` FORKED `git rev-parse` ONCE PER TRACKED FILE (@995).**
`snapshot.py` validated each tracked path inside its loop with
`normalize_repo_path(repository, ...)`, which re-derives the root through
`discover_repo_root` -- with a CONSTANT first argument, for a return value the
caller deliberately discards. Measured, one `build_snapshot(REPO)`:
**3224 forks / 6.66s -> 3 forks / 0.47s.** Fixed by `relative_to_repo`, the
same rule with the root already discovered; `normalize_repo_path` delegates to
it and is behaviourally identical (verified to agree on VALUE and on EXCEPTION
across in-repo, relative, nested, escaping and nonexistent paths). Other
consumers get it too: `differential_oracle`, `fuzz_harness`,
`reachability_service`, and `l0_extract` -- capture.sh step **[2b]**.
Box confirmation is OWED; the container ratio is not claimed to transfer.

**A MODULETYPE SUBCLASS IS A MODULE, AND TWO FIXES WERE BUILT ON NOT KNOWING
THAT.** `bd-leakprobe`'s snapshot compared `type(v).__name__` to the literal
`"module"`. cryptography installs `_ModuleWithDeprecations` over its cipher
submodules, and that class IS a `ModuleType` subclass -- so two files read as
leaking for no reason but importing cryptography. Repair 1 was a first-party
name filter, REFUTED by measurement (`sys.modules["email.parser"] = object()`
then `import email.parser` returns the planted object; `concurrent.futures` and
`urllib.request` identical) -- and it hid that from the verdict while
`--selftest` said SENSITIVE, because the canary only ever planted a BARE name.
Repair 2 was a per-file `--collect-only` ambient pass, which could not have
worked either: cryptography is imported INSIDE a test body, so collection never
triggers it. `isinstance` is the whole fix. **Nobody printed the object's type
until the third attempt.**

**A CANARY MUST COVER EVERY BRANCH THE VERDICT DISTINGUISHES.** That is the
generalisation. A sensitivity proof over a shape the verdict filters is not a
sensitivity proof, and it coexists happily with a confident clean report.
`_canary_ok` now checks THROUGH `_excess` rather than against the raw delta.

**TWO SIGNALS THAT LOOKED SOUND AND WERE FORGEABLE.** The canary's
"did the conftest run?" check asked whether the conftest had ADDED an env key
-- invoked from inside an outer pytest the child INHERITS those keys,
`setdefault` is a no-op, and a working tool reports BLIND. Its replacement,
`cwd == BD_HOME`, passes with `BD_HOME` set to the current directory and NO
conftest loaded, **which is the deploy box's own layout**. It now asks pytest
whether `isolated_bd_home` is in `request.fixturenames`.

**CI's GATE-SUITES BELONG ON EVERY BAND.** @994 went red on
`test_v3_66_653_dep_freshness` after a green 32-file band. CI's denominator is
file-INDEPENDENT by design, so no module-derived band reaches it; the band is
now the union with all 15 suites named in CLAUDE.md section 7. The finding
itself was right: a bare `import capture_lanes` from `toolchain/bin/` is a
cross-directory reach the gate cannot verify, while the same import from
`tests/` resolves as a sibling. Waiving it as third-party would have been false;
the tool now loads `tests/capture_lanes.py` BY PATH.

**`bd-band-derive` PRINTS 24 AND MEANS 253.** Its display truncates with
`"... +229 more"`. A `grep -oE` over the printed output gave a band **10% the
size of the real one**. Use `--json`. The printed list is not the denominator.

**WHAT REMAINS: 62 serial files. THE 58% BUCKET IS NOT BLOCKED BY THE NAMES.**
Re-derived twice, independently: removing a name from `SERIAL_EXACT_BASENAMES`
frees **ZERO** seconds for all 17 -- each falls to serial by a later rule and
none is in the allowlist. Confirmed by the orchestrator's own measurement that
deleting `SERIAL_SOURCE_SNIPPETS` + `SERIAL_SOURCE_PATTERNS` +
`SERIAL_NAME_TOKENS` entirely changes **0** classifications: everything
reaching them is unlisted and returns serial anyway. Freeing any file needs the
name removed AND an allowlist entry.

**UNVERIFIED PROPOSALS -- do not act on these without re-measuring.** From a
4-lens investigation whose verify phase was stopped (2-wide concurrency on 4
cores made it slower than inline, exactly as CLAUDE.md section 5 records):

- promote 8 read-only artifact/filename-string snippet files, ~26.1s (high)
- promote 9 `bd_module_wipe` files, ~22.8s (medium) -- conftest saves and
  restores around the marker, so the state does not outlive the file
- `test_v3_66_729_body_contract_fixtures` claimed STALE, 56.5s
- KEEP SERIAL: `test_u41_systemd_live_tests` launches 3 real browsers (probe
  proven armed, positive control saw launches elsewhere)

**ONE PROPOSAL WAS REFUTED AND MUST NOT BE RETRIED ON THE SAME EVIDENCE.**
`test_coverage_map_frontend` (96.3s) claimed STALE because the record names the
`*_frontend` family "by shape". The verifier reproduced every figure and then
showed the identification fails: the record's "(18 and 19 worker/child
references)" matches NO file under ten predicates -- one number was matched
under a predicate that cannot produce the other. Files are byte-identical to
the commit that wrote the comment, so this is not drift.

**TWO DEFECTS FOUND IN PASSING, both real, neither urgent:**

- **The ABSOLUTE runner-import rule has a demonstrated escape.**
  `spec_from_file_location("rtc", "run_tests_core.py") + exec_module +
  _prepare_runner_state()` classifies **parallel** and still rewires the
  interpreter (probe run with `BD_DISABLE_KEEPALIVE` POPPED).
- **`capture_lanes.py`'s own comment contradicts its code.** The @921 block at
  `tests/capture_lanes.py:269-286` says the source checks "stay ABSOLUTE... no
  allowlist entry may override them"; @923 moved the allowlist check ABOVE
  them. Found independently twice in one session. Live misinformation for
  anyone reading that file to decide what is promotable.

**FOUR CONTAINER-ONLY FAILURES, NEWLY CHARACTERISED.**
`test_v3_66_820_auth_health_reaped_on_site_delete` (2),
`test_u50_widget_backfills` (1),
`test_library_forward_path_records_an_absolute_path` (1). All pass on the box
(the @992 capture is 15251 total, 0 failed, 0 errors). They reproduce
IDENTICALLY on pristine source in the same directory, so they predate any of
this. A hypothesis that an empty `BD_INSTALL_DIR` caused them was REFUTED --
seeding the DB does not fix them, and `auth_health` gets WORSE on a second run
in the same dir (2 -> 3 failures), which is real state accumulation. They need
tables another FILE creates. Freeing `u50` is worth 0s, so this is robustness
work with no lane payoff.

### 15.78 | v3.66.992: the lane went 129 -> 87, and what is left is the real work

Close at `093cb60`. THIS SUPERSEDES 15.77's "promotion pool" framing -- the 75
are PROMOTED, not pending. Lane split on `main`: **1191 parallel / 87 serial**
(was 1149 / 129).

**WAVE 1 (75 files, freed by @990).** Serial baseline 720 tests green, then
`-n 2/3/4/6/8 --dist loadfile`. `-n 3` and `-n 4` were GREEN while `-n 2`, `-n 6`
and `-n 8` refuted two files. Had one width been run -- the obvious thing -- two
leaking tests would have shipped. xdist assigns files to workers by COUNT, so the
width decides who shares a worker and a single width samples one packing.

**THE TWO REFUSALS WERE FIXED, NOT PINNED**, which is the operator's stated
preference and the right call here. `test_p8_queue_intelligence` and
`test_v3_66_226_saved_search_patch` read the AMBIENT database and asserted global
counts (`assert 6 == 0` -- another file's rows still present), so they passed
serially only because nothing had seeded it yet in that process. Both now take
`clean_workdir` autouse: it chdirs to a tmpdir AND sets `BD_INSTALL_DIR`, the
variable `db._resolve_db_path()` actually consults, since chdir alone does not
survive later code chdir-ing away.

**WAVE 2 (42 files).** 31 only ever UNLISTED (fail-closed, never reviewed) plus
11 matched solely by a NAME TOKEN, which `capture_lanes` documents as a proxy for
"nobody has looked at this yet" and explicitly makes overridable by review.
Baseline 367 tests, same five widths, zero failures.

**WHAT REMAINS: 87 serial files.**

    runner-import (genuine importers)   21
    snippet (playwright/socket/systemd) 23
    refuted BY NAME                     17
    pattern (the next target)           26

**WAVE 3 IS THE 26, AND IT IS A PER-FILE REVIEW.** They pass at every width
TODAY and were deliberately NOT promoted on that: the hazard is a raw mutation
leaking into OTHER files on the same worker, which running the 26 alone
structurally cannot expose. That is the @921 shape -- the whole serial lane
passed together, and splitting it then broke `test_u50_widget_backfills`.

Measured fix surface: **104 write sites across 26 files -- 78 `os.environ`, 24
`sys.modules`, 2 `os.chdir`** -- and 16 of the 26 already carry a restore idiom
(`finally` / `monkeypatch` / `setUp`) that the regex cannot distinguish from a
leak. One file alone (`test_v3_66_504_envfile_editor.py`) has 25 env writes and
is an env-file editor, where they are probably intrinsic. Bulk-converting sites
nobody has read would be its own band-aid; the fix is `monkeypatch.setenv` /
`clean_workdir` per file, after reading each.

**TWO GUARDS CAUGHT THE PROMOTION ON ITS FIRST RUN**, which is the argument for
deriving a subject rather than restating it:

- `_has_source_hazard` (in `test_capture_execution_lanes.py`) borrowed the
  classifier's CONSTANTS but not the TEXT they are applied to, so after @990 the
  guard and the classifier held two different definitions of "hazard". It now
  borrows `code_only` too.
- Its non-empty-denominator floor was `> 100`, calibrated when the check read
  prose and counted 143 files of which 4 really imported the runner. The honest
  population is 22, so the floor is 15. The PREDICATE got more precise; the
  guard did not get weaker. Record that reasoning before lowering any ratchet.

**CONTAINER EVIDENCE IS NOT BOX EVIDENCE, and this is the open verification.**
The sweeps prove the promoted set does not interfere WITH ITSELF; they say
nothing about interference with the ~1074 files already in the lane, which is a
different composition. The capture is the gate. Anything it refutes goes into
`SERIAL_EXACT_BASENAMES` **by name with its mechanism** -- never by omission,
because the allowlist is regenerated and an omission would be regenerated away.

**A PREDICTION TO CHECK RATHER THAN ASSUME:** the serial lane was ~12 minutes at
129 files, but the 87 left are not uniformly cheap -- they are the browser,
socket, systemd and golden-regeneration suites, individually the slowest. Expect
LESS than a 32% drop, and measure it rather than quoting this sentence.

### 15.77 | v3.66.990: the serial lane was ~124 files deep in PROSE, and the program to drain it

Close at `1fc600a` (v3.66.990 on `main`). Every figure re-derivable with the
script at the end; re-derive rather than quote.

**THE MEASUREMENT.** `tests/capture_lanes.py` pins a file to the serial lane for
containing `import run_tests`, `from run_tests`, or `run_tests.py`. The rule is
RIGHT and its reason is literally true -- measured in a fresh subprocess with
the variable POPPED, importing `run_tests_core` set `BD_DISABLE_KEEPALIVE=1`
and prepended the repo root to `sys.path`, neither restored.

    pinned serial by the rule                         143
      ...that genuinely import the runner (AST)         4
      ...that only MENTION it in a comment/docstring  139
    still pinned after stripping comments+docstrings    19

**@990 asks the rule of CODE.** Same snippets, same absoluteness, same position
above the allowlist; only the text it reads. `code_only()` is FAIL-CLOSED --
tokenizer OR ast.parse failure returns the ORIGINAL source. Separately,
`run_tests_core`'s two mutations moved into `_prepare_runner_state()`, called
from all six public entry points, so the import is now inert.

**IT FREED ZERO FILES, BY DESIGN, AND THAT IS THE PART TO UNDERSTAND.** The
absolute checks sit ABOVE the allowlist, so a prose-matched file could not be
promoted by review at all -- it was structurally unreachable. Now it falls
through, is NOT on the allowlist (measured: 0 of 124), and hits the fail-closed
default. What changed is eligibility.

**THE PROMOTION POOL, measured:**

    freed from the runner rule                        124
      CLEAN by every remaining hazard check            75   <- candidates
      os.environ / sys.modules / chdir pattern         22
      serial snippet (playwright, socket, requests...) 19
      risky name token                                  7
      refuted BY NAME in SERIAL_EXACT_BASENAMES         1

**THE OPERATOR'S DIRECTION, 2026-08-10, verbatim in substance:** promote the 75
after the merge, measure the speedup, and if it is still unsatisfactory keep
going -- because "we've done this before where we fixed tests and made them more
robust reliable and prevented leakage and changed the order instead of putting a
band aid on a bullet hole". So the 49 hazardous files are candidates for BEING
FIXED, not for being pinned forever. That is the standing instruction for this
program.

**HOW A PROMOTION MUST BE EVIDENCED.** The allowlist is checked-in and reviewed,
and `capture_lanes`' own comments record why a green run is weak evidence: at
v3.66.921 the whole serial lane was run parallel TOGETHER and passed, then
splitting the lane broke `test_u50_widget_backfills`, whose table-seeding
dependency ended up on the other side. At v3.66.923 four widths (-n 64/32/24/16)
produced TEN distinct refuted files, and they kept ARRIVING as the width fell --
xdist assigns files to workers by count, so each width shuffles who shares a
worker. Precedent therefore: an all-parallel sweep at MORE THAN ONE width, and
every refutation recorded BY NAME in `SERIAL_EXACT_BASENAMES` rather than by
omission, because the allowlist is regenerated and an omission would be
regenerated away.

**RE-DERIVE THE POOL WITH THIS, not with the numbers above:**

```python
import pathlib, sys; sys.path.insert(0, "tests")
import capture_lanes as cl
for p in sorted(pathlib.Path("tests").glob("test*.py")):
    src = p.read_text(errors="ignore")
    if not any(s in src.lower() for s in cl.ABSOLUTE_SERIAL_SNIPPETS):
        continue
    code = cl.code_only(src)
    if any(s in code.lower() for s in cl.ABSOLUTE_SERIAL_SNIPPETS) \
       or cl.RUNTESTS_LITERAL.search(code):
        continue                      # genuinely serial: a real import
    hazard = (p.name in cl.SERIAL_EXACT_BASENAMES
              or any(s in code.lower() for s in cl.SERIAL_SOURCE_SNIPPETS)
              or any(pat.search(code) for pat in cl.SERIAL_SOURCE_PATTERNS)
              or any(t in p.name for t in cl.SERIAL_NAME_TOKENS))
    print(("HAZARD " if hazard else "CANDIDATE"), p.name)
```

**TWO FIXTURE DEFECTS `bd-mutate` CAUGHT THAT REVIEW DID NOT**, both the same
shape -- a test naming a condition it never exercised:

- `code_only` has TWO fallbacks and the "unparseable" fixture (`def broken(:`)
  fails in the TOKENIZER, so a mutant turning the ast.parse fallback into
  `return ""` escaped. `x = = 1` and a bad indent tokenize cleanly and fail
  `ast.parse` -- the only way in. The assertion was also strengthened from "the
  verdict is serial" to "the fallback returns text still CONTAINING the import",
  because a fallback returning LESS than it was given is how a strip becomes a
  hiding place, and the verdict alone cannot see that.
- (@988, same session) an over-sensitivity guard used `[role=dialog] a.dl` as "a
  selector that parses as JSON". It is not valid JSON, so the condition was
  never exercised.

**A PROCESS INCIDENT THAT COST A CAPTURE, recorded in full at 15.76 item 4:**
an agent's probe instructions said `export BD_INSTALL_DIR="$(mktemp -d)"`, the
operator ran it in the interactive shell, `./capture.sh` inherited it, and 89
tests failed against a shared tmpdir database. `12744 + 89 == 12833`, and the
clean re-run is `12833 passed`. Use the prefix form, never `export`.

### 15.75 | v3.66.987-989: the reliability numbers were wrong, and the corpus answered three questions

Close at commit `fa97230` (v3.66.988 on `main`; @989 was in flight as PR #284).
Four PRs merged this run: #281 (@986 record), #282 (@987), #283 (@988), plus
#284 open. Every figure below was MEASURED -- from a box run of
`bd-wacz-corpus --templates --jobs 0` over 742 captures / 158 sites, or by
running the pipeline on a constructed fixture. Re-derive before citing.

**WHAT WAS WRONG WITH THE v3.66.984 NUMBERS.** `_gate_support` read
`merge_drafts`' RAW selector_support keys (`download.button_hint`,
`download.row_selectors`) through a tuple looking for NORMALIZED ones
(`download.trigger`, `download.row_selectors`, `download.button`). Intersection
on a text-hint site: `row_selectors` only. `download.button` is emitted by
NOTHING -- `_map_selectors:102-104` collapses button_hint/trigger/button into
`trigger`. Consequences, both visible in the numbers the operator was given:

- a text-hint-only site scored no support and read `unknown` -> the
  `corroborated 84 / unknown 71` arithmetic that never reconciled against 71
  green sites;
- an inline row normalize DISCARDS read `corroborated` off raw row support while
  assess reported `row_selectors_count 0`.

@987 votes over `normalize_draft`'s OUTPUT and attributes to values read back out
of `normalize(merged)`. **There is deliberately no raw-key-to-clause map**: a
wider tuple is the same defect with more entries, and
`test_a_FUTURE_raw_key_is_VISIBLE_rather_than_vanishing` is the test no map can
pass. Attribution grades the SHIPPED value, not the vote winner -- measured,
drafts {button_hint:X},{trigger:Y},{trigger:Y} vote Y at 2 of 3 while the hint
takes precedence and the template ships X. Two of three candidate designs failed
exactly there.

**THE CORPUS RE-RUN, and it answers the 5/9 question the operator asked.**

| | v3.66.984 | v3.66.987 |
| --- | --- | --- |
| corroborated | 84 | **52** |
| unknown | 71 | 106 |
| green_from_one | 71 | 71 |

`disagree` is **0** on every one of the 5/9 sites -- dfxtra, evilangel,
nubiles-porn (5/0/4), filthykings (4/0/4), kellymadison (6/0/2), bangbros
(16/0/8). They are INCOMPLETE CAPTURES, not two page shapes. Corpus-wide only
**2** sites have any trigger disagreement: `app.reptyle.com` (34 support / **1**
disagree / 27 absent) and `vidstack.io` (4/20/0). **112** sites are absent-only.
So even reptyle -- which the operator confirmed is genuinely two-shaped -- was
27 incomplete captures and one alternative shape, not 28 disagreeing. **A
previous session's inference that "the same ratio at smaller n is the obvious
suspect for two shapes" is REFUTED.**

**THE ROLLUP, and the correction to a correction.** `gate_selector_blocked` =
84 sites: 18 discarded-by-normalizer, 24 no-download-evidence, 42 `other` (raw
leaves survived but no gate clause; all 42 have zero trigger candidates). So
**66 of 84 are capture-side** -- which means the ORIGINAL "77 is a capture-side
gap" reading was substantially right and the retraction over-corrected on
scale. The retraction was right to demand measurement; it was wrong to imply the
normalizer accounted for most of it.

**FINDING B's REAL SCALE, and a sampling error worth remembering.** A first pass
printed only the TOP dropped row per site (`dropped_rows[0]`) and concluded
"mostly junk". Over all **143** dropped selectors, **44 carry download
semantics** and they are the operator's member sites: `a.ct_dl_button` 30/39
(auth.wowgirls.com), `a.download__item` 7/8 (vip4k.com),
`a.dropdown-downloads-link` 5/9, `div.clickable.download-button...` 7/21
(ultrafilms.com), `a.d-flex.download-element` 4/6. **Sampling the top row per
group is not sampling the population.**

Those sites are green ON THEIR TRIGGER; the discarded rows are the
per-RESOLUTION links. So the operator's fourth step -- select the highest
resolution -- is what the modal rule silently removed, on sites the report calls
green. @989 admits a row that is a CLICK TARGET carrying a DOWNLOAD token; the
modal rule stays, because the other 99 dropped selectors are `a:nth-child(31)`,
`li.theo-menu-item`, `span.title`, `a.nav__link`.

**THE OPERATOR'S OWN SITES ARE FINE.** 17 member-site rows: **16 green +
corroborated, 1 not_green**. All 17 carry resolutions. The exception is
`ultrafilms.com` -- 21 captures, ZERO triggers extracted in all 21, plus a
`div.format-name` row dropped in 7. That is the SD/HD/FULL HD/4K screenshot
shape, and it is the one member site where B genuinely bites.

**CONTENT HOSTS WERE NEVER CAPTURED.** `auth.wowgirls.com` (39 captures) is in
the corpus; `venus.wowgirls.com` is NOT. `vip4k.com` is; `members.vip4k.com` is
not. The login halves were captured and the content halves were not -- which is
a concrete capture instruction, and it makes `auth.wowgirls.com` reading green
on a trigger 37/39 worth a look, since that is a download control on a login
host.

**DEFECT G's COST WAS EXACTLY 16 SITES.** All 16 `merge_artifact_only` sites were
`green_from_one` and modal-shaped (`.modal a.inject-url`,
`[role=dialog] a[role=button]`, `.drawer ...`), 15 of 16 at FULL support. They
were ungradable only because `merge_drafts` json.dumps a list leaf and wrote the
TEXT back. Measured: `'["[role=dialog] a.dl"]'` PASSES `_is_modal_scoped` -- the
JSON text contains the literal `[role=dialog]` -- survives normalize, and reaches
`promotion_ready True`. **It does not merely corrupt a selector, it manufactures
a green.** @988 records the encoding at vote time rather than re-detecting it,
because parsing "if it looks like JSON" cannot tell an encoded list from a
hand-written selector that happens to be valid JSON.

**HONEYPOTS ARE VISIBLE FOR FREE, with one caveat.** Verified through the real
merge pipeline: a decoy that rotates per page-load reads `support 1 of 3` in
`gate_support.clauses.*.candidates` while the real control reads `3 of 3`. The
caveat is load-bearing -- a honeypot that is BYTE-IDENTICAL on every page load
reads high support and this instrument cannot see it.

**GREEN STILL DOES NOT MEAN THE RESOLUTION STEP WORKS.** Re-verified: a draft
whose only resolution evidence is `network_discovery.resolutions_seen`, with
zero quality selectors, is `promotion_ready True`. That is now stated in the
mode's own JSON as `green_means_note` rather than only in chat.

**PROCESS FINDINGS FROM THIS RUN.**

- Adversarial review earned its keep three times. On @987 it found that the
  rollup classified `api_template`-only sites as "the normalizer discarded your
  control" when `api_template` SURVIVES normalize verbatim -- the retracted-77
  shape inverted, inside the rollup built to prevent it. On @988 it found that
  `isinstance(True, int)` makes `("scalar", True)` and `("scalar", 1)` one dict
  key, so storing originals by assignment silently changed FIRST-appearance to
  LAST -- `1080` could become `1080.0` on merge depending only on draft order.
- **Two EQUIVALENT mutants were correctly identified rather than "fixed".**
  @988's string-encoding mutant round-trips losslessly (verified: no vote-key
  collisions); @989's lint-ordering mutant is equivalent because the lint and
  affordance predicates do not overlap on any input -- the linter blocks bare
  generics and the affordance rule needs a token that makes a selector
  non-generic. Both are recorded as equivalences, and @989's test SAYS SO rather
  than dressing the ordering up as a passing test.
- `bd-mutate` caught a VACUOUS over-sensitivity fixture that review did not: a
  test pinning "a real selector that parses as JSON is not flagged" used
  `[role=dialog] a.dl`, which is not valid JSON, so the condition it existed to
  pin was never exercised.
- A test that built its counters BY HAND passed the moment it was written, over
  a defect that lives in how those counters are DERIVED. Drive the real
  pipeline.
- `A; B &` backgrounds only B. A 34-file band ran in the FOREGROUND and was
  reaped at 120s with exit 143 -- section 5's documented trap, hit verbatim.

**STILL OPEN.** C (`text=/Download/i` can go green on a heading -- VIP4K
measured), D (the repo's own `login_extract._login_is_honeypot` at :116-140
screens visibility and the template path never calls it for download
selectors), E (no post-login interstitial step is modelled -- the "No Thanks.
Continue to Members Area" shape). A pre-@988 template already on disk carrying a
stringified selector is detected and repaired by NOTHING -- measured, it
promotes cleanly. Cross-host grouping (A) is unchanged: `auth.X` and `app.X` are
still two sites, and `match.sibling_domain` is still written by nothing.

**OWED BY THE OPERATOR.** The 25-capture probe printing the real download
selector per capture; the siteid-pairing check
(`auth.reptyle.com_0b60f1ec_...` vs `app.reptyle.com_0b60f1ec_...`). Also a
stale gitignored `downloader_history.db` sits in the repo root dated
2026-08-05 -- item 36's class, invisible to `git status`, and it will feed rows
to the next unisolated probe.

### 15.74 | The template pipeline met seven real sites: seven findings, one retraction, and a REFUTED cut

**Not a session close** -- no ITEM LEDGER; 15.70's open set (31, 33) stands, 44 closed at 15.72.
Written mid-session against imminent compaction, so it records EVIDENCE and OPEN STATE rather than
narrative. Everything below was measured at `5ea790a` unless stated.

**THE BOX RUN THAT STARTED IT** -- `bd-wacz-corpus --templates -j 0` over
`/home/mboyle/BulkDownloader/captures`, 742 captures:

    examined 742  sites 158  jobs 88  unbuildable 5
    verdicts    green_from_one 71   not_green 82   unbuildable 5
    reliability corroborated 84     unknown 71     single_witness 3
    not_green blocking: gate_selector 77, "gate_selector,resolutions" 5
    GREEN_ONLY_MERGED = 0

`jobs 88` is @985's parallel path confirmed on real data. **GREEN_ONLY_MERGED = 0 is real but was
read wrongly at first**: no site is rescued by merging its OWN captures, which is not the same as
"merging is useless" -- see the cross-host finding below.

#### THE CUT THAT WAS REFUTED, and it is the most important thing here

**DO NOT narrow `ABSOLUTE_SERIAL_SNIPPETS` in `tests/capture_lanes.py`.** A proposed @986 would have
narrowed the trigger from "mentions run_tests" to "activates the stub", freeing ~137 files into the
parallel lane. Two independent adversarial agents refuted it and the refutation reproduces directly:

    env -u BD_DISABLE_KEEPALIVE python -c "import run_tests"
      BD_DISABLE_KEEPALIVE before: None   after: "1"

`run_tests_core.py:28` does `os.environ.setdefault("BD_DISABLE_KEEPALIVE","1")` and `:33` an
unconditional `sys.path.insert`, neither restored. **The code comment "importing the fallback runner
rewires global interpreter state" is literally true.** The claim that it was stale came from a probe
that measured `sys.modules` ONLY -- and that ran with `BD_DISABLE_KEEPALIVE=1` already set from the
panel env box, so `setdefault` was a silent no-op. **Section 0's subprocess-harness trap, hit by
someone who had read section 0 that morning, in a probe designed to answer exactly that question.**

Two further reasons the narrowing was unsafe, both worth keeping:

- **`tests/test_u42_resource_live_tests.py` is serial only because line 46 is a COMMENT** mentioning
  `run_tests.py`. Its real hazard is `checks._SAMPLE_COUNT = 4` at MODULE SCOPE (`:51-52`, real
  defaults 5 / 3.0), and the resulting cross-file failure is already recorded verbatim in
  `tests/test_l33_counts_orphans_not_processes.py:668-688`. A comment is accidentally the only thing
  keeping a genuinely unsafe file out of the parallel lane.
- **Narrowing alone frees ZERO files** (fail-closed default), and freeing requires an allowlist regen
  -- but the allowlist check sits ABOVE the remaining heuristics, so anything promoted bypasses every
  check the proposal claimed would still protect it. There is no middle path in the current design.

Corrected counts at `5ea790a`: 1274 tracked test files, 200 serial, 1074 parallel; 145 files match the
absolute snippets, 143 attributed after exact-basename takes 2 first; **0 files activate the stub
in-process** (all 6 textual hits are inside subprocess driver strings -- AST-verified). The classifier
and pytest agree exactly, sets identical, 0 files in both.

**THE REAL WORK IS HARDENING, NOT RECLASSIFYING.** Fix `run_tests_core`'s import-time `setdefault` and
`sys.path.insert` (move them into the entry point) and the import becomes genuinely inert, which frees
the population honestly. Then the module-scope-mutation family: `test_u42:51-52`, and
`os.environ.setdefault` at import in `test_dom_overlay:16-17`, `test_legacy_parity:15-16`,
`test_endpoint_catalog_in_sync:50`, `test_v3_66_776/777/778*`.

#### SEVEN FINDINGS ABOUT THE TEMPLATE PIPELINE, all proven by construction

Fixtures were rebuilt from operator screenshots of seven real member sites. **They are
RECONSTRUCTIONS, not the real captures** -- real markup carries ids/classes/data-attributes a PNG
cannot show, and `ultrafilms.com` is green on the box while the reconstruction extracted nothing. The
mechanisms are proven; their SHARE of the box's 71/82 is not.

- **A. Grouping splits every site.** `_place_by_host` buckets on the exact hostname. Five of seven
  operator sites span a login host and a content host: `auth.wowgirls.com`/`venus.wowgirls.com`,
  `vip4k.com`/`members.vip4k.com`, `auth.reptyle.com`(OAuth, `?referer=spa`)/`app.reptyle.com`,
  `bangbros.com`/`site-ma.bangbros.com`. Visible in the box data as two rows:
  `auth.reptyle.com cap=5 trigger 5/5` and `app.reptyle.com cap=62 trigger 34/62`. **A login-host
  bucket reporting green is a false green** -- login selectors are scored, never gated.
- **B. Modal-scoping discards real download panels.** `template_normalize:117-124` keeps a row
  selector only `if _is_modal_scoped(rs)`, and `_MODAL_RE` matches only dialog/modal/drawer/popover.
  Measured: `a.dl` False, `.download-block a.dl` False, `div.grid a.dl` False, `div.modal a.dl` True.
  Fed the real WowGirls grid, the builder extracts `row_selectors: ["a.dl"]` and normalize drops it ->
  `not_green`, `row_selectors` missing. **That is byte-for-byte the box's 77.** The rule is not wrong
  to exist -- reptyle's flow IS a modal and passes -- but container-scoping should count as scoping.
  **An earlier claim that the 77 are a capture-side gap needing re-capture is RETRACTED; do not
  re-capture on it.**
- **C. `text=/Download/i` can go green on a heading.** A panel whose only "Download" text is its `<h3>`
  yields `button_hint: "text=/Download/i"`, normalizes to `trigger`, and reports promotion_ready True.
  Reptyle's real trigger is an ICON with no text, so text hints are a weak basis for green in both
  directions.
- **D. The honeypot scorer exists and the template path never calls it.** AST-verified: zero honeypot
  imports in `build_template_from_wacz`, `template_normalize`, `template_inventory`,
  `bd-template-merge`, while `bulk_downloader/honeypot_score.py` offers `score_candidate` /
  `classify_score` and `dom_honeypot.py` exists. Wiring it into selector CHOICE is the highest-value
  answer to the operator's honeypot concern -- the defence is already owned.
- **E. No post-login interstitial step.** A "No Thanks. Continue to Members Area" upsell wall sits
  between login and content on at least one site; nothing in the schema models dismissing it. Grep
  finds interstitial vocab only in youtube consent, transport, and honeypot path terms.
- **G. `merge_drafts` corrupts list-valued selectors AND the result still promotes.**
  `bd-template-merge:96-98` json-dumps non-scalar leaves so they can be voted on, then writes the
  winning STRING back into the canonical slot. Measured on a modal-scoped row selector:
  single -> `["div.modal a[href*=\"dl\"]"]` promotion_ready True; merged ->
  `["[\"div.modal a[href*=\\\"dl\\\"]\"]"]` promotion_ready **True**. A garbage selector that passes
  the gate. Not yet bitten because GREEN_ONLY_MERGED = 0, but any direct `bd-template-merge` run on a
  row-selector site hits it -- `bitmovin.com` 24/24, `login.vixen.com` 5/5,
  `members.kellymadisonmedia.com` 6/8, `www.miruro.tv` 4/7.
- **H. `_gate_support` reads RAW, `assess` judges NORMALIZED.** So "corroborated" can be asserted for a
  selector normalization discarded. Reproduced end to end: a site reporting
  `verdict not_green, reliability corroborated, gate_support row_selectors 2/2, blocking
  ["gate_selector"]`. From the box marginals (corroborated 84 > green 71), **at least 13 sites** are
  this case. `_blocking` is also missing the `button` clause that `assess`'s gate includes.
- **RETRACTED -- F.** A "disabled options counted as available" finding was withdrawn: the greyed row
  in the screenshot was the operator hovering. It was the one finding marked inferred rather than
  constructed, and it is the only one that did not survive.

#### THE VARIATION THE EXTRACTOR HAS TO SURVIVE

Seven resolution label formats across seven sites: `1920 x 1080` (spaced); `3840x2160` (unspaced);
`1080p`; `SD/HD/FULL HD/4K/8K (HEVC)` (no digits at all); `Standard/High/Ultra` + `720p..2160p`;
`h264 - 2160p`; `Low/Small/Medium/.../4K` + `160p..2160p`. Five trigger shapes: icon-only ->
modal; inline grid; inline panel; a `Downloads` dropdown BESIDE a decoy `Quality` dropdown; icon+text
button. Measured on the last of those, the builder found **no download control** and reported the
resolution ceiling as **1080 while 3840x2160 was on the page**, because it read the playback Quality
ladder instead. Every login seen carries a CAPTCHA (Turnstile / "Verify you are human"), so step 1 of
the operator's four-step flow cannot be selector-driven at all.

#### CROSS-HOST: THE FIX IS NOT eTLD+1, AND THE COUNTER-CHECK FOUND A LIVE HAZARD

The runtime ALREADY supports one template covering several hosts --
`template_registry._template_host_match_key` matches exact host, `match.hosts` alias, subdomain, and
`match.sibling_domain`; probe-verified that a `venus.wowgirls.com` template resolves
`auth.wowgirls.com` and correctly refuses an unrelated `auth.bangbros.com`. **Nothing in the pipeline
ever writes `sibling_domain`** -- it appears in exactly one file repo-wide, the matcher. The capability
is real and unreachable. `bd-template-merge:175-182` separately hard-refuses drafts spanning hosts.

**The obvious key is broken on hosts already in the corpus.** `extension_vault.get_registrable_domain`
is last-two-labels:

    auth.wowgirls.com -> wowgirls.com    app.reptyle.com  -> reptyle.com
    www.bbc.co.uk     -> co.uk           shaka-player-demo.appspot.com -> appspot.com

`co.uk` and `appspot.com` would each become a "site". `app_secrets.py:466-468` records having already
abandoned that helper for this reason, and adding a PSL dependency is rejected by the design doc.

**So the design is: exact host stays ground truth; cross-host pairing is a labelled CANDIDATE; and the
pairing is DERIVED, not named.** The likeliest evidence source is BD's own capture convention -- if
`auth.reptyle.com_0b60f1ec_...` and `app.reptyle.com_0b60f1ec_...` share a `{siteid}`, the operator's
own site config already pairs them, with no domain guessing and no denylist. **UNTESTED**; the command
to settle it is a `find`+`sed` over the 39 filename-convention captures, grouping siteid -> hostnames
and keeping rows with more than one host.

#### OPEN, in the order the evidence supports

1. **A + H** -- both in `bd-wacz-corpus` only, both are why the reported numbers are wrong. Group by
   derived site identity with the method labelled; stop asserting corroboration for selectors the gate
   discarded; surface normalize's own `dropped row selector (not modal-scoped or unsafe)` warnings so
   the 77 can be split exactly rather than estimated.
2. **G** -- one function in `bd-template-merge`, high severity, independent.
3. **B** -- biggest win, but it is PRODUCT code on the capture path and needs operator sign-off.
4. **D, C, E** -- a program, not a cut. D first.
5. **The lane hardening** above, which is a source fix rather than a classifier fix.

**OWED MEASUREMENT, operator-side:** the pipeline run against 25 REAL captures printing the chosen
download selector per file. That decides whether B and C are the whole story or whether the
reconstructions were too thin. Nothing in section 1 above should be scaled to the corpus until it
returns.

#### ADDENDUM -- adversarial-lens results recovered from truncated workflow output

Four lens results exceeded the tool's return cap and were never read in-session; recovered from the
run journals and recorded here because each is better than the conclusion it replaces.

- **THE REAL PROMOTE GATE IS `template_manager.promote_gate_errors` (`:155-200`), NOT
  `template_inventory.assess`.** `assess` is a MIRROR of it and the lens reports a MEASURED
  divergence between the two. Everything this session called "green" was the mirror's verdict. The
  single highest-value field `--templates` could emit is `real_gate_promotable:
  promote_gate_errors(normalized) == []`, which closes that divergence instead of restating it.
- **THE `resolutions` GATE CLAUSE IS SATISFIED FROM THE WIRE, NOT THE DOM.**
  `build_template_from_wacz:2011-2013` builds `resolution_priority` from
  `network["resolutions_seen"]`, so a capture whose DOM offers NO resolution choice still passes.
  The builder emits `quality.open_menu`/`resolution_option` only for THEOplayer / xgplayer /
  aria-labelled players -- three literal marker families. **Do NOT widen the gate to require quality
  selectors** (that flips most of the 71 red and is section-0 over-sensitivity; modal-row sites
  genuinely do not need them). Instead emit a derived `resolution_path`, one of
  `quality_selectors` | `row_scrape` | `wire_only`. **`wire_only` is the honest name for
  green-with-no-step-4.** Structural bound on the 71: 0..71, none protected by construction.
- **THE VISIBILITY SCREEN ALREADY EXISTS, AND IT IS NOT `honeypot_score`.**
  `template_extractor_impl/login_extract._login_is_honeypot` (`:116-140`) already screens
  `display:none`, `left:-9999`, `width:1px`, `opacity:0`, `visibility:hidden`, `tabindex=-1`,
  `aria-hidden`, and `name~honeypot` across three parent levels -- for LOGIN fields only. Meanwhile
  `_html_selectors` (`:1590-1672`) and `_derive_download_trigger`/`_affordance` (`:796-853`) read
  `node.attributes` ONLY and rank an aria-label/title "download" match ABOVE a class match, with no
  visibility test anywhere. Lift `_login_is_honeypot` into a shared helper and call it from the
  trigger and row derivations; the serialized rrweb node carries style and attributes, so this is
  computable offline. That is a smaller and better fix than wiring in `honeypot_score`.
- **THE RUNTIME LOGIN NEVER READS A TEMPLATE.** The only template->runner bridge is
  `merge_template_download_hints` (`runner.py:3356` -> `template_assist.py:159+`), which carries
  download hints only; `do_login` takes no template. So **71/71 green sites get ZERO login capability
  from their template** -- step 1 runs on per-site config credentials and learned/fallback selectors,
  and greenness says nothing about it. Bounded by call-graph enumeration, not sampling.
- **`gate_support` CAN distinguish "different value" from "absent", and the data is already there.**
  `merge_drafts` keeps the full ranked candidate list. Add to the emitted object:
  `witnesses` = sum of support over the ranked list; `absent_in` = `of - witnesses` (**>0 means
  captures that built a draft with NO gate leaf at all** -- case b); `alternatives` = `ranked[1:]`
  verbatim (**non-empty means genuinely different values** -- case a). Pure plumbing in
  `_gate_support`. This is what turns `trigger 5/9` from ambiguous into actionable.
- **THE `_blocking` FIX IS TO STOP RE-DERIVING.** Rather than adding the missing `button` term to a
  second copy of the predicate, have `assess` return the `gate_selector` boolean it already computes
  (`template_inventory.py:115`) and have `_blocking` read it. The tool's own comment names the
  reason: a fourth definition of green is how a tool starts disagreeing with the gate it predicts.
- **CROSS-HOST, refined:** do NOT re-key `_place_by_host`. Add a labelled `site_families` CANDIDATE
  tier over the existing exact-host buckets, keep exact host as the merge unit (bd-template-merge's
  single-host guard is CORRECT for drafts), and bridge a family into one template not by merging
  cross-host drafts but by **writing the login host into the content template's `match.hosts`** --
  the runtime tier the matcher already supports and nothing writes.

### 15.73 | Template viability at v3.66.984, and a comparison whose two halves shared the same defect

**Not a session close** -- no ITEM LEDGER; 15.70's open set (31, 33) stands, with
44 closed at 15.72.

**THE OPERATOR'S QUESTION**, 2026-08-09: "can any of the files, or a combination
of the same site captures, make a green and reliable template?" Shipped as
`bd-wacz-corpus --templates` -- a MODE, not a new tool, so `_TOOL_BUDGET` does
not move and the grouping is the same tiering `--hosts` prints (`_place_by_host`
is now shared; two copies of the placement rule is how the two modes would start
disagreeing about which captures belong to one site).

**NEITHER HALF OF THE QUESTION IS DEFINED HERE, DELIBERATELY.** Green is
`tools/promote_template.py`, mirrored by `template_inventory.assess` "so the
numbers can't diverge from reality" -- the mode RUNS it. Reliable is the
operator's own definition, support across a site's captures, which
`bd-template-merge` already records with denominators. A fourth definition of
green is how a tool starts disagreeing with the gate it exists to predict.

**THE DEFECT THAT MATTERS, AND IT WAS IN THE TEST AS WELL AS THE CODE.**
`build_template` emits `resolution_priority`; `assess` reads `resolutions`. The
key is created by `template_normalize.normalize_draft`, which sits between them
in the real pipeline (draft -> normalize -> promote -> reviewed) and which the
first implementation skipped. Measured on ONE capture:

    assess(raw)                  promotion_ready = False
    assess(normalize_draft(raw)) promotion_ready = True   resolutions=[1080, 720]

Skipping it would have graded **every one of the box's 153 hosts `not_green` on
the resolutions clause** -- a confident, uniform, wrong answer over the whole
corpus.

**AND THE TEST WRITTEN TO CATCH EXACTLY THAT COULD NOT SEE IT.** It compared the
mode's verdict against `assess(build_template(p))` -- the same skipped step on
both sides. Both said False, both agreed, and the test passed. **A comparison
whose two halves share a defect proves the defect, not the code.** It now runs
the canonical `build -> normalize -> assess` and asserts the canonical side is
TRUE first, so a fixture that cannot reach green fails loudly instead of making
the agreement vacuous. This is section 0 in a differential test: the denominator
was the pipeline, and both halves excluded the same part of it.

**RELIABILITY IS THREE-STATE, and `unknown` is the load-bearing one.** A lone
capture reports support 1 of 1, arithmetically identical to a selector every
capture agrees on -- `bd-template-merge` refuses to merge a single draft for
exactly this reason. Reported `unknown`, never `corroborated`. The distinction
earns its keep immediately: a fixture site scored `green_from_one` with the gate
selector supported **1 of 3**, so it is green on evidence one capture provided.
A tool without the third state reports that as simply "green".

Read off the GATE-CRITICAL keys only (`download.trigger`, `row_selectors`,
`button`). An average across all selectors would let a well-corroborated login
mask a one-vote trigger, and the trigger is what the template lives on.

**Two fixture defects worth recording, both the same shape as the code's.** The
helper set a top-level `resolutions` key on the capture -- a shape
`build_template` never reads, so the fixture described a capture that does not
exist; real resolutions arrive off the NETWORK LOG. And the green-only-merged
fixture gave one capture BOTH clauses, so it was green alone and the case was
never constructed. Read the callee before building its input.

### 15.72 | Item 44 CLOSED at v3.66.983: the corpus answered, and it found a second marker form

**Not a session close** -- no ITEM LEDGER, and 15.70's open set otherwise stands.
Item 44 is accounted by its inventory entry in 15.36, which carries the full
measurement; this section carries what generalises beyond it.

**THE SECOND DEFECT THE REAL NAMES FOUND.** @978 fixed `_is_redacted` testing
`endswith(".redacted.wacz")` -- 197 of 601 misclassified. The marker set it left
behind is DOT-prefixed, and the box also writes UNDERSCORE forms with a profile
qualifier. Measured over 742 real names: `.redacted` x329, `.scrubbed` x104,
`_redacted_safe` x4, `_redacted_strict` x2, `.redacted_2` x1. Six read as RAW
captures; the seventh based to the fragment `shaka_2`, which pairs with nothing.

**SIX FILES OF 742 IS 0.8% AND IT WAS STILL WORTH A CUT, FOR A REASON THAT IS
NOT THE COUNT.** The defect INVERTS A FINDING. `--dupes` reports a derivative
byte-identical to its source as a no-op redaction -- @971's class, the evidence
that scrubbing never happened -- and that branch requires `_is_redacted` to be
true. While those six read as raw, a failed scrub among them is offered back as
**reclaimable disk**, which invites deleting the evidence. Reproduced end to end
before the fix: `reclaimable_bytes=281, noop_derivatives=0` on a raw and an
underscore-form derivative that were byte-identical. **Size the impact by what a
wrong answer causes, not by how many rows it touches.**

**WHAT THE FIX ACTUALLY CHANGED ON THE REAL DATA**, recomputed over the run's own
file lists rather than estimated: merge candidates 79 -> 79, unchanged, and three
groups' source counts corrected -- `auth.wowgirls.com` 22 -> 20,
`site-ma.bangbros.com` 13 -> 11, `shaka-player-demo.appspot.com` 4 -> 3. Five
over-counted sources across three sites. Stated plainly because the honest
number is small and the temptation was to lead with the inverted finding instead.

**THE OVER-STRIPPING DIRECTION IS WHERE A CARELESS FIX BREAKS**, and it is
tested: `cap_355b_redacted_safe` must keep `cap_355b`, since a rule eating from
the first underscore would merge every `cap_*` capture into one bucket. And
`wowza` / `wowza-1` must NOT collapse -- different byte sizes on the box, so
they are distinct captures and `-1` is an index, not a marker.

### 15.71 | Operator decision 2026-08-09: standing merge authority, and item 44's grouping shipped

**Deliberately not titled a session close** -- it declares no ITEM LEDGER and it
does not supersede 15.70's open set. 15.70 remains the newest close.

**THE DECISION.** Matt, verbatim: *"you have authority to merge once band is
green and ci is green"*. Recorded in `CLAUDE.md` section 9 as the one standing
exception to the per-task authorization rule, because a standing grant cannot be
re-derived from source. Both conditions, measured -- CI alone is not the band and
never was. It is authority to MERGE, not to deploy; the box remains Matt's.

The exchange that produced it is worth keeping, because it is a general lesson
about this contract rather than about this cut. v3.66.981 sat green as a draft
PR -- band green, CI 5/5, mergeable clean -- and was not merged, on the reading
that section 9 makes a release change need per-task authorization. That reading
was correct at the time and the pause was the right default, but a green,
mergeable, fully-verified PR waiting on nothing measurable is a cost, and the
operator removed it. **A default that is safe is still worth naming out loud, so
it can be overridden.**

**ITEM 44'S GROUPING SHIPPED at v3.66.981** (`172b0d1`, PR #276) and the item is
still OPEN by design -- see its entry in 15.36 for what it owes and the three
numbers a box run must read. The short version: every fixture is BD-shaped, and
this is the item that exists because the tool was validated on synthetic
fixtures, met the real corpus and was wrong.

**ONE MEASUREMENT FROM THAT CUT THAT GENERALISES.** A backgrounded band reported
`completed (exit code 0)` while pytest was at 15%. That is the WRAPPER's exit,
not the tool's, and 15.70 records the same class twice. The written marker
(`... ; echo $? > band.exit`) is the only thing that was evidence, and it read 0
five minutes later. If a runner's exit arrives sooner than the runtime you
expect, you are reading the wrong process.

**AND ONE ABOUT THE PR SURFACE ITSELF.** GitHub strips tag-shaped spans from a
PR body **even inside backticks**: `t_<hex>_<name>` was stored as `t__` and
`?token=<scrubbed>` as `?token=`, which turned a sentence about redaction
replacing a value into one reading as though it emptied it. The API returns the
stripped text, so it is visible if you read the body back and invisible if you
do not. Write braces in a PR body, and read it back.

### 15.70 | SESSION CLOSE 2026-08-09 at 0e72394 (v3.66.979) -- twelve cuts, six items closed, and a regression my own band could not see

**Commit named is already on `main`** (the squash of PR #274), per section 4: a
close section must name a commit that is an ancestor and ALREADY merged, never
this branch's tip, which the squash destroys.

**CUTS @968-@979.** 42 (anchor gate sees frontend citations); 17 (a restart
survives the hook's re-record); 12 rename (each "missing" figure names its
mechanism); 971 (capture_scrub decides by content); 12 close (dead endpoint
retired); 973 (`bd-wacz-corpus`); 974 (`bd-template-merge`); 975/976 (capture
records); 977 (yt-dlp freshness compares versions); 978 (corpus classifier
repaired + hash dedup); 979 (the boot probe does no network).

The gate that enforces this block caught the first draft of this very section,
which stated the same items in prose. Its message is the rule: a close section
declares its items in a form a machine can read, and prose is the form that
failed.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33, 44
CLOSED: 12, 17, 29, 42, 43, 45

**BOX CAPTURES, all PASS:** @973 `a1bc58b` 15128/15043/85; @974 `495e943`
15138/15053/85; @976 `22bbb76` 15138/15053/85. Four exact predictions, and the
@974 one was PUBLISHED BEFORE the run existed -- the only form of that claim
worth much, since a number computed after the fact cannot distinguish a correct
derivation from an arithmetic slip that happens to match.

**THE FAILURE THAT MATTERS MOST, because the instrument was mine and I broke
it.** @977 put a live PyPI call inside `_check_ytdlp()`. Every existing test
mocking only `status_dict` silently got live data; the box caught it, the
container did not. No other probe in `healthcheck.py` touches the network --
that invariant was measurable and I never checked it.

It escaped a GREEN 39-file band because I derived that band with
`ls tests/ | grep -iE "healthcheck|selftest|ytdlp|doctor" | head -8`. There are
**15** matches. `test_v3_66_661_healthcheck_ytdlp_shape` -- the one file named
for the function I was changing -- sorts **TENTH**. A `head` truncated my own
denominator inside a band derivation, in a session whose recurring subject is
denominators. **Section 4 warns that `bd-band-derive` is a floor; it does not
cover a floor the author chops off for readability. Never pipe a band derivation
through `head` -- use `cat -n`.**

**WHAT CAUGHT WHAT, since the pattern is the transferable part.** Six confident
claims of mine were falsified by measurement: item 42's central premise, item
17's recorded framing, the WACZ size-collapse read (left UNVERIFIED rather than
re-guessed), two stuck-CI claims, and twice reading a piped exit code as a
tool's own. Four tests passed while proving nothing -- one matched the tool's
echo of a path containing its own test-function name; one was satisfied by
python's "can't open file" exit while the tool did not exist; two let competing
rules agree on their fixtures. Three mutants escaped. A `NameError` hid inside
my own boot-safe `except`. A band run collected ZERO tests because I invented
two filenames. **Every one was caught by an instrument -- a battery, the
operator's real file list, a box run, an exit code read unpiped. None by
review.**

**@973's tool was wrong on the corpus it exists for, and only real data showed
it.** Validated against synthetic fixtures and the Drive corpus's
`t_<hex>_<name>` naming, it met the box's ad-hoc names and classified 197 of 601
redacted files as RAW, knew nothing of `.scrubbed` (176 files), and reported
`sites=22 merge_candidates=0 STATUS OK` -- a clean answer over a denominator it
could not parse, inside the tool written to apply section 0. Repaired at @978.

**CORPUS STATE HAS CHANGED -- do not quote this session's figures.** The
operator ran their own hash dedup and MOVED the duplicates out of
`BulkDownloader` after the 1251-file / 4.04 GB measurement. Re-derive before
citing. One check is still owed on the moved pile: a `.redacted` file
byte-identical to its RAW SOURCE is not a duplicate, it is evidence the scrubber
returned its input (@971's class). Moved rather than deleted, so it remains
answerable.

**ITEM 44 IS FULLY DESIGNED AND UNBUILT.** Host-based grouping, three tiers,
reusing what exists rather than inventing:

  1. `dom_analyzer._parse_capture_host(stem)` -- already parses
     `{host}_{siteid}_{YYYYMMDD}`, which is exactly
     `auth.reptyle.com_0b60f1ec_20260629_145050_52e5.wacz`. BD's own captures
     resolve with no archive read. method=`filename`.
  2. `pages/pages.jsonl` -- the WACZ-spec page record `wacz_export._pages_jsonl`
     writes. First page URL -> `urlsplit().netloc`. The authority, and the only
     thing that will ever group `123.wacz` with `1232.wacz`. method=`archive`.
  3. stem fallback, method=`unknown` -- grouped under itself, never guessed.

  Every group carries the method that produced it. **Tier 2 belongs in
  `bd-wacz-corpus`, NOT in `dom_analyzer`**, whose contract is that scanning
  opens zero wacz zips and says so.

**OPERATOR DECISION TAKEN, NOT YET BUILT: the socket guard, STAGED.** An autouse
conftest fixture that RECORDS non-loopback connect attempts without blocking;
run the suite once to turn "21 files might call out" into a measured list; then
enforce in a second cut with an opt-out marker. Measured: no such guard exists
today, and 42 test files reference outbound APIs of which 21 never mention
loopback -- an upper bound on external callers, not a count, since a reference
may be a mock target or a string. The guard is also the instrument that settles
it.

**STILL OPEN FOR THE OPERATOR:** `_TOOL_BUDGET` went 235 -> 237 across two new
tools, raised with stated reasons rather than paid for by retirement -- name two
and they can be swapped. Item 31's eight rows need real sites and credentials.
Item 33 is a ratchet with no finish line, and its own text is stale (says a
240-tool population; it is 237).

**ENVIRONMENT: nothing was installed this session.** `check_requirements.py`
exits 0 against the committed manifests, so a fresh container needs no
additional provisioning. A stray empty-schema `downloader_history.db` in the
repo root (item 36's documented class, predating this session) was removed.

### 15.69 | SESSION CLOSE 2026-08-08 at 0f3e435 (v3.66.951 base) -- the operator queue, six cuts, and three gates that could not see their own subjects

**READ THIS FIRST IF YOU ARE A FRESH SESSION.** It supersedes 15.68's open set.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   31, 33, 44
CLOSED: 2, 3, 8, 11, 12, 13, 16, 17, 18, 20, 23, 29, 30, 32, 36, 37, 38, 39, 40, 41, 42, 43, 45

Item 1 is CANNOT-EVALUATE and is accounted by the inventory marker rather than
by this ledger -- a third state, not a close.

The close tip named above is `0f3e435`, the commit this branch was cut FROM and
already on `main` -- deliberately not this branch's tip, which the squash
destroys, turning a green pre-merge check red on `main` where no band reaches
it (CLAUDE.md section 4).

SIX CUTS, every band run with REAL pytest per section 4:

| cut | subject | band |
| --- | --- | --- |
| @952 | item 3 tier 0 -- a ratchet on the retired sandbox home | 255 |
| @953 | item 3 tier 1 -- bdenv.sh stopped destroying a correct browser pool | 231 |
| @954 | item 3 tier 2 -- the pk-mirror gate could not see 20 duplicates | 310 |
| @955 | the register-promise gate, both directions | 219 |
| @956 | item 18 adjudicated closed | 68 |
| @957 | item 12(c) -- a capped audit count says it is a floor | 271 |

**THE THREE FINDINGS WORTH MORE THAN THE CUTS**, all one shape: a gate whose
denominator excluded its own subject.

1. **`test_pk_mirrors_stay_retired` enumerated `toolchain/bin/*` and matched on
   BASENAME.** Twenty byte-identical duplicates whose origin is `tools/` or
   `toolchain/` were structurally invisible, and its `_NOT_A_MIRROR` exception
   declared `bd-scan.py` "a real tool with no toolchain/bin twin, not a copy"
   while it was byte-identical to `tools/bd-scan.py`. True as written,
   misleading in effect. Same tree, same file: **0 duplicates reported, then 21.**
2. **`toolchain/bdenv.sh:12` exported the retired browser pool
   UNCONDITIONALLY**, replacing `/opt/pw-browsers` (eight real builds) with a
   directory that does not exist, for anything run under `bd` or `bd-status`.
   Two tools had already ROUTED AROUND it in comments rather than fixing it.
3. **`audit()`'s docstring claimed `missing` and `size_drift` were one
   population.** One LIMIT is not one WHERE: the drift window adds
   `file_size > 0`, so they saturate independently and a single disclosure flag
   would have been wrong in both directions.

**THE METHOD LESSON: THE FIX REPRODUCES THE SHAPE OF THE DEFECT, and it landed
three times in one session.** Each was caught by RUNNING something, never by
review:

- the @953 test harness drove the child with `VAR=x . file` -- a prefix
  assignment to the source builtin, which bash RESTORES when the command
  returns -- so it reported "preserved" against the UNFIXED source and passed.
  A fixture that cannot represent the failure it hunts is not a test.
- @955's own unnumbered-promise test drove a HAND-BUILT dict, so the parser
  could drop a prose row unobserved; and its session-close predicate could
  match every section, because the newest section happens to carry a ledger.
  Both found by mutation, neither by reading.
- @955's direction B read only the NEWEST ledger, so writing THIS section would
  have made the eleven items 15.68 closed read as unaccounted -- the gate
  manufacturing a gap by the act of recording the next session. Fixed at @958:
  a close is permanent and CLOSED accumulates across ledgers.

**WHAT THE PROMISE GATE FOUND ON ITS FIRST RUN.** 15.68 accounted for 25 of 36
inventory entries; **eleven were accounted for nowhere** -- 1, 2, 8, 11, 13, 16,
18, 20, 23, 29, 30. Two of those are PARTIAL closes a prose reading scores as
whole: 15.68 closed item 11's DENOMINATOR question and item 29's DATABASE
RECOVERY, not the items. Item 18 was then adjudicated closed by RUNNING the tool
(`pytest>=99.0` exits 1), leaving a baseline of ten. Freezing rather than
adjudicating the rest is deliberate: writing statuses nobody measured is the
failure this register exists to prevent.

**THE TEN BASELINED ITEMS WERE ADJUDICATED, AND THE BASELINE IS NOW EMPTY.**
Direction B's baseline held eleven entries when it first ran; item 18 went at
@956 and the remaining ten at @959. The 15.51 pattern repeated almost exactly --
most were already closed and nobody had written it down:

| verdict | items |
| --- | --- |
| already CLOSED, unrecorded | 8, 16, 20, 23, 30 |
| MIS-SCOPED, premise false | 13 |
| CANNOT-EVALUATE, permanently | 1 |
| genuinely OPEN | 2, 11, 29 |

Item 30 had shipped at @932 -- `.githooks/pre-push`, tracked, enforcing section
7's two-dot diff -- three weeks before this. Item 20 had shipped at @889 and
CLAUDE.md section 4 has said so ever since; only this inventory disagreed. Item
16's subject is simply gone: zero tracked extensionless runnable files remain
under `project-knowledge/`, typing on the SHEBANG rather than the extension.

**AND THE ADJUDICATION ITSELF REPRODUCED SECTION 1's LESSON.** A grep for a
refuse path reported `bd-env-report-check` as having none. Run with no report
present it exits **2** with "UNKNOWN: no report at ...". The tool words it
"UNKNOWN:" and the pattern did not match -- grep is not a denominator, and the
behavioural test is one line longer. A second harness slip in the same pass:
probing that tool from a tmpdir with a RELATIVE `venv/bin/python` returned 127,
which reads as a tool failure and is section 5's interpreter trap.

**WHY THE RE-DERIVATION KEPT HAPPENING, AND WHAT NOW STOPS IT.** The operator's
complaint at this session's end was that every session re-measures what is open.
The cause is not carelessness -- it is that closures were announced in git and
never propagated here. Validated against history rather than asserted:

| item | closed by | and the PR title said |
| --- | --- | --- |
| 20 | `bfe4ac7` v3.66.889 (#193) | "the import-graph gate was blind to tests/" |
| 30 | `48707ad` v3.66.932 (#237) | "a pre-push hook for section 7's two-dot diff **(item 30)**" |
| 16 | `e7b2a5f` v3.66.917 (#219) | "three retired tools survived where no gate could see them" |

**PR #237's title names the item number, and this inventory carried it open for
three weeks.** The information was in git the whole time. So the fix is not more
diligence, it is a citation rule: `test_every_accounted_entry_cites_its_closure`
now requires every CLOSED or CANNOT-EVALUATE entry to point at a version, a
commit, an earlier register section, or a `file:line` anchor to the evidence --
four forms because closures arrive four ways, the last for an item closed by
AUDIT with no code, as item 22 was. Measured before the rule shipped: 18 of 18
accounted entries already satisfied it, so this pins a convention the register
was already keeping rather than imposing a new chore. An entry that says only
"CLOSED" now fails, because that is the entry that forces the next session to go
and measure whether it really is.

**SO `_UNACCOUNTED` IS EMPTY, WHICH IS THE GOAL STATE AND NOT A DISABLED GATE.**
Every inventory entry is accounted for, so direction B now runs with nothing
excused.

**CLAUDE.md CORRECTED IN TWO PLACES, both section 7.** Its CI bullet named
`test_pk_mirrors_do_not_drift`, a file that does not exist -- a band derived
from that paragraph died on `file or directory not found` while `ci.yml` was
correct throughout -- and it described two jobs and an inline gate lane after
CI had split into three jobs with a sharded `gate-suites`.

**ENVIRONMENT NOTES A FRESH SESSION WILL WANT.**

- **A shallow clone breaks `rev-list --branches --not --remotes`, not just
  `--is-ancestor`.** The operator's own session check reported 50 unpushed
  commits; the true answer was 0. Local `main` sat at the snapshot base below a
  50-deep graft, so the two chains did not overlap in the visible window. Only
  `--is-ancestor` exit 0 is trustworthy while shallow; `--deepen` then answers.
  This container was deepened to 426 and is no longer shallow.
- **The repo-root `downloader_history.db` present at session start was NOT
  item 36's** -- 217088 bytes / 17 tables / 2026-08-05, versus 12288 / 1 table
  / 2026-08-08. It is provisioning residue baked into the snapshot; see item 36.
  It was modified during item 36's investigation (18 tables now, still 0 data
  rows) and is ephemeral container state.

**OPEN, the complete set, machine-declared in the ledger above:** **2**
(`capture.sh` commit identity -- a release gate needing an explicit operator
GO), **11** (the repo-root `.db-wal` writer, authorized as two cuts and never
started; @955 and item 36 answered its denominator and its harm, not the cuts),
**12** (producer divergence -- 12(c) shipped at @957), **17** (needs a
`bd-restart-check` exit 1 in a container; not manufacturable), **29** (the
archive sequence -- 15.68 closed its database recovery; the purge and the
single verified bundle remain, and both are box-bound), **31**, **32**, **33**
(the large parallel program, unchanged), **38** (zip-era retirement, ~45% of the
executable `/home/claude` population), **39** (the twenty frozen duplicates).

Ten items, all numbered, none carried as prose.

**THE BOX CAPTURED v3.66.957 AND IT RECONCILES EXACTLY.** Operator capture
2026-08-08T22:29:49 at `51ac1eb` (the merge of #255), branch `main`:

    PASS - unit 14996 pass / 0 fail / 0 error / 85 skip; live 36 / 0 / 0
    15081 total    graph pin OK (003746d04276c6fb)    GET / 200, /api/health ok

| | total | passed | skipped |
| --- | ---: | ---: | ---: |
| v3.66.950 (15.68) | 15060 | 14975 | 85 |
| v3.66.957 | 15081 | 14996 | 85 |
| delta | **+21** | **+21** | flat |

Predicted +21 from the six cuts, counting test functions per commit against
each one's own parent. Nothing unexplained in either direction and skips flat,
so nothing silently became a skip. **The first attempt at that prediction said
+17 and was wrong** -- the parents were hand-mapped and @952's was the commit
AFTER it; deriving them with `git rev-parse <sha>^` gave +21. A wrong prediction
here manufactures a phantom gap in a clean capture, which is worse than not
checking.

**59 tests from this session's files ran ON THE BOX with 0 failures** --
register_promises_resolve 11, desandbox_tool_verifiers 11, library_audit_panel
_contract 9, cut25b 8, env_parity 6, pk_mirrors_stay_retired 5, v3_66_915 5,
sandbox_home_stays_retired 4. So @952-957's three gates and item 12(c) are
box-verified, not merely container-green.

One advisory, not a defect: 07b's selftest battery is `11 ok, 1 warn, 0 fail`,
the warn being `yt-dlp is 35 days old -- consider updating`. Operational, on the
box.

**SIX CAPTURES, SIX EXACT RECONCILIATIONS.** All PASS, 0 failed, 0 errors,
skips flat at 85 throughout. Recorded here because three of them existed only
in a conversation until v3.66.966, which is the failure this register exists to
stop:

| capture | commit | total | passed | delta | predicted from git |
| --- | --- | ---: | ---: | ---: | --- |
| v3.66.957 | `51ac1eb` | 15081 | 14996 | +21 | +21 |
| v3.66.959 | `86f139d` | 15087 | 15002 | +6 | +6 |
| v3.66.961 | `dc9dae4` | 15089 | 15004 | +2 | +2 |
| v3.66.962 | `3d229be` | 15088 | 15003 | **-1** | -1 |
| v3.66.963 | `3bb6c29` | 15092 | 15007 | +4 | +4 |
| v3.66.964 | `1875ce8` | 15095 | 15010 | +3 | +3 |
| v3.66.973 | `a1bc58b` | 15128 | 15043 | +33 | +33 |
| v3.66.974 | `495e943` | 15138 | 15053 | +10 | +10 |
| v3.66.976 | `22bbb76` | 15138 | 15053 | 0 | 0 (unchanged) |

Graph pin OK on every one, live 36/0/0 except @961's single WARN, `.err` files
empty throughout. @961's live warn was `L28 service-restart-preserves-queue --
queue is empty`, which is the check REFUSING to mint a verdict over an empty
denominator rather than passing vacuously; it returned to 36/0/0 the next run,
so it is queue state, not a defect.

**FOUR OF MY DELTA PREDICTIONS WERE WRONG BEFORE THEY WERE RIGHT, ALL THE SAME
CLASS, AND THE BOX WAS CORRECT EVERY TIME.** Recorded because the pattern is
the reusable part:

- hand-mapped parents: @952's "parent" was the commit AFTER it, predicting +17
  against a true +21;
- a range that included the BASELINE cut itself, predicting +10 against +6 --
  the previous capture was AT @957, so @957's own tests were already counted;
- **counting additions but not REMOVALS**: @962 retired the pk-mirror staleness
  test, so "adds no tests" was true and the delta was -1, not 0;
- and a `git grep` band extraction that silently truncated 501 files to 24.

Each manufactured a phantom gap against a clean capture, which is worse than
not checking. **The rule: derive the range from the PREVIOUS CAPTURE'S COMMIT,
take parents with `git rev-parse <sha>^`, and count removals as well as
additions.** A prediction stated confidently is the thing section 1 exists to
catch, and it applies to predictions about the box exactly as it applies to
figures read out of a document.

**@964 MADE IT TWO EXACT PREDICTIONS IN A ROW SINCE THE RULE ABOVE WAS
WRITTEN.** @964 added one file, `tests/test_v3_66_964_app_config_writer_does_not_lose_updates.py`,
collecting 3 -- predicted +3, measured +3, and 15095 / 15010 / 85 called to the
test.

**THAT UNCHANGED PREDICTION WAS NEVER EXERCISED, AND "VOID" IS NOT "WRONG".**
It read: the box is 3 cuts behind (@964 vs @967) and the next capture should be
**15095 / 15010 / 85 UNCHANGED**, because @965-@967 are register and CHANGELOG
edits touching no `tests/` path but the version pin. The box did not capture
@967. It jumped to **@973**, so the antecedent never held and the prediction is
VOID rather than falsified.

Its LOGIC was checked separately rather than assumed, because a void prediction
is exactly the kind nobody re-derives: `git diff --diff-filter=A 1875ce8..1316c7f
-- 'tests/test_*.py'` returns **nothing**. The @965-@967 range really did add
zero test files, so the reasoning held and only its premise moved. Distinguish
the two -- recording it as "wrong" would have retired a rule that works.

**@973 IS THE THIRD EXACT PREDICTION IN A ROW.** The whole +33 is attributable
to @968-@973: six new test files collecting 33, zero removed, predicted +33 and
measured +33, with skips flat at 85.

| file | |
| --- | --- |
| `test_v3_66_968_anchor_gate_sees_frontend_citations.py` | 3 |
| `test_v3_66_969_a_restart_survives_the_hooks_rerecord.py` | 7 |
| `test_v3_66_970_missing_labels_name_their_mechanism.py` | 4 |
| `test_v3_66_971_capture_scrub_sniffs_content.py` | 4 |
| `test_v3_66_972_library_missing_stays_retired.py` | 4 |
| `test_v3_66_973_wacz_corpus_survey.py` | 11 |

**AND IT SETTLES THE CONTAINER-ONLY SET BY MEASUREMENT INSTEAD OF BY ARGUMENT.**
The @972 full sweep in the cloud container returned 10 failures and each was
attributed by mechanism -- 7 `test_e2e_smoke` (no SPA backend), 2 `exec_bridge`
(absent interpreter), and one `test_provision_test_host` display-lock race whose
own helper refuses to guess a display number when its bounded range is
contended. The box, with the full environment, reports **0 failed**. All ten
pass there. Section 5's list of container-only failures is therefore confirmed
in KIND, while its COUNT is stale in both directions -- it records 14 (e2e x7,
`no_backend` x1, `exec_bridge` x5, vpn x1); the measured run gave 10, with
`exec_bridge` at 2 not 5, neither `no_backend` nor the vpn probe firing, and one
order-dependent case not on the list at all. Re-derive that figure; do not quote
it.

**@974 CAME IN AT EXACTLY 15138 / 15053 / 85 -- THE FOURTH EXACT PREDICTION IN
A ROW, AND THE FIRST PUBLISHED BEFORE THE RUN EXISTED.** The number above was
written into this register and into PR #270 at @975, hours before the box
captured `495e943`. That is the only form of this claim worth much: the earlier
three were computed after the capture arrived, where an arithmetic slip and a
correct derivation are indistinguishable to the reader. Full verdict PASS --
unit 15053/0/0/85, live 36 pass / 0 warn / 0 fail, graph pin OK (content hash
matched, exit 0), gui parity 1246 items, no non-empty `.err`.

**THE SERVICE SELFTEST CARRIES ONE STANDING WARN, AND IT IS OPERATOR ACTION
RATHER THAN A DEFECT:** `extractor_freshness` reports *"yt-dlp is 36 days old --
consider updating"*. Present in the @973 capture as well, so it is not new and
not caused by any cut here. It matters more than a cosmetic warn on a
downloader -- extractors break as sites change, and the whole live lane is
exercised through them -- but nothing in the repo can fix it; it is a box-side
`yt-dlp` update. Named here because a WARN that appears in every capture is
exactly the kind that stops being read.

**A TEARDOWN ARTIFACT THAT IS NOT A FAILURE, recorded so the next reader does
not chase it.** `06_live_tests.log` ends with `Future exception was never
retrieved ... TargetClosedError('Target page, context or browser has been
closed')`. The live lane's own verdict on the same run is **36 pass / 0 warn /
0 fail**, so this is a browser closing while an async future was still pending
during teardown, not a test outcome. Read the verdict line, not the log tail.

**AN UNCHANGED PREDICTION IS STILL A PREDICTION, and this is the one shape the
four errors above could not produce.** Every earlier miss was an arithmetic
slip inside a nonzero delta; a doc-only range predicts EXACTLY zero movement,
so any delta at all falsifies it outright rather than by a margin. If the next
capture is not 15095 / 15010 / 85, the cause is not this range -- look for a
test whose collection depends on tree state rather than on a file being added.

**NOT BOX EVIDENCE.** Every band here is a container band. @957 changed
`library_final.py` and the SPA, and `frontend/dist` is gitignored and NOT
delivered by `git reset --hard`, so the box needs `npm run build` -- which
`scripts/deploy.sh` does and a manual deploy does not.

### 15.68 | SESSION CLOSE 2026-08-08 at 47409ed (v3.66.950) -- seventeen cuts, four captures, and three tools that were lying

**READ THIS FIRST IF YOU ARE A FRESH SESSION.** It supersedes 15.59's open set.

STATE AT CLOSE, all verified rather than assumed:

| | |
| --- | --- |
| `main` | `47409ed` = v3.66.950 |
| the box | v3.66.950, captured PASS -- NOT behind |
| working tree | clean, nothing unpushed on any branch |
| last capture | 15060 total / 14975 passed / 0 failed / 0 errors / 85 skipped |

FOUR CAPTURES, FOUR EXACT RECONCILIATIONS. Every delta is one new test file and
nothing else moved; skips flat at 85 throughout:

    @946 3f7bc1a  15041   baseline
    @947 43b4fb0  15048   +7  (one file)
    @949 c86e25a  15053   +5  (@948 doc-only contributed 0)
    @950 47409ed  15060   +7  (one file)

**THE THREE FINDINGS WORTH MORE THAN THE CUTS**, all the same shape: a tool
reporting confidently about something it could not see.

1. **`bd-band` -- the runner section 4 MANDATES -- was running a pytest stub.**
   86% of its non-PASS output was manufactured (28 reported, 24 measured passing
   under real pytest, verified 24/24). It survived because every component was
   individually honest: the stub says what it is, `bd-band` already refused to
   band on an interpreter lacking pytest, and `bd-parband`'s selftest asserted a
   file was PRESENT -- unconditionally true. Three correct-looking parts
   composing into a wrong answer. See 15.67.
2. **Item 34 was never SSRF or VPN.** A test shipped at @940 leaked
   `BD_INSTALL_DIR=v3` into the process; the four named tests were downstream
   victims of a relative install dir. Three sessions read the item's title and
   went looking in the wrong place. See 15.63.
3. **The full suite does not hang**, and both reasons the prohibition rested on
   were phantoms -- `test_perf_lab` passes in 2.5s, and the "second hanger"
   names a file that has never existed in any variant. See 15.65/15.66.

**THE METHOD LESSON THAT GENERALISES: a clean result from a PROVEN instrument is
a finding; the same result from an unproven one is worth nothing.** A cwd probe
proven able to detect a deleted cwd returned zero across the full band, and that
NEGATIVE is what killed the obvious hypothesis and forced the search to the call
site, where the real cause was. Every instrument this session was proven in both
directions before any result was read, and it paid three times.

**FOUR OF MY OWN MEASUREMENTS WERE WRONG FIRST, all caught by running them
rather than by review.** Recorded because the pattern is the point -- none was
caught by reading:

- a synthetic leaking test written into a tmpdir never loads `tests/conftest.py`
  (pytest resolves conftest from the TARGET FILE'S ancestors), so the harness
  reported "1 passed" and the assertion read that as "the guard is broken";
- an assertion required a bystander suite to PASS with a broken install dir,
  when the subject was the guard's CONTRIBUTION, not the suite's verdict;
- a fence-only document scanner could not see `requirements-test.txt`, which has
  no fenced blocks -- the one file carrying the offence;
- "the stub also HIDES a real failure" was stated before it was checked and is
  RETRACTED: that file passes in isolation under both runners and fails only in
  a co-batched run. A per-file-isolated run was compared against a co-batched
  one and the RUNNER was blamed for an ISOLATION difference.

**CLOSED THIS SESSION:** 5, 7, 9, 10, 14, 19, 21, 25, 26, 27, 28, 34, 35, item
29's database recovery (108 files, all `integrity_check = ok`), and item 11's
denominator question -- 0 inside-repo connects across the full suite in BOTH
parallel and serial configurations.

ITEM LEDGER -- machine-checked by tests/test_register_promises_resolve.py
OPEN:   3, 12, 17, 31, 32, 33, 36, 37
CLOSED: 5, 7, 9, 10, 14, 19, 21, 25, 26, 27, 28, 34, 35

A transcription of what this section already declared in prose, not a
re-adjudication. 12 is carried by its (c) sub-part; 37 is the register-promise
gate, which this section named without a number.

**OPEN, and nothing here is blocked on measurement:**

- **36** -- the unattributed writer (new; four candidates already ruled out)
- **3** -- the `/home/claude` scope draft, operator-approved to start
- **12(c)** -- disclose the saturation cap, operator-approved: new key, API + panel
- **the register-promise gate**, full form, operator-approved
- **31**, **32**, **33** -- the large parallel program, unchanged
- **17** -- needs a `bd-restart-check` exit 1 in a container; not manufacturable

**QUEUE ORDER the operator set:** item 3 draft, then the promise gate, then
12(c). Item 36 is unranked -- it is new and the operator has not seen it.

**ONE ENVIRONMENTAL NOTE FOR THE NEXT SESSION:** `pytest-timeout` is declared in
`requirements-test.txt` as of @949 and is required by the sanctioned full-sweep
form in section 5. It was installed BY HAND in this container, so a fresh one
gets it from the manifest -- but if the sweep ever reports no timeout guard,
that declaration is the thing to check first.

### 15.67 | The band tool CLAUDE.md mandates was running a pytest STUB, close at c86e25a

The most load-bearing finding of the session, and it arrived sideways: while
proposing to point section 5 at `bd-fullsuite`, checking what that tool actually
runs turned up that **`bd-band` and `bd-parband` never ran pytest either**.

MEASURED at v3.66.949, whole suite, per-file isolation on both sides:

| | files |
| --- | --- |
| the shim reports non-PASS | 28 |
| of those, PASS under real pytest (verified 24/24) | **24** |

**86% of the mandated band tool's output was manufactured.** One file, both
runners, same tree and interpreter: `tests/test_codex_handoff_stays_retired.py`
is `4 passed` under pytest and `IMPORT ERROR: No module named 'tracked_source'`
under the shim. `tests/` is not on `sys.path` there, so the 22 files importing a
sibling helper all die on import -- the floor, not the total.

**WHY IT SURVIVED SO LONG: nothing was wrong on the surface.** `run_tests_core`
is honest in its own docstring ("NOT a replacement for pytest in production").
`bd-band` already REFUSED to band on an interpreter that could not import
pytest, reasoning that such a runner "would report failures that are interpreter
artifacts, not defects" -- the right conclusion, half applied. And
`bd-parband`'s selftest asserted its delegation target was PRESENT, which is
always true in a checkout, so it reported PASS over the wrong runner for years.
Three correct-looking components composing into a wrong answer.

**THE SESSION'S BANDS WERE NEVER AFFECTED, by accident.** Every band here ran as
`venv/bin/python -m pytest <files>` directly rather than through `bd-band`. That
is why none of this bit, and it is now what section 4 documents -- previously the
contract mandated the tool and the tool was wrong.

**THREE MORE DOCSTRING DEFECTS, all found by the gate and none by review:**

- `bd-band`'s docstring advertised the shim command as the incantation it
  replaces, and cited the "known whole-dir / test_perf_lab / nav_guard hangs"
  that 15.65 disproved. Citing them was citing nothing.
- `bd-parband`'s selftest checked for the PRESENCE OF A FILE. Presence is not
  reachability of a runner, and the file is unconditionally present.
- `bd-fullsuite`'s docstring opened *"run the ENTIRE tests/ suite in-sandbox,
  **correctly**"* with no mention of the shim. **That word is what caused this
  session's wrong recommendation** -- it read as a stronger instrument than
  pytest and it is a fallback for machines that cannot run pytest at all.

**A RETRACTION, kept because it is the method lesson.** The cut was first argued
on "the shim also HIDES a real failure", from `test_t14_vpn_probe_egress`
failing under pytest and passing under the shim. It does not: that file passes in
ISOLATION under both runners and fails only in a co-batched xdist run. The
comparison was a per-file-isolated shim run against a co-batched pytest run,
blaming the RUNNER for an ISOLATION difference -- two variables moved and one was
credited. Caught by measuring the file directly before writing it into a commit
message. The case rests on the 24, each verified individually.

**THE SWAP DELETED CODE.** @897 had to detect "nothing ran" by string-matching
an UNEVALUABLE banner, because the shim prints a reassuring `Total: 0 |
Failed: 0` beside it. pytest gives that state as exit code **5**, so the third
state survives and is read from a number that cannot drift. `grade_pytest_rc()`
is a named function with a positive control rather than an inline predicate only
the mutated test could observe -- the @939/@944 lesson applied on sight.

### 15.66 | The full suite does not hang: the exemption, the measurement, and what it does NOT license, close at 43b4fb0

15.65 left the no-full-suite rule standing on the one case that could not be
tested without breaking it. The operator granted a one-time 30-minute exemption
and it settled the question.

**MEASURED at v3.66.948**, with `pytest-timeout` installed specifically because
it is the only instrument that converts a hang into a NAMED test with a stack
rather than an unexplained stall:

```
tests/  -n 4 --dist loadfile --timeout=240 --timeout-method=thread -q -p no:randomly
14 failed, 14943 passed, 91 skipped in 635.42s (10m35s)
tests exceeding 240s: ZERO -- the guard never fired
```

The 14 are the documented container-only set: `test_e2e_smoke` x7, the
`no_backend` body-contract case, absent-interpreter `exec_bridge` x5, a
no-tunnel vpn probe. **Item 34's four order-dependent failures are ABSENT**,
which is @945's fix holding at full denominator and a second confirmation of
that cut.

**NO SUBAGENTS WERE USED AND THAT WAS THE RIGHT CALL.** The offer was made; the
bottleneck is four cores running CPU-bound tests, and agents contend for exactly
that. CLAUDE.md section 5 already records eleven agents being SLOWER than inline
at v3.66.926 for the same reason. Reach for parallelism when the constraint is
reasoning, not when it is cores.

**WHAT IT DOES NOT LICENSE**, now written into the contract rather than left to
a reader's judgement:

- **One ORDERING was measured.** `-p no:randomly` with `--dist loadfile` keeps
  each file whole on one worker, so an interleaving-dependent hang was never
  given the chance. One green run is not a proof of absence.
- **It was PARALLEL.** The original prohibition most plausibly concerned a
  serial local run; that is a different denominator and remains untested.
- **It is not the box.** Section 7 is unchanged -- 14 of those failures are
  environmental here and pass on `test4`. Use the sweep to answer "does anything
  hang or interact", never "is the tree good".

**THE INSTRUMENT HAD TO BE DECLARED, NOT JUST USED.** Section 5 records that
anything installed by hand lives only until the session ends. A relaxed rule
depending on an undeclared `pytest-timeout` hands the next agent the sweep with
nothing watching -- and a hang becomes an unexplained stall again, which is
exactly how the phantom second hanger got recorded. It is declared in
`requirements-test.txt` for the reason pyflakes is there: `requirements-dev.txt`
is not on the deploy path.

**AND THE TEST MANIFEST HAD BEEN INSTRUCTING AGENTS TO BREAK THE RULE.** Its own
comment block advertised the whole suite with no timeout as the way to run
everything. The file you read to set the environment up told you to do the one
thing the contract prohibited -- the two-agent-facing-instructions defect
section 8 exists to stop, with the losing copy in a manifest nobody thinks of as
a document. Removed.

**THE GATE'S OWN SCANNER HIT THREE TRAPS THIS CONTRACT DOCUMENTS**, all three
caught by running it and none by review, which is the reusable part:

1. **Fence-only scanning could not see its subject.** `requirements-test.txt`
   has no fenced blocks, so the denominator structurally excluded the one
   document actually carrying the offence -- in a comment, which in a manifest
   IS the documentation. The scan reported clean over the thing it was written
   to find.
2. **A line-scoped check failed the CORRECT command.** The sanctioned invocation
   is backslash-continued, so the `--timeout` that makes it legal sits on line
   two. Section 0 records that shape three times over shell loops; this is a
   fourth, in a different syntax. `tests/shell_source.blocks_containing` was
   deliberately NOT reused -- its subject is enclosing shell CONSTRUCTS, not
   line continuation, and reaching for it would be the right tool on the wrong
   question.
3. **The comment explaining the removal named the removed commands**, putting
   them back in the file and tripping the gate. Cite the mechanism, never the
   literal.

### 15.65 | The phantom second hanger: both reasons for the no-full-suite rule are disproven, close at 43b4fb0

The operator chose "identify the phantom second hanger" over "add a sharded
container lane", on the argument that the no-full-suite rule could then be
relaxed with evidence rather than by assertion. The evidence came back and it
does not license relaxing the rule -- which is a more useful answer than the one
that was hoped for.

WHAT WAS MEASURED, every run individually bounded so the investigation did not
violate the rule it was investigating:

| claim the rule rested on | measured at v3.66.947 |
| --- | --- |
| `test_perf_lab.py` is THE recorded hanger | 17 passed in **2.5s**, and identically with `BD_DISABLE_KEEPALIVE` POPPED |
| a second hanger, `test_v3_66_146_nav_guard` | **no file of that name exists**, in any variant |
| the two real `146` files are slow | 23 passed in **0.77s** |
| a sweep of hang-prone shapes | **79** files (6% of 1270), zero hangs |

The sweep's predicate: `while True`; `.join()`/`.wait()`/`.acquire()` with no
timeout; `subprocess` with no `timeout=`; unbounded HTTP. 69 real test files all
completed; 9 were helper modules under `tests/_phase_scripts/` and
`tests/scan_wait.py` that collect nothing.

**THE SINGLE TIMEOUT WAS THE INSTRUMENT.** `test_fuzz_harness_frontend.py` hit a
60s cap; raised to 120s it returns 0 in 75s with 102 passed. **A timeout is not
evidence of a hang unless the bound EXCEEDS the legitimate runtime** -- set it
from the slowest known file, not from a guess. Written up as a hang it would
have sent the next session after a defect that does not exist, which is exactly
the phantom this work set out to close, recreated by the work itself.

**AND THE SWEEP'S OWN LOOP LOST A FILE.** The candidate list was written without
a trailing newline, so `while read` silently dropped its last entry; "full
coverage" would have been false by one. Caught only by diffing the input list
against the results. The dropped file then probed clean in 1s. A denominator
that shrinks in silence is section 0 in the measuring apparatus, and `while read
< file` does that by default.

**WHY THE RULE STANDS.** The untested case is a hang that emerges only in a
FULL-SUITE run, through order or resource interaction no per-file probe can
reproduce -- and testing it requires running the full suite, which is the rule.
Circular by construction. Item 34 is the precedent and it is not hypothetical:
four failures that appear only in a multi-file band and pass 15/15 in isolation.

So the rule's basis moved from "two named files" to "an untested interaction
case", and CLAUDE.md section 5 now says that in as many words -- because the
named files are gone, and an agent citing them is citing nothing. **If this is
ever to be settled, the sharded-lane option is the only instrument that can do
it**; the operator declined it once in favour of this measurement, and this
measurement is the argument for reconsidering.

### 15.64 | @945's guard broke the rule @945 shipped, close at 77d821a

@945 closed item 34 with an autouse guard that fails any test leaving
BD_INSTALL_DIR relative. Its CHANGELOG states CLAUDE.md section 0's rule -- "the
parent's value is part of the denominator" -- and the guard it added reads the
parent's value and blames the test for it. Measured with a `.env` carrying
`BD_INSTALL_DIR=v3`:

    @945 : tests/test_contracts.py -> 5 failed, 9 passed, 12 ERRORS
    @946 : tests/test_contracts.py -> 5 failed, 9 passed,  0 errors, 1 warning

**The exposure was real and operator-triggerable.**
`bulk_downloader/__init__.py:31` seeds from `.env` at PACKAGE IMPORT, and
BD_INSTALL_DIR is an `EDITOR_KEY_NAME` -- so one save through the GUI env editor
puts a relative value in front of all 1268 tests, and most of a capture fails
with each failure naming whichever test happened to be running. It did not fire
on 2026-08-08 only because `ls ~/BulkDownloader/.env` returned no such file.

**No band could have caught it, which is the structural point.** No test in the
114-file band runs with a `.env` present, so the condition never arose. It was
found only because the operator asked "how do we test this without a capture",
and answering that meant computing the guard's true denominator -- which turned
out to be every test that imports the product, conditioned on a file the
operator controls. **The question exposed the defect; the band never would.**

TWO OF THE FIX'S OWN MEASUREMENTS WERE WRONG FIRST, both caught by running them
rather than by review:

- **A harness whose subject was absent.** The test proving the guard still
  catches a real leak wrote a synthetic leaking test into a tmpdir. pytest loads
  `conftest.py` from the TARGET FILE'S ancestors, and a file under `/tmp` has
  none -- so the guard was never installed, the run said "1 passed", and the
  assertion read that as "the guard is broken". That reading would have sent a
  session rewriting a guard that worked. **A file outside `tests/` does not get
  `tests/conftest.py`**; inject into a real repo test through a plugin hook
  instead.
- **An over-scoped assertion.** The inherited-environment test first required
  the bystander suite to PASS with a relative BD_INSTALL_DIR. It does not and
  should not -- a relative install dir genuinely breaks the app, so 5 of those
  tests fail on their own merits and always did. The subject is the guard's
  CONTRIBUTION, not the suite's verdict; asserting the verdict makes the
  denominator "every failure in the run". Section 1, in a test file.

**Mutation caught the one that mattered.** `verdict-always-leaked` -- literally
the @945 behaviour -- is CAUGHT, so the fix is constrained against the defect it
removes rather than merely differing from it. One escape closed on the way:
repairing by POPPING the key passed every assertion, because "must be restored"
and "must be removed" are indistinguishable without a positive control.

### 15.63 | Item 34 root-caused and CLOSED at v3.66.945, close at bda4580 -- and a PROVEN NEGATIVE is what made it findable

Item 34 was misread three times as "four order-dependent SSRF/VPN band
failures". Every word of that except "four" was wrong. Full mechanism in the
v3.66.945 CHANGELOG entry; what belongs here is the method, because the method
is reusable and the finding is not.

**THE INSTRUMENT THAT FOUND NOTHING IS THE ONE THAT MATTERED.** The obvious
hypothesis was a relative path resolved against a DELETED cwd -- the error is
`unable to open database file`, and item 11 had just made that shape familiar.
A cwd probe at every test setup/teardown, **proven in both directions first**
(it detects a deleted cwd; it reports 0 on a clean isolated run), returned
**0 broken boundaries across the full 113-suite band**. That negative killed the
hypothesis and forced the search from the boundary to the call, where wrapping
`sqlite3.connect` and `os.environ.__setitem__` named the leak immediately.

Generalise it: **a clean result from a proven instrument is a FINDING, not a
non-event.** The same clean result from an unproven one is worth nothing, which
is why the proving step is not optional. Three instruments were built here and
all three were proven both ways before any result was read.

**THE RULE, now stated because CLAUDE.md only had its mirror.** Section 0 says a
test that VARIES an environment variable must POP it. The inverse is equally
load-bearing: **a test that exercises a real environment WRITER must CONTAIN the
write.** `monkeypatch` can only undo what it RECORDED; a direct
`os.environ[k] = v` inside the code under test is not recorded, and popping the
key on entry records nothing to restore. Popping on entry is necessary and not
sufficient.

**THE GUARD REPAIRS BEFORE IT FAILS**, and that is not politeness. A leaked
environment variable cascades -- every later test fails too -- and the one test
that caused it is buried under a hundred victims. That burial is exactly why
this item survived three readings. Naming the leaker and then cleaning up keeps
the rest of the run meaningful.

**THE REGISTER'S OWN EVIDENCE WAS TRUE AND USELESS, twice.** Item 34 recorded
these as "proven pre-existing on pristine `8e2b017`". `8e2b017` IS v3.66.942 --
*after* the @940 cut that caused them. Proving a defect pre-dates the cut you
happen to be testing says nothing about which cut caused it. Both times the
measurement was real, careful, and answered a question nobody had asked. That is
section 1's "a verification can answer a different question than the item asks",
in a register entry rather than in code.

**Item 35 is unaffected and still open.**

### 15.62 | Operator-gated items worked on the box 2026-08-08, close at 66db5fe -- items 21 and 25 closed, and `git bundle verify` was caught reporting OK about an unrestorable bundle

The operator asked what could be closed on the box straight after a capture.
Five items were attempted; three closed, one was already fine, and the method
failures cost more than the items did.

TWO BOX CAPTURES, BOTH PASS, BOTH RECONCILED EXACTLY

| capture | commit | total | passed | skipped |
| --- | --- | --- | --- | --- |
| v3.66.942 | `8e2b017` | 15037 | 14952 | 85 |
| v3.66.943 | `66db5fe` | 15021 | 14935 | 86 |

The @943 delta is **-16**, every line explained: `test_pk_mirrors_do_not_drift`
-10 (deleted), `test_pk_mirrors_stay_retired` +4 (added), and -10 mirror-related
tests across `test_bd_ready_preflight` (-1),
`test_bd_regen_check_is_read_only` (-2), `test_generated_artifact_workflow`
(-5), `test_toolchain_534` (-1), `test_versync_gate` (-1). Skips 85 -> 86 is
`test_bd_doctor_probes_the_real_environment::test_the_mirror_matches`, reason
`project-knowledge/bd-doctor does not exist` -- correct, and **retired at @944**
because after @943 it could only ever skip.

**A deploy that looked interrupted was not.** The v3.66.942 deploy was read here
as having stopped at the diffstat preview, because the box reported
`origin/main = 8e2b017` while this container had `66db5fe`. Wrong: `66db5fe`
merged at 22:14 EDT and the box fetched at ~00:29Z, so 943 did not exist yet.
The deploy was correct and complete. **Check the merge timestamp before
concluding a fetch was truncated** -- two branches of a story that differ only
in when they happened look identical in the output.

ITEM 21 -- CLOSED, NOTHING LOST, AND THREE WRONG INSTRUMENTS IN A ROW

The question: did force-pushing `preflight-setup-bh5n4z` discard work?
`b4f0c80` turned out to be **tagged** (`archive/preflight-preforce`), so the
"unreachable, on a two-week gc clock" premise this session opened with was
false -- and the tag was visible in the first command's own output.

Three instruments were proposed and each was blind to a different property of
this repo:

1. **Two-dot tree diff** (`git diff --stat origin/main b4f0c80`) -- returned 729
   files / -96117 lines, essentially all of it *main having moved on*. CLAUDE.md
   section 7's two-dot rule is for proving a branch's content is already merged,
   where the two tips are supposed to be content-identical. Against a five-
   commit-deep old line it drowns the subject in noise.
2. **Ancestry** (`merge-base --is-ancestor`) -- exit 1, authoritative (the box's
   clone is **not shallow**; `.git/shallow` absent, which also settles the
   "unverified" note in CLAUDE.md section 5). But it answers *were these commits
   kept*, not *was the work lost*.
3. **Patch equivalence** (`git log --cherry-pick --right-only`) -- printed eight
   commits, and **this repo squash-merges**. A squash of five commits has ONE
   patch-id; the five originals have five. They can never match, so the tool
   structurally cannot detect a squashed re-landing. Its non-empty output was
   not evidence of loss.

**What actually answered it was content, measured in the tree** -- every feature
in those commits is in `main` today: `tools/live_seed.py` (108400 bytes);
`capture.sh:756` step `[5a/9]` with `cleanup_live_seed` + the EXIT trap at
`:219-226`; `capture.sh:947` step `[5b/9]` via `bd_start_display`;
`live_tests/checks.py:3306` `done_today_count` with its comment intact;
`tests/test_provision_test_host.py` and `tests/test_live_checks_api_key_contract.py`;
lint handling in `scripts/lib/system_deps.sh`. `capture.sh:954-958`'s comment is
near-verbatim from `60c48d9`'s commit message -- the prose travelled with the
code. And the right-hand range named the mechanism outright: PR #32 is
*"Gate the provisioner's verdict logic, and seed synthetic input for the live
checks."* The force-push replaced the commits; the PRs re-landed the work.

Keep the tag. It cost nothing and it is the only reason this was answerable.

ITEM 25 -- CLOSED, AND IT ACCIDENTALLY GOT A MUCH STRONGER PROOF

`~/bd-orphans-2026-08-01.bundle` sha `a86a8fc4a31a...` matches on the original
and on the copy at `~/backups/`, `git bundle verify` clean, 24 refs.

**`git bundle verify` is NOT proof a bundle can be restored from, and this
session measured that.** `~/BulkDownloader-dp06.bundle` verified clean --
*"records a complete history"*, *"is okay"* -- and then failed to fetch into a
fresh empty repo:

    error: Could not read 8af6889cd493...
    fatal: Failed to traverse parents of commit 10231e54...
    error: did not send all necessary objects

MEASURED: those contradict. BEST EXPLANATION, not measured: `verify` only checks
that the bundle's declared *prerequisites* are satisfiable, and prints "records a
complete history" when the header declares none; it does not walk the packfile.
Run inside `~/BulkDownloader`, the objects exist locally anyway, so there is
nothing for it to notice.

That lands directly on 15.4, which says the orphan bundle was *"Verified BEFORE
trusting it, because a sole copy that cannot be cloned from is not a backup"* --
using the check that has now demonstrably passed a bundle you cannot clone from.
The orphan bundle **did** pass the strong test, as a side effect of the dp06
comparison: it fetched cleanly into the scratch repo, both shas resolved,
ancestry computed. **The rule to carry:** any bundle you intend to rely on gets

    T=$(mktemp -d) && git init -q "$T" && \
      git -C "$T" fetch "$BUNDLE" 'refs/*:refs/restored/*' && echo RESTORABLE

Apply it to whatever 29's consolidation produces, before deleting the sources.

`~/BulkDownloader-dp06.bundle` (18M, not in this register before) holds
`a106c763` for `fix/dp06-semantic-parentage`; the orphan bundle holds
`bf9f9721`. Measured: `a106c763` IS an ancestor of `bf9f9721`, the reverse is
not, and `bf9f9721..a106c763` is empty -- so the orphan bundle is a strict
superset, 15.4's completeness claim stands, and dp06.bundle is redundant **and**
broken.

ITEM 29 -- RECOVERY COMPLETE, AND THE REGISTER'S COUNT WAS WRONG

| | 15.10 said | measured 2026-08-08 |
| --- | --- | --- |
| `.db` | 91 | **108** |
| `.db-journal` | 90 | 90 |
| no journal | 1 implied | **18** |

108 - 18 = 90, so it reconciles internally and the measurement is the truth.
All 18 journal-less files are under `.worktrees/` and `cockpit_tasks/` -- the
same tree 15.10 lists as rebuildable bulk to be purged.

**All 108 passed `PRAGMA integrity_check`**, and 108 `.clean.db` were produced
by `VACUUM INTO`. `find` for `*.db-wal` / `*.db-shm` returned **0**, so every
database there is rollback-journal mode and there were no companions to miss.
Two method notes worth keeping: copies were keyed by a path-hash prefix because
an 11G tree holds many files named `downloader_history.db` and a flat copy would
have overwritten silently; and the `.clean.db` set was deleted and regenerated
once because two `VACUUM INTO` runs straddled the copy loop and their provenance
could not be established.

Remaining in 29: the purge, the consolidation, the 533 raw wacz decision and the
B2 dedup.

THE PROTON PASS EXPORT IS ABSENT, AND THE INVENTORY IS STILL BLIND

`~/BulkDownloader 3` exists at 18M -- so the denominator is real and the empty
result means something. Name sweep, July-`.xlsx` sweep and a zip/7z content
sweep all clean. Snapshot sizes match 15.10 exactly (328M / 3.0G / 18M / 11G),
so only the `.db` count had rotted.

`grep -n 'kdbx\|Pass_export\|keepass\|bitwarden\|1password' ~/archive_inventory.sh`
returns **nothing** -- the v2 inventory that measured all four snapshots still
cannot see a password-manager export, which is why this file had to be found by
hand. Add `*_export_*.csv` too: 1Password, Bitwarden and LastPass all export CSV
by default, which is plaintext credentials with no extension tell. That file is
in the operator's home, not the repo; nothing in `tools/` or `toolchain/bin`
scans for this class either.

STILL OPEN, unchanged: **3** (scope call, now unblocked), **12(c)**, **31**,
**32**, **33**; **17** needs a `bd-restart-check` exit 1 in a container; **21**
and **25** are now closed.

@944 WROTE A CONTENT GATE INSIDE THE FILE THAT ARGUES AGAINST CONTENT GATES

Kept because it is the cheapest instance of CLAUDE.md section 0's
fix-reproduces-the-defect rule yet recorded, and because **the band caught it,
not review**. The new test file's docstring states in as many words that its
assertions are *"deliberately name-level, not content-level"*, on the argument
that a content gate would fail on every `SESSION_CARRY.md` edit -- most cuts --
and a gate that fires that often gets switched off. Its last test then shelled
out to `bd-kb-sync check` and asserted **exit 0**, which folds `changed` into
the verdict. It went red on `SESSION_CARRY.md CHANGED`, for the register edit
belonging to this same cut.

Repaired by calling `diff()` in-process and asserting on `added`/`removed` only,
with `changed` explicitly left alone. The test keeps its actual value -- it is
the only one enumerating the way the TOOL does (`os.walk`) rather than the way
git does, which is the gap that hid the bytecode entry -- without the tax.

**Consequence for future cuts: the manifest reseed is a LAST step**, after the
final `project-knowledge/` edit, in the same way `bd-regen-order` must follow the
last source edit (section 2a). Nothing enforces the ordering; the name-level
gate deliberately will not fire on a stale sha, because making it fire is the
over-sensitivity that gets gates switched off.

**RECORDED LATE, AND THE LATENESS IS THE POINT.** Four order-dependent band
failures (webhooks-SSRF x3, vpn-quarantine x1) were proven pre-existing on
pristine `8e2b017` with an identical 130-suite list during the @943 session, and
this register was told they had been written down. They had not -- a grep for
them here returned zero. A finding that exists only in a conversation is lost at
the next context boundary, which is the failure this register exists to stop.
**Now item 34** in 15.36's inventory, with item 35 beside it.

**AND NOTHING COULD HAVE CAUGHT THAT, WHICH IS THE STRUCTURAL POINT.** The
operator asked why the standing rule is not enforced. Measured at v3.66.944, the
enforceable half IS: `bd-freshcheck`'s `_DOCS` covers CLAUDE.md and this file
(211/211 anchors resolve), the close-tip check requires an ancestor commit, and
`bd-doc-truth` scans 78 documents including 13 root ones. All green, all in CI.
But every one of those asks whether a cited **path** resolves. None can ask
whether a claim is TRUE, and none can ask whether a finding somebody SAID they
recorded exists -- the denominator for that lives in the conversation, so a
prose promise is unfalsifiable by construction.

The mechanizable fix is a policy first and a gate second: **a finding is a
numbered item in this inventory or it does not exist.** Prose promises
("recorded as", "filed as") stop being an accepted form; the gate then reduces
to a set comparison -- every item named in a close section's STILL OPEN list
must resolve to a numbered entry. Not built. Recorded here so the next session
does not re-derive the argument from scratch.

### 15.61 | Box capture at f7367487 (v3.66.941) -- PASS, and it settles three claims a container could not

The operator captured the merge commit directly. Everything below is read from
the bundle, not inferred.

VERDICT: **PASS**. unit 14945 passed / 0 failed / 0 errors / 85 skipped;
live 36 / 0 / 0. `/api/health` reports version 3.66.941, sha f7367487e312,
db_ok true, 1002 routes. Graph pin OK (content hash matches). Selftest 12
checks: 11 ok, 1 warn, 0 fail.

THE DELTA RECONCILES EXACTLY, WHICH IS THE CHECK WORTH DOING. The previous box
capture was at **v3.66.928** (not 933 -- 15.56's sentence says "v3.66.928 was
verified on the box" and it is easy to misread as the section's close version):

    14894  @928 capture
    +  42  cuts 929-933   (929:12, 930:13, 931:7, 932:10, 933:0)
    +  94  cuts 934-941   (934:16 935:18 936:17 937:9 938:11 939:8 940:9 941:6)
    -----
    15030  predicted
    15030  ACTUAL          delta 0

Skips unchanged at 85 across all thirteen cuts. Nothing unexplained in either
direction -- a surplus would mean something ran that should not, a deficit that
something silently stopped collecting. The per-suite counts were read from the
JUnit XML rather than from the cuts' own claims, so they are a measurement of
what the box collected, not a restatement of what was written down.

THREE CLAIMS SETTLED THAT THIS CONTAINER STRUCTURALLY COULD NOT

1. **`tests/test_e2e_smoke.py` is environmental -- CONFIRMED.** 7 testcases, 0
   failed, 0 skipped on the box. CLAUDE.md section 5 carried it as a
   container-only failure with an explicit caveat that there was no box
   evidence. There is now. The caveat can go; the entry stands.
2. **The `no_backend` `/api/live/watch` case is environmental -- CONFIRMED.**
   Zero failures anywhere in the run, so
   test_v3_66_729_body_contract_fixtures passed. Same disposition.
3. **Item 19 works on the box, not merely present in the tree.**
   `01_sysinfo.log` opens with `commit : f7367487...` / `branch : main`. 15.59
   recorded item 19 as closed after finding `emit_commit_identity` in source;
   the register had said ABSENT on a reading taken against an output BUNDLE.
   The box now demonstrates the behaviour, which is a stronger fact than the
   source reading that closed it.

**AND v3.66.940 IS INERT ON THE BOX.** `cat ~/BulkDownloader/.env` returns "No
such file or directory" -- there is no `.env` there at all, so the seed
allow-list skips nothing, warns about nothing, and changes no behaviour. That
was the single riskiest thing in the sweep (it governs what reaches the running
service's environment at import) and it is a no-op in the only deployment that
matters. Recorded because "the risky change turned out inert" is exactly the
kind of fact that gets assumed rather than checked next time.

TWO OBSERVATIONS, NEITHER A FAILURE

- **The live lane's tail looks alarming and is not.** `06_live_tests.log` ends
  with a playwright `TargetClosedError` and "Future exception was never
  retrieved", while the lane itself reports 36/0/0. Teardown noise after a
  green run. Written down so the next reader does not chase it.
- **Selftest warn: `extractor_freshness` -- "yt-dlp is 35 days old, consider
  updating".** Operational, non-gating, and the operator's call.

ITEM 11 IS CONFIRMED REAL, AND THE ATTRIBUTION IS AN ORDERING INTERACTION

Measured with the instrument CLAUDE.md section 0 prescribes -- a pytest plugin
wrapping `sqlite3.connect` and recording any path that resolves inside the
checkout. The wrapper was proven in BOTH directions first: a deliberate
relative connect from the repo root is RECORDED, while `/tmp/elsewhere.db` and
`:memory:` are not. A clean result from an unproven instrument is worth
nothing.

  * A 156-suite band over the DB-consumer population (derived by grepping
    tests for `db_conn|downloader_history|library_record|db_log`) produced
    **one** hit: `/home/user/BD/downloader_history.db`, cwd `/home/user/BD` at
    connect time. The file was on disk afterwards, 4096 bytes, gitignored at
    `.gitignore:20` so `git status` stayed clean. 1902 tests ran.
  * **The named test does NOT reproduce it alone.** Running
    `tests/test_v3_50_phase3.py` by itself: 44 passed, ZERO hits. So the leak
    needs a prior test to have run, which is why 15.51 could only reproduce it
    with a multi-file band and never with one file.

WHY THE FIRST ATTRIBUTION WAS UNSAFE, and it is the reusable part. The plugin
recorded the nodeid whose *protocol was active*, which is not the same claim as
"this test's body did it": a connect during fixture TEARDOWN happens after
`isolated_bd_home` restores cwd to the repo root, and a connect on a background
thread has no nodeid at all. The mechanism matters here --
`isolated_bd_home` IS autouse and DOES `os.chdir(tmp_path)`, so a test body
cannot easily produce cwd=repo; teardown and background threads can. Naming a
test on that evidence would have been a confident wrong answer of exactly the
kind this session kept producing. The plugin now captures
`traceback.format_stack()` and the thread name, and the band is being re-run
with it.

**ROOT-CAUSED. It is not a test at all -- it is the integrity-check thread,
and the defect is a promise a relative path cannot keep.**

The stack capture named it outright. Thread `bd-db-integrity`, not a test body:

    db.py:2079  _do_check
    db.py:2113  _row_count_estimate  ->  db_conn(path)
    db.py:558   sqlite3.connect(path or _resolve_db_path(), timeout=10.0)

`db.py:2062` does `_scheduled_path = _resolve_db_path()`, directly under a
comment that states the intent: *"a check scheduled for database A verifies A
even if the process later points DB_PATH elsewhere."* The intent is right and
the capture-at-schedule-time design is right. **But `_resolve_db_path()`
returns a bare RELATIVE path when `BD_INSTALL_DIR` is unset** -- measured:
`'downloader_history.db'`, `isabs == False`, and its own docstring says so
("use DB_PATH as-is, which sqlite3.connect() resolves against cwd"). A relative
string captured across a thread boundary captures nothing: it is re-resolved
against whatever cwd exists WHEN THE THREAD RUNS.

So the sequence is:

  1. A test runs; autouse `isolated_bd_home` has chdir'd to its tmp_path.
  2. An integrity check is scheduled; `_scheduled_path` = the relative name.
  3. The test ends; the fixture's `finally` restores cwd to the checkout.
  4. The background thread wakes and connects -- resolving that relative name
     against the REPO ROOT, and creating `downloader_history.db` there.

**THIS IS THE SHAPE OF THE WHOLE SESSION, in product code.** A fix written
specifically to survive a boundary captures a value that cannot survive it, and
the comment above it states the guarantee confidently enough that no reader
re-derives it. Compare @937's `emit_band` (a union whose root made it dead) and
@941's `_scan_worker` (writes bound to a global rather than to their own
state).

It also explains the operator-visible incident 15.51 recorded without a
mechanism: two test rows reaching the operator's PRODUCTION history during the
v3.66.926 capture (history 116 -> 118, provenance 101 -> 103). Same thread,
same relative path, resolved against the capture's cwd.

**THE ATTRIBUTION IS NON-DETERMINISTIC, WHICH IS THE PROOF.** Two runs of the
identical band named two DIFFERENT tests -- `test_api_library_stats_endpoint`
and `test_api_library_tag_add_requires_name` -- because the nodeid is only
whichever test's protocol was active when the thread happened to wake. Either
name, reported as "the leaking test", would have been a confident wrong answer.

**THE FIX IS ONE LINE**, and it is deliberately NOT taken here: make the
capture absolute at schedule time (`_os.path.abspath(_resolve_db_path())`), so
the value actually carries the guarantee the comment claims. It wants a RED
test that drives the thread with a cwd change between schedule and run, which
is fiddly enough to deserve its own cut rather than a tail-end edit.

WHAT IS ESTABLISHED vs NOT. **Established:** real, root-caused to
`db.py:2062`, reproducible on a 156-suite band, mechanism confirmed by stack
capture and by measuring `_resolve_db_path()` directly. **Not established:**
whether other populations leak (the denominator was 156 of ~1260 test files),
and whether any OTHER consumer of `_resolve_db_path()` captures it across a
boundary the same way. Both are worth asking in the cut that fixes this.

### 15.60 | The operator-queued sweep 2026-08-07, close at d670271 -- three cuts, and item 27 was already done

The operator queued D, B, A and 27 interactively and ratified dispositions for
items 3 and 27. Three cuts shipped. The fourth item needed no work, and the way
that was discovered is the finding.

SHIPPED

| cut | subject | band | mutation |
| --- | --- | --- | --- |
| v3.66.939 | the CI gate lane is sharded, and a shard can silently lose a file | 1641 passed | 7 caught / 0 escaped |
| v3.66.940 | the .env loader applied every key it found, not the declared set | 570 passed | 6 caught / 0 escaped |
| v3.66.941 | a cancelled scan's worker wrote its counters into the NEXT scan | 1494 passed | 5 caught / 0 escaped |

**ITEM 27 WAS ALREADY CLOSED, AND I ASKED THE OPERATOR TO RATIFY A SUPERSEDED
RECOMMENDATION.** 15.51 records "ITEM 27 -- qB/JD library rows: **N ROWS, ONE
PER FILE INSIDE THE DIRECTORY**" with a paragraph of implementor guidance. The
tree ships something else and has for some time: `library_path_for_completion`
(`library.py:88`), documented in its own docstring as "15.11 (option b)",
resolves a backend-reported completion NAME to the absolute path of ONE file --
the largest `_VIDEO_EXTS` file inside a directory, ties broken
lexicographically so the answer cannot depend on `os.walk` order. It is wired
at both qB/JD done-sites (`runner_integrations.py:575`, `:827`) and its suite
(`test_qb_jd_completions_record_the_largest_media_file.py`, 11 tests) passes.

So option b won, option a's write-up stayed in the register, and I read the
register instead of the tree before putting the question to the operator. That
is CLAUDE.md section 1's rule -- re-derive before working a queued item -- and
I broke it in the act of ASKING about the item, which is worse than breaking it
while working one: a wrong answer here spends the operator's judgement on a
decision that was already made. Re-derive before you ASK, not just before you
CUT.

Note what a careful reading of the options would NOT have caught: the operator
chose "record once at completion, accept staleness", and that IS what option b
does. The answer was right; the question was void.

WHAT THE CONSUMER RE-DERIVATION FOUND ANYWAY, and it is worth keeping because
it is the reason option a would have been expensive. `library_record` backfills
`history.library_id` (`library.py:240-245`), and that column is a single
INTEGER added by `migrations.py:455-460` explicitly "so we can navigate from a
history row to its library row". It is one-to-one BY SCHEMA. Writing N rows for
one `history_id` would have left the history row pointing at whichever member
was recorded last -- an arbitrary one of N -- unless the cut also chose a
representative and migrated the link. Option b picks the representative
up front instead, which is the same problem solved before it is created.

THE OTHER THREE

- **D** -- the operator chose the 3-way matrix in a separate job. Boundaries
  drawn from measured time: `test_toolchain_534` is 72.5s of 179.8s, 40% of the
  lane alone, so no two-way split could meet the 60s budget while it stayed
  whole. FIRST CI RUN CONFIRMED IT WORKS: gates 60s, toolchain 73s,
  parity-graph 73s, artifacts-pins 44s, postgres 59s -- the gates check now
  completes in ~77s wall against 194s before. Those are JOB totals including
  setup; the per-STEP pytest times were not read, so "every shard's step is
  under 60s" is unverified.
- **B** -- `_envfile`. The writer was already allow-listed; the reader applied
  everything. `SEEDABLE_KEYS` is now bound to `EDITOR_KEY_NAMES` rather than
  restated.
- **A** -- `scan_start`. Both halves: the worker is bound to its ScanState (13
  global references gone) and `scan_start` refuses while the previous thread is
  alive.

METHOD -- FIVE HARNESS DEFECTS, ALL CAUGHT BY AN INSTRUMENT, NONE BY REVIEW

1. **A subprocess probe without `env=`** inherited the runner's environment and
   "failed" by reporting the runner's own `PATH` as a leak -- a convincing red
   for entirely the wrong reason. CLAUDE.md section 0 names this exact shape.
2. **An AST predicate that counted COMMAS.** Pristine source is
   `args=(roots,)`; the trailing comma made the check pass vacuously. It counts
   tuple ELEMENTS now.
3. **A fixture that reset module globals under a live worker**, so the defect
   under test surfaced as `AttributeError` on a daemon thread -- harness noise
   masking the real failures. It joins before resetting.
4. **`print()` in library code.** `test_v3_43_78_static_analysis_fixes` sweeps
   every `bulk_downloader/*.py` and forbids it. The BAND caught that; nothing
   in the cut would have.
5. **@935's own ratchet caught @941**, correctly: the new tests hand-rolled a
   scan poll loop three times. Those loops wait for a scan to be demonstrably
   RUNNING, a different question from the convergence both helpers answer, so
   the response was a third helper (`scan_wait.wait_for_progress`) rather than
   an exemption. **A ratchet that keeps catching the same legitimate need is
   naming a missing tool.**

Two mutation escapes were also closed, both the same shape as v3.66.938's: a
verdict's comparison could be severed from its own measurement with no test
noticing, because the only assertions lived inside the mutated test. Extract a
verdict's comparison into a named helper with a positive control ON SIGHT
rather than waiting for a battery to find it -- that is now three cuts running.

OPERATOR DISPOSITIONS RECORDED

- **Item 3** -- DEFER until after mirror retirement step 2. 245 tracked files
  reference the path and 109 of them are under `project-knowledge/`, which the
  retirement is about to delete; sweeping them first would be work done twice.
- **Item 27** -- ratified "record once at completion, accept staleness", which
  is what the shipped option b already does. No work.

STILL OPEN: 3 (behind mirror retirement), 11, 12(c), 14, 31, 32, 33; 17/21/25
not evaluable from a container. A box re-capture is due -- main has moved
v3.66.936 -> v3.66.941 since the last one.

### 15.59 | Tier-1/tier-2 sweep 2026-08-07, close at d670271 -- four of four "open" items were already closed, and the re-derivation found the real ones

The operator asked for tier 1 and tier 2 of a speed-sorted register list "in
however many cuts it needs". The headline is that the SORTING was worth more
than the list: re-deriving before working (CLAUDE.md section 1) closed six
items without a line of code, and the two cuts that shipped were both found
DURING that re-derivation rather than being on the list at all.

SHIPPED

| cut | subject | band | mutation |
| --- | --- | --- | --- |
| v3.66.937 (a25afe6) | bd-band-derive derived its contract floor in two places | 545 passed | 7 caught / 0 escaped |
| v3.66.938 (7db669c) | an atomic write leaves a sidecar; .gitignore covered only the destination | 3491 passed / 7 env | 8 caught / 0 escaped |

CLOSED BY MEASUREMENT, NO CODE (six items)

- **5** -- bd-parband attributing a verdict to a suite it never ran. CLOSED.
  Its docstring states exit 2 for "a suite path that does not exist" and "exit
  2 never writes the band-results file"; `_py()` makes a null interpreter FATAL
  rather than a silent fallback. The `.bd_last_band.json` half was already
  closed by item 6 (`.gitignore:51`) -- and this session RE-PROPOSED that rule
  from memory before checking, which is the reason item 6 exists.
- **7** -- bd-band grading a zero-collect helper FAIL, and the derive sweeping
  helpers into bands. BOTH halves CLOSED at @897: `bd-band` names NOTHING RAN
  as a distinct state, and `is_suite()` (bd-band-derive:758, applied at :572)
  filters to the runner's own `test_*.py` predicate. Measured: two `--file`
  runs returned zero non-`test_*` entries.
- **26** -- census coverage counting rows it never examined. CLOSED. The tool
  now computes `uncompared_rows` (:396), reports it (:436-438), and the sweep
  prints "rows examined : %d of %d" (:485) with a "complete -- every done row
  was compared" line (:521).
- **28** -- six extractor completion paths that cannot execute. CLOSED.
  `runner_extractors.py:33` is a single local `safe_dest(_P(dl_dir) / rendered)`
  taking a Path; the six bare-str assignments are gone. 15.51 already said this
  and the register still carried the item.
- **9** -- `bd-claim` inert from a shell. CLOSED at @872: durable owner keyed
  off `/proc` start time, a TTL, and `add` REFUSING a derived owner another
  live claim already holds.
- **19** -- `git rev-parse HEAD` into the capture sysinfo, plus a selftest
  stage. CLOSED: `capture.sh` has `emit_commit_identity` (:298, with the
  walk-up guard) and the `[7b/9] Live selftest battery` (:1080). **The register
  said "re-confirmed ABSENT against the 883 bundle" -- a reading taken against
  an OUTPUT ARTIFACT rather than against source.** A bundle is evidence about
  the capture that produced it, never about the tree now.
- **10** (tier 1) -- `ai_boot_readiness.json` has no in-flight marker. CLOSED
  at @874, and the way this session got it wrong is the lesson: a grep for
  `in_flight|inflight|running|started` found nothing, so it was filed as fast
  and open. The marker is called **`final`**. `write_status` stamps it,
  `_persist` makes it required at every call site, and `read_status` returns
  `unknown/no_finality_marker` for a document without it -- unknown as a third
  state, exactly as the item asked. **A predicate over the wrong vocabulary is
  a grep that reports absence over a denominator that never contained the
  subject.**

That is 4 of 4 tier-2 items closed, plus 3 more. CLAUDE.md section 1 says "~half
of a stale register's open items are already closed or mis-scoped". Here it was
all of them.

WHAT THE RE-DERIVATION FOUND INSTEAD

- **@937.** Re-deriving item 7 meant reading bd-band-derive, where `emit_band`
  unioned FLOOR against a hardcoded sandbox home. `derive()` already unions the
  same FLOOR against the real `work`, so the floor DID reach every band and the
  second union was dead. The defect is the duplicate denominator, not the dead
  literal: had derive()'s union been removed, emit_band's would not have covered
  it. Its live half is that derive() dropped an absent floor in SILENCE.
- **@938.** Item C was filed as "gitignore misses `.integrity_last_run.tmp`" --
  trivial, one line. It is FOUR, and two are credential files:
  `vapid_keys.json.tmp` (the web-push PRIVATE key) and `secrets.json.tmp` /
  `secrets_meta.json.tmp` (the vault). All four destinations ignored, all four
  sidecars not. The existing gitignore gate could not see it because its subject
  is the RULES; a path with no rule is outside that denominator.

METHOD -- TWO WRONG MEASUREMENTS THIS SESSION, BOTH CAUGHT BEFORE SHIPPING

1. **A probe run from outside its own directory reported the answer expected of
   it.** `bd-band-derive` does `sys.path.insert(0, dirname(realpath(__file__)))`
   to import `bdtools_sec`. A "pristine" copy placed in the scratchpad therefore
   CRASHED, and `2>/dev/null` hid the traceback, so an empty stdout read as "a
   band without the floor" -- confirming the hypothesis under test. Four
   separate readings were void, and a CHANGELOG entry stating the false claim
   was written before the contradiction surfaced. The tell was arithmetic: the
   copy emitted ZERO suites where the real tool emits 23. **A pristine baseline
   must run from the path the code resolves against, and never with stderr
   discarded.**
2. **A predicate over the wrong part of the syntax.** A first pass asked whether
   a function's text contained both `FLOOR` and `isfile`, and flagged
   `selftest()`, which does `ref -= set(FLOOR)` and stats the disk for unrelated
   reasons. Mentioning the floor is not deriving it. Replaced with a structural
   AST predicate: a comprehension iterating FLOOR whose `ifs` call a disk probe.
3. **A cross product is not a denominator.** An early scan for the @938 subject
   crossed every `.gitignore` rule with every sidecar suffix and returned 330
   "hits" including `venv.tmp` and `node_modules.tmp`. The real denominator is
   the paths the CODE WRITES; derived from source it is four, plus one false
   positive that is now a declared exception rather than a silent inclusion.
4. **A detector with no detector.** @938's discovery gate could have its verdict
   severed from its own measurement, and NO test noticed, because the only
   assertion about it lived inside the test being mutated. Caught by the
   mutation battery, not by review. Closed with a named helper and a positive
   control.

ENVIRONMENTAL, CONTAINER-ONLY -- A THIRD ONE

`tests/test_e2e_smoke.py` fails 7/7 here (`_RealE2ESmoke`, playwright
`wait_for_selector` timeouts). PROVEN pre-existing: the same 7 fail on a
pristine HEAD with **0 modified paths** in the same directory. Note the method
trap hit on the way -- the first attempt used `git stash push --keep-index`,
which for a fully-staged tree leaves the working tree UNCHANGED, so it measured
the cut against itself and "proved" nothing. **Not verified on the box**; this
is a container reading only, and it joins the GTK typelib case and the
`no_backend` live-recording case in CLAUDE.md section 5.

STILL OPEN

- **D** (CI gate lane budget) -- MEASURED and the picture changed. Per-file
  timings on this container: `test_toolchain_534` **72.5s of 179.8s** (40% of
  the lane on its own), `test_gui_parity` 30.6s, the other thirteen 76.7s
  combined. In CI's single-process run (140s total) that scales to roughly 56s
  and 24s. **So no two-way split can meet the 60s budget** -- the rule's own
  remedy does not reach its own target while `test_toolchain_534` stays whole,
  and profiling it shows 59s of its 68s in four subprocess-heavy tests that
  walk the 240-tool suite, which is not a cheap win and not safe to trim. A
  three-way matrix in a SEPARATE job (so gitleaks/regen/compileall stay
  single-run) does fit, at the cost of two extra runners per PR. Split shape vs.
  raising the number is an operator cost decision and is left to the operator.
- **A** (`scan_start` over a live cancelled worker), **B** (`_envfile` has no
  allow-list), **3** / **27** (blocked on a scope call), **11**, **12(c)**,
  **14**, **31**, **32**, **33** -- unchanged.
- **17**, **21**, **25** -- still not evaluable from a container.

### 15.58 | Session close 2026-08-07 at b24e675 (v3.66.936) -- the three box-capture failures, and three defects found underneath them

Closes the three unit failures in the operator's 2026-08-07 capture (48707ad,
v3.66.932). Three cuts, each RED-first with a counterfactual, guards 7 ok / 0
drifted throughout.

| cut | subject | band | mutation |
| --- | --- | --- | --- |
| v3.66.934 (#240) | the suite inherited the operator's live AI config | 631 passed | 7 caught / 0 escaped |
| v3.66.935 (#241) | a scan wait that gave up read as one that finished | 1326 passed | 10 caught / 0 escaped |
| v3.66.936 (#242) | the "synthetic only" golden embedded live state | 503 passed | 7 caught / 0 escaped |

NONE OF THE THREE WAS A PRODUCT BUG. All three were checks that could not see
their own subject -- section 0, three times, in the gates rather than in the
app. The one with real operator cost was @934: turning AI ON in the Global
Config UI broke the test suite, and the test that broke was not the one that
changed.

**A | OPEN, PRODUCT: `scan_start` accepts a new scan over a live cancelled
worker, and the old worker corrupts the new state.**

`library.scan_start` refuses only while `finished_at is None and not
cancelled`. `scan_cancel()` sets `cancelled = True` and returns; nothing stops
the thread until the next file boundary. So between the cancel and the thread
actually leaving, a NEW `scan_start` is ACCEPTED -- and the old worker's
`_mut`/`_bump` resolve `_scan_state` at call time, so its counter writes land
in the NEW ScanState.

MEASURED at v3.66.935: after `scan_cancel()`, `running` reads False while
`seen` climbed 70 -> 190 and went on to 4000; `finished_at` stayed None
throughout.

NOT REACHABLE FROM ANY TEST -- an AST census over the 1256 tracked `tests/*.py`
finds ZERO callers of `scan_cancel`. It IS reachable in production from the
library scan route. `tests/scan_wait.py:start_and_wait` refuses to start while
the previous worker is unfinished rather than racing it, so the test surface is
closed; the product surface is not. Fixing it means either having the worker
hold its own ScanState reference, or having `scan_start` refuse while the
thread is alive. Not started -- it is a runtime change and needs the operator.

**B | OPEN, INFRA: `_envfile` applies every KEY=VALUE it finds into
os.environ, at import, with no allow-list.**

`bulk_downloader/__init__.py` calls `_envfile.load_envfile()` in the module
body. It reads the first existing candidate of `$BD_ENVFILE`, else `cwd/.env`,
else `$HOME/BulkDownloader/.env`, and applies EVERY key by `setdefault` with no
filtering. On the box `$HOME/BulkDownloader` IS the install directory, and the
module's own docstring names that file as the GUI env-editor's persistence
target -- so it is operator-writable through the UI.

`tests/conftest.py`'s `isolated_bd_home` chdirs per test, which closes the
`cwd/.env` candidate and CANNOT close the HOME one. No fixture can chdir away
from $HOME.

MEASURED at v3.66.936, with cwd deliberately elsewhere so only the HOME
fallback was live:

    control (clean):                            OK
    env BD_REDACT_EMAILS=keep:                  DRIFT (emails, reduced_redaction)
    $HOME/BulkDownloader/.env, cwd elsewhere:   DRIFT (the same two)

@936 immunised the capture-model golden against the `BD_REDACT_*` slice by
projecting an allow-list. The general surface is untouched: any `BD_*` key an
operator writes there is applied process-wide at import, to tests and to the
service alike. Whoever owns `_envfile` should decide whether an allow-list
belongs there.

**C | OPEN, TRIVIAL: .gitignore misses the sidecar its own atomic write
creates.** `.gitignore` covers `.integrity_last_run`; `db.py`'s writer produces
`.integrity_last_run.tmp`, which is NOT covered, so a test run leaves an
untracked stray in the repo root. One line. Not folded into any of the three
cuts because it is a different feature.

**D | ENVIRONMENTAL, container-only: no live-recording backend here.**
`tests/test_v3_66_729_body_contract_fixtures.py::test_the_app_never_5xxs_on_a_
well_formed_request` fails in this container with `/api/live/watch  app 5xx'd
on OUR fixture -> no_backend`. It is NOT a regression: it reproduces on the
pristine base in the same directory, and all 10 tests in that file PASS on the
box in the v3.66.932 capture. `which streamlink ffmpeg yt-dlp` returns nothing
here. Same class as the GTK false-failure section 5 already records; added
there so the next session does not chase it.

**E | METHOD: two mutation escapes worth more than the fixes they closed.**
Both were found by `bd-mutate` and neither was visible by reading.

- @935: a test asserted `"never_run" in str(ei.value)`, and the TIMEOUT
  branch's message embeds `_describe(st)`, which prints `never_run = True` as
  one of its fields. BOTH failure paths satisfied it, so deleting the branch it
  was written for left it green. Closed by matching the sentence only that
  branch emits, asserting the other path's sentence is ABSENT, and pinning the
  behaviour that actually differs (it must fail at once, not burn the budget).
- @936: there are TWO `dom_log_len` fields -- one `_proj_workflow` computes,
  one `_capture_health` derives -- and both are `2` on the fixture. A mutant
  aimed at the top-level one LOOKED correctly aimed while every assertion in
  the file read the health one, so the projection's own derived fields had no
  test at all. **When two fields share a name and a value, a mutant and a test
  can disagree about which one they mean and nothing shows it.**

The generalisation for both: an assertion that matches a SUBSTRING of a
diagnostic dump is not an assertion about the branch that produced it.

**F | The register cut that was already done.** 15.57 and the CLAUDE.md
section 7 correction landed inside @934 (`25131a4`), not as a separate
register-only cut. A later reading of this session mistakenly reported them as
unwritten; they are in the file. Re-derive before citing, including from a
session summary.

**Still open from 15.56, unchanged:** the mirror retirement step 2 (specified,
not executed) and item 3's `/home/claude` scope decision.

### 15.57 | Two stale facts found while cutting v3.66.934, both fixed at source; one open operator decision

Found 2026-08-07 at v3.66.934 while working the three box-capture failures.
Recorded because the OPERATOR asked that stale information be fixed rather
than noted, and because one of the two is a decision that is not mine.

**A | CLAUDE.md said CI runs no tests. It has run 161 since v3.66.849.**

The section 7 bullet read: "The `gates` job runs gitleaks, the
generated-artifacts sync check, `compileall`, advisory `pyflakes` and the
CHANGELOG ASCII check -- **no pytest at all**. The only job that runs tests is
`postgres-integration`, whose list is four mod3 files. So CI's entire test
denominator is four files."

That was CORRECT when written at v3.66.847. `.github/workflows/ci.yml` gained
a 15-file repo-wide pytest lane at v3.66.849 and nothing updated the bullet, so
for 85 releases the contract told every agent that a red `gates` job could not
be about their tests.

MEASURED 2026-08-07, by running the lane rather than reading it:

    161 passed in 130.25s      (this container)
    1 failed, 160 passed in 140.07s   (CI, on 90c5d9b)

The CI failure was `test_import_graph_no_new_edges` -- a real and correct catch
of a baseline re-freeze that had not been committed. So the bullet was actively
misleading at the exact moment it mattered: it would have told the reader that
a red gates job was not about their cut, when it was.

FIXED in CLAUDE.md section 7, with the 15 file names enumerated and the
measurement dated. The bullet now leads with "do not read this paragraph
instead of ci.yml", and carries its own staleness as the worked example of
section 1's rule. What survives unchanged is the part that was always the
point: CI's denominator is file-INDEPENDENT, so a green tick still says nothing
about the changed module's own suites.

**B | The CI lane has doubled and breached its own stated budget. OPEN --
operator decision.**

`.github/workflows/ci.yml`'s comment on that step sets a rule:

    Measured 2026-08-03: 81 tests, 52s. Keep it under a minute; if it grows
    past that, split rather than silently dropping files, because a truncated
    list here reads as coverage it does not have.

Re-measured at v3.66.934: **161 tests, 140s in CI, 130s in a container.**
Double the tests and more than double the budget.

The rule's own remedy is to SPLIT the job, not to raise the number -- raising
it silently is how a budget stops being a budget. I did neither: changing the
shape of a CI job is a build change and needs the operator (CLAUDE.md section
9). The comment in `ci.yml` now records the breach, the re-measurement, and
that the decision is deferred here, so the next reader does not find an
unexplained 140s against a 60s rule and quietly edit the number.

DO NOT resolve this by trimming the file list. Every entry is a gate that was
RED somewhere nothing else could see -- the @850 note in `ci.yml` names two
that sat red on `main` for three releases.

THE DECISION: split into two jobs (parallel, each under the minute) vs. raise
the budget to ~3 minutes and say why. Not started.

**C | Process finding, not a defect: staging is publication in this
environment.**

The stop hook commits and pushes whatever is STAGED. During this cut it fired
between `git add` and the import-graph re-freeze, so `90c5d9b` went out and
opened PR #240 carrying a tree my own band had already proven red. Nothing was
lost and the follow-up commit `ad2f2d3` closed it, but the ordering rule is now
sharper than CLAUDE.md section 2a states: **re-freeze and regen BEFORE staging,
not merely before the band.** Section 2a's existing advice -- `git add` before
the final band run so the `git ls-files` gates can see a new test file -- still
holds, and the two together mean: regen, re-freeze, THEN stage, THEN band.

### 15.56 | Session 2026-08-07: six cuts (928-933), and the mirror retirement that is half done

STATE AT CLOSE. main at 4f141f6 = v3.66.933, dirty 0, behind 0, one branch.
Guards 7 ok, bd-freshcheck exit 0, regen in sync. v3.66.928 was verified on the
box: capture PASS, 14894 total / 14809 passed / 0 failed / 85 skipped, live
36/0/0, /api/health reporting sha be66cba4163a and db_ok true.

WHAT SHIPPED. Every cut RED-first, 0 mutants escaped, band green.

  928  auto_recover_sqlite quarantined HEALTHY databases. OperationalError is
       a SUBCLASS of DatabaseError, so `database is locked` read as confirmed
       corruption. REPRODUCED: a 500-row db with integrity_check=ok, held by
       one BEGIN EXCLUSIVE, was renamed aside. Also the one-second quarantine
       name collision -- reproduced, first file destroyed with no trace.
       15.55 has the detail. THE REGISTER'S HIGHEST-VALUE ITEM, closed.
  929  bd-doc-truth could not see CLAUDE.md. Corpus 65 -> 78 documents.
  930  the nightly bit-rot scan had no download roots, so it decided nothing.
  931  bitrot schema init could not tell "already there" from "could not be
       done". Register called it a bare except; it was `except Exception`.
  932  .githooks/pre-push enforcing section 7's two-dot diff (item 30).
  933  mirror retirement step 1 of 2 -- the five files carrying baselined
       secrets, isolated deliberately.

FINDINGS WORTH MORE THAN THE CUTS.

  * GITLEAKS DOES NOT FLAG A SECRET ON A REMOVED LINE. Measured on PR #238,
    which deleted five gitleaks-baselined files (2062 lines) and passed the
    gates job -- gitleaks-action@v2 confirmed to have run, named in its own
    Node-deprecation warning. Section 7 warns a leak in branch history cannot
    be fixed forward, which is why this was isolated rather than assumed. It
    de-risks any future bulk deletion.
  * THE SQLITE RESULT CODE DOES NOT ALWAYS DISCRIMINATE. It cleanly separated
    corruption from contention in 928 (SQLITE_CORRUPT 11 / SQLITE_NOTADB 26 vs
    SQLITE_BUSY 5), and CANNOT in 931: `duplicate column name` and `no such
    table` are BOTH SQLITE_ERROR (1). Reusing 928's pattern on the assumption
    it generalised would have shipped a fix that was wrong on the exact case
    it existed to catch. Measure per site.
  * TWO FIXES REPRODUCED THE SHAPE OF THEIR OWN DEFECT, both caught by their
    own tests, neither by review. 930's first multi-root flat join returned
    the FIRST matching root -- the first-match-wins guess _resolve_recorded
    exists to refuse. 928's `cx.close()` in a finally deleted the -wal before
    it could be quarantined (harmless as it turns out: a clean close
    CHECKPOINTS the WAL into the db, so data is merged not lost -- but the
    test's premise was wrong, not the code).
  * A NEW-FILE CUT HAS NO MEANINGFUL RED PHASE. 932's nine tests all failed
    because the file did not exist. The mutants were the only real evidence,
    and one ESCAPED: the deletion test used a branch whose content matched
    main, so the diff was empty either way.

THE MIRROR RETIREMENT IS HALF DONE, AND STEP 2 IS FULLY SPECIFIED.

Operator confirmed 2026-08-07 that project-knowledge/ is NOT used as an
uploadable self-contained bundle -- the only reason a second copy of the
executable toolchain existed. 239 mirrors, ~2.1 MiB, never generated, kept in
sync by a test. The cost is on record: at v3.66.818
`project-knowledge/bd-guardcheck` reported "0 ok, 0 drifted, 0 missing" and
EXITED 0 while the real one reported 7 ok -- a cut had repaired one copy of a
two-copy tool and the tree reported success. This session paid it again at 929.

Step 2 was written, BANDED, AND REVERTED. The band found the real blast radius:
the mirror concept is wired into FIVE sites, not one, and one of them is
bd-band-derive itself. Reverted rather than half-finish a change to the tool
every future cut uses to compute its band. CI would NOT have caught it -- the
gates job runs no pytest over these files.

  1. tests/test_versync_gate.py:12,170 -- PK_MIRROR + test_pk_mirror_matches
     _toolchain_copy, a SECOND sha256 mirror gate, for bd-versync only.
  2. toolchain/bin/bd-band-derive:164,167,502 -- _PK_MIRROR_GATE,
     pk_mirror_coupled(), SIGNAL 8. Remove the signal with the gate.
  3. tests/test_toolchain_534.py::test_band_derive_reaches_the_pk_mirror_gate
     -- pins that signal; retires with it.
  4. tests/test_v3_66_918_tracked_source_denominator.py:74 -- `> 300` must
     become `> 200`; MEASURED 241 after the mirrors go.
  5. tests/test_capture_execution_lanes.py -- lane manifest names the retired
     file.
  Plus: delete the 234 mirrors (all VERIFIED byte-identical to their origin
  before deletion), add tests/test_pk_mirrors_stay_retired.py, and correct
  CLAUDE.md section 1 again to 231 / 2365.

ITEM 3 (/home/claude) IS STILL UNDECIDED, and the register's framing is wrong.
"~227 non-test references" counts FILES. MEASURED at 4f141f6: 977 occurrences
across 226 files, of which ZERO are in bulk_downloader/ -- no application code
touches it at all. 60 of 60 toolchain files carrying the string are
byte-identical mirrors, so those occurrences are the same text counted twice
and will vanish with step 2. About 53 occurrences are COMMENTS recording the
removal of these very paths ("@876: was /home/claude/work, a zip-era path
absent from any git"); rewriting those destroys the explanation that stops
someone re-adding them. Roughly 20 are live assignment defaults, and
/home/claude is the LIVE agent home which must not be deleted -- this is a
substitution question, not a removal one.

RECOMMENDATION, not a decision: centralise the ~20 live defaults through one
constant in bdtools_sec.py, which already owns require_bundle's default. No
behaviour change, no env surface, 977 sites become one point of change, and
the comments and history stay intact. Then "should the bundle root be
configurable" is a one-line edit whenever it is wanted, instead of a 977-site
sweep. Re-measure before acting; step 2 moves the numbers.

### 15.55 | Item 1 -- auto_recover_sqlite quarantined HEALTHY databases (v3.66.928)

15.54's item 1, the register's highest-value entry and the defect that actually
destroyed the operator's history on 2026-08-07. RE-DERIVED before working it,
per 15.51's finding that 15 of 23 items were already closed: this one was NOT.
The pristine code at bb41b5b still caught the parent exception class.

MEASURED, NOT READ. The hierarchy claim was checked by reproducing the loss:
a 500-row database with `integrity_check = ok`, with one competing EXCLUSIVE
transaction held on it, was renamed aside and the caller told a fresh schema
would be created. `sqlite3.OperationalError` is a subclass of
`sqlite3.DatabaseError`, so `database is locked` and `disk I/O error` -- what
parallel load produces -- were indistinguishable from `file is not a database`.

THE DISCRIMINATOR IS STRUCTURAL, and it was derived rather than assumed. A
probe over five failure modes read SQLite's own result codes: SQLITE_NOTADB
(26) and SQLITE_CORRUPT (11) for genuine damage, SQLITE_BUSY (5) for
contention. Masked to the low 8 bits so extended codes -- SQLITE_CORRUPT_VTAB
is 267, not 11 -- are recognised. A message-text fallback covers an
interpreter that does not populate `sqlite_errorcode`, because WITHOUT one an
unavailable code makes every verdict UNKNOWN, which passes every contention
test by turning the tool off. That is section 0's inverse defect and it was
nearly shipped.

UNKNOWN IS A THIRD STATE AND IT DOES NOT QUARANTINE. A database that could not
be read is reported WARN naming the reason and left exactly where it is. This
fails safe: a genuinely corrupt file that survives one startup fails loudly at
the next, which is strictly better than silently replacing good data.

THE COLLISION WAS ALSO REPRODUCED, not argued. Two quarantines in the same
wall-clock second: the first file was destroyed and only the second survived,
with the first's `-wal`/`-shm` left orphaned beside it -- exactly the
incomplete companion sets 15.49 observed in the operator's listing, so that
observation now has a demonstrated cause rather than a suspected one. The name
is now claimed with `O_CREAT|O_EXCL` before the move.

A DEFECT THE FIX INTRODUCED, caught by its own test rather than by reading.
Adding `cx.close()` in a `finally` -- ordinary hygiene, replacing a leaked
connection -- made SQLite delete the `-wal`/`-shm` before they could be
quarantined. Measured what that costs: a clean close CHECKPOINTS the WAL into
the database, so the data is merged into the file being quarantined rather
than lost, and SQLite only removes companions whose contents it just merged.
The code was right; the TEST's premise was not -- a hand-written `-wal` beside
garbage bytes measures SQLite's lifecycle, not the mover. Split into two
honest properties: a stubbed-connect test that SQLite never touches, proving
the mover carries companions under the unique basename; and a realistic test
that no companion is left orphaned at the old path, which is the actual hazard
(a stale `-wal` beside the fresh database `db_init()` is about to create).

EVIDENCE. RED proven on pristine source: 6 failing, 7 passing -- and the 7 are
the point, because a fix that merely stopped quarantining satisfies every
failing test and destroys the tool. 18 cases green after the fix. `bd-mutate`:
7 mutants, 7 caught, 0 escaped, including both predicate inversions, the
extended-code mask, and the O_EXCL removal. Band of 50 files: 559 passed, 0
failed, 273s -- long enough that the default two-minute command timeout would
have reaped it, per CLAUDE.md section 5.

STILL OPEN, and deliberately not folded in. 15.50-B: importing app writes
`app_config.json`, `logs/`, `live_recordings/` and `state/` into the cwd. The
`-wal`/`-shm` companions still move in a step separate from the database; the
collision that made that observable is fixed, but the move is not atomic as a
set, and a crash between the two steps still splits one.

NOT VERIFIED ON THE BOX. Container-green only. `./capture.sh` on test4 is the
gate and only the operator runs it.

### 15.54 | Session close 2026-08-07 at 5acd7c7 (v3.66.927) -- SUPERSEDES 15.48's open set

THIRTEEN CUTS, 915-927. The box capture at v3.66.927 is PASS: 14873 total /
14788 passed / 0 failed / 0 errors / 85 skipped, live 36/0/0, graph pin
matched, /api/health reporting sha 28cc9de and db_ok true.

READ FIRST, BEFORE ANY WORK. A fresh cloud session almost certainly starts from
a filesystem SNAPSHOT at an older commit, and the platform's lifecycle means the
session is never on `main` by the time the SessionStart hook runs -- so the
hook's auto-repair is structurally unreachable and you will get a
`*** STALE BASE ***` block instead. REBASE BEFORE DOING ANYTHING. Branching
from a snapshot-era commit and opening a PR makes GitHub diff it against
current main, and every commit merged since the snapshot appears as a REMOVAL:
the PR silently reverts the work below. CLAUDE.md section 5 carries the full
reading. Do not let the block's familiarity train you past it.

    git fetch origin main && git status && git log --oneline -1
    # HEAD should be 5acd7c7 or later. If it is behind, rebase.

WHAT SHIPPED, and the through-line is that every real finding came from an
INSTRUMENT and every wrong one came from reading:

  915/916  audit()'s two caps in one dict; regen_nfos_from_history resolving a
           bare basename CWD-relative.
  917-923  item 16 retirement, wired-gate invariants, and the capture
           regression: lane assignment had gone fail-closed at 1ae076a with 173
           files reviewed and nothing ever backfilled it, so 86% of the suite
           drifted serial. Capture went ~45min -> 4m06s.
  925      bitrot.verify_one resolved a bare basename against the CWD and WROTE
           a false integrity_issues row for a PRESENT file. Measured 3 -> 6 -> 9
           rows over three scans; nightly, and alerts_engine alarms on exactly
           that growth.
  926      Importing bulk_downloader.app booted the database at MODULE SCOPE in
           FOUR sites. Reading found one; tracing sqlite3.connect found the
           rest. This is what raced 64 xdist workers over the operator's live
           history on 2026-08-07.
  927      run_integrity_check scheduled a fire-and-forget thread that
           re-resolved DB_PATH at fire time, so it verified whichever database
           DB_PATH named when it woke -- and created it on contact. Four frames
           needed the path threaded, not one.

OPERATOR DATA WAS RECOVERED AND IS INTACT. 116 -> 118 history / 101 -> 103
provenance after the capture (the +2 are test rows; see 15.50 item B's
neighbour, not corruption). Originals remain in ~/db-rescue-20260807T012728Z/.
Contiguous filename numbering across the corruption gap (2_43 -> 2_44) is the
independent evidence the merge lost nothing.

THE OPEN SET, superseding 15.48. Re-derive each before working it -- 15.51
measured 15 of 23 register items ALREADY CLOSED, so assume the same here:

  1. **auto_recover_sqlite quarantines HEALTHY databases** (15.50 item A). THE
     HIGHEST-VALUE ITEM IN THE REGISTER. selftest.py:522 catches
     sqlite3.DatabaseError and OperationalError is a SUBCLASS, so transient
     contention reads as confirmed corruption. The recovered file was 3.7 MiB
     with integrity=ok when it was renamed aside. The quarantine is also racy:
     int(time.time()) at one-second resolution while Path.rename silently
     overwrites, with -wal/-shm moved non-atomically. Item 11 supplied the
     contention; THIS is what turned it into destruction.
  2. **bd-doctruth does not scan CLAUDE.md** (65 docs, project-knowledge only).
     Why the contract's own section 5 stayed wrong for weeks.
  3. **Registers 31 and 32 need re-deriving** -- TASK_TRACKER's 11 rows and
     CODEX_HANDOFF's 23 groups, both untouched by 15.51.
  4. **The nightly bitrot scan is inert** -- bg_scheduler.py:252 calls
     run_scan() with no download_dir, so it reports unknown=N and decides
     nothing. Deliberate at @925, still owed.
  5. **Item 30** -- no .githooks/pre-push enforcing section 7's two-dot diff.
  6. **Item 14** -- start_manual_login returns while the login thread is alive,
     so the Phase B takeover cannot open. NOT a solo cut: it changes threading
     in the login path.
  7. **Item 27** -- DECIDED (N rows per file, 15.52), not implemented.
  8. **Item 3** -- DECIDED (sweep the ~227 non-test references only, 15.52),
     not implemented.
  9. **15.50-B** -- importing app still writes app_config.json, logs/,
     live_recordings/ and state/ into the cwd.
  10. **bitrot.py:72-74** -- bare except on an ALTER TABLE.
  11. Operator- or box-bound: items 1, 21, 25, 29. Item 1 now HAS a measurement
      (240 tools, exactly 2 unreferenced) proving the twelve are not derivable
      from the tree; close it won't-fix or answer it from your records.
  12. Unevaluable from a container: item 17 (needs a bd-restart-check exit 1).

The ranked overnight plan is in 15.53. Nothing in this session is blocked on
anything in it.

### 15.53 | Persistence audit at 803a39a -- what was NOT written down, and the panel verified current

Run because "is it all saved?" is exactly the question that gets answered from
memory. Everything below was checked against the tree, not recalled.

STATE. main at 803a39a = v3.66.927: dirty 0, unpushed 0, behind 0, stashes 0,
one branch. 15.47 through 15.52 all present on origin/main. Guards 7 ok,
bd-freshcheck exit 0, bootstrap gates 29 passed / 1 skipped.

THE CLOUD PANEL IS CURRENT -- VERIFIED, NOT ASSUMED, AND THE OBVIOUS WORRY IS
WRONG. `scripts/cloud-bootstrap.sh` line 2 carries
`[cache-rebuild: 2026-08-05 v3.66.881]` against a v3.66.927 tree, which looks
stale. It is not:

  * NOTHING READS THE MARKER. It occurs exactly once in the repo, in the file
    itself. Its only function is to change the pasted TEXT so the panel hashes
    differently and rebuilds -- a manual lever. It therefore records WHEN A
    REBUILD WAS LAST FORCED, not the current version, and lagging is correct.
  * The bootstrap last changed at PR #185 (the commit that set the marker).
    `cloud-setup.sh` changed later, at v3.66.903 -- but the bootstrap EXECS the
    provisioner from the repo, so provisioner changes reach the next session
    with nothing to re-paste. **No re-paste is owed.**
  * Do not "fix" the marker by bumping it to the current version. That forces a
    cache rebuild for no reason and destroys the one piece of information it
    carries.

THE ENV BOX IS A CURATED SUBSET, and CLAUDE.md now says so. cloud-setup.sh
reads NINE BD_ variables; the box names four of them. The five unnamed are
optional skips with working defaults (BD_SKIP_AUDIT, BD_SKIP_CLOAK,
BD_SKIP_EXTRAS, BD_SKIP_NET, BD_SKIP_SECTOOLS). Conversely BD_DISABLE_KEEPALIVE
is IN the box and the provisioner never reads it -- its consumers are the app
and the test suite. Neither direction is a defect; both read like one until
checked.

FOUR THINGS THIS SESSION DERIVED AND NEVER RECORDED, now fixed:

  * **Item 1 (7b, name the twelve retired tools) HAS A MEASUREMENT.** 240 bd-*
    tools in toolchain/bin; exactly TWO (`bd-audit-gate.py`, `bd-triage.py`)
    are never referenced anywhere outside toolchain/bin. So the twelve are NOT
    derivable from the tracked tree -- the register's "unrecoverable" is
    CONFIRMED with a number rather than asserted. The disposition follows: it
    needs the operator's records or it should be closed won't-fix. Nothing
    depends on it; the cost of leaving twelve unnamed tools in place is that
    they sit there, which is already the status quo.
  * **`bitrot.py:72-74` wraps an `ALTER TABLE` in a bare `except Exception:
    pass`,** so it cannot distinguish "column already exists" (fine) from "the
    provenance table does not exist yet" (not fine) and `last_verified_ts` can
    silently never be added. Hit directly while writing @925's fixture and
    worked around rather than fixed. OPEN, small.
  * **The nightly bitrot scan is inert and it is only in the CHANGELOG.** @926
    left `bg_scheduler.py:252` calling `run_scan()` with no download_dir, so it
    reports `unknown=N` and decides nothing. That was a deliberate, stated
    deferral -- but a deferral recorded only in a per-cut changelog entry is not
    in anybody's open list. OPEN.
  * **`bd-doctruth` does not scan CLAUDE.md** (65 documents, project-knowledge
    only). Recorded in CLAUDE.md section 0 as the reason its own section 5 went
    stale for weeks. This is the highest-value doc-tooling item available.

CLAUDE.md's OWN NUMBERS WERE RE-DERIVED, and this is the half the audit nearly
got wrong. Section 1's worked example -- the one that teaches "the instrument
fixes the denominator; the predicate fixes the subject" -- carried a predicate
error of exactly that kind:

  | claim | measured at 803a39a |
  | --- | --- |
  | 2108 files end in .py | 2136 |
  | 469 extensionless bd-* scripts | 456 (231 toolchain, 225 project-knowledge) |
  | "...EXECUTABLE, python-shebang..." | only 232 of 456 are mode 100755 |
  | 234 / 235 per directory | 231 / 225 |
  | section 8: ~249 bd-* tools | 240 |
  | "seventeen shell scripts embed py heredocs" | 3, under two predicates |

  THE WORD "EXECUTABLE" WAS THE DEFECT. 224 of the 456 -- almost all of
  project-knowledge/ -- are tracked 100644, not 100755. An auditor who filtered
  on the exec bit BECAUSE THE PROSE SAID TO measured ONE file under
  project-knowledge and concluded the paragraph had rotted by 200x. It had not.
  The prose over-specified its own subject.

  THREE SUCCESSIVE READINGS OF THAT ONE BULLET WERE WRONG IN A SINGLE AUDIT --
  a predicate ignoring `bd-*`; one requiring mode 100755; then reading the
  survivor count as deletion drift when git showed only TEN files ever removed
  (seven at @858, three at @917). Each was stated confidently before the next
  measurement overturned it. That is section 1 failing inside section 1's own
  example, and it is the argument for the arithmetic check: 2136 + 456 = 2592
  reconciles, and the "1 under project-knowledge" reading never did.

  The heredoc count is left UNKNOWN rather than corrected. The class is real --
  a heredoc is invisible to an AST walk over files -- but 17 did not reproduce
  under two predicates, and replacing it with a number nobody has stood behind
  would just restart the cycle.

THE OVERNIGHT LIST, so it survives this conversation. Ranked, each
independently mergeable, all container-verifiable:

  1. auto_recover_sqlite quarantines HEALTHY databases (15.50 item A) -- the
     defect that actually destroyed the operator's history. Fails loud if
     wrong, unlike today.
  2. Widen bd-doctruth to CLAUDE.md, then re-derive its checkable claims.
  3. Re-derive registers 31 and 32 (TASK_TRACKER 11 rows, CODEX_HANDOFF 23
     groups) -- expect a similar rate to 15.51's 15-of-23.
  4. Wire run_scan's download_dir (above).
  5. Item 30 pre-push hook; 15.50-B cwd writes; the bitrot bare except.
  6. Slow detection jobs: attribute the +2 rows capture wrote to production,
     mutation batteries over this session's modules, a full-suite run.

  DRAFT-ONLY (operator approves the call): item 3's sweep -- 20 of the 246
  references live in CHANGELOG.md as HISTORICAL entries and rewriting those
  falsifies the record, so the rule is "live references only"; item 27's N-rows
  implementation; the 7 pre-existing e2e_smoke failures.

  NOT ALONE: item 14 (threading in the login path), items 1/21/25/29 (operator
  or box), item 17 (needs a restart that cannot be manufactured).

METHOD, recorded because it recurred: three of this audit's "*** UNRECORDED
***" verdicts were WRONG, and all three for the same reason -- grep across a
wrapped document misses a phrase the line break split. `grep 'Four frames'`
finds nothing in a file containing "Four
frames". Generalised into CLAUDE.md
section 1 alongside the parameter-only AST predicate that reported 97 offenders
where the truth was 2.

### 15.52 | Operator decisions 2026-08-07 on the three items that needed a call

Recorded so the next session does not re-ask, and so nobody guesses. All three
were put to the operator interactively after 15.51's re-derivation left five
items open.

ITEM 27 -- qB/JD library rows: **N ROWS, ONE PER FILE INSIDE THE DIRECTORY.**
The bridges record `st['filename']` as a bare name that may be a torrent
DIRECTORY (qb_bridge.py:514, jd_bridge.py:483). v3.66.837's contract -- record
a library row only when an absolute FILE path exists -- currently produces NO
row for these sites at all. The decision is to walk the directory at completion
and record one row per real file, because that is what a library row means
everywhere else in the panel; a directory row would make `file_path` mean two
different things depending on the source.

  WHAT THE IMPLEMENTOR MUST HANDLE, and none of it is optional: the walk can be
  large, and it RACES AN IN-PROGRESS SEED -- a torrent that is still seeding
  has its files on disk but may still be written to, so file_size captured at
  completion can go stale. Decide explicitly whether a row is recorded once at
  completion or refreshed, and say which. Also: this is the FIRST writer that
  turns one completion event into N rows, so anything counting completions by
  library rows changes meaning. Re-derive those consumers before writing code.

ITEM 3 -- /home/claude references: **SWEEP THE NON-TEST REFERENCES ONLY.**
Measured at 28cc9de: 246 tracked files reference the path, of which NINETEEN
are tracked test files that positively pin the string. Clean the ~227 non-test
files; leave the 19 tests alone and documented as a deliberate exception. No
gate goes red and no test is rewritten, which is the whole reason this scope
was chosen -- the register's original wording ("a test", singular) understated
the blocker by ~19x and would have sized the cut wrong.

  NOTE FOR WHOEVER RUNS IT: /home/claude is the LIVE agent home in a cloud
  container (140 entries incl. .ssh, .gitconfig, .claude/skills). It is not
  residue and must not be deleted. The subject is repo REFERENCES only.

NEXT CUT -- **NEITHER 30 NOR 14; STOP HERE.** The operator closed the session
after the pending merges rather than starting another cut. Both remain open and
tractable with no decision owed:

  * item 30 -- build `.githooks/pre-push` enforcing CLAUDE.md section 7's
    two-dot diff before a force-push. Small and self-contained; the test idiom
    is already established by tests/test_v3_66_872_claim_survives_the_shell.py,
    which builds a throwaway repo, copies the hook in and drives real git.
    `.githooks/` currently holds `pre-commit` only.
  * item 14 -- `start_manual_login` (runner_auth.py:379) still returns
    `(False, "An auto-login is already running")` while the login thread is
    alive, so the advertised Phase B takeover cannot open. Larger: the fix
    changes threading behaviour in the login path, and the register already
    records its anchors drifting once.

AND THE HIGHEST-VALUE ITEM IS IN NEITHER LIST. 15.50 item A --
`auto_recover_sqlite` quarantining HEALTHY databases -- is the defect that
actually destroyed the operator's history on 2026-08-07. `selftest.py:522`
catches `sqlite3.DatabaseError`, and OperationalError (`disk I/O error`,
`database is locked`) is a SUBCLASS, so transient contention under load reads
as confirmed corruption. The recovered file was 3.7 MiB with `integrity=ok`
when it was renamed aside. Item 11 supplied the contention; this is what turned
it into destruction, and it is untouched.

### 15.51 | Re-derivation of 15.36, 2026-08-07 at 28cc9de -- 15 of 23 measured items were ALREADY CLOSED

SUPERSEDES 15.36's status column. The list itself stands; what follows is each
item's MEASURED state, and the headline is the same one 15.40 found: the
register reports work that is already done. 15.40 measured six of eight. This
pass measured 23 items and found FIFTEEN closed, two mis-scoped, five genuinely
open and three unevaluable from a container.

EVERY VERDICT BELOW WAS OBTAINED BY RUNNING SOMETHING, never by reading a fix
comment -- a comment claiming a fix is exactly what satisfies an assertion
written to test for one.

CLOSED (15)

  2, 19  capture.sh commit identity + selftest stage. `emit_commit_identity()`
         at capture.sh:298, called :382, redirected into 01_sysinfo.log at
         :392, and the `[7b/9] Live selftest battery` stage exists. VERIFIED IN
         THE SHIPPED BUNDLE: the v3.66.926 capture's 01_sysinfo.log carries
         `commit : 28cc9de...`, branch, toplevel and commit date. Item 2 was
         filed as BLOCKED ON THE OPERATOR for a GO that was evidently given.
  5      bd-parband no longer mints a verdict for a suite it never ran. A
         nonexistent path now exits 2 with "BD-PARBAND UNEVALUABLE -- refusing
         to dispatch; no verdict minted". The register said "Small, confirmed
         open".
  7      Zero-collect classification. Subject chosen by AST over the 1250
         tracked tests/*.py, not by grep.
  8      Batch B as ONE parametrized invariant --
         tests/test_v3_66_912_wired_gates_refuse_on_empty.py, 11 passed
         (1 registry floor + 5 NEG + 5 POS), covering all five wired gates.
  9      bd-claim is NOT inert from a shell: `bd-claim add` exited 0 and a
         SEPARATE process saw the claim (`pid-512`). Released cleanly after.
  10     ai_boot_readiness in-flight marker EXISTS. `_persist(..., final: bool)`
         is keyword-only with NO default, and its docstring states the reason:
         "a silent True on an in-flight write is precisely the defect this
         closes."
  13     bd-state has FOUR callers, not one: build_release.py,
         build_pin_index.py, build_session_pack.py, verify_release.py.
  16     7a retirement. Tracked executable extensionless project-knowledge
         python files went 3 -> 1 (`bd-ready`), and all three *_stays_retired
         gates pass (11 passed).
  18     Venv specifier drift. check_requirements.py now builds
         `Requirement(line)`, compares `req.specifier.contains(have,
         prereleases=True)`, and RAISES Unevaluable when packaging is not
         importable rather than answering name-only.
         **CLAUDE.md section 5 IS STALE ON THIS** -- it still says specifiers
         are never compared and calls the item "Open, and nothing here can see
         it". Corrected in the same cut as this section.
  20     Import-graph gate is NOT blind to tests/: it walks `root / "tests"` at
         import_graph_gate.py:124, and 997 of the 1494 baseline edges have a
         tests/ source.
  23     The 885/886 capture gap is superseded -- v3.66.913 and v3.66.926 are
         both captured. The item's durable half, the delta reconciliation,
         PASSES on the newest pair: all three captures are internally
         consistent (passed+failed+skipped == total), and 913 -> 926 moves
         total +41 / passed +42, which reconciles exactly as 41 new tests all
         passing PLUS the one previously-FAILED test now passing.
  26     Census coverage. RUN against a seeded fixture (5 configured + 1
         unconfigured done row, no files on disk), the tool now prints
         "rows attributed but NEVER COMPARED : 5", "rows whose site_id is not
         in sites_config : 1", and "SWEEP EXAMINED NOTHING -- 0 of 6 rows
         resolved ... This is UNKNOWN, not clean". It also REFUSES to connect
         to an absent DB, "because connecting would create an empty one and
         this census would then report a clean library it never looked at".
  28     Six extractor completion paths. A helper `_dest_in_dir()`
         (runner_extractors.py:29-34) does `safe_dest(_P(dl_dir) / rendered)`
         and returns (path, basename); call sites use it (:1015). The leftover
         `from .detect import safe_dest` imports are DEAD, not evidence -- the
         grep that finds them is not the measurement.
  12     THREE OF FOUR sub-subjects closed: audit()'s two caps (@915),
         regen_nfos_from_history (@916), bitrot.verify_one (@925), each with a
         test. The producer-divergence subject was re-scoped by 15.47 (19
         producers across 4 tables, not 8 across 3) and 12(c)'s
         silent-saturation half remains open by decision.

STILL OPEN (5)

  11     Repo-root database writer. v3.66.926 removed every MODULE-SCOPE writer,
         so a bare import creates nothing (measured: 0 on-disk opens with the
         flag set AND unset, and still 0 after a 45s wait for scheduler
         timers). But test EXECUTION still lands `downloader_history.db` at the
         repo root -- measured by running a two-file band on a clean tree. That
         is the same mechanism that put 2 test rows into the operator's
         production history during the v3.66.926 capture (116 -> 118 history,
         101 -> 103 provenance). The item's specific `.db-wal` wording is
         narrower than what is actually happening.
  14     Phase B manual-takeover early-return STANDS. runner_auth.py:379
         start_manual_login still returns `(False, "An auto-login is already
         running")` when the login thread is alive, so the advertised takeover
         cannot open.
  27     qB/JD library rows unchanged: qb_bridge and jd_bridge still record
         `"filename": <name>` -- a bare NAME -- and neither records a library
         row (zero library_record call sites in either). Still a PRODUCT
         decision, so it needs an operator call rather than a fix.
  30     No `.githooks/pre-push` exists -- `.githooks/` contains pre-commit
         only. The repo-local mitigation for the launcher hook's advice is
         unbuilt.
  3      /home/claude residue, and IT IS MIS-SCOPED BY ROUGHLY 19x. The item
         says a blanket sweep "turns red a test that positively pins the
         string" -- singular. Measured: 246 tracked files reference the path
         and NINETEEN tracked test files pin it. Separately, /home/claude is
         the LIVE agent home in a cloud container (140 entries incl. .ssh,
         .gitconfig, .claude/skills), not sweepable residue -- so the subject
         is repo REFERENCES, not the directory. Still blocked on the operator's
         scope call, but the call is bigger than the item implies.

CANNOT EVALUATE FROM A CONTAINER (3)

  17     Does a container restart fire SessionStart? bd-restart-check returned
         OK exit 0, `source=resume`. The item needs an exit 1 mid-session and
         this session cannot manufacture one.
  21     The pre-force line b4f0c80 lives only in the box's object store.
  25     ~/bd-orphans-2026-08-01.bundle is box-local; absent here.

NOT RE-DERIVED (operator-bound or program-scale): 1 (name the twelve retired
tools -- unrecoverable from the tree), 29 (archive sequence), 31 (TASK_TRACKER,
11 rows), 32 (CODEX_HANDOFF, 23 groups). 33 (prose-only pool) is a ratchet, not
a target; toolchain/bin currently holds exactly 240 bd-* tools, matching the
population the item cites.

METHOD NOTE, because it is the reusable part. Eleven review agents were
launched for this and were SLOWER than measuring inline: this container has 4
cores, so the workflow concurrency cap is min(16, nproc-2) = 2, and most of
these items are greps or a single tool invocation. The agents completed 3 items
in the time inline measurement completed 12. Agents earn their cost on items
needing real investigation (14, 11), not on "does this file contain X". ALSO:
a re-derivation agent left a MUTANT in bulk_downloader/app.py -- the second such
residue this session -- re-introducing item 11's defect verbatim. The stop hook
caught it; committing would have shipped it. See CLAUDE.md section 2b.

### 15.50 | Item 11 closed in four sites, not one -- and three defects the fix introduced

v3.66.926 (PR #229). `import bulk_downloader.app` now performs ZERO on-disk
database opens and creates no database, MEASURED with BD_DISABLE_KEEPALIVE both
set and UNSET. 15.49 has the damage this caused; this is the repair and what it
cost to get right.

FOUR MODULE-SCOPE DATABASE WRITERS, and how each was found. The count is the
finding: a reader's denominator is "the code I thought to look at".

  1. db_init() + run_history.init + db_integrity_check + db_fts_optimize +
     db_queue_recovery_summary + run_integrity_check -- found by READING.
  2. migrations.apply_pending(), ~1700 lines below (1) -- found by TRACING
     sqlite3.connect during a bare import. It is the one that actually created
     the file, since _ensure_history_table sits underneath it. Reading had
     already "finished" and the import still made a database.
  3. The startup self-test -- found by an ADVERSARIAL REVIEW AGENT. The most
     dangerous of the four: it opens the DB and can RENAME IT ASIDE via
     `selftest.py:525`, so a bare import could quarantine live history. That
     is exactly the mechanism of 15.49.
  4. bg_scheduler + the webhook drain worker -- same agent. Their tasks reach
     the DB within milliseconds (vpn_stats.auto_blacklist_check,
     federation.expire_old_claims).

Sites 3 and 4 fire only when BD_DISABLE_KEEPALIVE is UNSET -- the SERVICE's own
configuration. All four now live in boot_once(): idempotent, lock-guarded,
latched on the RESOLVED DB PATH, called from an app.before_request hook and
explicitly by downloader_ui.py before it serves.

FOUR DEFECTS THE FIX ITSELF INTRODUCED. Every one is section 0's shape, and
ZERO were caught by reading the diff:

  * A process-wide `_BOOTED = True` latch. It answers "already booted" for a
    database this process has never opened -- boot tmpdir A, point DB_PATH at
    tmpdir B, get an EMPTY SCHEMA silently. Now keyed on the resolved path.
    Caught by asking what clean_workdir does to the latch BEFORE running.
  * An autouse boot fixture that fixed 10 tests and BROKE 2 that were passing
    (test_phase3_migrations_apply_cleanly asserts `applied >= 6`; the fixture
    had already applied them). Net-zero reads as progress until you read the
    failure NAMES. Caught by the names changing.
  * boot_once(force=True) could latch a state it never reached: the key
    survived from the earlier successful boot, so a raising re-boot reported
    "booted" forever. force now discards the key first. Caught by an agent.
  * THE TEST WRITTEN TO CHECK THE UNFLAGGED PATH INHERITED THE FLAG. Its
    subprocess harness did `env = dict(os.environ)`; every band exports
    BD_DISABLE_KEEPALIVE=1. It passed over a denominator excluding its subject
    and let sites 3 and 4 ship. Caught by an agent. Generalised into CLAUDE.md
    section 0.

A REVIEW AGENT LEFT A MUTANT IN THE SHARED TREE that re-introduced the defect
verbatim (`if not BD_DISABLE_KEEPALIVE: boot_once()` appended to app.py), plus a
copied probe test file. It landed between a `git add` and the next
`git status`. Explicit-path staging is the only reason it did not ship; no gate
covers it and CI would have passed. Recorded in CLAUDE.md section 2b.

BAND. 498 files: 9 failed / 6872 passed, every failure attributed by
measurement -- 7 e2e_smoke PRE-EXISTING (the identical 7 fail on main), 2
parallel artifacts (pass serially), 0 from the change. Ownership of the earlier
12-test fallout was PROVEN rather than assumed: the same set on pristine main
(584432e) in the same directory returned 131 passed / 0 failed, and the totals
reconcile exactly (152 - 131 = 21 = the two files dropped from the baseline).

THREE OPEN ITEMS, named rather than silently dropped:

  A. `auto_recover_sqlite` QUARANTINES HEALTHY DATABASES. The highest-value of
     the three, and the defect that actually cost the operator their history on
     2026-08-07. `selftest.py:522` catches `sqlite3.DatabaseError`, and
     OperationalError -- `disk I/O error`, `database is locked` -- is a
     SUBCLASS. So transient contention under parallel load reads as confirmed
     corruption and a healthy DB is renamed aside and replaced with an empty
     one. Evidence it is real, not theoretical: the recovered file was 3.7 MiB
     with `integrity=ok` and 114 history rows when it was quarantined. Nine of
     the ten quarantine events were the system re-corrupting its own empty
     replacements. The quarantine is ALSO racy -- keyed on `int(time.time())`
     at one-second resolution while `Path.rename` silently overwrites on POSIX,
     and the -wal/-shm companions move in a separate non-atomic step (three
     incomplete sets observed). Collisions leave no trace, so data may already
     have been lost to them.

  B. Importing app still writes `app_config.json`, `logs/`, `live_recordings/`
     and `state/` into the cwd, even with the flag set. Out of item 11's scope
     (that was the DATABASE) and the tests assert over `*.db*` only, so nothing
     overclaims -- but "importing has no durable side effect" remains FALSE.

  C. The boot hook is registered FIRST, so it precedes auth and CSRF and an
     unauthenticated request can trigger migrations plus PRAGMA
     integrity_check, under a lock now taken on every request. Measured hook
     order: _bd_boot_before_request, _check_token, _check_csrf,
     _dev_metrics_start. Accepted: downloader_ui.py boots explicitly before
     app.run(), so in production the first request never boots, and the work
     happens exactly once.

OPERATOR STATE. The recovered database was restored 2026-08-07: 116 history /
101 provenance / 1 queue, integrity ok, service answering 200 on /api/health.
Contiguous filename numbering across the corruption gap (2_43 -> 2_44) is
independent evidence the merge lost nothing. Originals kept in
~/db-rescue-20260807T012728Z/. Once #229 is merged AND deployed, running
capture.sh in ~/BulkDownloader is safe again; until then it is not.

### 15.49 | The box's history DB was quarantined 10 times in 25 minutes -- item 11, on production data

`history: 0 rows` (15.48's open question) is ANSWERED and it is not bit rot: the
operator's DB was quarantined and replaced with a fresh empty one. The data is
in the quarantine files, not lost.

MEASURED CHAIN, every link from source, none inferred:

  * the service's DB is `${APP_DIR}/downloader_history.db` --
    `install_service.sh:214` sets `WorkingDirectory=${APP_DIR}`, and
    `constants.py:24` is `DB_PATH = "downloader_history.db"`, a BARE RELATIVE
    string that `sqlite3.connect()` resolves against the CWD;
  * pytest run from the deploy directory resolves THE SAME FILE. `capture.sh`
    sets no `BD_INSTALL_DIR`, so `db._resolve_db_path()` falls to rung 3;
  * isolation is OPT-IN. `conftest.py:232` `clean_workdir` is a plain fixture,
    not autouse -- a test gets a tmpdir only if it asks for one;
  * the DB boots during COLLECTION. `app.py:80` calls `db_init()` at module
    scope, before any fixture can run, and pytest imports every module in every
    xdist worker. That is item 11, and `-m` filtering happens after collection
    so no lane assignment can prevent it;
  * `selftest.py:525` then renames the malformed DB aside and `db_init()`
    recreates it empty.

THE TIMESTAMPS CONFIRM THE MECHANISM RATHER THAN MERELY FITTING IT. Ten
quarantines between 2026-08-06 23:51:05Z and 2026-08-07 00:16:57Z, in bursts of
THREE within 11 seconds and FOUR within 8 seconds. Sequential service restarts
cannot produce that; a systemd restart loop racing a still-running parallel
pytest can. The window matches the all-parallel experiment (N=16/24/64) run in
the deploy directory.

SO ITEM 11 IS MIS-FILED IN 15.48 AND IN THIS SESSION'S OWN FRAMING. It was
recorded as a worker-count CEILING -- "all-parallel aborts at collection at
-n 64+". The ceiling is a symptom. The property is that RUNNING THE TEST SUITE
IN THE DEPLOY DIRECTORY CAN DESTROY PRODUCTION HISTORY, at any N large enough
to race. Re-rate it accordingly: it is a data-integrity defect that happens to
also cap concurrency, not a concurrency defect.

A SECOND, INDEPENDENT DEFECT IS VISIBLE IN THE QUARANTINE FILE LIST, and it may
already have cost data:

  * `selftest.py:525` keys the backup name on `int(time.time())` -- ONE-SECOND
    resolution -- and `Path.rename` SILENTLY OVERWRITES an existing destination
    on POSIX. Two concurrent recoveries in the same second destroy one of the
    two quarantine files, invisibly. Collisions are undetectable after the fact
    precisely because the loser leaves no trace;
  * the `.db` and its `-wal`/`-shm` are moved in SEPARATE, non-atomic steps
    (`selftest.py:529-533`). The observed listing has incomplete sets --
    `.1786061276` with no `-shm`, `.1786061278` with no `-wal`, `.1786061482`
    with neither -- and a quarantined DB separated from its WAL has lost its
    most recent transactions.

RECOVERY IS MEASURE-FIRST, not assume-first. The earliest file is the LIKELY
data-bearing one (the first quarantine moves the real DB aside; later ones move
fresh empty DBs), but that is a prediction. Count rows in every candidate
before choosing, with the service STOPPED.

NOT FIXED HERE, and each needs its own cut: item 11 proper (deferred and
idempotent DB boot, per 15.48); the racy quarantine above; and whether
`capture.sh` should isolate the DB at all -- note `BD_INSTALL_DIR` is read by a
dozen modules (`constants.py:15`, `macro_recorder.py:139`, `drift_repair.py:57`,
`push.py:57`, `app_health.py:175`, `app.py:1199`) and `app.py:1191` documents
UNSET as how the service runs, so exporting it for capture is not the one-line
fix it looks like.

v3.66.925 fixed a DIFFERENT bitrot defect the same session -- `verify_one`
resolving a bare basename against the CWD and PERSISTING a false
`integrity_issues` row for a present file, measured at 3 -> 6 -> 9 rows over
three scans. That is unrelated to the quarantine story above except that both
are the recorded-basename trap and both were found chasing `history: 0`.

### 15.48 | Session close 2026-08-07 at 5a6b9a6 (v3.66.923) -- capture went 45min -> 4min, and item 11 turned out to be the ceiling

Continues 15.47, same session. The operator reported that capture "used to be
all parallel and done in 5-10 minutes" and now took ~45. That was true, it was a
REGRESSION, and chasing it produced a better finding than the speedup.

**THE REGRESSION.** Lane assignment became fail-closed at `1ae076a` with 173
files reviewed into `tests/capture_parallel_files.txt`. Nothing ever backfilled
it -- four edits since, two of them retirements that only REMOVED entries --
while the suite grew to 1232 files. Everything unreviewed defaulted to serial.
It was silent because **a fail-closed default raises no error**: the gate was
green the whole time, doing less and less.

Measured before the fix: 173 parallel / 1059 serial, and of the 1059, **526
matched no risk criterion at all**.

| cut | what it did |
| --- | --- |
| 921 | backfilled 617 evidence-clean files; made SERIAL_NAME_TOKENS overridable |
| 922 | demoted test_u50_widget_backfills -- the hazard 921 predicted, arriving |
| 923 | allowlist outranks all heuristics but one; 1074 parallel / 158 serial |

**THE HEADLINE NUMBER: the whole suite runs in 4m06s.** 1232 files, 14,856
tests, `-n 64 --dist loadfile`, user time 84m29s -- about 20x. Also 4m40s at
-n 24 and 5m56s at -n 16. Against ~45 minutes.

**HOW THE EVIDENCE WAS BUILT, because the method is reusable.** Run everything
in ONE parallel lane, then re-run only the failures SERIALLY. Anything that
passes serially was lane placement, not a bug. Across four widths: 0 real
failures, every single time.

**AND THE METHOD'S LIMIT, which is the part worth carrying.** Four widths
(-n 64/32/24/16) produced TEN distinct refuted files, and the list DID NOT
CONVERGE -- -n 32 added one, -n 16 added two more. Three fail at every width and
are deterministic; the rest appear in one or two runs each.

The reason: they are **LOAD-sensitive, not order-sensitive**. The `*_frontend`
family spawns workers and asserts against `AdapterBudget.timeout_seconds` -- a
ONE-SECOND wall clock -- with failures like `test_worker_ipc_bytes_are_bounded`.
Under 16-64 concurrent pytest processes that is a coin flip. No packing makes
them safe and each run samples a different subset, so enumerating them one width
at a time never terminates. All five were named by MECHANISM instead.

That is a DIFFERENT class from `test_u50_widget_backfills`, which was a genuine
cross-file dependency (it needed a table an earlier file had created) and IS
fixed by placement. Do not conflate them: one is fixed by naming, the other
would be fixed by giving the test its own schema.

**ITEM 11 IS THE CEILING, AND ITS PRIORITY CHANGED.** Repeat all-parallel runs
ABORT during collection at higher widths:

    bulk_downloader/app.py:80: in <module>
        db_init()
    sqlite3.OperationalError: disk I/O error

pytest imports every test module in EVERY worker at collection -- `-m` filters
AFTER collection, so **lanes cannot change this** -- and app.py boots the
database at module scope. Measured: -n 32 completes; -n 64 is marginal (one full
run finished, a later one aborted); -n 128 aborts, once reporting `database disk
image is malformed`. The downstream "Different tests were collected between gwX
and gwY" errors follow from the failed import diverging a worker's collection.

15.47 files item 11 as a RESIDUE problem (471,095 bytes of junk). It is also a
**concurrency limit and a corruption risk**, and that is the argument for taking
its contract change rather than deferring it again. The @919 attempt is still
unmerged on PR #221's ref (`4b0916c`) with its latch defect documented; the
right shape is a boot that is DEFERRED AND IDEMPOTENT rather than SUPPRESSED,
which is what the latch got wrong.

Box DB checked afterwards: `integrity: ok`. The varying table counts in those
logs (34/32/26) are NOT damage -- db_init creates 8 tables and 60 distinct
`CREATE TABLE IF NOT EXISTS` statements live across the package, created lazily
by whichever module a process imported. **Left open: `history` reported 0 rows.**
Nobody established whether that is normal for this box.

**A TRAP IN THE MEASUREMENT HARNESS, worth more than the runs.** The sweep
unions each run's `.fail` file. A run that ABORTS at collection produces an
EMPTY `.fail`, which contributes nothing to a union while looking exactly like
"this run found nothing". Two of four runs aborted, and the union file therefore
named 5 files when the true union was 8. Acting on it would have silently
dropped three. Section 0, inside the instrument built to apply section 0.

**FOUR BUGS THE GATES CAUGHT WHILE BUILDING THIS, none by reading:**

  * moving the allowlist above the heuristics left the function's tail returning
    "parallel", which promoted EVERY unreviewed file in the repo. Two tests
    failed on the first run.
  * a hand-picked subset of SERIAL_SOURCE_SNIPPETS omitted five entries, so 7
    files were promoted that the real predicate refuses. Fix: borrow the
    classifier's own constants, never restate them.
  * `test_generated_artifact_workflow.py` was used as a SYNTHETIC exemplar and
    is a REAL file -- once the allowlist covered the tree it classified parallel
    and failed a test for reasons unrelated to the classifier. The same
    stale-exemplar trap appeared TWICE in sibling tests. Synthetic cases now
    carry a `_zzsynth_` marker.
  * a commit landed on `main` locally because the topic branch had been deleted
    after the previous merge and never re-created. The push failed on a missing
    refspec, which is the ONLY reason it did not land. Re-create the branch
    immediately after every post-merge reset.

**WHAT TO DO NEXT, in order:** (1) confirm whether `history: 0` is expected on
the box; (2) build item 11 as a deferred-idempotent boot -- it is now the thing
capping worker count; (3) `bitrot.verify_one` from 15.47 remains the highest-
value correctness find and is untouched.

### 15.47 | Session close 2026-08-06 at 3b73ccc (v3.66.919) -- tier 4 worked top-down; item 16 closed, item 14 was never real, item 12 is four times larger

Continues 15.46. Six cuts merged. The operator approved four scope questions up
front and the answers are recorded here because THREE OF THEM CORRECTED THE
REGISTER rather than following it.

| cut | item | what it actually was |
| --- | --- | --- |
| 915 | 12(c) | `audit()` reported `missing` and `size_drift` over TWO windows (newest 500 vs newest 1000 of one table) as sibling counts |
| 916 | 12(d) | `regen_nfos_from_history` tested a bare basename against the CWD -- and had a SECOND expression nobody recorded |
| 917 | 16 / 7a | three retired tools still tracked as extensionless project-knowledge files; deleted |
| 918 | 16 / 7a | the retirement gates' denominator excluded 473 tracked files; widened, with content-typed routing |
| 919 | -- | the box capture's only failure: a gate test that measured the MACHINE |

**ITEM 16 IS CLOSED, and it was never blocked on what the register said.**
15.36 says the spec "turns three gates red on four LIVE tools" and the item sat
unstarted on that. Measured: the codex_handoff gate cannot go red under ANY
widening (zero newly-entering files mention its subject), and after 917 the
remaining damage was ONE gate and TWO tools -- both of which were REAL stale
references, so no allowlist was needed. The block was an artefact of bundling
two separable changes.

**Two of item 16's instructions were NOT EXECUTABLE and are corrected here:**

  * "Lower the toolchain budget from 240 when it lands." `_TOOL_BUDGET`'s
    denominator is `os.listdir(toolchain/bin)` filtered to `bd-*`. The
    survivors lived in `project-knowledge/`, so deleting them moves n by
    **zero**. n is 240, exactly at the ceiling, and lowering the pin turns the
    gate RED. Do not attempt it later either.
  * The gates were blind for TWO reasons. The known one is the `'*.py' '*.sh'`
    glob. The one nobody had noticed: each gate's EXISTENCE check is a
    hardcoded path tuple that globs nothing, and every path in those tuples
    named a spelling that does not exist.

**ITEM 14 IS NOT-REAL -- ALREADY FIXED at v3.66.835 (`7a15c4b`, an ancestor of
HEAD) -- and the register contradicts itself about it.** 15.13 recorded it
CLOSED on 2026-08-02; 15.36 re-lists it as open; 15.35 re-derived its ANCHORS
but never its STATUS. Nothing states a reason for the re-opening.

Graded as the compound claim it is. Conjunct (b), "Phase B runs inside that
thread", is CONFIRMED. Conjunct (a), "returns early while the login thread is
alive", is REFUTED: the alive-guard carries an identity exemption added by @835
(`... and self._login_thread is not threading.current_thread()`). The
conclusion "the takeover can never open" is refuted by runtime probe in BOTH
directions -- on its own login thread it returns `(True, 'Manual login window
opened')` and reaches the opener; an external caller with a different login
thread alive still gets `(False, 'An auto-login is already running')`.

Treated as the higher-stakes verdict per section 1: every alternative reading
was enumerated and none leaves a surviving defect under the item's own wording.

Residue, both DOCUMENTATION:
  * `tests/test_u36_login_live_tests.py` still asserts in prose that the
    takeover "can never open from inside login_async's own _run thread". False
    at HEAD. The test passes for an independent and still-sound reason, so it
    is not urgent -- but it is the fossil the item's filing cites.
  * NOT item 14, found beside it: `bulk_downloader/app_dashboard.py` calls
    `runner.start_manual_login()` with the return value UNBOUND and then
    reports `{'ok': True, ...}` unconditionally, so on the legitimate refusal
    path the operator is told a window opened when none did. Same false-SUCCESS
    class as v3.66.834.

**ITEM 12 IS FOUR TIMES LARGER THAN FILED, AND ONE PRODUCER WRITES.** The
register says eight producers across three tables. Re-derived by AST over 1344
non-test tracked python sources: **19 producer functions across FOUR tables** --
`library`, `history`, `provenance`, `integrity_issues` -- 14 leaves plus 5
projections. Proven by RUNNING: on one synthetic DB the nine read-producers
return **three different answers (1 / 2 / 3) over three disjoint row sets**.

Exactly ONE producer resolves a recorded filename correctly. THREE tested a
bare basename CWD-relative; 916 fixed one, leaving
`cleanup_helpers.find_missing_metadata` and `bitrot.verify_one`.

**Act on `bitrot.verify_one` first, because it is the only producer that
WRITES.** Measured with the file PRESENT on disk it returned `kind='missing'`
and PERSISTED a false `integrity_issues` row, which feeds four read surfaces
and a Prometheus gauge. A false count is a wrong number; a false persisted row
outlives the process.

Named by nothing in the register: `timeline._entries_from_bitrot` selects
`FROM bitrot_issues`, **a table that exists nowhere in the tree**, and its
`_safe_query`'s bare `except` returns `[]` -- so that surface is silently
always empty, measured empty with a real `integrity_issues` row present.

**ITEM 12(c)'s SILENT-SATURATION HALF IS STILL OPEN, deliberately.** 915 made
the two windows one; it did not disclose that a count is a floor once the
library exceeds the cap. Disclosure needs a new key, which moves
`test_library_audit_panel_contract.py` and `api-types.ts` together. The
operator scoped 915 internal on purpose and a test holds that line -- file the
disclosure with the 12(d) contract question rather than reopening 915.

**ITEM 11 IS NOT SHIPPED, AND THE REASON IS WORTH MORE THAN THE CUT.** The work
is real and measured: `pytest --collect-only` on any of the 22 module-scope app
importers deposited **471,095 bytes** across 5 paths with zero test bodies run;
a plain import, 471,992 across 11. It needed **eight** writers gated, not the
spec's six -- `migrations.apply_pending()` is a seventh 1700 lines below the
gated region, and the last residue path came from THREADS (the webhooks drain
worker and bg_scheduler's saved-searches task, both behind
`BD_DISABLE_KEEPALIVE` gates whose value is set by a conftest FIXTURE BODY that
`--collect-only` never runs). Residue went 5 paths -> 1 -> 0.

**It was held back because its own probe found a latent defect the 496-file
band could not see.** `app.py` reads the sentinel ONCE at import; for a
module-scope importer that import happens during COLLECTION, so the flag
latches True and the boot stays suppressed for the whole process. A test body
touching the DB then gets `no such table: history`. Nothing in the suite hits
it today -- 6857 passed -- but it is a landmine, and the cut's own
over-correction guard missed it because that guard tests a CHILD PROCESS
without the sentinel, not the in-process latch.

The obvious repairs do not work: completing the boot in
`pytest_collection_finish` reintroduces the residue (no fixture has run, so
`BD_INSTALL_DIR` still points at the repo), and wiping `bulk_downloader.*` from
`sys.modules` rebinds nothing in an already-imported test module -- the missing
thing is the SCHEMA, not the module object. The real question is a CONTRACT
change: after this cut, importing the app no longer initialises the database as
a side effect, so a test wanting storage must get it from a fixture. That is
arguably correct, and it needs an explicit decision.

Work preserved: PR #221 closed unmerged, commit `4b0916c` on its ref, blocker
documented in its thread.

**THE BOX FOUND SOMETHING NO CONTAINER COULD, AND 919 IS THAT FIX.** The
v3.66.913 capture was 14832 tests, 14746 passed, ONE failed:
`test_v3_66_912_wired_gates_refuse_on_empty ::
test_a_real_denominator_is_still_evaluable[bd-env-report-check]`, `assert 2 !=
2`. The TEST was wrong, not the tool: its POS case ran the checker against the
repo tree, and that tool's verdict is a property of `.claude-env-report.md`, a
gitignored per-machine artifact section 5 says is stale after every cut BY
DESIGN. Same day, same commit range: this container answered STALE (1), the box
answered UNKNOWN (2). A test whose verdict flips on that is testing the machine,
and every container band called it green. The POS case now builds a tree the
tool can evaluate. Section 7's "the box is the gate" -- demonstrated, not quoted.

**A CLAUDE.md CLAIM IS STALE and it changed how this session was run.** Section
7 says CI's `gates` job runs gitleaks, artifact sync, compileall, pyflakes and
the CHANGELOG ASCII check -- "**no pytest at all**" -- and concludes CI's whole
test denominator is four mod3 files. Read 2026-08-06: `gates` ALSO runs a
"Repo-wide gates must pass" step of **15 pytest files**, plus version-pin
coherence, guard-file checks and `bd-freshcheck --repo-only`. The four-file
claim is true only of `postgres-integration`. Every cut here substituted that
exact 15-file set locally, which is what made merging on local gates defensible.

**NEW FINDINGS FILED, NOT FIXED:**
  * **CI runs pyflakes over `bulk_downloader` and `tools` only, so `tests/` is
    outside the denominator of the one instrument that reports undefined
    names.** Measured over `tests/`: 4 reports, 2 deliberate (a
    code-intelligence fixture), 2 REAL latent `NameError`s. One was fixed at
    917; `RR_MOUSE_INTERACTION` in `tests/test_v3_66_50_at2_dom_capture.py`
    remains. A gate over `tests/` is a cheap cut with one offender left.
  * `project-knowledge/STATIC_KB_MANIFEST.json` still carries rows for the
    three files 917 deleted. Generated, already stale (dated 2026-07-23 against
    v3.66.817, `file_count` 363 vs 355 entries), and no test reads it.
  * `test_e2e_smoke::_RealE2ESmoke` -- all 7 -- fail in this container on the
    PRISTINE baseline as well as with changes: `#root` resolves to an empty
    hidden div while `frontend/dist/index.html` exists. Pre-existing, unrelated
    to any cut here, and NOT the palette flake @906 fixed.

**CI RAN ~45 MINUTES BEHIND ALL SESSION.** Not broken -- lagged: no run was
ever scheduled for any PR here, while the retroactive main-branch run for
`c83bedc` (the 915 merge) completed SUCCESS. **A second-order effect to expect
again:** because the branch name is reused per cut and the queue lags, a
queued run can fire after its head commit has been squash-merged and the branch
moved. `actions/checkout` fetches `refs/heads/*` only, the old commit survives
solely as `refs/pull/N/head`, and gitleaks is handed a range it cannot resolve
-- `fatal: ambiguous argument`, `scanned ~0 bytes`, exit 1. That red is an
ARTEFACT. Gitleaks itself behaved correctly, refusing to report clean over an
empty scan. Do not let the noise train anyone to ignore red.

**NOT BOX-VERIFIED.** Everything except the 913 capture is container evidence.

### 15.46 | Session close 2026-08-06 at 9bf4d05 (v3.66.913) -- and CI never ran

Nine cuts: @905 dependency ceilings, @906 the Radix Escape discard, @907 the
foreign log handler, @908-911 item 4, @912 item 8, @913 this register. Tiers 1,
2 and 3 of the 15.38 plan are COMPLETE.

**WHAT IS BOX-VERIFIED, AND WHAT IS NOT.** Two green captures:

  * `4c66bbe` (v3.66.907) -- 14721 pass / 0 fail. Closed the @906 palette fix
    and the @907 ui_events fix on real hardware, including the pytest-9 logging
    defect that a container running 8.4.2 structurally could not reproduce.
  * `3b1b656` (v3.66.911) -- 14736 pass / 0 fail, live 36/0/0. All 42 cases
    across the new runner suites pass. Item 4 is box-verified.

**@912 AND @913 ARE NOT IN EITHER CAPTURE.** The second ran at @911, before both
merged. @912 adds a test-only suite; @913 changed no source at all. Small
exposure, but state it rather than let a reader assume the last capture covers
the tip.

**GITHUB ACTIONS COULD NOT ALLOCATE A RUNNER ALL AFTERNOON, and #213, #214 and
#215 merged with ZERO CI.** Six failure modes observed, none of them ours: a
`Service Unavailable` resolving action download info; a 15-minute queue then a
cancel; a 17-minute cancel; a 58-minute queue at 0s duration; and a
`synchronize` push that produced no run either -- which is what establishes it
was runner CAPACITY and not event delivery. `ready_for_review` is NOT in
ci.yml's trigger list (a bare `pull_request:` defaults to opened/synchronize/
reopened), so un-drafting a PR will never start CI; only a push, a reopen, or a
UI re-run will.

**THE LOCAL SUBSTITUTE FOR THE `gates` JOB, which covers every one of its
steps.** Worth keeping because it will be needed again:

    venv/bin/python -m compileall -q bulk_downloader tools tests
    venv/bin/python toolchain/bin/bd-regen-order --work "$PWD" && git status --porcelain
    venv/bin/python toolchain/bin/bd-guardcheck
    venv/bin/python toolchain/bin/bd-freshcheck --repo-only
    gitleaks detect --source . --log-opts "$(git rev-parse origin/main)..HEAD"
    venv/bin/python -m pytest -q -p no:randomly <the 15 suites named in ci.yml>

**GITLEAKS: SCOPE IT TO THE PR RANGE, and nothing else.** A whole-history scan
reported 36 leaks and a `--no-git` directory scan 21 -- both wrong
denominators. The first covers 352 commits the PR does not touch; the second
walks 77MB including `venv/` and `node_modules/`. The question is whether THIS
change introduces one. Measured over `aa39d3b..HEAD`, all 7 commits merged this
session: **no leaks**. That retroactively covers #213, which merged unscanned.

A count that disagrees with its range needs explaining, not waving away: a
2-commit range reported "1 commits scanned" because one commit was EMPTY and
had no diff to scan. Verified per-commit rather than assumed.

**TIER 4 IS ALL THAT REMAINS**: 11, 12-remnants and 16 open; 14 held (it changes
a login thread and cannot be judged from a container); 4 and 28 done. Item 12(d)
additionally needs an OPERATOR NOD on the API shape -- additive and
non-breaking, but product-facing.

### 15.45 | Tiers 1-3 are complete, and item 15's "class stands" was false

Close at a85abf3 (v3.66.912). Item 8 was pinned at @912; item 15 needed nothing
and this section says why, so nobody re-derives it a third time.

**ITEM 15 WAS FIXED AND PINNED ALREADY.** Its text said "improved, but the
class stands". Both halves of the class are closed:

  * `install_service.sh` -- the /api/health probe landed at @836. It polls for
    15s and reports THREE states, with `unknown` distinguished when curl is
    absent rather than folded into a pass. Pinned by
    `test_install_service_waits_for_serving.py`, 7 passing, and it is a
    both-directions suite: a serving service is still reported RUNNING,
    active-but-not-serving is NOT, the problem is surfaced, and the probe is
    shown to have reached the endpoint.
  * `capture.sh` step [4] -- the vault-unlock gate. Pinned by
    `test_capture_step4_waits_for_serving.py`, 6 passing.

**AND THE HOLD ON 15 NO LONGER APPLIES TO ANYTHING.** The tier plan's caveat
says hold 15 and 14 back because they change live box behaviour and cannot be
judged from a container. True when written; for 15 there is now no change left
to make, so the caveat protects nothing. **14 is unaffected and still held.**

**A TRAP THIS NEARLY WALKED INTO, worth more than the closure.**
`test_capture_step4_waits_for_serving.py` reads `capture.sh` and ONLY
`capture.sh` (`CAPTURE_SH.read_text()`), while its own docstring says
"install_service.sh has the same shape" -- naming a file its denominator
excludes. Reading that test alone gives every impression the installer is
covered, and it is not; a SEPARATE suite covers it. The near-miss was writing a
duplicate gate for behaviour already pinned. **Before building a missing test,
grep for tests that merely TOUCH the subject file, not just ones whose name or
docstring matches it** -- section 8's "look before you hand-roll", generalised
from tools to tests.

**FOUR ITEMS TODAY WERE ALREADY DONE AND REPORTED OPEN**: 9, 26, 5a and 28 (the
@871/872 sweep), all five tools of item 8, and now 15. Every one cost minutes to
re-derive by running or reading the real thing, and would have cost a wasted cut
to take on trust. Three of them were fixed with NO test, which is why the
register kept reporting them open; 15 is the opposite case -- fixed AND pinned,
and the register still said the class stands. **Both directions of staleness
happen. Re-derive before costing anything.**

TIER 4 IS WHAT REMAINS: 11, 12-remnants, 16 open; 14 held; 4 and 28 done.

### 15.44 | Item 4 is CLOSED at @911, and its description was wrong in four ways

Close at 627f5c3 (v3.66.911). Sub-cuts @908, @909, @910, @911. Container-only;
no box capture covers any of them.

**ACCEPTANCE, IN TESTS RATHER THAN SUITES -- and the suite count is the reason
to distrust the headline.** Over the 16 suites that item 4's own scan
identified:

    stage           collected   passing     suites green
    @907 baseline         340       243          3/16
    @909                  545       468         13/16
    @910                  646       536         13/16
    @911                  646       603         13/16

**The suite count stopped moving at @909 while 135 more tests started passing.**
@910 took test_fuzz_harness_frontend from Total: 1 (a single import-error row)
to Total: 102, and @911 took coverage_map from 0/75 to 65/75 -- neither moved
the suite number at all. A pass/fail-per-suite metric cannot see a suite that
goes from "cannot import" to "68 of 102 pass". Report what the headline hides.

**THE ITEM'S DESCRIPTION WAS WRONG IN FOUR WAYS. Every correction came from
running something, not from reading.**

1. **"18 of 93 suites" does not reproduce.** Re-derived at @907 over a stated
   denominator -- `git ls-files -- 'tests/*.py'`, 1239 files -- 17 files use a
   feature the stub lacks, and 13 of the 16 that are real suites fail. The 93
   came from a hand-scoped run nobody recorded the membership of.
2. **It names FOUR unstubbed features; there are SEVEN.** `pytest.CaptureFixture`,
   `pytest.main` and `pytest.mark.capture_serial` are absent from its list.
3. **Two of the predicted-affected suites PASS anyway.** `pytest.main` sits in
   `if __name__` blocks that never execute under the runner. The static scan
   fixes the denominator; only running fixes the predicate.
4. **There are FOUR root causes, not three.** The fourth --
   `monkeypatch.setattr`'s dotted-path detector -- is in no version of the item.

**THE TWO CAUSES THE ITEM DOES NOT DESCRIBE, both section-0 shaped:**

  * `monkeypatch.setattr` discriminated its 2-arg and 3-arg forms with
    `value is None and not callable(name)`. In the 2-arg form `name` holds the
    REPLACEMENT, which is usually a lambda -- so `callable(name)` was true, the
    guard MISSED, and it fell through to `setattr(<str>, <function>, None)`.
    **The detector excluded the most common instance of its own subject**, and
    on the rare non-callable replacement it fired only to refuse, so the form
    was unreachable either way. All 75 coverage_map failures.
  * `discover_and_run` never put the module in `sys.modules` before
    `exec_module`, the step the importlib docs call out and real pytest
    performs. `@dataclass` under `from __future__ import annotations` then
    cannot resolve its field types -- "'NoneType' object has no attribute
    '__dict__'".

**THE DESIGN TRAP THE ITEM DID FLAG IS REAL, AND BIGGER THAN IT SAID.** It
warned that a `param` returning `values[0]` would break "33 of 37" sites.
Measured at @907: **45 of 49** carry 2-5 values. The correct implementation
returns the TUPLE, which the existing injection already zips against argnames --
so the sub-cut needed no change to the injection at all.

**A RULE THIS SESSION EARNED THE HARD WAY, FOUR TIMES IN FOUR CUTS.** Every one
of @908-@911 shipped a first draft whose assertion matched TEXT THE TEST ITSELF
SUPPLIED, and every one passed on pristine source for the wrong reason:

  * @908  searched the whole result blob for "marks"; matched the synthetic
          module's FILENAME, test_marks.py
  * @909a searched the error for "xfail"; matched the bare AttributeError
          "type object 'mark' has no attribute 'xfail'"
  * @909b accepted `"SKIP" in err.upper()`; matched the synthetic test's own
          failure message, which contained the word "skip"
  * @911  searched the error for the module name; the runner embeds the whole
          TRACEBACK, which echoes the source line containing that name

**bd-mutate or a RED re-run caught all four. Reading the test caught none.**
The fix in each case was to assert over a NARROWER field (the error text only,
not the row; a distinctive refusal phrase, not the subject's name) and to keep
the subject's name out of the fixture where possible. When an assertion is
about an error MESSAGE, ask what else in the denominator contains that string --
filenames, tracebacks, and the test's own assertion text all do.

**STILL OPEN in those 16 suites, and NONE is a stub gap:** 32 in fuzz_harness
(31 are EOFError from multiprocessing/connection.py -- the harness spawns
processes), 10 in coverage_map, 1 in desandbox. Genuine failures or
environment; they want their own investigation rather than being folded into a
closed item.

**THE STUB'S CONTRACT, now explicit, for whoever extends it next.** Faithful
where real pytest is permissive; loud where silence would change a result. An
unknown mark is INERT because this repo has no --strict-markers and registers
markers via addinivalue_line, so real pytest treats it as metadata. But
`usefixtures`, `xfail` and `filterwarnings` REFUSE, `pytest.param` refuses any
kwarg but `id`, and an unresolvable dotted path refuses by name. A stub that
accepts what it does not implement is a false green.

### 15.43 | The palette flake was ours, and three instruments were blind to it

Close at b876613 (v3.66.906). A box capture failure chased as a playwright 1.62
regression for most of a session turned out to be a defect in our own SPA. What
the chase cost is worth more than the fix.

**THE DEFECT.** `react-dismissable-layer` 1.1.11 guards its Escape handler with
`isHighestLayer`, comparing an `index` captured at RENDER time against
`layers.size` read at EVENT time. Inside that settle window the guard is false
and the handler RETURNS -- the keypress is DISCARDED, not queued -- so the
dialog stays `data-state="open"` indefinitely. `CommandPalette` closed on
Escape only via that primitive, and a comment in the file asserted the primitive
"already covers Esc". It does, except when it does not.

**THE TELL WAS IN THE FAILURE TEXT ALL ALONG, AND IT WAS READ BACKWARDS.**
`data-state` stayed `open` for all 20 polls. Radix sets `closed` SYNCHRONOUSLY
in `onDismiss` and only then animates out -- so `open` means the close never
STARTED, while `closed` would have meant a slow animate-out. The first three
hypotheses (slow animation, tight timeout, parallel-lane load) were all about a
close that ran slowly, and every one was excluded by the attribute printed in
the error. `animate-in`/`animate-out` compile to NOTHING here --
`tailwindcss-animate` is not installed -- so the real close is a median 14ms,
360ms at 20x CPU throttling. **2000ms was ~70x the real cost; the budget was
never the problem, and a bigger one could not have helped a discarded key.**

**INSTRUMENT 1 -- `bd-band-derive` CANNOT BAND A DEPENDENCY BUMP, STRUCTURALLY.**
Measured: for the three-ceiling change the band is 18 files, and the union of
its signals EXACTLY equals the band (nothing hidden). For a non-`.py` path,
`module_consumers()` matches the literal full basename, so S4 reaches every test
that NAMES `requirements.txt` -- tests ABOUT the manifest -- and cannot reach one
test about what the manifest INSTALLS, because such a test never spells that
string. No signal keys on a package name at all (`grep` for playwright /
cryptography / psutil in the tool: **0** non-comment hits). Every signal is a
relationship between FILES; "runs under pytest" or "drives a browser" is a
RUNTIME relationship to a PACKAGE, which no file-relationship signal can
express. **So for a dependency bump, derive the band from the PACKAGE.** AST
census (predicate = exact top-level module name, because `'playwright' in name`
also matches `playwright_stealth`): 12 tests import playwright directly, 37
first-party modules do, and **384 test files -- 31% of 1224 -- are reached
transitively.**

**INSTRUMENT 2 -- THE PAIRED-ARM EXPERIMENT PREDICTED ITS OWN BLIND SPOT AND WAS
READ AS COVERAGE ANYWAY.** The "43 files, 603/603 both arms" result was NOT a
derived band; it was a hand-scoped set, and 15.41 already recorded that it ran
with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` against a preinstalled pool and is
therefore "SILENT about browser-driver compatibility". The failure that followed
was exactly that class. A caveat written down is not a caveat applied -- this
session quoted the 603/603 as if it settled the question the caveat excluded.

**INSTRUMENT 3 -- A 3/3 GREEN RE-RUN THAT COULD NOT SEE THE SUBJECT.** The first
box re-run used `-k command_palette`, which deselects the five tests that share
the browser. It passed 3/3 and was nearly read as exoneration. The full-file
10-run tally reproduced it 1 in 10. **At a ~6-13% rate, 3 passes is worth almost
nothing** -- state the power of a negative result before quoting it.

**WHAT ACTUALLY DISCRIMINATED, and it was cheap.** The rollback freeze the
operator saved before upgrading (`pip freeze > ~/bd-freeze-rollback-*.txt`) reads
`playwright==1.62.0`. The version had NOT moved, so the two prior GREEN captures
ran the same playwright as the failing one, and the whole version theory died in
one line. **Take that freeze before every upgrade; it is the only artifact that
can answer "did this actually change?" after the fact.**

**AND THE 1.61-vs-1.62 A/B HAD NO POWER, WHICH IS NOT THE SAME AS NO
DIFFERENCE.** Raw 4/65 vs 0/65 looks suggestive; Fisher two-sided is p=0.1192,
the four failures cluster in one window, and a sibling agent was demonstrably
downloading 641MB into the same container during it. Three matched interleaved
A/B runs produced ZERO events in both arms. A zero-event experiment discriminates
nothing -- report it as UNKNOWN, never as "the control arm is clean".

**MUTATION FOUND A HARNESS DEFECT, NOT A MISSING ASSERTION.** The first battery
scored `input-focus-skipped` as ESCAPED: the probe dispatched Escape at the
dialog `<div>`, so `e.target` was never an `<input>` and `allowInInput` was
unconstrained -- while a real Escape goes to the focused cmdk input, which
`isTextInput()` would skip. Retargeted at `[cmdk-input]`, and RED re-proven 5/5
afterwards because waiting for the input to exist could have pushed the press
out of the window. **`bd-mutate` could not run this battery at all: it has no
rebuild hook, so every `.tsx` mutant would be scored against a STALE
`frontend/dist` and would escape for a reason unrelated to test quality.** That
is a real gap in the tool, filed here rather than fixed.

**CONTAINER FACT, DURABLE: the E2E suite has never been runnable in the cloud
container, and it fails in `setUp` rather than saying so.**
`frontend/dist/index.html` loads `fonts.googleapis.com` in `<head>` BEFORE the
module script; the container's only egress is the agent proxy, which
Playwright's chromium does not use, so the request hangs FOREVER -- it never even
fails -- and the pending stylesheet blocks the module script, so React never
mounts and `#root` stays hidden. Launching chromium with `proxy={server}` does
NOT fix it. A pytest plugin that `page.route()`s the two font hosts and fulfils
them via `urllib` (which does honour the proxy) makes the whole suite run. Three
route tests (`add_site_modal`, `history_tab`, `needs_review`) still fail
identically on PRISTINE source in-container -- open, and NOT introduced by @906.

### 15.42 | Next work, scoped and ordered, at v3.66.903

Supersedes 15.38's tier plan for everything tiers 1-2 covered; tiers 3-5 there
still stand. Re-derive any item filed before @871 by RUNNING it first -- 15.40's
rule, which turned two multi-tool batches into one real fix each.

FIRST, AND MECHANICAL: land `claude/deps-latest`.
  Blocked ONLY on a green box capture. Needs three things it deliberately
  lacks -- a version bump, a CHANGELOG entry, and a rebase (it branched at 902,
  main is 903). Do not merge it without the capture: the container experiment is
  silent on browser-driver compatibility, and the box's cryptography jump
  (45 -> 50) is larger than the container's (49 -> 50).

THEN THE SUBSTANTIAL ONE: item 4 -- **CLOSED at v3.66.911, see 15.44.**
  Shipped as FOUR sub-cuts (@908-@911), not the three described below, and the
  "18 of 93" never reproduced. Acceptance measured in TESTS because the suite
  count stopped moving three cuts before the work did: 340 collected / 243
  passing -> 646 / 603, suites 3/16 -> 13/16. Everything from here to the end of
  this item's description is SUPERSEDED by 15.44; it is left in place only
  because the design trap it flagged was real.

  THREE SUB-CUTS, AND ONE HARD CONSTRAINT BETWEEN THEM. An adversarial review
  found `pytest.param` claimed by TWO of them, with the sub-cut that recommends
  landing FIRST shipping the naive design: `param` as an identity returning
  v[0]. **33 of 37 in-literal param sites carry more than one value**, so five
  suites would go green feeding a scalar to multi-arg tests, and the other
  sub-cut would then have to undo shipped code. `param` belongs to the
  PARAMETRIZE sub-cut alone -- strike it from the API-surface scope, or land
  parametrize first.

  Sharpest sub-group is `shell_source`: that helper landed @880, so the suites
  guarding the four most recent cuts are exactly the ones bd-band cannot run.

  THE STUB MUST STAY A STUB. It exists because real pytest is not always
  available. A stub that silently ACCEPTS an unimplemented feature and runs the
  test wrong is far worse than one that refuses -- a false green. Any design
  must say what it is NOT implementing and fail loudly there.

QUEUED BEHIND THAT
  * **item 12(d)** -- regen_nfos_from_history resolves a bare basename
    CWD-relative. library_final.py:468-518. The fix is to route rows through the
    EXISTING _resolve_recorded (same file, already tested) with an OPTIONAL
    download_dir. NEEDS AN OPERATOR NOD, not a design: `ambiguous` and `unknown`
    must not fold into missing_files -- the resolver's own docstring calls
    first-match-wins the defect -- so the endpoint gains two counters, changing
    the JSON shape of POST /api/library/regen_nfos. Additive and non-breaking
    (`frontend/src/lib/api-types.ts:1164` RegenNfosResult carries an index signature and does not
    even declare missing_files today), but it is a product-facing surface.
  * **item 12(a)** -- the eight-producer divergence. Confirmed REAL by the
    2026-08-06 recon; five product files named in its report.
  * **cloud-setup.sh's remaining inline package lists** -- fd-find,
    `wireguard-tools nftables iproute2 iptables`, `pypy3 caddy postgresql-client
    patchelf`, fonts. @903 fixed the declared groups; these are the same
    section-5 violation one layer down and belong in the fragment as new groups.
  * **tests/test_pytest_runtime_requirement.py pins a LITERAL specifier
    string** ("pytest>=7.0,<9.0"). Its own message says the intent is that real
    pytest is installed rather than the fallback runner -- PRESENCE, not the
    bound. It had to be edited to move a ceiling on 2026-08-06. A semantic
    assertion (parse the requirement, check the NAME is declared) would not.

HELD, AND NOT BY SIZE: items 15 and 14 change live box behaviour -- a service
startup path and a login thread. Neither can be judged from a container. Item 16
(7a retirement) stays blocked on spec rework: as written it turns three gates red
on four LIVE tools.

### 15.41 | The box/container parity sweep, the dependency experiment, and what it cost to find

Continues 15.40. Cuts v3.66.901-903 plus one branch left UNMERGED pending a box
capture. Everything here came from ONE field added at v3.66.892 -- the commit
block that made a capture bundle name its own tree. It reported `tree: dirty`,
and the chain from there was: rotated logs -> box/container diff -> ffmpeg
absent -> cloud-setup installing 2 of 5 package groups. **None of it was on any
list.**

THE ENVIRONMENT DIFF, AND THE TWO ONE-LINERS THAT PRODUCE IT

A fresh session should not re-derive these. `bd-env-parity --write` is the
purpose-built tool (section 8: look before hand-rolling) and it already solves
the browser-pool trap -- PLAYWRIGHT_BROWSERS_PATH wins, because guessing
~/.cache/ms-playwright names a directory nobody uses. But it captures
CAPABILITIES only -- browsers, node, python, ffmpeg, netns tools -- and NOT
package versions, so on its own it misses exactly the drift that mattered.
Pair it with pip freeze and check_requirements:

    bd-env-parity --write --out /tmp/bd-env.json
    venv/bin/pip freeze > /tmp/bd-freeze.txt
    venv/bin/python tools/check_requirements.py requirements.txt      # @896: now
    venv/bin/python tools/check_requirements.py requirements-test.txt # compares
                                                                      # SPECIFIERS

MEASURED 2026-08-06, box vs container: 110 vs 101 packages, 21 differing, of
which FIVE are declared -- cryptography 45.0.7/49.0.0, playwright 1.61.0/1.62.0,
curl-cffi 0.15.0/0.16.0, cssselect 1.4.0/1.5.0, packaging 26.2/26.3.

**THE BOX WAS CLEAN AND THE CONTAINER WAS NOT.** `check_requirements` exited 0
on the box for both manifests. So the cryptography drift 15.40 left as an
operator decision was CONTAINER-ONLY -- that decision is closed, and no bound
needed changing for it.

Also unequal and worth knowing: the box runs pool=default
(~/.cache/ms-playwright), the container PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers.
Section 5's "two pools, different chromium revisions" is live between these two
machines, so a capture-related divergence can be about the browser build.

THE DEPENDENCY EXPERIMENT -- latest stable, paired arms

Only THREE of nineteen declared packages were held back by their ceiling; the
other sixteen were already at latest. Isolated venv, same 43 files both ways:

    latest deps   603 passed / 1 skipped / 0 failed
    control       603 passed / 1 skipped / 0 failed

Same collected count in both arms, which is what makes the zero delta
attributable to the dependencies rather than to two different runs (section 5's
change-one-variable rule -- a session once got two spurious signals from a
baseline run in a different directory). 49/49 modules imported under
cryptography 50 / psutil 7 / pytest 9. pytest 9 + pytest-xdist 3.8 verified
under the capture's own flags (`-n 4 --dist loadfile`); xdist declares only
pytest>=7.0.0, so there is no upper-bound conflict.

Branch `claude/deps-latest` raises cryptography <46->51, psutil <7->8,
pytest <9->10. Deliberately NO version bump and NO changelog, so it cannot land
by accident. RAISED TO THE NEXT MAJOR, NOT REMOVED: an unbounded requirement is
how a future major lands silently during a routine `pip install -r`.

WHAT THAT EXPERIMENT CANNOT SAY, and it is the reason the branch is not merged:
it ran with PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 against a preinstalled pool, so
it is honest about import-time and API breakage and SILENT about browser-driver
compatibility. And the box's jump is LARGER -- 45 -> 50 on the crypto package
against the container's 49 -> 50.

THE PROVISIONER FINDING, AND THE CORRECTION THE OPERATOR'S OWN RUN FORCED

cloud-setup.sh requested `gtk` and `lint` and nothing else, while install_linux
takes `all` and provision_test_host names all five. The `media` group is ffmpeg;
the box had 6.1.1-3ubuntu5 and the container had NONE, so integrity.py's ffprobe
shell-out could not run at all -- and system_deps.sh's own comment says absent
ffprobe makes that check FAIL OPEN.

**THE FIRST DRAFT OF THAT FIX ADDED ALL THREE MISSING GROUPS AND WAS WRONG.**
The operator ran install_linux.sh on the box within the hour and it failed
exactly there: "nodejs : Conflicts: npm ... you have held broken packages". The
`node` group is UBUNTU's `nodejs npm`, and NEITHER machine gets node from apt --
box v22.23.2, container /opt/node22 v22.22.2, neither dpkg-managed, and Ubuntu's
candidate is 18.19.1, four majors behind. Node IS required (npm run build
produces frontend/dist; a missing bundle is a silent 503) -- it is simply not
apt's to provide. **"Install every declared group" was the wrong contract:
widening a provisioner without asking whether each group SUITS that environment
is the section 0 shape wearing a fix's clothes.** Caught by a real run, not by
review. @903 excludes it and asserts BOTH the absence and the written reason,
because 4-of-5 otherwise reads as a gap someone closes.

STILL HAND-ROLLED IN cloud-setup.sh, and it is the same violation one layer
down: fd-find, `wireguard-tools nftables iproute2 iptables`, `pypy3 caddy
postgresql-client patchelf`, the font packages. @903 fixed the DECLARED groups;
these belong in the fragment as new groups.

THREE MORE ERRORS OF MINE, all caught by running something rather than by review

  1. **`git check-ignore` misused a SECOND time**, after retracting the same
     misuse at @887. I read `check-ignore -v logs/` as "NOT IGNORED" and
     reported a missing rule; testing the real path showed `*.log` DOES match
     logs/bulk_downloader.log. It answers about a RULE against a PATH STRING,
     never about files that exist. The real defect was narrower and only a
     WRITER-side question could find it: log.py runs RotatingFileHandler with
     backupCount=5, so `.log.1` .. `.log.5` exist by design and `*.log` matches
     none of them.
  2. **I told the operator to re-pin the graph hash and that was a guess.**
     tools/l0_extract.py's PROD tuple walks bulk_downloader/, tools/ and
     frontend/src ONLY -- a tests/ change cannot move the content hash. Stated
     as fact, wrong, and it would have cost a needless sudo step.
  3. **I handed over `playwright install chromium` -- a hardcoded engine list**
     -- an hour after shipping a cut whose whole point was that lists belong in
     the fragment. install_linux.sh:387 uses bd_playwright_engines, which
     declares core=(chromium) and extra=(firefox webkit) SEPARATELY, and live
     check L4 audits the extras' presence. The hand-rolled command would have
     left L4 checking for browsers playwright 1.62 could no longer find.

TWO OPERATOR DECISIONS STILL OPEN, both keeping `tree: dirty` alive

  * **bd-prune/ is a NESTED GIT REPOSITORY** on the box -- it contains only
    .git, dated 2026-08-01, and nothing in this repo creates it.
  * **Two templates/reviewed/*.template.json** carry schema
    "bulk_downloader.template.review_candidate.v1" -- tool-generated review
    candidates. Committing or discarding them is a product call.

Until both are resolved `tree: dirty` fires on every capture, which makes the
field added at @892 useless: a signal that is always on is no signal.

### 15.40 | Overnight 2026-08-06, v3.66.891-900 -- and SIX of eight "open" items were already fixed

Ten cuts in one night. The cut list is in CHANGELOG; this section carries what
outlives it.

THE HEADLINE IS NOT A CUT. **Six of the eight register items re-derived tonight
were ALREADY CLOSED**, most of them at v3.66.871/872, and **not one of them had
a TEST**. That is why nobody knew: the repairs were real, nothing pinned them,
and the register kept reporting them open. Verified by RUNNING each tool, never
by reading its fix comment -- a comment claiming a fix is exactly what satisfies
an assertion written to test for one.

| item | filed as | measured 2026-08-06 |
| --- | --- | --- |
| 5b, 6 | band artifacts un-ignored | CLOSED -- `.gitignore:51`, `:87` |
| 10 | ai_boot in-flight marker | CLOSED at @874 |
| 13 | bd-state unreachable | not the filed defect; real one fixed at @888 |
| 9 | bd-claim inert from a shell | CLOSED at @872 |
| 5a | bd-parband mints a verdict | CLOSED -- refuses, exit 2, "no verdict minted" |
| 26 | census counts unexamined rows | CLOSED at @845 |
| 28 | six extractor paths dead | CLOSED |
| 18 | venv specifier drift | **REAL** -- fixed at @896 |
| 7 | zero-collect classification | **REAL** -- fixed at @898, and not as filed |

**THE RULE THIS BUYS: re-derive any item filed before @871 by RUNNING it, before
costing or scheduling it.** Two batches were scoped as four-tool and three-tool
cuts and each collapsed to one real fix. A wrong "still open" costs a wasted
cut; the sweep costs minutes.

**AND A CLOSED ITEM WITH NO TEST IS NOT CLOSED.** Every one of the six could
regress silently tomorrow. @895 pins four of them; the rest are still bare.

THREE DEFECTS FOUND THAT WERE ON NO LIST

  1. **bd-freshcheck had never graded this session's close claim** (@900). It
     took `closes[-1]` -- FILE order -- and SESSION_CARRY is not written in
     numeric order, so 15.39 sat ~900 lines ABOVE 15.30 and was invisible. Its
     own docstring said "the newest". The code and its contract disagreed
     silently, for an unknown number of releases.
  2. **check_requirements never compared specifiers** (@896) -- `version(name)`
     called and discarded. `flask==0.0.1` against installed 3.1.3 exited 0 with
     silent stdout, in the SOLE instrument of all three recovery paths.
  3. **selftest_verdict read two keys that do not exist** (@897) -- mine, from
     @893, found by the operator's own capture bundle three hours later.

TWO LIVE FINDINGS THAT NEED AN OPERATOR DECISION -- deliberately not acted on

  * **cryptography 49.0.0 is installed against a declared `>=42.0,<46.0`.**
     Found by @896 on its first real run. Which version this project wants is a
     dependency decision, not a test fix. The box is unmeasured from here:
     `venv/bin/python tools/check_requirements.py requirements.txt` answers it.
  * **The box's working tree reads `dirty`.** Surfaced by @892's commit block on
     the first capture that carried it. `tools/deployed_version.txt` was the
     obvious suspect and is NOT it -- gitignored at `.gitignore:37`, checked.
     `cd ~/BulkDownloader && git status --porcelain` names it.

THE MIRROR OBLIGATION BROKE THREE TIMES IN ONE NIGHT -- @889, @895, and again
while writing @899, the cut that prevents it. `test_pk_mirrors_do_not_drift`
caught all three correctly and LATE, each costing a 4-minute band. @899 makes
bd-band-derive flag it up front AND band the enforcing suite, because naming an
obligation without banding its gate is the same trap one comment further up that
file already describes.

METHOD -- FOUR OF MY OWN ERRORS, ALL INSTRUMENT-CAUGHT, NONE REVIEW-CAUGHT

  1. **A "must be nonzero" battery that passed VACUOUSLY** (@893): with no tool
     on disk python exits 2 for a reason unrelated to grading. Six of seven rows
     were green before a precondition asserted the subject EXISTS.
  2. **A mutant escaped because two paths produced the same exit code** (@896,
     @893): `assert code != 0` cannot discriminate when the fall-through is
     caught by something else downstream. Assert on the DIAGNOSIS, not only the
     verdict.
  3. **A CI failure caused by an entry's POSITION, not its content**: the ASCII
     gate takes everything from the first `## ` header to the next, and a naive
     prepend put the entry above the title so the preamble's em dash fell inside
     the window. CLAUDE.md section 3 says "anchored on the PREVIOUS `## v...`
     header" for exactly this reason. **The band could not have caught it** --
     that check lives in ci.yml, not in a test.
  4. **The same missing-import mistake in two consecutive cuts** (@899 `sys`,
     @900 `pathlib`). Appending tests to an existing file without first reading
     its imports makes the new tests fail for a HARNESS reason, which reads as a
     valid RED and is not one. Both proofs had to be redone.

WHAT REMAINS, and none of it is blocked on anything but time: item 12 splits
into four REAL subjects (subject (d), `regen_nfos_from_history` resolving a bare
basename CWD-relative, is a live dead endpoint in library_final.py:468-518 and
needs no operator decision); item 4 is bd-band's three root causes, 18 of 93
suites, and its `pytest.param` scope must sit in ONE sub-cut -- an adversarial
review found two sub-cuts both claiming it, with the one recommending it land
first shipping an identity function while 33 of 37 param sites carry more than
one value.

### 15.39 | Session close 2026-08-06 at 1f096c4 (v3.66.893) -- tiers 1 and 2 are done

Nine cuts merged across this session; the last three are recorded here because
they close item 19 and finish the size-ordered plan in 15.38.

| cut | commit | subject |
| --- | --- | --- |
| 891 | `e70a1b0` | the tier plan into the register; its chain was already stale |
| 892 | `ef9f253` | capture bundles name their own commit (item 19, cut 1 of 2) |
| 893 | `1f096c4` | capture probes the selftest battery (item 19, cut 2 of 2) |

**Item 19 is CLOSED, as two cuts, per the spec's explicit "do not batch these".**
15.38's tier 2 table is updated in place; it read four-of-five and now reads all
five.

WHAT A CAPTURE BUNDLE GAINS. Both halves land in the archive, which is the only
thing that survives the run:

  * `01_sysinfo.log` now opens with a `--- commit ---` block: sha, branch,
    toplevel, commit date, clean/dirty, and `source`. Three states, mirroring
    `app_health.build_identity`. **The MISMATCH state is the design, not a
    nicety** -- `git rev-parse HEAD` searches UPWARD and capture.sh cds to
    BD_HOME first, so a BD_HOME sitting below another checkout would have
    emitted a valid-looking sha about a DIFFERENT tree. Confidently wrong is
    worse than the silence it replaced.
  * `07b_selftest.json` and `07b_selftest.log` from the new `[7b/9]` stage, and
    `selftest=$SELFTEST_EXIT` is now a `--stage-exit`. So a battery reporting
    FAILs turns the capture red; WARNs do not, deliberately, because
    `capture_verdict.py` has no warn tier and gating warnings would fail a
    healthy box for a condition no code change fixes.

THE LOAD-BEARING CHECK IS THE DENOMINATOR, not the HTTP status. `curl -fsS`
exits 0 on a 200 carrying `{"error":"endpoint not found"}` -- capture.sh's own
header records step [7] probing `sse_smoke` that way for months with nothing
noticing. `tools/selftest_verdict.py` requires `ok + warn + fail >= 1` and
refuses a body that disagrees with itself. Exits 0 / 1 / 2, where 1 is real
failures and 2 is cannot-evaluate; both are nonzero, and they are distinct so
the log can tell a failing box from an unreadable answer.

UNVERIFIED, AND IT IS THE FIRST THING TO CHECK ON THE BOX: the selftest stage
has never run against the live service. It should pass -- the box's battery is
15 checks, well past the `>= 1` floor -- but it is a NEW way for the release
gate to fail, so a surprise there fails the whole capture rather than warning.
`07b_selftest.json` holds the raw body and is the file to read first.

MEASUREMENT FOR THE NEXT CAPTURE. The 889 capture was PASS at 14734 total /
14649 passed / 85 skipped, live 36/0/0, and its delta reconciled exactly
(+10 from 885, +8 from 888, +6 from 889 against 884's 14710/14625). 892 adds 7
tests and 893 adds 19, so the next run should read **14760 / 14675 / 85**. A
mismatch is signal. Re-pin the graph hash first -- two cuts changed source, and
step [2b] turns drift into a whole-capture FAIL.

METHOD -- THREE AUTHORING ERRORS, ALL INSTRUMENT-CAUGHT, NONE REVIEW-CAUGHT.
The pattern is the finding, and all three were made by someone who had just read
the section warning about them:

  1. **Six of seven "must be nonzero" rows passed VACUOUSLY** on pristine source
     (893). With no tool on disk, python exits 2 because it cannot open the
     script -- nonzero for a reason that has nothing to do with grading. A
     section-0 defect inside the RED battery written to close a section-0
     defect. Fixed by asserting the tool EXISTS before grading anything. **A
     "must fail" assertion needs a precondition that the subject is reachable**,
     or it grades the harness.
  2. **A mutant ignoring curl's exit code ESCAPED** (893). The fall-through fed
     an empty body to the grader, which correctly returned CANNOT EVALUATE, so
     the stage was still nonzero and `assert code != 0` could not discriminate
     the two paths. What that branch actually buys is the DIAGNOSIS -- an
     unreachable service and an unparseable answer are different problems -- so
     the added test asserts the log names curl's exit and preserves its stderr.
     Proven RED with the mutant and green without.
  3. **`shell_source.blocks_containing` cannot cut a brace group** (892). It
     resolves `for`/`while`...`done` and falls back to the SINGLE LINE for
     anything else, so against `{ ... } > "$OUT/01_sysinfo.log"` it returned
     only the closing redirect -- a denominator that structurally excluded the
     calls it was asked about. It failed loudly rather than passing, so it cost
     a round trip rather than shipping a blind gate. Worth knowing before
     reaching for that helper on a non-loop construct.

WHERE THE BOARD STANDS. Tiers 1 and 2 complete. Next is either **throughput
`8 -> 5a+7`** (two cuts, three items, container-only, no capture, no operator
decision) or **impact `4` alone** (`bd-band`'s three root causes, acceptance
18 -> 0, the only item that unblocks what CLAUDE.md section 4 mandates on every
future cut). **15 and 14 stay held regardless of size.** Tier 3 and 4 statuses
remain INHERITED rather than re-measured -- section 1 applies before working any
of them.

### 15.34 | Open item A is fixed; the spec's last step had to be dropped, and the reading was void again

Session of 2026-08-05, v3.66.884, branch `claude/bulkdownloader-resume-tpi03q`.
Two things happened: the confirming reading was attempted and VOIDED, and open
item A was implemented against a spec whose final step turned out to be
inapplicable to this register's own data.

**THE READING IS VOID, AND THE WAY IT IS VOID IS THE FINDING.** This session
was itself a cache BUILD. Direct readings: boot ~`21:49:11Z` (uptime 274s read
at `21:53:45Z`); `cloud-setup.sh` report header `2026-08-05T21:49:21Z`, ten
seconds after boot; `generated_against_commit=f863c49` = HEAD; report finalized
`21:53:35Z`. 15.33 already establishes that a build session's denominator
excludes the subject, so nothing here bears on the snapshot question.

What is worth recording is that **all four observables read FRESH-CLONE**:

    reflog OLDEST      3 entries, oldest 2026-08-05 21:49:18, messageless
    git branch         main + claude/bulkdownloader-resume-tpi03q (its own)
    behind origin/main 0   (after `git fetch origin main`, exit 0)
    hook block         none

Under the snapshot model every one of those should have read the other way. They
did not, because a build session manufactures the fresh-clone column **under
either model**. So the four-observable table is not merely uninformative on a
build session -- it is actively MISLEADING, and an agent that runs the four
readings without doing 15.33's step 0 first gets four agreeing signals and the
wrong conclusion. That is the same shape as the 15.31 protocol failure, one
iteration later: the instrument is fine, the denominator is not. **Decide
build-vs-cached FIRST from `generated_at` against boot; only then read the
table.** CLAUDE.md section 5 now says so in those words.

**A SECOND FACT, unexplained: two cache builds 1h42m apart** -- `20:07:23Z`
(15.33) and `21:49:21Z` (here), same day. The ~7-day expiry cannot produce
that, and a repo commit cannot either, since the panel hashes the pasted
bootstrap TEXT. `scripts/cloud-bootstrap.sh` line 2 carries a deliberate
`[cache-rebuild: <date> <version>]` marker (PR #185) whose purpose is to force
a rebuild when re-pasted, currently reading `2026-08-05 v3.66.881`; the marker
commit landed `19:40:59Z`, two minutes before the operator edit 15.33 records
at 19:43Z, which explains the FIRST build cleanly. The second has no
observable cause from inside the container -- the panel's text cannot be read
from here. **Recorded as an open question, not a trigger theory.** The
practical consequence is small and worth stating: do not plan a session around
being cached. `CLAUDE_CODE_CONTAINER_ID` is in the environment and is the
cheapest way for a later session to tell whether it shares a container with an
earlier one; nobody has been recording it.

**ITEM A: FIXED.** The defect reproduced exactly as 15.33 describes -- a
`--depth 1` clone of this repo, `bd-freshcheck --repo-only` exit 1, STALE
against 15.30's `5e87c68`, a genuine ancestor. The shipped repair moves the
shallow BOUNDARY instead of reinterpreting the exit code, three-way: complete
history -> nonzero is authoritative -> STALE; shallow -> `--deepen` and re-ask,
where exit 0 cannot be fabricated; still shallow or deepen failed -> UNKNOWN.

**THE SPEC'S STEP 3 WAS DROPPED, ON A MEASUREMENT, AND THIS IS THE ONE THING
TO CARRY FORWARD.** 15.33 ends with a by-sha existence probe to split the
still-shallow case, adjudicated after two wrong drafts. It cannot be applied
to this register's data. Measured on a `--depth 1` clone:

    git fetch --depth=1 origin 5e87c68                      -> 128, couldn't find remote ref
    git fetch --depth=1 origin 5e87c6800954a632d778...8261   -> 0
    ... then merge-base --is-ancestor <that sha> HEAD        -> 1   (FALSE; it IS an ancestor)

Git reads a short sha as a REF NAME. **Every close section names a short sha.**
So "the fetch failed, therefore the commit does not exist, therefore STALE"
would have reproduced the false accusation the fix exists to remove -- a third
draft wrong in a third direction, and one that reads as sound because the probe
IS sound, just not on this sha FORMAT. The gate-cannot-see-its-subject failure
arrived through the argument's *data* rather than its logic. `--deepen` carries
the whole repair; the residual case admits UNKNOWN.

Side effect, stated because it is a real change of character: on the repair
path the gate now performs a network fetch and deepens `.git`. It does NOT on
the happy path -- the first `is-ancestor` short-circuits -- verified by
computing both a real-ancestor OK and an invented-sha STALE with `origin`
pointed at nothing, and by a full `--repo-only` run on this container leaving
depth at 50 and `.git/shallow` intact.

VERIFICATION, this container, at the commit this section ships in:

| check | result |
| --- | --- |
| new suite | 7 tests, **2 proven RED** on pristine source (false STALE; offline-shallow must be UNKNOWN) |
| `bd-mutate`, 6 mutants, new band | **6 caught, 0 escaped, 0 invalid**, baseline GREEN |
| `bd-freshcheck --selftest` | PASS |
| `test_toolchain_534` | 44/44 |
| end-to-end, real `--depth 1` clone, fixed tool | exit **0**, same sentence a full clone prints |
| container repo after a full run | depth 50, still shallow -- untouched |

The fixture is hermetic: it builds its own six-commit repo and clones it over
`file://`, so the freshness gate's own test needs no network on the box.

**THE `ci.yml` COMMENT IS ALSO CORRECTED, on operator sign-off given after the
first commit landed.** It claimed a depth-1 checkout returns UNKNOWN exit 2.
That was wrong in BOTH eras: measured pre-fix it returned STALE exit 1
(fail-wrong, not fail-safe), and post-fix a reachable remote makes it deepen
and return OK. Since the comment is the stated reason `fetch-depth: 0` is
load-bearing, and it explicitly invites a future editor to revisit the depth,
leaving it would have handed that editor a premise that was never true.

The rewrite states what the depth actually buys -- not correctness, which the
tool now has either way, but the avoidance of a step that reaches the network
and deepens the checkout to answer at all -- and marks as UNMEASURED whether
that fetch can reach the remote from inside Actions. Verified safe to rewrite
first: the two suites that read `ci.yml` (`test_generated_artifact_workflow`,
`test_toolchain_534`) assert on the artifact-sync step's substrings and on
nothing in this comment.

OPEN SET, changed by this branch only where stated. **A is closed; the `ci.yml`
comment item is closed with it.** B, B2, 7a, 7b and 9 are untouched and still
carry the gates 15.33 and the kickoff put on them. **Unmeasured and left so:**
whether a depth-1 checkout inside GitHub Actions can reach the remote to
deepen -- deliberately not probed, since arming it would mean changing the
depth on a CI file to find out.

### 15.33 | The cache-rebuild discriminator -- READINGS, and why the protocol could not answer its own question

Run 2026-08-05 per 15.31, in the first session started after the operator's
19:43Z setup-script edit. **The freeze in 15.31 is spent and lifted:** the
trigger was consumed by this session, the readings below are recorded, and
CLAUDE.md section 5 now carries them as MEASURED.

THE FOUR READINGS, verbatim:

    HEAD                    5eb43d6 v3.66.882: the discriminator protocol, plus
                                    recon items C and D (#186)
    behind origin/main      0
    reflog OLDEST           5eb43d6 HEAD@{2026-08-05 20:07:19 +0000}
    hook blocks             none   (predicted by 15.31; correct)

`bd-restart-check` adds that the hook DID run: `OK  boot unchanged since the
hook last ran (2026-08-05T20:12:41Z, source=startup)`. So `none` here means
"ran and found nothing to repair", not "did not run" -- a distinction the four
readings alone cannot make, and worth having recorded.

WHAT IS NEW, split by evidential status -- because the first draft labelled the
whole of it MEASURED and half of it is inference:

**MEASURED: this session IS a cache build.** Container boot ~20:07Z (uptime 6 min
at 20:13:08Z); `cloud-setup.sh` header `2026-08-05T20:07:23Z`; report
`generated_against_commit=5eb43d6` = HEAD; `venv/bin/python` mtime 20:07:28;
`frontend/dist/index.html` 20:08:38; report finalized 20:12:24. Every one of those
is a direct reading.

**DERIVED: the rebuild trigger is LAZY -- a session start, not the edit.** Nothing
was observed at or after 19:43Z; every reading above is about 20:07. The negative
half rests on two unstated premises: the snapshot model, which this very section
argues a cache-BUILD session's denominator structurally cannot decide, and the
assumption that a rebuild fired at 19:43 would have produced a snapshot consumable
by 20:07. **Unexcluded alternative:** an eager rebuild that started at 19:43 in a
container this session never saw yields byte-identical readings here. Treat LAZY
as the best explanation, not as a measurement -- and note CLAUDE.md section 5
carries the same split rather than the flat claim.

WHAT THE PROTOCOL COULD NOT DO, AND WHY -- read this before designing the next
one. 15.31's two branches were "reflog-oldest ~= the rebuild time" (snapshot
carries `.git`) and "reflog-oldest ~= this session's own start" (fresh clone
per session), expected to be 24+ minutes apart. Because the rebuild fires AT a
session start they are **the same number**. And the deeper reason is section 0:
a cache-BUILD session has a freshly provisioned repo under *either* model, so
its denominator structurally excludes the subject. The distinguishing
measurement can only be made from a CACHED session -- which is exactly what the
2026-07-28 reading was, and why that one reading still carries the model.

The protocol was still worth running: it cost ten minutes, it produced the
lazy-trigger finding, it confirmed the hook's predicted silence, and it turned
"the docs say fresh clone, the reflog says otherwise" into a stated,
falsifiable prediction. But it did not settle the question it was written to
settle, and 15.31's reading guide should not be re-run as-is.

THE CONFIRMING READING -- no protocol, no freeze, one ordering constraint: the
session must start AFTER v3.66.883 merges, so that `behind >= 1` is guaranteed
under the snapshot model and the hook-block observable is live.

**FOUR observables, not one -- but they are TWO facts with two readings each, and
saying "four independent" would overstate the confidence.** This session's reflog
records the platform's session lifecycle:

    5eb43d6 20:07:23  checkout: moving from main to claude/bulkdownloader-discriminator-das7sy
    5eb43d6 20:07:22  checkout: moving from 5eb43d67c3e6... to main
    5eb43d6 20:07:19  <NO MESSAGE AT ALL>

**THE OLDEST ENTRY IS NOT A CLONE RECORD, and an earlier draft of this section
annotated it "(clone)" -- a word I supplied, not one git wrote.** Verified with
`cat -A .git/logs/HEAD`: the line is
`0000...0000 5eb43d67c3e6... Claude <noreply@anthropic.com> 1785960439 +0000`
with no tab and no message, while every later entry carries one. `git clone`
writes `clone: from <url>`. So the repo was created by something else -- init +
fetch + `update-ref` is the shape that produces a messageless creation entry.
**That is a THIRD possibility neither column below represents**, and it weakens
"fresh clone per session" as a framing before the reading is even taken. Do not
let the two-model table hide it.

`main` -> `claude/<session>` at 20:07:23 is exact. But **"sessions never run on
`main`" is n=1** -- it supports "this session was moved off `main` before the hook
ran", not a platform invariant. The hook's repair predicate is
`[ -z "$dirty" ] && [ "$ahead" = "0" ] && [ "$branch" = "main" ]`
(`.claude/hooks/session-start.sh:169`). The two models predict:

| observable | reads which fact | snapshot carries `.git` | fresh clone per session |
| --- | --- | --- | --- |
| `git reflog --date=iso \| tail -1` | this `.git` survived | `2026-08-05 20:07:19` | ~= own session start |
| `git branch` | this `.git` survived | a `claude/*` branch this session did not create | only `main` + its own |
| `git rev-list --count HEAD..origin/main` | HEAD is stale, not on main | **>= 1** | **0** |
| hook block | HEAD is stale, not on main | `*** STALE BASE ***` | none |

Rows 1-2 are two readings of ONE fact, and so are rows 3-4 -- the hook computes
its block FROM `$behind` and `$branch`, so it cannot disagree with row 3. Two
facts, corroborated twice each. Useful; not four independent signals.

**Read reflog-oldest as PRIMARY and the branch as corroboration** -- the reverse
of an earlier draft, which called the branch "most legible" and told the reader to
take it first. Two hazards make a NEGATIVE branch reading worthless:

- **CLAUDE.md section 2b mandates `git branch -D <the merged topic branch>` in the
  same breath as the post-merge prune, and that branch is THIS one.** Delete it
  before the snapshot is taken and "no foreign branch" appears under BOTH models.
- The next cache build may run in a different container entirely.

So state the predicate as **any `claude/*` branch this session did not create**,
never the one name, and record that an ABSENT foreign branch is **INCONCLUSIVE,
not fresh-clone evidence**. A present one is still strong.

The branch should be in the snapshot IF the snapshot is taken at or after
`cloud-setup.sh` completes -- created 20:07:23, provisioning finished 20:12:24 --
but **that premise is untested**, and it is the same premise the parenthetical
below already hedges for the commit. Applied to both or neither: whether this
session's later commit is in the snapshot is likewise unknown, so the branch NAME
is the observable, not its tip.

    git reflog --date=iso | tail -1           # PRIMARY
    git branch                                # any claude/* you did not create?
    git rev-list --count HEAD..origin/main
    # and: which hook block appeared at session start

COROLLARY IF CONFIRMED, and it is a design collision, not a curiosity: **the
REPAIRED path is structurally unreachable at a platform-created session START**,
because the predicate requires `branch = main` and the platform has already moved
off it before the hook runs. **On a RESUME the branch is wherever the session
parked it**, so REPAIRED *is* reachable on a later resume of a session that
checked out `main` by hand -- the unreachability is a property of how sessions
START, not of the hook, and the carve-out reasoning below is unaffected by that.
Every platform-created session start that is behind therefore gets the
STALE BASE block -- which puts CLAUDE.md's "do not read it as routine noise" in
direct tension with a block that fires once per session, every session. Alarm
fatigue is the predictable outcome and it is section 0's over-sensitivity defect
arriving on schedule. CLAUDE.md is reworded in v3.66.883 to say what must stay
routine is the REBASE RESPONSE, not the ignoring.

THE DECISION THE READING FEEDS -- named here, deliberately NOT built. Either
every cached session pays a manual rebase forever, or the predicate gets a cloud
carve-out: `CLAUDE_CODE_REMOTE=true` AND branch matches `claude/` AND clean AND
`ahead == 0` -> the fast-forward is lossless AND the position was
platform-manufactured seconds earlier rather than operator-chosen, so @879's
protection argument (never reset a position someone deliberately parked at) does
not reach it. Reading first, carve-out after: if the fresh-clone model holds
instead, none of this is needed.

NEW OPEN ITEM A: **the container's clone is SHALLOW and `bd-freshcheck` reports
a FALSE STALE rather than admitting blindness.** `.git/shallow` exists,
`git rev-list --count HEAD` = 50, graft `75e9024` (2026-08-03); the concurrent
session's container was shallow at a different depth (117), so this is a
platform property, not a one-off. A pre-graft commit exits **128** from
`git merge-base --is-ancestor`, not 1 -- measured on `cee4be70`, a real ancestor
of `main`.

DEMONSTRATED end-to-end, not inferred: in a throwaway `git clone --depth 1`,
`bd-freshcheck --repo-only` returns **exit 1** and

    STALE  register close tip
           15.30 says 'close at 5e87c68', which is NOT an ancestor of HEAD
           (5eb43d67c3e6) -- it names a commit this branch does not contain

The section is innocent; the sentence is false. The `rc2 != 0` branch of
`bd-freshcheck`'s close-tip `merge-base --is-ancestor` check cannot distinguish 1
("not in this history") from 128 ("I cannot see it"). **Cited by mechanism rather
than `file:line` deliberately**: an earlier draft said `:172`, which is the CALL --
the test is at `:176` -- and the file is extensionless, so the anchor gate's regex
cannot see such an anchor and would never report the rot.

**A SECOND DEFECT, and it is the one that will bite someone: `ci.yml` documents
the opposite behaviour.** The `gates` job sets `fetch-depth: 0`, so the defect is
not armed in CI -- but the comment justifying that dependency says a depth-1
checkout makes the check "return UNKNOWN (exit 2), failing for an environmental
reason rather than a real one". Measured: it returns STALE, exit 1. Fail-safe
versus fail-wrong. The comment is the stated reason the `gates` job needs full
history, and it explicitly invites a future editor to revisit the depth if
gitleaks stops needing it. That editor will expect a loud environmental UNKNOWN
and get a confident false accusation instead. Comment-only fix, its own cut.

ADJUDICATION OF THE SPEC, revised twice: against the operator's
over-sensitivity objection, and then against **my own measured-and-wrong table
row**. "128 -> UNKNOWN, UNKNOWN fails" is wrong on its own -- it converts a
false STALE into a false FAIL on a healthy repo, the same over-sensitivity the
code comment above line 172 exists to prevent.

**CORRECTION, recorded because the first version of this table was the
register's one measured-and-wrong line.** It reported fetch-by-sha as *refused*.
That test used a **fabricated** 40-char SHA, and `upload-pack: not our ref` is
guaranteed for a nonexistent commit regardless of server policy, so it
established nothing. Re-run against the REAL SHA
(`cee4be70f8e7675e65b18315e9853dc940295797`, resolved in a deepened clone):

Two throwaway clones, named per row because the depths differ and the reason
matters -- clone **D1** (`--depth 1`, the subject) and clone **P** (`--depth 1`
then `--deepen=200`, used only to resolve the real sha and establish ground
truth):

| clone | attempt | result |
| --- | --- | --- |
| D1 | `git fetch origin <short-sha>` | exit 128, `couldn't find remote ref` -- git reads a short sha as a REF NAME, not a sha |
| D1 | `git fetch --depth=1 origin <REAL full sha>` | **exit 0. It works.** GitHub serves SHA-in-want for a reachable commit through this proxy. |
| D1 | ... then `merge-base --is-ancestor <sha> HEAD` | **exit 1** -- "NOT an ancestor", which is **FALSE** |
| D1 | `git fetch --deepen=200 origin main` | exit 0, depth 1 -> **359**, and `.git/shallow` is **GONE** |
| D1 | ... then `merge-base --is-ancestor <sha> HEAD` | **exit 0** -- correct |
| P | ground truth: `is-ancestor` at depth **333** | **0**: it IS an ancestor |

**The 333/359 gap is not a discrepancy, it is a finding.** P deepened to 333 and
is STILL shallow (`.git/shallow` present, 2 graft lines). D1 deepened to 359 and
is **no longer shallow at all** -- 359 is the complete history, so `--deepen=200`
reached the root and git removed the boundary. The difference is that D1 carried
an EXTRA graft from the by-sha fetch, and `--deepen` extends every graft, so the
by-sha fetch changed how far a subsequent deepen travelled. That is a second way
a by-sha fetch perturbs the repository state a verdict is computed over, and it is
why step 2 of the spec below can test the boundary at all.

**And the correction makes the case for `--deepen` far stronger than "the
alternative fails".** Fetching by sha delivers the OBJECT without the connecting
history, so ancestry becomes uncomputable and git answers **1** instead of
**128**. That trades a *detectable* blindness for an *undetectable false
negative* -- the gate would then report STALE with no signal that anything was
missing. **A fix built on fetch-by-sha would have reproduced the exact defect
class it was written to remove**, which is CLAUDE.md section 0's warning landing
inside the repair for an instance of section 0. Only `--deepen` connects the
history and yields the true answer, and it needs no SHA in hand and depends on
no server capability.

THE SPEC: on 128, `git fetch --deepen=<N> origin <tracking branch>`, then re-ask
`is-ancestor`. **Never fetch-by-sha** -- not because it fails, but because it
succeeds into a wrong answer.

**TWO DRAFTS OF THE UNKNOWN TRIGGER WERE WRONG IN OPPOSITE DIRECTIONS. Both are
recorded, because the second was written by someone who had just fixed the
first.**

Draft 1 -- "only an object still unreachable becomes UNKNOWN" -- keys on OBJECT
PRESENCE, and the false-1 state measured two tables up is exactly one where the
object IS present. A presence test calls that decided and ships the false STALE
unchanged.

Draft 2 -- "in a shallow clone only exit 0 is trustworthy; any nonzero is
UNKNOWN" -- fixes that and **destroys the gate.** The check's own comment
(`toolchain/bin/bd-freshcheck`, the paragraph above the `rc2` test) says the
gated condition exists to catch "a typo, a commit from an abandoned branch, and
a sha invented from memory". **All three are also nonzero-in-a-shallow-clone**,
so draft 2 turns every real STALE into UNKNOWN in the environment sessions
actually run in -- and this session's own fabricated-sha error is precisely the
case it would have laundered. Removing a false STALE by removing STALE is section
0's over-sensitivity flip, one draft after the false-clean.

THE SPEC, three-way, keyed on the shallow BOUNDARY and using by-sha for what it
is actually good for:

1. `git fetch --deepen=<N> origin <tracking branch>`, then re-ask `is-ancestor`.
   **Exit 0 -> OK.** A connected path was found, and the shallow boundary cannot
   fake one.
2. **Repo no longer shallow** (`git rev-parse --is-shallow-repository` false, i.e.
   the deepen reached the root and git removed `.git/shallow`) -> a nonzero is
   authoritative -> **STALE**.
3. **Still shallow and still nonzero** -> probe EXISTENCE with a by-sha fetch,
   whose 128-vs-0 splits the verdict: the fetch **failing** means the commit does
   not exist or is unreachable on the remote -- a typo or an invented sha --
   -> **STALE**, the true positive draft 2 threw away. The fetch **succeeding**
   means the object exists but ancestry cannot be decided in this clone ->
   **UNKNOWN**, with its own exit per `bd-restart-check`'s three-state precedent.

So the slogan sharpens: **never let a by-sha fetch feed `is-ancestor`.** It is a
sound existence probe and a poisonous ancestry input, and conflating those two
uses is what made draft 1 wrong. Two caveats to design against:
it makes a freshness gate non-hermetic (network), and it mutates `.git` by
deepening, which a read-only gate arguably should not do --
`test_bd_regen_check_is_read_only.py` is the precedent that such a property gets
asserted here. Wants a RED-first test that builds a shallow clone as its
fixture, RED proven in both directions (false STALE before, correct verdict
after, no new failure on a healthy full clone) -- and a case pinning the
by-sha-then-false-1 path, so nobody "optimises" the deepen away later.

**Unverified: whether the box's clone is shallow.** Container and scratch clone
only; do not generalise to `test4`.

NEW OPEN ITEM B: **`bd-band` manufactures 80 failing test cases across 22 suites
(82 across 23, of which `test_pin_index_in_sync`'s 2 were real), and
CLAUDE.md section 4 mandates the tool that does it.** v3.66.883's band ran 100
suites: 75 green, 26 FAIL lines = 1 summary line + 2 zero-collect helper modules
+ **23 real suites**. v3.66.883 touches no code (docs, register, changelog,
version string, version pin), so none of it could be this cut's -- with the one
pre-empted exception, `test_pin_index_in_sync`, which failed because
`build_pin_index.py` had not been re-run after the bump and passed 7/7 after the
regen.

THE MEASUREMENT, runner as the only variable, same container, same commit
`5eb43d6`, same packages:

| runner | 23 suites |
| --- | --- |
| `venv/bin/python -m pytest -q` | **413 passed, exit 0** (110s) |
| `bd-band` -> `run_tests.py` | **23 suites FAIL, exit 1** |

**The operator's dependency-drift prior is REFUTED, and so the specifier item
stays theoretical rather than becoming measured.** The venv was rebuilt from
names today and versions are unchecked outside the 19 core pins, so drift was
the right first suspect -- but the errors are fixture- and API-resolution
failures inside BD's own runner, and the same packages pass 413/413 under real
pytest. Nothing here is evidence about specifiers. Do not record this as the
specifier item's measurement.

ROOT CAUSE. `bd-band` does not run pytest. It runs `run_tests.py`, BD's offline
runner, whose pytest compatibility stub is incomplete. In `run_tests_core.py` the
shim for `clean_workdir` is applied to a TEST FUNCTION's parameters (`:453`) but
`_resolve_named` handles only `tmp_path`, `monkeypatch`, and same-module fixtures
(`:555`) -- so the identical name resolves on one path and is silently dropped on
the other, and the fixture call then raises `TypeError`. Section 0's shape
exactly: two resolution paths for one name, only one of them complete.

NINE SIGNATURE CLASSES ACROSS 22 SUITES (+ `pin_index_in_sync` = 23),
COLLAPSING TO FOUR ROOT CAUSES. Two counting errors are recorded here rather
than smoothed over, because both are section 1's lesson in miniature:

1. I sampled ONE suite, hypothesised that nine shared its cause, then checked
   all eleven -- they split **five** ways. The sample was honest; the
   extrapolation was wrong.
2. I then wrote "five distinct stub defects" as the headline. **Five was the
   split of the eleven unsignatured FAILs, not of the whole failure set** -- a
   subset's count promoted to the population's, sitting directly above a table
   that shows nine rows. A number that contradicts the table beneath it.

The four root causes, two of them spanning several signatures:

| root cause | signatures | suites |
| --- | --- | --- |
| stub API surface | `param`, `importorskip`, `MonkeyPatch`, `mark.slow` | 9 |
| fixture-dependency resolution | `clean_workdir` dropped | 7 |
| module import path | `shell_source`, `conftest`, cross-test-module | 5 |
| parametrize injection | test arg not injected | 1 |

| signature | suites |
| --- | --- |
| `named fixture X failed: TypeError: X() missing 1 required positional argument: 'clean_workdir'` | 7 -- census_file_size_drift, cut25b_history_filename, cut31_done_today_iso, cut40_dashboard_today_iso, cut41_ts_iso_producers, library_forward_path, queue_ts_since_cursor_pin |
| `'_PytestStub' object has no attribute 'param'` | 4 -- capture_shell_runtime, playwright_engines_single_source, provision_test_host, v3_66_820_installer_browser_reach |
| `'_PytestStub' object has no attribute 'importorskip'` | 3 -- auth_state_buckets, v3_66_550_weather_ssrf, webhooks_subscription_ssrf |
| `No module named 'shell_source'` (tests/ not on the stub's path) | 3 -- v3_66_879, v3_66_880, v3_66_881 |
| `'_PytestStub' object has no attribute 'MonkeyPatch'` | 1 -- live_seed_starts_and_settles |
| `type object 'mark' has no attribute 'slow'` | 1 -- desandbox_tool_verifiers |
| `No module named 'conftest'` | 1 -- home_config_stores_are_guarded |
| `No module named 'test_v3_66_879_...'` (test-module import) | 1 -- v3_66_882_restart_without_the_hook |
| parametrized test arg not injected (`TypeError: test_every_cli_mode_exits_nonzero_naming_the_file() missing 1 required positional argument`) | 1 -- dependency_graph_fails_closed |

Nine rows, 22 suites, plus `test_pin_index_in_sync` = 23. The `shell_source`
group is the newest and the most instructive: that helper landed at v3.66.880, so
three suites written since then cannot run under `bd-band` at all -- a band tool
that cannot execute the tests guarding the last three cuts.

SCOPE, AST-measured over **1231 tracked `.py` files under `tests/`** (denominator
stated; zero tracked extensionless python there, checked). The first predicate --
"a module fixture whose dependency the stub drops" -- returned 13 and was a
SUPERSET: seven failed, six did not, and I recorded the gap as unexplained rather
than rounding it away.

**MY FIRST EXPLANATION OF THE GAP WAS WRONG, AND THE CORRECTION IS A WORSE
FINDING.** I wrote that the six passing ones are autouse and that "autouse
fixtures are resolved on a DIFFERENT stub path", citing a code comment -- which
is about the ORDERING of named-fixture resolution relative to autouse and says
nothing of the kind. Two defects in one paragraph: a mechanism asserted from a
plausible reading, and a citation for a claim its source does not make.

MEASURED. `run_tests_core.py:689` (autouse) and `:695` (named) register a fixture --
only if `not name.startswith("_")`. `_isolate` begins with an underscore, so it
is **never registered at all**, and 834 runs 9/9 PASS with **zero** autouse lines
in its output.

**AUTOUSE CONFERS NO SAFETY WHATSOEVER, and a one-variable probe proves it.** Two
byte-identical modules, differing only in the fixture's NAME:

    @pytest.fixture(autouse=True) def _isolate(clean_workdir)  -> exit 0, 1/1 passed
    @pytest.fixture(autouse=True) def  isolate(clean_workdir)  -> exit 1
        "autouse fixture isolate failed: isolate() missing 1 required
         positional argument: 'clean_workdir'"

The autouse path has the SAME dropped-dependency gap -- it builds its kwargs from
`monkeypatch` and `tmp_path` alone, and `clean_workdir` is supplied only for a
TEST FUNCTION's parameters. **The discriminating variable is DISCOVERABILITY, not
autouse-ness.** That distinction is load-bearing rather than pedantic: a predicate
that declared autouse fixtures safe would be a false negative the moment anyone
adds a non-underscore autouse fixture with a dropped dependency -- section 0's
shape inside the closure of a section 0 finding. Corrected predicate: *a module
fixture the stub actually COLLECTS -- non-underscore name, defined in the test
module itself -- whose dependency the stub drops.* Exact in both directions, and
exact for the measured reason.

The second escape route is separate and also not resolution: `tests/conftest.py`
is never imported by the stub at all (the register's own signature row `No module
named 'conftest'` is that defect surfacing elsewhere), so its four fixtures could
not run whatever their names.

And the earlier "0/6 autouse" row was section 0 inside my own precision claim.
The 6 were **fixtures across three files**, not suites: four live in
`tests/conftest.py`, which is not a suite and can never appear in a FAIL list, so
two thirds of that denominator structurally excluded the subject. The honest
comparison, over the same AST denominator (**1231 tracked `.py` under `tests/`**):

| population | count | outcome |
| --- | --- | --- |
| discovered module fixtures (no leading `_`) with a dropped dependency | 7 suites | **7 / 7 FAIL** |
| underscore-prefixed module fixtures with a dropped dependency | 2 suites (834, 835) | pass -- fixture **silently never invoked** |
| `tests/conftest.py` fixtures with a dropped dependency | 4 fixtures | not suites; **excluded** rather than counted as passing |

So the 7/7 half stands and "exact in both directions" does not; the second row is
a different defect wearing the first one's clothes.

**NEW OPEN ITEM B2, and it is worse than the failures this item is about: a
declared fixture is SILENTLY SKIPPED, so the suite passes without it.** No
warning, no skip marker -- `bd-band` reports `Total: 9 | Passed: 9`. A test that
asserts nothing because its setup never ran is section 0's false clean, and
`bd-band` cannot see it by construction.

**Scope it precisely, because the obvious framing overstates it.** `_isolate`'s
docstring says `clean_workdir` sets BD_INSTALL_DIR *and* the cwd so
`downloader_history.db` cannot land in the repo. Measured under `run_tests.py`
with a probe test: **cwd IS a tmpdir** (`make_clean_workdir` chdirs for every
test, `run_tests_core.py:421`) but **`BD_INSTALL_DIR` is `None`** -- the stub's
mimic never sets it, and neither is `BD_TEST_MODE`. So the db-location property
survives anyway, via `_resolve_db_path()`'s THIRD rung resolving the relative
path against the cwd. What is lost is precisely the belt-and-braces half the real
`tests/conftest.py:233` comment names verbatim -- "even if subsequent code chdirs
away" -- plus `BD_TEST_MODE`. Real but narrower than "the isolation does not
happen", and stated this way because the first audit framing claimed the stronger
version and the measurement does not support it.

BLAST RADIUS IS THE CONTAINER DEV LOOP ONLY. `capture.sh` forwards to real pytest
via pytest-xdist -- and the v3.66.882 bundle below PROVES it rather than implying
it: all 23 of these suites ran on the box and all 23 passed. That is why the box
captures 14618 passed while
`bd-band` fails these here. No shipped code is implicated. But CLAUDE.md section
4 tells every session to derive with `bd-band-derive` and run with `bd-band`, so
the next session hits this and must not read it as a regression. Section 0 counts
over-sensitivity as a soundness bug equal to a false clean, and this is 80 false
failures sitting in the tool the contract points at.

**THE "~47" IN THIS ITEM'S FIRST DRAFT WAS INHERITED, NOT MEASURED, AND IT WAS
WRONG.** It came from the concurrent session's message and I repeated it twice as
a headline without deriving it. Measured over the stated denominator -- the
`Failed:` field summed across the 23 real FAIL suites in the band log -- the
figure is **82**, of which `test_pin_index_in_sync`'s 2 were a REAL stale-artifact
detection that the regen resolved, leaving **80 manufactured**. The largest
contributors are census_file_size_drift 26, live_seed 11, cut25b 8,
library_forward_path 7, queue_ts 4, dependency_graph 4. This is CLAUDE.md section
1's rule -- "numbers that move must be measured at decision time, never quoted"
-- violated in the very entry that cites it, and caught by an adversarial audit
rather than by review.

SMALL ITEM: `bd-band` grades a suite that collects **zero** tests as FAIL, and
`bd-band-derive`'s filename-stem signal sweeps non-suite helper modules
(`tests/shell_source.py`, `tests/_phase_scripts/__init__.py`) into a band. Two of
the 26 FAIL lines were that, not failures. Either the derive should exclude
non-`test_*` modules or `bd-band` should report zero-collected as its own state --
and that state must **FAIL for a `test_*` module** while being reported-not-failed
only for a non-suite module the derive should not have banded. A `test_*` suite
collecting zero tests is a REAL signal (a renamed test, an import guard that skips
everything, a decorator typo), so an advisory-only state would turn today's true
positive into a silent pass -- and those two causes are exactly the pair the new
state has to keep apart.

ITEM C IS STILL OPEN, BUT ITS INSTRUMENT IS NOT BLIND -- that risk is CLOSED.
`bd-restart-check` returned exit 0 (`boot unchanged since the hook last ran
(2026-08-05T20:12:41Z, source=startup)`), which means no container restart has
happened this session, so it reports the instrument is armed rather than
answering C. C is answered only by an exit 1 observed mid-session with no new
session started.

The concurrent session flagged a real risk against its own tool: if
`/proc/sys/kernel/random/boot_id` is the HOST kernel's rather than
container-scoped, the tool returns OK on precisely the event it was built to
detect. **Measured across two contemporaneous containers and REFUTED:**

    container      boot_id                                machine-id
    concurrent     a52ecbc5-90d8-418b-8c40-3c3d1b3c4270   0d0af05ee8fd4dc29275718f2ce4dff1
    this one       0a744e36-8cf8-4a46-8358-eac305ff6d79   0d0af05ee8fd4dc29275718f2ce4dff1

`boot_id` DIFFERS while `machine-id` is IDENTICAL. So `machine-id` is baked into
the image and useless as a container discriminator, and **the instrument is not
blind in the way flagged.**

**State the claim no wider than the evidence, which is narrower than
"container-scoped".** What is refuted is a SHARED `boot_id` across simultaneous
containers. Two further gaps remain: the two containers were never shown to be
**co-resident**, and two containers on different physical hosts would differ even
if `boot_id` were the host kernel's -- while an identical `machine-id` evidences a
shared IMAGE, not a shared host. And no container was observed restarting in
place, so "a restart changes it" is still inference from the platform's restart
being a fresh instance. The confirming check is whether
`/proc/sys/kernel/random/boot_id` is namespaced for this runtime; the
`6.18.5-fc-v18` kernel suggests a per-session microVM, which would make the claim
true for a reason not yet measured.

STANDARD VERIFICATION BLOCK. **Measured at `5eb43d6`, PRE-BUMP** -- named
because three rows move once this cut's own edits land, and an unlabelled block
makes a reader diagnose drift that is not there (CLAUDE.md section 2a: measure
after the last edit; 2b: a finding is about a commit, say which one). All exit
codes captured unpiped:

    venv/bin/python -V                          Python 3.12.3          exit=0
    bulk_downloader.__version__                 3.66.882               exit=0
    check_requirements requirements.txt                                exit=0
    check_requirements requirements-test.txt                           exit=0
    check_requirements requirements-cloak.txt                          exit=0
    check_requirements requirements-optional.txt                       exit=0
    check_requirements requirements-dev.txt     pyinstaller nuitka
                                                zstandard              exit=1
    bd-restart-check                            OK boot unchanged      exit=0
    bd-guardcheck                               7 ok, 0 drifted,
                                                0 missing, 0 unpinned  exit=0
    bd-freshcheck --repo-only                   145/145 anchors; 15.30
                                                names 5e87c68; 0 stale exit=0
    bd-env-report-check                         FRESH: version 3.66.882 exit=0

RE-RUN ON THE SHIPPING TREE, because two of those rows are now different and the
difference is this cut, not drift:

    bd-freshcheck --repo-only     150/150 anchors (the 5 new ones are IN this
                                  cut); 15.30 names 5e87c68, HEAD has moved
                                  since                                exit=0
    bd-env-report-check           STALE: version 3.66.882 != tree 3.66.883
                                                                       exit=1
    bd-regen-order --work $PWD                  REGEN COMPLETE          exit=0
    git status --porcelain (after regen)         empty

`requirements-cloak.txt` and `requirements-optional.txt` resolving confirms the
rebuild ran v3.66.880+ `cloud-setup.sh`. `requirements-dev.txt` failing on the
packaging chain is correct and deliberate. `bd-env-report-check` read FRESH at
`5eb43d6` only because this session generated the report at what was then the
tip; **it reads STALE on the shipping tree, and that is the designed steady state
after ANY version bump** -- version-decisive by construction, which CLAUDE.md
section 5 already says not to chase. The pre-bump FRESH is the anomaly here, not
the STALE.

**v3.66.882 IS CAPTURED PASS -- the operator supplied the bundle mid-session, and
it CLOSES the 879-882 gap.** This supersedes this section's own earlier line
saying 879-882 had no box evidence.

    CAPTURE VERDICT: PASS - unit 14618 pass/0 fail/0 error/85 skip;
                            live 36 pass/0 warn/0 fail
    version : 3.66.882          run at : 2026-08-05T21:00:20
    result  : 14703 total | 14618 passed | 0 failed | 0 errors | 85 skipped

| reading | @878 (previous) | @882 (this bundle) |
| --- | --- | --- |
| total / passed / failed / skipped | 14669 / 14584 / 0 / 85 | **14703 / 14618 / 0 / 85** |
| lanes | 1424 + 13160 | **1424 parallel + 13194 serial** = 14618 |
| live tests | -- | **36 pass / 0 warn / 0 fail / 1 n/a** (37 run) |
| graph check-hash | -- | **OK**, pin `7e0c554694558168...`, graph-gate exit 0 |
| `GET /` | -- | 200, 937 bytes |
| routes | -- | 1002 |

**The deployed commit is `5eb43d67c3e6`** -- read from `/api/health`'s
`build.sha`, `source: git`, `version 3.66.882`. That is exactly this session's
baseline commit, so the box and this container were on the same tree.

**AND IT CONFIRMS THE RUNNER DIAGNOSIS ON THE GATE THAT COUNTS.** All **23** of
the suites `bd-band` fails in the container are present in the box's junit output
and **all 23 passed, 0 failures**. The claim "blast radius is the container dev
loop only" is therefore box-evidenced rather than inferred from reading
`capture.sh`.

STILL NO BOX EVIDENCE FOR v3.66.883 ITSELF, and that is expected: the capture ran
at 21:00 against `5eb43d6`, and 883 is docs, register, changelog and a version
string, committed after it. It ships on container lanes plus green CI, which for a
no-code cut is the appropriate evidence.

**A STANDING OPEN ITEM IS REFUTED BY THIS BUNDLE.** 15.28's small item says
`git rev-parse HEAD` is absent from `01_sysinfo.log` "so capture bundles still
cannot self-identify". The first half holds -- checked, no sha anywhere in
`01_sysinfo.log`. The conclusion does not: `09_http_smoke.log` carries
`/api/health`'s `build.sha` = `5eb43d67c3e6`, so **this bundle DOES self-identify,
from a different file.** The narrow convenience ask stands; the stated
consequence was wrong because its denominator was one file. Section 1, in an open
item's rationale.

### 15.32 | Items C and D from the recon -- one closed by audit, one instrumented

**D -- CLOSED, no code, do not re-investigate.** The 25-agent recon flagged two
launcher hooks present on disk and registered in no readable settings file:
`stop-hook-reply-gate.py` and `user-prompt-submit-reply-reminder.py`, both under
the agent home (deliberately written without a `file:line` anchor -- they are
outside this repo and the anchor gate is right to reject one). Audited
2026-08-05 by reading them: both are **Anthropic launcher tooling for Slackbot
v2 sessions**, and their own docstrings explain the registration mystery --
"env-manager registers it in launcher-settings.json only when
CCR_REPLY_STOP_HOOK_REASON is set, and CCR sets that env var only when the
session is Slack-originated". They are absent from settings because this session
is not Slack-originated. Conditional registration working as designed, no
slackbot MCP server configured here, inert. Not BD's, not orphans, not a defect.

**C -- INSTRUMENTED, not yet answered.** Whether a mid-session CONTAINER RESTART
fires a SessionStart event is the residual exposure of @873/@879/@881: if it does
not, a rollback sits unrepaired for the rest of the session and every source
read after it is against a stale tree. The hook's comments and all three suites
ASSUME it does; none establishes it.

The instrument had to be chosen carefully, because the obvious one cannot work.
A hook that logs its own runs cannot record the runs it did not make -- with no
SessionStart, nothing writes anything, and the absent entry is
indistinguishable from a hook that was never installed. Section 0, exactly.

`/proc/sys/kernel/random/boot_id` is regenerated by the kernel on every boot, so
recording it makes "the machine changed under you" a POSITIVE fact. The hook
writes it to `$HOME/.bd_boot_state` on every run; `bd-restart-check` reads it
back.

**And the reading is only unambiguous MID-SESSION.** A mismatch measured at hook
time cannot discriminate: "the restart fired SessionStart" and "the restart
fired nothing, then a new session started later" produce the identical result,
because the hook is running now and the record predates the restart either way.
Checked from inside a running session with no new session begun, a mismatch can
only mean the hook did not run. That is why it is a tool and not another branch
in the hook -- and it is the whole design.

    bd-restart-check   exit 0  no restart since the hook last ran
                       exit 1  RESTARTED and the hook has not run since
                       exit 2  UNEVALUABLE (no record, or no boot_id)

**HOW TO ACTUALLY ANSWER C.** Run `bd-restart-check` when a session has been
running a while, and ESPECIALLY on any suspicion of a restart (the tells: a
`uptime` shorter than the session, a venv that lost packages, a checkout at an
unexpected commit). An exit 1 with no new session started is the answer, and it
is worth recording here when it happens. Until then C is UNKNOWN with an
instrument attached, which is a different state from UNKNOWN.

**A finding from wiring it, worth more than the wiring.** The first live reading
returned `OK ... source=clear` -- and no real session event had produced that.
It was the test suites' own last fixture: `_run_hook` did not override `HOME`,
so every test run overwrote the operator's real state file. A genuine restart
would then have read as OK -- the exact false-clean the tool exists to prevent,
introduced by the tool's own tests. Both copies of the runner (@873 and @879
each keep one) now redirect HOME. Caught by taking a live reading rather than by
trusting the green suite, which is section 10 in one line.

### 15.31 | The cache-rebuild discriminator -- PROTOCOL, run it before any other work

Written 2026-08-05T19:43Z, immediately after the operator pasted the
cache-rebuild marker into the panel's setup-script box. This section exists to
be executed by the NEXT session, not read by this one.

WHY THERE IS A PROTOCOL AT ALL. CLAUDE.md section 5 now records the panel's
cache mechanism -- setup script runs once per cache BUILD, filesystem is
snapshotted, later sessions start from the snapshot with the script skipped,
rebuild fires on a setup-script change / network-host change / ~7 day expiry.
That is the operator's account plus one measurement of this container (the git
reflog's oldest entry was 2026-07-28 18:42:12, the same minute
.claude-env-report.md was generated, which a fresh clone would contradict).

One measurement of one container is not a platform model. The docs say the repo
is re-cloned fresh each session; the reflog here says otherwise. Both cannot be
generally true, and which one holds decides whether the @881 session-start hook
is load-bearing or belt-and-braces.

THE REFERENCE TIME, which is the only thing this section adds that cannot be
re-derived later:

    panel setup-script edited (rebuild trigger)   2026-08-05T19:43Z
    origin/main tip at that moment               7e5e6e5
    snapshot in force BEFORE the trigger         2026-07-28T18:42Z

RECORD THESE FOUR, BEFORE ANY OTHER WORK, IN THE FIRST NEW SESSION:

    git log --oneline -1                                  # HEAD sha
    git rev-list --count HEAD..origin/main                # how far behind
    git reflog --date=iso | tail -1                       # reflog-OLDEST
    # and: which hook blocks appeared at session start --
    #   REPAIRED / *** STALE BASE *** / *** REPAIR FAILED *** / none

HOW TO READ THEM:

  * reflog-oldest ~= the rebuild time (after 2026-08-05T19:43Z), and the
    operator's post-touch commit PRESENT
        -> the snapshot carries .git. Snapshot-carried repo is the steady
           state, the @881 hook is MANDATORY, and a stale base recurs every
           cache cycle. Expect the hook to fire on the FOLLOWING cycle, not
           this fresh one.
  * reflog-oldest ~= this session's own start time
        -> 2026-07-28 was a one-off; the docs' fresh-clone model holds and the
           hook is a safety net rather than the mechanism.

  Either way the hook's behaviour is already correct and loud. The
  discriminator decides which platform model goes into CLAUDE.md section 5 as
  MEASURED rather than inferred -- and section 1 is the reason that distinction
  is worth a whole session's protocol.

EXPECT `none` FROM THE HOOK ON THE DISCRIMINATOR SESSION ITSELF. The rebuild
provisions from a current clone, so the checkout should already be at the tip. A
REPAIRED or STALE BASE block appearing on that first session would mean the
rebuild did not do what the docs say -- which is a useful reading, not a bug in
the hook.

FREEZE. The operator froze new-session starts until this protocol has been run
and its readings recorded, because an accidental session consumes the rebuild
and the discriminator loses its subject. Resuming a session never rebuilds, so
resumed work is safe; starting a NEW one is what spends the trigger.

### 15.30 | Session close 2026-08-05 at 5e87c68 (v3.66.878 tip), carried by v3.66.879 -- SUPERSEDES 15.29's open set

STATE: twelve cuts merged this session, #170-#180 plus this one.

  7a11f2f (#170) 868  a band tool minted a green PASS for a file that does not exist
  e6a3bf6 (#171) 869  the mirror gate ran in no band, for any file
  95da0e3 (#172) 870  the mirror gate saw 255 of 258, and the floor said fine
  c3ee855 (#173) 871  four tools, one defect wearing four hats
  bd67460 (#174) 872  the concurrent-writer guard was stillborn
  ada4a17 (#175) 873  the reverted checkout repairs itself when that is lossless
  8498558 (#176) 874  a readiness run in flight was indistinguishable from a failure
  a17e961 (#177) 875  an interrupted mutation battery left the mutant on disk
  d4fa476 (#178) 876  the band tool pinned its suites at a browser pool not there
  d9e580b (#179) 877  a gate certified 2568 files by reading one of them
  5e87c68 (#180) 878  the operator shell tools ran against a dead sandbox, exit 0
  (this cut)     879  the provision trigger could see one fifth of the damage

BOX EVIDENCE: FIVE captures, all PASS, arithmetic reconciling exactly. The last
is **v3.66.878, captured 2026-08-05T17:53** -- so 876, 877 and 878 are now
verified on the gate that counts. **v3.66.879 has NO box evidence: container
lanes only.** Deltas were verified by counting net new test functions per cut,
not assumed.

  capture @878  14669 total / 14584 passed / 0 failed / 85 skipped
                delta from @875 is +8/+8 with skips UNCHANGED at 85, and the
                net new test functions per cut are 876=3, 877=1, 878=4 = 8.
                Exact. Skips holding still is the half that matters: it says
                nothing quietly stopped running to make room.
                Lanes reconcile too -- 1424 parallel + 13160 serial = 14584
                passed, +85 skipped = 14669 total.
                graph check-hash OK against the pin; live 36 pass/0 warn/0 fail
                exit 0; GET / 200 and /api/health reporting sha 5e87c6800954 /
                version 3.66.878, so the running PROCESS matches the tree and
                not merely the checkout. Routes 1002.

**What 879 changes is exactly what a capture would exercise** -- the SessionStart
hook and the provisioner -- and its reconverge path is proven only against a
FAKE provisioner in tests. The trigger logic and all three refusal cases are
mutation-constrained (5 mutants, 5 caught), but the hand-over to the real
33-step cloud-setup.sh runs for the first time whenever the container next
actually reverts. Treat that path as unexercised until it is observed.

  capture @867  14610 total / 14525 passed / 0 failed / 85 skipped
  capture @870  14624 / 14539 / 0 / 85     (+14)
  capture @872  14647 / 14562 / 0 / 85     (+23)
  capture @875  14661 / 14576 / 0 / 85     (+14)

---

THE FINDING WORTH KEEPING: THE PROVISION TRIGGER WAS A GATE THAT COULD SEE ONE
FIFTH OF THE DAMAGE.

The operator reported three failures recurring across sessions -- the checkout
rolling back to a stale commit, the venv losing packages, the env report
asserting about a tree 60 versions old. v3.66.873 fixed the first and the other
two kept happening. That is the tell that a diagnosis is incomplete, and it was
worth more than the fix: three symptoms with one trigger event.

`.claude/hooks/session-start.sh` decided whether to provision by asking
`tools/check_requirements.py` whether every requirement NAME resolves. An image
reversion breaks five things -- the checkout, venv package VERSIONS,
frontend/dist, `__pycache__`, and `.claude-env-report.md` -- and four are
structurally invisible to a name-resolution check. So "is a repair needed?" was
answered by an instrument that could see one fifth of what breaks, and it
answered no while the session ran on a reverted image.

Section 0, inside the fix written for section 0. The repair converged the TREE
and nothing derived from it -- a container-side replay of the pre-v3.66.848
deploy gaps, which `scripts/deploy.sh` already enumerates and closes on the box.

---

WHAT A 25-AGENT RECON RETURNED, AND WHAT IT KILLED.

Six probes, adversarial refutation on every claimed-real finding, then a
completeness critic. 18 claimed real, **16 survived, 2 refuted**. Pinned at
5e87c68. The value was concentrated in the parts that said "no":

**Refuted -- do not resurrect without new evidence.**

  * "The repair WIDENS staleness by fast-forwarding the tree while leaving the
    gitignored report behind." Measured false in the direction claimed.
  * "SESSION_CARRY's top provenance block claims 3.66.818 while content runs to
    3.66.867, so a reader obeying the header discards fresher content." The
    header block does say that; the refutation established the reader-harm claim
    did not follow. **The stale header is real and is fixed by this entry.**

**Four proposed fixes would have REPRODUCED THE SHAPE OF THE DEFECT.** This is
why the refutation pass earns its cost -- at least two would have shipped:

  1. "Check the revision directories playwright expects" for the browser probe.
     `/opt/pw-browsers` holds chromium-1194/1228 AND
     chromium_headless_shell-1194/1228, and `launch(headless=True)` executes the
     headless SHELL. That is CLAUDE.md section 0 bullet 4 verbatim -- the recorded
     instance of this exact error, re-proposed as its own fix.
  2. "On env-report-check exit 1/2, re-run cloud-setup.sh." `__version__` bumps
     with every cut while the container does not change, so the report is
     GUARANTEED stale after every merge. A gate that fires on identity rather
     than content is the inverse defect, and a gate that cries wolf gets
     switched off.
  3. "Append a hand-dated provenance note recording the OPEN list's commit." A
     hand-maintained date is itself a document that goes stale silently -- the
     shape the refuted finding above documents biting in this same file.
  4. "Refuse the repair on detached HEAD." Milder and stated as a trade-off:
     section 2b instructs agents to `git checkout --detach FETCH_HEAD` before
     measuring, so this narrows the denominator to exclude a routine state.
     **Taken anyway, deliberately** -- a reverted image comes back on the branch
     it was built from, so requiring `main` costs the repair nothing, and the
     alternative silently destroys a deliberate position.

**Three failure classes nobody had probed:**

  * **Venv version/specifier drift is invisible to every automated gate.**
    `tools/check_requirements.py:82-90` calls `version(name)` and DISCARDS the
    result -- specifiers are never compared -- and it is the sole instrument in
    all three recovery paths (session-start.sh, cloud-setup.sh, deploy.sh). A
    reverted image can restore correct NAMES at wrong VERSIONS and every gate
    reports OK. **STILL OPEN.** Partially mitigated by this cut only because
    `pip install -r` re-converges to specifiers when it runs at all.
  * The lossless repair converged the tree and nothing derived from it. CLOSED
    by this cut.
  * **The launcher Stop hook's ADVICE reproduces two recorded git-hygiene
    failures.** the launcher's `stop-hook-git-check.sh` (under the
    agent home, NOT this repo -- deliberately written without a `file:line`
    anchor, because an anchor to an untracked path can never resolve and the
    doc-anchor gate is right to reject it) falls back to
    `origin/HEAD` when `origin/<branch>` does not resolve -- exactly the state of
    a surviving local branch after squash-merge + auto-delete + prune -- then
    tells the agent to push the pre-squash commits. That is the mechanism that
    produced duplicate PR #146. It lives OUTSIDE the repo (`/root/.claude/`), so
    no gate here can reach it. **OPEN, and not repo-fixable.**

---

FOUR PROSE-VS-CODE CONFLATIONS IN ONE SESSION. THE PATTERN IS NOW THE FINDING.

Every one was an assertion that could not tell a comment from executable text,
and every one was written by someone who had just read section 0:

  1. @876: a test forbade a literal that its own explanatory comment contained.
  2. @878: a comment quoted the dead interpreter path and tripped the test
     written in the same commit.
  3. @878 fixup: the comment explaining why `RELEASE_WORK` is unprefixed spelled
     the prefixed name in order to say it had been removed --
     `config_surface_inventory.py:_scan_shell_env` regexes the WHOLE file with
     no comment stripping, so the comment re-entered the ledger and failed the
     gate the rename had just fixed. **CI caught this, not review.**
  4. @879: the assertion that `requirements-test.txt` is graded read it out of
     the comment explaining the fix, so a mutant grading only the core manifest
     stayed green. **bd-mutate caught this, not review.**

THE RULE: in this repo a comment is INSIDE the denominator of every gate that
reads source text. Explaining a removal by naming the removed thing recreates
it. Assert over comment-stripped source, and cite the mechanism rather than the
literal -- the same discipline section 7 already states for secrets.

---

ENVIRONMENT FACTS A FRESH SESSION WILL OTHERWISE RE-DERIVE OR GET WRONG.

  * **The cloud panel env box is CORRECT and matches section 5** -- all five
    vars verified against a screenshot 2026-08-05. Not a suspect.
  * **The panel's setup script is byte-identical to `scripts/cloud-bootstrap.sh`**
    -- `diff` exit 0, 79 lines both. The fork this file warns about has NOT
    recurred. Note the limit of that evidence: it compares the operator's paste
    to the repo, so it proves the paste matches, and no gate can read the panel.
  * **`.claude-env-report.md` is STALE after EVERY cut, by design.**
    `bd-env-report-check` treats the VERSION as decisive and `__version__` bumps
    every merge, so exit 1 is the steady state, not a signal. Do not chase it,
    and do not wire it to trigger reprovisioning.
  * **`requirements-dev.txt` does not resolve in the container** (pyinstaller,
    nuitka, zstandard) and that is DELIBERATE -- it carries the packaging chain,
    which is why neither the hook nor cloud-setup.sh installs it. CI installs it
    for the postgres job only. Not a defect; do not "fix" it.
  * A stray gitignored `downloader_history.db` sits at the repo root, invisible
    to `git status` via `.gitignore:20`. With `BD_INSTALL_DIR` unset, any ad-hoc
    probe importing `bulk_downloader.db` from the repo root connects to it --
    the section 5 trap. Set `BD_INSTALL_DIR` to a tmpdir in every probe.

---

OPEN SET, superseding 15.29. Re-derive each from source before working it
(section 1) -- these were written at v3.66.879 and cuts land after registers.

  * **7a -- retirement completion.** Three tools retired before v3.66.858
    (`bd-reconcile`, `bd-tracker-recon`, `bd-deploy-manifest`) still exist as
    tracked runnable `project-knowledge/` files; the three `*_stays_retired`
    gates glob `*.py *.sh` and the survivors are EXTENSIONLESS, so the gates
    cannot see them. **Needs its over-sensitivity spec reworked first** -- as
    specified it turns three gates red on four LIVE tools.
  * **7b -- BLOCKED on the operator** naming the twelve retired tools.
    Unrecoverable from the tracked tree; retiring the wrong tool is not
    repairable, so this is CANNOT-EVALUATE, not merely unknown.
  * **Item 9 -- `capture.sh` commit identity.** Needs explicit go: release gate.
  * **Venv specifier drift** (above). No gate can see it.
  * **The launcher Stop hook's advice** (above). Outside the repo.

### 15.29 | Mid-session record 2026-08-04 at 6296b06 (v3.66.867) -- SUPERSEDES 15.28's open set

NOT a close section, deliberately. The session is still running, and 15.28
records what happens when a close names a tip written before its own merge.
Titled without the word "close" so bd-freshcheck's check_session_close_tip
keeps grading 15.28 rather than a claim that is not yet true.

STATE, measured: main 6296b06, version 3.66.867, working tree clean, `main` the
only local branch. Seven merges this session:

  a2245ca (#162) 861  the box capture's four failures -- three causes, all mine
  b3e138e (#163) 862  pyflakes was DECLARED where the deploy path cannot read
                      it; requirements-test.txt split out of requirements-dev
  e63b5b9 (#164) 863  bd-scrub-proof called an archive holding a
                      password-manager export SAFE TO SHARE
  35bab4c (#165) 864  bd-docstale read line 1 three times, not lines 1-3
  f4dcea1 (#166) 865  a reverted container reports a perfectly healthy tree
  c533f66 (#167) 866  that hook stopped reimplementing the provisioner
  6296b06 (#168) 867  the destructive-route gate asserted over 0 of 16 routes

BOX EVIDENCE, and the arithmetic reconciles at each step:

  capture @862  14591 total / 14506 passed / 0 failed / 85 skipped, live 36/0/0
  capture @866  14608 total / 14523 passed / 0 failed / 85 skipped, live 36/0/0
                delta +17/+17 with skips UNCHANGED -- 15 tests from 863 and 2
                from 864, so nothing quietly stopped running. Both captures
                verified the running PROCESS via /api/health sha, not just the
                tree. Graph check-hash OK on both.
  band @867     139 passed on the box. 867 touches no application code (one
                test file plus the version bump), so a derived band is real
                evidence rather than a shortcut -- stated because "band, not
                capture" is normally the wrong answer.

---

THE FINDING WORTH KEEPING: A `.redacted` FILENAME AND A SCRUB MANIFEST BOTH
CERTIFIED FILES THAT CARRIED SECRETS.

Census of every wacz under ~ (1658 files, bd-scrub-proof on each, read-only):

  root                      SAFE   SECRET   rate
  BulkDownloader 4          1118    197     15.0%
  BulkDownloader 1           216     22      9.2%
  BulkDownloader (live)       64      5      7.2%
  bd-archive-2026-08          28      4     12.5%
  BulkDownloader 2             4      0     clean
  TOTAL                     1430    228     13.8%

Four of the contaminated files sat in a directory named `from_scrub_manifest/`.
Twenty-two were in B1 -- the corpus 15.10 told the operator to KEEP, on the
strength of those files being redacted. They were not.

CAUSE: bd-wacz-scrub, bd-scrub-proof and bd-share-safe each carried their own
TEXT_EXT allowlist -- three sets, no two identical, none containing `.warc`,
which is where a WACZ's payload lives. Every one reported "verified clean" over
a denominator that excluded the subject. Fixed at v3.66.859 by switching to
content sniffing (sec.should_scan); this census is that defect's blast radius,
measured after the fact.

REMEDIATED: 228 scrubbed, each proved clean twice (the tool's own check, then
an INDEPENDENT bd-scrub-proof run -- the `.redacted` files were also produced
by a tool that verified its own work), then the dirty originals deleted only
where a proven-clean sibling existed. Re-census: 1658 SAFE, 0 SECRET, total
unchanged at 1658, which is the arithmetic proof that the 228 were replaced
1:1 rather than lost.

THE PORTABLE LESSON, since the counts will go stale: there was NO predictor.
Not the date, not the path, not the `auth.` subdomain, not the `.redacted`
label. A 2026-06-20 capture was clean, 06-29 dirty, 07-21 clean, 07-28 clean.
Every file had to be proven individually, and the slow approach was the only
correct one.

A SECOND CLASS THE SAME CENSUS COULD NOT SEE, closed at v3.66.863. A
password-manager export is a binary container that IS the credential store
rather than one that contains a secret. looks_binary() rightly refuses to regex
it, the member lands in `binary_skipped`, and binary_skipped never touched
`safe`. bd-scrub-proof returned exit 0, "SAFE TO SHARE", for an archive holding
"Proton Pass_export_<date>_<n>.xlsx" -- a real file that sat unencrypted on the
box from 2026-07-19 to 2026-08-04, which 15.10 flagged and nobody actioned. Now
destroyed. Note bd-share-safe needed its OWN fix: it does not call prove(), so
repairing the shared library left the tool that WRITES the handed-over bundle
still copying the export in at exit 0.

---

CLOSED ON BOX EVIDENCE, all three previously unanswerable from a container:

  site_name (15.22)  72 of 72 seeded history rows carry the marker in BOTH the
      URL and site_name. Cut #7's clear will delete all 72 and exit 0, not find
      nothing and exit 4. The concern was real and does not obtain here.
  BD_HOME vs install dir  CLOSED, and the register had it mis-framed.
      gui_parity_inventory.py's --outdir defaults to the RELATIVE "reports", so
      it resolves against the CWD; BD_HOME never enters it. Both sides run from
      the same checkout. It was a working-directory question, not an env-var one.
  Audit #3 (AI warm)  NOT REAL. The readiness service takes ~108s, retries
      internally, and converges with BOTH models resident on the Tesla T4.
      MECHANISM, so this is not closed on a lucky sample: attempt 1 is EXPECTED
      to fail -- it races Ollama's socket and the GPU driver. A reader sampling
      the state file during those 108s sees ollama_unreachable / gpu false /
      models pending and cannot distinguish it from a terminal failure. That is
      a real if minor defect (ai_boot_readiness.json has no in-flight marker)
      and it is what produced a WRONG "reproduced" verdict mid-session before
      the journal showed the run had not finished.

ITEM 18's WORKTREE HALF IS CLOSED. 12 worktrees -> 0, 8 local branches -> 1,
eight artifacts archived under ~/branch-archive (two .patch, four .tracked.diff,
two more .patch). Every discard was either proven merged by lowercase `git
branch -d` or archived first. Two branches whose work had reached main by
re-implementation rather than by merge were format-patch'd before -D, because
`git cherry` matches by patch-id and cannot see a re-implementation.

---

CORRECTIONS TO FIGURES THIS REGISTER ASSERTED. Each was true when written and
each was inherited as current:

  "18 trees, 6 prunable"  WRONG IN BOTH NUMBERS. Measured: 12 registered
      worktrees, and `git worktree prune -v` collected NOTHING -- prune only
      unregisters worktrees whose directory is GONE, and every directory
      existed. prune was never the right verb.
  "533 RAW wacz (may carry session material)"  Wrong scope and wrong emphasis.
      722 of 1661 under ~ are raw, and RAW WAS THE SAFER HALF: the redacted
      population was contaminated at 10% while carrying a name that asserted
      safety.
  "166 bd-* tools remain prose-only"  184 of 239, measured. The 166 predates
      four separate fixes to the ratchet's predicate.

---

PROCESS, earned the hard way:

  AN INTERRUPTED bd-mutate DOES NOT RESTORE. A battery killed by a 2-minute
      tool timeout left `len(hits) > 99` in the tree where `> 1` belonged.
      Happened twice this session. After any interrupted battery, grep for the
      mutant text before trusting a test result -- the sha256 restore only runs
      on the exit path.
  THE CLOUD CONTAINER REVERTS TO ITS BASE IMAGE ON RESTART. Measured: uptime
      2h29m against a session hours older, venv/bin/python dated 2026-07-28.
      Three times in one session the git checkout reappeared at an old commit,
      and once a source read against that stale tree produced a confidently
      WRONG conclusion about a fix that was present on main. lxml and cssselect
      "vanishing" is the same event. v3.66.865/866's SessionStart hook makes it
      announce itself; before that, a reverted container looked healthy.
  MUTATION TESTING FOUND TWO UNCONSTRAINED ASSERTIONS A GREEN BAND COULD NOT.
      Both in v3.66.867, both the same shape: a branch guarding a condition the
      live data never produces (zero missing, zero ambiguous names), so
      mutating it is a no-op. The band was green at every step.

---

OPEN, superseding 15.28's list:

  Batch A   bd-parband names a verdict about a suite it never ran (a bad path
      falls through to a broad run and the result is attributed to the missing
      suite), plus `.bd_last_band.json` has no gitignore rule. VERIFIED still
      open at 6296b06.
  Item 7    test_pk_mirrors_do_not_drift does not fire. DEMONSTRATED this
      session: a band passed 261 with all four project-knowledge mirrors stale,
      while bd-pk-mirror --check caught them. Two known defects -- it calls
      pytest.fail(), which the custom runner stubs without .fail, and its
      SOURCE_DIRS loop breaks on first match so tools/ copies are never
      compared.
  Batch B   four of five done? NO -- ONE of five. bd-docstale shipped at
      v3.66.864. bd-opv, bd-env-report-check, bd-equiv and bd-fullsuite remain.
  Item 3    the 12-tool retirement, ~20 references across 7 surviving tools.
      Tool budget sits at exactly 239/239, so retiring one is the only way to
      create headroom without spending the ratchet.
  bd-state  still held behind tools/build_session_pack.py:128.
  Item 6    bd-band's /home/claude paths. test_contracts gives 4 passed/10
      failed under bd-band and 14 under pytest; origin/main reproduces it, so
      it is pre-existing. Needs root-causing before it is a cut.
  NEW, found this session and not previously filed:
      - the credential-file patterns exist in bdtools_sec (v3.66.863) but the
        archive inventory that MISSED the Proton Pass export is untracked, so a
        fresh sweep still cannot see that class;
      - ai_boot_readiness.json has no in-flight marker (above);
      - CLAUDE.md section 6 owes a line about interrupted bd-mutate.
  ARCHIVE SEQUENCE, the operator half of item 18 and the only one with an
      ordering constraint: decide B4's 91 `.db` beside 90 `.db-journal`
      (operator chose recover-then-evaluate; none is a clean backup as it sits),
      purge the rebuildable bulk (kit packs, Windows venvs, PyInstaller dists,
      571 `.old` files), THEN consolidate into one verified bundle. Consolidation
      is materially safer than it was this morning because the keep-set is now
      provably clean rather than labelled clean.
