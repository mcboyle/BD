# decomp_tools — monolith-decomposition program helpers (built on v3.66.392)

Four sandbox-staged tools that fill the gaps the decomp program kit's own generators
don't reach. **Pure tooling — nothing here is landed in the work tree or cut.** All
RED-first, GREEN against the live 392 tree.

| tool | what it removes | needs |
|---|---|---|
| `tools/decomp_deletions.py` | the **overlay-can't-delete** footgun: a `.py`→package cut leaves the old `X.py` shadowing the new `X/` package. Diffs new build vs deployed baseline, emits the exact `rm` lines + flags `.py`→package conversions. | stdlib only; zips or dirs |
| `tools/decomp_regen.py` | the **regen-ORDER** failure class (381/382): runs a target's regens in the enforced order (`build_route_index` LAST, `pin ≠ route`, G12 final). `--dry-run` default; `--apply` runs them; **never Vite**. | stdlib only |
| `bin/bd-decomp` (+ `tools/bd_decomp_lib.py`) | the **per-cut dispatch**: one command for the right invariant + baseline + surface-lock + `cross_monolith_graph --check`, per target. `targets` / `baseline <t>` / `check <t>`. | runs under the `bd` env; reaches the kit via `$DECOMP_KIT` |
| `tools/decomp_lint.py` | the **sandbox-invisible structural mistakes**: module-level monolith import (cycle trap), stray `__file__`, depth-suspect `parents[N]`. | AST only; runs on stash |

## Run

```bash
# per-cut invariant ritual (app shown; runner/dev_suite/deep_detect dispatch too)
bd-decomp targets
bd-decomp check app                 # live url_map diff vs the frozen baseline + cross-monolith --check
bd-decomp baseline app              # (re)freeze ONLY at a phase open/close

# what to regen, in order, for a given target's cut (no Vite)
python3 tools/decomp_regen.py dev_suite                 # dry-run plan
python3 tools/decomp_regen.py app --apply --root /home/claude/work

# lint a MOVED module before banding
python3 tools/decomp_lint.py --root /home/claude/work bulk_downloader/dev_suite/<mod>.py

# guard the deploy note when a .py became a package
python3 tools/decomp_deletions.py --new <built.zip> --old <deployed.zip> --prefix bulk_downloader/

# run the tests
for t in tests/test_*.py; do python3 "$t"; done
```

## Verified (this session, v3.66.392)
- Unit tests: 21/21 GREEN (deletions 4, regen 5, lint 5, bd-decomp 7), RED-proven first.
- `bd-decomp check app` → INVARIANT HELD, 944 rules, == kit baseline `d92ccf3d8e05…`; cross-monolith acyclic.
- `decomp_lint bulk_downloader/dev_suite.py` → independently reproduced the kit's 8 `__file__` sites; 0 module-level monolith imports (matches A1: edges are lazy).

## Landing plan (at a cut / session close — gated)
- `tools/*.py` → work-tree `tools/`; `tests/test_*.py` → work-tree `tests/` (adapt to `run_tests.py`: zero-arg fns already; drop the `__main__` runner).
- `bin/bd-decomp` + `tools/bd_decomp_lib.py` → the `version.zip` **`kit/`** overlay so `setup.sh` installs `bd-decomp` on PATH next session.
- `decomp_regen` is best folded into `bd-cut` as a `--no-spa --decomp-target <t>` mode once it's proven in a real cut.
- NOT in the work tree today (keeps `bd-preflight` PASS).
