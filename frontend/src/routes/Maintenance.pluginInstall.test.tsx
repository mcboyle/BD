import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Maintenance } from "./Maintenance";

// v3.66.509: GUI plugin install. A file picker + required risk-acknowledgment
// checkbox gate the Install button; install POSTs multipart to
// /api/plugins/install. The managed-install registry + disclaimer come from
// GET /api/plugins/installed.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

let lastInstallBody: FormData | null = null;

beforeEach(() => {
  lastInstallBody = null;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/plugins/installed")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            installed: [{ file: "demo.py", version: "1.0.0", installed_at: "2026-06-28T00:00:00Z" }],
            risk_acknowledged: false,
            disclaimer: "Plugins run with no sandbox.",
          }),
        );
      }
      if (url.includes("/api/plugins/install")) {
        lastInstallBody = (init?.body as FormData) ?? null;
        return Promise.resolve(jsonResponse({ installed: true, name: "x", version: "1.0.0", file: "x.py" }));
      }
      if (url.includes("/api/plugins/config")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            config: { enabled: null, disabled: [], order: [], allow_full_access: false, node_bin: "" },
            discovered: [],
            full_access_enabled: false,
          }),
        );
      }
      if (url.includes("/api/csrf")) return Promise.resolve(jsonResponse({ token: "t" }));
      return new Promise<Response>(() => {});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Maintenance />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Maintenance plugin install card", () => {
  it("renders the install section, the no-sandbox warning, and managed installs", async () => {
    mount();
    expect(await screen.findByText("Install a plugin")).toBeInTheDocument();
    expect(
      screen.getByText(/Plugins run with no sandbox/i),
    ).toBeInTheDocument();
    // managed-install registry row
    expect(await screen.findByText("demo.py")).toBeInTheDocument();
  });

  it("disables Install until a file is chosen AND the risk is acknowledged", async () => {
    mount();
    const btn = (await screen.findByRole("button", { name: /install plugin/i })) as HTMLButtonElement;
    expect(btn).toBeDisabled();

    const file = new File(["PLUGIN = {}"], "x.py", { type: "text/x-python" });
    const picker = screen.getByLabelText(/plugin file/i) as HTMLInputElement;
    fireEvent.change(picker, { target: { files: [file] } });
    // file alone is not enough
    expect(btn).toBeDisabled();

    const ack = screen.getByLabelText(/I understand this plugin runs with full/i);
    fireEvent.click(ack);
    expect(btn).toBeEnabled();
  });

  it("uploads the file with ack=1 on install (through the confirm dialog)", async () => {
    mount();
    await screen.findByRole("button", { name: /install plugin/i });
    const file = new File(["PLUGIN = {}"], "x.py", { type: "text/x-python" });
    fireEvent.change(screen.getByLabelText(/plugin file/i), { target: { files: [file] } });
    fireEvent.click(screen.getByLabelText(/I understand this plugin runs with full/i));
    fireEvent.click(screen.getByRole("button", { name: /install plugin/i }));

    // arming opens the confirm dialog (no write is one-click); dispatch from it
    const confirm = await screen.findByRole("button", { name: /^confirm$/i });
    fireEvent.click(confirm);

    await waitFor(() => expect(lastInstallBody).not.toBeNull());
    expect(lastInstallBody?.get("ack")).toBe("1");
    expect((lastInstallBody?.get("file") as File)?.name).toBe("x.py");
  });
});
