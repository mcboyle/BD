import { useEffect, useRef } from "react";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";
import type { QueueV2 } from "@/lib/api-types";
import { useSyncedSoundPref } from "@/hooks/useSyncedSoundPref";

// Fire a short Web Audio ping when the queue transitions from
// "anything running" to "nothing running", IF the operator has
// opted in via the Settings toggle (Tier 1 #7 + Tier 4 #28).
//
// Design choices:
//   - Web Audio API, no sound file. A 0.18s sine pulse at 880 Hz with
//     a quick attack/decay envelope. Generated inline so we don't ship
//     a binary asset that would balloon the bundle.
//   - Trigger: edge-detected on the running count, not a level. If the
//     SPA mounts mid-queue and the count drops, that still triggers
//     once — exactly the desired behaviour ("I left the room and came
//     back to find it done").
//   - Pref source: v3.64.3 — useSyncedSoundPref. Default-off sync
//     means by default this still reads localStorage["bd-sound-on-complete"]
//     exactly as v3.64.2 did. If sync is opted in, the same pref
//     value comes from /api/global_config.sound_on_complete instead.
//   - Browser autoplay policies: AudioContext can only fire after a
//     user gesture. We lazy-create the context on first trigger; if
//     the user has clicked anywhere in the page since load, it works.
//     If not, the call silently no-ops, which is fine — we don't bug
//     the user with permission prompts for an ambient feature.
//
// Mounted from AppShell so it runs on every tab. Uses the same query
// key as the queue badge so we don't open a second pipe.

// Pref enabled-state now lives in useSyncedSoundPref (v3.64.3) — see
// the comment block above.

let _audioCtx: AudioContext | null = null;

function playPing(): void {
  if (typeof window === "undefined") return;
  try {
    // Reuse a single AudioContext across calls — browsers cap the
    // number of concurrent contexts at ~6, and creating new ones
    // leaks resources.
    if (!_audioCtx) {
      const AC = window.AudioContext || (window as unknown as {
        webkitAudioContext: typeof AudioContext;
      }).webkitAudioContext;
      if (!AC) return;
      _audioCtx = new AC();
    }
    // If the context was suspended (autoplay-block, tab-switch), try
    // to resume it. The promise rejection is benign — we just don't
    // play the tone.
    if (_audioCtx.state === "suspended") {
      _audioCtx.resume().catch(() => {});
    }
    const ctx = _audioCtx;
    const now = ctx.currentTime;
    // Two-tone ping: 660 Hz → 880 Hz, brief glide. Pleasanter than
    // a flat sine, less attention-grabbing than a square wave.
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(660, now);
    osc.frequency.exponentialRampToValueAtTime(880, now + 0.12);
    // Envelope: 8ms attack, 170ms decay. Quiet (0.15 peak) so it
    // doesn't startle.
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.15, now + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.2);
  } catch {
    // Any failure is silent — the feature is ambient.
  }
}

export function useCompletionSound(): void {
  // v3.64.3: read enabled state via the synced-pref hook. In the
  // default sync-off case this is just a localStorage read with a
  // cross-tab listener; in sync-on case it reads the server value
  // from the shared global-config query.
  const { enabled } = useSyncedSoundPref();

  // Read the queue badge — same key as AppShell so we reuse the
  // cached data rather than opening another query.
  const { data } = useQuery<QueueV2>({
    queryKey: ["queue-v2-badge"],
    queryFn: ({ signal }) => apiGet<QueueV2>("/api/queue/v2", signal),
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  // Edge-detect: ref holds the previous running count. When the new
  // count is 0 and the previous was > 0, play the tone (if enabled).
  // Ref instead of state so we don't trigger a render on every poll.
  const prevRunning = useRef<number | null>(null);

  useEffect(() => {
    const running = data?.running?.length ?? 0;
    const prev = prevRunning.current;
    prevRunning.current = running;
    if (prev === null) return; // first sample — no edge yet
    if (prev > 0 && running === 0 && enabled) {
      playPing();
    }
  }, [data?.running?.length, enabled]);
}
