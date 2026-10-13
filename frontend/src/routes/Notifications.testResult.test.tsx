// scs-fix F1 (bughunt spa-cockpit-settings, finder bd-worker-S52-A).
//
// F1: the Apprise / Telegram "Send test" toast read r.sent / r.failed, but the
// server answers sent_count / failed_count with ok = sent_count > 0 -- so a
// delivery that FAILED (sink 404, ok:false) toasted "Test sent (0 ok, 0 failed)"
// as a success. The fixtures below are the server's real response shapes
// (app_notify.api_notify_apprise_test, app_tg.api_tg_test).
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));
// PushSection owns a third "Send test" button and needs navigator.serviceWorker.
vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import { installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const BASE_FIXTURES = {
  "/api/notify/apprise/settings": {
    settings: { notify_apprise_urls_set: true, notify_apprise_urls_count: 1 },
  },
  "/api/tg/status": { available: true, running: false, allowlist_size: 2 },
  "/api/tg/settings": { settings: { tg_bot_token_set: true, tg_bot_allowlist: "111,222" } },
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
};

async function sendTest(index: 0 | 1, fixtures: Record<string, unknown>) {
  installApiFixtures(apiGetMock, apiPostMock, { ...BASE_FIXTURES, ...fixtures });
  const user = userEvent.setup();
  renderWired(<Notifications />, "/notifications");
  expect(await screen.findByText(/1 endpoint\(s\) configured/)).toBeInTheDocument();
  const buttons = screen.getAllByRole("button", { name: "Send test" });
  expect(buttons).toHaveLength(2);
  await user.click(buttons[index]);
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(toastMock.success.mock.calls.length + toastMock.error.mock.calls.length).toBe(1),
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
});

describe("scs-fix F1: Send test reports a failed delivery as a failure", () => {
  it("apprise ok:false (sink 404) -> error toast with the counts and the reason", async () => {
    await sendTest(0, {
      "POST /api/notify/apprise/test": {
        ok: false,
        sent_count: 0,
        failed_count: 1,
        skipped_count: 0,
        errors: [{ url: "json://127.0.0.1/hook", error: "HTTP 404" }],
      },
    });
    expect(toastMock.success, "BD-SCS-F1-FAILED-TEST-TOASTED-SUCCESS").not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith("Test failed (0 ok, 1 failed): HTTP 404");
  });

  it("apprise ok:true -> success toast with the server's sent_count, not legacy r.sent", async () => {
    await sendTest(0, {
      "POST /api/notify/apprise/test": { ok: true, sent_count: 1, failed_count: 0, errors: [] },
    });
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(toastMock.success).toHaveBeenCalledWith("Test sent (1 ok, 0 failed)");
  });

  it("telegram ok:false -> error toast", async () => {
    await sendTest(1, {
      "POST /api/tg/test": { ok: false, error: "token or allowlist missing" },
    });
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(toastMock.error).toHaveBeenCalledWith(
      "TG test failed (0 sent, 0 failed): token or allowlist missing",
    );
  });

  it("telegram ok:true -> success toast with sent_count, not legacy r.sent", async () => {
    await sendTest(1, {
      "POST /api/tg/test": { ok: true, sent_count: 2, failed_count: 0, errors: [] },
    });
    expect(toastMock.error).not.toHaveBeenCalled();
    expect(toastMock.success).toHaveBeenCalledWith("TG test: 2 sent");
  });
});
