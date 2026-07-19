// App.test.tsx — v3.66.7 wiring test (Backlog #3).
//
// Proves the full ResolutionPicker flow works end-to-end inside the
// app shell, with the deep_detect HTTP endpoint stubbed.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { App, buildJobPayload } from "./App";
import type {
  DeepDetectReport,
  DownloadCandidate,
} from "./types/deepDetect";

function mkCandidate(
  label: string,
  rank: number,
  url: string,
  score: number,
  codec = "h264",
  fps = 30,
): DownloadCandidate {
  return {
    source_type: "resolution_download_card",
    url,
    click_selector: `a.resolution-card[href='${url}']`,
    height: rank,
    resolution: { label, rank },
    quality_label: label,
    codec,
    fps,
    size_bytes: null,
    requires_click: false,
    reasons: [],
    warnings: [],
    score,
  };
}

const MULTI_RES_REPORT: DeepDetectReport = {
  best_login: null,
  best_download: mkCandidate("4k", 4000, "https://ex.com/dl/4k.mp4", 175, "hevc", 60),
  login_candidates: [],
  download_candidates: [
    mkCandidate("4k", 4000, "https://ex.com/dl/4k.mp4", 175, "hevc", 60),
    mkCandidate("1080p", 1080, "https://ex.com/dl/1080.mp4", 80),
    mkCandidate("720p", 720, "https://ex.com/dl/720.mp4", 65),
  ],
  rejected: [],
  warnings: [],
  blockers: { blocked: false, do_not_auto_submit: false },
  provider_embeds: [],
  workflow_required: null,
  manifests: { hls: null, dash: null },
  source_breakdown: { resolution_download_card: 3 },
};

const SINGLE_CAND_REPORT: DeepDetectReport = {
  ...MULTI_RES_REPORT,
  download_candidates: [
    mkCandidate("1080p", 1080, "https://ex.com/dl/only.mp4", 80),
  ],
  best_download: mkCandidate("1080p", 1080, "https://ex.com/dl/only.mp4", 80),
  source_breakdown: { resolution_download_card: 1 },
};

function stubFetch(report: DeepDetectReport, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve("stubbed-error-body"),
    json: () => Promise.resolve({ report }),
  } as Response);
}

describe("App — deep_detect to ResolutionPicker wiring", () => {
  beforeEach(() => {
    // Clean fetch between tests
    vi.unstubAllGlobals();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens the ResolutionPicker when there are 2+ candidates", async () => {
    vi.stubGlobal("fetch", stubFetch(MULTI_RES_REPORT));
    const onQueue = vi.fn();
    render(<App onQueue={onQueue} />);

    fireEvent.change(screen.getByTestId("detect-url-input"), {
      target: { value: "https://ex.com/v/123" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    // Picker opens
    await waitFor(() =>
      expect(screen.getByTestId("resolution-picker")).toBeInTheDocument(),
    );

    // 3 rows visible
    expect(screen.getByTestId("resolution-picker-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-row-2")).toBeInTheDocument();

    // Nothing queued yet
    expect(onQueue).not.toHaveBeenCalled();
  });

  it("queues silently when only 1 candidate (no picker)", async () => {
    vi.stubGlobal("fetch", stubFetch(SINGLE_CAND_REPORT));
    const onQueue = vi.fn();
    render(<App onQueue={onQueue} />);

    fireEvent.change(screen.getByTestId("detect-url-input"), {
      target: { value: "https://ex.com/v/123" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    await waitFor(() => expect(onQueue).toHaveBeenCalledTimes(1));
    expect(onQueue.mock.calls[0][0]).toMatchObject({
      url: "https://ex.com/dl/only.mp4",
      resolution: "1080p",
    });

    // Picker should NOT be present
    expect(screen.queryByTestId("resolution-picker")).not.toBeInTheDocument();
  });

  it("queues the user's pick when they override best_download", async () => {
    vi.stubGlobal("fetch", stubFetch(MULTI_RES_REPORT));
    const onQueue = vi.fn();
    render(<App onQueue={onQueue} />);

    fireEvent.change(screen.getByTestId("detect-url-input"), {
      target: { value: "https://ex.com/v/123" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    await waitFor(() =>
      expect(screen.getByTestId("resolution-picker")).toBeInTheDocument(),
    );

    // Pick 720p (row 2)
    const row720 = screen
      .getByTestId("resolution-picker-row-2")
      .querySelector("input[type='radio']") as HTMLInputElement;
    fireEvent.click(row720);

    // Confirm
    fireEvent.click(screen.getByRole("button", { name: /confirm|download|ok/i }));

    await waitFor(() => expect(onQueue).toHaveBeenCalledTimes(1));
    expect(onQueue.mock.calls[0][0]).toMatchObject({
      url: "https://ex.com/dl/720.mp4",
      resolution: "720p",
    });
    // Picker closes after confirm
    expect(screen.queryByTestId("resolution-picker")).not.toBeInTheDocument();
  });

  it("surfaces HTTP errors without crashing", async () => {
    vi.stubGlobal("fetch", stubFetch(MULTI_RES_REPORT, 500));
    const onQueue = vi.fn();
    render(<App onQueue={onQueue} />);

    fireEvent.change(screen.getByTestId("detect-url-input"), {
      target: { value: "https://ex.com/v/123" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    await waitFor(() =>
      expect(screen.getByTestId("detect-error")).toBeInTheDocument(),
    );
    expect(onQueue).not.toHaveBeenCalled();
  });

  it("posts to the configured detectEndpoint", async () => {
    const fetchStub = stubFetch(SINGLE_CAND_REPORT);
    vi.stubGlobal("fetch", fetchStub);

    render(<App detectEndpoint="/custom/endpoint" onQueue={vi.fn()} />);
    fireEvent.change(screen.getByTestId("detect-url-input"), {
      target: { value: "https://ex.com/v/123" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(fetchStub.mock.calls[0][0]).toBe("/custom/endpoint");
    const body = JSON.parse(fetchStub.mock.calls[0][1].body);
    expect(body).toEqual({ url: "https://ex.com/v/123" });
  });

  it("HTML input mode posts {html} not {url}", async () => {
    const fetchStub = stubFetch(SINGLE_CAND_REPORT);
    vi.stubGlobal("fetch", fetchStub);

    render(<App onQueue={vi.fn()} />);
    // Switch to HTML mode
    fireEvent.click(screen.getByRole("radio", { name: /html/i }));
    fireEvent.change(screen.getByTestId("detect-html-input"), {
      target: { value: "<html>hi</html>" },
    });
    fireEvent.click(screen.getByTestId("detect-run"));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    const body = JSON.parse(fetchStub.mock.calls[0][1].body);
    expect(body).toEqual({ html: "<html>hi</html>" });
  });
});

describe("buildJobPayload", () => {
  it("translates a candidate into the /api/jobs shape", () => {
    const c = mkCandidate(
      "4k",
      4000,
      "https://ex.com/dl/4k.mp4",
      175,
      "hevc",
      60,
    );
    expect(buildJobPayload(c)).toEqual({
      url: "https://ex.com/dl/4k.mp4",
      click_selector: "a.resolution-card[href='https://ex.com/dl/4k.mp4']",
      resolution: "4k",
      codec: "hevc",
      fps: 60,
      source_type: "resolution_download_card",
    });
  });

  it("prefers quality_label over resolution.label", () => {
    const c: DownloadCandidate = {
      source_type: "resolution_download_card",
      url: "https://x",
      quality_label: "ultra-hd",
      resolution: { label: "4k", rank: 4000 },
      score: 1,
    };
    expect(buildJobPayload(c).resolution).toBe("ultra-hd");
  });

  it("handles null url (click-only candidate)", () => {
    const c: DownloadCandidate = {
      source_type: "resolution_download_card",
      url: null,
      click_selector: "a.dl",
      resolution: { label: "1080p", rank: 1080 },
      requires_click: true,
      score: 1,
    };
    expect(buildJobPayload(c)).toEqual({
      url: null,
      click_selector: "a.dl",
      resolution: "1080p",
      codec: undefined,
      fps: undefined,
      source_type: "resolution_download_card",
    });
  });
});
