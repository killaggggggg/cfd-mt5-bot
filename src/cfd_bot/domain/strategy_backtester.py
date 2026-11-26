from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import pandas as pd

from cfd_bot.domain.models import (
    AccountState,
    LotMode,
    OrderSide,
    PreventiveCostBreakdown,
    SymbolSpec,
    TradeSignal,
)
from cfd_bot.domain.ports import (
    PreventivePolicyPort,
    RiskPolicyPort,
    TradeManagementPolicyPort,
)
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.domain.risk_engine import RiskEngine, estimate_notional_per_lot
from cfd_bot.domain.strategies.base import BaseStrategy
from cfd_bot.domain.strategies.lorentzian_ml import compute_atr_array


@dataclass(frozen=True)
class _DefaultSimRiskConfig:
    """Fallback RiskPolicyPort used when standalone backtest callers omit risk_cfg."""

    lot_mode: LotMode = LotMode.DYNAMIC_RISK
    fixed_lot: float = 0.01
    risk_percent: float = 1.0
    use_equity_for_risk: bool = True
    equity_step_amount: float = 1000.0
    equity_step_lot: float = 0.01
    max_lot_cap: Optional[float] = None
    min_sl_points: Optional[int] = None
    include_costs_in_risk_sizing: bool = True
    max_effective_account_leverage: Optional[float] = 20.0
    max_effective_symbol_leverage: Optional[float] = None
    max_margin_usage_pct: Optional[float] = 25.0
    min_margin_level_pct: Optional[float] = 500.0
    min_stopout_buffer_multiplier: Optional[float] = None
    scale_down_lot_for_leverage: bool = True
    allow_fallback_formula_if_margin_api_unavailable: bool = True


@dataclass(frozen=True)
class PortfolioSummary:
    """Account-level simulation summary across every executed backtest trade."""

    currency: str
    starting_equity: float
    ending_balance: float
    ending_equity: float
    net_profit_money: float
    net_profit_pct: float
    max_drawdown_money: float
    max_drawdown_pct: float
    lot_mode: str
    risk_percent: float
    account_leverage: int
    min_lot: float
    avg_lot: float
    max_lot: float
    last_lot: float
    avg_margin_used: float
    max_margin_used: float
    last_margin_used: float
    min_margin_level_pct: Optional[float]
    last_margin_level_pct: Optional[float]
    scaled_down_trades: int
    below_min_lot_fallback_trades: int
    risk_rejected_signals: int
    account_blown: bool = False
    blown_time: Optional[int] = None
    blown_reason: Optional[str] = None
    bars_until_blown: Optional[int] = None
    closed_max_drawdown_pct: float = 0.0


@dataclass(frozen=True)
class BacktestTrade:
    """Single simulated signal execution recorded during strategy replay."""

    side: OrderSide
    entry_time: int
    exit_time: int
    entry_price: float
    sl_price: float
    tp_price: float
    exit_price: float
    outcome: Literal["WIN", "LOSS", "STOP_OUT", "OPEN"]
    r_multiple: float
    bars_held: int
    reason: str
    lot: float = 0.0
    margin_used: float = 0.0
    margin_level_pct: float = 0.0
    pnl_money: float = 0.0
    equity_after: float = 0.0
    risk_note: str = ""


@dataclass(frozen=True)
class BacktestReport:
    """Performance report covering both signal R-multiples and account equity sizing."""

    strategy_name: str
    symbol: str
    bars_tested: int
    total_trades: int
    wins: int
    losses: int
    open_trades: int
    win_rate_pct: float
    profit_factor: Optional[float]
    total_r: float
    expectancy_r: float
    max_drawdown_r: float
    trades: list[BacktestTrade]
    equity_curve_r: list[dict[str, float | int]]
    latest_signal: Optional[TradeSignal]
    defensive_applied: bool = False
    blocked_signals: int = 0
    blocked_reasons: dict[str, int] = field(default_factory=dict)
    blocked_details: list[dict[str, Any]] = field(default_factory=list)
    portfolio: Optional[PortfolioSummary] = None
    closed_total_r: float = 0.0
    open_r: float = 0.0
    open_pnl_money: float = 0.0
    closed_max_drawdown_r: float = 0.0
    breakeven_loss_trades: int = 0


def _bar_ts(raw_time: Any, offset_seconds: int) -> int:
    return int(pd.Timestamp(raw_time).timestamp()) - offset_seconds


def _bucket_preventive_reason(raw_reason: str) -> str:
    low = raw_reason.lower()
    if "net r:r" in low or "net tp reward" in low:
        return "Net R:R ต่ำกว่าเพดานตั้งค่าบอท"
    if "spread" in low:
        return "Spread สูงเกินเพดาน/เทียบ TP"
    if "rollover" in low:
        return "ช่วง Rollover Blackout"
    if "weekend" in low:
        return "วันหยุดสุดสัปดาห์"
    if "daily drawdown" in low:
        return "ขาดทุนรายวันเกินเพดาน"
    if "stops_level" in low or "sl " in low:
        return "ระยะ SL/TP ใกล้กว่าระดับ Stops Level"
    return "ด่านป้องกัน Defensive อื่นๆ"


def _describe_block_category(
    tag: str,
    preventive_cfg: Optional[PreventivePolicyPort],
    risk_cfg: Optional[RiskPolicyPort],
) -> tuple[str, str]:
    """Explains in plain Thai why a Defensive guard blocked signals and how to adjust it."""
    if tag == "ช่วง Rollover Blackout" and preventive_cfg is not None:
        hr = int(getattr(preventive_cfg, "swap_rollover_hour_server", 0) or 0)
        before_m = int(getattr(preventive_cfg, "rollover_blackout_minutes_before", 0) or 0)
        after_m = int(getattr(preventive_cfg, "rollover_blackout_minutes_after", 0) or 0)
        return (
            f"สัญญาณเกิดขึ้นในช่วงเปลี่ยนวันตลาด (Server Hour {hr:02d}:00 น. ก่อน -{before_m} นาที ถึงหลัง +{after_m} นาที) "
            "ซึ่งเป็นช่วงสภาพคล่องต่ำ สเปรดทองคำมักถ่างกว้างผิดปกติและมีโอกาสโดนคิด Swap ข้ามคืน บอทจึงงดเปิดออเดอร์ใหม่ชั่วคราว",
            "หากต้องการให้เข้าเทรดช่วงนี้ด้วย สามารถปรับลดเวลา Rollover Blackout หรือปิด 'บังคับห้ามเข้าช่วง Rollover (Intraday)' ได้ในหน้า ตั้งค่าบอท",
        )
    if tag == "Net R:R ต่ำกว่าเพดานตั้งค่าบอท":
        min_rr = getattr(preventive_cfg, "min_net_rr", None)
        rr_text = f"{min_rr:.2f}" if min_rr is not None else "ที่ตั้งไว้"
        return (
            f"หลังหักต้นทุน Spread + Commission + Slippage แล้ว สัดส่วนกำไรต่อขาดทุนสุทธิ (Net R:R) เหลือต่ำกว่าเกณฑ์ MIN_NET_RR = {rr_text}",
            "กดปุ่ม 'แก้ไขค่าทับซ้อนในตั้งค่าบอทอัตโนมัติ' ด้านบน หรือปรับเพิ่มเป้ากำไร TP R:R / ลด MIN_NET_RR ในหน้า ตั้งค่าบอท",
        )
    if tag == "Spread สูงเกินเพดาน/เทียบ TP":
        return (
            "ค่า Spread ของโบรคเกอร์สูงเกินสัดส่วนระยะทำกำไร (TP) หรือ ATR ที่ตั้งจำกัดไว้ เพื่อกันไม่ให้ต้นทุนเปิดไม้กินกำไรมากเกินไป",
            "เพิ่ม TP R:R หรือผ่อนปรนเพดาน MAX_SPREAD_TO_TP_RATIO / MAX_SPREAD_TO_ATR_RATIO ในหน้า ตั้งค่าบอท",
        )
    if tag == "วันหยุดสุดสัปดาห์":
        return (
            "เวลาแท่งเทียนตรงกับช่วงปิดตลาดเสาร์-อาทิตย์ และปิดอนุญาตเทรดวันหยุดไว้ (ALLOW_WEEKEND_TRADING = false)",
            "เป็นพฤติกรรมป้องกันปกติของตลาดปิดวันหยุด ไม่จำเป็นต้องแก้ไข",
        )
    if tag == "ขาดทุนรายวันเกินเพดาน":
        return (
            "ผลจำลองในวันนั้นขาดทุนสะสมเกินเพดาน MAX_DAILY_LOSS_PCT ระบบจึงสั่งหยุดเปิดไม้เพิ่มชั่วคราวเพื่อปกป้องพอร์ต",
            "ปรับเพดานขาดทุนรายวันในหน้า ตั้งค่าบอท หรือเว้นว่างหากไม่ต้องการจำกัด",
        )
    if tag == "SL แคบกว่า MIN_SL_POINTS":
        min_sl = getattr(risk_cfg, "min_sl_points", None)
        return (
            f"ระยะตัดขาดทุน (SL) ของจังหวะนั้นแคบกว่าเกณฑ์ขั้นต่ำ MIN_SL_POINTS ({min_sl} จุด)",
            "เพิ่ม 'ระยะ SL ขั้นต่ำกันสะบัด (x ATR)' ในกลยุทธ์ หรือลด/ปิด MIN_SL_POINTS ในหน้า ตั้งค่าบอท",
        )
    if tag == "ขนาด Lot ต่ำกว่าขั้นต่ำ (ทุน/Risk% ไม่พอ)":
        rp = float(getattr(risk_cfg, "risk_percent", 1.0) or 1.0) if risk_cfg is not None else 1.0
        return (
            f"งบความเสี่ยงต่อไม้ ({rp:.2f}% ของพอร์ตจำลอง) น้อยกว่าระยะ SL ขั้นต่ำของ 0.01 Lot ระบบจึงไม่ปัดล็อตขึ้นเพื่อรักษาเพดานขาดทุน",
            "เพิ่มทุนพอร์ตจำลอง, เพิ่ม 'ความเสี่ยง % ต่อไม้' ในหน้า ตั้งค่าบอท, หรือลด 'ระยะ SL ขั้นต่ำ (x ATR)' ให้แคบลง",
        )
    if tag == "ไม่ผ่านเกณฑ์ Margin/Leverage":
        return (
            "ขนาด Lot ที่คำนวณได้ติดเพดาน Margin หรือ Max Effective Leverage ที่ตั้งไว้ในหน้า ตั้งค่าบอท",
            "เพิ่มทุนพอร์ตจำลอง หรือขยายเพดาน Leverage / Margin Usage ในหน้า ตั้งค่าบอท",
        )
    if tag == "ระยะ SL/TP ใกล้กว่าระดับ Stops Level":
        return (
            "ระยะ SL หรือ TP ใกล้ราคาตลาดเกินกว่าขั้นต่ำ (Stops/Freeze Level) ที่โบรคเกอร์อนุญาตให้ส่งคำสั่งได้",
            "เพิ่มค่า 'ระยะ SL ขั้นต่ำกันสะบัด (x ATR)' ของกลยุทธ์เพื่อให้ SL/TP ห่างราคาตลาดมากขึ้น",
        )
    return (
        "ไม่ผ่านเงื่อนไขความปลอดภัยข้อใดข้อหนึ่งจากหน้า ตั้งค่าบอท (เช่น จำนวนไม้สูงสุดต่อวัน/ต่อคู่เงิน หรือสิทธิ์เทรดของสัญลักษณ์)",
        "ดูรายละเอียดตัวอย่างเวลาที่ถูกบล็อกด้านล่าง และเช็กค่าที่เกี่ยวข้องในหน้า ตั้งค่าบอท",
    )


def _zero_cost_breakdown(spec: SymbolSpec, sig: TradeSignal) -> PreventiveCostBreakdown:
    """Constructs a zero-fee PreventiveCostBreakdown for Pure Offensive lot sizing."""
    sl_dist = abs(sig.entry_price - sig.sl_price)
    tp_dist = abs(sig.tp_price - sig.entry_price)
    tick_sz = spec.effective_tick_size if spec.effective_tick_size > 0 else (spec.point or 0.01)
    sl_val = (sl_dist / tick_sz) * spec.effective_tick_value_loss if tick_sz > 0 else 0.0
    tp_val = (tp_dist / tick_sz) * spec.effective_tick_value_profit if tick_sz > 0 else 0.0
    rr = (tp_val / sl_val) if sl_val > 0 else 0.0
    return PreventiveCostBreakdown(
        spread_points=0.0,
        spread_cost_per_lot=0.0,
        commission_per_lot=0.0,
        slippage_cost_per_lot=0.0,
        swap_rate_raw=0.0,
        swap_mode=int(spec.swap_mode),
        swap_days_projected=0.0,
        triple_swap_applied=False,
        swap_pnl_per_lot=0.0,
        swap_cost_per_lot=0.0,
        total_cost_per_lot=0.0,
        gross_sl_risk_per_lot=sl_val,
        gross_tp_reward_per_lot=tp_val,
        net_sl_risk_per_lot=sl_val,
        net_tp_reward_per_lot=tp_val,
        gross_rr=rr,
        net_rr=rr,
        spread_to_tp_ratio=0.0,
        swap_to_tp_ratio=0.0,
        spread_to_atr_ratio=None,
        true_breakeven_points=0.0,
    )


def _compute_trade_money_pnl(
    spec: SymbolSpec,
    is_buy: bool,
    entry: float,
    exit_price: float,
    lot: float,
    round_trip_cost_per_lot: float,
) -> float:
    """Calculates realized or floating PnL in deposit currency using symbol tick values."""
    if lot <= 0.0:
        return 0.0
    diff = (exit_price - entry) if is_buy else (entry - exit_price)
    tick_sz = spec.effective_tick_size if spec.effective_tick_size > 0 else (spec.point or 0.01)
    tick_val = spec.effective_tick_value_profit if diff >= 0.0 else spec.effective_tick_value_loss
    if tick_sz <= 0.0 or tick_val <= 0.0:
        return 0.0
    gross = (diff / tick_sz) * tick_val * lot
    return round(gross - (round_trip_cost_per_lot * lot), 2)


def _abs_risk_money(spec: SymbolSpec, risk_dist: float, lot: float) -> float:
    """Computes gross money risk at initial SL distance for 1R normalization."""
    if lot <= 0.0 or risk_dist <= 0.0:
        return 0.0
    tick_sz = spec.effective_tick_size if spec.effective_tick_size > 0 else (spec.point or 0.01)
    tick_val = spec.effective_tick_value_loss
    if tick_sz <= 0.0 or tick_val <= 0.0:
        return 0.0
    return (risk_dist / tick_sz) * tick_val * lot


def _compute_trade_r_and_money(
    spec: SymbolSpec,
    is_buy: bool,
    entry: float,
    exit_price: float,
    risk_dist: float,
    lot: float,
    rt_cost_per_lot: float,
    use_defensive: bool,
) -> tuple[float, float]:
    """Returns (r_multiple, pnl_money) where Defensive mode uses net PnL after costs."""
    pnl_money = _compute_trade_money_pnl(
        spec=spec,
        is_buy=is_buy,
        entry=entry,
        exit_price=exit_price,
        lot=lot,
        round_trip_cost_per_lot=rt_cost_per_lot,
    )
    raw_r = (((exit_price - entry) if is_buy else (entry - exit_price)) / risk_dist) if risk_dist > 0.0 else 0.0
    if use_defensive and lot > 0.0:
        initial_risk_money = _abs_risk_money(spec, risk_dist, lot) + (rt_cost_per_lot * lot)
        if initial_risk_money > 0.0:
            return (pnl_money / initial_risk_money), pnl_money
    return raw_r, pnl_money


def _compute_stop_out_price(
    spec: SymbolSpec,
    is_buy: bool,
    entry: float,
    lot: float,
    current_balance: float,
    rt_cost_per_lot: float,
    so_equity: float,
    bar_open: float,
    worst_px: float,
) -> float:
    """Computes the conservative price where equity hits broker Stop Out level."""
    if lot <= 0.0:
        return worst_px
    tick_sz = spec.effective_tick_size if spec.effective_tick_size > 0 else (spec.point or 0.01)
    tick_val = spec.effective_tick_value_loss
    if tick_sz <= 0.0 or tick_val <= 0.0:
        return worst_px
    rt_total = rt_cost_per_lot * lot
    target_gross = so_equity - current_balance + rt_total
    if target_gross >= 0.0:
        return bar_open if ((is_buy and bar_open <= entry) or ((not is_buy) and bar_open >= entry)) else entry
    abs_diff = abs(target_gross * tick_sz) / (tick_val * lot)
    raw_so_px = round(entry - abs_diff if is_buy else entry + abs_diff, spec.digits)
    if is_buy:
        return max(worst_px, min(bar_open, raw_so_px))
    return min(worst_px, max(bar_open, raw_so_px))


def run_pure_offensive_backtest(
    strategy: BaseStrategy,
    spec: SymbolSpec,
    rates: pd.DataFrame,
    server_utc_offset_seconds: int = 0,
    lookback_window: int = 260,
    include_defensive: bool = False,
    preventive_cfg: Optional[PreventivePolicyPort] = None,
    risk_cfg: Optional[RiskPolicyPort] = None,
    trade_cfg: Optional[TradeManagementPolicyPort] = None,
    initial_equity: Optional[float] = None,
    account_leverage: int = 100,
    account_currency: str = "USD",
    eval_start_ts: Optional[int] = None,
    stop_out_pct: float = 0.0,
) -> BacktestReport:
    """
    Runs a candle-by-candle replay of `strategy` over `rates`.

    By default (`include_defensive=False`), runs Pure Offensive with zero spread/guards
    and static SL/TP in R-multiples. When `include_defensive=True`, overlays broker spread,
    PreventiveGuard cost/safety gates, RiskConfig min_sl_points check, and
    TradeManagementConfig (Auto-Breakeven & ATR Trailing Stop).
    """
    use_defensive = bool(include_defensive and preventive_cfg is not None)
    start_equity = round(float(initial_equity), 2) if (initial_equity is not None and initial_equity > 0) else 10000.0
    eff_leverage = max(1, int(account_leverage or 100))
    eff_currency = (account_currency or "USD").strip() or "USD"
    eff_risk_cfg = risk_cfg or _DefaultSimRiskConfig()
    risk_engine = RiskEngine(eff_risk_cfg)

    def _empty_portfolio() -> PortfolioSummary:
        return PortfolioSummary(
            currency=eff_currency,
            starting_equity=start_equity,
            ending_balance=start_equity,
            ending_equity=start_equity,
            net_profit_money=0.0,
            net_profit_pct=0.0,
            max_drawdown_money=0.0,
            max_drawdown_pct=0.0,
            lot_mode=str(eff_risk_cfg.lot_mode.value if hasattr(eff_risk_cfg.lot_mode, "value") else eff_risk_cfg.lot_mode),
            risk_percent=float(eff_risk_cfg.risk_percent),
            account_leverage=eff_leverage,
            min_lot=0.0,
            avg_lot=0.0,
            max_lot=0.0,
            last_lot=0.0,
            avg_margin_used=0.0,
            max_margin_used=0.0,
            last_margin_used=0.0,
            min_margin_level_pct=None,
            last_margin_level_pct=None,
            scaled_down_trades=0,
            below_min_lot_fallback_trades=0,
            risk_rejected_signals=0,
        )

    if rates.empty or len(rates) < 35:
        return BacktestReport(
            strategy_name=strategy.name,
            symbol=spec.name,
            bars_tested=len(rates),
            total_trades=0,
            wins=0,
            losses=0,
            open_trades=0,
            win_rate_pct=0.0,
            profit_factor=None,
            total_r=0.0,
            expectancy_r=0.0,
            max_drawdown_r=0.0,
            trades=[],
            equity_curve_r=[],
            latest_signal=None,
            defensive_applied=use_defensive,
            blocked_signals=0,
            blocked_reasons={},
            blocked_details=[],
            portfolio=_empty_portfolio(),
        )

    df = rates.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], utc=True)

    # Convert bar times into true UTC epoch seconds once up-front for chart markers
    utc_times = [_bar_ts(t, server_utc_offset_seconds) for t in df["time"]]
    opens = [float(v) for v in df["open"]]
    highs = [float(v) for v in df["high"]]
    lows = [float(v) for v in df["low"]]
    closes = [float(v) for v in df["close"]]
    atr14_arr = (
        compute_atr_array(
            df["high"].to_numpy(dtype=float),
            df["low"].to_numpy(dtype=float),
            df["close"].to_numpy(dtype=float),
            14,
        )
        if use_defensive and trade_cfg is not None and trade_cfg.enable_trailing_stop
        else None
    )

    n_bars = len(df)
    base_start_idx = min(max(32, min(n_bars - 2, 50)), n_bars - 1)
    start_idx = base_start_idx
    bars_tested = n_bars
    if eval_start_ts is not None:
        first_eval_idx = next((i for i, ts in enumerate(utc_times) if ts >= eval_start_ts), n_bars)
        start_idx = max(base_start_idx, first_eval_idx)
        bars_tested = max(0, n_bars - first_eval_idx)
    effective_window = max(lookback_window, 260)

    guard = PreventiveGuard(preventive_cfg, calendar_service=None) if use_defensive and preventive_cfg is not None else None
    spread_px = (
        spec.spread_price
        if spec.spread_price > 0
        else float(max(0, spec.spread_points)) * (spec.point if spec.point > 0 else 0.01)
    )
    point = spec.point if spec.point > 0 else 0.01

    trades: list[BacktestTrade] = []
    active_trade: Optional[dict[str, Any]] = None
    blocked_signals = 0
    blocked_reasons: dict[str, int] = {}
    blocked_samples: dict[str, list[dict[str, Any]]] = {}
    running_r = 0.0
    peak_r_mtm = 0.0
    max_intra_dd_r = 0.0
    current_balance = start_equity
    peak_money_mtm = start_equity
    max_intra_dd_money = 0.0
    max_intra_dd_pct = 0.0
    breakeven_loss_trades = 0
    scaled_down_trades = 0
    below_min_lot_fallback_trades = 0
    risk_rejected_signals = 0
    account_blown = False
    blown_time: Optional[int] = None
    blown_reason: Optional[str] = None
    bars_until_blown: Optional[int] = None
    eff_stop_out_pct = max(0.0, float(stop_out_pct or 0.0))
    strat_max_hold = max(0, int(getattr(strategy, "max_hold_bars", 0) or 0))
    min_stop_dist = max(spec.trade_stops_level, spec.trade_freeze_level) * point
    precomputed_ctx = strategy.precompute_backtest_context(df.iloc[:-1]) if n_bars > 60 else None

    for idx in range(start_idx, n_bars):
        if account_blown:
            break
        bar_ts = utc_times[idx]
        bar_open = opens[idx]
        bar_high = highs[idx]
        bar_low = lows[idx]
        bar_close = closes[idx]

        # 1. If not in a trade, inspect closed bars BEFORE idx using idx as forming bar open
        if active_trade is None:
            if use_defensive:
                bar_spec = replace(spec, ask=bar_open + spread_px, bid=bar_open, quote_time=None)
            else:
                bar_spec = replace(spec, ask=bar_open, bid=bar_open, spread_points=0)
            if precomputed_ctx is not None:
                sig = strategy.generate_signal_from_context(bar_spec, precomputed_ctx, idx)
            else:
                win_start = max(0, idx - effective_window)
                window_df = df.iloc[win_start : idx + 1]
                sig = strategy.generate_signal(bar_spec, window_df)
            if sig is not None:
                risk_dist = abs(sig.entry_price - sig.sl_price)
                if risk_dist > 0:
                    allowed = True
                    sim_acc = AccountState(
                        login=1,
                        server="Backtest",
                        currency=eff_currency,
                        currency_digits=2,
                        balance=current_balance,
                        equity=current_balance,
                        margin=0.0,
                        margin_free=current_balance,
                        margin_level=0.0,
                        leverage=eff_leverage,
                        trade_allowed=True,
                        trade_expert=True,
                    )
                    sim_costs: PreventiveCostBreakdown = _zero_cost_breakdown(bar_spec, sig)

                    if use_defensive and guard is not None:
                        if risk_cfg is not None and risk_cfg.min_sl_points and (risk_dist / point) < risk_cfg.min_sl_points:
                            blocked_signals += 1
                            tag = "SL แคบกว่า MIN_SL_POINTS"
                            blocked_reasons[tag] = blocked_reasons.get(tag, 0) + 1
                            bucket = blocked_samples.setdefault(tag, [])
                            if len(bucket) < 5:
                                bucket.append(
                                    {
                                        "time": bar_ts,
                                        "side": sig.side.value,
                                        "detail": f"SL {risk_dist / point:.1f} pts < MIN_SL_POINTS {risk_cfg.min_sl_points}",
                                    }
                                )
                            allowed = False
                        else:
                            dec = guard.evaluate(
                                account=sim_acc,
                                spec=bar_spec,
                                signal=sig,
                                open_positions=[],
                                now=datetime.fromtimestamp(bar_ts, tz=timezone.utc),
                            )
                            sim_costs = dec.costs
                            if not dec.approved:
                                allowed = False
                                blocked_signals += 1
                                raw_msg = dec.reasons[0] if dec.reasons else "Blocked by Defensive Guard"
                                tag = _bucket_preventive_reason(raw_msg) if dec.reasons else "ด่าน Defensive"
                                blocked_reasons[tag] = blocked_reasons.get(tag, 0) + 1
                                bucket = blocked_samples.setdefault(tag, [])
                                if len(bucket) < 5:
                                    bucket.append(
                                        {
                                            "time": bar_ts,
                                            "side": sig.side.value,
                                            "detail": raw_msg,
                                        }
                                    )

                    if allowed:
                        lot_plan = risk_engine.compute_lot_plan(
                            account=sim_acc,
                            spec=bar_spec,
                            signal=sig,
                            costs=sim_costs,
                            open_positions=[],
                            gateway=None,
                        )
                        trade_lot = 0.0
                        margin_used = 0.0
                        margin_level_pct = 0.0
                        risk_note = ""

                        if lot_plan.approved:
                            trade_lot = lot_plan.lot
                            margin_used = round(lot_plan.leverage_snapshot.projected_margin_used, 2)
                            raw_ml = lot_plan.leverage_snapshot.projected_margin_level_pct
                            margin_level_pct = round(raw_ml, 1) if math.isfinite(raw_ml) else 0.0
                            if lot_plan.clamp_reasons:
                                scaled_down_trades += 1
                                risk_note = lot_plan.clamp_reasons[0]
                        elif use_defensive:
                            allowed = False
                            risk_rejected_signals += 1
                            blocked_signals += 1
                            raw_rej = lot_plan.rejection_reasons[0] if lot_plan.rejection_reasons else "RiskEngine rejected lot"
                            tag = (
                                "ขนาด Lot ต่ำกว่าขั้นต่ำ (ทุน/Risk% ไม่พอ)"
                                if "below broker volume_min" in raw_rej.lower()
                                else "ไม่ผ่านเกณฑ์ Margin/Leverage"
                            )
                            blocked_reasons[tag] = blocked_reasons.get(tag, 0) + 1
                            bucket = blocked_samples.setdefault(tag, [])
                            if len(bucket) < 5:
                                bucket.append(
                                    {
                                        "time": bar_ts,
                                        "side": sig.side.value,
                                        "detail": raw_rej,
                                    }
                                )
                        else:
                            # Pure Offensive preserves exact signal count/R; simulate min-lot floor when balance is tight
                            if spec.volume_min > 0 and lot_plan.raw_lot > 0 and current_balance > 0:
                                below_min_lot_fallback_trades += 1
                                trade_lot = spec.volume_min
                                mpl = lot_plan.leverage_snapshot.margin_per_lot
                                margin_used = round(trade_lot * mpl, 2) if mpl > 0 else 0.0
                                margin_level_pct = (
                                    round((current_balance / margin_used) * 100.0, 1) if margin_used > 0 else 0.0
                                )
                                risk_note = f"งบเสี่ยงต่ำกว่า {spec.volume_min:.2f} Lot — จำลองด้วย {spec.volume_min:.2f} Lot"
                            else:
                                allowed = False

                        if allowed and trade_lot >= spec.volume_min and current_balance > 0:
                            rt_cost_per_lot = (
                                sim_costs.commission_per_lot + sim_costs.slippage_cost_per_lot
                                if use_defensive
                                else 0.0
                            )
                            so_equity = (
                                round(margin_used * (eff_stop_out_pct / 100.0), 4)
                                if (eff_stop_out_pct > 0.0 and margin_used > 0.0)
                                else 0.0
                            )
                            active_trade = {
                                "side": sig.side,
                                "entry_idx": idx,
                                "entry_time": bar_ts,
                                "entry_price": sig.entry_price,
                                "initial_sl": sig.sl_price,
                                "sl_price": sig.sl_price,
                                "tp_price": sig.tp_price,
                                "risk_dist": risk_dist,
                                "reason": sig.reason,
                                "moved_sl": False,
                                "lot": trade_lot,
                                "margin_used": margin_used,
                                "margin_level_pct": margin_level_pct,
                                "rt_cost_per_lot": rt_cost_per_lot,
                                "so_equity": so_equity,
                                "risk_note": risk_note,
                            }

        # 2. Evaluate intra-bar Stop Out, floating MAE drawdown & conservative stop/target exit on active trade
        if active_trade is not None:
            is_buy = active_trade["side"] == OrderSide.BUY
            sl = active_trade["sl_price"]
            tp = active_trade["tp_price"]
            entry = active_trade["entry_price"]
            risk_dist = active_trade["risk_dist"]
            trade_lot = active_trade["lot"]
            so_equity = active_trade["so_equity"]
            ask_shift = spread_px if (use_defensive and not is_buy) else 0.0
            eval_open = bar_open + ask_shift
            eval_high = bar_high + ask_shift
            eval_low = bar_low + ask_shift
            eval_close = bar_close + ask_shift
            worst_px = eval_low if is_buy else eval_high

            worst_pnl = _compute_trade_money_pnl(
                spec=spec,
                is_buy=is_buy,
                entry=entry,
                exit_price=worst_px,
                lot=trade_lot,
                round_trip_cost_per_lot=active_trade["rt_cost_per_lot"],
            )
            worst_eq = current_balance + worst_pnl
            hit_sl = (eval_low <= sl) if is_buy else (eval_high >= sl)
            hit_tp = (eval_high >= tp) if is_buy else (eval_low <= tp)
            bars_held_now = max(1, idx - active_trade["entry_idx"] + 1)

            stop_out_triggered = False
            so_price = worst_px
            if worst_eq <= so_equity:
                so_price = _compute_stop_out_price(
                    spec=spec,
                    is_buy=is_buy,
                    entry=entry,
                    lot=trade_lot,
                    current_balance=current_balance,
                    rt_cost_per_lot=active_trade["rt_cost_per_lot"],
                    so_equity=so_equity,
                    bar_open=eval_open,
                    worst_px=worst_px,
                )
                sl_reached_before_so = hit_sl and ((is_buy and sl > so_price) or ((not is_buy) and sl < so_price))
                if not sl_reached_before_so:
                    stop_out_triggered = True

            hit_time_stop = bool(
                not stop_out_triggered
                and not hit_sl
                and not hit_tp
                and strat_max_hold > 0
                and bars_held_now >= strat_max_hold
            )

            # Track intra-bar floating MAE drawdown against realized peak (prevents hiding intra-trade pain)
            mae_px = so_price if stop_out_triggered else (sl if hit_sl else worst_px)
            mae_r, mae_pnl = _compute_trade_r_and_money(
                spec=spec,
                is_buy=is_buy,
                entry=entry,
                exit_price=mae_px,
                risk_dist=risk_dist,
                lot=trade_lot,
                rt_cost_per_lot=active_trade["rt_cost_per_lot"],
                use_defensive=use_defensive,
            )
            dd_r_now = max(0.0, peak_r_mtm - (running_r + mae_r))
            if dd_r_now > max_intra_dd_r:
                max_intra_dd_r = dd_r_now
            worst_eq_now = max(0.0, current_balance + mae_pnl)
            dd_m_now = max(0.0, peak_money_mtm - worst_eq_now)
            if dd_m_now > max_intra_dd_money:
                max_intra_dd_money = dd_m_now
            if peak_money_mtm > 0.0:
                dd_p_now = (dd_m_now / peak_money_mtm) * 100.0
                if dd_p_now > max_intra_dd_pct:
                    max_intra_dd_pct = dd_p_now

            if stop_out_triggered or hit_sl or hit_tp or hit_time_stop:
                if stop_out_triggered:
                    exit_price = so_price
                elif hit_sl:
                    exit_price = sl
                elif hit_tp:
                    exit_price = tp
                else:
                    exit_price = eval_close

                r_mult, pnl_money = _compute_trade_r_and_money(
                    spec=spec,
                    is_buy=is_buy,
                    entry=entry,
                    exit_price=exit_price,
                    risk_dist=risk_dist,
                    lot=trade_lot,
                    rt_cost_per_lot=active_trade["rt_cost_per_lot"],
                    use_defensive=use_defensive,
                )
                if stop_out_triggered:
                    outcome: Literal["WIN", "LOSS", "STOP_OUT", "OPEN"] = "STOP_OUT"
                elif use_defensive and trade_lot > 0.0:
                    # Strictly net-profitable trades count as WIN (break-even net losses never inflate WR)
                    outcome = "WIN" if pnl_money > 0.0 else "LOSS"
                else:
                    outcome = "WIN" if r_mult > 0.0 else "LOSS"

                if hit_sl and active_trade["moved_sl"] and outcome == "LOSS":
                    breakeven_loss_trades += 1

                raw_next_balance = round(current_balance + pnl_money, 2)
                if raw_next_balance <= 0.0:
                    pnl_money = round(-current_balance, 2)
                    current_balance = 0.0
                else:
                    current_balance = raw_next_balance
                running_r += r_mult
                if running_r > peak_r_mtm:
                    peak_r_mtm = running_r
                if current_balance > peak_money_mtm:
                    peak_money_mtm = current_balance

                if outcome == "STOP_OUT":
                    reason_suffix = " [STOP OUT ล้างพอร์ต]"
                elif hit_time_stop:
                    reason_suffix = f" [TIME STOP {strat_max_hold} แท่ง]"
                elif active_trade["moved_sl"]:
                    reason_suffix = " [BE/Trail]"
                else:
                    reason_suffix = ""
                trades.append(
                    BacktestTrade(
                        side=active_trade["side"],
                        entry_time=active_trade["entry_time"],
                        exit_time=bar_ts,
                        entry_price=entry,
                        sl_price=active_trade["initial_sl"],
                        tp_price=tp,
                        exit_price=round(exit_price, spec.digits),
                        outcome=outcome,
                        r_multiple=round(r_mult, 2),
                        bars_held=bars_held_now,
                        reason=active_trade["reason"] + reason_suffix,
                        lot=trade_lot,
                        margin_used=active_trade["margin_used"],
                        margin_level_pct=active_trade["margin_level_pct"],
                        pnl_money=pnl_money,
                        equity_after=current_balance,
                        risk_note=active_trade["risk_note"],
                    )
                )
                active_trade = None

                # Terminate simulation permanently if account is stopped out, <= 0, or cannot afford broker min lot
                min_lot_notional = estimate_notional_per_lot(spec, bar_close) * max(spec.volume_min, 0.01)
                min_lot_margin = min_lot_notional / float(max(1, eff_leverage)) if min_lot_notional > 0 else 0.0
                if outcome == "STOP_OUT" or current_balance <= 0.0 or (
                    min_lot_margin > 0.0 and current_balance < min_lot_margin
                ):
                    account_blown = True
                    blown_time = bar_ts
                    eval_anchor = first_eval_idx if eval_start_ts is not None else start_idx
                    bars_until_blown = max(1, idx - eval_anchor + 1)
                    if outcome == "STOP_OUT":
                        blown_reason = (
                            f"โดน Stop Out ({eff_stop_out_pct:.0f}% Margin Level) เงินทุนลดเหลือ {current_balance:.2f} {eff_currency}"
                        )
                    elif current_balance <= 0.0:
                        blown_reason = f"ขาดทุนจนเงินทุนเหลือ 0.00 {eff_currency} (ล้างพอร์ต)"
                    else:
                        blown_reason = (
                            f"เงินทุนเหลือ {current_balance:.2f} {eff_currency} ต่ำกว่ามาร์จิ้นขั้นต่ำของ {spec.volume_min:.2f} Lot ({min_lot_margin:.2f} {eff_currency})"
                        )
                    break
            elif use_defensive and trade_cfg is not None:
                # 3. Post-bar Defensive Auto True Break-Even & ATR Trailing management matching OrderExecutor
                if trade_cfg.enable_auto_breakeven:
                    cur_sl = active_trade["sl_price"]
                    tick_sz = spec.effective_tick_size if spec.effective_tick_size > 0 else point
                    point_loss = (point / tick_sz) * spec.effective_tick_value_loss if tick_sz > 0 else 0.0
                    extra_cost_pts = (active_trade["rt_cost_per_lot"] / point_loss) if point_loss > 0 else 0.0
                    be_lock = (max(0, trade_cfg.breakeven_lock_points) + extra_cost_pts) * point
                    if is_buy and cur_sl < entry:
                        if (eval_high - entry) >= trade_cfg.breakeven_trigger_r * risk_dist:
                            new_be = round(entry + be_lock, spec.digits)
                            if new_be > cur_sl and new_be < eval_close and (eval_close - new_be) >= min_stop_dist:
                                active_trade["sl_price"] = new_be
                                active_trade["moved_sl"] = True
                    elif (not is_buy) and cur_sl > entry:
                        if (entry - eval_low) >= trade_cfg.breakeven_trigger_r * risk_dist:
                            new_be = round(entry - be_lock, spec.digits)
                            if new_be < cur_sl and new_be > eval_close and (new_be - eval_close) >= min_stop_dist:
                                active_trade["sl_price"] = new_be
                                active_trade["moved_sl"] = True

                if trade_cfg.enable_trailing_stop and atr14_arr is not None:
                    bar_atr = float(atr14_arr[idx])
                    if bar_atr > 0:
                        trail_dist = trade_cfg.trailing_atr_mult * bar_atr
                        step_dist = max(1, trade_cfg.trailing_step_points) * point
                        cur_sl = active_trade["sl_price"]
                        if is_buy:
                            cand = round(eval_close - trail_dist, spec.digits)
                            if cand > entry and cand >= cur_sl + step_dist and (eval_close - cand) >= min_stop_dist:
                                active_trade["sl_price"] = cand
                                active_trade["moved_sl"] = True
                        else:
                            cand = round(eval_close + trail_dist, spec.digits)
                            if cand < entry and cand <= cur_sl - step_dist and (cand - eval_close) >= min_stop_dist:
                                active_trade["sl_price"] = cand
                                active_trade["moved_sl"] = True

    # Mark-to-market open position at final candle if still running
    open_trades = 0
    unrealized_money = 0.0
    open_r = 0.0
    if active_trade is not None:
        open_trades = 1
        is_buy = active_trade["side"] == OrderSide.BUY
        mtm_exit = closes[-1] if is_buy else (closes[-1] + (spread_px if use_defensive else 0.0))
        entry = active_trade["entry_price"]
        risk_dist = active_trade["risk_dist"]
        unrealized_r, unrealized_money = _compute_trade_r_and_money(
            spec=spec,
            is_buy=is_buy,
            entry=entry,
            exit_price=mtm_exit,
            risk_dist=risk_dist,
            lot=active_trade["lot"],
            rt_cost_per_lot=active_trade["rt_cost_per_lot"],
            use_defensive=use_defensive,
        )
        open_r = round(unrealized_r, 2)
        trades.append(
            BacktestTrade(
                side=active_trade["side"],
                entry_time=active_trade["entry_time"],
                exit_time=utc_times[-1],
                entry_price=entry,
                sl_price=active_trade["initial_sl"],
                tp_price=active_trade["tp_price"],
                exit_price=round(mtm_exit, spec.digits),
                outcome="OPEN",
                r_multiple=open_r,
                bars_held=max(1, (n_bars - 1) - active_trade["entry_idx"] + 1),
                reason=active_trade["reason"],
                lot=active_trade["lot"],
                margin_used=active_trade["margin_used"],
                margin_level_pct=active_trade["margin_level_pct"],
                pnl_money=unrealized_money,
                equity_after=round(current_balance + unrealized_money, 2),
                risk_note=active_trade["risk_note"],
            )
        )

    closed_trades = [t for t in trades if t.outcome != "OPEN"]
    wins = sum(1 for t in closed_trades if t.outcome == "WIN")
    losses = sum(1 for t in closed_trades if t.outcome in ("LOSS", "STOP_OUT"))
    # Include mark-to-market open position in Total R and Profit Factor so open losses are never hidden
    gross_win_r = sum(t.r_multiple for t in trades if t.r_multiple > 0)
    gross_loss_r = abs(sum(t.r_multiple for t in trades if t.r_multiple < 0))

    closed_total_r = round(sum(t.r_multiple for t in closed_trades), 2)
    total_r = round(sum(t.r_multiple for t in trades), 2)
    win_rate = round((wins / len(closed_trades)) * 100.0, 1) if closed_trades else 0.0
    expectancy = round(total_r / len(trades), 2) if trades else 0.0
    profit_factor: Optional[float] = (
        round(gross_win_r / gross_loss_r, 2) if gross_loss_r > 0 else (round(gross_win_r, 2) if gross_win_r > 0 else None)
    )

    cum_r = 0.0
    peak_r = 0.0
    closed_max_dd_r = 0.0
    equity_curve: list[dict[str, float | int]] = []
    peak_money = start_equity
    closed_max_dd_money = 0.0
    closed_max_dd_pct = 0.0
    for t in closed_trades:
        cum_r = round(cum_r + t.r_multiple, 2)
        if cum_r > peak_r:
            peak_r = cum_r
        dd = round(peak_r - cum_r, 2)
        if dd > closed_max_dd_r:
            closed_max_dd_r = dd
        equity_curve.append({"time": t.exit_time, "equity_r": cum_r, "equity_money": t.equity_after})

        eq_m = t.equity_after
        if eq_m > peak_money:
            peak_money = eq_m
        dd_m = round(peak_money - eq_m, 2)
        if dd_m > closed_max_dd_money:
            closed_max_dd_money = dd_m
        if peak_money > 0:
            dd_p = round((dd_m / peak_money) * 100.0, 2)
            if dd_p > closed_max_dd_pct:
                closed_max_dd_pct = dd_p

    ending_balance = round(current_balance, 2)
    ending_equity = round(current_balance + unrealized_money, 2)
    if open_trades > 0:
        equity_curve.append({"time": utc_times[-1], "equity_r": round(cum_r + open_r, 2), "equity_money": ending_equity})
        dd_open_r = max(0.0, peak_r - (cum_r + open_r))
        if dd_open_r > max_intra_dd_r:
            max_intra_dd_r = dd_open_r
    if ending_equity < peak_money:
        dd_open = round(peak_money - ending_equity, 2)
        if dd_open > max_intra_dd_money:
            max_intra_dd_money = dd_open
        if peak_money > 0:
            max_intra_dd_pct = max(max_intra_dd_pct, (dd_open / peak_money) * 100.0)

    max_dd_r = round(max(closed_max_dd_r, max_intra_dd_r), 2)
    max_dd_money = round(max(closed_max_dd_money, max_intra_dd_money), 2)
    max_dd_pct = round(max(closed_max_dd_pct, max_intra_dd_pct), 2)

    lots = [t.lot for t in trades if t.lot > 0]
    margins = [t.margin_used for t in trades if t.margin_used > 0]
    margin_levels = [t.margin_level_pct for t in trades if t.margin_level_pct > 0]
    net_profit_money = round(ending_equity - start_equity, 2)
    net_profit_pct = round((net_profit_money / start_equity) * 100.0, 2) if start_equity > 0 else 0.0

    portfolio_summary = PortfolioSummary(
        currency=eff_currency,
        starting_equity=start_equity,
        ending_balance=ending_balance,
        ending_equity=ending_equity,
        net_profit_money=net_profit_money,
        net_profit_pct=net_profit_pct,
        max_drawdown_money=max_dd_money,
        max_drawdown_pct=max_dd_pct,
        lot_mode=str(eff_risk_cfg.lot_mode.value if hasattr(eff_risk_cfg.lot_mode, "value") else eff_risk_cfg.lot_mode),
        risk_percent=float(eff_risk_cfg.risk_percent),
        account_leverage=eff_leverage,
        min_lot=round(min(lots), 2) if lots else 0.0,
        avg_lot=round(sum(lots) / len(lots), 2) if lots else 0.0,
        max_lot=round(max(lots), 2) if lots else 0.0,
        last_lot=round(trades[-1].lot, 2) if trades else 0.0,
        avg_margin_used=round(sum(margins) / len(margins), 2) if margins else 0.0,
        max_margin_used=round(max(margins), 2) if margins else 0.0,
        last_margin_used=round(trades[-1].margin_used, 2) if trades else 0.0,
        min_margin_level_pct=round(min(margin_levels), 1) if margin_levels else None,
        last_margin_level_pct=round(trades[-1].margin_level_pct, 1) if (trades and trades[-1].margin_level_pct > 0) else None,
        scaled_down_trades=scaled_down_trades,
        below_min_lot_fallback_trades=below_min_lot_fallback_trades,
        risk_rejected_signals=risk_rejected_signals,
        account_blown=account_blown,
        blown_time=blown_time,
        blown_reason=blown_reason,
        bars_until_blown=bars_until_blown,
        closed_max_drawdown_pct=round(closed_max_dd_pct, 2),
    )

    latest_sig = None if account_blown else strategy.generate_signal(spec, df)
    blocked_details: list[dict[str, Any]] = []
    for tag, count in blocked_reasons.items():
        why_th, fix_th = _describe_block_category(tag, preventive_cfg, risk_cfg)
        blocked_details.append(
            {
                "reason": tag,
                "count": count,
                "why_th": why_th,
                "fix_th": fix_th,
                "samples": blocked_samples.get(tag, []),
            }
        )

    return BacktestReport(
        strategy_name=strategy.name,
        symbol=spec.name,
        bars_tested=bars_tested,
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        open_trades=open_trades,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        total_r=total_r,
        expectancy_r=expectancy,
        max_drawdown_r=max_dd_r,
        trades=trades,
        equity_curve_r=equity_curve,
        latest_signal=latest_sig,
        defensive_applied=use_defensive,
        blocked_signals=blocked_signals,
        blocked_reasons=blocked_reasons,
        blocked_details=blocked_details,
        portfolio=portfolio_summary,
        closed_total_r=closed_total_r,
        open_r=open_r,
        open_pnl_money=round(unrealized_money, 2),
        closed_max_drawdown_r=round(closed_max_dd_r, 2),
        breakeven_loss_trades=breakeven_loss_trades,
    )
