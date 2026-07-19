import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { KnowledgeNotesPanel } from "@/components/ui/KnowledgeNotesPanel";
import { KNOWLEDGE_NOTE_KINDS } from "@/hooks/useKnowledgeNotes";

// v3.66.751 — the knowledge/notes CONTROL cluster gets a GUI.
//
// THE LOAD-BEARING TESTS:
//  1. The list's error shape is rendered, never laundered. GET degrades to
//     {notes: [], error} at HTTP 500 — zero notes with an error present must
//     read as BROKEN, not as "no notes yet". Same honesty rule as the
//     semantic panel's indexed-count readout.
//  2. Submit is disabled until pattern AND resolution are present — the
//     endpoint's real 400. The panel never fires a doomed POST.
//  3. kind is a CONSTRAINED select over the derived vocabulary — free text
//     is meaning-wrong the day a consumer branches on kind.
//  4. Delete confirms first (a note's pattern->resolution mapping is gone
//     for every future failure) and hits the int-typed path.

function mount(siteId = "alpha") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/sites/alpha/settings"]}>
        <KnowledgeNotesPanel siteId={siteId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const NOTES = {
  notes: [
    {
      id: 7,
      site_id: "alpha",
      kind: "failure",
      pattern: "cloudflare",
      resolution: "rotate the fingerprint and retry",
      created_at: 1,
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
  opts: { listBody?: unknown; listStatus?: number } = {},
) {
  const listBody = opts.listBody ?? NOTES;
  const listStatus = opts.listStatus ?? 200;
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (method !== "GET") calls.push({ url, init });
      if (url.includes("/api/knowledge/notes") && method === "GET")
        return json(listBody, listStatus < 400, listStatus);
      if (url.includes("/api/knowledge/notes/") && method === "DELETE")
        return json({ ok: true });
      if (url.includes("/api/knowledge/notes") && method === "POST")
        return json({ ok: true, id: 8 });
      return json({ ok: true });
    });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("KnowledgeNotesPanel wires the dark knowledge/notes cluster", () => {
  it("renders the list from GET /api/knowledge/notes scoped to the site", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const spy = installFetch(calls);
    mount();

    expect(await screen.findByText(/cloudflare/)).toBeInTheDocument();
    const listCall = spy.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes("/api/knowledge/notes"));
    expect(listCall).toContain("site_id=alpha");
  });

  // ---- THE HONESTY RULE THIS PANEL EXISTS TO GET RIGHT -------------------
  it("says the store is BROKEN when the list degrades to {notes:[], error}", async () => {
    installFetch([], {
      listBody: { notes: [], error: "db exploded" },
      listStatus: 200, // shape check: even at 200, error present means broken
    });
    mount();

    expect(await screen.findByRole("alert")).toHaveTextContent("db exploded");
    // a broken store must never read as an empty one
    expect(screen.queryByText(/No notes yet/)).not.toBeInTheDocument();
  });

  it("disables Save until pattern AND resolution are present (the real 400)", async () => {
    installFetch([], { listBody: { notes: [] } });
    const user = userEvent.setup();
    mount();

    const save = await screen.findByRole("button", { name: "Save note" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Failure pattern"), "captcha loop");
    expect(save).toBeDisabled(); // resolution still missing

    await user.type(screen.getByLabelText("Resolution"), "cool down 10 min");
    expect(save).toBeEnabled();
  });

  it("POSTs {site_id, kind, pattern, resolution} with the constrained kind", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls, { listBody: { notes: [] } });
    const user = userEvent.setup();
    mount();

    // the kind control offers exactly the derived vocabulary, nothing free-text
    const kindSelect = await screen.findByLabelText("Kind");
    const options = Array.from(
      (kindSelect as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(options).toEqual([...KNOWLEDGE_NOTE_KINDS]);

    await user.selectOptions(kindSelect, "rate_limit");
    await user.type(screen.getByLabelText("Failure pattern"), "429 too many");
    await user.type(screen.getByLabelText("Resolution"), "raise the delay");
    await user.click(screen.getByRole("button", { name: "Save note" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const post = calls.find(
      (c) => (c.init?.method ?? "").toUpperCase() === "POST",
    )!;
    expect(post.url).toContain("/api/knowledge/notes");
    const body = JSON.parse(post.init!.body as string);
    expect(body).toMatchObject({
      site_id: "alpha",
      kind: "rate_limit",
      pattern: "429 too many",
      resolution: "raise the delay",
    });
  });

  it("DELETE confirms first and hits /api/knowledge/notes/<id>", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await screen.findByText(/cloudflare/);
    await user.click(screen.getByRole("button", { name: "Delete note 7" }));
    // nothing fired yet — the confirm gate is the point
    expect(
      calls.filter((c) => (c.init?.method ?? "").toUpperCase() === "DELETE"),
    ).toHaveLength(0);

    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.includes("/api/knowledge/notes/7") &&
            (c.init?.method ?? "").toUpperCase() === "DELETE",
        ),
      ).toBe(true),
    );
  });
});
