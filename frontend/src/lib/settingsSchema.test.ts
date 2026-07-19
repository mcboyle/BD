import { describe, it, expect } from "vitest";
import {
  SETTINGS_SECTIONS,
  sectionForField,
  SETTINGS_SCHEMA,
} from "./settingsSchema";

// Cut 1 substrate (hidden long-pole): field -> section map. Shared by Cut 5
// (changed-markers + mini-ToC) AND Cut 6.3 (command-palette settings search).
// Built early so those cuts are free.

describe("settingsSchema", () => {
  it("exposes a non-empty ordered section list", () => {
    expect(Array.isArray(SETTINGS_SECTIONS)).toBe(true);
    expect(SETTINGS_SECTIONS.length).toBeGreaterThan(0);
    expect(SETTINGS_SECTIONS).toContain("Downloads");
    expect(SETTINGS_SECTIONS).toContain("AI assist");
  });

  it("maps known global-config fields to their section", () => {
    expect(sectionForField("global_max_concurrent")).toBe("Downloads");
    expect(sectionForField("ai_provider")).toBe("AI assist");
    expect(sectionForField("ai_api_key")).toBe("AI assist");
    expect(sectionForField("rate_limit_global_per_sec")).toBe("Network");
  });

  it("returns null for an unknown field (no fabricated section)", () => {
    expect(sectionForField("totally_made_up_field")).toBeNull();
  });

  it("every schema entry points at a declared section", () => {
    for (const field of Object.keys(SETTINGS_SCHEMA)) {
      const section = SETTINGS_SCHEMA[field].section;
      expect(SETTINGS_SECTIONS).toContain(section);
    }
  });

  // ── Cut 5 expansion (RED-first) ──────────────────────────────────────────
  // The schema reconciles to the page's 16 nav sections and maps the FULL
  // /api/global_config field set (not just the 10 origins fields). Section
  // assignments below are PAGE-TRUTH from routes/Settings.tsx `label=` blocks.
  const PAGE_SECTIONS = [
    "Downloads",
    "AI assist",
    "Network",
    "Queue housekeeping",
    "Capture",
    "Diagnostics",
    "Session keep-alive",
    "System",
    "Tools & operations",
    "Supervisor throttle",
    "Browser",
    "Challenge handling",
    "Advanced",
    "Security & access",
    "Environment (restart required)",
    "Store metadata (raw / advanced)",
    "Import / Export",
  ] as const;

  it("declares all 17 page sections in nav order", () => {
    for (const s of PAGE_SECTIONS) expect(SETTINGS_SECTIONS).toContain(s);
    // ordered prefix: the page renders these first seven in this order
    expect(SETTINGS_SECTIONS.slice(0, 7)).toEqual(PAGE_SECTIONS.slice(0, 7));
  });

  it("maps the expanded global_config field set to its page section", () => {
    const expected: Record<string, string> = {
      watch_folder: "Downloads",
      watch_interval_sec: "Downloads",
      // page-truth: these render under the Network section label
      log_level: "Network",
      ui_logging_level: "Network",
      template_auto_detect_mode: "Network",
      queue_hk_abandon: "Queue housekeeping",
      queue_hk_stale_hours: "Queue housekeeping",
      capture_bodies: "Capture",
      redact_dom_urls: "Capture",
      slow_query_ms: "Diagnostics",
      session_keep_alive_lead_time_min: "Session keep-alive",
      browser_backend: "Browser",
      challenge_wait_s: "Challenge handling",
      auth_throttle: "Advanced",
      secrets_audit: "Advanced",
      bd_token: "Security & access",
    };
    for (const [field, section] of Object.entries(expected)) {
      expect(sectionForField(field)).toBe(section);
    }
  });

  it("keeps secret fields flagged so chips/ToC never echo a value", () => {
    expect(SETTINGS_SCHEMA["ai_api_key"]?.secret).toBe(true);
  });
});
