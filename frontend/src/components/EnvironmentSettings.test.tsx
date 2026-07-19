import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EnvironmentSettings } from "./EnvironmentSettings";

// Bucket 2 (GUI-config parity): the editable "Environment (restart required)"
// panel. It reads/writes /api/settings/envfile (NOT the global_config draft),
// surfaces saved-vs-live with a "restart pending" chip, and on save POSTs only
// the dirty fields. A rejected save persists nothing and surfaces the message.

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

const STATE = {
  ok: true,
  count: 2,
  path: "/home/mboyle/BulkDownloader/.env",
  read_only: false,
  note: "edit -> .env -> restart",
  env: [
    {
      name: "BD_PORT",
      kind: "port",
      applies: "restart",
      applies_note: "Applies on restart.",
      foundation: false,
      danger: false,
      danger_note: "",
      saved: "9000",
      effective: "5555",
      restart_pending: true,
    },
    {
      name: "BD_REPO",
      kind: "path",
      applies: "restart-recommended",
      applies_note: "Applies on restart (recommended).",
      foundation: true,
      danger: false,
      danger_note: "",
      saved: "/srv/bd",
      effective: "/srv/bd",
      restart_pending: false,
    },
  ],
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <EnvironmentSettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("EnvironmentSettings (.env editor)", () => {
  it("renders rows and the restart-pending chip when saved != live", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(STATE)));
    mount();
    expect(await screen.findByLabelText("BD_PORT")).toBeInTheDocument();
    expect(screen.getByLabelText("BD_REPO")).toBeInTheDocument();
    // BD_PORT saved(9000) != effective(5555) -> pending chip
    expect(screen.getByTestId("pending-BD_PORT")).toBeInTheDocument();
    // BD_REPO saved == effective -> no chip
    expect(screen.queryByTestId("pending-BD_REPO")).not.toBeInTheDocument();
    // foundation marker present on BD_REPO input
    expect(screen.getByLabelText("BD_REPO")).toHaveAttribute("data-foundation", "1");
  });

  it("POSTs only the edited field as {updates} on save", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/settings/envfile") && opts?.method === "POST") {
        return jsonResponse({ ok: true, written: ["BD_PORT"], restart_required: true });
      }
      return jsonResponse(STATE);
    });
    vi.stubGlobal("fetch", fetchMock);
    mount();
    const input = await screen.findByLabelText("BD_PORT");
    fireEvent.change(input, { target: { value: "8080" } });
    fireEvent.click(screen.getByRole("button", { name: /save to \.env/i }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([, o]) => (o as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body).toEqual({ updates: { BD_PORT: "8080" } });
    });
  });

  it("surfaces a rejected value and writes nothing", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/api/settings/envfile") && opts?.method === "POST") {
        return jsonResponse(
          { ok: false, rejected: { BD_PORT: "port out of range — expected 1..65535" } },
          false,
          400,
        );
      }
      return jsonResponse(STATE);
    });
    vi.stubGlobal("fetch", fetchMock);
    mount();
    const input = await screen.findByLabelText("BD_PORT");
    fireEvent.change(input, { target: { value: "70000" } });
    fireEvent.click(screen.getByRole("button", { name: /save to \.env/i }));
    expect(await screen.findByTestId("reject-BD_PORT")).toHaveTextContent("out of range");
  });
});
