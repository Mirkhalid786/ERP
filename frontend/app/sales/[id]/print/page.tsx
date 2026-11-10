"use client";

/**
 * Printable invoice: ?layout=thermal (80 mm receipt) or ?layout=a4, &auto=1 opens the print dialog.
 * Opening this page records one print (ORIGINAL first, DUPLICATE after). It never changes stock or money.
 */
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Invoice, PrintRecord, fmtDate, num, salesApi } from "@/lib/sales-api";
import { errorText } from "@/components/sales-ui";
import { assetUrl, useBranding } from "@/lib/branding";
import { printResetVars } from "@/lib/theme";

// Paper is always white with black text, whatever theme the screen uses
const PAPER = { ...printResetVars(), background: "#ffffff", color: "#000000" } as React.CSSProperties;

const r = (v: string | number) => num(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function statusLabel(inv: Invoice) {
  if (inv.status === "VOIDED") return "VOIDED / CANCELLED";
  const pay = { PAID: "PAID", PARTIALLY_PAID: "PARTIALLY PAID", UNPAID: "CREDIT SALE - UNPAID" }[inv.payment_status] || inv.payment_status;
  const ret = inv.return_status === "FULL" ? " · REFUNDED / RETURNED" : inv.return_status === "PARTIAL" ? " · PARTLY RETURNED" : "";
  return pay + ret;
}

function PrintView() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const layout = search.get("layout") === "a4" ? "a4" : "thermal";
  const [inv, setInv] = useState<Invoice | null>(null);
  const [rec, setRec] = useState<PrintRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const { brand } = useBranding();
  const logo = brand.text_only ? null : assetUrl("logo", brand.assets.logo);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const [invoice, record] = await Promise.all([salesApi.invoice(id), salesApi.recordPrint(id, layout)]);
        setInv(invoice);
        setRec(record);
        if (search.get("auto") === "1") setTimeout(() => window.print(), 400);
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, [id, layout, search]);

  if (error) return <div className="print-doc p-6 text-red-700 bg-white min-h-screen" style={PAPER}>{error}</div>;
  if (!inv || !rec) return <div className="print-doc p-6 text-slate-600 bg-white min-h-screen" style={PAPER}>Preparing invoice…</div>;

  const ph = rec.pharmacy;
  const when = new Date(inv.invoice_date).toLocaleString("en-IN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  const address = [ph.address_line1, ph.city, ph.pincode].filter(Boolean).join(", ");
  const taxes = inv.is_inter_state ? [["IGST", inv.igst_total]] : [["CGST", inv.cgst_total], ["SGST", inv.sgst_total]];
  const payments = inv.payments.filter((p) => p.status !== "REVERSED");

  const style = (
    <style>{`
      @page { size: ${layout === "a4" ? "A4" : "80mm auto"}; margin: ${layout === "a4" ? "12mm" : "3mm"}; }
      @media print { .no-print { display: none !important; } body { background: #fff !important; } }
      .inv { color: #000; background: #fff; font-family: ${layout === "a4" ? "Arial, Helvetica, sans-serif" : "'Courier New', monospace"}; }
      .inv table { border-collapse: collapse; width: 100%; }
    `}</style>
  );

  const toolbar = (
    <div className="no-print flex gap-2 justify-center p-3 bg-slate-100 border-b">
      <button onClick={() => window.print()} className="px-4 py-1.5 rounded bg-emerald-600 text-white text-sm font-semibold">Print</button>
      <a href={`/sales/${inv.id}/print?layout=${layout === "a4" ? "thermal" : "a4"}`} className="px-4 py-1.5 rounded bg-slate-300 text-sm">Switch to {layout === "a4" ? "receipt" : "A4"}</a>
      <a href={`/sales/${inv.id}`} className="px-4 py-1.5 rounded bg-slate-300 text-sm">Back</a>
    </div>
  );

  if (layout === "thermal") {
    return (
      <div className="print-doc min-h-screen bg-white" style={PAPER}>
        {style}
        {toolbar}
        <div className="inv mx-auto p-2 text-[11px] leading-tight" style={{ width: "76mm" }}>
          <div className="text-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {logo && <img src={logo} alt="" className="mx-auto mb-1 object-contain" style={{ maxHeight: "14mm", maxWidth: "50mm", filter: "grayscale(1)" }} />}
            <div className="font-bold text-[14px]">{ph.name}</div>
            {address && <div>{address}</div>}
            {ph.phone && <div>Ph: {ph.phone}</div>}
            {ph.gstin && <div>GSTIN: {ph.gstin}</div>}
            {ph.drug_license_number && <div>DL: {ph.drug_license_number}</div>}
            <div className="font-bold mt-1">TAX INVOICE · {rec.copy_label}</div>
          </div>
          <div className="border-t border-dashed border-black my-1" />
          <div>Bill: {inv.invoice_number}</div>
          <div>Date: {when}</div>
          <div>Customer: {inv.customer_name}{inv.customer_mobile ? ` (${inv.customer_mobile.slice(-10)})` : ""}</div>
          {inv.cashier_name && <div>Cashier: {inv.cashier_name}</div>}
          {inv.has_prescription && <div>Rx: recorded</div>}
          <div className="border-t border-dashed border-black my-1" />
          {inv.items.map((it) => (
            <div key={it.id} className="mb-1">
              <div className="font-bold">{it.medicine_name}</div>
              <div className="flex justify-between">
                <span>{num(it.quantity)} {it.unit_name} x {r(it.selling_rate)}</span>
                <span>{r(it.line_total)}</span>
              </div>
              <div className="text-[10px]">B:{it.batch_number} Exp:{fmtDate(it.expiry_date)} MRP:{r(it.mrp)} GST:{num(it.tax_rate)}%{num(it.discount_amount) ? ` Disc:${r(it.discount_amount)}` : ""}</div>
            </div>
          ))}
          <div className="border-t border-dashed border-black my-1" />
          {[["Gross", inv.gross_amount], ["Discount", -num(inv.discount_total)], ["Taxable", inv.taxable_total], ...taxes, ["Round off", inv.round_off]].map(([l, v]) => (
            <div key={l} className="flex justify-between"><span>{l}</span><span>{r(v)}</span></div>
          ))}
          <div className="flex justify-between font-bold text-[13px] border-t border-black mt-1 pt-1"><span>TOTAL Rs.</span><span>{r(inv.grand_total)}</span></div>
          {payments.map((p) => (
            <div key={p.id} className="flex justify-between"><span>{p.method}{num(p.change_returned) ? ` (given ${r(p.amount_tendered || 0)}, change ${r(p.change_returned)})` : ""}</span><span>{r(p.allocated_to_invoice)}</span></div>
          ))}
          {num(inv.balance_due) > 0 && <div className="flex justify-between font-bold"><span>BALANCE DUE{inv.due_date ? ` by ${fmtDate(inv.due_date)}` : ""}</span><span>{r(inv.balance_due)}</span></div>}
          <div className="text-center font-bold mt-1">{statusLabel(inv)}</div>
          <div className="border-t border-dashed border-black my-1" />
          <div className="text-center whitespace-pre-line">{rec.receipt_footer || rec.footer_note}</div>
          <div className="text-center text-[9px] mt-1">Prices include GST as per MRP. Printed {rec.print_count}x.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="print-doc min-h-screen bg-white" style={PAPER}>
      {style}
      {toolbar}
      <div className="inv mx-auto p-8 text-[12px]" style={{ maxWidth: "190mm" }}>
        <div className="flex justify-between items-start border-b-2 border-black pb-2">
          <div className="flex items-start gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {logo && <img src={logo} alt="" className="object-contain" style={{ maxHeight: "20mm", maxWidth: "45mm" }} />}
          <div>
            <div className="text-xl font-bold">{ph.name}</div>
            {ph.legal_name && ph.legal_name !== ph.name && <div>{ph.legal_name}</div>}
            {address && <div>{address}</div>}
            <div>{[ph.phone && `Phone: ${ph.phone}`, ph.email].filter(Boolean).join(" · ")}</div>
            {ph.gstin && <div>GSTIN: {ph.gstin}</div>}
            {ph.drug_license_number && <div>Drug Licence: {ph.drug_license_number}</div>}
            {rec.website && <div>{rec.website}</div>}
          </div>
          </div>
          <div className="text-right">
            <div className="text-lg font-bold">TAX INVOICE</div>
            <div className="font-bold">{rec.copy_label}</div>
            <div>No: <b>{inv.invoice_number}</b></div>
            <div>Date: {when}</div>
            <div className="font-bold mt-1">{statusLabel(inv)}</div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4 py-2 border-b border-black">
          <div>
            <div className="font-bold">Bill to</div>
            <div>{inv.customer_name}</div>
            {inv.customer_mobile && <div>Mobile: {inv.customer_mobile.slice(-10)}</div>}
            {inv.customer_gstin && <div>GSTIN: {inv.customer_gstin}</div>}
          </div>
          <div className="text-right">
            {inv.cashier_name && <div>Billed by: {inv.cashier_name}</div>}
            {inv.due_date && <div>Due date: {fmtDate(inv.due_date)}</div>}
            {inv.has_prescription && <div>Prescription: recorded</div>}
            <div>Place of supply: {inv.is_inter_state ? "Inter-state" : "Intra-state"}</div>
          </div>
        </div>
        <table className="mt-2">
          <thead>
            <tr className="border-b border-black text-left">
              {["#", "Medicine", "HSN", "Batch", "Exp", "Qty", "MRP", "Rate", "Disc", "Taxable", "GST", "Amount"].map((h) => <th key={h} className="py-1 pr-1">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {inv.items.map((it, i) => (
              <tr key={it.id} className="border-b border-slate-300">
                <td className="py-1 pr-1">{i + 1}</td>
                <td className="pr-1">{it.medicine_name}</td>
                <td className="pr-1">{it.hsn_code}</td>
                <td className="pr-1">{it.batch_number}</td>
                <td className="pr-1">{fmtDate(it.expiry_date)}</td>
                <td className="pr-1 text-right">{num(it.quantity)} {it.unit_name}</td>
                <td className="pr-1 text-right">{r(it.mrp)}</td>
                <td className="pr-1 text-right">{r(it.selling_rate)}</td>
                <td className="pr-1 text-right">{r(it.discount_amount)}</td>
                <td className="pr-1 text-right">{r(it.taxable_amount)}</td>
                <td className="pr-1 text-right">{num(it.tax_rate)}% {r(it.tax_amount)}</td>
                <td className="text-right">{r(it.line_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex justify-between mt-3 gap-6">
          <div className="text-[11px] flex-1">
            <div className="font-bold">Payments</div>
            {payments.length === 0 ? <div>None — credit sale</div> : payments.map((p) => (
              <div key={p.id}>{p.method} Rs. {r(p.allocated_to_invoice)}{p.reference ? ` (ref ${p.reference})` : ""} — {p.receipt_number}</div>
            ))}
            {inv.returns.map((rt) => <div key={rt.id}>Return {rt.return_number}: Rs. {r(rt.total_amount)}</div>)}
            {inv.status === "VOIDED" && <div className="font-bold mt-1">Voided: {inv.void_reason}</div>}
          </div>
          <table style={{ width: "70mm" }}>
            <tbody>
              {[["Gross (incl. GST)", inv.gross_amount], ["Discount", -num(inv.discount_total)], ["Taxable value", inv.taxable_total], ...taxes, ["Round off", inv.round_off]].map(([l, v]) => (
                <tr key={l}><td>{l}</td><td className="text-right">{r(v)}</td></tr>
              ))}
              <tr className="border-t border-black font-bold text-[14px]"><td>Grand total Rs.</td><td className="text-right">{r(inv.grand_total)}</td></tr>
              <tr><td>Paid</td><td className="text-right">{r(inv.amount_paid)}</td></tr>
              {num(inv.returned_value) > 0 && <tr><td>Returned</td><td className="text-right">{r(inv.returned_value)}</td></tr>}
              <tr className="font-bold"><td>Balance due</td><td className="text-right">{r(inv.balance_due)}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="mt-6 flex justify-between items-end text-[11px]">
          <div><span className="whitespace-pre-line">{rec.footer_note}</span><br />Computer generated invoice. Prices include GST as per MRP.</div>
          <div className="text-center">For {ph.name}<br /><br />Authorised signatory</div>
        </div>
      </div>
    </div>
  );
}

export default function PrintPage() {
  return (
    <Suspense fallback={<div className="print-doc p-6 bg-white min-h-screen" style={PAPER}>Loading…</div>}>
      <PrintView />
    </Suspense>
  );
}
