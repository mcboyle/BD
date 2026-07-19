// TemplateAuthoringSection — T10 (v3.66.211) template authoring, mounted in
// the existing /templates route (TemplateManager). extract/refine are pure
// compute (ungated); sandbox fetches a LIVE url → B-tier confirm.
import { useState } from "react";
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
import { Input } from "@/components/ui/input";
import {
  useTemplateExtract,
  useTemplateRefine,
  useTemplateSandbox,
} from "@/hooks/useTemplateAuthoring";

function errText(e: unknown): string {
  return e && typeof e === "object" && "message" in e ? String((e as Error).message) : String(e);
}

export function TemplateAuthoringSection() {
  const extract = useTemplateExtract();
  const refine = useTemplateRefine();
  const sandbox = useTemplateSandbox();

  const [html, setHtml] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [sandboxUrl, setSandboxUrl] = useState("");
  const [confirmSandbox, setConfirmSandbox] = useState(false);

  const runExtract = () =>
    extract.mutate(
      { html, page_url: pageUrl || undefined },
      { onError: (e) => toast.error(errText(e)) },
    );

  const runRefine = () => {
    const tpl = extract.data?.template;
    if (!tpl) {
      toast.error("extract a draft first");
      return;
    }
    refine.mutate(
      { html, template: tpl, candidates: extract.data?.candidates },
      { onError: (e) => toast.error(errText(e)) },
    );
  };

  const runSandbox = () => {
    const tpl = extract.data?.template;
    if (!sandboxUrl.trim() || !tpl) {
      toast.error("need a draft template + a url");
      return;
    }
    setConfirmSandbox(false);
    sandbox.mutate(
      { url: sandboxUrl.trim(), template: tpl, mode: "http" },
      { onError: (e) => toast.error(errText(e)) },
    );
  };

  return (
    <Card className="mt-4 p-4">
      <h2 className="mb-2 font-medium">Template authoring</h2>
      <p className="mb-2 text-xs text-muted-foreground">
        Paste page HTML to extract a draft template, refine it with AI assist, then sandbox it
        against a live URL before saving.
      </p>
      <textarea
        className="mb-2 h-28 w-full rounded border border-border bg-background p-2 font-mono text-xs"
        placeholder="<paste page HTML here>"
        value={html}
        onChange={(e) => setHtml(e.target.value)}
        aria-label="page html"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="max-w-[18rem]"
          placeholder="page url (optional)"
          value={pageUrl}
          onChange={(e) => setPageUrl(e.target.value)}
          aria-label="page url"
        />
        <Button variant="outline" onClick={runExtract} disabled={extract.isPending || !html}>
          Extract draft
        </Button>
        <Button variant="outline" onClick={runRefine} disabled={refine.isPending || !extract.data}>
          Refine (AI)
        </Button>
      </div>

      {extract.data?.template && (
        <div className="mt-3 border-t border-border/40 pt-3">
          <p className="mb-1 text-xs font-medium">Sandbox the draft against a live URL</p>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="max-w-[20rem]"
              placeholder="https://example.com/page"
              value={sandboxUrl}
              onChange={(e) => setSandboxUrl(e.target.value)}
              aria-label="sandbox url"
            />
            <Button
              variant="destructive"
              onClick={() => setConfirmSandbox(true)}
              disabled={sandbox.isPending || !sandboxUrl}
            >
              Run sandbox…
            </Button>
          </div>
          {sandbox.data?.matches && (
            <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
              {JSON.stringify(sandbox.data.matches, null, 2)}
            </pre>
          )}
        </div>
      )}

      <Dialog open={confirmSandbox} onOpenChange={setConfirmSandbox}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Run template sandbox</DialogTitle>
            <DialogDescription>
              Fetch the live URL and apply the draft template's selectors? This makes a network
              request from this host.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmSandbox(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={runSandbox}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
