// ssqh-fix r2 -- Queue bulk toasts report the SERVER count (F1); the pin-to-front
// path encodes the site id (F4, Queue part).
//
// Sweep spa-sites-queue-history (bd-worker-S21-C): POST bulk_retry on 2 waiting URLs
// answered {"ok":true,"retried":0} and the page toasted "Retried: 2 job(s) across 1
// site(s)" -- the selection size, not what the server did. And pinMut put the raw
// site id into /api/sites/<sid>/priority while every bulk call encodes it. Claims
// below are about toast text and request paths, never about the component source.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({ toast: toastMock }));

import { calledPaths, installApiFixtures, renderAppAt } from "@/test/wiredGateHarness";

const LAZY_ROUTE_TIMEOUT = 20000;

const URL_A = "https://example.invalid/ssqh/a.mp4";
const URL_B = "https://example.invalid/ssqh/b.mp4";

function fixtures(siteId: string, extra: Record<string, unknown> = {}) {
  const site = { site_id: siteId, site_name: "ssqh-site", avatar_color: "#e11d48" };
  return {
    "/api/auth/whoami": { ok: true, user: null, multi_user: false },
    "/api/queue/preflight": { ready: true, checks: [] },
    "/api/queue/v2": {
      running: [],
      waiting: [
        { ...site, url: URL_A, filename: "a.mp4", priority: 0, queued_ts: 1 },
        { ...site, url: URL_B, filename: "b.mp4", priority: 0, queued_ts: 2 },
      ],
      terminal: [],
      terminal_truncated_count: 0,
      done_today_count: 0,
      per_site: [{ site_id: siteId, name: "ssqh-site", state: "running", pending: 2, running: 0 }],
    },
    "/api/runs?status=failed": { ok: true, runs: [] },
    ...extra,
  };
}

async function bulkRetryAll() {
  const user = userEvent.setup();
  renderAppAt("/queue?status=waiting");
  await screen.findByText("a.mp4", {}, { timeout: LAZY_ROUTE_TIMEOUT });
  await user.click(screen.getByRole("button", { name: "Select waiting jobs for bulk cancel" }));
  await user.click(screen.getByRole("button", { name: /Select all waiting/ }));
  await user.click(screen.getByRole("button", { name: "Retry selected waiting jobs" }));
  await waitFor(() => expect(calledPaths(apiPostMock)).toContain("/api/sites/s1/bulk_retry"));
  await waitFor(() =>
    expect(toastMock.success.mock.calls.length + toastMock.warning.mock.calls.length).toBe(1),
  );
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
  toastMock.warning.mockReset();
});

describe("ssqh F1: Queue bulk toast reports the server count", () => {
  it(
    "server retried 0 of 2 -> a warning with 0 of 2, never 'Retried: 2'",
    async () => {
      installApiFixtures(
        apiGetMock,
        apiPostMock,
        fixtures("s1", { "POST /api/sites/s1/bulk_retry": { ok: true, retried: 0 } }),
      );
      await bulkRetryAll();
      expect(
        toastMock.success.mock.calls.map((c) => c[0]),
        "SSQH_F1_TOAST_USES_SELECTION_SIZE",
      ).not.toContain("Retried: 2 job(s) across 1 site(s)");
      expect(toastMock.warning).toHaveBeenCalledWith("Retried: 0 of 2 job(s) across 1 site(s)");
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "control: server retried 2 of 2 -> success with 2",
    async () => {
      installApiFixtures(
        apiGetMock,
        apiPostMock,
        fixtures("s1", { "POST /api/sites/s1/bulk_retry": { ok: true, retried: 2 } }),
      );
      await bulkRetryAll();
      expect(toastMock.success).toHaveBeenCalledWith("Retried: 2 job(s) across 1 site(s)");
      expect(toastMock.warning).not.toHaveBeenCalled();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );
});

describe("ssqh F4: pin-to-front encodes the site id", () => {
  for (const [sid, path] of [
    ["a#b?c", "/api/sites/a%23b%3Fc/priority"],
    ["s1", "/api/sites/s1/priority"], // control: a plain id is unchanged
  ] as const) {
    it(
      `site id ${sid} -> ${path}`,
      async () => {
        installApiFixtures(apiGetMock, apiPostMock, fixtures(sid));
        const user = userEvent.setup();
        renderAppAt("/queue?status=waiting");
        await screen.findByText("a.mp4", {}, { timeout: LAZY_ROUTE_TIMEOUT });
        await user.click(screen.getAllByRole("button", { name: "Pin to front" })[0]);
        await waitFor(() =>
          expect(calledPaths(apiPostMock).filter((p) => p.endsWith("/priority")).length).toBe(1),
        );
        expect(calledPaths(apiPostMock), "SSQH_F4_PRIORITY_PATH_UNENCODED").toContain(path);
      },
      LAZY_ROUTE_TIMEOUT + 5000,
    );
  }
});
