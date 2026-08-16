"use client";

// STEP 11: protected route boundary. Fail-closed by rendering nothing
// (never the page content) until auth status resolves - this is a
// convenience redirect, not the security boundary (that's FastAPI's
// require_auth/require_role - see docs/architecture/WEB_CLIENT.md's
// "Security" section for why hiding this route is not enforcement).
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth/AuthContext";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { status, isAuthenticated } = useAuth();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return (
      <div className="page-loading" role="status">
        Loading...
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return <AppShell>{children}</AppShell>;
}
