// T7 / row 188 -- ENDPOINT CONSUMPTION, EXERCISED.
//
// tests/test_t7_notifications_wired.py used to answer "are the 7 notify/tg/
// alerts families SPA-wired?" by searching useNotificationsData.ts for the
// string "/api/tg/status". A path NAMED is not a path CALLED: replacing the
// queryFn with a stub and leaving the literal in a trailing comment kept that
// scan green while the SPA stopped talking to the endpoint entirely. This spec
// mounts the real route and reads the paths the mocked transport was actually
// handed.
//
// FIXTURE CONSTANTS ARE DUPLICATED PER SPEC ON PURPOSE. A shared
// notificationsFixtures.ts would carry all seven "/api/..." literals and would
// be classified PRODUCT by tools/spa_population.py (only *.test.*/*.spec.* is
// SPEC), so the parity scanner would count a TEST FIXTURE as proof the SPA
// wires those routes -- the exact laundering v3.66.1217 closed. Keeping the
// literals inside a *.test.tsx file keeps them out of the product population.
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

// PushSection owns a THIRD "Send test" button and needs navigator.serviceWorker,
// which jsdom does not implement. Unmocked it manufactures failures that have
// nothing to do with T7.
vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
} from "@/test/wiredGateHarness";

// THE ROW'S OWN DENOMINATOR, PINNED RATHER THAN DERIVED. T1's Dashboard spec
// reads its endpoint set out of the hook so a newly added query joins the gate
// automatically. That is the right trade there and the WRONG one here: row 188
// names SEVEN specific families as the contract, and a derivation from the hook
// would let an evader delete the queryFn and the derived entry in one edit --
// which is mutant M3, the row's own named evasion. So the seven are pinned, the
// count is asserted, and set EQUALITY (not containment) is the verdict, so a
// dropped family and an undeclared new one both go red.
const T7_FAMILIES = [
  "/api/notify/apprise/settings",
  "/api/notify/apprise/validate",
  "/api/notify/apprise/test",
  "/api/tg/status",
  "/api/tg/settings",
  "/api/tg/test",
  "/api/alerts/active",
];

// The families this route GETs on mount. /api/alerts/active is matched by
// pattern, never by "?hours=24": useNotificationsData.ts defaults hours = 24 and
// an innocent default change must not turn a wiring gate red.
const MOUNT_GETS = [
  "/api/notify/apprise/settings",
  "/api/tg/status",
  "/api/tg/settings",
];
const ALERTS_WITH_WINDOW = /^\/api\/alerts\/active\?hours=\d+$/;

const T7_PATH_RE = /^\/api\/(notify|tg|alerts)\//;

const FIXTURES = {
  "/api/notify/apprise/settings": {
    settings: { notify_apprise_urls_set: true, notify_apprise_urls_count: 2 },
  },
  "/api/tg/status": { available: true, running: false, allowlist_size: 0 },
  "/api/tg/settings": { settings: { tg_bot_token_set: true } },
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
  "POST /api/notify/apprise/settings": { settings: {} },
  "POST /api/notify/apprise/validate": { results: [] },
  "POST /api/notify/apprise/test": { sent: 1, failed: 0 },
  "POST /api/tg/settings": { settings: {} },
  "POST /api/tg/test": { sent: 1 },
};

function t7Paths(mock: ReturnType<typeof vi.fn>): string[] {
  return calledPaths(mock)
    .map((p) => p.split("?")[0])
    .filter((p) => T7_PATH_RE.test(p));
}

/** Click a page button, then Confirm inside the dialog it arms. */
async function driveConfirmed(user: ReturnType<typeof userEvent.setup>, trigger: HTMLElement) {
  await user.click(trigger);
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
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
  installApiFixtures(apiGetMock, apiPostMock, FIXTURES);
});

describe("T7 endpoint consumption", () => {
  it("mounting the route reads exactly its four GET families and posts nothing", async () => {
    renderWired(<Notifications />, "/notifications");
    expect(await screen.findByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();

    const observed = t7Paths(apiGetMock);
    // VACUITY GUARD FIRST: an empty observation satisfies every containment
    // claim below, so it is refused before any of them is evaluated.
    expect(observed.length).toBeGreaterThan(0);

    const raw = calledPaths(apiGetMock).filter((p) => T7_PATH_RE.test(p));
    expect([...new Set(observed)].sort()).toEqual(
      [...MOUNT_GETS, "/api/alerts/active"].sort(),
    );
    expect(raw.some((p) => ALERTS_WITH_WINDOW.test(p))).toBe(true);
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("a full drive observes exactly the seven T7 endpoint families", async () => {
    // THE DENOMINATOR IS NONZERO AND IS THE ONE THE ROW NAMES.
    expect(T7_FAMILIES).toHaveLength(7);
    expect(new Set(T7_FAMILIES).size).toBe(7);

    const user = userEvent.setup();
    renderWired(<Notifications />, "/notifications");
    expect(await screen.findByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText(/one apprise URL per line/),
      "tgram://token/chat",
    );
    await user.click(screen.getByRole("button", { name: "Validate" }));

    const sendTests = screen.getAllByRole("button", { name: "Send test" });
    // PRECONDITION: exactly two, so indexing cannot silently pick the wrong one.
    expect(sendTests).toHaveLength(2);

    await driveConfirmed(user, screen.getByRole("button", { name: "Save apprise" }));
    await driveConfirmed(user, screen.getAllByRole("button", { name: "Send test" })[0]);
    await driveConfirmed(user, screen.getByRole("button", { name: "Save telegram" }));
    await driveConfirmed(user, screen.getAllByRole("button", { name: "Send test" })[1]);

    await waitFor(() => {
      const union = new Set([...t7Paths(apiGetMock), ...t7Paths(apiPostMock)]);
      expect(union.size).toBeGreaterThan(0);
      expect([...union].sort()).toEqual([...T7_FAMILIES].sort());
    });
  });

  it("Validate posts the pasted URLs and touches no save or test family", async () => {
    const user = userEvent.setup();
    renderWired(<Notifications />, "/notifications");
    expect(await screen.findByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText(/one apprise URL per line/),
      "tgram://token/chat",
    );
    await user.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/notify/apprise/validate", {
        urls: "tgram://token/chat",
      }),
    );
    expect(t7Paths(apiPostMock)).toEqual(["/api/notify/apprise/validate"]);
  });
});
