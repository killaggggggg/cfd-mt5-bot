"""CLI entrypoint (Composition Root) to run the CFD MT5 bot."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.bot_runner import BotRunner, configure_logging
from cfd_bot.application.config import load_config_from_env
from cfd_bot.domain.strategies import build_strategy
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService
from cfd_bot.infrastructure.mt5_gateway import LiveMT5Gateway
from cfd_bot.infrastructure.order_executor import OrderExecutor


def main() -> None:
    configure_logging(ROOT / "logs")
    cfg = load_config_from_env()
    gateway = LiveMT5Gateway(cfg.mt5)
    strategy = build_strategy(cfg.strategy.active_strategy, cfg.strategy.params)
    custom_path = Path(cfg.preventive.custom_events_path) if cfg.preventive.custom_events_path else None
    if custom_path is not None and not custom_path.is_absolute():
        custom_path = ROOT / custom_path
    calendar = EconomicCalendarService(
        cfg.preventive,
        cache_dir=ROOT / "data" / "cache",
        custom_events_path=custom_path,
    )
    executor = OrderExecutor(
        gateway=gateway,
        magic_number=cfg.magic_number,
        deviation_points=cfg.deviation_points,
        dry_run=cfg.dry_run,
    )
    runner = BotRunner(
        config=cfg,
        gateway=gateway,
        strategy=strategy,
        executor=executor,
        calendar_service=calendar,
    )
    runner.run_forever()


if __name__ == "__main__":
    main()
