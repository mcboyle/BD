// Row 238, the DISPLAY half -- a settings form must show the value it is about
// to send.
//
// Notifications.roundtrip.test.tsx owns the transport contract. This spec owns
// the two questions a preloaded cache cannot answer:
//
//  (1) ASYNC ARRIVAL. A preloaded cache makes the GET payload available on the
//      FIRST render, so it cannot tell a correct implementation apart from
//      `useState(query.data?...)` -- an initializer that runs once, observes
//      `undefined` in the real app (where the fetch resolves AFTER mount),
//      falls back to the constant, and loses the operator's data exactly as
//      before. So the async case mounts with an EMPTY cache and lets
//      installApiFixtures resolve on its own schedule. That is tracked mutant
//      M2 and this is its catcher.
//
//  (2) THE OVER-SENSITIVITY CONTROL. The other tempting implementation, a
//      `useEffect(() => setX(server), [server])` seed, passes (1) and then
//      CLOBBERS an in-progress edit on the next background refetch --
//      useAppriseSettings polls every 30s, so that is a real keystroke-eating
//      bug, not a hypothetical. The last test drives a late payload into the
//      cache, proves the component consumed it, and asserts the operator's
//      typing survived.
//
// Fixture literals are duplicated per spec rather than shared: see the note at
// the top of Notifications.endpoints.test.tsx.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

import Notifications from "@/routes/Notifications";
import { freshQueryClient, installApiFixtures } from "@/test/wiredGateHarness";

const STORED_ALLOWLIST = "424242,-1001337,90210";
const LATE_ALLOWLIST = "555000,555001";
const TYPED_ALLOWLIST = "777001";

const STORED_APPRISE = {
  settings: {
    notify_apprise_enabled: true,
    notify_apprise_urls_set: true,
    notify_apprise_urls_count: 2,
  },
};
const STORED_TG = {
  settings: {
    tg_bot_enabled: true,
    tg_bot_token_set: true,
    tg_bot_allowlist: STORED_ALLOWLIST,
  },
};
const STORED_TG_STATUS = { available: true, running: true, allowlist_size: 3 };

const FIXTURES = {
  "/api/notify/apprise/settings": STORED_APPRISE,
  "/api/tg/status": STORED_TG_STATUS,
  "/api/tg/settings": STORED_TG,
  "/api/alerts/active?hours=24": { alerts: [] },
  "/api/queue/v2": { waiting: [], running: [] },
  "POST /api/notify/apprise/settings": { settings: {} },
  "POST /api/tg/settings": { settings: {} },
};

// Query keys copied from src/hooks/useNotificationsData.ts.
function preload(qc: QueryClient): QueryClient {
  qc.setQueryData(["notify", "apprise", "settings"], STORED_APPRISE);
  qc.setQueryData(["tg", "settings"], STORED_TG);
  qc.setQueryData(["tg", "status"], STORED_TG_STATUS);
  qc.setQueryData(["alerts", "active", 24], { alerts: [] });
  return qc;
}

function renderWithClient(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/notifications"]}>
        <Notifications />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function allowlistInput(): HTMLElement {
  return screen.getByPlaceholderText(/chat-id allowlist/);
}

function botCheckbox(): HTMLInputElement {
  return screen.getByRole("checkbox", { name: "Enable bot" }) as HTMLInputElement;
}

function appriseCheckbox(): HTMLInputElement {
  return screen.getByRole("checkbox", {
    name: "Enable apprise notifications",
  }) as HTMLInputElement;
}

async function driveConfirmed(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await user.click(screen.getByRole("button", { name }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

function lastBody(path: string): Record<string, unknown> {
  const call = [...apiPostMock.mock.calls].reverse().find((c) => c[0] === path);
  expect(call, `no POST to ${path} was observed`).toBeTruthy();
  return (call as unknown[])[1] as Record<string, unknown>;
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

describe("row 238 -- non-secret settings fields are seeded from GET", () => {
  it("shows the stored allowlist and the stored enable flags, not the component defaults", () => {
    renderWithClient(preload(freshQueryClient()));
    // PRECONDITION: rendered only from the GET payloads, so the assertions below
    // cannot pass on a page that never consumed them. Holds on the defective
    // tree too, which keeps the value assertions the first thing that can fail.
    expect(screen.getByText(/token set/)).toBeInTheDocument();
    expect(screen.getByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();

    expect(allowlistInput()).toHaveValue(STORED_ALLOWLIST);
    expect(botCheckbox().checked).toBe(true);
    expect(appriseCheckbox().checked).toBe(true);
  });

  it("ASYNC ARRIVAL: a payload that lands AFTER mount still reaches the field and the save body", async () => {
    // The empty cache is the whole point -- see note (1) at the top of the file.
    const user = userEvent.setup();
    const qc = freshQueryClient();
    // PRECONDITION ON THE FIXTURE ITSELF: nothing is preloaded, so anything the
    // field shows below had to arrive through the transport.
    expect(qc.getQueryData(["tg", "settings"])).toBeUndefined();
    renderWithClient(qc);
    expect(allowlistInput()).toHaveValue("");

    await waitFor(() => expect(allowlistInput()).toHaveValue(STORED_ALLOWLIST));
    await waitFor(() => expect(botCheckbox().checked).toBe(true));

    // and the seeded value is what the transport is handed, not decoration
    await driveConfirmed(user, "Save telegram");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    const body = lastBody("/api/tg/settings");
    expect(body.tg_bot_allowlist).toBe(STORED_ALLOWLIST);
    expect(body.tg_bot_enabled).toBe(true);
  });

  it("OVER-SENSITIVITY CONTROL: a later GET payload does not clobber an in-progress edit", async () => {
    // Green on the defective tree AND on the fixed tree by design: it is the
    // control on the seeding fix, not a RED. It goes red on the useEffect-seed
    // implementation (tracked mutant M6), which eats keystrokes on every 30s
    // background refetch.
    const user = userEvent.setup();
    const qc = preload(freshQueryClient());
    renderWithClient(qc);
    expect(screen.getByText(/token set/)).toBeInTheDocument();

    await user.clear(allowlistInput());
    await user.type(allowlistInput(), TYPED_ALLOWLIST);
    expect(allowlistInput()).toHaveValue(TYPED_ALLOWLIST);

    // A background refetch lands a DIFFERENT server value. tg_bot_token_set is
    // flipped in the same payload purely as the receipt: when the copy changes
    // to "no token", the component provably consumed this payload, so "the
    // field still shows what I typed" cannot pass because the update never
    // arrived.
    qc.setQueryData(["tg", "settings"], {
      settings: {
        tg_bot_enabled: true,
        tg_bot_token_set: false,
        tg_bot_allowlist: LATE_ALLOWLIST,
      },
    });
    expect(await screen.findByText(/no token/)).toBeInTheDocument();

    expect(allowlistInput()).toHaveValue(TYPED_ALLOWLIST);
    await driveConfirmed(user, "Save telegram");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    expect(lastBody("/api/tg/settings").tg_bot_allowlist).toBe(TYPED_ALLOWLIST);
  });
});
