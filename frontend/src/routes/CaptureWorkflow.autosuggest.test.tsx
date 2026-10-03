import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";

const { apiGetMock, apiPostMock, apiPutMock, toastSuccess } = vi.hoisted(() => ({
  apiGetMock: vi.fn(), apiPostMock: vi.fn(), apiPutMock: vi.fn(), toastSuccess: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock, apiPost: apiPostMock, apiPut: apiPutMock,
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: toastSuccess, error: vi.fn(), message: vi.fn() }),
  Toaster: () => null,
}));

import { CaptureWorkflow } from "./CaptureWorkflow";
import { renderWired, WiredProviders } from "@/test/wiredGateHarness";

const SUGGEST = "/api/captures/suggest_rows";
const callsTo = (path: string) => apiPostMock.mock.calls.filter(([url]) => url === path);
const arms = () => callsTo(SUGGEST).filter(([, body]) => body.action === "arm");

async function click(name: string | RegExp) {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
}
async function setup() {
  fireEvent.change(screen.getByPlaceholderText("WowGirls"), { target: { value: "Session site" } });
  fireEvent.change(screen.getByPlaceholderText("https://example.com/login"), {
    target: { value: "https://members.example.invalid/login" },
  });
  await click(/Create site & continue/i);
  expect(screen.getByText("Start a capture")).toBeInTheDocument();
}
async function openSession(id: string) {
  await click("Open session");
  expect(screen.getByText("Learn the download affordance")).toBeInTheDocument();
  expect(callsTo("/cockpit/api/run-capture").at(-1)?.[1]).toEqual({
    name: "capture_session", params: { url: "https://members.example.invalid/login" },
  });
  expect(arms().at(-1)?.[1], `autosuggest: session ${id} must arm its own row suggestion`).toEqual({
    task_id: id, action: "arm",
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(callsTo(SUGGEST).at(-1)?.[1]).toEqual({ task_id: id, action: "poll" });
  expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining(`.rows-${id}`));
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  apiGetMock.mockReset(); apiPostMock.mockReset(); apiPutMock.mockReset(); toastSuccess.mockReset();
  apiGetMock.mockImplementation((path: string) => Promise.resolve(
    path === "/cockpit/api/novnc" ? { ok: true, url: "" } : {},
  ));
  let sessions = 0;
  apiPostMock.mockImplementation((path: string, body?: Record<string, unknown>) => {
    if (path === "/api/captures/setup_site") return Promise.resolve({
      ok: true, id: "site_reset", login_url: "https://members.example.invalid/login",
    });
    if (path === "/cockpit/api/run-capture") return Promise.resolve({
      task: { task_id: ++sessions === 1 ? "A" : "B" },
    });
    if (path === SUGGEST && body?.action === "poll") return Promise.resolve({
      groups: [{ selector: `.rows-${body.task_id}`, count: 3, visible: 3 }],
    });
    return Promise.resolve({ ok: true });
  });
  apiPutMock.mockResolvedValue({ ok: true });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("row autosuggestion belongs to each capture session", () => {
  it("suggests once in A, again in B, and stays once through B rerenders", async () => {
    const view = renderWired(<CaptureWorkflow />, "/capture");
    await setup();
    await openSession("A");
    expect(arms()).toHaveLength(1);
    await click("Discard session");
    await setup();
    await openSession("B");
    expect(arms()).toHaveLength(2);
    await click(/Guided · rails on/i);
    view.rerender(<WiredProviders path="/capture"><CaptureWorkflow /></WiredProviders>);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(arms(), "autosuggest: B rerenders must not suggest twice").toHaveLength(2);
    expect(callsTo(SUGGEST)).toHaveLength(4);
  });

  it("negative control: returning to the capture URL keeps the same request bytes", async () => {
    renderWired(<CaptureWorkflow />, "/capture");
    await setup();
    await openSession("A");
    await click("Go to my URL");
    expect(JSON.stringify(callsTo("/cockpit/api/captures/goto"))).toBe(
      '[["/cockpit/api/captures/goto",{"task_id":"A"}]]',
    );
    expect(arms()).toHaveLength(1);
  });
});
