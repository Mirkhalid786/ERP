"use client";

import { useEffect, useState } from "react";
import { MessageCircle, Save } from "lucide-react";
import {
  Badge, Btn, Card, ErrorBox, Field, Notice, SalesShell, Spinner, errorText, inputCls, useStaff,
} from "@/components/sales-ui";
import { MessagingConfig, salesApi } from "@/lib/sales-api";

const TEMPLATE_LABELS: Record<string, string> = {
  "messaging.template.invoice": "Invoice message",
  "messaging.template.reminder_upcoming": "Reminder before due date",
  "messaging.template.reminder_due_today": "Reminder on due date",
  "messaging.template.reminder_overdue": "Overdue reminder",
};

export default function CommunicationsSettingsPage() {
  const { user, can } = useStaff();
  const [cfg, setCfg] = useState<MessagingConfig | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!user || !can("messaging.configure")) return;
    salesApi.messagingConfig().then((c) => { setCfg(c); setValues(c.settings); }).catch((e) => setError(errorText(e)));
  }, [user, can]);

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const changed = Object.fromEntries(Object.entries(values).filter(([k, v]) => v !== cfg?.settings[k]));
      const c = await salesApi.updateMessagingConfig(changed);
      setCfg(c);
      setValues(c.settings);
      setSaved(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const p = cfg?.provider;
  return (
    <SalesShell user={user} title="WhatsApp & reminders" subtitle="Message templates, reminder rules and provider status" icon={<MessageCircle className="w-6 h-6" />}
      actions={cfg && <Btn variant="primary" busy={busy} onClick={save}><Save className="w-4 h-4" /> Save changes</Btn>}>
      {user && !can("messaging.configure") ? <ErrorBox message="You do not have permission to configure messaging." /> : !cfg ? (error ? <ErrorBox message={error} /> : <Spinner />) : (
        <>
          <ErrorBox message={error} onClose={() => setError(null)} />
          {saved && <Notice tone="emerald">Saved.</Notice>}

          <Card title="Provider">
            <div className="flex flex-wrap gap-2 mb-3">
              {p!.cloud_api_configured ? <Badge value="PAID" label="Cloud API configured" /> : <Badge value="PENDING" label="Manual sharing mode" />}
              <Badge value={p!.app_secret_set ? "PAID" : "CANCELLED"} label={p!.app_secret_set ? "Webhook signature check on" : "No app secret (webhook off)"} />
              <Badge value={p!.attach_invoice_pdf ? "PAID" : "CANCELLED"} label={p!.attach_invoice_pdf ? "PDF attached" : "No PDF attachment"} />
            </div>
            {p!.cloud_api_configured ? (
              <Notice>Messages go through the WhatsApp Business Platform (API {p!.api_version}). A message shows as delivered or read only after WhatsApp reports it through the webhook.</Notice>
            ) : (
              <Notice tone="amber">
                No WhatsApp Business API credentials are configured, so the app opens WhatsApp (wa.me) with the message filled in and staff press Send.
                These messages are recorded as &ldquo;opened in WhatsApp&rdquo; — the app cannot know whether they were sent or delivered.
                To enable automatic sending, set WHATSAPP_PROVIDER=cloud_api and the related variables in the server&apos;s .env (see README). Credentials are never entered or shown here.
              </Notice>
            )}
            <div className="text-xs text-slate-400 mt-3">
              Approved templates (Cloud API, for messages outside the 24-hour window):{" "}
              {Object.entries(p!.templates).map(([k, v]) => `${k.toLowerCase()}: ${v || "— (plain text)"}`).join(" · ")}
            </div>
          </Card>

          <Card title="Message templates">
            <div className="text-xs text-slate-400 mb-3">
              Placeholders: {cfg.placeholders.map((x) => `{${x}}`).join(" ")}. Keep messages polite; do not add medicine names or health details.
            </div>
            <div className="space-y-3">
              {Object.keys(TEMPLATE_LABELS).map((k) => (
                <Field key={k} label={TEMPLATE_LABELS[k]}>
                  <textarea rows={3} className={inputCls} value={values[k] || ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
                </Field>
              ))}
            </div>
          </Card>

          <Card title="Reminder rules">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {["reminders.upcoming_days_before", "reminders.overdue_interval_days", "reminders.max_overdue_reminders", "reminders.send_window_start_hour", "reminders.send_window_end_hour"].map((k) => (
                <Field key={k} label={cfg.descriptions[k]}>
                  <input className={inputCls} inputMode="numeric" value={values[k] || ""} onChange={(e) => setValues({ ...values, [k]: e.target.value.replace(/\D/g, "") })} />
                </Field>
              ))}
              <label className="flex items-center gap-2 text-sm text-slate-300 md:col-span-3">
                <input type="checkbox" disabled={!p!.cloud_api_configured} checked={values["reminders.auto_send_enabled"] === "true"}
                  onChange={(e) => setValues({ ...values, "reminders.auto_send_enabled": e.target.checked ? "true" : "false" })} />
                Send reminders automatically {!p!.cloud_api_configured && "(needs the Cloud API; without it, reminders wait on the Receivables page for staff)"}
              </label>
            </div>
          </Card>
        </>
      )}
    </SalesShell>
  );
}
