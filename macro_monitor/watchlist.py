"""User watchlist: ticker persistence + Yahoo search.

The watchlist is a plaintext list of ticker symbols stored under the XDG
config dir (`~/.config/macro_monitor/watchlist.txt` by default). The file is
the source of truth — CLI flags / env vars only seed it if the file doesn't
yet exist.

`search_yahoo` queries Yahoo's public search autocomplete to resolve a
user-typed query (ticker or company name) to a list of candidate symbols.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

log = logging.getLogger(__name__)


def _default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "macro_monitor" / "watchlist.txt"


class WatchlistConfig:
    """Thread-safe on-disk watchlist of ticker symbols."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else _default_config_path()
        self._tickers: list[str] = []
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._tickers = []
            return
        seen: set[str] = set()
        tickers: list[str] = []
        for line in self.path.read_text().splitlines():
            t = line.strip().upper()
            if not t or t.startswith("#"):
                continue
            if t not in seen:
                seen.add(t)
                tickers.append(t)
        self._tickers = tickers

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(self._tickers)
        self.path.write_text(body + ("\n" if body else ""))

    def seed_if_empty(self, tickers: list[str]) -> None:
        """Populate the watchlist only if it's currently empty."""
        with self._lock:
            if self._tickers:
                return
            seen: set[str] = set()
            out: list[str] = []
            for t in tickers:
                k = t.strip().upper()
                if k and k not in seen:
                    seen.add(k)
                    out.append(k)
            if out:
                self._tickers = out
                self._save()

    def tickers(self) -> list[str]:
        with self._lock:
            return list(self._tickers)

    def add(self, ticker: str) -> bool:
        t = ticker.strip().upper()
        if not t:
            return False
        with self._lock:
            if t in self._tickers:
                return False
            self._tickers.append(t)
            self._save()
        return True

    def remove(self, ticker: str) -> bool:
        t = ticker.strip().upper()
        with self._lock:
            if t not in self._tickers:
                return False
            self._tickers.remove(t)
            self._save()
        return True


def search_yahoo(query: str, *, limit: int = 8) -> list[dict]:
    """Query Yahoo's public search endpoint.

    Returns a list of dicts with keys: symbol, name, type, exchange.
    Empty list on network error (callers show "no matches" state).
    """
    # Imported lazily to avoid circular import at module load.
    from .data import _http_json

    q = urlencode({"q": query, "quotesCount": limit, "newsCount": 0})
    url = f"https://query1.finance.yahoo.com/v1/finance/search?{q}"
    try:
        data = _http_json(url)
    except Exception as exc:
        log.info("yahoo search failed for %r: %s", query, exc)
        return []

    out: list[dict] = []
    for r in (data.get("quotes") or [])[:limit]:
        sym = r.get("symbol")
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": r.get("shortname") or r.get("longname") or "",
            "type": r.get("quoteType") or "",
            "exchange": r.get("exchDisp") or r.get("exchange") or "",
        })
    return out
