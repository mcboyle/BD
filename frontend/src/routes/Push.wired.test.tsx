import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, createRootMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  createRootMock: vi.fn(() => ({ render: vi.fn() })),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

vi.mock("react-dom/client", () => ({
  default: { createRoot: createRootMock },
}));

import { PushSection } from "@/components/sections/PushSection";
import { buildSubscriptionForEnable } from "@/hooks/usePush";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
} from "@/test/wiredGateHarness";

type PushPlatform = {
  getSubscription: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  requestPermission: ReturnType<typeof vi.fn>;
};

function installPushPlatform(existing: object | null): PushPlatform {
  const getSubscription = vi.fn().mockResolvedValue(existing);
  const subscribe = vi.fn();
  const requestPermission = vi.fn().mockResolvedValue("granted");
  const registration = { pushManager: { getSubscription, subscribe } };

  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve(registration), register: vi.fn() },
  });
  Object.defineProperty(window, "PushManager", {
    configurable: true,
    value: class PushManager {},
  });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { requestPermission },
  });
  return { getSubscription, subscribe, requestPermission };
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  createRootMock.mockClear();
  document.body.innerHTML = '<div id="root"></div>';
});

describe("T9b push runtime wiring", () => {
  it("reuses an existing subscription without permission or re-subscribing", async () => {
    const json = {
      endpoint: "https://push.test/existing",
      keys: { p256dh: "fixture-p256dh", auth: "fixture-auth" },
    };
    const existing = { toJSON: vi.fn(() => json) };
    const platform = installPushPlatform(existing);

    await expect(buildSubscriptionForEnable("AQID")).resolves.toEqual(json);
    expect(platform.getSubscription).toHaveBeenCalledOnce();
    expect(platform.subscribe).not.toHaveBeenCalled();
    expect(platform.requestPermission).not.toHaveBeenCalled();
  });

  it("mints a first subscription only after permission with the server VAPID key", async () => {
    const json = {
      endpoint: "https://push.test/new",
      keys: { p256dh: "new-p256dh", auth: "new-auth" },
    };
    const fresh = { toJSON: vi.fn(() => json) };
    const platform = installPushPlatform(null);
    platform.subscribe.mockResolvedValue(fresh);

    await expect(buildSubscriptionForEnable("AQID")).resolves.toEqual(json);
    expect(platform.requestPermission).toHaveBeenCalledOnce();
    expect(platform.subscribe).toHaveBeenCalledOnce();
    const options = platform.subscribe.mock.calls[0][0];
    expect(options.userVisibleOnly).toBe(true);
    expect(Array.from(options.applicationServerKey)).toEqual([1, 2, 3]);
  });

  it("mounts as subscribed using reads only, then confirms the test write", async () => {
    const existing = {
      endpoint: "https://push.test/existing",
      toJSON: vi.fn(() => ({ endpoint: "https://push.test/existing" })),
    };
    installPushPlatform(existing);
    installApiFixtures(apiGetMock, apiPostMock, {
      "/api/push/info": { available: true, public_key: "AQID" },
      "POST /api/push/test": { ok: true, sent: 1, failed: 0, throttled: 0 },
    });
    const user = userEvent.setup();

    renderWired(<PushSection />, "/notifications");
    expect(await screen.findByText(/This device is subscribed/)).toBeInTheDocument();
    expect(calledPaths(apiGetMock)).toContain("/api/push/info");
    expect(apiPostMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(calledPaths(apiPostMock)).not.toContain("/api/push/test");
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Send test" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/push/test", {}),
    );
  });

  it("enables a new device and posts the browser subscription JSON", async () => {
    const json = {
      endpoint: "https://push.test/new",
      keys: { p256dh: "new-p256dh", auth: "new-auth" },
    };
    const fresh = { toJSON: vi.fn(() => json) };
    const platform = installPushPlatform(null);
    platform.subscribe.mockResolvedValue(fresh);
    installApiFixtures(apiGetMock, apiPostMock, {
      "/api/push/info": { available: true, public_key: "AQID" },
      "POST /api/push/subscribe": { ok: true },
    });
    const user = userEvent.setup();

    renderWired(<PushSection />, "/notifications");
    const enable = await screen.findByRole("button", {
      name: "Enable on this device",
    });
    expect(apiPostMock).not.toHaveBeenCalled();
    await user.click(enable);

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/push/subscribe", json),
    );
  });

  it("unsubscribes the browser endpoint before tombstoning it on the server", async () => {
    const unsubscribe = vi.fn().mockResolvedValue(true);
    const existing = {
      endpoint: "https://push.test/existing",
      unsubscribe,
      toJSON: vi.fn(() => ({ endpoint: "https://push.test/existing" })),
    };
    installPushPlatform(existing);
    installApiFixtures(apiGetMock, apiPostMock, {
      "/api/push/info": { available: true, public_key: "AQID" },
      "POST /api/push/unsubscribe": { ok: true },
    });
    const user = userEvent.setup();

    renderWired(<PushSection />, "/notifications");
    const disable = await screen.findByRole("button", {
      name: "Disable on this device",
    });
    expect(apiPostMock).not.toHaveBeenCalled();
    await user.click(disable);

    await waitFor(() => {
      expect(unsubscribe).toHaveBeenCalledOnce();
      expect(apiPostMock).toHaveBeenCalledWith("/api/push/unsubscribe", {
        endpoint: "https://push.test/existing",
      });
    });
  });

  // TIMEOUT IS EXPLICIT BECAUSE THE DEFAULT IS THE DEFECT, NOT THE PRODUCT.
  // This test does `await import("@/main")`, which pulls the whole application
  // module graph through esbuild; measured at 7,583ms with register() called
  // exactly once as ("/sw.js", {scope:"/"}). vitest's default testTimeout is
  // 5000ms, so the test failed on a CORRECT implementation -- CLAUDE.md counts a
  // bound that fires on correct work as a soundness bug, not a safe default. It
  // is host-load dependent, which is why one integrator run measured 24/24 green
  // and a reviewer measured it red 3/3 on the same tree.
  it("registers the existing root service worker at root scope on load", async () => {
    installPushPlatform(null);
    const register = vi.fn().mockResolvedValue({});
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { ready: Promise.resolve({}), register },
    });

    await import("@/main");
    window.dispatchEvent(new Event("load"));

    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
    expect(createRootMock).toHaveBeenCalledOnce();
  }, 30000);
});
