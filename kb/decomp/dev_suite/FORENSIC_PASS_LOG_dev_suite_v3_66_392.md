# FORENSIC PASS LOG — dev_suite @ v3.66.392

The auditable record of every lens run during the dev_suite decomposition dig. Purpose: (1) **prove the
investigation was exhaustive**; (2) let the **next target** skip settled lenses. `HIT` rows produced
findings (see `DECOMP_FORENSICS_dev_suite_v3_66_392.md` F-numbers); `CLEAN` rows bound the search space.

**Outcome:** ~33 lenses across 2 rounds → **converged**. Material findings: F1 (`__file__`-depth, 8 sites),
F2 (guard imports a private), F3 (inventory undercount → 84 sections), F4 (INV-floor), F11 (dynamic
file-path import compounds F1), F15 (constants need `_common`), F17 (submodule↔sibling name collision). All
non-pure-move edits reduce to three: the F1 `__file__` centralization, the F7 import transform, the F17 rename.

---

## Round 1 (F1–F10)

| # | Lens | Result | Evidence / command |
|---|---|---|---|
| 1 | Duplicate top-level fn names | CLEAN | `grep '^def ' \| sort \| uniq -d` → none |
| 2 | **`__file__`-depth path math** | **HIT → F1** | 8 sites: L623 `_repo_root` `parents[1]`; L1817 `with_name("runner.py")`; L3859/4012/4075/4184/4852/5119 `dirname(__file__)` |
| 3 | Mutable module-level state | HIT → F5 | only `_FIXTURE_SERVERS={}` (L5104); all touchers contiguous L5094–5204 → safe |
| 4 | Import-time side effects | CLEAN | docstring "Nothing here runs at import time"; only consts + empty dict at module level |
| 5 | Module-level `import dev_suite` | CLEAN | none anywhere; all importers import lazily/in-fn → no cycle |
| 6 | **Guard/tool import FORM** | **HIT → F2** | `build_release.py` (guard) `from bulk_downloader.dev_suite import (_manifest_excluded, zip_manifest_check)` — pulls a **private** |
| 7 | Test import forms | CLEAN(→F6) | 6 files use `from bulk_downloader import dev_suite as ds` (module-attr, shim-covered); tier0 also imports sibling `_bat_lint` |
| 8 | Sibling delegations | HIT → F6 | `_bat_lint.py`, `login_templates_data.py` already-extracted siblings dev_suite wraps |
| 9 | Literal/path-grep tests | CLEAN | no test reads `dev_suite.py` by path; `test_inv_tags` uses tree-wide `rglob` |
| 10 | INV-tag floor count | HIT → F4 | `test_inv_tags_not_regressed` floor (tree-wide rglob) |
| 11 | Dynamic/reflective access | CLEAN | no `getattr/vars/dir(dev_suite)` in tree → explicit re-export suffices |
| 12 | Submodule-name vs **fn**-name | CLEAN | no fn named `logs`/`config_tools`/… (NB: missed sibling-modules — see R2 #32) |
| 13 | Coverage map / inventory | **HIT → F3** | `test_t34`–`t40` name tools a digit-only section grep missed → 84 sections, not 68; 89 test files |
| 14 | Single-file machinery assumption | CLEAN | manifest walk enumerates `.py` (expect +~13 namelist); PIN/FUNCTION_INDEX don't ref the path |
| 15 | Public→public cross-section calls | HIT → F8 | only 5, 1 caller each — likely intra-section |
| 16 | `_common` membership (functions) | HIT → F8 | `_repo_root`(9) `_percentile`(8) `_redact`(5) `_collect_cred_refs`(5) `_human_secs`(4) … |

## Round 2 (F11–F18)

| # | Lens | Result | Evidence / command |
|---|---|---|---|
| 17 | `global` statements | CLEAN | `grep '^\s*global '` → none |
| 18 | `__module__`/`__qualname__` in output | CLEAN | `__name__` uses are all `type(e).__name__` (move-invariant) |
| 19 | Module-level threading/async primitives | CLEAN | no `_X = threading.Lock()/Event()` at module level |
| 20 | Decorators on functions | CLEAN | `grep '^@'` → 0 decorated defs |
| 21 | Pre-existing `__all__` | CLEAN | none → shim defines it fresh |
| 22 | dev_suite owns routes/Blueprint | CLEAN | no `@app.route`/`Blueprint(`/`add_url_rule` → routes live in app.py |
| 23 | **Dynamic-import machinery** | **HIT → F11/F12** | L5116 `spec_from_file_location` on `__file__` path (compounds F1); L6051 `sys.modules.get("bulk_downloader.app")`; L6163 `import_module(<str>)` |
| 24 | `inspect.getsource` target | CLEAN(→F13) | L1074/1104 inspect app.py `view`/`hook`, not dev_suite → move-safe |
| 25 | Module-level optional-import guards | CLEAN | no col-0 `try/except ImportError` |
| 26 | Module-level `re.compile` | CLEAN | none |
| 27 | Cross-section **constants** | **HIT → F15** | `_SECRET_KEY_HINTS`(4) + `_BD_ENV_VARS`(2) cross-submodule → `_common`; `_DISPATCH_CHAIN`(9) all §U5 (local) |
| 28 | `import_module` arg resolution | HIT → F18 | arg from `_PROMPT_REGISTRY` → external module-path strings (move-safe) |
| 29 | Hardcoded module-path strings | CLEAN(→F12) | **no `"bulk_downloader.dev_suite"` literal** in tree |
| 30 | Conditional/redefined top-level defs | CLEAN | no `if: def` at module level |
| 31 | **Complete sibling-import set** | HIT → F14/F16 | ~45 distinct `from . import X`; all files exist; §19 already absolute |
| 32 | **Submodule-name vs SIBLING-module** | **HIT → F17** | proposed `maintenance` == `bulk_downloader/maintenance.py` (imported L8171) → rename → `housekeeping` |
| 33 | `import *` (surface pollution) | CLEAN | none → the def list == public `dir()` surface |
| 34 | Checksum-coupled byte literals | CLEAN | `_TEST_PNG_B64` not asserted by any test → safe to relocate |

---

## Round 3 (F19–F21) — consumption & packaging

| # | Lens | Result | Evidence / command |
|---|---|---|---|
| 35 | app.py call-site import FORM (all 125) | CLEAN | 125× `from . import dev_suite as _ds`; **0** direct-name imports |
| 36 | **All-consumer direct PRIVATE imports** | **HIT → F19** | 5 privates: `_manifest_excluded` + `_MANIFEST_EXCLUDE_{DIRS,NAMES,PATHS,SUFFIXES}` |
| 37 | **Consumer reads `ds.__file__`/`__path__`** | **HIT → F20** | `test_u27_security_cluster.py:54` `dirname(abspath(ds.__file__))` — external mirror of F1 |
| 38 | File-count pin tests | CLEAN(→F21) | all relative (`>0`, `>50`, sum-equality), not absolute module-count pins |
| 39 | `__init__`/`dev_tools` → dev_suite cycle | CLEAN | neither imports dev_suite |
| 40 | Module-level `__getattr__`/`__dir__` | CLEAN | none → no PEP-562 shim to replicate |
| 41 | Build enumeration vs recursive walk | CLEAN | `_walk_tree` = `root.rglob("*")` → submodule files ship automatically |
| 42 | conftest dev_suite coupling | CLEAN | `tests/conftest.py` neutral |

**Discovery rate across rounds: 10 → 3 → 2 material items.** Every major lens category now covered.
Further nominal passes return CLEAN. **Investigation closed.**

- **The two lenses that hide** (#2 `__file__`, #9/#10 path/floor tests) ship broken on a green import check.
- **#12 had a blind spot** corrected by #32 (compare against sibling modules, not just functions).
- **#13 (coverage map) found inventory the structural grep missed.**

> Max-verify note (v3.66.392): the "11 INV tags" in F4 is a tree-wide misattribution — dev_suite carries
> **5** (`# INV-001/002/004/005/006`); tree-wide is 6 distinct / 23 occurrences. The 8 `__file__` code
> sites are correct (the L616 mention is a comment). See DECOMPOSITION_PROGRAM_ROADMAP §1.
