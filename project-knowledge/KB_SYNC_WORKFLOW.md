<!-- verified-against: v3.66.754 -->
# Static-KB sync workflow (bd-kb-sync)

How the bd-* scripts keep the **static project knowledge** (the files pasted into the
Claude project) from silently going stale. This is the "static is a refreshed cache;
the pack is the live edge; the manifest is the truth" model.

## Why this exists

Static project knowledge is always-on context, but it is a CACHE: durable docs
(`ADVANCED_PROJECT_KNOWLEDGE`, `GLOSSARY`, `ARCHITECTURE_MAP`, the
reference cards, ...) evolve mid-session. Nothing forced the pasted copy to be refreshed,
so it drifted -- the project's named nemesis (a stale cache of a derived fact). `bd-kb-sync`
makes the drift visible, carries the updated docs volatile so nothing is lost, and produces
a paste-ready update.

## The three roles

- **Static** = the pasted set. Always-on baseline. Refreshed by Matt when convenient.
- **The pack** (`version.zip`) = the live edge. Carries the newest durable docs +
  `STATIC_KB_MANIFEST.json` + (on drift) `BulkDownloader_project_files_v<ver>.zip` + the
  `PROJECT_KNOWLEDGE_UPDATE.md` flag. So the NEXT session has the latest even before a re-paste.
- **`STATIC_KB_MANIFEST.json`** = sha256 of every static file = what SHOULD be in static now.
  It never tracks itself or the update flag.

## The loop

1. **Mid-session**, the operator maintains the static-KB working copy (for
   example `$BD_STATIC_KB`) and edits durable docs there as they change.
2. **Session close** -- run `bd-kb-sync stage <static_kb_working_dir> --out <out>`:
   - it diffs the working copy vs the manifest, **reseeds the
     manifest to the new state**, and (on drift) writes `BulkDownloader_project_files_v<ver>.zip`
     + `PROJECT_KNOWLEDGE_UPDATE.md` into `--out`, printing **STATIC KB UPDATE REQUIRED**;
   - the release build then carries those artifacts into the release evidence.
3. **Matt re-pastes** static from `BulkDownloader_project_files_v<ver>.zip` (delete all,
   paste). The pasted set now matches the manifest inside it.
4. **Next bootstrap** -- `bd-boot` runs `bd-kb-sync verify /mnt/project` (integrity: catches a
   partial/edited paste) and `bd-kb-sync diff /mnt/project <pack_manifest>` (freshness: catches
   "re-paste owed"). Non-gating -- a stale static set is a follow-up, not a blocker; the pack's
   copies win for the session.

## Modes (all stdlib, no network, no BD imports)

| mode | does |
|---|---|
| `bd-kb-sync seed <root> --version vX.Y.Z` | (re)generate the manifest from a known-good set |
| `bd-kb-sync check <root>` | diff `<root>` vs its manifest; exit 1 on drift |
| `bd-kb-sync verify <root>` | strict integrity: `<root>` must match its manifest exactly |
| `bd-kb-sync stage <root> --out <dir> --version vX.Y.Z` | build the paste-ready zip + flag + reseed |
| `bd-kb-sync diff <root> <other_manifest>` | freshness: is `<root>` behind another (newer) manifest |

## WARNING -- `stage` RESEEDS THE MANIFEST BEHIND THE CALLER (KBSYNC-STAGE, P1)

`stage` (and `seed`) **rewrite `STATIC_KB_MANIFEST.json` to match the working copy**, then
any freshness/integrity check compares the working copy against *the manifest it just wrote*
-- so it is GREEN whenever they agree, INCLUDING when the pasted PK is far behind. The
"is this fresh?" question has an empty denominator after a stage: the thing it would compare
against was just overwritten.

**Consequence:** never trust `stage`'s own output to tell you the paste is current, and never
derive the STATE pin from the working copy. Pin from **the manifest INSIDE the built zip**,
and hand-verify the delta by a real hash-diff against the *currently pasted* set (what changed
for the operator), not against the freshly-reseeded working copy.

This is why the session-close order is: build the zip -> pin STATE from the ZIP's manifest ->
hand-write the update note from a hash-diff vs `/mnt/project` -> only then present. The boot
gate (`verify --state`) is the honest check: it compares the manifest's sha to the EXTERNAL
pin held in STATE, which `stage` cannot reach in and rewrite.

## First-time / re-baseline

After a clean paste, the manifest in the pasted set IS the baseline (it shipped in the zip).
To re-baseline by hand: `bd-kb-sync seed /path/to/static_set --version v3.66.<n>`.

## Guarantees

- A durable doc change is surfaced by manifest drift and the staged paste-ready update.
- No session loses an update: the pack carries the newer copies regardless of re-paste timing.
- A partial or stale paste is caught at the next `bd-boot` (integrity + freshness), not silently.
