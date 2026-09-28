// dl95-file-examples-3 -- Queue "Status: failed" filter listed the WAITING jobs.
//
// Home's needs-attention "Failed runs" row links to /queue?status=failed. The
// Queue page only understood all/running/waiting, so "failed" fell through to
// "show both sections": the chip read "Status: failed" above the 18 waiting
// jobs and none of the 6 failed runs (O1508 rerun on test2,
// harness-work/UIUX-20260928/download-95/shots/fe95-failed-runs.png).
//
// Rendered through the real route table (renderAppAt) so the Home link's
// destination is what is exercised.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";

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

const WAITING_URL = "https://file-examples.com/storage/x/file_example_AVI_1280_1_5MG.avi";
// WaitingJobRow renders the filename; a failed run carries only its URL.
const WAITING_NAME = "file_example_AVI_1280_1_5MG.avi";
const FAILED_URL = "https://file-examples.com/storage/x/file_example_MP4_480_1_5MG.mp4";

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/queue/preflight": { ready: true, checks: [] },
  "/api/queue/v2": {
    running: [],
    waiting: [
      {
        site_id: "s1",
        site_name: "o1508-file-examples-95",
        avatar_color: "#e11d48",
        url: WAITING_URL,
        filename: WAITING_NAME,
        priority: 0,
        queued_ts: 1,
      },
    ],
    done_today_count: 0,
    per_site: [],
  },
  "/api/runs?status=failed": {
    ok: true,
    runs: [
      {
        id: 7,
        site_id: "s1",
        url: FAILED_URL,
        status: "failed",
        started_at: "2026-09-28T22:40:00Z",
        finished_at: "2026-09-28T22:40:05Z",
        reason_code: "network",
      },
    ],
  },
};

const LAZY_ROUTE_TIMEOUT = 20000;

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
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("Queue status=failed filter (dl95-file-examples-3)", () => {
  it(
    "lists failed runs, not the waiting list",
    async () => {
      renderAppAt("/queue?status=failed");
      expect(
        await screen.findByText(FAILED_URL, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
      expect(screen.queryByText(WAITING_NAME)).not.toBeInTheDocument();
      expect(calledPaths(apiGetMock)).toContain("/api/runs?status=failed");
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "status=waiting still lists waiting jobs and never lists failed runs",
    async () => {
      renderAppAt("/queue?status=waiting");
      expect(
        await screen.findByText(WAITING_NAME, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
      expect(screen.queryByText(FAILED_URL)).not.toBeInTheDocument();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );
});
