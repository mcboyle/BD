import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// F015 (APP-FEATURE): toggling a switch and toggling it back must leave the
// page clean. The dirty count compared the draft with the raw loaded config,
// so a switch whose key was absent (rendered OFF via `!!draft.k`, or ON via
// `draft.k ?? true`) came back as `false`/`true` vs `undefined` and kept the
// "1 unsaved change" bar and an enabled Save after the round trip.

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

let config: Record<string, unknown> = {};

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function rowSwitch(label: string): HTMLElement {
  const row = screen.getByText(label).closest("[data-srow]");
  const sw = row?.querySelector<HTMLElement>('[role="switch"]');
  if (!sw) throw new Error(`no switch in row ${label}`);
  return sw;
}

function saveButton(): HTMLElement {
  return screen.getByRole("button", { name: "Save" });
}

beforeEach(() => {
  config = {};
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/global_config")) {
        const path = new URL(url, "http://x").pathname;
        return Promise.resolve(jsonResponse(path === "/api/global_config" ? config : {}));
      }
      return new Promise<Response>(() => {});
    }),
  );
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function roundTrip(label: string, startChecked: "true" | "false", filter?: string) {
  mount();
  if (filter) {
    // Collapsed sections reveal their rows while a filter query is active.
    fireEvent.change(await screen.findByLabelText("Filter settings"), {
      target: { value: filter },
    });
  }
  await screen.findByText(label);
  const sw = rowSwitch(label);
  expect(sw).toHaveAttribute("aria-checked", startChecked);
  expect(saveButton()).toBeDisabled();

  fireEvent.click(sw);
  // Control: one toggle is a real change (bar + enabled Save).
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  expect(saveButton()).toBeEnabled();

  fireEvent.click(sw);
  expect(sw).toHaveAttribute("aria-checked", startChecked);
  expect(screen.queryByText(/unsaved change/)).toBeNull();
  expect(saveButton()).toBeDisabled();
}

describe("Settings — switch round trip is clean (F015)", () => {
  it("absent key shown OFF: off -> on -> off clears the bar", async () => {
    await roundTrip("Archive processed files", "false");
  });

  it("absent key shown ON (?? true): on -> off -> on clears the bar", async () => {
    await roundTrip("Slow-query logging", "true", "slow-query");
  });

  it("loaded key: on -> off -> on clears the bar", async () => {
    config = { watch_archive: true };
    await roundTrip("Archive processed files", "true");
  });

  it("loaded null: page loads clean and off -> on -> off clears the bar", async () => {
    // Only a switch's boolean draft maps a null baseline to its fallback; any
    // other null-valued key must still compare null to null (clean on load).
    config = { watch_archive: null, watch_folder: null };
    await roundTrip("Archive processed files", "false");
  });
});
