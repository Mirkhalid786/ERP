"""
Business clock. Timestamps are stored in UTC; business dates (invoice day, due dates,
day-end, document-number periods) use the pharmacy's UTC offset (APP_UTC_OFFSET_MINUTES).
"""
from datetime import date, datetime, time, timedelta, timezone

from app.core.config import settings

BUSINESS_TZ = timezone(timedelta(minutes=settings.APP_UTC_OFFSET_MINUTES))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def business_now() -> datetime:
    return datetime.now(BUSINESS_TZ)


def today_local() -> date:
    """Business date in the pharmacy's time zone (not the server's, which is UTC in Docker)."""
    return business_now().date()


def day_bounds(day: date) -> tuple:
    """UTC instants [start, end) of a business day, for filtering stored UTC timestamps."""
    start = datetime.combine(day, time.min, tzinfo=BUSINESS_TZ).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def local_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BUSINESS_TZ).date()
