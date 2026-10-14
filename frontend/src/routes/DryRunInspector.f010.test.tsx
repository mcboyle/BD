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

const CHECK = "✓ a safe candidate would be selected";
const GENERIC =
  "no template — generic detection would pick a candidate (not a template result)";

const row = {
  selector: "video",
  text: "Direct Video",
  url: "https://example.com/media/direct_video.mp4",
  href: "",
  data_href: "",
  data_url: "",
  data_src: "",
  score: -15,
  size: 0,
  host: "example.com",
  signals: ["media_extension"],
  kind: "download",
  accepted: true,
  reason: "download: media_extension",
};

function templateResponse(matched: boolean) {
  return {
    ok: true,
    url: "https://example.com/watch/1",
    host: "example.com",
    template_matched: matched,
    template: matched
      ? { resolutions: ["1080"], selectors: [{}], network_patterns: [] }
      : null,
    network_patterns: [],
    lint_warnings: [],
    has_blocking_lint: false,
    selector_hit_counts: [],
    candidate_classification: null,
    safe_candidate_selected: true,
  };
}

function inspectResponse() {
  return {
    ok: true,
    page_url: "https://example.com/watch/1",
    page_host: "example.com",
    winner: row,
    candidates: [row],
    n_candidates: 1,
    n_accepted: 1,
    n_rejected: 0,
    safe_candidate_available: true,
  };
}

function mountAndClick(button: string) {
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
    target: { value: "<video src='x.mp4'>Direct Video</video>" },
  });
  fireEvent.click(screen.getByRole("button", { name: button }));
}

beforeEach(() => apiPostMock.mockReset());

describe("DryRunInspector F010", () => {
  it("no template: no green check beside the no-template badge", async () => {
    apiPostMock.mockResolvedValue(templateResponse(false));
    mountAndClick("Test template");

    expect(await screen.findByText("no template")).toBeTruthy();
    const line = screen.getByText(GENERIC);
    expect(line.className).toContain("text-ink-3");
    expect(screen.queryByText(CHECK)).toBeNull();
  });

  it("matched template: the green check stays", async () => {
    apiPostMock.mockResolvedValue(templateResponse(true));
    mountAndClick("Test template");

    expect(await screen.findByText("template matched")).toBeTruthy();
    expect(screen.getByText(CHECK).className).toContain("text-green");
    expect(screen.queryByText(GENERIC)).toBeNull();
  });

  it("a negative score is labelled as a score, not shown bare", async () => {
    apiPostMock.mockResolvedValue(inspectResponse());
    mountAndClick("Inspect candidates");

    const score = await screen.findByText("score -15");
    expect(score.getAttribute("title")).toContain("not an error");
    expect(screen.queryByText("-15")).toBeNull();
  });
});
