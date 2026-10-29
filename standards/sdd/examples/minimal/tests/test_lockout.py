"""Example of test linking (standard section 9): each test names the acceptance criteria it proves."""

import pytest

from auth.lockout import is_locked, record_failure


@pytest.mark.req("FR-AUTH-003.1")
def test_fifth_failure_locks(clock, account):
    for _ in range(5):
        record_failure(account, at=clock.now())
    assert is_locked(account, at=clock.now())


@pytest.mark.req("FR-AUTH-003.3")
def test_unlocks_after_15_minutes(clock, account):
    for _ in range(5):
        record_failure(account, at=clock.now())
    assert not is_locked(account, at=clock.now() + clock.minutes(15))
