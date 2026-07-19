import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SearchFacetsStrip } from "@/components/ui/SearchFacetsStrip";

// v3.66.743 — /api/search/facets gets its consumer. It is the read the
// History FTS search depends on (breakdown of matches by site and status)
// and was never CONTROL-classified: spa_wired=False since it shipped.
//
// CONTRACT: this is the GET-args family (query/site_id/status in
// request.args), matching its sibling /api/search — NOT the POST-body
// live-search family. The test pins the querystring shape.

function mount(q: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <SearchFacetsStrip q={q} />
    </QueryClientProvider>,
  );
}

function json(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

const FACETS = {
  ok: true,
  facets: {
    by_site: { alpha: 3, beta: 1 },
    by_status: { done: 4 },
    total: 4,
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SearchFacetsStrip", () => {
  it("fetches /api/search/facets with the query in the ARGS and renders the breakdown", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input: unknown) => {
      urls.push(String(input));
      return json(FACETS);
    });
    mount("cats");

    expect(await screen.findByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/3/)).toBeInTheDocument();

    const facetCalls = urls.filter((u) =>
      u.startsWith("/api/search/facets?"),
    );
    expect(facetCalls.length).toBeGreaterThan(0);
    expect(facetCalls[0]).toContain("query=cats");
    // args family: nothing rides a body on a GET
  });

  it("renders nothing (and fetches nothing) for an empty query", () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input: unknown) => {
      urls.push(String(input));
      return json(FACETS);
    });
    const { container } = mount("");
    expect(urls).toHaveLength(0);
    expect(container).toBeEmptyDOMElement();
  });
});
