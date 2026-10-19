import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { createHash } from "node:crypto";

import {
  ImportsCenter,
  cleanBulkSiteName,
  isValidHttpLoginUrl,
  parseBulkSitesCsvPreview,
  pyTitle,
} from "./ImportsCenter";
import fixture from "./ImportsCenter.bulkPreview.cases.json";
import rules from "./ImportsCenter.bulkSiteRules.cases.json";

type FetchCall = { url: string; method: string; body?: string };
let calls: FetchCall[] = [];

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ImportsCenter />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      const body = typeof init?.body === "string" ? init.body : undefined;
      calls.push({ url, method, body });
      if (url === "/api/csrf") return jsonResponse({ csrf_token: "test-csrf" });
      if (url.startsWith("/api/captcha/pending")) return jsonResponse({ ok: true, pending: [] });
      if (url.startsWith("/api/sites/bulk_csv")) {
        return jsonResponse({ ok: true, created: 1, results: [] });
      }
      return new Promise<Response>(() => {});
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("F026 Bulk site import parsed-row preview and cancel before commit", () => {
  it("renders parsed-row preview (columns and rows) when CSV is pasted into Bulk site import", () => {
    mount();
    const csv = [
      "name,login_url,username,password,template,login_template",
      "Alpha Site,https://alpha.example/login,alice,supersecret123,video_js,form_login",
      ",https://www.beta-club.example/auth,,,hls_stream,",
    ].join("\n");

    fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
      target: { value: csv },
    });

    const preview = screen.getByTestId("bulk-sites-preview");
    expect(preview).toHaveTextContent(/2 valid\s*·\s*0 invalid/i);
    expect(preview).toHaveTextContent(
      /columns:\s*name,\s*login_url,\s*username,\s*password,\s*template,\s*login_template/i,
    );
    expect(within(preview).getByText("Alpha Site")).toBeInTheDocument();
    expect(within(preview).getByText("https://alpha.example/login")).toBeInTheDocument();
    expect(within(preview).getByText("alice (password set)")).toBeInTheDocument();
    expect(within(preview).getByText("video_js")).toBeInTheDocument();
    expect(within(preview).getByText("form_login")).toBeInTheDocument();
    expect(preview).not.toHaveTextContent("supersecret123");
    // Blank name derives from login_url hostname
    expect(within(preview).getByText("Beta Club")).toBeInTheDocument();
    expect(within(preview).getByText("https://www.beta-club.example/auth")).toBeInTheDocument();
  });

  it("flags invalid rows (missing login_url or malformed URL) in the parsed-row preview before commit", () => {
    mount();
    const csv = [
      "name,login_url,template",
      "Good One,https://good.example/login,video_js",
      "Missing Url,,video_js",
      "Bad Url,not-a-valid-url,video_js",
    ].join("\n");

    fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
      target: { value: csv },
    });

    const preview = screen.getByTestId("bulk-sites-preview");
    expect(preview).toHaveTextContent(/1 valid\s*·\s*2 invalid/i);
    expect(within(preview).getByText(/invalid — missing login_url/i)).toBeInTheDocument();
    expect(within(preview).getByText(/invalid — invalid login_url/i)).toBeInTheDocument();
  });

  it.each([
    ["Firefox export", '"url","username","password"\n"https://foo.example/login","alice","supersecret123"'],
    ["typo'd header", "name,username,password,url\nFoo,alice,supersecret123,https://foo.example/login"],
    ["password first", "password,name,login-url\nsupersecret123,Foo,https://foo.example/login"],
  ])(
    "header without login_url (%s): flags the header, lists only data rows, echoes no cell (never the password)",
    (_shape, csv) => {
      mount();
      fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
        target: { value: csv },
      });

      const preview = screen.getByTestId("bulk-sites-preview");
      expect(preview).not.toHaveTextContent("supersecret123");
      // No positional mapping: any column could be the secret, so no data cell may reach the table.
      for (const cell of ["alice", "foo.example", "Foo"]) {
        expect(preview).not.toHaveTextContent(cell);
      }
      expect(preview).toHaveTextContent(/Invalid header: header row must include a 'login_url' column/);
      expect(preview).toHaveTextContent(/0 valid\s*·\s*1 invalid/i);
      expect(within(preview).queryByText("line 1")).toBeNull();
      expect(within(preview).getByText("line 2")).toBeInTheDocument();
    },
  );

  it.each([
    ["LF, pasted", "\n", "paste"],
    ["CRLF, uploaded file", "\r\n", "upload"],
  ])(
    "quoted multiline password (%s) stays in one row; neither preview shows it",
    async (_shape, eol, via) => {
      mount();
      const csv =
        `name,login_url,username,password,template${eol}` +
        `Alpha,https://a.example,alice,"firstline${eol}supersecret123",video_js`;
      if (via === "paste") {
        fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
          target: { value: csv },
        });
      } else {
        // An upload keeps the CR bytes; a textarea would normalise CRLF to LF before parsing.
        const file = new File([csv], "sites.csv", { type: "text/csv" });
        fireEvent.change(screen.getByLabelText(/upload csv/i), { target: { files: [file] } });
      }

      const preview = await screen.findByTestId("bulk-sites-preview");
      await waitFor(() => {
        expect(within(preview).getByText("Alpha")).toBeInTheDocument();
      });
      expect(preview).not.toHaveTextContent("supersecret123");
      expect(preview).not.toHaveTextContent("firstline");
      expect(preview).toHaveTextContent(/1 valid\s*·\s*0 invalid/i);
      expect(preview).toHaveTextContent("alice (password set)");
      expect(within(preview).getByText("line 2")).toBeInTheDocument();
      expect(within(preview).queryByText("line 3")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Import sites" }));
      const dialog = await screen.findByRole("dialog");
      const confirmPreview = within(dialog).getByTestId("bulk-sites-confirm-preview");
      expect(confirmPreview).not.toHaveTextContent("supersecret123");
      expect(confirmPreview).not.toHaveTextContent("firstline");
      expect(confirmPreview).toHaveTextContent(/1 valid\s*·\s*0 invalid/i);
      expect(within(confirmPreview).getByText("Alpha")).toBeInTheDocument();

      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    },
  );

  it.each([
    [
      "unterminated quote at EOF",
      'Alpha,https://a.example,alice,"firstline\nsupersecret123',
      /1 valid\s*·\s*1 invalid/i,
      "unterminated quote (values hidden)",
      ["line 2", "line 3"],
    ],
    [
      "unterminated quote, row follows",
      'Alpha,https://a.example,alice,"firstline\nsupersecret123\nBeta,https://b.example,bob,pw,video_js',
      /2 valid\s*·\s*1 invalid/i,
      "unterminated quote (values hidden)",
      ["line 2", "line 3", "line 4"],
    ],
    [
      "multiline block that is not one record",
      'Alpha,https://a.example,alice,"firstline\nsupersecret123"x,video_js',
      /1 valid\s*·\s*1 invalid/i,
      "malformed quoted field (values hidden)",
      ["line 2", "line 3"],
    ],
    [
      "single line, text after a closing quote",
      'Alpha,https://a.example,alice,"pw"supersecret123,video_js',
      /1 valid\s*·\s*0 invalid/i,
      "malformed quoted field (values hidden)",
      ["line 2"],
    ],
  ])(
    "%s: counted line by line as the backend does, but no value of a record that failed the strict parse is shown",
    async (_shape, body, counts, note, lines) => {
      mount();
      fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
        target: { value: `name,login_url,username,password,template\n${body}` },
      });

      const expectRedacted = (panel: HTMLElement) => {
        for (const text of ["supersecret123", "firstline", "Alpha", "alice", "a.example", "Beta", "bob"]) {
          expect(panel).not.toHaveTextContent(text);
        }
        expect(panel).toHaveTextContent(counts);
        expect(panel).toHaveTextContent(note);
      };
      const preview = screen.getByTestId("bulk-sites-preview");
      expectRedacted(preview);
      expect(within(preview).getAllByText(/^line \d+$/).map((el) => el.textContent)).toEqual(lines);

      fireEvent.click(screen.getByRole("button", { name: "Import sites" }));
      const dialog = await screen.findByRole("dialog");
      expectRedacted(within(dialog).getByTestId("bulk-sites-confirm-preview"));

      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    },
  );

  it("unterminated quote in the header: no column names or values from the failed record are shown", () => {
    mount();
    fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
      target: { value: 'name,login_url,"password\nAlpha,https://a.example,alice,supersecret123' },
    });

    const preview = screen.getByTestId("bulk-sites-preview");
    expect(preview).toHaveTextContent(/1 valid\s*·\s*0 invalid/i);
    expect(preview).toHaveTextContent("unterminated quote (values hidden)");
    for (const text of ["columns:", "supersecret123", "Alpha", "alice", "a.example"]) {
      expect(preview).not.toHaveTextContent(text);
    }
  });

  it("populates CSV and parsed-row preview from file upload input", async () => {
    mount();
    const fileCsv = "name,login_url,template\nUploaded Site,https://uploaded.example/login,video_js\n";
    const file = new File([fileCsv], "sites.csv", { type: "text/csv" });
    const input = screen.getByLabelText(/upload csv/i) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    const preview = await screen.findByTestId("bulk-sites-preview");
    await waitFor(() => {
      expect(within(preview).getByText("Uploaded Site")).toBeInTheDocument();
    });
    expect(within(preview).getByText("https://uploaded.example/login")).toBeInTheDocument();
    expect(preview).toHaveTextContent(/1 valid\s*·\s*0 invalid/i);
  });

  it("shows parsed-row preview inside Confirm action dialog and sends zero write requests on Cancel", async () => {
    mount();
    const csv = "name,login_url,template\nCancel Test Site,https://cancel.example/login,video_js";
    fireEvent.change(screen.getByPlaceholderText("name,login_url,template,…"), {
      target: { value: csv },
    });

    fireEvent.click(screen.getByRole("button", { name: "Import sites" }));

    const dialog = await screen.findByRole("dialog");
    const confirmPreview = within(dialog).getByTestId("bulk-sites-confirm-preview");
    expect(within(confirmPreview).getByText("Cancel Test Site")).toBeInTheDocument();
    expect(within(confirmPreview).getByText("https://cancel.example/login")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    const postCalls = calls.filter((c) => c.method === "POST" && c.url.includes("/api/sites/bulk_csv"));
    expect(postCalls).toEqual([]);
  });
});

// Property matrix. The generator mirrors tests/test_appfeat_f026_bulk_sites_preview.py exactly; the committed
// fixture holds what backend csv_bulk.parse_csv makes of each input, and its digest proves the inputs match.
const SENTINEL = "zq7sentinelq7z";
const PUBLIC = ["name", "login_url", "username", "template", "login_template"];
const HEADERS: [string, string, boolean][] = [
  ["standard", "name,login_url,username,password,template,login_template", true],
  ["password-first", "password,login_url,name", true],
  ["unknown-secret-mid", "login_url,api_token,name,password", true],
  ["r4-cr", "name,login_url,password", true],
  ["trailing-secret", "name,login_url,username,client_secret", true],
  ["sentinel-header", `name,login_url,${SENTINEL}_key`, true],
  ["r1-no-login_url", "name,url,username,password", false],
  ["export-no-login_url", "title,username,password,notes", false],
  ["r3-unterminated-header", 'name,login_url,"password', false],
  ["malformed-header", 'name,login_url,"pass"word', false],
];
const QUOTINGS: [string, string][] = [
  ["none", "{s}"],
  ["closed", '"{s}"'],
  ["closed-comma", '"pre,{s}"'],
  ["escaped-quote", '"pre""{s}"'],
  ["multiline", '"pre\n{s}"'],
  ["multiline-blank", '"pre\n\n{s}"'],
  ["multiline-malformed", '"pre\n{s}"x'],
  ["multiline-unterminated", '"pre\n{s}'],
  ["unterminated", '"{s}'],
  ["comma-spill", "pre,{s}"],
  ["stray-quote", 'pre"{s}'],
  ["text-after-quote", '"pre"{s}'],
];
const EOLS = ["lf", "crlf", "cr", "mixed"];
const MIXED = ["\n", "\r\n", "\r"];
const HEADER_ERROR = "header row must include a 'login_url' column";
const KNOWN_COLUMNS = ["name", "login_url", "username", "password", "template", "login_template"];

function cell(column: string, label: string): string {
  if (column === "name" || column === "title") return label;
  if (column === "login_url" || column === "url") {
    return label === "Gamma" ? "" : `https://${label.toLowerCase()}.example/login`;
  }
  const fixed: Record<string, string> = {
    username: label.toLowerCase(),
    template: "video_js",
    login_template: "form_login",
  };
  return fixed[column] ?? "otherpw";
}

function applyEol(text: string, eol: string): string {
  if (eol === "lf") return text;
  if (eol !== "mixed") return text.split("\n").join(eol === "crlf" ? "\r\n" : "\r");
  const parts = text.split("\n");
  return parts[0] + parts.slice(1).map((p, i) => MIXED[i % 3] + p).join("");
}

type Case = { id: string; shape: string; pos: number; firstPos: boolean; qi: number; eol: string; text: string };

function generateCases(): Case[] {
  const out: Case[] = [];
  for (const [shape, header, usable] of HEADERS) {
    const columns = header.split(",");
    const positions = columns.flatMap((c, i) => (!usable || !PUBLIC.includes(c) ? [i] : []));
    for (const pos of positions) {
      QUOTINGS.forEach(([quoting, template], qi) => {
        for (const eol of EOLS) {
          const rows = ["Alpha", "Beta", "Gamma"].map((label) => {
            const cells = columns.map((c) => cell(c, label));
            if (label === "Alpha") cells[pos] = template.replace("{s}", SENTINEL);
            return cells.join(",");
          });
          const preamble = (qi + pos) % 2 ? "﻿# exported sites\n\n" : "";
          const text = applyEol(`${preamble}${header}\n${rows.join("\n")}\n`, eol);
          out.push({ id: `${shape}|${pos}|${quoting}|${eol}`, shape, pos, firstPos: pos === positions[0], qi, eol, text });
        }
      });
    }
  }
  return out;
}

const CASES = generateCases();
const EXPECTED = (fixture as unknown as { cases: Record<string, [number[], number[], number]> }).cases;

// Everything the page shows outside the raw-text textarea (which legitimately holds what the operator typed).
function renderedOutsideTextarea(): string {
  const clone = document.body.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("textarea").forEach((t) => t.remove());
  return `${clone.innerHTML}\n${clone.textContent ?? ""}`.toLowerCase();
}

describe("F026 r5 class rule: an uploaded value reaches the DOM only if strictly parsed and a known non-secret field", () => {
  it("generator reproduces the backend-parity fixture inputs byte for byte (1008 cases)", () => {
    const digest = createHash("sha256");
    for (const c of CASES) digest.update(Buffer.concat([Buffer.from(c.text, "utf8"), Buffer.from([0])]));
    expect(CASES.length).toBe(1008);
    expect(fixture.count).toBe(1008);
    expect(CASES.map((c) => c.id)).toEqual(Object.keys(EXPECTED));
    expect(digest.digest("hex")).toBe(fixture.inputs_sha256);
    expect(fixture.sentinel).toBe(SENTINEL);
  });

  it("sentinel absent from every parsed preview; row and error lines match backend parse_csv (1008 cases)", () => {
    const failures: string[] = [];
    for (const c of CASES) {
      const p = parseBulkSitesCsvPreview(c.text);
      const [validLines, errorLines, headerError] = EXPECTED[c.id];
      if (JSON.stringify(p).toLowerCase().includes(SENTINEL)) failures.push(`${c.id}: SENTINEL LEAK ${JSON.stringify(p)}`);
      if (p.columns.some((col) => !KNOWN_COLUMNS.includes(col))) failures.push(`${c.id}: column outside allow-list ${p.columns}`);
      if (headerError) {
        if (p.headerError !== HEADER_ERROR || p.validCount !== 0) failures.push(`${c.id}: backend header error not mirrored`);
        continue;
      }
      // parse_csv makes a row an error for a missing or an invalid login_url (the shared rule); the preview marks
      // exactly those rows "invalid" (a displaced cell can land in login_url), hidden or not.
      const fr = p.rows.filter((r) => r.status !== "invalid").map((r) => r.line);
      const fe = p.rows.filter((r) => r.status === "invalid").map((r) => r.line);
      if (p.headerError || JSON.stringify([fr, fe]) !== JSON.stringify([validLines, errorLines])) {
        failures.push(`${c.id}: PARITY preview rows=${fr} errors=${fe} backend rows=${validLines} errors=${errorLines}`);
      }
      if (p.validCount + p.invalidCount !== validLines.length + errorLines.length) failures.push(`${c.id}: count mismatch`);
    }
    expect(failures).toEqual([]);
  });

  it.each(HEADERS.map(([shape]) => [shape]))(
    "%s: uploaded file x every quoting x rotated line endings: sentinel absent from inline and Confirm previews, 0 POSTs",
    async (shape) => {
      const failures: string[] = [];
      // Every quoting, each under two line endings (rotated so every ending is mounted); the pure matrix above
      // covers all four endings per quoting.
      const mounted = (x: Case) => x.eol === EOLS[x.qi % 4] || x.eol === EOLS[(x.qi + 2) % 4];
      for (const c of CASES.filter((x) => x.shape === shape && x.firstPos && mounted(x))) {
        const view = mount();
        const expected = parseBulkSitesCsvPreview(c.text);
        const counts = new RegExp(`${expected.validCount} valid\\s*·\\s*${expected.invalidCount} invalid`);
        const file = new File([c.text], "sites.csv", { type: "text/csv" });
        fireEvent.change(screen.getByLabelText(/upload csv/i), { target: { files: [file] } });
        const inline = await screen.findByTestId("bulk-sites-preview");
        await waitFor(() => expect(inline).toHaveTextContent(counts));
        if (renderedOutsideTextarea().includes(SENTINEL)) failures.push(`${c.id}: SENTINEL LEAK inline`);

        fireEvent.click(screen.getByRole("button", { name: "Import sites" }));
        const dialog = await screen.findByRole("dialog");
        const confirm = within(dialog).getByTestId("bulk-sites-confirm-preview");
        expect(confirm).toHaveTextContent(counts);
        if (renderedOutsideTextarea().includes(SENTINEL)) failures.push(`${c.id}: SENTINEL LEAK confirm`);
        fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        view.unmount();
      }
      expect(failures).toEqual([]);
      expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    },
    60_000,
  );

  it("columns metadata names only allow-listed columns; other header cells are a count, never text", async () => {
    mount();
    const csv = `name,login_url,${SENTINEL}_key,api_token\rAlpha,https://a.example,k1,t1`;
    const file = new File([csv], "sites.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText(/upload csv/i), { target: { files: [file] } });
    const preview = await screen.findByTestId("bulk-sites-preview");
    await waitFor(() => expect(preview).toHaveTextContent(/1 valid\s*·\s*0 invalid/));
    expect(preview).toHaveTextContent("columns: name, login_url +2 unrecognised columns");
    expect(renderedOutsideTextarea()).not.toContain(SENTINEL);
    expect(renderedOutsideTextarea()).not.toContain("api_token");
  });

  it("non-empty cells past the header width hide the row: an unquoted comma in a password cannot shift it into template", () => {
    const p = parseBulkSitesCsvPreview(
      `name,login_url,username,password,template\nAlpha,https://a.example,alice,super,${SENTINEL},video_js\nBeta,https://b.example,bob,pw,video_js,,`,
    );
    expect(JSON.stringify(p)).not.toContain(SENTINEL);
    expect(p.rows.map((r) => [r.line, r.name, r.template, r.error])).toEqual([
      [2, "(row)", "", "more cells than header columns (values hidden)"],
      [3, "Beta", "video_js", undefined],
    ]);
    expect([p.validCount, p.invalidCount]).toEqual([2, 0]);
  });
});

// ImportsCenter.bulkSiteRules.cases.json is asserted here AND by tests/test_appfeat_f026_bulk_sites_preview.py against
// csv_bulk (is_valid_http_login_url, clean_name, parse_csv, POST /api/sites/bulk_csv): preview == commit by test.
type Rules = {
  login_url: [string, boolean][];
  names: [string, string, string][];
  title: [string, string][];
};
const RULES = rules as unknown as Rules;

function rulesCsv(): string {
  const q = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const cells = [
    ...RULES.login_url.map(([u], i) => [`url${i}`, u]),
    ...RULES.names.map(([n, u]) => [n, u]),
  ];
  return "name,login_url\n" + cells.map(([n, u]) => `${q(n)},${q(u)}\n`).join("");
}

describe("F026 r6 shared login_url rule and committed names (backend csv_bulk parity table)", () => {
  it("isValidHttpLoginUrl gives every table verdict (javascript:/ftp: invalid, http(s) IPv6 literals valid)", () => {
    expect(RULES.login_url.map(([u]) => [u, isValidHttpLoginUrl(u)])).toEqual(RULES.login_url);
    const verdict = new Map(RULES.login_url);
    expect(["javascript:alert(1)", "data:text/html,x", "ftp://example.com/", "file:///etc/passwd"].map((u) => verdict.get(u))).toEqual([false, false, false, false]);
    expect(verdict.get("https://example.com")).toBe(true);
    expect(["http://[::1]/login", "https://[2001:db8::1]:8443/x"].map((u) => verdict.get(u))).toEqual([true, true]);
  });

  it("cleanBulkSiteName gives the name backend clean_name commits for every table row (all prefixes, str.title)", () => {
    expect(RULES.names.every(([, u]) => isValidHttpLoginUrl(u))).toBe(true);
    expect(RULES.names.map(([n, u]) => [n, u, cleanBulkSiteName(n, u)])).toEqual(RULES.names);
  });

  it("pyTitle is Python str.title on the table's title cases (final sigma, titlecase digraphs, ligatures)", () => {
    expect(RULES.title.map(([s]) => [s, pyTitle(s)])).toEqual(RULES.title);
  });

  it("parsed preview of the table CSV: valid rows and names, invalid rows, exactly as backend parse/commit", () => {
    const p = parseBulkSitesCsvPreview(rulesCsv());
    const valid: [number, string][] = [];
    const invalid: number[] = [];
    RULES.login_url.forEach(([, ok], i) => (ok ? valid.push([i + 2, `url${i}`]) : invalid.push(i + 2)));
    const base = RULES.login_url.length + 2;
    RULES.names.forEach(([, , want], i) => valid.push([base + i, want]));
    expect(p.rows.filter((r) => r.status === "valid").map((r) => [r.line, r.name])).toEqual(valid);
    expect(p.rows.filter((r) => r.status === "invalid").map((r) => [r.line, r.error])).toEqual(
      invalid.map((ln) => [ln, "invalid login_url"]),
    );
    expect([p.validCount, p.invalidCount]).toEqual([valid.length, invalid.length]);
  });
});
