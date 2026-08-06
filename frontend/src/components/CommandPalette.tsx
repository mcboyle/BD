import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity as ActivityIcon,
  Globe,
  Home as HomeIcon,
  ListOrdered,
  Pause,
  Play,
  RefreshCw,
  Settings as SettingsIcon,
} from "lucide-react";
import { toast } from "sonner";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useKeyboardShortcut } from "@/hooks/useKeyboardShortcut";
import { apiGet, apiPost } from "@/lib/api-client";
import type { SitesV2 } from "@/lib/api-types";
import { SETTINGS_SECTIONS } from "@/lib/settingsSchema";

// Cut 6.3 — palette settings-section search. A "Settings sections" group is
// generated from SETTINGS_SECTIONS (the single source of truth shared with the
// Settings page), so the palette never drifts from the real section list.
// Selecting a section deep-links to /settings#<slug>. No recents/pins.
const sectionSlug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

// ⌘K command palette. Mounted once at the AppShell level so it's
// available from any tab. Contents:
//   - Navigate to each tab (top-level)
//   - Jump to each site (typing the name filters via cmdk's
//     built-in fuzzy match)
//   - Quick actions: Pause all, Resume all, Refresh all queries
//
// Per-tab keyboard nav: g + h/s/q/a/c-style chord shortcuts would be
// the next step, but standard Cmd+1..5 is too risky to grab from the
// browser. The palette gets us most of the keyboard-nav benefit
// (4 keystrokes to switch tab from anywhere) without OS conflicts.

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();

  // ⌘K toggle. allowInInput is true so the palette opens even from
  // inside the wizard's name field — a fundamental palette UX rule.
  useKeyboardShortcut(
    "k",
    { meta: true, allowInInput: true },
    (e) => {
      e.preventDefault();
      setOpen((v) => !v);
    },
  );

  // v3.66.906 — Esc closes. The dialog primitive does NOT reliably cover this,
  // which is what the old comment here assumed. Radix's DismissableLayer
  // (react-dismissable-layer 1.1.11) guards its Escape handler with
  //   const isHighestLayer = index === context.layers.size - 1;
  //   if (!isHighestLayer) return;
  // where `index` is captured at RENDER time and `layers.size` is read at
  // EVENT time. Inside that settle window the guard is false and the handler
  // RETURNS — the keypress is DISCARDED, not queued — so the dialog stays
  // data-state="open" indefinitely and no timeout rescues it. Measured: Escape
  // fired the instant the dialog node appears is swallowed 10/10, at 64ms it
  // closes 5/5. This surfaced as a 1-in-10 capture failure (v3.66.902).
  //
  // Closing explicitly here does not depend on that guard. setOpen is
  // idempotent, and returning `v` unchanged when already closed means React
  // bails out of the re-render, so this stays a no-op app-wide until the
  // palette is actually open. allowInInput is required: focus sits in cmdk's
  // CommandInput, which the hook would otherwise skip.
  useKeyboardShortcut(
    "escape",
    { allowInInput: true },
    () => setOpen((v) => (v ? false : v)),
  );

  const { data: sitesData } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    // The palette doesn't need its own polling — it reads whatever
    // Sites.tsx has cached. If it's stale, the worst case is a
    // recently-deleted site appears in the palette for a few seconds.
    refetchInterval: false,
    staleTime: 30_000,
    enabled: open, // only kick the fetch when the palette opens
  });

  const pauseAll = useMutation({
    mutationFn: () => apiPost("/api/pause_all", {}),
    onSuccess: () => {
      toast.success("Paused all sites");
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
    onError: (e: Error) => toast.error(`Pause-all failed: ${e.message}`),
  });
  const resumeAll = useMutation({
    mutationFn: () => apiPost("/api/resume_all", {}),
    onSuccess: () => {
      toast.success("Resumed all sites");
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
    },
    onError: (e: Error) => toast.error(`Resume-all failed: ${e.message}`),
  });

  const go = (path: string) => {
    setOpen(false);
    nav(path);
  };
  // v3.66.506 — server-rendered consoles (/framework, /fleet, /cockpit) are NOT
  // react-router routes; nav(path) would route into the SPA catch-all. Open them
  // as external pages (new tab) instead.
  const goExternal = (path: string) => {
    setOpen(false);
    window.open(path, "_blank", "noopener,noreferrer");
  };
  const run = (mut: { mutate: () => void }) => {
    setOpen(false);
    mut.mutate();
  };
  const refreshAll = () => {
    setOpen(false);
    qc.invalidateQueries();
    toast.success("Refreshed");
  };

  // Reset the cmdk input on close so the next open starts fresh —
  // otherwise the previous filter string sticks.
  useEffect(() => {
    if (!open) {
      // cmdk's Dialog wraps its own search state. The component
      // recreates on every open via the `open` prop on CommandDialog,
      // so the input is reset automatically. Nothing to do here.
    }
  }, [open]);

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a command or search sites…" />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>

        <CommandGroup heading="Go to">
          <CommandItem onSelect={() => go("/")}>
            <HomeIcon className="mr-2 h-4 w-4" />
            Home
            <CommandShortcut>⌘K H</CommandShortcut>
          </CommandItem>
          <CommandItem onSelect={() => go("/sites")}>
            <Globe className="mr-2 h-4 w-4" />
            Sites
          </CommandItem>
          <CommandItem onSelect={() => go("/queue")}>
            <ListOrdered className="mr-2 h-4 w-4" />
            Queue
          </CommandItem>
          <CommandItem onSelect={() => go("/activity")}>
            <ActivityIcon className="mr-2 h-4 w-4" />
            Activity
          </CommandItem>
          <CommandItem onSelect={() => go("/settings")}>
            <SettingsIcon className="mr-2 h-4 w-4" />
            Settings
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        {/* v3.66.200 nav consolidation — every route reachable from ⌘K.
            The tab bar is contract-frozen at 5 entries, so the palette
            is the full-route surface. See docs/NAV_CONSOLIDATION.md. */}
        <CommandGroup heading="Tools">
          <CommandItem onSelect={() => go("/dashboard")}>System Overview</CommandItem>
          <CommandItem onSelect={() => go("/history")}>History &amp; Logs</CommandItem>
          <CommandItem onSelect={() => go("/needs-review")}>Needs review</CommandItem>
          <CommandItem onSelect={() => go("/notifications")}>Notifications</CommandItem>
          <CommandItem onSelect={() => go("/cluster")}>Cluster</CommandItem>
          <CommandItem onSelect={() => go("/templates")}>Template manager</CommandItem>
          <CommandItem onSelect={() => go("/settings/advanced")}>Advanced / diagnostics</CommandItem>
          <CommandItem onSelect={() => go("/secrets")}>Secrets vault</CommandItem>
          <CommandItem onSelect={() => go("/ai-teach")}>AI selector repair</CommandItem>
          <CommandItem onSelect={() => go("/dom-analyzer")}>DOM analyzer</CommandItem>
          <CommandItem onSelect={() => go("/capture")}>Capture workflow</CommandItem>
          <CommandItem onSelect={() => go("/library")}>Library</CommandItem>
          <CommandItem onSelect={() => go("/backup")}>Backup</CommandItem>
          <CommandItem onSelect={() => go("/maintenance")}>Maintenance</CommandItem>
          <CommandItem onSelect={() => go("/plugins/metrics")}>Plugin metrics</CommandItem>
          <CommandItem onSelect={() => goExternal("/framework")}>Framework dashboard ↗</CommandItem>
          <CommandItem onSelect={() => goExternal("/fleet")}>Fleet view ↗</CommandItem>
          <CommandItem onSelect={() => goExternal("/cockpit")}>Cockpit console ↗</CommandItem>
          <CommandItem onSelect={() => go("/more-actions")}>More actions</CommandItem>
          <CommandItem onSelect={() => go("/imports")}>Imports</CommandItem>
          <CommandItem onSelect={() => go("/import-views")}>Import views</CommandItem>
          <CommandItem onSelect={() => go("/dedup")}>Dedup</CommandItem>
          <CommandItem onSelect={() => go("/rebalance")}>Rebalance</CommandItem>
          <CommandItem onSelect={() => go("/vpn")}>VPN</CommandItem>
          <CommandItem onSelect={() => go("/integrations")}>Integrations</CommandItem>
          <CommandItem onSelect={() => go("/batch-ops")}>Batch operations</CommandItem>
          <CommandItem onSelect={() => go("/pools-macros")}>Pools &amp; macros</CommandItem>
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Settings sections">
          {SETTINGS_SECTIONS.map((section) => (
            <CommandItem
              key={section}
              onSelect={() => go(`/settings#${sectionSlug(section)}`)}
            >
              <SettingsIcon className="mr-2 h-4 w-4" />
              {section}
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="Actions">
          <CommandItem onSelect={() => run(pauseAll)}>
            <Pause className="mr-2 h-4 w-4" />
            Pause all sites
          </CommandItem>
          <CommandItem onSelect={() => run(resumeAll)}>
            <Play className="mr-2 h-4 w-4" />
            Resume all sites
          </CommandItem>
          <CommandItem onSelect={refreshAll}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh all data
          </CommandItem>
        </CommandGroup>

        {sitesData?.sites && sitesData.sites.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Jump to site">
              {sitesData.sites.map((site) => (
                <CommandItem
                  key={site.site_id}
                  // value combines name+id so cmdk's fuzzy matcher
                  // matches typed text against both
                  value={`${site.name} ${site.site_id}`}
                  onSelect={() => go("/sites")}
                >
                  <span
                    className="mr-2 grid h-4 w-4 place-items-center rounded-sm text-[9px] font-bold text-white"
                    style={{ backgroundColor: site.avatar_color }}
                  >
                    {(site.name || "?").charAt(0).toUpperCase()}
                  </span>
                  {site.name}
                  {(site.captcha_pending || site.auth_state === "expired") && (
                    <span className="ml-auto rounded-sm bg-amber-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-dim">
                      Issue
                    </span>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
}
