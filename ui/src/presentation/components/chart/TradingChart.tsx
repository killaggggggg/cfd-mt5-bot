import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type TickMarkType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { Calendar, Layers, Maximize2, RotateCcw } from "lucide-react";
import { api } from "../../../data/api";
import {
  CALENDAR_PRESETS,
  formatThaiDateSpan,
  getLocalTzOffsetMinutes,
  resolveCalendarPreset,
} from "../../../domain/dateRange";
import { computeEmaSeries, timeframeToSeconds } from "../../../domain/indicators";
import type {
  BacktestTrade,
  CandleBar,
  CandlesResponse,
  OpenPosition,
  PocLevelOverlay,
  SymbolSpec,
  ThemeMode,
  VolumeSpikeMarker,
} from "../../../domain/types";

interface Props {
  symbol: string;
  timeframe: string;
  theme: ThemeMode;
  height?: number;
  fillHeight?: boolean;
  liveSpec?: SymbolSpec | null;
  openPositions?: OpenPosition[];
  externalCandlesData?: CandlesResponse | null;
  backtestTrades?: BacktestTrade[];
  selectedBacktestTrade?: BacktestTrade | null;
  pocLevels?: PocLevelOverlay[];
  volumeSpikes?: VolumeSpikeMarker[];
  showQuickSymbols?: boolean;
  defaultShowNews?: boolean;
  onChangeSymbol?: (nextSymbol: string) => void;
  onChangeTimeframe?: (nextTf: string) => void;
  onExpandFull?: () => void;
}

interface OhlcSnapshot {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

const TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"];
const QUICK_SYMBOLS = ["XAUUSD", "EURUSD", "US30", "NAS100", "GBPUSD", "USDJPY"];

function formatPriceByDigits(price: number, digits: number): string {
  if (!Number.isFinite(price)) return "—";
  return price.toFixed(Math.min(6, Math.max(0, digits)));
}

function formatThaiCrosshair(tsSeconds: number): string {
  return (
    new Date(tsSeconds * 1000).toLocaleString("th-TH", {
      timeZone: "Asia/Bangkok",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }) + " น."
  );
}

/** Binary search returning last bar with `bar.time <= ts` (O(log N) for 50k+ candles). */
function findBarAtOrBefore(bars: CandleBar[], ts: number): CandleBar {
  let lo = 0;
  let hi = bars.length - 1;
  let ans = bars[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= ts) {
      ans = bars[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/** Binary search returning first bar with `bar.time >= ts` (fallback to latest bar). */
function findBarAtOrAfter(bars: CandleBar[], ts: number): CandleBar {
  let lo = 0;
  let hi = bars.length - 1;
  let ans = bars[bars.length - 1];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time >= ts) {
      ans = bars[mid];
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return ans;
}

/** Binary search returning first bar index with `bar.time <= ts` (0 fallback). */
function findBarIndexAtOrBefore(bars: CandleBar[], ts: number): number {
  let lo = 0;
  let hi = bars.length - 1;
  let best = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].time <= ts) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return best;
}

function getChartThemeOptions(theme: ThemeMode) {
  const isDark = theme === "dark";
  return {
    layout: {
      background: {
        type: ColorType.Solid,
        color: isDark ? "#0b1220" : "#ffffff",
      },
      textColor: isDark ? "#cbd5e1" : "#334155",
      fontFamily: '"Anuphan", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontSize: 12,
    },
    grid: {
      vertLines: { color: isDark ? "rgba(51, 65, 85, 0.35)" : "#f1f5f9" },
      horzLines: { color: isDark ? "rgba(51, 65, 85, 0.35)" : "#f1f5f9" },
    },
    rightPriceScale: {
      borderColor: isDark ? "#1e293b" : "#e2e8f0",
    },
    timeScale: {
      borderColor: isDark ? "#1e293b" : "#e2e8f0",
    },
    crosshair: {
      vertLine: {
        color: isDark ? "rgba(148, 163, 184, 0.45)" : "rgba(51, 65, 85, 0.35)",
        labelBackgroundColor: isDark ? "#1e293b" : "#0f172a",
      },
      horzLine: {
        color: isDark ? "rgba(148, 163, 184, 0.45)" : "rgba(51, 65, 85, 0.35)",
        labelBackgroundColor: isDark ? "#1e293b" : "#0f172a",
      },
    },
  };
}

export function TradingChart({
  symbol,
  timeframe,
  theme,
  height = 460,
  fillHeight = false,
  liveSpec = null,
  openPositions,
  externalCandlesData,
  backtestTrades,
  selectedBacktestTrade = null,
  pocLevels,
  volumeSpikes,
  showQuickSymbols = false,
  defaultShowNews = true,
  onChangeSymbol,
  onChangeTimeframe,
  onExpandFull,
}: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ema50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const ema200SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  const staticLinesRef = useRef<IPriceLine[]>([]);
  const liveLinesRef = useRef<IPriceLine[]>([]);
  const candlesRef = useRef<CandleBar[]>([]);
  const digitsRef = useRef<number>(5);
  const hasFittedKeyRef = useRef<string>("");

  const defaultCalRange = useMemo(() => resolveCalendarPreset("7d"), []);
  const [symbolInput, setSymbolInput] = useState(symbol);
  const [fetchedData, setFetchedData] = useState<CandlesResponse | null>(null);
  const chartData = externalCandlesData !== undefined ? externalCandlesData : fetchedData;
  const isExternalReplay = externalCandlesData !== undefined;
  const [showCalendarBar, setShowCalendarBar] = useState(false);
  const [draftStartDate, setDraftStartDate] = useState(defaultCalRange.startDate);
  const [draftEndDate, setDraftEndDate] = useState(defaultCalRange.endDate);
  const [histRange, setHistRange] = useState<{ startDate: string; endDate: string } | null>(null);
  const [calError, setCalError] = useState<string | null>(null);
  const isHistoricalFrozen = isExternalReplay || histRange !== null;
  const [loading, setLoading] = useState(false);
  const [showEma, setShowEma] = useState(true);
  const [showLevels, setShowLevels] = useState(true);
  const [showPocSpikes, setShowPocSpikes] = useState(true);
  const [showBidAsk, setShowBidAsk] = useState(true);
  const [showNews, setShowNews] = useState(defaultShowNews);
  const [hoveredBar, setHoveredBar] = useState<OhlcSnapshot | null>(null);

  useEffect(() => {
    setSymbolInput(symbol);
  }, [symbol]);

  const activeSpec = useMemo(() => {
    if (!liveSpec || !chartData?.symbol) return chartData?.spec ?? null;
    if (liveSpec.name.toUpperCase() === chartData.symbol.toUpperCase()) {
      return liveSpec;
    }
    return chartData?.spec ?? null;
  }, [liveSpec, chartData?.symbol, chartData?.spec]);

  const clearPriceLines = (targetLinesRef: React.MutableRefObject<IPriceLine[]>) => {
    const series = candleSeriesRef.current;
    if (!series) {
      targetLinesRef.current = [];
      return;
    }
    for (const line of targetLinesRef.current) {
      try {
        series.removePriceLine(line);
      } catch {
        // ignore stale handle
      }
    }
    targetLinesRef.current = [];
  };

  // 1. Initialize TradingView Lightweight Chart instance once cleanly
  useEffect(() => {
    const container = hostRef.current;
    if (!container) return;
    container.innerHTML = "";

    const initialHeight = fillHeight ? Math.max(320, container.clientHeight || height) : height;
    const chart = createChart(container, {
      ...getChartThemeOptions(theme),
      width: container.clientWidth || 720,
      height: initialHeight,
      crosshair: {
        ...getChartThemeOptions(theme).crosshair,
        mode: CrosshairMode.Normal,
      },
      timeScale: {
        ...getChartThemeOptions(theme).timeScale,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 8,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) => {
          if (typeof time !== "number") return null;
          const dt = new Date(time * 1000);
          if (tickMarkType <= 2) {
            return dt.toLocaleDateString("th-TH", {
              timeZone: "Asia/Bangkok",
              day: "numeric",
              month: "short",
            });
          }
          return dt.toLocaleTimeString("th-TH", {
            timeZone: "Asia/Bangkok",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          });
        },
      },
      localization: {
        locale: "th-TH",
        timeFormatter: (time: Time) => {
          if (typeof time !== "number") return String(time);
          return formatThaiCrosshair(time);
        },
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#089981",
      downColor: "#f23645",
      wickUpColor: "#089981",
      wickDownColor: "#f23645",
      borderVisible: false,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    const ema50Series = chart.addSeries(LineSeries, {
      color: "#0066CC",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "EMA 50",
    });

    const ema200Series = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      title: "EMA 200",
    });

    const markersApi = createSeriesMarkers(candleSeries, []);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema50SeriesRef.current = ema50Series;
    ema200SeriesRef.current = ema200Series;
    markersApiRef.current = markersApi;

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || typeof param.time !== "number") {
        setHoveredBar(null);
        return;
      }
      const data = param.seriesData.get(candleSeries) as
        | { open?: number; high?: number; low?: number; close?: number }
        | undefined;
      if (
        data &&
        typeof data.open === "number" &&
        typeof data.high === "number" &&
        typeof data.low === "number" &&
        typeof data.close === "number"
      ) {
        setHoveredBar({
          time: param.time,
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
        });
      }
    });

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        const h = fillHeight ? Math.max(320, Math.floor(entry.contentRect.height)) : height;
        if (w > 0 && h > 0) {
          chart.applyOptions({ width: w, height: h });
        }
      }
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      staticLinesRef.current = [];
      liveLinesRef.current = [];
      markersApiRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ema50SeriesRef.current = null;
      ema200SeriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, [height, fillHeight]);

  // 2. Sync Light / Dark Theme without losing scroll or zoom
  useEffect(() => {
    chartRef.current?.applyOptions(getChartThemeOptions(theme));
  }, [theme]);

  // 3. Fetch Candles from MT5 Backend cleanly (disabled when external replay data is provided)
  const loadCandles = useCallback(
    async (silent = false) => {
      if (isExternalReplay) return;
      if (!silent) {
        candlesRef.current = [];
        setCalError(null);
        setLoading(true);
      }
      try {
        const res = await api.getCandles(
          symbol,
          timeframe,
          500,
          histRange
            ? {
                start_date: histRange.startDate,
                end_date: histRange.endDate,
                tz_offset_minutes: getLocalTzOffsetMinutes(),
              }
            : undefined
        );
        setCalError(null);
        setFetchedData(res);
      } catch (err) {
        if (histRange) {
          setCalError((err as Error).message || "ไม่พบข้อมูลแท่งเทียนในช่วงวันที่ที่เลือก");
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [histRange, isExternalReplay, symbol, timeframe]
  );

  useEffect(() => {
    if (isExternalReplay) {
      setLoading(false);
      return;
    }
    void loadCandles(false);
    if (histRange !== null) return;
    const timer = window.setInterval(() => {
      void loadCandles(true);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [histRange, isExternalReplay, loadCandles]);

  const activePositions = useMemo(
    () => (openPositions !== undefined ? openPositions : chartData?.positions ?? []),
    [openPositions, chartData?.positions]
  );

  const resolvedSymUpper = (chartData?.symbol ?? symbol).trim().toUpperCase();

  const symbolPositions = useMemo(
    () =>
      activePositions.filter((pos) => {
        const ps = pos.symbol.trim().toUpperCase();
        return ps === resolvedSymUpper || ps.startsWith(resolvedSymUpper) || resolvedSymUpper.startsWith(ps);
      }),
    [activePositions, resolvedSymUpper]
  );

  const otherPositions = useMemo(
    () =>
      activePositions.filter((pos) => {
        const ps = pos.symbol.trim().toUpperCase();
        return !(ps === resolvedSymUpper || ps.startsWith(resolvedSymUpper) || resolvedSymUpper.startsWith(ps));
      }),
    [activePositions, resolvedSymUpper]
  );

  // 4. Push fetched bars + EMA 50/200 + Volume to Chart (without wiping zoom on poll)
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const ema50Series = ema50SeriesRef.current;
    const ema200Series = ema200SeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !volumeSeries || !ema50Series || !ema200Series || !chart) return;
    if (!chartData?.ok || !chartData.candles?.length) return;

    const bars = chartData.candles;
    candlesRef.current = bars;

    const digits = chartData.spec?.digits ?? 5;
    const point = chartData.spec?.point ?? 0.00001;
    digitsRef.current = digits;

    candleSeries.applyOptions({
      priceFormat: {
        type: "price",
        precision: Math.min(6, Math.max(0, digits)),
        minMove: point > 0 ? point : 0.00001,
      },
    });

    candleSeries.setData(
      bars.map((b) => ({
        time: b.time as UTCTimestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    volumeSeries.setData(
      bars.map((b) => ({
        time: b.time as UTCTimestamp,
        value: b.volume,
        color:
          b.close >= b.open
            ? "rgba(8, 153, 129, 0.28)"
            : "rgba(242, 54, 69, 0.28)",
      }))
    );

    if (showEma) {
      ema50Series.setData(
        computeEmaSeries(bars, 50).map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
      );
      ema200Series.setData(
        computeEmaSeries(bars, 200).map((d) => ({ time: d.time as UTCTimestamp, value: d.value }))
      );
    } else {
      ema50Series.setData([]);
      ema200Series.setData([]);
    }

    const fitKey = isExternalReplay
      ? `bt|${chartData.symbol ?? symbol}|${chartData.timeframe ?? timeframe}|${bars.length}|${bars[0].time}|${bars[bars.length - 1].time}`
      : histRange
        ? `cal|${chartData.symbol ?? symbol}|${chartData.timeframe ?? timeframe}|${histRange.startDate}|${histRange.endDate}|${bars[0].time}|${bars[bars.length - 1].time}`
        : `${chartData.symbol ?? symbol}|${chartData.timeframe ?? timeframe}`;
    if (hasFittedKeyRef.current !== fitKey) {
      hasFittedKeyRef.current = fitKey;
      if (histRange) {
        chart.timeScale().fitContent();
      } else {
        const from = Math.max(0, bars.length - 125);
        const to = bars.length + 6;
        chart.timeScale().setVisibleLogicalRange({ from, to });
      }
    }
  }, [chartData, histRange, isExternalReplay, showEma, symbol, timeframe]);

  // 4b. Reactive Order Entry/SL/TP Price Lines + Entry Arrow & News Markers (updates every 0.75s SSE tick!)
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const markersApi = markersApiRef.current;
    if (!candleSeries || !chartData?.ok || !chartData.candles?.length) return;

    const bars = chartData.candles;
    clearPriceLines(staticLinesRef);

    if (showLevels) {
      for (const pos of symbolPositions) {
        const plSign = pos.profit >= 0 ? "+" : "-";
        const plText = `${plSign}$${Math.abs(pos.profit).toFixed(2)}`;
        const entryColor = pos.side === "BUY" ? "#0066CC" : "#b91c1c";
        staticLinesRef.current.push(
          candleSeries.createPriceLine({
            price: pos.price_open,
            color: entryColor,
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: `${pos.side} ${pos.volume.toFixed(2)} (${plText})`,
          })
        );
        if (pos.sl > 0) {
          staticLinesRef.current.push(
            candleSeries.createPriceLine({
              price: pos.sl,
              color: "#dc2626",
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: `SL #${pos.ticket}`,
            })
          );
        }
        if (pos.tp > 0) {
          staticLinesRef.current.push(
            candleSeries.createPriceLine({
              price: pos.tp,
              color: "#059669",
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: `TP #${pos.ticket}`,
            })
          );
        }
      }

      if (!isExternalReplay && symbolPositions.length === 0 && chartData.last_signal) {
        const sig = chartData.last_signal;
        staticLinesRef.current.push(
          candleSeries.createPriceLine({
            price: sig.entry_price,
            color: "#0066CC",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: `สัญญาณ ${sig.side}`,
          }),
          candleSeries.createPriceLine({
            price: sig.sl_price,
            color: "#dc2626",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: "Signal SL",
          }),
          candleSeries.createPriceLine({
            price: sig.tp_price,
            color: "#059669",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: "Signal TP",
          })
        );
      }

      if (selectedBacktestTrade) {
        const bt = selectedBacktestTrade;
        staticLinesRef.current.push(
          candleSeries.createPriceLine({
            price: bt.entry_price,
            color: "#0066CC",
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: `จุดเข้า ${bt.side}`,
          }),
          candleSeries.createPriceLine({
            price: bt.sl_price,
            color: "#dc2626",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "Backtest SL",
          }),
          candleSeries.createPriceLine({
            price: bt.tp_price,
            color: "#059669",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "Backtest TP",
          }),
          candleSeries.createPriceLine({
            price: bt.exit_price,
            color: bt.outcome === "WIN" ? "#059669" : "#d97706",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: `ปิดไม้ (${bt.r_multiple >= 0 ? "+" : ""}${bt.r_multiple.toFixed(2)}R)`,
          })
        );
      }
    }

    if (showPocSpikes && pocLevels && pocLevels.length > 0) {
      for (const poc of pocLevels) {
        const isBull = poc.side === "BULL";
        staticLinesRef.current.push(
          candleSeries.createPriceLine({
            price: poc.price,
            color: isBull ? "#089981" : "#b91c1c",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `${isBull ? "Bull" : "Bear"} POC (รอทดสอบ)`,
          })
        );
      }
    }

    if (markersApi) {
      const mergedMarkers: SeriesMarker<Time>[] = [];

      // News event markers
      if (showNews && chartData.news_events?.length) {
        const byAnchor = new Map<number, { isHigh: boolean; labels: string[]; currency: string }>();
        for (const ev of chartData.news_events) {
          const anchor = findBarAtOrAfter(bars, ev.event_ts);
          if (!anchor) continue;
          const isHigh = ev.impact === "HIGH";
          const existing = byAnchor.get(anchor.time);
          if (existing) {
            existing.isHigh = existing.isHigh || isHigh;
            existing.labels.push(ev.title);
          } else {
            byAnchor.set(anchor.time, { isHigh, labels: [ev.title], currency: ev.currency });
          }
        }
        for (const [barTime, info] of byAnchor.entries()) {
          mergedMarkers.push({
            time: barTime as UTCTimestamp,
            position: info.isHigh ? "aboveBar" : "belowBar",
            color: info.isHigh ? "#dc2626" : "#d97706",
            shape: info.isHigh ? "arrowDown" : "circle",
            text:
              info.labels.length > 1
                ? `${info.currency} (${info.labels.length} ข่าว)`
                : `${info.currency} ${info.labels[0].slice(0, 18)}`,
          });
        }
      }

      // Open Position exact entry candle arrow markers
      if (showLevels && symbolPositions.length > 0) {
        const firstBarTs = bars[0].time;
        for (const pos of symbolPositions) {
          if (!pos.open_time || pos.open_time < firstBarTs - timeframeToSeconds(timeframe)) {
            continue;
          }
          const anchor = findBarAtOrBefore(bars, pos.open_time ?? 0);
          const isBuy = pos.side === "BUY";
          mergedMarkers.push({
            time: anchor.time as UTCTimestamp,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? "#089981" : "#dc2626",
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${pos.side} ${pos.volume.toFixed(2)}`,
          });
        }
      }

      // Backtest replay trade entry & outcome markers
      if (showLevels && backtestTrades && backtestTrades.length > 0 && bars.length > 0) {
        for (let idx = 0; idx < backtestTrades.length; idx += 1) {
          const bt = backtestTrades[idx];
          const entryAnchor = findBarAtOrBefore(bars, bt.entry_time);
          const isBuy = bt.side === "BUY";
          const rLabel = `${bt.r_multiple >= 0 ? "+" : ""}${bt.r_multiple.toFixed(1)}R`;
          mergedMarkers.push({
            time: entryAnchor.time as UTCTimestamp,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? "#0066CC" : "#dc2626",
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `#${idx + 1} ${bt.side} (${rLabel})`,
          });

          if (bt.outcome !== "OPEN") {
            const exitAnchor = findBarAtOrBefore(bars, bt.exit_time);
            if (exitAnchor.time !== entryAnchor.time) {
              mergedMarkers.push({
                time: exitAnchor.time as UTCTimestamp,
                position: isBuy ? "aboveBar" : "belowBar",
                color: bt.outcome === "WIN" ? "#059669" : "#dc2626",
                shape: "circle",
                text: `#${idx + 1} ${bt.outcome === "WIN" ? "TP" : bt.outcome === "STOP_OUT" ? "STOP OUT" : "SL"}`,
              });
            }
          }
        }
      }

      // Structural Volume Spike anomaly markers
      if (showPocSpikes && volumeSpikes && volumeSpikes.length > 0 && bars.length > 0) {
        for (const sp of volumeSpikes) {
          const anchor = findBarAtOrBefore(bars, sp.time);
          if (anchor.time > bars[bars.length - 1].time) continue;
          const isBull = sp.is_bull === 1;
          mergedMarkers.push({
            time: anchor.time as UTCTimestamp,
            position: isBull ? "belowBar" : "aboveBar",
            color: isBull ? "#0284c7" : "#ea580c",
            shape: "circle",
            text: `Vol ${sp.ratio.toFixed(1)}x`,
          });
        }
      }

      mergedMarkers.sort((a, b) => Number(a.time) - Number(b.time));
      markersApi.setMarkers(mergedMarkers);
    }
  }, [
    chartData,
    symbolPositions,
    backtestTrades,
    selectedBacktestTrade,
    pocLevels,
    volumeSpikes,
    isExternalReplay,
    showLevels,
    showPocSpikes,
    showNews,
    timeframe,
  ]);

  // 4c. Smoothly frame clicked backtest trade on the chart
  useEffect(() => {
    const chart = chartRef.current;
    const bars = chartData?.candles;
    if (!chart || !selectedBacktestTrade || !bars?.length) return;
    const i1 = findBarIndexAtOrBefore(bars, selectedBacktestTrade.entry_time);
    const i2 = Math.max(i1, findBarIndexAtOrBefore(bars, selectedBacktestTrade.exit_time));
    const from = Math.max(0, i1 - 15);
    const to = Math.min(bars.length + 5, i2 + 18);
    chart.timeScale().setVisibleLogicalRange({ from, to });
  }, [selectedBacktestTrade, chartData?.candles]);

  // 5. Real-time SSE Live Tick Candle & Bid/Ask Price Lines (0.75s cadence)
  useEffect(() => {
    if (isHistoricalFrozen) {
      clearPriceLines(liveLinesRef);
      return;
    }
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries || !activeSpec || !candlesRef.current.length) return;

    const livePrice = activeSpec.bid > 0 ? activeSpec.bid : activeSpec.ask;
    if (livePrice <= 0) return;

    const tfSec = timeframeToSeconds(timeframe);
    const nowBucket = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
    const bars = candlesRef.current;
    const lastBar = bars[bars.length - 1];

    if (nowBucket <= lastBar.time) {
      const updated: CandleBar = {
        ...lastBar,
        high: Math.max(lastBar.high, livePrice),
        low: Math.min(lastBar.low, livePrice),
        close: livePrice,
      };
      bars[bars.length - 1] = updated;
      candleSeries.update({
        time: updated.time as UTCTimestamp,
        open: updated.open,
        high: updated.high,
        low: updated.low,
        close: updated.close,
      });
    } else {
      const prevClose = lastBar.close;
      const newBar: CandleBar = {
        time: nowBucket,
        open: prevClose,
        high: Math.max(prevClose, livePrice),
        low: Math.min(prevClose, livePrice),
        close: livePrice,
        volume: 1,
      };
      bars.push(newBar);
      candleSeries.update({
        time: newBar.time as UTCTimestamp,
        open: newBar.open,
        high: newBar.high,
        low: newBar.low,
        close: newBar.close,
      });
    }

    clearPriceLines(liveLinesRef);
    if (showBidAsk) {
      if (activeSpec.bid > 0) {
        liveLinesRef.current.push(
          candleSeries.createPriceLine({
            price: activeSpec.bid,
            color: "#089981",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: "Bid",
          })
        );
      }
      if (activeSpec.ask > 0 && activeSpec.ask !== activeSpec.bid) {
        liveLinesRef.current.push(
          candleSeries.createPriceLine({
            price: activeSpec.ask,
            color: "#f23645",
            lineWidth: 1,
            lineStyle: LineStyle.Dotted,
            axisLabelVisible: true,
            title: "Ask",
          })
        );
      }
    }
  }, [activeSpec, isHistoricalFrozen, timeframe, showBidAsk]);

  const latestBar = chartData?.candles?.[chartData.candles.length - 1] ?? null;
  const displayBar = hoveredBar ?? latestBar;
  const digits = activeSpec?.digits ?? chartData?.spec?.digits ?? 5;
  const barDiff = displayBar ? displayBar.close - displayBar.open : 0;
  const barPct = displayBar && displayBar.open > 0 ? (barDiff / displayBar.open) * 100 : 0;
  const isUp = barDiff >= 0;

  const handleApplySymbolInput = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = symbolInput.trim();
    if (cleaned && onChangeSymbol) {
      onChangeSymbol(cleaned);
    }
  };

  return (
    <div className={`tv-chart-shell ${fillHeight ? "is-fill" : ""}`}>
      {/* TradingView-style Interactive Top Toolbar */}
      <div className="tv-toolbar">
        <div className="tv-toolbar-left">
          {showQuickSymbols && onChangeSymbol && (
            <>
              <form onSubmit={handleApplySymbolInput} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="text"
                  className="input-inline tv-sym-input"
                  value={symbolInput}
                  onChange={(e) => setSymbolInput(e.target.value)}
                  placeholder="ชื่อสินค้า..."
                />
                <button type="submit" className="btn btn-sm">
                  ดูกราฟ
                </button>
              </form>
              <div className="chip-row">
                {QUICK_SYMBOLS.map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    className={`chip-btn ${symbol.toUpperCase().startsWith(sym) ? "is-active" : ""}`}
                    onClick={() => onChangeSymbol(sym)}
                  >
                    {sym}
                  </button>
                ))}
              </div>
            </>
          )}

          {onChangeTimeframe && (
            <div className="tv-tf-group" role="group" aria-label="เลือกกรอบเวลา">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  type="button"
                  className={`tv-tf-btn ${timeframe.toUpperCase() === tf ? "is-active" : ""}`}
                  onClick={() => onChangeTimeframe(tf)}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="tv-toolbar-right">
          <button
            type="button"
            className={`chip-btn ${showEma ? "is-active" : ""}`}
            onClick={() => setShowEma((v) => !v)}
            title="แสดงเส้นค่าเฉลี่ย EMA 50 (น้ำเงิน) และ EMA 200 (ส้ม)"
          >
            <Layers size={13} /> EMA 50/200
          </button>
          <button
            type="button"
            className={`chip-btn ${showLevels ? "is-active" : ""}`}
            onClick={() => setShowLevels((v) => !v)}
            title="แสดงเส้นจุดเข้า / SL / TP ของออเดอร์ที่เปิดอยู่"
          >
            SL / TP
          </button>
          {((pocLevels && pocLevels.length > 0) || (volumeSpikes && volumeSpikes.length > 0)) && (
            <button
              type="button"
              className={`chip-btn ${showPocSpikes ? "is-active" : ""}`}
              onClick={() => setShowPocSpikes((v) => !v)}
              title="แสดงเส้น Untested POC และหมุด Volume Spike จาก Structural Leg Profiler"
            >
              POC / Spike
            </button>
          )}
          {!isExternalReplay && showQuickSymbols && (
            <button
              type="button"
              className={`chip-btn ${histRange || showCalendarBar ? "is-active" : ""}`}
              onClick={() => setShowCalendarBar((v) => !v)}
              title="เลือกดูกราฟแท่งเทียนย้อนหลังตามช่วงปฏิทินที่ต้องการ"
            >
              <Calendar size={13} /> {histRange ? "ช่วงปฏิทิน" : "ปฏิทิน"}
            </button>
          )}
          {!isHistoricalFrozen && (
            <button
              type="button"
              className={`chip-btn ${showBidAsk ? "is-active" : ""}`}
              onClick={() => setShowBidAsk((v) => !v)}
              title="แสดงเส้นราคาซื้อ/ขาย (Bid / Ask) แบบเรียลไทม์"
            >
              Bid / Ask
            </button>
          )}
          <button
            type="button"
            className={`chip-btn ${showNews ? "is-active" : ""}`}
            onClick={() => setShowNews((v) => !v)}
            title="แสดงหมุดข่าวเศรษฐกิจบนแท่งเทียน"
          >
            ข่าวแรง
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => chartRef.current?.timeScale().fitContent()}
            title="ปรับมุมมองให้เห็นแท่งเทียนทั้งหมดพอดีจอ"
          >
            <RotateCcw size={13} /> พอดีจอ
          </button>
          {onExpandFull && (
            <button type="button" className="btn btn-primary btn-sm" onClick={onExpandFull}>
              <Maximize2 size={13} /> เต็มจอ
            </button>
          )}
        </div>
      </div>

      {/* Optional Historical Calendar Range Strip for ChartPage */}
      {!isExternalReplay && showQuickSymbols && (showCalendarBar || histRange !== null) && (
        <div className="tv-calendar-bar tabular">
          <div className="tv-calendar-inputs">
            <span className="text-muted">ดูกราฟย้อนหลัง:</span>
            <input
              type="date"
              className="input-inline date-input-inline"
              value={draftStartDate}
              aria-label="วันที่เริ่มต้นกราฟ"
              onChange={(e) => setDraftStartDate(e.target.value)}
            />
            <span className="text-muted">ถึง</span>
            <input
              type="date"
              className="input-inline date-input-inline"
              value={draftEndDate}
              aria-label="วันที่สิ้นสุดกราฟ"
              onChange={(e) => setDraftEndDate(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => {
                if (!draftStartDate || !draftEndDate || draftStartDate > draftEndDate) {
                  setCalError("วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด");
                  return;
                }
                setCalError(null);
                setHistRange({ startDate: draftStartDate, endDate: draftEndDate });
              }}
            >
              ดูช่วงนี้
            </button>
            {histRange && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  setHistRange(null);
                  setCalError(null);
                }}
              >
                กลับดูกราฟสด (Live)
              </button>
            )}
          </div>
          <div className="chip-row">
            {CALENDAR_PRESETS.slice(0, 5).map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={`chip-btn ${
                  histRange &&
                  (() => {
                    const p = resolveCalendarPreset(preset.id);
                    return p.startDate === histRange.startDate && p.endDate === histRange.endDate;
                  })()
                    ? "is-active"
                    : ""
                }`}
                onClick={() => {
                  const next = resolveCalendarPreset(preset.id);
                  setDraftStartDate(next.startDate);
                  setDraftEndDate(next.endDate);
                  setCalError(null);
                  setHistRange(next);
                }}
              >
                {preset.label_th}
              </button>
            ))}
          </div>
          {histRange && chartData?.actual_start_time && chartData?.actual_end_time && (
            <span className="text-muted" style={{ fontSize: "0.78rem" }}>
              แสดง {chartData.candles?.length.toLocaleString("en-US") ?? 0} แท่ง ({formatThaiDateSpan(chartData.actual_start_time, chartData.actual_end_time)})
            </span>
          )}
          {calError && <span className="text-danger">{calError}</span>}
          {!calError && chartData?.range_notice_th && (
            <span className="text-warning">{chartData.range_notice_th}</span>
          )}
        </div>
      )}

      {/* Live OHLCV + Bid/Ask Legend Strip */}
      <div className="tv-ohlc-bar tabular">
        <div className="tv-ohlc-left">
          <span className="tv-pair-badge">
            {chartData?.symbol ?? symbol} • {chartData?.timeframe ?? timeframe}
          </span>
          {displayBar ? (
            <span className="tv-ohlc-values">
              <span>O <strong>{formatPriceByDigits(displayBar.open, digits)}</strong></span>
              <span>H <strong>{formatPriceByDigits(displayBar.high, digits)}</strong></span>
              <span>L <strong>{formatPriceByDigits(displayBar.low, digits)}</strong></span>
              <span>
                C <strong className={isUp ? "text-success" : "text-danger"}>{formatPriceByDigits(displayBar.close, digits)}</strong>
              </span>
              <span className={isUp ? "text-success" : "text-danger"}>
                ({isUp ? "+" : ""}
                {barPct.toFixed(2)}%)
              </span>
            </span>
          ) : (
            <span className="text-muted">
              {loading ? "กำลังดึงกราฟแท่งเทียนจาก MT5..." : chartData?.error ?? "รอกราฟจาก MT5"}
            </span>
          )}
        </div>

        <div className="tv-ohlc-right">
          {showEma && (
            <span className="tv-ema-legend">
              <span style={{ color: "#0066CC" }}>● EMA50</span>
              <span style={{ color: "#f59e0b" }}>● EMA200</span>
            </span>
          )}
          {activeSpec && (
            <span className="text-muted">
              Bid <strong>{formatPriceByDigits(activeSpec.bid, digits)}</strong> / Ask{" "}
              <strong>{formatPriceByDigits(activeSpec.ask, digits)}</strong> • Spread{" "}
              <strong>{activeSpec.spread_points} จุด</strong>
            </span>
          )}
        </div>
      </div>

      {/* Live Open Positions Ribbon on Chart */}
      {(symbolPositions.length > 0 || otherPositions.length > 0) && (
        <div className="tv-orders-ribbon tabular">
          {symbolPositions.map((pos) => {
            const isGain = pos.profit >= 0;
            return (
              <span key={pos.ticket} className={`tv-order-pill ${isGain ? "is-profit" : "is-loss"}`}>
                <strong>
                  #{pos.ticket} {pos.side} {pos.volume.toFixed(2)}
                </strong>
                <span>@ {formatPriceByDigits(pos.price_open, digits)}</span>
                {pos.sl > 0 && <span>• SL {formatPriceByDigits(pos.sl, digits)}</span>}
                {pos.tp > 0 && <span>• TP {formatPriceByDigits(pos.tp, digits)}</span>}
                <strong className={isGain ? "text-success" : "text-danger"}>
                  {isGain ? "+" : "-"}${Math.abs(pos.profit).toFixed(2)}
                </strong>
              </span>
            );
          })}
          {otherPositions.map((pos) => (
            <button
              key={pos.ticket}
              type="button"
              className="chip-btn tabular"
              onClick={() => onChangeSymbol && onChangeSymbol(pos.symbol)}
              title={`คลิกเพื่อสลับกราฟไปดูออเดอร์ #${pos.ticket} บน ${pos.symbol}`}
            >
              ออเดอร์อีกคู่: {pos.symbol} {pos.side} {pos.volume.toFixed(2)} ({pos.profit >= 0 ? "+" : "-"}$
              {Math.abs(pos.profit).toFixed(2)}) — กดดูกราฟ
            </button>
          ))}
        </div>
      )}

      {/* Lightweight Charts Canvas Container */}
      <div className="tv-canvas-wrap" style={fillHeight ? undefined : { height }}>
        <div ref={hostRef} className="tv-canvas-inner" style={fillHeight ? undefined : { height }} />
      </div>
    </div>
  );
}
