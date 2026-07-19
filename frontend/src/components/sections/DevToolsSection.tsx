// DevToolsSection — T10 (v3.66.211) developer/diagnostics surface, mounted in
// the existing /settings/advanced route (Advanced). The Dev console renders
// only when /api/dev/enabled reports enabled (release trees default OFF).
// dev/run + synthetic_tests/run_all are B-tier confirms; plugins / fixtures /
// i18n / discover are reads.
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
  useDevDiscover,
  useDevEnabled,
  useDevRun,
  useDevRunStatus,
  useI18nLoad,
  usePluginsEvents,
  usePluginsStatus,
  useSyntheticFixtures,
  useSyntheticRunAll,
} from "@/hooks/useDevTools";

function errText(e: unknown): string {
  return e && typeof e === "object" && "message" in e ? String((e as Error).message) : String(e);
}

export function DevToolsSection() {
  const devEnabled = useDevEnabled();
  const enabled = !!devEnabled.data?.enabled;

  const discover = useDevDiscover(enabled);
  const run = useDevRun();
  const [runId, setRunId] = useState<string | null>(null);
  const runStatus = useDevRunStatus(runId);
  const plugins = usePluginsStatus();
  const pluginEvents = usePluginsEvents();
  const fixtures = useSyntheticFixtures();
  const runAll = useSyntheticRunAll();
  const [lang, setLang] = useState<string | null>(null);
  const i18n = useI18nLoad(lang);

  const [target, setTarget] = useState("");
  const [confirmRun, setConfirmRun] = useState(false);
  const [confirmRunAll, setConfirmRunAll] = useState(false);

  const doRun = () => {
    setConfirmRun(false);
    run.mutate(
      { target: target.trim(), kind: "file" },
      {
        onSuccess: (r) => (r.run_id ? setRunId(r.run_id) : toast.error(r.error || "run failed")),
        onError: (e) => toast.error(errText(e)),
      },
    );
  };

  const doRunAll = () => {
    setConfirmRunAll(false);
    runAll.mutate(undefined, {
      onSuccess: () => toast.success("Synthetic fixtures replayed"),
      onError: (e) => toast.error(errText(e)),
    });
  };

  return (
    <Card className="mt-4 p-4">
      <h2 className="mb-2 font-medium">Developer &amp; diagnostics</h2>

      {/* Plugins + synthetic fixtures + i18n are always available reads */}
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span>plugins loaded: {plugins.data?.loaded?.length ?? "—"}</span>
        <span>hook events: {pluginEvents.data?.events?.length ?? "—"}</span>
        <span>fixtures: {fixtures.data?.fixtures?.length ?? "—"}</span>
        <Button variant="destructive" size="sm" onClick={() => setConfirmRunAll(true)} disabled={runAll.isPending}>
          Run all synthetic tests…
        </Button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input
          className="max-w-[12rem]"
          placeholder="locale (e.g. es)"
          value={lang || ""}
          onChange={(e) => setLang(e.target.value || null)}
          aria-label="i18n locale"
        />
        {i18n.data?.strings && (
          <span className="text-xs text-muted-foreground">
            {Object.keys(i18n.data.strings).length} string(s) for {i18n.data.lang}
          </span>
        )}
      </div>

      {/* Dev test runner — only when dev mode is enabled on the backend */}
      {enabled ? (
        <div className="mt-3 border-t border-border/40 pt-3">
          <p className="mb-1 text-xs font-medium">
            Dev test runner ({discover.data?.files?.length ?? 0} files discovered)
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="max-w-[18rem]"
              placeholder="test target (file or node id)"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              aria-label="dev run target"
            />
            <Button variant="destructive" onClick={() => setConfirmRun(true)} disabled={run.isPending || !target}>
              Run…
            </Button>
          </div>
          {runStatus.data && (
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
              [{runStatus.data.state}] {runStatus.data.output}
            </pre>
          )}
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          Dev mode is disabled on this deployment — the test runner is hidden.
        </p>
      )}

      <Dialog open={confirmRun} onOpenChange={setConfirmRun}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Start dev test run</DialogTitle>
            <DialogDescription>Run the selected test target on this host?</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmRun(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={doRun}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmRunAll} onOpenChange={setConfirmRunAll}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Run all synthetic tests</DialogTitle>
            <DialogDescription>
              Replay every configured fixture (HAR replay, no live network). Proceed?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmRunAll(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={doRunAll}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
