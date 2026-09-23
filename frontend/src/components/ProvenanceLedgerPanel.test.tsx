import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { ProvenanceLedgerPanel } from "./ProvenanceLedgerPanel";

const LOCAL = { count: 12, head_id: 12, head_chain_hash: "abcdef0123456789ffff", checkpoints: [[12, "abcdef0123456789ffff"]] };
const PEER = { count: 7, head_id: 7, head_chain_hash: "77", checkpoints: [[7, "77"]] };

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ProvenanceLedgerPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiGetMock.mockResolvedValue({ ok: true, digest: LOCAL });
});

describe("ProvenanceLedgerPanel (row 1063)", () => {
  it("shows this node's digest from GET /api/provenance/digest", async () => {
    mount();
    expect(await screen.findByText(/12 entries · head #12/)).toBeInTheDocument();
    expect(apiGetMock).toHaveBeenCalledWith("/api/provenance/digest");
  });

  it("POSTs the pasted peer digest to /api/provenance/reconcile and shows the verdict", async () => {
    apiPostMock.mockResolvedValue({
      ok: true,
      digest: LOCAL,
      verdict: { status: "ahead", local_head_id: 12, remote_head_id: 7, common_id: 7,
                 fork_after_id: null, lag: 5, ask_ids: [] },
    });
    mount();
    // The whole digest response is accepted as well as the bare digest.
    fireEvent.change(screen.getByLabelText("Peer ledger digest"), {
      target: { value: JSON.stringify({ ok: true, digest: PEER }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Compare ledgers" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/provenance/reconcile", { digest: PEER }),
    );
    const verdict = await screen.findByText(/ahead of the peer/);
    expect(verdict).toHaveAttribute("data-status", "ahead");
    expect(verdict).toHaveTextContent("Lag: 5.");
  });

  it("rejects non-JSON input without calling the API", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Peer ledger digest"), { target: { value: "not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Compare ledgers" }));
    expect(await screen.findByText(/not JSON/)).toBeInTheDocument();
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("names the last agreeing id for a fork", async () => {
    apiPostMock.mockResolvedValue({
      ok: true,
      digest: LOCAL,
      verdict: { status: "forked", local_head_id: 12, remote_head_id: 12, common_id: 4,
                 fork_after_id: 4, lag: 0, ask_ids: [] },
    });
    mount();
    fireEvent.change(screen.getByLabelText("Peer ledger digest"), {
      target: { value: JSON.stringify(PEER) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Compare ledgers" }));
    expect(await screen.findByText(/Last agreeing id: 4\./)).toBeInTheDocument();
  });
});
