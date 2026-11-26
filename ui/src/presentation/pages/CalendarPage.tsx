import { useEffect, useState } from "react";
import { AlertTriangle, CalendarDays, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { api } from "../../data/api";
import type { CalendarResponse } from "../../domain/types";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";
import { StatusBadge } from "../components/ui/StatusBadge";

interface Props {
  defaultSymbol: string;
  onNotify: (msg: string) => void;
}

export function CalendarPage({ defaultSymbol, onNotify }: Props) {
  const [symbol, setSymbol] = useState(defaultSymbol || "XAUUSD");
  const [data, setData] = useState<CalendarResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const [title, setTitle] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [impact, setImpact] = useState("HIGH");
  const [dateStr, setDateStr] = useState("2026-12-25");
  const [major, setMajor] = useState(true);

  const loadCalendar = async (target = symbol) => {
    setLoading(true);
    try {
      const res = await api.getCalendar(target);
      setData(res);
    } catch (err) {
      onNotify(`โหลดปฏิทินข่าวไม่สำเร็จ: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadCalendar();
  }, []);

  const handleAddCustom = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.addCustomEvent({ title, currency, impact, date: dateStr, major });
      setTitle("");
      onNotify("เพิ่มรายการวันหยุดหรือข่าวพิเศษแล้ว");
      await loadCalendar();
    } catch (err) {
      onNotify(`เพิ่มรายการไม่สำเร็จ: ${(err as Error).message}`);
    }
  };

  const handleDeleteCustom = async (idx: number) => {
    try {
      await api.deleteCustomEvent(idx);
      onNotify("ลบรายการเรียบร้อยแล้ว");
      await loadCalendar();
    } catch (err) {
      onNotify(`ลบรายการไม่สำเร็จ: ${(err as Error).message}`);
    }
  };

  return (
    <div className="ops-container">
      <PageHeader
        title="ปฏิทินข่าวเศรษฐกิจและวันหยุดตลาด"
        subtitle="บอทใช้ปฏิทินนี้ช่วยงดเทรดช่วงข่าวแรงหรือวันหยุดใหญ่ที่กราฟผันผวนผิดปกติ"
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="text" className="input-inline" style={{ width: 135 }} value={symbol} onChange={(e) => setSymbol(e.target.value)} />
            <button type="button" className="btn btn-primary" onClick={() => loadCalendar()} disabled={loading}>
              {loading ? "กำลังเช็ก..." : "ตรวจข่าวสินค้านี้"}
            </button>
          </div>
        }
      />

      <SectionCard
        title="ตารางข่าวและวันหยุดที่ตรวจพบ"
        subtitle="ดึงจากฟีดข่าวรายสัปดาห์ ปฏิทินวันหยุดธนาคารทั่วโลก และรายการที่คุณตั้งเพิ่มเอง"
        icon={<CalendarDays size={18} className="text-brand" />}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {data?.check.blocked && (
            <div className="alert-box alert-danger">
              <ShieldAlert size={17} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <strong>ขณะนี้อยู่ในช่วงห้ามเปิดไม้ใหม่:</strong> {data.check.reasons.join(" | ")}
              </div>
            </div>
          )}

          {data && data.check.warnings.length > 0 && (
            <div className="alert-box alert-warning">
              <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <strong>แจ้งเตือนล่วงหน้า:</strong> {data.check.warnings.join(" | ")}
              </div>
            </div>
          )}

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>วัน / เวลา (UTC)</th>
                  <th>สกุลเงิน</th>
                  <th>ความแรง</th>
                  <th>ชื่อข่าวหรือวันหยุด</th>
                  <th>แหล่งข้อมูล</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {!data?.check.upcoming_events.length ? (
                  <tr>
                    <td colSpan={5} className="text-muted">
                      ไม่มีข่าวระดับสูง/กลาง หรือวันหยุดใหญ่สำหรับสินค้านี้ในช่วงที่ตรวจจับ
                    </td>
                  </tr>
                ) : (
                  data.check.upcoming_events.map((ev, i) => (
                    <tr key={i}>
                      <td>{ev.is_all_day_holiday ? ev.local_date : new Date(ev.event_time).toUTCString()}</td>
                      <td>
                        <strong>{ev.currency}</strong>
                      </td>
                      <td>
                        <StatusBadge
                          tone={
                            ev.impact === "HIGH"
                              ? "danger"
                              : ev.impact === "HOLIDAY"
                              ? "warning"
                              : "info"
                          }
                        >
                          {ev.impact === "HIGH" ? "แรง (HIGH)" : ev.impact === "HOLIDAY" ? "วันหยุด" : "กลาง (MED)"}
                        </StatusBadge>
                      </td>
                      <td>{ev.title}</td>
                      <td className="text-muted">{ev.source}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </SectionCard>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <SectionCard title="เพิ่มวันหยุดหรือข่าวพิเศษเอง" subtitle="บันทึกเก็บไว้ให้บอทหลบช่วงเวลาที่คุณไม่อยากเทรด" icon={<Plus size={18} className="text-brand" />}>
          <form onSubmit={handleAddCustom} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="form-grid">
              <div className="field">
                <label>ชื่อเหตุการณ์</label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="เช่น ประชุมดอกเบี้ย หรือ ปิดปรับปรุงเซิร์ฟเวอร์"
                  required
                />
              </div>
              <div className="field">
                <label>สกุลเงินที่เกี่ยวข้อง</label>
                <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
                  <option value="ALL">ALL (มีผลกับทุกสินค้า)</option>
                  <option value="USD">USD</option>
                  <option value="EUR">EUR</option>
                  <option value="GBP">GBP</option>
                  <option value="JPY">JPY</option>
                  <option value="AUD">AUD</option>
                  <option value="CAD">CAD</option>
                </select>
              </div>
              <div className="field">
                <label>ระดับความสำคัญ</label>
                <select value={impact} onChange={(e) => setImpact(e.target.value)}>
                  <option value="HIGH">ข่าวแรง (บล็อกก่อน-หลังข่าว)</option>
                  <option value="HOLIDAY">วันหยุดตลาด (บล็อกทั้งวัน)</option>
                  <option value="MEDIUM">ข่าวระดับกลาง</option>
                </select>
              </div>
              <div className="field">
                <label>วัน/เวลา (YYYY-MM-DD หรือ ISO UTC)</label>
                <input
                  type="text"
                  value={dateStr}
                  onChange={(e) => setDateStr(e.target.value)}
                  placeholder="2026-12-16T19:00:00+00:00"
                  required
                />
              </div>
            </div>

            <label className="checkbox-tile">
              <input type="checkbox" checked={major} onChange={(e) => setMajor(e.target.checked)} />
              <span>ถือเป็นวันหยุดใหญ่ (ให้บอทหยุดเปิดออเดอร์ใหม่ตลอดทั้งวัน)</span>
            </label>

            <div>
              <button className="btn btn-primary" type="submit">
                <Plus size={15} /> บันทึกรายการเพิ่มเติม
              </button>
            </div>
          </form>
        </SectionCard>

        <SectionCard title={`รายการที่คุณเพิ่มไว้ (${data?.custom_events.length ?? 0})`} subtitle="ลบออกได้ตลอดเวลา">
          {!data?.custom_events.length ? (
            <p className="text-muted" style={{ margin: 0 }}>
              ยังไม่มีรายการที่เพิ่มเอง
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ชื่อรายการ</th>
                    <th>สกุลเงิน</th>
                    <th>ประเภท</th>
                    <th>วัน/เวลา</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody className="tabular">
                  {data.custom_events.map((item, idx) => (
                    <tr key={idx}>
                      <td>{item.title}</td>
                      <td>{item.currency}</td>
                      <td>{item.impact}</td>
                      <td>{item.date}</td>
                      <td>
                        <button type="button" className="btn btn-sm btn-danger-outline" onClick={() => handleDeleteCustom(idx)}>
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
