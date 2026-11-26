/**
 * Pure domain indicator and timeframe utilities for interactive candlestick charts.
 */

import type { CandleBar } from "./types";

const TF_SECONDS: Record<string, number> = {
  M1: 60,
  M5: 300,
  M15: 900,
  M30: 1800,
  H1: 3600,
  H4: 14400,
  D1: 86400,
};

export function timeframeToSeconds(tf: string): number {
  return TF_SECONDS[tf.toUpperCase()] ?? 900;
}

export function computeEmaSeries(
  candles: CandleBar[],
  period: number
): Array<{ time: number; value: number }> {
  if (candles.length < period || period <= 0) return [];
  const k = 2 / (period + 1);
  let ema = 0;
  for (let i = 0; i < period; i += 1) {
    ema += candles[i].close;
  }
  ema /= period;

  const out: Array<{ time: number; value: number }> = [
    { time: candles[period - 1].time, value: ema },
  ];
  for (let i = period; i < candles.length; i += 1) {
    ema = candles[i].close * k + ema * (1 - k);
    out.push({ time: candles[i].time, value: ema });
  }
  return out;
}
