"use client";

/**
 * POS counter billing.
 *
 * Keyboard: F2 search · ↑/↓ + Enter add · F4 customer · F8 payment · F9 or Ctrl+Enter finalise · Esc clear search.
 * Barcode scanners type the code and press Enter: an exact barcode match is added at once.
 * The cart (and its checkout key) is kept in this browser until the sale succeeds, so a
 * failed or interrupted finalisation can be retried without double-billing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NewCustomerModal } from "@/components/customer-modal";
import Link from "next/link";
import {
  AlertTriangle, CheckCircle2, FileText, Minus, Plus, Printer, Receipt, ScanBarcode, Search, Trash2,
  UserPlus, Users, Wallet,
} from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, errorText, inputCls, isoToday, useStaff,
} from "@/components/sales-ui";
import {
  Customer, Invoice, PosMedicine, PrescriptionIn, Quote, SaleLine, SalePaymentIn, Shift, fmtDate, money,
  newKey, num, salesApi,
} from "@/lib/sales-api";

interface CartLine {
  uid: string;
  medicine: PosMedicine;
  unit_id: number;
  quantity: string;
  batch_id: number | null;
  discount_percent: string;
}

interface PayRow {
  method: string;
  amount: string;
  tendered: string;
  reference: string;
}

interface Draft {
  key: string;
  lines: CartLine[];
  customer: Customer | null;
  mode: "CASH" | "UPI" | "CARD" | "SPLIT" | "CREDIT";
  cashTendered: string;
  reference: string;
  split: PayRow[];
  dueDate: string;
  overrideReason: string;
  prescription: PrescriptionIn;
  notes: string;
}

const DRAFT_KEY = "pharmacy-pos-draft-v1";
const emptyRx: PrescriptionIn = { prescription_reference: "", prescriber_name: "", patient_name: "", prescription_date: null };
const freshDraft = (): Draft => ({
  key: newKey(), lines: [], customer: null, mode: "CASH", cashTendered: "", reference: "",
  split: [{ method: "UPI", amount: "", tendered: "", reference: "" }, { method: "CASH", amount: "", tendered: "", reference: "" }],
  dueDate: "", overrideReason: "", prescription: emptyRx, notes: "",
});

function loadDraft(): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw) return { ...freshDraft(), ...JSON.parse(raw) };
  } catch {
    /* storage unavailable */
  }
  return freshDraft();
}

const unitOf = (line: CartLine) => line.medicine.units.find((u) => u.unit_id === line.unit_id) || line.medicine.units[0];

export default function PosPage() {
  const { user, can } = useStaff();
  const [draft, setDraft] = useState<Draft>(freshDraft);
  const [hydrated, setHydrated] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PosMedicine[]>([]);
  const [highlight, setHighlight] = useState(0);
  const [searching, setSearching] = useState(false);
  const [noResults, setNoResults] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const [done, setDone] = useState<Invoice | null>(null);
  const [shift, setShift] = useState<Shift | null | undefined>(undefined);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState<Customer[]>([]);
  const [newCustomerOpen, setNewCustomerOpen] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const customerRef = useRef<HTMLInputElement>(null);
  const payRef = useRef<HTMLInputElement>(null);
  const searchSeq = useRef(0);

  // ── Draft persistence ───────────────────────────────────────────────────────
  useEffect(() => {
    setDraft(loadDraft());
    setHydrated(true);
  }, []);
  useEffect(() => {
    if (!hydrated) return;
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    } catch {
      /* ignore */
    }
  }, [draft, hydrated]);
  const update = useCallback((patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch })), []);

  useEffect(() => {
    if (user) salesApi.currentShift().then((r) => setShift(r.shift)).catch(() => setShift(null));
  }, [user]);

  // ── Medicine search ─────────────────────────────────────────────────────────
  const runSearch = useCallback(async (q: string) => {
    const seq = ++searchSeq.current;
    if (q.trim().length < 2) {
      setResults([]);
      setNoResults(false);
      return [];
    }
    setSearching(true);
    try {
      // POS bills only what can actually be sold: drop anything without a
      // saleable batch even if a stale response or another source returns it.
      const rows = (await salesApi.search(q.trim())).filter((m) => m.batches.length > 0 && num(m.total_stock_base) > 0);
      if (seq === searchSeq.current) {
        setResults(rows);
        setHighlight(0);
        setNoResults(rows.length === 0);
      }
      return rows;
    } catch (e) {
      setError(errorText(e));
      return [];
    } finally {
      if (seq === searchSeq.current) setSearching(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => runSearch(query), 250);
    return () => clearTimeout(t);
  }, [query, runSearch]);

  const addMedicine = useCallback((med: PosMedicine) => {
    if (med.batches.length === 0) {
      setError(`'${med.name}' has no saleable stock (expired, quarantined or out of stock).`);
      return;
    }
    const unit = med.units.find((u) => u.is_default) || med.units[0];
    setDraft((d) => {
      const idx = d.lines.findIndex((l) => l.medicine.medicine_id === med.medicine_id && l.unit_id === unit.unit_id && !l.batch_id);
      if (idx >= 0) {
        const lines = [...d.lines];
        lines[idx] = { ...lines[idx], medicine: med, quantity: String(num(lines[idx].quantity) + 1) };
        return { ...d, lines };
      }
      return {
        ...d,
        lines: [...d.lines, { uid: newKey(), medicine: med, unit_id: unit.unit_id, quantity: "1", batch_id: null, discount_percent: "0" }],
      };
    });
    setQuery("");
    setResults([]);
    setNoResults(false);
    setError(null);
    searchRef.current?.focus();
  }, []);

  const onSearchKey = async (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.max(results.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      // Scanners press Enter immediately after the code: search now rather than wait for the debounce
      const rows = results.length && !searching ? results : await runSearch(query);
      const barcodeHit = rows.find((r) => r.matched_barcode);
      const pick = barcodeHit || rows[highlight] || rows[0];
      if (pick) addMedicine(pick);
      else if (query.trim()) setError(`No saleable stock found for "${query.trim()}"`);
    } else if (e.key === "Escape") {
      setQuery("");
      setResults([]);
      setNoResults(false);
    }
  };

  // ── Cart editing ────────────────────────────────────────────────────────────
  const setLine = (uid: string, patch: Partial<CartLine>) =>
    setDraft((d) => ({ ...d, lines: d.lines.map((l) => (l.uid === uid ? { ...l, ...patch } : l)) }));
  const removeLine = (uid: string) => setDraft((d) => ({ ...d, lines: d.lines.filter((l) => l.uid !== uid) }));
  const stepQty = (line: CartLine, delta: number) => {
    const next = Math.max(1, num(line.quantity) + delta);
    setLine(line.uid, { quantity: String(next) });
  };

  const saleLines: SaleLine[] = useMemo(
    () =>
      draft.lines
        .filter((l) => num(l.quantity) > 0)
        .map((l) => ({
          medicine_id: l.medicine.medicine_id,
          unit_id: l.unit_id,
          quantity: l.quantity,
          batch_id: l.batch_id,
          discount_percent: l.discount_percent || "0",
        })),
    [draft.lines],
  );

  // ── Live quote (same engine as finalisation) ────────────────────────────────
  useEffect(() => {
    if (!user || saleLines.length === 0) {
      setQuote(null);
      setQuoteError(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        setQuote(await salesApi.quote(saleLines, draft.customer?.id));
        setQuoteError(null);
      } catch (e) {
        setQuote(null);
        setQuoteError(errorText(e));
      }
    }, 300);
    return () => clearTimeout(t);
  }, [saleLines, draft.customer?.id, user]);

  const total = num(quote?.grand_total);
  const lineTotals = useMemo(() => {
    const map: Record<number, { total: number; batches: string[] }> = {};
    quote?.items.forEach((it) => {
      const row = (map[it.cart_line] ||= { total: 0, batches: [] });
      row.total += num(it.line_total);
      row.batches.push(`${it.batch_number} (${num(it.quantity_base)})`);
    });
    return map;
  }, [quote]);

  // ── Payments ────────────────────────────────────────────────────────────────
  const payments: SalePaymentIn[] = useMemo(() => {
    const m = draft.mode;
    if (m === "CASH") return draft.cashTendered ? [{ method: "CASH", tendered: draft.cashTendered }] : [];
    if (m === "UPI" || m === "CARD") return total > 0 ? [{ method: m, amount: total.toFixed(2), reference: draft.reference || undefined }] : [];
    // SPLIT and CREDIT (part payment) use rows
    return draft.split
      .filter((r) => num(r.method === "CASH" ? r.tendered || r.amount : r.amount) > 0)
      .map((r) =>
        r.method === "CASH"
          ? { method: "CASH", ...(r.amount ? { amount: r.amount } : {}), ...(r.tendered ? { tendered: r.tendered } : {}) }
          : { method: r.method, amount: r.amount, reference: r.reference || undefined },
      );
  }, [draft.mode, draft.cashTendered, draft.reference, draft.split, total]);

  const paidPreview = useMemo(() => {
    let nonCash = 0;
    let tendered = 0;
    payments.forEach((p) => {
      if (p.method === "CASH") tendered += num(p.tendered || p.amount);
      else nonCash += num(p.amount);
    });
    const cashApplied = Math.min(tendered, Math.max(total - nonCash, 0));
    return { paid: nonCash + cashApplied, change: Math.max(tendered - cashApplied, 0), unpaid: Math.max(total - nonCash - cashApplied, 0) };
  }, [payments, total]);

  const isCredit = paidPreview.unpaid > 0.004;
  const needsRx = !!quote?.prescription_required;

  // ── Finalise ────────────────────────────────────────────────────────────────
  const finalize = useCallback(async () => {
    if (finalizing || saleLines.length === 0) return;
    setError(null);
    if (isCredit && !draft.customer) {
      setError(`${money(paidPreview.unpaid)} is unpaid. Choose a customer for a credit sale, or collect the full amount.`);
      return;
    }
    setFinalizing(true);
    try {
      const invoice = await salesApi.finalize({
        idempotency_key: draft.key,
        customer_id: draft.customer?.id ?? null,
        items: saleLines,
        payments,
        due_date: isCredit && draft.dueDate ? draft.dueDate : null,
        credit_override_reason: draft.overrideReason || null,
        prescription: needsRx ? { ...draft.prescription, prescription_date: draft.prescription.prescription_date || null } : null,
        notes: draft.notes || null,
      });
      setDone(invoice);
      setDraft(freshDraft());
      setQuote(null);
      if (shift) salesApi.currentShift().then((r) => setShift(r.shift)).catch(() => undefined);
    } catch (e) {
      // The draft and its checkout key are kept: retrying cannot bill twice
      setError(errorText(e));
    } finally {
      setFinalizing(false);
    }
  }, [finalizing, saleLines, payments, draft, isCredit, needsRx, paidPreview.unpaid, shift]);

  // ── Shortcuts ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (done) {
        if (e.key === "Enter" || e.key === "Escape" || e.key === "F2") {
          e.preventDefault();
          setDone(null);
          setTimeout(() => searchRef.current?.focus(), 0);
        }
        return;
      }
      if (e.key === "F2") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "F4") {
        e.preventDefault();
        customerRef.current?.focus();
      } else if (e.key === "F8") {
        e.preventDefault();
        payRef.current?.focus();
      } else if (e.key === "F9" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        finalize();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [finalize, done]);

  // ── Customer search ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (customerQuery.trim().length < 2) {
      setCustomerResults([]);
      return;
    }
    const t = setTimeout(() => {
      salesApi.customers({ q: customerQuery.trim(), active_only: true, limit: 8 })
        .then((r) => setCustomerResults(r.items))
        .catch(() => setCustomerResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [customerQuery]);

  const pickCustomer = (c: Customer | null) => {
    update({ customer: c });
    setCustomerQuery("");
    setCustomerResults([]);
  };

  const clearBill = () => {
    if (draft.lines.length && !confirm("Clear this bill?")) return;
    setDraft(freshDraft());
    setError(null);
    searchRef.current?.focus();
  };

  if (user && !can("pos.access")) {
    return (
      <SalesShell user={user} title="POS">
        <ErrorBox message="You do not have access to the POS." />
      </SalesShell>
    );
  }

  return (
    <SalesShell
      user={user}
      wide
      title="Counter billing"
      subtitle="F2 search · F4 customer · F8 payment · F9 finalise"
      icon={<ScanBarcode className="w-6 h-6" />}
      actions={
        <>
          {shift === null && can("shifts.operate") && (
            <Link href="/day-end" className="text-xs text-amber-300 border border-amber-500/30 bg-amber-500/10 rounded-lg px-3 py-2">
              No cash shift open
            </Link>
          )}
          {shift && <span className="text-xs text-emerald-300 border border-emerald-500/30 bg-emerald-500/10 rounded-lg px-3 py-2">Shift open · cash {money(shift.expected_cash)}</span>}
          <Btn variant="ghost" onClick={clearBill}>New bill</Btn>
        </>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_400px] gap-4">
        {/* Left: search + cart */}
        <div className="space-y-4 min-w-0">
          <Card>
            <div className="relative">
              <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
              <input
                ref={searchRef}
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onSearchKey}
                placeholder="Scan barcode or type medicine / brand / composition / manufacturer…"
                className={`${inputCls} pl-9 py-2.5 text-base`}
                aria-label="Search medicines"
                autoComplete="off"
              />
              {(results.length > 0 || searching || noResults) && (
                <div className="absolute z-20 left-0 right-0 mt-1 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl max-h-[420px] overflow-y-auto" role="listbox">
                  {searching && results.length === 0 && <div className="p-3 text-sm text-slate-400">Searching…</div>}
                  {!searching && noResults && results.length === 0 && (
                    <div className="p-3 text-sm text-slate-400">No saleable stock found</div>
                  )}
                  {results.map((m, i) => (
                    <button
                      key={m.medicine_id}
                      role="option"
                      aria-selected={i === highlight}
                      onMouseEnter={() => setHighlight(i)}
                      onClick={() => addMedicine(m)}
                      className={`w-full text-left px-3 py-2 border-b border-slate-800 last:border-0 ${i === highlight ? "bg-emerald-500/10" : ""}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <span className="font-semibold text-white">{m.name}</span>{" "}
                          <span className="text-xs text-slate-400">{m.strength} · {m.generic_name}</span>
                          {m.manufacturer && <span className="text-xs text-slate-500"> · {m.manufacturer}</span>}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {m.requires_prescription && <Badge value="RX" label={`Rx ${m.schedule_type !== "NONE" ? m.schedule_type : ""}`} />}
                          <span className={`text-xs font-mono ${num(m.total_stock_base) > 0 ? "text-emerald-300" : "text-rose-300"}`}>
                            {num(m.total_stock_base)} {m.base_unit_name}
                          </span>
                        </div>
                      </div>
                      {m.batches[0] && (
                        <div className="text-[11px] text-slate-500 mt-0.5">
                          FEFO batch {m.batches[0].batch_number} · exp {fmtDate(m.batches[0].expiry_date)} · ₹{num(m.batches[0].selling_rate_per_base).toFixed(2)}/{m.base_unit_name}
                          {m.batches.length > 1 && ` · +${m.batches.length - 1} more batch(es)`}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </Card>

          <ErrorBox message={error} onClose={() => setError(null)} />

          <Card title={`Bill items (${draft.lines.length})`}>
            {draft.lines.length === 0 ? (
              <div className="text-center text-slate-500 py-10 text-sm">
                <ScanBarcode className="w-8 h-8 mx-auto mb-2 text-slate-600" />
                Scan or search to add medicines.
              </div>
            ) : (
              <div className="space-y-2">
                {draft.lines.map((line, index) => {
                  const unit = unitOf(line);
                  const factor = num(unit.conversion_factor);
                  const first = line.medicine.batches.find((b) => b.batch_id === line.batch_id) || line.medicine.batches[0];
                  const rate = first ? num(first.selling_rate_per_base) * factor : 0;
                  const mrp = first ? num(first.mrp_per_base) * factor : 0;
                  const allocation = lineTotals[index];
                  return (
                    <div key={line.uid} className="border border-slate-800 rounded-xl p-3 bg-slate-950/40">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="font-semibold text-white truncate">
                            {line.medicine.name}{" "}
                            {line.medicine.requires_prescription && <Badge value="RX" label="Rx" />}
                          </div>
                          <div className="text-[11px] text-slate-500">
                            MRP ₹{mrp.toFixed(2)} · Rate ₹{rate.toFixed(2)}/{unit.unit_name} · GST {num(line.medicine.tax_rate)}%
                            {allocation && ` · Batch ${allocation.batches.join(", ")}`}
                          </div>
                        </div>
                        <button onClick={() => removeLine(line.uid)} className="p-1.5 text-slate-500 hover:text-rose-300" aria-label={`Remove ${line.medicine.name}`}>
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-2 items-end">
                        <Field label="Quantity">
                          <div className="flex">
                            <button className="px-2 bg-slate-800 rounded-l-lg border border-slate-700" onClick={() => stepQty(line, -1)} aria-label="Decrease"><Minus className="w-3 h-3" /></button>
                            <input
                              value={line.quantity}
                              inputMode="decimal"
                              onChange={(e) => setLine(line.uid, { quantity: e.target.value.replace(/[^0-9.]/g, "") })}
                              className={`${inputCls} rounded-none text-center font-mono`}
                              aria-label="Quantity"
                            />
                            <button className="px-2 bg-slate-800 rounded-r-lg border border-slate-700" onClick={() => stepQty(line, 1)} aria-label="Increase"><Plus className="w-3 h-3" /></button>
                          </div>
                        </Field>
                        <Field label="Unit">
                          <select value={line.unit_id} onChange={(e) => setLine(line.uid, { unit_id: Number(e.target.value) })} className={inputCls}>
                            {line.medicine.units.map((u) => (
                              <option key={u.unit_id} value={u.unit_id}>
                                {u.unit_name}{u.is_base ? "" : ` (${num(u.conversion_factor)})`}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Batch">
                          <select value={line.batch_id ?? ""} onChange={(e) => setLine(line.uid, { batch_id: e.target.value ? Number(e.target.value) : null })} className={inputCls}>
                            <option value="">Auto (FEFO)</option>
                            {line.medicine.batches.map((b) => (
                              <option key={b.batch_id} value={b.batch_id}>
                                {b.batch_number} · exp {fmtDate(b.expiry_date)} · {num(b.stock_base)}
                              </option>
                            ))}
                          </select>
                        </Field>
                        <Field label="Disc %">
                          <input
                            value={line.discount_percent}
                            disabled={!can("sales.discount")}
                            title={can("sales.discount") ? "" : "You are not allowed to give discounts"}
                            inputMode="decimal"
                            onChange={(e) => setLine(line.uid, { discount_percent: e.target.value.replace(/[^0-9.]/g, "") })}
                            className={`${inputCls} font-mono`}
                          />
                        </Field>
                        <div className="text-right">
                          <div className="text-[11px] text-slate-500">Line total</div>
                          <div className="font-mono font-bold text-white">{allocation ? money(allocation.total) : "—"}</div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {quoteError && <div className="mt-3"><ErrorBox message={quoteError} /></div>}
          </Card>
        </div>

        {/* Right: customer, totals, payment */}
        <div className="space-y-4">
          <Card title="Customer" actions={can("customers.manage") && (
            <Btn variant="ghost" onClick={() => setNewCustomerOpen(true)}><UserPlus className="w-4 h-4" /> New</Btn>
          )}>
            {draft.customer ? (
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-semibold text-white">{draft.customer.name}</div>
                  <div className="text-xs text-slate-400">{draft.customer.mobile ? `+${draft.customer.mobile}` : "No mobile"}</div>
                  <div className="text-xs text-slate-400 mt-1">
                    Owes <span className="font-mono text-amber-300">{money(draft.customer.outstanding_balance)}</span> · credit left{" "}
                    <span className="font-mono">{money(draft.customer.available_credit)}</span>
                  </div>
                </div>
                <Btn variant="ghost" onClick={() => pickCustomer(null)}>Walk-in</Btn>
              </div>
            ) : (
              <div className="relative">
                <Users className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
                <input
                  ref={customerRef}
                  value={customerQuery}
                  onChange={(e) => setCustomerQuery(e.target.value)}
                  placeholder="Walk-in · search name or mobile (F4)"
                  className={`${inputCls} pl-9`}
                  aria-label="Search customer"
                />
                {customerResults.length > 0 && (
                  <div className="absolute z-20 left-0 right-0 mt-1 bg-slate-900 border border-slate-700 rounded-xl shadow-xl max-h-64 overflow-y-auto">
                    {customerResults.map((c) => (
                      <button key={c.id} onClick={() => pickCustomer(c)} className="w-full text-left px-3 py-2 hover:bg-slate-800 border-b border-slate-800 last:border-0">
                        <div className="text-sm text-white">{c.name}</div>
                        <div className="text-[11px] text-slate-500">{c.mobile ? `+${c.mobile}` : "—"} · owes {money(c.outstanding_balance)}</div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Card>

          <Card title="Bill summary">
            <dl className="text-sm space-y-1">
              {[
                ["Gross (incl. GST)", quote?.gross_amount],
                ["Discount", quote ? -num(quote.discount_total) : undefined],
                ["Taxable value", quote?.taxable_total],
                ...(quote?.is_inter_state ? [["IGST", quote?.igst_total]] : [["CGST", quote?.cgst_total], ["SGST", quote?.sgst_total]]),
                ["Round off", quote?.round_off],
              ].map(([label, value]) => (
                <div key={label as string} className="flex justify-between text-slate-400">
                  <dt>{label}</dt>
                  <dd className="font-mono">{value === undefined ? "—" : money(value as string)}</dd>
                </div>
              ))}
              <div className="flex justify-between text-white text-xl font-bold pt-2 border-t border-slate-800">
                <dt>Total</dt>
                <dd className="font-mono">{money(total)}</dd>
              </div>
            </dl>
          </Card>

          {needsRx && (
            <Card title="Prescription (required)">
              <Notice tone="amber">
                Needed for: {quote?.prescription_medicines.join(", ")}. Only a pharmacist can finalise this bill.
              </Notice>
              <div className="grid grid-cols-2 gap-2 mt-3">
                <Field label="Rx number / ref *"><input className={inputCls} value={draft.prescription.prescription_reference} onChange={(e) => update({ prescription: { ...draft.prescription, prescription_reference: e.target.value } })} /></Field>
                <Field label="Rx date"><input type="date" className={inputCls} value={draft.prescription.prescription_date || ""} onChange={(e) => update({ prescription: { ...draft.prescription, prescription_date: e.target.value } })} /></Field>
                <Field label="Doctor *"><input className={inputCls} value={draft.prescription.prescriber_name} onChange={(e) => update({ prescription: { ...draft.prescription, prescriber_name: e.target.value } })} /></Field>
                <Field label="Doctor reg. no."><input className={inputCls} value={draft.prescription.prescriber_registration_no || ""} onChange={(e) => update({ prescription: { ...draft.prescription, prescriber_registration_no: e.target.value } })} /></Field>
                <Field label="Patient *"><input className={inputCls} value={draft.prescription.patient_name} onChange={(e) => update({ prescription: { ...draft.prescription, patient_name: e.target.value } })} /></Field>
                <Field label="Patient address" hint="Required for H1 / X"><input className={inputCls} value={draft.prescription.patient_address || ""} onChange={(e) => update({ prescription: { ...draft.prescription, patient_address: e.target.value } })} /></Field>
                <div className="col-span-2"><Field label="Doctor address" hint="Required for H1 / X"><input className={inputCls} value={draft.prescription.prescriber_address || ""} onChange={(e) => update({ prescription: { ...draft.prescription, prescriber_address: e.target.value } })} /></Field></div>
              </div>
            </Card>
          )}

          <Card title="Payment">
            <div className="grid grid-cols-5 gap-1 mb-3" role="radiogroup" aria-label="Payment mode">
              {(["CASH", "UPI", "CARD", "SPLIT", "CREDIT"] as const).map((m) => (
                <button
                  key={m}
                  role="radio"
                  aria-checked={draft.mode === m}
                  onClick={() => update({ mode: m, ...(m === "CREDIT" ? { split: [{ method: "CASH", amount: "", tendered: "", reference: "" }] } : {}) })}
                  disabled={m === "CREDIT" && !can("sales.credit")}
                  className={`py-2 rounded-lg text-xs font-bold border ${draft.mode === m ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" : "bg-slate-950 border-slate-700 text-slate-400"} disabled:opacity-40`}
                >
                  {m === "CREDIT" ? "Udhaar" : m}
                </button>
              ))}
            </div>

            {draft.mode === "CASH" && (
              <Field label="Cash received (F8)" hint="Leave blank to sell on credit to the selected customer">
                <input ref={payRef} inputMode="decimal" className={`${inputCls} text-lg font-mono`} value={draft.cashTendered}
                  onChange={(e) => update({ cashTendered: e.target.value.replace(/[^0-9.]/g, "") })} placeholder={total ? total.toFixed(2) : "0.00"} />
                <div className="flex flex-wrap gap-1 mt-2">
                  {[["Exact", total ? total.toFixed(2) : ""], ["₹100", "100"], ["₹200", "200"], ["₹500", "500"], ["₹1000", "1000"], ["₹2000", "2000"]].map(([label, v]) => (
                    <button key={label} type="button" disabled={!v}
                      onClick={() => update({ cashTendered: v })}
                      className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 border border-slate-700 text-slate-300 hover:bg-slate-700 disabled:opacity-40">
                      {label}
                    </button>
                  ))}
                  {draft.cashTendered && <button type="button" onClick={() => update({ cashTendered: "" })} className="px-2.5 py-1 rounded-lg text-xs text-slate-500 hover:text-slate-300">Clear</button>}
                </div>
              </Field>
            )}
            {(draft.mode === "UPI" || draft.mode === "CARD") && (
              <Field label={`${draft.mode} reference / UTR`} hint="Recorded only — the app does not verify the payment with the bank">
                <input ref={payRef} className={inputCls} value={draft.reference} onChange={(e) => update({ reference: e.target.value })} />
              </Field>
            )}
            {(draft.mode === "SPLIT" || draft.mode === "CREDIT") && (
              <div className="space-y-2">
                {draft.mode === "CREDIT" && <div className="text-xs text-slate-400">Part payment now (optional); the rest goes on the customer&apos;s account.</div>}
                {draft.split.map((row, i) => (
                  <div key={i} className="grid grid-cols-[90px_1fr_1fr_auto] gap-1">
                    <select className={inputCls} value={row.method} onChange={(e) => update({ split: draft.split.map((r, j) => (j === i ? { ...r, method: e.target.value } : r)) })}>
                      {["CASH", "UPI", "CARD", "BANK_TRANSFER", "OTHER"].map((m) => <option key={m}>{m}</option>)}
                    </select>
                    <input ref={i === 0 ? payRef : undefined} inputMode="decimal" placeholder={row.method === "CASH" ? "Cash given" : "Amount"} className={`${inputCls} font-mono`}
                      value={row.method === "CASH" ? row.tendered : row.amount}
                      onChange={(e) => {
                        const v = e.target.value.replace(/[^0-9.]/g, "");
                        update({ split: draft.split.map((r, j) => (j === i ? (r.method === "CASH" ? { ...r, tendered: v, amount: "" } : { ...r, amount: v }) : r)) });
                      }} />
                    <input placeholder={row.method === "CASH" ? "" : "Ref / UTR"} disabled={row.method === "CASH"} className={inputCls} value={row.reference}
                      onChange={(e) => update({ split: draft.split.map((r, j) => (j === i ? { ...r, reference: e.target.value } : r)) })} />
                    <button className="text-slate-500 hover:text-rose-300 px-1" onClick={() => update({ split: draft.split.filter((_, j) => j !== i) })} aria-label="Remove payment"><Trash2 className="w-4 h-4" /></button>
                  </div>
                ))}
                <Btn variant="ghost" onClick={() => update({ split: [...draft.split, { method: "UPI", amount: "", tendered: "", reference: "" }] })}><Plus className="w-4 h-4" /> Add payment</Btn>
              </div>
            )}

            <div className="mt-3 grid grid-cols-3 gap-2 text-center">
              <div className="bg-slate-950 rounded-lg p-2"><div className="text-[10px] text-slate-500">Paid</div><div className="font-mono text-sm">{money(paidPreview.paid)}</div></div>
              <div className="bg-slate-950 rounded-lg p-2"><div className="text-[10px] text-slate-500">Change</div><div className="font-mono text-sm text-emerald-300">{money(paidPreview.change)}</div></div>
              <div className="bg-slate-950 rounded-lg p-2"><div className="text-[10px] text-slate-500">On credit</div><div className={`font-mono text-sm ${isCredit ? "text-amber-300" : ""}`}>{money(paidPreview.unpaid)}</div></div>
            </div>

            {isCredit && total > 0 && (
              <div className="mt-3 space-y-2">
                {!draft.customer ? (
                  <Notice tone="amber"><AlertTriangle className="w-4 h-4 inline mr-1" />Choose a customer to put {money(paidPreview.unpaid)} on credit.</Notice>
                ) : (
                  <>
                    <Field label="Due date" hint={`Default: ${draft.customer.payment_terms_days} days`}>
                      <input type="date" min={isoToday()} className={inputCls} value={draft.dueDate} onChange={(e) => update({ dueDate: e.target.value })} />
                    </Field>
                    {num(draft.customer.outstanding_balance) + paidPreview.unpaid > num(draft.customer.credit_limit) && (
                      <Field label="Credit limit override reason" hint={can("sales.credit_override") ? "Over the limit — you can approve with a reason" : "Over the limit — a manager must approve"}>
                        <input className={inputCls} value={draft.overrideReason} disabled={!can("sales.credit_override")} onChange={(e) => update({ overrideReason: e.target.value })} />
                      </Field>
                    )}
                  </>
                )}
              </div>
            )}

            <Btn variant="primary" className="w-full mt-4 py-3 text-base" onClick={finalize} busy={finalizing}
              disabled={!quote || saleLines.length === 0 || (isCredit && !draft.customer)}>
              <Receipt className="w-5 h-5" /> Finalise bill {total ? money(total) : ""} (F9)
            </Btn>
          </Card>
        </div>
      </div>

      <NewCustomerModal open={newCustomerOpen} canCredit={can("sales.credit_override")} onClose={() => setNewCustomerOpen(false)}
        onCreated={(c) => { pickCustomer(c); setNewCustomerOpen(false); }} />

      <Modal open={!!done} title="Bill saved" onClose={() => setDone(null)}>
        {done && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-emerald-300"><CheckCircle2 className="w-6 h-6" /> {done.replayed ? "This bill was already saved (retry returned the original)." : "Sale finalised."}</div>
            <div className="text-sm text-slate-300">Invoice <span className="font-mono font-bold text-white">{done.invoice_number}</span> · {done.customer_name}</div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="bg-slate-950 rounded-lg p-2"><div className="text-[10px] text-slate-500">Total</div><div className="font-mono">{money(done.grand_total)}</div></div>
              <div className="bg-slate-950 rounded-lg p-2"><div className="text-[10px] text-slate-500">Balance due</div><div className="font-mono text-amber-300">{money(done.balance_due)}</div></div>
              <div className="bg-emerald-500/10 rounded-lg p-2 border border-emerald-500/30"><div className="text-[10px] text-emerald-400">Give change</div><div className="font-mono text-xl font-bold text-emerald-300">{money(done.change_due)}</div></div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Btn variant="primary" onClick={() => window.open(`/sales/${done.id}/print?layout=thermal&auto=1`, "_blank")}><Printer className="w-4 h-4" /> Receipt</Btn>
              <Btn onClick={() => window.open(`/sales/${done.id}/print?layout=a4&auto=1`, "_blank")}><FileText className="w-4 h-4" /> A4 invoice</Btn>
              <a className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold border bg-slate-800 border-slate-700" href={salesApi.pdfUrl(done.id)}>PDF</a>
              <Link className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm font-semibold border bg-slate-800 border-slate-700" href={`/sales/${done.id}`}><Wallet className="w-4 h-4" /> Open invoice / WhatsApp</Link>
            </div>
            <div className="text-xs text-slate-500">Press Enter for the next bill.</div>
          </div>
        )}
      </Modal>
    </SalesShell>
  );
}
