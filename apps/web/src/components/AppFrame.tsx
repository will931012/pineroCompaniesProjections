"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Activity, LogOut, Shield } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { setCsrfToken } from "@/lib/api/client";
import { api } from "@/lib/api/endpoints";
import { CURRENT_PHASE, CURRENT_PHASE_NAME, MODULES, activeModule, isLive } from "@/lib/modules";
import { hasRole, useSession } from "@/lib/session";

const BARE_ROUTES = ["/login", "/register"];

export function AppFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (BARE_ROUTES.some((route) => pathname.startsWith(route))) return <>{children}</>;
  return <Workspace pathname={pathname}>{children}</Workspace>;
}

function Workspace({ pathname, children }: { pathname: string; children: ReactNode }) {
  const session = useSession();
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = session.data?.user;
  const current = pathname.startsWith("/admin")
    ? { label: "Administration", phase: 1 }
    : activeModule(pathname);

  async function signOut() {
    try {
      await api.logout();
    } finally {
      setCsrfToken(null);
      queryClient.clear();
      router.replace("/login");
    }
  }

  return (
    <div className="workspace-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="Pinero home">
          <span className="brand-mark"><Activity size={18} strokeWidth={2.4} /></span>
          <span>pinero<span className="brand-period">.</span></span>
        </Link>
        <span className="nav-caption nav-caption-first">WORKSPACE</span>
        <nav className="side-nav" aria-label="Main navigation">
          {MODULES.map(({ slug, href, label, icon: Icon, phase }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                aria-current={active ? "page" : undefined}
                className={`nav-item${active ? " is-active" : ""}`}
                href={href}
                key={slug}
                title={isLive(phase) ? label : `${label} — planned for Phase ${phase}`}
              >
                <Icon size={17} strokeWidth={1.8} />
                <span>{label}</span>
                {!isLive(phase) && <span className="nav-phase">P{phase}</span>}
              </Link>
            );
          })}
          {hasRole(user?.role, "admin") && (
            <Link
              aria-current={pathname.startsWith("/admin") ? "page" : undefined}
              className={`nav-item${pathname.startsWith("/admin") ? " is-active" : ""}`}
              href="/admin"
            >
              <Shield size={17} strokeWidth={1.8} />
              <span>Admin</span>
            </Link>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="user-row">
            <span className="user-avatar">{user?.display_name.slice(0, 1).toUpperCase() ?? "·"}</span>
            <span>
              {user?.display_name ?? "Loading…"}
              <small>{user ? `${user.role} · ${user.email}` : "Checking session"}</small>
            </span>
            {/* With sign-in disabled, signing out would only sign straight back in. */}
            {session.data?.auth_method !== "auth_disabled" && (
              <button className="icon-button sidebar-icon" type="button" onClick={signOut}
                aria-label="Sign out" title="Sign out">
                <LogOut size={15} />
              </button>
            )}
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-actions">
            <span className="environment-badge">
              <span className="status-dot" />
              {isLive(current.phase)
                ? `PHASE ${CURRENT_PHASE} · ${CURRENT_PHASE_NAME.toUpperCase()}`
                : `PLANNED · PHASE ${current.phase}`}
            </span>
          </div>
        </header>
        <div className="page-content">
          {session.isPending ? <div className="page-loading" aria-busy="true">Checking session…</div>
            : session.isError ? <SessionProblem message={session.error.message} /> : children}
        </div>
      </main>
    </div>
  );
}

function SessionProblem({ message }: { message: string }) {
  return (
    <section className="profile-unavailable">
      <div>
        <span className="section-kicker">SESSION</span>
        <h1>Workspace unavailable</h1>
        <p>{message}</p>
      </div>
    </section>
  );
}
