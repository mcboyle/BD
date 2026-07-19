import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {} }));

import AiAssist from "./AiAssist";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><AiAssist /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPostMock.mockResolvedValue({ ok: true, models: [] });
  apiGetMock.mockResolvedValue({});
});

describe("AiAssist (382)", () => {
  it("renders inside the AppShell chrome (dark bg fills the route, 3d)", () => {
    mount();
    // AppShell mobile branch renders PageHeader's <header> (role=banner).
    // A bare <div className="space-y-4 p-4"> route has no banner -> RED pre-fix.
    expect(screen.getByRole("banner")).toBeTruthy();
  });
});
