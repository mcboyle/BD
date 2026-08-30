// SiteSettings — schema-driven per-site settings editor (CLI->GUI parity Phase 4.1,
// v3.66.310). Renders a control for EVERY editable per-site key the backend exposes:
//   - field_meta  : gui-safe fields (current values round-trip)
//   - gated_meta  : secrets / login / selector / relogin (secrets are presence-only —
//                   masked write-only inputs, preserve-on-blank; never a value on the wire)
// Reads  GET  /api/settings/site/<sid>/editable   (descriptor surface)
// Writes PUT  /api/sites/<sid>                     (app.api_update — validates, range-
//                                                   backstops, preserve-on-blanks secrets,
//                                                   and audits the change server-side)
// High-risk changes (any secret / gated field) are gated behind a confirm dialog.
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { Save } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { CookieClipboardPanel } from "@/components/ui/CookieClipboardPanel";
import { KnowledgeNotesPanel } from "@/components/ui/KnowledgeNotesPanel";
import { SettingRow, SettingSection } from "@/components/SettingSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PathValidator } from "@/components/PathValidator";
import { SecretField } from "@/components/SecretField";
import { CopyButton } from "@/components/ui/CopyButton";
import { buildSiteConfigClipboard } from "@/lib/copySiteConfig";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPut } from "@/lib/api-client";
import { cn } from "@/lib/utils";

interface FieldDescriptor {
  key: string;
  category: string;
  secret: boolean;
  preserve_on_blank: boolean;
  type: string; // boolean|integer|number|bool|int|str|string|enum
  enum?: string[] | null; // explicit choices when type === "enum" (e.g. backend)
  description: string;
  range: [number, number] | null;
  required: boolean;
  current: unknown; // scalar, or {present:boolean} for secrets / structured fields
}

interface EditableResponse {
  ok: boolean;
  sid: string;
  field_meta: Record<string, FieldDescriptor>;
  groups: Record<string, FieldDescriptor[]>;
  gated_meta: Record<string, FieldDescriptor>;
  gated_groups: Record<string, FieldDescriptor[]>;
}

// v3.66.336: one selectable VPN tunnel for the vpn_tunnel_id dropdown.
interface VpnTunnelOption {
  tunnel_id: string;
  name?: string;
}

const isBool = (t: string) => t === "bool" || t === "boolean";
const isNum = (t: string) => t === "int" || t === "integer" || t === "number";
const isSecretPresent = (d: FieldDescriptor) =>
  d.secret && typeof d.current === "object" && d.current !== null &&
  (d.current as { present?: boolean }).present === true;

const CAPTCHA_PROVIDER_DISCLOSURES: Record<string, {
  name: string;
  termsUrl: string;
  pricingUrl: string;
  cost: string;
}> = {
  "2captcha": {
    name: "2Captcha",
    termsUrl: "https://2captcha.com/terms-of-service",
    pricingUrl: "https://2captcha.com/pricing",
    cost: "$0.001–$0.00299 per solve for published reCAPTCHA prices; Turnstile is $0.00145 per solve",
  },
  capsolver: {
    name: "CapSolver",
    termsUrl: "https://www.capsolver.com/legal/terms",
    pricingUrl: "https://docs.capsolver.com/en/pricing/",
    cost: "$0.0008–$0.0012 per solve for published reCAPTCHA and Turnstile prices",
  },
};

export function SiteSettings() {
  const { siteId = "" } = useParams();
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery<EditableResponse>({
    queryKey: ["site-editable", siteId],
    queryFn: ({ signal }) =>
      apiGet<EditableResponse>(
        `/api/settings/site/${encodeURIComponent(siteId)}/editable`,
        signal,
      ),
    enabled: !!siteId,
    refetchOnWindowFocus: false,
  });

  // v3.66.336: tunnel choices for the vpn_tunnel_id <select> (was free-text).
  // GET /api/vpn/tunnels -> {ok, tunnels:[{tunnel_id,name,...}]}. Fail-soft: an
  // empty/failed list just yields no extra options (the current value is always
  // preserved as an option below).
  const tunnelsQ = useQuery<{ ok: boolean; tunnels: VpnTunnelOption[] }>({
    queryKey: ["vpn-tunnels"],
    queryFn: ({ signal }) =>
      apiGet<{ ok: boolean; tunnels: VpnTunnelOption[] }>("/api/vpn/tunnels", signal),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const tunnels = tunnelsQ.data?.tunnels ?? [];

  // Draft of user edits, keyed by field. Absent => unchanged. Secrets start empty
  // (blank => preserve-on-blank, never sent).
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [confirmOpen, setConfirmOpen] = useState(false);

  const allMeta = useMemo<Record<string, FieldDescriptor>>(
    () => ({ ...(data?.field_meta ?? {}), ...(data?.gated_meta ?? {}) }),
    [data],
  );

  // Cut 6.7 — copy-whole-site-config. Serialise the non-secret field values for
  // the clipboard. Secret-flagged fields are dropped here (authoritative meta
  // flag), and buildSiteConfigClipboard redacts by key name as a second net.
  const configCopy = useMemo(() => {
    const obj: Record<string, unknown> = { site_id: siteId };
    for (const [k, d] of Object.entries(data?.field_meta ?? {})) {
      if (d.secret) continue;
      obj[k] = d.current;
    }
    return buildSiteConfigClipboard(obj);
  }, [data, siteId]);

  const saveMut = useMutation<unknown, Error, Record<string, unknown>>({
    mutationFn: (patch) =>
      apiPut(`/api/sites/${encodeURIComponent(siteId)}`, patch),
    onSuccess: () => {
      toast.success("Site settings saved");
      setDraft({});
      setConfirmOpen(false);
      qc.invalidateQueries({ queryKey: ["site-editable", siteId] });
    },
    onError: (e) => toast.error(e.message || "Save failed"),
  });

  const setField = (key: string, v: unknown) =>
    setDraft((d) => ({ ...d, [key]: v }));

  // Build the PUT payload: only changed fields; blank secrets dropped (preserve-on-blank).
  const buildPatch = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(draft)) {
      const d = allMeta[key];
      if (d?.secret && (v === "" || v == null)) continue; // preserve-on-blank
      out[key] = v;
    }
    return out;
  };

  const patch = buildPatch();
  const dirty = Object.keys(patch).length > 0;
  const touchesSensitive = Object.keys(patch).some(
    (k) => allMeta[k]?.secret || !!data?.gated_meta?.[k],
  );
  const changesCaptchaKey = Object.prototype.hasOwnProperty.call(patch, "captcha_api_key");
  const movesConfiguredCaptchaProvider =
    Object.prototype.hasOwnProperty.call(patch, "captcha_provider") &&
    !!allMeta.captcha_api_key && isSecretPresent(allMeta.captcha_api_key);
  const enablesCaptchaEgress = changesCaptchaKey || movesConfiguredCaptchaProvider;
  const selectedCaptchaProvider = String(
    patch.captcha_provider ?? allMeta.captcha_provider?.current ?? "2captcha",
  ).trim().toLowerCase();
  const captchaDisclosure = CAPTCHA_PROVIDER_DISCLOSURES[selectedCaptchaProvider] ?? {
    name: selectedCaptchaProvider || "selected provider",
    termsUrl: "",
    pricingUrl: "",
    cost: "Provider-controlled pricing varies per solve; review the provider's current price before enabling.",
  };

  const onSave = () => {
    if (!dirty) return;
    if (touchesSensitive) setConfirmOpen(true);
    else saveMut.mutate(patch);
  };

  const renderControl = (d: FieldDescriptor) => {
    if (d.secret) {
      return (
        <SecretField
          value={(draft[d.key] as string) ?? ""}
          placeholder={isSecretPresent(d) ? "•••••••• (set — leave blank to keep)" : "not set"}
          onChange={(v) => setField(d.key, v)}
          ariaLabel={d.key}
          className="w-64"
        />
      );
    }
    if (isBool(d.type)) {
      const cur = d.key in draft ? !!draft[d.key] : !!d.current;
      return <Switch checked={cur} onChange={(v) => setField(d.key, v)} ariaLabel={d.key} />;
    }
    if (isNum(d.type)) {
      const cur = d.key in draft ? (draft[d.key] as number) : (d.current as number) ?? 0;
      const [lo, hi] = d.range ?? [undefined, undefined];
      return (
        <Input
          type="number"
          min={lo}
          max={hi}
          value={cur}
          onChange={(e) => {
            let n = parseFloat(e.target.value);
            if (Number.isNaN(n)) n = lo ?? 0;
            if (lo != null) n = Math.max(lo, n);
            if (hi != null) n = Math.min(hi, n);
            setField(d.key, n);
          }}
          className="w-28 text-right tabular"
        />
      );
    }
    // v3.66.336: vpn_tunnel_id renders as a <select> of registered tunnels
    // (GET /api/vpn/tunnels) instead of free text. "(none)" clears the binding;
    // an already-set id that isn't in the current list is preserved as an option
    // so a stale-but-valid value is never silently dropped.
    if (d.key === "vpn_tunnel_id") {
      const cur = d.key in draft ? (draft[d.key] as string) : ((d.current as string) ?? "");
      const known = new Set(tunnels.map((t) => t.tunnel_id));
      return (
        <select
          value={cur}
          onChange={(e) => setField(d.key, e.target.value)}
          aria-label="vpn_tunnel_id"
          className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value="">(none)</option>
          {cur && !known.has(cur) && (
            <option value={cur}>{cur} (not in list)</option>
          )}
          {tunnels.map((t) => (
            <option key={t.tunnel_id} value={t.tunnel_id}>
              {t.name ? `${t.name} (${t.tunnel_id})` : t.tunnel_id}
            </option>
          ))}
        </select>
      );
    }
    // v3.66.468 WS4b: enum-typed fields (e.g. `backend`: teach/jd/qb) render
    // as a <select> of their declared choices instead of free text, so the
    // JD/qB backends are discoverable + switchable from the UI. A current
    // value that isn't in the list is preserved as an option.
    if (d.type === "enum" && Array.isArray(d.enum) && d.enum.length > 0) {
      const cur = d.key in draft ? (draft[d.key] as string) : ((d.current as string) ?? "");
      const known = new Set(d.enum);
      return (
        <select
          value={cur}
          onChange={(e) => setField(d.key, e.target.value)}
          aria-label={d.key}
          className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
        >
          {cur && !known.has(cur) && <option value={cur}>{cur} (not in list)</option>}
          {d.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      );
    }
    if (d.key === "download_dir") {
      const cur = d.key in draft ? (draft[d.key] as string) : ((d.current as string) ?? "");
      return (
        <div className="space-y-1.5">
          <Input
            type="text"
            value={cur}
            onChange={(e) => setField(d.key, e.target.value)}
            className="w-64"
            placeholder={d.preserve_on_blank ? "leave blank to keep" : ""}
          />
          {/* Cut 4: read-only path diagnosis — never creates the directory. */}
          <PathValidator path={cur} />
        </div>
      );
    }
    const cur = d.key in draft ? (draft[d.key] as string) : ((d.current as string) ?? "");
    return (
      <Input
        type="text"
        value={cur}
        onChange={(e) => setField(d.key, e.target.value)}
        className="w-64"
        placeholder={d.preserve_on_blank ? "leave blank to keep" : ""}
      />
    );
  };

  // v3.66.326: collapse the per-site groups. The two everyday categories
  // open by default; everything else (and all sensitive/credential groups)
  // folds closed so the editor isn't a wall of fields.
  const ESSENTIAL_CATEGORIES = new Set(["download/perf", "general"]);
  const renderSection = (
    label: string,
    desc: FieldDescriptor[],
    sensitive: boolean,
  ) => (
    <SettingSection
      key={label}
      collapsible
      defaultOpen={!sensitive && ESSENTIAL_CATEGORIES.has(label)}
      label={sensitive ? `${label} (sensitive)` : label}
      description={
        sensitive
          ? "Credentials, login, and selectors. Secrets are write-only — blank keeps the stored value."
          : undefined
      }
    >
      {[...desc]
        .sort((a, b) => a.key.localeCompare(b.key))
        .map((d) => (
          <SettingRow
            key={d.key}
            label={d.key}
            hint={d.description || undefined}
            control={renderControl(d)}
            stacked={!isBool(d.type) && !isNum(d.type)}
          />
        ))}
    </SettingSection>
  );

  return (
    <AppShell
      title={`Site settings — ${siteId}`}
      subtitle="Every editable per-site field · secrets write-only · saved with audit"
      backTo={{ to: `/sites/${siteId}`, label: "Back to site" }}
      breadcrumb={`Sites › ${siteId} › Settings`}
    >
      {isLoading && <Skeleton className="h-64 w-full" />}
      {isError && (
        <div role="alert" className="hairline rounded-md bg-surface p-4 text-sm text-ink-2">
          Could not load editable fields for this site.
        </div>
      )}

      {data && (
        <>
          <div className="sticky top-0 z-10 mb-3 flex items-center justify-end gap-2 bg-bg/80 py-2 backdrop-blur">
            {dirty && (
              <span className="text-xs text-ink-2">
                {Object.keys(patch).length} change(s) pending
              </span>
            )}
            <Button onClick={onSave} disabled={!dirty || saveMut.isPending}>
              <Save className="mr-1.5 h-4 w-4" />
              {saveMut.isPending ? "Saving…" : "Save"}
            </Button>
            <CopyButton value={configCopy} label="Copy site config (secrets omitted)" />
          </div>

          {Object.entries(data.groups ?? {})
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([cat, desc]) => renderSection(cat, desc, false))}

          {Object.entries(data.gated_groups ?? {})
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([cat, desc]) => renderSection(cat, desc, true))}
        </>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {enablesCaptchaEgress
                ? "Enable paid third-party captcha solving"
                : "Confirm sensitive change"}
            </DialogTitle>
            {enablesCaptchaEgress ? (
              <DialogDescription asChild>
                <div className="space-y-3 text-sm">
                  <p>
                    Enabling {captchaDisclosure.name} sends the target page URL,
                    captcha site key, challenge type, and reCAPTCHA action when present
                    to that third party. Your solver API credential is sent for billing.
                  </p>
                  <p>
                    Each submitted challenge can incur a charge. Current published cost:
                    {" "}{captchaDisclosure.cost}. Prices vary by challenge and can change;
                    the provider's pricing page is authoritative.
                  </p>
                  <p className="flex flex-wrap gap-x-3 gap-y-1">
                    {captchaDisclosure.termsUrl && (
                      <a
                        href={captchaDisclosure.termsUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline"
                      >
                        {captchaDisclosure.name} terms
                      </a>
                    )}
                    {captchaDisclosure.pricingUrl && (
                      <a
                        href={captchaDisclosure.pricingUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline"
                      >
                        {captchaDisclosure.name} current pricing
                      </a>
                    )}
                  </p>
                </div>
              </DialogDescription>
            ) : (
              <DialogDescription>
                This save changes credential / login / selector fields. Clearing or
                mis-setting a credential can lock the site's authenticated session out
                of the running instance. The change is audited. Continue?
              </DialogDescription>
            )}
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => saveMut.mutate(
                enablesCaptchaEgress
                  ? { ...patch, captcha_egress_disclosure_ack: true }
                  : patch,
              )}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending
                ? "Saving…"
                : enablesCaptchaEgress
                  ? "Acknowledge and enable"
                  : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {siteId ? <CookieClipboardPanel siteId={siteId} /> : null}
      {siteId ? <KnowledgeNotesPanel siteId={siteId} /> : null}
    </AppShell>
  );
}

// Tiny inline Switch (mirrors Settings.tsx). Promote to /components/ui/ if reused widely.
interface SwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  ariaLabel?: string;
}
function Switch({ checked, onChange, ariaLabel }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
        checked ? "bg-primary" : "bg-surface-2",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
  );
}
