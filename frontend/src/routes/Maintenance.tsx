import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { apiDelete, apiGet, apiPost, apiPostForm } from "@/lib/api-client";
import { Callout } from "@/components/ui/Callout";
import {
  PluginConfigForm,
  type PluginConfigField,
  type PluginConfigValues,
} from "@/components/PluginConfigForm";
import { DangerZone } from "@/components/ui/DangerZone";
import {
  downloadDiagnosticsBundle,
  useDiagBundlePreview,
  useRetentionApply,
  useRetentionAudit,
  useRetentionPreview,
  useRightsAudit,
  useRightsBlocklist,
  useRightsRemove,
  useSchedExportAdd,
  useSchedExportRemove,
  useSchedExportRunNow,
  useSchedExports,
} from "@/hooks/useGovernance";
import {
  useCrashAction,
  useCrashScan,
  useFileReveal,
  usePauseAll,
  useRateLimitStatus,
  useResumeAll,
  useRetryPolicy,
  useSetConcurrent,
} from "@/hooks/useOpsControls";
import { formatBytes, formatTimestamp } from "@/lib/format";
import type { OkResult } from "@/lib/api-types";

// GUI parity (178) — Maintenance & Diagnostics. Surfaces 8 existing operator
// endpoints under the risk model: benign operational actions use a one-step
// confirm; state-resetting / destructive actions get a labelled yes/no confirm.
// Surface-only — never reimplements the underlying operation; all writes are confirm-gated.

type UserTemplate = {
  id?: string;
  tid?: string;
  template_id?: string;
  name?: string;
  label?: string;
};
type UserTemplatesList = { ok?: boolean; templates?: UserTemplate[] };
type StatusSnapshot = {
  auth_health: unknown;
  selector_drift: unknown;
  daily_budget: unknown;
};

const tplId = (t: UserTemplate) => t.id ?? t.tid ?? t.template_id ?? t.name ?? "";
const tplLabel = (t: UserTemplate) => t.label || t.name || String(tplId(t));

type PluginDraft = {
  disabled: string[];
  allow_full_access: boolean;
  node_bin: string;
  granted_capabilities: string[];
};
type PluginsConfigResp = {
  ok?: boolean;
  config?: {
    enabled: string[] | null;
    disabled: string[];
    order: string[];
    allow_full_access: boolean;
    node_bin?: string;
    granted_capabilities?: string[];
  };
  discovered?: string[];
  schemas?: Record<string, PluginConfigField[]>;
  full_access_enabled?: boolean;
};

// v3.66.775: V3-A grant-UI. The gated-cap set and per-plugin load outcomes are
// DERIVED from /api/plugins/status (never hardcoded -- the backend's
// _GATED_CAPS is the single source of truth).
type PluginStatusEntry = {
  filename?: string;
  ok?: boolean;
  skipped_reason?: string;
  manifest?: { name?: string; capabilities?: string[] };
};
type PluginsStatusLite = {
  gated_capabilities?: string[];
  granted_capabilities?: string[];
  // v3.66.779: W6 operator-forced isolation list ("*" = every .py file).
  force_isolated?: string[];
  loaded?: PluginStatusEntry[];
};

// v3.66.509: GUI plugin install (POST /api/plugins/install multipart) + the
// managed-install registry (GET /api/plugins/installed).
type PluginInstalledRec = {
  file: string;
  name?: string;
  version?: string;
  source?: string;
  installed_at?: string;
};
type PluginsInstalledResp = {
  ok?: boolean;
  installed?: PluginInstalledRec[];
  risk_acknowledged?: boolean;
  disclaimer?: string;
};
type PluginInstallResult = {
  installed: boolean;
  name?: string;
  version?: string;
  file?: string;
  reason?: string;
  disclaimer?: string;
};

// INTEROP-GOV-1b (v3.66.639): interop provenance/risk-ack registry surface.
type InteropItem = {
  kind: string;
  item_id: string;
  source?: string;
  sha256?: string;
  commit?: string | null;
  risk_acknowledged: boolean;
  enabled: boolean;
};
type InteropRegistryResp = { ok: boolean; items: InteropItem[]; kinds: string[] };

type Pending =
  | { kind: "pluginsReload"; token: "" }
  | { kind: "pluginsConfigSave"; token: "" }
  | { kind: "pluginInstall"; token: "" }
  // Managed-plugin removal is destructive/irreversible -> Tier A (No-default
  // dialog, destructive Confirm, token shown as a caption -- NOT typed).
  | { kind: "pluginUninstall"; file: string; token: "REMOVE PLUGIN" }
  | { kind: "dedupCancel"; token: "" }
  | { kind: "authCheckAll"; token: "" }
  | { kind: "authCheckOne"; sid: string; token: "" }
  | { kind: "driftReset"; sid: string; token: string }
  | { kind: "budgetReset"; sid: string; token: string }
  | { kind: "historyPrune"; days: number; token: string }
  | { kind: "deleteTemplate"; id: string; label: string; token: string }
  // T4 (v3.66.207) operational controls — dangerous-selection class
  // (pause/resume all, crash delete) carries TYPED tokens; the rest are
  // one-step. Never one-click.
  | { kind: "pauseAll"; token: "" }
  | { kind: "resumeAll"; token: "" }
  | { kind: "crashDelete"; path: string; token: "DELETE PART" }
  | { kind: "crashIgnore"; path: string; token: "" }
  | { kind: "crashResume"; path: string; token: "" }
  | { kind: "setConcurrent"; sid: string; n: number; token: "" }
  | { kind: "fileReveal"; path: string; token: "" }
  // T5 (v3.66.208) governance — retention REAL apply deletes files →
  // TYPED token; dry-run apply + the remaining writes are one-step.
  // Reads (preview, audits, lists, bundle preview/download) are ungated.
  | { kind: "retentionApply"; token: "APPLY RETENTION" }
  | { kind: "retentionDryRun"; token: "" }
  | { kind: "rightsRemove"; bid: number; token: "" }
  | { kind: "schedAdd"; token: "" }
  | { kind: "schedRemove"; id: number; label: string; token: "" }
  | { kind: "schedRunNow"; token: "" }
  // INTEROP-GOV-1b (v3.66.639): governance writes route through the confirm
  // dialog (one-step, never one-click) like every other Maintenance write.
  | { kind: "interopRegister"; token: "" }
  | { kind: "interopAck"; ikind: string; itemId: string; token: "" }
  | { kind: "interopEnable"; ikind: string; itemId: string; enabled: boolean; token: "" };

const isTyped = (p: Pending): boolean => p.token.length > 0;

// Cut 3 — Maintenance is organized as an INTENT GRID (not the input->result
// spine): the operator picks by intent first. Each cell is data-intent tagged
// for testability/targeted styling; the detailed tool sections follow below.
const MAINTENANCE_INTENTS: { intent: string; blurb: string }[] = [
  { intent: "Inspect", blurb: "Status, per-site counters, diagnostics preview" },
  { intent: "Repair", blurb: "Crash recovery, orphan .part cleanup, selector resets" },
  { intent: "Maintenance", blurb: "Pause / resume, runner controls, limits & retries" },
  { intent: "Export", blurb: "Scheduled exports, diagnostics bundle, templates" },
  { intent: "Dangerous", blurb: "Retention apply, rights blocklist removals" },
];

function IntentGrid() {
  return (
    <div
      className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
      aria-label="Maintenance by intent"
    >
      {MAINTENANCE_INTENTS.map((it) => (
        <div
          key={it.intent}
          data-intent={it.intent}
          className="rounded-lg border border-border bg-black/20 p-3"
        >
          <div className="text-sm font-semibold text-ink-1">{it.intent}</div>
          <p className="mt-1 text-xs text-ink-3">{it.blurb}</p>
        </div>
      ))}
    </div>
  );
}

export function Maintenance() {
  const qc = useQueryClient();
  const [sid, setSid] = useState("");
  const [days, setDays] = useState("90");
  const [pending, setPending] = useState<Pending | null>(null);

  const status = useQuery<StatusSnapshot, Error>({
    queryKey: ["maintenance", "status"],
    queryFn: async () => {
      const grab = async (path: string): Promise<unknown> => {
        try {
          return await apiGet<unknown>(path);
        } catch {
          return null;
        }
      };
      const [auth_health, selector_drift, daily_budget] = await Promise.all([
        grab("/api/auth_health/status"),
        grab("/api/selector_drift/status"),
        grab("/api/daily_budget/status"),
      ]);
      return { auth_health, selector_drift, daily_budget };
    },
  });

  const templates = useQuery<UserTemplatesList, Error>({
    queryKey: ["user_templates"],
    queryFn: () => apiGet<UserTemplatesList>("/api/user_templates"),
  });

  const okToast = (msg: string) => (res: OkResult) =>
    res.ok === false ? toast.error(res.error || "failed") : toast.success(msg);

  const pluginsReload = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/plugins/reload", {}),
    onSuccess: okToast("Plugins reloaded"),
    onError: (e) => toast.error(e.message),
  });
  // v3.66.466: plugin-config GUI -- CLI->GUI parity for BD_PLUGINS_ENABLE /
  // BD_PLUGINS_ALLOW_FULL_ACCESS. Operator controls which plugins load + the
  // full-access gate from here; saved to plugins.json and applied on save.
  const pluginsCfg = useQuery<PluginsConfigResp, Error>({
    queryKey: ["plugins", "config"],
    queryFn: () => apiGet<PluginsConfigResp>("/api/plugins/config"),
  });
  // v3.66.775: read-only status feed for the grant panel (gated-cap set +
  // per-plugin declared caps / skip reasons). Same endpoint PluginMetrics uses.
  const pluginsStatus = useQuery<PluginsStatusLite, Error>({
    queryKey: ["plugins", "status", "grants"],
    queryFn: () => apiGet<PluginsStatusLite>("/api/plugins/status"),
  });
  const [pluginDraft, setPluginDraft] = useState<PluginDraft | null>(null);
  const [schemaValues, setSchemaValues] = useState<Record<string, PluginConfigValues>>({});
  const draft: PluginDraft = pluginDraft ?? {
    disabled: pluginsCfg.data?.config?.disabled ?? [],
    allow_full_access: pluginsCfg.data?.config?.allow_full_access ?? false,
    node_bin: pluginsCfg.data?.config?.node_bin ?? "",
    granted_capabilities: pluginsCfg.data?.config?.granted_capabilities ?? [],
  };
  const pluginsConfigSave = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>("/api/plugins/config", {
        disabled: draft.disabled,
        allow_full_access: draft.allow_full_access,
        node_bin: draft.node_bin,
        granted_capabilities: draft.granted_capabilities,
      }),
    onSuccess: (res) => {
      okToast("Plugin settings saved")(res);
      setPluginDraft(null);
      qc.invalidateQueries({ queryKey: ["plugins", "config"] });
      qc.invalidateQueries({ queryKey: ["plugins", "status", "grants"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const pluginStatusByFile: Record<string, PluginStatusEntry> = {};
  for (const e of pluginsStatus.data?.loaded ?? []) {
    if (e.filename) pluginStatusByFile[e.filename] = e;
  }
  const gatedCaps = pluginsStatus.data?.gated_capabilities ?? [];
  // v3.66.779: operator-forced isolation, surfaced per plugin. "*" forces all.
  const forceIsolated = pluginsStatus.data?.force_isolated ?? [];

  // v3.66.509: GUI plugin install. Upload a plugin file through the managed
  // install path (ast-read manifest, api-range gate, at-your-own-risk ack,
  // atomic stage). Install does NOT load it — operator hits Reload after.
  const [installFile, setInstallFile] = useState<File | null>(null);
  const [installAck, setInstallAck] = useState(false);
  const [installPersist, setInstallPersist] = useState(false);
  const pluginsInstalled = useQuery<PluginsInstalledResp, Error>({
    queryKey: ["plugins", "installed"],
    queryFn: () => apiGet<PluginsInstalledResp>("/api/plugins/installed"),
  });
  const pluginInstall = useMutation<PluginInstallResult, Error, void>({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", installFile as File);
      fd.append("ack", installAck ? "1" : "0");
      fd.append("persist_ack", installPersist ? "1" : "0");
      return apiPostForm<PluginInstallResult>("/api/plugins/install", fd);
    },
    onSuccess: (res) => {
      if (res.installed) {
        toast.success(`Installed ${res.name ?? res.file ?? "plugin"}${res.version ? ` v${res.version}` : ""}. Reload plugins to load it.`);
        setInstallFile(null);
        setInstallAck(false);
        qc.invalidateQueries({ queryKey: ["plugins", "installed"] });
        qc.invalidateQueries({ queryKey: ["plugins", "config"] });
      } else {
        toast.error(res.reason || "Install refused");
      }
    },
    onError: (e) => toast.error(e.message),
  });
  const pluginUninstall = useMutation<
    { uninstalled: boolean; file?: string; reason?: string },
    Error,
    string
  >({
    mutationFn: (file) =>
      apiPost<{ uninstalled: boolean; file?: string; reason?: string }>(
        "/api/plugins/uninstall",
        { file, ack: true },
      ),
    onSuccess: (res) => {
      if (res.uninstalled) {
        toast.success(`Removed ${res.file ?? "plugin"}. Reload plugins to drop it.`);
        qc.invalidateQueries({ queryKey: ["plugins", "installed"] });
        qc.invalidateQueries({ queryKey: ["plugins", "config"] });
      } else {
        toast.error(res.reason || "The plugin was not removed.");
      }
    },
    onError: (e) => toast.error(e.message),
  });
  // ── INTEROP-GOV-1b: interop provenance / risk-ack registry ──────────
  const interopReg = useQuery<InteropRegistryResp, Error>({
    queryKey: ["interop", "registry"],
    queryFn: () => apiGet<InteropRegistryResp>("/api/interop/registry"),
  });
  const [interopKind, setInteropKind] = useState("chromium_extension");
  const [interopItemId, setInteropItemId] = useState("");
  const [interopSource, setInteropSource] = useState("");
  const refreshInterop = () =>
    qc.invalidateQueries({ queryKey: ["interop", "registry"] });
  const interopRegister = useMutation<OkResult, Error, void>({
    mutationFn: () =>
      apiPost<OkResult>("/api/interop/register", {
        kind: interopKind,
        item_id: interopItemId,
        source: interopSource || undefined,
      }),
    onSuccess: () => {
      toast.success("Registered (off by default — acknowledge + enable to permit)");
      setInteropItemId("");
      refreshInterop();
    },
    onError: (e) => toast.error(e.message),
  });
  const interopAck = useMutation<OkResult, Error, { kind: string; item_id: string }>({
    mutationFn: (v) => apiPost<OkResult>("/api/interop/acknowledge", v),
    onSuccess: () => {
      toast.success("Risk acknowledged");
      refreshInterop();
    },
    onError: (e) => toast.error(e.message),
  });
  const interopEnable = useMutation<
    OkResult,
    Error,
    { kind: string; item_id: string; enabled: boolean }
  >({
    mutationFn: (v) => apiPost<OkResult>("/api/interop/enable", v),
    onSuccess: () => refreshInterop(),
    onError: (e) => toast.error(e.message),
  });

  const dedupCancel = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/dedup/scan/cancel", {}),
    onSuccess: okToast("Dedup scan cancelled"),
    onError: (e) => toast.error(e.message),
  });
  const authCheckAll = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/auth_health/check_all", {}),
    onSuccess: (res) => {
      okToast("Auth-health check started")(res);
      qc.invalidateQueries({ queryKey: ["maintenance", "status"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const authCheckOne = useMutation<OkResult, Error, string>({
    mutationFn: (s) => apiPost<OkResult>(`/api/auth_health/check/${encodeURIComponent(s)}`, {}),
    onSuccess: (res, s) => {
      okToast(`Checked ${s}`)(res);
      qc.invalidateQueries({ queryKey: ["maintenance", "status"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const driftReset = useMutation<OkResult, Error, string>({
    mutationFn: (s) => apiPost<OkResult>(`/api/selector_drift/reset/${encodeURIComponent(s)}`, {}),
    onSuccess: (res, s) => {
      okToast(`Selector drift reset for ${s}`)(res);
      qc.invalidateQueries({ queryKey: ["maintenance", "status"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const budgetReset = useMutation<OkResult, Error, string>({
    mutationFn: (s) => apiPost<OkResult>(`/api/daily_budget/reset/${encodeURIComponent(s)}`, {}),
    onSuccess: (res, s) => {
      okToast(`Daily budget reset for ${s}`)(res);
      qc.invalidateQueries({ queryKey: ["maintenance", "status"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const historyPrune = useMutation<OkResult & { deleted?: number }, Error, number>({
    mutationFn: (d) => apiPost<OkResult & { deleted?: number }>("/api/history/prune", { days: d }),
    onSuccess: (res) =>
      res.ok === false
        ? toast.error(res.error || "prune failed")
        : toast.success(`History pruned${res.deleted != null ? ` (${res.deleted} deleted)` : ""}`),
    onError: (e) => toast.error(e.message),
  });
  const deleteTemplate = useMutation<OkResult, Error, string>({
    mutationFn: (id) => apiDelete<OkResult>(`/api/user_templates/${encodeURIComponent(id)}`),
    onSuccess: (res, id) => {
      if (res.ok === false) toast.error(res.error || "delete failed");
      else {
        toast.success(`Deleted ${id}`);
        qc.invalidateQueries({ queryKey: ["user_templates"] });
      }
    },
    onError: (e) => toast.error(e.message),
  });

  // ── T4 (v3.66.207): operational controls ──────────────────────────
  const pauseAll = usePauseAll();
  const resumeAll = useResumeAll();
  const setConcurrent = useSetConcurrent();
  const rateLimit = useRateLimitStatus();
  const retryPolicy = useRetryPolicy();
  const crashScan = useCrashScan();
  const crashAction = useCrashAction();
  const fileReveal = useFileReveal();
  const [concN, setConcN] = useState("2");
  const [revealPath, setRevealPath] = useState("");

  // ── T5 (v3.66.208) governance state + hooks ─────────────────────────
  const [rtSite, setRtSite] = useState("");
  const [rtPreviewSite, setRtPreviewSite] = useState<string | null>(null);
  const [rtDry, setRtDry] = useState(true);
  const [rtAuditOpen, setRtAuditOpen] = useState(false);
  const [seLabel, setSeLabel] = useState("");
  const [seFormat, setSeFormat] = useState("csv");
  const [seDest, setSeDest] = useState("");
  const [seCadence, setSeCadence] = useState("24");
  const [diagOpen, setDiagOpen] = useState(false);
  const rtPreview = useRetentionPreview(rtPreviewSite);
  const rtAudit = useRetentionAudit(50);
  const rtApply = useRetentionApply();
  const rightsList = useRightsBlocklist();
  const rightsAudit = useRightsAudit(100);
  const rightsRemove = useRightsRemove();
  const schedList = useSchedExports();
  const schedAdd = useSchedExportAdd();
  const schedRemove = useSchedExportRemove();
  const schedRunNow = useSchedExportRunNow();
  const diagPreview = useDiagBundlePreview(diagOpen);

  const busy =
    pluginsReload.isPending ||
    pluginsConfigSave.isPending ||
    pluginInstall.isPending ||
    pluginUninstall.isPending ||
    dedupCancel.isPending ||
    authCheckAll.isPending ||
    authCheckOne.isPending ||
    driftReset.isPending ||
    budgetReset.isPending ||
    historyPrune.isPending ||
    deleteTemplate.isPending ||
    pauseAll.isPending ||
    resumeAll.isPending ||
    setConcurrent.isPending ||
    crashAction.isPending ||
    fileReveal.isPending ||
    rtApply.isPending ||
    interopRegister.isPending ||
    interopAck.isPending ||
    interopEnable.isPending ||
    rightsRemove.isPending ||
    schedAdd.isPending ||
    schedRemove.isPending ||
    schedRunNow.isPending;

  const arm = (p: Pending) => {
    setPending(p);
  };
  const requireSite = (): string | null => {
    const s = sid.trim();
    if (!s) {
      toast.error("Enter a site id first");
      return null;
    }
    return s;
  };

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "pluginsReload":
        pluginsReload.mutate();
        break;
      case "pluginsConfigSave":
        pluginsConfigSave.mutate();
        break;
      case "pluginInstall":
        pluginInstall.mutate();
        break;
      case "pluginUninstall":
        pluginUninstall.mutate(pending.file);
        break;
      case "dedupCancel":
        dedupCancel.mutate();
        break;
      case "authCheckAll":
        authCheckAll.mutate();
        break;
      case "authCheckOne":
        authCheckOne.mutate(pending.sid);
        break;
      case "driftReset":
        driftReset.mutate(pending.sid);
        break;
      case "budgetReset":
        budgetReset.mutate(pending.sid);
        break;
      case "historyPrune":
        historyPrune.mutate(pending.days);
        break;
      case "deleteTemplate":
        deleteTemplate.mutate(pending.id);
        break;
      case "pauseAll":
        pauseAll.mutate(undefined, {
          onSuccess: (r) => toast.success(`Paused ${r.paused ?? 0} runners`),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "resumeAll":
        resumeAll.mutate(undefined, {
          onSuccess: (r) => toast.success(`Resumed ${r.resumed ?? 0} runners`),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "crashDelete":
        crashAction.mutate(
          { action: "delete", path: pending.path },
          {
            onSuccess: (r) =>
              r.ok !== false ? toast.success("Orphan deleted") : toast.error(r.error || "delete failed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "crashIgnore":
        crashAction.mutate(
          { action: "ignore", path: pending.path },
          {
            onSuccess: (r) =>
              r.ok !== false ? toast.success("Marked ignored") : toast.error(r.error || "ignore failed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "crashResume":
        crashAction.mutate(
          { action: "resume", path: pending.path },
          {
            onSuccess: (r) =>
              r.ok !== false ? toast.success("Re-enqueued") : toast.error(r.error || "resume failed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "setConcurrent":
        setConcurrent.mutate(
          { sid: pending.sid, n: pending.n },
          {
            onSuccess: (r) =>
              r.ok !== false
                ? toast.success(`max_concurrent = ${r.max_concurrent ?? pending.n}`)
                : toast.error(r.error || "set failed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "fileReveal":
        fileReveal.mutate(
          { path: pending.path },
          {
            onSuccess: (r) =>
              r.ok !== false ? toast.success("Revealed on host") : toast.error(r.error || "reveal failed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      // ── T5 (v3.66.208) governance · F4.2 (227) preview-verbatim ───
      case "retentionApply": {
        // Real delete is BOUND to the previewed site's candidate ids, so
        // the server can only delete what the operator just previewed.
        const previewIds = (rtPreview.data?.candidates ?? [])
          .map((c) => c.id)
          .filter((x): x is number => typeof x === "number");
        rtApply.mutate(
          {
            dryRun: false,
            confirmIds: previewIds,
            siteId: rtPreviewSite ?? undefined,
          },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "retention apply failed")
                : toast.success(
                    `Retention applied — ${r.total_deleted ?? 0} file(s) ` +
                      "deleted (bound to preview)",
                  ),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      }
      case "retentionDryRun": {
        const hasPreview = !!(rtPreviewSite && rtPreview.data);
        const previewIds = (rtPreview.data?.candidates ?? [])
          .map((c) => c.id)
          .filter((x): x is number => typeof x === "number");
        rtApply.mutate(
          hasPreview
            ? {
                dryRun: true,
                confirmIds: previewIds,
                siteId: rtPreviewSite ?? undefined,
              }
            : { dryRun: true },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "dry-run failed")
                : toast.success("Retention dry-run complete — see audit (DRY rows)"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      }
      case "rightsRemove":
        rightsRemove.mutate(
          { bid: pending.bid },
          {
            onSuccess: (r) =>
              r.ok === false ? toast.error(r.error || "remove failed") : toast.success("Blocklist entry removed"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "schedAdd": {
        const cadence = parseInt(seCadence, 10);
        schedAdd.mutate(
          {
            label: seLabel.trim(),
            format: seFormat,
            destination: seDest.trim(),
            cadence_hours: cadence > 0 ? cadence : 24,
          },
          {
            onSuccess: (r) => {
              if (r.ok === false) toast.error(r.error || "add failed");
              else {
                toast.success(`Scheduled export #${r.id ?? "?"} added`);
                setSeLabel("");
                setSeDest("");
              }
            },
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      }
      case "schedRemove":
        schedRemove.mutate(
          { id: pending.id },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "remove failed")
                : toast.success(`Removed schedule ${pending.label}`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "schedRunNow":
        schedRunNow.mutate(undefined, {
          onSuccess: (r) =>
            r.ok === false ? toast.error(r.error || "run failed") : toast.success("Due exports run"),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "interopRegister":
        interopRegister.mutate();
        break;
      case "interopAck":
        interopAck.mutate({ kind: pending.ikind, item_id: pending.itemId });
        break;
      case "interopEnable":
        interopEnable.mutate({
          kind: pending.ikind,
          item_id: pending.itemId,
          enabled: pending.enabled,
        });
        break;
    }
    setPending(null);
  };

  const tpls = templates.data?.templates ?? [];

  return (
    <AppShell title="Maintenance · Diagnostics" subtitle="Operational actions · per-site resets · history prune">
      <GatedWriteBanner>
        Destructive actions require an explicit yes/no confirmation (No is the default) — nothing fires on a single
        click; every request is audited by the underlying endpoint. <b>Needs operator click-through validation.</b>
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mt-3">
        Operational upkeep and diagnostics: pause or resume work, reset per-site
        selector drift and daily budgets, prune old history, clean up orphaned
        files, and manage retention. Previews are non-destructive; destructive
        actions confirm with No as the default before they run.
      </Callout>

      <IntentGrid />

      <Card className="mt-4 p-4">
        <h2 className="section-head">Operational</h2>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy} onClick={() => arm({ kind: "pluginsReload", token: "" })}>
            Reload plugins
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => arm({ kind: "dedupCancel", token: "" })}>
            Cancel dedup scan
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => arm({ kind: "authCheckAll", token: "" })}>
            Auth-health check (all)
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Install a plugin</h2>
        <p className="text-sm text-muted-foreground mb-3">
          Upload a plugin file (<code>.py</code> / <code>.js</code> / <code>.mjs</code>). The file is
          staged through the managed install path — its manifest is read without executing it, the
          API version range is checked, and it is recorded in the registry. Installing does{" "}
          <b>not</b> load the plugin: enable it below and hit <b>Reload plugins</b> afterward. This is
          the GUI equivalent of <code>tools/plugin_install.py install</code>.
        </p>

        <Callout tone="danger" title="Plugins run with no sandbox — install only what you trust" className="mb-3">
          A loaded plugin runs inside BulkDownloader with the same access BD has: the filesystem, the
          network, your configuration and secrets, your authenticated session profiles, and the live
          browser context. There is no privilege boundary. A hostile plugin can exfiltrate your
          secrets and cookies or get your accounts banned. Install only plugins you wrote or fully
          trust, and ensure each complies with the sites' Terms of Service, applicable law, and the
          capture charter (no access-control bypass, DRM circumvention, or challenge-solving).
        </Callout>

        <div className="flex flex-col gap-3">
          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="plugin-install-file">
              Plugin file
            </label>
            <input
              id="plugin-install-file"
              type="file"
              accept=".py,.js,.mjs"
              aria-label="plugin file"
              disabled={busy}
              className="block text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-accent"
              onChange={(e) => {
                setInstallFile(e.target.files?.[0] ?? null);
                pluginInstall.reset();
              }}
            />
            {installFile && (
              <p className="text-xs text-muted-foreground mt-1">
                Selected: <span className="font-mono">{installFile.name}</span>{" "}
                ({Math.max(1, Math.round(installFile.size / 1024))} KB)
              </p>
            )}
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={installAck}
              disabled={busy}
              onChange={(e) => setInstallAck(e.target.checked)}
            />
            <span>
              I understand this plugin runs with full, un-sandboxed access and I accept responsibility
              for what it does. <span className="text-muted-foreground">(required)</span>
            </span>
          </label>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={installPersist}
              disabled={busy}
              onChange={(e) => setInstallPersist(e.target.checked)}
            />
            <span>Remember this acknowledgment for future installs</span>
          </label>

          <div>
            <Button
              disabled={busy || !installFile || !installAck}
              onClick={() => arm({ kind: "pluginInstall", token: "" })}
              title={!installFile ? "Choose a file first" : !installAck ? "Acknowledge the risk to enable" : "Install"}
            >
              {pluginInstall.isPending ? "Installing…" : "Install plugin"}
            </Button>
          </div>

          {pluginInstall.data && !pluginInstall.data.installed && (
            <Callout tone="caution" title="Install refused" className="mt-1">
              {pluginInstall.data.reason || "The plugin was not installed."}
            </Callout>
          )}
        </div>

        <div className="text-sm font-medium mt-4 mb-1">Managed installs</div>
        {(pluginsInstalled.data?.installed ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing installed through the managed path yet. (Hand-dropped files in the plugin
            directory appear under Discovered plugins below, not here.)
          </p>
        ) : (
          <div className="flex flex-col gap-1">
            {(pluginsInstalled.data?.installed ?? []).map((rec) => (
              <div key={rec.file} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-mono">{rec.file}</span>
                {rec.version ? <span className="text-muted-foreground">v{rec.version}</span> : null}
                {rec.installed_at ? (
                  <span className="text-xs text-muted-foreground">· {rec.installed_at}</span>
                ) : null}
                <Button
                  variant="destructive"
                  size="sm"
                  className="ml-auto"
                  disabled={busy}
                  onClick={() =>
                    arm({ kind: "pluginUninstall", file: rec.file, token: "REMOVE PLUGIN" })
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Plugin settings</h2>
        <p className="text-sm text-muted-foreground mb-3">
          Control which plugins load and whether full-access (live-browser) plugins are permitted.
          Saved to <code>plugins.json</code> and applied immediately. Equivalent to the{" "}
          <code>BD_PLUGINS_ENABLE</code> / <code>BD_PLUGINS_ALLOW_FULL_ACCESS</code> env overrides.
        </p>
        <label className="flex items-center gap-2 mb-2">
          <input
            type="checkbox"
            checked={draft.allow_full_access}
            disabled={busy}
            onChange={(e) => setPluginDraft({ ...draft, allow_full_access: e.target.checked })}
          />
          <span>Allow full-access (lifecycle / live-browser) plugins</span>
        </label>
        {gatedCaps.length > 0 ? (
          <div className="mb-3">
            <div className="text-sm font-medium mb-1">Per-capability grants</div>
            <p className="text-xs text-muted-foreground mb-1">
              Grant a specific gated capability without opening full access. Ungranted gated
              capabilities stay denied (deny-by-default). Saved to{" "}
              <code>granted_capabilities</code> in <code>plugins.json</code>.
            </p>
            <div className="flex flex-col gap-1">
              {gatedCaps.map((cap) => (
                <label key={cap} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`grant-${cap}`}
                    checked={draft.granted_capabilities.includes(cap)}
                    disabled={busy}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...draft.granted_capabilities, cap]
                        : draft.granted_capabilities.filter((c) => c !== cap);
                      setPluginDraft({ ...draft, granted_capabilities: next });
                    }}
                  />
                  <span className="font-mono text-sm">{cap}</span>
                </label>
              ))}
            </div>
            {draft.allow_full_access ? (
              <p className="text-xs text-muted-foreground mt-1">
                Full access is ON, which already covers every gated capability; these grants
                apply when full access is off.
              </p>
            ) : null}
          </div>
        ) : null}
        <div className="mb-3">
          <label className="block text-sm font-medium mb-1" htmlFor="plugin-node-bin">
            Node runtime path
          </label>
          <input
            id="plugin-node-bin"
            type="text"
            aria-label="node_bin"
            className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
            placeholder="node"
            value={draft.node_bin}
            disabled={busy}
            onChange={(e) => setPluginDraft({ ...draft, node_bin: e.target.value })}
          />
          <p className="text-xs text-muted-foreground mt-1">
            Interpreter used to run <code>.js</code>/<code>.mjs</code> plugins. Blank uses{" "}
            <code>node</code> from PATH. <code>BD_PLUGINS_NODE_BIN</code> overrides this when set.
          </p>
        </div>
        {draft.allow_full_access && (
          <Callout tone="danger" title="Full-access plugins run with no sandbox" className="mb-3">
            A full-access plugin runs inside BulkDownloader with the same privilege BD has: the
            filesystem, the network, your configuration and secrets, your authenticated session
            profiles, and the live browser context. Load only plugins you wrote or fully trust. You
            are responsible for each plugin's compliance with the sites' Terms of Service, applicable
            law, and the capture charter (no access-control bypass, no DRM circumvention, no
            challenge-solving).
          </Callout>
        )}
        <div className="text-sm font-medium mb-1">Discovered plugins</div>
        {(pluginsCfg.data?.discovered ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">None found in the plugin directory.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {(pluginsCfg.data?.discovered ?? []).map((name) => {
              const on = !draft.disabled.includes(name);
              const schema = pluginsCfg.data?.schemas?.[name];
              return (
                <div key={name} className="flex flex-col gap-1">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={busy}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? draft.disabled.filter((d) => d !== name)
                          : [...draft.disabled, name];
                        setPluginDraft({ ...draft, disabled: next });
                      }}
                    />
                    <span className="font-mono text-sm">{name}</span>
                    {(pluginStatusByFile[name]?.manifest?.capabilities ?? []).length > 0 ? (
                      <span
                        data-testid={`plugin-caps-${name}`}
                        className="flex flex-wrap gap-1"
                      >
                        {(pluginStatusByFile[name]?.manifest?.capabilities ?? []).map(
                          (cap) => (
                            <span
                              key={cap}
                              className={
                                gatedCaps.includes(cap)
                                  ? "rounded border border-destructive px-1 text-xs font-mono text-destructive"
                                  : "rounded border px-1 text-xs font-mono text-muted-foreground"
                              }
                              title={gatedCaps.includes(cap) ? "gated capability" : undefined}
                            >
                              {cap}
                            </span>
                          ),
                        )}
                      </span>
                    ) : null}
                    {forceIsolated.includes("*") || forceIsolated.includes(name) ? (
                      <span
                        data-testid={`plugin-force-isolated-${name}`}
                        className="rounded border border-amber bg-amber-soft px-1 text-xs font-mono text-amber-dim"
                        title={
                          "operator-forced isolation (force_isolated): this plugin " +
                          "never runs in-process -- bridge-contract .py go to a " +
                          "subprocess, others are skipped"
                        }
                      >
                        forced-isolated
                      </span>
                    ) : null}
                  </label>
                  {pluginStatusByFile[name]?.skipped_reason ? (
                    <p className="ml-6 text-xs text-destructive">
                      {pluginStatusByFile[name]?.skipped_reason}
                    </p>
                  ) : null}
                  {schema && schema.length > 0 ? (
                    <div className="ml-6 rounded border p-2">
                      <PluginConfigForm
                        fields={schema}
                        values={schemaValues[name] ?? {}}
                        onChange={(k, v) =>
                          setSchemaValues((prev) => ({
                            ...prev,
                            [name]: { ...(prev[name] ?? {}), [k]: v },
                          }))
                        }
                      />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
        <div className="mt-3">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => arm({ kind: "pluginsConfigSave", token: "" })}
          >
            Save plugin settings
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Interop governance</h2>
        <p className="mb-2 text-sm text-ink-3">
          Register an interop item (a Chrome-extension dir, or a JD / yt-dlp /
          gallery-dl plugin), record its provenance, then acknowledge its risk and
          enable it. This is records + consent, <span className="font-semibold">not</span>{" "}
          an allowlist &mdash; BD ships nothing. With governance enabled, an extension
          loads only once it is registered, risk-acknowledged, and enabled here (and
          its content still matches the pinned hash).
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <select
            value={interopKind}
            onChange={(e) => setInteropKind(e.target.value)}
            className="rounded border bg-transparent px-2 py-1 text-sm"
            aria-label="Interop kind"
          >
            {(interopReg.data?.kinds ?? ["chromium_extension"]).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <Input
            placeholder="item id (extension dir / repo@commit)"
            value={interopItemId}
            onChange={(e) => setInteropItemId(e.target.value)}
            className="min-w-[240px] flex-1"
          />
          <Input
            placeholder="source (optional)"
            value={interopSource}
            onChange={(e) => setInteropSource(e.target.value)}
            className="max-w-xs"
          />
          <Button
            variant="outline"
            disabled={!interopItemId.trim() || busy}
            onClick={() => arm({ kind: "interopRegister", token: "" })}
          >
            Register
          </Button>
        </div>
        <div className="mt-3 space-y-1">
          {(interopReg.data?.items ?? []).length === 0 ? (
            <p className="text-sm text-ink-3">No interop items registered.</p>
          ) : (
            (interopReg.data?.items ?? []).map((it) => (
              <div
                key={`${it.kind}:${it.item_id}`}
                className="flex flex-wrap items-center gap-2 text-sm"
              >
                <span className="font-mono text-ink-3">{it.kind}</span>
                <span className="flex-1 truncate font-mono" title={it.item_id}>
                  {it.item_id}
                </span>
                <span className={it.risk_acknowledged ? "text-emerald-400" : "text-amber-400"}>
                  {it.risk_acknowledged ? "acked" : "not acked"}
                </span>
                <span className={it.enabled ? "text-emerald-400" : "text-ink-3"}>
                  {it.enabled ? "enabled" : "disabled"}
                </span>
                {!it.risk_acknowledged && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      arm({ kind: "interopAck", ikind: it.kind, itemId: it.item_id, token: "" })
                    }
                  >
                    Acknowledge risk
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    arm({
                      kind: "interopEnable",
                      ikind: it.kind,
                      itemId: it.item_id,
                      enabled: !it.enabled,
                      token: "",
                    })
                  }
                >
                  {it.enabled ? "Disable" : "Enable"}
                </Button>
              </div>
            ))
          )}
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Runners · all sites</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => arm({ kind: "pauseAll", token: "" })}
            title="Stops dequeueing on every site; in-flight jobs finish"
          >
            Pause all
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => arm({ kind: "resumeAll", token: "" })}
          >
            Resume all
          </Button>
          <span className="ml-2 text-sm text-ink-3">per-site concurrency:</span>
          <Input
            className="max-w-[80px]"
            value={concN}
            onChange={(e) => setConcN(e.target.value)}
            placeholder="n"
          />
          <Button
            variant="outline"
            disabled={busy || !/^\d+$/.test(concN.trim())}
            onClick={() => {
              const s = requireSite();
              if (s) arm({ kind: "setConcurrent", sid: s, n: Number(concN.trim()), token: "" });
            }}
            title="Uses the site id from the Per-site box below (1–20)"
          >
            Set concurrency
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Crash recovery · orphan .part files</h2>
        {crashScan.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : !crashScan.data?.orphans?.length ? (
          <p className="text-sm text-ink-3">No orphan .part files.</p>
        ) : (
          <ul className="divide-y divide-border">
            {crashScan.data.orphans.map((o, i) => (
              <li key={o.path ?? i} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{o.path}</span>
                <div className="flex shrink-0 gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || !o.path}
                    onClick={() => arm({ kind: "crashResume", path: o.path as string, token: "" })}
                  >
                    Resume
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || !o.path}
                    onClick={() => arm({ kind: "crashIgnore", path: o.path as string, token: "" })}
                  >
                    Ignore
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy || !o.path}
                    onClick={() => arm({ kind: "crashDelete", path: o.path as string, token: "DELETE PART" })}
                  >
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Limits · retries · files</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <h3 className="text-xs font-semibold uppercase text-ink-3">Rate limiter</h3>
            {rateLimit.data ? (
              <p className="mt-1 text-sm text-ink-3">
                {Object.keys(rateLimit.data.domains ?? {}).length} active domains ·{" "}
                {rateLimit.data.global ? "global caps loaded" : "no global caps"}
              </p>
            ) : (
              <p className="mt-1 text-sm text-ink-3">loading…</p>
            )}
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase text-ink-3">Retry policy</h3>
            {retryPolicy.data?.classes ? (
              <ul className="mt-1 text-sm text-ink-3">
                {Object.entries(retryPolicy.data.classes).map(([cls, c]) => (
                  <li key={cls}>
                    {cls}: {(c.delays_seconds ?? []).length} attempts ·{" "}
                    {Math.round(c.total_window_seconds ?? 0)}s window
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-ink-3">loading…</p>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Input
            className="max-w-md"
            placeholder="Path to reveal in the host file manager"
            value={revealPath}
            onChange={(e) => setRevealPath(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={busy || !revealPath.trim()}
            onClick={() => arm({ kind: "fileReveal", path: revealPath.trim(), token: "" })}
          >
            Reveal file
          </Button>
        </div>
      </Card>

      {/* ── T5 (v3.66.208) — Retention · preview-first, verbatim from the
            legacy openRetention modal: per-site dry-run preview, apply with
            dry-run default ON, REAL apply destructive-confirm-gated, audit log. ── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Retention · preview first</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="site id to preview"
            value={rtSite}
            onChange={(e) => setRtSite(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={!rtSite.trim() || rtPreview.isFetching}
            onClick={() => setRtPreviewSite(rtSite.trim())}
          >
            Preview
          </Button>
        </div>
        {rtPreviewSite && (
          <div className="mt-3 rounded bg-black/30 p-3 text-sm">
            {rtPreview.isLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : rtPreview.isError ? (
              <p className="text-red-300">Preview failed: {rtPreview.error.message}</p>
            ) : rtPreview.data ? (
              <>
                <p className="text-ink-3">
                  <b className="text-foreground">{rtPreview.data.candidate_count ?? 0}</b> candidates · would
                  free <b className="text-foreground">{formatBytes(rtPreview.data.total_bytes ?? 0)}</b> ·
                  policy {rtPreview.data.retention_days ?? 0}d age cap, {rtPreview.data.retention_max_gb ?? 0}GB
                  size cap · protected tags:{" "}
                  {(rtPreview.data.retention_keep_tagged_with ?? []).join(", ") || "none"}
                </p>
                {(rtPreview.data.candidates ?? []).slice(0, 5).map((c, i) => (
                  <p key={i} className="mt-1 truncate font-mono text-xs text-ink-3">
                    • {c.filename ?? c.file_path} ({c.reason})
                  </p>
                ))}
              </>
            ) : null}
          </div>
        )}
        <DangerZone
          title="Retention"
          warning="Apply deletes the previewed files for this site and cannot be undone. Dry-run first; the real apply confirms (No is the default) before it runs."
          className="mt-3"
        >
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-3">
            <input type="checkbox" checked={rtDry} onChange={(e) => setRtDry(e.target.checked)} />
            dry-run (no files deleted)
          </label>
          <Button
            variant={rtDry ? "outline" : "destructive"}
            disabled={busy || (!rtDry && !(rtPreviewSite && rtPreview.data))}
            onClick={() =>
              rtDry
                ? arm({ kind: "retentionDryRun", token: "" })
                : arm({ kind: "retentionApply", token: "APPLY RETENTION" })
            }
            title={
              rtDry
                ? "Dry-run: simulates only. Binds to the loaded preview when present, else previews all sites."
                : "Deletes ONLY the files in the loaded preview for this site (preview-bound). Load a preview first."
            }
          >
            {rtDry ? "Apply (dry-run)" : "Apply retention"}
          </Button>
          {!rtDry && !(rtPreviewSite && rtPreview.data) && (
            <span className="text-xs text-amber-300">
              Load a preview to enable a preview-bound delete.
            </span>
          )}
          <Button variant="ghost" onClick={() => setRtAuditOpen((v) => !v)}>
            {rtAuditOpen ? "Hide audit" : "View recent deletions"}
          </Button>
        </div>
        </DangerZone>
        {rtAuditOpen && (
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-ink-3">
            {rtAudit.isLoading
              ? "Loading…"
              : (rtAudit.data?.audit ?? []).length === 0
                ? "No retention events yet."
                : (rtAudit.data?.audit ?? [])
                    .map(
                      (a) =>
                        `${a.deleted_at ? formatTimestamp(a.deleted_at) : "?"} ` +
                        `${a.dry_run ? "[DRY]" : "[REAL]"} ${a.site_id ?? "?"}/` +
                        `${(a.file_path ?? "").split(/[\\/]/).pop()} (${a.reason ?? ""})`,
                    )
                    .join("\n")}
          </pre>
        )}
      </Card>

      {/* ── T5 — Rights / blocklist ── */}
      <DangerZone
        title="Rights · blocklist"
        warning="Removing a blocklist entry unblocks that content. Each removal confirms before it runs."
        className="mt-4"
      >
        {rightsList.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (rightsList.data?.blocks ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">No blocklist entries.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <tbody>
              {(rightsList.data?.blocks ?? []).map((b) => (
                <tr key={b.id} className="border-b border-border/40">
                  <td className="py-1.5 pr-3 font-mono text-xs">{b.kind ?? "?"}</td>
                  <td className="min-w-0 max-w-[280px] truncate py-1.5 pr-3 font-mono text-xs">
                    {b.pattern || b.hash_hex || "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-ink-3">{b.reason || ""}</td>
                  <td className="py-1.5 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || b.id == null}
                      onClick={() => arm({ kind: "rightsRemove", bid: b.id as number, token: "" })}
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-3 border-t border-border pt-3" aria-label="Recent rights audit">
          <h3 className="text-xs font-semibold uppercase text-ink-3">Recent rights audit</h3>
          {rightsAudit.isLoading ? (
            <Skeleton className="mt-2 h-12 w-full" />
          ) : (rightsAudit.data?.entries ?? []).length === 0 ? (
            <p className="mt-1 text-sm text-ink-3">No rights events yet.</p>
          ) : (
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-ink-3">
              {JSON.stringify((rightsAudit.data?.entries ?? []).slice(0, 20), null, 2)}
            </pre>
          )}
        </div>
      </DangerZone>

      {/* ── T5 — Scheduled exports ── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Scheduled exports</h2>
        {schedList.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : (schedList.data?.schedules ?? []).length === 0 ? (
          <p className="text-sm text-ink-3">No scheduled exports.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <tbody>
              {(schedList.data?.schedules ?? []).map((s) => (
                <tr key={s.id} className="border-b border-border/40">
                  <td className="py-1.5 pr-3">{s.label || `#${s.id}`}</td>
                  <td className="py-1.5 pr-3 font-mono text-xs">{s.format}</td>
                  <td className="min-w-0 max-w-[220px] truncate py-1.5 pr-3 font-mono text-xs">
                    {s.destination}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-ink-3">
                    every {s.cadence_hours ?? "?"}h · {s.last_status ?? "never run"}
                  </td>
                  <td className="py-1.5 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || s.id == null}
                      onClick={() =>
                        arm({
                          kind: "schedRemove",
                          id: s.id as number,
                          label: s.label || `#${s.id}`,
                          token: "",
                        })
                      }
                    >
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Input
            className="max-w-[160px]"
            placeholder="label"
            value={seLabel}
            onChange={(e) => setSeLabel(e.target.value)}
          />
          <select
            className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
            value={seFormat}
            onChange={(e) => setSeFormat(e.target.value)}
          >
            {["csv", "json", "ndjson", "m3u", "eol"].map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <Input
            className="max-w-[220px]"
            placeholder="destination path"
            value={seDest}
            onChange={(e) => setSeDest(e.target.value)}
          />
          <Input
            type="number"
            min={1}
            className="max-w-[90px]"
            value={seCadence}
            onChange={(e) => setSeCadence(e.target.value)}
            title="cadence (hours)"
          />
          <Button
            variant="outline"
            disabled={busy || !seLabel.trim() || !seDest.trim()}
            onClick={() => arm({ kind: "schedAdd", token: "" })}
          >
            Add schedule
          </Button>
          <Button variant="outline" disabled={busy} onClick={() => arm({ kind: "schedRunNow", token: "" })}>
            Run due now
          </Button>
        </div>
      </Card>

      {/* ── T5 — Diagnostics bundle (reads; download is a CSRF-exempt GET) ── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Diagnostics bundle</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" onClick={() => setDiagOpen((v) => !v)}>
            {diagOpen ? "Hide preview" : "Preview inline"}
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              downloadDiagnosticsBundle().catch((e: Error) => toast.error(e.message))
            }
          >
            Download zip
          </Button>
        </div>
        {diagOpen && (
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
            {diagPreview.isLoading ? "Loading…" : JSON.stringify(diagPreview.data ?? {}, null, 2)}
          </pre>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Per-site</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="site id"
            value={sid}
            onChange={(e) => setSid(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              const s = requireSite();
              if (s) arm({ kind: "authCheckOne", sid: s, token: "" });
            }}
          >
            Check auth health
          </Button>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => {
              const s = requireSite();
              if (s) arm({ kind: "driftReset", sid: s, token: "" });
            }}
          >
            Reset selector drift
          </Button>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => {
              const s = requireSite();
              if (s) arm({ kind: "budgetReset", sid: s, token: "" });
            }}
          >
            Reset daily budget
          </Button>
        </div>
      </Card>

      <DangerZone
        title="History prune"
        warning="Permanently deletes history rows older than the cutoff — this cannot be undone."
        className="mt-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Input
            type="number"
            min={1}
            className="max-w-[120px]"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
          <span className="text-sm text-ink-3">days to keep</span>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => {
              const d = parseInt(days, 10);
              if (!(d > 0)) {
                toast.error("days must be a positive number");
                return;
              }
              arm({ kind: "historyPrune", days: d, token: `PRUNE ${d}` });
            }}
          >
            Prune older history
          </Button>
        </div>
      </DangerZone>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Status</h2>
        {status.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
            {JSON.stringify(status.data, null, 2)}
          </pre>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">User templates</h2>
        {templates.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : tpls.length === 0 ? (
          <p className="text-sm text-ink-3">No user templates.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <tbody>
              {tpls.map((t) => {
                const id = String(tplId(t));
                const label = tplLabel(t);
                return (
                  <tr key={id} className="border-b border-border/40">
                    <td className="py-1.5 pr-3 font-mono text-xs">{id}</td>
                    <td className="py-1.5 pr-3">{label}</td>
                    <td className="py-1.5 text-right">
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => arm({ kind: "deleteTemplate", id, label, token: `DELETE ${id}` })}
                      >
                        Delete
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending && isTyped(pending)
                ? `This is destructive and cannot be undone. Proceed?`
                : `Confirm this operation.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {pending && isTyped(pending) ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
