"""Calendar confirmation rules. Naive datetimes represent stored UTC."""

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

DOMAIN_TIMEZONE = ZoneInfo("Asia/Taipei")


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def confirmation_day(created_at: datetime) -> date:
    local = utc_naive(created_at).replace(tzinfo=timezone.utc).astimezone(DOMAIN_TIMEZONE)
    return local.date() + timedelta(days=int(local.time() >= time(15)))


def confirmation_deadline(created_at: datetime) -> datetime:
    """Return 16:30 Taipei on the responsibility day, stored as naive UTC."""
    return utc_naive(datetime.combine(confirmation_day(created_at), time(16, 30), DOMAIN_TIMEZONE))


class ConfirmationState(str, Enum):
    PENDING = "pending"
    CONFIRMED_ON_TIME = "confirmed_on_time"
    OVERDUE = "overdue"
    CONFIRMED_LATE = "confirmed_late"


def confirmation_state(
    ack_deadline_at: datetime, *, confirmed_at: datetime | None = None,
    now: datetime | None = None,
) -> ConfirmationState:
    deadline = utc_naive(ack_deadline_at)
    if confirmed_at is not None:
        return (ConfirmationState.CONFIRMED_ON_TIME if utc_naive(confirmed_at) <= deadline
                else ConfirmationState.CONFIRMED_LATE)
    current = utc_naive(now or datetime.now(timezone.utc))
    return ConfirmationState.PENDING if current <= deadline else ConfirmationState.OVERDUE
