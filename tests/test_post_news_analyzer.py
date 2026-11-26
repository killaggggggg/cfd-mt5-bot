from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import PreventiveConfig
from cfd_bot.domain.models import EconomicEvent, NewsImpact, NewsSentiment, PostNewsVerdict, SymbolSpec
from cfd_bot.domain.post_news_analyzer import PostNewsAnalyzer, parse_economic_number
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService


def make_spec(name: str, base: str, profit: str, digits: int = 5, point: float = 0.00001) -> SymbolSpec:
    return SymbolSpec(
        name=name,
        digits=digits,
        point=point,
        ask=1.10008 if digits == 5 else 2600.20,
        bid=1.10000 if digits == 5 else 2600.00,
        spread_points=8 if digits == 5 else 20,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100000.0 if digits == 5 else 100.0,
        trade_tick_size=point,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
        currency_base=base,
        currency_profit=profit,
        currency_margin=base,
        server_utc_offset_seconds=0,
    )


def build_post_news_rates(
    event_time: datetime,
    mode: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Builds deterministic M5 and M15 OHLCV DataFrames around an event timestamp."""
    rows5 = []
    start5 = event_time - timedelta(minutes=5 * 18)
    for i in range(22):
        ts = start5 + timedelta(minutes=5 * i)
        if ts < event_time:
            # Stable pre-news bars around 1.10000, ATR ~ 0.00040
            rows5.append({"time": ts, "open": 1.1000, "high": 1.1002, "low": 1.0998, "close": 1.1000, "tick_volume": 50})
        else:
            step_idx = int((ts - event_time).total_seconds() // 300)
            if mode == "trend_down":
                base = 1.1000 - 0.0003 * (step_idx + 1)
                rows5.append({"time": ts, "open": base + 0.0002, "high": base + 0.00025, "low": base - 0.00005, "close": base, "tick_volume": 300})
            elif mode == "lower_wick_trap":
                if step_idx == 0:
                    # Huge dump down (-0.0015 = ~3.75x ATR), then snaps right back up!
                    rows5.append({"time": ts, "open": 1.1000, "high": 1.1003, "low": 1.0985, "close": 1.1002, "tick_volume": 500})
                else:
                    rows5.append({"time": ts, "open": 1.1002, "high": 1.1008, "low": 1.1001, "close": 1.1007, "tick_volume": 250})
            else:
                rows5.append({"time": ts, "open": 1.1000, "high": 1.1001, "low": 1.0999, "close": 1.10005, "tick_volume": 60})

    df5 = pd.DataFrame(rows5)
    df15 = df5.iloc[::3].copy()
    if mode == "trend_down":
        df15.iloc[-1, df15.columns.get_loc("close")] = 1.0988
    elif mode == "lower_wick_trap":
        df15.iloc[-1, df15.columns.get_loc("close")] = 1.1007
    return df5, df15


class TestPostNewsAnalyzer(unittest.TestCase):
    def test_parse_economic_number(self) -> None:
        self.assertAlmostEqual(parse_economic_number("3.5%") or 0.0, 3.5)
        self.assertAlmostEqual(parse_economic_number("23.8K") or 0.0, 23800.0)
        self.assertAlmostEqual(parse_economic_number("-1.5M") or 0.0, -1500000.0)
        self.assertIsNone(parse_economic_number("--"))

    def test_economic_rule_scoring_across_fx_gold_and_indices(self) -> None:
        analyzer = PostNewsAnalyzer()
        ev_pce = EconomicEvent(
            title="Core PCE Price Index m/m",
            currency="USD",
            impact=NewsImpact.HIGH,
            event_time=datetime(2026, 11, 25, 13, 30, tzinfo=timezone.utc),
            actual="0.5%",
            forecast="0.3%",
            previous="0.3%",
        )

        eurusd = analyzer.score_event_for_symbol(ev_pce, make_spec("EURUSDm", "EUR", "USD"))
        xauusd = analyzer.score_event_for_symbol(ev_pce, make_spec("XAUUSDm", "XAU", "USD", digits=3, point=0.001))
        us30 = analyzer.score_event_for_symbol(ev_pce, make_spec("US30m", "USD", "USD", digits=1, point=0.1))
        usdjpy = analyzer.score_event_for_symbol(ev_pce, make_spec("USDJPYm", "USD", "JPY", digits=3, point=0.001))

        self.assertGreater(eurusd.currency_score, 0.0)
        self.assertEqual(eurusd.symbol_bias, NewsSentiment.BEARISH)
        self.assertEqual(xauusd.symbol_bias, NewsSentiment.BEARISH)
        self.assertEqual(us30.symbol_bias, NewsSentiment.BEARISH)  # Hawkish inflation hurts equities
        self.assertEqual(usdjpy.symbol_bias, NewsSentiment.BULLISH)

        # Unemployment higher than expected -> USD Bearish -> EURUSD Bullish
        ev_unemp = EconomicEvent(
            title="Unemployment Rate",
            currency="USD",
            impact=NewsImpact.HIGH,
            event_time=datetime(2026, 11, 25, 13, 30, tzinfo=timezone.utc),
            actual="4.5%",
            forecast="4.1%",
            previous="4.1%",
        )
        eurusd_unemp = analyzer.score_event_for_symbol(ev_unemp, make_spec("EURUSDm", "EUR", "USD"))
        self.assertLess(eurusd_unemp.currency_score, 0.0)
        self.assertEqual(eurusd_unemp.symbol_bias, NewsSentiment.BULLISH)

    def test_post_news_reaction_detects_trend_continuation_vs_wick_trap_vs_wait(self) -> None:
        analyzer = PostNewsAnalyzer()
        ev_time = datetime(2026, 11, 25, 13, 30, tzinfo=timezone.utc)
        ev_pce = EconomicEvent(
            title="Core PCE Price Index m/m",
            currency="USD",
            impact=NewsImpact.HIGH,
            event_time=ev_time,
            actual="0.5%",
            forecast="0.3%",
            previous="0.3%",
        )
        spec = make_spec("EURUSDm", "EUR", "USD")
        scored = analyzer.score_event_for_symbol(ev_pce, spec)

        # 1. Wait settle (< 5m)
        df5, df15 = build_post_news_rates(ev_time, "trend_down")
        wait_res = analyzer.analyze_post_news_reaction(scored, spec, df5, df15, now=ev_time + timedelta(minutes=2))
        self.assertEqual(wait_res.verdict, PostNewsVerdict.WAIT_SETTLE)

        # 2. Genuine Trend Continuation Bearish (at +15m)
        cont_res = analyzer.analyze_post_news_reaction(scored, spec, df5, df15, now=ev_time + timedelta(minutes=15))
        self.assertEqual(cont_res.verdict, PostNewsVerdict.TREND_CONTINUATION_BEARISH)
        self.assertTrue(cont_res.aligned_with_news)
        self.assertFalse(cont_res.wick_trap_detected)

        # 3. Reversal Trap Bullish (spike down below 1.0985 then snaps up to 1.1007 opposite to Bearish news!)
        trap5, trap15 = build_post_news_rates(ev_time, "lower_wick_trap")
        trap_res = analyzer.analyze_post_news_reaction(scored, spec, trap5, trap15, now=ev_time + timedelta(minutes=15))
        self.assertEqual(trap_res.verdict, PostNewsVerdict.REVERSAL_TRAP_BULLISH)
        self.assertTrue(trap_res.wick_trap_detected)

    def test_mt5_calendar_bridge_and_manual_actual_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "ff_calendar_thisweek.json").write_text("[]", encoding="utf-8")
            (cache_dir / "holidays_2026_US.json").write_text("[]", encoding="utf-8")

            bridge_file = root / "cfd_calendar_bridge.json"
            bridge_file.write_text(
                json.dumps(
                    {
                        "updated_utc": "2026-11-25T13:31:00+00:00",
                        "events": [
                            {
                                "id": 1001,
                                "title": "Core PCE Price Index m/m",
                                "currency": "USD",
                                "impact": "HIGH",
                                "time_utc": "2026-11-25T13:30:00+00:00",
                                "actual": 0.4,
                                "forecast": 0.3,
                                "previous": 0.3,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            svc = EconomicCalendarService(
                PreventiveConfig(),
                cache_dir=cache_dir,
                bridge_path=bridge_file,
                manual_actuals_path=root / "news_actuals.json",
            )
            status = svc.get_bridge_status()
            self.assertTrue(status["detected"])
            self.assertEqual(status["event_count"], 1)

            events, _ = svc.get_events({"USD"}, now=datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].actual, "0.4")

            # Manual override updates actual
            svc.save_manual_actual("Core PCE Price Index m/m", "USD", "2026-11-25T13:30:00+00:00", actual="0.6")
            events2, _ = svc.get_events({"USD"}, now=datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc))
            self.assertEqual(events2[0].actual, "0.6")


if __name__ == "__main__":
    unittest.main()
