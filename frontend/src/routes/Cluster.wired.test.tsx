// T8 Cluster RUNTIME contract. Replaces the source-text scan that
// tests/test_t8_cluster_wired.py used to be: every property below is decided by
// rendering the real route and driving real user interactions, so a respelling
// of the hazard in the codebase's own house style cannot evade it.
//
// DECLARED EXCLUSIONS (writes on this page that are NOT in the gated set):
//   /api/fed/set_trust      -- dispatched one-click from the peer-row <select>
//                              onChange (Cluster.tsx). Stated as fact, not as a
//                              product ruling; gating it is a separate row.
//   /api/fed/pending_review -- Approve goes through window.confirm, Reject is
//                              one-click. Also a separate row.
// The sweep exercises both anyway; it just does not fail on them.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock, apiPost: apiPostMock, ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import Cluster from "@/routes/Cluster";
import { installApiFixtures, renderAppAt } from "@/test/wiredGateHarness";

// Every write this page can reach that the T8 contract says must not fire from
// a single interaction.
//
// MATCHED BY PATH IDENTITY, NOT BY PREFIX. sync_pull carries a query string, so
// exact equality alone would be vacuously true forever -- but a bare
// startsWith() is worse: it silently swallows a RENAME. Measured -- with
// startsWith(), mutating the compose endpoint to "/api/edge_deploy/compose_v2"
// left this whole spec green, because the new name still prefixed the old one.
// So: equal, or the endpoint followed by "?".
const GATED = [
  "/api/fed/manual_register",
  "/api/fed/sync_pull",
  "/api/edge_deploy/compose",
  "/api/edge_deploy/all",
  "/api/pair",
  "/api/pair/redeem",
];
const EXCLUDED = ["/api/fed/set_trust", "/api/fed/pending_review"];

// The EXACT interactive inventory the typed sweep is measured over, sorted.
// Shared by tests 2 and 3 so the sweep's denominator and its pin can never
// drift apart -- a magic literal in one of them would let the other shrink.
const INVENTORY = [
  "<select:unlabelled>", "Approve", "Build all artifacts…", "Compose only",
  "Generate pairing code", "Pull now", "Redeem", "Register peer…", "Reject",
  "container image", "pairing token", "peer base url", "peer instance id",
];

function allCalls(): string[] {
  return [...apiGetMock.mock.calls, ...apiPostMock.mock.calls].map((c) => String(c[0]));
}
function isPath(actual: string, endpoint: string): boolean {
  return actual === endpoint || actual.startsWith(`${endpoint}?`);
}
function gatedCalls(): string[] {
  return allCalls().filter((p) => GATED.some((g) => isPath(p, g)));
}

const PEER = { instance_id: "peerA", base_url: "https://peer.a:5555", version: "3.66", trust_tier: "observed" };
const PENDING = [{ id: 7, from_instance: "peerA", site_id: "example.com", received_ts: 0, status: "pending" }];

beforeEach(() => {
  apiGetMock.mockReset(); apiPostMock.mockReset();
  toastMock.mockReset(); toastMock.success.mockReset(); toastMock.error.mockReset();
  focusManager.setFocused(true);
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(false));
  installApiFixtures(apiGetMock, apiPostMock, {
    "/api/fed/peers": { peers: [PEER], drift: [] },
    "/api/fed/status": { peers_active: 1, active_claims: 0, peers_behind: 0 },
    "/api/fed/pending_templates": { pending: PENDING },
    "POST /api/fed/manual_register": { ok: true },
    "POST /api/edge_deploy/all": { ok: true, artifacts: { compose: "x" } },
    "POST /api/pair/redeem": { ok: true, csrf_token: "c", expires_in: 60 },
  });
});

// An ADVERSARIAL client: refetch-on-focus and refetch-on-mount are ON, so a
// read wired as a useQuery really would fire on mount and on focus. The
// harness's freshQueryClient disables both, which would make the "sync_pull
// cannot auto-fire" negative control vacuous.
function renderCluster() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: true, refetchOnMount: "always" },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/cluster"]}><Cluster /></MemoryRouter>
    </QueryClientProvider>,
  );
}

async function ready() {
  await screen.findByText("Register a peer manually");
}

/** Cluster's OWN subtree, excluding AppShell chrome, so the control inventory
 *  does not churn when the shell changes. */
function clusterRoot(): HTMLElement {
  // Anchored on Cluster's OWN header paragraph: AppShell also renders a level-1
  // heading named "Cluster", so the heading role is ambiguous here.
  const own = screen.getByText(/Federation peers, edge deployment artifacts/);
  const root = own.closest("header")?.parentElement;
  expect(root, "Cluster's own content root not found").toBeTruthy();
  return root as HTMLElement;
}
// TAG ALONE IS AN EVADABLE DENOMINATOR, and this is measured rather than
// argued. The first draft of this spec queried "button, select, input,
// textarea"; a mutant binding a one-click /api/edge_deploy/all to a
// <div role="button" tabIndex={0}> ESCAPED the whole battery, because that
// element is neither a tag in the list nor -- therefore -- in the inventory
// pin, so BOTH test 2 and test 3 consented to it. So the typed inventory is
// now ROLE- and TABINDEX-aware as well, and test 3 additionally clicks every
// element in the subtree (see there), which no structural selector can dodge.
const INTERACTIVE = [
  "button", "select", "input", "textarea", "a[href]",
  '[role="button"]', '[role="link"]', '[role="switch"]', '[role="checkbox"]',
  '[role="radio"]', '[role="menuitem"]', '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]', '[role="option"]', '[role="tab"]',
  '[role="combobox"]', '[role="slider"]', '[role="spinbutton"]',
  '[role="textbox"]', "[tabindex]",
].join(", ");

function controls(): HTMLElement[] {
  return Array.from(clusterRoot().querySelectorAll<HTMLElement>(INTERACTIVE));
}
/** Every element under Cluster's root, in document order. */
function elements(): HTMLElement[] {
  return Array.from(clusterRoot().querySelectorAll<HTMLElement>("*"));
}
function nameOf(el: HTMLElement): string {
  // The peer-row <select> carries no accessible label today, so its textContent
  // is the concatenated option list. Give it a stable descriptor instead.
  if (el instanceof HTMLSelectElement) {
    return `<select:${el.getAttribute("aria-label") ?? "unlabelled"}>`;
  }
  return (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || el.tagName).trim();
}

describe("T8 Cluster runtime contract", () => {
  it("resolves at /cluster through the real route table and writes nothing on mount", async () => {
    renderAppAt("/cluster");
    await ready();
    expect(await screen.findByText("peerA")).toBeInTheDocument();
    expect(gatedCalls()).toEqual([]);
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("pins the interactive control inventory the sweep is measured over", async () => {
    renderCluster();
    await ready();
    await screen.findByText("example.com");
    const names = controls().map(nameOf).sort();
    // If this fails, a control was added/renamed/removed: re-derive the sweep
    // instead of editing this list, or the sweep silently stops covering it.
    expect(names).toEqual(INVENTORY);
  });

  it("dispatches no gated write from any single interaction with any control", async () => {
    const user = userEvent.setup();
    renderCluster();
    await ready();
    await screen.findByText("example.com");
    const inventory = controls().map(nameOf);
    expect([...inventory].sort()).toEqual(INVENTORY);

    for (let i = 0; i < inventory.length; i++) {
      const el = controls()[i];
      if (el instanceof HTMLSelectElement) {
        const other = Array.from(el.options).find((o) => o.value !== el.value);
        if (other) await user.selectOptions(el, other.value);
      } else if (el instanceof HTMLInputElement) {
        await user.type(el, "x{Enter}");
      } else {
        await user.click(el);
      }
      // Close whatever the interaction may have armed; neither Escape nor the
      // cancel path may dispatch either.
      await user.keyboard("{Escape}");
      expect(gatedCalls(), `single interaction with "${inventory[i]}" dispatched a gated write`).toEqual([]);
    }
    // The sweep really did reach the excluded writes -- proof it was driving
    // live controls and not a dead subtree.
    expect(allCalls().some((p) => EXCLUDED.some((e) => isPath(p, e)))).toBe(true);
    // and the iteration never silently shrank underneath the loop
    expect(controls().length).toBe(INVENTORY.length);
  });

  it("dispatches no gated write from a click on ANY element, not merely any tagged control", async () => {
    // WHY THIS EXISTS, MEASURED RATHER THAN ARGUED. The typed sweep above is
    // driven by a SELECTOR, and a selector is a denominator choice that a
    // respelling can sit outside. In the first battery against this spec, a
    // mutant dispatching one-click /api/edge_deploy/all from a
    // <div role="button" tabIndex={0}> ESCAPED: it was neither in the tag query
    // nor -- therefore -- in the inventory pin, so both tests consented. The
    // selector is now role-aware, which closes that exact mutant; this test
    // closes the CLASS, by asking every element in Cluster's subtree. A node
    // carrying no handler does nothing when clicked, so the only cost is time.
    renderCluster();
    await ready();
    await screen.findByText("example.com");
    const total = elements().length;
    expect(total).toBeGreaterThan(INVENTORY.length);

    // PRECONDITION ON THE INSTRUMENT ITSELF, and it caught a real bug in this
    // very test. A click on a known one-click control must be OBSERVABLE.
    // react-query's mutate() reaches its mutationFn on a microtask, so the
    // first draft of this sweep -- a synchronous loop with no await -- never
    // flushed one and asserted "no gated write" against a queue that had not
    // run yet. It was vacuous, and green. Every click below is therefore
    // awaited inside act(), and this precondition proves the stimulus lands.
    const reject = within(clusterRoot()).getByRole("button", { name: "Reject" });
    await act(async () => { fireEvent.click(reject); });
    await waitFor(() =>
      expect(
        allCalls().some((p) => isPath(p, "/api/fed/pending_review")),
        `fireEvent.click did not reach a React handler; saw ${JSON.stringify(allCalls())}`,
      ).toBe(true));
    apiGetMock.mockClear();
    apiPostMock.mockClear();

    for (let i = 0; i < total; i++) {
      const el = elements()[i];
      if (!el) continue; // re-queried each turn; the stable-count assert below
      // proves nothing was silently dropped from the sweep
      await act(async () => { fireEvent.click(el); });
      expect(
        gatedCalls(),
        `a click on <${el.tagName.toLowerCase()}> #${i} dispatched a gated write`,
      ).toEqual([]);
    }
    // LIVENESS. Without this the sweep passes just as happily over a subtree of
    // inert nodes: a real one-click write (excluded from the gated set, but a
    // dispatch all the same) must have been observed.
    expect(
      allCalls().some((p) => EXCLUDED.some((e) => isPath(p, e))),
      `the exhaustive sweep reached no live one-click write; it saw ${JSON.stringify(allCalls())}`,
    ).toBe(true);
    expect(elements().length).toBe(total);
  });

  it("gates manual_register A-tier: amber payload, No-default focus, only Yes dispatches", async () => {
    const user = userEvent.setup();
    renderCluster();
    await ready();
    await user.type(screen.getByLabelText("peer instance id"), "peerB");
    await user.type(screen.getByLabelText("peer base url"), "https://peer.b:5555");
    await user.click(screen.getByRole("button", { name: "Register peer…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/TRUST PEER\s+peerB\s+@\s+https:\/\/peer\.b:5555/)).toBeInTheDocument();
    const no = within(dialog).getByRole("button", { name: "No, cancel" });
    expect(document.activeElement).toBe(no);
    expect(gatedCalls()).toEqual([]);

    await user.click(no);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(gatedCalls()).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Register peer…" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, proceed" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/fed/manual_register", {
        instance_id: "peerB", base_url: "https://peer.b:5555",
      }));
    expect(gatedCalls()).toEqual(["/api/fed/manual_register"]);
  });

  it("gates edge_deploy/all A-tier: amber payload, No-default focus, only Yes dispatches", async () => {
    const user = userEvent.setup();
    renderCluster();
    await ready();
    await user.click(screen.getByRole("button", { name: "Build all artifacts…" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("BUILD FLEET ARTIFACTS")).toBeInTheDocument();
    const no = within(dialog).getByRole("button", { name: "No, cancel" });
    expect(document.activeElement).toBe(no);
    expect(gatedCalls()).toEqual([]);
    await user.click(no);
    expect(gatedCalls()).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Build all artifacts…" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Yes, proceed" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/edge_deploy/all", { image: "bulkdownloader:latest" }));
    expect(gatedCalls()).toEqual(["/api/edge_deploy/all"]);
  });

  it("never auto-fires sync_pull on mount or window focus, and fires it exactly once when confirmed", async () => {
    const user = userEvent.setup();
    renderCluster();
    await ready();
    const pullCalls = () => allCalls().filter((p) => isPath(p, "/api/fed/sync_pull"));
    const peerCalls = () => allCalls().filter((p) => isPath(p, "/api/fed/peers"));
    expect(pullCalls()).toEqual([]);

    // POSITIVE CONTROL for the stimulus: a real useQuery on this same page DOES
    // refetch under it. Without this the negative half passes on a dead event.
    const before = peerCalls().length;
    await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true); });
    await waitFor(() => expect(peerCalls().length).toBeGreaterThan(before));
    expect(pullCalls()).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Pull now" }));
    expect(pullCalls()).toEqual([]);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(pullCalls().length).toBe(1));
    expect(pullCalls()[0]).toMatch(/^\/api\/fed\/sync_pull\?since_id=0&limit=500$/);
  });

  it("keeps every gated endpoint reachable, so the sweep can never go vacuous", async () => {
    // WITHOUT THIS the sweep is satisfied by a GATED list that matches nothing:
    // rename an endpoint and `gatedCalls()` returns [] forever. Each prefix must
    // be OBSERVED firing through its own confirm dialog.
    const user = userEvent.setup();
    renderCluster();
    await ready();
    const confirmVia = async (name: RegExp | string) => {
      await user.click(screen.getByRole("button", { name }));
      const d = await screen.findByRole("dialog");
      const yes = within(d).queryByRole("button", { name: "Yes, proceed" })
        ?? within(d).getByRole("button", { name: "Confirm" });
      await user.click(yes);
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    };
    await user.type(screen.getByLabelText("peer instance id"), "peerB");
    await user.type(screen.getByLabelText("peer base url"), "https://peer.b:5555");
    await confirmVia("Register peer…");
    await confirmVia("Pull now");
    await confirmVia("Compose only");
    await confirmVia("Build all artifacts…");
    await confirmVia("Generate pairing code");
    await user.type(screen.getByLabelText("pairing token"), "T");
    await confirmVia("Redeem");

    const seen = allCalls();
    for (const g of GATED) {
      await waitFor(() =>
        expect(allCalls().some((p) => isPath(p, g)), `gated endpoint never observed: ${g}`).toBe(true));
    }
    expect(seen.length).toBeGreaterThan(0);
  });

  it("treats the pairing token as a write-only secret: masked, never seeded, cleared only on success", async () => {
    const user = userEvent.setup();
    renderCluster();
    await ready();
    const token = screen.getByLabelText("pairing token") as HTMLInputElement;
    expect(token.type).toBe("password");
    expect(token.value).toBe("");            // never seeded from a GET

    await user.type(token, "TOKEN-123");
    await user.click(screen.getByRole("button", { name: "Redeem" }));
    expect(gatedCalls()).toEqual([]);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("/api/pair/redeem", { token: "TOKEN-123" }));
    await waitFor(() => expect((screen.getByLabelText("pairing token") as HTMLInputElement).value).toBe(""));

    // OVER-SENSITIVITY CONTROL: a FAILED redeem must NOT wipe the operator's
    // token, so "cleared" cannot be satisfied by clearing unconditionally.
    apiPostMock.mockImplementation(() => Promise.resolve({ ok: false, error: "expired" }));
    await user.type(screen.getByLabelText("pairing token"), "TOKEN-456");
    await user.click(screen.getByRole("button", { name: "Redeem" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("expired"));
    expect((screen.getByLabelText("pairing token") as HTMLInputElement).value).toBe("TOKEN-456");
  });
});
