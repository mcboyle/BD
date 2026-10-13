import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// scs-fix F2 (bughunt spa-cockpit-settings, finder bd-worker-S52-A). POST
// /api/ai/models makes the SERVER call the configured AI endpoint. Settings
// fired it on every page open (2x, the draft settling) and on every keystroke
// in the Endpoint field -- with ai_enabled=false too -- so opening /settings
// called the AI endpoint, and typing "http://127.0.0.1:18753/v1" sent the
// server to 25 partial URLs. Now: never while AI is off; once per quiet period.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

let modelCalls: unknown[] = [];

function mount(config: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/csrf")) return Promise.resolve(jsonResponse({ csrf_token: "t" }));
      if (url.includes("/api/global_config")) return Promise.resolve(jsonResponse(config));
      if (url.includes("/api/ai/models")) {
        modelCalls.push(JSON.parse(String(init?.body ?? "{}")));
        return Promise.resolve(jsonResponse({ ok: true, models: ["m1"] }));
      }
      return new Promise<Response>(() => {});
    }),
  );
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const settle = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(() => {
  modelCalls = [];
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("scs-fix F2: AI model detection is gated and debounced", () => {
  it("AI disabled: opening Settings and typing an endpoint never POSTs /api/ai/models", async () => {
    mount({ ai_enabled: false, ai_endpoint: "http://localhost:11434" });
    const filter = await screen.findByLabelText("Filter settings");
    fireEvent.change(filter, { target: { value: "endpoint" } });
    const box = await screen.findByPlaceholderText("http://localhost:11434");
    for (const v of ["h", "ht", "htt", "http://x"]) fireEvent.change(box, { target: { value: v } });
    await settle(1500);
    expect(modelCalls, "BD-SCS-F2-DETECT-WHILE-AI-OFF").toHaveLength(0);
  });

  it("AI enabled: page open detects once; a burst of keystrokes detects once, with the final endpoint", async () => {
    mount({ ai_enabled: true, ai_provider: "ollama", ai_endpoint: "http://localhost:11434" });
    await waitFor(() => expect(modelCalls).toHaveLength(1), { timeout: 3000 });
    expect(modelCalls[0]).toEqual({ provider: "ollama", endpoint: "http://localhost:11434" });

    const filter = await screen.findByLabelText("Filter settings");
    fireEvent.change(filter, { target: { value: "endpoint" } });
    const box = await screen.findByDisplayValue("http://localhost:11434");
    const typed = "http://127.0.0.1:18753/v1";
    for (let i = 1; i <= typed.length; i++) fireEvent.change(box, { target: { value: typed.slice(0, i) } });
    await waitFor(() => expect(modelCalls).toHaveLength(2), { timeout: 3000 });
    await settle(1200);
    expect(modelCalls, "BD-SCS-F2-DETECT-PER-KEYSTROKE").toHaveLength(2);
    expect(modelCalls[1]).toEqual({ provider: "ollama", endpoint: typed });
  });
});
