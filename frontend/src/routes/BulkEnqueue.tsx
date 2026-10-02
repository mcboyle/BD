// BulkEnqueue — 380 batch URL enqueue (wired v3.66.382).
//
// Paste a batch of URLs and enqueue them on a configured site via the existing
// run path. The backend de-dupes against the live queue (a resubmit reports
// dupes, adds 0) and caps at 1000. Never touches capture/extraction.

import * as React from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useBulkEnqueue } from "@/hooks/useBulkEnqueue";

// F040: the ready count and the submitted batch are the same list -- non-blank
// lines that parse as http(s) URLs, first occurrence kept. Blank lines are not
// counted as invalid.
function parseUrls(raw: string) {
  const seen = new Set<string>();
  let invalid = 0;
  let duplicate = 0;
  for (const line of raw.split(/\r?\n/)) {
    const u = line.trim();
    if (!u) continue;
    let ok = false;
    try {
      const p = new URL(u).protocol;
      ok = p === "http:" || p === "https:";
    } catch {
      ok = false;
    }
    if (!ok) invalid++;
    else if (seen.has(u)) duplicate++;
    else seen.add(u);
  }
  return { urls: [...seen], invalid, duplicate };
}

export default function BulkEnqueue() {
  const enqueue = useBulkEnqueue();
  const [siteId, setSiteId] = React.useState("");
  const [raw, setRaw] = React.useState("");

  const { urls, invalid, duplicate } = parseUrls(raw);
  const dropped = [
    invalid ? `${invalid} invalid` : "",
    duplicate ? `${duplicate} duplicate` : "",
  ].filter(Boolean).join(", ");
  const canSubmit = siteId.trim().length > 0 && urls.length > 0 && !enqueue.isPending;

  function onSubmit() {
    if (!canSubmit) return;
    enqueue.mutate({ site_id: siteId.trim(), urls });
  }

  const res = enqueue.data;

  return (
    <AppShell title="Bulk enqueue" subtitle="Enqueue a batch of URLs on a site. Duplicates already in the queue are skipped automatically; up to 1000 per submit.">
      <div className="space-y-4" data-testid="bulk-enqueue-page">

        <div className="space-y-2 rounded-md border border-hairline p-3">
          <label className="block text-sm">
            Site ID
            <Input
              aria-label="site id"
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
              placeholder="e.g. example.com"
            />
          </label>
          <label className="block text-sm">
            URLs (one per line) — {urls.length} ready{dropped && ` (${dropped})`}
            <textarea
              aria-label="urls"
              className="mt-1 w-full min-h-[160px] rounded-md border border-hairline bg-transparent p-3 text-sm"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              placeholder={"https://\u2026\nhttps://\u2026"}
            />
          </label>
          <Button type="button" onClick={onSubmit} disabled={!canSubmit}>
            {enqueue.isPending ? "Enqueuing\u2026" : `Enqueue ${urls.length} URL(s)`}
          </Button>
        </div>

        {res && (
          <div
            role={res.ok ? undefined : "alert"}
            data-testid="bulk-enqueue-result"
            className="rounded-md border border-hairline p-3 text-sm"
          >
            {res.ok ? (
              <span>
                Enqueued on <strong>{res.site_id}</strong>: requested {res.requested},
                added {res.added}, dupes {res.dupes}, skipped {res.skipped}.
              </span>
            ) : (
              <span>{res.error || "Enqueue failed"}</span>
            )}
          </div>
        )}
        {enqueue.isError && !res && (
          <p role="alert" className="text-sm text-danger">
            {enqueue.error?.message || "Request error"}
          </p>
        )}
      </div>
    </AppShell>
  );
}
