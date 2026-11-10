"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { BellRing, Pencil, Printer, Scale, UserRound, Wallet } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, Spinner, Stat, Table, errorText, inputCls, openTabNow, sendTabTo,
  td, tdNum, useStaff,
} from "@/components/sales-ui";
import { CustomerStatement, fmtDate, fmtDateTime, money, newKey, num, salesApi } from "@/lib/sales-api";

export default function CustomerLedgerPage() {
  const { id } = useParams<{ id: string }>();
  const { user, can } = useStaff();
  const [st, setSt] = useState<CustomerStatement | null>(null);
  const [range, setRange] = useState({ from: "", to: "" });
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [modal, setModal] = useState<"" | "pay" | "edit" | "adjust">("");
  const [reconciled, setReconciled] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await salesApi.statement(id, range.from || undefined, range.to || undefined));
      setReconciled((await salesApi.reconcile(id)).is_reconciled);
    } catch (e) {
      setError(errorText(e));
    }
  }, [id, range]);
  useEffect(() => { if (user) load(); }, [user, load]);

  if (!st) {
    return <SalesShell user={user} title="Customer" icon={<UserRound className="w-6 h-6" />}>{error ? <ErrorBox message={error} /> : <Spinner />}</SalesShell>;
  }
  const c = st.customer;
  const owes = num(c.outstanding_balance);

  return (
    <SalesShell
      user={user}
      title={c.name}
      subtitle={`${c.mobile ? `+${c.mobile}` : "No mobile"} · terms ${c.payment_terms_days} days`}
      icon={<UserRound className="w-6 h-6" />}
      actions={
        <>
          {can("payments.receive") && <Btn variant="primary" onClick={() => setModal("pay")}><Wallet className="w-4 h-4" /> Receive payment</Btn>}
          {can("customers.manage") && <Btn onClick={() => setModal("edit")}><Pencil className="w-4 h-4" /> Edit</Btn>}
          <Btn variant="ghost" onClick={() => window.print()}><Printer className="w-4 h-4" /> Print statement</Btn>
        </>
      }
    >
      <ErrorBox message={error} onClose={() => setError(null)} />
      {info && <Notice tone="emerald">{info}</Notice>}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label={owes < 0 ? "Advance credit" : "Outstanding"} value={money(Math.abs(owes))} tone={owes > 0 ? "amber" : owes < 0 ? "sky" : "emerald"} />
        <Stat label="Overdue" value={money(c.overdue_amount)} tone={num(c.overdue_amount) > 0 ? "rose" : "slate"} />
        <Stat label="Credit limit" value={money(c.credit_limit)} hint={`${money(c.available_credit)} available`} />
        <Stat label="Unapplied credit" value={money(st.unapplied_credit)} tone="sky" />
        <Stat label="Ledger check" value={reconciled === null ? "…" : reconciled ? "Balanced" : "MISMATCH"} tone={reconciled === false ? "rose" : "emerald"} hint="Ledger = open bills − credit" />
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {c.whatsapp_consent ? <Badge value="PAID" label="WhatsApp consent" /> : <Badge value="CANCELLED" label="No WhatsApp consent" />}
        {c.reminders_opt_out && <Badge value="CANCELLED" label="Reminders off" />}
        {!c.is_active && <Badge value="VOIDED" label="Inactive" />}
        {num(st.unapplied_credit) > 0 && st.open_invoices.length > 0 && can("payments.receive") && (
          <Btn variant="ghost" onClick={async () => {
            try {
              const r = await salesApi.applyCredit(c.id);
              setInfo(`Applied credit: ${r.applied.map((a) => `${a.invoice_number} ${money(a.amount)}`).join(", ")}`);
              load();
            } catch (e) { setError(errorText(e)); }
          }}>Use {money(st.unapplied_credit)} credit on open bills</Btn>
        )}
      </div>

      <Card title={`Open invoices (${st.open_invoices.length})`} actions={can("customers.adjust") && st.open_invoices.length > 0 && <Btn variant="ghost" onClick={() => setModal("adjust")}><Scale className="w-4 h-4" /> Adjust / write off</Btn>}>
        <Table head={["Invoice", "Date", "Due", "Total", "Paid", "Balance", ""]} empty={st.open_invoices.length === 0 && "Nothing outstanding."}>
          {st.open_invoices.map((i) => (
            <tr key={i.id}>
              <td className={td}><Link className="font-mono text-emerald-300 hover:underline" href={`/sales/${i.id}`}>{i.invoice_number}</Link></td>
              <td className={`${td} text-xs text-slate-400`}>{fmtDate(i.invoice_date)}</td>
              <td className={td}>{fmtDate(i.due_date)} {i.overdue_days > 0 && <Badge value="OVERDUE" label={`${i.overdue_days}d`} />}</td>
              <td className={tdNum}>{money(i.grand_total)}</td>
              <td className={tdNum}>{money(i.amount_paid)}</td>
              <td className={`${tdNum} text-amber-300`}>{money(i.balance_due)}</td>
              <td className={td}>
                {can("messaging.send") && c.whatsapp_consent && !c.reminders_opt_out && (
                  <button className="text-xs text-emerald-300 hover:underline" onClick={async () => {
                    const tab = openTabNow();
                    try {
                      const m = await salesApi.sendReminder(i.id);
                      sendTabTo(tab, m.share_url);
                      setInfo(`Reminder for ${i.invoice_number}: ${m.status_label}`);
                    } catch (e) { sendTabTo(tab, null); setError(errorText(e)); }
                  }}><BellRing className="w-3 h-3 inline" /> Remind</button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      <Card title="Statement" actions={
        <div className="flex gap-2 items-center text-xs">
          <input type="date" className={`${inputCls} w-36`} value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} aria-label="From" />
          <span className="text-slate-500">to</span>
          <input type="date" className={`${inputCls} w-36`} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} aria-label="To" />
        </div>
      }>
        <div className="text-xs text-slate-400 mb-2">Opening balance {money(st.opening_balance)} · debits {money(st.total_debits)} · credits {money(st.total_credits)} · closing {money(st.closing_balance)}</div>
        <Table head={["Date", "Type", "Reference", "Details", "Debit ₹", "Credit ₹", "Balance ₹"]} empty={st.entries.length === 0 && "No entries in this period."}>
          {st.entries.map((e) => (
            <tr key={e.id}>
              <td className={`${td} text-xs text-slate-400 whitespace-nowrap`}>{fmtDateTime(e.created_at)}</td>
              <td className={td}><span className="text-[10px] font-bold text-slate-300">{e.entry_type.replace(/_/g, " ")}</span></td>
              <td className={`${td} font-mono text-xs`}>{e.invoice_id ? <Link className="text-emerald-300 hover:underline" href={`/sales/${e.invoice_id}`}>{e.reference}</Link> : e.reference}</td>
              <td className={`${td} text-xs`}>{e.description}{e.reason && <div className="text-slate-500">Reason: {e.reason}</div>}</td>
              <td className={tdNum}>{num(e.debit) ? money(e.debit) : ""}</td>
              <td className={tdNum}>{num(e.credit) ? money(e.credit) : ""}</td>
              <td className={tdNum}>{money(e.balance)}</td>
            </tr>
          ))}
        </Table>
      </Card>

      <CollectModal st={st} open={modal === "pay"} onClose={() => setModal("")} onDone={(m) => { setModal(""); setInfo(m); load(); }} />
      <EditModal st={st} open={modal === "edit"} canCredit={can("sales.credit_override")} onClose={() => setModal("")} onDone={() => { setModal(""); load(); }} />
      <AdjustModal st={st} open={modal === "adjust"} onClose={() => setModal("")} onDone={(m) => { setModal(""); setInfo(m); load(); }} />
    </SalesShell>
  );
}

function CollectModal({ st, open, onClose, onDone }: { st: CustomerStatement; open: boolean; onClose: () => void; onDone: (m: string) => void }) {
  const [form, setForm] = useState({ method: "CASH", amount: "", tendered: "", reference: "", key: newKey() });
  const [alloc, setAlloc] = useState<Record<number, string>>({});
  const [manual, setManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setForm({ method: "CASH", amount: num(st.customer.outstanding_balance) > 0 ? st.customer.outstanding_balance : "", tendered: "", reference: "", key: newKey() });
      setAlloc({});
      setManual(false);
      setErr(null);
    }
  }, [open, st.customer.outstanding_balance]);
  const allocTotal = Object.values(alloc).reduce((s, v) => s + num(v), 0);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await salesApi.receivePayment(st.customer.id, {
        idempotency_key: form.key, method: form.method, amount: form.amount,
        tendered: form.method === "CASH" && form.tendered ? form.tendered : undefined,
        reference: form.reference || undefined,
        allocations: manual ? Object.entries(alloc).filter(([, v]) => num(v) > 0).map(([k, v]) => ({ invoice_id: Number(k), amount: v })) : null,
      });
      onDone(`Receipt ${r.receipt_number}: ${money(r.amount)} received${num(r.unapplied_amount) ? `, ${money(r.unapplied_amount)} kept as advance` : ""}${num(r.change_returned) ? ` · change ${money(r.change_returned)}` : ""}.`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title={`Receive payment from ${st.customer.name}`} onClose={onClose} wide>
      <ErrorBox message={err} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Field label="Method">
          <select className={inputCls} value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Amount ₹"><input className={`${inputCls} font-mono`} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
        {form.method === "CASH"
          ? <Field label="Cash tendered"><input className={`${inputCls} font-mono`} value={form.tendered} onChange={(e) => setForm({ ...form, tendered: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
          : <Field label="Reference / UTR"><input className={inputCls} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>}
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} /> Choose which invoices to settle (otherwise oldest due first)
      </label>
      {manual && (
        <div className="space-y-1">
          {st.open_invoices.map((i) => (
            <div key={i.id} className="flex items-center gap-2 text-sm">
              <span className="font-mono flex-1">{i.invoice_number} · due {money(i.balance_due)}</span>
              <input className={`${inputCls} w-32 font-mono`} value={alloc[i.id] || ""} onChange={(e) => setAlloc({ ...alloc, [i.id]: e.target.value.replace(/[^0-9.]/g, "") })} placeholder="0.00" />
            </div>
          ))}
          <div className="text-xs text-slate-400">Allocated {money(allocTotal)} of {money(form.amount || 0)}; the rest becomes advance credit.</div>
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="primary" busy={busy} disabled={!(num(form.amount) > 0) || (manual && allocTotal > num(form.amount))} onClick={save}>Record payment</Btn>
      </div>
    </Modal>
  );
}

function EditModal({ st, open, canCredit, onClose, onDone }: { st: CustomerStatement; open: boolean; canCredit: boolean; onClose: () => void; onDone: () => void }) {
  const c = st.customer;
  const [f, setF] = useState({ ...c, mobile: c.mobile || "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setF({ ...c, mobile: c.mobile ? `+${c.mobile}` : "" }); setErr(null); } }, [open, c]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await salesApi.updateCustomer(c.id, {
        name: f.name, mobile: f.mobile || null, email: f.email || null, address: f.address || null, gstin: f.gstin || null,
        notes: f.notes || null, whatsapp_consent: f.whatsapp_consent, reminders_opt_out: f.reminders_opt_out, is_active: f.is_active,
        ...(canCredit ? { credit_limit: String(f.credit_limit), payment_terms_days: Number(f.payment_terms_days) } : {}),
      });
      onDone();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title="Edit customer" onClose={onClose}>
      <ErrorBox message={err} />
      <Field label="Name"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Mobile"><input className={inputCls} value={f.mobile || ""} onChange={(e) => setF({ ...f, mobile: e.target.value })} /></Field>
        <Field label="Email"><input className={inputCls} value={f.email || ""} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Credit limit ₹"><input className={inputCls} disabled={!canCredit} value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} /></Field>
        <Field label="Terms (days)"><input className={inputCls} disabled={!canCredit} value={f.payment_terms_days} onChange={(e) => setF({ ...f, payment_terms_days: Number(e.target.value) || 0 })} /></Field>
      </div>
      <Field label="Address"><input className={inputCls} value={f.address || ""} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
      <Field label="Notes"><input className={inputCls} value={f.notes || ""} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <div className="space-y-1 text-sm text-slate-300">
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.whatsapp_consent} onChange={(e) => setF({ ...f, whatsapp_consent: e.target.checked })} /> Agrees to WhatsApp bills / reminders</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.reminders_opt_out} onChange={(e) => setF({ ...f, reminders_opt_out: e.target.checked })} /> No payment reminders</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> Active</label>
      </div>
      <div className="flex justify-end gap-2"><Btn variant="ghost" onClick={onClose}>Cancel</Btn><Btn variant="primary" busy={busy} onClick={save}>Save</Btn></div>
    </Modal>
  );
}

function AdjustModal({ st, open, onClose, onDone }: { st: CustomerStatement; open: boolean; onClose: () => void; onDone: (m: string) => void }) {
  const [f, setF] = useState({ invoice_id: "", adjustment_type: "ADJUSTMENT_CREDIT", amount: "", reason: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await salesApi.adjustment(st.customer.id, { ...f, invoice_id: Number(f.invoice_id) });
      onDone(`${f.adjustment_type === "WRITE_OFF" ? "Write-off" : "Adjustment"} of ${money(f.amount)} posted.`);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title="Ledger adjustment" onClose={onClose}>
      <ErrorBox message={err} />
      <Notice tone="amber">Reduces what is due on one invoice. It is recorded in the ledger with your name and reason; nothing is deleted.</Notice>
      <Field label="Invoice">
        <select className={inputCls} value={f.invoice_id} onChange={(e) => setF({ ...f, invoice_id: e.target.value })}>
          <option value="">Choose…</option>
          {st.open_invoices.map((i) => <option key={i.id} value={i.id}>{i.invoice_number} · due {money(i.balance_due)}</option>)}
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Type">
          <select className={inputCls} value={f.adjustment_type} onChange={(e) => setF({ ...f, adjustment_type: e.target.value })}>
            <option value="ADJUSTMENT_CREDIT">Goodwill / correction</option>
            <option value="WRITE_OFF">Bad debt write-off</option>
          </select>
        </Field>
        <Field label="Amount ₹"><input className={inputCls} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
      </div>
      <Field label="Reason *"><input className={inputCls} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="flex justify-end gap-2"><Btn variant="ghost" onClick={onClose}>Cancel</Btn><Btn variant="primary" busy={busy} disabled={!f.invoice_id || !(num(f.amount) > 0) || f.reason.trim().length < 5} onClick={save}>Post</Btn></div>
    </Modal>
  );
}
