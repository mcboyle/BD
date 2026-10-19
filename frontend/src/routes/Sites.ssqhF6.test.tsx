// ssqh-f6 (FINDINGS spa-sites-queue-history F6): the "Paused" tab lists only
// sites that are actually paused, not every site that is not running, and a
// bulk Pause the server reports as a no-op (applied_to 0, skipped) toasts
// "nothing to pause", never "Paused 0 sites". Real Sites route; the transport
// is mocked with the bulk answer shape of app_sites_id_core.api_sites_v2_bulk.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
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

import { Sites } from "@/routes/Sites";
import { freshQueryClient } from "@/test/wiredGateHarness";

const T = 20000;
const TAG = "SSQH-F6";

const SITE = (site_id: string, state: string) => ({
  site_id,
  name: `Site ${site_id}`,
  avatar_color: "#888888",
  state,
  hold_reason: "",
  auth_state: "ok",
  captcha_pending: false,
  downloaded_total: 0,
  active_workers: 0,
  last_event_ts: 0,
  last_event_age: "",
});

function serveSites(sites: ReturnType<typeof SITE>[]) {
  apiGetMock.mockImplementation((path: string) =>
    String(path).startsWith("/api/sites/v2")
      ? Promise.resolve({ ok: true, sites, count: sites.length, ts: 0 })
      : Promise.resolve({}),
  );
}

function mount() {
  return render(
    <QueryClientProvider client={freshQueryClient()}>
      <MemoryRouter>
        <Sites />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Every text any toast variant was called with.
function toastTexts(): string[] {
  const fns = [toastMock, toastMock.success, toastMock.error, toastMock.warning,
    toastMock.info, toastMock.message];
  return fns.flatMap((f) => f.mock.calls.map((c) => String(c[0])));
}

async function bulkPause(answer: Record<string, unknown>) {
  apiPostMock.mockResolvedValue(answer);
  const user = userEvent.setup();
  mount();
  await screen.findByText("Site i1");
  await user.click(screen.getByRole("button", { name: "Select sites for bulk actions" }));
  await user.click(screen.getByText("Select all visible"));
  await user.click(screen.getByRole("button", { name: "Pause selected sites" }));
  await waitFor(() => expect(apiPostMock).toHaveBeenCalled());
  await waitFor(() => expect(toastTexts().length).toBeGreaterThan(0));
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
  for (const f of [toastMock, toastMock.success, toastMock.error, toastMock.warning,
    toastMock.info, toastMock.message]) f.mockReset();
});

describe("ssqh-f6 Sites Paused tab and bulk Pause toast", () => {
  it("the Paused tab lists paused sites only, not idle/stopped ones", async () => {
    serveSites([
      SITE("r1", "running"),
      SITE("i1", "idle"),
      SITE("s1", "stopped"),
      SITE("p1", "paused"),
      SITE("w1", "window_paused"),
    ]);
    const user = userEvent.setup();
    mount();
    await screen.findByText("Site i1");
    const tab = screen.getByRole("button", { name: /^Filter: Paused/ });
    expect(tab, `${TAG} Paused count includes not-running sites`).toHaveAccessibleName(
      "Filter: Paused (2)",
    );
    await user.click(tab);
    await waitFor(() => expect(screen.queryByText("Site i1")).toBeNull());
    expect(screen.queryByText("Site s1"), `${TAG} stopped site listed as Paused`).toBeNull();
    expect(screen.queryByText("Site r1")).toBeNull();
    expect(screen.getByText("Site p1")).toBeInTheDocument();
    expect(screen.getByText("Site w1")).toBeInTheDocument();
  }, T);

  it("a no-op bulk Pause toasts 'nothing to pause', not 'Paused 0 sites'", async () => {
    serveSites([SITE("i1", "idle")]);
    await bulkPause({
      ok: true,
      action: "pause",
      applied_to: 0,
      total: 1,
      errors: [],
      skipped: [{ site_id: "i1", state: "idle" }],
    });
    const texts = toastTexts();
    expect(texts.some((t) => /nothing to pause/i.test(t)), `${TAG} toasts ${JSON.stringify(texts)}`)
      .toBe(true);
    expect(texts.some((t) => /^Paused 0\b/.test(t) || /failed/.test(t))).toBe(false);
  }, T);

  it("a partial bulk Pause names the skipped sites, not '0 failed'", async () => {
    serveSites([SITE("r1", "running"), SITE("i1", "idle")]);
    await bulkPause({
      ok: true,
      action: "pause",
      applied_to: 1,
      total: 2,
      errors: [],
      skipped: [{ site_id: "i1", state: "idle" }],
    });
    const texts = toastTexts();
    expect(texts.some((t) => /^Paused 1 site\b/.test(t) && /1 not running/.test(t)),
      `${TAG} toasts ${JSON.stringify(texts)}`).toBe(true);
    expect(texts.some((t) => /0 failed/.test(t))).toBe(false);
  }, T);

  it("control: a bulk Pause that applied to every site still toasts success", async () => {
    serveSites([SITE("r1", "running"), SITE("i1", "running")]);
    await bulkPause({
      ok: true,
      action: "pause",
      applied_to: 2,
      total: 2,
      errors: [],
      skipped: [],
    });
    expect(toastMock.success).toHaveBeenCalledWith("Paused 2 sites");
  }, T);
});
