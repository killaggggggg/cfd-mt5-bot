from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Optional


class LotMode(str, Enum):
    """Supported position sizing automation modes."""

    FIXED = "fixed"
    DYNAMIC_RISK = "dynamic_risk"
    DYNAMIC_EQUITY_STEP = "dynamic_equity_step"


class OrderSide(str, Enum):
    """Order direction."""

    BUY = "BUY"
    SELL = "SELL"


class SymbolTradeMode(IntEnum):
    """Mirrors MT5 SYMBOL_TRADE_MODE_* constants."""

    DISABLED = 0
    LONGONLY = 1
    SHORTONLY = 2
    CLOSEONLY = 3
    FULL = 4


class SwapMode(IntEnum):
    """Mirrors MT5 SYMBOL_SWAP_MODE_* constants."""

    DISABLED = 0
    POINTS = 1
    CURRENCY_SYMBOL = 2
    CURRENCY_MARGIN = 3
    CURRENCY_DEPOSIT = 4
    INTEREST_CURRENT = 5
    INTEREST_OPEN = 6
    REOPEN_CURRENT = 7
    REOPEN_BID = 8


class StopOutMode(IntEnum):
    """Mirrors MT5 ACCOUNT_STOPOUT_MODE_* constants."""

    PERCENT = 0
    MONEY = 1


class NewsImpact(str, Enum):
    """Economic calendar event impact level."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    HOLIDAY = "HOLIDAY"


@dataclass(frozen=True)
class EconomicEvent:
    """Normalized economic release or bank holiday event (UTC timestamp)."""

    title: str
    currency: str
    impact: NewsImpact
    event_time: datetime
    is_all_day_holiday: bool = False
    is_major_market_holiday: bool = False
    local_date: Optional[str] = None
    source: str = "feed"
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None


class NewsSentiment(str, Enum):
    """Directional bias derived from economic rules or post-news price reaction."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    CONFLICTING = "CONFLICTING"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class PostNewsVerdict(str, Enum):
    """Post-news M5/M15 chart reaction classification."""

    PRE_NEWS = "PRE_NEWS"
    WAIT_SETTLE = "WAIT_SETTLE"
    TREND_CONTINUATION_BULLISH = "TREND_CONTINUATION_BULLISH"
    TREND_CONTINUATION_BEARISH = "TREND_CONTINUATION_BEARISH"
    REVERSAL_TRAP_BULLISH = "REVERSAL_TRAP_BULLISH"
    REVERSAL_TRAP_BEARISH = "REVERSAL_TRAP_BEARISH"
    CHOPPY_RANGE = "CHOPPY_RANGE"


@dataclass(frozen=True)
class EventImpactAnalysis:
    """Economic rule scoring for a single news release relative to a target symbol."""

    title: str
    currency: str
    impact: NewsImpact
    event_time: datetime
    source: str
    actual: Optional[str]
    forecast: Optional[str]
    previous: Optional[str]
    actual_num: Optional[float]
    forecast_num: Optional[float]
    previous_num: Optional[float]
    surprise_pct: Optional[float]
    currency_score: float
    symbol_score: float
    symbol_bias: NewsSentiment
    rule_label: str
    explanation_th: str


@dataclass(frozen=True)
class PostNewsReaction:
    """Combined Fundamental + M5/M15 Price Reaction assessment after a news release."""

    event_title: str
    event_currency: str
    event_time: datetime
    minutes_since_release: float
    symbol: str
    fundamental_bias: NewsSentiment
    fundamental_score: float
    verdict: PostNewsVerdict
    confidence_score: float
    pre_close: float
    latest_price: float
    net_move_points: float
    pre_atr_m5: float
    impulse_atr_ratio: float
    max_up_atr_ratio: float
    max_down_atr_ratio: float
    m5_direction: str
    m15_direction: str
    spread_normalized: bool
    wick_trap_detected: bool
    aligned_with_news: Optional[bool]
    summary_th: str


@dataclass(frozen=True)
class CalendarCheckResult:
    """Result of evaluating upcoming news and bank holidays for a symbol."""

    blocked: bool
    reasons: list[str]
    warnings: list[str]
    upcoming_events: list[EconomicEvent] = field(default_factory=list)


@dataclass(frozen=True)
class SymbolSpec:
    """Normalized broker specification snapshot for a CFD symbol."""

    name: str
    digits: int
    point: float
    ask: float
    bid: float
    spread_points: int
    volume_min: float
    volume_max: float
    volume_step: float
    trade_contract_size: float
    trade_tick_size: float
    trade_tick_value: float
    trade_tick_value_profit: float
    trade_tick_value_loss: float
    trade_calc_mode: int = 0
    trade_mode: int = int(SymbolTradeMode.FULL)
    filling_mode: int = 2
    trade_stops_level: int = 0
    trade_freeze_level: int = 0
    volume_limit: float = 0.0
    margin_initial: float = 0.0
    margin_maintenance: float = 0.0
    swap_long: float = 0.0
    swap_short: float = 0.0
    swap_mode: int = int(SwapMode.DISABLED)
    swap_rollover3days: int = 3  # Wednesday by default in MT5 (0=Sun..6=Sat)
    currency_base: str = ""
    currency_margin: str = ""
    currency_profit: str = ""
    quote_time: Optional[datetime] = None
    server_utc_offset_seconds: Optional[int] = None
    detected_commission_per_lot: Optional[float] = None

    @property
    def spread_price(self) -> float:
        if self.ask > 0 and self.bid > 0:
            return max(0.0, self.ask - self.bid)
        return max(0.0, self.spread_points * self.point)

    @property
    def effective_tick_size(self) -> float:
        return self.trade_tick_size if self.trade_tick_size > 0 else self.point

    @property
    def effective_tick_value_loss(self) -> float:
        if self.trade_tick_value_loss > 0:
            return self.trade_tick_value_loss
        return self.trade_tick_value

    @property
    def effective_tick_value_profit(self) -> float:
        if self.trade_tick_value_profit > 0:
            return self.trade_tick_value_profit
        return self.trade_tick_value


@dataclass(frozen=True)
class AccountState:
    """Live broker account snapshot."""

    login: int
    server: str
    currency: str
    currency_digits: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    leverage: int
    margin_so_call: float = 100.0
    margin_so_so: float = 50.0
    margin_so_mode: int = int(StopOutMode.PERCENT)
    trade_allowed: bool = True
    trade_expert: bool = True


@dataclass(frozen=True)
class OpenPosition:
    """Open position snapshot used for portfolio exposure and margin checks."""

    ticket: int
    symbol: str
    side: OrderSide
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float
    magic: int
    open_time: Optional[int] = None
    notional_value: Optional[float] = None


@dataclass(frozen=True)
class TradeSignal:
    """Strategy output passed into PreventiveGuard and RiskEngine."""

    symbol: str
    side: OrderSide
    entry_price: float
    sl_price: float
    tp_price: float
    expected_hold_days: float = 0.0
    atr: Optional[float] = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreventiveCostBreakdown:
    """Detailed pre-trade cost and R:R analysis in account currency."""

    spread_points: float
    spread_cost_per_lot: float
    commission_per_lot: float
    slippage_cost_per_lot: float
    swap_rate_raw: float
    swap_mode: int
    swap_days_projected: float
    triple_swap_applied: bool
    swap_pnl_per_lot: float
    swap_cost_per_lot: float
    total_cost_per_lot: float
    gross_sl_risk_per_lot: float
    gross_tp_reward_per_lot: float
    net_sl_risk_per_lot: float
    net_tp_reward_per_lot: float
    gross_rr: float
    net_rr: float
    spread_to_tp_ratio: float
    swap_to_tp_ratio: float
    spread_to_atr_ratio: Optional[float]
    true_breakeven_points: float


@dataclass(frozen=True)
class PreventiveDecision:
    """Gatekeeper result indicating whether a trade passes all preventive guards."""

    approved: bool
    reasons: list[str]
    warnings: list[str]
    costs: PreventiveCostBreakdown
    upcoming_events: list[EconomicEvent] = field(default_factory=list)


@dataclass(frozen=True)
class LeverageSnapshot:
    """Actual broker margin and effective leverage diagnostics for a proposed lot."""

    account_leverage: int
    margin_per_lot: float
    notional_per_lot: float
    effective_symbol_leverage: float
    projected_symbol_notional: float
    projected_effective_symbol_leverage: float
    projected_portfolio_notional: float
    projected_effective_portfolio_leverage: float
    projected_margin_used: float
    projected_free_margin: float
    projected_margin_level_pct: float


@dataclass(frozen=True)
class LotPlan:
    """Final lot sizing result after leverage, margin, and broker step normalization."""

    approved: bool
    lot: float
    raw_lot: float
    mode: LotMode
    risk_budget_money: float
    estimated_loss_at_sl: float
    estimated_profit_at_tp: float
    leverage_snapshot: LeverageSnapshot
    clamp_reasons: list[str]
    rejection_reasons: list[str]


ALL_FX_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD")

CURRENCY_TO_COUNTRY: dict[str, str] = {
    "USD": "US",
    "EUR": "DE",
    "GBP": "GB",
    "JPY": "JP",
    "AUD": "AU",
    "CAD": "CA",
    "CHF": "CH",
    "NZD": "NZ",
}


def extract_symbol_currencies(spec: SymbolSpec) -> set[str]:
    """Determines relevant economic calendar currencies for a CFD symbol."""
    currencies: set[str] = set()
    for raw in (spec.currency_base, spec.currency_profit, spec.currency_margin):
        code = raw.strip().upper()
        if code in CURRENCY_TO_COUNTRY:
            currencies.add(code)

    name_up = spec.name.upper()
    for code in ALL_FX_CURRENCIES:
        if code in name_up:
            currencies.add(code)

    if any(
        tag in name_up
        for tag in ("XAU", "XAG", "GOLD", "SILVER", "US30", "DJ30", "NAS100", "USTEC", "US500", "SPX", "USOIL", "WTI", "BTC", "ETH")
    ):
        currencies.add("USD")
    if any(tag in name_up for tag in ("GER40", "DE40", "DAX")):
        currencies.add("EUR")
    if any(tag in name_up for tag in ("UK100", "FTSE")):
        currencies.add("GBP")
    if any(tag in name_up for tag in ("JP225", "NIKKEI")):
        currencies.add("JPY")

    return currencies


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an order check / execution call."""

    executed: bool
    dry_run: bool
    retcode: int
    order_ticket: int
    volume: float
    price: float
    comment: str
    order_check_margin: float = 0.0
    order_check_free_margin: float = 0.0


@dataclass(frozen=True)
class PositionAdjustment:
    """Outcome of an active position SL protection move (True BE or ATR Trailing)."""

    ticket: int
    symbol: str
    action_name: str
    old_sl: float
    new_sl: float
    executed: bool
    dry_run: bool
    retcode: int
    comment: str

