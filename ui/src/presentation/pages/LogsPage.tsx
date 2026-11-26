import { useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { api } from "../../data/api";
import type { LogEntry } from "../../domain/types";
import { PageHeader } from "../components/ui/PageHeader";
import { SectionCard } from "../components/ui/SectionCard";

export function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [levelFilter, setLevelFilter] = useState("ALL");

  const loadLogs = async () => {
    try {
      const res = await api.getLogs(150);
      setLogs(res.logs);
    } catch {
      // ignore polling error
    }
  };

  useEffect(() => {
    void loadLogs();
    const timer = setInterval(() => void loadLogs(), 4000);
    return () => clearInterval(timer);
  }, []);

  const filtered = logs.filter((l) => levelFilter === "ALL" || l.level === levelFilter);

  return (
    <div className="ops-container">
      <PageHeader
        title="ประวัติการทำงานของบอท"
        subtitle="อัปเดตอัตโนมัติทุก 4 วินาที เพื่อเช็กเหตุผลทุกคำสั่งและคำเตือนของระบบ"
        actions={
          <div style={{ display: "flex", gap: 8 }}>
            <select className="input-inline" style={{ width: 140 }} value={levelFilter} onChange={(e) => setLevelFilter(e.target.value)}>
              <option value="ALL">ทั้งหมด (ALL)</option>
              <option value="INFO">ปกติ (INFO)</option>
              <option value="WARNING">แจ้งเตือน (WARN)</option>
              <option value="ERROR">ข้อผิดพลาด (ERR)</option>
            </select>
            <button type="button" className="btn" onClick={loadLogs}>
              <RefreshCw size={14} /> รีเฟรช
            </button>
          </div>
        }
      />

      <SectionCard
        title="บันทึกเหตุการณ์ล่าสุด"
        subtitle={`แสดง ${filtered.length} รายการล่าสุดจาก logs/cfd_bot.log`}
        icon={<ScrollText size={18} className="text-brand" />}
      >
        <div className="log-terminal">
          {!filtered.length ? (
            <div className="text-muted">ยังไม่มีประวัติการทำงานในระดับที่เลือก</div>
          ) : (
            filtered
              .slice()
              .reverse()
              .map((line, i) => {
                const levelColor =
                  line.level === "ERROR" || line.level === "CRITICAL"
                    ? "var(--danger-700)"
                    : line.level === "WARNING"
                    ? "var(--warning-700)"
                    : "var(--success-700)";
                return (
                  <div key={i} className="log-row tabular">
                    <span className="text-muted" style={{ flexShrink: 0 }}>
                      {line.time}
                    </span>
                    <span
                      style={{
                        flexShrink: 0,
                        width: 78,
                        fontWeight: 600,
                        color: levelColor,
                      }}
                    >
                      [{line.level}]
                    </span>
                    <span>{line.message}</span>
                  </div>
                );
              })
          )}
        </div>
      </SectionCard>
    </div>
  );
}
