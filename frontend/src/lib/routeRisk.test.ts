import { describe, it, expect } from "vitest";
import { routeRisk, type RouteRisk } from "./routeRisk";

// Cut 1 substrate: one source of truth mapping a route to its risk profile so
// banner tiering, Danger/Integrity-zone placement, and verification all read
// from the same place (no per-page drift).

describe("routeRisk", () => {
  it("returns a full descriptor for a known high-risk route", () => {
    const r: RouteRisk = routeRisk("/secrets");
    expect(r.severity).toBe("high");
    expect(r.bannerShape).toBe("full");
    expect(r.needsDangerZone).toBe(true);
  });

  it("maps a lower-risk read-mostly route to the chip banner", () => {
    const r = routeRisk("/history");
    expect(r.bannerShape).toBe("chip");
    expect(r.severity).toBe("low");
  });

  it("falls back to a safe default for an unknown route", () => {
    const r = routeRisk("/this-route-does-not-exist");
    // Unknown -> conservative but non-blocking: a low-severity chip, no zones.
    expect(r.severity).toBe("low");
    expect(r.bannerShape).toBe("chip");
    expect(r.needsDangerZone).toBe(false);
    expect(r.needsIntegrityZone).toBe(false);
  });

  it("flags the capture/redaction route as needing an integrity zone", () => {
    const r = routeRisk("/settings");
    expect(r.needsIntegrityZone).toBe(true);
  });
});
