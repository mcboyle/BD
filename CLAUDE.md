# CLAUDE.md — operating contract for BulkDownloader

You are working on **BulkDownloader (BD)**: a self-hosted Flask + Playwright +
React/TypeScript SPA batch video downloader. Single developer/operator (Matt),
single deployment target: headless host **`test4`** — verified from `uname` and
the `mboyle@test4` prompt in a capture, deploying to
`/home/mboyle/BulkDownloader`.

Older prose here, in `project-knowledge/`, and in the SDD reports calls that box
`stash`. That is a saved PuTTY session name, not a hostname
(`.superpowers/sdd/wacz-processing-report.md` records the session alongside the
same `mboyle` user). **Same machine — there is no second box.** Two sessions
have now spent time treating this as an open question; it is not one. Nothing in
any `.py` or `.sh` resolves, connects to, or branches on a hostname, so the name
is documentation only: the deploy is `git fetch` + `git reset --hard` run *on*
the box.

Read this file fully before your first edit. It encodes rules that were learned
by breaking things, not by preference. Where a rule looks arbitrary, it is
usually load-bearing and the reason is stated.

---

## 0 | The one rule that generates most of the others

**A gate that cannot see the thing it is asked about reports OK — and that is
worse than no gate.**

A check asserts over a denominator that structurally excludes its subject, so it
reports clean: truthfully, and uselessly. Real instances from this codebase:

- A band tool didn't count `.tsx`/`.ts` as source, so it reported "changed
  source (0)" on a real frontend cut.
- `test_gui_parity` asserted `all(key.startswith("BD_"))` over a scan that
  matched on that prefix — so unprefixed vars were invisible, and the test
  certified none existed.
- A deploy manifest reported "no orphans" while examining only source files
  rather than the full tree.
- A capture check inspected the chromium build while `launch(headless=True)`
  actually executes the headless *shell*.

**Fix pattern, every time:** make the denominator contain the subject; derive
reachability rather than assert it; and make a check that cannot verify *say so*
— **unknown is a third state, and it fails.**

**The inverse is equally damaging: a gate that fires on identity.** A manifest
pin once hashed bytes that included a wall-clock `generated` field, so an
unchanged tree "changed" every run. Two sessions nearly reconciled a diff that
did not exist. A gate that cries wolf gets switched off, so over-sensitivity is a
soundness bug, not a safe default. Attest over **content**, not bytes.

---

## 1 | The method rule

**Documents go stale silently and are then read as authority.**

Every figure taken from a register, tracker, changelog, or grep has at some point
been wrong. Every figure obtained by *running the tool* was right.

- **Verify-then-act.** Before working any queued item, re-derive its status from
  source. Historically ~half of a stale register's "open" items are already
  closed or mis-scoped.
- **Grep is not a denominator.** For import/symbol questions use AST. A grep for
  playwright importers was wrong in *both* directions simultaneously — two files
  matched the string with no import node, and two real importers were invisible.
- **AST is not automatically better.** An AST re-derivation returned 13 instead
  of 12 because the predicate was `'playwright' in name`, which also matches
  `playwright_stealth` — a different distribution. **The instrument fixes the
  denominator; the predicate fixes the subject. Say which you used.**
- Numbers that move (tool counts, coupling, retirement pools) must be measured at
  decision time, never quoted — **including from this file.**

---

## 2 | Release discipline (non-negotiable)

1. **RED-first TDD.** Tests proven failing on pristine source *before*
   implementation. A test that passes the moment you write it has proven nothing.
2. **Seven SHA-pinned guard files** must stay byte-identical unless the operator
   explicitly declares a new SHA:

   | File | sha256 (16) |
   | --- | --- |
   | `bulk_downloader/extraction_core.py` | `5b6248a5c9e664ab` |
   | `bulk_downloader/session_capture.py` | `547d70c95cde9377` |
   | `bulk_downloader/dom_capture.py` | `0559903d0b159162` |
   | `bulk_downloader/dom_recorder.py` | `1657d0a0e39917ae` |
   | `bulk_downloader/capture_bodies.py` | `6c7f5c9a87510cca` |
   | `tools/capture_session.py` | `27be68b965689317` |
   | `tools/build_release.py` | `be25241eb867b85a` |

   *(Pinned at v3.66.805. Re-derive with `bd-guardcheck`, which reads
   `guards.json` — the single source of truth, hashed from the files. A
   `BD-GATE-UNRUNNABLE` message or exit 2 means the pins were **not** verified;
   do not proceed on it. Do not trust this table after the next cut.)*

   Until v3.66.818 `bd-guardcheck` reported `0 ok, 0 drifted, 7 missing` and
   **exited 0** on a clean tree — it could not see the files it certifies, and
   said so with a success code. A zero-in-every-bucket summary is a failure
   signal, not a pass.
3. **One feature per cut.** Clean blast radius beats a convenient batch.
4. **No speculative code** before reading the relevant source.
5. **The band is absolute.** A band failure means fix the tree or fix the
   environment — never explain it away. It has caught real design regressions
   that the feature's own test could not see.

Before packaging a change for review, regenerate all tracked artifacts from the
repository root and keep the resulting diffs in the review package:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

This command does not re-freeze intent baselines; declaration flags remain
explicit operator decisions.

---

## 3 | Version bump = three edits, together

Never bump one without the others:

1. `bulk_downloader/__init__.py` → `__version__`
2. `tests/test_settings_center_slice4.py` → the `assert __version__ == "…"` pin
3. `CHANGELOG.md` → **ASCII-only** entry, prepended, anchored on the *previous*
   `## v…` header

Then regenerate `PIN_INDEX` (`venv/bin/python tools/build_pin_index.py`) and read
the `form == "version"` entries out of `PIN_INDEX.json` — do not assume the pin
list has stayed at one entry.

Do **not** reach for `grep -rnE '__version__ *== *"3\.66\.' tests/` here. It
returns five hits of which exactly one is a real pin
(`tests/test_settings_center_slice4.py:200`); the other four are fixture string
literals inside `test_release_hygiene_gates.py` and
`test_scan_version_pins_fixture.py`, plus `__pycache__` binary matches.
`build_pin_index.py` uses AST precisely so those fixtures are structurally
invisible to it. This is section 1's rule applied to this file: the instrument
fixes the denominator, the predicate fixes the subject.

CHANGELOG entries must be **ASCII-only**; an emoji trips a gate on the box.

---

## 4 | Band rules (which tests to run)

**A denominator change has the blast radius of the denominator, not the diff.**
Band every test touching the changed module — derive it with `grep -rl`, don't
guess.

- Route change → band **both** `test_route_index_in_sync` **and**
  `test_route_map_invariant`; re-freeze the baseline, then update
  `_BASELINE_SHA` at `tests/test_route_map_invariant.py:35` to the new file's
  sha256:

  ```bash
  PYTHONPATH="$PWD" venv/bin/python tools/route_map_snapshot.py \
      > tests/route_map_baseline.txt
  sha256sum tests/route_map_baseline.txt
  ```

  The redirect is required — `route_map_snapshot.py` writes to **stdout only**,
  it does not write the baseline file itself. `_BASELINE_SHA` is a constant in
  the test module, not an attribute of a `route_map_baseline` module; the `.txt`
  beside it is plain data. And **not** `env -u PYTHONPATH` — that strips the
  work tree from `sys.path`, produces an empty file, and silently overwrites the
  baseline. (Older copies of this rule said `PYTHONPATH=tree:/tmp/prestaged`;
  `/tmp/prestaged` does not exist here, and `venv` carries the deps.)
- Deleting a config key → band `grep "<key>" tests/`.
- Wiring a frontend control **is** a `ROUTE_INDEX` change (`spa_wired` flips).
  Regen order is **gui_parity before ROUTE_INDEX**.
- Any **new import edge** requires re-freezing the import-graph baseline in the
  **same** cut: `venv/bin/python tools/decomp/import_graph_gate.py --update`, and band
  `tests/test_import_graph_no_new_edges.py`. This is separate from regenerating
  `DEPENDENCY_GRAPH.json`.
- A `data_layer` route add must update **both** `test_wave2_backlog` **and**
  `test_v3_66_302_gui_parity_reconcile`.

**Naming trap:** `test_spa_wired_join_is_faithful` is a *function inside*
`tests/test_route_index_in_sync.py`, not a file. Passing it as a path makes the
runner fall back to a broad run → timeout → aborted cut. Band the **file**.

**Leak trap:** `test_phases_195_199` leaks `BD_INSTALL_DIR` — never co-band it
with `test_cut8_schedules`.

---

## 5 | Environment traps

- **`test_v3_43_80_modules::test_all_modules_import` is environmental, not a
  regression.** It false-fails a bare band with `tray_app: Namespace Gtk not
  available`; it passes 49/49 with GTK typelibs and `DISPLAY=:99`. Fix the
  environment (`scripts/provision_test_host.sh`, below), then re-band — do not
  chase it as a code defect.
- Three Python resolution paths exist (system / prestaged / service venv) and
  they carry **different playwright versions**. `import playwright` succeeding at
  a bare prompt proves nothing about what BD runs.
- Two Playwright browser pools exist with **different chromium revisions**.
  Behaviour differing inside vs outside the env wrapper may be a different
  browser build, not a different code path.
- Never run the whole `tests/` directory locally. `test_perf_lab.py` is the
  recorded hanger. A second was recorded as `test_v3_66_146_nav_guard` — **no
  file of that name exists**, in any variant, and both real `146` files
  (`test_v3_66_146_runtime_gate.py`, `test_v3_66_146_detection_safety.py`) pass
  in under a second. Treat the second hanger as unidentified until someone
  re-measures it; do not band or exclude a phantom.
- Always capture exit codes **unpiped**: `cmd > /tmp/out 2>&1; echo "exit=$?"`.
  Piping masks the exit code, and this bites even when you know about it.
- `pgrep -f "<cmd>"` **matches its own wrapper**. Never read it as "still
  running" — check `/proc/<pid>` or a written exit marker. This bites hardest
  inside a **wait loop**: `until ! pgrep -f 'pytest …'; do sleep 10; done` never
  exits, because the loop's own command line contains the pattern. A session
  reported a test lane as "running" for ten minutes when it had never started.
  Reaching for `pkill -f "until ! pgrep"` to clean that up matched *its* own
  command line and killed the shell. Wait on a **written marker or the job's
  own exit**, never on a process-table match.
- **Change one variable at a time, or the comparison is worthless.** To decide
  whether a failure was yours, run the baseline **in the same directory**.
  A session ran the pristine lane in a detached `git worktree` and got two
  spurious signals: a test that "only failed with the change" had in fact never
  *run* in the worktree (it self-skips when it cannot reach a service worker),
  and another failed only in the baseline because a probe could not find the
  real checkout from `/tmp`. The arithmetic gave it away — total collected
  differed by exactly the new file's test count while skips differed by five.
  If the totals do not reconcile, you changed more than you think.
- **The interpreter is `venv/bin/python`, never bare `python3`.** In the cloud
  container `/usr/local/bin/python3` is **3.11 without the project
  dependencies**, while `venv` is 3.12 (the box/CI interpreter). There is no
  `.venv` here — a command naming it exits 127 and the caller silently falls
  back to 3.11. That happened: a full test band was measured on 3.11 and
  reported seven failures that did not exist.
- **The Claude Code panel runs a thin bootstrap, not the provisioner.**
  `scripts/cloud-bootstrap.sh` is the text pasted into the panel; it locates the
  checkout and `exec`s `scripts/cloud-setup.sh` from it, so every fix to the
  provisioner reaches the next session with nothing to re-paste. Before this,
  the panel held a private copy that had forked three commits and 91 lines while
  13 tests certified the repo copy that never executed. If the env report's step
  labels do not match `scripts/cloud-setup.sh`, the panel has forked again.

  The bootstrap is pinned at **under 80 lines** by
  `test_bootstrap_stays_short`, and it sits at 79. That is not slack to spend:
  every line added there is a line that leaves the repo's sight. **Put new
  provisioning logic in `scripts/cloud-setup.sh`, never in the panel text.**

  The panel's **environment box** carries the session settings. These are not
  in any file, so they are the one thing a fresh session cannot re-derive —
  set them there:

  ```
  BD_HOME=/tmp/bd_home
  BD_REPO=/home/user/BD
  BD_SKIP_ARCHB=1
  BD_SKIP_BROWSERS=1
  BD_DISABLE_KEEPALIVE=1
  ```

  `BD_REPO` is the first probe rung, so setting it makes checkout location
  deterministic instead of relying on the glob. `BD_HOME` keeps app state out
  of the repo. The two `BD_SKIP_*` flags drop kasmvnc and the browser download
  — browsers are preinstalled at `PLAYWRIGHT_BROWSERS_PATH`, and the
  provisioner says which of "skipped but present" and "skipped and absent" is
  true rather than assuming the worst. `BD_DISABLE_KEEPALIVE` stops background
  threads outliving a test run.

  Note what these do **not** buy: `BD_HOME` does not protect
  `~/.config/bulk-downloader`, which resolves from `$HOME`, not `BD_HOME`.
  That is `tests/conftest.py`'s path-keyed store guard's job.
- **`pip check` cannot see an uninstalled requirement.** Its denominator is what
  *is* installed. `runtime deps OK` was reported with `beautifulsoup4` and
  `pytest-xdist` both absent. To ask whether requirements are satisfied, parse
  `requirements.txt` and resolve each name.

**Provisioning a test host.** `scripts/provision_test_host.sh` is the one command
that takes a fresh Ubuntu 24.04 box to a green `./capture.sh`: system tier,
`install_linux.sh`, Xvfb on `:99`, parity-inventory regen, graph content pin.
Run it instead of hand-installing typelibs.

The graph pin is the newest step and the least obvious. `capture.sh` step [2b]
compares the rebuilt source graph against a pin under `/var/lib/`, **outside the
repo** — so `git reset --hard` never delivers it and a fresh box has none. With
`BD_REQUIRE_GRAPH_HASH` unset (default `0`) the MISSING branch prints
`UNKNOWN -- optional check not armed` and **returns 0**, so the capture goes
green with the graph never checked. The provisioner now arms it and then
re-runs the gate's own `--check-hash` **as the invoking user** — writing a pin
proves a write, not that `capture.sh` can read and match it.

Re-pin by hand after any source change, or step [2b] reports drift and
`capture_verdict.py` turns that stage exit into a whole-capture FAIL:

```bash
GDB=$(mktemp -d)/KNOWLEDGE_GRAPH.db
venv/bin/python tools/l0_extract.py --root "$PWD" --db "$GDB"
sudo venv/bin/python tools/graph_build.py --db "$GDB" \
    --hash-pin /var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256 \
    --write-hash
```

Only the second command takes `sudo`: `--write-hash` sets `projection_mode`
false and returns before emitting any projection, so it writes the pin and
nothing else. Running the whole block elevated is the section 5 footgun —
`l0_extract` would build under `HOME=/root`.

`scripts/lib/system_deps.sh` is the **single source of truth** for system
packages; `install_linux.sh`, `scripts/provision_test_host.sh` and
`scripts/cloud-setup.sh` all source it. Never inline a package list again --
three copies is a denominator that drifts, and the copy nobody updated is the one
the box runs (S0/S8).

---

## 6 | Any tool that rewrites source must verify after writing

Parse the result (`ast.parse`), confirm the expected content is present, and
**restore the original + abort** if malformed. A bump tool once corrupted a test
pin via an over-escaped `re.sub` replacement (`r'\\1"'` emits a *literal* `\1`)
and shipped a `SyntaxError`. Use a lambda replacement instead.

**`ast.parse` is not name resolution.** A file referencing an undefined name
parses fine. Check the names too — import the module.

**The applied-check: use length arithmetic.** Every edit or mutation asserts
`src.count(old) == 1` first — an anchor matching 442 sites and applied with
`count=1` rewrites whichever site `re.subn` reaches first, and the resulting
verdict is evidence about a different location. But proving the replacement
*landed* is where two plausible checks are each wrong half the time:

| check | fails silently when |
| --- | --- |
| `new in after` | `new` already occurs elsewhere — trivially true, a no-op reads as applied |
| `after.count(old) == 0` | **append-style** (`old` is a substring of `new`) |
| `after.count(new) == count(new) + 1` | **shrink-style** (`new` is a substring of `old`) |

All three were hit, the last two inside a single mutation battery. Use instead:

```python
assert src.count(old) == 1
after = src.replace(old, new, 1)
assert after != src and len(after) == len(src) - len(old) + len(new)
```

Length arithmetic is exact for one replacement of a unique anchor and cannot be
fooled by substring overlap in **either** direction.

**A mutant that does not parse is INVALID, not caught, and not escaped.**
Deleting a line can orphan an `except:` clause; the runner then sees a
collection error, no named guard flips, and the row reads as an escape. Validate
the mutant (`ast.parse`, or `bash -n` for shell) *before* judging it, and report
"invalid" as its own outcome.

---

## 7 | What git changes, and what it does not

This repository is new. Most of BD's tooling was built in a world with **no
version control**, where the only reference was the previously shipped zip.

**Now genuinely easier:** diffing work against a baseline (`git diff` replaces
zip comparison), tracking deletions, checkpointing (branches replace snapshot
tarballs), bisecting a regression, and reviewing a cut before it ships.

**Changed — the deploy path is now git.** The box updates with
`git fetch origin main` + `git reset --hard origin/main` + a service restart.
There is no zip overlay and no zip fallback. Deletions therefore propagate
natively, and the orphan class that `tools/deploy_manifest.py` and
`bd-deploy-manifest` exist to detect can no longer occur. Two consequences are
**not** improvements: `git reset --hard` has no equivalent of `unzip -x`, so it
discards operator live-edits that the overlay was configured to preserve (see
`GATE_AUTHORITY.md` section C); and it moves files without making the running
system match them, which is the first item below.

**Unchanged — do not assume git fixed these:**

- **A deploy moves files. It does not make the running system match them.**
  Four gaps survive the move from `unzip -o` to `git reset --hard`, because not
  one of them was ever a property of the overlay: `__pycache__/*.pyc` are not
  cleared (the v3.66.161 stale-bytecode footgun is unchanged); gitignored
  generated artifacts are not refreshed; the service is not restarted; and
  `frontend/dist/` is not delivered **at all** — it holds zero tracked files and
  is gitignored, so a missing or stale bundle is a silent 503 from
  `bulk_downloader/app.py`. Rebuild it with `cd frontend && npm ci && npm run
  build` whenever SPA source changed. Treat this as a condition to re-derive,
  not a list to memorise: anything generated-and-ignored joins the set.

  **On the deploy box this is still yours to do.** In a *cloud container* it is
  not: `scripts/cloud-setup.sh` now runs `npm run build` and then reads
  `frontend/dist/index.html` back, failing the provision if the bundle is
  absent — exit 0 from `vite build` is not the property anyone depends on.
  Two tests fail without it and neither names the cause:
  `test_v3_66_790_nuitka_config::test_data_dirs_all_exist_in_tree` ("declared
  data dir does not exist: frontend/dist") and
  `test_phase1_root_flip::test_missing_asset_is_404_not_spa_html` (503). They
  were the last two failures a session had to wave away as environmental, so a
  future occurrence is now real signal rather than noise.
- **Gitignored generated artifacts still go stale, and `git clean -fd` will not
  remove them** -- that needs `-x`. `reports/gui_parity_inventory.json` is
  gitignored and build-time generated, so a stale copy left by an earlier deploy
  or provisioning run reads as parity drift and fails the **entire** suite:
  observed at v3.66.818 as
  a single failure, `only-regen=['pytest_capture_results']`, on an
  otherwise-green 13389-pass run. The durable fix is to **regenerate, not
  delete** -- `install_linux.sh`, `capture.sh` and
  `scripts/provision_test_host.sh` all regenerate it now.
- **`.claude-env-report.md` is in this class**, and it is worse because its own
  header instructs the reader to trust it. It is gitignored, survives
  `git clean -fd`, and is written once per provisioning run — one was found
  seven days old asserting v3.66.811 against a v3.66.818 tree. Check its
  `generated_against_version` / `generated_against_commit` header before
  believing any row in it. UNKNOWN provenance is not the same as current.
  `venv/bin/python toolchain/bin/bd-env-report-check` answers this for you:
  FRESH (0), STALE (1), UNKNOWN (2). In a container provisioned before
  v3.66.818 it returns 2, because a report that cannot be dated is
  indistinguishable from one written against another tree.
- **Band derivation is still required.** Tests are not derivable from a diff;
  blast radius follows the denominator.
- **The guard SHAs still apply.** Git history is not authorization.
- **The box is still the gate.** Sandbox green is necessary, not sufficient.

**The squash-merge branch trap.** PRs here merge with **squash**, which writes a
*new* commit on `main`. Your topic branch does not follow it, so immediately
after a merge `origin/<branch>` still points at the pre-squash commit and the
branch and `main` have **no common tip** even though their content is
identical. The next ordinary push is rejected as non-fast-forward, and the
tempting reflex — `--force` — is the one that can discard someone else's work.

The safe sequence, every time, is: prove the content is already merged, then
force **with lease**.

```bash
git fetch origin main
git diff --stat origin/main origin/<branch>     # MUST be empty
git reset --hard origin/main                    # continue from the merged tip
# ... new work, commit ...
git push -u origin <branch> --force-with-lease
```

The two-dot `git diff` is the load-bearing step: empty means the remote branch
carries nothing `main` lacks, so replacing it loses nothing. **Non-empty means
stop** — something is on that branch that was never merged, and forcing would
delete it. `--force-with-lease` is not optional; it refuses if the remote moved
since your last fetch, which is exactly the case where `--force` would
overwrite a collaborator.

**GitHub's own merge commits are not yours to re-author.** A squash lands as
`GitHub <noreply@github.com>`, signed with GitHub's web-flow key — it shows as
**Verified** on github.com and reports `%G? == E` locally only because that key
is not in the container's keyring. Never `--amend --reset-author` one: it is
published history on the default branch, and the deploy host updates with
`git reset --hard origin/main`, so rewriting it moves the tree under a running
deployment.

---

## 8 | Layout

```
bulk_downloader/     the application (.py)
tests/               test files (+ corpus/ and fixtures/ assets)
tools/               build, graph, regen, and gate scripts (.py)
frontend/            React/TS SPA (its own node_modules, not committed)
toolchain/bin/       bd-* operator tools (the "bdsuite")
project-knowledge/   durable docs, schemas, and cards
docs/repo/           environment and layout references
```

Sizes are deliberately not written here. Every count in this block has been
wrong at least once, and section 1 applies to this file too: measure at
decision time.

```bash
find bulk_downloader tools -name '*.py' | wc -l    # per directory as needed
find tests -name 'test_*.py' | wc -l
ls toolchain/bin/bd-* | wc -l
```

**`CODEX_HANDOFF.md` is a second agent-facing doc, and this file outranks it.**
It records a parallel Codex agent's 34-task program — a task ledger, design
decisions, and where Analysis Task 4 paused. It states no environment facts by
design: interpreter, deploy model, band rules and guard pins live here, and
`tests/test_codex_handoff_defers_to_claude_md.py` fails if it starts restating
them. It once shipped 14 commands against a dot-prefixed `venv` that does not
exist here, while this file said otherwise, and a session followed the wrong
one and reported seven failures that were not real.
Treat its task statuses as a register — re-derive before acting.

**Two populations share the word "tools":** `tools/**/*.py` and the
`toolchain/bin` bd-* suite. They are **disjoint** populations with different
members, and several checks disagree only because they count different ones —
a denominator mismatch, not rot. Their totals have at times been far apart and
at other times identical, so never read equal counts as evidence the two sets
are the same, or unequal counts as evidence something rotted. Re-derive both,
then ask which one the check in front of you means.

---

## 9 | Working with Matt

- Terse directives ("go", "cut", "1", or a bare file upload) mean **full
  authorization within the established scope**.
- "hold" / "wait" means stop immediately.
- Read-only analysis and planning are free. **Runtime, build, version, guard, and
  release changes need explicit per-task authorization.**
- Report honestly over optimistically. Results first, no narration, no
  aspirational documentation. If something is unverified, say which part.
- He deploys and runs the full suite himself. Do not claim a state on the box
  that you have not been told.

---

## 10 | Before you claim anything works

Run the check and paste the real output. "Should work", "looks correct", and
"the tests should pass" are not verification. If you could not run it, say so and
say why — an honest unknown is worth more than a confident guess, and this
project has been burned specifically by numbers nobody measured being written
down and then inherited as truth.
