import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { apiGet, apiPost } from "@/lib/api-client";
import type { OkResult, QueueV2, SitesV2 } from "@/lib/api-types";

// PHA-ADDURL (v3.66.328) -- the SPA Add-URL surface that replaces the legacy
// "Load URLs" panel ahead of the Phase C /legacy deletion. Three modes:
//   - Single URL -> POST /api/queue/v2/add_url        {site_id, url}
//   - URL list   -> POST /api/sites/<sid>/load_urls   {text}
//   - Scrape     -> POST /api/scrape_listing          {url}   (v3.66.767)
// Both enqueue modes target the same per-site `load_urls` path the legacy UI
// used, so manual URL queueing survives the legacy deletion. The scrape mode
// (v3.66.767) wires the previously-dark server-side listing scraper: paste a
// listing-page URL, the server returns the video-looking links, and they feed
// straight into the URL-list textarea for review + enqueue -- a real two-step
// flow, not a dead control. FULL /api/... literals (one templated by <sid>)
// are required for the parity scanner to credit these endpoints spa_wired.

type Mode = "single" | "list" | "scrape";

interface SingleResult extends OkResult {
  added?: number;
  dupes?: number;
  skipped?: number;
}

interface ListResult extends OkResult {
  added?: number;
  dupes_skipped?: number;
  already_on_disk?: number;
}

interface ScrapeResult extends OkResult {
  url?: string;
  found?: string[];
  count?: number;
  html_size?: number;
}

function summarize(added?: number, dupes?: number, skipped?: number): string {
  const parts = [`${added ?? 0} added`];
  if (dupes) parts.push(`${dupes} dupe${dupes === 1 ? "" : "s"}`);
  if (skipped) parts.push(`${skipped} already on disk`);
  return parts.join(" \u00b7 ");
}

export function AddUrlDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("single");
  const [siteId, setSiteId] = useState("");
  const [url, setUrl] = useState("");
  const [listText, setListText] = useState("");
  const [scrapeUrl, setScrapeUrl] = useState("");

  // Reuse the same site list the Sites page renders; only load it while the
  // dialog is open so a closed dialog adds no polling.
  const { data: sitesData, isLoading: sitesLoading } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    enabled: open,
  });
  const sites = sitesData?.sites ?? [];

  // Cut 6.7 — bulk-add preview reads the current queue (running + waiting) so it
  // can split pasted URLs into new vs already-queued. Only fetched while the
  // dialog is open and in list mode.
  const { data: queueData } = useQuery<QueueV2>({
    queryKey: ["queue-v2"],
    queryFn: ({ signal }) => apiGet<QueueV2>("/api/queue/v2", signal),
    enabled: open && mode === "list",
  });

  // Default the selector to the first site once they load.
  const effectiveSite = siteId || sites[0]?.site_id || "";

  const reset = () => {
    setUrl("");
    setListText("");
    setScrapeUrl("");
  };

  const afterEnqueue = () => {
    qc.invalidateQueries({ queryKey: ["queue-v2"] });
    qc.invalidateQueries({ queryKey: ["sites-v2"] });
    qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
  };

  const singleMut = useMutation<SingleResult, Error, void>({
    mutationFn: () =>
      apiPost<SingleResult>("/api/queue/v2/add_url", {
        site_id: effectiveSite,
        url: url.trim(),
      }),
    onSuccess: (res) => {
      toast.success(summarize(res.added, res.dupes, res.skipped));
      afterEnqueue();
      reset();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.message || "Add URL failed"),
  });

  const listMut = useMutation<ListResult, Error, void>({
    mutationFn: () =>
      apiPost<ListResult>(`/api/sites/${encodeURIComponent(effectiveSite)}/load_urls`, {
        text: listText,
      }),
    onSuccess: (res) => {
      toast.success(summarize(res.added, res.dupes_skipped, res.already_on_disk));
      afterEnqueue();
      reset();
      onOpenChange(false);
    },
    onError: (e) => toast.error(e.message || "Load URLs failed"),
  });

  // v3.66.767 -- scrape a listing page for video links. This does NOT enqueue;
  // it fetches the listing HTML server-side (SSRF-guarded) and returns the
  // video-looking links, which we drop into the list-mode textarea and switch
  // to "list" so the operator reviews + enqueues them through the normal path.
  const scrapeMut = useMutation<ScrapeResult, Error, void>({
    mutationFn: () =>
      apiPost<ScrapeResult>("/api/scrape_listing", {
        url: scrapeUrl.trim(),
      }),
    onSuccess: (res) => {
      const found = res.found ?? [];
      if (found.length === 0) {
        toast.info("No video links found on that page");
        return;
      }
      // merge with anything already staged, dedup, and hand off to list mode
      const existing = listText.split("\n").map((l) => l.trim()).filter(Boolean);
      const merged = Array.from(new Set([...existing, ...found]));
      setListText(merged.join("\n"));
      setMode("list");
      toast.success(
        `${found.length} link${found.length === 1 ? "" : "s"} found \u2014 review and add`,
      );
    },
    onError: (e) => toast.error(e.message || "Scrape failed"),
  });

  const busy = singleMut.isPending || listMut.isPending || scrapeMut.isPending;
  const noSites = !sitesLoading && sites.length === 0;

  // Cut 6.7 — client-side dedupe preview. A pasted list is split into:
  //   newCount  — unique valid URLs not already in the queue
  //   queued    — unique valid URLs already running/waiting
  //   invalid   — non-empty lines that aren't URLs
  const listPreview = useMemo(() => {
    const queued = new Set<string>();
    for (const r of queueData?.running ?? []) {
      const u = (r as { url?: string })?.url?.trim();
      if (u) queued.add(u);
    }
    for (const w of (queueData?.waiting ?? []) as Array<{ url?: string }>) {
      const u = w?.url?.trim();
      if (u) queued.add(u);
    }
    const valid = new Set<string>();
    let invalid = 0;
    for (const line of listText.split("\n")) {
      const l = line.trim();
      if (!l) continue;
      if (l.startsWith("http")) valid.add(l);
      else invalid += 1;
    }
    let newCount = 0;
    let alreadyQueued = 0;
    for (const u of valid) (queued.has(u) ? alreadyQueued++ : newCount++);
    return { newCount, queued: alreadyQueued, invalid, total: valid.size };
  }, [listText, queueData]);

  // Scrape mode doesn't enqueue, so it doesn't need a site selected; the other
  // two modes do. Its submit gate is a valid listing URL.
  const canSubmit =
    !busy &&
    (mode === "scrape"
      ? scrapeUrl.trim().startsWith("http")
      : !!effectiveSite &&
        (mode === "single" ? url.trim().startsWith("http") : listPreview.total > 0));

  const submit = () => {
    if (!canSubmit) return;
    if (mode === "single") singleMut.mutate();
    else if (mode === "list") listMut.mutate();
    else scrapeMut.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add URLs</DialogTitle>
          <DialogDescription>
            Queue one URL or paste a list. URLs are enqueued on the selected
            site, the same path the importer uses.
          </DialogDescription>
        </DialogHeader>

        {noSites ? (
          <p className="text-sm text-ink-3">
            No sites configured yet. Add a site first, then come back to queue
            URLs.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {/* mode toggle */}
            <div className="flex gap-1 rounded-md bg-surface-2 p-1">
              {(["single", "list", "scrape"] as Mode[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={
                    "flex-1 rounded-sm px-3 py-1.5 text-sm " +
                    (mode === m
                      ? "bg-surface text-ink shadow-sm"
                      : "text-ink-3 hover:text-ink")
                  }
                >
                  {m === "single"
                    ? "Single URL"
                    : m === "list"
                      ? "URL list"
                      : "Scrape listing"}
                </button>
              ))}
            </div>

            {/* site selector -- only the enqueue modes need a target site */}
            {mode !== "scrape" && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-ink-2">Site</span>
                <select
                  value={effectiveSite}
                  onChange={(e) => setSiteId(e.target.value)}
                  disabled={sitesLoading}
                  className="hairline rounded-md bg-surface px-2 py-1.5 text-sm"
                >
                  {sites.map((s) => (
                    <option key={s.site_id} value={s.site_id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            {mode === "scrape" ? (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-ink-2">Listing page URL</span>
                <Input
                  value={scrapeUrl}
                  onChange={(e) => setScrapeUrl(e.target.value)}
                  placeholder="https://example.com/videos"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submit();
                  }}
                />
                <span className="text-xs text-ink-3">
                  Fetches the page server-side and lists the video-looking links
                  it finds. JS-rendered listings may return nothing &mdash; use
                  the browser extension&apos;s scrape action for those.
                </span>
              </label>
            ) : mode === "single" ? (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-ink-2">URL</span>
                <Input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://..."
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submit();
                  }}
                />
              </label>
            ) : (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-ink-2">
                  URLs (one per line)
                  {listText.trim() ? (
                    <span className="ml-1 text-ink-3">
                      &middot;{" "}
                      {[
                        `${listPreview.newCount} new`,
                        listPreview.queued > 0
                          ? `${listPreview.queued} already queued`
                          : null,
                        listPreview.invalid > 0
                          ? `${listPreview.invalid} invalid`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" \u00b7 ")}
                    </span>
                  ) : null}
                </span>
                <textarea
                  value={listText}
                  onChange={(e) => setListText(e.target.value)}
                  rows={8}
                  placeholder={"https://...\nhttps://..."}
                  className="hairline w-full rounded-md bg-surface px-3 py-2 font-mono text-xs"
                />
              </label>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {busy
              ? mode === "scrape"
                ? "Scraping\u2026"
                : "Adding\u2026"
              : mode === "scrape"
                ? "Find links"
                : mode === "single"
                  ? "Add URL"
                  : "Add URLs"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
