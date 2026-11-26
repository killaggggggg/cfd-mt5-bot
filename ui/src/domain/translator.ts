/**
 * Pure domain helpers that translate raw English backend risk, preventive guard,
 * and calendar status strings into plain, everyday Thai for Thai traders,
 * plus actionable lot-sizing recovery suggestions for the Simulator.
 */

const LIMIT_NAME_THAI: Record<string, string> = {
  max_effective_account_leverage: "เพดานแรงดึงเงินรวมทั้งพอร์ต",
  max_effective_symbol_leverage: "เพดานแรงดึงเงินของสินค้านี้",
  max_margin_usage_pct: "เพดานสัดส่วนเงินประกัน (Margin %)",
  min_margin_level_pct: "ระดับรักษามาร์จินขั้นต่ำ (Margin Level %)",
  stopout_buffer: "ระยะปลอดภัยก่อนโดนบังคับปิดออเดอร์",
  max_lot_cap: "เพดาน Lot สูงสุดที่ตั้งไว้",
  volume_max: "เพดาน Lot สูงสุดของโบรคเกอร์",
};

function labelLimitName(rawName: string): string {
  const key = rawName.trim();
  return LIMIT_NAME_THAI[key] ?? key.replace(/_/g, " ");
}

function roundCleanSlStep(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (value >= 10000) return Math.floor(value / 1000) * 1000;
  if (value >= 2000) return Math.floor(value / 500) * 500;
  if (value >= 500) return Math.floor(value / 100) * 100;
  if (value >= 100) return Math.floor(value / 50) * 50;
  return Math.floor(value / 10) * 10;
}

/**
 * Translate a single backend risk / preventive / calendar message into plain Thai.
 * Preserves exact numbers and symbols while replacing technical English phrasing.
 */
export function translateBotMessage(rawMessage: string): string {
  const msg = rawMessage.trim();
  if (!msg) return "";

  const exactMap: Record<string, string> = {
    "Invalid entry price (<= 0)": "ราคาเข้าเทรดไม่ถูกต้อง (ต้องมากกว่า 0)",
    "Unable to determine notional value per lot":
      "ไม่สามารถคำนวณมูลค่าสัญญาต่อ 1 Lot จากข้อมูลโบรคเกอร์ได้",
    "Unable to determine required broker margin per lot":
      "ไม่สามารถคำนวณเงินประกัน (Margin) ต่อ 1 Lot จากโบรคเกอร์ได้",
    "Account trading disabled by broker": "บัญชีนี้ถูกโบรคเกอร์ปิดสิทธิ์การส่งออเดอร์",
    "Algorithmic trading (Expert Advisors) disabled on account":
      "บัญชี MT5 ยังไม่เปิดสิทธิ์เทรดอัตโนมัติ (Algo Trading / EA ปิดอยู่)",
    "Weekend market guard active":
      "ระบบป้องกันความเสี่ยงช่วงวันหยุดสุดสัปดาห์ทำงานอยู่ (ตลาดปิดหรือใกล้ปิด)",
    "Invalid symbol tick value / point size":
      "ข้อมูลมูลค่าต่อจุด (Tick Value / Point) ของสินค้านี้ไม่สมบูรณ์",
    "Computed SL loss per lot is <= 0":
      "ไม่สามารถคำนวณความเสี่ยงจากระยะ SL ต่อ 1 Lot ได้",
    "Economic calendar unavailable and fail_closed_on_calendar_error is enabled":
      "ดึงข้อมูลปฏิทินข่าวไม่สำเร็จ และระบบเปิดโหมดหยุดเทรดอัตโนมัติเมื่อขาดข้อมูลข่าวไว้",
  };

  if (exactMap[msg]) {
    return exactMap[msg];
  }

  let match: RegExpMatchArray | null = null;

  if ((match = msg.match(/^Calculated lot ([0-9.]+) is below broker volume_min ([0-9.]+)$/))) {
    return `ขนาด Lot ที่คำนวณได้ (${match[1]} Lot) น้อยกว่าขั้นต่ำของโบรคเกอร์ (${match[2]} Lot) จึงเปิดออเดอร์ไม่ได้`;
  }

  if ((match = msg.match(/^Capped at max_lot_cap \(([0-9.]+) -> ([0-9.]+)\)$/))) {
    return `จำกัดขนาด Lot ไม่ให้เกินเพดานสูงสุดที่ตั้งไว้ (ปรับจาก ${match[1]} เหลือ ${match[2]} Lot)`;
  }

  if ((match = msg.match(/^Capped at broker volume_max \(([0-9.]+) -> ([0-9.]+)\)$/))) {
    return `จำกัดขนาด Lot ไม่ให้เกินลิมิตสูงสุดของโบรคเกอร์ (ปรับจาก ${match[1]} เหลือ ${match[2]} Lot)`;
  }

  if ((match = msg.match(/^Tiered margin ([0-9.]+) exceeds allowed ([0-9.]+)$/))) {
    return `เงินประกันแบบขั้นบันได ($${match[1]}) สูงกว่าเพดานที่พอร์ตอนุญาต ($${match[2]})`;
  }

  if ((match = msg.match(/^Tiered broker margin adjustment \(([0-9.]+) -> ([0-9.]+)\)$/))) {
    return `โบรคเกอร์ปรับลดขนาด Lot ตามเงื่อนไขเงินประกันขั้นบันได (จาก ${match[1]} เหลือ ${match[2]} Lot)`;
  }

  if ((match = msg.match(/^Non-positive account capital \(([^)]+)\)$/))) {
    return `ยอดเงินทุนที่ใช้คำนวณเป็นศูนย์หรือติดลบ (${match[1]})`;
  }

  if ((match = msg.match(/^Scaled down by ([a-zA-Z0-9_]+) \(([0-9.]+) -> ([0-9.]+)\)$/))) {
    return `ปรับลดขนาด Lot เพื่อไม่ให้เกิน${labelLimitName(match[1])} (จาก ${match[2]} เหลือ ${match[3]} Lot)`;
  }

  if ((match = msg.match(/^Lot ([0-9.]+) exceeds ([a-zA-Z0-9_]+) limit \(([0-9.]+)\)$/))) {
    return `ขนาด Lot (${match[1]}) เกินกว่า${labelLimitName(match[2])}ที่ตั้งไว้ (สูงสุด ${match[3]} Lot)`;
  }

  if ((match = msg.match(/^Daily drawdown ([0-9.]+)% reached max limit ([0-9.]+)%$/))) {
    return `ขาดทุนสะสมวันนี้ ${match[1]}% ถึงเพดานหยุดพักอัตโนมัติ (${match[2]}%) แล้ว`;
  }

  if ((match = msg.match(/^Stale quote \(([0-9.]+)s > ([0-9.]+)s\); market may be closed$/))) {
    return `ราคาตลาดค้างไม่อัปเดต (${match[1]} วินาที เกิน ${match[2]} วินาที) — ตลาดอาจปิดอยู่`;
  }

  if ((match = msg.match(/^Total open positions (\d+) reached limit (\d+)$/))) {
    return `จำนวนออเดอร์รวมทุกสินค้าครบเพดานแล้ว (${match[1]}/${match[2]} ไม้)`;
  }

  if ((match = msg.match(/^Open positions for ([^\s(]+) \((\d+)\) reached limit (\d+)$/))) {
    return `จำนวนออเดอร์ของ ${match[1]} ครบเพดานรายสินค้าแล้ว (${match[2]}/${match[3]} ไม้)`;
  }

  if (
    (match = msg.match(
      /^Inside swap rollover blackout window around server hour (\d{1,2}):00$/
    ))
  ) {
    return `อยู่ในช่วงเวลาคิดดอกเบี้ยข้ามคืน (Rollover ประมาณ ${match[1]}:00 น. เวลาเซิร์ฟเวอร์)`;
  }

  if ((match = msg.match(/^Spread ([0-9.]+) pts exceeds max ([0-9.]+) pts$/))) {
    return `ค่า Spread ตอนนี้กว้างเกินเกณฑ์ (${match[1]} จุด / เพดานไม่เกิน ${match[2]} จุด)`;
  }

  if ((match = msg.match(/^Elevated spread: ([0-9.]+) pts \(limit ([0-9.]+)\)$/))) {
    return `คำเตือน: ค่า Spread ค่อนข้างกว้าง (${match[1]} จุด จากเพดาน ${match[2]} จุด)`;
  }

  if ((match = msg.match(/^Spread consumes ([0-9.]+)% of TP \(max ([0-9.]+)%\)$/))) {
    return `ค่า Spread กินส่วนแบ่งกำไรไปถึง ${match[1]}% ของระยะ TP (กำหนดไม่เกิน ${match[2]}%)`;
  }

  if ((match = msg.match(/^Spread\/ATR spike ([0-9.]+)% exceeds max ([0-9.]+)%$/))) {
    return `ค่า Spread กว้างผิดปกติเทียบกับความผันผวน ATR (${match[1]}% เกินเพดาน ${match[2]}%)`;
  }

  if (
    (match = msg.match(
      /^3x rollover swap projected \(\$([0-9.+-]+)\/lot over ([0-9.]+) eq\. days\)$/
    ))
  ) {
    return `คำเตือน: วันถือที่เลือกมีคืนคิด Swap 3 เท่า (คาดการณ์ $${match[1]}/Lot สำหรับ ${match[2]} วันเทียบเท่า)`;
  }

  if ((match = msg.match(/^Projected swap consumes ([0-9.]+)% of TP \(max ([0-9.]+)%\)$/))) {
    return `ค่าธรรมเนียมถือข้ามคืน (Swap) กินกำไรไป ${match[1]}% ของระยะ TP (กำหนดไม่เกิน ${match[2]}%)`;
  }

  if ((match = msg.match(/^Net TP reward after costs is non-positive \(\$([^/]+)\/lot\)$/))) {
    return `กำไรสุทธิหลังหักต้นทุน (Spread/Commission/Swap) ไม่คุ้มหรือติดลบ ($${match[1]}/Lot)`;
  }

  if (
    (match = msg.match(/^Net R:R ([0-9.]+) below minimum ([0-9.]+) \(Gross R:R ([0-9.]+)\)$/))
  ) {
    return `สัดส่วนกำไรต่อความเสี่ยงหลังหักต้นทุน (Net R:R ${match[1]}) ต่ำกว่าเกณฑ์ขั้นต่ำ (${match[2]}) [ก่อนหักต้นทุน ${match[3]}]`;
  }

  if ((match = msg.match(/^Symbol (\S+) trading is disabled$/))) {
    return `โบรคเกอร์ปิดการซื้อขายสินค้า ${match[1]} ชั่วคราว`;
  }

  if ((match = msg.match(/^Symbol (\S+) is in close-only mode$/))) {
    return `สินค้า ${match[1]} เปิดให้ปิดออเดอร์เดิมเท่านั้น (ห้ามเปิดออเดอร์ใหม่)`;
  }

  if ((match = msg.match(/^Symbol (\S+) allows LONG only$/))) {
    return `สินค้า ${match[1]} อนุญาตให้เปิดเฉพาะฝั่ง BUY เท่านั้น`;
  }

  if ((match = msg.match(/^Symbol (\S+) allows SHORT only$/))) {
    return `สินค้า ${match[1]} อนุญาตให้เปิดเฉพาะฝั่ง SELL เท่านั้น`;
  }

  if (
    (match = msg.match(
      /^Invalid BUY levels: require SL \(([^)]+)\) < Entry \(([^)]+)\) < TP \(([^)]+)\)$/
    ))
  ) {
    return `ระดับราคา BUY ไม่ถูกต้อง: ต้องตั้ง SL (${match[1]}) < ราคาเข้า (${match[2]}) < TP (${match[3]})`;
  }

  if (
    (match = msg.match(
      /^Invalid SELL levels: require TP \(([^)]+)\) < Entry \(([^)]+)\) < SL \(([^)]+)\)$/
    ))
  ) {
    return `ระดับราคา SELL ไม่ถูกต้อง: ต้องตั้ง TP (${match[1]}) < ราคาเข้า (${match[2]}) < SL (${match[3]})`;
  }

  if ((match = msg.match(/^SL distance ([0-9.]+) pts < broker stops_level ([0-9.]+)$/))) {
    return `ระยะ SL (${match[1]} จุด) ชิดราคาตลาดเกินไป (โบรคเกอร์กำหนดห่างอย่างน้อย ${match[2]} จุด)`;
  }

  if ((match = msg.match(/^TP distance ([0-9.]+) pts < broker stops_level ([0-9.]+)$/))) {
    return `ระยะ TP (${match[1]} จุด) ชิดราคาตลาดเกินไป (โบรคเกอร์กำหนดห่างอย่างน้อย ${match[2]} จุด)`;
  }

  if ((match = msg.match(/^Minor bank holiday today \(([^)]+)\):\s*(.+)$/))) {
    return `คำเตือน: วันนี้มีวันหยุดธนาคารย่อย (${match[1]}): ${match[2]}`;
  }

  if ((match = msg.match(/^Major bank holiday today \(([^)]+)\):\s*(.+)$/))) {
    return `หยุดเทรดเนื่องจากวันหยุดธนาคารสำคัญ (${match[1]}): ${match[2]}`;
  }

  if ((match = msg.match(/^Upcoming minor bank holiday \(([^)]+)\):\s*(.+)$/))) {
    return `คำเตือน: ใกล้ถึงวันหยุดธนาคารย่อย (${match[1]}): ${match[2]}`;
  }

  if ((match = msg.match(/^Upcoming major bank holiday \(([^)]+)\):\s*(.+)$/))) {
    return `หยุดเทรดช่วงใกล้วันหยุดธนาคารสำคัญ (${match[1]}): ${match[2]}`;
  }

  if ((match = msg.match(/^(HIGH|MEDIUM) news blackout \(([^)]+)\):\s*(.+)$/))) {
    return `งดเปิดออเดอร์ช่วงข่าวสำคัญระดับ ${match[1]} (${match[2]}): ${match[3]}`;
  }

  if ((match = msg.match(/^Upcoming (HIGH|MEDIUM) news in ([^\s(]+) \(([^)]+)\):\s*(.+)$/))) {
    return `คำเตือน: กำลังจะมีข่าวระดับ ${match[1]} ของ ${match[3]} ในอีก ${match[2]} (${match[4]})`;
  }

  if ((match = msg.match(/^MT5 Calendar Bridge parse error:\s*(.+)$/))) {
    return `อ่านข้อมูลจาก MT5 Calendar Bridge ไม่สำเร็จ: ${match[1]}`;
  }

  if ((match = msg.match(/^Failed to parse custom events file:\s*(.+)$/))) {
    return `อ่านไฟล์ข่าวที่กำหนดเองไม่สำเร็จ: ${match[1]}`;
  }

  return msg;
}

/**
 * Extract broker `volume_min` from RiskEngine rejection messages when present.
 */
export function extractVolumeMinFromReasons(reasons: string[]): number | null {
  for (const r of reasons) {
    const match = r.match(/below broker volume_min ([0-9.]+)/);
    if (match) {
      const parsed = Number(match[1]);
      if (Number.isFinite(parsed) && parsed > 0) {
        return parsed;
      }
    }
  }
  return null;
}

export interface MinLotGuidance {
  minLot: number;
  lossAtMinLotUsd: number;
  minRiskForOneStepUsd: number;
  recommendedSlPoints: number | null;
  recommendedSlPriceDelta: number | null;
  estimatedSlLossAtRecommendedUsd: number | null;
  slPriceDelta: number;
  summaryText: string;
}

/**
 * Build practical, plain-Thai guidance whenever calculated Lot hits 0 because
 * the chosen SL distance and risk budget produce less than broker `volume_min`.
 */
export function buildMinLotGuidance(params: {
  lot: number;
  volumeMin: number;
  riskBudgetUsd: number;
  slPointsUsed: number;
  slPriceDelta: number;
  netSlRiskPerLotUsd: number;
}): MinLotGuidance | null {
  const {
    lot,
    volumeMin,
    riskBudgetUsd,
    slPointsUsed,
    slPriceDelta,
    netSlRiskPerLotUsd,
  } = params;

  if (lot > 0 || volumeMin <= 0 || slPointsUsed <= 0 || netSlRiskPerLotUsd <= 0) {
    return null;
  }

  const lossAtMinLotUsd = netSlRiskPerLotUsd * volumeMin;
  const minRiskForOneStepUsd = Math.ceil(lossAtMinLotUsd + 1);
  const lossPerPointAtMinLot = lossAtMinLotUsd / slPointsUsed;
  const pricePerPoint = slPriceDelta / slPointsUsed;

  let recommendedSlPoints: number | null = null;
  let recommendedSlPriceDelta: number | null = null;
  let estimatedSlLossAtRecommendedUsd: number | null = null;

  if (lossPerPointAtMinLot > 0 && riskBudgetUsd > 0) {
    const rawAffordableSl = (riskBudgetUsd * 0.9) / lossPerPointAtMinLot;
    const cleaned = roundCleanSlStep(rawAffordableSl);
    if (cleaned >= 50 && cleaned < slPointsUsed) {
      recommendedSlPoints = cleaned;
      recommendedSlPriceDelta = cleaned * pricePerPoint;
      estimatedSlLossAtRecommendedUsd = cleaned * lossPerPointAtMinLot;
    }
  }

  const priceDecimals = pricePerPoint < 0.001 ? 4 : pricePerPoint < 0.05 ? 2 : 1;

  return {
    minLot: volumeMin,
    lossAtMinLotUsd,
    minRiskForOneStepUsd,
    recommendedSlPoints,
    recommendedSlPriceDelta,
    estimatedSlLossAtRecommendedUsd,
    slPriceDelta,
    summaryText: `โบรคเกอร์ให้เปิดเล็กสุดที่ ${volumeMin.toFixed(2)} Lot แต่ด้วยระยะ SL ตอนนี้ (${Math.round(slPointsUsed).toLocaleString()} จุด • ราคาขยับ ${slPriceDelta.toFixed(priceDecimals)}) ถ้าเปิด ${volumeMin.toFixed(2)} Lot จะเสี่ยงประมาณ $${lossAtMinLotUsd.toFixed(2)} ซึ่งเกินงบที่คุณตั้งไว้ ($${riskBudgetUsd.toFixed(2)}) ระบบจึงปัดเป็น 0.00 Lot เพื่อปกป้องพอร์ตไม่ให้เสี่ยงเกินงบ`,
  };
}

/**
 * Format an ISO timestamp string into readable Bangkok local time (`Asia/Bangkok`, UTC+7).
 */
export function formatThaiDateTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) return String(isoString);
  return (
    dt.toLocaleString("th-TH", {
      timeZone: "Asia/Bangkok",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }) + " น. (เวลาไทย)"
  );
}

/**
 * Format an ISO timestamp string into compact UTC time for broker/feed cross-reference.
 */
export function formatUtcTimeShort(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) return String(isoString);
  const hh = String(dt.getUTCHours()).padStart(2, "0");
  const mm = String(dt.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}

/**
 * Translate M5/M15 candle direction tags (`UP`/`DOWN`/`FLAT`) into plain Thai.
 */
export function translateCandleDir(dir: string | null | undefined): string {
  switch ((dir ?? "").toUpperCase()) {
    case "UP":
      return "ขึ้น";
    case "DOWN":
      return "ลง";
    case "FLAT":
      return "ทรงตัว";
    default:
      return dir || "—";
  }
}
