import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { NAV_JUMP_LABELS } from "@/hooks/useKeyboardNav";

// Cut 2 — ShortcutsSheet: the `?` cheat-sheet. Documents the global keyboard
// layer (g-jumps from useKeyboardNav's single source of truth, `/` filter, `?`
// help). Reuses the dialog primitive; Esc / overlay / close button dismiss via
// onOpenChange(false). Presentational only.

const EXTRA_KEYS: Array<{ keys: string; label: string }> = [
  { keys: "/", label: "Focus the filter on this page" },
  { keys: "?", label: "Show this shortcuts sheet" },
  { keys: "⌘K", label: "Open the command palette" },
  { keys: "j / k", label: "Move between rows (where supported)" },
];

function Row({ keys, label }: { keys: string; label: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1">
      <span className="text-sm text-ink-2">{label}</span>
      <kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-xs font-medium text-ink-1">
        {keys}
      </kbd>
    </div>
  );
}

export interface ShortcutsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShortcutsSheet({ open, onOpenChange }: ShortcutsSheetProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Press <kbd className="rounded border border-line px-1">g</kbd> then a
            letter to jump. Shortcuts are inert while you're typing.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">
              Go to
            </div>
            {NAV_JUMP_LABELS.map((r) => (
              <Row key={r.keys} keys={r.keys} label={r.label} />
            ))}
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">
              Actions
            </div>
            {EXTRA_KEYS.map((r) => (
              <Row key={r.keys} keys={r.keys} label={r.label} />
            ))}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
