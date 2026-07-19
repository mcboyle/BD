// MacrosOpsSection — T10 (v3.66.211) macro get/save/replay, mounted in the
// existing /pools-macros route (PoolsMacros). save = B-tier; replay opens a
// NESTED Playwright context (INV-001) → B-tier confirm carries the
// pause-workers warning. get is an ungated read.
import { useState } from "react";
import { toast } from "sonner";

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
import { useMacroGet, useMacroReplay, useMacroSave } from "@/hooks/useMacrosOps";

function errText(e: unknown): string {
  return e && typeof e === "object" && "message" in e ? String((e as Error).message) : String(e);
}

export function MacrosOpsSection() {
  const get = useMacroGet();
  const save = useMacroSave();
  const replay = useMacroReplay();

  const [sid, setSid] = useState("");
  const [name, setName] = useState("");
  const [confirmReplay, setConfirmReplay] = useState(false);

  const runGet = () => {
    if (!sid.trim() || !name.trim()) {
      toast.error("site id + macro name required");
      return;
    }
    get.mutate(
      { sid: sid.trim(), name: name.trim() },
      { onError: (e) => toast.error(errText(e)) },
    );
  };

  const runSave = () => {
    const m = get.data;
    if (!m) {
      toast.error("load a macro first");
      return;
    }
    save.mutate(
      {
        site_id: sid.trim(),
        name: name.trim(),
        actions: m.actions || [],
        description: m.description,
        tags: m.tags,
      },
      {
        onSuccess: () => toast.success("Macro saved"),
        onError: (e) => toast.error(errText(e)),
      },
    );
  };

  const runReplay = () => {
    setConfirmReplay(false);
    replay.mutate(
      { sid: sid.trim(), name: name.trim(), body: { headless: true } },
      {
        onSuccess: (r) => (r.ok ? toast.success("Replay complete") : toast.error(r.error || "replay failed")),
        onError: (e) => toast.error(errText(e)),
      },
    );
  };

  return (
    <Card className="mt-4 p-4">
      <h2 className="mb-2 font-medium">Macro inspect &amp; replay</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="max-w-[12rem]"
          placeholder="site id"
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          aria-label="macro site id"
        />
        <Input
          className="max-w-[14rem]"
          placeholder="macro name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label="macro name"
        />
        <Button variant="outline" onClick={runGet} disabled={get.isPending}>
          Load
        </Button>
        <Button variant="destructive" onClick={runSave} disabled={save.isPending || !get.data}>
          Save
        </Button>
        <Button
          variant="destructive"
          onClick={() => setConfirmReplay(true)}
          disabled={replay.isPending || !sid || !name}
        >
          Replay…
        </Button>
      </div>
      {get.data?.actions && (
        <p className="mt-2 text-xs text-muted-foreground">
          {get.data.actions.length} action(s) loaded.
        </p>
      )}

      <Dialog open={confirmReplay} onOpenChange={setConfirmReplay}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Replay macro</DialogTitle>
            <DialogDescription>
              This opens a nested browser context. Pause any running workers for this site first to
              avoid a collision (INV-001). Proceed?
            </DialogDescription>
          </DialogHeader>
          <p className="font-mono text-xs text-amber-300">REPLAY {sid}/{name} — pause workers first</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmReplay(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={runReplay}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
