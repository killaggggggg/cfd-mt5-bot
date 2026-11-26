import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Calculator,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Info,
  Lightbulb,
  SlidersHorizontal,
  XOctagon,
} from "lucide-react";
import { api } from "../../data/api";
import {
  buildMinLotGuidance,
  extractVolumeMinFromReasons,
  translateBotMessage,
} from "../../domain/translator";
import type { SimulateResponse } from "../../domain/types";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  defaultSymbol: string;
  onNotify: (msg: string) => void;
}

type SizingInputMode = "risk_percent" | "risk_money" | "fixed_lot" | "equity_step";
type StopInputMode = "points_rr" | "price_levels";

interface SimOverrides {
  symbol?: string;
  side?: "BUY" | "SELL";
  useCustomEquity?: boolean;
  customEquity?: number;
  sizingMode?: SizingInputMode;
  riskPercent?: number;
  riskAmount?: number;
  fixedLot?: number;
  stepAmount?: number;
  stepLot?: number;
  stopMode?: StopInputMode;
  slPoints?: string;
  tpPoints?: string;
  entryPrice?: string;
  slPrice?: string;
  tpPrice?: string;
  rrRatio?: number;
  holdDays?: number;
}

const QUICK_SYMBOLS = ["EURUSD", "XAUUSD", "US30", "NAS100", "GBPUSD", "USDJPY"];

const SIZING_HINTS: Record<SizingInputMode, string> = {
  risk_percent: "คิดจาก % ของเงินทุน ถ้าออเดอร์วิ่งชน SL จะเสียไม่เกินงบที่ตั้งไว้",
  risk_money: "กำหนดเป็นเงิน USD ชัดเจนเลยว่าไม้นี้พร้อมยอมขาดทุนสูงสุดเท่าไร",
  fixed_lot: "ล็อกขนาด Lot คงที่ตามที่กรอก แล้วตรวจดูว่าชน SL จะเสียกี่ดอลลาร์",
  equity_step: "สูตรไต่ระดับ: เพิ่มขนาด Lot ตามขั้นเงินทุนในพอร์ตที่เติบโตขึ้น",
};

function formatPriceDelta(delta: number): string {
  if (!Number.isFinite(delta) || delta <= 0) return "0";
  if (delta < 0.05) return delta.toFixed(4);
  if (delta < 2) return delta.toFixed(3);
  return delta.toFixed(2);
}

export function SimulatorPage({ defaultSymbol, onNotify }: Props) {
  const [symbol, setSymbol] = useState(defaultSymbol || "EURUSD");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");

  // Step 2: Equity & Sizing
  const [useCustomEquity, setUseCustomEquity] = useState(false);
  const [customEquity, setCustomEquity] = useState(5000);
  const [sizingMode, setSizingMode] = useState<SizingInputMode>("risk_percent");
  const [riskPercent, setRiskPercent] = useState(1.0);
  const [riskAmount, setRiskAmount] = useState(25);
  const [fixedLot, setFixedLot] = useState(0.05);
  const [stepAmount, setStepAmount] = useState(1000);
  const [stepLot, setStepLot] = useState(0.01);

  // Step 3: SL / TP + Hold Days + Advanced
  const [stopMode, setStopMode] = useState<StopInputMode>("points_rr");
  const [slPoints, setSlPoints] = useState("");
  const [tpPoints, setTpPoints] = useState("");
  const [rrRatio, setRrRatio] = useState(2.0);
  const [entryPrice, setEntryPrice] = useState("");
  const [slPrice, setSlPrice] = useState("");
  const [tpPrice, setTpPrice] = useState("");

  const [holdDays, setHoldDays] = useState(0.0);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [ignoreCaps, setIgnoreCaps] = useState(false);
  const [customMaxLot, setCustomMaxLot] = useState("");
  const [customMaxLev, setCustomMaxLev] = useState("");
  const [customMaxMarginPct, setCustomMaxMarginPct] = useState("");

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SimulateResponse | null>(null);

  const runSimulation = async (overrides: SimOverrides = {}, silent = false) => {
    const activeSymbol = overrides.symbol ?? symbol;
    const activeSide = overrides.side ?? side;
    const activeUseCustomEq = overrides.useCustomEquity ?? useCustomEquity;
    const activeCustomEq = overrides.customEquity ?? customEquity;
    const activeSizingMode = overrides.sizingMode ?? sizingMode;
    const activeRiskPercent = overrides.riskPercent ?? riskPercent;
    const activeRiskAmount = overrides.riskAmount ?? riskAmount;
    const activeFixedLot = overrides.fixedLot ?? fixedLot;
    const activeStepAmount = overrides.stepAmount ?? stepAmount;
    const activeStepLot = overrides.stepLot ?? stepLot;
    const activeStopMode = overrides.stopMode ?? stopMode;
    const activeSlPoints = overrides.slPoints ?? slPoints;
    const activeTpPoints = overrides.tpPoints ?? tpPoints;
    const activeEntryPrice = overrides.entryPrice ?? entryPrice;
    const activeSlPrice = overrides.slPrice ?? slPrice;
    const activeTpPrice = overrides.tpPrice ?? tpPrice;
    const activeRrRatio = overrides.rrRatio ?? rrRatio;
    const activeHoldDays = overrides.holdDays ?? holdDays;

    setLoading(true);
    try {
      const lotMode =
        activeSizingMode === "fixed_lot"
          ? "fixed"
          : activeSizingMode === "equity_step"
          ? "dynamic_equity_step"
          : "dynamic_risk";

      const payload: Record<string, unknown> = {
        symbol: activeSymbol,
        side: activeSide,
        lot_mode: lotMode,
        hold_days: activeHoldDays,
        ignore_leverage_caps: ignoreCaps,
      };

      if (activeUseCustomEq && activeCustomEq > 0) {
        payload.custom_equity = activeCustomEq;
      }

      if (activeSizingMode === "risk_money") {
        payload.risk_amount = activeRiskAmount;
      } else if (activeSizingMode === "fixed_lot") {
        payload.fixed_lot = activeFixedLot;
      } else if (activeSizingMode === "equity_step") {
        payload.equity_step_amount = activeStepAmount;
        payload.equity_step_lot = activeStepLot;
      } else {
        payload.risk_percent = activeRiskPercent;
      }

      if (activeStopMode === "price_levels") {
        if (activeEntryPrice.trim()) payload.entry_price = Number(activeEntryPrice);
        if (activeSlPrice.trim()) payload.sl_price = Number(activeSlPrice);
        if (activeTpPrice.trim()) payload.tp_price = Number(activeTpPrice);
      } else {
        if (activeSlPoints.trim()) payload.sl_points = Number(activeSlPoints);
        if (activeTpPoints.trim()) {
          payload.tp_points = Number(activeTpPoints);
        } else {
          payload.rr_ratio = activeRrRatio;
        }
      }

      if (!ignoreCaps) {
        if (customMaxLot.trim()) payload.max_lot_cap = Number(customMaxLot);
        if (customMaxLev.trim()) payload.max_effective_account_leverage = Number(customMaxLev);
        if (customMaxMarginPct.trim()) payload.max_margin_usage_pct = Number(customMaxMarginPct);
      }

      const res = await api.simulate(payload);
      setResult(res);
      if (!res.ok && res.error && !silent) {
        onNotify(res.error);
      }
    } catch (err) {
      if (!silent) {
        onNotify(`คำนวณไม่สำเร็จ: ${(err as Error).message}`);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void runSimulation({}, true);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runSimulation({}, false);
  };

  const prev = result?.preventive;
  const plan = result?.lot_plan;
  const costs = prev?.costs;
  const snap = plan?.leverage_snapshot;

  const activeEquityDisplay = useCustomEquity
    ? customEquity
    : result?.live_equity ?? result?.equity_used ?? 0;

  const slPriceDelta = result?.signal
    ? Math.abs(result.signal.entry_price - result.signal.sl_price)
    : 0;
  const tpPriceDelta = result?.signal
    ? Math.abs(result.signal.tp_price - result.signal.entry_price)
    : 0;

  const volumeMin =
    plan && plan.rejection_reasons.length > 0
      ? extractVolumeMinFromReasons(plan.rejection_reasons)
      : null;

  const minLotGuidance =
    plan && costs && result?.sl_points_used && volumeMin
      ? buildMinLotGuidance({
          lot: plan.lot,
          volumeMin,
          riskBudgetUsd: plan.risk_budget_money,
          slPointsUsed: result.sl_points_used,
          slPriceDelta,
          netSlRiskPerLotUsd: costs.net_sl_risk_per_lot,
        })
      : null;

  return (
    <div className="ops-container">
      <PageHeader
        title="ทดลองคำนวณ Lot และวางแผนความเสี่ยง"
        subtitle="ตั้งค่าตามลำดับขั้น 1 → 3 ด้านซ้าย เพื่อดูขนาด Lot ที่ปลอดภัย ต้นทุนจริง และคำแนะนำปรับระยะทันทีด้านขวา"
      />

      <div className="grid-2" style={{ alignItems: "start" }}>
        {/* Left: Step 1 -> Step 2 -> Step 3 Controls */}
        <SectionCard
          title="ตั้งค่าจำลองแบบทีละขั้น"
          subtitle="ทดลองได้อิสระ ไม่มีผลกระทบกับบัญชีจริงหรือการตั้งค่าบอท"
          icon={<Calculator size={18} className="text-brand" />}
        >
          <form onSubmit={handleSubmit} noValidate style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {/* Step 1: Symbol & Side */}
            <div className="sim-step">
              <div className="sim-step-head">
                <span className="sim-step-badge">1</span>
                <div>
                  <h3 className="sim-step-title">เลือกสินค้าและฝั่งเทรด</h3>
                  <div className="sim-step-sub">จิ้มเลือกสินค้ายอดนิยมหรือพิมพ์ชื่อตามโบรคเกอร์ แล้วเลือกฝั่ง BUY / SELL</div>
                </div>
              </div>

              <div className="chip-row">
                {QUICK_SYMBOLS.map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    className={`chip-btn ${symbol.toUpperCase().startsWith(sym) ? "is-active" : ""}`}
                    onClick={() => {
                      setSymbol(sym);
                      setSlPoints("");
                      setTpPoints("");
                      setEntryPrice("");
                      setSlPrice("");
                      setTpPrice("");
                      void runSimulation(
                        {
                          symbol: sym,
                          slPoints: "",
                          tpPoints: "",
                          entryPrice: "",
                          slPrice: "",
                          tpPrice: "",
                        },
                        true
                      );
                    }}
                  >
                    {sym}
                  </button>
                ))}
              </div>

              <div className="form-grid">
                <div className="field">
                  <label>สินค้า (พิมพ์ชื่อใดก็ได้)</label>
                  <input type="text" value={symbol} onChange={(e) => setSymbol(e.target.value)} required />
                </div>
                <div className="field">
                  <label>ทิศทางที่ต้องการเทรด</label>
                  <div className="seg-group">
                    <button
                      type="button"
                      className={`seg-btn is-buy ${side === "BUY" ? "is-active" : ""}`}
                      onClick={() => {
                        setSide("BUY");
                        void runSimulation({ side: "BUY" }, true);
                      }}
                    >
                      BUY (ซื้อขึ้น)
                    </button>
                    <button
                      type="button"
                      className={`seg-btn is-sell ${side === "SELL" ? "is-active" : ""}`}
                      onClick={() => {
                        setSide("SELL");
                        void runSimulation({ side: "SELL" }, true);
                      }}
                    >
                      SELL (ขายลง)
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Step 2: Equity & Risk Budget */}
            <div className="sim-step">
              <div className="sim-step-head">
                <span className="sim-step-badge">2</span>
                <div>
                  <h3 className="sim-step-title">กำหนดเงินทุนและงบความเสี่ยง</h3>
                  <div className="sim-step-sub">เลือกใช้ทุนจริงในพอร์ตหรือลองจำลองพอร์ตอื่น พร้อมวิธีคุมความเสี่ยง</div>
                </div>
              </div>

              <div className="seg-group">
                <button
                  type="button"
                  className={`seg-btn ${!useCustomEquity ? "is-active" : ""}`}
                  onClick={() => setUseCustomEquity(false)}
                >
                  ใช้เงินจริงใน MT5{result?.live_equity ? ` ($${result.live_equity.toLocaleString()})` : ""}
                </button>
                <button
                  type="button"
                  className={`seg-btn ${useCustomEquity ? "is-active" : ""}`}
                  onClick={() => setUseCustomEquity(true)}
                >
                  กำหนดทุนจำลองเอง ($)
                </button>
              </div>

              {useCustomEquity && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div className="field">
                    <input
                      type="number"
                      min="1"
                      step="any"
                      value={customEquity}
                      onChange={(e) => setCustomEquity(Number(e.target.value))}
                    />
                  </div>
                  <div className="chip-row">
                    {[100, 500, 1000, 5000, 10000].map((eq) => (
                      <button
                        key={eq}
                        type="button"
                        className={`chip-btn tabular ${customEquity === eq ? "is-active" : ""}`}
                        onClick={() => setCustomEquity(eq)}
                      >
                        ${eq.toLocaleString()}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="field">
                <label>วิธีคำนวณขนาด Lot</label>
                <div className="seg-group">
                  <button
                    type="button"
                    className={`seg-btn ${sizingMode === "risk_percent" ? "is-active" : ""}`}
                    onClick={() => setSizingMode("risk_percent")}
                  >
                    % ความเสี่ยง
                  </button>
                  <button
                    type="button"
                    className={`seg-btn ${sizingMode === "risk_money" ? "is-active" : ""}`}
                    onClick={() => setSizingMode("risk_money")}
                  >
                    ระบุเป็นเงิน ($)
                  </button>
                  <button
                    type="button"
                    className={`seg-btn ${sizingMode === "fixed_lot" ? "is-active" : ""}`}
                    onClick={() => setSizingMode("fixed_lot")}
                  >
                    ระบุ Lot เอง
                  </button>
                  <button
                    type="button"
                    className={`seg-btn ${sizingMode === "equity_step" ? "is-active" : ""}`}
                    onClick={() => setSizingMode("equity_step")}
                  >
                    ตามขั้นพอร์ต
                  </button>
                </div>
                <small>{SIZING_HINTS[sizingMode]}</small>
              </div>

              {sizingMode === "risk_percent" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div className="field">
                    <input
                      type="number"
                      step="any"
                      min="0.01"
                      max="100"
                      value={riskPercent}
                      onChange={(e) => setRiskPercent(Number(e.target.value))}
                    />
                    {activeEquityDisplay > 0 && Number.isFinite(riskPercent) && (
                      <small className="tabular">
                        งบยอมขาดทุนสูงสุดต่อไม้: <strong>${((activeEquityDisplay * riskPercent) / 100).toFixed(2)}</strong>{" "}
                        ({riskPercent}% ของ ${activeEquityDisplay.toLocaleString()})
                      </small>
                    )}
                  </div>
                  <div className="chip-row">
                    {[0.5, 1, 2, 3, 5].map((pct) => (
                      <button
                        key={pct}
                        type="button"
                        className={`chip-btn tabular ${riskPercent === pct ? "is-active" : ""}`}
                        onClick={() => setRiskPercent(pct)}
                      >
                        {pct}%
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {sizingMode === "risk_money" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div className="field">
                    <input
                      type="number"
                      step="any"
                      min="0.01"
                      value={riskAmount}
                      onChange={(e) => setRiskAmount(Number(e.target.value))}
                    />
                  </div>
                  <div className="chip-row">
                    {[10, 25, 50, 100, 250].map((amt) => (
                      <button
                        key={amt}
                        type="button"
                        className={`chip-btn tabular ${riskAmount === amt ? "is-active" : ""}`}
                        onClick={() => setRiskAmount(amt)}
                      >
                        ${amt}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {sizingMode === "fixed_lot" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div className="field">
                    <input
                      type="number"
                      step="any"
                      min="0.01"
                      value={fixedLot}
                      onChange={(e) => setFixedLot(Number(e.target.value))}
                    />
                  </div>
                  <div className="chip-row">
                    {[0.01, 0.05, 0.1, 0.5, 1.0].map((l) => (
                      <button
                        key={l}
                        type="button"
                        className={`chip-btn tabular ${fixedLot === l ? "is-active" : ""}`}
                        onClick={() => setFixedLot(l)}
                      >
                        {l.toFixed(2)} Lot
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {sizingMode === "equity_step" && (
                <div className="form-grid">
                  <div className="field">
                    <label>ทุกๆ เงินทุน ($)</label>
                    <input
                      type="number"
                      min="1"
                      step="any"
                      value={stepAmount}
                      onChange={(e) => setStepAmount(Number(e.target.value))}
                    />
                  </div>
                  <div className="field">
                    <label>เพิ่มขนาด (Lot)</label>
                    <input
                      type="number"
                      min="0.01"
                      step="any"
                      value={stepLot}
                      onChange={(e) => setStepLot(Number(e.target.value))}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Step 3: SL / TP + Hold Days + Advanced */}
            <div className="sim-step">
              <div className="sim-step-head">
                <span className="sim-step-badge">3</span>
                <div>
                  <h3 className="sim-step-title">ตั้งระยะ SL / TP และจำนวนวันถือ</h3>
                  <div className="sim-step-sub">เว้นว่าง SL เพื่อให้บอทใช้ระยะแกว่งจริงของกราฟ (1.5x ATR) หรือใส่ระยะเอง</div>
                </div>
              </div>

              <div className="seg-group">
                <button
                  type="button"
                  className={`seg-btn ${stopMode === "points_rr" ? "is-active" : ""}`}
                  onClick={() => setStopMode("points_rr")}
                >
                  ใส่ระยะจุด & R:R
                </button>
                <button
                  type="button"
                  className={`seg-btn ${stopMode === "price_levels" ? "is-active" : ""}`}
                  onClick={() => setStopMode("price_levels")}
                >
                  ใส่ราคาจากกราฟจริง
                </button>
              </div>

              {stopMode === "points_rr" ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="form-grid">
                    <div className="field">
                      <label>ระยะ SL (จุด)</label>
                      <input
                        type="number"
                        step="any"
                        min="1"
                        placeholder="เว้นว่าง = 1.5x ATR"
                        value={slPoints}
                        onChange={(e) => setSlPoints(e.target.value)}
                      />
                    </div>
                    <div className="field">
                      <label>ระยะ TP (จุด)</label>
                      <input
                        type="number"
                        step="any"
                        min="1"
                        placeholder={`เว้นว่าง = ${rrRatio}x SL`}
                        value={tpPoints}
                        onChange={(e) => setTpPoints(e.target.value)}
                      />
                    </div>
                  </div>

                  {!tpPoints.trim() && (
                    <div className="chip-row" style={{ alignItems: "center" }}>
                      <span className="text-muted" style={{ fontSize: 12 }}>อัตราส่วนเป้ากำไร R:R:</span>
                      {[1.5, 2, 3, 4, 5].map((r) => (
                        <button
                          key={r}
                          type="button"
                          className={`chip-btn tabular ${rrRatio === r ? "is-active" : ""}`}
                          onClick={() => setRrRatio(r)}
                        >
                          1:{r}
                        </button>
                      ))}
                    </div>
                  )}

                  {result?.ok && result.sl_points_used && slPriceDelta > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                      <span className="text-muted tabular" style={{ fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <Info size={13} />
                        รอบนี้ใช้ SL {Math.round(result.sl_points_used).toLocaleString()} จุด (ราคาขยับ {formatPriceDelta(slPriceDelta)})
                      </span>
                      {slPoints.trim() && (
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            setSlPoints("");
                            setTpPoints("");
                            void runSimulation({ slPoints: "", tpPoints: "" }, true);
                          }}
                        >
                          ใช้ระยะ ATR อัตโนมัติ
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="form-grid">
                  <div className="field">
                    <label>ราคาเข้า (Entry)</label>
                    <input
                      type="number"
                      step="any"
                      placeholder="เว้นว่าง = ราคาตลาดตอนนี้"
                      value={entryPrice}
                      onChange={(e) => setEntryPrice(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label>ราคาตัดขาดทุน (SL)</label>
                    <input
                      type="number"
                      step="any"
                      placeholder="ใส่ราคา SL บนกราฟ"
                      value={slPrice}
                      onChange={(e) => setSlPrice(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label>ราคาทำกำไร (TP)</label>
                    <input
                      type="number"
                      step="any"
                      placeholder="เว้นว่าง = คิดจาก R:R"
                      value={tpPrice}
                      onChange={(e) => setTpPrice(e.target.value)}
                    />
                  </div>
                </div>
              )}

              <div className="field">
                <label>ระยะถือออเดอร์ (วัน)</label>
                <div className="chip-row" style={{ alignItems: "center" }}>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    style={{ width: 110 }}
                    value={holdDays}
                    onChange={(e) => setHoldDays(Number(e.target.value))}
                  />
                  {[
                    { label: "จบในวัน (0)", val: 0 },
                    { label: "ค้าง 1 คืน", val: 1 },
                    { label: "ค้าง 3 วัน", val: 3 },
                    { label: "ข้ามสัปดาห์ (7)", val: 7 },
                  ].map((item) => (
                    <button
                      key={item.val}
                      type="button"
                      className={`chip-btn ${holdDays === item.val ? "is-active" : ""}`}
                      onClick={() => setHoldDays(item.val)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                className="btn btn-sm"
                style={{ alignSelf: "flex-start" }}
                onClick={() => setShowAdvanced((s) => !s)}
              >
                <SlidersHorizontal size={13} /> ตัวเลือกจำลองขั้นสูง (ปรับเพดาน Lot / Leverage)
                {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>

              {showAdvanced && (
                <div
                  style={{
                    padding: 12,
                    borderRadius: "var(--radius-lg)",
                    border: "1px solid var(--border-subtle)",
                    background: "var(--surface)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 12,
                  }}
                >
                  <label className="checkbox-tile" style={{ background: "var(--surface-muted)" }}>
                    <input type="checkbox" checked={ignoreCaps} onChange={(e) => setIgnoreCaps(e.target.checked)} />
                    <span>จำลองอิสระ (ปลดเพดานจำกัด Leverage/Margin ของบอท เพื่อดูขนาด Lot ตามสูตรเต็มๆ)</span>
                  </label>

                  {!ignoreCaps && (
                    <div className="form-grid">
                      <div className="field">
                        <label>เพดาน Lot สูงสุดเฉพาะรอบนี้</label>
                        <input
                          type="number"
                          step="any"
                          min="0.01"
                          placeholder="ค่าจากตั้งค่าบอท"
                          value={customMaxLot}
                          onChange={(e) => setCustomMaxLot(e.target.value)}
                        />
                      </div>
                      <div className="field">
                        <label>จำกัดแรงดึงเงินพอร์ต (เท่า)</label>
                        <input
                          type="number"
                          step="any"
                          min="1"
                          placeholder="ค่าจากตั้งค่าบอท"
                          value={customMaxLev}
                          onChange={(e) => setCustomMaxLev(e.target.value)}
                        />
                      </div>
                      <div className="field">
                        <label>ใช้มัดจำรวมไม่เกิน (%)</label>
                        <input
                          type="number"
                          step="any"
                          min="1"
                          max="95"
                          placeholder="ค่าจากตั้งค่าบอท"
                          value={customMaxMarginPct}
                          onChange={(e) => setCustomMaxMarginPct(e.target.value)}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div>
              <button className="btn btn-primary" type="submit" disabled={loading}>
                <Calculator size={15} /> {loading ? "กำลังคำนวณจาก MT5..." : "คำนวณขนาด Lot และเปรียบเทียบ"}
              </button>
            </div>
          </form>
        </SectionCard>

        {/* Right: Results + Smart Guidance + Comparison Matrix */}
        <SectionCard
          title="ผลการคำนวณและความปลอดภัย"
          subtitle="คำนวณด้วยสเปกและมัดจำจริงจากโบรคเกอร์พร้อมต้นทุนทุกอย่าง"
          headerRight={
            prev && (
              <StatusBadge tone={prev.approved && plan?.approved ? "success" : "danger"}>
                {prev.approved && plan?.approved ? <CheckCircle2 size={14} /> : <XOctagon size={14} />}
                {prev.approved && plan?.approved ? "ผ่านเกณฑ์พร้อมเทรด" : "ยังไม่ผ่านเงื่อนไข"}
              </StatusBadge>
            )
          }
        >
          {!result ? (
            <p className="text-muted" style={{ margin: 0 }}>
              กำลังดึงข้อมูลราคาจาก MT5 เพื่อคำนวณ...
            </p>
          ) : !result.ok ? (
            <div className="alert-box alert-danger">{translateBotMessage(result.error ?? "")}</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {result.signal && (
                <div className="stat-card" style={{ padding: 14, background: "var(--surface-muted)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                    <span className="stat-card-label">
                      จุดเข้าและระยะ ({result.symbol} {result.signal.side}) • ทุนคำนวณ ${result.equity_used?.toLocaleString()}
                    </span>
                    <span className="stat-card-label tabular">
                      SL {Math.round(result.sl_points_used ?? 0).toLocaleString()} จุด (ขยับ {formatPriceDelta(slPriceDelta)}) • TP{" "}
                      {Math.round(result.tp_points_used ?? 0).toLocaleString()} จุด (ขยับ {formatPriceDelta(tpPriceDelta)})
                    </span>
                  </div>
                  <div className="tabular" style={{ fontSize: 15, fontWeight: 600 }}>
                    เข้า {Number(result.signal.entry_price.toFixed(6))} • SL {Number(result.signal.sl_price.toFixed(6))} • TP{" "}
                    {Number(result.signal.tp_price.toFixed(6))}
                  </div>
                </div>
              )}

              {plan && snap && (
                <div className="sim-stat-grid">
                  <div className="stat-card" style={{ padding: 14, background: "var(--surface-muted)" }}>
                    <span className="stat-card-label">ขนาด Lot ที่ได้ (ก่อนปัด: {plan.raw_lot.toFixed(4)})</span>
                    <div className={`stat-card-val tabular ${plan.approved ? "text-brand" : "text-danger"}`}>
                      {plan.lot.toFixed(2)} Lot
                    </div>
                    <div className="stat-card-hint tabular">
                      ชน SL: <strong className="text-danger">-${plan.estimated_loss_at_sl.toFixed(2)}</strong> • ถึง TP:{" "}
                      <strong className="text-success">+${plan.estimated_profit_at_tp.toFixed(2)}</strong>
                    </div>
                  </div>

                  <div className="stat-card" style={{ padding: 14, background: "var(--surface-muted)" }}>
                    <span className="stat-card-label">มัดจำ (Margin) หลังเปิดไม้</span>
                    <div className="stat-card-val tabular">${snap.projected_margin_used.toFixed(2)}</div>
                    <div className="stat-card-hint tabular">
                      Margin Level: {snap.projected_margin_level_pct !== null ? `${snap.projected_margin_level_pct.toFixed(0)}%` : "สูงมาก"} • ดึงเงินพอร์ต{" "}
                      {snap.projected_effective_portfolio_leverage.toFixed(2)}x
                    </div>
                  </div>
                </div>
              )}

              {/* Translated Rejection Reasons + Actionable 1-Click Guidance */}
              {plan && plan.rejection_reasons.length > 0 && (
                <div className="alert-box alert-danger">
                  <XOctagon size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>เหตุผลที่ไม่ผ่านเกณฑ์คำนวณ Lot:</strong>
                    <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                      {plan.rejection_reasons.map((r, i) => (
                        <li key={i}>{translateBotMessage(r)}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {minLotGuidance && (
                <div className="sim-guidance-box">
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 9 }}>
                    <Lightbulb size={17} className="text-brand" style={{ flexShrink: 0, marginTop: 2 }} />
                    <div>
                      <strong>ทางแก้ที่แนะนำ (คลิกเดียวเพื่อปรับแล้วคำนวณใหม่ทันที):</strong>
                      <div style={{ marginTop: 4 }}>{minLotGuidance.summaryText}</div>
                    </div>
                  </div>
                  <div className="sim-guidance-actions">
                    {minLotGuidance.recommendedSlPoints !== null && (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm tabular"
                        onClick={() => {
                          const nextSl = String(minLotGuidance.recommendedSlPoints);
                          setStopMode("points_rr");
                          setSlPoints(nextSl);
                          setTpPoints("");
                          void runSimulation({ stopMode: "points_rr", slPoints: nextSl, tpPoints: "" }, true);
                        }}
                      >
                        ลดระยะ SL เหลือ {minLotGuidance.recommendedSlPoints.toLocaleString()} จุด (~${minLotGuidance.estimatedSlLossAtRecommendedUsd?.toFixed(2)})
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-sm tabular"
                      onClick={() => {
                        const nextBudget = minLotGuidance.minRiskForOneStepUsd;
                        setSizingMode("risk_money");
                        setRiskAmount(nextBudget);
                        void runSimulation({ sizingMode: "risk_money", riskAmount: nextBudget }, true);
                      }}
                    >
                      คงระยะ SL เดิม แต่ขยับงบเป็น ${minLotGuidance.minRiskForOneStepUsd} (พอดี {minLotGuidance.minLot.toFixed(2)} Lot)
                    </button>
                  </div>
                </div>
              )}

              {costs && (
                <div className="stat-card" style={{ padding: 14, background: "var(--surface-muted)" }}>
                  <span className="stat-card-label">สรุปค่าใช้จ่ายจริงต่อ 1.0 Lot</span>
                  <div className="tabular" style={{ fontSize: 13.5, lineHeight: 1.8 }}>
                    <div>
                      Spread: {costs.spread_points.toFixed(1)} จุด (${costs.spread_cost_per_lot.toFixed(2)})
                    </div>
                    <div>
                      ค่าคอมมิชชัน: ${costs.commission_per_lot.toFixed(2)} • เผื่อราคาคลาดเคลื่อน: ${costs.slippage_cost_per_lot.toFixed(2)}
                    </div>
                    <div>
                      Swap ({costs.swap_days_projected.toFixed(1)} วัน{costs.triple_swap_applied ? " รวมคืนคิด 3 เท่า" : ""}):{" "}
                      <span className={costs.swap_pnl_per_lot < 0 ? "text-warning" : "text-success"} style={{ fontWeight: 600 }}>
                        ${costs.swap_pnl_per_lot.toFixed(2)}
                      </span>
                    </div>
                    <div>
                      <strong>จุดเท่าทุนจริง (รวมค่าใช้จ่าย): {costs.true_breakeven_points.toFixed(1)} จุด</strong> • ความคุ้มค่าสุทธิ{" "}
                      {costs.gross_rr.toFixed(2)} → <strong>{costs.net_rr.toFixed(2)} เท่า</strong>
                    </div>
                  </div>
                </div>
              )}

              {/* Clickable Multi-Risk Scenario Matrix */}
              {result.scenarios && result.scenarios.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <span className="stat-card-label">เปรียบเทียบหลายระดับความเสี่ยง (คลิกแถวเพื่อเลือกใช้งบ % นั้นทันที)</span>
                  <div className="table-wrap" style={{ border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-lg)" }}>
                    <table>
                      <thead>
                        <tr>
                          <th>ความเสี่ยง</th>
                          <th>งบ ($)</th>
                          <th>Lot ที่ได้</th>
                          <th>เสียเมื่อโดน SL</th>
                          <th>ได้เมื่อถึง TP</th>
                          <th>ใช้มัดจำ ($)</th>
                        </tr>
                      </thead>
                      <tbody className="tabular">
                        {result.scenarios.map((sc, idx) => {
                          const isSelected = sizingMode === "risk_percent" && riskPercent === sc.risk_percent;
                          return (
                            <tr
                              key={idx}
                              className={`sim-scenario-row ${isSelected ? "is-selected" : ""}`}
                              onClick={() => {
                                setSizingMode("risk_percent");
                                setRiskPercent(sc.risk_percent);
                                void runSimulation({ sizingMode: "risk_percent", riskPercent: sc.risk_percent }, true);
                              }}
                            >
                              <td>
                                <strong>{sc.risk_percent}%</strong>
                              </td>
                              <td>${sc.risk_budget_money.toFixed(2)}</td>
                              <td className={sc.approved ? "text-brand" : "text-danger"} style={{ fontWeight: 700 }}>
                                {sc.lot.toFixed(2)}
                              </td>
                              <td className="text-danger">-${sc.loss_at_sl.toFixed(2)}</td>
                              <td className="text-success">+${sc.profit_at_tp.toFixed(2)}</td>
                              <td>${sc.margin_used.toFixed(2)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {prev && prev.reasons.length > 0 && (
                <div className="alert-box alert-danger">
                  <XOctagon size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>คำเตือนจากระบบป้องกัน (เงื่อนไขปัจจุบันไม่อนุมัติให้ออกไม้อัตโนมัติ):</strong>
                    <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                      {prev.reasons.map((r, i) => (
                        <li key={i}>{translateBotMessage(r)}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {plan && plan.clamp_reasons.length > 0 && (
                <div className="alert-box alert-warning">
                  <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>ระบบปรับลดขนาด Lot ตามเพดานความปลอดภัย:</strong>
                    <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                      {plan.clamp_reasons.map((c, i) => (
                        <li key={i}>{translateBotMessage(c)}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {prev && prev.warnings.length > 0 && (
                <div className="alert-box alert-warning">
                  <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
                  <div>
                    <strong>ข้อควรระวังล่วงหน้า:</strong>
                    <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                      {prev.warnings.map((w, i) => (
                        <li key={i}>{translateBotMessage(w)}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
