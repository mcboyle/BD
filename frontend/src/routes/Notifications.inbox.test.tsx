// F005 -- NOTIFICATION INBOX ON /notifications, EXERCISED.
//
// APP-FEATURE-QUEUE row F005: "list loads; mark one read/unread; filter". The
// live page measured 0 list items, 0 read/unread controls and 0 filters. This
// spec mounts the real route, reads the list the inbox renders, clicks a real
// Mark read / Mark unread button and asks the mocked transport what was POSTed,
// and drives both filters and asks what was GETted.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of Notifications.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

// PushSection needs navigator.serviceWorker, which jsdom does not implement.
vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const ALL = "/api/notifications/inbox?status=all&severity=all";
const UNREAD = "/api/notifications/inbox?status=unread&severity=all";
const UNREAD_FAIL = "/api/notifications/inbox?status=unread&severity=fail";

const DISK = {
  id: 7,
  rule_id: "disk_low",
  name: "Disk space low",
  severity: "fail",
  ts: 1760000000,
  message: "2 GB free",
  read: false,
  read_at: null,
};
const RATE = {
  id: 5,
  rule_id: "high_failure_rate",
  name: "High failure rate",
  severity: "warn",
  ts: 1759990000,
  message: "40% failed",
  read: true,
  read_at: 1759999000,
};

const FIXTURES = {
  "/api/notify/apprise/settings": { settings: {} },
  "/api/tg/status": { available: false },
  "/api/tg/settings": { settings: {} },
  "/api/alerts/active?hours=24": { alerts: [] },
  [ALL]: { notifications: [DISK, RATE], total: 2, unread: 1 },
  [UNREAD]: { notifications: [DISK], total: 2, unread: 1 },
  [UNREAD_FAIL]: { notifications: [], total: 2, unread: 1 },
  "POST /api/notifications/inbox/7/read": { ok: true, id: 7, read: true },
  "POST /api/notifications/inbox/5/read": { ok: true, id: 5, read: false },
};

async function inboxItems() {
  const list = await screen.findByRole("list", { name: "Notification list" });
  return within(list).getAllByRole("listitem");
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
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("F005 notification inbox", () => {
  it("the list loads every notification with its read state and the counts", async () => {
    renderWired(<Notifications />, "/notifications");
    const items = await inboxItems();
    // EXACT COUNT, not "at least one": both fixture rows, nothing else.
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Disk space low — 2 GB free");
    expect(items[0]).toHaveAttribute("data-read", "false");
    expect(items[1]).toHaveTextContent("High failure rate — 40% failed");
    expect(items[1]).toHaveAttribute("data-read", "true");
    expect(screen.getByTestId("inbox-counts")).toHaveTextContent("1 unread of 2");
    expect(calledPaths(apiGetMock)).toContain(ALL);
  });

  it("Mark read / Mark unread POST the item's id with the flipped state", async () => {
    const user = userEvent.setup();
    renderWired(<Notifications />, "/notifications");
    await inboxItems();
    await user.click(screen.getByRole("button", { name: "Mark read: Disk space low" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/notifications/inbox/7/read", {
        read: true,
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Mark unread: High failure rate" }),
    );
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/notifications/inbox/5/read", {
        read: false,
      }),
    );
    // Exactly those two writes, and no T7 send/save rode along.
    expect(calledPaths(apiPostMock)).toEqual([
      "/api/notifications/inbox/7/read",
      "/api/notifications/inbox/5/read",
    ]);
  });

  it("the status and severity filters re-query the inbox and narrow the list", async () => {
    const user = userEvent.setup();
    renderWired(<Notifications />, "/notifications");
    expect(await inboxItems()).toHaveLength(2);

    await user.selectOptions(screen.getByLabelText("Status filter"), "unread");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain(UNREAD));
    await waitFor(async () => expect(await inboxItems()).toHaveLength(1));
    expect((await inboxItems())[0]).toHaveTextContent("Disk space low");

    await user.selectOptions(screen.getByLabelText("Severity filter"), "fail");
    await waitFor(() => expect(calledPaths(apiGetMock)).toContain(UNREAD_FAIL));
    expect(await screen.findByText("No notifications match.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Notification list" })).toBeNull();
  });

  it("an inbox error is shown, never rendered as an empty inbox", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {
      ...FIXTURES,
      [ALL]: { error: "notification inbox unreadable: RuntimeError: locked" },
    });
    renderWired(<Notifications />, "/notifications");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Notifications unavailable: notification inbox unreadable",
    );
    expect(screen.queryByText("No notifications match.")).toBeNull();
  });
});
