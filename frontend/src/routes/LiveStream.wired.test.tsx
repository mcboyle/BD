import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { LiveSection } from "@/components/sections/LiveSection";
import { useLiveStatus } from "@/hooks/useLive";
import { Library } from "@/routes/Library";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
  renderWiredHook,
} from "@/test/wiredGateHarness";

const FIXTURES = {
  "/api/live/status": {
    ok: true,
    available: true,
    active_count: 1,
    max_active: 2,
  },
  "/api/live/recordings": {
    ok: true,
    recordings: [
      { id: "rec-1", state: "recording", site: "fixture", bytes: 128 },
    ],
  },
  "/api/library/browse?limit=200": {
    ok: true,
    rows: [{ id: 7, title: "Fixture movie", history_id: 42 }],
  },
  "/api/library/tags": { ok: true, tags: [] },
  "/api/library/scan/status": { ok: true, scan: { state: "idle" } },
  "/api/library/stats": {},
  "/api/scene_score/bottom?limit=20": {},
  "/api/queue/v2": { waiting: [], running: [] },
  "/api/sites/v2": { sites: [] },
  "POST /api/live/watch": { ok: true, recording_id: "rec-2" },
  "POST /api/live/unwatch": { ok: true },
  "POST /api/stream/token/42": { ok: true, token: "stream-fixture" },
};

beforeEach(() => {
  vi.useRealTimers();
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiDeleteMock.mockResolvedValue({ ok: true });
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("T9a live and stream runtime wiring", () => {
  it("mounts the live section with status and recordings reads but no writes", async () => {
    renderWired(<LiveSection />, "/library");
    expect(screen.getByRole("heading", { name: "Live recordings" })).toBeInTheDocument();

    await waitFor(() => {
      const paths = calledPaths(apiGetMock);
      expect(paths).toContain("/api/live/status");
      expect(paths).toContain("/api/live/recordings");
    });
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("polls live status again after five seconds", async () => {
    vi.useFakeTimers();
    renderWiredHook(() => useLiveStatus());
    expect(calledPaths(apiGetMock)).toEqual(["/api/live/status"]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(calledPaths(apiGetMock)).toEqual([
      "/api/live/status",
      "/api/live/status",
    ]);
  });

  it("does not arm a watch until the operator confirms", async () => {
    const user = userEvent.setup();
    renderWired(<LiveSection />, "/library");
    await user.type(screen.getByPlaceholderText("Live URL"), "https://live.test/room");
    await user.type(screen.getByPlaceholderText("Output dir"), "/tmp/output");
    await user.click(screen.getByRole("button", { name: "Watch" }));
    expect(calledPaths(apiPostMock)).not.toContain("/api/live/watch");

    await user.click(screen.getByRole("button", { name: "Arm recording" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/live/watch", {
        url: "https://live.test/room",
        output_dir: "/tmp/output",
      }),
    );
  });

  it("does not cancel a recording until the operator confirms", async () => {
    const user = userEvent.setup();
    renderWired(<LiveSection />, "/library");
    await screen.findByText("fixture");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calledPaths(apiPostMock)).not.toContain("/api/live/unwatch");

    await user.click(screen.getByRole("button", { name: "Cancel recording" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/live/unwatch", {
        recording_id: "rec-1",
      }),
    );
  });

  it("mints a stream token for the row's real history id", async () => {
    const user = userEvent.setup();
    renderWired(<Library />, "/library");
    await user.click(await screen.findByRole("button", { name: "Stream link" }));

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/stream/token/42", {
        ttl_seconds: 3600,
      }),
    );
    expect(await screen.findByDisplayValue(/stream-fixture/)).toBeInTheDocument();
  });
});
