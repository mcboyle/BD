import { useMutation } from "@tanstack/react-query";
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
import { apiPost } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

// F035 — teach-session controls. The runner parks a site's first URL in
// needs_review saying "take over to teach download selectors", and the three
// endpoints that do it (take_over_url / take_over_done / take_over_cancel)
// had no SPA caller. Each is wired with a FULL /api/sites/${...}/<segment>
// literal so the reachability + body-contract scanners can resolve it.
//
// The card keeps no "session open" state of its own: a takeover can be started
// from the CLI or another tab, so Done and Cancel stay available and the
// server's own answer ("No pending manual download") is what gets shown.

type TeachStep = "start" | "done" | "cancel";

type TeachResult = OkResult & {
  message?: string;
  learned_summary?: Record<string, unknown>;
};

const STEP_LABEL: Record<TeachStep, string> = {
  start: "Take over to teach",
  done: "I'm Done",
  cancel: "Cancel teach",
};

function summaryText(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function TeachSessionCard({
  siteId,
  initialUrl = "",
}: {
  siteId: string;
  initialUrl?: string;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [pending, setPending] = useState<TeachStep | null>(null);
  const [result, setResult] = useState<{ step: TeachStep; res: TeachResult } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const target = url.trim();
  // Same test the runner applies (start_manual_download: startswith("http")),
  // narrowed to a real scheme so a refused start never costs a round trip.
  const urlOk = /^https?:\/\//.test(target);

  const run = useMutation<TeachResult, Error, TeachStep>({
    mutationFn: (step) => {
      const sid = encodeURIComponent(siteId);
      if (step === "start") {
        return apiPost<TeachResult>(`/api/sites/${sid}/take_over_url`, { url: target });
      }
      if (step === "done") {
        return apiPost<TeachResult>(`/api/sites/${sid}/take_over_done`);
      }
      return apiPost<TeachResult>(`/api/sites/${sid}/take_over_cancel`);
    },
    onSuccess: (res, step) => {
      const reason = res.error || (typeof res.message === "string" ? res.message : "");
      if (res.ok === false) {
        setResult(null);
        setFailure(reason || `${STEP_LABEL[step]} failed`);
        toast.error(reason || `${STEP_LABEL[step]} failed`);
        return;
      }
      setFailure(null);
      setResult({ step, res });
      toast.success(reason || "Done");
    },
    onError: (e) => {
      setResult(null);
      setFailure(e.message);
      toast.error(e.message);
    },
  });

  const confirmRun = () => {
    if (!pending) return;
    run.mutate(pending);
    setPending(null);
  };

  const learned = Object.entries(result?.res.learned_summary ?? {});

  return (
    <Card className="mb-3 p-4">
      <h2 className="mb-2 text-sm font-semibold">Teach session</h2>
      <p className="mb-2 text-xs text-muted-foreground">
        Opens a takeover browser at the URL with this site&apos;s cookies. Click
        through the download there, then come back: I&apos;m Done saves the
        selectors it recorded, Cancel teach discards them.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Teach URL"
          className="min-w-64 flex-1"
          placeholder="https://…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={run.isPending || !siteId || !urlOk}
          onClick={() => setPending("start")}
        >
          {STEP_LABEL.start}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={run.isPending || !siteId}
          onClick={() => setPending("done")}
        >
          {STEP_LABEL.done}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={run.isPending || !siteId}
          onClick={() => setPending("cancel")}
        >
          {STEP_LABEL.cancel}
        </Button>
      </div>
      {target && !urlOk ? (
        <p className="mt-2 text-xs text-amber">Enter an http(s) URL to take over.</p>
      ) : null}

      {failure !== null ? (
        <p role="alert" className="mt-3 whitespace-pre-wrap text-sm">{failure}</p>
      ) : null}
      {result ? (
        <div role="status" aria-label="Teach session result" className="mt-3 text-sm">
          <p>{result.res.message || `${STEP_LABEL[result.step]}: ok`}</p>
          {result.step === "done" ? (
            learned.length > 0 ? (
              <dl aria-label="Learned selectors" className="mt-2 grid gap-1 text-xs">
                {learned.map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <dt className="font-mono text-muted-foreground">{key}</dt>
                    <dd className="break-all font-mono">{summaryText(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">No selectors were learned.</p>
            )
          ) : null}
        </div>
      ) : null}

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending === "cancel" ? "Cancel teaching session?" : "Confirm teach action"}
            </DialogTitle>
            <DialogDescription>
              {pending === "start" && `Open a takeover browser for site "${siteId}" at ${target}.`}
              {pending === "done" && `Finish the teach session for site "${siteId}" and save what it learned.`}
              {pending === "cancel" && `Close the takeover browser for site "${siteId}". Nothing it recorded is saved.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            {pending === "cancel" ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, keep teaching
                </Button>
                <Button variant="destructive" disabled={run.isPending} onClick={confirmRun}>
                  Yes, cancel teach
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Back
                </Button>
                <Button variant="default" disabled={run.isPending} onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
