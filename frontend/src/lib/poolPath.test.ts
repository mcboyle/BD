import { describe, it, expect } from "vitest";
import { actionSuffixWithIdx } from "./poolPath";

// v3.66.336: locks the doubled-segment fix. The descriptor suffix already ends
// in the action verb (e.g. "account_pool/reset"); an indexed action must append
// ONLY the index, never the verb again (which 404'd as account_pool/reset/reset/<idx>).
describe("actionSuffixWithIdx", () => {
  it("appends only the index for an indexed action", () => {
    expect(actionSuffixWithIdx("account_pool/reset", true, "2")).toBe(
      "account_pool/reset/2",
    );
  });

  it("does NOT double the trailing verb segment", () => {
    expect(actionSuffixWithIdx("account_pool/reset", true, "0")).not.toContain(
      "reset/reset",
    );
  });

  it("returns the suffix unchanged when no index is needed", () => {
    expect(actionSuffixWithIdx("accounts/rotate", false, "")).toBe(
      "accounts/rotate",
    );
  });

  it("url-encodes the index segment", () => {
    expect(actionSuffixWithIdx("account_pool/reset", true, "a b")).toBe(
      "account_pool/reset/a%20b",
    );
  });
});
