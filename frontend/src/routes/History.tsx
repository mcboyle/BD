import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
  useHistory,
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
import { formatBytes } from "@/lib/format";

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

function HistorySearchTab() {
  const [q, setQ] = useState("");
  const debounced = useDebouncedValue(q, 300);
  const history = useHistory({ limit: 200 });
  const search = useSearch(debounced);
  const searching = debounced.trim().length > 0;
  const rows: import("@/lib/api-types").SearchResultRow[] = searching
    ? (search.data?.results ?? [])
    : (history.data ?? []);
  const loading = searching ? search.isLoading : history.isLoading;

  const { isCompact } = useDensity();
  // P6-1 — client-side column sort over the fetched page. Accessors normalize
  // the comparable value (site falls back to id; size is numeric).
  const { sorted, sortKey, sortDir, toggle } = useTableSort(rows, {
    accessors: {
      when: (r) => r.ts ?? "",
      site: (r) => r.site_name || r.site_id || "",
      status: (r) => r.status ?? "",
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
          onChange={(e) => setQ(e.target.value)}
        />
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
          title={searching ? "No matches." : "No history yet."}
          hint={
            searching
              ? "Try a different search term."
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
                <SortHeader sortKey="file" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">File</SortHeader>
                <SortHeader sortKey="size" active={sortKey} dir={sortDir} onToggle={toggle} className="py-1 pr-3">Size</SortHeader>
                <th className="py-1 font-medium text-ink-3">Message</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.id} className="border-b border-border/40 align-top">
                  <td className={`pr-3 whitespace-nowrap text-xs tabular-nums ${cellPad}`}>
                    {r.ts ?? ""}
                  </td>
                  <td className={`pr-3 ${cellPad}`}>{r.site_name || r.site_id || ""}</td>
                  <td className={`pr-3 ${cellPad}`}>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className={`max-w-[260px] truncate pr-3 ${cellPad}`}>
                    {searching
                      ? stripMarks(r.snippet_filename) || r.filename || ""
                      : r.filename || ""}
                  </td>
                  <td className={`pr-3 whitespace-nowrap tabular-nums ${cellPad}`}>
                    {r.file_size ? formatBytes(r.file_size) : ""}
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
    </div>
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
        <div className="flex items-center justify-between">
          <TabsList>
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
