"use client";

/**
 * Shared printable payment document (customer receipt / supplier voucher).
 * ?layout=thermal (80 mm) or ?layout=a4, &auto=1 opens the print dialog.
 * Paper is always white with black text, whatever theme the screen uses.
 */
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { PharmacyHeader, num } from "@/lib/sales-api";
import { errorText } from "@/components/sales-ui";
import { printResetVars } from "@/lib/theme";

export const PAPER = { ...printResetVars(), background: "#ffffff", color: "#000000" } as React.CSSProperties;

export interface DocView {
  title: string;
  number: string;
  dateText: string;
  status: string;
  isReversed: boolean;
  pharmacy: PharmacyHeader;
  partyLabel: string;
  partyName: string;
  partySub?: string;
  meta: [string, string][];
  allocations: { label: string; sub?: string; amount: string }[];
  totals: [string, string, boolean?][];
  backHref: string;
}

const r = (v: string | number) => num(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function DocPrintView({ load }: { load: () => Promise<DocView> }) {
  const search = useSearchParams();
  const layout = search.get("layout") === "a4" ? "a4" : "thermal";
  const [doc, setDoc] = useState<DocView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        setDoc(await load());
        if (search.get("auto") === "1") setTimeout(() => window.print(), 400);
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, [load, search]);

  if (error) return <div className="print-doc p-6 text-red-700 bg-white min-h-screen" style={PAPER}>{error}</div>;
  if (!doc) return <div className="print-doc p-6 text-slate-600 bg-white min-h-screen" style={PAPER}>Preparing document…</div>;

  const ph = doc.pharmacy;
  const address = [ph.address_line1, ph.city, ph.pincode].filter(Boolean).join(", ");
  const style = (
    <style>{`
      @page { size: ${layout === "a4" ? "A4" : "80mm auto"}; margin: ${layout === "a4" ? "12mm" : "3mm"}; }
      @media print { .no-print { display: none !important; } body { background: #fff !important; } }
      .inv { color: #000; background: #fff; font-family: ${layout === "a4" ? "Arial, Helvetica, sans-serif" : "'Courier New', monospace"}; }
      .inv table { border-collapse: collapse; width: 100%; }
    `}</style>
  );
  const base = typeof window !== "undefined" ? window.location.pathname : "";
  const toolbar = (
    <div className="no-print flex gap-2 justify-center p-3 bg-slate-100 border-b">
      <button onClick={() => window.print()} className="px-4 py-1.5 rounded bg-emerald-600 text-white text-sm font-semibold">Print</button>
      <a href={`${base}?layout=${layout === "a4" ? "thermal" : "a4"}`} className="px-4 py-1.5 rounded bg-slate-300 text-sm">Switch to {layout === "a4" ? "receipt" : "A4"}</a>
      <a href={doc.backHref} className="px-4 py-1.5 rounded bg-slate-300 text-sm">Back</a>
    </div>
  );
  const reversed = doc.isReversed && (
    <div className="font-bold text-center border border-black py-0.5 my-1">REVERSED — NOT A VALID {doc.title.toUpperCase()}</div>
  );

  if (layout === "thermal") {
    return (
      <div className="print-doc min-h-screen bg-white" style={PAPER}>
        {style}
        {toolbar}
        <div className="inv mx-auto p-2 text-[11px] leading-tight" style={{ width: "76mm" }}>
          <div className="text-center">
            <div className="font-bold text-[14px]">{ph.name}</div>
            {address && <div>{address}</div>}
            {ph.phone && <div>Ph: {ph.phone}</div>}
            {ph.gstin && <div>GSTIN: {ph.gstin}</div>}
            {ph.drug_license_number && <div>DL: {ph.drug_license_number}</div>}
            <div className="font-bold mt-1">{doc.title.toUpperCase()}</div>
          </div>
          {reversed}
          <div className="border-t border-dashed border-black my-1" />
          <div>No: {doc.number}</div>
          <div>Date: {doc.dateText}</div>
          <div>{doc.partyLabel}: {doc.partyName}</div>
          {doc.partySub && <div>{doc.partySub}</div>}
          {doc.meta.map(([k, v]) => <div key={k}>{k}: {v}</div>)}
          <div className="border-t border-dashed border-black my-1" />
          {doc.allocations.length === 0
            ? <div>No invoice allocation — amount held as advance</div>
            : doc.allocations.map((a, i) => (
              <div key={i} className="flex justify-between"><span>{a.label}{a.sub ? ` (${a.sub})` : ""}</span><span>{r(a.amount)}</span></div>
            ))}
          <div className="border-t border-dashed border-black my-1" />
          {doc.totals.map(([l, v, bold]) => (
            <div key={l} className={`flex justify-between ${bold ? "font-bold text-[13px]" : ""}`}><span>{l}</span><span>{r(v)}</span></div>
          ))}
          <div className="text-center text-[9px] mt-2">This is a payment {doc.title.toLowerCase().includes("receipt") ? "receipt" : "voucher"}, not a tax invoice. Computer generated.</div>
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
          <div>
            <div className="text-xl font-bold">{ph.name}</div>
            {ph.legal_name && ph.legal_name !== ph.name && <div>{ph.legal_name}</div>}
            {address && <div>{address}</div>}
            <div>{[ph.phone && `Phone: ${ph.phone}`, ph.email].filter(Boolean).join(" · ")}</div>
            {ph.gstin && <div>GSTIN: {ph.gstin}</div>}
            {ph.drug_license_number && <div>Drug Licence: {ph.drug_license_number}</div>}
          </div>
          <div className="text-right">
            <div className="text-lg font-bold">{doc.title.toUpperCase()}</div>
            <div>No: <b>{doc.number}</b></div>
            <div>Date: {doc.dateText}</div>
            <div>Status: {doc.status}</div>
          </div>
        </div>
        {reversed}
        <div className="grid grid-cols-2 gap-4 py-2 border-b border-black">
          <div>
            <div className="font-bold">{doc.partyLabel}</div>
            <div>{doc.partyName}</div>
            {doc.partySub && <div>{doc.partySub}</div>}
          </div>
          <div className="text-right">
            {doc.meta.map(([k, v]) => <div key={k}>{k}: {v}</div>)}
          </div>
        </div>
        <table className="mt-2">
          <thead>
            <tr className="border-b border-black text-left">
              <th className="py-1 pr-1">#</th><th className="py-1 pr-1">Document</th><th className="py-1 pr-1">Details</th>
              <th className="py-1 text-right">Amount Rs.</th>
            </tr>
          </thead>
          <tbody>
            {doc.allocations.length === 0 ? (
              <tr className="border-b border-slate-300"><td className="py-1" colSpan={4}>No invoice allocation — amount held as advance</td></tr>
            ) : doc.allocations.map((a, i) => (
              <tr key={i} className="border-b border-slate-300">
                <td className="py-1 pr-1">{i + 1}</td>
                <td className="py-1 pr-1">{a.label}</td>
                <td className="py-1 pr-1">{a.sub || ""}</td>
                <td className="py-1 text-right">{r(a.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex justify-end mt-3">
          <table style={{ width: "80mm" }}>
            <tbody>
              {doc.totals.map(([l, v, bold]) => (
                <tr key={l} className={bold ? "border-t border-black font-bold text-[14px]" : ""}>
                  <td className="py-0.5">{l}</td><td className="text-right py-0.5">{r(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-6 flex justify-between items-end text-[11px]">
          <div>Computer generated. This is a payment {doc.title.toLowerCase().includes("receipt") ? "receipt" : "voucher"}, not a tax invoice.</div>
          <div className="text-center">For {ph.name}<br /><br />Authorised signatory</div>
        </div>
      </div>
    </div>
  );
}

export function PaymentDocPrintPage({ load }: { load: () => Promise<DocView> }) {
  return (
    <Suspense fallback={<div className="print-doc p-6 bg-white min-h-screen" style={PAPER}>Loading…</div>}>
      <DocPrintView load={load} />
    </Suspense>
  );
}
