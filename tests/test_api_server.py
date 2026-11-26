from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import MetaTrader5 as mt5
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import BotConfig, load_config_from_env
from cfd_bot.application.operation_service import OperationService, sanitize_json
from cfd_bot.domain.models import AccountState, CalendarCheckResult, EconomicEvent, NewsImpact, OpenPosition, OrderSide, SymbolSpec
from cfd_bot.infrastructure.web.api_server import build_operation_service, create_app


class FakeCalendar:
    def __init__(self) -> None:
        self.events: list[EconomicEvent] = []

    def evaluate(self, spec: SymbolSpec, now: datetime, expected_hold_days: float = 0.0) -> CalendarCheckResult:
        return CalendarCheckResult(blocked=False, reasons=[], warnings=[], upcoming_events=[])

    def get_events(self, currencies: set[str], now: datetime) -> tuple[list[EconomicEvent], list[str]]:
        return self.events, []

    def get_bridge_status(self) -> dict:
        return {"detected": False, "path": "", "event_count": 0, "updated_utc": None}

    def save_manual_actual(
        self, title: str, currency: str, event_time_iso: str, actual: str, forecast=None, previous=None, impact="HIGH"
    ) -> list[dict]:
        self.events = [
            EconomicEvent(
                title=title,
                currency=currency,
                impact=NewsImpact(impact),
                event_time=datetime.fromisoformat(event_time_iso),
                actual=actual,
                forecast=forecast,
                previous=previous,
                source="manual",
            )
        ]
        return [{"title": title, "actual": actual}]


class FakeGateway:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.spec = SymbolSpec(
            name="EURUSDm",
            digits=5,
            point=0.00001,
            ask=1.09850,
            bid=1.09842,
            spread_points=8,
            volume_min=0.01,
            volume_max=200.0,
            volume_step=0.01,
            trade_contract_size=100000.0,
            trade_tick_size=0.00001,
            trade_tick_value=1.0,
            trade_tick_value_profit=1.0,
            trade_tick_value_loss=1.0,
            quote_time=now,
            server_utc_offset_seconds=0,
        )

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def resolve_symbol(self, symbol: str) -> str:
        return self.spec.name

    def get_account_state(self) -> AccountState:
        return AccountState(
            login=414590470,
            server="Fake-MT5",
            currency="USD",
            currency_digits=2,
            balance=1000.0,
            equity=1000.0,
            margin=0.0,
            margin_free=1000.0,
            margin_level=0.0,
            leverage=200,
        )

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        return self.spec

    def calc_margin(self, side: OrderSide, symbol: str, volume: float, price: float) -> float:
        return 549.25 * volume

    def calc_profit(self, side: OrderSide, symbol: str, volume: float, price_open: float, price_close: float) -> float:
        return 0.0

    def get_open_positions(self, symbol=None, magic=None):
        return [
            OpenPosition(
                ticket=99001,
                symbol="EURUSDm",
                side=OrderSide.BUY,
                volume=0.05,
                price_open=1.0981,
                price_current=1.09842,
                sl=1.0965,
                tp=1.1015,
                profit=1.60,
                swap=0.0,
                magic=202611,
                open_time=int(datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc).timestamp()),
            )
        ]

    def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "time": datetime(2026, 11, 25, h % 24, 0, tzinfo=timezone.utc),
                    "open": 1.098,
                    "high": 1.099,
                    "low": 1.097,
                    "close": 1.0984,
                    "tick_volume": 100,
                }
                for h in range(30)
            ]
        )

    def get_rates_range(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        return self.get_rates(symbol, timeframe, 30)

    def get_deal_history(self, date_from: datetime, date_to: datetime) -> list[dict]:
        return []

    def order_check(self, request: dict) -> dict:
        return {"retcode": 0, "margin": 16.48, "margin_free": 983.52, "comment": "OK"}

    def order_send(self, request: dict) -> dict:
        return {"retcode": mt5.TRADE_RETCODE_DONE, "order": 1, "deal": 1, "volume": 0.0, "price": 0.0, "comment": "OK"}


async def _call_asgi(app, method: str, path: str, body: dict | None = None, query: str = "") -> tuple[int, dict]:
    raw_body = json.dumps(body).encode("utf-8") if body is not None else b""
    sent = False
    status_code = 500
    chunks: list[bytes] = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query.encode("ascii"),
        "headers": [(b"content-type", b"application/json")],
        "server": ("127.0.0.1", 8787),
        "client": ("127.0.0.1", 50000),
    }
    await app(scope, receive, send)
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    return status_code, payload


class TestApiServer(unittest.TestCase):
    def test_sanitize_json_handles_inf_and_nan(self) -> None:
        cleaned = sanitize_json({"inf": float("inf"), "nan": float("nan"), "ok": 1.25})
        self.assertIsNone(cleaned["inf"])
        self.assertIsNone(cleaned["nan"])
        self.assertEqual(cleaned["ok"], 1.25)

    def test_overview_simulate_config_and_custom_calendar_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = build_operation_service(
                root_dir=tmp,
                gateway=FakeGateway(),
                config=BotConfig(symbol="EURUSD", dry_run=True),
                calendar_service=FakeCalendar(),
                enable_file_log=False,
            )
            app = create_app(svc)

            # 1. Overview
            status, ov = asyncio.run(_call_asgi(app, "GET", "/api/overview"))
            self.assertEqual(status, 200)
            self.assertTrue(ov["mt5_connected"])
            self.assertEqual(ov["account"]["login"], 414590470)
            self.assertEqual(ov["bot_state"]["symbol"], "EURUSDm")

            # 2. Simulate trade (default + flexible what-if overrides)
            status, sim = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/simulate",
                    body={"symbol": "EURUSD", "side": "BUY", "lot_mode": "dynamic_risk", "risk_percent": 1.0, "sl_points": 200, "tp_points": 400},
                )
            )
            self.assertEqual(status, 200)
            self.assertTrue(sim["ok"])
            self.assertTrue(sim["lot_plan"]["approved"])
            self.assertGreater(sim["lot_plan"]["lot"], 0.0)
            self.assertGreaterEqual(len(sim.get("scenarios", [])), 4)

            # 2b. Flexible What-If simulation (custom_equity, risk_amount $, exact chart prices, ignore caps)
            status, sim_flex = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/simulate",
                    body={
                        "symbol": "EURUSD",
                        "side": "BUY",
                        "custom_equity": 10000,
                        "risk_amount": 150,
                        "entry_price": 1.10000,
                        "sl_price": 1.09700,
                        "tp_price": 1.10900,
                        "ignore_leverage_caps": True,
                    },
                )
            )
            self.assertEqual(status, 200)
            self.assertTrue(sim_flex["ok"])
            self.assertAlmostEqual(sim_flex["equity_used"], 10000.0, places=2)
            self.assertAlmostEqual(sim_flex["sl_points_used"], 300.0, places=1)
            self.assertAlmostEqual(sim_flex["tp_points_used"], 900.0, places=1)
            self.assertAlmostEqual(sim_flex["lot_plan"]["risk_budget_money"], 150.0, places=2)

            # 3. Update & persist config
            status, cfg_res = asyncio.run(
                _call_asgi(app, "PUT", "/api/config", body={"symbol": "XAUUSD", "risk": {"risk_percent": 0.5}})
            )
            self.assertEqual(status, 200)
            self.assertTrue(cfg_res["ok"])
            self.assertTrue((Path(tmp) / ".env").exists())
            self.assertEqual(cfg_res["config"]["symbol"], "XAUUSD")
            self.assertEqual(cfg_res["config"]["risk"]["risk_percent"], 0.5)

            # 4. Evaluate once
            status, eval_res = asyncio.run(_call_asgi(app, "POST", "/api/bot/evaluate-once"))
            self.assertEqual(status, 200)
            self.assertTrue(eval_res["ok"])

            # 5. Add & delete custom calendar event
            status, add_res = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/calendar/custom",
                    body={"title": "Test Holiday", "currency": "USD", "impact": "HOLIDAY", "date": "2026-12-25"},
                )
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(add_res["custom_events"]), 1)

            status, del_res = asyncio.run(_call_asgi(app, "DELETE", "/api/calendar/custom/0"))
            self.assertEqual(status, 200)
            self.assertEqual(len(del_res["custom_events"]), 0)

            # 6. Post-news intelligence & manual actual entry
            ev_iso = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            status, save_act = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/news-actuals",
                    body={
                        "title": "Core PCE Price Index m/m",
                        "currency": "USD",
                        "event_time": ev_iso,
                        "actual": "0.5%",
                        "forecast": "0.3%",
                    },
                )
            )
            self.assertEqual(status, 200)
            self.assertTrue(save_act["ok"])

            status, intel = asyncio.run(_call_asgi(app, "GET", "/api/news-intelligence", query="symbol=EURUSD"))
            self.assertEqual(status, 200)
            self.assertEqual(intel["aggregate_bias"], "BEARISH")
            self.assertEqual(len(intel["cfd_matrix"]), 4)

            # 6b. Candles endpoint for TradingView chart (ascending, deduplicated)
            status, candles_res = asyncio.run(
                _call_asgi(app, "GET", "/api/candles", query="symbol=EURUSD&timeframe=M15&count=50")
            )
            self.assertEqual(status, 200)
            self.assertTrue(candles_res["ok"])
            self.assertEqual(candles_res["symbol"], "EURUSDm")
            self.assertEqual(candles_res["timeframe"], "M15")
            times = [c["time"] for c in candles_res["candles"]]
            self.assertEqual(len(times), 24)
            self.assertEqual(times, sorted(times))
            self.assertEqual(len(candles_res["positions"]), 1)
            self.assertEqual(candles_res["positions"][0]["ticket"], 99001)
            self.assertIsNotNone(candles_res["positions"][0]["open_time"])

            # 7. Realtime live SSE snapshot endpoint (?once=1)
            snap = svc.get_live_snapshot(["EURUSD"])
            self.assertTrue(snap["overview"]["mt5_connected"])
            self.assertEqual(len(snap["watchlist"]), 1)
            self.assertEqual(snap["watchlist"][0]["spec"]["name"], "EURUSDm")

            async def _check_sse() -> str:
                raw_chunks: list[bytes] = []
                sse_status = 500
                sent_flag = False
                disconnect_ev = asyncio.Event()

                async def sse_receive():
                    nonlocal sent_flag
                    if not sent_flag:
                        sent_flag = True
                        return {"type": "http.request", "body": b"", "more_body": False}
                    await disconnect_ev.wait()
                    return {"type": "http.disconnect"}

                async def sse_send(msg):
                    nonlocal sse_status
                    if msg["type"] == "http.response.start":
                        sse_status = msg["status"]
                    elif msg["type"] == "http.response.body":
                        raw_chunks.append(msg.get("body", b""))
                        if not msg.get("more_body", False):
                            disconnect_ev.set()

                await app(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0"},
                        "http_version": "1.1",
                        "method": "GET",
                        "scheme": "http",
                        "path": "/api/stream",
                        "raw_path": b"/api/stream",
                        "query_string": b"once=1&symbols=EURUSD",
                        "headers": [],
                        "server": ("127.0.0.1", 8787),
                        "client": ("127.0.0.1", 50000),
                    },
                    sse_receive,
                    sse_send,
                )
                self.assertEqual(sse_status, 200)
                return b"".join(raw_chunks).decode("utf-8")

            sse_text = asyncio.run(_check_sse())
            self.assertTrue(sse_text.startswith("data: "))
            sse_json = json.loads(sse_text.removeprefix("data: ").strip())
            self.assertEqual(sse_json["watchlist"][0]["spec"]["name"], "EURUSDm")

            # 8. Optional config limits saved as null persist as NONE and reload as None
            status, null_cfg_res = asyncio.run(
                _call_asgi(
                    app,
                    "PUT",
                    "/api/config",
                    body={
                        "risk": {
                            "max_lot_cap": None,
                            "max_effective_account_leverage": None,
                            "max_margin_usage_pct": None,
                        },
                        "preventive": {
                            "max_spread_points": None,
                            "min_net_rr": "",
                            "max_daily_loss_pct": None,
                            "news_block_minutes_before": None,
                        },
                    },
                )
            )
            self.assertEqual(status, 200)
            self.assertIsNone(null_cfg_res["config"]["risk"]["max_lot_cap"])
            self.assertIsNone(null_cfg_res["config"]["preventive"]["min_net_rr"])
            env_content = (Path(tmp) / ".env").read_text(encoding="utf-8")
            self.assertIn("MAX_LOT_CAP=NONE", env_content)
            self.assertIn("MAX_DAILY_LOSS_PCT=NONE", env_content)

            with mock.patch.dict(os.environ, {}, clear=True):
                reloaded = load_config_from_env(str(Path(tmp) / ".env"))
            self.assertIsNone(reloaded.risk.max_lot_cap)
            self.assertIsNone(reloaded.risk.max_effective_account_leverage)
            self.assertIsNone(reloaded.preventive.min_net_rr)
            self.assertIsNone(reloaded.preventive.max_daily_loss_pct)

    def test_offensive_strategy_catalog_switch_and_backtest_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = build_operation_service(
                root_dir=tmp,
                gateway=FakeGateway(),
                config=BotConfig(symbol="EURUSD", dry_run=True),
                calendar_service=FakeCalendar(),
                enable_file_log=False,
            )
            app = create_app(svc)

            # Set conflicting defensive values first to verify conflict detection & isolation
            status, _ = asyncio.run(
                _call_asgi(
                    app,
                    "PUT",
                    "/api/config",
                    body={
                        "timeframe": "M15",
                        "preventive": {"max_spread_points": 123.0, "min_net_rr": 1.30, "max_spread_to_tp_ratio": 0.15},
                        "trade_management": {"enable_auto_breakeven": True, "breakeven_trigger_r": 0.9},
                    },
                )
            )
            self.assertEqual(status, 200)

            # 1. Fetch strategy catalog & check detected defensive conflicts
            status, cat = asyncio.run(_call_asgi(app, "GET", "/api/strategies"))
            self.assertEqual(status, 200)
            self.assertTrue(cat["ok"])
            self.assertEqual(cat["active_strategy"], "M5KernelPullback")
            self.assertEqual(
                [s["name"] for s in cat["strategies"]],
                ["M5KernelPullback", "VolatilitySqueezeBreakout", "M5RangeScalper"],
            )
            conflict_codes = {c["code"] for c in cat.get("defensive_conflicts", [])}
            self.assertEqual(
                conflict_codes,
                {"timeframe_mismatch", "min_net_rr_clash", "spread_to_tp_tight", "breakeven_early_trigger"},
            )

            # 1b. One-click sync resolves overlaps cleanly without wiping unrelated caps
            status_sync, synced = asyncio.run(_call_asgi(app, "POST", "/api/strategies/sync-defensive"))
            self.assertEqual(status_sync, 200)
            self.assertEqual(synced["defensive_conflicts"], [])

            # 2. Tune active M5 offensive parameters cleanly
            status, switched = asyncio.run(
                _call_asgi(
                    app,
                    "PUT",
                    "/api/strategies/active",
                    body={
                        "strategy_name": "M5KernelPullback",
                        "params": {"min_adx": 20.0, "tp_rr_multiplier": 1.35},
                    },
                )
            )
            self.assertEqual(status, 200)
            self.assertTrue(switched["ok"])
            self.assertEqual(switched["active_strategy"], "M5KernelPullback")
            self.assertEqual(switched["active_params"]["min_adx"], 20.0)

            # Defensive synced values + unrelated cap preserved
            _, cfg_res = asyncio.run(_call_asgi(app, "GET", "/api/config"))
            self.assertEqual(cfg_res["timeframe"], "M5")
            self.assertEqual(cfg_res["preventive"]["max_spread_points"], 123.0)
            self.assertEqual(cfg_res["preventive"]["min_net_rr"], 1.0)
            self.assertEqual(cfg_res["preventive"]["max_spread_to_tp_ratio"], 0.25)
            self.assertGreaterEqual(cfg_res["trade_management"]["breakeven_trigger_r"], 1.15)

            # Verify .env persistence round-trip for active M5 strategy
            env_content = (Path(tmp) / ".env").read_text(encoding="utf-8")
            self.assertIn("ACTIVE_STRATEGY=M5KernelPullback", env_content)
            with mock.patch.dict(os.environ, {}, clear=True):
                reloaded = load_config_from_env(str(Path(tmp) / ".env"))
            self.assertEqual(reloaded.strategy.active_strategy, "M5KernelPullback")
            self.assertEqual(reloaded.strategy.params["min_adx"], 20.0)

            # 3. Pure Offensive backtest vs Defensive-Aware backtest + custom bars=3000
            status, bt = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/strategies/backtest",
                    body={
                        "strategy_name": "M5KernelPullback",
                        "params": {"min_adx": 18.0},
                        "symbol": "EURUSD",
                        "timeframe": "M5",
                        "bars": 3000,
                        "include_defensive": False,
                    },
                )
            )
            self.assertEqual(status, 200)
            self.assertTrue(bt["ok"])
            self.assertEqual(bt["mode"], "PURE_OFFENSIVE")
            self.assertEqual(bt["strategy_name"], "M5KernelPullback")
            self.assertEqual(bt["bars_requested"], 3000)
            self.assertFalse(bt["report"]["defensive_applied"])

            status2, bt_def = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/strategies/backtest",
                    body={
                        "strategy_name": "M5KernelPullback",
                        "symbol": "EURUSD",
                        "timeframe": "M5",
                        "bars": 600,
                        "include_defensive": True,
                    },
                )
            )
            self.assertEqual(status2, 200)
            self.assertEqual(bt_def["mode"], "DEFENSIVE_AWARE")
            self.assertTrue(bt_def["report"]["defensive_applied"])

            # 3b. Switch active strategy to VolatilitySqueezeBreakout and sync Defensive timeframe to H1
            st_sq, sw_sq = asyncio.run(
                _call_asgi(
                    app,
                    "PUT",
                    "/api/strategies/active",
                    body={"strategy_name": "VolatilitySqueezeBreakout", "params": {"min_adx": 18.0, "tp_rr_multiplier": 1.8}},
                )
            )
            self.assertEqual(st_sq, 200)
            self.assertEqual(sw_sq["active_strategy"], "VolatilitySqueezeBreakout")
            conflict_codes_sq = {c["code"] for c in sw_sq.get("defensive_conflicts", [])}
            self.assertIn("timeframe_mismatch", conflict_codes_sq)
            st_sync_sq, synced_sq = asyncio.run(_call_asgi(app, "POST", "/api/strategies/sync-defensive"))
            self.assertEqual(st_sync_sq, 200)
            self.assertEqual(synced_sq["bot_timeframe"], "H1")
            self.assertEqual(synced_sq["defensive_conflicts"], [])

            # 3c. Switch active strategy to M5RangeScalper and verify H1 -> M5 timeframe conflict sync
            st_hft, sw_hft = asyncio.run(
                _call_asgi(
                    app,
                    "PUT",
                    "/api/strategies/active",
                    body={"strategy_name": "M5RangeScalper", "params": {"channel_mode": 1, "tp_rr_multiplier": 1.5}},
                )
            )
            self.assertEqual(st_hft, 200)
            self.assertEqual(sw_hft["active_strategy"], "M5RangeScalper")
            conflict_codes_hft = {c["code"] for c in sw_hft.get("defensive_conflicts", [])}
            self.assertEqual(conflict_codes_hft, {"timeframe_mismatch"})
            st_sync_hft, synced_hft = asyncio.run(_call_asgi(app, "POST", "/api/strategies/sync-defensive"))
            self.assertEqual(st_sync_hft, 200)
            self.assertEqual(synced_hft["bot_timeframe"], "M5")
            self.assertEqual(synced_hft["defensive_conflicts"], [])

            # 4. Reject retired or unknown strategy cleanly
            status_ret, bad_ret = asyncio.run(
                _call_asgi(app, "PUT", "/api/strategies/active", body={"strategy_name": "SessionRangeBreakout"})
            )
            self.assertEqual(status_ret, 400)
            self.assertFalse(bad_ret["ok"])
            status, bad = asyncio.run(
                _call_asgi(app, "PUT", "/api/strategies/active", body={"strategy_name": "UnknownStrat"})
            )
            self.assertEqual(status, 400)
            self.assertFalse(bad["ok"])

    def test_calendar_date_range_backtest_and_candles_endpoints(self) -> None:
        class CalendarFakeGateway(FakeGateway):
            def __init__(self) -> None:
                super().__init__()
                rows = []
                price = 1.0800
                t0 = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
                for i in range(9 * 288):
                    wave = 0.0005 if (i % 18) < 12 else -0.0004
                    o = price
                    c = round(o + wave, 5)
                    h = round(max(o, c) + 0.00025, 5)
                    l = round(min(o, c) - 0.00025, 5)
                    rows.append(
                        {
                            "time": t0 + timedelta(minutes=5 * i),
                            "open": o,
                            "high": h,
                            "low": l,
                            "close": c,
                            "tick_volume": 120,
                        }
                    )
                    price = c
                self._df = pd.DataFrame(rows)

            def get_rates_range(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
                mask = (self._df["time"] >= date_from) & (self._df["time"] <= date_to)
                return self._df.loc[mask].copy().reset_index(drop=True)

        with tempfile.TemporaryDirectory() as tmp:
            svc = build_operation_service(
                root_dir=tmp,
                gateway=CalendarFakeGateway(),
                config=BotConfig(symbol="EURUSD", dry_run=True),
                calendar_service=FakeCalendar(),
                enable_file_log=False,
            )
            app = create_app(svc)

            # Local UTC+7 window 2026-11-14 00:00..2026-11-16 23:59 -> UTC 2026-11-13 17:00..2026-11-16 16:59
            win_start_utc = int(datetime(2026, 11, 13, 17, 0, 0, tzinfo=timezone.utc).timestamp())
            win_end_utc = int(datetime(2026, 11, 16, 16, 59, 59, tzinfo=timezone.utc).timestamp())

            st_bt, bt_cal = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/strategies/backtest",
                    body={
                        "strategy_name": "M5KernelPullback",
                        "params": {"require_ml_confirm": 0, "min_adx": 8.0, "pullback_band_atr": 0.8},
                        "symbol": "EURUSD",
                        "timeframe": "M5",
                        "bars": 1500,
                        "range_mode": "calendar",
                        "start_date": "2026-11-14",
                        "end_date": "2026-11-16",
                        "tz_offset_minutes": 420,
                        "include_defensive": False,
                    },
                )
            )
            self.assertEqual(st_bt, 200)
            self.assertTrue(bt_cal["ok"])
            self.assertEqual(bt_cal["range_mode"], "calendar")
            candles = bt_cal["candles"]
            self.assertGreater(len(candles), 500)
            self.assertGreaterEqual(candles[0]["time"], win_start_utc)
            self.assertLessEqual(candles[-1]["time"], win_end_utc)
            self.assertEqual(bt_cal["report"]["bars_tested"], len(candles))
            for tr in bt_cal["report"]["trades"]:
                self.assertGreaterEqual(tr["entry_time"], win_start_utc)
                self.assertLessEqual(tr["exit_time"], win_end_utc)

            # Candles API also honors calendar dates
            st_c, c_res = asyncio.run(
                _call_asgi(
                    app,
                    "GET",
                    "/api/candles",
                    query="symbol=EURUSD&timeframe=M5&start_date=2026-11-14&end_date=2026-11-15&tz_offset_minutes=420",
                )
            )
            self.assertEqual(st_c, 200)
            self.assertTrue(c_res["ok"])
            self.assertEqual(c_res["range_mode"], "calendar")
            c_bars = c_res["candles"]
            self.assertGreaterEqual(c_bars[0]["time"], win_start_utc)
            self.assertLessEqual(
                c_bars[-1]["time"],
                int(datetime(2026, 11, 15, 16, 59, 59, tzinfo=timezone.utc).timestamp()),
            )

            # Invalid date order returns 400
            st_bad, bad_res = asyncio.run(
                _call_asgi(
                    app,
                    "POST",
                    "/api/strategies/backtest",
                    body={
                        "strategy_name": "M5KernelPullback",
                        "range_mode": "calendar",
                        "start_date": "2026-11-16",
                        "end_date": "2026-11-14",
                    },
                )
            )
            self.assertEqual(st_bad, 400)
            self.assertFalse(bad_res["ok"])

    def test_orders_history_and_forward_journal_persistence(self) -> None:
        class OrdersFakeGateway(FakeGateway):
            def get_deal_history(self, date_from: datetime, date_to: datetime) -> list[dict]:
                t_open = int(datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc).timestamp())
                t_close = int(datetime(2026, 11, 25, 11, 30, tzinfo=timezone.utc).timestamp())
                t_man_open = int(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc).timestamp())
                t_man_close = int(datetime(2026, 11, 25, 13, 0, tzinfo=timezone.utc).timestamp())
                return [
                    {
                        "ticket": 1,
                        "order": 100,
                        "position_id": 0,
                        "time": t_open - 3600,
                        "side": "BALANCE",
                        "entry_kind": "IN",
                        "reason": "OTHER",
                        "magic": 0,
                        "symbol": "",
                        "volume": 0.0,
                        "price": 0.0,
                        "profit": 1000.0,
                        "commission": 0.0,
                        "swap": 0.0,
                        "fee": 0.0,
                        "comment": "Initial deposit",
                    },
                    # Bot trade (magic 202611): BUY win +15.0 gross, -0.35 com
                    {
                        "ticket": 2,
                        "order": 200,
                        "position_id": 5001,
                        "time": t_open,
                        "side": "BUY",
                        "entry_kind": "IN",
                        "reason": "EXPERT",
                        "magic": 202611,
                        "symbol": "EURUSDm",
                        "volume": 0.05,
                        "price": 1.09500,
                        "profit": 0.0,
                        "commission": -0.35,
                        "swap": 0.0,
                        "fee": 0.0,
                        "comment": "M5KernelPullback",
                    },
                    {
                        "ticket": 3,
                        "order": 201,
                        "position_id": 5001,
                        "time": t_close,
                        "side": "SELL",
                        "entry_kind": "OUT",
                        "reason": "TP",
                        "magic": 202611,
                        "symbol": "EURUSDm",
                        "volume": 0.05,
                        "price": 1.09800,
                        "profit": 15.0,
                        "commission": 0.0,
                        "swap": -0.15,
                        "fee": 0.0,
                        "comment": "[tp 1.09800]",
                    },
                    # Manual trade (magic 0): LOSS -5.0
                    {
                        "ticket": 4,
                        "order": 202,
                        "position_id": 5002,
                        "time": t_man_open,
                        "side": "SELL",
                        "entry_kind": "IN",
                        "reason": "CLIENT",
                        "magic": 0,
                        "symbol": "EURUSDm",
                        "volume": 0.02,
                        "price": 1.09820,
                        "profit": 0.0,
                        "commission": 0.0,
                        "swap": 0.0,
                        "fee": 0.0,
                        "comment": "manual",
                    },
                    {
                        "ticket": 5,
                        "order": 203,
                        "position_id": 5002,
                        "time": t_man_close,
                        "side": "BUY",
                        "entry_kind": "OUT",
                        "reason": "SL",
                        "magic": 0,
                        "symbol": "EURUSDm",
                        "volume": 0.02,
                        "price": 1.10070,
                        "profit": -5.0,
                        "commission": 0.0,
                        "swap": 0.0,
                        "fee": 0.0,
                        "comment": "[sl 1.10070]",
                    },
                ]

        with tempfile.TemporaryDirectory() as tmp:
            svc = build_operation_service(
                root_dir=tmp,
                gateway=OrdersFakeGateway(),
                config=BotConfig(symbol="EURUSD", dry_run=True, magic_number=202611),
                calendar_service=FakeCalendar(),
                enable_file_log=True,
            )
            svc._append_journal_entry(
                {
                    "event_type": "ORDER",
                    "status": "dry_run_ok",
                    "dry_run": True,
                    "cycle": 1,
                    "time": int(datetime(2026, 11, 25, 10, 5, tzinfo=timezone.utc).timestamp()),
                    "symbol": "EURUSDm",
                    "timeframe": "M5",
                    "strategy_name": "M5KernelPullback",
                    "side": "BUY",
                    "entry_price": 1.095,
                    "lot": 0.05,
                    "message": "Dry-run verified",
                }
            )
            app = create_app(svc)

            st_all, res_all = asyncio.run(
                _call_asgi(
                    app,
                    "GET",
                    "/api/orders/history",
                    query="start_date=2026-11-25&end_date=2026-11-25&tz_offset_minutes=0",
                )
            )
            self.assertEqual(st_all, 200)
            self.assertTrue(res_all["ok"])
            self.assertEqual(res_all["summary"]["closed_count"], 2)
            self.assertEqual(res_all["summary"]["wins"], 1)
            self.assertEqual(res_all["summary"]["losses"], 1)
            self.assertEqual(res_all["summary"]["bot_trades_count"], 1)
            self.assertEqual(res_all["summary"]["manual_trades_count"], 1)
            self.assertAlmostEqual(res_all["summary"]["net_realized_pnl"], 9.5, places=2)
            self.assertAlmostEqual(res_all["summary"]["deposits_net"], 1000.0, places=2)
            self.assertEqual(len(res_all["open_positions"]), 1)
            self.assertEqual(len(res_all["forward_journal"]), 1)

            # magic_only=1 filters closed and open positions cleanly
            st_bot, res_bot = asyncio.run(
                _call_asgi(
                    app,
                    "GET",
                    "/api/orders/history",
                    query="start_date=2026-11-25&end_date=2026-11-25&tz_offset_minutes=0&magic_only=1",
                )
            )
            self.assertEqual(st_bot, 200)
            self.assertEqual(res_bot["summary"]["closed_count"], 1)
            self.assertEqual(res_bot["closed_trades"][0]["close_reason"], "TP")
            self.assertAlmostEqual(res_bot["closed_trades"][0]["net_pnl"], 14.5, places=2)

            # Verify JSONL persistence across service restart
            reloaded_svc = build_operation_service(
                root_dir=tmp,
                gateway=OrdersFakeGateway(),
                config=BotConfig(symbol="EURUSD", dry_run=True, magic_number=202611),
                calendar_service=FakeCalendar(),
                enable_file_log=True,
            )
            self.assertEqual(len(reloaded_svc._forward_journal), 1)


if __name__ == "__main__":
    unittest.main()
