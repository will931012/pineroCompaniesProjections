import { Activity } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

export function AuthCard({ title, subtitle, children, footer }: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        <Link className="brand auth-brand" href="/login">
          <span className="brand-mark"><Activity size={18} strokeWidth={2.4} /></span>
          <span>pinero<span className="brand-period">.</span></span>
        </Link>
        <h1>{title}</h1>
        <p className="auth-subtitle">{subtitle}</p>
        {children}
        {footer && <div className="auth-footer">{footer}</div>}
      </section>
      <p className="auth-legal">Research data only. No real-money trading. Every value shows its source.</p>
    </main>
  );
}
