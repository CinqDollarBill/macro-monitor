"""Entry point: `python -m macro_monitor [--live] [--fred-key KEY] [--refresh N]`."""

from __future__ import annotations

import argparse
import os
import sys

from .app import MacroMonitorApp
from .data import LiveDataProvider, StaticDataProvider
from .news import NewsProvider
from .watchlist import WatchlistConfig


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="macro_monitor",
        description="Terminal-based macro / markets dashboard.",
    )
    ap.add_argument(
        "--live", action="store_true",
        help="Fetch real data (Yahoo Finance + FRED + BoC Valet). "
             "Falls back to static data per-endpoint on any failure.",
    )
    ap.add_argument(
        "--fred-key", default=os.environ.get("FRED_API_KEY"),
        help="FRED API key for U.S. Treasury yields. Defaults to $FRED_API_KEY. "
             "Get one free at https://fred.stlouisfed.org/docs/api/api_key.html",
    )
    ap.add_argument(
        "--refresh", type=float, default=15.0,
        help="UI redraw interval in seconds (default 15). "
             "Does not affect API call cadence — that's governed by per-endpoint TTLs.",
    )
    ap.add_argument(
        "--no-news", action="store_true",
        help="Disable the news feed (skip WSJ/FT/Bloomberg RSS polling).",
    )
    ap.add_argument(
        "--watchlist", default=os.environ.get("WATCHLIST", ""),
        help="Comma-separated tickers used to seed the watchlist on first run "
             "(e.g. 'AAPL,NVDA,TSLA'). Defaults to $WATCHLIST. Ignored if the "
             "watchlist file already exists — use `a` inside the app to manage.",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    watchlist = WatchlistConfig()
    if args.watchlist:
        seed = [t for t in args.watchlist.replace(";", ",").split(",") if t.strip()]
        watchlist.seed_if_empty(seed)

    static = StaticDataProvider()
    if args.live:
        if not args.fred_key:
            print(
                "[macro_monitor] --live enabled without --fred-key / $FRED_API_KEY. "
                "U.S. Treasury yields will show STATIC fallback; everything else goes live.",
                file=sys.stderr,
            )
        provider = LiveDataProvider(
            fallback=static, fred_api_key=args.fred_key, watchlist=watchlist,
        )
    else:
        # Even without --live, the watchlist fetches real quotes: user
        # tickers have no static data, so zeros would be the alternative.
        provider = LiveDataProvider(
            fallback=static, watchlist=watchlist, live_keys=("watchlist",),
        )

    # The news provider always runs live — RSS is keyless and free. Users who
    # want to skip it (offline, low bandwidth, …) can pass --no-news.
    news = NewsProvider(warm_on_init=not args.no_news)
    if args.no_news:
        news.stop()

    try:
        MacroMonitorApp(
            provider=provider, news=news,
            refresh_seconds=args.refresh, watchlist=watchlist,
        ).run()
    finally:
        provider.stop()
        news.stop()


if __name__ == "__main__":
    main()
