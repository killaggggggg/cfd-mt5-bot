import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Award,
  BadgeCheck,
  BarChart3,
  BookOpen,
  Calendar,
  ChartCandlestick,
  Coins,
  Crosshair,
  FileCode2,
  FlaskConical,
  Gauge,
  Play,
  RotateCcw,
  Settings,
  ShieldCheck,
  ShieldOff,
  SlidersHorizontal,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { api } from "../../data/api";
import {
  CALENDAR_PRESETS,
  type CalendarPresetId,
  formatThaiDateSpan,
  getLocalTzOffsetMinutes,
  resolveCalendarPreset,
} from "../../domain/dateRange";
import {
  LOT_MODE_LABELS,
  type BacktestResponse,
  type CandlesResponse,
  type StrategiesResponse,
  type StrategyDescriptor,
  type ThemeMode,
} from "../../domain/types";
import { TradingChart } from "../components/chart/TradingChart";
import { FieldWithHint, InlineHintToggle, type HelpHintContent } from "../components/ui/HelpHint";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatCard } from "../components/ui/StatCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  defaultSymbol: string;
  defaultTimeframe: string;
  theme: ThemeMode;
  onNotify: (msg: string) => void;
  onStrategyActivated: () => void;
}

const QUICK_SYMBOLS = ["XAUUSD", "EURUSD", "US30", "NAS100"];
const TIMEFRAMES = ["M1", "M3", "M5", "M15", "M30", "H1", "H4"];
const BAR_COUNTS = [1500, 5000, 10000, 20000, 50000];
const DEFAULT_TABLE_LIMIT = 250;

interface StrategyPreset {
  id: string;
  label_th: string;
  badge_th: string;
  params: Record<string, number>;
}

const TREND_PULLBACK_PRESETS: StrategyPreset[] = [
  {
    id: "balanced_ml",
    label_th: "มาตรฐานสมดุล (ML กรองแม่น)",
    badge_th: "ML=1 • SL ≤ 2.2 ATR • Time-Stop 48 แท่ง",
    params: {
      fast_ema_period: 21,
      mid_ema_period: 50,
      slow_ema_period: 200,
      min_adx: 18,
      kernel_h: 8,
      require_ml_confirm: 1,
      pullback_band_atr: 0.3,
      min_body_atr: 0.25,
      sl_min_atr: 1.6,
      sl_max_atr: 2.2,
      sl_buffer_atr: 0.35,
      tp_rr_multiplier: 1.25,
      max_hold_bars: 48,
      rsi_buy_min: 40,
      rsi_buy_max: 66,
    },
  },
  {
    id: "hf_profit",
    label_th: "Trend Pullback ถี่ + กำไรสูง",
    badge_th: "ML=0 • SL ≤ 2.2 ATR • Time-Stop 48 แท่ง",
    params: {
      fast_ema_period: 21,
      mid_ema_period: 50,
      slow_ema_period: 200,
      min_adx: 12,
      kernel_h: 8,
      require_ml_confirm: 0,
      pullback_band_atr: 0.6,
      min_body_atr: 0.1,
      sl_min_atr: 1.6,
      sl_max_atr: 2.2,
      sl_buffer_atr: 0.35,
      tp_rr_multiplier: 1.25,
      max_hold_bars: 48,
      rsi_buy_min: 35,
      rsi_buy_max: 72,
    },
  },
  {
    id: "hf_winrate",
    label_th: "Trend Pullback ถี่ + เน้น Win Rate สูง",
    badge_th: "ML=0 • SL 2.2–2.5 ATR • R:R 1.0 • Time-Stop 48 แท่ง",
    params: {
      fast_ema_period: 21,
      mid_ema_period: 50,
      slow_ema_period: 200,
      min_adx: 12,
      kernel_h: 8,
      require_ml_confirm: 0,
      pullback_band_atr: 0.6,
      min_body_atr: 0.1,
      sl_min_atr: 2.2,
      sl_max_atr: 2.5,
      sl_buffer_atr: 0.35,
      tp_rr_multiplier: 1.0,
      max_hold_bars: 48,
      rsi_buy_min: 35,
      rsi_buy_max: 72,
    },
  },
];

const SQUEEZE_PRESETS: StrategyPreset[] = [
  {
    id: "h1_champ_3y",
    label_th: "H1 แชมป์ 3 ปี (PF ~1.46)",
    badge_th: "ADX 18 • R:R 1.8 • Max DD ~8.88R",
    params: {
      donchian_period: 20,
      bb_period: 20,
      squeeze_lookback: 10,
      squeeze_kc_mult: 2.0,
      min_adx: 18,
      ema_period: 200,
      trade_start_hour_utc: 6,
      trade_end_hour_utc: 20,
      atr_sl_multiplier: 2.0,
      tp_rr_multiplier: 1.8,
      max_hold_bars: 0,
    },
  },
  {
    id: "h1_more_trades",
    label_th: "H1 ออกไม้บ่อยขึ้น (ADX 14)",
    badge_th: "Donchian 16 • ADX 14 • R:R 1.6",
    params: {
      donchian_period: 16,
      bb_period: 20,
      squeeze_lookback: 12,
      squeeze_kc_mult: 2.2,
      min_adx: 14,
      ema_period: 200,
      trade_start_hour_utc: 6,
      trade_end_hour_utc: 21,
      atr_sl_multiplier: 1.8,
      tp_rr_multiplier: 1.6,
      max_hold_bars: 0,
    },
  },
  {
    id: "h1_all_hours",
    label_th: "H1 เทรดตลอด 24 ชม.",
    badge_th: "0–24 UTC • ADX 18 • R:R 2.0",
    params: {
      donchian_period: 20,
      bb_period: 20,
      squeeze_lookback: 10,
      squeeze_kc_mult: 2.0,
      min_adx: 18,
      ema_period: 200,
      trade_start_hour_utc: 0,
      trade_end_hour_utc: 24,
      atr_sl_multiplier: 2.0,
      tp_rr_multiplier: 2.0,
      max_hold_bars: 0,
    },
  },
];

const M5_RANGE_PRESETS: StrategyPreset[] = [
  {
    id: "m5_keltner_champ",
    label_th: "M5 แชมป์ 17 เดือน (Keltner • 12/17 เดือนบวก)",
    badge_th: "~1,065 ไม้ • PF ~1.16 • +73.8R • H1 Flat",
    params: {
      channel_mode: 1,
      htf_minutes: 60,
      htf_max_adx: 18,
      channel_period: 20,
      band_multiplier: 1.8,
      rsi_buy_max: 42,
      rsi_sell_min: 58,
      trade_start_hour_utc: 6,
      trade_end_hour_utc: 20,
      sl_atr_multiplier: 2.0,
      tp_rr_multiplier: 1.5,
      max_hold_bars: 0,
    },
  },
  {
    id: "m5_keltner_lowdd",
    label_th: "M5 Keltner เน้น DD ต่ำ (RSI 40/60)",
    badge_th: "~941 ไม้ • Max DD ~15.9R • Buy/Sell สมดุล",
    params: {
      channel_mode: 1,
      htf_minutes: 60,
      htf_max_adx: 18,
      channel_period: 20,
      band_multiplier: 1.8,
      rsi_buy_max: 40,
      rsi_sell_min: 60,
      trade_start_hour_utc: 6,
      trade_end_hour_utc: 20,
      sl_atr_multiplier: 2.0,
      tp_rr_multiplier: 1.5,
      max_hold_bars: 0,
    },
  },
  {
    id: "m5_bb_2026",
    label_th: "M5 Bollinger Reversion (สมดุล OOS 2026)",
    badge_th: "~840 ไม้ • OOS PF ~1.15 • Buy/Sell 50/50",
    params: {
      channel_mode: 0,
      htf_minutes: 60,
      htf_max_adx: 18,
      channel_period: 20,
      band_multiplier: 2.0,
      rsi_buy_max: 40,
      rsi_sell_min: 60,
      trade_start_hour_utc: 6,
      trade_end_hour_utc: 20,
      sl_atr_multiplier: 2.0,
      tp_rr_multiplier: 1.5,
      max_hold_bars: 0,
    },
  },
];

const LAB_HINTS: Record<string, HelpHintContent> = {
  presets: {
    summary:
      "ชุดพารามิเตอร์สำเร็จรูปสำหรับกลยุทธ์ Trend Pullback (M5KernelPullback) ที่ผ่านการทดสอบบนข้อมูลจริง XAUUSDm 5,000 แท่ง — กดเลือกแล้วระบบจะลอง Backtest ให้ดูทันทีโดยยังไม่ทับค่าบอทจริงจนกว่าคุณจะกดบันทึก",
    example:
      "'ถี่ + กำไรสูง' ใช้ R:R 1.25 เข้ากันกับ MIN_NET_RR=1.00 ได้เลย (~7.5 ไม้/วัน); ส่วน 'ถี่ + เน้น Win Rate' ใช้ SL 2.2 และ R:R 1.00 หากใช้รวม Defensive ควรกดบันทึกแล้วกดแก้ค่าทับซ้อนด้วย",
    recommended: "เริ่มเทียบระหว่าง 'มาตรฐานสมดุล' กับ 'ถี่ + กำไรสูง' บน 3,000–5,000 แท่ง",
  },
  pipeline: {
    summary:
      "กลยุทธ์ทำงานเป็น 4 ขั้น: (1) เช็กทิศทางหลักด้วย EMA 21/50/200 และความแรง ADX -> (2) รอราคาย่อเข้าโซน EMA สั้น -> (3) ยืนยันด้วยความชัน Kernel, RSI และ Lorentzian AI -> (4) วาง SL เลยสวิงล่าสุดด้วย ATR พร้อมเป้า TP ตามสัดส่วน R:R",
    example:
      "ถ้าอยากให้เข้าไม้ถี่ขึ้น: ปิด ML Confirm (=0), ลด ADX เหลือ ~12, ขยาย Band ย่อตัว ~0.5–0.6 และลด SL Min / TP R:R ให้เหมาะกับกรอบเวลา",
    recommended: "ใช้ค่ามาตรฐาน M5 เป็นฐาน แล้วปรับทีละ 1–2 ค่าเพื่อเทียบผลบน 3,000–5,000 แท่ง",
  },
  btSymbol: {
    summary:
      "เลือกสินค้าที่ต้องการจำลองย้อนหลัง ระบบจับคู่ชื่อ Suffix ของโบรคเกอร์ให้อัตโนมัติ (เช่น XAUUSD -> XAUUSDm)",
    example: "ทองคำ (XAUUSD) แท่ง 5 นาทีวิ่งแรงและมีจังหวะเข้าบ่อยกว่าคู่เงิน",
    recommended: "XAUUSD (สินค้าหลัก)",
  },
  btTimeframe: {
    summary:
      "กรอบเวลาของแท่งเทียนที่ใช้จำลอง: กรอบ M5 เป็นกรอบหลักสำหรับทั้ง M5KernelPullback (สายเทรนด์) และ M5RangeScalper (สายสแคลปถี่ช่วงไซด์เวย์), ส่วน H1 เหมาะกับ VolatilitySqueezeBreakout",
    example:
      "บนกรอบ M5 สามารถใช้คู่กับข้อมูลย้อนหลัง 6 เดือน–1 ปี (~35,000–70,000 แท่ง) เพื่อวัดความทนทานข้ามทั้งช่วงเทรนด์และไซด์เวย์",
    recommended: "M5 (กรอบหลักสำหรับ Trend & High-Freq Scalper) หรือ H1 (Squeeze Breakout)",
  },
  btBars: {
    summary:
      "เลือกขอบเขตข้อมูลย้อนหลังจาก MT5 ได้ 2 แบบ: (1) ตามช่วงวันที่ (ปฏิทิน) — ระบุวันเริ่มต้นถึงวันสิ้นสุดที่ต้องการ พร้อมดึงแท่งล่วงหน้าไปวอร์มอินดิเคเตอร์ให้ครบก่อนวันแรกที่เลือก และ (2) ตามจำนวนแท่งล่าสุด (60 – 50,000 แท่ง)",
    example:
      "อยากตรวจเดือนไหนเป็นพิเศษ (เช่น พ.ย. ปีก่อน หรือช่วงทองผันผวนแรง) ให้เลือก 'ตามปฏิทิน' แล้วกด 30 วันล่าสุด / เดือนที่แล้ว / 3 เดือนล่าสุด ได้เลย (กรอบ M5 ย้อนได้ถึง ~17 เดือน)",
    recommended: "ใช้โหมดปฏิทินเวลาตรวจช่วงเหตุการณ์จริง และใช้โหมดจำนวนแท่งเวลาเทสต์เร็ว 5,000–50,000 แท่ง",
  },
  btCapital: {
    summary:
      "เงินทุนตั้งต้นสำหรับจำลองการคำนวณขนาด Lot, ค่า Margin ที่ใช้จริง, Drawdown เป็นเงินและ %, จนถึงยอดเงินพอร์ตคงเหลือหลังจบไม้ล่าสุด",
    example: "เว้นว่าง = ดึงยอด Equity จริงจากพอร์ต MT5 ปัจจุบัน; หรือพิมพ์ตัวเลขเอง เช่น 1000 หรือ 5000 เพื่อจำลองถ้าเพิ่ม/ลดทุนพอร์ต",
    recommended: "เว้นว่างเพื่อใช้ทุนจริงจาก MT5 หรือทดลองใส่ 1,000 / 5,000 เทียบขนาด Lot",
  },
  portfolioSummary: {
    summary:
      "สรุปการจำลองพอร์ตจริงตามกฎบริหารเงินในหน้า 'ตั้งค่าบอท' (โหมด Lot, % ความเสี่ยง, Leverage โบรคเกอร์ และเพดาน Margin) ต่อเนื่องจากทุนตั้งต้นจนถึงไม้ล่าสุด",
    example:
      "ช่วยให้เห็นทันทีว่า ทุนเท่านี้จะออก Lot ได้กี่เท่า ใช้ Margin กี่เหรียญ มี Margin Level เหลือแค่ไหน และจบช่วงทดสอบแล้วพอร์ตจะเหลือเท่าไร",
    recommended: "ดู 'Max Drawdown พอร์ตจริง' และ 'Margin Level ต่ำสุด' ควบคู่กับยอดพอร์ตหลังไม้ล่าสุดเสมอ",
  },
  btDefensive: {
    summary:
      "ติ๊กเปิดเพื่อเอา 'ด่านป้องกันและบริหารไม้จริง' จากหน้าตั้งค่าบอทมาจำลองด้วย ทั้ง Spread จริงของโบรคเกอร์, เกณฑ์ MIN_NET_RR, Rollover Blackout, Auto-Breakeven และ Trailing Stop",
    example:
      "ข้อควรระวัง: ถ้าตั้งเป้า TP R:R = 1.00 ขณะที่ตั้งค่าบอทตั้ง MIN_NET_RR = 1.00 ไว้ พอหัก Spread ออก Net R:R จะเหลือ < 1.00 ทำให้ทุกสัญญาณถูกบล็อก (ได้ 0 ไม้) — ต้องกดแก้ค่าทับซ้อนหรือลด MIN_NET_RR ก่อน",
    recommended: "เปิดเทียบคู่กับแบบ Pure Offensive เสมอก่อนนำค่าขึ้นรันจริง",
  },
  kpiWinRate: {
    summary:
      "เปอร์เซ็นต์ของไม้ที่ปิดทำกำไรสุทธิจริง (> 0 หลังหักต้นทุนในโหมด Defensive) เทียบกับไม้ที่ปิดจบแล้วทั้งหมด — ไม้ที่โดน BE/Trail แต่หักค่าคอมฯ แล้วติดลบจะไม่ถูกนับเป็น WIN",
    example:
      "ต้องดูคู่กับ TP R:R เสมอ: ถ้าตั้ง R:R = 1.25 ชนะเกิน ~45% ก็เริ่มมีกำไรสุทธิ; ถ้าตั้ง R:R = 1.00 ต้องชนะเกิน 50% (และ >53% หลังหัก Spread)",
    recommended: ">= 50% (เมื่อใช้ R:R >= 1.15) หรือ >= 56% (เมื่อใช้ R:R = 1.00)",
  },
  kpiTotalR: {
    summary:
      "ผลตอบแทนรวมแบบ Mark-to-Market คิดทั้งไม้ที่ปิดแล้วและกำไร/ขาดทุนลอยตัวของไม้ที่ยังเปิดค้างอยู่ (ในโหมดรวม Defensive จะคำนวณเป็น Net R หลังหัก Spread, Commission และ Slippage)",
    example:
      "ถ้าเปิด 41 ไม้ ปิดแล้ว +16.50 R แต่ยังมี 1 ไม้เปิดค้าง -0.48 R ระบบจะรวมให้ดูตรงๆ เป็น +16.02 R ไม่ซ่อนยอดติดลบของไม้ที่เปิดอยู่",
    recommended: "Total R เป็นบวกหลังรวม Defensive แล้ว และ Expectancy >= +0.10 R/ไม้",
  },
  kpiProfitFactor: {
    summary:
      "อัตราส่วนระหว่าง 'กำไรรวม (R)' หารด้วย 'ขาดทุนรวม (R)' โดยนับรวมทั้งไม้ที่ปิดแล้วและผลลัพธ์ลอยตัวของไม้ที่เปิดค้างอยู่ (MTM)",
    example:
      "PF = 0.93 แปลว่าทุก 1 R ที่เสีย ได้คืนแค่ 0.93 R (ขาดทุนสุทธิ); PF = 1.45 แปลว่าทุก 1 R ที่เสีย ได้คืน 1.45 R (ทำกำไรชัดเจน)",
    recommended: ">= 1.20 (พอใช้ได้) และ >= 1.35 (ดี ทน Spread จริงได้)",
  },
  kpiDrawdown: {
    summary:
      "ช่วงขาดทุนสะสมลึกที่สุดนับจากยอดสูงสุดลงมาจุดต่ำสุด (Peak-to-Trough) โดยเช็ก 'แรงลากติดลบระหว่างถือทุกแท่ง (Intra-trade MAE)' และไม้ที่ยังเปิดค้างอยู่ด้วย ไม่ใช่แค่จุดหลังปิดไม้",
    example:
      "ถ้าระหว่างถือไม้เคยถูกลากติดลบ -0.90 R ก่อนเด้งกลับมาปิดเสมอ ระบบจะบันทึกช่วงลากจริงไว้ใน Max Drawdown เพื่อให้เห็นความเสี่ยงจริงของพอร์ต",
    recommended: "ควรคุมให้ต่ำกว่า -8.00 R และทดสอบบน 10,000–50,000 แท่งเสมอ",
  },
  replayTable: {
    summary:
      "ตารางบันทึกทุกจังหวะที่จำลองได้: 'ระยะเวลา (แท่ง)' คือจำนวนแท่งเทียนที่ถือไม้จนปิด, 'ผลลัพธ์ (R)' คือกำไร/ขาดทุนเทียบระยะ SL ตั้งต้น (+ คือชนะ, - คือแพ้), '[BE/Trail]' ในเหตุผลหมายถึงโดนเลื่อน SL ตามราคา",
    example: "คลิกแถวใดก็ได้เพื่อให้กราฟด้านบนกระโดดไปยังจุดเข้า-ออก พร้อมตีเส้น Entry / SL / TP ของไม้นั้นทันที",
    recommended: "ไล่คลิกดูไม้ที่แพ้ติดกัน เพื่อเช็กว่าตลาดตอนนั้นเป็น Sideway หรือโดนสะบัด SL",
  },
};

function formatThaiDateTime(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleString("th-TH", {
    timeZone: "Asia/Bangkok",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatPrice(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return "-";
  return value.toFixed(Math.min(6, Math.max(2, digits)));
}

function formatMoney(value: number, currency = "USD", signed = false): string {
  if (!Number.isFinite(value)) return "-";
  const prefix = signed && value > 0 ? "+" : value < 0 ? "-" : "";
  const formatted = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${prefix}${formatted} ${currency}`;
}

function parseCapitalInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const num = Number(trimmed);
  if (!Number.isFinite(num) || num <= 0) return null;
  return num;
}

function normalizeBarCount(raw: string | number): number {
  const num = Math.round(Number(raw));
  if (!Number.isFinite(num) || num <= 0) return 1500;
  return Math.max(60, Math.min(50000, num));
}

export function StrategyLabPage({
  defaultSymbol,
  theme,
  onNotify,
  onStrategyActivated,
}: Props) {
  const [catalog, setCatalog] = useState<StrategiesResponse | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [selectedName, setSelectedName] = useState<string>("M5KernelPullback");
  const [paramsDraft, setParamsDraft] = useState<Record<string, number>>({});

  const defaultCalendarRange = useMemo(() => resolveCalendarPreset("7d"), []);
  const [btSymbol, setBtSymbol] = useState<string>(defaultSymbol || "XAUUSD");
  const [btTimeframe, setBtTimeframe] = useState<string>("M5");
  const [rangeMode, setRangeMode] = useState<"calendar" | "bars">("calendar");
  const [startDate, setStartDate] = useState<string>(defaultCalendarRange.startDate);
  const [endDate, setEndDate] = useState<string>(defaultCalendarRange.endDate);
  const [btBars, setBtBars] = useState<number>(1500);
  const [btBarsInput, setBtBarsInput] = useState<string>("1500");
  const [capitalInput, setCapitalInput] = useState<string>("");
  const [includeDefensive, setIncludeDefensive] = useState<boolean>(false);

  const [runningBacktest, setRunningBacktest] = useState(false);
  const [savingStrategy, setSavingStrategy] = useState(false);
  const [syncingDefensive, setSyncingDefensive] = useState(false);
  const [showAllHints, setShowAllHints] = useState(false);

  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null);
  const [backtestError, setBacktestError] = useState<string | null>(null);
  const [selectedTradeIndex, setSelectedTradeIndex] = useState<number | null>(null);
  const [showAllTrades, setShowAllTrades] = useState(false);

  const hasAutoRunRef = useRef(false);

  const runBacktestWith = useCallback(
    async (
      strategyName: string,
      params: Record<string, number>,
      symbol: string,
      timeframe: string,
      bars: number,
      withDefensive: boolean,
      initialCapital: number | null = null,
      calendarOpts?: {
        rangeMode: "calendar" | "bars";
        startDate: string;
        endDate: string;
      }
    ) => {
      setRunningBacktest(true);
      setBacktestError(null);
      setSelectedTradeIndex(null);
      try {
        const useCalendar = calendarOpts?.rangeMode === "calendar";
        const res = await api.runStrategyBacktest({
          strategy_name: strategyName,
          params,
          symbol: symbol.trim() || defaultSymbol,
          timeframe,
          bars,
          range_mode: useCalendar ? "calendar" : "bars",
          ...(useCalendar
            ? {
                start_date: calendarOpts.startDate,
                end_date: calendarOpts.endDate,
                tz_offset_minutes: getLocalTzOffsetMinutes(),
              }
            : {}),
          include_defensive: withDefensive,
          initial_capital: initialCapital,
        });
        setBacktestResult(res);
      } catch (err) {
        const msg = (err as Error).message || "รันทดสอบกลยุทธ์ไม่สำเร็จ";
        setBacktestError(msg);
        onNotify(`ทดสอบกลยุทธ์ไม่สำเร็จ: ${msg}`);
      } finally {
        setRunningBacktest(false);
      }
    },
    [defaultSymbol, onNotify]
  );

  const triggerBacktest = useCallback(
    (overrides?: {
      strategyName?: string;
      params?: Record<string, number>;
      symbol?: string;
      timeframe?: string;
      bars?: number;
      rangeMode?: "calendar" | "bars";
      startDate?: string;
      endDate?: string;
      withDefensive?: boolean;
      capital?: number | null;
    }) => {
      const nextMode = overrides?.rangeMode ?? rangeMode;
      const nextStart = overrides?.startDate ?? startDate;
      const nextEnd = overrides?.endDate ?? endDate;
      if (nextMode === "calendar" && nextStart && nextEnd && nextStart > nextEnd) {
        const err = "วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด";
        setBacktestError(err);
        onNotify(err);
        return;
      }
      const safeBars = overrides?.bars ?? normalizeBarCount(btBarsInput);
      setBtBars(safeBars);
      setBtBarsInput(String(safeBars));
      const cap =
        overrides && "capital" in overrides ? overrides.capital ?? null : parseCapitalInput(capitalInput);
      void runBacktestWith(
        overrides?.strategyName ?? selectedName,
        overrides?.params ?? paramsDraft,
        overrides?.symbol ?? btSymbol,
        overrides?.timeframe ?? btTimeframe,
        safeBars,
        overrides?.withDefensive ?? includeDefensive,
        cap,
        {
          rangeMode: nextMode,
          startDate: nextStart,
          endDate: nextEnd,
        }
      );
    },
    [btBarsInput, btSymbol, btTimeframe, capitalInput, endDate, includeDefensive, onNotify, paramsDraft, rangeMode, runBacktestWith, selectedName, startDate]
  );

  useEffect(() => {
    let active = true;
    (async () => {
      setLoadingCatalog(true);
      try {
        const res = await api.getStrategies();
        if (!active) return;
        setCatalog(res);
        const initStratName = res.active_strategy || "M5KernelPullback";
        const initDesc = res.strategies.find((s) => s.name === initStratName);
        const initTf = initDesc?.recommended_timeframe || res.bot_timeframe || "M5";
        setSelectedName(initStratName);
        setParamsDraft({ ...res.active_params });
        setBtTimeframe(initTf);

        if (!hasAutoRunRef.current) {
          hasAutoRunRef.current = true;
          const initCal =
            initTf === "H1"
              ? resolveCalendarPreset("6m")
              : defaultCalendarRange;
          setStartDate(initCal.startDate);
          setEndDate(initCal.endDate);
          void runBacktestWith(
            initStratName,
            res.active_params,
            defaultSymbol || "XAUUSD",
            initTf,
            1500,
            false,
            null,
            {
              rangeMode: "calendar",
              startDate: initCal.startDate,
              endDate: initCal.endDate,
            }
          );
        }
      } catch (err) {
        if (active) {
          setBacktestError((err as Error).message || "โหลดข้อมูลกลยุทธ์ไม่สำเร็จ");
        }
      } finally {
        if (active) setLoadingCatalog(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [defaultCalendarRange.endDate, defaultCalendarRange.startDate, defaultSymbol, runBacktestWith]);

  const selectedDescriptor: StrategyDescriptor | null = useMemo(() => {
    if (!catalog?.strategies.length) return null;
    return catalog.strategies.find((s) => s.name === selectedName) ?? catalog.strategies[0];
  }, [catalog, selectedName]);

  const handleParamChange = (key: string, rawValue: string, kind: "int" | "float") => {
    const parsed = kind === "int" ? Math.round(Number(rawValue)) : Number(rawValue);
    if (!Number.isFinite(parsed)) return;
    setParamsDraft((prev) => ({ ...prev, [key]: parsed }));
  };

  const handleResetDefaultParams = () => {
    if (!selectedDescriptor) return;
    const defaults = { ...selectedDescriptor.default_params };
    setParamsDraft(defaults);
    onNotify(`คืนค่ามาตรฐานของ ${selectedDescriptor.name} แล้ว`);
  };

  const handleActivateStrategy = async () => {
    setSavingStrategy(true);
    try {
      const res = await api.saveActiveStrategy({
        strategy_name: selectedName,
        params: paramsDraft,
      });
      setCatalog(res);
      setParamsDraft({ ...res.active_params });
      onStrategyActivated();
      onNotify(`บันทึกพารามิเตอร์ "${res.active_display_name_th}" ให้บอทใช้งานเรียบร้อย`);
    } catch (err) {
      onNotify(`บันทึกกลยุทธ์ไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSavingStrategy(false);
    }
  };

  const handleSyncDefensive = async () => {
    setSyncingDefensive(true);
    try {
      const res = await api.syncDefensiveWithStrategy();
      setCatalog(res);
      onStrategyActivated();
      onNotify(`ปรับจูนค่าตั้งค่าบอท (Defensive) ให้สอดคล้องกับกลยุทธ์ ${res.active_strategy} เรียบร้อย`);
      if (includeDefensive) {
        triggerBacktest({ withDefensive: true });
      }
    } catch (err) {
      onNotify(`ซิงก์ค่าทับซ้อนไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSyncingDefensive(false);
    }
  };

  const replayCandlesData = useMemo<CandlesResponse | null>(() => {
    if (!backtestResult) return null;
    return {
      ok: backtestResult.ok,
      error: backtestResult.error,
      symbol: backtestResult.symbol,
      timeframe: backtestResult.timeframe,
      server_utc_offset_seconds: backtestResult.spec?.server_utc_offset_seconds ?? 0,
      spec: backtestResult.spec ?? undefined,
      candles: backtestResult.candles,
      positions: [],
      last_signal: backtestResult.report.latest_signal,
      news_events: [],
    };
  }, [backtestResult]);

  const trades = backtestResult?.report.trades ?? [];
  const selectedTrade =
    selectedTradeIndex !== null && trades[selectedTradeIndex] ? trades[selectedTradeIndex] : null;

  const isSelectedCurrentlyActive = catalog?.active_strategy === selectedName;
  const paramsDifferFromActive = useMemo(() => {
    if (!catalog || !isSelectedCurrentlyActive) return true;
    return Object.keys(paramsDraft).some((k) => paramsDraft[k] !== catalog.active_params[k]);
  }, [catalog, isSelectedCurrentlyActive, paramsDraft]);

  const report = backtestResult?.report ?? null;
  const portfolio = report?.portfolio ?? null;
  const digits = backtestResult?.spec?.digits ?? 2;
  const conflicts = catalog?.defensive_conflicts ?? [];
  const blockedEntries = Object.entries(report?.blocked_reasons ?? {});

  const activePresets =
    selectedName === "VolatilitySqueezeBreakout"
      ? SQUEEZE_PRESETS
      : selectedName === "M5RangeScalper"
      ? M5_RANGE_PRESETS
      : TREND_PULLBACK_PRESETS;

  const handleSelectStrategyCard = (strat: StrategyDescriptor) => {
    const nextParams =
      catalog?.active_strategy === strat.name ? { ...catalog.active_params } : { ...strat.default_params };
    const recTf = strat.recommended_timeframe || "M5";
    setSelectedName(strat.name);
    setParamsDraft(nextParams);
    setBtTimeframe(recTf);
    let nextStart = startDate;
    let nextEnd = endDate;
    let nextBars: number | undefined;
    if (rangeMode === "calendar" && (recTf === "H1" || strat.name === "M5RangeScalper")) {
      const longRange = resolveCalendarPreset("1y");
      nextStart = longRange.startDate;
      nextEnd = longRange.endDate;
      setStartDate(nextStart);
      setEndDate(nextEnd);
    } else if (rangeMode === "bars" && strat.name === "M5RangeScalper" && Number(btBarsInput) < 20000) {
      nextBars = 20000;
      setBtBarsInput("20000");
    }
    triggerBacktest({
      strategyName: strat.name,
      params: nextParams,
      timeframe: recTf,
      bars: nextBars,
      startDate: nextStart,
      endDate: nextEnd,
    });
  };

  return (
    <div className="ops-container">
      <PageHeader
        title="กลยุทธ์เทรด (Offensive Strategy Lab)"
        subtitle="เลือกสลับและทดสอบกลยุทธ์แยกจากด่านป้องกันความเสี่ยง พร้อมตรวจสอบค่าทับซ้อนกับหน้าตั้งค่าบอทอัตโนมัติ"
        actions={
          <>
            <button
              type="button"
              className={`btn ${showAllHints ? "btn-primary" : ""}`}
              onClick={() => setShowAllHints((prev) => !prev)}
            >
              <BookOpen size={15} />
              <span>{showAllHints ? "ซ่อนคำอธิบายทั้งหมด" : "เปิดคำอธิบายทั้งหมด"}</span>
            </button>
            <button
              type="button"
              className="btn"
              disabled={runningBacktest || loadingCatalog}
              onClick={() => triggerBacktest()}
            >
              <Play size={14} />
              <span>{runningBacktest ? "กำลังจำลอง..." : "รันทดสอบ Backtest"}</span>
            </button>
            <button
              type="button"
              className="btn btn-primary btn-save-stable"
              disabled={savingStrategy || loadingCatalog}
              onClick={() => void handleActivateStrategy()}
            >
              <BadgeCheck size={15} />
              <span>
                {savingStrategy
                  ? "กำลังบันทึก..."
                  : isSelectedCurrentlyActive && !paramsDifferFromActive
                  ? "กำลังใช้ค่านี้กับบอทอยู่"
                  : `บันทึก "${selectedName}" ให้บอท`}
              </span>
            </button>
          </>
        }
      />

      {/* Explicit Backtest Scope + Defensive Overlap Audit */}
      <div className="offensive-banner" role="note">
        <div className="offensive-banner-icon">
          {includeDefensive ? <ShieldCheck size={18} /> : <ShieldOff size={18} />}
        </div>
        <div className="offensive-banner-body">
          {includeDefensive ? (
            <>
              <strong>กำลังทดสอบโหมด "จำลองรวมค่า Defensive ของบอท":</strong>{" "}
              นำค่าจากหน้าตั้งค่าบอทจริงมาคำนวณด้วย (Spread จริงของโบรคเกอร์, ด่าน Net R:R, เพดานสเปรด/ขาดทุนรายวัน, ระยะ SL ขั้นต่ำ, Auto-Breakeven และ Trailing Stop) เพื่อเทียบผลใกล้เคียงตอนบอทเทรดจริงที่สุด
            </>
          ) : (
            <>
              <strong>ค่าเริ่มต้น Backtest เป็นโหมด "Pure Offensive (ไม่ปน Defensive)":</strong>{" "}
              ตอนทดสอบปกติจะ<strong>ไม่นำค่าตั้งค่า Defensive มาใช้</strong> (Spread=0, ไม่บล็อกข่าว/Net R:R และไม่เลื่อน BE/Trailing) เพื่อให้วัดความแม่นยำของสัญญาณ {selectedName} ล้วนๆ — คุณสามารถติ๊กเปิด <strong>"จำลองรวมค่า Defensive"</strong> ในส่วนที่ 3 เพื่อทดสอบร่วมกันได้ตลอดเวลา
            </>
          )}
        </div>
      </div>

      {/* Live Conflict Audit Box */}
      {!loadingCatalog && (
        <div className={`conflict-box ${conflicts.length > 0 ? "has-conflicts" : "is-clean"}`}>
          <div className="conflict-box-head">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Settings size={16} />
              <strong>
                {conflicts.length > 0
                  ? `พบค่าตั้งค่าบอท (Defensive) ทับซ้อน/ขัดแย้งกับกลยุทธ์ที่เปิดใช้อยู่ (${catalog?.active_strategy ?? selectedName}) จำนวน ${conflicts.length} จุด`
                  : `ตรวจสอบความเข้ากันได้: ไม่มีค่าตั้งค่าบอท (Defensive) ทับซ้อนกับกลยุทธ์ที่เปิดใช้อยู่ (${catalog?.active_strategy ?? selectedName})`}
              </strong>
            </div>
            {conflicts.length > 0 && (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={syncingDefensive}
                onClick={() => void handleSyncDefensive()}
              >
                {syncingDefensive ? "กำลังแก้ไข..." : "แก้ไขค่าทับซ้อนในตั้งค่าบอทอัตโนมัติ"}
              </button>
            )}
          </div>
          {conflicts.length > 0 && (
            <ul className="conflict-list">
              {conflicts.map((cf) => (
                <li key={cf.code}>
                  <strong>{cf.field_label}</strong>: ปัจจุบัน <code>{cf.current_value}</code> (แนะนำ{" "}
                  <code className="text-brand">{cf.recommended_value}</code>) — {cf.detail_th}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 1. Strategy Selector & Active Summary */}
      {selectedDescriptor && (
        <SectionCard
          title={`1. เลือกกลยุทธ์เทรด: ${selectedDescriptor.display_name_th}`}
          subtitle={selectedDescriptor.description_th}
          icon={<Crosshair size={18} />}
          headerRight={
            <StatusBadge tone={isSelectedCurrentlyActive ? "success" : "info"}>
              {isSelectedCurrentlyActive
                ? `บอทใช้งานอยู่: ${catalog?.active_strategy} (${catalog?.bot_timeframe ?? "M5"})`
                : `กำลังทดลอง: ${selectedDescriptor.name} (แนะนำ ${selectedDescriptor.recommended_timeframe ?? "M5"})`}
            </StatusBadge>
          }
        >
          {catalog?.strategies && catalog.strategies.length > 0 && (
            <div className="strategy-grid" style={{ marginBottom: 14 }}>
              {catalog.strategies.map((strat) => {
                const isSel = strat.name === selectedName;
                const isLiveActive = strat.name === catalog.active_strategy;
                return (
                  <button
                    key={strat.name}
                    type="button"
                    className={`strategy-card ${isSel ? "is-selected" : ""}`}
                    onClick={() => handleSelectStrategyCard(strat)}
                  >
                    <div className="strategy-card-top">
                      <span className="strategy-card-code">{strat.name}</span>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <StatusBadge tone="info">
                          แนะนำ {strat.recommended_timeframe ?? "M5"}
                        </StatusBadge>
                        {isLiveActive && <StatusBadge tone="success">บอทใช้อยู่</StatusBadge>}
                      </div>
                    </div>
                    <div className="strategy-card-title">{strat.display_name_th}</div>
                    <p className="strategy-card-desc">{strat.description_th}</p>
                  </button>
                );
              })}
            </div>
          )}

          {selectedDescriptor.name === "VolatilitySqueezeBreakout" ? (
            <div className="m5-summary-pills tabular">
              <StatusBadge tone="info">Donchian {paramsDraft.donchian_period ?? 20} แท่ง</StatusBadge>
              <StatusBadge tone="neutral">BB {paramsDraft.bb_period ?? 20} &lt; KC {paramsDraft.squeeze_kc_mult ?? 2.0} ATR</StatusBadge>
              <StatusBadge tone="neutral">EMA {paramsDraft.ema_period ?? 200}</StatusBadge>
              <StatusBadge tone="neutral">ADX &gt;= {paramsDraft.min_adx ?? 18}</StatusBadge>
              <StatusBadge tone="neutral">UTC {paramsDraft.trade_start_hour_utc ?? 6}:00–{paramsDraft.trade_end_hour_utc ?? 20}:00</StatusBadge>
              <StatusBadge tone="neutral">SL {paramsDraft.atr_sl_multiplier ?? 2.0} ATR</StatusBadge>
              <StatusBadge tone="success">เป้ากำไร: {paramsDraft.tp_rr_multiplier ?? 1.8} R:R</StatusBadge>
            </div>
          ) : selectedDescriptor.name === "M5RangeScalper" ? (
            <div className="m5-summary-pills tabular">
              <StatusBadge tone="info">
                {(paramsDraft.channel_mode ?? 1) > 0 ? "Keltner" : "Bollinger"} {paramsDraft.channel_period ?? 20} (±{paramsDraft.band_multiplier ?? 1.8} {(paramsDraft.channel_mode ?? 1) > 0 ? "ATR" : "SD"})
              </StatusBadge>
              <StatusBadge tone="neutral">กรอง Sideway: M{paramsDraft.htf_minutes ?? 60} (ADX &lt; {paramsDraft.htf_max_adx ?? 18})</StatusBadge>
              <StatusBadge tone="neutral">RSI BUY &lt; {paramsDraft.rsi_buy_max ?? 42} • SELL &gt; {paramsDraft.rsi_sell_min ?? 58}</StatusBadge>
              <StatusBadge tone="neutral">UTC {paramsDraft.trade_start_hour_utc ?? 6}:00–{paramsDraft.trade_end_hour_utc ?? 20}:00</StatusBadge>
              <StatusBadge tone="neutral">SL {paramsDraft.sl_atr_multiplier ?? 2.0} ATR</StatusBadge>
              <StatusBadge tone="success">เป้ากำไร: {paramsDraft.tp_rr_multiplier ?? 1.5} R:R</StatusBadge>
            </div>
          ) : (
            <>
              <div className="m5-summary-pills tabular">
                <StatusBadge tone="info">EMA {paramsDraft.fast_ema_period ?? 21} / {paramsDraft.mid_ema_period ?? 50} / {paramsDraft.slow_ema_period ?? 200}</StatusBadge>
                <StatusBadge tone="neutral">ADX ขั้นต่ำ: {paramsDraft.min_adx ?? 18}</StatusBadge>
                <StatusBadge tone="neutral">
                  Lorentzian ML: {(paramsDraft.require_ml_confirm ?? 1) === 1 ? "เปิดกรอง AI" : "ปิด (เน้นรอบถี่)"}
                </StatusBadge>
                <StatusBadge tone="neutral">RSI BUY: {paramsDraft.rsi_buy_min ?? 40}–{paramsDraft.rsi_buy_max ?? 66}</StatusBadge>
                <StatusBadge tone="neutral">SL ขั้นต่ำ: {paramsDraft.sl_min_atr ?? 1.6} ATR</StatusBadge>
                <StatusBadge tone="success">เป้ากำไร: {paramsDraft.tp_rr_multiplier ?? 1.25} R:R</StatusBadge>
              </div>
              <div style={{ marginTop: 10 }}>
                <InlineHintToggle
                  label="ขั้นตอนทำงานของป้ายสรุปด้านบน"
                  titleText="กลยุทธ์นี้อ่านอะไรบ้าง"
                  hint={LAB_HINTS.pipeline}
                  forceOpen={showAllHints}
                />
              </div>
            </>
          )}
        </SectionCard>
      )}

      {/* 2. Strategy Parameter Tuner */}
      {selectedDescriptor && (
        <SectionCard
          title={`2. ปรับจูนพารามิเตอร์ (${selectedDescriptor.name})`}
          subtitle={`เลือกชุดพรีเซ็ตหรือปรับค่าเองด้านล่าง — หากต้องการให้มีผลกับบอทจริง กด "บันทึก ${selectedDescriptor.name} ให้บอท" ด้านบน`}
          icon={<SlidersHorizontal size={18} />}
          headerRight={
            <button type="button" className="btn btn-sm" onClick={handleResetDefaultParams}>
              <RotateCcw size={13} />
              <span>คืนค่ามาตรฐาน</span>
            </button>
          }
        >
          <div className="preset-strip">
            <div className="preset-strip-head">
              <strong>พรีเซ็ตแนะนำสำหรับ {selectedDescriptor.name} (กดเลือกเพื่อลองจำลองทันที):</strong>
              <InlineHintToggle label="ชุดพรีเซ็ตสำเร็จรูป" hint={LAB_HINTS.presets} forceOpen={showAllHints} />
            </div>
            <div className="preset-grid">
              {activePresets.map((preset) => {
                const isActivePreset = Object.entries(preset.params).every(
                  ([k, v]) => Number(paramsDraft[k] ?? selectedDescriptor.default_params[k]) === v
                );
                return (
                  <button
                    key={preset.id}
                    type="button"
                    className={`preset-btn ${isActivePreset ? "is-active" : ""}`}
                    onClick={() => {
                      const next = { ...preset.params };
                      setParamsDraft(next);
                      onNotify(`โหลดพรีเซ็ต "${preset.label_th}" แล้ว (กำลังจำลอง Backtest...)`);
                      triggerBacktest({ params: next });
                    }}
                  >
                    <span className="preset-btn-title">{preset.label_th}</span>
                    <span className="preset-btn-meta tabular">{preset.badge_th}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid-3">
            {selectedDescriptor.param_specs.map((spec) => {
              const val = paramsDraft[spec.key] ?? spec.default;
              return (
                <FieldWithHint
                  key={spec.key}
                  label={spec.label_th}
                  forceOpen={showAllHints}
                  smallText={`มาตรฐาน ${spec.default} (ช่วง ${spec.min_val} - ${spec.max_val})`}
                  hint={{
                    summary: spec.hint_th,
                    example: spec.example_th || undefined,
                    recommended: spec.recommended_th || `${spec.default}`,
                  }}
                >
                  {spec.options && spec.options.length > 0 ? (
                    <select
                      value={val}
                      onChange={(e) => handleParamChange(spec.key, e.target.value, spec.kind)}
                    >
                      {spec.options.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label_th}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="number"
                      className="tabular"
                      value={val}
                      min={spec.min_val}
                      max={spec.max_val}
                      step={spec.step}
                      onChange={(e) => handleParamChange(spec.key, e.target.value, spec.kind)}
                    />
                  )}
                </FieldWithHint>
              );
            })}
          </div>
        </SectionCard>
      )}

      {/* 3. Backtest Controls & Metrics */}
      <SectionCard
        title="3. จำลองการเทรดย้อนหลัง M5 (Backtest)"
        subtitle="ดึงแท่งเทียนจริงจาก MT5 มาจำลองสัญญาณทีละแท่ง และเทียบระหว่างสัญญาณล้วน (Pure Offensive) กับรวมค่าตั้งค่า Defensive"
        icon={<FlaskConical size={18} />}
        headerRight={
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {backtestResult && (
              <StatusBadge tone={backtestResult.mode === "DEFENSIVE_AWARE" ? "warning" : "info"}>
                {backtestResult.mode === "DEFENSIVE_AWARE" ? "โหมด: รวม Defensive" : "โหมด: Pure Offensive"}
              </StatusBadge>
            )}
            {report?.latest_signal && (
              <StatusBadge tone={report.latest_signal.side === "BUY" ? "success" : "danger"}>
                สัญญาณล่าสุด: {report.latest_signal.side} @ {formatPrice(report.latest_signal.entry_price, digits)}
              </StatusBadge>
            )}
          </div>
        }
      >
        <div className="backtest-controls-bar">
          <div className="backtest-control-group">
            <div className="backtest-label-row">
              <label>สินค้า (Symbol)</label>
              <InlineHintToggle label="สินค้า (Symbol)" hint={LAB_HINTS.btSymbol} forceOpen={showAllHints} />
            </div>
            <div className="backtest-inline-row">
              <input
                type="text"
                className="input-inline"
                value={btSymbol}
                onChange={(e) => setBtSymbol(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    triggerBacktest({ symbol: btSymbol });
                  }
                }}
                placeholder="เช่น XAUUSD"
                style={{ width: 115 }}
              />
              <div className="chip-row">
                {QUICK_SYMBOLS.map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    className={`chip-btn ${btSymbol.toUpperCase().startsWith(sym) ? "is-active" : ""}`}
                    onClick={() => {
                      setBtSymbol(sym);
                      triggerBacktest({ symbol: sym });
                    }}
                  >
                    {sym}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="backtest-control-group">
            <div className="backtest-label-row">
              <label>กรอบเวลา (Timeframe)</label>
              <InlineHintToggle label="กรอบเวลา (Timeframe)" hint={LAB_HINTS.btTimeframe} forceOpen={showAllHints} />
            </div>
            <div className="tv-tf-group">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  type="button"
                  className={`tv-tf-btn ${btTimeframe === tf ? "is-active" : ""}`}
                  onClick={() => {
                    setBtTimeframe(tf);
                    triggerBacktest({ timeframe: tf });
                  }}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          <div className="backtest-control-group">
            <div className="backtest-label-row">
              <label>ขอบเขตย้อนหลัง</label>
              <InlineHintToggle label="ขอบเขตย้อนหลัง" hint={LAB_HINTS.btBars} forceOpen={showAllHints} />
            </div>
            <div className="backtest-inline-row" style={{ marginBottom: 4 }}>
              <div className="tv-tf-group" role="group" aria-label="วิธีเลือกช่วงเวลาย้อนหลัง">
                <button
                  type="button"
                  className={`tv-tf-btn ${rangeMode === "calendar" ? "is-active" : ""}`}
                  onClick={() => {
                    setRangeMode("calendar");
                    triggerBacktest({ rangeMode: "calendar" });
                  }}
                >
                  <Calendar size={12} style={{ marginRight: 4, verticalAlign: "-1px" }} />
                  ตามปฏิทิน
                </button>
                <button
                  type="button"
                  className={`tv-tf-btn ${rangeMode === "bars" ? "is-active" : ""}`}
                  onClick={() => {
                    setRangeMode("bars");
                    triggerBacktest({ rangeMode: "bars" });
                  }}
                >
                  ตามจำนวนแท่ง
                </button>
              </div>
            </div>

            {rangeMode === "calendar" ? (
              <div className="backtest-calendar-wrap">
                <div className="backtest-inline-row">
                  <span className="text-muted" style={{ fontSize: "0.78rem" }}>
                    ตั้งแต่
                  </span>
                  <input
                    type="date"
                    className="input-inline date-input-inline tabular"
                    value={startDate}
                    aria-label="วันที่เริ่มต้น"
                    onChange={(e) => setStartDate(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        triggerBacktest({ rangeMode: "calendar" });
                      }
                    }}
                  />
                  <span className="text-muted" style={{ fontSize: "0.78rem" }}>
                    ถึง
                  </span>
                  <input
                    type="date"
                    className="input-inline date-input-inline tabular"
                    value={endDate}
                    aria-label="วันที่สิ้นสุด"
                    onChange={(e) => setEndDate(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        triggerBacktest({ rangeMode: "calendar" });
                      }
                    }}
                  />
                </div>
                <div className="chip-row">
                  {CALENDAR_PRESETS.map((preset) => {
                    const resolved = resolveCalendarPreset(preset.id as CalendarPresetId);
                    const isActive =
                      resolved.startDate === startDate && resolved.endDate === endDate;
                    return (
                      <button
                        key={preset.id}
                        type="button"
                        className={`chip-btn ${isActive ? "is-active" : ""}`}
                        onClick={() => {
                          const next = resolveCalendarPreset(preset.id);
                          setRangeMode("calendar");
                          setStartDate(next.startDate);
                          setEndDate(next.endDate);
                          triggerBacktest({
                            rangeMode: "calendar",
                            startDate: next.startDate,
                            endDate: next.endDate,
                          });
                        }}
                      >
                        {preset.label_th}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="backtest-inline-row">
                <input
                  type="number"
                  className="input-inline tabular"
                  value={btBarsInput}
                  min={60}
                  max={50000}
                  step={500}
                  placeholder="1500"
                  style={{ width: 96 }}
                  aria-label="กำหนดจำนวนแท่งย้อนหลังเอง"
                  onChange={(e) => setBtBarsInput(e.target.value)}
                  onBlur={() => {
                    const safeBars = normalizeBarCount(btBarsInput);
                    setBtBars(safeBars);
                    setBtBarsInput(String(safeBars));
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      triggerBacktest({ rangeMode: "bars" });
                    }
                  }}
                />
                <div className="tv-tf-group">
                  {BAR_COUNTS.map((cnt) => (
                    <button
                      key={cnt}
                      type="button"
                      className={`tv-tf-btn ${btBars === cnt && btBarsInput === String(cnt) ? "is-active" : ""}`}
                      onClick={() => triggerBacktest({ rangeMode: "bars", bars: cnt })}
                    >
                      {cnt} แท่ง
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="backtest-control-group">
            <div className="backtest-label-row">
              <label>ทุนจำลองพอร์ต ({catalog?.live_account_currency ?? "USD"})</label>
              <InlineHintToggle label="ทุนจำลองพอร์ต" hint={LAB_HINTS.btCapital} forceOpen={showAllHints} />
            </div>
            <div className="backtest-inline-row">
              <input
                type="number"
                className="input-inline tabular"
                value={capitalInput}
                min={10}
                step="any"
                placeholder={
                  catalog?.live_account_equity ? `MT5 (${catalog.live_account_equity})` : "10000"
                }
                style={{ width: 122 }}
                aria-label="ทุนจำลองพอร์ต (USD)"
                onChange={(e) => setCapitalInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    triggerBacktest();
                  }
                }}
              />
            </div>
          </div>

          <div className="backtest-control-action">
            <button
              type="button"
              className="btn btn-primary"
              disabled={runningBacktest}
              onClick={() => triggerBacktest()}
            >
              <Play size={14} />
              <span>{runningBacktest ? "กำลังคำนวณ..." : "รันทดสอบ"}</span>
            </button>
          </div>
        </div>

        {/* Toggle for Defensive-Aware vs Pure Offensive Simulation */}
        <div className="backtest-defensive-toggle-row">
          <label className="defensive-switch-label">
            <input
              type="checkbox"
              checked={includeDefensive}
              onChange={(e) => {
                const next = e.target.checked;
                setIncludeDefensive(next);
                triggerBacktest({ withDefensive: next });
              }}
            />
            <span>
              <strong>จำลองรวมค่า Defensive จากหน้าตั้งค่าบอท</strong> (คำนวณ Spread จริง + กรอง MIN_NET_RR + ระยะ SL ขั้นต่ำ + Auto-Breakeven + Trailing Stop)
            </span>
          </label>
          <InlineHintToggle
            label="โหมดจำลองรวมค่า Defensive"
            titleText="อธิบายโหมดจำลองนี้"
            hint={LAB_HINTS.btDefensive}
            forceOpen={showAllHints}
          />
        </div>

        {backtestResult?.mode === "DEFENSIVE_AWARE" && (report?.blocked_signals ?? 0) > 0 && (
          <div className="blocked-summary-strip">
            <div className="blocked-summary-head">
              <strong>ด่าน Defensive บล็อกสัญญาณ {backtestResult.timeframe} ไปทั้งหมด {report?.blocked_signals} ครั้ง:</strong>{" "}
              <span>{blockedEntries.map(([reason, cnt]) => `${reason} (${cnt})`).join(" • ")}</span>
            </div>
            {report?.blocked_details && report.blocked_details.length > 0 && (
              <ul className="blocked-detail-list">
                {report.blocked_details.map((bd) => (
                  <li key={bd.reason}>
                    <div>
                      <strong>{bd.reason} ({bd.count} ครั้ง):</strong> {bd.why_th}
                    </div>
                    {bd.samples && bd.samples.length > 0 && (
                      <div className="blocked-sample-row">
                        <span className="blocked-sample-label">เวลาที่ถูกบล็อก (ไทย):</span>
                        {bd.samples.map((s, idx) => (
                          <code key={`${s.time}-${idx}`} title={s.detail}>
                            {formatThaiDateTime(s.time)} ({s.side})
                          </code>
                        ))}
                      </div>
                    )}
                    <div className="blocked-fix-note">
                      <strong>วิธีปรับแก้:</strong> {bd.fix_th}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {backtestResult?.actual_start_time && backtestResult?.actual_end_time && (
          <div className="range-meta-strip tabular">
            <Calendar size={14} className="text-brand" style={{ flexShrink: 0 }} />
            <span>
              <strong>ช่วงข้อมูลจริงที่จำลอง:</strong>{" "}
              {formatThaiDateSpan(backtestResult.actual_start_time, backtestResult.actual_end_time)} ({backtestResult.candles.length.toLocaleString("en-US")} แท่งเทียน{backtestResult.range_mode === "calendar" ? " • วอร์มอินดิเคเตอร์ล่วงหน้าก่อนวันเริ่มแล้ว" : ""})
            </span>
          </div>
        )}

        {backtestResult?.range_notice_th && (
          <div className="portfolio-sim-note" style={{ marginTop: 10 }}>
            <strong>หมายเหตุข้อมูลประวัติ:</strong> {backtestResult.range_notice_th}
          </div>
        )}

        {backtestError && (
          <p className="text-danger" style={{ marginTop: 12 }}>
            {backtestError}
          </p>
        )}

        {/* 4 Summary KPI Cards */}
        {report && (
          <div className="grid-4" style={{ marginTop: 16 }}>
            <StatCard
              label="อัตราชนะ (Win Rate)"
              value={`${report.win_rate_pct.toFixed(1)}%`}
              hint={`ปิดแล้ว ${report.wins + report.losses} ไม้ (ชนะ ${report.wins} / แพ้ ${report.losses})${
                report.open_trades > 0 ? ` • ค้าง ${report.open_trades} ไม้` : ""
              }${
                (report.breakeven_loss_trades ?? 0) > 0
                  ? ` • BE หักค่าคอมฯลบ ${report.breakeven_loss_trades}`
                  : ""
              }`}
              icon={Award}
              valueTone={report.win_rate_pct >= 50 ? "success" : "warning"}
              helpHint={LAB_HINTS.kpiWinRate}
              forceOpen={showAllHints}
            />
            <StatCard
              label="ผลตอบแทนรวม (MTM Total R)"
              value={`${report.total_r >= 0 ? "+" : ""}${report.total_r.toFixed(2)} R`}
              hint={
                report.open_trades > 0 && report.closed_total_r !== undefined && report.open_r !== undefined
                  ? `ปิดแล้ว ${report.closed_total_r >= 0 ? "+" : ""}${report.closed_total_r.toFixed(2)} R • ค้าง ${
                      report.open_r >= 0 ? "+" : ""
                    }${report.open_r.toFixed(2)} R • เฉลี่ย ${report.expectancy_r >= 0 ? "+" : ""}${report.expectancy_r.toFixed(2)} R`
                  : `Expectancy ${report.expectancy_r >= 0 ? "+" : ""}${report.expectancy_r.toFixed(2)} R/ไม้ • ${
                      report.defensive_applied ? "Net R หลังหักต้นทุน" : "Pure R (ยังไม่หักสเปรด/คอมฯ)"
                    }`
              }
              icon={TrendingUp}
              valueTone={report.total_r >= 0 ? "success" : "danger"}
              helpHint={LAB_HINTS.kpiTotalR}
              forceOpen={showAllHints}
            />
            <StatCard
              label="Profit Factor (MTM)"
              value={
                report.profit_factor === null
                  ? report.wins > 0
                    ? "∞"
                    : "0.00"
                  : report.profit_factor.toFixed(2)
              }
              hint={`${report.defensive_applied ? "คิด Net หลังต้นทุน+ไม้ค้าง" : "Pure MTM รวมไม้ค้าง"} • ${report.bars_tested} แท่งเทียน`}
              icon={BarChart3}
              valueTone={(report.profit_factor ?? 0) >= 1.2 || (report.profit_factor === null && report.wins > 0) ? "brand" : "default"}
              helpHint={LAB_HINTS.kpiProfitFactor}
              forceOpen={showAllHints}
            />
            <StatCard
              label="Max Drawdown (รวมลาก)"
              value={`-${report.max_drawdown_r.toFixed(2)} R`}
              hint={
                report.closed_max_drawdown_r !== undefined &&
                report.max_drawdown_r > report.closed_max_drawdown_r + 0.05
                  ? `เฉพาะปิดไม้ -${report.closed_max_drawdown_r.toFixed(2)} R${
                      report.open_trades > 0 ? ` • ค้าง ${report.open_trades} ไม้` : ""
                    }`
                  : report.open_trades > 0
                  ? `ยังมี ${report.open_trades} ไม้เปิดถึงแท่งล่าสุด`
                  : "ปิดครบทุกไม้ในกรอบทดสอบ"
              }
              icon={Activity}
              valueTone={report.max_drawdown_r >= 4 ? "danger" : "default"}
              helpHint={LAB_HINTS.kpiDrawdown}
              forceOpen={showAllHints}
            />
          </div>
        )}

        {/* Portfolio, Lot Sizing, Margin & Ending Equity Summary */}
        {report && portfolio && (
          <div className="portfolio-sim-panel">
            <div className="portfolio-sim-head">
              <div className="portfolio-sim-title-row">
                <Wallet size={17} className="text-brand" />
                <strong>สรุปจำลองพอร์ต เงินทุน มาร์จิ้น และ Lot จริงหลังไม้ล่าสุด</strong>
                <InlineHintToggle
                  label="สรุปพอร์ต เงินทุน และมาร์จิ้น"
                  hint={LAB_HINTS.portfolioSummary}
                  forceOpen={showAllHints}
                />
              </div>
              <div className="portfolio-sim-pills tabular">
                {portfolio.account_blown && (
                  <StatusBadge tone="danger">ล้างพอร์ตแล้ว (หยุดเทรดทันที)</StatusBadge>
                )}
                <StatusBadge tone="info">
                  {LOT_MODE_LABELS[portfolio.lot_mode] ?? portfolio.lot_mode}
                  {portfolio.lot_mode === "dynamic_risk" ? ` (${portfolio.risk_percent}%)` : ""}
                </StatusBadge>
                <StatusBadge tone="neutral">Leverage 1:{portfolio.account_leverage}</StatusBadge>
                <StatusBadge tone="neutral">ทุนเริ่มต้น {formatMoney(portfolio.starting_equity, portfolio.currency)}</StatusBadge>
              </div>
            </div>

            <div className="grid-4" style={{ marginTop: 12 }}>
              <StatCard
                label={`พอร์ตหลังไม้ล่าสุด (${portfolio.currency})`}
                value={
                  portfolio.account_blown
                    ? `${formatMoney(portfolio.ending_equity, portfolio.currency)} (ล้างพอร์ต)`
                    : formatMoney(portfolio.ending_equity, portfolio.currency)
                }
                hint={`กำไร/ขาดทุนสุทธิ ${formatMoney(portfolio.net_profit_money, portfolio.currency, true)} (${
                  portfolio.net_profit_pct >= 0 ? "+" : ""
                }${portfolio.net_profit_pct.toFixed(2)}%)`}
                icon={Wallet}
                valueTone={portfolio.net_profit_money >= 0 && !portfolio.account_blown ? "success" : "danger"}
              />
              <StatCard
                label="ขนาด Lot ที่ออกต่อไม้"
                value={portfolio.avg_lot > 0 ? `${portfolio.avg_lot.toFixed(2)} Lot (เฉลี่ย)` : "0.00 Lot"}
                hint={
                  portfolio.avg_lot > 0
                    ? `ต่ำสุด ${portfolio.min_lot.toFixed(2)} – สูงสุด ${portfolio.max_lot.toFixed(2)} • ล่าสุด ${portfolio.last_lot.toFixed(2)} Lot`
                    : "ยังไม่มีไม้ที่ออก Lot ได้"
                }
                icon={Coins}
                valueTone="brand"
              />
              <StatCard
                label={`Margin ตอนออกล็อต (${portfolio.currency})`}
                value={`${formatMoney(portfolio.avg_margin_used, portfolio.currency)} / ไม้`}
                hint={`สูงสุด ${formatMoney(portfolio.max_margin_used, portfolio.currency)} • ล่าสุด ${formatMoney(
                  portfolio.last_margin_used,
                  portfolio.currency
                )}${portfolio.last_margin_level_pct ? ` (ML ${portfolio.last_margin_level_pct.toFixed(0)}%)` : ""}`}
                icon={Gauge}
                valueTone="default"
              />
              <StatCard
                label="Max Drawdown พอร์ตจริง"
                value={`-${formatMoney(portfolio.max_drawdown_money, portfolio.currency)}`}
                hint={`คิดเป็น -${portfolio.max_drawdown_pct.toFixed(2)}%${
                  portfolio.closed_max_drawdown_pct !== undefined &&
                  portfolio.max_drawdown_pct > portfolio.closed_max_drawdown_pct + 0.2
                    ? ` (ปิดไม้ -${portfolio.closed_max_drawdown_pct.toFixed(2)}%)`
                    : ""
                } • ML ต่ำสุด ${
                  portfolio.min_margin_level_pct ? `${portfolio.min_margin_level_pct.toFixed(0)}%` : "-"
                } • ลดล็อต ${portfolio.scaled_down_trades} ครั้ง`}
                icon={Activity}
                valueTone={portfolio.max_drawdown_pct >= 8 ? "danger" : "warning"}
              />
            </div>

            {portfolio.account_blown && (
              <div className="blown-alert-banner">
                <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <strong>ล้างพอร์ตแล้ว — ระบบหยุดการเทรดย้อนหลังทันทีที่ไม้ที่ {trades.length}:</strong>{" "}
                  {portfolio.blown_reason ?? "เงินทุนหมด / โดน Stop Out"}
                  {portfolio.blown_time ? ` ณ เวลา ${formatThaiDateTime(portfolio.blown_time)}` : ""}
                  {portfolio.bars_until_blown ? ` (รอดมาได้ ${portfolio.bars_until_blown.toLocaleString("en-US")} แท่ง)` : ""}
                </div>
              </div>
            )}

            {portfolio.below_min_lot_fallback_trades > 0 && (
              <div className="portfolio-sim-note">
                <strong>ข้อสังเกตขนาด Lot:</strong> มี {portfolio.below_min_lot_fallback_trades} ไม้ที่งบความเสี่ยง{" "}
                {portfolio.risk_percent}% ของทุน {formatMoney(portfolio.starting_equity, portfolio.currency)} น้อยกว่าความเสี่ยงของขั้นต่ำ 0.01 Lot{" "}
                — ในโหมด Pure Offensive จึงจำลองด้วย 0.01 Lot ให้เห็นยอดเงิน (หากเปิดโหมดรวม Defensive หรือรันบอทจริง บอทจะงดเข้าไม้เหล่านั้นจนกว่าจะเพิ่มทุนหรือ Risk%)
              </div>
            )}
          </div>
        )}
      </SectionCard>

      {/* 4. Interactive Replay TradingView Chart */}
      <SectionCard
        title="4. กราฟจำลองจุดเข้า-ออก M5 (Strategy Replay Chart)"
        subtitle="แสดงจุดเข้าซื้อ-ขาย, เส้น EMA 50/200 และหมุด TP/SL — คลิกที่แถวรายการไม้ด้านล่างเพื่อซูมดูจุดเข้า SL และ TP บนกราฟ"
        icon={<ChartCandlestick size={18} />}
        headerRight={
          selectedTrade && (
            <button type="button" className="btn btn-sm" onClick={() => setSelectedTradeIndex(null)}>
              ล้างกรอบโฟกัสไม้ที่เลือก
            </button>
          )
        }
      >
        <TradingChart
          symbol={backtestResult?.symbol ?? btSymbol}
          timeframe={backtestResult?.timeframe ?? btTimeframe}
          theme={theme}
          height={460}
          externalCandlesData={replayCandlesData}
          backtestTrades={trades}
          selectedBacktestTrade={selectedTrade}
          defaultShowNews={false}
        />
      </SectionCard>

      {/* 5. Clickable Simulated Trade History */}
      <SectionCard
        title="5. ประวัติไม้จำลอง (Simulated Trades)"
        subtitle="คลิกที่แถวใดก็ได้เพื่อให้กราฟเลื่อนไปยังแท่งเทียนนั้นพร้อมตีเส้น Entry, SL, TP และจุดปิดไม้ให้เห็นชัดเจน"
        icon={<Crosshair size={18} />}
        headerRight={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <InlineHintToggle
              label="ความหมายคอลัมน์ตารางผลลัพธ์"
              titleText="อ่านตารางยังไง"
              hint={LAB_HINTS.replayTable}
              forceOpen={showAllHints}
            />
            {trades.length > DEFAULT_TABLE_LIMIT && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setShowAllTrades((prev) => !prev)}
              >
                {showAllTrades
                  ? `ย่อเหลือ ${DEFAULT_TABLE_LIMIT} ไม้ล่าสุด`
                  : `แสดงครบทั้งหมด (${trades.length} ไม้)`}
              </button>
            )}
            <StatusBadge tone="neutral">{trades.length} รายการ</StatusBadge>
          </div>
        }
      >
        {trades.length === 0 ? (
          <p className="text-muted">
            {backtestResult?.range_mode === "calendar"
              ? `ไม่พบจุดเข้าตามเงื่อนไขในช่วงวันที่ ${startDate} ถึง ${endDate} — ลองขยายช่วงวันที่ หรือตรวจดูว่าด่าน Defensive บล็อกอยู่หรือไม่`
              : `ไม่พบจุดเข้าตามเงื่อนไขในช่วง ${btBars} แท่งล่าสุด — ลองปรับจำนวนแท่งเป็น 5000 หรือ 20000 หรือตรวจดูว่าด่าน Defensive บล็อกอยู่หรือไม่`}
          </p>
        ) : (
          <div className="table-wrap">
            <table className="backtest-table tabular">
              <thead>
                <tr>
                  <th>#</th>
                  <th>ฝั่ง</th>
                  <th>เวลาเข้า (ไทย)</th>
                  <th>ราคาเข้า</th>
                  <th>SL</th>
                  <th>TP</th>
                  <th>ราคาปิด</th>
                  <th>Lot</th>
                  <th>Margin</th>
                  <th>ระยะเวลา (แท่ง)</th>
                  <th>ผลลัพธ์ (R)</th>
                  <th>กำไร/ขาดทุน</th>
                  <th>พอร์ตหลังไม้</th>
                  <th>สถานะ / เหตุผล</th>
                </tr>
              </thead>
              <tbody>
                {(showAllTrades ? [...trades] : trades.slice(-DEFAULT_TABLE_LIMIT))
                  .reverse()
                  .map((tr, revIdx) => {
                    const realIdx = trades.length - 1 - revIdx;
                  const isRowSelected = selectedTradeIndex === realIdx;
                  const lotVal = tr.lot ?? 0;
                  const marginVal = tr.margin_used ?? 0;
                  const pnlVal = tr.pnl_money ?? 0;
                  const eqAfter = tr.equity_after ?? 0;
                  const ccy = portfolio?.currency ?? "USD";
                  return (
                    <tr
                      key={`${realIdx}-${tr.entry_time}`}
                      className={`backtest-row ${isRowSelected ? "is-selected" : ""}`}
                      onClick={() => setSelectedTradeIndex(isRowSelected ? null : realIdx)}
                    >
                      <td>#{realIdx + 1}</td>
                      <td>
                        <StatusBadge tone={tr.side === "BUY" ? "success" : "danger"}>{tr.side}</StatusBadge>
                      </td>
                      <td>{formatThaiDateTime(tr.entry_time)}</td>
                      <td>{formatPrice(tr.entry_price, digits)}</td>
                      <td className="text-danger">{formatPrice(tr.sl_price, digits)}</td>
                      <td className="text-success">{formatPrice(tr.tp_price, digits)}</td>
                      <td>{formatPrice(tr.exit_price, digits)}</td>
                      <td>{lotVal > 0 ? lotVal.toFixed(2) : "-"}</td>
                      <td>
                        {marginVal > 0 ? (
                          <span title={tr.margin_level_pct ? `Margin Level: ${tr.margin_level_pct.toFixed(0)}%` : ""}>
                            {formatMoney(marginVal, ccy)}
                          </span>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>{tr.bars_held}</td>
                      <td className={tr.r_multiple >= 0 ? "text-success fw-600" : "text-danger fw-600"}>
                        {tr.r_multiple >= 0 ? "+" : ""}
                        {tr.r_multiple.toFixed(2)} R
                      </td>
                      <td className={pnlVal >= 0 ? "text-success fw-600" : "text-danger fw-600"}>
                        {lotVal > 0 ? formatMoney(pnlVal, ccy, true) : "-"}
                      </td>
                      <td>{lotVal > 0 ? formatMoney(eqAfter, ccy) : "-"}</td>
                      <td title={tr.reason}>
                        <StatusBadge
                          tone={
                            tr.outcome === "WIN"
                              ? "success"
                              : tr.outcome === "LOSS" || tr.outcome === "STOP_OUT"
                              ? "danger"
                              : "info"
                          }
                        >
                          {tr.outcome === "WIN"
                            ? tr.reason.includes("[TIME STOP")
                              ? "ปิดทำกำไร (หมดเวลา)"
                              : tr.reason.includes("[BE/Trail]")
                              ? "กำไร (BE/Trail)"
                              : "ชน TP"
                            : tr.outcome === "STOP_OUT"
                            ? "Stop Out (ล้างพอร์ต)"
                            : tr.outcome === "LOSS"
                            ? tr.reason.includes("[TIME STOP")
                              ? "ตัดปิด (หมดเวลา)"
                              : tr.reason.includes("[BE/Trail]")
                              ? "BE/Trail (หักต้นทุนลบ)"
                              : "โดน SL"
                            : "เปิดอยู่"}
                        </StatusBadge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* 6. Architecture Summary Note */}
      <details className="strategy-dev-guide">
        <summary>
          <FileCode2 size={16} />
          <span>โครงสร้างแยกส่วน Offensive vs Defensive สำหรับพัฒนาต่อ</span>
        </summary>
        <div className="strategy-dev-guide-body">
          <p>
            กลยุทธ์หลักที่ใช้งานคือ <code>M5KernelPullback</code> (<code>src/cfd_bot/domain/strategies/m5_kernel_pullback.py</code>)
            ลงทะเบียนอยู่ใน <code>STRATEGY_CLASSES</code> ภายใน <code>registry.py</code> สามารถเพิ่มกลยุทธ์ใหม่หรือแก้ไขได้อิสระโดยไม่กระทบระบบ <code>PreventiveGuard</code> และ <code>RiskEngine</code>
          </p>
        </div>
      </details>
    </div>
  );
}
