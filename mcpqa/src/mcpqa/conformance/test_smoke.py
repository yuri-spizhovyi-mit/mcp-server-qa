"""Temporary smoke check (M3a Task 3); replaced by the real checks in Task 4."""


def test_session_opens(session):
    assert isinstance(session.capabilities, dict)
