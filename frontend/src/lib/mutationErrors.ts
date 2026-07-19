// P6-2 (feedback) — helpers for the global MutationCache.onError safety net.
// Every write gets a failure toast, but only when the mutation didn't already
// register its own onError (the ~177 route mutations that do `onError: (e) =>
// toast.error(e.message)` keep handling their own — no double toast). The
// silent hook mutations (invalidate-on-success, no onError) get covered here.

/** True when the global error toast should fire (mutation has no own onError). */
export function shouldGlobalErrorToast(hasOwnOnError: boolean): boolean {
  return !hasOwnOnError;
}

/** Human message for a mutation failure, with a safe generic fallback. */
export function mutationErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Action failed";
}
