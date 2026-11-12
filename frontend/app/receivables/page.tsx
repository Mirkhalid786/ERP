"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { BellRing, HandCoins, Printer, Search } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Notice, SalesShell, Stat, Table, errorText, inputCls, isoToday, openTabNow, sendTabTo, td,
  tdNum, useStaff,
} from "@/components/sales-ui";
import { InvoiceListItem, Message, PaymentResult, ReminderCandidate, fmtDate, fmtDateTime, money, num, salesApi } from "@/lib/sales-api";

const BUCKETS: [string, string][] = [
  ["not_due", "Not yet due"], ["days_1_30", "1–30 days"], ["days_31_60", "31–60 days"], ["days_61_90", "61–90 days"], ["days_over_90", "Over 90 days"],
];

export default function ReceivablesPage() {
  const { user, can } = useStaff();
  const [q, setQ] = useState("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [outstanding, setOutstanding] = useState("0");
  const [aging, setAging] = useState<Record<string, string> | null>(null);
  const [pending, setPending] = useState<Message[]>([]);
  const [payQ, setPayQ] = useState("");
  const [payMethod, setPayMethod] = useState("");
  const [payments, setPayments] = useState<PaymentResult[]>([]);
  const [scan, setScan] = useState<{ dry: boolean; results: ReminderCandidate[] } | null>(null);
  const [scanDate, setScanDate] = useState(isoToday());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await salesApi.receivables({ q: q.trim() || undefined, overdue_only: overdueOnly, limit: 200 });
      setRows(r.items);
      setOutstanding(r.total_outstanding);
      setAging((await salesApi.aging()).totals);
      if (can("messaging.send")) setPending((await salesApi.outbox({ status: "PENDING", limit: 50 })).items);
      if (can("payments.receive")) {
        setPayments((await salesApi.customerPayments({ q: payQ.trim() || undefined, method: payMethod || undefined, limit: 50 })).items);
      }
    } catch (e) {
      setError(errorText(e));
    }
  }, [q, overdueOnly, can, payQ, payMethod]);

  useEffect(() => {
    if (!user) return;
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [user, load]);

  const runScan = async (dry: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const r = await salesApi.scanReminders(dry, scanDate);
      setScan({ dry, results: r.results });
      if (!dry) load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const share = async (m: Message) => {
    const tab = openTabNow();
    try {
      sendTabTo(tab, (await salesApi.shareMessage(m.id)).share_url);
      load();
    } catch (e) {
      sendTabTo(tab, null);
      setError(errorText(e));
      load();
    }
  };

  return (
    <SalesShell user={user} title="Receivables" subtitle="Udhaar: open credit bills, ageing and payment reminders" icon={<HandCoins className="w-6 h-6" />}>
      <ErrorBox message={error} onClose={() => setError(null)} />
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Stat label="Total outstanding" value={money(outstanding)} tone="amber" />
        {BUCKETS.map(([k, label]) => <Stat key={k} label={label} value={money(aging?.[k])} tone={k === "not_due" ? "slate" : "rose"} />)}
      </div>

      {can("messaging.send") && (
        <Card title="Payment reminders" actions={
          <div className="flex flex-wrap items-center gap-2">
            <input type="date" className={`${inputCls} w-40`} value={scanDate} onChange={(e) => setScanDate(e.target.value)} aria-label="Scan date" />
            <Btn busy={busy} onClick={() => runScan(true)}>Preview (test mode)</Btn>
            {can("messaging.configure") && <Btn variant="primary" busy={busy} onClick={() => runScan(false)}><BellRing className="w-4 h-4" /> Create due reminders</Btn>}
          </div>
        }>
          <Notice>Reminders go only to customers who agreed to WhatsApp and have not opted out; paid, paused and disputed bills are skipped. One reminder per bill per window — repeats are blocked.</Notice>
          {scan && (
            <div className="mt-3">
              <div className="text-xs text-slate-400 mb-1">{scan.dry ? "Preview only — nothing was created." : "Reminders created."} {scan.results.length} bill(s) checked.</div>
              <Table head={["Invoice", "Customer", "Reminder", "Due", "Balance", "Result"]} empty={scan.results.length === 0 && "No bills need a reminder on this date."}>
                {scan.results.map((r) => (
                  <tr key={`${r.invoice_id}-${r.purpose}`}>
                    <td className={`${td} font-mono`}>{r.invoice_number}</td>
                    <td className={td}>{r.customer_name}</td>
                    <td className={`${td} text-xs`}>{r.purpose.replace("REMINDER_", "").replace("_", " ").toLowerCase()}</td>
                    <td className={td}>{fmtDate(r.due_date)}</td>
                    <td className={tdNum}>{money(r.balance_due)}</td>
                    <td className={td}>{r.eligible ? <Badge value="PAID" label={scan.dry ? "Would send" : "Created"} /> : <span className="text-xs text-slate-400">{r.reason}</span>}</td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
          <div className="mt-4">
            <div className="text-xs text-slate-400 mb-1">Waiting to be sent by staff ({pending.length})</div>
            <Table head={["Created", "Customer", "Invoice", "Message", ""]} empty={pending.length === 0 && "No pending reminders."}>
              {pending.map((m) => (
                <tr key={m.id}>
                  <td className={`${td} text-xs text-slate-400`}>{fmtDateTime(m.created_at)}</td>
                  <td className={td}>{m.customer_name}<div className="text-[11px] text-slate-500">+{m.to_number}</div></td>
                  <td className={`${td} font-mono text-xs`}>{m.invoice_number}</td>
                  <td className={`${td} text-xs text-slate-400 max-w-md`}>{m.body}</td>
                  <td className={td}>
                    <div className="flex gap-2">
                      <Btn variant="primary" onClick={() => share(m)}>Open WhatsApp</Btn>
                      <Btn variant="ghost" onClick={async () => { await salesApi.cancelMessage(m.id).catch((e) => setError(errorText(e))); load(); }}>Skip</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
          </div>
        </Card>
      )}

      {can("payments.receive") && (
        <Card title="Payment history" actions={
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
              <input className={`${inputCls} pl-9 w-48`} placeholder="Receipt number" value={payQ} onChange={(e) => setPayQ(e.target.value)} />
            </div>
            <select className={`${inputCls} w-36`} value={payMethod} onChange={(e) => setPayMethod(e.target.value)} aria-label="Payment method">
              <option value="">All methods</option>
              {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        }>
          <Table head={["Receipt", "Date", "Method", "Amount", "Advance", "Status", ""]} empty={payments.length === 0 && "No payments match."}>
            {payments.map((p) => (
              <tr key={p.id}>
                <td className={`${td} font-mono text-xs`}>{p.receipt_number}</td>
                <td className={`${td} text-xs text-slate-400`}>{p.received_at ? fmtDateTime(p.received_at) : ""}</td>
                <td className={td}>{p.method}{p.reference ? <div className="text-[11px] text-slate-500">{p.reference}</div> : null}</td>
                <td className={tdNum}>{money(p.amount)}</td>
                <td className={tdNum}>{num(p.unapplied_amount) > 0 ? money(p.unapplied_amount) : ""}</td>
                <td className={td}><Badge value={p.status || "POSTED"} /></td>
                <td className={td}>
                  <span className="flex gap-2 text-xs whitespace-nowrap">
                    <button className="text-emerald-300 hover:underline" onClick={() => window.open(`/customers/payments/${p.id}/print?layout=thermal&auto=1`, "_blank")}><Printer className="w-3 h-3 inline" /> Receipt</button>
                    <a className="text-slate-300 hover:underline" href={salesApi.paymentPdfUrl(p.id)}>PDF</a>
                  </span>
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}

      <Card title={`Open bills (${rows.length})`} actions={
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
            <input className={`${inputCls} pl-9 w-56`} placeholder="Customer / invoice" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <label className="text-xs text-slate-400 flex items-center gap-1"><input type="checkbox" checked={overdueOnly} onChange={(e) => setOverdueOnly(e.target.checked)} /> Overdue</label>
        </div>
      }>
        <Table head={["Invoice", "Customer", "Date", "Due", "Total", "Paid", "Balance"]} empty={rows.length === 0 && "No open bills."}>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className={td}><Link className="font-mono text-emerald-300 hover:underline" href={`/sales/${r.id}`}>{r.invoice_number}</Link></td>
              <td className={td}>{r.customer_id ? <Link className="hover:underline" href={`/customers/${r.customer_id}`}>{r.customer_name}</Link> : r.customer_name}</td>
              <td className={`${td} text-xs text-slate-400`}>{fmtDate(r.invoice_date)}</td>
              <td className={td}>{fmtDate(r.due_date)} {r.overdue_days > 0 && <Badge value="OVERDUE" label={`${r.overdue_days}d`} />}</td>
              <td className={tdNum}>{money(r.grand_total)}</td>
              <td className={tdNum}>{money(r.amount_paid)}</td>
              <td className={`${tdNum} ${num(r.balance_due) > 0 ? "text-amber-300" : ""}`}>{money(r.balance_due)}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </SalesShell>
  );
}
