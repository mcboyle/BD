// T7 / row 188 -- "WRITES ARE NEVER ONE-CLICK", EXERCISED.
//
// The scan this replaces was a regex, `onClick=\{[^}]*save(Apprise|Tg)\.mutate`.
// Mutant M5 routes the same mutation through `[saveTg].map((m) => m.mutate(...))`
// inside the onClick: the literal text "saveTg.mutate" never appears, the regex
// stays silent, and Save telegram becomes a genuine one-click write. Here every
// one of the four gated writes is clicked for real and the transport is asked
// whether anything was sent BEFORE the dialog is confirmed.
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

// PushSection owns a third "Send test" button and needs navigator.serviceWorker.
vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import {
  calledPaths,
  installApiFixtures,
  renderWired,
} from "@/test/wiredGateHarness";

const FIXTURES = {
  // notify_apprise_urls_set gates the apprise "Send test" button; without it
  // that button is disabled and its case below would pass for the wrong reason.
  "/api/notify/apprise/settings": {
    settings: { notify_apprise_urls_set: true, notify_apprise_urls_count: 2 },
  },
  "/api/tg/status": { available: true, running: false, allowlist_size: 0 },
  "/api/tg/settings": { settings: { tg_bot_token_set: true } },
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
  "POST /api/notify/apprise/settings": { settings: {} },
  "POST /api/notify/apprise/test": { sent: 1, failed: 0 },
  "POST /api/tg/settings": { settings: {} },
  "POST /api/tg/test": { sent: 1 },
};

// (button label, index among same-labelled buttons, endpoint the confirm must hit)
const GATED_WRITES: Array<[string, number, string]> = [
  ["Save apprise", 0, "/api/notify/apprise/settings"],
  ["Send test", 0, "/api/notify/apprise/test"],
  ["Save telegram", 0, "/api/tg/settings"],
  ["Send test", 1, "/api/tg/test"],
];

function postedTo(path: string): boolean {
  return calledPaths(apiPostMock).some((p) => p.split("?")[0] === path);
}

async function mount(): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup();
  renderWired(<Notifications />, "/notifications");
  // PRECONDITION: the page is really up before any click is attributed to it.
  expect(await screen.findByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();
  expect(apiPostMock).not.toHaveBeenCalled();
  return user;
}

function trigger(label: string, index: number): HTMLElement {
  const buttons = screen.getAllByRole("button", { name: label });
  // PRECONDITION ON THE SELECTION ITSELF. "Send test" is rendered twice; a
  // layout change that dropped one would otherwise make index 1 silently
  // resolve to the wrong control, or throw an error unrelated to the claim.
  expect(buttons.length).toBeGreaterThan(index);
  if (label === "Send test") expect(buttons).toHaveLength(2);
  const button = buttons[index];
  expect(button).toBeEnabled();
  return button;
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

describe("T7 confirmation-gated writes", () => {
  for (const [label, index, endpoint] of GATED_WRITES) {
    it(`${label}[${index}] arms a dialog and posts ${endpoint} only on Confirm`, async () => {
      const user = await mount();

      await user.click(trigger(label, index));

      // THE DISCRIMINATING ASSERTION: the click alone must not have written.
      expect(postedTo(endpoint)).toBe(false);
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByRole("button", { name: "Confirm" })).toBeInTheDocument();
      expect(postedTo(endpoint)).toBe(false);

      await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(postedTo(endpoint)).toBe(true));
    });
  }

  it("cancelling the dialog closes it and posts nothing at all", async () => {
    const user = await mount();

    await user.click(trigger("Save telegram", 0));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
