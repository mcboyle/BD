import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WebhooksPanel, WEBHOOK_EVENTS } from "@/components/ui/WebhooksPanel";

// v3.66.731 — the webhooks CONTROL cluster gets a GUI.
//
// /api/webhooks (GET, POST), /api/webhooks/<wid> (DELETE) and
// /api/webhooks/drain (POST) were CONTROL-classified and GUI-dark: the backend
// blueprint has existed since v3.66.405 and nothing in the SPA could reach it.
// An operator could register a webhook only by curling the API.
//
// The event vocabulary is DERIVED from what the backend actually fires
// (webhooks.fire() call sites), not invented: download.done / download.failed /
// download.needs_review / alert.fired / maintenance.start / maintenance.end.
// download.progress and download.retry are PLUGIN emits (_pl.emit), NOT webhook
// events -- offering them would be a type-correct, meaning-wrong control.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/integrations"]}>
        <WebhooksPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const SUBS = [
  { id: 2, url: "https://hooks.example.com/b", events: ["alert.fired"], secret: "<8 chars>" },
  { id: 1, url: "https://hooks.example.com/a", events: ["download.done"], secret: "" },
];

function jsonOnce(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 400,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("WebhooksPanel wires the dark webhooks CONTROL cluster", () => {
  it("offers ONLY the events the backend actually fires", () => {
    // A control that offers an event nothing emits is a lie with a checkbox on it.
    expect([...WEBHOOK_EVENTS].sort()).toEqual(
      [
        "alert.fired",
        "download.done",
        "download.failed",
        "download.needs_review",
        "maintenance.end",
        "maintenance.start",
      ].sort(),
    );
    expect(WEBHOOK_EVENTS).not.toContain("download.progress");
    expect(WEBHOOK_EVENTS).not.toContain("download.retry");
  });

  it("lists existing subscriptions from GET /api/webhooks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonOnce({ subscriptions: SUBS })),
    );
    mount();
    await waitFor(() =>
      expect(screen.getByText("https://hooks.example.com/a")).toBeInTheDocument(),
    );
    expect(screen.getByText("https://hooks.example.com/b")).toBeInTheDocument();
  });

  it("never renders a raw secret -- only the redacted form the API returns", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonOnce({ subscriptions: SUBS })),
    );
    const { container } = mount();
    await waitFor(() =>
      expect(screen.getByText("https://hooks.example.com/b")).toBeInTheDocument(),
    );
    // the API redacts to "<N chars>"; the panel must not invent a reveal control
    expect(container.textContent).not.toMatch(/s3cr3t|supersecret/i);
    expect(container.querySelector('[data-testid="reveal-secret"]')).toBeNull();
  });

  it("POSTs a new subscription with url + selected events", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (init?.method === "POST") return jsonOnce({ ok: true, id: 3 });
        return jsonOnce({ subscriptions: SUBS });
      }),
    );
    mount();
    await waitFor(() => expect(screen.getByLabelText("Webhook URL")).toBeInTheDocument());

    await userEvent.type(
      screen.getByLabelText("Webhook URL"),
      "https://hooks.example.com/new",
    );
    await userEvent.click(screen.getByLabelText("download.failed"));
    await userEvent.click(screen.getByRole("button", { name: "Add webhook" }));

    await waitFor(() => {
      const post = calls.find((c) => c.init?.method === "POST");
      expect(post).toBeTruthy();
      expect(post!.url).toBe("/api/webhooks");
      const body = JSON.parse(String(post!.init!.body));
      expect(body.url).toBe("https://hooks.example.com/new");
      expect(body.events).toEqual(["download.failed"]);
    });
  });

  it("refuses to POST with no events selected (the backend 400s on it)", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonOnce({ subscriptions: SUBS });
      }),
    );
    mount();
    await waitFor(() => expect(screen.getByLabelText("Webhook URL")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("Webhook URL"), "https://x.example.com");

    // url present, zero events -> add must be disabled, not fire a doomed request
    expect(screen.getByRole("button", { name: "Add webhook" })).toBeDisabled();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("DELETEs a subscription by id", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (init?.method === "DELETE") return jsonOnce({ ok: true });
        return jsonOnce({ subscriptions: SUBS });
      }),
    );
    mount();
    await waitFor(() =>
      expect(screen.getByText("https://hooks.example.com/a")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Remove webhook 1" }));

    await waitFor(() => {
      const del = calls.find((c) => c.init?.method === "DELETE");
      expect(del).toBeTruthy();
      expect(del!.url).toBe("/api/webhooks/1");
    });
  });

  it("POSTs /api/webhooks/drain when the operator forces a drain", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (String(url).endsWith("/drain")) return jsonOnce({ sent: 2, failed: 0 });
        return jsonOnce({ subscriptions: SUBS });
      }),
    );
    mount();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Drain queue now" })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Drain queue now" }));

    await waitFor(() => {
      const drain = calls.find((c) => String(c.url).endsWith("/drain"));
      expect(drain).toBeTruthy();
      expect(drain!.init?.method).toBe("POST");
    });
  });
});
