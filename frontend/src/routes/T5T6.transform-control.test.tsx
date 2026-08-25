// Mutation transform control: this spec imports the subject and proves only
// that the module transformed. It intentionally asserts no endpoint or click
// behaviour. A valid semantic mutant must ESCAPE here; a failure would expose
// a compile/import break masquerading as a behavioural catch.
import { expect, it } from "vitest";

import * as integrations from "@/hooks/useIntegrations";

it("imports useIntegrations without asserting its behaviour", () => {
  expect(typeof integrations).toBe("object");
});
