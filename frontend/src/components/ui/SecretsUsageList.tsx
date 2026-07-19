import type { SecretsUsage } from "@/hooks/useIntegrations";

// Cut 7 (Track A) — read-only secret USAGE list. Fed by GET /api/secrets/usage.
// Shows which stored secret keys exist and which sites reference them, by NAME
// only. It never displays a secret value (the endpoint never returns one).

export interface SecretsUsageListProps {
  data?: SecretsUsage;
  loading?: boolean;
}

export function SecretsUsageList({ data, loading }: SecretsUsageListProps) {
  if (loading && !data) {
    return <p className="text-sm text-ink-3">Loading secret usage…</p>;
  }
  const keys = data?.stored_keys ?? [];
  if (keys.length === 0) {
    return <p className="text-sm text-ink-3">No stored secrets.</p>;
  }
  const usage = data?.usage ?? {};
  const unreferenced = new Set(data?.unreferenced ?? []);
  return (
    <ul aria-label="Secret usage" className="space-y-1">
      {keys.map((key) => {
        const refs = usage[key] ?? [];
        return (
          <li key={key} className="text-sm">
            <span className="font-mono">{key}</span>
            {unreferenced.has(key) ? (
              <span className="ml-2 text-amber">unreferenced</span>
            ) : (
              <span className="ml-2 text-ink-3">
                used by {refs.length} site{refs.length === 1 ? "" : "s"}
                {refs.length ? `: ${refs.join(", ")}` : ""}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
