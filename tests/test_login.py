"""Tests voor login strategieën."""
import pytest

from src.login import LoginStrategy, create_login_strategy


def test_create_login_strategy_none():
    """Mode 'none' maakt een correcte strategie aan."""
    strategy = create_login_strategy("none")
    assert strategy.mode == "none"
    assert strategy.username is None
    assert strategy.password is None


def test_create_login_strategy_form_valid():
    """Mode 'form' met credentials maakt correcte strategie aan."""
    strategy = create_login_strategy("form", username="user@test.com", password="geheim")
    assert strategy.mode == "form"
    assert strategy.username == "user@test.com"
    assert strategy.password == "geheim"


def test_create_login_strategy_form_missing_user():
    """Mode 'form' zonder --user gooit ValueError."""
    with pytest.raises(ValueError, match="--user"):
        create_login_strategy("form", username=None, password="geheim")


def test_create_login_strategy_form_missing_password():
    """Mode 'form' zonder --pass gooit ValueError."""
    with pytest.raises(ValueError):
        create_login_strategy("form", username="user@test.com", password=None)


def test_create_login_strategy_manual():
    """Mode 'manual' heeft geen credentials nodig."""
    strategy = create_login_strategy("manual")
    assert strategy.mode == "manual"
    assert strategy.username is None


def test_login_strategy_dataclass():
    """LoginStrategy is een dataclass met de verwachte velden."""
    s = LoginStrategy(mode="none", username="u", password="p")
    assert s.mode == "none"
    assert s.username == "u"
    assert s.password == "p"
