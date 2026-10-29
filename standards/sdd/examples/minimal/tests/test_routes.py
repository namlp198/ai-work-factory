"""Example of test linking at the endpoint level; one test may prove several criteria."""

import pytest


@pytest.mark.req("FR-AUTH-003.1")
def test_locked_returns_423(client, locked_account):
    response = client.post("/sign-in", json={"email": locked_account.email, "password": "wrong"})
    assert response.status_code == 423


@pytest.mark.req("FR-AUTH-003.2")
def test_right_password_while_locked(client, locked_account):
    response = client.post("/sign-in", json={"email": locked_account.email, "password": locked_account.password})
    assert response.status_code == 423
    assert "token" not in response.json()
