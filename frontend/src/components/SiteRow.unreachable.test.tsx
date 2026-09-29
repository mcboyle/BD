import { describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";

vi.mock("@/lib/api-client", () => ({
  apiGet: vi.fn(() => Promise.resolve({})),
  apiPost: vi.fn(() => Promise.resolve({ ok: true })),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

// dl95-kellymadisonmedia-1 — a site whose login page never loads reports
// auth_state "unreachable"; the row must say so instead of looking healthy.
import { SiteRow } from "./SiteRow";
import type { AuthState, SiteEntryV2 } from "@/lib/api-types";
import { renderWired } from "@/test/wiredGateHarness";

function site(auth_state: AuthState): SiteEntryV2 {
  return {
    site_id: "d477c086",
    name: "kellymadisonmedia",
    avatar_color: "#e11d48",
    state: "idle",
    auth_state,
    captcha_pending: false,
    downloaded_total: 0,
    active_workers: 0,
    last_event_ts: 0,
    last_event_age: "",
  };
}

describe("SiteRow unreachable login host (dl95-kellymadisonmedia-1)", () => {
  it("shows a 'Login host unreachable' badge", () => {
    renderWired(<SiteRow site={site("unreachable")} />);
    expect(screen.getByText("Login host unreachable")).toBeInTheDocument();
  });

  it("shows no such badge for a site never logged into", () => {
    renderWired(<SiteRow site={site("unknown")} />);
    expect(screen.queryByText("Login host unreachable")).not.toBeInTheDocument();
  });
});
