import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AddSiteWizard } from "./AddSiteWizard";

// Slice 4c.4 + 4d integration: the wizard renders the SHARED WorkflowSteps
// stepper (aria-label "Wizard progress") and surfaces advisory inline URL
// validation via FieldCard — without gating submit (advisory only).

function open() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AddSiteWizard open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
});

describe("AddSiteWizard shared-primitive adoption (Slice 4c.4 / 4d)", () => {
  it("renders the shared workflow stepper", () => {
    open();
    expect(screen.getByRole("list", { name: "Wizard progress" })).toBeInTheDocument();
  });

  it("shows an advisory error for a non-URL Start URL and clears it for a valid one", () => {
    open();
    const startUrl = screen.getByPlaceholderText("https://example.com/library");
    fireEvent.change(startUrl, { target: { value: "not a url" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/full URL/i);
    fireEvent.change(startUrl, { target: { value: "https://example.com/x" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// 3b (v3.66.512) — quick-add can now set login selectors (user_field /
// pass_field / submit_btn) under an Advanced disclosure, so a host with no
// curated login template can self-drive login from the quick flow. The
// backend already accepts these as cfg keys (app.py do_login + CFG_FIELDS);
// this is the FE gap. Collapsed by default; the whole draft is POSTed, so a
// wired+visible field is enough to prove the value reaches /api/sites.
describe("AddSiteWizard advanced login selectors (3b)", () => {
  it("hides the selector fields until the Advanced disclosure is expanded", () => {
    open();
    expect(screen.queryByLabelText("Username field selector")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /advanced.*login selectors/i }));
    expect(screen.getByLabelText("Username field selector")).toBeInTheDocument();
    expect(screen.getByLabelText("Password field selector")).toBeInTheDocument();
    expect(screen.getByLabelText("Submit button selector")).toBeInTheDocument();
  });

  it("wires the username selector into the draft (controlled input)", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: /advanced.*login selectors/i }));
    const uf = screen.getByLabelText("Username field selector") as HTMLInputElement;
    fireEvent.change(uf, { target: { value: 'input[name="email"]' } });
    expect(uf.value).toBe('input[name="email"]');
  });
});
