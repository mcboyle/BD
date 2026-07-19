import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CookieClipboardPanel } from "@/components/ui/CookieClipboardPanel";

// v3.66.735 — the cookie_clipboard CONTROL cluster gets a GUI.
//
// THE LOAD-BEARING TEST is "save sends the RAW TEXT". /save/<sid> re-parses the
// text itself and reads nothing else from the body:
//
//     text   = body.get("text", "")
//     parsed = _cc.auto_detect_and_parse(text)
//     if not parsed.get("cookies"): return 400 "could not parse any cookies"
//
// So the "efficient" wiring — parse once, POST the parsed cookies — would send a
// body the endpoint never reads, and EVERY save would 400 while the preview on
// screen showed a perfect parse. Type-correct, meaning-wrong.
//
// The second is secret hygiene: cookie values are session tokens. The parse
// response carries them and this panel must never render one.

function mount(siteId = "alpha") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/sites/alpha/settings"]}>
        <CookieClipboardPanel siteId={siteId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const RAW = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tsid\tSUPERSECRETVALUE";

const PARSED = {
  format: "netscape",
  count: 1,
  confidence: 90,
  cookies: [
    {
      name: "sid",
      value: "SUPERSECRETVALUE",
      domain: ".example.com",
      path: "/",
      secure: true,
    },
  ],
};

function json(body: unknown, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

function installFetch(
  calls: { url: string; init?: RequestInit }[],
  saveBody: unknown = { ok: true, count: 1, format: "netscape", path: "/x/cookies.json" },
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET") calls.push({ url, init });
    if (url.includes("/api/cookie_clipboard/parse")) return json(PARSED);
    if (url.includes("/api/cookie_clipboard/save/")) return json(saveBody);
    return json({ ok: true });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function pasteAndParse(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByLabelText("Pasted cookie text"));
  await user.paste(RAW);
  await user.click(screen.getByRole("button", { name: "Parse" }));
  await screen.findByTestId("cookie-preview");
}

describe("CookieClipboardPanel wires the dark cookie_clipboard cluster", () => {
  // ---- THE NEGATIVE CONTROL THIS CUT EXISTS FOR --------------------------
  it("save sends the RAW TEXT, not the parsed cookies", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await pasteAndParse(user);
    await user.click(screen.getByRole("button", { name: /Save to alpha/ }));
    await user.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(calls.some((c) => c.url.includes("/save/"))).toBe(true));
    const post = calls.find((c) => c.url.includes("/save/"))!;
    const body = JSON.parse(post.init!.body as string);

    // the endpoint re-parses `text` itself...
    expect(body.text).toBe(RAW);
    // ...and reads NOTHING else. Sending parsed cookies instead would 400
    // "could not parse any cookies" on every save.
    expect(body).not.toHaveProperty("cookies");
    expect(post.url).toBe("/api/cookie_clipboard/save/alpha");
  });

  it("never renders a cookie VALUE (they are session tokens)", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await pasteAndParse(user);

    const list = screen.getByTestId("cookie-preview-list");
    expect(list).toHaveTextContent("sid");
    expect(list).toHaveTextContent(".example.com");
    // the secret itself must not reach the DOM of the preview
    expect(list.textContent).not.toContain("SUPERSECRETVALUE");
    expect(list).toHaveTextContent("<16 chars>");
  });

  it("parse is read-only and fires NO confirm (theatre trains click-through)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await pasteAndParse(user);
    // parse went straight out, with no dialog in between
    expect(calls.some((c) => c.url.includes("/parse"))).toBe(true);
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("save IS confirmed (it overwrites the site's jar)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await pasteAndParse(user);
    await user.click(screen.getByRole("button", { name: /Save to alpha/ }));
    // nothing sent until the operator confirms
    expect(calls.some((c) => c.url.includes("/save/"))).toBe(false);
    await screen.findByRole("button", { name: "Confirm" });
  });

  it("does not fire a doomed request on empty text (backend 400s)", async () => {
    installFetch([]);
    mount();
    expect(screen.getByRole("button", { name: "Parse" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Save to alpha/ })).toBeDisabled();
  });

  it("surfaces the backend's reason when the site has no cookie_file", async () => {
    // The SPA CANNOT pre-check this: cookie_file is secret-classed and excluded
    // from the editable surface on purpose. So we fire and report the reason
    // rather than inventing a read to get around the exclusion.
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, { ok: false, error: "site has no cookie_file configured" });
    const user = userEvent.setup();
    mount();

    await pasteAndParse(user);
    await user.click(screen.getByRole("button", { name: /Save to alpha/ }));
    await user.click(await screen.findByRole("button", { name: "Confirm" }));

    expect(await screen.findByText(/no cookie_file configured/i)).toBeInTheDocument();
  });

  it("editing the text drops a stale preview (it must not justify a new save)", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await pasteAndParse(user);
    expect(screen.getByTestId("cookie-preview")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Pasted cookie text"), "x");
    await waitFor(() =>
      expect(screen.queryByTestId("cookie-preview")).not.toBeInTheDocument(),
    );
    // and Save is disabled again until the new text is parsed
    expect(screen.getByRole("button", { name: /Save to alpha/ })).toBeDisabled();
  });
});
