import { useCallback, useEffect, useState } from "react";
import { WifiOff } from "lucide-react";
import { api } from "./data/api";
import type { OverviewResponse, PageKey, SpecMetrics, ThemeMode } from "./domain/types";
import { Header } from "./presentation/components/layout/Header";
import { Sidebar } from "./presentation/components/layout/Sidebar";
import { CalendarPage } from "./presentation/pages/CalendarPage";
import { ChartPage } from "./presentation/pages/ChartPage";
import { LogsPage } from "./presentation/pages/LogsPage";
import { NewsIntelPage } from "./presentation/pages/NewsIntelPage";
import { OrdersPage } from "./presentation/pages/OrdersPage";
import { OverviewPage } from "./presentation/pages/OverviewPage";
import { SettingsPage } from "./presentation/pages/SettingsPage";
import { SimulatorPage } from "./presentation/pages/SimulatorPage";
import { StrategyLabPage } from "./presentation/pages/StrategyLabPage";

interface LiveStreamPayload {
  overview: OverviewResponse;
  watchlist: SpecMetrics[];
  server_time_utc?: string;
}

export function App() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    try {
      return localStorage.getItem("cfd_ops_theme") === "dark" ? "dark" : "light";
    } catch {
      return "light";
    }
  });
  const [activePage, setActivePage] = useState<PageKey>("overview");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [watchlistInput, setWatchlistInput] = useState("XAUUSD, EURUSD, US30, NAS100");
  const [streamSymbols, setStreamSymbols] = useState("XAUUSD, EURUSD, US30, NAS100");
  const [watchlist, setWatchlist] = useState<SpecMetrics[]>([]);
  const [lastTickTime, setLastTickTime] = useState<string | null>(null);
  const [streamLive, setStreamLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [offlineFailures, setOfflineFailures] = useState(0);
  const [chartSymbolOverride, setChartSymbolOverride] = useState<string | null>(null);
  const [chartTimeframeOverride, setChartTimeframeOverride] = useState<string | null>(null);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => {
      setToast((prev) => (prev === msg ? null : prev));
    }, 4000);
  }, []);

  const loadOverview = useCallback(async () => {
    try {
      const data = await api.getOverview();
      setOverview(data);
      setOfflineFailures(0);
    } catch {
      setOfflineFailures((c) => c + 1);
    }
  }, []);

  const refreshWatchlistNow = useCallback(
    async (customInput?: string) => {
      const target = (customInput ?? watchlistInput).trim() || "XAUUSD, EURUSD, US30, NAS100";
      setStreamSymbols(target);
      try {
        const res = await api.getWatchlist(target);
        setWatchlist(res.items);
        setLastTickTime(new Date().toLocaleTimeString("th-TH", { hour12: false }));
      } catch (err) {
        notify(`สแกนรายชื่อสินค้าไม่สำเร็จ: ${(err as Error).message}`);
      }
    },
    [notify, watchlistInput]
  );

  // Sync Light / Dark theme attribute on <html>
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("cfd_ops_theme", theme);
    } catch {
      // ignore storage quota errors
    }
  }, [theme]);

  // Initial immediate snapshot
  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // Real-time Server-Sent Events stream (pushes live MT5 ticks every ~0.75s)
  useEffect(() => {
    const url = `/api/stream?symbols=${encodeURIComponent(streamSymbols)}`;
    const es = new EventSource(url);

    es.onopen = () => {
      setStreamLive(true);
      setOfflineFailures(0);
    };

    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as LiveStreamPayload;
        if (payload.overview) {
          setOverview(payload.overview);
        }
        if (Array.isArray(payload.watchlist)) {
          setWatchlist(payload.watchlist);
        }
        setLastTickTime(new Date().toLocaleTimeString("th-TH", { hour12: false }));
        setStreamLive(true);
        setOfflineFailures(0);
      } catch {
        // ignore malformed chunk
      }
    };

    es.onerror = () => {
      setStreamLive(false);
      setOfflineFailures((c) => c + 1);
    };

    return () => {
      es.close();
      setStreamLive(false);
    };
  }, [streamSymbols]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const res = await api.startBot();
      notify(res.message);
      await loadOverview();
    } catch (err) {
      notify(`เริ่มทำงานไม่ได้: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      await api.stopBot();
      notify("หยุดการทำงานของบอทแล้ว");
      await loadOverview();
    } catch (err) {
      notify(`หยุดไม่ได้: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleEvalOnce = async () => {
    setBusy(true);
    try {
      const res = await api.evaluateOnce();
      notify(res.ok ? "ทดลองสแกนและคำนวณครบ 1 รอบเรียบร้อย" : res.error || "ทดลองไม่สำเร็จ");
      await loadOverview();
    } catch (err) {
      notify(`ผิดพลาด: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const defaultSymbol = overview?.bot_state.symbol || "EURUSD";
  const activeChartSymbol = chartSymbolOverride ?? overview?.active_spec?.spec.name ?? defaultSymbol;
  const activeChartTimeframe = chartTimeframeOverride ?? overview?.bot_state.timeframe ?? "M15";

  const findLiveSpecForSymbol = useCallback(
    (targetSym: string) => {
      const clean = targetSym.trim().toUpperCase();
      if (overview?.active_spec?.spec.name.toUpperCase() === clean) {
        return overview.active_spec.spec;
      }
      const match = watchlist.find(
        (w) =>
          w.found &&
          (w.spec.name.toUpperCase() === clean || w.spec.name.toUpperCase().startsWith(clean))
      );
      if (match?.found) return match.spec;
      if (overview?.active_spec?.spec.name.toUpperCase().startsWith(clean)) {
        return overview.active_spec.spec;
      }
      return null;
    },
    [overview, watchlist]
  );

  return (
    <div className="ops-shell">
      <Sidebar
        activePage={activePage}
        collapsed={sidebarCollapsed}
        mobileOpen={mobileMenuOpen}
        overview={overview}
        onSelectPage={setActivePage}
        onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
        onCloseMobile={() => setMobileMenuOpen(false)}
      />

      <div className="ops-main-col">
        <Header
          activePage={activePage}
          overview={overview}
          theme={theme}
          busy={busy}
          onOpenMobileMenu={() => setMobileMenuOpen(true)}
          onToggleTheme={() => setTheme((t) => (t === "light" ? "dark" : "light"))}
          onStart={handleStart}
          onStop={handleStop}
          onEvalOnce={handleEvalOnce}
          onRefresh={() => void loadOverview()}
        />

        <main className={`ops-scroll-main ${activePage === "chart" ? "is-chart-full" : ""}`}>
          {offlineFailures >= 2 && (
            <div className="ops-container" style={{ marginBottom: 16 }}>
              <div className="alert-box alert-warning">
                <WifiOff size={17} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <strong>กำลังรอเชื่อมต่อหลังบ้าน:</strong> ระบบกำลังลองเชื่อมกับเซิร์ฟเวอร์ใหม่อัตโนมัติ กรุณาตรวจสอบว่าเปิด{" "}
                  <code>python scripts/start_ops.py</code> อยู่หรือไม่
                </div>
              </div>
            </div>
          )}

          {activePage === "overview" && (
            <OverviewPage
              overview={overview}
              watchlist={watchlist}
              watchlistInput={watchlistInput}
              lastTickTime={lastTickTime}
              streamLive={streamLive}
              theme={theme}
              chartSymbol={activeChartSymbol}
              chartTimeframe={activeChartTimeframe}
              liveChartSpec={findLiveSpecForSymbol(activeChartSymbol)}
              onChangeChartSymbol={setChartSymbolOverride}
              onChangeChartTimeframe={setChartTimeframeOverride}
              onOpenFullChart={() => setActivePage("chart")}
              onChangeWatchlistInput={setWatchlistInput}
              onScanWatchlist={() => void refreshWatchlistNow()}
              onNotify={notify}
              onReloadOverview={() => void loadOverview()}
            />
          )}
          {activePage === "orders" && (
            <OrdersPage
              overview={overview}
              onNotify={notify}
              onReloadOverview={() => void loadOverview()}
              onOpenSymbolChart={(sym) => {
                setChartSymbolOverride(sym);
                setActivePage("chart");
              }}
            />
          )}
          {activePage === "chart" && (
            <ChartPage
              symbol={activeChartSymbol}
              timeframe={activeChartTimeframe}
              theme={theme}
              overview={overview}
              liveSpec={findLiveSpecForSymbol(activeChartSymbol)}
              onChangeSymbol={setChartSymbolOverride}
              onChangeTimeframe={setChartTimeframeOverride}
            />
          )}
          {activePage === "simulator" && (
            <SimulatorPage defaultSymbol={chartSymbolOverride ?? defaultSymbol} onNotify={notify} />
          )}
          {activePage === "news_intel" && (
            <NewsIntelPage
              defaultSymbol={defaultSymbol}
              theme={theme}
              openPositions={overview?.positions ?? []}
              findLiveSpec={findLiveSpecForSymbol}
              onNotify={notify}
            />
          )}
          {activePage === "calendar" && <CalendarPage defaultSymbol={defaultSymbol} onNotify={notify} />}
          {activePage === "strategy_lab" && (
            <StrategyLabPage
              defaultSymbol={defaultSymbol}
              defaultTimeframe={overview?.bot_state.timeframe ?? "M15"}
              theme={theme}
              onNotify={notify}
              onStrategyActivated={() => void loadOverview()}
            />
          )}
          {activePage === "settings" && <SettingsPage onNotify={notify} onSaved={() => void loadOverview()} />}
          {activePage === "logs" && <LogsPage />}
        </main>
      </div>

      {toast && <div className="toast-float">{toast}</div>}
    </div>
  );
}

export default App;
