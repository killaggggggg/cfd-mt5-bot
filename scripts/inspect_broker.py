"""
Live MT5 Broker Diagnostic & Preventive Sizing Inspector.

Connects to the currently open MT5 terminal (or credentials in .env) and prints:
- Account leverage vs actual per-symbol effective leverage (via order_calc_margin)
- Spread, Swap Long/Short, Swap Mode, 3-day rollover day, Volume limits, Tick values
- Simulated Fixed Lot and Dynamic Risk Lot plans with full Preventive cost breakdown

Usage:
    python scripts/inspect_broker.py --symbols XAUUSD EURUSD US30 --side BUY --hold-days 1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import load_config_from_env
from cfd_bot.domain.models import LotMode, OrderSide, SwapMode, SymbolSpec, TradeSignal
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.domain.risk_engine import RiskEngine
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService
from cfd_bot.infrastructure.mt5_gateway import LiveMT5Gateway


MT5_DOW_NAMES = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}


def compute_h1_atr(gw: LiveMT5Gateway, symbol: str, period: int = 14) -> Optional[float]:
    rates = gw.get_rates(symbol, "H1", period + 10)
    if rates.empty or len(rates) < period + 2:
        return None
    closed = rates.iloc[:-1]
    high = closed["high"].astype(float)
    low = closed["low"].astype(float)
    prev_close = closed["close"].astype(float).shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    val = tr.rolling(window=period).mean().iloc[-1]
    return float(val) if pd.notna(val) and val > 0 else None


def resolve_sl_tp_distances(
    gw: LiveMT5Gateway,
    spec: SymbolSpec,
    entry: float,
    sl_points: Optional[int],
    tp_points: Optional[int],
) -> tuple[float, float, Optional[float]]:
    atr = compute_h1_atr(gw, spec.name)
    if sl_points is not None:
        sl_dist = sl_points * spec.point
    elif atr is not None:
        sl_dist = 1.5 * atr
    else:
        sl_dist = entry * 0.003

    if tp_points is not None:
        tp_dist = tp_points * spec.point
    else:
        tp_dist = sl_dist * 2.0

    return round(sl_dist, spec.digits), round(tp_dist, spec.digits), atr


def inspect_symbols(
    symbols: list[str],
    side: OrderSide,
    hold_days: float,
    sl_points: Optional[int],
    tp_points: Optional[int],
    max_spread_pts_override: Optional[float],
) -> int:
    cfg = load_config_from_env()
    if max_spread_pts_override is not None:
        cfg.preventive.max_spread_points = max_spread_pts_override
    elif not (ROOT / ".env").exists():
        # In multi-asset diagnostic mode without .env, default to scale-invariant Spread/TP & Spread/ATR guards
        cfg.preventive.max_spread_points = 0.0

    gw = LiveMT5Gateway(cfg.mt5)
    if not gw.connect():
        print("ERROR: Could not connect to MetaTrader 5 terminal. Please open MT5 and log in to a Demo account.")
        return 1

    try:
        account = gw.get_account_state()
        if account is None:
            print("ERROR: Failed to read MT5 account info.")
            return 1

        print("=" * 78)
        print(
            f"Account #{account.login} | Server: {account.server} | Currency: {account.currency}\n"
            f"Balance: {account.balance:,.2f} | Equity: {account.equity:,.2f} | Free Margin: {account.margin_free:,.2f}\n"
            f"Account Leverage: 1:{account.leverage} | StopOut: {account.margin_so_so:.1f}% (Call: {account.margin_so_call:.1f}%)"
        )
        print("=" * 78)

        cal_service = EconomicCalendarService(cfg.preventive, cache_dir=ROOT / "data" / "cache")
        guard = PreventiveGuard(cfg.preventive, calendar_service=cal_service)
        risk = RiskEngine(cfg.risk)
        now = datetime.now(timezone.utc)
        positions = gw.get_open_positions()

        for raw_sym in symbols:
            spec = gw.get_symbol_spec(raw_sym)
            if spec is None:
                print(f"\n[!] Symbol '{raw_sym}' not found in MT5 Market Watch.")
                continue

            entry = spec.ask if side == OrderSide.BUY else spec.bid
            sl_dist, tp_dist, atr = resolve_sl_tp_distances(gw, spec, entry, sl_points, tp_points)
            if side == OrderSide.BUY:
                sl_price = round(entry - sl_dist, spec.digits)
                tp_price = round(entry + tp_dist, spec.digits)
            else:
                sl_price = round(entry + sl_dist, spec.digits)
                tp_price = round(entry - tp_dist, spec.digits)

            signal = TradeSignal(
                symbol=spec.name,
                side=side,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=hold_days,
                atr=atr,
                reason="Diagnostic simulation",
            )

            decision = guard.evaluate(account=account, spec=spec, signal=signal, open_positions=positions, now=now)
            dyn_plan = risk.compute_lot_plan(
                account=account,
                spec=spec,
                signal=signal,
                costs=decision.costs,
                open_positions=positions,
                gateway=gw,
                lot_mode_override=LotMode.DYNAMIC_RISK,
            )
            fix_plan = risk.compute_lot_plan(
                account=account,
                spec=spec,
                signal=signal,
                costs=decision.costs,
                open_positions=positions,
                gateway=gw,
                lot_mode_override=LotMode.FIXED,
            )

            snap = dyn_plan.leverage_snapshot
            costs = decision.costs
            try:
                swap_mode_name = SwapMode(spec.swap_mode).name
            except ValueError:
                swap_mode_name = str(spec.swap_mode)

            atr_str = f"{atr:.{spec.digits}f}" if atr else "N/A"
            offset_str = (
                f"UTC{spec.server_utc_offset_seconds / 3600.0:+.1f}h"
                if spec.server_utc_offset_seconds is not None
                else "unknown/closed"
            )
            comm_tag = "auto" if (cfg.preventive.auto_detect_commission and spec.detected_commission_per_lot is not None) else "cfg"
            print(f"\n--- {spec.name} ({side.value} @ {entry:.{spec.digits}f} | SL={sl_price:.{spec.digits}f} TP={tp_price:.{spec.digits}f} | H1 ATR={atr_str}) ---")
            print(
                f"Broker Specs   : Bid={spec.bid:.{spec.digits}f} Ask={spec.ask:.{spec.digits}f} "
                f"Spread={costs.spread_points:.1f} pts ({spec.spread_price:.{spec.digits}f}) | StopsLevel={spec.trade_stops_level} pts | ServerTZ={offset_str}"
            )
            print(
                f"Volume Rules   : min={spec.volume_min} max={spec.volume_max} step={spec.volume_step} | "
                f"Point={spec.point} TickSize={spec.effective_tick_size} TickVal(L/P)={spec.effective_tick_value_loss:.4f}/{spec.effective_tick_value_profit:.4f}"
            )
            print(
                f"Real Leverage  : Account=1:{account.leverage} | Actual Symbol Leverage=1:{snap.effective_symbol_leverage:.1f} "
                f"(Margin/1.0 lot=${snap.margin_per_lot:,.2f}, Notional/1.0 lot=${snap.notional_per_lot:,.2f})"
            )
            print(
                f"Swap Profile   : Mode={swap_mode_name} | Long={spec.swap_long} Short={spec.swap_short} | "
                f"3x Day={MT5_DOW_NAMES.get(spec.swap_rollover3days, spec.swap_rollover3days)}"
            )
            print(
                f"Costs (1.0 lot): Spread=${costs.spread_cost_per_lot:.2f} | Comm({comm_tag})=${costs.commission_per_lot:.2f} | "
                f"Slip=${costs.slippage_cost_per_lot:.2f} | Swap({costs.swap_days_projected:.1f}d)=${costs.swap_pnl_per_lot:+.2f} "
                f"| True BE={costs.true_breakeven_points:.1f} pts"
            )
            spread_atr_pct = f"{costs.spread_to_atr_ratio:.1%}" if costs.spread_to_atr_ratio is not None else "N/A"
            print(
                f"R:R Analysis   : Gross R:R={costs.gross_rr:.2f} -> Net R:R={costs.net_rr:.2f} | "
                f"Spread/TP={costs.spread_to_tp_ratio:.1%} | Spread/ATR={spread_atr_pct} | Swap/TP={costs.swap_to_tp_ratio:.1%}"
            )
            print(f"Preventive Gate: {'PASS' if decision.approved else 'BLOCKED'} | Reasons={decision.reasons} | Warnings={decision.warnings}")
            if decision.upcoming_events:
                ev_summary = ", ".join(
                    f"[{ev.impact.value} {ev.currency}] {ev.title} ({ev.local_date or ev.event_time.strftime('%m-%d %H:%M UTC')})"
                    for ev in decision.upcoming_events[:4]
                )
                print(f"Calendar/News  : {ev_summary}")
            else:
                print("Calendar/News  : No high/medium news or bank holidays in lookahead window")
            print(
                f"Dynamic Lot    : Approved={dyn_plan.approved} Lot={dyn_plan.lot} (raw={dyn_plan.raw_lot:.4f}) | "
                f"EstSL=-${dyn_plan.estimated_loss_at_sl:.2f} | MarginUsed=${snap.projected_margin_used:,.2f} "
                f"| MarginLvl={snap.projected_margin_level_pct:.1f}% | EffPortLev={snap.projected_effective_portfolio_leverage:.2f}x"
            )
            print(
                f"Fixed Lot      : Approved={fix_plan.approved} Lot={fix_plan.lot} (target={cfg.risk.fixed_lot}) | "
                f"Clamps={fix_plan.clamp_reasons} | Rejects={fix_plan.rejection_reasons}"
            )
    finally:
        gw.disconnect()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect MT5 CFD broker specs, leverage, and preventive sizing.")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "US30"], help="Symbols to inspect")
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY", help="Simulated order direction")
    parser.add_argument("--hold-days", type=float, default=1.0, help="Simulated holding days for swap projection")
    parser.add_argument("--sl-points", type=int, default=None, help="Optional fixed SL distance in points (default: 1.5x H1 ATR)")
    parser.add_argument("--tp-points", type=int, default=None, help="Optional fixed TP distance in points (default: 2.0x SL)")
    parser.add_argument("--max-spread-points", type=float, default=None, help="Optional raw max spread points override (0=disable)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        inspect_symbols(
            symbols=args.symbols,
            side=OrderSide(args.side),
            hold_days=args.hold_days,
            sl_points=args.sl_points,
            tp_points=args.tp_points,
            max_spread_pts_override=args.max_spread_points,
        )
    )
