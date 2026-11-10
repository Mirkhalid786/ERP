import "./globals.css";
import type { Metadata } from "next";
import { BrandingProvider } from "@/lib/branding";
import { NO_FLASH_SCRIPT } from "@/lib/theme";

// Static defaults: the build must not depend on the API being reachable (in Docker it is not).
// The BrandingProvider replaces the browser title and favicon with the pharmacy's branding at runtime.
export const metadata: Metadata = {
  title: "My Pharmacy",
  description: "Pharmacy billing, stock & accounts",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full bg-slate-950 text-slate-100 antialiased" suppressHydrationWarning>
      <head>
        {/* Constant script (no user data): re-applies the cached theme before first paint */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body className="h-full min-h-screen flex flex-col font-sans">
        <BrandingProvider>{children}</BrandingProvider>
      </body>
    </html>
  );
}
