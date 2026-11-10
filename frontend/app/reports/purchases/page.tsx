"use client";

/** Phase 6: purchase summary by supplier and input GST by rate (finalised supplier bills). */
import { useEffect, useState } from "react";
import { Download, Truck } from "lucide-react";
import {
  Card, ErrorBox, Field, SalesShell, Stat, Table, errorText, inputCls, isoMonthStart, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { fetchJson } from "@/lib/api";
import { money } from "@/lib/sales-api";

type Row = Record<string, string | number>;
interface Report { suppliers: Row[]; input_gst_by_rate: Row[]; totals: Row; note: string }

export default function PurchasesReportPage() {
  const { user, can } = useStaff();
  const [from, setFrom] = useState(isoMonthStart());
  const [to, setTo] = useState(isoToday());
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!user || !can("reports.view")) return;
    setData(null);
    fetchJson<Report>(`/api/v1/reports/purchases?date_from=${from}&date_to=${to}`).then(setData).catch((e) => setError(errorText(e)));
  }, [user, can, from, to]);
  return (
    <SalesShell user={user} title="Purchase report" subtitle="Finalised supplier bills by supplier, and input GST by rate" icon={<Truck className="w-6 h-6" />}>
      {user && !can("reports.view") ? <ErrorBox message="You do not have permission to view reports." /> : (
        <>
          <ErrorBox message={error} onClose={() => setError(null)} />
          <div className="flex gap-2 items-end">
            <Field label="From"><input type="date" className={`${inputCls} w-40`} value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
            <Field label="To"><input type="date" className={`${inputCls} w-40`} value={to} onChange={(e) => setTo(e.target.value)} /></Field>
            {can("reports.export") && <a className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline mb-2" href={`/api/v1/reports/purchases?date_from=${from}&date_to=${to}&format=csv`}><Download className="w-3 h-3" /> CSV</a>}
          </div>
          {data && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Stat label="Bills" value={String(data.totals.bills)} />
                <Stat label="Purchases (incl. GST)" value={money(data.totals.total)} />
                <Stat label="Paid against these bills" value={money(data.totals.paid)} />
                <Stat label="Returns (debit notes)" value={money(data.totals.returns)} />
              </div>
              <Card title="By supplier">
                <Table head={["Supplier", "Bills", "Gross", "Discount", "CGST", "SGST", "IGST", "Other", "Total", "Paid", "Returns"]} empty={data.suppliers.length === 0 && "No finalised bills in this period."}>
                  {data.suppliers.map((r) => (
                    <tr key={String(r.supplier_id)}><td className={td}>{r.supplier}</td><td className={tdNum}>{r.bills}</td>
                      {["gross", "discount", "cgst", "sgst", "igst", "other_charges", "total", "paid", "returns"].map((k) => <td key={k} className={tdNum}>{money(r[k])}</td>)}</tr>
                  ))}
                </Table>
              </Card>
              <Card title="Input GST by rate (as billed)">
                <Table head={["Rate %", "Taxable", "CGST", "SGST", "IGST", "Total tax"]} empty={data.input_gst_by_rate.length === 0 && "None."}>
                  {data.input_gst_by_rate.map((r) => (
                    <tr key={String(r.rate)}><td className={td}>{r.rate}</td>{["taxable", "cgst", "sgst", "igst", "tax"].map((k) => <td key={k} className={tdNum}>{money(r[k])}</td>)}</tr>
                  ))}
                </Table>
                <div className="text-xs text-slate-500 mt-2">{data.note}</div>
              </Card>
            </>
          )}
        </>
      )}
    </SalesShell>
  );
}
