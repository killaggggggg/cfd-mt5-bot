from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from cfd_bot.domain.models import (
    AccountState,
    EconomicEvent,
    OpenPosition,
    OrderSide,
    PreventiveCostBreakdown,
    PreventiveDecision,
    SwapMode,
    SymbolSpec,
    SymbolTradeMode,
    TradeSignal,
)
from cfd_bot.domain.ports import CalendarPort, PreventivePolicyPort
from cfd_bot.domain.risk_engine import estimate_notional_per_lot


class PreventiveGuard:
    """
    Pre-trade preventive analyzer and gatekeeper for CFD trading.

    Computes explicit transaction costs (spread, swap across MT5 swap modes + 3x rollover,
    commission, slippage), checks true Net R:R, enforces market/account safety caps,
    and checks economic news & bank holiday blackout windows.
    """

    def __init__(
        self,
        config: PreventivePolicyPort,
        calendar_service: Optional[CalendarPort] = None,
    ) -> None:
        self._cfg = config
        self._calendar = calendar_service
        self._baseline_date: Optional[date] = None
        self._daily_starting_equity: Optional[float] = None

    def reset_daily_baseline(self, equity: float, now: datetime) -> None:
        """Initializes or resets daily starting equity on a new UTC/server date."""
        today = now.date()
        if self._baseline_date != today or self._daily_starting_equity is None:
            self._baseline_date = today
            self._daily_starting_equity = equity

    def daily_drawdown_pct(self, current_equity: float, now: datetime) -> float:
        """Returns current day drawdown percentage (0.0 if in profit)."""
        self.reset_daily_baseline(current_equity, now)
        assert self._daily_starting_equity is not None
        if self._daily_starting_equity <= 0:
            return 0.0
        loss = max(0.0, self._daily_starting_equity - current_equity)
        return (loss / self._daily_starting_equity) * 100.0

    def evaluate(
        self,
        account: AccountState,
        spec: SymbolSpec,
        signal: TradeSignal,
        open_positions: Optional[list[OpenPosition]] = None,
        now: Optional[datetime] = None,
    ) -> PreventiveDecision:
        """Runs all preventive checks and returns full cost breakdown + approval."""
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        server_time = self._to_server_time(current_time, spec.server_utc_offset_seconds)

        positions = open_positions or []
        reasons: list[str] = []
        warnings: list[str] = []

        entry = signal.entry_price if signal.entry_price > 0 else (
            spec.ask if signal.side == OrderSide.BUY else spec.bid
        )

        # 1. Calculate explicit per-lot costs & Net R:R (using broker server time for rollover)
        costs = self.analyze_costs(spec=spec, signal=signal, entry=entry, now=server_time)

        # 2. Daily drawdown circuit breaker (optional: None or <= 0 disables)
        dd_pct = self.daily_drawdown_pct(account.equity, server_time)
        if self._cfg.max_daily_loss_pct is not None and self._cfg.max_daily_loss_pct > 0:
            if dd_pct >= self._cfg.max_daily_loss_pct:
                reasons.append(
                    f"Daily drawdown {dd_pct:.2f}% reached max limit {self._cfg.max_daily_loss_pct:.2f}%"
                )

        # 3. Account & symbol trading permissions
        if not account.trade_allowed:
            reasons.append("Account trading disabled by broker")
        if not account.trade_expert:
            reasons.append("Algorithmic trading (Expert Advisors) disabled on account")

        self._check_symbol_trade_mode(spec, signal.side, reasons)

        # 4. Weekend & stale quote protection (MT5 tick.time uses broker server clock)
        if not self._cfg.allow_weekend_trading and server_time.weekday() >= 5:
            reasons.append("Weekend market guard active")

        if spec.quote_time is not None and self._cfg.max_quote_age_seconds is not None and self._cfg.max_quote_age_seconds > 0:
            qt = spec.quote_time if spec.quote_time.tzinfo else spec.quote_time.replace(tzinfo=timezone.utc)
            age_sec = (server_time - qt).total_seconds()
            if age_sec > self._cfg.max_quote_age_seconds:
                reasons.append(
                    f"Stale quote ({age_sec:.0f}s > {self._cfg.max_quote_age_seconds}s); market may be closed"
                )

        # 5. Open position limits (optional: None or <= 0 disables)
        if self._cfg.max_open_positions_total is not None and self._cfg.max_open_positions_total > 0:
            if len(positions) >= self._cfg.max_open_positions_total:
                reasons.append(
                    f"Total open positions {len(positions)} reached limit {self._cfg.max_open_positions_total}"
                )
        sym_positions = sum(1 for p in positions if p.symbol == spec.name)
        if self._cfg.max_open_positions_symbol is not None and self._cfg.max_open_positions_symbol > 0:
            if sym_positions >= self._cfg.max_open_positions_symbol:
                reasons.append(
                    f"Open positions for {spec.name} ({sym_positions}) reached limit {self._cfg.max_open_positions_symbol}"
                )

        # 6. Rollover blackout window check for intraday trades
        if self._cfg.enforce_rollover_blackout_for_intraday and signal.expected_hold_days <= 0.0:
            if self._is_in_rollover_blackout(server_time):
                reasons.append(
                    f"Inside swap rollover blackout window around server hour {self._cfg.swap_rollover_hour_server:02d}:00"
                )

        # 7. SL/TP geometry & broker Stops/Freeze level validation
        self._check_stops_and_geometry(spec, signal, entry, reasons)

        # 8. Spread preventive thresholds (None or <= 0 disables each individual guard)
        if self._cfg.max_spread_points is not None and self._cfg.max_spread_points > 0:
            if costs.spread_points > self._cfg.max_spread_points:
                reasons.append(
                    f"Spread {costs.spread_points:.1f} pts exceeds max {self._cfg.max_spread_points:.1f} pts"
                )
            elif costs.spread_points >= 0.75 * self._cfg.max_spread_points:
                warnings.append(
                    f"Elevated spread: {costs.spread_points:.1f} pts (limit {self._cfg.max_spread_points:.1f})"
                )

        if self._cfg.max_spread_to_tp_ratio is not None and self._cfg.max_spread_to_tp_ratio > 0:
            if costs.spread_to_tp_ratio > self._cfg.max_spread_to_tp_ratio:
                reasons.append(
                    f"Spread consumes {costs.spread_to_tp_ratio:.1%} of TP (max {self._cfg.max_spread_to_tp_ratio:.1%})"
                )

        if self._cfg.max_spread_to_atr_ratio is not None and self._cfg.max_spread_to_atr_ratio > 0:
            if costs.spread_to_atr_ratio is not None and costs.spread_to_atr_ratio > self._cfg.max_spread_to_atr_ratio:
                reasons.append(
                    f"Spread/ATR spike {costs.spread_to_atr_ratio:.1%} exceeds max {self._cfg.max_spread_to_atr_ratio:.1%}"
                )

        # 9. Swap preventive thresholds
        if costs.triple_swap_applied and costs.swap_cost_per_lot > 0:
            warnings.append(
                f"3x rollover swap projected (${costs.swap_cost_per_lot:.2f}/lot over {costs.swap_days_projected:.1f} eq. days)"
            )
        if self._cfg.max_swap_to_tp_ratio is not None and self._cfg.max_swap_to_tp_ratio > 0:
            if costs.swap_to_tp_ratio > self._cfg.max_swap_to_tp_ratio:
                reasons.append(
                    f"Projected swap consumes {costs.swap_to_tp_ratio:.1%} of TP (max {self._cfg.max_swap_to_tp_ratio:.1%})"
                )

        # 10. Net Risk:Reward after all transaction costs (None or <= 0 disables)
        if self._cfg.min_net_rr is not None and self._cfg.min_net_rr > 0:
            if costs.net_tp_reward_per_lot <= 0.0:
                reasons.append(
                    f"Net TP reward after costs is non-positive (${costs.net_tp_reward_per_lot:.2f}/lot)"
                )
            elif costs.net_rr < self._cfg.min_net_rr:
                reasons.append(
                    f"Net R:R {costs.net_rr:.2f} below minimum {self._cfg.min_net_rr:.2f} (Gross R:R {costs.gross_rr:.2f})"
                )

        # 11. High-impact economic news & bank holiday blackout check
        upcoming_events: list[EconomicEvent] = []
        if self._calendar is not None and (self._cfg.enable_news_guard or self._cfg.enable_holiday_guard):
            cal_res = self._calendar.evaluate(
                spec=spec,
                now=current_time,
                expected_hold_days=signal.expected_hold_days,
            )
            upcoming_events = cal_res.upcoming_events
            if cal_res.blocked:
                reasons.extend(cal_res.reasons)
            warnings.extend(cal_res.warnings)

        return PreventiveDecision(
            approved=len(reasons) == 0,
            reasons=reasons,
            warnings=warnings,
            costs=costs,
            upcoming_events=upcoming_events,
        )

    def analyze_costs(
        self,
        spec: SymbolSpec,
        signal: TradeSignal,
        entry: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> PreventiveCostBreakdown:
        """Calculates spread, swap, commission, slippage, and net R:R per 1.0 lot."""
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        eff_entry = entry if (entry is not None and entry > 0) else (
            spec.ask if signal.side == OrderSide.BUY else spec.bid
        )
        point = spec.point if spec.point > 0 else 0.0001
        tick_size = spec.effective_tick_size if spec.effective_tick_size > 0 else point
        tick_val_loss = spec.effective_tick_value_loss
        tick_val_profit = spec.effective_tick_value_profit

        point_loss_per_lot = (point / tick_size) * tick_val_loss if tick_size > 0 else 0.0
        point_profit_per_lot = (point / tick_size) * tick_val_profit if tick_size > 0 else 0.0

        # Spread
        if spec.ask > 0 and spec.bid > 0 and point > 0:
            spread_points = max(0.0, (spec.ask - spec.bid) / point)
        else:
            spread_points = float(max(0, spec.spread_points))
        spread_cost_per_lot = spread_points * point_loss_per_lot

        # Commission (auto-detected from broker deal history if available and enabled) & slippage
        if self._cfg.auto_detect_commission and spec.detected_commission_per_lot is not None:
            commission_per_lot = spec.detected_commission_per_lot
        else:
            commission_per_lot = float(self._cfg.commission_per_lot_roundtrip or 0.0)
        slippage_cost_per_lot = float(self._cfg.estimated_slippage_points or 0.0) * point_loss_per_lot

        # Swap projection
        swap_days, triple_applied = self.project_swap_days(
            now=current_time,
            expected_hold_days=signal.expected_hold_days,
            rollover3days=spec.swap_rollover3days,
        )
        raw_swap = spec.swap_long if signal.side == OrderSide.BUY else spec.swap_short
        daily_swap_pnl = self._daily_swap_pnl_per_lot(
            spec=spec,
            raw_swap=raw_swap,
            price=eff_entry,
            point_loss_per_lot=point_loss_per_lot,
            point_profit_per_lot=point_profit_per_lot,
        )
        swap_pnl_per_lot = daily_swap_pnl * swap_days
        swap_cost_per_lot = max(0.0, -swap_pnl_per_lot)

        total_cost_per_lot = spread_cost_per_lot + commission_per_lot + slippage_cost_per_lot + swap_cost_per_lot

        # Gross SL / TP distances
        sl_dist = abs(eff_entry - signal.sl_price) if eff_entry > 0 else 0.0
        tp_dist = abs(signal.tp_price - eff_entry) if eff_entry > 0 else 0.0

        gross_sl_risk = (sl_dist / tick_size) * tick_val_loss if tick_size > 0 else 0.0
        gross_tp_reward = (tp_dist / tick_size) * tick_val_profit if tick_size > 0 else 0.0

        # Entry price is already Ask (BUY) or Bid (SELL), so spread is already baked into entry-to-SL/TP;
        # commission, slippage, and swap are incremental cash flows on top.
        net_sl_risk = gross_sl_risk + commission_per_lot + slippage_cost_per_lot + swap_cost_per_lot
        net_tp_reward = gross_tp_reward - commission_per_lot - slippage_cost_per_lot + swap_pnl_per_lot

        gross_rr = (gross_tp_reward / gross_sl_risk) if gross_sl_risk > 0 else 0.0
        net_rr = (net_tp_reward / net_sl_risk) if net_sl_risk > 0 else 0.0

        spread_to_tp = (spread_cost_per_lot / gross_tp_reward) if gross_tp_reward > 0 else math.inf
        swap_to_tp = (swap_cost_per_lot / gross_tp_reward) if (gross_tp_reward > 0 and swap_cost_per_lot > 0) else 0.0
        spread_to_atr = (spec.spread_price / signal.atr) if (signal.atr and signal.atr > 0) else None

        extra_cost_points = (
            (commission_per_lot + slippage_cost_per_lot + swap_cost_per_lot) / point_loss_per_lot
            if point_loss_per_lot > 0
            else 0.0
        )
        true_breakeven_points = spread_points + extra_cost_points

        return PreventiveCostBreakdown(
            spread_points=spread_points,
            spread_cost_per_lot=spread_cost_per_lot,
            commission_per_lot=commission_per_lot,
            slippage_cost_per_lot=slippage_cost_per_lot,
            swap_rate_raw=raw_swap,
            swap_mode=spec.swap_mode,
            swap_days_projected=swap_days,
            triple_swap_applied=triple_applied,
            swap_pnl_per_lot=swap_pnl_per_lot,
            swap_cost_per_lot=swap_cost_per_lot,
            total_cost_per_lot=total_cost_per_lot,
            gross_sl_risk_per_lot=gross_sl_risk,
            gross_tp_reward_per_lot=gross_tp_reward,
            net_sl_risk_per_lot=net_sl_risk,
            net_tp_reward_per_lot=net_tp_reward,
            gross_rr=gross_rr,
            net_rr=net_rr,
            spread_to_tp_ratio=spread_to_tp,
            swap_to_tp_ratio=swap_to_tp,
            spread_to_atr_ratio=spread_to_atr,
            true_breakeven_points=true_breakeven_points,
        )

    def project_swap_days(
        self,
        now: datetime,
        expected_hold_days: float,
        rollover3days: int,
    ) -> tuple[float, bool]:
        """
        Projects effective swap rollover days including broker 3-day swap day.

        MT5 DAY_OF_WEEK mapping: Sunday=0, Monday=1, ..., Wednesday=3, ..., Saturday=6.
        Python weekday() mapping: Monday=0, ..., Sunday=6 -> mt5_dow = (py_wd + 1) % 7.
        """
        if expected_hold_days <= 0.0:
            return 0.0, False

        nights = max(1, math.ceil(expected_hold_days))
        rollover_today = now.replace(
            hour=self._cfg.swap_rollover_hour_server,
            minute=0,
            second=0,
            microsecond=0,
        )
        first_rollover = rollover_today if now < rollover_today else rollover_today + timedelta(days=1)

        total_days = 0.0
        triple_applied = False
        # Early-morning rollovers (e.g. 00:00 server midnight) settle the prior trading day
        settle_prev_day = self._cfg.swap_rollover_hour_server < 12

        for idx in range(nights):
            r_dt = first_rollover + timedelta(days=idx)
            settled_dt = (r_dt - timedelta(days=1)) if settle_prev_day else r_dt
            mt5_dow = (settled_dt.weekday() + 1) % 7
            # Traditional CFD/FX markets skip Sat(6) and Sun(0) swap; 3x day covers the weekend
            if mt5_dow in (0, 6) and rollover3days not in (0, 6):
                continue
            if mt5_dow == rollover3days:
                total_days += 3.0
                triple_applied = True
            else:
                total_days += 1.0

        return total_days, triple_applied

    def _daily_swap_pnl_per_lot(
        self,
        spec: SymbolSpec,
        raw_swap: float,
        price: float,
        point_loss_per_lot: float,
        point_profit_per_lot: float,
    ) -> float:
        mode = spec.swap_mode
        if mode == int(SwapMode.DISABLED) or raw_swap == 0.0:
            return 0.0

        if mode in (int(SwapMode.POINTS), int(SwapMode.REOPEN_CURRENT), int(SwapMode.REOPEN_BID)):
            pv = point_profit_per_lot if raw_swap >= 0 else point_loss_per_lot
            return raw_swap * pv

        if mode == int(SwapMode.CURRENCY_SYMBOL):
            return raw_swap * self._cfg.symbol_currency_to_account_rate

        if mode == int(SwapMode.CURRENCY_MARGIN):
            return raw_swap * self._cfg.margin_currency_to_account_rate

        if mode == int(SwapMode.CURRENCY_DEPOSIT):
            return raw_swap

        if mode in (int(SwapMode.INTEREST_CURRENT), int(SwapMode.INTEREST_OPEN)):
            notional = estimate_notional_per_lot(spec, price)
            return (notional * (raw_swap / 100.0)) / float(self._cfg.swap_year_days)

        return 0.0

    @staticmethod
    def _to_server_time(utc_dt: datetime, offset_seconds: Optional[int]) -> datetime:
        if offset_seconds is None or offset_seconds == 0:
            return utc_dt
        return utc_dt + timedelta(seconds=offset_seconds)

    def _is_in_rollover_blackout(self, now: datetime) -> bool:
        rollover_today = now.replace(
            hour=self._cfg.swap_rollover_hour_server,
            minute=0,
            second=0,
            microsecond=0,
        )
        candidates = [
            rollover_today - timedelta(days=1),
            rollover_today,
            rollover_today + timedelta(days=1),
        ]
        before_mins = int(self._cfg.rollover_blackout_minutes_before or 0)
        after_mins = int(self._cfg.rollover_blackout_minutes_after or 0)
        if before_mins <= 0 and after_mins <= 0:
            return False
        before = timedelta(minutes=before_mins)
        after = timedelta(minutes=after_mins)
        return any((center - before) <= now <= (center + after) for center in candidates)

    def _check_symbol_trade_mode(
        self,
        spec: SymbolSpec,
        side: OrderSide,
        reasons: list[str],
    ) -> None:
        if spec.trade_mode == int(SymbolTradeMode.DISABLED):
            reasons.append(f"Symbol {spec.name} trading is disabled")
        elif spec.trade_mode == int(SymbolTradeMode.CLOSEONLY):
            reasons.append(f"Symbol {spec.name} is in close-only mode")
        elif spec.trade_mode == int(SymbolTradeMode.LONGONLY) and side == OrderSide.SELL:
            reasons.append(f"Symbol {spec.name} allows LONG only")
        elif spec.trade_mode == int(SymbolTradeMode.SHORTONLY) and side == OrderSide.BUY:
            reasons.append(f"Symbol {spec.name} allows SHORT only")

    def _check_stops_and_geometry(
        self,
        spec: SymbolSpec,
        signal: TradeSignal,
        entry: float,
        reasons: list[str],
    ) -> None:
        if entry <= 0.0:
            reasons.append("Invalid entry price (<= 0)")
            return

        if signal.side == OrderSide.BUY:
            if not (signal.sl_price < entry < signal.tp_price):
                reasons.append(
                    f"Invalid BUY levels: require SL ({signal.sl_price}) < Entry ({entry}) < TP ({signal.tp_price})"
                )
                return
            market_ref = spec.bid if spec.bid > 0 else entry
        else:
            if not (signal.tp_price < entry < signal.sl_price):
                reasons.append(
                    f"Invalid SELL levels: require TP ({signal.tp_price}) < Entry ({entry}) < SL ({signal.sl_price})"
                )
                return
            market_ref = spec.ask if spec.ask > 0 else entry

        min_dist_points = max(spec.trade_stops_level, spec.trade_freeze_level)
        if min_dist_points > 0 and spec.point > 0:
            min_dist_price = min_dist_points * spec.point
            # Slight float tolerance (1e-9) prevents false rejection on exact boundary
            if abs(market_ref - signal.sl_price) + 1e-9 < min_dist_price:
                reasons.append(
                    f"SL distance {abs(market_ref - signal.sl_price) / spec.point:.1f} pts < broker stops_level {min_dist_points}"
                )
            if abs(signal.tp_price - market_ref) + 1e-9 < min_dist_price:
                reasons.append(
                    f"TP distance {abs(signal.tp_price - market_ref) / spec.point:.1f} pts < broker stops_level {min_dist_points}"
                )
