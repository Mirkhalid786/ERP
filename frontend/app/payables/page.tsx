"use client";

/**
 * Payables (Stage 5B preparation): finalise supplier bills, record dated supplier
 * payments with allocations, reverse mistakes, and see what is owed to each supplier.
 * Postings to the general ledger happen in the backend (app.services.gl_posting).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Wallet } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, Stat, Table, errorText, inputCls, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { api, SupplierInvoiceResponse, SupplierItem } from "@/lib/api";
import {
  CREDIT_NOTE_REASONS, CreditNote, FinalizeCheck, OpenInvoice, PAYMENT_METHODS, REFERENCE_REQUIRED, SupplierBalance, SupplierPayment, payablesApi,
} from "@/lib/payables-api";
import { fmtDate, fmtDateTime, money, newKey, num } from "@/lib/sales-api";
import { LedgerBadge, useLedgerStatus } from "@/components/ledger-status";

type Tab = "bills" | "payments" | "credits" | "balances";
const TABS: [Tab, string][] = [["bills", "Supplier bills"], ["payments", "Payments"], ["credits", "Credit notes"], ["balances", "Balances"]];
const toPaise = (v: string | number | null | undefined) => Math.round(Number(v || 0) * 100);
const fromPaise = (p: number) => (p / 100).toFixed(2);

interface PayForm {
  key: string;
  supplier_id: number | "";
  payment_date: string;
  method: string;
  cash_source: string;
  amount: string;
  reference: string;
  bank_account: string;
  notes: string;
  alloc: Record<number, string>;
  confirm_duplicate: boolean;
}

const blankPay = (supplierId: number | "" = ""): PayForm => ({
  key: newKey(), supplier_id: supplierId, payment_date: isoToday(), method: "BANK_TRANSFER", cash_source: "",
  amount: "", reference: "", bank_account: "", notes: "", alloc: {}, confirm_duplicate: false,
});

export default function PayablesPage() {
  const { user, can } = useStaff();
  const [tab, setTab] = useState<Tab>("bills");
  const [bills, setBills] = useState<SupplierInvoiceResponse[]>([]);
  const [billFilter, setBillFilter] = useState("");
  const [payments, setPayments] = useState<SupplierPayment[]>([]);
  const [notes, setNotes] = useState<CreditNote[]>([]);
  const [cn, setCn] = useState<{ key: string; supplier_id: number | ""; credit_note_number: string; note_date: string; invoice_id: string;
    reason_code: string; taxable_amount: string; cgst_amount: string; sgst_amount: string; igst_amount: string; notes: string } | null>(null);
  const [balances, setBalances] = useState<SupplierBalance[]>([]);
  const [recon, setRecon] = useState<Awaited<ReturnType<typeof payablesApi.reconciliation>> | null>(null);
  const [suppliers, setSuppliers] = useState<SupplierItem[]>([]);
  const [check, setCheck] = useState<{ bill: SupplierInvoiceResponse; result: FinalizeCheck; ack: boolean } | null>(null);
  const [pay, setPay] = useState<PayForm | null>(null);
  const [open, setOpen] = useState<OpenInvoice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canView = can("supplier_payments.view") || can("purchases.invoice_finalize");
  const ledger = useLedgerStatus([...bills.filter((b) => b.status !== "DRAFT").map((b) => `SUPPLIER_BILL:${b.id}`),
    ...payments.map((p) => `SUPPLIER_PAYMENT:${p.id}`)], can("accounting.view"));

  const load = useCallback(async () => {
    try {
      setBills(await api.getSupplierInvoices({ status: billFilter || undefined, limit: 200 }));
      if (can("supplier_payments.view")) {
        setPayments((await payablesApi.payments({ limit: 100 })).items);
        setNotes(await payablesApi.creditNotes());
        setBalances(await payablesApi.balances());
        setRecon(await payablesApi.reconciliation());
      }
    } catch (e) {
      setError(errorText(e));
    }
  }, [billFilter, can]);

  useEffect(() => { if (user && canView) load(); }, [user, canView, load]);
  useEffect(() => { if (user) api.getSuppliers({ limit: 200 }).then(setSuppliers).catch(() => undefined); }, [user]);

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setInfo(msg);
      await load();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const openCheck = async (bill: SupplierInvoiceResponse) => {
    try {
      setCheck({ bill, result: await payablesApi.finalizeCheck(bill.id), ack: false });
    } catch (e) {
      setError(errorText(e));
    }
  };

  const startPay = useCallback(async (supplierId: number | "", billId?: number) => {
    const form = blankPay(supplierId);
    setOpen([]);
    if (supplierId) {
      const rows = await payablesApi.openInvoices(supplierId);
      setOpen(rows);
      if (billId) {
        const row = rows.find((r) => r.id === billId);
        if (row) {
          form.alloc = { [row.id]: row.outstanding };
          form.amount = row.outstanding;
        }
      }
    }
    setPay(form);
  }, []);

  // Deep link from the purchasing page: /payables?bill=<id>
  useEffect(() => {
    if (!user || bills.length === 0) return;
    const id = Number(new URLSearchParams(window.location.search).get("bill"));
    if (!id) return;
    window.history.replaceState(null, "", "/payables");
    const bill = bills.find((b) => b.id === id);
    if (!bill) return;
    if (bill.status === "DRAFT" && can("purchases.invoice_finalize")) openCheck(bill);
    else if (bill.status === "POSTED" && can("supplier_payments.create")) startPay(bill.supplier_id, bill.id);
  }, [user, bills, can, startPay]);

  const allocTotal = useMemo(() => Object.values(pay?.alloc || {}).reduce((s, v) => s + toPaise(v), 0), [pay]);
  const advance = pay ? toPaise(pay.amount) - allocTotal : 0;
  const needsRef = !!pay && REFERENCE_REQUIRED.includes(pay.method);
  const payInvalid = !pay || !pay.supplier_id || !(num(pay.amount) > 0) || advance < 0 || (needsRef && !pay.reference.trim()) ||
    (pay.method === "CASH" && !pay.cash_source) ||
    open.some((o) => toPaise(pay.alloc[o.id]) > toPaise(o.outstanding));

  const fillOldestFirst = () => {
    if (!pay) return;
    let left = toPaise(pay.amount);
    const alloc: Record<number, string> = {};
    for (const o of open) {
      if (left <= 0) break;
      const take = Math.min(left, toPaise(o.outstanding));
      if (take > 0) alloc[o.id] = fromPaise(take);
      left -= take;
    }
    setPay({ ...pay, alloc });
  };

  const submitPay = async () => {
    if (!pay || !pay.supplier_id) return;
    const allocations = Object.entries(pay.alloc).filter(([, v]) => toPaise(v) > 0).map(([id, v]) => ({ invoice_id: Number(id), amount: v }));
    setBusy(true);
    setError(null);
    try {
      const res = await payablesApi.recordPayment({
        idempotency_key: pay.key, supplier_id: pay.supplier_id, payment_date: pay.payment_date, method: pay.method,
        cash_source: pay.method === "CASH" ? pay.cash_source : null, amount: pay.amount, reference: pay.reference || null,
        bank_account: pay.bank_account || null, notes: pay.notes || null, allocations: allocations.length ? allocations : null,
        confirm_duplicate: pay.confirm_duplicate,
      });
      setInfo(res.replayed ? `Payment ${res.payment_number} was already recorded.` : `Payment ${res.payment_number} recorded.`);
      setPay(null);
      await load();
    } catch (e) {
      const text = errorText(e);
      setError(text);
      // Same data again after a duplicate warning: keep the key so a retry cannot double-post
      if (/confirm/i.test(text)) setPay({ ...pay, confirm_duplicate: false });
    } finally {
      setBusy(false);
    }
  };

  const supplierName = (id: number) => suppliers.find((s) => s.id === id)?.name || `#${id}`;
  const owed = bills.filter((b) => b.status === "POSTED").reduce((s, b) => s + Math.max(0, toPaise(b.outstanding_amount)), 0);
  const advances = balances.reduce((s, b) => s + toPaise(b.unallocated_payments), 0);

  return (
    <SalesShell user={user} title="Payables" subtitle="Finalise supplier bills, record supplier payments, see what you owe"
      icon={<Wallet className="w-6 h-6" />}>
      <ErrorBox message={error} onClose={() => setError(null)} />
      {info && <Notice tone="emerald">{info}</Notice>}
      {user && !canView ? <ErrorBox message="You do not have permission to view payables." /> : (
        <>
          <div className="flex gap-3 text-sm">
            <span className="text-white font-semibold">Supplier bills &amp; payments</span>
            {(can("expenses.view") || can("expenses.create")) && <a className="text-sky-300 hover:underline" href="/expenses">Expenses →</a>}
          </div>
          <Notice>Finalised bills, supplier returns and payments are posted to the accounts automatically once accounting is set up (from the books start date).</Notice>
          {can("supplier_payments.view") && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Owed on finalised bills" value={money(owed / 100)} tone="amber" hint={billFilter ? "For the bills listed" : undefined} />
              <Stat label="Advances / unallocated" value={money(advances / 100)} tone="sky" />
              <Stat label="Draft bills" value={String(bills.filter((b) => b.status === "DRAFT").length)} hint="Not yet payable" />
              <Stat label="Reconciliation" value={recon ? (recon.ok ? "OK" : `${recon.problems.length} problem(s)`) : "…"}
                tone={recon && !recon.ok ? "rose" : "emerald"} />
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {TABS.filter(([t]) => t === "bills" || can("supplier_payments.view")).map(([t, label]) => (
              <Btn key={t} variant={tab === t ? "primary" : "ghost"} onClick={() => setTab(t)}>{label}</Btn>
            ))}
          </div>

          {tab === "bills" && (
            <Card title="Supplier bills" actions={
              <select className={`${inputCls} w-44`} value={billFilter} onChange={(e) => setBillFilter(e.target.value)}>
                <option value="">All statuses</option>
                <option value="DRAFT">Draft</option>
                <option value="POSTED">Finalised</option>
                <option value="CANCELLED">Cancelled</option>
              </select>
            }>
              <Table head={["Bill", "Supplier", "Date / due", "Status", "Total ₹", "Paid ₹", "Owed ₹", ""]} empty={bills.length === 0 && "No supplier bills."}>
                {bills.map((b) => (
                  <tr key={b.id}>
                    <td className={`${td} font-mono`}>{b.invoice_number}{b.grn_number && <div className="text-[11px] text-slate-500">GRN {b.grn_number}</div>}</td>
                    <td className={td}>{b.supplier_name}</td>
                    <td className={`${td} text-xs`}>{fmtDate(b.invoice_date)}<div className="text-slate-500">due {fmtDate(b.due_date)}</div></td>
                    <td className={td}>
                      <Badge value={b.status === "POSTED" ? "SENT" : b.status === "CANCELLED" ? "VOIDED" : "PENDING"}
                        label={b.status === "POSTED" ? "Finalised" : b.status === "CANCELLED" ? "Cancelled" : "Draft"} />
                      {b.status === "POSTED" && <div className="text-[11px] text-slate-500 mt-1">{b.payment_status.replace("_", " ").toLowerCase()}</div>}
                      {b.cancel_reason && <div className="text-[11px] text-slate-500 mt-1">{b.cancel_reason}</div>}
                      <div className="mt-1"><LedgerBadge status={ledger[`SUPPLIER_BILL:${b.id}`]} /></div>
                    </td>
                    <td className={tdNum}>{money(b.calculated_grand_total)}</td>
                    <td className={tdNum}>{money(b.paid_amount)}</td>
                    <td className={`${tdNum} font-bold`}>{b.status === "POSTED" ? money(b.outstanding_amount) : "—"}</td>
                    <td className={td}>
                      <div className="flex flex-wrap gap-1 justify-end">
                        {b.status === "DRAFT" && can("purchases.invoice_finalize") && <Btn variant="ghost" onClick={() => openCheck(b)}>Check &amp; finalise</Btn>}
                        {b.status === "POSTED" && num(b.outstanding_amount) > 0 && can("supplier_payments.create") && (
                          <Btn variant="ghost" onClick={() => startPay(b.supplier_id, b.id)}>Pay</Btn>
                        )}
                        {b.status !== "CANCELLED" && (b.status === "DRAFT" ? can("purchases.create") : can("purchases.invoice_cancel")) && (
                          <Btn variant="ghost" onClick={() => {
                            const reason = prompt(`Why is bill ${b.invoice_number} being cancelled? (at least 10 characters; the bill is kept for audit)`);
                            if (reason) run(() => payablesApi.cancelInvoice(b.id, reason), `Bill ${b.invoice_number} cancelled.`);
                          }}>Cancel</Btn>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          {tab === "payments" && (
            <Card title="Supplier payments" actions={can("supplier_payments.create") && <Btn variant="primary" onClick={() => startPay("")}>Record payment</Btn>}>
              <Table head={["Number", "Supplier", "Date", "Method", "Amount ₹", "Allocated to", "Status", ""]} empty={payments.length === 0 && "No supplier payments yet."}>
                {payments.map((p) => (
                  <tr key={p.id} className={p.status === "REVERSED" ? "opacity-60" : ""}>
                    <td className={`${td} font-mono`}>{p.payment_number}{p.is_legacy && <div className="text-[11px] text-amber-400">migrated — verify</div>}</td>
                    <td className={td}>{p.supplier_name}</td>
                    <td className={`${td} text-xs`}>{fmtDate(p.payment_date)}<div className="text-slate-500">by {p.created_by_name}</div></td>
                    <td className={`${td} text-xs`}>
                      {p.method.replace("_", " ")}{p.cash_source && ` · ${p.cash_source === "DRAWER" ? "from drawer" : "office cash"}`}
                      {p.reference && <div className="text-slate-500 font-mono">{p.reference}</div>}
                    </td>
                    <td className={tdNum}>{money(p.amount)}</td>
                    <td className={`${td} text-xs`}>
                      {p.allocations.filter((a) => a.status === "ACTIVE").map((a) => <div key={a.id}>{a.invoice_number}: {money(a.amount)}</div>)}
                      {num(p.unallocated) > 0 && <div className="text-sky-300">Advance {money(p.unallocated)}</div>}
                    </td>
                    <td className={td}>
                      <Badge value={p.status === "POSTED" ? "SENT" : "VOIDED"} label={p.status === "POSTED" ? "Recorded" : "Reversed"} />
                      <div className="mt-1"><LedgerBadge status={ledger[`SUPPLIER_PAYMENT:${p.id}`]} /></div>
                      {p.reversal_reason && <div className="text-[11px] text-slate-500 mt-1">{p.reversal_reason} — {p.reversed_by_name}, {fmtDateTime(p.reversed_at)}</div>}
                    </td>
                    <td className={td}>
                      <div className="flex flex-wrap gap-1 justify-end">
                        {p.status === "POSTED" && num(p.unallocated) > 0 && can("supplier_payments.create") && (
                          <Btn variant="ghost" onClick={() => run(() => payablesApi.allocate(p.id, null), "Advance allocated to the oldest open bills.")}>Allocate advance</Btn>
                        )}
                        {p.status === "POSTED" && can("supplier_payments.reverse") && (
                          <Btn variant="ghost" onClick={() => {
                            const reason = prompt(`Reverse ${p.payment_number}? Give the reason (at least 10 characters).`);
                            if (reason) run(() => payablesApi.reversePayment(p.id, reason), `${p.payment_number} reversed.`);
                          }}>Reverse</Btn>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          {tab === "credits" && (
            <Card title="Supplier credit notes (no goods returned)" actions={can("supplier_payments.create") && (
              <Btn variant="primary" onClick={() => setCn({ key: newKey(), supplier_id: "", credit_note_number: "", note_date: isoToday(), invoice_id: "",
                reason_code: "RATE_DIFFERENCE", taxable_amount: "", cgst_amount: "", sgst_amount: "", igst_amount: "", notes: "" })}>Record credit note</Btn>)}>
              <p className="text-xs text-slate-400 mb-3">Rate differences, schemes and expiry/damage claims that reduce what you owe. Goods sent back are recorded as purchase returns instead.</p>
              <Table head={["Number", "Supplier", "Date", "Against bill", "Reason", "Total ₹", "Status", ""]} empty={notes.length === 0 && "No credit notes."}>
                {notes.map((n) => (
                  <tr key={n.id} className={n.status === "CANCELLED" ? "opacity-60" : ""}>
                    <td className={`${td} font-mono`}>{n.credit_note_number}</td><td className={td}>{n.supplier_name}</td>
                    <td className={`${td} text-xs`}>{fmtDate(n.note_date)}</td><td className={`${td} font-mono text-xs`}>{n.invoice_number || "— (on account)"}</td>
                    <td className={`${td} text-xs`}>{CREDIT_NOTE_REASONS.find(([k]) => k === n.reason_code)?.[1] || n.reason_code}</td>
                    <td className={tdNum}>{money(n.total_amount)}</td>
                    <td className={td}><Badge value={n.status === "POSTED" ? "SENT" : "VOIDED"} label={n.status === "POSTED" ? "Recorded" : "Cancelled"} />
                      {n.cancel_reason && <div className="text-[11px] text-slate-500 mt-1">{n.cancel_reason}</div>}</td>
                    <td className={td}>{n.status === "POSTED" && can("purchases.invoice_cancel") && (
                      <Btn variant="ghost" onClick={() => { const r = prompt(`Cancel credit note ${n.credit_note_number}? Reason (at least 10 characters):`);
                        if (r) run(() => payablesApi.cancelCreditNote(n.id, r), `Credit note ${n.credit_note_number} cancelled.`); }}>Cancel</Btn>)}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          {tab === "balances" && (
            <>
              <Card title="What each supplier is owed">
                <Table head={["Supplier", "Finalised bills ₹", "Returns ₹", "Paid ₹", "Advance ₹", "Balance ₹", "Overdue bills"]} empty={balances.length === 0 && "No supplier activity."}>
                  {balances.map((b) => (
                    <tr key={b.supplier_id}>
                      <td className={td}>{b.supplier_name}</td>
                      <td className={tdNum}>{money(b.invoiced)}</td>
                      <td className={tdNum}>{money(b.returns)}</td>
                      <td className={tdNum}>{money(b.paid)}</td>
                      <td className={tdNum}>{money(b.unallocated_payments)}</td>
                      <td className={`${tdNum} font-bold ${num(b.balance) < 0 ? "text-sky-300" : ""}`}>{money(b.balance)}</td>
                      <td className={tdNum}>{b.overdue_invoices || ""}</td>
                    </tr>
                  ))}
                </Table>
                <div className="text-xs text-slate-500 mt-2">Balance = finalised bills − posted returns − payments. A negative balance is an advance paid to the supplier.</div>
              </Card>
              {recon && (
                <Card title="Reconciliation check">
                  {recon.ok ? <Notice tone="emerald">Payments, allocations and bill balances agree ({recon.invoices_checked} bills, {recon.payments_checked} payments checked).</Notice> : (
                    <ul className="text-sm text-rose-300 list-disc pl-5">{recon.problems.map((p) => <li key={p}>{p}</li>)}</ul>
                  )}
                </Card>
              )}
            </>
          )}
        </>
      )}

      <Modal open={!!check} title={check ? `Finalise bill ${check.bill.invoice_number}` : ""} onClose={() => setCheck(null)} wide>
        {check && (
          <>
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div>Calculated <div className="font-mono text-white">{money(check.result.calculated_total)}</div></div>
              <div>Supplier&apos;s printed total <div className="font-mono text-white">{money(check.result.declared_total)}</div></div>
              <div>Difference <div className={`font-mono ${num(check.result.discrepancy) !== 0 ? "text-amber-300" : "text-white"}`}>{money(check.result.discrepancy)}</div></div>
            </div>
            {check.result.errors.length > 0 && (
              <div><div className="text-xs text-rose-300 font-semibold mb-1">Must be fixed (edit the draft on the Purchasing page)</div>
                <ul className="text-sm text-rose-300 list-disc pl-5">{check.result.errors.map((e) => <li key={e}>{e}</li>)}</ul></div>
            )}
            {check.result.warnings.length > 0 && (
              <div><div className="text-xs text-amber-300 font-semibold mb-1">Please check</div>
                <ul className="text-sm text-amber-200 list-disc pl-5">{check.result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                <label className="flex items-center gap-2 text-sm mt-2">
                  <input type="checkbox" checked={check.ack} onChange={(e) => setCheck({ ...check, ack: e.target.checked })} /> I have checked these warnings
                </label></div>
            )}
            <div className="text-xs text-slate-500">After finalising, the bill can no longer be edited and the amount becomes payable to the supplier.</div>
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setCheck(null)}>Close</Btn>
              <Btn variant="primary" busy={busy} disabled={!check.result.can_finalize || (check.result.warnings.length > 0 && !check.ack)}
                onClick={async () => { if (await run(() => payablesApi.finalize(check.bill.id, check.ack), `Bill ${check.bill.invoice_number} finalised.`)) setCheck(null); }}>
                Finalise bill
              </Btn>
            </div>
          </>
        )}
      </Modal>

      <Modal open={!!cn} title="Record supplier credit note" onClose={() => setCn(null)} wide>
        {cn && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <Field label="Supplier *">
                <select className={inputCls} value={cn.supplier_id} onChange={async (e) => {
                  const id = e.target.value ? Number(e.target.value) : "";
                  setCn({ ...cn, supplier_id: id, invoice_id: "" });
                  setOpen(id ? await payablesApi.openInvoices(id) : []);
                }}>
                  <option value="">Choose…</option>{suppliers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              </Field>
              <Field label="Supplier's credit note number *"><input className={`${inputCls} font-mono`} value={cn.credit_note_number} onChange={(e) => setCn({ ...cn, credit_note_number: e.target.value })} /></Field>
              <Field label="Date *"><input type="date" max={isoToday()} className={inputCls} value={cn.note_date} onChange={(e) => setCn({ ...cn, note_date: e.target.value })} /></Field>
              <Field label="Against bill">
                <select className={inputCls} value={cn.invoice_id} onChange={(e) => setCn({ ...cn, invoice_id: e.target.value })}>
                  <option value="">On account (no specific bill)</option>
                  {open.map((o) => <option key={o.id} value={o.id}>{o.invoice_number} · owes {money(o.outstanding)}</option>)}
                </select>
              </Field>
              <Field label="Reason *">
                <select className={inputCls} value={cn.reason_code} onChange={(e) => setCn({ ...cn, reason_code: e.target.value })}>
                  {CREDIT_NOTE_REASONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </Field>
              <Field label="Taxable ₹ *"><input className={`${inputCls} font-mono`} value={cn.taxable_amount} onChange={(e) => setCn({ ...cn, taxable_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="CGST ₹"><input className={`${inputCls} font-mono`} value={cn.cgst_amount} onChange={(e) => setCn({ ...cn, cgst_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="SGST ₹"><input className={`${inputCls} font-mono`} value={cn.sgst_amount} onChange={(e) => setCn({ ...cn, sgst_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="IGST ₹ (inter-state)"><input className={`${inputCls} font-mono`} value={cn.igst_amount} onChange={(e) => setCn({ ...cn, igst_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
            </div>
            <Field label="Notes"><input className={inputCls} value={cn.notes} onChange={(e) => setCn({ ...cn, notes: e.target.value })} /></Field>
            <div className="text-sm text-slate-300">Total {money(num(cn.taxable_amount) + num(cn.cgst_amount) + num(cn.sgst_amount) + num(cn.igst_amount))}</div>
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setCn(null)}>Cancel</Btn>
              <Btn variant="primary" busy={busy} disabled={!cn.supplier_id || !cn.credit_note_number.trim() || !(num(cn.taxable_amount) + num(cn.cgst_amount) + num(cn.sgst_amount) + num(cn.igst_amount) > 0)}
                onClick={async () => {
                  if (await run(() => payablesApi.createCreditNote({
                    idempotency_key: cn.key, supplier_id: cn.supplier_id, credit_note_number: cn.credit_note_number, note_date: cn.note_date,
                    invoice_id: cn.invoice_id ? Number(cn.invoice_id) : null, reason_code: cn.reason_code, taxable_amount: cn.taxable_amount || "0",
                    cgst_amount: cn.cgst_amount || "0", sgst_amount: cn.sgst_amount || "0", igst_amount: cn.igst_amount || "0", notes: cn.notes || null,
                  }), `Credit note ${cn.credit_note_number} recorded.`)) setCn(null);
                }}>Record credit note</Btn>
            </div>
          </>
        )}
      </Modal>

      <Modal open={!!pay} title="Record supplier payment" onClose={() => setPay(null)} wide>
        {pay && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <Field label="Supplier *">
                <select className={inputCls} value={pay.supplier_id} onChange={async (e) => {
                  const id = e.target.value ? Number(e.target.value) : "";
                  setPay({ ...pay, supplier_id: id, alloc: {} });
                  setOpen(id ? await payablesApi.openInvoices(id) : []);
                }}>
                  <option value="">Choose…</option>
                  {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
              <Field label="Payment date *"><input type="date" className={inputCls} max={isoToday()} value={pay.payment_date} onChange={(e) => setPay({ ...pay, payment_date: e.target.value })} /></Field>
              <Field label="Amount ₹ *"><input className={`${inputCls} font-mono`} value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="Method *">
                <select className={inputCls} value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value, cash_source: "" })}>
                  {PAYMENT_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              {pay.method === "CASH" ? (
                <Field label="Cash from *" hint="Drawer cash is taken from your open shift">
                  <select className={inputCls} value={pay.cash_source} onChange={(e) => setPay({ ...pay, cash_source: e.target.value })}>
                    <option value="">Choose…</option>
                    <option value="OFFICE_CASH">Office cash / safe (not the drawer)</option>
                    <option value="DRAWER">POS cash drawer (today, open shift)</option>
                  </select>
                </Field>
              ) : (
                <Field label={needsRef ? (pay.method === "CHEQUE" ? "Cheque number *" : "UTR / reference *") : "Reference"}>
                  <input className={`${inputCls} font-mono`} value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} />
                </Field>
              )}
              <Field label="Paid from bank account"><input className={inputCls} value={pay.bank_account} onChange={(e) => setPay({ ...pay, bank_account: e.target.value })} /></Field>
            </div>
            <Field label="Notes"><input className={inputCls} value={pay.notes} onChange={(e) => setPay({ ...pay, notes: e.target.value })} /></Field>

            {pay.supplier_id !== "" && (
              <div>
                <div className="flex items-center justify-between mb-1">
                  <div className="text-xs text-slate-400">Allocate to finalised bills</div>
                  <Btn variant="ghost" disabled={!(num(pay.amount) > 0) || open.length === 0} onClick={fillOldestFirst}>Fill oldest first</Btn>
                </div>
                <Table head={["Bill", "Due", "Owed ₹", "Allocate ₹"]} empty={open.length === 0 && "No open finalised bills — the whole amount will be an advance."}>
                  {open.map((o) => (
                    <tr key={o.id}>
                      <td className={`${td} font-mono`}>{o.invoice_number}</td>
                      <td className={`${td} text-xs`}>{fmtDate(o.due_date)}</td>
                      <td className={tdNum}>{money(o.outstanding)}</td>
                      <td className={td}>
                        <input className={`${inputCls} font-mono w-32 ${toPaise(pay.alloc[o.id]) > toPaise(o.outstanding) ? "border-rose-500" : ""}`}
                          value={pay.alloc[o.id] || ""} onChange={(e) => setPay({ ...pay, alloc: { ...pay.alloc, [o.id]: e.target.value.replace(/[^0-9.]/g, "") } })} />
                      </td>
                    </tr>
                  ))}
                </Table>
                <div className={`text-sm mt-1 ${advance < 0 ? "text-rose-300" : "text-slate-400"}`}>
                  Allocated {money(allocTotal / 100)}{advance > 0 && ` · ${money(advance / 100)} stays as an advance to ${supplierName(Number(pay.supplier_id))}`}
                  {advance < 0 && " · allocations are more than the payment"}
                </div>
              </div>
            )}
            {/confirm/i.test(error || "") && (
              <label className="flex items-center gap-2 text-sm text-amber-200">
                <input type="checkbox" checked={pay.confirm_duplicate} onChange={(e) => setPay({ ...pay, confirm_duplicate: e.target.checked, key: newKey() })} />
                This is a separate payment, not the same one entered twice
              </label>
            )}
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setPay(null)}>Cancel</Btn>
              <Btn variant="primary" busy={busy} disabled={payInvalid} onClick={submitPay}>Record payment</Btn>
            </div>
          </>
        )}
      </Modal>
    </SalesShell>
  );
}
