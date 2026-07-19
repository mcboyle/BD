// Auth / identity API client -- Cut 626 / C7 sub-wave 11.1a.
//
// The typed frontend client for the multi-user auth endpoints (backed by the
// `app_auth` blueprint / `user_accounts` engine). This is the real API layer for
// the login + user-admin surface; the login form / admin panel components that
// consume it land as the 11.1b UI follow. Kept in `lib/` alongside api-client so
// the SPA has a single, typed entry point for identity calls.

import { apiGet, apiPost, apiDelete } from "./api-client";

export interface AuthUser {
  username: string;
  role: string;
  created_ts?: number;
}

export interface WhoamiResult {
  ok: boolean;
  user: AuthUser | null;
  multi_user: boolean;
}

export interface UsersResult {
  ok: boolean;
  users: AuthUser[];
}

/** Authenticate; on success the server sets the httponly `bd_user` session cookie. */
export function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; user: AuthUser }> {
  return apiPost<{ ok: boolean; user: AuthUser }>("/api/auth/login", {
    username,
    password,
  });
}

/** Clear the `bd_user` session cookie. */
export function logout(): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>("/api/auth/logout", {});
}

/** Resolve the current user from the session cookie (or null) + the multi-user flag. */
export function whoami(): Promise<WhoamiResult> {
  return apiGet<WhoamiResult>("/api/auth/whoami");
}

/** Create a user. The first account bootstraps without auth; later ones are admin-gated. */
export function createUser(
  username: string,
  password: string,
  role: string,
): Promise<{ ok: boolean; user: AuthUser }> {
  return apiPost<{ ok: boolean; user: AuthUser }>("/api/auth/users", {
    username,
    password,
    role,
  });
}

/** List all users (admin-gated). */
export function listUsers(): Promise<UsersResult> {
  return apiGet<UsersResult>("/api/auth/users");
}

/** Set a user's role (admin-gated). C7 11.1b. */
export function setUserRole(
  username: string,
  role: string,
): Promise<{ ok: boolean; user: AuthUser }> {
  return apiPost<{ ok: boolean; user: AuthUser }>(
    `/api/auth/users/${encodeURIComponent(username)}/role`,
    { role },
  );
}

/** Reset a user's password (admin-gated). C7 11.1b. */
export function setUserPassword(
  username: string,
  password: string,
): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>(
    `/api/auth/users/${encodeURIComponent(username)}/password`,
    { password },
  );
}

/** Delete a user (admin-gated). C7 11.1b. */
export function deleteUser(username: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(
    `/api/auth/users/${encodeURIComponent(username)}`,
  );
}
