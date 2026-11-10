"use client";

/** Staff sign-in. Shows only the public branding (name, tagline, welcome text, logo); the sign-in flow is unchanged. */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { BrandLogo, useBranding } from "@/lib/branding";
import { AlertCircle, Eye, EyeOff, Loader2, LogIn, ShieldCheck } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const { brand } = useBranding();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Enter your username and password.");
      return;
    }
    setError(null);
    setLoading(true);

    try {
      await api.login(username, password);
      try {
        const currentUser = await api.getMe();
        const perms = currentUser.permissions || [];
        if (perms.some((p: string) => ["settings.manage", "reports.view", "audit.view"].includes(p))) {
          router.push("/dashboard");
        } else if (perms.includes("pos.access")) {
          router.push("/pos");
        } else if (perms.includes("purchases.view")) {
          router.push("/purchasing");
        } else if (perms.includes("stock.view")) {
          router.push("/inventory");
        } else {
          router.push("/medicines");
        }
      } catch {
        router.push("/dashboard");
      }
    } catch (err: any) {
      setError(err.detail || "Authentication failed. Check your credentials.");
    } finally {
      setLoading(false);
    }
  };

  const input =
    "w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/30 transition-colors";

  return (
    <div className="flex-1 min-h-screen grid lg:grid-cols-2">
      {/* Brand panel (wide screens) */}
      <section className="hidden lg:flex flex-col justify-between p-12 relative overflow-hidden bg-emerald-500/10 border-r border-slate-800">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-emerald-500/15 blur-3xl" aria-hidden="true" />
        <div className="absolute -bottom-32 -left-20 w-96 h-96 rounded-full bg-teal-500/10 blur-3xl" aria-hidden="true" />
        <div className="relative flex items-center gap-3">
          <BrandLogo size={48} />
          <div className="text-lg font-bold text-white">{brand.app_name}</div>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-4xl font-bold tracking-tight text-white leading-tight">{brand.tagline || brand.app_name}</h2>
          <p className="mt-4 text-slate-400">Billing, batch-wise stock, credit and accounts — in one place.</p>
        </div>
        <div className="relative flex items-center gap-2 text-xs text-slate-500">
          <ShieldCheck className="w-4 h-4" aria-hidden="true" /> Staff access only. Every sign-in is recorded.
        </div>
      </section>

      {/* Sign-in form */}
      <main className="flex flex-col items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="flex flex-col items-center text-center mb-8">
            <BrandLogo size={56} className="mb-4" />
            <h1 className="text-2xl font-bold tracking-tight text-white">{brand.app_name}</h1>
            {brand.login_welcome && <p className="mt-2 text-sm text-slate-400">{brand.login_welcome}</p>}
          </div>

          {error && (
            <div role="alert" className="mb-5 p-3.5 bg-rose-500/10 border border-rose-500/20 rounded-xl flex items-center gap-3 text-rose-400 text-sm">
              <AlertCircle className="w-5 h-5 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-4" noValidate aria-busy={loading}>
            <div>
              <label htmlFor="username" className="block text-xs font-medium text-slate-400 mb-1.5">Username</label>
              <input id="username" name="username" type="text" autoComplete="username" required autoFocus
                value={username} onChange={(e) => setUsername(e.target.value)} placeholder="e.g. admin or cashier1"
                aria-invalid={!!error && !username.trim()} className={input} />
            </div>

            <div>
              <label htmlFor="password" className="block text-xs font-medium text-slate-400 mb-1.5">Password</label>
              <div className="relative">
                <input id="password" name="password" type={show ? "text" : "password"} autoComplete="current-password" required
                  value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••"
                  aria-invalid={!!error && !password} className={`${input} pr-11`} />
                <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-lg text-slate-500 hover:text-slate-200">
                  {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button type="submit" disabled={loading}
              className="w-full mt-2 py-3 px-4 bg-emerald-600 hover:bg-emerald-700 active:scale-[0.98] disabled:opacity-60 text-on-primary font-semibold rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-emerald-500/20 transition-all cursor-pointer">
              {loading ? (
                <><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /><span>Signing in…</span></>
              ) : (
                <><LogIn className="w-4 h-4" aria-hidden="true" /><span>Sign in</span></>
              )}
            </button>
          </form>
          <p className="mt-8 text-center text-[11px] text-slate-500 lg:hidden">{brand.tagline}</p>
        </div>
      </main>
    </div>
  );
}
