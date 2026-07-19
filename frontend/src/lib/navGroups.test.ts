import { describe, it, expect } from "vitest";
import { NAV_GROUPS } from "./navGroups";

// v3.66.506 — the server-rendered consoles (/framework, /fleet, /cockpit) are not
// React routes; they must be declared as external:true nav entries so the shells
// render them as <a href> (new tab) rather than react-router <NavLink> (which
// would route into the SPA catch-all). Mirrors tools/nav_reachability.check_external_nav.

describe("navGroups — external console entries", () => {
  const all = NAV_GROUPS.flatMap((g) => g.items);
  const byTo = (to: string) => all.find((i) => i.to === to);

  it.each(["/framework", "/fleet", "/cockpit"])(
    "declares %s as external:true",
    (to) => {
      const item = byTo(to);
      expect(item, `nav item for ${to}`).toBeTruthy();
      expect(item!.external).toBe(true);
    },
  );

  it("keeps internal (SPA) items non-external", () => {
    expect(byTo("/capture")?.external).toBeFalsy();
    expect(byTo("/plugins/metrics")?.external).toBeFalsy();
  });
});
