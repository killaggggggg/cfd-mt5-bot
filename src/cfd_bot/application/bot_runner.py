from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from cfd_bot.application.config import BotConfig
from cfd_bot.domain.models import (
    ExecutionResult,
    LotPlan,
    PositionAdjustment,
    PreventiveDecision,
    TradeSignal,
)
from cfd_bot.domain.ports import CalendarPort, MarketGatewayPort, OrderExecutorPort, StrategyPort
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.domain.risk_engine import RiskEngine

logger = logging.getLogger("cfd_bot")

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


@dataclass(frozen=True)
class EvaluationCycleResult:
    """Result of one single bot cycle (useful for diagnostics and deterministic tests)."""

    status: str
    signal: Optional[TradeSignal] = None
    preventive: Optional[PreventiveDecision] = None
    lot_plan: Optional[LotPlan] = None
    execution: Optional[ExecutionResult] = None
    adjustments: list[PositionAdjustment] = field(default_factory=list)


def configure_logging(log_dir: Path | str = "logs") -> None:
    """Configures clean console + rotating file logging."""
    root_logger = logging.getLogger("cfd_bot")
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root_logger.addHandler(console)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_path / "cfd_bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)


def _compute_closed_atr(rates: pd.DataFrame, period: int = 14) -> Optional[float]:
    if rates.empty or len(rates) < period + 2:
        return None
    closed = rates.iloc[:-1]
    high = closed["high"].astype(float)
    low = closed["low"].astype(float)
    prev_close = closed["close"].astype(float).shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    val = tr.rolling(window=period).mean().iloc[-1]
    return float(val) if pd.notna(val) and val > 0 else None


class BotRunner:
    """Orchestrates Position Protection -> Strategy -> PreventiveGuard -> RiskEngine -> OrderExecutor."""

    def __init__(
        self,
        config: BotConfig,
        gateway: MarketGatewayPort,
        strategy: StrategyPort,
        executor: Optional[OrderExecutorPort] = None,
        calendar_service: Optional[CalendarPort] = None,
    ) -> None:
        if executor is None:
            raise ValueError("BotRunner requires an OrderExecutorPort implementation.")
        self._cfg = config
        self._gw = gateway
        self._strategy = strategy
        self._calendar = calendar_service
        self._guard = PreventiveGuard(config.preventive, calendar_service=self._calendar)
        self._risk = RiskEngine(config.risk)
        self._executor = executor

    def evaluate_once(self, now: Optional[datetime] = None) -> EvaluationCycleResult:
        current_time = now or datetime.now(timezone.utc)
        account = self._gw.get_account_state()
        if account is None:
            return EvaluationCycleResult(status="account_unavailable")

        spec = self._gw.get_symbol_spec(self._cfg.symbol)
        if spec is None:
            return EvaluationCycleResult(status="symbol_unavailable")

        history_bars = max(250, int(getattr(self._strategy, "recommended_history_bars", 250) or 250))
        rates = self._gw.get_rates(spec.name, self._cfg.timeframe, history_bars)
        if rates.empty:
            return EvaluationCycleResult(status="rates_unavailable")

        open_positions = self._gw.get_open_positions(magic=None)
        eff_comm = (
            spec.detected_commission_per_lot
            if (self._cfg.preventive.auto_detect_commission and spec.detected_commission_per_lot is not None)
            else self._cfg.preventive.commission_per_lot_roundtrip
        )
        atr = _compute_closed_atr(rates, period=14)
        adjustments = self._executor.manage_open_positions(
            spec=spec,
            positions=open_positions,
            trade_cfg=self._cfg.trade_management,
            commission_per_lot=eff_comm,
            estimated_slippage_points=float(self._cfg.preventive.estimated_slippage_points or 0.0),
            atr=atr,
        )

        # Enforce optional strategy Time-Stop on stagnant bot positions
        max_hold_bars = max(0, int(getattr(self._strategy, "max_hold_bars", 0) or 0))
        if max_hold_bars > 0 and open_positions:
            tf_sec = TIMEFRAME_SECONDS.get(self._cfg.timeframe.upper(), 300)
            hold_limit_sec = max_hold_bars * tf_sec
            now_ts = int(current_time.timestamp())
            remaining_positions = []
            for pos in open_positions:
                if (
                    pos.magic == self._cfg.magic_number
                    and pos.symbol == spec.name
                    and pos.open_time > 0
                    and (now_ts - int(pos.open_time)) >= hold_limit_sec
                ):
                    close_res = self._executor.close_position(
                        pos=pos,
                        spec=spec,
                        comment=f"TimeStop_{max_hold_bars}bars",
                    )
                    adjustments.append(
                        PositionAdjustment(
                            ticket=pos.ticket,
                            symbol=pos.symbol,
                            action_name="TIME_STOP_CLOSE",
                            old_sl=pos.sl,
                            new_sl=pos.sl,
                            executed=close_res.executed,
                            dry_run=close_res.dry_run,
                            retcode=close_res.retcode,
                            comment=f"Held >= {max_hold_bars} bars ({close_res.comment})",
                        )
                    )
                    if close_res.executed:
                        continue
                remaining_positions.append(pos)
            open_positions = remaining_positions

        signal = self._strategy.generate_signal(spec, rates)
        if signal is None:
            return EvaluationCycleResult(status="no_signal", adjustments=adjustments)

        preventive = self._guard.evaluate(
            account=account,
            spec=spec,
            signal=signal,
            open_positions=open_positions,
            now=current_time,
        )
        if not preventive.approved:
            return EvaluationCycleResult(
                status="blocked_by_preventive_guard",
                signal=signal,
                preventive=preventive,
                adjustments=adjustments,
            )

        lot_plan = self._risk.compute_lot_plan(
            account=account,
            spec=spec,
            signal=signal,
            costs=preventive.costs,
            open_positions=open_positions,
            gateway=self._gw,
        )
        if not lot_plan.approved:
            return EvaluationCycleResult(
                status="rejected_by_risk_engine",
                signal=signal,
                preventive=preventive,
                lot_plan=lot_plan,
                adjustments=adjustments,
            )

        execution = self._executor.execute_market_order(
            spec=spec,
            signal=signal,
            lot_plan=lot_plan,
            comment=self._strategy.name,
        )
        status = (
            "executed"
            if execution.executed
            else ("dry_run_ok" if execution.dry_run and execution.retcode == 0 else "execution_failed")
        )
        return EvaluationCycleResult(
            status=status,
            signal=signal,
            preventive=preventive,
            lot_plan=lot_plan,
            execution=execution,
            adjustments=adjustments,
        )

    def run_forever(self) -> None:
        if not self._gw.connect():
            raise RuntimeError("Failed to connect to MetaTrader 5 terminal")

        account = self._gw.get_account_state()
        spec = self._gw.get_symbol_spec(self._cfg.symbol)
        logger.info(
            "Started CFD MT5 Bot | Server=%s Account=%s Equity=%.2f %s | Symbol=%s TF=%s LotMode=%s DryRun=%s",
            account.server if account else "?",
            account.login if account else "?",
            account.equity if account else 0.0,
            account.currency if account else "",
            spec.name if spec else self._cfg.symbol,
            self._cfg.timeframe,
            self._cfg.risk.lot_mode.value,
            self._cfg.dry_run,
        )
        if spec is not None and (self._cfg.preventive.enable_news_guard or self._cfg.preventive.enable_holiday_guard):
            cal_res = self._calendar.evaluate(spec=spec, now=datetime.now(timezone.utc), expected_hold_days=0.0)
            for warn in cal_res.reasons + cal_res.warnings:
                logger.warning("[Calendar] %s", warn)
            for ev in cal_res.upcoming_events[:5]:
                logger.info(
                    "[Calendar Upcoming] %s | %s | %s (%s)",
                    ev.event_time.strftime("%Y-%m-%d %H:%M UTC"),
                    ev.currency,
                    ev.title,
                    ev.impact.value,
                )

        try:
            while True:
                res = self.evaluate_once()
                for adj in res.adjustments:
                    logger.info(
                        "[%s] Position #%d %s SL %.5f -> %.5f (%s)",
                        adj.symbol,
                        adj.ticket,
                        adj.action_name,
                        adj.old_sl,
                        adj.new_sl,
                        adj.comment,
                    )

                if res.status == "blocked_by_preventive_guard" and res.preventive:
                    logger.warning("Signal blocked by PreventiveGuard: %s", "; ".join(res.preventive.reasons))
                elif res.status == "rejected_by_risk_engine" and res.lot_plan:
                    logger.warning("Order rejected by RiskEngine: %s", "; ".join(res.lot_plan.rejection_reasons))
                elif res.status in ("executed", "dry_run_ok", "execution_failed") and res.lot_plan and res.preventive:
                    snap = res.lot_plan.leverage_snapshot
                    costs = res.preventive.costs
                    logger.info(
                        "[%s] %s %s lot=%.2f | NetRR=%.2f TrueBE=%.1fpts | Margin=%.2f MarginLvl=%.1f%% EffLev=%.2fx | %s",
                        res.status.upper(),
                        res.signal.symbol if res.signal else self._cfg.symbol,
                        res.signal.side.value if res.signal else "",
                        res.lot_plan.lot,
                        costs.net_rr,
                        costs.true_breakeven_points,
                        snap.projected_margin_used,
                        snap.projected_margin_level_pct,
                        snap.projected_effective_portfolio_leverage,
                        res.execution.comment if res.execution else "",
                    )
                elif res.status in ("account_unavailable", "symbol_unavailable", "rates_unavailable"):
                    logger.warning("Cycle skipped: %s", res.status)

                time.sleep(self._cfg.poll_seconds)
        except KeyboardInterrupt:
            logger.info("Stopping bot by user interrupt.")
        finally:
            self._gw.disconnect()
