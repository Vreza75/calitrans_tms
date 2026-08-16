import type { Metadata } from "next";

import { AuthProvider } from "@/lib/auth/AuthContext";
import { QueryProvider } from "@/lib/query/QueryProvider";

import "./globals.css";

export const metadata: Metadata = {
  title: "CaliTrans TMS",
  description: "CaliTrans TMS web client (Phase 10 foundation)",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>
          <AuthProvider>{children}</AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
