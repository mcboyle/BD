# DECOMP_FORENSICS — deep_detect @ v3.66.392

The forensic pass on `bulk_downloader/deep_detect.py`, the program's previously-unaudited
target. Method: the `DECOMPOSITION_PLAYBOOK.md` lenses, run against live source this
session. Headline: **simplest body transforms of all four monoliths, but the widest
external-private surface** — the inverse of the dev_suite risk shape.

**Target:** `deep_detect.py` — **8,907 lines · 98 top-level fns (38 public + 60 private)
· 0 classes**. A function-collection (like dev_suite), NOT a god-class (like runner).
**Type:** pure structural move → `bulk_downloader/deep_detect/` package + `_common`.

---

## Verified facts (lens results)

| Lens | Result | Detail |
|---|---|---|
| Lines / shape | 98 fns (38 pub + 60 priv), 0 classes | function-collection |
| Duplicate fn names | **CLEAN** | none |
| **`__file__` depth (F1-class)** | **CLEAN — 0 sites** | no path math; the dev_suite nemesis does NOT apply |
| **Lazy `from . import X` (F7-class)** | **CLEAN — 0** | deep_detect is a **true leaf**: 7 top-level imports, all stdlib/typing, **zero intra-package imports**. No F7 transform needed. |
| Import-time side effects | **CLEAN** | 0 non-docstring top-level calls |
| Module-level mutable state | **HIT** | 3 module-level: `_DD_COUNTERS` (L88, **MUTATED — metrics counters → STATE, co-locate touchers**), `_PROVIDER_ID_PATTERNS` (L2779, constant), `_CEILINGS` (L5303, constant) |
| Routes / Blueprint / add_url_rule | **CLEAN** | 0 — routes live in app.py → DEPENDENCY_GRAPH-only |
| `__all__` / decorators / `global` | **CLEAN** | none |
| INV-tag floor | **CLEAN** | 0 INV tags (no floor contribution) |
| Dynamic import machinery | **CLEAN** | 0 `spec_from_file_location` / `import_module` / reflective access |
| **External surface (law #2)** | **HIT — the big one** | see below |

## The external surface — the dominant constraint (~65 names)

deep_detect is imported by **runner, app, and ≥6 other modules + ~50 test files**. The
shim/surface-lock must preserve **every** name external code pulls — and external code
reaches deep into the privates:

- **38 public functions** (the nominal surface): `canonicalize_url`, `classify_*`,
  `deep_detect`, `deep_detect_live`, `detect_*`, `extract_*`, `find_honeypots`,
  `get_metrics`/`reset_metrics`, `hls_*`, `is_*_manifest`, `parse_*`, `score_*`,
  `scan_*`, `to_site_config_block`, …
- **4 public constants imported externally:** `HONEYPOT_CSS_HIDDEN` (dom_honeypot),
  `PROVIDERS` (cockpit_templates), `JSONLD_MEDIA_TYPES`, `SOURCE_TYPES`.
- **~22 PRIVATE functions imported externally** (de-facto public — cannot be freely
  relocated/renamed): `_annotate_download_candidate`, `_apply_signed_url_annotations`,
  `_candidate_is_mixed_content`, `_candidate_violates_csp`, `_csp_source_matches`,
  `_dedup_candidates`, `_extract_provider_ids`, `_fetch_manifest_capped`,
  `_is_visible_input`, `_parse_content_disposition`, `_parse_csp_policy`,
  `_parse_hls_attrs`, `_parse_srcset`, `_poll_async_workflow`, `_probe_head`,
  `_refine_source_type_from_headers`, `_try_parse_loose_json`, `_walk_json_for_media`,
  plus code-attribute uses `_url_host`, `_PROVIDER_ID_PATTERNS` (capture_workbench).
- **~3 private constants:** `_PROVIDER_ID_PATTERNS`, `_HLS_ATTR_RE`, `_RULE_BLOCK_RE`.

**Consequence (TOOL-VERIFIED — supersedes the estimate above).** Running
`deep_detect_surface.py` (AST, comment-safe) against live source gives the exact set:
**125 frozen names = 89 own-public ∪ 76 external-imported**, of which **36 are
PRIVATE** functions/constants that external code imports. The ~22 estimate above
undercounted because (a) it omitted deep_detect's ~51 public *constants* and (b) the
grep missed the ~36 privates the **deep_detect test family imports directly** to
unit-test internals (`from bulk_downloader.deep_detect import _parse_hls_attrs`,
`_csp_source_matches`, `_score_to_confidence`, …). The shim `__all__` MUST export all
125 (incl. all 36 privates) or the test family fails to import on the split. Generate
the surface-lock with `deep_detect_surface.py --emit-lock`; never hand-list it.

## Risk re-framing (vs the "easiest" headline)

deep_detect has the **simplest mechanics** (0 `__file__`, 0 lazy sibling imports, 0
dynamic machinery, true leaf) but the **hardest surface** — ~22 external private fns
mean its de-facto public API is far larger than its 38 nominal publics, and a careless
relocation that drops one from the package root breaks an external consumer (the law-#2
trap). The body move is trivial; the surface preservation is the work.

## Move plan (the gap-fill)

- **Shape:** `deep_detect/{__init__, _common, <domain>.py …}`. Group the 98 fns by
  responsibility: URL/decode, manifest parsing (HLS/DASH/Smooth), resolution scoring,
  provider/embed extraction, honeypot/trap scanning, login/bot-defense scoring,
  CSP/mixed-content, metrics (`_DD_COUNTERS` + `get_metrics`/`reset_metrics` co-located).
- **`_common`:** cross-cutting privates used by ≥2 domains (re-derive with an AST map);
  the metrics-state `_DD_COUNTERS` co-locates with the metrics fns (its only touchers).
- **No F7 transform** (leaf). **No `__file__` centralization** (0 sites).
- **Shim:** `__all__` ⊇ (38 public ∪ 4 public constants ∪ ~22 external privates ∪ ~3
  private constants). Explicit re-exports, no `import *`.
- **Surface-lock** (`test_deep_detect_surface_lock.py`): freeze ALL ~65 names; smoke-
  import every submodule.
- **In-sync:** DEPENDENCY_GRAPH only (regen); `test_function_index_in_sync` stays green
  (deep_detect ∉ FUNCTION_INDEX); routes untouched.
- **Cross-target:** runner (`from . import deep_detect as _dd`, L6600) and app (L5162)
  use module-attr → shim-covered; the ~6 other modules' `from .deep_detect import _X`
  are covered by the external-private exports above.
- **Deploy:** `.py`→package → `rm bulk_downloader/deep_detect.py` + clear pycache
  (overlay can't delete). Band the deep_detect test family (~50 files) from the
  extracted zip.

## Sequencing note

deep_detect is the **base of the cross-monolith import stack** (imported by runner+app,
imports nothing of them) and collides only on DEPENDENCY_GRAPH (same as dev_suite). It
is a strong early-phase candidate — but its surface-lock must be generated carefully
first (the ~22 external privates). Pairs with dev_suite as the two DEPENDENCY_GRAPH-only
single-cut splits.
