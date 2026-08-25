// Row 238 -- A SAVE MUST NOT WRITE A VALUE THE OPERATOR NEVER SAW.
//
// THE DEFECT. Notifications.tsx held tgAllowlist / tgEnabled / appriseEnabled in
// useState(<constant>), never seeded them from the GET payload, and put all
// three into the save patch UNCONDITIONALLY -- while the line immediately below
// guarded the far more sensitive tg_bot_token with `if (tgToken.trim())`. That
// asymmetry is what marks it an oversight. An operator who opened the page and
// saved for any reason PATCHed tg_bot_allowlist:"" and tg_bot_enabled:false;
// app_tg.py writes both through on mere key presence, app.py re-parses the
// allowlist into an empty set, and tg_bot.py then refuses to start
// ("tg_bot: not starting - empty allowlist"). Silent data loss plus a
// self-inflicted outage -- it fails CLOSED, so it is NOT an authz widening.
//
// THIS SPEC IS THE TRANSPORT CONTRACT: what the POST body actually carries.
// Notifications.seeding.test.tsx is the display/seeding half, and deliberately
// carries the async-arrival case that a poisoned cache cannot pose.
//
// WHY THE CACHE IS PRELOADED. installApiFixtures resolves asynchronously, so a
// spec that merely mounts would let a body assertion pass for the wrong reason
// on either tree. Every render here seeds the query cache BEFORE mounting, and
// every test asserts the payload was really consumed (the GET-derived
// "token set" / "endpoint(s) configured" copy) and that a POST was really
// dispatched, BEFORE it asserts anything about the body.
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

// The operator's stored chat-id list. Distinctive on purpose: an assertion that
// this exact string came back out of the transport cannot be satisfied by any
// default, placeholder or truncation.
const STORED_ALLOWLIST = "424242,-1001337,90210";
const TYPED_ALLOWLIST = "777001";
const TYPED_APPRISE_URL = "json://row238.invalid/hook";

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
function storedClient(): QueryClient {
  const qc = freshQueryClient();
  qc.setQueryData(["notify", "apprise", "settings"], STORED_APPRISE);
  qc.setQueryData(["tg", "settings"], STORED_TG);
  qc.setQueryData(["tg", "status"], STORED_TG_STATUS);
  qc.setQueryData(["alerts", "active", 24], { alerts: [] });
  return qc;
}

function renderStored() {
  return render(
    <QueryClientProvider client={storedClient()}>
      <MemoryRouter initialEntries={["/notifications"]}>
        <Notifications />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function allowlistInput(): HTMLElement {
  return screen.getByPlaceholderText(/chat-id allowlist/);
}

function appriseInput(): HTMLElement {
  return screen.getByPlaceholderText(/one apprise URL per line/);
}

// PRECONDITIONS, asserted before any verdict. Both strings are rendered ONLY
// from the GET payloads (tg_bot_token_set / notify_apprise_urls_count), so they
// prove the preload was consumed rather than merely supplied. They hold on the
// defective tree too, which is what makes the body assertions below the FIRST
// thing that can fail.
function assertPayloadsConsumed(): void {
  expect(screen.getByText(/token set/)).toBeInTheDocument();
  expect(screen.getByText(/2 endpoint\(s\) configured/)).toBeInTheDocument();
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

// PRECONDITION ON DISPATCH. Fails loudly with its own message when nothing was
// POSTed, so "the body did not carry X" can never be a silent stand-in for "no
// save happened at all".
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

describe("row 238 -- saves preserve settings the operator never retyped", () => {
  it("toggling the bot on does not erase the stored chat-id allowlist", async () => {
    const user = userEvent.setup();
    renderStored();
    assertPayloadsConsumed();

    // The operator's ONLY interaction is the enable checkbox. They never touch
    // the allowlist field -- that is the entire scenario.
    const enable = screen.getByRole("checkbox", { name: "Enable bot" });
    const before = (enable as HTMLInputElement).checked;
    await user.click(enable);
    expect((enable as HTMLInputElement).checked).toBe(!before);

    await driveConfirmed(user, "Save telegram");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    // THE DISTINCTIVE ASSERTION. Not "something was sent" -- the operator's own
    // three chat ids, byte for byte.
    expect(lastBody("/api/tg/settings").tg_bot_allowlist).toBe(STORED_ALLOWLIST);
  });

  it("an allowlist edit does not silently disable a running bot", async () => {
    const user = userEvent.setup();
    renderStored();
    assertPayloadsConsumed();
    // PRECONDITION: the fixture really says the bot is on, so `true` below is a
    // preserved value and not a coincidence of the default.
    expect(STORED_TG.settings.tg_bot_enabled).toBe(true);

    await user.clear(allowlistInput());
    await user.type(allowlistInput(), TYPED_ALLOWLIST);
    expect(allowlistInput()).toHaveValue(TYPED_ALLOWLIST);

    await driveConfirmed(user, "Save telegram");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    const body = lastBody("/api/tg/settings");
    expect(body.tg_bot_enabled).toBe(true);
    // and the edit itself still lands, so this is not passing by sending nothing
    expect(body.tg_bot_allowlist).toBe(TYPED_ALLOWLIST);
  });

  it("pasting apprise URLs does not silently disable apprise notifications", async () => {
    const user = userEvent.setup();
    renderStored();
    assertPayloadsConsumed();
    expect(STORED_APPRISE.settings.notify_apprise_enabled).toBe(true);

    await user.type(appriseInput(), TYPED_APPRISE_URL);
    expect(appriseInput()).toHaveValue(TYPED_APPRISE_URL);

    await driveConfirmed(user, "Save apprise");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    const body = lastBody("/api/notify/apprise/settings");
    expect(body.notify_apprise_enabled).toBe(true);
    expect(body.notify_apprise_urls).toBe(TYPED_APPRISE_URL);
  });

  it("THE CONTROL THAT MATTERS: a deliberately emptied allowlist still sends an empty string", async () => {
    // WITHOUT THIS, the fix could simply make clearing impossible. Guarding the
    // allowlist the way the token is guarded (`if (tgAllowlist.trim())`) would
    // satisfy every test above and leave the operator unable to revoke a chat
    // id from the UI at all -- a different bug, not a fix. That evasion is
    // tracked mutant M5 and this test is its named control.
    //
    // "Never typed" and "cleared on purpose" must stay DISTINGUISHABLE: this
    // test types a distinctive value first, so the subsequent clear is an
    // observed operator action and not a vacuous no-op on an already-empty
    // field. It is green on the defective tree AND on the fixed tree by
    // design -- it is a control, not a RED.
    const user = userEvent.setup();
    renderStored();
    assertPayloadsConsumed();

    await user.clear(allowlistInput());
    await user.type(allowlistInput(), TYPED_ALLOWLIST);
    expect(allowlistInput()).toHaveValue(TYPED_ALLOWLIST);
    await user.clear(allowlistInput());
    expect(allowlistInput()).toHaveValue("");

    await driveConfirmed(user, "Save telegram");
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());

    const body = lastBody("/api/tg/settings");
    expect(body).toHaveProperty("tg_bot_allowlist");
    expect(body.tg_bot_allowlist).toBe("");
  });
});
