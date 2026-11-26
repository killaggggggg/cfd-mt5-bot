from __future__ import annotations

from typing import Optional

import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec


class RsiMeanReversionStrategy(BaseStrategy):
    """
    Pure Offensive Counter-Trend Strategy: RSI Extremes Mean Reversion.

    Enters BUY when Wilder RSI crosses back upward out of oversold territory,
    and SELL when RSI crosses back downward out of overbought territory.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        oversold_level: float = 30.0,
        overbought_level: float = 70.0,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        tp_rr_multiplier: float = 1.8,
    ) -> None:
        self.rsi_period = int(rsi_period)
        self.oversold_level = float(oversold_level)
        self.overbought_level = float(overbought_level)
        self.atr_period = int(atr_period)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)

    @property
    def name(self) -> str:
        return "RsiMeanReversion"

    @property
    def display_name_th(self) -> str:
        return "RSI Mean Reversion (กลับตัวโซนอิ่มตัว)"

    @property
    def description_th(self) -> str:
        return (
            "จับจังหวะราคาวิ่งตึงจน RSI ต่ำกว่า Oversold หรือสูงกว่า Overbought แล้วตัดกลับเข้ากรอบ "
            "เหมาะกับตลาดแกว่ง Sideway"
        )

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec("rsi_period", "คาบคำนวณ RSI", "จำนวนแท่งเทียนมาตรฐานของ RSI (ปกติ 14)", "int", 14, 4, 40, 1),
            StrategyParamSpec("oversold_level", "โซนขายมากเกิน (Oversold)", "ระดับ RSI ที่มองว่าลงลึกเกินไป (ค่ามาตรฐาน 30)", "float", 30.0, 10.0, 45.0, 1.0),
            StrategyParamSpec("overbought_level", "โซนซื้อมากเกิน (Overbought)", "ระดับ RSI ที่มองว่าขึ้นตึงเกินไป (ค่ามาตรฐาน 70)", "float", 70.0, 55.0, 90.0, 1.0),
            StrategyParamSpec("atr_period", "ช่วงคำนวณ ATR", "จำนวนแท่งเทียนสำหรับคำนวณระยะ SL", "int", 14, 5, 50, 1),
            StrategyParamSpec("atr_sl_multiplier", "ตัวคูณระยะ SL (x ATR)", "ตั้ง SL กว้างกี่เท่าของความผันผวน ATR", "float", 1.5, 0.5, 5.0, 0.1),
            StrategyParamSpec("tp_rr_multiplier", "เป้ากำไร R:R", "อัตราส่วนระยะ TP ต่อระยะ SL", "float", 1.8, 0.5, 6.0, 0.1),
        ]

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        min_bars = max(self.rsi_period + 5, self.atr_period + 5, 30)
        if rates.empty or len(rates) < min_bars:
            return None

        closed = rates.iloc[:-1]
        if len(closed) < min_bars - 1:
            return None

        close_s = closed["close"].astype(float)
        high_s = closed["high"].astype(float)
        low_s = closed["low"].astype(float)
        prev_close = close_s.shift(1)

        tr = pd.concat([high_s - low_s, (high_s - prev_close).abs(), (low_s - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=self.atr_period).mean().iloc[-1]
        if pd.isna(atr) or float(atr) <= 0.0:
            return None
        atr_val = float(atr)

        delta = close_s.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.ewm(alpha=1.0 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0.0, pd.NA)
        rsi_s = 100.0 - (100.0 / (1.0 + rs))
        rsi_s = rsi_s.fillna(100.0)

        curr_rsi = float(rsi_s.iloc[-1])
        prev_rsi = float(rsi_s.iloc[-2])
        last_close = float(close_s.iloc[-1])

        if prev_rsi <= self.oversold_level and curr_rsi > self.oversold_level:
            entry = spec.ask if spec.ask > 0 else last_close
            sl_dist = self.atr_sl_multiplier * atr_val
            sl_price = round(entry - sl_dist, spec.digits)
            tp_price = round(entry + sl_dist * self.tp_rr_multiplier, spec.digits)
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=0.0,
                atr=atr_val,
                reason=f"RSI({self.rsi_period}) crossed back above {self.oversold_level:.0f} ({prev_rsi:.1f} -> {curr_rsi:.1f})",
                metadata={"rsi": curr_rsi, "prev_rsi": prev_rsi, "atr": atr_val},
            )

        if prev_rsi >= self.overbought_level and curr_rsi < self.overbought_level:
            entry = spec.bid if spec.bid > 0 else last_close
            sl_dist = self.atr_sl_multiplier * atr_val
            sl_price = round(entry + sl_dist, spec.digits)
            tp_price = round(entry - sl_dist * self.tp_rr_multiplier, spec.digits)
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.SELL,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                expected_hold_days=0.0,
                atr=atr_val,
                reason=f"RSI({self.rsi_period}) crossed back below {self.overbought_level:.0f} ({prev_rsi:.1f} -> {curr_rsi:.1f})",
                metadata={"rsi": curr_rsi, "prev_rsi": prev_rsi, "atr": atr_val},
            )

        return None
