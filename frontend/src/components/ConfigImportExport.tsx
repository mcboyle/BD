import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/Callout";
import { DangerZone } from "@/components/ui/DangerZone";
import { Input } from "@/components/ui/input";
import { apiGet, apiPost } from "@/lib/api-client";
import type { SitesV2 } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Settings → Import / Export panel (v3.66.183).
//
// Wires POST /api/config/import (merge | replace) and GET /api/config/export.
// FULL "/api/…" literals below are REQUIRED so the GUI-parity scanner counts
// these endpoints as spa_wired — do NOT rebuild them from a concatenated base.
//
// Risk-model notes:
//   * The uploaded file may contain plaintext passwords. We parse it only to
//     count + name-match for the preview; we NEVER render any field value, so
//     no secret is displayed. The raw parsed object is POSTed as-is (the import
//     endpoint preserves a blank password against the existing site), the same
//     posture as the CLI import.
//   * "replace" wipes every existing site first — destructive. It is gated
//     behind a typed REPLACE confirm plus a "delete N sites" preview, and is
//     never a one-click action.
const IMPORT_EP = "/api/config/import";
const EXPORT_EP = "/api/config/export";

type Mode = "merge" | "replace";

interface ImportResult {
  ok: boolean;
  imported: number;
  updated: number;
  mode: string;
}

interface ParsedConfig {
  sites: Array<Record<string, unknown>>;
  names: string[]; // lower-cased, non-empty site names from the file
  fileName: string;
}

export function ConfigImportExport() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("merge");
  const [parsed, setParsed] = useState<ParsedConfig | null>(null);
  const [parseError, setParseError] = useState<string>("");
  const [confirmText, setConfirmText] = useState<string>("");
  const [includePw, setIncludePw] = useState<boolean>(false);

  // Current sites — read-only, drives the import preview (count + name match).
  const { data: current } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    refetchOnWindowFocus: false,
  });
  const currentNames = new Set(
    (current?.sites ?? []).map((s) => s.name.trim().toLowerCase()),
  );
  const currentCount = current?.count ?? current?.sites?.length ?? 0;

  function handleFile(file: File) {
    setParseError("");
    setParsed(null);
    setConfirmText("");
    const reader = new FileReader();
    reader.onerror = () => setParseError("Could not read the file.");
    reader.onload = () => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const raw: any = JSON.parse(String(reader.result));
        const sites: Array<Record<string, unknown>> = Array.isArray(raw)
          ? raw
          : Array.isArray(raw?.sites)
            ? raw.sites
            : [];
        if (sites.length === 0) {
          setParseError("No sites found in this file.");
          return;
        }
        const names = sites
          .map((s) => String((s?.name as unknown) ?? "").trim().toLowerCase())
          .filter(Boolean);
        setParsed({ sites, names, fileName: file.name });
      } catch (e) {
        setParseError(`Not valid JSON: ${(e as Error).message}`);
      }
    };
    reader.readAsText(file);
  }

  // Client-side preview. No secret values are read — counts + names only.
  const preview = (() => {
    if (!parsed) return null;
    if (mode === "replace") {
      return {
        kind: "replace" as const,
        willDelete: currentCount,
        willImport: parsed.sites.length,
      };
    }
    let added = 0;
    let updated = 0;
    for (const n of parsed.names) {
      if (currentNames.has(n)) updated++;
      else added++;
    }
    // Sites without a usable name are always added (server names them).
    const unnamed = parsed.sites.length - parsed.names.length;
    return { kind: "merge" as const, added: added + unnamed, updated };
  })();

  const importMut = useMutation<ImportResult, Error, void>({
    mutationFn: () => apiPost<ImportResult>(IMPORT_EP, { mode, sites: parsed!.sites }),
    onSuccess: (r) => {
      toast.success(`Imported ${r.imported} new, updated ${r.updated} (${r.mode}).`);
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      setParsed(null);
      setConfirmText("");
      if (fileRef.current) fileRef.current.value = "";
    },
    onError: (e) => toast.error(`Import failed: ${e.message}`),
  });

  const replaceArmed =
    mode !== "replace" || confirmText.trim().toUpperCase() === "REPLACE";
  const canImport = !!parsed && !importMut.isPending && replaceArmed;

  return (
    <div className="space-y-5 p-3">
      {/* ── EXPORT ─────────────────────────────────────────────── */}
      <DangerZone
        title="Export configuration"
        warning="Exports all site configs as JSON. With passwords included, the file holds secrets in clear text — keep it offline."
      >
        <div className="text-[11px] text-ink-3">
          Download all site configs as JSON. Passwords are stripped unless you
          opt in below.
        </div>
        <Callout tone="caution" title="Cleartext export" className="mt-2">
          <label className="flex items-center gap-2 text-ink-2">
            <input
              type="checkbox"
              checked={includePw}
              onChange={(e) => setIncludePw(e.target.checked)}
            />
            Include passwords (offline backup only — the file will contain secrets
            in clear text)
          </label>
        </Callout>
        <a
          href={includePw ? `${EXPORT_EP}?include_passwords=1` : EXPORT_EP}
          className={cn(
            "mt-2 inline-flex items-center gap-2 rounded-md border border-border px-3 py-2",
            "text-sm font-medium text-ink hover:bg-surface-2 transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          )}
        >
          <Download className="h-4 w-4" aria-hidden /> Export config
        </a>
      </DangerZone>

      <div className="h-px bg-border" />

      {/* ── IMPORT ─────────────────────────────────────────────── */}
      <div>
        <div className="text-sm font-medium text-ink">Import configuration</div>
        <div className="mt-0.5 text-[11px] text-ink-3">
          Import site configs from a JSON file. Merge keeps existing sites and
          adds/updates by name; Replace wipes all sites first.
        </div>

        {/* mode toggle */}
        <div className="mt-2 inline-flex overflow-hidden rounded-md border border-border">
          {(["merge", "replace"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => {
                setMode(m);
                setConfirmText("");
              }}
              className={cn(
                "px-3 py-1.5 text-xs font-medium transition-colors",
                mode === m
                  ? m === "replace"
                    ? "bg-err text-white"
                    : "bg-primary text-white"
                  : "bg-surface text-ink-2 hover:bg-surface-2",
              )}
            >
              {m === "merge" ? "Merge" : "Replace"}
            </button>
          ))}
        </div>

        {/* file picker */}
        <div className="mt-2 flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
            }}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => fileRef.current?.click()}
          >
            <Upload className="h-4 w-4" aria-hidden /> Choose file…
          </Button>
          {parsed && (
            <span className="text-[11px] text-ink-2">
              {parsed.fileName} · {parsed.sites.length} site(s)
            </span>
          )}
        </div>
        {parseError && (
          <div className="mt-2 text-[11px] text-err">{parseError}</div>
        )}

        {/* preview */}
        {preview?.kind === "merge" && (
          <div className="mt-2 rounded-md bg-surface-2 p-2 text-[11px] text-ink-2">
            Merge preview: <b>{preview.added}</b> new, <b>{preview.updated}</b>{" "}
            updated. Existing sites not in the file are kept.
          </div>
        )}
        {preview?.kind === "replace" && (
          <div className="mt-2 rounded-md border border-err/40 bg-err/10 p-2 text-[11px] text-ink">
            <div className="flex items-center gap-1 font-medium text-err">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Destructive
            </div>
            <div className="mt-1">
              This will <b>delete all {preview.willDelete} current site(s)</b>,
              then import {preview.willImport}. This cannot be undone.
            </div>
            <div className="mt-2">
              Type{" "}
              <span className="font-mono font-semibold">REPLACE</span> to
              confirm:
              <Input
                className="mt-1"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="REPLACE"
                aria-label="Type REPLACE to confirm destructive import"
              />
            </div>
          </div>
        )}

        <Button
          type="button"
          className="mt-3"
          disabled={!canImport}
          onClick={() => importMut.mutate()}
        >
          {importMut.isPending
            ? "Importing…"
            : mode === "replace"
              ? "Replace all & import"
              : "Merge import"}
        </Button>
      </div>
    </div>
  );
}
