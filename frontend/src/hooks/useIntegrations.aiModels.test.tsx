import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { useAiModels } from "./useIntegrations";

// Cut 7 (7.1) — useAiModels gains optional draft-endpoint variables so the
// model list reflects the endpoint being EDITED, not just the saved one.
// Back-compat: calling it with no variables must still POST {} exactly as
// before (the existing Integrations consumer passes nothing).

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

let bodies: string[] = [];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/csrf")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ csrf_token: "t" }),
        } as unknown as Response);
      }
      // record only the model-list POST body
      bodies.push(String(init?.body ?? ""));
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ ok: true, models: ["qwen2.5:7b"], provider: "ollama" }),
      } as unknown as Response);
    }),
  );
});

describe("useAiModels back-compat (Cut 7 / 7.1)", () => {
  it("posts an empty body when called with no variables", async () => {
    const { result } = renderHook(() => useAiModels(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync();
    });
    await waitFor(() => expect(bodies.length).toBe(1));
    expect(JSON.parse(bodies[0])).toEqual({});
  });

  it("forwards draft endpoint variables when provided", async () => {
    const { result } = renderHook(() => useAiModels(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ provider: "ollama", endpoint: "http://host:11434" });
    });
    await waitFor(() => expect(bodies.length).toBe(1));
    expect(JSON.parse(bodies[0])).toEqual({ provider: "ollama", endpoint: "http://host:11434" });
  });
});
