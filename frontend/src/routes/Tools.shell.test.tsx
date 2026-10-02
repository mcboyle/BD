import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Tools } from "./Tools";
import BulkEnqueue from "./BulkEnqueue";

// F058-fix (UIUX re-exercise, builds 3.66.1706 + 3.66.1726): OPERATIONS > Tools
// rendered WITHOUT the app shell -- no sidebar, no <main>, no active nav link,
// only browser Back as an exit. Every routed page owns its <AppShell>; Tools
// returned a bare <div>. At desktop width the shell is DesktopShell's
// <aside> rail + <main>, and the rail marks the current route aria-current.

function mountAt(node: React.ReactNode, path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Desktop viewport: AppShell renders DesktopShell at (min-width: 1024px).
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("min-width: 1024px"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
  vi.stubGlobal("EventSource", undefined);
  // Hold every request pending: the shell, not the data, is under test.
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function expectShell(container: HTMLElement, path: string, label: string) {
  expect(container.querySelector("aside"), `F058: ${label} at ${path} has no <aside> nav rail`).not.toBeNull();
  expect(container.querySelector("main"), `F058: ${label} at ${path} has no <main>`).not.toBeNull();
  const active = Array.from(container.querySelectorAll('a[aria-current="page"]'))
    .map((a) => a.getAttribute("href"));
  expect(active, `F058: ${label} at ${path} has no active nav link`).toContain(path);
}

describe("F058 Tools keeps the app shell", () => {
  it("control: a peer OPERATIONS route (/bulk-enqueue) renders inside the shell", () => {
    const { container } = mountAt(<BulkEnqueue />, "/bulk-enqueue");
    expectShell(container, "/bulk-enqueue", "Bulk enqueue");
  });

  it("/tools renders inside the shell with its nav link active", () => {
    const { container } = mountAt(<Tools />, "/tools");
    expectShell(container, "/tools", "Tools");
  });
});
