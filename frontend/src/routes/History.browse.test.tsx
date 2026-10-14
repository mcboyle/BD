// F003 -- /history: filter by site and status, paginate, open a row detail.
//
// The row's bar is "list loads; search, filter by site/status, sort, paginate,
// open a row detail". Search and sort are covered by History.endpoints.test.tsx
// and the SortHeader specs; this file drives the three behaviours the live
// probe found MISSING, through the transport the route really uses: every
// claim below is about a request path handed to apiGet or about rendered text,
// never about the component's source.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMock }));

import History from "@/routes/History";
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const LONG_MESSAGE =
  "HTTP 403 from origin after 3 attempts; the signed URL expired before the " +
  "second range request and the refresh endpoint answered with a login page";
const LONG_URL =
  "https://media.example.test/library/2026/10/episode-0042/source/original.mp4?sig=abcdef0123456789";

// Stored columns the table never shows, plus one this file's author never
// heard of: the detail is "every populated stored field", not a fixed list.
const DIAGNOSTICS = {
  honeypot_score: 0.625,
  bytes_fetched: 777,
  transfer_mode: "stream",
  egress_ip: "192.0.2.123",
  file_path: "/data/alpha/file-9.mp4",
  later_added_column: "value-from-a-newer-schema",
  snippet_message: "<mark>HTTP</mark> 403",
};

/** The <dd> text beside a <dt> label inside the open detail. */
const detailValue = (dialog: HTMLElement, label: string) =>
  within(dialog).getByText(label, { selector: "dt" }).nextElementSibling?.textContent;

const row = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  ts: `2026-10-13T05:${String(id % 60).padStart(2, "0")}:00`,
  site_id: "alpha",
  site_name: "Alpha Site",
  url: `https://alpha.example.test/item/${id}`,
  status: "done",
  filename: `file-${id}.mp4`,
  file_size: 1024,
  message: "",
  ...over,
});

// Ids descend, as `ORDER BY h.id DESC` returns them.
const FIRST_25 = Array.from({ length: 25 }, (_, i) => row(100 - i));
const OLDER_3 = [row(75), row(74), row(73)];

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/queue/v2": { waiting: [], running: [] },
  "/api/status?light=1": { alpha: { name: "Alpha Site" }, beta: { name: "Beta Site" } },
  "/api/history?limit=200": [
    row(9, { status: "failed", url: LONG_URL, message: LONG_MESSAGE, ...DIAGNOSTICS }),
    row(8, { honeypot_score: 0, egress_ip: null, transfer_mode: "" }),
    // A site no longer in the config: only its history rows name it.
    row(7, { site_id: "gone", site_name: "Gone Site", filename: "file-7.mp4" }),
  ],
  "/api/history?status=failed&limit=200": [
    row(9, { status: "failed", url: LONG_URL, message: LONG_MESSAGE }),
  ],
  "/api/history?site_id=beta&status=failed&limit=200": [],
  "/api/history?limit=25": FIRST_25,
  "/api/history?limit=25&cursor=76": { rows: OLDER_3, next_cursor: null },
  "/api/search?q=alpha&limit=100&status=failed": {
    results: [row(9, { status: "failed", snippet_filename: "<mark>alpha</mark>-hit.mp4" })],
    count: 1,
  },
};

const historyGets = () => calledPaths(apiGetMock).filter((p) => p.startsWith("/api/history"));

async function mounted() {
  renderWired(<History />, "/history");
  await waitFor(() => expect(historyGets()).toContain("/api/history?limit=200"));
  // PRECONDITION for every case: the fixture rows really rendered, so a
  // control that is only drawn beside a non-empty table can be found.
  await screen.findByText("file-9.mp4");
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("F003 history browse", () => {
  it("filters the list by status and by site on the server", async () => {
    const user = userEvent.setup();
    await mounted();
    // The site choices are the configured sites, by name.
    const site = screen.getByRole("combobox", { name: "Filter by site" });
    await waitFor(() =>
      expect(within(site).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "All sites",
        "Alpha Site",
        "Beta Site",
        "Gone Site",
      ]),
    );

    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "failed");
    await waitFor(() => expect(historyGets()).toContain("/api/history?status=failed&limit=200"));
    await waitFor(() => expect(screen.queryByText("file-8.mp4")).toBeNull());
    expect(screen.getByText("file-9.mp4")).toBeInTheDocument();

    await user.selectOptions(site, "beta");
    await waitFor(() =>
      expect(historyGets()).toContain("/api/history?site_id=beta&status=failed&limit=200"),
    );
    // A filter that matches nothing says so, and does not claim the history is empty.
    expect(await screen.findByText("No rows match the selected site and status.")).toBeInTheDocument();
    expect(screen.queryByText("No history yet.")).toBeNull();

    // NEGATIVE CONTROL: clearing both filters returns to the unfiltered request
    // and the unfiltered rows -- the filter narrowed the list, it did not break it.
    await user.selectOptions(site, "");
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "");
    expect(await screen.findByText("file-8.mp4")).toBeInTheDocument();
  }, 20000);

  it("the filters also ride the FTS request", async () => {
    const user = userEvent.setup();
    await mounted();
    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "failed");
    await user.type(screen.getByPlaceholderText(/Search history/), "alpha");
    await waitFor(
      () =>
        expect(calledPaths(apiGetMock)).toContain("/api/search?q=alpha&limit=100&status=failed"),
      { timeout: 5000 },
    );
    expect(await screen.findByText("alpha-hit.mp4")).toBeInTheDocument();
  }, 20000);

  it("pages older rows by cursor and comes back", async () => {
    const user = userEvent.setup();
    await mounted();
    const prev = screen.getByRole("button", { name: "Previous page" });
    const next = screen.getByRole("button", { name: "Next page" });
    // NEGATIVE CONTROL: three rows under a 200-row page is the whole list.
    expect(prev).toBeDisabled();
    expect(next).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "25");
    await waitFor(() => expect(historyGets()).toContain("/api/history?limit=25"));
    expect(await screen.findByText("file-100.mp4")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(26);
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled());

    // The cursor is the id of the LAST row of the page being left.
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(historyGets()).toContain("/api/history?limit=25&cursor=76"));
    expect(await screen.findByText("file-73.mp4")).toBeInTheDocument();
    expect(screen.queryByText("file-100.mp4")).toBeNull();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText(/Page 2 · 3 rows/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await screen.findByText("file-100.mp4")).toBeInTheDocument();
    expect(screen.queryByText("file-73.mp4")).toBeNull();
    expect(screen.getByText(/Page 1 · 25 rows/)).toBeInTheDocument();
  }, 20000);

  it("opens a row detail carrying the untruncated stored fields", async () => {
    const user = userEvent.setup();
    await mounted();
    // NEGATIVE CONTROL: nothing is open, and the URL is not on the page at all
    // (the table has no URL column), until a row is opened.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText(LONG_URL)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Open details for history row 9" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("History row 9")).toBeInTheDocument();
    expect(within(dialog).getByText(LONG_URL)).toBeInTheDocument();
    expect(within(dialog).getByText(LONG_MESSAGE)).toBeInTheDocument();
    expect(within(dialog).getByText("Alpha Site · alpha")).toBeInTheDocument();
    expect(within(dialog).getByText("failed")).toBeInTheDocument();
    // The OTHER row's file is not in this detail.
    expect(within(dialog).queryByText("file-8.mp4")).toBeNull();


    // F1 (lens cx120): EVERY populated field of the stored row is shown, the
    // diagnostics included -- and one the component has no label for.
    expect(detailValue(dialog, "Honeypot score")).toBe("0.625");
    expect(detailValue(dialog, "Bytes fetched")).toBe("777");
    expect(detailValue(dialog, "Transfer mode")).toBe("stream");
    expect(detailValue(dialog, "Egress ip")).toBe("192.0.2.123");
    expect(detailValue(dialog, "File path")).toBe("/data/alpha/file-9.mp4");
    expect(detailValue(dialog, "Later added column")).toBe("value-from-a-newer-schema");
    expect(detailValue(dialog, "Id")).toBe("9");
    // The raw byte count rides beside the formatted size.
    expect(detailValue(dialog, "Size")).toContain("1024");
    // COMPLETENESS, as a set: every populated key of the fixture row has a line.
    // snippet_* is the FTS lane's highlight markup, not a stored column.
    const shown = within(dialog).getAllByRole("term").length;
    const populated = Object.entries(FIXTURES["/api/history?limit=200"] as object[])
      .map(([, r]) => r as Record<string, unknown>)
      .filter((r) => r.id === 9)
      .flatMap((r) => Object.entries(r))
      .filter(([k, v]) => !k.startsWith("snippet_") && v !== null && v !== "").length;
    expect(populated).toBeGreaterThanOrEqual(15);
    // site_id and site_name share the one "Site" line.
    expect(shown).toBe(populated - 1);
    expect(within(dialog).queryByText(/mark>/)).toBeNull();

    await user.click(within(dialog).getByRole("button", { name: "Close details" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // A click anywhere on a row opens that row.
    await user.click(screen.getByText("file-8.mp4"));
    const second = await screen.findByRole("dialog");
    expect(within(second).getByText("History row 8")).toBeInTheDocument();
    expect(within(second).queryByText(LONG_URL)).toBeNull();
    // A stored 0 is a value and is shown; null and empty columns are not.
    expect(detailValue(second, "Honeypot score")).toBe("0");
    expect(within(second).queryByText("Egress ip")).toBeNull();
    expect(within(second).queryByText("Transfer mode")).toBeNull();
  }, 20000);
});
