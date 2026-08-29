import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import {
  AffordanceLearningPanel,
  selectOptionForPolicy,
} from "./AffordanceLearningPanel";

const actions = {
  onLearn: vi.fn(),
  onCaptureNetwork: vi.fn(),
  onCrawl: vi.fn(),
  onSave: vi.fn(),
};

describe("AffordanceLearningPanel row 363 states", () => {
  it("honors a leading best token in the ordered quality preference cascade", () => {
    const options = [
      { height: 720, label: "HD 720p", href: "/720p/mp4" },
      { height: 1080, label: "Full HD 1080p", href: "/1080p/mp4" },
      { height: 2160, label: "4K 2160p", href: "/2160p/mp4" },
    ];

    expect(selectOptionForPolicy(options, "best,1080", 720)).toMatchObject({
      status: "SELECTED",
      option: { height: 2160 },
    });
    expect(selectOptionForPolicy(options, "1080,best", 720)).toMatchObject({
      status: "SELECTED",
      option: { height: 1080 },
    });
    expect(selectOptionForPolicy([options[2]], "1080,best", 720)).toMatchObject({
      status: "SELECTED",
      option: { height: 2160 },
    });
  });

  it("applies min_resolution after the first cascade token finds an option", () => {
    const options = [
      { height: 720, label: "HD 720p", href: "/720p/mp4" },
      { height: 2160, label: "4K 2160p", href: "/2160p/mp4" },
    ];

    expect(selectOptionForPolicy(options, "1080,best", 1080)).toMatchObject({
      status: "BELOW_MIN_RESOLUTION",
      option: null,
      reason: expect.stringMatching(/720p.*below min_resolution 1080p/i),
    });
  });

  it("renders three separate idle actions for learning, network, and listing crawl", () => {
    render(
      <AffordanceLearningPanel
        learning={{ state: "idle" }}
        network={{ state: "idle" }}
        crawl={{ state: "idle" }}
        qualityPreference="1080,720"
        minResolution={720}
        {...actions}
      />,
    );

    expect(screen.getByRole("button", { name: /learn from live page/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /capture network evidence/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /crawl this listing/i })).toBeEnabled();
    expect(screen.getByText("Learning: idle")).toBeInTheDocument();
    expect(screen.getByText("Network: idle")).toBeInTheDocument();
    expect(screen.getByText("Listing: idle")).toBeInTheDocument();
  });

  it("shows a legitimate nothing-found result as UNKNOWN, not success", () => {
    render(
      <AffordanceLearningPanel
        learning={{
          state: "nothing",
          result: {
            status: "UNKNOWN",
            shape: "UNKNOWN",
            options: [],
            selector_attempts: [],
          },
        }}
        network={{ state: "nothing", count: 0 }}
        crawl={{ state: "idle" }}
        qualityPreference="1080,720"
        minResolution={720}
        {...actions}
      />,
    );

    expect(screen.getByText(/UNKNOWN.*nothing found/i)).toBeInTheDocument();
    expect(screen.getByText("Network evidence found nothing.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save learned template/i })).toBeDisabled();
  });

  it("shows every option and refuses save when all are below min_resolution", () => {
    render(
      <AffordanceLearningPanel
        learning={{
          state: "found",
          result: {
            status: "FOUND",
            shape: "BAR",
            row_selector: "a[class*='DownloadOption']",
            trigger_selector: null,
            options: [
              { height: 540, container: "mp4", size: "692.70 MB", label: "Web HD 540p", href: "/540p/mp4" },
              { height: 720, container: "mp4", size: "1.08 GB", label: "HD 720p", href: "/720p/mp4" },
            ],
            selection: {
              status: "BELOW_MIN_RESOLUTION",
              option: null,
              reason: "Best available 720p is below min_resolution 1080p",
            },
            network_evidence: [
              {
                url: "https://media.example.invalid/master/1080p.m3u8?token=<scrubbed>",
                kind: "manifest",
                status: 200,
              },
            ],
            corroboration: {
              status: "DISAGREE",
              detail: "DOM and network exposed different paths; both are retained.",
            },
            selector_attempts: [],
          },
        }}
        network={{
          state: "found",
          count: 2,
          evidence: [
            { url: "https://service.example.invalid/user/history/download/site/<scrubbed>", kind: "download_history" },
            { url: "https://media.example.invalid/manifest/master.mpd", kind: "manifest" },
          ],
        }}
        crawl={{ state: "idle" }}
        qualityPreference="2160,1080,720"
        minResolution={1080}
        {...actions}
      />,
    );

    expect(screen.getByText(/BAR.*a\[class\*='DownloadOption'\]/i)).toBeInTheDocument();
    expect(screen.getByText(/Web HD 540p/)).toBeInTheDocument();
    expect(screen.getByText(/HD 720p/)).toBeInTheDocument();
    const webHdRow = screen.getByText("Web HD 540p").closest("tr");
    expect(webHdRow).not.toBeNull();
    expect(within(webHdRow!).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "Web HD 540p",
      "540p",
      "mp4",
      "692.70 MB",
    ]);
    expect(screen.getByText("Learning: found 2")).toBeInTheDocument();
    expect(screen.getByText("Network: found 2")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/below min_resolution 1080p/i);
    expect(screen.getByText(/Capture evidence available to learning: 1 media-ish request/i)).toBeInTheDocument();
    expect(screen.getAllByText(/DOM\/network: DISAGREE/i)).toHaveLength(2);
    expect(screen.getByRole("button", { name: /save learned template/i })).toBeDisabled();
  });

  it("shows runner history without using it as current-page corroboration", () => {
    render(
      <AffordanceLearningPanel
        learning={{
          state: "found",
          result: {
            status: "FOUND",
            shape: "BAR",
            row_selector: "a[class*='DownloadOption']",
            options: [
              {
                height: 1080,
                container: "mp4",
                label: "Full HD 1080p",
                href: "/movieaction/download/fixture/1080p/mp4",
              },
            ],
            selector_attempts: [],
          },
        }}
        network={{
          state: "found",
          count: 2,
          evidence: [
            { url: "https://media.example.invalid/current/master.mpd", kind: "manifest" },
          ],
          runnerEvidence: [
            {
              url: "200 GET https://members.example.invalid/movieaction/download/fixture/1080p/mp4",
              kind: "runner_network",
            },
          ],
        }}
        crawl={{ state: "idle" }}
        qualityPreference="1080"
        minResolution={720}
        {...actions}
      />,
    );

    expect(screen.getByText(/Latest DOM\/network: DISAGREE/i)).toBeInTheDocument();
    expect(screen.getByText(/Runner log_network history: 1/i)).toBeInTheDocument();
  });

  it("renders a found listing plan before download and invokes no fetch action", () => {
    const onCrawl = vi.fn();
    render(
      <AffordanceLearningPanel
        learning={{ state: "idle" }}
        network={{ state: "idle" }}
        crawl={{
          state: "found",
          count: 2,
          plans: [
            { url: "https://members.example.invalid/scene/one", chosen_height: 1080, status: "PLANNED" },
            {
              url: "https://members.example.invalid/scene/two",
              chosen_height: null,
              status: "REFUSED",
              selection_status: "BELOW_MIN_RESOLUTION",
              reason: "Best available 720p is below min_resolution 1080p",
            },
          ],
        }}
        qualityPreference="1080,720"
        minResolution={720}
        {...actions}
        onCrawl={onCrawl}
      />,
    );

    expect(screen.getByText(/Found 2 scenes/i)).toBeInTheDocument();
    expect(screen.getAllByText(/1080p/)).toHaveLength(2);
    expect(screen.getByText(/Best available 720p is below min_resolution/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
    expect(onCrawl).not.toHaveBeenCalled();
  });

  it("keeps listing zero and request errors visually distinct", () => {
    const { rerender } = render(
      <AffordanceLearningPanel
        learning={{ state: "idle" }}
        network={{ state: "idle" }}
        crawl={{
          state: "nothing",
          count: 0,
          reason: "Rendered listing explicitly declares zero scenes.",
        }}
        qualityPreference="1080"
        minResolution={720}
        {...actions}
      />,
    );
    expect(screen.getByText("Listing: found nothing")).toBeInTheDocument();
    expect(screen.getByText(/explicitly declares zero scenes/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(
      <AffordanceLearningPanel
        learning={{ state: "failed", error: "capture bridge failed" }}
        network={{ state: "idle" }}
        crawl={{ state: "failed", error: "zero scenes found on rendered listing" }}
        qualityPreference="1080"
        minResolution={720}
        {...actions}
      />,
    );
    expect(screen.getByText(/Learning failed.*capture bridge failed/i)).toBeInTheDocument();
    expect(screen.getByText(/Listing failed.*zero scenes found/i)).toBeInTheDocument();

    rerender(
      <AffordanceLearningPanel
        learning={{ state: "running" }}
        network={{ state: "running" }}
        crawl={{ state: "running" }}
        qualityPreference="1080"
        minResolution={720}
        {...actions}
      />,
    );
    expect(screen.getByText(/Learning from the live page/i)).toBeInTheDocument();
    expect(screen.getByText(/Recording media-ish requests/i)).toBeInTheDocument();
    expect(screen.getByText(/Crawling pagination and infinite scroll/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Learning…/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Capturing…/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Crawling…/i })).toBeDisabled();
  });

  it("wires the existing quality policy inputs instead of inventing a second policy", () => {
    const onQualityPreferenceChange = vi.fn();
    const onMinResolutionChange = vi.fn();
    render(
      <AffordanceLearningPanel
        learning={{ state: "idle" }}
        network={{ state: "idle" }}
        crawl={{ state: "idle" }}
        qualityPreference="1080,720"
        minResolution={720}
        onQualityPreferenceChange={onQualityPreferenceChange}
        onMinResolutionChange={onMinResolutionChange}
        {...actions}
      />,
    );
    fireEvent.change(screen.getByLabelText(/quality_preference/i), {
      target: { value: "2160,1080" },
    });
    fireEvent.change(screen.getByLabelText(/min_resolution/i), {
      target: { value: "1080" },
    });
    expect(onQualityPreferenceChange).toHaveBeenCalledWith("2160,1080");
    expect(onMinResolutionChange).toHaveBeenCalledWith(1080);
  });
});
