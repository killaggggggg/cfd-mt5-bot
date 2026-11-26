from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec
from cfd_bot.domain.strategies.lorentzian_ml import (
    _ema_np,
    compute_lorentzian_series,
    compute_normalized_adx,
)
from cfd_bot.domain.strategies.structural_poc import StructuralPocLevel, analyze_structural_legs


class JdubPocLorentzianStrategy(BaseStrategy):
    """
    Pure Offensive combination tuned for M5/M15 accuracy:
      - D (Direction): EMA 200 macro bias + ADX chop filter + jdehorty Lorentzian ML & Kernel Regression
      - L (Location): LuxAlgo Structural Leg Profiler Untested POC liquidity zones (with anti-chase cap)
      - E (Execution): Jdub Trades 3 Entry Models (Touch / Displacement PA / 2-Candle Confirmation) + structural SL/TP
    """

    name = "JdubPocLorentzian"
    display_name_th = "Jdub 3-Entry + Untested POC + Lorentzian ML"
    description_th = (
        "ผสานสูตร DLE ของ Jdub + โซน Untested POC + AI Lorentzian ML & Kernel พร้อมกรองเทรนด์ EMA 200 และ ADX "
        "จูนค่าเริ่มต้นสำหรับ M5 ให้คัดกรองเฉพาะจังหวะคอนเฟิร์มตามเทรนด์ (Win Rate ~55-60%)"
    )

    def __init__(
        self,
        entry_model: int = 3,
        direction_mode: int = 2,
        use_ema200_filter: int = 1,
        min_adx: float = 16.0,
        atr_swing_period: int = 5,
        swing_mult: float = 2.5,
        poc_zone_atr: float = 0.5,
        retest_lookback: int = 4,
        displacement_body_atr: float = 0.25,
        max_overext_atr: float = 1.8,
        ml_neighbors: int = 8,
        kernel_h: int = 8,
        sl_buffer_atr: float = 0.6,
        tp_rr_multiplier: float = 1.3,
        use_opposing_poc_tp: int = 0,
        require_poc_delta: int = 0,
        require_volume_spike: int = 0,
    ) -> None:
        self.entry_model = int(entry_model)
        self.direction_mode = int(direction_mode)
        self.use_ema200_filter = int(use_ema200_filter)
        self.min_adx = float(min_adx)
        self.atr_swing_period = int(atr_swing_period)
        self.swing_mult = float(swing_mult)
        self.poc_zone_atr = float(poc_zone_atr)
        self.retest_lookback = int(retest_lookback)
        self.displacement_body_atr = float(displacement_body_atr)
        self.max_overext_atr = float(max_overext_atr)
        self.ml_neighbors = int(ml_neighbors)
        self.kernel_h = int(kernel_h)
        self.sl_buffer_atr = float(sl_buffer_atr)
        self.tp_rr_multiplier = float(tp_rr_multiplier)
        self.use_opposing_poc_tp = int(use_opposing_poc_tp)
        self.require_poc_delta = int(require_poc_delta)
        self.require_volume_spike = int(require_volume_spike)

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec(
                key="entry_model",
                label_th="โมเดลการเข้าไม้ (Jdub Entry Model)",
                hint_th="สำหรับ M5 แนะนำ Model 3 เพื่อลดสัญญาณหลอกและดัน Win Rate ให้สูงขึ้น",
                kind="int",
                default=3,
                min_val=1,
                max_val=3,
                step=1,
                options=(
                    {"value": 1, "label_th": "Model 1: แตะโซน POC แล้วเข้าทันที (Aggressive)"},
                    {"value": 2, "label_th": "Model 2: แตะ POC + แท่งเทียนกลับตัวแรง PA (Balanced)"},
                    {"value": 3, "label_th": "Model 3: แตะ POC + PA + คอนเฟิร์ม 2 แท่ง (แนะนำสำหรับ M5)"},
                ),
            ),
            StrategyParamSpec(
                key="direction_mode",
                label_th="ตัวกรองทิศทาง Lorentzian / Kernel",
                hint_th="กำหนดการผสาน AI Lorentzian ML และเส้น Kernel Regression",
                kind="int",
                default=2,
                min_val=1,
                max_val=3,
                step=1,
                options=(
                    {"value": 1, "label_th": "Lorentzian ML + Kernel ต้องชี้ทางเดียวกัน (เข้มงวด)"},
                    {"value": 2, "label_th": "Lorentzian หรือ Kernel นำทาง + ไม่สวนฝั่งแรง (สมดุล)"},
                    {"value": 3, "label_th": "Nadaraya-Watson Kernel + Volatility Filter ล้วน"},
                ),
            ),
            StrategyParamSpec(
                key="use_ema200_filter",
                label_th="กรองเทรนด์หลัก EMA 200 (1=เปิด / 0=ปิด)",
                hint_th="1 = บังคับ Buy เฉพาะเหนือ EMA 200 และ Sell เฉพาะใต้ EMA 200 (ลดเทรดสวนเทรนด์บน M5)",
                kind="int",
                default=1,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="min_adx",
                label_th="ความแรงเทรนด์ขั้นต่ำ (ADX)",
                hint_th="กรองตลาดซบเซาออกข้าง (ค่า 16 ช่วยคัดตลาดวิ่งจริงบน M5; ใส่ 0 หากต้องการปิด)",
                kind="float",
                default=16.0,
                min_val=0.0,
                max_val=40.0,
                step=1.0,
            ),
            StrategyParamSpec(
                key="atr_swing_period",
                label_th="คาบ ATR จับโครงสร้าง Leg",
                hint_th="จำนวนแท่ง ATR สำหรับหาคลื่น Swing High/Low เพื่อสร้าง Untested POC (ค่ามาตรฐาน 5)",
                kind="int",
                default=5,
                min_val=3,
                max_val=20,
                step=1,
            ),
            StrategyParamSpec(
                key="swing_mult",
                label_th="ตัวคูณ Swing ZigZag (x ATR)",
                hint_th="ค่า 2.5 กรองสวิงหลอกบน M5 ได้ดีกว่าค่าน้อย และได้ POC ที่มีนัยสำคัญ",
                kind="float",
                default=2.5,
                min_val=1.0,
                max_val=5.0,
                step=0.1,
            ),
            StrategyParamSpec(
                key="poc_zone_atr",
                label_th="ความกว้างโซนแตะ POC (x ATR)",
                hint_th="ระยะเผื่อรอบเส้น Untested POC ที่ถือว่าราคาลงมาทดสอบโซนแล้ว",
                kind="float",
                default=0.5,
                min_val=0.1,
                max_val=1.5,
                step=0.05,
            ),
            StrategyParamSpec(
                key="retest_lookback",
                label_th="กรอบแท่งรอคอนเฟิร์มหลังแตะ POC",
                hint_th="อนุญาตให้แท่ง PA / คอนเฟิร์ม 2 แท่ง เกิดตามมาหลังราคาแตะโซนได้กี่แท่ง",
                kind="int",
                default=4,
                min_val=1,
                max_val=8,
                step=1,
            ),
            StrategyParamSpec(
                key="displacement_body_atr",
                label_th="ขนาดแท่งเทียน PA แรง (x ATR)",
                hint_th="เนื้อเทียนขั้นต่ำเทียบ ATR สำหรับแท่งยืนยันการกลับตัวจากโซน POC",
                kind="float",
                default=0.25,
                min_val=0.1,
                max_val=1.5,
                step=0.05,
            ),
            StrategyParamSpec(
                key="max_overext_atr",
                label_th="เพดานระยะห่างจาก POC สูงสุด (x ATR)",
                hint_th="กันไม่ให้ไล่ราคาถ้าแท่งคอนเฟิร์มวิ่งหนี POC ไปไกลเกิน x เท่าของ ATR แล้ว",
                kind="float",
                default=1.8,
                min_val=0.5,
                max_val=5.0,
                step=0.1,
            ),
            StrategyParamSpec(
                key="ml_neighbors",
                label_th="จำนวนเพื่อนบ้าน Lorentzian ML (K)",
                hint_th="จำนวนแท่งประวัติศาสตร์คล้ายคลึงที่ใช้ทำนายทิศทาง 4 แท่งข้างหน้า",
                kind="int",
                default=8,
                min_val=3,
                max_val=24,
                step=1,
            ),
            StrategyParamSpec(
                key="kernel_h",
                label_th="ความยาวเส้น Kernel (h)",
                hint_th="ความยาวเฉลี่ยถ่วงน้ำหนัก Nadaraya-Watson Kernel",
                kind="int",
                default=8,
                min_val=3,
                max_val=30,
                step=1,
            ),
            StrategyParamSpec(
                key="sl_buffer_atr",
                label_th="ระยะเผื่อ SL หลังสวิง (x ATR)",
                hint_th="วาง SL เลยปลายสวิงออกไปอีกเพื่อลดการโดนไส้เทียน M5 กวาด Stop Loss",
                kind="float",
                default=0.6,
                min_val=0.1,
                max_val=1.8,
                step=0.05,
            ),
            StrategyParamSpec(
                key="tp_rr_multiplier",
                label_th="เป้ากำไร R:R",
                hint_th="บน M5 ช่วง 1.2 - 1.5 ช่วยรักษา Win Rate สูงกว่าเป้าหมายที่ไกลเกิน 2.0",
                kind="float",
                default=1.3,
                min_val=0.8,
                max_val=5.0,
                step=0.05,
            ),
            StrategyParamSpec(
                key="use_opposing_poc_tp",
                label_th="ขยาย TP ไป POC ฝั่งตรงข้าม (1=เปิด / 0=ใช้ R:R คงที่)",
                hint_th="0 = ปิดเพื่อเก็บตาม R:R สม่ำเสมอ, 1 = ลากเป้าไกลขึ้นถ้ามี POC ฝั่งตรงข้าม",
                kind="int",
                default=0,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="require_poc_delta",
                label_th="กรอง Net Delta ตามฝั่ง POC (1=เปิด / 0=ปิด)",
                hint_th="1 = บังคับให้แรงซื้อ/ขายสะสมในคลื่น POC ต้องสอดคล้องทิศ (เด่นใน EURUSD M5)",
                kind="int",
                default=0,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="require_volume_spike",
                label_th="บังคับต้องมี Volume Spike (1=เปิด / 0=ปิด)",
                hint_th="1 = ต้องมีแท่งปริมาณซื้อง/ขายพุ่ง >= 2x SMA20 ตอนแตะโซน POC ถึงจะเข้าไม้",
                kind="int",
                default=0,
                min_val=0,
                max_val=1,
                step=1,
            ),
        ]

    @staticmethod
    def _find_recent_touched_poc(
        pocs: list[StructuralPocLevel],
        side: str,
        t: int,
        retest_lookback: int,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        zone_band: float,
        max_overext_dist: float,
        require_delta_match: bool,
    ) -> Optional[tuple[StructuralPocLevel, int]]:
        candidates = [
            p
            for p in pocs
            if p.side == side
            and p.end_bar_idx < t - 1
            and (p.tested_bar_idx is None or (t - retest_lookback <= p.tested_bar_idx <= t))
        ]
        if not candidates:
            return None

        for p in reversed(candidates[-3:]):
            if require_delta_match:
                if side == "BULL" and p.net_delta <= 0.0:
                    continue
                if side == "BEAR" and p.net_delta >= 0.0:
                    continue

            c_t = float(close[t])
            if side == "BULL" and (c_t < p.leg_low or (c_t - p.price) > max_overext_dist):
                continue
            if side == "BEAR" and (c_t > p.leg_high or (p.price - c_t) > max_overext_dist):
                continue

            win_start = max(p.end_bar_idx + 1, t - retest_lookback)
            touch_bar: Optional[int] = None
            for k in range(t, win_start - 1, -1):
                if float(low[k]) <= p.price + zone_band and float(high[k]) >= p.price - zone_band:
                    touch_bar = k
                    break
            if touch_bar is not None:
                return p, touch_bar
        return None

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates is None or len(rates) < 55:
            return None

        closed = rates.iloc[:-1] if len(rates) > 55 else rates
        n = len(closed)
        t = n - 1
        if t < 40:
            return None

        poc_analysis = analyze_structural_legs(
            closed_df=closed,
            atr_period=self.atr_swing_period,
            swing_mult=self.swing_mult,
            max_boxes=50,
            max_active_per_side=3,
            bubble_mult=2.0,
            min_tick=max(spec.point, 1e-5),
            server_utc_offset_seconds=spec.server_utc_offset_seconds or 0,
            compute_timestamps=False,
        )
        atr14 = float(poc_analysis.atr14[t])
        if atr14 <= 0 or not np.isfinite(atr14):
            return None

        open_arr = closed["open"].to_numpy(dtype=float)
        high_arr = closed["high"].to_numpy(dtype=float)
        low_arr = closed["low"].to_numpy(dtype=float)
        close_arr = closed["close"].to_numpy(dtype=float)

        if self.min_adx > 0.0:
            adx_arr = compute_normalized_adx(high_arr, low_arr, close_arr, 14) * 100.0
            if float(adx_arr[t]) < self.min_adx:
                return None

        ema200_ok_bull = True
        ema200_ok_bear = True
        if self.use_ema200_filter and n >= 60:
            ema200_arr = _ema_np(close_arr, min(200, n))
            ema200_ok_bull = float(close_arr[t]) > float(ema200_arr[t])
            ema200_ok_bear = float(close_arr[t]) < float(ema200_arr[t])

        ml_out = compute_lorentzian_series(
            closed_df=closed,
            neighbors_count=self.ml_neighbors,
            max_bars_back=min(220, max(40, n - 15)),
            use_volatility_filter=True,
            use_regime_filter=True,
            use_kernel_filter=True,
            kernel_h=self.kernel_h,
            kernel_r=8.0,
            eval_tail_bars=30,
        )

        # Direction (D)
        ml_sig = int(ml_out.signals[t])
        ml_pred = float(ml_out.predictions[t])
        ml_bull = ml_sig == 1 or ml_pred > 0
        ml_bear = ml_sig == -1 or ml_pred < 0
        kern_bull = bool(ml_out.kernel_bullish[t])
        kern_bear = bool(ml_out.kernel_bearish[t])

        if self.direction_mode == 1:
            dir_bull = (ml_sig == 1) and kern_bull and ema200_ok_bull
            dir_bear = (ml_sig == -1) and kern_bear and ema200_ok_bear
        elif self.direction_mode == 3:
            dir_bull = kern_bull and bool(ml_out.filter_ok[t]) and ema200_ok_bull
            dir_bear = kern_bear and bool(ml_out.filter_ok[t]) and ema200_ok_bear
        else:
            dir_bull = (ml_bull or kern_bull) and not (ml_sig == -1 and kern_bear) and ema200_ok_bull
            dir_bear = (ml_bear or kern_bear) and not (ml_sig == 1 and kern_bull) and ema200_ok_bear

        if not dir_bull and not dir_bear:
            return None

        zone_band = max(self.poc_zone_atr * atr14, spec.point * 10.0)
        max_overext_dist = max(self.max_overext_atr * atr14, spec.point * 20.0)
        spike_bars = {int(s["bar_idx"]) for s in poc_analysis.volume_spikes}

        def _has_spike(start_k: int) -> bool:
            return any(k in spike_bars for k in range(max(0, start_k), t + 1))

        buy_match = (
            self._find_recent_touched_poc(
                poc_analysis.pocs,
                "BULL",
                t,
                self.retest_lookback,
                high_arr,
                low_arr,
                close_arr,
                zone_band,
                max_overext_dist,
                bool(self.require_poc_delta),
            )
            if dir_bull
            else None
        )
        sell_match = (
            self._find_recent_touched_poc(
                poc_analysis.pocs,
                "BEAR",
                t,
                self.retest_lookback,
                high_arr,
                low_arr,
                close_arr,
                zone_band,
                max_overext_dist,
                bool(self.require_poc_delta),
            )
            if dir_bear
            else None
        )

        body_curr = close_arr[t] - open_arr[t]
        min_disp = self.displacement_body_atr * atr14

        chosen_side: Optional[OrderSide] = None
        chosen_poc: Optional[StructuralPocLevel] = None
        chosen_touch_bar = t

        if buy_match is not None:
            poc, touch_bar = buy_match
            if not (self.require_volume_spike and not _has_spike(touch_bar - 1)):
                ok_exec = False
                if self.entry_model == 1:
                    ok_exec = touch_bar == t and close_arr[t] >= poc.price - zone_band * 0.25 and body_curr >= 0
                elif self.entry_model == 3:
                    body_prev = close_arr[t - 1] - open_arr[t - 1]
                    ok_exec = (
                        body_prev > 0
                        and body_curr >= min_disp
                        and close_arr[t] > high_arr[t - 1]
                        and close_arr[t] > poc.price
                    )
                else:
                    ok_exec = body_curr >= min_disp and close_arr[t] > poc.price
                if ok_exec:
                    chosen_side = OrderSide.BUY
                    chosen_poc = poc
                    chosen_touch_bar = touch_bar

        if chosen_side is None and sell_match is not None:
            poc, touch_bar = sell_match
            if not (self.require_volume_spike and not _has_spike(touch_bar - 1)):
                ok_exec = False
                bear_body = open_arr[t] - close_arr[t]
                if self.entry_model == 1:
                    ok_exec = touch_bar == t and close_arr[t] <= poc.price + zone_band * 0.25 and bear_body >= 0
                elif self.entry_model == 3:
                    bear_prev = open_arr[t - 1] - close_arr[t - 1]
                    ok_exec = (
                        bear_prev > 0
                        and bear_body >= min_disp
                        and close_arr[t] < low_arr[t - 1]
                        and close_arr[t] < poc.price
                    )
                else:
                    ok_exec = bear_body >= min_disp and close_arr[t] < poc.price
                if ok_exec:
                    chosen_side = OrderSide.SELL
                    chosen_poc = poc
                    chosen_touch_bar = touch_bar

        if chosen_side is None or chosen_poc is None:
            return None

        spike_tag = " + VolSpike" if _has_spike(chosen_touch_bar - 1) else ""
        model_tag = f"M{self.entry_model}"

        if chosen_side == OrderSide.BUY:
            entry = spec.ask if spec.ask > 0 else float(close_arr[t])
            struct_low = float(np.min(low_arr[max(0, chosen_touch_bar - 1) : t + 1]))
            raw_sl = min(struct_low, chosen_poc.price) - self.sl_buffer_atr * atr14
            sl_dist = max(0.8 * atr14, min(3.2 * atr14, entry - raw_sl), spec.point * 10.0)
            sl_price = entry - sl_dist
            min_tp = entry + sl_dist * self.tp_rr_multiplier
            tp_price = min_tp
            if self.use_opposing_poc_tp:
                opp_targets = [
                    p.price - self.sl_buffer_atr * atr14
                    for p in poc_analysis.untested_pocs
                    if p.side == "BEAR"
                    and (p.price - self.sl_buffer_atr * atr14) >= min_tp
                    and (p.price - entry) <= sl_dist * self.tp_rr_multiplier * 2.5
                ]
                if opp_targets:
                    tp_price = min(opp_targets)
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=round(entry, spec.digits),
                sl_price=round(sl_price, spec.digits),
                tp_price=round(tp_price, spec.digits),
                atr=atr14,
                reason=f"Jdub {model_tag} Bull POC ({chosen_poc.price:.2f}) + ML({ml_pred:+.0f}){spike_tag}",
                metadata={"poc_price": round(chosen_poc.price, spec.digits), "entry_model": self.entry_model},
            )

        entry = spec.bid if spec.bid > 0 else float(close_arr[t])
        struct_high = float(np.max(high_arr[max(0, chosen_touch_bar - 1) : t + 1]))
        raw_sl = max(struct_high, chosen_poc.price) + self.sl_buffer_atr * atr14
        sl_dist = max(0.8 * atr14, min(3.2 * atr14, raw_sl - entry), spec.point * 10.0)
        sl_price = entry + sl_dist
        min_tp = entry - sl_dist * self.tp_rr_multiplier
        tp_price = min_tp
        if self.use_opposing_poc_tp:
            opp_targets = [
                p.price + self.sl_buffer_atr * atr14
                for p in poc_analysis.untested_pocs
                if p.side == "BULL"
                and (p.price + self.sl_buffer_atr * atr14) <= min_tp
                and (entry - p.price) <= sl_dist * self.tp_rr_multiplier * 2.5
            ]
            if opp_targets:
                tp_price = max(opp_targets)
        return TradeSignal(
            symbol=spec.name,
            side=OrderSide.SELL,
            entry_price=round(entry, spec.digits),
            sl_price=round(sl_price, spec.digits),
            tp_price=round(tp_price, spec.digits),
            atr=atr14,
            reason=f"Jdub {model_tag} Bear POC ({chosen_poc.price:.2f}) + ML({ml_pred:+.0f}){spike_tag}",
            metadata={"poc_price": round(chosen_poc.price, spec.digits), "entry_model": self.entry_model},
        )
