from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec
from cfd_bot.domain.strategies.lorentzian_ml import _ema_np, compute_atr_array, compute_normalized_adx


class VolatilitySqueezeBreakoutStrategy(BaseStrategy):
    """
    Volatility Compression Squeeze + Donchian Breakout Strategy.

    Identifies tight Bollinger-inside-Keltner volatility compression phases, then
    enters on a confirmed Donchian Channel breakout aligned with ADX momentum,
    EMA macro trend, and liquid London/NY session hours. Tuned primarily for H1.
    """

    def __init__(
        self,
        donchian_period: int = 20,
        bb_period: int = 20,
        squeeze_lookback: int = 10,
        squeeze_kc_mult: float = 2.0,
        min_adx: float = 18.0,
        ema_period: int = 200,
        trade_start_hour_utc: int = 6,
        trade_end_hour_utc: int = 20,
        atr_sl_multiplier: float = 2.0,
        tp_rr_multiplier: float = 1.8,
        max_hold_bars: int = 0,
    ) -> None:
        self.donchian_period = int(donchian_period)
        self.bb_period = int(bb_period)
        self.squeeze_lookback = int(squeeze_lookback)
        self.squeeze_kc_mult = float(squeeze_kc_mult)
        self.min_adx = float(min_adx)
        self.ema_period = int(ema_period)
        self.trade_start_hour_utc = int(trade_start_hour_utc)
        self.trade_end_hour_utc = int(trade_end_hour_utc)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)
        self.max_hold_bars = max(0, int(max_hold_bars))

    @property
    def name(self) -> str:
        return "VolatilitySqueezeBreakout"

    @property
    def display_name_th(self) -> str:
        return "Volatility Squeeze Breakout (บีบตัวแล้วเบรกแรง)"

    @property
    def description_th(self) -> str:
        return (
            "รอจังหวะตลาดบีบอัดความผันผวน (Bollinger แคบกว่ากรอบ Keltner) แล้วเข้าเมื่อแท่งเทียนเบรก "
            "Donchian Channel ตามเทรนด์ EMA 200 และความแรง ADX — เด่นมากบนกรอบ H1"
        )

    @property
    def recommended_timeframe(self) -> str:
        return "H1"

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec(
                key="donchian_period",
                label_th="กรอบเบรก Donchian (แท่ง)",
                hint_th="จำนวนแท่งย้อนหลังที่ใช้หาจุดสูงสุด/ต่ำสุดสำหรับสัญญาณเบรกเอาต์",
                kind="int",
                default=20,
                min_val=10,
                max_val=40,
                step=2,
                recommended_th="20",
            ),
            StrategyParamSpec(
                key="bb_period",
                label_th="คาบ Bollinger Squeeze (แท่ง)",
                hint_th="จำนวนแท่งสำหรับวัดส่วนเบี่ยงเบนมาตรฐานความแคบของราคา",
                kind="int",
                default=20,
                min_val=10,
                max_val=40,
                step=2,
                recommended_th="20",
            ),
            StrategyParamSpec(
                key="squeeze_lookback",
                label_th="ช่วงเช็กการบีบตัวล่าสุด (แท่ง)",
                hint_th="ยอมรับสัญญาณเบรกหากเพิ่งเกิดภาวะราคาบีบตัวภายในกี่แท่งล่าสุด",
                kind="int",
                default=10,
                min_val=2,
                max_val=20,
                step=1,
                recommended_th="10",
            ),
            StrategyParamSpec(
                key="squeeze_kc_mult",
                label_th="ตัวคูณกรอบ Keltner (x ATR)",
                hint_th="เกณฑ์ความกว้างกรอบ ATR ที่ใช้เทียบว่า Bollinger กำลังบีบตัวแคบกว่าปกติ",
                kind="float",
                default=2.0,
                min_val=1.0,
                max_val=3.5,
                step=0.1,
                recommended_th="2.0",
            ),
            StrategyParamSpec(
                key="min_adx",
                label_th="ความแรงเทรนด์ขั้นต่ำ (ADX)",
                hint_th="กรองการเบรกหลอกช่วงตลาดไร้แรงวิ่ง (แนะนำ 16–22 บน H1; ใส่ 0 หากปิด)",
                kind="float",
                default=18.0,
                min_val=0.0,
                max_val=45.0,
                step=1.0,
                recommended_th="18",
            ),
            StrategyParamSpec(
                key="ema_period",
                label_th="เส้นเทรนด์หลัก (EMA)",
                hint_th="บังคับ Buy เฉพาะเหนือ EMA และ Sell เฉพาะใต้ EMA",
                kind="int",
                default=200,
                min_val=50,
                max_val=200,
                step=25,
                recommended_th="200",
            ),
            StrategyParamSpec(
                key="trade_start_hour_utc",
                label_th="ชั่วโมงเริ่มเทรด (UTC)",
                hint_th="6 UTC = 13:00 น. เวลาไทย (ก่อนเปิดลอนดอน)",
                kind="int",
                default=6,
                min_val=0,
                max_val=23,
                step=1,
                recommended_th="6 (13:00 ไทย)",
            ),
            StrategyParamSpec(
                key="trade_end_hour_utc",
                label_th="ชั่วโมงหยุดรับสัญญาณ (UTC)",
                hint_th="20 UTC = 03:00 น. เวลาไทย (จบช่วงนิวยอร์ก; ใส่ 24 หากเทรดตลอดวัน)",
                kind="int",
                default=20,
                min_val=1,
                max_val=24,
                step=1,
                recommended_th="20 (03:00 ไทย)",
            ),
            StrategyParamSpec(
                key="atr_sl_multiplier",
                label_th="ระยะ SL (x ATR)",
                hint_th="ตั้งระยะ Stop Loss กว้างกี่เท่าของ ATR(14)",
                kind="float",
                default=2.0,
                min_val=1.0,
                max_val=5.0,
                step=0.2,
                recommended_th="2.0",
            ),
            StrategyParamSpec(
                key="tp_rr_multiplier",
                label_th="เป้ากำไร Take Profit (R:R)",
                hint_th="สัดส่วนกำไรต่อความเสี่ยงตั้งต้น (เข้ากับ MIN_NET_RR=1.00 และ Trailing Stop ได้ดีมาก)",
                kind="float",
                default=1.8,
                min_val=0.8,
                max_val=5.0,
                step=0.1,
                recommended_th="1.80",
            ),
            StrategyParamSpec(
                key="max_hold_bars",
                label_th="ตัดปิดอัตโนมัติถ้าถือค้างเกิน (แท่ง)",
                hint_th="Time-Stop ปิดไม้ที่ถือค้างนานเกินกำหนด (ใช้ทั้งใน Backtest และบอทจริง; ใส่ 0 = ปิดใช้งาน)",
                kind="int",
                default=0,
                min_val=0,
                max_val=72,
                step=4,
                recommended_th="0 (ปิด) หรือ 24 (1 วันบน H1)",
            ),
        ]

    def _min_required_bars(self) -> int:
        return max(self.ema_period + 5, self.bb_period + self.squeeze_lookback + 5, self.donchian_period + 5, 60)

    def _compute_feature_context(self, closed_df: pd.DataFrame) -> dict[str, Any]:
        h = closed_df["high"].to_numpy(dtype=float)
        l = closed_df["low"].to_numpy(dtype=float)
        c = closed_df["close"].to_numpy(dtype=float)
        ts = pd.to_datetime(closed_df["time"], utc=True)
        hours = ts.dt.hour.to_numpy(dtype=int)

        sc = pd.Series(c)
        sh = pd.Series(h)
        sl_s = pd.Series(l)
        bb_std = sc.rolling(self.bb_period, min_periods=self.bb_period).std()
        atr = compute_atr_array(h, l, c, 14)
        atr_s = pd.Series(atr)

        bb_half = 2.0 * bb_std
        kc_half = self.squeeze_kc_mult * atr_s
        in_squeeze = (
            (bb_half < kc_half)
            .rolling(max(1, self.squeeze_lookback), min_periods=1)
            .max()
            .fillna(0.0)
            .to_numpy(dtype=float)
            > 0.0
        )
        donch_hi = sh.rolling(self.donchian_period).max().shift(1).to_numpy(dtype=float)
        donch_lo = sl_s.rolling(self.donchian_period).min().shift(1).to_numpy(dtype=float)
        adx = compute_normalized_adx(h, l, c, 14) * 100.0
        ema = _ema_np(c, self.ema_period)

        return {
            "c": c,
            "atr": atr,
            "adx": adx,
            "ema": ema,
            "in_squeeze": in_squeeze,
            "donch_hi": donch_hi,
            "donch_lo": donch_lo,
            "hours": hours,
        }

    def _hour_in_session(self, hr: int) -> bool:
        start = self.trade_start_hour_utc
        end = self.trade_end_hour_utc
        if start == 0 and end >= 24:
            return True
        if start <= end:
            return start <= hr < end
        return hr >= start or hr < end

    def _evaluate_bar_signal(self, spec: SymbolSpec, ctx: dict[str, Any], closed_idx: int) -> Optional[TradeSignal]:
        if closed_idx < max(self.bb_period, self.donchian_period, 30):
            return None
        hr = int(ctx["hours"][closed_idx])
        if not self._hour_in_session(hr):
            return None

        atr = float(ctx["atr"][closed_idx])
        adx = float(ctx["adx"][closed_idx])
        c = float(ctx["c"][closed_idx])
        c_prev = float(ctx["c"][closed_idx - 1])
        hi = float(ctx["donch_hi"][closed_idx])
        lo = float(ctx["donch_lo"][closed_idx])
        ema_val = float(ctx["ema"][closed_idx])

        if not (np.isfinite(atr) and atr > 0.0 and np.isfinite(hi) and np.isfinite(lo) and np.isfinite(ema_val)):
            return None
        if not bool(ctx["in_squeeze"][closed_idx]):
            return None
        if self.min_adx > 0.0 and adx < self.min_adx:
            return None

        sl_dist = self.atr_sl_multiplier * atr
        if c > hi and c_prev <= hi and c > ema_val:
            entry = spec.ask if spec.ask > 0 else c
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
                reason=f"Volatility Squeeze Breakout Buy (Donchian {self.donchian_period} + ADX {adx:.0f})",
                metadata={"adx": adx, "ema": ema_val, "donchian_high": hi},
            )

        if c < lo and c_prev >= lo and c < ema_val:
            entry = spec.bid if spec.bid > 0 else c
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
                reason=f"Volatility Squeeze Breakout Sell (Donchian {self.donchian_period} + ADX {adx:.0f})",
                metadata={"adx": adx, "ema": ema_val, "donchian_low": lo},
            )

        return None

    def precompute_backtest_context(self, closed_df: pd.DataFrame) -> Optional[dict[str, Any]]:
        if closed_df.empty or len(closed_df) < self._min_required_bars() - 1:
            return None
        return self._compute_feature_context(closed_df)

    def generate_signal_from_context(
        self, spec: SymbolSpec, ctx: dict[str, Any], bar_idx: int
    ) -> Optional[TradeSignal]:
        closed_idx = bar_idx - 1
        if closed_idx < max(self.ema_period, self._min_required_bars() - 2):
            return None
        return self._evaluate_bar_signal(spec, ctx, closed_idx)

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates.empty or len(rates) < self._min_required_bars():
            return None
        closed = rates.iloc[:-1]
        if len(closed) < self._min_required_bars() - 1:
            return None
        ctx = self._compute_feature_context(closed)
        return self._evaluate_bar_signal(spec, ctx, len(closed) - 1)
