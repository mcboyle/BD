// v3.66.719 (Cut 8) -- the control surface for the exec bridge.
//
// 717 built the validated, allowlisted bridge (tool_bridge). Its endpoints were live
// but had NO GUI -- /api/tools/run was classified in the reachability ledger as an
// exec-bridge endpoint awaiting its control. This is that control.
//
// The allowlist is FETCHED from /api/tools/available, never hardcoded: a second copy
// in the frontend would drift from tool_bridge.ALLOWLIST the instant a tool is added
// or removed. Every input is rendered FROM the server's per-flag spec (bool -> switch,
// enum -> select, path/str -> text), so the UI cannot offer a flag the bridge would
// reject -- the validation contract and the control are the same source of truth.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiGet, apiPost } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Callout } from "@/components/ui/Callout";

interface FlagSpec {
  name: string;
  type: "bool" | "int" | "enum" | "str" | "path";
  choices?: string[];
  positional?: boolean;
  max_len?: number;
}
interface ToolSpec {
  name: string;
  desc: string;
  flags: FlagSpec[];
}
interface AvailableResp {
  ok: boolean;
  tools: ToolSpec[];
}
interface RunResp {
  ok: boolean;
  returncode: number | null;
  stdout: string;
  stderr: string;
  argv: string[];
  timed_out: boolean;
  error?: string;
}

export function Tools() {
  const toolsQ = useQuery<AvailableResp>({
    queryKey: ["tools-available"],
    queryFn: ({ signal }) => apiGet<AvailableResp>("/api/tools/available", signal),
  });

  const [selected, setSelected] = useState<string>("");
  // per-flag input values, keyed by flag name
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [result, setResult] = useState<RunResp | null>(null);

  const runMut = useMutation<RunResp, Error, { tool: string; flags: Record<string, unknown> }>({
    mutationFn: (body) => apiPost<RunResp>("/api/tools/run", body),
    onSuccess: (r) => {
      setResult(r);
      if (r.returncode === 0) toast.success(`${selected} exited 0`);
      else if (r.timed_out) toast.error(`${selected} timed out`);
      else toast.error(`${selected} exited ${r.returncode}`);
    },
    onError: (e) => toast.error(`Refused: ${e.message}`),
  });

  const tools = toolsQ.data?.tools ?? [];
  const current = tools.find((t) => t.name === selected);

  function pick(name: string) {
    setSelected(name);
    setValues({});
    setResult(null);
  }

  function run() {
    if (!current) return;
    // build the flags object from the values, dropping empty non-bool inputs so the
    // bridge sees only what the operator actually set.
    const flags: Record<string, unknown> = {};
    for (const f of current.flags) {
      const v = values[f.name];
      if (f.type === "bool") {
        if (v === true) flags[f.name] = true;
      } else if (v !== undefined && v !== "") {
        flags[f.name] = v;
      }
    }
    runMut.mutate({ tool: current.name, flags });
  }

  return (
    <div className="mx-auto max-w-4xl p-4">
      <h1 className="mb-1 text-xl font-semibold text-ink">Tools</h1>
      <p className="mb-3 text-sm text-ink-3">
        Run allowlisted, read-only tools through the validated exec bridge. Only the
        tools and flags below are permitted; every value is validated server-side, no
        shell is ever invoked, and output is capped. This is the one seam between the GUI
        and BD's command-line tooling.
      </p>

      <Callout tone="info" title="Allowlisted and validated">
        This list is fetched from the server allowlist, so it always matches exactly what
        the bridge will accept. A tool or flag not shown here cannot be run from here.
      </Callout>

      {toolsQ.isLoading && <p className="text-ink-3">Loading tools…</p>}
      {toolsQ.isError && (
        <p className="text-danger">Could not load the tool allowlist.</p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {tools.map((t) => (
          <Button
            key={t.name}
            variant={t.name === selected ? "default" : "outline"}
            size="sm"
            onClick={() => pick(t.name)}
          >
            {t.name}
          </Button>
        ))}
      </div>

      {current && (
        <div className="mt-4 rounded border border-border p-3">
          <p className="mb-3 text-xs text-ink-3">{current.desc}</p>
          <div className="space-y-2">
            {current.flags.map((f) => (
              <div key={f.name} className="flex items-center gap-3">
                <label className="w-56 text-sm text-ink">
                  {f.name}
                  <span className="ml-1 text-xs text-ink-3">
                    ({f.type}
                    {f.positional ? ", positional" : ""})
                  </span>
                </label>
                {f.type === "bool" ? (
                  <Button
                    size="sm"
                    variant={values[f.name] === true ? "default" : "outline"}
                    onClick={() =>
                      setValues((s) => ({ ...s, [f.name]: values[f.name] !== true }))
                    }
                  >
                    {values[f.name] === true ? "on" : "off"}
                  </Button>
                ) : f.type === "enum" ? (
                  <select
                    className="rounded border border-border bg-surface px-2 py-1 text-sm text-ink"
                    value={String(values[f.name] ?? "")}
                    onChange={(e) =>
                      setValues((s) => ({ ...s, [f.name]: e.target.value }))
                    }
                  >
                    <option value="">(unset)</option>
                    {(f.choices ?? []).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    value={String(values[f.name] ?? "")}
                    placeholder={f.type === "path" ? "path under an allowed root" : ""}
                    onChange={(e) =>
                      setValues((s) => ({ ...s, [f.name]: e.target.value }))
                    }
                    className="flex-1"
                  />
                )}
              </div>
            ))}
          </div>
          <div className="mt-3">
            <Button onClick={run} disabled={runMut.isPending}>
              {runMut.isPending ? "Running…" : `Run ${current.name}`}
            </Button>
          </div>
        </div>
      )}

      {result && (
        <div className="mt-4 rounded border border-border p-3">
          <p className="mb-2 text-xs text-ink-3">
            argv: <code className="text-ink">{result.argv?.join(" ")}</code>
            {" · "}
            exit {result.timed_out ? "(timed out)" : result.returncode}
          </p>
          {result.stdout && (
            <pre className="max-h-80 overflow-auto rounded bg-surface-2 p-2 text-[11px] text-ink">
              {result.stdout}
            </pre>
          )}
          {result.stderr && (
            <pre className="mt-2 max-h-40 overflow-auto rounded bg-surface-2 p-2 text-[11px] text-ink-3">
              {result.stderr}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default Tools;
