"use client";

/**
 * Administration (Phases 6–7): system health, database backups (run, verify, download)
 * and the audit-log viewer. Restoring a backup is an operator procedure (docs/DEPLOYMENT.md),
 * never a button here.
 */
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Badge, Btn, Card, ErrorBox, Field, Notice, Pager, Spinner, Stat, Table, errorText, inputCls, td, tdNum, useStaff } from "@/components/sales-ui";
import { fetchJson } from "@/lib/api";
import { fmtDateTime } from "@/lib/sales-api";

type Tab = "health" | "backups" | "audit";
interface Health { ok: boolean; environment: string; checked_at: string; checks: Record<string, Record<string, unknown> & { ok: boolean }> }
interface Backup {
  id: number; file_name: string; trigger: string; status: string; size_bytes?: number | null; sha256?: string | null;
  tables?: number | null; total_rows?: number | null; alembic_revision?: string | null; error?: string | null;
  started_at: string; finished_at?: string | null; verified_at?: string | null; verify_status?: string | null; verify_detail?: string | null;
}
interface AuditItem { id: number; timestamp: string; user_name?: string | null; action: string; entity_type?: string | null; entity_id?: string | null; reason?: string | null; metadata?: string | null }

const post = <T,>(url: string) => fetchJson<T>(url, { method: "POST", body: "{}" });
const LABELS: Record<string, string> = { database: "Database", migrations: "Migrations", disk: "Disk space", backup: "Last backup", worker: "Background worker", messaging: "WhatsApp outbox", accounting: "Accounting postings" };

function describe(name: string, c: Record<string, unknown>): string {
  switch (name) {
    case "database": return String(c.version || c.error || "");
    case "migrations": return `database ${c.current ?? "unknown"} · code ${c.code_head ?? "unknown"}${c.detail ? ` — ${c.detail}` : ""}`;
    case "disk": return c.error ? String(c.error) : `${c.free_gb} GB free of ${c.total_gb} GB (${c.free_percent}%)`;
    case "backup": return c.last_file ? `${c.last_file} · ${c.age_hours} h ago · verification ${c.verified ?? "not run"}` : String(c.detail || "none");
    case "worker": return c.last_seen ? `last pass ${c.minutes_ago} min ago` : "no heartbeat recorded (is the worker container running?)";
    case "messaging": return `${c.pending} pending · ${c.failed} failed · provider ${c.provider ?? "?"}`;
    case "accounting": return c.set_up ? `books start ${c.books_start_date} · ${c.open_posting_issues} failed posting(s)` : "accounting not set up";
    default: return "";
  }
}

export default function AdminPage() {
  const { user, can } = useStaff();
  const tabs: [Tab, string, string][] = ([["health", "System health", "settings.manage"], ["backups", "Backups", "backups.manage"], ["audit", "Audit log", "audit.view"]] as [Tab, string, string][])
    .filter(([, , perm]) => can(perm));
  const [tab, setTab] = useState<Tab | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  useEffect(() => { if (!tab && tabs.length) setTab(tabs[0][0]); }, [tabs, tab]);
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><ShieldCheck className="w-6 h-6" /></div>
          <div><h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Administration</h1>
            <p className="text-sm text-slate-400">System health, backups and the audit trail{can("settings.manage") && <> · <a className="text-sky-300 hover:underline" href="/settings">Application settings</a> · <a className="text-sky-300 hover:underline" href="/settings/branding">Branding &amp; appearance</a></>}</p></div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {info && <Notice tone="emerald">{info}</Notice>}
        {!user ? <Spinner /> : tabs.length === 0 ? <ErrorBox message="You do not have access to administration." /> : (
          <>
            <nav className="flex gap-1">{tabs.map(([k, label]) => (
              <button key={k} onClick={() => { setTab(k); setInfo(null); }} className={`px-3 py-1.5 rounded-lg text-xs font-semibold border ${tab === k ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30" : "border-transparent text-slate-400 hover:bg-slate-800"}`}>{label}</button>
            ))}</nav>
            {tab === "health" && <HealthTab onError={(e) => setError(errorText(e))} />}
            {tab === "backups" && <BackupsTab onError={(e) => setError(errorText(e))} onDone={setInfo} />}
            {tab === "audit" && <AuditTab onError={(e) => setError(errorText(e))} />}
          </>
        )}
      </main>
    </div>
  );
}

function HealthTab({ onError }: { onError: (e: unknown) => void }) {
  const [h, setH] = useState<Health | null>(null);
  const load = useCallback(() => { setH(null); fetchJson<Health>("/api/v1/admin/system-health").then(setH).catch(onError); }, [onError]);
  useEffect(load, [load]);
  return (
    <Card title="System health" actions={<Btn onClick={load}>Check again</Btn>}>
      {!h ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-4">
            <Stat label="Overall" value={h.ok ? "All checks OK" : "Needs attention"} tone={h.ok ? "emerald" : "amber"} />
            <Stat label="Environment" value={h.environment} />
            <Stat label="Checked" value={fmtDateTime(h.checked_at)} />
          </div>
          <Table head={["Check", "Status", "Detail"]}>
            {Object.entries(h.checks).map(([name, c]) => (
              <tr key={name}><td className={td}>{LABELS[name] || name}</td><td className={td}><Badge value={c.ok ? "SENT" : "FAILED"} label={c.ok ? "OK" : "Check"} /></td>
                <td className={`${td} text-xs text-slate-300`}>{describe(name, c)}</td></tr>
            ))}
          </Table>
        </>
      )}
    </Card>
  );
}

function BackupsTab({ onError, onDone }: { onError: (e: unknown) => void; onDone: (m: string) => void }) {
  const [rows, setRows] = useState<Backup[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { fetchJson<Backup[]>("/api/v1/admin/backups").then(setRows).catch(onError); }, [onError]);
  useEffect(load, [load]);
  const run = async (fn: () => Promise<unknown>, msg: string) => { setBusy(true); try { await fn(); onDone(msg); load(); } catch (e) { onError(e); } finally { setBusy(false); } };
  return (
    <Card title="Database backups" actions={<Btn variant="primary" busy={busy} onClick={() => run(() => post<Backup>("/api/v1/admin/backups"), "Backup completed.")}>Back up now</Btn>}>
      <p className="text-xs text-slate-400 mb-3">A consistent snapshot of the whole database, compressed, with a SHA-256 checksum. Verify re-reads the file and compares every table&apos;s row count (and test-restores it if a scratch database is configured). Set a daily automatic backup time on the Settings page (daily backup time). Copy backups off this computer regularly; restoring is described in docs/DEPLOYMENT.md.</p>
      {!rows ? <Spinner /> : (
        <Table head={["File", "Made", "Size", "Rows", "Status", "Verification", ""]} empty={rows.length === 0 && "No backups made by the application yet."}>
          {rows.map((b) => (
            <tr key={b.id}>
              <td className={`${td} font-mono text-xs`}>{b.file_name}<div className="text-slate-500 break-all">{b.sha256?.slice(0, 16)}…</div></td>
              <td className={`${td} text-xs`}>{fmtDateTime(b.started_at)}<div className="text-slate-500">{b.trigger.toLowerCase()}</div></td>
              <td className={tdNum}>{b.size_bytes ? `${(b.size_bytes / 1024).toFixed(0)} KB` : ""}</td>
              <td className={tdNum}>{b.total_rows ?? ""}</td>
              <td className={td}><Badge value={b.status === "COMPLETED" ? "SENT" : b.status === "FAILED" ? "FAILED" : "PENDING"} label={b.status.toLowerCase()} />{b.error && <div className="text-[11px] text-rose-300">{b.error}</div>}</td>
              <td className={td}>{b.verify_status ? <Badge value={b.verify_status === "PASSED" ? "SENT" : "FAILED"} label={b.verify_status.toLowerCase()} /> : <span className="text-xs text-slate-500">not run</span>}
                {b.verified_at && <div className="text-[11px] text-slate-500">{fmtDateTime(b.verified_at)}</div>}</td>
              <td className={td}>{b.status === "COMPLETED" && (
                <div className="flex gap-1">
                  <Btn variant="ghost" busy={busy} onClick={() => run(() => post(`/api/v1/admin/backups/${b.id}/verify`), "Verification finished.")}>Verify</Btn>
                  <a className="px-2 py-1 text-xs text-sky-300 hover:underline" href={`/api/v1/admin/backups/${b.id}/download`}>Download</a>
                </div>)}</td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

function AuditTab({ onError }: { onError: (e: unknown) => void }) {
  const [f, setF] = useState({ entity_type: "", action: "", date_from: "", date_to: "", q: "" });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; facets: { entity_types: string[]; actions: string[] }; items: AuditItem[] } | null>(null);
  const load = useCallback(() => {
    const qs = new URLSearchParams(Object.entries({ ...f, limit: "100", offset: String(offset) }).filter(([, v]) => v));
    fetchJson<typeof data>(`/api/v1/admin/audit-logs?${qs}`).then(setData).catch(onError);
  }, [f, offset, onError]);
  useEffect(load, [load]);
  return (
    <Card title="Audit log">
      <div className="flex flex-wrap gap-2 items-end mb-3">
        <Field label="Area"><select className={`${inputCls} w-44`} value={f.entity_type} onChange={(e) => { setOffset(0); setF({ ...f, entity_type: e.target.value }); }}>
          <option value="">All</option>{data?.facets.entity_types.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
        <Field label="Action starts with"><input className={`${inputCls} w-40`} list="audit-actions" value={f.action} onChange={(e) => { setOffset(0); setF({ ...f, action: e.target.value }); }} />
          <datalist id="audit-actions">{data?.facets.actions.map((a) => <option key={a} value={a} />)}</datalist></Field>
        <Field label="From"><input type="date" className={`${inputCls} w-40`} value={f.date_from} onChange={(e) => { setOffset(0); setF({ ...f, date_from: e.target.value }); }} /></Field>
        <Field label="To"><input type="date" className={`${inputCls} w-40`} value={f.date_to} onChange={(e) => { setOffset(0); setF({ ...f, date_to: e.target.value }); }} /></Field>
        <Field label="Record / reason contains"><input className={`${inputCls} w-44`} value={f.q} onChange={(e) => { setOffset(0); setF({ ...f, q: e.target.value }); }} /></Field>
      </div>
      {!data ? <Spinner /> : (
        <>
          <Table head={["When", "User", "Action", "Record", "Reason / details"]} empty={data.items.length === 0 && "No matching entries."}>
            {data.items.map((a) => (
              <tr key={a.id}>
                <td className={`${td} text-xs whitespace-nowrap`}>{fmtDateTime(a.timestamp)}</td>
                <td className={`${td} text-xs`}>{a.user_name || "system"}</td>
                <td className={`${td} font-mono text-xs`}>{a.action}</td>
                <td className={`${td} text-xs`}>{a.entity_type} {a.entity_id}</td>
                <td className={`${td} text-xs text-slate-400 max-w-md break-words`}>{a.reason}{a.metadata && <div className="font-mono text-[10px] text-slate-500">{a.metadata.slice(0, 300)}</div>}</td>
              </tr>
            ))}
          </Table>
          <Pager total={data.total} limit={100} offset={offset} onChange={setOffset} />
        </>
      )}
    </Card>
  );
}
