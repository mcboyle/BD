// Row 553 / row 488, GUI half: /api/secrets/usage refuses with 409 and the
// secret-usage panel answers "No stored secrets." anyway.
//
// The BACKEND half of this defect was fixed at b562cf3e: api_secrets_usage no
// longer launders SecretsUnreadableError / SecretsIntegrityError into an
// affirmative ok:true empty inventory, it answers 409 with a named state.  The
// laundering then moved one seam downstream and survived there.  apiGet throws
// ApiError on !response.ok (frontend/src/lib/api-client.ts), TanStack Query
// therefore leaves `data` undefined, Integrations.tsx passed only `data` and
// `loading` to <SecretsUsageList>, and the panel's `data?.stored_keys ?? []`
// turned an UNMEASURED inventory into a measured-empty one and printed the
// operator-facing sentence row 553 names verbatim.  CLAUDE.md A7: an inventory
// that could not be read is UNKNOWN, never zero -- and a diagnostic that
// collapses distinct failures costs the investigation, so unreadable,
// integrity_error and a transport failure must not read alike.
//
// This spec renders the REAL Integrations route through the REAL hook, so the
// wiring that drops the error is inside its denominator; a component-only spec
// would pass while Integrations.tsx still discarded the refusal.
//
// Every literal below is a documented zero-entropy synthetic value.  Nothing
// here is a credential and the endpoint never returns one.
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});
// importOriginal keeps the REAL ApiError: the fix reads .status/.body/.message,
// so a stub error class would let the panel pass over a shape production never
// produces.
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, apiGet: apiGetMock, apiPost: apiPostMock };
});
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { ApiError } from "@/lib/api-client";
import { Integrations } from "@/routes/Integrations";
import { renderWired } from "@/test/wiredGateHarness";

const USAGE = "/api/secrets/usage";

// Zero-entropy synthetic diagnostics standing in for the server's own words.
const INTEGRITY_WORDS = "row553-synthetic-ciphertexts-container-is-malformed";
const UNREADABLE_WORDS = "row553-synthetic-vault-file-could-not-be-parsed";
const TRANSPORT_WORDS = "row553-synthetic-transport-failure";
const KEY_A = "row553-synthetic-key-a";
const KEY_B = "row553-synthetic-key-b";

// The exact 409 bodies bulk_downloader/app_secrets.py::api_secrets_usage emits.
function refusalBody(state: string, words: string) {
  return {
    ok: false,
    state,
    stored_keys: null,
    usage: null,
    unreferenced: null,
    rotation: null,
    error: words,
  };
}

function refusal(state: string, words: string): ApiError {
  return new ApiError(`GET ${USAGE} → 409`, 409, refusalBody(state, words));
}

/** apiGet resolves {} for every path except USAGE, which gets `outcome`. */
function installUsageOutcome(outcome: { reject?: unknown; resolve?: unknown }) {
  apiGetMock.mockImplementation((path: string) => {
    if (String(path) === USAGE) {
      return "reject" in outcome
        ? Promise.reject(outcome.reject)
        : Promise.resolve(outcome.resolve);
    }
    return Promise.resolve({});
  });
  apiPostMock.mockImplementation(() => Promise.resolve({ ok: true }));
}

function usageCalls(): string[] {
  return apiGetMock.mock.calls
    .map((c) => String(c[0]))
    .filter((p) => p === USAGE);
}

/** The panel's own region, so an unrelated empty state elsewhere on this very
 *  busy route can never satisfy or defeat an assertion about the vault. */
async function usagePanel(): Promise<HTMLElement> {
  const heading = await screen.findByText(/Secret usage . references only/i);
  const region = heading.parentElement;
  expect(region).not.toBeNull();
  return region as HTMLElement;
}

const CONFIDENT_EMPTY = "No stored secrets.";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Integrations · secret usage panel · an unverifiable answer is not an answer", () => {
  it("does not answer 'No stored secrets.' when the vault refused with 409 integrity_error", async () => {
    const err = refusal("integrity_error", INTEGRITY_WORDS);

    // PRECONDITION 1: the fixture really is the production refusal shape.
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect((err.body as { state: string }).state).toBe("integrity_error");
    expect((err.body as { stored_keys: unknown }).stored_keys).toBeNull();
    // ApiError folds the server's own words into .message; the fix must be able
    // to reach them.
    expect(err.message).toContain(INTEGRITY_WORDS);

    installUsageOutcome({ reject: err });
    renderWired(<Integrations />);

    const panel = await usagePanel();

    // PRECONDITION 2: the seam is nonzero -- the route really asked the vault,
    // and really received the refusal.  Without this the verdict below could be
    // green over a panel that was never rendered or never queried.
    await waitFor(() => expect(usageCalls().length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(panel.textContent || "").not.toMatch(/Loading secret usage/i),
    );

    // VERDICT: the confident empty must be gone ...
    expect(panel.textContent || "").not.toContain(CONFIDENT_EMPTY);
    // ... replaced by an explicit unmeasured state that carries the server's
    // own words and its named state token.
    expect(panel.textContent || "").toMatch(/could not be (read|measured)|unknown|unavailable/i);
    expect(panel.textContent || "").toContain(INTEGRITY_WORDS);
    expect(panel.textContent || "").toContain("integrity_error");
  });

  it("distinguishes unreadable from integrity_error (A7: a diagnostic must not collapse)", async () => {
    installUsageOutcome({ reject: refusal("unreadable", UNREADABLE_WORDS) });
    renderWired(<Integrations />);
    const panel = await usagePanel();
    await waitFor(() => expect(usageCalls().length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(panel.textContent || "").toContain(UNREADABLE_WORDS),
    );
    const text = panel.textContent || "";
    expect(text).not.toContain(CONFIDENT_EMPTY);
    expect(text).toContain("unreadable");
    // The two refusals are genuinely different messages, not one word swapped
    // into a shared "unavailable".
    expect(text).not.toContain("integrity_error");
    expect(text).not.toContain(INTEGRITY_WORDS);
  });

  it("distinguishes a transport failure from either named vault refusal", async () => {
    // No ApiError, no status, no body: fetch itself rejected.
    installUsageOutcome({ reject: new TypeError(TRANSPORT_WORDS) });
    renderWired(<Integrations />);
    const panel = await usagePanel();
    await waitFor(() => expect(usageCalls().length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(panel.textContent || "").toContain(TRANSPORT_WORDS),
    );
    const text = panel.textContent || "";
    expect(text).not.toContain(CONFIDENT_EMPTY);
    // It must not borrow the vault's vocabulary for a failure that never
    // reached the vault.
    expect(text).not.toContain("integrity_error");
    expect(text).not.toContain("unreadable");
    expect(text).toMatch(/did not complete|no response|request failed/i);
  });

  it("NEGATIVE CONTROL: a healthy vault still lists its keys", async () => {
    installUsageOutcome({
      resolve: {
        ok: true,
        stored_keys: [KEY_A, KEY_B],
        usage: { [KEY_A]: ["siteRow553"], [KEY_B]: [] },
        unreferenced: [KEY_B],
        rotation: {},
      },
    });
    renderWired(<Integrations />);
    const panel = await usagePanel();
    await waitFor(() => expect(panel.textContent || "").toContain(KEY_A));
    const text = panel.textContent || "";
    expect(text).toContain(KEY_B);
    expect(text).toContain("siteRow553");
    expect(text).toContain("unreferenced");
    // The guard must not fire over a vault that answered.
    expect(text).not.toMatch(/could not be (read|measured)/i);
    expect(text).not.toContain(CONFIDENT_EMPTY);
  });

  it("NEGATIVE CONTROL: a MEASURED empty vault still reads 'No stored secrets.'", async () => {
    // The mirror defect this repo has actually shipped before: a guard that
    // refuses legitimate work.  ok:true with an empty inventory is a real
    // measurement and must keep its plain answer.
    installUsageOutcome({
      resolve: { ok: true, stored_keys: [], usage: {}, unreferenced: [], rotation: {} },
    });
    renderWired(<Integrations />);
    const panel = await usagePanel();
    await waitFor(() =>
      expect(panel.textContent || "").not.toMatch(/Loading secret usage/i),
    );
    await waitFor(() => expect(usageCalls().length).toBeGreaterThan(0));
    const text = panel.textContent || "";
    expect(text).toContain(CONFIDENT_EMPTY);
    expect(text).not.toMatch(/could not be (read|measured)|unknown|unavailable/i);
  });
});
