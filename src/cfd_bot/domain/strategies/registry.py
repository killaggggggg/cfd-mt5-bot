from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from cfd_bot.domain.strategies.base import BaseStrategy
from cfd_bot.domain.strategies.m5_kernel_pullback import M5KernelPullbackStrategy
from cfd_bot.domain.strategies.m5_range_scalper import M5RangeScalperStrategy
from cfd_bot.domain.strategies.volatility_squeeze_breakout import VolatilitySqueezeBreakoutStrategy

# Plug-and-play registry of active offensive strategies exposed to BotRunner & UI.
STRATEGY_CLASSES: dict[str, type[BaseStrategy]] = {
    "M5KernelPullback": M5KernelPullbackStrategy,
    "VolatilitySqueezeBreakout": VolatilitySqueezeBreakoutStrategy,
    "M5RangeScalper": M5RangeScalperStrategy,
}


def list_strategy_descriptors() -> list[dict[str, Any]]:
    """Returns serializable catalog entries for every registered offensive strategy."""
    items: list[dict[str, Any]] = []
    for cls in STRATEGY_CLASSES.values():
        instance = cls()
        items.append(
            {
                "name": instance.name,
                "display_name_th": instance.display_name_th,
                "description_th": instance.description_th,
                "recommended_timeframe": instance.recommended_timeframe,
                "param_specs": [asdict(p) for p in cls.get_param_specs()],
                "default_params": instance.get_params(),
            }
        )
    return items


def build_strategy(
    strategy_name: Optional[str],
    params: Optional[dict[str, Any]] = None,
) -> BaseStrategy:
    """Instantiates a registered offensive strategy cleanly with validated parameter overrides."""
    target = (strategy_name or "").strip() or "M5KernelPullback"
    cls = STRATEGY_CLASSES.get(target)
    if cls is None:
        available = ", ".join(STRATEGY_CLASSES.keys())
        raise ValueError(f"Unknown strategy '{target}'. Available: {available}")
    return cls.from_params(params)
