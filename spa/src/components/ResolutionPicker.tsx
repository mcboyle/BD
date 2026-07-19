// ResolutionPicker — modal that opens when deep_detect returns 2+
// resolution_download_card candidates. Shows per-candidate
// resolution / codec / fps / size; defaults selection to
// report.best_download and lets the user override before the parent
// queues the download.
//
// Backlog #1 from v3_66_5_handoff.txt. Pure React + tailwind classes,
// no @radix-ui dependency — spa/ doesn't ship one.

import { useEffect, useMemo, useState } from "react";
import type {
  DeepDetectReport,
  DownloadCandidate,
} from "../types/deepDetect";

export interface ResolutionPickerProps {
  /** Full deep_detect report. The picker reads `download_candidates`
   *  and `best_download`. */
  report: DeepDetectReport;
  /** Whether the modal is shown. Parent owns this state. */
  open: boolean;
  /** Called when the user confirms a selection. */
  onConfirm: (selected: DownloadCandidate) => void;
  /** Called when the user dismisses without choosing. */
  onCancel: () => void;
  /** Optional accessible title override. */
  title?: string;
}

/** Format bytes as a human-friendly string. Returns "—" for null/0. */
export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  // 1 decimal for MB and up, none for B/KB
  return i >= 2 ? `${n.toFixed(1)} ${units[i]}` : `${Math.round(n)} ${units[i]}`;
}

/** Build a stable identity key for a candidate (URL preferred, falling
 *  back to a composite). */
export function candidateKey(c: DownloadCandidate, idx: number): string {
  if (c.url) return `url:${c.url}`;
  if (c.click_selector)
    return `click:${c.click_selector}:${c.quality_label || c.resolution?.label || idx}`;
  return `idx:${idx}:${c.quality_label || c.resolution?.label || ""}`;
}

/** True if the report has enough resolution_download_card candidates
 *  to bother showing a picker (>= 2). The auto-pick flow should NOT
 *  open the modal otherwise — just use best_download silently. */
export function shouldPrompt(report: DeepDetectReport): boolean {
  const cards = (report.download_candidates || []).filter(
    (c) => c.source_type === "resolution_download_card",
  );
  return cards.length >= 2;
}

export function ResolutionPicker({
  report,
  open,
  onConfirm,
  onCancel,
  title = "Choose a download resolution",
}: ResolutionPickerProps) {
  // Cards to show: prefer resolution_download_card source type so the
  // picker is meaningful. If a report has only manifest-derived
  // candidates (hls_variant / dash_representation), surface those too
  // — same shape, same scoring. Empty list short-circuits the render.
  const cards = useMemo(
    () =>
      (report.download_candidates || []).filter((c) =>
        [
          "resolution_download_card",
          "hls_variant",
          "dash_representation",
        ].includes(c.source_type as string),
      ),
    [report],
  );

  // Default to best_download if it's in `cards`; otherwise the first
  // card. We match by content (URL when present, else click_selector +
  // quality), not by candidateKey — best_download is passed in
  // separately from the cards array and doesn't carry an index.
  const defaultKey = useMemo(() => {
    const bd = report.best_download;
    if (bd) {
      const matchIdx = cards.findIndex((c) => {
        if (bd.url && c.url) return bd.url === c.url;
        if (bd.url || c.url) return false;
        return (
          (bd.click_selector || "") === (c.click_selector || "") &&
          (bd.quality_label || bd.resolution?.label || "") ===
            (c.quality_label || c.resolution?.label || "")
        );
      });
      if (matchIdx >= 0) return candidateKey(cards[matchIdx], matchIdx);
    }
    return cards.length ? candidateKey(cards[0], 0) : "";
  }, [report.best_download, cards]);

  const [selectedKey, setSelectedKey] = useState<string>(defaultKey);

  // Re-sync if the parent passes a new report while open.
  useEffect(() => {
    setSelectedKey(defaultKey);
  }, [defaultKey]);

  // ESC dismisses
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  // Disclaimer banner from probes (v3.66.5 HEAD-probe relaxation).
  const disclaimer = report.probes?.disclaimer || null;

  const handleConfirm = () => {
    const sel = cards.find((c, i) => candidateKey(c, i) === selectedKey);
    if (sel) onConfirm(sel);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="resolution-picker"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => {
        // backdrop click cancels; clicks inside the panel stopPropagate below
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        className="w-full max-w-2xl rounded-lg bg-zinc-900 text-zinc-100 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b border-zinc-800 px-5 py-3">
          <h2 className="text-lg font-semibold">{title}</h2>
        </header>

        {disclaimer && (
          <div
            role="alert"
            data-testid="resolution-picker-disclaimer"
            className="border-b border-amber-900/50 bg-amber-950/40 px-5 py-2 text-sm text-amber-200"
          >
            {disclaimer}
          </div>
        )}

        <div className="max-h-[60vh] overflow-y-auto p-2">
          {cards.length === 0 ? (
            <p
              data-testid="resolution-picker-empty"
              className="px-4 py-6 text-center text-sm text-zinc-400"
            >
              No download candidates were detected.
            </p>
          ) : (
            <ul className="divide-y divide-zinc-800">
              {cards.map((c, i) => {
                const key = candidateKey(c, i);
                const selected = selectedKey === key;
                const label =
                  c.quality_label ||
                  c.resolution?.label ||
                  (c.height ? `${c.height}p` : "unknown");
                return (
                  <li
                    key={key}
                    data-testid={`resolution-picker-row-${i}`}
                    aria-selected={selected}
                  >
                    <label
                      className={`flex cursor-pointer items-center gap-3 px-4 py-2 hover:bg-zinc-800/50 ${
                        selected ? "bg-zinc-800/80" : ""
                      }`}
                    >
                      <input
                        type="radio"
                        name="resolution-pick"
                        className="h-4 w-4"
                        checked={selected}
                        onChange={() => setSelectedKey(key)}
                      />
                      <span className="w-16 font-mono text-sm font-semibold">
                        {label}
                      </span>
                      <span className="w-20 text-xs text-zinc-400">
                        {c.codec || "—"}
                      </span>
                      <span className="w-12 text-xs text-zinc-400">
                        {c.fps ? `${c.fps}fps` : "—"}
                      </span>
                      <span className="w-20 text-right text-xs text-zinc-400">
                        {formatBytes(c.size_bytes)}
                      </span>
                      {c.requires_click && (
                        <span
                          title="No direct URL; click required"
                          className="ml-auto rounded bg-zinc-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-300"
                        >
                          click
                        </span>
                      )}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <footer className="flex justify-end gap-2 border-t border-zinc-800 px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            data-testid="resolution-picker-cancel"
            className="rounded border border-zinc-700 px-3 py-1 text-sm hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!selectedKey || cards.length === 0}
            data-testid="resolution-picker-confirm"
            className="rounded bg-blue-600 px-3 py-1 text-sm font-medium hover:bg-blue-500 disabled:opacity-50"
          >
            Download
          </button>
        </footer>
      </div>
    </div>
  );
}

export default ResolutionPicker;
