import { useQuery } from "@tanstack/react-query";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface HealthResponse {
  ok: boolean;
  version?: string;
  uptime_seconds?: number;
}

// Placeholder route for U1. Renders enough to verify:
//   - Tailwind is applying the UI_TOKENS palette
//   - TanStack Query can hit Flask /api/health through the dev proxy
//     (and through the same origin in production)
//   - The shadcn components compiled and import cleanly
//   - Inter font loaded
// Replaced wholesale by the real Home page in U3+.
export function ScaffoldHome() {
  const { data, isLoading, isError } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: async () => {
      const r = await fetch("/api/health");
      if (!r.ok) throw new Error(`health ${r.status}`);
      return r.json();
    },
  });

  return (
    <main className="min-h-dvh bg-bg text-ink p-6">
      <div className="mx-auto max-w-xl space-y-4">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tighter">
            BulkDownloader
          </h1>
          <p className="text-ink-3 text-sm">
            D3 scaffold — U1. Real UI lands in U3.
          </p>
        </header>

        <Card className="space-y-3 p-4">
          <div className="flex items-center justify-between">
            <span className="text-ink-2 text-sm">Backend</span>
            {isLoading && <Badge variant="outline">checking…</Badge>}
            {isError && <Badge variant="destructive">unreachable</Badge>}
            {data?.ok && (
              <Badge variant="default">
                ok · v<span className="tabular">{data.version ?? "?"}</span>
              </Badge>
            )}
          </div>
          <div className="hairline rounded-md p-3 text-ink-3 text-xs">
            This page exists so the scaffold is verifiable end-to-end.
            If you can see the styling above and the badge resolves to
            “ok”, U1 is wired correctly.
          </div>
        </Card>
      </div>
    </main>
  );
}
