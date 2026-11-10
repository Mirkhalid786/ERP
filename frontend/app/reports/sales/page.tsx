"use client";

import { useEffect, useState } from "react";
import { BarChart3, Download } from "lucide-react";
import {
  Card, ErrorBox, Field, SalesShell, Spinner, Stat, Table, errorText, inputCls, isoMonthStart, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { fmtDate, fmtDateTime, money, num, salesApi } from "@/lib/sales-api";

type Row = Record<string, unknown>;
type Summary = Record<string, unknown> & { collections_by_method: Record<string, Row> };

const REPORTS: { key: string; label: string; cols: [string, string][] }[] = [
  { key: "daily", label: "Daily", cols: [["date", "Date"], ["bills", "Bills"], ["gross_sales", "Gross ₹"], ["discounts", "Discount ₹"], ["net_sales", "Net sales ₹"], ["tax", "GST ₹"], ["returns_value", "Returns ₹"], ["net_after_returns", "After returns ₹"], ["credit_given", "Credit ₹"]] },
  { key: "items", label: "Item-wise", cols: [["medicine_name", "Medicine"], ["quantity_sold_base", "Qty sold"], ["quantity_returned_base", "Qty returned"], ["net_sales", "Net sales ₹"], ["taxable_value", "Taxable ₹"], ["cost", "Cost ₹"], ["margin_before_returns", "Margin ₹"]] },
  { key: "batches", label: "Batch-wise", cols: [["medicine_name", "Medicine"], ["batch_number", "Batch"], ["expiry_date", "Expiry"], ["quantity_sold_base", "Qty sold"], ["net_sales", "Net sales ₹"], ["cost", "Cost ₹"]] },
  { key: "cashiers", label: "Cashier-wise", cols: [["cashier_name", "Cashier"], ["bills", "Bills"], ["net_sales", "Net sales ₹"], ["discounts", "Discount ₹"], ["credit_given", "Credit ₹"], ["average_bill", "Avg bill ₹"]] },
  { key: "discounts", label: "Discounts", cols: [["invoice_number", "Invoice"], ["invoice_date", "Date"], ["cashier_name", "Cashier"], ["customer_name", "Customer"], ["gross_amount", "Gross ₹"], ["discount_total", "Discount ₹"], ["discount_percent", "Disc %"]] },
  { key: "register", label: "Invoice register", cols: [["invoice_number", "Invoice"], ["invoice_date", "Date"], ["status", "Status"], ["customer_name", "Customer"], ["customer_gstin", "GSTIN"], ["taxable_value", "Taxable ₹"], ["cgst", "CGST ₹"], ["sgst", "SGST ₹"], ["igst", "IGST ₹"], ["grand_total", "Total ₹"]] },
];

function cell(key: string, v: unknown) {
  if (v === null || v === undefined || v === "") return "—";
  if (/date$/.test(key) && typeof v === "string") return v.length > 10 ? fmtDateTime(v) : fmtDate(v);
  if (/_base$|bills|percent/.test(key)) return String(num(v as string));
  if (typeof v === "string" && /^-?\d+\.\d{2}$/.test(v)) return money(v);
  return String(v);
}

export default function SalesReportsPage() {
  const { user, can } = useStaff();
  const [from, setFrom] = useState(isoMonthStart());
  const [to, setTo] = useState(isoToday());
  const [tab, setTab] = useState("daily");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dash, setDash] = useState<Awaited<ReturnType<typeof salesApi.dashboard>> | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [extra, setExtra] = useState<Row | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || !can("reports.view")) return;
    setError(null);
    salesApi.dashboard().then(setDash).catch((e) => setError(errorText(e)));
    salesApi.report<Summary>("summary", from, to).then((r) => setSummary(r.data)).catch((e) => setError(errorText(e)));
  }, [user, can, from, to]);

  useEffect(() => {
    if (!user || !can("reports.view")) return;
    setRows(null);
    setExtra(null);
    if (["payments", "tax", "returns"].includes(tab)) {
      salesApi.report<Row>(tab, from, to).then((r) => setExtra(r.data)).catch((e) => setError(errorText(e)));
    } else {
      salesApi.report<Row[]>(tab, from, to).then((r) => setRows(r.data)).catch((e) => setError(errorText(e)));
    }
  }, [user, can, tab, from, to]);

  const def = REPORTS.find((r) => r.key === tab);
  const t = (dash?.today || {}) as Record<string, string>;

  return (
    <SalesShell user={user} title="Sales reports" subtitle="Gross vs net sales, GST, collections, credit and returns" icon={<BarChart3 className="w-6 h-6" />}>
      {user && !can("reports.view") ? <ErrorBox message="You do not have permission to view reports." /> : (
        <>
          <ErrorBox message={error} onClose={() => setError(null)} />
          {dash && (
            <Card title="Today">
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                <Stat label="Net sales today" value={money(t.net_sales)} hint={`${t.bills ?? 0} bills · avg ${money(t.average_bill)}`} tone="emerald" />
                <Stat label="Collections today" value={money(t.collections_total)} hint="All methods, incl. udhaar received" />
                <Stat label="Credit given today" value={money(t.credit_given)} tone="amber" />
                <Stat label="Returns today" value={money(t.returns_value)} hint={`Refunds paid ${money(t.refunds_paid)}`} />
                <Stat label="Outstanding (all)" value={money(dash.outstanding_receivables)} hint={`${dash.customers_with_balance} customers`} tone="amber" />
                <Stat label="Overdue (all)" value={money(dash.overdue_receivables)} tone="rose" />
              </div>
            </Card>
          )}

          <Card>
            <div className="flex flex-wrap gap-2 items-end">
              <Field label="From"><input type="date" className={inputCls} value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
              <Field label="To"><input type="date" className={inputCls} value={to} onChange={(e) => setTo(e.target.value)} /></Field>
            </div>
          </Card>

          {summary ? (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
              <Stat label="Gross sales" value={money(summary.gross_sales as string)} hint="Before discount, incl. GST" />
              <Stat label="Discounts" value={money(summary.discounts as string)} />
              <Stat label="Net sales" value={money(summary.net_sales as string)} hint={`${summary.bills} bills, incl. GST & round-off`} tone="emerald" />
              <Stat label="Taxable value" value={money(summary.taxable_value as string)} />
              <Stat label="GST in sales" value={money(summary.tax as string)} />
              <Stat label="Returns" value={money(summary.returns_value as string)} hint={`Net after returns ${money(summary.net_after_returns as string)}`} />
              <Stat label="Collections" value={money(summary.collections_total as string)} hint="Money received, all methods" tone="sky" />
              <Stat label="Credit given" value={money(summary.credit_given as string)} tone="amber" />
              <Stat label="Voided bills" value={String(summary.voided_bills)} hint={money(summary.voided_value as string)} />
              <Stat label="Cost of goods" value={money(summary.cost_of_goods_sold as string)} />
              <Stat label="Gross margin" value={money(summary.gross_margin as string)} hint="Batch avg cost; excl. GST — indicative" />
              <Stat label="Average bill" value={money(summary.average_bill as string)} />
            </div>
          ) : <Spinner />}

          <Card title="Detail" actions={can("reports.export") && (
            <a className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline" href={salesApi.reportCsvUrl(tab, from, to)}><Download className="w-3 h-3" /> Export CSV</a>
          )}>
            <div className="flex gap-1 overflow-x-auto mb-3">
              {[...REPORTS.map((r) => [r.key, r.label]), ["payments", "Payment methods"], ["tax", "Tax summary"], ["returns", "Returns & voids"]].map(([k, l]) => (
                <button key={k} onClick={() => setTab(k)} className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap border ${tab === k ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30" : "border-transparent text-slate-400 hover:bg-slate-800"}`}>{l}</button>
              ))}
            </div>
            {def && (rows === null ? <Spinner /> : (
              <Table head={def.cols.map((c) => c[1])} empty={rows.length === 0 && "No data for this period."}>
                {rows.map((row, i) => (
                  <tr key={i}>{def.cols.map(([k]) => <td key={k} className={/₹|Qty|Bills|Disc %/.test(def.cols.find((c) => c[0] === k)![1]) ? tdNum : td}>{cell(k, row[k])}</td>)}</tr>
                ))}
              </Table>
            ))}
            {tab === "payments" && extra && (
              <Table head={["Method", "At sale ₹", "Collections ₹", "Received ₹", "Refunds ₹", "Net ₹", "Count"]}>
                {Object.entries((extra.by_method || {}) as Record<string, Row>).map(([m, v]) => (
                  <tr key={m}><td className={td}>{m}</td>{["at_sale", "collections", "received", "refunds", "net"].map((k) => <td key={k} className={tdNum}>{money(v[k] as string)}</td>)}<td className={tdNum}>{String(v.count)}</td></tr>
                ))}
              </Table>
            )}
            {tab === "tax" && extra && (
              <>
                {(["sales", "returns"] as const).map((section) => (
                  <div key={section} className="mb-4">
                    <div className="text-xs text-slate-400 mb-1">{section === "sales" ? "Outward supplies (sales)" : "Credit notes (returns)"}</div>
                    <Table head={["GST rate", "Taxable ₹", "CGST ₹", "SGST ₹", "IGST ₹", "Total tax ₹", "Value incl. tax ₹"]} empty={((extra[section] as Row[]) || []).length === 0 && "None"}>
                      {((extra[section] as Row[]) || []).map((r, i) => (
                        <tr key={i}><td className={td}>{num(r.tax_rate as string)}%</td>{["taxable_value", "cgst", "sgst", "igst", "total_tax", "value_incl_tax"].map((k) => <td key={k} className={tdNum}>{money(r[k] as string)}</td>)}</tr>
                      ))}
                    </Table>
                  </div>
                ))}
                <div className="text-xs text-amber-300">{String(extra.note)}</div>
              </>
            )}
            {tab === "returns" && extra && (
              <>
                <Table head={["Return", "Date", "Invoice", "Customer", "Reason", "Value ₹", "Refund ₹", "Credit ₹"]} empty={((extra.returns as Row[]) || []).length === 0 && "No returns."}>
                  {((extra.returns as Row[]) || []).map((r) => (
                    <tr key={String(r.return_id)}><td className={td}>{String(r.return_number)}</td><td className={td}>{fmtDateTime(r.created_at as string)}</td><td className={td}>{String(r.invoice_number)}</td><td className={td}>{String(r.customer_name)}</td><td className={td}>{String(r.reason)}</td><td className={tdNum}>{money(r.total_amount as string)}</td><td className={tdNum}>{money(r.refund_amount as string)}</td><td className={tdNum}>{money(r.credit_note_amount as string)}</td></tr>
                  ))}
                </Table>
                <div className="text-xs text-slate-400 mt-4 mb-1">Voided invoices</div>
                <Table head={["Invoice", "Voided", "Customer", "Reason", "Total ₹", "Refunded ₹"]} empty={((extra.voids as Row[]) || []).length === 0 && "No voids."}>
                  {((extra.voids as Row[]) || []).map((v) => (
                    <tr key={String(v.invoice_id)}><td className={td}>{String(v.invoice_number)}</td><td className={td}>{fmtDateTime(v.voided_at as string)}</td><td className={td}>{String(v.customer_name)}</td><td className={td}>{String(v.reason)}</td><td className={tdNum}>{money(v.grand_total as string)}</td><td className={tdNum}>{money(v.refunded_amount as string)}</td></tr>
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
