import { useEffect, useState } from "react";

// Cut 2 — useLastUpdated: turn a react-query `dataUpdatedAt` epoch-ms into a
// short relative label that ticks. Pair with RefreshChip in page headers.
//   0 / falsy  -> null (nothing fetched yet)
//   < 5s       -> "just now"
//   < 60s      -> "Ns ago"
//   < 60m      -> "Nm ago"
//   else       -> "Nh ago"
// Re-renders on a 1s interval so the label stays honest without the caller
// wiring a timer. Cheap: one setInterval per mounted chip.

function format(ms: number, now: number): string | null {
  if (!ms) return null;
  const delta = Math.max(0, now - ms);
  const s = Math.floor(delta / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

export function useLastUpdated(updatedAt: number): string | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!updatedAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [updatedAt]);

  return format(updatedAt, now);
}
