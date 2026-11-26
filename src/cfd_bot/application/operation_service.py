from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from cfd_bot.application.bot_runner import BotRunner, EvaluationCycleResult, configure_logging
from cfd_bot.application.config import BotConfig, MT5ConnectionConfig, PreventiveConfig, StrategyConfig, load_config_from_env
from cfd_bot.domain.models import (
    LotMode,
    NewsImpact,
    NewsSentiment,
    OrderSide,
    StopOutMode,
    SymbolSpec,
    TradeSignal,
    extract_symbol_currencies,
)
from cfd_bot.domain.ports import CalendarPort, MarketGatewayPort, OrderExecutorPort
from cfd_bot.domain.post_news_analyzer import PostNewsAnalyzer
from cfd_bot.domain.preventive_guard import PreventiveGuard
from cfd_bot.domain.risk_engine import RiskEngine, estimate_notional_per_lot
from cfd_bot.domain.strategies import BaseStrategy, build_strategy, list_strategy_descriptors
from cfd_bot.domain.strategies.structural_poc import analyze_structural_legs
from cfd_bot.domain.strategy_backtester import run_pure_offensive_backtest

logger = logging.getLogger("cfd_bot")


def sanitize_json(value: Any) -> Any:
    """Recursively converts dataclasses, Enums, datetimes, and non-finite floats into strict JSON types."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize_json(asdict(value))
    if isinstance(value, dict):
        return {str(k): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def _compute_h1_atr(gw: MarketGatewayPort, symbol: str, period: int = 14) -> Optional[float]:
    try:
        rates = gw.get_rates(symbol, "H1", period + 10)
    except Exception:
        return None
    if rates.empty or len(rates) < period + 2:
        return None
    closed = rates.iloc[:-1]
    high = closed["high"].astype(float)
    low = closed["low"].astype(float)
    prev_close = closed["close"].astype(float).shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    val = tr.rolling(window=period).mean().iloc[-1]
    return float(val) if pd.notna(val) and val > 0 else None


class RingLogHandler(logging.Handler):
    """Stores recent log lines in memory for fast Operation UI display."""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__()
        self._buf: deque[dict[str, str]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s", datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        ts = self.format(record)
        with self._lock:
            self._buf.append(
                {
                    "time": ts,
                    "level": record.levelname,
                    "message": record.getMessage(),
                }
            )

    def tail(self, limit: int = 100) -> list[dict[str, str]]:
        with self._lock:
            items = list(self._buf)
        return items[-limit:]


class OperationService:
    """Thread-safe coordinator bridging the React Operation UI, MT5 terminal, and BotRunner."""

    def __init__(
        self,
        root_dir: Path | str = ".",
        gateway: Optional[MarketGatewayPort] = None,
        config: Optional[BotConfig] = None,
        calendar_service: Optional[CalendarPort] = None,
        executor_factory: Optional[Callable[[MarketGatewayPort, int, int, bool], OrderExecutorPort]] = None,
        gateway_factory: Optional[Callable[[MT5ConnectionConfig], MarketGatewayPort]] = None,
        calendar_factory: Optional[Callable[[PreventiveConfig, Path, Optional[Path]], CalendarPort]] = None,
        enable_file_log: bool = True,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.env_path = self.root_dir / ".env"
        self.custom_events_path = self.root_dir / "config" / "custom_events.json"

        if enable_file_log:
            configure_logging(self.root_dir / "logs")
        else:
            logger.setLevel(logging.INFO)
        self.ring_handler = RingLogHandler()
        if not any(isinstance(h, RingLogHandler) for h in logger.handlers):
            logger.addHandler(self.ring_handler)

        if config is not None:
            self.config = config
        elif self.env_path.exists():
            self.config = load_config_from_env(str(self.env_path))
        else:
            self.config = BotConfig()

        if gateway is not None:
            self._gw: MarketGatewayPort = gateway
        elif gateway_factory is not None:
            self._gw = gateway_factory(self.config.mt5)
        else:
            raise ValueError("OperationService requires gateway or gateway_factory.")

        self._mt5_lock = threading.RLock()
        self._connected = False
        self._calendar_factory = calendar_factory
        self._executor_factory = executor_factory

        if calendar_service is not None:
            self._calendar: Optional[CalendarPort] = calendar_service
        elif calendar_factory is not None:
            self._calendar = calendar_factory(
                self.config.preventive,
                self.root_dir / "data" / "cache",
                self._resolved_custom_events_path(),
            )
        else:
            self._calendar = None

        try:
            self._strategy: BaseStrategy = build_strategy(
                self.config.strategy.active_strategy,
                self.config.strategy.params,
            )
        except ValueError as exc:
            logger.warning("Configured strategy '%s' unavailable (%s); falling back to M5KernelPullback.", self.config.strategy.active_strategy, exc)
            self._strategy = build_strategy("M5KernelPullback")
        self.config.strategy.active_strategy = self._strategy.name
        self.config.strategy.params = self._strategy.get_params()
        self._runner = BotRunner(
            config=self.config,
            gateway=self._gw,
            strategy=self._strategy,
            executor=self._build_executor(self.config),
            calendar_service=self._calendar,
        )

        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._bot_running = False
        self._cycle_count = 0
        self._last_cycle_time: Optional[datetime] = None
        self._last_cycle: Optional[EvaluationCycleResult] = None
        self._atr_cache: dict[str, tuple[float, Optional[float]]] = {}
        self._enable_file_log = enable_file_log
        self.journal_path = self.root_dir / "logs" / "forward_journal.jsonl"
        self._forward_journal: deque[dict[str, Any]] = deque(maxlen=500)
        self._load_forward_journal()

    def _build_executor(self, cfg: BotConfig) -> OrderExecutorPort:
        if self._executor_factory is None:
            raise ValueError("OperationService requires executor_factory.")
        return self._executor_factory(self._gw, cfg.magic_number, cfg.deviation_points, cfg.dry_run)

    def _resolved_custom_events_path(self) -> Optional[Path]:
        raw = self.config.preventive.custom_events_path
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_absolute() else (self.root_dir / p)

    def ensure_connected(self) -> bool:
        with self._mt5_lock:
            if self._connected:
                if self._gw.get_account_state() is not None:
                    return True
            self._connected = self._gw.connect()
            return self._connected

    def start_bot(self) -> dict[str, Any]:
        with self._mt5_lock:
            if self._bot_running:
                return {"started": False, "message": "Bot is already running", "running": True}
            if not self.ensure_connected():
                return {"started": False, "message": "Cannot connect to MT5 terminal", "running": False}
            self._stop_event.clear()
            self._bot_running = True
            self._worker_thread = threading.Thread(target=self._run_loop, name="cfd-bot-worker", daemon=True)
            self._worker_thread.start()
            logger.info("Operation UI started bot loop (Symbol=%s, TF=%s, DryRun=%s)", self.config.symbol, self.config.timeframe, self.config.dry_run)
            return {"started": True, "message": "Bot started", "running": True}

    def stop_bot(self) -> dict[str, Any]:
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)
        self._bot_running = False
        logger.info("Operation UI stopped bot loop")
        return {"stopped": True, "running": False}

    def evaluate_once(self) -> dict[str, Any]:
        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 terminal unavailable"}
            res = self._runner.evaluate_once()
            self._cycle_count += 1
            now_utc = datetime.now(timezone.utc)
            self._last_cycle_time = now_utc
            self._last_cycle = res
            self._log_cycle(res)
            self._record_cycle_events(res, self._cycle_count, now_utc)
            return sanitize_json({"ok": True, "cycle": res})

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._mt5_lock:
                    res = self._runner.evaluate_once()
                    self._cycle_count += 1
                    now_utc = datetime.now(timezone.utc)
                    self._last_cycle_time = now_utc
                    self._last_cycle = res
                    self._log_cycle(res)
                    self._record_cycle_events(res, self._cycle_count, now_utc)
            except Exception as exc:
                logger.error("Error in bot worker cycle: %s", exc)
            self._stop_event.wait(self.config.poll_seconds)
        self._bot_running = False

    def _log_cycle(self, res: EvaluationCycleResult) -> None:
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
            ml_txt = f"{snap.projected_margin_level_pct:.1f}%" if snap.projected_margin_level_pct is not None else "N/A"
            logger.info(
                "[%s] %s %s lot=%.2f NetRR=%.2f MarginLvl=%s | %s",
                res.status.upper(),
                res.signal.symbol if res.signal else self.config.symbol,
                res.signal.side.value if res.signal else "",
                res.lot_plan.lot,
                res.preventive.costs.net_rr,
                ml_txt,
                res.execution.comment if res.execution else "",
            )

    def get_overview(self) -> dict[str, Any]:
        with self._mt5_lock:
            connected = self.ensure_connected()
            account = self._gw.get_account_state() if connected else None
            spec = self._gw.get_symbol_spec(self.config.symbol) if connected else None
            positions = self._gw.get_open_positions() if connected else []

            spec_metrics = self._build_spec_metrics(spec, account) if (spec and account) else None
            now_utc = datetime.now(timezone.utc)
            dd_pct = self._runner._guard.daily_drawdown_pct(account.equity, now_utc) if account else 0.0
            active_offset = int(spec.server_utc_offset_seconds or 0) if spec else 0

            pos_rows = []
            for p in positions:
                raw_open_ts = getattr(p, "open_time", None)
                utc_open_ts = (raw_open_ts - active_offset) if raw_open_ts else None
                pos_rows.append(
                    {
                        **sanitize_json(p),
                        "open_time": utc_open_ts,
                        "owned_by_bot": p.magic == self.config.magic_number,
                    }
                )

            return sanitize_json(
                {
                    "mt5_connected": connected,
                    "account": account,
                    "active_spec": spec_metrics,
                    "positions": pos_rows,
                    "bot_state": {
                        "running": self._bot_running,
                        "dry_run": self.config.dry_run,
                        "symbol": spec.name if spec else self.config.symbol,
                        "timeframe": self.config.timeframe,
                        "strategy_name": self._strategy.name,
                        "strategy_display_name": self._strategy.display_name_th,
                        "lot_mode": self.config.risk.lot_mode.value,
                        "cycle_count": self._cycle_count,
                        "last_cycle_time": self._last_cycle_time,
                        "last_cycle": self._last_cycle,
                        "daily_drawdown_pct": dd_pct,
                        "max_daily_loss_pct": self.config.preventive.max_daily_loss_pct,
                    },
                }
            )

    def _get_cached_h1_atr(self, symbol: str, ttl_seconds: float = 30.0) -> Optional[float]:
        now_mono = time.monotonic()
        cached = self._atr_cache.get(symbol)
        if cached is not None and (now_mono - cached[0]) < ttl_seconds:
            return cached[1]
        val = _compute_h1_atr(self._gw, symbol)
        self._atr_cache[symbol] = (now_mono, val)
        return val

    def _build_spec_metrics(self, spec: SymbolSpec, account: Any) -> dict[str, Any]:
        margin_1lot = self._gw.calc_margin(OrderSide.BUY, spec.name, 1.0, spec.ask) if spec.ask > 0 else None
        notional_1lot = estimate_notional_per_lot(spec, spec.ask) if spec.ask > 0 else 0.0
        eff_lev = (notional_1lot / margin_1lot) if (margin_1lot and margin_1lot > 0) else float(account.leverage)
        h1_atr = self._get_cached_h1_atr(spec.name)
        return {
            "spec": sanitize_json(spec),
            "margin_per_lot": margin_1lot,
            "notional_per_lot": notional_1lot,
            "effective_symbol_leverage": eff_lev,
            "h1_atr": h1_atr,
            "spread_price": spec.spread_price,
        }

    def get_live_snapshot(self, symbols: Optional[list[str]] = None) -> dict[str, Any]:
        """Returns overview + optional watchlist ticks in one atomic snapshot for SSE realtime updates."""
        target_symbols = symbols or ["XAUUSD", "EURUSD", "US30", "NAS100"]
        with self._mt5_lock:
            overview = self.get_overview()
            watchlist = self.inspect_watchlist(target_symbols) if overview.get("mt5_connected") else {"mt5_connected": False, "items": []}
            return sanitize_json(
                {
                    "overview": overview,
                    "watchlist": watchlist.get("items", []),
                    "server_time_utc": datetime.now(timezone.utc),
                }
            )

    def inspect_watchlist(self, symbols: list[str]) -> dict[str, Any]:
        with self._mt5_lock:
            if not self.ensure_connected():
                return {"mt5_connected": False, "items": []}
            account = self._gw.get_account_state()
            if account is None:
                return {"mt5_connected": False, "items": []}
            items = []
            for sym in symbols:
                clean = sym.strip()
                if not clean:
                    continue
                spec = self._gw.get_symbol_spec(clean)
                if spec is None:
                    items.append({"requested": clean, "found": False})
                else:
                    metrics = self._build_spec_metrics(spec, account)
                    items.append({"requested": clean, "found": True, **metrics})
            return sanitize_json({"mt5_connected": True, "items": items})

    def simulate_trade(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 terminal unavailable"}
            live_account = self._gw.get_account_state()
            if live_account is None:
                return {"ok": False, "error": "Account unavailable"}

            raw_symbol = str(payload.get("symbol") or self.config.symbol)
            spec = self._gw.get_symbol_spec(raw_symbol)
            if spec is None:
                return {"ok": False, "error": f"Symbol '{raw_symbol}' not found"}

            side = OrderSide(str(payload.get("side", "BUY")).upper())
            hold_days = max(0.0, float(payload.get("hold_days", 0.0)))
            lot_mode = LotMode(str(payload.get("lot_mode", self.config.risk.lot_mode.value)))

            # Optional simulated equity override (e.g. testing $500 or $10,000 scenarios)
            custom_equity_raw = payload.get("custom_equity")
            active_account = live_account
            if custom_equity_raw is not None and str(custom_equity_raw).strip() != "":
                sim_eq = float(custom_equity_raw)
                if sim_eq <= 0:
                    return {"ok": False, "error": "ทุนจำลองต้องมากกว่า 0"}
                sim_free = max(0.0, sim_eq - live_account.margin)
                sim_ml = (sim_eq / live_account.margin * 100.0) if live_account.margin > 0 else live_account.margin_level
                active_account = replace(
                    live_account,
                    balance=sim_eq,
                    equity=sim_eq,
                    margin_free=sim_free,
                    margin_level=sim_ml,
                )

            # Support Risk % OR direct USD Risk Amount
            risk_amount_raw = payload.get("risk_amount")
            if risk_amount_raw is not None and str(risk_amount_raw).strip() != "":
                risk_amt = float(risk_amount_raw)
                if risk_amt <= 0:
                    return {"ok": False, "error": "งบยอมขาดทุน ($) ต้องมากกว่า 0"}
                eff_risk_pct = min(100.0, max(0.01, (risk_amt / active_account.equity) * 100.0))
            else:
                eff_risk_pct = min(100.0, max(0.01, float(payload.get("risk_percent", self.config.risk.risk_percent))))

            ignore_caps = bool(payload.get("ignore_leverage_caps", False))
            risk_updates: dict[str, Any] = {
                "lot_mode": lot_mode,
                "fixed_lot": max(0.001, float(payload.get("fixed_lot", self.config.risk.fixed_lot))),
                "risk_percent": eff_risk_pct,
            }
            if payload.get("equity_step_amount") is not None and float(payload["equity_step_amount"]) > 0:
                risk_updates["equity_step_amount"] = float(payload["equity_step_amount"])
            if payload.get("equity_step_lot") is not None and float(payload["equity_step_lot"]) > 0:
                risk_updates["equity_step_lot"] = float(payload["equity_step_lot"])
            if payload.get("scale_down_lot_for_leverage") is not None:
                risk_updates["scale_down_lot_for_leverage"] = bool(payload["scale_down_lot_for_leverage"])

            if ignore_caps:
                risk_updates["max_lot_cap"] = max(spec.volume_max, 1000.0)
                risk_updates["max_effective_account_leverage"] = 10000.0
                risk_updates["max_effective_symbol_leverage"] = 10000.0
                risk_updates["max_margin_usage_pct"] = 95.0
                risk_updates["min_margin_level_pct"] = 100.0
            else:
                if payload.get("max_lot_cap") is not None and float(payload["max_lot_cap"]) > 0:
                    risk_updates["max_lot_cap"] = float(payload["max_lot_cap"])
                if payload.get("max_effective_account_leverage") is not None and float(payload["max_effective_account_leverage"]) > 0:
                    risk_updates["max_effective_account_leverage"] = float(payload["max_effective_account_leverage"])
                if payload.get("max_effective_symbol_leverage") is not None and float(payload["max_effective_symbol_leverage"]) > 0:
                    risk_updates["max_effective_symbol_leverage"] = float(payload["max_effective_symbol_leverage"])
                if payload.get("max_margin_usage_pct") is not None and float(payload["max_margin_usage_pct"]) > 0:
                    risk_updates["max_margin_usage_pct"] = min(95.0, float(payload["max_margin_usage_pct"]))

            risk_cfg = self.config.risk.model_copy(update=risk_updates)

            # Entry price: custom chart price or live Ask/Bid
            entry_raw = payload.get("entry_price")
            if entry_raw is not None and str(entry_raw).strip() != "" and float(entry_raw) > 0:
                entry = float(entry_raw)
            else:
                entry = spec.ask if side == OrderSide.BUY else spec.bid

            h1_atr = self._get_cached_h1_atr(spec.name)
            sl_price_raw = payload.get("sl_price")
            tp_price_raw = payload.get("tp_price")
            sl_pts = payload.get("sl_points")
            tp_pts = payload.get("tp_points")
            rr_raw = payload.get("rr_ratio")
            rr_ratio = float(rr_raw) if (rr_raw is not None and str(rr_raw).strip() != "" and float(rr_raw) > 0) else 2.0

            if sl_price_raw is not None and str(sl_price_raw).strip() != "" and float(sl_price_raw) > 0:
                sl_price = round(float(sl_price_raw), spec.digits)
                sl_dist = (entry - sl_price) if side == OrderSide.BUY else (sl_price - entry)
                if sl_dist <= 0:
                    hint = "ต้องต่ำกว่าราคาเข้า" if side == OrderSide.BUY else "ต้องสูงกว่าราคาเข้า"
                    return {"ok": False, "error": f"ราคา SL สำหรับ {side.value} {hint} (Entry={entry}, SL={sl_price})"}
            else:
                if sl_pts is not None and str(sl_pts).strip() != "" and float(sl_pts) > 0:
                    sl_dist = float(sl_pts) * spec.point
                elif h1_atr:
                    sl_dist = 1.5 * h1_atr
                else:
                    sl_dist = entry * 0.003
                sl_price = round(entry - sl_dist, spec.digits) if side == OrderSide.BUY else round(entry + sl_dist, spec.digits)

            if tp_price_raw is not None and str(tp_price_raw).strip() != "" and float(tp_price_raw) > 0:
                tp_price = round(float(tp_price_raw), spec.digits)
                tp_dist = (tp_price - entry) if side == OrderSide.BUY else (entry - tp_price)
                if tp_dist <= 0:
                    hint = "ต้องสูงกว่าราคาเข้า" if side == OrderSide.BUY else "ต้องต่ำกว่าราคาเข้า"
                    return {"ok": False, "error": f"ราคา TP สำหรับ {side.value} {hint} (Entry={entry}, TP={tp_price})"}
            else:
                if tp_pts is not None and str(tp_pts).strip() != "" and float(tp_pts) > 0:
                    tp_dist = float(tp_pts) * spec.point
                else:
                    tp_dist = sl_dist * rr_ratio
                tp_price = round(entry + tp_dist, spec.digits) if side == OrderSide.BUY else round(entry - tp_dist, spec.digits)

            signal = TradeSignal(
                symbol=spec.name,
                side=side,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=hold_days,
                atr=h1_atr,
                reason="Interactive Simulator",
            )
            positions = self._gw.get_open_positions()
            guard = PreventiveGuard(self.config.preventive, calendar_service=self._calendar)
            decision = guard.evaluate(
                account=active_account,
                spec=spec,
                signal=signal,
                open_positions=positions,
                now=datetime.now(timezone.utc),
            )
            engine = RiskEngine(risk_cfg)
            lot_plan = engine.compute_lot_plan(
                account=active_account,
                spec=spec,
                signal=signal,
                costs=decision.costs,
                open_positions=positions,
                gateway=self._gw,
                lot_mode_override=lot_mode,
            )

            # Build quick comparison table across risk presets
            scenario_pcts = [0.5, 1.0, 2.0, 3.0]
            if round(eff_risk_pct, 2) not in scenario_pcts:
                scenario_pcts = sorted([*scenario_pcts, round(eff_risk_pct, 2)])
            scenarios: list[dict[str, Any]] = []
            for pct in scenario_pcts:
                sc_cfg = risk_cfg.model_copy(update={"lot_mode": LotMode.DYNAMIC_RISK, "risk_percent": pct})
                sc_plan = RiskEngine(sc_cfg).compute_lot_plan(
                    account=active_account,
                    spec=spec,
                    signal=signal,
                    costs=decision.costs,
                    open_positions=positions,
                    gateway=self._gw,
                    lot_mode_override=LotMode.DYNAMIC_RISK,
                )
                scenarios.append(
                    {
                        "risk_percent": pct,
                        "risk_budget_money": sc_plan.risk_budget_money,
                        "lot": sc_plan.lot,
                        "raw_lot": sc_plan.raw_lot,
                        "approved": sc_plan.approved,
                        "loss_at_sl": sc_plan.estimated_loss_at_sl,
                        "profit_at_tp": sc_plan.estimated_profit_at_tp,
                        "margin_used": sc_plan.leverage_snapshot.projected_margin_used,
                        "margin_level_pct": sc_plan.leverage_snapshot.projected_margin_level_pct,
                    }
                )

            return sanitize_json(
                {
                    "ok": True,
                    "symbol": spec.name,
                    "equity_used": active_account.equity,
                    "live_equity": live_account.equity,
                    "sl_points_used": round(sl_dist / spec.point, 1) if spec.point > 0 else 0.0,
                    "tp_points_used": round(tp_dist / spec.point, 1) if spec.point > 0 else 0.0,
                    "signal": signal,
                    "preventive": decision,
                    "lot_plan": lot_plan,
                    "h1_atr": h1_atr,
                    "scenarios": scenarios,
                }
            )

    def get_calendar(self, symbol: Optional[str] = None) -> dict[str, Any]:
        with self._mt5_lock:
            target = symbol or self.config.symbol
            spec = self._gw.get_symbol_spec(target) if self.ensure_connected() else None
            if spec is None:
                spec = SymbolSpec(
                    name=target,
                    digits=5,
                    point=0.00001,
                    ask=1.0,
                    bid=1.0,
                    spread_points=0,
                    volume_min=0.01,
                    volume_max=100.0,
                    volume_step=0.01,
                    trade_contract_size=100000.0,
                    trade_tick_size=0.00001,
                    trade_tick_value=1.0,
                    trade_tick_value_profit=1.0,
                    trade_tick_value_loss=1.0,
                )
            res = self._calendar.evaluate(spec=spec, now=datetime.now(timezone.utc), expected_hold_days=1.0)
            custom_items = self._read_custom_events_raw()
        return sanitize_json(
            {
                "symbol": spec.name,
                "check": res,
                "custom_events": custom_items,
            }
        )

    @staticmethod
    def _serialize_rates_to_candles(rates: pd.DataFrame, offset_sec: int) -> list[dict[str, Any]]:
        """Converts MT5 OHLCV DataFrame rows into deduplicated UTC-epoch candle dicts."""
        candles: list[dict[str, Any]] = []
        seen_times: set[int] = set()
        if rates is None or rates.empty:
            return candles
        ts_series = pd.to_datetime(rates["time"], utc=True)
        epoch_arr = ts_series.dt.tz_convert(None).to_numpy("datetime64[s]").astype("int64") - int(offset_sec)
        opens = rates["open"].to_numpy(dtype=float)
        highs = rates["high"].to_numpy(dtype=float)
        lows = rates["low"].to_numpy(dtype=float)
        closes = rates["close"].to_numpy(dtype=float)
        if "tick_volume" in rates.columns:
            vols = rates["tick_volume"].to_numpy(dtype=int)
        elif "real_volume" in rates.columns:
            vols = rates["real_volume"].to_numpy(dtype=int)
        else:
            vols = [0] * len(rates)

        for utc_ts, op, hi, lo, cl, vol in zip(epoch_arr, opens, highs, lows, closes, vols):
            ts_int = int(utc_ts)
            if ts_int in seen_times:
                continue
            seen_times.add(ts_int)
            candles.append(
                {
                    "time": ts_int,
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                    "volume": int(vol),
                }
            )
        candles.sort(key=lambda c: c["time"])
        return candles

    @staticmethod
    def _warmup_days_for_timeframe(timeframe: str) -> int:
        tf = timeframe.upper()
        mapping = {
            "M1": 3,
            "M3": 3,
            "M5": 5,
            "M15": 10,
            "M30": 16,
            "H1": 30,
            "H4": 95,
            "D1": 450,
        }
        return mapping.get(tf, 10)

    @staticmethod
    def _format_user_dt(utc_ts: int, tz_offset_minutes: int) -> str:
        dt = datetime.fromtimestamp(int(utc_ts), tz=timezone.utc) + timedelta(minutes=int(tz_offset_minutes))
        return dt.strftime("%d/%m/%Y %H:%M")

    @staticmethod
    def _parse_calendar_bounds(
        start_date: Optional[str],
        end_date: Optional[str],
        tz_offset_minutes: int = 0,
    ) -> tuple[Optional[datetime], Optional[datetime], Optional[str]]:
        offset_delta = timedelta(minutes=int(tz_offset_minutes))
        now_local = datetime.now(timezone.utc) + offset_delta

        def _parse_one(raw: str, is_end: bool) -> datetime:
            text = raw.strip()
            if len(text) == 10:
                parsed = datetime.strptime(text, "%Y-%m-%d")
                if is_end:
                    parsed = parsed.replace(hour=23, minute=59, second=59)
                else:
                    parsed = parsed.replace(hour=0, minute=0, second=0)
                return parsed.replace(tzinfo=timezone.utc) - offset_delta
            iso_val = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if iso_val.tzinfo is None:
                return iso_val.replace(tzinfo=timezone.utc) - offset_delta
            return iso_val.astimezone(timezone.utc)

        try:
            if end_date and str(end_date).strip():
                end_utc = _parse_one(str(end_date), is_end=True)
            else:
                end_utc = now_local.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc) - offset_delta

            if start_date and str(start_date).strip():
                start_utc = _parse_one(str(start_date), is_end=False)
            else:
                local_start = (end_utc + offset_delta) - timedelta(days=30)
                start_utc = local_start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc) - offset_delta
        except ValueError:
            return None, None, "รูปแบบวันที่ไม่ถูกต้อง (กรุณาใช้ YYYY-MM-DD)"

        if start_utc > end_utc:
            return None, None, "วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด"
        return start_utc, end_utc, None

    def _fetch_calendar_window(
        self,
        spec: SymbolSpec,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        tz_offset_minutes: int,
        include_warmup: bool,
        max_calendar_bars: int = 120_000,
    ) -> dict[str, Any]:
        offset_sec = int(spec.server_utc_offset_seconds or 0)
        warmup_days = self._warmup_days_for_timeframe(timeframe) if include_warmup else 0
        req_from_utc = start_utc - timedelta(days=warmup_days)

        # Convert true UTC window back into broker server pseudo-UTC timestamps for MT5 lookup
        raw_from = req_from_utc + timedelta(seconds=offset_sec) - timedelta(hours=2)
        raw_to = end_utc + timedelta(seconds=offset_sec) + timedelta(hours=2)

        if hasattr(self._gw, "get_rates_range"):
            raw_df = self._gw.get_rates_range(spec.name, timeframe, raw_from, raw_to)
        else:
            raw_df = self._gw.get_rates(spec.name, timeframe, 50_000)

        if raw_df is None or raw_df.empty:
            return {
                "ok": False,
                "error": f"ไม่พบแท่งเทียน {spec.name} ({timeframe}) ในช่วงวันที่ที่เลือก",
            }

        df = raw_df.copy()
        ts_series = pd.to_datetime(df["time"], utc=True)
        utc_epochs = ts_series.dt.tz_convert(None).to_numpy("datetime64[s]").astype("int64") - offset_sec
        df["_utc_ts"] = utc_epochs
        df = df.drop_duplicates(subset=["_utc_ts"]).sort_values("_utc_ts").reset_index(drop=True)
        utc_arr = df["_utc_ts"].to_numpy(dtype="int64")

        start_ts = int(start_utc.timestamp())
        end_ts = int(end_utc.timestamp())
        in_window = (utc_arr >= start_ts) & (utc_arr <= end_ts)
        eval_df = df.loc[in_window].reset_index(drop=True)

        if eval_df.empty:
            earliest_str = self._format_user_dt(int(utc_arr[0]), tz_offset_minutes)
            latest_str = self._format_user_dt(int(utc_arr[-1]), tz_offset_minutes)
            return {
                "ok": False,
                "error": (
                    f"ไม่พบแท่งเทียน {spec.name} ({timeframe}) ในช่วงที่เลือก "
                    f"(ตลาดอาจปิดช่วงวันหยุด หรือโบรคเกอร์เก็บประวัติช่วง {earliest_str} ถึง {latest_str})"
                ),
            }

        range_notice_th: Optional[str] = None
        truncated = False
        if len(eval_df) > max_calendar_bars:
            eval_df = eval_df.iloc[-max_calendar_bars:].reset_index(drop=True)
            truncated = True
            range_notice_th = (
                f"ช่วงวันที่ที่เลือกมีข้อมูลเกิน {max_calendar_bars:,} แท่ง "
                f"— ระบบดึงเฉพาะ {max_calendar_bars:,} แท่งล่าสุดในช่วงนั้นมาแสดง"
            )

        effective_start_ts = int(eval_df["_utc_ts"].iloc[0])
        actual_end_ts = int(eval_df["_utc_ts"].iloc[-1])

        if (not truncated) and (effective_start_ts - start_ts > 4 * 86400):
            range_notice_th = (
                f"โบรคเกอร์มีข้อมูล {spec.name} ({timeframe}) ย้อนหลังเก่าสุดถึง "
                f"{self._format_user_dt(effective_start_ts, tz_offset_minutes)} "
                f"— แสดงข้อมูลตั้งแต่จุดเริ่มต้นที่มีจริง ({len(eval_df):,} แท่ง)"
            )

        if include_warmup:
            pre_df = df.loc[df["_utc_ts"] < effective_start_ts].tail(350)
            model_df = pd.concat([pre_df, eval_df], ignore_index=True)
        else:
            model_df = eval_df

        return {
            "ok": True,
            "model_rates": model_df.drop(columns=["_utc_ts"]),
            "display_rates": eval_df.drop(columns=["_utc_ts"]),
            "eval_start_ts": effective_start_ts,
            "actual_start_time": effective_start_ts,
            "actual_end_time": actual_end_ts,
            "range_notice_th": range_notice_th,
        }

    def get_candles(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        count: int = 500,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tz_offset_minutes: int = 0,
    ) -> dict[str, Any]:
        """Returns OHLCV candles in true UTC epoch seconds plus position/signal/news overlays for TradingView charting."""
        supported_tfs = {"M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"}
        raw_tf = (timeframe or self.config.timeframe or "M15").strip().upper()
        tf = raw_tf if raw_tf in supported_tfs else "M15"
        bar_count = max(30, min(10000, int(count)))
        use_calendar = bool((start_date and str(start_date).strip()) or (end_date and str(end_date).strip()))

        start_utc: Optional[datetime] = None
        end_utc: Optional[datetime] = None
        if use_calendar:
            start_utc, end_utc, err_msg = self._parse_calendar_bounds(start_date, end_date, tz_offset_minutes)
            if err_msg or start_utc is None or end_utc is None:
                return {"ok": False, "error": err_msg or "ช่วงวันที่ไม่ถูกต้อง"}

        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 terminal unavailable"}

            target = (symbol or self.config.symbol).strip() or self.config.symbol
            spec = self._gw.get_symbol_spec(target)
            if spec is None:
                return {"ok": False, "error": f"Symbol '{target}' not found"}

            offset_sec = int(spec.server_utc_offset_seconds or 0)
            range_notice_th: Optional[str] = None
            if use_calendar and start_utc is not None and end_utc is not None:
                win = self._fetch_calendar_window(
                    spec=spec,
                    timeframe=tf,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    tz_offset_minutes=tz_offset_minutes,
                    include_warmup=False,
                )
                if not win.get("ok"):
                    return {"ok": False, "error": win.get("error")}
                rates = win["display_rates"]
                range_notice_th = win.get("range_notice_th")
                actual_start_time = win.get("actual_start_time")
                actual_end_time = win.get("actual_end_time")
            else:
                rates = self._gw.get_rates(spec.name, tf, bar_count)
                actual_start_time = None
                actual_end_time = None

            candles = self._serialize_rates_to_candles(rates, offset_sec)
            if candles:
                actual_start_time = actual_start_time or candles[0]["time"]
                actual_end_time = actual_end_time or candles[-1]["time"]

            positions = [
                replace(p, open_time=(p.open_time - offset_sec) if p.open_time else None)
                for p in self._gw.get_open_positions()
                if p.symbol == spec.name
            ]

            last_signal = None
            if self._last_cycle is not None and self._last_cycle.signal is not None:
                if self._last_cycle.signal.symbol == spec.name:
                    last_signal = self._last_cycle.signal

            news_markers: list[dict[str, Any]] = []
            if candles and hasattr(self._calendar, "get_events"):
                min_ts = candles[0]["time"] - 3600
                max_ts = candles[-1]["time"] + 86400
                currencies = extract_symbol_currencies(spec)
                events, _ = self._calendar.get_events(currencies=currencies, now=datetime.now(timezone.utc))
                for ev in events:
                    if ev.impact not in (NewsImpact.HIGH, NewsImpact.MEDIUM):
                        continue
                    if ev.currency not in currencies and ev.currency != "ALL":
                        continue
                    ev_ts = int(ev.event_time.timestamp())
                    if min_ts <= ev_ts <= max_ts:
                        news_markers.append(
                            {
                                "title": ev.title,
                                "currency": ev.currency,
                                "impact": ev.impact.value,
                                "event_time": ev.event_time,
                                "event_ts": ev_ts,
                            }
                        )

            return sanitize_json(
                {
                    "ok": True,
                    "range_mode": "calendar" if use_calendar else "bars",
                    "symbol": spec.name,
                    "timeframe": tf,
                    "actual_start_time": actual_start_time,
                    "actual_end_time": actual_end_time,
                    "range_notice_th": range_notice_th,
                    "server_utc_offset_seconds": offset_sec,
                    "spec": spec,
                    "candles": candles,
                    "positions": positions,
                    "last_signal": last_signal,
                    "news_events": news_markers[:25],
                }
            )

    def _fallback_spec(self, symbol: str) -> SymbolSpec:
        return SymbolSpec(
            name=symbol,
            digits=5,
            point=0.00001,
            ask=1.0,
            bid=1.0,
            spread_points=0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100000.0,
            trade_tick_size=0.00001,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
        )

    def get_news_intelligence(self, symbol: Optional[str] = None) -> dict[str, Any]:
        with self._mt5_lock:
            now_utc = datetime.now(timezone.utc)
            target = symbol or self.config.symbol
            connected = self.ensure_connected()
            spec = self._gw.get_symbol_spec(target) if connected else None
            if spec is None:
                spec = self._fallback_spec(target)

            currencies = extract_symbol_currencies(spec)
            if hasattr(self._calendar, "get_events"):
                all_events, cal_warnings = self._calendar.get_events(currencies=currencies | {"USD", "EUR", "GBP", "JPY"}, now=now_utc)
            else:
                all_events, cal_warnings = [], []

            bridge_status = (
                self._calendar.get_bridge_status()
                if hasattr(self._calendar, "get_bridge_status")
                else {"detected": False, "path": "", "event_count": 0, "updated_utc": None}
            )

            analyzer = PostNewsAnalyzer()
            win_start = now_utc - timedelta(hours=72)
            win_end = now_utc + timedelta(hours=48)

            relevant_events = [
                ev
                for ev in all_events
                if ev.impact != NewsImpact.HOLIDAY
                and (ev.currency in currencies or ev.currency == "ALL")
                and win_start <= ev.event_time <= win_end
                and (ev.impact in (NewsImpact.HIGH, NewsImpact.MEDIUM) or ev.actual is not None)
            ]
            relevant_events.sort(key=lambda e: e.event_time, reverse=True)

            event_analyses = [analyzer.score_event_for_symbol(ev, spec) for ev in relevant_events]

            # Aggregate fundamental bias across released events in last 24h
            recent_scored = [
                a
                for a in event_analyses
                if a.actual_num is not None and (now_utc - timedelta(hours=24)) <= a.event_time <= now_utc
            ]
            if not recent_scored:
                agg_score = 0.0
                agg_bias = NewsSentiment.UNKNOWN
            else:
                agg_score = round(
                    max(-100.0, min(100.0, sum(a.symbol_score for a in recent_scored))),
                    1,
                )
                has_pos = any(a.symbol_score >= 15.0 for a in recent_scored)
                has_neg = any(a.symbol_score <= -15.0 for a in recent_scored)
                if has_pos and has_neg and abs(agg_score) < 20.0:
                    agg_bias = NewsSentiment.CONFLICTING
                elif agg_score >= 15.0:
                    agg_bias = NewsSentiment.BULLISH
                elif agg_score <= -15.0:
                    agg_bias = NewsSentiment.BEARISH
                else:
                    agg_bias = NewsSentiment.NEUTRAL

            # Post-news M5/M15 chart reaction for most recent released HIGH/MEDIUM event (prefer one with Actual, else latest past event)
            past_analyses = [a for a in event_analyses if a.event_time <= now_utc]
            target_event = next((a for a in past_analyses if a.actual_num is not None), None)
            if target_event is None and past_analyses:
                target_event = past_analyses[0]

            post_news_reaction = None
            if target_event is not None and connected:
                m5_rates = self._gw.get_rates(spec.name, "M5", 200)
                m15_rates = self._gw.get_rates(spec.name, "M15", 100)
                post_news_reaction = analyzer.analyze_post_news_reaction(
                    event_analysis=target_event,
                    spec=spec,
                    m5_rates=m5_rates,
                    m15_rates=m15_rates,
                    now=now_utc,
                )

            # Multi-CFD Matrix across key CFDs (EURUSD, XAUUSD, US30, NAS100)
            matrix_symbols = ["EURUSD", "XAUUSD", "US30", "NAS100"]
            cfd_matrix = []
            released_events = [
                ev for ev in all_events if ev.impact != NewsImpact.HOLIDAY and ev.actual is not None and (now_utc - timedelta(hours=36)) <= ev.event_time <= now_utc
            ]
            for m_sym in matrix_symbols:
                m_spec = (self._gw.get_symbol_spec(m_sym) if connected else None) or self._fallback_spec(m_sym)
                m_scores = [analyzer.score_event_for_symbol(ev, m_spec) for ev in released_events]
                m_total = round(max(-100.0, min(100.0, sum(s.symbol_score for s in m_scores))), 1) if m_scores else 0.0
                if not m_scores:
                    m_bias = NewsSentiment.UNKNOWN
                elif any(s.symbol_score >= 15 for s in m_scores) and any(s.symbol_score <= -15 for s in m_scores) and abs(m_total) < 20:
                    m_bias = NewsSentiment.CONFLICTING
                elif m_total >= 15:
                    m_bias = NewsSentiment.BULLISH
                elif m_total <= -15:
                    m_bias = NewsSentiment.BEARISH
                else:
                    m_bias = NewsSentiment.NEUTRAL
                cfd_matrix.append(
                    {
                        "symbol": m_spec.name,
                        "score": m_total,
                        "bias": m_bias.value,
                        "events_counted": len(m_scores),
                    }
                )

            return sanitize_json(
                {
                    "symbol": spec.name,
                    "mt5_connected": connected,
                    "bridge_status": bridge_status,
                    "aggregate_score": agg_score,
                    "aggregate_bias": agg_bias.value,
                    "cfd_matrix": cfd_matrix,
                    "post_news_reaction": post_news_reaction,
                    "events": event_analyses[:25],
                    "warnings": cal_warnings,
                }
            )

    def save_news_actual(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        currency = str(payload.get("currency", "USD")).strip().upper()
        event_time = str(payload.get("event_time", "")).strip()
        actual = str(payload.get("actual", "")).strip()
        forecast = str(payload["forecast"]).strip() if payload.get("forecast") not in (None, "") else None
        previous = str(payload["previous"]).strip() if payload.get("previous") not in (None, "") else None
        impact = str(payload.get("impact", "HIGH")).strip().upper()

        if not title or not event_time or not actual:
            return {"ok": False, "error": "title, event_time, and actual are required"}
        if not hasattr(self._calendar, "save_manual_actual"):
            return {"ok": False, "error": "Calendar service does not support manual actuals"}

        self._calendar.save_manual_actual(
            title=title,
            currency=currency,
            event_time_iso=event_time,
            actual=actual,
            forecast=forecast,
            previous=previous,
            impact=impact,
        )
        logger.info("Saved manual Actual for %s (%s): Actual=%s Forecast=%s", title, currency, actual, forecast)
        return {"ok": True}

    def _read_custom_events_raw(self) -> list[dict[str, Any]]:
        path = self._resolved_custom_events_path() or self.custom_events_path
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def add_custom_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolved_custom_events_path() or self.custom_events_path
        path.parent.mkdir(parents=True, exist_ok=True)
        items = self._read_custom_events_raw()
        new_item = {
            "title": str(payload.get("title", "")).strip(),
            "currency": str(payload.get("currency", "ALL")).strip().upper(),
            "impact": str(payload.get("impact", "HIGH")).strip().upper(),
            "date": str(payload.get("date", "")).strip(),
            "major": bool(payload.get("major", True)),
        }
        if not new_item["title"] or not new_item["date"]:
            return {"ok": False, "error": "Title and date are required"}
        items.append(new_item)
        path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        self._calendar._memory_events = []
        self._calendar._last_refresh_ts = 0.0
        logger.info("Added custom calendar event: %s (%s %s)", new_item["title"], new_item["currency"], new_item["date"])
        return {"ok": True, "custom_events": items}

    def delete_custom_event(self, index: int) -> dict[str, Any]:
        path = self._resolved_custom_events_path() or self.custom_events_path
        items = self._read_custom_events_raw()
        if index < 0 or index >= len(items):
            return {"ok": False, "error": "Index out of range"}
        removed = items.pop(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        self._calendar._memory_events = []
        self._calendar._last_refresh_ts = 0.0
        logger.info("Removed custom calendar event: %s", removed.get("title"))
        return {"ok": True, "custom_events": items}

    def get_public_config(self) -> dict[str, Any]:
        dumped = self.config.model_dump()
        if dumped.get("mt5", {}).get("password"):
            dumped["mt5"]["password"] = "***"
        return sanitize_json(dumped)

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Preserve existing secret password if masked
        current = self.config.model_dump()
        merged = self._deep_merge(current, payload)
        if merged.get("mt5", {}).get("password") == "***":
            merged["mt5"]["password"] = current["mt5"]["password"]

        new_cfg = BotConfig.model_validate(merged)
        try:
            rebuilt_strategy = build_strategy(new_cfg.strategy.active_strategy, new_cfg.strategy.params)
        except ValueError:
            rebuilt_strategy = build_strategy("M5KernelPullback")
        new_cfg.strategy.active_strategy = rebuilt_strategy.name
        new_cfg.strategy.params = rebuilt_strategy.get_params()
        self.config = new_cfg
        self._persist_env(new_cfg)

        # Reload runner, strategy & calendar with updated settings
        with self._mt5_lock:
            self._strategy = rebuilt_strategy
            if self._calendar_factory is not None:
                self._calendar = self._calendar_factory(
                    new_cfg.preventive,
                    self.root_dir / "data" / "cache",
                    self._resolved_custom_events_path(),
                )
            self._runner = BotRunner(
                config=new_cfg,
                gateway=self._gw,
                strategy=self._strategy,
                executor=self._build_executor(new_cfg),
                calendar_service=self._calendar,
            )

        logger.info(
            "Updated config via Operation UI: Symbol=%s TF=%s LotMode=%s DryRun=%s",
            new_cfg.symbol,
            new_cfg.timeframe,
            new_cfg.risk.lot_mode.value,
            new_cfg.dry_run,
        )
        return {"ok": True, "config": self.get_public_config()}

    def audit_defensive_conflicts(
        self,
        strategy: Optional[BaseStrategy] = None,
        params_override: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Detects overlaps and potential blocks between M5 Offensive strategy and Defensive BotConfig."""
        target_strat = strategy or self._strategy
        params = dict(target_strat.get_params())
        if params_override:
            params.update(params_override)

        conflicts: list[dict[str, Any]] = []
        tp_rr = float(params.get("tp_rr_multiplier", 1.25))

        # 1. Timeframe mismatch between Defensive BotConfig and Strategy recommended timeframe
        rec_tf = str(getattr(target_strat, "recommended_timeframe", "M5")).upper()
        if str(self.config.timeframe).upper() != rec_tf:
            conflicts.append(
                {
                    "code": "timeframe_mismatch",
                    "severity": "high",
                    "field_label": "กรอบเวลาของบอทจริง (Timeframe)",
                    "current_value": str(self.config.timeframe),
                    "recommended_value": rec_tf,
                    "detail_th": (
                        f"ตั้งค่าบอทยังรันที่ {self.config.timeframe} อยู่ ขณะที่กลยุทธ์นี้แนะนำ {rec_tf} หากเปิดบอทจริง ระบบจะอ่านแท่ง {self.config.timeframe} แทน {rec_tf}"
                    ),
                }
            )

        # 1b. Polling interval check for high-frequency scalping strategies (M1 / M3)
        rec_poll = 1.0 if rec_tf == "M1" else (2.0 if rec_tf == "M3" else None)
        if rec_poll is not None and float(self.config.poll_seconds) > rec_poll + 0.5:
            conflicts.append(
                {
                    "code": "poll_too_slow_for_scalping",
                    "severity": "medium",
                    "field_label": "ความถี่เช็กกราฟของบอทจริง (Poll Seconds)",
                    "current_value": f"{self.config.poll_seconds:.1f}s",
                    "recommended_value": f"{rec_poll:.1f}s",
                    "detail_th": (
                        f"กลยุทธ์ {rec_tf} ความถี่สูงควรเช็กแท่งเทียนปิดทุก {rec_poll:.1f} วินาที "
                        f"(ปัจจุบันตั้งไว้ {self.config.poll_seconds:.1f}s อาจเปิดออเดอร์ช้ากว่าต้นแท่งใหม่)"
                    ),
                }
            )

        # 2. MIN_NET_RR >= gross TP R:R minus transaction costs
        min_net_rr = self.config.preventive.min_net_rr
        safe_net_rr = round(max(0.85, tp_rr - 0.25), 2)
        if min_net_rr is not None and min_net_rr > 0 and min_net_rr > max(0.0, tp_rr - 0.12):
            conflicts.append(
                {
                    "code": "min_net_rr_clash",
                    "severity": "high",
                    "field_label": "MIN_NET_RR (สัดส่วน R:R สุทธิหลังหักค่าใช้จ่าย)",
                    "current_value": f"{min_net_rr:.2f}",
                    "recommended_value": f"{safe_net_rr:.2f}",
                    "detail_th": (
                        f"กลยุทธ์ตั้งเป้า R:R = {tp_rr:.2f} พอหัก Spread + Commission + Slippage บน {rec_tf} Net R:R จะเหลือราว "
                        f"{max(0.1, tp_rr - 0.15):.2f} ซึ่งจะโดนเพดาน MIN_NET_RR={min_net_rr:.2f} ในหน้าตั้งค่าบอทบล็อกไม่ให้ส่งออเดอร์"
                    ),
                }
            )

        # 3. Spread-to-TP ratio threshold on tight M1/M5 targets
        spread_tp = self.config.preventive.max_spread_to_tp_ratio
        if spread_tp is not None and spread_tp > 0 and spread_tp < 0.22:
            conflicts.append(
                {
                    "code": "spread_to_tp_tight",
                    "severity": "medium",
                    "field_label": "MAX_SPREAD_TO_TP_RATIO (เพดานสเปรดเทียบระยะ TP)",
                    "current_value": f"{spread_tp:.2f}",
                    "recommended_value": "0.25",
                    "detail_th": (
                        f"ระยะ TP ของกรอบ {rec_tf} แคบกว่ากราฟใหญ่ การตั้งเพดานสเปรดเทียบ TP ต่ำกว่า 0.22 อาจทำให้ถูกบล็อกช่วงทองคำสเปรดขยับ"
                    ),
                }
            )

        # 4. Auto-Breakeven trigger hugging short M5 TP
        tm = self.config.trade_management
        rec_be_r = round(max(1.10, tp_rr - 0.10), 2)
        if tm.enable_auto_breakeven and tp_rr <= 1.40 and tm.breakeven_trigger_r < rec_be_r - 0.01:
            conflicts.append(
                {
                    "code": "breakeven_early_trigger",
                    "severity": "medium",
                    "field_label": "BREAKEVEN_TRIGGER_R (จุดเลื่อนกันหน้าทุน Auto-BE)",
                    "current_value": f"{tm.breakeven_trigger_r:.2f}R",
                    "recommended_value": f"{rec_be_r:.2f}R",
                    "detail_th": (
                        f"ตั้ง Auto-BE ที่ {tm.breakeven_trigger_r:.2f}R ขณะที่ TP ของ {rec_tf} อยู่ที่ {tp_rr:.2f}R ทำให้ราคาที่พักตัวก่อนถึงเป้าอาจโดนปิดเสมอทุนต่างจาก Backtest"
                    ),
                }
            )

        return conflicts

    def get_strategies_catalog(self) -> dict[str, Any]:
        """Returns pluggable offensive strategy catalog, active selection, parameters, and defensive conflict audit."""
        live_equity: Optional[float] = None
        live_currency = "USD"
        try:
            with self._mt5_lock:
                if self.ensure_connected():
                    acc = self._gw.get_account_state()
                    if acc is not None:
                        live_equity = round(float(acc.equity if acc.equity > 0 else acc.balance), 2)
                        live_currency = acc.currency or "USD"
        except Exception:
            pass

        return sanitize_json(
            {
                "ok": True,
                "active_strategy": self._strategy.name,
                "active_display_name_th": self._strategy.display_name_th,
                "active_params": self._strategy.get_params(),
                "strategies": list_strategy_descriptors(),
                "defensive_conflicts": self.audit_defensive_conflicts(),
                "bot_timeframe": self.config.timeframe,
                "live_account_equity": live_equity,
                "live_account_currency": live_currency,
            }
        )

    def sync_defensive_with_strategy(self) -> dict[str, Any]:
        """Resolves M5/Offensive vs Defensive configuration overlaps in BotConfig and persists to .env."""
        tp_rr = float(self._strategy.get_params().get("tp_rr_multiplier", 1.25))
        safe_net_rr = round(max(0.85, tp_rr - 0.25), 2)
        rec_be_r = round(max(1.10, tp_rr - 0.10), 2)

        with self._mt5_lock:
            prev_update = self.config.preventive.model_copy()
            if prev_update.min_net_rr is not None and prev_update.min_net_rr > max(0.0, tp_rr - 0.12):
                prev_update = prev_update.model_copy(update={"min_net_rr": safe_net_rr})
            if prev_update.max_spread_to_tp_ratio is not None and prev_update.max_spread_to_tp_ratio < 0.22:
                prev_update = prev_update.model_copy(update={"max_spread_to_tp_ratio": 0.25})

            tm_update = self.config.trade_management.model_copy()
            if tm_update.enable_auto_breakeven and tp_rr <= 1.40 and tm_update.breakeven_trigger_r < rec_be_r - 0.01:
                tm_update = tm_update.model_copy(update={"breakeven_trigger_r": rec_be_r})

            next_tf = str(getattr(self._strategy, "recommended_timeframe", self.config.timeframe)).upper()
            rec_poll = 1.0 if next_tf == "M1" else (2.0 if next_tf == "M3" else None)
            next_poll = (
                rec_poll
                if (rec_poll is not None and float(self.config.poll_seconds) > rec_poll + 0.5)
                else float(self.config.poll_seconds)
            )
            new_cfg = self.config.model_copy(
                update={
                    "timeframe": next_tf,
                    "poll_seconds": next_poll,
                    "preventive": prev_update,
                    "trade_management": tm_update,
                }
            )
            self.config = new_cfg
            self._persist_env(new_cfg)
            self._runner = BotRunner(
                config=new_cfg,
                gateway=self._gw,
                strategy=self._strategy,
                executor=self._build_executor(new_cfg),
                calendar_service=self._calendar,
            )

        logger.info(
            "Synchronized defensive config with %s (TF=%s min_net_rr=%s be_trigger=%.2f)",
            self._strategy.name,
            next_tf,
            prev_update.min_net_rr,
            tm_update.breakeven_trigger_r,
        )
        return self.get_strategies_catalog()

    def update_active_strategy(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Hot-swaps the active offensive strategy and/or its parameters without altering defensive settings."""
        raw_name = str(payload.get("strategy_name") or payload.get("active_strategy") or self._strategy.name).strip()
        raw_params = payload.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}

        try:
            new_strategy = build_strategy(raw_name, params)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        normalized_params = new_strategy.get_params()
        with self._mt5_lock:
            new_cfg = self.config.model_copy(
                update={
                    "strategy": StrategyConfig(
                        active_strategy=new_strategy.name,
                        params=normalized_params,
                    )
                }
            )
            self.config = new_cfg
            self._strategy = new_strategy
            self._persist_env(new_cfg)
            self._runner = BotRunner(
                config=new_cfg,
                gateway=self._gw,
                strategy=self._strategy,
                executor=self._build_executor(new_cfg),
                calendar_service=self._calendar,
            )

        logger.info(
            "Switched active offensive strategy: %s params=%s",
            new_strategy.name,
            json.dumps(normalized_params, ensure_ascii=False, separators=(",", ":")),
        )
        return self.get_strategies_catalog()

    def run_strategy_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Runs Pure Offensive backtest OR Defensive-Aware replay when include_defensive=True."""
        raw_name = str(payload.get("strategy_name") or self._strategy.name).strip()
        raw_params = payload.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        include_defensive = bool(payload.get("include_defensive", False))

        try:
            strategy = build_strategy(raw_name, params)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        supported_tfs = {"M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"}
        raw_tf = str(payload.get("timeframe") or self.config.timeframe or "M5").strip().upper()
        tf = raw_tf if raw_tf in supported_tfs else "M5"
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        raw_mode = str(payload.get("range_mode") or "").strip().lower()
        use_calendar = bool(
            raw_mode == "calendar"
            or (start_date and str(start_date).strip())
            or (end_date and str(end_date).strip())
        )
        try:
            tz_offset_minutes = int(payload.get("tz_offset_minutes") or 0)
        except (TypeError, ValueError):
            tz_offset_minutes = 0
        try:
            bars = max(60, min(50000, int(payload.get("bars") or 600)))
        except (TypeError, ValueError):
            bars = 600

        start_utc: Optional[datetime] = None
        end_utc: Optional[datetime] = None
        if use_calendar:
            start_utc, end_utc, err_msg = self._parse_calendar_bounds(start_date, end_date, tz_offset_minutes)
            if err_msg or start_utc is None or end_utc is None:
                return {"ok": False, "error": err_msg or "ช่วงวันที่ไม่ถูกต้อง"}

        custom_capital: Optional[float] = None
        raw_cap = payload.get("initial_capital")
        if raw_cap is not None and str(raw_cap).strip() != "":
            try:
                parsed_cap = float(raw_cap)
                if parsed_cap > 0:
                    custom_capital = parsed_cap
            except (TypeError, ValueError):
                pass

        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 terminal unavailable"}
            target = str(payload.get("symbol") or self.config.symbol).strip() or self.config.symbol
            spec = self._gw.get_symbol_spec(target)
            if spec is None:
                return {"ok": False, "error": f"Symbol '{target}' not found"}
            offset_sec = int(spec.server_utc_offset_seconds or 0)

            eval_start_ts: Optional[int] = None
            actual_start_time: Optional[int] = None
            actual_end_time: Optional[int] = None
            range_notice_th: Optional[str] = None
            if use_calendar and start_utc is not None and end_utc is not None:
                win = self._fetch_calendar_window(
                    spec=spec,
                    timeframe=tf,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    tz_offset_minutes=tz_offset_minutes,
                    include_warmup=True,
                )
                if not win.get("ok"):
                    return {"ok": False, "error": win.get("error")}
                model_rates = win["model_rates"]
                display_rates = win["display_rates"]
                eval_start_ts = win.get("eval_start_ts")
                actual_start_time = win.get("actual_start_time")
                actual_end_time = win.get("actual_end_time")
                range_notice_th = win.get("range_notice_th")
                bars = len(display_rates)
            else:
                model_rates = self._gw.get_rates(spec.name, tf, bars)
                display_rates = model_rates

            acc = self._gw.get_account_state()
            acc_currency = (acc.currency if acc and acc.currency else "USD")
            acc_equity = (
                custom_capital
                if custom_capital is not None
                else (float(acc.equity if acc.equity > 0 else acc.balance) if acc else 10000.0)
            )
            eff_leverage = max(1, int(acc.leverage)) if (acc and acc.leverage > 0) else 100
            stop_out_pct = (
                float(acc.margin_so_so)
                if (acc and acc.margin_so_mode == int(StopOutMode.PERCENT) and acc.margin_so_so > 0)
                else 0.0
            )
            ref_px = spec.ask if spec.ask > 0 else spec.bid
            if ref_px > 0:
                notional_1 = estimate_notional_per_lot(spec, ref_px)
                margin_1 = self._gw.calc_margin(OrderSide.BUY, spec.name, 1.0, ref_px)
                if notional_1 > 0 and margin_1 is not None and margin_1 > 0:
                    eff_leverage = max(1, int(round(notional_1 / margin_1)))

        candles = self._serialize_rates_to_candles(display_rates, offset_sec)
        if candles:
            actual_start_time = actual_start_time or candles[0]["time"]
            actual_end_time = actual_end_time or candles[-1]["time"]

        report = run_pure_offensive_backtest(
            strategy=strategy,
            spec=spec,
            rates=model_rates,
            server_utc_offset_seconds=offset_sec,
            lookback_window=260,
            include_defensive=include_defensive,
            preventive_cfg=self.config.preventive if include_defensive else None,
            risk_cfg=self.config.risk,
            trade_cfg=self.config.trade_management if include_defensive else None,
            initial_equity=acc_equity,
            account_leverage=eff_leverage,
            account_currency=acc_currency,
            eval_start_ts=eval_start_ts,
            stop_out_pct=stop_out_pct,
        )
        poc_closed_df = (
            display_rates.iloc[max(0, len(display_rates) - 2501) : -1]
            if display_rates is not None and len(display_rates) > 20
            else display_rates
        )
        poc_analysis = analyze_structural_legs(
            closed_df=poc_closed_df,
            atr_period=int(getattr(strategy, "atr_swing_period", 5)),
            swing_mult=float(getattr(strategy, "swing_mult", 2.5)),
            max_boxes=50,
            max_active_per_side=3,
            bubble_mult=2.0,
            min_tick=max(spec.point, 1e-5),
            server_utc_offset_seconds=offset_sec,
        )
        poc_levels = [
            {
                "side": p.side,
                "price": round(p.price, spec.digits),
                "start_time": p.start_time,
                "end_time": p.end_time,
                "tested": p.tested_bar_idx is not None,
                "net_delta": round(p.net_delta, 1),
            }
            for p in poc_analysis.untested_pocs
        ]
        return sanitize_json(
            {
                "ok": True,
                "mode": "DEFENSIVE_AWARE" if include_defensive else "PURE_OFFENSIVE",
                "range_mode": "calendar" if use_calendar else "bars",
                "start_date": str(start_date).strip() if start_date else None,
                "end_date": str(end_date).strip() if end_date else None,
                "actual_start_time": actual_start_time,
                "actual_end_time": actual_end_time,
                "range_notice_th": range_notice_th,
                "symbol": spec.name,
                "timeframe": tf,
                "bars_requested": bars,
                "strategy_name": strategy.name,
                "strategy_display_name_th": strategy.display_name_th,
                "params": strategy.get_params(),
                "spec": spec,
                "candles": candles,
                "report": report,
                "poc_levels": poc_levels,
                "volume_spikes": poc_analysis.volume_spikes[-20:],
            }
        )

    def close_position_by_ticket(self, ticket: int) -> dict[str, Any]:
        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 unavailable"}
            positions = self._gw.get_open_positions()
            target = next((p for p in positions if p.ticket == ticket), None)
            if target is None:
                return {"ok": False, "error": f"Position #{ticket} not found"}
            spec = self._gw.get_symbol_spec(target.symbol)
            if spec is None:
                return {"ok": False, "error": f"Symbol {target.symbol} unavailable"}
            executor = self._build_executor(self.config)
            res = executor.close_position(target, spec)
            logger.info("Close position #%d (%s): %s", ticket, target.symbol, res.comment)
            return sanitize_json({"ok": res.executed or res.dry_run, "result": res})

    def breakeven_position_by_ticket(self, ticket: int) -> dict[str, Any]:
        with self._mt5_lock:
            if not self.ensure_connected():
                return {"ok": False, "error": "MT5 unavailable"}
            positions = self._gw.get_open_positions()
            target = next((p for p in positions if p.ticket == ticket), None)
            if target is None:
                return {"ok": False, "error": f"Position #{ticket} not found"}
            spec = self._gw.get_symbol_spec(target.symbol)
            if spec is None:
                return {"ok": False, "error": f"Symbol {target.symbol} unavailable"}
            eff_comm = (
                spec.detected_commission_per_lot
                if (self.config.preventive.auto_detect_commission and spec.detected_commission_per_lot is not None)
                else float(self.config.preventive.commission_per_lot_roundtrip or 0.0)
            )
            executor = self._build_executor(self.config)
            adj = executor.move_position_to_breakeven(
                pos=target,
                spec=spec,
                lock_points=self.config.trade_management.breakeven_lock_points,
                commission_per_lot=eff_comm,
                estimated_slippage_points=float(self.config.preventive.estimated_slippage_points or 0.0),
            )
            logger.info("Manual True BE position #%d (%s): %s", ticket, target.symbol, adj.comment)
            return sanitize_json({"ok": adj.executed or adj.dry_run, "adjustment": adj})

    def _load_forward_journal(self) -> None:
        if not self._enable_file_log or not self.journal_path.exists():
            return
        try:
            lines = self.journal_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    self._forward_journal.append(item)
        except Exception:
            pass

    def _append_journal_entry(self, entry: dict[str, Any]) -> None:
        cleaned = sanitize_json(entry)
        self._forward_journal.append(cleaned)
        if self._enable_file_log:
            try:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                with self.journal_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def _record_cycle_events(self, res: EvaluationCycleResult, cycle_num: int, now_utc: datetime) -> None:
        ts_sec = int(now_utc.timestamp())
        for adj in res.adjustments:
            self._append_journal_entry(
                {
                    "event_type": "ADJUSTMENT",
                    "status": "adjusted",
                    "dry_run": self.config.dry_run,
                    "cycle": cycle_num,
                    "time": ts_sec,
                    "symbol": adj.symbol,
                    "timeframe": self.config.timeframe,
                    "strategy_name": self._strategy.name,
                    "ticket": adj.ticket,
                    "action_name": adj.action_name,
                    "old_sl": adj.old_sl,
                    "new_sl": adj.new_sl,
                    "message": adj.comment,
                }
            )

        if res.status in (
            "executed",
            "dry_run_ok",
            "execution_failed",
            "blocked_by_preventive_guard",
            "rejected_by_risk_engine",
        ):
            event_type = (
                "ORDER"
                if res.status in ("executed", "dry_run_ok", "execution_failed")
                else ("RISK_REJECTED" if res.status == "rejected_by_risk_engine" else "BLOCKED")
            )
            reasons: list[str] = []
            if res.status == "blocked_by_preventive_guard" and res.preventive:
                reasons = list(res.preventive.reasons)
            elif res.status == "rejected_by_risk_engine" and res.lot_plan:
                reasons = list(res.lot_plan.rejection_reasons)
            elif res.status == "execution_failed" and res.execution:
                reasons = [res.execution.comment]

            snap = res.lot_plan.leverage_snapshot if res.lot_plan else None
            self._append_journal_entry(
                {
                    "event_type": event_type,
                    "status": res.status,
                    "dry_run": res.execution.dry_run if res.execution else self.config.dry_run,
                    "cycle": cycle_num,
                    "time": ts_sec,
                    "symbol": res.signal.symbol if res.signal else self.config.symbol,
                    "timeframe": self.config.timeframe,
                    "strategy_name": self._strategy.name,
                    "side": res.signal.side.value if res.signal else None,
                    "entry_price": res.signal.entry_price if res.signal else None,
                    "sl_price": res.signal.sl_price if res.signal else None,
                    "tp_price": res.signal.tp_price if res.signal else None,
                    "signal_reason": res.signal.reason if res.signal else None,
                    "lot": res.lot_plan.lot if res.lot_plan else None,
                    "margin_used": snap.projected_margin_used if snap else None,
                    "margin_level_pct": snap.projected_margin_level_pct if snap else None,
                    "order_ticket": res.execution.order_ticket if res.execution else None,
                    "executed_price": res.execution.executed_price if res.execution else None,
                    "retcode": res.execution.retcode if res.execution else None,
                    "message": res.execution.comment if res.execution else (reasons[0] if reasons else ""),
                    "reasons": reasons,
                }
            )

    def get_orders_and_history(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        tz_offset_minutes: int = 0,
        magic_only: bool = False,
        symbol: Optional[str] = None,
    ) -> dict[str, Any]:
        """Returns live open positions, real MT5 paired closed-trade history, balance ledger, and forward bot journal."""
        start_utc, end_utc, err_msg = self._parse_calendar_bounds(start_date, end_date, tz_offset_minutes)
        if err_msg or start_utc is None or end_utc is None:
            return {"ok": False, "error": err_msg or "ช่วงวันที่ไม่ถูกต้อง"}

        start_ts = int(start_utc.timestamp())
        end_ts = int(end_utc.timestamp())
        filter_sym = symbol.strip().upper() if (symbol and symbol.strip()) else ""

        with self._mt5_lock:
            connected = self.ensure_connected()
            account = self._gw.get_account_state() if connected else None
            spec = self._gw.get_symbol_spec(self.config.symbol) if connected else None
            offset_sec = int(spec.server_utc_offset_seconds or 0) if (spec and spec.server_utc_offset_seconds) else 0
            open_raw = self._gw.get_open_positions() if connected else []

            # Fetch deals with extra 30d lookback so trades opened before start_date still pair cleanly with entry deals
            raw_from = start_utc - timedelta(days=30) + timedelta(seconds=offset_sec) - timedelta(hours=2)
            raw_to = end_utc + timedelta(seconds=offset_sec) + timedelta(hours=2)
            raw_deals = (
                self._gw.get_deal_history(raw_from, raw_to)
                if (connected and hasattr(self._gw, "get_deal_history"))
                else []
            )
            journal_snapshot = list(self._forward_journal)

        open_positions: list[dict[str, Any]] = []
        open_bot_count = 0
        open_unrealized_pnl = 0.0
        for p in open_raw:
            raw_open_ts = getattr(p, "open_time", None)
            utc_open_ts = (raw_open_ts - offset_sec) if raw_open_ts else None
            is_bot = p.magic == self.config.magic_number
            net_open = round(float(p.profit) + float(p.swap), 2)
            if filter_sym and filter_sym not in p.symbol.upper():
                continue
            if magic_only and not is_bot:
                continue
            if is_bot:
                open_bot_count += 1
            open_unrealized_pnl += net_open
            open_positions.append(
                {
                    **sanitize_json(p),
                    "open_time": utc_open_ts,
                    "owned_by_bot": is_bot,
                    "net_profit": net_open,
                }
            )

        deals_by_pos: dict[int, list[dict[str, Any]]] = {}
        balance_events: list[dict[str, Any]] = []
        for d in raw_deals:
            deal_ts = int(d["time"]) - offset_sec
            side = str(d.get("side") or "")
            pos_id = int(d.get("position_id") or 0)
            if side in ("BALANCE", "CREDIT") or (pos_id == 0 and side not in ("BUY", "SELL")):
                if start_ts <= deal_ts <= end_ts:
                    balance_events.append(
                        {
                            "ticket": int(d["ticket"]),
                            "time": deal_ts,
                            "kind": side or "BALANCE",
                            "amount": round(float(d.get("profit") or 0.0), 2),
                            "comment": str(d.get("comment") or ""),
                        }
                    )
                continue
            if pos_id > 0:
                deals_by_pos.setdefault(pos_id, []).append({**d, "_utc_ts": deal_ts})

        closed_trades: list[dict[str, Any]] = []
        for pos_id, pos_deals in deals_by_pos.items():
            pos_deals.sort(key=lambda item: (item["_utc_ts"], int(item["ticket"])))
            ins = [d for d in pos_deals if d.get("entry_kind") == "IN"]
            outs = [d for d in pos_deals if d.get("entry_kind") in ("OUT", "INOUT", "OUT_BY")]
            if not outs:
                continue
            close_time = max(int(d["_utc_ts"]) for d in outs)
            if not (start_ts <= close_time <= end_ts):
                continue

            open_deal = ins[0] if ins else pos_deals[0]
            open_time = int(open_deal["_utc_ts"])
            tr_symbol = str(open_deal.get("symbol") or outs[-1].get("symbol") or "")
            tr_magic = int(open_deal.get("magic") or outs[-1].get("magic") or 0)
            is_bot = tr_magic == self.config.magic_number
            if filter_sym and filter_sym not in tr_symbol.upper():
                continue
            if magic_only and not is_bot:
                continue

            if ins:
                in_vol = sum(float(d["volume"]) for d in ins) or 0.0
                entry_price = (
                    sum(float(d["price"]) * float(d["volume"]) for d in ins) / in_vol
                    if in_vol > 0
                    else float(open_deal.get("price") or 0.0)
                )
                tr_side = "BUY" if open_deal.get("side") == "BUY" else "SELL"
            else:
                entry_price = float(outs[0].get("price") or 0.0)
                tr_side = "SELL" if outs[0].get("side") == "BUY" else "BUY"

            out_vol = sum(float(d["volume"]) for d in outs) or 0.0
            exit_price = (
                sum(float(d["price"]) * float(d["volume"]) for d in outs) / out_vol
                if out_vol > 0
                else float(outs[-1].get("price") or 0.0)
            )
            gross_profit = round(sum(float(d.get("profit") or 0.0) for d in pos_deals), 2)
            commission = round(sum(float(d.get("commission") or 0.0) for d in pos_deals), 2)
            swap = round(sum(float(d.get("swap") or 0.0) for d in pos_deals), 2)
            fee = round(sum(float(d.get("fee") or 0.0) for d in pos_deals), 2)
            net_pnl = round(gross_profit + commission + swap + fee, 2)

            closed_trades.append(
                {
                    "position_id": pos_id,
                    "symbol": tr_symbol,
                    "side": tr_side,
                    "volume": round(out_vol or float(open_deal.get("volume") or 0.0), 2),
                    "magic": tr_magic,
                    "owned_by_bot": is_bot,
                    "open_time": open_time,
                    "close_time": close_time,
                    "duration_seconds": max(0, close_time - open_time),
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "gross_profit": gross_profit,
                    "commission": commission,
                    "swap": swap,
                    "fee": fee,
                    "net_pnl": net_pnl,
                    "close_reason": str(outs[-1].get("reason") or "OTHER"),
                    "comment": str(open_deal.get("comment") or outs[-1].get("comment") or ""),
                }
            )

        closed_trades.sort(key=lambda t: t["close_time"], reverse=True)
        chronological = list(reversed(closed_trades))
        peak_cum = 0.0
        cum_pnl = 0.0
        max_dd_money = 0.0
        for tr in chronological:
            cum_pnl = round(cum_pnl + tr["net_pnl"], 2)
            if cum_pnl > peak_cum:
                peak_cum = cum_pnl
            dd_now = round(peak_cum - cum_pnl, 2)
            if dd_now > max_dd_money:
                max_dd_money = dd_now

        wins = sum(1 for t in closed_trades if t["net_pnl"] > 0)
        losses = sum(1 for t in closed_trades if t["net_pnl"] < 0)
        breakevens = sum(1 for t in closed_trades if t["net_pnl"] == 0)
        gross_win_money = round(sum(t["net_pnl"] for t in closed_trades if t["net_pnl"] > 0), 2)
        gross_loss_money = round(abs(sum(t["net_pnl"] for t in closed_trades if t["net_pnl"] < 0)), 2)
        net_realized_pnl = round(sum(t["net_pnl"] for t in closed_trades), 2)
        profit_factor = (
            round(gross_win_money / gross_loss_money, 2)
            if gross_loss_money > 0
            else (round(gross_win_money, 2) if gross_win_money > 0 else None)
        )

        journal_filtered = [
            item
            for item in reversed(journal_snapshot)
            if start_ts <= int(item.get("time") or 0) <= end_ts
            and (not filter_sym or filter_sym in str(item.get("symbol") or "").upper())
        ]
        balance_events.sort(key=lambda b: b["time"], reverse=True)

        return sanitize_json(
            {
                "ok": True,
                "mt5_connected": connected,
                "currency": account.currency if account else "USD",
                "account_equity": float(account.equity) if account else None,
                "account_balance": float(account.balance) if account else None,
                "bot_magic_number": self.config.magic_number,
                "bot_state": {
                    "running": self._bot_running,
                    "dry_run": self.config.dry_run,
                    "symbol": spec.name if spec else self.config.symbol,
                    "timeframe": self.config.timeframe,
                    "strategy_name": self._strategy.name,
                    "strategy_display_name": self._strategy.display_name_th,
                    "cycle_count": self._cycle_count,
                    "last_cycle_time": self._last_cycle_time,
                    "last_cycle": self._last_cycle,
                },
                "start_time": start_ts,
                "end_time": end_ts,
                "open_positions": open_positions,
                "closed_trades": closed_trades,
                "balance_events": balance_events,
                "forward_journal": journal_filtered[:250],
                "summary": {
                    "closed_count": len(closed_trades),
                    "wins": wins,
                    "losses": losses,
                    "breakevens": breakevens,
                    "win_rate_pct": round((wins / len(closed_trades)) * 100.0, 1) if closed_trades else 0.0,
                    "gross_profit_money": round(sum(t["gross_profit"] for t in closed_trades), 2),
                    "total_commission": round(sum(t["commission"] + t["fee"] for t in closed_trades), 2),
                    "total_swap": round(sum(t["swap"] for t in closed_trades), 2),
                    "net_realized_pnl": net_realized_pnl,
                    "profit_factor": profit_factor,
                    "max_drawdown_money": max_dd_money,
                    "total_volume": round(sum(t["volume"] for t in closed_trades), 2),
                    "bot_trades_count": sum(1 for t in closed_trades if t["owned_by_bot"]),
                    "manual_trades_count": sum(1 for t in closed_trades if not t["owned_by_bot"]),
                    "deposits_net": round(sum(b["amount"] for b in balance_events), 2),
                    "open_count": len(open_positions),
                    "open_bot_count": open_bot_count,
                    "open_unrealized_pnl": round(open_unrealized_pnl, 2),
                },
            }
        )

    def get_logs(self, limit: int = 120) -> list[dict[str, str]]:
        log_file = self.root_dir / "logs" / "cfd_bot.log"
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
            parsed = []
            for line in lines:
                # Format: YYYY-MM-DD HH:MM:SS [LEVEL] message
                if " [" in line and "] " in line:
                    ts, rest = line.split(" [", 1)
                    lvl, msg = rest.split("] ", 1)
                    parsed.append({"time": ts, "level": lvl, "message": msg})
                else:
                    parsed.append({"time": "", "level": "INFO", "message": line})
            if parsed:
                return parsed
        return self.ring_handler.tail(limit)

    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        result = dict(base)
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = OperationService._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    @staticmethod
    def _env_opt(val: Optional[int | float]) -> str:
        return "NONE" if val is None else str(val)

    def _persist_env(self, cfg: BotConfig) -> None:
        opt = self._env_opt
        lines = [
            f"MT5_LOGIN={cfg.mt5.login or ''}",
            f"MT5_PASSWORD={cfg.mt5.password or ''}",
            f"MT5_SERVER={cfg.mt5.server or ''}",
            f"MT5_PATH={cfg.mt5.path or ''}",
            f"BOT_SYMBOL={cfg.symbol}",
            f"BOT_TIMEFRAME={cfg.timeframe}",
            f"BOT_MAGIC_NUMBER={cfg.magic_number}",
            f"BOT_DRY_RUN={'true' if cfg.dry_run else 'false'}",
            f"BOT_POLL_SECONDS={cfg.poll_seconds}",
            f"BOT_DEVIATION_POINTS={cfg.deviation_points}",
            f"LOT_MODE={cfg.risk.lot_mode.value}",
            f"FIXED_LOT={cfg.risk.fixed_lot}",
            f"RISK_PERCENT={cfg.risk.risk_percent}",
            f"USE_EQUITY_FOR_RISK={'true' if cfg.risk.use_equity_for_risk else 'false'}",
            f"EQUITY_STEP_AMOUNT={cfg.risk.equity_step_amount}",
            f"EQUITY_STEP_LOT={cfg.risk.equity_step_lot}",
            f"MAX_LOT_CAP={opt(cfg.risk.max_lot_cap)}",
            f"MIN_SL_POINTS={opt(cfg.risk.min_sl_points)}",
            f"INCLUDE_COSTS_IN_RISK_SIZING={'true' if cfg.risk.include_costs_in_risk_sizing else 'false'}",
            f"MAX_EFFECTIVE_ACCOUNT_LEVERAGE={opt(cfg.risk.max_effective_account_leverage)}",
            f"MAX_EFFECTIVE_SYMBOL_LEVERAGE={opt(cfg.risk.max_effective_symbol_leverage)}",
            f"MAX_MARGIN_USAGE_PCT={opt(cfg.risk.max_margin_usage_pct)}",
            f"MIN_MARGIN_LEVEL_PCT={opt(cfg.risk.min_margin_level_pct)}",
            f"MIN_STOPOUT_BUFFER_MULTIPLIER={opt(cfg.risk.min_stopout_buffer_multiplier)}",
            f"SCALE_DOWN_LOT_FOR_LEVERAGE={'true' if cfg.risk.scale_down_lot_for_leverage else 'false'}",
            f"MAX_SPREAD_POINTS={opt(cfg.preventive.max_spread_points)}",
            f"MAX_SPREAD_TO_TP_RATIO={opt(cfg.preventive.max_spread_to_tp_ratio)}",
            f"MAX_SPREAD_TO_ATR_RATIO={opt(cfg.preventive.max_spread_to_atr_ratio)}",
            f"COMMISSION_PER_LOT_ROUNDTRIP={opt(cfg.preventive.commission_per_lot_roundtrip)}",
            f"AUTO_DETECT_COMMISSION={'true' if cfg.preventive.auto_detect_commission else 'false'}",
            f"ESTIMATED_SLIPPAGE_POINTS={opt(cfg.preventive.estimated_slippage_points)}",
            f"MIN_NET_RR={opt(cfg.preventive.min_net_rr)}",
            f"MAX_SWAP_TO_TP_RATIO={opt(cfg.preventive.max_swap_to_tp_ratio)}",
            f"SWAP_ROLLOVER_HOUR_SERVER={cfg.preventive.swap_rollover_hour_server}",
            f"ROLLOVER_BLACKOUT_MINUTES_BEFORE={opt(cfg.preventive.rollover_blackout_minutes_before)}",
            f"ROLLOVER_BLACKOUT_MINUTES_AFTER={opt(cfg.preventive.rollover_blackout_minutes_after)}",
            f"ENFORCE_ROLLOVER_BLACKOUT_FOR_INTRADAY={'true' if cfg.preventive.enforce_rollover_blackout_for_intraday else 'false'}",
            f"MAX_DAILY_LOSS_PCT={opt(cfg.preventive.max_daily_loss_pct)}",
            f"MAX_OPEN_POSITIONS_SYMBOL={opt(cfg.preventive.max_open_positions_symbol)}",
            f"MAX_OPEN_POSITIONS_TOTAL={opt(cfg.preventive.max_open_positions_total)}",
            f"MAX_QUOTE_AGE_SECONDS={opt(cfg.preventive.max_quote_age_seconds)}",
            f"ALLOW_WEEKEND_TRADING={'true' if cfg.preventive.allow_weekend_trading else 'false'}",
            f"ENABLE_NEWS_GUARD={'true' if cfg.preventive.enable_news_guard else 'false'}",
            f"ENABLE_HOLIDAY_GUARD={'true' if cfg.preventive.enable_holiday_guard else 'false'}",
            f"HOLIDAY_POLICY={cfg.preventive.holiday_policy}",
            f"NEWS_BLOCK_MINUTES_BEFORE={opt(cfg.preventive.news_block_minutes_before)}",
            f"NEWS_BLOCK_MINUTES_AFTER={opt(cfg.preventive.news_block_minutes_after)}",
            f"BLOCK_MEDIUM_IMPACT_NEWS={'true' if cfg.preventive.block_medium_impact_news else 'false'}",
            f"NEWS_MEDIUM_BLOCK_MINUTES_BEFORE={opt(cfg.preventive.news_medium_block_minutes_before)}",
            f"NEWS_MEDIUM_BLOCK_MINUTES_AFTER={opt(cfg.preventive.news_medium_block_minutes_after)}",
            f"NEWS_WARN_HOURS={opt(cfg.preventive.news_warn_hours)}",
            f"FAIL_CLOSED_ON_CALENDAR_ERROR={'true' if cfg.preventive.fail_closed_on_calendar_error else 'false'}",
            f"CUSTOM_EVENTS_PATH={cfg.preventive.custom_events_path or ''}",
            f"ENABLE_AUTO_BREAKEVEN={'true' if cfg.trade_management.enable_auto_breakeven else 'false'}",
            f"BREAKEVEN_TRIGGER_R={cfg.trade_management.breakeven_trigger_r}",
            f"BREAKEVEN_LOCK_POINTS={cfg.trade_management.breakeven_lock_points}",
            f"ENABLE_TRAILING_STOP={'true' if cfg.trade_management.enable_trailing_stop else 'false'}",
            f"TRAILING_ATR_MULT={cfg.trade_management.trailing_atr_mult}",
            f"TRAILING_STEP_POINTS={cfg.trade_management.trailing_step_points}",
            f"ACTIVE_STRATEGY={cfg.strategy.active_strategy}",
            f"STRATEGY_PARAMS_JSON={json.dumps(cfg.strategy.params, ensure_ascii=False, separators=(',', ':'))}",
        ]
        self.env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
