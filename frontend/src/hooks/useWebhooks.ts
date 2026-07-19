import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPost } from "@/lib/api-client";

// v3.66.731 — the webhooks CONTROL cluster.
//
// The blueprint (bulk_downloader/app_webhooks.py) has existed since v3.66.405
// and NOTHING in the SPA could reach it: /api/webhooks (GET, POST),
// /api/webhooks/<wid> (DELETE) and /api/webhooks/drain (POST) were all
// GUI-dark. Registering a webhook meant curling the API by hand.

/** A subscription as the API returns it. `secret` arrives REDACTED ("<N chars>")
 *  -- list_subscriptions() never emits the real value, and this surface must
 *  never try to recover it. */
export interface WebhookSubscription {
  id: number;
  url: string;
  events: string[];
  secret?: string;
  created_at?: number;
}

export function useWebhooks() {
  return useQuery<{ subscriptions: WebhookSubscription[] }, Error>({
    queryKey: ["webhooks", "list"],
    queryFn: ({ signal }) =>
      apiGet<{ subscriptions: WebhookSubscription[] }>("/api/webhooks", signal),
    retry: 0,
  });
}

export function useWebhooksStats() {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["webhooks", "stats"],
    queryFn: ({ signal }) => apiGet<Record<string, unknown>>("/api/webhooks/stats", signal),
    retry: 0,
  });
}

export function useAddWebhook() {
  const qc = useQueryClient();
  return useMutation<
    { ok: boolean; id?: number; error?: string },
    Error,
    { url: string; events: string[]; secret?: string }
  >({
    mutationFn: (body) =>
      apiPost<{ ok: boolean; id?: number; error?: string }>("/api/webhooks", {
        url: body.url,
        events: body.events,
        secret: body.secret ?? "",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
}

export function useRemoveWebhook() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean }, Error, number>({
    mutationFn: (wid) => apiDelete<{ ok: boolean }>(`/api/webhooks/${wid}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
}

export function useDrainWebhooks() {
  const qc = useQueryClient();
  return useMutation<Record<string, unknown>, Error, void>({
    mutationFn: () => apiPost<Record<string, unknown>>("/api/webhooks/drain", {}),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
}
