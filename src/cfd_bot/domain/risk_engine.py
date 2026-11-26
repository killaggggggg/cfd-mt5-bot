from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR
from typing import Optional

from cfd_bot.domain.models import (
    AccountState,
    LeverageSnapshot,
    LotMode,
    LotPlan,
    OpenPosition,
    OrderSide,
    PreventiveCostBreakdown,
    StopOutMode,
    SymbolSpec,
    TradeSignal,
)
from cfd_bot.domain.ports import MarketGatewayPort, RiskPolicyPort


def floor_to_step(value: float, step: float) -> float:
    """Floors a lot size strictly to a multiple of broker volume_step without float drift."""
    if value <= 0.0 or step <= 0.0:
        return 0.0
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    units = (d_val / d_step).to_integral_value(rounding=ROUND_FLOOR)
    floored = units * d_step
    return float(floored)


def estimate_notional_per_lot(spec: SymbolSpec, price: float) -> float:
    """
    Computes 1.0-lot notional value in account currency.

    Using (price / tick_size) * tick_value automatically accounts for cross-currency
    conversion because MT5 supplies tick_value in the account deposit currency.
    """
    if price <= 0.0:
        return 0.0
    tick_size = spec.effective_tick_size
    tick_val = spec.trade_tick_value if spec.trade_tick_value > 0 else spec.effective_tick_value_loss
    if tick_size > 0 and tick_val > 0:
        return (price / tick_size) * tick_val
    if spec.trade_contract_size > 0:
        return spec.trade_contract_size * price
    return 0.0


class RiskEngine:
    """
    Calculates Fixed or Dynamic lot sizes adapted to real broker leverage and margin tiers.
    """

    def __init__(self, config: RiskPolicyPort) -> None:
        self._cfg = config

    def compute_lot_plan(
        self,
        account: AccountState,
        spec: SymbolSpec,
        signal: TradeSignal,
        costs: PreventiveCostBreakdown,
        open_positions: Optional[list[OpenPosition]] = None,
        gateway: Optional[MarketGatewayPort] = None,
        lot_mode_override: Optional[LotMode] = None,
    ) -> LotPlan:
        """Computes a normalized, leverage-checked LotPlan for a trade signal."""
        mode = lot_mode_override or self._cfg.lot_mode
        positions = open_positions or []
        clamp_reasons: list[str] = []
        rejection_reasons: list[str] = []

        entry = self._resolve_entry_price(spec, signal)
        if entry <= 0.0:
            rejection_reasons.append("Invalid entry price (<= 0)")

        notional_per_lot = estimate_notional_per_lot(spec, entry)
        if notional_per_lot <= 0.0:
            rejection_reasons.append("Unable to determine notional value per lot")

        margin_per_lot = self._resolve_margin_per_lot(
            account=account,
            spec=spec,
            side=signal.side,
            volume=1.0,
            price=entry,
            notional_per_lot=notional_per_lot,
            gateway=gateway,
        )
        if margin_per_lot is None or margin_per_lot <= 0.0:
            rejection_reasons.append("Unable to determine required broker margin per lot")
            margin_per_lot = 0.0

        effective_symbol_leverage = (
            notional_per_lot / margin_per_lot if margin_per_lot > 0 else float(max(1, account.leverage))
        )

        # 1. Base risk & raw lot calculation
        raw_lot, risk_budget_money, sl_loss_per_lot = self._compute_raw_lot(
            mode=mode,
            account=account,
            spec=spec,
            entry=entry,
            signal=signal,
            costs=costs,
            rejection_reasons=rejection_reasons,
        )

        if rejection_reasons:
            empty_snap = self._build_leverage_snapshot(
                account=account,
                spec=spec,
                lot=0.0,
                margin_per_lot=margin_per_lot,
                notional_per_lot=notional_per_lot,
                effective_symbol_leverage=effective_symbol_leverage,
                positions=positions,
                gateway=gateway,
            )
            return LotPlan(
                approved=False,
                lot=0.0,
                raw_lot=raw_lot,
                mode=mode,
                risk_budget_money=risk_budget_money,
                estimated_loss_at_sl=0.0,
                estimated_profit_at_tp=0.0,
                leverage_snapshot=empty_snap,
                clamp_reasons=clamp_reasons,
                rejection_reasons=rejection_reasons,
            )

        # 2. Apply absolute config / broker volume caps (None or <= 0 disables max_lot_cap)
        candidate_lot = raw_lot
        if self._cfg.max_lot_cap is not None and self._cfg.max_lot_cap > 0 and candidate_lot > self._cfg.max_lot_cap:
            clamp_reasons.append(f"Capped at max_lot_cap ({candidate_lot:.4f} -> {self._cfg.max_lot_cap:.4f})")
            candidate_lot = self._cfg.max_lot_cap

        if spec.volume_max > 0 and candidate_lot > spec.volume_max:
            clamp_reasons.append(f"Capped at broker volume_max ({candidate_lot:.4f} -> {spec.volume_max:.4f})")
            candidate_lot = spec.volume_max

        # 3. Apply leverage & margin safety caps
        candidate_lot = self._apply_leverage_and_margin_caps(
            candidate_lot=candidate_lot,
            account=account,
            spec=spec,
            notional_per_lot=notional_per_lot,
            margin_per_lot=margin_per_lot,
            positions=positions,
            gateway=gateway,
            clamp_reasons=clamp_reasons,
            rejection_reasons=rejection_reasons,
        )

        # 4. Floor to broker volume step
        final_lot = floor_to_step(candidate_lot, spec.volume_step)

        # 5. Re-verify exact broker margin at final_lot (handles tiered dynamic leverage)
        final_margin = margin_per_lot * final_lot
        if gateway is not None and final_lot >= spec.volume_min:
            max_margin_allowed = self._max_usable_new_margin(account)
            for _ in range(3):
                queried_final_margin = gateway.calc_margin(signal.side, spec.name, final_lot, entry)
                if queried_final_margin is None or queried_final_margin <= 0:
                    break
                final_margin = queried_final_margin
                if final_margin <= max_margin_allowed or final_lot <= 0:
                    break
                if not self._cfg.scale_down_lot_for_leverage:
                    rejection_reasons.append(
                        f"Tiered margin {final_margin:.2f} exceeds allowed {max_margin_allowed:.2f}"
                    )
                    break
                ratio = max_margin_allowed / final_margin
                scaled = floor_to_step(final_lot * ratio, spec.volume_step)
                if scaled >= final_lot:
                    scaled = floor_to_step(final_lot - spec.volume_step, spec.volume_step)
                clamp_reasons.append(
                    f"Tiered broker margin adjustment ({final_lot:.4f} -> {scaled:.4f})"
                )
                final_lot = scaled
                if final_lot < spec.volume_min:
                    break

        if final_lot < spec.volume_min:
            rejection_reasons.append(
                f"Calculated lot {final_lot:.4f} is below broker volume_min {spec.volume_min:.4f}"
            )
            final_lot = 0.0

        approved = len(rejection_reasons) == 0 and final_lot >= spec.volume_min
        snap = self._build_leverage_snapshot(
            account=account,
            spec=spec,
            lot=final_lot,
            margin_per_lot=margin_per_lot,
            notional_per_lot=notional_per_lot,
            effective_symbol_leverage=effective_symbol_leverage,
            positions=positions,
            gateway=gateway,
            override_new_margin=final_margin if final_lot > 0 else 0.0,
        )

        est_loss = final_lot * costs.net_sl_risk_per_lot if final_lot > 0 else 0.0
        est_profit = final_lot * costs.net_tp_reward_per_lot if final_lot > 0 else 0.0

        return LotPlan(
            approved=approved,
            lot=final_lot,
            raw_lot=raw_lot,
            mode=mode,
            risk_budget_money=risk_budget_money,
            estimated_loss_at_sl=est_loss,
            estimated_profit_at_tp=est_profit,
            leverage_snapshot=snap,
            clamp_reasons=clamp_reasons,
            rejection_reasons=rejection_reasons,
        )

    def _resolve_entry_price(self, spec: SymbolSpec, signal: TradeSignal) -> float:
        if signal.entry_price > 0:
            return signal.entry_price
        return spec.ask if signal.side == OrderSide.BUY else spec.bid

    def _resolve_margin_per_lot(
        self,
        account: AccountState,
        spec: SymbolSpec,
        side: OrderSide,
        volume: float,
        price: float,
        notional_per_lot: float,
        gateway: Optional[MarketGatewayPort],
    ) -> Optional[float]:
        if gateway is not None and price > 0:
            margin = gateway.calc_margin(side, spec.name, volume, price)
            if margin is not None and margin > 0:
                return margin

        if not self._cfg.allow_fallback_formula_if_margin_api_unavailable:
            return None

        if spec.margin_initial > 0:
            return spec.margin_initial * volume

        if account.leverage > 0 and notional_per_lot > 0:
            return (notional_per_lot * volume) / float(account.leverage)

        return None

    def _compute_raw_lot(
        self,
        mode: LotMode,
        account: AccountState,
        spec: SymbolSpec,
        entry: float,
        signal: TradeSignal,
        costs: PreventiveCostBreakdown,
        rejection_reasons: list[str],
    ) -> tuple[float, float, float]:
        base_capital = account.equity if self._cfg.use_equity_for_risk else account.balance
        if base_capital <= 0:
            rejection_reasons.append(f"Non-positive account capital ({base_capital:.2f})")
            return 0.0, 0.0, 0.0

        # Minimum SL floor protects against micro-SL lot explosion (optional when None or 0)
        raw_sl_points = abs(entry - signal.sl_price) / spec.point if spec.point > 0 else 0.0
        min_sl_pts = max(int(self._cfg.min_sl_points or 0), spec.trade_stops_level, 1)
        effective_sl_points = max(raw_sl_points, float(min_sl_pts))

        point_loss_per_lot = self._point_value_loss_per_lot(spec)
        if point_loss_per_lot <= 0:
            rejection_reasons.append("Invalid symbol tick value / point size")
            return 0.0, 0.0, 0.0

        sl_loss_per_lot = effective_sl_points * point_loss_per_lot
        if self._cfg.include_costs_in_risk_sizing:
            sl_loss_per_lot += costs.commission_per_lot + costs.slippage_cost_per_lot + costs.swap_cost_per_lot

        if sl_loss_per_lot <= 0:
            rejection_reasons.append("Computed SL loss per lot is <= 0")
            return 0.0, 0.0, 0.0

        if mode == LotMode.FIXED:
            raw_lot = self._cfg.fixed_lot
            risk_budget = raw_lot * sl_loss_per_lot
            return raw_lot, risk_budget, sl_loss_per_lot

        if mode == LotMode.DYNAMIC_EQUITY_STEP:
            steps = max(0.0, base_capital / self._cfg.equity_step_amount)
            raw_lot = steps * self._cfg.equity_step_lot
            risk_budget = raw_lot * sl_loss_per_lot
            return raw_lot, risk_budget, sl_loss_per_lot

        # LotMode.DYNAMIC_RISK
        risk_budget = base_capital * (self._cfg.risk_percent / 100.0)
        raw_lot = risk_budget / sl_loss_per_lot
        return raw_lot, risk_budget, sl_loss_per_lot

    def _point_value_loss_per_lot(self, spec: SymbolSpec) -> float:
        tick_size = spec.effective_tick_size
        tick_val = spec.effective_tick_value_loss
        if tick_size <= 0 or tick_val <= 0 or spec.point <= 0:
            return 0.0
        return (spec.point / tick_size) * tick_val

    def _max_usable_new_margin(self, account: AccountState) -> float:
        equity = max(0.0, account.equity)
        if self._cfg.max_margin_usage_pct is not None and self._cfg.max_margin_usage_pct > 0:
            max_total_by_pct = equity * (self._cfg.max_margin_usage_pct / 100.0)
            budget_by_pct = max(0.0, max_total_by_pct - account.margin)
        else:
            budget_by_pct = math.inf

        required_ml = self._required_margin_level_pct(account)
        max_total_by_ml = (equity * 100.0) / required_ml if required_ml > 0 else math.inf
        budget_by_ml = max(0.0, max_total_by_ml - account.margin) if math.isfinite(max_total_by_ml) else math.inf

        return max(0.0, min(account.margin_free, budget_by_pct, budget_by_ml))

    def _required_margin_level_pct(self, account: AccountState) -> float:
        req = (
            float(self._cfg.min_margin_level_pct)
            if (self._cfg.min_margin_level_pct is not None and self._cfg.min_margin_level_pct > 0)
            else 0.0
        )
        mult = self._cfg.min_stopout_buffer_multiplier
        if mult is not None and mult > 0 and account.margin_so_mode == int(StopOutMode.PERCENT) and account.margin_so_so > 0:
            req = max(req, account.margin_so_so * mult)
        return req

    def _apply_leverage_and_margin_caps(
        self,
        candidate_lot: float,
        account: AccountState,
        spec: SymbolSpec,
        notional_per_lot: float,
        margin_per_lot: float,
        positions: list[OpenPosition],
        gateway: Optional[MarketGatewayPort],
        clamp_reasons: list[str],
        rejection_reasons: list[str],
    ) -> float:
        equity = account.equity
        existing_sym_notional, existing_port_notional = self._existing_notionals(
            positions=positions,
            current_symbol=spec.name,
            current_notional_per_lot=notional_per_lot,
            gateway=gateway,
        )

        limits: list[tuple[str, float]] = []

        # Cap 1: Max effective leverage for this symbol (optional: None or <= 0 disables)
        if self._cfg.max_effective_symbol_leverage is not None and self._cfg.max_effective_symbol_leverage > 0:
            max_sym_notional = equity * self._cfg.max_effective_symbol_leverage
            rem_sym_notional = max(0.0, max_sym_notional - existing_sym_notional)
            limits.append(
                ("symbol_effective_leverage", rem_sym_notional / notional_per_lot if notional_per_lot > 0 else 0.0)
            )

        # Cap 2: Max effective leverage across whole account portfolio (optional: None or <= 0 disables)
        if self._cfg.max_effective_account_leverage is not None and self._cfg.max_effective_account_leverage > 0:
            max_port_notional = equity * self._cfg.max_effective_account_leverage
            rem_port_notional = max(0.0, max_port_notional - existing_port_notional)
            limits.append(
                ("account_effective_leverage", rem_port_notional / notional_per_lot if notional_per_lot > 0 else 0.0)
            )

        # Cap 3: Margin budget (Free margin + optional Max Margin Usage % + optional Min Margin Level %)
        usable_new_margin = self._max_usable_new_margin(account)
        if margin_per_lot > 0:
            limits.append(("margin_safety_cap", usable_new_margin / margin_per_lot))

        if not limits:
            return candidate_lot

        tightest_name, tightest_lot = min(limits, key=lambda item: item[1])
        if candidate_lot > tightest_lot:
            if self._cfg.scale_down_lot_for_leverage:
                clamp_reasons.append(
                    f"Scaled down by {tightest_name} ({candidate_lot:.4f} -> {tightest_lot:.4f})"
                )
                return tightest_lot
            rejection_reasons.append(
                f"Lot {candidate_lot:.4f} exceeds {tightest_name} limit ({tightest_lot:.4f})"
            )
            return 0.0

        return candidate_lot

    def _existing_notionals(
        self,
        positions: list[OpenPosition],
        current_symbol: str,
        current_notional_per_lot: float,
        gateway: Optional[MarketGatewayPort] = None,
    ) -> tuple[float, float]:
        sym_notional = 0.0
        port_notional = 0.0
        spec_cache: dict[str, Optional[SymbolSpec]] = {}

        for pos in positions:
            if pos.notional_value is not None and pos.notional_value > 0:
                pos_notional = pos.notional_value
            elif pos.symbol == current_symbol:
                pos_notional = pos.volume * current_notional_per_lot
            else:
                if pos.symbol not in spec_cache and gateway is not None:
                    spec_cache[pos.symbol] = gateway.get_symbol_spec(pos.symbol)
                other_spec = spec_cache.get(pos.symbol)
                if other_spec is not None:
                    ref_price = pos.price_current if pos.price_current > 0 else pos.price_open
                    pos_notional = pos.volume * estimate_notional_per_lot(other_spec, ref_price)
                else:
                    pos_notional = pos.volume * current_notional_per_lot

            port_notional += pos_notional
            if pos.symbol == current_symbol:
                sym_notional += pos_notional

        return sym_notional, port_notional

    def _build_leverage_snapshot(
        self,
        account: AccountState,
        spec: SymbolSpec,
        lot: float,
        margin_per_lot: float,
        notional_per_lot: float,
        effective_symbol_leverage: float,
        positions: list[OpenPosition],
        gateway: Optional[MarketGatewayPort] = None,
        override_new_margin: Optional[float] = None,
    ) -> LeverageSnapshot:
        existing_sym_notional, existing_port_notional = self._existing_notionals(
            positions=positions,
            current_symbol=spec.name,
            current_notional_per_lot=notional_per_lot,
            gateway=gateway,
        )
        new_notional = lot * notional_per_lot
        new_margin = override_new_margin if override_new_margin is not None else (lot * margin_per_lot)

        proj_sym_notional = existing_sym_notional + new_notional
        proj_port_notional = existing_port_notional + new_notional
        proj_margin = account.margin + new_margin
        proj_free = account.margin_free - new_margin

        equity = max(0.0, account.equity)
        proj_sym_lev = proj_sym_notional / equity if equity > 0 else 0.0
        proj_port_lev = proj_port_notional / equity if equity > 0 else 0.0
        proj_ml = (equity / proj_margin) * 100.0 if proj_margin > 0 else math.inf

        return LeverageSnapshot(
            account_leverage=account.leverage,
            margin_per_lot=margin_per_lot,
            notional_per_lot=notional_per_lot,
            effective_symbol_leverage=effective_symbol_leverage,
            projected_symbol_notional=proj_sym_notional,
            projected_effective_symbol_leverage=proj_sym_lev,
            projected_portfolio_notional=proj_port_notional,
            projected_effective_portfolio_leverage=proj_port_lev,
            projected_margin_used=proj_margin,
            projected_free_margin=proj_free,
            projected_margin_level_pct=proj_ml,
        )
