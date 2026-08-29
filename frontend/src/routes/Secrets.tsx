// Secrets — vault lifecycle operator surface.
//
// Wires the six vault-lifecycle endpoints (all POST/GET, all return ZERO stored
// secret values — the server never echoes a credential, so nothing redacted ever
// round-trips into a field):
//
//   * GET  /api/secrets/status           — backend, lock state, plaintext count,
//                                           stored KEY NAMES only, capability flags.
//   * POST /api/secrets/configure         — {backend} switch active backend (no migrate).
//   * POST /api/secrets/unlock            — {password} unlock master_password backend.
//                                           Server-side escalating back-off → 429.
//   * POST /api/secrets/lock              — forget the derived key.
//   * POST /api/secrets/change_password   — {old_password,new_password} re-encrypt all.
//                                           new ≥ 8; distinct 401 (wrong old) / 500 (corrupt).
//   * POST /api/secrets/migrate           — move plaintext passwords → encrypted backend.
//
// Posture: configure-to-plaintext (a downgrade), change_password (re-encrypts
// everything) and migrate (moves plaintext into the backend) each take a single
// confirm. unlock/lock are routine. No secret value is ever displayed.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { ApiTokensPanel } from "@/components/ApiTokensPanel";
import { SecretField } from "@/components/SecretField";
import { StatusPill, type PillTone } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { DangerZone } from "@/components/ui/DangerZone";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";
import { formatTimestamp } from "@/lib/format";

interface SecretsStatus {
  ok: boolean;
  backend: string;
  is_unlocked: boolean;
  is_initialized: boolean;
  plaintext_count: number;
  plaintext_sites: string[];
  stored_keys: string[];
  keyring_available: boolean;
  crypto_available: boolean;
}

type BackendName = "windows_credential" | "master_password" | "plaintext";

// Mirror of secrets_store.configure_backend's accepted set. The server validates
// (returns 400 if a backend is unavailable on this host), so a stale list here can
// never select an invalid backend — it just won't be offered until updated.
const BACKENDS: { name: BackendName; label: string; needs: keyof SecretsStatus | null }[] = [
  { name: "master_password", label: "Master password (encrypted)", needs: "crypto_available" },
  { name: "windows_credential", label: "Windows Credential Manager", needs: "keyring_available" },
  { name: "plaintext", label: "Plaintext (no encryption)", needs: null },
];

function backendTone(name: string): PillTone {
  if (name === "plaintext") return "amber";
  return "green";
}

export function Secrets() {
  const qc = useQueryClient();

  const statusQ = useQuery<SecretsStatus>({
    queryKey: ["secrets-status"],
    queryFn: ({ signal }) => apiGet<SecretsStatus>("/api/secrets/status", signal),
    refetchInterval: 5000,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["secrets-status"] });

  // ── configure (switch backend; confirm a plaintext downgrade) ──────────────
  const [pendingBackend, setPendingBackend] = useState<BackendName | null>(null);
  const configureMut = useMutation<{ ok?: boolean; error?: string; backend?: string }, Error, BackendName>({
    mutationFn: (backend) => apiPost("/api/secrets/configure", { backend }),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "configure failed");
        return;
      }
      toast.success(`Backend set to ${r.backend}`);
      setPendingBackend(null);
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });
  function chooseBackend(name: BackendName) {
    if (name === "plaintext") {
      setPendingBackend(name); // downgrade — confirm
      return;
    }
    configureMut.mutate(name);
  }

  // ── unlock / lock ──────────────────────────────────────────────────────────
  const [unlockPw, setUnlockPw] = useState("");
  const unlockMut = useMutation<{ ok?: boolean; error?: string; note?: string }, Error, string>({
    mutationFn: (password) => apiPost("/api/secrets/unlock", { password }),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "unlock failed");
        return;
      }
      toast.success(r.note || "Unlocked");
      setUnlockPw("");
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });
  const lockMut = useMutation<{ ok?: boolean; error?: string }, Error, void>({
    mutationFn: () => apiPost("/api/secrets/lock", {}),
    onSuccess: () => {
      toast.success("Locked");
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  // ── change_password (re-encrypt all; confirm) ──────────────────────────────
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmChange, setConfirmChange] = useState(false);
  const changeMut = useMutation<
    { ok?: boolean; error?: string },
    Error,
    { old_password: string; new_password: string }
  >({
    mutationFn: (body) => apiPost("/api/secrets/change_password", body),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "change failed");
        return;
      }
      toast.success("Master password changed");
      setOldPw("");
      setNewPw("");
      setConfirmChange(false);
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  // ── migrate (plaintext → encrypted; confirm) ───────────────────────────────
  const [confirmMigrate, setConfirmMigrate] = useState(false);
  const migrateMut = useMutation<
    { ok?: boolean; error?: string; migrated?: number; errors?: string[]; remaining_plaintext?: number },
    Error,
    void
  >({
    mutationFn: () => apiPost("/api/secrets/migrate", {}),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "migrate failed");
        return;
      }
      const errs = r.errors?.length ? ` (${r.errors.length} error${r.errors.length === 1 ? "" : "s"})` : "";
      toast.success(`Migrated ${r.migrated ?? 0} secret(s)${errs}; ${r.remaining_plaintext ?? 0} plaintext remaining`);
      setConfirmMigrate(false);
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  // ── delete a stored secret (by key) ────────────────────────────────────────
  const [deleteKey, setDeleteKey] = useState("");
  const deleteMut = useMutation<
    { ok?: boolean; error?: string; removed?: boolean; config_cleaned?: boolean },
    Error,
    string
  >({
    mutationFn: (key) => apiPost("/api/secrets/delete", { key }),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "delete failed");
        return;
      }
      toast.success(r.removed ? "Secret deleted" : "No such key");
      setDeleteKey("");
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  // ── import flow (preview → apply) ──────────────────────────────────────────
  // import_file returns the user's OWN export records, which DO carry passwords.
  // We hold them in component state to send to import_apply, but the password is
  // NEVER displayed — the preview masks it. (This is the one redaction point.)
  interface ImportRecord {
    name?: string;
    url?: string;
    username?: string;
    password?: string;
  }
  const [importText, setImportText] = useState("");
  const [importRecords, setImportRecords] = useState<ImportRecord[]>([]);
  const [importFormat, setImportFormat] = useState("");
  const [importPick, setImportPick] = useState<boolean[]>([]);
  const [importSiteIds, setImportSiteIds] = useState<string[]>([]);
  const importFileMut = useMutation<
    { ok?: boolean; error?: string; format?: string; records?: ImportRecord[]; count?: number },
    Error,
    string
  >({
    mutationFn: (content) => apiPost("/api/secrets/import_file", { content }),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "parse failed");
        return;
      }
      const recs = r.records ?? [];
      setImportRecords(recs);
      setImportFormat(r.format ?? "");
      setImportPick(recs.map(() => true));
      setImportSiteIds(recs.map(() => ""));
      toast.success(`Parsed ${recs.length} record(s) (${r.format})`);
    },
    onError: (e) => toast.error(e.message),
  });
  const importApplyMut = useMutation<
    { ok?: boolean; error?: string; saved?: number; skipped?: number; errors?: string[] },
    Error,
    { records: ImportRecord[]; site_ids: string[] }
  >({
    mutationFn: (body) => apiPost("/api/secrets/import_apply", body),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "import failed");
        return;
      }
      const errs = r.errors?.length ? ` (${r.errors.length} error${r.errors.length === 1 ? "" : "s"})` : "";
      toast.success(`Saved ${r.saved ?? 0}, skipped ${r.skipped ?? 0}${errs}`);
      setImportText("");
      setImportRecords([]);
      setImportPick([]);
      setImportSiteIds([]);
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });
  function applyImport() {
    const records: ImportRecord[] = [];
    const site_ids: string[] = [];
    importRecords.forEach((rec, i) => {
      if (importPick[i]) {
        records.push(rec);
        site_ids.push(importSiteIds[i] || "");
      }
    });
    if (records.length === 0) {
      toast.error("No records selected");
      return;
    }
    importApplyMut.mutate({ records, site_ids });
  }

  // ── extension pairing ──────────────────────────────────────────────────────
  interface PairedExt {
    id: string;
    label: string;
    issued_at: number;
    last_used_at: number;
  }
  const pairedQ = useQuery<{ ok: boolean; extensions: PairedExt[] }>({
    queryKey: ["secrets-paired"],
    queryFn: ({ signal }) => apiGet("/api/secrets/extension/list_paired", signal),
    refetchInterval: 10000,
  });
  const [pairingToken, setPairingToken] = useState<string | null>(null);
  const [pairingExpiry, setPairingExpiry] = useState<number>(0);
  const pairIssueMut = useMutation<
    { ok?: boolean; error?: string; pairing_token?: string; expires_in_seconds?: number },
    Error,
    void
  >({
    mutationFn: () => apiPost("/api/secrets/extension/pair_issue", {}),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "pairing failed");
        return;
      }
      setPairingToken(r.pairing_token ?? null);
      setPairingExpiry(r.expires_in_seconds ?? 0);
    },
    onError: (e) => toast.error(e.message),
  });
  const [revokeId, setRevokeId] = useState<string | null>(null);
  const revokeMut = useMutation<{ ok?: boolean; error?: string; removed?: boolean }, Error, string>({
    mutationFn: (id) => apiPost("/api/secrets/extension/revoke", { id }),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "revoke failed");
        return;
      }
      toast.success(r.removed ? "Extension revoked" : "Nothing matched");
      setRevokeId(null);
      qc.invalidateQueries({ queryKey: ["secrets-paired"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const busy =
    configureMut.isPending ||
    unlockMut.isPending ||
    lockMut.isPending ||
    changeMut.isPending ||
    migrateMut.isPending ||
    deleteMut.isPending ||
    importFileMut.isPending ||
    importApplyMut.isPending ||
    pairIssueMut.isPending ||
    revokeMut.isPending;

  const s = statusQ.data;
  const isMaster = s?.backend === "master_password";
  const newPwTooShort = newPw.length > 0 && newPw.length < 8;

  return (
    <AppShell title="Secrets" subtitle="Vault backend · unlock / lock · rotate · migrate">
      <WorkflowPage
        purpose={<>
      <GatedWriteBanner title="Operator credential surface" className="mb-3">
        Secret values are <strong>write-only</strong> and never
        displayed. <strong>Change password</strong> re-encrypts every stored secret;{" "}
        <strong>Migrate</strong> moves plaintext passwords into the encrypted backend — each takes
        a confirm.
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mb-3">
        Manage the encrypted credential vault: choose a backend, unlock or lock
        it, change the master password, import from a password-manager export,
        pair a browser extension, and mint scoped API tokens. Secret values are
        write-only and never displayed; deleting a stored secret is grouped in
        the danger zone below.
      </Callout>
        </>}
        inputs={<>
      {statusQ.isError && (
        <Card className="mb-3 border-red-700/40 p-3 text-sm text-red-300">
          Could not load secrets status. {(statusQ.error as Error)?.message}
        </Card>
      )}

      {/* ── status ─────────────────────────────────────────────────────────── */}
      <Card className="mb-3 p-4">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-sm font-medium text-ink">Vault status</span>
          {s && <StatusPill tone={backendTone(s.backend)}>{s.backend}</StatusPill>}
          {s && (
            <StatusPill tone={s.is_unlocked ? "green" : "neutral"}>
              {s.is_unlocked ? "unlocked" : "locked"}
            </StatusPill>
          )}
        </div>
        {s ? (
          <div className="space-y-1 text-xs text-ink-3">
            <div>
              Initialized: {String(s.is_initialized)} · stored keys: {s.stored_keys.length} · keyring:{" "}
              {String(s.keyring_available)} · crypto: {String(s.crypto_available)}
            </div>
            {s.stored_keys.length > 0 && (
              <div className="truncate">
                Key names: <span className="font-mono">{s.stored_keys.join(", ")}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-ink-3">Loading…</div>
        )}
      </Card>

      {/* ── migration banner ───────────────────────────────────────────────── */}
      {s && s.plaintext_count > 0 && (
        <Card className="mb-3 border-amber-700/40 bg-amber-950/20 p-4">
          <div className="text-sm font-medium text-amber-200">
            {s.plaintext_count} plaintext password{s.plaintext_count === 1 ? "" : "s"} in
            sites_config.json
          </div>
          <div className="mt-0.5 text-[11px] text-ink-3 truncate">
            Sites: <span className="font-mono">{s.plaintext_sites.join(", ")}</span>
          </div>
          <div className="mt-2">
            <Button
              size="sm"
              disabled={busy || (isMaster && !s.is_unlocked)}
              onClick={() => setConfirmMigrate(true)}
            >
              {migrateMut.isPending ? "Migrating…" : "Migrate to encrypted backend"}
            </Button>
            {isMaster && !s.is_unlocked && (
              <span className="ml-2 text-[11px] text-ink-3">Unlock the backend first.</span>
            )}
          </div>
        </Card>
      )}

      {/* ── backend ────────────────────────────────────────────────────────── */}
      <Card className="mb-3 p-4">
        <div className="mb-2 text-sm font-medium text-ink">Backend</div>
        <div className="flex flex-wrap gap-2">
          {BACKENDS.map((b) => {
            const available = b.needs == null || (s ? Boolean(s[b.needs]) : true);
            const active = s?.backend === b.name;
            return (
              <Button
                key={b.name}
                size="sm"
                variant={active ? "default" : "outline"}
                disabled={busy || active || !available}
                onClick={() => chooseBackend(b.name)}
                title={available ? undefined : "Unavailable on this host"}
              >
                {b.label}
                {!available ? " (unavailable)" : ""}
              </Button>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-ink-3">
          Switching the backend does not move existing data — use Migrate for that.
        </p>
      </Card>

      {/* ── unlock / lock (master_password only) ───────────────────────────── */}
      {isMaster && (
        <Card className="mb-3 p-4">
          <div className="mb-2 text-sm font-medium text-ink">Unlock / lock</div>
          <p className="mb-3 text-xs text-ink-3">
            The master key is held only in this app process. This deployment
            requires a human unlock after every service restart, crash, deploy,
            or host reboot.
          </p>
          <div className="flex items-center gap-2">
            <SecretField
              value={unlockPw}
              onChange={setUnlockPw}
              ariaLabel="Master password to unlock"
              placeholder="master password"
              disabled={busy}
            />
            <Button
              size="sm"
              disabled={busy || !unlockPw}
              onClick={() => unlockMut.mutate(unlockPw)}
            >
              {unlockMut.isPending ? "Unlocking…" : "Unlock"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !s?.is_unlocked}
              onClick={() => lockMut.mutate()}
            >
              Lock
            </Button>
          </div>
        </Card>
      )}

      {/* ── change master password ─────────────────────────────────────────── */}
      {isMaster && (
        <Card className="mb-3 p-4">
          <div className="mb-2 text-sm font-medium text-ink">Change master password</div>
          <div className="space-y-2">
            <SecretField
              value={oldPw}
              onChange={setOldPw}
              ariaLabel="Current master password"
              placeholder="current password"
              disabled={busy}
            />
            <SecretField
              value={newPw}
              onChange={setNewPw}
              ariaLabel="New master password"
              placeholder="new password (min 8 chars)"
              disabled={busy}
            />
            {newPwTooShort && (
              <div className="text-[11px] text-red-400">New password must be at least 8 characters.</div>
            )}
            <Button
              size="sm"
              disabled={busy || !oldPw || !newPw || newPwTooShort}
              onClick={() => setConfirmChange(true)}
            >
              {changeMut.isPending ? "Changing…" : "Change password"}
            </Button>
          </div>
        </Card>
      )}

        </>}
        danger={<>
      {/* ── delete a stored secret ─────────────────────────────────────────── */}
      <DangerZone
        title="Delete a stored secret"
        warning="Permanently removes the selected secret from the vault — this cannot be undone."
        className="mb-3"
      >
        {s && s.stored_keys.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="h-8 rounded-sm border border-input bg-background px-2 text-xs text-ink"
              value={deleteKey}
              disabled={busy}
              aria-label="Stored secret key to delete"
              onChange={(e) => setDeleteKey(e.target.value)}
            >
              <option value="">Select a key…</option>
              {s.stored_keys.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy || !deleteKey}
              onClick={() => deleteMut.mutate(deleteKey)}
            >
              {deleteMut.isPending ? "Deleting…" : "Delete"}
            </Button>
          </div>
        ) : (
          <p className="text-[11px] text-ink-3">No stored secrets to delete.</p>
        )}
      </DangerZone>
        </>}
        result={<>
      {/* ── import from a password-manager export ───────────────────────────── */}
      <Card className="mb-3 p-4">
        <div className="mb-2 text-sm font-medium text-ink">Import passwords</div>
        <p className="mb-2 text-[11px] text-ink-3">
          Paste a password-manager export (Bitwarden, 1Password, CSV…). Parsed locally for
          preview; nothing is saved until you apply. Passwords are never displayed.
          {isMaster && !s?.is_unlocked ? " Unlock the backend before applying." : ""}
        </p>
        <textarea
          className="h-24 w-full rounded border border-input bg-background p-2 font-mono text-xs"
          value={importText}
          disabled={busy}
          placeholder="paste export contents here"
          aria-label="Password export contents"
          onChange={(e) => setImportText(e.target.value)}
        />
        <div className="mt-2">
          <Button
            size="sm"
            disabled={busy || !importText.trim()}
            onClick={() => importFileMut.mutate(importText)}
          >
            {importFileMut.isPending ? "Parsing…" : "Parse / preview"}
          </Button>
        </div>

        {importRecords.length > 0 && (
          <div className="mt-3 space-y-1">
            <div className="text-[11px] text-ink-3">
              {importRecords.length} record(s) parsed ({importFormat}). Select which to save; the
              password column is masked.
            </div>
            <div className="max-h-64 overflow-auto rounded border border-input">
              <table className="bd-table w-full text-left text-xs">
                <thead className="bg-surface-2 text-ink-3">
                  <tr>
                    <th className="p-1"></th>
                    <th className="p-1">name</th>
                    <th className="p-1">username</th>
                    <th className="p-1">password</th>
                    <th className="p-1">link to site (optional)</th>
                  </tr>
                </thead>
                <tbody>
                  {importRecords.map((rec, i) => (
                    <tr key={i} className="border-t border-input/50">
                      <td className="p-1">
                        <input
                          type="checkbox"
                          checked={importPick[i] ?? false}
                          disabled={busy}
                          aria-label={`Select ${rec.name || "record " + (i + 1)}`}
                          onChange={(e) =>
                            setImportPick((p) => p.map((v, idx) => (idx === i ? e.target.checked : v)))
                          }
                        />
                      </td>
                      <td className="p-1 truncate">{rec.name || "—"}</td>
                      <td className="p-1 truncate">{rec.username || "—"}</td>
                      <td className="p-1 font-mono text-ink-3">{rec.password ? "••••••••" : "—"}</td>
                      <td className="p-1">
                        <input
                          className="h-7 w-28 rounded-sm border border-input bg-background px-1 text-xs"
                          value={importSiteIds[i] ?? ""}
                          disabled={busy}
                          placeholder="site_id"
                          aria-label={`Site id for ${rec.name || "record " + (i + 1)}`}
                          onChange={(e) =>
                            setImportSiteIds((a) => a.map((v, idx) => (idx === i ? e.target.value : v)))
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Button
              size="sm"
              disabled={busy || (isMaster && !s?.is_unlocked)}
              onClick={applyImport}
            >
              {importApplyMut.isPending ? "Saving…" : "Apply selected"}
            </Button>
          </div>
        )}
      </Card>

      {/* ── browser-extension pairing ──────────────────────────────────────── */}
      <Card className="mb-3 p-4">
        <div className="mb-2 text-sm font-medium text-ink">Browser extension</div>
        <p className="mb-2 text-[11px] text-ink-3">
          Pair a browser extension for autofill. Issuing a pairing token shows it once — paste it
          into the extension's options page within the expiry window.
        </p>
        <Button size="sm" disabled={busy} onClick={() => pairIssueMut.mutate()}>
          {pairIssueMut.isPending ? "Issuing…" : "Pair extension"}
        </Button>
        {pairingToken && (
          <div className="mt-2 rounded border border-amber-700/40 bg-amber-950/20 p-2">
            <div className="text-[11px] text-ink-3">
              Pairing token (expires in {pairingExpiry}s) — paste into the extension:
            </div>
            <code className="mt-1 block break-all rounded bg-muted px-1 py-0.5 font-mono text-xs">
              {pairingToken}
            </code>
            <Button
              size="sm"
              variant="ghost"
              className="mt-1"
              onClick={() => {
                void navigator.clipboard?.writeText(pairingToken);
                toast.success("Copied");
              }}
            >
              Copy
            </Button>
          </div>
        )}

        <div className="mt-3">
          <div className="text-[11px] text-ink-3">
            {pairedQ.data?.extensions?.length
              ? `${pairedQ.data.extensions.length} paired`
              : "No extensions paired"}
          </div>
          {pairedQ.data?.extensions?.map((ext) => (
            <div
              key={ext.id}
              className="mt-1 flex items-center justify-between rounded border border-input p-2"
            >
              <div className="min-w-0">
                <div className="truncate text-xs text-ink">
                  {ext.label} <span className="font-mono text-ink-3">({ext.id})</span>
                </div>
                <div className="text-[11px] text-ink-3">
                  last used: {ext.last_used_at ? formatTimestamp(ext.last_used_at) : "never"}
                </div>
              </div>
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() => setRevokeId(ext.id)}
              >
                Revoke
              </Button>
            </div>
          ))}
        </div>
      </Card>

        </>}
      />

      {/* ── confirm dialogs (inline, typed-free single confirm) ────────────── */}
      {pendingBackend === "plaintext" && (
        <ConfirmBar
          tone="danger"
          message="Switch to the plaintext backend? Stored secrets will no longer be encrypted."
          confirmLabel="Switch to plaintext"
          busy={configureMut.isPending}
          onConfirm={() => configureMut.mutate("plaintext")}
          onCancel={() => setPendingBackend(null)}
        />
      )}
      {confirmChange && (
        <ConfirmBar
          message="Re-encrypt every stored secret with the new master password? Your old password stays in effect if anything fails."
          confirmLabel="Change password"
          busy={changeMut.isPending}
          onConfirm={() => changeMut.mutate({ old_password: oldPw, new_password: newPw })}
          onCancel={() => setConfirmChange(false)}
        />
      )}
      {confirmMigrate && (
        <ConfirmBar
          message="Move all plaintext passwords from sites_config.json into the encrypted backend?"
          confirmLabel="Migrate"
          busy={migrateMut.isPending}
          onConfirm={() => migrateMut.mutate()}
          onCancel={() => setConfirmMigrate(false)}
        />
      )}
      {revokeId && (
        <ConfirmBar
          tone="danger"
          message={`Revoke paired extension ${revokeId}? It will lose vault access immediately.`}
          confirmLabel="Revoke"
          busy={revokeMut.isPending}
          onConfirm={() => revokeMut.mutate(revokeId)}
          onCancel={() => setRevokeId(null)}
        />
      )}

      <ApiTokensPanel />
    </AppShell>
  );
}

function ConfirmBar({
  message,
  confirmLabel,
  busy,
  tone = "default",
  onConfirm,
  onCancel,
}: {
  message: string;
  confirmLabel: string;
  busy?: boolean;
  tone?: "default" | "danger";
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Card
      className={
        "mb-3 p-4 " + (tone === "danger" ? "border-red-700/50 bg-red-950/20" : "border-amber-700/40 bg-amber-950/20")
      }
    >
      <div className="text-sm text-ink">{message}</div>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant={tone === "danger" ? "destructive" : "default"} disabled={busy} onClick={onConfirm}>
          {busy ? "Working…" : confirmLabel}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}
