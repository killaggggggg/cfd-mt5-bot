from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.bot_runner import BotRunner
from cfd_bot.application.config import BotConfig, PreventiveConfig, RiskConfig
from cfd_bot.domain.models import AccountState, CalendarCheckResult, LotMode, OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy
from cfd_bot.infrastructure.order_executor import OrderExecutor


class FakeCalendarService:
    def __init__(self, blocked: bool = False, reason: str = "") -> None:
        self._blocked = blocked
        self._reason = reason

    def evaluate(self, spec: SymbolSpec, now: datetime, expected_hold_days: float = 0.0) -> CalendarCheckResult:
        return CalendarCheckResult(
            blocked=self._blocked,
            reasons=[self._reason] if self._blocked and self._reason else [],
            warnings=[],
            upcoming_events=[],
        )


class StubStrategy(BaseStrategy):
    def __init__(self, signal: TradeSignal | None) -> None:
        self._signal = signal

    @property
    def name(self) -> str:
        return "StubStrategy"

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> TradeSignal | None:
        return self._signal


class FakeMT5Gateway:
    def __init__(self, spec: SymbolSpec, check_retcode: int = 0) -> None:
        self.spec = spec
        self.check_retcode = check_retcode
        self.sent_requests: list[dict] = []
        self.rate_counts: list[int] = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def resolve_symbol(self, symbol: str) -> str:
        return symbol

    def get_account_state(self) -> AccountState:
        return AccountState(
            login=999,
            server="Demo",
            currency="USD",
            currency_digits=2,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            margin_level=0.0,
            leverage=500,
        )

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        return self.spec

    def calc_margin(self, side: OrderSide, symbol: str, volume: float, price: float) -> float:
        return 1300.0 * volume

    def calc_profit(self, side: OrderSide, symbol: str, volume: float, price_open: float, price_close: float) -> float:
        return 0.0

    def get_open_positions(self, symbol=None, magic=None):
        return []

    def get_deal_history(self, date_from: datetime, date_to: datetime) -> list[dict]:
        return []

    def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        self.rate_counts.append(count)
        return pd.DataFrame([{"time": datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc), "close": 2600.0}])

    def get_rates_range(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        return self.get_rates(symbol, timeframe, 1)

    def order_check(self, request: dict) -> dict:
        return {
            "retcode": self.check_retcode,
            "margin": 1300.0 * request["volume"],
            "margin_free": 10000.0 - 1300.0 * request["volume"],
            "comment": "OK" if self.check_retcode == 0 else "Insufficient funds",
        }

    def order_send(self, request: dict) -> dict:
        self.sent_requests.append(request)
        return {
            "retcode": mt5.TRADE_RETCODE_DONE,
            "order": 777111,
            "deal": 777112,
            "volume": request.get("volume", 0.0),
            "price": request.get("price", 0.0),
            "comment": "Done",
        }


def make_spec(now: datetime, spread_pts: int = 20) -> SymbolSpec:
    ask = 2600.00 + spread_pts * 0.01
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        ask=ask,
        bid=2600.00,
        spread_points=spread_pts,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
        quote_time=now,
    )


def _build_runner(
    cfg: BotConfig,
    gw: FakeMT5Gateway,
    strategy: BaseStrategy,
    calendar_service: FakeCalendarService,
) -> BotRunner:
    executor = OrderExecutor(gw, magic_number=cfg.magic_number, deviation_points=cfg.deviation_points, dry_run=cfg.dry_run)
    return BotRunner(cfg, gw, strategy, executor=executor, calendar_service=calendar_service)


class TestBotRunner(unittest.TestCase):
    def test_dry_run_checks_order_without_sending(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(now, spread_pts=20)
        sig = TradeSignal("XAUUSD", OrderSide.BUY, spec.ask, 2595.20, 2610.20, expected_hold_days=0.0)
        gw = FakeMT5Gateway(spec)
        cfg = BotConfig(
            dry_run=True,
            risk=RiskConfig(lot_mode=LotMode.DYNAMIC_RISK, risk_percent=1.0),
            preventive=PreventiveConfig(max_spread_points=50.0, min_net_rr=1.2),
        )
        res = _build_runner(cfg, gw, StubStrategy(sig), FakeCalendarService()).evaluate_once(now=now)

        self.assertEqual(res.status, "dry_run_ok")
        self.assertIsNotNone(res.lot_plan)
        self.assertEqual(len(gw.sent_requests), 0)

    def test_live_mode_executes_valid_order(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(now, spread_pts=20)
        sig = TradeSignal("XAUUSD", OrderSide.BUY, spec.ask, 2595.20, 2610.20, expected_hold_days=0.0)
        gw = FakeMT5Gateway(spec)
        cfg = BotConfig(dry_run=False)
        res = _build_runner(cfg, gw, StubStrategy(sig), FakeCalendarService()).evaluate_once(now=now)

        self.assertEqual(res.status, "executed")
        self.assertIsNotNone(res.execution)
        assert res.execution is not None
        self.assertEqual(res.execution.order_ticket, 777111)
        self.assertEqual(len(gw.sent_requests), 1)

    def test_blocks_when_spread_too_wide_before_execution(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(now, spread_pts=120)
        sig = TradeSignal("XAUUSD", OrderSide.BUY, spec.ask, 2595.20, 2610.20, expected_hold_days=0.0)
        gw = FakeMT5Gateway(spec)
        cfg = BotConfig(dry_run=False, preventive=PreventiveConfig(max_spread_points=60.0))
        res = _build_runner(cfg, gw, StubStrategy(sig), FakeCalendarService()).evaluate_once(now=now)

        self.assertEqual(res.status, "blocked_by_preventive_guard")
        self.assertEqual(len(gw.sent_requests), 0)

    def test_blocks_when_high_impact_news_active(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(now, spread_pts=20)
        sig = TradeSignal("XAUUSD", OrderSide.BUY, spec.ask, 2595.20, 2610.20, expected_hold_days=0.0)
        gw = FakeMT5Gateway(spec)
        cfg = BotConfig(dry_run=False)
        cal = FakeCalendarService(blocked=True, reason="HIGH news blackout (USD in 10m): FOMC")
        res = _build_runner(cfg, gw, StubStrategy(sig), cal).evaluate_once(now=now)

        self.assertEqual(res.status, "blocked_by_preventive_guard")
        self.assertIsNotNone(res.preventive)
        assert res.preventive is not None
        self.assertTrue(any("FOMC" in r for r in res.preventive.reasons))
        self.assertEqual(len(gw.sent_requests), 0)

    def test_stops_if_mt5_order_check_fails(self) -> None:
        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        spec = make_spec(now, spread_pts=20)
        sig = TradeSignal("XAUUSD", OrderSide.BUY, spec.ask, 2595.20, 2610.20, expected_hold_days=0.0)
        gw = FakeMT5Gateway(spec, check_retcode=10019)
        cfg = BotConfig(dry_run=False)
        res = _build_runner(cfg, gw, StubStrategy(sig), FakeCalendarService()).evaluate_once(now=now)

        self.assertEqual(res.status, "execution_failed")
        self.assertEqual(len(gw.sent_requests), 0)

    def test_manage_open_positions_moves_sl_to_true_breakeven_and_trails_atr(self) -> None:
        from cfd_bot.application.config import TradeManagementConfig
        from cfd_bot.domain.models import OpenPosition
        from cfd_bot.infrastructure.order_executor import OrderExecutor

        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        # Bid=2606.00 (entry=2600.00, initial SL=2595.00 -> risk=$5, current profit=$6 >= 1.0R)
        spec = SymbolSpec(**{**make_spec(now, spread_pts=20).__dict__, "bid": 2606.00, "ask": 2606.20})
        gw = FakeMT5Gateway(spec)
        executor = OrderExecutor(gw, magic_number=202611, dry_run=False)
        pos = OpenPosition(
            ticket=5001,
            symbol="XAUUSD",
            side=OrderSide.BUY,
            volume=0.10,
            price_open=2600.00,
            price_current=2606.00,
            sl=2595.00,
            tp=2615.00,
            profit=60.0,
            swap=0.0,
            magic=202611,
        )

        # Case 1: True BE only -> Comm $7 + Slip 5pts ($5) = 12 pts ($0.12) + 5 pts lock = $0.17 -> SL = 2600.17
        t_cfg = TradeManagementConfig(
            enable_auto_breakeven=True,
            breakeven_trigger_r=1.0,
            breakeven_lock_points=5,
            enable_trailing_stop=False,
        )
        adjs = executor.manage_open_positions(
            spec=spec,
            positions=[pos],
            trade_cfg=t_cfg,
            commission_per_lot=7.0,
            estimated_slippage_points=5.0,
        )
        self.assertEqual(len(adjs), 1)
        self.assertTrue(adjs[0].executed)
        self.assertEqual(adjs[0].action_name, "TRUE_BREAKEVEN")
        self.assertAlmostEqual(adjs[0].new_sl, 2600.17, places=2)

        # Case 2: ATR Trailing advances SL further when ATR trail > BE (Bid=2610, ATR=2.0, mult=1.5 -> 2607.00)
        spec_high = SymbolSpec(**{**spec.__dict__, "bid": 2610.00, "ask": 2610.20})
        pos_moved = OpenPosition(**{**pos.__dict__, "sl": 2600.17, "price_current": 2610.00})
        t_cfg_trail = TradeManagementConfig(
            enable_auto_breakeven=True,
            enable_trailing_stop=True,
            trailing_atr_mult=1.5,
            trailing_step_points=10,
        )
        adjs_trail = executor.manage_open_positions(
            spec=spec_high,
            positions=[pos_moved],
            trade_cfg=t_cfg_trail,
            commission_per_lot=7.0,
            estimated_slippage_points=5.0,
            atr=2.0,
        )
        self.assertEqual(len(adjs_trail), 1)
        self.assertEqual(adjs_trail[0].action_name, "ATR_TRAILING")
        self.assertAlmostEqual(adjs_trail[0].new_sl, 2607.00, places=2)

    def test_respects_strategy_recommended_history_bars(self) -> None:
        from cfd_bot.domain.strategies import build_strategy

        now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
        gw = FakeMT5Gateway(make_spec(now))
        cfg = BotConfig(symbol="XAUUSD", timeframe="M5", dry_run=True)
        strat = build_strategy("M5RangeScalper", {"htf_minutes": 60})
        runner = BotRunner(cfg, gw, strat, executor=OrderExecutor(gw, dry_run=True), calendar_service=FakeCalendarService())
        runner.evaluate_once(now=now)
        self.assertEqual(gw.rate_counts[-1], 780)

    def test_bot_runner_time_stop_closes_stagnant_position(self) -> None:
        from datetime import timedelta
        from cfd_bot.domain.models import OpenPosition
        from cfd_bot.domain.strategies import build_strategy

        now = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)
        old_open = int((now - timedelta(minutes=5 * 50)).timestamp())
        gw = FakeMT5Gateway(make_spec(now))
        stale_pos = OpenPosition(
            ticket=88001,
            symbol="XAUUSD",
            side=OrderSide.BUY,
            volume=0.05,
            price_open=2600.0,
            price_current=2598.0,
            sl=2580.0,
            tp=2625.0,
            profit=-10.0,
            swap=0.0,
            magic=202611,
            open_time=old_open,
        )
        gw.get_open_positions = lambda symbol=None, magic=None: [stale_pos]  # type: ignore[method-assign]
        cfg = BotConfig(symbol="XAUUSD", timeframe="M5", magic_number=202611, dry_run=False)
        strat = build_strategy("M5KernelPullback", {"max_hold_bars": 48})
        runner = BotRunner(
            cfg,
            gw,
            strat,
            executor=OrderExecutor(gw, magic_number=202611, dry_run=False),
            calendar_service=FakeCalendarService(),
        )
        res = runner.evaluate_once(now=now)
        self.assertTrue(any(adj.action_name == "TIME_STOP_CLOSE" and adj.executed for adj in res.adjustments))
        self.assertTrue(any(req.get("position") == 88001 for req in gw.sent_requests))


if __name__ == "__main__":
    unittest.main()
