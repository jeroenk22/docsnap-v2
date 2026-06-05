"""Tests voor de Claude API content cleaner."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cleaner import _clean_single_page
from src.scraper import ScrapedPage


@pytest.fixture
def sample_page() -> ScrapedPage:
    return ScrapedPage(
        url="https://docs.example.com/getting-started",
        html=(
            "<html><body><nav>Nav</nav>"
            "<main><h1>Getting Started</h1><p>Hello world.</p></main>"
            "</body></html>"
        ),
        title="Getting Started",
    )


@pytest.mark.asyncio
async def test_clean_single_page_returns_markdown(sample_page: ScrapedPage) -> None:
    """clean_single_page roept Claude aan en geeft markdown terug."""
    mock_content = MagicMock()
    mock_content.text = "# Getting Started\n\nHello world."

    mock_message = MagicMock()
    mock_message.content = [mock_content]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)

    semaphore = asyncio.Semaphore(1)
    result = await _clean_single_page(mock_client, semaphore, sample_page)

    assert result["url"] == sample_page.url
    assert result["title"] == sample_page.title
    assert "# Getting Started" in result["markdown"]


@pytest.mark.asyncio
async def test_clean_single_page_empty_response(sample_page: ScrapedPage) -> None:
    """Lege Claude response wordt correct afgehandeld (geen crash)."""
    mock_message = MagicMock()
    mock_message.content = []

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)

    semaphore = asyncio.Semaphore(1)
    result = await _clean_single_page(mock_client, semaphore, sample_page)

    assert result["markdown"] == ""
    assert result["url"] == sample_page.url


@pytest.mark.asyncio
async def test_clean_single_page_html_truncated(sample_page: ScrapedPage) -> None:
    """Grote HTML wordt afgekapt voordat het naar Claude gaat."""
    from src.cleaner import MAX_HTML_CHARS

    sample_page.html = "x" * (MAX_HTML_CHARS + 10_000)

    captured_calls: list = []

    async def capture_call(**kwargs: object) -> MagicMock:
        captured_calls.append(kwargs)
        msg = MagicMock()
        msg.content = [MagicMock(text="# Truncated")]
        return msg

    mock_client = AsyncMock()
    mock_client.messages.create = capture_call

    semaphore = asyncio.Semaphore(1)
    await _clean_single_page(mock_client, semaphore, sample_page)

    assert len(captured_calls) == 1
    user_content = captured_calls[0]["messages"][0]["content"]
    # HTML in de content mag niet groter zijn dan MAX_HTML_CHARS
    assert len(user_content) <= MAX_HTML_CHARS + 500  # header tekst erbij
