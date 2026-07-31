import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Play, RotateCcw, Square, Trash2 } from "lucide-react";
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

export function Library() {
  const qc = useQueryClient();
  const { isCompact } = useDensity();

  const items = useQuery<LibraryBrowse, Error>({
    queryKey: ["library", "browse"],
    queryFn: () => apiGet<LibraryBrowse>("/api/library/browse?limit=200"),
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
  const itemRows = items.data?.rows ?? [];
  const itemsSort = useTableSort(itemRows, {
    accessors: {
      added: (r) => (typeof r.added_at === "number" ? r.added_at : null),
      title: (r) => itemLabel(r),
      size: (r) => (typeof r.file_size === "number" ? r.file_size : null),
      rating: (r) => (typeof r.rating === "number" ? r.rating : null),
      duration: (r) => (typeof r.duration_s === "number" ? r.duration_s : null),
    },
  });

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
  const streamTokenMut = useMutation<
    { ok?: boolean; error?: string; token?: string; history_id?: number },
    Error,
    number
  >({
    // T9a: FULL /api/ literal (inline ${hid} path param) so legacy_parity
    // credits it spa_wired — the earlier string-concat form worked at runtime
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
  const parseIds = (raw: string): number[] =>
    raw
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter((t) => /^\d+$/.test(t))
      .map(Number);

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

  const scanState =
    scan.data?.scan?.state ??
    scan.data?.scan?.status ??
    (scan.data?.scan?.running ? "running" : "idle");

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
        <span className="text-sm text-muted-foreground">scan: {scanState}</span>
        <Button
          variant="destructive"
          disabled={busy}
          onClick={() => arm({ kind: "rotateStream", token: "ROTATE STREAM" })}
          title="Invalidate every previously-issued stream link"
        >
          <RotateCcw className="mr-1 h-4 w-4" /> Rotate stream secret
        </Button>
        <div className="ml-auto">
          <DensityToggle />
        </div>
      </div>

      <Card className="mt-4 p-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="section-head mb-0">Items</h2>
          {!!items.data?.rows?.length && (
            <SortSelect
              options={ITEM_SORT_OPTS}
              sortKey={itemsSort.sortKey}
              dir={itemsSort.sortDir}
              onSet={itemsSort.setSort}
            />
          )}
        </div>
        {items.isLoading ? (
          <SkeletonRows count={6} rowClassName="h-9" />
        ) : !items.data?.rows?.length ? (
          <p className="text-sm text-ink-3">No library items.</p>
        ) : (
          <ul className="divide-y divide-border">
            {itemsSort.sorted.map((it) => (
              <li
                key={it.id}
                className={`flex items-center justify-between ${isCompact ? "py-0.5" : "py-2"}`}
              >
                <span className="text-sm">
                  <span className="text-muted-foreground">#{it.id}</span>{" "}
                  {itemLabel(it)}
                </span>
                <div className="flex items-center gap-2">
                  <select
                    value={typeof it.rating === "number" ? String(it.rating) : ""}
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
                    {[1, 2, 3, 4, 5].map((r) => (
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
        ) : !tags.data?.tags?.length ? (
          <p className="text-sm text-ink-3">No tags.</p>
        ) : (
          <ul className="divide-y divide-border">
            {tags.data.tags.map((t) => (
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
            {String(audit.data.missing ?? 0)} · duplicate groups{" "}
            {String(audit.data.duplicate_groups ?? 0)} · size drift{" "}
            {String(audit.data.size_drift ?? 0)} · orphan size{" "}
            {String(audit.data.orphan_size_gb ?? 0)} GB · reclaimable{" "}
            {String(audit.data.duplicate_reclaimable_gb ?? 0)} GB
          </p>
        )}
        {orphans.data && (
          <p className="mt-1 text-sm text-muted-foreground">
            orphans on disk: {orphans.data.orphans?.length ?? 0}
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
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
        {libStats.data?.stats && (
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
          <Input
            placeholder="Tag"
            value={bulkTag}
            onChange={(e) => setBulkTag(e.target.value)}
            className="max-w-[160px]"
          />
          <Button
            variant="outline"
            disabled={busy || !bulkTag.trim() || parseIds(bulkIds).length === 0}
            onClick={() =>
              arm({ kind: "tagAdd", ids: parseIds(bulkIds), tag: bulkTag.trim(), token: "" })
            }
          >
            Add to rows
          </Button>
          <Button
            variant="destructive"
            disabled={busy || !bulkTag.trim() || parseIds(bulkIds).length === 0}
            onClick={() =>
              arm({
                kind: "tagRemove",
                ids: parseIds(bulkIds),
                tag: bulkTag.trim(),
                token: "",
              })
            }
          >
            Remove from rows
          </Button>
        </div>
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
            <DialogDescription>
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

      <LiveSection />
    </AppShell>
  );
}
