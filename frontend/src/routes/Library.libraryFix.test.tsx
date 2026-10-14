// library-fix r1 (bughunt deep-spa-library-templates-mobile F2 F4 F5 F6 F8 F10 F11).
//
// F2: six Library reads rendered a failed load exactly like an empty/idle one.
// F4: list rows did not wrap, so ordinary titles pushed row controls off a
// 320 px screen (jsdom has no layout: the wrap contract is pinned here; rig
// probe L22 is the measured receipt).
// F5: at a 200% text size long button labels ran off a 390 px screen (rig
// probe L25 is the measured receipt; AppShell/LiveSection remainders are NOTEs).
// F6: the Tags card rendered every tag (5 000 tags -> 50 000 nodes).
// F6b (r2): a typed filter lost its box when a refetch dropped the count to
// the window, leaving 0 rows and no way back until remount.
// F8: a stored rating that is not the number 1-5 read as "none".
// F10: the bulk id box rounded a 19-digit id, kept duplicates and dropped
// invalid tokens without notice.
// F11: a repeated item id left a ghost row after a refetch.
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
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const BROWSE = "/api/library/browse?limit=200";
const base = (over: Record<string, unknown> = {}) => ({
  [BROWSE]: { ok: true, rows: [{ id: 1, title: "Alpha 001" }] },
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": { ok: true, stats: { a: 1, b: 2 } },
  "/api/scene_score/bottom?limit=20": { ok: true, scenes: [] },
  "/api/tags/rows/sometag?limit=100": { ok: true, rows: [] },
  "/api/tags/suggest/5": { ok: true, suggested: [] },
  "/api/live/status": { ok: true, available: false },
  "/api/live/recordings": { ok: true, recordings: [] },
  ...over,
});

function serve(fixtures: Record<string, unknown>, failing: string[] = []) {
  installApiFixtures(apiGetMock, apiPostMock, fixtures);
  const impl = apiGetMock.getMockImplementation()!;
  apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
    failing.includes(p) ? Promise.reject(new Error("HTTP 500 LF-boom")) : impl(p, ...rest),
  );
}

function card(heading: string) {
  return screen.getByRole("heading", { name: heading }).parentElement!;
}

beforeEach(() => {
  window.localStorage.clear();
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
});

describe("library-fix F2 a failed read is not shown as empty/idle", () => {
  it("tags, scan status, scene scores and stats show their failure", async () => {
    serve(base(), ["/api/library/tags", "/api/library/scan/status", "/api/scene_score/bottom?limit=20", "/api/library/stats"]);
    renderWired(<Library />, "/library");
    await waitFor(() => expect(screen.queryByText(/Couldn.t load tags: HTTP 500 LF-boom/), "LF-F2-TAGS").not.toBeNull());
    expect(screen.queryByText("No tags."), "LF-F2-TAGS-EMPTY").toBeNull();
    await waitFor(() => expect(screen.queryByText(/scan: status unavailable \(HTTP 500 LF-boom\)/), "LF-F2-SCAN").not.toBeNull());
    expect(screen.queryByText("scan: idle"), "LF-F2-SCAN-IDLE").toBeNull();
    await waitFor(() => expect(screen.queryByText(/Couldn.t load scene scores: HTTP 500 LF-boom/), "LF-F2-SCENE").not.toBeNull());
    expect(screen.queryByText("No scored scenes."), "LF-F2-SCENE-EMPTY").toBeNull();
    await waitFor(() => expect(screen.queryByText(/Couldn.t load library stats: HTTP 500 LF-boom/), "LF-F2-STATS").not.toBeNull());
  });

  it("rows-with-tag and tag suggestions show their failure", async () => {
    serve(base(), ["/api/tags/rows/sometag?limit=100", "/api/tags/suggest/5"]);
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await user.type(await screen.findByPlaceholderText("Rows with tag…"), "sometag");
    await user.type(screen.getByPlaceholderText("Suggest tags for history id…"), "5");
    await waitFor(() => expect(screen.queryByText(/Couldn.t load rows tagged "sometag": HTTP 500 LF-boom/), "LF-F2-TAGROWS").not.toBeNull());
    expect(screen.queryByText('0 rows tagged "sometag"'), "LF-F2-TAGROWS-ZERO").toBeNull();
    await waitFor(() => expect(screen.queryByText(/Couldn.t load suggestions: HTTP 500 LF-boom/), "LF-F2-SUGGEST").not.toBeNull());
    expect(screen.queryByText("no suggestions"), "LF-F2-SUGGEST-NONE").toBeNull();
  });

  it("scan status: a failure stays shown while the 5 s refetch is pending, and unread is not idle", async () => {
    serve(base());
    const impl = apiGetMock.getMockImplementation()!;
    let scanCalls = 0;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) => {
      if (p !== "/api/library/scan/status") return impl(p, ...rest);
      scanCalls += 1;
      return scanCalls === 1 ? Promise.reject(new Error("HTTP 500 LF-boom")) : new Promise(() => {});
    });
    renderWired(<Library />, "/library");
    await waitFor(() => expect(screen.queryByText(/scan: status unavailable \(HTTP 500 LF-boom\)/), "LF-F2-SCAN").not.toBeNull());
    await waitFor(() => expect(scanCalls).toBe(2), { timeout: 7000 });
    expect(screen.queryByText("scan: idle"), "LF-F2-SCAN-IDLE-ON-REFETCH").toBeNull();
    const line = screen.queryByText(/^scan: status unavailable/);
    expect(line, "LF-F2-SCAN-FAIL-LOST-ON-REFETCH").not.toBeNull();
    expect(line!.getAttribute("role"), "LF-F2-SCAN-NOT-ALERT").toBe("alert");
  }, 12_000);

  it("scan status: a read still in flight is not shown as idle", async () => {
    serve(base());
    const impl = apiGetMock.getMockImplementation()!;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
      p === "/api/library/scan/status" ? new Promise(() => {}) : impl(p, ...rest),
    );
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Alpha 001" });
    expect(screen.queryByText("scan: idle"), "LF-F2-SCAN-IDLE-UNREAD").toBeNull();
    expect(screen.getByText("scan: status loading…")).toBeInTheDocument();
  });

  it("control: real empty/idle answers keep their empty/idle text", async () => {
    serve(base());
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    expect(await screen.findByText("No tags.")).toBeInTheDocument();
    expect(screen.getByText("scan: idle")).toBeInTheDocument();
    expect(await screen.findByText("No scored scenes.")).toBeInTheDocument();
    expect(await screen.findByText("stats: 2 dimensions loaded")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("Rows with tag…"), "sometag");
    await user.type(screen.getByPlaceholderText("Suggest tags for history id…"), "5");
    expect(await screen.findByText('0 rows tagged "sometag"')).toBeInTheDocument();
    expect(await screen.findByText("no suggestions")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn.t load|status unavailable/)).toBeNull();
  });
});

describe("library-fix F4 list rows wrap on a narrow screen", () => {
  it("a list row and its control group may wrap", async () => {
    serve(base({ [BROWSE]: { ok: true, rows: [{ id: 1, title: "Ordinary title number 1" }] } }));
    renderWired(<Library />, "/library");
    const title = await screen.findByRole("button", { name: "Ordinary title number 1" });
    const li = title.closest("li")!;
    expect(li.className, "LF-F4-ROW-NOWRAP").toMatch(/(^| )flex-wrap( |$)/);
    const controls = within(li).getByTitle("Rating").parentElement!;
    expect(controls.className, "LF-F4-CONTROLS-NOWRAP").toMatch(/(^| )flex-wrap( |$)/);
  });
});

describe("library-fix F5 long labels wrap at a large text size", () => {
  it("the long Library buttons and the Items header may wrap", async () => {
    serve(base());
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "Alpha 001" });
    const rotate = screen.getByRole("button", { name: /Rotate stream secret/ }).className;
    expect(rotate, "LF-F5-NOWRAP rotate").toMatch(/(^| )whitespace-normal( |$)/);
    expect(rotate, "LF-F5-FIXED-HEIGHT rotate").not.toMatch(/(^| )h-10( |$)/);
    // The NFO pair keeps its button markup (tracked mutant anchors); its row wraps them.
    for (const name of [/Preview regen \(dry run\)/, /Regen missing NFOs/]) {
      const row = screen.getByRole("button", { name }).parentElement!.className;
      expect(row, `LF-F5-NOWRAP ${name}`).toContain("[&>button]:whitespace-normal");
      expect(row, `LF-F5-FIXED-HEIGHT ${name}`).toContain("[&>button]:h-auto");
    }
    const head = screen.getByRole("heading", { name: "Items" }).parentElement!;
    expect(head.className, "LF-F5-ITEMS-HEAD-NOWRAP").toMatch(/(^| )flex-wrap( |$)/);
  });
});

describe("library-fix F6 the Tags card renders a window, not every tag", () => {
  it("5 000 tags render at most 200 rows, and the filter reaches the rest", async () => {
    const tags = Array.from({ length: 5000 }, (_, i) => ({ id: i + 1, name: `tag-${i + 1}` }));
    serve(base({ "/api/library/tags": { ok: true, tags } }));
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByText("tag-1");
    const tagsCard = card("Tags");
    expect(within(tagsCard).getAllByRole("listitem").length, "LF-F6-ALL-TAGS-RENDERED").toBeLessThanOrEqual(200);
    expect(within(tagsCard).getByText(/Showing 200 of 5000 tags/)).toBeInTheDocument();
    await user.type(within(tagsCard).getByRole("searchbox", { name: "Filter tags" }), "tag-4999");
    await waitFor(() => expect(within(tagsCard).queryByText("tag-4999"), "LF-F6-FILTER").not.toBeNull());
    expect(within(tagsCard).getAllByRole("listitem").length).toBe(1);
  });

  it("control: a short tag list renders whole with no filter", async () => {
    serve(base({ "/api/library/tags": { ok: true, tags: [{ id: 1, name: "one" }, { id: 2, name: "two" }] } }));
    renderWired(<Library />, "/library");
    await screen.findByText("two");
    expect(within(card("Tags")).getAllByRole("listitem").length).toBe(2);
    expect(screen.queryByRole("searchbox", { name: "Filter tags" })).toBeNull();
  });
});

describe("library-fix F6b a typed tag filter stays reachable after a refetch", () => {
  function tagList(n: number) {
    return { ok: true, tags: Array.from({ length: n }, (_, i) => ({ id: i + 1, name: `tag-${i + 1}` })) };
  }

  async function filterThenDelete(before: number, filter: string, victim: string) {
    let page = tagList(before);
    serve(base());
    const impl = apiGetMock.getMockImplementation()!;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
      p === "/api/library/tags" ? Promise.resolve(page) : impl(p, ...rest),
    );
    apiDeleteMock.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await screen.findByText("tag-1");
    const tagsCard = card("Tags");
    await user.type(within(tagsCard).getByRole("searchbox", { name: "Filter tags" }), filter);
    const row = await within(tagsCard).findByText(victim);
    page = { ok: true, tags: page.tags.filter((t) => `tag-${t.id}` !== victim) };
    await user.click(within(row.closest("li")!).getByRole("button", { name: "Delete tag" }));
    await user.click(await screen.findByRole("button", { name: "Yes, proceed" }));
    await waitFor(() => expect(apiGetMock.mock.calls.filter(([p]) => p === "/api/library/tags").length).toBe(2));
    await waitFor(() => expect(within(tagsCard).queryByText(victim)).toBeNull());
    return { tagsCard, user };
  }

  it("201 -> filter -> delete the match -> 200: the filter box stays and clearing it shows all 200", async () => {
    const { tagsCard, user } = await filterThenDelete(201, "tag-201", "tag-201");
    const box = within(tagsCard).queryByRole("searchbox", { name: "Filter tags" });
    expect(box, "LF-F6B-FILTER-HIDDEN").not.toBeNull();
    expect((box as HTMLInputElement).value).toBe("tag-201");
    await user.clear(box!);
    await waitFor(() => expect(within(tagsCard).queryAllByRole("listitem").length, "LF-F6B-TAGS-UNREACHABLE").toBe(200));
    expect(within(tagsCard).queryByText(/Showing \d+ of/)).toBeNull();
  });

  it("control: 202 -> 201 keeps the filter box over the window", async () => {
    const { tagsCard } = await filterThenDelete(202, "tag-202", "tag-202");
    expect(within(tagsCard).getByRole("searchbox", { name: "Filter tags" })).toBeInTheDocument();
  });
});

describe("library-fix F8 the rating select shows the stored rating", () => {
  it("0, 7, 3.5, \"4\", -1 and 3 each read as stored; rendering writes nothing", async () => {
    const data = [0, 7, 3.5, "4", -1, 3];
    serve(base({ [BROWSE]: { ok: true, rows: data.map((r, i) => ({ id: i + 1, title: `r${i}`, rating: r })) } }));
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "r5" });
    const shown = screen.getAllByTitle("Rating").map((s) => (s as HTMLSelectElement).value);
    expect(shown, "LF-F8-RATING-READS-NONE").toEqual(["0", "7", "3.5", "4", "-1", "3"]);
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("the item details dialog shows a stored \"4\", not none", async () => {
    serve(base({ [BROWSE]: { ok: true, rows: [{ id: 1, title: "four", rating: "4" }] } }));
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await user.click(await screen.findByRole("button", { name: "four" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Rating").nextElementSibling?.textContent, "LF-F8-PREVIEW-NONE").toBe("4");
  });

  it("control: no stored rating reads none", async () => {
    serve(base({ [BROWSE]: { ok: true, rows: [{ id: 1, title: "n", rating: null }] } }));
    renderWired(<Library />, "/library");
    await screen.findByRole("button", { name: "n" });
    expect((screen.getByTitle("Rating") as HTMLSelectElement).value).toBe("");
  });
});

describe("library-fix F10 bulk id box", () => {
  it("names the tokens that are not ids and sends nothing until they are fixed", async () => {
    serve(base());
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    const box = await screen.findByPlaceholderText("History ids (comma/space separated)");
    await user.type(box, "1, 2 abc -3 1e3 9999999999999999999 ,, 2 0x10 3.7");
    const alert = await screen.findByText(/Not history ids/).catch(() => null);
    expect(alert?.textContent, "LF-F10-SILENT-DROP").toBe(
      "Not history ids (fix them first): abc -3 1e3 9999999999999999999 0x10 3.7",
    );
    expect((screen.getByRole("button", { name: "Load row tags" }) as HTMLButtonElement).disabled, "LF-F10-SENDS-PARTIAL").toBe(true);
    await user.clear(box);
    await user.type(box, "1, 2 9007199254740991 ,, 2");
    await user.click(screen.getByRole("button", { name: "Load row tags" }));
    await waitFor(() =>
      expect(apiPostMock, "LF-F10-IDS-SENT").toHaveBeenCalledWith("/api/tags/for_many", {
        history_ids: [1, 2, 9007199254740991],
      }),
    );
  });

  it("control: plain ids enable the action with no notice", async () => {
    serve(base());
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await user.type(await screen.findByPlaceholderText("History ids (comma/space separated)"), "3 4");
    expect(screen.getByRole("button", { name: "Load row tags" })).toBeEnabled();
    expect(screen.queryByText(/Not history ids/)).toBeNull();
  });
});

describe("library-fix F11 a repeated item id leaves no ghost row", () => {
  it("after a refetch without the repeated id, its rows are gone", async () => {
    let page = { ok: true, rows: [{ id: 7, title: "dup-A" }, { id: 7, title: "dup-B" }, { id: 8, title: "six" }] };
    serve(base());
    const impl = apiGetMock.getMockImplementation()!;
    apiGetMock.mockImplementation((p: string, ...rest: unknown[]) =>
      p === BROWSE ? Promise.resolve(page) : impl(p, ...rest),
    );
    apiDeleteMock.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    const six = await screen.findByRole("button", { name: "six" });
    page = { ok: true, rows: [{ id: 8, title: "six" }] };
    await user.click(within(six.closest("li")!).getByRole("button", { name: "Delete" }));
    await user.click(await screen.findByRole("button", { name: "Yes, proceed" }));
    await waitFor(() => expect(apiGetMock.mock.calls.filter(([p]) => p === BROWSE).length).toBe(2));
    await waitFor(() => expect(screen.queryByText("dup-A"), "LF-F11-GHOST-ROW").toBeNull());
    expect(screen.getAllByTitle("Show details").length).toBe(1);
  });
});
