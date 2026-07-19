<!-- verified-against: v3.66.276 -->
# README_KB — start here

This is the static, version-agnostic project-knowledge set for BulkDownloader.

**Read order and the static-vs-`version.zip` split live in `KB_ACTIVE_INDEX.md` — start there.**
In short: `bd_starting_message.txt` (halt guard) → `PROJECT_CHARTER.md` → `PROJECT_GOALS.md` →
`AUTOMATION_POLICY.md` → `PROJECT_OPERATING_INSTRUCTIONS.md` → `SANDBOX.md` → `SCHEMAS.md` → **(from
the version.zip)** newest `KB_HANDOFF_v3_66_<n>.md`, then `Backlog`/`Roadmap`/`TASK_TRACKER` →
`REPTYLE_CAPTURE_RUNBOOK.md` → the reference cards.

**Current state is NOT here.** Versions, guard SHAs, parity, and "what's next" ride the per-session
`version.zip` (newest `KB_HANDOFF_v3_66_<n>.md` + `STATE.json`, validated by `bd-state`). The
static set is set once and changed only when one of these docs genuinely changes.

**Halt guard:** if no `KB_HANDOFF_v3_66_*.md` is in uploads, the `version.zip` wasn't attached —
STOP and request it. Do not proceed from static project knowledge alone.

Key cards: `GATE_AUTHORITY.md` (guards / in-sync gates / deploy-excluded), `TOUCHED_FILE_TO_TEST.md`
(band-coverage map), `0_INDEX.md` (the reference-card index), `KNOWN_FLAKES.md`.
