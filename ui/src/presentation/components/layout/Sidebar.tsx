import {
  Activity,
  BarChart3,
  Calculator,
  CalendarDays,
  ChartCandlestick,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Crosshair,
  ScrollText,
  Settings,
  ShieldCheck,
  X,
  type LucideIcon,
} from "lucide-react";
import type { OverviewResponse, PageKey } from "../../../domain/types";

interface NavItem {
  key: PageKey;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    title: "ภาพรวมและคำนวณ",
    items: [
      { key: "overview", label: "ภาพรวมบัญชี", icon: Activity },
      { key: "orders", label: "ติดตามออเดอร์ & ประวัติจริง", icon: ClipboardList },
      { key: "chart", label: "กราฟราคาสด", icon: ChartCandlestick },
      { key: "simulator", label: "ทดลองคำนวณ Lot", icon: Calculator },
    ],
  },
  {
    title: "ข่าวและตลาด",
    items: [
      { key: "news_intel", label: "วิเคราะห์ข่าวและกราฟ", icon: BarChart3 },
      { key: "calendar", label: "ปฏิทินข่าวและวันหยุด", icon: CalendarDays },
    ],
  },
  {
    title: "ตั้งค่าระบบ",
    items: [
      { key: "strategy_lab", label: "กลยุทธ์เทรด (Offensive)", icon: Crosshair },
      { key: "settings", label: "ตั้งค่าบอท", icon: Settings },
    ],
  },
  {
    title: "ตรวจสอบ",
    items: [{ key: "logs", label: "ประวัติการทำงาน", icon: ScrollText }],
  },
];

interface Props {
  activePage: PageKey;
  collapsed: boolean;
  mobileOpen: boolean;
  overview: OverviewResponse | null;
  onSelectPage: (page: PageKey) => void;
  onToggleCollapse: () => void;
  onCloseMobile: () => void;
}

export function Sidebar({
  activePage,
  collapsed,
  mobileOpen,
  overview,
  onSelectPage,
  onToggleCollapse,
  onCloseMobile,
}: Props) {
  const connected = overview?.mt5_connected ?? false;
  const bot = overview?.bot_state;
  const showFull = !collapsed || mobileOpen;

  const handleSelect = (page: PageKey) => {
    onSelectPage(page);
    onCloseMobile();
  };

  return (
    <>
      <div className={`drawer-backdrop ${mobileOpen ? "is-open" : ""}`} onClick={onCloseMobile} />
      <aside className={`ops-sidebar ${collapsed && !mobileOpen ? "is-collapsed" : ""} ${mobileOpen ? "is-mobile-open" : ""}`}>
        <div className="ops-sidebar-brand">
          {showFull && (
            <div className="ops-brand-link">
              <div className="ops-brand-logo">
                <ShieldCheck size={18} />
              </div>
              <div className="ops-brand-text">
                <span className="ops-brand-name">CFD MT5 Guard</span>
                <span className="ops-brand-sub">ศูนย์ควบคุมบอทเทรด</span>
              </div>
            </div>
          )}

          <button
            type="button"
            className="btn btn-icon desktop-only"
            onClick={onToggleCollapse}
            title={collapsed ? "ขยายเมนู" : "ย่อเมนู"}
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>

          <button type="button" className="btn btn-icon mobile-only" onClick={onCloseMobile} title="ปิดเมนู">
            <X size={16} />
          </button>
        </div>

        <nav className="ops-nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.title}>
              {showFull && <div className="ops-nav-group-label">{group.title}</div>}
              <div className="ops-nav-list">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = activePage === item.key;
                  return (
                    <button
                      key={item.key}
                      type="button"
                      className={`ops-nav-item ${active ? "is-active" : ""}`}
                      onClick={() => handleSelect(item.key)}
                      title={!showFull ? item.label : undefined}
                    >
                      <Icon size={18} />
                      {showFull && <span>{item.label}</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {showFull && (
          <div className="ops-sidebar-footer">
            <div className="ops-sidebar-chip">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 }}>
                <span className="text-muted">การเชื่อมต่อ MT5</span>
                <span className={connected ? "text-success" : "text-danger"} style={{ fontWeight: 600 }}>
                  {connected ? "พร้อมใช้งาน" : "ไม่ได้เชื่อมต่อ"}
                </span>
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-700)" }} className="tabular">
                {bot ? `${bot.symbol} • ${bot.timeframe}` : "กำลังตรวจสอบ..."}
              </div>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
