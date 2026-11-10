"use client";

/**
 * Settings → My Appearance: every signed-in user chooses their own theme, mode and layout.
 * Only choices that differ from the pharmacy defaults are saved, so later changes to the defaults still reach
 * everything this user has not customised. Nothing here changes other users or the shared branding.
 */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Palette, RotateCcw, Save } from "lucide-react";
import { Navbar } from "@/components/Navbar";
import { Btn, ErrorBox, Notice, Spinner, errorText, useStaff } from "@/components/sales-ui";
import { ThemeEditor, ThemeSample } from "@/components/theme-editor";
import { useBranding } from "@/lib/branding";
import { Appearance, resolveMode } from "@/lib/theme";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export default function MyAppearancePage() {
  const { user, can } = useStaff();
  const { mine, setPreview, savePersonal, resetPersonal, mode } = useBranding();
  const [draft, setDraft] = useState<Appearance | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => { if (mine && !draft) setDraft(mine.effective); }, [mine, draft]);
  useEffect(() => { setPreview(draft); }, [draft, setPreview]);
  useEffect(() => () => setPreview(null), [setPreview]);

  const dirty = useMemo(() => !!(draft && mine && !same(draft, mine.effective)), [draft, mine]);
  const systemDark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;

  const save = async () => {
    if (!draft || !mine) return;
    setBusy(true); setError(null);
    try {
      // Store only what differs from the pharmacy defaults
      const override: Record<string, unknown> = {};
      for (const k of Object.keys(draft) as (keyof Appearance)[]) if (!same(draft[k], mine.defaults[k])) override[k] = draft[k];
      const out = await savePersonal(override, true);
      setDraft(out.effective);
      setInfo(out.customized ? "Your appearance is saved. It applies only to your account." : "Saved. You are using the pharmacy default appearance.");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const reset = async () => {
    setBusy(true); setError(null);
    try {
      await resetPersonal();
      setDraft(null);
      setInfo("Your appearance now follows the pharmacy default.");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      <Navbar user={user} />
      <main className="flex-1 w-full max-w-6xl mx-auto px-4 sm:px-6 py-5 space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-xl"><Palette className="w-6 h-6" /></div>
            <div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-white">My appearance</h1>
              <p className="text-sm text-slate-400">Your personal theme and layout. Other staff and the pharmacy branding are not affected.</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {mine?.customized && <Btn onClick={reset} disabled={busy}><RotateCcw className="w-4 h-4" /> Use pharmacy default</Btn>}
            <Btn variant="primary" onClick={save} disabled={!dirty} busy={busy}><Save className="w-4 h-4" /> Apply theme</Btn>
          </div>
        </div>
        <ErrorBox message={error} onClose={() => setError(null)} />
        {info && <Notice tone="emerald">{info}</Notice>}
        {mine && (
          <div className="text-xs text-slate-400">
            {mine.customized ? "You have personal settings." : "You are following the pharmacy default."}
            {can("settings.manage") && <> The pharmacy-wide default is set in <Link href="/settings/branding" className="text-emerald-400 hover:underline">Branding & Appearance</Link>.</>}
            {dirty && <span className="ml-2 text-amber-300">Previewing unsaved changes — leave this page without applying to discard them.</span>}
          </div>
        )}
        {!user || !draft ? <Spinner /> : (
          <div className="grid lg:grid-cols-[1fr_320px] gap-5 items-start">
            <ThemeEditor value={draft} onChange={setDraft} resolvedMode={resolveMode(draft.mode, systemDark) || mode} />
            <div className="lg:sticky lg:top-5"><ThemeSample /></div>
          </div>
        )}
      </main>
    </div>
  );
}
