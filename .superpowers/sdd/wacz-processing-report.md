# Task 4 WACZ processing report

## Outcome

**DONE_WITH_CONCERNS**

- Installed 16 privacy-scrubbed fixtures: 15 WACZ files and one derived JSON file.
- All 15 installed WACZ files pass ZIP CRC validation and the project's
  `scan_floor_secrets(...) == []` gate. The installed `capA.json` also has zero
  floor findings.
- All 16 final target SHA-256 values match their staged processed copies.
- Ran exactly the ten requested capture-test modules: **95 passed, 15 failed,
  0 skipped** across 110 collected tests.
- Built a privacy-safe read-only manifest and ran builder, normalization, and
  promotion-preflight classification over every current valid unique WACZ SHA.
  Nothing was written to the draft/review queues and no reviewed template was
  promoted or altered.
- The inventory wording says "629 unique valid WACZ", but the inventory's own
  arithmetic and the fresh scan show **629 unique SHA payloads total = 626 valid
  unique WACZ + 3 unique invalid archives**. All 626 valid unique payloads were
  classified; the three invalid payloads were listed only.

## Host, staging, and privacy posture

- Host: saved PuTTY session `stash`, non-interactive Plink, user `mboyle`.
- Successful private stage: `/home/mboyle/.wacz-stage.4a3hyat0`, mode `0700`.
- Earlier validation-only stage: `/home/mboyle/.wacz-stage.4ywp477v`, mode
  `0700`; it stopped before installation when the standalone strict scrubber's
  output still had project-floor findings. It is retained for audit and was not
  promoted.
- Project scrubber SHA-256:
  `65a08eaa2442da734d785ec43f449bf54c957af2f03c2edc77090f3d02515089`.
- Project floor module SHA-256:
  `5ba4208bf2d779683a5101524e02f4789a8e8c317c4c1cdeaf36cd516e0d4853`.
- Both remote hashes matched the worktree copies before processing.
- Scrubber stdout/stderr and builder stdout/stderr were suppressed. No preview
  mode was run, and no raw URL, cookie, token, header value, DOM content, or
  pre-redaction value was retained in the results or manifest.

The first pass proved an important distinction: `tools/capture_scrub.py
--mode strict` completed and its independent verifier was clean, but the
project's canonical floor still found 93 items in the first Bros capture's
`archive/capture.json`. No file had been installed. The successful pass applied
the same strict scrubber and then the project's canonical `redact_capture`
floor to the staged capture JSON before requiring a zero result from
`scan_floor_secrets`. This is strictly more redaction, not a test or validator
weakening.

## Source resolution and immutability proof

Every required basename was re-resolved immediately before staging by its
expected SHA-256 under `/home/mboyle`. Each basename resolved to exactly one
regular, non-symlink path and one expected hash. For every source,
source-before SHA = source-after SHA = staged-copy SHA. All required source
archives passed CRC before processing.

| Source basename | Re-resolved source | Source/staged SHA-256 |
|---|---|---|
| `bros_title1_1.wacz` | `/home/mboyle/templates/bros_title1_1.wacz` | `3e9c69f8d6d8c149f176d5d84bc92b2cc8226dc281e071a4bbb78bd6086a9ffc` |
| `bros_title1_cap2.wacz` | `/home/mboyle/templates/bros_title1_cap2.wacz` | `dde94029e2f8dcf49fafb32fd583f00082a5f258653ca2ea2afab996768b1054` |
| `nubile_title1_cap1.wacz` | `/home/mboyle/templates/nubile_title1_cap1.wacz` | `2de9c910e03480bd358463af09f173dad05d51ec6ad6ea8c56de7f1c5dfb0614` |
| `nubile_title1_cap2.wacz` | `/home/mboyle/templates/nubile_title1_cap2.wacz` | `d11f490a4f85afcf131ff1d3205a01ff0ca5c51a0557a80620b25fe32248aa05` |
| `t_title1_cap2.wacz` | `/home/mboyle/templates/t_title1_cap2.wacz` | `6887623d8f42f2ac103e6ae65bb2a9d9ea2ed9437f24e577b018367f729db83b` |
| `filthy_title1_cap1.wacz` | `/home/mboyle/templates/filthy_title1_cap1.wacz` | `f5cd4ff04f9a9b243187fdd91b4d53ae55559afe273e747f06c4cf0d3b18a677` |
| `filthy_title1_cap2.wacz` | `/home/mboyle/templates/filthy_title1_cap2.wacz` | `6ac5267253ea97b49aab68b877d76fccc67bf8c23a731cfa264228838cfb9a3f` |
| `ultrafilms_title1_later.wacz` | `/home/mboyle/templates/ultrafilms_title1_later.wacz` | `6689a33920e665f7340173bdc814ebf4c5d8fcf416ca282488f72b289a3ba141` |
| `yultrafilms_title1_later.wacz` | `/home/mboyle/templates/yultrafilms_title1_later.wacz` | `b8c0ef77ddae5c69eb743322a7648e20df8af852c82f41809f0b9b6facf83e22` |
| `ultrafilms_title2.wacz` | `/home/mboyle/templates/ultrafilms_title2.wacz` | `3e48c8610314eee6da02bfc1852fa37c3d0e9f408af191933a91adfb2abc032d` |
| `ultrafilms_title14_later.wacz` | `/home/mboyle/templates/ultrafilms_title14_later.wacz` | `85df69516d67b5038d89e0d679b33173c798f8641bbd693d1050fcbb2a28471e` |
| `ultrafilms_2candies_4k.wacz` | `/home/mboyle/templates/ultrafilms_2candies_4k.wacz` | `ed11344cf7eec081f950ca590abd509647975f95f2bcc3e9d48b19bc92188e44` |
| `ultrafilms_2candies_720p.wacz` | `/home/mboyle/templates/ultrafilms_2candies_720p.wacz` | `9420eefbf6b7f799ec02e0447a8729ab8bfa1aed89dd5a9cda68e78f238f0627` |
| `capA.wacz` | `/home/mboyle/templates/capA.wacz` | `fc8143295d7ff3bbc6d6264af087d36185e6d82b59d36d7fa6338d16b4a3b351` |
| `bang247_redacted_strict.wacz` | `/home/mboyle/templates/bang247_redacted_strict.wacz` | `34367ac1062ded99d2a42e27f0e65d20ad4da6e3bc7f6bfbf5abd7209c78ad31` |
| `wow247_redacted_strict.wacz` | `/home/mboyle/templates/wow247_redacted_strict.wacz` | `bd4c6599f69cda3020ec68f3559443daf15d980f4b9aa9e7cb7683cb068eb71d` |

No source was deleted, renamed, moved, or edited.

## Installed fixtures and exact hashes

All processing occurred on staged copies. Installation used a copy to a
same-directory temporary file, SHA verification, and atomic rename. The target
directories were absent and were created only at the authorized locations.
Every target was new, so there were **zero pre-existing targets and zero backup
files to create**.

| Installed target | Bytes | Installed SHA-256 | CRC | Floor findings |
|---|---:|---|---:|---:|
| `/mnt/user-data/uploads/bros_title1_1.wacz` | 83,929 | `5f305a653135b2a2264c9a72a1faaea7ca99f02bd5b0de952c99a3f54bc57ae2` | pass | 0 |
| `/mnt/user-data/uploads/bros_title1_cap2.wacz` | 114,515 | `060ad03f95c2395a7a7e79a4a513378f90c8df81464fa05945a1d7016ca1686c` | pass | 0 |
| `/mnt/user-data/uploads/filthy_title1_cap1.wacz` | 465,962 | `cd3e124c10cb65abb1b9f12f3da92e811a5f0a6edfd44f924ae1300fbe011b91` | pass | 0 |
| `/mnt/user-data/uploads/filthy_title1_cap2.wacz` | 282,008 | `58ea3b9c0cf0da6796397a12cc0f028e06c92aa7786bb5cb2d14f97a12456f58` | pass | 0 |
| `/mnt/user-data/uploads/nubile_title1_cap1.wacz` | 94,184 | `53bf22b7e6ceefa4a8cd8f14168a69b42435ef1fd7f7b580406117328f20c3ba` | pass | 0 |
| `/mnt/user-data/uploads/nubile_title1_cap2.wacz` | 122,904 | `8acbc374e38e2c41baa26f6cfed5f5f842a4f309d3106a4290391a7b546122be` | pass | 0 |
| `/mnt/user-data/uploads/t_title1_cap2.wacz` | 38,861 | `863c067c77d3b410d77851a8c514eec7096daf471a2057fa43966e1bdfb92966` | pass | 0 |
| `/mnt/user-data/uploads/ultrafilms_2candies_4k.wacz` | 12,387 | `88d44618ce732a0e761f99c23533c53d5436c6c7803da92e052f5315263fefed` | pass | 0 |
| `/mnt/user-data/uploads/ultrafilms_2candies_720p.wacz` | 10,412 | `3d076dc8d67f1c52c68eb2e8c5f8829be80bcfe355ad26d14375c7a1db334914` | pass | 0 |
| `/mnt/user-data/uploads/ultrafilms_title14_later.wacz` | 186,099 | `3cc58e21391cf52203f794bfc0f1e28fe05b5c7006f0c79723bdb016869a5191` | pass | 0 |
| `/mnt/user-data/uploads/ultrafilms_title1_later.wacz` | 184,495 | `276d1b435e126c6dead0d76707f0f97c99b9ddfd5f5f35367ae9f6e3c989186b` | pass | 0 |
| `/mnt/user-data/uploads/ultrafilms_title2.wacz` | 187,652 | `6d03f433aa803f71dd24512893e465c3e56fb05fabde098468f6d68b58de83fd` | pass | 0 |
| `/mnt/user-data/uploads/yultrafilms_title1_later.wacz` | 84,695 | `19ffe98a2c12fc625ce33b9f8cfc3dc3543cd1b8758deb2f292f794486f1f9cf` | pass | 0 |
| `/mnt/user-data/uploads/capA.json` | 1,054,467 | `3c89f69b7582654aba46180cadf7d7f2b888f2c06ffceaee408145687142a952` | n/a | 0 |
| `/home/claude/corpus/wacz/bang247_redacted_strict.wacz` | 318,105 | `17aa2cd8b597098cc95da5590f6440c2fdf7bf3decd190c8618784e5109ca45c` | pass | 0 |
| `/home/claude/corpus/wacz/wow247_redacted_strict.wacz` | 238,713 | `f2e8ada1d16db18a1e275cdb46d1c9cb16d030d1357dd0a9a167a52c51b5a2a6` | pass | 0 |

`capA.json` was derived only by extracting `archive/capture.json` from the
CRC-valid staged `capA.wacz`, then applying strict scrub plus the canonical
floor. It was not synthesized or hand-edited.

An independent post-test verification re-hashed all 16 targets and repeated
CRC/floor checks: 16/16 SHA matches, 0 CRC failures, 0 total floor findings.

## Invalid-archive quarantine list

These were re-resolved and re-hashed, confirmed invalid, and listed only. No
copy, move, rename, delete, or repair was attempted.

| Path | Bytes | SHA-256 | Result |
|---|---:|---|---|
| `/home/mboyle/templates/t_120575cc91a942fc_nookies.redacted.wacz` | 524,288 | `b05c8bf6ba9b723f30bef33b36a5843937f1fe0e6b1d4d894f6f77261629dfb7` | invalid ZIP |
| `/home/mboyle/templates/bang170 (dup1).wacz` | 5,767,168 | `4a68c6acab0d5bf993e683718cde8bb5cd9e32e97caf91dcd5144dc1d0352f6f` | invalid ZIP |
| `/home/mboyle/templates/._banb.redacted.wacz` | 212 | `b6e9767abe58c6db0426aef9fec8d7d0442aeb4e3aead66fa95ba9f4a3205d35` | invalid ZIP |

## Test run

The remote venv did not contain pytest. Per authorization, pytest was installed
only in `/home/mboyle/BulkDownloader/venv` with the constraint `pytest>=7,<9`;
the resolved version is **8.4.2**.

Exactly these ten modules ran together, with no test weakening or fixture-name
substitution:

| Module | Passed | Failed | Skipped |
|---|---:|---:|---:|
| `tests/test_v3_66_77_sharded_collapse.py` | 8 | 1 | 0 |
| `tests/test_v3_66_82_confidence_admission.py` | 8 | 6 | 0 |
| `tests/test_v3_66_83_hls_manifest_preference.py` | 8 | 1 | 0 |
| `tests/test_v3_66_84_rendition_suffix.py` | 5 | 1 | 0 |
| `tests/test_v3_66_85_signing_in_path.py` | 8 | 0 | 0 |
| `tests/test_v3_66_87_temporal_harness.py` | 4 | 5 | 0 |
| `tests/test_v3_66_88_perturbation_harness.py` | 9 | 0 | 0 |
| `tests/test_v3_66_89_offline_capture_ingest.py` | 15 | 1 | 0 |
| `tests/test_v3_66_101_cockpit_wave3.py` | 23 | 0 | 0 |
| `tests/test_v3_66_249_aylo_api_recognizer.py` | 7 | 0 | 0 |
| **Total** | **95** | **15** | **0** |

The brief states that 54 assertions were previously skipped. All are now
exercised (zero remain skipped): arithmetically, 39 became passes and 15 became
failures. The current test source has 53 directly identifiable external-fixture
skip guards; those account for 38 passes and all 15 failures. The prior
node-level test report was not available, so the identity of the brief's one
additional historical skip cannot be proven without guessing.

Directly identifiable formerly fixture-gated assertions now passing:

- Confidence admission: T signing/routing demotion; promoted-slot score/signal visibility.
- HLS selection: both Bros manifest assertions; Filthy fallback; goal-selection
  record shape; query-stripped candidate posture.
- Signing-in-path: all four real-capture assertions.
- Temporal harness: N=2 floor remains honestly untested; Ultrafilms signing is
  untestable after redaction; report contains no signing values.
- Perturbation harness: all nine assertions.
- Offline ingest: 12 of the 13 fixture-gated assertions, including JSON/WACZ
  loading, normalization posture, temporal reuse, report generation, corpus
  boundary, and non-mutation.
- Cockpit evidence diff: real two-capture posture assertion.
- Aylo recognizer: both real strict-corpus assertions.

### Remaining semantic failures

The 15 failures are preserved honestly:

1. `TestRealBros::test_bros_collapses_to_one_identity`
2. `TestBrosCollapseRetained::test_one_sharded_identity`
3. `TestNubileLossRetired::test_slug_carries_filename_echo_signal`
4. `TestNubileLossRetired::test_slug_now_promoted`
5. `TestOpaqueIdNoRegression::test_filthy_uuid_identity_unchanged`
6. `TestOpaqueIdNoRegression::test_ultrafilms_identity_unchanged`
7. `TestTOverGenerationRetired::test_only_one_real_identity`
8. `TestProgressiveUnchanged::test_ultrafilms_no_manifest`
9. `TestNubileNowCorrect::test_slug_identity_filename_rendition`
10. `TestN2FloorUntested::test_signing_drift_measurable_when_values_present`
11. `TestUltrafilmsN3::test_floor_confirmed_with_real_n3_data`
12. `TestUltrafilmsN3::test_identity_invariant_confirmed`
13. `TestUltrafilmsN3::test_rendition_varies_attributed_to_rendition`
14. `TestUltrafilmsN3::test_structural_stable_confirmed`
15. `TestAnalysisReuse::test_single_capture_uses_goal_skeleton`

The failures cluster around identity/slug/rendition structure and temporal
comparisons. The mandated strict scrub and canonical floor remove or mask the
same path/query evidence those assertions expect to classify. In particular,
the Nubile signing-drift assertion expects present differing signing values,
while strict privacy processing deliberately removes them. No test was changed,
no validator was weakened, and no raw fixture was substituted to make these
assertions pass. Resolving them requires a product decision: either design
privacy-preserving structural evidence that retains these semantics, or update
the semantic contract with separately reviewed safe fixtures. This task did
neither automatically.

## Full-corpus privacy-safe manifest and read-only preflight

Manifest:
`/home/mboyle/.wacz-stage.4a3hyat0/privacy-safe-wacz-json-manifest.json`

- Bytes: 1,396,701
- SHA-256: `62e85e5c43f6b973849043801de2a6af7e802e83395d2c9acb9e0c65fa451b2f`
- WACZ files observed: 1,086
- Valid WACZ files: 1,083
- Invalid WACZ files: 3
- Unique valid WACZ SHA payloads: 626
- Read-only builder + normalize completed: 626/626
- Builder errors: 0
- Normalized `review_ready` / gate-pass: 181
- Normalized `draft_review_required` / gate-blocked: 445
- Auto-promotions: 0
- Draft/review-candidate writes: 0
- External JSON parsed: 1,100 files, 778 unique SHA payloads
- External JSON parse failures: 2
- Unique JSON schema classes: array 22; capture 47; extension-hash metadata 1;
  generic object 304; inspection state 188; template draft/review 63;
  other versioned schema 153.

Each valid unique WACZ entry contains only SHA, file metadata, copy paths,
member names, capture container/key names, and sanitized builder/preflight
classifications. Every corresponding internal `archive/capture.json` was parsed
for schema/key metadata as part of the 626-entry pass. External JSON was also
parsed and classified by container/key schema without retaining values.

The manifest is intentionally review-only. The 181 gate-pass classifications
are not approvals, and the 445 blocked classifications were not altered or
forced through.

## Evidence artifacts

All are mode-protected inside the successful stage:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `task4-install-result.json` | 16,578 | `dc1e5c75f522f55950e2d268618a66542a0c51efbc36ff62b425cf069a2ee324` |
| `task4-test-result.json` | 18,755 | `d18b4e187344516a36f7b966b69f90a88222ab5a6f8c90c6ff692f7345ecb81e` |
| `privacy-safe-wacz-json-manifest.json` | 1,396,701 | `62e85e5c43f6b973849043801de2a6af7e802e83395d2c9acb9e0c65fa451b2f` |

## Rollback

There are no prior-target backups because all 16 target paths were absent.
Rollback therefore means deleting exactly the 16 installed target files listed
in the installed-fixtures table. That destructive rollback was **not** run.
After file rollback, the two newly created target directory trees may be removed
only if they are empty and the operator explicitly wants them removed.

The successful stage remains available for audit or exact reinstallation. The
original source files remain in place and are not part of rollback. The earlier
failed stage may also be removed later by an authorized operator after deciding
that its audit evidence is no longer needed.

## Local repository posture

No application or test code was changed for this operational task, and no local
git commit was created. The only local deliverable is this report.
