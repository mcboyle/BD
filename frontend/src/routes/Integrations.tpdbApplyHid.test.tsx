// o1671-a13: TPDB apply must only write the lookup result onto the history row
// it was looked up for.
//
// The per-history-row card shares one "history id" input between "TPDB
// lookup" and "Apply metadata".  On the defective route, confirmRun paired
// pending.hid (the CURRENT input) with tpdbLookup.data?.result (whatever row
// was looked up LAST), so typing a new id after a lookup wrote row A's .nfo
// metadata onto row B.  This spec renders the REAL route through the REAL
// hooks and asserts the apply POST never carries A's metadata to B.
//
// Every literal below is a documented zero-entropy synthetic value.
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, apiGet: apiGetMock, apiPost: apiPostMock };
});
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { Integrations } from "@/routes/Integrations";
import { renderWired } from "@/test/wiredGateHarness";

const HID_A = 111;
const HID_B = 222;
const META_A = { title: "a13-synthetic-row-a-title" };
const META_B = { title: "a13-synthetic-row-b-title" };

const applyCalls = () =>
  apiPostMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/tpdb/apply/"));
const lookupCalls = () =>
  apiPostMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/tpdb/lookup/"));

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  apiGetMock.mockImplementation(() => Promise.resolve({}));
  apiPostMock.mockImplementation((path: string) => {
    const p = String(path);
    if (p === `/api/tpdb/lookup/${HID_A}`) {
      return Promise.resolve({ ok: true, history_id: HID_A, result: META_A });
    }
    if (p === `/api/tpdb/lookup/${HID_B}`) {
      return Promise.resolve({ ok: true, history_id: HID_B, result: META_B });
    }
    if (p.startsWith("/api/tpdb/apply/")) return Promise.resolve({ ok: true });
    return Promise.resolve({});
  });
});

const hidInput = () => screen.getByPlaceholderText("history id") as HTMLInputElement;
const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

async function lookUpRowA() {
  renderWired(<Integrations />);
  fireEvent.change(hidInput(), { target: { value: String(HID_A) } });
  fireEvent.click(button("TPDB lookup"));
  // PRECONDITION: the lookup really ran for A and its result reached the page.
  await waitFor(() => expect(lookupCalls().map((c) => c[0])).toEqual([`/api/tpdb/lookup/${HID_A}`]));
  await waitFor(() => expect(screen.getByText(/a13-synthetic-row-a-title/)).toBeInTheDocument());
}

describe("Integrations · TPDB apply · a lookup result applies only to its own history row", () => {
  it("never writes row A's lookup metadata onto row B after the id input changes", async () => {
    await lookUpRowA();

    fireEvent.change(hidInput(), { target: { value: String(HID_B) } });
    // Drive the apply path even if the button is disabled: a guard that only
    // lives in `disabled` is not a guard on confirmRun.
    const apply = button("Apply metadata");
    if (!apply.disabled) {
      fireEvent.click(apply);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      fireEvent.click(confirm);
    }
    await new Promise((r) => setTimeout(r, 50));

    const mismatched = applyCalls().filter(
      (c) => c[0] === `/api/tpdb/apply/${HID_B}` && JSON.stringify(c[1]) === JSON.stringify({ metadata: META_A }),
    );
    expect(mismatched).toEqual([]);
    expect(applyCalls()).toEqual([]);
    // Stale lookup output for A is no longer shown against B.
    expect(screen.queryByText(/a13-synthetic-row-a-title/)).toBeNull();
  });

  it("refuses with a toast when the confirm targets a different row than the lookup result", async () => {
    await lookUpRowA();

    // Open the confirm for A, then look up B while it is still open.
    fireEvent.click(button("Apply metadata"));
    const confirm = await screen.findByRole("button", { name: "Confirm" });
    // The modal aria-hides the page behind it; the controls are still live.
    fireEvent.change(hidInput(), { target: { value: String(HID_B) } });
    fireEvent.click(screen.getByRole("button", { name: "TPDB lookup", hidden: true }));
    await waitFor(() => expect(screen.getByText(/a13-synthetic-row-b-title/)).toBeInTheDocument());

    fireEvent.click(confirm);
    await new Promise((r) => setTimeout(r, 50));

    expect(applyCalls()).toEqual([]);
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining(`#${HID_B}, not #${HID_A}`));
  });

  it("still applies the lookup result to the row it was looked up for (negative control)", async () => {
    await lookUpRowA();

    fireEvent.click(button("Apply metadata"));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(applyCalls().length).toBe(1));
    expect(applyCalls()[0][0]).toBe(`/api/tpdb/apply/${HID_A}`);
    expect(applyCalls()[0][1]).toEqual({ metadata: META_A });
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});
