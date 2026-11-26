from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol

import pandas as pd

from cfd_bot.domain.models import (
    AccountState,
    CalendarCheckResult,
    EconomicEvent,
    ExecutionResult,
    LotMode,
    LotPlan,
    OpenPosition,
    OrderSide,
    PositionAdjustment,
    SymbolSpec,
    TradeSignal,
)


class RiskPolicyPort(Protocol):
    """Policy contract required by RiskEngine (structural typing)."""

    lot_mode: LotMode
    fixed_lot: float
    risk_percent: float
    use_equity_for_risk: bool
    equity_step_amount: float
    equity_step_lot: float
    max_lot_cap: Optional[float]
    min_sl_points: Optional[int]
    include_costs_in_risk_sizing: bool
    max_effective_account_leverage: Optional[float]
    max_effective_symbol_leverage: Optional[float]
    max_margin_usage_pct: Optional[float]
    min_margin_level_pct: Optional[float]
    min_stopout_buffer_multiplier: Optional[float]
    scale_down_lot_for_leverage: bool
    allow_fallback_formula_if_margin_api_unavailable: bool


class PreventivePolicyPort(Protocol):
    """Policy contract required by PreventiveGuard (structural typing)."""

    max_spread_points: Optional[float]
    max_spread_to_tp_ratio: Optional[float]
    max_spread_to_atr_ratio: Optional[float]
    commission_per_lot_roundtrip: Optional[float]
    auto_detect_commission: bool
    estimated_slippage_points: Optional[float]
    min_net_rr: Optional[float]
    max_swap_to_tp_ratio: Optional[float]
    swap_year_days: int
    swap_rollover_hour_server: int
    rollover_blackout_minutes_before: Optional[int]
    rollover_blackout_minutes_after: Optional[int]
    enforce_rollover_blackout_for_intraday: bool
    symbol_currency_to_account_rate: float
    margin_currency_to_account_rate: float
    max_daily_loss_pct: Optional[float]
    max_open_positions_symbol: Optional[int]
    max_open_positions_total: Optional[int]
    max_quote_age_seconds: Optional[int]
    allow_weekend_trading: bool
    enable_news_guard: bool
    enable_holiday_guard: bool
    holiday_policy: str
    news_block_minutes_before: Optional[int]
    news_block_minutes_after: Optional[int]
    block_medium_impact_news: bool
    news_medium_block_minutes_before: Optional[int]
    news_medium_block_minutes_after: Optional[int]
    news_warn_hours: Optional[int]
    calendar_lookahead_hours: int
    calendar_cache_ttl_seconds: int
    holiday_cache_ttl_seconds: int
    calendar_http_timeout_seconds: float
    fail_closed_on_calendar_error: bool
    custom_events_path: Optional[str]


class TradeManagementPolicyPort(Protocol):
    """Policy contract required for break-even and ATR trailing stop management."""

    enable_auto_breakeven: bool
    breakeven_trigger_r: float
    breakeven_lock_points: int
    enable_trailing_stop: bool
    trailing_atr_mult: float
    trailing_step_points: int


class MarketGatewayPort(Protocol):
    """Abstract market & broker gateway enabling offline simulation and deterministic tests."""

    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def resolve_symbol(self, symbol: str) -> str: ...
    def get_account_state(self) -> Optional[AccountState]: ...
    def get_symbol_spec(self, symbol: str) -> Optional[SymbolSpec]: ...
    def calc_margin(self, side: OrderSide, symbol: str, volume: float, price: float) -> Optional[float]: ...
    def calc_profit(
        self, side: OrderSide, symbol: str, volume: float, price_open: float, price_close: float
    ) -> Optional[float]: ...
    def get_open_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list[OpenPosition]: ...
    def get_deal_history(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]: ...
    def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame: ...
    def get_rates_range(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame: ...
    def order_check(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def order_send(self, request: dict[str, Any]) -> dict[str, Any]: ...


class OrderExecutorPort(Protocol):
    """Abstract order execution & active position management adapter."""

    def execute_market_order(
        self,
        spec: SymbolSpec,
        signal: TradeSignal,
        lot_plan: LotPlan,
        comment: str = "cfd-mt5-bot",
    ) -> ExecutionResult: ...

    def manage_open_positions(
        self,
        spec: SymbolSpec,
        positions: list[OpenPosition],
        trade_cfg: TradeManagementPolicyPort,
        commission_per_lot: float,
        estimated_slippage_points: float,
        atr: Optional[float] = None,
    ) -> list[PositionAdjustment]: ...

    def close_position(
        self,
        pos: OpenPosition,
        spec: SymbolSpec,
        comment: str = "ops-close",
    ) -> ExecutionResult: ...

    def move_position_to_breakeven(
        self,
        pos: OpenPosition,
        spec: SymbolSpec,
        lock_points: int,
        commission_per_lot: float,
        estimated_slippage_points: float,
    ) -> PositionAdjustment: ...


class CalendarPort(Protocol):
    """Abstract economic calendar & bank holiday provider."""

    def evaluate(
        self,
        spec: SymbolSpec,
        now: datetime,
        expected_hold_days: float = 0.0,
    ) -> CalendarCheckResult: ...

    def get_events(self, currencies: set[str], now: datetime) -> tuple[list[EconomicEvent], list[str]]: ...

    def get_bridge_status(self) -> dict[str, Any]: ...

    def save_manual_actual(
        self,
        title: str,
        currency: str,
        event_time_iso: str,
        actual: str,
        forecast: Optional[str] = None,
        previous: Optional[str] = None,
        impact: str = "HIGH",
    ) -> list[dict[str, Any]]: ...

    def invalidate_cache(self) -> None: ...


class StrategyPort(Protocol):
    """Abstract signal strategy interface."""

    @property
    def name(self) -> str: ...

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]: ...


# Backward-compatible Protocol aliases
MT5GatewayProtocol = MarketGatewayPort
CalendarServiceProtocol = CalendarPort
