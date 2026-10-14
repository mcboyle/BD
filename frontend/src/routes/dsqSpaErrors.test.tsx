// dsq-spa-errors (deep-spa-sites-queue-history F2, F5, F7): failed reads and
// no-op writes must be visible. Real route components; the transport is mocked
// with the answers the finder measured from the real backend.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const t = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    message: vi.fn(),
    dismiss: vi.fn(),
  });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: t };
});
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
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import BulkEnqueue from "@/routes/BulkEnqueue";
import History from "@/routes/History";
import NeedsReview from "@/routes/NeedsReview";
import { Sites } from "@/routes/Sites";
import { mutationErrorMessage, shouldGlobalErrorToast } from "@/lib/mutationErrors";
import { freshQueryClient } from "@/test/wiredGateHarness";

const T = 20000;

// The app's own client shape (main.tsx): the global MutationCache error toast.
function appQueryClient() {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _v, _c, mutation) => {
        if (!shouldGlobalErrorToast(Boolean(mutation.options.onError))) return;
        toastMock.error(mutationErrorMessage(error));
      },
    }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
}

let qc: QueryClient;
function mount(el: ReactElement, client: QueryClient = freshQueryClient()) {
  qc = client;
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{el}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function getAnswers(table: Record<string, unknown>) {
  apiGetMock.mockImplementation((path: string) => {
    for (const [prefix, ans] of Object.entries(table)) {
      if (String(path).startsWith(prefix)) {
        return ans instanceof Error ? Promise.reject(ans) : Promise.resolve(ans);
      }
    }
    return Promise.resolve({});
  });
}

const ROW = (i: number) => ({
  id: i,
  site_id: "s1",
  site_name: "Site s1",
  url: `https://x.invalid/v${i}`,
  status: "needs_review",
  message: "verify failed",
});

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
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function () {};
});

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  toastMock.mockReset();
  for (const k of ["success", "error", "warning", "info", "message"] as const) {
    toastMock[k].mockReset();
  }
  getAnswers({});
  apiPostMock.mockImplementation(() => Promise.resolve({ ok: true }));
});

describe("F2 NeedsReview", () => {
  it("F2a a failed history read shows an error, not an empty queue", async () => {
    getAnswers({ "/api/history": new Error("HTTP 500 boom-f2a") });
    mount(<NeedsReview />);
    const alert = await screen.findByRole("alert", {}, { timeout: 5000 });
    expect(alert.textContent, "dsq F2a: no error shown for a failed read").toMatch(
      /Couldn't load the review queue/,
    );
    expect(document.body.textContent).not.toMatch(/No review items|nothing waiting/);
  }, T);

  it("F2a control: rows render", async () => {
    getAnswers({ "/api/history": [ROW(1), ROW(2)] });
    mount(<NeedsReview />);
    expect(await screen.findByText(/2 items awaiting review/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  }, T);

  it("F2b control: the app client toasts a rejected approve (no fix owed)", async () => {
    getAnswers({ "/api/history": [ROW(1)] });
    apiPostMock.mockImplementation(() => Promise.reject(new Error("HTTP 500 approve-xyz")));
    mount(<NeedsReview />, appQueryClient());
    await userEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("HTTP 500 approve-xyz"));
  }, T);

  it("F2b control: the bare test client shows no toast (what the sweep saw)", async () => {
    getAnswers({ "/api/history": [ROW(1)] });
    apiPostMock.mockImplementation(() => Promise.reject(new Error("HTTP 500 approve-xyz")));
    mount(<NeedsReview />);
    await userEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 300));
    expect(toastMock.error).not.toHaveBeenCalled();
  }, T);

  it("F2c approve that approved nothing says so", async () => {
    getAnswers({ "/api/history": [ROW(1)] });
    apiPostMock.mockImplementation(() => Promise.resolve({ ok: true, approved: 0 }));
    mount(<NeedsReview />);
    await userEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() =>
      expect(toastMock.warning, "dsq F2c: approved:0 gave no feedback").toHaveBeenCalledWith(
        expect.stringMatching(/Nothing was approved/),
      ),
    );
  }, T);

  it("F2c control: approved:1 gives no warning", async () => {
    getAnswers({ "/api/history": [ROW(1)] });
    apiPostMock.mockImplementation(() => Promise.resolve({ ok: true, approved: 1 }));
    mount(<NeedsReview />);
    await userEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 300));
    expect(toastMock.warning).not.toHaveBeenCalled();
  }, T);
});

const OK_RES = { ok: true, site_id: "aaaa1111", requested: 2, added: 2, dupes: 0, skipped: 0 };

async function enqueueTwo() {
  fireEvent.change(screen.getByLabelText("site id"), { target: { value: "aaaa1111" } });
  fireEvent.change(screen.getByLabelText("urls"), {
    target: { value: "https://x.invalid/1\nhttps://x.invalid/2" },
  });
  await userEvent.click(screen.getByRole("button", { name: /Enqueue 2 URL/ }));
  await screen.findByTestId("bulk-enqueue-result");
}

describe("F5 BulkEnqueue", () => {
  it("F5a a successful enqueue refreshes the queue and site views", async () => {
    apiPostMock.mockImplementation(() => Promise.resolve(OK_RES));
    mount(<BulkEnqueue />);
    const spy = vi.spyOn(qc, "invalidateQueries");
    await enqueueTwo();
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
      expect(keys, "dsq F5a: nothing invalidated after enqueue").toContain('["queue-v2"]');
      expect(keys).toContain('["sites-v2"]');
    });
  }, T);

  it("F5b editing the site id retires the old result", async () => {
    apiPostMock.mockImplementation(() => Promise.resolve(OK_RES));
    mount(<BulkEnqueue />);
    await enqueueTwo();
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "bbbb2222" } });
    await waitFor(() =>
      expect(
        screen.queryByTestId("bulk-enqueue-result"),
        "dsq F5b: stale 'Enqueued on aaaa1111' after editing the site id",
      ).toBeNull(),
    );
  }, T);

  it("F5b control: the result stays while the inputs are unchanged", async () => {
    apiPostMock.mockImplementation(() => Promise.resolve(OK_RES));
    mount(<BulkEnqueue />);
    await enqueueTwo();
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.getByTestId("bulk-enqueue-result").textContent).toMatch(/aaaa1111/);
  }, T);

  it("F5c the site id hint shows the real id shape", () => {
    mount(<BulkEnqueue />);
    const ph = screen.getByLabelText("site id").getAttribute("placeholder") || "";
    expect(ph, "dsq F5c: placeholder suggests a domain").not.toMatch(/example\.com/);
    expect(ph).toMatch(/\b[0-9a-f]{8}\b/);
  });

  it("F5d over 1000 URLs: the cap is named before and after submit", async () => {
    const many = Array.from({ length: 1500 }, (_, i) => `https://x.invalid/${i}`).join("\n");
    apiPostMock.mockImplementation(() =>
      Promise.resolve({ ok: true, site_id: "aaaa1111", requested: 1500, added: 1000, dupes: 0, skipped: 500 }),
    );
    mount(<BulkEnqueue />);
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "aaaa1111" } });
    fireEvent.change(screen.getByLabelText("urls"), { target: { value: many } });
    expect(
      screen.getByRole("status").textContent,
      "dsq F5d: no word about the 1000 cap",
    ).toMatch(/first 1000 .*500 will be skipped/);
    await userEvent.click(screen.getByRole("button", { name: /Enqueue 1500 URL/ }));
    const res = await screen.findByTestId("bulk-enqueue-result");
    expect(res.textContent).toMatch(/500 were over the 1000-per-submit limit/);
  }, T);

  it("F5d control: 2 URLs, no cap note", async () => {
    apiPostMock.mockImplementation(() => Promise.resolve(OK_RES));
    mount(<BulkEnqueue />);
    await enqueueTwo();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("bulk-enqueue-result").textContent).not.toMatch(/limit/);
  }, T);
});

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

describe("F7 History tabs and Sites", () => {
  it("F7 Events tab: failed reads are errors, not 'No events yet.'", async () => {
    getAnswers({
      "/api/events_all": new Error("HTTP 500 ev"),
      "/api/session_history": new Error("HTTP 500 sess"),
      "/api/history": [],
    });
    mount(<History />);
    await openTab("Events");
    expect(await screen.findByText("Couldn't load events.")).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load session events.")).toBeInTheDocument();
    expect(document.body.textContent, "dsq F7: failed read shown as empty").not.toMatch(
      /No events yet|No session events yet/,
    );
  }, T);

  it("F7 control: empty answers still read as empty", async () => {
    getAnswers({
      "/api/events_all": { events: [] },
      "/api/session_history": { events: [] },
      "/api/history": [],
    });
    mount(<History />);
    await openTab("Events");
    expect(await screen.findByText("No events yet.")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load/)).toBeNull();
  }, T);

  it("F7 Logs tab: a failed tail is an error, not 'log empty'", async () => {
    getAnswers({ "/api/logs/tail": new Error("HTTP 500 tail"), "/api/history": [] });
    mount(<History />);
    await openTab("Logs");
    expect(await screen.findByText("Couldn't load the log.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/log empty/);
  }, T);

  it("F7 Saved searches tab: a failed list is an error", async () => {
    getAnswers({ "/api/saved_searches": new Error("HTTP 500 saved"), "/api/history": [] });
    mount(<History />);
    await openTab("Saved searches");
    expect(await screen.findByText("Couldn't load saved searches.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/No saved searches/);
  }, T);

  it("F7 a 0-byte file shows 0 B, not a blank size", async () => {
    getAnswers({
      "/api/history": [
        { id: 1, site_id: "s1", url: "https://x.invalid/z", status: "done", filename: "zero.mp4", file_size: 0 },
      ],
    });
    mount(<History />);
    const cell = await screen.findByText("zero.mp4");
    const row = cell.closest("tr");
    expect(row?.textContent, "dsq F7: 0-byte size blank").toMatch(/0 B/);
  }, T);

  it("F7 Sites: a failed read shows only the error, not 'No sites yet'", async () => {
    getAnswers({ "/api/sites/v2": new Error("HTTP 500 sites") });
    mount(<Sites />);
    expect(await screen.findByText(/Couldn.t load sites/)).toBeInTheDocument();
    expect(document.body.textContent, "dsq F7: error and empty card together").not.toMatch(
      /No sites yet/,
    );
  }, T);
});

// dsq-spa-errors r2 (R88-D check 2): the hooks poll with retry 0, and a failed
// REFETCH sets isError while TanStack keeps the data already loaded. One failed
// poll must not swap loaded content for "Couldn't load ...".
async function failOnePoll(bad: Record<string, unknown>) {
  getAnswers(bad);
  await qc.refetchQueries().catch(() => {});
  // the failed poll is recorded on a query that still holds its data ...
  await waitFor(() =>
    expect(
      qc.getQueryCache().findAll().some((q) => q.state.status === "error" && q.state.data !== undefined),
    ).toBe(true),
  );
  // ... and React has rendered that state.
  await act(async () => {});
}

describe("r2 a failed poll keeps loaded data", () => {
  it("P1 NeedsReview rows survive one failed poll", async () => {
    getAnswers({ "/api/history": [ROW(1), ROW(2)] });
    mount(<NeedsReview />);
    expect(await screen.findByText(/2 items awaiting review/)).toBeInTheDocument();
    await failOnePoll({ "/api/history": new Error("HTTP 502 blip") });
    const text = document.body.textContent ?? "";
    expect(text, "dsq r2 P1: rows hidden after one failed poll").toMatch(/2 items awaiting review/);
    expect(text).toMatch(/x\.invalid\/v2/);
    expect(text).not.toMatch(/could not load|Couldn't load the review queue/);
  }, T);

  it("P1 wrong action: 'a' approves only a row the page shows", async () => {
    getAnswers({ "/api/history": [ROW(1), ROW(2)] });
    mount(<NeedsReview />);
    expect(await screen.findByText(/2 items awaiting review/)).toBeInTheDocument();
    await failOnePoll({ "/api/history": new Error("HTTP 502 blip") });
    const shown = document.body.textContent ?? "";
    fireEvent.keyDown(window, { key: "a" });
    await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
    const [path, body] = apiPostMock.mock.calls[0] as [string, { urls: string[] }];
    expect(path).toBe("/api/sites/s1/bulk_approve");
    expect(shown, `dsq r2 P1: 'a' approved ${body.urls[0]}, a row the page does not show`).toContain(
      body.urls[0],
    );
  }, T);

  it("P1 control: a failed FIRST read is still an error and 'a' approves nothing", async () => {
    getAnswers({ "/api/history": new Error("HTTP 500 first") });
    mount(<NeedsReview />);
    expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent(
      /Couldn't load the review queue/,
    );
    expect(document.body.textContent).toMatch(/could not load/);
    fireEvent.keyDown(window, { key: "a" });
    await act(async () => {});
    expect(apiPostMock).not.toHaveBeenCalled();
  }, T);

  it("P1 control: the next good poll shows the new rows, no alert", async () => {
    getAnswers({ "/api/history": [ROW(1), ROW(2)] });
    mount(<NeedsReview />);
    expect(await screen.findByText(/2 items awaiting review/)).toBeInTheDocument();
    await failOnePoll({ "/api/history": new Error("HTTP 502 blip") });
    getAnswers({ "/api/history": [ROW(1)] });
    await qc.refetchQueries();
    expect(await screen.findByText(/1 item awaiting review/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  }, T);

  it("P2 Events and Session events survive one failed poll", async () => {
    getAnswers({
      "/api/events_all": { events: [{ ts: "t1", site_id: "s1", kind: "k", message: "EVT-ALPHA" }] },
      "/api/session_history": { events: [{ ts: "t2", site_id: "s1", kind: "k", message: "SESS-BETA" }] },
      "/api/history": [],
    });
    mount(<History />);
    await openTab("Events");
    expect(await screen.findByText("EVT-ALPHA")).toBeInTheDocument();
    expect(await screen.findByText("SESS-BETA")).toBeInTheDocument();
    await failOnePoll({
      "/api/events_all": new Error("HTTP 502"),
      "/api/session_history": new Error("HTTP 502"),
      "/api/history": [],
    });
    const text = document.body.textContent ?? "";
    expect(text, "dsq r2 P2: events hidden after one failed poll").toMatch(/EVT-ALPHA/);
    expect(text, "dsq r2 P2: session events hidden after one failed poll").toMatch(/SESS-BETA/);
    expect(text).not.toMatch(/Couldn't load/);
  }, T);

  it("P3 Logs survive one failed poll", async () => {
    getAnswers({ "/api/logs/tail": { lines: ["LOG-GAMMA"] }, "/api/history": [] });
    mount(<History />);
    await openTab("Logs");
    expect(await screen.findByText(/LOG-GAMMA/)).toBeInTheDocument();
    await failOnePoll({ "/api/logs/tail": new Error("HTTP 502"), "/api/history": [] });
    const text = document.body.textContent ?? "";
    expect(text, "dsq r2 P3: log hidden after one failed poll").toMatch(/LOG-GAMMA/);
    expect(text).not.toMatch(/Couldn't load the log/);
  }, T);

  it("P4 Saved searches survive one failed refetch", async () => {
    getAnswers({
      "/api/saved_searches": { searches: [{ id: 1, name: "SAVED-DELTA", query: "q" }] },
      "/api/history": [],
    });
    mount(<History />);
    await openTab("Saved searches");
    expect(await screen.findByText("SAVED-DELTA")).toBeInTheDocument();
    await failOnePoll({ "/api/saved_searches": new Error("HTTP 502"), "/api/history": [] });
    const text = document.body.textContent ?? "";
    expect(text, "dsq r2 P4: saved searches hidden after one failed refetch").toMatch(/SAVED-DELTA/);
    expect(text).not.toMatch(/Couldn't load saved searches/);
  }, T);
});
