"""Data providers.

The dashboard consumes data through the DataProvider interface, so the UI is
agnostic to where quotes come from.

    StaticDataProvider — ships the demo dataset. No network.
    LiveDataProvider   — fetches real data from free public endpoints:
                             Yahoo Finance chart API (indexes/FX/commodities)
                             Binance public API       (crypto)
                             FRED                     (U.S. Treasury yields)
                             Bank of Canada Valet     (Government of Canada yields)

LiveDataProvider layers three safety nets on top of raw fetches:

    1. TTL cache per endpoint (prices 60s, yields 5min, etc.)
    2. Background warmer thread that keeps the cache fresh off the UI thread
    3. Graceful fallback to a delegate provider whenever a fetch fails

The render path always reads from cache; it never blocks on I/O. Startup
shows fallback data and swaps in live data within a few seconds as the
warmer populates the cache.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import EconomicEvent, Instrument, PolicyOutcome, PolicyRate, RatesInstrument
from .watchlist import WatchlistConfig

log = logging.getLogger(__name__)

# Keys tracked by LiveDataProvider.status()
LIVE_CAPABLE_KEYS = (
    "indexes", "commodities", "fx", "crypto",
    "us_rates", "canada_rates",
    "policy_rates",
    "watchlist",
)


class DataProvider(ABC):
    """Abstract source of market data for the dashboard."""

    @abstractmethod
    def get_indexes(self) -> List[Instrument]: ...

    @abstractmethod
    def get_commodities(self) -> List[Instrument]: ...

    @abstractmethod
    def get_fx(self) -> List[Instrument]: ...

    @abstractmethod
    def get_crypto(self) -> List[Instrument]: ...

    @abstractmethod
    def get_us_rates(self) -> List[RatesInstrument]: ...

    @abstractmethod
    def get_canada_rates(self) -> List[RatesInstrument]: ...

    @abstractmethod
    def get_policy_rates(self) -> List[PolicyRate]: ...

    @abstractmethod
    def get_events(self) -> List[EconomicEvent]: ...

    def get_watchlist(self) -> List[Instrument]:
        """User-defined watchlist tickers. Empty by default."""
        return []

    @property
    def mode_label(self) -> str:
        return "STATIC"

    def status(self, key: str) -> dict:
        """Per-endpoint status used by the UI to render a source badge.

        Returns a dict with keys:
            source:      'live' | 'cache' | 'fallback' | 'pending' | 'static'
            fetched_at:  unix ts of last successful fetch, or None
            error:       last error message, or None
        """
        return {"source": "static", "fetched_at": None, "error": None}


# ---------------------------------------------------------------------------
# Static demo data
# ---------------------------------------------------------------------------


class StaticDataProvider(DataProvider):
    """In-memory demo data. Same numbers the brief specifies."""

    def get_indexes(self) -> List[Instrument]:
        return [
            Instrument("S&P 500", 6582.00, -3.20, "", "equity",
                       trail=[6800, 6755, 6710, 6660, 6620, 6600, 6582], ytd_pct=12.40),
            Instrument("Dow Jones", 46504.00, -4.10, "", "equity",
                       trail=[48500, 48100, 47700, 47300, 46900, 46600, 46504], ytd_pct=9.35),
            Instrument("Nasdaq", 22180.00, -3.85, "", "equity",
                       trail=[23100, 22950, 22780, 22600, 22420, 22300, 22180], ytd_pct=14.90),
            Instrument("Stoxx Europe 600", 562.40, -1.20, "", "equity",
                       trail=[572, 570, 568, 566, 565, 563, 562.40], ytd_pct=8.10),
        ]

    def get_commodities(self) -> List[Instrument]:
        return [
            Instrument("Oil (NYMEX WTI)", 111.54, 49.24, "USD/BBL", "commodity",
                       trail=[74.7, 80.2, 86.0, 92.5, 99.0, 106.2, 111.54], ytd_pct=55.80),
            Instrument("Gold", 4676.00, -12.59, "USD", "commodity",
                       trail=[5350, 5250, 5120, 4980, 4860, 4750, 4676], ytd_pct=21.70),
        ]

    def get_fx(self) -> List[Instrument]:
        return [
            Instrument("USD/CAD", 1.3946, 2.00, "FX", "fx",
                       trail=[1.3673, 1.3720, 1.3790, 1.3840, 1.3880, 1.3920, 1.3946], ytd_pct=-3.10),
            Instrument("EUR/USD", 1.1522, -0.78, "FX", "fx",
                       trail=[1.1613, 1.1600, 1.1580, 1.1560, 1.1540, 1.1530, 1.1522], ytd_pct=4.85),
            Instrument("USD/JPY", 159.63, 1.33, "FX", "fx",
                       trail=[157.53, 158.00, 158.40, 158.80, 159.10, 159.40, 159.63], ytd_pct=1.60),
        ]

    def get_crypto(self) -> List[Instrument]:
        return [
            Instrument("BTC/USD", 98420.00, 4.80, "USD", "crypto",
                       trail=[93900, 94800, 95600, 96500, 97200, 97900, 98420], ytd_pct=41.20),
            Instrument("ETH/USD", 3612.00, 2.10, "USD", "crypto",
                       trail=[3538, 3555, 3570, 3585, 3598, 3606, 3612], ytd_pct=28.50),
        ]

    def get_us_rates(self) -> List[RatesInstrument]:
        return [
            RatesInstrument("2Y UST", 3.805, 9.56, "%", "ust", tenor="2Y", country="US",
                            trail=[3.47, 3.55, 3.62, 3.68, 3.74, 3.78, 3.805], ytd_pct=-7.20),
            RatesInstrument("5Y UST", 3.950, 9.63, "%", "ust", tenor="5Y", country="US",
                            trail=[3.60, 3.68, 3.76, 3.83, 3.89, 3.93, 3.95], ytd_pct=-4.30),
            RatesInstrument("10Y UST", 4.309, 6.76, "%", "ust", tenor="10Y", country="US",
                            trail=[4.04, 4.10, 4.16, 4.22, 4.26, 4.29, 4.309], ytd_pct=-2.10),
            RatesInstrument("30Y UST", 4.880, 4.21, "%", "ust", tenor="30Y", country="US",
                            trail=[4.68, 4.72, 4.76, 4.80, 4.84, 4.86, 4.88], ytd_pct=1.50),
        ]

    def get_canada_rates(self) -> List[RatesInstrument]:
        return [
            RatesInstrument("2Y GoC", 2.810, -0.38, "%", "goc", tenor="2Y", country="CA",
                            trail=[2.821, 2.820, 2.818, 2.816, 2.814, 2.812, 2.810], ytd_pct=-8.40),
            RatesInstrument("5Y GoC", 3.089, 12.12, "%", "goc", tenor="5Y", country="CA",
                            trail=[2.755, 2.820, 2.890, 2.960, 3.020, 3.060, 3.089], ytd_pct=-1.20),
            RatesInstrument("10Y GoC", 3.483, 8.10, "%", "goc", tenor="10Y", country="CA",
                            trail=[3.222, 3.280, 3.335, 3.385, 3.430, 3.460, 3.483], ytd_pct=2.80),
            RatesInstrument("30Y GoC", 3.685, 4.52, "%", "goc", tenor="30Y", country="CA",
                            trail=[3.524, 3.555, 3.595, 3.625, 3.652, 3.670, 3.685], ytd_pct=5.15),
        ]

    def get_policy_rates(self) -> List[PolicyRate]:
        return [
            PolicyRate(
                bank="Fed", rate_low=3.50, rate_high=3.75,
                meeting_date=date(2026, 4, 29),
                outcomes=[
                    PolicyOutcome("Cut 50+bps", 0.002),
                    PolicyOutcome("Cut 25bps",  0.004),
                    PolicyOutcome("Hold",       0.992),
                    PolicyOutcome("Hike 25+bps", 0.003),
                ],
                prob_source="Polymarket",
            ),
            PolicyRate(
                bank="BoC", rate_low=2.25, rate_high=2.25,
                meeting_date=date(2026, 4, 29),
                outcomes=[
                    PolicyOutcome("Cut 50+bps", 0.004),
                    PolicyOutcome("Cut 25bps",  0.007),
                    PolicyOutcome("Hold",       0.981),
                    PolicyOutcome("Hike 25+bps", 0.008),
                ],
                prob_source="Polymarket",
            ),
        ]

    def get_events(self) -> List[EconomicEvent]:
        return [
            EconomicEvent("Nonfarm Payrolls", date(2026, 4, 3), "release", "high"),
            EconomicEvent("Unemployment Rate", date(2026, 4, 3), "release", "high"),
            EconomicEvent("FOMC Meeting (Day 1)", date(2026, 4, 28), "central_bank", "high"),
            EconomicEvent("FOMC Meeting (Day 2) + Statement", date(2026, 4, 29), "central_bank", "high"),
            EconomicEvent("BoC Rate Decision", date(2026, 4, 29), "central_bank", "high"),
        ]


# ---------------------------------------------------------------------------
# HTTP fetchers (stdlib only)
# ---------------------------------------------------------------------------


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_HTTP_TIMEOUT = 8.0


def _http_json(url: str, headers: Optional[dict] = None) -> dict:
    """GET a URL and parse JSON. Raises on network or parse errors."""
    hdr = {"User-Agent": _UA, "Accept": "application/json,*/*"}
    if headers:
        hdr.update(headers)
    req = Request(url, headers=hdr)
    # Use default SSL context; on macOS this uses the system trust store.
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
        return json.loads(resp.read().decode())


class _YahooFetcher:
    """Yahoo Finance, with two layers of resilience.

    Primary:   direct GET to query1.finance.yahoo.com chart API (stdlib urllib).
    Fallback:  yfinance library (has its own session/cookie/crumb handling and
               tends to survive when the raw endpoint 429s).

    If both fail the caller sees the raised exception and degrades to static.
    """

    BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def quote(self, symbol: str) -> tuple[float, float, float, list[float]]:
        """Return (last_close, mtd_pct, ytd_pct, close_trail)."""
        pairs = self._pairs(symbol)
        last, _dtd, _wtd, mtd, ytd, trail = _period_metrics(pairs)
        return last, mtd, ytd, trail

    def quote_portfolio(
        self, symbol: str,
    ) -> tuple[float, float, float, float, float, list[float]]:
        """Return (last, dtd_pct, wtd_pct, mtd_pct, ytd_pct, close_trail)."""
        return _period_metrics(self._pairs(symbol))

    def _pairs(self, symbol: str) -> list[tuple[datetime, float]]:
        try:
            return self._direct_pairs(symbol)
        except Exception as exc:
            log.info("yahoo direct failed for %s (%s) — trying yfinance", symbol, exc)
            return self._yfinance_pairs(symbol)

    def _direct_pairs(self, symbol: str) -> list[tuple[datetime, float]]:
        url = f"{self.BASE.format(symbol=symbol)}?interval=1d&range=1y"
        data = _http_json(url)
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            raise ValueError(f"empty chart response for {symbol}")
        r0 = result[0]
        timestamps = r0.get("timestamp") or []
        quotes = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        closes_raw = quotes.get("close") or []
        pairs = [
            (datetime.fromtimestamp(t), float(c))
            for t, c in zip(timestamps, closes_raw)
            if c is not None
        ]
        if not pairs:
            raise ValueError(f"no close data for {symbol}")
        return pairs

    def _yfinance_pairs(self, symbol: str) -> list[tuple[datetime, float]]:
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "yfinance not installed; run `pip install yfinance` to enable fallback"
            ) from exc

        hist = yf.Ticker(symbol).history(period="1y", auto_adjust=False)
        closes = hist["Close"].dropna()
        if closes.empty:
            raise ValueError(f"yfinance returned no closes for {symbol}")
        return [
            (ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime(ts.year, ts.month, ts.day), float(v))
            for ts, v in zip(closes.index, closes.values)
        ]


class _BinanceFetcher:
    """Binance public market data. Keyless; generous rate limits."""

    BASE = "https://api.binance.com/api/v3/klines"

    def quote(self, symbol: str) -> tuple[float, float, float, list[float]]:
        """Return (last_close, mtd_pct, ytd_pct, close_trail). `symbol` like 'BTCUSDT'."""
        url = f"{self.BASE}?symbol={symbol}&interval=1d&limit=400"
        data = _http_json(url)
        if not isinstance(data, list) or not data:
            raise ValueError(f"empty klines for {symbol}")
        # Kline row: [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]
        pairs = [(datetime.fromtimestamp(k[0] / 1000), float(k[4])) for k in data]
        closes = [c for _, c in pairs]
        last = closes[-1]

        now = datetime.now()
        mtd_start = ytd_start = None
        for d, c in pairs:
            if ytd_start is None and d.year == now.year:
                ytd_start = c
            if d.year == now.year and d.month == now.month:
                mtd_start = c
                break
        if mtd_start is None:
            mtd_start = closes[0]
        if ytd_start is None:
            ytd_start = closes[0]
        mtd = (last - mtd_start) / mtd_start * 100.0 if mtd_start else 0.0
        ytd = (last - ytd_start) / ytd_start * 100.0 if ytd_start else 0.0
        return float(last), float(mtd), float(ytd), [float(c) for c in closes[-30:]]


class _FREDFetcher:
    """FRED series/observations API. Requires an API key (free)."""

    BASE = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def observations(self, series: str) -> list[tuple[date, float]]:
        # Start in mid-December of the prior year so we always have a
        # pre-YTD anchor (first business day of January may be Jan 2–4).
        today = date.today()
        start = date(today.year - 1, 12, 15).isoformat()
        q = urlencode({
            "series_id": series,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": start,
            "sort_order": "asc",
        })
        data = _http_json(f"{self.BASE}?{q}")
        out: list[tuple[date, float]] = []
        for obs in data.get("observations", []):
            v = obs.get("value")
            if v in (None, ".", ""):
                continue
            try:
                out.append((date.fromisoformat(obs["date"]), float(v)))
            except (ValueError, TypeError):
                continue
        return out


class _BoCFetcher:
    """Bank of Canada Valet API. Keyless."""

    BASE = "https://www.bankofcanada.ca/valet/observations/{series}/json"

    def observations(
        self,
        series: str,
        *,
        recent_months: Optional[int] = None,
        start_date: Optional[date] = None,
    ) -> list[tuple[date, float]]:
        if start_date is not None:
            q = urlencode({"start_date": start_date.isoformat()})
        else:
            q = urlencode({"recent_months": recent_months or 2})
        data = _http_json(f"{self.BASE.format(series=series)}?{q}")
        out: list[tuple[date, float]] = []
        for obs in data.get("observations", []):
            cell = obs.get(series) or {}
            v = cell.get("v")
            if v in (None, "", "."):
                continue
            try:
                out.append((date.fromisoformat(obs["d"]), float(v)))
            except (ValueError, TypeError):
                continue
        return out


class _PolymarketFetcher:
    """Polymarket Gamma API — public prediction-market prices.

    Prices come as Yes/No binary markets; the "Yes" price (outcomePrices[0])
    is the implied probability of that outcome on a 0..1 scale.
    """

    BASE = "https://gamma-api.polymarket.com"
    # Map from our slug prefix to the terms used in question text.
    BANK_SLUGS = {"Fed": "fed", "BoC": "bank-of-canada"}

    def decision_event(self, bank: str, target_date: date) -> Optional[dict]:
        """Fetch the decision event for `bank` around `target_date`.

        Polymarket names events like `fed-decision-in-april`; we try the
        target month first, then the next month as a fallback so this keeps
        working across the meeting transition.
        """
        import calendar
        prefix = self.BANK_SLUGS[bank]
        attempts = [target_date]
        # also try the month after, in case the current event already closed
        nxt = target_date.replace(day=28)
        nxt = (nxt.replace(day=28) + __import__("datetime").timedelta(days=7)).replace(day=1)
        attempts.append(nxt)
        for d in attempts:
            slug = f"{prefix}-decision-in-{calendar.month_name[d.month].lower()}"
            try:
                data = _http_json(f"{self.BASE}/events?slug={slug}")
            except Exception:
                continue
            if isinstance(data, list) and data:
                ev = data[0]
                if ev.get("markets") and not ev.get("closed", False):
                    return ev
        return None

    @staticmethod
    def parse_outcomes(event: dict) -> tuple[list[PolicyOutcome], Optional[date]]:
        """Extract probability outcomes + meeting end-date from an event."""
        import re
        outcomes: list[PolicyOutcome] = []
        for m in event.get("markets", []):
            q = (m.get("question") or "").lower()
            prices_raw = m.get("outcomePrices")
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
                yes_prob = float(prices[0])
            except (ValueError, IndexError, TypeError):
                continue
            label = _normalize_outcome_label(q)
            if label:
                outcomes.append(PolicyOutcome(label=label, probability=yes_prob))

        # Deduplicate by label (keep highest probability)
        by_label: dict[str, float] = {}
        for o in outcomes:
            by_label[o.label] = max(by_label.get(o.label, 0.0), o.probability)
        outcomes = [PolicyOutcome(k, v) for k, v in by_label.items()]
        outcomes.sort(key=_outcome_sort_key)

        meeting = None
        end_raw = event.get("endDate")
        if end_raw:
            try:
                meeting = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).date()
            except Exception:
                pass
        return outcomes, meeting


def _normalize_outcome_label(question_lower: str) -> Optional[str]:
    """Map a Polymarket question into a short label (Cut/Hold/Hike + bps)."""
    import re
    if "no change" in question_lower:
        return "Hold"
    direction = None
    if any(w in question_lower for w in ("decrease", "cut")):
        direction = "Cut"
    elif any(w in question_lower for w in ("increase", "hike", "raise")):
        direction = "Hike"
    if direction is None:
        return None
    m = re.search(r"(\d+)\s*(\+)?\s*bps", question_lower)
    if m:
        bps, plus = m.group(1), (m.group(2) or "")
        return f"{direction} {bps}{plus}bps"
    return direction


def _outcome_sort_key(outcome: PolicyOutcome) -> int:
    """Order: biggest cut first → Hold → biggest hike last."""
    import re
    label = outcome.label
    if label == "Hold":
        return 0
    m = re.match(r"(Cut|Hike)\s*(\d+)(\+)?bps", label)
    if not m:
        return 999 if label.startswith("Hike") else -999
    direction, bps, plus = m.group(1), int(m.group(2)), m.group(3) == "+"
    sign = -1 if direction == "Cut" else 1
    return sign * (bps + (1 if plus else 0))


def _period_metrics(
    pairs: list[tuple[datetime, float]],
) -> tuple[float, float, float, float, float, list[float]]:
    """Given daily (datetime, close) pairs, return (last, dtd, wtd, mtd, ytd, trail).

    DTD is the 1-day change vs the prior close. WTD/MTD/YTD anchor on the first
    close on/after Monday of the current week, the 1st of the current month, and
    January 1st of the current year, respectively.
    """
    if not pairs:
        raise ValueError("no price pairs")
    closes = [c for _, c in pairs]
    last = closes[-1]

    dtd = (last - closes[-2]) / closes[-2] * 100.0 if len(closes) >= 2 and closes[-2] else 0.0

    now = datetime.now()
    week_start = (now - timedelta(days=now.weekday())).date()
    wtd_anchor = mtd_anchor = ytd_anchor = None
    for d, c in pairs:
        if ytd_anchor is None and d.year == now.year:
            ytd_anchor = c
        if mtd_anchor is None and d.year == now.year and d.month == now.month:
            mtd_anchor = c
        if wtd_anchor is None and d.date() >= week_start:
            wtd_anchor = c

    if wtd_anchor is None:
        wtd_anchor = closes[-1]
    if mtd_anchor is None:
        mtd_anchor = closes[0]
    if ytd_anchor is None:
        ytd_anchor = closes[0]

    wtd = (last - wtd_anchor) / wtd_anchor * 100.0 if wtd_anchor else 0.0
    mtd = (last - mtd_anchor) / mtd_anchor * 100.0 if mtd_anchor else 0.0
    ytd = (last - ytd_anchor) / ytd_anchor * 100.0 if ytd_anchor else 0.0
    trail = [float(c) for c in closes[-30:]]
    return float(last), float(dtd), float(wtd), float(mtd), float(ytd), trail


def _mtd_ytd_change(obs: list[tuple[date, float]]) -> tuple[float, float, float, list[float]]:
    """Given a sorted list of (date, value), return (last, mtd_pct, ytd_pct, trail)."""
    if not obs:
        raise ValueError("no observations")
    today = date.today()
    in_year = [(d, v) for (d, v) in obs if d.year == today.year]
    in_month = [v for (d, v) in in_year if d.month == today.month]
    mtd_anchor = in_month[0] if in_month else (in_year[0][1] if in_year else obs[0][1])
    ytd_anchor = in_year[0][1] if in_year else obs[0][1]
    last = obs[-1][1]
    mtd = (last - mtd_anchor) / mtd_anchor * 100.0 if mtd_anchor else 0.0
    ytd = (last - ytd_anchor) / ytd_anchor * 100.0 if ytd_anchor else 0.0
    trail = [v for _, v in obs[-15:]]
    return last, mtd, ytd, trail


# ---------------------------------------------------------------------------
# Live provider
# ---------------------------------------------------------------------------


class LiveDataProvider(DataProvider):
    """Real-data provider with TTL cache, background warmer, and fallback.

    Reads are non-blocking: `get_*` returns the cached value (or falls back to
    the delegate provider if the cache is cold). A daemon thread refreshes the
    cache in the background, so the UI is never stuck on a slow API.
    """

    TTLS = {
        "indexes": 60,
        "commodities": 60,
        "fx": 60,
        "crypto": 60,
        "us_rates": 300,
        "canada_rates": 300,
        "policy_rates": 600,  # central bank rates move rarely; probabilities drift slowly
        "watchlist": 60,
    }

    YAHOO_SYMBOLS = {
        "indexes": [
            ("^GSPC", "S&P 500"),
            ("^DJI", "Dow Jones"),
            ("^IXIC", "Nasdaq"),
            ("^STOXX", "Stoxx Europe 600"),
        ],
        "commodities": [
            ("CL=F", "Oil (NYMEX WTI)", "USD/BBL"),
            ("GC=F", "Gold", "USD"),
        ],
        "fx": [("CAD=X", "USD/CAD"), ("EURUSD=X", "EUR/USD"), ("JPY=X", "USD/JPY")],
    }

    BINANCE_SYMBOLS = [("BTCUSDT", "BTC/USD"), ("ETHUSDT", "ETH/USD")]

    FRED_SERIES = [
        ("DGS2", "2Y UST", "2Y"),
        ("DGS5", "5Y UST", "5Y"),
        ("DGS10", "10Y UST", "10Y"),
        ("DGS30", "30Y UST", "30Y"),
    ]

    BOC_SERIES = [
        ("BD.CDN.2YR.DQ.YLD",  "2Y GoC",  "2Y"),
        ("BD.CDN.5YR.DQ.YLD",  "5Y GoC",  "5Y"),
        ("BD.CDN.10YR.DQ.YLD", "10Y GoC", "10Y"),
        # Canada's long benchmark is the ~30-year yield published under `LONG`.
        ("BD.CDN.LONG.DQ.YLD", "30Y GoC", "30Y"),
    ]

    def __init__(
        self,
        fallback: DataProvider,
        fred_api_key: Optional[str] = None,
        *,
        warm_on_init: bool = True,
        watchlist: Optional[WatchlistConfig] = None,
        live_keys: Optional[Iterable[str]] = None,
    ):
        self.fallback = fallback
        self.fred_api_key = fred_api_key
        self._yahoo = _YahooFetcher()
        self._binance = _BinanceFetcher()
        self._fred = _FREDFetcher(fred_api_key) if fred_api_key else None
        self._boc = _BoCFetcher()
        self._poly = _PolymarketFetcher()
        self.watchlist = watchlist or WatchlistConfig()

        self._cache: dict[str, tuple[float, Any]] = {}
        self._status: dict[str, dict] = {}
        # Per-ticker cache for the watchlist so individual failures don't
        # blank out the whole panel.
        self._wl_cache: dict[str, tuple[float, Instrument]] = {}
        self._lock = threading.RLock()

        # Keys this provider fetches live. Everything else is served straight
        # from the fallback provider. Static mode uses this to run a
        # watchlist-only live feed — user tickers have no canned data.
        self._live_keys: set[str] = (
            set(live_keys) if live_keys is not None else set(LIVE_CAPABLE_KEYS)
        )

        # Keys we refuse to attempt: not selected live, or config is missing.
        self._disabled: set[str] = set(LIVE_CAPABLE_KEYS) - self._live_keys
        if not self.fred_api_key:
            self._disabled.add("us_rates")

        # Seed status so the topbar has something to show before first fetch.
        for k in LIVE_CAPABLE_KEYS:
            if k not in self._live_keys:
                self._status[k] = {"source": "static", "fetched_at": None, "error": None}
            elif k in self._disabled:
                self._status[k] = {
                    "source": "fallback", "fetched_at": None,
                    "error": "FRED API key not configured",
                }
            else:
                self._status[k] = {"source": "pending", "fetched_at": None, "error": None}

        self._stop = threading.Event()
        self._warmer: Optional[threading.Thread] = None
        if warm_on_init:
            self.start_warmer()

    @property
    def mode_label(self) -> str:
        # Watchlist-only providers still present as STATIC: every visible
        # panel except the watchlist shows demo data, and the watchlist has
        # its own LIVE badge.
        return "LIVE" if self._live_keys == set(LIVE_CAPABLE_KEYS) else "STATIC"

    def status(self, key: str) -> dict:
        with self._lock:
            return dict(self._status.get(key, {"source": "static", "fetched_at": None, "error": None}))

    # -- lifecycle --

    def start_warmer(self) -> None:
        if self._warmer and self._warmer.is_alive():
            return
        t = threading.Thread(target=self._warm_loop, name="macro-warmer", daemon=True)
        t.start()
        self._warmer = t

    def stop(self) -> None:
        self._stop.set()

    # -- internals --

    _FETCHERS: dict[str, str]  # forward-declared; set below

    def _warm_loop(self) -> None:
        # Always refresh on startup, then sleep a beat between scans. The TTLs
        # prevent each endpoint from being hit more often than allowed.
        while not self._stop.is_set():
            for key in LIVE_CAPABLE_KEYS:
                if self._stop.is_set():
                    return
                if key in self._disabled:
                    continue
                try:
                    self._refresh(key)
                except Exception as exc:
                    log.warning("warm %s failed: %s", key, exc)
            # Shortest TTL is 60s — scan at that cadence; _refresh is a no-op
            # for keys whose cache is still fresh.
            self._stop.wait(min(self.TTLS.values()))

    def _refresh(self, key: str, *, force: bool = False) -> None:
        """Fetch and cache `key`. No-op if cache is still fresh."""
        with self._lock:
            cached = self._cache.get(key)
        if cached and not force and time.time() - cached[0] < self.TTLS[key]:
            return

        fn = {
            "indexes": self._fetch_indexes,
            "commodities": self._fetch_commodities,
            "fx": self._fetch_fx,
            "crypto": self._fetch_crypto,
            "us_rates": self._fetch_us_rates,
            "canada_rates": self._fetch_canada_rates,
            "policy_rates": self._fetch_policy_rates,
            "watchlist": self._fetch_watchlist,
        }[key]
        try:
            val = fn()
            ts = time.time()
            with self._lock:
                self._cache[key] = (ts, val)
                self._status[key] = {"source": "live", "fetched_at": ts, "error": None}
        except Exception as exc:
            with self._lock:
                have_cache = key in self._cache
                self._status[key] = {
                    "source": "cache" if have_cache else "fallback",
                    "fetched_at": self._cache[key][0] if have_cache else None,
                    "error": str(exc),
                }
            raise

    def _get(self, key: str, fallback_fn: Callable[[], Any]) -> Any:
        """Non-blocking cache read. Returns fallback if cache is cold."""
        with self._lock:
            cached = self._cache.get(key)
        if cached:
            return cached[1]
        return fallback_fn()

    # -- fetch implementations --

    def _fetch_indexes(self) -> list[Instrument]:
        out: list[Instrument] = []
        for sym, name in self.YAHOO_SYMBOLS["indexes"]:
            last, mtd, ytd, trail = self._yahoo.quote(sym)
            out.append(Instrument(name, last, mtd, "", "equity", trail=trail, ytd_pct=ytd))
        return out

    def _fetch_commodities(self) -> list[Instrument]:
        out: list[Instrument] = []
        for sym, name, unit in self.YAHOO_SYMBOLS["commodities"]:
            last, mtd, ytd, trail = self._yahoo.quote(sym)
            out.append(Instrument(name, last, mtd, unit, "commodity", trail=trail, ytd_pct=ytd))
        return out

    def _fetch_fx(self) -> list[Instrument]:
        out: list[Instrument] = []
        for sym, name in self.YAHOO_SYMBOLS["fx"]:
            last, mtd, ytd, trail = self._yahoo.quote(sym)
            out.append(Instrument(name, last, mtd, "FX", "fx", trail=trail, ytd_pct=ytd))
        return out

    def _fetch_crypto(self) -> list[Instrument]:
        out: list[Instrument] = []
        for sym, name in self.BINANCE_SYMBOLS:
            last, mtd, ytd, trail = self._binance.quote(sym)
            out.append(Instrument(name, last, mtd, "USD", "crypto", trail=trail, ytd_pct=ytd))
        return out

    def _fetch_us_rates(self) -> list[RatesInstrument]:
        if not self._fred:
            raise RuntimeError("FRED API key not configured")
        out: list[RatesInstrument] = []
        for series, name, tenor in self.FRED_SERIES:
            obs = self._fred.observations(series)
            last, mtd, ytd, trail = _mtd_ytd_change(obs)
            out.append(RatesInstrument(name, last, mtd, "%", "ust",
                                       tenor=tenor, country="US", trail=trail, ytd_pct=ytd))
        return out

    def _fetch_canada_rates(self) -> list[RatesInstrument]:
        out: list[RatesInstrument] = []
        today = date.today()
        start = date(today.year - 1, 12, 15)
        for series, name, tenor in self.BOC_SERIES:
            try:
                obs = self._boc.observations(series, start_date=start)
                last, mtd, ytd, trail = _mtd_ytd_change(obs)
                out.append(RatesInstrument(name, last, mtd, "%", "goc",
                                           tenor=tenor, country="CA", trail=trail, ytd_pct=ytd))
            except Exception as exc:
                # Per-tenor failure is non-fatal: we'd rather show 3 good rows
                # than drop the whole panel back to static for one bad series.
                log.info("boc %s (%s) failed: %s", series, name, exc)
        if not out:
            raise RuntimeError("all BoC series failed")
        return out

    def _fetch_policy_rates(self) -> list[PolicyRate]:
        """Fed + BoC current policy rates, each with Polymarket-implied odds.

        Each sub-fetch is best-effort: a failure in one dimension (e.g. FRED
        down, or Polymarket event not yet published) degrades that field to
        defaults rather than blowing up the whole endpoint.
        """
        today = date.today()
        results: list[PolicyRate] = []

        # --- Fed ---
        fed_low = fed_high = None
        if self._fred:
            try:
                obs_u = self._fred.observations("DFEDTARU")
                obs_l = self._fred.observations("DFEDTARL")
                if obs_u and obs_l:
                    fed_high = obs_u[-1][1]
                    fed_low = obs_l[-1][1]
            except Exception as exc:
                log.info("fed target fetch failed: %s", exc)

        fed_outcomes: list[PolicyOutcome] = []
        fed_meeting: Optional[date] = None
        try:
            ev = self._poly.decision_event("Fed", today)
            if ev:
                fed_outcomes, fed_meeting = _PolymarketFetcher.parse_outcomes(ev)
        except Exception as exc:
            log.info("fed polymarket fetch failed: %s", exc)

        if fed_low is not None:
            results.append(PolicyRate(
                bank="Fed",
                rate_low=fed_low, rate_high=fed_high if fed_high is not None else fed_low,
                meeting_date=fed_meeting,
                outcomes=fed_outcomes,
                prob_source="Polymarket" if fed_outcomes else None,
            ))

        # --- BoC ---
        boc_rate = None
        try:
            obs = self._boc.observations("V39079", recent_months=3)
            if obs:
                boc_rate = obs[-1][1]
        except Exception as exc:
            log.info("boc policy rate fetch failed: %s", exc)

        boc_outcomes: list[PolicyOutcome] = []
        boc_meeting: Optional[date] = None
        try:
            ev = self._poly.decision_event("BoC", today)
            if ev:
                boc_outcomes, boc_meeting = _PolymarketFetcher.parse_outcomes(ev)
        except Exception as exc:
            log.info("boc polymarket fetch failed: %s", exc)

        if boc_rate is not None:
            results.append(PolicyRate(
                bank="BoC",
                rate_low=boc_rate, rate_high=boc_rate,
                meeting_date=boc_meeting,
                outcomes=boc_outcomes,
                prob_source="Polymarket" if boc_outcomes else None,
            ))

        if not results:
            raise RuntimeError("no policy rate data could be fetched")
        return results

    # -- public DataProvider API --

    def get_indexes(self):
        return self._get("indexes", self.fallback.get_indexes)

    def get_commodities(self):
        return self._get("commodities", self.fallback.get_commodities)

    def get_fx(self):
        return self._get("fx", self.fallback.get_fx)

    def get_crypto(self):
        return self._get("crypto", self.fallback.get_crypto)

    def get_us_rates(self):
        return self._get("us_rates", self.fallback.get_us_rates)

    def get_canada_rates(self):
        return self._get("canada_rates", self.fallback.get_canada_rates)

    def get_policy_rates(self):
        return self._get("policy_rates", self.fallback.get_policy_rates)

    def get_events(self):
        # Economic calendar APIs generally require paid keys; use fallback.
        return self.fallback.get_events()

    def get_watchlist(self) -> list[Instrument]:
        """Return Instrument rows for each ticker in the user's watchlist.

        Reads per-ticker cache; tickers with no cached data yet appear as
        placeholder rows (0 values) until the warmer populates them.
        """
        out: list[Instrument] = []
        with self._lock:
            for sym in self.watchlist.tickers():
                cached = self._wl_cache.get(sym)
                if cached:
                    out.append(cached[1])
                else:
                    out.append(Instrument(sym, 0.0, 0.0, "", "watchlist"))
        return out

    def _fetch_watchlist(self) -> list[Instrument]:
        """Refresh every watchlist ticker. Updates the per-ticker cache.

        Per-ticker failures are logged but never abort the whole fetch — the
        row keeps whatever was already cached (or a placeholder). We treat
        partial success as success so the aggregate status reads "LIVE".
        """
        tickers = self.watchlist.tickers()
        out: list[Instrument] = []
        for sym in tickers:
            try:
                ins = self._fetch_ticker(sym)
                with self._lock:
                    self._wl_cache[sym] = (time.time(), ins)
                out.append(ins)
            except Exception as exc:
                log.info("watchlist fetch %s failed: %s", sym, exc)
                with self._lock:
                    cached = self._wl_cache.get(sym)
                out.append(cached[1] if cached else Instrument(sym, 0.0, 0.0, "", "watchlist"))
        # Prune cached entries for tickers the user has removed.
        with self._lock:
            for stale in [s for s in self._wl_cache if s not in tickers]:
                self._wl_cache.pop(stale, None)
        return out

    def _fetch_ticker(self, symbol: str) -> Instrument:
        last, dtd, wtd, mtd, ytd, trail = self._yahoo.quote_portfolio(symbol)
        return Instrument(
            symbol, last, mtd, "", "watchlist",
            trail=trail, ytd_pct=ytd, dtd_pct=dtd, wtd_pct=wtd,
        )

    def prime_ticker(self, symbol: str) -> None:
        """Kick off an immediate background fetch for a newly added ticker.

        Called after a user adds a symbol so the panel populates within a few
        seconds instead of waiting for the next warm cycle.
        """
        def work() -> None:
            try:
                ins = self._fetch_ticker(symbol)
                with self._lock:
                    self._wl_cache[symbol] = (time.time(), ins)
                    # Force the aggregate watchlist cache to refresh next tick.
                    self._cache.pop("watchlist", None)
            except Exception as exc:
                log.info("watchlist prime %s failed: %s", symbol, exc)

        threading.Thread(target=work, name=f"wl-prime-{symbol}", daemon=True).start()

    def drop_ticker(self, symbol: str) -> None:
        with self._lock:
            self._wl_cache.pop(symbol.upper(), None)
            self._cache.pop("watchlist", None)
