import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAddWebhook,
  useDrainWebhooks,
  useRemoveWebhook,
  useWebhooks,
} from "@/hooks/useWebhooks";

// v3.66.731 — GUI for the webhooks CONTROL cluster.
//
// THE EVENT VOCABULARY IS DERIVED, NOT INVENTED. These are exactly the event
// names the backend passes to webhooks.fire():
//
//   bulk_downloader/runner.py:1797       download.done / download.failed /
//                                        download.needs_review
//   bulk_downloader/alerts_engine.py:321 alert.fired
//   bulk_downloader/maintenance.py:210   maintenance.start
//   bulk_downloader/maintenance.py:218   maintenance.end
//
// download.progress and download.retry look like they belong here and DO NOT:
// they are PLUGIN emits (_pl.emit in runner.py), never fired to webhooks. A
// checkbox for an event nothing emits is a dead control -- the operator would
// subscribe and simply never be called. Offering only what fires is the whole
// point; if a new fire() site is added, add it here in the same cut.
export const WEBHOOK_EVENTS = [
  "download.done",
  "download.failed",
  "download.needs_review",
  "alert.fired",
  "maintenance.start",
  "maintenance.end",
] as const;

export function WebhooksPanel() {
  const list = useWebhooks();
  const add = useAddWebhook();
  const remove = useRemoveWebhook();
  const drain = useDrainWebhooks();

  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState<string[]>([]);

  const toggle = (ev: string) =>
    setEvents((prev) => (prev.includes(ev) ? prev.filter((e) => e !== ev) : [...prev, ev]));

  // The backend 400s on an empty events list. Sending a request we KNOW will be
  // refused is a dead control with extra steps -- gate it here instead.
  const canAdd = url.trim().length > 0 && events.length > 0 && !add.isPending;

  const submit = () => {
    if (!canAdd) return;
    add.mutate(
      { url: url.trim(), events, secret: secret.trim() },
      {
        onSuccess: (r) => {
          if (r.ok) {
            toast.success("Webhook registered");
            setUrl("");
            setSecret("");
            setEvents([]);
          } else {
            toast.error(r.error || "Webhook rejected");
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const subs = list.data?.subscriptions ?? [];

  return (
    <Card className="p-4 space-y-4" data-testid="webhooks-panel">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Outgoing webhooks</h2>
          <p className="text-xs text-muted-foreground">
            POSTs a signed JSON payload to your endpoint when a subscribed event fires.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          aria-label="Drain queue now"
          disabled={drain.isPending}
          onClick={() =>
            drain.mutate(undefined, {
              onSuccess: (r) =>
                toast.success(
                  `Drain complete: ${String(r.sent ?? 0)} sent, ${String(r.failed ?? 0)} failed`,
                ),
              onError: (e) => toast.error(e.message),
            })
          }
        >
          Drain queue now
        </Button>
      </div>

      {/* existing subscriptions */}
      {list.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : subs.length === 0 ? (
        <p className="text-xs text-muted-foreground">No webhooks registered.</p>
      ) : (
        <ul className="divide-y rounded border">
          {subs.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 p-2">
              <div className="min-w-0">
                <div className="truncate text-sm">{s.url}</div>
                <div className="text-xs text-muted-foreground">
                  {s.events.join(", ") || "no events"}
                  {s.secret ? ` — secret ${s.secret}` : " — no secret"}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove webhook ${s.id}`}
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(s.id, {
                    onSuccess: () => toast.success("Webhook removed"),
                    onError: (e) => toast.error(e.message),
                  })
                }
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      {/* add form */}
      <div className="space-y-2">
        <label className="block text-xs font-medium" htmlFor="wh-url">
          Webhook URL
        </label>
        <Input
          id="wh-url"
          aria-label="Webhook URL"
          placeholder="https://hooks.example.com/bulkdl"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />

        <label className="block text-xs font-medium" htmlFor="wh-secret">
          Signing secret (optional)
        </label>
        <Input
          id="wh-secret"
          aria-label="Signing secret"
          type="password"
          placeholder="leave blank for none"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />

        <fieldset className="space-y-1">
          <legend className="text-xs font-medium">Events</legend>
          <div className="grid grid-cols-2 gap-1">
            {WEBHOOK_EVENTS.map((ev) => (
              <label key={ev} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  aria-label={ev}
                  checked={events.includes(ev)}
                  onChange={() => toggle(ev)}
                />
                <span className="font-mono">{ev}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <Button size="sm" aria-label="Add webhook" disabled={!canAdd} onClick={submit}>
          Add webhook
        </Button>
      </div>
    </Card>
  );
}
