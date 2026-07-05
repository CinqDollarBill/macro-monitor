"""Data models for the Macro Monitor dashboard.

Kept intentionally thin: these are the shapes any DataProvider must return.
Swapping to a live feed means filling these same dataclasses from an API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Instrument:
    """A generic market instrument: equity index, commodity, FX pair, etc."""

    name: str
    value: float
    change_pct: float              # month-to-date % change
    unit: str = ""
    category: str = ""
    # Optional recent price trail used to draw sparklines. If empty, the
    # renderer falls back to a magnitude-based bar derived from change_pct.
    trail: list[float] = field(default_factory=list)
    ytd_pct: float = 0.0           # year-to-date % change
    dtd_pct: float = 0.0           # 1-day % change (populated for portfolio rows)
    wtd_pct: float = 0.0           # week-to-date % change (populated for portfolio rows)

    @property
    def is_up(self) -> bool:
        return self.change_pct >= 0

    def formatted_value(self) -> str:
        """Render the last price with an instrument-appropriate format."""
        u = self.unit
        if u == "%":
            return f"{self.value:.3f}%"
        if u == "FX":
            # FX pairs customarily shown to 4 decimals (JPY crosses to 2).
            return f"{self.value:.4f}" if self.value < 50 else f"{self.value:,.2f}"
        return f"{self.value:,.2f}"


@dataclass
class RatesInstrument(Instrument):
    """Government bond yield point on a curve."""

    tenor: str = ""
    country: str = ""


@dataclass
class PolicyOutcome:
    """One possible outcome of a central-bank rate decision, with its probability."""

    label: str          # e.g. "Cut 25bps", "Hold", "Hike 25+bps"
    probability: float  # 0.0 to 1.0


@dataclass
class PolicyRate:
    """Central-bank policy rate with optional market-implied next-meeting odds."""

    bank: str                       # "Fed" or "BoC"
    rate_low: float                 # e.g. 3.50 (lower bound of target range)
    rate_high: float                # e.g. 3.75 (same as rate_low for point targets)
    meeting_date: Optional[date] = None
    outcomes: list[PolicyOutcome] = field(default_factory=list)
    prob_source: Optional[str] = None  # "Polymarket", etc. — empty when unavailable


@dataclass
class EconomicEvent:
    """A scheduled economic release or central-bank meeting."""

    name: str
    event_date: date
    category: str = "release"  # release | central_bank | speech | auction
    importance: str = "normal"  # normal | high

    def days_until(self, today: Optional[date] = None) -> int:
        if today is None:
            today = date.today()
        return (self.event_date - today).days
