// Vitest + RTL tests for ResolutionPicker.
// Run via `cd spa && ./node_modules/.bin/vitest run` (or with the
// project's node kit: `../node/bin/node ./node_modules/vitest/vitest.mjs run`).

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import {
  ResolutionPicker,
  formatBytes,
  candidateKey,
  shouldPrompt,
} from "./ResolutionPicker";
import type {
  DeepDetectReport,
  DownloadCandidate,
} from "../types/deepDetect";

function card(
  partial: Partial<DownloadCandidate> & { label: string; rank: number },
): DownloadCandidate {
  return {
    source_type: "resolution_download_card",
    url: null,
    width: null,
    height: partial.rank,
    resolution: { label: partial.label, rank: partial.rank },
    quality_label: partial.label,
    codec: "h264",
    fps: 30,
    size_bytes: null,
    requires_click: false,
    text: "",
    reasons: [],
    warnings: [],
    score: 100,
    ...partial,
  } as DownloadCandidate;
}

function makeReport(cards: DownloadCandidate[]): DeepDetectReport {
  return {
    best_login: null,
    best_download: cards[0] || null,
    login_candidates: [],
    download_candidates: cards,
    rejected: [],
    warnings: [],
    blockers: {},
    provider_embeds: [],
    workflow_required: null,
    manifests: { hls: null, dash: null },
    source_breakdown: { resolution_download_card: cards.length },
  };
}

describe("formatBytes", () => {
  it("handles null/zero", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(0)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
  });
  it("formats bytes/KB without decimals", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2 KB");
  });
  it("formats MB/GB with 1 decimal", () => {
    expect(formatBytes(5_242_880)).toBe("5.0 MB");
    expect(formatBytes(2_147_483_648)).toBe("2.0 GB");
  });
});

describe("candidateKey", () => {
  it("prefers URL", () => {
    const c = card({ label: "1080p", rank: 1080, url: "https://x/y.mp4" });
    expect(candidateKey(c, 0)).toBe("url:https://x/y.mp4");
  });
  it("falls back to click_selector", () => {
    const c = card({
      label: "1080p",
      rank: 1080,
      url: null,
      click_selector: "button.dl[data-q='1080p']",
    });
    expect(candidateKey(c, 0)).toContain("click:button.dl");
  });
  it("falls back to idx as last resort", () => {
    const c = card({ label: "720p", rank: 720, url: null });
    expect(candidateKey(c, 3)).toBe("idx:3:720p");
  });
});

describe("shouldPrompt", () => {
  it("returns false for 0 or 1 cards", () => {
    expect(shouldPrompt(makeReport([]))).toBe(false);
    expect(shouldPrompt(makeReport([card({ label: "1080p", rank: 1080 })]))).toBe(
      false,
    );
  });
  it("returns true for 2+ resolution cards", () => {
    expect(
      shouldPrompt(
        makeReport([
          card({ label: "1080p", rank: 1080 }),
          card({ label: "720p", rank: 720 }),
        ]),
      ),
    ).toBe(true);
  });
  it("ignores non-resolution-card sources for the threshold", () => {
    const cards = [
      card({ label: "1080p", rank: 1080 }),
      { ...card({ label: "1080p", rank: 1080 }), source_type: "state_url" },
    ];
    expect(shouldPrompt(makeReport(cards))).toBe(false);
  });
});

describe("<ResolutionPicker />", () => {
  it("does not render when open=false", () => {
    const r = makeReport([
      card({ label: "1080p", rank: 1080 }),
      card({ label: "720p", rank: 720 }),
    ]);
    const { container } = render(
      <ResolutionPicker
        report={r}
        open={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one row per candidate", () => {
    const r = makeReport([
      card({ label: "4k", rank: 2160 }),
      card({ label: "1080p", rank: 1080 }),
      card({ label: "720p", rank: 720 }),
    ]);
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId("resolution-picker-row-0")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-row-2")).toBeInTheDocument();
    expect(screen.queryByTestId("resolution-picker-row-3")).toBeNull();
  });

  it("defaults selection to best_download", () => {
    const cards = [
      card({ label: "4k", rank: 2160 }),
      card({ label: "1080p", rank: 1080 }),
    ];
    const r = makeReport(cards);
    r.best_download = cards[1]; // 1080p, not the top-ranked 4k
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const row1 = screen
      .getByTestId("resolution-picker-row-1")
      .querySelector("input[type='radio']") as HTMLInputElement;
    const row0 = screen
      .getByTestId("resolution-picker-row-0")
      .querySelector("input[type='radio']") as HTMLInputElement;
    expect(row1.checked).toBe(true);
    expect(row0.checked).toBe(false);
  });

  it("invokes onConfirm with the selected candidate", () => {
    const cards = [
      card({ label: "4k", rank: 2160 }),
      card({ label: "1080p", rank: 1080 }),
    ];
    const r = makeReport(cards);
    const onConfirm = vi.fn();
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    // change selection to row 1 (1080p)
    const row1 = screen
      .getByTestId("resolution-picker-row-1")
      .querySelector("input[type='radio']") as HTMLInputElement;
    fireEvent.click(row1);
    fireEvent.click(screen.getByTestId("resolution-picker-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0].quality_label).toBe("1080p");
  });

  it("invokes onCancel from the Cancel button", () => {
    const r = makeReport([card({ label: "1080p", rank: 1080 })]);
    const onCancel = vi.fn();
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("invokes onCancel on ESC", () => {
    const r = makeReport([card({ label: "1080p", rank: 1080 })]);
    const onCancel = vi.fn();
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders the disclaimer banner when probes.disclaimer is set", () => {
    const r = makeReport([
      card({ label: "1080p", rank: 1080 }),
      card({ label: "720p", rank: 720 }),
    ]);
    r.probes = { disclaimer: "DRM markers detected — proceed at your own risk." };
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const banner = screen.getByTestId("resolution-picker-disclaimer");
    expect(banner.textContent).toContain("DRM markers detected");
  });

  it("shows an empty-state message when there are no candidates", () => {
    const r = makeReport([]);
    render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId("resolution-picker-empty")).toBeInTheDocument();
    expect(
      (screen.getByTestId("resolution-picker-confirm") as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("marks click-required candidates with a 'click' tag", () => {
    const cards = [
      { ...card({ label: "1080p", rank: 1080 }), requires_click: true },
      card({ label: "720p", rank: 720 }),
    ];
    const r = makeReport(cards);
    const { container } = render(
      <ResolutionPicker
        report={r}
        open={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const tags = container.querySelectorAll("span");
    const clickTag = Array.from(tags).find((s) =>
      (s.textContent || "").toLowerCase().includes("click"),
    );
    expect(clickTag).toBeTruthy();
  });
});
