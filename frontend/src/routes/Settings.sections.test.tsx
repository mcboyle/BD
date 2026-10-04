import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Settings } from "./Settings";
import { SETTINGS_SECTIONS } from "@/lib/settingsSchema";

// O1826 BRIEF-28 (M222): the page's nav + changed-section markers must be
// driven by lib/settingsSchema.ts SETTINGS_SECTIONS (the single source shared
// with the command palette). A local copy in Settings.tsx omitted "Automation"
// and "Store metadata (raw / advanced)", so neither appeared in the nav and an
// unsaved Automation edit marked no section.

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/settings"]}>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function navButtons(): Promise<HTMLElement[]> {
  const nav = await screen.findByRole("navigation", { name: "Settings sections" });
  return within(nav).getAllByRole("button");
}

async function navButton(label: string): Promise<HTMLElement | undefined> {
  return (await navButtons()).find((b) => b.textContent === label);
}

function toggleRow(label: string) {
  const row = screen.getByText(label).closest("[data-srow]");
  const sw = row?.querySelector<HTMLElement>('[role="switch"]');
  if (!sw) throw new Error(`no switch in row ${label}`);
  fireEvent.click(sw);
}

// Pre-fix nav ids (Settings.tsx hand list at BASE). Chip clicks, bookmarked
// #anchors and ValidationSummary jumps resolve through these; they must not move.
const LEGACY_IDS: Record<string, string> = {
  Downloads: "downloads",
  "AI assist": "ai-assist",
  Network: "network",
  "Queue housekeeping": "queue-housekeeping",
  Capture: "capture",
  Diagnostics: "diagnostics",
  "Session keep-alive": "session-keep-alive",
  System: "system",
  "Tools & operations": "tools-operations",
  "Supervisor throttle": "supervisor-throttle",
  Browser: "browser",
  "Challenge handling": "challenge-handling",
  Advanced: "advanced",
  "Security & access": "security-access",
  "Environment (restart required)": "environment-restart-required",
  "Import / Export": "import-export",
};
const EXISTING = Object.keys(LEGACY_IDS);

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/global_config")) {
        return Promise.resolve(jsonResponse({}));
      }
      return new Promise<Response>(() => {});
    }),
  );
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Settings — sections single source (O1826 BRIEF-28)", () => {
  it("nav lists every schema section, in schema order", async () => {
    mount();
    await screen.findByLabelText("Filter settings");
    const labels = (await navButtons()).map((b) => b.textContent);
    expect(labels).toEqual([...SETTINGS_SECTIONS]);
    expect(labels).toContain("Automation");
    expect(labels).toContain("Store metadata (raw / advanced)");
  });

  it("every nav entry scrolls to its own section; legacy ids unchanged", async () => {
    mount();
    await screen.findByLabelText("Filter settings");
    const btns = await navButtons();
    expect(btns).toHaveLength(SETTINGS_SECTIONS.length);
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const bad: string[] = [];
    for (const b of btns) {
      const before = scroll.mock.instances.length;
      fireEvent.click(b);
      const label = b.textContent ?? "";
      // A missing anchor makes jumpToSection a silent no-op (no scroll call).
      const el = scroll.mock.instances.slice(before)[0] as HTMLElement | undefined;
      const heading = el?.querySelector(".eyebrow")?.textContent;
      if (el?.tagName !== "SECTION" || !el.hasAttribute("data-section") || heading !== label) {
        bad.push(`${label} -> ${el ? `${el.tagName}#${el.id} "${heading}"` : "no scroll target"}`);
      }
      if (label in LEGACY_IDS && el?.id !== LEGACY_IDS[label]) {
        bad.push(`${label} id ${el?.id ?? "NONE"} != legacy ${LEGACY_IDS[label]}`);
      }
    }
    expect(bad).toEqual([]);
    expect(scroll).toHaveBeenCalledTimes(SETTINGS_SECTIONS.length);
  });

  it("an unsaved Automation edit marks the Automation nav entry", async () => {
    mount();
    await screen.findByLabelText("Filter settings");
    const trigger = document.querySelector<HTMLElement>(
      "section#automation button[aria-expanded]",
    );
    expect(trigger).not.toBeNull();
    fireEvent.click(trigger!);
    toggleRow("Drift sweep (L1)");
    await waitFor(async () => {
      const auto = await navButton("Automation");
      expect(auto).toBeDefined();
      expect(
        within(auto!).getByTestId("settingsnav-changed-marker"),
      ).toBeInTheDocument();
    });
  });

  it("existing sections keep their nav entries and markers (negative control)", async () => {
    mount();
    await screen.findByLabelText("Filter settings");
    const labels = (await navButtons()).map((b) => b.textContent);
    for (const s of EXISTING) expect(labels).toContain(s);
    expect(screen.queryAllByTestId("settingsnav-changed-marker")).toHaveLength(0);
    toggleRow("Archive processed files");
    await waitFor(async () => {
      const dl = await navButton("Downloads");
      expect(within(dl!).getByTestId("settingsnav-changed-marker")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("settingsnav-changed-marker")).toHaveLength(1);
  });
});
