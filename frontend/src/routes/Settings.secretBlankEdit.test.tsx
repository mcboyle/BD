import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";

// dss-spa-secrets F9: typing one character into a stored secret and deleting it
// again is not an edit. The field must still read "set", no change may be
// counted, and a real edit must still count (control).

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/global_config")) {
        return Promise.resolve(
          new Response(JSON.stringify({ auth_token: "<configured>" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return new Promise<Response>(() => {});
    }),
  );
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function authTokenField(): Promise<HTMLInputElement> {
  mount();
  const filter = await screen.findByLabelText("Filter settings");
  fireEvent.change(filter, { target: { value: "API auth token" } });
  return (await screen.findByLabelText("API auth token")) as HTMLInputElement;
}

describe("Settings — a stored secret typed into and emptied again (dss F9)", () => {
  it("still reads 'set' and counts no change", async () => {
    const field = await authTokenField();
    expect(field.placeholder).toMatch(/set — blank keeps it/);
    fireEvent.change(field, { target: { value: "x" } });
    await waitFor(() => expect(screen.getByLabelText("Unsaved changes")).toBeInTheDocument());
    fireEvent.change(field, { target: { value: "" } });
    await waitFor(() =>
      expect(field.placeholder, "DSS-F9-SET-SECRET-READS-NOT-SET").toMatch(/set — blank keeps it/),
    );
    expect(screen.queryByLabelText("Unsaved changes"), "DSS-F9-FAKE-CHANGE").toBeNull();
  });

  it("control: a typed value is one unsaved change", async () => {
    const field = await authTokenField();
    fireEvent.change(field, { target: { value: "new-token" } });
    await waitFor(() =>
      expect(screen.getByLabelText("Unsaved changes")).toHaveTextContent("1 unsaved change"),
    );
    expect(field.value).toBe("new-token");
  });
});
