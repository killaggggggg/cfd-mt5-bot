from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, Optional

import pandas as pd

from cfd_bot.domain.models import SymbolSpec, TradeSignal


@dataclass(frozen=True)
class StrategyParamSpec:
    """Declarative metadata for a single strategy-specific offensive parameter."""

    key: str
    label_th: str
    hint_th: str
    kind: Literal["int", "float"]
    default: int | float
    min_val: float
    max_val: float
    step: float = 1.0
    options: Optional[tuple[dict[str, Any], ...]] = None
    example_th: str = ""
    recommended_th: str = ""


class BaseStrategy(ABC):
    """
    Plug-and-play offensive strategy interface decoupled from RiskEngine and PreventiveGuard.

    Developers can create a new file in `domain/strategies/`, subclass `BaseStrategy`,
    implement `generate_signal(...)` + `get_param_specs()`, and register the class in
    `domain/strategies/registry.py` to expose it automatically in the API and UI.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier used in config, logs, and registry."""

    @property
    def display_name_th(self) -> str:
        """Human-readable Thai title for the UI."""
        return self.name

    @property
    def description_th(self) -> str:
        """Plain-Thai summary explaining the offensive logic."""
        return ""

    @property
    def recommended_timeframe(self) -> str:
        """Preferred execution timeframe shown in Strategy Lab and Defensive audit."""
        return "M5"

    @property
    def recommended_history_bars(self) -> int:
        """Minimum closed+forming candle history fetched by BotRunner for signal parity."""
        return 250

    @classmethod
    def get_param_specs(cls) -> list[StrategyParamSpec]:
        """Returns tunable parameter metadata for UI auto-generation."""
        return []

    def get_params(self) -> dict[str, Any]:
        """Returns current parameter values keyed by `StrategyParamSpec.key`."""
        return {spec.key: getattr(self, spec.key, spec.default) for spec in self.get_param_specs()}

    @classmethod
    def from_params(cls, params: Optional[dict[str, Any]] = None) -> "BaseStrategy":
        """Constructs an instance applying validated overrides onto declared defaults."""
        specs = cls.get_param_specs()
        if not specs:
            return cls()
        raw = params or {}
        kwargs: dict[str, Any] = {}
        for spec in specs:
            val = raw.get(spec.key, spec.default)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                val = spec.default
            num = float(val)
            clamped = max(spec.min_val, min(spec.max_val, num))
            kwargs[spec.key] = int(round(clamped)) if spec.kind == "int" else clamped
        return cls(**kwargs)

    def precompute_backtest_context(self, closed_df: pd.DataFrame) -> Optional[dict[str, Any]]:
        """
        Optional O(N) indicator precomputation hook for fast multi-decade backtesting.

        Subclasses implementing this must remain strictly causal at every index `t`.
        Returning None falls back cleanly to bar-by-bar `generate_signal`.
        """
        return None

    def generate_signal_from_context(
        self,
        spec: SymbolSpec,
        ctx: dict[str, Any],
        bar_idx: int,
    ) -> Optional[TradeSignal]:
        """Evaluates bar `bar_idx` (closed index `bar_idx - 1`) using precomputed arrays."""
        return None

    @abstractmethod
    def generate_signal(self, spec: SymbolSpec, rates: pd.DataFrame) -> Optional[TradeSignal]:
        """
        Inspects OHLCV bars and current SymbolSpec, returning a TradeSignal or None.

        Strategies return raw entry/SL/TP geometry only — zero defensive filters
        (no spread, news, rollover, margin, or lot sizing logic).
        """
