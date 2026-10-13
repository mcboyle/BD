// sltm-fix (bughunt spa-library-templates-mobile F1/F2/F3, finder bd-worker-C2-C).
//
// F1: the Library fetched only the newest 200 items (/api/library/browse?limit=200)
// and searched/sorted that page client-side, ignoring next_cursor: an older item
// could not be found and nothing said the list was partial.
// F3: a failed items load rendered "No library items." exactly like an empty
// library.
// F2: a long unbroken title widened the whole page at 390 px. Layout itself is
// measured in Chromium (sltm-fix evidence); here only the wrapping classes the
// fix relies on are pinned.
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const LONG_TITLE = "x".repeat(160);
const SIDE = {
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": {},
  "/api/scene_score/bottom?limit=20": {},
  "/api/live/status": { ok: true, available: false },
  "/api/live/recordings": { ok: true, recordings: [] },
};
const PAGED = {
  ...SIDE,
  "/api/library/browse?limit=200": {
    ok: true,
    rows: [
      { id: 902, title: "New Item 902" },
      { id: 901, title: LONG_TITLE },
    ],
    next_cursor: "c-901",
  },
  "/api/library/browse?limit=200&after_id=c-901": {
    ok: true,
    rows: [
      { id: 900, title: "Old Item 900", tags: [{ id: 5, name: "needle-tag", color: null }] },
      { id: 899, title: "Old Item 899", file_path: "/lib/archive-2019/a.mp4", notes: "director-cut" },
    ],
    next_cursor: null,
  },
  "/api/library/browse?limit=200&q=Old%20Item%20900": {
    ok: true,
    rows: [{ id: 900, title: "Old Item 900", tags: [{ id: 5, name: "needle-tag", color: null }] }],
  },
  "/api/library/browse?limit=200&q=needle-tag": { ok: true, rows: [] },
  // Server q= also matches path/notes; the row's title does not contain the term.
  "/api/library/browse?limit=200&q=archive-2019": {
    ok: true,
    rows: [{ id: 899, title: "Old Item 899", file_path: "/lib/archive-2019/a.mp4", notes: "director-cut" }],
  },
  "/api/library/browse?limit=200&q=director-cut": {
    ok: true,
    rows: [{ id: 899, title: "Old Item 899", file_path: "/lib/archive-2019/a.mp4", notes: "director-cut" }],
  },
  "/api/library/browse?limit=200&tag=needle-tag": {
    ok: true,
    rows: [{ id: 900, title: "Old Item 900", tags: [{ id: 5, name: "needle-tag", color: null }] }],
  },
};

function listTitles() {
  return within(within(screen.getByRole("main")).getByRole("list"))
    .getAllByRole("button", { name: /Item \d+|^x+$/ })
    .map((b) => b.textContent);
}

beforeEach(() => {
  window.localStorage.clear();
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
});

describe("sltm-fix Library: items beyond the first page", () => {
  it("F1 search finds an item older than the loaded page by title", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "New Item 902" });
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "Old Item 900");
    expect(await screen.findByRole("button", { name: "Old Item 900" })).toBeInTheDocument();
    expect(screen.queryByText("No items match")).toBeNull();
  });

  it("F1 search finds an older item by its exact tag", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "New Item 902" });
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "needle-tag");
    expect(await screen.findByRole("button", { name: "Old Item 900" })).toBeInTheDocument();
  });

  it("F1 search keeps an older item the server matched by path", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "New Item 902" });
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "archive-2019");
    expect(await screen.findByRole("button", { name: "Old Item 899" })).toBeInTheDocument();
  });

  // cx166 R1: once Load more consumes the last cursor the server search stops;
  // the same query must keep matching the same row by path or notes.
  for (const term of ["archive-2019", "director-cut"]) {
    it(`R1 a ${term === "archive-2019" ? "path" : "notes"} match survives loading the final page`, async () => {
      installApiFixtures(apiGetMock, apiPostMock, PAGED);
      const user = userEvent.setup();
      renderWired(<Library />, "/library");
      await screen.findByRole("button", { name: "New Item 902" });
      await user.type(screen.getByRole("searchbox", { name: "Search library" }), term);
      expect(await screen.findByRole("button", { name: "Old Item 899" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Load more" }));
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: "Load more" })).toBeNull(),
      );
      expect(await screen.findByRole("button", { name: "Old Item 899" })).toBeInTheDocument();
      expect(screen.queryByText("No items match")).toBeNull();
    });
  }

  it("R1 control: a title match also survives loading the final page", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "New Item 902" });
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "Old Item 900");
    expect(await screen.findByRole("button", { name: "Old Item 900" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("button", { name: "Old Item 900" })).toBeInTheDocument();
  });

  it("F1 says the list is partial and Load more appends the next page", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "New Item 902" });
    expect(screen.getByText(/Showing the newest 2 items/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("button", { name: "Old Item 900" })).toBeInTheDocument();
    expect(calledPaths(apiGetMock)).toContain("/api/library/browse?limit=200&after_id=c-901");
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(listTitles()).toEqual(["New Item 902", LONG_TITLE, "Old Item 900", "Old Item 899"]);
  });

  it("negative control: a single complete page shows no partial hint and searches nothing remote", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {
      ...SIDE,
      "/api/library/browse?limit=200": { ok: true, rows: [{ id: 1, title: "Alpha 001" }] },
    });
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Alpha 001" });
    expect(screen.queryByText(/Showing the newest/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    await user.type(screen.getByRole("searchbox", { name: "Search library" }), "missing-title");
    expect(await screen.findByText("No items match")).toBeInTheDocument();
    expect(calledPaths(apiGetMock).filter((p) => p.includes("q=") || p.includes("tag="))).toEqual([]);
  });
});

describe("sltm-fix Library: load failure and long titles", () => {
  it("F3 a failed items load shows an error, not the empty state", async () => {
    installApiFixtures(apiGetMock, apiPostMock, SIDE);
    const impl = apiGetMock.getMockImplementation()!;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
      p.startsWith("/api/library/browse")
        ? Promise.reject(new Error("HTTP 500 database is locked"))
        : impl(p, ...rest),
    );
    renderWired(<Library />, "/library");
    expect(await screen.findByText(/Couldn.t load library items/)).toBeInTheDocument();
    expect(screen.getByText(/Couldn.t load library items/).closest("[role=alert]")).not.toBeNull();
    expect(screen.queryByText("No library items.")).toBeNull();
  });

  it("negative control: a real empty library still shows the empty state and no alert", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {
      ...SIDE,
      "/api/library/browse?limit=200": { ok: true, rows: [] },
    });
    renderWired(<Library />, "/library");
    expect(await screen.findByText("No library items.")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn.t load library items/)).toBeNull();
  });

  it("F2 a long title can wrap: its row text may shrink and break anywhere", async () => {
    installApiFixtures(apiGetMock, apiPostMock, PAGED);
    renderWired(<Library />, "/library");
    const title = await screen.findByRole("button", { name: LONG_TITLE });
    expect(title.className).toContain("[overflow-wrap:anywhere]");
    expect(title.parentElement?.className).toContain("min-w-0");
  });
});
