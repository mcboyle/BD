import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// C7: the Cluster federation trust control. This locks the endpoint/body
// contract that surfaces the 632 backend trust tier, at the hook seam.
//
// CORRECTION (backlog row 185). This comment used to claim "Cluster.tsx itself
// has no vitest harness (its AppShell import tree does not resolve under
// jsdom)". That is measured FALSE: Cluster renders under jsdom both standalone
// and through renderAppAt("/cluster"), and Cluster.wired.test.tsx now drives
// the whole route that way. The claim is corrected here rather than left to
// mislead the next reader into rebuilding this file's workaround.
// NOTE ON /api/fed/set_trust: it is dispatched ONE-CLICK from the peer-row
// <select> onChange, so it is a DECLARED EXCLUSION from the gated-write set in
// tests/test_t8_cluster_wired.py -- a fact about that gate's denominator, not a
// product ruling. Gating it is a separate backlog row.
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
