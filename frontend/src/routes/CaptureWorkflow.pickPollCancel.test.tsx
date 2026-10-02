import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";

// O1671 a13 (AUDIT-13 HIGH): the pick poll effect's cleanup cleared the
// interval but never set `cancelled`, so a poll already in flight when the
// operator disarmed (or the page unmounted) still filled the field, cleared
// armedField and toasted "Grabbed ...". Each test holds the first poll open on
// a deferred, disarms or unmounts, then resolves it with a selector.

const { apiGetMock, apiPostMock, apiPutMock, toastSuccess } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: toastSuccess,
    error: vi.fn(),
    message: vi.fn(),
  }),
  Toaster: () => null,
}));

import { renderAppAt } from "@/test/wiredGateHarness";

const PICKED = {
  ok: true,
  result: {
    selector: "div.stale-pick",
    group_selector: "div.stale-group",
    group_count: 3,
    group_visible: 3,
    visible: true,
  },
};

let releaseFirstPoll: (v: unknown) => void = () => {};

function grabbedToasts() {
  return toastSuccess.mock.calls.filter(([msg]) => String(msg).startsWith("Grabbed"));
}

function pollCalls() {
  return apiPostMock.mock.calls.filter(
    ([path, body]) => path === "/cockpit/api/captures/pick" && body?.action === "poll",
  );
}

async function openSessionAndArm() {
  const view = renderAppAt("/capture");
  await screen.findByRole("heading", { name: "Live capture workflow", level: 1 }, { timeout: 20_000 });
  fireEvent.change(screen.getByPlaceholderText("WowGirls"), { target: { value: "Pick site" } });
  fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), {
    target: { value: "https://members.example.invalid/login" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Create site & continue/i }));
  expect(await screen.findByText("Start a capture")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open session" }));
  expect(await screen.findByText("Learn the download affordance")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Pick element" }));
  expect(await screen.findByText(/Pick mode/)).toBeInTheDocument();
  // The interval fires at 800ms; wait for the first poll to be in flight.
  await waitFor(() => expect(pollCalls().length).toBe(1), { timeout: 3_000 });
  return view;
}

async function resolveFirstPollAndSettle() {
  await act(async () => {
    releaseFirstPoll(PICKED);
    await new Promise((r) => setTimeout(r, 50));
  });
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
  localStorage.clear();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPutMock.mockReset();
  toastSuccess.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/api/auth/whoami") {
      return Promise.resolve({ ok: true, user: null, multi_user: false });
    }
    if (path === "/cockpit/api/novnc") return Promise.resolve({ ok: true, url: "" });
    return Promise.resolve({});
  });
  let polls = 0;
  apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
    if (path === "/api/captures/setup_site") {
      return Promise.resolve({ ok: true, id: "site_a13", login_url: "https://members.example.invalid/login" });
    }
    if (path === "/cockpit/api/run-capture") {
      return Promise.resolve({ task: { task_id: "t_a13" } });
    }
    if (path === "/cockpit/api/captures/pick" && body?.action === "poll") {
      polls += 1;
      // First poll: held open until the test releases it. Later polls never
      // settle, so only the first one can ever deliver a selector.
      if (polls === 1) {
        return new Promise((resolve) => {
          releaseFirstPoll = resolve;
        });
      }
      return new Promise(() => {});
    }
    return Promise.resolve({ ok: true });
  });
  apiPutMock.mockResolvedValue({ ok: true });
});

describe("O1671 a13 capture pick poll cancellation", () => {
  it(
    "positive control: a poll that resolves while still armed grabs the selector",
    async () => {
      await openSessionAndArm();
      await resolveFirstPollAndSettle();
      expect(grabbedToasts().length).toBe(1);
      expect(screen.queryByText(/Pick mode/)).toBeNull();
    },
    30_000,
  );

  it(
    "a poll in flight when the operator disarms makes no state change and no toast",
    async () => {
      await openSessionAndArm();
      fireEvent.click(screen.getByRole("button", { name: "Interact" }));
      await waitFor(() => expect(screen.queryByText(/Pick mode/)).toBeNull());
      // Re-arm so a stale fill would be visible as armedField being cleared.
      fireEvent.click(screen.getByRole("button", { name: "Pick element" }));
      expect(await screen.findByText(/Pick mode/)).toBeInTheDocument();

      await resolveFirstPollAndSettle();

      expect(grabbedToasts(), "a13: stale pick poll toasted after disarm").toEqual([]);
      expect(screen.getByText(/Pick mode/)).toBeInTheDocument();
    },
    30_000,
  );

  it(
    "a poll in flight when the page unmounts makes no state change and no toast",
    async () => {
      const view = await openSessionAndArm();
      view.unmount();

      await resolveFirstPollAndSettle();

      expect(grabbedToasts(), "a13: stale pick poll toasted after unmount").toEqual([]);
    },
    30_000,
  );
});
