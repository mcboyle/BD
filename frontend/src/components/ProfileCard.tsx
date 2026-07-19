import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDriveDownload, ShieldCheck, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api-client";
import type { ProfileSeedResult, ProfileStorageStatus } from "@/lib/api-types";

// v3.66.149 (#7/#8) — local-only browser-profile tools on the site page.
//
//   - #8 status: GET /api/sites/<sid>/profile/status — per profile
//     (manual / main / worker / keeper) shows how many login-continuity
//     items (Cookies, Local Storage, IndexedDB, …) are present. Metadata
//     only; never cookie/token/storage VALUES.
//   - #7 seed: POST /api/sites/<sid>/profile/seed — copies the manual-login
//     session into the runtime profiles (LOCK-skipped, keepers guarded,
//     backup-before-overwrite). Local-only, same-machine.

const KIND_ORDER: Record<string, number> = {
  manual: 0,
  main: 1,
  worker: 2,
  keeper: 3,
};

export function ProfileCard({ siteId }: { siteId: string }) {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<ProfileStorageStatus>({
    queryKey: ["profile-status", siteId],
    queryFn: ({ signal }) =>
      apiGet<ProfileStorageStatus>(
        `/api/sites/${encodeURIComponent(siteId)}/profile/status`,
        signal,
      ),
    refetchOnWindowFocus: false,
  });

  const seedMut = useMutation<ProfileSeedResult, Error, void>({
    mutationFn: () =>
      apiPost<ProfileSeedResult>(
        `/api/sites/${encodeURIComponent(siteId)}/profile/seed`,
        {},
      ),
    onSuccess: (res) => {
      if (res.skipped_reason) {
        toast(res.skipped_reason);
      } else if (res.seeded.length) {
        toast.success(
          `Seeded ${res.seeded.length} profile(s): ${res.seeded
            .map((s) => s.profile)
            .join(", ")}`,
        );
      } else {
        toast("Nothing copied — runtime profiles already current");
      }
      qc.invalidateQueries({ queryKey: ["profile-status", siteId] });
    },
    onError: (err) => toast.error(`Seed failed: ${err.message}`),
  });

  const profiles = [...(data?.sites?.[0]?.profiles ?? [])].sort(
    (a, b) => (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9),
  );
  const manual = profiles.find((p) => p.kind === "manual");
  const manualReady = !!manual?.present;

  return (
    <Card className="hairline border bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex items-center gap-2">
            {manualReady ? (
              <ShieldCheck className="h-4 w-4 text-green" aria-hidden />
            ) : (
              <ShieldAlert className="h-4 w-4 text-ink-3" aria-hidden />
            )}
            <span className="eyebrow">
              Profiles
            </span>
          </div>
          <div className="truncate text-sm font-medium text-ink">
            {isLoading
              ? "Checking profile storage…"
              : manualReady
                ? "Manual login present — workers can be seeded"
                : "No manual-login profile yet"}
          </div>

          {!isLoading && profiles.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {profiles.map((p) => {
                const present = p.items.filter((i) => i.present).length;
                const ok = present > 0;
                return (
                  <span
                    key={p.profile}
                    title={p.items
                      .filter((i) => i.present)
                      .map((i) => i.name)
                      .join(", ")}
                    className={
                      "rounded px-1.5 py-0.5 text-[11px] tabular-nums " +
                      (ok
                        ? "bg-green-soft text-green"
                        : "bg-ink-1/40 text-ink-3")
                    }
                  >
                    {p.profile} · {present}
                  </span>
                );
              })}
            </div>
          )}
          {!isLoading && profiles.length === 0 && (
            <div className="mt-1 text-xs text-ink-3">
              No profiles on disk for this site yet.
            </div>
          )}
        </div>

        <Button
          size="sm"
          variant="outline"
          onClick={() => seedMut.mutate()}
          disabled={seedMut.isPending || isLoading || !manualReady}
          aria-label="Seed worker profiles from the manual login (local-only)"
        >
          <HardDriveDownload className="h-3.5 w-3.5" aria-hidden />
          {seedMut.isPending ? "Seeding…" : "Seed workers"}
        </Button>
      </div>
    </Card>
  );
}
