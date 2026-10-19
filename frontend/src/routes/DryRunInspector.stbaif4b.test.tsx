import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";

const { apiPostMock } = vi.hoisted(() => ({ apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: vi.fn(),
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { DryRunInspector } from "./DryRunInspector";

// stbai F4b: react-router keeps the same DryRunInspector instance when only
// :siteId changes, so siteA's inspect/template result stayed on siteB's page.
function inspectResponse(site: string) {
  return {
    ok: true,
    page_url: `https://${site}.example.com/v`,
    page_host: `${site}.example.com`,
    winner: {
      selector: `a.${site}-winner`,
      url: `https://${site}.example.com/v.mp4`,
      score: 90,
      signals: ["mp4"],
      kind: "download",
      accepted: true,
      reason: "ok",
    },
    candidates: [],
    n_candidates: 1,
    n_accepted: 1,
    n_rejected: 0,
    safe_candidate_available: true,
  };
}

function templateResponse(site: string) {
  return {
    ok: true,
    url: `https://${site}.example.com/v`,
    host: `${site}.example.com`,
    template_matched: false,
    template: null,
    network_patterns: [],
    lint_warnings: [],
    has_blocking_lint: false,
    selector_hit_counts: {},
    candidate_classification: null,
    safe_candidate_selected: false,
  };
}

function NavHarness() {
  const nav = useNavigate();
  return (
    <div>
      <button onClick={() => nav("/sites/siteA/inspect")}>go-siteA</button>
      <button onClick={() => nav("/sites/siteB/inspect")}>go-siteB</button>
      <Routes>
        <Route path="/sites/:siteId/inspect" element={<DryRunInspector />} />
      </Routes>
    </div>
  );
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/sites/siteA/inspect"]}>
        <NavHarness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function paste(html: string) {
  fireEvent.change(screen.getByPlaceholderText("Paste page HTML source here…"), {
    target: { value: html },
  });
}

beforeEach(() => apiPostMock.mockReset());

describe("DryRunInspector stbai F4b result is scoped to :siteId", () => {
  it("siteA's inspect winner is gone after navigating to siteB", async () => {
    apiPostMock.mockResolvedValue(inspectResponse("siteA"));
    mount();
    paste("<a class='siteA-winner' href='/v.mp4'>DL</a>");
    fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
    expect(await screen.findByText("a.siteA-winner")).toBeTruthy(); // control
    expect(apiPostMock.mock.calls[0][0]).toBe("/api/sites/siteA/candidates/inspect");

    fireEvent.click(screen.getByText("go-siteB"));
    expect(
      screen.queryByText("a.siteA-winner"),
      "F4B-STALE-SITE-RESULT: siteA's inspect winner still shown on /sites/siteB/inspect",
    ).toBeNull();
    expect(screen.queryByText("Would select")).toBeNull();
  });

  it("siteA's template result is gone after navigating to siteB", async () => {
    apiPostMock.mockResolvedValue(templateResponse("siteA"));
    mount();
    paste("<a href='/v.mp4'>DL</a>");
    fireEvent.click(screen.getByRole("button", { name: "Test template" }));
    expect(await screen.findByText("siteA.example.com")).toBeTruthy(); // control

    fireEvent.click(screen.getByText("go-siteB"));
    expect(
      screen.queryByText("siteA.example.com"),
      "F4B-STALE-SITE-RESULT: siteA's template result still shown on /sites/siteB/inspect",
    ).toBeNull();
  });

  it("a siteA request resolving after the switch is not shown on siteB", async () => {
    let resolveA: (v: unknown) => void = () => {};
    apiPostMock.mockReturnValueOnce(new Promise((r) => (resolveA = r)));
    mount();
    paste("<a class='siteA-winner' href='/v.mp4'>DL</a>");
    fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
    expect(await screen.findByText("Inspecting…")).toBeTruthy();

    fireEvent.click(screen.getByText("go-siteB"));
    await act(async () => resolveA(inspectResponse("siteA")));
    expect(
      screen.queryByText("a.siteA-winner"),
      "F4B-STALE-SITE-RESULT: in-flight siteA inspect landed on /sites/siteB/inspect",
    ).toBeNull();
  });

  it("siteB's own inspect still renders and posts to siteB", async () => {
    apiPostMock.mockResolvedValueOnce(inspectResponse("siteA"));
    mount();
    paste("<a>A</a>");
    fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
    expect(await screen.findByText("a.siteA-winner")).toBeTruthy();

    fireEvent.click(screen.getByText("go-siteB"));
    apiPostMock.mockResolvedValueOnce(inspectResponse("siteB"));
    paste("<a>B</a>");
    fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
    expect(await screen.findByText("a.siteB-winner")).toBeTruthy();
    expect(apiPostMock.mock.calls[1][0]).toBe("/api/sites/siteB/candidates/inspect");
    expect(screen.queryByText("a.siteA-winner")).toBeNull();
  });

  it("NEG: navigating to the same site keeps its result", async () => {
    apiPostMock.mockResolvedValue(inspectResponse("siteA"));
    mount();
    paste("<a class='siteA-winner' href='/v.mp4'>DL</a>");
    fireEvent.click(screen.getByRole("button", { name: "Inspect candidates" }));
    expect(await screen.findByText("a.siteA-winner")).toBeTruthy();

    fireEvent.click(screen.getByText("go-siteA"));
    expect(screen.getByText("a.siteA-winner")).toBeTruthy();
    expect(screen.getByText("Would select")).toBeTruthy();
  });
});
