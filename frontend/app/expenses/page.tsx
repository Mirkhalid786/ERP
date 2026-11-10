"use client";

/**
 * Expenses (Stage 5B preparation): rent, salaries, electricity, courier … paid in cash or
 * through the bank, with approval, reversal and categories. Posted to the accounts automatically once accounting is set up.
 */
import { useCallback, useEffect, useState } from "react";
import { Receipt } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, Stat, Table, errorText, inputCls, isoMonthStart, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { Expense, ExpenseCategory, PAYMENT_METHODS, REFERENCE_REQUIRED, payablesApi } from "@/lib/payables-api";
import { fmtDate, fmtDateTime, money, newKey, num } from "@/lib/sales-api";
import { LedgerBadge, useLedgerStatus } from "@/components/ledger-status";

const STATUS_BADGE: Record<string, [string, string]> = {
  APPROVED: ["SENT", "Approved"], PENDING_APPROVAL: ["PENDING", "Awaiting approval"], REJECTED: ["FAILED", "Rejected"], REVERSED: ["VOIDED", "Reversed"],
};

const blank = () => ({
  key: newKey(), category_id: "" as number | "", expense_date: isoToday(), amount: "", gst_amount: "", payee: "", payee_gstin: "",
  bill_number: "", payment_method: "CASH", cash_source: "", reference: "", notes: "", confirm_duplicate: false,
});

export default function ExpensesPage() {
  const { user, can } = useStaff();
  const [cats, setCats] = useState<ExpenseCategory[]>([]);
  const [list, setList] = useState<Awaited<ReturnType<typeof payablesApi.expenses>> | null>(null);
  const [filters, setFilters] = useState({ date_from: isoMonthStart(), date_to: isoToday(), status: "" });
  const [form, setForm] = useState<ReturnType<typeof blank> | null>(null);
  const [catForm, setCatForm] = useState<{ id: number | null; code: string; name: string; account_code_hint: string; is_active: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      if (can("expenses.view") || can("expenses.create")) setCats(await payablesApi.categories().catch(() => []));
      if (can("expenses.view")) setList(await payablesApi.expenses({ ...filters, limit: 200 }));
    } catch (e) {
      setError(errorText(e));
    }
  }, [filters, can]);
  useEffect(() => { if (user) load(); }, [user, load]);

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

  const needsRef = !!form && REFERENCE_REQUIRED.includes(form.payment_method);
  const invalid = !form || !form.category_id || !(num(form.amount) > 0) || num(form.gst_amount) > num(form.amount) ||
    form.payee.trim().length < 2 || (needsRef && !form.reference.trim()) || (form.payment_method === "CASH" && !form.cash_source);

  const submit = async () => {
    if (!form || !form.category_id) return;
    setBusy(true);
    setError(null);
    try {
      const res = await payablesApi.recordExpense({
        idempotency_key: form.key, category_id: form.category_id, expense_date: form.expense_date, amount: form.amount,
        gst_amount: form.gst_amount || "0", payee: form.payee, payee_gstin: form.payee_gstin || null, bill_number: form.bill_number || null,
        payment_method: form.payment_method, cash_source: form.payment_method === "CASH" ? form.cash_source : null,
        reference: form.reference || null, notes: form.notes || null, confirm_duplicate: form.confirm_duplicate,
      });
      setInfo(`${res.expense_number} ${res.status === "APPROVED" ? "recorded" : "sent for approval"}.`);
      setForm(null);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const allowed = can("expenses.view") || can("expenses.create");
  const ledger = useLedgerStatus((list?.items || []).filter((e) => e.status === "APPROVED" || e.status === "REVERSED")
    .map((e) => `EXPENSE:${e.id}`), can("accounting.view"));
  return (
    <SalesShell user={user} title="Expenses" subtitle="Rent, salaries, electricity, courier and other business expenses" icon={<Receipt className="w-6 h-6" />}>
      <ErrorBox message={error} onClose={() => setError(null)} />
      {info && <Notice tone="emerald">{info}</Notice>}
      {user && !allowed ? <ErrorBox message="You do not have permission to use expenses." /> : (
        <>
          <div className="flex gap-3 text-sm">
            {(can("supplier_payments.view") || can("purchases.invoice_finalize")) && <a className="text-sky-300 hover:underline" href="/payables">← Supplier bills &amp; payments</a>}
            <span className="text-white font-semibold">Expenses</span>
          </div>
          <Notice>Approved expenses and their reversals are posted to the accounts automatically once accounting is set up (from the books start date); pending and rejected ones are not.</Notice>
          <div className="flex flex-wrap items-end gap-2">
            {can("expenses.create") && <Btn variant="primary" onClick={() => setForm(blank())}>Record expense</Btn>}
            {can("expenses.view") && (
              <>
                <Field label="From"><input type="date" className={`${inputCls} w-40`} value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></Field>
                <Field label="To"><input type="date" className={`${inputCls} w-40`} value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></Field>
                <Field label="Status">
                  <select className={`${inputCls} w-48`} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                    <option value="">All</option>
                    {Object.entries(STATUS_BADGE).map(([k, [, l]]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </Field>
              </>
            )}
          </div>

          {list && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Stat label="Approved in period" value={money(list.approved_total)} tone="amber" />
                <Stat label="Awaiting approval" value={String(list.items.filter((e) => e.status === "PENDING_APPROVAL").length)} />
                {list.approved_by_category.slice(0, 2).map((c) => <Stat key={c.category} label={c.category} value={money(c.amount)} hint={`${c.count} expense(s)`} />)}
              </div>
              <Card title="Expenses">
                <Table head={["Number", "Date", "Category", "Payee", "Paid by", "Amount ₹", "Status", ""]} empty={list.items.length === 0 && "No expenses in this period."}>
                  {list.items.map((e: Expense) => {
                    const [badge, label] = STATUS_BADGE[e.status] || ["QUEUED", e.status];
                    return (
                      <tr key={e.id} className={["REVERSED", "REJECTED"].includes(e.status) ? "opacity-60" : ""}>
                        <td className={`${td} font-mono`}>{e.expense_number}{e.bill_number && <div className="text-[11px] text-slate-500">bill {e.bill_number}</div>}</td>
                        <td className={`${td} text-xs`}>{fmtDate(e.expense_date)}<div className="text-slate-500">by {e.created_by_name}</div></td>
                        <td className={td}>{e.category_name}</td>
                        <td className={td}>{e.payee}{e.notes && <div className="text-[11px] text-slate-500">{e.notes}</div>}</td>
                        <td className={`${td} text-xs`}>
                          {e.payment_method.replace("_", " ")}{e.cash_source && ` · ${e.cash_source === "DRAWER" ? "from drawer" : "office cash"}`}
                          {e.reference && <div className="font-mono text-slate-500">{e.reference}</div>}
                        </td>
                        <td className={tdNum}>{money(e.amount)}{num(e.gst_amount) > 0 && <div className="text-[11px] text-slate-500">GST {money(e.gst_amount)}</div>}</td>
                        <td className={td}>
                          <Badge value={badge} label={label} />
                          {e.approved_by_name && e.status !== "PENDING_APPROVAL" && <div className="text-[11px] text-slate-500 mt-1">{e.status === "REJECTED" ? "rejected" : "approved"} by {e.approved_by_name}</div>}
                          {e.decision_note && <div className="text-[11px] text-slate-500">{e.decision_note}</div>}
                          {e.reversal_reason && <div className="text-[11px] text-slate-500">{e.reversal_reason} — {e.reversed_by_name}, {fmtDateTime(e.reversed_at)}</div>}
                          <div className="mt-1"><LedgerBadge status={ledger[`EXPENSE:${e.id}`]} /></div>
                        </td>
                        <td className={td}>
                          <div className="flex flex-wrap gap-1 justify-end">
                            {e.status === "PENDING_APPROVAL" && can("expenses.approve") && (
                              <>
                                <Btn variant="ghost" onClick={() => run(() => payablesApi.approveExpense(e.id), `${e.expense_number} approved.`)}>Approve</Btn>
                                <Btn variant="ghost" onClick={() => {
                                  const note = prompt("Reason for rejecting this expense");
                                  if (note) run(() => payablesApi.rejectExpense(e.id, note), `${e.expense_number} rejected.`);
                                }}>Reject</Btn>
                              </>
                            )}
                            {e.status === "APPROVED" && can("expenses.reverse") && (
                              <Btn variant="ghost" onClick={() => {
                                const reason = prompt(`Reverse ${e.expense_number}? Give the reason (at least 10 characters).`);
                                if (reason) run(() => payablesApi.reverseExpense(e.id, reason), `${e.expense_number} reversed.`);
                              }}>Reverse</Btn>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </Table>
              </Card>
            </>
          )}

          {can("expenses.configure") && (
            <Card title="Expense categories" actions={<Btn variant="ghost" onClick={() => setCatForm({ id: null, code: "", name: "", account_code_hint: "", is_active: true })}>Add category</Btn>}>
              <Table head={["Code", "Name", "Suggested account", "Active", ""]}>
                {cats.map((c) => (
                  <tr key={c.id} className={c.is_active ? "" : "opacity-50"}>
                    <td className={`${td} font-mono text-xs`}>{c.code}</td>
                    <td className={td}>{c.name}</td>
                    <td className={`${td} font-mono text-xs`}>{c.account_code_hint || "—"}</td>
                    <td className={td}>{c.is_active ? "Yes" : "No"}</td>
                    <td className={td}><Btn variant="ghost" onClick={() => setCatForm({ id: c.id, code: c.code, name: c.name, account_code_hint: c.account_code_hint || "", is_active: c.is_active })}>Edit</Btn></td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}
        </>
      )}

      <Modal open={!!form} title="Record expense" onClose={() => setForm(null)} wide>
        {form && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <Field label="Category *">
                <select className={inputCls} value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value ? Number(e.target.value) : "" })}>
                  <option value="">Choose…</option>
                  {cats.filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </Field>
              <Field label="Date *"><input type="date" max={isoToday()} className={inputCls} value={form.expense_date} onChange={(e) => setForm({ ...form, expense_date: e.target.value })} /></Field>
              <Field label="Paid to *"><input className={inputCls} value={form.payee} onChange={(e) => setForm({ ...form, payee: e.target.value })} /></Field>
              <Field label="Total paid ₹ (incl. GST) *"><input className={`${inputCls} font-mono`} value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="GST in that amount ₹"><input className={`${inputCls} font-mono`} value={form.gst_amount} onChange={(e) => setForm({ ...form, gst_amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
              <Field label="Payee GSTIN"><input className={`${inputCls} font-mono`} value={form.payee_gstin} onChange={(e) => setForm({ ...form, payee_gstin: e.target.value.toUpperCase() })} /></Field>
              <Field label="Bill / receipt number"><input className={inputCls} value={form.bill_number} onChange={(e) => setForm({ ...form, bill_number: e.target.value })} /></Field>
              <Field label="Paid by *">
                <select className={inputCls} value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value, cash_source: "" })}>
                  {PAYMENT_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              {form.payment_method === "CASH" ? (
                <Field label="Cash from *" hint={can("expenses.approve") ? "Drawer cash comes out of your open shift" : "Paying from the drawer needs a manager"}>
                  <select className={inputCls} value={form.cash_source} onChange={(e) => setForm({ ...form, cash_source: e.target.value })}>
                    <option value="">Choose…</option>
                    <option value="OFFICE_CASH">Office cash / safe (not the drawer)</option>
                    {can("expenses.approve") && <option value="DRAWER">POS cash drawer (today, open shift)</option>}
                  </select>
                </Field>
              ) : (
                <Field label={needsRef ? "UTR / cheque number *" : "Reference"}><input className={`${inputCls} font-mono`} value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} /></Field>
              )}
            </div>
            <Field label="Notes"><input className={inputCls} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            {!can("expenses.approve") && <div className="text-xs text-amber-300">This expense will wait for a manager&apos;s approval.</div>}
            {/confirm/i.test(error || "") && (
              <label className="flex items-center gap-2 text-sm text-amber-200">
                <input type="checkbox" checked={form.confirm_duplicate} onChange={(e) => setForm({ ...form, confirm_duplicate: e.target.checked, key: newKey() })} />
                This is a separate expense, not the same one entered twice
              </label>
            )}
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setForm(null)}>Cancel</Btn>
              <Btn variant="primary" busy={busy} disabled={invalid} onClick={submit}>Save expense</Btn>
            </div>
          </>
        )}
      </Modal>

      <Modal open={!!catForm} title={catForm?.id ? "Edit category" : "Add category"} onClose={() => setCatForm(null)}>
        {catForm && (
          <>
            {!catForm.id && <Field label="Code *"><input className={`${inputCls} font-mono`} value={catForm.code} onChange={(e) => setCatForm({ ...catForm, code: e.target.value.toUpperCase() })} /></Field>}
            <Field label="Name *"><input className={inputCls} value={catForm.name} onChange={(e) => setCatForm({ ...catForm, name: e.target.value })} /></Field>
            <Field label="Suggested account code" hint="Used when expenses are connected to the accounts"><input className={`${inputCls} font-mono`} value={catForm.account_code_hint} onChange={(e) => setCatForm({ ...catForm, account_code_hint: e.target.value })} /></Field>
            {catForm.id && (
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={catForm.is_active} onChange={(e) => setCatForm({ ...catForm, is_active: e.target.checked })} /> Active</label>
            )}
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setCatForm(null)}>Cancel</Btn>
              <Btn variant="primary" busy={busy} disabled={catForm.name.trim().length < 2 || (!catForm.id && catForm.code.trim().length < 2)}
                onClick={async () => {
                  if (await run(() => payablesApi.saveCategory(catForm.id, {
                    code: catForm.code, name: catForm.name, account_code_hint: catForm.account_code_hint || null,
                    is_active: catForm.id ? catForm.is_active : undefined,
                  }), "Category saved.")) setCatForm(null);
                }}>Save</Btn>
            </div>
          </>
        )}
      </Modal>
    </SalesShell>
  );
}
