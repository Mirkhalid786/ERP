"use client";

/**
 * Settings → Branding & Appearance (settings.manage): the shared pharmacy identity, logos, document footers and the
 * default appearance for every user who has not chosen their own. All changes are audited on the server.
 * GSTIN, drug licence and state are tax identity and stay with first-run setup; they are shown read-only here.
 */
import { ChangeEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Building2, FileText, ImageIcon, Paintbrush, RotateCcw, Save, Trash2, Upload, X } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Btn, ErrorBox, Field, Notice, Spinner, errorText, inputCls, useStaff } from "@/components/sales-ui";
import { ThemeEditor, ThemeSample } from "@/components/theme-editor";
import { ApiError, fetchJson } from "@/lib/api";
import { AssetKind, BrandLogo, PublicBranding, assetUrl, useBranding } from "@/lib/branding";
import { Appearance, mergeAppearance, DEFAULT_APPEARANCE, resolveMode } from "@/lib/theme";

interface AssetInfo { version: string; content_type: string; size_bytes: number; width: number | null; height: number | null; uploaded_at: string }
interface Identity { app_name: string; short_name: string; tagline: string; login_welcome: string; website: string; receipt_footer: string; logo_fit: "contain" | "cover"; text_only: boolean }
interface Pharmacy { name: string; legal_name: string | null; address_line1: string | null; address_line2: string | null; city: string | null; pincode: string | null; phone: string | null; email: string | null; gstin: string | null; drug_license_number: string | null; state_code: string | null }
interface AdminBranding {
  identity: Identity;
  invoice_footer: string;
  pharmacy: Pharmacy;
  appearance: Appearance;
  assets: Record<AssetKind, AssetInfo | null>;
  options: { asset_kinds: Record<AssetKind, { max_bytes: number; types: string[] }> };
}

const SLOTS: { kind: AssetKind; title: string; hint: string }[] = [
  { kind: "logo", title: "Main logo", hint: "Login page, sidebar, invoices. A wide PNG with a transparent background works best." },
  { kind: "logo_dark", title: "Logo for dark mode", hint: "Optional. Used on dark themes; the main logo is used if empty." },
  { kind: "logo_compact", title: "Compact logo / mark", hint: "Square icon for the collapsed sidebar." },
  { kind: "favicon", title: "Browser icon (favicon)", hint: "Square, at least 64 × 64 px." },
];
const ACCEPT = ["image/png", "image/jpeg", "image/webp"];
const PHARMACY_EDITABLE: (keyof Pharmacy)[] = ["name", "legal_name", "address_line1", "address_line2", "city", "pincode", "phone", "email"];
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const size = (b: number) => (b < 1024 ? `${b} bytes` : `${(b / 1024).toFixed(0)} KB`);

function Panel({ icon, title, hint, children, actions }: { icon: ReactNode; title: string; hint?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="p-2 bg-emerald-500/10 text-emerald-400 rounded-lg" aria-hidden="true">{icon}</div>
          <div><h2 className="text-sm font-semibold text-white">{title}</h2>{hint && <p className="text-xs text-slate-400 mt-0.5">{hint}</p>}</div>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

async function upload(kind: AssetKind, file: File): Promise<AssetInfo> {
  const csrf = document.cookie.split("; ").find((c) => c.startsWith("pharmacy_csrf="))?.split("=")[1];
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(`/api/v1/branding/assets/${kind}`, {
    method: "POST", body, credentials: "include", headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* keep status text */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

function LogoSlot({ kind, title, hint, info, maxBytes, onChanged, onError }: {
  kind: AssetKind; title: string; hint: string; info: AssetInfo | null; maxBytes: number;
  onChanged: (msg: string) => void; onError: (msg: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  const choose = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!ACCEPT.includes(f.type)) return onError("Choose a PNG, JPEG or WebP image. SVG and other files are not accepted.");
    if (f.size > maxBytes) return onError(`That image is ${(f.size / 1024).toFixed(0)} KB; the limit for ${title.toLowerCase()} is ${maxBytes / 1024} KB.`);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
  };
  const confirm = async () => {
    if (!file) return;
    setBusy(true);
    try { await upload(kind, file); setFile(null); setPreviewUrl(null); onChanged(`${title} updated.`); }
    catch (e) { onError(errorText(e)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!window.confirm(`Remove the ${title.toLowerCase()}? The text brand is shown instead.`)) return;
    setBusy(true);
    try { await fetchJson(`/api/v1/branding/assets/${kind}`, { method: "DELETE" }); onChanged(`${title} removed.`); }
    catch (e) { onError(errorText(e)); } finally { setBusy(false); }
  };
  const current = info ? assetUrl(kind, info.version) : null;
  const shown = previewUrl || current;
  const darkBg = kind === "logo_dark";

  return (
    <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl flex flex-col gap-3">
      <div>
        <div className="text-sm font-medium text-slate-200">{title}</div>
        <div className="text-[11px] text-slate-500">{hint}</div>
      </div>
      <div className={`h-24 rounded-lg border border-dashed flex items-center justify-center overflow-hidden ${darkBg ? "bg-[#0b1220] border-slate-600" : "bg-[#f8fafc] border-slate-300"}`}
        style={darkBg ? undefined : { backgroundImage: "linear-gradient(45deg,#e2e8f0 25%,transparent 25%,transparent 75%,#e2e8f0 75%),linear-gradient(45deg,#e2e8f0 25%,transparent 25%,transparent 75%,#e2e8f0 75%)", backgroundSize: "12px 12px", backgroundPosition: "0 0,6px 6px" }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {shown ? <img src={shown} alt={`${title} preview`} className="max-h-20 max-w-[90%] object-contain" /> : <span className="text-xs text-slate-400">No image</span>}
      </div>
      {previewUrl && <div className="text-[11px] text-amber-300">Preview — not saved yet ({file && size(file.size)}).</div>}
      {info && !previewUrl && <div className="text-[11px] text-slate-500">{info.width}×{info.height} px · {size(info.size_bytes)} · {info.content_type.replace("image/", "").toUpperCase()}</div>}
      <input ref={input} type="file" accept={ACCEPT.join(",")} className="hidden" onChange={choose} aria-label={`Choose ${title}`} />
      <div className="flex flex-wrap gap-2 mt-auto">
        {previewUrl ? (
          <>
            <Btn variant="primary" onClick={confirm} busy={busy}><Save className="w-4 h-4" /> Save image</Btn>
            <Btn onClick={() => { setFile(null); setPreviewUrl(null); }} disabled={busy}><X className="w-4 h-4" /> Cancel</Btn>
          </>
        ) : (
          <>
            <Btn onClick={() => input.current?.click()} disabled={busy}><Upload className="w-4 h-4" /> {info ? "Replace" : "Upload"}</Btn>
            {info && <Btn variant="danger" onClick={remove} busy={busy}><Trash2 className="w-4 h-4" /> Remove</Btn>}
          </>
        )}
      </div>
    </div>
  );
}

export default function BrandingPage() {
  const { user, can } = useStaff();
  const { refresh, setPreview, brand: liveBrand } = useBranding();
  const [data, setData] = useState<AdminBranding | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [pharmacy, setPharmacy] = useState<Pharmacy | null>(null);
  const [invoiceFooter, setInvoiceFooter] = useState("");
  const [appearance, setAppearance] = useState<Appearance | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [previewTheme, setPreviewTheme] = useState(true);

  const accept = useCallback((d: AdminBranding) => {
    setData(d);
    setIdentity(d.identity);
    setPharmacy(d.pharmacy);
    setInvoiceFooter(d.invoice_footer);
    setAppearance(mergeAppearance(DEFAULT_APPEARANCE, d.appearance));
  }, []);
  const load = useCallback(() => fetchJson<AdminBranding>("/api/v1/branding").then(accept).catch((e) => setError(errorText(e))), [accept]);
  useEffect(() => { if (user && can("settings.manage")) load(); }, [user, can, load]);
  useEffect(() => { setPreview(previewTheme ? appearance : null); }, [appearance, previewTheme, setPreview]);
  useEffect(() => () => setPreview(null), [setPreview]);

  const dirty = useMemo(() => !!data && !!identity && !!pharmacy && !!appearance && (
    !same(identity, data.identity) || !same(pharmacy, data.pharmacy) || invoiceFooter !== data.invoice_footer || !same(appearance, mergeAppearance(DEFAULT_APPEARANCE, data.appearance))
  ), [data, identity, pharmacy, invoiceFooter, appearance]);

  const afterChange = async (msg: string) => { setInfo(msg); setError(null); await Promise.all([load(), refresh()]); };

  const save = async () => {
    if (!data || !identity || !pharmacy || !appearance) return;
    setBusy(true); setError(null);
    try {
      const body: Record<string, unknown> = {};
      if (!same(identity, data.identity)) body.identity = identity;
      if (invoiceFooter !== data.invoice_footer) body.invoice_footer = invoiceFooter;
      if (!same(pharmacy, data.pharmacy)) body.pharmacy = Object.fromEntries(PHARMACY_EDITABLE.map((k) => [k, pharmacy[k] ?? ""]));
      if (!same(appearance, mergeAppearance(DEFAULT_APPEARANCE, data.appearance))) body.appearance = appearance;
      accept(await fetchJson<AdminBranding>("/api/v1/branding", { method: "PUT", body: JSON.stringify(body) }));
      await refresh();
      setInfo("Branding saved. Everyone sees the new identity; users with their own theme keep it.");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const reset = async (section: "identity" | "appearance") => {
    const what = section === "identity" ? "the app name, tagline, welcome text and footers" : "the default theme and layout";
    if (!window.confirm(`Restore ${what} to the built-in defaults? Official business details, logos and users' own themes are not changed.`)) return;
    setBusy(true); setError(null);
    try {
      accept(await fetchJson<AdminBranding>(`/api/v1/branding/reset/${section}`, { method: "POST" }));
      await refresh();
      setInfo(section === "identity" ? "Identity texts restored to defaults." : "Default appearance restored (Emerald Green + White).");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const draftBrand: PublicBranding | null = identity && appearance ? {
    ...liveBrand, ...identity, appearance,
  } : null;
  const systemDark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const text = (k: keyof Identity, label: string, opts: { max: number; area?: boolean; hint?: string; placeholder?: string }) => identity && (
    <Field label={label} hint={opts.hint}>
      {opts.area ? (
        <textarea className={`${inputCls} min-h-[64px]`} maxLength={opts.max} value={String(identity[k] ?? "")} placeholder={opts.placeholder}
          onChange={(e) => setIdentity({ ...identity, [k]: e.target.value })} />
      ) : (
        <input className={inputCls} maxLength={opts.max} value={String(identity[k] ?? "")} placeholder={opts.placeholder}
          onChange={(e) => setIdentity({ ...identity, [k]: e.target.value })} />
      )}
    </Field>
  );
  const phField = (k: keyof Pharmacy, label: string, max: number, hint?: string) => pharmacy && (
    <Field label={label} hint={hint}>
      <input className={inputCls} maxLength={max} value={pharmacy[k] ?? ""} onChange={(e) => setPharmacy({ ...pharmacy, [k]: e.target.value })} />
    </Field>
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-6xl mx-auto px-4 sm:px-6 py-5 space-y-5 pb-28">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><Paintbrush className="w-6 h-6" /></div>
          <div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">Branding & Appearance</h1>
            <p className="text-sm text-slate-400">The pharmacy&apos;s identity, logos and invoice details, and the default look for all staff. Staff can still choose their own theme in <Link href="/settings/appearance" className="text-emerald-400 hover:underline">My appearance</Link>.</p>
          </div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {info && <Notice tone="emerald">{info}</Notice>}

        {!user ? <Spinner /> : !can("settings.manage") ? <ErrorBox message="You need the 'Change pharmacy settings' permission to change the branding." /> : !data || !identity || !pharmacy || !appearance || !draftBrand ? <Spinner /> : (
          <>
            <Panel icon={<Building2 className="w-4 h-4" />} title="Application identity" hint="Shown in the browser tab, sidebar and login page. Visible before sign-in."
              actions={<Btn variant="ghost" onClick={() => reset("identity")} disabled={busy}><RotateCcw className="w-4 h-4" /> Defaults</Btn>}>
              <div className="grid sm:grid-cols-2 gap-3">
                {text("app_name", "Application name", { max: 60, hint: "Browser title and login page" })}
                {text("short_name", "Short name", { max: 24, hint: "Sidebar and mobile header" })}
                {text("tagline", "Tagline", { max: 120 })}
                {text("website", "Website", { max: 200, placeholder: "https://", hint: "Printed on A4 invoices; not shown before sign-in" })}
                <div className="sm:col-span-2">{text("login_welcome", "Login welcome message", { max: 240, area: true })}</div>
              </div>
            </Panel>

            <Panel icon={<ImageIcon className="w-4 h-4" />} title="Logos" hint="PNG, JPEG or WebP. Stored in the database, so they are included in every backup.">
              <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
                {SLOTS.map((s) => (
                  <LogoSlot key={s.kind} {...s} info={data.assets[s.kind]} maxBytes={data.options.asset_kinds[s.kind].max_bytes}
                    onChanged={afterChange} onError={(m) => { setError(m); setInfo(null); }} />
                ))}
              </div>
              <div className="flex flex-wrap gap-6 items-end">
                <Field label="Logo fit">
                  <select className={inputCls} value={identity.logo_fit} onChange={(e) => setIdentity({ ...identity, logo_fit: e.target.value as Identity["logo_fit"] })}>
                    <option value="contain">Fit inside (no cropping)</option>
                    <option value="cover">Fill and crop to the frame</option>
                  </select>
                </Field>
                <label className="flex items-center gap-2 text-sm text-slate-300 pb-2">
                  <input type="checkbox" checked={identity.text_only} onChange={(e) => setIdentity({ ...identity, text_only: e.target.checked })} className="accent-emerald-500 w-4 h-4" />
                  Use a text-only brand (ignore uploaded logos)
                </label>
              </div>
            </Panel>

            <Panel icon={<FileText className="w-4 h-4" />} title="Business details & documents"
              hint="Printed on invoices and receipts. Invoices are printed with the current details, so a change also shows when an older invoice is reprinted. Amounts, numbers and tax are never changed.">
              <div className="grid sm:grid-cols-2 gap-3">
                {phField("name", "Business name", 255, "Printed on invoices")}
                {phField("legal_name", "Legal name", 255, "If different from the business name")}
                {phField("address_line1", "Address line 1", 255)}
                {phField("address_line2", "Address line 2", 255)}
                {phField("city", "City", 100)}
                {phField("pincode", "PIN code", 6)}
                {phField("phone", "Phone", 15)}
                {phField("email", "Email", 255)}
              </div>
              <div className="grid sm:grid-cols-3 gap-3 text-xs">
                {[["GSTIN", pharmacy.gstin], ["Drug licence", pharmacy.drug_license_number], ["State code", pharmacy.state_code]].map(([l, v]) => (
                  <div key={l} className="p-3 bg-slate-950 border border-slate-800 rounded-xl">
                    <div className="text-slate-500">{l} (read-only)</div><div className="font-mono text-slate-200 mt-0.5">{v || "—"}</div>
                  </div>
                ))}
              </div>
              <div className="grid sm:grid-cols-2 gap-3">
                <Field label="Invoice footer / thank-you message" hint="A4 invoices (and receipts when the receipt footer is empty)">
                  <textarea className={`${inputCls} min-h-[64px]`} maxLength={300} value={invoiceFooter} onChange={(e) => setInvoiceFooter(e.target.value)} />
                </Field>
                {text("receipt_footer", "Receipt footer", { max: 300, area: true, hint: "80 mm thermal receipts; leave empty to use the invoice footer" })}
              </div>
            </Panel>

            <Panel icon={<Paintbrush className="w-4 h-4" />} title="Previews" hint="Unsaved text and the current logos.">
              <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
                <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl text-center">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">Login page</div>
                  <BrandLogo brandOverride={draftBrand} size={44} className="mx-auto mb-2" />
                  <div className="font-bold text-white truncate">{identity.app_name}</div>
                  <div className="text-[11px] text-slate-400 line-clamp-2">{identity.login_welcome}</div>
                  <div className="mt-3 h-7 rounded-lg bg-emerald-600 text-on-primary text-xs flex items-center justify-center">Sign in</div>
                </div>
                <div className="p-4 border border-slate-800 rounded-xl" style={{ background: "var(--app-sidebar)" }}>
                  <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">Sidebar</div>
                  <div className="flex items-center gap-2.5">
                    <BrandLogo brandOverride={draftBrand} size={34} />
                    <div className="min-w-0"><div className="font-bold text-white text-sm truncate">{identity.short_name}</div><div className="text-[10px] text-slate-400 truncate">{identity.tagline}</div></div>
                  </div>
                  <div className="mt-3 space-y-1">
                    <div className="px-2 py-1.5 rounded-lg text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">Dashboard</div>
                    <div className="px-2 py-1.5 rounded-lg text-xs text-slate-400">POS</div>
                  </div>
                  <div className="mt-3 flex items-center gap-2"><BrandLogo brandOverride={draftBrand} variant="compact" size={28} /><span className="text-[10px] text-slate-500">Collapsed</span></div>
                </div>
                <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">Dashboard</div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="p-2 rounded-lg bg-slate-900 border border-slate-800"><div className="text-[10px] text-slate-400">Stock value</div><div className="text-sm font-bold text-white font-mono">₹—</div></div>
                    <div className="p-2 rounded-lg bg-slate-900 border border-slate-800"><div className="text-[10px] text-slate-400">Alerts</div><div className="text-sm font-bold text-amber-300 font-mono">—</div></div>
                  </div>
                  <div className="mt-2 h-6 rounded-lg bg-emerald-500 text-slate-950 text-[11px] flex items-center justify-center font-semibold">Primary action</div>
                </div>
                <div className="p-4 rounded-xl border border-slate-300 text-black" style={{ background: "#ffffff" }}>
                  <div className="text-[10px] uppercase tracking-wider mb-3" style={{ color: "#64748b" }}>Invoice (always black on white)</div>
                  <div className="flex items-start gap-2">
                    {!identity.text_only && data.assets.logo && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={assetUrl("logo", data.assets.logo.version) || ""} alt="" className="max-h-8 max-w-[70px] object-contain" />
                    )}
                    <div className="text-[10px] leading-tight" style={{ color: "#000" }}>
                      <div className="font-bold text-[12px]">{pharmacy.name}</div>
                      <div>{[pharmacy.address_line1, pharmacy.city, pharmacy.pincode].filter(Boolean).join(", ")}</div>
                      {pharmacy.phone && <div>Phone: {pharmacy.phone}</div>}
                      {pharmacy.gstin && <div>GSTIN: {pharmacy.gstin}</div>}
                    </div>
                  </div>
                  <div className="mt-2 pt-2 text-[10px] whitespace-pre-line" style={{ borderTop: "1px dashed #000", color: "#000" }}>{identity.receipt_footer || invoiceFooter}</div>
                </div>
              </div>
            </Panel>

            <div className="flex items-center justify-between gap-3 pt-2">
              <div>
                <h2 className="text-base font-semibold text-white">Default appearance</h2>
                <p className="text-xs text-slate-400">For new users and anyone who has not chosen their own. Users who picked their own theme keep their choice.</p>
              </div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs text-slate-400">
                  <input type="checkbox" checked={previewTheme} onChange={(e) => setPreviewTheme(e.target.checked)} className="accent-emerald-500" /> Live preview
                </label>
                <Btn variant="ghost" onClick={() => reset("appearance")} disabled={busy}><RotateCcw className="w-4 h-4" /> Reset to default</Btn>
              </div>
            </div>
            <div className="grid lg:grid-cols-[1fr_320px] gap-5 items-start">
              <ThemeEditor value={appearance} onChange={setAppearance} resolvedMode={resolveMode(appearance.mode, systemDark)}
                layoutNote="Default navigation, density and dashboard layout for users who have not chosen their own." />
              <div className="lg:sticky lg:top-5"><ThemeSample /></div>
            </div>
          </>
        )}
      </main>

      {dirty && (
        <div className="fixed bottom-0 inset-x-0 z-40 border-t border-slate-800 bg-slate-900/95 backdrop-blur no-print" role="region" aria-label="Unsaved changes">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between gap-3 lg:pl-[calc(var(--app-sidebar-w)+1.5rem)]">
            <span className="text-sm text-amber-300">You have unsaved branding changes.</span>
            <div className="flex gap-2">
              <Btn onClick={() => data && accept(data)} disabled={busy}>Discard</Btn>
              <Btn variant="primary" onClick={save} busy={busy}><Save className="w-4 h-4" /> Apply & save</Btn>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
