import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PlainLanguageHint } from "@/components/ui/PlainLanguageHint";

// v3.66.753 — the operator half of the a11y adjudication gets wired.
//
// THE LOAD-BEARING TEST: plain_language RETURNS THE ORIGINAL when no
// pattern matches. plain === original must render as "no simpler
// phrasing", never as the same text presented twice as an explanation —
// that would be a no-answer laundered into an answer.

function mount(message: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PlainLanguageHint message={message} />
    </QueryClientProvider>,
  );
}

function json(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("PlainLanguageHint wires a11y/plain_language honestly", () => {
  it("POSTs the message and renders the rewrite", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: unknown, init?: RequestInit) => {
        const url = String(input);
        if ((init?.method ?? "").toUpperCase() === "POST")
          calls.push({ url, body: JSON.parse(init!.body as string) });
        if (url.includes("/api/a11y/plain_language"))
          return json({
            plain: "The site took too long to answer. Try again in a minute.",
            original: "ConnectionError: HTTPSConnectionPool timeout",
          });
        return json({ ok: true });
      },
    );
    const user = userEvent.setup();
    mount("ConnectionError: HTTPSConnectionPool timeout");

    await user.click(screen.getByRole("button", { name: "Explain plainly" }));
    expect(
      await screen.findByText(/took too long to answer/),
    ).toBeInTheDocument();
    expect(calls[0].url).toContain("/api/a11y/plain_language");
    expect(calls[0].body).toMatchObject({
      message: "ConnectionError: HTTPSConnectionPool timeout",
    });
  });

  // ---- THE HONESTY RULE ---------------------------------------------------
  it("says 'no simpler phrasing' when the endpoint echoes the original", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input: unknown) => {
      const url = String(input);
      if (url.includes("/api/a11y/plain_language"))
        return json({ plain: "weird bespoke failure", original: "weird bespoke failure" });
      return json({ ok: true });
    });
    const user = userEvent.setup();
    mount("weird bespoke failure");

    await user.click(screen.getByRole("button", { name: "Explain plainly" }));
    expect(
      await screen.findByText(/No simpler phrasing is available/),
    ).toBeInTheDocument();
    // the echoed text must NOT be presented as an explanation block
    expect(
      screen.queryByText("weird bespoke failure", { selector: "p.bg-surface-2" }),
    ).not.toBeInTheDocument();
  });

  it("renders nothing for an empty message", () => {
    mount("");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
