// useCluster — T8 (v3.66.211) cluster wiring: fed · edge_deploy · pair.
//
// Carries all eight endpoint families as full /api/ literals so
// gui_parity_inventory.py sees the SPA consumers (inline ${x} only on a
// true path param). Handler-correct shapes re-derived from
// bulk_downloader/app.py at 210:
//
//   GET  /api/fed/peers              {peers:[{instance_id,base_url,version,
//                                     hostname,last_seen_ts,last_history_id}]}
//   GET  /api/fed/status             {peers_active,active_claims,
//                                     last_expire_run_ts}
//   GET  /api/fed/sync_pull?since_id&limit  {rows:[...]}  ← side-effecting
//                                     pull; wired as a MUTATION (never a
//                                     query) so react-query cannot auto-fire
//                                     it on mount / focus / interval.
//   POST /api/fed/manual_register    {instance_id,base_url,version?,hostname?}
//                                     → {ok} | 400/500 {ok:false,error}.
//                                     CSRF. Federation TRUST boundary →
//                                     page gates it A-tier (No-default).
//   POST /api/edge_deploy/compose    body {image?,port?,install_dir?,
//                                     downloads_dir?,tz?,with_qbittorrent?,
//                                     with_flaresolverr?,with_vpn?}
//                                     → {ok,yaml}. CSRF. Pure compute (B).
//   POST /api/edge_deploy/all        same body → {ok,artifacts:{name:text}}.
//                                     CSRF. FLEET-WIDE fan-out → A-tier.
//   GET  /api/pair                   {ok,url,base_url,lan_ip,port,token,
//                                     qr_svg,qr_error}. token is the 5-min
//                                     one-shot pairing credential, shown as a
//                                     QR by design (not stored, not echoed
//                                     back into any input).
//   POST /api/pair/redeem            {token} → {ok,csrf_token,expires_in}
//                                     | 400/404/410. AUTH surface: the token
//                                     is a write-only (R) secret input —
//                                     masked, never seeded from a GET, cleared
//                                     after submit.
//
// Nothing here is one-click: every write arms a page-level Pending and
// dispatches from a confirm dialog (A or B tier per the table above).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  EdgeAllResult,
  EdgeComposeBody,
  EdgeComposeResult,
  FedManualRegisterBody,
  FedPeersResponse,
  FedStatus,
  FedSyncPullResult,
  PairInfo,
  PairRedeemResult,
} from "@/lib/api-types";

export function useFedPeers() {
  return useQuery<FedPeersResponse, Error>({
    queryKey: ["fed", "peers"],
    queryFn: ({ signal }) => apiGet<FedPeersResponse>("/api/fed/peers", signal),
  });
}

export function useFedStatus() {
  return useQuery<FedStatus, Error>({
    queryKey: ["fed", "status"],
    refetchInterval: 30_000,
    queryFn: ({ signal }) => apiGet<FedStatus>("/api/fed/status", signal),
  });
}

/** sync_pull is a GET with a peer-pull side-effect. Wired as a MUTATION so
 *  it only fires on an explicit "Pull now" tap — never on mount/focus. */
export function useFedSyncPull() {
  const qc = useQueryClient();
  return useMutation<FedSyncPullResult, Error, { sinceId?: number; limit?: number }>({
    mutationFn: ({ sinceId = 0, limit = 500 }) =>
      apiGet<FedSyncPullResult>(
        `/api/fed/sync_pull?since_id=${sinceId}&limit=${limit}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fed", "status"] });
    },
  });
}

/** Federation trust boundary — A-tier confirm at the page. */
export function useFedManualRegister() {
  const qc = useQueryClient();
  return useMutation<{ ok?: boolean; error?: string }, Error, FedManualRegisterBody>({
    mutationFn: (body) => apiPost<{ ok?: boolean; error?: string }>("/api/fed/manual_register", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fed", "peers"] });
      qc.invalidateQueries({ queryKey: ["fed", "status"] });
    },
  });
}

/** Set a peer's trust tier (C7 11.2). Operator action; a blocked peer is refused
 *  download coordination. */
export function useFedSetTrust() {
  const qc = useQueryClient();
  return useMutation<
    { ok?: boolean; error?: string },
    Error,
    { instance_id: string; tier: string }
  >({
    mutationFn: (body) =>
      apiPost<{ ok?: boolean; error?: string }>("/api/fed/set_trust", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fed", "peers"] });
      qc.invalidateQueries({ queryKey: ["fed", "status"] });
    },
  });
}

export interface FedPendingTemplate {
  id: number;
  from_instance: string;
  site_id: string;
  received_ts: number;
  status: string;
}
export interface FedPendingResponse {
  pending: FedPendingTemplate[];
}

/** Pending peer templates awaiting operator review (C7-11.2 template
 *  federation). Read-only list. */
export function useFedPendingTemplates() {
  return useQuery<FedPendingResponse, Error>({
    queryKey: ["fed", "pending_templates"],
    queryFn: ({ signal }) =>
      apiGet<FedPendingResponse>("/api/fed/pending_templates", signal),
  });
}

/** Approve or reject a pending peer template. Approve writes it
 *  NON-DESTRUCTIVELY into the template store (fed_<peer>_<host>) — a template
 *  TRUST action, so the page gates it with an A-tier confirm. */
export function useFedPendingReview() {
  const qc = useQueryClient();
  return useMutation<
    { ok?: boolean; error?: string; applied?: boolean },
    Error,
    { id: number; action: "approve" | "reject" }
  >({
    mutationFn: (body) =>
      apiPost<{ ok?: boolean; error?: string; applied?: boolean }>(
        "/api/fed/pending_review",
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fed", "pending_templates"] });
    },
  });
}

/** Pure compute (generates a compose file) — B-tier. */
export function useEdgeCompose() {
  return useMutation<EdgeComposeResult, Error, EdgeComposeBody>({
    mutationFn: (body) => apiPost<EdgeComposeResult>("/api/edge_deploy/compose", body),
  });
}

/** Fleet-wide artifact fan-out — A-tier confirm at the page. */
export function useEdgeAll() {
  return useMutation<EdgeAllResult, Error, EdgeComposeBody>({
    mutationFn: (body) => apiPost<EdgeAllResult>("/api/edge_deploy/all", body),
  });
}

/** Generate a one-time pairing code + QR. B-tier single-tap; the returned
 *  token is shown as a QR by design and is never written into an input. */
export function useGeneratePairing() {
  return useMutation<PairInfo, Error, void>({
    mutationFn: () => apiGet<PairInfo>("/api/pair"),
  });
}

/** Redeem a pairing token → mints a session. The token is a write-only (R)
 *  secret supplied by the caller; this hook never persists or echoes it. */
export function useRedeemPairing() {
  return useMutation<PairRedeemResult, Error, { token: string }>({
    mutationFn: ({ token }) => apiPost<PairRedeemResult>("/api/pair/redeem", { token }),
  });
}
