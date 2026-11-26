import { Menu, Moon, Play, RefreshCw, ShieldAlert, ShieldCheck, Square, Sun, Zap } from "lucide-react";
import { PAGE_LABELS, type OverviewResponse, type PageKey, type ThemeMode } from "../../../domain/types";
import { StatusBadge } from "../ui/StatusBadge";

interface Props {
  activePage: PageKey;
  overview: OverviewResponse | null;
  theme: ThemeMode;
  busy: boolean;
  onOpenMobileMenu: () => void;
  onToggleTheme: () => void;
  onStart: () => void;
  onStop: () => void;
  onEvalOnce: () => void;
  onRefresh: () => void;
}

export function Header({
  activePage,
  overview,
  theme,
  busy,
  onOpenMobileMenu,
  onToggleTheme,
  onStart,
  onStop,
  onEvalOnce,
  onRefresh,
}: Props) {
  const connected = overview?.mt5_connected ?? false;
  const account = overview?.account;
  const bot = overview?.bot_state;

  return (
    <header className="ops-header">
      <div className="ops-header-left">
        <button type="button" className="btn btn-icon mobile-only" onClick={onOpenMobileMenu} title="เปิดเมนู">
          <Menu size={17} />
        </button>
        <span className="ops-header-title">{PAGE_LABELS[activePage]}</span>
        {bot && (
          <StatusBadge
            tone={bot.dry_run ? "warning" : "danger"}
            title={bot.dry_run ? "โหมดซ้อม — ยังไม่ส่งคำสั่งเงินจริงเข้าโบรกเกอร์" : "โหมดเงินจริง (LIVE)"}
          >
            {bot.dry_run ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
            <span>{bot.dry_run ? "โหมดซ้อม" : "พอร์ตจริง"}</span>
          </StatusBadge>
        )}
      </div>

      <div className="ops-header-right">
        <StatusBadge tone={connected ? "success" : "danger"}>
          {connected ? `MT5 #${account?.login ?? ""}` : "MT5 ออฟไลน์"}
        </StatusBadge>

        {account && (
          <StatusBadge tone="neutral" className="tabular hide-below-1100">
            พอร์ต ${account.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </StatusBadge>
        )}

        <button type="button" className="btn btn-sm" onClick={onEvalOnce} disabled={busy}>
          <Zap size={14} /> ทดลอง 1 รอบ
        </button>

        {bot?.running ? (
          <button type="button" className="btn btn-sm btn-danger" onClick={onStop} disabled={busy}>
            <Square size={14} /> หยุดบอท
          </button>
        ) : (
          <button type="button" className="btn btn-sm btn-primary" onClick={onStart} disabled={busy}>
            <Play size={14} /> เริ่มทำงาน
          </button>
        )}

        <button
          type="button"
          className="btn btn-icon"
          onClick={onToggleTheme}
          title={theme === "light" ? "สลับเป็นธีมมืด" : "สลับเป็นธีมสว่าง"}
        >
          {theme === "light" ? <Moon size={15} /> : <Sun size={15} />}
        </button>

        <button type="button" className="btn btn-icon" onClick={onRefresh} title="อัปเดตข้อมูลล่าสุด">
          <RefreshCw size={15} />
        </button>
      </div>
    </header>
  );
}
