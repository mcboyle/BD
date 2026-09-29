import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { apiPost } from "@/lib/api-client";
import { SiteActions } from "./SiteActions";

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("@/lib/api-client", () => ({ apiPost: vi.fn(), apiGet: vi.fn(), apiDelete: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

function mount() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/sites/site/actions"]}>
    <Routes><Route path="/sites/:siteId/actions" element={<SiteActions />} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

function scan() {
  fireEvent.click(screen.getByRole("button", { name: /^Scan / }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
}

beforeEach(() => vi.clearAllMocks());

describe("watch-folder scan action", () => {
  it("names the folder and preserves successful summaries", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ok: true, scanned: 0, results: [] });
    mount();
    expect(screen.getByRole("button", { name: "Scan watch folder" })).toBeInTheDocument();
    scan();
    await waitFor(() => expect(screen.getByText(/"scanned": 0/)).toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledWith("/api/sites/site/watch/scan_now", {});
  });

  it("renders HTTP errors as readable result text", async () => {
    vi.mocked(apiPost).mockRejectedValue(new Error("watch_folder not configured"));
    mount();
    scan();
    expect(await screen.findByRole("alert")).toHaveTextContent("watch_folder not configured");
    expect(screen.getByRole("alert").textContent).toBe("watch_folder not configured");
  });

  it("renders legacy error payloads without raw JSON", async () => {
    vi.mocked(apiPost).mockResolvedValue({ ok: false, error: "watch_folder not configured" });
    mount();
    scan();
    expect(await screen.findByRole("alert")).toHaveTextContent("watch_folder not configured");
    expect(screen.getByRole("alert").textContent).toBe("watch_folder not configured");
  });

  it("replaces an earlier success with the next HTTP error", async () => {
    vi.mocked(apiPost).mockResolvedValueOnce({ ok: true, scanned: 2, results: [] })
      .mockRejectedValueOnce(new Error("scan fixture denied"));
    mount();
    scan();
    await screen.findByText(/"scanned": 2/);
    scan();
    expect(await screen.findByRole("alert")).toHaveTextContent("scan fixture denied");
    expect(screen.queryByText(/"scanned": 2/)).not.toBeInTheDocument();
  });
});

it("preserves message-only failures from manual login actions", async () => {
  vi.mocked(apiPost).mockResolvedValue({ ok: false, message: "No pending manual login" });
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Manual login done" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText(/No pending manual login/)).toBeInTheDocument();
});

it("preserves failure details when no error or message string is supplied", async () => {
  vi.mocked(apiPost).mockResolvedValue({ ok: false, reason: "fixture manual login unavailable" });
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Manual login done" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText(/fixture manual login unavailable/)).toBeInTheDocument();
});
