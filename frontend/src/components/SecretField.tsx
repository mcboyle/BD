// Write-only secret input. Encodes the same discipline the VPN ConfigEditor
// applies to secret-named config keys, but for the discrete password fields the
// Secrets vault-lifecycle page needs (unlock password, old/new master password):
//
//   * always type="password" — the value is never shown in plaintext;
//   * autoComplete="new-password" — browsers must not pre-fill or remember it;
//   * the value is owned by local component state and is the caller's to clear
//     after submit. The server NEVER returns a stored secret value, so nothing
//     redacted is ever echoed back into the field (no "***" round-trip).
//
// This is deliberately NOT the key/value ConfigEditor: the lifecycle page edits
// discrete named passwords, not arbitrary backend-config rows.
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface SecretFieldProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  ariaLabel: string;
  autoFocus?: boolean;
  /** Per-site width/spacing (e.g. "w-full", "w-64", "max-w-xs"). Merged with
   *  the base text-sm so the sweep preserves each call site's layout. */
  className?: string;
  /** Enter-to-submit and similar keyboard handlers from the call site. */
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

export function SecretField({
  value,
  onChange,
  placeholder = "secret (write-only)",
  disabled,
  ariaLabel,
  autoFocus,
  className,
  onKeyDown,
}: SecretFieldProps) {
  return (
    <Input
      type="password"
      autoComplete="new-password"
      spellCheck={false}
      autoFocus={autoFocus}
      className={cn("text-sm", className)}
      placeholder={placeholder}
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
    />
  );
}
