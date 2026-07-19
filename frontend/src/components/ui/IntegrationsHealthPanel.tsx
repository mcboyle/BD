import type { IntegrationsHealth } from "@/hooks/useIntegrations";

// Cut 7 (Track A) — read-only integration health panel. Fed by
// GET /api/integrations/health (fail-open, sanitized). Renders one row per
// integration with a status dot derived from `ok` / `configured`. It shows
// only booleans/counts — never an endpoint, token, or key.

function dotClass(ok: boolean): string {
  return ok ? "bg-green" : "bg-ink-3";
}

export interface IntegrationsHealthPanelProps {
  data?: IntegrationsHealth;
  loading?: boolean;
}

export function IntegrationsHealthPanel({ data, loading }: IntegrationsHealthPanelProps) {
  if (loading && !data) {
    return <p className="text-sm text-ink-3">Checking integration health…</p>;
  }
  const integrations = data?.integrations ?? {};
  const names = Object.keys(integrations);
  if (names.length === 0) {
    return <p className="text-sm text-ink-3">No integrations reported.</p>;
  }
  return (
    <ul aria-label="Integration health" className="space-y-1">
      {names.map((name) => {
        const info = integrations[name] || {};
        const ok = Boolean(info.ok ?? info.configured ?? false);
        return (
          <li key={name} className="flex items-center gap-2 text-sm">
            <span className={`inline-block h-2 w-2 rounded-full ${dotClass(ok)}`} />
            <span className="font-medium">{name}</span>
            <span className="text-ink-3">
              {info.ok !== undefined
                ? ok
                  ? "healthy"
                  : "unhealthy"
                : info.configured
                  ? "configured"
                  : "not configured"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
