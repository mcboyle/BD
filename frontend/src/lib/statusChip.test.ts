import { describe, it, expect } from "vitest";
import { statusChipText } from "./statusChip";

// P6-2 (feedback) — the DesktopShell footer status chip grows from "Idle · 32m"
// to surface queued count + worker usage: "Running · 3 queued · 2/4 workers · 32m".
// Honesty: workers_total is None on an empty fleet (frozen by
// test_u49_workers_total_honest) — never fabricate a denominator.

describe("statusChipText (P6-2)", () => {
  it("shows Idle with no activity", () => {
    expect(statusChipText({ activeDownloads: 0, queueDepth: 0 })).toBe(
      "Idle · 0 queued",
    );
  });

  it("shows Running when downloads are in flight", () => {
    expect(
      statusChipText({ activeDownloads: 2, queueDepth: 5 }),
    ).toBe("Running · 5 queued");
  });

  it("includes worker usage when a total is known", () => {
    expect(
      statusChipText({
        activeDownloads: 2,
        queueDepth: 5,
        workersActive: 2,
        workersTotal: 4,
      }),
    ).toBe("Running · 5 queued · 2/4 workers");
  });

  it("never fabricates a denominator when workersTotal is null (empty fleet)", () => {
    // workersTotal null -> no "/N"; show active count alone if non-zero
    expect(
      statusChipText({
        activeDownloads: 1,
        queueDepth: 0,
        workersActive: 1,
        workersTotal: null,
      }),
    ).toBe("Running · 0 queued · 1 active");
  });

  it("omits workers entirely when nothing is active and total is unknown", () => {
    expect(
      statusChipText({
        activeDownloads: 0,
        queueDepth: 0,
        workersActive: 0,
        workersTotal: null,
      }),
    ).toBe("Idle · 0 queued");
  });

  it("appends uptime when provided", () => {
    expect(
      statusChipText({ activeDownloads: 0, queueDepth: 0, uptime: "32m" }),
    ).toBe("Idle · 0 queued · 32m");
  });

  it("omits queued when queueDepth is undefined (data not yet loaded)", () => {
    expect(statusChipText({ activeDownloads: 0 })).toBe("Idle");
  });
});
