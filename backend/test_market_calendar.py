"""Standalone verification of the trading-calendar facade.

Run: .venv\\Scripts\\python test_market_calendar.py
"""
from datetime import datetime, timedelta, timezone

from app.services import market_calendar as cal

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


# Reference moments in 2026. 2026-08-15 is a Saturday.
SAT_EVENING = utc("2026-08-15T20:00")
MON_MIDDAY = utc("2026-08-17T17:00")  # 13:00 ET, NYSE open
MON_NIGHT = utc("2026-08-17T23:00")  # 19:00 ET, NYSE closed

print("Calendar selection")
check("stocks use XNYS", cal.calendar_name("stock") == "XNYS")
check("funds use XNYS", cal.calendar_name("fund") == "XNYS")
check("commodities use 24/5", cal.calendar_name("commodity") == "24/5")
check("crypto has no calendar", cal.calendar_name("crypto") is None)
check("crypto is always open", cal.always_open("crypto"))
check("unknown class is treated as always open", cal.always_open("nonsense"))

print("Crypto never closes")
state = cal.session_state("crypto", SAT_EVENING)
check("always_open flag", state["always_open"] is True)
check("open on a Saturday night", state["is_open"] is True)
check("no next_open", state["next_open"] is None)
check("no next_close", state["next_close"] is None)

print("Stocks: closed on a Saturday evening")
state = cal.session_state("stock", SAT_EVENING)
check("not open", state["is_open"] is False)
check("timezone reported", state["timezone"] == "America/New_York", state["timezone"])
check(
    "next open is Monday 13:30 UTC",
    state["next_open"] == utc("2026-08-17T13:30"),
    f"(got {state['next_open']})",
)
check(
    "next close is Monday 20:00 UTC",
    state["next_close"] == utc("2026-08-17T20:00"),
    f"(got {state['next_close']})",
)
check("no current session while closed", state["current_session_end"] is None)

print("Stocks: open during the session")
state = cal.session_state("stock", MON_MIDDAY)
check("open", state["is_open"] is True)
check(
    "current session ends at today's close",
    state["current_session_end"] == utc("2026-08-17T20:00"),
    f"(got {state['current_session_end']})",
)
check("next_open omitted while open", state["next_open"] is None)

print("Commodities follow the 24/5 calendar")
state = cal.session_state("commodity", SAT_EVENING)
check("closed on Saturday", state["is_open"] is False)
check("reopens Monday", state["next_open"] == utc("2026-08-17T00:00"), str(state["next_open"]))
state = cal.session_state("commodity", MON_NIGHT)
check("open on a Monday night", state["is_open"] is True)

print("advance_sessions: expiry measured in sessions, not hours")
check(
    "Saturday + 1 session = Monday's close",
    cal.advance_sessions("stock", 1, SAT_EVENING) == utc("2026-08-17T20:00"),
    str(cal.advance_sessions("stock", 1, SAT_EVENING)),
)
check(
    "Saturday + 2 sessions = Tuesday's close",
    cal.advance_sessions("stock", 2, SAT_EVENING) == utc("2026-08-18T20:00"),
    str(cal.advance_sessions("stock", 2, SAT_EVENING)),
)
check(
    "mid-session + 1 session = today's close",
    cal.advance_sessions("stock", 1, MON_MIDDAY) == utc("2026-08-17T20:00"),
    str(cal.advance_sessions("stock", 1, MON_MIDDAY)),
)
check(
    "after the close + 1 session = tomorrow's close",
    cal.advance_sessions("stock", 1, MON_NIGHT) == utc("2026-08-18T20:00"),
    str(cal.advance_sessions("stock", 1, MON_NIGHT)),
)
check(
    "five sessions from Monday skips the weekend",
    cal.advance_sessions("stock", 5, MON_MIDDAY) == utc("2026-08-21T20:00"),
    str(cal.advance_sessions("stock", 5, MON_MIDDAY)),
)
check(
    "crypto sessions are 24-hour days",
    cal.advance_sessions("crypto", 2, SAT_EVENING) == SAT_EVENING + timedelta(days=2),
)
check(
    "a long horizon stays inside the calendar",
    cal.advance_sessions("stock", 60, MON_MIDDAY) > MON_MIDDAY + timedelta(days=80),
)
try:
    cal.advance_sessions("stock", 0, MON_MIDDAY)
    check("zero sessions rejected", False, "(no error)")
except ValueError:
    check("zero sessions rejected", True)

print("Holidays and early closes are respected")
# US Independence Day 2026 falls on Saturday, observed Friday 2026-07-03.
jul2 = utc("2026-07-02T21:00")  # Thursday after the close
check(
    "Independence Day observance is skipped",
    cal.advance_sessions("stock", 1, jul2) == utc("2026-07-06T20:00"),
    str(cal.advance_sessions("stock", 1, jul2)),
)
# Thanksgiving 2026 is 26 November; the 27th is a 13:00 ET early close.
wed_nov25 = utc("2026-11-25T22:00")
check(
    "Thanksgiving itself is not a session",
    cal.advance_sessions("stock", 1, wed_nov25) == utc("2026-11-27T18:00"),
    str(cal.advance_sessions("stock", 1, wed_nov25)),
)
check(
    "the day after Thanksgiving closes early (13:00 ET)",
    cal.session_state("stock", utc("2026-11-27T17:00"))["current_session_end"]
    == utc("2026-11-27T18:00"),
)
check(
    "winter closes shift with DST (16:00 ET = 21:00 UTC)",
    cal.advance_sessions("stock", 1, utc("2026-12-07T10:00")) == utc("2026-12-07T21:00"),
    str(cal.advance_sessions("stock", 1, utc("2026-12-07T10:00"))),
)

print("sessions_between: trading time, not wall-clock time")
check(
    "a weekend is zero sessions",
    cal.sessions_between("stock", utc("2026-08-14T21:00"), SAT_EVENING) == 0,
)
check(
    "Friday close to Monday close is one session",
    cal.sessions_between("stock", utc("2026-08-14T21:00"), utc("2026-08-17T21:00")) == 1,
)
check(
    "a full week is five sessions",
    cal.sessions_between("stock", utc("2026-08-16T00:00"), utc("2026-08-22T00:00")) == 5,
)
check(
    "the Thanksgiving week has four",
    cal.sessions_between("stock", utc("2026-11-23T00:00"), utc("2026-11-28T00:00")) == 4,
)
check("crypto counts whole days", cal.sessions_between("crypto", SAT_EVENING, SAT_EVENING + timedelta(days=3)) == 3)
check("an inverted range is zero", cal.sessions_between("stock", MON_MIDDAY, SAT_EVENING) == 0)
check("an empty range is zero", cal.sessions_between("stock", MON_MIDDAY, MON_MIDDAY) == 0)

print("Naive datetimes are read as UTC")
check(
    "naive input matches aware input",
    cal.advance_sessions("stock", 1, datetime(2026, 8, 15, 20, 0))
    == cal.advance_sessions("stock", 1, SAT_EVENING),
)

print("Defaults to now without blowing up")
state = cal.session_state("stock")
check("live call returns a state", isinstance(state["is_open"], bool))
check("live advance returns a future close", cal.advance_sessions("stock", 1) > datetime.now(timezone.utc))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
