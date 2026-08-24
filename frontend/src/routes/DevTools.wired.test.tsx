import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

import { DevToolsSection } from "@/components/sections/DevToolsSection";
import { MacrosOpsSection } from "@/components/sections/MacrosOpsSection";
import { TemplateAuthoringSection } from "@/components/sections/TemplateAuthoringSection";
import { useTemplateLibrary } from "@/hooks/useTemplateAuthoring";
import { Advanced } from "@/routes/Advanced";
import { PoolsMacros } from "@/routes/PoolsMacros";
import { TemplateManager } from "@/routes/TemplateManager";
import {
  calledPaths,
  installApiFixtures,
  renderAppAt,
  renderWired,
  renderWiredHook,
} from "@/test/wiredGateHarness";

const HEALTH = {
  ok: true,
  db_ok: true,
  db_journal_mode: "wal",
  degraded: null,
  ollama: { reachable: false, error: "fixture" },
  last_suite: { available: false },
  disks: [],
  version: "fixture",
  uptime_s: 1,
  sites_loaded: 1,
  queue_depth: 0,
};

const BASE_FIXTURES = {
  "/api/template_manager": { reviewed: [], drafts: [] },
  "/api/templates": { templates: [] },
  "/api/account_pool/status_all": { pools: [] },
  "/api/macros/list": { macros: [] },
  "/api/health/v2": HEALTH,
  "/api/dev/enabled": { enabled: true },
  "/api/dev/discover": { files: ["tests/test_fixture.py"] },
  "/api/plugins/status": { loaded: [] },
  "/api/plugins/events": { events: [] },
  "/api/synthetic_tests/list": { fixtures: [] },
  "/api/i18n/load/es": { lang: "es", strings: { ready: "listo" } },
  "/api/dev/runs/run-fixture": { state: "done", output: "ok" },
  "POST /api/template/extract": {
    ok: true,
    template: { selectors: { title: ".title" } },
    candidates: [],
  },
  "POST /api/template/refine": {
    ok: true,
    template: { selectors: { title: ".title" } },
  },
  "POST /api/template/sandbox": { ok: true, matches: { title: "fixture" } },
  "POST /api/macros/save": { ok: true, actions: [] },
  "POST /api/macros/replay/site%20fixture/macro%2Ffixture": { ok: true },
  "POST /api/dev/run": { ok: true, run_id: "run-fixture" },
  "POST /api/synthetic_tests/run_all": { ok: true },
};

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, BASE_FIXTURES);
});

describe("T10 developer surfaces runtime wiring", () => {
  it("mounts all three sections through their real routes and starts their reads", async () => {
    renderWired(<TemplateManager />, "/templates");
    expect(
      screen.getByRole("heading", { name: "Template authoring" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(calledPaths(apiGetMock)).toContain("/api/template_manager"),
    );

    cleanup();
    apiGetMock.mockClear();
    renderWired(<PoolsMacros />, "/pools-macros");
    expect(
      screen.getByRole("heading", { name: "Macro inspect & replay" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(calledPaths(apiGetMock)).toContain("/api/account_pool/status_all");
      expect(calledPaths(apiGetMock)).toContain("/api/macros/list");
    });

    cleanup();
    apiGetMock.mockClear();
    renderWired(<Advanced />, "/settings/advanced");
    expect(
      screen.getByRole("heading", { name: "Developer & diagnostics" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      const paths = calledPaths(apiGetMock);
      expect(paths).toContain("/api/health/v2");
      expect(paths).toContain("/api/dev/enabled");
      expect(paths).toContain("/api/plugins/status");
      expect(paths).toContain("/api/plugins/events");
      expect(paths).toContain("/api/synthetic_tests/list");
    });
  });

  it("executes the template library hook instead of merely finding its literal", async () => {
    renderWiredHook(() => useTemplateLibrary());
    await waitFor(() =>
      expect(calledPaths(apiGetMock)).toContain("/api/templates"),
    );
  });

  it("extracts and refines a draft, then confirms the live sandbox write", async () => {
    const user = userEvent.setup();
    renderWired(<TemplateAuthoringSection />, "/templates");

    await user.type(screen.getByLabelText("page html"), "<html>fixture</html>");
    await user.click(screen.getByRole("button", { name: "Extract draft" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/template/extract", {
        html: "<html>fixture</html>",
        page_url: undefined,
      }),
    );
    const sandboxUrl = await screen.findByLabelText("sandbox url");

    await user.click(screen.getByRole("button", { name: "Refine (AI)" }));
    await waitFor(() =>
      expect(calledPaths(apiPostMock)).toContain("/api/template/refine"),
    );
    await user.type(sandboxUrl, "https://fixture.test/page");
    await user.click(screen.getByRole("button", { name: "Run sandbox…" }));
    expect(calledPaths(apiPostMock)).not.toContain("/api/template/sandbox");

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/network request from this host/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/template/sandbox", {
        url: "https://fixture.test/page",
        template: { selectors: { title: ".title" } },
        mode: "http",
      }),
    );
  });

  it("loads and saves a macro, then confirms replay with the INV-001 warning", async () => {
    const user = userEvent.setup();
    apiGetMock.mockImplementation((path: string) =>
      Promise.resolve(
        path === "/api/macros/get/site%20fixture/macro%2Ffixture"
          ? { actions: [{ kind: "click" }], description: "fixture", tags: ["wired"] }
          : BASE_FIXTURES[path as keyof typeof BASE_FIXTURES] ?? {},
      ),
    );
    renderWired(<MacrosOpsSection />, "/pools-macros");

    await user.type(screen.getByLabelText("macro site id"), "site fixture");
    await user.type(screen.getByLabelText("macro name"), "macro/fixture");
    await user.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() =>
      expect(calledPaths(apiGetMock)).toContain(
        "/api/macros/get/site%20fixture/macro%2Ffixture",
      ),
    );
    expect(await screen.findByText("1 action(s) loaded.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("/api/macros/save", {
        site_id: "site fixture",
        name: "macro/fixture",
        actions: [{ kind: "click" }],
        description: "fixture",
        tags: ["wired"],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Replay…" }));
    expect(calledPaths(apiPostMock)).not.toContain(
      "/api/macros/replay/site%20fixture/macro%2Ffixture",
    );
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/INV-001/)).toBeInTheDocument();
    expect(within(dialog).getByText(/pause workers first/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "/api/macros/replay/site%20fixture/macro%2Ffixture",
        { headless: true },
      ),
    );
  });

  it("keeps the dev runner hidden when the server disables dev mode", async () => {
    installApiFixtures(apiGetMock, apiPostMock, {
      ...BASE_FIXTURES,
      "/api/dev/enabled": { enabled: false },
    });
    renderWired(<DevToolsSection />, "/settings/advanced");

    expect(await screen.findByText(/Dev mode is disabled/)).toBeInTheDocument();
    expect(screen.queryByLabelText("dev run target")).not.toBeInTheDocument();
    expect(calledPaths(apiGetMock)).not.toContain("/api/dev/discover");
  });

  it("discovers tests and confirms dev-run and run-all with exact requests", async () => {
    const user = userEvent.setup();
    renderWired(<DevToolsSection />, "/settings/advanced");
    expect(await screen.findByText(/1 files discovered/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("i18n locale"), "es");
    await waitFor(() =>
      expect(calledPaths(apiGetMock)).toContain("/api/i18n/load/es"),
    );

    await user.click(
      screen.getByRole("button", { name: "Run all synthetic tests…" }),
    );
    expect(calledPaths(apiPostMock)).not.toContain("/api/synthetic_tests/run_all");
    let dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "/api/synthetic_tests/run_all",
        {},
      ),
    );

    await user.type(screen.getByLabelText("dev run target"), "tests/test_fixture.py");
    await user.click(screen.getByRole("button", { name: "Run…" }));
    expect(calledPaths(apiPostMock)).not.toContain("/api/dev/run");
    dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(apiPostMock).toHaveBeenCalledWith("/api/dev/run", {
        target: "tests/test_fixture.py",
        kind: "file",
      });
      expect(calledPaths(apiGetMock)).toContain("/api/dev/runs/run-fixture");
    });
  });

  // ROUTE BINDING, NOT COMPONENT RENDERING. The tests above place the section
  // component directly in the router, which proves it calls its endpoints but
  // says nothing about whether an operator can REACH it. App.tsx's
  // `<Route path="/settings/advanced" element={<Advanced />} />` is a separate
  // fact, and an evasion that removes or repaths that binding leaves every
  // component-level assertion above green. This renders the REAL route table.
  it("is reachable at its real URL through App's route table", async () => {
    renderAppAt("/settings/advanced");
    expect(
      await screen.findByRole("heading", { name: /advanced/i }, { timeout: 15000 }),
    ).toBeInTheDocument();
  }, 20000);
});
