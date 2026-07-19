import { Pause, Play, Zap } from "lucide-react";

// Cut 6.6 — inline row quick-actions (revealed on hover) for queue rows.
// Presentational: the handlers call existing mutation endpoints at the call
// site. Icon-only buttons carry aria-labels for a11y. When the row is paused we
// show Resume in place of Pause.

export function RowQuickActions({
  onPause,
  onResume,
  onCaptureNow,
  paused,
}: {
  onPause: () => void;
  onResume: () => void;
  onCaptureNow: () => void;
  paused: boolean;
}) {
  const btn =
    "rounded-sm p-1 text-ink-3 hover:bg-surface-2 hover:text-ink focus-visible:outline focus-visible:outline-2";

  return (
    <div className="inline-flex items-center gap-0.5">
      {paused ? (
        <button type="button" aria-label="Resume" onClick={onResume} className={btn}>
          <Play aria-hidden className="h-4 w-4" />
        </button>
      ) : (
        <button type="button" aria-label="Pause" onClick={onPause} className={btn}>
          <Pause aria-hidden className="h-4 w-4" />
        </button>
      )}
      <button type="button" aria-label="Capture now" onClick={onCaptureNow} className={btn}>
        <Zap aria-hidden className="h-4 w-4" />
      </button>
    </div>
  );
}
