import type { ThemeMode } from "@/lib/themes";

import { ThemeMenu } from "./ThemeMenu";

// Cut A (Cut 1 adoption remainder) — ThemeToggle is the thin re-export of the
// ThemeMenu `inline` variant: ONE theme control reused in Settings AND the
// sidebar footer, no copied second picker.
//
// The three quick modes the toggle offers — "system" / "light" / "dark" — are
// declared here as the canonical list and CONSUMED by ThemeMenu, so this file
// stays the source of truth for "which modes the toggle exposes" even though
// the rendering lives in ThemeMenu. System is the default so users who never
// open Settings still get the right palette from the OS preference.
export const THEME_QUICK_MODES: ReadonlyArray<{
  value: ThemeMode;
  label: string;
}> = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function ThemeToggle() {
  return <ThemeMenu variant="inline" quickModes={THEME_QUICK_MODES} />;
}

export default ThemeToggle;
