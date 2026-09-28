import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// dl95-dailymotion-3: "Scrape listing" on a JS-rendered page (dailymotion.com/us) found 0 links and the dialog only
// flashed a toast. The server's explanation (what it measured + where the rendered crawl is) must stay on screen,
// with a way to the DOM analyzer, and must clear once the URL changes.
import { AddUrlDialog } from "./AddUrlDialog";

const HINT =
  "No video links in this page's HTML (0 links, 56 KB). If the listing is built by JavaScript, crawl it rendered: " +
  "DOM analyzer > Discover scenes with this URL as the listing page, or the browser extension's scrape action.";

let scrapeBody: unknown;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AddUrlDialog open={true} onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

async function scrape(url: string) {
  fireEvent.click(screen.getByRole("button", { name: /scrape listing/i }));
  fireEvent.change(screen.getByPlaceholderText("https://example.com/videos"), {
    target: { value: url },
  });
  fireEvent.click(screen.getByRole("button", { name: /find links/i }));
}

beforeEach(() => {
  scrapeBody = { ok: true, found: [], count: 0, html_size: 57979, anchors: 0, hint: HINT };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes("/api/csrf")) return json({ csrf_token: "t" });
      if (path.includes("/api/scrape_listing")) return json(scrapeBody);
      // one site: the "links found" path switches to list mode, which needs a target site
      return json({ sites: [{ site_id: "s1", name: "dailymotion" }], waiting: [], running: [] });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AddUrlDialog scrape with no links (dl95-dailymotion-3)", () => {
  it("keeps the server's explanation on screen and links to the DOM analyzer", async () => {
    mount();
    await scrape("https://www.dailymotion.com/us");
    const status = await screen.findByText(/Discover scenes/);
    expect(status.closest('[role="status"]')).toHaveTextContent("0 links, 56 KB");
    expect(screen.getByRole("link", { name: /open dom analyzer/i })).toHaveAttribute("href", "/dom-analyzer");
  });

  it("clears the explanation when the URL is edited", async () => {
    mount();
    await scrape("https://www.dailymotion.com/us");
    await screen.findByText(/Discover scenes/);
    fireEvent.change(screen.getByPlaceholderText("https://example.com/videos"), {
      target: { value: "https://www.dailymotion.com/tseries2" },
    });
    expect(screen.queryByText(/Discover scenes/)).not.toBeInTheDocument();
  });

  it("shows no explanation when links are found (control)", async () => {
    scrapeBody = { ok: true, found: ["https://www.dailymotion.com/video/x8abc1"], count: 1, html_size: 100 };
    mount();
    await scrape("https://www.dailymotion.com/us");
    await screen.findByDisplayValue("https://www.dailymotion.com/video/x8abc1");
    expect(screen.queryByRole("link", { name: /open dom analyzer/i })).not.toBeInTheDocument();
  });
});
