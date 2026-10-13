import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ssqh-fix F4 (bug-hunt spa-sites-queue-history, S21-C): SiteDetail's Settings link and the
// SiteActions / SitePayloadActions back links interpolated the raw site id into the route, so an
// id carrying a URL metacharacter ('#', '?') produced "/sites/a#b?c/settings" -- the router then
// lands on site "a" -- while the sibling Inspect/Actions links already encoded it.

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock, apiPost: apiPostMock, apiPut: vi.fn(), apiPatch: vi.fn(),
  apiDelete: vi.fn(), apiPostForm: vi.fn(), ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
import { installApiFixtures, renderAppAt } from "@/test/wiredGateHarness";

const ODD = "a#b?c";
const ENC = encodeURIComponent(ODD); // a%23b%3Fc
const site = (id: string) => ({ site_id: id, name: `Site ${id}`, state: "idle", auth_state: "unknown",
  avatar_color: "#eab308", active_workers: 0, downloaded_total: 0 });
const T = 20000;

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function () {};
});
beforeEach(() => {
  apiGetMock.mockReset(); apiPostMock.mockReset();
  installApiFixtures(apiGetMock, apiPostMock, {
    "/api/auth/whoami": { ok: true, user: null, multi_user: false },
    "/api/sites/v2": { ok: true, count: 2, ts: 1, sites: [site(ODD), site("plain1")] },
  });
});

async function backHrefs() {
  const links = await screen.findAllByRole("link", { name: /Back to site$/ }, { timeout: T });
  return links.map((l) => l.getAttribute("href"));
}

describe("site links encode the site id (ssqh-fix F4)", () => {
  it("SiteDetail Settings link encodes the id and opens THAT site's settings", async () => {
    renderAppAt(`/sites/${ENC}`);
    const link = await screen.findByRole("link", { name: "Edit this site's settings" }, { timeout: T });
    expect(link.getAttribute("href"), "SSQH_F4_SETTINGS_HREF_UNENCODED").toBe(`/sites/${ENC}/settings`);
    await userEvent.click(link);
    const h = await screen.findByRole("heading", { name: `Site settings — ${ODD}` }, { timeout: T });
    expect(h).toBeInTheDocument();
  }, T + 10000);

  it.each([["actions"], ["payload-actions"]])("/%s back links encode the id", async (page) => {
    renderAppAt(`/sites/${ENC}/${page}`);
    const hrefs = await backHrefs();
    expect(hrefs.length).toBeGreaterThan(0);
    expect(hrefs, "SSQH_F4_BACK_HREF_UNENCODED").toEqual(hrefs.map(() => `/sites/${ENC}`));
  }, T + 10000);

  it("plain ids are unchanged (control)", async () => {
    renderAppAt("/sites/plain1/actions");
    const hrefs = await backHrefs();
    expect(hrefs.length).toBeGreaterThan(0);
    expect(hrefs).toEqual(hrefs.map(() => "/sites/plain1"));
  }, T + 10000);
});
