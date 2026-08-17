"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/AuthContext";

type NavItem = {
  label: string;
  href: string;
  enabled: boolean;
};

// STEP 29: only Loads is functional this phase - everything else is a
// visible, explicitly-labeled placeholder, never a fake empty page that
// implies completeness.
const NAV_ITEMS: NavItem[] = [
  { label: "Overview", href: "/app", enabled: false },
  { label: "Loads", href: "/app/loads", enabled: true },
  { label: "Inbox", href: "/app/inbox", enabled: false },
  { label: "Dispatch", href: "/app/dispatch", enabled: false },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { currentUser, role, signOut } = useAuth();

  function handleSignOut() {
    signOut();
    router.replace("/login");
  }

  return (
    <div className="app-shell">
      <aside className="app-shell__sidebar">
        <div className="app-shell__brand">CaliTrans TMS</div>
        <nav aria-label="Main navigation">
          <ul>
            {NAV_ITEMS.map((item) => (
              <li key={item.href}>
                {item.enabled ? (
                  <Link href={item.href} className={pathname?.startsWith(item.href) ? "active" : undefined}>
                    {item.label}
                  </Link>
                ) : (
                  <span className="nav-disabled" title="Coming in a later phase">
                    {item.label}
                    <em> (Phase 10A+)</em>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="app-shell__main">
        <header className="app-shell__topbar">
          <span className="app-shell__environment-note">
            Web client foundation - Streamlit remains the operational system of record
          </span>
          <div className="app-shell__user">
            {currentUser && (
              <span>
                {currentUser.display_name} <em>({role})</em>
              </span>
            )}
            <button type="button" onClick={handleSignOut}>
              Sign Out
            </button>
          </div>
        </header>
        <main className="app-shell__content">{children}</main>
      </div>
    </div>
  );
}
