/** Multipart POST with the session's CSRF header (fetchJson always sends JSON). */
import { ApiError } from "./api";

export async function uploadForm<T>(path: string, form: FormData): Promise<T> {
  const csrf = document.cookie.split("; ").find((c) => c.startsWith("pharmacy_csrf="))?.split("=")[1];
  const res = await fetch(path, {
    method: "POST", body: form, credentials: "include",
    headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const d = await res.json();
      detail = typeof d.detail === "string" ? d.detail : "The upload was not accepted";
    } catch { /* keep the status text */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}
