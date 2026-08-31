"""
utils/timeutils.py
====================
CRITICAL: GitHub Actions runners (and many cloud servers) use UTC as their
system clock, not IST. Every previous use of dt.datetime.now() / dt.date.today()
in this codebase was comparing UTC wall-clock time against IST-defined market
hours (9:15 AM - 3:30 PM IST) as if they were the same clock -- this caused
the system to think it was "market hours" when it was actually UTC time that
happened to fall in the 9:15-15:30 numeric range (which in IST could be late
evening!). Always use now_ist() / today_ist() from this module instead of
the raw datetime/date "now" functions anywhere market-hours logic is involved.
"""

from datetime import datetime, date
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current date+time, correctly in IST regardless of the server's system timezone."""
    return datetime.now(IST)


def today_ist() -> date:
    """Current date in IST (matters near midnight -- IST date can differ from UTC date)."""
    return now_ist().date()
