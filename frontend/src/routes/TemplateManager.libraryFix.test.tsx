// library-fix r1 (bughunt deep-spa-library-templates-mobile F1 F3 F7 F9).
//
// F1: an entry without resolutions/selectors/network_patterns, or a body
// without reviewed/drafts, threw "reading 'length'" and blanked the SPA.
// F3: the promote dialog had no height cap, so at 844x390 it ran off both
// edges with Cancel/Promote below the screen (jsdom has no layout: the class
// contract is pinned here; the rig probe T12 is the measured receipt).
// F7: a draft with blocking lint had a disabled Promote with no title and no
// accessible description; the reason now rides on the button itself.
// F9: a failed Disable POST never refetched the list, and an ok reply without
// `disabled` toasted "Disabled undefined".
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() });
  return { apiGetMock: vi.fn(), apiPostMock: vi.fn(), toastMock: toast };
});

vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  ApiError: class extends Error {},
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { TemplateManager } from "@/routes/TemplateManager";
import { calledPaths, installApiFixtures, renderWired } from "@/test/wiredGateHarness";

const entry = (i: number, over: Record<string, unknown> = {}) => ({
  file: `t${i}.template.json`,
  host: `host${i}.example`,
  status: "reviewed",
  enabled: true,
  selectors: ["a"],
  resolutions: [720],
  network_patterns: ["https://x/{id}"],
  lint_warnings: [],
  has_blocking_lint: false,
  ...over,
});

function serve(body: unknown, post: Record<string, unknown> = {}) {
  installApiFixtures(apiGetMock, apiPostMock, { "/api/template_manager": body });
  const postImpl = apiPostMock.getMockImplementation()!;
  apiPostMock.mockImplementation((p: string, ...rest: unknown[]) =>
    p in post
      ? post[p] instanceof Error
        ? Promise.reject(post[p])
        : Promise.resolve(post[p])
      : postImpl(p, ...rest),
  );
}

const listFetches = () =>
  calledPaths(apiGetMock).filter((p) => p === "/api/template_manager").length;

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  toastMock.success.mockReset();
  toastMock.error.mockReset();
});

describe("library-fix F1 template list shapes never blank the page", () => {
  it.each([
    ["entry-arrays-absent", { ok: true, reviewed: [{ file: "a.json", host: "a", enabled: true }], drafts: [] }],
    ["entry-arrays-null", { ok: true, reviewed: [entry(1, { resolutions: null, selectors: null, network_patterns: null, lint_warnings: null })], drafts: [] }],
  ])("%s: the row renders with zero counts", async (_n, body) => {
    serve(body);
    renderWired(<TemplateManager />, "/templates");
    await waitFor(() => expect(screen.queryByText(/Reviewed \(1\)/), "LF-F1-ROW-BLANK").not.toBeNull());
    expect(screen.getByText(/0 selector groups/), "LF-F1-ROW-COUNTS").toBeInTheDocument();
  });

  it.each([
    ["keys-absent", { ok: true }, "the server sent no template list"],
    ["null-lists", { ok: true, reviewed: null, drafts: null }, "the server sent no template list"],
    ["ok-false", { ok: false, error: "REASON-LIST" }, "REASON-LIST"],
    ["ok-false-with-lists", { ok: false, error: "REASON-LISTS", reviewed: [], drafts: [] }, "REASON-LISTS"],
    ["bare-array", [1, 2], "the server sent no template list"],
  ])("%s: a body with no template list shows the load error", async (_n, body, why) => {
    serve(body);
    renderWired(<TemplateManager />, "/templates");
    await waitFor(() => expect(screen.queryByText(/Couldn.t load templates/), "LF-F1-BODY-BLANK").not.toBeNull());
    const alert = screen.getByText(/Couldn.t load templates/);
    expect(alert.closest("[role=alert]"), "LF-F1-BODY-NO-ALERT").not.toBeNull();
    expect(alert.textContent, "LF-F1-BODY-REASON").toContain(why);
    expect(screen.queryByText(/Reviewed \(0\)/), "LF-F1-BODY-READ-AS-EMPTY").toBeNull();
  });

  it("control: a full entry and a host+file-null entry render the list, no alert", async () => {
    serve({ ok: true, reviewed: [entry(1), entry(2, { host: null, file: null })], drafts: [] });
    renderWired(<TemplateManager />, "/templates");
    expect(await screen.findByText(/Reviewed \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("host1.example")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn.t load templates/)).toBeNull();
  });
});

describe("library-fix F3 promote dialog fits a short viewport", () => {
  it("the dialog is height-capped and scrolls inside", async () => {
    const cand = { base: "https://api.h.example", endpoints: Array.from({ length: 30 }, (_, i) => `/v1/endpoint/${i}`) };
    serve({ ok: true, reviewed: [], drafts: [entry(3, { enabled: false, status: "draft", api_candidate: cand })] });
    const user = userEvent.setup();
    renderWired(<TemplateManager />, "/templates");
    await user.click(await screen.findByRole("button", { name: "Promote host3.example" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className, "LF-F3-DIALOG-NO-SCROLL").toContain("overflow-y-auto");
    expect(dialog.className, "LF-F3-DIALOG-NO-CAP").toMatch(/max-h-\[calc\(100dvh-2rem\)\]/);
  });
});

describe("library-fix F7 a disabled Promote says why", () => {
  const blocked = entry(5, {
    enabled: false,
    status: "draft",
    has_blocking_lint: true,
    lint_warnings: [{ level: "warning", message: "minor" }, { level: "error", message: "LF-LINT-REASON" }],
  });

  it("the blocked draft's Promote carries the lint reason as title and description", async () => {
    serve({ ok: true, reviewed: [], drafts: [blocked] });
    renderWired(<TemplateManager />, "/templates");
    const btn = await screen.findByRole("button", { name: "Promote host5.example" });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title") ?? "", "LF-F7-NO-TITLE").toContain("LF-LINT-REASON");
    const ref = btn.getAttribute("aria-describedby");
    expect(ref, "LF-F7-NO-DESCRIBEDBY").not.toBeNull();
    expect(document.getElementById(ref!)?.textContent ?? "", "LF-F7-DESCRIBEDBY-DANGLING").toContain("LF-LINT-REASON");
    expect(btn, "LF-F7-NO-DESCRIPTION").toHaveAccessibleDescription(/LF-LINT-REASON/);
  });

  it("control: a clean draft's Promote is enabled with no reason attached", async () => {
    serve({ ok: true, reviewed: [], drafts: [blocked, entry(6, { enabled: false, status: "draft" })] });
    renderWired(<TemplateManager />, "/templates");
    const btn = await screen.findByRole("button", { name: "Promote host6.example" });
    expect(btn).toBeEnabled();
    expect(btn.getAttribute("title")).toBeNull();
    expect(btn.getAttribute("aria-describedby")).toBeNull();
    expect(screen.getByRole("button", { name: "Promote host5.example" })).toBeDisabled();
  });
});

describe("library-fix F9 Disable outcome", () => {
  async function disableFirst() {
    const user = userEvent.setup();
    renderWired(<TemplateManager />, "/templates");
    await user.click(await screen.findByRole("button", { name: "Disable" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disable" }));
  }

  it("an HTTP failure toasts and refetches the list", async () => {
    serve({ ok: true, reviewed: [entry(1)], drafts: [] }, {
      "/api/template_manager/disable": new Error("HTTP 404 template not found"),
    });
    await disableFirst();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Disable failed: HTTP 404 template not found"));
    await waitFor(() => expect(listFetches(), "LF-F9-NO-REFETCH-ON-ERROR").toBe(2));
  });

  it("an ok reply without `disabled` names the file, never undefined", async () => {
    serve({ ok: true, reviewed: [entry(1)], drafts: [] }, { "/api/template_manager/disable": { ok: true } });
    await disableFirst();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    expect(toastMock.success.mock.calls[0][0], "LF-F9-DISABLED-UNDEFINED").toBe("Disabled t1.template.json");
    await waitFor(() => expect(listFetches()).toBe(2));
  });

  it("control: ok:false toasts the reason and refetches", async () => {
    serve({ ok: true, reviewed: [entry(1)], drafts: [] }, {
      "/api/template_manager/disable": { ok: false, error: "already disabled" },
    });
    await disableFirst();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("already disabled"));
    await waitFor(() => expect(listFetches()).toBe(2));
  });
});
