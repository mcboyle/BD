// Guided mode Cut 2 (P3) — pure logic for the live drift + repair + post-promote
// verify shells. Everything here is pure/deterministic except the one thin
// fetchDriftStatus wrapper, so the drift-compare state machine, the repair-summary
// builder, and the post-promote-verify state machine are all vitest-testable.
//
// IMPORTANT testability boundary: the LIVE behaviors these shells drive — the
// actual per-slot re-pick against the held-open session, the real drift
// resolution on a live page, and the real runner download in the post-promote
// verify — are NOT exercised here and are NOT sandbox-testable. They are verified
// in the noVNC / sentinel / operator flow. This module models only the state and
// copy around those operator-verified actions.
//
// No new backend route: repair-entry reads the existing GET
// /api/selector_drift/status/<sid>; the per-slot drift check reuses the existing
// interactive pick plumbing; post-promote verify reuses the existing queue +
// history-watch. (Plan decision #3: add a live-resolve route only if the cheap
// pick-based check can't be done with existing plumbing — it can.)

import { apiGet } from "@/lib/api-client";

import { mapVerdict, type VerdictRow } from "@/lib/guidedCapture";

// ─────────────────────────── Drift compare (at Review) ───────────────────────────
// The candidate's checkable selector slots. The "cheap pick-based" compare asks,
// per slot, "does this still resolve on the live session?" — answered by whether
// an operator re-pick on the canvas matches. We model the per-slot result; the
// re-pick itself is operator-driven.

export type DriftSlotKey = "download_trigger" | "row_selectors" | "play_button";

export const DRIFT_SLOTS: DriftSlotKey[] = [
  "download_trigger",
  "row_selectors",
  "play_button",
];

export const DRIFT_SLOT_LABEL: Record<DriftSlotKey, string> = {
  download_trigger: "Download trigger",
  row_selectors: "Row selector",
  play_button: "Play button",
};

export type SlotResolution = "unchecked" | "resolved" | "drifted";

export type DriftCompareState = "idle" | "comparing" | "clean" | "drifted";

/** Which slots came back drifted (re-pick failed / changed). */
export function driftedSlots(
  slots: Partial<Record<DriftSlotKey, SlotResolution>>,
): DriftSlotKey[] {
  return DRIFT_SLOTS.filter((k) => slots[k] === "drifted");
}

/** State machine for the in-flow compare. */
export function driftCompareState(
  slots: Partial<Record<DriftSlotKey, SlotResolution>>,
  /** Only the slots the candidate actually defines are checkable. */
  applicable: DriftSlotKey[] = DRIFT_SLOTS,
): DriftCompareState {
  const vals = applicable.map((k) => slots[k] ?? "unchecked");
  if (vals.every((v) => v === "unchecked")) return "idle";
  if (vals.some((v) => v === "unchecked")) return "comparing";
  return vals.some((v) => v === "drifted") ? "drifted" : "clean";
}

export interface DriftCompareView {
  state: DriftCompareState;
  label: string;
  detail: string;
}

export function driftCompareView(
  slots: Partial<Record<DriftSlotKey, SlotResolution>>,
  applicable: DriftSlotKey[] = DRIFT_SLOTS,
): DriftCompareView {
  const st = driftCompareState(slots, applicable);
  const drifted = driftedSlots(slots);
  switch (st) {
    case "idle":
      return {
        state: st,
        label: "Not compared yet",
        detail:
          "Re-pick each slot on the live session to confirm it still resolves.",
      };
    case "comparing":
      return {
        state: st,
        label: "Comparing…",
        detail: "Re-pick the remaining slots on the live canvas.",
      };
    case "clean":
      return {
        state: st,
        label: "No drift",
        detail: "Every checked selector still resolves on the live session.",
      };
    case "drifted":
      return {
        state: st,
        label: `${drifted.length} selector${drifted.length === 1 ? "" : "s"} drifted`,
        detail: `Re-pick: ${drifted
          .map((k) => DRIFT_SLOT_LABEL[k])
          .join(", ")}.`,
      };
  }
}

// ─────────────────────────── Repair as the entry point ───────────────────────────
// When guided mode opens on an already-enabled site, read the persisted drift
// monitor and, if the site is failing/stale, lead with "repair these N" instead
// of re-walking all 7 steps. Built from GET /api/selector_drift/status/<sid>.

export interface DriftStatus {
  site_id?: string;
  consecutive_failures?: number;
  last_failure_ts?: number | null;
  last_success_ts?: number | null;
  last_selector?: string;
  last_url?: string;
  flagged_stale?: boolean;
}

export interface RepairSummary {
  /** True when the site is worth landing on Repair rather than a fresh walk. */
  needed: boolean;
  flaggedStale: boolean;
  consecutiveFailures: number;
  lastSelector: string;
  lastUrl: string;
  headline: string;
  /** Whether the candidate's API host looks unchanged (caller-supplied signal). */
  apiHostChanged: boolean;
  /** The slot to jump the operator to first. */
  jumpSlot: DriftSlotKey;
}

/** Build the repair landing. Returns needed=false when there's nothing to repair. */
export function repairSummary(
  drift: DriftStatus | null | undefined,
  opts: { apiHostChanged?: boolean } = {},
): RepairSummary {
  const cf = drift?.consecutive_failures ?? 0;
  const flagged = !!drift?.flagged_stale;
  const lastSelector = drift?.last_selector || "";
  const lastUrl = drift?.last_url || "";
  const apiHostChanged = !!opts.apiHostChanged;
  const needed = flagged || cf > 0;
  // The download/row path is the usual culprit for zero-match download failures.
  const jumpSlot: DriftSlotKey = "row_selectors";
  let headline: string;
  if (!needed) {
    headline = "No drift recorded — this site's selectors are healthy.";
  } else {
    const n = cf || 1;
    const hostBit = apiHostChanged
      ? "API host changed"
      : "API host unchanged";
    headline =
      `${n} consecutive download failure${n === 1 ? "" : "s"} recorded` +
      (flagged ? " (flagged stale)" : "") +
      `; ${hostBit}. Repair the drifted selectors instead of re-walking all steps.`;
  }
  return {
    needed,
    flaggedStale: flagged,
    consecutiveFailures: cf,
    lastSelector,
    lastUrl,
    headline,
    apiHostChanged,
    jumpSlot,
  };
}

// ─────────────────────────── Post-promote live verify (Step 8) ───────────────────────────
// After enable, offer "queue one real download and watch it land" — the loop the
// OPV runbook does by hand. Reuses the queue + the history watch. The real runner
// download is operator-verified; this is just the state machine + copy around it.

export type VerifyState = "idle" | "queued" | "watching" | "verified" | "failed";

export interface VerifyView {
  state: VerifyState;
  label: string;
  detail: string;
  done: boolean;
}

/** Map a polled history row to the terminal verify outcome (reuses the 7-state
 *  verdict map: a pass — including a persist-off probe — is "verified"). */
export function mapVerifyRow(row: VerdictRow | null | undefined): VerifyState {
  if (!row || !row.status) return "watching";
  const v = mapVerdict(row);
  if (v.pass) return "verified";
  if (v.state === "FAILED") return "failed";
  return "watching"; // PENDING / NEEDS_REVIEW: still in flight from the verify POV
}

export function verifyView(state: VerifyState): VerifyView {
  switch (state) {
    case "idle":
      return {
        state,
        label: "Not verified yet",
        detail:
          "Optional: queue one real download against the just-enabled template and watch it land.",
        done: false,
      };
    case "queued":
      return {
        state,
        label: "Queued…",
        detail: "Queued a real download on the runner.",
        done: false,
      };
    case "watching":
      return {
        state,
        label: "Watching the runner…",
        detail: "Waiting for the download to land in history.",
        done: false,
      };
    case "verified":
      return {
        state,
        label: "Verified live",
        detail: "The enabled template downloaded a real file end-to-end.",
        done: true,
      };
    case "failed":
      return {
        state,
        label: "Verify failed",
        detail:
          "The enabled template didn't download — check drift/credentials and re-teach.",
        done: true,
      };
  }
}

// ─────────────────────────── The one client call (read-only, existing route) ───────────────────────────

/** GET /api/selector_drift/status/<sid> — the persisted drift monitor for one
 *  site. Read-only; existing route. Full /api literal prefix so the parity
 *  scanner credits it spa_wired. */
export async function fetchDriftStatus(sid: string): Promise<DriftStatus> {
  return apiGet<DriftStatus>(`/api/selector_drift/status/${sid}`);
}
