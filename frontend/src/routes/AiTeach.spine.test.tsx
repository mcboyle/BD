import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AiTeach } from "./AiTeach";

// Cut 7 (Track A) — AiTeach adopts the WorkflowPage spine and groups the
// destructive apply_repairs commit in a DangerZone (the live-site write).

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/aiteach"]}>
        <AiTeach />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const u = typeof input === "string" ? input : input.toString();
      if (u.includes("/api/csrf")) return Promise.resolve(jsonResponse({ csrf_token: "t" }));
      if (u.includes("/api/sites/v2")) return Promise.resolve(jsonResponse({ sites: [] }));
      if (u.includes("/api/ai/diff_repair")) {
        return Promise.resolve(
          jsonResponse({
            ok: true,
            repairs: [
              {
                old_selector: ".old-row a.title",
                new_selector: ".new-row a.title",
                role: "row_selectors",
                reasoning: "redesign moved the title link",
                confidence: 90,
              },
            ],
            removed: [],
          }),
        );
      }
      return new Promise<Response>(() => {});
    }),
  );
});

describe("AiTeach adopts the WorkflowPage spine (Cut 7 / Track A)", () => {
  it("lays the body out in workflow slots (purpose + inputs)", () => {
    const { container } = mount();
    expect(container.querySelector('[data-slot="purpose"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="inputs"]')).toBeTruthy();
  });

  it("groups the live-site commit in a DangerZone after a proposal", async () => {
    mount();
    fireEvent.change(screen.getByPlaceholderText(".old-row a.title"), {
      target: { value: ".old-row a.title" },
    });
    fireEvent.click(screen.getByText("Propose repairs"));
    await waitFor(() => {
      expect(screen.getByLabelText("Apply to live site")).toBeInTheDocument();
    });
    // the commit button lives inside that DangerZone
    const dz = screen.getByLabelText("Apply to live site");
    expect(dz.textContent || "").toMatch(/Commit/i);
  });
});
