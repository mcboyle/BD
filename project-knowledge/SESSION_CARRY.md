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

    generated            2026-07-29
    against version      3.66.818   (bulk_downloader/__init__.py:33)
    against branch       claude/bulkdownloader-handoff-68xjky @ 35f09b7
    against origin/main  d38590d
    working tree         clean at time of writing

If the tree has moved past `d38590d`, treat every finding below as a claim to
re-derive, not a fact to inherit. A document that cannot be dated is
indistinguishable from one written against another tree.

---

## 1 | Where the work stands

Five pull requests merged to `main` this session, in order:

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

## 10 | If the operator grants access to the box

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
