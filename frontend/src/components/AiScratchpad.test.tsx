// AiScratchpad — 9.10 frontend behavioral gate (vitest/jsdom).
//
// Covers the 9.10 FE RED-first list: empty prompt disables Send; posts to
// /api/ai/chat; renders the response as TEXT (injected HTML does not execute);
// model override flows through; image attach hidden for a text-only model and
// shown for a vision model; provider/latency shown; Clear resets state.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiPost: apiPostMock,
  ApiError: class ApiError extends Error {},
}));

import { AiScratchpad } from "./AiScratchpad";

function mount(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AiScratchpad {...props} />
    </QueryClientProvider>,
  );
}

function ok(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    response: "hello from model",
    model: "bd-text-small",
    provider: "ollama",
    latency_ms: 12,
    image_included: false,
    error: "",
    ...overrides,
  };
}

const sendBtn = () => screen.getByRole("button", { name: /^send$/i }) as HTMLButtonElement;
const prompt = () => screen.getByLabelText("prompt") as HTMLTextAreaElement;

beforeEach(() => {
  apiPostMock.mockReset();
});

describe("AiScratchpad — 9.10 backend wiring", () => {
  it("disables Send when the prompt is empty", () => {
    mount();
    expect(sendBtn().disabled).toBe(true);
    fireEvent.change(prompt(), { target: { value: "hi" } });
    expect(sendBtn().disabled).toBe(false);
  });

  it("posts to /api/ai/chat with the prompt and renders the reply as text", async () => {
    apiPostMock.mockResolvedValue(ok());
    mount();
    fireEvent.change(prompt(), { target: { value: "hi" } });
    fireEvent.click(sendBtn());
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "/api/ai/chat",
        expect.objectContaining({ prompt: "hi" }),
        expect.anything(),
      ),
    );
    const out = await screen.findByTestId("ai-response");
    expect(out.textContent).toContain("hello from model");
  });

  it("renders injected HTML as inert text (no dangerouslySetInnerHTML)", async () => {
    apiPostMock.mockResolvedValue(
      ok({ response: '<img src=x onerror="window.__pwned=1">' }),
    );
    const { container } = mount();
    fireEvent.change(prompt(), { target: { value: "x" } });
    fireEvent.click(sendBtn());
    const out = await screen.findByTestId("ai-response");
    expect(out.textContent).toContain("<img");
    expect(container.querySelector("img")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("flows a model override through to the request body", async () => {
    apiPostMock.mockResolvedValue(ok({ model: "qwen2.5vl:7b" }));
    mount({ models: ["bd-text-small", "qwen2.5vl:7b"], defaultModel: "qwen2.5vl:7b" });
    fireEvent.change(prompt(), { target: { value: "hi" } });
    fireEvent.click(sendBtn());
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "/api/ai/chat",
        expect.objectContaining({ model: "qwen2.5vl:7b" }),
        expect.anything(),
      ),
    );
  });

  it("shows provider and latency on the response", async () => {
    apiPostMock.mockResolvedValue(ok({ provider: "ollama", latency_ms: 42 }));
    mount();
    fireEvent.change(prompt(), { target: { value: "hi" } });
    fireEvent.click(sendBtn());
    await screen.findByTestId("ai-response");
    expect(screen.getByText(/42ms/)).toBeTruthy();
    expect(screen.getByText(/ollama/)).toBeTruthy();
  });

  it("Clear resets the prompt and the response", async () => {
    apiPostMock.mockResolvedValue(ok({ response: "resp text" }));
    mount();
    fireEvent.change(prompt(), { target: { value: "hi" } });
    fireEvent.click(sendBtn());
    await screen.findByTestId("ai-response");
    fireEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(screen.queryByTestId("ai-response")).toBeNull();
    expect(prompt().value).toBe("");
  });

  it("seeds the prompt from a preset", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: /summarize this error/i }));
    expect(prompt().value.length).toBeGreaterThan(0);
  });
});

describe("AiScratchpad — vision image gating", () => {
  it("hides the image attach for a text-only model", () => {
    mount({ models: ["bd-text-small"], defaultModel: "bd-text-small" });
    expect(screen.queryByLabelText(/image \(vision model\)/i)).toBeNull();
  });

  it("shows the image attach for a vision model", () => {
    mount({ models: ["qwen2.5vl:7b"], defaultModel: "qwen2.5vl:7b" });
    expect(screen.getByLabelText(/image \(vision model\)/i)).toBeTruthy();
  });
});
