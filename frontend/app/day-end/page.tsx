"use client";

import { useCallback, useEffect, useState } from "react";
import { Landmark } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Modal, Notice, SalesShell, Stat, Table, errorText, inputCls, isoToday, td, tdNum, useStaff,
} from "@/components/sales-ui";
import { DayEnd, Shift, fmtDateTime, money, num, salesApi } from "@/lib/sales-api";

type Categories = Awaited<ReturnType<typeof salesApi.movementCategories>>;

function ShiftFigures({ s, cats, onClassify }: { s: Shift; cats?: Categories | null; onClassify?: (id: number, type: string) => void }) {
  const rows: [string, string, string][] = [
    ["Opening cash", s.opening_cash, ""],
    ["+ Cash sales (applied, after change)", s.cash_sales, ""],
    ["+ Cash udhaar collections", s.cash_collections, ""],
    ["− Cash refunds", s.cash_refunds, "neg"],
    ["− Cash paid out", s.paid_out, "neg"],
    ["+ Cash paid in", s.paid_in, ""],
    ["− Supplier payments from drawer", s.supplier_payments_paid || "0", "neg"],
    ["− Expenses paid from drawer", s.expenses_paid || "0", "neg"],
  ];
  const label = (m: Shift["cash_movements"][number]) =>
    cats?.categories[m.movement_type]?.[m.category] || cats?.legacy_only[m.movement_type]?.[m.category] ||
    (m.category === "UNCATEGORISED" ? "Not categorised (recorded before categories)" : m.category);
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <dl className="text-sm space-y-1">
        {rows.map(([l, v, neg]) => (
          <div key={l} className="flex justify-between text-slate-400"><dt>{l}</dt><dd className={`font-mono ${neg ? "text-rose-300" : ""}`}>{money(v)}</dd></div>
        ))}
        <div className="flex justify-between text-white font-bold border-t border-slate-800 pt-1"><dt>Expected cash in drawer</dt><dd className="font-mono">{money(s.expected_cash)}</dd></div>
        {s.counted_cash != null && (
          <>
            <div className="flex justify-between text-slate-300"><dt>Counted</dt><dd className="font-mono">{money(s.counted_cash)}</dd></div>
            <div className={`flex justify-between font-bold ${num(s.variance) === 0 ? "text-emerald-300" : "text-rose-300"}`}><dt>Variance</dt><dd className="font-mono">{money(s.variance)}</dd></div>
            {s.variance_note && <div className="text-xs text-slate-400">Note: {s.variance_note}</div>}
          </>
        )}
      </dl>
      <div>
        <div className="text-xs text-slate-500 mb-1">Not cash — not in the drawer</div>
        {Object.keys(s.non_cash_totals).length === 0 ? <div className="text-sm text-slate-500">None</div> : (
          <dl className="text-sm space-y-1">
            {Object.entries(s.non_cash_totals).map(([k, v]) => (
              <div key={k} className="flex justify-between text-slate-400"><dt>{k.replace(/_/g, " ").toLowerCase()}</dt><dd className="font-mono">{money(v)}</dd></div>
            ))}
          </dl>
        )}
        {s.cash_movements.length > 0 && (
          <div className="mt-3 text-xs text-slate-400">
            {s.cash_movements.map((m) => (
              <div key={m.id} className="flex flex-wrap items-center gap-1">
                <span>{m.movement_type.replace("_", " ")} {money(m.amount)} · {label(m)} — {m.reason}{m.reference ? ` (ref ${m.reference})` : ""}</span>
                {m.category === "UNCATEGORISED" && onClassify && (
                  <button className="text-sky-300 underline" onClick={() => onClassify(m.id, m.movement_type)}>Classify</button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function DayEndPage() {
  const { user, can } = useStaff();
  const [shift, setShift] = useState<Shift | null>(null);
  const [day, setDay] = useState(isoToday());
  const [report, setReport] = useState<DayEnd | null>(null);
  const [mine, setMine] = useState<Shift[]>([]);
  const [opening, setOpening] = useState("");
  const [close, setClose] = useState({ counted: "", note: "" });
  const [move, setMove] = useState({ open: false, type: "PAID_OUT", category: "", amount: "", reason: "", reference: "" });
  const [cats, setCats] = useState<Categories | null>(null);
  const [classify, setClassify] = useState<{ id: number; type: string; category: string; note: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setShift((await salesApi.currentShift()).shift);
      if (can("shifts.review")) setReport(await salesApi.dayEnd(day));
      else setMine(await salesApi.shifts(day));
    } catch (e) {
      setError(errorText(e));
    }
  }, [day, can]);
  useEffect(() => { if (user) load(); }, [user, load]);
  useEffect(() => { if (user && can("shifts.operate")) salesApi.movementCategories().then(setCats).catch(() => undefined); }, [user, can]);
  const onClassify = can("shifts.review") ? (id: number, type: string) => setClassify({ id, type, category: "", note: "" }) : undefined;
  const moveCats = cats?.categories[move.type] || {};
  const needsRef = !!cats?.reference_required.includes(move.category);
  const isOther = move.category.startsWith("OTHER");

  const run = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setInfo(msg);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const variancePreview = shift && close.counted ? num(close.counted) - num(shift.expected_cash) : null;

  return (
    <SalesShell user={user} title="Cash shift & day-end" subtitle="Open your drawer, record paid-outs, count and close" icon={<Landmark className="w-6 h-6" />}>
      <ErrorBox message={error} onClose={() => setError(null)} />
      {info && <Notice tone="emerald">{info}</Notice>}

      {user && !can("shifts.operate") ? <ErrorBox message="You do not have permission to operate a cash shift." /> : !shift ? (
        <Card title="Open a shift">
          <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
            <Field label="Opening cash in drawer ₹"><input className={`${inputCls} font-mono`} value={opening} onChange={(e) => setOpening(e.target.value.replace(/[^0-9.]/g, ""))} /></Field>
            <Btn variant="primary" busy={busy} disabled={opening === ""} onClick={() => run(() => salesApi.openShift(opening), "Shift opened.")}>Open shift</Btn>
          </div>
        </Card>
      ) : (
        <Card title={`Your open shift · since ${fmtDateTime(shift.opened_at)}`} actions={can("shifts.review") && <Btn variant="ghost" onClick={() => setMove({ ...move, open: true })}>Paid out / in</Btn>}>
          <ShiftFigures s={shift} cats={cats} onClassify={onClassify} />
          <div className="border-t border-slate-800 mt-4 pt-4 grid grid-cols-1 sm:grid-cols-[180px_1fr_auto] gap-2 items-end">
            <Field label="Counted cash ₹"><input className={`${inputCls} font-mono`} value={close.counted} onChange={(e) => setClose({ ...close, counted: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
            <Field label={variancePreview ? `Explain variance of ${money(variancePreview)}` : "Note (if counted ≠ expected)"}>
              <input className={inputCls} value={close.note} onChange={(e) => setClose({ ...close, note: e.target.value })} />
            </Field>
            <Btn variant="primary" busy={busy} disabled={close.counted === "" || (!!variancePreview && close.note.trim().length < 3)}
              onClick={() => run(() => salesApi.closeShift(shift.id, close.counted, close.note || undefined), "Shift closed.")}>Close shift</Btn>
          </div>
        </Card>
      )}

      <div className="flex items-center gap-2">
        <Field label="Day"><input type="date" className={`${inputCls} w-44`} value={day} onChange={(e) => setDay(e.target.value)} /></Field>
      </div>

      {report && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Bills" value={String(report.bills)} />
            <Stat label="Net sales (incl. GST)" value={money(report.net_sales)} />
            <Stat label="Given on credit" value={money(report.credit_given)} tone="amber" />
            <Stat label="Cash not in any shift" value={money(report.cash_outside_shifts)} tone={num(report.cash_outside_shifts) > 0 ? "rose" : "slate"} hint="Taken with no shift open" />
          </div>
          <Card title="Money received by method">
            <Table head={["Method", "At sale ₹", "Collections ₹", "Refunds ₹", "Net ₹", "Count"]} empty={Object.keys(report.by_method).length === 0 && "No payments this day."}>
              {Object.entries(report.by_method).map(([m, v]) => (
                <tr key={m}>
                  <td className={td}>{m}</td>
                  <td className={tdNum}>{money(v.sales)}</td>
                  <td className={tdNum}>{money(v.collections)}</td>
                  <td className={tdNum}>{money(v.refunds)}</td>
                  <td className={`${tdNum} font-bold`}>{money(v.net)}</td>
                  <td className={tdNum}>{v.count}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </>
      )}

      {(report?.shifts || mine).map((s) => (
        <Card key={s.id} title={`${s.user_name || "Shift"} · ${fmtDateTime(s.opened_at)}${s.closed_at ? ` → ${fmtDateTime(s.closed_at)}` : ""}`} actions={
          <div className="flex items-center gap-2">
            <Badge value={s.status} />
            {s.needs_review && <Badge value="FAILED" label="Needs review" />}
            {s.status === "CLOSED" && can("shifts.review") && (
              <Btn variant="ghost" onClick={() => {
                const note = prompt("Review note (what was checked / decided)");
                if (note) run(() => salesApi.reviewShift(s.id, note), "Shift reviewed.");
              }}>Review</Btn>
            )}
          </div>
        }>
          <ShiftFigures s={s} cats={cats} onClassify={onClassify} />
          {s.review_note && <div className="text-xs text-slate-400 mt-2">Reviewed by {s.reviewed_by_name}: {s.review_note}</div>}
        </Card>
      ))}

      <Modal open={move.open} title="Cash paid out / paid in" onClose={() => setMove({ ...move, open: false })}>
        <Notice tone="amber">Expenses (tea, courier, repairs …) and supplier payments are recorded on the Expenses and Payables screens with “cash from drawer” — not here — so they are not counted twice.</Notice>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Type">
            <select className={inputCls} value={move.type} onChange={(e) => setMove({ ...move, type: e.target.value, category: "" })}>
              <option value="PAID_OUT">Paid out of drawer</option>
              <option value="PAID_IN">Paid into drawer</option>
            </select>
          </Field>
          <Field label="Amount ₹"><input className={inputCls} value={move.amount} onChange={(e) => setMove({ ...move, amount: e.target.value.replace(/[^0-9.]/g, "") })} /></Field>
        </div>
        <Field label="Category *">
          <select className={inputCls} value={move.category} onChange={(e) => setMove({ ...move, category: e.target.value })}>
            <option value="">Choose…</option>
            {Object.entries(moveCats).map(([code, text]) => <option key={code} value={code}>{text}</option>)}
          </select>
        </Field>
        <Field label={isOther ? "Reason * (at least 10 characters)" : "Reason *"}><input className={inputCls} value={move.reason} onChange={(e) => setMove({ ...move, reason: e.target.value })} /></Field>
        <Field label={needsRef ? "Bank slip reference *" : "Reference (optional)"}><input className={inputCls} value={move.reference} onChange={(e) => setMove({ ...move, reference: e.target.value })} /></Field>
        <div className="flex justify-end gap-2">
          <Btn variant="ghost" onClick={() => setMove({ ...move, open: false })}>Cancel</Btn>
          <Btn variant="primary" busy={busy}
            disabled={!shift || !(num(move.amount) > 0) || !move.category || move.reason.trim().length < (isOther ? 10 : 3) || (needsRef && !move.reference.trim())}
            onClick={() => shift && run(async () => {
              await salesApi.cashMovement(shift.id, move.type, move.category, move.amount, move.reason, move.reference);
              setMove({ open: false, type: "PAID_OUT", category: "", amount: "", reason: "", reference: "" });
            }, "Recorded.")}>Save</Btn>
        </div>
      </Modal>

      <Modal open={!!classify} title="Classify an older cash movement" onClose={() => setClassify(null)}>
        {classify && (
          <>
            <div className="text-sm text-slate-400">This movement was recorded before categories existed. Choosing a category does not change its amount or create any new record.</div>
            <Field label="Category *">
              <select className={inputCls} value={classify.category} onChange={(e) => setClassify({ ...classify, category: e.target.value })}>
                <option value="">Choose…</option>
                {Object.entries({ ...(cats?.categories[classify.type] || {}), ...(cats?.legacy_only[classify.type] || {}) }).map(([code, text]) => (
                  <option key={code} value={code}>{text}</option>
                ))}
              </select>
            </Field>
            <Field label="Note *"><input className={inputCls} value={classify.note} onChange={(e) => setClassify({ ...classify, note: e.target.value })} /></Field>
            <div className="flex justify-end gap-2">
              <Btn variant="ghost" onClick={() => setClassify(null)}>Cancel</Btn>
              <Btn variant="primary" busy={busy} disabled={!classify.category || classify.note.trim().length < 5}
                onClick={() => run(async () => { await salesApi.classifyMovement(classify.id, classify.category, classify.note); setClassify(null); }, "Movement classified.")}>Save</Btn>
            </div>
          </>
        )}
      </Modal>
    </SalesShell>
  );
}
