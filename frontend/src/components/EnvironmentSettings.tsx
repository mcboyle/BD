import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { SettingRow, SettingSection } from "@/components/SettingSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Callout } from "@/components/ui/Callout";
import { apiGet, apiPost, ApiError } from "@/lib/api-client";
import type {
  EnvfileState,
  EnvfileSaveResult,
} from "@/lib/api-types";

// Bucket 2 (GUI-config parity): the "Environment (restart required)" Settings
// section. Unlike the rest of the page (which round-trips /api/global_config),
// these deploy/path/port/host vars are consumed at BOOT or by external CLI tools,
// so a live write is meaningless. This panel reads/writes the dedicated
// /api/settings/envfile endpoint (persist to .env -> applies on restart), with
// its OWN fetch/save state separate from the global-config draft. Each row shows
// the saved .env value (editable) + the live effective value + an "applies on
// restart" chip when they differ; foundation paths are validated on save.

const APPLIES_LABEL: Record<string, string> = {
  restart: "applies on restart",
  "restart-recommended": "restart recommended",
  "cli-tool": "CLI tools only",
  informational: "informational",
};

export function EnvironmentSettings() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery<EnvfileState>({
    queryKey: ["envfile"],
    queryFn: ({ signal }) => apiGet<EnvfileState>("/api/settings/envfile", signal),
  });

  // local edits keyed by var name; only dirty fields are submitted.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [rejected, setRejected] = useState<Record<string, string>>({});

  const dirty = useMemo(() => Object.keys(edits), [edits]);

  const saveMut = useMutation<EnvfileSaveResult, Error, Record<string, string>>({
    mutationFn: (updates) =>
      apiPost<EnvfileSaveResult>("/api/settings/envfile", { updates }),
    onSuccess: (res) => {
      if (!res.ok) {
        setRejected(res.rejected || {});
        toast.error("Some values were rejected — nothing was saved.");
        return;
      }
      setRejected({});
      setEdits({});
      (res.warnings || []).forEach((w) => toast.warning(w));
      toast.success("Saved to .env — restart the service to apply.");
      qc.invalidateQueries({ queryKey: ["envfile"] });
    },
    onError: (err) => {
      // a 400 from validation throws ApiError with the parsed body attached.
      const body =
        err instanceof ApiError ? (err.body as EnvfileSaveResult | undefined) : undefined;
      if (body?.rejected) setRejected(body.rejected);
      toast.error("Save failed — nothing was written.");
    },
  });

  if (isLoading) {
    return (
      <SettingSection label="Environment (restart required)">
        <SettingRow
          label="Loading…"
          control={<span className="text-ink-3 text-sm">Reading .env…</span>}
        />
      </SettingSection>
    );
  }
  if (isError || !data) {
    return (
      <SettingSection label="Environment (restart required)">
        <SettingRow
          label="Unavailable"
          control={
            <span className="text-amber-600 text-sm">
              Could not load the .env editor.
            </span>
          }
        />
      </SettingSection>
    );
  }

  return (
    <SettingSection label="Environment (restart required)">
      <div className="px-4 py-3">
        <Callout tone="info" title="These take effect after a service restart">
          Edits here are persisted to <code>{data.path}</code> and read at the next
          boot. The running process keeps its current values until restarted.
          Call-time path roots are restart-recommended (a mid-run change splits
          state between the old and new root). Foundation paths are validated on
          save — a bad value can prevent startup.
        </Callout>
      </div>

      {data.env.map((row) => {
        const editedVal = edits[row.name];
        const value = editedVal !== undefined ? editedVal : row.saved ?? "";
        const rej = rejected[row.name];
        const desc = [
          row.applies_note,
          row.danger ? row.danger_note : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <SettingRow
            key={row.name}
            label={row.name}
            hint={desc}
            danger={row.danger}
            stacked
            control={
              <div className="flex flex-col items-end gap-1">
                <Input
                  aria-label={row.name}
                  value={value}
                  data-foundation={row.foundation ? "1" : undefined}
                  data-danger={row.danger ? "1" : undefined}
                  onChange={(e) =>
                    setEdits((m) => ({ ...m, [row.name]: e.target.value }))
                  }
                  className="w-72 font-mono text-xs"
                />
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="rounded bg-surface-2 px-1.5 py-0.5 text-ink-3">
                    {APPLIES_LABEL[row.applies] ?? row.applies}
                  </span>
                  {row.restart_pending && (
                    <span
                      data-testid={`pending-${row.name}`}
                      className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700"
                    >
                      restart pending
                    </span>
                  )}
                  {row.effective != null && (
                    <span className="text-ink-3">
                      live: <code>{row.effective}</code>
                    </span>
                  )}
                </div>
                {rej && (
                  <span
                    data-testid={`reject-${row.name}`}
                    className="text-[11px] text-red-600"
                  >
                    {rej}
                  </span>
                )}
              </div>
            }
          />
        );
      })}

      <div className="flex items-center justify-end gap-3 px-4 py-3">
        {dirty.length > 0 && (
          <span className="text-ink-3 text-xs">{dirty.length} unsaved</span>
        )}
        <Button
          disabled={dirty.length === 0 || saveMut.isPending}
          onClick={() => saveMut.mutate(edits)}
        >
          Save to .env
        </Button>
      </div>
    </SettingSection>
  );
}
