from __future__ import annotations

from typing import Any, Optional

import MetaTrader5 as mt5

from cfd_bot.domain.models import (
    ExecutionResult,
    LotPlan,
    OpenPosition,
    OrderSide,
    PositionAdjustment,
    SymbolSpec,
    TradeSignal,
)
from cfd_bot.domain.ports import MarketGatewayPort, TradeManagementPolicyPort


class OrderExecutor:
    """Validates orders via MT5 order_check, executes market orders, and manages open SL protection."""

    def __init__(
        self,
        gateway: MarketGatewayPort,
        magic_number: int = 202611,
        deviation_points: int = 20,
        dry_run: bool = True,
    ) -> None:
        self._gw = gateway
        self._magic = magic_number
        self._deviation = deviation_points
        self._dry_run = dry_run

    def execute_market_order(
        self,
        spec: SymbolSpec,
        signal: TradeSignal,
        lot_plan: LotPlan,
        comment: str = "CFD_Bot",
    ) -> ExecutionResult:
        if not lot_plan.approved or lot_plan.lot <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=self._dry_run,
                retcode=-1,
                order_ticket=0,
                volume=0.0,
                price=0.0,
                comment="LotPlan not approved",
            )

        order_type = mt5.ORDER_TYPE_BUY if signal.side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        price = spec.ask if signal.side == OrderSide.BUY else spec.bid
        if price <= 0:
            price = signal.entry_price

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": spec.name,
            "volume": float(lot_plan.lot),
            "type": order_type,
            "price": round(price, spec.digits),
            "sl": round(signal.sl_price, spec.digits),
            "tp": round(signal.tp_price, spec.digits),
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._resolve_filling_mode(spec.filling_mode),
        }

        check = self._gw.order_check(request)
        check_retcode = int(check.get("retcode", -1))
        if check_retcode not in (0, mt5.TRADE_RETCODE_DONE):
            return ExecutionResult(
                executed=False,
                dry_run=self._dry_run,
                retcode=check_retcode,
                order_ticket=0,
                volume=lot_plan.lot,
                price=price,
                comment=f"order_check failed: {check.get('comment', '')}",
                order_check_margin=float(check.get("margin", 0.0)),
                order_check_free_margin=float(check.get("margin_free", 0.0)),
            )

        if self._dry_run:
            return ExecutionResult(
                executed=False,
                dry_run=True,
                retcode=0,
                order_ticket=0,
                volume=lot_plan.lot,
                price=price,
                comment="DRY_RUN passed order_check",
                order_check_margin=float(check.get("margin", 0.0)),
                order_check_free_margin=float(check.get("margin_free", 0.0)),
            )

        send_res = self._gw.order_send(request)
        retcode = int(send_res.get("retcode", -1))
        success = retcode == mt5.TRADE_RETCODE_DONE
        return ExecutionResult(
            executed=success,
            dry_run=False,
            retcode=retcode,
            order_ticket=int(send_res.get("order", 0)),
            volume=float(send_res.get("volume", lot_plan.lot)),
            price=float(send_res.get("price", price)),
            comment=str(send_res.get("comment", "")),
            order_check_margin=float(check.get("margin", 0.0)),
            order_check_free_margin=float(check.get("margin_free", 0.0)),
        )

    def manage_open_positions(
        self,
        spec: SymbolSpec,
        positions: list[OpenPosition],
        trade_cfg: TradeManagementPolicyPort,
        commission_per_lot: float,
        estimated_slippage_points: float,
        atr: Optional[float] = None,
    ) -> list[PositionAdjustment]:
        """Applies True Break-Even and ATR Trailing Stop to bot-owned positions on spec.name."""
        adjustments: list[PositionAdjustment] = []
        if not trade_cfg.enable_auto_breakeven and not trade_cfg.enable_trailing_stop:
            return adjustments

        point = spec.point if spec.point > 0 else 0.0001
        tick_size = spec.effective_tick_size if spec.effective_tick_size > 0 else point
        point_loss = (point / tick_size) * spec.effective_tick_value_loss if tick_size > 0 else 0.0
        extra_cost_pts = (commission_per_lot / point_loss) + estimated_slippage_points if point_loss > 0 else 0.0
        be_buffer_price = (trade_cfg.breakeven_lock_points + extra_cost_pts) * point
        min_stop_dist = max(spec.trade_stops_level, spec.trade_freeze_level) * point
        step_dist = trade_cfg.trailing_step_points * point

        for pos in positions:
            if pos.magic != self._magic or pos.symbol != spec.name:
                continue

            target_sl: Optional[float] = None
            action_name = ""
            market_ref = spec.bid if pos.side == OrderSide.BUY else spec.ask
            if market_ref <= 0:
                continue

            # 1. Auto True Break-Even (triggers only while SL is still on the loss side of entry)
            if trade_cfg.enable_auto_breakeven and pos.sl > 0:
                if pos.side == OrderSide.BUY and pos.sl < pos.price_open:
                    initial_risk = pos.price_open - pos.sl
                    if (market_ref - pos.price_open) >= trade_cfg.breakeven_trigger_r * initial_risk:
                        cand_be = round(pos.price_open + be_buffer_price, spec.digits)
                        if cand_be > pos.sl and (market_ref - cand_be) >= min_stop_dist:
                            target_sl = cand_be
                            action_name = "TRUE_BREAKEVEN"
                elif pos.side == OrderSide.SELL and pos.sl > pos.price_open:
                    initial_risk = pos.sl - pos.price_open
                    if (pos.price_open - market_ref) >= trade_cfg.breakeven_trigger_r * initial_risk:
                        cand_be = round(pos.price_open - be_buffer_price, spec.digits)
                        if cand_be < pos.sl and (cand_be - market_ref) >= min_stop_dist:
                            target_sl = cand_be
                            action_name = "TRUE_BREAKEVEN"

            # 2. ATR Trailing Stop (only trails forward in profit territory)
            if trade_cfg.enable_trailing_stop and atr is not None and atr > 0:
                trail_dist = trade_cfg.trailing_atr_mult * atr
                current_floor = target_sl if target_sl is not None else pos.sl
                if pos.side == OrderSide.BUY:
                    cand_trail = round(market_ref - trail_dist, spec.digits)
                    if (
                        cand_trail > pos.price_open
                        and (current_floor <= 0 or (cand_trail - current_floor) >= step_dist)
                        and (market_ref - cand_trail) >= min_stop_dist
                    ):
                        target_sl = cand_trail
                        action_name = "ATR_TRAILING"
                else:
                    cand_trail = round(market_ref + trail_dist, spec.digits)
                    if (
                        cand_trail < pos.price_open
                        and (current_floor <= 0 or (current_floor - cand_trail) >= step_dist)
                        and (cand_trail - market_ref) >= min_stop_dist
                    ):
                        target_sl = cand_trail
                        action_name = "ATR_TRAILING"

            if target_sl is None:
                continue

            if self._dry_run:
                adjustments.append(
                    PositionAdjustment(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        action_name=action_name,
                        old_sl=pos.sl,
                        new_sl=target_sl,
                        executed=False,
                        dry_run=True,
                        retcode=0,
                        comment="DRY_RUN SL modification skipped",
                    )
                )
                continue

            mod_req: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": target_sl,
                "tp": pos.tp,
            }
            res = self._gw.order_send(mod_req)
            retcode = int(res.get("retcode", -1))
            adjustments.append(
                PositionAdjustment(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    action_name=action_name,
                    old_sl=pos.sl,
                    new_sl=target_sl,
                    executed=retcode == mt5.TRADE_RETCODE_DONE,
                    dry_run=False,
                    retcode=retcode,
                    comment=str(res.get("comment", "")),
                )
            )

        return adjustments

    def close_position(
        self,
        pos: OpenPosition,
        spec: SymbolSpec,
        comment: str = "Ops_Close",
    ) -> ExecutionResult:
        """Closes a specific open position by ticket."""
        close_type = mt5.ORDER_TYPE_SELL if pos.side == OrderSide.BUY else mt5.ORDER_TYPE_BUY
        price = spec.bid if close_type == mt5.ORDER_TYPE_SELL else spec.ask
        if price <= 0:
            price = pos.price_current

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": float(pos.volume),
            "type": close_type,
            "position": int(pos.ticket),
            "price": round(price, spec.digits),
            "deviation": self._deviation,
            "magic": pos.magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._resolve_filling_mode(spec.filling_mode),
        }

        if self._dry_run:
            return ExecutionResult(
                executed=False,
                dry_run=True,
                retcode=0,
                order_ticket=pos.ticket,
                volume=pos.volume,
                price=price,
                comment="DRY_RUN close skipped",
            )

        res = self._gw.order_send(request)
        retcode = int(res.get("retcode", -1))
        return ExecutionResult(
            executed=retcode == mt5.TRADE_RETCODE_DONE,
            dry_run=False,
            retcode=retcode,
            order_ticket=int(res.get("order", pos.ticket)),
            volume=float(res.get("volume", pos.volume)),
            price=float(res.get("price", price)),
            comment=str(res.get("comment", "")),
        )

    def move_position_to_breakeven(
        self,
        pos: OpenPosition,
        spec: SymbolSpec,
        lock_points: int,
        commission_per_lot: float,
        estimated_slippage_points: float,
    ) -> PositionAdjustment:
        """Manually moves a position SL to True Break-Even covering transaction costs."""
        point = spec.point if spec.point > 0 else 0.0001
        tick_size = spec.effective_tick_size if spec.effective_tick_size > 0 else point
        point_loss = (point / tick_size) * spec.effective_tick_value_loss if tick_size > 0 else 0.0
        extra_cost_pts = (commission_per_lot / point_loss) + estimated_slippage_points if point_loss > 0 else 0.0
        be_buffer = (lock_points + extra_cost_pts) * point
        min_stop_dist = max(spec.trade_stops_level, spec.trade_freeze_level) * point

        if pos.side == OrderSide.BUY:
            new_sl = round(pos.price_open + be_buffer, spec.digits)
            market_ref = spec.bid
            valid = market_ref > new_sl and (market_ref - new_sl) >= min_stop_dist
        else:
            new_sl = round(pos.price_open - be_buffer, spec.digits)
            market_ref = spec.ask
            valid = market_ref < new_sl and (new_sl - market_ref) >= min_stop_dist

        if not valid:
            return PositionAdjustment(
                ticket=pos.ticket,
                symbol=pos.symbol,
                action_name="MANUAL_TRUE_BE",
                old_sl=pos.sl,
                new_sl=new_sl,
                executed=False,
                dry_run=self._dry_run,
                retcode=-1,
                comment="Current price has not cleared True Break-Even + stops_level",
            )

        if self._dry_run:
            return PositionAdjustment(
                ticket=pos.ticket,
                symbol=pos.symbol,
                action_name="MANUAL_TRUE_BE",
                old_sl=pos.sl,
                new_sl=new_sl,
                executed=False,
                dry_run=True,
                retcode=0,
                comment="DRY_RUN True BE move skipped",
            )

        res = self._gw.order_send(
            {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": new_sl,
                "tp": pos.tp,
            }
        )
        retcode = int(res.get("retcode", -1))
        return PositionAdjustment(
            ticket=pos.ticket,
            symbol=pos.symbol,
            action_name="MANUAL_TRUE_BE",
            old_sl=pos.sl,
            new_sl=new_sl,
            executed=retcode == mt5.TRADE_RETCODE_DONE,
            dry_run=False,
            retcode=retcode,
            comment=str(res.get("comment", "")),
        )

    @staticmethod
    def _resolve_filling_mode(filling_flags: int) -> int:
        if filling_flags & 2:
            return mt5.ORDER_FILLING_IOC
        if filling_flags & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN
