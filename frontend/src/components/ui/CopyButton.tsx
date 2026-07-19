import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

// Cut 5 — CopyButton: copies a path / env value to the clipboard. REFUSES
// secret-bearing fields (paths/env only, never secrets) — when `secret`, the
// control is disabled and the clipboard is never touched. Shows a transient
// check on success.

export interface CopyButtonProps {
  /** The value to copy. Never rendered; only written to the clipboard. */
  value: string;
  /** Secret field — disables copy entirely (never echoes/copies the value). */
  secret?: boolean;
  /** Accessible label (defaults to "Copy"). */
  label?: string;
  className?: string;
}

export function CopyButton({ value, secret = false, label = "Copy", className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const onClick = () => {
    if (secret) return; // hard refusal — never copy a secret
    void navigator.clipboard?.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={secret}
      aria-label={secret ? "Copy disabled for secret field" : label}
      title={secret ? "Secrets can't be copied" : label}
      className={cn(
        "inline-flex items-center justify-center rounded p-1 text-ink-3",
        "hover:text-ink-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-ink-3",
        className,
      )}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-green" aria-hidden />
      ) : (
        <Copy className="h-3.5 w-3.5" aria-hidden />
      )}
    </button>
  );
}
