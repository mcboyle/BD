import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import NeedsReview from "./NeedsReview";
import { SiteActions } from "./SiteActions";

// F035 API wiring: the REAL api-client runs here, only fetch is stubbed, so
// these pin what leaves the browser -- method, path, JSON body, CSRF header --
// for the teach-session controls on /sites/<id>/actions, and the hand-off link
// from /needs-review that carries the parked URL there.

vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("@/components/ui/AiReanalyzeSection", () => ({ AiReanalyzeSection: () => null }));
vi.mock("@/components/ui/PlainLanguageHint", () => ({ PlainLanguageHint: () => null }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const PARKED_URL = "https://example.test/watch?v=1&t=2";
const SITE = "site-a";

type Sent = { path: string; method: string; body: unknown; csrf: string | null };
let sent: Sent[] = [];
let replies: Record<string, unknown> = {};

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: () => Promise.resolve(body) } as unknown as Response;
}

beforeEach(() => {
  sent = [];
  replies = {};
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path === "/api/csrf") return Promise.resolve(json({ csrf_token: "tok-f035" }));
    if (method === "GET" && path === "/api/sites/v2") {
      return Promise.resolve(json({ ok: true, sites: [{ site_id: SITE, name: "Site A" }] }));
    }
    if (method === "GET" && path.startsWith("/api/history?")) {
      return Promise.resolve(json([
        { id: 7, site_id: SITE, site_name: "Site A", url: PARKED_URL, status: "needs_review",
          message: "Auto-teach: take over to teach download selectors." },
      ]));
    }
    const headers = (init?.headers ?? {}) as Record<string, string>;
    sent.push({
      path,
      method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body,
      csrf: headers["X-CSRF-Token"] ?? null,
    });
    return Promise.resolve(json(replies[path] ?? { ok: true, message: "ok" }));
  }));
});

afterEach(() => vi.unstubAllGlobals());

function mount(entry: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/needs-review" element={<NeedsReview />} />
          <Route path="/sites/:siteId/actions" element={<SiteActions />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const button = (name: string) => screen.getByRole("button", { name });

describe("teach session wiring (F035)", () => {
  it("SiteActions prefills ?teach_url and POSTs take_over_url with the URL and CSRF token", async () => {
    replies[`/api/sites/${SITE}/take_over_url`] = { ok: true, message: "Manual download takeover started" };
    mount(`/sites/${SITE}/actions?teach_url=${encodeURIComponent(PARKED_URL)}`);
    expect(screen.getByRole("heading", { name: "Teach session" })).toBeInTheDocument();
    expect(screen.getByLabelText("Teach URL")).toHaveValue(PARKED_URL);

    fireEvent.click(button("Take over to teach"));
    fireEvent.click(button("Confirm"));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toEqual({
      path: `/api/sites/${SITE}/take_over_url`,
      method: "POST",
      body: { url: PARKED_URL },
      csrf: "tok-f035",
    });
    expect(await screen.findByRole("status", { name: "Teach session result" }))
      .toHaveTextContent("Manual download takeover started");
  });

  it("I'm Done then Cancel teach POST take_over_done and take_over_cancel, in that order", async () => {
    replies[`/api/sites/${SITE}/take_over_done`] = {
      ok: true, message: "Manual download finished, learned 1 role(s)",
      learned_summary: { trigger_selector: "a.download" },
    };
    replies[`/api/sites/${SITE}/take_over_cancel`] = { ok: false, message: "No pending manual download" };
    mount(`/sites/${SITE}/actions`);

    fireEvent.click(button("I'm Done"));
    fireEvent.click(button("Confirm"));
    expect(await screen.findByLabelText("Learned selectors")).toHaveTextContent("a.download");

    fireEvent.click(button("Cancel teach"));
    fireEvent.click(button("Yes, cancel teach"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No pending manual download");

    expect(sent.map((s) => [s.method, s.path, s.body])).toEqual([
      ["POST", `/api/sites/${SITE}/take_over_done`, {}],
      ["POST", `/api/sites/${SITE}/take_over_cancel`, {}],
    ]);
  });

  it("the page's other actions still go to their own endpoints", async () => {
    mount(`/sites/${SITE}/actions`);
    fireEvent.click(button("Manual login cancel"));
    fireEvent.click(button("Confirm"));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].path).toBe(`/api/sites/${SITE}/login_manual_cancel`);
  });

  it("/needs-review links the parked URL to the site's teach controls", async () => {
    mount("/needs-review");
    const link = await screen.findByRole("link", { name: "Take over to teach" });
    expect(link).toHaveAttribute(
      "href",
      `/sites/${SITE}/actions?teach_url=${encodeURIComponent(PARKED_URL)}`,
    );
    fireEvent.click(link);
    expect(await screen.findByLabelText("Teach URL")).toHaveValue(PARKED_URL);
    expect(sent).toEqual([]);
  });
});
