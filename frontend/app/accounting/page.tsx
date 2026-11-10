"use client";

/**
 * Accounting (Phase 5A): setup, chart of accounts, journals, periods, opening balances,
 * trial balance / account ledger and the ledger integrity check.
 * Stage 5B: finalised sales, purchases, payments, expenses and stock post automatically;
 * the Postings tab lists failed or missing postings and compares control accounts.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { BookOpen, Download, Lock, Plus, ShieldCheck, Trash2, Unlock } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, Spinner, Stat, Table, errorText, inputCls, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { api } from "@/lib/api";
import { Account, FiscalYear, Journal, JournalLine, JournalListItem, OpeningBatch, OpeningLine, TrialBalanceRow, accountingApi, sumPaise } from "@/lib/accounting-api";
import { fmtDate, fmtDateTime, money, newKey, salesApi } from "@/lib/sales-api";

type Tab = "chart" | "journals" | "periods" | "opening" | "trial" | "integrity" | "postings" | "cutover" | "statements" | "bank";
const TABS: [Tab, string][] = [["chart", "Chart of accounts"], ["journals", "Journals"], ["periods", "Periods"],
  ["opening", "Opening balances"], ["trial", "Trial balance"], ["statements", "Financial statements"], ["bank", "Bank & settlements"],
  ["postings", "Automatic postings"], ["integrity", "Integrity check"], ["cutover", "Cutover summary"]];
const paise = (n: number) => money(n / 100);

export default function AccountingPage() {
  const { user, can } = useStaff();
  const [status, setStatus] = useState<Awaited<ReturnType<typeof accountingApi.status>> | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [tab, setTab] = useState<Tab>("chart");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const s = await accountingApi.status();
      setStatus(s);
      if (s.is_set_up) setAccounts(await accountingApi.accounts());
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => { if (user && can("accounting.view")) reload(); }, [user, can, reload]);

  const ok = (msg: string) => { setInfo(msg); setError(null); reload(); };
  const fail = (e: unknown) => { setError(errorText(e)); setInfo(null); };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><BookOpen className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Accounting</h1>
            <p className="text-sm text-slate-400">Double-entry general ledger{status?.books_start_date ? ` · books start ${fmtDate(status.books_start_date)}` : ""}</p>
          </div>
        </div>
        {!user ? <Spinner /> : !can("accounting.view") ? <ErrorBox message="You do not have access to accounting." /> : !status ? <Spinner /> : (
          <>
            <ErrorBox message={error} onClose={() => setError(null)} />
            {info && <Notice tone="emerald">{info}</Notice>}
            {!status.is_set_up ? (
              <>
                <SetupCard canSetup={can("accounting.accounts_manage")} onDone={ok} onError={fail} />
                <CutoverCard onError={fail} />
              </>
            ) : (
              <>
                <Notice tone="amber">Finalised sales, receipts, returns, supplier bills and payments, approved expenses, cash movements and stock adjustments dated on or after the books start are posted automatically. Anything that could not be posted is listed under <b>Automatic postings</b>.</Notice>
                <nav className="flex gap-1 overflow-x-auto pb-1">
                  {TABS.map(([k, label]) => (
                    <button key={k} onClick={() => { setTab(k); setInfo(null); }} className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap border ${tab === k ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30" : "border-transparent text-slate-400 hover:bg-slate-800"}`}>{label}</button>
                  ))}
                </nav>
                {tab === "chart" && <ChartTab accounts={accounts} can={can} onDone={ok} onError={fail} />}
                {tab === "journals" && <JournalsTab accounts={accounts} can={can} onDone={ok} onError={fail} />}
                {tab === "periods" && <PeriodsTab can={can} onDone={ok} onError={fail} />}
                {tab === "opening" && <OpeningTab accounts={accounts} can={can} onDone={ok} onError={fail} />}
                {tab === "trial" && <TrialTab accounts={accounts} can={can} onError={fail} />}
                {tab === "integrity" && <IntegrityTab onError={fail} />}
                {tab === "postings" && <PostingsTab can={can} onDone={ok} onError={fail} />}
                {tab === "cutover" && <CutoverCard onError={fail} />}
                {tab === "statements" && <StatementsTab can={can} onError={fail} />}
                {tab === "bank" && <BankTab accounts={accounts} can={can} onDone={ok} onError={fail} />}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

type TabProps = { can: (p: string) => boolean; onDone: (m: string) => void; onError: (e: unknown) => void };

function SetupCard({ canSetup, onDone, onError }: { canSetup: boolean; onDone: (m: string) => void; onError: (e: unknown) => void }) {
  const [start, setStart] = useState(isoToday().slice(0, 8) + "01");
  const [busy, setBusy] = useState(false);
  return (
    <Card title="Set up accounting">
      <p className="text-sm text-slate-300">Choose the date your books start. Opening balances (cash, bank, stock, udhaar, supplier dues, capital) will be entered as at this date. Months before it are locked.</p>
      <ul className="text-xs text-slate-400 list-disc pl-5 my-3 space-y-1">
        <li>Creates a starting chart of accounts for an Indian retail pharmacy (review it with your CA) and the fiscal year (April–March) with monthly periods.</li>
        <li>From this date every finalised sale, purchase, payment and expense is posted automatically. Pick a date <b>after</b> any test transactions; anything earlier belongs in the opening balances.</li>
      </ul>
      {canSetup ? (
        <div className="flex gap-2 items-end">
          <Field label="Books start date"><input type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Btn variant="primary" busy={busy} onClick={async () => {
            setBusy(true);
            try { await accountingApi.setup(start); onDone("Accounting set up."); } catch (e) { onError(e); } finally { setBusy(false); }
          }}>Set up accounting</Btn>
        </div>
      ) : <ErrorBox message="An administrator with 'accounting.accounts_manage' must set up accounting." />}
    </Card>
  );
}

// ── Chart of accounts ──────────────────────────────────────────────────────────

function depthOf(a: Account, byId: Map<number, Account>): number {
  let d = 0, p = a.parent_id;
  while (p && byId.get(p)) { d++; p = byId.get(p)!.parent_id; }
  return d;
}

function ChartTab({ accounts, can, onDone, onError }: TabProps & { accounts: Account[] }) {
  const byId = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const parents = useMemo(() => new Set(accounts.map((a) => a.parent_id).filter(Boolean)), [accounts]);
  const [adding, setAdding] = useState<Account | null>(null);
  const manage = can("accounting.accounts_manage");
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); onDone(msg); } catch (e) { onError(e); } };
  return (
    <Card title={`${accounts.length} accounts`}>
      <Table head={["Code", "Account", "Type", "Flags", "Balance ₹", ""]}>
        {accounts.map((a) => (
          <tr key={a.id} className={a.is_active ? "" : "opacity-50"}>
            <td className={`${td} font-mono text-xs`}>{a.code}</td>
            <td className={td}>
              <span style={{ paddingLeft: depthOf(a, byId) * 16 }} className={a.is_group ? "font-semibold text-slate-200" : "text-slate-300"}>{a.name}</span>
              {a.description && <div style={{ paddingLeft: depthOf(a, byId) * 16 }} className="text-[11px] text-slate-500">{a.description}</div>}
            </td>
            <td className={`${td} text-xs`}>{a.account_type.toLowerCase()}{a.normal_balance !== (["ASSET", "EXPENSE"].includes(a.account_type) ? "DEBIT" : "CREDIT") && " (contra)"}</td>
            <td className={td}>
              <div className="flex flex-wrap gap-1">
                {a.is_group && <Badge value="QUEUED" label="Group" />}
                {a.system_key && <Badge value="SENT" label="System" />}
                {a.is_control && <Badge value="PENDING" label="Control" />}
                {a.party_type && <Badge value="QUEUED" label={`Per ${a.party_type.toLowerCase()}`} />}
                {!a.is_active && <Badge value="VOIDED" label="Inactive" />}
              </div>
            </td>
            <td className={tdNum}>{a.is_group ? "" : money(a.balance)}</td>
            <td className={td}>
              {manage && (
                <div className="flex gap-1">
                  {a.is_group && <Btn variant="ghost" onClick={() => setAdding(a)} title="Add sub-account"><Plus className="w-4 h-4" /></Btn>}
                  {!a.system_key && !a.has_postings && !parents.has(a.id) && (
                    <Btn variant="ghost" onClick={() => act(() => accountingApi.updateAccount(a.id, { is_active: !a.is_active }), `${a.name} ${a.is_active ? "deactivated" : "activated"}`)}>{a.is_active ? "Deactivate" : "Activate"}</Btn>
                  )}
                  {!a.system_key && !a.has_postings && !parents.has(a.id) && (
                    <Btn variant="ghost" title="Delete (never used)" onClick={() => confirm(`Delete ${a.code} ${a.name}?`) && act(() => accountingApi.deleteAccount(a.id), `${a.name} deleted`)}><Trash2 className="w-4 h-4" /></Btn>
                  )}
                </div>
              )}
            </td>
          </tr>
        ))}
      </Table>
      <div className="text-xs text-slate-500 mt-3">System accounts are used by automatic postings; control accounts (receivables, payables, inventory, advances) are fed only by sales, purchases, stock and opening balances. Accounts with postings can be renamed but never deleted or deactivated.</div>
      {adding && <AddAccountModal parent={adding} onClose={() => setAdding(null)} onDone={(m) => { setAdding(null); onDone(m); }} onError={onError} />}
    </Card>
  );
}

function AddAccountModal({ parent, onClose, onDone, onError }: { parent: Account; onClose: () => void; onDone: (m: string) => void; onError: (e: unknown) => void }) {
  const [f, setF] = useState({ code: "", name: "", is_group: false, description: "" });
  const [busy, setBusy] = useState(false);
  return (
    <Modal open title={`New account under ${parent.code} ${parent.name}`} onClose={onClose}>
      <div className="grid grid-cols-3 gap-2">
        <Field label="Code *"><input className={inputCls} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <div className="col-span-2"><Field label="Name *"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field></div>
      </div>
      <Field label="Description"><input className={inputCls} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={f.is_group} onChange={(e) => setF({ ...f, is_group: e.target.checked })} /> Group (holds sub-accounts; no postings)</label>
      <div className="text-xs text-slate-500">Type: {parent.account_type.toLowerCase()} (inherited)</div>
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={!f.code || f.name.length < 2} onClick={async () => {
          setBusy(true);
          try { await accountingApi.createAccount({ ...f, account_type: parent.account_type, parent_id: parent.id }); onDone(`Account ${f.code} created`); }
          catch (e) { onError(e); } finally { setBusy(false); }
        }}>Create</Btn>
      </div>
    </Modal>
  );
}

// ── Journals ───────────────────────────────────────────────────────────────────

function JournalsTab({ accounts, can, onDone, onError }: TabProps & { accounts: Account[] }) {
  const [rows, setRows] = useState<JournalListItem[] | null>(null);
  const [filter, setFilter] = useState({ status: "", q: "" });
  const [open, setOpen] = useState<number | "new" | null>(null);
  const load = useCallback(() => {
    accountingApi.journals({ status: filter.status || undefined, q: filter.q || undefined, limit: 100 })
      .then((r) => setRows(r.items)).catch(onError);
  }, [filter, onError]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);
  return (
    <Card title="Journal register" actions={can("accounting.journal_create") && <Btn variant="primary" onClick={() => setOpen("new")}><Plus className="w-4 h-4" /> Manual journal</Btn>}>
      <div className="flex gap-2 mb-3">
        <input className={`${inputCls} max-w-xs`} placeholder="Number, narration, reference" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
        <select className={`${inputCls} max-w-[160px]`} value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
          <option value="">All statuses</option><option value="DRAFT">Draft</option><option value="POSTED">Posted</option><option value="REVERSED">Reversed</option>
        </select>
      </div>
      <Table head={["Number", "Date", "Source", "Narration", "Status", "Amount ₹"]} empty={rows !== null && rows.length === 0 && "No journal entries yet."}>
        {(rows || []).map((j) => (
          <tr key={j.id} className="hover:bg-slate-800/30 cursor-pointer" onClick={() => setOpen(j.id)}>
            <td className={`${td} font-mono text-xs text-emerald-300`}>{j.entry_number || `Draft #${j.id}`}</td>
            <td className={td}>{fmtDate(j.entry_date)}</td>
            <td className={`${td} text-xs`}>{j.source_type.toLowerCase()}</td>
            <td className={`${td} text-xs text-slate-300 max-w-md`}>{j.narration}</td>
            <td className={td}><Badge value={j.status === "POSTED" ? "PAID" : j.status === "DRAFT" ? "PENDING" : "VOIDED"} label={j.status.toLowerCase()} /></td>
            <td className={tdNum}>{j.status === "DRAFT" ? "—" : money(j.total_debit)}</td>
          </tr>
        ))}
      </Table>
      {open !== null && <JournalModal id={open === "new" ? null : open} accounts={accounts} can={can}
        onClose={() => setOpen(null)} onDone={(m) => { setOpen(null); onDone(m); load(); }} onError={onError} />}
    </Card>
  );
}

const blankLine = (): JournalLine => ({ account_id: 0, debit: "", credit: "", description: "" });

function JournalModal({ id, accounts, can, onClose, onDone, onError }: TabProps & { id: number | null; accounts: Account[]; onClose: () => void }) {
  const [j, setJ] = useState<Journal | null>(null);
  const [form, setForm] = useState({ entry_date: isoToday(), narration: "", reference: "", lines: [blankLine(), blankLine()], key: newKey() });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (id) accountingApi.journal(id).then((x) => {
      setJ(x);
      setForm({ entry_date: x.entry_date, narration: x.narration, reference: x.reference || "", key: newKey(),
        lines: x.lines.map((l) => ({ account_id: l.account_id, debit: Number(l.debit) ? l.debit : "", credit: Number(l.credit) ? l.credit : "", description: l.description || "" })) });
    }).catch((e) => setErr(errorText(e)));
  }, [id]);
  const editable = !id || j?.status === "DRAFT";
  const postable = accounts.filter((a) => !a.is_group && a.is_active && !a.is_control);
  const dr = sumPaise(form.lines.map((l) => l.debit)), cr = sumPaise(form.lines.map((l) => l.credit));
  const setLine = (i: number, patch: Partial<JournalLine>) => setForm({ ...form, lines: form.lines.map((l, k) => (k === i ? { ...l, ...patch } : l)) });
  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true); setErr(null);
    try { await fn(); onDone(msg); } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  const save = async (andPost: boolean) => {
    const lines = form.lines.filter((l) => l.account_id).map((l) => ({ ...l, debit: l.debit || "0", credit: l.credit || "0" }));
    const saved = await accountingApi.saveDraft(id, { idempotency_key: id ? undefined : form.key, entry_date: form.entry_date, narration: form.narration, reference: form.reference || undefined, lines });
    if (andPost) await accountingApi.postDraft(saved.id);
  };
  if (id && !j) return <Modal open title="Journal" onClose={onClose}>{err ? <ErrorBox message={err} /> : <Spinner />}</Modal>;
  return (
    <Modal open wide title={j ? `${j.entry_number || `Draft #${j.id}`} · ${j.status.toLowerCase()}` : "New manual journal"} onClose={onClose}>
      <ErrorBox message={err} />
      {j && j.status !== "DRAFT" && (
        <div className="text-xs text-slate-400 space-y-0.5">
          <div>Source: {j.source_type.toLowerCase()} · period {j.period_name} · posted by {j.posted_by_name} {fmtDateTime(j.posted_at)}</div>
          {j.reversal_of_number && <div>Reverses {j.reversal_of_number}</div>}
          {j.reversed_by_number && <div className="text-amber-300">Reversed by {j.reversed_by_number}: {j.reversal_reason}</div>}
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
        <Field label="Date"><input type="date" disabled={!editable} className={inputCls} value={form.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} /></Field>
        <div className="sm:col-span-2"><Field label="Narration *"><input disabled={!editable} className={inputCls} value={form.narration} onChange={(e) => setForm({ ...form, narration: e.target.value })} /></Field></div>
        <Field label="Reference"><input disabled={!editable} className={inputCls} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
      </div>
      <div className="space-y-1">
        {form.lines.map((l, i) => (
          <div key={i} className="grid grid-cols-[1fr_110px_110px_1fr_auto] gap-1 items-center">
            {editable ? (
              <select className={inputCls} value={l.account_id || ""} onChange={(e) => setLine(i, { account_id: Number(e.target.value) })}>
                <option value="">Account…</option>
                {postable.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
              </select>
            ) : <div className="text-sm">{j?.lines[i]?.account_code} {j?.lines[i]?.account_name}{j?.lines[i]?.party_name && <span className="text-xs text-slate-500"> · {j.lines[i].party_name}</span>}</div>}
            <input disabled={!editable} placeholder="Debit" inputMode="decimal" className={`${inputCls} font-mono`} value={l.debit} onChange={(e) => setLine(i, { debit: e.target.value.replace(/[^0-9.]/g, ""), credit: "" })} />
            <input disabled={!editable} placeholder="Credit" inputMode="decimal" className={`${inputCls} font-mono`} value={l.credit} onChange={(e) => setLine(i, { credit: e.target.value.replace(/[^0-9.]/g, ""), debit: "" })} />
            <input disabled={!editable} placeholder="Line note" className={inputCls} value={l.description || ""} onChange={(e) => setLine(i, { description: e.target.value })} />
            {editable && form.lines.length > 2 ? <button className="text-slate-500 hover:text-rose-300" onClick={() => setForm({ ...form, lines: form.lines.filter((_, k) => k !== i) })} aria-label="Remove line"><Trash2 className="w-4 h-4" /></button> : <span />}
          </div>
        ))}
        {editable && <Btn variant="ghost" onClick={() => setForm({ ...form, lines: [...form.lines, blankLine()] })}><Plus className="w-4 h-4" /> Line</Btn>}
      </div>
      <div className={`flex justify-between text-sm font-mono border-t border-slate-800 pt-2 ${dr === cr && dr > 0 ? "text-emerald-300" : "text-amber-300"}`}>
        <span>Debits {paise(dr)} · Credits {paise(cr)}</span>
        <span>{dr === cr ? (dr > 0 ? "Balanced" : "") : `Difference ${paise(Math.abs(dr - cr))}`}</span>
      </div>
      {editable && <div className="text-[11px] text-slate-500">Receivables, payables, inventory and advances are not offered here: they are posted by their own documents or opening balances.</div>}
      <div className="flex flex-wrap justify-end gap-2">
        {j?.status === "DRAFT" && can("accounting.journal_create") && <Btn variant="danger" busy={busy} onClick={() => run(() => accountingApi.deleteDraft(j.id), "Draft deleted")}>Delete draft</Btn>}
        {editable && can("accounting.journal_create") && <Btn busy={busy} disabled={form.narration.length < 3} onClick={() => run(() => save(false), "Draft saved")}>Save draft</Btn>}
        {editable && can("accounting.journal_post") && <Btn variant="primary" busy={busy} disabled={dr !== cr || dr === 0 || form.narration.length < 3} onClick={() => run(() => save(true), "Journal posted")}>Save & post</Btn>}
      </div>
      {j?.status === "POSTED" && !j.reversal_of_id && ["MANUAL", "OPENING"].includes(j.source_type) && can("accounting.journal_reverse") && (
        <div className="border-t border-slate-800 pt-3 flex gap-2 items-end">
          <div className="flex-1"><Field label="Reverse this entry — reason *"><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
          <Btn variant="danger" busy={busy} disabled={reason.trim().length < 5} onClick={() => run(() => accountingApi.reverse(j.id, reason), "Entry reversed; the original is kept and marked reversed")}>Reverse</Btn>
        </div>
      )}
    </Modal>
  );
}

// ── Periods ────────────────────────────────────────────────────────────────────

function PeriodsTab({ can, onDone, onError }: TabProps) {
  const [years, setYears] = useState<FiscalYear[] | null>(null);
  const load = useCallback(() => { accountingApi.periods().then(setYears).catch(onError); }, [onError]);
  useEffect(load, [load]);
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); onDone(msg); load(); } catch (e) { onError(e); } };
  const last = years?.[years.length - 1];
  return (
    <div className="space-y-4">
      {(years || []).map((y) => (
        <Card key={y.id} title={`Fiscal year ${y.name} (${fmtDate(y.start_date)} – ${fmtDate(y.end_date)})`} actions={can("accounting.year_end") && (
          y.status === "CLOSED" ? (
            <div className="flex items-center gap-2"><Badge value="CLOSED" label="Year closed" />
              {can("accounting.period_reopen") && <Btn variant="ghost" onClick={() => { const r = prompt(`Reopen ${y.name}? The year-end entry will be reversed. Reason (at least 10 characters):`); if (r) act(() => accountingApi.reopenYear(y.id, r), `${y.name} reopened`); }}><Unlock className="w-4 h-4" /> Reopen year</Btn>}
            </div>
          ) : (
            <Btn variant="ghost" onClick={() => confirm(`Close ${y.name}? Income and expenses are moved to Retained Earnings and the last period is closed. Every other period must already be closed.`) && act(() => accountingApi.closeYear(y.id), `${y.name} closed`)}><Lock className="w-4 h-4" /> Close year</Btn>
          ))}>
          <Table head={["Period", "Status", "Entries", "Closed / reopened", ""]}>
            {y.periods.map((p) => (
              <tr key={p.id}>
                <td className={td}>{p.name}</td>
                <td className={td}><Badge value={p.status === "OPEN" ? "OPEN" : p.status === "CLOSED" ? "CLOSED" : "CANCELLED"} label={p.status === "PRE_OPENING" ? "Before books" : p.status.toLowerCase()} /></td>
                <td className={tdNum}>{p.entries}</td>
                <td className={`${td} text-xs text-slate-400`}>
                  {p.closed_at && <div>Closed {fmtDateTime(p.closed_at)} by {p.closed_by}</div>}
                  {p.reopen_reason && <div>Reopened by {p.reopened_by}: {p.reopen_reason}</div>}
                </td>
                <td className={td}>
                  {p.status === "OPEN" && can("accounting.period_close") && <Btn variant="ghost" onClick={() => confirm(`Close ${p.name}? No posting will be possible until it is reopened.`) && act(() => accountingApi.closePeriod(p.id), `${p.name} closed`)}><Lock className="w-4 h-4" /> Close</Btn>}
                  {p.status === "CLOSED" && can("accounting.period_reopen") && <Btn variant="ghost" onClick={() => { const r = prompt("Why must this period be reopened? (recorded in the audit log)"); if (r) act(() => accountingApi.reopenPeriod(p.id, r), `${p.name} reopened`); }}><Unlock className="w-4 h-4" /> Reopen</Btn>}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      ))}
      {last && can("accounting.accounts_manage") && (
        <Btn onClick={() => {
          const s = new Date(`${last.end_date}T00:00:00`); s.setDate(s.getDate() + 1);
          const e = new Date(s); e.setFullYear(e.getFullYear() + 1); e.setDate(e.getDate() - 1);
          const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
          act(() => accountingApi.createFiscalYear(iso(s), iso(e)), "Next fiscal year created");
        }}><Plus className="w-4 h-4" /> Create next fiscal year</Btn>
      )}
    </div>
  );
}

// ── Opening balances ───────────────────────────────────────────────────────────

function OpeningTab({ accounts, can, onDone, onError }: TabProps & { accounts: Account[] }) {
  const [batch, setBatch] = useState<OpeningBatch | null | undefined>(undefined);
  const [form, setForm] = useState<{ source_description: string; post_difference_to_suspense: boolean; lines: OpeningLine[] }>({ source_description: "", post_difference_to_suspense: false, lines: [] });
  const [customers, setCustomers] = useState<{ id: number; name: string }[]>([]);
  const [suppliers, setSuppliers] = useState<{ id: number; name: string }[]>([]);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const b = await accountingApi.opening();
      setBatch(b);
      if (b) setForm({ source_description: b.source_description, post_difference_to_suspense: b.post_difference_to_suspense,
        lines: b.lines.map((l) => ({ ...l, debit: Number(l.debit) ? l.debit : "", credit: Number(l.credit) ? l.credit : "" })) });
    } catch (e) { onError(e); }
  }, [onError]);
  useEffect(() => {
    load();
    salesApi.customers({ limit: 200 }).then((r) => setCustomers(r.items)).catch(() => undefined);
    api.getSuppliers({ limit: 200 }).then((r) => setSuppliers(r as { id: number; name: string }[])).catch(() => undefined);
  }, [load]);
  const editable = can("accounting.opening_balances") && (!batch || batch.status === "DRAFT" || batch.status === "REVERSED");
  const usable = accounts.filter((a) => !a.is_group && a.is_active && ["ASSET", "LIABILITY", "EQUITY"].includes(a.account_type) && a.system_key !== "OPENING_DIFFERENCE");
  const acc = (id: number) => accounts.find((a) => a.id === id);
  const setLine = (i: number, patch: Partial<OpeningLine>) => setForm({ ...form, lines: form.lines.map((l, k) => (k === i ? { ...l, ...patch } : l)) });
  const save = async () => {
    setBusy(true);
    try {
      const b = await accountingApi.saveOpening(batch && batch.status === "DRAFT" ? batch.id : null, {
        ...form, lines: form.lines.filter((l) => l.account_id).map((l) => ({ ...l, debit: l.debit || "0", credit: l.credit || "0",
          document_date: l.document_date || null, due_date: l.due_date || null })) });
      setBatch(b);
    } catch (e) { onError(e); } finally { setBusy(false); }
  };
  if (batch === undefined) return <Spinner />;
  const p = batch?.preview;
  return (
    <div className="space-y-4">
      {batch?.status === "POSTED" && <Notice tone="emerald">Opening balances are posted (journal #{batch.journal_entry_id}). They can no longer be edited; corrections need a journal, or reversing the opening entry with permission.</Notice>}
      <Card title={`Opening balances as at ${batch ? fmtDate(batch.opening_date) : "the books' start date"}`}>
        <Field label="Source of these figures *" hint="e.g. 'Closing balance sheet 30-09-2026 from Tally, certified by CA A. Mehta'">
          <input disabled={!editable} className={inputCls} value={form.source_description} onChange={(e) => setForm({ ...form, source_description: e.target.value })} />
        </Field>
        <div className="space-y-2 mt-3">
          {form.lines.map((l, i) => {
            const a = acc(l.account_id);
            return (
              <div key={i} className="border border-slate-800 rounded-lg p-2 space-y-1">
                <div className="grid grid-cols-[1fr_120px_120px_auto] gap-1">
                  <select disabled={!editable} className={inputCls} value={l.account_id || ""} onChange={(e) => setLine(i, { account_id: Number(e.target.value), party_type: acc(Number(e.target.value))?.party_type || null, party_id: null })}>
                    <option value="">Account…</option>
                    {usable.map((x) => <option key={x.id} value={x.id}>{x.code} {x.name}</option>)}
                  </select>
                  <input disabled={!editable} placeholder="Debit" className={`${inputCls} font-mono`} value={l.debit} onChange={(e) => setLine(i, { debit: e.target.value.replace(/[^0-9.]/g, ""), credit: "" })} />
                  <input disabled={!editable} placeholder="Credit" className={`${inputCls} font-mono`} value={l.credit} onChange={(e) => setLine(i, { credit: e.target.value.replace(/[^0-9.]/g, ""), debit: "" })} />
                  {editable ? <button className="text-slate-500 hover:text-rose-300" onClick={() => setForm({ ...form, lines: form.lines.filter((_, k) => k !== i) })} aria-label="Remove"><Trash2 className="w-4 h-4" /></button> : <span />}
                </div>
                {a?.party_type && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-1">
                    <select disabled={!editable} className={inputCls} value={l.party_id || ""} onChange={(e) => setLine(i, { party_id: Number(e.target.value) })}>
                      <option value="">{a.party_type === "CUSTOMER" ? "Customer…" : "Supplier…"}</option>
                      {(a.party_type === "CUSTOMER" ? customers : suppliers).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                    </select>
                    <input disabled={!editable} placeholder="Bill / khata no." className={inputCls} value={l.document_number || ""} onChange={(e) => setLine(i, { document_number: e.target.value })} />
                    <input disabled={!editable} type="date" title="Document date" className={inputCls} value={l.document_date || ""} onChange={(e) => setLine(i, { document_date: e.target.value })} />
                    <input disabled={!editable} type="date" title="Due date" className={inputCls} value={l.due_date || ""} onChange={(e) => setLine(i, { due_date: e.target.value })} />
                  </div>
                )}
              </div>
            );
          })}
          {editable && <Btn variant="ghost" onClick={() => setForm({ ...form, lines: [...form.lines, { account_id: 0, debit: "", credit: "" }] })}><Plus className="w-4 h-4" /> Balance</Btn>}
        </div>
        {editable && (
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-3">
            <input type="checkbox" checked={form.post_difference_to_suspense} onChange={(e) => setForm({ ...form, post_difference_to_suspense: e.target.checked })} />
            If debits and credits differ, post the difference to &ldquo;Opening Balance Difference (Suspense)&rdquo; for later investigation
          </label>
        )}
        {editable && <div className="flex justify-end mt-3"><Btn variant="primary" busy={busy} disabled={form.source_description.length < 5} onClick={save}>Save &amp; check</Btn></div>}
      </Card>
      {p && (
        <Card title="Check before posting">
          <div className="grid grid-cols-3 gap-3 mb-3">
            <Stat label="Debits" value={money(p.total_debit)} />
            <Stat label="Credits" value={money(p.total_credit)} />
            <Stat label="Difference" value={money(p.difference)} tone={Number(p.difference) ? "rose" : "emerald"} />
          </div>
          {p.errors.map((e) => <ErrorBox key={e} message={e} />)}
          {p.warnings.map((w) => <div key={w} className="mt-1"><Notice tone="amber">{w}</Notice></div>)}
          <div className="mt-3">
            <Table head={["Compared with ERP", "Entered ₹", "ERP ₹", "Difference ₹", "Basis"]}>
              {p.reconciliation.map((r) => (
                <tr key={r.item}><td className={td}>{r.item}</td><td className={tdNum}>{money(r.entered)}</td><td className={tdNum}>{money(r.erp)}</td>
                  <td className={`${tdNum} ${Number(r.difference) ? "text-amber-300" : ""}`}>{money(r.difference)}</td><td className={`${td} text-xs text-slate-500`}>{r.basis}</td></tr>
              ))}
            </Table>
          </div>
          {batch?.status === "DRAFT" && can("accounting.opening_balances") && can("accounting.journal_post") && (
            <div className="flex items-center justify-end gap-3 mt-3">
              {p.warnings.length > 0 && <label className="text-sm text-slate-300 flex items-center gap-2"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have reviewed the warnings</label>}
              <Btn variant="primary" busy={busy} disabled={!p.can_post || (p.warnings.length > 0 && !ack)} onClick={async () => {
                setBusy(true);
                try { const r = await accountingApi.postOpening(batch.id, ack); onDone(`Opening balances posted as ${r.entry_number}`); load(); }
                catch (e) { onError(e); } finally { setBusy(false); }
              }}>Post opening balances</Btn>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

// ── Trial balance & ledger ─────────────────────────────────────────────────────

function TrialTab({ accounts, can, onError }: { accounts: Account[]; can: (p: string) => boolean; onError: (e: unknown) => void }) {
  const [range, setRange] = useState({ from: "", to: isoToday() });
  const [data, setData] = useState<{ rows: TrialBalanceRow[]; totals: Record<string, string>; is_balanced: boolean } | null>(null);
  const [ledgerId, setLedgerId] = useState<number | null>(null);
  useEffect(() => { accountingApi.trialBalance(range.from || undefined, range.to).then(setData).catch(onError); }, [range, onError]);
  return (
    <Card title="Trial balance" actions={
      <div className="flex gap-2 items-end">
        <Field label="From"><input type="date" className={inputCls} value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
        <Field label="To"><input type="date" className={inputCls} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
        {can("accounting.export") && <a className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline pb-2" href={accountingApi.trialBalanceCsv(range.from || undefined, range.to)}><Download className="w-3 h-3" /> CSV</a>}
      </div>
    }>
      {!data ? <Spinner /> : (
        <>
          <div className="mb-3">{data.is_balanced ? <Notice tone="emerald"><ShieldCheck className="w-4 h-4 inline" /> Balanced: debits equal credits.</Notice> : <ErrorBox message="NOT BALANCED — run the integrity check." />}</div>
          <Table head={["Code", "Account", "Opening Dr ₹", "Opening Cr ₹", "Debit ₹", "Credit ₹", "Closing Dr ₹", "Closing Cr ₹"]} empty={data.rows.length === 0 && "No postings in this range."}>
            {data.rows.map((r) => (
              <tr key={r.account_id} className="hover:bg-slate-800/30 cursor-pointer" onClick={() => setLedgerId(r.account_id)}>
                <td className={`${td} font-mono text-xs`}>{r.code}</td><td className={td}>{r.name}</td>
                {(["opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"] as const).map((k) => <td key={k} className={tdNum}>{Number(r[k]) ? money(r[k]) : ""}</td>)}
              </tr>
            ))}
            <tr className="font-bold border-t-2 border-slate-700">
              <td className={td} /><td className={td}>Total</td>
              {["opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"].map((k) => <td key={k} className={tdNum}>{money(data.totals[k])}</td>)}
            </tr>
          </Table>
        </>
      )}
      {ledgerId && <LedgerModal id={ledgerId} account={accounts.find((a) => a.id === ledgerId)} range={range} onClose={() => setLedgerId(null)} />}
    </Card>
  );
}

function LedgerModal({ id, account, range, onClose }: { id: number; account?: Account; range: { from: string; to: string }; onClose: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof accountingApi.ledger>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { accountingApi.ledger(id, range.from || undefined, range.to).then(setData).catch((e) => setErr(errorText(e))); }, [id, range]);
  return (
    <Modal open wide title={`Ledger · ${account?.code} ${account?.name}`} onClose={onClose}>
      <ErrorBox message={err} />
      {data && (
        <>
          <div className="text-xs text-slate-400">Opening {money(data.opening_balance)} · Closing {money(data.closing_balance)} (debit positive)</div>
          <Table head={["Date", "Entry", "Narration", "Debit ₹", "Credit ₹", "Balance ₹"]} empty={data.entries.length === 0 && "No postings."}>
            {data.entries.map((e, i) => (
              <tr key={i}><td className={td}>{fmtDate(e.entry_date)}</td><td className={`${td} font-mono text-xs`}>{e.entry_number}{e.status === "REVERSED" && " (rev.)"}</td>
                <td className={`${td} text-xs`}>{e.narration}{e.description && <div className="text-slate-500">{e.description}</div>}</td>
                <td className={tdNum}>{Number(e.debit) ? money(e.debit) : ""}</td><td className={tdNum}>{Number(e.credit) ? money(e.credit) : ""}</td><td className={tdNum}>{money(e.balance)}</td></tr>
            ))}
          </Table>
        </>
      )}
    </Modal>
  );
}

function IntegrityTab({ onError }: { onError: (e: unknown) => void }) {
  const [r, setR] = useState<Awaited<ReturnType<typeof accountingApi.integrity>> | null>(null);
  const run = () => accountingApi.integrity().then(setR).catch(onError);
  useEffect(() => { run(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card title="Ledger integrity check" actions={<Btn onClick={run}>Run again</Btn>}>
      <p className="text-xs text-slate-400 mb-3">Re-adds every posted entry, compares it with its stored totals and its fingerprint taken at posting, and checks that the whole ledger balances. Any change made directly in the database shows up here.</p>
      {!r ? <Spinner /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <Stat label="Result" value={r.ok ? "All good" : "PROBLEMS"} tone={r.ok ? "emerald" : "rose"} />
            <Stat label="Entries / lines" value={`${r.entries_checked} / ${r.lines_checked}`} />
            <Stat label="Total debits" value={money(r.total_debit)} />
            <Stat label="Total credits" value={money(r.total_credit)} />
          </div>
          {r.problems.map((p, i) => <ErrorBox key={i} message={`${p.entry}: ${p.problem}`} />)}
        </>
      )}
    </Card>
  );
}

function PostingsTab({ can, onDone, onError }: TabProps) {
  const [r, setR] = useState<Awaited<ReturnType<typeof accountingApi.postingReconciliation>> | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(() => { setR(null); accountingApi.postingReconciliation().then(setR).catch(onError); }, [onError]);
  useEffect(() => { run(); }, [run]);
  const retry = async (key?: string) => {
    setBusy(true);
    try {
      const res = await accountingApi.retryPostings(key);
      onDone(`Retry: ${res.summary.posted} posted, ${res.summary.failed} still failing, ${res.summary.already_posted} already posted.`);
      run();
    } catch (e) { onError(e); } finally { setBusy(false); }
  };
  const manage = can("accounting.postings_manage");
  return (
    <Card title="Automatic postings" actions={
      <div className="flex gap-2">
        <Btn onClick={run}>Check again</Btn>
        {manage && r && (r.open_issues.length > 0 || r.missing.length > 0) && <Btn variant="primary" busy={busy} onClick={() => retry()}>Retry all</Btn>}
      </div>
    }>
      <p className="text-xs text-slate-400 mb-3">Every eligible document since the books start is checked: it must have exactly one journal whose amounts still match the document. Control accounts are compared with the khata, supplier balances and stock value.</p>
      {!r ? <Spinner /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Stat label="Result" value={r.ok ? "All good" : "Needs attention"} tone={r.ok ? "emerald" : "rose"} />
            <Stat label="Documents checked" value={String(r.checked)} />
            <Stat label="Failed postings" value={String(r.open_issues.length)} tone={r.open_issues.length ? "rose" : "slate"} />
            <Stat label="Missing journals" value={String(r.missing.length)} tone={r.missing.length ? "amber" : "slate"} />
            <Stat label="Changed since posting" value={String(r.mismatched.length)} tone={r.mismatched.length ? "rose" : "slate"} />
          </div>
          {r.open_issues.length > 0 && (
            <Table head={["Document", "Date", "Error", "Attempts", ""]}>
              {r.open_issues.map((i) => (
                <tr key={i.id}>
                  <td className={`${td} font-mono text-xs`}>{i.source_key}</td>
                  <td className={`${td} text-xs`}>{i.event_date ? fmtDate(i.event_date) : ""}</td>
                  <td className={`${td} text-xs text-rose-300`}>{i.error}</td>
                  <td className={tdNum}>{i.attempts}</td>
                  <td className={td}>{manage && <Btn variant="ghost" busy={busy} onClick={() => retry(i.source_key)}>Retry</Btn>}</td>
                </tr>
              ))}
            </Table>
          )}
          {r.missing.length > 0 && (
            <div>
              <div className="text-xs text-amber-300 font-semibold mb-1">Documents without a journal</div>
              <ul className="text-xs text-slate-300 space-y-0.5">{r.missing.slice(0, 50).map((m) => <li key={m.source_key}><span className="font-mono">{m.source_key}</span> · {fmtDate(m.date)} · {m.narration}</li>)}</ul>
            </div>
          )}
          {[...r.mismatched.map((m) => `${m.source_key} (${m.entry_number}): the document changed after posting — investigate; it is never re-posted silently`),
            ...r.unbuildable.map((u) => `${u.source_key}: ${u.error}`)].map((t) => <ErrorBox key={t} message={t} />)}
          <Table head={["Control account", "General ledger ₹", "Operational ₹", "Status"]}>
            {r.controls.map((c) => (
              <tr key={c.control}>
                <td className={td}>{c.control}{c.differences.length > 0 && <div className="text-[11px] text-slate-500">{c.differences.length} party difference(s)</div>}</td>
                <td className={tdNum}>{money(c.gl)}</td>
                <td className={tdNum}>{money(c.operational)}</td>
                <td className={td}><Badge value={c.ok ? "SENT" : "FAILED"} label={c.ok ? "Agrees" : "Differs"} /></td>
              </tr>
            ))}
          </Table>
          <div className="text-[11px] text-slate-500">Control accounts agree only once opening balances for customers, suppliers and stock have been posted.</div>
        </div>
      )}
    </Card>
  );
}

function CutoverCard({ onError }: { onError: (e: unknown) => void }) {
  const [s, setS] = useState<Awaited<ReturnType<typeof accountingApi.cutoverSummary>> | null>(null);
  useEffect(() => { accountingApi.cutoverSummary().then(setS).catch(onError); }, [onError]);
  return (
    <Card title="Cutover summary (read-only)">
      <p className="text-xs text-slate-400 mb-3">What the ERP holds today. Use it with your previous books to agree the books start date and opening balances — these figures are not opening balances by themselves, and nothing here is posted.</p>
      {!s ? <Spinner /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Activity" value={s.first_activity ? `${fmtDate(s.first_activity)} – ${fmtDate(s.last_activity)}` : "None"} />
            <Stat label="Customer khata outstanding" value={money(s.balances_now.customer_khata_outstanding)} />
            <Stat label="Stock value at cost" value={money(s.balances_now.stock_value_at_cost)} />
            <Stat label="Received, not billed" value={money(s.goods_received_not_billed.unbilled_value)} tone="amber"
              hint={s.goods_received_not_billed.grns_without_finalised_bill.length ? `${s.goods_received_not_billed.grns_without_finalised_bill.length} GRN(s) without a finalised bill` : undefined} />
          </div>
          <Table head={["Transactions", "Count", "From", "To", "Total ₹"]}>
            {Object.values(s.sections).flat().map((r) => (
              <tr key={r.item}>
                <td className={td}>{r.item}</td>
                <td className={tdNum}>{r.count}</td>
                <td className={`${td} text-xs`}>{r.first ? fmtDate(r.first) : ""}</td>
                <td className={`${td} text-xs`}>{r.last ? fmtDate(r.last) : ""}</td>
                <td className={tdNum}>{r.total != null ? money(r.total) : ""}</td>
              </tr>
            ))}
          </Table>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            <div>
              <div className="text-xs text-slate-400 mb-1">Customer balances (khata)</div>
              {s.customer_balances.length === 0 ? <div className="text-slate-500">None</div> : s.customer_balances.map((c) => (
                <div key={c.customer_id} className="flex justify-between"><span>{c.name}</span><span className="font-mono">{money(c.balance)}</span></div>))}
              <div className="text-xs text-slate-400 mt-3 mb-1">Supplier balances</div>
              {s.supplier_balances.length === 0 ? <div className="text-slate-500">None (no finalised bills or payments)</div> : s.supplier_balances.map((c) => (
                <div key={c.supplier_id} className="flex justify-between"><span>{c.name}</span><span className="font-mono">{money(c.balance)}</span></div>))}
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-1">Cash</div>
              {s.cash.open_shifts.map((o) => <div key={o.shift_id}>Open shift #{o.shift_id} since {fmtDateTime(o.opened_at)}: expected {money(o.expected_cash)}</div>)}
              {s.cash.last_closed_shift && <div>Last closed shift #{s.cash.last_closed_shift.shift_id}: counted {money(s.cash.last_closed_shift.counted_cash)}</div>}
              <div className="text-xs text-slate-500">{s.cash.note}</div>
              <div className="text-xs text-slate-400 mt-3 mb-1">Bank</div>
              <div className="text-xs text-slate-500">{s.bank}</div>
              <div className="text-xs text-slate-400 mt-3 mb-1">Test masters</div>
              <div className="text-xs">{Object.entries(s.test_masters).map(([k, v]) => `${v} ${k}`).join(" · ")} named TEST</div>
            </div>
          </div>
          <Notice tone="amber">{s.note}</Notice>
        </div>
      )}
    </Card>
  );
}

// ── Financial statements (Stage 5C) ────────────────────────────────────────────

const REPORTS: [string, string][] = [["profit-loss", "Profit & loss"], ["balance-sheet", "Balance sheet"], ["cash-flow", "Cash flow"],
  ["day-book", "Day book"], ["cash-book", "Cash / bank book"], ["gst", "GST (ledger)"], ["payables-ageing", "Payables ageing"]];

function AmountRows({ rows, label = "name" }: { rows: { code?: string; name?: string; line?: string; amount: string }[]; label?: string }) {
  return (
    <>{rows.map((r, i) => (
      <div key={i} className="flex justify-between text-sm py-0.5"><span className="text-slate-300">{r.code ? <span className="font-mono text-xs text-slate-500 mr-2">{r.code}</span> : null}{(r as Record<string, string>)[label] || r.name || r.line}</span><span className="font-mono">{money(r.amount)}</span></div>
    ))}</>
  );
}

function Total({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return <div className={`flex justify-between border-t border-slate-800 pt-1 mt-1 ${strong ? "font-bold text-white" : "text-slate-200"}`}><span>{label}</span><span className="font-mono">{money(value)}</span></div>;
}

function StatementsTab({ can, onError }: { can: (p: string) => boolean; onError: (e: unknown) => void }) {
  const [report, setReport] = useState("profit-loss");
  const [range, setRange] = useState({ date_from: isoToday().slice(0, 8) + "01", date_to: isoToday(), account_id: "" });
  const [data, setData] = useState<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  const params = { date_from: range.date_from, date_to: range.date_to, account_id: range.account_id ? Number(range.account_id) : undefined };
  const load = useCallback(() => { setData(null); accountingApi.report(report, params).then(setData).catch(onError); }, [report, range]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(load, [load]);
  return (
    <Card title="Financial statements" actions={
      <div className="flex flex-wrap gap-2 items-end">
        {can("accounting.export") && <a className="text-xs text-sky-300 hover:underline" href={accountingApi.reportCsvUrl(report, params)}>CSV</a>}
        {can("accounting.export") && <a className="text-xs text-sky-300 hover:underline" href={accountingApi.caPackUrl(range.date_from, range.date_to)}>CA export pack (ZIP)</a>}
      </div>
    }>
      <div className="flex flex-wrap gap-2 items-end mb-4">
        <Field label="Report">
          <select className={`${inputCls} w-48`} value={report} onChange={(e) => setReport(e.target.value)}>
            {REPORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        {report !== "balance-sheet" && report !== "payables-ageing" && <Field label="From"><input type="date" className={`${inputCls} w-40`} value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} /></Field>}
        <Field label={report === "balance-sheet" || report === "payables-ageing" ? "As at" : "To"}><input type="date" className={`${inputCls} w-40`} value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></Field>
        {report === "cash-book" && <Field label="Account id (blank = cash)"><input className={`${inputCls} w-32`} value={range.account_id} onChange={(e) => setRange({ ...range, account_id: e.target.value.replace(/\D/g, "") })} /></Field>}
      </div>
      {!data ? <Spinner /> : report === "profit-loss" ? (
        <div className="grid md:grid-cols-2 gap-6">
          <div><div className="text-xs text-slate-400 mb-1">Revenue</div><AmountRows rows={data.sections.revenue} /><Total label="Net revenue" value={data.totals.revenue} />
            <div className="text-xs text-slate-400 mt-3 mb-1">Cost of sales</div><AmountRows rows={data.sections.cost_of_sales} /><Total label="Gross profit" value={data.gross_profit} strong /></div>
          <div><div className="text-xs text-slate-400 mb-1">Other income</div><AmountRows rows={data.sections.other_income} />
            <div className="text-xs text-slate-400 mt-3 mb-1">Expenses</div><AmountRows rows={data.sections.expenses} /><Total label="Total expenses" value={data.totals.expenses} />
            <Total label={Number(data.net_profit) >= 0 ? "Net profit" : "Net loss"} value={data.net_profit} strong /></div>
        </div>
      ) : report === "balance-sheet" ? (
        <div className="grid md:grid-cols-2 gap-6">
          <div><div className="text-xs text-slate-400 mb-1">Assets</div><AmountRows rows={data.sides.assets} /><Total label="Total assets" value={data.totals.assets} strong /></div>
          <div><div className="text-xs text-slate-400 mb-1">Liabilities</div><AmountRows rows={data.sides.liabilities} /><Total label="Total liabilities" value={data.totals.liabilities} />
            <div className="text-xs text-slate-400 mt-3 mb-1">Equity</div><AmountRows rows={data.sides.equity} /><Total label="Total equity" value={data.totals.equity} />
            <Total label="Liabilities + equity" value={data.liabilities_and_equity} strong />
            <div className={`text-xs mt-2 ${data.is_balanced ? "text-emerald-300" : "text-rose-300"}`}>{data.is_balanced ? "Balanced" : "NOT balanced — run the integrity check"}</div></div>
        </div>
      ) : report === "cash-flow" ? (
        <div className="space-y-3 max-w-xl">
          <Total label="Opening cash, bank & clearing" value={data.opening_cash} />
          {Object.entries(data.sections as Record<string, { line: string; amount: string }[]>).map(([cat, rows]) => (
            <div key={cat}><div className="text-xs text-slate-400 mb-1 capitalize">{cat} activities</div><AmountRows rows={rows} label="line" /><Total label={`Net ${cat}`} value={data.totals[cat]} /></div>
          ))}
          <Total label="Net change" value={data.net_change} /><Total label="Closing cash, bank & clearing" value={data.closing_cash} strong />
          <div className={`text-xs ${data.reconciles ? "text-emerald-300" : "text-rose-300"}`}>{data.reconciles ? "Opening + net change = closing" : "Does not reconcile"}</div>
        </div>
      ) : report === "gst" ? (
        <div className="max-w-md text-sm space-y-1">
          {Object.entries(data.output as Record<string, string>).map(([k, v]) => <div key={k} className="flex justify-between"><span>Output {k}</span><span className="font-mono">{money(v)}</span></div>)}
          <Total label="Output GST" value={data.output_total} />
          {Object.entries(data.input_eligible as Record<string, string>).map(([k, v]) => <div key={k} className="flex justify-between"><span>Input {k} (eligible)</span><span className="font-mono">{money(v)}</span></div>)}
          <div className="flex justify-between text-amber-200"><span>Input GST pending review</span><span className="font-mono">{money(data.input_pending_review)}</span></div>
          <Total label="Net payable (output − eligible input)" value={data.net_payable} strong />
          <div className="text-xs text-slate-500 pt-2">{data.note}</div>
        </div>
      ) : report === "payables-ageing" ? (
        <Table head={["Supplier", "Current", "1–30", "31–60", "61–90", "90+", "Total"]} empty={data.suppliers.length === 0 && "Nothing owed."}>
          {data.suppliers.map((r: Record<string, string>) => (
            <tr key={r.supplier_id}><td className={td}>{r.supplier}</td>{["current", "1-30", "31-60", "61-90", "90+", "total"].map((k) => <td key={k} className={tdNum}>{money(r[k])}</td>)}</tr>
          ))}
        </Table>
      ) : report === "cash-book" ? (
        <>
          <div className="text-sm text-slate-300 mb-2">{data.account?.code} {data.account?.name} · opening {money(data.opening_balance)} · closing {money(data.closing_balance)}</div>
          <Table head={["Date", "Entry", "Narration", "Debit", "Credit", "Balance"]} empty={data.entries.length === 0 && "No movements."}>
            {data.entries.map((e: Record<string, string>, i: number) => (
              <tr key={i}><td className={`${td} text-xs`}>{fmtDate(e.entry_date)}</td><td className={`${td} font-mono text-xs`}>{e.entry_number}</td><td className={`${td} text-xs`}>{e.narration}</td>
                <td className={tdNum}>{Number(e.debit) ? money(e.debit) : ""}</td><td className={tdNum}>{Number(e.credit) ? money(e.credit) : ""}</td><td className={tdNum}>{money(e.balance)}</td></tr>
            ))}
          </Table>
        </>
      ) : (
        <Table head={["Date", "Entry", "Narration", "Account", "Debit", "Credit"]} empty={data.entries.length === 0 && "No entries."}>
          {data.entries.flatMap((e: any) => e.lines.map((l: any, i: number) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
            <tr key={`${e.entry_id}-${i}`}>
              <td className={`${td} text-xs`}>{i === 0 ? fmtDate(e.entry_date) : ""}</td><td className={`${td} font-mono text-xs`}>{i === 0 ? e.entry_number : ""}</td>
              <td className={`${td} text-xs`}>{i === 0 ? e.narration : ""}</td><td className={`${td} text-xs`}>{l.code} {l.account}</td>
              <td className={tdNum}>{Number(l.debit) ? money(l.debit) : ""}</td><td className={tdNum}>{Number(l.credit) ? money(l.credit) : ""}</td>
            </tr>)))}
        </Table>
      )}
    </Card>
  );
}

// ── Bank & settlements (Stage 5D) ──────────────────────────────────────────────

function parseStatementCsv(text: string) {
  // Columns: date (YYYY-MM-DD or DD/MM/YYYY), description, reference, amount (+ in / - out)  — or date, description, reference, debit, credit
  const rows: { statement_date: string; description: string; reference?: string; amount: string }[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const cols = raw.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
    if (cols.length < 4 || !/\d/.test(cols[0])) continue;
    let d = cols[0];
    const m = d.match(/^(\d{2})[/-](\d{2})[/-](\d{4})$/);
    if (m) d = `${m[3]}-${m[2]}-${m[1]}`;
    let amount = cols[3];
    if (cols.length >= 5) amount = String((Number(cols[4] || 0) - Number(cols[3] || 0)).toFixed(2));
    if (!Number(amount)) continue;
    rows.push({ statement_date: d, description: cols[1] || "-", reference: cols[2] || undefined, amount });
  }
  return rows;
}

function BankTab({ accounts, can, onDone, onError }: TabProps & { accounts: Account[] }) {
  const banks = accounts.filter((a) => !a.is_group && a.is_active && (a.system_key === "BANK" || accounts.find((p) => p.id === a.parent_id)?.code === "1120"));
  const [bankId, setBankId] = useState<number | null>(banks[0]?.id ?? null);
  const [st, setSt] = useState<Awaited<ReturnType<typeof accountingApi.settlements>> | null>(null);
  const [recon, setRecon] = useState<Awaited<ReturnType<typeof accountingApi.bankReconciliation>> | null>(null);
  const [form, setForm] = useState({ clearing_key: "UPI_CLEARING", settlement_date: isoToday(), gross_amount: "", fee_amount: "", fee_gst_amount: "", reference: "" });
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState(false);
  const manage = can("accounting.bank_reconcile");
  const load = useCallback(() => {
    accountingApi.settlements().then(setSt).catch(onError);
    if (bankId) accountingApi.bankReconciliation(bankId).then(setRecon).catch(onError);
  }, [bankId, onError]);
  useEffect(load, [load]);
  const run = async (fn: () => Promise<unknown>, msg: string) => { setBusy(true); try { await fn(); onDone(msg); load(); } catch (e) { onError(e); } finally { setBusy(false); } };
  const net = (Number(form.gross_amount || 0) - Number(form.fee_amount || 0) - Number(form.fee_gst_amount || 0)).toFixed(2);
  return (
    <div className="space-y-4">
      <Card title="Payment settlements (UPI / card → bank)">
        {!st ? <Spinner /> : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">{st.clearing.map((c) => <Stat key={c.clearing_key} label={`Waiting in ${c.account}`} value={money(c.balance)} />)}</div>
            {manage && bankId && (
              <div className="grid grid-cols-2 md:grid-cols-7 gap-2 items-end mb-3">
                <Field label="From"><select className={inputCls} value={form.clearing_key} onChange={(e) => setForm({ ...form, clearing_key: e.target.value })}>
                  <option value="UPI_CLEARING">UPI</option><option value="CARD_CLEARING">Card</option><option value="OTHER_CLEARING">Other</option></select></Field>
                <Field label="Date"><input type="date" max={isoToday()} className={inputCls} value={form.settlement_date} onChange={(e) => setForm({ ...form, settlement_date: e.target.value })} /></Field>
                <Field label="Gross ₹"><input className={`${inputCls} font-mono`} value={form.gross_amount} onChange={(e) => setForm({ ...form, gross_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
                <Field label="Fee ₹"><input className={`${inputCls} font-mono`} value={form.fee_amount} onChange={(e) => setForm({ ...form, fee_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
                <Field label="GST on fee ₹"><input className={`${inputCls} font-mono`} value={form.fee_gst_amount} onChange={(e) => setForm({ ...form, fee_gst_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
                <Field label="Reference"><input className={inputCls} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
                <Btn variant="primary" busy={busy} disabled={!(Number(form.gross_amount) > 0) || Number(net) < 0}
                  onClick={() => run(() => accountingApi.createSettlement({ ...form, idempotency_key: newKey(), bank_account_id: bankId,
                    fee_amount: form.fee_amount || "0", fee_gst_amount: form.fee_gst_amount || "0" }), `Settlement recorded: ${money(net)} to bank`)}>Record ({money(net)} net)</Btn>
              </div>
            )}
            <Table head={["Number", "Date", "From", "Gross", "Fees", "Net to bank", "Status", ""]} empty={st.items.length === 0 && "No settlements yet."}>
              {st.items.map((s) => (
                <tr key={s.id} className={s.status === "REVERSED" ? "opacity-60" : ""}>
                  <td className={`${td} font-mono text-xs`}>{s.settlement_number}</td><td className={`${td} text-xs`}>{fmtDate(s.settlement_date)}</td>
                  <td className={`${td} text-xs`}>{s.clearing_key.replace("_CLEARING", "")}</td><td className={tdNum}>{money(s.gross_amount)}</td>
                  <td className={tdNum}>{money(Number(s.fee_amount) + Number(s.fee_gst_amount))}</td><td className={tdNum}>{money(s.net_amount)}</td>
                  <td className={td}><Badge value={s.status === "POSTED" ? "SENT" : "VOIDED"} label={s.status.toLowerCase()} /></td>
                  <td className={td}>{s.status === "POSTED" && manage && <Btn variant="ghost" onClick={() => { const r = prompt("Reason for reversing this settlement (at least 10 characters)"); if (r) run(() => accountingApi.reverseSettlement(s.id, r), "Settlement reversed"); }}>Reverse</Btn>}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </Card>
      <Card title="Bank reconciliation" actions={banks.length > 1 && (
        <select className={`${inputCls} w-56`} value={bankId ?? ""} onChange={(e) => setBankId(Number(e.target.value))}>{banks.map((b) => <option key={b.id} value={b.id}>{b.code} {b.name}</option>)}</select>)}>
        {!bankId ? <ErrorBox message="No bank account in the chart." /> : !recon ? <Spinner /> : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Book balance" value={money(recon.book_balance)} />
              <Stat label="Imported statement movements" value={money(recon.statement_movements)} />
              <Stat label="Matched" value={String(recon.matched_lines)} tone="emerald" />
              <Stat label="Unmatched (book / statement)" value={`${recon.unmatched_book.length} / ${recon.unmatched_statement.length}`} tone={recon.unmatched_book.length + recon.unmatched_statement.length ? "amber" : "slate"} />
            </div>
            {manage && (
              <div>
                <Field label="Paste the bank statement CSV (date, description, reference, amount  — or date, description, reference, debit, credit)">
                  <textarea className={`${inputCls} font-mono text-xs h-24`} value={csv} onChange={(e) => setCsv(e.target.value)} />
                </Field>
                <div className="flex gap-2 mt-2">
                  <Btn variant="primary" busy={busy} disabled={parseStatementCsv(csv).length === 0}
                    onClick={() => run(async () => { const r = await accountingApi.importStatement(bankId, parseStatementCsv(csv)); setCsv(""); onDone(`Imported ${r.added} line(s), ${r.skipped_duplicates} duplicate(s) skipped, ${r.auto_matched} matched`); }, "Statement imported")}>
                    Import {parseStatementCsv(csv).length || ""} line(s)</Btn>
                  <Btn busy={busy} onClick={() => run(() => accountingApi.autoMatch(bankId), "Auto-match done")}>Auto-match</Btn>
                </div>
              </div>
            )}
            <div className="grid md:grid-cols-2 gap-4">
              <div><div className="text-xs text-slate-400 mb-1">Statement lines not matched</div>
                <Table head={["Date", "Description", "Amount", ""]} empty={recon.unmatched_statement.length === 0 && "None."}>
                  {recon.unmatched_statement.map((l) => (
                    <tr key={l.id}><td className={`${td} text-xs`}>{fmtDate(l.statement_date)}</td><td className={`${td} text-xs`}>{l.description}{l.reference ? ` (${l.reference})` : ""}</td><td className={tdNum}>{money(l.amount)}</td>
                      <td className={td}>{manage && <select className={`${inputCls} w-40 text-xs`} defaultValue="" onChange={(e) => e.target.value && run(() => accountingApi.matchLine(l.id, Number(e.target.value)), "Matched")}>
                        <option value="">Match to…</option>{recon.unmatched_book.filter((b) => Number(b.amount) === Number(l.amount)).map((b) => <option key={b.journal_line_id} value={b.journal_line_id}>{b.entry_number} {fmtDate(b.entry_date)}</option>)}
                      </select>}</td></tr>
                  ))}
                </Table></div>
              <div><div className="text-xs text-slate-400 mb-1">Book entries not on the statement</div>
                <Table head={["Date", "Entry", "Narration", "Amount"]} empty={recon.unmatched_book.length === 0 && "None."}>
                  {recon.unmatched_book.map((b) => <tr key={b.journal_line_id}><td className={`${td} text-xs`}>{fmtDate(b.entry_date)}</td><td className={`${td} font-mono text-xs`}>{b.entry_number}</td><td className={`${td} text-xs`}>{b.narration}</td><td className={tdNum}>{money(b.amount)}</td></tr>)}
                </Table></div>
            </div>
            <div className="text-[11px] text-slate-500">{recon.note}</div>
          </div>
        )}
      </Card>
    </div>
  );
}
