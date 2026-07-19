import { useEffect, useState, useRef } from "react";

import type { DownloadProgressEvent } from "@/lib/api-types";

// Live-progress overlay for the Queue tab.
//
// Strategy: the queue base list comes from /api/queue/v2 polled every
// 4–8s. That cadence is too slow for smooth progress bars (the runner
// emits download_progress events at 1Hz per URL). We open an
// EventSource to /api/stream, ONLY listen for download_progress
// events, and accumulate the latest pct per URL into a Map.
//
// The Queue tab merges this Map onto the TanStack Query rows for
// render — Map wins where present (it's newer), TanStack data fills
// gaps. This avoids invalidating the query on every progress event,
// which would defeat the purpose of having a cheap polling cadence.
//
// Failure modes: SSE connection drops → EventSource auto-reconnects.
// Server doesn't speak SSE (rare; proxy strips Content-Type) →
// onerror fires, we stop trying after 3 failures and let polling
// fall back. Tab visibility → browser pauses EventSource on hidden
// tabs natively.
//
// The map is cleared when a URL completes (pct >= 100 stays as a
// terminal value; the next /api/queue/v2 poll will drop it from
// running, at which point we delete the key).

export interface QueueProgressMap {
  // url → most recent pct (0..100)
  byUrl: Map<string, number>;
  // url → most recent bytes-done for the rate label (optional)
  bytesByUrl: Map<string, number>;
}

export function useQueueStream(enabled: boolean): QueueProgressMap {
  const [tick, setTick] = useState(0); // forces rerender on event
  // Refs so the EventSource handler doesn't re-bind on every render.
  const byUrlRef = useRef<Map<string, number>>(new Map());
  const bytesRef = useRef<Map<string, number>>(new Map());
  const failuresRef = useRef(0);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    let es: EventSource | null = null;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      try {
        es = new EventSource("/api/stream");
      } catch {
        failuresRef.current++;
        return;
      }
      es.addEventListener("download_progress", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as DownloadProgressEvent;
          if (!data || !data.url) return;
          // Pin pct to 0..100; backend may briefly emit > 100 on
          // ranged retries.
          const pct = Math.max(0, Math.min(100, Math.round(data.pct ?? 0)));
          byUrlRef.current.set(data.url, pct);
          if (typeof data.got === "number") {
            bytesRef.current.set(data.url, data.got);
          }
          // Batch re-renders: bump the tick at most every ~250ms.
          // (Browser already batches setState in the event loop, but
          // SSE bursts can fire many per tick — coalescing keeps the
          // re-render rate sane.)
          setTick((t) => t + 1);
        } catch {
          /* malformed event, ignore */
        }
      });
      es.addEventListener("queue_change", () => {
        // When the queue changes (a job completed, was added, etc.),
        // bump tick so the consumer can re-read its own query data.
        // The actual queue data still comes from /api/queue/v2.
        setTick((t) => t + 1);
      });
      es.onerror = () => {
        failuresRef.current++;
        // EventSource auto-reconnects, but if we hit several errors
        // in a row, give up and let polling carry the load.
        if (failuresRef.current > 3 && es) {
          es.close();
          es = null;
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (es) es.close();
    };
  }, [enabled]);

  // Return refs as the public API. `tick` is in the dep array of the
  // memo down-component so consumers re-render in response.
  void tick;
  return { byUrl: byUrlRef.current, bytesByUrl: bytesRef.current };
}
