"""Exchange trading dates for the supported A-share calendar year.

Update this file from the exchange's annual closure notice before a new year.
Unknown years fail closed so a weekday holiday cannot be treated as a session.
"""

from datetime import date


CALENDAR_SOURCE = (
    "https://www.sse.com.cn/disclosure/announcement/general/"
    "c/c_20251222_10802507.shtml"
)
SHENZHEN_CALENDAR_SOURCE = "https://www.szse.cn/disclosure/notice/t20251222_618087.html"
BEIJING_CALENDAR_SOURCE = "https://www.bse.cn/important_news/200027428.html"

# Shanghai, Shenzhen and Beijing Stock Exchange 2026 notices, 2025-12-22.
# Weekends are handled separately; these are the affected weekdays.
CLOSED_WEEKDAYS = {
    2026: frozenset(
        date.fromisoformat(value)
        for value in (
            "2026-01-01", "2026-01-02",
            "2026-02-16", "2026-02-17", "2026-02-18",
            "2026-02-19", "2026-02-20", "2026-02-23",
            "2026-04-06",
            "2026-05-01", "2026-05-04", "2026-05-05",
            "2026-06-19",
            "2026-09-25",
            "2026-10-01", "2026-10-02", "2026-10-05",
            "2026-10-06", "2026-10-07",
        )
    ),
}


def trading_day_state(day: date) -> bool | None:
    """True for an open day, False for a closure, None for an unknown year."""
    if day.weekday() >= 5:
        return False
    closures = CLOSED_WEEKDAYS.get(day.year)
    if closures is None:
        return None
    return day not in closures
