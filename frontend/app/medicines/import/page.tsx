"use client";

/**
 * Medicines → Import catalogue (medicines.manage). Upload a CSV/Excel file (or use the bundled starter list),
 * check the column mapping and the preview, then import. The import is all-or-nothing and creates no stock.
 */
import { useState } from "react";
import { Download, FileSpreadsheet, Upload } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Btn, Card, ErrorBox, Notice, Spinner, errorText, inputCls, useStaff } from "@/components/sales-ui";
import { ApiError } from "@/lib/api";

interface RowResult { row: number; action: string; values: Record<string, string | boolean | null>; errors: string[]; warnings: string[] }
interface Preview {
  file_name: string; headers: string[]; mapping: Record<string, string>; fields: Record<string, string>; total_rows: number;
  summary: { create: number; update: number; skip: number; error: number }; rows: RowResult[]; problems: RowResult[];
}

const ACTION_STYLE: Record<string, string> = {
  create: "text-emerald-300", update: "text-sky-300", skip: "text-slate-400", error: "text-rose-300",
};

async function post<T>(path: string, form: FormData): Promise<T> {
  const csrf = document.cookie.split("; ").find((c) => c.startsWith("pharmacy_csrf="))?.split("=")[1];
  const res = await fetch(path, { method: "POST", body: form, credentials: "include", headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {} });
  if (!res.ok) {
    let detail = res.statusText;
    try { const d = await res.json(); detail = typeof d.detail === "string" ? d.detail : "The request was not valid"; } catch { /* status text */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export default function CatalogueImportPage() {
  const { user, can } = useStaff();
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<"upload" | "starter">("upload");
  const [mapping, setMapping] = useState<Record<string, string> | null>(null);
  const [onDuplicate, setOnDuplicate] = useState<"skip" | "update">("skip");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const form = (m: Record<string, string> | null) => {
    const f = new FormData();
    f.append("source", source);
    if (source === "upload" && file) f.append("file", file);
    if (m) f.append("mapping", JSON.stringify(m));
    f.append("on_duplicate", onDuplicate);
    return f;
  };

  const runPreview = async (m: Record<string, string> | null = mapping) => {
    setBusy(true); setError(null); setDone(null);
    try {
      const p = await post<Preview>("/api/v1/medicines/import/preview", form(m));
      setPreview(p);
      setMapping(p.mapping);
    } catch (e) { setError(errorText(e)); setPreview(null); } finally { setBusy(false); }
  };

  const runImport = async () => {
    if (!preview) return;
    const s = preview.summary;
    if (!confirm(`Import ${s.create} new and update ${s.update} existing medicine(s)? ${s.error} row(s) with errors and ${s.skip} duplicate(s) will be skipped. No stock is created.`)) return;
    setBusy(true); setError(null);
    try {
      const r = await post<{ summary: Preview["summary"]; created_masters: { manufacturers: number; categories: number } }>("/api/v1/medicines/import/commit", form(mapping));
      setDone(`Imported: ${r.summary.create} created, ${r.summary.update} updated, ${r.summary.skip} skipped, ${r.summary.error} failed. ` +
        `New manufacturers: ${r.created_masters.manufacturers}, new categories: ${r.created_masters.categories}.`);
      setPreview(null);
    } catch (e) { setError(`${errorText(e)} — nothing was imported.`); } finally { setBusy(false); }
  };

  const canRun = source === "starter" || !!file;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><FileSpreadsheet className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Import medicine catalogue</h1>
            <p className="text-sm text-slate-400">Adds product definitions only — no batches or stock. Preview first; the import is all-or-nothing.</p>
          </div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {done && <Notice tone="emerald">{done}</Notice>}
        {!user ? <Spinner /> : !can("medicines.manage") ? <ErrorBox message="You need the 'Create and edit medicines' permission to import a catalogue." /> : (
          <>
            <Card title="1. Choose the data">
              <div className="grid md:grid-cols-2 gap-4 text-sm">
                <label className={`p-4 rounded-xl border cursor-pointer ${source === "upload" ? "border-emerald-500 bg-emerald-500/5" : "border-slate-800"}`}>
                  <input type="radio" className="mr-2 accent-emerald-500" checked={source === "upload"} onChange={() => { setSource("upload"); setPreview(null); setMapping(null); }} />
                  <span className="font-semibold text-slate-100">Upload a CSV or Excel (.xlsx) file</span>
                  <p className="text-xs text-slate-400 mt-1">From your distributor, a licensed catalogue or your previous software. Up to 50,000 rows / 10 MB.</p>
                  <input type="file" accept=".csv,.xlsx,text/csv" className="mt-3 block text-xs" aria-label="Catalogue file"
                    onChange={(e) => { setFile(e.target.files?.[0] || null); setSource("upload"); setPreview(null); setMapping(null); }} />
                  <a href="/api/v1/medicines/import/template" className="mt-2 inline-flex items-center gap-1 text-xs text-emerald-400 hover:underline"><Download className="w-3.5 h-3.5" /> Template CSV</a>
                </label>
                <label className={`p-4 rounded-xl border cursor-pointer ${source === "starter" ? "border-emerald-500 bg-emerald-500/5" : "border-slate-800"}`}>
                  <input type="radio" className="mr-2 accent-emerald-500" checked={source === "starter"} onChange={() => { setSource("starter"); setPreview(null); setMapping(null); }} />
                  <span className="font-semibold text-slate-100">Starter list of common generics (318 items)</span>
                  <p className="text-xs text-slate-400 mt-1">
                    Generic names with standard strengths and forms (tablets, capsules, syrups, injections, drops, creams, inhalers, consumables).
                    No brands, prices or pack sizes; GST and schedule are left for you to set. Not a complete list of medicines in India.
                  </p>
                  <a href="/api/v1/medicines/import/starter" className="mt-2 inline-flex items-center gap-1 text-xs text-emerald-400 hover:underline"><Download className="w-3.5 h-3.5" /> Download the list</a>
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-4 mt-4">
                <label className="text-xs text-slate-400 flex items-center gap-2">If a medicine already exists:
                  <select className={`${inputCls} w-auto`} value={onDuplicate} onChange={(e) => { setOnDuplicate(e.target.value as "skip" | "update"); setPreview(null); }}>
                    <option value="skip">Skip it</option>
                    <option value="update">Fill in its blank details</option>
                  </select>
                </label>
                <Btn variant="primary" onClick={() => runPreview(source === "starter" ? null : mapping)} disabled={!canRun} busy={busy}><Upload className="w-4 h-4" /> Preview</Btn>
              </div>
            </Card>

            {preview && (
              <>
                <Card title="2. Column mapping" actions={<Btn onClick={() => runPreview(mapping)} busy={busy}>Re-check with this mapping</Btn>}>
                  <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {Object.entries(preview.fields).map(([field, label]) => (
                      <label key={field} className="text-xs text-slate-400">
                        {label}{field === "name" || field === "dosage_form" ? " *" : ""}
                        <select className={inputCls} value={mapping?.[field] || ""}
                          onChange={(e) => setMapping({ ...(mapping || {}), [field]: e.target.value })}>
                          <option value="">— not in file —</option>
                          {preview.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                        </select>
                      </label>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-500 mt-2">* Name (or generic name) is required. A missing dosage form is taken from the name when possible (e.g. “Tab”, “Syp”).</p>
                </Card>

                <Card title={`3. Preview — ${preview.total_rows} row(s) in ${preview.file_name}`}>
                  <div className="flex flex-wrap gap-3 text-sm mb-3">
                    <span className="px-3 py-1 rounded-lg bg-emerald-500/10 text-emerald-300">{preview.summary.create} new</span>
                    <span className="px-3 py-1 rounded-lg bg-sky-500/10 text-sky-300">{preview.summary.update} update</span>
                    <span className="px-3 py-1 rounded-lg bg-slate-800 text-slate-300">{preview.summary.skip} duplicate (skipped)</span>
                    <span className="px-3 py-1 rounded-lg bg-rose-500/10 text-rose-300">{preview.summary.error} with errors (skipped)</span>
                  </div>
                  <div className="overflow-x-auto max-h-[420px]">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-slate-900"><tr className="text-left text-slate-500 border-b border-slate-800">
                        {["Row", "Result", "Name", "Generic", "Strength", "Form", "Stock unit", "Pack", "GST", "Notes"].map((h) => <th key={h} className="py-2 pr-3">{h}</th>)}
                      </tr></thead>
                      <tbody>
                        {preview.rows.map((r) => (
                          <tr key={r.row} className="border-b border-slate-800/60 align-top">
                            <td className="py-1.5 pr-3 text-slate-500">{r.row}</td>
                            <td className={`pr-3 font-semibold ${ACTION_STYLE[r.action]}`}>{r.action}</td>
                            <td className="pr-3 text-slate-100">{String(r.values.name ?? "")}</td>
                            <td className="pr-3 text-slate-400">{String(r.values.generic_name ?? "")}</td>
                            <td className="pr-3 text-slate-400">{String(r.values.strength ?? "")}</td>
                            <td className="pr-3 text-slate-400">{String(r.values.dosage_form ?? "")}</td>
                            <td className="pr-3 text-slate-400">{String(r.values.base_unit ?? "")}</td>
                            <td className="pr-3 text-slate-400">{r.values.pack_unit ? `${r.values.pack_unit} of ${r.values.pack_size}` : ""}</td>
                            <td className="pr-3 text-slate-400">{r.values.gst_rate ? `${r.values.gst_rate}%` : "—"}</td>
                            <td className="pr-3">
                              {r.errors.map((e) => <div key={e} className="text-rose-300">{e}</div>)}
                              {r.warnings.map((w) => <div key={w} className="text-amber-300/80">{w}</div>)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {preview.total_rows > preview.rows.length && <p className="text-[11px] text-slate-500 mt-2">Showing the first {preview.rows.length} rows; every row is checked.</p>}
                  {preview.problems.length > 0 && (
                    <details className="mt-3 text-xs">
                      <summary className="cursor-pointer text-rose-300">All {preview.summary.error} row(s) with errors</summary>
                      <ul className="mt-2 space-y-1 max-h-60 overflow-auto">
                        {preview.problems.map((p) => <li key={p.row} className="text-slate-300">Row {p.row}: {p.errors.join("; ")}</li>)}
                      </ul>
                    </details>
                  )}
                  <div className="flex justify-end mt-4">
                    <Btn variant="primary" onClick={runImport} busy={busy} disabled={preview.summary.create + preview.summary.update === 0}>
                      Import {preview.summary.create + preview.summary.update} medicine(s)
                    </Btn>
                  </div>
                </Card>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
