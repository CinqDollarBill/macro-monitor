"""Rich-based renderers for each dashboard panel.

Each `render_*` returns a Rich renderable (Panel/Table/Group) that a Textual
Static widget can display. Keeping rendering in pure functions means the UI
layer is dumb — it just swaps in the latest render every tick.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import EconomicEvent, Instrument, PolicyRate
from .news import NewsItem

# Unicode glyphs chosen to render cleanly in Terminal.app and iTerm2.
UP_ARROW = "▲"
DOWN_ARROW = "▼"
FLAT_ARROW = "●"


def color_for(change_pct: float) -> str:
    """Finance-desk color coding: green up, red down, white flat."""
    if change_pct > 0:
        return "bold green"
    if change_pct < 0:
        return "bold red"
    return "bold white"


def arrow_for(change_pct: float) -> str:
    if change_pct > 0.05:
        return UP_ARROW
    if change_pct < -0.05:
        return DOWN_ARROW
    return FLAT_ARROW


def status_badge(status: Optional[dict]) -> str:
    """Render a small source-indicator badge for a panel title.

    Status shape matches DataProvider.status():
        {"source": "live" | "cache" | "fallback" | "pending" | "static",
         "fetched_at": float | None, "error": str | None}
    """
    if not status:
        return ""
    src = status.get("source", "static")
    ts = status.get("fetched_at")
    if src == "live":
        age = int(time.time() - ts) if ts else 0
        return f"  [bold green]● LIVE[/] [dim](fetched {age}s ago)[/]"
    if src == "cache":
        age = int(time.time() - ts) if ts else 0
        return f"  [bold yellow]◆ CACHED[/] [dim]({age}s old)[/]"
    if src == "fallback":
        return "  [bold red]○ FALLBACK[/]"
    if src == "pending":
        return "  [dim]○ loading…[/]"
    return "  [dim]● STATIC[/]"


def _base_table(compact: bool) -> Table:
    return Table(
        show_header=True,
        header_style="bold yellow",
        show_edge=False,
        expand=True,
        padding=(0, 1),
        box=None,
        pad_edge=False,
        collapse_padding=compact,
    )


def render_instrument_table(
    title: str,
    rows: Iterable[Instrument],
    *,
    compact: bool = False,
    status: Optional[dict] = None,
    empty_hint: Optional[str] = None,
    show_short_term: bool = False,
) -> Panel:
    """Table of instruments with arrow, MTD %, and YTD %.

    `empty_hint` is shown in place of the table when `rows` is empty — used
    to prompt the user to populate an otherwise-empty panel (e.g. watchlist).
    When `show_short_term` is True, Daily % and Weekly % columns are inserted
    before MTD % — used by the watchlist where shorter horizons matter.
    """
    rows_list = list(rows)
    if not rows_list and empty_hint:
        hint = Text.from_markup(empty_hint)
        hint.stylize("dim italic")
        body: object = hint
    else:
        table = _base_table(compact)
        table.add_column("Instrument", style="bold white", no_wrap=True, ratio=3)
        table.add_column("Last", justify="right", ratio=2)
        table.add_column("", justify="center", width=2)
        if show_short_term:
            table.add_column("Daily %", justify="right", ratio=2)
            table.add_column("Weekly %", justify="right", ratio=2)
        table.add_column("MTD %", justify="right", ratio=2)
        table.add_column("YTD %", justify="right", ratio=2)

        for ins in rows_list:
            mtd_color = color_for(ins.change_pct)
            ytd_color = color_for(ins.ytd_pct)
            arrow = arrow_for(ins.change_pct)
            row = [
                Text(ins.name, style="bold white"),
                Text(ins.formatted_value(), style="white"),
                Text(arrow, style=mtd_color),
            ]
            if show_short_term:
                dtd_color = color_for(ins.dtd_pct)
                wtd_color = color_for(ins.wtd_pct)
                row.append(Text(f"{ins.dtd_pct:+.2f}%", style=dtd_color))
                row.append(Text(f"{ins.wtd_pct:+.2f}%", style=wtd_color))
            row.append(Text(f"{ins.change_pct:+.2f}%", style=mtd_color))
            row.append(Text(f"{ins.ytd_pct:+.2f}%", style=ytd_color))
            table.add_row(*row)
        body = table

    return Panel(
        body,
        title=f"[bold yellow]▌ {title.upper()}[/]{status_badge(status)}",
        title_align="left",
        border_style="bright_black",
        padding=(0, 1),
    )


def render_events_panel(events: Iterable[EconomicEvent], today: date, *, compact: bool = False) -> Panel:
    """Economic calendar: sorted by date, color-coded by urgency/importance."""
    table = _base_table(compact)
    table.add_column("Event", style="bold white", ratio=4)
    table.add_column("Category", style="cyan", ratio=2)
    table.add_column("Date", justify="right", ratio=2)
    table.add_column("Countdown", justify="right", ratio=2)

    events = sorted(events, key=lambda e: e.event_date)
    for e in events:
        days = e.days_until(today)
        if days < 0:
            cd = Text(f"{-days}d ago", style="dim")
        elif days == 0:
            cd = Text("TODAY", style="bold yellow on red")
        elif days <= 7:
            cd = Text(f"in {days}d", style="bold yellow")
        else:
            cd = Text(f"in {days}d", style="white")

        marker = "★ " if e.importance == "high" else "  "
        name_style = "bold red" if e.importance == "high" and days >= 0 and days <= 14 else "bold white"
        cat_label = {
            "central_bank": "CENTRAL BANK",
            "release": "RELEASE",
            "speech": "SPEECH",
            "auction": "AUCTION",
        }.get(e.category, e.category.upper())

        table.add_row(
            Text(f"{marker}{e.name}", style=name_style),
            cat_label,
            e.event_date.strftime("%b %d, %Y"),
            cd,
        )

    return Panel(
        table,
        title="[bold yellow]▌ ECONOMIC CALENDAR & UPCOMING CENTRAL BANK MEETINGS[/]",
        title_align="left",
        border_style="bright_black",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Policy rates panel — current Fed/BoC rates + Polymarket probabilities
# ---------------------------------------------------------------------------


def _bar(prob: float, width: int = 18) -> Text:
    """Horizontal bar for a probability 0..1."""
    filled = max(0, min(width, int(round(prob * width))))
    empty = width - filled
    t = Text()
    t.append("█" * filled, style="bold")
    t.append("░" * empty, style="dim")
    return t


def _outcome_style(label: str) -> str:
    if label.startswith("Cut"):
        return "green"
    if label.startswith("Hike"):
        return "red"
    return "yellow"  # Hold


def _bank_block(r: PolicyRate) -> Group:
    """Vertically stacked lines for one central bank — goes into a column cell."""
    lines: list = []
    rate_str = (
        f"{r.rate_low:.2f}%"
        if r.rate_high == r.rate_low
        else f"{r.rate_low:.2f}% – {r.rate_high:.2f}%"
    )
    meeting_str = r.meeting_date.strftime("%b %d, %Y").upper() if r.meeting_date else "—"

    header = Text()
    header.append(r.bank, style="bold yellow")
    header.append(f"   {rate_str}", style="bold white")
    header.append("    NEXT MEETING: ", style="dim")
    header.append(meeting_str, style="bold white")
    lines.append(header)

    if not r.outcomes:
        lines.append(Text("  (no market-implied probabilities)", style="dim italic"))
        return Group(*lines)

    top_prob = max(o.probability for o in r.outcomes)
    for o in r.outcomes:
        base = _outcome_style(o.label)
        is_consensus = o.probability == top_prob
        label_style = f"bold {base}" if is_consensus else base
        row = Text()
        row.append(f"  {o.label:<13s}", style=label_style)
        bar = _bar(o.probability, width=14)
        bar.stylize(label_style)
        row.append_text(bar)
        row.append(f"  {o.probability * 100:>5.1f}%", style=label_style)
        if is_consensus:
            row.append("  ◂", style="dim yellow")
        lines.append(row)
    return Group(*lines)


def render_policy_panel(
    rates: Iterable[PolicyRate],
    *,
    status: Optional[dict] = None,
) -> Panel:
    """Full-width panel with one column per central bank, laid out horizontally."""
    rates_list = list(rates)
    if not rates_list:
        body: object = Text("no policy rate data", style="dim")
    else:
        table = Table(show_header=False, expand=True, box=None, padding=(0, 2), pad_edge=False)
        for _ in rates_list:
            table.add_column(ratio=1, no_wrap=False)
        table.add_row(*[_bank_block(r) for r in rates_list])
        body = table

    title = f"[bold yellow]▌ POLICY RATES[/]{status_badge(status)}"
    return Panel(
        body,
        title=title, title_align="left",
        border_style="bright_black", padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# News panel — latest headlines from WSJ / FT / Bloomberg RSS
# ---------------------------------------------------------------------------


_SOURCE_STYLES = {
    "WSJ": "bold magenta",
    "FT":  "bold #ffb07c",
    "BBG": "bold cyan",
}


def _relative_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (now - dt).total_seconds()
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def render_news(
    items: Iterable[NewsItem],
    *,
    status: Optional[dict] = None,
    limit: int = 14,
) -> Panel:
    table = _base_table(compact=True)
    table.add_column("Age",  width=4, no_wrap=True, style="dim", justify="right")
    table.add_column("Src",  width=3, no_wrap=True)
    table.add_column("Headline", ratio=1, no_wrap=False, overflow="ellipsis")

    rows = list(items)[:limit]
    if not rows:
        table.add_row(Text("—"), Text("—"), Text("loading headlines…", style="dim"))

    for it in rows:
        src_style = _SOURCE_STYLES.get(it.source, "bold white")
        table.add_row(
            Text(_relative_time(it.pub_time)),
            Text(it.source, style=src_style),
            Text(it.title, style="white"),
        )

    title = f"[bold yellow]▌ MARKETS NEWS[/]{status_badge(status)}"
    return Panel(
        table,
        title=title, title_align="left",
        border_style="bright_black", padding=(0, 1),
    )
