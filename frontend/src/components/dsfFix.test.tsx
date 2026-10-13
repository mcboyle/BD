// dsf-fix -- the deep-spa-forms-sites-settings sweep rows fixed in this cut
// (O1789 bughunt, finder S49-C). Ids (F2.., S14..) are the sweep's. The rig is
// the sweep's own: real components, api-client mocked with the server's answer
// shapes. F1/F3/F5 live in routes/SiteSettings.tsx and are not in this cut.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";

const { apiGetMock, apiPostMock, apiPutMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  toastMock: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), message: vi.fn() },
}));
vi.mock("@/lib/api-client", () => ({
  apiGet: apiGetMock, apiPost: apiPostMock, apiPut: apiPutMock, apiPatch: vi.fn(),
  apiDelete: vi.fn(), apiPostForm: vi.fn(), apiPostDownload: vi.fn(),
  ApiError: class extends Error { body?: unknown },
}));
vi.mock("sonner", () => ({ toast: toastMock, Toaster: () => null }));

import { AddSiteWizard } from "@/components/AddSiteWizard";
import { StoreRawSettings } from "@/components/StoreRawSettings";
import { Settings } from "@/routes/Settings";
import { freshQueryClient } from "@/test/wiredGateHarness";

const T = 15000;
type User = ReturnType<typeof userEvent.setup>;
function deferred<V>() {
  let resolve!: (v: V) => void, reject!: (e: unknown) => void;
  const promise = new Promise<V>((r, j) => { resolve = r; reject = j; });
  return { promise, resolve, reject };
}
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));
const posts = (p: string) => apiPostMock.mock.calls.filter((c) => c[0] === p);
const gets = (part: string) => apiGetMock.mock.calls.filter((c) => String(c[0]).includes(part));
let confirmAnswer = true;
let confirmAsked: string[] = [];

beforeAll(() => {
  if (!("ResizeObserver" in globalThis)) vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = function () {};
  vi.stubGlobal("confirm", (msg: string) => { confirmAsked.push(msg); return confirmAnswer; });
});
beforeEach(() => {
  apiGetMock.mockReset(); apiPostMock.mockReset(); apiPutMock.mockReset();
  Object.values(toastMock).forEach((f) => f.mockReset());
  confirmAnswer = true; confirmAsked = [];
  apiGetMock.mockImplementation(() => Promise.resolve({}));
  apiPostMock.mockImplementation(() => Promise.resolve({ ok: true }));
  apiPutMock.mockImplementation(() => Promise.resolve({ ok: true }));
});

function mount(el: ReactElement) {
  return render(
    <QueryClientProvider client={freshQueryClient()}>
      <MemoryRouter initialEntries={["/settings"]}>{el}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------- StoreRawSettings (F2, F9)
describe("StoreRawSettings", () => {
  const VPN = { path: "/x/tunnels.json", text: '{"schema_version":1,"tunnels":[{"tunnel_id":"t1"}]}' };
  const WID = { path: "/x/widgets.json", text: '{"global":["a"]}' };
  const editor = (store: string) => screen.getByLabelText(`${store} store JSON`) as HTMLTextAreaElement;
  async function openRaw(u: User) {
    mount(<StoreRawSettings />);
    await u.click(await screen.findByRole("button", { name: /Store metadata/ }));
  }
  const bothFast = () =>
    apiGetMock.mockImplementation((p: string) => Promise.resolve(p.includes("store=vpn") ? VPN : WID));

  it("F2 a late vpn reply never lands in the widgets editor, and Save posts widgets JSON", async () => {
    const slowVpn = deferred<unknown>();
    apiGetMock.mockImplementation((p: string) => (p.includes("store=vpn") ? slowVpn.promise : Promise.resolve(WID)));
    const u = userEvent.setup(); await openRaw(u);
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
    slowVpn.resolve(VPN);
    await pause(100);
    expect(editor("widgets").value).toBe(WID.text);
    expect(document.body.textContent).toContain("/x/widgets.json");
    expect(document.body.textContent).not.toContain("/x/tunnels.json");
    fireEvent.change(editor("widgets"), { target: { value: '{"global":["a","b"]}' } });
    await u.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(posts("/api/settings/store-raw")).toHaveLength(1));
    expect(posts("/api/settings/store-raw")[0][1]).toEqual({ store: "widgets", text: '{"global":["a","b"]}' });
  }, T);

  it("F2 control: the old store's text is not shown while the new store is still loading", async () => {
    const slowWid = deferred<unknown>();
    apiGetMock.mockImplementation((p: string) => (p.includes("store=vpn") ? Promise.resolve(VPN) : slowWid.promise));
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    await u.click(screen.getByRole("button", { name: "widgets" }));
    expect(editor("widgets").value).toBe("");
    expect(screen.getByRole("button", { name: /^Save/ })).toBeDisabled();
    slowWid.resolve(WID);
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
  }, T);

  it("F9 switching store with unsaved edits asks first; declining keeps the edits", async () => {
    bothFast();
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    fireEvent.change(editor("vpn"), { target: { value: '{"edited":true}' } });
    confirmAnswer = false;
    await u.click(screen.getByRole("button", { name: "widgets" }));
    expect(confirmAsked).toHaveLength(1);
    expect(confirmAsked[0]).toContain("unsaved edits to the vpn store");
    expect(editor("vpn").value).toBe('{"edited":true}');
    expect(gets("store=widgets")).toHaveLength(0);
    confirmAnswer = true;
    await u.click(screen.getByRole("button", { name: "widgets" }));
    expect(confirmAsked).toHaveLength(2);
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
  }, T);

  it("F9 control: a clean switch, and a click on the open store, ask nothing", async () => {
    bothFast();
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    await u.click(screen.getByRole("button", { name: "vpn" }));
    await pause(50);
    expect(editor("vpn").value).toBe(VPN.text);            // the open store is neither cleared nor reloaded
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
    expect(confirmAsked).toEqual([]);
    expect(gets("store=vpn")).toHaveLength(1);
  }, T);

  // r2 (lens D183 F2): Save and Rename reload the store they were clicked on when their POST
  // settles. That reload is the NEWEST load, so the sequence guard alone let it through.
  const SAVE = "/api/settings/store-raw", REKEY = "/api/settings/store-raw/rekey";
  const slowPost = (path: string) => {
    const d = deferred<unknown>();
    apiPostMock.mockImplementation((p: string) => (p === path ? d.promise : Promise.resolve({ ok: true })));
    return d;
  };
  async function saveVpnThenSwitch(u: User) {
    await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    fireEvent.change(editor("vpn"), { target: { value: VPN.text + " " } });
    await u.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(posts(SAVE)).toHaveLength(1));
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
  }

  it("r2 P1 a vpn save that settles after a switch to widgets leaves the widgets editor alone", async () => {
    bothFast();
    const slowSave = slowPost(SAVE);
    const u = userEvent.setup(); await saveVpnThenSwitch(u);
    slowSave.resolve({ ok: true });
    await pause(150);
    expect(editor("widgets").value).toBe(WID.text);
    expect(document.body.textContent).not.toContain("/x/tunnels.json");
    expect(gets("store=vpn")).toHaveLength(1);                 // no reload of the store that is not open
    expect(toastMock.success).toHaveBeenCalledWith("Saved vpn store + reloaded.");
    apiPostMock.mockImplementation(() => Promise.resolve({ ok: true }));
    fireEvent.change(editor("widgets"), { target: { value: WID.text + " " } });
    await u.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(posts(SAVE)).toHaveLength(2));
    expect(posts(SAVE)[1][1]).toEqual({ store: "widgets", text: WID.text + " " });
  }, T);

  it("r2 P2 a rename that settles after a switch to widgets leaves the widgets editor alone", async () => {
    bothFast();
    const slowRekey = slowPost(REKEY);
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    fireEvent.change(screen.getByLabelText("Current tunnel_id"), { target: { value: "t1" } });
    fireEvent.change(screen.getByLabelText("New tunnel_id"), { target: { value: "t2" } });
    await u.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(posts(REKEY)).toHaveLength(1));
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
    slowRekey.resolve({ ok: true });
    await pause(150);
    expect(editor("widgets").value).toBe(WID.text);
    expect(document.body.textContent).not.toContain("/x/tunnels.json");
    expect(gets("store=vpn")).toHaveLength(1);
  }, T);

  it("r2 a settled vpn save does not mark the widgets edits made meanwhile as saved", async () => {
    bothFast();
    const slowSave = slowPost(SAVE);
    const u = userEvent.setup(); await saveVpnThenSwitch(u);
    fireEvent.change(editor("widgets"), { target: { value: '{"global":["mine"]}' } });
    slowSave.resolve({ ok: true });
    await pause(150);
    expect(editor("widgets").value).toBe('{"global":["mine"]}');
    confirmAnswer = false; confirmAsked = [];
    await u.click(screen.getByRole("button", { name: "vpn" }));
    expect(confirmAsked).toHaveLength(1);
    expect(confirmAsked[0]).toContain("unsaved edits to the widgets store");
    expect(editor("widgets").value).toBe('{"global":["mine"]}');
  }, T);

  it("r2 a vpn save that FAILS after a switch names its store in a toast, not on the widgets editor", async () => {
    bothFast();
    const slowSave = slowPost(SAVE);
    const u = userEvent.setup(); await saveVpnThenSwitch(u);
    slowSave.reject(new Error("orphaned-secrets-xyz"));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain("Save of the vpn store rejected");
    expect(String(toastMock.error.mock.calls[0][0])).toContain("orphaned-secrets-xyz");
    expect(document.body.textContent).not.toContain("orphaned-secrets-xyz");
    expect(editor("widgets").value).toBe(WID.text);
  }, T);

  it("r2 a rename that FAILS after a switch reports in a toast, not on the widgets editor", async () => {
    bothFast();
    const slowRekey = slowPost(REKEY);
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    fireEvent.change(screen.getByLabelText("Current tunnel_id"), { target: { value: "t1" } });
    fireEvent.change(screen.getByLabelText("New tunnel_id"), { target: { value: "t2" } });
    await u.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(posts(REKEY)).toHaveLength(1));
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
    slowRekey.reject(new Error("no-such-tunnel-xyz"));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("Rename failed: no-such-tunnel-xyz"));
    expect(document.body.textContent).not.toContain("no-such-tunnel-xyz");
  }, T);

  it("r2 when the new store fails to load, the old store's text and path are not left under its label", async () => {
    apiGetMock.mockImplementation((p: string) =>
      p.includes("store=vpn") ? Promise.resolve(VPN) : Promise.reject(new Error("widgets-unreadable-xyz")));
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(document.body.textContent).toContain("widgets-unreadable-xyz"));
    expect(editor("widgets").value).toBe("");
    expect(document.body.textContent).not.toContain("/x/tunnels.json");
  }, T);

  it("r2 control: with no switch, Save reloads the saved store and a failure shows on the editor", async () => {
    bothFast();
    const u = userEvent.setup(); await openRaw(u);
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));
    fireEvent.change(editor("vpn"), { target: { value: VPN.text + " " } });
    await u.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(gets("store=vpn")).toHaveLength(2));
    await waitFor(() => expect(editor("vpn").value).toBe(VPN.text));     // reloaded from the server
    await u.click(screen.getByRole("button", { name: "widgets" }));
    await waitFor(() => expect(editor("widgets").value).toBe(WID.text));
    expect(confirmAsked.filter((m) => m.includes("unsaved edits"))).toEqual([]);   // the save cleared dirty
    apiPostMock.mockImplementation(() => Promise.reject(new Error("bad-shape-xyz")));
    fireEvent.change(editor("widgets"), { target: { value: WID.text + " " } });
    await u.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(document.body.textContent).toContain("bad-shape-xyz"));
    expect(toastMock.error).toHaveBeenCalledWith("Save rejected — nothing was written.");
  }, T);
});

// ---------------------------------------------------------------- AddSiteWizard (F4, F7, F8)
describe("AddSiteWizard", () => {
  const URL_BOX = "https://example.com/library";
  function mountWizard() {
    const onOpenChange = vi.fn();
    render(
      <QueryClientProvider client={freshQueryClient()}>
        <AddSiteWizard open onOpenChange={onOpenChange} />
      </QueryClientProvider>,
    );
    return onOpenChange;
  }
  async function fillWizard(u: User, pw = "pw") {
    await u.type(screen.getByPlaceholderText("e.g. ExampleSite"), "N");
    await u.type(screen.getByPlaceholderText(URL_BOX), "https://example.com/lib");
    if (pw) await u.type(screen.getByPlaceholderText("stored in the vault"), pw);
  }
  const addBtn = () => screen.getByRole("button", { name: /^(Add site|Saving)$/ });
  function wizardPost(map: Record<string, (body: unknown) => Promise<unknown>>) {
    apiPostMock.mockImplementation((p: string, body: unknown) =>
      (map[p] ?? (() => Promise.resolve({ ok: true, errors: [], warnings: [] })))(body));
  }
  async function settleValidation() {
    await waitFor(() => expect(posts("/api/sites/validate").length).toBeGreaterThan(0), { timeout: 3000 });
    await waitFor(() => expect(addBtn()).not.toBeDisabled(), { timeout: 3000 });
  }
  const LOCKED_CREATE = { "/api/sites": () => Promise.resolve({ ok: true, id: "n1", secrets_locked: true }) };
  async function toUnlockBox(u: User) {
    mountWizard(); await fillWizard(u); await settleValidation();
    await u.click(addBtn());
    return screen.findByLabelText("Vault master password");
  }

  for (const [flag, reason] of [["secrets_locked", "vault is locked again"], ["secrets_plaintext", "backend is plaintext"]] as const) {
    it(`F4 post-unlock PUT answered {ok:true, ${flag}:true} is reported as NOT stored`, async () => {
      wizardPost({ ...LOCKED_CREATE, "/api/secrets/unlock": () => Promise.resolve({ ok: true }) });
      apiPutMock.mockImplementation(() => Promise.resolve({ ok: true, [flag]: true }));
      const u = userEvent.setup();
      await u.type(await toUnlockBox(u), "m");
      await u.click(screen.getByRole("button", { name: /Unlock & save/ }));
      await waitFor(() => expect(apiPutMock).toHaveBeenCalledWith("/api/sites/n1", { password: "pw" }));
      await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
      expect(toastMock.error.mock.calls[0][0]).toContain("Credential not stored");
      expect(toastMock.error.mock.calls[0][0]).toContain(reason);
      expect(toastMock.success).not.toHaveBeenCalledWith("Credential stored.");
    }, T);
  }

  it("F4 control: a plain {ok:true} PUT still says stored", async () => {
    wizardPost({ ...LOCKED_CREATE, "/api/secrets/unlock": () => Promise.resolve({ ok: true }) });
    const u = userEvent.setup();
    await u.type(await toUnlockBox(u), "m");
    await u.click(screen.getByRole("button", { name: /Unlock & save/ }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith("Credential stored."));
    expect(toastMock.error).not.toHaveBeenCalled();
  }, T);

  it("F7 Enter twice in the unlock box sends one unlock; after a failure Enter works again", async () => {
    const first = deferred<unknown>();
    let calls = 0;
    wizardPost({ ...LOCKED_CREATE,
      "/api/secrets/unlock": () => { calls += 1; return calls === 1 ? first.promise : Promise.resolve({ ok: true }); } });
    const u = userEvent.setup();
    const box = await toUnlockBox(u);
    await u.type(box, "m"); await u.keyboard("{Enter}{Enter}");
    await pause(100);
    expect(posts("/api/secrets/unlock")).toHaveLength(1);
    first.reject(new Error("wrong master password"));
    expect(await screen.findByRole("alert")).toHaveTextContent("wrong master password");
    await u.click(box); await u.keyboard("{Enter}");
    await waitFor(() => expect(posts("/api/secrets/unlock")).toHaveLength(2));
  }, T);

  it("F8 Add is disabled inside the validation debounce, so a stale OK cannot create the site", async () => {
    wizardPost({
      "/api/sites/validate": (b) => Promise.resolve(String((b as { start_url?: string }).start_url).includes("bad")
        ? { ok: false, errors: ["start_url: bad"], warnings: [] } : { ok: true, errors: [], warnings: [] }),
      "/api/sites": () => Promise.resolve({ ok: true, id: "n2" }),
    });
    const u = userEvent.setup(); mountWizard(); await fillWizard(u, ""); await settleValidation();
    await u.type(screen.getByPlaceholderText(URL_BOX), "/bad");
    expect(addBtn()).toBeDisabled();
    await u.click(addBtn());
    await pause(700);
    expect(posts("/api/sites")).toHaveLength(0);
    expect(addBtn()).toBeDisabled();                       // and the validator's answer for /bad keeps it so
    expect(screen.getByText("start_url: bad")).toBeInTheDocument();
  }, T);

  it("F8 once the validator has answered for the draft on screen, Add creates that draft", async () => {
    wizardPost({ "/api/sites": () => Promise.resolve({ ok: true, id: "n3" }) });
    const u = userEvent.setup(); mountWizard(); await fillWizard(u, ""); await settleValidation();
    await u.type(screen.getByPlaceholderText(URL_BOX), "/more");
    expect(addBtn()).toBeDisabled();
    await waitFor(() => expect(addBtn()).not.toBeDisabled(), { timeout: 3000 });
    await u.click(addBtn());
    await waitFor(() => expect(posts("/api/sites")).toHaveLength(1));
    expect((posts("/api/sites")[0][1] as { start_url: string }).start_url).toBe("https://example.com/lib/more");
  }, T);
});

// ---------------------------------------------------------------- Settings supervisor (F6)
describe("Settings supervisor global bytes/sec", () => {
  async function openSup(u: User) {
    apiGetMock.mockImplementation((p: string) => Promise.resolve(({
      "/api/vpn/tunnels": { ok: true, tunnels: [] },
      "/api/settings/env/effective": { env: [] },
      "/api/settings/envfile": { env: [], path: "/x/.env", exists: true, writable: true },
      "/api/supervisor/status": { ok: true, stats: { enabled: true, config: { global_bps: 12500000, per_site_bps: {} } } },
    } as Record<string, unknown>)[p] ?? {}));
    mount(<Settings />);
    await waitFor(() => expect(document.getElementById("supervisor-throttle")).toBeTruthy(), { timeout: T });
    const sec = document.getElementById("supervisor-throttle") as HTMLElement;
    await u.click(within(sec).getAllByRole("button")[0]);
    const input = within(sec).getByRole("spinbutton") as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe("12500000"));
    return { sec, input };
  }
  async function apply(u: User, sec: HTMLElement) {
    await u.click(within(sec).getByRole("button", { name: "Apply" }));
    const dialog = await screen.findByRole("dialog");
    const said = dialog.textContent ?? "";
    await u.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(posts("/api/supervisor/configure").length).toBeGreaterThan(0));
    return { sent: (posts("/api/supervisor/configure").at(-1)![1] as { global_bps: number }).global_bps, said };
  }

  for (const typed of ["1.5e6", "2500.9", "-5", "1e3"]) {
    it(`F6 typed ${JSON.stringify(typed)} blocks Apply instead of being cut short by parseInt`, async () => {
      const u = userEvent.setup(); const { sec, input } = await openSup(u);
      fireEvent.change(input, { target: { value: typed } });
      expect(input.value).toBe(typed);                              // the box really holds it
      expect(within(sec).getByRole("button", { name: "Apply" })).toBeDisabled();
      expect(within(sec).getByRole("alert")).toHaveTextContent("whole bytes/sec only");
      await u.click(within(sec).getByRole("button", { name: "Apply" }));
      await pause(100);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(posts("/api/supervisor/configure")).toHaveLength(0);
      fireEvent.change(input, { target: { value: "7" } });          // a valid value re-enables it
      expect(within(sec).getByRole("button", { name: "Apply" })).toBeEnabled();
      expect(within(sec).queryByRole("alert")).not.toBeInTheDocument();
    }, T + 10000);
  }

  // Controls. Blank -> 0 is the deliberate clear that v3.66.1240 pins (Settings.supervisorRoundtrip).
  for (const [typed, want] of [["1000", 1000], ["0", 0], ["", 0]] as const) {
    it(`F6 control: typed ${JSON.stringify(typed)} is sent as ${want}`, async () => {
      const u = userEvent.setup(); const { sec, input } = await openSup(u);
      fireEvent.change(input, { target: { value: typed } });
      expect(within(sec).queryByRole("alert")).not.toBeInTheDocument();
      const { sent, said } = await apply(u, sec);
      expect(sent).toBe(want);
      expect(said).toContain(`global ${want} B/s`);
    }, T + 10000);
  }
});
