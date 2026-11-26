from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import MetaTrader5 as mt5
import pandas as pd

from cfd_bot.application.config import MT5ConnectionConfig
from cfd_bot.domain.models import AccountState, OpenPosition, OrderSide, SymbolSpec
from cfd_bot.domain.ports import MarketGatewayPort


TIMEFRAME_MAP: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("XAUUSD", "GOLD"),
    "GOLD": ("GOLD", "XAUUSD"),
    "XAGUSD": ("XAGUSD", "SILVER"),
    "US30": ("US30", "DJ30", "WS30", "WALLST30", "DJI30"),
    "NAS100": ("NAS100", "USTEC", "US100", "NDX", "NQ100"),
    "USTEC": ("USTEC", "NAS100", "US100", "NDX"),
    "SPX500": ("SPX500", "US500", "SP500"),
    "US500": ("US500", "SPX500", "SP500"),
    "GER40": ("GER40", "DE40", "DAX40", "DE30"),
    "USOIL": ("USOIL", "WTI", "XTIUSD", "CL"),
    "UKOIL": ("UKOIL", "BRENT", "XBRUSD"),
}


MT5GatewayProtocol = MarketGatewayPort


class LiveMT5Gateway:
    """Broker-agnostic wrapper over MetaTrader5 with symbol/timezone/commission auto-detection."""

    COMMON_SUFFIXES = ("", "m", "c", ".a", ".r", ".i", ".pro", ".raw", ".std", ".ecn", ".cash", "_", "#")

    def __init__(self, conn: Optional[MT5ConnectionConfig] = None) -> None:
        self._conn = conn or MT5ConnectionConfig()
        self._resolved_cache: dict[str, str] = {}
        self._commission_cache: dict[str, Optional[float]] = {}

    def connect(self) -> bool:
        params: dict[str, Any] = {}
        if self._conn.path:
            params["path"] = self._conn.path
        if self._conn.login and self._conn.password and self._conn.server:
            params["login"] = self._conn.login
            params["password"] = self._conn.password
            params["server"] = self._conn.server

        if not mt5.initialize(**params):
            mt5.shutdown()
            if not mt5.initialize():
                return False

        return mt5.account_info() is not None

    def disconnect(self) -> None:
        mt5.shutdown()

    def resolve_symbol(self, symbol: str) -> str:
        cached = self._resolved_cache.get(symbol)
        if cached and mt5.symbol_select(cached, True):
            return cached

        upper = symbol.strip().upper()
        bases = SYMBOL_ALIASES.get(upper, (symbol.strip(),))

        for base in bases:
            for suffix in self.COMMON_SUFFIXES:
                for candidate in (f"{base}{suffix}", f"{base.upper()}{suffix}", f"{base.lower()}{suffix}"):
                    if mt5.symbol_select(candidate, True):
                        self._resolved_cache[symbol] = candidate
                        return candidate

        # Fallback: scan full broker symbol catalog for prefixed/aliased CFD names (e.g. XAUUSD.lmx, US30Cash)
        all_syms = mt5.symbols_get()
        if all_syms:
            candidates = []
            for sym_obj in all_syms:
                name_up = sym_obj.name.upper()
                if any(name_up.startswith(base.upper()) for base in bases):
                    # Prefer full-tradeable symbols and shorter names (avoid exotic forwards if spot exists)
                    trade_penalty = 0 if getattr(sym_obj, "trade_mode", 0) == mt5.SYMBOL_TRADE_MODE_FULL else 1
                    candidates.append((trade_penalty, len(sym_obj.name), sym_obj.name))
            if candidates:
                candidates.sort()
                best = candidates[0][2]
                if mt5.symbol_select(best, True):
                    self._resolved_cache[symbol] = best
                    return best

        return symbol

    def get_account_state(self) -> Optional[AccountState]:
        info = mt5.account_info()
        if info is None:
            return None
        return AccountState(
            login=info.login,
            server=info.server,
            currency=info.currency,
            currency_digits=info.currency_digits,
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            margin_free=float(info.margin_free),
            margin_level=float(info.margin_level),
            leverage=int(info.leverage),
            margin_so_call=float(info.margin_so_call),
            margin_so_so=float(info.margin_so_so),
            margin_so_mode=int(info.margin_so_mode),
            trade_allowed=bool(info.trade_allowed),
            trade_expert=bool(info.trade_expert),
        )

    def get_symbol_spec(self, symbol: str) -> Optional[SymbolSpec]:
        actual = self.resolve_symbol(symbol)
        info = mt5.symbol_info(actual)
        if info is None:
            return None

        tick = mt5.symbol_info_tick(actual)
        ask = float(tick.ask) if tick and tick.ask > 0 else float(info.ask)
        bid = float(tick.bid) if tick and tick.bid > 0 else float(info.bid)

        # Newly subscribed symbols in Market Watch can take a brief moment before first tick arrives
        if ask <= 0.0 or bid <= 0.0:
            for _ in range(10):
                time.sleep(0.1)
                tick = mt5.symbol_info_tick(actual)
                info_retry = mt5.symbol_info(actual)
                if info_retry is not None:
                    info = info_retry
                ask = float(tick.ask) if tick and tick.ask > 0 else float(info.ask)
                bid = float(tick.bid) if tick and tick.bid > 0 else float(info.bid)
                if ask > 0.0 and bid > 0.0:
                    break

        raw_time = int(tick.time) if tick and tick.time > 0 else int(info.time)
        quote_dt = datetime.fromtimestamp(raw_time, tz=timezone.utc) if raw_time > 0 else None
        server_offset = self._infer_server_utc_offset_seconds(raw_time)
        detected_comm = self._detect_commission_per_lot(actual)

        live_spread = (
            int(round((ask - bid) / float(info.point)))
            if (ask > 0.0 and bid > 0.0 and info.point > 0.0)
            else int(info.spread)
        )

        return SymbolSpec(
            name=info.name,
            digits=int(info.digits),
            point=float(info.point),
            ask=ask,
            bid=bid,
            spread_points=live_spread,
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            trade_contract_size=float(info.trade_contract_size),
            trade_tick_size=float(info.trade_tick_size),
            trade_tick_value=float(info.trade_tick_value),
            trade_tick_value_profit=float(info.trade_tick_value_profit),
            trade_tick_value_loss=float(info.trade_tick_value_loss),
            trade_calc_mode=int(info.trade_calc_mode),
            trade_mode=int(info.trade_mode),
            filling_mode=int(info.filling_mode),
            trade_stops_level=int(info.trade_stops_level),
            trade_freeze_level=int(info.trade_freeze_level),
            volume_limit=float(info.volume_limit),
            margin_initial=float(info.margin_initial),
            margin_maintenance=float(info.margin_maintenance),
            swap_long=float(info.swap_long),
            swap_short=float(info.swap_short),
            swap_mode=int(info.swap_mode),
            swap_rollover3days=int(info.swap_rollover3days),
            currency_base=info.currency_base,
            currency_margin=info.currency_margin,
            currency_profit=info.currency_profit,
            quote_time=quote_dt,
            server_utc_offset_seconds=server_offset,
            detected_commission_per_lot=detected_comm,
        )

    @staticmethod
    def _infer_server_utc_offset_seconds(raw_server_tick_ts: int) -> Optional[int]:
        """
        Infers broker server UTC offset (e.g. +2h/+3h EET or 0h UTC) when quote is live.

        Only applies within +/-14h of UTC so stale weekend quotes are never mistaken for a timezone.
        """
        if raw_server_tick_ts <= 0:
            return None
        utc_now_ts = datetime.now(timezone.utc).timestamp()
        delta = raw_server_tick_ts - utc_now_ts
        if abs(delta) > 14 * 3600:
            return None
        return int(round(delta / 1800.0) * 1800)

    def _detect_commission_per_lot(self, symbol: str) -> Optional[float]:
        """
        Estimates round-trip commission per 1.0 lot from recent deal history for this symbol.
        """
        if symbol in self._commission_cache:
            return self._commission_cache[symbol]

        utc_now = datetime.now(timezone.utc)
        start = utc_now - timedelta(days=90)
        deals = mt5.history_deals_get(start, utc_now)
        if not deals:
            self._commission_cache[symbol] = None
            return None

        by_position: dict[int, dict[str, float]] = {}
        fallback_oneway: Optional[float] = None

        for deal in deals:
            if deal.symbol != symbol:
                continue
            if deal.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
                continue
            comm = abs(float(deal.commission)) + abs(float(deal.fee))
            vol = float(deal.volume)
            if vol <= 0:
                continue

            if deal.entry == mt5.DEAL_ENTRY_IN and fallback_oneway is None:
                fallback_oneway = (comm / vol) * 2.0

            pos_id = int(deal.position_id)
            bucket = by_position.setdefault(pos_id, {"comm": 0.0, "in_vol": 0.0, "has_in": 0.0, "has_out": 0.0})
            bucket["comm"] += comm
            if deal.entry == mt5.DEAL_ENTRY_IN:
                bucket["in_vol"] += vol
                bucket["has_in"] = 1.0
            elif deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
                bucket["has_out"] = 1.0

        # Prefer closed round-trip position (covers entry-only, exit-only, and split commissions)
        for pos_id in reversed(list(by_position.keys())):
            bucket = by_position[pos_id]
            if bucket["has_in"] > 0 and bucket["has_out"] > 0 and bucket["in_vol"] > 0:
                est = bucket["comm"] / bucket["in_vol"]
                self._commission_cache[symbol] = est
                return est

        self._commission_cache[symbol] = fallback_oneway
        return fallback_oneway

    def calc_margin(self, side: OrderSide, symbol: str, volume: float, price: float) -> Optional[float]:
        action = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        margin = mt5.order_calc_margin(action, symbol, volume, price)
        return float(margin) if margin is not None else None

    def calc_profit(
        self, side: OrderSide, symbol: str, volume: float, price_open: float, price_close: float
    ) -> Optional[float]:
        action = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        profit = mt5.order_calc_profit(action, symbol, volume, price_open, price_close)
        return float(profit) if profit is not None else None

    def get_open_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> list[OpenPosition]:
        raw_positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not raw_positions:
            return []

        result: list[OpenPosition] = []
        for pos in raw_positions:
            if magic is not None and pos.magic != magic:
                continue
            side = OrderSide.BUY if pos.type == mt5.ORDER_TYPE_BUY else OrderSide.SELL
            result.append(
                OpenPosition(
                    ticket=int(pos.ticket),
                    symbol=pos.symbol,
                    side=side,
                    volume=float(pos.volume),
                    price_open=float(pos.price_open),
                    price_current=float(pos.price_current),
                    sl=float(pos.sl),
                    tp=float(pos.tp),
                    profit=float(pos.profit),
                    swap=float(pos.swap),
                    magic=int(pos.magic),
                    open_time=int(getattr(pos, "time", 0) or 0) or None,
                )
            )
        return result

    def get_deal_history(self, date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
        utc_from = date_from.replace(tzinfo=timezone.utc) if date_from.tzinfo is None else date_from.astimezone(timezone.utc)
        utc_to = date_to.replace(tzinfo=timezone.utc) if date_to.tzinfo is None else date_to.astimezone(timezone.utc)
        raw_deals = mt5.history_deals_get(utc_from, utc_to)
        if not raw_deals:
            return []

        type_map = {
            mt5.DEAL_TYPE_BUY: "BUY",
            mt5.DEAL_TYPE_SELL: "SELL",
            mt5.DEAL_TYPE_BALANCE: "BALANCE",
            mt5.DEAL_TYPE_CREDIT: "CREDIT",
        }
        entry_map = {
            mt5.DEAL_ENTRY_IN: "IN",
            mt5.DEAL_ENTRY_OUT: "OUT",
            mt5.DEAL_ENTRY_INOUT: "INOUT",
            mt5.DEAL_ENTRY_OUT_BY: "OUT_BY",
        }
        reason_map = {
            mt5.DEAL_REASON_TP: "TP",
            mt5.DEAL_REASON_SL: "SL",
            mt5.DEAL_REASON_SO: "STOP_OUT",
            mt5.DEAL_REASON_EXPERT: "EXPERT",
            mt5.DEAL_REASON_CLIENT: "CLIENT",
            mt5.DEAL_REASON_MOBILE: "MOBILE",
            mt5.DEAL_REASON_WEB: "WEB",
            mt5.DEAL_REASON_ROLLOVER: "ROLLOVER",
        }

        items: list[dict[str, Any]] = []
        for deal in raw_deals:
            items.append(
                {
                    "ticket": int(deal.ticket),
                    "order": int(deal.order),
                    "position_id": int(deal.position_id),
                    "time": int(deal.time),
                    "side": type_map.get(int(deal.type), "OTHER"),
                    "entry_kind": entry_map.get(int(deal.entry), "OTHER"),
                    "reason": reason_map.get(int(getattr(deal, "reason", -1)), "OTHER"),
                    "magic": int(deal.magic),
                    "symbol": str(deal.symbol or ""),
                    "volume": float(deal.volume),
                    "price": float(deal.price),
                    "profit": float(deal.profit),
                    "commission": float(deal.commission),
                    "swap": float(deal.swap),
                    "fee": float(getattr(deal, "fee", 0.0) or 0.0),
                    "comment": str(getattr(deal, "comment", "") or ""),
                }
            )
        return items

    def get_rates(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        tf_code = TIMEFRAME_MAP.get(timeframe.upper())
        if tf_code is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        actual = self.resolve_symbol(symbol)
        rates = mt5.copy_rates_from_pos(actual, tf_code, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    @staticmethod
    def _range_chunk_days(timeframe: str) -> int:
        tf = timeframe.upper()
        if tf in ("M1", "M2"):
            return 14
        if tf in ("M3", "M4"):
            return 35
        if tf in ("M5", "M6", "M10", "M12"):
            return 60
        if tf in ("M15", "M20", "M30"):
            return 180
        return 365

    def _copy_range_with_split(
        self, symbol: str, tf_code: int, cur_from: datetime, cur_to: datetime, depth: int = 0
    ) -> list[Any]:
        """Fetches MT5 rates over [cur_from, cur_to], recursively halving if MT5 rejects oversized range."""
        if cur_from > cur_to:
            return []
        rates = mt5.copy_rates_range(symbol, tf_code, cur_from, cur_to)
        if rates is not None:
            return [rates] if len(rates) > 0 else []

        span_seconds = (cur_to - cur_from).total_seconds()
        if depth >= 6 or span_seconds <= 3600:
            return []

        mid = cur_from + timedelta(seconds=span_seconds / 2.0)
        left = self._copy_range_with_split(symbol, tf_code, cur_from, mid, depth + 1)
        right = self._copy_range_with_split(symbol, tf_code, mid + timedelta(seconds=1), cur_to, depth + 1)
        return left + right

    def get_rates_range(
        self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime
    ) -> pd.DataFrame:
        tf_code = TIMEFRAME_MAP.get(timeframe.upper())
        if tf_code is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        utc_from = date_from.replace(tzinfo=timezone.utc) if date_from.tzinfo is None else date_from.astimezone(timezone.utc)
        utc_to = date_to.replace(tzinfo=timezone.utc) if date_to.tzinfo is None else date_to.astimezone(timezone.utc)
        if utc_from > utc_to:
            return pd.DataFrame()

        actual = self.resolve_symbol(symbol)
        chunk_delta = timedelta(days=self._range_chunk_days(timeframe))
        chunks: list[Any] = []
        cursor = utc_from
        while cursor <= utc_to:
            chunk_end = min(utc_to, cursor + chunk_delta)
            chunks.extend(self._copy_range_with_split(actual, tf_code, cursor, chunk_end))
            cursor = chunk_end + timedelta(seconds=1)

        if not chunks:
            return pd.DataFrame()

        df = pd.concat([pd.DataFrame(c) for c in chunks], ignore_index=True)
        if df.empty:
            return df
        df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def order_check(self, request: dict[str, Any]) -> dict[str, Any]:
        res = mt5.order_check(request)
        if res is None:
            return {"retcode": -1, "comment": str(mt5.last_error())}
        return {
            "retcode": int(res.retcode),
            "balance": float(res.balance),
            "equity": float(res.equity),
            "margin": float(res.margin),
            "margin_free": float(res.margin_free),
            "margin_level": float(res.margin_level),
            "comment": res.comment,
        }

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        res = mt5.order_send(request)
        if res is None:
            return {"retcode": -1, "comment": str(mt5.last_error())}
        return {
            "retcode": int(res.retcode),
            "order": int(res.order),
            "deal": int(res.deal),
            "volume": float(res.volume),
            "price": float(res.price),
            "comment": res.comment,
        }
