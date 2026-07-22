# PROCESS_CONVENTIONS — continuity + plan/tracker discipline

Durable conventions that keep the tracker and the plans from drifting. Distilled
from the v3.66.277/278/279 process retrospective. These are *conventions*, not
gates; the tools named here are the enforcement.

## 1. Plans reference tracker IDs, never version numbers
A plan (BUILD_PLAN, the migration/retirement plans, Track-F docs) sequences work
by **stable IDs** (`BP-VH1`, `T12`, `P4.a`), never by pre-assigned cut numbers.
The version is assigned **only at cut time**. Pre-assigning ("W3 = Cut 276 =
verdict hardening") goes stale the instant a cut ships something else — which is
exactly what happened when 276 shipped the row selector instead. `TASK_TRACKER`
owns status; the plan owns the spec; the version is decided when the cut lands.

## 2. TASK_TRACKER_DATA.json is the single source; the narratives are history
`TASK_TRACKER_DATA.json` is canonical. `TASK_TRACKER.md` and
`TASK_TRACKER.xlsx` are generated views of that JSON and together form the
human-readable "what's left" list.
`Backlog.md`, `Roadmap.md`, Track-F docs, `BUILD_PLAN` = per-cut history / specs,
not parallel task lists. Anything actionable in a narrative must exist as a
tracker row. **Memory may be stale** — a memory summary said T11 was "next" when
it had been LIVE for 12 versions. Re-derive status from the tracker + source each
session; never act on a memory/plan claim without confirming against the tree.

## 3. JSON -> generated Markdown/XLSX is the gated order
After changing `TASK_TRACKER_DATA.json`, run these in order:

1. `python3 tools/tasktracker_gen.py --render <pack-dir>`
2. `python3 tools/tasktracker_gen.py --audit <pack-dir>`
3. `python3 tools/tasktracker_gen.py --check <pack-dir>`

`--audit` checks section schemas and duplicate IDs; `--check` independently
renders both generated artifacts and rejects drift. `tasktracker_sync.py` is a
legacy recovery/diagnostic tool, not the canonical editing path. A GCW-3 row
once lived in the xlsx but not the md and was caught only by an ad-hoc diff;
the generator gates make that class of drift impossible to miss.

## 4. Canonical plans travel in the pack or land in-tree
A doc referenced as canonical (e.g. Backlog named `PHASE4_RETIREMENT_PLAN.md` as
THE Phase-4 plan) must be reachable at bootstrap — either shipped in-tree under
`docs/` or carried in the session `version.zip`. A canonical doc that lives
nowhere the bootstrap can find it is a latent gap. Plans anchor code references on
**symbols**, never line numbers (they drift ~130 lines across ~60 versions).

## 5. Generated > hand-maintained for safety-relevant lists
The Phase-4 retirement §C test-pin list is **generated** by
`tools/legacy_pin_scan.py`, not hand-written — hand-maintenance is how it went
both stale (already-migrated entries) and incomplete (missed live pins). The
scanner errs toward over-reporting so it can never miss a pin the deletion would
break; the operator confirms each. Run it at the deletion cut, not from memory.

## 6. In-zip STATE is not authoritative; the pack STATE is
`build_release` stamps the in-zip `STATE.json` (built_version, file_count, name)
and, as of v3.66.278, **nulls the un-stampable `zip.sha256`** and **refreshes the
guard SHAs from the built tree**. `live_version` legitimately lags an undeployed
cut (verify_release prints an advisory NOTE, not a failure). The **canonical**
pin — real full-zip sha — lives in the session pack `STATE.json`, validated by
`bd-state` (which now reads the pack copy first, per Fix A). As of v3.66.280
`verify_release` leads its display with `built_version` and labels the in-zip copy
NON-authoritative, so the stale `live_version` can't mislead.

## 7. Versioning policy for non-functional cuts
Pure docs / tooling / hygiene changes (no `/api/health` behavior change) do not
each need their own semver slot — batch them into one periodic **maintenance
cut** rather than a slot per change (277/278/279 burned three slots + three
on-stash suite cycles for changes that touched no runtime). Rules:
- **Guard-touching changes are still isolated** (clean, unambiguous guard-sha
  declaration) even within a batch — that exception is why 278 was solo. A
  maintenance batch with NO guard change (e.g. 280) is the normal case.
- Functional/runtime cuts keep one-slice-one-cut as before.
- Each maintenance cut still runs the full release gate set (it ships tree
  changes); the saving is slot count + suite cycles, not rigor.

## 8. Predict the cut; derive the overlay
`python3 tools/precut_check.py --baseline <live-release.zip>` reports, BEFORE the
bump, what the cut needs: version-consistency, which guard SHAs moved (declare),
which in-sync docs will regen, and the band suite — turning mid-build gate
*failures* (the dep-graph 145→147 trip) into up-front facts. Derive the deploy
overlay from the diff, never by hand:
`python3 tools/make_overlay.py --baseline <prev.zip> --new <release.zip> --out <ov.zip>`.
At close, fold the whole pack ritual into one gated command:
`python3 tools/build_session_pack.py --state <draft-STATE.json> --zip <release.zip>
--pack-dir <dir> --out <pack.zip> --baseline <prev.zip> --overlay <ov.zip>` — it
refreshes STATE's mechanical fields (sha/count/guards) from the zip, prunes
`changes_<N>` to the newest two, gates on STATE-schema required keys + the tracker
drift check, builds the overlay, assembles the pack, and cross-checks with
`bd-state`. You still hand-write the narrative STATE fields (deploy_status /
validation / next / changes_<N>); the tool owns everything mechanical.
`precut_check` and the version-pin scanner are now authoritative: precut reuses
`build_release`'s exclusion walk (same file set the build zips) and runs the real
`scan_version_pins` gate, which now ignores `__version__ == "X"` literals that sit
*inside* a string (fixture data), so a new test's fixture can't trip the build —
no more reword-the-fixture or grow-the-`--ignore`-list workarounds.
