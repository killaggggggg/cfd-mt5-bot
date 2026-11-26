from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec
from cfd_bot.domain.strategies.lorentzian_ml import (
    _ema_np,
    compute_atr_array,
    compute_normalized_adx,
    compute_normalized_rsi,
)


def _compute_causal_htf_trend_dir(
    times: pd.Series,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    htf_minutes: int,
    htf_trend_adx: float,
) -> np.ndarray:
    """
    Detects whether higher-timeframe market is actively trending (+1/-1) or range/neutral (0).

    Uses strictly completed HTF bars shifted by 1 period before mapping onto M5 timestamps
    so live evaluation and backtest precomputation have zero look-ahead bias.
    """
    idx = pd.to_datetime(times, utc=True)
    df_tmp = pd.DataFrame({"high": high, "low": low, "close": close}, index=idx)
    rule = f"{max(5, int(htf_minutes))}min"
    agg = df_tmp.resample(rule).agg({"high": "max", "low": "min", "close": "last"}).dropna()
    if len(agg) < 25:
        return np.zeros(len(close), dtype=int)

    hc = agg["close"].to_numpy(dtype=float)
    hh = agg["high"].to_numpy(dtype=float)
    hl = agg["low"].to_numpy(dtype=float)
    ema20 = _ema_np(hc, 20)
    ema50 = _ema_np(hc, 50)
    adx = compute_normalized_adx(hh, hl, hc, 14) * 100.0
    raw_dir = np.where(
        (hc > ema20) & (ema20 > ema50) & (adx >= htf_trend_adx),
        1,
        np.where((hc < ema20) & (ema20 < ema50) & (adx >= htf_trend_adx), -1, 0),
    )
    shifted = pd.Series(raw_dir, index=agg.index).shift(1).fillna(0)
    return shifted.reindex(df_tmp.index, method="ffill").fillna(0).to_numpy(dtype=int)


class M5RangeScalperStrategy(BaseStrategy):
    """
    High-frequency M5 intraday mean-reversion scalper gated by higher-TF range regime.

    Supports both Keltner Channel (EMA +/- K*ATR, 12/17 profitable months on XAUUSD M5)
    and Bollinger Band modes when higher-timeframe H1 is not strongly trending.
    """

    def __init__(
        self,
        channel_mode: int = 1,
        htf_minutes: int = 60,
        htf_max_adx: float = 18.0,
        channel_period: int = 20,
        band_multiplier: float = 1.8,
        rsi_buy_max: float = 42.0,
        rsi_sell_min: float = 58.0,
        trade_start_hour_utc: int = 6,
        trade_end_hour_utc: int = 20,
        sl_atr_multiplier: float = 2.0,
        tp_rr_multiplier: float = 1.5,
        max_hold_bars: int = 0,
    ) -> None:
        self.channel_mode = int(channel_mode)
        self.htf_minutes = int(htf_minutes)
        self.htf_max_adx = float(htf_max_adx)
        self.channel_period = int(channel_period)
        self.band_multiplier = float(band_multiplier)
        self.rsi_buy_max = float(rsi_buy_max)
        self.rsi_sell_min = float(rsi_sell_min)
        self.trade_start_hour_utc = int(trade_start_hour_utc)
        self.trade_end_hour_utc = int(trade_end_hour_utc)
        self.sl_atr_multiplier = float(sl_atr_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)
        self.max_hold_bars = max(0, int(max_hold_bars))

    @property
    def name(self) -> str:
        return "M5RangeScalper"

    @property
    def display_name_th(self) -> str:
        return "High-Freq M5 Keltner/Range Scalper"

    @property
    def description_th(self) -> str:
        return (
            "สแคลปความถี่สูงกรอบ M5 (~3 ไม้/วัน, 1,065 ไม้ใน 17 เดือน, บวก 12/17 เดือน) "
            "จับจังหวะราคาทะลุขอบ Keltner/Bollinger แล้วเด้งกลับช่วง H1 พักตัว เสริมคู่กับสายเทรนด์ M5KernelPullback"
        )

    @property
    def recommended_timeframe(self) -> str:
        return "M5"

    @property
    def recommended_history_bars(self) -> int:
        # On M5, each H1 bar spans 12 bars -> 65 H1 bars = 780 M5 bars to warm up H1 EMA50 + ADX14.
        bars_per_htf = max(1, int(round(self.htf_minutes / 5.0)))
        return max(300, bars_per_htf * 65)

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec(
                key="channel_mode",
                label_th="ชนิดกรอบราคา (Keltner vs Bollinger)",
                hint_th="1=Keltner Channel (EMA +/- K*ATR ชนะ 12/17 เดือน +73.8R), 0=Bollinger Band (SMA +/- K*SD)",
                kind="int",
                default=1,
                min_val=0,
                max_val=1,
                step=1,
                options=(
                    {"value": 1, "label": "Keltner Channel ATR (แนะนำ — PF ~1.16)"},
                    {"value": 0, "label": "Bollinger Band SD (เน้น OOS ปี 2026)"},
                ),
                recommended_th="1 (Keltner)",
            ),
            StrategyParamSpec(
                key="htf_minutes",
                label_th="กรอบใหญ่คุมสภาวะไซด์เวย์ (นาที)",
                hint_th="กรอบเวลาที่ใช้ตรวจว่าตลาดใหญ่ยังไม่เกิดเทรนด์แรง (60=H1 แนะนำ, 30=M30, 15=M15)",
                kind="int",
                default=60,
                min_val=15,
                max_val=60,
                step=15,
                options=(
                    {"value": 60, "label": "60 นาที (H1 — แนะนำ)"},
                    {"value": 30, "label": "30 นาที (M30)"},
                    {"value": 15, "label": "15 นาที (M15)"},
                ),
                recommended_th="60 (H1)",
            ),
            StrategyParamSpec(
                key="htf_max_adx",
                label_th="เพดาน ADX กรอบใหญ่ (ตัดตอนเป็นเทรนด์)",
                hint_th="เมื่อกรอบใหญ่เรียง EMA 20/50 และ ADX >= ค่านี้ ระบบจะงดสวนเทรนด์",
                kind="float",
                default=18.0,
                min_val=10.0,
                max_val=30.0,
                step=1.0,
                recommended_th="18.0",
            ),
            StrategyParamSpec(
                key="channel_period",
                label_th="คาบกรอบราคา (แท่ง M5)",
                hint_th="จำนวนแท่งเฉลี่ยของ EMA/SMA สำหรับคำนวณกรอบ (มาตรฐาน 20)",
                kind="int",
                default=20,
                min_val=10,
                max_val=40,
                step=2,
                recommended_th="20",
            ),
            StrategyParamSpec(
                key="band_multiplier",
                label_th="ตัวคูณความกว้างขอบ (x ATR หรือ SD)",
                hint_th="แนะนำ 1.8 สำหรับ Keltner Channel หรือ 2.0 สำหรับ Bollinger Band",
                kind="float",
                default=1.8,
                min_val=1.2,
                max_val=3.0,
                step=0.1,
                recommended_th="1.8 (Keltner) / 2.0 (BB)",
            ),
            StrategyParamSpec(
                key="rsi_buy_max",
                label_th="RSI สูงสุดฝั่ง BUY (Oversold)",
                hint_th="เข้า Buy เฉพาะตอน RSI14 ต่ำกว่าค่านี้ (42=ออกไม้ถี่ ~1,065 ไม้ / 40=เน้น DD ต่ำ)",
                kind="float",
                default=42.0,
                min_val=20.0,
                max_val=50.0,
                step=1.0,
                recommended_th="42.0",
            ),
            StrategyParamSpec(
                key="rsi_sell_min",
                label_th="RSI ต่ำสุดฝั่ง SELL (Overbought)",
                hint_th="เข้า Sell เฉพาะตอน RSI14 สูงกว่าค่านี้ (58=ออกไม้ถี่ / 60=เน้น DD ต่ำ)",
                kind="float",
                default=58.0,
                min_val=50.0,
                max_val=80.0,
                step=1.0,
                recommended_th="58.0",
            ),
            StrategyParamSpec(
                key="trade_start_hour_utc",
                label_th="ชั่วโมงเริ่มเทรด (UTC)",
                hint_th="6 UTC = 13:00 น. เวลาไทย (ก่อนตลาดลอนดอนเปิด)",
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
                hint_th="20 UTC = 03:00 น. เวลาไทย (หลีกเลี่ยงช่วงสเปรดดึกเอเชียซึม)",
                kind="int",
                default=20,
                min_val=1,
                max_val=24,
                step=1,
                recommended_th="20 (03:00 ไทย)",
            ),
            StrategyParamSpec(
                key="sl_atr_multiplier",
                label_th="ตัวคูณ Stop Loss (x ATR14)",
                hint_th="วาง SL กว้างพอไม่ให้ไส้สะบัดหลุดกรอบและสู้ Spread จริงได้",
                kind="float",
                default=2.0,
                min_val=1.0,
                max_val=4.0,
                step=0.1,
                recommended_th="2.0",
            ),
            StrategyParamSpec(
                key="tp_rr_multiplier",
                label_th="สัดส่วนกำไร Take Profit (R:R)",
                hint_th="1.50 เข้ากับ MIN_NET_RR=1.00 และ Auto-BE/Trailing ของบอทจริงโดยไม่มี Conflict",
                kind="float",
                default=1.5,
                min_val=1.0,
                max_val=4.0,
                step=0.1,
                recommended_th="1.5",
            ),
            StrategyParamSpec(
                key="max_hold_bars",
                label_th="ตัดปิดอัตโนมัติถ้าถือค้างเกิน (แท่ง)",
                hint_th="Time-Stop ปิดไม้ที่วิ่งซึมนานเกินกำหนด (ใช้ทั้งใน Backtest และบอทจริง; ใส่ 0 = ปิดใช้งาน)",
                kind="int",
                default=0,
                min_val=0,
                max_val=144,
                step=6,
                recommended_th="0 (ปิด) หรือ 48 (~4 ชม.)",
            ),
        ]

    def _min_required_bars(self) -> int:
        return max(self.channel_period + 15, 60)

    def _compute_feature_context(self, closed: pd.DataFrame) -> dict[str, Any]:
        o = closed["open"].to_numpy(dtype=float)
        h = closed["high"].to_numpy(dtype=float)
        l = closed["low"].to_numpy(dtype=float)
        c = closed["close"].to_numpy(dtype=float)
        times = pd.to_datetime(closed["time"], utc=True)
        hours = times.dt.hour.to_numpy(dtype=int)

        atr14 = compute_atr_array(h, l, c, 14)
        if self.channel_mode > 0:
            # Keltner Channel: EMA(period) +/- band_multiplier * ATR14
            mid = _ema_np(c, self.channel_period)
            band_up = mid + self.band_multiplier * atr14
            band_lo = mid - self.band_multiplier * atr14
        else:
            # Bollinger Band: SMA(period) +/- band_multiplier * SD
            sc = pd.Series(c)
            mid = sc.rolling(self.channel_period, min_periods=self.channel_period).mean().to_numpy(dtype=float)
            sd = sc.rolling(self.channel_period, min_periods=self.channel_period).std(ddof=0).to_numpy(dtype=float)
            band_up = mid + self.band_multiplier * sd
            band_lo = mid - self.band_multiplier * sd

        return {
            "n": len(closed),
            "o": o,
            "h": h,
            "l": l,
            "c": c,
            "hours": hours,
            "atr14": atr14,
            "band_up": band_up,
            "band_lo": band_lo,
            "rsi14": compute_normalized_rsi(c, 14, 1) * 100.0,
            "htf_dir": _compute_causal_htf_trend_dir(times, h, l, c, self.htf_minutes, self.htf_max_adx),
        }

    def precompute_backtest_context(self, closed_rates: pd.DataFrame) -> dict[str, Any]:
        if len(closed_rates) < self._min_required_bars():
            return {"n": 0}
        return self._compute_feature_context(closed_rates)

    def _hour_allowed(self, hour_utc: int) -> bool:
        start_h = self.trade_start_hour_utc
        end_h = self.trade_end_hour_utc
        if start_h == end_h:
            return True
        if start_h < end_h:
            return start_h <= hour_utc < end_h
        return hour_utc >= start_h or hour_utc < end_h

    def _evaluate_bar_signal(self, spec: SymbolSpec, ctx: dict[str, Any], next_idx: int) -> Optional[TradeSignal]:
        i = next_idx - 1
        n = int(ctx.get("n", 0))
        if n <= 0 or i < self._min_required_bars() or i >= n:
            return None

        if not self._hour_allowed(int(ctx["hours"][i])):
            return None

        if int(ctx["htf_dir"][i]) != 0:
            return None

        atr_val = float(ctx["atr14"][i])
        band_lo = float(ctx["band_lo"][i])
        band_up = float(ctx["band_up"][i])
        if np.isnan(atr_val) or np.isnan(band_lo) or np.isnan(band_up) or atr_val <= 0.0:
            return None

        o_i = float(ctx["o"][i])
        h_i = float(ctx["h"][i])
        l_i = float(ctx["l"][i])
        c_i = float(ctx["c"][i])
        rsi_i = float(ctx["rsi14"][i])
        sl_dist = atr_val * self.sl_atr_multiplier
        tp_dist = sl_dist * self.tp_rr_multiplier
        digits = spec.digits
        tag = "Keltner" if self.channel_mode > 0 else "BB"

        if l_i < band_lo and c_i > band_lo and c_i > o_i and rsi_i < self.rsi_buy_max:
            entry = float(spec.ask) if spec.ask > 0 else c_i
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=round(entry, digits),
                sl_price=round(entry - sl_dist, digits),
                tp_price=round(entry + tp_dist, digits),
                expected_hold_days=0.0,
                atr=atr_val,
                reason=f"M5 {tag} Reversion BUY (<{band_lo:.2f}, RSI={rsi_i:.1f}, H{self.htf_minutes}m Flat)",
            )

        if h_i > band_up and c_i < band_up and c_i < o_i and rsi_i > self.rsi_sell_min:
            entry = float(spec.bid) if spec.bid > 0 else c_i
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.SELL,
                entry_price=round(entry, digits),
                sl_price=round(entry + sl_dist, digits),
                tp_price=round(entry - tp_dist, digits),
                expected_hold_days=0.0,
                atr=atr_val,
                reason=f"M5 {tag} Reversion SELL (>{band_up:.2f}, RSI={rsi_i:.1f}, H{self.htf_minutes}m Flat)",
            )

        return None

    def generate_signal_from_context(
        self, spec: SymbolSpec, context: Any, next_bar_idx: int
    ) -> Optional[TradeSignal]:
        return self._evaluate_bar_signal(spec, context, next_bar_idx)

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates.empty or len(rates) < self._min_required_bars() + 2:
            return None
        closed = rates.iloc[:-1].reset_index(drop=True)
        ctx = self._compute_feature_context(closed)
        return self._evaluate_bar_signal(spec, ctx, len(closed))
