// Users — multi-user identity admin surface (C7 11.1b).
//
// Wires the eight /api/auth/* endpoints (the 11.1a base + the 11.1b admin ops):
//   * GET    /api/auth/whoami                      — current user + multi_user flag
//   * GET    /api/auth/users                       — list (admin)
//   * POST   /api/auth/users                       — create (bootstrap first, else admin)
//   * POST   /api/auth/users/<u>/role              — set role (admin)   [11.1b]
//   * POST   /api/auth/users/<u>/password          — reset password (admin) [11.1b]
//   * DELETE /api/auth/users/<u>                   — delete (admin)     [11.1b]
//   * POST   /api/auth/login, /api/auth/logout     — session (Login surface)
//
// The role/password/delete routes are SENSITIVE + mutating; this component is the
// real operator-facing SPA wiring for them (operator_facing_unwired==0). No stored
// password is ever displayed — set_password is write-only, like the Secrets page.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { SecretField } from "@/components/SecretField";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/card";
import { DangerZone } from "@/components/ui/DangerZone";
import { Input } from "@/components/ui/input";
import {
  createUser,
  deleteUser,
  listUsers,
  setUserPassword,
  setUserRole,
  whoami,
  type AuthUser,
} from "@/lib/auth";

const ROLES = ["admin", "operator", "reviewer", "viewer"];

export function Users() {
  const qc = useQueryClient();
  const who = useQuery({ queryKey: ["auth", "whoami"], queryFn: whoami });
  const users = useQuery({
    queryKey: ["auth", "users"],
    queryFn: listUsers,
    // only attempt the admin list once we know an admin is logged in
    enabled: who.data?.user?.role === "admin",
  });

  const [newName, setNewName] = useState("");
  const [newPass, setNewPass] = useState("");
  const [newRole, setNewRole] = useState("operator");
  const [pwFor, setPwFor] = useState<string | null>(null);
  const [pwValue, setPwValue] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["auth", "users"] });
    qc.invalidateQueries({ queryKey: ["auth", "whoami"] });
  };

  const create = useMutation({
    mutationFn: () => createUser(newName.trim(), newPass, newRole),
    onSuccess: () => {
      toast.success(`Created ${newName.trim()}`);
      setNewName("");
      setNewPass("");
      setNewRole("operator");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Create failed"),
  });

  const changeRole = useMutation({
    mutationFn: (v: { username: string; role: string }) =>
      setUserRole(v.username, v.role),
    onSuccess: (_d, v) => {
      toast.success(`${v.username} → ${v.role}`);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Role change failed"),
  });

  const changePw = useMutation({
    mutationFn: (v: { username: string; password: string }) =>
      setUserPassword(v.username, v.password),
    onSuccess: (_d, v) => {
      toast.success(`Password reset for ${v.username}`);
      setPwFor(null);
      setPwValue("");
    },
    onError: (e: Error) => toast.error(e.message || "Password reset failed"),
  });

  const remove = useMutation({
    mutationFn: (username: string) => deleteUser(username),
    onSuccess: (_d, username) => {
      toast.success(`Deleted ${username}`);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message || "Delete failed"),
  });

  const me = who.data?.user ?? null;
  const isAdmin = me?.role === "admin";
  const bootstrap = who.data ? !who.data.multi_user : false;

  return (
    <AppShell title="Users" subtitle="Multi-user identity · roles · access">
      <div className="space-y-5">
        {who.isLoading ? (
          <Card>Loading…</Card>
        ) : bootstrap ? (
          <Callout tone="info" title="Bootstrap the first account">
            No users exist yet. The first account you create becomes the initial
            admin (no prior sign-in required); afterwards, user management is
            admin-gated.
          </Callout>
        ) : !isAdmin ? (
          <Callout tone="caution" title="Admin role required">
            {me
              ? `Signed in as ${me.username} (${me.role}). User administration requires the admin role.`
              : "Sign in as an admin to manage users."}
          </Callout>
        ) : null}

        {(bootstrap || isAdmin) && (
          <Card>
            <h3 className="text-sm font-medium mb-3">
              {bootstrap ? "Create the first admin" : "Add a user"}
            </h3>
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex flex-col gap-1">
                <label className="text-xs text-muted">Username</label>
                <Input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="username"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-muted">Password</label>
                <SecretField
                  value={newPass}
                  onChange={setNewPass}
                  ariaLabel="New user password"
                  placeholder="password (write-only)"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-muted">Role</label>
                <select
                  className="h-9 rounded-md border border-border bg-transparent px-2 text-sm"
                  value={bootstrap ? "admin" : newRole}
                  disabled={bootstrap}
                  onChange={(e) => setNewRole(e.target.value)}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                onClick={() => create.mutate()}
                disabled={
                  !newName.trim() || !newPass || create.isPending
                }
              >
                {create.isPending ? "Creating…" : "Create"}
              </Button>
            </div>
          </Card>
        )}

        {isAdmin && (
          <Card>
            <h3 className="text-sm font-medium mb-3">Accounts</h3>
            {users.isLoading ? (
              <div>Loading users…</div>
            ) : (
              <div className="flex flex-col divide-y divide-border">
                {(users.data?.users ?? []).map((u: AuthUser) => (
                  <div
                    key={u.username}
                    className="flex flex-wrap items-center gap-3 py-2"
                  >
                    <span className="font-mono text-sm min-w-[8rem]">
                      {u.username}
                    </span>
                    <StatusPill tone={u.role === "admin" ? "green" : "neutral"}>
                      {u.role}
                    </StatusPill>
                    <select
                      className="h-8 rounded-md border border-border bg-transparent px-2 text-xs"
                      value={u.role}
                      onChange={(e) =>
                        changeRole.mutate({
                          username: u.username,
                          role: e.target.value,
                        })
                      }
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="outline"
                      onClick={() =>
                        setPwFor(pwFor === u.username ? null : u.username)
                      }
                    >
                      Reset password
                    </Button>
                    {u.username !== me?.username && (
                      <Button
                        variant="destructive"
                        onClick={() => {
                          if (
                            window.confirm(`Delete user ${u.username}? This cannot be undone.`)
                          ) {
                            remove.mutate(u.username);
                          }
                        }}
                      >
                        Delete
                      </Button>
                    )}
                    {pwFor === u.username && (
                      <div className="flex w-full items-end gap-2 pl-4">
                        <SecretField
                          value={pwValue}
                          onChange={setPwValue}
                          ariaLabel="Reset password"
                          placeholder="new password (write-only)"
                        />
                        <Button
                          onClick={() =>
                            changePw.mutate({
                              username: u.username,
                              password: pwValue,
                            })
                          }
                          disabled={!pwValue || changePw.isPending}
                        >
                          Set
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <DangerZone>
              Role, password-reset and delete actions take effect immediately and
              are audited. Deleting the last admin will lock out administration.
            </DangerZone>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
