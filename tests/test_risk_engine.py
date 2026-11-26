from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import PreventiveConfig, RiskConfig
from cfd_bot.domain.models import AccountState, LotMode, OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.domain.risk_engine import RiskEngine, floor_to_step


class FakeGateway:
    def __init__(self, margin_per_lot: float = 1300.0, tiered_threshold: float | None = None, tiered_mult: float = 2.0):
        self.margin_per_lot = margin_per_lot
        self.tiered_threshold = tiered_threshold
        self.tiered_mult = tiered_mult
        self.extra_specs: dict[str, SymbolSpec] = {}

    def calc_margin(self, side: OrderSide, symbol: str, volume: float, price: float) -> float:
        mult = self.tiered_mult if (self.tiered_threshold and volume >= self.tiered_threshold) else 1.0
        return self.margin_per_lot * volume * mult

    def get_symbol_spec(self, symbol: str):
        return self.extra_specs.get(symbol)


def make_account(equity: float = 10000.0, leverage: int = 500, margin: float = 0.0) -> AccountState:
    return AccountState(
        login=123456,
        server="Demo-Server",
        currency="USD",
        currency_digits=2,
        balance=equity,
        equity=equity,
        margin=margin,
        margin_free=equity - margin,
        margin_level=(equity / margin * 100.0) if margin > 0 else 0.0,
        leverage=leverage,
        margin_so_call=100.0,
        margin_so_so=50.0,
        margin_so_mode=0,
    )


def make_gold_spec(volume_step: float = 0.01) -> SymbolSpec:
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        ask=2600.50,
        bid=2600.00,
        spread_points=50,
        volume_min=volume_step,
        volume_max=100.0,
        volume_step=volume_step,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_tick_value_profit=1.0,
        trade_tick_value_loss=1.0,
    )


class TestRiskEngine(unittest.TestCase):
    def test_floor_to_step_prevents_rounding_up(self) -> None:
        self.assertEqual(floor_to_step(0.1999, 0.01), 0.19)
        self.assertEqual(floor_to_step(1.89, 0.1), 1.8)
        self.assertEqual(floor_to_step(2.99, 1.0), 2.0)

    def test_fixed_lot_approved_and_adapts_to_broker_margin(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.10,
            max_effective_account_leverage=20.0,
            max_effective_symbol_leverage=15.0,
            max_margin_usage_pct=25.0,
        )
        account = make_account(equity=10000.0, leverage=500)
        spec = make_gold_spec()
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2610.50)
        guard = PreventiveGuard(PreventiveConfig(commission_per_lot_roundtrip=7.0, estimated_slippage_points=0.0))
        costs = guard.analyze_costs(spec, signal)

        # Gold broker margin = $1,300/lot -> implied symbol leverage = 260,000 / 1,300 = 200x (not 500x!)
        gw = FakeGateway(margin_per_lot=1300.0)
        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)

        self.assertTrue(plan.approved)
        self.assertEqual(plan.lot, 0.10)
        self.assertAlmostEqual(plan.leverage_snapshot.effective_symbol_leverage, 200.04, places=1)
        self.assertAlmostEqual(plan.leverage_snapshot.projected_margin_used, 130.0, places=2)

    def test_fixed_lot_scales_down_when_broker_leverage_tightens(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.50,
            max_effective_symbol_leverage=15.0,
            max_effective_account_leverage=20.0,
            max_margin_usage_pct=25.0,
            scale_down_lot_for_leverage=True,
        )
        account = make_account(equity=2000.0, leverage=500)
        spec = make_gold_spec()
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2610.50)
        costs = PreventiveGuard(PreventiveConfig(commission_per_lot_roundtrip=0.0, estimated_slippage_points=0.0)).analyze_costs(spec, signal)

        # Broker tightens leverage to ~1:20 (margin $13,000/lot) -> 25% of $2,000 = $500 max margin -> max 0.03 lot
        gw = FakeGateway(margin_per_lot=13000.0)
        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)

        self.assertTrue(plan.approved)
        self.assertEqual(plan.lot, 0.03)
        self.assertTrue(any("Scaled down" in r for r in plan.clamp_reasons))

    def test_fixed_lot_rejects_when_scale_down_disabled(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.50,
            max_effective_symbol_leverage=10.0,
            scale_down_lot_for_leverage=False,
        )
        account = make_account(equity=2000.0, leverage=500)
        spec = make_gold_spec()
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2610.50)
        costs = PreventiveGuard(PreventiveConfig()).analyze_costs(spec, signal)
        gw = FakeGateway(margin_per_lot=1300.0)

        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)
        self.assertFalse(plan.approved)
        self.assertEqual(plan.lot, 0.0)

    def test_dynamic_risk_sizing_includes_costs_and_floors_step(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.DYNAMIC_RISK,
            risk_percent=1.0,
            max_lot_cap=5.0,
            max_effective_symbol_leverage=50.0,
            max_effective_account_leverage=50.0,
            include_costs_in_risk_sizing=True,
        )
        # Equity $10,000 -> 1% risk budget = $100
        # SL = $5.00 (500 pts) -> $500/lot + $7 comm + $5 slip = $512/lot -> 100 / 512 = 0.1953 -> floor 0.19
        account = make_account(equity=10000.0)
        spec = make_gold_spec(volume_step=0.01)
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2615.50)
        costs = PreventiveGuard(
            PreventiveConfig(commission_per_lot_roundtrip=7.0, estimated_slippage_points=5.0)
        ).analyze_costs(spec, signal)

        gw = FakeGateway(margin_per_lot=1300.0)
        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)

        self.assertTrue(plan.approved)
        self.assertAlmostEqual(plan.risk_budget_money, 100.0, places=2)
        self.assertEqual(plan.lot, 0.19)
        self.assertLessEqual(plan.estimated_loss_at_sl, 100.0)

    def test_dynamic_equity_step_mode(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.DYNAMIC_EQUITY_STEP,
            equity_step_amount=1000.0,
            equity_step_lot=0.02,
            max_effective_symbol_leverage=30.0,
            max_effective_account_leverage=30.0,
        )
        account = make_account(equity=5500.0)
        spec = make_gold_spec()
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2610.50)
        costs = PreventiveGuard(PreventiveConfig()).analyze_costs(spec, signal)
        gw = FakeGateway(margin_per_lot=1300.0)

        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)
        # 5.5 * 0.02 = 0.11 lot
        self.assertTrue(plan.approved)
        self.assertEqual(plan.lot, 0.11)

    def test_tiered_broker_margin_recheck_scales_lot_down(self) -> None:
        cfg = RiskConfig(
            lot_mode=LotMode.DYNAMIC_RISK,
            risk_percent=2.0,
            max_lot_cap=2.0,
            max_effective_symbol_leverage=50.0,
            max_effective_account_leverage=50.0,
            max_margin_usage_pct=20.0,
            min_margin_level_pct=500.0,
            scale_down_lot_for_leverage=True,
        )
        # Equity $10,000 -> 20% max margin = $2,000.
        # At 1.0 lot normal margin is $1,300 (so 1.33 lot fits), BUT tier doubles margin for lot >= 1.20!
        account = make_account(equity=10000.0)
        spec = make_gold_spec()
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2599.00, 2610.50)
        costs = PreventiveGuard(PreventiveConfig(commission_per_lot_roundtrip=0.0, estimated_slippage_points=0.0)).analyze_costs(spec, signal)

        gw = FakeGateway(margin_per_lot=1300.0, tiered_threshold=1.20, tiered_mult=2.0)
        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=gw)

        self.assertTrue(plan.approved)
        self.assertLessEqual(plan.leverage_snapshot.projected_margin_used, 2000.0)
        self.assertTrue(any("Tiered broker margin adjustment" in r for r in plan.clamp_reasons))

    def test_multi_symbol_portfolio_leverage_cap(self) -> None:
        from cfd_bot.domain.models import OpenPosition

        cfg = RiskConfig(
            lot_mode=LotMode.FIXED,
            fixed_lot=0.50,
            max_effective_account_leverage=20.0,
            max_effective_symbol_leverage=20.0,
            max_margin_usage_pct=50.0,
            min_margin_level_pct=200.0,
            scale_down_lot_for_leverage=True,
        )
        # Equity $10,000 -> Max portfolio notional = $200,000 (20x)
        # Existing EURUSD position uses 1.0 lot * $110,000 = $110,000 -> Remaining notional = $90,000
        # Gold notional = $260,050/lot -> max remaining Gold lot = 90,000 / 260,050 = 0.346 -> floor 0.34
        account = make_account(equity=10000.0, margin=220.0)
        spec_gold = make_gold_spec()
        spec_eurusd = SymbolSpec(
            name="EURUSD",
            digits=5,
            point=0.00001,
            ask=1.10010,
            bid=1.10000,
            spread_points=10,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100000.0,
            trade_tick_size=0.00001,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )
        gw = FakeGateway(margin_per_lot=1300.0)
        gw.extra_specs["EURUSD"] = spec_eurusd
        existing = [
            OpenPosition(
                ticket=1,
                symbol="EURUSD",
                side=OrderSide.BUY,
                volume=1.0,
                price_open=1.10000,
                price_current=1.10000,
                sl=1.09500,
                tp=1.11000,
                profit=0.0,
                swap=0.0,
                magic=1,
            )
        ]
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2595.50, 2610.50)
        costs = PreventiveGuard(PreventiveConfig(commission_per_lot_roundtrip=0.0, estimated_slippage_points=0.0)).analyze_costs(spec_gold, signal)

        plan = RiskEngine(cfg).compute_lot_plan(account, spec_gold, signal, costs, open_positions=existing, gateway=gw)
        self.assertTrue(plan.approved)
        self.assertEqual(plan.lot, 0.34)
        self.assertLessEqual(plan.leverage_snapshot.projected_effective_portfolio_leverage, 20.0)

    def test_optional_none_or_zero_limits_are_disabled(self) -> None:
        account = make_account(equity=10000.0, leverage=1000)
        spec = make_gold_spec()
        cfg = RiskConfig(
            lot_mode=LotMode.DYNAMIC_RISK,
            risk_percent=10.0,
            max_lot_cap=None,
            min_sl_points=None,
            max_effective_account_leverage=None,
            max_effective_symbol_leverage=0.0,
            max_margin_usage_pct=None,
            min_margin_level_pct=None,
            min_stopout_buffer_multiplier=None,
        )
        signal = TradeSignal("XAUUSD", OrderSide.BUY, 2600.50, 2599.50, 2605.50)
        costs = PreventiveGuard(
            PreventiveConfig(commission_per_lot_roundtrip=0.0, estimated_slippage_points=0.0)
        ).analyze_costs(spec, signal)

        plan = RiskEngine(cfg).compute_lot_plan(account, spec, signal, costs, gateway=FakeGateway(margin_per_lot=200.0))
        self.assertTrue(plan.approved)
        # $1,000 budget / $100 SL loss per lot = 10.00 lots (not clamped by 1.00 cap or leverage limits)
        self.assertEqual(plan.lot, 10.0)
        self.assertEqual(plan.clamp_reasons, [])


if __name__ == "__main__":
    unittest.main()
