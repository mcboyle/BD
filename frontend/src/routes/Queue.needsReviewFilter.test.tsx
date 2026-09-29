// dl95-file-examples-6 -- /queue?status=needs_review listed running + waiting jobs.
//
// FE3 taught the Queue page "failed"; any other status still fell through to
// "all": on test2 the chip read "Status: needs_review" above 15 running and 4
// waiting jobs and none of the needs_review job
// (harness-work/DOT95-LANE/live-dl-f4/queue-needs-review.png). needs_review jobs
// have their own page (/needs-review, from /api/history); the Queue URL now
// leads there, and a status with no view shows "all" without a lying chip.
//
// Rendered through the real route table (renderAppAt).
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

const WAITING_URL = "https://members.africancasting.com/bonus/4/african-sex-trip/";
const WAITING_NAME = "african-sex-trip";
const REVIEW_SITE = "o1508-dlf4-95";
const REVIEW_URL = "https://file-examples.com/index.php/sample-video-files/sample-mp4-files/";

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/queue/preflight": { ready: true, checks: [] },
  "/api/queue/v2": {
    running: [],
    waiting: [
      {
        site_id: "s0",
        site_name: "africancasting",
        avatar_color: "#0ea5e9",
        url: WAITING_URL,
        filename: WAITING_NAME,
        priority: 0,
        queued_ts: 1,
      },
    ],
    done_today_count: 0,
    per_site: [],
  },
  "/api/history?status=needs_review&limit=200": [
    {
      id: 11,
      site_id: "s1",
      site_name: REVIEW_SITE,
      url: REVIEW_URL,
      filename: "",
      status: "needs_review",
      message: "No video on this page",
      ts: "2026-09-29 00:03:18",
    },
  ],
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

describe("Queue status=needs_review (dl95-file-examples-6)", () => {
  it(
    "shows the needs_review jobs, not the waiting list",
    async () => {
      renderAppAt("/queue?status=needs_review");
      // The triage page names the job in its list and its detail pane.
      expect(
        await screen.findAllByText(REVIEW_SITE, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).not.toHaveLength(0);
      expect(screen.queryByText(WAITING_NAME)).not.toBeInTheDocument();
      expect(screen.queryByText("Status: needs_review")).not.toBeInTheDocument();
      expect(calledPaths(apiGetMock)).toContain("/api/history?status=needs_review&limit=200");
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "a status with no view shows all jobs without a chip claiming a filter",
    async () => {
      renderAppAt("/queue?status=stopped");
      expect(
        await screen.findByText(WAITING_NAME, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
      expect(screen.queryByText("Status: stopped")).not.toBeInTheDocument();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "control: status=waiting keeps its chip and its list",
    async () => {
      renderAppAt("/queue?status=waiting");
      expect(
        await screen.findByText(WAITING_NAME, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
      expect(screen.getByText("Status: waiting")).toBeInTheDocument();
      expect(screen.queryByText(REVIEW_SITE)).not.toBeInTheDocument();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );
});
