"use client";

/** Purchases → Import supplier bill: upload a PDF / photo of a supplier bill, then review it. Nothing is posted here. */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FileUp, ScanLine } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Card, ErrorBox, Spinner, errorText, useStaff } from "@/components/sales-ui";
import { fetchJson } from "@/lib/api";
import { uploadForm } from "@/lib/upload";

interface ImportRow {
  id: number; status: string; file_name: string; supplier_name?: string | null; invoice_number?: string | null;
  invoice_date?: string | null; grand_total?: string | null; uploaded_at: string; extraction_method?: string | null;
  grn_id?: number | null; supplier_invoice_id?: number | null;
}

const STATUS: Record<string, string> = {
  EXTRACTED: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  FAILED: "bg-rose-500/10 text-rose-300 border-rose-500/30",
  CONFIRMED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  DISCARDED: "bg-slate-800 text-slate-400 border-slate-700",
};
const STATUS_LABEL: Record<string, string> = { EXTRACTED: "Needs review", FAILED: "Enter by hand", CONFIRMED: "Imported", DISCARDED: "Discarded" };

export default function BillImportPage() {
  const router = useRouter();
  const { user, can } = useStaff();
  const [rows, setRows] = useState<ImportRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchJson<ImportRow[]>("/api/v1/purchases/bill-imports").then(setRows).catch((e) => setError(errorText(e)));
  }, []);
  useEffect(() => { if (user && can("purchases.view")) load(); }, [user, can, load]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { setError("The file is larger than 10 MB."); return; }
    if (!/\.(pdf|jpe?g|png|webp)$/i.test(file.name)) { setError("Choose a PDF, JPG/JPEG, PNG or WebP file."); return; }
    setBusy(true); setError(null);
    try {
      const f = new FormData();
      f.append("file", file);
      const rec = await uploadForm<{ id: number }>("/api/v1/purchases/bill-imports", f);
      router.push(`/purchasing/bill-import/${rec.id}`);
    } catch (e) { setError(errorText(e)); setBusy(false); }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-6xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><ScanLine className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Import supplier bill</h1>
            <p className="text-sm text-slate-400">Upload a bill; the ERP reads it and prepares the purchase entry. You review everything before anything is saved to stock or accounts.</p>
          </div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {!user ? <Spinner /> : !can("purchases.view") ? <ErrorBox message="You do not have permission to view purchases." /> : (
          <>
            {can("purchases.create") && (
              <Card title="Upload a bill">
                <label className={`block border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition ${busy ? "border-emerald-500/50 bg-emerald-500/5" : "border-slate-700 hover:border-emerald-500/60"}`}
                  onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files?.[0]); }}>
                  <input type="file" className="hidden" accept="application/pdf,image/jpeg,image/png,image/webp" disabled={busy}
                    onChange={(e) => upload(e.target.files?.[0])} aria-label="Supplier bill file" />
                  {busy ? (
                    <div className="space-y-2"><Spinner label="Reading the bill… scanned bills and photos take a few seconds per page" /></div>
                  ) : (
                    <div className="space-y-2">
                      <FileUp className="w-8 h-8 mx-auto text-emerald-400" />
                      <div className="text-sm text-slate-200 font-medium">Drop a bill here or click to choose</div>
                      <div className="text-xs text-slate-500">PDF (text or scanned), JPG/JPEG, PNG or WebP photo · up to 10 MB · read on this computer, not sent anywhere</div>
                    </div>
                  )}
                </label>
              </Card>
            )}
            <Card title="Recent bill imports">
              {!rows ? <Spinner /> : rows.length === 0 ? <p className="text-sm text-slate-500">No bills imported yet.</p> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-xs text-slate-500 border-b border-slate-800">
                      {["#", "Status", "Supplier", "Bill no.", "Bill date", "Total (₹)", "Uploaded", "Read by", ""].map((h) => <th key={h} className="py-2 pr-3">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.id} className="border-b border-slate-800/60">
                          <td className="py-2 pr-3 text-slate-500">{r.id}</td>
                          <td className="pr-3"><span className={`text-[11px] px-2 py-0.5 rounded-full border ${STATUS[r.status] || ""}`}>{STATUS_LABEL[r.status] || r.status}</span></td>
                          <td className="pr-3 text-slate-200">{r.supplier_name || "—"}</td>
                          <td className="pr-3 font-mono text-xs">{r.invoice_number || "—"}</td>
                          <td className="pr-3 text-slate-400">{r.invoice_date || "—"}</td>
                          <td className="pr-3 font-mono">{r.grand_total ?? "—"}</td>
                          <td className="pr-3 text-xs text-slate-500">{new Date(r.uploaded_at).toLocaleString("en-IN")}</td>
                          <td className="pr-3 text-xs text-slate-500">{r.extraction_method === "pdf-text" ? "PDF text" : r.extraction_method === "ocr-local" ? "OCR" : "Manual"}</td>
                          <td><a className="text-emerald-400 hover:underline text-xs" href={`/purchasing/bill-import/${r.id}`}>{r.status === "EXTRACTED" || r.status === "FAILED" ? "Review" : "Open"}</a></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </>
        )}
      </main>
    </div>
  );
}
