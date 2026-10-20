import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { TakeoverViewer } from "@/components/TakeoverViewer";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
import { WorkflowPage } from "@/components/ui/WorkflowPage";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api-client";
import { downloadSitesTemplate, useBulkSitesCsv } from "@/hooks/useOpsControls";
import type { OkResult } from "@/lib/api-types";

// GUI parity (180, Wave A) — Imports & Captcha Queue. Surfaces five existing
// operator endpoints under the risk model: captcha-queue actions operate on an
// already-pending item (one-step confirm), while the two payload imports are
// gated with a confirm dialog. Surface-only — never reimplements the underlying
// operation; never one-click. The endpoint literals below are exactly what the
// parity inventory's SPA-wiring scanner matches.

type CaptchaItem = {
  url: string;
  site_id?: string;
  captcha_type?: string;
  status?: string;
  title?: string;
  solve_session_id?: string | null;
  // MOD-1 C-6: effective takeover mode + downgrade reason + KasmVNC viewer URL,
  // persisted onto the polled pending state so the cockpit shows what is running.
  mode?: string | null;
  mode_reason?: string | null;
  vnc_url?: string | null;
};
type CaptchaPending = { ok?: boolean; pending?: CaptchaItem[] };
type ImportResult = OkResult & { added?: number; skipped?: number };

// Cut 3 — read-only import-preview shapes (mirror the backend contract).
type UtPreviewItem = {
  id?: string;
  name?: string;
  status: "new" | "changed" | "conflict" | "invalid";
  secrets_omitted: string[];
  // BD-SEC-5: config_defaults keys outside the template allowlist (commands,
  // hooks, paths) -- dropped by the import, never applied to a site.
  refused?: string[];
  error?: string;
};
type UtPreview = {
  ok: boolean;
  mode: "merge" | "replace";
  counts: {
    new: number;
    changed: number;
    conflict: number;
    invalid: number;
    destructive: number;
    secrets_omitted: number;
    refused?: number;
  };
  items: UtPreviewItem[];
  destructive: { id?: string; name?: string }[];
  errors: string[];
};
type MpPreview = {
  ok: boolean;
  site_id: string;
  status?: "new" | "changed";
  config_preview?: Record<string, unknown>;
  secrets_omitted?: string[];
  warnings?: string[];
  errors?: string[];
};

type Pending =
  | { kind: "captchaStart"; url: string; token: "" }
  | { kind: "captchaResolved"; url: string; token: "" }
  | { kind: "captchaDismiss"; url: string; site_id?: string; token: "" }
  | { kind: "templatesImport"; payload: unknown; merge: boolean; token: "" }
  | { kind: "marketplaceImport"; body: Record<string, unknown>; token: "" }
  // T4 (v3.66.207): bulk site creation from pasted CSV — one-step confirm;
  // per-row results render for review after the run.
  | { kind: "bulkSites"; csv: string; token: "" };

const isTyped = (p: Pending): boolean => p.token.length > 0;

const BULK_CSV_COLUMNS = [
  "name",
  "login_url",
  "username",
  "password",
  "template",
  "login_template",
] as const;

export type BulkSitePreviewRow = {
  line: number;
  name: string;
  login_url: string;
  username: string;
  password_set: boolean;
  template: string;
  login_template: string;
  status: "valid" | "invalid";
  error?: string;
};

export type BulkSitesCsvPreview = {
  // Recognised header names only, each one a BULK_CSV_COLUMNS constant (never a raw header cell).
  columns: string[];
  // Header cells outside BULK_CSV_COLUMNS: shown as a count, never as text.
  unrecognisedColumns: number;
  rows: BulkSitePreviewRow[];
  validCount: number;
  invalidCount: number;
  headerError?: string;
};

// Physical line boundaries of Python str.splitlines(): backend parse_csv splits on these before any quote
// logic, so LF, CRLF and bare CR (and the rarer separators) all end a line here exactly as they do there.
const PY_LINE_BREAK = new RegExp("\\r\\n|[\\n\\r\\x0b\\x0c\\x1c\\x1d\\x1e\\x85\\u2028\\u2029]");

function splitLinesPy(text: string): string[] {
  const lines = text.split(PY_LINE_BREAK);
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

// Python str.strip() whitespace (str.isspace), used where backend strips cells and blank/comment lines.
const PY_SPACE = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_STRIP = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, "g");
const pyStrip = (s: string): string => s.replace(PY_STRIP, "");

// One line read the way backend's non-strict csv.reader does: a quote opens only at the start of a field, a
// quote inside an unquoted field is literal, text after a closing quote is appended, an open quote ends at EOL.
function parseCsvLine(raw: string): string[] {
  const out: string[] = [];
  let cur = "";
  let state: "start" | "field" | "quoted" = "start";
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (state === "quoted") {
      if (ch !== '"') {
        cur += ch;
      } else if (raw[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else {
        state = "field";
      }
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
      state = "start";
    } else if (ch === '"' && state === "start") {
      state = "quoted";
    } else {
      cur += ch;
      state = "field";
    }
  }
  out.push(cur);
  return out;
}

// Every record a STRICT CSV reader finds in `text` (a quoted cell may span lines); null when the text is
// not well-formed CSV. Mirrors backend csv_bulk._csv_records (csv.reader strict=True).
function strictCsvRecords(text: string): string[][] | null {
  const records: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let state: "start" | "field" | "quoted" | "closed" = "start";
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (state === "quoted") {
      if (ch !== '"') {
        cur += ch;
      } else if (text[i + 1] === '"') {
        cur += '"';
        i += 1;
      } else {
        state = "closed";
      }
    } else if (ch === ",") {
      row.push(cur);
      cur = "";
      state = "start";
    } else if (ch === "\n") {
      row.push(cur);
      records.push(row);
      row = [];
      cur = "";
      state = "start";
    } else if (state === "closed") {
      return null;
    } else if (ch === '"' && state === "start") {
      state = "quoted";
    } else {
      cur += ch;
      state = "field";
    }
  }
  if (state === "quoted") return null;
  if (state !== "start" || row.length > 0) {
    row.push(cur);
    records.push(row);
  }
  return records;
}

// Backend csv_bulk.derive_name, ported: urllib.parse.urlparse(url).hostname (Python 3.12 keeps the raw host; WHATWG
// URL() would punycode it), every listed prefix stripped in turn, first label, "-" -> " ", then str.title().
const BULK_NAME_PREFIXES = ["www.", "m.", "login.", "members.", "secure.", "auth.", "portal.", "account."];

// ipaddress.IPv4Address(text) accepts (CPython 3.12): four dot-separated decimal octets <= 255, no leading zeros.
function pyIsIPv4(text: string): boolean {
  const octets = text.split(".");
  return octets.length === 4 && octets.every((o) => /^(?:0|[1-9][0-9]{0,2})$/.test(o) && Number(o) <= 255);
}

// ipaddress.IPv6Address(text) accepts (CPython 3.12 _split_scope_id + _ip_int_from_string): an optional non-empty
// "%scope" without a second "%", 3..9 colon parts (an IPv4 tail counts as two), at most one "::", 1-4 hex digit hextets.
function pyIsIPv6(text: string): boolean {
  if (text.includes("/")) return false;
  const pct = text.indexOf("%");
  if (pct >= 0 && (pct === text.length - 1 || text.indexOf("%", pct + 1) >= 0)) return false;
  const parts = (pct >= 0 ? text.slice(0, pct) : text).split(":");
  if (parts.length < 3) return false;
  if (parts[parts.length - 1].includes(".")) {
    if (!pyIsIPv4(parts.pop() as string)) return false;
    parts.push("0", "0");
  }
  if (parts.length > 9) return false;
  let skip = -1;
  for (let i = 1; i < parts.length - 1; i += 1) {
    if (parts[i] !== "") continue;
    if (skip >= 0) return false;
    skip = i;
  }
  let hi = parts.length;
  let lo = 0;
  if (skip >= 0) {
    hi = skip;
    lo = parts.length - skip - 1;
    if (parts[0] === "" && --hi) return false;
    if (parts[parts.length - 1] === "" && --lo) return false;
    if (hi + lo > 7) return false;
  } else if (parts.length !== 8 || parts[0] === "" || parts[7] === "") {
    return false;
  }
  return [...parts.slice(0, hi), ...parts.slice(parts.length - lo)].every((h) => /^[0-9A-Fa-f]{1,4}$/.test(h));
}

// urllib.parse.urlsplit (CPython 3.12) of a str URL: its scheme and SplitResult.hostname ("" for None). Throws where
// urlsplit raises ValueError: unbalanced brackets, a bracketed netloc _check_bracketed_netloc refuses (data before
// "[" or between "]" and ":", a host that is not IPvFuture or ipaddress-valid IPv6), or an NFKC-expanding netloc.
function pyUrlSplit(url: string): { scheme: string; hostname: string } {
  let rest = url.replace(new RegExp("^[\\x00-\\x20]+"), "").replace(/[\t\r\n]/g, "");
  let scheme = "";
  const colon = rest.indexOf(":");
  if (colon > 0 && /^[A-Za-z][A-Za-z0-9+.-]*$/.test(rest.slice(0, colon))) {
    scheme = rest.slice(0, colon).toLowerCase();
    rest = rest.slice(colon + 1);
  }
  if (!rest.startsWith("//")) return { scheme, hostname: "" };
  let end = rest.length;
  for (const ch of "/?#") {
    const at = rest.indexOf(ch, 2);
    if (at >= 0) end = Math.min(end, at);
  }
  const netloc = rest.slice(2, end);
  const hostinfo = netloc.slice(netloc.lastIndexOf("@") + 1);
  const bracketed = hostinfo.startsWith("[");
  const close = hostinfo.indexOf("]");
  const hostname = bracketed ? hostinfo.slice(1, close < 0 ? undefined : close) : hostinfo.split(":")[0];
  if (netloc.includes("[") !== netloc.includes("]")) throw new Error("Invalid IPv6 URL");
  if (netloc.includes("[")) {
    if (hostinfo.includes("[") && !bracketed) throw new Error("Invalid IPv6 URL");
    if (bracketed && close >= 0 && close + 1 < hostinfo.length && hostinfo[close + 1] !== ":") {
      throw new Error("Invalid IPv6 URL");
    }
    const ok = hostname.startsWith("v")
      ? /^v[a-fA-F0-9]+\.[^\n]+$/.test(hostname)
      : !pyIsIPv4(hostname) && pyIsIPv6(hostname);
    if (!ok) throw new Error("Invalid bracketed host");
  }
  if (!/^[\x00-\x7f]*$/.test(netloc)) {
    // urlsplit _checknetloc: characters that NFKC-expand to a URL delimiter (e.g. a/c from one code point).
    const n = netloc.replace(/[@:#?]/g, "");
    const n2 = n.normalize("NFKC");
    if (n !== n2 && /[/?#@:]/.test(n2)) throw new Error("netloc contains invalid characters under NFKC normalization");
  }
  const pct = hostname.indexOf("%");
  return { scheme, hostname: pct < 0 ? hostname.toLowerCase() : hostname.slice(0, pct).toLowerCase() + hostname.slice(pct) };
}

// Python str.title() titlecases a code point unless the one before it is cased (then lowercases it, with the
// Final_Sigma rule for capital sigma). Its titlecase differs from toUpperCase() only at these code points
// (CPython 3.12, Unicode 15): [first, last, title - code point] runs, then multi-character titles.
const PY_TITLE_RUNS: [number, number, number][] = [
  [0x1c4, 0x1c4, 1],
  [0x1c5, 0x1c5, 0],
  [0x1c6, 0x1c6, -1],
  [0x1c7, 0x1c7, 1],
  [0x1c8, 0x1c8, 0],
  [0x1c9, 0x1c9, -1],
  [0x1ca, 0x1ca, 1],
  [0x1cb, 0x1cb, 0],
  [0x1cc, 0x1cc, -1],
  [0x1f1, 0x1f1, 1],
  [0x1f2, 0x1f2, 0],
  [0x1f3, 0x1f3, -1],
  [0x10d0, 0x10fa, 0],
  [0x10fd, 0x10ff, 0],
  [0x1f80, 0x1f87, 8],
  [0x1f88, 0x1f8f, 0],
  [0x1f90, 0x1f97, 8],
  [0x1f98, 0x1f9f, 0],
  [0x1fa0, 0x1fa7, 8],
  [0x1fa8, 0x1faf, 0],
  [0x1fb3, 0x1fb3, 9],
  [0x1fbc, 0x1fbc, 0],
  [0x1fc3, 0x1fc3, 9],
  [0x1fcc, 0x1fcc, 0],
  [0x1ff3, 0x1ff3, 9],
  [0x1ffc, 0x1ffc, 0],
];
const PY_TITLE_MULTI: Record<number, number[]> = {
  0xdf: [0x53, 0x73],
  0x587: [0x535, 0x582],
  0x1fb2: [0x1fba, 0x345],
  0x1fb4: [0x386, 0x345],
  0x1fb7: [0x391, 0x342, 0x345],
  0x1fc2: [0x1fca, 0x345],
  0x1fc4: [0x389, 0x345],
  0x1fc7: [0x397, 0x342, 0x345],
  0x1ff2: [0x1ffa, 0x345],
  0x1ff4: [0x38f, 0x345],
  0x1ff7: [0x3a9, 0x342, 0x345],
  0xfb00: [0x46, 0x66],
  0xfb01: [0x46, 0x69],
  0xfb02: [0x46, 0x6c],
  0xfb03: [0x46, 0x66, 0x69],
  0xfb04: [0x46, 0x66, 0x6c],
  0xfb05: [0x53, 0x74],
  0xfb06: [0x53, 0x74],
  0xfb13: [0x544, 0x576],
  0xfb14: [0x544, 0x565],
  0xfb15: [0x544, 0x56b],
  0xfb16: [0x54e, 0x576],
  0xfb17: [0x544, 0x56d],
};
const PY_CASED = /\p{Cased}/u;
const PY_CASE_IGNORABLE = /\p{Case_Ignorable}/u;
const CAPITAL_SIGMA = String.fromCodePoint(0x3a3);

function pyTitleCodePoint(c: string): string {
  const cp = c.codePointAt(0) ?? 0;
  const multi = PY_TITLE_MULTI[cp];
  if (multi) return String.fromCodePoint(...multi);
  for (const [first, last, delta] of PY_TITLE_RUNS) {
    if (cp >= first && cp <= last) return String.fromCodePoint(cp + delta);
  }
  return c.toUpperCase();
}

function pyLowerAt(cps: string[], i: number): string {
  if (cps[i] !== CAPITAL_SIGMA) return cps[i].toLowerCase();
  let j = i - 1;
  while (j >= 0 && PY_CASE_IGNORABLE.test(cps[j])) j -= 1;
  let finalSigma = j >= 0 && PY_CASED.test(cps[j]);
  if (finalSigma && i + 1 < cps.length) {
    j = i + 1;
    while (j < cps.length && PY_CASE_IGNORABLE.test(cps[j])) j += 1;
    finalSigma = j === cps.length || !PY_CASED.test(cps[j]);
  }
  return String.fromCodePoint(finalSigma ? 0x3c2 : 0x3c3);
}

export function pyTitle(text: string): string {
  const cps = Array.from(text);
  let out = "";
  let previousIsCased = false;
  cps.forEach((c, i) => {
    out += previousIsCased ? pyLowerAt(cps, i) : pyTitleCodePoint(c);
    previousIsCased = PY_CASED.test(c);
  });
  return out;
}

export function deriveBulkSiteName(loginUrl: string): string {
  let host: string;
  try {
    host = pyUrlSplit(loginUrl.includes("://") ? loginUrl : `http://${loginUrl}`).hostname;
  } catch {
    host = "";
  }
  host = host.toLowerCase();
  for (const prefix of BULK_NAME_PREFIXES) {
    if (host.startsWith(prefix)) host = host.slice(prefix.length);
  }
  const base = host ? host.split(".")[0] : "";
  return pyTitle(base.replace(/-/g, " ")) || "Imported Site";
}

export function cleanBulkSiteName(rawName: string, loginUrl: string): string {
  const trimmed = pyStrip(rawName || "");
  if (!trimmed || trimmed.includes("://") || trimmed.toLowerCase().startsWith("www.")) {
    return loginUrl ? deriveBulkSiteName(loginUrl) : "(unnamed)";
  }
  return trimmed;
}

// The one login_url rule, backend csv_bulk.is_valid_http_login_url through the urlsplit port above (both suites assert
// it against ImportsCenter.bulkSiteRules.cases.json): http or https scheme and a non-empty hostname; a URL urlsplit
// rejects is invalid. A row this marks invalid is one backend parse rejects as "invalid login_url".
export function isValidHttpLoginUrl(rawUrl: string): boolean {
  try {
    const { scheme, hostname } = pyUrlSplit(rawUrl);
    return (scheme === "http" || scheme === "https") && hostname !== "";
  } catch {
    return false;
  }
}

export function parseBulkSitesCsvPreview(rawText: string): BulkSitesCsvPreview {
  const empty: BulkSitesCsvPreview = {
    columns: [],
    unrecognisedColumns: 0,
    rows: [],
    validCount: 0,
    invalidCount: 0,
  };
  const cleanText = rawText.startsWith("\ufeff") ? rawText.slice(1) : rawText;
  if (!pyStrip(cleanText)) return empty;
  const lines = splitLinesPy(cleanText);
  // CLASS RULE: a value from the uploaded text reaches the DOM only when (1) its record came from a strict CSV
  // parse of backend-identical lines, (2) the header record did too and accounts for every non-empty cell, and
  // (3) its column is a known non-secret BULK_CSV_COLUMNS field. Header cells are never echoed: recognised names
  // are emitted from the constant list, anything else is a count. password is reduced to password_set.
  // A record failing (1)/(2) is still counted, read leniently as backend parse_csv does, but `hidden` keeps its
  // values off screen: an unterminated or malformed quote may have swallowed part of a multiline password.
  const records: { line: number; vals: string[]; hidden?: string }[] = [];
  const addLines = (block: { line: number; raw: string }[], hidden?: string) => {
    for (const { line, raw } of block) {
      const s = pyStrip(raw);
      if (!s || s.startsWith("#")) continue;
      const strict = hidden ? null : strictCsvRecords(raw);
      if (strict && strict.length === 1) records.push({ line, vals: strict[0] });
      else records.push({ line, vals: parseCsvLine(raw), hidden: hidden ?? "malformed quoted field" });
    }
  };
  // A quoted cell may hold line breaks (multiline passwords): gather lines until their quotes balance; exactly
  // one well-formed record is one row under its first line, anything else is read line by line (backend parse_csv).
  let pending: { line: number; raw: string }[] = [];
  let quoteOpen = false;
  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    if (pending.length === 0) {
      const s = pyStrip(raw);
      if (!s || s.startsWith("#")) continue;
    }
    pending.push({ line: i + 1, raw });
    if ((raw.split('"').length - 1) % 2 === 1) quoteOpen = !quoteOpen;
    if (quoteOpen) continue;
    const found =
      pending.length > 1 ? strictCsvRecords(pending.map((p) => p.raw).join("\n")) : null;
    if (found && found.length === 1) records.push({ line: pending[0].line, vals: found[0] });
    else addLines(pending, pending.length > 1 ? "malformed quoted field" : undefined);
    pending = [];
  }
  addLines(pending, "unterminated quote");
  if (records.length === 0) return empty;
  const header = records[0];
  const headerLower = header.vals.map((v) => pyStrip(v).toLowerCase());
  const known = new Set<string>(BULK_CSV_COLUMNS);
  const columns = header.hidden
    ? []
    : BULK_CSV_COLUMNS.filter((c) => headerLower.includes(c)).map((c) => c as string);
  const unrecognisedColumns = header.hidden
    ? 0
    : headerLower.filter((c) => c.length > 0 && !known.has(c)).length;
  if (!headerLower.includes("login_url")) {
    const errMsg = "header row must include a 'login_url' column";
    // No positional mapping: with an unknown header any cell may be a password (password-manager exports).
    const rows: BulkSitePreviewRow[] = records.slice(1).map((rec) => ({
      line: rec.line,
      name: "(row)",
      login_url: "",
      username: "",
      password_set: false,
      template: "",
      login_template: "",
      status: "invalid",
      error: errMsg,
    }));
    return {
      columns,
      unrecognisedColumns,
      rows,
      validCount: 0,
      invalidCount: rows.length,
      headerError: errMsg,
    };
  }

  const rows: BulkSitePreviewRow[] = [];
  let validCount = 0;
  let invalidCount = 0;
  for (const rec of records.slice(1)) {
    if (!rec.vals.some((v) => pyStrip(v).length > 0)) continue;
    const mapped: Record<string, string> = {};
    for (const col of BULK_CSV_COLUMNS) mapped[col] = "";
    headerLower.forEach((col, idx) => {
      if (idx < rec.vals.length && Object.prototype.hasOwnProperty.call(mapped, col)) {
        mapped[col] = pyStrip(rec.vals[idx]);
      }
    });
    const loginUrl = mapped.login_url;
    const name = cleanBulkSiteName(mapped.name, loginUrl);
    let status: "valid" | "invalid" = "valid";
    let error: string | undefined;
    if (!loginUrl) {
      status = "invalid";
      error = "missing login_url";
    } else if (!isValidHttpLoginUrl(loginUrl)) {
      status = "invalid";
      error = "invalid login_url";
    }
    if (status === "valid") validCount += 1;
    else invalidCount += 1;
    // Non-empty cells past the header's width mean field boundaries are not the header's (e.g. an unquoted comma
    // inside a password shifts its tail into the next column); backend drops them, the preview hides the row.
    const hidden =
      rec.hidden ??
      (header.hidden
        ? "malformed header"
        : rec.vals.slice(headerLower.length).some((v) => pyStrip(v).length > 0)
          ? "more cells than header columns"
          : undefined);
    if (hidden) {
      const note = `${hidden} (values hidden)`;
      rows.push({
        line: rec.line,
        name: "(row)",
        login_url: "",
        username: "",
        password_set: false,
        template: "",
        login_template: "",
        status,
        error: error ? `${error}; ${note}` : note,
      });
      continue;
    }
    rows.push({
      line: rec.line,
      name,
      login_url: loginUrl,
      username: mapped.username,
      password_set: Boolean(mapped.password),
      template: mapped.template,
      login_template: mapped.login_template,
      status,
      error,
    });
  }
  return { columns, unrecognisedColumns, rows, validCount, invalidCount };
}

function BulkSitesPreviewPanel({
  preview,
  testId,
}: {
  preview: BulkSitesCsvPreview;
  testId: string;
}) {
  return (
    <div
      data-testid={testId}
      className="mt-3 rounded border border-border bg-black/20 p-3 text-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold text-ink-1">
          Parsed CSV preview: {preview.validCount} valid · {preview.invalidCount} invalid
        </span>
        {(preview.columns.length > 0 || preview.unrecognisedColumns > 0) && (
          <span className="text-ink-3">
            columns: {preview.columns.join(", ")}
            {preview.unrecognisedColumns > 0 &&
              ` +${preview.unrecognisedColumns} unrecognised column${preview.unrecognisedColumns === 1 ? "" : "s"}`}
          </span>
        )}
      </div>
      {preview.headerError && (
        <p className="mt-1 text-red-300">Invalid header: {preview.headerError}</p>
      )}
      {preview.rows.length === 0 ? (
        <p className="mt-2 text-ink-3">No data rows parsed below header.</p>
      ) : (
        <table className="bd-table mt-2 w-full text-xs">
          <thead>
            <tr className="border-b border-border/60 text-left text-ink-3">
              <th className="py-1 pr-2">Line</th>
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Login URL</th>
              <th className="py-1 pr-2">Username</th>
              <th className="py-1 pr-2">Template</th>
              <th className="py-1 pr-2">Login tpl</th>
              <th className="py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((r, i) => (
              <tr
                key={`${r.line}-${i}`}
                data-status={r.status}
                className="border-b border-border/40"
              >
                <td className="py-1 pr-2 text-ink-3">line {r.line}</td>
                <td className="py-1 pr-2 font-medium text-foreground">{r.name}</td>
                <td className="py-1 pr-2 font-mono text-emerald-200/80">
                  {r.login_url || "—"}
                </td>
                <td className="py-1 pr-2 text-ink-2">
                  {r.username || "—"}
                  {r.password_set ? " (password set)" : ""}
                </td>
                <td className="py-1 pr-2 font-mono text-ink-2">{r.template || "—"}</td>
                <td className="py-1 pr-2 font-mono text-ink-2">
                  {r.login_template || "—"}
                </td>
                <td
                  className={
                    r.status === "invalid" ? "py-1 text-red-300" : "py-1 text-emerald-300"
                  }
                >
                  {r.status}
                  {r.error ? ` — ${r.error}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// Cut 3 — read-only preview renderers (shown in the WorkflowPage `plan` slot).
function UtPreviewPanel({ p }: { p: UtPreview }) {
  if (!p.ok) {
    return (
      <Card className="p-3">
        <h3 className="text-sm font-semibold text-red">Templates preview failed</h3>
        <ul className="mt-1 text-xs text-ink-3">
          {(p.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      </Card>
    );
  }
  const c = p.counts;
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold text-ink-1">
        Templates import preview ({p.mode})
      </h3>
      <p className="mt-1 text-xs text-ink-3">
        {c.new} new · {c.changed} changed · {c.conflict} conflict · {c.invalid} invalid
        {c.destructive ? ` · ${c.destructive} removed` : ""}
        {c.secrets_omitted ? ` · ${c.secrets_omitted} with secrets omitted` : ""}
        {c.refused ? ` · ${c.refused} with REFUSED settings` : ""}
      </p>
      {p.items.length > 0 && (
        <ul className="mt-2 max-h-40 divide-y divide-border overflow-auto rounded border border-border bg-black/20 text-xs">
          {p.items.map((it, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-2 px-2 py-1">
              <span className="font-medium text-foreground">{it.name ?? it.id ?? "(unnamed)"}</span>
              <span className="text-ink-3">{it.status}</span>
              {it.secrets_omitted.length > 0 && (
                <span className="text-amber-300">secrets omitted: {it.secrets_omitted.join(", ")}</span>
              )}
              {(it.refused ?? []).length > 0 && (
                <span className="text-red-300">REFUSED (never applied): {(it.refused ?? []).join(", ")}</span>
              )}
              {it.error && <span className="text-red-300">{it.error}</span>}
            </li>
          ))}
        </ul>
      )}
      {p.destructive.length > 0 && (
        <p className="mt-2 text-xs text-red-300">
          Replace would remove: {p.destructive.map((d) => d.name ?? d.id).join(", ")}
        </p>
      )}
      {p.errors.length > 0 && (
        <ul className="mt-2 text-xs text-amber-300">
          {p.errors.map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
    </Card>
  );
}

function MpPreviewPanel({ p }: { p: MpPreview }) {
  if (!p.ok) {
    return (
      <Card className="p-3">
        <h3 className="text-sm font-semibold text-red">Bundle preview failed</h3>
        <ul className="mt-1 text-xs text-ink-3">
          {(p.errors ?? []).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      </Card>
    );
  }
  return (
    <Card className="p-3">
      <h3 className="text-sm font-semibold text-ink-1">
        Bundle preview · {p.site_id} ({p.status})
      </h3>
      {(p.secrets_omitted ?? []).length > 0 && (
        <p className="mt-1 text-xs text-amber-300">
          secrets omitted: {(p.secrets_omitted ?? []).join(", ")}
        </p>
      )}
      {(p.warnings ?? []).length > 0 && (
        <ul className="mt-1 text-xs text-amber-200">
          {(p.warnings ?? []).map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
        {JSON.stringify(p.config_preview ?? {}, null, 2)}
      </pre>
    </Card>
  );
}

export function ImportsCenter() {
  const qc = useQueryClient();
  const [utJson, setUtJson] = useState("");
  const [utMerge, setUtMerge] = useState(true);
  const [mpBundle, setMpBundle] = useState("");
  const [mpTarget, setMpTarget] = useState("");
  const [mpVerify, setMpVerify] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  // Cut 3 — read-only preview state (shown before an apply).
  const [utPreview, setUtPreview] = useState<UtPreview | null>(null);
  const [mpPreview, setMpPreview] = useState<MpPreview | null>(null);
  // T4 (v3.66.207): bulk site import.
  const bulkSites = useBulkSitesCsv();
  const [bulkCsv, setBulkCsv] = useState("");

  const captcha = useQuery<CaptchaPending, Error>({
    queryKey: ["captcha", "pending"],
    queryFn: () => apiGet<CaptchaPending>("/api/captcha/pending"),
    refetchInterval: 5000,
  });

  const okToast = (msg: string) => (res: OkResult) =>
    res.ok === false ? toast.error(res.error || "failed") : toast.success(msg);

  const invalidateCaptcha = () => qc.invalidateQueries({ queryKey: ["captcha", "pending"] });

  const captchaStart = useMutation<OkResult, Error, string>({
    mutationFn: (url) => apiPost<OkResult>("/api/captcha/start_solve", { url }),
    onSuccess: (res) => {
      okToast("Solve session started")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });
  const captchaResolved = useMutation<OkResult, Error, string>({
    mutationFn: (url) => apiPost<OkResult>("/api/captcha/resolved", { url }),
    onSuccess: (res) => {
      okToast("Marked resolved")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });
  const captchaDismiss = useMutation<OkResult, Error, { url: string; site_id?: string }>({
    // BH1-46: dismiss is scoped to the owning site; a bare {url} is refused (409).
    mutationFn: ({ url, site_id }) => apiPost<OkResult>("/api/captcha/dismiss", { url, site_id }),
    onSuccess: (res) => {
      okToast("Dismissed")(res);
      invalidateCaptcha();
    },
    onError: (e) => toast.error(e.message),
  });

  const templatesImport = useMutation<ImportResult, Error, { payload: unknown; merge: boolean }>({
    mutationFn: ({ payload, merge }) =>
      apiPost<ImportResult>(`/api/user_templates/import?merge=${merge ? "1" : "0"}`, payload),
    onSuccess: (res) =>
      res.ok === false
        ? toast.error(res.error || "import failed")
        : toast.success(
            `Templates imported${res.added != null ? ` (added ${res.added}, skipped ${res.skipped})` : ""}`,
          ),
    onError: (e) => toast.error(e.message),
  });
  const marketplaceImport = useMutation<OkResult, Error, Record<string, unknown>>({
    mutationFn: (body) => apiPost<OkResult>("/api/marketplace/import", body),
    onSuccess: okToast("Bundle imported"),
    onError: (e) => toast.error(e.message),
  });

  // Cut 3 — read-only PREVIEW-before-apply. Classifies what an import would do
  // (new/changed/conflict/destructive/secrets-omitted) without writing.
  const templatesPreview = useMutation<UtPreview, Error, { payload: unknown; merge: boolean }>({
    mutationFn: ({ payload, merge }) =>
      apiPost<UtPreview>(`/api/user_templates/import/preview?merge=${merge ? "1" : "0"}`, payload),
    onSuccess: (r) => {
      setUtPreview(r);
      if (!r.ok) toast.error(r.errors?.[0] || "preview failed");
      else toast.success("Preview ready — review before importing");
    },
    onError: (e) => toast.error(e.message),
  });
  const marketplacePreview = useMutation<MpPreview, Error, Record<string, unknown>>({
    mutationFn: (body) => apiPost<MpPreview>("/api/marketplace/import/preview", body),
    onSuccess: (r) => {
      setMpPreview(r);
      if (!r.ok) toast.error(r.errors?.[0] || "preview failed");
      else toast.success("Bundle preview ready");
    },
    onError: (e) => toast.error(e.message),
  });

  const busy =
    captchaStart.isPending ||
    captchaResolved.isPending ||
    captchaDismiss.isPending ||
    templatesImport.isPending ||
    marketplaceImport.isPending ||
    templatesPreview.isPending ||
    marketplacePreview.isPending ||
    bulkSites.isPending;

  const arm = (p: Pending) => {
    setPending(p);
  };

  const armTemplatesImport = () => {
    const raw = utJson.trim();
    if (!raw) {
      toast.error("Paste an exported templates payload first");
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    arm({ kind: "templatesImport", payload, merge: utMerge, token: "" });
  };

  const armMarketplaceImport = () => {
    const raw = mpBundle.trim();
    if (!raw) {
      toast.error("Paste a marketplace bundle first");
      return;
    }
    let bundle: unknown;
    try {
      bundle = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    const body: Record<string, unknown> = { bundle };
    if (mpTarget.trim()) body.target_site_id = mpTarget.trim();
    if (mpVerify.trim()) body.verify_with = mpVerify.trim();
    arm({ kind: "marketplaceImport", body, token: "" });
  };

  // Cut 3 — preview handlers (parse the same payload, hit the read-only route).
  const previewTemplatesImport = () => {
    const raw = utJson.trim();
    if (!raw) {
      toast.error("Paste an exported templates payload first");
      return;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setUtPreview(null);
    templatesPreview.mutate({ payload, merge: utMerge });
  };

  const previewMarketplaceImport = () => {
    const raw = mpBundle.trim();
    if (!raw) {
      toast.error("Paste a marketplace bundle first");
      return;
    }
    let bundle: unknown;
    try {
      bundle = JSON.parse(raw);
    } catch (e) {
      toast.error(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    const body: Record<string, unknown> = { bundle };
    if (mpTarget.trim()) body.target_site_id = mpTarget.trim();
    if (mpVerify.trim()) body.verify_with = mpVerify.trim();
    setMpPreview(null);
    marketplacePreview.mutate(body);
  };

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "captchaStart":
        captchaStart.mutate(pending.url);
        break;
      case "captchaResolved":
        captchaResolved.mutate(pending.url);
        break;
      case "captchaDismiss":
        captchaDismiss.mutate({ url: pending.url, site_id: pending.site_id });
        break;
      case "templatesImport":
        templatesImport.mutate({ payload: pending.payload, merge: pending.merge });
        break;
      case "marketplaceImport":
        marketplaceImport.mutate(pending.body);
        break;
      case "bulkSites":
        bulkSites.mutate(
          { csv: pending.csv },
          {
            onSuccess: (r) =>
              r.error
                ? toast.error(r.error)
                : toast.success(`Created ${r.created ?? 0} sites — see per-row results`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
    }
    setPending(null);
  };

  const items = captcha.data?.pending ?? [];

  const confirmLabel = (p: Pending): string => {
    switch (p.kind) {
      case "captchaStart":
        return `Start a manual solve session for:\n  ${p.url}`;
      case "captchaResolved":
        return `Mark this captcha resolved and signal the worker to retry?\n  ${p.url}`;
      case "captchaDismiss":
        return `Dismiss (give up on) this captcha?\n  ${p.url}`;
      case "templatesImport":
        return p.merge
          ? "Import user templates (merge — keeps existing)."
          : "REPLACE ALL user templates with the imported set. This is destructive and cannot be undone.";
      case "marketplaceImport":
        return "Import this marketplace bundle into the install.";
      case "bulkSites":
        return `Bulk-create sites from the pasted CSV (${p.csv.split("\n").filter((l) => l.trim()).length} non-empty lines). Per-row results follow.`;
    }
  };

  return (
    <AppShell title="Imports · Captcha Queue" subtitle="Act on pending captchas · import templates / marketplace bundles">
      <GatedWriteBanner level="chip">
        Imports and captcha-queue actions confirm via dialog; nothing
        confirm before firing — nothing fires on a single click; every request is audited by the
        underlying endpoint. <b>Needs operator click-through validation.</b>
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mt-3">
        Act on pending captcha challenges and import data into BulkDownloader —
        user templates, a marketplace bundle, or a bulk site list from CSV/XLSX.
        Imports confirm via dialog; replacing all templates is destructive and is
        flagged inline where you choose it.
      </Callout>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Captcha queue</h2>
        <p className="mb-2 text-xs text-ink-3">
          Pending challenges awaiting a manual solve. Acting on a queued item is not destructive to stored data.
        </p>
        {captcha.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length === 0 ? (
          <p className="py-6 text-center text-sm text-ink-3">No pending captchas.</p>
        ) : (
          <table className="bd-table w-full text-sm">
            <tbody>
              {items.map((p) => (
                <tr key={p.url} className="border-b border-border/40">
                  <td className="py-1.5 pr-3 align-top">
                    <div className="text-ink-2">
                      {p.captcha_type || "?"} · {p.status || ""}
                    </div>
                    <div className="text-xs text-ink-3">
                      {p.site_id || ""}
                      {p.title ? ` · ${p.title}` : ""}
                    </div>
                    <div className="break-all font-mono text-xs text-emerald-200/70">{p.url}</div>
                    {p.status === "solving" && p.solve_session_id ? (
                      <TakeoverViewer
                        sid={p.solve_session_id}
                        mode={p.mode}
                        vncUrl={p.vnc_url}
                        reason={p.mode_reason}
                      />
                    ) : null}
                  </td>
                  <td className="py-1.5 text-right align-top">
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaStart", url: p.url, token: "" })}
                      >
                        Start solve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaResolved", url: p.url, token: "" })}
                      >
                        Resolved
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => arm({ kind: "captchaDismiss", url: p.url, site_id: p.site_id, token: "" })}
                      >
                        Dismiss
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <WorkflowPage
        inputs={
          <>
      <Card className="mt-4 p-4">
        <h2 className="section-head">Import user templates</h2>
        <p className="mb-2 text-xs text-ink-3">
          Paste a payload previously produced by the user-templates export. Merge keeps existing
          templates; Replace is destructive (replaces all user templates).
        </p>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder='{"templates":[...]}'
          value={utJson}
          onChange={(e) => setUtJson(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={utMerge} onChange={(e) => setUtMerge(e.target.checked)} />
            merge (uncheck = replace all, destructive)
          </label>
          <Button variant="outline" disabled={busy} onClick={previewTemplatesImport}>
            Preview
          </Button>
          <Button variant="outline" disabled={busy} onClick={armTemplatesImport}>
            Import templates
          </Button>
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Import marketplace bundle</h2>
        <p className="mb-2 text-xs text-ink-3">
          Paste a marketplace bundle (JSON object). Optional target site id and verify secret.
        </p>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder='{"name":"...","config":{...}}'
          value={mpBundle}
          onChange={(e) => setMpBundle(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="target_site_id (optional)"
            value={mpTarget}
            onChange={(e) => setMpTarget(e.target.value)}
          />
          <Input
            className="max-w-[220px]"
            placeholder="verify_with (optional)"
            value={mpVerify}
            onChange={(e) => setMpVerify(e.target.value)}
          />
          <Button variant="outline" disabled={busy} onClick={previewMarketplaceImport}>
            Preview
          </Button>
          <Button variant="outline" disabled={busy} onClick={armMarketplaceImport}>
            Import bundle
          </Button>
        </div>
      </Card>

      {/* ── T4 (v3.66.207): bulk site import ─────────────────────────── */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Bulk site import</h2>
        <p className="mb-2 text-xs text-ink-3">
          Download a template, fill it, upload or paste the CSV back here. Parsed rows and invalid
          entries are previewed before commit; results come back per-row after import.
        </p>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={() => downloadSitesTemplate("csv")}>
            CSV template
          </Button>
          <Button variant="outline" onClick={() => downloadSitesTemplate("xlsx")}>
            XLSX template
          </Button>
          <label className="inline-flex cursor-pointer items-center rounded-md border border-border px-3 py-1.5 text-xs text-ink-2 hover:bg-white/5">
            Upload CSV
            <input
              type="file"
              accept=".csv,.txt,text/csv"
              aria-label="Upload CSV"
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (typeof file.text === "function") {
                  void file.text().then((t) => setBulkCsv(t));
                } else {
                  const reader = new FileReader();
                  reader.onload = () => setBulkCsv(String(reader.result ?? ""));
                  reader.readAsText(file);
                }
              }}
            />
          </label>
        </div>
        <textarea
          className="min-h-[110px] w-full rounded-md border border-border bg-black/40 p-2 font-mono text-xs text-foreground"
          placeholder="name,login_url,template,…"
          value={bulkCsv}
          onChange={(e) => setBulkCsv(e.target.value)}
        />
        {bulkCsv.trim() && (
          <BulkSitesPreviewPanel
            preview={parseBulkSitesCsvPreview(bulkCsv)}
            testId="bulk-sites-preview"
          />
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            disabled={busy || !bulkCsv.trim()}
            onClick={() => arm({ kind: "bulkSites", csv: bulkCsv, token: "" })}
          >
            Import sites
          </Button>
        </div>
        {bulkSites.data?.results && (
          <table className="bd-table mt-3 w-full text-xs">
            <tbody>
              {bulkSites.data.results.map((r, i) => (
                <tr key={i} className="border-b border-border/40">
                  <td className="py-1 pr-2 text-ink-3">line {r.line ?? "—"}</td>
                  <td className="py-1 pr-2">{r.name ?? ""}</td>
                  <td
                    className={
                      r.status === "error" ? "py-1 text-red-300" : "py-1 text-emerald-300"
                    }
                  >
                    {r.status}
                    {r.error ? ` — ${r.error}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
          </>
        }
        plan={
          utPreview || mpPreview ? (
            <div className="space-y-3">
              {utPreview && <UtPreviewPanel p={utPreview} />}
              {mpPreview && <MpPreviewPanel p={mpPreview} />}
            </div>
          ) : undefined
        }
      />

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending && isTyped(pending)
                ? "This is destructive and cannot be undone. Proceed?"
                : "Confirm this operation."}
            </DialogDescription>
          </DialogHeader>
          {pending && (
            <p className="whitespace-pre-wrap text-sm text-amber-200">{confirmLabel(pending)}</p>
          )}
          {pending && pending.kind === "bulkSites" && (
            <BulkSitesPreviewPanel
              preview={parseBulkSitesCsvPreview(pending.csv)}
              testId="bulk-sites-confirm-preview"
            />
          )}
          {pending && isTyped(pending) && (
            <p className="font-mono text-xs text-amber-300">{pending.token}</p>
          )}
          <DialogFooter>
            {pending && isTyped(pending) ? (
              <>
                <Button autoFocus variant="default" onClick={() => setPending(null)}>
                  No, cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Yes, proceed
                </Button>
              </>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant="destructive" onClick={confirmRun}>
                  Confirm
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
