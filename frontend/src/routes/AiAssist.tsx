// AiAssist — mounts the 9.10 AI scratchpad (wired v3.66.382).
//
// A dedicated page for the "Ask the model" scratchpad. It pulls the detected
// model tags from the existing (already-wired) POST /api/ai/models so the
// optional picker offers real options; everything else is delegated to
// <AiScratchpad/>, which posts to /api/ai/chat.

import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import { AiScratchpad } from "@/components/AiScratchpad";
import { AiHelpersPanel } from "@/components/ui/AiHelpersPanel";
import { apiPost } from "@/lib/api-client";

interface ModelsResponse {
  ok?: boolean;
  models?: string[];
  error?: string;
}

export default function AiAssist() {
  // The model list rides the existing /api/ai/models endpoint (already
  // spa_wired). Failure is non-fatal: the picker just falls back to free text.
  const models = useQuery<ModelsResponse>({
    queryKey: ["ai-models", "scratchpad"],
    queryFn: ({ signal }) =>
      apiPost<ModelsResponse>("/api/ai/models", {}, signal),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const options = Array.isArray(models.data?.models) ? models.data!.models! : [];

  return (
    <AppShell title="AI Assist — scratchpad" subtitle="Ask the configured local model a one-off question. Advisory only.">
      <div className="space-y-4" data-testid="ai-assist-page">
        <AiScratchpad models={options} />
        <AiHelpersPanel />
      </div>
    </AppShell>
  );
}
