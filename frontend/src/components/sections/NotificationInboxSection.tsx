// NotificationInboxSection — F005 notification inbox, mounted at the top of
// the /notifications route. Every FIRED alert is a notification: the list
// loads newest first, each item marks read/unread in one click (reversible,
// so no confirm dialog — the Tier B confirm guards sends and saves), and two
// filters (status, severity) narrow the list server-side while the unread /
// total counts stay whole-inbox.
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  useMarkNotificationRead,
  useNotificationInbox,
} from "@/hooks/useNotificationsData";
import type { InboxSeverity, InboxStatus } from "@/lib/api-types";

const SELECT_CLASS = "rounded border border-line bg-surface-2 p-1 text-sm text-ink";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function NotificationInboxSection() {
  const [status, setStatus] = useState<InboxStatus>("all");
  const [severity, setSeverity] = useState<InboxSeverity>("all");
  const inbox = useNotificationInbox(status, severity);
  const mark = useMarkNotificationRead();
  const items = inbox.data?.notifications ?? [];

  async function toggle(id: number, read: boolean) {
    try {
      await mark.mutateAsync({ id, read });
    } catch (e) {
      toast.error(errText(e));
    }
  }

  return (
    <Card className="mb-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="section-head">Inbox</h2>
        <span className="text-xs text-muted-foreground" data-testid="inbox-counts">
          {inbox.data?.unread ?? 0} unread of {inbox.data?.total ?? 0}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1">
          Status
          <select
            aria-label="Status filter"
            value={status}
            onChange={(e) => setStatus(e.target.value as InboxStatus)}
            className={SELECT_CLASS}
          >
            <option value="all">All</option>
            <option value="unread">Unread</option>
            <option value="read">Read</option>
          </select>
        </label>
        <label className="flex items-center gap-1">
          Severity
          <select
            aria-label="Severity filter"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as InboxSeverity)}
            className={SELECT_CLASS}
          >
            <option value="all">All</option>
            <option value="info">Info</option>
            <option value="warn">Warn</option>
            <option value="fail">Fail</option>
          </select>
        </label>
      </div>
      {inbox.isLoading ? (
        <p className="mt-2 text-sm text-muted-foreground">Loading…</p>
      ) : inbox.isError || inbox.data?.error ? (
        <p className="mt-2 text-sm text-red" role="alert">
          Notifications unavailable: {inbox.data?.error || errText(inbox.error)}
        </p>
      ) : !items.length ? (
        <p className="mt-2 text-sm text-muted-foreground">No notifications match.</p>
      ) : (
        <ul className="mt-2 divide-y divide-border" aria-label="Notification list">
          {items.map((n) => (
            <li
              key={n.id}
              data-read={n.read ? "true" : "false"}
              className="flex items-start justify-between gap-2 py-2 text-sm"
            >
              <div className={n.read ? "text-muted-foreground" : "font-semibold"}>
                <span className="text-xs uppercase">{n.severity}</span>{" "}
                {n.name}
                {n.message ? ` — ${n.message}` : ""}
                <div className="text-xs font-normal text-muted-foreground">
                  {new Date(n.ts * 1000).toLocaleString()}
                  {n.read ? " · read" : " · unread"}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={mark.isPending}
                aria-label={`${n.read ? "Mark unread" : "Mark read"}: ${n.name}`}
                onClick={() => toggle(n.id, !n.read)}
              >
                {n.read ? "Mark unread" : "Mark read"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
