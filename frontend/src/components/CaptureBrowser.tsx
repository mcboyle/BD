// Item 3 — capture SCAN + browser (read-only).
//
// Discovers ALL capture artifacts under ~/BulkDownloader, including the hundreds
// of onboarding captures nested under captures/template_onboarding/… that the
// flat analyzer list never surfaced. The expensive recursive walk runs ONLY when
// the operator clicks "Scan for captures" (POST /api/captures/scan); the list
// (GET /api/captures) serves the cached inventory and opens no zips.
//
//   POST /api/captures/scan   — (re)build the inventory, return a summary
//   GET  /api/captures        — cached inventory, paginated + filterable
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";

interface CaptureRow {
  rel_path: string;
  name: string;
  dir: string;
  host: string | null;
  captured_at: number;
  size: number;
  kind: string;
  redacted: boolean;
}
interface ScanSummary {
  ok: boolean;
  total: number;
  by_host: Record<string, number>;
  new_since_last: number;
  took_ms: number;
}
interface CapturesList {
  ok: boolean;
  scanned: boolean;
  built_at: number | null;
  total: number;
  page: number;
  per_page: number;
  captures: CaptureRow[];
  summary: ScanSummary | null;
}

function fmtBytes(n: number): string {
  if (n < 0) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}
function fmtWhen(ts: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

export function CaptureBrowser() {
  const qc = useQueryClient();
  const [hostFilter, setHostFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  const perPage = 50;

  const list = useQuery({
    queryKey: ["captures-list", hostFilter, page],
    queryFn: () => {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      });
      if (hostFilter) params.set("host", hostFilter);
      return apiGet<CapturesList>(`/api/captures?${params.toString()}`);
    },
  });

  const scan = useMutation({
    mutationFn: () => apiPost<ScanSummary>("/api/captures/scan", {}),
    onSuccess: (s) => {
      const hosts = Object.keys(s.by_host || {}).length;
      toast.success(
        `Scanned ${s.total} capture${s.total === 1 ? "" : "s"} across ${hosts} host${
          hosts === 1 ? "" : "s"
        } · ${s.new_since_last} new`,
      );
      setPage(1);
      qc.invalidateQueries({ queryKey: ["captures-list"] });
    },
    onError: (e) => toast.error(`Scan failed: ${String(e)}`),
  });

  // Per-capture actions (Item 3 follow-on). Both resolve the capture by its
  // rel_path token server-side; build_draft writes a REVIEW-ONLY draft, scrub
  // emits the share-ready redacted twin (raw capture untouched).
  const buildDraft = useMutation({
    mutationFn: (token: string) =>
      apiPost<{ ok: boolean; host?: string; draft?: string; error?: string }>(
        "/api/captures/build_draft",
        { token },
      ),
    onSuccess: (r) =>
      r.ok
        ? toast.success(`Draft built for ${r.host} — review it in the Template Workbench`)
        : toast.error(`Build failed: ${r.error || "unknown"}`),
    onError: (e) => toast.error(`Build failed: ${String(e)}`),
  });

  const scrub = useMutation({
    mutationFn: (token: string) =>
      apiPost<{
        ok: boolean;
        result?: { ran?: boolean; reason?: string; status?: string; redaction_total?: number };
      }>("/api/captures/scrub", { token }),
    onSuccess: (r) => {
      const res = r.result || {};
      if (res.ran)
        toast.success(
          `Scrubbed → redacted twin (${res.status || "done"}` +
            (res.redaction_total ? `, ${res.redaction_total} redactions)` : ")"),
        );
      else toast.message(`Scrub skipped: ${res.reason || "not run"}`);
      qc.invalidateQueries({ queryKey: ["captures-list"] });
    },
    onError: (e) => toast.error(`Scrub failed: ${String(e)}`),
  });

  const data = list.data;
  const summary = data?.summary || null;
  const hosts = summary ? Object.keys(summary.by_host || {}).sort() : [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / perPage)) : 1;

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[13px] font-medium text-ink">Capture browser</div>
          <p className="text-[12px] text-ink-3">
            Find every capture under your install — including onboarding captures in
            subfolders that the picker above doesn&apos;t list. Read-only.
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
        >
          {scan.isPending ? "Scanning…" : "Scan for captures"}
        </Button>
      </div>

      <div className="rounded-md hairline bg-primary/5 p-2 text-[12px] text-ink-2">
        Need to discover today&apos;s download control?{" "}
        <Link to="/capture" className="font-medium text-accent underline-offset-2 hover:underline">
          Learn from a live page
        </Link>{" "}
        in the existing Capture workflow.
      </div>

      {data && !data.scanned && (
        <div className="rounded-md hairline p-2 text-[12px] text-ink-3">
          No scan yet — click <strong>Scan for captures</strong> to build the inventory.
        </div>
      )}

      {summary && (
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
          <span>
            {summary.total} capture{summary.total === 1 ? "" : "s"} ·{" "}
            {hosts.length} host{hosts.length === 1 ? "" : "s"}
            {data?.built_at ? ` · scanned ${fmtWhen(data.built_at)}` : ""}
          </span>
          {hosts.length > 0 && (
            <select
              value={hostFilter}
              onChange={(e) => {
                setHostFilter(e.target.value);
                setPage(1);
              }}
              className="rounded hairline bg-surface-2 px-2 py-1 text-[12px]"
            >
              <option value="">all hosts</option>
              {hosts.map((h) => (
                <option key={h} value={h}>
                  {h} ({summary.by_host[h]})
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {data && data.captures.length > 0 && (
        <div className="space-y-1">
          {data.captures.map((c) => (
            <div
              key={c.rel_path}
              className="flex items-center justify-between gap-2 rounded-md hairline px-2 py-1 text-[12px]"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-ink-2">{c.name}</div>
                <div className="text-ink-3">
                  {c.host || "(no host)"} · {fmtWhen(c.captured_at)} · {fmtBytes(c.size)}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Badge variant="outline">{c.kind}</Badge>
                <Badge variant={c.redacted ? "outline" : "destructive"}>
                  {c.redacted ? "redacted" : "raw"}
                </Badge>
                {c.kind === "wacz" && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={buildDraft.isPending}
                      onClick={() => buildDraft.mutate(c.rel_path)}
                      title="Build a review-only template draft from this capture"
                    >
                      Build draft
                    </Button>
                    {!c.redacted && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={scrub.isPending}
                        onClick={() => scrub.mutate(c.rel_path)}
                        title="Produce a share-ready redacted twin (raw capture untouched)"
                      >
                        Scrub
                      </Button>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {data && data.scanned && data.total > perPage && (
        <div className="flex items-center justify-between gap-2 text-[12px] text-ink-3">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            Prev
          </Button>
          <span>
            page {page} / {totalPages}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </Card>
  );
}
