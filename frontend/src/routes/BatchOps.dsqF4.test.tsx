import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BatchOps } from "./BatchOps";

// dsq-batch-ops F4 (deep-spa-sites-queue-history): BatchOps executed a delete on a filter that
// was never previewed (preview site-a, clear the site, Execute still enabled -> all sites), and
// "1e2" passed the integer check and was sent as 1. Execute now acts only on the previewed
// filter, and a number field takes digits only (anything else blocks Preview and Execute).

let fetchMock: ReturnType<typeof vi.fn>;

function posts(route: string) {
  return fetchMock.mock.calls
    .filter(([url]) => String(url).includes(route))
    .map(([, init]) => JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")));
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/batch-ops"]}>
        <BatchOps />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const executeDelete = () => screen.getByRole("button", { name: "Execute delete" });
const deleteRow = () => executeDelete().parentElement!;
const previewDelete = () => within(deleteRow()).getByRole("button", { name: "Preview (dry run)" });
const field = (ph: string) => screen.getByPlaceholderText(ph);

beforeEach(() => {
  fetchMock = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify({ ok: true, candidates_matched: 3, processed: 0, csrf_token: "t" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
});

describe("BatchOps executes only the previewed filter (dsq-batch-ops F4)", () => {
  it("editing the filter after a preview disables Execute delete until a new preview", async () => {
    mount();
    fireEvent.change(field("site_id (optional)"), { target: { value: "site-a" } });
    fireEvent.click(previewDelete());
    await waitFor(() => expect(executeDelete()).toBeEnabled());
    fireEvent.change(field("site_id (optional)"), { target: { value: "" } });
    expect(executeDelete(), "Execute delete stayed enabled for a filter that was never previewed").toBeDisabled();
    fireEvent.click(previewDelete());
    await waitFor(() => expect(executeDelete()).toBeEnabled());
  });

  it("CONTROL: the live delete posts exactly the previewed filter", async () => {
    mount();
    fireEvent.change(field("site_id (optional)"), { target: { value: "site-a" } });
    fireEvent.change(field("older_than_days"), { target: { value: "30" } });
    fireEvent.click(previewDelete());
    await waitFor(() => expect(executeDelete()).toBeEnabled());
    fireEvent.click(executeDelete());
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, proceed" }));
    await waitFor(() => expect(posts("/api/batch/delete").filter((b) => b.dry_run === false)).toHaveLength(1));
    const live = posts("/api/batch/delete").find((b) => b.dry_run === false)!;
    expect(live.filter).toEqual({ site_id: "site-a", older_than_days: 30, limit: 500 });
  });

  it('"1e2" is not a whole number: Preview and Execute are blocked, nothing is sent as 1', async () => {
    mount();
    fireEvent.change(field("older_than_days"), { target: { value: "1e2" } });
    expect(previewDelete()).toBeDisabled();
    expect(executeDelete()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("older_than_days must be a whole number");
    fireEvent.change(field("older_than_days"), { target: { value: "" } });
    fireEvent.change(field("limit"), { target: { value: "1e2" } });
    expect(previewDelete()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("limit must be a whole number");
    expect(posts("/api/batch/delete").some((b) => b.filter?.older_than_days === 1 || b.filter?.limit === 1)).toBe(false);
  });

  it("r2 (cx219 R1): an invalid age blocks Move -- never a move with the age filter dropped", async () => {
    mount();
    fireEvent.change(field("older_than_days"), { target: { value: "1e2" } });
    fireEvent.change(screen.getByLabelText("target directory"), { target: { value: "/x/moved" } });
    const moveRow = screen.getByRole("button", { name: "Execute move" }).parentElement!;
    expect(within(moveRow).getByRole("button", { name: "Preview (dry run)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Execute move" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Execute move" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(posts("/api/batch/move"), "a move was posted with an invalid age field").toHaveLength(0);
  });

  it("r2 CONTROL: a valid age reaches the move preview", async () => {
    mount();
    fireEvent.change(field("older_than_days"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("target directory"), { target: { value: "/x/moved" } });
    const moveRow = screen.getByRole("button", { name: "Execute move" }).parentElement!;
    fireEvent.click(within(moveRow).getByRole("button", { name: "Preview (dry run)" }));
    await waitFor(() => expect(posts("/api/batch/move")).toHaveLength(1));
    expect(posts("/api/batch/move")[0]).toMatchObject({ dry_run: true, filter: { older_than_days: 30 } });
  });

  it("r2: a digit string past 2^53 is not a whole number (it would be sent as null)", async () => {
    mount();
    fireEvent.change(field("older_than_days"), { target: { value: "9".repeat(400) } });
    expect(previewDelete()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("older_than_days must be a whole number");
  });

  it("r2: an invalid limit blocks the duplicate scan", async () => {
    mount();
    fireEvent.change(field("limit"), { target: { value: "1e2" } });
    expect(screen.getByRole("button", { name: "Scan for duplicates" })).toBeDisabled();
    fireEvent.change(field("limit"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan for duplicates" }));
    await waitFor(() => expect(posts("/api/batch/dedup_scan")).toHaveLength(1));
    expect(posts("/api/batch/dedup_scan")[0].limit).toBe(100);
  });

  it("editing the filter after a retry preview disables Execute retry", async () => {
    mount();
    const card = screen.getByRole("heading", { name: "Batch retry" }).parentElement!;
    const execute = within(card).getByRole("button", { name: "Execute retry" });
    fireEvent.click(within(card).getByRole("button", { name: "Preview (dry run)" }));
    await waitFor(() => expect(execute).toBeEnabled());
    fireEvent.change(field("status (optional)"), { target: { value: "failed" } });
    expect(execute, "Execute retry stayed enabled for a filter that was never previewed").toBeDisabled();
  });
});
