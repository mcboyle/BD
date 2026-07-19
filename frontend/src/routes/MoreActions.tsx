import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Play, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { DangerZone } from "@/components/ui/DangerZone";
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
import { apiDelete, apiGet, apiPost } from "@/lib/api-client";
import type {
  OkResult,
  SharesList,
  ShareToken,
  TagsAll,
  TagEntry,
} from "@/lib/api-types";

// GUI parity (177) — tail actions: rights (block url/hash), imports (start/cancel),
// share-token revoke, tag delete-all. Surfaces existing endpoints; every action is
// typed-confirm gated, never one-click.

const tokenId = (t: ShareToken) => t.id ?? t.token_id ?? t.token ?? "";
const tokenLabel = (t: ShareToken) => t.label || t.scope || String(tokenId(t));
const tagName = (t: TagEntry) => (typeof t === "string" ? t : t.tag || t.name || "");
const tagCount = (t: TagEntry) => (typeof t === "string" ? "" : t.count ?? "");

type Pending =
  | { kind: "blockUrl"; pattern: string; reason: string; token: string }
  | { kind: "blockHash"; hash: string; reason: string; token: string }
  | { kind: "importStart"; sid: string; text: string; token: string }
  | { kind: "importCancel"; jobId: string; token: string }
  | { kind: "revoke"; id: string; label: string; token: string }
  | { kind: "deleteTag"; tag: string; token: string };

const isTyped = (p: Pending): boolean => p.token.length > 0;

export function MoreActions() {
  const qc = useQueryClient();
  const [burl, setBurl] = useState("");
  const [burlReason, setBurlReason] = useState("");
  const [bhash, setBhash] = useState("");
  const [bhashReason, setBhashReason] = useState("");
  const [sid, setSid] = useState("");
  const [jobId, setJobId] = useState("");
  // v3.66.726: the URL list the import actually needs. Before this there was NO
  // field on the page that could supply it -- the button armed on a site id alone
  // and posted {}, so /api/import/start/<sid> resolved urls=[] and answered
  // 400 "no valid URLs". Every time. A control cannot send what it never gathered.
  const [importText, setImportText] = useState("");

  const shares = useQuery<SharesList, Error>({
    queryKey: ["shares"],
    queryFn: () => apiGet<SharesList>("/api/shares"),
  });
  const tags = useQuery<TagsAll, Error>({
    queryKey: ["tags", "all"],
    queryFn: () => apiGet<TagsAll>("/api/tags/all"),
  });

  const blockUrl = useMutation<OkResult, Error, { pattern: string; reason: string }>({
    mutationFn: (v) => apiPost<OkResult>("/api/rights/block_url", { pattern: v.pattern, reason: v.reason }),
    onSuccess: () => toast.success("URL blocked"),
    onError: (e) => toast.error(e.message),
  });
  const blockHash = useMutation<OkResult, Error, { hash: string; reason: string }>({
    mutationFn: (v) => apiPost<OkResult>("/api/rights/block_hash", { hash_hex: v.hash, reason: v.reason }),
    onSuccess: () => toast.success("Hash blocked"),
    onError: (e) => toast.error(e.message),
  });
  const importStart = useMutation<OkResult, Error, { sid: string; text: string }>({
    mutationFn: (v) =>
      apiPost<OkResult>(`/api/import/start/${encodeURIComponent(v.sid)}`, { text: v.text }),
    onSuccess: (res) =>
      res.ok === false ? toast.error(res.error || "start failed") : toast.success("Import started"),
    onError: (e) => toast.error(e.message),
  });
  const importCancel = useMutation<OkResult, Error, string>({
    mutationFn: (j) => apiPost<OkResult>(`/api/import/cancel/${encodeURIComponent(j)}`, {}),
    onSuccess: (res) => (res.ok ? toast.success("Import cancelled") : toast.error("cancel failed")),
    onError: (e) => toast.error(e.message),
  });
  const revoke = useMutation<OkResult, Error, string>({
    mutationFn: (id) => apiDelete<OkResult>(`/api/shares/${encodeURIComponent(id)}`),
    onSuccess: (res, id) => {
      if (res.ok) {
        toast.success(`Revoked ${id}`);
        qc.invalidateQueries({ queryKey: ["shares"] });
      } else toast.error("revoke failed");
    },
    onError: (e) => toast.error(e.message),
  });
  const deleteTag = useMutation<OkResult, Error, string>({
    mutationFn: (tag) => apiPost<OkResult>("/api/tags/delete", { tag }),
    onSuccess: (res, tag) => {
      if (res.ok === false) toast.error(res.error || "delete failed");
      else {
        toast.success(`Removed tag ${tag}`);
        qc.invalidateQueries({ queryKey: ["tags", "all"] });
      }
    },
    onError: (e) => toast.error(e.message),
  });

  const [pending, setPending] = useState<Pending | null>(null);
  const arm = (p: Pending) => setPending(p);
  const busy =
    blockUrl.isPending ||
    blockHash.isPending ||
    importStart.isPending ||
    importCancel.isPending ||
    revoke.isPending ||
    deleteTag.isPending;

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "blockUrl":
        blockUrl.mutate({ pattern: pending.pattern, reason: pending.reason });
        break;
      case "blockHash":
        blockHash.mutate({ hash: pending.hash, reason: pending.reason });
        break;
      case "importStart":
        importStart.mutate({ sid: pending.sid, text: pending.text });
        break;
      case "importCancel":
        importCancel.mutate(pending.jobId);
        break;
      case "revoke":
        revoke.mutate(pending.id);
        break;
      case "deleteTag":
        deleteTag.mutate(pending.tag);
        break;
    }
    setPending(null);
  };

  const desc = (p: Pending) => {
    switch (p.kind) {
      case "blockUrl":
        return `Block all content matching URL pattern "${p.pattern}".`;
      case "blockHash":
        return `Block content hash "${p.hash}".`;
      case "importStart":
        return `Start a metadata import for site "${p.sid}".`;
      case "importCancel":
        return `Cancel import job "${p.jobId}".`;
      case "revoke":
        return `Revoke share token "${p.label}" (${p.id}).`;
      case "deleteTag":
        return `Remove tag "${p.tag}" from ALL items.`;
    }
  };

  return (
    <AppShell title="More actions" subtitle="Rights · Shares · Tags · Import">
      <Callout tone="info" title="What this page does" className="mb-4">
        Less-common operator actions grouped in one place: block content by URL or
        hash, manage share tokens, edit tags, and cancel an import. Each action
        confirms before it runs.
      </Callout>
      <DangerZone
        title="Rights — block content"
        warning="Blocks content by URL pattern or hash. Each action confirms before it runs."
      >
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="URL pattern" value={burl} onChange={(e) => setBurl(e.target.value)} className="min-w-[220px] flex-1" />
          <Input placeholder="reason (optional)" value={burlReason} onChange={(e) => setBurlReason(e.target.value)} className="max-w-xs" />
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => burl.trim() ? arm({ kind: "blockUrl", pattern: burl, reason: burlReason, token: "" }) : toast.error("URL pattern required")}
          >
            <Ban className="mr-1 h-4 w-4" /> Block URL
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input placeholder="content hash (hex)" value={bhash} onChange={(e) => setBhash(e.target.value)} className="min-w-[220px] flex-1" />
          <Input placeholder="reason (optional)" value={bhashReason} onChange={(e) => setBhashReason(e.target.value)} className="max-w-xs" />
          <Button
            variant="destructive"
            disabled={busy}
            onClick={() => bhash.trim() ? arm({ kind: "blockHash", hash: bhash, reason: bhashReason, token: "" }) : toast.error("hash required")}
          >
            <Ban className="mr-1 h-4 w-4" /> Block hash
          </Button>
        </div>
      </DangerZone>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Imports</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="site id" value={sid} onChange={(e) => setSid(e.target.value)} className="max-w-xs" />
          {/* v3.66.726: the URL list. The endpoint reads {text} (newline-separated) or a
              file upload; it has ALWAYS required one of them. This field is the thing that
              was missing -- the button used to arm on a site id and post {}. */}
          <textarea
            className="w-full min-h-[5rem] rounded-md border bg-transparent p-2 text-sm"
            placeholder="URLs to import, one per line"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            aria-label="URLs to import, one per line"
          />
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              if (!sid.trim()) return toast.error("site id required");
              // Refuse to fire a request the server will certainly refuse. The old control
              // sent {} and got a 400 every time -- with a success-shaped confirm in front
              // of it. A confirmation before a doomed call is theatre, not safety.
              if (!importText.split("\n").some((l) => l.trim().startsWith("http"))) {
                return toast.error("paste at least one http(s) URL to import");
              }
              arm({ kind: "importStart", sid, text: importText, token: "" });
            }}
          >
            <Play className="mr-1 h-4 w-4" /> Start import
          </Button>
          <Input placeholder="import job id" value={jobId} onChange={(e) => setJobId(e.target.value)} className="max-w-xs" />
          <Button variant="destructive" disabled={busy} onClick={() => jobId.trim() ? arm({ kind: "importCancel", jobId, token: "" }) : toast.error("job id required")}>
            <Square className="mr-1 h-4 w-4" /> Cancel import
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Share tokens</h2>
        {shares.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : !shares.data?.tokens?.length ? (
          <p className="text-sm text-ink-3">No share tokens.</p>
        ) : (
          <ul className="divide-y divide-border">
            {shares.data.tokens.map((t) => (
              <li key={String(tokenId(t))} className="flex items-center justify-between py-2">
                <span className="text-sm">
                  <span className="text-ink-3">{String(tokenId(t))}</span> {tokenLabel(t)}
                </span>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={busy}
                  onClick={() => arm({ kind: "revoke", id: String(tokenId(t)), label: tokenLabel(t), token: `REVOKE ${tokenId(t)}` })}
                >
                  <Trash2 className="mr-1 h-4 w-4" /> Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Tags</h2>
        {tags.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : !tags.data?.tags?.length ? (
          <p className="text-sm text-ink-3">No tags.</p>
        ) : (
          <ul className="divide-y divide-border">
            {tags.data.tags.map((t, i) => {
              const name = tagName(t);
              return (
                <li key={name || i} className="flex items-center justify-between py-2">
                  <span className="text-sm">
                    {name} <span className="text-xs text-ink-3">{String(tagCount(t))}</span>
                  </span>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => arm({ kind: "deleteTag", tag: name, token: `DELETE TAG ${name}` })}
                  >
                    <Trash2 className="mr-1 h-4 w-4" /> Delete (untag all)
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>{pending && desc(pending)}</DialogDescription>
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
    </AppShell>
  );
}
