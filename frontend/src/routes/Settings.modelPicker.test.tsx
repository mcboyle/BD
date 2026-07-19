import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// Cut 7 (7.1) — the two AI model fields (vision/text) become ModelSelect
// editable comboboxes fed by POST /api/ai/models. The list is a CONVENIENCE:
// when detection fails open (ok:false / no models), the fields stay free-text
// and the page never blocks. We assert the wiring lands (datalist options on a
// good detect) and that a failed detect degrades to a still-editable field.

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function stubFetch(modelsBody: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/csrf")) return Promise.resolve(jsonResponse({ csrf_token: "t" }));
      if (url.includes("/api/global_config")) return Promise.resolve(jsonResponse({}));
      if (url.includes("/api/ai/models")) return Promise.resolve(jsonResponse(modelsBody));
      return new Promise<Response>(() => {});
    }),
  );
}

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("Settings model picker (Cut 7 / 7.1)", () => {
  it("offers detected models as datalist options", async () => {
    stubFetch({ ok: true, models: ["qwen2.5:7b", "qwen2.5vl:7b"], provider: "ollama" });
    const { container } = mount();
    // searching renders sections fully — reveal the (collapsed) AI model fields
    const filter = await screen.findByLabelText("Filter settings");
    fireEvent.change(filter, { target: { value: "model" } });
    await waitFor(() => {
      const opts = Array.from(container.querySelectorAll("datalist option")).map(
        (o) => (o as HTMLOptionElement).value,
      );
      expect(opts).toContain("qwen2.5:7b");
      expect(opts).toContain("qwen2.5vl:7b");
    });
  });

  it("keeps the model field editable when detection fails open", async () => {
    stubFetch({ ok: false, models: [], error: "ollama unreachable" });
    mount();
    const filter = await screen.findByLabelText("Filter settings");
    fireEvent.change(filter, { target: { value: "model" } });
    // The combobox(es) for the model fields must still be present + enabled.
    await waitFor(() => {
      const boxes = screen.getAllByRole("combobox") as HTMLInputElement[];
      expect(boxes.length).toBeGreaterThan(0);
      expect(boxes.some((b) => !b.disabled)).toBe(true);
    });
  });
});
