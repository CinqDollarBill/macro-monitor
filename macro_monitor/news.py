"""News feed provider.

Polls a handful of free RSS feeds (WSJ Markets, FT, Bloomberg via Google News)
on a background thread, parses them with stdlib XML, and exposes sorted
headlines to the UI. Full article text is paywalled, so titles only.

Architecture mirrors LiveDataProvider:
  - TTL cache (default 5 min)
  - Daemon warmer thread; UI never blocks on I/O
  - Per-source try/except so one feed going down doesn't kill the panel
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_HTTP_TIMEOUT = 10.0


@dataclass
class NewsItem:
    title: str
    source: str
    pub_time: Optional[datetime] = None
    link: str = ""


# (display_label, feed_url). Kept short so the UI's source column stays tight.
DEFAULT_FEEDS: list[tuple[str, str]] = [
    # Wall Street Journal — Markets
    ("WSJ",  "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    # Financial Times — Markets section (FT's own RSS occasionally blocks
    # non-browser UAs; we send a Chrome UA below, which works in practice).
    ("FT",   "https://www.ft.com/markets?format=rss"),
    # Bloomberg has no reliable direct feed since they deprecated theirs;
    # Google News filtered to bloomberg.com gives us their headlines.
    ("BBG",  "https://news.google.com/rss/search?q=when:1d+site:bloomberg.com&hl=en-US&gl=US&ceid=US:en"),
]


# Regex blocklist for opinion/advice/off-topic headlines. Each pattern is
# matched case-insensitively against the title; a hit drops the item. The
# categories below err on the side of dropping promotional / personal-finance
# listicles and marked-opinion columns while keeping factual reporting.
_OPINION_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Explicit opinion markers: only at start of title or after a pipe
    # (WSJ uses "Opinion | Headline", FT sometimes "Editorial: ...").
    r"(?:^|\|\s*)(opinion|editorial|op[- ]?ed|commentary)\b\s*[:|]",
    # First-person / advice voice
    r"\bi[''`\u2019]?ve\b|\bi[''`\u2019]?m\b|\bmy take\b",
    r"\b(why|how|should|can|what) (you|i|we)\b",
    # Personal finance & listicle spam
    r"\b(best|top) \d+\b",
    r"\bthe best .{0,40}(companies|firms|funds|stocks|advisors?|brokers?|cards?|accounts?)\b",
    r"\bfinancial advisors?\b|\binvestment firms?\b",
    r"\b(retirement|401\(k\)|ira)\b.*\b(plan|tips|how|guide)\b",
    r"\b(guide|primer) to\b",
    r"\bconsider(ing)?\s*$",
    # Named opinion/analysis columns
    r"\b(lex|alphaville|heard on the street|breakingviews|the ft view|the big take|the intelligent investor)\b",
    # Audio / video formats
    r"\b(podcast|explainer|video|watch:)\b",
    # Lifestyle / off-topic leaks (common in general RSS feeds)
    r"\b(wine|watches? and wonders|fashion|luxury|real estate)\b",
]]


def _is_opinion(title: str) -> bool:
    return any(p.search(title) for p in _OPINION_PATTERNS)


def _parse_rss_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        return None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_feed(source: str, url: str) -> list[NewsItem]:
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml,application/xml,*/*"})
    with urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        body = resp.read()
    root = ET.fromstring(body)
    items: list[NewsItem] = []
    # Works for RSS 2.0 (items under channel). For Atom, <entry>/<title> would differ,
    # but all three of our sources publish RSS 2.0.
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        # Google News wraps titles like "Headline - Source"; strip the trailing source.
        if source == "BBG" and " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()
        pub = _parse_rss_date((it.findtext("pubDate") or "").strip())
        link = (it.findtext("link") or "").strip()
        items.append(NewsItem(title=title, source=source, pub_time=pub, link=link))
    return items


class NewsProvider:
    """RSS aggregator with background warming + TTL caching."""

    TTL_SECONDS = 300  # 5 minutes
    KEEP_ITEMS = 60    # max items to hold in memory

    def __init__(self, feeds: Optional[list[tuple[str, str]]] = None, *, warm_on_init: bool = True):
        self.feeds = list(feeds or DEFAULT_FEEDS)
        self._items: list[NewsItem] = []
        self._status: dict = {"source": "pending", "fetched_at": None, "error": None}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._warmer: Optional[threading.Thread] = None
        if warm_on_init:
            self.start_warmer()

    # -- lifecycle --

    def start_warmer(self) -> None:
        if self._warmer and self._warmer.is_alive():
            return
        t = threading.Thread(target=self._warm_loop, name="news-warmer", daemon=True)
        t.start()
        self._warmer = t

    def stop(self) -> None:
        self._stop.set()

    # -- public API --

    def items(self, limit: int = 20) -> list[NewsItem]:
        with self._lock:
            return list(self._items[:limit])

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # -- internals --

    def _warm_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception as exc:  # should be rare — _refresh swallows per-feed errors
                log.warning("news warmer crashed: %s", exc)
            self._stop.wait(self.TTL_SECONDS)

    def _refresh(self) -> None:
        all_items: list[NewsItem] = []
        errors: list[str] = []
        for source, url in self.feeds:
            try:
                all_items.extend(_fetch_feed(source, url))
            except Exception as exc:
                errors.append(f"{source}: {exc.__class__.__name__}")
                log.info("news fetch %s failed: %s", source, exc)

        # Drop opinion / listicle / personal-finance headlines. Keeping factual
        # market reporting is the whole point; "Best 6 Financial Advisors" and
        # "The main reason I've bought equities again" are not that.
        all_items = [i for i in all_items if not _is_opinion(i.title)]

        # Newest first; items with no pub_time sort last.
        all_items.sort(
            key=lambda i: i.pub_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        with self._lock:
            had_cache = bool(self._items)
            if all_items:
                self._items = all_items[: self.KEEP_ITEMS]
                self._status = {
                    "source": "live" if not errors else "cache",
                    "fetched_at": time.time(),
                    "error": "; ".join(errors) if errors else None,
                }
            elif errors:
                self._status = {
                    "source": "cache" if had_cache else "fallback",
                    "fetched_at": self._status.get("fetched_at"),
                    "error": "; ".join(errors),
                }
