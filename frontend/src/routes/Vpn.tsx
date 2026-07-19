import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
import { DangerZone } from "@/components/ui/DangerZone";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { StatusPill, type PillTone } from "@/components/StatusPill";
import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api-client";
import { isVpnSecretKey } from "@/lib/secretKeys.generated";
import type {
  VpnAutoBlacklistResult,
  VpnBackendsAvailability,
  VpnBestForResult,
  VpnBlacklistResult,
  VpnCredTestResult,
  VpnGlobalSettings,
  VpnKillSwitchState,
  VpnLeakHistoryResult,
  VpnLeakResult,
  VpnLocationsResult,
  VpnProvidersResult,
  VpnSettingsResult,
  VpnStatsResult,
  VpnSysKsAvailResult,
  VpnTunnelDetailResult,
} from "@/lib/api-types";

// GUI parity (T15) — VPN control surface.
//
// The SPA had no VPN surface at all (only a comment in Advanced.tsx noting
// VPN was deliberately omitted). This route renders the operator-facing
// status overview and wires the four bodyless, posture-sensitive control
// writes — each individually gated, never bundled (AUTOMATION_POLICY: the
// safety gate wins):
//   * POST   /api/vpn/tunnels/<id>/stop   — take a tunnel down. Recoverable;
//     light single-confirm. Lowest blast radius, wired first.
//   * POST   /api/vpn/tunnels/<id>/start  — bring a tunnel up. CHANGES network
//     posture (routes traffic through it); confirm flags the routing change.
//   * POST   /api/vpn/tunnels/<id>/cycle  — rotate a tunnel (new exit). CHANGES
//     posture (exit IP changes); confirm flags it.
//   * DELETE  /api/vpn/tunnels/<id>       — remove a tunnel permanently.
//     Destructive; yes/no confirm (No default) DELETE TUNNEL.
//
// All four backend routes are pre-existing audited routes — nothing fires on a
// single click; every action passes through a confirm dialog. start/stop/cycle
// return HTTP 200 with an action boolean (started/stopped/cycled) that can be
// false, so the toast reads that boolean rather than assuming success.
//
// Create / update (the two body-carrying config writes) are now wired via a
// tunnel-editor form — the last Phase-B VPN writes:
//   * POST  /api/vpn/tunnels          — register a tunnel. name/provider/backend
//     required; optional location + config k/v. Posture-neutral (does not start).
//   * PUT   /api/vpn/tunnels/<id>      — edit name/location and MERGE config.
//     provider/backend are immutable post-create (backend only sets name/location
//     + merges config), so the edit form renders them read-only. PUT (not PATCH)
//     because apiPut already exists; the route accepts both.
//
// Secrets discipline: tunnel.to_dict() redacts config server-side (secret-named
// keys → "***"), so the editor only ever receives "***" for a stored secret,
// never the real value. Secret-named config fields render write-only: on edit a
// blank secret field is omitted from the body (skipped, so the redaction marker
// can never round-trip back into storage), leaving the existing secret intact.
// No credential/token value is ever displayed.

// UI option lists. Source of truth is the backend (vpn.VALID_PROVIDERS /
// VALID_BACKENDS); these mirror it for the create-form selects. If the backend
// adds a provider/backend the form just won't offer it until updated — and the
// backend still validates, returning a clear error, so a stale list can't create
// an invalid tunnel.
const PROVIDERS = ["mullvad", "proton", "pia", "generic", "selfhosted"] as const;
const BACKENDS = ["wireguard", "openvpn"] as const;

// REDACT-SOT Cut 3 (D3): the secret-key predicate is sourced from the server SoT
// via generated constants (tools/gen_frontend_secret_keys.py) so the GUI can
// never drift from vpn_config's server-side masking. A config key classified
// secret is rendered as a write-only password field, and the server returns
// "***" for its value (never the real one).
const REDACTED = "***";
function isSecretKey(k: string): boolean {
  return isVpnSecretKey(k);
}

// A single editable config key/value row. `wasRedacted` marks a row whose value
// arrived from the server as "***" (a stored secret) — on edit, if such a row is
// left blank we omit it from the body so the existing secret is preserved and the
// "***" marker never round-trips into storage.
interface ConfigRow {
  key: string;
  value: string;
  wasRedacted: boolean;
}
function emptyRow(): ConfigRow {
  return { key: "", value: "", wasRedacted: false };
}
// Build editor rows from a tunnel's (redacted) config object.
function rowsFromConfig(cfg: Record<string, unknown> | undefined): ConfigRow[] {
  if (!cfg || typeof cfg !== "object") return [];
  return Object.entries(cfg).map(([key, v]) => {
    const redacted = v === REDACTED;
    return {
      key,
      value: redacted ? "" : v == null ? "" : String(v),
      wasRedacted: redacted,
    };
  });
}
// Collapse editor rows into a config object for the request body.
//   - rows with an empty key are dropped
//   - a redacted-secret row left blank is dropped (keep existing secret)
// Returns undefined when there's nothing to send.
function configFromRows(rows: ConfigRow[]): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (!k) continue;
    if (r.wasRedacted && r.value === "") continue; // leave the stored secret intact
    out[k] = r.value;
  }
  return Object.keys(out).length ? out : undefined;
}

type ControlAction = "stop" | "start" | "cycle";
interface ControlTarget {
  tunnelId: string;
  name: string;
  action: ControlAction;
}
interface DeleteTarget {
  tunnelId: string;
  name: string;
}

interface Tunnel {
  tunnel_id: string;
  name: string;
  provider: string;
  backend: string;
  location?: string | null;
  socks_port?: number;
  state?: string; // down | starting | up | failing | stopping
  started_at?: number | null;
  last_health_check?: number | null;
  health_ok?: boolean;
  failure_count?: number;
  public_ip?: string | null;
  last_error?: string | null;
  config?: Record<string, unknown>; // redacted server-side; secrets arrive as "***"
}
interface KillState {
  tunnel_id: string;
  killed_at?: number;
  reason?: string;
  state?: string; // killed | cycling | cleared
  cycle_attempts?: number;
  last_leak_summary?: string;
}
interface Provider {
  id: string;
  name: string;
  supported_backends?: string[];
}
interface SystemKillActive {
  tunnel_id: string;
  vpn_endpoint?: string;
  rules?: number;
}
interface VpnStatus {
  ok?: boolean;
  tunnels?: Tunnel[];
  kill_states?: KillState[];
  providers?: Provider[];
  system_killswitch_available?: boolean;
  system_killswitch_reason?: string;
  system_killswitch_active?: SystemKillActive[];
  error?: string;
}

// Map a tunnel state to a StatusPill tone.
function stateTone(state?: string): PillTone {
  switch (state) {
    case "up":
      return "green";
    case "failing":
      return "red";
    case "starting":
    case "stopping":
      return "amber";
    default:
      return "neutral"; // down / unknown
  }
}

function killTone(state?: string): PillTone {
  switch (state) {
    case "killed":
      return "red";
    case "cycling":
      return "amber";
    case "cleared":
      return "green";
    default:
      return "neutral";
  }
}

// Reusable key/value config editor shared by the create + edit dialogs.
// Secret-named keys render as password (write-only) inputs. A redacted row
// (value came back as "***") locks its key and shows a "blank to keep" hint.
function ConfigEditor({
  rows,
  setRows,
  disabled,
}: {
  rows: ConfigRow[];
  setRows: (r: ConfigRow[]) => void;
  disabled?: boolean;
}) {
  const update = (i: number, patch: Partial<ConfigRow>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-ink-3">config (optional)</div>
      {rows.length === 0 && (
        <p className="text-[11px] text-ink-3">
          No config keys. Add backend-specific keys (e.g. wireguard{" "}
          <code className="rounded bg-muted px-1">private_key</code>,{" "}
          <code className="rounded bg-muted px-1">endpoint</code>). Secret-named keys are
          write-only.
        </p>
      )}
      {rows.map((r, i) => {
        const secret = isSecretKey(r.key);
        return (
          <div key={i} className="flex items-center gap-1">
            <Input
              className="text-xs"
              placeholder="key"
              value={r.key}
              disabled={disabled || r.wasRedacted}
              onChange={(e) => update(i, { key: e.target.value })}
            />
            <Input
              className="text-xs"
              type={secret ? "password" : "text"}
              placeholder={
                r.wasRedacted
                  ? "•••• set — blank to keep"
                  : secret
                    ? "secret (write-only)"
                    : "value"
              }
              value={r.value}
              disabled={disabled}
              onChange={(e) => update(i, { value: e.target.value })}
            />
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => setRows(rows.filter((_, idx) => idx !== i))}
            >
              ✕
            </Button>
          </div>
        );
      })}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={disabled}
        onClick={() => setRows([...rows, emptyRow()])}
      >
        + Add key
      </Button>
    </div>
  );
}

export function Vpn() {
  const qc = useQueryClient();

  // v3.66.714 (A-GUI Cut 5): the system kill switch had NO CONTROL. This page already
  // RENDERED its state (system_killswitch_available / _active / _reason) and called none
  // of its four endpoints -- plan / apply / commit / revert. A safety control you can
  // watch and cannot touch is not a control. It looked "wired" to a naive reachability
  // scan only because a CLI tool posts to it; a CLI caller is not an operator surface.
  //
  // plan is a DRY RUN (it computes the rule set and changes nothing), so it needs no
  // confirm. apply / commit / revert change system-level network state -> confirm-gated.
  const [sysKillPlan, setSysKillPlan] = useState<string | null>(null);
  const [sysKillConfirm, setSysKillConfirm] = useState<
    { tid: string; verb: "apply" | "commit" | "revert" } | null
  >(null);
  // The endpoint is spelled out PER VERB rather than interpolated
  // (`.../${verb}`). A templated verb is invisible to any static reachability scan:
  // the string "/commit" never appears, so the ledger cannot prove a control reaches
  // it -- and a control whose target is not legible is one nobody can audit. Same
  // reason the automation KILL_KEY is spelled exactly once, literally.
  const sysKillMut = useMutation<unknown, Error, { tid: string; verb: string }>({
    mutationFn: (v) => {
      const id = encodeURIComponent(v.tid);
      const url =
        v.verb === "plan"
          ? `/api/vpn/system_killswitch/${id}/plan`
          : v.verb === "apply"
            ? `/api/vpn/system_killswitch/${id}/apply`
            : v.verb === "commit"
              ? `/api/vpn/system_killswitch/${id}/commit`
              : `/api/vpn/system_killswitch/${id}/revert`;
      return apiPost(url, {});
    },
    onSuccess: (data, v) => {
      if (v.verb === "plan") {
        setSysKillPlan(JSON.stringify(data, null, 2));
        toast.success("Kill-switch plan computed (nothing applied)");
      } else {
        setSysKillPlan(null);
        toast.success(`System kill switch: ${v.verb} ok`);
      }
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
    },
    onError: (e) => toast.error(`Kill switch failed: ${e.message}`),
  });

  const statusQ = useQuery<VpnStatus>({
    queryKey: ["vpn-status"],
    queryFn: ({ signal }) => apiGet<VpnStatus>("/api/vpn/status", signal),
    refetchInterval: 5000, // live posture surface — keep state pills current
  });

  // ── start / stop / cycle (bodyless POSTs; light confirm) ───────────
  // All three return HTTP 200 with a per-action boolean that can be false;
  // the toast reads that boolean rather than assuming success.
  const [controlTarget, setControlTarget] = useState<ControlTarget | null>(null);
  const controlMut = useMutation<
    { ok?: boolean; error?: string; started?: boolean; stopped?: boolean; cycled?: boolean },
    Error,
    ControlTarget
  >({
    mutationFn: (t) =>
      apiPost(`/api/vpn/tunnels/${encodeURIComponent(t.tunnelId)}/${t.action}`, {}),
    onSuccess: (r, t) => {
      if (r.ok === false) {
        toast.error(r.error || `${t.action} failed`);
      } else if (t.action === "stop") {
        if (r.stopped) toast.success(`Stopped ${t.name}`);
        else toast.message(`${t.name} was not running`);
      } else if (t.action === "start") {
        if (r.started) toast.success(`Started ${t.name} — traffic now routes through it`);
        else toast.error(`${t.name} did not come up — check last error`);
      } else {
        if (r.cycled) toast.success(`Cycled ${t.name} — new exit`);
        else toast.error(`${t.name} did not cycle`);
      }
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── delete (destructive → yes/no confirm, No default) ──────────────
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const deleteMut = useMutation<{ ok?: boolean; error?: string; removed?: string }, Error, DeleteTarget>({
    mutationFn: (t) => apiDelete(`/api/vpn/tunnels/${encodeURIComponent(t.tunnelId)}`),
    onSuccess: (r, t) => {
      if (r.ok === false) toast.error(r.error || "delete failed");
      else toast.success(`Deleted tunnel ${t.name}`);
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── create (POST /api/vpn/tunnels — register a tunnel; posture-neutral) ─────
  const [createOpen, setCreateOpen] = useState(false);
  const [cName, setCName] = useState("");
  const [cProvider, setCProvider] = useState<string>(PROVIDERS[0]);
  const [cBackend, setCBackend] = useState<string>(BACKENDS[0]);
  const [cLocation, setCLocation] = useState("");
  const [cRows, setCRows] = useState<ConfigRow[]>([]);
  function resetCreate() {
    setCName("");
    setCProvider(PROVIDERS[0]);
    setCBackend(BACKENDS[0]);
    setCLocation("");
    setCRows([]);
  }
  const createMut = useMutation<
    { ok?: boolean; error?: string; tunnel_id?: string },
    Error,
    {
      name: string;
      provider: string;
      backend: string;
      location?: string;
      config?: Record<string, string>;
    }
  >({
    mutationFn: (body) => apiPost("/api/vpn/tunnels", body),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "create failed");
        return;
      }
      toast.success(`Created tunnel ${cName}`);
      setCreateOpen(false);
      resetCreate();
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── edit (PUT /api/vpn/tunnels/<id> — name/location + MERGE config) ─────────
  // provider/backend are immutable post-create (the backend update handler only
  // sets name/location and merges config), so the form shows them read-only.
  // config is MERGED server-side: this can add or overwrite keys, not remove them.
  const [editTarget, setEditTarget] = useState<Tunnel | null>(null);
  const [eName, setEName] = useState("");
  const [eLocation, setELocation] = useState("");
  const [eRows, setERows] = useState<ConfigRow[]>([]);
  function openEdit(t: Tunnel) {
    setEditTarget(t);
    setEName(t.name || "");
    setELocation(t.location || "");
    setERows(rowsFromConfig(t.config));
  }
  const updateMut = useMutation<
    { ok?: boolean; error?: string; tunnel?: Tunnel },
    Error,
    { tunnelId: string; name: string; location: string; config?: Record<string, string> }
  >({
    mutationFn: ({ tunnelId, ...body }) =>
      apiPut(`/api/vpn/tunnels/${encodeURIComponent(tunnelId)}`, body),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "update failed");
        return;
      }
      toast.success(`Updated ${eName}`);
      setEditTarget(null);
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
    },
    onError: (e) => toast.error(e.message),
  });

  // ── VPN row U (v3.66.208): kill switch · global settings · provider
  //    credential check. Gating: kill CLEAR re-enables traffic on a tunnel
  //    the kill switch deliberately blocked → destructive yes/no confirm, No default ("CLEAR KILL");
  //    trigger (blocks traffic — safety-positive but disruptive),
  //    auto-recover toggle and settings save are one-step confirm. The
  //    provider credential check is a server-side read (nothing persists);
  //    credential inputs are write-only password fields and the response
  //    carries only valid/message — no value ever echoes. ──────────────
  const [ksClearTarget, setKsClearTarget] = useState<string | null>(null);
  const [ksTriggerTarget, setKsTriggerTarget] = useState<string | null>(null);
  const [ksTriggerConfirm, setKsTriggerConfirm] = useState(false);
  const [autoRecoverConfirm, setAutoRecoverConfirm] = useState(false);
  const killClearMut = useMutation<{ ok?: boolean; error?: string }, Error, string>({
    mutationFn: (tid) => apiPost(`/api/vpn/kill_switch/${encodeURIComponent(tid)}/clear`, {}),
    onSuccess: (r, tid) => {
      if (r.ok === false) toast.error(r.error || "clear failed");
      else toast.success(`Kill switch cleared on ${tid} — traffic can flow again`);
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
      qc.invalidateQueries({ queryKey: ["vpn-kill-state"] });
    },
    onError: (e) => toast.error(e.message),
  });
  const killTriggerMut = useMutation<{ ok?: boolean; error?: string }, Error, string>({
    mutationFn: (tid) =>
      apiPost(`/api/vpn/kill_switch/${encodeURIComponent(tid)}/trigger`, {
        reason: "manual trigger via SPA",
      }),
    onSuccess: (r, tid) => {
      if (r.ok === false) toast.error(r.error || "trigger failed");
      else toast.success(`Kill switch triggered on ${tid} — traffic blocked`);
      qc.invalidateQueries({ queryKey: ["vpn-status"] });
      qc.invalidateQueries({ queryKey: ["vpn-kill-state"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const killStateQ = useQuery<VpnKillSwitchState>({
    queryKey: ["vpn-kill-state"],
    queryFn: ({ signal }) => apiGet<VpnKillSwitchState>("/api/vpn/kill_switch/state", signal),
    refetchInterval: 10000,
  });
  const autoRecoverMut = useMutation<VpnKillSwitchState, Error, boolean>({
    mutationFn: (enabled) => apiPut("/api/vpn/kill_switch/auto_recover", { enabled }),
    onSuccess: (r) => {
      if (r.ok === false) toast.error(r.error || "update failed");
      else toast.success(`Auto-recover ${r.auto_recover ? "enabled" : "disabled"}`);
      qc.invalidateQueries({ queryKey: ["vpn-kill-state"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const settingsQ = useQuery<VpnSettingsResult>({
    queryKey: ["vpn-settings"],
    queryFn: ({ signal }) => apiGet<VpnSettingsResult>("/api/vpn/settings", signal),
  });
  const [setLeakMin, setSetLeakMin] = useState<string | null>(null);
  const [setSysDefault, setSetSysDefault] = useState<boolean | null>(null);
  const [settingsConfirm, setSettingsConfirm] = useState(false);
  const settingsMut = useMutation<VpnSettingsResult, Error, VpnGlobalSettings>({
    mutationFn: (body) => apiPut("/api/vpn/settings", body),
    onSuccess: (r) => {
      if (r.ok === false) toast.error(r.error || "save failed");
      else toast.success("VPN settings saved");
      qc.invalidateQueries({ queryKey: ["vpn-settings"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const providersQ = useQuery<VpnProvidersResult>({
    queryKey: ["vpn-providers"],
    queryFn: ({ signal }) => apiGet<VpnProvidersResult>("/api/vpn/providers", signal),
  });
  const [credProvider, setCredProvider] = useState("");
  const [credRows, setCredRows] = useState<ConfigRow[]>([]);
  const [credResult, setCredResult] = useState<string | null>(null);

  // ── VPN diagnostics (v3.66.768, 6A dark-cluster wiring) ────────────────────
  // Read-only health/blacklist surface + two explicit actions (best-for lookup,
  // auto-blacklist recompute). All FULL /api/vpn/... literals so the parity
  // scanner credits them spa_wired.
  const [bestForSid, setBestForSid] = useState("");
  const [bestForResult, setBestForResult] = useState<string | null>(null);

  const statsQ = useQuery<VpnStatsResult>({
    queryKey: ["vpn-stats"],
    queryFn: ({ signal }) => apiGet<VpnStatsResult>("/api/vpn/stats", signal),
  });
  const blacklistQ = useQuery<VpnBlacklistResult>({
    queryKey: ["vpn-blacklist"],
    queryFn: ({ signal }) => apiGet<VpnBlacklistResult>("/api/vpn/blacklist", signal),
  });
  const backendsQ = useQuery<VpnBackendsAvailability>({
    queryKey: ["vpn-backends-availability"],
    queryFn: ({ signal }) =>
      apiGet<VpnBackendsAvailability>("/api/vpn/backends/availability", signal),
  });
  const sysKsAvailQ = useQuery<VpnSysKsAvailResult>({
    queryKey: ["vpn-sysks-available"],
    queryFn: ({ signal }) =>
      apiGet<VpnSysKsAvailResult>("/api/vpn/system_killswitch/available", signal),
  });

  const bestForMut = useMutation<VpnBestForResult, Error, void>({
    mutationFn: () =>
      apiGet<VpnBestForResult>(
        `/api/vpn/best_for/${encodeURIComponent(bestForSid.trim())}`,
      ),
    onSuccess: (res) => {
      setBestForResult(
        res.vpn_profile ? `Best profile: ${res.vpn_profile}` : "No profile recommendation",
      );
    },
    onError: (e) => setBestForResult(e.message || "Lookup failed"),
  });

  const autoBlacklistMut = useMutation<VpnAutoBlacklistResult, Error, void>({
    mutationFn: () => apiPost<VpnAutoBlacklistResult>("/api/vpn/auto_blacklist", {}),
    onSuccess: (res) => {
      const n = (res.new_blacklisted ?? []).length;
      toast.success(
        n === 0 ? "No new profiles blacklisted" : `${n} profile${n === 1 ? "" : "s"} blacklisted`,
      );
      qc.invalidateQueries({ queryKey: ["vpn-blacklist"] });
    },
    onError: (e) => toast.error(e.message || "Auto-blacklist failed"),
  });

  // ── VPN leak tests (v3.66.769, 6B dark-cluster wiring) ─────────────────────
  // Per-tunnel leak-probe surface: pick a tunnel, view its detail + latest/
  // history leak results, and run the probes on demand. Templated FULL literals
  // (`/api/vpn/tunnels/${id}/...`) so the scanner credits them spa_wired.
  const [leakTunnelId, setLeakTunnelId] = useState("");

  const leakDetailQ = useQuery<VpnTunnelDetailResult>({
    queryKey: ["vpn-tunnel-detail", leakTunnelId],
    queryFn: ({ signal }) =>
      apiGet<VpnTunnelDetailResult>(
        `/api/vpn/tunnels/${encodeURIComponent(leakTunnelId)}`,
        signal,
      ),
    enabled: !!leakTunnelId,
  });
  const leakLatestQ = useQuery<VpnLeakResult>({
    queryKey: ["vpn-leak-latest", leakTunnelId],
    queryFn: ({ signal }) =>
      apiGet<VpnLeakResult>(
        `/api/vpn/tunnels/${encodeURIComponent(leakTunnelId)}/leak_test/latest`,
        signal,
      ),
    enabled: !!leakTunnelId,
  });
  const leakHistoryQ = useQuery<VpnLeakHistoryResult>({
    queryKey: ["vpn-leak-history", leakTunnelId],
    queryFn: ({ signal }) =>
      apiGet<VpnLeakHistoryResult>(
        `/api/vpn/tunnels/${encodeURIComponent(leakTunnelId)}/leak_test/history`,
        signal,
      ),
    enabled: !!leakTunnelId,
  });
  const leakRunMut = useMutation<VpnLeakResult, Error, void>({
    mutationFn: () =>
      apiPost<VpnLeakResult>(
        `/api/vpn/tunnels/${encodeURIComponent(leakTunnelId)}/leak_test/run`,
        {},
      ),
    onSuccess: () => {
      toast.success("Leak test complete");
      qc.invalidateQueries({ queryKey: ["vpn-leak-latest", leakTunnelId] });
      qc.invalidateQueries({ queryKey: ["vpn-leak-history", leakTunnelId] });
    },
    onError: (e) => toast.error(e.message || "Leak test failed"),
  });
  const credTestMut = useMutation<VpnCredTestResult, Error, void>({
    mutationFn: () =>
      apiPost(
        `/api/vpn/providers/${encodeURIComponent(credProvider)}/test_credentials`,
        configFromRows(credRows) ?? {},
      ),
    onSuccess: (r) =>
      setCredResult(r.valid ? `✓ valid — ${r.message || ""}` : `✗ ${r.message || r.error || "invalid"}`),
    onError: (e) => setCredResult(`✗ ${e.message}`),
  });
  const credLocMut = useMutation<VpnLocationsResult, Error, void>({
    mutationFn: () =>
      apiPost(
        `/api/vpn/providers/${encodeURIComponent(credProvider)}/locations`,
        configFromRows(credRows) ?? {},
      ),
    onSuccess: (r) => {
      const names = (r.locations ?? []).map((l) =>
        typeof l === "string" ? l : String((l as Record<string, unknown>).id ?? (l as Record<string, unknown>).name ?? "?"),
      );
      setCredResult(names.length ? `locations: ${names.slice(0, 30).join(", ")}` : "no locations returned");
    },
    onError: (e) => setCredResult(`✗ ${e.message}`),
  });

  const busy =
    controlMut.isPending ||
    deleteMut.isPending ||
    createMut.isPending ||
    updateMut.isPending ||
    killClearMut.isPending ||
    killTriggerMut.isPending ||
    autoRecoverMut.isPending ||
    settingsMut.isPending;

  const tunnels = statusQ.data?.tunnels ?? [];
  const killStates = statusQ.data?.kill_states ?? [];
  const providers = statusQ.data?.providers ?? [];
  const sysActive = statusQ.data?.system_killswitch_active ?? [];
  const sysAvail = statusQ.data?.system_killswitch_available;

  return (
    <AppShell title="VPN" subtitle="Tunnel status · create · edit · control · kill switch">
      <WorkflowPage
        purpose={<>
      <GatedWriteBanner title="Operator control surface" className="mb-3">
        <strong>New tunnel</strong> / <strong>Edit</strong> are
        posture-neutral (they register or amend config, they don't start anything). Tunnel
        controls change VPN posture: <strong>stop</strong> is recoverable (single confirm);
        <strong>start</strong> and <strong>cycle</strong> alter routing / exit IP (confirm flags
        it); <strong>delete</strong> is permanent (yes/no confirm, No default). Nothing fires on a single
        click. Secret config values are write-only and never displayed.
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mb-3">
        Register, edit, and control VPN tunnels, and arm the kill switch. New
        tunnel and Edit only register or amend config — they don't start
        anything. Start, cycle, stop, and delete change live VPN posture and
        each takes a confirmation; secret config values are write-only.
      </Callout>
        </>}
        inputs={<>
      {/* Tunnels */}
      <Card className="mb-3 p-4">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="section-head">Tunnels</h2>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => {
              resetCreate();
              setCreateOpen(true);
            }}
          >
            New tunnel
          </Button>
        </div>
        <p className="mb-2 text-xs text-ink-3">
          Registered VPN tunnels and their live state. Reads <code>/api/vpn/status</code>.
        </p>
        {statusQ.isLoading && <p className="text-xs text-ink-3">Loading status…</p>}
        {statusQ.isError && <p className="text-xs text-destructive">Failed to load VPN status.</p>}
        {!statusQ.isLoading && tunnels.length === 0 && (
          <p className="text-xs text-ink-3">No tunnels registered.</p>
        )}
        {tunnels.length > 0 && (
          <div className="max-h-96 overflow-auto rounded border border-border">
            <table className="bd-table w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="px-2 py-1">name</th>
                  <th className="px-2 py-1">provider / backend</th>
                  <th className="px-2 py-1">location</th>
                  <th className="px-2 py-1">state</th>
                  <th className="px-2 py-1">health</th>
                  <th className="px-2 py-1">socks</th>
                  <th className="px-2 py-1">public IP</th>
                  <th className="px-2 py-1">controls</th>
                </tr>
              </thead>
              <tbody>
                {tunnels.map((t) => (
                  <tr key={t.tunnel_id} className="border-t border-border align-top">
                    <td className="break-all px-2 py-1">
                      <div className="font-medium">{t.name || t.tunnel_id}</div>
                      {t.last_error && (
                        <div className="text-[11px] text-destructive">{t.last_error}</div>
                      )}
                    </td>
                    <td className="px-2 py-1">
                      {t.provider || "?"} / {t.backend || "?"}
                    </td>
                    <td className="break-all px-2 py-1">{t.location || "—"}</td>
                    <td className="px-2 py-1">
                      <StatusPill tone={stateTone(t.state)} size="sm">
                        {t.state || "down"}
                      </StatusPill>
                    </td>
                    <td className="px-2 py-1">
                      {t.health_ok ? (
                        <StatusPill tone="green" size="sm">
                          ok
                        </StatusPill>
                      ) : (
                        <span className="text-ink-3">
                          {t.failure_count ? `${t.failure_count} fail` : "—"}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1">{t.socks_port ? t.socks_port : "—"}</td>
                    <td className="break-all px-2 py-1">{t.public_ip || "—"}</td>
                    <td className="px-2 py-1">
                      <div className="flex flex-wrap justify-end gap-1">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || t.state === "up" || t.state === "starting"}
                          onClick={() =>
                            setControlTarget({
                              tunnelId: t.tunnel_id,
                              name: t.name || t.tunnel_id,
                              action: "start",
                            })
                          }
                        >
                          Start
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || t.state === "down" || t.state === "stopping"}
                          onClick={() =>
                            setControlTarget({
                              tunnelId: t.tunnel_id,
                              name: t.name || t.tunnel_id,
                              action: "stop",
                            })
                          }
                        >
                          Stop
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || t.state === "down" || t.state === "stopping"}
                          onClick={() =>
                            setControlTarget({
                              tunnelId: t.tunnel_id,
                              name: t.name || t.tunnel_id,
                              action: "cycle",
                            })
                          }
                        >
                          Cycle
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => openEdit(t)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={busy}
                          onClick={() => {
                            setDeleteTarget({ tunnelId: t.tunnel_id, name: t.name || t.tunnel_id });
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
        </>}
        danger={
      <DangerZone
        title="Kill switch"
        warning="Clearing a kill switch re-enables traffic the switch deliberately blocked — a destructive, confirmed action."
      >
        <p className="mb-2 text-xs text-ink-3">
          Per-tunnel kill states and the system-level kill switch.
        </p>
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span className="text-ink-3">System kill switch:</span>
          {sysAvail === undefined ? (
            <span className="text-ink-3">—</span>
          ) : sysAvail ? (
            <StatusPill tone="green" size="sm">
              available
            </StatusPill>
          ) : (
            <StatusPill tone="amber" size="sm">
              unavailable
            </StatusPill>
          )}
          {statusQ.data?.system_killswitch_reason && (
            <span className="text-ink-3">{statusQ.data.system_killswitch_reason}</span>
          )}
        </div>
        {sysActive.length > 0 && (
          <p className="mb-2 text-xs text-ink-3">
            {sysActive.length} active system rule set(s):{" "}
            {sysActive.map((a) => a.tunnel_id).join(", ")}
          </p>
        )}

        {/* v3.66.714: the operator controls for the system kill switch. */}
        {sysAvail && tunnels.length > 0 && (
          <div className="mb-3 rounded border border-border p-2">
            <p className="mb-2 text-xs text-ink-3">
              System kill switch — <strong>plan</strong> is a dry run (computes the rule
              set, changes nothing). <strong>Apply</strong> installs the rules,{" "}
              <strong>commit</strong> makes them persist, <strong>revert</strong> removes
              them. Apply / commit / revert change system-level network state and are
              confirm-gated.
            </p>
            {tunnels.map((t) => (
              <div key={t.tunnel_id} className="flex flex-wrap items-center gap-2 py-1">
                <span className="text-xs tabular">{t.tunnel_id}</span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={sysKillMut.isPending}
                  onClick={() => sysKillMut.mutate({ tid: t.tunnel_id, verb: "plan" })}
                >
                  Plan
                </Button>
                {(["apply", "commit", "revert"] as const).map((verb) => (
                  <Button
                    key={verb}
                    size="sm"
                    variant="outline"
                    disabled={sysKillMut.isPending}
                    onClick={() => setSysKillConfirm({ tid: t.tunnel_id, verb })}
                  >
                    {verb[0].toUpperCase() + verb.slice(1)}
                  </Button>
                ))}
              </div>
            ))}
            {sysKillPlan && (
              <pre className="mt-2 max-h-40 overflow-auto rounded bg-surface-2 p-2 text-[11px]">
                {sysKillPlan}
              </pre>
            )}
          </div>
        )}
        {killStates.length === 0 ? (
          <p className="text-xs text-ink-3">No tunnels in a kill state.</p>
        ) : (
          <div className="overflow-auto rounded border border-border">
            <table className="bd-table w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="px-2 py-1">tunnel</th>
                  <th className="px-2 py-1">state</th>
                  <th className="px-2 py-1">cycles</th>
                  <th className="px-2 py-1">reason</th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {killStates.map((k) => (
                  <tr key={k.tunnel_id} className="border-t border-border">
                    <td className="break-all px-2 py-1">{k.tunnel_id}</td>
                    <td className="px-2 py-1">
                      <StatusPill tone={killTone(k.state)} size="sm">
                        {k.state || "?"}
                      </StatusPill>
                    </td>
                    <td className="px-2 py-1">{k.cycle_attempts ?? 0}</td>
                    <td className="break-all px-2 py-1">{k.reason || "—"}</td>
                    <td className="px-2 py-1 text-right">
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy || k.state === "cleared"}
                        title="Re-enables traffic on a tunnel the kill switch blocked — destructive confirm"
                        onClick={() => {
                          setKsClearTarget(k.tunnel_id);
                        }}
                      >
                        Clear
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3 text-xs">
          <span className="text-ink-3">Manual trigger:</span>
          <select
            className="h-8 rounded-md border border-input bg-transparent px-2 text-xs"
            value={ksTriggerTarget ?? ""}
            onChange={(e) => setKsTriggerTarget(e.target.value || null)}
          >
            <option value="">— pick tunnel —</option>
            {tunnels.map((t) => (
              <option key={t.tunnel_id} value={t.tunnel_id}>
                {t.name || t.tunnel_id}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !ksTriggerTarget}
            onClick={() => setKsTriggerConfirm(true)}
            title="Blocks the tunnel's traffic immediately"
          >
            Trigger kill
          </Button>
          <span className="ml-3 text-ink-3">
            Auto-recover: <b className="text-foreground">{killStateQ.data?.auto_recover ? "on" : "off"}</b>
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || killStateQ.data?.auto_recover === undefined}
            onClick={() => setAutoRecoverConfirm(true)}
          >
            Toggle auto-recover
          </Button>
        </div>
      </DangerZone>
        }
        result={<>
      {/* Global settings — verbatim fields from the legacy vpn_ui settings
          tab (leak interval, syskill default; auto-recover lives in the
          kill-switch card via its dedicated PUT). One-step confirm. */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Global settings</h2>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-2 text-ink-3">
            Leak-test interval (minutes)
            <Input
              type="number"
              min={5}
              max={1440}
              className="h-8 max-w-[90px] text-xs"
              value={
                setLeakMin ??
                String(Math.round(((settingsQ.data?.settings?.leak_test_interval_s ?? 1800) as number) / 60))
              }
              onChange={(e) => setSetLeakMin(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2 text-ink-3">
            <input
              type="checkbox"
              disabled={!sysAvail}
              checked={setSysDefault ?? !!settingsQ.data?.settings?.system_killswitch_default}
              onChange={(e) => setSetSysDefault(e.target.checked)}
            />
            Default to system kill switch on new tunnels
          </label>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setSettingsConfirm(true)}>
            Save settings
          </Button>
        </div>
      </Card>

      {/* Providers */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Providers</h2>
        {providers.length === 0 ? (
          <p className="text-xs text-ink-3">No providers loaded.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {providers.map((p) => (
              <span
                key={p.id}
                className="rounded border border-border px-2 py-1 text-xs text-ink-3"
              >
                <span className="text-foreground">{p.name}</span>
                {p.supported_backends && p.supported_backends.length > 0 && (
                  <> · {p.supported_backends.join(", ")}</>
                )}
              </span>
            ))}
          </div>
        )}
        {/* Credential check (row U). Server-side read: nothing persists, the
            response is only valid/message or location names. Credential
            fields are write-only password inputs (ConfigEditor secret rule)
            and never round-trip. */}
        <div className="mt-3 border-t border-border pt-3">
          <h3 className="mb-1 text-xs font-semibold uppercase text-ink-3">
            Credential check
          </h3>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <select
              className="h-8 rounded-md border border-input bg-transparent px-2 text-xs"
              value={credProvider}
              onChange={(e) => {
                setCredProvider(e.target.value);
                setCredResult(null);
                const schema = (providersQ.data?.providers ?? []).find(
                  (p) => p.id === e.target.value,
                )?.credentials_schema;
                setCredRows(
                  (schema ?? []).map((f) => ({ key: f.key, value: "", wasRedacted: false })),
                );
              }}
            >
              <option value="">— pick provider —</option>
              {(providersQ.data?.providers ?? providers).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name || p.id}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={!credProvider || credTestMut.isPending}
              onClick={() => credTestMut.mutate()}
            >
              Test credentials
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!credProvider || credLocMut.isPending}
              onClick={() => credLocMut.mutate()}
            >
              List locations
            </Button>
          </div>
          {credProvider && <ConfigEditor rows={credRows} setRows={setCredRows} />}
          {credResult && <p className="mt-2 text-xs text-ink-3">{credResult}</p>}
        </div>
      </Card>

      {/* Diagnostics (v3.66.768, 6A) — read-only VPN health + blacklist, plus a
          best-for lookup and an auto-blacklist recompute. Reads are posture-
          neutral; auto-blacklist recomputes the blacklist from recent stats. */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Diagnostics</h2>

        {/* backend availability */}
        <div className="mt-2">
          <h3 className="mb-1 text-xs font-semibold uppercase text-ink-3">
            Backends
          </h3>
          {backendsQ.isLoading ? (
            <p className="text-xs text-ink-3">Checking…</p>
          ) : (
            <div className="flex flex-wrap gap-2 text-xs">
              {(["wireguard", "openvpn"] as const).map((b) => {
                const a = backendsQ.data?.[b];
                return (
                  <span
                    key={b}
                    className="rounded border border-border px-2 py-1 text-ink-3"
                    title={a?.reason || ""}
                  >
                    <span className="text-foreground">{b}</span>{" "}
                    {a?.available ? "available" : "unavailable"}
                  </span>
                );
              })}
              <span
                className="rounded border border-border px-2 py-1 text-ink-3"
                title={sysKsAvailQ.data?.reason || ""}
              >
                <span className="text-foreground">system kill switch</span>{" "}
                {sysKsAvailQ.isLoading
                  ? "checking…"
                  : sysKsAvailQ.data?.available
                    ? "available"
                    : "unavailable"}
              </span>
            </div>
          )}
        </div>

        {/* blacklist */}
        <div className="mt-3 border-t border-border pt-3">
          <h3 className="mb-1 text-xs font-semibold uppercase text-ink-3">
            Blacklist
          </h3>
          {blacklistQ.isLoading ? (
            <p className="text-xs text-ink-3">Loading…</p>
          ) : (blacklistQ.data?.blacklist ?? []).length === 0 ? (
            <p className="text-xs text-ink-3">No profiles blacklisted.</p>
          ) : (
            <div className="flex flex-wrap gap-2 text-xs">
              {(blacklistQ.data?.blacklist ?? []).map((e, i) => (
                <span
                  key={i}
                  className="rounded border border-border px-2 py-1 text-ink-3"
                >
                  {typeof e === "string" ? e : JSON.stringify(e)}
                </span>
              ))}
            </div>
          )}
          <Button
            size="sm"
            variant="outline"
            className="mt-2"
            disabled={autoBlacklistMut.isPending}
            onClick={() => autoBlacklistMut.mutate()}
          >
            {autoBlacklistMut.isPending ? "Recomputing…" : "Auto-blacklist now"}
          </Button>
        </div>

        {/* best-for lookup */}
        <div className="mt-3 border-t border-border pt-3">
          <h3 className="mb-1 text-xs font-semibold uppercase text-ink-3">
            Best profile for site
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={bestForSid}
              onChange={(e) => {
                setBestForSid(e.target.value);
                setBestForResult(null);
              }}
              placeholder="site id"
              className="h-8 w-40 text-xs"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!bestForSid.trim() || bestForMut.isPending}
              onClick={() => bestForMut.mutate()}
            >
              Find best
            </Button>
            {bestForResult && (
              <span className="text-xs text-ink-3">{bestForResult}</span>
            )}
          </div>
        </div>

        {/* stats summary */}
        <div className="mt-3 border-t border-border pt-3">
          <h3 className="mb-1 text-xs font-semibold uppercase text-ink-3">
            Profile stats
          </h3>
          {statsQ.isLoading ? (
            <p className="text-xs text-ink-3">Loading…</p>
          ) : statsQ.data?.report ? (
            <p className="text-xs text-ink-3">
              {Object.keys(statsQ.data.report).length} profile metric group
              {Object.keys(statsQ.data.report).length === 1 ? "" : "s"} reported.
            </p>
          ) : (
            <p className="text-xs text-ink-3">No stats yet.</p>
          )}
        </div>
      </Card>

      {/* Leak tests (v3.66.769, 6B) — per-tunnel DNS/WebRTC/IP leak probes. Pick
          a tunnel to see its detail + latest/history results, and run the probes
          on demand. Run is an explicit action; the reads are posture-neutral. */}
      <Card className="mb-3 p-4">
        <h2 className="section-head">Leak tests</h2>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            className="h-8 rounded-md border border-input bg-transparent px-2 text-xs"
            value={leakTunnelId}
            onChange={(e) => setLeakTunnelId(e.target.value)}
          >
            <option value="">— pick tunnel —</option>
            {tunnels.map((t) => (
              <option key={t.tunnel_id} value={t.tunnel_id}>
                {t.name || t.tunnel_id}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="outline"
            disabled={!leakTunnelId || leakRunMut.isPending}
            onClick={() => leakRunMut.mutate()}
          >
            {leakRunMut.isPending ? "Running…" : "Run leak test"}
          </Button>
        </div>

        {leakTunnelId && (
          <div className="mt-3 space-y-3 border-t border-border pt-3 text-xs text-ink-3">
            {/* tunnel detail */}
            <div>
              <span className="font-semibold uppercase text-ink-3">Tunnel</span>{" "}
              {leakDetailQ.isLoading
                ? "loading…"
                : leakDetailQ.data?.tunnel
                  ? String(
                      (leakDetailQ.data.tunnel as Record<string, unknown>).public_ip ??
                        (leakDetailQ.data.tunnel as Record<string, unknown>).state ??
                        "no detail",
                    )
                  : "—"}
            </div>
            {/* latest leak result */}
            <div>
              <span className="font-semibold uppercase text-ink-3">Latest</span>{" "}
              {leakLatestQ.isLoading
                ? "loading…"
                : leakLatestQ.data?.result
                  ? "result available"
                  : "no result yet"}
            </div>
            {/* history */}
            <div>
              <span className="font-semibold uppercase text-ink-3">History</span>{" "}
              {leakHistoryQ.isLoading
                ? "loading…"
                : `${(leakHistoryQ.data?.history ?? []).length} prior run${
                    (leakHistoryQ.data?.history ?? []).length === 1 ? "" : "s"
                  }`}
            </div>
          </div>
        )}
      </Card>
        </>}
      />
      {/* Kill-switch CLEAR (destructive — re-enables traffic the kill switch blocked) */}
      <Dialog open={ksClearTarget !== null} onOpenChange={(o) => !o && setKsClearTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Clear kill switch</DialogTitle>
            <DialogDescription>
              Re-enable traffic on{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">{ksClearTarget}</code>.
              The kill switch blocked this tunnel for a reason — clearing it lets traffic flow
              again. Proceed?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setKsClearTarget(null)}>
              No, cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (ksClearTarget) killClearMut.mutate(ksClearTarget);
                setKsClearTarget(null);
              }}
            >
              Yes, clear kill switch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Kill-switch TRIGGER (one-step — blocks traffic now) */}
      <Dialog open={ksTriggerConfirm} onOpenChange={setKsTriggerConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Trigger kill switch</DialogTitle>
            <DialogDescription>
              Immediately block all traffic on{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">{ksTriggerTarget}</code>.
              Downloads through this tunnel stop until it is cleared or auto-recovers.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setKsTriggerConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (ksTriggerTarget) killTriggerMut.mutate(ksTriggerTarget);
                setKsTriggerConfirm(false);
              }}
            >
              Trigger
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Auto-recover toggle (one-step) */}
      <Dialog open={autoRecoverConfirm} onOpenChange={setAutoRecoverConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Toggle auto-recover</DialogTitle>
            <DialogDescription>
              {killStateQ.data?.auto_recover
                ? "Turn auto-recover OFF — killed tunnels stay blocked until manually cleared."
                : "Turn auto-recover ON — a killed tunnel cycles automatically up to 2 times."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAutoRecoverConfirm(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                autoRecoverMut.mutate(!killStateQ.data?.auto_recover);
                setAutoRecoverConfirm(false);
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Settings save (one-step) */}
      {/* v3.66.714: apply / commit / revert change system-level network state.
          The copy states the DIRECTION -- reverting removes protection, applying can
          cut connectivity if the plan is wrong. Plan first; it is a dry run. */}
      <Dialog
        open={sysKillConfirm !== null}
        onOpenChange={(o) => !o && setSysKillConfirm(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              System kill switch: {sysKillConfirm?.verb} on {sysKillConfirm?.tid}
            </DialogTitle>
            <DialogDescription>
              {sysKillConfirm?.verb === "revert"
                ? "This REMOVES the system kill-switch rules for this tunnel. Traffic will no longer be blocked if the VPN drops."
                : sysKillConfirm?.verb === "apply"
                  ? "This installs system-level firewall rules. If the plan is wrong it can cut connectivity to this host. Run Plan first and read it."
                  : "This makes the applied rules persist across restarts."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSysKillConfirm(null)}>
              Cancel
            </Button>
            <Button
              disabled={sysKillMut.isPending}
              onClick={() => {
                if (sysKillConfirm) {
                  sysKillMut.mutate({
                    tid: sysKillConfirm.tid,
                    verb: sysKillConfirm.verb,
                  });
                }
                setSysKillConfirm(null);
              }}
            >
              Confirm {sysKillConfirm?.verb}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={settingsConfirm} onOpenChange={setSettingsConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save VPN settings</DialogTitle>
            <DialogDescription>
              Persist the leak-test interval and system-kill-switch default. Applies globally.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSettingsConfirm(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                const mins = parseInt(
                  setLeakMin ??
                    String(
                      Math.round(((settingsQ.data?.settings?.leak_test_interval_s ?? 1800) as number) / 60),
                    ),
                  10,
                );
                settingsMut.mutate({
                  leak_test_interval_s: (mins > 0 ? mins : 30) * 60,
                  system_killswitch_default:
                    setSysDefault ?? !!settingsQ.data?.settings?.system_killswitch_default,
                });
                setSettingsConfirm(false);
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Control confirm (start/stop/cycle — light, recoverable, single tap) */}
      <Dialog open={controlTarget !== null} onOpenChange={(o) => !o && setControlTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {controlTarget?.action === "start" && "Start tunnel"}
              {controlTarget?.action === "stop" && "Stop tunnel"}
              {controlTarget?.action === "cycle" && "Cycle tunnel"}
            </DialogTitle>
            <DialogDescription>
              {controlTarget?.action === "start" && (
                <>
                  Bring{" "}
                  <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                    {controlTarget?.name}
                  </code>{" "}
                  up. This routes traffic through the tunnel and changes your network posture
                  (exit IP).
                </>
              )}
              {controlTarget?.action === "stop" && (
                <>
                  Take{" "}
                  <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                    {controlTarget?.name}
                  </code>{" "}
                  down. Traffic stops routing through this tunnel. You can start it again.
                </>
              )}
              {controlTarget?.action === "cycle" && (
                <>
                  Cycle{" "}
                  <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                    {controlTarget?.name}
                  </code>
                  . This rotates the tunnel — the exit IP changes and the connection briefly drops.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setControlTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                const t = controlTarget;
                setControlTarget(null);
                if (t) controlMut.mutate(t);
              }}
            >
              {controlTarget?.action === "start" && "Confirm start"}
              {controlTarget?.action === "stop" && "Confirm stop"}
              {controlTarget?.action === "cycle" && "Confirm cycle"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm (destructive — yes/no, No default) */}
      <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete tunnel</DialogTitle>
            <DialogDescription>
              Permanently delete tunnel{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                {deleteTarget?.name}
              </code>
              . If it is up it will be stopped first, then its config is removed. This cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setDeleteTarget(null)}>
              No, cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => {
                const t = deleteTarget;
                setDeleteTarget(null);
                if (t) deleteMut.mutate(t);
              }}
            >
              Yes, delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create tunnel (posture-neutral register — does not start it) */}
      <Dialog
        open={createOpen}
        onOpenChange={(o) => {
          if (!o) setCreateOpen(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New tunnel</DialogTitle>
            <DialogDescription>
              Register a VPN tunnel. This saves its definition — it does <strong>not</strong> start
              it or change your network posture. Start it explicitly afterward.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <div>
              <label className="text-xs font-medium text-ink-3">name</label>
              <Input
                value={cName}
                onChange={(e) => setCName(e.target.value)}
                placeholder="my-tunnel"
              />
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <label className="text-xs font-medium text-ink-3">provider</label>
                <select
                  className="w-full rounded border border-border bg-muted px-2 py-1.5 text-xs"
                  value={cProvider}
                  onChange={(e) => setCProvider(e.target.value)}
                >
                  {PROVIDERS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="text-xs font-medium text-ink-3">backend</label>
                <select
                  className="w-full rounded border border-border bg-muted px-2 py-1.5 text-xs"
                  value={cBackend}
                  onChange={(e) => setCBackend(e.target.value)}
                >
                  {BACKENDS.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-ink-3">
                location (optional)
              </label>
              <Input
                value={cLocation}
                onChange={(e) => setCLocation(e.target.value)}
                placeholder="e.g. us-nyc"
              />
            </div>
            <ConfigEditor rows={cRows} setRows={setCRows} disabled={busy} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !cName.trim()}
              onClick={() =>
                createMut.mutate({
                  name: cName.trim(),
                  provider: cProvider,
                  backend: cBackend,
                  location: cLocation.trim() || undefined,
                  config: configFromRows(cRows),
                })
              }
            >
              Create tunnel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit tunnel (name / location / MERGE config — posture-neutral) */}
      <Dialog open={editTarget !== null} onOpenChange={(o) => !o && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit tunnel</DialogTitle>
            <DialogDescription>
              Edit{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-foreground">
                {editTarget?.name || editTarget?.tunnel_id}
              </code>
              . Provider / backend are fixed once created. Config is <strong>merged</strong> — you
              can add or change keys here, not remove them. Editing does not change posture.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <div className="flex gap-3 text-xs text-ink-3">
              <span>
                provider: <span className="text-foreground">{editTarget?.provider}</span>
              </span>
              <span>
                backend: <span className="text-foreground">{editTarget?.backend}</span>
              </span>
            </div>
            <div>
              <label className="text-xs font-medium text-ink-3">name</label>
              <Input value={eName} onChange={(e) => setEName(e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-ink-3">location</label>
              <Input value={eLocation} onChange={(e) => setELocation(e.target.value)} />
            </div>
            <ConfigEditor rows={eRows} setRows={setERows} disabled={busy} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditTarget(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !eName.trim()}
              onClick={() => {
                const t = editTarget;
                if (!t) return;
                updateMut.mutate({
                  tunnelId: t.tunnel_id,
                  name: eName.trim(),
                  location: eLocation.trim(),
                  config: configFromRows(eRows),
                });
              }}
            >
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
