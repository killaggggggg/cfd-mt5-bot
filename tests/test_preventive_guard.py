from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import PreventiveConfig
from cfd_bot.domain.models import AccountState, OrderSide, SwapMode, SymbolSpec, SymbolTradeMode, TradeSignal
from cfd_bot.domain.preventive_guard import PreventiveGuard


def make_account(equity: float = 10000.0) -> AccountState:
    return AccountState(
        login=123456,
        server="Demo-Server",
        currency="USD",
        currency_digits=2,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        margin_level=0.0,
        leverage=500,
    )


def make_spec(
    ask: float = 2600.30,
    bid: float = 2600.00,
    swap_long: float = -12.0,
    swap_short: float = 4.0,
    swap_mode: int = int(SwapMode.POINTS),
    rollover3days: int = 3,
    stops_level: int = 20,
    trade_mode: int = int(SymbolTradeMode.FULL),
    quote_time: datetime | None = None,
) -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        ask=ask,
        bid=bid,
        spread_points=round((ask - bid) / 0.01),
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
        trade_mode=trade_mode,
        trade_stops_level=stops_level,
        swap_long=swap_long,
        swap_short=swap_short,
        swap_mode=swap_mode,
        swap_rollover3days=rollover3days,
        quote_time=quote_time,
    )


class TestPreventiveGuard(unittest.TestCase):
    def test_approves_healthy_intraday_setup_and_computes_net_rr(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)  # Wednesday 10:00 UTC
        spec = make_spec(ask=2600.30, bid=2600.00, quote_time=now)
        signal = TradeSignal(
            symbol="XAUUSD",
            side=OrderSide.BUY,
            entry_price=2600.30,
            sl_price=2595.30,  # $5.00 SL -> $500/lot gross
            tp_price=2610.30,  # $10.00 TP -> $1000/lot gross
            expected_hold_days=0.0,
            atr=4.0,
        )
        cfg = PreventiveConfig(
            max_spread_points=60.0,
            max_spread_to_tp_ratio=0.20,
            max_spread_to_atr_ratio=0.35,
            commission_per_lot_roundtrip=7.0,
            estimated_slippage_points=5.0,  # $5/lot
            min_net_rr=1.50,
        )
        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=now)

        self.assertTrue(decision.approved, msg=f"Unexpected reasons: {decision.reasons}")
        self.assertAlmostEqual(decision.costs.spread_points, 30.0, places=2)
        self.assertAlmostEqual(decision.costs.spread_cost_per_lot, 30.0, places=2)
        self.assertAlmostEqual(decision.costs.gross_rr, 2.0, places=2)
        # Net TP = 1000 - 7 - 5 = 988; Net SL = 500 + 7 + 5 = 512 -> 988/512 = 1.9297
        self.assertAlmostEqual(decision.costs.net_rr, 988.0 / 512.0, places=3)
        # True BE = 30 pts spread + (7 + 5) / $1/pt = 42 pts
        self.assertAlmostEqual(decision.costs.true_breakeven_points, 42.0, places=2)

    def test_blocks_excessive_spread_and_spread_to_tp_ratio(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(ask=2600.80, bid=2600.00, quote_time=now)  # 80 pts spread
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.80, 2598.80, 2603.80, expected_hold_days=0.0)
        cfg = PreventiveConfig(max_spread_points=60.0, max_spread_to_tp_ratio=0.20)
        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=now)

        self.assertFalse(decision.approved)
        self.assertTrue(any("Spread" in r for r in decision.reasons))
        self.assertTrue(any("consumes" in r for r in decision.reasons))

    def test_zero_max_spread_points_uses_ratio_guard_only(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        # 3-digit Gold style: spread=220 pts ($0.22), SL=$10, TP=$20 -> Spread/TP=1.1%
        spec = make_spec(ask=2602.20, bid=2600.00, quote_time=now)
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2602.20, 2592.20, 2622.20, expected_hold_days=0.0)
        cfg = PreventiveConfig(max_spread_points=0.0, max_spread_to_tp_ratio=0.20, min_net_rr=1.5)
        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=now)
        self.assertTrue(decision.approved, msg=f"Unexpected reasons: {decision.reasons}")

    def test_triple_swap_on_wednesday_and_blocks_high_swap_to_tp(self) -> None:
        # 2026-11-25 is Wednesday (MT5 DAY_OF_WEEK_WEDNESDAY == 3)
        now = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)
        spec = make_spec(
            ask=2600.20,
            bid=2600.00,
            swap_long=-40.0,  # -40 pts/day = -$40/day/lot -> 3x on Wed = -$120/lot!
            swap_mode=int(SwapMode.POINTS),
            rollover3days=3,
            quote_time=now,
        )
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.20, 2597.20, 2605.20, expected_hold_days=1.0)
        cfg = PreventiveConfig(max_swap_to_tp_ratio=0.15, swap_rollover_hour_server=0)

        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=now)
        self.assertFalse(decision.approved)
        self.assertTrue(decision.costs.triple_swap_applied)
        self.assertEqual(decision.costs.swap_days_projected, 3.0)
        self.assertAlmostEqual(decision.costs.swap_cost_per_lot, 120.0, places=2)
        self.assertTrue(any("Projected swap consumes" in r for r in decision.reasons))

        # Thursday should charge only 1x rollover day (swap_cost=$40/lot)
        thursday = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)
        spec_thu = make_spec(
            ask=2600.20,
            bid=2600.00,
            swap_long=-40.0,
            swap_mode=int(SwapMode.POINTS),
            rollover3days=3,
            quote_time=thursday,
        )
        costs_thu = PreventiveGuard(cfg).analyze_costs(spec_thu, signal, now=thursday)
        self.assertFalse(costs_thu.triple_swap_applied)
        self.assertEqual(costs_thu.swap_days_projected, 1.0)
        self.assertAlmostEqual(costs_thu.swap_cost_per_lot, 40.0, places=2)

    def test_interest_swap_mode_and_positive_swap_credit(self) -> None:
        now = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)  # Thursday
        spec = make_spec(
            ask=2600.20,
            bid=2600.00,
            swap_short=5.0,  # +5% annualized
            swap_mode=int(SwapMode.INTEREST_CURRENT),
            rollover3days=3,
            quote_time=now,
        )
        signal = TradeSignal("XAUUSD", OrderSide.SELL, 2600.00, 2605.00, 2590.00, expected_hold_days=1.0)
        costs = PreventiveGuard(PreventiveConfig(swap_year_days=360)).analyze_costs(spec, signal, now=now)

        # Notional = 260,000 -> 5% / 360 = +36.111/day
        self.assertGreater(costs.swap_pnl_per_lot, 36.0)
        self.assertEqual(costs.swap_cost_per_lot, 0.0)
        self.assertEqual(costs.swap_to_tp_ratio, 0.0)

    def test_blocks_rollover_blackout_for_intraday(self) -> None:
        now = datetime(2026, 11, 25, 23, 50, tzinfo=timezone.utc)  # 10 mins before 00:00 rollover
        spec = make_spec(quote_time=now)
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.30, 2595.30, 2610.30, expected_hold_days=0.0)
        cfg = PreventiveConfig(
            swap_rollover_hour_server=0,
            rollover_blackout_minutes_before=20,
            rollover_blackout_minutes_after=15,
            enforce_rollover_blackout_for_intraday=True,
        )
        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=now)
        self.assertFalse(decision.approved)
        self.assertTrue(any("blackout" in r for r in decision.reasons))

    def test_blocks_stops_level_stale_quote_trade_mode_and_daily_drawdown(self) -> None:
        now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        stale_time = now - timedelta(seconds=120)
        spec = make_spec(
            ask=2600.20,
            bid=2600.00,
            stops_level=100,  # minimum 100 pts = $1.00 from Bid/Ask
            trade_mode=int(SymbolTradeMode.SHORTONLY),
            quote_time=stale_time,
        )
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.20, 2599.50, 2605.00, expected_hold_days=0.0)
        cfg = PreventiveConfig(max_quote_age_seconds=30, max_daily_loss_pct=3.0)
        guard = PreventiveGuard(cfg)

        # Initialize daily equity at 10,000, then drop to 9,650 (-3.5% DD)
        guard.reset_daily_baseline(10000.0, now)
        decision = guard.evaluate(make_account(equity=9650.0), spec, signal, now=now)

        self.assertFalse(decision.approved)
        joined = " | ".join(decision.reasons)
        self.assertIn("Daily drawdown", joined)
        self.assertIn("SHORT only", joined)
        self.assertIn("Stale quote", joined)
        self.assertIn("stops_level", joined)

    def test_server_utc_offset_adjusts_rollover_and_quote_freshness(self) -> None:
        # EET broker (UTC+2): at 21:50 UTC it is 23:50 server time -> inside 00:00 server rollover blackout!
        utc_now = datetime(2026, 11, 25, 21, 50, tzinfo=timezone.utc)
        server_tick_time = utc_now + timedelta(hours=2)  # 23:50 server time
        base_spec = make_spec(quote_time=server_tick_time)
        spec = SymbolSpec(**{**base_spec.__dict__, "server_utc_offset_seconds": 7200})
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.30, 2595.30, 2610.30, expected_hold_days=0.0)
        cfg = PreventiveConfig(
            swap_rollover_hour_server=0,
            rollover_blackout_minutes_before=20,
            rollover_blackout_minutes_after=15,
            enforce_rollover_blackout_for_intraday=True,
        )
        decision = PreventiveGuard(cfg).evaluate(make_account(), spec, signal, now=utc_now)
        self.assertFalse(decision.approved)
        self.assertTrue(any("blackout" in r for r in decision.reasons))
        self.assertFalse(any("Stale quote" in r for r in decision.reasons))

    def test_auto_detected_commission_overrides_fallback(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        base_spec = make_spec(quote_time=now)
        spec = SymbolSpec(**{**base_spec.__dict__, "detected_commission_per_lot": 0.0})
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.30, 2595.30, 2610.30, expected_hold_days=0.0)
        cfg = PreventiveConfig(commission_per_lot_roundtrip=7.0, auto_detect_commission=True)
        costs = PreventiveGuard(cfg).analyze_costs(spec, signal, now=now)
        self.assertEqual(costs.commission_per_lot, 0.0)

    def test_optional_none_guards_skip_all_optional_blocks(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        base_spec = make_spec(ask=2601.20, bid=2600.00, quote_time=now - timedelta(hours=2))
        spec = SymbolSpec(**{**base_spec.__dict__, "detected_commission_per_lot": 0.0})
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2601.20, 2595.00, 2603.00, expected_hold_days=0.0)
        cfg = PreventiveConfig(
            max_spread_points=None,
            max_spread_to_tp_ratio=None,
            max_spread_to_atr_ratio=None,
            commission_per_lot_roundtrip=None,
            estimated_slippage_points=None,
            min_net_rr=None,
            max_swap_to_tp_ratio=None,
            max_daily_loss_pct=None,
            max_open_positions_symbol=None,
            max_open_positions_total=None,
            max_quote_age_seconds=None,
            enable_news_guard=False,
            enable_holiday_guard=False,
        )
        guard = PreventiveGuard(cfg)
        guard.reset_daily_baseline(1000.0, now)
        decision = guard.evaluate(make_account(equity=500.0), spec, signal, now=now)
        self.assertTrue(decision.approved, decision.reasons)


if __name__ == "__main__":
    unittest.main()
