import { useState } from "react";
import {
  Activity,
  ChartCandlestick,
  Coins,
  Eye,
  EyeOff,
  Gauge,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  Wallet,
  XCircle,
} from "lucide-react";
import { api } from "../../data/api";
import { translateBotMessage } from "../../domain/translator";
import {
  CYCLE_STATUS_LABELS,
  DOW_LABELS,
  LOT_MODE_LABELS,
  SWAP_MODE_LABELS,
  type OpenPosition,
  type OverviewResponse,
  type SpecMetrics,
  type SymbolSpec,
  type ThemeMode,
} from "../../domain/types";
import { TradingChart } from "../components/chart/TradingChart";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatCard } from "../components/ui/StatCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  overview: OverviewResponse | null;
  watchlist: SpecMetrics[];
  watchlistInput: string;
  lastTickTime: string | null;
  streamLive: boolean;
  theme: ThemeMode;
  chartSymbol: string;
  chartTimeframe: string;
  liveChartSpec: SymbolSpec | null;
  onChangeChartSymbol: (nextSymbol: string) => void;
  onChangeChartTimeframe: (nextTf: string) => void;
  onOpenFullChart: () => void;
  onChangeWatchlistInput: (value: string) => void;
  onScanWatchlist: () => void;
  onNotify: (msg: string) => void;
  onReloadOverview: () => void;
}

export function OverviewPage({
  overview,
  watchlist,
  watchlistInput,
  lastTickTime,
  streamLive,
  theme,
  chartSymbol,
  chartTimeframe,
  liveChartSpec,
  onChangeChartSymbol,
  onChangeChartTimeframe,
  onOpenFullChart,
  onChangeWatchlistInput,
  onScanWatchlist,
  onNotify,
  onReloadOverview,
}: Props) {
  const [switchingKey, setSwitchingKey] = useState<string | null>(null);
  const [showChartCard, setShowChartCard] = useState(true);

  const handleQuickSwitch = async (nextSymbol?: string, nextTimeframe?: string) => {
    const cleanSym = (nextSymbol ?? bot?.symbol ?? "EURUSD").trim();
    const cleanTf = (nextTimeframe ?? bot?.timeframe ?? "M15").trim();
    setSwitchingKey(`${cleanSym}:${cleanTf}`);
    try {
      await api.saveConfig({ symbol: cleanSym, timeframe: cleanTf });
      onChangeChartSymbol(cleanSym);
      onChangeChartTimeframe(cleanTf);
      onNotify(`เปลี่ยนสินค้าหลักเป็น ${cleanSym} (${cleanTf}) แล้ว`);
      onReloadOverview();
    } catch (err) {
      onNotify(`เปลี่ยนสินค้าไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSwitchingKey(null);
    }
  };

  const handleClosePosition = async (pos: OpenPosition) => {
    const isLive = !overview?.bot_state.dry_run;
    if (isLive && !window.confirm(`ยืนยันปิดออเดอร์ #${pos.ticket} (${pos.symbol} ${pos.side} ${pos.volume} Lot) ในพอร์ตจริง?`)) {
      return;
    }
    try {
      const res = await api.closePosition(pos.ticket);
      onNotify(res.ok ? `ส่งคำสั่งปิดไม้ #${pos.ticket} แล้ว` : "ไม่สามารถปิดไม้นี้ได้");
      onReloadOverview();
    } catch (err) {
      onNotify(`ผิดพลาด: ${(err as Error).message}`);
    }
  };

  const handleMoveBE = async (pos: OpenPosition) => {
    try {
      const res = await api.moveToBreakeven(pos.ticket);
      onNotify(res.ok ? `เลื่อนจุดตัดขาดทุนไปจุดเท่าทุนจริงให้ไม้ #${pos.ticket} แล้ว` : "กำไรยังไม่พ้นระยะค่าใช้จ่ายทั้งหมด");
      onReloadOverview();
    } catch (err) {
      onNotify(`ผิดพลาด: ${(err as Error).message}`);
    }
  };

  const active = overview?.active_spec;
  const spec = active?.spec;
  const bot = overview?.bot_state;
  const acc = overview?.account;
  const lastCycle = bot?.last_cycle;
  const activeSymbolName = spec?.name ?? bot?.symbol ?? "EURUSD";
  const symbolOptions = Array.from(
    new Set([
      activeSymbolName,
      ...watchlist.filter((w) => w.found).map((w) => w.spec.name),
      "EURUSD",
      "XAUUSD",
      "US30",
      "NAS100",
      "GBPUSD",
      "USDJPY",
    ])
  );

  return (
    <div className="ops-container">
      <PageHeader
        title="ภาพรวมบัญชีและตลาด"
        subtitle="ติดตามยอดเงินจริง ค่าใช้จ่ายบนโบรกเกอร์ และเปลี่ยนสินค้าหลักได้ทันทีจากหน้านี้"
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--ink-500)", fontWeight: 600 }}>
              <span>สินค้า:</span>
              <select
                className="input-inline"
                style={{ width: 126, padding: "6px 10px", fontSize: 13, fontWeight: 600 }}
                value={activeSymbolName}
                disabled={switchingKey !== null}
                onChange={(e) => void handleQuickSwitch(e.target.value, bot?.timeframe)}
                title="เลือกสินค้าหลักที่ต้องการให้บอทดูและติดตาม"
              >
                {symbolOptions.map((sym) => (
                  <option key={sym} value={sym}>
                    {sym}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--ink-500)", fontWeight: 600 }}>
              <span>กราฟ:</span>
              <select
                className="input-inline"
                style={{ width: 78, padding: "6px 10px", fontSize: 13, fontWeight: 600 }}
                value={bot?.timeframe ?? "M15"}
                disabled={switchingKey !== null}
                onChange={(e) => void handleQuickSwitch(activeSymbolName, e.target.value)}
                title="เลือกกรอบเวลา (Timeframe)"
              >
                {["M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                  </option>
                ))}
              </select>
            </label>
            {streamLive && lastTickTime && (
              <StatusBadge tone="success" className="tabular">
                <span className="live-dot" /> อัปเดตสด {lastTickTime}
              </StatusBadge>
            )}
            {bot && (
              <StatusBadge tone={bot.running ? "info" : "neutral"}>
                {bot.running ? `บอททำงานอยู่ (รอบที่ ${bot.cycle_count})` : "บอทพักอยู่"}
              </StatusBadge>
            )}
          </div>
        }
      />

      <div className="grid-4">
        <StatCard
          label={`สินค้าหลัก (${bot?.timeframe ?? "M15"})`}
          value={spec?.name ?? bot?.symbol ?? "—"}
          hint={
            bot
              ? `${spec ? `ขั้นต่ำ ${spec.volume_min} Lot • ` : ""}${LOT_MODE_LABELS[bot.lot_mode] ?? bot.lot_mode}`
              : "กำลังโหลด..."
          }
          icon={Activity}
          valueTone="brand"
        />

        <StatCard
          label="ราคาตลาดและส่วนต่าง (Spread)"
          value={spec ? `${spec.bid.toFixed(spec.digits)} / ${spec.ask.toFixed(spec.digits)}` : "—"}
          hint={
            spec
              ? `Spread ${spec.spread_points} จุด (${active?.spread_price.toFixed(spec.digits) ?? "—"})`
              : "รอข้อมูลราคาจาก MT5"
          }
          icon={Coins}
        />

        <StatCard
          label="แรงดึงเงินจริงของสินค้านี้"
          value={active ? `1:${active.effective_symbol_leverage.toFixed(1)}` : "—"}
          hint={
            active && acc && spec
              ? active.margin_per_lot !== null
                ? `มัดจำ 1 Lot: $${active.margin_per_lot.toFixed(2)} • ${spec.volume_min} Lot: $${(active.margin_per_lot * spec.volume_min).toFixed(2)}`
                : `พอร์ต 1:${acc.leverage}`
              : "คำนวณจากมัดจำจริงบน MT5"
          }
          icon={Gauge}
        />

        <StatCard
          label="ขาดทุนสะสมวันนี้"
          value={bot ? `${bot.daily_drawdown_pct.toFixed(2)}%` : "—"}
          hint={
            bot
              ? bot.max_daily_loss_pct !== null && bot.max_daily_loss_pct > 0
                ? `เพดานหยุดพักอัตโนมัติไม่เกิน ${bot.max_daily_loss_pct.toFixed(1)}% ต่อวัน`
                : "ไม่ได้จำกัดขาดทุนรายวัน (เว้นว่างไว้)"
              : "ตรวจสอบอัตโนมัติ"
          }
          icon={Wallet}
          valueTone={
            bot &&
            bot.max_daily_loss_pct !== null &&
            bot.max_daily_loss_pct > 0 &&
            bot.daily_drawdown_pct >= bot.max_daily_loss_pct
              ? "danger"
              : "default"
          }
        />
      </div>

      <SectionCard
        title={`กราฟราคาสด (${chartSymbol} • ${chartTimeframe})`}
        subtitle="แท่งเทียนจริงจาก MT5 พร้อม EMA 50/200, เส้น SL/TP และหมุดข่าว — กด 'เต็มจอ' เพื่อเปิดหน้ากราฟแบบ TradingView เต็มรูปแบบ"
        icon={<ChartCandlestick size={18} className="text-brand" />}
        headerRight={
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setShowChartCard((s) => !s)}
          >
            {showChartCard ? <EyeOff size={13} /> : <Eye size={13} />}
            {showChartCard ? "ซ่อนกราฟ" : "แสดงกราฟ"}
          </button>
        }
      >
        {showChartCard ? (
          <TradingChart
            symbol={chartSymbol}
            timeframe={chartTimeframe}
            theme={theme}
            height={380}
            liveSpec={liveChartSpec}
            openPositions={overview?.positions ?? []}
            onChangeSymbol={onChangeChartSymbol}
            onChangeTimeframe={onChangeChartTimeframe}
            onExpandFull={onOpenFullChart}
          />
        ) : (
          <p className="text-muted" style={{ margin: 0 }}>
            ซ่อนกราฟอยู่ชั่วคราว — กดปุ่ม <strong>"แสดงกราฟ"</strong> ด้านขวาบนเพื่อเปิดดูอีกครั้ง
          </p>
        )}
      </SectionCard>

      <SectionCard
        title="ผลการเช็กสัญญาณรอบล่าสุด"
        subtitle="สรุปว่าบอทเจอโอกาสเทรดหรือไม่ และผ่านด่านตรวจสอบความปลอดภัยครบทุกข้อหรือยัง"
        icon={<TrendingUp size={18} className="text-brand" />}
        headerRight={
          <StatusBadge tone="neutral" className="tabular">
            {bot?.last_cycle_time
              ? `เช็กล่าสุด ${new Date(bot.last_cycle_time).toLocaleTimeString("th-TH", { hour12: false })}`
              : "ยังไม่เคยเช็กสัญญาณ"}
          </StatusBadge>
        }
      >
        {!lastCycle ? (
          <p className="text-muted" style={{ margin: 0 }}>
            กดปุ่ม <strong>"ทดลอง 1 รอบ"</strong> หรือ <strong>"เริ่มทำงาน"</strong> ด้านบนขวา เพื่อให้บอทลองอ่านกราฟและแสดงผลการตรวจเช็กตรงนี้
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <StatusBadge tone={lastCycle.status === "executed" || lastCycle.status === "dry_run_ok" ? "success" : "info"} className="tabular is-wrap">
                {CYCLE_STATUS_LABELS[lastCycle.status] ?? lastCycle.status}
              </StatusBadge>
              {lastCycle.signal && (
                <StatusBadge tone={lastCycle.signal.side === "BUY" ? "success" : "danger"} className="tabular is-wrap">
                  {lastCycle.signal.side === "BUY" ? "สัญญาณซื้อ (BUY)" : "สัญญาณขาย (SELL)"} ที่ราคา{" "}
                  {Number(lastCycle.signal.entry_price.toFixed(6))} • SL {Number(lastCycle.signal.sl_price.toFixed(6))} • TP{" "}
                  {Number(lastCycle.signal.tp_price.toFixed(6))}
                </StatusBadge>
              )}
              {lastCycle.lot_plan && (
                <StatusBadge tone="neutral" className="tabular is-wrap">
                  ขนาดไม้แนะนำ: {lastCycle.lot_plan.lot} Lot (ความคุ้มค่าสุทธิ Net R:R{" "}
                  {lastCycle.preventive?.costs.net_rr.toFixed(2) ?? "—"})
                </StatusBadge>
              )}
            </div>
            {lastCycle.signal?.reason && (
              <div className="text-muted">เหตุผลของสัญญาณ: {translateBotMessage(lastCycle.signal.reason)}</div>
            )}
            {lastCycle.preventive && !lastCycle.preventive.approved && (
              <div className="alert-box alert-danger">
                <div>
                  <strong>ระบบป้องกันไม่อนุญาตให้เข้าออเดอร์:</strong>{" "}
                  {lastCycle.preventive.reasons.map(translateBotMessage).join(" | ")}
                </div>
              </div>
            )}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title={`ออเดอร์ที่เปิดอยู่บน MT5 (${overview?.positions.length ?? 0})`}
        subtitle="เลื่อนจุดตัดขาดทุนมาที่จุดเท่าทุนจริง (รวมค่าคอมและค่าคลาดเคลื่อนแล้ว) หรือสั่งปิดไม้ได้ทันที"
        icon={<ShieldCheck size={18} className="text-brand" />}
      >
        {!overview?.positions.length ? (
          <p className="text-muted" style={{ margin: 0 }}>
            ยังไม่มีออเดอร์เปิดค้างอยู่ในบัญชีขณะนี้
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>เลขไม้ (Ticket)</th>
                  <th>สินค้า</th>
                  <th>ฝั่ง</th>
                  <th>Lot</th>
                  <th>ราคาเปิด</th>
                  <th>ราคาปัจจุบัน</th>
                  <th>SL / TP</th>
                  <th>Swap</th>
                  <th>กำไร/ขาดทุน</th>
                  <th>จัดการ</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {overview.positions.map((pos) => (
                  <tr key={pos.ticket}>
                    <td>
                      #{pos.ticket}{" "}
                      <StatusBadge tone={pos.owned_by_bot ? "info" : "neutral"}>
                        {pos.owned_by_bot ? "บอทเปิด" : "เปิดเอง"}
                      </StatusBadge>
                    </td>
                    <td>
                      <strong>{pos.symbol}</strong>
                    </td>
                    <td>
                      <StatusBadge tone={pos.side === "BUY" ? "success" : "danger"}>{pos.side}</StatusBadge>
                    </td>
                    <td>{pos.volume.toFixed(2)}</td>
                    <td>{pos.price_open}</td>
                    <td>{pos.price_current}</td>
                    <td>
                      {pos.sl} / {pos.tp}
                    </td>
                    <td>{pos.swap.toFixed(2)}</td>
                    <td className={pos.profit >= 0 ? "text-success" : "text-danger"} style={{ fontWeight: 600 }}>
                      {pos.profit >= 0 ? "+" : ""}
                      {pos.profit.toFixed(2)}
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            onChangeChartSymbol(pos.symbol);
                            setShowChartCard(true);
                          }}
                          title="เปิดกราฟดูออเดอร์นี้"
                        >
                          <ChartCandlestick size={13} /> ดูบนกราฟ
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => handleMoveBE(pos)}>
                          กันทุนจริง
                        </button>
                        <button type="button" className="btn btn-sm btn-danger-outline" onClick={() => handleClosePosition(pos)}>
                          <XCircle size={13} /> ปิดไม้
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="ตารางสแกนค่าจริงจากโบรกเกอร์ (อัปเดตสด)"
        subtitle="ตรวจเช็กราคา มัดจำขั้นต่ำ Spread เลเวอเรจจริง และค่าถือข้ามคืน (Swap) แบบเรียลไทม์"
        headerRight={
          <div className="watchlist-controls">
            <input
              type="text"
              className="input-inline watchlist-input"
              value={watchlistInput}
              onChange={(e) => onChangeWatchlistInput(e.target.value)}
              placeholder="XAUUSD, EURUSD, US30, NAS100"
            />
            <button type="button" className="btn btn-primary" onClick={onScanWatchlist}>
              <RefreshCw size={14} /> สแกนทันที
            </button>
          </div>
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ชื่อสินค้าบนพอร์ต</th>
                <th>Bid / Ask</th>
                <th>Spread</th>
                <th>Lot ขั้นต่ำ / ขยับทีละ</th>
                <th>มัดจำต่อ 1 Lot</th>
                <th>เลเวอเรจจริง</th>
                <th>Swap ซื้อ / ขาย</th>
                <th>วันคิด Swap 3 เท่า</th>
                <th>สลับใช้งาน</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {!watchlist.length ? (
                <tr>
                  <td colSpan={9} className="text-muted">
                    กำลังดึงข้อมูลสินค้าเรียลไทม์จาก MT5...
                  </td>
                </tr>
              ) : (
                watchlist.map((item, idx) =>
                  !item.found ? (
                    <tr key={idx}>
                      <td>
                        <strong>{item.requested}</strong>
                      </td>
                      <td colSpan={8} className="text-muted">
                        ไม่พบสินค้านี้ใน Market Watch ของโบรกเกอร์
                      </td>
                    </tr>
                  ) : (
                    <tr key={item.spec.name}>
                      <td>
                        <strong>{item.spec.name}</strong>
                        <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                          {SWAP_MODE_LABELS[item.spec.swap_mode] ?? `รูปแบบ ${item.spec.swap_mode}`}
                        </div>
                      </td>
                      <td>
                        {item.spec.bid.toFixed(item.spec.digits)} / {item.spec.ask.toFixed(item.spec.digits)}
                      </td>
                      <td>
                        {item.spec.spread_points} จุด ({item.spread_price.toFixed(item.spec.digits)})
                      </td>
                      <td>
                        {item.spec.volume_min} / {item.spec.volume_step}
                      </td>
                      <td>
                        {item.margin_per_lot !== null ? (
                          <div>
                            <strong>
                              ${item.margin_per_lot.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                            </strong>
                            <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                              ขั้นต่ำ {item.spec.volume_min} = $
                              {(item.margin_per_lot * item.spec.volume_min).toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 2,
                              })}
                            </div>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="text-success" style={{ fontWeight: 600 }}>
                        1:{item.effective_symbol_leverage.toFixed(1)}
                      </td>
                      <td>
                        {item.spec.swap_long} / {item.spec.swap_short}
                      </td>
                      <td>{DOW_LABELS[item.spec.swap_rollover3days] ?? item.spec.swap_rollover3days}</td>
                      <td>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => {
                              onChangeChartSymbol(item.spec.name);
                              setShowChartCard(true);
                            }}
                            title="เปิดดูกราฟแท่งเทียนของสินค้านี้"
                          >
                            <ChartCandlestick size={13} /> กราฟ
                          </button>
                          {item.spec.name === activeSymbolName ? (
                            <StatusBadge tone="info">สินค้าหลัก</StatusBadge>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-sm"
                              disabled={switchingKey !== null}
                              onClick={() => void handleQuickSwitch(item.spec.name, bot?.timeframe)}
                            >
                              เลือกใช้
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                )
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  );
}
