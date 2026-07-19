import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { QueueTemplatesPanel } from "@/components/ui/QueueTemplatesPanel";
import { APPLY_MODES } from "@/hooks/useQueueTemplates";

// v3.66.733 — the queue_templates CONTROL cluster gets a GUI.
//
// /api/queue_templates (GET, POST), /api/queue_templates/<int:tid>
// (GET, PUT, DELETE) and /api/queue_templates/<int:tid>/apply/<sid> (POST)
// were CONTROL-classified and GUI-dark: the blueprint exists, and nothing in
// the SPA could reach it.
//
// THE LOAD-BEARING TEST IN THIS FILE is the query-string one. The backend reads
//     mode = (request.args.get("mode") or "append").lower()
// so a body of {"mode": "replace"} is ACCEPTED, returns 200 {"ok": true}, and
// APPENDS. The operator would believe they replaced a queue they had merely
// grown. Type-correct, meaning-wrong -- a slower way of lying. If someone later
// "tidies" mode into the body, that test goes red.

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/queue"]}>
        <QueueTemplatesPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const TEMPLATES = [
  {
    id: 7,
    name: "nightly set",
    origin_site_id: "alpha",
    note: "",
    ts_created: 1_700_000_000,
    ts_used: null,
    use_count: 0,
    url_count: 3,
  },
];

const ONE = {
  ...TEMPLATES[0],
  urls: ["https://a.example/1", "https://a.example/2", "https://a.example/3"],
  priority_map: {},
  force_set: [],
};

const SITES = { sites: [{ site_id: "alpha" }, { site_id: "beta" }] };

function json(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 400,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    headers: new Headers({ "content-type": "application/json" }),
  } as unknown as Response);
}

/** Route the panel's reads; record every write for inspection. */
function installFetch(calls: { url: string; init?: RequestInit }[], overrides: Record<string, unknown> = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    // apiGet leaves `method` undefined -- an absent method IS a GET. Reading it
    // as "not a GET" made every read fall through to the write branch and all
    // eight tests went red against a correct panel: the harness lying, not the
    // product breaking.
    const method = (init?.method ?? "GET").toUpperCase();
    if (method !== "GET") calls.push({ url, init });
    if (url.startsWith("/api/sites/v2")) return json(SITES);
    if (url.startsWith("/api/queue_templates/7") && method === "GET")
      return json({ ok: true, template: ONE });
    if (url.startsWith("/api/queue_templates") && method === "GET")
      return json({ ok: true, templates: TEMPLATES });
    return json(overrides[url] ?? { ok: true, added: 3, mode: "append" });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("QueueTemplatesPanel wires the dark queue_templates cluster", () => {
  it("lists templates from GET /api/queue_templates", async () => {
    installFetch([]);
    mount();
    expect(await screen.findByText("nightly set")).toBeInTheDocument();
  });

  // ---- THE NEGATIVE CONTROL THIS CUT EXISTS FOR ----------------------------
  it("sends mode on the QUERY STRING, never in the body", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByLabelText("Apply nightly set"));
    await user.selectOptions(await screen.findByLabelText("Target site"), "beta");
    await user.selectOptions(screen.getByLabelText("Import mode"), "replace");
    await user.click(screen.getByRole("button", { name: "Import" }));
    // replace is destructive -> Tier-A confirm before anything is sent
    await user.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const post = calls.find((c) => c.url.includes("/apply/"));
    expect(post).toBeDefined();

    // mode is IN THE URL...
    expect(post!.url).toBe("/api/queue_templates/7/apply/beta?mode=replace");

    // ...and NOT in the body. request.args.get("mode") cannot see a body key:
    // a body-borne mode silently degrades to the "append" default.
    const body = JSON.parse((post!.init!.body as string) || "{}");
    expect(body).not.toHaveProperty("mode");
  });

  it("append does NOT fire a confirm (safety theatre trains click-through)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByLabelText("Apply nightly set"));
    await user.selectOptions(await screen.findByLabelText("Target site"), "alpha");
    await user.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() => expect(calls.some((c) => c.url.includes("/apply/"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/apply/"))!.url).toBe(
      "/api/queue_templates/7/apply/alpha?mode=append",
    );
  });

  it("offers exactly the two modes the backend accepts", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByLabelText("Apply nightly set"));
    const sel = (await screen.findByLabelText("Import mode")) as HTMLSelectElement;
    const opts = Array.from(sel.options).map((o) => o.value);
    // anything else 400s ("unknown mode: X") -- a doomed request is a dead control
    expect(opts).toEqual([...APPLY_MODES]);
  });

  it("cannot create a template with no name (backend 400s on empty)", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await screen.findByText("nightly set");
    const create = screen.getByRole("button", { name: "Create template" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText("Template name"), "set b");
    expect(create).toBeEnabled();
  });

  it("Save stays disabled until something actually changed (a no-op PUT returns ok:false)", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByLabelText("Edit nightly set"));

    const save = await screen.findByRole("button", { name: "Save changes" });
    await waitFor(() => expect(screen.getByLabelText("Template name")).toHaveValue("nightly set"));
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Template note"), "x");
    await waitFor(() => expect(save).toBeEnabled());
  });

  it("the editor populates urls from GET /<tid> (the list response has none)", async () => {
    installFetch([]);
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByLabelText("Edit nightly set"));
    await waitFor(() =>
      expect(screen.getByLabelText("Template URLs")).toHaveValue(ONE.urls.join("\n")),
    );
  });

  it("create POSTs a urls LIST, not a blob (backend 400s on a non-list)", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    installFetch(calls);
    const user = userEvent.setup();
    mount();
    await screen.findByText("nightly set");

    await user.type(screen.getByLabelText("Template name"), "set b");
    await user.type(screen.getByLabelText("Template URLs"), "https://x/1\n\nhttps://x/2\n");
    await user.click(screen.getByRole("button", { name: "Create template" }));

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    const post = calls.find((c) => c.url === "/api/queue_templates")!;
    const body = JSON.parse(post.init!.body as string);
    expect(Array.isArray(body.urls)).toBe(true);
    expect(body.urls).toEqual(["https://x/1", "https://x/2"]); // blanks stripped
    expect(body.name).toBe("set b");
  });
});
