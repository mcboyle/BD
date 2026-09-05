import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: vi.fn(),
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { DryRunInspector } from "./DryRunInspector";

const winner = {
  selector: "a.fixture",
  text: "Fixture",
  url: "https://example.com/video/fixture.mp4",
  href: "/video/fixture.mp4",
  data_href: "",
  data_url: "",
  data_src: "",
  score: 100,
  size: 1,
  host: "example.com",
  signals: ["media_extension"],
  kind: "download",
  accepted: true,
  reason: "download: media_extension",
};

function response(available: boolean) {
  return {
    ok: true,
    page_url: "https://example.com/category/fixture",
    page_host: "example.com",
    winner: available ? winner : null,
    candidates: available ? [winner] : [],
    n_candidates: available ? 1 : 0,
    n_accepted: available ? 1 : 0,
    n_rejected: 0,
    safe_candidate_available: available,
  };
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/sites/fixture/inspect"]}>
        <Routes>
          <Route path="/sites/:siteId/inspect" element={<DryRunInspector />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function inspect(available: boolean) {
  apiPostMock.mockResolvedValue(response(available));
  mount();
  fireEvent.change(screen.getByPlaceholderText("Paste page HTML source here…"), {
    target: { value: "<a>fixture</a>" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
}

beforeEach(() => apiPostMock.mockReset());

describe("DryRunInspector candidate availability contract (row 734)", () => {
  it("renders the backend's emitted true key as a checkmark", async () => {
    await inspect(true);

    expect(
      await screen.findByText("✓ a safe candidate would be selected"),
    ).toBeTruthy();
    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(apiPostMock).toHaveBeenCalledWith(
      "/api/sites/fixture/candidates/inspect",
      { html: "<a>fixture</a>" },
    );
  });

  it("renders the backend's emitted false key without a checkmark", async () => {
    await inspect(false);

    expect(
      await screen.findByText("no safe candidate from supplied HTML"),
    ).toBeTruthy();
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });
});
