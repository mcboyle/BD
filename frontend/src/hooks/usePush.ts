// usePush — T9b (v3.66.213) web-push wiring: info · subscribe · test ·
// unsubscribe. The LAST wireable legacy-parity tranche. Replaces the legacy
// static/app.js push button (refreshPushStatus/togglePush) + static/pwa.js.
//
// Carries all four push endpoint families as full /api/ literals so
// gui_parity_inventory.py sees the SPA consumers. Shapes re-derived from
// bulk_downloader/app.py :: /api/push/* and bulk_downloader/push.py at 212:
//
//   GET  /api/push/info        {available, public_key, error?}. available is
//                              false when crypto/pywebpush is missing → the
//                              section hides the enable control. useQuery.
//   POST /api/push/subscribe   body = PushSubscription.toJSON()
//                              {endpoint, keys:{p256dh, auth}} → {ok}. CSRF.
//                              useMutation (never auto-fires; armed by the
//                              user toggling the section ON).
//   POST /api/push/test        {} → {ok, sent, failed, throttled}. CSRF.
//                              useMutation, B-tier confirm at the section.
//   POST /api/push/unsubscribe {endpoint} → {ok}. CSRF. useMutation.
//
// SUBSCRIPTION SURVIVAL (the hard gate T9 was split for): a PushSubscription
// is bound to its service-worker REGISTRATION SCOPE, not the script URL. The
// SPA registers the SAME root-scope `/sw.js` the legacy UI used (see
// main.tsx) → the browser treats it as an UPDATE of the existing scope-`/`
// registration, so getSubscription() returns the legacy subscription with an
// UNCHANGED endpoint. enable() reuses it and NEVER re-subscribes when one
// exists → the endpoint (and the server's push_subscriptions row keyed by it)
// is preserved across the legacy → SPA cutover. We only ever call
// pushManager.subscribe() for a brand-new subscriber, always with the same
// server VAPID applicationServerKey from /api/push/info.

import { useMutation, useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  PushInfo,
  PushSubscribeResult,
  PushTestResult,
  PushUnsubscribeResult,
} from "@/lib/api-types";

export function pushSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window !== "undefined" &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Server VAPID public key (base64url) → the Uint8Array PushManager wants.
 *  Allocates a concrete ArrayBuffer (not the default ArrayBufferLike) so the
 *  result satisfies BufferSource for PushManager.subscribe under strict DOM
 *  lib typings. */
function urlBase64ToUint8Array(b64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (b64.length % 4)) % 4);
  const base64 = (b64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

/**
 * Return the existing PushSubscription (legacy or prior-SPA) WITHOUT ever
 * re-subscribing — the survival read. Resolves null when push is
 * unsupported or no subscription exists.
 */
export async function getExistingSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

/**
 * Produce the PushSubscription JSON to POST to /api/push/subscribe.
 *
 * Survival-first: if a subscription already exists (the legacy user case),
 * REUSE it as-is — do not unsubscribe + re-subscribe, which would mint a new
 * endpoint and orphan the server row. Only mint a fresh subscription for a
 * brand-new subscriber, and only after an explicit permission grant, using
 * the server's stable VAPID key.
 */
export async function buildSubscriptionForEnable(
  publicKey: string,
): Promise<PushSubscriptionJSON> {
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("permission denied");
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });
  }
  return sub.toJSON() as PushSubscriptionJSON;
}

/** Browser-side unsubscribe; returns the endpoint to tombstone server-side. */
export async function unsubscribeLocally(): Promise<string | null> {
  const sub = await getExistingSubscription();
  if (!sub) return null;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  return endpoint;
}

export function usePushInfo() {
  return useQuery<PushInfo, Error>({
    queryKey: ["push", "info"],
    queryFn: ({ signal }) => apiGet<PushInfo>("/api/push/info", signal),
  });
}

/** Register a (reused or freshly minted) subscription. Never auto-fires. */
export function usePushSubscribe() {
  return useMutation<PushSubscribeResult, Error, PushSubscriptionJSON>({
    mutationFn: (sub) =>
      apiPost<PushSubscribeResult>("/api/push/subscribe", sub),
  });
}

/** Tombstone a subscription by endpoint. Never auto-fires. */
export function usePushUnsubscribe() {
  return useMutation<PushUnsubscribeResult, Error, { endpoint: string }>({
    mutationFn: (body) =>
      apiPost<PushUnsubscribeResult>("/api/push/unsubscribe", body),
  });
}

/** Send a test push to all subscribers. B-tier confirm at the section. */
export function usePushTest() {
  return useMutation<PushTestResult, Error, void>({
    mutationFn: () => apiPost<PushTestResult>("/api/push/test", {}),
  });
}
