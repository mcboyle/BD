import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  useParseCookies,
  useSaveCookies,
  type ParseResult,
} from "@/hooks/useCookieClipboard";

// v3.66.735 — GUI for the cookie_clipboard CONTROL cluster (2 dark endpoints).
//
// Flow: paste a jar -> Parse (read-only preview) -> Save to this site's
// cookie_file. Derived from app_cookie_clipboard.py + cookie_clipboard.py:
//
//  * SAVE SENDS THE RAW TEXT. The endpoint re-parses it and ignores anything
//    else in the body. Handing it the already-parsed cookies would 400 with
//    "could not parse any cookies" while the preview showed a perfect parse.
//    See the header of hooks/useCookieClipboard.ts.
//  * COOKIE VALUES ARE SECRETS. auto_detect_and_parse returns them, and this
//    panel NEVER renders one. Name + domain + flags only; the value is shown as
//    its length, the same shape the webhooks panel uses for its secret. There is
//    no affordance anywhere to reveal it.
//  * Save is destructive-ish (it overwrites the site's existing cookie jar), so
//    it gets a Tier-A confirm. Parse is read-only and gets none.
//  * The panel cannot know whether the site has a cookie_file: that key is
//    secret-classed and excluded from the editable surface on purpose. So Save
//    is NOT pre-gated on it -- we fire and surface the backend's 400 reason,
//    which is actionable ("no cookie_file configured").

function redact(value: string): string {
  const n = (value ?? "").length;
  return n ? `<${n} chars>` : "<empty>";
}

export function CookieClipboardPanel({ siteId }: { siteId: string }) {
  const parse = useParseCookies();
  const save = useSaveCookies();

  const [text, setText] = useState("");
  const [preview, setPreview] = useState<ParseResult | null>(null);
  const [confirmSave, setConfirmSave] = useState(false);
  // The save refusal is an INSTRUCTION ("no cookie_file configured" -> go set
  // one), not a passing notification. A toast evaporates; this persists until
  // the operator changes something.
  const [saveError, setSaveError] = useState<string | null>(null);

  const hasText = text.trim().length > 0;

  const doParse = () => {
    if (!hasText) return; // backend 400s on empty -- do not fire a doomed request
    parse.mutate(text, {
      onSuccess: (r) => {
        setPreview(r);
        if (!r.cookies?.length) {
          toast.error(r.error ?? "No cookies recognized in that text.");
        }
      },
      onError: (e) => toast.error(e.message),
    });
  };

  const doSave = () => {
    setSaveError(null);
    save.mutate(
      { sid: siteId, text }, // RAW TEXT -- the endpoint re-parses it itself
      {
        onSuccess: (r) => {
          if (r.ok) {
            toast.success(`Saved ${r.count ?? 0} cookie(s) to ${siteId}.`);
            setText("");
            setPreview(null);
          } else {
            // e.g. "site has no cookie_file configured" -- actionable, so it
            // stays on screen rather than flashing past in a toast.
            setSaveError(r.error ?? "Could not save the cookies.");
          }
        },
        onError: (e) => setSaveError(e.message),
      },
    );
  };

  const cookies = preview?.cookies ?? [];

  return (
    <Card className="p-4 space-y-3" data-testid="cookie-clipboard-panel">
      <div>
        <h2 className="text-lg font-semibold">Import cookies from clipboard</h2>
        <p className="text-sm text-muted-foreground">
          Paste a cookie jar (Netscape, JSON, cURL, or a Cookie header). Parse to preview,
          then save it to this site.
        </p>
      </div>

      <textarea
        aria-label="Pasted cookie text"
        className="w-full min-h-28 rounded-md border bg-background p-2 text-sm font-mono"
        placeholder="Paste cookies here"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setPreview(null); // a stale preview must never justify a new save
          setSaveError(null);
        }}
      />

      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={!hasText || parse.isPending} onClick={doParse}>
          Parse
        </Button>
        <Button
          size="sm"
          disabled={!hasText || cookies.length === 0 || save.isPending}
          onClick={() => setConfirmSave(true)}
        >
          Save to {siteId}
        </Button>
      </div>

      {saveError && (
        <p className="text-sm text-destructive" role="alert" data-testid="cookie-save-error">
          {saveError}
        </p>
      )}

      {preview && (
        <div className="rounded border p-3 space-y-2" data-testid="cookie-preview">
          <div className="text-sm">
            Detected <span className="font-medium">{preview.format ?? "nothing"}</span> —{" "}
            {preview.count} cookie(s), confidence {preview.confidence}
          </div>
          {cookies.length > 0 && (
            <ul className="text-xs font-mono space-y-1" data-testid="cookie-preview-list">
              {cookies.map((c, i) => (
                <li key={`${c.name}-${i}`} className="flex gap-2">
                  <span className="font-medium">{c.name}</span>
                  <span className="text-muted-foreground">{c.domain ?? ""}</span>
                  {/* the value is a session token -- never rendered */}
                  <span className="text-muted-foreground">{redact(c.value)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmSave}
        target={`the cookie jar for ${siteId}`}
        consequence="This overwrites the site's existing saved cookies."
        onCancel={() => setConfirmSave(false)}
        onConfirm={() => {
          setConfirmSave(false);
          doSave();
        }}
      />
    </Card>
  );
}
