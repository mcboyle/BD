import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: vi.fn(),
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { DryRunInspector } from "./DryRunInspector";

// stb-fix F1: dry_run._selector_hit_counts returns a dict keyed by
// "group.field" -> [{selector, hits}]; the inspector must render it.
function templateResponse(hits: unknown) {
  return {
    ok: true,
    url: "https://example.com/watch/1",
    host: "example.com",
    template_matched: true,
    template: {
      enabled: true,
      host: "example.com",
      selectors: ["download"],
      resolutions: [1080],
      patterns: [],
    },
    network_patterns: [],
    lint_warnings: [],
    has_blocking_lint: false,
    selector_hit_counts: hits,
    candidate_classification: null,
    safe_candidate_selected: false,
  };
}

function mountAndTest() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/sites/fixture/inspect"]}>
        <Routes>
          <Route path="/sites/:siteId/inspect" element={<DryRunInspector />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByPlaceholderText("Paste page HTML source here…"), {
    target: { value: "<a class='dl' href='/d/1080'>Download</a>" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test template" }));
}

beforeEach(() => apiPostMock.mockReset());

describe("DryRunInspector stb-fix F1 selector hit-counts", () => {
  it("renders the backend dict shape (group -> [{selector, hits}])", async () => {
    apiPostMock.mockResolvedValue(
      templateResponse({
        "download.row_selectors": [
          { selector: "a.dl", hits: 2 },
          { selector: "a.alt", hits: 0 },
        ],
        "quality.open_menu": [
          { selector: "button:has-text('Quality')", hits: "n/a (non-CSS selector)" },
        ],
      }),
    );
    mountAndTest();

    expect(await screen.findByText("template matched")).toBeTruthy();
    expect(
      screen.queryByText("Selector hits"),
      "F1_HITS_NOT_RENDERED: dict-shaped selector_hit_counts produced no Selector hits card",
    ).not.toBeNull();
    expect(screen.getByText("a.dl")).toBeTruthy();
    expect(screen.getByText("a.alt")).toBeTruthy();
    expect(screen.getByText("button:has-text('Quality')")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getByText("n/a (non-CSS selector)")).toBeTruthy();
    expect(screen.getAllByText("download.row_selectors")).toHaveLength(2);
    expect(screen.getAllByText("quality.open_menu")).toHaveLength(1);
  });

  it("an empty dict renders no Selector hits card", async () => {
    apiPostMock.mockResolvedValue(templateResponse({}));
    mountAndTest();

    expect(await screen.findByText("template matched")).toBeTruthy();
    expect(screen.queryByText("Selector hits")).toBeNull();
  });

  it("a flat array (legacy shape) still renders", async () => {
    apiPostMock.mockResolvedValue(templateResponse([{ selector: "a.flat", hits: 3 }]));
    mountAndTest();

    expect(await screen.findByText("Selector hits")).toBeTruthy();
    expect(screen.getByText("a.flat")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });
});
