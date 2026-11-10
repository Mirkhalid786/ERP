"use client";

/**
 * Theme editor shared by Settings → Branding & Appearance (global defaults) and Settings → My Appearance (own choices).
 * Edits are previewed live across the whole app (setPreview) and only persisted when the page saves them.
 */
import { ReactNode } from "react";
import { ArrowDown, ArrowUp, Check, Eye, EyeOff, Monitor, Moon, RotateCcw, Sun } from "lucide-react";
import { Appearance, ColorKey, COLOR_LABELS, PRESETS, contrastWarnings, isHex, presetById } from "@/lib/theme";

export const DASHBOARD_SECTIONS: Record<string, string> = {
  kpis: "Inventory KPIs",
  quick_actions: "Quick actions",
  recent_activity: "Recent stock movements",
  system: "Roles & settings overview",
  status: "Status banner",
};
export const DASHBOARD_IDS = Object.keys(DASHBOARD_SECTIONS);

const COLOR_GROUPS: { title: string; keys: ColorKey[] }[] = [
  { title: "Brand", keys: ["primary", "secondary", "accent"] },
  { title: "Surfaces & text", keys: ["background", "surface", "sidebar", "text", "muted", "border"] },
  { title: "Status", keys: ["success", "warning", "error", "info"] },
];

function Section({ title, hint, children, action }: { title: string; hint?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-white">{title}</h2>
          {hint && <p className="text-xs text-slate-400 mt-0.5">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string; icon?: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex p-1 bg-slate-950 border border-slate-800 rounded-xl gap-1 flex-wrap">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition ${
            value === o.value ? "bg-emerald-500 text-slate-950" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"}`}>
          {o.icon}{o.label}
        </button>
      ))}
    </div>
  );
}

export function ThemeEditor({ value, onChange, resolvedMode, showLayout = true, layoutNote }: {
  value: Appearance;
  onChange: (a: Appearance) => void;
  resolvedMode: "light" | "dark";
  showLayout?: boolean;
  layoutNote?: string;
}) {
  const set = (patch: Partial<Appearance>) => onChange({ ...value, ...patch });
  const setColor = (k: ColorKey, v: string | null) => {
    const colors = { ...value.colors };
    if (v && isHex(v)) colors[k] = v.toLowerCase(); else delete colors[k];
    set({ colors });
  };
  const warnings = contrastWarnings(value, resolvedMode);
  const order = [...value.dashboard_order.filter((id) => DASHBOARD_IDS.includes(id)), ...DASHBOARD_IDS.filter((id) => !value.dashboard_order.includes(id))];
  const move = (id: string, dir: -1 | 1) => {
    const i = order.indexOf(id);
    const j = i + dir;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    set({ dashboard_order: next });
  };
  const toggleHidden = (id: string) =>
    set({ dashboard_hidden: value.dashboard_hidden.includes(id) ? value.dashboard_hidden.filter((x) => x !== id) : [...value.dashboard_hidden, id] });
  const preset = presetById(value.preset);

  return (
    <div className="space-y-5">
      <Section title="Theme" hint="Pick a professionally designed preset. Light, dark or system mode can be chosen separately below.">
        <div role="radiogroup" aria-label="Theme preset" className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
          {PRESETS.map((p) => {
            const active = value.preset === p.id;
            return (
              <button key={p.id} type="button" role="radio" aria-checked={active}
                onClick={() => set({ preset: p.id, mode: p.mode || value.mode, colors: {} })}
                className={`text-left p-3 rounded-xl border transition relative ${active ? "border-emerald-500 ring-2 ring-emerald-500/30 bg-emerald-500/5" : "border-slate-800 hover:border-slate-600 bg-slate-950"}`}>
                <div className="flex gap-1 mb-2" aria-hidden="true">
                  {p.swatch.map((c, i) => <span key={i} className="h-6 flex-1 rounded-md border border-black/10" style={{ background: c }} />)}
                </div>
                <div className="text-xs font-semibold text-slate-100 pr-5">{p.name}</div>
                <div className="text-[11px] text-slate-500 leading-snug mt-0.5">{p.description}</div>
                {active && <Check className="w-4 h-4 text-emerald-400 absolute top-2 right-2" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      </Section>

      <Section title="Mode" hint="System follows the device's light/dark setting.">
        <Segmented label="Colour mode" value={value.mode} onChange={(mode) => set({ mode })} options={[
          { value: "light", label: "Light", icon: <Sun className="w-3.5 h-3.5" /> },
          { value: "dark", label: "Dark", icon: <Moon className="w-3.5 h-3.5" /> },
          { value: "system", label: "System", icon: <Monitor className="w-3.5 h-3.5" /> },
        ]} />
      </Section>

      <Section title="Custom colours" hint={`Optional. Leave a colour empty to use the ${preset.name} preset.`}
        action={Object.keys(value.colors).length > 0 && (
          <button type="button" onClick={() => set({ colors: {} })} className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1">
            <RotateCcw className="w-3.5 h-3.5" /> Restore preset colours
          </button>
        )}>
        <div className="space-y-4">
          {COLOR_GROUPS.map((g) => (
            <div key={g.title}>
              <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-2">{g.title}</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {g.keys.map((k) => {
                  const v = value.colors[k];
                  return (
                    <div key={k} className="flex items-center gap-2 p-2 bg-slate-950 border border-slate-800 rounded-xl">
                      <input type="color" aria-label={`${COLOR_LABELS[k]} colour`} value={v || "#888888"}
                        onChange={(e) => setColor(k, e.target.value)}
                        className={`w-9 h-9 rounded-lg border border-slate-700 bg-transparent cursor-pointer ${v ? "" : "opacity-40"}`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs text-slate-300">{COLOR_LABELS[k]}</div>
                        <input aria-label={`${COLOR_LABELS[k]} hex value`} placeholder="Preset" value={v || ""} maxLength={7}
                          onChange={(e) => { const t = e.target.value.trim(); if (!t) setColor(k, null); else if (isHex(t)) setColor(k, t); }}
                          className="w-full bg-transparent text-[11px] font-mono text-slate-500 focus:outline-none" />
                      </div>
                      {v && <button type="button" onClick={() => setColor(k, null)} className="text-[11px] text-slate-500 hover:text-slate-200" aria-label={`Clear ${COLOR_LABELS[k]}`}>Clear</button>}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {warnings.length > 0 && (
            <div role="status" className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs space-y-1">
              {warnings.map((w) => <div key={w}>{w}</div>)}
            </div>
          )}
        </div>
      </Section>

      {showLayout && (
        <Section title="Layout" hint={layoutNote}>
          <div className="flex flex-wrap gap-6">
            <div>
              <div className="text-xs text-slate-400 mb-1.5">Navigation</div>
              <Segmented label="Navigation style" value={value.nav} onChange={(nav) => set({ nav })}
                options={[{ value: "sidebar", label: "Sidebar" }, { value: "top", label: "Top bar" }]} />
            </div>
            <div>
              <div className="text-xs text-slate-400 mb-1.5">Density</div>
              <Segmented label="Density" value={value.density} onChange={(density) => set({ density })}
                options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} />
            </div>
            {value.nav === "sidebar" && (
              <div>
                <div className="text-xs text-slate-400 mb-1.5">Sidebar</div>
                <Segmented label="Sidebar state" value={value.sidebar_collapsed ? "collapsed" : "expanded"}
                  onChange={(v) => set({ sidebar_collapsed: v === "collapsed" })}
                  options={[{ value: "expanded", label: "Expanded" }, { value: "collapsed", label: "Collapsed" }]} />
              </div>
            )}
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Dashboard sections</div>
            <ul className="divide-y divide-slate-800 border border-slate-800 rounded-xl overflow-hidden">
              {order.map((id, i) => {
                const hidden = value.dashboard_hidden.includes(id);
                return (
                  <li key={id} className="flex items-center gap-2 px-3 py-2 bg-slate-950">
                    <span className={`flex-1 text-sm ${hidden ? "text-slate-500 line-through" : "text-slate-200"}`}>{DASHBOARD_SECTIONS[id]}</span>
                    <button type="button" onClick={() => toggleHidden(id)} aria-pressed={!hidden} aria-label={`${hidden ? "Show" : "Hide"} ${DASHBOARD_SECTIONS[id]}`}
                      className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-800">{hidden ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}</button>
                    <button type="button" onClick={() => move(id, -1)} disabled={i === 0} aria-label={`Move ${DASHBOARD_SECTIONS[id]} up`}
                      className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-800 disabled:opacity-30"><ArrowUp className="w-4 h-4" /></button>
                    <button type="button" onClick={() => move(id, 1)} disabled={i === order.length - 1} aria-label={`Move ${DASHBOARD_SECTIONS[id]} down`}
                      className="p-1.5 rounded-lg text-slate-400 hover:bg-slate-800 disabled:opacity-30"><ArrowDown className="w-4 h-4" /></button>
                  </li>
                );
              })}
            </ul>
            <p className="text-[11px] text-slate-500 mt-1.5">Hiding a section only changes the layout; it never grants access to data a role cannot see.</p>
          </div>
        </Section>
      )}
    </div>
  );
}

/** A small specimen of the current theme: buttons, badges, a table row, an alert and a card. */
export function ThemeSample() {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4" aria-label="Theme preview">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-white">Preview</div>
          <div className="text-xs text-slate-400">How screens look with these settings</div>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Live</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-semibold">Primary</button>
        <button type="button" className="px-3 py-1.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-200 text-xs">Secondary</button>
        <button type="button" className="px-3 py-1.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs">Danger</button>
        <button type="button" disabled className="px-3 py-1.5 rounded-lg bg-slate-800 text-slate-500 text-xs opacity-60">Disabled</button>
      </div>
      <div className="flex flex-wrap gap-2 text-[11px]">
        <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">Paid</span>
        <span className="px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/20">Near expiry</span>
        <span className="px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-300 border border-rose-500/20">Overdue</span>
        <span className="px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-500/20">Info</span>
        <span className="px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-300 border border-teal-500/20">Secondary</span>
      </div>
      <table className="w-full text-xs">
        <thead><tr className="text-left text-slate-500 border-b border-slate-800"><th className="py-1.5">Medicine</th><th>Batch</th><th className="text-right">Stock</th></tr></thead>
        <tbody>
          <tr className="border-b border-slate-800/60"><td className="py-1.5 text-slate-200">Paracetamol 500</td><td className="text-slate-400 font-mono">B2401</td><td className="text-right text-white font-mono">120</td></tr>
          <tr><td className="py-1.5 text-slate-200">Amoxicillin 250</td><td className="text-slate-400 font-mono">A1187</td><td className="text-right text-amber-300 font-mono">8</td></tr>
        </tbody>
      </table>
      <input aria-label="Sample input" placeholder="Search medicines…" className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-emerald-500" />
      <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs">Sample saved successfully.</div>
    </div>
  );
}
