// T2 / row 182 -- ROUTE REACHABILITY, EXERCISED THROUGH THE REAL ROUTE TABLE.
//
// The scan this replaces asserted a `lazy(() => import("./routes/History"))`
// regex and `'path="/history"' in App.tsx` and `'go("/history")' in
// CommandPalette.tsx`. Repath the <Route> and restore the old substring in a
// JSX comment and the URL 404s while the scan stays green; keep the palette
// item's LABEL and change only its destination and any assertion about the item
// merely EXISTING passes.
//
// renderAppAt, NOT renderWired: renderWired places the component by hand inside
// a MemoryRouter with no <Route> of its own, so an unhooked or repathed binding
// passes it untouched. See the note in src/test/wiredGateHarness.tsx.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of History.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

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

import { CommandPalette } from "@/components/CommandPalette";
import { installApiFixtures, renderAppAt, renderWired } from "@/test/wiredGateHarness";

const FIXTURES: Record<string, unknown> = {
  "/api/auth/whoami": { ok: true, user: null, multi_user: false },
  "/api/history?limit=200": [],
  "/api/queue/v2": { waiting: [], running: [] },
};

// A stable piece of the route's own content -- not a class name, a DOM shape,
// or a built chunk filename.
const ROUTE_TEXT = "History · Logs · Search";

// The lazy chunk is transformed at test time. The default 5000ms bound is known
// in this repository to fire on a CORRECT implementation under load (see
// Push.wired.test.tsx), so it is stated rather than inherited.
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

describe("T2 route reachability", () => {
  it(
    "/history resolves to the History screen through the real App route table",
    async () => {
      renderAppAt("/history");
      expect(
        await screen.findByText(ROUTE_TEXT, {}, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it(
    "NEGATIVE CONTROL: the same render at another path does NOT show that screen",
    async () => {
      // Without this, "findByText succeeded" would be equally satisfied by an
      // App that renders History everywhere, and the case above would say
      // nothing about the /history BINDING in particular.
      renderAppAt("/cluster");
      await new Promise((resolve) => setTimeout(resolve, 500));
      expect(screen.queryByText(ROUTE_TEXT)).toBeNull();
    },
    LAZY_ROUTE_TIMEOUT + 5000,
  );

  it("the command palette navigates to /history", async () => {
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
    const item = await screen.findByText("History & Logs");
    expect(item).toBeInTheDocument();
    await userEvent.click(item);

    expect(screen.getByText("/history")).toBeInTheDocument();
  }, 20000);
});
