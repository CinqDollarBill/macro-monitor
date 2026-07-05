"""Macro Monitor — terminal-based macro dashboard.

Package layout:
    models.py    dataclasses for instruments and events
    data.py      DataProvider abstraction (Static + Live stub)
    widgets.py   Rich-based render functions for each panel
    app.py       Textual application and layout
    __main__.py  entrypoint: `python -m macro_monitor`
"""

__version__ = "0.1.0"
