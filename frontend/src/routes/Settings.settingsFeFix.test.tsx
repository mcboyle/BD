// settings-fe-fix F05-F07, F10-F16: the settings surfaces report and keep what
// actually happened. Each case names its finding in every assertion message, so
// a failure reads as the finding that came back.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";

import { Settings } from "@/routes/Settings";
import AlertRules from "@/routes/AlertRules";
import Notifications from "@/routes/Notifications";
import { Secrets } from "@/routes/Secrets";
import { WebhooksPanel } from "@/components/ui/WebhooksPanel";
import { StoreRawSettings } from "@/components/StoreRawSettings";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock("@/components/sections/PushSection", () => ({
  PushSection: () => null,
}));

type Json = Record<string, unknown>;

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function makeQc() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

function renderAt(qc: QueryClient, path: string, ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// A /api/global_config that stores what is posted, like the real endpoint.
interface ConfigServer {
  cfg: Json;
  posts: Json[];
  failPost: (body: Json) => boolean;
  holdGet: boolean;
  supervisor: Json;
  supPosts: Json[];
}

function stubSettingsServer(cfg: Json): ConfigServer {
  const srv: ConfigServer = {
    cfg: { ...cfg },
    posts: [],
    failPost: () => false,
    holdGet: false,
    supervisor: { enabled: true, config: { global_bps: 1000, per_site_bps: { site1: 500 } } },
    supPosts: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      if (url.includes("/api/supervisor/configure") && method === "POST") {
        const b = JSON.parse(String(init?.body || "{}")) as Json;
        srv.supPosts.push(b);
        srv.supervisor = {
          enabled: b.enabled,
          config: { global_bps: b.global_bps, per_site_bps: b.per_site_bps },
        };
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      if (url.includes("/api/supervisor/status")) {
        return Promise.resolve(jsonResponse({ stats: srv.supervisor }));
      }
      if (url.endsWith("/api/global_config") && method === "POST") {
        const b = JSON.parse(String(init?.body || "{}")) as Json;
        srv.posts.push(b);
        if (srv.failPost(b)) {
          return Promise.resolve(jsonResponse({ error: "disk full" }, 500));
        }
        srv.cfg = { ...srv.cfg, ...b };
        return Promise.resolve(jsonResponse(srv.cfg));
      }
      if (url.endsWith("/api/global_config")) {
        if (srv.holdGet) return new Promise<Response>(() => {});
        return Promise.resolve(jsonResponse({ ...srv.cfg }));
      }
      if (url.includes("/api/settings/envfile")) {
        return Promise.resolve(jsonResponse({ ok: true, path: "/srv/state/.env", env: [] }));
      }
      if (url.includes("/api/settings/store-raw")) {
        return Promise.resolve(
          jsonResponse({ ok: true, store: "vpn", path: "/srv/state/tunnels.json", text: "{}" }),
        );
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    }),
  );
  return srv;
}

const saveButton = () => screen.getAllByRole("button", { name: /^Save$/i })[0];
const rowControl = <T extends Element>(label: string, sel: string): T =>
  screen.getByText(label).closest("[data-srow]")!.querySelector(sel) as T;

describe("settings-fe-fix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("F05: a refetch keeps an unsaved edit, adopts untouched fields, and a save re-baselines", async () => {
    const srv = stubSettingsServer({
      watch_folder: "/initial",
      path_allowlist: ["/srv/a"],
      rate_limit_domain_overrides: {},
    });
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    const watch = (await screen.findByPlaceholderText("/path/to/watch")) as HTMLInputElement;
    await waitFor(() => expect(watch.value, "F05: first load seeds the form").toBe("/initial"));
    const allow = rowControl<HTMLTextAreaElement>("Path allowlist", "textarea");
    expect(allow.value, "F05: first load seeds the allowlist").toBe("/srv/a");

    fireEvent.change(watch, { target: { value: "/unsaved-edit" } });
    act(() => {
      qc.setQueryData(["global-config"], {
        watch_folder: "/initial",
        path_allowlist: ["/srv/b"],
        rate_limit_domain_overrides: {},
      });
    });
    await waitFor(() =>
      expect(allow.value, "F05: an untouched field follows the refetch").toBe("/srv/b"),
    );
    expect(watch.value, "F05: background refetch clobbered the unsaved edit").toBe("/unsaved-edit");

    fireEvent.click(saveButton());
    await waitFor(() => expect(srv.posts.length).toBe(1));
    expect(srv.posts[0].watch_folder, "F05: Save sends the kept edit").toBe("/unsaved-edit");
    await screen.findByText("Settings saved");

    act(() => {
      qc.setQueryData(["global-config"], { ...srv.cfg, watch_folder: "/changed-elsewhere" });
    });
    await waitFor(() =>
      expect(watch.value, "F05: after a save the saved field follows the server again").toBe(
        "/changed-elsewhere",
      ),
    );
  });

  it("F05: the 'Settings saved' banner goes away on the next edit and on a failed save", async () => {
    const srv = stubSettingsServer({
      watch_folder: "/initial",
      path_allowlist: [],
      rate_limit_domain_overrides: {},
    });
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    const watch = (await screen.findByPlaceholderText("/path/to/watch")) as HTMLInputElement;
    await waitFor(() => expect(watch.value).toBe("/initial"));
    fireEvent.change(watch, { target: { value: "/saved-once" } });
    fireEvent.click(saveButton());
    await screen.findByText("Settings saved");

    fireEvent.change(watch, { target: { value: "/will-fail" } });
    expect(screen.queryByText("Settings saved"), "F05: banner outlived the next edit").toBeNull();

    srv.failPost = () => true;
    fireEvent.click(saveButton());
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.queryByText("Settings saved"), "F05: banner shown after a failed save").toBeNull();
  });

  it("F06: AlertRules reports a rejected save, a failed remove and both test-send outcomes", async () => {
    let mode: "reject" | "ok" = "reject";
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method || "GET").toUpperCase();
        if (url.includes("/api/alerts/rules") && method === "GET") {
          return Promise.resolve(
            jsonResponse({
              rules: [{ id: "custom_1", metric: "bd_pending_count", op: ">=", threshold: 10, builtin: false }],
            }),
          );
        }
        if (mode === "reject") {
          return Promise.resolve(jsonResponse({ ok: false, error: "invalid rule" }, 400));
        }
        return Promise.resolve(jsonResponse({ evaluated: 6, fired: 2, results: [] }));
      }),
    );
    const qc = makeQc();
    renderAt(qc, "/alerts/rules", <AlertRules />);

    await screen.findByText("custom_1");
    fireEvent.change(screen.getByLabelText("rule id"), { target: { value: "bad_rule" } });
    fireEvent.change(screen.getByLabelText("threshold"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "Save rule" }));
    const alert = await screen.findByRole("alert").catch(() => null);
    expect(alert?.textContent ?? "", "F06: a 400 save rendered no alert").toMatch(/invalid rule/);

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(
        vi.mocked(toast.error).mock.calls.map((c) => String(c[0])),
        "F06: a 400 remove said nothing",
      ).toContainEqual(expect.stringMatching(/custom_1 was not removed/)),
    );

    fireEvent.click(screen.getByRole("button", { name: /Test-send/i }));
    await waitFor(() =>
      expect(
        vi.mocked(toast.error).mock.calls.map((c) => String(c[0])),
        "F06: a failed test-send said nothing",
      ).toContainEqual(expect.stringMatching(/Test-send failed/)),
    );

    mode = "ok";
    await waitFor(() => expect(screen.getByRole("button", { name: /Test-send/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /Test-send/i }));
    await waitFor(() =>
      expect(toast.success, "F06: a successful test-send said nothing").toHaveBeenCalledWith(
        "Evaluation pass ran",
      ),
    );
  });

  it("F07: a failed sound write is reported and a page Save does not post the sound keys", async () => {
    const srv = stubSettingsServer({
      watch_folder: "/initial",
      sound_on_complete: true,
      path_allowlist: [],
      rate_limit_domain_overrides: {},
    });
    srv.failPost = (b) => Object.keys(b).length === 1 && "sound_on_complete" in b;
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    const watch = (await screen.findByPlaceholderText("/path/to/watch")) as HTMLInputElement;
    await waitFor(() => expect(watch.value).toBe("/initial"));
    fireEvent.click(screen.getAllByText("System")[1]);
    fireEvent.click(screen.getByRole("switch", { name: "Sync sound preference across devices" }));
    await waitFor(() => expect(srv.posts.length).toBe(1));

    const soundSwitch = () => rowControl<HTMLElement>("Sound on completion", '[role="switch"]');
    fireEvent.click(soundSwitch());
    await waitFor(() => expect(srv.posts.length).toBe(2));
    await waitFor(() =>
      expect(
        vi.mocked(toast.error).mock.calls.map((c) => String(c[0])),
        "F07: the failed sound write was swallowed",
      ).toContainEqual(expect.stringMatching(/Sound setting not saved/)),
    );

    // The sound write lands, but the form's refetch has not come back yet.
    srv.failPost = () => false;
    srv.holdGet = true;
    fireEvent.click(soundSwitch());
    await waitFor(() => expect(srv.posts.length).toBe(3));
    const soundNow = srv.cfg.sound_on_complete;

    fireEvent.change(watch, { target: { value: "/new-watch" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(srv.posts.length).toBe(4));
    expect(srv.posts[3].watch_folder).toBe("/new-watch");
    expect(
      "sound_on_complete" in srv.posts[3],
      `F07: page Save posted sound_on_complete=${String(srv.posts[3].sound_on_complete)} over the synced ${String(soundNow)}`,
    ).toBe(false);
    expect("sound_sync_enabled" in srv.posts[3], "F07: page Save posted sound_sync_enabled").toBe(false);
  });

  it("F10: slow_query_ms posts a whole number >= 1 (default 100 on blank)", async () => {
    const srv = stubSettingsServer({
      slow_query_log: true,
      slow_query_ms: 100,
      path_allowlist: [],
      rate_limit_domain_overrides: {},
    });
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    await screen.findByLabelText("Filter settings");
    fireEvent.click(screen.getAllByText("Diagnostics")[1]);
    const slow = rowControl<HTMLInputElement>("Slow-query threshold (ms)", "input");
    const cases: [string, number][] = [["12.5", 12], ["", 100], ["-10", 1]];
    let prev = 100;
    for (const [typed, want] of cases) {
      if (want === prev) continue;
      const n = srv.posts.length;
      fireEvent.change(slow, { target: { value: typed } });
      fireEvent.click(saveButton());
      await waitFor(() => expect(srv.posts.length, `F10: no save for ${JSON.stringify(typed)}`).toBe(n + 1));
      expect(srv.posts[n].slow_query_ms, `F10: typed ${JSON.stringify(typed)}`).toBe(want);
      await waitFor(() => expect(saveButton()).toBeDisabled());
      prev = want;
    }
  });

  it("F11: Domain overrides refuses non-numeric or negative limits before Save", async () => {
    const srv = stubSettingsServer({ path_allowlist: [], rate_limit_domain_overrides: {} });
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    await screen.findByLabelText("Filter settings");
    const box = rowControl<HTMLTextAreaElement>("Domain overrides", "textarea");
    for (const bad of [
      '{"example.com": {"max_concurrent": "not-a-number", "max_per_sec": 1}}',
      '{"example.com": {"max_concurrent": 2, "max_per_sec": -99}}',
      '{"example.com": {"max_concurrent": 1.5}}',
      '{"example.com": 3}',
    ]) {
      fireEvent.change(box, { target: { value: bad } });
      fireEvent.click(saveButton());
      await act(async () => {
        await new Promise((r) => setTimeout(r, 30));
      });
      expect(srv.posts, `F11: posted ${bad}`).toEqual([]);
      expect(screen.queryAllByText(/Limits for "example\.com" must be/).length, `F11: no error for ${bad}`).toBeGreaterThan(0);
    }
    fireEvent.change(box, { target: { value: '{"example.com": {"max_concurrent": 2, "max_per_sec": 1.5}}' } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(srv.posts.length, "F11 control: a valid override did not save").toBe(1));
    expect(srv.posts[0].rate_limit_domain_overrides).toEqual({
      "example.com": { max_concurrent: 2, max_per_sec: 1.5 },
    });
  });

  it("F12: StoreRawSettings Validate refuses a top-level array or bare value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/settings/store-raw")) {
          return Promise.resolve(
            jsonResponse({ ok: true, store: "vpn", path: "/srv/state/tunnels.json", text: "{}" }),
          );
        }
        return Promise.resolve(jsonResponse({ ok: true }));
      }),
    );
    const qc = makeQc();
    render(
      <QueryClientProvider client={qc}>
        <StoreRawSettings />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("Store metadata (raw / advanced)"));
    const area = await screen.findByLabelText("vpn store JSON");
    for (const bad of ["[1, 2, 3]", "42", "null"]) {
      vi.mocked(toast.success).mockClear();
      fireEvent.change(area, { target: { value: bad } });
      fireEvent.click(screen.getByRole("button", { name: "Validate" }));
      expect(toast.success, `F12: ${bad} toasted "Valid JSON."`).not.toHaveBeenCalledWith("Valid JSON.");
      expect(screen.getByText(/top-level JSON must be an object/), `F12: no error for ${bad}`).toBeTruthy();
    }
    fireEvent.change(area, { target: { value: '{"a": 1}' } });
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(toast.success, "F12 control: an object is valid").toHaveBeenCalledWith("Valid JSON.");
  });

  it("F13: Notifications fields follow the server again after a save", async () => {
    const server = { tgAllow: "111", appriseOn: true };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method || "GET").toUpperCase();
        if (method === "POST") {
          const b = JSON.parse(String(init?.body || "{}")) as Json;
          if ("tg_bot_allowlist" in b) server.tgAllow = String(b.tg_bot_allowlist);
          if ("notify_apprise_enabled" in b) server.appriseOn = Boolean(b.notify_apprise_enabled);
          return Promise.resolve(jsonResponse({ ok: true, settings: b }));
        }
        if (url.includes("/api/notify/apprise/settings")) {
          return Promise.resolve(
            jsonResponse({
              settings: {
                notify_apprise_enabled: server.appriseOn,
                notify_apprise_urls_set: true,
                notify_apprise_urls_count: 1,
              },
            }),
          );
        }
        if (url.includes("/api/tg/settings")) {
          return Promise.resolve(
            jsonResponse({
              settings: { tg_bot_enabled: true, tg_bot_token_set: true, tg_bot_allowlist: server.tgAllow },
            }),
          );
        }
        if (url.includes("/api/tg/status")) {
          return Promise.resolve(jsonResponse({ available: true, running: true, allowlist_size: 1 }));
        }
        return Promise.resolve(jsonResponse({ alerts: [], waiting: [], running: [] }));
      }),
    );
    const qc = makeQc();
    renderAt(qc, "/notifications", <Notifications />);

    const allow = (await screen.findByPlaceholderText(/chat-id allowlist/i)) as HTMLInputElement;
    await waitFor(() => expect(allow.value).toBe("111"));
    fireEvent.change(allow, { target: { value: "222" } });
    fireEvent.click(screen.getByRole("button", { name: "Save telegram" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(server.tgAllow).toBe("222"));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Telegram settings saved"));

    const apprise = screen.getByLabelText("Enable apprise notifications") as HTMLInputElement;
    fireEvent.click(apprise);
    expect(apprise.checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save apprise" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(server.appriseOn).toBe(false));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Apprise settings saved"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    // The 30s poll brings values written elsewhere.
    act(() => {
      qc.setQueryData(["tg", "settings"], {
        settings: { tg_bot_enabled: true, tg_bot_token_set: true, tg_bot_allowlist: "333,444" },
      });
      qc.setQueryData(["notify", "apprise", "settings"], {
        settings: { notify_apprise_enabled: true, notify_apprise_urls_set: true, notify_apprise_urls_count: 1 },
      });
    });
    await waitFor(() => expect(allow.value, "F13: tg allowlist edit shadows the poll").toBe("333,444"));
    await waitFor(() => expect(apprise.checked, "F13: apprise enable edit shadows the poll").toBe(true));
  });

  it("F14: WebhooksPanel drain toast reads the drain counters and a refused remove is an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method || "GET").toUpperCase();
        if (url.includes("/api/webhooks/drain") && method === "POST") {
          return Promise.resolve(jsonResponse({ processed: 5, ok: 4, retried: 1, dropped: 0 }));
        }
        if (url.includes("/api/webhooks/") && method === "DELETE") {
          return Promise.resolve(jsonResponse({ ok: false }));
        }
        return Promise.resolve(
          jsonResponse({
            subscriptions: [{ id: "wh-1", url: "https://example.com/hook", events: ["download.done"], secret: "" }],
          }),
        );
      }),
    );
    const qc = makeQc();
    render(
      <QueryClientProvider client={qc}>
        <WebhooksPanel />
      </QueryClientProvider>,
    );

    await screen.findByText("https://example.com/hook");
    fireEvent.click(screen.getByRole("button", { name: "Drain queue now" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.success, "F14: drain toast ignores processed/ok/retried/dropped").toHaveBeenCalledWith(
      "Drain complete: 5 processed, 4 delivered, 1 retried, 0 dropped",
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove webhook wh-1" }));
    await waitFor(() =>
      expect(toast.error, "F14: ok:false remove was not an error").toHaveBeenCalledWith(
        "Webhook wh-1 was not removed",
      ),
    );
    expect(toast.success, "F14: ok:false remove toasted success").not.toHaveBeenCalledWith("Webhook removed");
  });

  it("F15: supervisor per-site rates must be whole bytes/sec, and fields follow the server after Apply", async () => {
    const srv = stubSettingsServer({ path_allowlist: [], rate_limit_domain_overrides: {} });
    const qc = makeQc();
    renderAt(qc, "/settings", <Settings />);

    await screen.findByLabelText("Filter settings");
    fireEvent.click(screen.getAllByText("Supervisor throttle")[1]);
    const perSite = (await screen.findByLabelText("Per-site bytes per second JSON")) as HTMLInputElement;
    await waitFor(() => expect(perSite.value).toBe('{"site1":500}'));
    for (const bad of ['{"siteA": "not-a-number"}', '{"siteA": 12.5}', '{"siteA": -5}']) {
      fireEvent.change(perSite, { target: { value: bad } });
      expect(screen.queryByText("valid"), `F15: ${bad} shown as valid`).toBeNull();
      expect(screen.getByRole("button", { name: "Apply" }), `F15: Apply enabled for ${bad}`).toBeDisabled();
    }

    fireEvent.change(perSite, { target: { value: '{"siteA": 700}' } });
    expect(screen.getByText("valid")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(srv.supPosts.length).toBe(1));
    expect(srv.supPosts[0].per_site_bps).toEqual({ siteA: 700 });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Supervisor limits applied"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });

    act(() => {
      qc.setQueryData(["supervisor", "status"], {
        stats: { enabled: true, config: { global_bps: 1000, per_site_bps: { siteA: 900 } } },
      });
    });
    await waitFor(() =>
      expect(perSite.value, "F15: the applied edit shadows later server status").toBe('{"siteA":900}'),
    );
  });

  it("F16: an import that saved nothing is an error and keeps the parsed rows", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method || "GET").toUpperCase();
        if (url.includes("/api/secrets/import_file") && method === "POST") {
          return Promise.resolve(
            jsonResponse({
              ok: true,
              format: "chrome_csv",
              count: 1,
              records: [{ name: "SiteA", url: "https://a.example", username: "u1", password: "pw1" }],
            }),
          );
        }
        if (url.includes("/api/secrets/import_apply") && method === "POST") {
          return Promise.resolve(
            jsonResponse({ ok: true, saved: 0, skipped: 0, errors: ["row 0: vault write failed"] }),
          );
        }
        if (url.includes("/api/api_tokens")) return Promise.resolve(jsonResponse({ ok: true, tokens: [] }));
        if (url.includes("/api/secrets/extension/list_paired")) {
          return Promise.resolve(jsonResponse({ ok: true, extensions: [] }));
        }
        return Promise.resolve(
          jsonResponse({
            ok: true,
            backend: "master_password",
            is_unlocked: true,
            is_initialized: true,
            plaintext_count: 0,
            plaintext_sites: [],
            stored_keys: [],
            keyring_available: true,
            crypto_available: true,
          }),
        );
      }),
    );
    const qc = makeQc();
    renderAt(qc, "/secrets", <Secrets />);

    const exportBox = (await screen.findByLabelText("Password export contents")) as HTMLTextAreaElement;
    const csv = "name,url,username,password\nSiteA,https://a.example,u1,pw1";
    fireEvent.change(exportBox, { target: { value: csv } });
    fireEvent.click(screen.getByRole("button", { name: "Parse / preview" }));
    const siteId = (await screen.findByLabelText("Site id for SiteA")) as HTMLInputElement;
    fireEvent.change(siteId, { target: { value: "my_custom_site_id" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply selected" }));

    await waitFor(() =>
      expect(
        vi.mocked(toast.error).mock.calls.map((c) => String(c[0])),
        "F16: saved 0 with errors was not an error",
      ).toContainEqual(expect.stringMatching(/Nothing saved \(1 error\): row 0: vault write failed/)),
    );
    expect(toast.success, "F16: saved 0 toasted success").not.toHaveBeenCalledWith("Saved 0, skipped 0 (1 error)");
    expect(exportBox.value, "F16: the pasted export was wiped").toBe(csv);
    expect(
      (screen.queryByLabelText("Site id for SiteA") as HTMLInputElement | null)?.value,
      "F16: the parsed rows / site_id mapping were wiped",
    ).toBe("my_custom_site_id");
  });
});
