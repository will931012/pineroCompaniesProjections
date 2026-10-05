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
export type Filing = Schemas["FilingOut"];
export type FilingsResponse = Schemas["FilingsResponse"];
export type FilingDetail = Schemas["FilingDetail"];
export type FilingSection = Schemas["SectionOut"];
export type SectionDiff = Schemas["SectionDiffOut"];
export type DiffBlock = Schemas["DiffBlockOut"];
export type IndexDocumentsOut = Schemas["IndexDocumentsOut"];
export type SearchResponse = Schemas["SearchResponse"];
export type SearchHit = Schemas["SearchHitOut"];
export type InsidersResponse = Schemas["InsidersResponse"];
export type InsiderTransaction = Schemas["InsiderTransactionOut"];
export type NewsResponse = Schemas["NewsResponse"];
export type NewsItem = Schemas["NewsItemOut"];
export type EventsResponse = Schemas["EventsResponse"];
export type TimelineEvent = Schemas["TimelineEvent"];
export type EarningsResponse = Schemas["EarningsResponse"];
export type FeedResponse = Schemas["FeedResponse"];
export type EventType = Schemas["EventTypeOut"];
export type AlertRule = Schemas["RuleOut"];
export type AlertRuleIn = Schemas["RuleIn"];
export type AlertsResponse = Schemas["AlertsResponse"];
export type AlertStatus = Schemas["AlertStatus"];
export type JobsOverview = Schemas["JobsOverview"];
export type ValuationOut = Schemas["ValuationOut"];
export type ValuationScenario = Schemas["ScenarioOut"];
export type ValuationGrid = Schemas["GridOut"];
export type ValuationRunSummary = Schemas["RunSummary"];
export type ValuationRun = Schemas["RunOut"];
export type RelativeValuation = Schemas["RelativeOut"];
export type MarketsOverview = Schemas["MarketsOut"];
export type BitcoinAnalysis = Schemas["BitcoinOut"];
export type Technicals = Schemas["TechnicalsOut"];
export type Factors = Schemas["FactorsOut"];
export type Ownership = Schemas["OwnershipOut"];
export type Predictions = Schemas["PredictionsOut"];
export type ModelSummary = Schemas["ModelSummary"];
export type ModelDetail = Schemas["ModelDetail"];
export type ResearchStatus = Schemas["ResearchStatus"];
export type BacktestIn = Schemas["BacktestIn"];
export type BacktestOptions = Schemas["OptionsOut"];
export type BacktestSummary = Schemas["BacktestSummary"];
export type BacktestResult = Schemas["BacktestOut"];

const companyPath = (ticker: string) => `/companies/${encodeURIComponent(ticker)}`;

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

  filings: (ticker: string, forms: string, offset = 0, limit = 50) =>
    apiRequest<FilingsResponse>(`${companyPath(ticker)}/filings`, {
      query: { forms: forms || undefined, offset, limit },
    }),
  filing: (ticker: string, accession: string) =>
    apiRequest<FilingDetail>(`${companyPath(ticker)}/filings/${encodeURIComponent(accession)}`),
  filingDiff: (ticker: string, accession: string, section: string) =>
    apiRequest<SectionDiff>(`${companyPath(ticker)}/filings/${encodeURIComponent(accession)}/diff`, {
      query: { section },
    }),
  indexFilings: (ticker: string, body: Schemas["IndexDocumentsIn"]) =>
    apiRequest<IndexDocumentsOut>(`${companyPath(ticker)}/filings/index`, { method: "POST", body }),
  loadFilingHistory: (ticker: string) =>
    apiRequest<Schemas["HistoryOut"]>(`${companyPath(ticker)}/filings/history`, { method: "POST" }),
  insiders: (ticker: string, months: number, load: number) =>
    apiRequest<InsidersResponse>(`${companyPath(ticker)}/insiders`, { query: { months, load } }),
  searchFilings: (q: string, tickers?: string, forms?: string) =>
    apiRequest<SearchResponse>("/search/filings", {
      query: { q, tickers: tickers || undefined, forms: forms || undefined, limit: 20 },
    }),

  news: (ticker: string, days: number, confidence: "high" | "all") =>
    apiRequest<NewsResponse>(`${companyPath(ticker)}/news`, { query: { days, confidence } }),
  refreshNews: (ticker: string) =>
    apiRequest<Schemas["NewsRefreshOut"]>(`${companyPath(ticker)}/news/refresh`, { method: "POST" }),
  events: (ticker: string, days: number, includeRepeats = false) =>
    apiRequest<EventsResponse>(`${companyPath(ticker)}/events`, {
      query: { days, include_repeats: includeRepeats ? "true" : undefined },
    }),
  earnings: (ticker: string) => apiRequest<EarningsResponse>(`${companyPath(ticker)}/earnings`),
  eventTypes: () => apiRequest<EventType[]>("/events/types"),
  feed: (days = 7) => apiRequest<FeedResponse>("/feed", { query: { days } }),

  alertRules: () => apiRequest<AlertRule[]>("/alerts/rules"),
  createAlertRule: (body: AlertRuleIn) =>
    apiRequest<AlertRule>("/alerts/rules", { method: "POST", body }),
  updateAlertRule: (id: string, body: Schemas["RuleUpdate"]) =>
    apiRequest<AlertRule>(`/alerts/rules/${id}`, { method: "PATCH", body }),
  deleteAlertRule: (id: string) => apiRequest<void>(`/alerts/rules/${id}`, { method: "DELETE" }),
  evaluateAlertRule: (id: string) =>
    apiRequest<Schemas["EvaluateOut"]>(`/alerts/rules/${id}/evaluate`, { method: "POST" }),
  alerts: () => apiRequest<AlertsResponse>("/alerts"),
  markAlertsRead: (ids?: number[]) =>
    apiRequest<void>("/alerts/read", { method: "POST", body: { ids: ids ?? null } }),
  alertStatus: () => apiRequest<AlertStatus>("/alerts/status"),
  testAlertEmail: () => apiRequest<void>("/alerts/test-email", { method: "POST" }),

  valuationDefaults: (ticker: string) =>
    apiRequest<Schemas["DefaultsOut"]>(`${companyPath(ticker)}/valuation/defaults`),
  computeValuation: (ticker: string, body: Schemas["ValuationIn"], signal?: AbortSignal) =>
    apiRequest<ValuationOut>(`${companyPath(ticker)}/valuation/compute`, { method: "POST", body, signal }),
  valuationRuns: (ticker: string) =>
    apiRequest<ValuationRunSummary[]>(`${companyPath(ticker)}/valuation/runs`),
  saveValuationRun: (ticker: string, body: Schemas["SaveRunIn"]) =>
    apiRequest<ValuationRun>(`${companyPath(ticker)}/valuation/runs`, { method: "POST", body }),
  valuationRun: (id: string) => apiRequest<ValuationRun>(`/valuation/runs/${id}`),
  deleteValuationRun: (id: string) => apiRequest<void>(`/valuation/runs/${id}`, { method: "DELETE" }),
  relativeValuation: (ticker: string) =>
    apiRequest<RelativeValuation>(`${companyPath(ticker)}/valuation/relative`),

  marketsOverview: () => apiRequest<MarketsOverview>("/markets/overview"),
  bitcoin: () => apiRequest<BitcoinAnalysis>("/bitcoin"),
  technicals: (ticker: string, years: number) =>
    apiRequest<Technicals>(`${companyPath(ticker)}/technicals`, { query: { years } }),
  factors: (ticker: string) => apiRequest<Factors>(`${companyPath(ticker)}/factors`),
  ownership: (ticker: string) => apiRequest<Ownership>(`${companyPath(ticker)}/ownership`),
  predictions: (ticker: string) => apiRequest<Predictions>(`${companyPath(ticker)}/predictions`),
  models: () => apiRequest<ModelSummary[]>("/models"),
  model: (id: string) => apiRequest<ModelDetail>(`/models/${id}`),
  researchStatus: () => apiRequest<ResearchStatus>("/research/status"),

  backtestOptions: () => apiRequest<BacktestOptions>("/backtests/options"),
  backtests: () => apiRequest<BacktestSummary[]>("/backtests"),
  backtest: (id: string) => apiRequest<BacktestResult>(`/backtests/${id}`),
  runBacktest: (body: BacktestIn) => apiRequest<BacktestResult>("/backtests", { method: "POST", body }),
  deleteBacktest: (id: string) => apiRequest<void>(`/backtests/${id}`, { method: "DELETE" }),

  adminJobs: () => apiRequest<JobsOverview>("/admin/jobs"),
  enqueueJob: (kind: string, tickers: string[] = []) =>
    apiRequest<Schemas["JobOut"]>("/admin/jobs", { method: "POST", body: { kind, tickers } }),

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
