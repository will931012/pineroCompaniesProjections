import { apiRequest, type Schemas } from "./client";

export type SessionOut = Schemas["SessionOut"];
export type UserOut = Schemas["UserOut"];
export type AuthConfig = Schemas["AuthConfigOut"];
export type CompanySummary = Schemas["CompanySummary"];
export type CompanySearchResponse = Schemas["CompanySearchResponse"];
export type CompanyProfile = Schemas["CompanyProfile"];
export type SourceRef = Schemas["SourceRef"];
export type MarketBarsResponse = Schemas["MarketBarsResponse"];
export type DailyBar = Schemas["DailyBarOut"];
export type Watchlist = Schemas["WatchlistOut"];
export type SystemStatus = Schemas["SystemStatus"];
export type AdminUser = Schemas["AdminUserOut"];
export type ProviderFetch = Schemas["ProviderFetchOut"];
export type AuditEvent = Schemas["AuditEventOut"];
export type Role = UserOut["role"];
export type FundamentalsResponse = Schemas["FundamentalsResponse"];
export type LineItem = Schemas["LineItemOut"];
export type MetricSeries = Schemas["MetricSeriesOut"];
export type FinancialPeriod = Schemas["PeriodOut"];
export type CompanyMetrics = Schemas["CompanyMetricsResponse"];
export type SnapshotMetric = Schemas["SnapshotMetricOut"];
export type PeersResponse = Schemas["PeersResponse"];
export type ScreenMetric = Schemas["ScreenMetricOut"];
export type ScreenRequest = Schemas["ScreenRequest"];
export type ScreenResponse = Schemas["ScreenResponse"];
export type ScreenFilter = Schemas["Filter"];

export const api = {
  authConfig: () => apiRequest<AuthConfig>("/auth/config"),
  session: () => apiRequest<SessionOut>("/auth/session"),
  login: (body: Schemas["LoginIn"]) => apiRequest<SessionOut>("/auth/login", { method: "POST", body }),
  register: (body: Schemas["RegisterIn"]) =>
    apiRequest<SessionOut>("/auth/register", { method: "POST", body }),
  logout: () => apiRequest<void>("/auth/logout", { method: "POST" }),

  searchCompanies: (query: string, limit = 10, signal?: AbortSignal) =>
    apiRequest<CompanySearchResponse>("/companies", { query: { query, limit }, signal }),
  company: (ticker: string) => apiRequest<CompanyProfile>(`/companies/${encodeURIComponent(ticker)}`),
  dailyBars: (ticker: string, from: string, to: string) =>
    apiRequest<MarketBarsResponse>(`/market-data/${encodeURIComponent(ticker)}/bars`, {
      query: { from, to, interval: "1d" },
    }),

  fundamentals: (ticker: string, period: "annual" | "quarterly", asOf?: string) =>
    apiRequest<FundamentalsResponse>(`/companies/${encodeURIComponent(ticker)}/fundamentals`, {
      query: { period, as_of: asOf || undefined, limit: period === "annual" ? 10 : 12 },
    }),
  companyMetrics: (ticker: string) =>
    apiRequest<CompanyMetrics>(`/companies/${encodeURIComponent(ticker)}/metrics`),
  peers: (ticker: string, extra?: string) =>
    apiRequest<PeersResponse>(`/companies/${encodeURIComponent(ticker)}/peers`, {
      query: { tickers: extra || undefined },
    }),
  screenMetrics: () => apiRequest<ScreenMetric[]>("/screener/metrics"),
  screenSectors: () => apiRequest<string[]>("/screener/sectors"),
  screen: (body: ScreenRequest) =>
    apiRequest<ScreenResponse>("/screener", { method: "POST", body }),
  syncFundamentals: (tickers: string[]) =>
    apiRequest<Schemas["FundamentalsSyncResult"][]>("/admin/ingestion/fundamentals", {
      method: "POST",
      body: { tickers },
    }),

  watchlists: () => apiRequest<Watchlist[]>("/watchlists"),
  createWatchlist: (name: string) =>
    apiRequest<Watchlist>("/watchlists", { method: "POST", body: { name } }),
  deleteWatchlist: (id: string) => apiRequest<void>(`/watchlists/${id}`, { method: "DELETE" }),
  addToWatchlist: (id: string, ticker: string) =>
    apiRequest<Watchlist>(`/watchlists/${id}/items`, { method: "POST", body: { ticker } }),
  removeFromWatchlist: (id: string, ticker: string) =>
    apiRequest<Watchlist>(`/watchlists/${id}/items/${encodeURIComponent(ticker)}`, {
      method: "DELETE",
    }),

  systemStatus: () => apiRequest<SystemStatus>("/system/status"),

  adminUsers: () => apiRequest<AdminUser[]>("/admin/users"),
  updateUser: (id: string, body: Schemas["AdminUserUpdate"]) =>
    apiRequest<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body }),
  syncSecDirectory: () =>
    apiRequest<Record<string, number | boolean>>("/admin/ingestion/sec-directory", {
      method: "POST",
    }),
  providerFetches: () => apiRequest<ProviderFetch[]>("/admin/provider-fetches"),
  auditEvents: () => apiRequest<AuditEvent[]>("/admin/audit-events"),
};
