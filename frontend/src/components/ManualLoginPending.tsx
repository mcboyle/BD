import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiPost } from "@/lib/api-client";
import { cn } from "@/lib/utils";

// fx-im-done-wiring (O1567) — the controls a manual-login takeover asks for.
//
// The takeover browser (and the login status) tell the operator to finish
// logging in and "click I'm Done". This is that button, plus Cancel:
//   • I'm Done → POST /api/sites/<sid>/login_manual_done (captures the
//     takeover's cookies, closes the browser)
//   • Cancel   → POST /api/sites/<sid>/login_manual_cancel (closes it, no
//     cookies)
// Rendered by the Home "Needs attention" banner (manual_login_pending entry)
// and the Site page (sites-v2 row awaiting_manual_login).

export interface ManualLoginPendingProps {
  siteId: string;
  name: string;
}

interface ManualLoginResponse {
  ok: boolean;
  message?: string;
}

export function ManualLoginPending({ siteId, name }: ManualLoginPendingProps) {
  const qc = useQueryClient();
  const mut = useMutation<ManualLoginResponse, Error, "done" | "cancel">({
    mutationFn: (action) =>
      apiPost<ManualLoginResponse>(
        `/api/sites/${encodeURIComponent(siteId)}/login_manual_${action}`,
        {},
      ),
    onSuccess: (data, action) => {
      const what = action === "done" ? "Manual login" : "Manual login cancel";
      if (data.ok) toast.success(data.message || `${what}: ${name}`);
      else toast.error(data.message || `${what} failed for ${name}`);
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
    },
    onError: (err, action) => {
      toast.error(`${action === "done" ? "I'm Done" : "Cancel"} ${name}: ${err.message}`);
    },
  });

  const button = cn(
    "flex-1 rounded-md py-3 px-4 text-sm font-semibold",
    "transition-opacity hover:opacity-90 active:opacity-80",
    "disabled:cursor-not-allowed disabled:opacity-50",
  );

  return (
    <div className="flex gap-2">
      <button
        type="button"
        disabled={mut.isPending}
        onClick={() => mut.mutate("done")}
        aria-label={`I'm Done: save the ${name} login from the takeover browser`}
        className={cn(button, "bg-ink text-surface")}
      >
        I'm Done
      </button>
      <button
        type="button"
        disabled={mut.isPending}
        onClick={() => mut.mutate("cancel")}
        aria-label={`Cancel the ${name} manual login`}
        className={cn(button, "border border-ink/20 text-ink")}
      >
        Cancel
      </button>
    </div>
  );
}
