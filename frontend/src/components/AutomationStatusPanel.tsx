// v3.66.723 (AF5) — the automation safety nets, ON SCREEN.
//
// Two guardrails shipped and reported to nobody:
//   706 restore rehearsal — verdict persisted, reader existed, NOTHING called it.
//   708 pipeline halt     — verdict returned to a scheduler wrapper that DISCARDED it.
//
//   GET /api/automation/status — both verdicts. Read-only; a readout is not a lever.
//
// UNKNOWN IS A THIRD STATE AND IT IS NOT GREEN. "Never run" and "ran and passed" are
// different answers, and this panel must never let the first masquerade as the second —
// that inference is exactly the bug this whole cut exists to kill.
import { useQuery } from "@tanstack/react-query";

import { Card } from "@/components/ui/card";
import { apiGet } from "@/lib/api-client";

type NetState = "unknown" | "ok" | "failed" | "halted";

interface RehearsalStatus {
  state: NetState;
  ok: boolean;
  detail: string;
  checked_at: number | null;
  age_days: number | null;
  path?: string;
  error: string;
}
interface PipelineStatus {
  state: NetState;
  ok: boolean;
  detail: string;
  ran_at: number | null;
  reason?: string;
  hosts: number;
  halted: string[];
}
interface AutomationStatus {
  ok: boolean;
  rehearsal: RehearsalStatus;
  pipeline: PipelineStatus;
}

function fmtWhen(ts: number | null): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

const TONE: Record<NetState, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  failed: "text-red-600 dark:text-red-400",
  halted: "text-red-600 dark:text-red-400",
  // Deliberately amber, NOT grey and NOT green: "I have no idea" is a finding.
  unknown: "text-amber-600 dark:text-amber-400",
};

const LABEL: Record<NetState, string> = {
  ok: "OK",
  failed: "FAILED",
  halted: "HALTED",
  unknown: "UNKNOWN",
};

function Net({ title, state, detail, when, extra }: {
  title: string;
  state: NetState;
  detail: string;
  when: string;
  extra?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <div className="min-w-0">
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-muted-foreground">{detail}</div>
        {extra ? <div className="text-xs text-red-600 dark:text-red-400 break-words">{extra}</div> : null}
        <div className="text-xs text-muted-foreground">Last checked: {when}</div>
      </div>
      <div className={`shrink-0 text-sm font-semibold tabular-nums ${TONE[state]}`}>
        {LABEL[state]}
      </div>
    </div>
  );
}

export default function AutomationStatusPanel() {
  const { data, isLoading, isError } = useQuery<AutomationStatus>({
    queryKey: ["automation-status"],
    queryFn: () => apiGet<AutomationStatus>("/api/automation/status"),
    refetchInterval: 60_000,
  });

  if (isLoading) return <Card className="p-3 text-xs text-muted-foreground">Loading safety-net status…</Card>;

  // A readout that cannot reach its source must SAY SO. Rendering nothing here would
  // recreate the exact invisibility this panel was built to remove.
  if (isError || !data) {
    return (
      <Card className="p-3 text-xs text-amber-600 dark:text-amber-400">
        Safety-net status UNAVAILABLE — could not reach /api/automation/status. The nets
        may or may not have fired; this panel cannot tell you. That is not an all-clear.
      </Card>
    );
  }

  const { rehearsal: r, pipeline: p } = data;

  return (
    <Card className="p-3">
      <div className="mb-1 flex items-center justify-between">
        <div className="text-sm font-semibold">Safety-net status</div>
        <div className={`text-xs font-semibold ${data.ok ? TONE.ok : TONE.unknown}`}>
          {data.ok ? "both nets green" : "not all-clear"}
        </div>
      </div>
      <div className="divide-y">
        <Net
          title="Restore rehearsal (706)"
          state={r.state}
          detail={r.detail}
          when={fmtWhen(r.checked_at)}
          extra={r.error || undefined}
        />
        <Net
          title="Autonomous pipeline (708)"
          state={p.state}
          detail={p.detail}
          when={fmtWhen(p.ran_at)}
          extra={p.halted.length ? `Halted on: ${p.halted.join(", ")}` : undefined}
        />
      </div>
      {!data.ok ? (
        <div className="mt-2 text-xs text-muted-foreground">
          UNKNOWN means the net has never reported — not that it passed. Turn the
          rehearsal on below and let one pass complete before trusting either.
        </div>
      ) : null}
    </Card>
  );
}
