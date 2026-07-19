import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ReadinessBadge } from "./ReadinessBadge";

// Cut 4 — per-site readiness badge over GET /api/sites/<id>/readiness.

function mockReadiness(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response),
    ),
  );
}
function mount(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}
afterEach(() => vi.unstubAllGlobals());

describe("ReadinessBadge", () => {
  it("compact: renders the level label for red", async () => {
    mockReadiness({ ok: true, site_id: "s1", level: "red", checks: [], fixes: [] });
    mount(<ReadinessBadge siteId="s1" />);
    expect(await screen.findByText(/not ready/i)).toBeTruthy();
  });

  it("expanded: renders checks and suggested fixes", async () => {
    mockReadiness({
      ok: true,
      site_id: "s1",
      level: "amber",
      checks: [
        { key: "login_url", label: "Login URL", status: "ok", detail: "configured" },
        { key: "download_dir", label: "Download directory", status: "warn", detail: "not set" },
      ],
      fixes: ["Set a download directory for this site."],
    });
    mount(<ReadinessBadge siteId="s1" expanded />);
    expect(await screen.findByText("Login URL")).toBeTruthy();
    expect(screen.getByText("Download directory")).toBeTruthy();
    expect(screen.getByText(/set a download directory/i)).toBeTruthy();
  });
});
