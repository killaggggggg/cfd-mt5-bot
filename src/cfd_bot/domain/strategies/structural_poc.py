from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from cfd_bot.domain.strategies.lorentzian_ml import compute_atr_array


@dataclass
class StructuralPocLevel:
    """Volume-profile Point of Control (POC) derived from an ATR structural swing leg."""

    side: Literal["BULL", "BEAR"]
    price: float
    leg_low: float
    leg_high: float
    start_bar_idx: int
    end_bar_idx: int
    start_time: int
    end_time: int
    total_volume: float
    net_delta: float
    tested_bar_idx: Optional[int] = None


@dataclass(frozen=True)
class StructuralLegAnalysis:
    """Outputs of LuxAlgo Structural Leg Profiler ported causally to Python."""

    pocs: list[StructuralPocLevel]
    untested_pocs: list[StructuralPocLevel]
    volume_spikes: list[dict[str, float | int]]
    atr5: np.ndarray
    atr14: np.ndarray


def _row_volume(df: pd.DataFrame) -> np.ndarray:
    if "tick_volume" in df.columns:
        return df["tick_volume"].to_numpy(dtype=float)
    if "real_volume" in df.columns:
        return df["real_volume"].to_numpy(dtype=float)
    if "volume" in df.columns:
        return df["volume"].to_numpy(dtype=float)
    return np.ones(len(df), dtype=float)


def _row_epoch_seconds(times: pd.Series, server_utc_offset_seconds: Optional[int] = 0) -> np.ndarray:
    offset = int(server_utc_offset_seconds or 0)
    return np.array([int(pd.Timestamp(v).timestamp()) - offset for v in times], dtype=int)


def _build_leg_poc(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    vol: np.ndarray,
    times: Optional[np.ndarray],
    start_idx: int,
    end_idx: int,
    is_up_leg: bool,
    base_box_height: float,
    max_boxes: int,
    min_tick: float,
) -> Optional[StructuralPocLevel]:
    if end_idx <= start_idx or start_idx < 0 or end_idx >= len(close):
        return None

    seg_h = high[start_idx : end_idx + 1]
    seg_l = low[start_idx : end_idx + 1]
    seg_c = close[start_idx : end_idx + 1]
    seg_v = vol[start_idx : end_idx + 1]

    leg_high = float(np.max(seg_h))
    leg_low = float(np.min(seg_l))
    leg_range = leg_high - leg_low
    if leg_range <= min_tick:
        return None

    box_h = max(base_box_height, min_tick)
    num_boxes = max(1, min(max_boxes, int(round(leg_range / box_h)) or 1))
    adj_box_h = leg_range / num_boxes

    # Vectorized bin overlap calculation across all bars in this structural leg
    box_edges = leg_low + np.arange(num_boxes, dtype=float) * adj_box_h
    box_tops = box_edges + adj_box_h

    clipped_v = np.maximum(1.0, seg_v)
    bar_rng = np.maximum(seg_h - seg_l, min_tick)
    buy_v = clipped_v * np.clip((seg_c - seg_l) / bar_rng, 0.0, 1.0)
    sell_v = clipped_v * np.clip((seg_h - seg_c) / bar_rng, 0.0, 1.0)
    total_buy = float(np.sum(buy_v))
    total_sell = float(np.sum(sell_v))

    overlaps = np.maximum(
        0.0,
        np.minimum(box_tops[None, :], seg_h[:, None]) - np.maximum(box_edges[None, :], seg_l[:, None]),
    )
    vol_dist = np.sum(overlaps * (clipped_v / bar_rng)[:, None], axis=0)

    max_vol_idx = int(np.argmax(vol_dist))
    poc_price = leg_low + (max_vol_idx * adj_box_h) + (adj_box_h / 2.0)

    return StructuralPocLevel(
        side="BULL" if is_up_leg else "BEAR",
        price=float(poc_price),
        leg_low=leg_low,
        leg_high=leg_high,
        start_bar_idx=start_idx,
        end_bar_idx=end_idx,
        start_time=int(times[start_idx]) if times is not None else 0,
        end_time=int(times[end_idx]) if times is not None else 0,
        total_volume=float(total_buy + total_sell),
        net_delta=float(total_buy - total_sell),
        tested_bar_idx=None,
    )


def analyze_structural_legs(
    closed_df: pd.DataFrame,
    atr_period: int = 5,
    swing_mult: float = 2.5,
    max_boxes: int = 50,
    max_active_per_side: int = 2,
    bubble_mult: float = 2.0,
    min_tick: float = 0.01,
    server_utc_offset_seconds: Optional[int] = 0,
    compute_timestamps: bool = True,
) -> StructuralLegAnalysis:
    """Detects ATR ZigZag structural legs, computes leg POC levels, tracks untested mitigation & volume spikes."""
    n = len(closed_df)
    if n < 10:
        empty = np.zeros(n, dtype=float)
        return StructuralLegAnalysis(pocs=[], untested_pocs=[], volume_spikes=[], atr5=empty, atr14=empty)

    high = closed_df["high"].to_numpy(dtype=float)
    low = closed_df["low"].to_numpy(dtype=float)
    close = closed_df["close"].to_numpy(dtype=float)
    open_arr = closed_df["open"].to_numpy(dtype=float)
    vol = _row_volume(closed_df)
    times = _row_epoch_seconds(closed_df["time"], server_utc_offset_seconds) if compute_timestamps else None

    atr5 = compute_atr_array(high, low, close, max(2, atr_period))
    atr14 = compute_atr_array(high, low, close, 14)

    vol_sma20 = pd.Series(vol).rolling(window=20, min_periods=5).mean().to_numpy(dtype=float)
    volume_spikes: list[dict[str, float | int]] = []

    zz_dir = 1
    zz_high = float(high[0])
    zz_high_bar = 0
    zz_low = float(low[0])
    zz_low_bar = 0
    last_pivot_bar = 0

    all_pocs: list[StructuralPocLevel] = []
    active_pocs: list[StructuralPocLevel] = []

    for i in range(1, n):
        # 1. Volume spike check
        if np.isfinite(vol_sma20[i]) and vol_sma20[i] > 0:
            ratio = float(vol[i] / vol_sma20[i])
            if ratio >= bubble_mult:
                volume_spikes.append(
                    {
                        "bar_idx": i,
                        "time": int(times[i]) if times is not None else 0,
                        "ratio": round(ratio, 2),
                        "is_bull": 1 if close[i] >= open_arr[i] else 0,
                    }
                )

        # 2. Check active POC mitigation on current closed bar BEFORE new leg creation
        for poc in active_pocs:
            if poc.tested_bar_idx is None and i > poc.end_bar_idx:
                if high[i] >= poc.price >= low[i]:
                    poc.tested_bar_idx = i

        # Remove tested POCs from active pool
        active_pocs = [p for p in active_pocs if p.tested_bar_idx is None]

        # 3. ATR ZigZag swing detection
        rev_amount = float(atr5[i]) * float(swing_mult)
        is_swing_high = False
        is_swing_low = False

        if zz_dir == 1:
            if high[i] > zz_high:
                zz_high = float(high[i])
                zz_high_bar = i
            if rev_amount > 0 and low[i] < zz_high - rev_amount:
                zz_dir = -1
                zz_low = float(low[i])
                zz_low_bar = i
                is_swing_high = True
        else:
            if low[i] < zz_low:
                zz_low = float(low[i])
                zz_low_bar = i
            if rev_amount > 0 and high[i] > zz_low + rev_amount:
                zz_dir = 1
                zz_high = float(high[i])
                zz_high_bar = i
                is_swing_low = True

        base_box_h = max(float(atr14[i]) / 10.0, min_tick)

        if is_swing_high:
            new_poc = _build_leg_poc(
                high,
                low,
                close,
                vol,
                times,
                last_pivot_bar,
                zz_high_bar,
                is_up_leg=True,
                base_box_height=base_box_h,
                max_boxes=max_boxes,
                min_tick=min_tick,
            )
            if new_poc is not None:
                # Re-check bars formed between pivot peak & reversal bar
                for check_bar in range(new_poc.end_bar_idx + 1, i + 1):
                    if high[check_bar] >= new_poc.price >= low[check_bar]:
                        new_poc.tested_bar_idx = check_bar
                        break
                all_pocs.append(new_poc)
                if new_poc.tested_bar_idx is None:
                    active_pocs.append(new_poc)
            last_pivot_bar = zz_high_bar

        if is_swing_low:
            new_poc = _build_leg_poc(
                high,
                low,
                close,
                vol,
                times,
                last_pivot_bar,
                zz_low_bar,
                is_up_leg=False,
                base_box_height=base_box_h,
                max_boxes=max_boxes,
                min_tick=min_tick,
            )
            if new_poc is not None:
                for check_bar in range(new_poc.end_bar_idx + 1, i + 1):
                    if high[check_bar] >= new_poc.price >= low[check_bar]:
                        new_poc.tested_bar_idx = check_bar
                        break
                all_pocs.append(new_poc)
                if new_poc.tested_bar_idx is None:
                    active_pocs.append(new_poc)
            last_pivot_bar = zz_low_bar

        # Cap active untested POCs to newest N per side (matching bullCount/bearCount <= 2)
        if active_pocs:
            bulls = [p for p in active_pocs if p.side == "BULL"][-max_active_per_side:]
            bears = [p for p in active_pocs if p.side == "BEAR"][-max_active_per_side:]
            keep_set = {id(p) for p in bulls + bears}
            active_pocs = [p for p in active_pocs if id(p) in keep_set]

    return StructuralLegAnalysis(
        pocs=all_pocs,
        untested_pocs=active_pocs,
        volume_spikes=volume_spikes,
        atr5=atr5,
        atr14=atr14,
    )
