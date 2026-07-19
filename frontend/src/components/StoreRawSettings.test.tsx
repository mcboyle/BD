import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StoreRawSettings } from "./StoreRawSettings";

// Bucket 3b — the raw store editor reads/writes /api/settings/store-raw with its
// own fetch state. Mock apiGet/apiPost and assert it fetches the vpn store on
// mount and renders the editable JSON.

const apiGet = vi.fn();
const apiPost = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiGet: (...a: unknown[]) => apiGet(...a),
  apiPost: (...a: unknown[]) => apiPost(...a),
  ApiError: class ApiError extends Error {
    body?: unknown;
    constructor(m: string, _s?: number, body?: unknown) {
      super(m);
      this.body = body;
    }
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StoreRawSettings />
    </QueryClientProvider>,
  );
}

// the section is collapsible + closed by default; expand it to reach the editor.
function expand() {
  fireEvent.click(
    screen.getByRole("button", { name: /store metadata \(raw \/ advanced\)/i }),
  );
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("StoreRawSettings", () => {
  it("fetches the vpn store from /api/settings/store-raw on mount", async () => {
    apiGet.mockResolvedValue({
      ok: true,
      store: "vpn",
      path: "/x/tunnels.json",
      text: '{\n  "schema_version": 1,\n  "tunnels": []\n}',
    });
    renderIt();
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expect(apiGet.mock.calls[0][0]).toBe("/api/settings/store-raw?store=vpn");
    expand();
    const ta = (await screen.findByLabelText(
      /vpn store json/i,
    )) as HTMLTextAreaElement;
    expect(ta.value).toContain("schema_version");
  });

  it("renders the rename-tunnel (re-key) affordance", async () => {
    apiGet.mockResolvedValue({
      ok: true,
      store: "vpn",
      path: "/x/tunnels.json",
      text: "{}",
    });
    renderIt();
    await waitFor(() => expect(apiGet).toHaveBeenCalled());
    expand();
    expect(
      screen.getByRole("button", { name: /^rename$/i }),
    ).toBeInTheDocument();
  });
});
