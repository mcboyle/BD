// Mutation transform control only.  This spec imports the route subjects so a
// malformed TSX mutant is caught by Vitest/esbuild, but deliberately makes no
// assertion about endpoint consumption or confirmation behaviour.  A valid
// one-click mutant must therefore ESCAPE the Python nodeid that delegates here.
import { describe, expect, it } from "vitest";

import { Library } from "@/routes/Library";
import { ImportsCenter } from "@/routes/ImportsCenter";
import { Maintenance } from "@/routes/Maintenance";
import { RebalanceCenter } from "@/routes/RebalanceCenter";

describe("T3/T4 transform control", () => {
  it("imports each route module without judging its behaviour", () => {
    expect([Library, ImportsCenter, Maintenance, RebalanceCenter].every(
      (subject) => typeof subject === "function",
    )).toBe(true);
  });
});
