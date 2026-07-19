import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// C7: the Cluster federation trust control. We test the hook wiring directly --
// Cluster.tsx itself has no vitest harness (its AppShell import tree does not
// resolve under jsdom, a pre-existing gap unrelated to this change), and tsc +
// the app-boot smoke cover the component render. This locks the endpoint/body
// contract that surfaces the 632 backend trust tier.
const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { useFedSetTrust } from "@/hooks/useCluster";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ ok: true });
});

describe("useFedSetTrust (C7 Cluster trust control)", () => {
  it("POSTs /api/fed/set_trust with instance_id + tier", async () => {
    const { result } = renderHook(() => useFedSetTrust(), { wrapper });
    result.current.mutate({ instance_id: "peerX", tier: "blocked" });
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/fed/set_trust", {
        instance_id: "peerX",
        tier: "blocked",
      }),
    );
  });

  it("supports all three tiers", async () => {
    const { result } = renderHook(() => useFedSetTrust(), { wrapper });
    for (const tier of ["trusted", "observed", "blocked"]) {
      result.current.mutate({ instance_id: "p", tier });
      await waitFor(() =>
        expect(apiPostMock).toHaveBeenCalledWith("/api/fed/set_trust", {
          instance_id: "p",
          tier,
        }),
      );
    }
  });
});
