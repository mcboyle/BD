import { describe, expect, it } from "vitest";

import {
  driftCompareState,
  driftCompareView,
  driftedSlots,
  type DriftSlotKey,
  mapVerifyRow,
  repairSummary,
  type SlotResolution,
  verifyView,
} from "@/lib/guidedDrift";

function slots(
  o: Partial<Record<DriftSlotKey, SlotResolution>>,
): Partial<Record<DriftSlotKey, SlotResolution>> {
  return o;
}

describe("drift compare state machine", () => {
  it("all unchecked → idle", () => {
    expect(driftCompareState({})).toBe("idle");
    expect(
      driftCompareState(slots({ download_trigger: "unchecked" })),
    ).toBe("idle");
  });

  it("some checked, some not → comparing", () => {
    expect(
      driftCompareState(slots({ download_trigger: "resolved" })),
    ).toBe("comparing");
  });

  it("all checked, none drifted → clean", () => {
    expect(
      driftCompareState(
        slots({
          download_trigger: "resolved",
          row_selectors: "resolved",
          play_button: "resolved",
        }),
      ),
    ).toBe("clean");
  });

  it("all checked, ≥1 drifted → drifted, and lists them", () => {
    const s = slots({
      download_trigger: "resolved",
      row_selectors: "drifted",
      play_button: "resolved",
    });
    expect(driftCompareState(s)).toBe("drifted");
    expect(driftedSlots(s)).toEqual(["row_selectors"]);
  });

  it("respects the applicable-slots subset (candidate without a play button)", () => {
    const s = slots({ download_trigger: "resolved", row_selectors: "resolved" });
    expect(driftCompareState(s, ["download_trigger", "row_selectors"])).toBe(
      "clean",
    );
  });

  it("view labels match the state and name the drifted slots", () => {
    expect(driftCompareView({}).state).toBe("idle");
    const v = driftCompareView(
      slots({
        download_trigger: "drifted",
        row_selectors: "resolved",
        play_button: "resolved",
      }),
    );
    expect(v.state).toBe("drifted");
    expect(v.label).toContain("1 selector");
    expect(v.detail).toContain("Download trigger");
  });
});

describe("repair summary (entry on an enabled site)", () => {
  it("healthy site → not needed", () => {
    const r = repairSummary({ consecutive_failures: 0, flagged_stale: false });
    expect(r.needed).toBe(false);
    expect(r.headline.toLowerCase()).toContain("healthy");
  });

  it("null drift → not needed (no crash)", () => {
    expect(repairSummary(null).needed).toBe(false);
  });

  it("flagged stale → needed, headline carries the failure count + host signal", () => {
    const r = repairSummary(
      {
        consecutive_failures: 3,
        flagged_stale: true,
        last_selector: ".dl",
        last_url: "https://x/y",
      },
      { apiHostChanged: false },
    );
    expect(r.needed).toBe(true);
    expect(r.flaggedStale).toBe(true);
    expect(r.consecutiveFailures).toBe(3);
    expect(r.headline).toContain("3 consecutive");
    expect(r.headline.toLowerCase()).toContain("api host unchanged");
    expect(r.jumpSlot).toBe("row_selectors");
  });

  it("failures without the stale flag still trigger repair", () => {
    expect(repairSummary({ consecutive_failures: 1 }).needed).toBe(true);
  });

  it("api host changed is surfaced when signalled", () => {
    const r = repairSummary(
      { consecutive_failures: 2 },
      { apiHostChanged: true },
    );
    expect(r.headline.toLowerCase()).toContain("api host changed");
  });
});

describe("post-promote verify", () => {
  it("no row → still watching", () => {
    expect(mapVerifyRow(null)).toBe("watching");
    expect(mapVerifyRow({ status: "running" })).toBe("watching");
  });

  it("done+bytes → verified", () => {
    expect(mapVerifyRow({ status: "done", file_size: 1200 })).toBe("verified");
  });

  it("persist-off probe (done+0) is also verified", () => {
    expect(mapVerifyRow({ status: "done", file_size: 0 })).toBe("verified");
  });

  it("failed → failed", () => {
    expect(mapVerifyRow({ status: "failed" })).toBe("failed");
    expect(mapVerifyRow({ status: "error" })).toBe("failed");
  });

  it("needs_review stays watching (not a terminal verify)", () => {
    expect(mapVerifyRow({ status: "needs_review" })).toBe("watching");
  });

  it("view marks verified/failed as done, others not", () => {
    expect(verifyView("idle").done).toBe(false);
    expect(verifyView("watching").done).toBe(false);
    expect(verifyView("verified").done).toBe(true);
    expect(verifyView("failed").done).toBe(true);
  });
});
