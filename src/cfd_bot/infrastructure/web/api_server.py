from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from cfd_bot.application.config import BotConfig
from cfd_bot.application.operation_service import OperationService
from cfd_bot.domain.ports import CalendarPort, MarketGatewayPort
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService
from cfd_bot.infrastructure.mt5_gateway import LiveMT5Gateway
from cfd_bot.infrastructure.order_executor import OrderExecutor


def build_operation_service(
    root_dir: Optional[Path | str] = None,
    gateway: Optional[MarketGatewayPort] = None,
    config: Optional[BotConfig] = None,
    calendar_service: Optional[CalendarPort] = None,
    enable_file_log: bool = True,
) -> OperationService:
    """Composition root wiring concrete MT5, OrderExecutor, and Calendar adapters into OperationService."""
    resolved_root = Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parents[4]
    return OperationService(
        root_dir=resolved_root,
        gateway=gateway,
        config=config,
        calendar_service=calendar_service,
        gateway_factory=lambda mt5_cfg: LiveMT5Gateway(mt5_cfg),
        calendar_factory=lambda prev_cfg, cache_dir, custom_path: EconomicCalendarService(
            prev_cfg,
            cache_dir=cache_dir,
            custom_events_path=custom_path,
        ),
        executor_factory=lambda gw, magic, dev, dry: OrderExecutor(
            gateway=gw,
            magic_number=magic,
            deviation_points=dev,
            dry_run=dry,
        ),
        enable_file_log=enable_file_log,
    )


def create_app(service: Optional[OperationService] = None) -> Starlette:
    svc = service or build_operation_service(root_dir=Path(__file__).resolve().parents[4])

    async def api_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "mt5_connected": svc.ensure_connected(), "bot_running": svc._bot_running})

    async def api_overview(request: Request) -> JSONResponse:
        return JSONResponse(svc.get_overview())

    async def api_watchlist(request: Request) -> JSONResponse:
        raw = request.query_params.get("symbols", "XAUUSD,EURUSD,US30,NAS100")
        symbols = [s.strip() for s in raw.split(",") if s.strip()]
        return JSONResponse(svc.inspect_watchlist(symbols))

    async def api_candles(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol")
        timeframe = request.query_params.get("timeframe")
        raw_count = request.query_params.get("count", "350")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        raw_tz = request.query_params.get("tz_offset_minutes")
        try:
            count = int(raw_count)
        except ValueError:
            count = 350
        try:
            tz_offset_minutes = int(raw_tz) if raw_tz not in (None, "") else 0
        except ValueError:
            tz_offset_minutes = 0
        res = svc.get_candles(
            symbol=symbol,
            timeframe=timeframe,
            count=count,
            start_date=start_date,
            end_date=end_date,
            tz_offset_minutes=tz_offset_minutes,
        )
        status_code = 200 if res.get("ok", True) else 400
        return JSONResponse(res, status_code=status_code)

    async def api_simulate(request: Request) -> JSONResponse:
        payload = await request.json()
        return JSONResponse(svc.simulate_trade(payload))

    async def api_calendar(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol")
        return JSONResponse(svc.get_calendar(symbol))

    async def api_add_custom_event(request: Request) -> JSONResponse:
        payload = await request.json()
        res = svc.add_custom_event(payload)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_delete_custom_event(request: Request) -> JSONResponse:
        idx = int(request.path_params["index"])
        res = svc.delete_custom_event(idx)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_get_config(request: Request) -> JSONResponse:
        return JSONResponse(svc.get_public_config())

    async def api_put_config(request: Request) -> JSONResponse:
        payload = await request.json()
        try:
            return JSONResponse(svc.update_config(payload))
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def api_bot_start(request: Request) -> JSONResponse:
        return JSONResponse(svc.start_bot())

    async def api_bot_stop(request: Request) -> JSONResponse:
        return JSONResponse(svc.stop_bot())

    async def api_bot_eval_once(request: Request) -> JSONResponse:
        return JSONResponse(svc.evaluate_once())

    async def api_close_position(request: Request) -> JSONResponse:
        ticket = int(request.path_params["ticket"])
        return JSONResponse(svc.close_position_by_ticket(ticket))

    async def api_be_position(request: Request) -> JSONResponse:
        ticket = int(request.path_params["ticket"])
        return JSONResponse(svc.breakeven_position_by_ticket(ticket))

    async def api_logs(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "120"))
        return JSONResponse({"logs": svc.get_logs(limit)})

    async def api_orders_history(request: Request) -> JSONResponse:
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        raw_tz = request.query_params.get("tz_offset_minutes")
        magic_only = request.query_params.get("magic_only", "0").strip().lower() in ("1", "true", "yes")
        symbol = request.query_params.get("symbol")
        try:
            tz_offset_minutes = int(raw_tz) if raw_tz not in (None, "") else 0
        except ValueError:
            tz_offset_minutes = 0
        res = await run_in_threadpool(
            svc.get_orders_and_history,
            start_date,
            end_date,
            tz_offset_minutes,
            magic_only,
            symbol,
        )
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_news_intelligence(request: Request) -> JSONResponse:
        symbol = request.query_params.get("symbol")
        return JSONResponse(svc.get_news_intelligence(symbol))

    async def api_save_news_actual(request: Request) -> JSONResponse:
        payload = await request.json()
        res = svc.save_news_actual(payload)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_get_strategies(request: Request) -> JSONResponse:
        return JSONResponse(svc.get_strategies_catalog())

    async def api_put_active_strategy(request: Request) -> JSONResponse:
        payload = await request.json()
        res = svc.update_active_strategy(payload)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_post_strategy_backtest(request: Request) -> JSONResponse:
        payload = await request.json()
        res = await run_in_threadpool(svc.run_strategy_backtest, payload)
        return JSONResponse(res, status_code=200 if res.get("ok") else 400)

    async def api_post_sync_defensive(request: Request) -> JSONResponse:
        return JSONResponse(svc.sync_defensive_with_strategy())

    async def api_stream(request: Request) -> StreamingResponse:
        raw = request.query_params.get("symbols", "XAUUSD,EURUSD,US30,NAS100")
        symbols = [s.strip() for s in raw.split(",") if s.strip()]
        once = request.query_params.get("once") == "1"

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    snapshot = await run_in_threadpool(svc.get_live_snapshot, symbols)
                    yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                    if once:
                        break
                    await asyncio.sleep(0.75)
            except (asyncio.CancelledError, GeneratorExit):
                return

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    routes: list[Any] = [
        Route("/api/health", api_health, methods=["GET"]),
        Route("/api/overview", api_overview, methods=["GET"]),
        Route("/api/stream", api_stream, methods=["GET"]),
        Route("/api/watchlist", api_watchlist, methods=["GET"]),
        Route("/api/candles", api_candles, methods=["GET"]),
        Route("/api/simulate", api_simulate, methods=["POST"]),
        Route("/api/calendar", api_calendar, methods=["GET"]),
        Route("/api/calendar/custom", api_add_custom_event, methods=["POST"]),
        Route("/api/calendar/custom/{index:int}", api_delete_custom_event, methods=["DELETE"]),
        Route("/api/news-intelligence", api_news_intelligence, methods=["GET"]),
        Route("/api/news-actuals", api_save_news_actual, methods=["POST"]),
        Route("/api/strategies", api_get_strategies, methods=["GET"]),
        Route("/api/strategies/active", api_put_active_strategy, methods=["PUT"]),
        Route("/api/strategies/backtest", api_post_strategy_backtest, methods=["POST"]),
        Route("/api/strategies/sync-defensive", api_post_sync_defensive, methods=["POST"]),
        Route("/api/config", api_get_config, methods=["GET"]),
        Route("/api/config", api_put_config, methods=["PUT"]),
        Route("/api/bot/start", api_bot_start, methods=["POST"]),
        Route("/api/bot/stop", api_bot_stop, methods=["POST"]),
        Route("/api/bot/evaluate-once", api_bot_eval_once, methods=["POST"]),
        Route("/api/positions/{ticket:int}/close", api_close_position, methods=["POST"]),
        Route("/api/positions/{ticket:int}/breakeven", api_be_position, methods=["POST"]),
        Route("/api/orders/history", api_orders_history, methods=["GET"]),
        Route("/api/logs", api_logs, methods=["GET"]),
    ]

    ui_dist = svc.root_dir / "ui" / "dist"
    if ui_dist.exists():
        assets_dir = ui_dist / "assets"
        if assets_dir.exists():
            routes.append(Mount("/assets", app=StaticFiles(directory=str(assets_dir)), name="assets"))

        async def spa_index(request: Request) -> FileResponse | JSONResponse:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(ui_dist / "index.html"))

        routes.append(Route("/", spa_index, methods=["GET"]))
        routes.append(Route("/{path:path}", spa_index, methods=["GET"]))

    app = Starlette(routes=routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8787"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.service = svc
    return app
