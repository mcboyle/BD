import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiTokensPanel } from "@/components/ApiTokensPanel";

import { Secrets } from "./Secrets";

// dss-spa-secrets F10-F13: the secret/token UI must not report what it has not
// measured (an unread list as "none", an unwritten clipboard as "Copied", a
// failed status poll as "unlocked") and must not delete a key that is no longer
// the selected one. Each defect test carries its DSS id and has a control.

const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toastMock }));

const STATUS = {
  ok: true,
  backend: "master_password",
  is_unlocked: true,
  is_initialized: true,
  plaintext_count: 0,
  plaintext_sites: [],
  stored_keys: ["site:a", "site:b"],
  keyring_available: true,
  crypto_available: true,
};

let statusFails = false;
let storedKeys: string[] = [];
let tokensMode: "ok" | "fail" | "pending" = "ok";
let tokens: unknown[] = [];
let posts: { url: string; body: unknown }[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === "string" ? input : input.toString();
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") {
    posts.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.includes("/api/secrets/import_file"))
      return Promise.resolve(json({ ok: true, format: "csv", records: [{ name: "n", username: "u", password: "p" }] }));
    if (url.includes("/api/secrets/extension/pair_issue"))
      return Promise.resolve(json({ ok: true, pairing_token: "pair-tok-1", expires_in_seconds: 60 }));
    if (url.includes("/api/api_tokens"))
      return Promise.resolve(json({ ok: true, token: "api-tok-1", scope: "read" }));
    return Promise.resolve(json({ ok: true, removed: true }));
  }
  if (url.includes("/api/secrets/status"))
    return Promise.resolve(
      statusFails ? json({ error: "boom" }, 500) : json({ ...STATUS, stored_keys: storedKeys }),
    );
  if (url.includes("/api/api_tokens")) {
    if (tokensMode === "pending") return new Promise<Response>(() => {});
    return Promise.resolve(tokensMode === "fail" ? json({ error: "boom" }, 500) : json({ ok: true, tokens }));
  }
  if (url.includes("/api/csrf")) return Promise.resolve(json({ csrf_token: "c" }));
  return Promise.resolve(json({ ok: true, extensions: [] }));
}

function mount(node: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/secrets"]}>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

beforeEach(() => {
  statusFails = false;
  storedKeys = ["site:a", "site:b"];
  tokensMode = "ok";
  tokens = [];
  posts = [];
  toastMock.success.mockClear();
  toastMock.error.mockClear();
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
});

afterEach(() => {
  vi.unstubAllGlobals();
  setClipboard(undefined);
});

const copied = () => toastMock.success.mock.calls.filter((c) => c[0] === "Copied").length;
const button = (name: string) => screen.getByRole("button", { name });
// A plain boolean, so a failure prints the DSS id (jest-dom matchers drop the message).
const isDisabled = (el: HTMLElement) => (el as HTMLButtonElement).disabled;

describe("F10 — an API-token list that was not read is not reported as empty", () => {
  it("says the list could not be loaded when GET /api/api_tokens fails", async () => {
    tokensMode = "fail";
    mount(<ApiTokensPanel />);
    await waitFor(() =>
      expect(screen.queryByText(/could not load api tokens/i), "DSS-F10-FAILED-LIST-SAYS-NONE").not.toBeNull(),
    );
    expect(screen.queryByText("No tokens yet"), "DSS-F10-FAILED-LIST-SAYS-NONE").toBeNull();
  });

  it("does not say 'No tokens yet' while the list is still loading", async () => {
    tokensMode = "pending";
    mount(<ApiTokensPanel />);
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("No tokens yet"), "DSS-F10-LOADING-SAYS-NONE").toBeNull();
  });

  it("control: a list that was read says 'No tokens yet' when empty and counts when not", async () => {
    const qc = mount(<ApiTokensPanel />);
    expect(await screen.findByText("No tokens yet")).toBeInTheDocument();
    tokens = [{ token_id: "t1", label: "ci", scope: "read", status: "active" }];
    await qc.invalidateQueries({ queryKey: ["api_tokens", "list"] });
    expect(await screen.findByText("1 token(s)")).toBeInTheDocument();
  });
});

describe("F11 — 'Copied' only after the clipboard took the shown-once token", () => {
  async function mintThenCopy() {
    mount(<ApiTokensPanel />);
    fireEvent.click(button("Create token"));
    await screen.findByText("api-tok-1");
    fireEvent.click(button("Copy"));
  }

  it("API token: no clipboard (plain-HTTP origin) is reported as a failed copy", async () => {
    setClipboard(undefined);
    await mintThenCopy();
    await waitFor(() => expect(toastMock.error, "DSS-F11-COPIED-WITHOUT-CLIPBOARD").toHaveBeenCalled());
    expect(copied(), "DSS-F11-COPIED-WITHOUT-CLIPBOARD").toBe(0);
  });

  it("API token: a refused clipboard write is reported as a failed copy", async () => {
    setClipboard({ writeText: vi.fn(() => Promise.reject(new Error("denied"))) });
    await mintThenCopy();
    await waitFor(() => expect(toastMock.error, "DSS-F11-COPIED-ON-REJECT").toHaveBeenCalled());
    expect(copied(), "DSS-F11-COPIED-ON-REJECT").toBe(0);
  });

  it("pairing token: no clipboard is reported as a failed copy", async () => {
    setClipboard(undefined);
    mount(<Secrets />);
    await screen.findByText(/Key names:/);
    fireEvent.click(button("Pair extension"));
    await screen.findByText("pair-tok-1");
    fireEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]);
    await waitFor(() => expect(toastMock.error, "DSS-F11-PAIRING-COPIED-WITHOUT-CLIPBOARD").toHaveBeenCalled());
    expect(copied(), "DSS-F11-PAIRING-COPIED-WITHOUT-CLIPBOARD").toBe(0);
  });

  it("control: a clipboard that takes the token gets it, and 'Copied' is said once", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    setClipboard({ writeText });
    await mintThenCopy();
    await waitFor(() => expect(copied()).toBe(1));
    expect(writeText).toHaveBeenCalledWith("api-tok-1");
    expect(toastMock.error).not.toHaveBeenCalled();
  });
});

describe("F12 — status-dependent actions wait for a status that was actually read", () => {
  async function parseAnImport() {
    fireEvent.change(screen.getByLabelText("Password export contents"), { target: { value: "x,y,z" } });
    fireEvent.click(button("Parse / preview"));
    await screen.findByRole("button", { name: "Apply selected" });
  }

  it("first load failed: 'Pair extension' and 'Apply selected' are disabled", async () => {
    statusFails = true;
    mount(<Secrets />);
    await screen.findByText(/Could not load secrets status/);
    await parseAnImport();
    expect(isDisabled(button("Pair extension")), "DSS-F12-ARMED-ON-FAILED-LOAD").toBe(true);
    expect(isDisabled(button("Apply selected")), "DSS-F12-ARMED-ON-FAILED-LOAD").toBe(true);
  });

  it("a poll that fails after an unlocked load disables both (the unlocked answer is stale)", async () => {
    const qc = mount(<Secrets />);
    await screen.findByText(/Key names:/);
    await parseAnImport();
    statusFails = true;
    await qc.invalidateQueries({ queryKey: ["secrets-status"] });
    await screen.findByText(/Could not load secrets status/);
    expect(isDisabled(button("Pair extension")), "DSS-F12-ARMED-ON-STALE-STATUS").toBe(true);
    expect(isDisabled(button("Apply selected")), "DSS-F12-ARMED-ON-STALE-STATUS").toBe(true);
  });

  it("control: with a read, unlocked status both are enabled", async () => {
    mount(<Secrets />);
    await screen.findByText(/Key names:/);
    await parseAnImport();
    expect(button("Pair extension")).toBeEnabled();
    expect(button("Apply selected")).toBeEnabled();
  });
});

describe("F13 — Delete posts only the key the select shows", () => {
  const zone = () => within(screen.getByLabelText("Delete a stored secret"));
  const deletes = () => posts.filter((p) => p.url.includes("/api/secrets/delete"));

  it("a key dropped by a poll is no longer armed for deletion", async () => {
    const qc = mount(<Secrets />);
    await screen.findByText(/Key names:/);
    const select = screen.getByLabelText("Stored secret key to delete") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "site:b" } });
    expect(zone().getByRole("button", { name: "Delete" })).toBeEnabled();
    storedKeys = ["site:a"];
    await qc.invalidateQueries({ queryKey: ["secrets-status"] });
    await waitFor(() => expect(within(select).queryByText("site:b")).toBeNull());
    expect(select.value).toBe("");
    const del = zone().getByRole("button", { name: "Delete" });
    expect(isDisabled(del), "DSS-F13-DELETE-ARMED-FOR-UNSHOWN-KEY").toBe(true);
    fireEvent.click(del);
    await new Promise((r) => setTimeout(r, 50));
    expect(deletes(), "DSS-F13-DELETE-ARMED-FOR-UNSHOWN-KEY").toEqual([]);
  });

  it("control: the selected key, still listed, is the one deleted", async () => {
    mount(<Secrets />);
    await screen.findByText(/Key names:/);
    fireEvent.change(screen.getByLabelText("Stored secret key to delete"), { target: { value: "site:b" } });
    fireEvent.click(zone().getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deletes().map((p) => p.body)).toEqual([{ key: "site:b" }]));
  });
});
