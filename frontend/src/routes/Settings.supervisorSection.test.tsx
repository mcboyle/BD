// Backlog row 240 (v3.66.1240), the STRUCTURAL spec -- and, deliberately, the
// TRANSFORM CONTROL for this cut's mutation battery.
//
// bd-mutate has no parse handler for .tsx (toolchain/bin/bd-mutate _validate
// falls through to "unknown type: not our place to guess"), so a mutant that
// merely broke the file would score CAUGHT on "named catcher failed" alone.
// This spec renders and drives the same mutated module WITHOUT asserting
// anything about seeding, so re-pointing a seeding mutant at it must ESCAPE.
// That is what proves the other two specs discriminate on BEHAVIOUR rather than
// on compilability. It is also the `preserves` target for the over-correction
// mutants, because nothing here reads or writes the three seeded fields'
// values.
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

const FIXTURES: Record<string, unknown> = {
  "/api/settings/env/effective": { env: [] },
  "/api/settings/envfile": { env: [], path: "/x/.env", exists: true, writable: true },
  "/api/supervisor/status": {
    ok: true,
    stats: {
      enabled: true,
      config: { global_bps: 12500000, per_site_bps: { "example.com": 500000 } },
    },
  },
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

function configurePosts(): unknown[][] {
  return apiPostMock.mock.calls.filter(
    (c) => String(c[0]) === "/api/supervisor/configure",
  );
}

async function openSupervisor(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(document.getElementById("supervisor-throttle")).toBeTruthy(),
  );
  const header = within(section()).getAllByRole("button")[0];
  await user.click(header);
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

describe("row 240 -- the supervisor throttle section still works as a section", () => {
  it("is collapsed by default and mounts its three controls when opened", async () => {
    const user = userEvent.setup();
    mount(freshQueryClient());
    await waitFor(() =>
      expect(document.getElementById("supervisor-throttle")).toBeTruthy(),
    );
    // ui/collapsible.tsx renders `{shown && children}`, so a closed section has
    // no controls at all. Assert the closed state before opening, or "the
    // control appeared" proves nothing about the click.
    expect(
      screen.queryByRole("switch", { name: "Supervisor throttle enabled" }),
    ).toBeNull();

    await openSupervisor(user);
    expect(
      screen.getByRole("switch", { name: "Supervisor throttle enabled" }),
    ).toBeInTheDocument();
    expect(within(section()).getByRole("spinbutton")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Per-site bytes per second JSON"),
    ).toBeInTheDocument();
    expect(
      within(section()).getByRole("button", { name: "Apply" }),
    ).toBeInTheDocument();
  });

  it("Apply is confirm-gated: the dialog opens and Cancel POSTs nothing", async () => {
    const user = userEvent.setup();
    mount(freshQueryClient());
    await openSupervisor(user);

    await user.click(within(section()).getByRole("button", { name: "Apply" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Apply supervisor throttle"),
    ).toBeInTheDocument();
    // Scoped to the configure path on purpose: the page POSTs /api/ai/models
    // on mount, so a bare "the spy was never called" would fail for an
    // unrelated reason and never reach the claim.
    expect(configurePosts()).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(configurePosts()).toHaveLength(0);
  });
});
