// Formatting helpers shared across the SPA. Keep these pure (no
// side effects, no React hooks) so they're testable in isolation.

export function formatBytes(bytes: number, decimals = 1): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(decimals)} ${units[i]}`;
}

export function formatRate(bytesPerSec: number): string {
  if (!Number.isFinite(bytesPerSec) || bytesPerSec <= 0) return "—";
  return `${formatBytes(bytesPerSec)}/s`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return "—";
  }
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function formatCount(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function formatDelta(abs: number | null, pct: number | null): string {
  if (abs === null || abs === undefined) return "";
  const sign = abs > 0 ? "↑" : abs < 0 ? "↓" : "·";
  if (pct === null || pct === undefined) return `${sign} ${Math.abs(abs)}`;
  return `${sign} ${Math.abs(pct)}% vs previous`;
}

// Percentage of an already-computed 0..100 value. Em-dash for missing/non-finite
// so callers can drop their `x != null ? `${x.toFixed(n)}%` : "—"` ternaries.
export function formatPercent(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(decimals)}%`;
}

// One home for the bug-prone "epoch may be seconds or millis" normalize that was
// copy-pasted across JobErrorModal / LogDiff / ApiTokensPanel / Secrets /
// Maintenance. `time` style → local zero-padded HH:MM:SS; `datetime` (default) →
// locale string.
export function formatTimestamp(
  epoch: number | null | undefined,
  opts: { style?: "datetime" | "time" } = {},
): string {
  if (epoch === null || epoch === undefined || !epoch || !Number.isFinite(epoch)) return "—";
  const ms = epoch > 1e12 ? epoch : epoch * 1000;
  const d = new Date(ms);
  if (opts.style === "time") {
    const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }
  return d.toLocaleString();
}
