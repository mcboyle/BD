# Task 7 report: corpus semantic failures

## Outcome

**DONE**

- Code commit: `fbba9dbf9aea0be4a8e4ad127330747feaeb4639`
  (`test: isolate private capture corpus lane`).
- Default ten-module release lane: **56 passed, 0 failed, 54 skipped**.
- Explicit private-corpus lane: **110 passed, 0 failed, 0 skipped**.
- Helper contract suite: **6 passed**.
- No production redactor default, privacy-floor scanner, semantic assertion
  count, or floor gate was weakened.
- No raw fixture was promoted. Every replacement came from the supplied corpus
  through the canonical `redact_capture` export boundary and had zero
  `scan_floor_secrets` findings.
- Original sources were unchanged. Ten installed artifacts were replaced only
  after staged validation, with timestamped rollback copies retaining the exact
  previous hashes.

## Authorization and privacy posture

The initial brief required strict processing or isolation. During execution the
user explicitly authorized a narrower private-fixture posture: omit the extra
standalone strict scrub for these private test fixtures while retaining the
canonical project redactor and an unchanged, fail-closed privacy floor. The user
later authorized remapping the remaining private fixtures and replacing one
obsolete capture-specific literal contract with a meaningful structural
contract.

All diagnostics below contain only test names, categories, booleans, counts,
artifact hashes, and filesystem metadata. No capture URL, header value, token,
cookie, DOM content, or pre-redaction value was printed or retained. The raw
sources were read only in memory. The raw option authorized by the user was not
needed.

The active canonical profile was not reduced:

- network signed URLs: `keep_structure`
- DOM embedded URLs: `keep_structure`
- emails: `redact`
- reduced-redaction flag: `false`

## Reproduction and sanitized failure taxonomy

The initial exact remote run, with traceback and captured output suppressed,
reproduced **95 passed, 15 failed, 0 skipped**. The canonical-floor output was
object-equal to the installed Task 4 capture JSON for the inspected artifacts.

| Failing assertion | Sanitized expected category | Sanitized installed actual | Raw/canonical-floor control | Root cause |
|---|---|---|---|---|
| `TestRealBros::test_bros_collapses_to_one_identity` | one identity; sharded count 11 | two identities; one sharded; max sharded count 11 | passes | extra strict scrub over-redaction |
| `TestNubileLossRetired::test_slug_now_promoted` | expected identity present | expected identity absent; total identity count 1 | passes | extra strict scrub over-redaction |
| `TestNubileLossRetired::test_slug_carries_filename_echo_signal` | expected identity and signal present | both absent | passes | extra strict scrub over-redaction |
| `TestTOverGenerationRetired::test_only_one_real_identity` | one identity | two identities; expected identity still present; all four literal-marker checks present | passes | extra strict scrub over-redaction |
| `TestBrosCollapseRetained::test_one_sharded_identity` | one sharded identity | two identities; one sharded | passes | extra strict scrub over-redaction |
| `TestOpaqueIdNoRegression::test_ultrafilms_identity_unchanged` | one stable opaque identity, historically coupled to one literal | no identity in the old pair | old raw pair also fails; only two corpus artifacts carried the historical literal | incompatible historical literal contract |
| `TestOpaqueIdNoRegression::test_filthy_uuid_identity_unchanged` | at least one UUID-shaped identity | zero identities; zero UUID-shaped identities | passes | extra strict scrub over-redaction |
| `TestProgressiveUnchanged::test_ultrafilms_no_manifest` | progressive selection via highest-sequence media | progressive kind via last-request fallback; zero manifest candidates | old raw pair also fails | wrong source mapping |
| `TestNubileNowCorrect::test_slug_identity_filename_rendition` | exact one identity and rendition evidence | one different identity; zero renditions | passes | extra strict scrub over-redaction |
| `TestUltrafilmsN3::test_identity_invariant_confirmed` | confirmed | falsified | old raw series also falsified | wrong source mapping |
| `TestUltrafilmsN3::test_rendition_varies_attributed_to_rendition` | confirmed | falsified | old raw series also falsified | wrong source mapping |
| `TestUltrafilmsN3::test_structural_stable_confirmed` | confirmed | falsified | old raw series also falsified | wrong source mapping |
| `TestUltrafilmsN3::test_floor_confirmed_with_real_n3_data` | confirmed; qualifying true | falsified; qualifying true | old raw series also falsified | wrong source mapping |
| `TestN2FloorUntested::test_signing_drift_measurable_when_values_present` | confirmed | untested | passes | extra strict scrub over-redaction |
| `TestAnalysisReuse::test_single_capture_uses_goal_skeleton` | identity-slot count greater than zero | identity-slot count 0 | old raw source also fails | wrong source mapping |

Totals: **8 over-redaction**, **6 wrong-source mappings**, and **1 incompatible
historical literal contract**.

The strict-only stage introduced all eight over-redaction failures. Applying
the unchanged canonical floor directly to those raw sources produced zero floor
findings and passed all eight. No product-code defect was found: the conflict was
between an intentionally more destructive standalone scrub and the historical
semantic fixtures.

## Full supplied-corpus search

The initial privacy-safe manifest shortlist covered 41 unique payloads: 2 Bros,
1 capA, 1 T, 10 Filthy, 15 Nubile, and 12 Ultrafilms. It found no alternative
strict-plus-floor match. A canonical-floor structural projection found only the
already selected Bros, T, Nubile, and Filthy sources.

After the user broadened authorization, the search covered **all 626 valid
unique WACZ payloads**, in both raw in-memory and canonical-floor structural
modes:

- processed: 626/626
- unresolved, load, or floor errors: 0
- captures with a media goal: 489
- canonical-floor captures with a nonempty identity: 352
- stable, nonempty-identity N>=3 structural groups: 19
- selected Ultrafilms-aligned group: three unique source hashes and three
  distinct capture timestamps
- raw fixtures promoted: 0
- synthetic fixtures created: 0

Only two supplied artifacts carried the old capture-specific identity literal.
They form a valid progressive pair but cannot truthfully establish an N=3
invariant. The selected real three-capture group satisfies the progressive,
single-capture identity, identity invariance, rendition attribution, structural
stability, and N=3 floor contracts. The remaining literal assertion was updated
to require exactly one eight-character hex opaque identity. This preserves the
semantic claim while removing coupling to one obsolete capture value.

## Explicit private-corpus lane

`capture_test_fixtures.py` centralizes external roots:

- `BD_TEST_CAPTURE_ROOT` enables the regular private capture corpus.
- `BD_TEST_STRICT_CAPTURE_ROOT` independently enables the strict recognizer
  corpus.
- both lanes are disabled when their environment variable is absent.
- roots must be absolute and artifact names cannot traverse outside the root.

All ten modules now use the helper. The default suite cannot discover the old
host paths. A static regression test rejects either embedded legacy root in any
of the ten modules.

Remote validation used the code-only mode-0700 staging tree
`/home/mboyle/.task7-corpus-lane-c0b76ff`. Artifact processing used the
mode-0700 stage `/home/mboyle/.wacz-stage.task7-floor-c0b76ff`.

## Red-green evidence

1. Helper tests first failed collection because `capture_test_fixtures` did not
   exist. After the minimal helper implementation: 5 passed.
2. The module-centralization regression then failed on the first embedded
   legacy path. After all ten modules used the helper: 6 passed.
3. The selected real three-capture series was tested before changing the stale
   literal assertion: 6 passed, 1 failed, with the single expected literal
   mismatch. After changing only that contract to the nontrivial opaque-id shape:
   7 passed.
4. Before installation, the complete candidate corpus ran 110/110.

## Installed floor-only replacements and rollback evidence

All WACZ rows are CRC-valid and internally digest-valid. Every row has zero
floor findings. Source-before SHA equaled source-after SHA for all ten source
payloads.

First replacement set, backup timestamp `20260721T184658Z`:

| Target | Source SHA-256 | Previous/backup SHA-256 | Installed SHA-256 | Bytes | Backup file |
|---|---|---|---|---:|---|
| `bros_title1_1.wacz` | `3e9c69f8d6d8c149f176d5d84bc92b2cc8226dc281e071a4bbb78bd6086a9ffc` | `5f305a653135b2a2264c9a72a1faaea7ca99f02bd5b0de952c99a3f54bc57ae2` | `3eef62fafd684b0cc5f3e7267c5ef1c2fcb0287bde8719bfe8a24015c1d5491e` | 953,062 | `bros_title1_1.wacz.task7-backup-20260721T184658Z` |
| `bros_title1_cap2.wacz` | `dde94029e2f8dcf49fafb32fd583f00082a5f258653ca2ea2afab996768b1054` | `060ad03f95c2395a7a7e79a4a513378f90c8df81464fa05945a1d7016ca1686c` | `1590191ae2fdb75770b34fe75e5e4bdca94de39431934fb95378a40ecdac38c5` | 1,137,008 | `bros_title1_cap2.wacz.task7-backup-20260721T184658Z` |
| `nubile_title1_cap1.wacz` | `2de9c910e03480bd358463af09f173dad05d51ec6ad6ea8c56de7f1c5dfb0614` | `53bf22b7e6ceefa4a8cd8f14168a69b42435ef1fd7f7b580406117328f20c3ba` | `7ad72094b81ce0e1fe05a821ab64366980233a2db2ac9f96170efad7bcb45760` | 785,166 | `nubile_title1_cap1.wacz.task7-backup-20260721T184658Z` |
| `nubile_title1_cap2.wacz` | `d11f490a4f85afcf131ff1d3205a01ff0ca5c51a0557a80620b25fe32248aa05` | `8acbc374e38e2c41baa26f6cfed5f5f842a4f309d3106a4290391a7b546122be` | `b2a4c937ffc96b10af15741ac26e0309a1de6312c3796a68986711f79eeccdbb` | 1,075,521 | `nubile_title1_cap2.wacz.task7-backup-20260721T184658Z` |
| `t_title1_cap2.wacz` | `6887623d8f42f2ac103e6ae65bb2a9d9ea2ed9437f24e577b018367f729db83b` | `863c067c77d3b410d77851a8c514eec7096daf471a2057fa43966e1bdfb92966` | `c305904ccd5b52a5bf99f34dd1f53b8809248834ebc411f23b4f30b35c3ab82f` | 475,860 | `t_title1_cap2.wacz.task7-backup-20260721T184658Z` |
| `filthy_title1_cap1.wacz` | `f5cd4ff04f9a9b243187fdd91b4d53ae55559afe273e747f06c4cf0d3b18a677` | `cd3e124c10cb65abb1b9f12f3da92e811a5f0a6edfd44f924ae1300fbe011b91` | `7c7991cbb4a87da36d1d8ffe6518ace48a446ffad1e90ca128505d6d6d5331de` | 5,244,239 | `filthy_title1_cap1.wacz.task7-backup-20260721T184658Z` |
| `filthy_title1_cap2.wacz` | `6ac5267253ea97b49aab68b877d76fccc67bf8c23a731cfa264228838cfb9a3f` | `58ea3b9c0cf0da6796397a12cc0f028e06c92aa7786bb5cb2d14f97a12456f58` | `39d77a36c6aee2ec6af41d6c2c26c7ec10bd64034df8e50a9b88ba32cfeb5fe4` | 3,549,589 | `filthy_title1_cap2.wacz.task7-backup-20260721T184658Z` |

Three-capture remap, backup timestamp `20260721T185924Z`:

| Target | Selected source SHA-256 | Previous/backup SHA-256 | Installed SHA-256 | Bytes | Backup file |
|---|---|---|---|---:|---|
| `capA.json` | `0f4eeb105c114255e36b9b0b6f8b516074a8243d07f2095cbedf8dddec647972` | `3c89f69b7582654aba46180cadf7d7f2b888f2c06ffceaee408145687142a952` | `d1d6ef9adffa5c17a07a33711583d56efc915e6b8a27209846c0609856181cae` | 2,109,647 | `capA.json.task7-backup-20260721T185924Z` |
| `ultrafilms_title1_later.wacz` | `85df69516d67b5038d89e0d679b33173c798f8641bbd693d1050fcbb2a28471e` | `276d1b435e126c6dead0d76707f0f97c99b9ddfd5f5f35367ae9f6e3c989186b` | `78b07c90d3806f4e71d8dccfe89fce7208298578413e0e87e62911a1da713a0c` | 2,036,489 | `ultrafilms_title1_later.wacz.task7-backup-20260721T185924Z` |
| `yultrafilms_title1_later.wacz` | `3e48c8610314eee6da02bfc1852fa37c3d0e9f408af191933a91adfb2abc032d` | `19ffe98a2c12fc625ce33b9f8cfc3dc3543cd1b8758deb2f292f794486f1f9cf` | `77be46432046b898c7031b06832263567b08764542a9412da7556ec25874623b` | 2,080,766 | `yultrafilms_title1_later.wacz.task7-backup-20260721T185924Z` |

All backups are beside their targets under `/mnt/user-data/uploads`. Target
modes were preserved. Replacement used validated same-directory temporary files
and `os.replace`; the installer had automatic restore-from-backup logic for any
mid-transaction failure. No rollback was needed.

## Final per-module results

| Module | Default pass | Default fail | Default skip | Explicit pass | Explicit fail | Explicit skip |
|---|---:|---:|---:|---:|---:|---:|
| `test_v3_66_77_sharded_collapse.py` | 8 | 0 | 1 | 9 | 0 | 0 |
| `test_v3_66_82_confidence_admission.py` | 6 | 0 | 8 | 14 | 0 | 0 |
| `test_v3_66_83_hls_manifest_preference.py` | 3 | 0 | 6 | 9 | 0 | 0 |
| `test_v3_66_84_rendition_suffix.py` | 5 | 0 | 1 | 6 | 0 | 0 |
| `test_v3_66_85_signing_in_path.py` | 3 | 0 | 5 | 8 | 0 | 0 |
| `test_v3_66_87_temporal_harness.py` | 1 | 0 | 8 | 9 | 0 | 0 |
| `test_v3_66_88_perturbation_harness.py` | 0 | 0 | 9 | 9 | 0 | 0 |
| `test_v3_66_89_offline_capture_ingest.py` | 3 | 0 | 13 | 16 | 0 | 0 |
| `test_v3_66_101_cockpit_wave3.py` | 22 | 0 | 1 | 23 | 0 | 0 |
| `test_v3_66_249_aylo_api_recognizer.py` | 5 | 0 | 2 | 7 | 0 | 0 |
| **Total** | **56** | **0** | **54** | **110** | **0** | **0** |

The explicit lane exercised every supplied fixture assertion. The default lane
is intentionally isolated from private external files and contains no failure.
