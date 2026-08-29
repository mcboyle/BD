// CaptureWorkflow — the F2.7 LIVE half of the capture -> approve loop, as one
// console. (DomAnalyzer is the F2.6 REPLAY half.) The captured authenticated
// session is HELD OPEN the whole time, so at any step you can flip the canvas to
// Pick and grab a selector off the still-logged-in page, then test-extract that
// draft against a real URL — no cold re-capture. Every gate (first-enable,
// promote) stays a deliberate operator action; this removes the surface-hopping
// (noVNC + SSH sentinel + cockpit + workbench), not the gates.
//
// All endpoints are FULL /api/ literals so the GUI-parity scanner credits them
// spa_wired (never a concatenated base var):
//   GET  /cockpit/api/novnc                — the noVNC iframe URL (config-only)
//   POST /cockpit/api/run-capture          — start the held-open capture session
//   POST /cockpit/api/captures/pick        — arm | poll | clear the ACTIVE one-shot pick
//   POST /cockpit/api/captures/finish      — finish (save WACZ) / discard the session
//   POST /api/template/test_extract        — live test download (persist OFF by default)
//   POST /api/ai/suggest_selectors         — AI selector suggestion (review-gated)
//   POST /api/template_manager/promote     — the manual ENABLE gate
//
// The element-pick itself happens when the operator clicks inside the live
// noVNC canvas; the capture process resolves the click to a finished selector
// (bulk_downloader.element_pick) and this console polls for it. The canvas is a
// cross-origin iframe, so the SPA never reads the live DOM directly — it only
// arms the pick and reads the resolved selector back.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import {
  AffordanceLearningPanel,
  selectOptionForPolicy,
  type AffordanceResult,
  type CrawlState,
  type LearningState,
  type NetworkState,
} from "@/components/AffordanceLearningPanel";
import { SecretField } from "@/components/SecretField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { Input } from "@/components/ui/input";
import { apiGet, apiPost, apiPut } from "@/lib/api-client";
import { useGuidedDraft, useGuidedMode } from "@/hooks/useGuidedMode";
import {
  allowlistAdd,
  failureHint,
  firstBlocker,
  type GuidedCtx,
  mapVerdict,
  promoteCheck,
  STEP_COPY,
  stepStatus,
  templateSandbox,
  triggerMatchCountFromSandbox,
  validateDownloadDir,
  type VerdictState,
} from "@/lib/guidedCapture";
import {
  driftCompareView,
  type DriftSlotKey,
  type DriftStatus,
  fetchDriftStatus,
  mapVerifyRow,
  repairSummary,
  type SlotResolution,
  type VerifyState,
  verifyView,
} from "@/lib/guidedDrift";
import { cn } from "@/lib/utils";
import {
  useCaptureRailWidth,
  CAPTURE_RAIL_DEFAULT,
} from "@/hooks/useUiLayout";
import {
  DEFAULT_SITE_MIN_RESOLUTION,
  DEFAULT_SITE_QUALITY_PREFERENCE,
} from "@/lib/api-types";

// ── API response shapes ──────────────────────────────────────────────────────
interface NovncResult {
  ok: boolean;
  url?: string;
  note?: string;
}
// GCW-3: POST /api/captures/setup_site — create a site + (optionally) store its
// login password as a @cred: ref, returning the 8-hex id to auto-fill downstream.
interface SetupSiteResponse {
  ok: boolean;
  id?: string;
  login_url?: string;
  cred_stored?: boolean;
  cred_error?: string;
  auto_pick?: unknown;
  error?: string;
}
// GCW-4: a history row, polled to learn the real Test verdict. A real file with
// non-zero bytes = pass; a `done` with 0 bytes (e.g. the runner's "no dl dir"
// click-but-save-nothing branch) is NOT a pass.
interface HistoryRow {
  url?: string;
  status?: string;
  filename?: string;
  file_size?: number;
  message?: string;
  ts?: string;
}
interface PickResult {
  selector: string;
  unique: boolean;
  count: number;
  visible: boolean;
  tag?: string;
  text?: string;
  // Item C: the repeating (row) selector + its total/visible match counts.
  // For a grid tile this generalizes across rows where `selector` pins to one.
  group_selector?: string | null;
  group_count?: number;
  group_visible?: number;
}
interface PickPollResponse {
  task_id: string;
  action: string;
  armed: boolean;
  result?: PickResult | null;
}
// C3 (v3.66.290): live mirror of the HUD action timeline + verify readout.
// Structure-only — selectors / roles / request kinds+counts; no values cross.
interface InspectActionEffect {
  req_count?: number;
  manifest?: number;
  segments?: number;
  direct_media?: number;
  signed?: boolean;
  nav?: boolean;
}
interface InspectAction {
  selector?: string;
  role?: string;
  confidence?: number;
  tag?: string;
  effect?: InspectActionEffect;
}
interface InspectVerify {
  tier?: string;
  warnings?: string[];
  action_count?: number;
  trigger_selector?: string | null;
}
interface InspectState {
  actions?: InspectAction[];
  verify?: InspectVerify | null;
  rec?: boolean;
}
interface InspectPollResponse {
  task_id: string;
  action: string;
  state?: InspectState | null;
}
// v3.66.276: auto-detect row-group candidate + the suggest_rows poll response.
interface AutoRowGroup {
  selector: string;
  count: number;
  visible: number;
  has_dl_shape: boolean;
  score: number;
  sample_text?: string;
}
interface SuggestRowsResponse {
  task_id: string;
  action: string;
  groups?: AutoRowGroup[] | null;
}
interface TestExtractResponse {
  ok: boolean;
  error?: string;
  matched?: string;
  status?: number;
  bytes?: number;
  filename?: string;
  visible?: boolean;
  persisted?: boolean;
}
interface AiSuggestion {
  selector?: string;
  role?: string;
  rationale?: string;
  confidence?: number;
}
interface AiSuggestResponse {
  ok: boolean;
  suggestions?: AiSuggestion[];
  model?: string;
  latency_ms?: number;
  error?: string;
}
// F2.7c: live-DOM excerpt pulled over the held-open session for AI assist.
interface DomPollResponse {
  task_id: string;
  action: string;
  requested: boolean;
  result?: { html: string; url?: string } | null;
}
// F2.7b: the real builder surface (POST /api/captures/normalize).
interface NormalizeResponse {
  host?: string;
  status?: string;
  warnings?: string[];
  resolutions?: string[];
  has_download_trigger?: boolean;
  network_patterns?: number;
  candidate_path?: string;
  wacz?: string;
}
// F2.7b: the real review surface (GET /api/review-candidates).
interface ReviewCandidate {
  file: string;
  host?: string;
  status?: string;
  warnings?: string[];
  resolutions?: string[];
  has_download_trigger?: boolean;
  has_row_selectors?: boolean;
  network_patterns?: number;
  promote_cmd?: string;
}
interface ReviewCandidatesResponse {
  candidates: ReviewCandidate[];
  dir?: string;
}
interface LiveLearningArmResponse {
  ok: boolean;
  request_id: string;
  state: string;
  error?: string;
}
interface LiveLearningPollResponse {
  ok: boolean;
  state: string;
  response?: {
    state: string;
    result?: Record<string, unknown>;
    error?: string;
  } | null;
  error?: string;
}
interface StageLearningResponse {
  ok: boolean;
  file?: string;
  status?: string;
  error?: string;
  template?: {
    patterns?: string[];
    learned?: {
      download?: {
        trigger_selectors?: string[];
        row_selectors?: string[];
        url_attribute?: string;
      };
    };
    config_defaults?: {
      quality_preference?: string;
      min_resolution?: number;
    };
    resolutions?: number[];
    network_patterns?: string[];
    learning_evidence?: {
      shape?: string;
      option_count?: number;
      corroboration?: string;
      dom_options_proven?: boolean;
    };
  };
}
interface RunnerNetworkEventsResponse {
  ok: boolean;
  events?: Array<{ message?: string; kind?: string; seq?: number }>;
}

// A draft field the operator can pick / edit / remove. `key` is the draft slot.
// Single-pick fields (login_*) carry a single `value`. A `multi` field
// (row_selectors) carries an `entries` list — picks ACCUMULATE and each can be
// deleted individually (GCW-2 per-entry delete), matching the runner's list
// semantics for row_selectors ((row_selectors||[]).index(matched_selector)).
interface DraftField {
  key: string;
  label: string;
  value: string;
  multi?: boolean;
  entries?: string[];
}

const STEPS = [
  { key: "setup", label: "Setup" },
  { key: "capture", label: "Capture" },
  { key: "build", label: "Build" },
  { key: "inspect", label: "Inspect" },
  { key: "test", label: "Test" },
  { key: "review", label: "Review" },
  { key: "promote", label: "Promote" },
] as const;
type StepKey = (typeof STEPS)[number]["key"];

const INITIAL_FIELDS: DraftField[] = [
  { key: "row_selectors", label: "row_selectors", value: "", multi: true, entries: [] },
  { key: "login_email", label: "login.username / email", value: "" },
  { key: "login_password", label: "login.password", value: "" },
  { key: "login_submit", label: "login.submit", value: "" },
];

function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function CaptureWorkflow() {
  // Session (the held-open capture). taskId threads through pick + finish.
  const [taskId, setTaskId] = useState<string | null>(null);
  const [host, setHost] = useState("");
  const [startUrl, setStartUrl] = useState("");
  const [siteId, setSiteId] = useState("");
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  const [step, setStep] = useState<StepKey>("setup");
  const [fields, setFields] = useState<DraftField[]>(INITIAL_FIELDS);

  // GCW-3 "Setup site" step: create the site + store creds before teaching.
  const [setupName, setSetupName] = useState("");
  const [setupLoginUrl, setSetupLoginUrl] = useState("");
  const [setupUsername, setSetupUsername] = useState("");
  const [setupPassword, setSetupPassword] = useState("");
  const [setupDownloadDir, setSetupDownloadDir] = useState("");
  const [qualityPreference, setQualityPreference] = useState(DEFAULT_SITE_QUALITY_PREFERENCE);
  const [minResolution, setMinResolution] = useState(DEFAULT_SITE_MIN_RESOLUTION);
  const [logNetwork, setLogNetwork] = useState(false);
  const [setupBusy, setSetupBusy] = useState(false);

  // Pick mode: which field is awaiting a live click (null = Interact mode).
  const [armedField, setArmedField] = useState<string | null>(null);
  // GCW-2: holds the last per-entry delete so the operator can undo a mis-delete.
  const [undoBuf, setUndoBuf] = useState<{
    key: string;
    index: number;
    selector: string;
  } | null>(null);
  const pollRef = useRef<number | null>(null);
  // C3: live HUD analysis mirror (polled while in the Inspect step).
  const [inspectState, setInspectState] = useState<InspectState | null>(null);
  const inspectPollRef = useRef<number | null>(null);
  // v3.66.276: live auto-detect-rows. `suggesting` drives the button spinner;
  // `autoSuggestedRef` ensures the Test-step auto-prefill fires at most once.
  const [suggesting, setSuggesting] = useState(false);
  const autoSuggestedRef = useRef(false);

  // Test + AI + promote.
  const [testUrl, setTestUrl] = useState("");
  const [persist, setPersist] = useState(false);
  // GCW probe mode (v3.66.274): quick verify — trigger the download, sample
  // the first bytes to prove the media path, then abort (no full download, no
  // download_dir needed). The verdict watch + gate are identical to a full Test.
  const [probe, setProbe] = useState(false);
  // BP-VH3: force re-download — re-test a URL even if a prior `done` row exists.
  const [force, setForce] = useState(false);
  const [testing, setTesting] = useState(false);
  // GCW-4: e2e verdict + promote gate. e2ePass null=not run yet, true/false=verdict.
  const [e2ePass, setE2ePass] = useState<boolean | null>(null);
  const [verdict, setVerdict] = useState<HistoryRow | null>(null);
  const [watching, setWatching] = useState(false);

  // ── Guided mode (GCW Cut 1) ────────────────────────────────────────────
  // Opt-in overlay on the SAME held-open session + state. Default guided-ON for
  // a brand-new host (no reviewed template yet); remembers the operator's last
  // explicit choice thereafter.
  const { guided, toggle: toggleGuided } = useGuidedMode(true);
  // R1 inline download-root verdict (null = not yet checked / field empty-ok).
  const [rootCheck, setRootCheck] = useState<{ ok: boolean; error?: string } | null>(
    null,
  );
  const [allowlistBusy, setAllowlistBusy] = useState(false);
  const [allowlistConfirm, setAllowlistConfirm] = useState(false); // 2-step gate
  // R2 promote preflight result (null = not yet run).
  const [promotePreflight, setPromotePreflight] = useState<{
    ok: boolean;
    error?: string;
    gate_errors?: string[];
    gate_warnings?: string[];
    lint_warnings?: unknown[];
  } | null>(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  // Resume substrate: persist the recoverable draft state keyed by host.
  const draftStore = useGuidedDraft(host);

  // ── Guided mode Cut 2 (P3): live drift + repair + post-promote verify ───
  // Persisted drift monitor for the current site (repair-entry signal). The
  // LIVE re-pick and the real runner download are operator-verified.
  const [driftStatus, setDriftStatus] = useState<DriftStatus | null>(null);
  const [driftSlots, setDriftSlots] = useState<
    Partial<Record<DriftSlotKey, SlotResolution>>
  >({});
  const [driftCompareOpen, setDriftCompareOpen] = useState(false);
  const [verifyState, setVerifyState] = useState<VerifyState>("idle");
  // Item 1: live progress text shown while the verdict watch waits out a large
  // download (so a multi-GB Test doesn't look hung).
  const [watchProgress, setWatchProgress] = useState("");
  const [overrideE2e, setOverrideE2e] = useState(false);
  const [ai, setAi] = useState<AiSuggestResponse | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [promoteConfirm, setPromoteConfirm] = useState(false);

  // F2.7b: Build (normalize) + Review (candidates) state.
  const [building, setBuilding] = useState(false);
  const [buildResult, setBuildResult] = useState<NormalizeResponse | null>(null);
  // 2c-guard live-count: the trigger selector's live match count from the Test
  // step's /api/template/sandbox probe. null = unknown (not checked / fetch
  // failed). Threaded into the promote preflight so a 0-match trigger surfaces a
  // soft "stale trigger" warning. The probe is stash-only (sandbox has no net).
  const [triggerLiveCount, setTriggerLiveCount] = useState<number | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewCands, setReviewCands] = useState<ReviewCandidate[] | null>(null);
  const [learningState, setLearningState] = useState<LearningState>({ state: "idle" });
  const [networkState, setNetworkState] = useState<NetworkState>({ state: "idle" });
  const [crawlState, setCrawlState] = useState<CrawlState>({ state: "idle" });
  const [stagedDraftFile, setStagedDraftFile] = useState<string | null>(null);
  const [learningRequestId, setLearningRequestId] = useState<string | null>(null);
  const [stagedTemplate, setStagedTemplate] = useState<StageLearningResponse["template"] | null>(null);
  const sessionEpochRef = useRef(0);
  const candidateEpochRef = useRef(0);

  const currentLearningSelection = learningState.state === "found"
    ? selectOptionForPolicy(
        learningState.result.options,
        qualityPreference,
        minResolution,
      )
    : null;
  const resolutionPolicySatisfied = learningState.state !== "found"
    || currentLearningSelection?.status === "SELECTED";
  const draftFileForGate = stagedDraftFile
    || `${host.trim().toLowerCase()}.template-draft.json`;

  function clearCandidateState() {
    candidateEpochRef.current += 1;
    setStagedDraftFile(null);
    setStagedTemplate(null);
    setBuildResult(null);
    setReviewCands(null);
    setNetworkState({ state: "idle" });
    setCrawlState({ state: "idle" });
    setTriggerLiveCount(null);
    setPromotePreflight(null);
    setPreflightBusy(false);
    setPromoteConfirm(false);
    setE2ePass(null);
    setVerdict(null);
    setOverrideE2e(false);
    setVerifyState("idle");
    setTesting(false);
    setWatching(false);
    setWatchProgress("");
  }

  function dropCurrentLearnedRow() {
    if (learningState.state !== "found" || !learningState.result.row_selector) return;
    const learnedRow = learningState.result.row_selector;
    setFields((previous) => previous.map((field) =>
      field.key === "row_selectors"
        ? {
            ...field,
            entries: (field.entries || []).filter((entry) => entry !== learnedRow),
          }
        : field));
  }

  function changeQualityPreference(value: string) {
    if (learningState.state === "running") {
      setLearningState({ state: "idle" });
      setLearningRequestId(null);
    }
    if (stagedTemplate) {
      dropCurrentLearnedRow();
      setLearningState({ state: "idle" });
      setLearningRequestId(null);
    }
    clearCandidateState();
    setQualityPreference(value);
  }

  function changeMinResolution(value: number) {
    if (learningState.state === "running") {
      setLearningState({ state: "idle" });
      setLearningRequestId(null);
    }
    if (stagedTemplate) {
      dropCurrentLearnedRow();
      setLearningState({ state: "idle" });
      setLearningRequestId(null);
    }
    clearCandidateState();
    setMinResolution(Math.max(0, value || 0));
  }

  useEffect(() => {
    setPromotePreflight(null);
    setPromoteConfirm(false);
  }, [stagedDraftFile, learningRequestId, qualityPreference, minResolution]);

  const novnc = useQuery<NovncResult>({
    queryKey: ["novnc-url"],
    queryFn: () => apiGet<NovncResult>("/cockpit/api/novnc"),
    staleTime: 60_000,
  });

  // Elapsed clock while a session is open.
  useEffect(() => {
    if (!startedAt) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);

  useEffect(() => () => {
    sessionEpochRef.current += 1;
  }, []);

  // v3.66.276 (a): when the live session is up on the Capture step and the
  // operator hasn't picked any rows yet, run auto-detect ONCE to pre-fill the
  // row_selectors field. Quiet on a miss; the explicit "Suggest rows" button
  // (b) covers re-runs and surfaces misses. Fires at most once per session.
  useEffect(() => {
    if (!taskId || autoSuggestedRef.current) return;
    const rowField = fields.find((f) => f.key === "row_selectors");
    if (rowField && (rowField.entries?.length ?? 0) > 0) return;
    autoSuggestedRef.current = true;
    void suggestRows(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // Pick poll loop: while a field is armed, poll the capture for the resolved
  // selector and fill the field when it lands.
  useEffect(() => {
    if (!armedField || !taskId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await apiPost<PickPollResponse>(
          "/cockpit/api/captures/pick",
          { task_id: taskId, action: "poll" },
        );
        if (cancelled) return;
        if (r.result && r.result.selector) {
          const picked = r.result;
          // Item C: a row (multi) field wants the REPEATING selector that
          // matches every tile, not the unique-to-one selector (which pins to
          // a single data-id). Prefer group_selector for multi fields.
          const field = INITIAL_FIELDS.find((f) => f.key === armedField);
          const useGroup = !!(field?.multi && picked.group_selector);
          const chosen = useGroup
            ? (picked.group_selector as string)
            : picked.selector;
          setFields((prev) =>
            prev.map((f) => {
              if (f.key !== armedField) return f;
              if (f.multi) {
                // GCW-2: accumulate picks into the entries list (dedupe), so a
                // page with several row/resolution selectors keeps them all.
                const cur = f.entries ?? [];
                if (cur.includes(chosen)) return f;
                return { ...f, entries: [...cur, chosen] };
              }
              return { ...f, value: chosen };
            }),
          );
          setArmedField(null);
          if (useGroup) {
            const total = picked.group_count ?? 0;
            const vis = picked.group_visible ?? 0;
            const dup =
              total > vis && vis > 0 ? ` (${total} matched, ${vis} visible — page renders duplicates)` : "";
            toast.success(`Grabbed ${chosen} — matches ${vis || total} rows${dup}`);
          } else {
            toast.success(
              picked.visible
                ? `Grabbed ${chosen}`
                : `Grabbed ${chosen} — heads up: this element is hidden`,
            );
          }
        }
      } catch {
        /* keep polling; a transient miss is fine */
      }
    };
    pollRef.current = window.setInterval(tick, 800);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [armedField, taskId]);

  // C3: while in the Inspect step on a live session, poll the live HUD analysis
  // mirror (action timeline + verify) so the SPA reflects what the noVNC HUD is
  // showing. Read-only mirror — no arming; relaxed cadence; cleared when the
  // step/session ends.
  useEffect(() => {
    if (step !== "inspect" || !taskId) {
      setInspectState(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await apiPost<InspectPollResponse>(
          "/cockpit/api/captures/pick",
          { task_id: taskId, action: "inspect_poll" },
        );
        if (!cancelled && r.state) setInspectState(r.state);
      } catch {
        /* transient miss — keep polling */
      }
    };
    void tick();
    inspectPollRef.current = window.setInterval(tick, 1500);
    return () => {
      cancelled = true;
      if (inspectPollRef.current) window.clearInterval(inspectPollRef.current);
      inspectPollRef.current = null;
    };
  }, [step, taskId]);

  // GCW-3: create the site (+ optional @cred password) and thread the returned
  // 8-hex id into siteId so Test/Promote auto-fill it. The password is write-only
  // — cleared after submit; the server stores it as a @cred ref, never plaintext.
  async function setupSite() {
    const nm = setupName.trim();
    const lu = setupLoginUrl.trim();
    if (!nm || !lu) {
      toast.error("Enter a site name and a login URL");
      return;
    }
    setSetupBusy(true);
    try {
      const created = await apiPost<SetupSiteResponse>(
        "/api/captures/setup_site",
        {
          name: nm,
          login_url: lu,
          username: setupUsername.trim(),
          password: setupPassword,
          download_dir: setupDownloadDir.trim(),
          quality_preference: qualityPreference,
          min_resolution: minResolution,
          log_network: logNetwork,
        },
      );
      if (!created.ok || !created.id) {
        toast.error(created.error || "Setup failed");
        return;
      }
      setSiteId(created.id); // auto-fill the id downstream (kills the friction)
      setStartUrl(lu); // seed the Capture step's start URL with the login URL
      setSetupPassword(""); // write-only secret: clear it after submit
      // Reset the e2e gate — a freshly set-up site has not proven a download yet.
      setE2ePass(null);
      setVerdict(null);
      setOverrideE2e(false);
      if (created.cred_stored) {
        toast.success(`Site created (${created.id}) — credential stored`);
      } else if (created.cred_error) {
        toast.error(
          `Site created (${created.id}) but credential not stored: ${created.cred_error}`,
        );
      } else {
        toast.success(
          `Site created (${created.id}) — log in by hand during capture`,
        );
      }
      setStep("capture");
    } catch (e) {
      toast.error(`Could not set up site: ${String(e)}`);
    } finally {
      setSetupBusy(false);
    }
  }

  async function startCapture() {
    if (!startUrl.trim()) {
      toast.error("Enter a start URL for the capture");
      return;
    }
    try {
      const r = await apiPost<{ task?: { task_id?: string } }>(
        "/cockpit/api/run-capture",
        { name: "capture_session", params: { url: startUrl.trim() } },
      );
      const tid = r.task?.task_id;
      if (!tid) {
        toast.error("Capture did not start (no task id returned)");
        return;
      }
      setTaskId(tid);
      sessionEpochRef.current += 1;
      setStartedAt(Date.now());
      // BUG-5: a new capture must start from a clean slate. Suggestion state
      // (auto-detected row_selectors entries + the AI suggestions panel) is
      // seeded per session and otherwise ACCUMULATES across sessions, so a
      // later capture on a different host showed the previous host's classes
      // (e.g. videojs.org marketing selectors bleeding onto an archive.org
      // capture). Reset fields to INITIAL (fresh row_selectors.entries: []),
      // clear armed pick + AI panel, so nothing bleeds across host changes.
      setFields(INITIAL_FIELDS.map((f) => ({ ...f, entries: f.entries ? [] : f.entries })));
      setAi(null);
      setArmedField(null);
      setLearningState({ state: "idle" });
      setNetworkState({ state: "idle" });
      setCrawlState({ state: "idle" });
      setLearningRequestId(null);
      clearCandidateState();
      try {
        setHost(new URL(startUrl.trim()).host);
      } catch {
        setHost(startUrl.trim());
      }
      setStep("build");
      toast.success("Session held open — navigate to a scene, then learn it in Build");
    } catch (e) {
      toast.error(`Could not start capture: ${String(e)}`);
    }
  }

  async function armPick(fieldKey: string) {
    if (!taskId) {
      toast.error("Start a capture first");
      return;
    }
    try {
      await apiPost("/cockpit/api/captures/pick", {
        task_id: taskId,
        action: "arm",
      });
      setArmedField(fieldKey);
      toast.message("Pick armed — click the element in the live session");
    } catch (e) {
      toast.error(`Could not arm pick: ${String(e)}`);
    }
  }

  // v3.66.276: live auto-detect row groups. Arms the AUTO_ROW_REQUEST bridge,
  // polls AUTO_ROW_RESULT, and pre-fills the row_selectors field (dedup) with
  // the ranked, scoped candidates. RECOMMENDATION only — the operator confirms
  // or refines via Pick element; nothing is promoted. `auto` (Test-step
  // pre-fill) stays quiet unless it finds something; the explicit button
  // surfaces misses too.
  async function suggestRows(auto = false) {
    if (!taskId) {
      if (!auto) toast.error("Start a capture first");
      return;
    }
    setSuggesting(true);
    try {
      await apiPost("/api/captures/suggest_rows", {
        task_id: taskId,
        action: "arm",
      });
      let groups: AutoRowGroup[] | null = null;
      for (let i = 0; i < 12 && !groups; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const r = await apiPost<SuggestRowsResponse>(
          "/api/captures/suggest_rows",
          { task_id: taskId, action: "poll" },
        );
        if (r.groups && r.groups.length) groups = r.groups;
      }
      if (!groups || !groups.length) {
        if (!auto) toast.message("No repeating row group detected on this page");
        return;
      }
      const sels = groups.map((g) => g.selector);
      setFields((prev) =>
        prev.map((f) => {
          if (f.key !== "row_selectors") return f;
          const cur = f.entries ?? [];
          const merged = [...cur];
          for (const s of sels) if (!merged.includes(s)) merged.push(s);
          return { ...f, entries: merged };
        }),
      );
      const top = groups[0];
      const dup =
        top.count > top.visible && top.visible > 0
          ? ` (${top.count} matched, ${top.visible} visible — auto-scoped)`
          : "";
      toast.success(
        `Suggested ${sels.length} row selector${sels.length === 1 ? "" : "s"} — top: ${top.selector}${dup}`,
      );
    } catch (e) {
      if (!auto) toast.error(`Could not suggest rows: ${String(e)}`);
    } finally {
      setSuggesting(false);
    }
  }

  async function cancelPick() {
    if (taskId) {
      try {
        await apiPost("/cockpit/api/captures/pick", {
          task_id: taskId,
          action: "clear",
        });
      } catch {
        /* best-effort */
      }
    }
    setArmedField(null);
  }

  function removeField(fieldKey: string) {
    setFields((prev) =>
      prev.map((f) => (f.key === fieldKey ? { ...f, value: "" } : f)),
    );
    toast.message("Removed from draft (the live page is untouched)");
  }

  // GCW-2 per-entry delete: remove ONE entry from a multi-pick list by index,
  // keeping the rest — never blanks the whole slot. Stashes it for undo. Draft-
  // only: no backend call, the live page is untouched.
  function removePickEntry(fieldKey: string, index: number) {
    setFields((prev) =>
      prev.map((f) => {
        if (f.key !== fieldKey || !f.multi) return f;
        const cur = f.entries ?? [];
        if (index < 0 || index >= cur.length) return f;
        setUndoBuf({ key: fieldKey, index, selector: cur[index] });
        return { ...f, entries: cur.filter((_, i) => i !== index) };
      }),
    );
    toast.message("Removed one selector from the draft (the live page is untouched)");
  }

  // GCW-2: undo the last per-entry delete, restoring it at its original index.
  function undoRemovePickEntry() {
    if (!undoBuf) return;
    const { key, index, selector } = undoBuf;
    setFields((prev) =>
      prev.map((f) => {
        if (f.key !== key || !f.multi) return f;
        const cur = f.entries ?? [];
        const next = [...cur];
        next.splice(Math.min(index, next.length), 0, selector);
        return { ...f, entries: next };
      }),
    );
    setUndoBuf(null);
  }

  // GCW-2: add a typed selector to a multi-pick list (dedupe). Lets the operator
  // hand-enter a selector without the live pick.
  function addPickEntry(fieldKey: string, selector: string) {
    const sel = selector.trim();
    if (!sel) return;
    setFields((prev) =>
      prev.map((f) => {
        if (f.key !== fieldKey || !f.multi) return f;
        const cur = f.entries ?? [];
        if (cur.includes(sel)) return f;
        return { ...f, entries: [...cur, sel] };
      }),
    );
  }

  function setFieldValue(fieldKey: string, value: string) {
    setFields((prev) =>
      prev.map((f) => (f.key === fieldKey ? { ...f, value } : f)),
    );
  }

  function assembleDraft(): Record<string, unknown> {
    const get = (k: string) => fields.find((f) => f.key === k)?.value.trim() || "";
    const draft: Record<string, unknown> = {};
    // row_selectors is a multi-pick LIST (matches the canonical schema +
    // runner list semantics); send the accumulated entries.
    const rowField = fields.find((f) => f.key === "row_selectors");
    const rows = (rowField?.entries ?? []).map((s) => s.trim()).filter(Boolean);
    if (rows.length) draft.row_selectors = rows;
    const login: Record<string, string> = {};
    if (get("login_email")) login.email = get("login_email");
    if (get("login_password")) login.password = get("login_password");
    if (get("login_submit")) login.submit = get("login_submit");
    if (Object.keys(login).length) draft.login = login;
    if (learningState.state === "found") {
      const learned = learningState.result;
      draft.learned = {
        download: {
          trigger_selectors: learned.trigger_selector
            ? [learned.trigger_selector]
            : [],
          row_selectors: learned.row_selector ? [learned.row_selector] : [],
          url_attribute: learned.url_attribute || "href",
        },
      };
    }
    return draft;
  }

  async function runTest() {
    if (learningState.state === "found" && currentLearningSelection?.status !== "SELECTED") {
      toast.error(currentLearningSelection?.reason || "Learned options violate the configured quality policy");
      return;
    }
    if (!siteId.trim()) {
      toast.error("Enter the target site id");
      return;
    }
    if (!testUrl.trim()) {
      toast.error("Enter a URL to test against");
      return;
    }
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    setTesting(true);
    setVerdict(null);
    setE2ePass(null);
    const since = Date.now();
    try {
      const r = await apiPost<TestExtractResponse>("/api/template/test_extract", {
        site_id: siteId.trim(),
        template: assembleDraft(),
        url: testUrl.trim(),
        persist,
        probe,
        force_download: force,
      });
      if (!isCurrentCandidate()) return;
      if (!r.ok) {
        toast.error(r.error || "Test extract failed");
        return;
      }
      // 2c-guard live-count: best-effort probe of the trigger selector's LIVE
      // match count against the same URL, fed into the promote interlock. Uses
      // the build-derived trigger selector; fire-and-forget so it never delays
      // the watch. Stash-only (sandbox network off -> count stays null/unknown).
      const _trigSel = inspectState?.verify?.trigger_selector;
      if (_trigSel && testUrl.trim()) {
        void templateSandbox(testUrl.trim(), { trigger_selector: _trigSel }, "http")
          .then((resp) => {
            if (isCurrentCandidate()) {
              setTriggerLiveCount(triggerMatchCountFromSandbox(resp));
            }
          })
          .catch(() => {
            if (isCurrentCandidate()) setTriggerLiveCount(null);
          });
      }
      // WATCH the run for the real verdict: a /api/history row for this URL with
      // a real file. A `done` with 0 bytes (runner's "no dl dir" click-but-save-
      // nothing) is NOT a pass — that was the ultrafilms false-positive.
      toast.message("Test started — watching for a real download…");
      setWatchProgress("");
      setWatching(true);
      const row = await watchForVerdict(
        siteId.trim(),
        testUrl.trim(),
        since,
        isCurrentCandidate,
      );
      if (!isCurrentCandidate()) return;
      if (!row) {
        setE2ePass(false);
        toast.error(
          "No verdict yet — the run is still going or didn't finish in time. Check History; if it lands as 'done' there, the draft is fine and you can re-test or override.",
        );
        return;
      }
      setVerdict(row);
      const passed = row.status === "done" && (row.file_size ?? 0) > 0;
      setE2ePass(passed);
      if (passed) {
        if (probe) {
          toast.success(
            `Probe OK — media reachable · sampled ${fmtBytes(row.file_size ?? 0)} (no full download)`,
          );
        } else {
          toast.success(`Downloaded ${row.filename || "file"} · ${fmtBytes(row.file_size ?? 0)}`);
        }
      } else if ((row.file_size ?? 0) === 0) {
        toast.error("Clicked but saved 0 bytes — set a Download folder in Setup, then re-test");
      } else {
        toast.error(row.message || `Test did not download (status: ${row.status || "?"})`);
      }
    } catch (e) {
      if (isCurrentCandidate()) {
        setE2ePass(false);
        toast.error(`Test failed: ${String(e)}`);
      }
    } finally {
      if (isCurrentCandidate()) {
        setTesting(false);
        setWatching(false);
      }
    }
  }

  // Item 1 (large-file verdict): a download can take far longer than a couple
  // minutes — a 3.8 GB file completed but the old fixed ~2.5 min deadline
  // expired first, so the gate saw no verdict. /api/history only gets a row at
  // the TERMINAL event (db_log), so during the download there is nothing to
  // poll. We now ALSO read the live in-flight job from /api/status
  // (runner.get_status -> jobs[url] carries status + file_size): keep waiting
  // while bytes are flowing, surface progress, and only give up on a real stall
  // (no byte progress for STALL_MS while running), the absolute cap, or a run
  // that never started (no live job and no terminal row within GRACE_MS).
  async function watchForVerdict(
    sid: string,
    url: string,
    since: number,
    isCurrent: () => boolean = () => true,
  ): Promise<HistoryRow | null> {
    const MAX_WALL = 45 * 60_000; // absolute cap (large files can be slow)
    const STALL_MS = 8 * 60_000; // running but no byte progress this long = stalled
    const GRACE_MS = 30_000; // the run must appear (live or terminal) within this
    const t0 = Date.now();
    const deadline = t0 + MAX_WALL;
    // BP-VH2: all 5 terminal runner statuses resolve here (the 7-status map is
    // these 5 + the non-terminal running/pending handled below). Adding
    // skipped_duplicate + stopped stops them waiting out MAX_WALL silently.
    const terminal = new Set([
      "done", "failed", "needs_review", "skipped_duplicate", "stopped",
    ]);
    let everSeenLive = false;
    let lastBytes = -1;
    let lastProgressAt = t0;
    while (Date.now() < deadline) {
      if (!isCurrent()) return null;
      // 1) authoritative terminal row
      try {
        const rows = await apiGet<HistoryRow[]>(
          `/api/history?site_id=${encodeURIComponent(sid)}&limit=25`,
        );
        if (!isCurrent()) return null;
        const hit = (rows || []).find((r) => {
          if (r.url !== url || !terminal.has(r.status || "")) return false;
          const t = Date.parse((r.ts || "").replace(" ", "T") + "Z");
          return Number.isNaN(t) ? true : t >= since - 5000;
        });
        if (hit) return hit;
      } catch {
        /* transient — keep polling */
      }
      if (!isCurrent()) return null;
      // 2) live in-flight job — so we keep waiting through a long download
      let live:
        | { status?: string; file_size?: number; message?: string }
        | null = null;
      try {
        const st = await apiGet<
          Record<
            string,
            { jobs?: Record<string, { status?: string; file_size?: number; message?: string }> }
          >
        >("/api/status");
        if (!isCurrent()) return null;
        live = st?.[sid]?.jobs?.[url] ?? null;
      } catch {
        /* transient — keep polling */
      }
      if (!isCurrent()) return null;
      if (live && terminal.has(live.status || "")) {
        // BP-VH2: the in-flight job reached a terminal status (e.g.
        // skipped_duplicate / stopped) — resolve now, don't wait for the row.
        return {
          url,
          status: live.status,
          file_size: live.file_size ?? 0,
          message: live.message,
        } as HistoryRow;
      }
      if (live && (live.status === "running" || live.status === "pending")) {
        everSeenLive = true;
        const b = live.file_size ?? 0;
        if (b > lastBytes) {
          lastBytes = b;
          lastProgressAt = Date.now();
        }
        setWatchProgress(
          live.message || (b > 0 ? `Downloading ${fmtBytes(b)}…` : "Starting…"),
        );
        if (Date.now() - lastProgressAt > STALL_MS) {
          // Stalled: running but no byte progress for STALL_MS.
          return {
            url,
            status: "failed",
            file_size: lastBytes > 0 ? lastBytes : 0,
            message: "Download stalled — no progress (check the site/network)",
          } as HistoryRow;
        }
      } else if (!everSeenLive && Date.now() - t0 > GRACE_MS) {
        // Never started: no live job and no terminal row within the grace.
        return null;
      }
      await new Promise((res) => setTimeout(res, 2500));
    }
    return null;
  }

  // F2.7c: pull a live-DOM excerpt over the held-open session (same cross-
  // process sentinel pattern as pick). Best-effort + bounded: request, then
  // poll a few ticks; an empty string just means the AI gets context-only
  // (the prior behaviour), never an error. Credential values are scrubbed
  // capture-side before the excerpt ever leaves the box.
  async function fetchDomExcerpt(): Promise<string> {
    if (!taskId || !sessionLive) return "";
    try {
      await apiPost("/cockpit/api/captures/pick", { task_id: taskId, action: "dom" });
    } catch {
      return "";
    }
    for (let i = 0; i < 6; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try {
        const r = await apiPost<DomPollResponse>("/cockpit/api/captures/pick", {
          task_id: taskId,
          action: "dom_poll",
        });
        if (r.result && r.result.html) return r.result.html;
      } catch {
        /* transient miss — keep polling */
      }
    }
    return "";
  }

  async function askAi() {
    setAiBusy(true);
    setAi(null);
    try {
      const draft = assembleDraft();
      const dom_excerpt = await fetchDomExcerpt();
      const r = await apiPost<AiSuggestResponse>("/api/ai/suggest_selectors", {
        dom_excerpt,
        page_url: startUrl.trim(),
        context_hint: `Current draft selectors: ${JSON.stringify(draft)}. Tighten row selectors, flag likely decoys, and fill any missing login field.`,
      });
      setAi(r);
      if (!r.ok) toast.error(r.error || "AI suggestion unavailable");
    } catch (e) {
      toast.error(`AI suggestion failed: ${String(e)}`);
    } finally {
      setAiBusy(false);
    }
  }

  function applyAiSelector(sel: string, role?: string) {
    // Map the AI's role to a draft field; default to row_selectors.
    const key =
      role === "password"
        ? "login_password"
        : role === "submit"
          ? "login_submit"
          : role === "email" || role === "username"
            ? "login_email"
            : "row_selectors";
    setFieldValue(key, sel);
    toast.success("Applied — review before promote");
  }

  async function runLiveLearningAction<T extends Record<string, unknown>>(
    mode: "learn" | "network" | "crawl",
    payload: Record<string, unknown> = {},
  ): Promise<{ result: T; requestId: string }> {
    if (!taskId) throw new Error("Start a held-open Capture session first");
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    const armed = await apiPost<LiveLearningArmResponse>(
      "/api/captures/live_learning",
      { task_id: taskId, site_id: siteId, action: "arm", mode, payload },
    );
    if (!armed.ok || !armed.request_id) {
      throw new Error(armed.error || "learning request was not armed");
    }
    const cancelOwned = async () => {
      try {
        await apiPost("/api/captures/live_learning", {
          task_id: taskId,
          action: "cancel",
          mode,
          request_id: armed.request_id,
        });
      } catch {
        // The server-side deadline remains the fallback if cancellation races
        // with Capture completion or session shutdown.
      }
    };
    if (!isCurrentCandidate()) {
      await cancelOwned();
      throw new Error("Capture candidate changed while the action was arming");
    }
    // Learning is normally one Capture tick; a per-scene listing plan can take
    // longer because it opens each scene in the same authenticated context.
    const maxPolls = mode === "crawl" ? 2700 : 360;
    for (let attempt = 0; attempt < maxPolls; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const polled = await apiPost<LiveLearningPollResponse>(
        "/api/captures/live_learning",
        {
          task_id: taskId,
          action: "poll",
          mode,
          request_id: armed.request_id,
        },
      );
      if (!isCurrentCandidate()) {
        await cancelOwned();
        throw new Error("Capture candidate changed; stale learning result ignored");
      }
      if (polled.response?.state === "failed") {
        throw new Error(polled.response.error || `${mode} failed in the Capture page`);
      }
      if (polled.response?.result) {
        return { result: polled.response.result as T, requestId: armed.request_id };
      }
    }
    await cancelOwned();
    throw new Error(`${mode} timed out while the Capture session was open`);
  }

  async function learnAffordance() {
    const ownerSessionEpoch = sessionEpochRef.current;
    dropCurrentLearnedRow();
    clearCandidateState();
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    setLearningState({ state: "running" });
    setLearningRequestId(null);
    try {
      if (siteId.trim()) await persistLearningPolicy(true);
      if (!isCurrentCandidate()) return;
      const { result, requestId } = await runLiveLearningAction<AffordanceResult & Record<string, unknown>>(
        "learn",
        { quality_preference: qualityPreference, min_resolution: minResolution },
      );
      if (!isCurrentCandidate()) return;
      if (result.status === "FOUND" && Array.isArray(result.options)) {
        setLearningState({ state: "found", result });
        setLearningRequestId(requestId);
        if (result.row_selector) {
          setFields((previous) =>
            previous.map((field) => {
              if (field.key !== "row_selectors") return field;
              const entries = field.entries || [];
              return entries.includes(result.row_selector as string)
                ? field
                : { ...field, entries: [...entries, result.row_selector as string] };
            }),
          );
        }
        toast.success(
          `Found ${result.options.length} options via ${result.shape} · ${result.row_selector}`,
        );
      } else if (result.status === "UNKNOWN" && Array.isArray(result.options)) {
        setLearningState({ state: "nothing", result });
        setLearningRequestId(requestId);
        toast.message("UNKNOWN — no download affordance found on this rendered page");
      } else {
        throw new Error("learner returned a malformed result without an options list");
      }
    } catch (error) {
      if (isCurrentCandidate()) {
        setLearningState({ state: "failed", error: String(error) });
      }
    }
  }

  async function captureNetworkEvidence() {
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    setNetworkState({ state: "running" });
    try {
      const { result } = await runLiveLearningAction<{
        status: string;
        count: number;
        network_evidence?: AffordanceResult["network_evidence"];
      } & Record<string, unknown>>("network");
      if (!isCurrentCandidate()) return;
      const evidence = [...(result.network_evidence || [])];
      const runnerEvidence: NonNullable<AffordanceResult["network_evidence"]> = [];
      // log_network controls the normal runner, whose bounded event buffer is
      // exposed by the existing per-site events endpoint. Keep that historical
      // stream visibly separate: it must never corroborate DOM from the current
      // held page or create a stale/background-tab AGREE result.
      if (siteId.trim()) {
        try {
          const runner = await apiGet<RunnerNetworkEventsResponse>(
            `/api/sites/${encodeURIComponent(siteId.trim())}/events?kind=network&limit=100`,
          );
          for (const event of runner.events || []) {
            const message = String(event.message || "").trim();
            if (message && !runnerEvidence.some((row) => row.url === message)) {
              runnerEvidence.push({ url: message, kind: "runner_network" });
            }
          }
        } catch {
          // A newly-created or idle runner can have no buffer yet. The live
          // Capture evidence remains a complete, separately labelled result.
        }
      }
      if (!isCurrentCandidate()) return;
      const totalEvidence = evidence.length + runnerEvidence.length;
      if (totalEvidence > 0) {
        setNetworkState({
          state: "found",
          count: totalEvidence,
          evidence,
          runnerEvidence,
        });
      } else {
        setNetworkState({ state: "nothing", count: 0 });
      }
    } catch (error) {
      if (isCurrentCandidate()) {
        setNetworkState({ state: "failed", error: String(error) });
      }
    }
  }

  async function crawlListing() {
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    setCrawlState({ state: "running" });
    try {
      if (siteId.trim()) await persistLearningPolicy(true);
      if (!isCurrentCandidate()) return;
      const learned = learningState.state === "found" ? learningState.result : null;
      const options = learned?.options || [];
      const { result } = await runLiveLearningAction<{
        status: string;
        scene_count: number;
        plans: Extract<CrawlState, { state: "found" }>["plans"];
        error?: string;
        reason?: string;
      } & Record<string, unknown>>("crawl", {
        options,
        row_selectors: learned?.row_selector ? [learned.row_selector] : [],
        trigger_selectors: learned?.trigger_selector ? [learned.trigger_selector] : [],
        quality_preference: qualityPreference,
        min_resolution: minResolution,
      });
      if (!isCurrentCandidate()) return;
      if (
        result.status === "EMPTY"
        && result.scene_count === 0
        && Array.isArray(result.plans)
        && result.plans.length === 0
      ) {
        setCrawlState({
          state: "nothing",
          count: 0,
          reason: result.reason || "Rendered listing explicitly declares zero scenes.",
        });
        return;
      }
      if (result.status !== "FOUND" || result.scene_count < 1) {
        setCrawlState({
          state: "failed",
          error: result.error || "Zero scenes found on the rendered listing",
        });
        return;
      }
      setCrawlState({
        state: "found",
        count: result.scene_count,
        plans: result.plans,
      });
    } catch (error) {
      if (isCurrentCandidate()) {
        setCrawlState({ state: "failed", error: String(error) });
      }
    }
  }

  async function persistLearningPolicy(silent = false) {
    if (!siteId.trim()) throw new Error("Create the site in Setup before saving its policy");
    await apiPut(`/api/sites/${encodeURIComponent(siteId.trim())}`, {
      quality_preference: qualityPreference,
      min_resolution: minResolution,
      log_network: logNetwork,
    });
    if (!silent) toast.success("Saved quality_preference, min_resolution, and log_network");
  }

  async function saveLearningPolicy() {
    if (!siteId.trim()) {
      toast.error("Create the site in Setup before saving its policy");
      return;
    }
    try {
      await persistLearningPolicy();
    } catch (error) {
      toast.error(`Could not save policy: ${String(error)}`);
    }
  }

  async function stageLearnedTemplate() {
    if (learningState.state !== "found" || !learningRequestId || !taskId) return;
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    try {
      await persistLearningPolicy(true);
      if (!isCurrentCandidate()) return;
      const staged = await apiPost<StageLearningResponse>(
        "/api/captures/stage_learning",
        {
          task_id: taskId,
          request_id: learningRequestId,
          site_id: siteId,
        },
      );
      if (!isCurrentCandidate()) return;
      if (!staged.ok || !staged.file) throw new Error(staged.error || "draft was not staged");
      setStagedDraftFile(staged.file);
      setStagedTemplate(staged.template || null);
      setLearningRequestId(null);
      setReviewCands([
        {
          file: staged.file,
          host,
          status: staged.status || "draft_review_required",
          resolutions: learningState.result.options
            .map((option) => option.height)
            .filter((height): height is number => typeof height === "number")
            .map(String),
          has_download_trigger: !!learningState.result.trigger_selector,
          has_row_selectors: !!learningState.result.row_selector,
          network_patterns: staged.template?.network_patterns?.length || 0,
        },
      ]);
      setPromotePreflight(null);
      setPromoteConfirm(false);
      setStep("inspect");
      toast.success(`Staged ${staged.file}; continue through Inspect and Test before Review`);
    } catch (error) {
      if (isCurrentCandidate()) {
        toast.error(`Could not stage learned template: ${String(error)}`);
      }
    }
  }

  // F2.7b: Build → the real builder/normalizer (build_template_from_wacz +
  // normalize → review candidate). Needs a saved .wacz; if the session isn't
  // finished yet the backend returns "finish the capture first" (surfaced).
  async function buildDraft() {
    if (!taskId) {
      toast.error("Start a capture first");
      return;
    }
    setBuilding(true);
    setBuildResult(null);
    try {
      const r = await apiPost<NormalizeResponse>("/cockpit/api/captures/normalize", {
        task_id: taskId,
      });
      setBuildResult(r);
      if (r.candidate_path) {
        const candidateFile = r.candidate_path.split(/[\\/]/).pop();
        if (candidateFile) setStagedDraftFile(candidateFile);
      }
      toast.success(
        `Built ${r.host || "draft"} — ${r.status || "candidate"}${
          r.has_download_trigger ? "" : " · no download trigger derived"
        }`,
      );
    } catch (e) {
      toast.error(`Build failed: ${String(e)}`);
    } finally {
      setBuilding(false);
    }
  }

  // F2.7b: Review → the real review surface (normalized candidates). Read-only;
  // promotion stays the deliberate gate on the Promote step.
  async function loadReview() {
    setReviewBusy(true);
    try {
      const r = await apiGet<ReviewCandidatesResponse>("/cockpit/api/review-candidates");
      const cands = r.candidates || [];
      const h = host.trim().toLowerCase();
      const filtered = h ? cands.filter((c) => (c.host || "").toLowerCase() === h) : cands;
      if (stagedDraftFile && !filtered.some((candidate) => candidate.file === stagedDraftFile)) {
        filtered.unshift({
          file: stagedDraftFile,
          host,
          status: "draft_review_required",
          has_row_selectors: learningState.state === "found",
          has_download_trigger:
            learningState.state === "found" && !!learningState.result.trigger_selector,
          network_patterns: stagedTemplate?.network_patterns?.length || 0,
        });
      }
      setReviewCands(filtered);
    } catch (e) {
      toast.error(`Could not load review candidates: ${String(e)}`);
      setReviewCands([]);
    } finally {
      setReviewBusy(false);
    }
  }

  async function promote() {
    if (!resolutionPolicySatisfied) {
      toast.error(
        currentLearningSelection?.reason
          || "Learned options violate the configured quality policy",
      );
      return;
    }
    if (!siteId.trim()) {
      toast.error("Enter the target site id first");
      return;
    }
    try {
      await apiPost("/api/template_manager/promote", {
        file: draftFileForGate,
        enable: true,
      });
      toast.success("Promoted and enabled");
      setPromoteConfirm(false);
    } catch (e) {
      toast.error(`Promote failed: ${String(e)}`);
    }
  }

  // Item 2: end the held-open session. discard=false FINISHES and SAVES the
  // WACZ (the backend finish endpoint writes the FINISH sentinel ->
  // capture_session saves it); discard=true throws the capture away. Either way
  // the wizard resets to a clean Setup. This is the missing "complete the
  // process" action — the flow previously had Promote/Enable + Discard only.
  async function finishSession(discard: boolean) {
    sessionEpochRef.current += 1;
    if (taskId) {
      try {
        await apiPost("/cockpit/api/captures/finish", {
          task_id: taskId,
          discard,
        });
      } catch {
        /* best-effort */
      }
    }
    setTaskId(null);
    setStartedAt(null);
    setArmedField(null);
    setE2ePass(null);
    setVerdict(null);
    setWatchProgress("");
    setOverrideE2e(false);
    setLearningState({ state: "idle" });
    setNetworkState({ state: "idle" });
    setCrawlState({ state: "idle" });
    setLearningRequestId(null);
    clearCandidateState();
    setStep("setup");
    toast.message(
      discard
        ? "Session discarded — context closed"
        : "Session finished — capture saved, context closed",
    );
  }

  const discardSession = () => finishSession(true);

  // CAP-ROBUST (B) — send the held-open live page back to the URL I typed.
  // After a login redirect dumps the session on the site's home/landing page,
  // this drops the GOTO sentinel so capture_session re-navigates to my start URL.
  async function gotoStartUrl() {
    if (!taskId) return;
    try {
      await apiPost("/cockpit/api/captures/goto", { task_id: taskId });
      toast.message("Returning to your capture URL…");
    } catch (e) {
      toast.error(`Could not return to your URL: ${String(e)}`);
    }
  }

  const sessionLive = !!taskId;

  // ── Guided context + handlers (GCW Cut 1) ──────────────────────────────
  const setupUrlOk = (() => {
    const u = setupLoginUrl.trim();
    if (!u) return false;
    try {
      // eslint-disable-next-line no-new
      new URL(u);
      return true;
    } catch {
      return false;
    }
  })();
  const rowField = fields.find((f) => f.key === "row_selectors");
  const requiredSelectorsResolved = !!(
    rowField &&
    ((rowField.entries && rowField.entries.length > 0) || rowField.value)
  );
  const verdictView = mapVerdict(verdict);
  const verdictState: VerdictState = verdictView.pass
    ? verdictView.state
    : overrideE2e
      ? "NEEDS_REVIEW"
      : verdictView.state;
  const guidedCtx: GuidedCtx = {
    setupNameOk: setupName.trim().length > 0,
    setupUrlOk,
    downloadRootOk: rootCheck === null ? true : rootCheck.ok,
    sessionLive,
    loggedIn: sessionLive, // cross-origin: can't introspect; soft nudge
    contentPageVisited: sessionLive,
    liveLearningAttempted:
      learningState.state === "found" || learningState.state === "nothing",
    resolutionPolicySatisfied,
    draftBuilt: !!buildResult || !!stagedDraftFile,
    requiredSelectorsResolved,
    verdictState,
    candidateAssembled: (reviewCands?.length || 0) > 0,
    unreviewedAiEdits: 0, // per-item accept tracking is a later refinement
    promotePreflightOk: promotePreflight?.ok === true,
  };

  // R1 — validate the download folder against the allowlist on field blur.
  async function checkDownloadRoot(path: string) {
    const p = path.trim();
    if (!p) {
      setRootCheck(null); // empty = "use default", no error
      return;
    }
    try {
      const r = await validateDownloadDir(p);
      setRootCheck(r);
    } catch {
      setRootCheck({ ok: false, error: "could not validate path" });
    }
  }

  // R3 — the confirm-gated, audited allowlist widening one-click. First click
  // arms the two-step confirm; second click performs the (audited) add.
  async function addRootToAllowlist() {
    const p = setupDownloadDir.trim();
    if (!p) return;
    if (!allowlistConfirm) {
      setAllowlistConfirm(true);
      return;
    }
    setAllowlistBusy(true);
    try {
      const r = await allowlistAdd(p);
      if (r.ok) {
        setRootCheck({ ok: true });
        setAllowlistConfirm(false);
        toast.success("Root added to the allowlist (audited).");
      } else {
        toast.error(r.error || "could not add root");
      }
    } catch {
      toast.error("could not add root");
    } finally {
      setAllowlistBusy(false);
    }
  }

  // R2 — read-only promote preflight (BAD_TERMS / lint / readiness) before the
  // confirm, so the operator sees green or a precise blocker, not a raw refusal.
  async function runPromotePreflight(file: string, triggerMatchCount?: number) {
    if (!file) return;
    const ownerSessionEpoch = sessionEpochRef.current;
    const ownerCandidateEpoch = candidateEpochRef.current;
    const isCurrentCandidate = () =>
      sessionEpochRef.current === ownerSessionEpoch
      && candidateEpochRef.current === ownerCandidateEpoch;
    setPreflightBusy(true);
    try {
      const r = await promoteCheck(file, triggerMatchCount);
      if (isCurrentCandidate()) setPromotePreflight(r);
    } catch {
      if (isCurrentCandidate()) {
        setPromotePreflight({ ok: false, error: "preflight failed" });
      }
    } finally {
      if (isCurrentCandidate()) setPreflightBusy(false);
    }
  }

  // Linear nav for the guided advance footer.
  const stepIdx = STEPS.findIndex((s) => s.key === step);
  const prevStep = stepIdx > 0 ? STEPS[stepIdx - 1].key : null;
  const nextStep =
    stepIdx >= 0 && stepIdx < STEPS.length - 1 ? STEPS[stepIdx + 1].key : null;
  const stepBlocker = firstBlocker(step, guidedCtx);

  // Cut 2 — read the persisted drift monitor for the current site (the
  // repair-entry signal). Read-only existing route; safe to poll on focus.
  useEffect(() => {
    if (!guided || !siteId.trim()) {
      setDriftStatus(null);
      return;
    }
    let cancelled = false;
    fetchDriftStatus(siteId.trim())
      .then((d) => {
        if (!cancelled) setDriftStatus(d);
      })
      .catch(() => {
        if (!cancelled) setDriftStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [guided, siteId]);

  // Repair landing (null-safe). apiHostChanged stays a caller signal — there's no
  // live re-observation here, so it's reported unchanged unless one is wired.
  const repair = repairSummary(driftStatus, { apiHostChanged: false });
  // Only row_selectors is canvas-re-pickable in this workflow; the compare scopes
  // to it (download trigger / play button are build-derived, not re-pick slots).
  const driftApplicable: DriftSlotKey[] = ["row_selectors"];
  const driftView = driftCompareView(driftSlots, driftApplicable);

  // Cut 2 — post-promote live verify: queue one REAL download against the
  // just-enabled template and watch it land. Reuses test_extract + the history
  // watch (no new route). The real runner download is operator-verified.
  async function runPostPromoteVerify() {
    if (learningState.state === "found" && currentLearningSelection?.status !== "SELECTED") {
      toast.error(currentLearningSelection?.reason || "Learned options violate the configured quality policy");
      return;
    }
    if (!siteId.trim() || !testUrl.trim()) {
      toast.error("Need the enabled site id and a content URL to verify live");
      return;
    }
    setVerifyState("queued");
    const since = Date.now();
    try {
      const r = await apiPost<TestExtractResponse>(
        "/api/template/test_extract",
        {
          site_id: siteId.trim(),
          template: assembleDraft(),
          url: testUrl.trim(),
          persist: true, // a real download for the live verify
          probe: false,
          force_download: true,
        },
      );
      if (!r.ok) {
        setVerifyState("failed");
        toast.error(r.error || "verify failed to start");
        return;
      }
      setVerifyState("watching");
      const row = await watchForVerdict(siteId.trim(), testUrl.trim(), since);
      setVerifyState(mapVerifyRow(row));
    } catch {
      setVerifyState("failed");
    }
  }
  const novncUrl = novnc.data?.url || "";
  // BUG-2: freeze the first non-empty noVNC URL so the embedded iframe's src is
  // referentially stable across re-renders (window resize, query refetch). Once
  // the canvas is connected we never want the URL identity to change under it.
  const frozenNovncRef = useRef<string>("");
  if (novncUrl && !frozenNovncRef.current) {
    frozenNovncRef.current = novncUrl;
  }
  const frozenNovncUrl = frozenNovncRef.current || novncUrl;

  // 269 — drag-resizable "Inspect & refine" rail (per-device, localStorage).
  // The grid's rail track is var(--rail); the splitter sets it from the
  // distance between the pointer and the grid's right edge. Pointer-captured
  // for robust dragging; double-click resets to the default width.
  const rail = useCaptureRailWidth();
  const [draggingRail, setDraggingRail] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);

  const onRailDown = (e: React.PointerEvent) => {
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDraggingRail(true);
  };
  const onRailMove = (e: React.PointerEvent) => {
    if (!draggingRail || !gridRef.current) return;
    const rect = gridRef.current.getBoundingClientRect();
    rail.setWidth(rect.right - e.clientX);
  };
  const onRailUp = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    setDraggingRail(false);
  };

  return (
    <AppShell
      title="Live capture workflow"
      subtitle="Capture → build → inspect → test → review → promote, on one held-open session"
      wide
      trailing={
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleGuided}
            className={cn(
              "rounded-full px-3 py-1 text-[12px] hairline transition-colors",
              guided
                ? "bg-primary/10 text-primary"
                : "text-ink-3 hover:bg-surface-2",
            )}
            title={
              guided
                ? "Guided: rails on (linear, validated). Switch to Expert (flip anywhere)."
                : "Expert: rails off (flip anywhere). Switch to Guided."
            }
          >
            {guided ? "Guided · rails on" : "Expert · rails off"}
          </button>
          {sessionLive && (
            <>
              <Badge variant="outline" className="gap-1">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-green" />
                held open · {fmtElapsed(now - (startedAt || now))}
              </Badge>
              <Button variant="outline" size="sm" onClick={gotoStartUrl}>
                Go to my URL
              </Button>
              <Button size="sm" onClick={() => finishSession(false)}>
                Finish &amp; save
              </Button>
              <Button variant="destructive" size="sm" onClick={discardSession}>
                Discard session
              </Button>
            </>
          )}
        </div>
      }
    >
      <div className="space-y-3 p-2">
        {/* Slice helper card (UI convergence #4) — what this workflow does +
            its safe boundary. References only existing steps/controls. */}
        <Callout tone="info" title="How this workflow works">
          Each step runs on one authenticated session held open from capture
          through promote. If a site shows a challenge it's handed to you to
          complete in the live canvas — nothing is auto-solved. A built template
          is dry-run inspected and tested before the{" "}
          <span className="text-ink">Promote</span> gate, and nothing is applied
          until you approve it there.
        </Callout>
        {/* ── Stepper ── */}
        <div className="flex flex-wrap items-center gap-1.5">
          {STEPS.map((s, i) => {
            const active = s.key === step;
            const isGate = s.key === "promote";
            const st = guided ? stepStatus(s.key, step, guidedCtx) : null;
            const reason = guided ? firstBlocker(s.key, guidedCtx) : null;
            const glyph =
              st === "done"
                ? "✓ "
                : st === "blocked"
                  ? "⚠ "
                  : st === "locked"
                    ? "· "
                    : "";
            const title = !guided
              ? undefined
              : st === "blocked"
                ? `Blocked: ${reason}`
                : st === "current" && reason
                  ? `To continue: ${reason}`
                  : st === "done"
                    ? "Done"
                    : st === "locked"
                      ? "Not yet reachable"
                      : "Current step";
            return (
              <button
                key={s.key}
                onClick={() => setStep(s.key)}
                disabled={guided && st === "locked"}
                title={title}
                className={cn(
                  "rounded-full px-3 py-1 text-[12px] transition-colors",
                  active
                    ? "bg-primary text-white"
                    : st === "done"
                      ? "text-green hover:bg-surface-2"
                      : st === "blocked"
                        ? "text-amber-dim hover:bg-surface-2"
                        : "text-ink-3 hover:bg-surface-2",
                  isGate ? "hairline" : "",
                )}
              >
                <span className="mr-1 opacity-60">{i + 1}</span>
                {guided ? glyph : ""}
                {s.label}
                {isGate ? " ·gate" : ""}
              </button>
            );
          })}
        </div>

        <div
          ref={gridRef}
          className="grid grid-cols-1 gap-4 lg:gap-0 lg:grid-cols-[minmax(0,1fr)_12px_var(--rail)]"
          style={{ "--rail": `${rail.width}px` } as React.CSSProperties}
        >
          {/* ── Live canvas (held open through every step) ── */}
          <Card className="overflow-hidden p-0">
            <div className="flex items-center justify-between border-b border-hairline px-3 py-2 text-[12px]">
              <span className="text-ink-3">
                {host ? <b className="text-ink">{host}</b> : "no session"}
                {sessionLive ? " · cloakbrowser" : ""}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant={armedField ? "ghost" : "outline"}
                  onClick={cancelPick}
                  disabled={!armedField}
                >
                  Interact
                </Button>
                <Button
                  size="sm"
                  variant={armedField ? "outline" : "ghost"}
                  onClick={() => armPick("row_selectors")}
                  disabled={!sessionLive}
                  title={
                    sessionLive
                      ? "Click, then pick an element in the live session"
                      : "Available once a session is open (Capture step)"
                  }
                >
                  Pick element
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => suggestRows(false)}
                  disabled={!sessionLive || suggesting}
                  title={
                    sessionLive
                      ? "Auto-detect repeating row groups on the live page"
                      : "Available once a session is open (Capture step)"
                  }
                >
                  {suggesting ? "Detecting…" : "Suggest rows"}
                </Button>
              </div>
            </div>
            {armedField && (
              <div className="border-b border-hairline bg-amber-soft px-3 py-1.5 text-[12px] text-amber-dim">
                Pick mode — click the element in the live session to grab its selector
                <button className="ml-2 underline" onClick={cancelPick}>
                  cancel
                </button>
              </div>
            )}
            <div className="relative min-h-[82vh] bg-surface-2">
              {sessionLive && novncUrl ? (
                <iframe
                  title="live capture session"
                  // BUG-2: bind the iframe src ONCE (frozen on the first live
                  // URL) and give it a stable key. A window resize reconciles
                  // this subtree; if src were a value that shifts (query refetch,
                  // derived string) React could remount the iframe, opening a
                  // fresh VNC WebSocket that shows the password prompt again. A
                  // constant key plus frozen src keeps the SAME element across
                  // resizes, so the established (auto-authed) connection survives.
                  key="bd-novnc-frame"
                  src={frozenNovncUrl}
                  allow="clipboard-read; clipboard-write"
                  className="h-full min-h-[82vh] w-full border-0"
                />
              ) : (
                <div className="flex min-h-[82vh] items-center justify-center p-6 text-center text-[13px] text-ink-3">
                  {!sessionLive
                    ? "Start a capture to open a held-open session here."
                    : "Set BD_NOVNC_URL in the server environment to embed the live session canvas."}
                </div>
              )}
            </div>
          </Card>

          {/* ── Rail drag-resize splitter (the 12px middle grid track) ── */}
          <div
            data-testid="rail-resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize inspect panel"
            onPointerDown={onRailDown}
            onPointerMove={onRailMove}
            onPointerUp={onRailUp}
            onDoubleClick={() => rail.setWidth(CAPTURE_RAIL_DEFAULT)}
            className="group hidden cursor-col-resize items-stretch justify-center lg:flex"
          >
            <div
              className={cn(
                "h-full w-px transition-colors group-hover:bg-primary",
                draggingRail ? "bg-primary" : "bg-hairline",
              )}
            />
          </div>

          {/* ── Step rail ── */}
          <div className="space-y-4">
            {guided && (
              <Card className="space-y-1 border-l-2 border-l-primary p-3">
                <div className="text-[12px] font-medium text-ink">
                  {STEP_COPY[step].purpose}
                </div>
                <div className="text-[11px] text-ink-3">
                  <b>Do this:</b> {STEP_COPY[step].doThis}
                </div>
                <div className="text-[11px] text-ink-3">
                  <b>Success looks like:</b> {STEP_COPY[step].success}
                </div>
              </Card>
            )}
            {guided &&
              draftStore.saved &&
              draftStore.saved.furthestStep !== step && (
                <Card className="flex items-center justify-between gap-2 bg-surface-2 p-3 text-[12px]">
                  <span className="text-ink-3">
                    A saved draft for this host reached{" "}
                    <b className="text-ink">{draftStore.saved.furthestStep}</b>.
                    Re-open the session to continue from there.
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        draftStore.saved &&
                        setStep(draftStore.saved.furthestStep)
                      }
                    >
                      Resume
                    </Button>
                    <button
                      type="button"
                      className="text-[11px] underline text-ink-3"
                      onClick={() => draftStore.clear()}
                    >
                      dismiss
                    </button>
                  </div>
                </Card>
              )}
            {guided && repair.needed && (
              <Card className="space-y-2 border-l-2 border-l-amber-dim bg-amber-soft p-3 text-[12px]">
                <div className="font-medium text-amber-dim">
                  This site has drifted — repair instead of re-walking
                </div>
                <div className="text-ink-2">{repair.headline}</div>
                {(repair.lastSelector || repair.lastUrl) && (
                  <div className="text-[11px] text-ink-3">
                    Last failure:{" "}
                    {repair.lastSelector ? (
                      <code>{repair.lastSelector}</code>
                    ) : (
                      "—"
                    )}
                    {repair.lastUrl ? (
                      <>
                        {" "}
                        on <code>{repair.lastUrl}</code>
                      </>
                    ) : null}
                  </div>
                )}
                <div className="flex items-center gap-2 pt-1">
                  <Button size="sm" onClick={() => setStep("inspect")}>
                    Repair selectors (Inspect)
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setStep("review");
                      setDriftCompareOpen(true);
                    }}
                  >
                    Compare to live first
                  </Button>
                </div>
              </Card>
            )}
            {step === "setup" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium">Set up the site</div>
                <p className="text-[12px] text-ink-3">
                  Create the site and (optionally) store its login credential,
                  then open the capture. The id is filled in for you downstream —
                  no more hand-typing the 8-hex id.
                </p>
                <label className="block text-[12px] text-ink-3">
                  Site name
                  <Input
                    value={setupName}
                    onChange={(e) => setSetupName(e.target.value)}
                    placeholder="WowGirls"
                    className="mt-1"
                  />
                </label>
                <label className="block text-[12px] text-ink-3">
                  Login URL
                  <Input
                    value={setupLoginUrl}
                    onChange={(e) => setSetupLoginUrl(e.target.value)}
                    placeholder="https://example.com/login"
                    className="mt-1"
                  />
                </label>
                <label className="block text-[12px] text-ink-3">
                  Download folder
                  <Input
                    value={setupDownloadDir}
                    onChange={(e) => setSetupDownloadDir(e.target.value)}
                    onBlur={() => checkDownloadRoot(setupDownloadDir)}
                    placeholder="/path/under/an/allowed/download/root"
                    className="mt-1"
                  />
                  <span className="mt-1 block text-[11px] text-ink-3">
                    Where files are saved. Must be under an allowed download root
                    (Settings → Global). Without it, Test clicks the trigger but
                    saves nothing — and Promote stays blocked.
                  </span>
                </label>
                {guided && rootCheck && !rootCheck.ok && (
                  <div className="rounded-md border border-amber-soft bg-amber-soft px-3 py-2 text-[12px] text-amber-dim">
                    <div className="font-medium">
                      Not under an allowed root — Test will save nothing and
                      Promote will be blocked.
                    </div>
                    {rootCheck.error && (
                      <div className="mt-0.5 opacity-80">{rootCheck.error}</div>
                    )}
                    <div className="mt-2 flex items-center gap-2">
                      <Button
                        size="sm"
                        variant={allowlistConfirm ? "destructive" : "outline"}
                        onClick={addRootToAllowlist}
                        disabled={allowlistBusy}
                      >
                        {allowlistBusy
                          ? "Adding…"
                          : allowlistConfirm
                            ? "Confirm: widen the allowlist"
                            : "Add this root to the allowlist"}
                      </Button>
                      {allowlistConfirm && !allowlistBusy && (
                        <button
                          type="button"
                          className="text-[11px] underline text-ink-3"
                          onClick={() => setAllowlistConfirm(false)}
                        >
                          cancel
                        </button>
                      )}
                      <Link
                        to="/settings"
                        className="text-[11px] underline text-ink-3"
                      >
                        open Settings → Global
                      </Link>
                    </div>
                    {allowlistConfirm && (
                      <div className="mt-1 text-[11px] opacity-80">
                        Widening the allowlist is a security-relevant change and
                        is recorded in the audit log.
                      </div>
                    )}
                  </div>
                )}
                {guided && rootCheck && rootCheck.ok && setupDownloadDir.trim() && (
                  <div className="text-[11px] text-green">
                    ✓ Download root is under an allowed root.
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <label className="block text-[12px] text-ink-3">
                    quality_preference
                    <Input
                      aria-label="quality_preference"
                      value={qualityPreference}
                      onChange={(e) => changeQualityPreference(e.target.value)}
                      placeholder="2160,1080,720"
                      className="mt-1"
                    />
                  </label>
                  <label className="block text-[12px] text-ink-3">
                    min_resolution
                    <Input
                      aria-label="min_resolution"
                      type="number"
                      min={0}
                      value={minResolution}
                      onChange={(e) => changeMinResolution(Number(e.target.value))}
                      className="mt-1"
                    />
                  </label>
                </div>
                <label className="flex items-start gap-2 text-[12px] text-ink-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={logNetwork}
                    onChange={(e) => setLogNetwork(e.target.checked)}
                  />
                  <span>
                    <span className="font-medium">Record network responses</span>
                    <span className="block text-[11px] text-ink-3">
                      Enables the existing log_network runner setting. Build also
                      has a separate live evidence action for this Capture session.
                    </span>
                  </span>
                </label>
                <label className="block text-[12px] text-ink-3">
                  Username <span className="text-ink-3">(optional)</span>
                  <Input
                    value={setupUsername}
                    onChange={(e) => setSetupUsername(e.target.value)}
                    placeholder="you@example.com"
                    autoComplete="off"
                    className="mt-1"
                  />
                </label>
                <label className="block text-[12px] text-ink-3">
                  Password <span className="text-ink-3">(optional)</span>
                  <div className="mt-1">
                    <SecretField
                      value={setupPassword}
                      onChange={setSetupPassword}
                      ariaLabel="site login password"
                      placeholder="stored encrypted as a @cred reference"
                    />
                  </div>
                </label>
                <p className="text-[11px] text-ink-3">
                  A password is stored in the encrypted secrets backend as a{" "}
                  <code>@cred:</code> reference — never in plaintext, never in a
                  template. Leave it blank to log in by hand during the capture.
                </p>
                <Button onClick={setupSite} disabled={setupBusy}>
                  {setupBusy ? "Setting up…" : "Create site & continue"}
                </Button>
              </Card>
            )}

            {step === "capture" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium">Start a capture</div>
                <p className="text-[12px] text-ink-3">
                  Opens a headed session that stays open behind the whole workflow.
                </p>
                <label className="block text-[12px] text-ink-3">
                  Start URL
                  <Input
                    value={startUrl}
                    onChange={(e) => setStartUrl(e.target.value)}
                    placeholder="https://example.com/login"
                    className="mt-1"
                  />
                </label>
                <Button onClick={startCapture} disabled={sessionLive}>
                  {sessionLive ? "Session already open" : "Open session"}
                </Button>
              </Card>
            )}

            {step === "build" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium text-ink">Build draft</div>
                <p className="text-[12px] text-ink-3">
                  Navigate the held-open session to a scene or listing, then learn
                  the selector that works on today&apos;s rendered page. BAR is tried
                  before a one-click DROPDOWN. Network evidence and listing crawl
                  keep their own visible state.
                </p>
                <AffordanceLearningPanel
                  learning={learningState}
                  network={networkState}
                  crawl={crawlState}
                  qualityPreference={qualityPreference}
                  minResolution={minResolution}
                  logNetwork={logNetwork}
                  onQualityPreferenceChange={changeQualityPreference}
                  onMinResolutionChange={changeMinResolution}
                  onLogNetworkChange={setLogNetwork}
                  onSavePolicy={saveLearningPolicy}
                  saveAvailable={!!learningRequestId}
                  onLearn={learnAffordance}
                  onCaptureNetwork={captureNetworkEvidence}
                  onCrawl={crawlListing}
                  onSave={stageLearnedTemplate}
                />
                <div className="border-t border-hairline pt-3">
                  <div className="text-[12px] font-medium text-ink">
                    Saved-capture normalization
                  </div>
                  <p className="text-[11px] text-ink-3">
                    Optional legacy evidence path: after Finish &amp; save, normalize
                    the WACZ into a review candidate. Live learning above works
                    before the session is finished.
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={buildDraft} disabled={building || !taskId}>
                  {building ? "Building…" : "Build draft"}
                </Button>
                {buildResult ? (
                  <Card className="space-y-1 hairline p-3 text-[12px]">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-ink">{buildResult.host || "—"}</span>
                      <span className="text-ink-3">{buildResult.status || "candidate"}</span>
                    </div>
                    <div className="text-ink-3">
                      download trigger: {buildResult.has_download_trigger ? "yes" : "no"}
                      {typeof buildResult.network_patterns === "number"
                        ? ` · ${buildResult.network_patterns} network patterns`
                        : ""}
                    </div>
                    {buildResult.warnings?.length ? (
                      <div className="text-amber-700">
                        {buildResult.warnings.length} warning(s):{" "}
                        {buildResult.warnings.slice(0, 3).join("; ")}
                      </div>
                    ) : null}
                    {buildResult.resolutions?.length ? (
                      <div className="text-ink-3">
                        {buildResult.resolutions.length} resolution(s) suggested
                      </div>
                    ) : null}
                    <div className="text-[11px] text-ink-3">
                      Candidate written — review it before promote. Nothing enabled.
                    </div>
                  </Card>
                ) : null}
              </Card>
            )}

            {step === "inspect" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium">Inspect &amp; refine</div>
                <p className="text-[12px] text-ink-3">
                  Wrong or missing? Flip the canvas to Pick and grab it live.
                </p>
                {inspectState &&
                ((inspectState.actions?.length ?? 0) > 0 || inspectState.verify) ? (
                  <Card className="space-y-1 hairline p-3">
                    <div className="flex items-center justify-between text-[12px]">
                      <span className="font-medium">Live HUD analysis</span>
                      {inspectState.verify?.tier ? (
                        <span className="text-ink-3">
                          readiness: {inspectState.verify.tier}
                          {typeof inspectState.verify.action_count === "number"
                            ? ` · ${inspectState.verify.action_count} actions`
                            : ""}
                        </span>
                      ) : null}
                    </div>
                    {(inspectState.actions ?? []).slice(-6).map((a, i) => (
                      <div
                        key={`act-${i}-${a.selector ?? ""}`}
                        className="flex items-center justify-between gap-2 text-[12px]"
                      >
                        <code className="flex-1 truncate text-ink">
                          {a.selector || "—"}
                        </code>
                        <span className="text-ink-3">{a.role || "element"}</span>
                      </div>
                    ))}
                    {(inspectState.verify?.warnings ?? []).map((w, i) => (
                      <div key={`warn-${i}`} className="text-[11px] text-amber-600">
                        {w}
                      </div>
                    ))}
                  </Card>
                ) : null}
                {fields.map((f) => (
                  <div key={f.key} className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[12px] text-ink-2">{f.label}</span>
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant={armedField === f.key ? "outline" : "ghost"}
                          onClick={() => armPick(f.key)}
                          disabled={!sessionLive}
                        >
                          {armedField === f.key ? "Picking…" : "Pick from live page"}
                        </Button>
                        {!f.multi && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => removeField(f.key)}
                            disabled={!f.value}
                          >
                            Remove
                          </Button>
                        )}
                      </div>
                    </div>
                    {f.multi ? (
                      <div className="space-y-1">
                        {(f.entries ?? []).length === 0 ? (
                          <div className="text-[11px] text-ink-3">
                            No selectors yet — Pick from the live page (each click
                            adds one), or type one below.
                          </div>
                        ) : (
                          (f.entries ?? []).map((sel, i) => (
                            <div
                              key={`${f.key}-${i}-${sel}`}
                              className="flex items-center gap-2"
                            >
                              <code className="flex-1 truncate font-mono text-[12px] text-ink">
                                {sel}
                              </code>
                              <button
                                aria-label={`Remove selector ${i + 1}`}
                                title="Remove this selector (draft only)"
                                className="px-1 text-ink-3 hover:text-red-600"
                                onClick={() => removePickEntry(f.key, i)}
                              >
                                ✕
                              </button>
                            </div>
                          ))
                        )}
                        {undoBuf && undoBuf.key === f.key && (
                          <button
                            className="text-[11px] text-ink-3 underline"
                            onClick={undoRemovePickEntry}
                          >
                            Undo remove
                          </button>
                        )}
                        <Input
                          placeholder="add a selector…"
                          className="font-mono text-[12px]"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              addPickEntry(f.key, e.currentTarget.value);
                              e.currentTarget.value = "";
                            }
                          }}
                        />
                      </div>
                    ) : (
                      <Input
                        value={f.value}
                        onChange={(e) => setFieldValue(f.key, e.target.value)}
                        placeholder="—"
                        className="font-mono text-[12px]"
                      />
                    )}
                  </div>
                ))}

                <div className="flex items-center gap-2 pt-1">
                  <Button size="sm" variant="outline" onClick={askAi} disabled={aiBusy}>
                    {aiBusy ? "Asking…" : "AI suggestion"}
                  </Button>
                  <span className="text-[11px] text-ink-3">
                    flags decoys · tightens selectors · fills gaps
                  </span>
                </div>
                {ai?.ok && ai.suggestions?.length ? (
                  <Card className="space-y-2 hairline p-3">
                    <div className="flex items-center justify-between text-[12px]">
                      <span className="font-medium">AI suggestions</span>
                      <span className="text-ink-3">
                        {ai.model}
                        {typeof ai.latency_ms === "number" ? ` · ${ai.latency_ms}ms` : ""}
                      </span>
                    </div>
                    {ai.suggestions.map((s, i) => (
                      <div key={i} className="space-y-1 border-t border-hairline pt-2 first:border-0 first:pt-0">
                        {s.rationale && (
                          <div className="text-[12px] text-ink-2">{s.rationale}</div>
                        )}
                        {s.selector && (
                          <div className="flex items-center justify-between gap-2">
                            <code className="truncate text-[12px] text-ink">{s.selector}</code>
                            <Button size="sm" onClick={() => applyAiSelector(s.selector!, s.role)}>
                              Apply
                            </Button>
                          </div>
                        )}
                      </div>
                    ))}
                    <div className="text-[11px] text-ink-3">
                      AI output is review-before-promote.
                    </div>
                  </Card>
                ) : null}
              </Card>
            )}

            {step === "test" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium">Test extract</div>
                <p className="text-[12px] text-ink-3">
                  Drives one real extraction off this draft. Persist is OFF by
                  default — nothing is written or enabled.
                </p>
                <label className="block text-[12px] text-ink-3">
                  Site id
                  <Input
                    value={siteId}
                    onChange={(e) => setSiteId(e.target.value)}
                    placeholder="site id this draft targets"
                    className="mt-1"
                  />
                </label>
                <label className="block text-[12px] text-ink-3">
                  Test URL
                  <Input
                    value={testUrl}
                    onChange={(e) => setTestUrl(e.target.value)}
                    placeholder="https://example.com/video/123"
                    className="mt-1"
                  />
                </label>
                <label className="flex items-center gap-2 text-[12px] text-ink-2">
                  <input
                    type="checkbox"
                    checked={persist}
                    onChange={(e) => setPersist(e.target.checked)}
                  />
                  Persist learned selectors (off = nothing written)
                </label>
                <label className="flex items-center gap-2 text-[12px] text-ink-2">
                  <input
                    type="checkbox"
                    checked={probe}
                    onChange={(e) => setProbe(e.target.checked)}
                  />
                  Quick probe — sample first bytes &amp; abort (no full download)
                </label>
                <label className="flex items-center gap-2 text-[12px] text-ink-2">
                  <input
                    type="checkbox"
                    checked={force}
                    onChange={(e) => setForce(e.target.checked)}
                  />
                  Force re-download — re-test even if already downloaded
                </label>
                <Button
                  onClick={runTest}
                  disabled={testing || !resolutionPolicySatisfied}
                  title={!resolutionPolicySatisfied ? currentLearningSelection?.reason : undefined}
                >
                  {testing
                    ? "Running…"
                    : probe
                      ? "Run quick probe"
                      : "Run test download"}
                </Button>
                {watching && (
                  <div className="rounded-md hairline p-2 text-[12px] text-ink-3">
                    Watching the run for a real download…
                    {watchProgress ? (
                      <div className="mt-1 text-ink-2">{watchProgress}</div>
                    ) : null}
                  </div>
                )}
                {!guided && !watching && e2ePass !== null && (
                  <div
                    className={[
                      "rounded-md p-2 text-[12px]",
                      e2ePass ? "hairline bg-green-soft text-ink-2" : "hairline bg-red-soft text-red",
                    ].join(" ")}
                  >
                    {e2ePass ? (
                      <div>
                        ✓ Downloaded{" "}
                        {verdict?.filename ? <code>{verdict.filename}</code> : "a file"} ·{" "}
                        {fmtBytes(verdict?.file_size ?? 0)}
                        {persist ? " · persisted" : " · persist OFF (nothing written)"}
                      </div>
                    ) : (verdict?.file_size ?? -1) === 0 ? (
                      <div>
                        ✗ Clicked but saved <strong>0 bytes</strong> — set a Download
                        folder in Setup, then re-test. (Promote stays blocked.)
                      </div>
                    ) : (
                      <div>
                        ✗ {verdict?.message || "No download"}
                        {verdict?.status ? ` (status: ${verdict.status})` : ""}
                      </div>
                    )}
                  </div>
                )}
                {guided && !watching && verdict && (
                  <div
                    className={cn(
                      "rounded-md p-2 text-[12px]",
                      verdictView.pass
                        ? "hairline bg-green-soft text-ink-2"
                        : verdictView.state === "NEEDS_REVIEW"
                          ? "hairline bg-amber-soft text-amber-dim"
                          : "hairline bg-red-soft text-red",
                    )}
                  >
                    <div className="font-medium">
                      {verdictView.pass
                        ? "✓ "
                        : verdictView.state === "NEEDS_REVIEW"
                          ? "⚠ "
                          : "✗ "}
                      {verdictView.label}
                    </div>
                    <div className="mt-0.5 opacity-90">{verdictView.detail}</div>
                    {verdictView.state === "FAILED" &&
                      (() => {
                        const h = failureHint(
                          verdict?.message || verdict?.status,
                        );
                        return (
                          <div className="mt-1 opacity-90">
                            <div>{h.sentence}</div>
                            <div className="mt-0.5">
                              <b>Fix:</b> {h.fix}
                            </div>
                          </div>
                        );
                      })()}
                  </div>
                )}
                {triggerLiveCount !== null && (
                  <div
                    className={cn(
                      "rounded-md p-2 text-[12px] hairline",
                      triggerLiveCount === 0
                        ? "bg-amber-soft text-amber-dim"
                        : "bg-surface-2 text-ink-3",
                    )}
                  >
                    trigger: {triggerLiveCount} match
                    {triggerLiveCount === 1 ? "" : "es"} on live
                    {triggerLiveCount === 0
                      ? " — Promote will flag a stale trigger"
                      : ""}
                  </div>
                )}
              </Card>
            )}

            {step === "review" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium text-ink">Review</div>
                <p className="text-[12px] text-ink-3">
                  Compare evidence and the diff against the prior gold before
                  promoting. Nothing here enables a template.
                </p>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="outline" onClick={loadReview} disabled={reviewBusy}>
                    {reviewBusy ? "Loading…" : "Load review candidates"}
                  </Button>
                  <Link
                    to="/dom-analyzer"
                    className="text-[12px] text-accent underline-offset-2 hover:underline"
                  >
                    Open DOM analyzer / workbench →
                  </Link>
                </div>
                {stagedDraftFile && stagedTemplate ? (
                  <Card className="space-y-2 hairline p-3 text-[12px]">
                    <div className="font-medium text-ink">
                      Exact staged artifact · <code>{stagedDraftFile}</code>
                    </div>
                    <div>
                      {stagedTemplate.learning_evidence?.shape || "UNKNOWN"} · pattern{" "}
                      <code>{stagedTemplate.patterns?.join(", ") || "—"}</code>
                    </div>
                    <div>
                      row selector{" "}
                      <code>
                        {stagedTemplate.learned?.download?.row_selectors?.join(", ") || "—"}
                      </code>
                    </div>
                    <div>
                      trigger selector{" "}
                      <code>
                        {stagedTemplate.learned?.download?.trigger_selectors?.join(", ") || "BAR (none)"}
                      </code>{" "}
                      · URL attribute{" "}
                      <code>{stagedTemplate.learned?.download?.url_attribute || "href"}</code>
                    </div>
                    <div>
                      resolutions: {(stagedTemplate.resolutions || []).map((height) => `${height}p`).join(", ") || "—"}
                    </div>
                    <div>
                      quality_preference {stagedTemplate.config_defaults?.quality_preference || "—"} · min_resolution{" "}
                      {stagedTemplate.config_defaults?.min_resolution ?? "—"}p
                    </div>
                    <div className={currentLearningSelection?.status === "SELECTED" ? "text-green" : "text-red"}>
                      policy: {currentLearningSelection?.status || "UNKNOWN"}
                      {currentLearningSelection?.option?.height
                        ? ` · planned ${currentLearningSelection.option.height}p`
                        : currentLearningSelection?.reason
                          ? ` · ${currentLearningSelection.reason}`
                          : ""}
                    </div>
                    <div>
                      DOM/network: {learningState.state === "found"
                        ? learningState.result.corroboration?.status || "NONE"
                        : "NONE"}
                    </div>
                    {learningState.state === "found"
                      ? (learningState.result.network_evidence || []).map((row, index) => (
                          <div key={`review-network-${row.url}-${index}`} className="truncate font-mono text-[11px] text-ink-3">
                            {row.kind || "media"}: {row.url}
                          </div>
                        ))
                      : null}
                    <div className="text-ink-3">
                      network patterns: {stagedTemplate.network_patterns?.length || 0} · DOM options proven:{" "}
                      {stagedTemplate.learning_evidence?.dom_options_proven ? "yes" : "no"}
                    </div>
                  </Card>
                ) : null}
                {reviewCands !== null ? (
                  reviewCands.length === 0 ? (
                    <div className="text-[12px] text-ink-3">
                      No review candidates{host.trim() ? ` for ${host.trim()}` : ""} yet —
                      build the draft first.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {reviewCands.map((c) => (
                        <Card key={c.file} className="space-y-1 hairline p-3 text-[12px]">
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-ink">{c.host || c.file}</span>
                            <span className="text-ink-3">{c.status || "—"}</span>
                          </div>
                          <div className="text-ink-3">
                            row selectors: {c.has_row_selectors ? "yes" : "no"} · download
                            trigger: {c.has_download_trigger ? "yes" : "no"}
                            {typeof c.network_patterns === "number"
                              ? ` · ${c.network_patterns} net patterns`
                              : ""}
                          </div>
                          {c.warnings?.length ? (
                            <div className="text-amber-700">
                              {c.warnings.length} warning(s)
                            </div>
                          ) : null}
                          {c.promote_cmd ? (
                            <code className="block truncate text-[11px] text-ink-3">
                              {c.promote_cmd}
                            </code>
                          ) : null}
                        </Card>
                      ))}
                    </div>
                  )
                ) : null}
                {guided && (
                  <div className="rounded-md hairline p-3 text-[12px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">
                        Compare to the live site
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setDriftCompareOpen((v) => !v)}
                      >
                        {driftCompareOpen ? "Hide" : "Compare"}
                      </Button>
                    </div>
                    {driftCompareOpen && (
                      <div className="mt-2 space-y-2">
                        <p className="text-[11px] text-ink-3">
                          Cheap check: re-pick the row selector on the held-open
                          session and confirm it still resolves. (The heavier
                          gold-vs-live diff is deferred.)
                        </p>
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            variant={armedField ? "outline" : "ghost"}
                            onClick={() => armPick("row_selectors")}
                            disabled={!sessionLive}
                            title={
                              sessionLive
                                ? "Re-pick the row selector on the live canvas"
                                : "Open the session first (Capture)"
                            }
                          >
                            Re-pick row selector
                          </Button>
                          <button
                            type="button"
                            className="text-[11px] underline text-green"
                            onClick={() =>
                              setDriftSlots((s) => ({
                                ...s,
                                row_selectors: "resolved",
                              }))
                            }
                          >
                            still resolves
                          </button>
                          <button
                            type="button"
                            className="text-[11px] underline text-amber-dim"
                            onClick={() =>
                              setDriftSlots((s) => ({
                                ...s,
                                row_selectors: "drifted",
                              }))
                            }
                          >
                            drifted
                          </button>
                        </div>
                        <div
                          className={cn(
                            "rounded px-2 py-1 text-[11px]",
                            driftView.state === "clean"
                              ? "bg-green-soft text-ink-2"
                              : driftView.state === "drifted"
                                ? "bg-amber-soft text-amber-dim"
                                : "text-ink-3",
                          )}
                        >
                          <b>{driftView.label}.</b> {driftView.detail}
                          {driftView.state === "drifted" && (
                            <>
                              {" "}
                              <button
                                type="button"
                                className="underline"
                                onClick={() => setStep("inspect")}
                              >
                                Repair in Inspect →
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            )}

            {step === "promote" && (
              <Card className="space-y-3 p-4">
                <div className="text-[13px] font-medium">Promote</div>
                <p className="text-[12px] text-ink-3">
                  The manual gate. Promote normalizes the draft into a reviewed
                  gold and enables it. This is never automatic.
                </p>
                <label className="block text-[12px] text-ink-3">
                  Site id
                  <Input
                    value={siteId}
                    onChange={(e) => setSiteId(e.target.value)}
                    placeholder="site id to enable"
                    className="mt-1"
                  />
                </label>
                {guided && (
                  <div className="rounded-md hairline p-2 text-[12px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-ink-3">
                        Preflight the draft for blocked terms and unsafe
                        selectors before enabling.
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={preflightBusy || !host}
                        onClick={() =>
                          runPromotePreflight(
                            draftFileForGate,
                            triggerLiveCount ?? undefined,
                          )
                        }
                      >
                        {preflightBusy ? "Checking…" : "Run preflight"}
                      </Button>
                    </div>
                    {promotePreflight && promotePreflight.ok && (
                      <div className="mt-2 text-green">
                        ✓ Safe to promote — no blocked terms or unsafe selectors.
                      </div>
                    )}
                    {promotePreflight &&
                      (promotePreflight.gate_warnings || []).length > 0 && (
                        <div className="mt-2 space-y-1 text-amber-dim">
                          {(promotePreflight.gate_warnings || []).map((w, i) => (
                            <div key={i} className="opacity-80">
                              ⚠ {w}
                            </div>
                          ))}
                        </div>
                      )}
                    {promotePreflight && !promotePreflight.ok && (
                      <div className="mt-2 space-y-1 text-amber-dim">
                        <div className="font-medium">
                          Blocked: {promotePreflight.error || "preflight failed"}
                        </div>
                        {(promotePreflight.gate_errors || []).map((g, i) => (
                          <div key={i} className="opacity-80">
                            • {g}
                          </div>
                        ))}
                        {(promotePreflight.lint_warnings || []).length > 0 && (
                          <div className="opacity-80">
                            • {(promotePreflight.lint_warnings || []).length}{" "}
                            selector warning(s) — re-pick a more specific row
                            selector in Inspect.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {/* GCW-4: promote is gated on a green e2e (a real download this
                    session). A 0-byte "done" is NOT a pass. An explicit override
                    covers the cases the e2e legitimately can't complete in session. */}
                {e2ePass === true ? (
                  <div className="rounded-md hairline bg-green-soft p-2 text-[12px] text-ink-2">
                    ✓ e2e verified this session — a real download
                    {verdict?.filename ? (
                      <>
                        {" "}
                        (<code>{verdict.filename}</code>)
                      </>
                    ) : null}
                    .
                  </div>
                ) : (
                  <div className="space-y-1 rounded-md hairline bg-amber-soft p-2 text-[12px] text-ink-2">
                    <div>
                      Promote is gated on a green Test (a real download, non-zero
                      bytes) this session.
                      {(verdict?.file_size ?? -1) === 0
                        ? " The last Test saved 0 bytes — set a Download folder in Setup."
                        : " Run the Test step first."}
                    </div>
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={overrideE2e}
                        onChange={(e) => setOverrideE2e(e.target.checked)}
                      />
                      Promote without verifying (the e2e can't complete in session)
                    </label>
                  </div>
                )}
                {!promoteConfirm ? (
                  <Button
                    variant="outline"
                    disabled={
                      !(e2ePass === true || overrideE2e)
                      || !resolutionPolicySatisfied
                      || (guided && promotePreflight?.ok !== true)
                    }
                    title={
                      !resolutionPolicySatisfied
                        ? currentLearningSelection?.reason
                        : undefined
                    }
                    onClick={() => setPromoteConfirm(true)}
                  >
                    Promote &amp; enable…
                  </Button>
                ) : (
                  <div className="space-y-2 rounded-md hairline bg-amber-soft p-2">
                    <div className="text-[12px] text-ink-2">
                      Enable <code>{siteId || "—"}</code> for live extraction? The
                      prior gold is backed up first.
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={promote}>
                        Confirm enable
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setPromoteConfirm(false)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
                {guided && (
                  <div className="rounded-md hairline p-2 text-[12px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">
                        Verify live (optional)
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          verifyState === "queued" || verifyState === "watching"
                        }
                        onClick={runPostPromoteVerify}
                      >
                        {verifyState === "queued" || verifyState === "watching"
                          ? "Verifying…"
                          : "Queue a real download"}
                      </Button>
                    </div>
                    <p className="mt-1 text-[11px] text-ink-3">
                      After enabling, queue one real download against the
                      just-enabled template and watch it land — the end-to-end
                      check the OPV runbook does by hand. Uses the URL from the
                      Test step.
                    </p>
                    {verifyState !== "idle" &&
                      (() => {
                        const v = verifyView(verifyState);
                        return (
                          <div
                            className={cn(
                              "mt-1 rounded px-2 py-1 text-[11px]",
                              v.state === "verified"
                                ? "bg-green-soft text-ink-2"
                                : v.state === "failed"
                                  ? "bg-red-soft text-red"
                                  : "text-ink-3",
                            )}
                          >
                            <b>{v.label}.</b> {v.detail}
                          </div>
                        );
                      })()}
                  </div>
                )}
                <div className="mt-1 border-t border-hairline pt-3">
                  <p className="mb-2 text-[12px] text-ink-3">
                    Done? Finish saves the capture (WACZ) and closes the
                    session. You don&apos;t have to promote to finish.
                  </p>
                  <Button onClick={() => finishSession(false)} disabled={!sessionLive}>
                    Finish &amp; save
                  </Button>
                </div>
              </Card>
            )}
            {guided && (
              <Card className="flex items-center justify-between gap-2 p-3">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!prevStep}
                  onClick={() => prevStep && setStep(prevStep)}
                >
                  ← Back
                </Button>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    className="text-[11px] underline text-ink-3"
                    onClick={() => {
                      draftStore.save({
                        host,
                        furthestStep: step,
                        fields: {
                          setupName,
                          setupLoginUrl,
                          setupDownloadDir,
                          siteId,
                        },
                        siteId,
                      });
                      toast.success("Draft saved — resume from this host later.");
                    }}
                  >
                    Save draft &amp; exit
                  </button>
                  <Button
                    size="sm"
                    disabled={!nextStep || stepBlocker !== null}
                    title={stepBlocker ? `Blocked: ${stepBlocker}` : undefined}
                    onClick={() => nextStep && setStep(nextStep)}
                  >
                    Continue →
                  </Button>
                </div>
              </Card>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

export default CaptureWorkflow;
