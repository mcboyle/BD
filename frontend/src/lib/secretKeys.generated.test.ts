import { describe, it, expect } from "vitest";

// REDACT-SOT Cut 3 (D3): the generated secret-key predicates mirror the server
// SoT. These tests lock the client-side behavior, in particular the desync that
// Cut 3 closes: VPN config keys the old hand-copied mirror missed
// (cookies / passphrase / preshared) must now classify secret.
import {
  isSecretConfigKey,
  isVpnSecretKey,
  isClipboardSecretKey,
} from "./secretKeys.generated";

describe("isSecretConfigKey (mirrors site_editor.is_secret_config_key)", () => {
  it("flags exact SECRET_FIELDS and floor substrings", () => {
    for (const k of ["password", "plex_token", "stash_api_key", "private_key",
                     "passphrase", "cookies", "api_key"]) {
      expect(isSecretConfigKey(k), k).toBe(true);
    }
  });
  it("fails open on bare ambiguous terms (D1)", () => {
    for (const k of ["session_timeout", "sid_cookie_name", "key_rotation_days",
                     "login_url", "username"]) {
      expect(isSecretConfigKey(k), k).toBe(false);
    }
  });
});

describe("isVpnSecretKey (mirrors vpn_config._vpn_key_is_secret)", () => {
  it("closes the desync: floor additions now masked client-side", () => {
    for (const k of ["cookies", "passphrase", "preshared_key"]) {
      expect(isVpnSecretKey(k), k).toBe(true);
    }
  });
  it("keeps the conservative VPN posture (bare key/private/account_number)", () => {
    for (const k of ["peer_public_key", "private", "account_number",
                     "key_rotation_days"]) {
      expect(isVpnSecretKey(k), k).toBe(true);
    }
  });
  it("preserves genuinely benign VPN fields", () => {
    for (const k of ["endpoint", "allowed_ips", "mtu"]) {
      expect(isVpnSecretKey(k), k).toBe(false);
    }
  });
});

describe("isClipboardSecretKey (mirrors the clipboard token class)", () => {
  it("redacts the OAuth/CSRF token class incl otp/nonce and bare cookie", () => {
    for (const k of ["csrf", "bearer", "otp", "nonce", "jwt", "cookie"]) {
      expect(isClipboardSecretKey(k), k).toBe(true);
    }
  });
  it("does NOT over-redact the ambiguous bare code/state", () => {
    for (const k of ["code", "state", "resolution", "format"]) {
      expect(isClipboardSecretKey(k), k).toBe(false);
    }
  });
});
