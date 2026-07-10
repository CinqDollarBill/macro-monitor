"""Textual application layer.

Layout (top to bottom):
    TopBar               — title, clock, data-mode health
    Tables row           — [Watchlist / Indexes / Commodities / FX / Crypto]  |  [UST / GoC]
    Policy panel         — Fed / BoC current rate + Polymarket probabilities
    News row             — WSJ / FT / Bloomberg headlines
    Events calendar      — economic calendar + upcoming central bank meetings
    Footer               — keybinding hints

Press `a` to open the watchlist manager (search/add/remove tickers).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable, Optional

from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from . import widgets as W
from .data import LIVE_CAPABLE_KEYS, DataProvider, LiveDataProvider, StaticDataProvider
from .models import Instrument
from .news import NewsProvider
from .watchlist import WatchlistConfig, search_yahoo


class DataPanel(Static):
    """Static widget whose content is produced by a render callable."""

    def __init__(self, render_fn: Callable[[], RenderableType], **kwargs):
        super().__init__("", **kwargs)
        self._render_fn = render_fn

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        try:
            self.update(self._render_fn())
        except Exception as exc:  # surfaces render errors instead of crashing the app
            self.update(f"[red]render error:[/] {exc!r}")


class TopBar(Static):
    """Header line: title, live clock, data-mode indicator, health summary."""

    mode_label = reactive("STATIC")
    health = reactive("")

    def on_mount(self) -> None:
        self.refresh_content()

    def refresh_content(self) -> None:
        now = datetime.now().strftime("%a %b %d, %Y  %H:%M:%S")
        sep = "[#444] │ [/]"
        mode_color = "bold green" if self.mode_label == "LIVE" else "bold #00d7af"
        parts = [
            "[bold yellow on #1a1a00]  MACRO MONITOR  [/]",
            f"[white]{now}[/]",
            f"[{mode_color}]DATA MODE:[/] [bold white]{self.mode_label}[/]",
        ]
        if self.health:
            parts.append(self.health)
        else:
            parts.append("[dim]READY FOR LIVE FEEDS[/]")
        self.update(sep.join(parts))


_HINT_DEFAULT = "[bold]enter[/]=add  [bold]↑↓[/]=navigate list  [bold]esc[/]=close"


class WatchlistScreen(ModalScreen[None]):
    """Modal: add/remove watchlist tickers.

    Primary flow: type a symbol, press Enter. We validate by fetching it via
    Yahoo → yfinance; if the fetch succeeds the ticker is added, otherwise
    the hint line shows a red error so bad symbols don't silently pollute the
    watchlist. Yahoo's company-name search is shown as suggestions when it
    works, but it's opportunistic — direct ticker entry is always available.

    With the input empty, the list shows the current watchlist; Enter on a
    row removes it.
    """

    CSS = """
    WatchlistScreen {
        align: center middle;
    }
    #wl-box {
        width: 70;
        height: 24;
        background: #0a0a0a;
        border: tall #333;
        padding: 0 1;
    }
    #wl-title {
        background: #1a1a00;
        color: yellow;
        text-style: bold;
        width: 100%;
        padding: 0 1;
        margin-bottom: 1;
    }
    #wl-current {
        color: #888;
        padding: 0 1;
        margin-bottom: 1;
    }
    #wl-search {
        margin: 0 0 1 0;
    }
    #wl-results {
        height: 1fr;
        border: none;
        background: #050505;
    }
    #wl-hint {
        color: #888;
        padding: 0 1;
    }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, watchlist: WatchlistConfig, on_change: Optional[Callable[[str, str], None]] = None):
        super().__init__()
        self.watchlist = watchlist
        self.on_change = on_change
        self._results: list[dict] = []
        self._query: str = ""
        self._search_timer = None
        self._mode: str = "current"  # 'current' | 'search'

    def compose(self) -> ComposeResult:
        with Vertical(id="wl-box"):
            yield Label("▌ WATCHLIST MANAGER", id="wl-title")
            yield Label(self._current_str(), id="wl-current")
            yield Input(placeholder="Type a ticker (e.g. AAPL) or company name…", id="wl-search")
            yield ListView(id="wl-results")
            yield Label(_HINT_DEFAULT, id="wl-hint")

    def _current_str(self) -> str:
        t = self.watchlist.tickers()
        if not t:
            return "Current watchlist: [dim](empty — type a ticker and press enter)[/]"
        return "Current watchlist: [bold white]" + ", ".join(t) + "[/]"

    def on_mount(self) -> None:
        self.query_one("#wl-search", Input).focus()
        self._populate()

    # ---- list population ----

    def _populate(self) -> None:
        """Refresh the ListView for the current mode."""
        lv = self.query_one("#wl-results", ListView)
        lv.clear()
        if self._mode == "current":
            tickers = self.watchlist.tickers()
            if not tickers:
                lv.append(ListItem(Label(
                    "[dim italic]no tickers yet — type a symbol above and press enter[/]"
                )))
                return
            for t in tickers:
                lv.append(ListItem(Label(
                    f"[bold white]{t:<10}[/]  [dim red]× enter to remove[/]"
                )))
        else:
            # First row is always "add literal query" so Enter Just Works
            # even when search returns nothing.
            q_upper = self._query.upper()
            lv.append(ListItem(Label(
                f"[bold green]+ ADD[/] [bold white]{q_upper}[/]  "
                f"[dim](press enter to pull from Yahoo / yfinance)[/]"
            )))
            for r in self._results:
                sym = r["symbol"]
                name = (r["name"] or "")[:32]
                typ = (r["type"] or "?").lower()
                exch = r["exchange"] or ""
                lv.append(ListItem(Label(
                    f"[bold white]{sym:<10}[/] [white]{name:<32}[/] "
                    f"[dim]{typ:<7} {exch}[/]"
                )))

    def _refresh_current_label(self) -> None:
        self.query_one("#wl-current", Label).update(self._current_str())

    def _show_hint(self, markup: str) -> None:
        try:
            self.query_one("#wl-hint", Label).update(markup)
        except Exception:
            pass

    # ---- input handlers ----

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip()
        self._query = query
        if self._search_timer is not None:
            try:
                self._search_timer.stop()
            except Exception:
                pass
            self._search_timer = None

        if not query:
            self._mode = "current"
            self._results = []
            self._populate()
            self._show_hint(_HINT_DEFAULT)
            return

        # Show the "ADD literal" row immediately so the user never has to
        # wait on the network to proceed.
        self._mode = "search"
        self._results = []
        self._populate()
        self._show_hint(_HINT_DEFAULT)
        # Debounce: wait 0.3s of idle typing before hitting the network.
        self._search_timer = self.set_timer(0.3, self._kickoff_search)

    def _kickoff_search(self) -> None:
        q = self._query
        if not q:
            return
        threading.Thread(
            target=self._search_worker, args=(q,), daemon=True,
        ).start()

    def _search_worker(self, q: str) -> None:
        try:
            results = search_yahoo(q, limit=8)
        except Exception:
            results = []
        # UI mutation must happen on the event loop thread.
        self.app.call_from_thread(self._apply_results, q, results)

    def _apply_results(self, q: str, results: list[dict]) -> None:
        if q != self._query:
            return  # a newer query is already in flight
        self._mode = "search"
        self._results = results
        self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter in the input always adds the literal typed value. Search
        # results exist only so the user can pick a specific exchange — if
        # they don't care, typing + Enter is the fast path.
        if self._query:
            self._add(self._query)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is None:
            return
        if self._mode == "current":
            tickers = self.watchlist.tickers()
            if 0 <= idx < len(tickers):
                self._remove(tickers[idx])
        else:
            if idx == 0:
                # "+ ADD <literal>" row
                self._add(self._query)
            else:
                ri = idx - 1
                if 0 <= ri < len(self._results):
                    self._add(self._results[ri]["symbol"])

    # ---- mutations ----

    def _add(self, symbol: str) -> None:
        """Validate the ticker in the background; only add on fetch success."""
        sym = symbol.strip().upper()
        if not sym:
            return
        if sym in self.watchlist.tickers():
            self._show_hint(f"[yellow]{sym} is already in the watchlist[/]")
            return
        self._show_hint(f"[dim]fetching {sym} from Yahoo/yfinance…[/]")
        threading.Thread(
            target=self._validate_and_add_worker, args=(sym,), daemon=True,
        ).start()

    def _validate_and_add_worker(self, sym: str) -> None:
        # Lazy import to keep the modal module free of data-layer internals
        # at import time.
        from .data import _YahooFetcher
        try:
            _YahooFetcher().quote(sym)
        except Exception as exc:
            msg = str(exc).splitlines()[0][:80] or "unknown error"
            self.app.call_from_thread(
                self._show_hint, f"[red]couldn't fetch {sym}: {msg}[/]"
            )
            return
        self.app.call_from_thread(self._finalize_add, sym)

    def _finalize_add(self, sym: str) -> None:
        added = self.watchlist.add(sym)
        if added and self.on_change:
            self.on_change(sym, "add")
        self._refresh_current_label()
        inp = self.query_one("#wl-search", Input)
        inp.value = ""
        inp.focus()
        self._query = ""
        self._mode = "current"
        self._results = []
        self._populate()
        self._show_hint(f"[green]✓ added {sym}[/]")

    def _remove(self, symbol: str) -> None:
        removed = self.watchlist.remove(symbol)
        if removed and self.on_change:
            self.on_change(symbol, "remove")
        self._refresh_current_label()
        self._populate()
        if removed:
            self._show_hint(f"[green]✓ removed {symbol}[/]")


class MacroMonitorApp(App):
    """Main Textual app."""

    CSS = """
    Screen {
        background: #050505;
    }

    #topbar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: #0d0d0d;
        color: white;
    }

    #tables {
        height: auto;
    }

    /* height: auto so these size to their children; `100%` collapses to 0
       when the parent #tables is also auto. */
    #left, #right {
        width: 1fr;
        height: auto;
    }

    .panel {
        background: #050505;
        height: auto;
        margin: 0 1;
    }

    /* Watchlist sits in its own full-width band above the two-column tables
       so a long ticker list doesn't unbalance the columns below. */
    #p_watch {
        width: 1fr;
        height: auto;
        margin: 0 1;
    }

    #p_news {
        height: 1fr;
        min-height: 10;
        margin: 0 1;
    }

    #p_events {
        height: auto;
        margin: 0 1;
    }

    Footer {
        background: #0d0d0d;
        color: #888;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("c", "toggle_compact", "Compact"),
        Binding("a", "manage_watchlist", "Watchlist"),
    ]

    compact_mode = reactive(False)

    def __init__(
        self,
        provider: DataProvider | None = None,
        news: NewsProvider | None = None,
        refresh_seconds: float = 15.0,
        watchlist: WatchlistConfig | None = None,
    ):
        super().__init__()
        self.provider: DataProvider = provider or StaticDataProvider()
        self.news: NewsProvider = news or NewsProvider()
        self.refresh_seconds = refresh_seconds
        # The watchlist lives on the LiveDataProvider when available (so the
        # warmer can see it) — fall back to a standalone config otherwise.
        self.watchlist = (
            getattr(self.provider, "watchlist", None) or watchlist or WatchlistConfig()
        )

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")

        yield DataPanel(
            lambda: W.render_instrument_table(
                self._watchlist_title(), self._watchlist_rows(),
                compact=self.compact_mode,
                status=self.provider.status("watchlist"),
                empty_hint="press [bold]a[/] to add tickers",
                show_short_term=True,
            ),
            id="p_watch",
        )

        with Horizontal(id="tables"):
            with Vertical(id="left"):
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "Indexes", self.provider.get_indexes(),
                        compact=self.compact_mode,
                        status=self.provider.status("indexes"),
                    ),
                    classes="panel", id="p_eq",
                )
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "Commodities", self.provider.get_commodities(),
                        compact=self.compact_mode,
                        status=self.provider.status("commodities"),
                    ),
                    classes="panel", id="p_co",
                )
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "FX", self.provider.get_fx(),
                        compact=self.compact_mode,
                        status=self.provider.status("fx"),
                    ),
                    classes="panel", id="p_fx",
                )
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "Crypto", self.provider.get_crypto(),
                        compact=self.compact_mode,
                        status=self.provider.status("crypto"),
                    ),
                    classes="panel", id="p_crypto",
                )
            with Vertical(id="right"):
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "U.S. Treasury Curve", self.provider.get_us_rates(),
                        compact=self.compact_mode,
                        status=self.provider.status("us_rates"),
                    ),
                    classes="panel", id="p_ust",
                )
                yield DataPanel(
                    lambda: W.render_instrument_table(
                        "Government of Canada Curve", self.provider.get_canada_rates(),
                        compact=self.compact_mode,
                        status=self.provider.status("canada_rates"),
                    ),
                    classes="panel", id="p_goc",
                )

        yield DataPanel(
            lambda: W.render_policy_panel(
                self.provider.get_policy_rates(),
                status=self.provider.status("policy_rates"),
            ),
            classes="panel", id="p_policy",
        )

        yield DataPanel(
            lambda: W.render_news(self.news.items(), status=self.news.status()),
            id="p_news",
        )

        yield DataPanel(
            lambda: W.render_events_panel(
                self.provider.get_events(), datetime.now().date(), compact=self.compact_mode
            ),
            classes="panel", id="p_events",
        )
        yield Footer()

    def _watchlist_title(self) -> str:
        n = len(self.watchlist.tickers())
        return f"Watchlist ({n})" if n else "Watchlist"

    def _watchlist_rows(self) -> list[Instrument]:
        """Provider rows, padded with placeholders for tickers the provider
        doesn't know about (happens in static mode or before the first fetch).
        Output is ordered to match the on-disk watchlist.
        """
        rows = list(self.provider.get_watchlist())
        known = {r.name.upper() for r in rows}
        for t in self.watchlist.tickers():
            if t not in known:
                rows.append(Instrument(t, 0.0, 0.0, "", "watchlist"))
        order = {t: i for i, t in enumerate(self.watchlist.tickers())}
        rows.sort(key=lambda r: order.get(r.name.upper(), 10**9))
        return rows

    # ---------- lifecycle ----------

    def on_mount(self) -> None:
        # Clock ticks every second; data panels re-render every `refresh_seconds`.
        # The provider and news warmers refresh their underlying caches
        # independently on background threads, so UI ticks are pure redraws.
        self.set_interval(1.0, self._tick_clock)
        self.set_interval(self.refresh_seconds, self._tick_data)
        # News ticks at its own slower cadence.
        self.set_interval(30.0, self._tick_news)

    def _tick_clock(self) -> None:
        tb = self.query_one(TopBar)
        tb.mode_label = self.provider.mode_label
        tb.health = self._health_summary()
        tb.refresh_content()

    def _tick_data(self) -> None:
        for panel in self.query(DataPanel):
            panel.refresh_content()
        self._tick_clock()

    def _tick_news(self) -> None:
        news_panel = self.query_one("#p_news", DataPanel)
        news_panel.refresh_content()

    def _health_summary(self) -> str:
        """Per-endpoint source counts for the topbar."""
        if not isinstance(self.provider, LiveDataProvider):
            return ""
        counts = {"live": 0, "cache": 0, "fallback": 0, "pending": 0}
        # Watchlist status only counts when the user has tickers configured,
        # and keys the provider isn't fetching live don't count at all (in
        # static mode only the watchlist is live).
        has_watchlist = bool(self.watchlist.tickers())
        keys = [
            k for k in LIVE_CAPABLE_KEYS
            if k in self.provider._live_keys and (k != "watchlist" or has_watchlist)
        ]
        if not keys:
            return ""
        for key in keys:
            src = self.provider.status(key).get("source", "pending")
            if src in counts:
                counts[src] += 1
        total = len(keys)
        color = "green" if counts["live"] == total else ("yellow" if counts["fallback"] == 0 else "red")
        bits = [f"[bold {color}]{counts['live']}/{total} live[/]"]
        if counts["cache"]:
            bits.append(f"[yellow]{counts['cache']} cached[/]")
        if counts["fallback"]:
            bits.append(f"[red]{counts['fallback']} fallback[/]")
        if counts["pending"]:
            bits.append(f"[dim]{counts['pending']} loading[/]")
        return "  ".join(bits)

    # ---------- actions ----------

    def action_refresh_now(self) -> None:
        if isinstance(self.provider, LiveDataProvider):
            for key in LIVE_CAPABLE_KEYS:
                if key in self.provider._disabled:
                    continue
                try:
                    self.provider._refresh(key, force=True)
                except Exception:
                    pass
        # force a news refresh too
        try:
            self.news._refresh()
        except Exception:
            pass
        self._tick_data()
        self._tick_clock()

    def action_toggle_compact(self) -> None:
        self.compact_mode = not self.compact_mode
        self._tick_data()

    def action_manage_watchlist(self) -> None:
        self.push_screen(WatchlistScreen(self.watchlist, on_change=self._on_watchlist_change))

    def _on_watchlist_change(self, symbol: str, action: str) -> None:
        """Called from the modal after a ticker is added/removed."""
        if isinstance(self.provider, LiveDataProvider):
            if action == "add":
                self.provider.prime_ticker(symbol)
            elif action == "remove":
                self.provider.drop_ticker(symbol)
        # Redraw the watchlist panel immediately so the row count updates.
        try:
            self.query_one("#p_watch", DataPanel).refresh_content()
        except Exception:
            pass
