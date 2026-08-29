import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api-client";
import { AppShell } from "./AppShell";


vi.mock("@/lib/api-client", () => ({ apiGet: vi.fn() }));
vi.mock("@/hooks/useMediaQuery", () => ({ useMediaQuery: () => false }));
vi.mock("@/hooks/useCompletionSound", () => ({ useCompletionSound: () => undefined }));
vi.mock("@/hooks/useKeyboardNav", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useKeyboardNav")>()),
  useKeyboardNav: () => undefined,
}));
// The test keeps AppShell real and removes unrelated global chrome whose own
// queries/keyboard tables would obscure the vault-alert seam under judgment.
vi.mock("./CommandPalette", () => ({ CommandPalette: () => null }));
vi.mock("./ShortcutsSheet", () => ({ ShortcutsSheet: () => null }));
vi.mock("./BottomTabBar", () => ({ BottomTabBar: () => null }));
vi.mock("./PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

const mockedApiGet = vi.mocked(apiGet);

const lockedStatus = {
  ok: true,
  backend: "master_password",
  is_unlocked: false,
  is_initialized: true,
  plaintext_count: 0,
  plaintext_sites: [],
  stored_keys: ["bulkdl-site-row368-a", "bulkdl-site-row368-b"],
  keyring_available: false,
  crypto_available: true,
};

function renderShell(status: typeof lockedStatus) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  mockedApiGet.mockImplementation(async (path: string) => {
    if (path === "/api/secrets/status") return status;
    if (path === "/api/queue/v2") return { running: [], waiting: [] };
    if (path === "/api/health/v2") {
      return { ok: true, version: "row368", sites_loaded: 2, active_downloads: 0 };
    }
    throw new Error(`unexpected GET ${path}`);
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AppShell title="Row 368 test">
          <div>page body</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedApiGet.mockReset();
});
afterEach(() => {
  vi.clearAllMocks();
});

describe("AppShell credential-vault alert", () => {
  it("makes an initialized locked vault globally loud with one unlock link", async () => {
    // PRECONDITION: this fixture is a real locked/nonempty shape, not an empty
    // first-run vault that happens to default to is_unlocked=false.
    expect(lockedStatus.is_initialized).toBe(true);
    expect(lockedStatus.is_unlocked).toBe(false);
    expect(lockedStatus.stored_keys).toHaveLength(2);

    renderShell(lockedStatus);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Credential vault locked");
    expect(alert).toHaveTextContent("stored credentials cannot run");
    const unlockLinks = screen.getAllByRole("link", { name: "Unlock vault" });
    expect(unlockLinks).toHaveLength(1);
    expect(unlockLinks[0]).toHaveAttribute("href", "/secrets");
    await waitFor(() => {
      expect(
        mockedApiGet.mock.calls.filter(([path]) => path === "/api/secrets/status"),
      ).toHaveLength(1);
    });
  });

  it("uses unlocked as the negative control and renders zero lock alerts", async () => {
    const unlockedStatus = { ...lockedStatus, is_unlocked: true };

    // NEGATIVE CONTROL PRECONDITION: the same exact two stored keys remain;
    // only the measured lock state changes.
    expect(unlockedStatus.is_initialized).toBe(true);
    expect(unlockedStatus.is_unlocked).toBe(true);
    expect(unlockedStatus.stored_keys).toHaveLength(2);

    renderShell(unlockedStatus);

    await waitFor(() => {
      expect(
        mockedApiGet.mock.calls.filter(([path]) => path === "/api/secrets/status"),
      ).toHaveLength(1);
    });
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });
});
