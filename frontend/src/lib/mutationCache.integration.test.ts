import { describe, it, expect, vi } from "vitest";
import { QueryClient, MutationCache, MutationObserver } from "@tanstack/react-query";
import { shouldGlobalErrorToast, mutationErrorMessage } from "./mutationErrors";

// P6-2 — prove the global write-failure toast actually fires through a real
// react-query v5 MutationCache, and that mutation.options.onError is the field
// that distinguishes "handles its own error" (so we don't double-toast).

function makeClient(globalToast: (msg: string) => void) {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _v, _c, mutation) => {
        if (!shouldGlobalErrorToast(Boolean(mutation.options.onError))) return;
        globalToast(mutationErrorMessage(error));
      },
    }),
    defaultOptions: { mutations: { retry: 0 } },
  });
}

async function runMutation(
  client: QueryClient,
  options: { mutationFn: () => Promise<unknown>; onError?: () => void },
) {
  const obs = new MutationObserver(client, options);
  try {
    await obs.mutate();
  } catch {
    /* expected rejection */
  }
}

describe("MutationCache global error toast (P6-2)", () => {
  it("fires for a mutation WITHOUT its own onError", async () => {
    const globalToast = vi.fn();
    const client = makeClient(globalToast);
    await runMutation(client, {
      mutationFn: () => Promise.reject(new Error("nope")),
    });
    expect(globalToast).toHaveBeenCalledTimes(1);
    expect(globalToast).toHaveBeenCalledWith("nope");
  });

  it("does NOT fire when the mutation handles its own onError", async () => {
    const globalToast = vi.fn();
    const ownOnError = vi.fn();
    const client = makeClient(globalToast);
    await runMutation(client, {
      mutationFn: () => Promise.reject(new Error("nope")),
      onError: ownOnError,
    });
    expect(ownOnError).toHaveBeenCalledTimes(1);
    expect(globalToast).not.toHaveBeenCalled();
  });
});
