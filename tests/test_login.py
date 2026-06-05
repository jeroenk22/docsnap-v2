"""Tests voor login strategieën."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.login import LoginStrategy, apply_login, create_login_strategy


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


# ---------------------------------------------------------------------------
# Tests voor apply_login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_login_none_skips_navigation() -> None:
    """apply_login met mode 'none' navigeert niet naar de login URL."""
    mock_page = AsyncMock()
    strategy = LoginStrategy(mode="none")
    await apply_login(mock_page, strategy, "https://docs.example.com/login")
    mock_page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_apply_login_form_fills_and_submits() -> None:
    """apply_login met mode 'form' vult het formulier in en submit."""
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_locator.first = AsyncMock()
    mock_locator.first.fill = AsyncMock()
    mock_locator.first.click = AsyncMock()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)
    mock_page.wait_for_load_state = AsyncMock()

    strategy = LoginStrategy(mode="form", username="user@test.com", password="geheim123")
    await apply_login(mock_page, strategy, "https://docs.example.com/login")

    mock_page.goto.assert_called_once_with(
        "https://docs.example.com/login", wait_until="networkidle"
    )
    mock_page.wait_for_load_state.assert_called()


@pytest.mark.asyncio
async def test_apply_login_form_no_matching_fields() -> None:
    """apply_login met mode 'form' crasht niet als er geen velden gevonden worden."""
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=0)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)
    mock_page.wait_for_load_state = AsyncMock()

    strategy = LoginStrategy(mode="form", username="u", password="p")
    await apply_login(mock_page, strategy, "https://docs.example.com/login")

    mock_page.wait_for_load_state.assert_called()


@pytest.mark.asyncio
async def test_apply_login_manual_waits_for_input() -> None:
    """apply_login met mode 'manual' wacht op gebruikersinput."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()

    strategy = LoginStrategy(mode="manual")
    with patch("src.login.input", return_value=""):
        await apply_login(mock_page, strategy, "https://docs.example.com/login")

    mock_page.wait_for_load_state.assert_called()
