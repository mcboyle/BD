# DECOMPOSITION_LOG.md

Per-cut audit trail for the runner.py decomposition (and app.py F5.1 if interleaved
-- keep separate sections). Append one row per shipped cut. This is the rollback map
and the proof that every motion preserved the method-inventory invariant.

Columns: cut = sequence; unit/module = what moved; meth/lines = moved out of
runner.py; runner.py after = its line count post-cut; snapshot = `runner_api_snapshot
--check` result; band = banded-suite result from the extracted zip; suite = on-stash
full suite; guards = 7-guard byte-identical (Y) or declared change; zip sha;
deploy = `/api/health` version confirmed.

## runner.py

| cut | version | unit -> module | meth | lines | runner.py after | snapshot | band | on-stash suite | guards | zip sha (first12) | deploy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (baseline) | 3.66.392 | -- (pre-decomposition; all in runner.py) | -- | -- | 12065 | FROZEN (167 methods / 12 exports / 0 dup / bases=[]) | n/a | 10204/0/59 (total 10263) | Y (7/7) | dbe5cfd14416 | confirmed live |
| 1 | 3.66.397 | util -> runner_util.py | 10 (+consts/ledger) | 317 | 11748 | PASS (167/12/0 dup; bases unchanged) | Failed:0 (family 1383/0) | 10237/0/59 (total 10296) | Y (7/7) | (see 397 handoff) | confirmed live |
| 2 | 3.66.398 | integrations -> runner_integrations.py | 17 | 794 | 10956 | PASS (167/12/0 dup; bases [IntegrationsMixin]; owners moved 17) | Failed:0 (family 1383/0) | 10237/0/59 (total 10296) | Y (7/7) | da5dfd6a779a | confirmed live |
| 3 | 3.66.399 | manual+challenge+teach+integrity -> runner_{manual,challenge,teach,integrity}.py (batched) | 25 (+_ManualDownloadSession class) | 1578 | 9378 | PASS (167/12/0 dup; bases [Manual,Integrity,Teach,Challenge,Integrations]; owners moved 25) | Failed:0 (family 1441/0 from extracted zip) -- BUT missed 3 marker-count structural tests not in the family grep | 10240/3/59 @399 (3 floor-count tests read runner.py by path; VPN-CONTROLPLANE + install_teach_overlay markers moved into runner_manual) -> FIXED @400 | Y (7/7) | 8ef4064f9361 | confirmed live @399 |
| 3-fix | 3.66.400 | (test-only fix-forward for 399) adapt test_vpn_egress_followons + test_v3_43_44_heuristic_scoring _RUNNER_PY to aggregate runner.py + runner_*.py | 0 | 0 | 9378 | PASS (unchanged) | the 2 fixed files green + family re-swept incl marker tests | 10243/0/59 (total 10302) GREEN | Y (7/7) | 33c5e6b66e06 | confirmed live |
| 4 | 3.66.401 | accounts+browser+scheduler+telemetry+queue -> runner_{accounts,browser,scheduler,telemetry,queue}.py (batched) | 62 | 1870 | 7507 | PASS (167/12/0 dup; 10 mixins; owners moved 62) | comprehensive 90-file union sweep (import-runner + path-reading) green from extracted zip; 15 source-coupled structural failures across 9 files caught IN-SANDBOX + adapted to glob _AggregateSrc | 10243/0/59 (total 10302) GREEN | Y (7/7) | c064affa1059 | confirmed live |
| 5 | 3.66.402 | extractors -> runner_extractors.py (isolated; 1st of 3 HIGH) | 10 | 1841 | 5667 | PASS (167/12/0 dup; 11 mixins; owners moved 10) | 90-file union sweep green from extracted zip; 6 monkeypatch seam breaks caught IN-SANDBOX (patch runner.find_best_download -> retarget to runner_extractors use-site) + adapted | 10243/0/59 (total 10302) GREEN | Y (7/7) | 2b99f68d842f | confirmed live |
| 6 | 3.66.403 | auth -> runner_auth.py (isolated; 2nd of 3 HIGH) | 15 | 765 | 4902 | PASS (167/12/0 dup; 12 mixins; owners moved 15) | 90-file union sweep green from extracted zip; 1 seam (runner.do_login -> runner_auth use-site, 4 sites) + 16 source-coupled structural across 6 files (adapted to _bd_runner_src aggregate; file78 surgical -- count-tests left on runner.py) caught IN-SANDBOX | 10243/0/59 (total 10302) GREEN | Y (7/7) | f9188ca236d7 | confirmed live |
| 7 | 3.66.404 | transport -> runner_transport.py (isolated; 3rd/FINAL HIGH; only Gotcha-A unit) | 15 | 1796 | 3104 | PASS (167/12/0 dup; 13 mixins; owners moved 15) | 90-file union sweep green from extracted zip; Gotcha-A: 5 SiteRunner._foo (handoff said 4) -> TransportMixin._foo (preserves static dispatch; 2 are in staticmethod bodies w/o self); 24 sandbox fails fixed = 2 db_log seams (274 hard + 146 latent -> runner_transport use-site) + 22 source-coupled structural across 8 files (incl 3 stale local _AggregateSrc GLOB-ified durably) | awaiting deploy | Y (7/7) | STATE.json (self-ref) | awaiting deploy |

**PHASE 3 COMPLETE @ v3.66.404** -- runner.py decomposed 7506 -> 3104 lines; SiteRunner = thin shell over 13 sibling mixin modules (integrations/kernel done pre-session; manual/challenge/teach/integrity @399; accounts/browser/scheduler/telemetry/queue @401; extractors @402; auth @403; transport @404). Next: Phase 4 (app.py).

## app.py (F5.1) -- only if interleaved; prefer finishing one file first

| cut | version | domain -> blueprint | routes | lines | app.py after | snapshot | band | on-stash suite | guards | zip sha | deploy |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (baseline) | 3.66.392 | -- (pre-decomposition) | -- | -- | 20503 | route_map_snapshot FROZEN | n/a | 10204/0/59 | Y (7/7) | dbe5cfd14416 | confirmed live |

---

### How to fill a row (per cut)
1. `runner.py after` = `wc -l bulk_downloader/runner.py` after the move.
2. `snapshot` = paste the one-line `runner_api_snapshot --check` RESULT (PASS + "owners
   moved: N").
3. `band` = `Failed:N` from the extracted-zip band (must be 0); name the suites if the
   transport band (track_k/rate_limit/vpn_egress_followons) was in play.
4. `on-stash suite` = the `capture.sh --summary` totals after Matt deploys (the binding
   gate); must hold `10204/0/59` unless a row's change intentionally adds tests.
5. `guards` = Y if all 7 byte-identical; if a guard changed (shouldn't for runner motion),
   record old->new SHA and why.
6. `zip sha` = first 12 of the built zip sha256.
7. `deploy` = "confirmed live" once `/api/health` reports the new version on stash.
