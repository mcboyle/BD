import type { SecretsUsage } from "@/hooks/useIntegrations";

// Cut 7 (Track A) — read-only secret USAGE list. Fed by GET /api/secrets/usage.
// Shows which stored secret keys exist and which sites reference them, by NAME
// only. It never displays a secret value (the endpoint never returns one).
//
// Rows 553 / 488: AN INVENTORY THAT COULD NOT BE READ IS UNKNOWN, NEVER ZERO.
// The backend half of this was fixed first -- api_secrets_usage stopped
// laundering SecretsUnreadableError / SecretsIntegrityError into an
// affirmative ok:true empty inventory and now answers 409 with a named state.
// The laundering then moved here: apiGet throws ApiError on !response.ok, so
// TanStack Query leaves `data` undefined, and `data?.stored_keys ?? []` turned
// that refusal back into "No stored secrets." -- the operator sentence that
// steers someone into re-entering or deleting ciphertexts that were intact.
//
// CLAUDE.md A7, both directions:
//   * a failure must not read as a measurement -- the refusal branch is
//     evaluated BEFORE the empty-inventory branch;
//   * distinct failures must not collapse -- an unreadable vault, a malformed
//     ciphertexts container, an unrecognised server state, an unnamed HTTP
//     refusal and a request that never arrived each get their own sentence,
//     because they lead to different operator actions;
//   * and the mirror defect is guarded too -- a vault that ANSWERED with an
//     empty inventory keeps its plain "No stored secrets.", so this guard
//     never refuses legitimate work.

export interface SecretsUsageListProps {
  data?: SecretsUsage;
  loading?: boolean;
  /** The query's rejection, if any. Unset/null means nothing failed. */
  error?: unknown;
}

interface Unmeasured {
  /** Why the inventory is unknown. Distinct per cause -- never a shared word. */
  headline: string;
  /** The server's own state token, when it named one. */
  state: string | null;
  /** The server's own words, or the transport error's, verbatim. */
  detail: string;
}

function bodyOf(error: unknown): Record<string, unknown> | null {
  const body = (error as { body?: unknown } | null)?.body;
  return body !== null && typeof body === "object"
    ? (body as Record<string, unknown>)
    : null;
}

function statusOf(error: unknown): number | null {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : null;
}

function wordsOf(error: unknown, body: Record<string, unknown> | null): string {
  if (body && typeof body.error === "string" && body.error) return body.error;
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === "string" && message ? message : String(error);
}

/**
 * Classify a failed usage read. Returns null when nothing failed.
 *
 * `data` is inspected as well as `error`: apiGet throws today, but a response
 * that says ok:false or carries a null inventory is not a measurement either,
 * and this component must not be the place that decides otherwise.
 */
export function describeUnmeasuredUsage(
  error: unknown,
  data?: SecretsUsage,
): Unmeasured | null {
  if (error === null || error === undefined) {
    if (!data) return null;
    if (data.ok !== false && data.stored_keys != null) return null;
    const state = typeof data.state === "string" ? data.state : null;
    return {
      headline:
        "The secret inventory could not be measured: the server returned a usage " +
        "response that reports no completed reading of the vault.",
      state,
      detail:
        typeof data.error === "string" && data.error
          ? data.error
          : "the response carried no reason",
    };
  }

  const body = bodyOf(error);
  const status = statusOf(error);
  const detail = wordsOf(error, body);
  const state = body && typeof body.state === "string" ? body.state : null;

  if (state === "unreadable") {
    return {
      headline:
        "The vault file could not be read or parsed, so the secret inventory " +
        "could not be measured. The stored ciphertexts were left untouched — " +
        "repair or restore the vault rather than re-entering credentials.",
      state,
      detail,
    };
  }
  if (state === "integrity_error") {
    return {
      headline:
        "The stored ciphertexts container is malformed, so the secret " +
        "inventory could not be measured. The stored ciphertexts were left " +
        "untouched — repair or restore the vault rather than deleting keys.",
      state,
      detail,
    };
  }
  if (state !== null) {
    // A named state this build does not recognise -- including the endpoint's
    // own "unknown" arm. Report the token rather than guessing at it.
    return {
      headline:
        "The secret inventory could not be measured: the vault refused with a " +
        "state this page does not recognise.",
      state,
      detail,
    };
  }
  if (status !== null) {
    // An HTTP refusal that named no state at all -- a proxy, an auth wall, an
    // error page. Distinct from a vault that answered and from one never asked.
    return {
      headline:
        `The secret inventory could not be measured: the request was refused ` +
        `with HTTP ${status} and no vault state.`,
      state: null,
      detail,
    };
  }
  return {
    headline:
      "The secret inventory could not be measured: the request to " +
      "/api/secrets/usage did not complete.",
    state: null,
    detail,
  };
}

export function SecretsUsageList({ data, loading, error }: SecretsUsageListProps) {
  // Evaluated FIRST. A refusal that arrives while a stale `data` is still in
  // cache, or alongside `loading`, is still a refusal.
  const unmeasured = describeUnmeasuredUsage(error, data);
  if (unmeasured) {
    return (
      <div role="status" className="text-sm text-amber">
        <p>{unmeasured.headline}</p>
        <p className="mt-1 text-ink-3">
          {unmeasured.state ? (
            <>
              state: <span className="font-mono">{unmeasured.state}</span> ·{" "}
            </>
          ) : null}
          <span className="font-mono">{unmeasured.detail}</span>
        </p>
      </div>
    );
  }
  if (loading && !data) {
    return <p className="text-sm text-ink-3">Loading secret usage…</p>;
  }
  const keys = data?.stored_keys ?? [];
  if (keys.length === 0) {
    // Reached only when the vault ANSWERED. A measured-empty inventory keeps
    // its plain sentence; widening this to cover unmeasured states is the
    // defect rows 553/488 name.
    return <p className="text-sm text-ink-3">No stored secrets.</p>;
  }
  const usage = data?.usage ?? {};
  const unreferenced = new Set(data?.unreferenced ?? []);
  return (
    <ul aria-label="Secret usage" className="space-y-1">
      {keys.map((key) => {
        const refs = usage[key] ?? [];
        return (
          <li key={key} className="text-sm">
            <span className="font-mono">{key}</span>
            {unreferenced.has(key) ? (
              <span className="ml-2 text-amber">unreferenced</span>
            ) : (
              <span className="ml-2 text-ink-3">
                used by {refs.length} site{refs.length === 1 ? "" : "s"}
                {refs.length ? `: ${refs.join(", ")}` : ""}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
