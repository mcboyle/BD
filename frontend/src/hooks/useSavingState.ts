import { useCallback, useEffect, useRef, useState } from "react";

// Cut 2 — useSavingState: a transient "saving… -> saved -> idle" micro-state
// for mutations, so a save control can show progress + a brief confirmation
// without each page hand-rolling timers.
//   start()   -> "saving"
//   succeed() -> "saved", then auto-clears to "idle" after `savedMs` (3s)
//   fail()    -> "idle" (the error surfaces via the mutation's own toast)
// The auto-clear timer is cancelled on unmount and on any new transition.

export type SavingStatus = "idle" | "saving" | "saved";

export interface SavingState {
  status: SavingStatus;
  start: () => void;
  succeed: () => void;
  fail: () => void;
}

export function useSavingState(savedMs = 3000): SavingState {
  const [status, setStatus] = useState<SavingStatus>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const start = useCallback(() => {
    clear();
    setStatus("saving");
  }, [clear]);

  const succeed = useCallback(() => {
    clear();
    setStatus("saved");
    timer.current = setTimeout(() => setStatus("idle"), savedMs);
  }, [clear, savedMs]);

  const fail = useCallback(() => {
    clear();
    setStatus("idle");
  }, [clear]);

  useEffect(() => clear, [clear]);

  return { status, start, succeed, fail };
}
