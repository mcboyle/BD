// Login — standalone sign-in surface (C7 follow-on).
//
// Rendered by AuthGate ONLY when multi-user is enabled AND there is no current
// session. With multi-user off (the default single-operator posture) this page is
// never shown and the app is fully accessible, unchanged. On success the server
// sets the httponly bd_user cookie; we invalidate whoami so AuthGate re-renders
// the app.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { SecretField } from "@/components/SecretField";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiGet } from "@/lib/api-client";
import { login } from "@/lib/auth";

export function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  // v3.66.681 (B2/P6): show the SSO button only when an OIDC provider is
  // configured + enabled. Read-only status probe; the button navigates the
  // browser to the provider redirect endpoint.
  const sso = useQuery<{ enabled: boolean }, Error>({
    queryKey: ["auth", "oidc", "status"],
    queryFn: ({ signal }) =>
      apiGet<{ enabled: boolean }>("/api/auth/oidc/status", signal),
    staleTime: 60_000,
  });

  const signIn = useMutation({
    mutationFn: () => login(username.trim(), password),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["auth", "whoami"] });
    },
    onError: (e: Error) => setError(e.message || "Sign-in failed"),
  });

  const submit = () => {
    if (username.trim() && password) signIn.mutate();
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <Card className="w-full max-w-sm">
        <h1 className="text-lg font-semibold mb-1">Sign in</h1>
        <p className="text-sm text-muted mb-4">
          This BulkDownloader instance requires a sign-in.
        </p>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted" htmlFor="login-username">
              Username
            </label>
            <Input
              id="login-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              autoFocus
              placeholder="username"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted" htmlFor="login-password">
              Password
            </label>
            <SecretField
              value={password}
              onChange={setPassword}
              ariaLabel="Password"
              placeholder="password"
            />
          </div>
          {error && (
            <p className="text-sm text-red" role="alert">
              {error}
            </p>
          )}
          <Button
            onClick={submit}
            disabled={!username.trim() || !password || signIn.isPending}
          >
            {signIn.isPending ? "Signing in…" : "Sign in"}
          </Button>
          {sso.data?.enabled && (
            <>
              <div className="flex items-center gap-2 my-1">
                <div className="h-px flex-1 bg-border" />
                <span className="text-xs text-muted">or</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <Button
                variant="outline"
                onClick={() => {
                  window.location.href = "/api/auth/oidc/login";
                }}
              >
                Sign in with SSO
              </Button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
