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
      { id: 2, title: "Beta 002" },
      { id: 1, title: "Alpha 001" },
    ],
  } satisfies LibraryBrowse,
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": {},
  "/api/scene_score/bottom?limit=20": {},
  "/api/live/status": { ok: true, available: false },
  "/api/live/recordings": { ok: true, recordings: [] },
};

function itemList() {
  return within(screen.getByRole("main")).getByRole("list");
}

function viewButtons() {
  const group = screen.getByRole("group", { name: "Item view" });
  return {
    list: within(group).getByRole("button", { name: "List" }),
    grid: within(group).getByRole("button", { name: "Grid" }),
  };
}

beforeEach(() => {
  window.localStorage.clear();
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("F019 Library grid/list toggle", () => {
  it("starts as a list and flips the items between grid and list layouts", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    const { list, grid } = viewButtons();

    expect(list).toHaveAttribute("aria-pressed", "true");
    expect(grid).toHaveAttribute("aria-pressed", "false");
    expect(itemList()).toHaveAttribute("data-view", "list");
    expect(itemList().className).not.toMatch(/\bgrid\b/);

    await user.click(grid);
    expect(grid).toHaveAttribute("aria-pressed", "true");
    expect(list).toHaveAttribute("aria-pressed", "false");
    expect(itemList()).toHaveAttribute("data-view", "grid");
    expect(itemList().className).toMatch(/\bgrid\b/);
    expect(itemList().className).not.toMatch(/\bdivide-y\b/);
    expect(within(itemList()).getAllByRole("listitem")).toHaveLength(2);

    await user.click(list);
    expect(itemList()).toHaveAttribute("data-view", "list");
    expect(itemList().className).toMatch(/\bdivide-y\b/);
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(apiDeleteMock).not.toHaveBeenCalled();
  });

  it("keeps search and item preview working in grid view", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    await user.click(viewButtons().grid);

    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "alpha");
    expect(within(itemList()).getAllByRole("listitem")).toHaveLength(1);
    await user.click(within(itemList()).getByRole("button", { name: "Alpha 001" }));
    expect(within(await screen.findByRole("dialog")).getByText("Library item #1")).toBeInTheDocument();
  });

  it("remembers the chosen view and falls back to list on a junk stored value", async () => {
    const user = userEvent.setup();
    const first = renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    await user.click(viewButtons().grid);
    expect(window.localStorage.getItem("bd-library-view")).toBe("grid");
    first.unmount();

    const second = renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    expect(itemList()).toHaveAttribute("data-view", "grid");
    second.unmount();

    window.localStorage.setItem("bd-library-view", "mosaic");
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Beta 002" });
    expect(itemList()).toHaveAttribute("data-view", "list");
    expect(viewButtons().list).toHaveAttribute("aria-pressed", "true");
  });
});
