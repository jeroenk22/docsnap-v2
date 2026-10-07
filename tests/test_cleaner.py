"""Tests voor de Claude API content cleaner."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cleaner import _clean_single_page, _split_html_into_chunks, clean_pages
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
async def test_clean_single_page_large_html_uses_chunking(sample_page: ScrapedPage) -> None:
    """Grote HTML wordt in meerdere chunks verwerkt (één Claude-call per chunk)."""
    from src.cleaner import MAX_HTML_CHARS

    # Geen headings → 2 raw chunks (MAX_HTML_CHARS + 10_000 tekens)
    sample_page.html = "x" * (MAX_HTML_CHARS + 10_000)

    captured_calls: list = []

    async def capture_call(**kwargs: object) -> MagicMock:
        captured_calls.append(kwargs)
        msg = MagicMock()
        msg.content = [MagicMock(text=f"# Part {len(captured_calls)}")]
        return msg

    mock_client = AsyncMock()
    mock_client.messages.create = capture_call

    semaphore = asyncio.Semaphore(1)
    result = await _clean_single_page(mock_client, semaphore, sample_page)

    # 2 Claude-calls: één per chunk
    assert len(captured_calls) == 2
    # Resulterende markdown bevat beide parts samengevoegd
    assert "# Part 1" in result["markdown"]
    assert "# Part 2" in result["markdown"]


# ---------------------------------------------------------------------------
# Tests voor _split_html_into_chunks
# ---------------------------------------------------------------------------

def test_split_html_small_input_returns_single_chunk() -> None:
    """HTML kleiner dan max_chars geeft één chunk terug."""
    html = "<h1>Title</h1><p>Content</p>"
    chunks = _split_html_into_chunks(html, max_chars=1000)
    assert chunks == [html]


def test_split_html_splits_at_heading_boundary() -> None:
    """Grote HTML wordt gesplitst vlak vóór heading-tags."""
    intro = "x" * 100
    section_a = "<h2>Section A</h2>" + "a" * 100
    section_b = "<h2>Section B</h2>" + "b" * 100
    html = intro + section_a + section_b  # 336 tekens totaal

    # max_chars=150: elke chunk splitst bij de volgende heading-positie
    # chunk1 = intro(100), chunk2 = section_a(118), chunk3 = section_b(118)
    chunks = _split_html_into_chunks(html, max_chars=150)
    assert len(chunks) == 3
    assert chunks[0] == "x" * 100
    assert "<h2>Section A</h2>" in chunks[1]
    assert "<h2>Section B</h2>" in chunks[2]


def test_split_html_no_headings_falls_back_to_raw_split() -> None:
    """HTML zonder headings wordt ruw op max_chars gesplitst."""
    html = "a" * 250
    chunks = _split_html_into_chunks(html, max_chars=100)
    assert len(chunks) == 3
    assert all(len(c) <= 100 for c in chunks)


def test_split_html_empty_chunks_filtered() -> None:
    """Chunks die enkel whitespace bevatten worden weggefilterd."""
    html = "   " + "<h1>Header</h1>" + "content" * 50
    chunks = _split_html_into_chunks(html, max_chars=50)
    assert all(c.strip() for c in chunks)


# ---------------------------------------------------------------------------
# Test voor _call_claude
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_claude_returns_text() -> None:
    """_call_claude geeft de text van de eerste content-block terug."""
    from src.cleaner import _call_claude

    mock_content = MagicMock()
    mock_content.text = "# Result"
    mock_message = MagicMock()
    mock_message.content = [mock_content]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)

    result = await _call_claude(mock_client, "https://example.com", "Title", "<p>html</p>")
    assert result == "# Result"
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_clean_pages_reports_progress_and_summary(capsys) -> None:
    """clean_pages print per-pagina progress en eindtotaal."""
    pages = [
        ScrapedPage(url="https://docs.example.com/a", html="<main>content a</main>", title="A"),
        ScrapedPage(url="https://docs.example.com/b", html="<main>content b</main>", title="B"),
        ScrapedPage(url="https://docs.example.com/empty", html="<main></main>", title="Empty"),
    ]

    call_count = [0]

    async def fake_create(**kwargs):
        call_count[0] += 1
        msg = MagicMock()
        # Derde pagina geeft lege response (loginpagina / geen content)
        msg.content = [MagicMock(text="" if call_count[0] == 3 else "# Content")]
        return msg

    mock_client = AsyncMock()
    mock_client.messages.create = fake_create

    with patch("src.cleaner.anthropic.AsyncAnthropic", return_value=mock_client):
        result = await clean_pages(pages, concurrency=3)

    out = capsys.readouterr().out
    assert "[1/3]" in out or "[2/3]" in out  # progress zichtbaar
    assert "2/3" in out  # samenvatting: 2 van 3 ok
    assert "https://docs.example.com/empty" in out  # lege pagina gemarkeerd
    assert len(result) == 3


@pytest.mark.asyncio
async def test_clean_pages_empty_input() -> None:
    """clean_pages met lege lijst geeft lege lijst terug zonder crash."""
    result = await clean_pages([])
    assert result == []
