import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { Library } from "@/routes/Library";
import type { LibraryBrowse } from "@/lib/api-types";
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const FIXTURES = {
  "/api/library/browse?limit=200": {
    ok: true,
    rows: [
      { id: 2, title: "Beta 002", tags: [{ id: 7, name: "Drama", color: null }, { id: 8, name: "4k", color: null }] },
      { id: 1, title: "Alpha 001", tags: [{ id: 7, name: "drama", color: null }] },
      { id: 3, title: "Gamma 003" },
    ],
  } satisfies LibraryBrowse,
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": {},
  "/api/scene_score/bottom?limit=20": {},
  "/api/live/status": { ok: true, available: false },
  "/api/live/recordings": { ok: true, recordings: [] },
};

function itemTitles() {
  return within(within(screen.getByRole("main")).getByRole("list"))
    .getAllByRole("button", { name: /^(Alpha|Beta|Gamma) / })
    .map((button) => button.textContent);
}

beforeEach(() => {
  window.localStorage.clear();
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("F019 Library search", () => {
  it("filters titles, reports no match, and restores all items on clear", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    await user.type(search, "002");
    expect(itemTitles()).toEqual(["Beta 002"]);
    await user.clear(search);
    await user.type(search, "missing-title");
    expect(screen.getByText("No items match")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("list")).toBeNull();
    await user.clear(search);
    expect(itemTitles()).toEqual(["Beta 002", "Alpha 001", "Gamma 003"]);
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(apiDeleteMock).not.toHaveBeenCalled();
  });

  it("matches titles and tags without case sensitivity, including absent tags", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    const search = screen.getByRole("searchbox", { name: "Search library" });
    await user.type(search, "DRAMA");
    expect(itemTitles()).toEqual(["Beta 002", "Alpha 001"]);
    await user.clear(search);
    await user.type(search, "gAmMa");
    expect(itemTitles()).toEqual(["Gamma 003"]);
  });

  it("keeps filtering across sort direction and density changes", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    const search = screen.getByRole("searchbox", { name: "Search library" });
    await user.type(search, "drama");
    await user.selectOptions(screen.getByRole("combobox", { name: "Sort by" }), "title");
    expect(itemTitles()).toEqual(["Alpha 001", "Beta 002"]);
    await user.click(screen.getByRole("button", { name: "Sort direction: ascending" }));
    expect(itemTitles()).toEqual(["Beta 002", "Alpha 001"]);
    await user.click(screen.getByRole("button", { name: "Compact" }));
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute("aria-pressed", "true");
    expect(itemTitles()).toEqual(["Beta 002", "Alpha 001"]);
    for (const row of within(within(screen.getByRole("main")).getByRole("list")).getAllByRole("listitem")) {
      expect(row).toHaveClass("py-0.5");
    }
    await user.clear(search);
    expect(itemTitles()).toEqual(["Gamma 003", "Beta 002", "Alpha 001"]);
  });

  it("also filters rows carrying string tags", async () => {
    const user = userEvent.setup();
    installApiFixtures(apiGetMock, apiPostMock, {
      ...FIXTURES,
      "/api/library/browse?limit=200": {
        ok: true,
        rows: FIXTURES["/api/library/browse?limit=200"].rows.map((row) => ({
          ...row,
          tags: row.tags?.map((tag) => tag.name),
        })),
      },
    });
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "DRAMA");
    expect(itemTitles()).toEqual(["Beta 002", "Alpha 001"]);
  });

  it("negative control: empty-query order equals the existing list order", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    expect(JSON.stringify(itemTitles())).toBe('["Beta 002","Alpha 001","Gamma 003"]');
    await user.selectOptions(screen.getByRole("combobox", { name: "Sort by" }), "title");
    expect(JSON.stringify(itemTitles())).toBe('["Alpha 001","Beta 002","Gamma 003"]');
    await user.click(screen.getByRole("button", { name: "Sort direction: ascending" }));
    expect(JSON.stringify(itemTitles())).toBe('["Gamma 003","Beta 002","Alpha 001"]');
  });
});
