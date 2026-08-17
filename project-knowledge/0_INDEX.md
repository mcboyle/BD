<!-- verified-against: v3.66.464 -->
# BulkDownloader -- build/audit/sandbox reference cards

Focused reference distilled from the release sessions. **Split for the
static/version.zip model** (historical; the checkout is now the handoff):

## Durable -- live in static project knowledge (`reference/`)

| # | Card | Value |
|---|------|-------|
| 2 | `2_RELEASE_TEST_BAND.md` | the proof band + the `--skip-tests`-then-run-the-band builder trap |
| 3 | `3_VERSION_COUPLED_TESTS.md` | tests that hard-pin `__version__`; find them before building |
| 4 | `4_RUNTIME_ARTIFACTS_AND_NAMELIST_RULE.md` | run-dir diffs false-alarm; audit the namelist |
| 6 | `6_MANIFEST_EXCLUSION_RULES.md` | the exact `dev_suite` dir/suffix/name exclusion sets |
| 7 | `7_VPN_CONFIG_API_SURFACE.md` | `vpn_config` has no `get_config()` -> empty VPN summary (safe) |
| 8 | `8_BUILD_RELEASE_CHEATSHEET.md` | `build_release.py` gates, flags, exit codes, regen invocations |
| 9 | `9_SETTINGS_CENTER_SAFETY_SPEC.md` | expected route/secret/PUT invariants |
| 10 | `10_SANDBOX_SHELL_PREFLIGHT.md` | dash-shell + custom-runner hard constraints |


## Named cards (static; not numbered)

| Card | Value |
|------|-------|
| `GATE_AUTHORITY.md` | the 7 guards, the 4 in-sync regen targets + G12, and the deploy-excluded files -- one source |
| `TOUCHED_FILE_TO_TEST.md` | source surface -> the tests that MUST be banded when it changes (band-coverage map) |
| `ADVANCED_PROJECT_KNOWLEDGE.md` | the consolidated @464-validated "what bites and why" reference (9 sections A-I) |
| `DANGER_MAPv2.md` | full numbered runner/db/session load-bearing-invariant registry (backs ADVANCED section I) |
| `GLOSSARY.md` | the project jargon decode ring (capture/recognizer/release/decomp/process terms) |
| `ARCHITECTURE_MAP.md` | source topology: capture->template->download pipeline + 169 blueprints by domain + runner mixins + guard boundary |
| `KB_SYNC_WORKFLOW.md` | how `bd-kb-sync` keeps the static set fresh (manifest loop; static=cache, pack=live edge) |

## Version-specific -- travel in the per-session `version.zip`

| # | Card | Refresh |
|---|------|---------|
| 1 | `REFCARD_1_artifact_provenance.md` | new every release (canonical zip hash, superseded copies) |
| 5 | `REFCARD_5_delta_spec.md` | new every release (modified/new/removed delta + base hash) |

Cards 1 and 5 are **not** in this static folder by design -- regenerate them at
session close and ship in the version.zip.
