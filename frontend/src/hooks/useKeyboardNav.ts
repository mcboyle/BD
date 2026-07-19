import { useEffect, useRef } from "react";

// Cut 2 — useKeyboardNav: the global power-user keyboard layer, mounted once in
// AppShell. Builds on the same focus-safety convention as useKeyboardShortcut.
//
//   g then <letter>  route jump (g-prefix primes a short window):
//                       h Home · s Sites · q Queue · a Activity · t seTtings ·
//                       d Dashboard · r needs-Review · l Library
//   /                focus the page filter      -> onFilter()
//   ?                open the shortcuts sheet    -> onShowHelp()
//   j / k            OPT-IN row nav              -> onRowNav("next"|"prev")
//                       (only active when onRowNav is provided by a route)
//
// Safety invariants:
//   - INERT while focus is in an <input>/<textarea>/<select>/contenteditable.
//   - INERT while `dialogOpen` is true (a modal/sheet owns the keyboard).
//   - The g-prefix window is 1500ms; any non-mapped key cancels it.
//   - Pure navigation/UX — never gates, confirms, or mutates.

export const NAV_JUMPS: Record<string, string> = {
  h: "/",
  s: "/sites",
  q: "/queue",
  a: "/activity",
  t: "/settings",
  d: "/dashboard",
  r: "/needs-review",
  l: "/library",
};

// Human-readable rows for the ShortcutsSheet (single source of truth).
export const NAV_JUMP_LABELS: Array<{ keys: string; label: string }> = [
  { keys: "g h", label: "Home" },
  { keys: "g s", label: "Sites" },
  { keys: "g q", label: "Queue" },
  { keys: "g a", label: "Activity" },
  { keys: "g t", label: "Settings" },
  { keys: "g d", label: "Dashboard" },
  { keys: "g r", label: "Needs review" },
  { keys: "g l", label: "Library" },
];

function isTextInput(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (t.isContentEditable) return true;
  return false;
}

export interface KeyboardNavOptions {
  navigate: (to: string) => void;
  onFilter?: () => void;
  onShowHelp?: () => void;
  /** OPT-IN per-route row nav (j/k). Absent -> j/k do nothing. */
  onRowNav?: (dir: "next" | "prev") => void;
  /** Suppress the whole layer while a modal/sheet is open. */
  dialogOpen?: boolean;
}

const G_WINDOW_MS = 1500;

export function useKeyboardNav({
  navigate,
  onFilter,
  onShowHelp,
  onRowNav,
  dialogOpen = false,
}: KeyboardNavOptions): void {
  // Keep the latest callbacks/flags without re-binding the listener each render.
  const ref = useRef({ navigate, onFilter, onShowHelp, onRowNav, dialogOpen });
  ref.current = { navigate, onFilter, onShowHelp, onRowNav, dialogOpen };

  // g-prefix primed-at timestamp (0 = not primed).
  const gAt = useRef(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const { navigate, onFilter, onShowHelp, onRowNav, dialogOpen } =
        ref.current;
      if (dialogOpen) return;
      if (isTextInput(e.target)) return;
      // Never hijack a browser/OS chord.
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const key = e.key;
      const now = Date.now();
      const primed = gAt.current && now - gAt.current <= G_WINDOW_MS;

      if (primed) {
        gAt.current = 0;
        const dest = NAV_JUMPS[key.toLowerCase()];
        if (dest) {
          e.preventDefault();
          navigate(dest);
        }
        // non-mapped second key: sequence already cancelled above.
        return;
      }

      if (key === "g") {
        gAt.current = now;
        return;
      }
      if (key === "/") {
        if (onFilter) {
          e.preventDefault();
          onFilter();
        }
        return;
      }
      if (key === "?") {
        if (onShowHelp) {
          e.preventDefault();
          onShowHelp();
        }
        return;
      }
      if (onRowNav && (key === "j" || key === "k")) {
        e.preventDefault();
        onRowNav(key === "j" ? "next" : "prev");
        return;
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
