from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import PreventiveConfig
from cfd_bot.domain.models import AccountState, NewsImpact, OrderSide, SymbolSpec, TradeSignal, extract_symbol_currencies
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService


def make_spec(name: str = "XAUUSDm", base: str = "XAU", profit: str = "USD", now: datetime | None = None) -> SymbolSpec:
    return SymbolSpec(
        name=name,
        digits=2,
        point=0.01,
        ask=2600.20,
        bid=2600.00,
        spread_points=20,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
        currency_base=base,
        currency_profit=profit,
        currency_margin=base,
        quote_time=now,
    )


def make_account() -> AccountState:
    return AccountState(
        login=1,
        server="Demo",
        currency="USD",
        currency_digits=2,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
        margin_level=0.0,
        leverage=200,
    )


class TestCalendarService(unittest.TestCase):
    def test_extract_symbol_currencies(self) -> None:
        self.assertIn("USD", extract_symbol_currencies(make_spec("XAUUSDm", "XAU", "USD")))
        self.assertEqual(extract_symbol_currencies(make_spec("EURUSDm", "EUR", "USD")), {"EUR", "USD"})
        self.assertIn("USD", extract_symbol_currencies(make_spec("US30m", "USD", "USD")))

    def test_blocks_high_impact_news_within_window_and_passes_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            # Pre-seed fresh cache files so test never touches the network
            # 2026-11-25 08:30 EST == 13:30 UTC
            ff_payload = [
                {
                    "title": "Core PCE Price Index m/m",
                    "country": "USD",
                    "date": "2026-11-25T08:30:00-05:00",
                    "impact": "High",
                },
                {
                    "title": "Minor Speech",
                    "country": "USD",
                    "date": "2026-11-25T10:00:00-05:00",
                    "impact": "Medium",
                },
            ]
            (cache_dir / "ff_calendar_thisweek.json").write_text(json.dumps(ff_payload), encoding="utf-8")
            (cache_dir / "holidays_2026_US.json").write_text("[]", encoding="utf-8")

            cfg = PreventiveConfig(
                news_block_minutes_before=30,
                news_block_minutes_after=30,
                block_medium_impact_news=False,
                news_warn_hours=12,
            )
            svc = EconomicCalendarService(cfg, cache_dir=cache_dir)
            spec = make_spec("XAUUSDm", "XAU", "USD")

            # Inside window: 13:15 UTC (15m before 13:30 UTC High-impact news)
            res_blocked = svc.evaluate(spec, now=datetime(2026, 11, 25, 13, 15, tzinfo=timezone.utc))
            self.assertTrue(res_blocked.blocked)
            self.assertTrue(any("Core PCE Price Index" in r for r in res_blocked.reasons))

            # Before window: 10:00 UTC (3h30m before news -> allowed, but warned!)
            res_warn = svc.evaluate(spec, now=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc))
            self.assertFalse(res_warn.blocked)
            self.assertTrue(any("Upcoming HIGH news" in w for w in res_warn.warnings))

            # Outside window: 14:15 UTC (45m after news -> allowed)
            res_after = svc.evaluate(spec, now=datetime(2026, 11, 25, 14, 15, tzinfo=timezone.utc))
            self.assertFalse(res_after.blocked)

    def test_blocks_major_bank_holiday_for_matching_currency_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            ff_payload = [
                {
                    "title": "Bank Holiday",
                    "country": "USD",
                    "date": "2026-11-26T08:00:00-05:00",
                    "impact": "Holiday",
                }
            ]
            nager_us = [
                {
                    "date": "2026-11-26",
                    "name": "Thanksgiving Day",
                    "global": True,
                    "types": ["Public", "Bank"],
                },
                {
                    "date": "2026-11-11",
                    "name": "Veterans Day",
                    "global": True,
                    "types": ["Public", "Bank"],
                },
            ]
            (cache_dir / "ff_calendar_thisweek.json").write_text(json.dumps(ff_payload), encoding="utf-8")
            (cache_dir / "holidays_2026_US.json").write_text(json.dumps(nager_us), encoding="utf-8")
            (cache_dir / "holidays_2026_DE.json").write_text("[]", encoding="utf-8")
            (cache_dir / "holidays_2026_GB.json").write_text("[]", encoding="utf-8")
            (cache_dir / "holidays_2026_JP.json").write_text("[]", encoding="utf-8")

            cfg = PreventiveConfig(holiday_policy="block")
            svc = EconomicCalendarService(cfg, cache_dir=cache_dir)

            # On Thanksgiving Day (2026-11-26), USD pair is blocked, while EURJPY is NOT blocked!
            thanksgiving = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
            res_gold = svc.evaluate(make_spec("XAUUSDm", "XAU", "USD"), now=thanksgiving)
            res_eurjpy = svc.evaluate(make_spec("EURJPYm", "EUR", "JPY"), now=thanksgiving)

            self.assertTrue(res_gold.blocked)
            self.assertTrue(any("Bank Holiday today" in r for r in res_gold.reasons))
            self.assertFalse(res_eurjpy.blocked)

            # On minor holiday (Veterans Day 2026-11-11), warns without hard-blocking
            veterans = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
            res_vet = svc.evaluate(make_spec("XAUUSDm", "XAU", "USD"), now=veterans)
            self.assertFalse(res_vet.blocked)
            self.assertTrue(any("Minor bank holiday" in w for w in res_vet.warnings))

    def test_custom_events_file_and_preventive_guard_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "ff_calendar_thisweek.json").write_text("[]", encoding="utf-8")
            (cache_dir / "holidays_2026_US.json").write_text("[]", encoding="utf-8")

            custom_path = Path(tmp) / "custom_events.json"
            custom_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Emergency Central Bank Briefing",
                            "currency": "ALL",
                            "impact": "HIGH",
                            "date": "2026-11-25T15:00:00+00:00",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            now = datetime(2026, 11, 25, 14, 45, tzinfo=timezone.utc)
            cfg = PreventiveConfig(news_block_minutes_before=30, news_block_minutes_after=30)
            svc = EconomicCalendarService(cfg, cache_dir=cache_dir, custom_events_path=custom_path)
            guard = PreventiveGuard(cfg, calendar_service=svc)

            spec = make_spec("XAUUSDm", "XAU", "USD", now=now)
            sig = TradeSignal("XAUUSDm", OrderSide.BUY, 2600.20, 2590.20, 2620.20, expected_hold_days=0.0)
            decision = guard.evaluate(make_account(), spec, sig, now=now)

            self.assertFalse(decision.approved)
            self.assertTrue(any("Emergency Central Bank Briefing" in r for r in decision.reasons))
            self.assertEqual(len(decision.upcoming_events), 1)
            self.assertEqual(decision.upcoming_events[0].impact, NewsImpact.HIGH)

    def test_graceful_fallback_when_network_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = PreventiveConfig(calendar_http_timeout_seconds=1.0, fail_closed_on_calendar_error=False)
            svc = EconomicCalendarService(cfg, cache_dir=Path(tmp))
            # Point URLs to unroutable localhost port so network fails deterministically
            svc.FF_WEEKLY_URL = "http://127.0.0.1:9/ff.json"
            svc.NAGER_URL_TEMPLATE = "http://127.0.0.1:9/{year}/{country}.json"

            res = svc.evaluate(make_spec("EURUSDm", "EUR", "USD"), now=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc))
            self.assertFalse(res.blocked)
            self.assertTrue(any("unreachable" in w for w in res.warnings))


if __name__ == "__main__":
    unittest.main()
