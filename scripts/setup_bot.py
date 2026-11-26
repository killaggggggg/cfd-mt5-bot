"""
Interactive / CLI Quick-Setup Wizard for CFD MT5 Bot.

Connects to the currently open MT5 terminal, inspects the broker's real specs
for your chosen symbol, checks minimum-lot compatibility against your equity,
and writes a ready-to-run .env file (defaulting BOT_DRY_RUN=true for safety).

Usage:
    python scripts/setup_bot.py
    python scripts/setup_bot.py --symbol XAUUSD --timeframe M15 --lot-mode dynamic_risk --risk 1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cfd_bot.application.config import MT5ConnectionConfig, PreventiveConfig
from cfd_bot.domain.models import LotMode, OrderSide
from cfd_bot.domain.risk_engine import estimate_notional_per_lot
from cfd_bot.infrastructure.calendar_service import EconomicCalendarService
from cfd_bot.infrastructure.mt5_gateway import LiveMT5Gateway


def _prompt(text: str, default: str) -> str:
    raw = input(f"{text} [{default}]: ").strip()
    return raw if raw else default


def run_setup(
    symbol: Optional[str],
    timeframe: Optional[str],
    lot_mode: Optional[str],
    risk_pct: Optional[float],
    fixed_lot: Optional[float],
    max_sym_lev: Optional[float],
    commission: Optional[float],
    live: bool,
    non_interactive: bool,
) -> int:
    gw = LiveMT5Gateway(MT5ConnectionConfig())
    if not gw.connect():
        print("ERROR: Could not connect to MetaTrader 5. Please open MT5 and log in first.")
        return 1

    try:
        account = gw.get_account_state()
        if account is None:
            print("ERROR: Unable to read MT5 account state.")
            return 1

        print("=" * 72)
        print(
            f"Connected MT5 Account #{account.login} ({account.server})\n"
            f"Equity: {account.equity:,.2f} {account.currency} | Account Leverage: 1:{account.leverage}"
        )
        print("=" * 72)

        if not non_interactive:
            symbol = symbol or _prompt("Target symbol (e.g. XAUUSD, EURUSD, US30, NAS100)", "XAUUSD")
            timeframe = timeframe or _prompt("Timeframe (M5, M15, H1)", "M15")
            lot_mode = lot_mode or _prompt("Lot mode (dynamic_risk / fixed / dynamic_equity_step)", "dynamic_risk")
            if lot_mode == LotMode.FIXED.value:
                fixed_lot = fixed_lot if fixed_lot is not None else float(_prompt("Fixed lot size", "0.01"))
                risk_pct = risk_pct if risk_pct is not None else 1.0
            else:
                risk_pct = risk_pct if risk_pct is not None else float(_prompt("Risk % per trade", "1.0"))
                fixed_lot = fixed_lot if fixed_lot is not None else 0.01
            max_sym_lev = max_sym_lev if max_sym_lev is not None else float(_prompt("Max effective symbol leverage cap", "15.0"))
        else:
            symbol = symbol or "XAUUSD"
            timeframe = timeframe or "M15"
            lot_mode = lot_mode or "dynamic_risk"
            risk_pct = risk_pct if risk_pct is not None else 1.0
            fixed_lot = fixed_lot if fixed_lot is not None else 0.01
            max_sym_lev = max_sym_lev if max_sym_lev is not None else 15.0

        spec = gw.get_symbol_spec(symbol)
        if spec is None:
            print(f"ERROR: Symbol '{symbol}' was not found on {account.server}.")
            return 1

        margin_1lot = gw.calc_margin(OrderSide.BUY, spec.name, 1.0, spec.ask) or 0.0
        notional_1lot = estimate_notional_per_lot(spec, spec.ask)
        sym_lev = (notional_1lot / margin_1lot) if margin_1lot > 0 else float(account.leverage)
        offset_h = (spec.server_utc_offset_seconds / 3600.0) if spec.server_utc_offset_seconds is not None else 0.0

        if commission is not None:
            comm_val = commission
            comm_src = "user specified"
        elif spec.detected_commission_per_lot is not None:
            comm_val = round(spec.detected_commission_per_lot, 2)
            comm_src = "auto-detected from deal history"
        else:
            comm_val = 7.0
            comm_src = "default (no deal history yet; set 0 for Standard accounts)"

        print(f"\nResolved Symbol : {spec.name} (digits={spec.digits}, point={spec.point})")
        print(f"Live Quote      : Bid={spec.bid:.{spec.digits}f} Ask={spec.ask:.{spec.digits}f} Spread={spec.spread_points} pts")
        print(f"Volume Limits   : min={spec.volume_min} step={spec.volume_step} max={spec.volume_max}")
        print(f"Symbol Leverage : 1:{sym_lev:.1f} (Account 1:{account.leverage}, Margin/1.0 lot=${margin_1lot:,.2f})")
        print(f"Server Timezone : UTC{offset_h:+.1f}h")
        print(f"Commission      : ${comm_val:.2f}/lot round-trip ({comm_src})")

        cal_service = EconomicCalendarService(PreventiveConfig(), cache_dir=ROOT / "data" / "cache")
        from datetime import datetime, timezone
        cal_res = cal_service.evaluate(spec=spec, now=datetime.now(timezone.utc), expected_hold_days=0.0)
        if cal_res.reasons or cal_res.warnings:
            print(f"Calendar Alerts : {cal_res.reasons + cal_res.warnings}")
        if cal_res.upcoming_events:
            print(
                "Upcoming News   : "
                + ", ".join(f"{e.currency}:{e.title}({e.impact.value})" for e in cal_res.upcoming_events[:4])
            )

        # Compatibility sanity check on min lot vs 1% equity risk
        min_margin = margin_1lot * spec.volume_min
        if min_margin > account.equity * 0.25:
            print(
                f"\n[WARNING] Minimum lot ({spec.volume_min}) requires ${min_margin:,.2f} margin, "
                f"which exceeds 25% of your ${account.equity:,.2f} equity!"
            )

        env_content = (
            f"# Auto-generated by setup_bot.py for {account.server} (#{account.login})\n"
            f"BOT_SYMBOL={spec.name}\n"
            f"BOT_TIMEFRAME={timeframe.upper()}\n"
            f"BOT_MAGIC_NUMBER=202611\n"
            f"BOT_DRY_RUN={'false' if live else 'true'}\n"
            f"BOT_POLL_SECONDS=5\n\n"
            f"# Lot Sizing: fixed | dynamic_risk | dynamic_equity_step\n"
            f"LOT_MODE={lot_mode}\n"
            f"FIXED_LOT={fixed_lot}\n"
            f"RISK_PERCENT={risk_pct}\n"
            f"USE_EQUITY_FOR_RISK=true\n"
            f"EQUITY_STEP_AMOUNT=1000.0\n"
            f"EQUITY_STEP_LOT=0.01\n"
            f"MAX_LOT_CAP=1.00\n"
            f"MIN_SL_POINTS=50\n\n"
            f"# Broker Leverage & Margin Safety\n"
            f"MAX_EFFECTIVE_ACCOUNT_LEVERAGE={max(max_sym_lev, 20.0)}\n"
            f"MAX_EFFECTIVE_SYMBOL_LEVERAGE={max_sym_lev}\n"
            f"MAX_MARGIN_USAGE_PCT=25.0\n"
            f"MIN_MARGIN_LEVEL_PCT=500.0\n"
            f"SCALE_DOWN_LOT_FOR_LEVERAGE=true\n\n"
            f"# Preventive Guards (MAX_SPREAD_POINTS=0 uses scale-invariant Spread/TP & Spread/ATR)\n"
            f"MAX_SPREAD_POINTS=0.0\n"
            f"MAX_SPREAD_TO_TP_RATIO=0.20\n"
            f"MAX_SPREAD_TO_ATR_RATIO=0.35\n"
            f"COMMISSION_PER_LOT_ROUNDTRIP={comm_val}\n"
            f"AUTO_DETECT_COMMISSION=true\n"
            f"ESTIMATED_SLIPPAGE_POINTS=5.0\n"
            f"MIN_NET_RR=1.20\n"
            f"MAX_SWAP_TO_TP_RATIO=0.15\n"
            f"SWAP_ROLLOVER_HOUR_SERVER=0\n"
            f"ROLLOVER_BLACKOUT_MINUTES_BEFORE=20\n"
            f"ROLLOVER_BLACKOUT_MINUTES_AFTER=15\n"
            f"ENFORCE_ROLLOVER_BLACKOUT_FOR_INTRADAY=true\n"
            f"MAX_DAILY_LOSS_PCT=3.0\n"
            f"MAX_OPEN_POSITIONS_SYMBOL=1\n"
            f"MAX_OPEN_POSITIONS_TOTAL=3\n"
            f"MAX_QUOTE_AGE_SECONDS=30\n\n"
            f"# Economic News & Bank Holiday Guard\n"
            f"ENABLE_NEWS_GUARD=true\n"
            f"ENABLE_HOLIDAY_GUARD=true\n"
            f"HOLIDAY_POLICY=block\n"
            f"NEWS_BLOCK_MINUTES_BEFORE=30\n"
            f"NEWS_BLOCK_MINUTES_AFTER=30\n"
            f"BLOCK_MEDIUM_IMPACT_NEWS=false\n"
            f"NEWS_WARN_HOURS=12\n"
            f"FAIL_CLOSED_ON_CALENDAR_ERROR=false\n"
            f"CUSTOM_EVENTS_PATH=config/custom_events.json\n\n"
            f"# Active Trade Management (True Break-Even & ATR Trailing Stop)\n"
            f"ENABLE_AUTO_BREAKEVEN=true\n"
            f"BREAKEVEN_TRIGGER_R=1.0\n"
            f"BREAKEVEN_LOCK_POINTS=5\n"
            f"ENABLE_TRAILING_STOP=true\n"
            f"TRAILING_ATR_MULT=1.5\n"
            f"TRAILING_STEP_POINTS=10\n"
        )

        env_path = ROOT / ".env"
        env_path.write_text(env_content, encoding="utf-8")
        print(f"\nSaved configuration to: {env_path}")
        print(f"Dry-Run mode: {'OFF (LIVE ORDERS)' if live else 'ON (safe simulation)'}")
        print("Next step: run 'python scripts/run_bot.py'")
    finally:
        gw.disconnect()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure .env automatically from live MT5 broker specs.")
    parser.add_argument("--symbol", default=None, help="Target symbol (e.g. XAUUSD, EURUSD, US30)")
    parser.add_argument("--timeframe", default=None, help="Timeframe (e.g. M15, H1)")
    parser.add_argument("--lot-mode", choices=["fixed", "dynamic_risk", "dynamic_equity_step"], default=None)
    parser.add_argument("--risk", type=float, default=None, help="Risk percent per trade")
    parser.add_argument("--fixed-lot", type=float, default=None, help="Fixed lot size")
    parser.add_argument("--max-symbol-leverage", type=float, default=None, help="Max effective symbol leverage")
    parser.add_argument("--commission", type=float, default=None, help="Override round-trip commission per lot")
    parser.add_argument("--live", action="store_true", help="Disable dry-run (default is safe DRY_RUN=true)")
    parser.add_argument("--yes", action="store_true", help="Non-interactive mode using flags/defaults")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        run_setup(
            symbol=args.symbol,
            timeframe=args.timeframe,
            lot_mode=args.lot_mode,
            risk_pct=args.risk,
            fixed_lot=args.fixed_lot,
            max_sym_lev=args.max_symbol_leverage,
            commission=args.commission,
            live=args.live,
            non_interactive=args.yes,
        )
    )
