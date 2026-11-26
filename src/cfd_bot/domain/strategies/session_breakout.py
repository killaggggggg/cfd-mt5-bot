from __future__ import annotations

from typing import Optional

import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec


class SessionRangeBreakoutStrategy(BaseStrategy):
    """
    Starter CFD Intraday Strategy: Pre-Session Consolidation Range Breakout.

    Builds a high/low box during [range_start_hour_utc, range_end_hour_utc) and triggers
    on a confirmed closed-candle breakout during [trade_start_hour_utc, trade_end_hour_utc).
    """

    def __init__(
        self,
        range_start_hour_utc: int = 0,
        range_end_hour_utc: int = 6,
        trade_start_hour_utc: int = 7,
        trade_end_hour_utc: int = 17,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        tp_rr_multiplier: float = 2.0,
        breakout_buffer_atr: float = 0.10,
        min_range_atr_ratio: float = 0.5,
        max_range_atr_ratio: float = 4.0,
    ) -> None:
        self.range_start_hour_utc = int(range_start_hour_utc)
        self.range_end_hour_utc = int(range_end_hour_utc)
        self.trade_start_hour_utc = int(trade_start_hour_utc)
        self.trade_end_hour_utc = int(trade_end_hour_utc)
        self.atr_period = int(atr_period)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)
        self.breakout_buffer_atr = float(breakout_buffer_atr)
        self.min_range_atr_ratio = float(min_range_atr_ratio)
        self.max_range_atr_ratio = float(max_range_atr_ratio)

    @property
    def name(self) -> str:
        return "SessionRangeBreakout"

    @property
    def display_name_th(self) -> str:
        return "Session Breakout (ทะลุกรอบราคาช่วงพักตัว)"

    @property
    def description_th(self) -> str:
        return (
            "วัดกรอบราคา High/Low ช่วงตลาดเอเชีย แล้วเข้าเทรดเมื่อแท่งเทียนเบรกทะลุกรอบในรอบลอนดอน/นิวยอร์ก "
            "(เวลาคิดเป็น UTC; เวลาไทย = UTC + 7 ชม.)"
        )

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec("range_start_hour_utc", "ชั่วโมงเริ่มตีกรอบ (UTC)", "0 UTC = 07:00 น. เวลาไทย", "int", 0, 0, 23, 1),
            StrategyParamSpec("range_end_hour_utc", "ชั่วโมงจบตีกรอบ (UTC)", "6 UTC = 13:00 น. เวลาไทย", "int", 6, 1, 23, 1),
            StrategyParamSpec("trade_start_hour_utc", "ชั่วโมงเริ่มหาจุดเข้า (UTC)", "7 UTC = 14:00 น. เวลาไทย (เปิดยุโรป)", "int", 7, 0, 23, 1),
            StrategyParamSpec("trade_end_hour_utc", "ชั่วโมงหยุดหาจุดเข้า (UTC)", "17 UTC = 00:00 น. เวลาไทย", "int", 17, 1, 24, 1),
            StrategyParamSpec("atr_sl_multiplier", "ตัวคูณระยะ SL (x ATR)", "ตั้ง SL กว้างกี่เท่าของความผันผวน ATR", "float", 1.5, 0.5, 5.0, 0.1),
            StrategyParamSpec("tp_rr_multiplier", "เป้ากำไร R:R", "อัตราส่วนระยะ TP ต่อระยะ SL (เช่น 2.0 = 1:2)", "float", 2.0, 0.5, 8.0, 0.1),
            StrategyParamSpec("breakout_buffer_atr", "ระยะกันเบรกหลอก (x ATR)", "ต้องปิดเลยกรอบออกไปอีกกี่เท่าของ ATR", "float", 0.10, 0.0, 1.0, 0.05),
            StrategyParamSpec("min_range_atr_ratio", "กรอบแคบสุดที่ยอมรับ (x ATR)", "กรอบเล็กเกินไปอาจแกว่งมั่ว", "float", 0.5, 0.1, 3.0, 0.1),
            StrategyParamSpec("max_range_atr_ratio", "กรอบกว้างสุดที่ยอมรับ (x ATR)", "กรอบกว้างเกินไปอาจหมดแรงวิ่งแล้ว", "float", 4.0, 1.0, 12.0, 0.5),
        ]

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates.empty or len(rates) < max(self.atr_period + 5, 30):
            return None

        if pd.api.types.is_datetime64_any_dtype(rates["time"]):
            df = rates
        else:
            df = rates.copy()
            df["time"] = pd.to_datetime(df["time"], utc=True)

        # Use closed bars only (drop currently forming bar at iloc[-1])
        closed = df.iloc[:-1].copy()
        if len(closed) < self.atr_period + 2:
            return None

        atr = self._compute_atr(closed, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        last_bar = closed.iloc[-1]
        bar_time = last_bar["time"]
        if not (self.trade_start_hour_utc <= bar_time.hour < self.trade_end_hour_utc):
            return None

        # Pre-session box for the same UTC trading day
        same_day = closed[closed["time"].dt.date == bar_time.date()]
        box_bars = same_day[
            (same_day["time"].dt.hour >= self.range_start_hour_utc)
            & (same_day["time"].dt.hour < self.range_end_hour_utc)
        ]
        if box_bars.empty:
            return None

        range_high = float(box_bars["high"].max())
        range_low = float(box_bars["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            return None

        range_atr_ratio = range_width / atr
        if range_atr_ratio < self.min_range_atr_ratio or range_atr_ratio > self.max_range_atr_ratio:
            return None

        close_price = float(last_bar["close"])
        prev_close = float(closed.iloc[-2]["close"])
        buffer_dist = self.breakout_buffer_atr * atr

        # Trigger on fresh breakout cross of the range boundary
        bullish_breakout = close_price > (range_high + buffer_dist) and prev_close <= (range_high + buffer_dist)
        bearish_breakout = close_price < (range_low - buffer_dist) and prev_close >= (range_low - buffer_dist)

        if bullish_breakout:
            entry = spec.ask if spec.ask > 0 else close_price
            sl_dist = max(self.atr_sl_multiplier * atr, abs(entry - range_low) * 0.5)
            sl_price = round(entry - sl_dist, spec.digits)
            tp_price = round(entry + sl_dist * self.tp_rr_multiplier, spec.digits)
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=0.0,
                atr=atr,
                reason=f"Bullish breakout above pre-session high {range_high:.{spec.digits}f}",
                metadata={"range_high": range_high, "range_low": range_low, "atr": atr},
            )

        if bearish_breakout:
            entry = spec.bid if spec.bid > 0 else close_price
            sl_dist = max(self.atr_sl_multiplier * atr, abs(range_high - entry) * 0.5)
            sl_price = round(entry + sl_dist, spec.digits)
            tp_price = round(entry - sl_dist * self.tp_rr_multiplier, spec.digits)
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.SELL,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=0.0,
                atr=atr,
                reason=f"Bearish breakout below pre-session low {range_low:.{spec.digits}f}",
                metadata={"range_high": range_high, "range_low": range_low, "atr": atr},
            )

        return None

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int) -> Optional[float]:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        prev_close = df["close"].astype(float).shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(window=period).mean()
        val = atr_series.iloc[-1]
        if pd.isna(val):
            return None
        return float(val)
