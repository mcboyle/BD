// stb-fix (sweep spa-tools-budget-ai F3-F6). API mocked throughout; no AI calls.
//  F3 /ai-assist must not list provider models while the AI master switch is off.
//  F4 Clear (and unmount) must abort an in-flight scratchpad turn.
//  F5 a user Cancel must not raise the global mutation error toast; real failures still toast.
//  F6 the budget history request carries the site id as an encoded ?site= query parameter.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const { apiGetMock, apiPostMock, toastError } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

import { toast } from "sonner";
import { AiScratchpad } from "@/components/AiScratchpad";
import { useBudgetHistory } from "@/hooks/useBudgetHistory";
import { mutationErrorMessage, shouldGlobalErrorToast } from "@/lib/mutationErrors";
import AiAssist from "@/routes/AiAssist";

// Same wiring as main.tsx's MutationCache safety net.
function client() {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _v, _c, mutation) => {
        if (!shouldGlobalErrorToast(Boolean(mutation.options.onError))) return;
        toast.error(mutationErrorMessage(error));
      },
    }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
}

// apiPost that hangs until aborted, then rejects like fetch does; records the signal.
let lastSignal: AbortSignal | null = null;
function hangingPost() {
  apiPostMock.mockImplementation((_u: string, _b: unknown, s: AbortSignal) => {
    lastSignal = s;
    return new Promise((_res, rej) => {
      s.addEventListener("abort", () => rej(new DOMException("signal is aborted without reason", "AbortError")));
    });
  });
}

async function sendPrompt() {
  fireEvent.change(screen.getByLabelText("prompt"), { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Cancel" });
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  toastError.mockReset();
  lastSignal = null;
});
afterEach(() => cleanup());

describe("F4 scratchpad Clear/unmount abort the in-flight turn", () => {
  it("Clear aborts the request", async () => {
    hangingPost();
    render(<QueryClientProvider client={client()}><AiScratchpad /></QueryClientProvider>);
    await sendPrompt();
    expect(lastSignal?.aborted).toBe(false); // control: in flight before Clear
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Clear" })); });
    expect(lastSignal?.aborted).toBe(true); // F4_CLEAR_NO_ABORT
  });
  it("unmount aborts the request", async () => {
    hangingPost();
    const v = render(<QueryClientProvider client={client()}><AiScratchpad /></QueryClientProvider>);
    await sendPrompt();
    v.unmount();
    expect(lastSignal?.aborted).toBe(true); // F4_UNMOUNT_NO_ABORT
  });
});

describe("F5 user Cancel is not a failure toast", () => {
  it("Cancel raises no error toast", async () => {
    hangingPost();
    render(<QueryClientProvider client={client()}><AiScratchpad /></QueryClientProvider>);
    await sendPrompt();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Cancel" })); });
    await new Promise((r) => setTimeout(r, 30));
    expect(lastSignal?.aborted).toBe(true);
    expect(toastError).not.toHaveBeenCalled(); // F5_CANCEL_TOASTED
  });
  it("a real failure still toasts once (control)", async () => {
    apiPostMock.mockRejectedValue(new Error("POST /api/ai/chat -> 500"));
    render(<QueryClientProvider client={client()}><AiScratchpad /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("prompt"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError.mock.calls[0][0]).toBe("POST /api/ai/chat -> 500");
  });
});

describe("F3 /ai-assist lists models only with the AI switch on", () => {
  function mountAiAssist(enabled: boolean) {
    apiGetMock.mockImplementation(async (p: string) => (p.startsWith("/api/ai/status") ? { ok: enabled, enabled } : {}));
    apiPostMock.mockResolvedValue({ ok: true, models: ["m1"] });
    render(<QueryClientProvider client={client()}><MemoryRouter><AiAssist /></MemoryRouter></QueryClientProvider>);
  }
  const modelCalls = () => apiPostMock.mock.calls.filter(([u]) => u === "/api/ai/models").length;
  it("switch off: no /api/ai/models request", async () => {
    mountAiAssist(false);
    await waitFor(() => expect(apiGetMock).toHaveBeenCalledWith("/api/ai/status", expect.anything()));
    await new Promise((r) => setTimeout(r, 30));
    expect(modelCalls()).toBe(0); // F3_MODELS_WHILE_OFF
  });
  it("switch on: models are listed (control)", async () => {
    mountAiAssist(true);
    await waitFor(() => expect(modelCalls()).toBe(1));
  });
});

describe("F6 budget history encodes the site id", () => {
  function Probe({ siteId }: { siteId: string }) {
    useBudgetHistory(siteId, 30);
    return null;
  }
  it.each([
    ["example.com", "/api/daily_budget/history?site=example.com&days=30"], // control: plain id
    ["a/b", "/api/daily_budget/history?site=a%2Fb&days=30"],
    ["..", "/api/daily_budget/history?site=..&days=30"],
    ["/../x", "/api/daily_budget/history?site=%2F..%2Fx&days=30"],
    ["x?days=1", "/api/daily_budget/history?site=x%3Fdays%3D1&days=30"],
    ["50%", "/api/daily_budget/history?site=50%25&days=30"],
  ])("site %s", async (sid, want) => {
    apiGetMock.mockResolvedValue({ ok: true, days: [] });
    render(<QueryClientProvider client={client()}><Probe siteId={sid} /></QueryClientProvider>);
    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    expect(apiGetMock.mock.calls[0][0]).toBe(want); // F6_RAW_SITE_ID
  });
});
