import type { AiBootReadiness } from "@/hooks/useIntegrations";

export function AiBootReadinessStatus({ value }: { value?: AiBootReadiness }) {
  let label = "AI readiness unknown";
  const state = value?.state;
  if (state === "ready") label = "AI ready (GPU)";
  else if (state === "retrying") label = "AI warming";
  else if (
    state === "degraded" &&
    value?.models?.text?.state === "ready" &&
    value?.models?.vision?.state !== "ready"
  ) label = "Text ready; vision retrying";
  else if (state === "degraded") label = `AI degraded${value?.error_code ? `: ${value.error_code}` : ""}`;
  else if (state === "not_applicable") label = "AI boot warm not applicable";
  else if (state === "stale") label = "AI readiness stale";

  return <p className="text-sm text-ink-3" role="status">{label}</p>;
}
