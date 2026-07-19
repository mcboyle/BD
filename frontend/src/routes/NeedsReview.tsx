import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { AiReanalyzeSection } from "@/components/ui/AiReanalyzeSection";
import { PlainLanguageHint } from "@/components/ui/PlainLanguageHint";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/StatusPill";
import { useHistory } from "@/hooks/useHistoryData";
import { isHttpUrl } from "@/lib/safeUrl";
import { useKeyboardShortcut } from "@/hooks/useKeyboardShortcut";
import { useNeedsReview, type TriageTarget } from "@/hooks/useNeedsReview";
import type { HistoryRow } from "@/lib/api-types";

// F2.3 — Screenshot triage UI (needs_review tab).
//
// A global queue of jobs the runner parked in `needs_review`, each shown
// with the failure screenshot beside its message so the operator can act in
// one glance. Three actions, all on existing endpoints (useNeedsReview):
//   a / Approve  — bulk_approve (bypass min_resolution + re-download)
//   r / Retry    — retry_one (re-queue)
//   s / Skip     — jobs/mark failed (dismiss; stops auto-retry)
// j / k move the selection. The read rides the existing
// useHistory({ status: "needs_review" }); a successful action invalidates it
// so the row clears and the cursor advances.

// Path-traversal-safe /screenshots/ URL. The stored `screenshot` field is a
// posix path under SCREENSHOTS_DIR/<sid>/; we trust ONLY the basename and
// re-prepend site_id — mirroring the backend AI-reanalyze precedent
// (SCREENSHOTS_DIR / sid / Path(ss_path).name) and the serve_ss CWE-22
// boundary check. Never a file:// URL. Returns null when no screenshot.
function screenshotUrl(r: HistoryRow): string | null {
  const ss = typeof r.screenshot === "string" ? r.screenshot : "";
  const sid = typeof r.site_id === "string" ? r.site_id : "";
  if (!ss || !sid) return null;
  const base = ss.split("/").pop() || "";
  if (!base) return null;
  return `/screenshots/${encodeURIComponent(sid)}/${encodeURIComponent(base)}`;
}

function rowKey(r: HistoryRow, i: number): string {
  return r.id != null ? `id:${r.id}` : `${r.site_id ?? ""}|${r.url ?? ""}|${i}`;
}

export default function NeedsReview() {
  const history = useHistory({ status: "needs_review", limit: 200 });
  const rows = useMemo<HistoryRow[]>(
    () => (history.data ?? []).filter((r) => r.status === "needs_review"),
    [history.data],
  );
  const { approve, retry, skip } = useNeedsReview();

  const [cursor, setCursor] = useState(0);
  // Keep the cursor in range as the list shrinks (a cleared row drops out).
  useEffect(() => {
    setCursor((c) => (rows.length === 0 ? 0 : Math.min(c, rows.length - 1)));
  }, [rows.length]);

  const current: HistoryRow | undefined = rows[cursor];
  const busy = approve.isPending || retry.isPending || skip.isPending;

  const act = (m: { mutate: (t: TriageTarget) => void }) => {
    if (!current || busy) return;
    const sid = typeof current.site_id === "string" ? current.site_id : "";
    const url = typeof current.url === "string" ? current.url : "";
    if (!sid || !url) return;
    m.mutate({ sid, url });
  };

  // Keyboard: j/k navigate, a/r/s act. useKeyboardShortcut skips text inputs.
  useKeyboardShortcut("j", {}, () =>
    setCursor((c) => Math.min(rows.length - 1, c + 1)),
  );
  useKeyboardShortcut("k", {}, () => setCursor((c) => Math.max(0, c - 1)));
  useKeyboardShortcut("a", {}, () => act(approve));
  useKeyboardShortcut("r", {}, () => act(retry));
  useKeyboardShortcut("s", {}, () => act(skip));

  const ss = current ? screenshotUrl(current) : null;

  return (
    <AppShell
      title="Needs review"
      subtitle={
        rows.length === 0
          ? "Triage queue — nothing waiting"
          : `${rows.length} item${rows.length === 1 ? "" : "s"} awaiting review`
      }
      trailing={
        <span className="hidden text-xs text-muted sm:inline">
          j/k move · a approve · r retry · s skip
        </span>
      }
    >
      {history.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No review items"
          hint="Validation and operator approvals will appear here. Jobs the runner can't resolve on its own — failed verification, blocked downloads, low-resolution media — land here for a quick approve / retry / skip."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          {/* List */}
          <Card className="max-h-[70vh] overflow-y-auto p-1">
            <ul className="divide-y divide-border">
              {rows.map((r, i) => {
                const active = i === cursor;
                return (
                  <li key={rowKey(r, i)}>
                    <button
                      type="button"
                      onClick={() => setCursor(i)}
                      aria-current={active}
                      className={
                        "block w-full rounded-md px-3 py-2 text-left text-sm transition-colors " +
                        (active ? "bg-surface-2" : "hover:bg-surface-2")
                      }
                    >
                      <div className="truncate font-medium text-ink">
                        {(r.site_name as string) ||
                          (r.site_id as string) ||
                          "—"}
                      </div>
                      <div className="truncate text-xs text-muted">
                        {(r.url as string) || ""}
                      </div>
                      {r.message ? (
                        <div className="mt-0.5 truncate text-xs text-amber">
                          {r.message as string}
                        </div>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>

          {/* Detail */}
          <Card className="p-4">
            {current ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill tone="amber">needs review</StatusPill>
                  <span className="text-sm font-medium text-ink">
                    {(current.site_name as string) ||
                      (current.site_id as string) ||
                      "—"}
                  </span>
                  {current.ts ? (
                    <span className="text-xs text-muted">
                      {String(current.ts).replace("T", " ")}
                    </span>
                  ) : null}
                </div>

                {current.url ? (
                  isHttpUrl(current.url) ? (
                    <a
                      href={current.url as string}
                      target="_blank"
                      rel="noreferrer"
                      className="block break-all text-xs text-muted underline-offset-2 hover:underline"
                    >
                      {current.url as string}
                    </a>
                  ) : (
                    <span className="block break-all text-xs text-muted">
                      {current.url as string}
                    </span>
                  )
                ) : null}

                {/* Screenshot + message side by side */}
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="overflow-hidden rounded-md hairline bg-surface-2">
                    {ss ? (
                      <a href={ss} target="_blank" rel="noreferrer">
                        <img
                          src={ss}
                          alt="Failure screenshot"
                          loading="lazy"
                          className="block max-h-[48vh] w-full object-contain"
                        />
                      </a>
                    ) : (
                      <div className="flex h-32 items-center justify-center text-xs text-muted">
                        No screenshot captured
                      </div>
                    )}
                  </div>
                  <div className="space-y-1">
                    <div className="text-xs font-medium uppercase tracking-wide text-muted">
                      Message
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm text-ink">
                      {(current.message as string) || "—"}
                    </p>
                    {typeof current.message === "string" && current.message ? (
                      <PlainLanguageHint
                        key={current.message}
                        message={current.message}
                      />
                    ) : null}
                    {typeof current.honeypot_score === "number" ? (
                      <div className="pt-1 text-xs text-muted">
                        honeypot score: {current.honeypot_score}
                      </div>
                    ) : null}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button onClick={() => act(approve)} disabled={busy}>
                    Approve <span className="ml-1 opacity-60">a</span>
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => act(retry)}
                    disabled={busy}
                  >
                    Retry <span className="ml-1 opacity-60">r</span>
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => act(skip)}
                    disabled={busy}
                  >
                    Skip <span className="ml-1 opacity-60">s</span>
                  </Button>
                </div>
                <p className="text-xs text-muted">
                  Approve re-downloads this item, bypassing the resolution
                  threshold. Retry re-queues it. Skip marks it failed and
                  stops the auto-retry scanner.
                </p>

                {typeof current.site_id === "string" &&
                typeof current.url === "string" &&
                current.site_id &&
                current.url ? (
                  <AiReanalyzeSection
                    key={`${current.site_id}|${current.url}`}
                    sid={current.site_id}
                    url={current.url}
                  />
                ) : null}
              </div>
            ) : (
              <div className="p-4 text-sm text-muted">Select an item.</div>
            )}
          </Card>
        </div>
      )}
    </AppShell>
  );
}
