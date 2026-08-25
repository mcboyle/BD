// Backlog row 240 (v3.66.1239), the DISPLAY half -- a settings form must show
// the value it is about to send.
//
// Settings.supervisorRoundtrip.test.tsx owns the transport contract. This spec
// owns the three questions a preloaded cache cannot answer:
//
//  (1) ASYNC ARRIVAL. A preloaded cache makes the GET payload available on the
//      FIRST render, so it cannot tell a correct implementation apart from
//      `useState(query.data?...)` -- an initializer that runs once, observes
//      `undefined` in the real app (where the fetch resolves after mount),
//      falls back to the constant, and resets the operator's live limits
//      exactly as before. The async case therefore mounts with an EMPTY cache.
//
//  (2) THE OVER-SENSITIVITY CONTROL. The other tempting implementation, a
//      `useEffect(() => setX(server), [server])` seed, passes (1) and then
//      CLOBBERS an in-progress edit on the next background refetch. The third
//      test drives a late payload into the cache, proves the component
//      CONSUMED it (an untouched field moves), and asserts the operator's
//      typing survived in the touched one.
//
//  (3) THE UNAVAILABLE PAYLOAD. app_supervisor.py returns
//      {ok:false, error:"supervisor unavailable"} with NO stats key at all, and
//      HTTP 200. The seeding must degrade to the shipped defaults rather than
//      throw -- the new optional chain must not fire on a correct-but-empty
//      response.
//
// The supervisor section is `collapsible defaultOpen={false}`, and
// ui/collapsible.tsx renders `{shown && children}`, so the controls are NOT
// MOUNTED until the header is clicked. Every test opens it explicitly; a spec
// that queried the fields without opening would fail on element lookup and
// never reach its verdict. The overlay state lives in Settings itself, so the
// section's mount schedule does not affect what is being measured.
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
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  apiPostForm: vi.fn(),
  apiPostDownload: vi.fn(),
  ApiError: class extends Error {},
}));

import { Settings } from "@/routes/Settings";
import { freshQueryClient, installApiFixtures } from "@/test/wiredGateHarness";

const STATUS_PATH = "/api/supervisor/status";
const STATUS_KEY = ["supervisor", "status"];

// The operator's live limits. Every value is deliberately NOT the component
// default (false / "0" / ""), so any assertion below that passes is passing on
// the server payload and not on a constant that happens to agree.
const STORED_GLOBAL_BPS = 12500000;
const STORED_PER_SITE = { "example.com": 500000, aylo: 250000 };
const STORED_PER_SITE_JSON = '{"example.com":500000,"aylo":250000}';
const STORED_STATUS = {
  ok: true,
  stats: {
    enabled: true,
    global: { name: "global", rate_bps: STORED_GLOBAL_BPS },
    per_site: {},
    config: { global_bps: STORED_GLOBAL_BPS, per_site_bps: STORED_PER_SITE },
  },
};
const UNAVAILABLE_STATUS = { ok: false, error: "supervisor unavailable" };

// Fixtures the rest of the page demands before it will render at all --
// EnvironmentSettings dereferences data.env, so an empty {} crashes the tree.
const FIXTURES: Record<string, unknown> = {
  "/api/settings/env/effective": { env: [] },
  "/api/settings/envfile": { env: [], path: "/x/.env", exists: true, writable: true },
  [STATUS_PATH]: STORED_STATUS,
};

function mount(qc: QueryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function section(): HTMLElement {
  const el = document.getElementById("supervisor-throttle");
  expect(el, "the supervisor throttle section is not in the DOM").toBeTruthy();
  return el as HTMLElement;
}

// PRECONDITION HELPER. Opens the collapsed section and asserts the three
// controls it is about to interrogate actually mounted. This holds on the
// DEFECTIVE tree too -- deliberately, so that the value assertions in each test
// are the first thing that can fail, with their own diagnostic.
async function openSupervisor(user: ReturnType<typeof userEvent.setup>) {
  // The page renders a skeleton until /api/global_config resolves, so the
  // section does not exist on the first tick.
  await waitFor(() => expect(document.getElementById("supervisor-throttle")).toBeTruthy());
  const header = within(section()).getAllByRole("button")[0];
  expect(header).toHaveAttribute("aria-expanded", "false");
  await user.click(header);
  expect(header).toHaveAttribute("aria-expanded", "true");
  expect(enabledSwitch()).toBeInTheDocument();
  expect(globalInput()).toBeInTheDocument();
  expect(perSiteBox()).toBeInTheDocument();
}

function enabledSwitch(): HTMLElement {
  return screen.getByRole("switch", { name: "Supervisor throttle enabled" });
}

function globalInput(): HTMLInputElement {
  return within(section()).getByRole("spinbutton") as HTMLInputElement;
}

function perSiteBox(): HTMLTextAreaElement {
  return screen.getByLabelText(
    "Per-site bytes per second JSON",
  ) as HTMLTextAreaElement;
}

function statusGets(): string[] {
  return apiGetMock.mock.calls
    .map((c) => String(c[0]))
    .filter((p) => p === STATUS_PATH);
}

async function applyConfirmed(user: ReturnType<typeof userEvent.setup>) {
  await user.click(within(section()).getByRole("button", { name: "Apply" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
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

describe("row 240 -- the supervisor throttle form is seeded from GET", () => {
  it("shows the stored limits, not the component defaults", async () => {
    const user = userEvent.setup();
    const qc = freshQueryClient();
    qc.setQueryData(STATUS_KEY, STORED_STATUS);
    mount(qc);
    await openSupervisor(user);

    expect(enabledSwitch()).toHaveAttribute("aria-checked", "true");
    expect(globalInput().value).toBe(String(STORED_GLOBAL_BPS));
    expect(perSiteBox().value).toBe(STORED_PER_SITE_JSON);
  });

  it("ASYNC ARRIVAL: a payload that lands AFTER mount still reaches the fields and the POST body", async () => {
    // The empty cache is the whole point -- see note (1) at the top of the file.
    const user = userEvent.setup();
    const qc = freshQueryClient();
    // PRECONDITION ON THE FIXTURE ITSELF: nothing is preloaded, so anything the
    // fields show below had to arrive through the transport.
    expect(qc.getQueryData(STATUS_KEY)).toBeUndefined();
    mount(qc);
    await openSupervisor(user);

    await waitFor(() =>
      expect(globalInput().value).toBe(String(STORED_GLOBAL_BPS)),
    );
    expect(enabledSwitch()).toHaveAttribute("aria-checked", "true");
    expect(perSiteBox().value).toBe(STORED_PER_SITE_JSON);

    // and the seeded values are what the transport is handed, not decoration
    await applyConfirmed(user);
    const body = lastBody("/api/supervisor/configure");
    expect(body.enabled).toBe(true);
    expect(body.global_bps).toBe(STORED_GLOBAL_BPS);
    expect(body.per_site_bps).toEqual(STORED_PER_SITE);
  });

  it("OVER-SENSITIVITY CONTROL: a later GET payload does not clobber an in-progress edit", async () => {
    // MEASURED ON THE BASE, NOT ASSUMED. This is the control against the
    // useEffect-seed implementation, which eats keystrokes on every background
    // refetch -- but it is RED on the defective base too, and the reason is
    // stated rather than glossed: its RECEIPT (the untouched switch moving when
    // the late payload lands) is itself a seeding behaviour, so it cannot hold
    // on a tree that never reads the payload. Its control role is against the
    // WRONG FIXES, not against the base.
    const user = userEvent.setup();
    const qc = freshQueryClient();
    qc.setQueryData(STATUS_KEY, {
      ok: true,
      stats: { enabled: false, config: { global_bps: 1000, per_site_bps: {} } },
    });
    mount(qc);
    await openSupervisor(user);

    await user.clear(globalInput());
    await user.type(globalInput(), "999");
    expect(globalInput().value).toBe("999");

    // A background refetch lands a DIFFERENT server payload. `enabled` is
    // flipped in the same payload purely as the RECEIPT: the operator never
    // touched that control, so when the switch moves the component provably
    // consumed this payload -- "the number I typed is still there" therefore
    // cannot pass merely because the update never arrived.
    qc.setQueryData(STATUS_KEY, {
      ok: true,
      stats: { enabled: true, config: { global_bps: 4242, per_site_bps: {} } },
    });
    await waitFor(() =>
      expect(enabledSwitch()).toHaveAttribute("aria-checked", "true"),
    );

    expect(globalInput().value).toBe("999");
    await applyConfirmed(user);
    const body = lastBody("/api/supervisor/configure");
    expect(body.global_bps).toBe(999);
    expect(body.enabled).toBe(true);
  });

  it("UNAVAILABLE CONTROL: {ok:false} with no stats degrades to the shipped defaults without throwing", async () => {
    const user = userEvent.setup();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    installApiFixtures(apiGetMock, apiPostMock, {
      ...FIXTURES,
      [STATUS_PATH]: UNAVAILABLE_STATUS,
    });
    mount(freshQueryClient());
    await openSupervisor(user);

    // PRECONDITION: the query really ran and really returned the unavailable
    // payload, so the defaults below are the fallback being exercised rather
    // than a page that never asked.
    await waitFor(() => expect(statusGets().length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(apiGetMock.mock.results.length).toBeGreaterThan(0),
    );
    expect(enabledSwitch()).toHaveAttribute("aria-checked", "false");
    expect(globalInput().value).toBe("0");
    expect(perSiteBox().value).toBe("");
    expect(within(section()).getByText("valid")).toBeInTheDocument();
  });
});
