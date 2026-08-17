<!-- verified-against: v3.66.818 -->
# README_KB — start here

This is the static, version-agnostic project-knowledge set for BulkDownloader.

**Read order lives in `KB_ACTIVE_INDEX.md` -- start there.**
In short: `bd_starting_message.txt` (halt guard) -> `PROJECT_CHARTER.md` -> `PROJECT_GOALS.md` ->
`AUTOMATION_POLICY.md` -> `CLAUDE.md` -> `SANDBOX.md` -> `SCHEMAS.md` ->
`project-knowledge/IMPROVEMENT_BACKLOG.md` -> `REPTYLE_CAPTURE_RUNBOOK.md` ->
the reference cards.

**Current state is in the TREE.** Version: `bulk_downloader/__init__.py`. Guard SHAs: `guards.json`,
checked with `bd-guardcheck`. Counts and parity: re-derive with `bd-factcheck` / the regen tools --
never quote them from a doc, including this one.

**There is no handoff zip.** Earlier revisions of this file told you to STOP until a per-session
`version.zip` (newest `KB_HANDOFF_v3_66_<n>.md` + `STATE.json`, validated by `bd-state`) was
attached. None of those artifacts exist in this tree; the git checkout IS the handoff. Do not wait
for one -- but do not read this static set as current state either: re-derive every figure at
decision time (CLAUDE.md section 1).

Key cards: `GATE_AUTHORITY.md` (guards / in-sync gates / deploy-excluded), `TOUCHED_FILE_TO_TEST.md`
(band-coverage map), `0_INDEX.md` (the reference-card index), `KNOWN_FLAKES.md`.
