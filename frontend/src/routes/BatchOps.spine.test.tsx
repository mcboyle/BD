import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BatchOps } from "./BatchOps";

// Cut 7 (Track A) — BatchOps adopts the WorkflowPage spine; the destructive
// batch-delete stays grouped in its DangerZone, now inside the `danger` slot.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/batch"]}>
        <BatchOps />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
});

describe("BatchOps adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="danger"]')).toBeTruthy();
  });

  it("keeps batch-delete inside the danger slot's DangerZone", () => {
    const { container } = mount();
    const danger = container.querySelector('[data-slot="danger"]');
    expect(danger).toBeTruthy();
    expect(danger!.querySelector('[aria-label="Batch delete history"]')).toBeTruthy();
  });

  it("disables Execute retry on page load until preview matches candidates (F027)", () => {
    const { getByRole } = mount();
    const executeRetry = getByRole("button", { name: "Execute retry" });
    expect(executeRetry).toBeDisabled();
  });

  it("enables Execute retry when preview matches candidates and disables when status changes (positive control)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL) => {
        const urlStr = typeof url === "string" ? url : url.toString();
        if (urlStr.includes("/api/batch/retry")) {
          return new Response(JSON.stringify({ ok: true, candidates_matched: 5, processed: 0 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const { getByRole, getAllByRole, getByLabelText } = mount();
    const executeRetry = getByRole("button", { name: "Execute retry" });
    expect(executeRetry).toBeDisabled();

    // Click Preview (dry run) in Batch retry section (index 1 of 3)
    const previewButtons = getAllByRole("button", { name: "Preview (dry run)" });
    await userEvent.click(previewButtons[1]);

    await waitFor(() => {
      expect(executeRetry).toBeEnabled();
    });

    // Changing resetTo input should disable executeRetry again
    const input = getByLabelText("reset to status");
    await userEvent.type(input, "_test");
    expect(executeRetry).toBeDisabled();
  });
});


