"use client";

import { useCallback, useEffect, useState } from "react";
import { History, KeyRound, Palette, Pencil, Search, ShieldCheck, UserCog, UserPlus } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, Spinner, Table, errorText, inputCls, td, useStaff,
} from "@/components/sales-ui";
import { fmtDateTime } from "@/lib/sales-api";
import { fetchJson } from "@/lib/api";
import { ManagedUser, RoleInfo, UserAuditEntry, passwordChecks, usersApi } from "@/lib/users-api";

// Shown so an administrator can see what a role really allows before assigning it
const SENSITIVE: Record<string, string> = {
  "users.manage": "Manage users",
  "roles.manage": "Manage roles",
  "settings.manage": "Change settings",
  "sales.cancel": "Void invoices",
  "sales.refund": "Returns & refunds",
  "sales.discount": "Give discounts",
  "sales.credit": "Credit (udhaar) sales",
  "sales.credit_override": "Override credit limits",
  "sales.prescription": "Sell prescription medicines",
  "prescriptions.view": "See prescriptions",
  "customers.adjust": "Ledger adjustments / write-offs",
  "shifts.review": "Approve day-end / paid-outs",
  "messaging.configure": "WhatsApp settings",
  "reports.view": "Reports",
  "stock.adjust": "Stock adjustments",
  "purchases.post": "Post purchases to stock",
};

export default function UsersPage() {
  const { user, can } = useStaff();
  const [rows, setRows] = useState<ManagedUser[] | null>(null);
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [modal, setModal] = useState<{ kind: "" | "create" | "edit" | "reset" | "history"; user?: ManagedUser }>({ kind: "" });

  const load = useCallback(async () => {
    try {
      setRows(await usersApi.list(q.trim() || undefined, showInactive));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [q, showInactive]);

  useEffect(() => {
    if (!user || !can("users.manage")) return;
    usersApi.roles().then(setRoles).catch((e) => setError(errorText(e)));
  }, [user, can]);
  useEffect(() => {
    if (!user || !can("users.manage")) return;
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [user, can, load]);

  const toggleActive = async (u: ManagedUser) => {
    if (u.is_active && !confirm(`Deactivate ${u.name}? They are signed out at once and cannot log in. Their past bills and records stay.`)) return;
    try {
      await usersApi.update(u.id, { is_active: !u.is_active });
      setInfo(`${u.name} ${u.is_active ? "deactivated and signed out" : "re-activated"}.`);
      load();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const resetAppearance = async (u: ManagedUser) => {
    if (!confirm(`Reset ${u.name}'s theme and layout to the pharmacy default?`)) return;
    try {
      await fetchJson(`/api/v1/users/${u.id}/appearance`, { method: "DELETE" });
      setInfo(`${u.name} now uses the pharmacy default appearance.`);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const close = (msg?: string) => {
    setModal({ kind: "" });
    if (msg) setInfo(msg);
    load();
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><UserCog className="w-6 h-6" /></div>
            <div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Staff users</h1>
              <p className="text-sm text-slate-400">Logins, roles and access for cashiers, pharmacists and administrators</p>
            </div>
          </div>
          {user && can("users.manage") && <Btn variant="primary" onClick={() => setModal({ kind: "create" })}><UserPlus className="w-4 h-4" /> New user</Btn>}
        </div>

        {!user ? <Spinner /> : !can("users.manage") ? <ErrorBox message="Only administrators can manage users." /> : (
          <>
            <ErrorBox message={error} onClose={() => setError(null)} />
            {info && <Notice tone="emerald">{info}</Notice>}

            <Card>
              <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
                <div className="relative flex-1">
                  <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
                  <input className={`${inputCls} pl-9`} placeholder="Search name, username or role" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
                </div>
                <label className="flex items-center gap-2 text-sm text-slate-400">
                  <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show deactivated
                </label>
              </div>
            </Card>

            <Card title={rows ? `${rows.length} user(s)` : "Loading…"}>
              <Table head={["User", "Role", "Status", "Last login", "Created", "Actions"]} empty={rows !== null && rows.length === 0 && "No users match."}>
                {(rows || []).map((u) => (
                  <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
                    <td className={td}>
                      <div className="font-semibold text-white">{u.name}{u.id === user.user_id && <span className="text-xs text-slate-500"> (you)</span>}</div>
                      <div className="text-xs font-mono text-slate-400">{u.username}</div>
                    </td>
                    <td className={td}>
                      <div className="flex items-center gap-1">
                        <span>{u.role_name}</span>
                        {u.is_admin && <span title="Can manage users"><ShieldCheck className="w-4 h-4 text-emerald-400" /></span>}
                      </div>
                    </td>
                    <td className={td}>
                      {u.is_active ? <Badge value="PAID" label="Active" /> : <Badge value="VOIDED" label="Deactivated" />}
                      {u.active_sessions > 0 && <div className="text-[11px] text-slate-500 mt-0.5">{u.active_sessions} signed-in session(s)</div>}
                    </td>
                    <td className={`${td} text-xs text-slate-400`}>{u.last_login_at ? fmtDateTime(u.last_login_at) : "Never"}</td>
                    <td className={`${td} text-xs text-slate-400`}>{fmtDateTime(u.created_at)}{u.created_by_name && <div>by {u.created_by_name}</div>}</td>
                    <td className={td}>
                      <div className="flex flex-wrap gap-1">
                        <Btn variant="ghost" onClick={() => setModal({ kind: "edit", user: u })} title="Edit"><Pencil className="w-4 h-4" /> Edit</Btn>
                        <Btn variant="ghost" onClick={() => setModal({ kind: "reset", user: u })} title="Reset password"><KeyRound className="w-4 h-4" /> Password</Btn>
                        {u.id !== user.user_id && (
                          <Btn variant={u.is_active ? "danger" : "secondary"} onClick={() => toggleActive(u)}>{u.is_active ? "Deactivate" : "Activate"}</Btn>
                        )}
                        <Btn variant="ghost" onClick={() => resetAppearance(u)} title="Reset appearance to the pharmacy default"><Palette className="w-4 h-4" /></Btn>
                        <Btn variant="ghost" onClick={() => setModal({ kind: "history", user: u })} title="History"><History className="w-4 h-4" /></Btn>
                      </div>
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>

            <Card title="What each role can do">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                {roles.map((r) => (
                  <div key={r.id} className="border border-slate-800 rounded-xl p-3">
                    <div className="font-semibold text-white flex items-center gap-1">{r.name}{r.permissions.includes("users.manage") && <ShieldCheck className="w-4 h-4 text-emerald-400" />}</div>
                    <div className="text-[11px] text-slate-500 mb-2">{r.permissions.length} permissions</div>
                    <ul className="text-xs space-y-0.5">
                      {Object.entries(SENSITIVE).map(([code, label]) => (
                        <li key={code} className={r.permissions.includes(code) ? "text-emerald-300" : "text-slate-600 line-through"}>{label}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              <div className="text-xs text-slate-500 mt-3">Access comes from each role&apos;s permissions (checked by the server on every request), not from its name. The shield marks roles that can manage users.</div>
            </Card>
          </>
        )}
      </main>

      {modal.kind === "create" && <CreateModal roles={roles} onClose={close} />}
      {modal.kind === "edit" && modal.user && <EditModal u={modal.user} self={modal.user.id === user?.user_id} roles={roles} onClose={close} />}
      {modal.kind === "reset" && modal.user && <ResetModal u={modal.user} self={modal.user.id === user?.user_id} onClose={close} />}
      {modal.kind === "history" && modal.user && <HistoryModal u={modal.user} onClose={() => setModal({ kind: "" })} />}
    </div>
  );
}

function PasswordFields({ pw, setPw, confirmPw, setConfirmPw }: { pw: string; setPw: (v: string) => void; confirmPw: string; setConfirmPw: (v: string) => void }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Password *"><input type="password" autoComplete="new-password" className={inputCls} value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        <Field label="Confirm *"><input type="password" autoComplete="new-password" className={inputCls} value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} /></Field>
      </div>
      <ul className="text-xs flex flex-wrap gap-x-3 gap-y-1">
        {passwordChecks(pw).map((c) => <li key={c.label} className={c.ok ? "text-emerald-300" : "text-slate-500"}>{c.ok ? "✓" : "○"} {c.label}</li>)}
        <li className={pw && pw === confirmPw ? "text-emerald-300" : "text-slate-500"}>{pw && pw === confirmPw ? "✓" : "○"} both match</li>
      </ul>
    </>
  );
}

const passwordOk = (pw: string, confirmPw: string) => passwordChecks(pw).every((c) => c.ok) && pw === confirmPw;

function RoleSelect({ roles, value, onChange }: { roles: RoleInfo[]; value: number | ""; onChange: (v: number) => void }) {
  return (
    <select className={inputCls} value={value} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="" disabled>Choose a role…</option>
      {roles.map((r) => <option key={r.id} value={r.id}>{r.name}{r.permissions.includes("users.manage") ? " (administrator)" : ""}</option>)}
    </select>
  );
}

function CreateModal({ roles, onClose }: { roles: RoleInfo[]; onClose: (msg?: string) => void }) {
  const [f, setF] = useState({ name: "", username: "", role_id: "" as number | "" });
  const [pw, setPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const u = await usersApi.create({ name: f.name, username: f.username, password: pw, role_id: Number(f.role_id) });
      onClose(`User ${u.username} created as ${u.role_name}. Give them the password in person.`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open title="New staff user" onClose={() => onClose()}>
      <ErrorBox message={err} />
      <Field label="Full name *"><input autoFocus className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Username *" hint="Letters, digits, . _ - (saved in lowercase)"><input className={inputCls} autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Role *"><RoleSelect roles={roles} value={f.role_id} onChange={(v) => setF({ ...f, role_id: v })} /></Field>
      </div>
      <PasswordFields pw={pw} setPw={setPw} confirmPw={confirmPw} setConfirmPw={setConfirmPw} />
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={() => onClose()}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={f.name.trim().length < 2 || f.username.trim().length < 3 || !f.role_id || !passwordOk(pw, confirmPw)} onClick={save}>Create user</Btn>
      </div>
    </Modal>
  );
}

function EditModal({ u, self, roles, onClose }: { u: ManagedUser; self: boolean; roles: RoleInfo[]; onClose: (msg?: string) => void }) {
  const [f, setF] = useState({ name: u.name, username: u.username, role_id: u.role_id as number | "", is_active: u.is_active });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const roleChange = f.role_id !== u.role_id;
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: { name?: string; username?: string; role_id?: number; is_active?: boolean } = {};
      if (f.name !== u.name) body.name = f.name;
      if (f.username !== u.username) body.username = f.username;
      if (roleChange) body.role_id = Number(f.role_id);
      if (f.is_active !== u.is_active) body.is_active = f.is_active;
      const saved = await usersApi.update(u.id, body);
      onClose(`${saved.name} updated.`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open title={`Edit ${u.name}`} onClose={() => onClose()}>
      <ErrorBox message={err} />
      <Field label="Full name"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Username"><input className={inputCls} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Role"><RoleSelect roles={roles} value={f.role_id} onChange={(v) => setF({ ...f, role_id: v })} /></Field>
      </div>
      {roleChange && <Notice tone="amber">The new role&apos;s permissions apply from their next action — no re-login needed.{self && " This is your own account: you may lose access to this page."}</Notice>}
      {!self && (
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active (can log in)
        </label>
      )}
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={() => onClose()}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={f.name.trim().length < 2 || f.username.trim().length < 3} onClick={save}>Save</Btn>
      </div>
    </Modal>
  );
}

function ResetModal({ u, self, onClose }: { u: ManagedUser; self: boolean; onClose: (msg?: string) => void }) {
  const [pw, setPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await usersApi.resetPassword(u.id, pw);
      onClose(`Password for ${u.username} changed. ${r.sessions_ended} other session(s) signed out.`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open title={`Reset password · ${u.username}`} onClose={() => onClose()}>
      <ErrorBox message={err} />
      <Notice>{self ? "Your other signed-in devices will be signed out; this one stays signed in." : "The user is signed out everywhere and must log in with the new password."} Passwords are never shown or stored in readable form.</Notice>
      <PasswordFields pw={pw} setPw={setPw} confirmPw={confirmPw} setConfirmPw={setConfirmPw} />
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={() => onClose()}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={!passwordOk(pw, confirmPw)} onClick={save}>Set new password</Btn>
      </div>
    </Modal>
  );
}

const ACTION_LABEL: Record<string, string> = {
  "user.create": "Created", "user.update": "Details edited", "user.role_change": "Role changed",
  "user.deactivate": "Deactivated", "user.activate": "Re-activated", "user.password_reset": "Password reset",
};

function HistoryModal({ u, onClose }: { u: ManagedUser; onClose: () => void }) {
  const [rows, setRows] = useState<UserAuditEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { usersApi.audit(u.id).then(setRows).catch((e) => setErr(errorText(e))); }, [u.id]);
  const describe = (d: Record<string, unknown>) => {
    const role = d.role as { from?: string; to?: string } | string | undefined;
    if (role && typeof role === "object") return `${role.from} → ${role.to}`;
    if (typeof role === "string") return role;
    if (d.sessions_ended !== undefined) return `${d.sessions_ended} session(s) ended`;
    return Object.keys(d).filter((k) => k !== "username").join(", ");
  };
  return (
    <Modal open title={`Account history · ${u.username}`} onClose={onClose}>
      <ErrorBox message={err} />
      {!rows ? <Spinner /> : rows.length === 0 ? <div className="text-sm text-slate-500">No administrative changes recorded.</div> : (
        <ul className="space-y-2 text-sm">
          {rows.map((r, i) => (
            <li key={i} className="border border-slate-800 rounded-lg p-2">
              <div className="flex justify-between"><span className="text-white">{ACTION_LABEL[r.action] || r.action}</span><span className="text-xs text-slate-500">{fmtDateTime(r.at)}</span></div>
              <div className="text-xs text-slate-400">by {r.by || "—"} · {describe(r.details)}</div>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
