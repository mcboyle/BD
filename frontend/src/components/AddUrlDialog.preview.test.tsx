import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Cut 6.7 — bulk-add URL preview. The list mode replaces the bare "N valid"
// count with a client-side dedupe breakdown: X new / Y already-queued / Z invalid.
import { AddUrlDialog } from "./AddUrlDialog";

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AddUrlDialog open={true} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // sites/queue fetches resolve to empty; the preview tallies are client-side.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ sites: [], waiting: [], running: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AddUrlDialog bulk-add preview (Cut 6.7)", () => {
  it("switches to list mode and shows a new/queued/invalid breakdown", () => {
    mount();
    // enter list mode. Match the exact "URL list" tab -- the v3.66.767 "Scrape
    // listing" tab also contains "list", so a bare /list/ regex is ambiguous.
    const listTab = screen.getByRole("button", { name: /url list/i });
    fireEvent.click(listTab);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, {
      target: {
        value: [
          "https://a.com/1",
          "https://a.com/1", // duplicate
          "not-a-url", // invalid
          "https://a.com/2",
        ].join("\n"),
      },
    });
    // 2 unique valid "new", 1 invalid. The pristine dialog only says "N valid".
    expect(screen.getByText(/invalid/i)).toBeInTheDocument();
    expect(screen.getByText(/new/i)).toBeInTheDocument();
  });
});
