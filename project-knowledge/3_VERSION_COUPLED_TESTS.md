<!-- verified-against: v3.66.276 (deliberately version-AGNOSTIC; do not re-pin a "current" number here) -->
# #3 — Version-coupled tests (fix BEFORE the first build)

Some tests hard-pin `__version__`. After bumping `bulk_downloader/__init__.py` line 26, these fail
unless updated **in the same change** — catch them before building, not after a failed build.

> **This card is intentionally version-agnostic.** It used to name a "current pin: N" and got
> hand-bumped every release (a maintenance smell + a staleness trap). The authoritative answer is
> always the grep below against the live tree — never a number baked into this doc.

## Find them yourself each release (authoritative)
```
# any test asserting a specific version string:
grep -rnE '__version__ *== *"3\.66\.|assert .*3\.66\.[0-9]+' tests/ | grep -v '\.pyc'
# the CHANGELOG-coupled contract test also checks the current version is present:
grep -rn 'version' tests/test_contracts.py | head
```
Through the 27x line the **only** hard `__version__` pin in the band has been
`tests/test_settings_center_slice4.py` (asserts `__version__ == "<current>"`, ~line 200) — but
**do not trust that to stay a single entry**; the grep is the source of truth. (Historical false
positives the grep surfaces but which are NOT app pins: `test_build_release_f02.py` fixture
literals like `built_version == "3.66.215"` / `live_version == "3.66.999"`; and
`test_release_hygiene_gates.py` synthetic `_mktree` strings like `3.66.168/169`. The
`APP VERSION-PIN SCAN` in `build_release` flags these as informational — leave them.)

## Rule of thumb (the 3-part bump)
`test_contracts.py` requires the **current** version present in `CHANGELOG.md` with matching health
— so treat "bump version" as a single 3-part edit that lands together:
1. `bulk_downloader/__init__.py` line 26 (`__version__`),
2. `CHANGELOG.md` top `## vX.Y.Z` (anchor the `str_replace` on the **previous** version's `## `
   header and re-emit it),
3. any pinned test the grep finds (currently `test_settings_center_slice4.py`).
4. `tests/test_scan_version_pins_fixture.py` -- exercises the pin-SCANNER itself on a synthetic fixture (`3.66.100`/`3.66.280`); it is NOT a real version pin and must NOT be bumped when `__version__` changes. Named here so a session does not mistake its fixture strings for stale pins (the grep `__version__ == "3\.66\.` matches it).
