from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.models import OrderSide, SymbolSpec, TradeSignal
from cfd_bot.domain.strategies.base import BaseStrategy, StrategyParamSpec


def _ema_np(values: np.ndarray, period: int) -> np.ndarray:
    n = len(values)
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out
    alpha = 2.0 / (max(1, period) + 1.0)
    out[0] = float(values[0])
    for i in range(1, n):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def _rma_np(values: np.ndarray, period: int) -> np.ndarray:
    n = len(values)
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out
    p = max(1, period)
    alpha = 1.0 / p
    out[0] = float(values[0])
    for i in range(1, n):
        out[i] = alpha * float(values[i]) + (1.0 - alpha) * out[i - 1]
    return out


def _sma_np(values: np.ndarray, period: int) -> np.ndarray:
    p = max(1, period)
    return pd.Series(values).rolling(window=p, min_periods=1).mean().to_numpy(dtype=float)


def compute_atr_array(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    if n == 0:
        return np.zeros(0, dtype=float)
    prev_close = np.empty(n, dtype=float)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    return _rma_np(tr, period)


def compute_normalized_rsi(close: np.ndarray, n1: int = 14, n2: int = 1) -> np.ndarray:
    n = len(close)
    if n < 2:
        return np.full(n, 0.5, dtype=float)
    diff = np.diff(close, prepend=close[0])
    gains = np.where(diff > 0.0, diff, 0.0)
    losses = np.where(diff < 0.0, -diff, 0.0)
    avg_gain = _rma_np(gains, n1)
    avg_loss = _rma_np(losses, n1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 1e-12, avg_gain / avg_loss, 100.0)
        rsi = np.where(avg_loss <= 1e-12, np.where(avg_gain > 1e-12, 100.0, 50.0), 100.0 - (100.0 / (1.0 + rs)))
    smoothed = _ema_np(rsi, max(1, n2))
    return np.clip(smoothed / 100.0, 0.0, 1.0)


def compute_normalized_wt(hlc3: np.ndarray, n1: int = 10, n2: int = 11) -> np.ndarray:
    esa = _ema_np(hlc3, n1)
    d = _ema_np(np.abs(hlc3 - esa), n1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ci = np.where(d > 1e-12, (hlc3 - esa) / (0.015 * d), 0.0)
    wt1 = _ema_np(ci, n2)
    wt2 = _sma_np(wt1, 4)
    wt = wt1 - wt2
    # Bounded smooth rescale into [0, 1] comparable across symbols without future leak
    return np.clip(0.5 + 0.5 * np.tanh(wt / 40.0), 0.0, 1.0)


def compute_normalized_cci(close: np.ndarray, n1: int = 20, n2: int = 1) -> np.ndarray:
    n = len(close)
    p = max(2, int(n1))
    sma_arr = _sma_np(close, p)
    mad_arr = np.zeros(n, dtype=float)
    if n >= p:
        wins = np.lib.stride_tricks.sliding_window_view(close, p)
        mad_arr[p - 1 :] = np.mean(np.abs(wins - sma_arr[p - 1 :, None]), axis=1)
    for i in range(1, min(p - 1, n)):
        w = close[: i + 1]
        mad_arr[i] = float(np.mean(np.abs(w - np.mean(w))))
    with np.errstate(divide="ignore", invalid="ignore"):
        cci = np.where(mad_arr > 1e-12, (close - sma_arr) / (0.015 * mad_arr), 0.0)
    smoothed = _ema_np(cci, max(1, n2))
    return np.clip(0.5 + 0.5 * np.tanh(smoothed / 150.0), 0.0, 1.0)


def compute_normalized_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n1: int = 20) -> np.ndarray:
    n = len(close)
    if n < 2:
        return np.full(n, 0.2, dtype=float)
    up_move = np.diff(high, prepend=high[0])
    dn_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > dn_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0.0), dn_move, 0.0)
    prev_close = np.empty(n, dtype=float)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr_s = _rma_np(tr, n1)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(tr_s > 1e-12, 100.0 * _rma_np(plus_dm, n1) / tr_s, 0.0)
        minus_di = np.where(tr_s > 1e-12, 100.0 * _rma_np(minus_dm, n1) / tr_s, 0.0)
        di_sum = plus_di + minus_di
        dx = np.where(di_sum > 1e-12, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)
    adx = _rma_np(dx, n1)
    return np.clip(adx / 100.0, 0.0, 1.0)


def causal_rational_quadratic_kernel(src: np.ndarray, h: int = 8, r: float = 8.0, window: int = 60) -> np.ndarray:
    """Strictly causal Nadaraya-Watson Rational Quadratic Kernel regression over prior closed bars."""
    n = len(src)
    if n == 0:
        return np.zeros(0, dtype=float)
    h_eff = max(1.0, float(h))
    r_eff = max(0.1, float(r))
    max_lag = max(5, min(window, n - 1))
    lags = np.arange(0, max_lag + 1, dtype=float)
    weights = np.power(1.0 + (lags ** 2) / (2.0 * r_eff * (h_eff ** 2)), -r_eff)

    out = np.empty(n, dtype=float)
    for t in range(n):
        k = min(t, max_lag) + 1
        w = weights[:k]
        # src slice in reverse distance order: src[t], src[t-1], ...
        vals = src[t::-1][:k]
        denom = float(np.sum(w))
        out[t] = float(np.dot(w, vals) / denom) if denom > 0 else float(src[t])
    return out


def causal_gaussian_kernel(src: np.ndarray, h: int = 6, window: int = 60) -> np.ndarray:
    """Strictly causal Nadaraya-Watson Gaussian Kernel regression over prior closed bars."""
    n = len(src)
    if n == 0:
        return np.zeros(0, dtype=float)
    h_eff = max(1.0, float(h))
    max_lag = max(5, min(window, n - 1))
    lags = np.arange(0, max_lag + 1, dtype=float)
    weights = np.exp(-(lags ** 2) / (2.0 * (h_eff ** 2)))

    out = np.empty(n, dtype=float)
    for t in range(n):
        k = min(t, max_lag) + 1
        w = weights[:k]
        vals = src[t::-1][:k]
        denom = float(np.sum(w))
        out[t] = float(np.dot(w, vals) / denom) if denom > 0 else float(src[t])
    return out


@dataclass(frozen=True)
class LorentzianSeriesOutput:
    predictions: np.ndarray
    signals: np.ndarray
    kernel_rq: np.ndarray
    kernel_gauss: np.ndarray
    kernel_bullish: np.ndarray
    kernel_bearish: np.ndarray
    filter_ok: np.ndarray
    atr14: np.ndarray


def compute_lorentzian_series(
    closed_df: pd.DataFrame,
    neighbors_count: int = 8,
    max_bars_back: int = 220,
    use_volatility_filter: bool = True,
    use_regime_filter: bool = True,
    regime_threshold: float = -0.1,
    use_kernel_filter: bool = True,
    use_kernel_smoothing: bool = False,
    kernel_h: int = 8,
    kernel_r: float = 8.0,
    kernel_lag: int = 2,
    eval_tail_bars: Optional[int] = None,
) -> LorentzianSeriesOutput:
    """Computes causal jdehorty Lorentzian KNN predictions + Nadaraya-Watson Kernels on closed bars."""
    high = closed_df["high"].to_numpy(dtype=float)
    low = closed_df["low"].to_numpy(dtype=float)
    close = closed_df["close"].to_numpy(dtype=float)
    hlc3 = (high + low + close) / 3.0
    n = len(close)

    atr14 = compute_atr_array(high, low, close, 14)
    atr1 = compute_atr_array(high, low, close, 1)
    atr10 = compute_atr_array(high, low, close, 10)

    # Default 5 normalized features matching Lorentzian Classification script
    f1 = compute_normalized_rsi(close, 14, 1)
    f2 = compute_normalized_wt(hlc3, 10, 11)
    f3 = compute_normalized_cci(close, 20, 1)
    f4 = compute_normalized_adx(high, low, close, 20)
    f5 = compute_normalized_rsi(close, 9, 1)
    features = np.column_stack([f1, f2, f3, f4, f5])

    # Precompute causal kernels
    krq = causal_rational_quadratic_kernel(close, h=kernel_h, r=kernel_r, window=max(30, kernel_h * 6))
    kgauss = causal_gaussian_kernel(close, h=max(2, kernel_h - kernel_lag), window=max(30, kernel_h * 6))

    # Slope filter & Volatility/Regime filters
    vol_ok = (atr1 > atr10 * 0.75) if use_volatility_filter else np.ones(n, dtype=bool)
    if use_regime_filter:
        slope = np.empty(n, dtype=float)
        slope[0] = 0.0
        slope[1:] = np.where(atr14[1:] > 1e-12, (krq[1:] - krq[:-1]) / atr14[1:], 0.0)
        # Positive slope or mild pullback vs threshold captures non-dead regime
        regime_ok = np.abs(slope) >= max(0.0, -float(regime_threshold) * 0.15) if regime_threshold <= 0 else slope >= float(regime_threshold)
    else:
        regime_ok = np.ones(n, dtype=bool)
    filter_ok = vol_ok & regime_ok

    if use_kernel_filter:
        if use_kernel_smoothing:
            kernel_bullish = kgauss >= krq
            kernel_bearish = kgauss <= krq
        else:
            kernel_bullish = np.zeros(n, dtype=bool)
            kernel_bearish = np.zeros(n, dtype=bool)
            if n > 1:
                kernel_bullish[1:] = krq[1:] >= krq[:-1]
                kernel_bearish[1:] = krq[1:] <= krq[:-1]
    else:
        kernel_bullish = np.ones(n, dtype=bool)
        kernel_bearish = np.ones(n, dtype=bool)

    # Strictly causal y_train[i] = direction 4 bars AFTER i (only valid for i <= t - 4)
    y_train = np.zeros(n, dtype=int)
    if n > 4:
        diff4 = close[4:] - close[:-4]
        y_train[:-4] = np.where(diff4 > 0.0, 1, np.where(diff4 < 0.0, -1, 0))

    preds = np.zeros(n, dtype=float)
    signals = np.zeros(n, dtype=int)
    k_neighbors = max(2, int(neighbors_count))
    k_q_idx = int(round(k_neighbors * 3 / 4))
    lookback = max(40, int(max_bars_back))
    horizon = 4

    # Chronological index mask (i % 4 != 0 reduces collinear neighbor crowding)
    valid_step_mask = (np.arange(n) % 4) != 0
    eval_start = 25 if eval_tail_bars is None else max(25, n - max(10, int(eval_tail_bars)))

    for t in range(eval_start, n):
        max_hist_idx = t - horizon
        if max_hist_idx < 21:
            signals[t] = signals[t - 1]
            continue
        start_idx = max(14, t - lookback)
        cand_mask = valid_step_mask[start_idx : max_hist_idx + 1]
        cand_indices = np.nonzero(cand_mask)[0] + start_idx
        if len(cand_indices) == 0:
            signals[t] = signals[t - 1]
            continue

        # Vectorized Lorentzian distance across candidate historical bars
        dists = np.sum(np.log1p(np.abs(features[cand_indices] - features[t])), axis=1)

        # jdehorty ANN priority scan maintaining non-overlapping neighbor selection
        last_dist = -1.0
        buf_d: list[float] = []
        buf_y: list[int] = []
        for pos in range(len(cand_indices)):
            d = float(dists[pos])
            if d >= last_dist:
                last_dist = d
                buf_d.append(d)
                buf_y.append(int(y_train[cand_indices[pos]]))
                if len(buf_y) > k_neighbors:
                    q = min(k_q_idx, len(buf_d) - 1)
                    last_dist = buf_d[q]
                    buf_d.pop(0)
                    buf_y.pop(0)

        pred = float(sum(buf_y)) if buf_y else 0.0
        preds[t] = pred

        if pred > 0.0 and filter_ok[t]:
            signals[t] = 1
        elif pred < 0.0 and filter_ok[t]:
            signals[t] = -1
        else:
            signals[t] = signals[t - 1]

    return LorentzianSeriesOutput(
        predictions=preds,
        signals=signals,
        kernel_rq=krq,
        kernel_gauss=kgauss,
        kernel_bullish=kernel_bullish,
        kernel_bearish=kernel_bearish,
        filter_ok=filter_ok,
        atr14=atr14,
    )


class LorentzianClassifierStrategy(BaseStrategy):
    """Pluggable pure Offensive strategy implementing jdehorty Lorentzian Classification + Kernel Regression."""

    name = "LorentzianClassifier"
    display_name_th = "Lorentzian ML + Kernel Regression"
    description_th = (
        "ใช้ AI แบบ K-Nearest Neighbors วัดระยะทาง Lorentzian Distance เปรียบเทียบแพตเทิร์นย้อนหลังด้วย 5 อินดิเคเตอร์ "
        "(RSI, WaveTrend, CCI, ADX) กรองทิศทางด้วย Nadaraya-Watson Kernel Regression และสภาวะผันผวน"
    )

    def __init__(
        self,
        neighbors_count: int = 8,
        max_bars_back: int = 220,
        kernel_h: int = 8,
        kernel_r: float = 8.0,
        use_volatility_filter: int = 1,
        use_kernel_filter: int = 1,
        atr_sl_multiplier: float = 1.6,
        tp_rr_multiplier: float = 2.0,
    ) -> None:
        self.neighbors_count = int(neighbors_count)
        self.max_bars_back = int(max_bars_back)
        self.kernel_h = int(kernel_h)
        self.kernel_r = float(kernel_r)
        self.use_volatility_filter = int(use_volatility_filter)
        self.use_kernel_filter = int(use_kernel_filter)
        self.atr_sl_multiplier = float(atr_sl_multiplier)
        self.tp_rr_multiplier = float(tp_rr_multiplier)

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        return [
            StrategyParamSpec(
                key="neighbors_count",
                label_th="จำนวนเพื่อนบ้าน ML (Neighbors K)",
                hint_th="จำนวนแพตเทิร์นอดีตที่คล้ายสุดเพื่อนำมาโหวตทิศทาง (มาตรฐาน 8)",
                kind="int",
                default=8,
                min_val=3,
                max_val=30,
                step=1,
            ),
            StrategyParamSpec(
                key="max_bars_back",
                label_th="ขอบเขตแท่งประวัติศาสตร์ ML",
                hint_th="จำนวนแท่งย้อนหลังที่ใช้ฝึกจับแพตเทิร์นให้ ML เรียนรู้",
                kind="int",
                default=220,
                min_val=60,
                max_val=500,
                step=20,
            ),
            StrategyParamSpec(
                key="kernel_h",
                label_th="ความยาวเส้น Kernel (Lookback h)",
                hint_th="ค่าน้อยเส้น Kernel ไวต่อราคา ค่ามากเส้นจะเรียบและจับเทรนด์ใหญ่",
                kind="int",
                default=8,
                min_val=3,
                max_val=40,
                step=1,
            ),
            StrategyParamSpec(
                key="kernel_r",
                label_th="น้ำหนัก Kernel (Relative Weight r)",
                hint_th="ปรับความสมดุลระหว่างคลื่นระยะสั้นกับระยะยาวของ Rational Quadratic Kernel",
                kind="float",
                default=8.0,
                min_val=0.5,
                max_val=25.0,
                step=0.5,
            ),
            StrategyParamSpec(
                key="use_volatility_filter",
                label_th="กรองความผันผวน (1=เปิด / 0=ปิด)",
                hint_th="1 = งดเข้าเมื่อตลาดซบเซามากผิดปกติ, 0 = ปิดตัวกรองเพื่อจับทุกสัญญาณ",
                kind="int",
                default=1,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="use_kernel_filter",
                label_th="กรองตามเส้น Kernel (1=เปิด / 0=ปิด)",
                hint_th="1 = ต้องสอดคล้องความชัน Nadaraya-Watson Kernel, 0 = ใช้สัญญาณ ML ล้วน",
                kind="int",
                default=1,
                min_val=0,
                max_val=1,
                step=1,
            ),
            StrategyParamSpec(
                key="atr_sl_multiplier",
                label_th="ตัวคูณระยะ SL (x ATR)",
                hint_th="ตั้งระยะ Stop Loss กว้างกี่เท่าของความผันผวน ATR(14)",
                kind="float",
                default=1.6,
                min_val=0.5,
                max_val=5.0,
                step=0.1,
            ),
            StrategyParamSpec(
                key="tp_rr_multiplier",
                label_th="เป้ากำไร R:R",
                hint_th="อัตราส่วนระยะ TP ต่อระยะ SL (เช่น 2.0 = เสี่ยง 1 เอา 2)",
                kind="float",
                default=2.0,
                min_val=0.8,
                max_val=6.0,
                step=0.1,
            ),
        ]

    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        if rates is None or len(rates) < 40:
            return None

        closed = rates.iloc[:-1] if len(rates) > 40 else rates
        if len(closed) < 35:
            return None

        ml_out = compute_lorentzian_series(
            closed_df=closed,
            neighbors_count=self.neighbors_count,
            max_bars_back=self.max_bars_back,
            use_volatility_filter=bool(self.use_volatility_filter),
            use_regime_filter=True,
            use_kernel_filter=bool(self.use_kernel_filter),
            kernel_h=self.kernel_h,
            kernel_r=self.kernel_r,
            eval_tail_bars=32,
        )
        t = len(closed) - 1
        if t < 1:
            return None

        sig_curr = int(ml_out.signals[t])
        sig_prev = int(ml_out.signals[t - 1])
        is_new_buy = sig_curr == 1 and sig_prev != 1 and bool(ml_out.kernel_bullish[t])
        is_new_sell = sig_curr == -1 and sig_prev != -1 and bool(ml_out.kernel_bearish[t])

        if not is_new_buy and not is_new_sell:
            return None

        atr = float(ml_out.atr14[t])
        if atr <= 0 or pd.isna(atr):
            return None

        sl_dist = max(atr * self.atr_sl_multiplier, spec.point * 10.0)
        tp_dist = sl_dist * self.tp_rr_multiplier
        pred_score = float(ml_out.predictions[t])

        if is_new_buy:
            entry = spec.ask if spec.ask > 0 else float(closed["close"].iloc[-1])
            return TradeSignal(
                symbol=spec.name,
                side=OrderSide.BUY,
                entry_price=round(entry, spec.digits),
                sl_price=round(entry - sl_dist, spec.digits),
                tp_price=round(entry + tp_dist, spec.digits),
                atr=atr,
                reason=f"Lorentzian Buy Flip (ML Score +{pred_score:.0f}, Kernel Bullish)",
            )

        entry = spec.bid if spec.bid > 0 else float(closed["close"].iloc[-1])
        return TradeSignal(
            symbol=spec.name,
            side=OrderSide.SELL,
            entry_price=round(entry, spec.digits),
            sl_price=round(entry + sl_dist, spec.digits),
            tp_price=round(entry - tp_dist, spec.digits),
            atr=atr,
            reason=f"Lorentzian Sell Flip (ML Score {pred_score:.0f}, Kernel Bearish)",
        )
