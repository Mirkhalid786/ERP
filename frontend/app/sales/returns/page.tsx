"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { RotateCcw, Search } from "lucide-react";
import {
  Btn, Card, ErrorBox, Field, Notice, SalesShell, Table, errorText, inputCls, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { Invoice, InvoiceListItem, SalesReturn, fmtDate, fmtDateTime, money, newKey, num, salesApi } from "@/lib/sales-api";

interface ReturnRow { qty: string; condition: string }

function ReturnsView() {
  const { user, can } = useStaff();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [matches, setMatches] = useState<InvoiceListItem[]>([]);
  const [inv, setInv] = useState<Invoice | null>(null);
  const [rows, setRows] = useState<Record<number, ReturnRow>>({});
  const [reason, setReason] = useState("");
  const [settlement, setSettlement] = useState("REFUND");
  const [refundMethod, setRefundMethod] = useState("");
  const [refundRef, setRefundRef] = useState("");
  const [key, setKey] = useState(newKey());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SalesReturn | null>(null);
  const [recent, setRecent] = useState<SalesReturn[]>([]);

  const loadRecent = () => salesApi.returns({ limit: 15 }).then((r) => setRecent(r.items)).catch(() => undefined);

  const pick = async (id: number) => {
    setError(null);
    setResult(null);
    try {
      const invoice = await salesApi.invoice(id);
      setInv(invoice);
      setRows({});
      setKey(newKey());
      setMatches([]);
      setQ("");
    } catch (e) {
      setError(errorText(e));
    }
  };

  useEffect(() => {
    if (!user) return;
    loadRecent();
    const pre = params.get("invoice");
    if (pre) pick(Number(pre));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  useEffect(() => {
    if (q.trim().length < 2) { setMatches([]); return; }
    const t = setTimeout(() => salesApi.invoices({ q: q.trim(), status: "FINALIZED", limit: 10 }).then((r) => setMatches(r.items)).catch(() => setMatches([])), 250);
    return () => clearTimeout(t);
  }, [q]);

  const lines = inv?.items
    .map((it) => ({ it, row: rows[it.id], returnable: num(it.quantity_base) - num(it.returned_quantity_base) }))
    .filter(({ row }) => row && num(row.qty) > 0) || [];
  const estimate = lines.reduce((s, { it, row }) => s + (num(it.line_total) * num(row.qty)) / num(it.quantity_base), 0);

  const submit = async () => {
    if (!inv) return;
    setBusy(true);
    setError(null);
    try {
      const r = await salesApi.createReturn({
        idempotency_key: key, invoice_id: inv.id, reason,
        items: lines.map(({ it, row }) => ({ invoice_item_id: it.id, quantity_base: row.qty, condition: row.condition })),
        settlement, refund_method: refundMethod || null, refund_reference: refundRef || undefined,
      });
      setResult(r);
      setInv(await salesApi.invoice(inv.id));
      setRows({});
      setReason("");
      setKey(newKey());
      loadRecent();
    } catch (e) {
      setError(errorText(e));   // key kept: a retry cannot post the return twice
    } finally {
      setBusy(false);
    }
  };

  return (
    <SalesShell user={user} title="Sales returns" subtitle="Return items against a finalised invoice" icon={<RotateCcw className="w-6 h-6" />}>
      {user && !can("sales.refund") ? <ErrorBox message="You do not have permission to process returns." /> : (
        <>
          <Card title="1. Find the original invoice">
            <div className="relative">
              <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
              <input className={`${inputCls} pl-9`} placeholder="Invoice number, customer name or mobile" value={q} onChange={(e) => setQ(e.target.value)} />
              {matches.length > 0 && (
                <div className="absolute z-20 left-0 right-0 mt-1 bg-slate-900 border border-slate-700 rounded-xl shadow-xl max-h-72 overflow-y-auto">
                  {matches.map((m) => (
                    <button key={m.id} onClick={() => pick(m.id)} className="w-full text-left px-3 py-2 hover:bg-slate-800 border-b border-slate-800 last:border-0 text-sm">
                      <span className="font-mono text-emerald-300">{m.invoice_number}</span> · {m.customer_name} · {money(m.grand_total)} · {fmtDateTime(m.invoice_date)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <ErrorBox message={error} onClose={() => setError(null)} />
          {result && (
            <Notice tone="emerald">
              Return {result.return_number} posted: value {money(result.total_amount)}
              {num(result.applied_to_balance) > 0 && ` · ${money(result.applied_to_balance)} taken off the balance`}
              {num(result.refund_amount) > 0 && ` · refund ${money(result.refund_amount)} by ${result.refund_method}`}
              {num(result.credit_note_amount) > 0 && ` · ${money(result.credit_note_amount)} kept as customer credit`}
              . Restocked: {result.items.filter((i) => i.disposition === "RESTOCKED").length}, written off: {result.items.filter((i) => i.disposition === "WRITTEN_OFF").length}.
            </Notice>
          )}

          {inv && (
            <Card title={`2. Items on ${inv.invoice_number} (${inv.customer_name})`} actions={<Link className="text-xs text-emerald-300 hover:underline" href={`/sales/${inv.id}`}>Open invoice</Link>}>
              {inv.status !== "FINALIZED" && <ErrorBox message="This invoice is voided; nothing can be returned." />}
              <Table head={["Medicine", "Batch / Exp", "Sold", "Returned", "Return qty", "Condition"]}>
                {inv.items.map((it) => {
                  const returnable = num(it.quantity_base) - num(it.returned_quantity_base);
                  const row = rows[it.id] || { qty: "", condition: "SALEABLE" };
                  return (
                    <tr key={it.id}>
                      <td className={td}>{it.medicine_name}<div className="text-[11px] text-slate-500">{money(it.line_total)} for {num(it.quantity_base)} units</div></td>
                      <td className={`${td} text-xs text-slate-400`}>{it.batch_number}<br />{fmtDate(it.expiry_date)}</td>
                      <td className={tdNum}>{num(it.quantity_base)}</td>
                      <td className={tdNum}>{num(it.returned_quantity_base)}</td>
                      <td className={td}>
                        <input disabled={returnable <= 0} className={`${inputCls} w-24 font-mono`} placeholder={`max ${returnable}`} value={row.qty}
                          onChange={(e) => setRows({ ...rows, [it.id]: { ...row, qty: e.target.value.replace(/[^0-9.]/g, "") } })} />
                        <div className="text-[10px] text-slate-500">in base units (e.g. tablets)</div>
                      </td>
                      <td className={td}>
                        <select className={inputCls} value={row.condition} disabled={returnable <= 0} onChange={(e) => setRows({ ...rows, [it.id]: { ...row, condition: e.target.value } })}>
                          <option value="SALEABLE">Sealed, saleable</option>
                          <option value="OPENED">Opened</option>
                          <option value="DAMAGED">Damaged</option>
                          <option value="EXPIRED">Expired</option>
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </Table>
              <Notice>Only sealed, saleable items into an active, unexpired batch go back into stock. Opened, damaged or expired items are recorded as written off.</Notice>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-2 mt-3">
                <div className="md:col-span-2"><Field label="Reason *"><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
                <Field label="Amount not owed on the bill">
                  <select className={inputCls} value={settlement} onChange={(e) => setSettlement(e.target.value)}>
                    <option value="REFUND">Refund money</option>
                    {inv.customer_id && <option value="CUSTOMER_CREDIT">Keep as customer credit</option>}
                  </select>
                </Field>
                {settlement === "REFUND" && (
                  <Field label="Refund method" hint="Default: original payment method">
                    <select className={inputCls} value={refundMethod} onChange={(e) => setRefundMethod(e.target.value)}>
                      <option value="">Original method</option>
                      {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m}>{m}</option>)}
                    </select>
                  </Field>
                )}
                {settlement === "REFUND" && refundMethod && refundMethod !== "CASH" && (
                  <Field label="Refund reference"><input className={inputCls} value={refundRef} onChange={(e) => setRefundRef(e.target.value)} /></Field>
                )}
              </div>
              <div className="flex items-center justify-between mt-3">
                <div className="text-sm text-slate-400">
                  Estimated value <span className="font-mono text-white">{money(estimate)}</span>
                  {num(inv.balance_due) > 0 && <> · first reduces the {money(inv.balance_due)} still due</>}
                </div>
                <Btn variant="primary" busy={busy} disabled={lines.length === 0 || reason.trim().length < 3 || inv.status !== "FINALIZED"} onClick={submit}>
                  Post return
                </Btn>
              </div>
            </Card>
          )}

          <Card title="Recent returns">
            <Table head={["Return", "Invoice", "Customer", "When", "Value", "Refund"]} empty={recent.length === 0 && "No returns yet."}>
              {recent.map((r) => (
                <tr key={r.id}>
                  <td className={`${td} font-mono`}>{r.return_number}</td>
                  <td className={td}><Link className="text-emerald-300 hover:underline font-mono" href={`/sales/${r.invoice_id}`}>{r.invoice_number}</Link></td>
                  <td className={td}>{r.customer_name}</td>
                  <td className={`${td} text-xs text-slate-400`}>{fmtDateTime(r.created_at)}</td>
                  <td className={tdNum}>{money(r.total_amount)}</td>
                  <td className={tdNum}>{num(r.refund_amount) ? `${money(r.refund_amount)} ${r.refund_method}` : num(r.credit_note_amount) ? `${money(r.credit_note_amount)} credit` : "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </>
      )}
    </SalesShell>
  );
}

export default function ReturnsPage() {
  return <Suspense><ReturnsView /></Suspense>;
}
