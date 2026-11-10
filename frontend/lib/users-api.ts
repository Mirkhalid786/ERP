/** Staff user management API (permission: users.manage). Passwords are sent, never received. */
import { fetchJson } from "@/lib/api";

export interface ManagedUser {
  id: number;
  name: string;
  username: string;
  role_id: number;
  role_name: string;
  is_active: boolean;
  is_admin: boolean;
  last_login_at?: string | null;
  created_at: string;
  updated_at?: string | null;
  created_by_name?: string | null;
  active_sessions: number;
}

export interface RoleInfo {
  id: number;
  name: string;
  description?: string | null;
  is_system_role: boolean;
  permissions: string[];
}

export interface UserAuditEntry {
  action: string;
  by?: string | null;
  at: string;
  details: Record<string, unknown>;
}

const json = (method: string, body: unknown) => ({ method, body: JSON.stringify(body) });

export const usersApi = {
  list: (q?: string, includeInactive = true) =>
    fetchJson<ManagedUser[]>(`/api/v1/users?${new URLSearchParams({ ...(q ? { q } : {}), include_inactive: String(includeInactive) })}`),
  roles: () => fetchJson<RoleInfo[]>("/api/v1/roles"),
  create: (body: { name: string; username: string; password: string; role_id: number }) =>
    fetchJson<ManagedUser>("/api/v1/users", json("POST", body)),
  update: (id: number, body: { name?: string; username?: string; role_id?: number; is_active?: boolean }) =>
    fetchJson<ManagedUser>(`/api/v1/users/${id}`, json("PUT", body)),
  resetPassword: (id: number, new_password: string) =>
    fetchJson<{ message: string; sessions_ended: number }>(`/api/v1/users/${id}/reset-password`, json("POST", { new_password })),
  audit: (id: number) => fetchJson<UserAuditEntry[]>(`/api/v1/users/${id}/audit`),
};

/** Same rule as the server (app/schemas/auth.py: check_password_policy). */
export const passwordChecks = (pw: string) => [
  { ok: pw.length >= 8 && pw.length <= 128, label: "8–128 characters" },
  { ok: /[A-Z]/.test(pw), label: "an uppercase letter" },
  { ok: /[a-z]/.test(pw), label: "a lowercase letter" },
  { ok: /\d/.test(pw), label: "a digit" },
];
