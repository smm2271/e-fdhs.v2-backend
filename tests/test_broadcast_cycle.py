from datetime import datetime, timedelta, timezone

import pytest

from database.broadcast_cycle import (
    DOMAIN_TIMEZONE, ConfirmationState, confirmation_day, confirmation_deadline,
    confirmation_state,
)


@pytest.mark.parametrize("local,day", [
    (datetime(2026, 10, 5, 14, 59, 59), 5),
    (datetime(2026, 10, 5, 15), 6),
    (datetime(2026, 10, 5, 15, 0, 1), 6),
    (datetime(2026, 10, 5, 23, 59, 59, 999999), 6),
    (datetime(2026, 10, 6, 0), 6),
    (datetime(2026, 10, 9, 15), 10),  # Friday -> Saturday
])
def test_confirmation_calendar_boundaries(local, day):
    aware = local.replace(tzinfo=DOMAIN_TIMEZONE)
    expected = datetime(2026, 10, day, 8, 30)
    assert confirmation_day(aware).day == day
    assert confirmation_deadline(aware) == expected
    assert confirmation_deadline(aware.astimezone(timezone.utc).replace(tzinfo=None)) == expected


@pytest.mark.parametrize("confirmed,now,state", [
    (None, 0, ConfirmationState.PENDING),
    (None, 1, ConfirmationState.OVERDUE),
    (-1, 1, ConfirmationState.CONFIRMED_ON_TIME),
    (0, 1, ConfirmationState.CONFIRMED_ON_TIME),
    (1, -1, ConfirmationState.CONFIRMED_LATE),
])
def test_confirmation_state_at_exact_deadline(confirmed, now, state):
    deadline = datetime(2026, 10, 5, 8, 30, tzinfo=timezone.utc)
    confirmed_at = None if confirmed is None else deadline + timedelta(microseconds=confirmed)
    assert confirmation_state(deadline, confirmed_at=confirmed_at,
                              now=deadline + timedelta(microseconds=now)) == state
