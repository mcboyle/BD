import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SemanticSearchPanel } from "@/components/ui/SemanticSearchPanel";

// v3.66.743 — the semantic CONTROL cluster gets a GUI (status, search, reindex).
//
// THE LOAD-BEARING FACT: search and reindex run happily against an EMPTY index
// and return ok:true with zero hits — indistinguishable in the UI from "no
// matches" unless `indexed` from /api/semantic/status is shown. A reindex
// button with no indexed-count readout is unknown laundered into OK. The
// status read is not decoration; it is the control's meaning.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/templates"]}>
        <SemanticSearchPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function json(body: unknown, ok = true, status?: number) {
  return Promise.resolve({
    ok,
    status: status ?? (ok ? 200 : 400),
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

const STATUS = { ok: true, enabled: true, indexed: 42, dims: 384, has_sqlite_vec: false };

function installFetch(
  calls: { url: string; init?: RequestInit }[],
  overrides: Record<string, unknown> = {},
  opts: { slowReindex?: boolean } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, init });
      if (url.startsWith("/api/semantic/status"))
        return json("status" in overrides ? overrides.status : STATUS);
      if (url.startsWith("/api/semantic/search") && method === "POST")
        return json(
          overrides["/api/semantic/search"] ?? { ok: true, hits: [], k: 10 },
        );
      if (url.startsWith("/api/semantic/reindex") && method === "POST") {
        if (opts.slowReindex)
          return new Promise(() => undefined) as Promise<Response>; // never resolves
        return json({ ok: true, indexed: 42 });
      }
      return json({ ok: true });
    },
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SemanticSearchPanel — the indexed readout is the control", () => {
  it("reads /api/semantic/status and SHOWS the indexed count", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    expect(await screen.findByText(/42 indexed/i)).toBeInTheDocument();
    expect(
      calls.some((c) => c.url.startsWith("/api/semantic/status")),
    ).toBe(true);
  });

  it("an EMPTY index is said out loud, not passed off as 'no matches'", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, {
      status: { ok: true, enabled: true, indexed: 0, dims: 384, has_sqlite_vec: false },
    });
    mount();
    expect(
      await screen.findByText(/index is empty/i),
    ).toBeInTheDocument();
  });
});

describe("SemanticSearchPanel — contracts", () => {
  it("search posts {query, k} in the BODY to the full literal path", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/42 indexed/i);

    await userEvent.type(
      screen.getByPlaceholderText(/semantic search/i),
      "login wall",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /^search$/i }),
    );

    await waitFor(() => {
      const post = calls.find((c) => c.url === "/api/semantic/search");
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post!.init!.body));
      expect(body.query).toBe("login wall");
      if ("k" in body) {
        expect(body.k).toBeGreaterThanOrEqual(1);
        expect(body.k).toBeLessThanOrEqual(50);
      }
    });
  });

  it("empty query never posts (the endpoint 400s on it)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/42 indexed/i);
    await userEvent.click(
      screen.getByRole("button", { name: /^search$/i }),
    );
    const posts = calls.filter(
      (c) => (c.init?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("reindex is single-fire: DISABLED while pending, so it cannot be double-fired", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, {}, { slowReindex: true });
    mount();
    await screen.findByText(/42 indexed/i);

    const btn = screen.getByRole("button", { name: /reindex/i });
    await userEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());

    await userEvent.click(btn).catch(() => undefined);
    const posts = calls.filter((c) => c.url === "/api/semantic/reindex");
    expect(posts).toHaveLength(1);
  });
});
