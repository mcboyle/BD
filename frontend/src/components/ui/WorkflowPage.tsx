import { cn } from "@/lib/utils";

// Cut 1 substrate — WorkflowPage: a layout scaffold with named slots so
// form-heavy pages become config, not bespoke layout. Cut 1 ships the scaffold
// ONLY (no page adopts it yet). Slot order encodes the intended reading flow:
//   purpose -> inputs -> plan/preview -> danger -> result
// Each slot renders only when provided (no empty wrappers), and carries a
// data-slot marker for testability + targeted styling later.

export interface WorkflowPageProps {
  /** Why this page exists / what it does (a Callout or sentence). */
  purpose?: React.ReactNode;
  /** Primary inputs / form controls. */
  inputs?: React.ReactNode;
  /** Plan / preview of what will happen before committing. */
  plan?: React.ReactNode;
  /** Destructive / high-risk controls (group in a DangerZone at the call-site). */
  danger?: React.ReactNode;
  /** Outcome / result of the last action. */
  result?: React.ReactNode;
  className?: string;
}

function Slot({ name, children }: { name: string; children?: React.ReactNode }) {
  if (children == null || children === false) return null;
  return (
    <div data-slot={name} className="space-y-2">
      {children}
    </div>
  );
}

export function WorkflowPage({
  purpose,
  inputs,
  plan,
  danger,
  result,
  className,
}: WorkflowPageProps) {
  return (
    <div className={cn("space-y-5", className)}>
      <Slot name="purpose">{purpose}</Slot>
      <Slot name="inputs">{inputs}</Slot>
      <Slot name="plan">{plan}</Slot>
      <Slot name="danger">{danger}</Slot>
      <Slot name="result">{result}</Slot>
    </div>
  );
}
