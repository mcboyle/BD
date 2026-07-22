import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { AiBootReadinessStatus } from "@/components/ui/AiBootReadinessStatus";
import { Button } from "@/components/ui/button";
import { GatedWriteBanner } from "@/components/ui/GatedWriteBanner";
import { routeRisk } from "@/lib/routeRisk";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
import { IntegrationsHealthPanel } from "@/components/ui/IntegrationsHealthPanel";
import { SecretsUsageList } from "@/components/ui/SecretsUsageList";
import { WebhooksPanel } from "@/components/ui/WebhooksPanel";
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
import {
  useAiModels,
  useAiStatus,
  useContactSheet,
  useIntegrationsHealth,
  useSecretsUsage,
  useJsonapiProbe,
  useMarketplaceExport,
  usePlexLibraryStats,
  usePlexOnDeck,
  usePlexRecentlyAdded,
  usePlexSearch,
  usePlexServerInfo,
  usePlexStatus,
  useSubtitlesFetch,
  useTpdbApply,
  useTpdbLookup,
} from "@/hooks/useIntegrations";

// Integrations — T6 (v3.66.208). External-integration operator surface:
// plex_advanced (per-site reads) · tpdb lookup→apply (preview-first; apply
// writes an .nfo sidecar) · subtitles fetch · thumbnail contact sheet ·
// marketplace export · jsonapi probe · ai status/models. Read-mostly; the
// four writes (tpdb apply, subtitles fetch, contact sheet, marketplace
// export) are ONE-STEP confirm — never one-click. No secrets on this page
// (tpdb/plex keys are configured elsewhere; this surface only consumes
// server-side config).

type Pending =
  | { kind: "tpdbApply"; hid: number }
  | { kind: "subtitles"; hid: number }
  | { kind: "contactSheet"; hid: number }
  | { kind: "marketplaceExport"; sid: string };

const pendingLabel = (p: Pending): string => {
  switch (p.kind) {
    case "tpdbApply":
      return `Write TPDB .nfo metadata for history row #${p.hid}`;
    case "subtitles":
      return `Fetch subtitles for history row #${p.hid}`;
    case "contactSheet":
      return `Generate a thumbnail contact sheet for history row #${p.hid}`;
    case "marketplaceExport":
      return `Export site "${p.sid}" as a marketplace template`;
  }
};

export function Integrations() {
  const [pending, setPending] = useState<Pending | null>(null);

  // ── plex_advanced ────────────────────────────────────────────────────
  const [plexSid, setPlexSid] = useState("");
  const [plexActiveSid, setPlexActiveSid] = useState<string | null>(null);
  const [plexQ, setPlexQ] = useState("");
  const [plexActiveQ, setPlexActiveQ] = useState("");
  const plexStatus = usePlexStatus();
  const serverInfo = usePlexServerInfo(plexActiveSid);
  const libStats = usePlexLibraryStats(plexActiveSid);
  const recent = usePlexRecentlyAdded(plexActiveSid);
  const onDeck = usePlexOnDeck(plexActiveSid);
  const search = usePlexSearch(plexActiveSid, plexActiveQ);

  // ── per-history-row tools ────────────────────────────────────────────
  const [hidStr, setHidStr] = useState("");
  const hid = /^\d+$/.test(hidStr.trim()) ? Number(hidStr.trim()) : null;
  const tpdbLookup = useTpdbLookup();
  const tpdbApply = useTpdbApply();
  const subtitles = useSubtitlesFetch();
  const sheet = useContactSheet();

  // ── marketplace / jsonapi / ai ───────────────────────────────────────
  const [mkSid, setMkSid] = useState("");
  const mkExport = useMarketplaceExport();
  const [probeUrl, setProbeUrl] = useState("");
  const probe = useJsonapiProbe();
  const aiStatus = useAiStatus();
  const aiModels = useAiModels();
  const integrationsHealth = useIntegrationsHealth();
  const secretsUsage = useSecretsUsage();

  const busy =
    tpdbLookup.isPending ||
    tpdbApply.isPending ||
    subtitles.isPending ||
    sheet.isPending ||
    mkExport.isPending ||
    probe.isPending ||
    aiModels.isPending;

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "tpdbApply": {
        const metadata = tpdbLookup.data?.result;
        if (!metadata) {
          toast.error("Run a lookup first — apply uses the lookup result");
          break;
        }
        tpdbApply.mutate(
          { hid: pending.hid, metadata },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "apply failed")
                : toast.success(".nfo written from TPDB metadata"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      }
      case "subtitles":
        subtitles.mutate(
          { hid: pending.hid },
          {
            onSuccess: (r) =>
              r.ok === false ? toast.error(r.error || "fetch failed") : toast.success("Subtitles fetched"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "contactSheet":
        sheet.mutate(
          { hid: pending.hid },
          {
            onSuccess: (r) =>
              r.ok === false
                ? toast.error(r.error || "sheet generation failed")
                : toast.success(`Contact sheet generated${r.path ? `: ${r.path}` : ""}`),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
      case "marketplaceExport":
        mkExport.mutate(
          { sid: pending.sid },
          {
            onSuccess: (r) =>
              r.ok === false ? toast.error(r.error || "export failed") : toast.success("Marketplace export written"),
            onError: (e) => toast.error(e.message),
          },
        );
        break;
    }
    setPending(null);
  };

  const jsonPane = (label: string, q: { isLoading: boolean; data?: unknown }) => (
    <div className="min-w-0">
      <h3 className="text-xs font-semibold uppercase text-ink-3">{label}</h3>
      {q.isLoading ? (
        <Skeleton className="mt-1 h-12 w-full" />
      ) : (
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
          {JSON.stringify(q.data ?? {}, null, 2)}
        </pre>
      )}
    </div>
  );

  return (
    <AppShell title="Integrations" subtitle="Plex · TPDB · subtitles · sheets · marketplace · JSON-API · AI">
      <GatedWriteBanner
        title="Read-mostly surface"
        shape={routeRisk("/integrations").bannerShape}
      >
        The four writes (TPDB apply, subtitles, contact sheet, marketplace export) take a
        one-step confirm — nothing fires on a single click.
      </GatedWriteBanner>

      <Callout tone="info" title="What this page does" className="mt-3">
        Connect BulkDownloader to outside services — Plex, ThePornDB, subtitle
        and sheet providers, the template marketplace, JSON APIs, and the AI
        assist backend. Most actions read or look up; the few writes confirm
        before they run.
      </Callout>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Integration health · read-only</h2>
        <IntegrationsHealthPanel
          data={integrationsHealth.data}
          loading={integrationsHealth.isLoading}
        />
        <h3 className="section-head mt-4">Secret usage · references only</h3>
        <SecretsUsageList data={secretsUsage.data} loading={secretsUsage.isLoading} />
      </Card>

      {/* v3.66.731: the webhooks CONTROL cluster. The blueprint has existed since
          v3.66.405 with no way to reach it from the GUI -- registering a webhook
          meant curling the API. Outgoing webhooks are an integration, so they
          live here. */}
      <div className="mt-4">
        <WebhooksPanel />
      </div>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Plex · per-site</h2>
        {plexStatus.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : (
          <p className="mb-2 text-sm text-ink-3">
            status: {JSON.stringify(plexStatus.data ?? {})}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="site id"
            value={plexSid}
            onChange={(e) => setPlexSid(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={!plexSid.trim()}
            onClick={() => setPlexActiveSid(plexSid.trim())}
          >
            Load
          </Button>
          <Input
            className="max-w-[220px]"
            placeholder="search query"
            value={plexQ}
            onChange={(e) => setPlexQ(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={!plexActiveSid || !plexQ.trim()}
            onClick={() => setPlexActiveQ(plexQ.trim())}
          >
            Search
          </Button>
        </div>
        {plexActiveSid && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {jsonPane("Server info", serverInfo)}
            {jsonPane("Library stats", libStats)}
            {jsonPane("Recently added", recent)}
            {jsonPane("On deck", onDeck)}
            {plexActiveQ ? jsonPane(`Search “${plexActiveQ}”`, search) : null}
          </div>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Per-history-row tools</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[140px]"
            placeholder="history id"
            value={hidStr}
            onChange={(e) => setHidStr(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={busy || hid == null}
            onClick={() =>
              tpdbLookup.mutate(
                { hid: hid as number },
                {
                  onSuccess: (r) => {
                    if (r.ok === false) toast.error(r.error || "lookup failed");
                  },
                  onError: (e) => toast.error(e.message),
                },
              )
            }
          >
            TPDB lookup
          </Button>
          <Button
            variant="outline"
            disabled={busy || hid == null || !tpdbLookup.data?.result}
            onClick={() => setPending({ kind: "tpdbApply", hid: hid as number })}
            title="Writes an .nfo sidecar from the lookup result"
          >
            Apply metadata
          </Button>
          <Button
            variant="outline"
            disabled={busy || hid == null}
            onClick={() => setPending({ kind: "subtitles", hid: hid as number })}
          >
            Fetch subtitles
          </Button>
          <Button
            variant="outline"
            disabled={busy || hid == null}
            onClick={() => setPending({ kind: "contactSheet", hid: hid as number })}
          >
            Contact sheet
          </Button>
        </div>
        {tpdbLookup.data?.result ? (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
            {JSON.stringify(tpdbLookup.data.result, null, 2)}
          </pre>
        ) : null}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">Marketplace · JSON-API probe</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="max-w-[220px]"
            placeholder="site id to export"
            value={mkSid}
            onChange={(e) => setMkSid(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={busy || !mkSid.trim()}
            onClick={() => setPending({ kind: "marketplaceExport", sid: mkSid.trim() })}
          >
            Export template
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Input
            className="max-w-[320px]"
            placeholder="https://… URL to probe for a JSON API"
            value={probeUrl}
            onChange={(e) => setProbeUrl(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={busy || !probeUrl.trim()}
            onClick={() =>
              probe.mutate(
                { url: probeUrl.trim() },
                { onError: (e) => toast.error(e.message) },
              )
            }
          >
            Probe
          </Button>
        </div>
        {probe.data ? (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
            {JSON.stringify(probe.data, null, 2)}
          </pre>
        ) : null}
      </Card>

      <Card className="mt-4 p-4">
        <h2 className="section-head">AI assist</h2>
        {aiStatus.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : (
          <AiBootReadinessStatus value={aiStatus.data?.boot_readiness} />
        )}
        <div className="mt-2 flex items-center gap-2">
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => aiModels.mutate(undefined, { onError: (e) => toast.error(e.message) })}
          >
            List models
          </Button>
        </div>
        {aiModels.data ? (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-xs text-emerald-200/80">
            {JSON.stringify(aiModels.data, null, 2)}
          </pre>
        ) : null}
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>{pending ? pendingLabel(pending) : ""}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button variant="default" onClick={confirmRun}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
