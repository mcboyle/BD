import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SiteSearchPanel } from "@/components/ui/SiteSearchPanel";

// v3.66.743 — the search CONTROL cluster gets a GUI (4 of the 7: sites_available,
// search/site, search/all; facets rides History).
//
// THE LOAD-BEARING FACT of this cluster: both search endpoints gate on
// capability and DEGRADE AT HTTP 200, not 4xx —
//     if not (_SEARCH_AVAILABLE and _search_mod): return {"ok": False,
//         "error": "search_extractor unavailable"}          # <- 200!
// On a box without search_extractor every POST "succeeds" with ok:false. A
// panel that fires first and reads the error later is a form that posts into
// the dark. The control that matters is the READ: the panel consumes
// /api/search/sites_available (which carries the same guard) and DISABLES,
// never firing a doomed POST. The negative-control test pins exactly that.
//
// SHADOW GUARD: /api/search (GET, args) is HISTORY FTS — a different job
// (History.tsx already wires it). This panel must never call it.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/queue"]}>
        <SiteSearchPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function json(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 400,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

const AVAILABLE = {
  ok: true,
  available: [
    { site_id: "alpha", name: "Alpha" },
    { site_id: "beta", name: "Beta" },
  ],
  count: 2,
  total_sites: 5,
};

const UNAVAILABLE = { ok: false, error: "search_extractor unavailable" };

function installFetch(
  calls: { url: string; init?: RequestInit }[],
  overrides: Record<string, unknown> = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, init });
      if (url.startsWith("/api/search/sites_available"))
        return json("sites_available" in overrides ? overrides.sites_available : AVAILABLE);
      if (url.startsWith("/api/search/all") && method === "POST")
        return json(
          overrides["/api/search/all"] ?? {
            ok: true, query: "q", results: {}, stats: { total_hits: 0 },
          },
        );
      if (url.startsWith("/api/search/site") && method === "POST")
        return json(
          overrides["/api/search/site"] ?? {
            ok: true, site_id: "alpha", query: "q", hits: [],
          },
        );
      return json({ ok: true });
    },
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SiteSearchPanel — capability gate (the negative control)", () => {
  it("reads sites_available and DISABLES when the extractor is absent — no POST fired", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, { sites_available: UNAVAILABLE });
    mount();

    // the disabled state is rendered, verbatim enough to recognize
    expect(
      await screen.findByText(/search extractor not installed/i),
    ).toBeInTheDocument();

    const btn = screen.getByRole("button", { name: /search all sites/i });
    expect(btn).toBeDisabled();

    // belt AND suspenders: even a forced click posts nothing
    await userEvent.click(btn).catch(() => undefined);
    const posts = calls.filter(
      (c) => (c.init?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("shows 'search N of your M sites' from the read when available", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    expect(
      await screen.findByText(/search 2 of your 5 sites/i),
    ).toBeInTheDocument();
  });
});

describe("SiteSearchPanel — contracts (query/body, the SiteActions trap)", () => {
  it("search/all: query rides the BODY; `sites` when present is a LIST", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/search 2 of your 5 sites/i);

    await userEvent.type(
      screen.getByPlaceholderText(/search your sites/i),
      "cats",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /search all sites/i }),
    );

    await waitFor(() => {
      const post = calls.find((c) => c.url === "/api/search/all");
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post!.init!.body));
      expect(body.query).toBe("cats");
      if ("sites" in body) expect(Array.isArray(body.sites)).toBe(true);
    });
  });

  it("search/site: {site_id, query} in the body, POSTed to the full literal path", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/search 2 of your 5 sites/i);

    await userEvent.selectOptions(
      screen.getByLabelText(/site/i),
      "alpha",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/search your sites/i),
      "dogs",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /search site/i }),
    );

    await waitFor(() => {
      const post = calls.find((c) => c.url === "/api/search/site");
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post!.init!.body));
      expect(body.site_id).toBe("alpha");
      expect(body.query).toBe("dogs");
    });
  });

  it("empty query never posts (both endpoints reject it at 200-ok:false)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/search 2 of your 5 sites/i);

    await userEvent.click(
      screen.getByRole("button", { name: /search all sites/i }),
    );
    const posts = calls.filter(
      (c) => (c.init?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("SHADOW GUARD: never calls history /api/search (GET-args family)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    mount();
    await screen.findByText(/search 2 of your 5 sites/i);
    await userEvent.type(
      screen.getByPlaceholderText(/search your sites/i),
      "cats",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /search all sites/i }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/search/all")).toBe(true),
    );
    const historyCalls = calls.filter(
      (c) =>
        c.url === "/api/search" || c.url.startsWith("/api/search?"),
    );
    expect(historyCalls).toHaveLength(0);
  });

  it("ok:false-200 from a search surfaces as an error state, not silence", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, {
      "/api/search/all": { ok: false, error: "search_extractor unavailable" },
    });
    mount();
    await screen.findByText(/search 2 of your 5 sites/i);
    await userEvent.type(
      screen.getByPlaceholderText(/search your sites/i),
      "cats",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /search all sites/i }),
    );
    expect(
      await screen.findByText(/search_extractor unavailable/i),
    ).toBeInTheDocument();
  });
});
