// Row 515 -- the paired-extensions card must not render a 503 as "no extensions
// paired".
//
// pairedQ was declared with no error branch and no pending branch, and the card
// rendered a bare truthiness test on pairedQ.data, so the exact string "No
// extensions paired" appeared identically for a successful EMPTY inventory, the
// INITIAL PENDING request, and a query FAILURE -- because apiGet throws ApiError
// on any non-2xx and pairedQ.data stays undefined.
//
// Since v3.66.1373 the backing route api_secrets_extension_list_paired answers
// HTTP 503 with {state: "unreadable"} through _vault_store_unreadable, a refusal
// written precisely so that NO route reports an empty inventory over an unread
// token store. The UI converted that refusal straight back into the empty
// sentence. Each listed row's only control is Revoke, so the decision this text
// drives is whether anything needs revoking.
//
// CONTRACT: the empty-inventory sentence is reachable ONLY from a 2xx carrying
// an extensions array of length 0. An unmeasured inventory renders as
// unmeasured, never as zero.
//
// THE MIRROR DEFECT IS TESTED TOO: the two negative controls prove the new
// branches did not simply make every state fail -- an empty 2xx still renders
// the sentence exactly once with zero Revoke controls, and a 2xx with two
// extensions still renders "2 paired" with exactly two Revoke controls.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Secrets } from "./Secrets";
import { ApiError } from "@/lib/api-client";

const PAIRED = "/api/secrets/extension/list_paired";
const EMPTY_SENTENCE = "No extensions paired";

interface Scripted {
  status: number;
  body: unknown;
}

/**
 * Route the stub BY URL.
 *
 * A stub that answered every endpoint the same way would put statusQ into its
 * own error state, and its error card would then pollute the exact-count
 * assertions below -- a green (or red) for a reason that is not the subject.
 * Only list_paired is scripted per case; every sibling gets a benign 2xx.
 */
function stubFetch(paired: Scripted | "pending") {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes(PAIRED)) {
        if (paired === "pending") {
          return new Promise<Response>(() => {}); // never settles
        }
        return Promise.resolve({
          ok: paired.status >= 200 && paired.status < 300,
          status: paired.status,
          json: () => Promise.resolve(paired.body),
        } as Response);
      }
      // Vault status and any other sibling: a measured, healthy answer.
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            ok: true,
            backend: "master_password",
            is_unlocked: true,
            is_initialized: true,
            plaintext_count: 0,
            plaintext_sites: [],
            stored_keys: [],
            keyring_available: true,
            crypto_available: true,
            tokens: [],
          }),
      } as Response);
    }),
  );
  return calls;
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: {
      // Load-bearing: with retries on, the 503 case would keep re-requesting
      // and the query would never settle into `error` for the assertions.
      queries: { retry: false, refetchInterval: false },
      mutations: { retry: false },
    },
  });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/secrets"]}>
        <Secrets />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, view };
}

function countText(container: HTMLElement, needle: string): number {
  return (container.textContent ?? "").split(needle).length - 1;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Row 515 -- an unmeasured paired inventory renders as unmeasured", () => {
  it("renders the unreadable-store refusal, not the empty sentence, on a 503", async () => {
    const calls = stubFetch({
      status: 503,
      body: {
        ok: false,
        state: "unreadable",
        error: "extension token store could not be decoded",
      },
    });
    const { qc, view } = mount();

    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("error");
    });

    // PRECONDITIONS, asserted before any verdict on what was rendered.
    const state = qc.getQueryState(["secrets-paired"]);
    expect(state?.data).toBeUndefined();
    expect(state?.error).not.toBeNull();
    expect(state?.error).toBeInstanceOf(ApiError);
    expect((state?.error as ApiError).status).toBe(503);
    expect((state?.error as ApiError).body).toMatchObject({ state: "unreadable" });
    expect(calls.filter((u) => u.includes(PAIRED)).length).toBeGreaterThan(0);

    // THE CONTRACT.
    expect(countText(view.container, EMPTY_SENTENCE)).toBe(0);
    expect(countText(view.container, "could not be read")).toBe(1);
    // The server's OWN words, carried rather than paraphrased.
    expect(view.container.textContent).toContain(
      "extension token store could not be decoded",
    );
    expect(view.container.textContent).toContain("unreadable");
  });

  it("distinguishes an ordinary failure from the unreadable store", async () => {
    // A7: two refusals that lead to different actions must not collapse into
    // one diagnostic.
    stubFetch({ status: 500, body: { ok: false, error: "boom" } });
    const { qc, view } = mount();

    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("error");
    });

    expect(countText(view.container, EMPTY_SENTENCE)).toBe(0);
    expect(countText(view.container, "could not be read")).toBe(1);
    expect(view.container.textContent).toContain("HTTP 500");
    expect(view.container.textContent).not.toContain("unreadable");
  });

  it("renders a not-yet-known state, not the empty sentence, while pending", async () => {
    stubFetch("pending");
    const { qc, view } = mount();

    await waitFor(() => {
      expect(screen.getByText(/Loading paired extensions/)).toBeInTheDocument();
    });

    // PRECONDITION: the request genuinely has not settled.
    const state = qc.getQueryState(["secrets-paired"]);
    expect(state?.status).toBe("pending");
    expect(state?.data).toBeUndefined();
    expect(state?.error).toBeNull();

    expect(countText(view.container, EMPTY_SENTENCE)).toBe(0);
    expect(countText(view.container, "Loading paired extensions")).toBe(1);
  });
});

describe("Row 515 -- a stale success does not survive a later refusal", () => {
  it("replaces a previously-measured inventory with the refusal, controls included", async () => {
    // THE DESIGN CHOICE, PINNED RATHER THAN LEFT INCIDENTAL. react-query keeps
    // `data` from an earlier success when a later refetch fails, so the card
    // could show "2 paired" and two Revoke buttons over a store that can no
    // longer be read. It does not: the contract says an unmeasured inventory
    // renders as unmeasured, and a count carried over from a previous request
    // is unmeasured NOW.
    //
    // No capability is lost by hiding the controls: api_secrets_extension_revoke
    // answers the SAME 503 through _vault_store_unreadable, so a Revoke offered
    // here could not succeed -- and {"ok": true, "removed": false} over an
    // unreadable store is the most dangerous fail-open on this surface, since
    // the operator is revoking a token they believe leaked.
    let fail = false;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes(PAIRED)) {
          if (fail) {
            return Promise.resolve({
              ok: false,
              status: 503,
              json: () =>
                Promise.resolve({ ok: false, state: "unreadable", error: "sealed" }),
            } as Response);
          }
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                ok: true,
                extensions: [
                  { id: "ext-a", label: "Laptop", issued_at: 1, last_used_at: 2 },
                  { id: "ext-b", label: "Desktop", issued_at: 3, last_used_at: 0 },
                ],
              }),
          } as Response);
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ok: true, backend: "master_password",
            is_unlocked: true, is_initialized: true, plaintext_count: 0,
            plaintext_sites: [], stored_keys: [], keyring_available: true,
            crypto_available: true, tokens: [] }),
        } as Response);
      }),
    );

    const { qc, view } = mount();

    // PRECONDITION: a real success first, with both rows rendered.
    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("success");
    });
    expect(countText(view.container, "2 paired")).toBe(1);
    expect(screen.queryAllByRole("button", { name: "Revoke" })).toHaveLength(2);

    fail = true;
    await qc.refetchQueries({ queryKey: ["secrets-paired"] });

    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("error");
    });
    // The stale success is still in the cache -- so this is a real choice.
    expect(qc.getQueryState(["secrets-paired"])?.data).toBeDefined();

    expect(countText(view.container, "could not be read")).toBe(1);
    expect(countText(view.container, "2 paired")).toBe(0);
    expect(countText(view.container, EMPTY_SENTENCE)).toBe(0);
    expect(screen.queryAllByRole("button", { name: "Revoke" })).toHaveLength(0);
  });
});

describe("Row 515 negative controls -- the measured states still read measured", () => {
  it("a 2xx with an empty array still renders the empty sentence exactly once", async () => {
    stubFetch({ status: 200, body: { ok: true, extensions: [] } });
    const { qc, view } = mount();

    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("success");
    });

    // PRECONDITION: this really is a measured empty inventory.
    expect(qc.getQueryState(["secrets-paired"])?.data).toEqual({
      ok: true,
      extensions: [],
    });

    expect(countText(view.container, EMPTY_SENTENCE)).toBe(1);
    expect(countText(view.container, "could not be read")).toBe(0);
    expect(countText(view.container, "Loading paired extensions")).toBe(0);
    expect(screen.queryAllByRole("button", { name: "Revoke" })).toHaveLength(0);
  });

  it("a 2xx with two extensions still renders '2 paired' and two Revoke controls", async () => {
    stubFetch({
      status: 200,
      body: {
        ok: true,
        extensions: [
          { id: "ext-a", label: "Laptop", issued_at: 1, last_used_at: 2 },
          { id: "ext-b", label: "Desktop", issued_at: 3, last_used_at: 0 },
        ],
      },
    });
    const { qc, view } = mount();

    await waitFor(() => {
      expect(qc.getQueryState(["secrets-paired"])?.status).toBe("success");
    });

    expect(countText(view.container, "2 paired")).toBe(1);
    expect(countText(view.container, EMPTY_SENTENCE)).toBe(0);
    expect(countText(view.container, "could not be read")).toBe(0);
    expect(screen.queryAllByRole("button", { name: "Revoke" })).toHaveLength(2);
    expect(view.container.textContent).toContain("ext-a");
    expect(view.container.textContent).toContain("ext-b");
  });
});
