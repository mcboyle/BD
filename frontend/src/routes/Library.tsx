import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, Link2, List, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { LiveSection } from "@/components/sections/LiveSection";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { SkeletonRows } from "@/components/ui/SkeletonRows";
import { SortSelect } from "@/components/ui/SortSelect";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDensity } from "@/hooks/useDensity";
import { useTableSort } from "@/hooks/useTableSort";
import { apiDelete, apiGet, apiPost } from "@/lib/api-client";
import {
  useLibraryAddTag,
  useLibraryAudit,
  useLibraryOrphans,
  useLibrarySetRating,
  useLibrarySetWatched,
  useLibraryStats,
  useRegenNfos,
  useSceneScoreBottom,
  useTagAdd,
  useTagsForMany,
  useTagRemove,
  useTagRename,
  useTagRows,
  useTagSuggest,
} from "@/hooks/useLibraryOps";
import type {
  LibraryBrowse,
  LibraryItem,
  LibraryScanStatus,
  LibraryTag,
  LibraryTagList,
  OkResult,
} from "@/lib/api-types";

// GUI parity (177) — Library actions. Surfaces the EXISTING library write
// endpoints under the risk model: every destructive / runtime action is gated
// by a labelled yes/no confirm dialog (No is the default); no unlabelled one-click.
// CSRF + audit are handled by api-client + the backend endpoints. Read paths
// expose no secrets.

// P6-1 — sort fields offered on the Library Items list (a non-tabular <ul>).
const ITEM_SORT_OPTS = [
  { key: "added", label: "Date added" },
  { key: "title", label: "Title" },
  { key: "size", label: "Size" },
  { key: "rating", label: "Rating" },
  { key: "duration", label: "Duration" },
];

const itemLabel = (it: LibraryItem) =>
  it.title || it.name || it.path || `#${it.id}`;
const tagLabel = (t: LibraryTag) => t.name || t.tag || `#${t.id}`;
// BD browse rows name the file `file_path` and send tags as {id, name, color}.
const itemPath = (it: LibraryItem) =>
  (typeof it.file_path === "string" && it.file_path) || it.path || "";

// library-fix F8: a stored rating outside the 1-5 options (0, 7, 3.5) or sent
// as a numeric string ("4") read as "none". Show what is stored.
const ratingText = (r: unknown): string => {
  const n =
    typeof r === "number" ? r : typeof r === "string" && r.trim() ? Number(r) : NaN;
  return Number.isFinite(n) ? String(n) : "";
};
const RATING_OPTS = ["1", "2", "3", "4", "5"];

// library-fix F6: the Tags card rendered every tag (5 000 -> 50 000 nodes,
// every keystroke re-rendered them all). Render a filtered window instead.
const TAG_WINDOW = 200;

// library-fix F10: history ids are positive integers that survive Number()
// exactly; anything else is reported, never silently dropped or rounded.
const parseIdBox = (raw: string): { ids: number[]; rejected: string[] } => {
  const ids: number[] = [];
  const rejected: string[] = [];
  for (const tok of raw.split(/[\s,]+/)) {
    if (!tok) continue;
    const n = Number(tok);
    if (!/^\d+$/.test(tok) || !Number.isSafeInteger(n)) rejected.push(tok);
    else if (!ids.includes(n)) ids.push(n);
  }
  return { ids, rejected };
};

type Pending =
  | { kind: "item"; id: number | string; label: string; token: string }
  | { kind: "tag"; id: number | string; label: string; token: string }
  | { kind: "scanStart"; root: string; token: "" }
  | { kind: "scanCancel"; token: "" }
  | { kind: "rotateStream"; token: string }
  // T3 (v3.66.207) bulk-tag + NFO writes — confirm-gated (single-tap tier), never one-click.
  | { kind: "tagAdd"; ids: number[]; tag: string; token: "" }
  | { kind: "tagRemove"; ids: number[]; tag: string; token: "" }
  | { kind: "tagRename"; from: string; to: string; token: "" }
  | { kind: "regenNfos"; overwrite: boolean; token: "" };

// F019 — the Items card renders as a list (default) or a card grid. The choice
// is page-local and survives a reload via localStorage; anything other than
// the literal "grid" reads back as "list".
type ItemView = "list" | "grid";
const ITEM_VIEW_KEY = "bd-library-view";
const BROWSE = "/api/library/browse?limit=200";
const ITEM_VIEW_OPTS: ReadonlyArray<{ value: ItemView; label: string; Icon: typeof List }> = [
  { value: "list", label: "List", Icon: List },
  { value: "grid", label: "Grid", Icon: LayoutGrid },
];

function readItemView(): ItemView {
  try {
    return window.localStorage.getItem(ITEM_VIEW_KEY) === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

export function Library() {
  const qc = useQueryClient();
  const { isCompact } = useDensity();
  const [itemView, setItemViewState] = useState<ItemView>(readItemView);
  const setItemView = (v: ItemView) => {
    setItemViewState(v);
    try {
      window.localStorage.setItem(ITEM_VIEW_KEY, v);
    } catch {
      /* localStorage blocked — the view still switches for this visit */
    }
  };
  const isGrid = itemView === "grid";

  // sltm-fix F1: the list is paged (next_cursor). Only the newest page used to
  // load, and search/sort ran over it alone, so older items were unfindable.
  // Pages now load on demand, and a search also asks the server (below).
  const items = useInfiniteQuery<LibraryBrowse, Error>({
    queryKey: ["library", "browse"],
    queryFn: ({ pageParam }) =>
      apiGet<LibraryBrowse>(
        pageParam
          ? `${BROWSE}&after_id=${encodeURIComponent(String(pageParam))}`
          : BROWSE,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor || undefined,
  });
  const tags = useQuery<LibraryTagList, Error>({
    queryKey: ["library", "tags"],
    queryFn: () => apiGet<LibraryTagList>("/api/library/tags"),
  });
  const scan = useQuery<LibraryScanStatus, Error>({
    queryKey: ["library", "scan"],
    queryFn: () => apiGet<LibraryScanStatus>("/api/library/scan/status"),
    refetchInterval: 5000,
  });

  // P6-1 — sort the fetched library page client-side via the SortSelect control
  // (a <ul>, not a table, so no column headers). Direction toggle governs
  // newest/oldest etc.
  // library-fix F11: a repeated id became a repeated React key, and a refetch
  // left a ghost row behind. Keep the first row per id.
  const loadedRows: LibraryItem[] = [];
  const seen = new Set<string>();
  for (const r of items.data?.pages.flatMap((pg) => pg.rows ?? []) ?? []) {
    if (!seen.has(String(r.id))) {
      seen.add(String(r.id));
      loadedRows.push(r);
    }
  }
  const partial = !!items.hasNextPage;
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  // While pages remain unloaded, a search also runs server-side: q= matches
  // title/path/notes and tag= an exact tag name across the whole library.
  const remoteTerm = useDebouncedValue(search.trim(), 300);
  const remote = useQuery<LibraryItem[], Error>({
    queryKey: ["library", "browse", "search", remoteTerm],
    queryFn: async () => {
      const enc = encodeURIComponent(remoteTerm);
      const [byText, byTag] = await Promise.all([
        apiGet<LibraryBrowse>(`${BROWSE}&q=${enc}`),
        apiGet<LibraryBrowse>(`${BROWSE}&tag=${enc}`),
      ]);
      return [...(byText?.rows ?? []), ...(byTag?.rows ?? [])];
    },
    enabled: partial && remoteTerm.length > 0,
  });
  const remoteIds = new Set(
    query && partial ? (remote.data ?? []).map((r) => r.id) : [],
  );
  const itemRows = [...loadedRows];
  if (remoteIds.size) {
    for (const r of remote.data ?? []) {
      if (!seen.has(String(r.id))) {
        seen.add(String(r.id));
        itemRows.push(r);
      }
    }
  }
  const itemsSort = useTableSort(itemRows, {
    accessors: {
      added: (r) => (typeof r.added_at === "number" ? r.added_at : null),
      title: (r) => itemLabel(r),
      size: (r) => (typeof r.file_size === "number" ? r.file_size : null),
      rating: (r) => (typeof r.rating === "number" ? r.rating : null),
      duration: (r) => (typeof r.duration_s === "number" ? r.duration_s : null),
    },
  });
  const visibleItems = query
    ? itemsSort.sorted.filter((item) =>
        [
          itemLabel(item),
          // cx166 R1: the same fields the server search matches (title,
          // path, notes; tags), so a match survives once every page loads.
          typeof item.file_path === "string" ? item.file_path : item.path ?? "",
          typeof item.notes === "string" ? item.notes : "",
          ...(item.tags ?? []).map((tag) =>
            typeof tag === "string" ? tag : tag.name ?? "",
          ),
        ].some((value) => value.toLowerCase().includes(query)),
      )
    : itemsSort.sorted;

  const delItem = useMutation<OkResult, Error, number | string>({
    mutationFn: (id) => apiDelete<OkResult>(`/api/library/${id}`),
    onSuccess: (res, id) => {
      if (res.ok) {
        toast.success(`Deleted item ${id}`);
        qc.invalidateQueries({ queryKey: ["library", "browse"] });
      } else toast.error(res.error || "delete failed");
    },
    onError: (e) => toast.error(e.message),
  });
  const delTag = useMutation<OkResult, Error, number | string>({
    mutationFn: (id) => apiDelete<OkResult>(`/api/library/tags/${id}`),
    onSuccess: (res, id) => {
      if (res.ok) {
        toast.success(`Deleted tag ${id}`);
        qc.invalidateQueries({ queryKey: ["library", "tags"] });
      } else toast.error(res.error || "tag delete failed");
    },
    onError: (e) => toast.error(e.message),
  });
  const scanStart = useMutation<OkResult, Error, string>({
    mutationFn: (root) =>
      apiPost<OkResult>("/api/library/scan/start", root ? { roots: [root] } : {}),
    onSuccess: (res) => {
      if (res.ok) {
        toast.success("Scan started");
        qc.invalidateQueries({ queryKey: ["library", "scan"] });
      } else toast.error(res.error || "scan start failed");
    },
    onError: (e) => toast.error(e.message),
  });
  const scanCancel = useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/library/scan/cancel", {}),
    onSuccess: (res) => {
      if (res.ok) {
        toast.success("Scan cancelled");
        qc.invalidateQueries({ queryKey: ["library", "scan"] });
      } else toast.error(res.error || "cancel failed");
    },
    onError: (e) => toast.error(e.message),
  });

  // Stream link (T18): POST /api/stream/token/<hid> mints a short-lived token for
  // a history-linked file; the result is a /stream/<token> URL the operator can
  // share. Only history-linked rows (history_id present) can be streamed.
  const [streamLink, setStreamLink] = useState<{ url: string; ttl: number } | null>(null);
  // F019 (O1671): clicking an item title opens a read-only detail dialog.
  const [preview, setPreview] = useState<LibraryItem | null>(null);
  const streamTokenMut = useMutation<
    { ok?: boolean; error?: string; token?: string; history_id?: number },
    Error,
    number
  >({
    // Full /api/ literal (inline ${hid} path param) so the parity inventory
    // sees the SPA consumer; the earlier string-concat form worked at runtime
    // but the static scanner never counted it, leaving
    // /api/stream/token/{x} stranded in legacy_only.
    mutationFn: (hid) => apiPost(`/api/stream/token/${hid}`, { ttl_seconds: 3600 }),
    onSuccess: (r) => {
      if (r.ok === false || !r.token) {
        toast.error(r.error || "could not mint a stream token");
        return;
      }
      setStreamLink({ url: window.location.origin + "/stream/" + r.token, ttl: 3600 });
    },
    onError: (e) => toast.error(e.message),
  });

  // Rotate the stream-token signing secret (T18): panic button — invalidates
  // EVERY previously-issued stream link in one shot. Labelled yes/no confirm (No default).
  const rotateStreamMut = useMutation<{ ok?: boolean; error?: string; message?: string }, Error, void>({
    mutationFn: () => apiPost("/api/stream/rotate_secret", {}),
    onSuccess: (r) => {
      if (r.ok === false) {
        toast.error(r.error || "rotate failed");
        return;
      }
      toast.success(r.message || "All stream links invalidated");
      setStreamLink(null);
    },
    onError: (e) => toast.error(e.message),
  });

  // Read a history_id off a library row (nullable FK; library.id != history.id).
  const historyIdOf = (it: LibraryItem): number | null => {
    const h = (it as Record<string, unknown>).history_id;
    return typeof h === "number" ? h : null;
  };

  // ── T3 (v3.66.207): library ops + bulk tags + scene score ─────────
  const libStats = useLibraryStats();
  const audit = useLibraryAudit();
  const orphans = useLibraryOrphans();
  const setRating = useLibrarySetRating();
  const setWatched = useLibrarySetWatched();
  const addTag = useLibraryAddTag();
  const regenNfos = useRegenNfos();
  const tagsForMany = useTagsForMany();
  const tagAdd = useTagAdd();
  const tagRemove = useTagRemove();
  const tagRename = useTagRename();
  const sceneBottom = useSceneScoreBottom(20);
  const [auditDir, setAuditDir] = useState("");
  const [bulkIds, setBulkIds] = useState("");
  const [bulkTag, setBulkTag] = useState("");
  const [renameFrom, setRenameFrom] = useState("");
  const [renameTo, setRenameTo] = useState("");
  const [rowsTag, setRowsTag] = useState("");
  const [suggestHid, setSuggestHid] = useState("");
  const tagRows = useTagRows(rowsTag.trim());
  const suggestN = /^\d+$/.test(suggestHid.trim()) ? Number(suggestHid.trim()) : null;
  const tagSuggest = useTagSuggest(suggestN);
  const bulk = parseIdBox(bulkIds);
  const parseIds = (raw: string): number[] => parseIdBox(raw).ids;
  const bulkOk = bulk.ids.length > 0 && bulk.rejected.length === 0;
  const [tagFilter, setTagFilter] = useState("");

  // Confirm state: destructive actions get a labelled yes/no dialog with No as
  // the default; the confirm button is disabled only while a request is busy.
  const [pending, setPending] = useState<Pending | null>(null);
  const [scanRoot, setScanRoot] = useState("");

  const busy =
    delItem.isPending ||
    delTag.isPending ||
    scanStart.isPending ||
    scanCancel.isPending ||
    streamTokenMut.isPending ||
    rotateStreamMut.isPending ||
    tagsForMany.isPending ||
    tagAdd.isPending ||
    tagRemove.isPending ||
    tagRename.isPending ||
    setRating.isPending ||
    setWatched.isPending ||
    addTag.isPending ||
    regenNfos.isPending;

  const arm = (p: Pending) => {
    setPending(p);
  };

  const confirmRun = () => {
    if (!pending) return;
    if (pending.kind === "item") delItem.mutate(pending.id);
    else if (pending.kind === "tag") delTag.mutate(pending.id);
    else if (pending.kind === "scanStart") scanStart.mutate(pending.root);
    else if (pending.kind === "scanCancel") scanCancel.mutate();
    else if (pending.kind === "rotateStream") rotateStreamMut.mutate();
    else if (pending.kind === "tagAdd")
      tagAdd.mutate(
        { history_ids: pending.ids, tag: pending.tag },
        {
          onSuccess: (r) =>
            r.ok !== false ? toast.success("Tag applied") : toast.error(r.error || "tag add failed"),
          onError: (e) => toast.error(e.message),
        },
      );
    else if (pending.kind === "tagRemove")
      tagRemove.mutate(
        { history_ids: pending.ids, tag: pending.tag },
        {
          onSuccess: (r) =>
            r.ok !== false ? toast.success("Tag removed") : toast.error(r.error || "tag remove failed"),
          onError: (e) => toast.error(e.message),
        },
      );
    else if (pending.kind === "tagRename")
      tagRename.mutate(
        { old: pending.from, new: pending.to },
        {
          onSuccess: (r) =>
            r.ok !== false ? toast.success("Tag renamed") : toast.error(r.error || "rename failed"),
          onError: (e) => toast.error(e.message),
        },
      );
    else if (pending.kind === "regenNfos")
      regenNfos.mutate(
        { overwrite: pending.overwrite, dry_run: false },
        {
          onSuccess: (r) =>
            r.error ? toast.error(r.error) : toast.success("NFO regen complete"),
          onError: (e) => toast.error(e.message),
        },
      );
    setPending(null);
  };

  // library-fix F2: a failed status read showed "idle", the real idle text.
  // With no data yet, each 5 s refetch puts the query back to pending (error
  // cleared), so an earlier failure is read from errorUpdatedAt, and a status
  // never read is not "idle" either.
  const scanFailed = scan.isError || (scan.data === undefined && scan.errorUpdatedAt > 0);
  const scanState = scan.isError
    ? `status unavailable (${scan.error?.message ?? "request failed"})`
    : scan.data === undefined
      ? scanFailed
        ? "status unavailable (retrying)"
        : "status loading…"
      : scan.data.scan?.state ??
        scan.data.scan?.status ??
        (scan.data.scan?.running ? "running" : "idle");

  const tagNeedle = tagFilter.trim().toLowerCase();
  const matchedTags = (tags.data?.tags ?? []).filter(
    (t) => !tagNeedle || tagLabel(t).toLowerCase().includes(tagNeedle),
  );
  const shownTags = matchedTags.slice(0, TAG_WINDOW);

  return (
    <AppShell title="Library" subtitle="Items, tags, and scans">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Scan root (blank = configured roots)"
          value={scanRoot}
          onChange={(e) => setScanRoot(e.target.value)}
          className="max-w-xs"
        />
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => arm({ kind: "scanStart", root: scanRoot, token: "" })}
        >
          <Play className="mr-1 h-4 w-4" /> Start scan
        </Button>
        <Button
          variant="destructive"
          disabled={busy}
          onClick={() => arm({ kind: "scanCancel", token: "" })}
        >
          <Square className="mr-1 h-4 w-4" /> Cancel scan
        </Button>
        <span
          className={`text-sm ${scanFailed ? "text-red" : "text-muted-foreground"}`}
          role={scanFailed ? "alert" : undefined}
        >
          scan: {scanState}
        </span>
        <Button
          variant="destructive"
          disabled={busy}
          onClick={() => arm({ kind: "rotateStream", token: "ROTATE STREAM" })}
          title="Invalidate every previously-issued stream link"
          // library-fix F5: at a 200% text size these labels ran off a 390 px screen.
          className="h-auto min-h-10 whitespace-normal"
        >
          <RotateCcw className="mr-1 h-4 w-4" /> Rotate stream secret
        </Button>
        <div className="ml-auto">
          <DensityToggle />
        </div>
      </div>

      <Card className="mt-4 p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="section-head mb-0">Items</h2>
          <div
            role="group"
            aria-label="Item view"
            className="ml-auto inline-flex items-center gap-0.5 rounded-md border border-border bg-surface-2 p-0.5"
          >
            {ITEM_VIEW_OPTS.map(({ value, label, Icon }) => (
              <button
                key={value}
                type="button"
                aria-pressed={itemView === value}
                aria-label={label}
                title={`${label} view`}
                onClick={() => setItemView(value)}
                className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                  itemView === value
                    ? "bg-surface text-ink shadow-xs"
                    : "text-ink-3 hover:text-ink"
                }`}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>
          {!!loadedRows.length && (
            <SortSelect
              options={ITEM_SORT_OPTS}
              sortKey={itemsSort.sortKey}
              dir={itemsSort.sortDir}
              onSet={itemsSort.setSort}
            />
          )}
        </div>
        <label htmlFor="library-search" className="mb-1 block text-sm text-ink-3">
          Search library
        </label>
        <Input
          id="library-search"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search titles and tags"
          className="mb-3 max-w-sm"
        />
        {partial && (
          <p className="mb-2 flex flex-wrap items-center gap-2 text-xs text-ink-3">
            <span>
              Showing the newest {loadedRows.length} items; sort covers these only
              {query ? ", search also checks the full library by title, path, notes and exact tag" : ""}.
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void items.fetchNextPage()}
              disabled={items.isFetchingNextPage}
            >
              {items.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </p>
        )}
        {(items.isFetchNextPageError || (query && partial && remote.isError)) && (
          <Card className="mb-2 border-red bg-red-soft p-3 text-sm text-red" role="alert">
            {items.isFetchNextPageError
              ? `Couldn’t load more library items: ${items.error?.message ?? "request failed"}`
              : `Couldn’t search the full library: ${remote.error?.message ?? "request failed"}`}
          </Card>
        )}
        {items.isLoading ? (
          <SkeletonRows count={6} rowClassName="h-9" />
        ) : items.isError && !loadedRows.length ? (
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn’t load library items: {items.error?.message ?? "request failed"}
          </Card>
        ) : !loadedRows.length ? (
          <p className="text-sm text-ink-3">No library items.</p>
        ) : !visibleItems.length ? (
          <p className="text-sm text-ink-3">
            {query && partial && remote.isFetching ? "Searching the full library…" : "No items match"}
          </p>
        ) : (
          <ul
            data-view={itemView}
            className={isGrid ? "grid grid-cols-1 gap-2 sm:grid-cols-2" : "divide-y divide-border"}
          >
            {visibleItems.map((it) => (
              <li
                key={it.id}
                className={
                  isGrid
                    ? `hairline flex flex-col gap-2 rounded-md ${isCompact ? "p-1.5" : "p-3"}`
                    : `flex flex-wrap items-center justify-between gap-x-2 gap-y-1 ${isCompact ? "py-0.5" : "py-2"}`
                }
              >
                <span className="min-w-0 text-sm">
                  <span className="text-muted-foreground">#{it.id}</span>{" "}
                  <button
                    type="button"
                    className="text-left [overflow-wrap:anywhere] hover:underline focus-visible:underline"
                    onClick={() => setPreview(it)}
                    title="Show details"
                  >
                    {itemLabel(it)}
                  </button>
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={ratingText(it.rating)}
                    onChange={(e) =>
                      setRating.mutate({
                        id: it.id,
                        rating:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    disabled={busy}
                    title="Rating"
                    className="hairline rounded-md bg-surface px-1.5 py-1 text-xs tabular"
                  >
                    <option value="">none</option>
                    {ratingText(it.rating) && !RATING_OPTS.includes(ratingText(it.rating)) && (
                      <option value={ratingText(it.rating)} disabled>
                        {ratingText(it.rating)} (stored)
                      </option>
                    )}
                    {RATING_OPTS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      setWatched.mutate({ id: it.id, watched: !it.watched })
                    }
                    title="Toggle watched"
                  >
                    {it.watched ? "Watched" : "Unwatched"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      const tag = window.prompt("Tag name")?.trim();
                      if (tag) addTag.mutate({ id: it.id, tag });
                    }}
                    title="Add a tag"
                  >
                    Tag
                  </Button>
                  {historyIdOf(it) !== null && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => streamTokenMut.mutate(historyIdOf(it) as number)}
                      title="Generate a shareable streaming link"
                    >
                      <Link2 className="mr-1 h-4 w-4" /> Stream link
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() =>
                      arm({
                        kind: "item",
                        id: it.id,
                        label: itemLabel(it),
                        token: `DELETE ${it.id}`,
                      })
                    }
                  >
                    <Trash2 className="mr-1 h-4 w-4" /> Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Tags</h2>
        {tags.isLoading ? (
          <SkeletonRows count={4} rowClassName="h-8" />
        ) : tags.isError ? (
          // library-fix F2: a failed load used to read "No tags.".
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn’t load tags: {tags.error?.message ?? "request failed"}
          </Card>
        ) : !tags.data?.tags?.length ? (
          <p className="text-sm text-ink-3">No tags.</p>
        ) : (
          <>
            {/* library-fix F6b: a typed filter keeps its box after a refetch drops the count to the window. */}
            {(tagFilter !== "" || tags.data.tags.length > TAG_WINDOW) && (
              <Input
                type="search"
                aria-label="Filter tags"
                placeholder="Filter tags"
                value={tagFilter}
                onChange={(e) => setTagFilter(e.target.value)}
                className="mb-2 max-w-sm"
              />
            )}
            <ul className="divide-y divide-border">
              {shownTags.map((t) => (
                <li
                  key={String(t.id)}
                  className={`flex items-center justify-between ${isCompact ? "py-0.5" : "py-2"}`}
                >
                  <span className="text-sm">
                    <span className="text-muted-foreground">#{t.id}</span>{" "}
                    {tagLabel(t)}
                  </span>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() =>
                      arm({
                        kind: "tag",
                        id: t.id,
                        label: tagLabel(t),
                        token: `DELETE TAG ${t.id}`,
                      })
                    }
                  >
                    <Trash2 className="mr-1 h-4 w-4" /> Delete tag
                  </Button>
                </li>
              ))}
            </ul>
            {matchedTags.length > shownTags.length && (
              <p className="mt-2 text-xs text-ink-3">
                Showing {shownTags.length} of {matchedTags.length} tags — filter to find the rest.
              </p>
            )}
          </>
        )}
      </Card>

      {/* ── T3 (v3.66.207): library ops — audit · orphans · stats · NFOs ── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Library audit</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="Download dir to audit"
            value={auditDir}
            onChange={(e) => setAuditDir(e.target.value)}
            className="max-w-md"
          />
          <Button
            variant="outline"
            disabled={!auditDir.trim() || audit.isPending}
            onClick={() =>
              audit.mutate(
                { download_dir: auditDir.trim() },
                { onError: (e) => toast.error(e.message) },
              )
            }
          >
            Run audit
          </Button>
          <Button
            variant="outline"
            disabled={!auditDir.trim() || orphans.isPending}
            onClick={() =>
              orphans.mutate(
                { download_dir: auditDir.trim() },
                { onError: (e) => toast.error(e.message) },
              )
            }
          >
            Find orphans
          </Button>
        </div>
        {audit.data && (
          <p className="mt-2 text-sm text-muted-foreground">
            orphans {String(audit.data.orphans ?? 0)} · missing from disk{" "}
            {String(audit.data.missing ?? 0)} (live stat under{" "}
            {auditDir.trim() || "no directory"}) · duplicate groups{" "}
            {String(audit.data.duplicate_groups ?? 0)} · size drift{" "}
            {String(audit.data.size_drift ?? 0)} · orphan size{" "}
            {String(audit.data.orphan_size_gb ?? 0)} GB · reclaimable{" "}
            {String(audit.data.duplicate_reclaimable_gb ?? 0)} GB · window{" "}
            {String(audit.data.audit_row_limit ?? 0)} rows · missing capped{" "}
            {String(audit.data.missing_saturated ?? false)} · drift capped{" "}
            {String(audit.data.size_drift_saturated ?? false)}
          </p>
        )}
        {orphans.data && (
          <p className="mt-1 text-sm text-muted-foreground">
            orphans on disk: {orphans.data.orphans?.length ?? 0}
          </p>
        )}
        {/* library-fix F5: at a 200% text size these labels ran off a 390 px screen. */}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3 [&>button]:h-auto [&>button]:min-h-10 [&>button]:whitespace-normal">
          <span className="text-sm">NFO sidecars:</span>
          <Button
            variant="outline"
            disabled={regenNfos.isPending}
            onClick={() =>
              regenNfos.mutate(
                { dry_run: true },
                {
                  onSuccess: (r) =>
                    toast.success(
                      `Dry run: would write ${String(r.written ?? r.count ?? "?")}`,
                    ),
                  onError: (e) => toast.error(e.message),
                },
              )
            }
          >
            Preview regen (dry run)
          </Button>
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => arm({ kind: "regenNfos", overwrite: false, token: "" })}
          >
            Regen missing NFOs
          </Button>
        </div>
        {libStats.isError ? (
          <p className="mt-2 text-xs text-red" role="alert">
            Couldn’t load library stats: {libStats.error?.message ?? "request failed"}
          </p>
        ) : libStats.data?.stats && (
          <p className="mt-2 text-xs text-muted-foreground">
            stats: {Object.keys(libStats.data.stats).length} dimensions loaded
          </p>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Bulk tags</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="History ids (comma/space separated)"
            value={bulkIds}
            onChange={(e) => setBulkIds(e.target.value)}
            className="max-w-xs"
          />
          <Button
            variant="outline"
            disabled={busy || !bulkOk}
            onClick={() => tagsForMany.mutate({ history_ids: parseIds(bulkIds) })}
          >
            Load row tags
          </Button>
          <Input
            placeholder="Tag"
            value={bulkTag}
            onChange={(e) => setBulkTag(e.target.value)}
            className="max-w-[160px]"
          />
          <Button
            variant="outline"
            disabled={busy || !bulkTag.trim() || !bulkOk}
            onClick={() =>
              arm({ kind: "tagAdd", ids: bulk.ids, tag: bulkTag.trim(), token: "" })
            }
          >
            Add to rows
          </Button>
          <Button
            variant="destructive"
            disabled={busy || !bulkTag.trim() || !bulkOk}
            onClick={() =>
              arm({
                kind: "tagRemove",
                ids: bulk.ids,
                tag: bulkTag.trim(),
                token: "",
              })
            }
          >
            Remove from rows
          </Button>
        </div>
        {bulk.rejected.length > 0 && (
          <p className="mt-2 text-xs text-red" role="alert">
            Not history ids (fix them first): {bulk.rejected.join(" ")}
          </p>
        )}
        {tagsForMany.data?.tags && (
          <p className="mt-2 text-xs text-muted-foreground">
            loaded tags for {Object.keys(tagsForMany.data.tags).length} rows
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Input
            placeholder="Rename: old tag"
            value={renameFrom}
            onChange={(e) => setRenameFrom(e.target.value)}
            className="max-w-[160px]"
          />
          <Input
            placeholder="new tag"
            value={renameTo}
            onChange={(e) => setRenameTo(e.target.value)}
            className="max-w-[160px]"
          />
          <Button
            variant="destructive"
            disabled={busy || !renameFrom.trim() || !renameTo.trim()}
            onClick={() =>
              arm({
                kind: "tagRename",
                from: renameFrom.trim(),
                to: renameTo.trim(),
                token: "",
              })
            }
            title="Merges into an existing tag of the new name — not reversible"
          >
            Rename / merge
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap items-start gap-4 border-t border-border pt-3">
          <div>
            <Input
              placeholder="Rows with tag…"
              value={rowsTag}
              onChange={(e) => setRowsTag(e.target.value)}
              className="max-w-[180px]"
            />
            {rowsTag.trim() && (
              <p className="mt-1 text-xs text-muted-foreground">
                {tagRows.isLoading
                  ? "looking…"
                  : tagRows.isError
                    ? `Couldn’t load rows tagged "${rowsTag.trim()}": ${tagRows.error?.message ?? "request failed"}`
                    : `${tagRows.data?.rows?.length ?? 0} rows tagged "${rowsTag.trim()}"`}
              </p>
            )}
          </div>
          <div>
            <Input
              placeholder="Suggest tags for history id…"
              value={suggestHid}
              onChange={(e) => setSuggestHid(e.target.value)}
              className="max-w-[220px]"
            />
            {suggestN !== null && (
              <p className="mt-1 text-xs text-muted-foreground">
                {tagSuggest.isLoading
                  ? "inferring…"
                  : tagSuggest.isError
                    ? `Couldn’t load suggestions: ${tagSuggest.error?.message ?? "request failed"}`
                    : (tagSuggest.data?.suggested?.length ?? 0) > 0
                      ? `suggested: ${(tagSuggest.data?.suggested ?? []).join(", ")}`
                      : "no suggestions"}
              </p>
            )}
          </div>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Lowest-scored scenes</h2>
        {sceneBottom.isLoading ? (
          <SkeletonRows count={4} rowClassName="h-7" />
        ) : sceneBottom.isError ? (
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn’t load scene scores: {sceneBottom.error?.message ?? "request failed"}
          </Card>
        ) : !sceneBottom.data?.scenes?.length ? (
          <p className="text-sm text-ink-3">No scored scenes.</p>
        ) : (
          <ul className="divide-y divide-border">
            {sceneBottom.data.scenes.map((s, i) => (
              <li
                key={`${s.history_id ?? i}`}
                className={`flex items-center justify-between ${isCompact ? "py-0.5" : "py-1.5"}`}
              >
                <span className="truncate text-sm">
                  {s.filename || s.path || `#${s.history_id ?? i}`}
                </span>
                <span className="ml-2 shrink-0 text-sm text-muted-foreground">
                  {typeof s.score === "number" ? s.score.toFixed(1) : "—"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription className="[overflow-wrap:anywhere]">
              {pending?.kind === "item" &&
                `Delete library item "${pending.label}" (id ${pending.id}). This cannot be undone.`}
              {pending?.kind === "tag" &&
                `Delete tag "${pending.label}" (id ${pending.id}).`}
              {pending?.kind === "scanStart" &&
                `Start a library scan${pending.root ? ` of "${pending.root}"` : " (configured roots)"}.`}
              {pending?.kind === "scanCancel" && "Cancel the running library scan."}
              {pending?.kind === "rotateStream" &&
                "Rotate the stream-token signing secret. Every previously-issued stream link stops working immediately. This cannot be undone."}
              {pending?.kind === "tagAdd" &&
                `Apply tag "${pending.tag}" to ${pending.ids.length} history rows.`}
              {pending?.kind === "tagRemove" &&
                `Remove tag "${pending.tag}" from ${pending.ids.length} history rows.`}
              {pending?.kind === "tagRename" &&
                `Rename tag "${pending.from}" to "${pending.to}" on every row. If "${pending.to}" already exists this MERGES them — not reversible.`}
              {pending?.kind === "regenNfos" &&
                "Regenerate NFO sidecars from the history table (real run, not a dry run). Existing NFOs are kept; missing ones are written."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {pending && pending.token.length > 0 ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" disabled={busy} onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" disabled={busy} onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={streamLink !== null} onOpenChange={(o) => !o && setStreamLink(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Stream link</DialogTitle>
            <DialogDescription>
              Anyone with this URL can stream the file until it expires (
              {streamLink ? Math.round(streamLink.ttl / 60) : 0} min) or you rotate the stream
              secret.
            </DialogDescription>
          </DialogHeader>
          <Input readOnly value={streamLink?.url ?? ""} onFocus={(e) => e.currentTarget.select()} />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (streamLink) {
                  void navigator.clipboard?.writeText(streamLink.url);
                  toast.success("Copied");
                }
              }}
            >
              Copy
            </Button>
            <Button variant="ghost" onClick={() => setStreamLink(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={preview !== null} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="[overflow-wrap:anywhere]">{preview ? itemLabel(preview) : ""}</DialogTitle>
            <DialogDescription>Library item #{preview?.id}</DialogDescription>
          </DialogHeader>
          {preview && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Path</dt>
              <dd className="break-all">{itemPath(preview) || "—"}</dd>
              <dt className="text-muted-foreground">Rating</dt>
              <dd>{ratingText(preview.rating) || "none"}</dd>
              <dt className="text-muted-foreground">Watched</dt>
              <dd>{preview.watched ? "yes" : "no"}</dd>
              <dt className="text-muted-foreground">Tags</dt>
              <dd>
                {preview.tags?.length
                  ? preview.tags.map((t) => (typeof t === "string" ? t : tagLabel(t))).join(", ")
                  : "none"}
              </dd>
            </dl>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPreview(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <LiveSection />
    </AppShell>
  );
}
