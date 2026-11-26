import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  ChartCandlestick,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Link2,
  RefreshCw,
  Save,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { api } from "../../data/api";
import {
  formatThaiDateTime,
  formatUtcTimeShort,
  translateBotMessage,
  translateCandleDir,
} from "../../domain/translator";
import {
  BIAS_META,
  VERDICT_META,
  type EventImpactAnalysis,
  type NewsIntelligenceResponse,
  type SymbolSpec,
  type ThemeMode,
} from "../../domain/types";
import { TradingChart } from "../components/chart/TradingChart";
import { FieldWithHint, type HelpHintContent } from "../components/ui/HelpHint";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatCard } from "../components/ui/StatCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  defaultSymbol: string;
  theme: ThemeMode;
  openPositions?: import("../../domain/types").OpenPosition[];
  findLiveSpec?: (sym: string) => SymbolSpec | null;
  onNotify: (msg: string) => void;
}

const NEWS_FORM_HINTS: Record<string, HelpHintContent> = {
  title: {
    summary: "ชื่อข่าวเศรษฐกิจที่ใช้จับคู่กฎวิเคราะห์ (เช่น US CPI, Non-Farm Payrolls, Core PCE, Unemployment Rate)",
    example: "คลิกปุ่ม 'กรอก/แก้ไข' ในตารางด้านบนเพื่อดึงชื่อและเวลาจริงของข่าวมาใส่อัตโนมัติ",
  },
  currency: {
    summary: "สกุลเงินของประเทศที่ประกาศข่าวนี้ เพื่อเทียบผลบวก/ลบกับคู่เงิน ทองคำ และดัชนีหุ้น",
    example: "ข่าว USD แข็งค่ามักกดดัน EURUSD และทองคำ (XAUUSD) แต่ถ้ารุนแรงอาจกดดันหุ้นสหรัฐฯ",
  },
  event_time: {
    summary: "เวลาที่ข่าวออก (รูปแบบ UTC ISO) เพื่อให้บอทไปดึงแท่งเทียน M5/M15 ใน MT5 ก่อนและหลังเวลานั้นมาวัดอาการกราฟ",
    example: "ใช้ปุ่มลัด 'เมื่อ 20 นาทีที่แล้ว' เพื่อทดสอบดูอาการกราฟล่าสุดได้ทันทีโดยไม่ต้องคำนวณเวลาเอง",
  },
  actual: {
    summary: "ตัวเลขจริงที่ประกาศออกมา — ระบบจะเอาไปเทียบกับตัวเลขคาดการณ์ (Forecast) เพื่อดูว่าดีหรือแย่กว่าที่ตลาดคาด",
    example: "CPI คาดการณ์ 0.3% แต่ออกจริง 0.5% = เงินเฟ้อสูงกว่าคาด ดอลลาร์มักมีแรงหนุนแข็งค่า",
  },
  forecast: {
    summary: "ตัวเลขที่นักวิเคราะห์คาดการณ์ไว้ก่อนข่าวออก (หัวใจสำคัญในการวัด 'เซอร์ไพรส์' ของตลาด)",
    example: "ถ้าเว้นว่าง ระบบจะเทียบกับตัวเลขครั้งก่อน (Previous) แทนให้อัตโนมัติ",
  },
  previous: {
    summary: "ตัวเลขของรอบเดือน/ไตรมาสก่อนหน้า ใช้เป็นตัวอ้างอิงเพิ่มเติม",
  },
};

function matchesSymbolPrefix(activeSymbol: string, cardSymbol: string): boolean {
  const cleanActive = activeSymbol.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const cleanCard = cardSymbol.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return cleanActive.startsWith(cleanCard) || cleanCard.startsWith(cleanActive);
}

export function NewsIntelPage({ defaultSymbol, theme, openPositions, findLiveSpec, onNotify }: Props) {
  const [symbol, setSymbol] = useState(defaultSymbol || "EURUSD");
  const [chartTf, setChartTf] = useState("M5");
  const [data, setData] = useState<NewsIntelligenceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [showBridgeGuide, setShowBridgeGuide] = useState(false);

  const [title, setTitle] = useState("Core PCE Price Index m/m");
  const [currency, setCurrency] = useState("USD");
  const [eventTime, setEventTime] = useState(new Date(Date.now() - 20 * 60_000).toISOString());
  const [actualVal, setActualVal] = useState("");
  const [forecastVal, setForecastVal] = useState("0.3%");
  const [previousVal, setPreviousVal] = useState("0.3%");
  const [savingActual, setSavingActual] = useState(false);

  const formRef = useRef<HTMLDivElement | null>(null);

  const loadIntel = async (target = symbol) => {
    setLoading(true);
    try {
      const res = await api.getNewsIntelligence(target);
      setData(res);
    } catch (err) {
      onNotify(`โหลดผลวิเคราะห์ข่าวไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadIntel();
  }, []);

  const handleSelectSymbol = (nextSymbol: string) => {
    setSymbol(nextSymbol);
    void loadIntel(nextSymbol);
  };

  const scrollToFormWithMessage = (msg: string) => {
    onNotify(msg);
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handlePrefillRow = (ev: EventImpactAnalysis) => {
    setTitle(ev.title);
    setCurrency(ev.currency);
    setEventTime(ev.event_time);
    setForecastVal(ev.forecast ?? "");
    setPreviousVal(ev.previous ?? "");
    setActualVal(ev.actual ?? "");
    scrollToFormWithMessage(`ดึงข้อมูล "${ev.title}" มาใส่ฟอร์มด้านล่างแล้ว กรอกช่อง Actual แล้วกดบันทึกได้เลย`);
  };

  const handlePrefillExample = () => {
    setTitle("Core PCE Price Index m/m");
    setCurrency("USD");
    setEventTime(new Date(Date.now() - 20 * 60_000).toISOString());
    setForecastVal("0.3%");
    setPreviousVal("0.3%");
    setActualVal("0.5%");
    scrollToFormWithMessage("ใส่ตัวอย่างข่าว USD เงินเฟ้อสูงกว่าคาด (20 นาทีที่แล้ว) แล้ว — กด 'บันทึกตัวเลขและวิเคราะห์ทันที' ได้เลย");
  };

  const handleSaveActual = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!actualVal.trim()) {
      onNotify("กรุณากรอกตัวเลขที่ประกาศจริง (Actual) ก่อนบันทึกครับ");
      return;
    }
    setSavingActual(true);
    try {
      await api.saveNewsActual({
        title,
        currency,
        event_time: eventTime,
        actual: actualVal,
        forecast: forecastVal || null,
        previous: previousVal || null,
        impact: "HIGH",
      });
      onNotify(`บันทึกตัวเลขจริงของ ${title} และคำนวณทิศทางใหม่แล้ว`);
      await loadIntel();
    } catch (err) {
      onNotify(`บันทึกไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setSavingActual(false);
    }
  };

  const reaction = data?.post_news_reaction;
  const bridge = data?.bridge_status;
  const aggMeta = BIAS_META[data?.aggregate_bias ?? "UNKNOWN"];

  return (
    <div className="ops-container">
      <PageHeader
        title="วิเคราะห์ข่าวและอาการกราฟหลังข่าวออก"
        subtitle="เทียบตัวเลขจริงกับที่ตลาดคาดการณ์ พร้อมจับอาการแท่งเทียน M5/M15 หลังข่าวออก 5–30 นาที ฟรี 100% ไม่ต้องใช้ AI เสียเงิน"
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="text-muted" style={{ fontSize: 12.5, fontWeight: 600 }}>
              สินค้า:
            </span>
            <input
              type="text"
              className="input-inline"
              style={{ width: 130 }}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
            <button type="button" className="btn btn-primary" onClick={() => loadIntel()} disabled={loading}>
              <RefreshCw size={14} /> {loading ? "กำลังวิเคราะห์..." : "วิเคราะห์สินค้านี้"}
            </button>
          </div>
        }
      />

      {/* MT5 Bridge Status Banner */}
      <div className={`alert-box is-wrap ${bridge?.detected ? "alert-success" : "alert-info"}`}>
        <Link2 size={18} style={{ flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: "1 1 240px", minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>
            ตัวดึงตัวเลขข่าวจาก MT5 อัตโนมัติ (MT5 Bridge):{" "}
            {bridge?.detected
              ? `เชื่อมต่ออยู่ (${bridge.event_count} รายการ)`
              : "ใช้ข้อมูลตารางข่าวมาตรฐานอยู่ — ยังไม่ได้เปิดดึงตัวเลขจริงอัตโนมัติจาก MT5"}
          </div>
          <div style={{ fontSize: 12.5, opacity: 0.92, marginTop: 3 }}>
            {bridge?.detected ? (
              <span className="tabular">
                อัปเดตล่าสุด: {formatThaiDateTime(bridge.updated_utc)} ({formatUtcTimeShort(bridge.updated_utc)})
              </span>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
                <span>ตารางวัน-เวลาข่าวใช้งานได้ปกติ ส่วนตัวเลขประกาศจริง (Actual) กรอกทดลองเองด้านล่างหรือกดเปิด Service ใน MT5</span>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => setShowBridgeGuide((s) => !s)}
                >
                  วิธีเปิดใน MT5 {showBridgeGuide ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                </button>
              </div>
            )}
          </div>

          {!bridge?.detected && showBridgeGuide && (
            <div
              style={{
                marginTop: 8,
                padding: "8px 12px",
                borderRadius: "var(--radius-md)",
                background: "var(--surface)",
                border: "1px solid var(--border-subtle)",
                color: "var(--ink-700)",
                fontSize: 12.5,
                lineHeight: 1.6,
              }}
            >
              <div><strong>1.</strong> เปิดโปรแกรม MetaTrader 5 แล้วกด <code>Ctrl + N</code> เพื่อเปิดแถบ <strong>Navigator</strong></div>
              <div><strong>2.</strong> คลิกขวาที่หัวข้อ <strong>Services</strong> → กด <strong>Refresh</strong></div>
              <div><strong>3.</strong> แตกโฟลเดอร์ Services แล้วคลิกขวา <strong>CFDCalendarBridge</strong> → กด <strong>Start</strong> จากนั้นกลับมากด "วิเคราะห์สินค้านี้"</div>
            </div>
          )}
        </div>

        <StatusBadge tone={aggMeta.tone} className="tabular is-wrap">
          สรุปข่าว {data?.symbol ?? symbol}: {data ? `${data.aggregate_score >= 0 ? "+" : ""}${data.aggregate_score}` : "0"} ({aggMeta.label})
        </StatusBadge>
      </div>

      {data?.warnings && data.warnings.length > 0 && (
        <div className="alert-box alert-warning">
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>
            <strong>หมายเหตุข้อมูลปฏิทิน:</strong> {data.warnings.map(translateBotMessage).join(" | ")}
          </div>
        </div>
      )}

      {/* Interactive 4-CFD Impact Matrix */}
      <div className="grid-4">
        {(data?.cfd_matrix ?? []).map((item) => {
          const meta = BIAS_META[item.bias];
          const isActive = matchesSymbolPrefix(data?.symbol ?? symbol, item.symbol);
          return (
            <button
              key={item.symbol}
              type="button"
              className={`stat-card cfd-matrix-card ${isActive ? "is-active" : ""}`}
              onClick={() => handleSelectSymbol(item.symbol)}
            >
              <div className="stat-card-top">
                <span style={{ fontWeight: 700, fontSize: 15, color: "var(--ink-900)" }}>{item.symbol}</span>
                <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
              </div>
              <div className="stat-card-val tabular">
                {item.score > 0 ? `+${item.score}` : item.score}
              </div>
              <div className="stat-card-hint">
                จากข่าวที่มีตัวเลขจริง {item.events_counted} รายการ (36 ชม. ล่าสุด) • คลิกเพื่อดูรายละเอียดคู่นี้
              </div>
            </button>
          );
        })}
      </div>

      {/* Post-News M5/M15 Candlestick Reaction */}
      <SectionCard
        title="อาการกราฟหลังข่าวออก (แท่งเทียน M5 / M15)"
        subtitle="ช่วยแยกแยะว่ากราฟกำลังวิ่งตามข่าวจริง หรือกำลังดีดหลอกกลับทาง (หักมุม / กวาดไส้)"
        icon={<BarChart3 size={18} className="text-brand" />}
        headerRight={
          reaction && <StatusBadge tone={VERDICT_META[reaction.verdict].tone}>{VERDICT_META[reaction.verdict].label}</StatusBadge>
        }
      >
        {!reaction ? (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <p className="text-muted" style={{ margin: 0, flex: "1 1 320px" }}>
              ยังไม่มีข่าวแรงที่เพิ่งออกในช่วงไม่กี่ชั่วโมงล่าสุด — ระบบส่วนนี้จะทำงานอัตโนมัติหลังข่าวสีแดง/ส้มประกาศ หรือกดปุ่มด้านขวาเพื่อดูตัวอย่างจากกราฟจริงช่วง 20 นาทีที่ผ่านมา
            </p>
            <button type="button" className="btn btn-sm" onClick={handlePrefillExample}>
              <Sparkles size={14} /> ใส่ตัวอย่างทดสอบในฟอร์มด้านล่าง
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div
              className={`alert-box ${
                reaction.verdict.startsWith("TREND_CONTINUATION")
                  ? "alert-success"
                  : reaction.verdict.startsWith("REVERSAL_TRAP") || reaction.verdict === "WAIT_SETTLE"
                  ? "alert-warning"
                  : "alert-info"
              }`}
            >
              {reaction.verdict.includes("BULLISH") ? (
                <TrendingUp size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              ) : reaction.verdict.includes("BEARISH") ? (
                <TrendingDown size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              ) : (
                <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              )}
              <div>
                <div style={{ fontWeight: 600 }}>
                  {reaction.event_title} ({reaction.event_currency}) — หลังข่าวออกแล้ว {reaction.minutes_since_release.toFixed(0)} นาที
                </div>
                <div>{reaction.summary_th}</div>
              </div>
            </div>

            <div className="grid-4">
              <StatCard
                label="ความชัดเจนของอาการกราฟ"
                value={`${reaction.confidence_score.toFixed(0)}%`}
                hint={
                  reaction.aligned_with_news === null
                    ? "ยังไม่มีตัวเลขจริงเทียบกับข่าว"
                    : reaction.aligned_with_news
                    ? "ราคาวิ่งสอดคล้องกับผลข่าว"
                    : "ราคาวิ่งสวนทางกับผลข่าว"
                }
                icon={CheckCircle2}
              />
              <StatCard
                label="ระยะวิ่งสุทธิหลังข่าว"
                value={`${reaction.net_move_points >= 0 ? "+" : ""}${reaction.net_move_points.toFixed(1)} จุด`}
                hint={`แรงกว่าค่าเฉลี่ยปกติ ${reaction.impulse_atr_ratio.toFixed(2)} เท่า`}
                icon={TrendingUp}
              />
              <StatCard
                label="ทิศทางแท่ง M5 / M15"
                value={`${translateCandleDir(reaction.m5_direction)} / ${translateCandleDir(reaction.m15_direction)}`}
                hint={`ดันขึ้นสุด ${reaction.max_up_atr_ratio.toFixed(2)}x • ลงสุด ${reaction.max_down_atr_ratio.toFixed(2)}x`}
                icon={BarChart3}
              />
              <StatCard
                label="อาการกวาดไส้และ Spread"
                value={reaction.spread_normalized ? "Spread ปกติ" : "Spread ยังถ่าง"}
                hint={reaction.wick_trap_detected ? "ตรวจพบไส้เทียนกวาดหลอก" : "ยังไม่พบไส้หลอกรุนแรง"}
                icon={AlertTriangle}
                valueTone={reaction.wick_trap_detected || !reaction.spread_normalized ? "warning" : "success"}
              />
            </div>
          </div>
        )}
      </SectionCard>

      {/* Post-News Live Chart matching M5/M15 Analysis */}
      <SectionCard
        title={`กราฟแท่งเทียนประกอบการดูอาการหลังข่าว (${data?.symbol ?? symbol} • ${chartTf})`}
        subtitle="หมุดสีแดง/ส้มบนแท่งเทียนแสดงจุดเวลาที่ข่าวออก เพื่อเทียบอาการพุ่งหรือกวาดไส้จริงกับสถิติด้านบน"
        icon={<ChartCandlestick size={18} className="text-brand" />}
      >
        <TradingChart
          symbol={data?.symbol ?? symbol}
          timeframe={chartTf}
          theme={theme}
          height={340}
          liveSpec={findLiveSpec ? findLiveSpec(data?.symbol ?? symbol) : null}
          openPositions={openPositions}
          defaultShowNews
          onChangeTimeframe={setChartTf}
        />
      </SectionCard>

      {/* Full-Width Scorecard Table with Thai Local Time (UTC+7) */}
      <SectionCard
        title="ตารางคะแนนข่าว (ตัวเลขจริง vs คาดการณ์)"
        subtitle="แสดงเวลาไทย (UTC+7) ให้อ่านง่าย — กดปุ่ม 'กรอก/แก้ไข' เพื่อป้อนตัวเลขข่าวจริงหรือทดลองจำลองผลกระทบ"
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ชื่อข่าวและเวลาไทย (UTC+7)</th>
                <th>สกุลเงิน</th>
                <th>จริง / คาดการณ์ / ครั้งก่อน</th>
                <th>ผลต่อ {data?.symbol ?? symbol}</th>
                <th>คำอธิบายเข้าใจง่าย</th>
                <th>จัดการ</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {!(data?.events.length ?? 0) ? (
                <tr>
                  <td colSpan={6} className="text-muted">
                    ไม่พบข่าวระดับสูงหรือกลางในช่วง -72 ถึง +48 ชั่วโมง
                  </td>
                </tr>
              ) : (
                data!.events.map((ev, idx) => {
                  const meta = BIAS_META[ev.symbol_bias];
                  return (
                    <tr key={idx}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{ev.title}</div>
                        <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                          {formatThaiDateTime(ev.event_time)} •{" "}
                          <span style={{ opacity: 0.85 }}>{formatUtcTimeShort(ev.event_time)}</span>
                        </div>
                      </td>
                      <td>
                        <strong>{ev.currency}</strong>
                      </td>
                      <td>
                        <strong>{ev.actual ?? "—"}</strong> / {ev.forecast ?? "—"} / {ev.previous ?? "—"}
                      </td>
                      <td>
                        <StatusBadge tone={meta.tone}>
                          {ev.symbol_score > 0 ? `+${ev.symbol_score}` : ev.symbol_score} • {meta.label}
                        </StatusBadge>
                      </td>
                      <td style={{ whiteSpace: "normal", minWidth: 220 }}>{ev.explanation_th}</td>
                      <td>
                        <button type="button" className="btn btn-sm" onClick={() => handlePrefillRow(ev)}>
                          กรอก/แก้ไข
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* Manual Actual Test Form */}
      <div ref={formRef}>
        <SectionCard
          title="กรอกตัวเลขข่าวจริงด้วยตัวเอง (หรือจำลองล่วงหน้า)"
          subtitle="ใส่ตัวเลขที่เพิ่งประกาศ (หรือสมมติค่า) แล้วดูว่าระบบให้คะแนนบวกหรือลบ และแท่งกราฟวิ่งตรงตามข่าวหรือไม่"
          icon={<CheckCircle2 size={18} className="text-brand" />}
        >
          <form onSubmit={handleSaveActual} noValidate style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="form-grid">
              <FieldWithHint
                label="ชื่อข่าว"
                hint={NEWS_FORM_HINTS.title}
                smallText="ชื่อข่าวภาษาอังกฤษมาตรฐานหรือเลือกจากตารางด้านบน"
              >
                <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} required />
              </FieldWithHint>

              <FieldWithHint
                label="สกุลเงินของข่าว"
                hint={NEWS_FORM_HINTS.currency}
                smallText="ประเทศที่ประกาศตัวเลขข่าวนี้"
              >
                <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
                  {["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </FieldWithHint>

              <FieldWithHint
                label="เวลาประกาศข่าว (ISO UTC)"
                hint={NEWS_FORM_HINTS.event_time}
                smallText={`ตรงกับเวลาไทย: ${formatThaiDateTime(eventTime)}`}
              >
                <input type="text" value={eventTime} onChange={(e) => setEventTime(e.target.value)} required />
              </FieldWithHint>

              <FieldWithHint
                label="ตัวเลขที่ประกาศจริง (Actual)"
                hint={NEWS_FORM_HINTS.actual}
                smallText="ใส่ตัวเลขพร้อมหน่วย เช่น 0.5%, 220K, 51.2"
              >
                <input
                  type="text"
                  value={actualVal}
                  onChange={(e) => setActualVal(e.target.value)}
                  placeholder="เช่น 0.5% หรือ 220K"
                  required
                />
              </FieldWithHint>

              <FieldWithHint
                label="ตัวเลขที่คาดการณ์ (Forecast)"
                hint={NEWS_FORM_HINTS.forecast}
                smallText="ใช้เทียบว่าข่าวจริงดีหรือแย่กว่าที่ตลาดคาด"
              >
                <input
                  type="text"
                  value={forecastVal}
                  onChange={(e) => setForecastVal(e.target.value)}
                  placeholder="เช่น 0.3%"
                />
              </FieldWithHint>

              <FieldWithHint
                label="ตัวเลขครั้งก่อน (Previous)"
                hint={NEWS_FORM_HINTS.previous}
                smallText="ค่าที่ประกาศในงวดก่อนหน้า"
              >
                <input
                  type="text"
                  value={previousVal}
                  onChange={(e) => setPreviousVal(e.target.value)}
                  placeholder="เช่น 0.3%"
                />
              </FieldWithHint>
            </div>

            <div className="chip-row" style={{ alignItems: "center" }}>
              <span className="text-muted tabular" style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
                <Clock size={13} /> ตั้งเวลาข่าวด่วน:
              </span>
              {[
                { label: "เมื่อ 15 นาทีที่แล้ว", offsetMin: -15 },
                { label: "เมื่อ 20 นาทีที่แล้ว", offsetMin: -20 },
                { label: "เมื่อ 1 ชม. ที่แล้ว", offsetMin: -60 },
                { label: "ตอนนี้", offsetMin: 0 },
              ].map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  className="chip-btn"
                  onClick={() => setEventTime(new Date(Date.now() + preset.offsetMin * 60_000).toISOString())}
                >
                  {preset.label}
                </button>
              ))}
            </div>

            <div>
              <button className="btn btn-primary" type="submit" disabled={savingActual}>
                <Save size={15} /> {savingActual ? "กำลังคำนวณ..." : "บันทึกตัวเลขและวิเคราะห์ทันที"}
              </button>
            </div>
          </form>
        </SectionCard>
      </div>
    </div>
  );
}
