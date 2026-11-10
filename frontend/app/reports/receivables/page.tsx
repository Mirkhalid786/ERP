"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Download, PieChart } from "lucide-react";
import {
  Card, ErrorBox, Field, SalesShell, Stat, Table, errorText, inputCls, isoMonthStart, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { fmtDate, fmtDateTime, money, salesApi } from "@/lib/sales-api";

type Row = Record<string, string | number | null>;

const BUCKETS: [string, string][] = [
  ["not_due", "Not yet due"], ["days_1_30", "1–30"], ["days_31_60", "31–60"], ["days_61_90", "61–90"], ["days_over_90", "90+"],
];

export default function ReceivablesReportPage() {
  const { user, can } = useStaff();
  const [aging, setAging] = useState<{ as_of: string; totals: Record<string, string>; grand_total: string; customers: Row[] } | null>(null);
  const [credit, setCredit] = useState<{ credit_sales: Row[]; collections: Row[]; credit_total: string; collections_total: string } | null>(null);
  const [from, setFrom] = useState(isoMonthStart());
  const [to, setTo] = useState(isoToday());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || !can("reports.view")) return;
    salesApi.report<NonNullable<typeof aging>>("aging").then((r) => setAging(r.data)).catch((e) => setError(errorText(e)));
  }, [user, can]);
  useEffect(() => {
    if (!user || !can("reports.view")) return;
    salesApi.report<NonNullable<typeof credit>>("credit", from, to).then((r) => setCredit(r.data)).catch((e) => setError(errorText(e)));
  }, [user, can, from, to]);

  return (
    <SalesShell user={user} title="Credit & ageing reports" subtitle="Who owes what, for how long; credit given vs collected" icon={<PieChart className="w-6 h-6" />}>
      {user && !can("reports.view") ? <ErrorBox message="You do not have permission to view reports." /> : (
        <>
          <ErrorBox message={error} onClose={() => setError(null)} />
          {aging && (
            <Card title={`Ageing as of ${fmtDate(aging.as_of)}`} actions={can("reports.export") && <a className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline" href={salesApi.reportCsvUrl("aging")}><Download className="w-3 h-3" /> CSV</a>}>
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
                <Stat label="Total receivable" value={money(aging.grand_total)} tone="amber" />
                {BUCKETS.map(([k, l]) => <Stat key={k} label={l === "Not yet due" ? l : `${l} days overdue`} value={money(aging.totals[k])} tone={k === "not_due" ? "slate" : "rose"} />)}
              </div>
              <Table head={["Customer", "Bills", ...BUCKETS.map(([, l]) => `${l} ₹`), "Total ₹"]} empty={aging.customers.length === 0 && "Nothing outstanding."}>
                {aging.customers.map((c) => (
                  <tr key={String(c.customer_id)}>
                    <td className={td}><Link className="text-emerald-300 hover:underline" href={`/customers/${c.customer_id}`}>{c.customer_name}</Link><div className="text-[11px] text-slate-500">{c.customer_mobile ? `+${c.customer_mobile}` : ""}</div></td>
                    <td className={tdNum}>{c.invoices}</td>
                    {BUCKETS.map(([k]) => <td key={k} className={tdNum}>{money(c[k] as string)}</td>)}
                    <td className={`${tdNum} font-bold`}>{money(c.total as string)}</td>
                  </tr>
                ))}
              </Table>
            </Card>
          )}

          <Card title="Credit given and collected" actions={
            <div className="flex gap-2 items-end">
              <Field label="From"><input type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
              <Field label="To"><input type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} /></Field>
            </div>
          }>
            {credit && (
              <>
                <div className="grid grid-cols-2 gap-3 mb-4">
                  <Stat label="Credit given (unpaid at counter)" value={money(credit.credit_total)} tone="amber" />
                  <Stat label="Udhaar collected later" value={money(credit.collections_total)} tone="emerald" />
                </div>
                <div className="text-xs text-slate-400 mb-1">Credit sales</div>
                <Table head={["Invoice", "Date", "Customer", "Total ₹", "Paid at sale ₹", "Credit ₹", "Balance ₹", "Due"]} empty={credit.credit_sales.length === 0 && "No credit sales."}>
                  {credit.credit_sales.map((r) => (
                    <tr key={String(r.invoice_id)}>
                      <td className={td}><Link className="text-emerald-300 hover:underline font-mono" href={`/sales/${r.invoice_id}`}>{r.invoice_number}</Link></td>
                      <td className={td}>{fmtDate(r.invoice_date as string)}</td>
                      <td className={td}>{r.customer_name}</td>
                      {["grand_total", "paid_at_sale", "credit_given", "balance_due"].map((k) => <td key={k} className={tdNum}>{money(r[k] as string)}</td>)}
                      <td className={td}>{fmtDate(r.due_date as string)}</td>
                    </tr>
                  ))}
                </Table>
                <div className="text-xs text-slate-400 mt-4 mb-1">Collections</div>
                <Table head={["Receipt", "When", "Method", "Reference", "Verified", "Amount ₹"]} empty={credit.collections.length === 0 && "No collections."}>
                  {credit.collections.map((r) => (
                    <tr key={String(r.payment_id)}>
                      <td className={`${td} font-mono`}>{r.receipt_number}</td>
                      <td className={td}>{fmtDateTime(r.received_at as string)}</td>
                      <td className={td}>{r.method}</td>
                      <td className={td}>{r.reference || "—"}</td>
                      <td className={td}>{r.verification_status === "VERIFIED" ? "Yes" : r.verification_status === "RECORDED" ? "Not yet" : "—"}</td>
                      <td className={tdNum}>{money(r.amount as string)}</td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
          </Card>
        </>
      )}
    </SalesShell>
  );
}
