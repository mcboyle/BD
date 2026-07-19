import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

// Cut 1 substrate — ConfirmDialog: the shared destructive confirm, built to the
// v3.66.209 confirm-tier operator decision (2026-06-12):
//   Tier A (destructive/irreversible/security): a yes/no dialog where **No is
//   the default** (Cancel autofocused) and Confirm is styled destructive. The
//   token-entry requirement is retired across the SPA, so this shell offers no
//   such field.
// This generalizes the existing throttle/Class-B + payload bulk-delete confirms.
// Confirmation/gating still live wherever the underlying action enforces them —
// this is the UI shell, not a guard, and never makes a destructive action
// easier to fire.

export interface ConfirmDialogProps {
  open: boolean;
  /** What the action targets (e.g. "all stored secrets"). */
  target: React.ReactNode;
  /** What will happen (e.g. "This permanently deletes them."). */
  consequence: React.ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
  title?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
}

export function ConfirmDialog({
  open,
  target,
  consequence,
  onConfirm,
  onCancel,
  title = "Confirm",
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            <span className="font-medium text-ink-1">{target}</span>
            {" — "}
            {consequence}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          {/* Tier A: No is the default — Cancel is autofocused and styled as the
              primary action so initial focus never lands on the destructive
              button; Confirm is styled destructive. */}
          <Button autoFocus variant="default" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
