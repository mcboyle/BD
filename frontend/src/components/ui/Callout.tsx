import { Info, AlertTriangle, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";

// Shared callout (Slice 5). One Info / Caution / Danger treatment used to group
// and frame advisory or high-risk regions — the convergence target for the
// SPA's banners and (visually) the cockpit Posture banner. Presentational: it
// frames content, it does not change any behavior.
//
//   info     neutral surface + hairline — context / "heads up"
//   caution  amber — reversible-but-notable
//   danger   red — destructive / access-control surface (role=alert)

export type CalloutTone = "info" | "caution" | "danger";

const TONE: Record<
  CalloutTone,
  { wrap: string; icon: string; title: string; Icon: typeof Info; role: "note" | "alert" }
> = {
  info: {
    wrap: "bg-surface-2 border-hairline",
    icon: "text-ink-3",
    title: "text-ink",
    Icon: Info,
    role: "note",
  },
  caution: {
    wrap: "bg-amber-soft border-amber/30",
    icon: "text-amber-dim",
    title: "text-amber-dim",
    Icon: AlertTriangle,
    role: "note",
  },
  danger: {
    wrap: "bg-red-soft border-red/30",
    icon: "text-red",
    title: "text-red",
    Icon: ShieldAlert,
    role: "alert",
  },
};

export interface CalloutProps {
  tone: CalloutTone;
  title: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export function Callout({ tone, title, children, className }: CalloutProps) {
  const t = TONE[tone];
  const Icon = t.Icon;
  return (
    <div
      role={t.role}
      className={cn("rounded-lg border p-3", t.wrap, className)}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn("h-4 w-4 shrink-0", t.icon)} aria-hidden />
        <span className={cn("text-sm font-semibold", t.title)}>{title}</span>
      </div>
      {children && (
        <div className="mt-1.5 text-xs text-ink-3">{children}</div>
      )}
    </div>
  );
}
