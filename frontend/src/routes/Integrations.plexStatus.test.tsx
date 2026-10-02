// O1671 R8b / UIUX F029 U1: the "Plex · per-site" card printed its backend
// status as a raw JSON dump -- `status: {"available":true,"import_error":null,
// "operations":[...]}` -- and, when /api/plex_advanced/status failed, as
// `status: {}`, which reads as a measured answer when nothing was measured.
//
// This spec renders the REAL Integrations route through the REAL hook with the
// exact payload shapes bulk_downloader/plex_advanced.py::status_dict and
// app_plex.py::api_plex_adv_status emit (200 and 500).
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, apiGet: apiGetMock, apiPost: apiPostMock };
});
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { ApiError } from "@/lib/api-client";
import { Integrations } from "@/routes/Integrations";
import { renderWired } from "@/test/wiredGateHarness";

const STATUS = "/api/plex_advanced/status";
const OPERATIONS = [
  "server_info", "library_stats", "recently_added",
  "on_deck", "search", "mark_watched", "mark_unwatched",
];
const IMPORT_WORDS = "o1671-synthetic-no-module-named-plexapi";
const TRANSPORT_WORDS = "o1671-synthetic-status-failed";

function installStatusOutcome(outcome: { reject?: unknown; resolve?: unknown }) {
  apiGetMock.mockImplementation((path: string) => {
    if (String(path) === STATUS) {
      return "reject" in outcome
        ? Promise.reject(outcome.reject)
        : Promise.resolve(outcome.resolve);
    }
    return Promise.resolve({});
  });
  apiPostMock.mockImplementation(() => Promise.resolve({ ok: true }));
}

/** The Plex card's own region, so other panels on this busy route cannot
 *  satisfy or defeat an assertion about the Plex status line. */
async function plexCard(): Promise<HTMLElement> {
  const heading = await screen.findByText(/Plex . per-site/);
  const region = heading.parentElement;
  expect(region).not.toBeNull();
  return region as HTMLElement;
}

/** Card text once the status query has settled (rendered either way: the old
 *  JSON dump or the readable line), so a RED names the dump, not a timeout. */
async function settledCard(): Promise<string> {
  const card = await plexCard();
  await waitFor(() => expect(apiGetMock.mock.calls.map((c) => String(c[0]))).toContain(STATUS));
  await waitFor(() => expect(card.textContent || "").toMatch(/available|could not be read|\{\}/));
  return card.textContent || "";
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Integrations · Plex status line is readable, not a JSON dump", () => {
  it("renders backend availability and operations without raw JSON", async () => {
    installStatusOutcome({ resolve: { available: true, import_error: null, operations: OPERATIONS } });
    renderWired(<Integrations />);
    const text = await settledCard();

    expect(text).not.toMatch(/[{}"]/);
    expect(text).not.toContain("import_error");
    expect(text).toMatch(/plexapi backend: available/);
    for (const op of OPERATIONS) expect(text).toContain(op.replace(/_/g, " "));
  });

  it("names the import error and the install hint when plexapi is missing", async () => {
    installStatusOutcome({
      resolve: { available: false, import_error: IMPORT_WORDS, install_with: "pip install plexapi", operations: OPERATIONS },
    });
    renderWired(<Integrations />);
    const text = await settledCard();

    expect(text).not.toMatch(/[{}"]/);
    expect(text).toMatch(/plexapi backend: not available/);
    expect(text).toContain(IMPORT_WORDS);
    expect(text).toContain("pip install plexapi");
  });

  it("says the status could not be read when the endpoint fails, never an empty {}", async () => {
    const err = new ApiError(`GET ${STATUS} → 500`, 500, { available: false, error: TRANSPORT_WORDS });
    expect(err.message).toContain(TRANSPORT_WORDS);
    installStatusOutcome({ reject: err });
    renderWired(<Integrations />);
    const text = await settledCard();

    expect(text).not.toContain("{}");
    expect(text).toMatch(/status could not be read/);
    expect(text).toContain(TRANSPORT_WORDS);
  });
});
