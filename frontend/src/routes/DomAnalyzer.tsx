// DomAnalyzer — DOM Analyzer Workbench (F2.6). The REPLAY half of the dev-tools
// loop (F2.7 is the live half): load an existing capture, browse its REDACTED
// DOM offline, test selectors against the captured DOM, and pin a review-only
// template candidate. Five endpoints, all full /api/ literals so the parity
// scanner credits them spa_wired:
//
//   GET  /api/analyzer/captures            — list available captures
//   POST /api/analyzer/load                — gated tree + html + redaction proof
//   POST /api/analyzer/tree                — depth/breadth-limited tree (big DOMs)
//   POST /api/analyzer/test                — selectors vs the CAPTURED dom (offline,
//                                            not /api/playground/test's live fetch)
//   POST /api/analyzer/pin                 — review-only draft (never enabled)
//
// The tree renders from the redacted JSON node tree — never innerHTML of the
// captured markup — so captured DOM can't execute in the operator's browser.
// Every DOM the server emits has passed the layered, fail-closed F2 gate; the
// redaction badge surfaces that state (clean / no-DOM / held).
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { CaptureBrowser } from "@/components/CaptureBrowser";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";

interface CaptureRow {
  name: string;
  dir: string;
  // BUG-3/7: rel_path is the recursive (subfolder-aware) resolve token the
  // picker endpoint (scan_captures) returns; send it so onboarding/guided
  // captures nested in subfolders both list AND load. Falls back to name.
  rel_path?: string;
  size: number;
  kind: string;
}
interface CapturesResult {
  ok: boolean;
  captures: CaptureRow[];
}

type TreeNode =
  | { type: "text"; text: string }
  | {
      type: "element";
      tag: string;
      id?: string | null;
      classes?: string[];
      attrs?: Record<string, unknown>;
      redacted?: string | null;
      children?: TreeNode[];
      truncated?: boolean;
    }
  | { type: "fragment"; children?: TreeNode[]; truncated?: boolean };

interface LoadResult {
  ok: boolean;
  has_dom: boolean;
  residual_count: number;
  residual_kinds: Record<string, number>;
  tree: TreeNode | null;
  html: string | null;
  capture?: string;
  host?: string;
  note?: string;
  error?: string;
}
interface SelectorTestRow {
  selector: string;
  count?: number;
  sample?: string;
  error?: string;
}
interface TestResult {
  ok: boolean;
  results?: SelectorTestRow[];
  error?: string;
}
interface PinResult {
  ok: boolean;
  file?: string;
  status?: string;
  enabled?: boolean;
  error?: string;
}

// Mirror of dom_analyzer.candidate_selector_for — same preference order, so the
// operator sees the selector the server would derive. Re-validated by /test.
function candidateSelector(n: Extract<TreeNode, { type: "element" }>): string {
  const tag = (n.tag || "*").toLowerCase();
  const attrs = (n.attrs || {}) as Record<string, string>;
  const hashy = /(?:[0-9a-f]{6,}|\d{4,}|--|__\d)/i;
  if (n.id && !hashy.test(n.id)) return `${tag}#${n.id}`;
  const name = attrs["name"];
  if (name && !hashy.test(name)) return `${tag}[name="${name}"]`;
  for (const k of Object.keys(attrs)) {
    if (k.startsWith("data-") && attrs[k] && !hashy.test(String(attrs[k])))
      return `${tag}[${k}="${attrs[k]}"]`;
  }
  if (tag === "input" && attrs["type"]) return `input[type="${attrs["type"]}"]`;
  const volatile = /^(?:js|is|has|u|ng|v|x|aos)-|^(?:fade|sr-only|visually-hidden|active|open|show|hidden|selected|disabled)$|[0-9a-f]{5,}/i;
  const stable = (n.classes || []).filter((c) => c && !volatile.test(c)).slice(0, 2);
  if (stable.length) return `${tag}.${stable.join(".")}`;
  return tag;
}

function TreeView({
  node,
  depth,
  onPick,
  picked,
}: {
  node: TreeNode;
  depth: number;
  onPick: (n: Extract<TreeNode, { type: "element" }>) => void;
  picked: Extract<TreeNode, { type: "element" }> | null;
}) {
  const [open, setOpen] = useState(depth < 2);
  if (node.type === "text") {
    return <div className="py-0.5 pl-4 font-mono text-[11px] text-ink-3">“{node.text}”</div>;
  }
  const children = node.type === "element" || node.type === "fragment" ? node.children || [] : [];
  if (node.type === "fragment") {
    return (
      <div>
        {children.map((c, i) => (
          <TreeView key={i} node={c} depth={depth} onPick={onPick} picked={picked} />
        ))}
      </div>
    );
  }
  const el = node;
  const isPicked = picked === el;
  const label =
    `${el.tag}` +
    (el.id ? `#${el.id}` : "") +
    ((el.classes || []).length ? "." + (el.classes || []).join(".") : "");
  return (
    <div style={{ marginLeft: depth ? 12 : 0 }}>
      <div className="flex items-center gap-1">
        {children.length > 0 ? (
          <button
            onClick={() => setOpen((o) => !o)}
            className="h-4 w-4 shrink-0 rounded text-[10px] text-ink-3 hover:bg-surface-2"
            aria-label={open ? "Collapse" : "Expand"}
          >
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="inline-block h-4 w-4" />
        )}
        <button
          onClick={() => onPick(el)}
          className={
            "rounded px-1 py-0.5 text-left font-mono text-[12px] hover:bg-surface-2 " +
            (isPicked ? "bg-primary-soft text-primary" : "text-ink")
          }
        >
          {label}
          {el.redacted ? (
            <span className="ml-1 rounded bg-amber-soft px-1 text-[10px] text-amber-dim">
              {el.redacted}
            </span>
          ) : null}
        </button>
      </div>
      {open
        ? children.map((c, i) => (
            <TreeView key={i} node={c} depth={depth + 1} onPick={onPick} picked={picked} />
          ))
        : null}
      {el.truncated ? (
        <div className="pl-5 text-[11px] text-ink-3">… more children (load full view)</div>
      ) : null}
    </div>
  );
}

export function DomAnalyzer() {
  const [capture, setCapture] = useState("");
  const [picked, setPicked] = useState<Extract<TreeNode, { type: "element" }> | null>(null);
  const [selectors, setSelectors] = useState("");
  const [role, setRole] = useState("download");
  const [partName, setPartName] = useState("button");

  const capturesQ = useQuery({
    queryKey: ["analyzer", "captures"],
    queryFn: () => apiGet<CapturesResult>("/api/analyzer/captures"),
  });

  const loadM = useMutation({
    mutationFn: (name: string) => apiPost<LoadResult>("/api/analyzer/load", { capture: name }),
    onSuccess: (d) => {
      setPicked(null);
      if (!d.ok) toast.error(d.note || d.error || "Load failed");
    },
    onError: () => toast.error("Could not load capture"),
  });

  // Depth-limited view for very large captures — wires /api/analyzer/tree.
  const treeM = useMutation({
    mutationFn: (name: string) =>
      apiPost<LoadResult>("/api/analyzer/tree", { capture: name, max_depth: 6, max_children: 40 }),
    onError: () => toast.error("Could not load limited tree"),
  });

  const testM = useMutation({
    mutationFn: (sels: string[]) =>
      apiPost<TestResult>("/api/analyzer/test", { capture, selectors: sels }),
    onError: () => toast.error("Selector test failed"),
  });

  const pinM = useMutation({
    mutationFn: (sel: string) =>
      apiPost<PinResult>("/api/analyzer/pin", {
        capture,
        selector: sel,
        role,
        name: partName,
        host: loadM.data?.host || undefined,
      }),
    onSuccess: (d) => {
      if (d.ok) toast.success(`Pinned to ${d.file} — review required before enabling`);
      else toast.error(d.error || "Pin failed");
    },
    onError: () => toast.error("Pin failed"),
  });

  const loaded = loadM.data && loadM.data.ok ? loadM.data : null;
  const cand = useMemo(() => (picked ? candidateSelector(picked) : ""), [picked]);

  function doLoad(name: string) {
    setCapture(name);
    setPicked(null);
    if (name) loadM.mutate(name);
  }

  return (
    <AppShell
      title="DOM analyzer"
      subtitle="Inspect a captured DOM offline · test selectors · pin a review-only candidate"
    >
      <div className="mx-auto max-w-5xl space-y-4 p-4">
        <p className="text-[12px] text-ink-3">
          Open an existing capture, browse its redacted DOM, test selectors against the
          captured page, then pin a candidate. Every capture is redaction-checked before its
          DOM is shown; nothing is enabled without your review.
        </p>

        <CaptureBrowser />

        {/* ── Capture picker ── */}
        <Card className="space-y-3 p-4">
          <label className="block">
            <span className="text-[12px] font-medium text-ink">Capture</span>
            <select
              value={capture}
              onChange={(e) => doLoad(e.target.value)}
              className="mt-1 w-full rounded border border-hairline bg-surface-2 p-2 text-sm text-ink"
            >
              <option value="">Select a capture…</option>
              {(capturesQ.data?.captures || []).map((c) => (
                <option key={c.rel_path ?? c.name} value={c.rel_path ?? c.name}>
                  {c.name} · {c.dir}
                </option>
              ))}
            </select>
          </label>
          {capturesQ.data && (capturesQ.data.captures || []).length === 0 ? (
            <p className="text-[12px] text-ink-3">
              No captures found. Run a capture session first, then return here.
            </p>
          ) : null}
        </Card>

        {/* ── Redaction proof badge (signature element) ── */}
        {loadM.data ? <RedactionBadge d={loadM.data} /> : null}

        {loaded && loaded.has_dom ? (
          <div className="grid gap-4 md:grid-cols-2">
            {/* ── DOM tree ── */}
            <Card className="space-y-2 overflow-auto p-4" style={{ maxHeight: 520 }}>
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium text-ink">DOM tree</span>
                <button
                  onClick={() => capture && treeM.mutate(capture)}
                  className="text-[11px] text-ink-3 underline hover:text-ink"
                >
                  Limited view
                </button>
              </div>
              {(treeM.data?.ok && treeM.data.tree ? treeM.data.tree : loaded.tree) ? (
                <TreeView
                  node={(treeM.data?.ok && treeM.data.tree ? treeM.data.tree : loaded.tree) as TreeNode}
                  depth={0}
                  onPick={setPicked}
                  picked={picked}
                />
              ) : null}
            </Card>

            {/* ── Inspect / test / pin ── */}
            <div className="space-y-4">
              <Card className="space-y-2 p-4">
                <span className="text-[12px] font-medium text-ink">Selected element</span>
                {picked ? (
                  <>
                    <div className="rounded bg-surface-2 p-2 font-mono text-[12px] text-primary">
                      {cand}
                    </div>
                    <div className="text-[11px] text-ink-3 pt-1">
                      Host:{" "}
                      <span className="text-ink">
                        {loadM.data?.host || "(none derivable — draft needs a host)"}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2 pt-1">
                      <Button
                        variant="outline"
                        onClick={() => setSelectors((s) => (s ? s + "\n" + cand : cand))}
                      >
                        Add to test
                      </Button>
                      <Button onClick={() => pinM.mutate(cand)} disabled={pinM.isPending}>
                        Pin candidate
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="text-[12px] text-ink-3">
                    Pick an element in the tree to derive a selector.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-2 pt-1">
                  <label className="block">
                    <span className="text-[11px] text-ink-3">Role</span>
                    <input
                      value={role}
                      onChange={(e) => setRole(e.target.value)}
                      className="mt-1 w-full rounded border border-hairline bg-surface-2 p-1.5 text-[12px] text-ink"
                    />
                  </label>
                  <label className="block">
                    <span className="text-[11px] text-ink-3">Name</span>
                    <input
                      value={partName}
                      onChange={(e) => setPartName(e.target.value)}
                      className="mt-1 w-full rounded border border-hairline bg-surface-2 p-1.5 text-[12px] text-ink"
                    />
                  </label>
                </div>
              </Card>

              <Card className="space-y-2 p-4">
                <span className="text-[12px] font-medium text-ink">Test selectors</span>
                <textarea
                  value={selectors}
                  onChange={(e) => setSelectors(e.target.value)}
                  rows={3}
                  placeholder="one selector per line"
                  className="w-full rounded border border-hairline bg-surface-2 p-2 font-mono text-[12px] text-ink"
                />
                <Button
                  variant="outline"
                  disabled={testM.isPending || !selectors.trim()}
                  onClick={() =>
                    testM.mutate(selectors.split("\n").map((s) => s.trim()).filter(Boolean))
                  }
                >
                  Run against captured DOM
                </Button>
                {testM.data?.results ? (
                  <ul className="space-y-1 pt-1">
                    {testM.data.results.map((r, i) => (
                      <li key={i} className="font-mono text-[12px]">
                        <span className={r.count ? "text-green" : "text-ink-3"}>
                          {r.error ? "err" : `${r.count ?? 0}×`}
                        </span>{" "}
                        <span className="text-ink">{r.selector}</span>
                        {r.error ? <span className="text-amber-dim"> — {r.error}</span> : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </Card>
            </div>
          </div>
        ) : null}

        {loaded && !loaded.has_dom ? (
          <Card className="p-4 text-[12px] text-ink-3">
            This capture has no DOM snapshot — common for iframe-player captures, where the
            signal is in the network ladder rather than the DOM.
          </Card>
        ) : null}
      </div>
    </AppShell>
  );
}

function RedactionBadge({ d }: { d: LoadResult }) {
  if (!d.ok) {
    const kinds = Object.entries(d.residual_kinds || {})
      .map(([k, n]) => `${n} ${k}`)
      .join(", ");
    return (
      <Card className="border border-hairline bg-red-soft p-3">
        <div className="text-[12px] font-medium text-red">Redaction gate held — DOM withheld</div>
        <div className="text-[11px] text-ink-3">
          {d.residual_count} residual value(s) survived redaction ({kinds || "by kind"}); the tree
          is not shown. This capture should not be inspected here — report it.
        </div>
      </Card>
    );
  }
  if (!d.has_dom) {
    return (
      <Card className="border border-hairline bg-amber-soft p-3 text-[12px] text-amber-dim">
        No DOM in this capture (iframe player or aborted session).
      </Card>
    );
  }
  return (
    <Card className="border border-hairline bg-green-soft p-3 text-[12px] text-green">
      DOM redacted · proven clean (0 residual) — safe to inspect.
    </Card>
  );
}
