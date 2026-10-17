// appfeat-F013 r3 -- per-JOB pause / resume / retry on the Queue page.
//
// UIUX-20260928 F013 (agy-feat-26 r2 FAIL on v3.66.1841): the waiting row's
// Pause button posted /api/sites/<sid>/pause and paused the whole SITE, and a
// cancelled/failed job had no Retry control at all (terminal jobs were never
// rendered). Per-job controls must hit the single-job endpoints with
// {site_id, url} -- route AND body are asserted (724 suite rule).
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn() },
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

const WAITING_URL = "https://example.invalid/f013/waiting.mp4";
const WAITING_NAME = "waiting.mp4";
const PAUSED_URL = "https://example.invalid/f013/paused.mp4";
const PAUSED_NAME = "paused.mp4";
const CANCELLED_URL = "https://example.invalid/f013/cancelled.mp4";
const CANCELLED_NAME = "cancelled.mp4";
const DONE_URL = "https://example.invalid/f013/done.mp4";
const DONE_NAME = "done.mp4";

const SITE = { site_id: "s1", site_name: "f013-site", avatar_color: "#e11d48" };

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/queue/preflight": { ready: true, checks: [] },
  "/api/queue/v2": {
    running: [],
    waiting: [
      { ...SITE, url: WAITING_URL, filename: WAITING_NAME, priority: 0, queued_ts: 1 },
    ],
    terminal: [
      {
        ...SITE,
        url: PAUSED_URL,
        filename: PAUSED_NAME,
        status: "stopped",
        message: "Paused by user",
        ts_iso: "2026-10-17T00:00:00Z",
        paused: true,
      },
      {
        ...SITE,
        url: CANCELLED_URL,
        filename: CANCELLED_NAME,
        status: "stopped",
        message: "Cancelled by user",
        ts_iso: "2026-10-17T00:00:01Z",
        paused: false,
      },
      {
        ...SITE,
        url: DONE_URL,
        filename: DONE_NAME,
        status: "done",
        message: "",
        ts_iso: "2026-10-17T00:00:02Z",
        paused: false,
      },
    ],
    terminal_truncated_count: 0,
    done_today_count: 0,
    per_site: [{ site_id: "s1", name: "f013-site", state: "running", pending: 1, running: 0 }],
  },
  "/api/runs?status=failed": { ok: true, runs: [] },
  "POST /api/queue/v2/pause": { ok: true, paused: true, previous_status: "pending" },
  "POST /api/queue/v2/resume": { ok: true, resumed: true, previous_status: "stopped" },
  "POST /api/queue/v2/retry": { ok: true, retried: true, previous_status: "stopped" },
};

const LAZY_ROUTE_TIMEOUT = 20000;

function postCalls(): Array<[string, unknown]> {
  return apiPostMock.mock.calls.map((c) => [c[0] as string, c[1]]);
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
  toastMock.error.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("Queue per-job controls (appfeat-F013)", () => {
  it(
    "Pause on a waiting row pauses that JOB, not the site",
    async () => {
      const user = userEvent.setup();
      renderAppAt("/queue?status=waiting");
      await screen.findByText(WAITING_NAME, {}, { timeout: LAZY_ROUTE_TIMEOUT });
      await user.click(screen.getByRole("button", { name: "Pause" }));
      await waitFor(() =>
        expect(postCalls()).toContainEqual([
          "/api/queue/v2/pause",
          { site_id: "s1", url: WAITING_URL },
        ]),
      );
      expect(calledPaths(apiPostMock)).not.toContain("/api/sites/s1/pause");
      expect(toastMock.error).not.toHaveBeenCalled();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "paused job shows Resume, cancelled job shows Retry, done job shows neither",
    async () => {
      const user = userEvent.setup();
      renderAppAt("/queue");
      await screen.findByText(CANCELLED_NAME, {}, { timeout: LAZY_ROUTE_TIMEOUT });
      expect(screen.getByText(PAUSED_NAME)).toBeInTheDocument();
      expect(screen.queryByText(DONE_NAME)).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: `Resume job ${PAUSED_NAME}` }));
      await waitFor(() =>
        expect(postCalls()).toContainEqual([
          "/api/queue/v2/resume",
          { site_id: "s1", url: PAUSED_URL },
        ]),
      );

      await user.click(screen.getByRole("button", { name: `Retry job ${CANCELLED_NAME}` }));
      await waitFor(() =>
        expect(postCalls()).toContainEqual([
          "/api/queue/v2/retry",
          { site_id: "s1", url: CANCELLED_URL },
        ]),
      );
      // negative control: a cancelled job must not offer Resume, a paused one not Retry
      expect(
        screen.queryByRole("button", { name: `Resume job ${CANCELLED_NAME}` }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: `Retry job ${PAUSED_NAME}` }),
      ).not.toBeInTheDocument();
      expect(calledPaths(apiPostMock)).not.toContain("/api/sites/s1/resume");
      expect(calledPaths(apiPostMock)).not.toContain("/api/sites/s1/bulk_retry");
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );
});
