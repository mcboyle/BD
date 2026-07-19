import { describe, it, expect } from "vitest";
import {
  shouldGlobalErrorToast,
  mutationErrorMessage,
} from "./mutationErrors";

// P6-2 (feedback) — a global MutationCache.onError gives every write a failure
// toast, but ONLY when the mutation doesn't already handle its own onError
// (avoids double-toasting the ~177 mutations that toast their own errors).

describe("mutationErrors (P6-2)", () => {
  it("fires the global toast when the mutation has no own onError", () => {
    expect(shouldGlobalErrorToast(false)).toBe(true);
  });

  it("suppresses the global toast when the mutation handles its own error", () => {
    expect(shouldGlobalErrorToast(true)).toBe(false);
  });

  it("uses the Error message when present", () => {
    expect(mutationErrorMessage(new Error("boom"))).toBe("boom");
  });

  it("falls back to a generic message for non-Error / empty", () => {
    expect(mutationErrorMessage(undefined)).toBe("Action failed");
    expect(mutationErrorMessage("string error")).toBe("Action failed");
    expect(mutationErrorMessage(new Error(""))).toBe("Action failed");
  });
});
