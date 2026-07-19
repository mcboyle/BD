import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SiteSettings } from "./SiteSettings";

// v3.66.468 WS4b: the per-site `backend` key (teach/jd/qb) is enum-typed, so
// SiteSettings must render it as a <select> of those choices (the JD GUI gap:
// it was free-text before, so JD couldn't be selected from the UI). jd_host /
// jd_port ride alongside as labelled string/integer fields.

const BACKEND_DESC = {
  key: "backend",
  category: "general",
  secret: false,
  preserve_on_blank: false,
  type: "enum",
  enum: ["teach", "jd", "qb"],
  description: "Download backend for this site",
  range: null,
  required: false,
  current: "teach",
};

const EDITABLE = {
  ok: true,
  sid: "demo",
  field_meta: { backend: BACKEND_DESC },
  groups: { general: [BACKEND_DESC] },
  gated_meta: {},
  gated_groups: {},
};

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/editable")) return Promise.resolve(jsonResponse(EDITABLE));
    if (url.includes("/api/vpn/tunnels")) return Promise.resolve(jsonResponse({ ok: true, tunnels: [] }));
    return Promise.resolve(jsonResponse({ ok: true }));
  });
});

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/sites/demo/settings"]}>
        <Routes>
          <Route path="/sites/:siteId/settings" element={<SiteSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("SiteSettings backend enum", () => {
  it("renders backend as a select with teach/jd/qb options", async () => {
    mount();
    const select = (await screen.findByLabelText("backend")) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    const opts = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(opts).toEqual(["teach", "jd", "qb"]);
  });
});
