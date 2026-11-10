"use client";

/** General application settings (POS, invoices, credit, shifts, purchases, backups). Changes are audited. */
import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Btn, Card, ErrorBox, Notice, Spinner, Table, errorText, inputCls, td, useStaff } from "@/components/sales-ui";
import { fetchJson } from "@/lib/api";

interface Row { key: string; value: string; default: string; description: string }

export default function SettingsPage() {
  const { user, can } = useStaff();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const load = useCallback(() => { fetchJson<Row[]>("/api/v1/settings/general").then(setRows).catch((e) => setError(errorText(e))); }, []);
  useEffect(() => { if (user && can("settings.manage")) load(); }, [user, can, load]);
  const save = async (key: string) => {
    setError(null);
    try {
      await fetchJson(`/api/v1/settings/general/${key}`, { method: "PUT", body: JSON.stringify({ setting_value: edits[key] }) });
      setInfo(`${key} saved.`);
      setEdits((e) => { const n = { ...e }; delete n[key]; return n; });
      load();
    } catch (e) { setError(errorText(e)); }
  };
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-5xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><Settings className="w-6 h-6" /></div>
          <div><h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Settings</h1>
            <p className="text-sm text-slate-400">POS, invoices, credit, cash shifts, purchases and backups. WhatsApp settings are on their own page; the books start date is set in Accounts. Logos, names and themes: <a className="text-emerald-400 hover:underline" href="/settings/branding">Branding &amp; appearance</a>.</p></div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {info && <Notice tone="emerald">{info}</Notice>}
        {!user ? <Spinner /> : !can("settings.manage") ? <ErrorBox message="You do not have permission to change settings." /> : !rows ? <Spinner /> : (
          <Card title="Application settings">
            <Table head={["Setting", "Value", ""]}>
              {rows.map((r) => {
                const editing = r.key in edits;
                const value = editing ? edits[r.key] : r.value;
                const isBool = r.default === "true" || r.default === "false";
                return (
                  <tr key={r.key}>
                    <td className={td}><div className="text-sm text-slate-200">{r.description}</div><div className="font-mono text-[11px] text-slate-500">{r.key} · default “{r.default}”</div></td>
                    <td className={`${td} w-64`}>
                      {isBool ? (
                        <select className={inputCls} value={value} onChange={(e) => setEdits({ ...edits, [r.key]: e.target.value })}>
                          <option value="true">Yes</option><option value="false">No</option>
                        </select>
                      ) : (
                        <input className={inputCls} value={value} onChange={(e) => setEdits({ ...edits, [r.key]: e.target.value })} />
                      )}
                    </td>
                    <td className={td}>{editing && edits[r.key] !== r.value && <Btn variant="primary" onClick={() => save(r.key)}>Save</Btn>}</td>
                  </tr>
                );
              })}
            </Table>
          </Card>
        )}
      </main>
    </div>
  );
}
