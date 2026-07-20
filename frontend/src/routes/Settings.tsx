import { useEffect, useMemo, useState } from "react";
import { parsePathAllowlist } from "@/lib/pathAllowlist";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Save, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import AutomationStatusPanel from "@/components/AutomationStatusPanel";
import { DiscoRunPanel } from "@/components/DiscoRunPanel";
import { ConfigImportExport } from "@/components/ConfigImportExport";
import { EnvironmentSettings } from "@/components/EnvironmentSettings";
import { StoreRawSettings } from "@/components/StoreRawSettings";
import {
  SettingRow,
  SettingSection,
  SettingsSearchContext,
} from "@/components/SettingSection";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SecretField } from "@/components/SecretField";
import { Callout } from "@/components/ui/Callout";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { StickySaveBar } from "@/components/ui/StickySaveBar";
import { SettingsNav } from "@/components/ui/SettingsNav";
import { OriginChip } from "@/components/ui/OriginChip";
import { CopyButton } from "@/components/ui/CopyButton";
import { IntegrityZone } from "@/components/ui/IntegrityZone";
import { ValidationSummary } from "@/components/ui/ValidationSummary";
import { sectionForField } from "@/lib/settingsSchema";
import { apiGet, apiPost } from "@/lib/api-client";
import type { GlobalConfigSubset } from "@/lib/api-types";
import { useQueueBadgeMode } from "@/hooks/useQueueBadgeMode";
import { useSyncedSoundPref } from "@/hooks/useSyncedSoundPref";
import { useAiModels } from "@/hooks/useIntegrations";
import { ModelSelect } from "@/components/ui/ModelSelect";
import { cn } from "@/lib/utils";

// Settings tab. Composition (mockup settings.png):
//   - DOWNLOADS section: concurrent cap, watch folder, watch interval
//   - NETWORK section: path allowlist (read-only display), log level
//   - SYSTEM section: theme toggle, sound toggle, link to Advanced
//
// The page reads /api/global_config on mount, populates a local draft
// state, and POSTs back only the fields the SPA owns. Other config
// keys round-trip untouched because we don't include them in the POST.
//
// Sound on completion is by default a pure-client feature — Web Audio
// API plays a short ping when /api/queue/v2 transitions from
// running > 0 to running == 0. Persisted in localStorage like the
// theme. NOT in global_config by default because the per-device
// design from v3.64.2 still holds. v3.64.3 adds an OPT-IN sync via
// useSyncedSoundPref, which routes the same enabled-state through
// /api/global_config.sound_on_complete instead.

// v3.66.711: the emergency stop for ALL autonomous action. Spelled once, here --
// the parity inventory derives exposure from this literal, so a typo silently
// reports the control as missing rather than failing the build.
const KILL_KEY = "automation.master_off_switch" as const;

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;

// Cut 5 (B1.4): GET /api/global_config/origins response. Provenance only —
// never a field value. `restart` apply-timing is currently empty server-side
// (all fields immediate), but the shape is honored for forward-compat.
interface OriginDesc {
  origin: "default" | "global" | "env";
  apply_timing: "immediate" | "restart";
  env_locked: boolean;
  is_secret: boolean;
}
interface GlobalConfigOrigins {
  ok: boolean;
  fields: Record<string, OriginDesc>;
}

const SETTINGS_SECTIONS: { label: string; id: string }[] = [
  { label: "Downloads", id: "downloads" },
  { label: "AI assist", id: "ai-assist" },
  { label: "Network", id: "network" },
  { label: "Queue housekeeping", id: "queue-housekeeping" },
  { label: "Capture", id: "capture" },
  { label: "Diagnostics", id: "diagnostics" },
  { label: "Session keep-alive", id: "session-keep-alive" },
  { label: "System", id: "system" },
  { label: "Tools & operations", id: "tools-operations" },
  { label: "Supervisor throttle", id: "supervisor-throttle" },
  { label: "Browser", id: "browser" },
  { label: "Challenge handling", id: "challenge-handling" },
  { label: "Advanced", id: "advanced" },
  { label: "Security & access", id: "security-access" },
  { label: "Environment (restart required)", id: "environment-restart-required" },
  { label: "Import / Export", id: "import-export" },
];

function jumpToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function Settings() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery<GlobalConfigSubset>({
    queryKey: ["global-config"],
    queryFn: ({ signal }) => apiGet<GlobalConfigSubset>("/api/global_config", signal),
    refetchOnWindowFocus: false,
  });

  // Cut 5 (B1.4): per-field provenance — source + apply-timing + env-lock from
  // /api/global_config/origins. Read-only; values are never emitted by the
  // endpoint (secrets carry is_secret only). Drives OriginChip + env-lock +
  // the post-save summary's apply-timing line.
  const { data: origins } = useQuery<GlobalConfigOrigins>({
    queryKey: ["global-config-origins"],
    queryFn: ({ signal }) =>
      apiGet<GlobalConfigOrigins>("/api/global_config/origins", signal),
    refetchOnWindowFocus: false,
  });

  // P6-8: the SHIPPED defaults, fetched once. mod(k) badges a setting whose
  // saved value differs from its default. Keys without a shipped default
  // (most of the surface) never badge.
  const { data: gcDefaults } = useQuery<GlobalConfigSubset>({
    queryKey: ["global-config-defaults"],
    queryFn: ({ signal }) =>
      apiGet<GlobalConfigSubset>("/api/global_config/defaults", signal),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const mod = (k: keyof GlobalConfigSubset): boolean => {
    if (!data || !gcDefaults || !(k in gcDefaults)) return false;
    const cur = data[k];
    if (cur === undefined || cur === null) return false;
    return JSON.stringify(cur) !== JSON.stringify(gcDefaults[k]);
  };

  // Local draft mirrors the server-fetched config plus the user's
  // in-flight edits. Reset whenever the server's view changes.
  const [draft, setDraft] = useState<GlobalConfigSubset>({});
  const [query, setQuery] = useState("");
  // rate_limit_domain_overrides is edited as raw JSON in a textarea
  const [domainOverridesRaw, setDomainOverridesRaw] = useState<string>("");
  const [domainOverridesError, setDomainOverridesError] = useState<string>("");
  // path_allowlist is edited as one absolute root per line (T-bugfix)
  const [pathAllowlistRaw, setPathAllowlistRaw] = useState<string>("");
  const [pathAllowlistError, setPathAllowlistError] = useState<string>("");

  // Cut 7 (7.1): detect the models the (draft) endpoint actually exposes so the
  // two model fields can SUGGEST them. Fails open — on any error the suggestion
  // list is simply empty and the fields stay free-text editable.
  const aiModels = useAiModels();
  const detectModels = aiModels.mutate;
  const aiProvider = draft.ai_provider;
  const aiEndpoint = draft.ai_endpoint;
  useEffect(() => {
    detectModels({
      provider: aiProvider || undefined,
      endpoint: aiEndpoint || undefined,
    });
  }, [detectModels, aiProvider, aiEndpoint]);
  const modelOptions: string[] = (() => {
    const m = (aiModels.data as { models?: unknown[] } | undefined)?.models;
    return Array.isArray(m) ? m.map((x) => String(x)).filter(Boolean) : [];
  })();

  useEffect(() => {
    if (data) {
      setDraft(data);
      setDomainOverridesRaw(
        data.rate_limit_domain_overrides &&
        Object.keys(data.rate_limit_domain_overrides).length > 0
          ? JSON.stringify(data.rate_limit_domain_overrides, null, 2)
          : "",
      );
      setDomainOverridesError("");
      setPathAllowlistRaw((data.path_allowlist ?? []).join("\n"));
      setPathAllowlistError("");
    }
  }, [data]);

  // Sound preference — v3.64.3, via useSyncedSoundPref. Default is
  // localStorage-only (per-device, no behavior change from v3.64.2);
  // operator can opt into cross-device sync via the second toggle
  // below. The hook handles both paths and keeps localStorage in
  // sync so flipping sync OFF preserves the current value.
  const {
    enabled: sound,
    setEnabled: toggleSound,
    syncMode: soundSyncMode,
    setSyncMode: setSoundSyncMode,
  } = useSyncedSoundPref();

  // U3: Queue tab badge mode. Per-device, localStorage. Same scope
  // reasoning as the sound preference — silencing one device shouldn't
  // silence the others, and badge style is per-device taste.
  const { mode: queueBadgeMode, setMode: setQueueBadgeMode } = useQueueBadgeMode();

  const saveMut = useMutation<GlobalConfigSubset, Error, GlobalConfigSubset>({
    mutationFn: (patch) => apiPost<GlobalConfigSubset>("/api/global_config", patch),
    onSuccess: () => {
      toast.success("Settings saved");
      qc.invalidateQueries({ queryKey: ["global-config"] });
    },
    onError: (e) => toast.error(`Save failed: ${e.message}`),
  });

  const handleSave = () => {
    // Merge domain overrides from textarea into the payload
    let merged = { ...draft };
    const raw = domainOverridesRaw.trim();
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (typeof parsed !== "object" || Array.isArray(parsed)) {
          setDomainOverridesError("Must be a JSON object: { \"domain\": { max_concurrent, max_per_sec } }");
          return;
        }
        merged = { ...merged, rate_limit_domain_overrides: parsed };
        setDomainOverridesError("");
      } catch {
        setDomainOverridesError("Invalid JSON");
        return;
      }
    } else {
      // Empty textarea = clear overrides
      merged = { ...merged, rate_limit_domain_overrides: {} };
    }
    // Merge path allowlist (one absolute root per line; blank = empty allowlist)
    const al = parsePathAllowlist(pathAllowlistRaw);
    if ("error" in al) {
      setPathAllowlistError(al.error);
      return;
    }
    setPathAllowlistError("");
    merged = { ...merged, path_allowlist: al.roots };
    // Cut 5: snapshot the change count + apply-timing for the post-save summary.
    const n = changedKeys.length;
    const needsRestart = changedKeys.some(
      (k) => origins?.fields?.[k]?.apply_timing === "restart",
    );
    saveMut.mutate(merged, {
      onSuccess: () => setSavedSummary({ count: n, needsRestart }),
    });
  };

  // Cut 5: useDirtyForm-semantics changed-key engine over the existing draft.
  // (The page keeps its draft/setField value store + the two raw-JSON textareas,
  // which the hook's shallow per-key model doesn't capture; this derivation is
  // the changedKeys the hook exists to provide, computed against the saved
  // baseline `data` plus the textarea side-state.) Drives StickySaveBar count +
  // the SettingsNav section markers. `dirty` is now derived from it.
  const changedKeys = useMemo<string[]>(() => {
    if (!data) return [];
    const out: string[] = [];
    const keys = new Set<string>([
      ...Object.keys(draft),
      ...Object.keys(data),
    ]);
    keys.forEach((k) => {
      if (k === "rate_limit_domain_overrides" || k === "path_allowlist") return; // raw below
      const a = (draft as Record<string, unknown>)[k];
      const b = (data as Record<string, unknown>)[k];
      if (JSON.stringify(a) !== JSON.stringify(b)) out.push(k);
    });
    const doSaved =
      data.rate_limit_domain_overrides &&
      Object.keys(data.rate_limit_domain_overrides).length > 0
        ? JSON.stringify(data.rate_limit_domain_overrides, null, 2)
        : "";
    if (domainOverridesRaw.trim() !== doSaved) out.push("rate_limit_domain_overrides");
    if (pathAllowlistRaw.trim() !== (data.path_allowlist ?? []).join("\n").trim())
      out.push("path_allowlist");
    return out;
  }, [draft, data, domainOverridesRaw, pathAllowlistRaw]);

  const dirty = changedKeys.length > 0;

  // Section ids (page anchors) holding ≥1 unsaved config-backed field. Markers
  // fire only on config-backed sections (locked decision); a changed key with
  // no schema section contributes no marker.
  const changedSections = useMemo<Set<string>>(() => {
    const labelToId = new Map(SETTINGS_SECTIONS.map((s) => [s.label, s.id]));
    const out = new Set<string>();
    for (const k of changedKeys) {
      const sec = sectionForField(k);
      const id = sec ? labelToId.get(sec) : undefined;
      if (id) out.add(id);
    }
    return out;
  }, [changedKeys]);

  // Discard: restore the draft + both raw textareas to the saved baseline.
  const discard = () => {
    if (!data) return;
    setDraft(data);
    setDomainOverridesRaw(
      data.rate_limit_domain_overrides &&
        Object.keys(data.rate_limit_domain_overrides).length > 0
        ? JSON.stringify(data.rate_limit_domain_overrides, null, 2)
        : "",
    );
    setDomainOverridesError("");
    setPathAllowlistRaw((data.path_allowlist ?? []).join("\n"));
    setPathAllowlistError("");
  };

  // Post-save summary: count + apply-timing rollup, set on a successful save.
  const [savedSummary, setSavedSummary] = useState<{
    count: number;
    needsRestart: boolean;
  } | null>(null);

  // Cut 5: per-field provenance helpers (origins-backed fields only).
  const originOf = (k: string): OriginDesc | undefined => origins?.fields?.[k];
  const renderOrigin = (k: string) => {
    const o = originOf(k);
    if (!o) return null;
    return (
      <OriginChip
        origin={o.origin}
        applyTiming={o.apply_timing}
        envLocked={o.env_locked}
        isSecret={o.is_secret}
      />
    );
  };
  const envLocked = (k: string): boolean => !!originOf(k)?.env_locked;

  // Cut 5: aggregate active field-validation problems for the top summary.
  const problems = useMemo(() => {
    const out: { field: string; label: string; message: string }[] = [];
    if (domainOverridesError)
      out.push({
        field: "rate_limit_domain_overrides",
        label: "Per-domain rate overrides",
        message: domainOverridesError,
      });
    if (pathAllowlistError)
      out.push({
        field: "path_allowlist",
        label: "Path allowlist",
        message: pathAllowlistError,
      });
    return out;
  }, [domainOverridesError, pathAllowlistError]);

  const setField = <K extends keyof GlobalConfigSubset>(
    k: K,
    v: GlobalConfigSubset[K],
  ) => setDraft((prev) => ({ ...prev, [k]: v }));

  // ── Supervisor rate limits (T19a) ────────────────────────────────────
  // POST /api/supervisor/configure hot-reloads the supervisor's byte-rate
  // limiter ({enabled, global_bps, per_site_bps:{}}). Distinct from the
  // global_config rate caps above (request concurrency / per-second) — this is
  // the byte/sec throttle. It mutates live throttle state, so it is gated with
  // a typed confirm rather than the page's one-click Save.
  const [supEnabled, setSupEnabled] = useState(false);
  const [supGlobalBps, setSupGlobalBps] = useState("0");
  const [supPerSiteJson, setSupPerSiteJson] = useState("");
  const [supConfirm, setSupConfirm] = useState(false);
  // v3.66.711 (A-GUI Cut 3): the kill switch is ARM-CONFIRMED. It dominates every
  // other autonomy toggle, so it must not be flippable by a stray click. Two steps:
  // arm (opens the dialog) -> confirm (writes). Mirrors the BD_AUTONOMY_ENABLED
  // pattern from 319.
  const [offSwitchConfirm, setOffSwitchConfirm] = useState<boolean | null>(null);
  // v3.66.319 (4.3b): arming the autonomy final-apply switch (setting it "on")
  // requires an explicit second-step confirm, beyond the page's one-click Save —
  // it lets the system take autonomous Class-B state changes on its own.
  const [autonomyArmConfirm, setAutonomyArmConfirm] = useState(false);

  // Validate the per-site JSON locally so the operator sees the error before
  // arming the confirm. Empty = {}.
  let supPerSiteErr: string | null = null;
  let supPerSiteParsed: Record<string, number> = {};
  if (supPerSiteJson.trim()) {
    try {
      const parsed = JSON.parse(supPerSiteJson);
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        supPerSiteErr = "must be a JSON object {site: bps}";
      } else {
        supPerSiteParsed = parsed as Record<string, number>;
      }
    } catch {
      supPerSiteErr = "invalid JSON";
    }
  }

  const supMut = useMutation<{ ok: boolean; error?: string }, Error, void>({
    mutationFn: () =>
      apiPost<{ ok: boolean; error?: string }>("/api/supervisor/configure", {
        enabled: supEnabled,
        global_bps: Math.max(0, parseInt(supGlobalBps, 10) || 0),
        per_site_bps: supPerSiteParsed,
      }),
    onSuccess: (res) => {
      if (res.ok) toast.success("Supervisor limits applied");
      else toast.error(res.error ?? "Apply failed");
    },
    onError: (err) => toast.error(`Apply failed: ${err.message}`),
  });

  const trailing = (
    <Button
      size="sm"
      disabled={!dirty || saveMut.isPending}
      onClick={handleSave}
      className={cn(!dirty && "opacity-50")}
    >
      <Save className="h-3.5 w-3.5" aria-hidden />
      {saveMut.isPending ? "Saving…" : "Save"}
    </Button>
  );

  return (
    <AppShell title="Settings" trailing={trailing}>
      {isError && (
        <p className="rounded-md bg-red-soft p-3 text-sm text-red">
          Couldn't load settings.
        </p>
      )}
      {isLoading && !data && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {data && (
        <SettingsSearchContext.Provider value={query}>
        <div className={cn("space-y-5", query.trim() && "settings-searching")}>
          <div className="sticky top-0 z-10 -mx-1 space-y-2 bg-bg/95 px-1 pb-2 pt-1 backdrop-blur">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3"
                aria-hidden
              />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter settings…"
                className="pl-8"
                aria-label="Filter settings"
              />
            </div>
            {!query.trim() && (
              <SettingsNav
                variant="chips"
                sections={SETTINGS_SECTIONS}
                changedSections={changedSections}
                onNavigate={jumpToSection}
              />
            )}
          </div>
          <ValidationSummary problems={problems} onJump={(f) => {
            const sec = sectionForField(f);
            const id = sec
              ? SETTINGS_SECTIONS.find((s) => s.label === sec)?.id
              : undefined;
            if (id) jumpToSection(id);
          }} />
          {savedSummary && (
            <Callout tone="info" title="Settings saved" className="mb-1">
              {savedSummary.count === 1
                ? "1 change applied."
                : `${savedSummary.count} changes applied.`}{" "}
              {savedSummary.needsRestart
                ? "Some take effect after a restart."
                : "All take effect immediately."}
            </Callout>
          )}
          <SettingSection
            label="Downloads"
            description="Concurrency and watch-folder behaviour."
          >
            <SettingRow
              modified={mod("global_max_concurrent")}
              aside={renderOrigin("global_max_concurrent")}
              label="Global concurrent cap"
              hint="0 = uncapped; per-site limits still apply."
              control={
                <Input
                  type="number"
                  min={0}
                  max={64}
                  value={draft.global_max_concurrent ?? 0}
                  disabled={envLocked("global_max_concurrent")}
                  onChange={(e) =>
                    setField(
                      "global_max_concurrent",
                      Math.max(0, Math.min(64, parseInt(e.target.value, 10) || 0)),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Watch folder"
              hint="Empty = disabled. Folder scanned for new URLs."
              control={
                <Input
                  type="text"
                  value={draft.watch_folder ?? ""}
                  onChange={(e) => setField("watch_folder", e.target.value)}
                  placeholder="/path/to/watch"
                  className="w-56"
                />
              }
              stacked
            />
            <SettingRow
              label="Watch interval"
              hint="Seconds between folder scans."
              control={
                <Input
                  type="number"
                  min={5}
                  value={draft.watch_interval_sec ?? 30}
                  onChange={(e) =>
                    setField(
                      "watch_interval_sec",
                      Math.max(5, parseInt(e.target.value, 10) || 30),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Archive processed files"
              hint="Move watched-folder files to /processed after pickup."
              control={
                <Switch
                  checked={!!draft.watch_archive}
                  onChange={(v) => setField("watch_archive", v)}
                />
              }
            />
            <SettingRow
              label="Queue tab badge"
              hint="Count = red dot; Percent = live aggregate progress."
              control={
                <div
                  role="radiogroup"
                  aria-label="Queue tab badge mode"
                  className="hairline inline-flex rounded-md bg-surface p-0.5"
                >
                  <button
                    type="button"
                    role="radio"
                    aria-checked={queueBadgeMode === "count"}
                    onClick={() => setQueueBadgeMode("count")}
                    className={cn(
                      "rounded-sm px-2 py-1 text-xs font-medium transition-colors",
                      queueBadgeMode === "count"
                        ? "bg-primary text-white"
                        : "text-ink-2 hover:text-ink",
                    )}
                  >
                    Count
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={queueBadgeMode === "percent"}
                    onClick={() => setQueueBadgeMode("percent")}
                    className={cn(
                      "rounded-sm px-2 py-1 text-xs font-medium transition-colors",
                      queueBadgeMode === "percent"
                        ? "bg-primary text-white"
                        : "text-ink-2 hover:text-ink",
                    )}
                  >
                    Percent
                  </button>
                </div>
              }
            />
          </SettingSection>

          <SettingSection
            label="AI assist"
            description="Login-form detection and tagging via a local or cloud model."
          >
            <SettingRow
              modified={mod("ai_enabled")}
              aside={renderOrigin("ai_enabled")}
              label="Enabled"
              hint="Master switch for AI assist features."
              control={
                <Switch
                  checked={!!draft.ai_enabled}
                  disabled={envLocked("ai_enabled")}
                  onChange={(v) => setField("ai_enabled", v)}
                />
              }
            />
            <SettingRow
              modified={mod("ai_provider")}
              aside={renderOrigin("ai_provider")}
              label="Provider"
              hint="ollama is local/LAN-only; cloud providers need an API key."
              control={
                <select
                  value={draft.ai_provider ?? "ollama"}
                  disabled={envLocked("ai_provider")}
                  onChange={(e) => setField("ai_provider", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["ollama", "claude", "openai", "gemini"].map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              modified={mod("ai_endpoint")}
              aside={<>{renderOrigin("ai_endpoint")}<CopyButton value={String(draft.ai_endpoint ?? "")} label="Copy endpoint" /></>}
              label="Endpoint"
              hint="Model server URL (e.g. http://localhost:11434 for ollama)."
              control={
                <Input
                  type="text"
                  value={draft.ai_endpoint ?? ""}
                  disabled={envLocked("ai_endpoint")}
                  onChange={(e) => setField("ai_endpoint", e.target.value)}
                  placeholder="http://localhost:11434"
                />
              }
            />
            <SettingRow
              modified={mod("ai_model_vision")}
              aside={renderOrigin("ai_model_vision")}
              label="Vision model"
              hint="Model used for screenshot/vision tasks."
              control={
                <ModelSelect
                  value={draft.ai_model_vision ?? ""}
                  options={modelOptions}
                  loading={aiModels.isPending}
                  disabled={envLocked("ai_model_vision")}
                  onChange={(v) => setField("ai_model_vision", v)}
                  placeholder="qwen2.5vl:7b"
                />
              }
            />
            <SettingRow
              modified={mod("ai_model_text")}
              aside={renderOrigin("ai_model_text")}
              label="Text model"
              hint="Model used for text tasks."
              control={
                <ModelSelect
                  value={draft.ai_model_text ?? ""}
                  options={modelOptions}
                  loading={aiModels.isPending}
                  disabled={envLocked("ai_model_text")}
                  onChange={(v) => setField("ai_model_text", v)}
                  placeholder="qwen2.5:7b"
                />
              }
            />
            <SettingRow
              modified={mod("ai_api_key")}
              aside={renderOrigin("ai_api_key")}
              label="API key"
              stacked
              hint="Provider API key for cloud AI backends (claude / openai / gemini). Not needed for a local ollama endpoint. Stored encrypted; masked on read. Leave blank to keep the current key — a blank field never clears it."
              control={
                <SecretField
                  placeholder={draft.ai_api_key === "<configured>" ? "•••••••• (set — blank keeps it)" : "not set"}
                  value={draft.ai_api_key === "<configured>" ? "" : (draft.ai_api_key ?? "")}
                  disabled={envLocked("ai_api_key")}
                  onChange={(v) => setField("ai_api_key", v)}
                  ariaLabel="API key"
                  className="w-full"
                />
              }
            />
          </SettingSection>

          <SettingSection
            label="Network"
            description="Path security, rate limiting, and logging."
          >
            <SettingRow
              label="Log level"
              hint="Applied immediately; no restart needed."
              control={
                <select
                  value={draft.log_level ?? "INFO"}
                  onChange={(e) => setField("log_level", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {LOG_LEVELS.map((lv) => (
                    <option key={lv} value={lv}>
                      {lv}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              label="UI event detail"
              hint="Verbosity of in-app UI event logging. Applied immediately."
              control={
                <select
                  value={draft.ui_logging_level ?? "basic"}
                  onChange={(e) => setField("ui_logging_level", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["basic", "verbose", "extreme"].map((lv) => (
                    <option key={lv} value={lv}>
                      {lv}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              label="Template auto-detect"
              hint="How aggressively new sites are auto-detected at add time."
              control={
                <select
                  value={draft.template_auto_detect_mode ?? "static"}
                  onChange={(e) =>
                    setField("template_auto_detect_mode", e.target.value)
                  }
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["static", "detect", "detect_then_static", "deep"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              modified={mod("rate_limit_global_concurrent")}
              aside={renderOrigin("rate_limit_global_concurrent")}
              label="Global concurrent cap"
              hint="Max simultaneous requests across all domains. 0 = uncapped. Applied immediately."
              control={
                <Input
                  type="number"
                  min={0}
                  max={256}
                  value={draft.rate_limit_global_concurrent ?? 0}
                  disabled={envLocked("rate_limit_global_concurrent")}
                  onChange={(e) =>
                    setField(
                      "rate_limit_global_concurrent",
                      Math.max(0, parseInt(e.target.value, 10) || 0),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              modified={mod("rate_limit_global_per_sec")}
              aside={renderOrigin("rate_limit_global_per_sec")}
              label="Global per-second rate"
              hint="Max requests per second globally. 0 = uncapped. Applied immediately."
              control={
                <Input
                  type="number"
                  min={0}
                  step={0.5}
                  value={draft.rate_limit_global_per_sec ?? 0}
                  disabled={envLocked("rate_limit_global_per_sec")}
                  onChange={(e) =>
                    setField(
                      "rate_limit_global_per_sec",
                      Math.max(0, parseFloat(e.target.value) || 0),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Domain overrides"
              hint={
                domainOverridesError
                  ? domainOverridesError
                  : "JSON object: { \"example.com\": { \"max_concurrent\": 2, \"max_per_sec\": 1.0 } }. Empty = no overrides."
              }
              stacked
              control={
                <textarea
                  value={domainOverridesRaw}
                  onChange={(e) => {
                    setDomainOverridesRaw(e.target.value);
                    setDomainOverridesError("");
                  }}
                  rows={4}
                  placeholder={"{\n  \"example.com\": { \"max_concurrent\": 2, \"max_per_sec\": 1.0 }\n}"}
                  className={cn(
                    "hairline w-full rounded-md bg-surface px-2 py-1.5 font-mono text-xs",
                    domainOverridesError && "border-red",
                  )}
                />
              }
            />
            <SettingRow
              label="Path allowlist"
              hint={
                pathAllowlistError
                  ? pathAllowlistError
                  : "One absolute root per line. Empty = any absolute non-traversing path accepted. Saved with the page's Save button."
              }
              stacked
              control={
                <textarea
                  value={pathAllowlistRaw}
                  onChange={(e) => {
                    setPathAllowlistRaw(e.target.value);
                    setPathAllowlistError("");
                  }}
                  rows={4}
                  placeholder={"/srv/reports\n/mnt/captures"}
                  className={cn(
                    "hairline w-full rounded-md bg-surface px-2 py-1.5 font-mono text-xs",
                    pathAllowlistError && "border-red",
                  )}
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false} label="Queue housekeeping"
            description="Garbage-collection + stuck-row handling for the job queue. Takes effect on the next housekeeping run."
          >
            <SettingRow
              label="GC age (days)"
              hint="Delete finished (done/error/failed) rows untouched for this many days. 0 = immediate."
              control={
                <Input
                  type="number"
                  min={0}
                  value={draft.queue_hk_gc_age_days ?? 7}
                  onChange={(e) =>
                    setField(
                      "queue_hk_gc_age_days",
                      Math.max(0, parseInt(e.target.value, 10) || 0),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Abandon stuck rows"
              hint="Transition retry-exhausted / stale rows to failed. Higher blast radius — enable after a dry-run week."
              control={
                <Switch
                  checked={!!draft.queue_hk_abandon}
                  onChange={(v) => setField("queue_hk_abandon", v)}
                />
              }
            />
            <SettingRow
              label="Max retries"
              hint="A row at or above this retry count is eligible to be abandoned."
              control={
                <Input
                  type="number"
                  min={0}
                  value={draft.queue_hk_max_retries ?? 10}
                  onChange={(e) =>
                    setField(
                      "queue_hk_max_retries",
                      Math.max(0, parseInt(e.target.value, 10) || 0),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Stale threshold (hours)"
              hint="A pending/running row with no update for this many hours is considered stuck."
              control={
                <Input
                  type="number"
                  min={1}
                  value={draft.queue_hk_stale_hours ?? 24}
                  onChange={(e) =>
                    setField(
                      "queue_hk_stale_hours",
                      Math.max(1, parseInt(e.target.value, 10) || 24),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false} label="Capture"
            description="Guard-backed capture & redaction tunables, promoted from the BD_CAPTURE_* env vars. Take effect on the next capture — no restart. Controls marked ⚠ have IRRECOVERABLE effects; read the disclaimer before changing."
          >
            <IntegrityZone
              title="Redaction & capture integrity"
              className="mb-3"
            >
              These controls govern what a capture retains. Loosening redaction
              keeps secrets, cookies, signed media URLs and login tokens in the
              capture — and a capture cannot be un-circulated once shared. Keep
              raw or under-redacted captures strictly local.
            </IntegrityZone>
            <SettingRow
              label="Capture response bodies"
              danger
              hint="Backs the SHA-pinned capture-integrity core. Retains text/JSON response bodies in the WACZ. A wrong value can silently weaken capture; affected captures cannot be re-derived. Default OFF."
              control={
                <Switch
                  checked={!!draft.capture_bodies}
                  onChange={(v) => setField("capture_bodies", v)}
                />
              }
            />
            <SettingRow
              label="Navigation wait-until"
              danger
              hint="Guard-backed. Weakens the capture goto wait condition; arms the DOM recorder earlier and has historically destabilized heavy SPAs. Justify per-target with nav_probe.py first. Blank = unchanged (default)."
              control={
                <select
                  value={draft.capture_wait_until ?? ""}
                  onChange={(e) => setField("capture_wait_until", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["", "load", "domcontentloaded", "commit"].map((v) => (
                    <option key={v || "default"} value={v}>
                      {v || "(unchanged)"}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              label="DOM honeypot filter"
              hint="Detection-quality filter for hidden/decoy download links. cheap = fast heuristic; strict = both passes; off = disabled (default)."
              control={
                <select
                  value={draft.dom_honeypot_filter ?? "off"}
                  onChange={(e) => setField("dom_honeypot_filter", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["off", "cheap", "strict"].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              label="DOM embedded-URL redaction"
              danger
              hint="keep_full RETAINS signed URLs embedded in the DOM log — a capture made this way cannot be un-circulated once shared. keep_structure (default) redacts signing; strip_all removes them."
              control={
                <select
                  value={draft.redact_dom_urls ?? "keep_structure"}
                  onChange={(e) => setField("redact_dom_urls", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  {["keep_structure", "strip_all", "keep_full"].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              }
            />
            <SettingRow
              label="Raw capture (disable redaction)"
              danger
              hint="DANGER — disables ALL capture redaction. Secrets, cookies, signed media URLs and login tokens are RETAINED in the capture. A raw capture cannot be un-circulated once shared. Keep raw captures strictly local. Default OFF."
              control={
                <Switch
                  checked={!!draft.capture_raw}
                  onChange={(v) => setField("capture_raw", v)}
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false} label="Diagnostics"
            description="Slow-query logging. Takes effect on the next DB connection — no restart."
          >
            <SettingRow
              label="Slow-query logging"
              hint="Log SQL statements slower than the threshold below. Default on."
              control={
                <Switch
                  checked={draft.slow_query_log ?? true}
                  onChange={(v) => setField("slow_query_log", v)}
                />
              }
            />
            <SettingRow
              label="Slow-query threshold (ms)"
              hint="Statements slower than this are logged. Default 100ms."
              control={
                <Input
                  type="number"
                  min={1}
                  value={draft.slow_query_ms ?? 100}
                  onChange={(e) => setField("slow_query_ms", Number(e.target.value))}
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false} label="Session keep-alive"
            description="How often the background keeper refreshes login sessions. With the persistent Chromium context (v3.64.2), 30 minutes is comfortable — the browser's cookie jar survives across heartbeats. Lower these only if a specific site rotates session tokens aggressively."
          >
            <SettingRow
              label="Fetch heartbeat interval"
              hint="Lightweight in-page authenticated fetch. Minutes; 1–360."
              control={
                <Input
                  type="number"
                  min={1}
                  max={360}
                  value={draft.session_keep_alive_fetch_interval_min ?? 30}
                  onChange={(e) =>
                    setField(
                      "session_keep_alive_fetch_interval_min",
                      Math.max(1, Math.min(360, parseInt(e.target.value, 10) || 30)),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Full-navigate interval"
              hint="Full page reload — catches sites that only rotate cookies on navigation. Minutes; 1–360."
              control={
                <Input
                  type="number"
                  min={1}
                  max={360}
                  value={draft.session_keep_alive_navigate_interval_min ?? 30}
                  onChange={(e) =>
                    setField(
                      "session_keep_alive_navigate_interval_min",
                      Math.max(1, Math.min(360, parseInt(e.target.value, 10) || 30)),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Pre-expiry lead time"
              hint="How long before a predicted session expiry to refresh. If the site session is shorter than this, falls through to the fetch interval. Minutes; 1–360."
              control={
                <Input
                  type="number"
                  min={1}
                  max={360}
                  value={draft.session_keep_alive_lead_time_min ?? 30}
                  onChange={(e) =>
                    setField(
                      "session_keep_alive_lead_time_min",
                      Math.max(1, Math.min(360, parseInt(e.target.value, 10) || 30)),
                    )
                  }
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Use CloakBrowser for keep-alive"
              hint="Launch keep-alive browsers via CloakBrowser (canonical stealth Chromium). Off = vanilla Playwright. Per-site config and the BD_USE_CLOAK env var override this."
              control={
                <Switch
                  checked={draft.session_keeper_use_cloakbrowser ?? true}
                  onChange={(v) => setField("session_keeper_use_cloakbrowser", v)}
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false}
            label="System"
            description="UI + per-device preferences."
          >
            <SettingRow
              label="Theme"
              hint="Choose System to follow your OS setting."
              control={<ThemeToggle />}
            />
            <SettingRow
              label="Sound on completion"
              hint="Play a tone when the queue finishes."
              control={
                <Switch checked={sound} onChange={toggleSound} />
              }
            />
            <SettingRow
              label="Sync sound across devices"
              hint={
                soundSyncMode === "on"
                  ? "Sound preference reads from the server. All devices share it."
                  : "Sound preference is per-device. Toggle on to share across devices."
              }
              control={
                <Switch
                  checked={soundSyncMode === "on"}
                  onChange={(v) => setSoundSyncMode(v ? "on" : "off")}
                  ariaLabel="Sync sound preference across devices"
                />
              }
            />
            <Link
              to="/settings/advanced"
              className="flex items-center justify-between p-3 hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">Advanced</div>
                <div className="mt-0.5 text-[11px] text-ink-3">
                  Health checks, system info, diagnostics.
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
            </Link>
            <Link
              to="/secrets"
              className="flex items-center justify-between p-3 hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">Secrets vault</div>
                <div className="mt-0.5 text-[11px] text-ink-3">
                  Backend, unlock/lock, rotate password, migrate plaintext.
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
            </Link>
            <Link
              to="/ai-teach"
              className="flex items-center justify-between p-3 hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">AI selector repair</div>
                <div className="mt-0.5 text-[11px] text-ink-3">
                  Propose replacement selectors after a site redesign, review, commit.
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
            </Link>
            <Link
              to="/dom-analyzer"
              className="flex items-center justify-between p-3 hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-ink">DOM analyzer</div>
                <div className="mt-0.5 text-[11px] text-ink-3">
                  Inspect a captured DOM offline, test selectors, pin a review-only candidate.
                </div>
              </div>
              <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
            </Link>
          </SettingSection>

          {/* v3.66.200 nav consolidation — the GUI-parity wave pages
              (library, backup, imports, …) shipped routes + wired
              endpoints but no nav entry; they were typed-URL only.
              See docs/NAV_CONSOLIDATION.md. This section is their
              canonical home; CommandPalette mirrors it for ⌘K. */}
          <SettingSection
            collapsible defaultOpen={false}
            label="Tools & operations"
            description="Operator surfaces from the GUI-parity program. All actions inside are confirm-gated."
          >
            {[
              { to: "/library", label: "Library", hint: "Reconcile, prune, and verify the on-disk library." },
              { to: "/backup", label: "Backup", hint: "Config backup and restore." },
              { to: "/maintenance", label: "Maintenance", hint: "Cache, locks, retention, and DB housekeeping." },
              { to: "/more-actions", label: "More actions", hint: "Misc gated operations that fit nowhere else." },
              { to: "/imports", label: "Imports", hint: "Bulk import runs and history." },
              { to: "/import-views", label: "Import views", hint: "Saved import filters and views." },
              { to: "/dedup", label: "Dedup", hint: "Duplicate detection and resolution." },
              { to: "/rebalance", label: "Rebalance", hint: "Storage-tier rebalancing." },
              { to: "/vpn", label: "VPN", hint: "Tunnel status and posture controls." },
              { to: "/integrations", label: "Integrations", hint: "Plex, TPDB, subtitles, marketplace, JSON-API, AI." },
              { to: "/batch-ops", label: "Batch operations", hint: "Multi-site batched actions." },
              { to: "/pools-macros", label: "Pools & macros", hint: "Worker pools and macro definitions." },
            ].map(({ to, label, hint }) => (
              <Link
                key={to}
                to={to}
                className="flex items-center justify-between p-3 hover:bg-surface-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-ink">{label}</div>
                  <div className="mt-0.5 text-[11px] text-ink-3">{hint}</div>
                </div>
                <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
              </Link>
            ))}
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false}
            label="Supervisor throttle"
            description="Live byte-rate limiter. Hot-reloads on apply — separate from the request caps above."
          >
            <SettingRow
              label="Enabled"
              hint="Master switch for the byte/sec throttle."
              control={
                <Switch
                  checked={supEnabled}
                  onChange={setSupEnabled}
                  ariaLabel="Supervisor throttle enabled"
                />
              }
            />
            <SettingRow
              label="Global bytes/sec"
              hint="Total throughput cap across all sites. 0 = uncapped."
              control={
                <Input
                  type="number"
                  min={0}
                  value={supGlobalBps}
                  onChange={(e) => setSupGlobalBps(e.target.value)}
                  className="w-32 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Per-site bytes/sec"
              hint='JSON map of site → bytes/sec, e.g. {"example": 500000}. Blank = none.'
              stacked
              control={
                <div className="w-full space-y-1">
                  <textarea
                    className="h-20 w-full rounded border border-input bg-background p-2 font-mono text-xs"
                    value={supPerSiteJson}
                    onChange={(e) => setSupPerSiteJson(e.target.value)}
                    placeholder='{"example": 500000}'
                    aria-label="Per-site bytes per second JSON"
                  />
                  <div className="text-[11px] text-ink-3">
                    {supPerSiteErr ? (
                      <span className="text-red">{supPerSiteErr}</span>
                    ) : (
                      "valid"
                    )}
                  </div>
                </div>
              }
            />
            <SettingRow
              label="Apply throttle"
              hint="Hot-reloads the limiter immediately. Requires a typed confirm."
              control={
                <Button
                  size="sm"
                  disabled={!!supPerSiteErr || supMut.isPending}
                  onClick={() => {
                    setSupConfirm(true);
                  }}
                >
                  {supMut.isPending ? "Applying…" : "Apply"}
                </Button>
              }
            />
          </SettingSection>

          <SettingSection
            label="Browser"
            description="Which browser backend launches flows, and the noVNC URL for manual login. The matching env var (BD_BROWSER_BACKEND / BD_NOVNC_URL), when set at deploy time, overrides these."
          >
            <SettingRow
              label="Backend"
              hint="CloakBrowser (anti-detection) or vanilla Playwright. Falls back to Playwright if CloakBrowser isn't installed."
              control={
                <select
                  value={draft.browser_backend ?? ""}
                  onChange={(e) => setField("browser_backend", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1 text-sm text-ink"
                  aria-label="Browser backend"
                >
                  <option value="">Auto (default)</option>
                  <option value="cloakbrowser">CloakBrowser</option>
                  <option value="playwright">Playwright</option>
                </select>
              }
            />
            <SettingRow
              label="noVNC URL"
              hint="Empty = disabled. Server-set only; never browser-supplied."
              control={
                <Input
                  type="text"
                  value={draft.novnc_url ?? ""}
                  onChange={(e) => setField("novnc_url", e.target.value)}
                  placeholder="https://host:6080/vnc.html"
                  className="w-72"
                />
              }
              stacked
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false}
            label="Challenge handling"
            description="Honeypot-trap detection tuning. A Settings write takes effect on the next candidate resolution / per-site learn (the env var is the seed when unset)."
          >
            <SettingRow
              label="Honeypot score threshold"
              hint="Drop candidates scoring at/above this (0–1). Empty = off; values >1 or ≤0 are treated as off."
              control={
                <Input
                  type="text"
                  inputMode="decimal"
                  value={draft.honeypot_score_threshold ?? ""}
                  onChange={(e) => setField("honeypot_score_threshold", e.target.value)}
                  placeholder="off"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Per-site threshold learning"
              hint="Refine the threshold from each site's confirmed-trap scores (opt-in)."
              control={
                <Switch
                  checked={!!draft.honeypot_per_site}
                  onChange={(v) => setField("honeypot_per_site", v)}
                  ariaLabel="Per-site honeypot threshold learning"
                />
              }
            />
            <SettingRow
              label="Challenge wait (seconds)"
              hint="How long to wait for an anti-bot interstitial to clear before a capture is considered loaded. 0 disables the wait; empty uses the default (20)."
              control={
                <Input
                  type="text"
                  inputMode="decimal"
                  value={draft.challenge_wait_s ?? ""}
                  onChange={(e) => setField("challenge_wait_s", e.target.value)}
                  placeholder="20"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Captcha pending timeout (seconds)"
              hint="After this long with no user action, a pending captcha session auto-expires and the URL is failed in the queue. Empty uses the default (3600)."
              control={
                <Input
                  type="text"
                  inputMode="numeric"
                  value={draft.captcha_pending_timeout_s ?? ""}
                  onChange={(e) => setField("captcha_pending_timeout_s", e.target.value)}
                  placeholder="3600"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Captcha push dedupe (seconds)"
              hint="Minimum gap between push notifications for the same (site, challenge type). Empty uses the default (300)."
              control={
                <Input
                  type="text"
                  inputMode="numeric"
                  value={draft.captcha_push_dedupe_s ?? ""}
                  onChange={(e) => setField("captcha_push_dedupe_s", e.target.value)}
                  placeholder="300"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Captcha takeover mode"
              hint="How a captcha solve session presents. 'Visible' opens the solve browser on the server display (default). 'Remote' opens it headless and streams it to this cockpit over CDP so you can solve the challenge here. 'Remote VNC' streams a live KasmVNC display with real X input (harder to fingerprint as non-human, at the cost of a heavier stack and a different display fingerprint); it needs the VNC display stack and falls back to Remote when that stack is absent."
              control={
                <select
                  value={draft.captcha_takeover_mode ?? "visible"}
                  onChange={(e) => setField("captcha_takeover_mode", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  <option value="visible">Visible (server display)</option>
                  <option value="remote">Remote (cockpit takeover)</option>
                  <option value="remote_vnc">Remote VNC (KasmVNC)</option>
                </select>
              }
            />
            <SettingRow
              label="Remote takeover enabled (kill-switch)"
              hint="Master switch for remote captcha takeover. Off (default) forces all solves to the visible server-display path, even when mode is Remote. Flip off to instantly stop all remote takeover."
              control={
                <Switch
                  checked={!!draft.captcha_takeover_enabled}
                  onChange={(v) => setField("captcha_takeover_enabled", v)}
                />
              }
            />
            <SettingRow
              label="Remote takeover max concurrent"
              hint="Cap on simultaneous remote solve sessions. Beyond this, new solves fall back to visible. Empty uses the default (2)."
              control={
                <Input
                  type="text"
                  inputMode="numeric"
                  value={draft.captcha_takeover_max_concurrent ?? ""}
                  onChange={(e) => setField("captcha_takeover_max_concurrent", e.target.value)}
                  placeholder="2"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="Remote takeover idle timeout (sec)"
              hint="A remote solve session with no operator input for this long is closed and its URL dismissed. Each input resets the clock; the viewer stream ends on the same bound. Empty uses the default (300)."
              control={
                <Input
                  type="text"
                  inputMode="numeric"
                  value={draft.captcha_takeover_idle_timeout_s ?? ""}
                  onChange={(e) => setField("captcha_takeover_idle_timeout_s", e.target.value)}
                  placeholder="300"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="VNC takeover display"
              hint="X display the Remote-VNC takeover browser renders on (e.g. :5). Must match where kasmvncserver is serving. Empty uses the default (:5)."
              control={
                <Input
                  type="text"
                  value={draft.captcha_vnc_display ?? ""}
                  onChange={(e) => setField("captcha_vnc_display", e.target.value)}
                  placeholder=":5"
                  className="w-24 text-right tabular"
                />
              }
            />
            <SettingRow
              label="VNC takeover websocket port"
              hint="KasmVNC websocket port the derived capability probe + default viewer target. Must match the server's -websocketPort. Empty uses the default (8444)."
              control={
                <Input
                  type="text"
                  inputMode="numeric"
                  value={draft.captcha_vnc_websocket_port ?? ""}
                  onChange={(e) => setField("captcha_vnc_websocket_port", e.target.value)}
                  placeholder="8444"
                  className="w-24 text-right tabular"
                />
              }
            />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false}
            label="Advanced"
            description="Power-user tunables promoted from environment variables. A Settings write takes effect on the next relevant operation (the env var is the seed when unset). Numeric fields accept a number or blank for the default."
          >
            <SettingRow
              label="Auth throttle"
              hint="Exponential back-off on repeated failed logins."
              control={
                <Switch
                  checked={!!draft.auth_throttle}
                  onChange={(v) => setField("auth_throttle", v)}
                  ariaLabel="Auth throttle"
                />
              }
            />
            <SettingRow
              label="Cross-site selector reuse"
              hint="Reuse learned login/form selectors across sites with the same shape. Opt-in; off by default."
              control={
                <Switch
                  checked={!!draft.cross_site_selectors}
                  onChange={(v) => setField("cross_site_selectors", v)}
                  ariaLabel="Cross-site selector reuse"
                />
              }
            />
            <SettingRow
              label="Structural tie-breaker (player ID)"
              hint="When the player-recognition rules leave a genuine 2-way tie, let the structural-embedding classifier break it toward its high-confidence pick. Review-only build metadata; it never invents a player family or overrides a storage tell. Opt-in; off by default."
              control={
                <Switch
                  checked={!!draft.player_struct_tiebreak}
                  onChange={(v) => setField("player_struct_tiebreak", v)}
                  ariaLabel="Structural tie-breaker"
                />
              }
            />
            <SettingRow
              label="Throttle free attempts"
              hint="Failures allowed before back-off starts."
              control={
                <Input type="text" inputMode="numeric" placeholder="5"
                  value={draft.auth_throttle_free ?? ""}
                  onChange={(e) => setField("auth_throttle_free", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Throttle base seconds"
              hint="First back-off interval; doubles each failure."
              control={
                <Input type="text" inputMode="decimal" placeholder="2.0"
                  value={draft.auth_throttle_base ?? ""}
                  onChange={(e) => setField("auth_throttle_base", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Throttle max seconds"
              hint="Cap on the back-off interval."
              control={
                <Input type="text" inputMode="decimal" placeholder="300.0"
                  value={draft.auth_throttle_max ?? ""}
                  onChange={(e) => setField("auth_throttle_max", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Redact emails"
              hint="keep = retain emails in captures; redact = mask them. Loosening RETAINS sensitive data above the floor."
              danger
              control={
                <Input type="text" placeholder="default"
                  value={draft.redact_emails ?? ""}
                  onChange={(e) => setField("redact_emails", e.target.value)}
                  className="w-32 text-right" />
              }
            />
            <SettingRow
              label="Redact extra headers"
              hint="Comma-separated header names to also mask. Empty = built-in defaults."
              danger
              control={
                <Input type="text" placeholder="x-foo, x-bar"
                  value={draft.redact_extra_headers ?? ""}
                  onChange={(e) => setField("redact_extra_headers", e.target.value)}
                  className="w-48 text-right" />
              }
            />
            <SettingRow
              label="Redact network URLs"
              hint="keep_full / keep_structure / redact. Loosening RETAINS signed URLs in captures — IRRECOVERABLE once shared."
              danger
              control={
                <Input type="text" placeholder="default"
                  value={draft.redact_network_urls ?? ""}
                  onChange={(e) => setField("redact_network_urls", e.target.value)}
                  className="w-40 text-right" />
              }
            />
            <SettingRow
              label="Secrets audit"
              hint="off / mutations / all — audit-log secret store access."
              control={
                <Input type="text" placeholder="off"
                  value={draft.secrets_audit ?? ""}
                  onChange={(e) => setField("secrets_audit", e.target.value)}
                  className="w-32 text-right" />
              }
            />
            <SettingRow
              label="Secrets audit sink"
              hint="file / stderr — where audit entries are written."
              control={
                <Input type="text" placeholder="file"
                  value={draft.secrets_audit_sink ?? ""}
                  onChange={(e) => setField("secrets_audit_sink", e.target.value)}
                  className="w-32 text-right" />
              }
            />
            <SettingRow
              label="Secrets audit file"
              hint="Path for the audit log (when sink = file)."
              control={
                <Input type="text" placeholder="default"
                  value={draft.secrets_audit_file ?? ""}
                  onChange={(e) => setField("secrets_audit_file", e.target.value)}
                  className="w-48 text-right" />
              }
            />
            <SettingRow
              label="Secrets audit max bytes"
              hint="Rotate the audit log past this size."
              control={
                <Input type="text" inputMode="numeric" placeholder="default"
                  value={draft.secrets_audit_max_bytes ?? ""}
                  onChange={(e) => setField("secrets_audit_max_bytes", e.target.value)}
                  className="w-28 text-right tabular" />
              }
            />
            <SettingRow
              label="Held-out stale days"
              hint="Age after which a held-out site is flagged for re-assist."
              control={
                <Input type="text" inputMode="numeric" placeholder="180"
                  value={draft.held_out_stale_days ?? ""}
                  onChange={(e) => setField("held_out_stale_days", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Library reconcile missing days"
              hint="Look-back window for library reconciliation."
              control={
                <Input type="text" inputMode="numeric" placeholder="30"
                  value={draft.lib_reconcile_missing_days ?? ""}
                  onChange={(e) => setField("lib_reconcile_missing_days", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Fleet nodes file"
              hint="Path to a JSON file listing fleet nodes for the framework dashboard."
              control={
                <Input type="text" placeholder="none"
                  value={draft.fleet_nodes ?? ""}
                  onChange={(e) => setField("fleet_nodes", e.target.value)}
                  className="w-48 text-right" />
              }
            />
            <SettingRow
              label="YouTube cipher backend"
              hint="off / yt-dlp / player-js — signature-cipher solver for YouTube."
              control={
                <Input type="text" placeholder="off"
                  value={draft.youtube_cipher ?? ""}
                  onChange={(e) => setField("youtube_cipher", e.target.value)}
                  className="w-32 text-right" />
              }
            />
            <SettingRow
              label="HLS input timeout (us)"
              hint="ffmpeg input timeout per microsegment, in microseconds. Empty uses the default (10000000 = 10s)."
              control={
                <Input type="text" inputMode="numeric" placeholder="10000000"
                  value={draft.hls_input_timeout_us ?? ""}
                  onChange={(e) => setField("hls_input_timeout_us", e.target.value)}
                  className="w-32 text-right tabular" />
              }
            />
            <SettingRow
              label="HLS max runtime (seconds)"
              hint="Soft cap on total download wall time per HLS URL. Empty uses the default (3600 = 60min)."
              control={
                <Input type="text" inputMode="numeric" placeholder="3600"
                  value={draft.hls_max_runtime_s ?? ""}
                  onChange={(e) => setField("hls_max_runtime_s", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="HLS progress poll (seconds)"
              hint="How often to poll the output file size for HLS progress reports. Empty uses the default (1.0)."
              control={
                <Input type="text" inputMode="decimal" placeholder="1.0"
                  value={draft.hls_progress_poll_s ?? ""}
                  onChange={(e) => setField("hls_progress_poll_s", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Live poll interval (seconds)"
              hint="How often each watched room is checked for going live. Empty uses the default (60)."
              control={
                <Input type="text" inputMode="numeric" placeholder="60"
                  value={draft.live_poll_interval_s ?? ""}
                  onChange={(e) => setField("live_poll_interval_s", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Live disconnect tolerance (seconds)"
              hint="How long a recording may be 'stream not live' before it's finalized. Tolerates short mid-broadcast disconnects. Empty uses the default (180)."
              control={
                <Input type="text" inputMode="numeric" placeholder="180"
                  value={draft.live_disconnect_tolerance_s ?? ""}
                  onChange={(e) => setField("live_disconnect_tolerance_s", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Live max active recordings"
              hint="Hard cap on simultaneous active live recordings. Empty uses the default (32)."
              control={
                <Input type="text" inputMode="numeric" placeholder="32"
                  value={draft.live_max_active_recordings ?? ""}
                  onChange={(e) => setField("live_max_active_recordings", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Live launch timeout (seconds)"
              hint="Streamlink/ffmpeg launch timeout: if no output is written within this window after launch, the subprocess is considered stuck. Empty uses the default (45). (Reserved: no live consumer yet.)"
              control={
                <Input type="text" inputMode="numeric" placeholder="45"
                  value={draft.live_launch_timeout_s ?? ""}
                  onChange={(e) => setField("live_launch_timeout_s", e.target.value)}
                  className="w-24 text-right tabular" />
              }
            />
            <SettingRow
              label="Capture HUD"
              hint="Decorative capture status panel (default on). Backs the SHA-pinned capture core — change with a known-good value, then re-run release-guard verification."
              danger
              control={
                <Switch
                  checked={draft.hud_overlay ?? true}
                  onChange={(v) => setField("hud_overlay", v)}
                  ariaLabel="Capture HUD overlay"
                />
              }
            />
            <SettingRow
              label="KB-lint allow list"
              hint="Comma-separated --allow-missing-ref names for the build-time KB-lint gate. Backs a release-guard build tool — applies to builds run on this host."
              danger
              control={
                <Input type="text" placeholder="none"
                  value={draft.lint_kb_allow ?? ""}
                  onChange={(e) => setField("lint_kb_allow", e.target.value)}
                  className="w-48 text-right" />
              }
            />
          </SettingSection>

          {/* v3.66.711 (A-GUI Cut 3) -- the automation program's control surface.

              These 26 keys were READ by lifecycle_automation/automation_controller
              since they were introduced but never DECLARED in GLOBAL_CONFIG_SCHEMA, so
              a POST returned 200 and wrote NOTHING (fixed at 709). There has never been
              a GUI control for any of them -- including the emergency stop. The
              "Automation" NAV group is Templates/Pools/AI and is unrelated; this section
              is their first home.

              gui_exposure is DERIVED from whether the frontend references the key
              (config_surface_inventory, 710), so every key below must be spelled exactly
              as declared -- a typo does not fail loudly, it silently reports the control
              as missing. */}
          <SettingSection
            collapsible defaultOpen={false}
            label="Automation"
            description="Autonomous behaviour: what BD is allowed to do on its own. Everything here is default-OFF. The master off-switch dominates every other toggle -- when engaged, no autonomous action runs regardless of the switches below it."
          >
            {/* v3.66.723 (AF5): the nets report HERE. Before this, the 706 rehearsal
                verdict was persisted and read by nothing, and the 708 halt was handed to
                a scheduler wrapper that dropped it — so a guardrail could fire overnight
                and be invisible by morning. First thing in the section, above the
                switches, because you should not arm anything you cannot observe. */}
            <AutomationStatusPanel />

            <Callout tone="danger" title="The master off-switch is the emergency stop">
              Engaging it halts all autonomous action immediately, whatever the individual
              toggles say. It is safety-bearing and fails CLOSED: a malformed config
              engages it rather than leaving autonomy running. Turning it OFF (allowing
              autonomy) is the dangerous direction, and is confirm-gated.
            </Callout>
            <SettingRow
              danger
              modified={mod(KILL_KEY)}
              label="Master off-switch (EMERGENCY STOP)"
              hint="ON = all autonomous action is halted. This is the safe state."
              control={
                <Button
                  variant={draft[KILL_KEY] ? "default" : "ghost"}
                  onClick={() => setOffSwitchConfirm(!draft[KILL_KEY])}
                >
                  {draft[KILL_KEY] ? "ENGAGED - stand down" : "Engage emergency stop"}
                </Button>
              }
            />

            <SettingRow
              label="Restore rehearsal (safety net)"
              hint="Corrupt-a-backup drill: proves the restore path works, loudly, on a quiet day. Phase 1 of activation -- turn this on FIRST."
              control={
                <Switch
                  checked={!!draft["automation.restore_rehearsal_enabled"]}
                  onChange={(v) => setField("automation.restore_rehearsal_enabled", v)}
                />
              }
            />

            {/* L1-L3: observe and flag. No download is affected. */}
            {([
              ["automation.drift_sweep_enabled", "Drift sweep (L1)", "Scheduled template-drift scan. Read-only."],
              ["automation.validation_gate_enabled", "Validation gate (L2)", "Gate captures on drift findings."],
              ["automation.auto_flag_enabled", "Auto-flag needs-review (L3)", "Flag suspect items for a human. Does not act."],
            ] as const).map(([k, label, hint]) => (
              <SettingRow
                key={k}
                modified={mod(k)}
                label={label}
                hint={hint}
                control={
                  <Switch checked={!!draft[k]} onChange={(v) => setField(k, v)} />
                }
              />
            ))}

            {/* L4/L5 + A9: DOWNLOAD-AFFECTING. Safety-bearing; fail closed to disabled. */}
            {([
              ["automation.auto_quarantine_enabled", "Auto-quarantine (L4)", "Quarantines a site without asking. Download-affecting."],
              ["automation.disco_enabled", "Auto-discovery (L4 / A-DISCO)", "Enumerate -> triage -> auto-queue new targets. Download-affecting."],
              ["automation.auto_repair_enabled", "Auto-repair (L5)", "Rewrites selectors autonomously. Download-affecting."],
              ["automation.auto_refresh_enabled", "Auto-refresh templates (L5)", "Refreshes templates autonomously. Download-affecting."],
              ["automation.auto_promote_enabled", "Auto-promote candidates (A5)", "Promotes a clean candidate template without review."],
              ["automation.controller_enabled", "Autonomy controller (A9)", "Orchestrates the above. The off-switch dominates it."],
            ] as const).map(([k, label, hint]) => (
              <SettingRow
                key={k}
                danger
                modified={mod(k)}
                label={label}
                hint={hint}
                control={
                  <Switch checked={!!draft[k]} onChange={(v) => setField(k, v)} />
                }
              />
            ))}

            {/* Prep / restore / self-management: reversible, not keystone-required. */}
            {([
              ["automation.auto_onboard_enabled", "Auto-onboard (A4)", "Prepares new sites. Never enables them."],
              ["automation.auto_ci_enabled", "Auto-CI loop (A6)", "Snapshot + regression + rollback artifact. Restores known-good."],
              ["automation.auto_recover_enabled", "Self-recovery (A7)", "Cookie/profile/retry recovery. Restores known-good."],
              ["automation.auto_queue_enabled", "Queue self-management (A8)", "Dedup / prioritise / pause-resume. Reversible."],
              ["automation.auto_refresh_on_capture_enabled", "Refresh on capture", "Trigger mode for auto-refresh."],
              ["automation.auto_refresh_confirm_enabled", "Refresh needs confirm", "Require a human OK before a refresh applies."],
              ["automation.scrub_on_capture_enabled", "Scrub captures (redaction)", "Ships ON. Fails CLOSED -- a bad config keeps redacting."],
              ["automation.daily_digest_enabled", "Daily digest", "Emails the digest. Read-only."],
              ["automation.drift_repair_enabled", "Drift repair drafts", "Writes review-only repair drafts."],
              ["automation.template_canary_enabled", "Template canary", "Canary-tests a template before promotion."],
              ["automation.pipeline_enabled", "Autonomous pipeline (A-PIPE)", "Runs the checkpointed chain on a schedule. Gated on Phase 1 being PROVEN."],
            ] as const).map(([k, label, hint]) => (
              <SettingRow
                key={k}
                modified={mod(k)}
                label={label}
                hint={hint}
                control={
                  <Switch checked={!!draft[k]} onChange={(v) => setField(k, v)} />
                }
              />
            ))}

            {/* Ceilings (X-AUTO-2). 0 = uncapped = pre-707 behaviour. cycle_max_errors
                is the one that matters: before 707 a FAILING autonomous loop kept going. */}
            {([
              ["automation.cycle_max_errors", "Cycle ceiling: max errors", "0 = uncapped. Set this: a failing loop must HALT, not spin."],
              ["automation.cycle_max_steps", "Cycle ceiling: max steps", "0 = uncapped."],
              ["automation.cycle_wall_s", "Cycle ceiling: wall seconds", "0 = uncapped."],
              ["automation.auto_refresh_max_drift", "Max drift for auto-refresh", "Refuse an auto-refresh above this drift score."],
            ] as const).map(([k, label, hint]) => (
              <SettingRow
                key={k}
                modified={mod(k)}
                label={label}
                hint={hint}
                control={
                  <Input
                    type="number"
                    min={0}
                    value={Number(draft[k] ?? 0)}
                    onChange={(e) =>
                      setField(k, Math.max(0, parseInt(e.target.value, 10) || 0))
                    }
                    className="w-24 text-right tabular"
                  />
                }
              />
            ))}

            <SettingRow
              label="Scrub tool path override"
              hint="Absolute path to an alternative capture-scrub tool. Blank = the shipped tools/capture_scrub.py."
              control={
                <Input
                  value={String(draft["automation.scrub_on_capture_tool"] ?? "")}
                  onChange={(e) =>
                    setField("automation.scrub_on_capture_tool", e.target.value)
                  }
                  placeholder="(default)"
                />
              }
            />

            <DiscoRunPanel />
          </SettingSection>

          <SettingSection
            collapsible defaultOpen={false}
            label="Security & access"
            description="Authentication, the in-GUI dev surface, the cockpit shell, and path roots — promoted from their environment variables to live controls (store > env > default; a write takes effect without a restart). Every control here except Test mode can widen access or expose data: read each ⚠ disclaimer. Tokens are write-only — leave blank to keep the current value."
          >
            <Callout
              tone="danger"
              title="Danger zone"
              className="mb-3"
            >
              These settings control authentication and access scope. Changes
              take effect on save (no restart) and can widen access or expose
              data — review each disclaimer before changing one.
            </Callout>
            <SettingRow
              label="API auth token"
              stacked
              danger
              hint="⚠ The bearer token gating /api/*. Setting it here overrides the env token and enables auth. Leave blank to keep the current token; a blank field never clears or disables it. Clearing requires editing app_config.json / the env."
              control={
                <SecretField
                  placeholder={draft.auth_token === "<configured>" ? "•••••••• (set — blank keeps it)" : "set a token to enable auth"}
                  value={draft.auth_token === "<configured>" ? "" : (draft.auth_token ?? "")}
                  onChange={(v) => setField("auth_token", v)}
                  ariaLabel="API auth token"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="Secondary token (extension)"
              stacked
              danger
              hint="⚠ A second accepted bearer / X-BD-Token, in addition to the primary. Useful for the browser extension. Leave blank to keep the current value; blank = not accepted."
              control={
                <SecretField
                  placeholder={draft.bd_token === "<configured>" ? "•••••••• (set — blank keeps it)" : "optional second token"}
                  value={draft.bd_token === "<configured>" ? "" : (draft.bd_token ?? "")}
                  onChange={(v) => setField("bd_token", v)}
                  ariaLabel="Secondary token (extension)"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="Dev / test-runner surface"
              danger
              hint="⚠ Enables the in-GUI dev tools + test runner (spawns Python subprocesses). Default = on (the v3.47.7 default); Off disables it. The BD_DEV_MODE_DISABLE env kill-switch still wins. Widens the code-execution surface — trusted networks only."
              control={
                <select
                  value={draft.dev_mode ?? ""}
                  onChange={(e) => setField("dev_mode", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  <option value="">default (on)</option>
                  <option value="1">on</option>
                  <option value="0">off</option>
                </select>
              }
            />
            <SettingRow
              label="Cockpit shell"
              danger
              hint="⚠ Enables the cockpit PTY shell, which RUNS ARBITRARY COMMANDS on the host. Default = on (single-operator LAN). Off hard-disables it. Trusted networks only."
              control={
                <select
                  value={draft.cockpit_shell ?? ""}
                  onChange={(e) => setField("cockpit_shell", e.target.value)}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                >
                  <option value="">default (on)</option>
                  <option value="1">on</option>
                  <option value="0">off</option>
                </select>
              }
            />
            <SettingRow
              label="Autonomy final-apply switch"
              danger
              stacked
              hint="⚠ Arms autonomous Class-B state-changing actions — the system can then APPLY changes on its own (still gated by the kill switch + the Class-B policy level; arming never bypasses those). Applied autonomous changes cannot be auto-undone. Default = off; arming requires the explicit confirm below. Trusted single-operator network only."
              control={
                <div className="flex flex-col gap-1.5">
                  <select
                    value={draft.autonomy_enabled ?? ""}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (v === "1") {
                        // Don't arm directly — require the explicit confirm step.
                        setAutonomyArmConfirm(true);
                      } else {
                        setAutonomyArmConfirm(false);
                        setField("autonomy_enabled", v);
                      }
                    }}
                    className="hairline rounded-md bg-surface px-2 py-1.5 text-sm tabular"
                  >
                    <option value="">default (off)</option>
                    <option value="1">on (arm autonomous apply)</option>
                    <option value="0">off</option>
                  </select>
                  {autonomyArmConfirm && draft.autonomy_enabled !== "1" && (
                    <div className="flex items-center gap-2 text-xs text-danger">
                      <span>Arm autonomous apply? This is irreversible once applied.</span>
                      <Button
                        size="sm"
                        onClick={() => {
                          setField("autonomy_enabled", "1");
                          setAutonomyArmConfirm(false);
                        }}
                      >
                        Confirm arm
                      </Button>
                      <button
                        type="button"
                        className="underline"
                        onClick={() => setAutonomyArmConfirm(false)}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>
              }
            />
            <SettingRow
              label="Cockpit tasks root"
              stacked
              danger
              hint="⚠ Filesystem root the cockpit tasks dashboard reads/writes. Honored as-written (no jail) — repointing it can orphan data or expose other directories. Blank = env / default (./cockpit_tasks)."
              control={
                <Input
                  type="text"
                  placeholder="./cockpit_tasks"
                  value={draft.cockpit_tasks ?? ""}
                  onChange={(e) => setField("cockpit_tasks", e.target.value)}
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="Framework reports root"
              stacked
              danger
              hint="⚠ Filesystem root the framework reports dashboard reads. Honored as-written (no jail) — pointing it at a sensitive directory exposes it through the dashboard. Blank = env / default (./framework_reports)."
              control={
                <Input
                  type="text"
                  placeholder="./framework_reports"
                  value={draft.framework_reports ?? ""}
                  onChange={(e) => setField("framework_reports", e.target.value)}
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="Test mode (advisory)"
              hint="A visible TEST-MODE indicator surfaced in /api/health. No security or behavior effect — purely a marker for test/diagnostic boots."
              control={
                <Switch
                  checked={!!draft.test_mode}
                  onChange={(v) => setField("test_mode", v)}
                  ariaLabel="Test mode"
                />
              }
            />

            {/* v3.66.720 (Cut 9): OIDC / SSO. The last of the open-parity keys, wired.
                All global_config; the client secret is write-only (blank keeps it). */}
            <SettingRow
              label="OIDC single sign-on"
              danger
              hint="⚠ Enables OpenID Connect SSO. Requires a configured issuer, client id, and client secret below. Off = local auth only."
              control={
                <Switch
                  checked={!!draft.oidc_enabled}
                  onChange={(v) => setField("oidc_enabled", v)}
                  ariaLabel="OIDC single sign-on"
                />
              }
            />
            <SettingRow
              label="OIDC issuer"
              stacked
              hint="The identity provider's issuer URL (e.g. https://accounts.example.com). Its /.well-known/openid-configuration must resolve."
              control={
                <Input
                  value={draft.oidc_issuer ?? ""}
                  onChange={(e) => setField("oidc_issuer", e.target.value)}
                  placeholder="https://issuer.example.com"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="OIDC client id"
              stacked
              control={
                <Input
                  value={draft.oidc_client_id ?? ""}
                  onChange={(e) => setField("oidc_client_id", e.target.value)}
                  placeholder="client id"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="OIDC client secret"
              stacked
              danger
              hint="⚠ Write-only. Leave blank to keep the current secret; a blank field never clears it."
              control={
                <SecretField
                  placeholder={draft.oidc_client_secret === "<configured>" ? "•••••••• (set — blank keeps it)" : "client secret"}
                  value={draft.oidc_client_secret === "<configured>" ? "" : (draft.oidc_client_secret ?? "")}
                  onChange={(v) => setField("oidc_client_secret", v)}
                  ariaLabel="OIDC client secret"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="OIDC redirect URI"
              stacked
              hint="Where the IdP returns after auth. Must match a redirect URI registered with the provider (e.g. https://your-host/auth/callback)."
              control={
                <Input
                  value={draft.oidc_redirect_uri ?? ""}
                  onChange={(e) => setField("oidc_redirect_uri", e.target.value)}
                  placeholder="https://your-host/auth/callback"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="OIDC scopes"
              stacked
              hint="Space-separated scopes requested from the IdP. Default: openid email profile."
              control={
                <Input
                  value={draft.oidc_scopes ?? ""}
                  onChange={(e) => setField("oidc_scopes", e.target.value)}
                  placeholder="openid email profile"
                  className="w-full"
                />
              }
            />
            <SettingRow
              label="Egress isolation (netns / wg0)"
              hint="Confine the takeover + extractor browsers to a network namespace whose only route is the VPN, fail-closed (a broken tunnel blocks egress rather than leaking). Off by default; needs iproute2/nftables + CAP_NET_ADMIN on the host. The advanced {egress:{wg_iface,...}} form is file-managed."
              control={
                <Switch
                  checked={!!draft.netns_isolation}
                  onChange={(v) => setField("netns_isolation", v)}
                  ariaLabel="Egress isolation (netns / wg0 fail-closed)"
                />
              }
            />
          </SettingSection>

          <EnvEffectivePanel />

          <EnvironmentSettings />

          <StoreRawSettings />

          <SettingSection
            collapsible defaultOpen={false}
            label="Import / Export"
            description="Move site configs between hosts."
          >
            <ConfigImportExport />
          </SettingSection>

          <StickySaveBar
            changedCount={changedKeys.length}
            onSave={handleSave}
            onDiscard={discard}
            saving={saveMut.isPending}
          />
        </div>
        </SettingsSearchContext.Provider>
      )}
      {/* v3.66.711: the kill switch's second step. Engaging it is SAFE (autonomy
          halts); standing it down is the dangerous direction, so the copy changes
          with the direction rather than showing one generic "are you sure". */}
      <Dialog
        open={offSwitchConfirm !== null}
        onOpenChange={(o) => !o && setOffSwitchConfirm(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {offSwitchConfirm
                ? "Engage the emergency stop?"
                : "Stand down the emergency stop?"}
            </DialogTitle>
            <DialogDescription>
              {offSwitchConfirm
                ? "All autonomous action halts immediately, regardless of the individual toggles. This is the safe state. Nothing else needs to be turned off."
                : "This ALLOWS autonomous action to run again, subject to the individual toggles below it. Only stand down once the safety net is proven: restore rehearsal ON and a cycle error ceiling set."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOffSwitchConfirm(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                setField(KILL_KEY, offSwitchConfirm as boolean);
                setOffSwitchConfirm(null);
              }}
            >
              {offSwitchConfirm ? "Engage emergency stop" : "Stand down"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={supConfirm} onOpenChange={(o) => !o && setSupConfirm(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply supervisor throttle</DialogTitle>
            <DialogDescription>
              Hot-reload the byte-rate limiter:{" "}
              {supEnabled ? "enabled" : "disabled"}, global{" "}
              {Math.max(0, parseInt(supGlobalBps, 10) || 0)} B/s,{" "}
              {Object.keys(supPerSiteParsed).length} per-site override(s). This
              takes effect immediately for in-flight downloads.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSupConfirm(false)}>
              Cancel
            </Button>
            <Button
              disabled={supMut.isPending}
              onClick={() => {
                setSupConfirm(false);
                supMut.mutate();
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}

// v3.66.312 (Phase 4.6): read-only panel for deploy/path + import-time-bound env
// vars. These are set before the process starts or bound once at import, so there is
// no live write control — only the effective value + how to override (set env, restart).
interface EnvRow {
  name: string;
  effective: string | null;
  default: string;
  kind: string;
  override: string;
}
interface EnvEffectiveResp {
  ok: boolean;
  env: EnvRow[];
  count: number;
  note: string;
}
function EnvEffectivePanel() {
  const { data } = useQuery<EnvEffectiveResp>({
    queryKey: ["env-effective"],
    queryFn: ({ signal }) =>
      apiGet<EnvEffectiveResp>("/api/settings/env/effective", signal),
    refetchOnWindowFocus: false,
  });
  return (
    <SettingSection
      collapsible defaultOpen={false}
      label="Environment (effective, read-only)"
      description="Deploy/path and import-time-bound env vars. No live control — set the env var and restart to change."
    >
      <div className="overflow-x-auto">
        <table className="bd-table w-full text-sm">
          <thead>
            <tr className="text-left text-ink-2">
              <th className="py-1 pr-3 font-medium">Variable</th>
              <th className="py-1 pr-3 font-medium">Effective</th>
              <th className="py-1 pr-3 font-medium">Kind</th>
            </tr>
          </thead>
          <tbody>
            {(data?.env ?? []).map((r) => (
              <tr key={r.name} className="hairline-t">
                <td className="py-1 pr-3 font-mono text-xs">{r.name}</td>
                <td className="py-1 pr-3 tabular">
                  {r.effective == null || r.effective === "" ? (
                    <span className="text-ink-2">— (unset)</span>
                  ) : (
                    <span>{r.effective}</span>
                  )}
                </td>
                <td className="py-1 pr-3 text-ink-2">{r.kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </SettingSection>
  );
}

// Tiny inline Switch — used by SettingRow controls above. Not in
// shadcn/ui pre-stage because we only need one. If multiple
// switch-shaped controls appear later, promote it to /components/ui/.
interface SwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  ariaLabel?: string;
  disabled?: boolean;
}

function Switch({ checked, onChange, ariaLabel, disabled = false }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-surface-2",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
  );
}
