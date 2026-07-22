import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("SiteDetail widget data scope", () => {
  it("passes the route siteId to the live widget-data hook", () => {
    const source = readFileSync(resolve("src/routes/SiteDetail.tsx"), "utf8");

    expect(source).toMatch(/useWidgetData\(siteId\)/);
  });
});
