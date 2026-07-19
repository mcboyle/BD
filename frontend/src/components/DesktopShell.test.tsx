import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DesktopShell } from "./DesktopShell";

// Slice 4a — DesktopShell nav behavior: the group containing the active route
// auto-opens + is marked active (so navigating to /vpn doesn't bury you in a
// collapsed group), and a visible ⌘K affordance triggers the real palette
// (which listens for a window keydown of meta/ctrl + k).

function renderShell(path: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <DesktopShell title="VPN">
          <div>page-body</div>
        </DesktopShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("DesktopShell — active group surfacing", () => {
  it("opens + marks the group containing the active route", () => {
    renderShell("/vpn");
    // "VPN" lives in the "Network & security" group; on /vpn that group must
    // be open (its links rendered) and its header marked active.
    const header = screen.getByRole("button", { name: /network & security/i });
    expect(header).toHaveAttribute("data-active", "true");
    // The VPN nav link inside the group is rendered (group is open).
    const vpnLinks = screen.getAllByRole("link", { name: /vpn/i });
    expect(vpnLinks.length).toBeGreaterThan(0);
  });

  it("does not mark an unrelated group active", () => {
    renderShell("/vpn");
    const ops = screen.getByRole("button", { name: /^operations$/i });
    expect(ops).not.toHaveAttribute("data-active", "true");
  });
});

describe("DesktopShell — external console links", () => {
  it("renders /framework as an external anchor (new tab), not a NavLink", () => {
    // rendering at /framework auto-opens the Consoles group (forceOpen on active)
    renderShell("/framework");
    const link = screen.getByRole("link", { name: /framework dashboard/i });
    expect(link).toHaveAttribute("href", "/framework");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel") ?? "").toContain("noopener");
  });
});

describe("DesktopShell — ⌘K affordance", () => {
  it("renders a command-palette affordance", () => {
    renderShell("/");
    expect(
      screen.getByRole("button", { name: /command palette|search/i }),
    ).toBeInTheDocument();
  });

  it("dispatches a meta/ctrl+k window keydown when clicked", () => {
    renderShell("/");
    const spy = vi.fn();
    window.addEventListener("keydown", spy);
    fireEvent.click(
      screen.getByRole("button", { name: /command palette|search/i }),
    );
    window.removeEventListener("keydown", spy);
    expect(spy).toHaveBeenCalled();
    const ev = spy.mock.calls.find(
      ([e]) => (e as KeyboardEvent).key?.toLowerCase() === "k",
    )?.[0] as KeyboardEvent | undefined;
    expect(ev).toBeTruthy();
    expect(ev!.metaKey || ev!.ctrlKey).toBe(true);
  });
});
