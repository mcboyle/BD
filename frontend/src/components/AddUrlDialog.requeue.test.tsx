import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

// dl95-dailymotion-6: re-adding a URL whose job FAILED said "0 added · 1 dupe" and nothing re-ran.
// The server now names such dupes (retryable_dupes); the dialog must say they are failed and offer
// Requeue, which posts them to the same bulk_retry the Queue page uses.
import { AddUrlDialog } from "./AddUrlDialog";

const SID = "s1";
const URL_FAILED = "https://example.com/video/failed-1";

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];
let addResult: Record<string, unknown> = {};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Toaster />
      <AddUrlDialog open={true} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      let payload: unknown = { ok: true };
      if (url.includes("/api/sites/v2")) payload = { sites: [{ site_id: SID, name: "Site One" }] };
      else if (url.includes("/api/queue/v2/add_url")) payload = addResult;
      else if (url.includes("/bulk_retry")) payload = { ok: true, retried: 1 };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function addSingle() {
  mount();
  await screen.findByRole("option", { name: /site one/i });
  fireEvent.change(screen.getByRole("textbox", { name: /^url$/i }), { target: { value: URL_FAILED } });
  fireEvent.click(screen.getByRole("button", { name: /^add url$/i }));
}

describe("AddUrlDialog failed-dupe requeue (dl95-dailymotion-6)", () => {
  it("says the dupe is a failed job and Requeue posts it to bulk_retry", async () => {
    addResult = { ok: true, site_id: SID, url: URL_FAILED, added: 0, dupes: 1, skipped: 0, retryable_dupes: [URL_FAILED] };
    await addSingle();
    expect(await screen.findByText(/1 failed — not re-added/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Requeue" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/bulk_retry"))).toBe(true));
    const retry = calls.find((c) => c.url.includes("/bulk_retry"))!;
    expect(retry.url).toContain(`/api/sites/${SID}/bulk_retry`);
    expect(retry.method).toBe("POST");
    expect(retry.body).toEqual({ urls: [URL_FAILED] });
    expect(await screen.findByText("1 requeued")).toBeInTheDocument();
  });

  it("a plain dupe (not failed) keeps the success toast and offers no Requeue", async () => {
    addResult = { ok: true, site_id: SID, url: URL_FAILED, added: 0, dupes: 1, skipped: 0, retryable_dupes: [] };
    await addSingle();
    expect(await screen.findByText(/0 added · 1 dupe$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Requeue" })).not.toBeInTheDocument();
  });
});
