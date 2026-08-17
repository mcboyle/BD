# Cut 10: CLAUDE Contract Reduction Design

Status: approved design for implementation planning  
Base commit: `e0ca19444d40acad917f9cf21326270997d359b8`  
Base tree: `c4d7231ab33404ff292954e190617243c231db77`

## Objective

Reduce `CLAUDE.md` semantically rather than to an arbitrary byte target. The
result remains the sole agent-facing contract, but becomes a concise standing
authority instead of a chronological incident archive. No rule may disappear
merely because it is verbose: every one of the 306 current unique paragraphs
must receive exactly one disposition before `CLAUDE.md` changes.

The final contract should be approximately 250--400 lines. This is a design
target, not an acceptance denominator. Semantic coverage, direct enforcement,
and navigability take precedence over length.

## Source denominator and semantic map

The immutable base has 306 paragraphs and 306 unique fingerprints under the
production `bd-contract-rules` parser. Its baseline has 306 active rows and 14
declared historical removals. Before editing, create an external, uncommitted
map under `/home/mboyle/agent-runs/cut10/` with one row per base paragraph.

Each row contains:

- ordinal and base fingerprint;
- section and normalized excerpt;
- exactly one disposition;
- destination or removal evidence;
- the final-contract rule ID that preserves the meaning, when applicable;
- the point-of-use test/tool/runbook that carries the contract, when applicable;
- reviewer status and completion marker.

Several source paragraphs may map to one final rule because consolidation is
the purpose of this cut. Every source paragraph still appears exactly once,
and every referenced final rule must exist.

Allowed dispositions are:

1. `RETAIN_CONCISELY` -- express the standing rule in the final `CLAUDE.md`;
2. `ENFORCED_AT_POINT_OF_USE` -- retain a short pointer while executable tests,
   tools, or CI own the details;
3. `MOVED_TO_FOCUSED_RUNBOOK` -- retain a short pointer to an existing focused
   operator document;
4. `REMOVE_OBSOLETE_OR_DUPLICATE` -- remove narrative or stale detail with a
   current-source, test, history, or superseding-rule citation.

The map is proof-carrying review evidence, not a new repository authority. It
must bind the base SHA/tree and hash the source paragraph. Its exact 306-row
denominator, unique fingerprints, valid dispositions, resolved destinations,
and one-to-one source coverage are mechanically checked.

## Final contract architecture

The reduced `CLAUDE.md` has eight compact sections:

1. **Authority and scope.** BulkDownloader identity, sole agent-facing
   contract, authoritative backlog, host/commit identity, and instructions to
   measure volatile facts rather than copy counts.
2. **Authorization and state.** Matthew's authorization, hold/wait semantics,
   UNKNOWN as a failing third state, task/merge/deploy boundaries, and
   machine-visible deferral requirements.
3. **Change lifecycle.** One coherent feature per cut, RED-first TDD, exact
   scope, regeneration after the last source edit, independent review,
   exact-head CI, merge, deployment, cleanup, and terminal evidence.
4. **Writer and Git safety.** One authoritative integrator, path ownership,
   no broad staging during concurrent work, clean-state checks, no destructive
   reset outside the sanctioned deploy/recovery boundary, post-merge cleanup,
   force-with-lease prerequisites, secret scanning, and no re-authoring of
   GitHub merge commits.
5. **Verification.** Real pytest, affected-band derivation as a floor, exact
   canonical `-n 24 --dist loadfile` full-suite command, mandatory
   `env -u BD_INSTALL_DIR`, nonzero denominators, complete raw status/logs,
   split-or-ask rather than trimming CI, and evidence-backed reporting.
6. **Release and deployment.** The three version/changelog edits, canonical
   regeneration, package/frontend requirements, `scripts/deploy.sh`, its
   post-reset inode behavior and partial-failure state, health/version checks,
   and no live-service contact from test lanes.
7. **Engineering invariants.** Denominator integrity, fail-closed UNKNOWN,
   meaningful RED/negative controls, seam tests, source-rewriter applied
   checks, mutation safety, and environment isolation.
8. **Focused authorities and commands.** Small routing table to canonical
   backlog, touched-file mapping, environment provisioning, fresh-host/deploy
   runbooks, toolchain help, guard/footgun/invariant authorities, and repository
   layout. No duplicated volatile counts or copied machine data.

## Migration boundaries

Detailed historical incident narratives are removed after their rule is
represented in the final contract or a point-of-use mechanism. Git history and
the changelog remain the historical record; no new casebook, registry, or
replacement agent prompt is created.

Detailed operational procedures move only to already-current focused owners:

- `project-knowledge/IMPROVEMENT_BACKLOG.md` for open work;
- `project-knowledge/TOUCHED_FILE_TO_TEST.md` and `bd-band-derive` for bands;
- `docs/repo/ENVIRONMENT_PROVISIONING.md` for cloud/session environment;
- `docs/repo/FRESH_HOST_BRINGUP.md` and `scripts/deploy.sh` for deployment;
- tool docstrings/selftests for mutation, guards, regeneration, evidence, and
  other executable procedures;
- CI workflow plus its direct tests for shard membership and limits.

No migration may turn completed history into an open task. A destination must
already be authoritative for the subject or receive a narrow, directly tested
addition in this same cut.

## Temporary conservation machinery retirement

After the 306-row map is complete and the reduced contract is approved and
directly enforced, retire all Cut 3 paragraph-conservation machinery:

- `toolchain/bin/bd-contract-rules`;
- `project-knowledge/CONTRACT_RULES.baseline`;
- `tests/test_v3_66_1141_no_paragraph_leaves_undeclared.py`;
- its `tests/test_toolchain_534.py` selftest/catalog reference;
- direct CI shard and `_DECLARED` wiring;
- generated manifest/index references.

Replace it with one narrow repo-wide Cut 10 gate that proves:

- `CLAUDE.md` is the sole agent-facing contract;
- every mandatory semantic family below appears exactly once in the reduced
  structure and is not replaced by a second prompt;
- exact sanctioned commands and safety tokens remain present;
- every focused destination exists and is directly linked;
- retired conservation paths are untracked and fail `lexists`, including
  dangling-symlink adversaries;
- no current reader or executable still invokes the retired tool/baseline;
- every mandatory final rule and focused destination is represented without a
  duplicate authority.

The external map remains immutable evidence after merge but is not consumed by
the product or future gates. An external validator separately proves its 306
source rows reconcile to accepted dispositions and existing final rule IDs.

## Mandatory semantic families

The final gate and review must preserve or deliberately migrate all of these:

- authorization and hold semantics;
- task, merge, and deployment boundaries;
- UNKNOWN as a failing third state;
- machine-visible deferral policy;
- RED-first and one-feature-per-cut authority;
- post-merge cleanup and no-reset rules;
- multi-agent writer and staging rules;
- version and changelog requirements;
- exact sanctioned full-suite command with fixed `-n 24`;
- `env -u BD_INSTALL_DIR` behavior;
- deployment inode and partial-failure behavior;
- split or ask, never trim CI;
- force-with-lease, secret scanning, and no-amended-merge safety;
- one-agent-contract policy;
- verification and reporting requirements;
- pinned commands and environment warnings;
- denominator integrity and non-vacuous test requirements;
- host, commit, tree, and evidence identity.

## Error handling and safety

Mapping or generation fails closed on a missing/duplicate source paragraph,
invalid fingerprint, unresolved destination, unknown disposition, nonexistent
final rule ID, stale base, malformed result, or zero denominator. The repository remains
unchanged until the map and proposed final contract are reviewable.

The edit is applied as one coherent source change after RED tests exist. If
generation, tests, review, CI, or deployment fail, preserve the failed evidence,
repair narrowly, refreeze, and rerun every invalidated exact-SHA lane. Do not
restore lost text by regenerating the old baseline.

## Verification and acceptance

The final exact candidate must pass:

- the Cut 10 focused semantic/retirement gate and its adversarial mutations;
- affected-band tests derived from every changed path;
- guard, freshness, static-reference, regeneration, release, frontend, and
  packaging lanes;
- the exact canonical full suite;
- independent implementation/scope review;
- independent test-integrity/denominator review;
- independent evidence review;
- exact-head GitHub CI;
- clean repository and exact identity checks.

The final report records base/candidate/merge/tree identities, the 306-row map
path and hash, disposition counts, final line/word/byte counts, retired paths,
changed paths, test denominators, reviews, CI, merge, deployment, roadmap hash,
and rollback. After deployment, generate the separately requested inventory of
all remaining canonical backlog rows.
