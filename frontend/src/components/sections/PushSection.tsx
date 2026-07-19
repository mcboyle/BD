// PushSection — T9b (v3.66.213) web-push surface, mounted in the existing
// /notifications route alongside Apprise · Telegram · Alerts. Replaces the
// legacy static/app.js push button. Enable/disable toggles the browser
// subscription; "Send test" is a B-tier confirm (fans a notification out to
// every subscriber). No secret inputs: the VAPID public key is not a secret,
// and subscription endpoints/keys are never rendered.
//
// Survival: enable() reuses any existing subscription (legacy or prior SPA)
// and never re-subscribes when one exists, so the legacy endpoint — and its
// server row — is preserved across the SW-registration cutover (see usePush).
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  buildSubscriptionForEnable,
  getExistingSubscription,
  pushSupported,
  unsubscribeLocally,
  usePushInfo,
  usePushSubscribe,
  usePushTest,
  usePushUnsubscribe,
} from "@/hooks/usePush";

function errText(e: unknown): string {
  return e && typeof e === "object" && "message" in e
    ? String((e as Error).message)
    : String(e);
}

export function PushSection() {
  const info = usePushInfo();
  const subscribe = usePushSubscribe();
  const unsubscribe = usePushUnsubscribe();
  const test = usePushTest();

  const supported = pushSupported();
  const available = !!info.data?.available;
  const publicKey = info.data?.public_key || "";

  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmTest, setConfirmTest] = useState(false);

  // Reflect the current browser subscription state on mount — a READ only
  // (getSubscription never mints), so a legacy subscriber shows ON without
  // any re-subscribe.
  useEffect(() => {
    let live = true;
    (async () => {
      if (!supported) {
        if (live) setSubscribed(false);
        return;
      }
      try {
        const sub = await getExistingSubscription();
        if (live) setSubscribed(!!sub);
      } catch {
        if (live) setSubscribed(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [supported]);

  const enable = async () => {
    setBusy(true);
    try {
      const subJson = await buildSubscriptionForEnable(publicKey);
      const r = await subscribe.mutateAsync(subJson);
      if (r.ok) {
        setSubscribed(true);
        toast.success("Notifications enabled");
      } else {
        toast.error("Subscribe failed");
      }
    } catch (e) {
      toast.error(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      const endpoint = await unsubscribeLocally();
      if (endpoint) {
        await unsubscribe.mutateAsync({ endpoint });
      }
      setSubscribed(false);
      toast.success("Notifications disabled");
    } catch (e) {
      toast.error(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const doTest = () => {
    setConfirmTest(false);
    test.mutate(undefined, {
      onSuccess: (r) =>
        r.ok
          ? toast.success(`Test sent (${r.sent ?? 0})`)
          : toast.error("No subscribers received the test"),
      onError: (e) => toast.error(errText(e)),
    });
  };

  return (
    <Card className="mt-4 p-4">
      <h2 className="mb-2 text-sm font-semibold">Web push notifications</h2>
      {!supported ? (
        <p className="text-xs text-muted-foreground">
          This browser does not support web push.
        </p>
      ) : !available ? (
        <p className="text-xs text-muted-foreground">
          Push is unavailable on the server (VAPID key or pywebpush missing).
        </p>
      ) : (
        <>
          <p className="mb-3 text-xs text-muted-foreground">
            {subscribed === null
              ? "Checking subscription…"
              : subscribed
                ? "This device is subscribed. Existing subscriptions are preserved."
                : "Get a notification on this device when downloads finish."}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {subscribed ? (
              <Button variant="ghost" disabled={busy} onClick={disable}>
                Disable on this device
              </Button>
            ) : (
              <Button disabled={busy || subscribed === null} onClick={enable}>
                Enable on this device
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={test.isPending}
              onClick={() => setConfirmTest(true)}
            >
              Send test
            </Button>
          </div>
        </>
      )}

      {/* B-tier test confirm — fans a notification to every subscriber */}
      <Dialog open={confirmTest} onOpenChange={setConfirmTest}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send a test notification?</DialogTitle>
            <DialogDescription>
              This sends a test push to every subscribed device, not just this
              one.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmTest(false)}>
              Cancel
            </Button>
            <Button onClick={doTest}>Send test</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
