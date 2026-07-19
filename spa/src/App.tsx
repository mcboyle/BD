// App.tsx — v3.66.7 wiring of ResolutionPicker into a real SPA shell.
//
// Backlog #3 from v3_66_6_handoff.txt: the ResolutionPicker component
// shipped in v3.66.6 was unit-tested but not actually used from
// anywhere. This is a minimal opinionated shell that exercises the
// real flow:
//
//   1. User pastes a URL or HTML.
//   2. We POST to /api/dev/deep_detect (the same endpoint bdctl
//      deep-detect drives).
//   3. If the report has >=2 resolution_download_card candidates, we
//      open <ResolutionPicker/>; otherwise we just use best_download
//      silently and surface that.
//   4. On confirm, we hand the selected candidate to onQueue (which
//      in production would POST to /api/jobs; here it just shows the
//      payload).
//
// Intentionally tiny and unopinionated about routing/layout/state mgmt
// so it can be ripped out and replaced with the real m2/ app shell
// later. The point is to prove the picker → queue handoff works end-
// to-end and to give a manual-QA surface (`npm run dev`).

import { useState } from "react";
import {
  ResolutionPicker,
  shouldPrompt,
} from "./components/ResolutionPicker";
import type {
  DeepDetectReport,
  DownloadCandidate,
} from "./types/deepDetect";

type FetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; report: DeepDetectReport };

interface QueuedJob {
  url: string | null;
  click_selector: string | null | undefined;
  resolution: string | undefined;
  codec: string | null | undefined;
  fps: number | null | undefined;
  source_type: string;
}

/** Translate a confirmed candidate into the shape /api/jobs expects.
 *  Exposed so tests can verify the contract independent of the UI. */
export function buildJobPayload(c: DownloadCandidate): QueuedJob {
  return {
    url: c.url,
    click_selector: c.click_selector,
    resolution: c.quality_label || c.resolution?.label,
    codec: c.codec,
    fps: c.fps,
    source_type: String(c.source_type),
  };
}

export interface AppProps {
  /** Override the deep_detect endpoint. Defaults to the real dev API
   *  so `npm run dev` works against a running Flask instance. Tests
   *  pass a stub. */
  detectEndpoint?: string;
  /** Override the job-queue sink. Defaults to a console.log so the
   *  manual-QA surface is self-contained. */
  onQueue?: (job: QueuedJob) => void;
}

export function App({
  detectEndpoint = "/api/dev/deep_detect",
  onQueue = (j) => console.log("queued:", j),
}: AppProps) {
  const [input, setInput] = useState("");
  const [inputMode, setInputMode] = useState<"url" | "html">("url");
  const [state, setState] = useState<FetchState>({ kind: "idle" });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [lastQueued, setLastQueued] = useState<QueuedJob | null>(null);

  async function runDetect() {
    setState({ kind: "loading" });
    setLastQueued(null);
    try {
      const body = inputMode === "url" ? { url: input } : { html: input };
      const res = await fetch(detectEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setState({
          kind: "error",
          message: `HTTP ${res.status}: ${await res.text()}`,
        });
        return;
      }
      const json = await res.json();
      // The endpoint returns either {report: ...} or the report at top
      // level depending on the wrapper. Handle both for resilience.
      const report: DeepDetectReport = json.report ?? json;
      setState({ kind: "ready", report });

      if (shouldPrompt(report)) {
        setPickerOpen(true);
      } else if (report.best_download) {
        // Single-candidate path: queue silently.
        const job = buildJobPayload(report.best_download);
        setLastQueued(job);
        onQueue(job);
      }
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  function handleConfirm(c: DownloadCandidate) {
    const job = buildJobPayload(c);
    setPickerOpen(false);
    setLastQueued(job);
    onQueue(job);
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">BulkDownloader — Deep Detect</h1>
        <p className="text-sm text-zinc-400">
          Paste a URL or raw HTML, run deep_detect, pick a resolution.
        </p>
      </header>

      <div className="mb-3 flex gap-2">
        <label className="flex items-center gap-1 text-sm">
          <input
            type="radio"
            name="mode"
            checked={inputMode === "url"}
            onChange={() => setInputMode("url")}
          />
          URL
        </label>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="radio"
            name="mode"
            checked={inputMode === "html"}
            onChange={() => setInputMode("html")}
          />
          HTML
        </label>
      </div>

      {inputMode === "url" ? (
        <input
          data-testid="detect-url-input"
          className="mb-3 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2"
          placeholder="https://example.com/v/123"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      ) : (
        <textarea
          data-testid="detect-html-input"
          className="mb-3 h-40 w-full rounded border border-zinc-700 bg-zinc-900 px-3 py-2 font-mono text-xs"
          placeholder="<html>..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
      )}

      <button
        data-testid="detect-run"
        className="rounded bg-emerald-600 px-4 py-2 font-medium hover:bg-emerald-500 disabled:opacity-50"
        onClick={runDetect}
        disabled={state.kind === "loading" || input.length === 0}
      >
        {state.kind === "loading" ? "Detecting…" : "Run deep_detect"}
      </button>

      {state.kind === "error" && (
        <div
          data-testid="detect-error"
          role="alert"
          className="mt-4 rounded border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-200"
        >
          {state.message}
        </div>
      )}

      {state.kind === "ready" && (
        <section className="mt-6" data-testid="detect-summary">
          <h2 className="text-lg font-semibold">Result</h2>
          <p className="text-sm text-zinc-400">
            Candidates: {state.report.download_candidates?.length ?? 0}.
            best:{" "}
            {state.report.best_download?.quality_label ??
              state.report.best_download?.resolution?.label ??
              "(none)"}
            .
          </p>
          {!shouldPrompt(state.report) && state.report.best_download && (
            <p className="text-xs text-zinc-500">
              (Single candidate — picker skipped, queued automatically.)
            </p>
          )}
        </section>
      )}

      {lastQueued && (
        <section
          className="mt-4 rounded border border-emerald-900 bg-emerald-950/40 px-3 py-2"
          data-testid="last-queued"
        >
          <div className="text-sm font-semibold text-emerald-300">Queued:</div>
          <pre className="overflow-x-auto text-xs text-emerald-100">
            {JSON.stringify(lastQueued, null, 2)}
          </pre>
        </section>
      )}

      {state.kind === "ready" && (
        <ResolutionPicker
          report={state.report}
          open={pickerOpen}
          onConfirm={handleConfirm}
          onCancel={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
