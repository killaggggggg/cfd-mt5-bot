import type { OverviewResponse, SymbolSpec, ThemeMode } from "../../domain/types";
import { TradingChart } from "../components/chart/TradingChart";

interface Props {
  symbol: string;
  timeframe: string;
  theme: ThemeMode;
  overview: OverviewResponse | null;
  liveSpec: SymbolSpec | null;
  onChangeSymbol: (nextSymbol: string) => void;
  onChangeTimeframe: (nextTf: string) => void;
}

export function ChartPage({
  symbol,
  timeframe,
  theme,
  overview,
  liveSpec,
  onChangeSymbol,
  onChangeTimeframe,
}: Props) {
  return (
    <div className="ops-chart-fullview">
      <TradingChart
        symbol={symbol}
        timeframe={timeframe}
        theme={theme}
        fillHeight
        liveSpec={liveSpec}
        openPositions={overview?.positions ?? []}
        showQuickSymbols
        onChangeSymbol={onChangeSymbol}
        onChangeTimeframe={onChangeTimeframe}
      />
    </div>
  );
}
