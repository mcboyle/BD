import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

// C7 auth guard. The SAFETY-CRITICAL contract: with multi-user off (default), or
// while whoami is loading, or on a whoami error, the gate renders the app. It
// shows the Login wall ONLY on a definitive "multi_user on + no user".
const whoamiMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  whoami: () => whoamiMock(),
  // Login imports login(); stub it so the module graph resolves.
  login: vi.fn(),
}));

import { AuthGate } from "./AuthGate";

function renderGate() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AuthGate>
          <div data-testid="app-content">APP</div>
        </AuthGate>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  whoamiMock.mockReset();
});

describe("AuthGate (C7 sign-in guard)", () => {
  it("renders the app when multi-user is OFF (single-operator no-op)", async () => {
    whoamiMock.mockResolvedValue({ ok: true, user: null, multi_user: false });
    renderGate();
    await waitFor(() =>
      expect(screen.getByTestId("app-content")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("renders the app when a user IS signed in (multi-user on)", async () => {
    whoamiMock.mockResolvedValue({
      ok: true,
      user: { username: "matt", role: "admin" },
      multi_user: true,
    });
    renderGate();
    await waitFor(() =>
      expect(screen.getByTestId("app-content")).toBeInTheDocument(),
    );
  });

  it("shows Login ONLY when multi-user is on AND there is no session", async () => {
    whoamiMock.mockResolvedValue({ ok: true, user: null, multi_user: true });
    renderGate();
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Sign in" }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("app-content")).not.toBeInTheDocument();
  });

  it("renders the app (never the wall) while whoami is still loading", () => {
    // a promise that never resolves -> query stays in loading
    whoamiMock.mockReturnValue(new Promise(() => {}));
    renderGate();
    expect(screen.getByTestId("app-content")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("renders the app (never the wall) when whoami errors", async () => {
    whoamiMock.mockRejectedValue(new Error("network"));
    renderGate();
    // give the query a tick to settle into error
    await waitFor(() =>
      expect(screen.getByTestId("app-content")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });
});
