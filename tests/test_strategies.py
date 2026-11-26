from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.domain.models import OrderSide, SymbolSpec
from cfd_bot.domain.strategies.session_breakout import SessionRangeBreakoutStrategy


def make_spec() -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        ask=2612.20,
        bid=2612.00,
        spread_points=20,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
    )


def build_synthetic_bars(breakout_side: OrderSide | None = OrderSide.BUY) -> pd.DataFrame:
    start = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    rows = []
    # Build 24 bars from 00:00 to 07:45 UTC (20-min steps -> 23 closed bars + 1 forming bar)
    # Wait: use 15-min bars from 00:00 to 07:30 UTC (31 bars total, last bar at 07:30 is forming)
    for i in range(31):
        ts = start + timedelta(minutes=15 * i)
        if ts.hour < 6:
            # Asian consolidation box between 2598 and 2604
            o, h, l, c = 2600.0, 2604.0, 2598.0, 2601.0
        elif i < 29:
            # 06:00 - 07:00 inside box
            o, h, l, c = 2601.0, 2603.5, 2599.5, 2602.0
        elif i == 29:
            # Latest closed bar (07:15 UTC, inside trade session 07..17)
            if breakout_side == OrderSide.BUY:
                o, h, l, c = 2602.0, 2613.0, 2601.5, 2612.0
            elif breakout_side == OrderSide.SELL:
                o, h, l, c = 2600.0, 2600.5, 2589.0, 2590.0
            else:
                o, h, l, c = 2601.0, 2603.0, 2600.0, 2602.0
        else:
            # Currently forming bar (07:30 UTC) - ignored by strategy
            o, h, l, c = 2612.0, 2612.5, 2611.5, 2612.1

        rows.append({"time": ts, "open": o, "high": h, "low": l, "close": c, "tick_volume": 100})

    return pd.DataFrame(rows)


class TestSessionRangeBreakoutStrategy(unittest.TestCase):
    def test_generates_buy_breakout_signal(self) -> None:
        strat = SessionRangeBreakoutStrategy(
            range_start_hour_utc=0,
            range_end_hour_utc=6,
            trade_start_hour_utc=7,
            trade_end_hour_utc=17,
        )
        df = build_synthetic_bars(OrderSide.BUY)
        sig = strat.generate_signal(make_spec(), df)

        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, OrderSide.BUY)
        self.assertLess(sig.sl_price, sig.entry_price)
        self.assertGreater(sig.tp_price, sig.entry_price)
        self.assertEqual(sig.expected_hold_days, 0.0)

    def test_generates_sell_breakout_signal(self) -> None:
        strat = SessionRangeBreakoutStrategy()
        spec = SymbolSpec(**{**make_spec().__dict__, "ask": 2590.20, "bid": 2590.00})
        df = build_synthetic_bars(OrderSide.SELL)
        sig = strat.generate_signal(spec, df)

        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, OrderSide.SELL)
        self.assertGreater(sig.sl_price, sig.entry_price)
        self.assertLess(sig.tp_price, sig.entry_price)

    def test_returns_none_without_breakout(self) -> None:
        strat = SessionRangeBreakoutStrategy()
        df = build_synthetic_bars(None)
        self.assertIsNone(strat.generate_signal(make_spec(), df))


class TestPluggableOffensiveStrategies(unittest.TestCase):
    def test_registry_lists_and_builds_strategies(self) -> None:
        from cfd_bot.domain.strategies import build_strategy, list_strategy_descriptors

        descriptors = list_strategy_descriptors()
        names = {d["name"] for d in descriptors}
        self.assertEqual(names, {"M5KernelPullback", "VolatilitySqueezeBreakout", "M5RangeScalper"})

        m5_strat = build_strategy("M5KernelPullback", {"fast_ema_period": "20", "min_adx": 22.0})
        self.assertEqual(m5_strat.name, "M5KernelPullback")
        self.assertEqual(m5_strat.get_params()["fast_ema_period"], 20)
        self.assertEqual(m5_strat.get_params()["min_adx"], 22.0)

        sq_strat = build_strategy("VolatilitySqueezeBreakout", {"min_adx": 16.0})
        self.assertEqual(sq_strat.name, "VolatilitySqueezeBreakout")
        self.assertEqual(sq_strat.get_params()["min_adx"], 16.0)

        m5_range = build_strategy("M5RangeScalper", {"channel_mode": 1, "tp_rr_multiplier": 1.5})
        self.assertEqual(m5_range.name, "M5RangeScalper")
        self.assertEqual(m5_range.recommended_timeframe, "M5")
        self.assertGreaterEqual(m5_range.recommended_history_bars, 780)

        with self.assertRaises(ValueError):
            build_strategy("SessionRangeBreakout")
        with self.assertRaises(ValueError):
            build_strategy("NonExistentStrategy")

    def test_ema_pullback_generates_buy_signal(self) -> None:
        from cfd_bot.domain.strategies.ema_pullback import EmaTrendPullbackStrategy

        start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        rows = []
        price = 2300.0
        # Steady uptrend so EMA50 > EMA200 with healthy separation
        for i in range(230):
            o = price
            c = price + 0.8
            h = c + 0.4
            l = o - 0.3
            rows.append({"time": start + timedelta(hours=i), "open": o, "high": h, "low": l, "close": c, "tick_volume": 1000})
            price = c

        # Mild pullback to touch fast EMA50 region while staying well above EMA200
        ema50_approx = price - 20.0
        pullback_closes = [price - 5.0, price - 10.0, price - 15.0, ema50_approx + 1.5]
        for j, target_c in enumerate(pullback_closes):
            idx = 230 + j
            o = price - 1.0 if j < 3 else target_c - 4.0
            c = target_c
            l = ema50_approx - 2.5 if j == 3 else min(o, c) - 0.5
            h = max(o, c) + 0.8
            rows.append({"time": start + timedelta(hours=idx), "open": o, "high": h, "low": l, "close": c, "tick_volume": 1100})
            price = c

        # Unclosed forming bar
        rows.append({"time": start + timedelta(hours=234), "open": price, "high": price + 0.5, "low": price - 0.5, "close": price + 0.2, "tick_volume": 100})

        df = pd.DataFrame(rows)
        spec = SymbolSpec(**{**make_spec().__dict__, "ask": price + 0.2, "bid": price})
        sig = EmaTrendPullbackStrategy(fast_ema_period=50, slow_ema_period=200, pullback_band_atr=1.5).generate_signal(spec, df)

        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, OrderSide.BUY)
        self.assertLess(sig.sl_price, sig.entry_price)
        self.assertGreater(sig.tp_price, sig.entry_price)

    def test_rsi_reversal_generates_buy_signal(self) -> None:
        from cfd_bot.domain.strategies.rsi_reversal import RsiMeanReversionStrategy

        start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        rows = []
        price = 2500.0
        for i in range(25):
            rows.append({"time": start + timedelta(hours=i), "open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "tick_volume": 800})
        # Sharp sell-off pushing RSI < 30
        for j in range(10):
            idx = 25 + j
            o = price
            c = price - 3.0
            rows.append({"time": start + timedelta(hours=idx), "open": o, "high": o + 0.2, "low": c - 0.3, "close": c, "tick_volume": 900})
            price = c
        # Rebound bar recovering RSI back above oversold zone
        rows.append({"time": start + timedelta(hours=35), "open": price, "high": price + 16.0, "low": price - 0.2, "close": price + 15.0, "tick_volume": 1200})
        price += 15.0
        # Forming bar
        rows.append({"time": start + timedelta(hours=36), "open": price, "high": price + 0.5, "low": price - 0.5, "close": price + 0.1, "tick_volume": 100})

        df = pd.DataFrame(rows)
        spec = SymbolSpec(**{**make_spec().__dict__, "ask": price + 0.1, "bid": price})
        sig = RsiMeanReversionStrategy(rsi_period=14, oversold_level=30.0, overbought_level=70.0).generate_signal(spec, df)

        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, OrderSide.BUY)
        self.assertLess(sig.sl_price, sig.entry_price)
        self.assertGreater(sig.tp_price, sig.entry_price)

    def test_pure_offensive_backtest_ignores_defensive_spread_and_tracks_r(self) -> None:
        from cfd_bot.domain.strategies.rsi_reversal import RsiMeanReversionStrategy
        from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

        start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        rows = []
        price = 2500.0
        # Alternating sharp swings that trigger multiple oversold/overbought reversals
        for cycle in range(5):
            base_idx = cycle * 24
            for k in range(10):
                c = price - 3.0
                rows.append({"time": start + timedelta(hours=base_idx + k), "open": price, "high": price + 0.4, "low": c - 0.4, "close": c, "tick_volume": 900})
                price = c
            for k in range(10, 14):
                c = price + 6.0
                rows.append({"time": start + timedelta(hours=base_idx + k), "open": price, "high": c + 0.5, "low": price - 0.4, "close": c, "tick_volume": 1000})
                price = c
            for k in range(14, 24):
                c = price + 2.5 if k % 2 == 0 else price - 1.5
                rows.append({"time": start + timedelta(hours=base_idx + k), "open": price, "high": max(price, c) + 0.4, "low": min(price, c) - 0.4, "close": c, "tick_volume": 850})
                price = c

        df = pd.DataFrame(rows)
        # Ridiculously wide spread that Defensive PreventiveGuard would block instantly
        wide_spread_spec = SymbolSpec(**{**make_spec().__dict__, "ask": price + 25.0, "bid": price})
        strat = RsiMeanReversionStrategy(rsi_period=6, oversold_level=35.0, overbought_level=65.0, tp_rr_multiplier=1.2)
        report = run_pure_offensive_backtest(
            strategy=strat,
            spec=wide_spread_spec,
            rates=df,
            server_utc_offset_seconds=7200,
            lookback_window=80,
        )

        self.assertEqual(report.bars_tested, len(df))
        self.assertGreaterEqual(report.total_trades, 1)
        self.assertEqual(report.wins + report.losses + report.open_trades, report.total_trades)
        self.assertEqual(len(report.trades), report.total_trades)
        first_raw_ts = int(df.iloc[0]["time"].timestamp())
        self.assertGreaterEqual(report.trades[0].entry_time, first_raw_ts - 7200)

    def test_lorentzian_ml_strictly_causal_without_future_leak(self) -> None:
        import numpy as np
        from cfd_bot.domain.strategies.lorentzian_ml import compute_lorentzian_series

        rng = np.random.default_rng(19)
        steps = rng.normal(0.0, 1.4, 220)
        close = 2450.0 + np.cumsum(steps)
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        rows = []
        for i, c in enumerate(close):
            o = float(c - steps[i] * 0.5)
            h = float(max(o, c) + abs(rng.normal(0.7, 0.3)))
            l = float(min(o, c) - abs(rng.normal(0.7, 0.3)))
            rows.append({"time": start + timedelta(minutes=15 * i), "open": o, "high": h, "low": l, "close": float(c), "tick_volume": 120})
        full_df = pd.DataFrame(rows)
        prefix_df = full_df.iloc[:150].copy()

        res_full = compute_lorentzian_series(full_df, neighbors_count=8, max_bars_back=120)
        res_prefix = compute_lorentzian_series(prefix_df, neighbors_count=8, max_bars_back=120)

        self.assertAlmostEqual(float(res_prefix.kernel_rq[149]), float(res_full.kernel_rq[149]), places=9)
        self.assertAlmostEqual(float(res_prefix.predictions[149]), float(res_full.predictions[149]), places=9)
        self.assertEqual(int(res_prefix.signals[149]), int(res_full.signals[149]))

    def test_structural_poc_tracks_mitigation_and_jdub_three_entry_models(self) -> None:
        import numpy as np
        from cfd_bot.domain.strategies.jdub_poc_lorentzian import JdubPocLorentzianStrategy
        from cfd_bot.domain.strategies.lorentzian_ml import LorentzianClassifierStrategy
        from cfd_bot.domain.strategies.structural_poc import analyze_structural_legs
        from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

        rng = np.random.default_rng(7)
        steps = rng.normal(0.0, 1.5, 600)
        close = 2500.0 + np.cumsum(steps)
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        rows = []
        for i, c in enumerate(close):
            o = float(c - steps[i] * 0.5)
            h = float(max(o, c) + abs(rng.normal(0.8, 0.4)))
            l = float(min(o, c) - abs(rng.normal(0.8, 0.4)))
            vol = 450 if i in (120, 320, 480) else int(100 + abs(rng.normal(50, 20)))
            rows.append({"time": start + timedelta(minutes=15 * i), "open": o, "high": h, "low": l, "close": float(c), "tick_volume": vol})
        df = pd.DataFrame(rows)

        struct = analyze_structural_legs(df, atr_period=5, swing_mult=2.5, bubble_mult=2.0)
        self.assertGreater(len(struct.pocs), 10)
        self.assertGreaterEqual(len(struct.volume_spikes), 1)
        self.assertTrue(any(p.tested_bar_idx is not None for p in struct.pocs))

        spec = SymbolSpec(**{**make_spec().__dict__, "bid": float(close[-1]), "ask": float(close[-1] + 0.25)})
        lor = LorentzianClassifierStrategy()
        r_lor = run_pure_offensive_backtest(lor, spec, df)
        self.assertGreaterEqual(r_lor.total_trades, 3)

        counts = []
        for model in (1, 2, 3):
            strat = JdubPocLorentzianStrategy(entry_model=model)
            rep = run_pure_offensive_backtest(strat, spec, df)
            self.assertGreaterEqual(rep.total_trades, 1)
            counts.append(rep.total_trades)
        # Entry Model 1 (Aggressive) >= Model 2 (PA) >= Model 3 (PA + 2-Bar Confirm)
        self.assertGreaterEqual(counts[0], counts[1])
        self.assertGreaterEqual(counts[1], counts[2])

    def test_m5_kernel_pullback_scalper_generates_m5_trades(self) -> None:
        import numpy as np
        from cfd_bot.domain.strategies import build_strategy, list_strategy_descriptors
        from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

        descriptors = {d["name"]: d for d in list_strategy_descriptors()}
        self.assertIn("M5KernelPullback", descriptors)

        # Synthetic uptrend with deeper 4-bar pullbacks down to EMA21 followed by bullish bounce
        rows = []
        price = 2600.0
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        for i in range(420):
            phase = i % 24
            if i < 240:
                step = 0.45 if i % 4 != 0 else -0.2
            elif phase in (14, 15, 16, 17):
                step = -1.1
            elif phase in (18, 19):
                step = 1.15
            else:
                step = 0.4
            o = price
            c = price + step
            h = max(o, c) + 0.3
            l = min(o, c) - (1.8 if phase in (17, 18) else 0.3)
            rows.append(
                {
                    "time": start + timedelta(minutes=5 * i),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "tick_volume": 120,
                }
            )
            price = c

        df = pd.DataFrame(rows)
        spec = SymbolSpec(**{**make_spec().__dict__, "bid": price, "ask": price + 0.2})
        strat = build_strategy("M5KernelPullback", {"require_ml_confirm": 0, "min_adx": 10.0, "pullback_band_atr": 0.6})
        report = run_pure_offensive_backtest(strat, spec, df)
        self.assertFalse(report.defensive_applied)
        self.assertEqual(report.blocked_signals, 0)
        self.assertGreaterEqual(report.total_trades, 2)
        self.assertEqual(report.bars_tested, len(df))

        # Defensive-Aware replay using PreventiveConfig & TradeManagementConfig blocks Net R:R < 1.5
        from cfd_bot.application.config import PreventiveConfig, RiskConfig, TradeManagementConfig

        prev_strict = PreventiveConfig(min_net_rr=1.5, allow_weekend_trading=True)
        rep_def = run_pure_offensive_backtest(
            strat,
            spec,
            df,
            include_defensive=True,
            preventive_cfg=prev_strict,
            risk_cfg=RiskConfig(min_sl_points=None),
            trade_cfg=TradeManagementConfig(),
        )
        self.assertTrue(rep_def.defensive_applied)
        self.assertGreaterEqual(rep_def.blocked_signals, 1)
        self.assertIn("Net R:R ต่ำกว่าเพดานตั้งค่าบอท", rep_def.blocked_reasons)

        # Warmup-preserving calendar evaluation: eval_start_ts restricts entries without losing EMA200 warmup
        cutoff_ts = int(df["time"].iloc[260].timestamp())
        rep_win = run_pure_offensive_backtest(strat, spec, df, eval_start_ts=cutoff_ts)
        self.assertEqual(rep_win.bars_tested, len(df) - 260)
        for tr in rep_win.trades:
            self.assertGreaterEqual(tr.entry_time, cutoff_ts)

    def test_backtest_stops_immediately_on_stop_out_or_blown_account(self) -> None:
        from cfd_bot.application.config import LotMode, RiskConfig
        from cfd_bot.domain.models import OrderSide, TradeSignal
        from cfd_bot.domain.strategies.base import BaseStrategy
        from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

        class AlwaysBuyStrategy(BaseStrategy):
            name = "AlwaysBuyTest"
            display_name_th = "Test"
            description_th = "Test"

            def generate_signal(self, spec, rates):
                px = float(rates.iloc[-1]["open"])
                return TradeSignal(
                    symbol=spec.name,
                    side=OrderSide.BUY,
                    entry_price=px,
                    sl_price=round(px - 10.0, 2),
                    tp_price=round(px + 20.0, 2),
                    reason="Test Buy",
                )

        t0 = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i in range(90):
            if i == 55:
                o, h, l, c = 2600.0, 2601.0, 2585.0, 2588.0
            else:
                o, h, l, c = 2600.0 + (i * 0.1), 2601.0 + (i * 0.1), 2599.5 + (i * 0.1), 2600.5 + (i * 0.1)
            rows.append(
                {
                    "time": t0 + timedelta(minutes=5 * i),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "tick_volume": 100,
                }
            )
        df = pd.DataFrame(rows)
        gold_spec = SymbolSpec(
            name="XAUUSDm",
            digits=2,
            point=0.01,
            ask=2600.0,
            bid=2600.0,
            spread_points=0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )
        risk_cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.10,
            max_lot_cap=None,
            max_effective_account_leverage=None,
            max_effective_symbol_leverage=None,
            max_margin_usage_pct=None,
            min_margin_level_pct=None,
        )
        rep_so = run_pure_offensive_backtest(
            AlwaysBuyStrategy(),
            gold_spec,
            df,
            risk_cfg=risk_cfg,
            initial_equity=25.0,
            account_leverage=1000,
            stop_out_pct=50.0,
        )
        self.assertTrue(rep_so.portfolio.account_blown)
        self.assertIsNotNone(rep_so.portfolio.blown_time)
        self.assertEqual(rep_so.total_trades, 1)
        self.assertEqual(rep_so.trades[0].outcome, "STOP_OUT")
        self.assertIsNone(rep_so.latest_signal)
        self.assertGreaterEqual(rep_so.portfolio.ending_balance, 0.0)
        self.assertLess(rep_so.portfolio.ending_balance, 25.0)

        # Even at 0% Stop Out, blowing balance floors cleanly at 0.00 and stops opening any 0.00-lot ghost trades
        rep_zero = run_pure_offensive_backtest(
            AlwaysBuyStrategy(),
            gold_spec,
            df,
            risk_cfg=risk_cfg,
            initial_equity=25.0,
            account_leverage=1000,
            stop_out_pct=0.0,
        )
        self.assertTrue(rep_zero.portfolio.account_blown)
        self.assertEqual(rep_zero.total_trades, 1)
        self.assertEqual(rep_zero.trades[0].outcome, "STOP_OUT")
        self.assertEqual(rep_zero.portfolio.ending_balance, 0.0)
        self.assertEqual(rep_zero.portfolio.ending_equity, 0.0)

    def test_volatility_squeeze_breakout_signals_and_context_parity(self) -> None:
        from cfd_bot.domain.strategies import build_strategy

        strat = build_strategy(
            "VolatilitySqueezeBreakout",
            {"donchian_period": 12, "bb_period": 12, "squeeze_lookback": 8, "min_adx": 10.0, "ema_period": 50},
        )
        self.assertEqual(strat.name, "VolatilitySqueezeBreakout")
        self.assertEqual(strat.recommended_timeframe, "H1")

        # Gentle uptrend -> tight squeeze consolidation -> sharp upward breakout during London session (UTC 08:00)
        t0 = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        rows = []
        px = 2500.0
        for i in range(95):
            if i < 65:
                o = px
                c = o + 0.8
                h = c + 0.4
                l = o - 0.3
            elif i < 92:
                # Tight sideways squeeze inside ATR band
                osc = 0.25 if i % 2 == 0 else -0.25
                o = px
                c = px + osc
                h = max(o, c) + 0.8
                l = min(o, c) - 0.8
            elif i == 92:
                # Decisive bullish Donchian breakout bar
                o = px
                c = px + 6.5
                h = c + 0.5
                l = o - 0.2
            else:
                o = px + 6.5
                c = o + 0.5
                h = c + 0.3
                l = o - 0.3
            rows.append(
                {
                    "time": t0 + timedelta(hours=i),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "tick_volume": 300,
                }
            )
            px = c

        # Place bar 92 inside active UTC 06..20 session by forcing timestamp hour=10 for bars 90..94
        df = pd.DataFrame(rows)
        for idx_fix in range(90, 95):
            df.loc[idx_fix, "time"] = datetime(2026, 11, 6, 10 + (idx_fix - 90), 0, tzinfo=timezone.utc)

        spec = SymbolSpec(
            name="XAUUSDm",
            digits=2,
            point=0.01,
            ask=float(df.iloc[-1]["open"]) + 0.2,
            bid=float(df.iloc[-1]["open"]),
            spread_points=20,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )

        # Bar 93 checks closed bar 92 via live generate_signal AND via precomputed O(N) context
        sub_rates = df.iloc[:94].copy()
        live_sig = strat.generate_signal(spec, sub_rates)
        self.assertIsNotNone(live_sig)
        self.assertEqual(live_sig.side, OrderSide.BUY)
        self.assertLess(live_sig.sl_price, live_sig.entry_price)
        self.assertGreater(live_sig.tp_price, live_sig.entry_price)

        ctx = strat.precompute_backtest_context(df.iloc[:-1])
        self.assertIsNotNone(ctx)
        ctx_sig = strat.generate_signal_from_context(spec, ctx, 93)
        self.assertIsNotNone(ctx_sig)
        self.assertEqual(ctx_sig.side, live_sig.side)
        self.assertEqual(ctx_sig.sl_price, live_sig.sl_price)
        self.assertEqual(ctx_sig.tp_price, live_sig.tp_price)

    def test_m5_range_scalper_signals_and_context_parity(self) -> None:
        import math
        from cfd_bot.domain.strategies import build_strategy

        strat = build_strategy(
            "M5RangeScalper",
            {
                "channel_mode": 1,
                "htf_minutes": 60,
                "htf_max_adx": 18.0,
                "channel_period": 20,
                "band_multiplier": 1.8,
                "rsi_buy_max": 48.0,
                "trade_start_hour_utc": 0,
                "trade_end_hour_utc": 24,
            },
        )
        self.assertEqual(strat.name, "M5RangeScalper")
        self.assertEqual(strat.recommended_timeframe, "M5")

        # 800 M5 bars in sideways H1 oscillation so H1 stays neutral (htf_dir == 0),
        # followed by sharp oversold dip & bullish reclaim closing inside lower Keltner Channel.
        t0 = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        rows = []
        px = 2600.0
        for i in range(805):
            if i < 795:
                target = 2600.0 + 1.6 * math.sin(i / 5.0)
                o = px
                c = target
                h = max(o, c) + 0.35
                l = min(o, c) - 0.35
                px = c
            elif i < 800:
                o = px
                c = px - 0.9
                h = o + 0.15
                l = c - 0.15
                px = c
            elif i == 800:
                # Sweep well below lower Keltner band then strong bullish reclaim close back inside band
                o = px
                l = px - 3.5
                c = px + 2.2
                h = c + 0.2
                px = c
            else:
                o = px
                c = o + 0.1
                h = c + 0.3
                l = o - 0.3
                px = c
            rows.append(
                {
                    "time": t0 + timedelta(minutes=5 * i),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "tick_volume": 300,
                }
            )

        df = pd.DataFrame(rows)
        spec = SymbolSpec(
            name="XAUUSDm",
            digits=2,
            point=0.01,
            ask=float(df.iloc[801]["open"]) + 0.2,
            bid=float(df.iloc[801]["open"]),
            spread_points=20,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )

        # Bar 801 inspects closed bar 800 via live generate_signal AND via O(N) context
        sub_rates = df.iloc[:802].copy()
        live_sig = strat.generate_signal(spec, sub_rates)
        self.assertIsNotNone(live_sig)
        self.assertEqual(live_sig.side, OrderSide.BUY)
        self.assertLess(live_sig.sl_price, live_sig.entry_price)
        self.assertGreater(live_sig.tp_price, live_sig.entry_price)

        ctx = strat.precompute_backtest_context(df.iloc[:-1])
        ctx_sig = strat.generate_signal_from_context(spec, ctx, 801)
        self.assertIsNotNone(ctx_sig)
        self.assertEqual(ctx_sig.side, live_sig.side)
        self.assertEqual(ctx_sig.sl_price, live_sig.sl_price)
        self.assertEqual(ctx_sig.tp_price, live_sig.tp_price)

    def test_backtest_honest_mtm_intra_mae_and_net_cost_win_classification(self) -> None:
        from cfd_bot.application.config import LotMode, PreventiveConfig, RiskConfig, TradeManagementConfig
        from cfd_bot.domain.models import OrderSide, TradeSignal
        from cfd_bot.domain.strategies import build_strategy
        from cfd_bot.domain.strategies.base import BaseStrategy
        from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

        # 1. Verify sl_max_atr cap and max_hold_bars Time-Stop in M5KernelPullback
        strat_capped = build_strategy("M5KernelPullback", {"sl_min_atr": 1.6, "sl_max_atr": 2.0, "max_hold_bars": 6})
        self.assertEqual(strat_capped.get_params()["sl_max_atr"], 2.0)
        self.assertEqual(strat_capped.get_params()["max_hold_bars"], 6)

        # 2. Verify MTM Total R, intra-trade floating MAE drawdown, BE net-loss classification & Time-Stop
        class ControlledEntryStrategy(BaseStrategy):
            name = "ControlledTest"
            display_name_th = "Controlled"
            description_th = "Controlled"
            max_hold_bars = 5

            def generate_signal(self, spec, rates):
                # Enter ONLY at specific bar index lengths
                n = len(rates)
                px = float(rates.iloc[-1]["open"])
                if n == 51:
                    # Trade 1: triggers Auto-BE -> closes at BE (+0.02 pts) where comm+slip > lock -> NET LOSS!
                    return TradeSignal(spec.name, OrderSide.BUY, px, round(px - 10.0, 2), round(px + 20.0, 2), reason="T1")
                if n == 56:
                    # Trade 2: dips -0.8R intra-trade before hitting TP +2.0R -> intra-trade MAE drawdown >= 0.8R
                    return TradeSignal(spec.name, OrderSide.BUY, px, round(px - 10.0, 2), round(px + 20.0, 2), reason="T2")
                if n == 60:
                    # Trade 3: held 5 bars flat inside [SL, TP] -> closed via Time-Stop
                    return TradeSignal(spec.name, OrderSide.BUY, px, round(px - 10.0, 2), round(px + 20.0, 2), reason="T3")
                if n == 68:
                    # Trade 4: stays OPEN at final bar with -0.5R floating loss -> included in total_r & profit_factor
                    return TradeSignal(spec.name, OrderSide.BUY, px, round(px - 10.0, 2), round(px + 20.0, 2), reason="T4")
                return None

        t0 = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i in range(70):
            o = h = l = c = 2600.0
            if i == 50:
                # Bar 50: opens 2600, rallies +11 (triggers BE >= 1.0R)
                o, h, l, c = 2600.0, 2611.0, 2599.5, 2610.5
            elif i == 51:
                # Bar 51: reverses to hit BE lock slightly above entry -> net money LOSS after comm+slip!
                o, h, l, c = 2610.5, 2611.0, 2599.0, 2600.0
            elif i == 55:
                # Bar 55: enters Trade 2 at 2600, drops to 2592 (-0.8R) without touching 2590 SL
                o, h, l, c = 2600.0, 2601.0, 2592.0, 2599.0
            elif i == 56:
                # Bar 56: surges straight to 2621 (+20 TP hit)
                o, h, l, c = 2599.0, 2621.0, 2599.0, 2620.0
            elif 59 <= i <= 63:
                # Bars 59..63: Trade 3 drifts sideways inside [SL, TP] and triggers Time-Stop at bar 63 (-2 USD drop)
                c = 2598.0 if i == 63 else 2599.5
                o, h, l = 2599.5, 2600.5, 2597.5
            elif i >= 67:
                # Bars 67..69 (3 bars < max_hold_bars=5): Trade 4 enters at 2600 and holds -5 USD (-0.5R) OPEN to the last bar
                o, h, l, c = 2600.0, 2600.5, 2595.0, 2595.0
            rows.append({"time": t0 + timedelta(minutes=5 * i), "open": o, "high": h, "low": l, "close": c, "tick_volume": 100})

        df = pd.DataFrame(rows)
        spec = SymbolSpec(
            name="XAUUSDm",
            digits=2,
            point=0.01,
            ask=2600.0,
            bid=2600.0,
            spread_points=0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )
        risk_cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.10,
            max_lot_cap=None,
            max_effective_account_leverage=None,
            max_margin_usage_pct=None,
            min_margin_level_pct=None,
        )
        prev_cfg = PreventiveConfig(
            min_net_rr=0.5,
            commission_per_lot_roundtrip=20.0,
            auto_detect_commission=False,
            estimated_slippage_points=0.0,
            allow_weekend_trading=True,
            enforce_rollover_blackout_for_intraday=False,
        )
        tm_cfg = TradeManagementConfig(
            enable_auto_breakeven=True,
            breakeven_trigger_r=1.0,
            breakeven_lock_points=1,
            enable_trailing_stop=False,
        )
        # First check Time-Stop & OPEN trade honesty in Pure Offensive (where BE/cost aren't active)
        rep_pure = run_pure_offensive_backtest(
            ControlledEntryStrategy(),
            spec,
            df,
            risk_cfg=risk_cfg,
            initial_equity=1000.0,
            account_leverage=1000,
        )
        self.assertGreaterEqual(rep_pure.open_trades, 1)
        self.assertLess(rep_pure.open_r, 0.0)
        self.assertAlmostEqual(rep_pure.total_r, round(rep_pure.closed_total_r + rep_pure.open_r, 2), places=2)
        self.assertTrue(any("[TIME STOP 5 แท่ง]" in t.reason for t in rep_pure.trades))
        self.assertGreater(rep_pure.max_drawdown_r, rep_pure.closed_max_drawdown_r)

        # Next verify Defensive True-BE & strict net-money WIN check even with zero lock extra slippage edge
        rep_def = run_pure_offensive_backtest(
            ControlledEntryStrategy(),
            spec,
            df,
            include_defensive=True,
            preventive_cfg=prev_cfg,
            risk_cfg=risk_cfg,
            trade_cfg=tm_cfg,
            initial_equity=1000.0,
            account_leverage=1000,
        )
        for tr in rep_def.trades:
            if tr.outcome == "WIN":
                self.assertGreater(tr.pnl_money, 0.0)


if __name__ == "__main__":
    unittest.main()
