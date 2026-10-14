// spa-tools-fix: Tools / AiTeach / DomAnalyzer / drift + scrub / Budget /
// AiScratchpad UI defects (F1 F2 F3 F6 F8). Adapted from the finder's sweep
// spec; every assertion names its finding. Wrapped by tests/test_spa_tools_fix.py.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";

const { apiGetMock, apiPostMock, toastSuccess, toastError, toastInfo, toastMessage } =
  vi.hoisted(() => ({
    apiGetMock: vi.fn(),
    apiPostMock: vi.fn(),
    toastSuccess: vi.fn(),
    toastError: vi.fn(),
    toastInfo: vi.fn(),
    toastMessage: vi.fn(),
  }));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  ApiError: class extends Error {},
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, info: toastInfo, message: toastMessage },
}));

import { Tools } from "./Tools";
import Budget from "./Budget";
import { AiScratchpad } from "@/components/AiScratchpad";
import { AiTeach } from "./AiTeach";
import { DriftRepairPanel } from "@/components/DriftRepairPanel";
import { DomAnalyzer } from "./DomAnalyzer";
import { CaptureBrowser } from "@/components/CaptureBrowser";

function wrap(node: React.ReactNode, path = "/") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  for (const m of [apiGetMock, apiPostMock, toastSuccess, toastError, toastInfo, toastMessage])
    m.mockReset();
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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TOOLS = {
  ok: true,
  tools: [
    { name: "yt-dlp", desc: "", flags: [] },
    { name: "ffprobe", desc: "", flags: [] },
  ],
};
const RUN_OK = {
  ok: true,
  argv: ["yt-dlp"],
  returncode: 0,
  stdout: "yt-dlp-stdout",
  stderr: "",
  timed_out: false,
};

describe("F1 Tools", () => {
  it("F1: a run that resolves after a tool switch names its own tool and stays off the new pane", async () => {
    apiGetMock.mockResolvedValue(TOOLS);
    let resolveRun!: (v: unknown) => void;
    apiPostMock.mockImplementation(() => new Promise((res) => (resolveRun = res)));
    render(wrap(<Tools />, "/tools"));
    fireEvent.click(await screen.findByRole("button", { name: "yt-dlp" }));
    fireEvent.click(screen.getByRole("button", { name: "Run yt-dlp" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "ffprobe" }));
    resolveRun(RUN_OK);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess, "F1: toast named the newly picked tool").toHaveBeenCalledWith(
      "yt-dlp exited 0",
    );
    expect(screen.queryByText("yt-dlp-stdout"), "F1: stale output painted under ffprobe").toBeNull();
    expect(screen.getByRole("button", { name: "Run ffprobe" })).toBeInTheDocument();
  });

  it("F1 control: a run on the same tool shows its output", async () => {
    apiGetMock.mockResolvedValue(TOOLS);
    apiPostMock.mockResolvedValue(RUN_OK);
    render(wrap(<Tools />, "/tools"));
    fireEvent.click(await screen.findByRole("button", { name: "yt-dlp" }));
    fireEvent.click(screen.getByRole("button", { name: "Run yt-dlp" }));
    expect(await screen.findByText("yt-dlp-stdout")).toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalledWith("yt-dlp exited 0");
  });

  it("F1: an {ok:false} run reports its error, not 'exited null'", async () => {
    apiGetMock.mockResolvedValue(TOOLS);
    apiPostMock.mockResolvedValue({
      ok: false,
      error: "tool binary not on PATH",
      returncode: null,
      timed_out: false,
    });
    render(wrap(<Tools />, "/tools"));
    fireEvent.click(await screen.findByRole("button", { name: "yt-dlp" }));
    fireEvent.click(screen.getByRole("button", { name: "Run yt-dlp" }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError, "F1: ok:false toasted as an exit code").toHaveBeenCalledWith(
      "yt-dlp did not run: tool binary not on PATH",
    );
  });
});

function teachGets(sites: { site_id: string; name: string }[]) {
  apiGetMock.mockImplementation((url: string) =>
    Promise.resolve(
      url.includes("/api/sites/v2")
        ? { sites }
        : { ok: true, enabled: false, last_run: null, drafts_pending: 0 },
    ),
  );
}
const REPAIR = {
  old_selector: ".a-old",
  new_selector: ".a-new",
  role: "video_links",
  confidence: 95,
  reasoning: "moved",
};

async function proposeOn(site: string, broken: string) {
  const select = (await screen.findByRole("combobox")) as HTMLSelectElement;
  await waitFor(() => expect(select.options.length).toBeGreaterThan(1));
  fireEvent.change(select, { target: { value: site } });
  fireEvent.change(screen.getByPlaceholderText(".old-row a.title"), {
    target: { value: broken },
  });
  fireEvent.click(screen.getByText("Propose repairs"));
  return select;
}

describe("F2 AiTeach", () => {
  it("F2: switching site drops the plan proposed under the previous site", async () => {
    teachGets([
      { site_id: "siteA", name: "Site A" },
      { site_id: "siteB", name: "Site B" },
    ]);
    apiPostMock.mockResolvedValue({ ok: true, repairs: [REPAIR], removed: [".a-dead"] });
    render(wrap(<AiTeach />, "/ai/teach"));
    const select = await proposeOn("siteA", ".a-old");
    expect(await screen.findByText(".a-new")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "siteB" } });
    expect(screen.queryByText(".a-new"), "F2: siteA plan still shown under siteB").toBeNull();
    expect(screen.queryByRole("button", { name: /Commit/i })).toBeNull();
    expect(apiPostMock.mock.calls.some(([u]) => String(u).includes("/siteB/"))).toBe(false);
  });

  it("F2: a removed-only plan shows the removed list and an enabled Commit", async () => {
    teachGets([{ site_id: "siteA", name: "Site A" }]);
    apiPostMock.mockResolvedValue({ ok: true, repairs: [], removed: [".dead-only"] });
    render(wrap(<AiTeach />, "/ai/teach"));
    await proposeOn("siteA", ".dead-only");
    expect(
      await screen.findByText(/Marked removed .*\.dead-only/),
      "F2: removed-only plan hidden",
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Commit 0 to site/i })).toBeEnabled();
  });

  it("F2: a commit that applies nothing is an error and keeps the plan", async () => {
    teachGets([{ site_id: "siteA", name: "Site A" }]);
    apiPostMock.mockImplementation((url: string) =>
      Promise.resolve(
        url.includes("/api/ai/diff_repair")
          ? { ok: true, repairs: [REPAIR], removed: [] }
          : {
              ok: true,
              dry_run: false,
              count: 0,
              applied: [],
              removed: [],
              rejected: [{ reason: "old_selector not present in learned" }],
            },
      ),
    );
    render(wrap(<AiTeach />, "/ai/teach"));
    await proposeOn("siteA", ".a-old");
    fireEvent.click(await screen.findByRole("button", { name: /Commit 1 to site/i }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError).toHaveBeenCalledWith(
      "Applied 0 repairs · 1 rejected: old_selector not present in learned",
    );
    expect(toastSuccess, "F2: 0 applied toasted as success").not.toHaveBeenCalledWith(
      expect.stringContaining("Applied 0"),
    );
    expect(screen.getByText(".a-new"), "F2: plan wiped after nothing applied").toBeInTheDocument();
  });

  it("F2 control: committing on the proposing site posts there and clears the plan", async () => {
    teachGets([{ site_id: "siteA", name: "Site A" }]);
    apiPostMock.mockImplementation((url: string) =>
      Promise.resolve(
        url.includes("/api/ai/diff_repair")
          ? { ok: true, repairs: [REPAIR], removed: [] }
          : { ok: true, dry_run: false, count: 1, applied: [], removed: [], rejected: [] },
      ),
    );
    render(wrap(<AiTeach />, "/ai/teach"));
    await proposeOn("siteA", ".a-old");
    fireEvent.click(await screen.findByRole("button", { name: /Commit 1 to site/i }));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Applied 1 repair"));
    expect(apiPostMock).toHaveBeenCalledWith(
      "/api/sites/siteA/learned/apply_repairs",
      expect.objectContaining({ repairs: [{ old_selector: ".a-old", new_selector: ".a-new", role: "video_links" }] }),
    );
    expect(screen.queryByText(".a-new")).toBeNull();
  });
});

function node(id: string) {
  return { type: "element", tag: "div", id, classes: [], attrs: {}, text: "", children: [] };
}
function analyzerMocks(tree: () => Promise<unknown>) {
  apiGetMock.mockImplementation((url: string) =>
    Promise.resolve(
      url.includes("/api/analyzer/captures")
        ? {
            ok: true,
            captures: [
              { rel_path: "capA.json", name: "capA.json", dir: "captures" },
              { rel_path: "capB.json", name: "capB.json", dir: "captures" },
            ],
          }
        : { ok: true, scanned: false, built_at: null, total: 0, page: 1, per_page: 50, captures: [], summary: null, sites: [] },
    ),
  );
  apiPostMock.mockImplementation((url: string, body: { capture?: string }) => {
    if (url.includes("/api/analyzer/load")) {
      const c = body.capture === "capA.json" ? "capA" : "capB";
      return Promise.resolve({
        ok: true,
        capture: `${c}.json`,
        host: `${c}.example.com`,
        has_dom: true,
        residual_count: 0,
        residual_kinds: {},
        tree: node(`full-${c}`),
      });
    }
    if (url.includes("/api/analyzer/tree")) return tree();
    if (url.includes("/api/analyzer/test"))
      return Promise.resolve({ ok: true, results: [{ selector: ".capA-result", count: 3 }] });
    return Promise.resolve({ ok: true });
  });
}
const LIMITED_A = {
  ok: true,
  capture: "capA.json",
  has_dom: true,
  residual_count: 0,
  residual_kinds: {},
  tree: node("limited-capA"),
};

async function loadCapA() {
  const selects = await screen.findAllByRole("combobox");
  const cap = selects[selects.length - 1] as HTMLSelectElement;
  await waitFor(() => expect(cap.options.length).toBe(3));
  fireEvent.change(cap, { target: { value: "capA.json" } });
  expect(await screen.findByText(/div#full-capA/)).toBeInTheDocument();
  return cap;
}

describe("F3 DomAnalyzer", () => {
  it("F3: loading capture B drops capture A's limited tree and selector results", async () => {
    analyzerMocks(() => Promise.resolve(LIMITED_A));
    render(wrap(<DomAnalyzer />, "/ai/analyzer"));
    const cap = await loadCapA();
    fireEvent.click(screen.getByText("Limited view"));
    expect(await screen.findByText(/div#limited-capA/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("one selector per line"), {
      target: { value: ".sel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run against captured DOM" }));
    expect(await screen.findByText(".capA-result")).toBeInTheDocument();
    fireEvent.change(cap, { target: { value: "capB.json" } });
    expect(await screen.findByText(/div#full-capB/), "F3: capA tree shown under capB").toBeInTheDocument();
    expect(screen.queryByText(/div#limited-capA/)).toBeNull();
    expect(screen.queryByText(".capA-result"), "F3: capA results shown under capB").toBeNull();
  });

  it("F3: a limited view still in flight when B loads never paints over B", async () => {
    let resolveTree!: (v: unknown) => void;
    analyzerMocks(() => new Promise((res) => (resolveTree = res)));
    render(wrap(<DomAnalyzer />, "/ai/analyzer"));
    const cap = await loadCapA();
    fireEvent.click(screen.getByText("Limited view"));
    await waitFor(() => expect(resolveTree).toBeTypeOf("function"));
    fireEvent.change(cap, { target: { value: "capB.json" } });
    expect(await screen.findByText(/div#full-capB/)).toBeInTheDocument();
    resolveTree(LIMITED_A);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/div#limited-capA/), "F3: late capA tree painted over capB").toBeNull();
    expect(screen.getByText(/div#full-capB/)).toBeInTheDocument();
  });
});

describe("F6 drift run + scrub", () => {
  it("F6: an {ok:false} sweep run or scrub is an error toast", async () => {
    apiGetMock.mockResolvedValue({
      ok: true,
      enabled: false,
      last_run: null,
      drafts_pending: 0,
      scanned: true,
      built_at: 1700000000,
      total: 1,
      page: 1,
      per_page: 50,
      captures: [
        {
          rel_path: "cap1.wacz",
          name: "cap1.wacz",
          dir: "captures",
          host: "example.com",
          captured_at: 1700000000,
          size: 1024,
          kind: "wacz",
          redacted: false,
        },
      ],
      summary: { ok: true, total: 1, by_host: { "example.com": 1 }, new_since_last: 0, took_ms: 1 },
      sites: [],
    });
    apiPostMock.mockResolvedValue({ ok: false, error: "backend failure" });
    render(
      wrap(
        <>
          <DriftRepairPanel />
          <CaptureBrowser />
        </>,
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Run now" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Run failed: backend failure"));
    fireEvent.click(await screen.findByRole("button", { name: /Scrub/i }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Scrub failed: backend failure"));
    expect(toastMessage, "F6: ok:false shown as a neutral message").not.toHaveBeenCalled();
  });
});

describe("F8 Budget + AiScratchpad", () => {
  it("F8: non-finite bytes render a dash and Enter in Site ID loads", async () => {
    apiGetMock.mockResolvedValue({
      site_id: "alpha",
      history: [
        { ymd: "2026-10-01", bytes: 0 },
        { ymd: "2026-10-02", bytes: 0.5 },
        { ymd: "2026-10-03", bytes: Number.NaN },
      ],
    });
    render(wrap(<Budget />, "/budget"));
    const input = screen.getByLabelText("site id");
    fireEvent.change(input, { target: { value: "alpha" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(await screen.findByText("2026-10-03"), "F8: Enter did not load").toBeInTheDocument();
    expect(apiGetMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/daily_budget/history?site=alpha"),
      expect.anything(),
    );
    expect(screen.queryByText(/NaN|undefined/), "F8: NaN bytes rendered as text").toBeNull();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("0.5 B")).toBeInTheDocument();
  });

  it("F8 control: positive bytes format and Load still loads", async () => {
    apiGetMock.mockResolvedValue({ site_id: "beta", history: [{ ymd: "2026-10-04", bytes: 1048576 }] });
    render(wrap(<Budget />, "/budget"));
    fireEvent.change(screen.getByLabelText("site id"), { target: { value: "beta" } });
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(await screen.findByText("1.0 MB")).toBeInTheDocument();
  });

  it("F8: a defaultModel that arrives after mount is used; a set model is not replaced", async () => {
    const qc = new QueryClient();
    const at = (m: string) => (
      <QueryClientProvider client={qc}>
        <AiScratchpad models={["llava:13b", "mistral:7b"]} defaultModel={m} />
      </QueryClientProvider>
    );
    const { rerender, container } = render(at(""));
    expect(container.querySelector("#ai-chat-image")).toBeNull();
    rerender(at("llava:13b"));
    await waitFor(() =>
      expect(container.querySelector("#ai-chat-image"), "F8: late defaultModel ignored").not.toBeNull(),
    );
    rerender(at("mistral:7b"));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("#ai-chat-image")).not.toBeNull();
  });
});
