import { useCallback, useEffect, useState } from "react";

import type { StepKey } from "@/lib/guidedCapture";

// Guided/Expert toggle for the Capture workflow. Per-device (localStorage),
// matching useQueueBadgeMode / useTheme / useCompletionSound — the established
// pattern for UI preferences (no global_config round-trip).
//
// Default policy (per the build plan): remember the last explicit choice, BUT
// default guided-ON for a brand-new site (the host has no reviewed template
// yet). The component passes `defaultOn` once it knows whether the host has a
// template; once the operator toggles, their choice is remembered.

export type CaptureMode = "guided" | "expert";

const MODE_KEY = "bd-capture-mode";

function readStoredMode(): CaptureMode | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(MODE_KEY);
    if (v === "guided" || v === "expert") return v;
  } catch {
    /* localStorage blocked */
  }
  return null;
}

export function useGuidedMode(defaultOn = true): {
  mode: CaptureMode;
  guided: boolean;
  setMode: (m: CaptureMode) => void;
  toggle: () => void;
} {
  const [mode, setModeState] = useState<CaptureMode>(
    () => readStoredMode() ?? (defaultOn ? "guided" : "expert"),
  );

  // Cross-tab sync, same as useQueueBadgeMode.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key !== MODE_KEY) return;
      const v = readStoredMode();
      if (v) setModeState(v);
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const setMode = useCallback((m: CaptureMode) => {
    try {
      window.localStorage.setItem(MODE_KEY, m);
    } catch {
      /* ignore */
    }
    setModeState(m);
  }, []);

  const toggle = useCallback(() => {
    setMode(mode === "guided" ? "expert" : "guided");
  }, [mode, setMode]);

  return { mode, guided: mode === "guided", setMode, toggle };
}

// ─────────────────────────── Resume substrate ───────────────────────────
// "Save draft & exit" + "resume at the furthest validated step". The held-open
// SESSION cannot survive a tab close, so we persist only the recoverable draft
// state (the furthest validated step + the draft fields), keyed by host. On
// re-entry the component rehydrates and prompts the operator to re-open the
// session to continue from that step. Per-device localStorage.

export interface GuidedDraftSnapshot {
  host: string;
  furthestStep: StepKey;
  fields: Record<string, unknown>;
  siteId?: string;
  savedAt: number;
}

const DRAFT_PREFIX = "bd-capture-draft:";

function draftKey(host: string): string {
  return DRAFT_PREFIX + host;
}

export function useGuidedDraft(host: string): {
  saved: GuidedDraftSnapshot | null;
  save: (snap: Omit<GuidedDraftSnapshot, "savedAt">) => void;
  clear: () => void;
} {
  const [saved, setSaved] = useState<GuidedDraftSnapshot | null>(null);

  // Load on host change.
  useEffect(() => {
    if (!host || typeof window === "undefined") {
      setSaved(null);
      return;
    }
    try {
      const raw = window.localStorage.getItem(draftKey(host));
      setSaved(raw ? (JSON.parse(raw) as GuidedDraftSnapshot) : null);
    } catch {
      setSaved(null);
    }
  }, [host]);

  const save = useCallback((snap: Omit<GuidedDraftSnapshot, "savedAt">) => {
    if (!snap.host || typeof window === "undefined") return;
    const full: GuidedDraftSnapshot = { ...snap, savedAt: Date.now() };
    try {
      window.localStorage.setItem(draftKey(snap.host), JSON.stringify(full));
    } catch {
      /* ignore quota/blocked */
    }
    setSaved(full);
  }, []);

  const clear = useCallback(() => {
    if (!host || typeof window === "undefined") return;
    try {
      window.localStorage.removeItem(draftKey(host));
    } catch {
      /* ignore */
    }
    setSaved(null);
  }, [host]);

  return { saved, save, clear };
}
