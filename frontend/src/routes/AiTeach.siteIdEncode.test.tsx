import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AiTeach } from "./AiTeach";
import { fetchDriftStatus } from "@/lib/guidedDrift";

const ids = ["a/b?x=1", "plain-id"];
const repair = { old_selector: ".old", new_selector: ".new", role: "row_selectors", confidence: 90 };
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes("/api/csrf")) return Promise.resolve(response({ csrf_token: "fixture" }));
    if (path === "/api/sites/v2") return Promise.resolve(response({ ok: true, sites: ids.map(site_id => ({ site_id, name: site_id })) }));
    if (path === "/api/ai/diff_repair") return Promise.resolve(response({ ok: true, repairs: [repair], removed: [] }));
    if (path.endsWith("/learned/apply_repairs")) return Promise.resolve(response({ ok: true, count: 1, applied: [], removed: [], rejected: [] }));
    return Promise.resolve(response({ ok: true }));
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const cases = [
  ["a/b?x=1", "a%2Fb%3Fx%3D1"],
  ["plain-id", "plain-id"],
] as const;

describe("site ids remain one API path segment", () => {
  for (const [id, encoded] of cases) {
    for (const dryRun of [true, false]) {
      it(`requests one repair endpoint for ${id} dryRun=${dryRun}`, async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
        render(<QueryClientProvider client={client}><MemoryRouter><AiTeach /></MemoryRouter></QueryClientProvider>);
        await screen.findByRole("option", { name: id });
        fireEvent.change(screen.getByRole("combobox"), { target: { value: id } });
        fireEvent.change(screen.getByPlaceholderText(".old-row a.title"), { target: { value: ".old" } });
        fireEvent.click(screen.getByText("Propose repairs"));
        const button = await screen.findByRole("button", { name: dryRun ? "Dry-run preview" : "Commit 1 to site" });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);
        await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/learned/apply_repairs"))).toHaveLength(1));
        const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/learned/apply_repairs"));
        expect(calls[0][0], "SITE_ID_PATH_SPLIT repair endpoint").toBe(`/api/sites/${encoded}/learned/apply_repairs`);
        expect(calls[0][1]?.method).toBe("POST");
        expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ repairs: [{ old_selector: ".old", new_selector: ".new", role: "row_selectors" }], removed: [], dry_run: dryRun });
        client.clear();
      });
    }
    it(`requests one drift endpoint for ${id}`, async () => {
      await fetchDriftStatus(id);
      expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
      expect(vi.mocked(fetch).mock.calls[0][0], "SITE_ID_PATH_SPLIT drift endpoint").toBe(`/api/selector_drift/status/${encoded}`);
    });
  }
});
