from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec
from cfd_bot.domain.strategies.lorentzian_ml import (
    _ema_np,
    causal_rational_quadratic_kernel,
    compute_atr_array,
    compute_lorentzian_series,
    compute_normalized_adx,
    compute_normalized_rsi,
)


class M5KernelPullbackStrategy(BaseStrategy):
    """
    High-Win-Rate M5 Trend-Pullback Scalper combining:
      - Macro Structure: EMA 50 vs EMA 200 trend alignment
      - Volatility & Momentum Filter: ADX trend strength >= min_adx & RSI pullback zone
      - Kernel & AI Confluence: Nadaraya-Watson Rational Quadratic Kernel slope + optional Lorentzian ML
      - Dynamic Structural SL behind 3-bar pullback swing extreme
    """

    name = "M5KernelPullback"
    display_name_th = "M5 Kernel + EMA Pullback (เน้น Win Rate สูง 5 นาที)"
    description_th = (
        "ออกแบบเฉพาะกรอบ 5 นาที (M5): ตามเทรนด์ EMA 50/200 รอราคาพักตัวแตะโซน EMA 21 ในช่วง RSI ที่ไม่ตึงเกินไป "
        "ยืนยันด้วยความชัน Nadaraya-Watson Kernel, ADX และ Lorentzian ML ตั้ง SL กว้างกันไส้เทียนพร้อมเป้า R:R 1.25"
    )

    def __init__(
        self,
        fast_ema_period: int = 21,
        mid_ema_period: int = 50,
        slow_ema_period: int = 200,
        min_adx: float = 18.0,
        kernel_h: int = 8,
        require_ml_confirm: int = 1,
        pullback_band_atr: float = 0.3,
        min_body_atr: float = 0.25,
        sl_min_atr: float = 1.6,
        sl_max_atr: float = 2.2,
        sl_buffer_atr: float = 0.35,
        tp_rr_multiplier: float = 1.25,
        max_hold_bars: int = 48,
        rsi_buy_min: float = 40.0,
        rsi_buy_max: float = 66.0,
    ) -> None:
        self.fast_ema_period = int(fast_ema_period)
        self.mid_ema_period = int(mid_ema_period)
        self.slow_ema_period = int(slow_ema_period)
        self.min_adx = float(min_adx)
        self.kernel_h = int(kernel_h)
        self.require_ml_confirm = int(require_ml_confirm)
        self.pullback_band_atr = float(pullback_band_atr)
        self.min_body_atr = float(min_body_atr)
        self.sl_min_atr = float(sl_min_atr)
        self.sl_max_atr = max(self.sl_min_atr, float(sl_max_atr))
        self.sl_buffer_atr = float(sl_buffer_atr)
        self.tp_rr_multiplier = float(tp_rr_multiplier)
        self.max_hold_bars = max(0, int(max_hold_bars))
        lo = float(rsi_buy_min)
        hi = float(rsi_buy_max)
        self.rsi_buy_min = min(lo, hi)
        self.rsi_buy_max = max(lo, hi)

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec(
                key="fast_ema_period",
                label_th="เส้น EMA ย่อรับสั้น (Pullback EMA)",
                hint_th="เส้นค่าเฉลี่ยระยะสั้นที่ใช้เป็นจุดรอย่อตัวของราคา (ในขาขึ้นรอราคาย่อลงมาแตะเส้นนี้แล้วดีดกลับ)",
                example_th="ลดเป็น 13 หรือ 9 เพื่อจับจังหวะย่อสั้นๆ ออกไม้ถี่ขึ้น; ใช้ 21 เพื่อรอการย่อตัวที่เป็นจังหวะชัดเจน",
                recommended_th="21 (มาตรฐาน) หรือ 13 (เน้นถี่ขึ้น)",
                kind="int",
                default=21,
                min_val=8,
                max_val=45,
                step=1,
            ),
            StrategyParamSpec(
                key="mid_ema_period",
                label_th="เส้น EMA กลาง (Mid Trend EMA)",
                hint_th="ตัวแบ่งโครงสร้างเทรนด์ระยะกลาง ราคาปิดและเส้นนี้ต้องเรียงอยู่ฝั่งเดียวกับ EMA ใหญ่ถึงจะอนุญาตให้เข้าไม้",
                example_th="ลดเป็น 34 หรือ 21 เพื่อตามรอบเทรนด์เร็วขึ้น; ใช้ 50 เพื่อกรองตลาดช่วงแกว่งแคบออก",
                recommended_th="50 (มาตรฐาน) หรือ 34 (คู่กับ 13/100)",
                kind="int",
                default=50,
                min_val=20,
                max_val=120,
                step=1,
            ),
            StrategyParamSpec(
                key="slow_ema_period",
                label_th="เส้น EMA ใหญ่ (Macro Trend EMA)",
                hint_th="เข็มทิศเทรนด์ใหญ่หลัก: ราคาเหนือ EMA นี้จะมองเฉพาะฝั่ง BUY และใต้เส้นนี้จะมองเฉพาะฝั่ง SELL",
                example_th="ลดเป็น 89–100 จะเปลี่ยนทิศทางไวและออกไม้ได้มากขึ้น; ใช้ 200 เพื่อเทรดตามเทรนด์ใหญ่เท่านั้น",
                recommended_th="200 (มาตรฐาน) หรือ 100 (จับรอบไว)",
                kind="int",
                default=200,
                min_val=60,
                max_val=300,
                step=5,
            ),
            StrategyParamSpec(
                key="min_adx",
                label_th="ความแรงเทรนด์ขั้นต่ำ (ADX)",
                hint_th="ตัวกรองภาวะตลาดซบเซา (Sideway): ถ้าค่า ADX ต่ำกว่าเลขนี้ กลยุทธ์จะงดเข้าออเดอร์เพื่อเลี่ยงช่วงราคาวิ่งออกข้าง",
                example_th="ลดเป็น 10–12 เพื่อเข้าเทรดถี่ขึ้นในช่วงต้นเทรนด์; เพิ่มเป็น 20–25 เพื่อคัดเฉพาะตอนตลาดวิ่งแรง (จำนวนไม้ลดลง)",
                recommended_th="18 (คัดเน้นคุณภาพ) หรือ 12 (เน้นออกไม้ถี่ขึ้น)",
                kind="float",
                default=18.0,
                min_val=0.0,
                max_val=40.0,
                step=1.0,
            ),
            StrategyParamSpec(
                key="kernel_h",
                label_th="ความยาวเส้น Kernel (Lookback h)",
                hint_th="เส้นกราฟสมูท Nadaraya-Watson Rational Quadratic ใช้เช็กความชันโมเมนตัมระยะสั้นว่ากำลังหักหัวไปทางเดียวกับเทรนด์จริง",
                example_th="ค่าน้อย (5–6) เส้นไวตามราคาเร็ว ออกไม้ไวขึ้น; ค่ามาก (8–12) เส้นนิ่งขึ้น ลดสัญญาณหลอกช่วงย่อลึก",
                recommended_th="8 (มาตรฐาน) หรือ 6 (คู่กับโหมดเร็ว)",
                kind="int",
                default=8,
                min_val=3,
                max_val=25,
                step=1,
            ),
            StrategyParamSpec(
                key="require_ml_confirm",
                label_th="ยืนยันด้วย Lorentzian ML (1=เปิด / 0=ปิด)",
                hint_th="สวิตช์ตัวกรอง AI (ค้นหาแท่งประวัติศาสตร์ที่พฤติกรรมคล้ายกัน): 1 = ต้องให้ AI เห็นพ้องด้วยถึงเข้าไม้, 0 = ใช้เฉพาะ EMA + Kernel + RSI",
                example_th="ตั้ง 1 = ไม้ลดลงแต่คัดกรองจังหวะคมขึ้น; ตั้ง 0 = เพิ่มจำนวนไม้เกือบ 2 เท่า เหมาะเวลาทดสอบเน้นรอบถี่บนทองคำ",
                recommended_th="1 (สมดุล/ปลอดภัย) หรือ 0 (เน้นถี่ขึ้น ~2 เท่า)",
                kind="int",
                default=1,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="pullback_band_atr",
                label_th="ระยะย่อใกล้ EMA (x ATR)",
                hint_th="ระยะเผื่อว่าไส้เทียนย่อเข้าใกล้เส้น Pullback EMA ได้มากน้อยแค่ไหน (วัดเป็นเท่าของ ATR14 เพื่อปรับตามความผันผวนอัตโนมัติ)",
                example_th="เพิ่มเป็น 0.5–0.6 = แค่ราคาย่อเข้าใกล้โซนเส้น EMA ก็เข้าได้ (ไม้เยอะขึ้น); ลดเหลือ 0.1–0.3 = ต้องย่อชนเส้นเป๊ะๆ",
                recommended_th="0.30 (ย่อลึก) หรือ 0.50 – 0.60 (เน้นเข้าถี่ขึ้น)",
                kind="float",
                default=0.3,
                min_val=0.0,
                max_val=1.5,
                step=0.05,
            ),
            StrategyParamSpec(
                key="min_body_atr",
                label_th="แรงแท่งเทียนปิดกลับตัว (x ATR)",
                hint_th="ขนาดเนื้อเทียนขั้นต่ำของแท่งสัญญาณ (วัดเป็นเท่าของ ATR14) เพื่อยืนยันว่ามีแรงซื้อหรือแรงขายผลักกลับจริง ไม่ใช่แท่งโดจิลังเล",
                example_th="ลดเป็น 0.10 = แท่งกลับตัวขนาดเล็กก็อนุมัติเข้า (ไม้ถี่ขึ้น); เพิ่มเป็น 0.30+ = รอเฉพาะแท่งโมเมนตัมใหญ่",
                recommended_th="0.25 (มาตรฐาน) หรือ 0.10 (เปิดโอกาสเข้าถี่ขึ้น)",
                kind="float",
                default=0.25,
                min_val=0.05,
                max_val=1.5,
                step=0.05,
            ),
            StrategyParamSpec(
                key="sl_min_atr",
                label_th="ระยะ SL ขั้นต่ำกันสะบัด (x ATR)",
                hint_th="บังคับระยะตัดขาดทุน (Stop Loss) ให้กว้างอย่างน้อยกี่เท่าของ ATR14 เพื่อป้องกันโดนไส้เทียนสั้นๆ กวาด SL ก่อนราคาวิ่งตามเทรนด์",
                example_th="เพิ่มเป็น 2.0–2.2 = กันไส้สะบัดเก่ง Win Rate สูงขึ้น แต่ Lot จะเล็กลงตามสูตรเสี่ยง %; ลดต่ำกว่า 1.4 เสี่ยงโดนไส้กิน SL บ่อย",
                recommended_th="1.6 (สมดุล) หรือ 2.2 (เน้นเพิ่ม Win Rate กันสะบัด)",
                kind="float",
                default=1.6,
                min_val=0.8,
                max_val=3.5,
                step=0.1,
            ),
            StrategyParamSpec(
                key="sl_max_atr",
                label_th="เพดาน SL สูงสุดกันแท่งสะบัด (x ATR)",
                hint_th="จำกัดไม่ให้แท่งเทียนผันผวนแรงหรือไส้สวิงกว้างดึงระยะ SL และ TP บานออกไปจนถือค้างนานเกินไป (เดิมล็อกตายไว้ที่ 3.0x ATR)",
                example_th="ตั้ง 2.2 = ถึงมีแท่งสะบัดแรงก่อนเข้า ระยะ SL ก็จะไม่เกิน 2.2 เท่าของ ATR14 ช่วยให้จบไม้ไวขึ้นและไม่โดนลากยาว",
                recommended_th="2.2 (คุมระยะสวิง M5) หรือ 2.5",
                kind="float",
                default=2.2,
                min_val=1.5,
                max_val=3.5,
                step=0.1,
            ),
            StrategyParamSpec(
                key="sl_buffer_atr",
                label_th="ระยะเผื่อเลย Swing High/Low (x ATR)",
                hint_th="ระยะเผื่อเพิ่มด้านหลังจุดสวิงสูง/ต่ำ 3 แท่งล่าสุดตอนวาง SL เพื่อไม่ให้ SL ไปวางจ่อตรงปลายไส้พอดีแล้วโดน Spread เกี่ยว",
                example_th="ใส่ 0.35 = วาง SL เลยจุดสวิงไปอีก 0.35 เท่าของ ATR; ใส่ 0.50 จะกันไส้แถวแนวรับต้านได้ดีขึ้น",
                recommended_th="0.35 (มาตรฐาน M5)",
                kind="float",
                default=0.35,
                min_val=0.05,
                max_val=1.5,
                step=0.05,
            ),
            StrategyParamSpec(
                key="tp_rr_multiplier",
                label_th="เป้ากำไร R:R",
                hint_th="อัตราส่วนกำไรต่อความเสี่ยง: เอา 'ระยะ SL' คูณตัวเลขนี้เพื่อคำนวณจุด Take Profit (TP) โดยตรง",
                example_th="ตั้ง 1.00 = TP ไกลเท่า SL (ปิดกำไรง่าย Win Rate สูง แต่ถ้าเปิด Defensive ควรลด MIN_NET_RR ด้วย); ตั้ง 1.25–1.50 = รอคำใหญ่ขึ้น",
                recommended_th="1.25 (สมดุลกับ MIN_NET_RR 1.0) หรือ 1.00 (เน้น Win Rate ~58–60%)",
                kind="float",
                default=1.25,
                min_val=0.8,
                max_val=4.0,
                step=0.05,
            ),
            StrategyParamSpec(
                key="max_hold_bars",
                label_th="ตัดปิดอัตโนมัติถ้าถือค้างเกิน (แท่ง)",
                hint_th="Time-Stop สำหรับปิดไม้ที่ราคาวิ่งซึมออกข้าง ไม่ยอมชนทั้ง SL และ TP นานเกินกำหนด (ใช้ทั้งใน Backtest และบอทจริง; ใส่ 0 = ปิด)",
                example_th="ตั้ง 48 บนกราฟ M5 = ถ้าเปิดค้างครบ ~4 ชั่วโมงแล้วยังไม่ถึง SL/TP ระบบจะปิดออกตามราคาตลาดทันที ไม่ปล่อยค้างข้ามคืน",
                recommended_th="48 (~4 ชม. บน M5) หรือ 36 (~3 ชม.); 0 = ปิด",
                kind="int",
                default=48,
                min_val=0,
                max_val=144,
                step=6,
            ),
            StrategyParamSpec(
                key="rsi_buy_min",
                label_th="โซน RSI ขั้นต่ำ (ฝั่ง BUY / สะท้อน SELL)",
                hint_th="ขอบล่างของค่า RSI สำหรับจังหวะย่อซื้อ (และระบบคำนวณขอบบนฝั่ง SELL ให้สมมาตรอัตโนมัติ = 100 - ค่านี้ เช่น 40 -> ฝั่ง SELL ไม่เกิน 60)",
                example_th="ลดจาก 40 เหลือ 35 จะเปิดรับจังหวะย่อตัวลึกมากขึ้น (ฝั่ง SELL รับถึง 65) ช่วยเพิ่มความถี่ในการเข้าไม้",
                recommended_th="40 (มาตรฐาน) หรือ 35 (โหมดถี่)",
                kind="float",
                default=40.0,
                min_val=20.0,
                max_val=50.0,
                step=1.0,
            ),
            StrategyParamSpec(
                key="rsi_buy_max",
                label_th="โซน RSI ขั้นสูง (ฝั่ง BUY / สะท้อน SELL)",
                hint_th="ขอบบนของค่า RSI สำหรับจังหวะย่อซื้อ กันไม่ให้ไล่ซื้อตอน Overbought เกินไป (ฝั่ง SELL จะสะท้อนอัตโนมัติ = 100 - ค่านี้ เช่น 66 -> ฝั่ง SELL ขั้นต่ำ 34)",
                example_th="เพิ่มจาก 66 เป็น 72 จะอนุญาตให้เข้าตามเทรนด์แรงต่อเนื่องได้บ่อยขึ้น (ฝั่ง SELL ลงได้ถึง 28)",
                recommended_th="66 (มาตรฐาน) หรือ 72 (โหมดถี่)",
                kind="float",
                default=66.0,
                min_val=52.0,
                max_val=85.0,
                step=1.0,
            ),
        ]

    def _min_required_bars(self) -> int:
        return max(60, min(self.slow_ema_period, 120) + 10)

    def _compute_feature_context(self, closed: pd.DataFrame, eval_all_ml: bool) -> dict[str, Any]:
        """Computes strictly causal indicator arrays once across `closed` bars."""
        n = len(closed)
        open_arr = closed["open"].to_numpy(dtype=float)
        high_arr = closed["high"].to_numpy(dtype=float)
        low_arr = closed["low"].to_numpy(dtype=float)
        close_arr = closed["close"].to_numpy(dtype=float)

        atr_arr = compute_atr_array(high_arr, low_arr, close_arr, 14)
        adx_arr = (
            compute_normalized_adx(high_arr, low_arr, close_arr, 14) * 100.0
            if self.min_adx > 0.0
            else None
        )
        ema_fast = _ema_np(close_arr, self.fast_ema_period)
        ema_mid = _ema_np(close_arr, self.mid_ema_period)
        ema_slow = _ema_np(close_arr, min(self.slow_ema_period, n))
        rsi_arr = compute_normalized_rsi(close_arr, 14, 1) * 100.0

        if self.require_ml_confirm:
            ml_out = compute_lorentzian_series(
                closed_df=closed,
                neighbors_count=8,
                max_bars_back=min(220, max(40, n - 15)),
                use_volatility_filter=True,
                use_regime_filter=True,
                use_kernel_filter=True,
                kernel_h=self.kernel_h,
                kernel_r=8.0,
                eval_tail_bars=n if eval_all_ml else 24,
            )
            kern_bull_arr = ml_out.kernel_bullish
            kern_bear_arr = ml_out.kernel_bearish
            ml_bull_arr = (ml_out.signals == 1) | (ml_out.predictions > 0.0)
            ml_bear_arr = (ml_out.signals == -1) | (ml_out.predictions < 0.0)
        else:
            krq = causal_rational_quadratic_kernel(close_arr, h=self.kernel_h, r=8.0, window=max(30, self.kernel_h * 6))
            kern_bull_arr = np.zeros(n, dtype=bool)
            kern_bear_arr = np.zeros(n, dtype=bool)
            if n > 1:
                kern_bull_arr[1:] = krq[1:] >= krq[:-1]
                kern_bear_arr[1:] = krq[1:] <= krq[:-1]
            ml_bull_arr = np.ones(n, dtype=bool)
            ml_bear_arr = np.ones(n, dtype=bool)

        return {
            "n": n,
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "atr": atr_arr,
            "adx": adx_arr,
            "ema_fast": ema_fast,
            "ema_mid": ema_mid,
            "ema_slow": ema_slow,
            "rsi": rsi_arr,
            "kern_bull": kern_bull_arr,
            "kern_bear": kern_bear_arr,
            "ml_bull": ml_bull_arr,
            "ml_bear": ml_bear_arr,
        }

    def _evaluate_bar_signal(self, spec: SymbolSpec, ctx: dict[str, Any], t: int) -> Optional[TradeSignal]:
        if t < 50 or t >= ctx["n"]:
            return None

        atr = float(ctx["atr"][t])
        if atr <= 0 or not np.isfinite(atr):
            return None

        adx_arr = ctx["adx"]
        if adx_arr is not None and float(adx_arr[t]) < self.min_adx:
            return None

        o_t = float(ctx["open"][t])
        c_t = float(ctx["close"][t])
        h_t = float(ctx["high"][t])
        l_t = float(ctx["low"][t])
        fast_t = float(ctx["ema_fast"][t])
        mid_t = float(ctx["ema_mid"][t])
        slow_t = float(ctx["ema_slow"][t])
        rsi_val = float(ctx["rsi"][t])

        min_body = self.min_body_atr * atr
        band = self.pullback_band_atr * atr
        low_arr = ctx["low"]
        high_arr = ctx["high"]

        # Bullish M5 Pullback Setup
        if (
            c_t > mid_t > slow_t
            and bool(ctx["kern_bull"][t])
            and bool(ctx["ml_bull"][t])
            and (c_t - o_t) >= min_body
            and l_t <= fast_t + band
            and self.rsi_buy_min <= rsi_val <= self.rsi_buy_max
        ):
            entry = spec.ask if spec.ask > 0 else c_t
            swing_low = float(np.min(low_arr[max(0, t - 2) : t + 1]))
            raw_dist = (entry - swing_low) + (self.sl_buffer_atr * atr)
            sl_dist = min(self.sl_max_atr * atr, max(self.sl_min_atr * atr, raw_dist, spec.point * 10.0))
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=round(entry, spec.digits),
                sl_price=round(entry - sl_dist, spec.digits),
                tp_price=round(entry + sl_dist * self.tp_rr_multiplier, spec.digits),
                atr=atr,
                reason=f"M5 Trend Pullback Buy (EMA{self.fast_ema_period} + Kernel + RSI {rsi_val:.0f})",
            )

        # Bearish M5 Pullback Setup
        rsi_sell_min = 100.0 - self.rsi_buy_max
        rsi_sell_max = 100.0 - self.rsi_buy_min
        if (
            c_t < mid_t < slow_t
            and bool(ctx["kern_bear"][t])
            and bool(ctx["ml_bear"][t])
            and (o_t - c_t) >= min_body
            and h_t >= fast_t - band
            and rsi_sell_min <= rsi_val <= rsi_sell_max
        ):
            entry = spec.bid if spec.bid > 0 else c_t
            swing_high = float(np.max(high_arr[max(0, t - 2) : t + 1]))
            raw_dist = (swing_high - entry) + (self.sl_buffer_atr * atr)
            sl_dist = min(self.sl_max_atr * atr, max(self.sl_min_atr * atr, raw_dist, spec.point * 10.0))
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.SELL,
                entry_price=round(entry, spec.digits),
                sl_price=round(entry + sl_dist, spec.digits),
                tp_price=round(entry - sl_dist * self.tp_rr_multiplier, spec.digits),
                atr=atr,
                reason=f"M5 Trend Pullback Sell (EMA{self.fast_ema_period} + Kernel + RSI {rsi_val:.0f})",
            )

        return None

    def precompute_backtest_context(self, closed_df: pd.DataFrame) -> Optional[dict[str, Any]]:
        if closed_df is None or len(closed_df) < 60:
            return None
        return self._compute_feature_context(closed_df, eval_all_ml=True)

    def generate_signal_from_context(
        self,
        spec: SymbolSpec,
        ctx: dict[str, Any],
        bar_idx: int,
    ) -> Optional[TradeSignal]:
        if (bar_idx + 1) < self._min_required_bars():
            return None
        return self._evaluate_bar_signal(spec, ctx, bar_idx - 1)

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates is None or len(rates) < self._min_required_bars():
            return None

        closed = rates.iloc[:-1] if len(rates) > 60 else rates
        t = len(closed) - 1
        if t < 50:
            return None
        ctx = self._compute_feature_context(closed, eval_all_ml=False)
        return self._evaluate_bar_signal(spec, ctx, t)
