import { describe, expect, it } from "vitest";

import {
  failureHint,
  firstBlocker,
  type GuidedCtx,
  mapVerdict,
  promoteGateWarnings,
  readinessAllGreen,
  readinessBoard,
  sceneCrawlView,
  stepReady,
  stepStatus,
  triggerMatchCountFromSandbox,
} from "@/lib/guidedCapture";

describe("sceneCrawlView", () => {
  it("keeps NOT_LOGGED_IN distinct from a completed zero-scene crawl", () => {
    const view = sceneCrawlView({
      state: "NOT_LOGGED_IN",
      discovered: 0,
      queued: 0,
      pages_walked: 1,
      zero_scenes_found: false,
    });
    expect(view.tone).toBe("warning");
    expect(view.label).toMatch(/not logged in/i);
    expect(view.label).not.toMatch(/no scenes/i);
  });

  it("reports exact discovered, queued and page counts for a completed run", () => {
    const view = sceneCrawlView({
      state: "COMPLETED",
      discovered: 9,
      queued: 8,
      pages_walked: 3,
      zero_scenes_found: false,
    });
    expect(view.label).toContain("9 discovered");
    expect(view.label).toContain("8 queued");
    expect(view.label).toContain("3 pages");
  });
});

function ctx(over: Partial<GuidedCtx> = {}): GuidedCtx {
  return {
    setupNameOk: true,
    setupUrlOk: true,
    downloadRootOk: true,
    sessionLive: true,
    loggedIn: true,
    contentPageVisited: true,
    liveLearningAttempted: true,
    resolutionPolicySatisfied: true,
    draftBuilt: true,
    requiredSelectorsResolved: true,
    verdictState: "DONE",
    candidateAssembled: true,
    unreviewedAiEdits: 0,
    promotePreflightOk: true,
    ...over,
  };
}

describe("mapVerdict", () => {
  it("null row → PENDING, not a pass", () => {
    const v = mapVerdict(null);
    expect(v.state).toBe("PENDING");
    expect(v.pass).toBe(false);
  });

  it("done + bytes → DONE saved, pass", () => {
    const v = mapVerdict({ status: "done", file_size: 2800, filename: "a.mp4" });
    expect(v.state).toBe("DONE");
    expect(v.pass).toBe(true);
    expect(v.label.toLowerCase()).toContain("validated");
  });

  it("done + 0 bytes → DONE validated-not-saved (persist-off probe is a pass)", () => {
    const v = mapVerdict({ status: "done", file_size: 0, message: "aborted" });
    expect(v.state).toBe("DONE");
    expect(v.pass).toBe(true);
    expect(v.label.toLowerCase()).toContain("not saved");
  });

  it("needs_review → NEEDS_REVIEW, not auto-pass", () => {
    const v = mapVerdict({ status: "needs_review" });
    expect(v.state).toBe("NEEDS_REVIEW");
    expect(v.pass).toBe(false);
  });

  it("failed / error → FAILED", () => {
    expect(mapVerdict({ status: "failed" }).state).toBe("FAILED");
    expect(mapVerdict({ status: "error" }).state).toBe("FAILED");
  });

  it("running → PENDING", () => {
    expect(mapVerdict({ status: "running" }).state).toBe("PENDING");
  });
});

describe("failureHint", () => {
  it("download-root reason → allowlist remedy", () => {
    const h = failureHint("not under an allowed root");
    expect(h.fix.toLowerCase()).toContain("allowlist");
  });
  it("blocked term → review remedy", () => {
    expect(failureHint("contains a blocked term").fix.toLowerCase()).toContain("review");
  });
  it("challenge → manual handoff, flow paused not failed", () => {
    const h = failureHint("challenge detected");
    expect(h.fix.toLowerCase()).toContain("hand");
    expect(h.fix.toLowerCase()).toContain("paused");
  });
  it("never echoes a raw empty reason", () => {
    expect(failureHint("").sentence.length).toBeGreaterThan(0);
    expect(failureHint(null).fix.length).toBeGreaterThan(0);
  });
});

describe("step readiness + status map", () => {
  it("a fully-green ctx makes every step ready", () => {
    for (const s of [
      "setup",
      "capture",
      "build",
      "inspect",
      "test",
      "review",
      "promote",
    ] as const) {
      expect(stepReady(s, ctx())).toBe(true);
    }
  });

  it("setup is blocked on a bad download root and names it", () => {
    const c = ctx({ downloadRootOk: false });
    expect(stepReady("setup", c)).toBe(false);
    expect(firstBlocker("setup", c)).toContain("allowed root");
  });

  it("test gate accepts a NEEDS_REVIEW override but blocks PENDING", () => {
    expect(stepReady("test", ctx({ verdictState: "NEEDS_REVIEW" }))).toBe(true);
    expect(stepReady("test", ctx({ verdictState: "PENDING" }))).toBe(false);
    expect(stepReady("test", ctx({ verdictState: "FAILED" }))).toBe(false);
  });

  it("build stays blocked until live affordance learning has been attempted", () => {
    const c = ctx({ liveLearningAttempted: false });
    expect(stepReady("build", c)).toBe(false);
    expect(firstBlocker("build", c)).toContain("live download affordance");
  });

  it("build refuses to advance when every learned option violates min_resolution", () => {
    const c = ctx({ resolutionPolicySatisfied: false });
    expect(stepReady("build", c)).toBe(false);
    expect(firstBlocker("build", c)).toContain("min_resolution");
  });

  it("promote is blocked until preflight is green", () => {
    expect(stepReady("promote", ctx({ promotePreflightOk: false }))).toBe(false);
  });

  it("status map: past=done when ready, current=current, future=locked", () => {
    const c = ctx();
    expect(stepStatus("setup", "build", c)).toBe("done");
    expect(stepStatus("build", "build", c)).toBe("current");
    expect(stepStatus("promote", "build", c)).toBe("locked");
  });

  it("a past step that lost its precondition shows blocked, not stale done", () => {
    const c = ctx({ downloadRootOk: false });
    expect(stepStatus("setup", "test", c)).toBe("blocked");
  });
});

describe("readiness board", () => {
  it("hides cred/AI rows when not applicable", () => {
    const rows = readinessBoard({
      downloadRootOk: true,
      allowlistConfigured: true,
      secretsUnlocked: null,
      aiReachable: null,
    });
    expect(rows.length).toBe(2);
  });

  it("all-green only when every present row is ok", () => {
    expect(
      readinessAllGreen({
        downloadRootOk: true,
        allowlistConfigured: true,
        secretsUnlocked: true,
        aiReachable: null,
      }),
    ).toBe(true);
    expect(
      readinessAllGreen({
        downloadRootOk: false,
        allowlistConfigured: true,
        secretsUnlocked: null,
        aiReachable: null,
      }),
    ).toBe(false);
  });
});

describe("promoteGateWarnings (2c-guard live-trigger interlock)", () => {
  it("returns the soft gate_warnings from a promote_check response", () => {
    const w = promoteGateWarnings({
      ok: true,
      gate_warnings: ["download trigger matches 0 elements on the live page"],
    });
    expect(w).toHaveLength(1);
    expect(w[0].toLowerCase()).toContain("live");
  });

  it("is empty when no gate_warnings present (unchecked / matched)", () => {
    expect(promoteGateWarnings({ ok: true })).toEqual([]);
    expect(promoteGateWarnings({ ok: true, gate_warnings: [] })).toEqual([]);
  });

  it("never throws on a malformed response", () => {
    // @ts-expect-error intentionally malformed
    expect(promoteGateWarnings(null)).toEqual([]);
    // @ts-expect-error intentionally malformed
    expect(promoteGateWarnings({ gate_warnings: "nope" })).toEqual([]);
  });
});

describe("triggerMatchCountFromSandbox (2c-guard live-count source)", () => {
  it("returns 0 when the trigger selector matched 0 live elements", () => {
    const n = triggerMatchCountFromSandbox({
      ok: true,
      matches: {
        trigger_selector: { selector: "a.dl", match_count: 0, samples: [] },
        dl_selector: { selector: "", match_count: 0, samples: [] },
      },
    });
    expect(n).toBe(0);
  });

  it("returns the live count when the trigger matched N elements", () => {
    const n = triggerMatchCountFromSandbox({
      ok: true,
      matches: { trigger_selector: { selector: "a.dl", match_count: 7 } },
    });
    expect(n).toBe(7);
  });

  it("returns null when no trigger match info is present (unknown)", () => {
    expect(triggerMatchCountFromSandbox({ ok: true, matches: {} })).toBeNull();
    expect(triggerMatchCountFromSandbox({ ok: true })).toBeNull();
    expect(triggerMatchCountFromSandbox({ ok: false, error: "fetch failed" })).toBeNull();
  });

  it("never throws on a malformed response", () => {
    // @ts-expect-error intentionally malformed
    expect(triggerMatchCountFromSandbox(null)).toBeNull();
    // @ts-expect-error intentionally malformed
    expect(triggerMatchCountFromSandbox({ matches: { trigger_selector: {} } })).toBeNull();
    // @ts-expect-error intentionally malformed
    expect(triggerMatchCountFromSandbox({ matches: { trigger_selector: { match_count: "x" } } })).toBeNull();
  });
});
