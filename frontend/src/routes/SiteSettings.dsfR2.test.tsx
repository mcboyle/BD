import { describe, it, expect, beforeEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { SiteSettings } from "./SiteSettings";

// dsf-fix r2 (O1789 bughunt deep-spa-forms-sites-settings F1 F3; spa-sites-queue-history F4-rest):
//   F1  PUT /api/sites/<id> answers 200 {ok, secrets_locked|secrets_plaintext} when it applied the
//       other fields but DROPPED the typed password; the editor said "Site settings saved".
//   F3  onSuccess ran setDraft({}), so an edit typed while the PUT was in flight was lost.
//   F4  the "Back to site" link put the raw site id into the path.
//   F5  number boxes clamped on every keystroke, so a value at or near the range floor could not be
//       typed (clear + "50" in [10,100] sent 100; "0.5" in [0.1,30] sent 0.15). Clamp now shows on blur.

vi.mock("sonner", async (importOriginal) => {
  const real = await importOriginal<typeof import("sonner")>();
  return {
    ...real,
    toast: Object.assign(vi.fn(), {
      ...real.toast,
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    }),
  };
});

const DELAY = {
  key: "delay",
  category: "download/perf",
  secret: false,
  preserve_on_blank: false,
  type: "integer",
  enum: null,
  description: "Delay between downloads",
  range: [0, 60],
  required: false,
  current: 3,
};

const CONCURRENT = {
  ...DELAY,
  key: "max_concurrent",
  description: "Parallel downloads",
  range: [10, 100],
  current: 20,
};

const CRAWL_DELAY = {
  ...DELAY,
  key: "crawler_delay_s",
  type: "number",
  description: "Crawler delay (s)",
  range: [0.1, 30],
  current: 2,
};

const PASSWORD = {
  key: "password",
  category: "login",
  secret: true,
  preserve_on_blank: true,
  type: "string",
  enum: null,
  description: "Site login password",
  range: null,
  required: false,
  current: { present: false },
};

function editable(sid: string) {
  return {
    ok: true,
    sid,
    field_meta: { delay: DELAY, max_concurrent: CONCURRENT, crawler_delay_s: CRAWL_DELAY },
    groups: { "download/perf": [DELAY, CONCURRENT, CRAWL_DELAY] },
    gated_meta: { password: PASSWORD },
    gated_groups: { login: [PASSWORD] },
  };
}

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

// putReply: what the PUT resolves with; a function lets a test hold it open.
let putReply: () => Promise<Response>;
let puts: unknown[] = [];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  puts = [];
  putReply = () => Promise.resolve(jsonResponse({ ok: true }));
  vi.spyOn(global, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return putReply();
    }
    if (url.includes("/editable")) {
      const sid = decodeURIComponent(url.split("/api/settings/site/")[1].split("/editable")[0]);
      return Promise.resolve(jsonResponse(editable(sid)));
    }
    if (url.includes("/api/vpn/tunnels")) return Promise.resolve(jsonResponse({ ok: true, tunnels: [] }));
    if (url.includes("/api/csrf")) return Promise.resolve(jsonResponse({ csrf_token: "dsf-r2-csrf" }));
    return Promise.resolve(jsonResponse({ ok: true }));
  });
});

function mount(siteId = "demo") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/sites/${encodeURIComponent(siteId)}/settings`]}>
        <Routes>
          <Route path="/sites/:siteId/settings" element={<SiteSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function savePassword(user: ReturnType<typeof userEvent.setup>, pw: string) {
  await user.click(await screen.findByRole("button", { name: /login \(sensitive\)/i }));
  await user.type(await screen.findByLabelText("password"), pw);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await user.click(await screen.findByRole("button", { name: "Save changes" }));
}

describe("SiteSettings save reports a dropped password (dsf F1)", () => {
  it.each([
    ["secrets_locked", /vault is locked/i],
    ["secrets_plaintext", /secrets backend is plaintext/i],
  ])("%s: no success toast, error names the reason, typed password kept", async (flag, reason) => {
    putReply = () => Promise.resolve(jsonResponse({ ok: true, [flag]: true }));
    const user = userEvent.setup();
    mount();
    await savePassword(user, "fixture-pw-dsf");

    await waitFor(() => expect(puts).toHaveLength(1));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
    const msg = String(vi.mocked(toast.error).mock.calls[0][0]);
    expect(msg).toMatch(/password was not stored/i);
    expect(msg).toMatch(reason);
    expect((screen.getByLabelText("password") as HTMLInputElement).value).toBe("fixture-pw-dsf");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("control: a plain ok reply still reports success and clears the box", async () => {
    const user = userEvent.setup();
    mount();
    await savePassword(user, "fixture-pw-dsf");

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Site settings saved"));
    expect(toast.error).not.toHaveBeenCalled();
    expect((screen.getByLabelText("password") as HTMLInputElement).value).toBe("");
  });
});

describe("SiteSettings keeps edits typed during a save (dsf F3)", () => {
  it("an edit made while the PUT is in flight survives the save", async () => {
    let release!: () => void;
    putReply = () =>
      new Promise<Response>((resolve) => {
        release = () => resolve(jsonResponse({ ok: true }));
      });
    mount();
    const input = (await screen.findByDisplayValue("3")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toEqual([{ delay: 5 }]));

    fireEvent.change(input, { target: { value: "7" } });
    release();

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Site settings saved"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(input.value).toBe("7");
    expect(screen.getByText("1 change(s) pending")).toBeInTheDocument();
  });

  it("control: with no edit during the save the draft clears", async () => {
    mount();
    const input = (await screen.findByDisplayValue("3")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Site settings saved"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled());
    expect(screen.queryByText(/change\(s\) pending/)).not.toBeInTheDocument();
  });

  it("control: a cleared (blank, never sent) password box does not outlive the save", async () => {
    const user = userEvent.setup();
    mount();
    const input = (await screen.findByDisplayValue("3")) as HTMLInputElement;
    await user.click(screen.getByRole("button", { name: /login \(sensitive\)/i }));
    const pw = await screen.findByLabelText("password");
    await user.type(pw, "x");
    await user.clear(pw);
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Site settings saved"));
    expect(puts).toEqual([{ delay: 5 }]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled());
  });
});

describe("SiteSettings back link encodes the site id (ssqh F4-rest)", () => {
  it("a site id with # and ? round-trips in the Back to site href", async () => {
    mount("a#b?c");
    await screen.findByDisplayValue("3");
    const links = screen.getAllByRole("link", { name: /back to site/i });
    expect(links.length).toBeGreaterThan(0);
    for (const a of links) {
      expect(a.getAttribute("href")).toBe("/sites/a%23b%3Fc");
    }
  });
});

describe("SiteSettings number boxes clamp on blur, not per keystroke (dsf F5)", () => {
  it("clear and type 50 in [10,100] sends 50", async () => {
    const user = userEvent.setup();
    mount();
    const num = (await screen.findByDisplayValue("20")) as HTMLInputElement;
    await user.clear(num);
    await user.type(num, "50");
    expect(num.value).toBe("50");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toEqual([{ max_concurrent: 50 }]));
  });

  it("select-all and type 0.5 in [0.1,30] sends 0.5", async () => {
    const user = userEvent.setup();
    mount();
    const num = (await screen.findByDisplayValue("2")) as HTMLInputElement;
    await user.tripleClick(num);
    await user.keyboard("0.5");
    expect(num.value).toBe("0.5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toEqual([{ crawler_delay_s: 0.5 }]));
  });

  it("control: an out-of-range value still shows its clamp on blur and is sent clamped", async () => {
    const user = userEvent.setup();
    mount();
    const num = (await screen.findByDisplayValue("20")) as HTMLInputElement;
    await user.clear(num);
    await user.type(num, "500");
    await user.tab();
    expect(num.value).toBe("100");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toEqual([{ max_concurrent: 100 }]));
  });

  it("control: Cancel puts the stored value back in a box still being typed in", async () => {
    mount();
    const num = (await screen.findByDisplayValue("20")) as HTMLInputElement;
    fireEvent.change(num, { target: { value: "55" } });
    expect(num.value).toBe("55");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(num.value).toBe("20");
  });
});
