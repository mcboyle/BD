// T7 / row 188 -- ROUTE REACHABILITY, EXERCISED THROUGH THE REAL ROUTE TABLE.
//
// The scan this replaces asserted `'path="/notifications"' in app` and
// `'go("/notifications")' in cp`. Mutant M4 repaths the <Route> to
// /notifications-v2 and restores the old substring in a JSX comment: the URL
// 404s and the scan stays green. The probe battery's palette mutant keeps the
// item's LABEL and changes only its destination, so any assertion about the
// item merely EXISTING passes.
//
// renderAppAt, NOT renderWired: renderWired places the component by hand inside
// a MemoryRouter with no <Route> of its own, so an unhooked or repathed binding
// passes it untouched. See the note in src/test/wiredGateHarness.tsx.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of Notifications.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));

vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import { CommandPalette } from "@/components/CommandPalette";
import {
  installApiFixtures,
  renderAppAt,
  renderWired,
} from "@/test/wiredGateHarness";

const FIXTURES = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/notify/apprise/settings": {
    settings: { notify_apprise_urls_set: true, notify_apprise_urls_count: 2 },
  },
  "/api/tg/status": { available: true, running: false, allowlist_size: 0 },
  "/api/tg/settings": { settings: { tg_bot_token_set: true } },
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
};

// A stable piece of the route's own content, not a class name, a DOM shape, or
// a built chunk filename.
const ROUTE_HEADING = "Apprise endpoints";

// The lazy route is imported through esbuild at test time; measured at ~0.4s on
// test5, but the default 5000ms bound is known in this repository to fire on a
// CORRECT implementation under load (see Push.wired.test.tsx), so it is stated.
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

describe("T7 route reachability", () => {
  it(
    "/notifications resolves to the Notifications screen through the real App route table",
    async () => {
      renderAppAt("/notifications");
      expect(
        await screen.findByText(ROUTE_HEADING, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "NEGATIVE CONTROL: the same render at another path does NOT show that screen",
    async () => {
      // Without this, "findByText succeeded" would be equally satisfied by an
      // App that renders Notifications everywhere, and the test above would say
      // nothing about the /notifications BINDING in particular.
      renderAppAt("/cluster");
      // Give the lazy chunk the same room to arrive that the positive case has.
      await new Promise((resolve) => setTimeout(resolve, 500));
      expect(screen.queryByText(ROUTE_HEADING)).toBeNull();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it("the command palette navigates to /notifications", async () => {
    function Location() {
      return <output>{useLocation().pathname}</output>;
    }
    renderWired(
      <>
        <CommandPalette />
        <Routes>
          <Route path="*" element={<Location />} />
        </Routes>
      </>,
    );
    fireEvent.keyDown(window, { key: "k", metaKey: true });

    // PRECONDITION: the item exists. The VERDICT is where selecting it lands --
    // an item that keeps its label and changes its destination passes the first
    // and fails the second.
    const item = screen.getByText("Notifications");
    expect(item).toBeInTheDocument();
    await userEvent.click(item);

    expect(screen.getByText("/notifications")).toBeInTheDocument();
  });
});
