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
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiGet, apiPost } from "@/lib/api-client";
import type { SitesV2 } from "@/lib/api-types";
import {
  fetchSceneCrawlStatus,
  sceneCrawlView,
  startSceneCrawl,
  type SceneCrawlStatus,
} from "@/lib/guidedCapture";

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
  const [selectedSite, setSelectedSite] = useState("");
  const [listingOverride, setListingOverride] = useState<string | null>(null);
  const [newestOverride, setNewestOverride] = useState<number | null>(null);
  const [maxPagesOverride, setMaxPagesOverride] = useState<number | null>(null);
  const [maxScrollsOverride, setMaxScrollsOverride] = useState<number | null>(null);
  const [delayOverride, setDelayOverride] = useState<number | null>(null);
  const [titleLimitOverride, setTitleLimitOverride] = useState<number | null>(null);
  const perPage = 50;

  const sites = useQuery({
    queryKey: ["sites-v2", "scene-discovery"],
    queryFn: () => apiGet<SitesV2>("/api/sites/v2"),
  });

  const siteRows = Array.isArray(sites.data?.sites) ? sites.data.sites : [];
  useEffect(() => {
    if (!siteRows.length) {
      setSelectedSite("");
      return;
    }
    if (!siteRows.some((site) => site.site_id === selectedSite)) {
      setSelectedSite(siteRows[0].site_id);
    }
  }, [siteRows, selectedSite]);

  useEffect(() => {
    setListingOverride(null);
    setNewestOverride(null);
    setMaxPagesOverride(null);
    setMaxScrollsOverride(null);
    setDelayOverride(null);
    setTitleLimitOverride(null);
  }, [selectedSite]);

  const crawlStatus = useQuery({
    queryKey: ["scene-crawl-status", selectedSite],
    enabled: Boolean(selectedSite),
    queryFn: () => fetchSceneCrawlStatus(selectedSite),
    refetchInterval: (query) =>
      (query.state.data as SceneCrawlStatus | undefined)?.state === "RUNNING"
        ? 1_500
        : false,
  });

  const defaults = crawlStatus.data?.defaults;
  const listingUrl = listingOverride ?? defaults?.listing_url ?? "";
  const newestN = newestOverride ?? defaults?.newest_n ?? 50;
  const maxPages = maxPagesOverride ?? defaults?.max_pages ?? 5;
  const maxScrolls = maxScrollsOverride ?? defaults?.max_scrolls ?? 8;
  const delayS = delayOverride ?? defaults?.delay_s ?? 1;
  const titleFetchLimit = titleLimitOverride ?? defaults?.title_fetch_limit ?? 50;

  const crawl = useMutation({
    mutationFn: () =>
      startSceneCrawl({
        site_id: selectedSite,
        listing_url: listingUrl.trim(),
        newest_n: newestN,
        max_pages: maxPages,
        max_scrolls: maxScrolls,
        delay_s: delayS,
        title_fetch_limit: titleFetchLimit,
      }),
    onSuccess: (result) => {
      qc.setQueryData<SceneCrawlStatus>(
        ["scene-crawl-status", selectedSite],
        (previous) => ({
          discovered: 0,
          queued: 0,
          pages_walked: 0,
          zero_scenes_found: false,
          ...previous,
          ...result,
        }),
      );
      toast.success("Scene discovery started");
    },
    onError: (error) => toast.error(`Couldn't start discovery: ${String(error)}`),
  });

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
            Scan local captures, or crawl an authenticated site library and queue its
            discovered scene URLs.
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
      <section className="space-y-2 rounded-md hairline bg-surface-2/40 p-3">
        <div>
          <div className="text-[13px] font-medium text-ink">Scene discovery</div>
          <p className="text-[12px] text-ink-3">
            Uses the site&apos;s stored session, scrolls and paginates autonomously,
            then queues new scene URLs through the normal queue.
          </p>
        </div>

        {siteRows.length === 0 ? (
          <div className="text-[12px] text-ink-3">
            Add a site before discovering scenes.
          </div>
        ) : (
          <>
            <div className="grid gap-2 sm:grid-cols-[minmax(9rem,0.7fr)_minmax(16rem,1.7fr)_8rem]">
              <label className="space-y-1 text-[11px] font-medium text-ink-2">
                Site
                <select
                  aria-label="Site for scene discovery"
                  value={selectedSite}
                  onChange={(event) => setSelectedSite(event.target.value)}
                  className="hairline flex h-10 w-full rounded-md bg-surface px-2 text-sm"
                >
                  {siteRows.map((site) => (
                    <option key={site.site_id} value={site.site_id}>
                      {site.name || site.site_id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-[11px] font-medium text-ink-2">
                Library listing URL
                <Input
                  type="url"
                  value={listingUrl}
                  onChange={(event) => setListingOverride(event.target.value)}
                  placeholder="https://example.com/members/videos"
                />
              </label>
              <label className="space-y-1 text-[11px] font-medium text-ink-2">
                Newest scenes
                <Input
                  type="number"
                  min={0}
                  step={1}
                  value={newestN}
                  onChange={(event) =>
                    setNewestOverride(Math.max(0, Number(event.target.value) || 0))
                  }
                />
              </label>
            </div>

            <details className="text-[12px] text-ink-3">
              <summary className="cursor-pointer select-none font-medium text-ink-2">
                Crawl limits and request pacing
              </summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-4">
                <label className="space-y-1">
                  Pages per run
                  <Input
                    aria-label="Pages per discovery run"
                    type="number"
                    min={1}
                    max={500}
                    value={maxPages}
                    onChange={(event) =>
                      setMaxPagesOverride(Math.max(1, Number(event.target.value) || 1))
                    }
                  />
                </label>
                <label className="space-y-1">
                  Scroll steps
                  <Input
                    aria-label="Scroll steps per page"
                    type="number"
                    min={0}
                    max={50}
                    value={maxScrolls}
                    onChange={(event) =>
                      setMaxScrollsOverride(Math.max(0, Number(event.target.value) || 0))
                    }
                  />
                </label>
                <label className="space-y-1">
                  Delay (seconds)
                  <Input
                    aria-label="Delay between requests"
                    type="number"
                    min={0.1}
                    max={30}
                    step={0.1}
                    value={delayS}
                    onChange={(event) =>
                      setDelayOverride(Math.max(0.1, Number(event.target.value) || 0.1))
                    }
                  />
                </label>
                <label className="space-y-1">
                  Scene titles fetched
                  <Input
                    aria-label="Scene title fetch limit"
                    type="number"
                    min={0}
                    max={1000}
                    value={titleFetchLimit}
                    onChange={(event) =>
                      setTitleLimitOverride(Math.max(0, Number(event.target.value) || 0))
                    }
                  />
                </label>
              </div>
              <p className="mt-1">Set Newest scenes to 0 for a resumable whole-library walk.</p>
            </details>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <div
                className={
                  crawlStatus.data && sceneCrawlView(crawlStatus.data).tone === "warning"
                    ? "text-[12px] text-amber-dim"
                    : crawlStatus.data && sceneCrawlView(crawlStatus.data).tone === "danger"
                      ? "text-[12px] text-red"
                      : "text-[12px] text-ink-3"
                }
                role={crawlStatus.data?.state === "NOT_LOGGED_IN" ? "alert" : undefined}
              >
                {crawlStatus.data
                  ? sceneCrawlView(crawlStatus.data).label
                  : "Loading discovery state…"}
              </div>
              {crawlStatus.data && (
                <Button
                  size="sm"
                  onClick={() => crawl.mutate()}
                  disabled={
                    crawl.isPending ||
                    crawlStatus.data.state === "RUNNING" ||
                    !listingUrl.trim()
                  }
                >
                  {crawl.isPending || crawlStatus.data.state === "RUNNING"
                    ? "Discovering…"
                    : "Discover scenes"}
                </Button>
              )}
            </div>
          </>
        )}
      </section>

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
