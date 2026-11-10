"use client";

/**
 * Review an imported supplier bill. The original document is shown beside the values read from it; every value can
 * be corrected. Confirming creates a goods receipt and a supplier bill through the normal purchasing workflow;
 * posting to stock and finalising the bill are explicit options. Nothing changes stock or accounts before that.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Plus, Save, Trash2, X } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Btn, Card, ErrorBox, Notice, Spinner, errorText, inputCls, useStaff } from "@/components/sales-ui";
import { MedicinePicker, MedicineUnitOption, loadPickedMedicine } from "@/components/medicine-picker";
import { ApiError, fetchJson } from "@/lib/api";

type Conf = "high" | "medium" | "low";
interface Line {
  source_index?: number; description?: string | null; medicine_id: number | null;
  medicine_match?: { candidates: { medicine_id: number; name: string; score: number }[] } | null;
  new_medicine: { name: string; generic_name: string; strength: string; dosage_form: string } | null;
  packaging_unit_id: number | null; new_pack: { unit_id: number | null; size: string } | null; pack_size_on_bill?: number | null;
  hsn?: string | null; batch_number: string | null; expiry_date: string | null; qty: string | null; free_qty: string | null;
  purchase_rate: string | null; discount_percent: string | null; mrp: string | null; selling_rate: string | null;
  gst_percent?: string | null; tax_rate_id: number | null; amount_on_bill?: string | null;
  confidence?: Record<string, Conf>; issues?: string[];
}
interface Review {
  supplier_id: number | null; supplier_match?: { confidence: Conf; reason: string; candidates: { supplier_id: number; name: string; score: number }[] } | null;
  new_supplier: { name: string; gstin: string; phone: string } | null;
  invoice_number: string | null; invoice_date: string | null; due_date: string | null; received_date: string | null; po_reference?: string | null;
  declared: Record<string, string | null>; lines: Line[]; discrepancy_notes: string; acknowledge_discrepancy: boolean;
}
interface Check { errors: string[]; warnings: string[]; totals: Record<string, string | null>; duplicates: { kind: string; message: string }[] }
interface Detail {
  id: number; status: string; file_name: string; content_type: string; page_count: number | null; extraction_method: string | null;
  extraction_error: string | null; grn_id: number | null; supplier_invoice_id: number | null;
  extraction: { warnings?: string[]; extraction?: { method: string; warnings: string[]; confidence: number | null } };
  review: Review; check: Check; duplicates: { kind: string; message: string }[];
}
interface Opt { id: number; name: string }

const lowCls = (c?: Conf) => (c === "low" ? "ring-1 ring-amber-500/60" : "");
const blankLine = (): Line => ({
  medicine_id: null, new_medicine: null, packaging_unit_id: null, new_pack: null, batch_number: "", expiry_date: "", qty: "",
  free_qty: "0", purchase_rate: "", discount_percent: "0", mrp: "", selling_rate: "", tax_rate_id: null,
});

export default function BillReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user, can } = useStaff();
  const [d, setD] = useState<Detail | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [suppliers, setSuppliers] = useState<Opt[]>([]);
  const [taxes, setTaxes] = useState<{ id: number; name: string; rate_percentage: string }[]>([]);
  const [units, setUnits] = useState<Opt[]>([]);
  const [medUnits, setMedUnits] = useState<Record<number, MedicineUnitOption[]>>({});
  const [postGrn, setPostGrn] = useState(false);
  const [finalize, setFinalize] = useState(false);
  const [ackWarnings, setAckWarnings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    const det = await fetchJson<Detail>(`/api/v1/purchases/bill-imports/${id}`);
    setD(det); setReview(det.review); setCheck(det.check);
    const ids = Array.from(new Set(det.review.lines.map((l) => l.medicine_id).filter(Boolean))) as number[];
    for (const mid of ids) loadPickedMedicine(mid).then((p) => setMedUnits((u) => ({ ...u, [mid]: p.units }))).catch(() => undefined);
  }, [id]);
  useEffect(() => {
    if (!user) return;
    load().catch((e) => setError(errorText(e)));
    fetchJson<Opt[]>("/api/v1/suppliers").then(setSuppliers).catch(() => undefined);
    fetchJson<{ id: number; name: string; rate_percentage: string }[]>("/api/v1/tax-rates").then(setTaxes).catch(() => undefined);
    fetchJson<Opt[]>("/api/v1/units").then(setUnits).catch(() => undefined);
  }, [user, load]);

  const editable = !!d && (d.status === "EXTRACTED" || d.status === "FAILED") && can("purchases.create");
  const setR = (patch: Partial<Review>) => setReview((r) => (r ? { ...r, ...patch } : r));
  const setLine = (i: number, patch: Partial<Line>) =>
    setReview((r) => (r ? { ...r, lines: r.lines.map((l, j) => (j === i ? { ...l, ...patch } : l)) } : r));
  const applyMedicine = (i: number, l: Line, picked: { id: number; units: MedicineUnitOption[]; default_unit_id: number } | null) => {
    if (!picked) { setLine(i, { medicine_id: null, packaging_unit_id: null, new_pack: null }); return; }
    setMedUnits((u) => ({ ...u, [picked.id]: picked.units }));
    const billPack = l.pack_size_on_bill && l.pack_size_on_bill > 1 ? l.pack_size_on_bill : null;
    const match = billPack ? picked.units.find((u) => u.factor === billPack) : null;
    setLine(i, {
      medicine_id: picked.id, new_medicine: null,
      packaging_unit_id: match ? match.unit_id : billPack ? null : picked.default_unit_id,
      new_pack: match || !billPack ? null : { unit_id: units.find((u) => u.name === "Strip")?.id ?? null, size: String(billPack) },
    });
  };

  const runCheck = async (r = review) => {
    if (!r) return;
    try { setCheck(await fetchJson<Check>(`/api/v1/purchases/bill-imports/${id}/check`, { method: "POST", body: JSON.stringify(r) })); }
    catch (e) { setError(errorText(e)); }
  };
  const save = async () => {
    if (!review) return;
    setBusy(true); setError(null);
    try {
      const det = await fetchJson<Detail>(`/api/v1/purchases/bill-imports/${id}`, { method: "PUT", body: JSON.stringify(review) });
      setCheck(det.check); setInfo("Draft saved. Nothing has been posted.");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const confirmImport = async () => {
    if (!review) return;
    const steps = ["create the goods receipt and supplier bill as drafts", postGrn && "post the goods to stock", finalize && "finalise the bill (supplier payable + accounting)"].filter(Boolean).join(", ");
    if (!window.confirm(`Confirm this bill? This will ${steps}.`)) return;
    setBusy(true); setError(null); setErrors([]);
    try {
      await fetchJson(`/api/v1/purchases/bill-imports/${id}/confirm`, {
        method: "POST", body: JSON.stringify({ review, post_grn: postGrn, finalize, acknowledge_warnings: ackWarnings }),
      });
      setInfo("Bill imported.");
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.detail === "The request could not be completed") setError("The bill has problems to fix — see the checks below.");
      else setError(errorText(e));
      await runCheck();
    } finally { setBusy(false); }
  };
  const discard = async () => {
    if (!window.confirm("Discard this bill import? The uploaded document is kept for the record.")) return;
    try { await fetchJson(`/api/v1/purchases/bill-imports/${id}/discard`, { method: "POST" }); router.push("/purchasing/bill-import"); }
    catch (e) { setError(errorText(e)); }
  };

  // The document is fetched and shown from a local blob: the site sends X-Frame-Options DENY on every response,
  // which would otherwise stop the PDF from showing in the frame.
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  useEffect(() => {
    let url: string | null = null;
    fetch(`/api/v1/purchases/bill-imports/${id}/file`, { credentials: "include" })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error("The original document could not be loaded"))))
      .then((b) => { url = URL.createObjectURL(b); setFileUrl(url); })
      .catch((e) => setError(errorText(e)));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id]);
  const method = d?.extraction?.extraction?.method || d?.extraction_method;
  const warnings = useMemo(() => [...(d?.extraction?.warnings || []), ...(d?.extraction?.extraction?.warnings || [])], [d]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 py-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <a href="/purchasing/bill-import" className="text-xs text-emerald-400 hover:underline">← Bill imports</a>
            <h1 className="text-xl font-bold text-white">Review supplier bill #{id}</h1>
            {d && <p className="text-xs text-slate-400">{d.file_name} · {d.page_count || 1} page(s) · read by {method === "pdf-text" ? "PDF text" : method === "ocr-local" ? "OCR on this computer" : "hand entry"} · {d.status === "CONFIRMED" ? "imported" : d.status === "DISCARDED" ? "discarded" : "not posted yet"}</p>}
          </div>
          {editable && (
            <div className="flex flex-wrap gap-2">
              <Btn onClick={discard}><Trash2 className="w-4 h-4" /> Discard</Btn>
              <Btn onClick={save} busy={busy}><Save className="w-4 h-4" /> Save draft</Btn>
            </div>
          )}
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {info && <Notice tone="emerald">{info}</Notice>}
        {!d || !review ? <Spinner /> : (
          <div className="grid xl:grid-cols-[minmax(380px,40%)_1fr] gap-4 items-start">
            <div className="xl:sticky xl:top-4 bg-white rounded-2xl overflow-hidden border border-slate-800 h-[50vh] xl:h-[86vh]">
              {!fileUrl ? <div className="p-6 text-sm text-slate-600">Loading the original…</div>
                : d.content_type === "application/pdf"
                ? <iframe src={fileUrl} title="Original bill" className="w-full h-full" />
                // eslint-disable-next-line @next/next/no-img-element
                : <img src={fileUrl} alt="Original bill" className="w-full h-full object-contain" />}
            </div>

            <div className="space-y-4 min-w-0">
              {d.status === "CONFIRMED" && (
                <Notice tone="emerald">
                  <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4" /> Imported as goods receipt #{d.grn_id} and supplier bill #{d.supplier_invoice_id}.
                    Continue in <a className="underline" href="/purchasing">Purchasing</a> (post the GRN / finalise the bill there if not done yet).</span>
                </Notice>
              )}
              {d.extraction_error && <Notice tone="amber">{d.extraction_error}. Enter the bill details by hand below.</Notice>}
              {warnings.length > 0 && <Notice tone="amber">{warnings.join(" · ")}</Notice>}
              {d.duplicates.length > 0 && (
                <Notice tone="amber"><span className="flex items-start gap-2"><AlertTriangle className="w-4 h-4 mt-0.5" />
                  <span>Possible duplicate: {d.duplicates.map((x) => x.message).join(" · ")}</span></span></Notice>
              )}

              <Card title="Supplier & bill">
                <div className="grid md:grid-cols-3 gap-3 text-xs">
                  <label className="md:col-span-2">
                    <span className="text-slate-400">Supplier {review.supplier_match && <ConfBadge c={review.supplier_match.confidence} text={review.supplier_match.reason} />}</span>
                    <select className={inputCls} disabled={!editable} value={review.new_supplier && !review.supplier_id ? "new" : review.supplier_id ?? ""}
                      onChange={(e) => e.target.value === "new"
                        ? setR({ supplier_id: null, new_supplier: review.new_supplier || { name: "", gstin: "", phone: "" } })
                        : setR({ supplier_id: e.target.value ? Number(e.target.value) : null, new_supplier: null })}>
                      <option value="">— choose —</option>
                      {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      <option value="new">+ New supplier…</option>
                    </select>
                  </label>
                  <Field2 label="Bill number" value={review.invoice_number} onChange={(v) => setR({ invoice_number: v })} disabled={!editable} />
                  {review.new_supplier && !review.supplier_id && (
                    <>
                      <Field2 label="New supplier name" value={review.new_supplier.name} disabled={!editable} onChange={(v) => setR({ new_supplier: { ...review.new_supplier!, name: v } })} />
                      <Field2 label="GSTIN (optional)" value={review.new_supplier.gstin} disabled={!editable} onChange={(v) => setR({ new_supplier: { ...review.new_supplier!, gstin: v.toUpperCase() } })} />
                      <Field2 label="Phone (optional)" value={review.new_supplier.phone} disabled={!editable} onChange={(v) => setR({ new_supplier: { ...review.new_supplier!, phone: v } })} />
                    </>
                  )}
                  <Field2 label="Bill date" type="date" value={review.invoice_date} onChange={(v) => setR({ invoice_date: v })} disabled={!editable} />
                  <Field2 label="Due date" type="date" value={review.due_date} onChange={(v) => setR({ due_date: v || null })} disabled={!editable} />
                  <Field2 label="Received on" type="date" value={review.received_date} onChange={(v) => setR({ received_date: v })} disabled={!editable} />
                </div>
              </Card>

              <Card title={`Items (${review.lines.length})`} actions={editable ? <Btn onClick={() => setR({ lines: [...review.lines, blankLine()] })}><Plus className="w-4 h-4" /> Add line</Btn> : undefined}>
                <div className="space-y-3">
                  {review.lines.map((l, i) => (
                    <div key={i} className="p-3 bg-slate-950/60 border border-slate-800 rounded-xl space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="text-[11px] text-slate-500">Line {i + 1}{l.description ? <> · on bill: <span className="text-slate-300 font-mono">{l.description}</span></> : null}
                          {l.pack_size_on_bill ? <> · pack {l.pack_size_on_bill}</> : null}{l.amount_on_bill ? <> · amount ₹{l.amount_on_bill}</> : null}</div>
                        {editable && <button type="button" aria-label={`Remove line ${i + 1}`} className="text-slate-500 hover:text-rose-400" onClick={() => setR({ lines: review.lines.filter((_, j) => j !== i) })}><X className="w-4 h-4" /></button>}
                      </div>
                      <div className="grid md:grid-cols-[2fr_1fr] gap-2 text-xs">
                        <div>
                          <span className="text-slate-400">Medicine {l.confidence?.description && <ConfBadge c={l.medicine_id ? "medium" : "low"} />}</span>
                          {l.new_medicine ? (
                            <div className="grid grid-cols-2 gap-1.5">
                              <input className={inputCls} placeholder="New medicine name" value={l.new_medicine.name} disabled={!editable} onChange={(e) => setLine(i, { new_medicine: { ...l.new_medicine!, name: e.target.value } })} />
                              <input className={inputCls} placeholder="Generic / salt" value={l.new_medicine.generic_name} disabled={!editable} onChange={(e) => setLine(i, { new_medicine: { ...l.new_medicine!, generic_name: e.target.value } })} />
                              <input className={inputCls} placeholder="Strength" value={l.new_medicine.strength} disabled={!editable} onChange={(e) => setLine(i, { new_medicine: { ...l.new_medicine!, strength: e.target.value } })} />
                              <input className={inputCls} placeholder="Dosage form (Tablet…)" value={l.new_medicine.dosage_form} disabled={!editable} onChange={(e) => setLine(i, { new_medicine: { ...l.new_medicine!, dosage_form: e.target.value } })} />
                              {editable && <button type="button" className="col-span-2 text-left text-[11px] text-emerald-400 hover:underline" onClick={() => setLine(i, { new_medicine: null })}>Match an existing medicine instead</button>}
                            </div>
                          ) : (
                            <>
                              {editable ? (
                                <MedicinePicker value={l.medicine_id ? String(l.medicine_id) : ""} onChange={(_mid, picked) => applyMedicine(i, l, picked)} />
                              ) : <div className="text-slate-200 py-1.5">Medicine #{l.medicine_id}</div>}
                              {editable && (
                                <div className="flex flex-wrap gap-2 mt-1">
                                  {(l.medicine_match?.candidates || []).filter((c) => c.medicine_id !== l.medicine_id && c.score >= 0.5).slice(0, 3).map((c) => (
                                    <button key={c.medicine_id} type="button" className="text-[11px] px-2 py-0.5 rounded-full border border-slate-700 text-slate-300 hover:border-emerald-500"
                                      onClick={() => loadPickedMedicine(c.medicine_id).then((p) => applyMedicine(i, l, p))}>
                                      {c.name} ({Math.round(c.score * 100)}%)
                                    </button>
                                  ))}
                                  <button type="button" className="text-[11px] text-emerald-400 hover:underline"
                                    onClick={() => setLine(i, { medicine_id: null, packaging_unit_id: null, new_medicine: { name: l.description || "", generic_name: "", strength: "", dosage_form: "" },
                                                                 new_pack: l.pack_size_on_bill && l.pack_size_on_bill > 1 ? { unit_id: units.find((u) => u.name === "Strip")?.id ?? null, size: String(l.pack_size_on_bill) } : null })}>
                                    + Create new medicine
                                  </button>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                        <div>
                          <span className="text-slate-400">Purchase unit</span>
                          {l.new_pack ? (
                            <div className="flex gap-1.5">
                              <select className={inputCls} value={l.new_pack.unit_id ?? ""} disabled={!editable} onChange={(e) => setLine(i, { new_pack: { ...l.new_pack!, unit_id: Number(e.target.value) } })}>
                                {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                              </select>
                              <input className={`${inputCls} w-20`} aria-label="Units per pack" value={l.new_pack.size} disabled={!editable} onChange={(e) => setLine(i, { new_pack: { ...l.new_pack!, size: e.target.value } })} />
                            </div>
                          ) : (
                            <select className={inputCls} disabled={!editable} value={l.packaging_unit_id ?? ""} onChange={(e) => setLine(i, { packaging_unit_id: Number(e.target.value) })}>
                              <option value="">— choose —</option>
                              {(l.medicine_id && medUnits[l.medicine_id] ? medUnits[l.medicine_id] : []).map((u) => <option key={u.unit_id} value={u.unit_id}>{u.label}</option>)}
                            </select>
                          )}
                          {l.new_pack && <div className="text-[11px] text-amber-300/80 mt-0.5">New pack size for this medicine (added on confirm)</div>}
                        </div>
                      </div>
                      <div className="grid grid-cols-3 sm:grid-cols-5 2xl:grid-cols-9 gap-1.5 text-xs">
                        <Mini label="Batch" value={l.batch_number} c={l.confidence?.batch} disabled={!editable} onChange={(v) => setLine(i, { batch_number: v.toUpperCase() })} />
                        <Mini label="Expiry" type="date" value={l.expiry_date} c={l.confidence?.expiry} disabled={!editable} onChange={(v) => setLine(i, { expiry_date: v })} />
                        <Mini label="Qty" value={l.qty} c={l.confidence?.qty} disabled={!editable} onChange={(v) => setLine(i, { qty: v })} />
                        <Mini label="Free" value={l.free_qty} c={l.confidence?.free} disabled={!editable} onChange={(v) => setLine(i, { free_qty: v })} />
                        <Mini label="Rate" value={l.purchase_rate} c={l.confidence?.rate} disabled={!editable} onChange={(v) => setLine(i, { purchase_rate: v })} />
                        <Mini label="Disc %" value={l.discount_percent} c={l.confidence?.discount} disabled={!editable} onChange={(v) => setLine(i, { discount_percent: v })} />
                        <Mini label="MRP" value={l.mrp} c={l.confidence?.mrp} disabled={!editable} onChange={(v) => setLine(i, { mrp: v, selling_rate: l.selling_rate || v })} />
                        <Mini label="Selling rate" value={l.selling_rate} disabled={!editable} onChange={(v) => setLine(i, { selling_rate: v })} />
                        <label><span className="text-slate-400">GST</span>
                          <select className={`${inputCls} ${lowCls(l.confidence?.gst)}`} disabled={!editable} value={l.tax_rate_id ?? ""} onChange={(e) => setLine(i, { tax_rate_id: e.target.value ? Number(e.target.value) : null })}>
                            <option value="">—</option>
                            {taxes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                          </select>
                        </label>
                      </div>
                      {(l.issues || []).length > 0 && <div className="text-[11px] text-amber-300">{l.issues!.join(" · ")}</div>}
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-slate-500 mt-2">Amber outlines mark values the reader was unsure of. Quantities and rates are per purchase unit (e.g. per strip).</p>
              </Card>

              <Card title="Totals" actions={<Btn onClick={() => runCheck()}>Re-check</Btn>}>
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-xs">
                  {(["taxable", "cgst", "sgst", "igst", "round_off", "grand_total"] as const).map((k) => (
                    <Mini key={k} label={{ taxable: "Taxable (bill)", cgst: "CGST", sgst: "SGST", igst: "IGST", round_off: "Round off", grand_total: "Grand total (bill)" }[k]}
                      value={review.declared[k] ?? ""} disabled={!editable} onChange={(v) => setR({ declared: { ...review.declared, [k]: v } })} />
                  ))}
                </div>
                {check && (
                  <div className="mt-3 grid sm:grid-cols-4 gap-2 text-sm">
                    <Stat label="Calculated taxable" value={check.totals.taxable} />
                    <Stat label="Calculated GST" value={check.totals.tax} />
                    <Stat label="Calculated total" value={check.totals.calculated} />
                    <Stat label="Difference vs bill" value={check.totals.difference} warn={!!check.totals.difference && Math.abs(Number(check.totals.difference)) > 1} />
                  </div>
                )}
                {editable && (
                  <div className="mt-3 grid md:grid-cols-[auto_1fr] gap-2 items-center text-xs">
                    <label className="flex items-center gap-2 text-slate-300"><input type="checkbox" className="accent-emerald-500" checked={review.acknowledge_discrepancy} onChange={(e) => setR({ acknowledge_discrepancy: e.target.checked })} /> I acknowledge a difference from the printed total</label>
                    <input className={inputCls} placeholder="Explain the difference (required to acknowledge)" value={review.discrepancy_notes} onChange={(e) => setR({ discrepancy_notes: e.target.value })} />
                  </div>
                )}
              </Card>

              {check && (check.errors.length > 0 || check.warnings.length > 0) && (
                <Card title="Checks">
                  {check.errors.length > 0 && <ul className="text-sm text-rose-300 space-y-1 list-disc pl-5">{check.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
                  {check.warnings.length > 0 && <ul className="text-sm text-amber-300 space-y-1 list-disc pl-5 mt-2">{check.warnings.map((e) => <li key={e}>{e}</li>)}</ul>}
                </Card>
              )}
              {errors.length > 0 && <ErrorBox message={errors.join(" · ")} onClose={() => setErrors([])} />}

              {editable && (
                <Card title="Confirm import">
                  <div className="space-y-2 text-sm">
                    <p className="text-xs text-slate-400">Confirming creates a goods receipt and a supplier bill as drafts. Choose whether to also:</p>
                    <label className={`flex items-center gap-2 ${can("purchases.post") ? "" : "opacity-50"}`}><input type="checkbox" className="accent-emerald-500" disabled={!can("purchases.post")} checked={postGrn} onChange={(e) => { setPostGrn(e.target.checked); if (!e.target.checked) setFinalize(false); }} /> Post the goods receipt to stock now (adds the batches)</label>
                    <label className={`flex items-center gap-2 ${can("purchases.invoice_finalize") && postGrn ? "" : "opacity-50"}`}><input type="checkbox" className="accent-emerald-500" disabled={!can("purchases.invoice_finalize") || !postGrn} checked={finalize} onChange={(e) => setFinalize(e.target.checked)} /> Finalise the supplier bill now (creates the payable and the accounting entries)</label>
                    {finalize && <label className="flex items-center gap-2"><input type="checkbox" className="accent-emerald-500" checked={ackWarnings} onChange={(e) => setAckWarnings(e.target.checked)} /> I have reviewed the warnings above</label>}
                    <div className="flex justify-end">
                      <Btn variant="primary" onClick={confirmImport} busy={busy} disabled={!!check && check.errors.length > 0}>Confirm import</Btn>
                    </div>
                    {check && check.errors.length > 0 && <p className="text-[11px] text-rose-300 text-right">Fix the problems listed above, then Re-check.</p>}
                  </div>
                </Card>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function ConfBadge({ c, text }: { c: Conf; text?: string }) {
  const cls = c === "high" ? "text-emerald-400" : c === "medium" ? "text-sky-300" : "text-amber-300";
  return <span className={`ml-1 text-[10px] ${cls}`}>{text ? `${text} · ` : ""}{c === "high" ? "sure" : c === "medium" ? "likely" : "check"}</span>;
}

function Field2({ label, value, onChange, disabled, type = "text" }: { label: string; value: string | null | undefined; onChange: (v: string) => void; disabled?: boolean; type?: string }) {
  return (
    <label><span className="text-slate-400">{label}</span>
      <input type={type} className={inputCls} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} /></label>
  );
}

function Mini({ label, value, onChange, disabled, c, type = "text" }: { label: string; value: string | null | undefined; onChange: (v: string) => void; disabled?: boolean; c?: Conf; type?: string }) {
  return (
    <label><span className="text-slate-400">{label}</span>
      <input type={type} inputMode={type === "text" ? "decimal" : undefined} className={`${inputCls} px-2 ${lowCls(c)}`} value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} /></label>
  );
}

function Stat({ label, value, warn }: { label: string; value: string | null | undefined; warn?: boolean }) {
  return (
    <div className={`p-2 rounded-lg border ${warn ? "border-amber-500/40 bg-amber-500/10" : "border-slate-800 bg-slate-950"}`}>
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`font-mono ${warn ? "text-amber-300" : "text-slate-100"}`}>{value != null ? `₹${value}` : "—"}</div>
    </div>
  );
}
