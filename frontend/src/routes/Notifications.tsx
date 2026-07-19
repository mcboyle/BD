import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { PushSection } from "@/components/sections/PushSection";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SecretField } from "@/components/SecretField";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useAppriseSettings,
  useSaveAppriseSettings,
  useAppriseValidate,
  useAppriseTest,
  useTgStatus,
  useTgSettings,
  useSaveTgSettings,
  useTgTest,
  useActiveAlerts,
} from "@/hooks/useNotificationsData";

// ── T7 Notifications (v3.66.210) ────────────────────────────────────
// One route consolidating the notify(apprise) · tg(bot) · alerts
// families. Secrets (apprise URLs, tg token) are WRITE-ONLY: the inputs
// start empty, GET never echoes them, and they are POSTed only when
// non-empty. Writes are never one-click — each arms a single-tap (Tier
// B) confirm. Capture-body redaction for these secrets lands in the
// same cut (capture_redact.py).

type Pending =
  | { kind: "saveApprise" }
  | { kind: "testApprise" }
  | { kind: "saveTg" }
  | { kind: "testTg" };

export default function Notifications() {
  const apprise = useAppriseSettings();
  const saveApprise = useSaveAppriseSettings();
  const validateApprise = useAppriseValidate();
  const testApprise = useAppriseTest();
  const tgStatus = useTgStatus();
  const tgSettings = useTgSettings();
  const saveTg = useSaveTgSettings();
  const testTg = useTgTest();
  const alerts = useActiveAlerts();

  // write-only secret fields (never seeded from GET)
  const [appriseUrls, setAppriseUrls] = useState("");
  const [appriseEnabled, setAppriseEnabled] = useState(false);
  const [tgToken, setTgToken] = useState("");
  const [tgEnabled, setTgEnabled] = useState(false);
  const [tgAllowlist, setTgAllowlist] = useState("");

  const [pending, setPending] = useState<Pending | null>(null);
  const busy =
    saveApprise.isPending ||
    testApprise.isPending ||
    saveTg.isPending ||
    testTg.isPending;

  const aprCount = apprise.data?.settings?.notify_apprise_urls_count ?? 0;
  const aprSet = apprise.data?.settings?.notify_apprise_urls_set ?? false;
  const tgTokenSet = tgSettings.data?.settings?.tg_bot_token_set ?? false;

  const confirmRun = () => {
    if (!pending) return;
    switch (pending.kind) {
      case "saveApprise": {
        const patch: Record<string, unknown> = {
          notify_apprise_enabled: appriseEnabled,
        };
        // write-only: only send URLs when the operator pasted a replacement
        if (appriseUrls.trim()) patch.notify_apprise_urls = appriseUrls;
        saveApprise.mutate(patch, {
          onSuccess: () => {
            setAppriseUrls(""); // never retain the secret in state
            toast.success("Apprise settings saved");
          },
          onError: (e) => toast.error(String((e as Error).message)),
        });
        break;
      }
      case "testApprise":
        testApprise.mutate(
          { title: "BulkDownloader test", body: "Test notification." },
          {
            onSuccess: (r) =>
              toast.success(`Test sent (${r.sent ?? 0} ok, ${r.failed ?? 0} failed)`),
            onError: (e) => toast.error(String((e as Error).message)),
          },
        );
        break;
      case "saveTg": {
        const patch: Record<string, unknown> = {
          tg_bot_enabled: tgEnabled,
          tg_bot_allowlist: tgAllowlist,
        };
        if (tgToken.trim()) patch.tg_bot_token = tgToken;
        saveTg.mutate(patch, {
          onSuccess: () => {
            setTgToken("");
            toast.success("Telegram settings saved");
          },
          onError: (e) => toast.error(String((e as Error).message)),
        });
        break;
      }
      case "testTg":
        testTg.mutate(undefined, {
          onSuccess: (r) => toast.success(`TG test: ${r.sent ?? 0} sent`),
          onError: (e) => toast.error(String((e as Error).message)),
        });
        break;
    }
    setPending(null);
  };

  return (
    <AppShell title="Notifications" subtitle="Apprise · Telegram · Alerts">
      {/* Apprise */}
      <Card className="p-4">
        <h2 className="section-head">Apprise endpoints</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          {aprSet ? `${aprCount} endpoint(s) configured` : "none configured"} ·
          URLs are write-only — paste to replace, never shown.
        </p>
        <label className="mb-2 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={appriseEnabled}
            onChange={(e) => setAppriseEnabled(e.target.checked)}
          />
          Enable apprise notifications
        </label>
        <textarea
          className="mb-2 min-h-[80px] w-full rounded border bg-background p-2 font-mono text-xs"
          placeholder="one apprise URL per line (write-only — leave blank to keep existing)"
          value={appriseUrls}
          onChange={(e) => setAppriseUrls(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            disabled={validateApprise.isPending || !appriseUrls.trim()}
            onClick={() =>
              validateApprise.mutate(appriseUrls, {
                onSuccess: (r) => {
                  const bad = (r.results ?? []).filter((x) => !x.ok).length;
                  bad
                    ? toast.error(`${bad} invalid URL(s)`)
                    : toast.success("All URLs valid");
                },
                onError: (e) => toast.error(String((e as Error).message)),
              })
            }
          >
            Validate
          </Button>
          <Button disabled={busy} onClick={() => setPending({ kind: "saveApprise" })}>
            Save apprise
          </Button>
          <Button
            variant="outline"
            disabled={busy || !aprSet}
            onClick={() => setPending({ kind: "testApprise" })}
          >
            Send test
          </Button>
        </div>
      </Card>

      {/* Telegram */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Telegram bot</h2>
        <p className="mb-2 text-xs text-muted-foreground">
          {tgStatus.data?.available
            ? `${tgStatus.data?.running ? "running" : "stopped"} · ${
                tgStatus.data?.allowlist_size ?? 0
              } allowlisted`
            : "bot module unavailable"}
          {tgTokenSet ? " · token set" : " · no token"}
        </p>
        <label className="mb-2 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={tgEnabled}
            onChange={(e) => setTgEnabled(e.target.checked)}
          />
          Enable bot
        </label>
        <SecretField
          className="mb-2"
          placeholder="bot token (write-only — leave blank to keep existing)"
          value={tgToken}
          onChange={setTgToken}
          ariaLabel="Telegram bot token"
        />
        <Input
          className="mb-2"
          placeholder="chat-id allowlist (comma-separated)"
          value={tgAllowlist}
          onChange={(e) => setTgAllowlist(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => setPending({ kind: "saveTg" })}>
            Save telegram
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => setPending({ kind: "testTg" })}
          >
            Send test
          </Button>
        </div>
      </Card>

      {/* Active alerts */}
      <Card className="mt-4 p-4">
        <h2 className="section-head">Active alerts</h2>
        {alerts.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !alerts.data?.alerts?.length ? (
          <p className="text-sm text-muted-foreground">No active alerts.</p>
        ) : (
          <ul className="divide-y divide-border">
            {alerts.data.alerts.map((a, i) => (
              <li key={a.id || i} className="py-2 text-sm">
                <span className="text-xs text-muted-foreground">
                  {a.severity || "info"}
                </span>{" "}
                {a.message || a.rule || "alert"}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Tier B single-tap confirm — never one-click */}
      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm action</DialogTitle>
            <DialogDescription>
              {pending?.kind === "saveApprise" && "Save apprise settings and apply."}
              {pending?.kind === "testApprise" && "Send a test notification now."}
              {pending?.kind === "saveTg" && "Save telegram settings and apply."}
              {pending?.kind === "testTg" && "Send a telegram test message now."}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button disabled={busy} onClick={confirmRun}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* T9b: web-push surface (the last wireable legacy-parity tranche) */}
      <PushSection />
    </AppShell>
  );
}
