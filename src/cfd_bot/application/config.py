from __future__ import annotations

import json
import os
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from cfd_bot.domain.models import LotMode

_EMPTY_SENTINELS = {"", "none", "null", "off", "disabled"}


def _env_json_dict(name: str) -> dict[str, Any]:
    """Parses an optional JSON object env var for persisted strategy parameters."""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _coerce_optional_number(value: Any) -> Any:
    """Converts blank strings or disable sentinels (`none`/`off`) to None for optional caps."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _EMPTY_SENTINELS:
        return None
    return value


class MT5ConnectionConfig(BaseModel):
    """MT5 terminal login and path configuration."""

    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    path: Optional[str] = None


class RiskConfig(BaseModel):
    """Lot sizing, broker leverage adaptation, and optional margin protection settings."""

    lot_mode: LotMode = LotMode.DYNAMIC_RISK
    fixed_lot: float = Field(default=0.01, ge=0.0)
    risk_percent: float = Field(default=1.0, gt=0.0, le=100.0)
    use_equity_for_risk: bool = True
    equity_step_amount: float = Field(default=1000.0, gt=0.0)
    equity_step_lot: float = Field(default=0.01, gt=0.0)
    max_lot_cap: Optional[float] = Field(default=1.00, ge=0.0)
    min_sl_points: Optional[int] = Field(default=50, ge=0)
    include_costs_in_risk_sizing: bool = True

    # Optional leverage & margin limits (None or <= 0 disables that specific cap)
    max_effective_account_leverage: Optional[float] = Field(default=20.0, ge=0.0)
    max_effective_symbol_leverage: Optional[float] = Field(default=15.0, ge=0.0)
    max_margin_usage_pct: Optional[float] = Field(default=25.0, ge=0.0, le=100.0)
    min_margin_level_pct: Optional[float] = Field(default=500.0, ge=0.0)
    min_stopout_buffer_multiplier: Optional[float] = Field(default=3.0, ge=0.0)
    scale_down_lot_for_leverage: bool = True
    allow_fallback_formula_if_margin_api_unavailable: bool = True

    @field_validator(
        "max_lot_cap",
        "min_sl_points",
        "max_effective_account_leverage",
        "max_effective_symbol_leverage",
        "max_margin_usage_pct",
        "min_margin_level_pct",
        "min_stopout_buffer_multiplier",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return _coerce_optional_number(v)


class PreventiveConfig(BaseModel):
    """Preventive cost guards for spread, swap, commission, slippage, and sessions."""

    # Spread (None or <= 0 disables each limit)
    max_spread_points: Optional[float] = Field(default=60.0, ge=0.0)
    max_spread_to_tp_ratio: Optional[float] = Field(default=0.25, ge=0.0, le=1.0)
    max_spread_to_atr_ratio: Optional[float] = Field(default=0.35, ge=0.0, le=1.0)

    # Commission, Slippage & Net R:R
    commission_per_lot_roundtrip: Optional[float] = Field(default=7.0, ge=0.0)
    auto_detect_commission: bool = True
    estimated_slippage_points: Optional[float] = Field(default=5.0, ge=0.0)
    min_net_rr: Optional[float] = Field(default=1.0, ge=0.0)

    # Swap & Rollover
    max_swap_to_tp_ratio: Optional[float] = Field(default=0.15, ge=0.0, le=1.0)
    swap_year_days: int = Field(default=360, ge=360, le=366)
    swap_rollover_hour_server: int = Field(default=0, ge=0, le=23)
    rollover_blackout_minutes_before: Optional[int] = Field(default=20, ge=0, le=120)
    rollover_blackout_minutes_after: Optional[int] = Field(default=15, ge=0, le=120)
    enforce_rollover_blackout_for_intraday: bool = True
    symbol_currency_to_account_rate: float = Field(default=1.0, gt=0.0)
    margin_currency_to_account_rate: float = Field(default=1.0, gt=0.0)

    # Safety & Circuit Breakers (None or <= 0 disables that cap)
    max_daily_loss_pct: Optional[float] = Field(default=3.0, ge=0.0, le=100.0)
    max_open_positions_symbol: Optional[int] = Field(default=1, ge=0)
    max_open_positions_total: Optional[int] = Field(default=3, ge=0)
    max_quote_age_seconds: Optional[int] = Field(default=30, ge=0)
    allow_weekend_trading: bool = False

    # Economic News & Bank Holiday Guard
    enable_news_guard: bool = True
    enable_holiday_guard: bool = True
    holiday_policy: str = "block"  # "block" | "warn"
    news_block_minutes_before: Optional[int] = Field(default=30, ge=0, le=240)
    news_block_minutes_after: Optional[int] = Field(default=30, ge=0, le=240)
    block_medium_impact_news: bool = False
    news_medium_block_minutes_before: Optional[int] = Field(default=15, ge=0, le=120)
    news_medium_block_minutes_after: Optional[int] = Field(default=15, ge=0, le=120)
    news_warn_hours: Optional[int] = Field(default=12, ge=0, le=72)
    calendar_lookahead_hours: int = Field(default=24, ge=1, le=168)
    calendar_cache_ttl_seconds: int = Field(default=3600, ge=60)
    holiday_cache_ttl_seconds: int = Field(default=604800, ge=3600)
    calendar_http_timeout_seconds: float = Field(default=5.0, gt=0.5, le=30.0)
    fail_closed_on_calendar_error: bool = False
    custom_events_path: Optional[str] = "config/custom_events.json"

    @field_validator(
        "max_spread_points",
        "max_spread_to_tp_ratio",
        "max_spread_to_atr_ratio",
        "commission_per_lot_roundtrip",
        "estimated_slippage_points",
        "min_net_rr",
        "max_swap_to_tp_ratio",
        "rollover_blackout_minutes_before",
        "rollover_blackout_minutes_after",
        "max_daily_loss_pct",
        "max_open_positions_symbol",
        "max_open_positions_total",
        "max_quote_age_seconds",
        "news_block_minutes_before",
        "news_block_minutes_after",
        "news_medium_block_minutes_before",
        "news_medium_block_minutes_after",
        "news_warn_hours",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return _coerce_optional_number(v)

    @model_validator(mode="after")
    def _validate_positions(self) -> "PreventiveConfig":
        if (
            self.max_open_positions_symbol is not None
            and self.max_open_positions_total is not None
            and self.max_open_positions_symbol > 0
            and self.max_open_positions_total > 0
            and self.max_open_positions_symbol > self.max_open_positions_total
        ):
            self.max_open_positions_total = self.max_open_positions_symbol
        return self


class TradeManagementConfig(BaseModel):
    """Active position protection: True Break-Even and ATR Trailing Stop."""

    enable_auto_breakeven: bool = True
    breakeven_trigger_r: float = Field(default=1.15, gt=0.0)
    breakeven_lock_points: int = Field(default=5, ge=0)
    enable_trailing_stop: bool = True
    trailing_atr_mult: float = Field(default=1.5, gt=0.0)
    trailing_step_points: int = Field(default=10, ge=1)


class StrategyConfig(BaseModel):
    """Pluggable offensive strategy selection and parameters (isolated from defensive guards)."""

    active_strategy: str = "M5KernelPullback"
    params: dict[str, Any] = Field(default_factory=dict)


class BotConfig(BaseModel):
    """Top-level bot configuration."""

    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    magic_number: int = 202611
    dry_run: bool = True
    poll_seconds: float = Field(default=5.0, gt=0.1)
    deviation_points: int = Field(default=20, ge=1)
    mt5: MT5ConnectionConfig = Field(default_factory=MT5ConnectionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    preventive: PreventiveConfig = Field(default_factory=PreventiveConfig)
    trade_management: TradeManagementConfig = Field(default_factory=TradeManagementConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_opt_float(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    if raw.strip().lower() in _EMPTY_SENTINELS:
        return None
    return float(raw)


def _env_opt_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    if raw.strip().lower() in _EMPTY_SENTINELS:
        return None
    return int(float(raw))


def load_config_from_env(env_path: Optional[str] = None) -> BotConfig:
    """Loads BotConfig from environment variables / optional .env file."""
    load_dotenv(dotenv_path=env_path)

    return BotConfig(
        symbol=os.getenv("BOT_SYMBOL", "XAUUSD"),
        timeframe=os.getenv("BOT_TIMEFRAME", "M5"),
        magic_number=_env_int("BOT_MAGIC_NUMBER", 202611) or 202611,
        dry_run=_env_bool("BOT_DRY_RUN", True),
        poll_seconds=_env_float("BOT_POLL_SECONDS", 5.0),
        deviation_points=_env_int("BOT_DEVIATION_POINTS", 20) or 20,
        mt5=MT5ConnectionConfig(
            login=_env_int("MT5_LOGIN", None),
            password=os.getenv("MT5_PASSWORD") or None,
            server=os.getenv("MT5_SERVER") or None,
            path=os.getenv("MT5_PATH") or None,
        ),
        risk=RiskConfig(
            lot_mode=LotMode(os.getenv("LOT_MODE", LotMode.DYNAMIC_RISK.value).strip().lower()),
            fixed_lot=_env_float("FIXED_LOT", 0.01),
            risk_percent=_env_float("RISK_PERCENT", 1.0),
            use_equity_for_risk=_env_bool("USE_EQUITY_FOR_RISK", True),
            equity_step_amount=_env_float("EQUITY_STEP_AMOUNT", 1000.0),
            equity_step_lot=_env_float("EQUITY_STEP_LOT", 0.01),
            max_lot_cap=_env_opt_float("MAX_LOT_CAP", 1.00),
            min_sl_points=_env_opt_int("MIN_SL_POINTS", 50),
            include_costs_in_risk_sizing=_env_bool("INCLUDE_COSTS_IN_RISK_SIZING", True),
            max_effective_account_leverage=_env_opt_float("MAX_EFFECTIVE_ACCOUNT_LEVERAGE", 20.0),
            max_effective_symbol_leverage=_env_opt_float("MAX_EFFECTIVE_SYMBOL_LEVERAGE", 15.0),
            max_margin_usage_pct=_env_opt_float("MAX_MARGIN_USAGE_PCT", 25.0),
            min_margin_level_pct=_env_opt_float("MIN_MARGIN_LEVEL_PCT", 500.0),
            min_stopout_buffer_multiplier=_env_opt_float("MIN_STOPOUT_BUFFER_MULTIPLIER", 3.0),
            scale_down_lot_for_leverage=_env_bool("SCALE_DOWN_LOT_FOR_LEVERAGE", True),
        ),
        preventive=PreventiveConfig(
            max_spread_points=_env_opt_float("MAX_SPREAD_POINTS", 60.0),
            max_spread_to_tp_ratio=_env_opt_float("MAX_SPREAD_TO_TP_RATIO", 0.25),
            max_spread_to_atr_ratio=_env_opt_float("MAX_SPREAD_TO_ATR_RATIO", 0.35),
            commission_per_lot_roundtrip=_env_opt_float("COMMISSION_PER_LOT_ROUNDTRIP", 7.0),
            auto_detect_commission=_env_bool("AUTO_DETECT_COMMISSION", True),
            estimated_slippage_points=_env_opt_float("ESTIMATED_SLIPPAGE_POINTS", 5.0),
            min_net_rr=_env_opt_float("MIN_NET_RR", 1.0),
            max_swap_to_tp_ratio=_env_opt_float("MAX_SWAP_TO_TP_RATIO", 0.15),
            swap_rollover_hour_server=_env_int("SWAP_ROLLOVER_HOUR_SERVER", 0) or 0,
            rollover_blackout_minutes_before=_env_opt_int("ROLLOVER_BLACKOUT_MINUTES_BEFORE", 20),
            rollover_blackout_minutes_after=_env_opt_int("ROLLOVER_BLACKOUT_MINUTES_AFTER", 15),
            enforce_rollover_blackout_for_intraday=_env_bool("ENFORCE_ROLLOVER_BLACKOUT_FOR_INTRADAY", True),
            max_daily_loss_pct=_env_opt_float("MAX_DAILY_LOSS_PCT", 3.0),
            max_open_positions_symbol=_env_opt_int("MAX_OPEN_POSITIONS_SYMBOL", 1),
            max_open_positions_total=_env_opt_int("MAX_OPEN_POSITIONS_TOTAL", 3),
            max_quote_age_seconds=_env_opt_int("MAX_QUOTE_AGE_SECONDS", 30),
            allow_weekend_trading=_env_bool("ALLOW_WEEKEND_TRADING", False),
            enable_news_guard=_env_bool("ENABLE_NEWS_GUARD", True),
            enable_holiday_guard=_env_bool("ENABLE_HOLIDAY_GUARD", True),
            holiday_policy=os.getenv("HOLIDAY_POLICY", "block").strip().lower(),
            news_block_minutes_before=_env_opt_int("NEWS_BLOCK_MINUTES_BEFORE", 30),
            news_block_minutes_after=_env_opt_int("NEWS_BLOCK_MINUTES_AFTER", 30),
            block_medium_impact_news=_env_bool("BLOCK_MEDIUM_IMPACT_NEWS", False),
            news_medium_block_minutes_before=_env_opt_int("NEWS_MEDIUM_BLOCK_MINUTES_BEFORE", 15),
            news_medium_block_minutes_after=_env_opt_int("NEWS_MEDIUM_BLOCK_MINUTES_AFTER", 15),
            news_warn_hours=_env_opt_int("NEWS_WARN_HOURS", 12),
            fail_closed_on_calendar_error=_env_bool("FAIL_CLOSED_ON_CALENDAR_ERROR", False),
            custom_events_path=os.getenv("CUSTOM_EVENTS_PATH", "config/custom_events.json") or None,
        ),
        trade_management=TradeManagementConfig(
            enable_auto_breakeven=_env_bool("ENABLE_AUTO_BREAKEVEN", True),
            breakeven_trigger_r=_env_float("BREAKEVEN_TRIGGER_R", 1.15),
            breakeven_lock_points=_env_int("BREAKEVEN_LOCK_POINTS", 5) or 0,
            enable_trailing_stop=_env_bool("ENABLE_TRAILING_STOP", True),
            trailing_atr_mult=_env_float("TRAILING_ATR_MULT", 1.5),
            trailing_step_points=_env_int("TRAILING_STEP_POINTS", 10) or 1,
        ),
        strategy=StrategyConfig(
            active_strategy=os.getenv("ACTIVE_STRATEGY", "M5KernelPullback").strip() or "M5KernelPullback",
            params=_env_json_dict("STRATEGY_PARAMS_JSON"),
        ),
    )
