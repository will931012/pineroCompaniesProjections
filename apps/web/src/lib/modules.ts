import {
  Activity,
  Bell,
  BookOpenText,
  Briefcase,
  Building2,
  FlaskConical,
  LayoutDashboard,
  ListFilter,
  Newspaper,
  ReceiptText,
  Sigma,
  type LucideIcon,
} from "lucide-react";

/** The latest completed roadmap phase. Modules and tabs at or below it are live. */
export const CURRENT_PHASE = 4;
export const CURRENT_PHASE_NAME = "News & alerts";

export function isLive(phase: number): boolean {
  return phase <= CURRENT_PHASE;
}

export type WorkspaceModule = {
  slug: string;
  href: string;
  label: string;
  icon: LucideIcon;
  /** Roadmap phase that delivers the module. */
  phase: number;
  summary: string;
  planned: string[];
};

export const MODULES: WorkspaceModule[] = [
  {
    slug: "dashboard", href: "/", label: "Dashboard", icon: LayoutDashboard, phase: 1,
    summary: "Workspace overview, watchlists, and data-connection health.", planned: [],
  },
  {
    slug: "markets", href: "/markets", label: "Markets", icon: Activity, phase: 6,
    summary: "Index, rates, commodity, and volatility overview with regime context.",
    planned: ["Major indexes and sector performance", "Macro indicators from FRED",
      "Market regime classification (Phase 6 models)"],
  },
  {
    slug: "companies", href: "/companies", label: "Companies", icon: Building2, phase: 1,
    summary: "SEC-sourced company directory and company profiles.", planned: [],
  },
  {
    slug: "research", href: "/research", label: "Research", icon: BookOpenText, phase: 8,
    summary: "Evidence-grounded research reports and versioned investment theses.",
    planned: ["Structured company reports with citations",
      "Fact / calculation / model output / interpretation separation",
      "Thesis versioning and change assessment", "Research chat over retrieved data"],
  },
  {
    slug: "news", href: "/news", label: "News", icon: Newspaper, phase: 4,
    summary: "News and SEC events for your watchlists, linked to companies and classified.",
    planned: [],
  },
  {
    slug: "screener", href: "/screener", label: "Screener", icon: ListFilter, phase: 2,
    summary: "Filter companies on fundamentals, valuation multiples, and momentum.",
    planned: [],
  },
  {
    slug: "models", href: "/models", label: "Models", icon: Sigma, phase: 6,
    summary: "Calibrated probabilistic models with explainability and a prediction journal.",
    planned: ["Point-in-time feature store", "Return-distribution and direction models",
      "Calibration curves and walk-forward evaluation", "SHAP explanations per prediction"],
  },
  {
    slug: "backtests", href: "/backtests", label: "Backtests", icon: FlaskConical, phase: 7,
    summary: "Walk-forward strategy simulation with realistic costs and bias controls.",
    planned: ["Costs, spread, slippage, liquidity", "Delisted securities and survivorship controls",
      "Benchmark-relative performance analytics"],
  },
  {
    slug: "portfolio", href: "/portfolio", label: "Portfolio", icon: Briefcase, phase: 9,
    summary: "Exposures, risk measures, and stress scenarios.",
    planned: ["Sector, factor, and country exposure", "Volatility, beta, VaR, CVaR",
      "Scenario and stress testing"],
  },
  {
    slug: "paper-trading", href: "/paper-trading", label: "Paper trading", icon: ReceiptText,
    phase: 10,
    summary: "Simulated order management. No real-money execution.",
    planned: ["Market, limit, and stop orders", "Simulated commissions, spread, and slippage",
      "Pre-trade risk checks and full order history"],
  },
  {
    slug: "alerts", href: "/alerts", label: "Alerts", icon: Bell, phase: 4,
    summary: "Email alerts on filings, earnings, news events, insider trades, and price moves.",
    planned: [],
  },
];

export function moduleBySlug(slug: string): WorkspaceModule | undefined {
  return MODULES.find((module) => module.slug === slug);
}

export function activeModule(pathname: string): WorkspaceModule {
  if (pathname === "/") return MODULES[0];
  return MODULES.find((m) => m.href !== "/" && pathname.startsWith(m.href)) ?? MODULES[0];
}

/** Company-page tabs and the phase that delivers each one. */
export const COMPANY_TABS: { label: string; phase: number }[] = [
  { label: "Overview", phase: 1 },
  { label: "Financials", phase: 2 },
  { label: "Valuation", phase: 5 },
  { label: "SEC", phase: 3 },
  { label: "Earnings", phase: 4 },
  { label: "News", phase: 4 },
  { label: "Technicals", phase: 6 },
  { label: "Quant", phase: 6 },
  // 13F institutional holdings were deferred from Phase 3 (they need CUSIP mapping).
  { label: "Ownership", phase: 6 },
  { label: "Insiders", phase: 3 },
  { label: "Peers", phase: 2 },
  { label: "AI Research", phase: 8 },
];
