import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ImportsCenter } from "./ImportsCenter";

// BD-SEC-5 H1: the user-templates import preview lists the config_defaults keys
// the import refuses (post_download_cmd, download_dir, ...) as REFUSED, so the
// operator sees them before importing. RED on b0ea77a6: the panel ignores
// `refused` and shows the template as a plain "new".

const PREVIEW = {
  ok: true,
  mode: "merge",
  counts: { new: 1, changed: 0, conflict: 0, invalid: 0, destructive: 0, secrets_omitted: 0, refused: 1 },
  items: [{ id: "user_bdsec5", name: "BDSEC5 shared", status: "new", secrets_omitted: [],
            refused: ["download_dir", "post_download_cmd"] }],
  destructive: [],
  errors: [],
};

function json(body: unknown): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url === "/api/csrf") return json({ csrf_token: "t" });
    if (url.startsWith("/api/user_templates/import/preview")) return json(PREVIEW);
    return new Promise<Response>(() => {});
  }));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BD-SEC-5 templates import preview shows REFUSED settings", () => {
  it("lists every refused key on the item and counts it", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <ImportsCenter />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText('{"templates":[...]}'), {
      target: { value: JSON.stringify({ templates: [{ id: "user_bdsec5" }] }) },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /^preview$/i })[0]);
    expect(await screen.findByText(/BDSEC5 shared/)).toBeInTheDocument();
    expect(screen.getByText("REFUSED (never applied): download_dir, post_download_cmd")).toBeInTheDocument();
    expect(screen.getByText(/1 with REFUSED settings/)).toBeInTheDocument();
  });
});
