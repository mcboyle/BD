import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api-client";
import type { OkResult, SavedView, SavedViewsList } from "@/lib/api-types";

// GUI parity (177) — Import plan preview (never executes) + saved-view deletes.
// These surface the EXISTING cockpit endpoints under /cockpit/api/*. Preview is
// pure read; delete is gated by a yes/no confirm (No default), never one-click.

const viewLabel = (v: SavedView) => v.name || v.title || `#${v.id}`;

export function ImportViews() {
  const qc = useQueryClient();
  const [csv, setCsv] = useState("");
  const [plan, setPlan] = useState<unknown>(null);

  const views = useQuery<SavedViewsList, Error>({
    queryKey: ["saved-views"],
    queryFn: () => apiGet<SavedViewsList>("/cockpit/api/saved-views"),
  });

  const preview = useMutation<unknown, Error, void>({
    mutationFn: () => apiPost<unknown>("/cockpit/api/import-plan/preview", { csv }),
    onSuccess: (res) => {
      setPlan(res);
      toast.success("Previewed (nothing executed)");
    },
    onError: (e) => toast.error(e.message),
  });

  const delView = useMutation<OkResult, Error, number | string>({
    mutationFn: (id) => apiPost<OkResult>("/cockpit/api/saved-views/delete", { id }),
    onSuccess: (res, id) => {
      toast.success(`Deleted view ${id}`);
      qc.invalidateQueries({ queryKey: ["saved-views"] });
      void res;
    },
    onError: (e) => toast.error(e.message),
  });

  const [pending, setPending] = useState<
    { id: number | string; label: string; token: string } | null
  >(null);

  const confirmRun = () => {
    if (!pending) return;
    delView.mutate(pending.id);
    setPending(null);
  };

  return (
    <AppShell title="Import & Saved Views" subtitle="Plan preview and saved-view cleanup">
      <Callout tone="info" title="What this page does" className="mb-4">
        Saved views let you reuse import plans and review results without repeating setup.
      </Callout>
      <Card className="p-4">
        <h2 className="section-head">
          Import plan preview{" "}
          <span className="text-xs font-normal text-ink-3">never executes</span>
        </h2>
        <textarea
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
          placeholder="paste planning CSV here"
          className="h-28 w-full rounded border border-border bg-muted p-2 font-mono text-xs"
        />
        <div className="mt-2">
          <Button variant="outline" disabled={preview.isPending} onClick={() => preview.mutate()}>
            <Eye className="mr-1 h-4 w-4" /> Preview plan
          </Button>
        </div>
        {plan !== null && (
          <pre className="mt-3 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">
            {JSON.stringify(plan, null, 2)}
          </pre>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Saved views</h2>
        {views.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : !views.data?.views?.length ? (
          <EmptyState
            bare
            title="No saved views yet"
            hint="Create one from an import plan or a search result and it will show up here."
          />
        ) : (
          <ul className="divide-y divide-border">
            {views.data.views.map((v) => (
              <li key={String(v.id)} className="flex items-center justify-between py-2">
                <span className="text-sm">
                  <span className="text-ink-3">#{v.id}</span> {viewLabel(v)}
                  {v.kind && <span className="ml-2 text-xs text-ink-3">{v.kind}</span>}
                </span>
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={delView.isPending}
                  onClick={() => {
                    setPending({ id: v.id, label: viewLabel(v), token: `DELETE VIEW ${v.id}` });
                  }}
                >
                  <Trash2 className="mr-1 h-4 w-4" /> Delete view
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete saved view</DialogTitle>
            <DialogDescription>
              {pending && `Delete saved view "${pending.label}" (id ${pending.id}).`}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-ink-3">
            This is destructive and cannot be undone. Proceed?
          </p>
          {pending && <p className="font-mono text-xs text-amber-300">{pending.token}</p>}
          <DialogFooter>
            <Button autoFocus variant="default" onClick={() => setPending(null)}>
              No, cancel
            </Button>
            <Button
              variant="destructive"
              disabled={!pending || delView.isPending}
              onClick={confirmRun}
            >
              Yes, proceed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
