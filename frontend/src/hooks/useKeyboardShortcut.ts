import { useEffect } from "react";

// Generic keyboard shortcut hook.
//
//   useKeyboardShortcut("k", { meta: true }, () => setPaletteOpen(true))
//   useKeyboardShortcut("/", {}, () => focusSearch())
//
// Why this hook and not, e.g., react-hotkeys-hook: zero dependency
// surface, and we only need a handful of shortcuts. If U7+ adds many
// more, swap to a library at that point.
//
// Behaviour:
//   - Listens on `window` (not document) so it works regardless of
//     which child element has focus.
//   - Skips when the focus is in an <input>/<textarea>/contenteditable
//     UNLESS the shortcut explicitly opts in via `allowInInput: true`.
//     This is the convention for ⌘K — palette must open from
//     anywhere, including inside the wizard's name field.
//   - meta means Cmd on macOS OR Ctrl on Windows/Linux. The browser
//     reports them as separate flags so we accept either.

export interface ShortcutOpts {
  /** Require Cmd (macOS) or Ctrl (other). */
  meta?: boolean;
  /** Require Shift. */
  shift?: boolean;
  /** Require Alt/Option. */
  alt?: boolean;
  /** Fire even when focus is in a text input. Default false. */
  allowInInput?: boolean;
}

function isTextInput(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (t.isContentEditable) return true;
  return false;
}

export function useKeyboardShortcut(
  key: string,
  opts: ShortcutOpts,
  handler: (e: KeyboardEvent) => void,
): void {
  useEffect(() => {
    const want_meta = !!opts.meta;
    const want_shift = !!opts.shift;
    const want_alt = !!opts.alt;
    const wantedKey = key.toLowerCase();
    const allowInInput = !!opts.allowInInput;
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== wantedKey) return;
      const hasMeta = e.metaKey || e.ctrlKey;
      if (want_meta !== hasMeta) return;
      if (want_shift !== e.shiftKey) return;
      if (want_alt !== e.altKey) return;
      if (!allowInInput && isTextInput(e.target)) return;
      handler(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, opts.meta, opts.shift, opts.alt, opts.allowInInput, handler]);
}
