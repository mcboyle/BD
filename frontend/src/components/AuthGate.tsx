// AuthGate — the app-wide sign-in guard (C7 follow-on).
//
// SAFETY-CRITICAL no-op contract: with multi-user OFF (the default) this gate is
// completely transparent — it always renders the app. It only shows the Login
// page when the server reports multi_user === true AND there is no current user.
// Any uncertainty (whoami still loading, or the request errored) renders the app,
// never the login wall — a broken whoami must never lock the sole operator out.
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { whoami } from "@/lib/auth";
import { Login } from "@/routes/Login";

export function AuthGate({ children }: { children: ReactNode }) {
  const who = useQuery({
    queryKey: ["auth", "whoami"],
    queryFn: whoami,
    staleTime: 60_000,
    retry: false,
  });

  // Show the login wall ONLY on a definitive "multi-user on, not signed in".
  // While loading, on error, or when multi-user is off -> render the app.
  if (who.data && who.data.multi_user && who.data.user == null) {
    return <Login />;
  }
  return <>{children}</>;
}
