# MACRO MONITOR

A terminal-based macro/markets dashboard for macOS — a lightweight Bloomberg-style
monitor showing a user-defined equity watchlist, indexes, commodities, FX,
sovereign yield curves, and a live-countdown economic calendar. Every row shows
both MTD and YTD % change. Built with Python + [Textual](https://textual.textualize.io/)
and [Rich](https://rich.readthedocs.io/).

```
┌────────────────────────────────────────────────────────────────────────────┐
│  MACRO MONITOR │ Fri Apr 17, 2026 14:32:04 │ DATA MODE: STATIC │ LIVE READY│
├──────────────────────────────────┬─────────────────────────────────────────┤
│ ▌ INDEXES                        │ ▌ U.S. TREASURY CURVE                   │
│ S&P 500     6,582.00  ▼  -3.20%  │ 2Y UST    3.805%  ▲  +9.56% ▁▃▄▅▆▇█    │
│ Dow Jones  46,504.00  ▼  -4.10%  │ 10Y UST   4.309%  ▲  +6.76% ▂▃▄▅▆▇█    │
│ ...                              │ ...                                     │
├──────────────────────────────────┴─────────────────────────────────────────┤
│ ▌ ECONOMIC CALENDAR & UPCOMING CENTRAL BANK MEETINGS                       │
│ ★ FOMC Meeting (Day 2) + Statement   CENTRAL BANK  Apr 29, 2026  in 12d    │
└────────────────────────────────────────────────────────────────────────────┘
```

## File layout

```
terminal/
├── requirements.txt
├── run.py                    # convenience launcher
├── start.sh                  # shell launcher: venv setup + .env + run
├── Macro Monitor.command     # macOS double-click launcher (see Quick start)
├── llms.txt                  # condensed project guide for AI assistants
├── README.md
└── macro_monitor/
    ├── __init__.py
    ├── __main__.py           # `python -m macro_monitor`
    ├── models.py             # Instrument / RatesInstrument / EconomicEvent
    ├── data.py               # DataProvider ABC + Static + Live stub
    ├── widgets.py            # Rich render functions (tables, sparklines)
    ├── watchlist.py          # User watchlist persistence + Yahoo search
    └── app.py                # Textual app, layout, keybindings
```

The app is split into three clean layers so swapping to a live feed is
mechanical:

- **data.py** — `DataProvider` ABC. `StaticDataProvider` ships the demo
  numbers; `LiveDataProvider` is a stub with integration notes for yfinance,
  FRED, Alpha Vantage, Polygon, and Bank of Canada Valet.
- **widgets.py** — Pure render functions producing Rich renderables. No
  knowledge of Textual.
- **app.py** — Textual layout, timers, and keybindings.

## Quick start (no coding needed)

1. On the GitHub page, click the green **Code** button → **Download ZIP**,
   and unzip it.
2. Double-click **`Macro Monitor.command`** in the unzipped folder.
   - First time only: macOS may block a file downloaded from the internet —
     right-click (Control-click) the file and choose **Open**, then **Open**
     again in the dialog.
   - If Python 3 isn't installed yet, macOS pops up an installer — click
     **Install**, wait for it to finish, then double-click the launcher again.
3. The first launch takes a minute to set itself up, then the dashboard opens
   with live data. Press `a` to add stocks to your watchlist, `q` to quit.

## Install (macOS)

Requires Python 3.10+.

```bash
cd ~/Documents/terminal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

### Static demo

```bash
python -m macro_monitor
```

Panels show canned demo data. The watchlist is the exception — user tickers
have no demo values, so watchlist quotes always fetch live (the only network
calls in this mode; an empty watchlist makes no requests).

### Live data

Easiest path — use the launcher, which sources `.env` automatically:

```bash
./start.sh --live
```

Put your FRED key in `.env` (file is `chmod 600` and gitignored):

```
export FRED_API_KEY=your_fred_key_here       # free at fred.stlouisfed.org
```

Or skip the launcher and do it manually:

```bash
export FRED_API_KEY=xxx
python -m macro_monitor --live
```

Without a FRED key, `--live` still works — UST yields just show `FALLBACK`
while Yahoo + BoC run live.

CLI flags:

| flag                 | default              | purpose                                                   |
|----------------------|----------------------|-----------------------------------------------------------|
| `--live`             | off (static)         | Enable live data feeds                                    |
| `--fred-key KEY`     | `$FRED_API_KEY`      | FRED API key for U.S. Treasury yields                     |
| `--refresh SECONDS`  | `15`                 | UI redraw interval (API cadence is governed by TTLs)      |
| `--watchlist LIST`   | `$WATCHLIST`         | Comma-separated tickers to seed the watchlist on first run (e.g. `AAPL,NVDA,TSLA`). Ignored once the watchlist file exists — use `a` inside the app instead. |

Maximize your Terminal.app / iTerm2 window for the cleanest layout — the panels
auto-size but the dashboard is designed for ~120 cols × 40 rows minimum.

## Keyboard shortcuts

| key | action                                                |
|-----|-------------------------------------------------------|
| `q` | Quit                                                  |
| `r` | Force data refresh (bypasses TTL and forces a fetch)  |
| `c` | Toggle compact view                                   |
| `a` | Open the watchlist manager (search, add, remove)      |

### Watchlist

Press `a` to open the watchlist manager. Start typing a ticker or company name
— Yahoo's search endpoint returns live matches; arrow-navigate and hit Enter
to add. With the input empty, the list shows your current tickers — Enter on a
row removes it. The watchlist persists to `~/.config/macro_monitor/watchlist.txt`
(one symbol per line) so it survives restarts. Watchlist quotes are fetched
live even without `--live` — the panel's badge shows its own source state.

### Copy-paste

Textual captures the mouse for the dashboard's interactivity, which disables
native click-drag text selection. To bypass it:

- **Hold ⌥ (Option) while dragging** to select text in Terminal.app or iTerm2.
  This is an OS-level override that works in any terminal app that tracks the
  mouse.
- Copy normally with ⌘C afterwards.

## Live mode — architecture

`LiveDataProvider` layers three safety nets over raw HTTP fetches so the UI is
both real-time and crash-proof:

1. **TTL cache** per endpoint — prices 60s, yields 300s. The UI reads from the
   cache only, so render is always instant.
2. **Background warmer thread** keeps the cache fresh off the UI thread. First
   render shows static fallback; live data swaps in within a few seconds.
3. **Per-endpoint fallback** — any fetch failure degrades that single panel to
   static data. Other panels keep ticking live.

Each panel's title shows its current state as a colored badge:

| badge                 | meaning                                                    |
|-----------------------|------------------------------------------------------------|
| `● LIVE (fetched Ns ago)` | Last fetch succeeded within TTL                       |
| `◆ CACHED (Ns old)`       | Fetch failed, showing last successful cache           |
| `○ FALLBACK`              | No cache ever landed; showing static seed data        |
| `○ loading…`              | Warmer hasn't completed its first pass yet            |
| `● STATIC`                | Running with `StaticDataProvider` (no `--live` flag)  |

The topbar summarises the whole dashboard, e.g. `4/5 live  1 fallback`.

## Data feeds used

| domain                          | source                              | key required |
|---------------------------------|-------------------------------------|--------------|
| Indexes, commodities, FX        | Yahoo `query1` → yfinance fallback  | no           |
| Crypto (BTC, ETH)               | Binance `/api/v3/klines`            | no           |
| U.S. Treasury yields 2/5/10/30Y | [FRED](https://fred.stlouisfed.org/) series `DGS{2,5,10,30}` | yes (free) |
| Government of Canada yields     | [Bank of Canada Valet](https://www.bankofcanada.ca/valet/docs) | no |
| Economic calendar               | Static (demo)                       | —            |

Yahoo has a two-tier fetch chain: first a direct GET to `query1.finance.yahoo.com`
(stdlib urllib, no deps), and on any failure (typically HTTP 429 rate limits)
it falls back to the `yfinance` library which carries its own session
management. Both returning errors degrades the panel to static fallback.
FRED and BoC fetchers use `urllib` only.

### Adding a new feed

1. Add a method `_fetch_<key>` in `LiveDataProvider` that returns the relevant
   dataclass list and raises on failure.
2. Register it in the `_refresh` dispatcher, add a TTL in `TTLS`, and list the
   key in `LIVE_CAPABLE_KEYS` (`data.py`).
3. If the key needs the UI to display a status badge, wire
   `status=self.provider.status("<key>")` into the matching `DataPanel` in
   `app.py`.
4. Ship a `_fetch_*` failure test case by temporarily pointing the fetcher at
   an invalid URL and confirming the panel shows `FALLBACK`.

### Polygon / Alpha Vantage / Tiingo

For production-grade intraday data (Yahoo's public endpoint rate-limits
aggressively), swap `_YahooFetcher` for a paid provider:

- **Polygon**: `https://api.polygon.io/v2/aggs/ticker/{sym}/prev?apiKey=...`
- **Alpha Vantage**: `https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={sym}&apikey=...`
- **Tiingo**: `https://api.tiingo.com/iex/{sym}/prices?token=...`

The calling code in `_fetch_indexes` / `_fetch_fx` / `_fetch_commodities`
only needs the `(last, mtd_pct, ytd_pct, trail)` tuple — drop-in replaceable.

## Design notes

- **Color discipline**: green = up, red = down, yellow = headers & urgent
  countdowns, cyan = categorical tags, dim gray = borders / past events.
- **Formatting**: FX crosses at 4 decimals (JPY pairs at 2), yields at 3
  decimals with `%`, indexes/commodities with comma grouping.
- **Sparklines** use a real price trail when available, falling back to a
  magnitude bar from the monthly % change so no cell is ever blank.
- **Refresh cadence**: clock ticks every 1s; data refreshes every 15s by
  default. With static data this is a no-op; with live data it throttles API
  calls appropriately. Manual refresh via `r`.
