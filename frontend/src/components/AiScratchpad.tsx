// AiScratchpad — 9.10 "Ask the model" dev/operator scratchpad (v3.66.381+).
//
// A stateless aid mounted under Settings -> AI Assist. It posts to the hardened
// POST /api/ai/chat via useAiChat and renders the model's reply as PLAIN TEXT
// only (never dangerouslySetInnerHTML). Image attach appears only for a
// vision-capable model (the backend is the real gate via the 9.1 registry; this
// is a UX guard). Nothing is persisted; Clear resets the in-page state.

import * as React from "react";

import { Button } from "@/components/ui/button";
import { ModelSelect } from "@/components/ui/ModelSelect";
import { useAiChat } from "@/hooks/useAiChat";

// Safe presets — each seeds the prompt with a benign framing. They never carry
// secrets or repo context (the backend injects none either).
const PRESETS: { label: string; text: string }[] = [
  { label: "Summarize this error", text: "Summarize the likely cause of this error and the first thing to check:\n\n" },
  { label: "Explain this route", text: "Explain what this app route does and its main user action:\n\n" },
  { label: "Draft a validation report", text: "Draft a short validation report from these notes:\n\n" },
  { label: "Find a11y issues", text: "List likely accessibility issues in this UI description:\n\n" },
  { label: "Explain a failed job", text: "Given this failed-job reason, explain it plainly and suggest a safe next step:\n\n" },
];

// Heuristic only; overridable. The backend enforces the real capability check.
function looksVision(model: string): boolean {
  return /vl|vision|llava|moondream|bakllava/i.test(model);
}

async function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const res = String(r.result || "");
      const comma = res.indexOf(",");
      resolve(comma >= 0 ? res.slice(comma + 1) : res);
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export interface AiScratchpadProps {
  /** Detected model tags for the optional picker (9.1 registry / Cut 7). */
  models?: string[];
  defaultModel?: string;
  /** Override the vision-capability heuristic (backend is the real gate). */
  isVisionModel?: (m: string) => boolean;
}

export function AiScratchpad({
  models = [],
  defaultModel = "",
  isVisionModel = looksVision,
}: AiScratchpadProps) {
  const [prompt, setPrompt] = React.useState("");
  const [model, setModel] = React.useState(defaultModel);
  const [imageB64, setImageB64] = React.useState("");
  const chat = useAiChat();

  const visionOk = model ? isVisionModel(model) : false;
  // Drop any attached image the moment the model is no longer vision-capable.
  React.useEffect(() => {
    if (!visionOk && imageB64) setImageB64("");
  }, [visionOk, imageB64]);

  const canSend = prompt.trim().length > 0 && !chat.isPending;

  function onSend() {
    if (!canSend) return;
    chat.mutate({
      prompt: prompt.trim(),
      ...(model ? { model } : {}),
      ...(imageB64 && visionOk ? { image_b64: imageB64 } : {}),
    });
  }

  function onClear() {
    setPrompt("");
    setImageB64("");
    chat.reset();
  }

  async function onPickImage(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      setImageB64(await fileToB64(f));
    } catch {
      setImageB64("");
    }
  }

  const resp = chat.data;

  return (
    <div className="space-y-3" data-testid="ai-scratchpad">
      <p className="text-xs text-ink-soft">
        Ask the model — advisory scratchpad. The reply is the model&apos;s text only:
        not a test result, not proof, never an action. Nothing here is saved.
      </p>

      {models.length > 0 && (
        <div className="space-y-1">
          <label htmlFor="ai-chat-model" className="text-sm">
            Model (optional)
          </label>
          <ModelSelect
            id="ai-chat-model"
            value={model}
            onChange={setModel}
            options={models}
            placeholder="default text model"
          />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <Button
            key={p.label}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setPrompt(p.text)}
          >
            {p.label}
          </Button>
        ))}
      </div>

      <textarea
        aria-label="prompt"
        className="w-full min-h-[120px] rounded-md border border-hairline bg-transparent p-3 text-sm"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="Ask the model..."
      />

      {visionOk && (
        <div className="space-y-1">
          <label htmlFor="ai-chat-image" className="text-sm">
            Image (vision model)
          </label>
          <input
            id="ai-chat-image"
            type="file"
            accept="image/*"
            onChange={onPickImage}
          />
          {imageB64 && <span className="ml-2 text-xs text-ink-soft">image attached</span>}
        </div>
      )}

      <div className="flex gap-2">
        <Button type="button" onClick={onSend} disabled={!canSend}>
          {chat.isPending ? "Sending..." : "Send"}
        </Button>
        {chat.isPending && (
          <Button type="button" variant="outline" onClick={chat.cancel}>
            Cancel
          </Button>
        )}
        <Button type="button" variant="outline" onClick={onClear}>
          Clear
        </Button>
      </div>

      {resp && (
        <div className="space-y-1">
          {resp.ok ? (
            <div
              data-testid="ai-response"
              className="rounded-md border border-hairline p-3 text-sm whitespace-pre-wrap"
            >
              {resp.response}
            </div>
          ) : (
            <div role="alert" className="rounded-md border border-hairline p-3 text-sm">
              {resp.error || "AI request failed"}
            </div>
          )}
          <p className="text-xs text-ink-soft">
            {resp.provider} &middot; {resp.model || "\u2014"} &middot; {resp.latency_ms}ms
            {resp.image_included ? " \u00b7 image" : ""}
          </p>
        </div>
      )}

      {chat.isError && !resp && (
        <div role="alert" className="rounded-md border border-hairline p-3 text-sm">
          {chat.error?.message || "Request error"}
        </div>
      )}
    </div>
  );
}
