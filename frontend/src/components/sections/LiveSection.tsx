// LiveSection — T9a (v3.66.212) live-recording surface, mounted in the
// existing /library route. Replaces the legacy static/live_recorder.js floating
// pill/panel. Status polls via useLiveStatus (5s). watch + unwatch are B-tier
// confirms (page-level Pending, never one-click). No secret inputs here; the
// stream-token mint lives on Library's history rows.
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
import {
  useLiveParseUrl,
  useLiveRecordings,
  useLiveStatus,
  useLiveUnwatch,
  useLiveWatch,
} from "@/hooks/useLive";
import { formatBytes } from "@/lib/format";
import type { LiveRecording } from "@/lib/api-types";

function errText(e: unknown): string {
  return e && typeof e === "object" && "message" in e
    ? String((e as Error).message)
    : String(e);
}

function fmtBytes(n?: number): string {
  return !n || n <= 0 ? "—" : formatBytes(n);
}

export function LiveSection() {
  const status = useLiveStatus();
  const recordings = useLiveRecordings();
  const watch = useLiveWatch();
  const unwatch = useLiveUnwatch();
  const parseUrl = useLiveParseUrl();

  const [url, setUrl] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [confirmWatch, setConfirmWatch] = useState(false);
  const [cancelId, setCancelId] = useState<string | null>(null);
  const [checkResult, setCheckResult] = useState<string | null>(null);

  const available = !!status.data?.available;
  const activeCount = status.data?.active_count ?? 0;
  const maxActive = status.data?.max_active ?? 0;
  const recs = recordings.data?.recordings ?? [];

  const doWatch = () => {
    setConfirmWatch(false);
    watch.mutate(
      { url: url.trim(), output_dir: outputDir.trim() },
      {
        onSuccess: (r) => {
          if (r.ok) {
            toast.success("Watch armed");
            setUrl("");
          } else {
            toast.error(r.message || r.error || "watch failed");
          }
        },
        onError: (e) => toast.error(errText(e)),
      },
    );
  };

  const doUnwatch = (rid: string) => {
    setCancelId(null);
    unwatch.mutate(
      { recording_id: rid },
      {
        onSuccess: (r) =>
          r.ok
            ? toast.success("Recording cancelled")
            : toast.error(r.error || "cancel failed"),
        onError: (e) => toast.error(errText(e)),
      },
    );
  };

  const doCheck = () => {
    setCheckResult(null);
    parseUrl.mutate(
      { url: url.trim() },
      {
        onSuccess: (r) => {
          if (!r.ok) {
            setCheckResult(r.error || "check failed");
          } else if (r.recognized) {
            setCheckResult(`Recognized: ${r.site ?? "?"} / ${r.room ?? "?"}`);
          } else {
            setCheckResult("Not a recognized live-cam URL");
          }
        },
        onError: (e) => setCheckResult(errText(e)),
      },
    );
  };

  const canWatch =
    url.trim() !== "" && outputDir.trim() !== "" && available && !watch.isPending;
  const canCheck = url.trim() !== "" && !parseUrl.isPending;

  return (
    <Card className="mt-4 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Live recordings</h2>
        <span className="text-xs text-ink-3">
          {available ? `${activeCount}/${maxActive} active` : "backend unavailable"}
        </span>
      </div>

      {/* Watch form — B-tier (arms a bounded recording) */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Live URL"
          className="w-64"
        />
        <Input
          value={outputDir}
          onChange={(e) => setOutputDir(e.target.value)}
          placeholder="Output dir"
          className="w-56"
        />
        <Button
          variant="outline"
          disabled={!canCheck}
          onClick={doCheck}
          title="Check whether this URL is a recognized live-cam URL (no watch armed)"
        >
          Check
        </Button>
        <Button disabled={!canWatch} onClick={() => setConfirmWatch(true)}>
          Watch
        </Button>
        {checkResult !== null && (
          <span className="text-xs text-ink-3">{checkResult}</span>
        )}
      </div>

      {/* Recordings list */}
      <div className="mt-3 border-t border-border pt-3">
        {recs.length === 0 ? (
          <p className="text-xs text-ink-3">No recordings.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {recs.map((r: LiveRecording) => (
              <li key={r.id} className="flex items-center gap-2 text-sm">
                <span className="font-mono text-xs text-ink-3">{r.state}</span>
                <span className="truncate">{r.site || r.url}</span>
                <span className="ml-auto tabular-nums text-xs text-ink-3">
                  {fmtBytes(r.bytes)}
                </span>
                {r.state === "recording" && r.id && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setCancelId(r.id!)}
                  >
                    Cancel
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* B-tier watch confirm */}
      <Dialog open={confirmWatch} onOpenChange={setConfirmWatch}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Arm live recording?</DialogTitle>
            <DialogDescription>
              This starts a background recording against the live URL and
              consumes one of {maxActive} active slots.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmWatch(false)}>
              No, cancel
            </Button>
            <Button onClick={doWatch}>Arm recording</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* B-tier unwatch confirm */}
      <Dialog
        open={cancelId !== null}
        onOpenChange={(o) => !o && setCancelId(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel this recording?</DialogTitle>
            <DialogDescription>
              The in-progress recording will be stopped.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCancelId(null)}>
              No, keep it
            </Button>
            <Button onClick={() => cancelId && doUnwatch(cancelId)}>
              Cancel recording
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
