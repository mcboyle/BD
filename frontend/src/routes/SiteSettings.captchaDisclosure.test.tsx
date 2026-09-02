import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SiteSettings } from "./SiteSettings";

const PROVIDER = {
  key: "captcha_provider",
  category: "integration",
  secret: false,
  preserve_on_blank: false,
  type: "enum",
  enum: ["2captcha", "capsolver"],
  description: "Third-party captcha solver provider",
  range: null,
  required: false,
  current: "2captcha",
};

const API_KEY = {
  key: "captcha_api_key",
  category: "integration",
  secret: true,
  preserve_on_blank: true,
  type: "string",
  enum: null,
  description: "API key for paid third-party captcha solving",
  range: null,
  required: false,
  current: { present: false },
};

const EDITABLE = {
  ok: true,
  sid: "demo",
  field_meta: { captcha_provider: PROVIDER },
  groups: { integration: [PROVIDER] },
  gated_meta: { captcha_api_key: API_KEY },
  gated_groups: { integration: [API_KEY] },
};

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/editable")) return Promise.resolve(jsonResponse(EDITABLE));
    if (url.includes("/api/vpn/tunnels")) {
      return Promise.resolve(jsonResponse({ ok: true, tunnels: [] }));
    }
    if (url.includes("/api/csrf")) {
      return Promise.resolve(jsonResponse({ csrf_token: "row395-csrf" }));
    }
    return Promise.resolve(jsonResponse({ ok: true }));
  });
});

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/sites/demo/settings"]}>
        <Routes>
          <Route path="/sites/:siteId/settings" element={<SiteSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SiteSettings captcha egress disclosure", () => {
  it("discloses egress, terms, and per-solve cost before the key enables solving", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /integration \(sensitive\)/i }));
    await user.type(await screen.findByLabelText("captcha_api_key"), "not-a-secret-zero-entropy-captcha-key-fixture");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("heading", { name: "Enable paid third-party captcha solving" }))
      .toBeInTheDocument();
    expect(screen.getByText(/target page URL, captcha site key, challenge type/i))
      .toBeInTheDocument();
    expect(screen.getByText(/\$0\.001.*\$0\.00299 per solve/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2Captcha terms" }))
      .toHaveAttribute("href", "https://2captcha.com/terms-of-service");
    expect(screen.getByRole("link", { name: "2Captcha current pricing" }))
      .toHaveAttribute("href", "https://2captcha.com/pricing");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/sites/demo",
      expect.objectContaining({ method: "PUT" }),
    );

    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.click(await screen.findByRole("button", { name: "Acknowledge and enable" }));

    await waitFor(() => {
      const putCall = vi.mocked(global.fetch).mock.calls.find(([, init]) => init?.method === "PUT");
      expect(putCall).toBeDefined();
      const payload = JSON.parse(String(putCall?.[1]?.body));
      expect(payload).toEqual({
        captcha_api_key: "not-a-secret-zero-entropy-captcha-key-fixture",
        captcha_egress_disclosure_ack: true,
      });
    });
  });
});
