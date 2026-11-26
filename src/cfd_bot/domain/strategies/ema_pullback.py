from __future__ import annotations

from typing import Optional

import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec


class EmaTrendPullbackStrategy(BaseStrategy):
    """
    Pure Offensive Trend Strategy: Fast EMA / Slow EMA Trend Pullback.

    Identifies trend direction via EMA(fast) vs EMA(slow), waits for a bar to
    dip into/near EMA(fast), and triggers on a confirmed candle close back in
    the primary trend direction.
    """

    def __init__(
        self,
        fast_ema_period: int = 50,
        slow_ema_period: int = 200,
        pullback_band_atr: float = 0.35,
        atr_period: int = 14,
        atr_sl_multiplier: float = 1.5,
        tp_rr_multiplier: float = 2.0,
    ) -> None:
        self.fast_ema_period = int(fast_ema_period)
        self.slow_ema_period = int(slow_ema_period)
        self.pullback_band_atr = float(pullback_band_atr)
        self.atr_period = int(atr_period)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)

    @property
    def name(self) -> str:
        return "EmaTrendPullback"

    @property
    def display_name_th(self) -> str:
        return "EMA 50/200 Trend Pullback (ย่อตัวตามเทรนด์)"

    @property
    def description_th(self) -> str:
        return (
            "อิงเทรนด์จากเส้น EMA เร็วเทียบ EMA ช้า รอราคาพักตัวย่อเข้าใกล้เส้น EMA เร็วแล้วปิดแท่งกลับตัวไปต่อ "
            "เหมาะกับตลาดที่มีแนวโน้มชัดเจน (M15 / H1)"
        )

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec("fast_ema_period", "เส้น EMA เร็ว (Period)", "เส้นแนวโน้มระยะกลาง (ค่ามาตรฐาน 50)", "int", 50, 5, 150, 1),
            StrategyParamSpec("slow_ema_period", "เส้น EMA ช้า (Period)", "เส้นแนวโน้มหลัก (ค่ามาตรฐาน 200)", "int", 200, 20, 400, 5),
            StrategyParamSpec("pullback_band_atr", "ระยะย่อเข้าใกล้ EMA (x ATR)", "ยอมรับการย่อเข้ามาภายในกี่เท่าของ ATR จากเส้น EMA เร็ว", "float", 0.35, 0.0, 2.0, 0.05),
            StrategyParamSpec("atr_period", "ช่วงคำนวณ ATR", "จำนวนแท่งเทียนสำหรับวัดความผันผวน ATR", "int", 14, 5, 50, 1),
            StrategyParamSpec("atr_sl_multiplier", "ตัวคูณระยะ SL (x ATR)", "ตั้ง SL กว้างกี่เท่าของความผันผวน ATR", "float", 1.5, 0.5, 5.0, 0.1),
            StrategyParamSpec("tp_rr_multiplier", "เป้ากำไร R:R", "อัตราส่วนระยะ TP ต่อระยะ SL (เช่น 2.0 = 1:2)", "float", 2.0, 0.5, 8.0, 0.1),
        ]

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        min_bars = max(self.slow_ema_period + 5, self.atr_period + 5, 30)
        if rates.empty or len(rates) < min_bars:
            return None

        # Use closed bars only
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

        ema_fast_s = close_s.ewm(span=self.fast_ema_period, adjust=False).mean()
        ema_slow_s = close_s.ewm(span=self.slow_ema_period, adjust=False).mean()

        ema_fast = float(ema_fast_s.iloc[-1])
        ema_slow = float(ema_slow_s.iloc[-1])

        last_open = float(closed["open"].iloc[-1])
        last_close = float(close_s.iloc[-1])
        last_low = float(low_s.iloc[-1])
        last_high = float(high_s.iloc[-1])
        prev_low = float(low_s.iloc[-2])
        prev_high = float(high_s.iloc[-2])

        band = self.pullback_band_atr * atr_val

        if ema_fast > ema_slow:
            dipped_near_ema = min(last_low, prev_low) <= (ema_fast + band)
            bullish_reclaim = last_close > ema_fast and last_close > last_open
            if dipped_near_ema and bullish_reclaim:
                entry = spec.ask if spec.ask > 0 else last_close
                sl_dist = max(self.atr_sl_multiplier * atr_val, (entry - last_low) + 0.25 * atr_val)
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
                    reason=f"Uptrend EMA({self.fast_ema_period}) > EMA({self.slow_ema_period}) bullish pullback bounce",
                    metadata={"ema_fast": ema_fast, "ema_slow": ema_slow, "atr": atr_val},
                )

        elif ema_fast < ema_slow:
            rallied_near_ema = max(last_high, prev_high) >= (ema_fast - band)
            bearish_reject = last_close < ema_fast and last_close < last_open
            if rallied_near_ema and bearish_reject:
                entry = spec.bid if spec.bid > 0 else last_close
                sl_dist = max(self.atr_sl_multiplier * atr_val, (last_high - entry) + 0.25 * atr_val)
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
                    reason=f"Downtrend EMA({self.fast_ema_period}) < EMA({self.slow_ema_period}) bearish pullback rejection",
                    metadata={"ema_fast": ema_fast, "ema_slow": ema_slow, "atr": atr_val},
                )

        return None
