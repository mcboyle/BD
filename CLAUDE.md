# CLAUDE.md — operating contract for BulkDownloader

You are working on **BulkDownloader (BD)**: a self-hosted Flask + Playwright +
React/TypeScript SPA batch video downloader. Single developer/operator (Matt),
single deployment target (headless host `stash`).

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

Then regenerate `PIN_INDEX` (`venv/bin/python tools/build_pin_index.py`) and
`grep -rnE '__version__ *== *"3\.66\.' tests/` — do not assume the pin list has
stayed at one entry.

CHANGELOG entries must be **ASCII-only**; an emoji trips a gate on the box.

---

## 4 | Band rules (which tests to run)

**A denominator change has the blast radius of the denominator, not the diff.**
Band every test touching the changed module — derive it with `grep -rl`, don't
guess.

- Route change → band **both** `test_route_index_in_sync` **and**
  `test_route_map_invariant`; re-freeze `route_map_baseline._BASELINE_SHA` via
  `tools/route_map_snapshot.py` with `PYTHONPATH=tree:/tmp/prestaged`.
  **Not** `env -u PYTHONPATH` — that strips the work tree from `sys.path`,
  produces an empty file, and silently overwrites the baseline.
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
- Never run the whole `tests/` directory locally — known hangers
  (`test_perf_lab.py`, `test_v3_66_146_nav_guard`).
- Always capture exit codes **unpiped**: `cmd > /tmp/out 2>&1; echo "exit=$?"`.
  Piping masks the exit code, and this bites even when you know about it.
- `pgrep -f "<cmd>"` **matches its own wrapper**. Never read it as "still
  running" — check `/proc/<pid>` or a written exit marker.
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
- **`pip check` cannot see an uninstalled requirement.** Its denominator is what
  *is* installed. `runtime deps OK` was reported with `beautifulsoup4` and
  `pytest-xdist` both absent. To ask whether requirements are satisfied, parse
  `requirements.txt` and resolve each name.

**Provisioning a test host.** `scripts/provision_test_host.sh` is the one command
that takes a fresh Ubuntu 24.04 box to a green `./capture.sh`: system tier,
`install_linux.sh`, Xvfb on `:99`, parity-inventory regen. Run it instead of
hand-installing typelibs.

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

---

## 7 | What git changes, and what it does not

This repository is new. Most of BD's tooling was built in a world with **no
version control**, where the only reference was the previously shipped zip.

**Now genuinely easier:** diffing work against a baseline (`git diff` replaces
zip comparison), tracking deletions, checkpointing (branches replace snapshot
tarballs), bisecting a regression, and reviewing a cut before it ships.

**Unchanged — do not assume git fixed these:**

- **The deploy path is still an overlay.** `unzip -o` overwrites and adds but
  **never deletes**. A file deleted in a cut keeps living on the box, and graph
  gates glob the disk, so the orphan trips the baseline. On any cut that deletes
  a file, run the deploy-manifest step *before* the overlay. Git tracking the
  deletion does not delete it on the target.
- **Gitignored generated artifacts still go stale, and `git clean -fd` will not
  remove them** -- that needs `-x`. `reports/gui_parity_inventory.json` is
  gitignored and build-time generated, so a stale copy left by an earlier overlay
  reads as parity drift and fails the **entire** suite: observed at v3.66.818 as
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
- **Band derivation is still required.** Tests are not derivable from a diff;
  blast radius follows the denominator.
- **The guard SHAs still apply.** Git history is not authorization.
- **The box is still the gate.** Sandbox green is necessary, not sufficient.

---

## 8 | Layout

```
bulk_downloader/     561 .py — the application
tests/               1073 test files (+ corpus/ and fixtures/ assets)
tools/               216 .py — build, graph, regen, and gate scripts
frontend/            React/TS SPA (its own node_modules, not committed)
toolchain/bin/       ~249 bd-* operator tools (the "bdsuite")
project-knowledge/   365 durable docs, schemas, and cards
docs/repo/           environment and layout references
```

**Two populations share the word "tools":** `tools/*.py` (216) and the
`toolchain/bin` bd-* suite (~249). Several checks disagree only because they
count different ones. This is a denominator mismatch, not rot.

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
