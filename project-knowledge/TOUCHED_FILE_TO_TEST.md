<!-- verified-against: v3.66.761 -->
# TOUCHED_FILE_TO_TEST — band-coverage map

When a cut touches a source surface, **band the rows below from the EXTRACTED zip** — not just the
new cut's own tests. This exists because the 274 band omitted `test_v3_66_271_gcw2_per_entry_delete.py`
(which exercises the touched `CaptureWorkflow.tsx`), so a stale assertion only surfaced on the
on-stash full suite at 275. A touched file → its band should be a **lookup, not a judgment call.**

Rule: band = (this cut's new tests) ∪ (every row whose surface you touched) ∪ `test_contracts.py`
(always, on any version bump). Re-confirm filenames against `ls tests/` each release; treat this as
the floor, not the ceiling. The on-stash full suite remains the binding gate.

| Touched surface | MUST band (regression) |
|---|---|
| `frontend/src/routes/CaptureWorkflow.tsx` | `test_v3_66_271_gcw2_per_entry_delete`, `test_v3_66_271_gcw2_pick_overlay`, `test_v3_66_272_gcw3_setup_site`, `test_v3_66_273_gcw_download_gate`, `test_v3_66_274_probe_mode`, `test_v3_66_274_gcw_finish_and_watch`, `test_v3_66_274_capture_ux`, `test_gui_parity` |
| `bulk_downloader/element_pick.py` | `test_element_pick_bridge`, `test_element_pick_selector`, `test_capture_pick_api`, `test_inspect_pick`, `test_v3_66_271_gcw2_pick_overlay`, `test_v3_66_274_capture_ux` (RSEL row-group: + the 276 selector tests) |
| `bulk_downloader/runner.py` (download/probe/verdict path) | `test_extraction_core`, `test_extraction_core_characterization`, `test_v3_66_274_probe_mode`, `test_v3_66_273_gcw_download_gate`, `test_v3_66_6_runner_deep_detect`, `test_v3_66_44_runner_gaps`, `test_t49_runner_timing` |
| `bulk_downloader/detect.py` (find_best_download / scoring) | `test_extraction_core`, `test_v3_66_6_runner_deep_detect`, + the 276 RSEL selector/score tests |
| `bulk_downloader/app.py` (routes) | `test_contracts`, `test_gui_parity`, then regen + `tools/check_route_counts.py` (see ARCHITECTURE_MAP safety surfaces) |
| `bulk_downloader/app.py` (`test_extract` body params, no new route) | `test_capture_pick_api`, the relevant `_27x` GCW suites; gui_parity should stay byte-identical (confirm) |
| `bulk_downloader/global_config.py` | `test_v3_66_133_live_config_apply`, `test_settings_center_wiring`, `test_settings_center_slice4/5`; **adding a KEY also requires:** FE-wire it (api-types.ts + settingsSchema.ts + Settings.tsx) + a `config_gui_manifest.json` row, then band `test_v3_66_710_config_denominator`, `test_v3_66_713_unscanned_surfaces`, `test_v3_66_716_decoys_and_shadow`, `test_v3_66_720_oidc_and_ffmpeg`, `test_config_parity_ratchet`, `test_gui_parity` (+`test_v3_66_305_config_danger` if safety-bearing) -- see FG-CONFIG-KEY-ADD-PARITY. **If the key is read via `runtime_flags.num` with a `BD_...` env seed, add a SECOND `config_gui_manifest.json` row keyed by the ENV-VAR NAME** (else the env var surfaces as its own `open_runtime_tunable` and 713/716/720 go RED); config-only keys need one row (v3.66.760). |
| `bulk_downloader/captcha_relay.py` | `test_v3_43_60_captcha_relay`, `test_v3_66_757_takeover_fold`, `test_v3_66_758_takeover_driver`, `test_v3_66_759_takeover_admission`, `test_v3_66_760_takeover_idle_sweep`, `test_v3_66_761_takeover_observability`, `test_headless_default` |
| `bulk_downloader/takeover.py` | `test_v3_66_757_takeover_fold`, `test_v3_66_758_takeover_driver`, `test_v3_66_759_takeover_admission`, `test_v3_66_760_takeover_idle_sweep`, `test_v3_66_761_takeover_observability`; changing `open_channel`/`sse_frames` also bands `test_v3_43_60_captcha_relay` (via the relay wrappers) |
| `bulk_downloader/metrics_prom.py` | `test_v3_66_12_phase1_p05_minimal_metrics`, `test_v3_66_761_takeover_observability`; adding a block that imports a NEW module (e.g. `metrics_prom -> takeover` @761) also requires re-freezing `import_graph_gate` (`--update`) + regen `DEPENDENCY_GRAPH` + band `test_import_graph_no_new_edges` in the SAME cut |
| `bulk_downloader/__init__.py` (version) + `CHANGELOG.md` | `test_contracts` (CHANGELOG/health/semver), `test_settings_center_slice4` (the version pin) |
| `bulk_downloader/capture_scrub_hook.py` / redaction | `test_capture_scrub_hook`, `test_capture_scrub_patches`, `test_capture_redact_nested`, `test_secret_display_never` (stash), `test_ct2_capture_bodies` |
| `tools/capture_session.py` *(GUARD)* | `test_capture_session_nav_resilience`, `test_v3_66_153_capture_finish_sentinel`, `test_v3_66_158_capture_finish_endpoint` + **declare the guard sha change** |
| `bulk_downloader/dom_recorder.py` *(GUARD)* | `test_dom_recorder_asi` (5 static ASI checks) + the Node A/B (`asi_node_ab.js`) + **declare guard sha** |
| `capture.sh` | `test_u45_capture_sh_shipped`; re-run `verify_release --zip` (banner gate) |
| `toolchain/bin/bd-writerec` | `test_v3_66_1194_write_recorder` |
| VPN / killswitch transport | `test_u46_vpn_kill_switch_probe`, `test_t14_vpn_probe_egress` (binding validation is stash) |
| Settings Center routes/secrets | `test_settings_center_slice4/5`, `test_settings_center_wiring`, `test_settings_center_secret_classifier`, `test_put_numeric_range_backstop` |

## Always-on (any cut)
- `test_contracts.py` — version/CHANGELOG/health/semver + route invariants.
- On any **route** change: regen the 4 in-sync targets + `check_route_counts.py` (ARCHITECTURE_MAP safety surfaces).
- On any **guard** touch: declare the new sha; re-derive all 7 from the extracted zip.

## Maintenance
Append a row whenever a cut reveals a surface→test coupling that wasn't obvious (that's how the
274→275 gap should have been caught). A future improvement is to *generate* this map from test
imports/paths; until then it's curated.
