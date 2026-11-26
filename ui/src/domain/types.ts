export interface AccountState {
  login: number;
  server: string;
  currency: string;
  currency_digits: number;
  balance: number;
  equity: number;
  margin: number;
  margin_free: number;
  margin_level: number;
  leverage: number;
  margin_so_call: number;
  margin_so_so: number;
  margin_so_mode: number;
  trade_allowed: boolean;
  trade_expert: boolean;
}

export interface SymbolSpec {
  name: string;
  digits: number;
  point: number;
  ask: number;
  bid: number;
  spread_points: number;
  volume_min: number;
  volume_max: number;
  volume_step: number;
  trade_contract_size: number;
  trade_tick_size: number;
  trade_tick_value: number;
  trade_tick_value_profit: number;
  trade_tick_value_loss: number;
  trade_stops_level: number;
  trade_freeze_level: number;
  swap_long: number;
  swap_short: number;
  swap_mode: number;
  swap_rollover3days: number;
  currency_base: string;
  currency_margin: string;
  currency_profit: string;
  quote_time: string | null;
  server_utc_offset_seconds: number | null;
  detected_commission_per_lot: number | null;
}

export interface SpecMetrics {
  requested?: string;
  found?: boolean;
  spec: SymbolSpec;
  margin_per_lot: number | null;
  notional_per_lot: number;
  effective_symbol_leverage: number;
  h1_atr: number | null;
  spread_price: number;
}

export interface OpenPosition {
  ticket: number;
  symbol: string;
  side: "BUY" | "SELL";
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  swap: number;
  magic: number;
  open_time?: number | null;
  owned_by_bot: boolean;
}

export interface EconomicEvent {
  title: string;
  currency: string;
  impact: "HIGH" | "MEDIUM" | "LOW" | "HOLIDAY";
  event_time: string;
  is_all_day_holiday: boolean;
  is_major_market_holiday: boolean;
  local_date: string | null;
  source: string;
}

export interface PreventiveCostBreakdown {
  spread_points: number;
  spread_cost_per_lot: number;
  commission_per_lot: number;
  slippage_cost_per_lot: number;
  swap_rate_raw: number;
  swap_mode: number;
  swap_days_projected: number;
  triple_swap_applied: boolean;
  swap_pnl_per_lot: number;
  swap_cost_per_lot: number;
  total_cost_per_lot: number;
  gross_sl_risk_per_lot: number;
  gross_tp_reward_per_lot: number;
  net_sl_risk_per_lot: number;
  net_tp_reward_per_lot: number;
  gross_rr: number;
  net_rr: number;
  spread_to_tp_ratio: number | null;
  swap_to_tp_ratio: number;
  spread_to_atr_ratio: number | null;
  true_breakeven_points: number;
}

export interface PreventiveDecision {
  approved: boolean;
  reasons: string[];
  warnings: string[];
  costs: PreventiveCostBreakdown;
  upcoming_events: EconomicEvent[];
}

export interface LeverageSnapshot {
  account_leverage: number;
  margin_per_lot: number;
  notional_per_lot: number;
  effective_symbol_leverage: number;
  projected_symbol_notional: number;
  projected_effective_symbol_leverage: number;
  projected_portfolio_notional: number;
  projected_effective_portfolio_leverage: number;
  projected_margin_used: number;
  projected_free_margin: number;
  projected_margin_level_pct: number | null;
}

export interface LotPlan {
  approved: boolean;
  lot: number;
  raw_lot: number;
  mode: "fixed" | "dynamic_risk" | "dynamic_equity_step";
  risk_budget_money: number;
  estimated_loss_at_sl: number;
  estimated_profit_at_tp: number;
  leverage_snapshot: LeverageSnapshot;
  clamp_reasons: string[];
  rejection_reasons: string[];
}

export interface EvaluationCycle {
  status: string;
  signal: {
    symbol: string;
    side: "BUY" | "SELL";
    entry_price: number;
    sl_price: number;
    tp_price: number;
    reason: string;
  } | null;
  preventive: PreventiveDecision | null;
  lot_plan: LotPlan | null;
  execution: {
    executed: boolean;
    dry_run: boolean;
    retcode: number;
    order_ticket: number;
    volume: number;
    price: number;
    comment: string;
  } | null;
}

export interface OverviewResponse {
  mt5_connected: boolean;
  account: AccountState | null;
  active_spec: SpecMetrics | null;
  positions: OpenPosition[];
  bot_state: {
    running: boolean;
    dry_run: boolean;
    symbol: string;
    timeframe: string;
    strategy_name: string;
    strategy_display_name?: string;
    lot_mode: string;
    cycle_count: number;
    last_cycle_time: string | null;
    last_cycle: EvaluationCycle | null;
    daily_drawdown_pct: number;
    max_daily_loss_pct: number | null;
  };
}

export interface SimulationScenario {
  risk_percent: number;
  risk_budget_money: number;
  lot: number;
  raw_lot: number;
  approved: boolean;
  loss_at_sl: number;
  profit_at_tp: number;
  margin_used: number;
  margin_level_pct: number | null;
}

export interface SimulateResponse {
  ok: boolean;
  error?: string;
  symbol?: string;
  equity_used?: number;
  live_equity?: number;
  sl_points_used?: number;
  tp_points_used?: number;
  signal?: {
    symbol: string;
    side: "BUY" | "SELL";
    entry_price: number;
    sl_price: number;
    tp_price: number;
    expected_hold_days: number;
  };
  preventive?: PreventiveDecision;
  lot_plan?: LotPlan;
  h1_atr?: number | null;
  scenarios?: SimulationScenario[];
}

export interface CalendarResponse {
  symbol: string;
  check: {
    blocked: boolean;
    reasons: string[];
    warnings: string[];
    upcoming_events: EconomicEvent[];
  };
  custom_events: Array<{
    title: string;
    currency: string;
    impact: string;
    date: string;
    major?: boolean;
  }>;
}

export interface BotConfigData {
  symbol: string;
  timeframe: string;
  magic_number: number;
  dry_run: boolean;
  poll_seconds: number;
  deviation_points: number;
  risk: {
    lot_mode: "fixed" | "dynamic_risk" | "dynamic_equity_step";
    fixed_lot: number;
    risk_percent: number;
    use_equity_for_risk: boolean;
    equity_step_amount: number;
    equity_step_lot: number;
    max_lot_cap: number | null;
    min_sl_points: number | null;
    include_costs_in_risk_sizing: boolean;
    max_effective_account_leverage: number | null;
    max_effective_symbol_leverage: number | null;
    max_margin_usage_pct: number | null;
    min_margin_level_pct: number | null;
    min_stopout_buffer_multiplier: number | null;
    scale_down_lot_for_leverage: boolean;
  };
  preventive: {
    max_spread_points: number | null;
    max_spread_to_tp_ratio: number | null;
    max_spread_to_atr_ratio: number | null;
    commission_per_lot_roundtrip: number | null;
    auto_detect_commission: boolean;
    estimated_slippage_points: number | null;
    min_net_rr: number | null;
    max_swap_to_tp_ratio: number | null;
    swap_year_days: number;
    swap_rollover_hour_server: number;
    rollover_blackout_minutes_before: number | null;
    rollover_blackout_minutes_after: number | null;
    enforce_rollover_blackout_for_intraday: boolean;
    symbol_currency_to_account_rate?: number;
    margin_currency_to_account_rate?: number;
    max_daily_loss_pct: number | null;
    max_open_positions_symbol: number | null;
    max_open_positions_total: number | null;
    max_quote_age_seconds: number | null;
    allow_weekend_trading: boolean;
    enable_news_guard: boolean;
    enable_holiday_guard: boolean;
    holiday_policy: string;
    news_block_minutes_before: number | null;
    news_block_minutes_after: number | null;
    block_medium_impact_news: boolean;
    news_medium_block_minutes_before: number | null;
    news_medium_block_minutes_after: number | null;
    news_warn_hours: number | null;
    fail_closed_on_calendar_error: boolean;
    custom_events_path: string | null;
  };
  trade_management: {
    enable_auto_breakeven: boolean;
    breakeven_trigger_r: number;
    breakeven_lock_points: number;
    enable_trailing_stop: boolean;
    trailing_atr_mult: number;
    trailing_step_points: number;
  };
}

export interface LogEntry {
  time: string;
  level: string;
  message: string;
}

export type NewsSentiment = "BULLISH" | "BEARISH" | "CONFLICTING" | "NEUTRAL" | "UNKNOWN";

export type PostNewsVerdict =
  | "PRE_NEWS"
  | "WAIT_SETTLE"
  | "TREND_CONTINUATION_BULLISH"
  | "TREND_CONTINUATION_BEARISH"
  | "REVERSAL_TRAP_BULLISH"
  | "REVERSAL_TRAP_BEARISH"
  | "CHOPPY_RANGE";

export interface EventImpactAnalysis {
  title: string;
  currency: string;
  impact: "HIGH" | "MEDIUM" | "LOW" | "HOLIDAY";
  event_time: string;
  source: string;
  actual: string | null;
  forecast: string | null;
  previous: string | null;
  actual_num: number | null;
  forecast_num: number | null;
  previous_num: number | null;
  surprise_pct: number | null;
  currency_score: number;
  symbol_score: number;
  symbol_bias: NewsSentiment;
  rule_label: string;
  explanation_th: string;
}

export interface PostNewsReaction {
  event_title: string;
  event_currency: string;
  event_time: string;
  minutes_since_release: number;
  symbol: string;
  fundamental_bias: NewsSentiment;
  fundamental_score: number;
  verdict: PostNewsVerdict;
  confidence_score: number;
  pre_close: number;
  latest_price: number;
  net_move_points: number;
  pre_atr_m5: number;
  impulse_atr_ratio: number;
  max_up_atr_ratio: number;
  max_down_atr_ratio: number;
  m5_direction: string;
  m15_direction: string;
  spread_normalized: boolean;
  wick_trap_detected: boolean;
  aligned_with_news: boolean | null;
  summary_th: string;
}

export interface NewsIntelligenceResponse {
  symbol: string;
  mt5_connected: boolean;
  bridge_status: {
    detected: boolean;
    path: string;
    event_count: number;
    updated_utc: string | null;
  };
  aggregate_score: number;
  aggregate_bias: NewsSentiment;
  cfd_matrix: Array<{
    symbol: string;
    score: number;
    bias: NewsSentiment;
    events_counted: number;
  }>;
  post_news_reaction: PostNewsReaction | null;
  events: EventImpactAnalysis[];
  warnings: string[];
}

export type PageKey =
  | "overview"
  | "orders"
  | "chart"
  | "strategy_lab"
  | "simulator"
  | "news_intel"
  | "calendar"
  | "settings"
  | "logs";

export type ThemeMode = "light" | "dark";

export type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral";

export const PAGE_LABELS: Record<PageKey, string> = {
  overview: "ภาพรวมบัญชี",
  orders: "ติดตามออเดอร์ & ประวัติจริง",
  chart: "กราฟราคาสด",
  strategy_lab: "กลยุทธ์เทรด (Offensive)",
  simulator: "ทดลองคำนวณ Lot",
  news_intel: "วิเคราะห์ข่าวและกราฟ",
  calendar: "ปฏิทินข่าวและวันหยุด",
  settings: "ตั้งค่าบอท",
  logs: "ประวัติการทำงาน",
};

export interface StrategyParamOption {
  value: number;
  label_th: string;
}

export interface StrategyParamSpec {
  key: string;
  label_th: string;
  hint_th: string;
  example_th?: string;
  recommended_th?: string;
  kind: "int" | "float";
  default: number;
  min_val: number;
  max_val: number;
  step: number;
  options?: StrategyParamOption[] | null;
}

export interface PocLevelOverlay {
  side: "BULL" | "BEAR";
  price: number;
  start_time: number;
  end_time: number;
  tested: boolean;
  net_delta: number;
}

export interface VolumeSpikeMarker {
  bar_idx: number;
  time: number;
  ratio: number;
  is_bull: number;
}

export interface StrategyDescriptor {
  name: string;
  display_name_th: string;
  description_th: string;
  recommended_timeframe?: string;
  param_specs: StrategyParamSpec[];
  default_params: Record<string, number>;
}

export interface DefensiveConflict {
  code: string;
  severity: "high" | "medium";
  field_label: string;
  current_value: string;
  recommended_value: string;
  detail_th: string;
}

export interface StrategiesResponse {
  ok: boolean;
  error?: string;
  active_strategy: string;
  active_display_name_th: string;
  active_params: Record<string, number>;
  strategies: StrategyDescriptor[];
  defensive_conflicts?: DefensiveConflict[];
  bot_timeframe?: string;
  live_account_equity?: number | null;
  live_account_currency?: string;
}

export interface PortfolioSummary {
  currency: string;
  starting_equity: number;
  ending_balance: number;
  ending_equity: number;
  net_profit_money: number;
  net_profit_pct: number;
  max_drawdown_money: number;
  max_drawdown_pct: number;
  lot_mode: string;
  risk_percent: number;
  account_leverage: number;
  min_lot: number;
  avg_lot: number;
  max_lot: number;
  last_lot: number;
  avg_margin_used: number;
  max_margin_used: number;
  last_margin_used: number;
  min_margin_level_pct: number | null;
  last_margin_level_pct: number | null;
  scaled_down_trades: number;
  below_min_lot_fallback_trades: number;
  risk_rejected_signals: number;
  account_blown?: boolean;
  blown_time?: number | null;
  blown_reason?: string | null;
  bars_until_blown?: number | null;
  closed_max_drawdown_pct?: number;
}

export interface BacktestTrade {
  side: "BUY" | "SELL";
  entry_time: number;
  exit_time: number;
  entry_price: number;
  sl_price: number;
  tp_price: number;
  exit_price: number;
  outcome: "WIN" | "LOSS" | "STOP_OUT" | "OPEN";
  r_multiple: number;
  bars_held: number;
  reason: string;
  lot?: number;
  margin_used?: number;
  margin_level_pct?: number;
  pnl_money?: number;
  equity_after?: number;
  risk_note?: string;
}

export interface BlockedReasonSample {
  time: number;
  side: string;
  detail: string;
}

export interface BlockedReasonDetail {
  reason: string;
  count: number;
  why_th: string;
  fix_th: string;
  samples: BlockedReasonSample[];
}

export interface BacktestReport {
  strategy_name: string;
  symbol: string;
  bars_tested: number;
  total_trades: number;
  wins: number;
  losses: number;
  open_trades: number;
  win_rate_pct: number;
  profit_factor: number | null;
  total_r: number;
  expectancy_r: number;
  max_drawdown_r: number;
  trades: BacktestTrade[];
  equity_curve_r: number[];
  latest_signal: ChartSignalOverlay | null;
  defensive_applied?: boolean;
  blocked_signals?: number;
  blocked_reasons?: Record<string, number>;
  blocked_details?: BlockedReasonDetail[];
  portfolio?: PortfolioSummary | null;
  closed_total_r?: number;
  open_r?: number;
  open_pnl_money?: number;
  closed_max_drawdown_r?: number;
  breakeven_loss_trades?: number;
}

export interface BacktestResponse {
  ok: boolean;
  error?: string;
  mode: "PURE_OFFENSIVE" | "DEFENSIVE_AWARE";
  range_mode?: "bars" | "calendar";
  start_date?: string | null;
  end_date?: string | null;
  actual_start_time?: number | null;
  actual_end_time?: number | null;
  range_notice_th?: string | null;
  symbol: string;
  timeframe: string;
  bars_requested: number;
  strategy_name: string;
  strategy_display_name_th: string;
  params: Record<string, number>;
  spec: SymbolSpec | null;
  candles: CandleBar[];
  report: BacktestReport;
  poc_levels?: PocLevelOverlay[];
  volume_spikes?: VolumeSpikeMarker[];
}

export const LOT_MODE_LABELS: Record<string, string> = {
  dynamic_risk: "ปรับ Lot ตามความเสี่ยง % อัตโนมัติ",
  fixed: "กำหนด Lot คงที่",
  dynamic_equity_step: "ขยับ Lot ตามขนาดพอร์ต",
};

export const CYCLE_STATUS_LABELS: Record<string, string> = {
  no_signal: "ยังไม่พบจังหวะเข้าเทรดจากกราฟ",
  blocked_by_preventive_guard: "พบสัญญาณ แต่ระบบป้องกันสั่งพักไว้ก่อน",
  rejected_by_risk_engine: "พบสัญญาณ แต่มัดจำ/ขนาดไม้ไม่ผ่านเกณฑ์ปลอดภัย",
  dry_run_ok: "จำลองผ่านครบทุกด่าน (พร้อมออกไม้)",
  executed: "ส่งคำสั่งเข้า MT5 สำเร็จ",
  execution_failed: "ส่งคำสั่งเข้า MT5 ไม่สำเร็จ",
  account_unavailable: "ยังไม่พบข้อมูลพอร์ตจาก MT5",
  symbol_unavailable: "ไม่พบข้อมูลสินค้านี้ใน MT5",
  rates_unavailable: "กำลังรอกราฟแท่งเทียนจาก MT5",
};

export const SWAP_MODE_LABELS: Record<number, string> = {
  0: "ไม่มี Swap",
  1: "คิดเป็นจุด (Points)",
  2: "สกุลเงินสินค้า",
  3: "สกุลเงินมัดจำ",
  4: "สกุลเงินบัญชี",
  5: "ดอกเบี้ย % (ปัจจุบัน)",
  6: "ดอกเบี้ย % (ตอนเปิด)",
};

export const DOW_LABELS: Record<number, string> = {
  0: "อาทิตย์",
  1: "จันทร์",
  2: "อังคาร",
  3: "พุธ",
  4: "พฤหัส",
  5: "ศุกร์",
  6: "เสาร์",
};

export const BIAS_META: Record<NewsSentiment, { label: string; tone: BadgeTone }> = {
  BULLISH: { label: "ผลบวก (มีแรงหนุนขึ้น)", tone: "success" },
  BEARISH: { label: "ผลลบ (มีแรงกดดันลง)", tone: "danger" },
  CONFLICTING: { label: "ข่าวขัดแย้งกัน", tone: "warning" },
  NEUTRAL: { label: "เป็นกลาง", tone: "neutral" },
  UNKNOWN: { label: "รอตัวเลขประกาศจริง", tone: "neutral" },
};

export const VERDICT_META: Record<PostNewsVerdict, { label: string; tone: BadgeTone }> = {
  PRE_NEWS: { label: "ยังไม่ถึงเวลาข่าวออก", tone: "neutral" },
  WAIT_SETTLE: { label: "รอกราฟนิ่ง 5 นาทีแรกให้ Spread ปกติ", tone: "warning" },
  TREND_CONTINUATION_BULLISH: { label: "ตลาดวิ่งตามข่าวจริง (ฝั่งขึ้น)", tone: "success" },
  TREND_CONTINUATION_BEARISH: { label: "ตลาดวิ่งตามข่าวจริง (ฝั่งลง)", tone: "danger" },
  REVERSAL_TRAP_BULLISH: { label: "ระวังหักมุม กวาดไส้ล่างแล้วดีดขึ้น", tone: "warning" },
  REVERSAL_TRAP_BEARISH: { label: "ระวังหักมุม กวาดไส้บนแล้วทุบลง", tone: "warning" },
  CHOPPY_RANGE: { label: "กราฟแกว่งซ้ายขวา ยังไม่เลือกทาง", tone: "neutral" },
};

export interface CandleBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ChartNewsMarker {
  title: string;
  currency: string;
  impact: "HIGH" | "MEDIUM";
  event_time: string;
  event_ts: number;
}

export interface ChartSignalOverlay {
  symbol: string;
  side: "BUY" | "SELL";
  entry_price: number;
  sl_price: number;
  tp_price: number;
  reason?: string;
}

export interface CandlesResponse {
  ok: boolean;
  error?: string;
  range_mode?: "bars" | "calendar";
  symbol?: string;
  timeframe?: string;
  actual_start_time?: number | null;
  actual_end_time?: number | null;
  range_notice_th?: string | null;
  server_utc_offset_seconds?: number;
  spec?: SymbolSpec;
  candles?: CandleBar[];
  positions?: OpenPosition[];
  last_signal?: ChartSignalOverlay | null;
  news_events?: ChartNewsMarker[];
}

export interface LiveOpenPosition extends OpenPosition {
  net_profit?: number;
}

export interface ClosedTradeRecord {
  position_id: number;
  symbol: string;
  side: "BUY" | "SELL";
  volume: number;
  magic: number;
  owned_by_bot: boolean;
  open_time: number;
  close_time: number;
  duration_seconds: number;
  entry_price: number;
  exit_price: number;
  gross_profit: number;
  commission: number;
  swap: number;
  fee: number;
  net_pnl: number;
  close_reason: string;
  comment: string;
}

export interface BalanceLedgerRecord {
  ticket: number;
  time: number;
  kind: string;
  amount: number;
  comment: string;
}

export interface ForwardJournalEntry {
  event_type: "ORDER" | "BLOCKED" | "RISK_REJECTED" | "ADJUSTMENT";
  status: string;
  dry_run: boolean;
  cycle: number;
  time: number;
  symbol: string;
  timeframe: string;
  strategy_name: string;
  side?: "BUY" | "SELL" | null;
  entry_price?: number | null;
  sl_price?: number | null;
  tp_price?: number | null;
  signal_reason?: string | null;
  lot?: number | null;
  margin_used?: number | null;
  margin_level_pct?: number | null;
  order_ticket?: number | null;
  executed_price?: number | null;
  retcode?: number | null;
  ticket?: number | null;
  action_name?: string | null;
  old_sl?: number | null;
  new_sl?: number | null;
  message?: string | null;
  reasons?: string[];
}

export interface OrdersHistorySummary {
  closed_count: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate_pct: number;
  gross_profit_money: number;
  total_commission: number;
  total_swap: number;
  net_realized_pnl: number;
  profit_factor: number | null;
  max_drawdown_money: number;
  total_volume: number;
  bot_trades_count: number;
  manual_trades_count: number;
  deposits_net: number;
  open_count: number;
  open_bot_count: number;
  open_unrealized_pnl: number;
}

export interface OrdersHistoryResponse {
  ok: boolean;
  error?: string;
  mt5_connected: boolean;
  currency: string;
  account_equity: number | null;
  account_balance: number | null;
  bot_magic_number: number;
  bot_state: {
    running: boolean;
    dry_run: boolean;
    symbol: string;
    timeframe: string;
    strategy_name: string;
    strategy_display_name: string;
    cycle_count: number;
    last_cycle_time: string | null;
    last_cycle: EvaluationCycle | null;
  };
  start_time: number;
  end_time: number;
  open_positions: LiveOpenPosition[];
  closed_trades: ClosedTradeRecord[];
  balance_events: BalanceLedgerRecord[];
  forward_journal: ForwardJournalEntry[];
  summary: OrdersHistorySummary;
}
