import type {
  BacktestResponse,
  BotConfigData,
  CalendarResponse,
  CandlesResponse,
  LogEntry,
  NewsIntelligenceResponse,
  OverviewResponse,
  OrdersHistoryResponse,
  SimulateResponse,
  SpecMetrics,
  StrategiesResponse,
} from "../domain/types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  getOverview: () => requestJson<OverviewResponse>("/api/overview"),
  getWatchlist: (symbols: string) =>
    requestJson<{ mt5_connected: boolean; items: SpecMetrics[] }>(
      `/api/watchlist?symbols=${encodeURIComponent(symbols)}`
    ),
  getCandles: (
    symbol: string,
    timeframe: string,
    count = 500,
    dateRange?: { start_date?: string; end_date?: string; tz_offset_minutes?: number }
  ) => {
    const params = new URLSearchParams({
      symbol,
      timeframe,
      count: String(count),
    });
    if (dateRange?.start_date) params.set("start_date", dateRange.start_date);
    if (dateRange?.end_date) params.set("end_date", dateRange.end_date);
    if (dateRange?.tz_offset_minutes !== undefined) {
      params.set("tz_offset_minutes", String(dateRange.tz_offset_minutes));
    }
    return requestJson<CandlesResponse>(`/api/candles?${params.toString()}`);
  },
  simulate: (payload: Record<string, unknown>) =>
    requestJson<SimulateResponse>("/api/simulate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getCalendar: (symbol?: string) =>
    requestJson<CalendarResponse>(
      `/api/calendar${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`
    ),
  addCustomEvent: (payload: Record<string, unknown>) =>
    requestJson<{ ok: boolean; custom_events: CalendarResponse["custom_events"] }>(
      "/api/calendar/custom",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),
  deleteCustomEvent: (index: number) =>
    requestJson<{ ok: boolean; custom_events: CalendarResponse["custom_events"] }>(
      `/api/calendar/custom/${index}`,
      { method: "DELETE" }
    ),
  getConfig: () => requestJson<BotConfigData>("/api/config"),
  saveConfig: (payload: Partial<BotConfigData>) =>
    requestJson<{ ok: boolean; config: BotConfigData }>("/api/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  startBot: () => requestJson<{ started: boolean; message: string; running: boolean }>("/api/bot/start", { method: "POST" }),
  stopBot: () => requestJson<{ stopped: boolean; running: boolean }>("/api/bot/stop", { method: "POST" }),
  evaluateOnce: () => requestJson<{ ok: boolean; cycle?: unknown; error?: string }>("/api/bot/evaluate-once", { method: "POST" }),
  closePosition: (ticket: number) => requestJson<{ ok: boolean; result?: unknown }>(`/api/positions/${ticket}/close`, { method: "POST" }),
  moveToBreakeven: (ticket: number) => requestJson<{ ok: boolean; adjustment?: unknown }>(`/api/positions/${ticket}/breakeven`, { method: "POST" }),
  getLogs: (limit = 120) => requestJson<{ logs: LogEntry[] }>(`/api/logs?limit=${limit}`),
  getOrdersHistory: (params?: {
    start_date?: string;
    end_date?: string;
    tz_offset_minutes?: number;
    magic_only?: boolean;
    symbol?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.start_date) qs.set("start_date", params.start_date);
    if (params?.end_date) qs.set("end_date", params.end_date);
    if (params?.tz_offset_minutes !== undefined) {
      qs.set("tz_offset_minutes", String(params.tz_offset_minutes));
    }
    if (params?.magic_only) qs.set("magic_only", "1");
    if (params?.symbol) qs.set("symbol", params.symbol);
    const query = qs.toString();
    return requestJson<OrdersHistoryResponse>(`/api/orders/history${query ? `?${query}` : ""}`);
  },
  getNewsIntelligence: (symbol?: string) =>
    requestJson<NewsIntelligenceResponse>(
      `/api/news-intelligence${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`
    ),
  saveNewsActual: (payload: {
    title: string;
    currency: string;
    event_time: string;
    actual: string;
    forecast?: string | null;
    previous?: string | null;
    impact?: string;
  }) =>
    requestJson<{ ok: boolean }>("/api/news-actuals", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getStrategies: () => requestJson<StrategiesResponse>("/api/strategies"),
  saveActiveStrategy: (payload: { strategy_name: string; params: Record<string, number> }) =>
    requestJson<StrategiesResponse>("/api/strategies/active", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  syncDefensiveWithStrategy: () =>
    requestJson<StrategiesResponse>("/api/strategies/sync-defensive", {
      method: "POST",
    }),
  runStrategyBacktest: (payload: {
    strategy_name: string;
    params: Record<string, number>;
    symbol: string;
    timeframe: string;
    bars: number;
    range_mode?: "bars" | "calendar";
    start_date?: string;
    end_date?: string;
    tz_offset_minutes?: number;
    include_defensive?: boolean;
    initial_capital?: number | null;
  }) =>
    requestJson<BacktestResponse>("/api/strategies/backtest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
