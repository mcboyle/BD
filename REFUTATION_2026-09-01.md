# Adversarial refutation of the 2026-08-31/09-01 fixes -- 2026-09-01

Produced by the `refute-todays-fixes` workflow: 18 refuters across 6 fix clusters
and 3 lenses (regression, incomplete, fail-open), every claim then put through an
independent skeptic whose default answer was REFUTED, then ranked.

**47 CONFIRMED. 7 marked act_now.**

## THE DEPLOY IS BLOCKED BY THIS FILE

The fleet is six releases behind and the intent was to deploy v3.66.1388. DO NOT.
Several of the fixes below REPRODUCE THE CORRUPTION THEY WERE WRITTEN TO PREVENT,
and deploying them puts that on eight hosts. The fleet's current v3.66.1379 has the
ORIGINAL defects, which are bad; the candidate has the original defects' shapes
re-manufactured by their own fixes, which is not obviously better and is certainly
not proven better.

Fix the act_now items, re-verify, then deploy.

## Ranked

### 1. Leaked .owner after a failed set-aside turns the next attempt into an unexamined adoption of foreign .part bytes

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/staging_claim.py`  **rows** 481  **act now** YES
- A single transient rename failure — or a deploy restart between the owner mint and the replace, which needs no error at all — leaves a claim on disk that makes the retry resume over another scene's bytes, splice them, and promote the concatenation as `done` under the right title: the exact 2026-08-29 corruption, reproduced by row 481's own fix; the precondition (an ownerless non-empty .part) is measured zero in /home/mboyle/d on test5 today and unverified on the other five hosts, but it self-manufactures on the first interrupted download and is minted in code by #9.

### 2. claim() publishes the owner before clearing the bytes and the reclaim branch never re-measures

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/staging_claim.py`  **rows** 481  **act now** no (duplicate or lower)
- Duplicate of #1 reported from the reclaim side — one rollback/unwind in claim() closes both, no separate work.

### 3. claim() mints the .owner before moving the bytes and never unwinds it, so the refusal is one-shot

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/staging_claim.py`  **rows** 481  **act now** no (duplicate or lower)
- Duplicate of #1 reported from the crash-window side — its no-error variant (SIGKILL/deploy restart between _create_owner and os.replace) is the most reachable trigger of the same fix, so treat it as evidence for #1 rather than a second item.

### 4. _try_ytdlp_fallback returns a 4-tuple on 8 of 10 paths while runner_challenge unpacks 5

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_extractors.py`  **rows** 479  **act now** YES
- The only defect here that fires in the shipped live configuration with no preconditions — the sole configured site has use_ytdlp_fallback=False and captcha_api_key='', so every captcha the auto-solver cannot clear dies at the unpack, the job is recorded as `failed: worker error: not enough values to unpack`, and the entire needs_review + screenshot + captcha_type + "Take over to solve it manually" flow (with push_on_review=True) never runs; the fix is adding the fifth element to eight return statements.

### 5. _save() overwrites the vault path from the construction-time snapshot, destroying a restored vault

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** YES
- After POST /api/backup/restore writes secrets.json (same relative path, cached backend never invalidated), the next ordinary credential save serialises the stale pre-restore dict over it — silently destroying the restored vault, its salt and every credential in it, with no error and no warning, on a host that has a real 1,824-byte vault today.

### 6. The row-482 re-probe is advisory only: the first-use branch still ends in an unconditional os.replace with no exclusion against the restore writer

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** YES
- Same restore-vs-vault-writer root cause as #5 and must be fixed with it — a restore landing in the ~2 ms between the presence probe and the rename destroys the operator's vault and re-initialises it under whatever password the unlock caller typed (measured 67 clobbers in 400 natural-race trials).

### 7. delete() never validates the vault it mutates, so a damaged-but-readable store is silently mutated and reported 200 ok:true

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 502  **act now** YES
- Over a vault whose KDF metadata or commitment envelope is damaged — recoverable by repairing one field — POST /api/secrets/delete destroys the ciphertext permanently while locked, never having unlocked, and answers 200 ok:true, while /api/secrets/status answers 409 over the same bytes.

### 8. delete() launders a damaged ciphertexts container into "not present" and the route wipes the @cred: reference

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 502  **act now** no (duplicate or lower)
- Same missing-validation root as #7 (the `or {}` normalisation in the same function) — one container check closes both; its distinct half is that the route then persists a sites_config wipe of the operator's only pointer to a credential the vault has just admitted it cannot enumerate.

### 9. A job's own partial bytes are set aside whenever its .owner is missing, destroying resume

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/staging_claim.py`  **rows** 481  **act now** YES
- runner_transport.py:1435 releases the claim while a multi-GB .part survives (violating release()'s own stated precondition), so the retry renames the job's own bytes to .orphaned-* and restarts a 5 GB scene at byte 0 — and nothing ever reaps those orphans, so they accumulate unbounded and manufacture exactly the ownerless-.part population #1 needs.

### 10. bd_candidate_replay adopts a concurrently-created empty output directory and later force-removes it

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** YES
- `git worktree add` returns 0 into a pre-existing empty directory, so occupied_before_add is computed and then ignored on the success branch: the tool records a foreign inode as its own, reports REPLAYED, and on any later conflict runs `git worktree remove --force` on another worker lane's directory, taking whatever was written into it and naming nothing — the destruction row 480 was cut to prevent, surviving at the sibling branch.

### 11. The window measurement is consulted only when the add FAILS

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- Duplicate of #10 — reading occupied_before_add on the rc==0 branch closes both, no separate work.

### 12. The default-ON dedup preflight answers the ownership question with the exact query the row-479 fix rejected, and runs first

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_integrity.py`  **rows** 479  **act now** no (duplicate or lower)
- dedup_exact_url is absent from the live config so it defaults True and matches any `done` row with no transfer test, which means the row-479 cut's headline arm can never fire for a same-URL re-run, and a URL that produced no file at all is reported "Duplicate of history #N" forever until someone hits Approve; predates this cut, but it is why almost everything else in the 479 cluster is unreachable in shipped configuration.

### 13. The skip arm eats the force_download flag, so the one route that reaches the fixed code is the one it refuses

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py`  **rows** 479  **act now** no (duplicate or lower)
- Operator-facing and live: Approve and the capture workflow's "verify live" both set force_download to bypass dedup, and the skip arm then pops the flag, logs another bytes_fetched=0 "already on disk" row and reports "Already have" — so a corrupt-but-present file can never be re-fetched from the UI, and guided capture grades that no-op as "Media validated".

### 14. _fail() drops every rollback note, collapsing source mutation and orphaned worktrees into a clean conflict

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- Three materially different terminal states — clean refusal, a worker source mutated mid-replay, and a retained orphan registration plus permanent claim file — emit byte-identical stdout, so the module's headline guarantee that the source did not change is measured and then thrown away and the operator investigates the wrong thing.

### 15. "same" is returned over another url's bytes when a library row is re-used in place

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/db.py`  **rows** 479  **act now** no (duplicate or lower)
- Gated but not theoretical — library.file_path is UNIQUE and never updated, so a second scene landing on a re-used name rewrites the row in place and the first URL still resolves "same", skips over the wrong file and retitles the library row back, erasing the disproof; needs a name collision (the live `{filename}{ext}` template can collide on generic source filenames), an externally removed file, and an Approve/dedup-off re-queue.

### 16. The vault re-probe records nothing, so store_state/is_initialized/list_keys keep answering "uninitialized" and zero

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- After the guard proves a vault exists it writes no sentinel, so /api/secrets/status returns 200 ok:true with stored_keys [] and /api/health reports "uninitialized" with every @cred reference missing, for the life of the process, over a populated vault — an unavailable measurement published as OK.

### 17. The re-probe refuses the write but every read surface still reports the reappeared vault as uninitialized

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- Duplicate of #16 — setting the load-error sentinel (or re-reading) in the guard closes both.

### 18. /api/secrets/status and /api/secrets/unlock report opposite is_initialized for the same instance

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- Same reappeared-vault state as #16 seen from the routes: the false is_initialized:false actively suppresses the "Credential vault locked" banner while the correct password dead-ends at a 409 until a restart, so the operator sees a healthy empty vault instead of a locked populated one.

### 19. A silently substituted PlaintextBackend makes delete a no-op that still reports ok:true and destroys the @cred: reference

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 502  **act now** no (duplicate or lower)
- If the cryptography import fails or the backend was ever configured to plaintext, the delete route's locked/unreadable arms are structurally bypassed and the encrypted credential is orphaned in secrets.json while its pointer is persisted away — recoverable, but reported as success; the sibling import route already guards exactly this and delete does not.

### 20. api_secrets_delete discards _save_sites_config()'s success bool, reporting config_cleaned:true over a failed write

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/app_secrets.py`  **rows** 502  **act now** no (duplicate or lower)
- When the vault write succeeds and the sites_config write does not (stale unwritable .tmp, ENOSPC on the larger second write, split paths), the two stores diverge permanently and the operator is told the cleanup was durable — leaving a dangling @cred: reference that resolves to None after restart.

### 21. /api/secrets/usage launders SecretsIntegrityError into a confident empty inventory

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/app_secrets.py`  **rows** 502  **act now** no (duplicate or lower)
- The secrets-usage panel prints "No stored secrets." with ok:true over a vault the neighbouring endpoint refuses with 409, steering the operator toward re-entering or deleting credentials whose ciphertexts are intact — already filed as OPEN row 488, do not double-file.

### 22. api_secrets_delete 500s on SecretsPersistError, the third exception delete() raises

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/app_secrets.py`  **rows** 502  **act now** no (duplicate or lower)
- A full disk or read-only mount turns a delete that safely rolled back into an opaque {"ok":false,"error":"internal server error"}, indistinguishable from a crash, while both sibling secrets routes already name the condition and the remedy.

### 23. The probe-failure refusal asserts the vault file EXISTS when existence is what could not be measured

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- An unsearchable install directory on a fresh or mis-owned host produces byte-identical output whether a vault is behind it or not, and the message the operator receives — "Repair or restore the file" — prescribes the remedy for the wrong one of two opposite conditions.

### 24. A git-side checkout failure is misdiagnosed as a foreign occupant and the transaction claim is retained

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- A transient mkdir failure during checkout (ENOSPC/EDQUOT/EIO on a fleet host) is reported as "the output path is occupied" about a path that does not exist, and the retained claim then refuses that --output with OUTPUT_CLAIMED forever until an operator hand-deletes the dotfile — a retryable failure converted into a permanent one, where the pre-fix code released the claim and reported git's own words.

### 25. The OUTPUT_FOREIGN_AT_PATH refusal leaks the claim file it created, permanently poisoning that output path

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- A transient collision leaves a CLAIMED tombstone that outlives the foreign worker's cleanup, so every later replay to that path refuses OUTPUT_CLAIMED and cannot be told apart from a live competing transaction — while the sibling pre-add refusal for the identical condition correctly releases it.

### 26. The new OUTPUT_FOREIGN_AT_PATH branch leaks its claim file forever

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- Duplicate of #25 — setting release_absent_claim on that branch closes both.

### 27. The "unproven" diagnostic is gated behind final_path.exists(), so a pre-existing bad attribution is usually never surfaced

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py`  **rows** 479  **act now** no (duplicate or lower)
- When the template no longer renders the colliding name (a tier or date change) or skip_if_exists is off, db_skip_identity is never called and _identity defaults to "fine", so the cut's one operator-visible output — a needs_review row naming the mis-attributed path — has an empty denominator for exactly the upgraded-host rows it was written for.

### 28. Legitimate bytes_fetched=0 done rows that db_log's own contract calls real are rejected as ownership

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/db.py`  **rows** 479  **act now** no (duplicate or lower)
- A completed 416 resume — a fully downloaded, ffprobe-verified file that moved no bytes on its last call — is read as non-ownership, producing a false needs_review row and a full-size duplicate at name_1.mp4; error direction is safe (re-download, not wrong skip), self-healing after one occurrence, and reachable only via Approve or dedup-off.

### 29. The 416 resume-complete arm's bytes_fetched=0 is misclassified "unproven"

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/db.py`  **rows** 479  **act now** no (duplicate or lower)
- Same defect as #28 stated from the transport side — one predicate change closes both; the extra fact worth keeping is that an operator STOP landing after the final chunk is a routine way to reach it.

### 30. The new needs_review db_log can raise on the pre-download path and park the job for 10 minutes

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py`  **rows** 479  **act now** no (duplicate or lower)
- A sqlite write lock held past the 10 s busy_timeout under multi-worker load, or a full disk, turns a job that was about to download into an unclassified "worker error: database is locked" parked 600 s with no history row — the same escape shape already filed as OPEN row 519 for the sibling stat() call, and only reachable behind Approve or dedup-off.

### 31. History pruning turns a healthy repeated skip into a full re-download plus a duplicate file

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/db.py`  **rows** 479  **act now** no (duplicate or lower)
- Largely theoretical in shipped configuration: it needs a manual /api/history/prune plus dedup_exact_url=False (a key with no UI, no preset and no entry in the live config) or force_download, and it costs one re-download and one duplicate before self-healing.

### 32. Renaming the pin constants to *_FLOOR silently blinded two operator-harness guards

- **file** `/home/mboyle/bd-row-audit.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- CLAUDE.md A8 puts the harness in the tool denominator and the cut did not enumerate it: bd-row-audit's C2 duplicate-pin check and bd-union-resolve's _collapse_pins both key on the deleted `_EXPECTED_*_COUNT` names, so a union-merged double `_DECLARED_GATE_FLOOR` binding now silently last-wins into a *lowered* removal tripwire at the live bd-drain dispatch chokepoint — cheap two-line fix protecting an active pipeline.

### 33. The cut deleted five of the eight claims OPEN row 518 enumerates, and the constant its GREEN criterion pins

- **file** `/home/mboyle/BulkDownloader/project-knowledge/IMPROVEMENT_BACKLOG.md`  **rows** 531, 532  **act now** no (duplicate or lower)
- Row 518 is still OPEN with an exact acceptance contract that current main cannot satisfy — its denominator is now 3 not 8, and satisfying its GREEN literally would turn a currently-passing gate red — so a worker dispatched on it will burn a cycle before discovering the row is stale; a backlog edit, not code.

### 34. The anti-re-pin guard is blind to the *_FLOOR names the fix introduced, and never runs in CI

- **file** `/home/mboyle/BulkDownloader/tests/test_row531_denominators_are_derived_not_pinned.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- A one-character `>=` → `==` edit on _CURRENT_MARKDOWN_FLOOR or _CONFIRMED_SAFETY_GATE_FLOOR reinstates the hand-bumped chore row 531 retired and the guard reports nothing — and since this operator merges unattended on green CI, the guard being module-scoped and absent from every workflow shard means the reversal lands in a file CI does run while its only refusal does not.

### 35. Row 532's comment-anchor gate skips every extensionless Python subject — 457 of 993 anchors

- **file** `/home/mboyle/BulkDownloader/tests/test_row532_a_mutant_anchor_must_resolve_into_code.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- The tree's only comment-anchor ratchet decides Python by file suffix, so every toolchain/bin tool — including bd-mutate's own 64 anchors — is outside its denominator and a comment-only mutant there is applied, changes nothing, and is graded CAUGHT; nothing is wrong today (all 457 classify CODE) and the `examined >= 500` floor guarantees the blindness can never surface on its own.

### 36. Row 532's ratchet skips 457 of 914 Python anchors (extensionless toolchain subjects)

- **file** `/home/mboyle/BulkDownloader/tests/test_row532_a_mutant_anchor_must_resolve_into_code.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- Duplicate of #35 — bd-mutate already carries the correct shebang-aware predicate to copy, one filter change closes both.

### 37. The monotonic gate floor does not refuse a silent removal — the live population sits one above it

- **file** `/home/mboyle/BulkDownloader/tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- len(_DECLARED)=236 against a floor of 235, and every other assertion is a relative comparison between _DECLARED and ci.yml, so one repo-wide gate can be deleted from both in a single commit and CI stays green — and because the floor is never raised on growth the blind window widens by one with every gate added, contradicting the helper's own docstring.

### 38. _DECLARED_GATE_FLOOR carries the pre-cut population while _DECLARED grew

- **file** `/home/mboyle/BulkDownloader/tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- Duplicate of #37 with the provenance corrected (the slack was created by the next cut, not this one, which makes it structural rather than a mis-set literal) — one ratchet policy fix closes both.

### 39. The exact tracked-Markdown denominator left CI: bidirectional check moved into a module-scoped file no workflow runs

- **file** `/home/mboyle/BulkDownloader/tests/test_row531_denominators_are_derived_not_pinned.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- CI's only remaining Markdown assertions are shrink-only floors, so a membership drift in tracked_markdown_corpus that preserves or grows the count passes every lane green while every downstream freshness scan covers the wrong corpus; the dangerous direction (silent under-collection) is still caught today at zero slack, and bd-band-derive does select the missing gate locally.

### 40. _comment_spans builds its offset table with str.splitlines(), which disagrees with tokenize's line numbering

- **file** `/home/mboyle/BulkDownloader/tests/test_row532_a_mutant_anchor_must_resolve_into_code.py`  **rows** 531, 532  **act now** no (duplicate or lower)
- Theoretical today — zero tracked .py files contain any of the eight extra separators, so the bidirectional CODE/COMMENT inversion cannot fire until someone adds a form feed or a U+2028 to a mutant subject; worth fixing only as a one-line hygiene change alongside #35.

### 41. With use_ramdisk_stage on, the resume offset and every byte come from a path no claim guards

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py`  **rows** 481  **act now** no (duplicate or lower)
- Unreachable in the shipped fleet — use_ramdisk_stage is absent from the live config, exposed in no UI or docs, and settable only by hand-merging a key through PUT /api/sites/<id>; already filed as OPEN row 501, do not double-file, and if it is ever enabled the flat basename namespace restores the row-481 splice on the ramdisk path.

### 42. With use_ramdisk_stage on, claim() and the set-aside measure <final>.part while the bytes live in RAM

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/runner_transport.py`  **rows** 481  **act now** no (duplicate or lower)
- Duplicate of #41 and of OPEN row 501 — same unenabled feature, same fix.

### 43. The reclaim branch measures nothing and identity is sha256(page_url), so one page's two media URLs share a .part

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/staging_claim.py`  **rows** 481  **act now** no (duplicate or lower)
- Real mechanism, gated to near-zero here: it needs tier_probe_enabled (absent from the live config, defaults off) for one page_url to resolve to different objects across attempts, plus an origin sending no validator; the reporter's mirror-loop limb was refuted, so tier probe is the only documented route in.

### 44. WindowsCredentialBackend._load_index probes SECRETS_META_FILE.exists() outside its try

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- The row-487 defect verbatim at its sibling, but this backend is the default only on Windows and on this Linux fleet requires an explicit POST /api/secrets/configure, so it is a latent one-line hygiene fix (move the probe inside the try) rather than live exposure.

### 45. SECRETS_FILE.exists() is a target-resolvability test, so a symlinked vault reads as absent and os.replace destroys the link

- **file** `/home/mboyle/BulkDownloader/bulk_downloader/secrets_store.py`  **rows** 482, 487, 510  **act now** no (duplicate or lower)
- Theoretical on this fleet — nothing in BD creates, documents or ships a symlink at the vault path, the deployed vault is a plain 0600 regular file in the install dir, and systemd's ReadWritePaths would refuse a target outside it; the underlying point (lexists/O_EXCL is the measurement that answers the question) is worth folding into the #5/#6 fix.

### 46. The foreign-occupant discriminator matches English-only git stderr while subprocesses inherit the ambient locale

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- Cannot fire on this fleet — Ubuntu 24.04 ships no git message catalogs and LANG is en_US — but where it can, the guard degrades to the pre-fix behaviour and force-removes another worker's tree, so pin LC_ALL=C in _git_environment (and bd_integration_verdict) as a one-liner inside any #10 fix, and note the 1197 locale gate's denominator is tests-only and cannot see scripts/.

### 47. An uppercase full 40-hex object name is refused EXPECTED_HEAD_NOT_LITERAL

- **file** `/home/mboyle/BulkDownloader/scripts/bd_candidate_replay.py`  **rows** 480, 500  **act now** no (duplicate or lower)
- Lowest-consequence item here and close to cosmetic: it fails closed (never a false certification), no in-tree caller passes anything but lowercase rev-parse output, and the only harm is a misnamed refusal on hand invocation — note that relaxing the regex alone swaps it for a bogus SOURCE_HEAD_MISMATCH, so the fix must casefold before comparing.
