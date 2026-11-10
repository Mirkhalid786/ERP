"use client";

/**
 * Search-as-you-type medicine selector for purchase documents. Works with large catalogues: it asks the API for
 * matches (brand, generic/salt, strength, form, manufacturer, barcode) instead of loading every medicine.
 * After a choice it loads the medicine's packaging units so the caller can offer only valid purchase units.
 */
import { useEffect, useId, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { fetchJson } from "@/lib/api";

interface MedicineHit {
  id: number;
  name: string;
  generic_name: string;
  strength?: string | null;
  dosage_form: string;
  manufacturer_name?: string | null;
  base_unit_id: number;
  base_unit_name?: string | null;
  current_stock_base?: string | number;
  mrp?: number | string | null;
  is_discontinued?: boolean;
}

export interface MedicineUnitOption { unit_id: number; label: string; factor: number; purchase: boolean }

export interface PickedMedicine {
  id: number;
  label: string;
  units: MedicineUnitOption[];          // base unit first, then configured packaging units
  default_unit_id: number;              // the preferred purchase unit
}

interface Conversion {
  packaging_unit_id: number; packaging_unit_name?: string | null; packaging_unit_abbr?: string | null;
  conversion_factor: string | number; is_purchase_unit: boolean;
}

const labelOf = (m: MedicineHit) =>
  [m.name, m.strength && !m.name.includes(m.strength) ? m.strength : null, m.dosage_form && !m.name.toLowerCase().includes(m.dosage_form.toLowerCase()) ? m.dosage_form : null]
    .filter(Boolean).join(" · ");

// Selected medicines are cached so rows keep their label and units without refetching
const cache = new Map<number, PickedMedicine>();

export async function loadPickedMedicine(id: number): Promise<PickedMedicine> {
  const hit = cache.get(id);
  if (hit) return hit;
  const [m, conv] = await Promise.all([
    fetchJson<MedicineHit>(`/api/v1/medicines/${id}`),
    fetchJson<Conversion[]>(`/api/v1/medicines/${id}/conversions`).catch(() => [] as Conversion[]),
  ]);
  const units: MedicineUnitOption[] = [
    { unit_id: m.base_unit_id, label: `${m.base_unit_name || "Base unit"} (1)`, factor: 1, purchase: false },
    ...conv.map((c) => ({
      unit_id: c.packaging_unit_id, factor: Number(c.conversion_factor), purchase: c.is_purchase_unit,
      label: `${c.packaging_unit_name || "Pack"} (${Number(c.conversion_factor)} ${m.base_unit_name || ""})`.trim(),
    })),
  ];
  const purchase = units.filter((u) => u.purchase).sort((a, b) => a.factor - b.factor);
  const picked = { id: m.id, label: labelOf(m), units, default_unit_id: (purchase[0] || units[units.length - 1]).unit_id };
  cache.set(id, picked);
  return picked;
}

export function MedicinePicker({ value, onChange, required, placeholder = "Search brand, salt, strength…" }: {
  value: string;
  onChange: (id: string, picked: PickedMedicine | null) => void;
  required?: boolean;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [hits, setHits] = useState<MedicineHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  // Label for an already selected medicine (e.g. an edited document)
  useEffect(() => {
    const id = Number(value);
    if (!id) { setLabel(""); return; }
    loadPickedMedicine(id).then((p) => setLabel(p.label)).catch(() => setLabel(`Medicine #${id}`));
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const q = text.trim();
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const qs = new URLSearchParams({ limit: "25", ...(q ? { search: q } : {}) });
        setHits(await fetchJson<MedicineHit[]>(`/api/v1/medicines?${qs}`));
        setActive(0);
      } catch { setHits([]); } finally { setLoading(false); }
    }, 200);
    return () => clearTimeout(t);
  }, [text, open]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const choose = async (m: MedicineHit) => {
    setOpen(false);
    setText("");
    setLabel(labelOf(m));
    try { onChange(String(m.id), await loadPickedMedicine(m.id)); } catch { onChange(String(m.id), null); }
  };

  return (
    <div ref={box} className="relative">
      <div className="flex items-center bg-slate-900 border border-slate-800 rounded-lg focus-within:border-emerald-500/60">
        <Search className="w-3.5 h-3.5 text-slate-500 ml-2 shrink-0" aria-hidden="true" />
        <input
          role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
          aria-label="Medicine" required={required && !value}
          value={open ? text : label}
          placeholder={label || placeholder}
          onFocus={() => { setOpen(true); setText(""); }}
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter" && open && hits[active]) { e.preventDefault(); choose(hits[active]); }
            else if (e.key === "Escape") setOpen(false);
          }}
          className="w-full bg-transparent px-2 py-1.5 text-xs text-white placeholder:text-slate-500 focus:outline-none"
        />
        {value && (
          <button type="button" aria-label="Clear medicine" onClick={() => { onChange("", null); setLabel(""); }}
            className="p-1 mr-1 text-slate-500 hover:text-slate-200"><X className="w-3.5 h-3.5" /></button>
        )}
      </div>
      {open && (
        <ul id={listId} role="listbox" className="absolute z-40 mt-1 w-full min-w-[320px] max-h-72 overflow-auto bg-slate-900 border border-slate-700 rounded-lg shadow-xl text-xs">
          {loading && hits.length === 0 && <li className="px-3 py-2 text-slate-500">Searching…</li>}
          {!loading && hits.length === 0 && (
            <li className="px-3 py-2 text-slate-500">No medicine matches “{text}”. Add it in Medicines, or import a catalogue.</li>
          )}
          {hits.map((m, i) => (
            <li key={m.id} role="option" aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); choose(m); }} onMouseEnter={() => setActive(i)}
              className={`px-3 py-2 cursor-pointer border-b border-slate-800/60 last:border-0 ${i === active ? "bg-emerald-500/10" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-slate-100 font-medium truncate">{labelOf(m)}</span>
                <div className="flex items-center gap-1.5 shrink-0">
                  {m.mrp && <span className="text-[10px] text-slate-400">MRP ₹{Number(m.mrp).toFixed(2)}</span>}
                  {m.is_discontinued && (
                    <span className="px-1.5 py-0.5 text-[9px] font-medium rounded bg-rose-500/10 text-rose-300 border border-rose-500/30">
                      Discontinued
                    </span>
                  )}
                </div>
              </div>
              <div className="text-[11px] text-slate-400 truncate mt-0.5">
                {m.generic_name}{m.manufacturer_name ? ` · ${m.manufacturer_name}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
