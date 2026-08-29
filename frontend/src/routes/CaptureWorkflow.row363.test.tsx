import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";

const { apiGetMock, apiPostMock, apiPutMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    message: vi.fn(),
  }),
  Toaster: () => null,
}));

import { renderAppAt } from "@/test/wiredGateHarness";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const learnedFixture = {
  status: "FOUND",
  shape: "DROPDOWN",
  row_selector: "a[class*='DownloadOption']",
  trigger_selector: "[class*='ScenePlayerHeaderPlus-IconItem']:has([class*='Icon-Download'])",
  url_attribute: "href",
  options: [
    {
      height: 1080,
      container: "mp4",
      size: "1.96 GB",
      label: "Full HD 1080p 1.96 GB",
      href: "/movieaction/download/fixture/1080p/mp4",
    },
  ],
  selector_attempts: [],
  selection: { status: "SELECTED", option: { height: 1080 }, reason: "" },
  network_evidence: [],
  corroboration: { status: "DOM_ONLY", detail: "DOM only" },
} as const;

async function createAndOpenSession() {
  fireEvent.change(screen.getByPlaceholderText("WowGirls"), { target: { value: "Measured site" } });
  fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), {
    target: { value: "https://members.example.invalid/login" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Create site & continue/i }));
  expect(await screen.findByText("Start a capture")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open session" }));
  expect(await screen.findByText("Learn the download affordance")).toBeInTheDocument();
}

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
});

beforeEach(() => {
  localStorage.clear();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPutMock.mockReset();
  apiGetMock.mockImplementation((path: string) => {
    if (path === "/api/auth/whoami") {
      return Promise.resolve({ ok: true, user: null, multi_user: false });
    }
    if (path === "/cockpit/api/novnc") return Promise.resolve({ ok: true, url: "" });
    return Promise.resolve({});
  });
  apiPostMock.mockResolvedValue({ ok: true });
  apiPutMock.mockResolvedValue({ ok: true });
});

describe("row 363 workflow reachability", () => {
  it(
    "mounts live learning, network evidence, and listing crawl inside the existing Build step",
    async () => {
      renderAppAt("/capture");
      expect(
        await screen.findByRole(
          "heading",
          { name: "Live capture workflow", level: 1 },
          { timeout: 20_000 },
        ),
      ).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /3.*Build/i }));
      expect(screen.getByRole("button", { name: /learn from live page/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /capture network evidence/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /crawl this listing/i })).toBeInTheDocument();
      expect(screen.getByLabelText("quality_preference")).toBeInTheDocument();
      expect(screen.getByLabelText("min_resolution")).toBeInTheDocument();
    },
    25_000,
  );

  it(
    "stages the nonce-proven artifact, then preserves Inspect → Test → Review",
    async () => {
      const requestId = "a".repeat(32);
      const learned = {
        status: "FOUND",
        shape: "DROPDOWN",
        row_selector: "a[class*='DownloadOption']",
        trigger_selector: "[class*='ScenePlayerHeaderPlus-IconItem']:has([class*='Icon-Download'])",
        url_attribute: "href",
        options: [
          {
            height: 1080,
            container: "mp4",
            size: "1.96 GB",
            label: "Full HD 1080p 1.96 GB",
            href: "/movieaction/download/fixture/1080p/mp4",
          },
        ],
        selector_attempts: [],
        selection: { status: "SELECTED", option: { height: 1080 }, reason: "" },
        network_evidence: [],
        corroboration: { status: "DOM_ONLY", detail: "DOM only" },
      };
      const stagedTemplate = {
        patterns: ["members\\.example\\.invalid"],
        learned: {
          download: {
            trigger_selectors: [learned.trigger_selector],
            row_selectors: [learned.row_selector],
            url_attribute: "href",
          },
        },
        config_defaults: {
          quality_preference: "4320,3160,2880,2160,1440,1080,720",
          min_resolution: 1080,
        },
        resolutions: [1080],
        network_patterns: [],
        learning_evidence: {
          shape: "DROPDOWN",
          option_count: 1,
          corroboration: "DOM_ONLY",
          dom_options_proven: true,
        },
      };
      apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363", login_url: "https://members.example.invalid/login" });
        }
        if (path === "/cockpit/api/run-capture") {
          return Promise.resolve({ task: { task_id: "t_row363" } });
        }
        if (path === "/api/captures/live_learning" && body?.action === "arm") {
          return Promise.resolve({ ok: true, request_id: requestId, state: "running" });
        }
        if (path === "/api/captures/live_learning" && body?.action === "poll") {
          return Promise.resolve({
            ok: true,
            state: "found",
            response: { state: "found", result: learned },
          });
        }
        if (path === "/api/captures/stage_learning") {
          return Promise.resolve({
            ok: true,
            file: "members.example.invalid.template-draft.json",
            status: "draft_review_required",
            template: stagedTemplate,
          });
        }
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      fireEvent.change(screen.getByPlaceholderText("WowGirls"), { target: { value: "Measured site" } });
      fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), {
        target: { value: "https://members.example.invalid/login" },
      });
      fireEvent.click(screen.getByRole("button", { name: /Create site & continue/i }));
      expect(await screen.findByText("Start a capture")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Open session" }));
      expect(await screen.findByText("Learn the download affordance")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Learn from live page/i }));
      expect(await screen.findByText(/Planned: 1080p/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Save learned template/i }));
      expect(await screen.findByText("Inspect & refine")).toBeInTheDocument();
      await waitFor(() => {
        const stageCall = apiPostMock.mock.calls.find(([path]) => path === "/api/captures/stage_learning");
        expect(stageCall?.[1]).toEqual({
          task_id: "t_row363",
          request_id: requestId,
          site_id: "site363",
        });
      });

      fireEvent.click(screen.getByRole("button", { name: /Continue/i }));
      expect(await screen.findByText("Test extract")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /6.*Review/i })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /6.*Review/i }));
      expect(await screen.findByText(/Exact staged artifact/i)).toHaveTextContent(
        "members.example.invalid.template-draft.json",
      );
      expect(screen.getByText(/DROPDOWN · pattern/i)).toBeInTheDocument();
      expect(screen.getByText(/policy: SELECTED/i)).toBeInTheDocument();
      expect(screen.getByText(/network patterns: 0/i)).toBeInTheDocument();

      // A later capture must not inherit the first session's staged candidate
      // or its Build/Review readiness.
      fireEvent.click(screen.getByRole("button", { name: /Expert · rails off/i }));
      fireEvent.click(screen.getByRole("button", { name: /Finish & save/i }));
      expect(await screen.findByRole("button", { name: /Create site & continue/i })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Create site & continue/i }));
      expect(await screen.findByText("Start a capture")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Open session" }));
      expect(await screen.findByText("Learn the download affordance")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /6.*Review/i }));
      expect(screen.queryByText(/Exact staged artifact/i)).not.toBeInTheDocument();
      expect(screen.queryByText("draft_review_required")).not.toBeInTheDocument();
    },
    30_000,
  );

  it(
    "sends proven selectors to crawl and clears derived evidence when policy changes",
    async () => {
      apiGetMock.mockImplementation((path: string) => {
        if (path === "/api/auth/whoami") {
          return Promise.resolve({ ok: true, user: null, multi_user: false });
        }
        if (path === "/cockpit/api/novnc") return Promise.resolve({ ok: true, url: "" });
        if (path.includes("/events?kind=network")) {
          return Promise.resolve({
            ok: true,
            events: [{
              kind: "network",
              message: "200 GET https://members.example.invalid/movieaction/download/fixture/1080p/mp4",
            }],
          });
        }
        return Promise.resolve({});
      });
      apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363" });
        }
        if (path === "/cockpit/api/run-capture") {
          return Promise.resolve({ task: { task_id: "t_derived" } });
        }
        if (path === "/api/captures/live_learning" && body?.action === "arm") {
          return Promise.resolve({
            ok: true,
            request_id: `${String(body.mode || "learn")}-request`,
            state: "running",
          });
        }
        if (path === "/api/captures/live_learning" && body?.action === "poll") {
          if (body.mode === "learn") {
            return Promise.resolve({
              ok: true,
              response: { state: "found", result: learnedFixture },
            });
          }
          if (body.mode === "network") {
            return Promise.resolve({
              ok: true,
              response: {
                state: "found",
                result: {
                  status: "FOUND",
                  count: 1,
                  network_evidence: [
                    { url: "https://media.example.invalid/fixture/1080p.mp4", kind: "media" },
                  ],
                },
              },
            });
          }
          if (body.mode === "crawl") {
            return Promise.resolve({
              ok: true,
              response: {
                state: "found",
                result: {
                  status: "FOUND",
                  scene_count: 1,
                  plans: [
                    {
                      url: "https://members.example.invalid/scene/1",
                      chosen_height: 1080,
                      status: "PLANNED",
                      selection_status: "SELECTED",
                    },
                  ],
                },
              },
            });
          }
        }
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      await createAndOpenSession();

      fireEvent.click(screen.getByRole("button", { name: /Learn from live page/i }));
      expect(await screen.findByText(/Planned: 1080p/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Capture network evidence/i }));
      expect(await screen.findByText(/Network found 2 media-ish requests/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      expect(screen.getByText(/Runner log_network history: 1/i)).toBeInTheDocument();
      expect(screen.getByText(/Latest DOM\/network: DISAGREE/i)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Crawl this listing/i }));
      expect(await screen.findByText(/Found 1 scenes/i, {}, { timeout: 5_000 })).toBeInTheDocument();

      const crawlArm = apiPostMock.mock.calls.find(
        ([path, body]) => path === "/api/captures/live_learning"
          && body?.action === "arm"
          && body?.mode === "crawl",
      );
      expect(crawlArm?.[1]).toEqual(expect.objectContaining({
        payload: expect.objectContaining({
          row_selectors: [learnedFixture.row_selector],
          trigger_selectors: [learnedFixture.trigger_selector],
        }),
      }));

      fireEvent.change(screen.getByLabelText("min_resolution"), { target: { value: "720" } });
      expect(screen.getByText("Network: idle")).toBeInTheDocument();
      expect(screen.getByText("Listing: idle")).toBeInTheDocument();
    },
    25_000,
  );

  it(
    "distinguishes an authoritatively empty listing from an unproved zero crawl",
    async () => {
      let crawlPoll = 0;
      apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363" });
        }
        if (path === "/cockpit/api/run-capture") {
          return Promise.resolve({ task: { task_id: "t_empty_listing" } });
        }
        if (path === "/api/captures/live_learning" && body?.action === "arm") {
          return Promise.resolve({
            ok: true,
            request_id: "c".repeat(32),
            state: "running",
          });
        }
        if (
          path === "/api/captures/live_learning"
          && body?.action === "poll"
          && body?.mode === "crawl"
        ) {
          crawlPoll += 1;
          return Promise.resolve({
            ok: true,
            response: {
              state: crawlPoll === 1 ? "found_nothing" : "found",
              result: crawlPoll === 1
                ? {
                    status: "EMPTY",
                    scene_count: 0,
                    plans: [],
                    reason: "Rendered listing explicitly declares zero scenes.",
                  }
                : {
                    status: "FOUND",
                    scene_count: 0,
                    plans: [],
                  },
            },
          });
        }
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      await createAndOpenSession();

      fireEvent.click(screen.getByRole("button", { name: /Crawl this listing/i }));
      expect(
        await screen.findByText("Listing: found nothing", {}, { timeout: 5_000 }),
      ).toBeInTheDocument();
      expect(screen.getByText(/explicitly declares zero scenes/i)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Crawl this listing/i }));
      expect(
        await screen.findByText(/Listing failed.*Zero scenes found/i, {}, { timeout: 5_000 }),
      ).toBeInTheDocument();
    },
    15_000,
  );

  it(
    "ignores a deferred stage response after policy invalidates the candidate",
    async () => {
      const requestId = "b".repeat(32);
      const pendingStage = deferred<Record<string, unknown>>();
      apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363" });
        }
        if (path === "/cockpit/api/run-capture") {
          return Promise.resolve({ task: { task_id: "t_stage_race" } });
        }
        if (path === "/api/captures/live_learning" && body?.action === "arm") {
          return Promise.resolve({ ok: true, request_id: requestId, state: "running" });
        }
        if (path === "/api/captures/live_learning" && body?.action === "poll") {
          return Promise.resolve({
            ok: true,
            response: { state: "found", result: learnedFixture },
          });
        }
        if (path === "/api/captures/stage_learning") return pendingStage.promise;
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      await createAndOpenSession();
      fireEvent.click(screen.getByRole("button", { name: /Learn from live page/i }));
      expect(await screen.findByText(/Planned: 1080p/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Save learned template/i }));
      await waitFor(() => {
        expect(apiPostMock.mock.calls.filter(([path]) => path === "/api/captures/stage_learning")).toHaveLength(1);
      });

      fireEvent.change(screen.getByLabelText("min_resolution"), { target: { value: "720" } });
      await act(async () => {
        pendingStage.resolve({
          ok: true,
          file: "stale.template-draft.json",
          status: "draft_review_required",
          template: {
            patterns: ["stale\\.example\\.invalid"],
            learned: { download: { row_selectors: [learnedFixture.row_selector] } },
          },
        });
        await pendingStage.promise;
      });

      expect(screen.getByRole("button", { name: "Build draft" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /6.*Review/i }));
      expect(screen.queryByText(/stale\.template-draft\.json/i)).not.toBeInTheDocument();
      expect(screen.queryByText("draft_review_required")).not.toBeInTheDocument();
    },
    25_000,
  );

  it(
    "clears an active test and ignores its deferred response after a new session starts",
    async () => {
      const testUrl = "https://members.example.invalid/scene/stale";
      const pendingExtract = deferred<{ ok: boolean }>();
      let captureNumber = 0;
      apiGetMock.mockImplementation((path: string) => {
        if (path === "/api/auth/whoami") {
          return Promise.resolve({ ok: true, user: null, multi_user: false });
        }
        if (path === "/cockpit/api/novnc") return Promise.resolve({ ok: true, url: "" });
        if (path.startsWith("/api/history")) {
          return Promise.resolve([{
            url: testUrl,
            status: "done",
            file_size: 2048,
            filename: "stale-first-session.mp4",
          }]);
        }
        return Promise.resolve({});
      });
      apiPostMock.mockImplementation((path: string) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363" });
        }
        if (path === "/cockpit/api/run-capture") {
          captureNumber += 1;
          return Promise.resolve({ task: { task_id: `t_test_race_${captureNumber}` } });
        }
        if (path === "/api/template/test_extract") return pendingExtract.promise;
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      await createAndOpenSession();
      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /5.*Test/i }));
      fireEvent.change(screen.getByPlaceholderText("https://example.com/video/123"), {
        target: { value: testUrl },
      });
      fireEvent.click(screen.getByRole("button", { name: "Run test download" }));
      await waitFor(() => {
        expect(apiPostMock.mock.calls.filter(([path]) => path === "/api/template/test_extract")).toHaveLength(1);
      });

      fireEvent.click(screen.getByRole("button", { name: /Finish & save/i }));
      expect(await screen.findByRole("button", { name: /Create site & continue/i })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Create site & continue/i }));
      expect(await screen.findByText("Start a capture")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Open session" }));
      expect(await screen.findByText("Learn the download affordance")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /5.*Test/i }));
      expect(screen.getByRole("button", { name: "Run test download" })).toBeEnabled();

      await act(async () => {
        pendingExtract.resolve({ ok: true });
        await pendingExtract.promise;
      });
      expect(screen.queryByText("stale-first-session.mp4")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Run test download" })).toBeEnabled();
    },
    25_000,
  );

  it(
    "ignores candidate A preflight after policy invalidation stages candidate B",
    async () => {
      const pendingPreflightA = deferred<{
        ok: boolean;
        gate_errors: string[];
        gate_warnings: string[];
        lint_warnings: unknown[];
      }>();
      let learnNumber = 0;
      let stageNumber = 0;
      let preflightNumber = 0;
      apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
        if (path === "/api/captures/setup_site") {
          return Promise.resolve({ ok: true, id: "site363" });
        }
        if (path === "/cockpit/api/run-capture") {
          return Promise.resolve({ task: { task_id: "t_preflight_race" } });
        }
        if (path === "/api/captures/suggest_rows") {
          return Promise.resolve(body?.action === "poll"
            ? {
                ok: true,
                groups: [{
                  selector: "a[class*='DownloadOption']",
                  count: 8,
                  visible: 8,
                }],
              }
            : { ok: true });
        }
        if (
          path === "/api/captures/live_learning"
          && body?.action === "arm"
          && body?.mode === "learn"
        ) {
          learnNumber += 1;
          return Promise.resolve({
            ok: true,
            request_id: (learnNumber === 1 ? "a" : "b").repeat(32),
            state: "running",
          });
        }
        if (
          path === "/api/captures/live_learning"
          && body?.action === "poll"
          && body?.mode === "learn"
        ) {
          return Promise.resolve({
            ok: true,
            response: { state: "found", result: learnedFixture },
          });
        }
        if (path === "/api/captures/stage_learning") {
          stageNumber += 1;
          return Promise.resolve({
            ok: true,
            file: `candidate-${stageNumber}.template-draft.json`,
            status: "draft_review_required",
            template: {
              patterns: ["members\\.example\\.invalid"],
              learned: {
                download: {
                  trigger_selectors: [learnedFixture.trigger_selector],
                  row_selectors: [learnedFixture.row_selector],
                  url_attribute: "href",
                },
              },
              config_defaults: {
                quality_preference: "4320,3160,2880,2160,1440,1080,720",
                min_resolution: stageNumber === 1 ? 1080 : 720,
              },
              resolutions: [1080],
              network_patterns: [],
              learning_evidence: {
                shape: "DROPDOWN",
                option_count: 1,
                corroboration: "DOM_ONLY",
                dom_options_proven: true,
              },
            },
          });
        }
        if (path === "/api/template_manager/promote_check") {
          preflightNumber += 1;
          if (preflightNumber === 1) return pendingPreflightA.promise;
          return Promise.resolve({
            ok: true,
            gate_errors: [],
            gate_warnings: [],
            lint_warnings: [],
          });
        }
        return Promise.resolve({ ok: true });
      });

      renderAppAt("/capture");
      await screen.findByRole("heading", { name: "Live capture workflow", level: 1 });
      await createAndOpenSession();

      fireEvent.click(screen.getByRole("button", { name: /Learn from live page/i }));
      expect(await screen.findByText(/Planned: 1080p/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Save learned template/i }));
      expect(await screen.findByText("Inspect & refine")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /7.*Promote/i }));
      fireEvent.click(screen.getByRole("button", { name: /Expert · rails off/i }));
      fireEvent.click(screen.getByRole("button", { name: "Run preflight" }));
      await waitFor(() => {
        expect(
          apiPostMock.mock.calls.filter(([path]) => path === "/api/template_manager/promote_check"),
        ).toHaveLength(1);
      });
      expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();

      fireEvent.click(screen.getByRole("button", { name: /3.*Build/i }));
      fireEvent.change(screen.getByLabelText("min_resolution"), { target: { value: "720" } });
      fireEvent.click(screen.getByRole("button", { name: /Learn from live page/i }));
      expect(await screen.findByText(/Planned: 1080p/i, {}, { timeout: 5_000 })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Save learned template/i }));
      expect(await screen.findByText("Inspect & refine")).toBeInTheDocument();
      expect(stageNumber).toBe(2);

      fireEvent.click(screen.getByRole("button", { name: /Guided · rails on/i }));
      fireEvent.click(screen.getByRole("button", { name: /6.*Review/i }));
      expect(screen.getByText(/Exact staged artifact/i)).toHaveTextContent(
        "candidate-2.template-draft.json",
      );
      fireEvent.click(screen.getByRole("button", { name: /7.*Promote/i }));
      fireEvent.click(screen.getByRole("button", { name: /Expert · rails off/i }));

      const candidateBBusyBeforeASettled = screen.getByRole("button", {
        name: /Run preflight|Checking…/,
      }).hasAttribute("disabled");
      await act(async () => {
        pendingPreflightA.resolve({
          ok: true,
          gate_errors: [],
          gate_warnings: [],
          lint_warnings: [],
        });
        await pendingPreflightA.promise;
      });

      expect({
        candidateBBusyBeforeASettled,
        staleGreenVisible: screen.queryByText(/Safe to promote/i) !== null,
      }).toEqual({
        candidateBBusyBeforeASettled: false,
        staleGreenVisible: false,
      });
      const candidateBPreflight = screen.getByRole("button", { name: "Run preflight" });
      expect(candidateBPreflight).toBeEnabled();
      fireEvent.click(candidateBPreflight);
      expect(await screen.findByText(/Safe to promote/i)).toBeInTheDocument();
      expect(preflightNumber).toBe(2);
    },
    30_000,
  );
});
