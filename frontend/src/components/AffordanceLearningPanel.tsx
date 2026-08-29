import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export interface LearnedOption {
  height: number | null;
  container?: string | null;
  format?: string | null;
  size?: string | null;
  label: string;
  href: string;
}

export interface SelectorAttempt {
  selector: string;
  status: "PROVEN" | "MISS" | "MALFORMED" | "FAILED";
  count?: number;
  error?: string;
  role?: string;
  phase?: string;
}

export interface AffordanceResult {
  status: "FOUND" | "UNKNOWN";
  shape: "BAR" | "DROPDOWN" | "UNKNOWN";
  row_selector?: string | null;
  trigger_selector?: string | null;
  url_attribute?: string;
  options: LearnedOption[];
  selector_attempts: SelectorAttempt[];
  selection?: {
    status: string;
    option: LearnedOption | null;
    reason?: string;
  };
  network_evidence?: Array<{
    url: string;
    kind?: string;
    status?: number | null;
    content_type?: string | null;
  }>;
  corroboration?: { status: string; detail: string };
}

export type LearningState =
  | { state: "idle" }
  | { state: "running" }
  | { state: "nothing"; result: AffordanceResult }
  | { state: "found"; result: AffordanceResult }
  | { state: "failed"; error: string };

export type NetworkState =
  | { state: "idle" }
  | { state: "running" }
  | { state: "nothing"; count: 0 }
  | {
      state: "found";
      count: number;
      evidence?: AffordanceResult["network_evidence"];
      runnerEvidence?: AffordanceResult["network_evidence"];
    }
  | { state: "failed"; error: string };

export interface ScenePlan {
  url: string;
  chosen_height: number | null;
  status: string;
  reason?: string;
  selection_status?: string;
}

export type CrawlState =
  | { state: "idle" }
  | { state: "running" }
  | { state: "nothing"; count: 0; reason?: string }
  | { state: "found"; count: number; plans: ScenePlan[] }
  | { state: "failed"; error: string };

interface Props {
  learning: LearningState;
  network: NetworkState;
  crawl: CrawlState;
  qualityPreference: string;
  minResolution: number;
  logNetwork?: boolean;
  onQualityPreferenceChange?: (value: string) => void;
  onMinResolutionChange?: (value: number) => void;
  onLogNetworkChange?: (value: boolean) => void;
  onSavePolicy?: () => void;
  saveAvailable?: boolean;
  onLearn: () => void;
  onCaptureNetwork: () => void;
  onCrawl: () => void;
  onSave: () => void;
}

function optionLabel(option: LearnedOption): string {
  const detail = [option.height ? `${option.height}p` : "height unknown", option.container, option.size]
    .filter(Boolean)
    .join(" · ");
  return `${option.label} — ${detail}`;
}

function learningStatus(state: LearningState): string {
  if (state.state === "found") return `found ${state.result.options.length}`;
  if (state.state === "nothing") return "found nothing";
  return state.state;
}

function networkStatus(state: NetworkState): string {
  if (state.state === "found") return `found ${state.count}`;
  if (state.state === "nothing") return "found nothing";
  return state.state;
}

function crawlStatus(state: CrawlState): string {
  if (state.state === "found") return `found ${state.count}`;
  if (state.state === "nothing") return "found nothing";
  return state.state;
}

function evidencePath(value: string): string {
  const match = String(value || "").match(/https?:\/\/\S+|\/[^\s]+/i);
  if (!match) return "";
  try {
    return new URL(match[0], "https://row363.invalid").pathname;
  } catch {
    return "";
  }
}

export function corroborateDomAndNetwork(
  options: LearnedOption[],
  evidence: NonNullable<AffordanceResult["network_evidence"]>,
): { status: string; detail: string } {
  if (!options.length && !evidence.length) {
    return { status: "NONE", detail: "Neither DOM rows nor media-ish requests were found." };
  }
  if (options.length && !evidence.length) {
    return { status: "DOM_ONLY", detail: "DOM options were found; the latest network capture did not corroborate them." };
  }
  if (!options.length && evidence.length) {
    return { status: "NETWORK_ONLY", detail: "Media-ish requests were found, but no DOM option was learned." };
  }
  const dom = new Set(options.map((row) => evidencePath(row.href)).filter(Boolean));
  const network = new Set(evidence.map((row) => evidencePath(row.url)).filter(Boolean));
  if ([...dom].some((path) => network.has(path))) {
    return { status: "AGREE", detail: "DOM and the latest network capture include the same media path." };
  }
  return {
    status: "DISAGREE",
    detail: "DOM options and the latest network evidence expose different paths; both are retained for review.",
  };
}

export function selectOptionForPolicy(
  options: LearnedOption[],
  qualityPreference: string,
  minResolution: number,
): AffordanceResult["selection"] {
  const ceilings: number[] = [];
  for (const token of qualityPreference.split(",").map((part) => part.trim()).filter(Boolean)) {
    if (/^(best|highest|max)$/i.test(token)) {
      ceilings.push(Number.POSITIVE_INFINITY);
      continue;
    }
    const match = token.match(/^(\d{2,4})(?:p)?$/i);
    if (match) ceilings.push(Number(match[1]));
  }
  if (!ceilings.length) ceilings.push(Number.POSITIVE_INFINITY);
  const ranked = options
    .filter((option): option is LearnedOption & { height: number } =>
      typeof option.height === "number")
    .sort((a, b) => b.height - a.height);
  let eligible: Array<LearnedOption & { height: number }> = [];
  for (const ceiling of ceilings) {
    eligible = ranked.filter((option) => option.height <= ceiling);
    if (eligible.length) break;
  }
  const option = eligible[0];
  if (!option) {
    return {
      status: "NO_OPTION_AT_OR_BELOW_PREFERENCE",
      option: null,
      reason: "No option is at or below quality_preference.",
    };
  }
  if (option.height < minResolution) {
    return {
      status: "BELOW_MIN_RESOLUTION",
      option: null,
      reason: `Best available ${option.height}p is below min_resolution ${minResolution}p; refusing.`,
    };
  }
  return { status: "SELECTED", option, reason: "" };
}

export function AffordanceLearningPanel({
  learning,
  network,
  crawl,
  qualityPreference,
  minResolution,
  logNetwork = false,
  onQualityPreferenceChange,
  onMinResolutionChange,
  onLogNetworkChange,
  onSavePolicy,
  saveAvailable = true,
  onLearn,
  onCaptureNetwork,
  onCrawl,
  onSave,
}: Props) {
  const learned = learning.state === "found" || learning.state === "nothing" ? learning.result : null;
  const effectiveSelection = learned?.options.length
    ? selectOptionForPolicy(learned.options, qualityPreference, minResolution)
    : learned?.selection;
  const refused = !!effectiveSelection && effectiveSelection.status !== "SELECTED";
  const saveEnabled = learning.state === "found" && effectiveSelection?.status === "SELECTED";
  const actionRunning = learning.state === "running" || network.state === "running" || crawl.state === "running";
  const latestCorroboration = network.state === "found" || network.state === "nothing"
    ? corroborateDomAndNetwork(
        learned?.options || [],
        network.state === "found" ? network.evidence || [] : [],
      )
    : null;

  return (
    <Card className="space-y-4 border-l-2 border-l-primary p-4">
      <div>
        <div className="text-[13px] font-medium text-ink">Learn the download affordance</div>
        <p className="text-[12px] text-ink-3">
          Uses the held-open authenticated page. It checks an in-page BAR first,
          then clicks one proven download trigger for a DROPDOWN. Nothing is downloaded.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-[12px] text-ink-3">
          quality_preference
          <Input
            aria-label="quality_preference"
            className="mt-1"
            value={qualityPreference}
            onChange={(event) => onQualityPreferenceChange?.(event.target.value)}
          />
        </label>
        <label className="text-[12px] text-ink-3">
          min_resolution
          <Input
            aria-label="min_resolution"
            className="mt-1"
            type="number"
            min={0}
            value={minResolution}
            onChange={(event) => onMinResolutionChange?.(Math.max(0, Number(event.target.value) || 0))}
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[12px] text-ink-3">
        <label className="inline-flex items-center gap-2">
          <input
            type="checkbox"
            checked={logNetwork}
            onChange={(event) => onLogNetworkChange?.(event.target.checked)}
          />
          Record network responses during later downloads (log_network)
        </label>
        {onSavePolicy ? (
          <Button size="sm" variant="outline" onClick={onSavePolicy}>
            Save policy to site
          </Button>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onLearn} disabled={actionRunning}>
          {learning.state === "running" ? "Learning…" : "Learn from live page"}
        </Button>
        <Button size="sm" variant="outline" onClick={onCaptureNetwork} disabled={actionRunning}>
          {network.state === "running" ? "Capturing…" : "Capture network evidence"}
        </Button>
        <Button size="sm" variant="outline" onClick={onCrawl} disabled={actionRunning}>
          {crawl.state === "running" ? "Crawling…" : "Crawl this listing"}
        </Button>
      </div>
      <div aria-live="polite" className="grid gap-1 text-[11px] text-ink-3 sm:grid-cols-3">
        <span>Learning: {learningStatus(learning)}</span>
        <span>Network: {networkStatus(network)}</span>
        <span>Listing: {crawlStatus(crawl)}</span>
      </div>

      {learning.state === "running" ? <p className="text-[12px]">Learning from the live page…</p> : null}
      {learning.state === "failed" ? (
        <div role="alert" className="rounded bg-red-soft p-2 text-[12px] text-red">
          Learning failed: {learning.error}
        </div>
      ) : null}
      {learning.state === "nothing" ? (
        <div role="status" className="rounded bg-amber-soft p-2 text-[12px] text-amber-dim">
          UNKNOWN — nothing found after checking BAR rows and one-click DROPDOWN candidates.
        </div>
      ) : null}
      {learning.state === "found" && learned ? (
        <div className="space-y-2 text-[12px]">
          <div className="font-medium">
            {`${learned.shape} · ${learned.row_selector || "selector unknown"}`}
            {learned.trigger_selector ? ` · trigger ${learned.trigger_selector}` : null}
            {` · URL attribute ${learned.url_attribute || "href"}`}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="text-ink-3">
                <tr><th className="py-1">Rendered label</th><th>Height</th><th>Format</th><th>Size</th></tr>
              </thead>
              <tbody>
                {learned.options.map((option, index) => (
                  <tr key={`${option.href}-${index}`} title={optionLabel(option)} className="border-t border-hairline">
                    <td className="py-1 pr-2">{option.label}</td>
                    <td>{option.height ? `${option.height}p` : "—"}</td>
                    <td>{option.container || option.format || "—"}</td>
                    <td>{option.size || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {effectiveSelection?.status === "SELECTED" && effectiveSelection.option ? (
            <div className="rounded bg-green-soft p-2">
              Planned: {effectiveSelection.option.height}p, the highest option at or below quality_preference.
            </div>
          ) : null}
          {refused ? (
            <div role="alert" className="rounded bg-red-soft p-2 text-red">
              {effectiveSelection?.reason || `Best option is below min_resolution ${minResolution}p; refusing.`}
            </div>
          ) : null}
        </div>
      ) : null}

      {learned?.selector_attempts?.some((attempt) => attempt.status === "MALFORMED") ? (
        <div className="space-y-1 text-[11px] text-amber-dim">
          {learned.selector_attempts
            .filter((attempt) => attempt.status === "MALFORMED")
            .map((attempt, index) => (
              <div key={`${attempt.selector}-${index}`}>
                MALFORMED <code>{attempt.selector}</code>: {attempt.error}
              </div>
            ))}
        </div>
      ) : null}
      {(learned?.selector_attempts || []).length > 0 ? (
        <details className="text-[11px] text-ink-3">
          <summary>Selector attempts ({learned?.selector_attempts.length})</summary>
          <div className="mt-1 space-y-1">
            {(learned?.selector_attempts || []).map((attempt, index) => (
              <div key={`attempt-${attempt.selector}-${index}`}>
                {attempt.status} · {attempt.phase || "probe"} · <code>{attempt.selector}</code>
                {typeof attempt.count === "number" ? ` · ${attempt.count} match${attempt.count === 1 ? "" : "es"}` : ""}
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <div className="space-y-1 text-[12px]">
        {(learned?.network_evidence || []).length > 0 ? (
          <div>
            <div className="font-medium">
              Capture evidence available to learning: {learned?.network_evidence?.length} media-ish request
              {learned?.network_evidence?.length === 1 ? "" : "s"}
            </div>
            {(learned?.network_evidence || []).map((row, index) => (
              <div key={`learn-${row.url}-${index}`} className="truncate font-mono text-[11px] text-ink-3">
                {row.kind || "media"}: {row.url}
              </div>
            ))}
          </div>
        ) : null}
        {network.state === "running" ? <p>Recording media-ish requests…</p> : null}
        {network.state === "nothing" ? <p>Network evidence found nothing.</p> : null}
        {network.state === "failed" ? <p role="alert">Network capture failed: {network.error}</p> : null}
        {network.state === "found" ? (
          <div>
            <div className="font-medium">Network found {network.count} media-ish request{network.count === 1 ? "" : "s"}</div>
            <div className="text-[11px] text-ink-3">
              Held page capture: {(network.evidence || []).length}
            </div>
            {(network.evidence || []).map((row, index) => (
              <div key={`held-${row.url}-${index}`} className="truncate font-mono text-[11px] text-ink-3">
                {row.kind || "media"}: {row.url}
              </div>
            ))}
            <div className="mt-1 text-[11px] text-ink-3">
              Runner log_network history: {(network.runnerEvidence || []).length}
            </div>
            {(network.runnerEvidence || []).map((row, index) => (
              <div key={`runner-${row.url}-${index}`} className="truncate font-mono text-[11px] text-ink-3">
                {row.kind || "runner_network"}: {row.url}
              </div>
            ))}
          </div>
        ) : null}
        {learned?.corroboration ? (
          <div className={learned.corroboration.status === "DISAGREE" ? "text-amber-dim" : "text-ink-3"}>
            Learning snapshot DOM/network: {learned.corroboration.status} — {learned.corroboration.detail}
          </div>
        ) : null}
        {latestCorroboration ? (
          <div className={latestCorroboration.status === "DISAGREE" ? "text-amber-dim" : "text-ink-3"}>
            Latest DOM/network: {latestCorroboration.status} — {latestCorroboration.detail}
          </div>
        ) : null}
      </div>

      <div className="space-y-1 text-[12px]">
        {crawl.state === "running" ? <p>Crawling pagination and infinite scroll…</p> : null}
        {crawl.state === "nothing" ? (
          <p role="status">
            Listing found nothing — {crawl.reason || "the rendered listing explicitly reports an empty catalog."}
          </p>
        ) : null}
        {crawl.state === "failed" ? <p role="alert">Listing failed: {crawl.error}</p> : null}
        {crawl.state === "found" ? (
          <div className="space-y-1">
            <div className="font-medium">Found {crawl.count} scenes — plans only; no downloads started.</div>
            <div className="max-h-40 overflow-y-auto">
              {crawl.plans.map((plan) => (
                <div key={plan.url} className="flex justify-between gap-2 border-t border-hairline py-1">
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-[11px]">{plan.url}</span>
                    {plan.reason ? <span className="block text-red">{plan.reason}</span> : null}
                  </span>
                  <span>{plan.chosen_height ? `${plan.chosen_height}p` : plan.selection_status || plan.status}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <Button size="sm" onClick={onSave} disabled={!saveEnabled || !saveAvailable}>
        Save learned template; continue to Inspect
      </Button>
      <p className="text-[11px] text-ink-3">
        Saves selectors and existing policy defaults only. Cookies, credentials,
        tokens, member URLs, and captured media URLs are rejected.
      </p>
    </Card>
  );
}
