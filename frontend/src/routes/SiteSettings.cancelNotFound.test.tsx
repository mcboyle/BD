import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SiteSettings } from "./SiteSettings";

// F009 (UIUX-20260928, agy-feat-26 r2): an in-progress edit had no Cancel --
// only a reload dropped it, silently -- and /sites/<unknown>/settings rendered
// the full editable form with Save, because the editable endpoint describes
// fields for any sid. The site is now looked up in GET /api/sites/v2.

const DELAY = {
  key: "delay",
  category: "download/perf",
  secret: false,
  preserve_on_blank: false,
  type: "integer",
  enum: null,
  description: "Delay between downloads",
  range: [0, 60],
  required: false,
  current: 3,
};

const EDITABLE = {
  ok: true,
  sid: "real-site",
  field_meta: { delay: DELAY },
  groups: { "download/perf": [DELAY] },
  gated_meta: {},
  gated_groups: {},
};

const SITES = { ok: true, count: 1, ts: 0, sites: [{ site_id: "real-site", name: "Real site" }] };

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

let writes: string[] = [];
let sitesRead = false;

beforeEach(() => {
  vi.restoreAllMocks();
  writes = [];
  sitesRead = false;
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method && init.method !== "GET") writes.push(`${init.method} ${url}`);
    if (url.includes("/editable")) return Promise.resolve(jsonResponse(EDITABLE));
    if (url.includes("/api/sites/v2")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => {
          sitesRead = true;
          return SITES;
        },
      } as unknown as Response);
    }
    if (url.includes("/api/vpn/tunnels")) return Promise.resolve(jsonResponse({ ok: true, tunnels: [] }));
    return Promise.resolve(jsonResponse({ ok: true }));
  });
});

function mount(siteId: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/sites/${siteId}/settings`]}>
        <Routes>
          <Route path="/sites/:siteId/settings" element={<SiteSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function unloadBlocked(): boolean {
  const ev = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(ev);
  return ev.defaultPrevented;
}

describe("SiteSettings cancel (F009)", () => {
  it("Cancel discards an in-progress edit without saving", async () => {
    mount("real-site");
    const input = (await screen.findByDisplayValue("3")) as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(save).toBeDisabled();
    expect(cancel).toBeDisabled();
    expect(unloadBlocked()).toBe(false);

    fireEvent.change(input, { target: { value: "9" } });
    expect(input.value).toBe("9");
    expect(save).toBeEnabled();
    expect(cancel).toBeEnabled();
    expect(screen.getByText("1 change(s) pending")).toBeInTheDocument();
    expect(unloadBlocked()).toBe(true);

    fireEvent.click(cancel);
    expect(input.value).toBe("3");
    expect(save).toBeDisabled();
    expect(cancel).toBeDisabled();
    expect(screen.queryByText(/change\(s\) pending/)).not.toBeInTheDocument();
    expect(unloadBlocked()).toBe(false);
    expect(writes).toEqual([]);
  });
});

describe("SiteSettings site lookup (F009)", () => {
  it("shows a not-found state, not an editable form, for an unknown site id", async () => {
    mount("nonexistent-site-test");
    expect(await screen.findByRole("alert")).toHaveTextContent("Site not found");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to sites/ })).toHaveAttribute("href", "/sites");
  });

  it("renders the editor for a known site id (positive control)", async () => {
    mount("real-site");
    expect(await screen.findByDisplayValue("3")).toBeInTheDocument();
    await waitFor(() => expect(sitesRead).toBe(true));
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.queryByText("Site not found")).not.toBeInTheDocument();
  });
});
