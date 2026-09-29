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
