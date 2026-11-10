"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Download, Receipt, Search } from "lucide-react";
import {
  Badge, Card, ErrorBox, Field, Pager, SalesShell, Table, errorText, inputCls, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { InvoiceFilters, InvoiceListItem, fmtDateTime, fmtDate, money, salesApi } from "@/lib/sales-api";

const LIMIT = 25;

export default function SalesHistoryPage() {
  const { user, can } = useStaff();
  const [filters, setFilters] = useState<InvoiceFilters>({ q: "", date_from: "", date_to: "" });
  const [applied, setApplied] = useState<InvoiceFilters>({});
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<InvoiceListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    salesApi.invoices({ ...applied, limit: LIMIT, offset })
      .then((r) => { setRows(r.items); setTotal(r.total); setError(null); })
      .catch((e) => setError(errorText(e)))
      .finally(() => setLoading(false));
  }, [user, applied, offset]);

  // Search as you type (debounced); other filters apply immediately
  useEffect(() => {
    const t = setTimeout(() => { setApplied({ ...filters }); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [filters]);

  const set = (patch: Partial<InvoiceFilters>) => setFilters((f) => ({ ...f, ...patch }));

  return (
    <SalesShell
      user={user}
      title="Sales invoices"
      subtitle="Search, filter, reprint and follow up finalised bills"
      icon={<Receipt className="w-6 h-6" />}
      actions={can("reports.export") && (
        <a href={salesApi.invoicesCsvUrl(applied)} className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold border bg-slate-800 border-slate-700">
          <Download className="w-4 h-4" /> Export CSV
        </a>
      )}
    >
      <Card>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <div className="col-span-2">
            <Field label="Search">
              <div className="relative">
                <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
                <input className={`${inputCls} pl-9`} placeholder="Invoice no, customer, mobile, medicine" value={filters.q || ""} onChange={(e) => set({ q: e.target.value })} />
              </div>
            </Field>
          </div>
          <Field label="From"><input type="date" className={inputCls} value={filters.date_from || ""} onChange={(e) => set({ date_from: e.target.value })} /></Field>
          <Field label="To"><input type="date" className={inputCls} value={filters.date_to || ""} onChange={(e) => set({ date_to: e.target.value })} /></Field>
          <Field label="Payment status">
            <select className={inputCls} value={filters.payment_status || ""} onChange={(e) => set({ payment_status: e.target.value })}>
              <option value="">All</option><option value="PAID">Paid</option><option value="PARTIALLY_PAID">Part paid</option><option value="UNPAID">Unpaid</option>
            </select>
          </Field>
          <Field label="Method / status">
            <select className={inputCls} value={`${filters.payment_method || ""}|${filters.status || ""}`} onChange={(e) => {
              const [payment_method, status] = e.target.value.split("|");
              set({ payment_method, status });
            }}>
              <option value="|">All</option>
              {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m} value={`${m}|`}>{m}</option>)}
              <option value="|VOIDED">Voided bills</option>
            </select>
          </Field>
        </div>
        <div className="flex gap-4 mt-2 text-xs text-slate-400">
          <label className="flex items-center gap-1"><input type="checkbox" checked={!!filters.credit_only} onChange={(e) => set({ credit_only: e.target.checked })} /> Credit sales only</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={!!filters.overdue_only} onChange={(e) => set({ overdue_only: e.target.checked })} /> Overdue only</label>
        </div>
      </Card>

      <ErrorBox message={error} />

      <Card title={loading ? "Loading…" : `${total} invoice(s)`}>
        <Table head={["Invoice", "Date", "Customer", "Paid by", "Status", "Total", "Balance"]} empty={!loading && rows.length === 0 && "No invoices match these filters."}>
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-slate-800/30">
              <td className={td}><Link className="font-mono text-emerald-300 hover:underline" href={`/sales/${r.id}`}>{r.invoice_number}</Link></td>
              <td className={`${td} text-slate-400 whitespace-nowrap`}>{fmtDateTime(r.invoice_date)}</td>
              <td className={td}>
                <div className="text-white">{r.customer_name}</div>
                {r.customer_mobile && <div className="text-[11px] text-slate-500">+{r.customer_mobile}</div>}
              </td>
              <td className={`${td} text-xs text-slate-400`}>{r.payment_methods || "—"}</td>
              <td className={td}>
                <div className="flex flex-wrap gap-1">
                  {r.status === "VOIDED" ? <Badge value="VOIDED" /> : <Badge value={r.payment_status} />}
                  {r.return_status !== "NONE" && <Badge value={r.return_status} />}
                  {r.overdue_days > 0 && <Badge value="OVERDUE" label={`${r.overdue_days}d overdue`} />}
                </div>
                {r.due_date && r.balance_due !== "0.00" && <div className="text-[11px] text-slate-500 mt-0.5">Due {fmtDate(r.due_date)}</div>}
              </td>
              <td className={tdNum}>{money(r.grand_total)}</td>
              <td className={`${tdNum} ${Number(r.balance_due) > 0 ? "text-amber-300" : "text-slate-500"}`}>{money(r.balance_due)}</td>
            </tr>
          ))}
        </Table>
        <Pager total={total} limit={LIMIT} offset={offset} onChange={setOffset} />
      </Card>
    </SalesShell>
  );
}
