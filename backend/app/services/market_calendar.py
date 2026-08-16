"""Trading hours per asset class: when a market is open, and what a session is.

Two questions need answering that a live `market_open` flag cannot: *when* does
this market open again, and *which* moment is "two trading sessions from now".
Both come from `exchange_calendars`, which knows holidays, Good Friday and
early closes. Twelve Data stays the source of truth for whether a market is
open right now — it sees halts and unscheduled closures the calendar cannot.

This module is deliberately a thin facade over the library. The rest of the
backend works in `Decimal` and plain `datetime`; nothing outside this file
touches pandas, so the dependency stays replaceable.

Sessions:
- stocks and funds follow XNYS (NYSE), roughly 09:30-16:00 ET on weekdays
- commodities follow the 24/5 forex calendar: continuous Monday to Friday
- crypto never closes, so a "session" is simply a 24-hour day
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("berebank.calendar")

# Asset class -> exchange_calendars name. Anything absent is treated as 24/7.
CALENDAR_NAMES: dict[str, str] = {
    "stock": "XNYS",
    "fund": "XNYS",
    "commodity": "24/5",
}

# Calendars are built for a bounded window. Keep two years of headroom and
# rebuild well before the end so expiry dates never fall off the edge.
_CALENDAR_YEARS_AHEAD = 2
_REBUILD_MARGIN = timedelta(days=90)

_calendars: dict[str, object] = {}
_lock = threading.Lock()


class CalendarUnavailable(Exception):
    """The calendar cannot answer for this moment (outside its window)."""


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _to_datetime(value) -> datetime | None:
    """pandas Timestamp -> aware UTC datetime."""
    if value is None:
        return None
    return _utc(value.to_pydatetime())


def _get_calendar(name: str, needed: datetime):
    """A cached calendar guaranteed to cover `needed`."""
    import exchange_calendars as xcals
    import pandas as pd

    with _lock:
        calendar = _calendars.get(name)
        if calendar is not None:
            last = _to_datetime(calendar.last_session)
            if last is not None and needed + _REBUILD_MARGIN < last:
                return calendar
        end = pd.Timestamp(
            _utc(None).replace(year=_utc(None).year + _CALENDAR_YEARS_AHEAD).date()
        )
        needed_end = pd.Timestamp((needed + _REBUILD_MARGIN * 2).date())
        calendar = xcals.get_calendar(name, end=max(end, needed_end))
        _calendars[name] = calendar
        return calendar


def calendar_name(asset_class: str | None) -> str | None:
    """The calendar for this asset class, or None when it never closes."""
    return CALENDAR_NAMES.get(asset_class or "")


def always_open(asset_class: str | None) -> bool:
    return calendar_name(asset_class) is None


def session_state(asset_class: str | None, now: datetime | None = None) -> dict:
    """Opening hours for an asset class as data rather than a snapshot.

    `is_open` follows the calendar and so does not know about halts; callers
    that have a live feed should prefer its flag and use these timestamps for
    the schedule. Crypto reports `always_open` with no timestamps, because
    "next open" has no meaning for a market that never closes.
    """
    now = _utc(now)
    name = calendar_name(asset_class)
    state: dict = {
        "asset_class": asset_class,
        "calendar": name,
        "always_open": name is None,
        "is_open": True,
        "timezone": "UTC",
        "next_open": None,
        "next_close": None,
        "current_session_end": None,
    }
    if name is None:
        return state

    try:
        calendar = _get_calendar(name, now)
        import pandas as pd

        stamp = pd.Timestamp(now)
        is_open = bool(calendar.is_open_on_minute(stamp))
        next_close = _to_datetime(calendar.next_close(stamp))
        state.update({
            "is_open": is_open,
            "timezone": str(calendar.tz),
            "next_open": None if is_open else _to_datetime(calendar.next_open(stamp)),
            "next_close": next_close,
            "current_session_end": next_close if is_open else None,
        })
    except Exception as exc:
        logger.warning("Calendar %s could not answer for %s: %s", name, now, exc)
    return state


_DISAGREEMENT_INTERVAL = timedelta(minutes=15)
_last_disagreement: dict[str, datetime] = {}


def note_disagreement(asset_class: str | None, live_open: bool | None) -> bool:
    """Log when the live feed and the calendar disagree about being open.

    A real halt or an unscheduled closure shows up here, and so does a missing
    `is_market_open` field — Twelve Data omitting it reads as closed. Throttled
    per asset class so a market list does not flood the log.
    """
    if live_open is None or always_open(asset_class):
        return False
    calendar_open = session_state(asset_class)["is_open"]
    if calendar_open == live_open:
        return False
    key = asset_class or ""
    now = _utc(None)
    previous = _last_disagreement.get(key)
    if previous is None or now - previous >= _DISAGREEMENT_INTERVAL:
        _last_disagreement[key] = now
        logger.warning(
            "Market hours disagree for %s: live feed says %s, %s calendar says %s",
            asset_class,
            "open" if live_open else "closed",
            calendar_name(asset_class),
            "open" if calendar_open else "closed",
        )
    return True


def _session_closes(calendar, start: datetime, count: int) -> list[datetime]:
    """The closes of the next `count` sessions ending strictly after `start`."""
    import pandas as pd

    # A session can only close after its own date, so looking back one day is
    # enough to catch a session that is still running.
    first = pd.Timestamp((start - timedelta(days=1)).date())
    # Weekends, holidays and a long break all shrink sessions per day; two
    # calendar days per session is a safe over-estimate.
    last = pd.Timestamp((start + timedelta(days=max(count, 1) * 3 + 10)).date())
    closes: list[datetime] = []
    for session in calendar.sessions_in_range(first, min(last, calendar.last_session)):
        close = _to_datetime(calendar.session_close(session))
        if close is not None and close > start:
            closes.append(close)
            if len(closes) >= count:
                break
    return closes


def advance_sessions(
    asset_class: str | None, sessions: int, start: datetime | None = None
) -> datetime:
    """The close of the `sessions`-th trading session ending after `start`.

    This is what "expire after two trading sessions" means: a NYSE order placed
    on Saturday with `sessions=2` runs out at Tuesday's close, not forty hours
    later in wall-clock time. For crypto, which never closes, a session is a
    24-hour day.
    """
    if sessions < 1:
        raise ValueError("sessions must be at least 1")
    start = _utc(start)
    name = calendar_name(asset_class)
    if name is None:
        return start + timedelta(days=sessions)

    calendar = _get_calendar(name, start + timedelta(days=sessions * 3 + 10))
    closes = _session_closes(calendar, start, sessions)
    if len(closes) < sessions:
        raise CalendarUnavailable(
            f"{name} has no {sessions} sessions available after {start.isoformat()}"
        )
    return closes[sessions - 1]


def sessions_between(
    asset_class: str | None, start: datetime, end: datetime | None = None
) -> int:
    """How many trading sessions closed in (start, end].

    Answers "how much trading time passed since my last run", which wall-clock
    hours cannot: a Friday-evening to Monday-morning gap is zero sessions.
    """
    start, end = _utc(start), _utc(end)
    if end <= start:
        return 0
    name = calendar_name(asset_class)
    if name is None:
        return int((end - start) // timedelta(days=1))

    try:
        calendar = _get_calendar(name, end)
        import pandas as pd

        first = pd.Timestamp((start - timedelta(days=1)).date())
        last = pd.Timestamp(min(end, _to_datetime(calendar.last_session)).date())
        if last < first:
            return 0
        return sum(
            1
            for session in calendar.sessions_in_range(first, last)
            if start < (_to_datetime(calendar.session_close(session)) or start) <= end
        )
    except Exception as exc:
        logger.warning("Calendar %s could not count sessions: %s", name, exc)
        return 0
