<!-- verified-against: v3.66.850 -->
# #6 — Manifest-exclusion ruleset (rationale; the sets themselves are derived)

What a clean release zip is allowed to omit — and therefore what should never
count as "missing." The builder (`tools/build_release.py`) and the verifier
(`dev_suite.zip_manifest_check`) share one definition; if either drifts, the
build's manifest gate fails.

## Where the definition lives

It is **not** one location any more, and it is not in a module called
`dev_suite.py` — that module became a package:

| set | source |
| --- | --- |
| `_MANIFEST_EXCLUDE_DIRS` | `bulk_downloader/dev_suite/_common.py:250` |
| `_MANIFEST_EXCLUDE_SUFFIXES` | `bulk_downloader/dev_suite/release_lint.py:229` |
| `_MANIFEST_EXCLUDE_NAMES` | `bulk_downloader/dev_suite/release_lint.py:285` |
| `_MANIFEST_EXCLUDE_PATHS` | `bulk_downloader/dev_suite/release_lint.py:406` |
| `_MANIFEST_REQUIRED_PRESENT` | `bulk_downloader/dev_suite/release_lint.py:457` |

All five are re-exported from the package, so read them through it rather than
chasing the split.

## Authoritative re-read

```bash
venv/bin/python -c "
import bulk_downloader.dev_suite as d
for n in ('_MANIFEST_EXCLUDE_DIRS','_MANIFEST_EXCLUDE_SUFFIXES',
          '_MANIFEST_EXCLUDE_NAMES','_MANIFEST_EXCLUDE_PATHS',
          '_MANIFEST_REQUIRED_PRESENT'):
    print(n, '=', sorted(getattr(d, n)))
"
```

**This document deliberately no longer copies the five sets out.** It used to,
and every copy rotted: at v3.66.849 the hand-written lists carried 11 of 13
exclude-dirs, 4 of 6 suffixes and 11 of 29 names, while the header claimed
`verified-against: v3.66.276` and the body described itself as "the 160 state
verbatim" — roughly 600 releases behind a tree that read it as authoritative.
A prose copy of a source constant has no mechanism that can keep it true, so
the copy is the defect, not the drift. Run the command; it cannot go stale.

(`bd-doc-truth` only detects *file-path* claims that stop resolving. It found
the dead `dev_suite.py` reference above at v3.66.850 — it could never have seen
the wrong set contents, which is why deriving beats re-copying.)

## Why these exclusions exist — the part source does not tell you

- **`state/`** is excluded because importing `bulk_downloader.app` during the
  endpoint-catalog gate spins up the heartbeat thread, which writes
  `state/heartbeat.json`. Without the exclusion the zip ships the developer's
  last heartbeat.
- **`.zip`** is excluded so the just-written release artifact sitting in the
  tree is not flagged "missing from zip."
- **`_MANIFEST_EXCLUDE_PATHS`** is a third mechanism alongside DIRS/SUFFIXES/
  NAMES: exclusion by **full relative path**, for when a basename appears in
  several places with different ship intent. The root `app_config.json` is
  path-excluded (any app boot in the work tree writes it, and it can carry a
  generated secret) while `frontend/app_config.json`, the SPA twin,
  intentionally still ships. A basename rule cannot express that split.
- **`_MANIFEST_REQUIRED_PRESENT`** is the inverse list — files whose *absence*
  is the failure, mostly the Windows `.bat` entry points and the test-fixture
  corpora.

## Nuances that matter

- **`logs/` is NOT a dir-exclude** — but `.log` files inside it are
  suffix-excluded, so an empty or `.log`-only `logs/` contributes nothing. A
  non-`.log` file dropped in `logs/` *would* ship.
- **`screenshots/` and `live_recordings/` are both dir-excludes.** An earlier
  revision of this file stated that `live_recordings/` was *not* in the dir
  list and that its contents were merely caught by name/suffix. That was true
  once and is now false — an example of why the sets are derived above.
- Matching is **dir + suffix + exact-name + exact-path**, not globs. Match
  accordingly when scanning.
