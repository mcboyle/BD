import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BatchOps } from "./BatchOps";

// AUDIT-13 (o1671-a13): "Execute retry" POSTed /api/batch/retry with dry_run:false on a
// single click, against the page contract ("Nothing fires on a single click"). The live
// retry now sits behind a confirm dialog, like batch move/delete -- on top of the F027
// gate (Execute stays disabled until a preview matched rows).

let fetchMock: ReturnType<typeof vi.fn>;

function liveRetryPosts() {
  return fetchMock.mock.calls.filter(([url, init]) => {
    if (!String(url).includes("/api/batch/retry")) return false;
    const body = JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}"));
    return body.dry_run === false;
  });
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

// Preview inside the Batch retry card, then wait for the F027 gate to enable Execute retry.
async function previewRetry() {
  const card = screen.getByRole("heading", { name: "Batch retry" }).parentElement!;
  fireEvent.click(within(card).getByRole("button", { name: "Preview (dry run)" }));
  const execute = within(card).getByRole("button", { name: "Execute retry" });
  await waitFor(() => expect(execute).toBeEnabled());
  return execute;
}

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

describe("BatchOps Execute retry is confirm-gated (o1671-a13)", () => {
  it("a single click opens a confirm dialog and sends no live retry", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("reset to status"), { target: { value: "queued" } });
    fireEvent.click(await previewRetry());
    await new Promise((r) => setTimeout(r, 50));
    expect(liveRetryPosts(), "live /api/batch/retry fired on a single click").toHaveLength(0);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/queued/)).toBeInTheDocument();
  });

  it("cancel sends nothing; confirm sends exactly one live retry", async () => {
    mount();
    const execute = await previewRetry();
    fireEvent.click(execute);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(liveRetryPosts()).toHaveLength(0);

    fireEvent.click(execute);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Requeue rows" }));
    await waitFor(() => expect(liveRetryPosts()).toHaveLength(1));
    const body = JSON.parse(String((liveRetryPosts()[0][1] as RequestInit).body));
    expect(body).toMatchObject({ dry_run: false, reset_to_status: "pending" });
  });
});
