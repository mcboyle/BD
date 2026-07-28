# Next-session bootstrap -- BulkDownloader (BD)

ASCII-only. Paste the "PROMPT" block below into a fresh session, OR just run the
preflight one-liner. This gets the environment installed, configured, and proven
ready before any real work -- so the session does not rediscover what earlier
ones already paid for. Companion: `LESSONS_LEARNED_v3_66_811.md`.

---

## PROMPT (paste this into a new session)

> You are working on BulkDownloader (BD) under the CLAUDE.md operating contract.
> Before anything else, run the preflight below and PASTE its output; do not
> trust any test result until it is green. Then read
> `project-knowledge/LESSONS_LEARNED_v3_66_811.md` -- it lists the traps that
> read green in the sandbox and fail on the box. Key standing facts:
>
> - `./venv` MUST be Python 3.12 (the box/CI interpreter). If it is 3.11,
>   rebuild it before any graph/parity regen (a 3.12-only f-string silently
>   drops edges under 3.11).
> - `reports/gui_parity_inventory.{json,md}` is GENERATED + gitignored, and
>   `git clean -fd` will NOT remove a stale copy (that needs `-x`). If you add any
>   `tools/*.py`, regenerate it: `./venv/bin/python tools/gui_parity_inventory.py`.
>   `capture.sh`, `install_linux.sh` and `scripts/provision_test_host.sh`
>   regenerate it too, but a stale copy left in your work tree still reads as
>   parity drift and fails the ENTIRE suite (observed at v3.66.818 as a single
>   failure, `only-regen=['pytest_capture_results']`, on an otherwise-green
>   13389-pass run). Deploy moves files; it does not refresh this artifact.
> - A merged PR is finished: restart the branch from `origin/main` for
>   follow-up work; never stack on merged history or rewrite the merge commit.
> - Sanctioned targets only; DETECT never SOLVE; secret-hygiene; every mutation
>   inside the envelope (snapshot -> act -> revert -> ledger).
>
> Tell me what you find, then wait for a directive.

---

## PREFLIGHT (run this first; paste the output)

```bash
cd /home/user/BD 2>/dev/null || cd "$(git rev-parse --show-toplevel 2>/dev/null)"
{
  echo "== interpreter =="
  echo "system python3 : $(python3 --version 2>&1)"
  echo "system 3.12    : $(python3.12 --version 2>&1 || echo absent)"
  echo "repo ./venv    : $(./venv/bin/python --version 2>&1 || echo 'no ./venv')"
  echo "== git identity (must be Claude / noreply@anthropic.com) =="
  echo "user.name  : $(git config user.name)"
  echo "user.email : $(git config user.email)"
  echo "== branch / sync =="
  echo "branch : $(git branch --show-current)  head: $(git rev-parse --short HEAD)"
  git fetch -q origin main 2>/dev/null && echo "behind main by: $(git rev-list --count HEAD..origin/main 2>/dev/null) commits"
  echo "== env report from cloud-setup.sh =="
  # Date the report BEFORE reading it. It is gitignored, survives `git clean
  # -fd`, and is written once per provisioning run -- one was found seven days
  # old asserting v3.66.811 against a v3.66.818 tree while its rows were being
  # read as current. FRESH=0, STALE=1, UNKNOWN=2 (and unknown is not a pass).
  venv/bin/python toolchain/bin/bd-env-report-check --tree "$PWD"
  if [ $? -eq 0 ]; then
    grep -E "VERDICT|FAIL|WARN" .claude-env-report.md | head
  else
    echo "(report not current for this tree -- rows above are NOT evidence about it)"
  fi
} 2>&1
```

## FIX-UPS (only if the preflight flags them)

```bash
# venv on the wrong interpreter (3.11) -> rebuild on 3.12:
rm -rf venv && python3.12 -m venv venv \
  && ./venv/bin/pip install -q -r requirements.txt \
  && ./venv/bin/pip install -q "pytest>=7.0,<9.0" pyflakes

# git identity not set (prevents the Unverified-commit stop-hook):
git config --global user.email noreply@anthropic.com && git config --global user.name Claude

# frontend toolchain missing (vitest/tsc) -> reinstall WITHOUT NODE_ENV=production:
( cd frontend && npm ci --no-audit --no-fund )

# full provisioning from scratch (browsers, audit venv, sec tools, ~10-14 min):
bash scripts/cloud-setup.sh
```

## READY-TO-GO COMMANDS (once preflight is green)

```bash
PY=./venv/bin/python            # 3.12 == the box; use it for EVERYTHING

# static gates (no live box needed):
$PY tools/opv_guide_lint.py project-knowledge/OPV_COMPLETION_GUIDE_v3_66_810.md
$PY tools/config_surface_inventory.py --check          # open_runtime_tunable must be 0
for t in build_endpoint_catalog build_function_index dependency_graph \
         check_route_counts capture_model_golden; do $PY tools/$t.py --check; echo "$t=$?"; done

# regen the gitignored inventory after adding a tools/*.py (deploy does NOT refresh it):
$PY tools/gui_parity_inventory.py

# band a change (derive it, do not guess) -- never run the whole tests/ dir:
grep -rl "<changed_module>" tests/        # then pytest only those files

# live-test an endpoint (service OOMs across bash calls -> do it in ONE call):
#   start wired service + poll readiness + probe + teardown, all in a single bash block.
```

## THINGS THAT WERE ALREADY DONE as of v3.66.811 (do not redo)

This list is a snapshot, NOT current state -- much more has landed since. For what
is done now, read `CHANGELOG.md` and the merged PR list, not this section.

- `scripts/cloud-setup.sh` installs the lot and now (a) sets the git identity and
  (b) rebuilds `./venv` on 3.12 if a stale 3.11 venv is present.
- `.env.example` documents the interpreter-parity rule and python hygiene.
- OPV orchestration shipped: `scripts/bd-opv-run.sh` (tiered CLI runner),
  `scripts/bd-opv-check.sh` + `scripts/bd-stash-report.sh` (diagnostics),
  `tools/opv_guide_lint.py` (guide validator), and both agent execution prompts
  (`OPV_COWORK_EXECUTION_PROMPT.txt`, `OPV_EXECUTION_PROMPT_CODEX.txt`).
- The v3.66.811 render-gap fix landed (Settings.tsx controls +
  render-verifying RED test); PR #3 is merged into main.
