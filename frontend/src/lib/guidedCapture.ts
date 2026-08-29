// Guided mode for the Capture workflow — pure logic + the 3 preflight/widening
// client calls. Frontend-only, no new authority: R1/R2 are read-only reports of
// checks the runtime already enforces; R3 is the one write and is confirm-gated
// + audited server-side (this client always sends confirm:true explicitly, and
// the UI pairs it with a two-step operator confirm).
//
// Everything in this file is pure/deterministic except the three thin
// apiGet/apiPost wrappers, so the step model, verdict map, copy map, and
// readiness aggregation are unit-testable under vitest (jsdom).

import { apiGet, apiPost } from "@/lib/api-client";

export type SceneCrawlState =
  | "IDLE"
  | "RUNNING"
  | "COMPLETED"
  | "NOT_LOGGED_IN"
  | "FAILED";

export interface SceneCrawlDefaults {
  listing_url: string;
  newest_n: number;
  max_pages?: number;
  max_scrolls?: number;
  delay_s?: number;
  title_fetch_limit?: number;
}

export interface SceneCrawlStatus {
  ok?: boolean;
  run_id?: string;
  site_id?: string;
  state: SceneCrawlState;
  discovered: number;
  queued: number;
  pages_walked: number;
  zero_scenes_found: boolean;
  error?: string;
  defaults?: SceneCrawlDefaults;
}

export interface SceneCrawlView {
  tone: "neutral" | "info" | "success" | "warning" | "danger";
  label: string;
}

/** Fail-closed crawler copy: logged out is never rendered as an empty library. */
export function sceneCrawlView(status: SceneCrawlStatus): SceneCrawlView {
  if (status.state === "NOT_LOGGED_IN") {
    return {
      tone: "warning",
      label: "Not logged in — refresh this site's authenticated session before discovery.",
    };
  }
  if (status.state === "FAILED") {
    return {
      tone: "danger",
      label: status.error ? `Discovery failed: ${status.error}` : "Discovery failed.",
    };
  }
  if (status.state === "RUNNING") {
    return { tone: "info", label: "Discovering scenes… scrolling and walking pages." };
  }
  if (status.state === "COMPLETED") {
    if (status.zero_scenes_found) {
      return {
        tone: "neutral",
        label: `No scenes found after ${status.pages_walked} ${status.pages_walked === 1 ? "page" : "pages"}.`,
      };
    }
    return {
      tone: "success",
      label: `${status.discovered} discovered · ${status.queued} queued · ${status.pages_walked} ${status.pages_walked === 1 ? "page" : "pages"}`,
    };
  }
  return { tone: "neutral", label: "Ready to discover the newest scenes." };
}

export interface StartSceneCrawlRequest {
  site_id: string;
  listing_url: string;
  newest_n: number;
  max_pages: number;
  max_scrolls: number;
  delay_s: number;
  title_fetch_limit: number;
}

export interface SceneCrawlStartResponse {
  ok: boolean;
  run_id: string;
  site_id: string;
  state: "RUNNING";
}

export async function startSceneCrawl(
  request: StartSceneCrawlRequest,
): Promise<SceneCrawlStartResponse> {
  return apiPost<SceneCrawlStartResponse>("/api/discovery/scenes/start", request);
}

export async function fetchSceneCrawlStatus(
  siteId: string,
): Promise<SceneCrawlStatus> {
  const query = new URLSearchParams({ site_id: siteId });
  return apiGet<SceneCrawlStatus>(`/api/discovery/scenes/status?${query.toString()}`);
}

export type StepKey =
  | "setup"
  | "capture"
  | "build"
  | "inspect"
  | "test"
  | "review"
  | "promote";

export const GUIDED_STEP_ORDER: StepKey[] = [
  "setup",
  "capture",
  "build",
  "inspect",
  "test",
  "review",
  "promote",
];

// Per-step coaching scaffold: Purpose -> Do-this -> Success looks like.
// (Preconditions + If-it-fails are computed dynamically from context below.)
export interface StepCopy {
  purpose: string;
  doThis: string;
  success: string;
  /** Concrete, non-generic primary-button label. */
  primary: string;
}

export const STEP_COPY: Record<StepKey, StepCopy> = {
  setup: {
    purpose:
      "Create the site and store its login as an encrypted @cred reference " +
      "before any teaching.",
    doThis:
      "Name the site, paste the login URL, set the download folder, and " +
      "(optionally) the password.",
    success: "Site created · creds stored · download root OK.",
    primary: "Create site & continue",
  },
  capture: {
    purpose:
      "Open the authenticated browser session in the canvas and log in inside it " +
      "once — it stays open for every later step.",
    doThis: "Start the capture, then log in inside the live canvas.",
    success: "Session held open · logged in.",
    primary: "Session open — continue",
  },
  build: {
    purpose:
      "Prove the live download affordance, enumerate its resolution ladder, " +
      "and compare DOM findings with captured requests.",
    doThis: "Navigate to a scene, click Learn from live page, then optionally crawl its listing.",
    success: "BAR/DROPDOWN shape recorded · selector proven · policy applied.",
    primary: "Affordance learned — continue",
  },
  inspect: {
    purpose:
      "Refine selectors against the still-live page — pick an element off the " +
      "canvas, suggest rows, watch the HUD mirror.",
    doThis: "Pick the required selector slots off the live canvas (or skip optionals).",
    success: "Required selector slots resolved.",
    primary: "Selectors set — continue",
  },
  test: {
    purpose:
      "Prove the draft actually downloads from a real URL (persist off — the " +
      "verdict is the point, not the file).",
    doThis: "Run a test extract against a real content URL and watch the verdict.",
    success: "Verdict is a pass (media validated).",
    primary: "Verdict OK — continue",
  },
  review: {
    purpose:
      "Assemble the review candidate — merge proven api + row_selectors, " +
      "optionally take AI selector suggestions (one-by-one, review-gated).",
    doThis: "Load the candidate; accept or reject each AI suggestion individually.",
    success: "Candidate assembled · 0 unreviewed AI edits.",
    primary: "Candidate ready — continue",
  },
  promote: {
    purpose:
      "The deliberate enable. Stays an explicit operator confirm; preflighted " +
      "for blocked terms and an enabled-gold overwrite first.",
    doThis: "Review the preflight, then enable.",
    success: "Enabled · live.",
    primary: "Enable & finish",
  },
};

// ─────────────────────────── Step readiness checklist ───────────────────────────
// Each unmet item names the one thing missing so the disabled-primary tooltip
// can surface it. The context is whatever the component already tracks; this is
// pure so it is fully unit-testable.

export interface GuidedCtx {
  setupNameOk: boolean;
  setupUrlOk: boolean;
  downloadRootOk: boolean; // R1 verdict (true also when the field is empty)
  sessionLive: boolean;
  loggedIn: boolean;
  contentPageVisited: boolean;
  liveLearningAttempted: boolean;
  resolutionPolicySatisfied: boolean;
  draftBuilt: boolean;
  requiredSelectorsResolved: boolean;
  verdictState: VerdictState;
  candidateAssembled: boolean;
  unreviewedAiEdits: number;
  promotePreflightOk: boolean;
}

export interface ChecklistItem {
  ok: boolean;
  label: string;
}

export function stepChecklist(step: StepKey, c: GuidedCtx): ChecklistItem[] {
  switch (step) {
    case "setup":
      return [
        { ok: c.setupNameOk, label: "Site name set" },
        { ok: c.setupUrlOk, label: "Login URL is a valid URL" },
        { ok: c.downloadRootOk, label: "Download folder under an allowed root" },
      ];
    case "capture":
      return [
        { ok: c.sessionLive, label: "Session is open" },
        { ok: c.loggedIn, label: "Logged in inside the session" },
      ];
    case "build":
      return [
        { ok: c.sessionLive, label: "Session is open" },
        { ok: c.contentPageVisited, label: "A content page has been visited" },
        {
          ok: c.liveLearningAttempted,
          label: "Learn the live download affordance (FOUND or UNKNOWN)",
        },
        {
          ok: c.resolutionPolicySatisfied,
          label: "Learned options satisfy quality_preference and min_resolution",
        },
      ];
    case "inspect":
      return [
        { ok: c.draftBuilt, label: "Draft built" },
        {
          ok: c.requiredSelectorsResolved,
          label: "Required selector slots resolved (or skipped)",
        },
      ];
    case "test":
      return [
        {
          ok: c.verdictState === "DONE" || c.verdictState === "NEEDS_REVIEW",
          label: "A test verdict is in (pass, or override Needs review)",
        },
      ];
    case "review":
      return [
        { ok: c.candidateAssembled, label: "Candidate assembled" },
        { ok: c.unreviewedAiEdits === 0, label: "No unreviewed AI edits" },
      ];
    case "promote":
      return [{ ok: c.promotePreflightOk, label: "Promote preflight is green" }];
    default:
      return [];
  }
}

/** First unmet checklist label (for the disabled-primary tooltip), or null. */
export function firstBlocker(step: StepKey, c: GuidedCtx): string | null {
  const miss = stepChecklist(step, c).find((i) => !i.ok);
  return miss ? miss.label : null;
}

export function stepReady(step: StepKey, c: GuidedCtx): boolean {
  return firstBlocker(step, c) === null;
}

// ─────────────────────────── Stepper status map ───────────────────────────

export type StepStatus = "done" | "current" | "blocked" | "locked";

export function stepStatus(
  step: StepKey,
  current: StepKey,
  c: GuidedCtx,
): StepStatus {
  const si = GUIDED_STEP_ORDER.indexOf(step);
  const ci = GUIDED_STEP_ORDER.indexOf(current);
  if (si < ci) return stepReady(step, c) ? "done" : "blocked";
  if (si === ci) return "current";
  return "locked";
}

// ─────────────────────────── Test verdict mapping ───────────────────────────
// The richer verdict replacing the binary `passed`. A persist-off probe that
// aborts after first bytes (status done, size 0) is a PASS rendered as
// "media validated, not saved" — not a scary zero-byte failure.

export type VerdictState = "PENDING" | "DONE" | "NEEDS_REVIEW" | "FAILED";

export interface VerdictView {
  state: VerdictState;
  /** Short badge label. */
  label: string;
  /** One-line human detail. */
  detail: string;
  /** True when the operator may advance past Test. */
  pass: boolean;
}

export interface VerdictRow {
  status?: string;
  file_size?: number;
  filename?: string;
  message?: string;
}

export function mapVerdict(row: VerdictRow | null | undefined): VerdictView {
  if (!row || !row.status) {
    return {
      state: "PENDING",
      label: "No verdict yet",
      detail: "Run a test, or check History if the run is still going.",
      pass: false,
    };
  }
  const s = String(row.status).toLowerCase();
  const size = row.file_size ?? -1;
  if (s === "needs_review") {
    return {
      state: "NEEDS_REVIEW",
      label: "Needs review",
      detail:
        row.message ||
        "Below threshold or ambiguous — approve in Needs review, or adjust the ladder and re-test.",
      pass: false,
    };
  }
  if (s === "failed" || s === "error" || s === "cancelled") {
    return {
      state: "FAILED",
      label: "Failed",
      detail: row.message || "No media downloaded.",
      pass: false,
    };
  }
  if (s === "done") {
    if (size > 0) {
      return {
        state: "DONE",
        label: "Media validated",
        detail: row.filename
          ? `Saved ${row.filename}.`
          : "A real file downloaded.",
        pass: true,
      };
    }
    // done + 0 bytes = persist-off probe aborted after first bytes — a pass.
    return {
      state: "DONE",
      label: "Media validated, not saved",
      detail:
        row.message ||
        "Probe OK: first bytes received, aborted — no file saved (persist off).",
      pass: true,
    };
  }
  // running / pending / queued / anything else still in flight
  return {
    state: "PENDING",
    label: "Probing…",
    detail: row.message || "The run is still going.",
    pass: false,
  };
}

// ─────────────────────────── Failure -> cause -> fix copy ───────────────────────────
// No raw status strings or import errors surfaced. Each known failure maps to a
// plain sentence + the remedy. Unknown reasons fall through to a generic line.

export interface FailureHint {
  sentence: string;
  fix: string;
}

export function failureHint(reason: string | null | undefined): FailureHint {
  const r = String(reason || "").toLowerCase();
  if (!r) {
    return { sentence: "Something didn't complete.", fix: "Retry the step." };
  }
  if (r.includes("allowed root") || r.includes("allowlist") || r.includes("path")) {
    return {
      sentence:
        "The download folder isn't under an allowed root — Test will save nothing and Promote will be blocked.",
      fix: "Add this root to the allowlist, or pick a folder under one.",
    };
  }
  if (r.includes("blocked term") || r.includes("bad_term")) {
    return {
      sentence: "Promote is blocked: a blocked term is present in reusable URL/API material.",
      fix: "Go back to Review and remove the offending field.",
    };
  }
  if (r.includes("unsafe selector") || r.includes("lint")) {
    return {
      sentence: "A selector is too generic to be safe (root/nav/bare tag).",
      fix: "Re-pick a more specific row selector in Inspect.",
    };
  }
  if (r.includes("challenge")) {
    return {
      sentence: "A challenge was detected on the live page.",
      fix: "Solve it by hand in the canvas (manual handoff), then continue — the flow is paused, not failed.",
    };
  }
  if (r.includes("auth") || r.includes("login") || r.includes("credentials")) {
    return {
      sentence: "The session looks logged out or the credentials didn't take.",
      fix: "Re-open the session and log in again, or check the stored cred.",
    };
  }
  if (r.includes("resolution") || r.includes("threshold")) {
    return {
      sentence: "The media came back below the resolution threshold.",
      fix: "Change quality_preference or min_resolution, then learn and plan again. Excluded media cannot be overridden silently.",
    };
  }
  if (r.includes("no verdict") || r.includes("timed out") || r.includes("timeout")) {
    return {
      sentence: "No verdict landed in time — a large file can outlast the watch.",
      fix: "Check History; if it lands as done there, re-test or override.",
    };
  }
  if (r.includes("not found") || r.includes("draft")) {
    return {
      sentence: "The draft couldn't be located.",
      fix: "Rebuild the draft from the session.",
    };
  }
  return {
    sentence: "The step didn't complete.",
    fix: "Retry, or drop to Expert mode to inspect the raw result.",
  };
}

// ─────────────────────────── Readiness board aggregation ───────────────────────────
// One panel that surfaces every cheap precondition at once, each with the field/
// action that fixes it. Pure: the component supplies the probed signals.

export interface ReadinessSignals {
  downloadRootOk: boolean;
  allowlistConfigured: boolean;
  secretsUnlocked: boolean | null; // null = not needed (no cred wanted)
  aiReachable: boolean | null; // null = AI not required for this flow
}

export interface ReadinessRow {
  ok: boolean;
  label: string;
  fix: string;
}

export function readinessBoard(s: ReadinessSignals): ReadinessRow[] {
  const rows: ReadinessRow[] = [
    {
      ok: s.downloadRootOk,
      label: "Download folder under an allowed root",
      fix: "Fix the folder at Setup or add its root to the allowlist.",
    },
    {
      ok: s.allowlistConfigured,
      label: "Path allowlist is configured",
      fix: "An empty allowlist is permissive — seed it in Settings → Global.",
    },
  ];
  if (s.secretsUnlocked !== null) {
    rows.push({
      ok: s.secretsUnlocked,
      label: "Secrets backend unlocked (a credential is wanted)",
      fix: "Unlock the encrypted backend in Settings → Secrets.",
    });
  }
  if (s.aiReachable !== null) {
    rows.push({
      ok: s.aiReachable,
      label: "AI provider reachable",
      fix: "Optional — Review still works manually; AI suggest is disabled if unreachable.",
    });
  }
  return rows;
}

export function readinessAllGreen(s: ReadinessSignals): boolean {
  return readinessBoard(s).every((r) => r.ok);
}

// ─────────────────────────── The 3 thin client calls ───────────────────────────

export interface ValidateRootResp {
  ok: boolean;
  error?: string;
}

/** R1 — read-only download-root check. Empty path resolves ok (means default). */
export async function validateDownloadDir(path: string): Promise<ValidateRootResp> {
  const q = encodeURIComponent(path);
  return apiGet<ValidateRootResp>(`/api/captures/validate_download_dir?path=${q}`);
}

export interface PromoteCheckResp {
  ok: boolean;
  error?: string;
  gate_errors?: string[];
  /** 2c-guard: non-blocking soft warnings (e.g. live trigger matches 0). */
  gate_warnings?: string[];
  lint_warnings?: unknown[];
}

/** R2 — read-only promote preflight (BAD_TERMS / lint / readiness), no write.
 *  2c-guard: an optional live trigger match count (from /api/template/sandbox)
 *  surfaces a soft "stale trigger" warning without blocking when it is 0. */
export async function promoteCheck(
  file: string,
  triggerMatchCount?: number,
): Promise<PromoteCheckResp> {
  const body: { file: string; trigger_match_count?: number } = { file };
  if (typeof triggerMatchCount === "number") {
    body.trigger_match_count = triggerMatchCount;
  }
  return apiPost<PromoteCheckResp>("/api/template_manager/promote_check", body);
}

/** 2c-guard: pull the soft (non-blocking) warnings out of a promote_check
 *  response. Pure + defensive so the UI can render them without a guard. */
export function promoteGateWarnings(resp: PromoteCheckResp): string[] {
  const w = resp && (resp as PromoteCheckResp).gate_warnings;
  return Array.isArray(w) ? w.filter((s) => typeof s === "string") : [];
}

/** /api/template/sandbox response — per-field live selector match counts. */
export interface TemplateSandboxResp {
  ok: boolean;
  error?: string;
  mode?: string;
  url?: string;
  matches?: Record<
    string,
    { selector?: string; match_count?: number; samples?: string[]; error?: string }
  >;
}

/** 2c-guard: read the trigger selector's LIVE match count out of a
 *  /api/template/sandbox response. Returns the count (>=0) when present + finite,
 *  else null (unknown — fetch failed, no trigger field, malformed). Pure +
 *  defensive: never throws, so a bad response just yields an unknown count and
 *  the promote interlock stays silent rather than warning spuriously. */
export function triggerMatchCountFromSandbox(
  resp: TemplateSandboxResp,
): number | null {
  const m = resp && resp.matches && resp.matches.trigger_selector;
  const n = m && (m as { match_count?: unknown }).match_count;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

/** 2c-guard: fetch the live selector match counts for a draft against a URL.
 *  Thin client over /api/template/sandbox; the FE reads the trigger count from
 *  the result (see triggerMatchCountFromSandbox) to feed the promote interlock.
 *  The real fetch is stash-only (the sandbox has no network) — it fails open to
 *  an error response there, which yields a null count (unknown), not a warn. */
export async function templateSandbox(
  url: string,
  template: Record<string, unknown>,
  mode: "http" | "browser" = "http",
): Promise<TemplateSandboxResp> {
  return apiPost<TemplateSandboxResp>("/api/template/sandbox", {
    url,
    template,
    mode,
  });
}

export interface AllowlistAddResp {
  ok: boolean;
  error?: string;
  allowlist?: string[];
}

/** R3 — the confirm-gated, audited allowlist widening. Always sends confirm:true
 *  explicitly; the caller must have shown a two-step operator confirm first. */
export async function allowlistAdd(path: string): Promise<AllowlistAddResp> {
  return apiPost<AllowlistAddResp>("/api/captures/allowlist_add", {
    path,
    confirm: true,
  });
}
