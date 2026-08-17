<!-- verified-against: v3.66.185 -->
# PROJECT KNOWLEDGE IS STATIC — read this before editing anything here

This folder is **machinery**, not state. It is meant to be edited **almost never**.
If you find yourself opening it for a routine release, stop — you're doing it wrong.

## The one rule
**Nothing in /mnt/project may contain a version number or a per-release count.**
Versions, SHAs, file counts, parity numbers, "live target," deploy status, the
current "next" tranche — all of that lives in **STATE.json**, which rides in the
per-session pack (the chat attachment), NOT here. A static doc that says "the 158
zip" or "six guards" or "live target 172" is a bug: it bakes state into machinery,
so it goes stale and drags you back here every release.

## Why this exists
Project knowledge drifted for many releases (158 zips, "six guards," 159-anchored
sandbox notes) precisely because version-shaped facts were written into static
docs. Strip them once, and there is nothing left here that *can* go stale — so you
stop needing to touch this folder.

## Where things live (the split)
| Bucket | Contains | Changes |
|---|---|---|
| **/mnt/project (here)** | scripts, schema, durable cards, operating *philosophy* | ~never (only when the machinery itself changes) |
| **per-session pack** (chat attach) | STATE.json + CONTINUATION/KB_HANDOFF/Backlog/Roadmap/kickoff + delta-spec + wiring specs | every session (cheap — it's data) |
| **stash** | the deployed release zip | every deploy |

## How the machinery stays version-free
The scripts **read** state at runtime; they never embed it:
- `bd-state` / `bd-preflight` and release verification derive version/sha/guards
  from the repository and exact release input.
- No script hardcodes a version. (If you ever add one, you've reintroduced the drift.)
So a new release changes STATE.json in the pack — and every script here keeps
working untouched.

## Self-check (so you never have to hunt for staleness)
Every static .md carries a header line `<!-- verified-against: vN -->`. To find
anything that slipped a version number back in:
```
grep -RIl '3\.66\.[0-9]' /mnt/project --include='*.md' \
  | grep -v -E 'CHANGELOG|history|archive'      # any hit here is suspect
grep -RIL 'verified-against' /mnt/project --include='*.md'   # docs missing the header
```
In steady state both return (near) nothing. If they don't, fix the offending doc
once — don't start a habit of per-release edits.

## When it IS legitimate to edit project knowledge
- A script/tool changes (a real bug like the bd-install stale-frontend fix).
- The operating *philosophy* changes (a new rail, a new gate).
- A durable card gains a genuinely durable fact.
Never for: a version bump, a new parity count, a deploy, or "the current next step."
Those are STATE.json + the pack.
