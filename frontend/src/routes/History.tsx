import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, Inbox, Play, RefreshCw, Trash2 } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { routeRisk } from "@/lib/routeRisk";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { SkeletonRows } from "@/components/ui/SkeletonRows";
import { SortHeader } from "@/components/ui/SortHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDensity } from "@/hooks/useDensity";
import { useTableSort } from "@/hooks/useTableSort";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useEventStream } from "@/hooks/useEventStream";
import { SearchFacetsStrip } from "@/components/ui/SearchFacetsStrip";
import {
  useHistoryPage,
  useHistoryVacuum,
  useSessionHistory,
  useEventsAll,
  useLogsTail,
  useLogsClear,
  useSearch,
  useSavedSearches,
  useSavedSearchAdd,
  useSavedSearchDelete,
  useSavedSearchUpdate,
  useSavedSearchRun,
  useSavedSearchDigest,
  useUiEventPageView,
  downloadUiEventsLog,
} from "@/hooks/useHistoryData";
import { apiGet } from "@/lib/api-client";
import { formatBytes } from "@/lib/format";
import type { SearchResultRow, StatusSnapshot } from "@/lib/api-types";

// ── T2 history/logs/search (v3.66.206) ──────────────────────────────
//
// One lazy SPA route consolidating the 12 legacy-only history/logs/
// search families as four tabs under the current history route
// Phase 2 / T2. First confirm-gated writes of Phase 2: history vacuum,
// log clear, and saved-search delete take a TYPED confirmation; saved-
// search add and run take a one-step confirm. Nothing fires on a
// single click; every write is audited by the underlying endpoint. No
// endpoint in this tranche carries secrets.
//
// FTS <mark> snippets from /api/search are rendered as plain TEXT
// (tags stripped) — never via innerHTML.

type Pending =
  | { kind: "vacuum"; token: string }
  | { kind: "logsClear"; token: string }
  | { kind: "savedAdd"; name: string; query: string; token: "" }
  | { kind: "savedDelete"; id: number; name: string; token: string }
  | {
      kind: "savedUpdate";
      id: number;
      name: string;
      fields: Record<string, unknown>;
      token: string;
    }
  | { kind: "savedRun"; id: number; name: string; token: "" };

const isTyped = (p: Pending): boolean => p.token.length > 0;

/** Strip <mark>…</mark> (and any stray tags) from an FTS snippet so it
 *  renders as plain text. */
const stripMarks = (s?: string) =>
  (s ?? "").replace(/<\/?mark>/g, "").replace(/<[^>]+>/g, "");

function StatusBadge({ status }: { status?: string }) {
  const s = status ?? "?";
  const variant =
    s === "done" ? "default" : s === "failed" ? "destructive" : "secondary";
  return <Badge variant={variant}>{s}</Badge>;
}

// F003 — the statuses db_log() call sites write to the history table. A status
// outside this list that shows up in a loaded row is offered as well.
const HISTORY_STATUSES = ["done", "failed", "needs_review", "skipped_duplicate"];
const PAGE_SIZES = [25, 50, 100, 200];
const SELECT_CLASS = "hairline rounded-md bg-surface px-2 py-1 text-xs text-ink";

/** The configured sites, for the site filter. `light=1` leaves the per-site
 *  job lists out of the payload; the list is read once, not polled. */
function useHistorySites() {
  return useQuery<StatusSnapshot>({
    queryKey: ["status", "light"],
    queryFn: ({ signal }) => apiGet<StatusSnapshot>("/api/status?light=1", signal),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** Labels for the columns the detail leads with, in reading order. Any other
 *  key the API returns follows them under its own name: the detail is driven
 *  by the row, so a stored column cannot be left out by this list. */
const DETAIL_LEAD: [string, string][] = [
  ["id", "Id"],
  ["ts", "When"],
  ["status", "Status"],
  ["title", "Website name"],
  ["title_source", "Name source"],
  ["url", "URL"],
  ["filename", "File"],
  ["file_path", "File path"],
  ["file_size", "Size"],
  ["message", "Message"],
  ["screenshot", "Screenshot"],
];

const detailLabel = (key: string) => {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const detailText = (v: unknown): string =>
  typeof v === "object" ? JSON.stringify(v) : String(v);

/** Every populated field of a history row, untruncated, for the detail
 *  dialog. Values render as TEXT. A field is left out only when it is null or
 *  empty (a stored 0 is a value); snippet_* is FTS highlight markup, not a
 *  column, and site_id/site_name share one line. */
function detailFields(r: SearchResultRow): [string, string][] {
  const has = (k: string) => r[k] !== null && r[k] !== undefined && r[k] !== "";
  const out: [string, string][] = [];
  const done = new Set<string>(["site_id", "site_name"]);
  const push = (key: string, label: string) => {
    done.add(key);
    if (!has(key)) return;
    const text = detailText(r[key]);
    out.push([
      label,
      key === "file_size" && typeof r[key] === "number"
        ? `${formatBytes(r[key] as number)} (${text} bytes)`
        : text,
    ]);
  };
  for (const [key, label] of DETAIL_LEAD.slice(0, 2)) push(key, label);
  if (has("site_id") || has("site_name")) {
    out.push(["Site", [r.site_name, r.site_id].filter(Boolean).join(" · ")]);
  }
  for (const [key, label] of DETAIL_LEAD.slice(2)) push(key, label);
  for (const key of Object.keys(r)) {
    if (done.has(key) || key.startsWith("snippet_")) continue;
    push(key, detailLabel(key));
  }
  return out;
}

function HistorySearchTab() {
  const [q, setQ] = useState("");
  const debounced = useDebouncedValue(q, 300);
  // F003 — site/status filters, paging and the row detail. The filters are
  // server-side on both lanes. The list lane pages by cursor (each entry of
  // `cursors` is the last id of the page before it); the FTS lane returns one
  // capped result set, which is paged here.
  const [siteId, setSiteId] = useState("");
  const [status, setStatus] = useState("");
  const [pageSize, setPageSize] = useState(200);
  const [cursors, setCursors] = useState<number[]>([]);
  const [searchPage, setSearchPage] = useState(0);
  const [detail, setDetail] = useState<SearchResultRow | null>(null);
  const toFirstPage = () => {
    setCursors([]);
    setSearchPage(0);
  };
  const sites = useHistorySites();
  const filters = { site_id: siteId || undefined, status: status || undefined };
  const history = useHistoryPage({
    ...filters,
    limit: pageSize,
    cursor: cursors.length ? cursors[cursors.length - 1] : null,
  });
  const search = useSearch(debounced, 100, filters);
  const searching = debounced.trim().length > 0;
  const matches = search.data?.results ?? [];
  const rows: SearchResultRow[] = searching
    ? matches.slice(searchPage * pageSize, (searchPage + 1) * pageSize)
    : (history.data?.rows ?? []);
  const loading = searching ? search.isLoading : history.isLoading;
  const filtered = siteId !== "" || status !== "";
  // Configured sites first; a site that only survives in loaded history rows
  // (removed from the config since) is offered too, as is the active choice.
  const siteNames = new Map<string, string>();
  for (const [id, v] of Object.entries(sites.data ?? {})) {
    siteNames.set(id, (v && v.name) || id);
  }
  for (const r of rows) {
    if (r.site_id && !siteNames.has(r.site_id)) {
      siteNames.set(r.site_id, r.site_name || r.site_id);
    }
  }
  if (siteId && !siteNames.has(siteId)) siteNames.set(siteId, siteId);
  const siteOptions = [...siteNames]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const statusOptions = [...HISTORY_STATUSES];
  for (const st of [status, ...rows.map((r) => r.status ?? "")]) {
    if (st && !statusOptions.includes(st)) statusOptions.push(st);
  }
  const pageIndex = searching ? searchPage : cursors.length;
  const nextCursor = history.data?.next_cursor ?? null;
  const hasNext = searching
    ? (searchPage + 1) * pageSize < matches.length
    : nextCursor != null;
  const goPrev = () =>
    searching
      ? setSearchPage((n) => Math.max(0, n - 1))
      : setCursors((c) => c.slice(0, -1));
  const goNext = () => {
    if (!hasNext) return;
    if (searching) setSearchPage((n) => n + 1);
    else if (nextCursor != null) setCursors((c) => [...c, nextCursor]);
  };

  const { isCompact } = useDensity();
  // P6-1 — client-side column sort over the fetched page. Accessors normalize
  // the comparable value (site falls back to id; size is numeric).
  const { sorted, sortKey, sortDir, toggle } = useTableSort(rows, {
    accessors: {
      when: (r) => r.ts ?? "",
      site: (r) => r.site_name || r.site_id || "",
      status: (r) => r.status ?? "",
      title: (r) => r.title ?? "",
      file: (r) => r.filename ?? "",
      size: (r) => r.file_size ?? null,
    },
  });
  // Density-aware row padding. Comfortable is a touch roomier than the old
  // py-1; compact tightens it.
  const cellPad = isCompact ? "py-0.5" : "py-1.5";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="max-w-[360px]"
          placeholder="Search history (FTS) — leave empty for recent"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            toFirstPage();
          }}
        />
        <select
          aria-label="Filter by site"
          value={siteId}
          onChange={(e) => {
            setSiteId(e.target.value);
            toFirstPage();
          }}
          className={SELECT_CLASS}
        >
          <option value="">All sites</option>
          {siteOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            toFirstPage();
          }}
          className={SELECT_CLASS}
        >
          <option value="">All statuses</option>
          {statusOptions.map((st) => (
            <option key={st} value={st}>
              {st}
            </option>
          ))}
        </select>
        {searching && (
          <span className="text-xs text-ink-3">
            {search.data?.count ?? 0} matches
          </span>
        )}
        {/* v3.66.743 — /api/search/facets consumer: break the count down */}
        {searching && <SearchFacetsStrip q={debounced} />}
        <div className="ml-auto">
          <DensityToggle />
        </div>
      </div>
      {loading ? (
        <SkeletonRows count={8} rowClassName="h-7" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title={
            searching
              ? "No matches."
              : filtered || pageIndex > 0
                ? "No history rows here."
                : "No history yet."
          }
          hint={
            searching
              ? "Try a different search term."
              : filtered
                ? "No rows match the selected site and status."
                : pageIndex > 0
                  ? "There are no older rows."
                  : "Completed and failed jobs will be listed here."
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="bd-table w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs">
                <SortHeader sortKey="when" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">When</SortHeader>
                <SortHeader sortKey="site" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">Site</SortHeader>
                <SortHeader sortKey="status" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">Status</SortHeader>
                <SortHeader sortKey="title" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">Website name</SortHeader>
                <th className="py-1 pr-3 font-medium text-ink-3">Source</th>
                <SortHeader sortKey="file" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">File</SortHeader>
                <SortHeader sortKey="size" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">Size</SortHeader>
                <th className="py-1 font-medium text-ink-3">Message</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr
                  key={r.id}
                  className="cursor-pointer border-b border-border/40 align-top hover:bg-surface-2"
                  onClick={() => setDetail(r)}
                >
                  <td className={`pr-3 whitespace-nowrap text-xs tabular-nums ${cellPad}`}>
                    <button
                      type="button"
                      className="text-left hover:underline"
                      aria-label={`Open details for history row ${r.id ?? ""}`}
                    >
                      {r.ts ?? ""}
                    </button>
                  </td>
                  <td className={`pr-3 ${cellPad}`}>{r.site_name || r.site_id || ""}</td>
                  <td className={`pr-3 ${cellPad}`}>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className={`max-w-[260px] truncate pr-3 ${cellPad}`}>
                    {r.title || ""}
                  </td>
                  <td className={`pr-3 whitespace-nowrap text-xs text-ink-3 ${cellPad}`}>
                    {r.title_source || ""}
                  </td>
                  <td className={`max-w-[260px] truncate pr-3 ${cellPad}`}>
                    {searching
                      ? stripMarks(r.snippet_filename) || r.filename || ""
                      : r.filename || ""}
                  </td>
                  <td className={`pr-3 whitespace-nowrap tabular-nums ${cellPad}`}>
                    {r.file_size != null ? formatBytes(r.file_size) : ""}
                  </td>
                  <td className={`max-w-[360px] truncate text-xs text-ink-3 ${cellPad}`}>
                    {searching
                      ? stripMarks(r.snippet_message) || r.message || ""
                      : r.message || ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && (rows.length > 0 || pageIndex > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
          <span role="status">
            Page {pageIndex + 1} · {rows.length} rows
            {hasNext ? "" : " · end of list"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <label className="flex items-center gap-1.5">
              Rows per page
              <select
                aria-label="Rows per page"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  toFirstPage();
                }}
                className={SELECT_CLASS}
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <Button size="sm" variant="outline" disabled={pageIndex === 0} onClick={goPrev}>
              Previous page
            </Button>
            <Button size="sm" variant="outline" disabled={!hasNext} onClick={goNext}>
              Next page
            </Button>
          </div>
        </div>
      )}
      <Dialog open={detail !== null} onOpenChange={(o) => !o && setDetail(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>History row {detail?.id ?? ""}</DialogTitle>
            <DialogDescription>
              The recorded outcome of this job, as stored.
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm">
              {detailFields(detail).map(([label, value], i) => (
                <div key={`${i}-${label}`} className="contents">
                  <dt className="text-xs text-ink-3">{label}</dt>
                  <dd className="whitespace-pre-wrap break-all">{value}</dd>
                </div>
              ))}
            </dl>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetail(null)}>
              Close details
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// dsq-spa-errors F7: a failed read is not "nothing yet".
function LoadError({ what }: { what: string }) {
  return (
    <p role="alert" className="py-6 text-center text-sm text-red">
      Couldn't load {what}.
    </p>
  );
}

function EventsTab() {
  const events = useEventsAll(200);
  const sessions = useSessionHistory(100);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="p-4">
        <h3 className="section-head">Cross-site events</h3>
        {events.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : events.isError && !events.data ? (
          <LoadError what="events" />
        ) : (events.data?.events?.length ?? 0) === 0 ? (
          <p className="py-6 text-center text-sm text-ink-3">No events yet.</p>
        ) : (
          <ul className="max-h-[420px] space-y-1 overflow-y-auto text-xs">
            {[...(events.data?.events ?? [])].reverse().map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="whitespace-nowrap tabular-nums text-muted-foreground">
                  {e.ts ?? ""}
                </span>
                <span className="font-medium">{e.site_name ?? e.site_id}</span>
                <span className="text-muted-foreground">{e.kind ?? ""}</span>
                <span className="truncate">{e.message ?? ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card className="p-4">
        <h3 className="section-head">Session keeper events</h3>
        {sessions.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : sessions.isError && !sessions.data ? (
          <LoadError what="session events" />
        ) : (sessions.data?.events?.length ?? 0) === 0 ? (
          <p className="py-6 text-center text-sm text-ink-3">No session events yet.</p>
        ) : (
          <ul className="max-h-[420px] space-y-1 overflow-y-auto text-xs">
            {(sessions.data?.events ?? []).map((e, i) => (
              <li key={e.id ?? i} className="flex gap-2">
                <span className="whitespace-nowrap tabular-nums text-muted-foreground">
                  {e.ts ?? ""}
                </span>
                <span className="font-medium">{e.site_id ?? ""}</span>
                <span className="text-muted-foreground">{e.event ?? ""}</span>
                <span className="truncate">{e.message ?? ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function LogsTab({ arm, busy }: { arm: (p: Pending) => void; busy: boolean }) {
  const [lines, setLines] = useState(200);
  const tail = useLogsTail(lines);
  const onDownloadUiEvents = async () => {
    try {
      await downloadUiEventsLog();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "download failed");
    }
  };
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">
          {tail.data?.file_size != null
            ? `log ${formatBytes(tail.data.file_size)} · level ${tail.data.current_level ?? "?"}`
            : ""}
        </span>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setLines((n) => (n >= 1000 ? 200 : n * 5))}
          >
            {lines} lines
          </Button>
          <Button size="sm" variant="outline" onClick={() => tail.refetch()}>
            <RefreshCw className="mr-1 h-3 w-3" />
            Refresh
          </Button>
          <Button size="sm" variant="outline" onClick={onDownloadUiEvents}>
            <Download className="mr-1 h-3 w-3" />
            UI events log
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy}
            onClick={() => arm({ kind: "logsClear", token: "CLEAR LOGS" })}
          >
            <Trash2 className="mr-1 h-3 w-3" />
            Clear log
          </Button>
        </div>
      </div>
      {tail.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : tail.isError && !tail.data ? (
        <LoadError what="the log" />
      ) : (
        <pre className="max-h-[480px] overflow-auto rounded-md border bg-surface-2 p-3 text-[11px] leading-4">
          {(tail.data?.lines ?? []).join("\n") ||
            tail.data?.note ||
            "log empty"}
        </pre>
      )}
    </div>
  );
}

function SavedSearchesTab({
  arm,
  busy,
  onToggleAction,
}: {
  arm: (p: Pending) => void;
  busy: boolean;
  onToggleAction: (id: number, name: string, current: string) => void;
}) {
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const list = useSavedSearches();
  const digest = useSavedSearchDigest(168);
  const matchesById = new Map(
    (digest.data?.searches ?? []).map((d) => [d.id, d.matches ?? 0]),
  );
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="section-head">New saved search</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            className="max-w-[320px]"
            placeholder="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button
            size="sm"
            disabled={busy || !name.trim() || !query.trim()}
            onClick={() =>
              arm({
                kind: "savedAdd",
                name: name.trim(),
                query: query.trim(),
                token: "",
              })
            }
          >
            Save
          </Button>
        </div>
      </Card>
      <Card className="p-4">
        <h3 className="section-head">
          Saved searches{" "}
          <span className="font-normal text-muted-foreground">
            (matches, last 7 days)
          </span>
        </h3>
        {list.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : list.isError && !list.data ? (
          <LoadError what="saved searches" />
        ) : (list.data?.searches?.length ?? 0) === 0 ? (
          <p className="text-sm text-muted-foreground">No saved searches.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-1 pr-3">Name</th>
                <th className="py-1 pr-3">Query</th>
                <th className="py-1 pr-3">Matches (7d)</th>
                <th className="py-1 pr-3">Action</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {(list.data?.searches ?? []).map((s) => (
                <tr key={s.id} className="border-b border-border/40">
                  <td className="py-1 pr-3 font-medium">{s.name}</td>
                  <td className="max-w-[280px] truncate py-1 pr-3 font-mono text-xs">
                    {s.query}
                  </td>
                  <td className="py-1 pr-3 tabular-nums">
                    {matchesById.get(s.id) ?? "—"}
                  </td>
                  <td className="py-1 pr-3">
                    {(() => {
                      const action = (s.action as string) || "notify";
                      const isEnqueue = action === "enqueue";
                      return (
                        <div className="flex items-center gap-2">
                          {isEnqueue ? (
                            <Badge variant="default" className="gap-1">
                              <Inbox className="h-3 w-3" />→ pipeline
                            </Badge>
                          ) : (
                            <Badge variant="secondary">notify</Badge>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-xs"
                            disabled={busy || s.id == null}
                            onClick={() =>
                              s.id != null &&
                              onToggleAction(s.id, s.name ?? String(s.id), action)
                            }
                          >
                            {isEnqueue ? "→ notify" : "→ enqueue"}
                          </Button>
                        </div>
                      );
                    })()}
                  </td>
                  <td className="py-1 text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      className="mr-2"
                      disabled={busy || s.id == null}
                      onClick={() =>
                        s.id != null &&
                        arm({
                          kind: "savedRun",
                          id: s.id,
                          name: s.name ?? String(s.id),
                          token: "",
                        })
                      }
                    >
                      <Play className="mr-1 h-3 w-3" />
                      Run
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy || s.id == null}
                      onClick={() =>
                        s.id != null &&
                        arm({
                          kind: "savedDelete",
                          id: s.id,
                          name: s.name ?? String(s.id),
                          token: `DELETE ${s.id}`,
                        })
                      }
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export default function History() {
  useUiEventPageView("history");
  // F4.5: refresh the history list on a queue_change push (a job completed/
  // was added) instead of fast polling. useHistory()'s refetchInterval backs
  // off to a slow safety poll while the shared stream is connected.
  const queryClient = useQueryClient();
  useEventStream({
    queue_change: () => queryClient.invalidateQueries({ queryKey: ["history"] }),
  });
  const [pending, setPending] = useState<Pending | null>(null);

  const vacuum = useHistoryVacuum();
  const logsClear = useLogsClear();
  const savedAdd = useSavedSearchAdd();
  const savedDelete = useSavedSearchDelete();
  const savedUpdate = useSavedSearchUpdate();
  const savedRun = useSavedSearchRun();

  const busy =
    vacuum.isPending ||
    logsClear.isPending ||
    savedAdd.isPending ||
    savedDelete.isPending ||
    savedUpdate.isPending ||
    savedRun.isPending;

  const arm = (p: Pending) => {
    setPending(p);
  };

  // F3.1 action-lane toggle. Escalating to the enqueue lane (new matches
  // start feeding the download pipeline) takes a one-step confirm; de-
  // escalating back to notify is reversible and applies immediately.
  const onToggleAction = (id: number, name: string, current: string) => {
    const next = current === "enqueue" ? "notify" : "enqueue";
    if (next === "enqueue") {
      arm({ kind: "savedUpdate", id, name, fields: { action: "enqueue" }, token: "" });
    } else {
      savedUpdate.mutate(
        { id, fields: { action: "notify" } },
        {
          onSuccess: (r) =>
            r.ok === false
              ? toast.error("update failed")
              : toast.success(`"${name}" → notify only`),
          onError: (e) => toast.error(e.message),
        },
      );
    }
  };

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "vacuum":
        vacuum.mutate(undefined, {
          onSuccess: (r) =>
            r.ok === false
              ? toast.error(r.error || "vacuum failed")
              : toast.success("History database compacted"),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "logsClear":
        logsClear.mutate(undefined, {
          onSuccess: (r) =>
            r.ok === false
              ? toast.error(r.error || "clear failed")
              : toast.success(
                  `Log cleared — freed ${formatBytes(r.freed_bytes ?? 0)}, removed ${r.archives_removed ?? 0} archives`,
                ),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "savedAdd":
        savedAdd.mutate(
          { name: pending.name, query: pending.query },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "add failed")
                : toast.success(`Saved "${pending.name}"`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "savedDelete":
        savedDelete.mutate(pending.id, {
          onSuccess: (r) =>
            r.ok === false
              ? toast.error("delete failed")
              : toast.success(`Deleted "${pending.name}"`),
          onError: (e) => toast.error(e.message),
        });
        break;
      case "savedUpdate":
        savedUpdate.mutate(
          { id: pending.id, fields: pending.fields },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error("update failed")
                : toast.success(`"${pending.name}" → enqueue (feeds pipeline)`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "savedRun":
        savedRun.mutate(pending.id, {
          onSuccess: () => toast.success(`Ran "${pending.name}"`),
          onError: (e) => toast.error(e.message),
        });
        break;
    }
    setPending(null);
  };

  return (
    <AppShell
      title="History · Logs · Search"
      subtitle="Download history · event feeds · app log · saved searches"
    >
      <GatedWriteBanner shape={routeRisk("/history").bannerShape}>
        Destructive actions (clear log, compact database,
        delete saved search) require an explicit yes/no confirmation (No default) — nothing fires on a
        single click; every request is audited by the underlying endpoint.{" "}
        <b>Needs operator click-through validation.</b>
      </GatedWriteBanner>

      <Tabs defaultValue="history" className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList className="h-auto flex-wrap justify-start">
            <TabsTrigger value="history">History &amp; Search</TabsTrigger>
            <TabsTrigger value="events">Events</TabsTrigger>
            <TabsTrigger value="logs">Logs</TabsTrigger>
            <TabsTrigger value="saved">Saved searches</TabsTrigger>
          </TabsList>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => arm({ kind: "vacuum", token: "VACUUM HISTORY" })}
          >
            Compact database
          </Button>
        </div>
        <TabsContent value="history" className="mt-3">
          <HistorySearchTab />
        </TabsContent>
        <TabsContent value="events" className="mt-3">
          <EventsTab />
        </TabsContent>
        <TabsContent value="logs" className="mt-3">
          <LogsTab arm={arm} busy={busy} />
        </TabsContent>
        <TabsContent value="saved" className="mt-3">
          <SavedSearchesTab arm={arm} busy={busy} onToggleAction={onToggleAction} />
        </TabsContent>
      </Tabs>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending && isTyped(pending)
                ? "This is destructive and cannot be undone. Proceed?"
                : "Confirm this operation."}
            </DialogDescription>
          </DialogHeader>
          {pending && isTyped(pending) && (
            <p className="font-mono text-xs text-amber-300">{pending.token}</p>
          )}
          <DialogFooter>
            {pending && isTyped(pending) ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
