import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Calculator,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Coins,
  Gauge,
  Info,
  Layers,
  RotateCcw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  TrendingUp,
} from "lucide-react";
import { api } from "../../data/api";
import type {
  BotConfigData,
  OverviewResponse,
  SimulateResponse,
  StrategiesResponse,
  StrategyDescriptor,
} from "../../domain/types";
import { FieldWithHint, OptionalNumberField, type HelpHintContent } from "../components/ui/HelpHint";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  onNotify: (msg: string) => void;
  onSaved: () => void;
}

type StepNumber = 1 | 2 | 3 | 4 | 5;
type ViewMode = "wizard" | "all";

const QUICK_SYMBOLS = ["XAUUSD", "EURUSD", "US30", "NAS100"];
const TIMEFRAMES = ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"];
const QUICK_POLLS = [
  { val: 1, label: "1 วินาที (HFT/M1)" },
  { val: 2, label: "2 วินาที (M3)" },
  { val: 5, label: "5 วินาที (M5 มาตรฐาน)" },
  { val: 15, label: "15 วินาที (H1)" },
];

const LOT_MODE_LABELS: Record<BotConfigData["risk"]["lot_mode"], string> = {
  dynamic_risk: "เสี่ยง % ต่อไม้ (แนะนำ)",
  dynamic_equity_step: "เพิ่ม Lot ตามขั้นเงินทุน",
  fixed: "ขนาด Lot คงที่",
};

const HINTS: Record<string, HelpHintContent> = {
  symbol: {
    summary:
      "สินค้าหลักที่บอทจะใช้สแกนกราฟและเปิดออเดอร์อัตโนมัติ โดยระบบจับคู่ตัวต่อท้ายของโบรคเกอร์ให้เอง (เช่น XAUUSDm, EURUSDm)",
    example: "เลือก XAUUSD หรือพิมพ์ชื่อสินค้าแล้วระบบหาคู่ใน MT5 ให้อัตโนมัติ",
    recommended: "XAUUSD หรือ EURUSD",
  },
  timeframe: {
    summary:
      "ขนาดแท่งเทียนที่บอทใช้อ่านสัญญาณเข้า-ออก ควรตรงกับไทม์เฟรมที่กลยุทธ์แนะนำเพื่อไม่ให้สัญญาณผิดเพี้ยนจากผลการจำลอง Backtest",
    example: "M5 เหมาะกับ M5KernelPullback และ M5RangeScalper; H1 เหมาะกับ VolatilitySqueezeBreakout",
    recommended: "ตรงตามป้ายแนะนำของกลยุทธ์ที่เปิดใช้อยู่",
  },
  poll_seconds: {
    summary: "ทุกกี่วินาทีที่บอทจะตื่นมาดึงราคา ตรวจข่าว และเช็กออเดอร์จาก MT5 ระหว่างกดเริ่มทำงาน",
    example: "5 วินาที เหมาะกับ M5; 15 วินาที เหมาะกับ H1",
    recommended: "5 วินาที (M5) หรือ 15 วินาที (H1)",
  },
  magic_number: {
    summary:
      "เลขรหัสประจำตัวบอท เพื่อแยกออเดอร์ที่บอทเปิดออกจากออเดอร์ที่คุณกดเปิดมือเองใน MT5 เวลาเลื่อน SL จะไม่ไปยุ่งกับไม้กดมือ",
    example: "202611",
    recommended: "คงเลขเดิมไว้เพื่อให้ประวัติรวมต่อเนื่อง",
  },
  deviation_points: {
    summary:
      "ระยะคลาดเคลื่อนของราคาสูงสุดที่ยอมรับได้ตอนส่งคำสั่ง (Slippage Tolerance เป็นจุด) ถ้าราคาวิ่งหนีเกินนี้คำสั่งจะไม่ถูกจับคู่",
    recommended: "20 – 50 จุด",
  },
  lot_mode: {
    summary:
      "เลือกวิธีคำนวณขนาดไม้: (1) เสี่ยง % ต่อไม้ — คุมขาดทุนไม่เกินงบแม้ SL กว้างแคบต่างกัน, (2) ตามขั้นเงินทุน — พอร์ตโตค่อยขยับ Lot เป็นขั้น, (3) Lot คงที่ — ออกขนาดเดิมตลอด",
    example: "หากอยากให้ไม่ล้างพอร์ตง่าย แนะนำ 'เสี่ยง % ต่อไม้' เพราะเมื่อ SL กว้าง Lot จะลดลงให้เอง",
    recommended: "เสี่ยง % ต่อไม้ (Dynamic Risk)",
  },
  fixed_lot: {
    summary: "ขนาด Lot คงที่ที่จะส่งเปิดทุกครั้งเมื่อเลือกโหมด 'ขนาด Lot คงที่'",
    example: "0.01 Lot คือขนาดเล็กสุดมาตรฐาน",
    recommended: "0.01 – 0.05 Lot (ปรับให้เหมาะกับทุนพอร์ต)",
  },
  risk_percent: {
    summary: "เปอร์เซ็นต์ของเงินทุนที่ยอมเสียได้สูงสุดต่อ 1 ไม้ หากราคาวิ่งไปชนจุดตัดขาดทุน (SL)",
    example: "ทุน 1,000 USD ตั้ง 1% = ถ้าชน SL จะขาดทุนไม่เกินประมาณ 10 USD (รวม Spread/คอมแล้ว)",
    recommended: "0.5% – 2.0% ต่อไม้",
  },
  equity_step_amount: {
    summary: "ทุกๆ เงินทุนกี่ USD จะให้เพิ่มขนาด Lot ตามจำนวนด้านขวา (ปัดลงเสมอเพื่อไม่ให้ Over-lot)",
    example: "ตั้ง 1,000 USD ต่อ 0.01 Lot — ทุน 1,900 ได้ 0.01 Lot, ทุน 2,000 ได้ 0.02 Lot",
  },
  equity_step_lot: {
    summary: "ขนาด Lot ที่เพิ่มให้ต่อ 1 ขั้นเงินทุน",
    example: "0.01 Lot ต่อทุก 1,000 USD (หรือ 0.05 ต่อ 300 USD สำหรับสายเร่งพอร์ต)",
  },
  min_sl_points: {
    summary:
      "บังคับระยะ SL ขั้นต่ำในสูตรคำนวณ Lot (กัน Lot พุ่งใหญ่เกินเหตุเมื่อ SL แคบมาก) — เว้นว่างเพื่อใช้เฉพาะขั้นต่ำโบรคเกอร์",
  },
  max_lot_cap: {
    summary: "เพดาน Lot สูงสุดต่อ 1 ไม้ — เว้นว่างเพื่อไม่จำกัดเพดานนี้ (อิงตามลิมิตโบรคเกอร์)",
    example: "ใส่ 0.50 = ห้ามเปิดเกิน 0.50 Lot",
  },
  max_effective_account_leverage: {
    summary:
      "คุมมูลค่าสัญญารวมทุกไม้ในพอร์ตไม่ให้เกินกี่เท่าของทุนจริง — เว้นว่างเพื่อปิดเพดานนี้",
    example: "พอร์ต 1,000 USD ตั้ง 20 เท่า = มูลค่ารวมทุกไม้ไม่เกิน 20,000 USD",
    recommended: "15 – 25 เท่า",
  },
  max_effective_symbol_leverage: {
    summary: "คุมมูลค่ารวมเฉพาะสินค้าตัวเดียวกันไม่ให้เกินกี่เท่าของทุน — เว้นว่างเพื่อไม่จำกัดรายสินค้า",
  },
  max_margin_usage_pct: {
    summary:
      "สัดส่วนเงินมัดจำ (Used Margin) สูงสุดที่ใช้ได้จากพอร์ต — เว้นว่างเพื่อใช้ตาม Free Margin จริงของบัญชี",
    example: "ใส่ 25 = ใช้มัดจำไม่เกิน 25% ของทุน",
    recommended: "20% – 30%",
  },
  min_margin_level_pct: {
    summary: "ระดับ % Margin Level ขั้นต่ำหลังเปิดไม้ — เว้นว่างหากไม่ต้องการกำหนดพื้นเพิ่ม",
    recommended: "500% ขึ้นไป",
  },
  min_stopout_buffer_multiplier: {
    summary:
      "ตัวคูณระยะปลอดภัยเหนือระดับ Stop Out ของโบรคเกอร์ (เช่น Stop Out 50% คูณ 3 = ต้องเหลืออย่างน้อย 150%) — เว้นว่างเพื่อปิด",
  },
  max_spread_points: {
    summary: "ค่า Spread สูงสุด (จุด) หากเกินจะไม่ออกออเดอร์ — เว้นว่างเพื่อตรวจแบบสัดส่วนเทียบ TP แทน",
    example: "เว้นว่างเหมาะเวลาสลับเทรดหลายสินค้าที่จำนวนจุดต่างกันมาก (เช่น XAUUSD กับ EURUSD)",
  },
  max_spread_to_tp_ratio: {
    summary: "Spread คิดเป็นเงินต้องไม่เกินกี่ส่วนของระยะทำกำไร TP — เว้นว่างหากไม่ต้องการเช็ก",
    example: "0.25 = Spread ต้องไม่เกิน 25% ของระยะ TP (ช่วยกันโดนกินกำไรตั้งแต่เปิดไม้)",
    recommended: "0.25",
  },
  max_spread_to_atr_ratio: {
    summary: "Spread ต้องไม่เกินกี่ส่วนของความผันผวน ATR — เว้นว่างเพื่อปิดข้อนี้",
    example: "0.35 = Spread ต้องไม่เกิน 35% ของแท่ง ATR",
    recommended: "0.35",
  },
  commission_per_lot_roundtrip: {
    summary:
      "ค่าคอมมิชชันไป-กลับ (USD ต่อ 1 Lot) กรณีโบรคเกอร์ไม่ส่งค่าคอมจริงจากประวัติ — เว้นว่างหรือใส่ 0 หากบัญชีไม่มีค่าคอม (เช่น Standard)",
    example: "บัญชี Standard ใส่ 0 หรือเว้นว่าง; บัญชี Raw ใส่ 7",
  },
  estimated_slippage_points: {
    summary: "ระยะ Slippage เผื่อคลาดเคลื่อน (จุด) สำหรับใช้คำนวณต้นทุนสุทธิ — เว้นว่างหรือใส่ 0 หากไม่ต้องเผื่อ",
    recommended: "5 จุด",
  },
  min_net_rr: {
    summary:
      "ความคุ้มค่าสุทธิขั้นต่ำ (Net R:R หลังหัก Spread, ค่าคอม และ Slippage แล้ว) — ควรตั้งต่ำกว่า TP R:R ของกลยุทธ์เล็กน้อยเพื่อไม่ให้โดนบล็อกทุกไม้",
    example: "ถ้ากลยุทธ์ตั้ง TP 1.25 R:R ควรตั้ง MIN_NET_RR ไม่เกิน 1.00",
    recommended: "1.00",
  },
  max_swap_to_tp_ratio: {
    summary: "ค่า Swap คาดการณ์ต้องไม่เกินกี่ส่วนของระยะกำไร TP — เว้นว่างเพื่อไม่ตรวจข้อนี้",
  },
  rollover_blackout: {
    summary:
      "ช่วงเปลี่ยนวันของเซิร์ฟเวอร์ (เช่น 00:00 น.) ที่สเปรดมักถ่างผิดปกติ หากกลยุทธ์ไม่ได้ถือข้ามคืนสามารถเปิดเพื่อเลี่ยงช่วงดังกล่าวได้",
  },
  max_daily_loss_pct: {
    summary: "เบรกเกอร์หยุดพักอัตโนมัติเมื่อขาดทุนสะสมวันนี้ถึง % นี้ — เว้นว่างหากไม่ต้องการหยุดอัตโนมัติ",
    example: "ใส่ 3 = ขาดทุนครบ 3% ของวันหยุดเปิดไม้ใหม่จนถึงวันถัดไป",
    recommended: "2% – 5% (หรือเว้นว่างเวลาแบ็กเทสต์ยาว)",
  },
  max_open_positions_symbol: {
    summary: "จำกัดจำนวนออเดอร์ที่เปิดพร้อมกันต่อ 1 สินค้า — เว้นว่างหากไม่จำกัด",
    recommended: "1 ไม้",
  },
  max_open_positions_total: {
    summary: "จำกัดจำนวนออเดอร์รวมทุกสินค้าในพอร์ต — เว้นว่างหากไม่จำกัด",
    recommended: "3 ไม้",
  },
  max_quote_age_seconds: {
    summary: "ห้ามเข้าออเดอร์ถ้าราคาล่าสุดค้างนานเกินกี่วินาที (มักเกิดตอนตลาดใกล้ปิดหรือเน็ตหลุด)",
    recommended: "30 วินาที",
  },
  holiday_policy: {
    summary: "วิธีจัดการเมื่อตรงกับวันหยุดธนาคารใหญ่ (งดเปิดไม้ใหม่ หรือ เตือนอย่างเดียว)",
  },
  news_block_minutes_before: {
    summary: "หยุดเข้าออเดอร์ก่อนข่าวเศรษฐกิจแรง (สีแดง) ออกกี่นาที — เว้นว่างเพื่อปิดการบล็อกก่อนข่าว",
    recommended: "30 นาที",
  },
  news_block_minutes_after: {
    summary: "รอหลังข่าวแรงออกกี่นาทีก่อนกลับมาเทรด — เว้นว่างเพื่อปิดการรอหลังข่าว",
    recommended: "30 นาที",
  },
  news_warn_hours: {
    summary: "แสดงแถบเตือนข่าวแรงล่วงหน้ากี่ชั่วโมง",
    recommended: "12 ชั่วโมง",
  },
  breakeven_trigger_r: {
    summary:
      "เมื่อกำไรลอยตัวถึงกี่เท่าของความเสี่ยง (R) จึงจะเลื่อน SL มาบังทุนแท้ — ควรตั้งใกล้เป้า TP ของกลยุทธ์เพื่อไม่ให้โดนสะบัดปิดเสมอทุนเร็วเกินไป",
    example: "TP 1.25 R:R ควรตั้ง Break-Even ประมาณ 1.10 – 1.15 R",
    recommended: "1.15 R",
  },
  breakeven_lock_points: {
    summary: "บวกล็อกกำไรเพิ่มอีกกี่จุดเหนือจุดเท่าทุนแท้ (รวมค่าคอมและ Slippage ให้แล้ว)",
    recommended: "5 จุด",
  },
  trailing_atr_mult: {
    summary: "ระยะห่างของ Trailing Stop จากราคาปัจจุบันคิดเป็นกี่เท่าของ ATR(14)",
    recommended: "1.5x ATR",
  },
  trailing_step_points: {
    summary: "ระยะขั้นต่ำที่ราคาต้องขยับเพิ่มก่อนส่งคำสั่งแก้ SL เพื่อไม่ให้ยิงแก้ไขออเดอร์ถี่เกินไป",
    recommended: "10 จุด",
  },
};

function configsEqual(a: BotConfigData | null, b: BotConfigData | null): boolean {
  if (!a || !b) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

export function SettingsPage({ onNotify, onSaved }: Props) {
  const [cfg, setCfg] = useState<BotConfigData | null>(null);
  const [savedCfg, setSavedCfg] = useState<BotConfigData | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [strategiesMeta, setStrategiesMeta] = useState<StrategiesResponse | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>("wizard");
  const [activeStep, setActiveStep] = useState<StepNumber>(1);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [showAllHints, setShowAllHints] = useState(false);

  const [showAdvancedMain, setShowAdvancedMain] = useState(false);
  const [showAdvancedRisk, setShowAdvancedRisk] = useState(false);
  const [showAdvancedCost, setShowAdvancedCost] = useState(false);
  const [showAdvancedPrev, setShowAdvancedPrev] = useState(false);

  // Live Lot Preview state (Step 2)
  const [sampleSlPoints, setSampleSlPoints] = useState<string>("300");
  const [simLoading, setSimLoading] = useState(false);
  const [simResult, setSimResult] = useState<SimulateResponse | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [confRes, ovRes, stratRes] = await Promise.all([
        api.getConfig(),
        api.getOverview().catch(() => null),
        api.getStrategies().catch(() => null),
      ]);
      setCfg(confRes);
      setSavedCfg(confRes);
      if (ovRes) setOverview(ovRes);
      if (stratRes) setStrategiesMeta(stratRes);
    } catch (err) {
      onNotify(`โหลดตั้งค่าไม่สำเร็จ: ${(err as Error).message}`);
    }
  }, [onNotify]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const activeDescriptor: StrategyDescriptor | null = useMemo(() => {
    if (!strategiesMeta?.strategies.length) return null;
    return (
      strategiesMeta.strategies.find((s) => s.name === strategiesMeta.active_strategy) ??
      strategiesMeta.strategies[0]
    );
  }, [strategiesMeta]);

  const isDirty = useMemo(() => !configsEqual(cfg, savedCfg), [cfg, savedCfg]);

  // Real-time audit of unsaved draft against the active offensive strategy
  const draftAudit = useMemo(() => {
    const stepWarnings: Record<StepNumber, string[]> = { 1: [], 2: [], 3: [], 4: [], 5: [] };
    if (!cfg) return { stepWarnings, totalCount: 0, allMessages: [] as string[] };

    const recTf = (activeDescriptor?.recommended_timeframe || "").toUpperCase();
    const activeTpRr = Number(strategiesMeta?.active_params?.tp_rr_multiplier ?? 1.25);

    if (!cfg.dry_run) {
      stepWarnings[1].push("กำลังปิดโหมดซ้อม (LIVE MONEY): บอทจะส่งออเดอร์เงินจริงเข้า MT5 ทันทีที่มีสัญญาณ");
    }
    if (recTf && cfg.timeframe.toUpperCase() !== recTf) {
      stepWarnings[1].push(
        `กรอบเวลา ${cfg.timeframe} ไม่ตรงกับกลยุทธ์ ${activeDescriptor?.name} (แนะนำ ${recTf})`
      );
    }
    if (recTf === "M1" && cfg.poll_seconds > 1.5) {
      stepWarnings[1].push(`ความถี่เช็กตลาด ${cfg.poll_seconds}s ช้าเกินไปสำหรับ M1 (แนะนำ 1.0s)`);
    }

    if (
      cfg.risk.max_lot_cap === null &&
      cfg.risk.max_effective_account_leverage === null &&
      cfg.risk.max_margin_usage_pct === null &&
      cfg.risk.min_margin_level_pct === null
    ) {
      stepWarnings[2].push("ปิดเพดานคุมขนาด Lot และ Margin เสริมทั้งหมด (จะจำกัดเฉพาะตาม Margin จริงของโบรคเกอร์)");
    }

    if (cfg.preventive.min_net_rr !== null && cfg.preventive.min_net_rr > Math.max(0, activeTpRr - 0.12)) {
      stepWarnings[3].push(
        `MIN_NET_RR (${cfg.preventive.min_net_rr.toFixed(2)}) สูงกว่าหรือชิดเป้า TP ของกลยุทธ์ (${activeTpRr.toFixed(2)} R:R) — สัญญาณอาจถูกบล็อกทั้งหมด`
      );
    }
    if (
      cfg.preventive.max_spread_to_tp_ratio !== null &&
      cfg.preventive.max_spread_to_tp_ratio > 0 &&
      cfg.preventive.max_spread_to_tp_ratio < 0.22
    ) {
      stepWarnings[3].push("MAX_SPREAD_TO_TP_RATIO ต่ำกว่า 0.22 อาจทำให้สัญญาณถูกบล็อกบ่อยช่วงสเปรดขยับ");
    }

    if (
      cfg.trade_management.enable_auto_breakeven &&
      activeTpRr <= 1.4 &&
      cfg.trade_management.breakeven_trigger_r < Math.max(1.1, activeTpRr - 0.1) - 0.01
    ) {
      stepWarnings[5].push(
        `Auto-BE Trigger (${cfg.trade_management.breakeven_trigger_r.toFixed(2)}R) ใกล้เป้า TP (${activeTpRr.toFixed(2)}R) อาจถูกปัดเสมอทุนบ่อย`
      );
    }

    const allMessages = [
      ...stepWarnings[1],
      ...stepWarnings[2],
      ...stepWarnings[3],
      ...stepWarnings[4],
      ...stepWarnings[5],
    ];
    return { stepWarnings, totalCount: allMessages.length, allMessages };
  }, [activeDescriptor, cfg, strategiesMeta]);

  if (!cfg) {
    return (
      <div className="ops-container">
        <SectionCard title="ตั้งค่าบอทแบบทีละขั้นตอน (Step-by-Step)">
          <p className="text-muted" style={{ margin: 0 }}>
            กำลังโหลดการตั้งค่าจากบอทและ MT5...
          </p>
        </SectionCard>
      </div>
    );
  }

  const persistConfigNow = async () => {
    if (saving) return;
    if (!cfg.dry_run) {
      const ok = window.confirm(
        "คุณกำลังปิดโหมดซ้อม (DRY-RUN OFF) — บอทจะส่งคำสั่งเทรดเข้าบัญชี MT5 จริง ยืนยันหรือไม่?"
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const res = await api.saveConfig(cfg);
      setCfg(res.config);
      setSavedCfg(res.config);
      setJustSaved(true);
      window.setTimeout(() => setJustSaved(false), 2500);
      const freshStrat = await api.getStrategies().catch(() => null);
      if (freshStrat) setStrategiesMeta(freshStrat);
      onNotify("บันทึกการตั้งค่าเรียบร้อย และเริ่มใช้งานค่าใหม่ทันที");
      onSaved();
    } catch (err) {
      onNotify(`บันทึกไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitForm = (e: React.FormEvent) => {
    e.preventDefault();
    void persistConfigNow();
  };

  const handleDiscardChanges = () => {
    if (!savedCfg) return;
    setCfg(savedCfg);
    setSimResult(null);
    onNotify("คืนค่ากลับเป็นค่าที่บันทึกล่าสุดแล้ว");
  };

  const applyStrategyAlignedDraft = () => {
    const recTf = (activeDescriptor?.recommended_timeframe || cfg.timeframe).toUpperCase();
    const activeTpRr = Number(strategiesMeta?.active_params?.tp_rr_multiplier ?? 1.25);
    const safeNetRr = Number(Math.max(0.85, activeTpRr - 0.25).toFixed(2));
    const recBe = Number(Math.max(1.1, activeTpRr - 0.1).toFixed(2));

    setCfg({
      ...cfg,
      timeframe: recTf,
      poll_seconds: recTf === "M1" && cfg.poll_seconds > 1.5 ? 1.0 : cfg.poll_seconds,
      preventive: {
        ...cfg.preventive,
        min_net_rr:
          cfg.preventive.min_net_rr !== null && cfg.preventive.min_net_rr > Math.max(0, activeTpRr - 0.12)
            ? safeNetRr
            : cfg.preventive.min_net_rr,
        max_spread_to_tp_ratio:
          cfg.preventive.max_spread_to_tp_ratio !== null && cfg.preventive.max_spread_to_tp_ratio < 0.22
            ? 0.25
            : cfg.preventive.max_spread_to_tp_ratio,
      },
      trade_management: {
        ...cfg.trade_management,
        breakeven_trigger_r:
          cfg.trade_management.enable_auto_breakeven &&
          activeTpRr <= 1.4 &&
          cfg.trade_management.breakeven_trigger_r < recBe - 0.01
            ? recBe
            : cfg.trade_management.breakeven_trigger_r,
      },
    });
    onNotify(
      `ปรับร่างตั้งค่าให้สอดคล้องกับ ${activeDescriptor?.name ?? "กลยุทธ์หลัก"} (${recTf}) แล้ว — กด "บันทึกการตั้งค่า" เมื่อตรวจสอบครบ`
    );
  };

  const applyQuickProfile = (profile: "safe" | "balanced_step" | "open") => {
    const recTf = (activeDescriptor?.recommended_timeframe || cfg.timeframe).toUpperCase();
    if (profile === "safe") {
      setCfg({
        ...cfg,
        timeframe: recTf,
        poll_seconds: recTf === "H1" ? 15 : 5,
        dry_run: true,
        risk: {
          ...cfg.risk,
          lot_mode: "dynamic_risk",
          risk_percent: 1.0,
          use_equity_for_risk: true,
          include_costs_in_risk_sizing: true,
          max_lot_cap: 0.5,
          max_effective_account_leverage: 20,
          max_effective_symbol_leverage: 15,
          max_margin_usage_pct: 25,
          min_margin_level_pct: 500,
          min_stopout_buffer_multiplier: 3,
          scale_down_lot_for_leverage: true,
        },
        preventive: {
          ...cfg.preventive,
          max_spread_points: null,
          max_spread_to_tp_ratio: 0.25,
          max_spread_to_atr_ratio: 0.35,
          estimated_slippage_points: 5,
          min_net_rr: 1.0,
          max_daily_loss_pct: 3.0,
          max_open_positions_symbol: 1,
          max_open_positions_total: 3,
          max_quote_age_seconds: 30,
          enforce_rollover_blackout_for_intraday: true,
          enable_news_guard: true,
          enable_holiday_guard: true,
          news_block_minutes_before: 30,
          news_block_minutes_after: 30,
          allow_weekend_trading: false,
        },
        trade_management: {
          enable_auto_breakeven: true,
          breakeven_trigger_r: 1.15,
          breakeven_lock_points: 5,
          enable_trailing_stop: true,
          trailing_atr_mult: 1.5,
          trailing_step_points: 10,
        },
      });
      onNotify("โหลดโปรไฟล์ 'ปลอดภัยมาตรฐาน' ลงแบบร่างแล้ว (1% Risk + กันข่าว + กันขาดทุนรายวัน)");
      return;
    }

    if (profile === "balanced_step") {
      setCfg({
        ...cfg,
        timeframe: recTf,
        poll_seconds: 5,
        risk: {
          ...cfg.risk,
          lot_mode: "dynamic_equity_step",
          equity_step_amount: 300,
          equity_step_lot: 0.05,
          max_lot_cap: 0.1,
          min_sl_points: null,
          max_effective_account_leverage: 20,
          max_effective_symbol_leverage: null,
          max_margin_usage_pct: 25,
          min_margin_level_pct: 500,
          min_stopout_buffer_multiplier: null,
          scale_down_lot_for_leverage: true,
        },
        preventive: {
          ...cfg.preventive,
          max_spread_points: null,
          max_spread_to_tp_ratio: 0.25,
          max_spread_to_atr_ratio: 0.35,
          estimated_slippage_points: 5,
          min_net_rr: 1.0,
          max_daily_loss_pct: null,
          max_open_positions_symbol: 1,
          max_open_positions_total: 3,
          enforce_rollover_blackout_for_intraday: false,
          enable_news_guard: false,
          enable_holiday_guard: false,
          allow_weekend_trading: false,
        },
        trade_management: {
          enable_auto_breakeven: true,
          breakeven_trigger_r: 1.15,
          breakeven_lock_points: 5,
          enable_trailing_stop: true,
          trailing_atr_mult: 1.5,
          trailing_step_points: 10,
        },
      });
      onNotify("โหลดโปรไฟล์ 'เติบโตตามขั้นพอร์ตสมดุล' ลงแบบร่างแล้ว");
      return;
    }

    setCfg({
      ...cfg,
      timeframe: recTf,
      risk: {
        ...cfg.risk,
        lot_mode: "dynamic_risk",
        risk_percent: 1.0,
        max_lot_cap: null,
        min_sl_points: null,
        max_effective_account_leverage: null,
        max_effective_symbol_leverage: null,
        max_margin_usage_pct: null,
        min_margin_level_pct: null,
        min_stopout_buffer_multiplier: null,
        scale_down_lot_for_leverage: true,
      },
      preventive: {
        ...cfg.preventive,
        max_spread_points: null,
        max_spread_to_tp_ratio: null,
        max_spread_to_atr_ratio: null,
        estimated_slippage_points: 5,
        min_net_rr: null,
        max_swap_to_tp_ratio: null,
        max_daily_loss_pct: null,
        max_open_positions_symbol: 1,
        max_open_positions_total: 3,
        enforce_rollover_blackout_for_intraday: false,
        enable_news_guard: false,
        enable_holiday_guard: false,
        allow_weekend_trading: false,
      },
    });
    onNotify("โหลดโปรไฟล์ 'เปิดโล่งลดด่านบล็อก' ลงแบบร่างแล้ว (เน้นส่งสัญญาณตามกลยุทธ์)");
  };

  const clearAllRiskCaps = () => {
    setCfg({
      ...cfg,
      risk: {
        ...cfg.risk,
        max_lot_cap: null,
        min_sl_points: null,
        max_effective_account_leverage: null,
        max_effective_symbol_leverage: null,
        max_margin_usage_pct: null,
        min_margin_level_pct: null,
        min_stopout_buffer_multiplier: null,
      },
    });
    onNotify("ปิดเพดานคุมเสริมด้าน Risk ทั้งหมดแล้ว");
  };

  const clearAllPreventiveCaps = () => {
    setCfg({
      ...cfg,
      preventive: {
        ...cfg.preventive,
        max_spread_points: null,
        max_spread_to_tp_ratio: null,
        max_spread_to_atr_ratio: null,
        estimated_slippage_points: null,
        min_net_rr: null,
        max_swap_to_tp_ratio: null,
        max_daily_loss_pct: null,
        max_open_positions_symbol: null,
        max_open_positions_total: null,
        max_quote_age_seconds: null,
        enforce_rollover_blackout_for_intraday: false,
        enable_news_guard: false,
        enable_holiday_guard: false,
      },
    });
    onNotify("ปิดด่านกรองเสริมและข่าวทั้งหมดแล้ว");
  };

  const runLiveLotPreview = async () => {
    const slPts = Math.max(10, Math.round(Number(sampleSlPoints) || 300));
    const activeTpRr = Number(strategiesMeta?.active_params?.tp_rr_multiplier ?? 1.5);
    setSimLoading(true);
    try {
      const res = await api.simulate({
        symbol: cfg.symbol,
        side: "BUY",
        lot_mode: cfg.risk.lot_mode,
        fixed_lot: cfg.risk.fixed_lot,
        risk_percent: cfg.risk.risk_percent,
        equity_step_amount: cfg.risk.equity_step_amount,
        equity_step_lot: cfg.risk.equity_step_lot,
        scale_down_lot_for_leverage: cfg.risk.scale_down_lot_for_leverage,
        max_lot_cap: cfg.risk.max_lot_cap,
        max_effective_account_leverage: cfg.risk.max_effective_account_leverage,
        max_effective_symbol_leverage: cfg.risk.max_effective_symbol_leverage,
        max_margin_usage_pct: cfg.risk.max_margin_usage_pct,
        sl_points: slPts,
        rr_ratio: activeTpRr,
      });
      setSimResult(res);
      if (!res.ok) {
        onNotify(`ทดสอบจำลอง Lot ไม่สำเร็จ: ${res.error ?? "ไม่ทราบสาเหตุ"}`);
      }
    } catch (err) {
      onNotify(`ทดสอบจำลอง Lot ไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSimLoading(false);
    }
  };

  const liveEquity = overview?.account?.equity ?? strategiesMeta?.live_account_equity ?? null;
  const liveCurrency = overview?.account?.currency ?? strategiesMeta?.live_account_currency ?? "USD";
  const activeStepLotEstimate =
    liveEquity !== null && cfg.risk.equity_step_amount > 0
      ? Math.max(
          0.01,
          Math.floor(liveEquity / cfg.risk.equity_step_amount) * cfg.risk.equity_step_lot
        )
      : null;

  const activeCostGuardsCount = [
    cfg.preventive.max_spread_points,
    cfg.preventive.max_spread_to_tp_ratio,
    cfg.preventive.max_spread_to_atr_ratio,
    cfg.preventive.min_net_rr,
    cfg.preventive.max_swap_to_tp_ratio,
  ].filter((v) => v !== null && v > 0).length;

  const stepItems: Array<{ num: StepNumber; title: string; meta: string }> = [
    {
      num: 1,
      title: "โหมดรัน & สินค้า",
      meta: `${cfg.dry_run ? "โหมดซ้อม" : "เทรดจริง!"} • ${cfg.symbol} (${cfg.timeframe})`,
    },
    {
      num: 2,
      title: "เงินทุน & ขนาด Lot",
      meta:
        cfg.risk.lot_mode === "dynamic_risk"
          ? `เสี่ยง ${cfg.risk.risk_percent}%/ไม้`
          : cfg.risk.lot_mode === "dynamic_equity_step"
          ? `${cfg.risk.equity_step_lot} ต่อ ${cfg.risk.equity_step_amount} ${liveCurrency}`
          : `คงที่ ${cfg.risk.fixed_lot} Lot`,
    },
    {
      num: 3,
      title: "ต้นทุน & Spread",
      meta: `Net R:R ${cfg.preventive.min_net_rr !== null ? `>= ${cfg.preventive.min_net_rr}` : "ปิด"} • ${activeCostGuardsCount} ด่าน`,
    },
    {
      num: 4,
      title: "เบรกเกอร์ & ข่าว",
      meta: `ข่าว ${cfg.preventive.enable_news_guard ? "เปิด" : "ปิด"} • DD ${cfg.preventive.max_daily_loss_pct !== null ? `${cfg.preventive.max_daily_loss_pct}%` : "ปิด"}`,
    },
    {
      num: 5,
      title: "จัดการไม้ & สรุป",
      meta: `BE ${cfg.trade_management.enable_auto_breakeven ? `${cfg.trade_management.breakeven_trigger_r}R` : "ปิด"} • Trail ${cfg.trade_management.enable_trailing_stop ? `${cfg.trade_management.trailing_atr_mult}x` : "ปิด"}`,
    },
  ];

  const showStep = (step: StepNumber) => viewMode === "all" || activeStep === step;
  const goToStep = (next: StepNumber) => {
    setActiveStep(next);
    document.querySelector(".ops-scroll-main")?.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <form className="ops-container" onSubmit={handleSubmitForm} noValidate>
      <PageHeader
        title={viewMode === "all" ? "ตั้งค่าบอทครบทุกขั้นตอน" : "ตั้งค่าบอททีละขั้นตอน (Step-by-Step)"}
        subtitle="ปรับแต่งครบทุกจุดตั้งแต่โหมดซ้อม/จริง ขนาด Lot ต้นทุนสเปรด เบรกเกอร์ข่าว จนถึงการเลื่อน SL อัตโนมัติ พร้อมตรวจสอบความเข้ากันกับกลยุทธ์แบบเรียลไทม์"
        actions={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <div className="seg-group" role="group" aria-label="รูปแบบการแสดงหน้าตั้งค่า">
              <button
                type="button"
                className={`seg-btn ${viewMode === "wizard" ? "is-active" : ""}`}
                onClick={() => setViewMode("wizard")}
              >
                ทีละขั้น (Step-by-Step)
              </button>
              <button
                type="button"
                className={`seg-btn ${viewMode === "all" ? "is-active" : ""}`}
                onClick={() => setViewMode("all")}
              >
                <Layers size={13} /> ดูครบทุกขั้น
              </button>
            </div>
            <button
              type="button"
              className={`btn ${showAllHints ? "btn-primary" : ""}`}
              onClick={() => setShowAllHints((prev) => !prev)}
            >
              <BookOpen size={15} />
              {showAllHints ? "ซ่อนคำอธิบายทั้งหมด" : "เปิดคำอธิบายทั้งหมด"}
            </button>
            {isDirty && (
              <button type="button" className="btn" onClick={handleDiscardChanges}>
                <RotateCcw size={14} /> คืนค่าเดิม
              </button>
            )}
            <button
              className="btn btn-primary btn-save-stable"
              type="submit"
              aria-busy={saving}
            >
              <Save size={15} />
              {saving
                ? "กำลังบันทึก..."
                : justSaved
                ? "บันทึกเรียบร้อย"
                : isDirty
                ? "บันทึกการตั้งค่า (มีแก้ไข)"
                : "บันทึกการตั้งค่า"}
            </button>
          </div>
        }
      />

      {/* Live Account & Strategy Sync Status */}
      <div className="settings-context-bar">
        <div className="settings-context-pills">
          <StatusBadge tone={overview?.mt5_connected ? "success" : "warning"}>
            {overview?.mt5_connected ? "MT5 เชื่อมต่ออยู่" : "รอเชื่อมต่อ MT5"}
          </StatusBadge>
          {liveEquity !== null && (
            <StatusBadge tone="neutral">
              ทุนพอร์ตจริง: {liveEquity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}{" "}
              {liveCurrency} (1:{overview?.account?.leverage ?? "-"})
            </StatusBadge>
          )}
          <StatusBadge tone="info">
            กลยุทธ์ที่ใช้อยู่: {activeDescriptor?.name ?? strategiesMeta?.active_strategy ?? "M5KernelPullback"} (แนะนำ{" "}
            {activeDescriptor?.recommended_timeframe ?? "M5"})
          </StatusBadge>
          {isDirty && <StatusBadge tone="warning">มีการแก้ไขที่ยังไม่บันทึก</StatusBadge>}
        </div>

        {draftAudit.totalCount > 0 && (
          <button type="button" className="btn btn-sm btn-primary" onClick={applyStrategyAlignedDraft}>
            ปรับให้เข้ากับกลยุทธ์อัตโนมัติ
          </button>
        )}
      </div>

      {/* Starter Profiles (1-Click Draft Templates) */}
      <div className="preset-strip" style={{ marginBottom: 0 }}>
        <div className="preset-strip-head">
          <strong>เริ่มต้นไวด้วยโปรไฟล์สำเร็จรูป (โหลดลงแบบร่างก่อน ยังไม่บันทึกจนกว่าจะกดบันทึก):</strong>
        </div>
        <div className="preset-grid">
          <button type="button" className="preset-btn" onClick={() => applyQuickProfile("safe")}>
            <span className="preset-btn-title">1. ปลอดภัยมาตรฐาน (แนะนำมือใหม่)</span>
            <span className="preset-btn-meta">
              โหมดซ้อม • เสี่ยง 1%/ไม้ • กันข่าว + ขาดทุนวันละ 3% + Auto-BE/Trail
            </span>
          </button>
          <button type="button" className="preset-btn" onClick={() => applyQuickProfile("balanced_step")}>
            <span className="preset-btn-title">2. เติบโตตามขั้นพอร์ตสมดุล</span>
            <span className="preset-btn-meta">
              ทุก 300 USD ออก 0.05 Lot (เพดาน 0.10) • Net R:R 1.0 • BE 1.15R
            </span>
          </button>
          <button type="button" className="preset-btn" onClick={() => applyQuickProfile("open")}>
            <span className="preset-btn-title">3. เปิดโล่งลดด่านกรอง (เน้นสัญญาณล้วน)</span>
            <span className="preset-btn-meta">
              เสี่ยง 1%/ไม้ • ปิดด่าน Net R:R / สเปรด / ข่าว • เหลือแค่ Margin โบรคเกอร์
            </span>
          </button>
        </div>
      </div>

      {/* 5-Step Progress Navigation */}
      <div className="wizard-stepper" role="tablist" aria-label="ขั้นตอนการตั้งค่าบอท">
        {stepItems.map((st) => {
          const isActive = viewMode === "wizard" && activeStep === st.num;
          const isDone = viewMode === "wizard" && activeStep > st.num;
          const hasWarn = draftAudit.stepWarnings[st.num].length > 0;
          return (
            <button
              key={st.num}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`wizard-step-tab ${isActive ? "is-active" : ""} ${isDone ? "is-done" : ""} ${
                hasWarn ? "has-warn" : ""
              }`}
              onClick={() => {
                setViewMode("wizard");
                setActiveStep(st.num);
              }}
            >
              <div className="wizard-step-head">
                <span className="wizard-step-num">{isDone ? <Check size={12} /> : st.num}</span>
                {hasWarn && (
                  <AlertTriangle size={14} className="text-warning" aria-label="มีจุดควรตรวจทานในขั้นนี้" />
                )}
              </div>
              <div className="wizard-step-title">{st.title}</div>
              <div className="wizard-step-meta tabular">{st.meta}</div>
            </button>
          );
        })}
      </div>

      {/* ================================================================
          STEP 1: Mode, Symbol, Timeframe, Poll & Magic
      ================================================================ */}
      {showStep(1) && (
        <SectionCard
          title="ขั้นที่ 1/5 — โหมดรัน สินค้าหลัก และจังหวะอ่านกราฟ"
          subtitle="เลือกว่าจะให้บอทแค่ซ้อมจำลองหรือส่งออเดอร์เงินจริง จากนั้นเลือกสินค้าและกรอบเวลา"
          icon={<Sliders size={18} className="text-brand" />}
          headerRight={
            <StatusBadge tone={cfg.dry_run ? "success" : "danger"}>
              {cfg.dry_run ? "DRY-RUN (ซ้อมปลอดภัย)" : "LIVE (ส่งคำสั่งจริง!)"}
            </StatusBadge>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                1.1 เลือกโหมดการทำงาน
              </div>
              <div className="wizard-choice-grid-2">
                <button
                  type="button"
                  className={`wizard-choice-card ${cfg.dry_run ? "is-selected" : ""}`}
                  onClick={() => setCfg({ ...cfg, dry_run: true })}
                >
                  <div className="wizard-choice-title">
                    <span>โหมดซ้อม (DRY-RUN)</span>
                    <StatusBadge tone="success">แนะนำ</StatusBadge>
                  </div>
                  <p className="wizard-choice-desc">
                    บอทจะดึงราคาจริง คิดสัญญาณ คำนวณ Lot และตรวจด่านความเสี่ยงทุกขั้นตอนแล้วบันทึกลง Logs
                    โดยยังไม่ยิงออเดอร์จริงเข้าพอร์ต MT5
                  </p>
                </button>

                <button
                  type="button"
                  className={`wizard-choice-card is-danger ${!cfg.dry_run ? "is-selected" : ""}`}
                  onClick={() => setCfg({ ...cfg, dry_run: false })}
                >
                  <div className="wizard-choice-title">
                    <span>โหมดเทรดจริง (LIVE MONEY)</span>
                    <StatusBadge tone="danger">ส่งคำสั่งจริง</StatusBadge>
                  </div>
                  <p className="wizard-choice-desc">
                    เมื่อสัญญาณและด่านกรองผ่านครบ บอทจะเปิด-ปิด และเลื่อน SL/TP ในบัญชี MT5 ที่เชื่อมต่ออยู่ทันที
                    ควรทดสอบ Backtest และโหมดซ้อมให้มั่นใจก่อนเปิดใช้งาน
                  </p>
                </button>
              </div>
            </div>

            {draftAudit.stepWarnings[1].length > 0 && (
              <div className="alert-box alert-warning">
                <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  {draftAudit.stepWarnings[1].map((w, idx) => (
                    <div key={idx}>{w}</div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                1.2 สินค้า กรอบเวลา และความถี่เช็กกราฟ
              </div>
              <div className="form-grid">
                <FieldWithHint
                  label="สินค้าหลัก (Symbol)"
                  hint={HINTS.symbol}
                  forceOpen={showAllHints}
                  smallText="กดปุ่มลัดหรือพิมพ์ชื่อคู่เงิน/ทองที่ต้องการเทรด"
                >
                  <input
                    type="text"
                    value={cfg.symbol}
                    onChange={(e) => setCfg({ ...cfg, symbol: e.target.value })}
                  />
                  <div className="wizard-chip-row">
                    {QUICK_SYMBOLS.map((sym) => {
                      const active = cfg.symbol.toUpperCase().startsWith(sym);
                      return (
                        <button
                          key={sym}
                          type="button"
                          className={`btn btn-sm ${active ? "btn-primary" : ""}`}
                          onClick={() => setCfg({ ...cfg, symbol: sym })}
                        >
                          {sym}
                        </button>
                      );
                    })}
                  </div>
                </FieldWithHint>

                <FieldWithHint
                  label="กรอบเวลาวิเคราะห์กราฟ (Timeframe)"
                  hint={HINTS.timeframe}
                  forceOpen={showAllHints}
                  badgeRight={
                    activeDescriptor?.recommended_timeframe ? (
                      <span className="opt-status-pill">
                        กลยุทธ์แนะนำ {activeDescriptor.recommended_timeframe}
                      </span>
                    ) : undefined
                  }
                  smallText="ควรตั้งให้ตรงกับกลยุทธ์ที่เปิดใช้อยู่"
                >
                  <select
                    value={cfg.timeframe}
                    onChange={(e) => setCfg({ ...cfg, timeframe: e.target.value })}
                  >
                    {TIMEFRAMES.map((tf) => (
                      <option key={tf} value={tf}>
                        {tf}
                        {activeDescriptor?.recommended_timeframe?.toUpperCase() === tf
                          ? " (แนะนำสำหรับกลยุทธ์นี้)"
                          : ""}
                      </option>
                    ))}
                  </select>
                  <div className="wizard-chip-row">
                    {["M1", "M5", "M15", "H1", "H4"].map((tf) => (
                      <button
                        key={tf}
                        type="button"
                        className={`btn btn-sm ${cfg.timeframe.toUpperCase() === tf ? "btn-primary" : ""}`}
                        onClick={() => setCfg({ ...cfg, timeframe: tf })}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>
                </FieldWithHint>

                <FieldWithHint
                  label="ความถี่ในการเช็กตลาด (วินาที)"
                  hint={HINTS.poll_seconds}
                  forceOpen={showAllHints}
                  smallText="แนะนำ 5 วินาทีสำหรับ M5 หรือ 15 วินาทีสำหรับ H1"
                >
                  <input
                    type="number"
                    min="0.5"
                    step="any"
                    value={cfg.poll_seconds}
                    onChange={(e) =>
                      setCfg({ ...cfg, poll_seconds: Math.max(0.5, Number(e.target.value) || 5) })
                    }
                  />
                  <div className="wizard-chip-row">
                    {QUICK_POLLS.map((q) => (
                      <button
                        key={q.val}
                        type="button"
                        className={`btn btn-sm ${Number(cfg.poll_seconds) === q.val ? "btn-primary" : ""}`}
                        onClick={() => setCfg({ ...cfg, poll_seconds: q.val })}
                      >
                        {q.label}
                      </button>
                    ))}
                  </div>
                </FieldWithHint>

                <FieldWithHint
                  label="เลขรหัสประจำตัวบอท (Magic Number)"
                  hint={HINTS.magic_number}
                  forceOpen={showAllHints}
                  smallText="แยกไม้ออเดอร์บอทออกจากการกดมือใน MT5"
                >
                  <input
                    type="number"
                    step="any"
                    value={cfg.magic_number}
                    onChange={(e) =>
                      setCfg({ ...cfg, magic_number: Math.round(Number(e.target.value) || 202611) })
                    }
                  />
                </FieldWithHint>
              </div>
            </div>

            <div>
              <button type="button" className="btn btn-sm" onClick={() => setShowAdvancedMain((s) => !s)}>
                ตั้งค่าส่งคำสั่งเพิ่มเติม (Deviation){" "}
                {showAdvancedMain ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            </div>

            {showAdvancedMain && (
              <div className="form-grid">
                <FieldWithHint
                  label="Deviation ยอมรับราคาคลาดเคลื่อน (จุด)"
                  hint={HINTS.deviation_points}
                  forceOpen={showAllHints}
                  smallText="ระยะราคาไถลสูงสุดที่ยอมให้จับคู่คำสั่งเข้า MT5"
                >
                  <input
                    type="number"
                    min="1"
                    step="any"
                    value={cfg.deviation_points}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        deviation_points: Math.max(1, Math.round(Number(e.target.value) || 20)),
                      })
                    }
                  />
                </FieldWithHint>
              </div>
            )}
          </div>
        </SectionCard>
      )}

      {/* ================================================================
          STEP 2: Lot Sizing, Caps & Live Lot Preview
      ================================================================ */}
      {showStep(2) && (
        <SectionCard
          title="ขั้นที่ 2/5 — วิธีคำนวณขนาด Lot และเพดานมัดจำ"
          subtitle="เลือกสูตรออกขนาดไม้ให้ตรงสไตล์บริหารทุน และกดทดลองคำนวณกับพอร์ต MT5 จริงได้ทันที"
          icon={<Gauge size={18} className="text-brand" />}
          headerRight={
            <button type="button" className="btn btn-sm" onClick={clearAllRiskCaps}>
              ไม่ใช้เพดานคุมเสริมทั้งหมด
            </button>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                2.1 เลือกสูตรกำหนดขนาด Lot ต่อ 1 ไม้
              </div>
              <div className="wizard-choice-grid-3">
                <button
                  type="button"
                  className={`wizard-choice-card ${cfg.risk.lot_mode === "dynamic_risk" ? "is-selected" : ""}`}
                  onClick={() => setCfg({ ...cfg, risk: { ...cfg.risk, lot_mode: "dynamic_risk" } })}
                >
                  <div className="wizard-choice-title">
                    <span>เสี่ยง % ต่อไม้</span>
                    <StatusBadge tone="success">แนะนำ</StatusBadge>
                  </div>
                  <p className="wizard-choice-desc">
                    คำนวณ Lot ตามระยะ SL จริง ถ้า SL กว้างระบบจะย่อ Lot ลงให้โดยอัตโนมัติเพื่อคุมไม่ให้เสียเกินงบที่ตั้งไว้
                  </p>
                </button>

                <button
                  type="button"
                  className={`wizard-choice-card ${
                    cfg.risk.lot_mode === "dynamic_equity_step" ? "is-selected" : ""
                  }`}
                  onClick={() =>
                    setCfg({ ...cfg, risk: { ...cfg.risk, lot_mode: "dynamic_equity_step" } })
                  }
                >
                  <div className="wizard-choice-title">
                    <span>เพิ่ม Lot ตามขั้นพอร์ต</span>
                    <StatusBadge tone="info">ขั้นบันได</StatusBadge>
                  </div>
                  <p className="wizard-choice-desc">
                    เพิ่มขนาด Lot ทีละขั้นตามจำนวนเงินทุน (เช่น ทุก 300 {liveCurrency} ออก 0.05 Lot) เข้าใจง่ายและทบต้นไว
                  </p>
                </button>

                <button
                  type="button"
                  className={`wizard-choice-card ${cfg.risk.lot_mode === "fixed" ? "is-selected" : ""}`}
                  onClick={() => setCfg({ ...cfg, risk: { ...cfg.risk, lot_mode: "fixed" } })}
                >
                  <div className="wizard-choice-title">
                    <span>ขนาด Lot คงที่</span>
                    <StatusBadge tone="neutral">Manual</StatusBadge>
                  </div>
                  <p className="wizard-choice-desc">
                    สั่งขนาด Lot เท่าเดิมทุกครั้งไม่ว่า SL จะกว้างหรือแคบ หรือพอร์ตจะโตขึ้น/ลดลงเท่าไรก็ตาม
                  </p>
                </button>
              </div>
            </div>

            <div className="form-grid">
              {cfg.risk.lot_mode === "dynamic_risk" && (
                <FieldWithHint
                  label="ยอมขาดทุนสูงสุดต่อ 1 ไม้ (%)"
                  hint={HINTS.risk_percent}
                  forceOpen={showAllHints}
                  smallText={
                    liveEquity !== null
                      ? `จากทุน ${liveEquity.toFixed(2)} ${liveCurrency} = งบเสี่ยงราว ${(
                          (liveEquity * cfg.risk.risk_percent) /
                          100
                        ).toFixed(2)} ${liveCurrency}/ไม้`
                      : "กำหนด % ของพอร์ตที่ยอมเสียเมื่อชน SL (แนะนำ 0.5 – 2.0%)"
                  }
                >
                  <input
                    type="number"
                    step="any"
                    min="0.01"
                    max="100"
                    value={cfg.risk.risk_percent}
                    onChange={(e) =>
                      setCfg({ ...cfg, risk: { ...cfg.risk, risk_percent: Number(e.target.value) } })
                    }
                  />
                </FieldWithHint>
              )}

              {cfg.risk.lot_mode === "fixed" && (
                <FieldWithHint
                  label="ขนาด Lot คงที่"
                  hint={HINTS.fixed_lot}
                  forceOpen={showAllHints}
                  smallText="สั่งขนาดเท่านี้ทุกไม้ (0.01 = ไมโครล็อตเล็กสุด)"
                >
                  <input
                    type="number"
                    step="any"
                    min="0.01"
                    value={cfg.risk.fixed_lot}
                    onChange={(e) =>
                      setCfg({ ...cfg, risk: { ...cfg.risk, fixed_lot: Number(e.target.value) } })
                    }
                  />
                </FieldWithHint>
              )}

              {cfg.risk.lot_mode === "dynamic_equity_step" && (
                <>
                  <FieldWithHint
                    label={`ทุกๆ เงินทุน (${liveCurrency})`}
                    hint={HINTS.equity_step_amount}
                    forceOpen={showAllHints}
                    smallText="ขนาดขั้นเงินทุนสำหรับคิดเพิ่ม Lot (ปัดลงเสมอ)"
                  >
                    <input
                      type="number"
                      step="any"
                      min="1"
                      value={cfg.risk.equity_step_amount}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          risk: { ...cfg.risk, equity_step_amount: Number(e.target.value) },
                        })
                      }
                    />
                  </FieldWithHint>
                  <FieldWithHint
                    label="เพิ่มขนาด (Lot) ต่อขั้น"
                    hint={HINTS.equity_step_lot}
                    forceOpen={showAllHints}
                    smallText={
                      activeStepLotEstimate !== null
                        ? `ทุนปัจจุบันจะได้ ~${activeStepLotEstimate.toFixed(2)} Lot (ก่อนตรวจเพดานด้านล่าง)`
                        : "จำนวน Lot ที่เพิ่มให้ต่อ 1 ขั้นเงินทุน"
                    }
                  >
                    <input
                      type="number"
                      step="any"
                      min="0.01"
                      value={cfg.risk.equity_step_lot}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          risk: { ...cfg.risk, equity_step_lot: Number(e.target.value) },
                        })
                      }
                    />
                  </FieldWithHint>
                </>
              )}
            </div>

            {/* Live Simulator Test Box */}
            <div className="wizard-preview-box">
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Calculator size={17} className="text-brand" />
                  <strong>ทดลองคำนวณ Lot จริงบนพอร์ต MT5 จากค่าที่กำลังตั้งอยู่นี้ (ยังไม่ทับค่าเดิม)</strong>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <label style={{ fontSize: 12.5 }}>
                    สมมติระยะ SL (จุด):{" "}
                    <input
                      type="number"
                      className="input-inline"
                      style={{ width: 92, padding: "5px 8px" }}
                      min="10"
                      value={sampleSlPoints}
                      onChange={(e) => setSampleSlPoints(e.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={simLoading}
                    onClick={() => void runLiveLotPreview()}
                  >
                    {simLoading ? "กำลังคำนวณ..." : "คำนวณทดลองทันที"}
                  </button>
                </div>
              </div>

              {simResult && simResult.ok && simResult.lot_plan && (
                <div className="wizard-preview-metrics tabular">
                  <div className="wizard-preview-stat">
                    <span>ขนาด Lot ที่ได้จริง</span>
                    <strong>
                      {simResult.lot_plan.approved
                        ? `${simResult.lot_plan.lot.toFixed(2)} Lot`
                        : "ไม่ผ่านเกณฑ์"}
                    </strong>
                  </div>
                  <div className="wizard-preview-stat">
                    <span>ขาดทุนถ้าชน SL (รวมต้นทุน)</span>
                    <strong>
                      {simResult.lot_plan.estimated_loss_at_sl.toFixed(2)} {liveCurrency}
                    </strong>
                  </div>
                  <div className="wizard-preview-stat">
                    <span>Margin ที่ต้องวาง</span>
                    <strong>
                      {simResult.lot_plan.leverage_snapshot.projected_margin_used.toFixed(2)} {liveCurrency}
                    </strong>
                  </div>
                  <div className="wizard-preview-stat">
                    <span>Net R:R หลังหักต้นทุน</span>
                    <strong>
                      {simResult.preventive ? simResult.preventive.costs.net_rr.toFixed(2) : "-"} R:R
                    </strong>
                  </div>
                </div>
              )}

              {simResult && simResult.ok && simResult.lot_plan && simResult.lot_plan.clamp_reasons.length > 0 && (
                <div style={{ fontSize: 12.5 }} className="text-muted">
                  <strong>หมายเหตุระบบย่อ Lot:</strong> {simResult.lot_plan.clamp_reasons.join(" • ")}
                </div>
              )}
              {simResult && simResult.ok && simResult.lot_plan && !simResult.lot_plan.approved && (
                <div style={{ fontSize: 12.5 }} className="text-danger">
                  <strong>เหตุผลที่ไม่อนุมัติ Lot:</strong> {simResult.lot_plan.rejection_reasons.join(" • ")}
                </div>
              )}
            </div>

            {draftAudit.stepWarnings[2].length > 0 && (
              <div className="alert-box alert-info">
                <Info size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  {draftAudit.stepWarnings[2].map((w, idx) => (
                    <div key={idx}>{w}</div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                2.2 เพดานคุมขนาด Lot และ Margin (เว้นว่าง = ไม่จำกัดข้อนั้น)
              </div>
              <div className="form-grid">
                <OptionalNumberField
                  label="ขนาด Lot สูงสุดไม่เกิน (Max Lot)"
                  hint={HINTS.max_lot_cap}
                  value={cfg.risk.max_lot_cap}
                  onChange={(val) => setCfg({ ...cfg, risk: { ...cfg.risk, max_lot_cap: val } })}
                  forceOpen={showAllHints}
                  smallText="เพดานบนสุดของ Lot ต่อ 1 ไม้"
                />

                <OptionalNumberField
                  label="จำกัดแรงดึงเงินรวมทั้งพอร์ต (เท่า)"
                  hint={HINTS.max_effective_account_leverage}
                  value={cfg.risk.max_effective_account_leverage}
                  onChange={(val) =>
                    setCfg({ ...cfg, risk: { ...cfg.risk, max_effective_account_leverage: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="มูลค่ารวมทุกไม้ไม่เกินกี่เท่าของเงินทุน"
                />

                <OptionalNumberField
                  label="จำกัดแรงดึงเงินต่อสินค้า 1 ตัว (เท่า)"
                  hint={HINTS.max_effective_symbol_leverage}
                  value={cfg.risk.max_effective_symbol_leverage}
                  onChange={(val) =>
                    setCfg({ ...cfg, risk: { ...cfg.risk, max_effective_symbol_leverage: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="มูลค่ารวมเฉพาะสินค้านี้ไม่เกินกี่เท่าของเงินทุน"
                />

                <OptionalNumberField
                  label="ใช้เงินมัดจำ (Margin) รวมได้ไม่เกิน (%)"
                  hint={HINTS.max_margin_usage_pct}
                  value={cfg.risk.max_margin_usage_pct}
                  onChange={(val) =>
                    setCfg({ ...cfg, risk: { ...cfg.risk, max_margin_usage_pct: val } })
                  }
                  forceOpen={showAllHints}
                  max={100}
                  smallText="กันเงินวางประกันรวมไม่ให้เกิน % ของพอร์ต"
                />

                <OptionalNumberField
                  label="ระดับ Margin ขั้นต่ำหลังเปิดไม้ (%)"
                  hint={HINTS.min_margin_level_pct}
                  value={cfg.risk.min_margin_level_pct}
                  onChange={(val) =>
                    setCfg({ ...cfg, risk: { ...cfg.risk, min_margin_level_pct: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="หลังเปิดไม้ Margin Level ต้องยังสูงกว่าค่านี้"
                />
              </div>
            </div>

            <div>
              <button type="button" className="btn btn-sm" onClick={() => setShowAdvancedRisk((s) => !s)}>
                ตั้งค่า Risk เพิ่มเติม (ฐานคำนวณ / กันชน Stop Out / SL ขั้นต่ำ){" "}
                {showAdvancedRisk ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            </div>

            {showAdvancedRisk && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="form-grid">
                  <OptionalNumberField
                    label="ระยะ SL ขั้นต่ำที่ยอมรับได้ (จุด)"
                    hint={HINTS.min_sl_points}
                    value={cfg.risk.min_sl_points}
                    integerOnly
                    onChange={(val) => setCfg({ ...cfg, risk: { ...cfg.risk, min_sl_points: val } })}
                    forceOpen={showAllHints}
                    smallText="กันสูตร Lot พุ่งสูงเมื่อ SL แคบมาก"
                  />

                  <OptionalNumberField
                    label="ตัวคูณกันชนก่อน Stop Out (เท่า)"
                    hint={HINTS.min_stopout_buffer_multiplier}
                    value={cfg.risk.min_stopout_buffer_multiplier}
                    onChange={(val) =>
                      setCfg({ ...cfg, risk: { ...cfg.risk, min_stopout_buffer_multiplier: val } })
                    }
                    forceOpen={showAllHints}
                    smallText="คูณจากระดับ Stop Out ของโบรคเกอร์"
                  />
                </div>

                <div className="grid-2" style={{ gap: 12 }}>
                  <label className="checkbox-tile">
                    <input
                      type="checkbox"
                      checked={cfg.risk.use_equity_for_risk}
                      onChange={(e) =>
                        setCfg({ ...cfg, risk: { ...cfg.risk, use_equity_for_risk: e.target.checked } })
                      }
                    />
                    <span>ใช้ Equity ปัจจุบัน (รวมกำไร/ขาดทุนลอยตัว) คำนวณงบแทน Balance</span>
                  </label>
                  <label className="checkbox-tile">
                    <input
                      type="checkbox"
                      checked={cfg.risk.include_costs_in_risk_sizing}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          risk: { ...cfg.risk, include_costs_in_risk_sizing: e.target.checked },
                        })
                      }
                    />
                    <span>รวมค่าคอม/Slippage เข้าไปในงบคำนวณ Lot (เพื่อไม่ให้ชน SL แล้วขาดทุนเกิน %)</span>
                  </label>
                </div>
              </div>
            )}

            <label className="checkbox-tile">
              <input
                type="checkbox"
                checked={cfg.risk.scale_down_lot_for_leverage}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    risk: { ...cfg.risk, scale_down_lot_for_leverage: e.target.checked },
                  })
                }
              />
              <span>
                <strong>ย่อขนาด Lot อัตโนมัติเมื่อติดเพดานด้านบน</strong> — หากคำนวณได้ใหญ่กว่าเพดานหรือโบรคเกอร์ลดเลเวอเรจกะทันหัน บอทจะย่อ Lot ให้พอดีแทนการยกเลิกออเดอร์
              </span>
            </label>
          </div>
        </SectionCard>
      )}

      {/* ================================================================
          STEP 3: Transaction Costs, Spread, Net R:R & Rollover Swap
      ================================================================ */}
      {showStep(3) && (
        <SectionCard
          title="ขั้นที่ 3/5 — ต้นทุนค่าธรรมเนียม Spread, Net R:R และช่วง Rollover"
          subtitle="ป้องกันการเข้าไม้ตอนสเปรดถ่างผิดปกติหรือค่าธรรมเนียมกินกำไรจนไม่คุ้มเสี่ยง"
          icon={<Coins size={18} className="text-brand" />}
          headerRight={
            <button type="button" className="btn btn-sm" onClick={clearAllPreventiveCaps}>
              ปิดด่านกรองเสริมทั้งหมด
            </button>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {draftAudit.stepWarnings[3].length > 0 && (
              <div className="alert-box alert-warning">
                <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  {draftAudit.stepWarnings[3].map((w, idx) => (
                    <div key={idx}>{w}</div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                3.1 เกณฑ์กรอง Spread และความคุ้มค่าสุทธิ (Net R:R)
              </div>
              <div className="form-grid">
                <OptionalNumberField
                  label="Spread สูงสุดที่รับได้ (จุด)"
                  hint={HINTS.max_spread_points}
                  value={cfg.preventive.max_spread_points}
                  onChange={(val) =>
                    setCfg({ ...cfg, preventive: { ...cfg.preventive, max_spread_points: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="บล็อกเมื่อ Spread กว้างเกินจุดนี้"
                />

                <OptionalNumberField
                  label="Spread ต้องไม่เกินสัดส่วนของ TP"
                  hint={HINTS.max_spread_to_tp_ratio}
                  value={cfg.preventive.max_spread_to_tp_ratio}
                  max={1}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, max_spread_to_tp_ratio: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="เช่น 0.25 = Spread ไม่เกิน 25% ของระยะกำไร"
                />

                <OptionalNumberField
                  label="Spread ต้องไม่เกินสัดส่วนของ ATR"
                  hint={HINTS.max_spread_to_atr_ratio}
                  value={cfg.preventive.max_spread_to_atr_ratio}
                  max={1}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, max_spread_to_atr_ratio: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="กันช่วง Spread ดีดสูงเทียบความผันผวน"
                />

                <OptionalNumberField
                  label="ความคุ้มค่าสุทธิขั้นต่ำ (Net R:R)"
                  hint={HINTS.min_net_rr}
                  value={cfg.preventive.min_net_rr}
                  onChange={(val) =>
                    setCfg({ ...cfg, preventive: { ...cfg.preventive, min_net_rr: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="กำไร ÷ ความเสี่ยง หลังหักต้นทุนทุกอย่างแล้ว"
                />
              </div>
            </div>

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                3.2 ค่าคอมมิชชันและ Slippage สำรอง
              </div>
              <div className="form-grid">
                <OptionalNumberField
                  label="ค่าคอมมิชชันไป-กลับ (USD ต่อ 1 Lot)"
                  hint={HINTS.commission_per_lot_roundtrip}
                  value={cfg.preventive.commission_per_lot_roundtrip}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, commission_per_lot_roundtrip: val },
                    })
                  }
                  forceOpen={showAllHints}
                  offText="ไม่มีค่าคอม (0 USD)"
                  smallText="ใช้เมื่อโบรคเกอร์ไม่ได้ส่งประวัติคอมจริง"
                />

                <OptionalNumberField
                  label="เผื่อ Slippage ตอนคำนวณ (จุด)"
                  hint={HINTS.estimated_slippage_points}
                  value={cfg.preventive.estimated_slippage_points}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, estimated_slippage_points: val },
                    })
                  }
                  forceOpen={showAllHints}
                  offText="ไม่เผื่อ (0)"
                  smallText="ต้นทุนเผื่อราคาไถลเพิ่มเติม"
                />
              </div>

              <div style={{ marginTop: 12 }}>
                <label className="checkbox-tile">
                  <input
                    type="checkbox"
                    checked={cfg.preventive.auto_detect_commission}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, auto_detect_commission: e.target.checked },
                      })
                    }
                  />
                  <span>ดึงค่าคอมมิชชันจริงจากประวัติปิดไม้ใน MT5 อัตโนมัติ (แนะนำ)</span>
                </label>
              </div>
            </div>

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                3.3 ช่วงเปลี่ยนวัน Rollover และค่า Swap
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <label className="checkbox-tile">
                  <input
                    type="checkbox"
                    checked={cfg.preventive.enforce_rollover_blackout_for_intraday}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: {
                          ...cfg.preventive,
                          enforce_rollover_blackout_for_intraday: e.target.checked,
                        },
                      })
                    }
                  />
                  <span>
                    <strong>งดเปิดไม้ใหม่ช่วง Rollover เปลี่ยนวันของตลาด</strong> — ป้องกันช่วงสภาพคล่องต่ำและสเปรดถ่างตอนเปลี่ยนวัน
                  </span>
                </label>

                <div className="form-grid">
                  <FieldWithHint
                    label="ชั่วโมงเปลี่ยนวันของเซิร์ฟเวอร์ (0–23)"
                    hint={HINTS.rollover_blackout}
                    forceOpen={showAllHints}
                    smallText="เวลา Server MT5 ที่โบรคเกอร์คิด Swap (ส่วนใหญ่คือ 0)"
                  >
                    <input
                      type="number"
                      min="0"
                      max="23"
                      value={cfg.preventive.swap_rollover_hour_server}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          preventive: {
                            ...cfg.preventive,
                            swap_rollover_hour_server: Math.min(23, Math.max(0, Math.round(Number(e.target.value) || 0))),
                          },
                        })
                      }
                    />
                  </FieldWithHint>

                  <OptionalNumberField
                    label="งดก่อนเปลี่ยนวัน (นาที)"
                    hint={HINTS.rollover_blackout}
                    value={cfg.preventive.rollover_blackout_minutes_before}
                    integerOnly
                    max={120}
                    onChange={(val) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, rollover_blackout_minutes_before: val },
                      })
                    }
                    forceOpen={showAllHints}
                    smallText="งดเข้าก่อนถึงเวลา Rollover"
                  />

                  <OptionalNumberField
                    label="งดหลังเปลี่ยนวัน (นาที)"
                    hint={HINTS.rollover_blackout}
                    value={cfg.preventive.rollover_blackout_minutes_after}
                    integerOnly
                    max={120}
                    onChange={(val) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, rollover_blackout_minutes_after: val },
                      })
                    }
                    forceOpen={showAllHints}
                    smallText="รอหลังเปลี่ยนวันให้ Spread กลับมาปกติ"
                  />

                  <OptionalNumberField
                    label="ค่า Swap ต้องไม่เกินสัดส่วนของ TP"
                    hint={HINTS.max_swap_to_tp_ratio}
                    value={cfg.preventive.max_swap_to_tp_ratio}
                    max={1}
                    onChange={(val) =>
                      setCfg({ ...cfg, preventive: { ...cfg.preventive, max_swap_to_tp_ratio: val } })
                    }
                    forceOpen={showAllHints}
                    smallText="กันดอกเบี้ยข้ามคืนกินกำไรเยอะเกิน"
                  />
                </div>
              </div>
            </div>

            <div>
              <button type="button" className="btn btn-sm" onClick={() => setShowAdvancedCost((s) => !s)}>
                ตั้งค่าอัตราแปลงสกุลเงิน & ปฏิทิน Swap เพิ่มเติม{" "}
                {showAdvancedCost ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            </div>

            {showAdvancedCost && (
              <div className="form-grid">
                <div className="field">
                  <label>จำนวนวันต่อปีในการคิด Swap</label>
                  <select
                    value={cfg.preventive.swap_year_days}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, swap_year_days: Number(e.target.value) || 360 },
                      })
                    }
                  >
                    <option value={360}>360 วัน (มาตรฐาน Forex/CFD)</option>
                    <option value={365}>365 วัน</option>
                    <option value={366}>366 วัน</option>
                  </select>
                </div>
                <div className="field">
                  <label>อัตราแปลงสกุลเงินสินค้าเป็นเงินพอร์ต</label>
                  <input
                    type="number"
                    step="any"
                    min="0.0001"
                    value={cfg.preventive.symbol_currency_to_account_rate ?? 1.0}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: {
                          ...cfg.preventive,
                          symbol_currency_to_account_rate: Math.max(0.0001, Number(e.target.value) || 1),
                        },
                      })
                    }
                  />
                  <small>พอร์ต USD เทรด XAUUSD/EURUSD ใช้ 1.0</small>
                </div>
                <div className="field">
                  <label>อัตราแปลงสกุลเงิน Margin เป็นเงินพอร์ต</label>
                  <input
                    type="number"
                    step="any"
                    min="0.0001"
                    value={cfg.preventive.margin_currency_to_account_rate ?? 1.0}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: {
                          ...cfg.preventive,
                          margin_currency_to_account_rate: Math.max(0.0001, Number(e.target.value) || 1),
                        },
                      })
                    }
                  />
                  <small>พอร์ต USD เทรดทอง/คู่ USD ใช้ 1.0</small>
                </div>
              </div>
            )}
          </div>
        </SectionCard>
      )}

      {/* ================================================================
          STEP 4: Circuit Breakers, Open Limits, News & Holidays
      ================================================================ */}
      {showStep(4) && (
        <SectionCard
          title="ขั้นที่ 4/5 — เบรกเกอร์คุมพอร์ต จำนวนไม้ และตัวกรองข่าวเศรษฐกิจ"
          subtitle="จำกัดจำนวนออเดอร์พร้อมกัน พักพอร์ตเมื่อขาดทุนถึงเป้า และหลบช่วงข่าวผันผวนแรง"
          icon={<ShieldCheck size={18} className="text-brand" />}
          headerRight={
            <button type="button" className="btn btn-sm" onClick={clearAllPreventiveCaps}>
              ปิดด่านกรองเสริมทั้งหมด
            </button>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                4.1 เบรกเกอร์หยุดพักรายวัน และจำนวนออเดอร์สูงสุด
              </div>
              <div className="form-grid">
                <OptionalNumberField
                  label="ยอมขาดทุนสูงสุดต่อวัน (%)"
                  hint={HINTS.max_daily_loss_pct}
                  value={cfg.preventive.max_daily_loss_pct}
                  max={100}
                  onChange={(val) =>
                    setCfg({ ...cfg, preventive: { ...cfg.preventive, max_daily_loss_pct: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="ขาดทุนสะสมถึงเป้านี้ บอทจะหยุดพักทั้งวัน"
                />

                <OptionalNumberField
                  label="จำนวนไม้พร้อมกันต่อ 1 สินค้า"
                  hint={HINTS.max_open_positions_symbol}
                  value={cfg.preventive.max_open_positions_symbol}
                  integerOnly
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, max_open_positions_symbol: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="เช่น 1 = เปิดได้ทีละ 1 ไม้ต่อคู่"
                />

                <OptionalNumberField
                  label="จำนวนไม้พร้อมกันรวมทั้งพอร์ต"
                  hint={HINTS.max_open_positions_total}
                  value={cfg.preventive.max_open_positions_total}
                  integerOnly
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, max_open_positions_total: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="จำนวนออเดอร์สูงสุดรวมทุกสินค้า"
                />

                <OptionalNumberField
                  label="อายุราคาล่าสุดไม่เกิน (วินาที)"
                  hint={HINTS.max_quote_age_seconds}
                  value={cfg.preventive.max_quote_age_seconds}
                  integerOnly
                  onChange={(val) =>
                    setCfg({ ...cfg, preventive: { ...cfg.preventive, max_quote_age_seconds: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="เช็กราคาค้างตอนตลาดปิด"
                />
              </div>
            </div>

            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                4.2 ตัวกรองข่าวเศรษฐกิจ (News Guard) และวันหยุดธนาคาร
              </div>
              <div className="grid-2" style={{ gap: 12, marginBottom: 14 }}>
                <label className="checkbox-tile">
                  <input
                    type="checkbox"
                    checked={cfg.preventive.enable_news_guard}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, enable_news_guard: e.target.checked },
                      })
                    }
                  />
                  <span>
                    <strong>เปิดระบบหลบช่วงข่าวเศรษฐกิจแรง (สีแดง / HIGH Impact)</strong>
                  </span>
                </label>

                <label className="checkbox-tile">
                  <input
                    type="checkbox"
                    checked={cfg.preventive.enable_holiday_guard}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, enable_holiday_guard: e.target.checked },
                      })
                    }
                  />
                  <span>
                    <strong>เปิดระบบตรวจวันหยุดธนาคารเจ้าของสกุลเงิน</strong>
                  </span>
                </label>
              </div>

              <div className="form-grid">
                <OptionalNumberField
                  label="งดเทรดก่อนข่าวแรง (นาที)"
                  hint={HINTS.news_block_minutes_before}
                  value={cfg.preventive.news_block_minutes_before}
                  integerOnly
                  max={240}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, news_block_minutes_before: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="หยุดเข้าไม้ล่วงหน้าก่อนประกาศข่าวสีแดง"
                />

                <OptionalNumberField
                  label="งดเทรดหลังข่าวแรง (นาที)"
                  hint={HINTS.news_block_minutes_after}
                  value={cfg.preventive.news_block_minutes_after}
                  integerOnly
                  max={240}
                  onChange={(val) =>
                    setCfg({
                      ...cfg,
                      preventive: { ...cfg.preventive, news_block_minutes_after: val },
                    })
                  }
                  forceOpen={showAllHints}
                  smallText="รอให้กราฟหลังข่าวสงบก่อนกลับมาเทรด"
                />

                <FieldWithHint
                  label="วิธีจัดการเมื่อตรงกับวันหยุดธนาคาร"
                  hint={HINTS.holiday_policy}
                  forceOpen={showAllHints}
                  smallText="เลือกว่าวันหยุดใหญ่จะให้บล็อกหรือเตือน"
                >
                  <select
                    value={cfg.preventive.holiday_policy}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        preventive: { ...cfg.preventive, holiday_policy: e.target.value },
                      })
                    }
                  >
                    <option value="block">งดเปิดไม้ใหม่อัตโนมัติ (แนะนำ)</option>
                    <option value="warn">แจ้งเตือนอย่างเดียว</option>
                  </select>
                </FieldWithHint>

                <OptionalNumberField
                  label="เตือนข่าวแรงล่วงหน้า (ชั่วโมง)"
                  hint={HINTS.news_warn_hours}
                  value={cfg.preventive.news_warn_hours}
                  integerOnly
                  max={72}
                  onChange={(val) =>
                    setCfg({ ...cfg, preventive: { ...cfg.preventive, news_warn_hours: val } })
                  }
                  forceOpen={showAllHints}
                  smallText="โชว์แจ้งเตือนข่าวที่กำลังจะมาถึง"
                />
              </div>
            </div>

            <div>
              <button type="button" className="btn btn-sm" onClick={() => setShowAdvancedPrev((s) => !s)}>
                ตัวเลือกข่าวระดับกลาง (สีส้ม) วันเสาร์-อาทิตย์ และโหมดเข้มงวด{" "}
                {showAdvancedPrev ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            </div>

            {showAdvancedPrev && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="grid-2" style={{ gap: 12 }}>
                  <label className="checkbox-tile">
                    <input
                      type="checkbox"
                      checked={cfg.preventive.block_medium_impact_news}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          preventive: { ...cfg.preventive, block_medium_impact_news: e.target.checked },
                        })
                      }
                    />
                    <span>งดเทรดช่วงข่าวความแรงปานกลาง (สีส้ม / MEDIUM Impact) ด้วย</span>
                  </label>
                  <label className="checkbox-tile">
                    <input
                      type="checkbox"
                      checked={cfg.preventive.allow_weekend_trading}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          preventive: { ...cfg.preventive, allow_weekend_trading: e.target.checked },
                        })
                      }
                    />
                    <span>อนุญาตให้เทรดช่วงเสาร์-อาทิตย์ได้ (สำหรับบัญชีคริปโต 24/7)</span>
                  </label>
                  <label className="checkbox-tile">
                    <input
                      type="checkbox"
                      checked={cfg.preventive.fail_closed_on_calendar_error}
                      onChange={(e) =>
                        setCfg({
                          ...cfg,
                          preventive: {
                            ...cfg.preventive,
                            fail_closed_on_calendar_error: e.target.checked,
                          },
                        })
                      }
                    />
                    <span>หยุดเปิดไม้ใหม่ทันทีหากดึงปฏิทินข่าวไม่สำเร็จ (Fail-closed)</span>
                  </label>
                </div>

                {cfg.preventive.block_medium_impact_news && (
                  <div className="form-grid">
                    <OptionalNumberField
                      label="งดก่อนข่าวสีส้ม (นาที)"
                      hint={HINTS.news_block_minutes_before}
                      value={cfg.preventive.news_medium_block_minutes_before}
                      integerOnly
                      max={120}
                      onChange={(val) =>
                        setCfg({
                          ...cfg,
                          preventive: { ...cfg.preventive, news_medium_block_minutes_before: val },
                        })
                      }
                      forceOpen={showAllHints}
                    />
                    <OptionalNumberField
                      label="งดหลังข่าวสีส้ม (นาที)"
                      hint={HINTS.news_block_minutes_after}
                      value={cfg.preventive.news_medium_block_minutes_after}
                      integerOnly
                      max={120}
                      onChange={(val) =>
                        setCfg({
                          ...cfg,
                          preventive: { ...cfg.preventive, news_medium_block_minutes_after: val },
                        })
                      }
                      forceOpen={showAllHints}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        </SectionCard>
      )}

      {/* ================================================================
          STEP 5: Trade Management & Complete Pre-Save Review
      ================================================================ */}
      {showStep(5) && (
        <SectionCard
          title="ขั้นที่ 5/5 — จัดการออเดอร์หลังเปิดไม้ และตรวจสรุปก่อนบันทึก"
          subtitle="ตั้งจุดเลื่อน SL กันหน้าทุนกับ Trailing Stop และตรวจสอบความพร้อมทั้ง 5 ขั้นก่อนเริ่มรันจริง"
          icon={<TrendingUp size={18} className="text-brand" />}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {/* Visual SL Lifecycle Timeline */}
            <div className="wizard-lifecycle">
              <div className="wizard-lifecycle-node is-active">
                <span className="wizard-lifecycle-step">ระยะที่ 1: เปิดไม้ (0 R)</span>
                <strong>วาง SL/TP ตามสูตรกลยุทธ์</strong>
                <small className="text-muted">ควบคุมความเสี่ยงไม่เกินงบที่กำหนดในขั้นที่ 2</small>
              </div>
              <div className={`wizard-lifecycle-node ${cfg.trade_management.enable_auto_breakeven ? "is-active" : ""}`}>
                <span className="wizard-lifecycle-step">
                  ระยะที่ 2: กำไรถึง {cfg.trade_management.breakeven_trigger_r.toFixed(2)} R
                </span>
                <strong>
                  {cfg.trade_management.enable_auto_breakeven
                    ? `เลื่อน SL กันหน้าทุน (+${cfg.trade_management.breakeven_lock_points} จุด)`
                    : "ปิด Auto Break-Even อยู่"}
                </strong>
                <small className="text-muted">บวกชดเชย Spread/ค่าคอมและ Slippage ให้แล้ว</small>
              </div>
              <div className={`wizard-lifecycle-node ${cfg.trade_management.enable_trailing_stop ? "is-active" : ""}`}>
                <span className="wizard-lifecycle-step">ระยะที่ 3: วิ่งตามเทรนด์</span>
                <strong>
                  {cfg.trade_management.enable_trailing_stop
                    ? `ATR Trailing ${cfg.trade_management.trailing_atr_mult}x (ขยับทุก ${cfg.trade_management.trailing_step_points} จุด)`
                    : "ปิด ATR Trailing Stop อยู่"}
                </strong>
                <small className="text-muted">ล็อกกำไรตามการแกว่งตัวจริงจนกว่าจะถึง TP หรือปิดเทรนด์</small>
              </div>
            </div>

            {draftAudit.stepWarnings[5].length > 0 && (
              <div className="alert-box alert-warning">
                <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  {draftAudit.stepWarnings[5].map((w, idx) => (
                    <div key={idx}>{w}</div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid-2" style={{ gap: 12 }}>
              <label className="checkbox-tile">
                <input
                  type="checkbox"
                  checked={cfg.trade_management.enable_auto_breakeven}
                  onChange={(e) =>
                    setCfg({
                      ...cfg,
                      trade_management: {
                        ...cfg.trade_management,
                        enable_auto_breakeven: e.target.checked,
                      },
                    })
                  }
                />
                <span>
                  <strong>เปิด Auto True Break-Even</strong> — เลื่อน SL มาจุดเท่าทุนแท้ (รวมค่าคอมและ Slippage) เมื่อกำไรถึงเป้าหมาย
                </span>
              </label>

              <label className="checkbox-tile">
                <input
                  type="checkbox"
                  checked={cfg.trade_management.enable_trailing_stop}
                  onChange={(e) =>
                    setCfg({
                      ...cfg,
                      trade_management: {
                        ...cfg.trade_management,
                        enable_trailing_stop: e.target.checked,
                      },
                    })
                  }
                />
                <span>
                  <strong>เปิด ATR Trailing Stop</strong> — ขยับ SL ล็อกกำไรตามระยะแกว่งจริงของกราฟอัตโนมัติ
                </span>
              </label>
            </div>

            {(cfg.trade_management.enable_auto_breakeven || cfg.trade_management.enable_trailing_stop) && (
              <div className="form-grid">
                {cfg.trade_management.enable_auto_breakeven && (
                  <>
                    <FieldWithHint
                      label="เริ่มเลื่อนบังทุนเมื่อกำไรถึง (เท่าของความเสี่ยง R)"
                      hint={HINTS.breakeven_trigger_r}
                      forceOpen={showAllHints}
                      smallText="เช่น 1.15 = กำไร 1.15 เท่าของระยะ SL แล้วค่อยบังทุน"
                    >
                      <input
                        type="number"
                        step="any"
                        min="0.1"
                        value={cfg.trade_management.breakeven_trigger_r}
                        onChange={(e) =>
                          setCfg({
                            ...cfg,
                            trade_management: {
                              ...cfg.trade_management,
                              breakeven_trigger_r: Math.max(0.1, Number(e.target.value) || 1.0),
                            },
                          })
                        }
                      />
                    </FieldWithHint>

                    <FieldWithHint
                      label="ล็อกกำไรเผื่อเพิ่มจากจุดเท่าทุน (จุด)"
                      hint={HINTS.breakeven_lock_points}
                      forceOpen={showAllHints}
                      smallText="ใส่ 0 หากต้องการบังทุนพอดีเป๊ะ"
                    >
                      <input
                        type="number"
                        step="any"
                        min="0"
                        value={cfg.trade_management.breakeven_lock_points}
                        onChange={(e) =>
                          setCfg({
                            ...cfg,
                            trade_management: {
                              ...cfg.trade_management,
                              breakeven_lock_points: Math.max(0, Math.round(Number(e.target.value) || 0)),
                            },
                          })
                        }
                      />
                    </FieldWithHint>
                  </>
                )}

                {cfg.trade_management.enable_trailing_stop && (
                  <>
                    <FieldWithHint
                      label="ระยะห่าง Trailing Stop (เท่าของ ATR)"
                      hint={HINTS.trailing_atr_mult}
                      forceOpen={showAllHints}
                      smallText="ค่ายิ่งน้อย SL ยิ่งไล่ชิดกราฟ (1.5x กำลังสมดุล)"
                    >
                      <input
                        type="number"
                        step="any"
                        min="0.2"
                        value={cfg.trade_management.trailing_atr_mult}
                        onChange={(e) =>
                          setCfg({
                            ...cfg,
                            trade_management: {
                              ...cfg.trade_management,
                              trailing_atr_mult: Math.max(0.2, Number(e.target.value) || 1.5),
                            },
                          })
                        }
                      />
                    </FieldWithHint>

                    <FieldWithHint
                      label="ขยับ Trailing ขั้นต่ำทีละ (จุด)"
                      hint={HINTS.trailing_step_points}
                      forceOpen={showAllHints}
                      smallText="ช่วยลดการส่งคำสั่งแก้ออเดอร์ถี่เกินไป"
                    >
                      <input
                        type="number"
                        step="any"
                        min="1"
                        value={cfg.trade_management.trailing_step_points}
                        onChange={(e) =>
                          setCfg({
                            ...cfg,
                            trade_management: {
                              ...cfg.trade_management,
                              trailing_step_points: Math.max(1, Math.round(Number(e.target.value) || 10)),
                            },
                          })
                        }
                      />
                    </FieldWithHint>
                  </>
                )}
              </div>
            )}

            {/* Pre-Save Summary Checklist */}
            <div>
              <div className="settings-subhead" style={{ marginBottom: 10 }}>
                สรุปตรวจสอบความพร้อมทั้ง 5 ขั้นก่อนกดบันทึก
              </div>
              <div className="wizard-review-grid">
                <div className="wizard-review-row">
                  <div>
                    <strong>1. โหมดรัน & สินค้า:</strong>{" "}
                    <span>
                      {cfg.dry_run ? "โหมดซ้อม (DRY-RUN)" : "เทรดจริง (LIVE MONEY)"} • สินค้า {cfg.symbol} ({cfg.timeframe}) • เช็กทุก{" "}
                      {cfg.poll_seconds} วินาที • Magic #{cfg.magic_number}
                    </span>
                  </div>
                  {viewMode === "wizard" && (
                    <button type="button" className="btn btn-sm" onClick={() => goToStep(1)}>
                      แก้ไขขั้น 1
                    </button>
                  )}
                </div>

                <div className="wizard-review-row">
                  <div>
                    <strong>2. ขนาด Lot & Margin:</strong>{" "}
                    <span>
                      {LOT_MODE_LABELS[cfg.risk.lot_mode]} (
                      {cfg.risk.lot_mode === "dynamic_risk"
                        ? `${cfg.risk.risk_percent}% ต่อไม้`
                        : cfg.risk.lot_mode === "dynamic_equity_step"
                        ? `${cfg.risk.equity_step_lot} Lot ต่อทุก ${cfg.risk.equity_step_amount} ${liveCurrency}`
                        : `${cfg.risk.fixed_lot} Lot`}
                      ) • Max Lot: {cfg.risk.max_lot_cap ?? "ไม่จำกัด"} • Max Margin:{" "}
                      {cfg.risk.max_margin_usage_pct !== null ? `${cfg.risk.max_margin_usage_pct}%` : "ไม่จำกัด"}
                    </span>
                  </div>
                  {viewMode === "wizard" && (
                    <button type="button" className="btn btn-sm" onClick={() => goToStep(2)}>
                      แก้ไขขั้น 2
                    </button>
                  )}
                </div>

                <div className="wizard-review-row">
                  <div>
                    <strong>3. ต้นทุน Spread & Rollover:</strong>{" "}
                    <span>
                      MIN Net R:R = {cfg.preventive.min_net_rr ?? "ไม่บังคับ"} • Spread/TP ={" "}
                      {cfg.preventive.max_spread_to_tp_ratio ?? "ปิด"} • ค่าคอม ={" "}
                      {cfg.preventive.auto_detect_commission
                        ? "ดึงอัตโนมัติ (สำรอง " + (cfg.preventive.commission_per_lot_roundtrip ?? 0) + " USD)"
                        : `${cfg.preventive.commission_per_lot_roundtrip ?? 0} USD`}
                    </span>
                  </div>
                  {viewMode === "wizard" && (
                    <button type="button" className="btn btn-sm" onClick={() => goToStep(3)}>
                      แก้ไขขั้น 3
                    </button>
                  )}
                </div>

                <div className="wizard-review-row">
                  <div>
                    <strong>4. เบรกเกอร์ & ข่าวเศรษฐกิจ:</strong>{" "}
                    <span>
                      ขาดทุนวันละไม่เกิน {cfg.preventive.max_daily_loss_pct !== null ? `${cfg.preventive.max_daily_loss_pct}%` : "ไม่จำกัด"} • ถือสูงสุด{" "}
                      {cfg.preventive.max_open_positions_symbol ?? "ไม่จำกัด"}/{cfg.preventive.max_open_positions_total ?? "ไม่จำกัด"} ไม้ • กรองข่าว:{" "}
                      {cfg.preventive.enable_news_guard ? "เปิด" : "ปิด"}
                    </span>
                  </div>
                  {viewMode === "wizard" && (
                    <button type="button" className="btn btn-sm" onClick={() => goToStep(4)}>
                      แก้ไขขั้น 4
                    </button>
                  )}
                </div>
              </div>

              {draftAudit.totalCount === 0 ? (
                <div className="alert-box alert-success" style={{ marginTop: 12 }}>
                  <CheckCircle2 size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>พร้อมใช้งาน:</strong> ไม่พบจุดขัดแย้งกับกลยุทธ์{" "}
                    <code>{activeDescriptor?.name ?? strategiesMeta?.active_strategy}</code>
                  </div>
                </div>
              ) : (
                <div className="alert-box alert-warning" style={{ marginTop: 12 }}>
                  <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div style={{ flex: 1 }}>
                    <strong>พบข้อควรตรวจสอบ {draftAudit.totalCount} รายการ:</strong>
                    <ul className="conflict-list">
                      {draftAudit.allMessages.map((msg, idx) => (
                        <li key={idx}>{msg}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </div>
        </SectionCard>
      )}

      {/* Sticky Bottom Stepper Bar */}
      <div className="wizard-footer-nav">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {viewMode === "wizard" && activeStep > 1 && (
            <button type="button" className="btn" onClick={() => goToStep((activeStep - 1) as StepNumber)}>
              <ArrowLeft size={15} /> ขั้นก่อนหน้า ({activeStep - 1}/5)
            </button>
          )}
          <span className="text-muted" style={{ fontSize: 12.5 }}>
            {viewMode === "wizard"
              ? `ขั้นตอนที่ ${activeStep} จาก 5`
              : "กำลังแสดงครบทั้ง 5 ขั้นตอน"}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {viewMode === "wizard" && activeStep < 5 && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => goToStep((activeStep + 1) as StepNumber)}
            >
              ถัดไป: {stepItems[activeStep].title} <ArrowRight size={15} />
            </button>
          )}
          {(activeStep === 5 || viewMode === "all" || isDirty) && (
            <button
              className={`btn ${activeStep === 5 || viewMode === "all" ? "btn-primary" : ""}`}
              type="submit"
              aria-busy={saving}
            >
              <Save size={15} />
              {saving ? "กำลังบันทึก..." : justSaved ? "บันทึกเรียบร้อย" : "บันทึกการตั้งค่า"}
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
