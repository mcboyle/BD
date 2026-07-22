<!-- verified-against: v3.66.593 -->
# OPERATOR POLICY DECISIONS — Bucket-B dials (@v3.66.593)

<!-- Records the operator's settled choices on the "operator's-call" (Bucket B) constraints, and the -->
<!-- concrete consequences for the build order + governance docs. Bucket-A hard lines (no circumvention, -->
<!-- no redistribution, credential floor, new-host approval) are UNCHANGED and are NOT in scope here. -->
<!-- One item (scope) requires a PROJECT_CHARTER wording change — shown below for sign-off, not yet applied. -->

## The three decisions

| Dial | Choice | Meaning |
|---|---|---|
| **Autonomy (approved hosts)** | **L2 — act + notify** | BD auto-acts (refresh/promote) on previously-approved hosts and notifies after; reversible via A0; no mandatory hold-window (that's L3). |
| **Local-capture redaction** | **Floor only** | Only the credential floor is stripped on disk; functional URLs / signing params / path-signed segments are RETAINED locally. |
| **Operating scope** | **Full enumeration of approved hosts (level 4)** | Autonomously discover, enumerate, and queue candidates on already-approved hosts **to any depth** (a whole library / category); new hosts still require approval. |

---

## Consequence 1 — A0 (verified backup) becomes the FIRST build item

L2 "act" = an autonomous write, and the standing rule is *every autonomous write must be
backed-up-and-restorable first*. A0 is currently PARTIAL, so **L2 is the target but not yet safe to
run**; BD operates effectively at L1 (stage + confirm) until A0 lands.

- `AUTOMATION_PROGRAM_PLAN.md` updated: added the L0–L4 autonomy dial (set to L2), and A0 is the
  explicit unlock for L2. A2 (auto-refresh) / A5 (auto-promote) are the L2 actions, already A0-gated.
- **Action:** build **A0+ verified backup** (restore-and-assert into scratch before the write) first.
  Nothing at L2 runs before it.

## Consequence 2 — local redaction = floor-only → scrubber mandatory on share; Phase C parked

Floor-only is fine on security grounds (the floor is the part that matters). But floor-only local
captures contain **live signed URLs**, so:

- The Bucket-A **"never circulate a raw capture"** line is now genuinely load-bearing. Clean two-tier
  model: **minimal local redaction + aggressive scrub-on-share.** `capture_scrub.py` (offline,
  self-verifying) is **mandatory before any capture leaves the machine**; synthetic fixtures remain
  the only thing committed/circulated. (`CAPTURE_SHARING_POLICY.md` already encodes this — it now
  becomes the primary control, not a convenience.)
- **Phase C (F2 hardening) is PARKED for personal use.** That whole plan hardened redaction *beyond*
  the floor, which is being deliberately opted out of locally. Only **#9 floor-invariance
  verification** still applies (the floor must never regress). `PHASE_C_HARDENING_PLAN.md` stays on
  file; revisit only if the sharing/redaction posture changes (e.g. captures ever leave the host
  unscrubbed, or a multi-user mode appears).

## Consequence 3 — PROJECT_CHARTER scope guardrail wording change (APPROVED + APPLIED @593)

"Full enumeration of approved hosts" (level 4) contradicts the charter's current "not a whole-site crawler / targeted
operation" wording. **Operator approved @593; the edit below is now APPLIED in `PROJECT_CHARTER.md`
(this bundle).** The before→after is retained for the record.

**CURRENT (charter, Guardrails section):**
> - **Targeted operation.** The tool acts on operator-provided URLs and approved candidates. It is
>   not a whole-site crawler.

**PROPOSED (level 4 — full enumeration of approved hosts):**
> - **Scoped operation.** The tool operates on operator-provided URLs and **autonomously discovers,
>   enumerates, and queues candidates on already-approved hosts to any depth** (e.g. a performer's
>   whole library or a whole category on a host the operator has approved). Discovering or
>   runtime-enabling a **new host** requires explicit operator approval. It is **not a general /
>   whole-web crawler** — all discovery stays confined to already-approved hosts and remains bounded
>   by **rate-limiting / politeness** (never burden a site's infrastructure) and
>   **review-on-uncertainty** (navigation-looking candidates and generic selectors such as `a[href]`
>   are rejected unless they resolve to strong media/download evidence).

What this preserves (the compensating guardrails that must stay firm at broader scope):
1. **New-host approval** — discovery widens *within* approved hosts, never auto-approves a new host.
2. **Politeness / rate-limiting** — the part that affects *others'* infra; non-negotiable.
3. **Review-on-uncertainty** — reject nav-looking candidates + generic selectors; strong-evidence
   only. This is what keeps "broader discovery" from becoming "grab everything."

---

## Unchanged (Bucket A — restated so the delta is unambiguous)

- No access-control / DRM / CAPTCHA / paywall circumvention.
- No redistribution — captures local-only; synthetic fixtures only for circulation.
- Credential floor unconditional (stripped locally even under floor-only; verified every release).
- Authorized/authenticated sessions only; operator owns ToS/legal.
- Registry sites (adult cam) never mimicked in fixtures or tested live.

## Bucket C — RESOLVED (@593)
- **Retention / takedown stance** — DECIDED: permanent local archive, no auto-deletion (incl. on
  source removal), manual-only, no dedicated purge tool. Recorded in `RETENTION_AND_TAKEDOWN_POLICY.md`,
  with two riders: (a) manual delete must be *complete* (purge file + DB row + thumbnails/derived +
  cached capture) — recommend a small `delete_archived()` to guarantee it; (b) a non-negotiable floor:
  unlawful/non-consensual content is deleted promptly regardless of the keep-forever default.
- **Autonomy guardrails (AR4 blast/rate limits, AA5 graceful off-switch)** — not policy dials; they
  are build-items already captured in `AUTOMATION_PROGRAM_PLAN.md` §6, and AR4 is now called out as
  mandatory alongside A-DISCO (§7) given the level-4 enumeration scope.
- **Plugin supply-chain governance** — only relevant if the plugin marketplace / self-update is ever
  built; captured as a risk flag in `PLUGIN_V3_PLAN.md` §6 (signing + operator-pinned key, never
  auto-apply). Deferred until/unless that track is chosen.

---

## Toolchain-governance decisions (v3.66.774 session)

Two long-open "one-line decision" items, both resolving to **leave as-is with rationale** (no code change):

| Item | Choice | Rationale |
|---|---|---|
| **#14 graph content-pin (stash certification)** | **Require an external deployment-source pin; never ship the DB or pin** | The trust anchor is `/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256`, outside the install tree. Deployment acceptance derives it once from the exact installed source. Routine certification sets `BD_REQUIRE_GRAPH_HASH=1`, rebuilds the graph in a temporary directory, compares canonical rows, and deletes the DB. Mutable audit-state DB pins remain separate and advisory. |
| **#15 tool-subtraction (bd-audit-gate.py)** | **KEEP the tool; budget floor stays 247** | bd-audit-gate.py is NOT a dead stub — it is a functional composite gate running defect_patterns --check + invariants --check + review_state --check. "stub" in its docstring meant "extensible" (fuzz/differential replays can be added later), not "empty". There is no clear subtraction candidate, so the 247 leaf-budget floor holds unchanged. |

Decision #14 was superseded for the dependency/graph hardening release. Generate
the external pin only after the release archive has passed its independent
SHA/version/build gates on stash:

```bash
set -euo pipefail
release_root=/home/mboyle/BulkDownloader
test -d "$release_root"
cd "$release_root"
operator_group=$(id -gn)
graph_tmp=$(mktemp -d)
cleanup_graph_tmp() { rm -rf -- "${graph_tmp:?}"; }
trap cleanup_graph_tmp EXIT
venv/bin/python tools/l0_extract.py --root "$release_root" \
  --db "$graph_tmp/KNOWLEDGE_GRAPH.db"
venv/bin/python tools/graph_build.py \
  --db "$graph_tmp/KNOWLEDGE_GRAPH.db" \
  --hash-pin "$graph_tmp/KNOWLEDGE_GRAPH.content.sha256" \
  --write-hash
sudo install -d -o root -g "$operator_group" -m 0750 \
  /var/lib/bulkdownloader/validation
sudo install -o root -g "$operator_group" -m 0640 \
  "$graph_tmp/KNOWLEDGE_GRAPH.content.sha256" \
  /var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256
venv/bin/python tools/graph_build.py \
  --db "$graph_tmp/KNOWLEDGE_GRAPH.db" \
  --hash-pin /var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256 \
  --check-hash
```

Then run routine certification with `BD_REQUIRE_GRAPH_HASH=1`. Never write or
refresh the pin immediately before a routine check: doing so would bless the
very drift the gate is meant to detect. The release ZIP excludes the graph DB,
its SQLite sidecars, and both legacy and deployment graph-pin filenames.

Decision #15 remains settled; do not re-raise it without new evidence.
