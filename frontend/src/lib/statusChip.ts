// P6-2 (feedback) — format the DesktopShell footer status chip. Grows the old
// "Idle · 32m" into "Running · 3 queued · 2/4 workers · 32m", with honest
// omissions: a field is shown only when its data is present, and the worker
// denominator is never fabricated when workers_total is null (empty fleet —
// frozen by tests/test_u49_workers_total_honest.py).

export interface StatusChipInput {
  activeDownloads?: number;
  queueDepth?: number | null;
  workersActive?: number | null;
  workersTotal?: number | null;
  uptime?: string;
}

export function statusChipText({
  activeDownloads = 0,
  queueDepth,
  workersActive,
  workersTotal,
  uptime,
}: StatusChipInput): string {
  const parts: string[] = [activeDownloads > 0 ? "Running" : "Idle"];

  if (typeof queueDepth === "number") {
    parts.push(`${queueDepth} queued`);
  }

  if (typeof workersTotal === "number" && workersTotal > 0) {
    parts.push(`${workersActive ?? 0}/${workersTotal} workers`);
  } else if (typeof workersActive === "number" && workersActive > 0) {
    // total unknown/null (empty fleet) — show the active count alone, never "/—"
    parts.push(`${workersActive} active`);
  }

  if (uptime) parts.push(uptime);

  return parts.join(" · ");
}
