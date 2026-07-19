import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Source-level guards for the FE-batch fixes whose behaviour lives in full route
// components (too heavy to render in isolation). Each asserts the specific
// dangerous sink/pattern is gone and the safe replacement is present.
const read = (p: string) => readFileSync(join(process.cwd(), p), "utf8");

describe("F-FE04-01: pairing QR is not a raw-HTML/SVG XSS sink", () => {
  const cluster = read("src/routes/Cluster.tsx");
  it("Cluster.tsx has no dangerouslySetInnerHTML", () => {
    expect(cluster).not.toMatch(/dangerouslySetInnerHTML/);
  });
  it("renders the QR through an <img> data-URL (script cannot execute)", () => {
    expect(cluster).toMatch(/data:image\/svg\+xml/);
  });
});

describe("F-FE06-01: review-item URL href is scheme-gated", () => {
  const nr = read("src/routes/NeedsReview.tsx");
  it("NeedsReview.tsx gates the external link via isHttpUrl", () => {
    expect(nr).toMatch(/isHttpUrl/);
  });
  it("the isHttpUrl gate is applied to current.url (the anchor is conditional)", () => {
    expect(nr).toMatch(/isHttpUrl\(current\.url\)/);
  });
});

describe("F-FE02-01: fake 'typed-confirm' phrase labels removed (1-click confirm)", () => {
  // The fake gate was a standalone amber monospace <p> whose sole child was the
  // confirm token, implying a "type this phrase" requirement that was never
  // enforced. The dialogs keep their title/description + No-default footer.
  const FAKE_LABEL =
    /text-amber-300">\{(pending\.token|DELETE_TUNNEL_TOKEN|"CLEAR KILL")\}/;
  for (const f of [
    "src/routes/Vpn.tsx",
    "src/routes/Library.tsx",
    "src/routes/Maintenance.tsx",
  ]) {
    it(`${f} no longer renders a standalone amber confirm-phrase label`, () => {
      expect(read(f)).not.toMatch(FAKE_LABEL);
    });
  }
});
