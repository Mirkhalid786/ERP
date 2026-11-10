"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Activity, ShieldCheck, ArrowRight } from "lucide-react";

export default function HomePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [statusText, setStatusText] = useState("Checking system initialization...");

  useEffect(() => {
    async function initCheck() {
      try {
        const setup = await api.getSetupStatus();
        if (setup.setup_required) {
          router.replace("/setup");
          return;
        }

        try {
          const user = await api.getMe();
          if (user && user.user_id) {
            const perms = user.permissions || [];
            if (perms.some((p: string) => ["settings.manage", "reports.view", "audit.view"].includes(p))) {
              router.replace("/dashboard");
            } else if (perms.includes("pos.access")) {
              router.replace("/pos");
            } else if (perms.includes("purchases.view")) {
              router.replace("/purchasing");
            } else if (perms.includes("stock.view")) {
              router.replace("/inventory");
            } else {
              router.replace("/medicines");
            }
            return;
          }
        } catch {
          router.replace("/login");
          return;
        }
      } catch (err) {
        setStatusText("Unable to reach Pharmacy ERP backend. Ensure FastAPI server is running.");
      } finally {
        setLoading(false);
      }
    }
    initCheck();
  }, [router]);

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
      <div className="max-w-md w-full bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl backdrop-blur-xl">
        <div className="w-14 h-14 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 rounded-2xl flex items-center justify-center mx-auto mb-6 shadow-inner">
          <Activity className="w-7 h-7 animate-pulse" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-white mb-2">
          Pharmacy ERP
        </h1>
        <p className="text-slate-400 text-sm mb-6">
          Local-first Indian retail pharmacy management system
        </p>

        <div className="p-4 bg-slate-950/60 rounded-xl border border-slate-800/80 mb-6 flex items-center justify-center gap-3 text-sm text-slate-300">
          <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{statusText}</span>
        </div>

        {!loading && (
          <div className="flex flex-col gap-2">
            <button
              onClick={() => router.push("/setup")}
              className="w-full py-2.5 px-4 bg-emerald-600 hover:bg-emerald-500 active:scale-[0.98] transition-all text-on-primary font-medium rounded-xl flex items-center justify-center gap-2 shadow-lg shadow-emerald-950"
            >
              <span>First-time Setup</span>
              <ArrowRight className="w-4 h-4" />
            </button>
            <button
              onClick={() => router.push("/login")}
              className="w-full py-2.5 px-4 bg-slate-800 hover:bg-slate-700 active:scale-[0.98] transition-all text-slate-200 font-medium rounded-xl"
            >
              Sign In
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
