import { describe, it, expect, beforeAll } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// cmdk needs ResizeObserver + Element.scrollIntoView, neither of which jsdom
// implements. Polyfill no-ops so the palette can mount/render in the test env.
beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = function () {};
  }
});

// Cut 6.3 — command-palette settings-section search. The palette gains a
// "Settings sections" group driven by SETTINGS_SECTIONS from settingsSchema.ts
// (no recents/pins). Selecting a section navigates to /settings#<section>.
import { CommandPalette } from "./CommandPalette";
import { SETTINGS_SECTIONS } from "@/lib/settingsSchema";

function openPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // ⌘K opens the palette (the component listens on window).
  fireEvent.keyDown(window, { key: "k", metaKey: true });
}

describe("CommandPalette settings-section search (Cut 6.3)", () => {
  it("sanity: an existing palette item is reachable once open", () => {
    openPalette();
    expect(screen.getByText(/advanced \/ diagnostics/i)).toBeInTheDocument();
  });

  it("exposes settings sections as palette items (schema-driven)", () => {
    openPalette();
    // These are real sections from SETTINGS_SECTIONS that the pristine palette
    // does NOT list (it only has top-level Settings + Advanced shortcuts).
    expect(screen.getByText("Capture")).toBeInTheDocument();
    expect(screen.getByText("Session keep-alive")).toBeInTheDocument();
  });

  it("lists every schema section (no hardcoded subset drift)", () => {
    openPalette();
    for (const section of SETTINGS_SECTIONS) {
      expect(screen.getByText(section)).toBeInTheDocument();
    }
  });
});
