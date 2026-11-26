import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Briefcase,
  Calendar,
  ChartCandlestick,
  Coins,
  Filter,
  History,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  TrendingUp,
  Wallet,
  XCircle,
} from "lucide-react";
import { api } from "../../data/api";
import {
  CALENDAR_PRESETS,
  type CalendarPresetId,
  getLocalTzOffsetMinutes,
  resolveCalendarPreset,
} from "../../domain/dateRange";
import { translateBotMessage } from "../../domain/translator";
import {
  CYCLE_STATUS_LABELS,
  type OpenPosition,
  type OrdersHistoryResponse,
  type OverviewResponse,
} from "../../domain/types";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatCard } from "../components/ui/StatCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  overview: OverviewResponse | null;
  onNotify: (msg: string) => void;
  onReloadOverview: () => void;
  onOpenSymbolChart: (symbol: string) => void;
}

const ORDER_PRESETS: Array<{ id: CalendarPresetId; label_th: string }> = [
  { id: "today", label_th: "วันนี้" },
  ...CALENDAR_PRESETS,
];

const CLOSE_REASON_LABELS: Record<string, { label: string; tone: "success" | "danger" | "warning" | "info" | "neutral" }> = {
  TP: { label: "ชน TP", tone: "success" },
  SL: { label: "โดน SL", tone: "danger" },
  STOP_OUT: { label: "Stop Out", tone: "danger" },
  EXPERT: { label: "บอทปิด (EA)", tone: "info" },
  CLIENT: { label: "ปิดเอง (MT5)", tone: "neutral" },
  MOBILE: { label: "ปิดจากมือถือ", tone: "neutral" },
  WEB: { label: "ปิดจากเว็บ", tone: "neutral" },
  ROLLOVER: { label: "Rollover", tone: "warning" },
  OTHER: { label: "อื่นๆ", tone: "neutral" },
};

function formatThaiDt(epochSec?: number | null): string {
  if (!epochSec) return "-";
  return new Date(epochSec * 1000).toLocaleString("th-TH", {
    timeZone: "Asia/Bangkok",
    day: "2-digit",
    month: "short",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatIsoTime(iso?: string | null): string {
  if (!iso) return "-";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return iso;
  return formatThaiDt(Math.floor(ts / 1000));
}

function formatDurationTh(sec: number): string {
  if (sec < 60) return `${sec} วินาที`;
  const mins = Math.floor(sec / 60);
  if (mins < 60) return `${mins} นาที`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hrs < 24) return remMins > 0 ? `${hrs} ชม. ${remMins} น.` : `${hrs} ชม.`;
  const days = Math.floor(hrs / 24);
  const remHrs = hrs % 24;
  return remHrs > 0 ? `${days} วัน ${remHrs} ชม.` : `${days} วัน`;
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

export function OrdersPage({
  overview,
  onNotify,
  onReloadOverview,
  onOpenSymbolChart,
}: Props) {
  const defaultRange = useMemo(() => resolveCalendarPreset("30d"), []);
  const [startDate, setStartDate] = useState(defaultRange.startDate);
  const [endDate, setEndDate] = useState(defaultRange.endDate);
  const [magicOnly, setMagicOnly] = useState(false);
  const [symbolFilter, setSymbolFilter] = useState("");
  const [journalFilter, setJournalFilter] = useState<
    "ALL" | "ORDER" | "BLOCKED" | "RISK_REJECTED" | "ADJUSTMENT"
  >("ALL");

  const [history, setHistory] = useState<OrdersHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadHistory = useCallback(
    async (silent = false) => {
      if (startDate && endDate && startDate > endDate) {
        setErrorMsg("วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด");
        return;
      }
      if (!silent) setLoading(true);
      try {
        const res = await api.getOrdersHistory({
          start_date: startDate,
          end_date: endDate,
          tz_offset_minutes: getLocalTzOffsetMinutes(),
          magic_only: magicOnly,
          symbol: symbolFilter.trim() || undefined,
        });
        setHistory(res);
        setErrorMsg(null);
      } catch (err) {
        if (!silent) {
          setErrorMsg((err as Error).message || "ดึงประวัติออเดอร์ไม่สำเร็จ");
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [endDate, magicOnly, startDate, symbolFilter]
  );

  useEffect(() => {
    void loadHistory(false);
    const timer = window.setInterval(() => {
      void loadHistory(true);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadHistory]);

  const handleClosePosition = async (pos: OpenPosition) => {
    const isLive = !overview?.bot_state.dry_run;
    if (
      isLive &&
      !window.confirm(
        `ยืนยันปิดออเดอร์ #${pos.ticket} (${pos.symbol} ${pos.side} ${pos.volume.toFixed(2)} Lot) ในพอร์ตจริง?`
      )
    ) {
      return;
    }
    try {
      const res = await api.closePosition(pos.ticket);
      onNotify(res.ok ? `ส่งคำสั่งปิดไม้ #${pos.ticket} แล้ว` : "ไม่สามารถปิดไม้นี้ได้");
      onReloadOverview();
      void loadHistory(true);
    } catch (err) {
      onNotify(`ผิดพลาด: ${(err as Error).message}`);
    }
  };

  const handleMoveBE = async (pos: OpenPosition) => {
    try {
      const res = await api.moveToBreakeven(pos.ticket);
      onNotify(
        res.ok
          ? `เลื่อนจุดตัดขาดทุนไปจุดเท่าทุนจริงให้ไม้ #${pos.ticket} แล้ว`
          : "กำไรยังไม่พ้นระยะค่าใช้จ่ายทั้งหมด"
      );
      onReloadOverview();
      void loadHistory(true);
    } catch (err) {
      onNotify(`ผิดพลาด: ${(err as Error).message}`);
    }
  };

  const currency = history?.currency ?? overview?.account?.currency ?? "USD";
  const bot = overview?.bot_state ?? history?.bot_state;
  const lastCycle = bot?.last_cycle ?? null;

  // Prefer live SSE positions from overview so PnL ticks smoothly every 0.75s
  const livePositions = useMemo(() => {
    const raw = overview?.positions ?? history?.open_positions ?? [];
    const symClean = symbolFilter.trim().toUpperCase();
    return raw.filter((p) => {
      if (magicOnly && !p.owned_by_bot) return false;
      if (symClean && !p.symbol.toUpperCase().includes(symClean)) return false;
      return true;
    });
  }, [history?.open_positions, magicOnly, overview?.positions, symbolFilter]);

  const liveOpenPnl = useMemo(
    () => livePositions.reduce((acc, p) => acc + p.profit + p.swap, 0),
    [livePositions]
  );
  const liveBotOpenCount = useMemo(
    () => livePositions.filter((p) => p.owned_by_bot).length,
    [livePositions]
  );

  const summary = history?.summary;
  const closedTrades = history?.closed_trades ?? [];
  const balanceEvents = history?.balance_events ?? [];
  const journalEntries = useMemo(() => {
    const raw = history?.forward_journal ?? [];
    if (journalFilter === "ALL") return raw;
    return raw.filter((item) => item.event_type === journalFilter);
  }, [history?.forward_journal, journalFilter]);

  return (
    <div className="ops-container">
      <PageHeader
        title="ติดตามสถานะออเดอร์และประวัติเทรดจริง"
        subtitle="ติดตามออเดอร์เปิดสดบน MT5, ประวัติปิดกำไร/ขาดทุนจริงตามช่วงปฏิทิน และบันทึกการสแกน-ออกไม้ของบอทอัตโนมัติ"
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {bot && (
              <StatusBadge tone={bot.running ? "info" : "neutral"}>
                {bot.running
                  ? `บอทกำลังรัน (รอบที่ ${bot.cycle_count})`
                  : "บอทพักอยู่"}
              </StatusBadge>
            )}
            {bot && (
              <StatusBadge tone={bot.dry_run ? "warning" : "danger"}>
                {bot.dry_run ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
                <span>{bot.dry_run ? "โหมดซ้อม (Dry Run)" : "ส่งคำสั่งจริง (LIVE)"}</span>
              </StatusBadge>
            )}
            <button type="button" className="btn btn-sm" onClick={() => void loadHistory(false)} disabled={loading}>
              <RefreshCw size={14} /> รีเฟรช
            </button>
          </div>
        }
      />

      {/* Filter & Calendar Bar */}
      <div className="orders-filter-card">
        <div className="orders-filter-row">
          <div className="orders-filter-group">
            <span className="orders-filter-label">
              <Calendar size={14} /> ช่วงวันที่ดูประวัติ:
            </span>
            <input
              type="date"
              className="input-inline date-input-inline tabular"
              value={startDate}
              aria-label="วันที่เริ่มต้นประวัติออเดอร์"
              onChange={(e) => setStartDate(e.target.value)}
            />
            <span className="text-muted">ถึง</span>
            <input
              type="date"
              className="input-inline date-input-inline tabular"
              value={endDate}
              aria-label="วันที่สิ้นสุดประวัติออเดอร์"
              onChange={(e) => setEndDate(e.target.value)}
            />
            <button type="button" className="btn btn-primary btn-sm" onClick={() => void loadHistory(false)}>
              ดึงข้อมูล
            </button>
          </div>

          <div className="orders-filter-group">
            <span className="orders-filter-label">
              <Filter size={14} /> กรอง:
            </span>
            <div className="tv-tf-group">
              <button
                type="button"
                className={`tv-tf-btn ${!magicOnly ? "is-active" : ""}`}
                onClick={() => setMagicOnly(false)}
              >
                ทั้งหมด (บอท + มือ)
              </button>
              <button
                type="button"
                className={`tv-tf-btn ${magicOnly ? "is-active" : ""}`}
                onClick={() => setMagicOnly(true)}
              >
                เฉพาะบอท (#{history?.bot_magic_number ?? overview?.bot_state ? history?.bot_magic_number ?? "-" : "-"})
              </button>
            </div>
            <input
              type="text"
              className="input-inline"
              style={{ width: 120, padding: "5px 10px", fontSize: "0.84rem" }}
              value={symbolFilter}
              placeholder="กรองสินค้า..."
              onChange={(e) => setSymbolFilter(e.target.value)}
            />
          </div>
        </div>

        <div className="chip-row" style={{ marginTop: 8 }}>
          {ORDER_PRESETS.map((preset) => {
            const p = resolveCalendarPreset(preset.id);
            const active = p.startDate === startDate && p.endDate === endDate;
            return (
              <button
                key={preset.id}
                type="button"
                className={`chip-btn ${active ? "is-active" : ""}`}
                onClick={() => {
                  const next = resolveCalendarPreset(preset.id);
                  setStartDate(next.startDate);
                  setEndDate(next.endDate);
                }}
              >
                {preset.label_th}
              </button>
            );
          })}
        </div>

        {errorMsg && (
          <p className="text-danger" style={{ marginTop: 8, marginBottom: 0 }}>
            {errorMsg}
          </p>
        )}
      </div>

      {/* 4 Live & Realized KPI Cards */}
      <div className="grid-4" style={{ marginTop: 16 }}>
        <StatCard
          label="ออเดอร์เปิดอยู่ตอนนี้ (สด)"
          value={`${livePositions.length} ไม้`}
          hint={`ลอยตัว ${formatMoney(liveOpenPnl, currency, true)} • บอท ${liveBotOpenCount} / เอง ${
            livePositions.length - liveBotOpenCount
          }`}
          icon={Briefcase}
          valueTone={livePositions.length === 0 ? "default" : liveOpenPnl >= 0 ? "success" : "danger"}
        />
        <StatCard
          label="กำไร/ขาดทุนปิดจริงสุทธิ"
          value={formatMoney(summary?.net_realized_pnl ?? 0, currency, true)}
          hint={`ปิดแล้ว ${summary?.closed_count ?? 0} ไม้ • Com ${formatMoney(
            summary?.total_commission ?? 0,
            currency,
            true
          )} • Swap ${formatMoney(summary?.total_swap ?? 0, currency, true)}`}
          icon={TrendingUp}
          valueTone={(summary?.net_realized_pnl ?? 0) >= 0 ? "success" : "danger"}
        />
        <StatCard
          label="อัตราชนะจริง (Win Rate)"
          value={`${(summary?.win_rate_pct ?? 0).toFixed(1)}%`}
          hint={`ชนะ ${summary?.wins ?? 0} / แพ้ ${summary?.losses ?? 0} • PF ${
            summary?.profit_factor === null || summary?.profit_factor === undefined
              ? (summary?.wins ?? 0) > 0
                ? "∞"
                : "0.00"
              : summary.profit_factor.toFixed(2)
          }`}
          icon={Activity}
          valueTone={(summary?.win_rate_pct ?? 0) >= 50 ? "success" : "default"}
        />
        <StatCard
          label="ยอดพอร์ต MT5 ปัจจุบัน"
          value={
            overview?.account
              ? formatMoney(overview.account.equity, currency)
              : history?.account_equity !== null && history?.account_equity !== undefined
              ? formatMoney(history.account_equity, currency)
              : "—"
          }
          hint={`Balance ${formatMoney(
            overview?.account?.balance ?? history?.account_balance ?? 0,
            currency
          )} • Lot ปิดรวม ${(summary?.total_volume ?? 0).toFixed(2)}`}
          icon={Wallet}
          valueTone="brand"
        />
      </div>

      {/* Section 1: Live Open Positions + Latest Scan Status */}
      <SectionCard
        title={`1. สถานะออเดอร์ที่เปิดค้างอยู่บน MT5 (${livePositions.length} รายการ)`}
        subtitle="อัปเดตราคา กำไร/ขาดทุน และจุดกันหน้าทุน (Breakeven/Trailing) แบบเรียลไทม์ทุกวินาที"
        icon={<Coins size={18} className="text-brand" />}
        headerRight={
          bot && (
            <StatusBadge tone="info" className="tabular">
              กลยุทธ์: {bot.strategy_name} ({bot.timeframe})
            </StatusBadge>
          )
        }
      >
        {bot && (
          <div className="live-cycle-strip tabular">
            <div>
              <strong>สถานะรอบล่าสุด (#{bot.cycle_count}):</strong>{" "}
              <span>
                {lastCycle
                  ? CYCLE_STATUS_LABELS[lastCycle.status] ?? lastCycle.status
                  : "ยังไม่เคยกดสแกนในเซสชันนี้"}
              </span>
              {bot.last_cycle_time && (
                <span className="text-muted"> • อัปเดต {formatIsoTime(bot.last_cycle_time)}</span>
              )}
            </div>
            {lastCycle?.preventive && !lastCycle.preventive.approved && lastCycle.preventive.reasons.length > 0 && (
              <div className="text-warning" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                สาเหตุที่ด่านป้องกันพักไม้ไว้: {translateBotMessage(lastCycle.preventive.reasons[0])}
              </div>
            )}
            {lastCycle?.lot_plan && !lastCycle.lot_plan.approved && lastCycle.lot_plan.rejection_reasons.length > 0 && (
              <div className="text-danger" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                สาเหตุ RiskEngine ปฏิเสธ: {lastCycle.lot_plan.rejection_reasons[0]}
              </div>
            )}
          </div>
        )}

        {livePositions.length === 0 ? (
          <p className="text-muted" style={{ marginTop: 12, marginBottom: 0 }}>
            ขณะนี้ยังไม่มีออเดอร์เปิดค้างอยู่ในพอร์ต MT5
          </p>
        ) : (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>สินค้า</th>
                  <th>ฝั่ง</th>
                  <th>Lot</th>
                  <th>เวลาเปิด (ไทย)</th>
                  <th>ราคาเปิด → ปัจจุบัน</th>
                  <th>SL / TP</th>
                  <th>Swap</th>
                  <th>กำไรสุทธิ</th>
                  <th>จัดการ</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {livePositions.map((pos) => {
                  const netPnl = pos.profit + pos.swap;
                  const isBeLocked =
                    pos.sl > 0 &&
                    ((pos.side === "BUY" && pos.sl >= pos.price_open) ||
                      (pos.side === "SELL" && pos.sl <= pos.price_open));
                  return (
                    <tr key={pos.ticket}>
                      <td>
                        <strong>#{pos.ticket}</strong>{" "}
                        <StatusBadge tone={pos.owned_by_bot ? "info" : "neutral"}>
                          {pos.owned_by_bot ? "บอท" : "เปิดเอง"}
                        </StatusBadge>
                      </td>
                      <td>
                        <strong>{pos.symbol}</strong>
                      </td>
                      <td>
                        <StatusBadge tone={pos.side === "BUY" ? "success" : "danger"}>{pos.side}</StatusBadge>
                      </td>
                      <td>{pos.volume.toFixed(2)}</td>
                      <td>{formatThaiDt(pos.open_time)}</td>
                      <td>
                        {pos.price_open} → <strong>{pos.price_current}</strong>
                      </td>
                      <td>
                        <span>
                          {pos.sl || "-"} / {pos.tp || "-"}
                        </span>
                        {isBeLocked && (
                          <div style={{ marginTop: 2 }}>
                            <StatusBadge tone="success">กันหน้าทุนแล้ว</StatusBadge>
                          </div>
                        )}
                      </td>
                      <td>{pos.swap.toFixed(2)}</td>
                      <td className={netPnl >= 0 ? "text-success fw-600" : "text-danger fw-600"}>
                        {formatMoney(netPnl, currency, true)}
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => onOpenSymbolChart(pos.symbol)}
                          >
                            <ChartCandlestick size={13} /> กราฟ
                          </button>
                          <button type="button" className="btn btn-sm" onClick={() => void handleMoveBE(pos)}>
                            กันทุน
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger-outline"
                            onClick={() => void handleClosePosition(pos)}
                          >
                            <XCircle size={13} /> ปิด
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* Section 2: Real Closed Trade History from MT5 */}
      <SectionCard
        title={`2. ประวัติปิดออเดอร์จริงจากพอร์ต MT5 (${closedTrades.length} รายการ)`}
        subtitle={`จับคู่ดีลเปิด–ปิดจริงจากประวัติบัญชี MT5 ช่วงวันที่ ${startDate} ถึง ${endDate} (รวมค่าคอมมิชชันและ Swap จริง)`}
        icon={<History size={18} className="text-brand" />}
        headerRight={
          summary && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <StatusBadge tone="neutral">
                บอท {summary.bot_trades_count} • มือ {summary.manual_trades_count}
              </StatusBadge>
              <StatusBadge tone={summary.max_drawdown_money > 0 ? "warning" : "neutral"}>
                Max DD จริง: -{formatMoney(summary.max_drawdown_money, currency)}
              </StatusBadge>
            </div>
          )
        }
      >
        {closedTrades.length === 0 ? (
          <p className="text-muted" style={{ margin: 0 }}>
            ยังไม่มีประวัติปิดออเดอร์ในพอร์ต MT5 ช่วงวันที่เลือก — หากบอทยังเปิดโหมดซ้อม (Dry Run) ออเดอร์จำลองจะไปปรากฏในตารางด้านล่างแทน
          </p>
        ) : (
          <div className="table-wrap">
            <table className="backtest-table tabular">
              <thead>
                <tr>
                  <th>Position #</th>
                  <th>ที่มา</th>
                  <th>สินค้า</th>
                  <th>ฝั่ง</th>
                  <th>Lot</th>
                  <th>เวลาเปิด → ปิด (ไทย)</th>
                  <th>ถือไม้</th>
                  <th>ราคาเปิด → ปิด</th>
                  <th>เหตุผลปิด</th>
                  <th>Com + Swap</th>
                  <th>กำไรสุทธิ</th>
                </tr>
              </thead>
              <tbody>
                {closedTrades.map((tr) => {
                  const reasonMeta = CLOSE_REASON_LABELS[tr.close_reason] ?? CLOSE_REASON_LABELS.OTHER;
                  const totalCost = tr.commission + tr.swap + tr.fee;
                  return (
                    <tr key={tr.position_id}>
                      <td>#{tr.position_id}</td>
                      <td>
                        <StatusBadge tone={tr.owned_by_bot ? "info" : "neutral"}>
                          {tr.owned_by_bot ? "บอท" : `มือ (${tr.magic})`}
                        </StatusBadge>
                      </td>
                      <td>
                        <strong>{tr.symbol}</strong>
                      </td>
                      <td>
                        <StatusBadge tone={tr.side === "BUY" ? "success" : "danger"}>{tr.side}</StatusBadge>
                      </td>
                      <td>{tr.volume.toFixed(2)}</td>
                      <td>
                        <div>{formatThaiDt(tr.open_time)}</div>
                        <div className="text-muted" style={{ fontSize: "0.78rem" }}>
                          → {formatThaiDt(tr.close_time)}
                        </div>
                      </td>
                      <td>{formatDurationTh(tr.duration_seconds)}</td>
                      <td>
                        {tr.entry_price} → <strong>{tr.exit_price}</strong>
                      </td>
                      <td>
                        <StatusBadge tone={reasonMeta.tone}>{reasonMeta.label}</StatusBadge>
                      </td>
                      <td>{formatMoney(totalCost, currency, true)}</td>
                      <td className={tr.net_pnl >= 0 ? "text-success fw-600" : "text-danger fw-600"}>
                        {formatMoney(tr.net_pnl, currency, true)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {balanceEvents.length > 0 && (
          <div className="balance-ledger-box tabular">
            <strong>ความเคลื่อนไหวเงินฝาก / ถอนในบัญชี ({balanceEvents.length} รายการ):</strong>
            <div className="balance-ledger-chips">
              {balanceEvents.map((ev) => (
                <span key={ev.ticket} className="balance-ledger-pill">
                  <span>{formatThaiDt(ev.time)}</span>
                  <strong className={ev.amount >= 0 ? "text-success" : "text-danger"}>
                    {formatMoney(ev.amount, currency, true)}
                  </strong>
                  {ev.comment && <span className="text-muted">({ev.comment})</span>}
                </span>
              ))}
            </div>
          </div>
        )}
      </SectionCard>

      {/* Section 3: Forward Execution & Guard Audit Journal */}
      <SectionCard
        title={`3. บันทึกการสแกน & ส่งคำสั่งจริงของบอท (${journalEntries.length} เหตุการณ์)`}
        subtitle="บันทึกถาวรทุกจังหวะที่บอทพบสัญญาณจริง, ส่งออเดอร์ (ทั้งโหมดจริงและโหมดซ้อม), ถูกด่าน Defensive บล็อก และจังหวะเลื่อน Trailing/BE (logs/forward_journal.jsonl)"
        icon={<Activity size={18} className="text-brand" />}
        headerRight={
          <div className="tv-tf-group">
            {[
              { id: "ALL", label: "ทั้งหมด" },
              { id: "ORDER", label: "สั่งเปิดไม้" },
              { id: "ADJUSTMENT", label: "เลื่อน SL" },
              { id: "BLOCKED", label: "โดนบล็อก" },
              { id: "RISK_REJECTED", label: "Risk ปฏิเสธ" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`tv-tf-btn ${journalFilter === tab.id ? "is-active" : ""}`}
                onClick={() =>
                  setJournalFilter(
                    tab.id as "ALL" | "ORDER" | "BLOCKED" | "RISK_REJECTED" | "ADJUSTMENT"
                  )
                }
              >
                {tab.label}
              </button>
            ))}
          </div>
        }
      >
        {journalEntries.length === 0 ? (
          <p className="text-muted" style={{ margin: 0 }}>
            ยังไม่มีบันทึกสัญญาณหรือการส่งคำสั่งในช่วงวันที่นี้ — เมื่อบอททำงาน (กด "เริ่มทำงาน" หรือ "ทดลอง 1 รอบ") และพบสัญญาณเข้าเงื่อนไข เหตุการณ์ทั้งหมดจะถูกบันทึกไว้ที่นี่
          </p>
        ) : (
          <div className="table-wrap">
            <table className="backtest-table tabular">
              <thead>
                <tr>
                  <th>เวลา (ไทย)</th>
                  <th>ประเภท / โหมด</th>
                  <th>สินค้า / กลยุทธ์</th>
                  <th>ฝั่ง & ราคา (SL / TP)</th>
                  <th>Lot / Margin</th>
                  <th>ผลลัพธ์ / รายละเอียด</th>
                </tr>
              </thead>
              <tbody>
                {journalEntries.map((ev, idx) => {
                  const statusTone =
                    ev.status === "executed"
                      ? "success"
                      : ev.status === "dry_run_ok" || ev.event_type === "ADJUSTMENT"
                      ? "info"
                      : ev.event_type === "BLOCKED"
                      ? "warning"
                      : "danger";
                  const statusText =
                    ev.status === "executed"
                      ? `เปิดจริง #${ev.order_ticket ?? ""}`
                      : ev.status === "dry_run_ok"
                      ? "ซ้อมผ่าน (DryRun)"
                      : ev.event_type === "ADJUSTMENT"
                      ? `${ev.action_name ?? "เลื่อน SL"} #${ev.ticket ?? ""}`
                      : ev.event_type === "BLOCKED"
                      ? "ด่านป้องกันบล็อก"
                      : ev.event_type === "RISK_REJECTED"
                      ? "RiskEngine ปฏิเสธ"
                      : "ส่งคำสั่งไม่สำเร็จ";

                  return (
                    <tr key={`${ev.time}-${ev.cycle}-${idx}`}>
                      <td>
                        <div>{formatThaiDt(ev.time)}</div>
                        <div className="text-muted" style={{ fontSize: "0.75rem" }}>
                          รอบที่ #{ev.cycle}
                        </div>
                      </td>
                      <td>
                        <StatusBadge tone={statusTone}>{statusText}</StatusBadge>
                        <div className="text-muted" style={{ fontSize: "0.75rem", marginTop: 2 }}>
                          {ev.dry_run ? "โหมดซ้อม" : "พอร์ตจริง"}
                        </div>
                      </td>
                      <td>
                        <strong>{ev.symbol}</strong> ({ev.timeframe})
                        <div className="text-muted" style={{ fontSize: "0.76rem" }}>
                          {ev.strategy_name}
                        </div>
                      </td>
                      <td>
                        {ev.event_type === "ADJUSTMENT" ? (
                          <span>
                            SL {ev.old_sl} → <strong>{ev.new_sl}</strong>
                          </span>
                        ) : ev.side ? (
                          <div>
                            <StatusBadge tone={ev.side === "BUY" ? "success" : "danger"}>{ev.side}</StatusBadge>{" "}
                            <span>@ {ev.executed_price ?? ev.entry_price}</span>
                            <div className="text-muted" style={{ fontSize: "0.76rem" }}>
                              SL {ev.sl_price ?? "-"} / TP {ev.tp_price ?? "-"}
                            </div>
                          </div>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>
                        {ev.lot && ev.lot > 0 ? (
                          <div>
                            <strong>{ev.lot.toFixed(2)} Lot</strong>
                            {ev.margin_used !== null && ev.margin_used !== undefined && (
                              <div className="text-muted" style={{ fontSize: "0.76rem" }}>
                                {formatMoney(ev.margin_used, currency)}
                              </div>
                            )}
                          </div>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td style={{ whiteSpace: "normal", minWidth: 240 }}>
                        <div>{ev.signal_reason ?? ""}</div>
                        {ev.reasons && ev.reasons.length > 0 ? (
                          <div className="text-danger" style={{ fontSize: "0.8rem" }}>
                            {ev.reasons.map((r) => translateBotMessage(r)).join(" • ")}
                          </div>
                        ) : ev.message ? (
                          <div className="text-muted" style={{ fontSize: "0.8rem" }}>
                            {translateBotMessage(ev.message)}
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
