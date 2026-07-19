import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { SettingRow, SettingSection } from "@/components/SettingSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Callout } from "@/components/ui/Callout";
import { apiGet, apiPost, ApiError } from "@/lib/api-client";

// Bucket 3b (GUI-config parity): the "Store metadata (raw / advanced)" Settings
// section. vpn.* and widgets.* metadata (schema_version, tunnel_id, _saved_at)
// live in SEPARATE stores (tunnels.json / widgets.json), not global_config, and
// neither exposes a per-key meta API — so this is a RAW JSON store-file editor
// reading/writing the dedicated /api/settings/store-raw endpoint, with its OWN
// fetch/save state separate from the global-config draft.
//
// Footguns surfaced in-UI:
//   * _saved_at is auto-stamped on the next store save() — manual edits are
//     transient (it stays display-only in the parity ledger).
//   * schema_version is the migration key — a hand-set wrong version can break a
//     future store migration.
//   * tunnel_id is a primary key AND the secrets foreign-key (@cred:{id}:*). A
//     raw edit that drops/renames a tunnel_id with secrets is BLOCKED server-side
//     (400) to avoid orphaning them — use the Rename tunnel action below, which
//     re-keys the secrets atomically.

type StoreName = "vpn" | "widgets";

interface StoreRawState {
  ok: boolean;
  store: StoreName;
  path: string;
  text: string;
}

function errOf(e: unknown): string {
  if (e instanceof ApiError) {
    const b = e.body as { error?: string } | undefined;
    if (b?.error) return b.error;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export function StoreRawSettings() {
  const qc = useQueryClient();
  const [store, setStore] = useState<StoreName>("vpn");
  const [path, setPath] = useState<string>("");
  const [text, setText] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [saving, setSaving] = useState(false);

  // rekey mini-form
  const [oldId, setOldId] = useState("");
  const [newId, setNewId] = useState("");
  const [rekeying, setRekeying] = useState(false);

  async function loadStore(s: StoreName) {
    setLoading(true);
    setError("");
    try {
      const data = await apiGet<StoreRawState>(
        `/api/settings/store-raw?store=${s}`,
      );
      setPath(data.path);
      setText(data.text);
      setDirty(false);
    } catch (e) {
      setError(errOf(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStore(store);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store]);

  function validateClientSide(): boolean {
    try {
      JSON.parse(text);
      setError("");
      toast.success("Valid JSON.");
      return true;
    } catch (e) {
      setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  }

  async function save() {
    // client-side JSON parse first so an obvious typo never hits the server
    try {
      JSON.parse(text);
    } catch (e) {
      setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (
      !window.confirm(
        `Overwrite the ${store} store file?\n\n${path}\n\nThe in-memory store is reloaded immediately. ` +
          `A tunnel_id change that would orphan stored secrets is rejected — ` +
          `use Rename tunnel instead.`,
      )
    ) {
      return;
    }
    setSaving(true);
    setError("");
    try {
      await apiPost(`/api/settings/store-raw`, { store, text });
      toast.success(`Saved ${store} store + reloaded.`);
      setDirty(false);
      qc.invalidateQueries();
      void loadStore(store);
    } catch (e) {
      setError(errOf(e));
      toast.error("Save rejected — nothing was written.");
    } finally {
      setSaving(false);
    }
  }

  async function rekey() {
    if (!oldId || !newId) {
      setError("Rename tunnel needs both the current and new tunnel_id.");
      return;
    }
    if (
      !window.confirm(
        `Rename tunnel ${oldId} -> ${newId}? This atomically moves @cred:${oldId}:* secrets to @cred:${newId}:*.`,
      )
    ) {
      return;
    }
    setRekeying(true);
    setError("");
    try {
      await apiPost(`/api/settings/store-raw/rekey`, {
        old_id: oldId,
        new_id: newId,
      });
      toast.success(`Renamed ${oldId} -> ${newId} (secrets moved).`);
      setOldId("");
      setNewId("");
      qc.invalidateQueries();
      void loadStore(store);
    } catch (e) {
      setError(errOf(e));
      toast.error("Rename failed.");
    } finally {
      setRekeying(false);
    }
  }

  return (
    <SettingSection
      collapsible
      defaultOpen={false}
      label="Store metadata (raw / advanced)"
      description="Direct JSON editor for the VPN + widgets stores. System-managed — edit at your own risk."
    >
      <div className="px-4 py-3">
        <Callout tone="caution" title="System-managed stores — edit at your own risk">
          These files hold store metadata (<code>schema_version</code>,{" "}
          <code>tunnel_id</code>, <code>_saved_at</code>) that is normally written
          by the app, not by hand. <code>_saved_at</code> is re-stamped on the next
          save (manual edits are transient). <code>schema_version</code> is the
          migration key. <code>tunnel_id</code> is a primary key <em>and</em> the
          secrets foreign-key — a raw edit that drops or renames a tunnel with
          stored secrets is rejected; use <strong>Rename tunnel</strong> below.
        </Callout>
      </div>

      <div className="flex items-center gap-2 px-4 pb-2">
        {(["vpn", "widgets"] as StoreName[]).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setStore(s)}
            aria-pressed={store === s}
            className={
              "rounded-md px-3 py-1 text-sm " +
              (store === s
                ? "bg-primary-soft text-primary"
                : "bg-surface-2 text-ink-3 hover:text-ink")
            }
          >
            {s}
          </button>
        ))}
        {path && (
          <span className="ml-auto text-[11px] text-ink-3">
            <code>{path}</code>
          </span>
        )}
      </div>

      <div className="px-4">
        <textarea
          aria-label={`${store} store JSON`}
          spellCheck={false}
          value={loading ? "" : text}
          onChange={(e) => {
            setText(e.target.value);
            setDirty(true);
          }}
          className="h-72 w-full rounded-md border border-hairline bg-surface-2 p-2 font-mono text-xs"
          placeholder={loading ? "Loading…" : ""}
        />
        {error && (
          <p data-testid="store-raw-error" className="mt-1 text-[11px] text-red-600">
            {error}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-3 px-4 py-3">
        {dirty && <span className="text-ink-3 text-xs">unsaved</span>}
        <Button variant="outline" onClick={validateClientSide} disabled={loading}>
          Validate
        </Button>
        <Button onClick={save} disabled={loading || saving || !dirty}>
          Save store
        </Button>
      </div>

      <SettingRow
        label="Rename tunnel (re-key secrets)"
        hint="The only safe way to change a tunnel_id: moves @cred:OLD:* secrets to @cred:NEW:* atomically, then renames the tunnel."
        stacked
        control={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="Current tunnel_id"
              placeholder="current tunnel_id"
              value={oldId}
              onChange={(e) => setOldId(e.target.value)}
              className="w-44 font-mono text-xs"
            />
            <span className="text-ink-3">→</span>
            <Input
              aria-label="New tunnel_id"
              placeholder="new tunnel_id"
              value={newId}
              onChange={(e) => setNewId(e.target.value)}
              className="w-44 font-mono text-xs"
            />
            <Button
              variant="outline"
              onClick={rekey}
              disabled={rekeying || !oldId || !newId}
            >
              Rename
            </Button>
          </div>
        }
      />
    </SettingSection>
  );
}
