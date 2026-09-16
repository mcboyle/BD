import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useState } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Link, MemoryRouter, useLocation, useNavigate } from "react-router";
import App from "../App";
import type { SitesV2 } from "../lib/api-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Home } from "./Home";
import { Settings } from "./Settings";
import { Secrets } from "./Secrets";

// Slice-0 regression smoke (UX_IMPROVEMENT_PLAN.md): mount the operator home,
// the big Settings scroll, and a token/secret form page and assert each mounts
// without a thrown render. This is the cheap pre-slice tripwire that catches a
// crash-on-mount regression before the (expensive) chromium re-render diff.
//
// Pages fetch on mount via react-query; we hold every request pending so each
// page renders its loading/skeleton state deterministically (no network, no
// data-shape coupling) — the contract under test is "renders, doesn't throw",
// not any populated layout.

function mount(node: React.ReactNode, path: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Hold all fetches pending → components stay in their loading state.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
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

describe("route mount smoke (Slice-0 gate)", () => {
  it("mounts Home without throwing", () => {
    const { container } = mount(<Home />, "/");
    expect(container.firstChild).not.toBeNull();
  });

  it("mounts Settings without throwing", () => {
    const { container } = mount(<Settings />, "/settings");
    expect(container.firstChild).not.toBeNull();
  });

  it("mounts a form page (Secrets) without throwing", () => {
    const { container } = mount(<Secrets />, "/secrets");
    expect(container.firstChild).not.toBeNull();
  });
});

// Delay the real module to pin the router transition contract at App's
// Suspense boundary. This first dashboard visit precedes the route matrix.
const dashboardModule = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release, requested: false };
});
vi.mock("./Dashboard", async (importOriginal) => {
  dashboardModule.requested = true;
  await dashboardModule.ready;
  return importOriginal();
});

function RouteLocation() {
  const location = useLocation();
  return <output data-testid="app-location">{location.pathname}{location.search}{location.hash}</output>;
}

function RouteControls() {
  const navigate = useNavigate();
  const [navigationError, setNavigationError] = useState("");
  function rejectExternal(target: string) {
    try { navigate(target); }
    catch (error) { setNavigationError((error as Error).message); }
  }
  return <nav aria-label="Route proof controls">
    <Link to="/dashboard?from=smoke#metrics">Open delayed dashboard</Link>
    <Link to="/logs/diff?a=job%2Fa&b=job%20b#comparison">Compare route logs</Link>
    <button onClick={() => navigate(-1)}>Proof back</button>
    <button onClick={() => navigate(1)}>Proof forward</button>
    <button onClick={() => rejectExternal(String.raw`/\\evil.invalid/path`)}>Reject cross-origin target</button>
    <button onClick={() => rejectExternal("javascript:alert(1)")}>Reject script target</button>
    <button onClick={() => rejectExternal("/sites")}>Open valid sites route</button>
    <output data-testid="navigation-error">{navigationError}</output>
  </nav>;
}

const appRoutes = [
  ["/", "BulkDL"],
  ["/dashboard", "System Overview"],
  ["/history", "History · Logs · Search"],
  ["/needs-review", "Needs review"],
  ["/notifications", "Notifications"],
  ["/cluster", "Cluster"],
  ["/sites", "Sites"],
  ["/sites/demo", "Route fixture site"],
  ["/sites/demo/settings", "Site settings — demo"],
  ["/queue", "Queue"],
  ["/activity", "Activity"],
  ["/settings", "Settings"],
  ["/settings/advanced", "Advanced"],
  ["/templates", "Template Manager"],
  ["/sites/demo/inspect", "Candidate Inspector"],
  ["/sites/demo/actions", "Site actions — demo"],
  ["/sites/demo/payload-actions", "Site payload actions — demo"],
  ["/logs/diff", "Compare logs"],
  ["/library", "Library"],
  ["/backup", "Backup"],
  ["/import-views", "Import & Saved Views"],
  ["/more-actions", "More actions"],
  ["/maintenance", "Maintenance · Diagnostics"],
  ["/plugins/metrics", "Plugin Metrics"],
  ["/rebalance", "Storage Rebalance"],
  ["/imports", "Imports · Captcha Queue"],
  ["/batch-ops", "Batch operations"],
  ["/pools-macros", "Pools & macros"],
  ["/integrations", "Integrations"],
  ["/dedup", "Dedup"],
  ["/vpn", "VPN"],
  ["/secrets", "Secrets"],
  ["/tools", "Tools"],
  ["/users", "Users"],
  ["/ai-teach", "AI selector repair"],
  ["/dom-analyzer", "DOM analyzer"],
  ["/capture", "Live capture workflow"],
  ["/schedules", "Recurring capture schedules"],
  ["/alerts", "Alert rules"],
  ["/bulk-enqueue", "Bulk enqueue"],
  ["/budget", "Daily byte usage"],
  ["/ai-assist", "AI Assist — scratchpad"],
] as const;

const routeSites = {
  ok: true,
  count: 1,
  ts: 0,
  sites: [{
    site_id: "demo",
    name: "Route fixture site",
    avatar_color: "#123456",
    state: "idle",
    auth_state: "ok",
    captcha_pending: false,
    downloaded_total: 0,
    active_workers: 0,
    last_event_ts: 0,
    last_event_age: "never",
  }],
} satisfies SitesV2;


describe("row648 actual App routing", () => {
  let client: QueryClient;
  let requests: string[];

  beforeEach(() => {
    client = new QueryClient({defaultOptions: {queries: {retry: false, refetchOnWindowFocus: false}}});
    requests = [];
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("EventSource", undefined);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const raw = input instanceof Request ? input.url : String(input);
      const url = new URL(raw, window.location.origin);
      expect(url.origin).toBe(window.location.origin);
      expect(init?.method ?? (input instanceof Request ? input.method : "GET")).toBe("GET");
      requests.push(url.pathname + url.search);
      if (url.pathname === "/api/auth/whoami") {
        return Promise.resolve(Response.json({ok: true, user: null, multi_user: false}));
      }
      if (url.pathname === "/api/sites/v2") return Promise.resolve(Response.json(routeSites));
      return new Promise<Response>(() => {});
    }));
  });

  afterEach(() => {
    cleanup();
    client.clear();
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  function appBody() {
    return <QueryClientProvider client={client}>
      <RouteControls /><RouteLocation /><App />
    </QueryClientProvider>;
  }

  it("keeps Home visible during a lazy transition and preserves history, search and hash", async () => {
    render(<BrowserRouter>{appBody()}</BrowserRouter>);
    expect(await screen.findByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
    expect(dashboardModule.requested).toBe(false);
    await userEvent.click(screen.getByRole("link", {name: "Open delayed dashboard"}));
    await waitFor(() => expect(dashboardModule.requested).toBe(true));
    expect(screen.getByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
    await act(async () => { dashboardModule.release(); });
    expect(await screen.findByRole("heading", {name: "System Overview", level: 1})).toBeInTheDocument();
    expect(screen.getByTestId("app-location")).toHaveTextContent("/dashboard?from=smoke#metrics");
    await userEvent.click(screen.getByRole("button", {name: "Proof back"}));
    expect(await screen.findByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name: "Proof forward"}));
    expect(await screen.findByRole("heading", {name: "System Overview", level: 1})).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", {name: "Compare route logs"}));
    expect(await screen.findByRole("heading", {name: "Compare logs", level: 1})).toBeInTheDocument();
    expect(screen.getByTestId("app-location")).toHaveTextContent("/logs/diff?a=job%2Fa&b=job%20b#comparison");
    await waitFor(() => expect(requests).toContain("/api/queue/v2/job_log_diff?a=job%2Fa&b=job%20b&limit=200"));
  });

  it.each(["Reject cross-origin target", "Reject script target"])(
    "refuses %s without changing browser history or leaving App", async (label) => {
      render(<BrowserRouter>{appBody()}</BrowserRouter>);
      expect(await screen.findByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
      const historyLength = window.history.length;
      const href = window.location.href;
      const state = structuredClone(window.history.state);
      const push = vi.spyOn(window.history, "pushState");
      const replace = vi.spyOn(window.history, "replaceState");
      await userEvent.click(screen.getByRole("button", {name: label}));
      expect(screen.getByTestId("navigation-error").textContent).toBe("External navigation is not allowed");
      expect(screen.getByTestId("app-location").textContent).toBe("/");
      expect(window.history.length).toBe(historyLength);
      expect(window.location.href).toBe(href);
      expect(window.history.state).toEqual(state);
      expect(push).not.toHaveBeenCalled();
      expect(replace).not.toHaveBeenCalled();
      expect(screen.getByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", {name: "Open valid sites route"}));
      expect(await screen.findByRole("heading", {name: "Sites", level: 1})).toBeInTheDocument();
      expect(screen.getByTestId("app-location").textContent).toBe("/sites");
      expect(window.history.length).toBe(historyLength + 1);
      expect(push).toHaveBeenCalledTimes(1);
      expect(replace).not.toHaveBeenCalled();
    });

  it.each(appRoutes)("resolves %s through App as %s", async (path, title) => {
    expect(appRoutes).toHaveLength(42);
    render(<MemoryRouter initialEntries={[path]}>{appBody()}</MemoryRouter>);
    const headings = await screen.findAllByRole("heading", {name: title, level: 1});
    expect(headings).toHaveLength(path === "/cluster" ? 2 : 1);
    expect(screen.getByTestId("app-location").textContent).toBe(path);
    expect(requests).toContain("/api/auth/whoami");
    if (path === "/sites/demo/inspect") {
      expect(screen.getByRole("link", {name: "Back to site"})).toHaveAttribute("href", "/sites/demo");
    }
  });

  it("replaces an unknown URL so back returns to the preceding route", async () => {
    render(<MemoryRouter initialEntries={["/sites", "/not-a-route"]} initialIndex={1}>{appBody()}</MemoryRouter>);
    expect(await screen.findByRole("heading", {name: "BulkDL", level: 1})).toBeInTheDocument();
    expect(screen.getByTestId("app-location").textContent).toBe("/");
    await userEvent.click(screen.getByRole("button", {name: "Proof back"}));
    expect(await screen.findByRole("heading", {name: "Sites", level: 1})).toBeInTheDocument();
    expect(screen.getByTestId("app-location").textContent).toBe("/sites");
  });
});
