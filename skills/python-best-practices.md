# Python Best Practices — docsnap-v2

## Async / Await
- Gebruik altijd `asyncio.gather()` voor gelijktijdige operaties
- Gebruik `asyncio.Semaphore` om API calls te begrenzen (rate limiting)
- Sluit altijd browsers/contexts met `async with` of expliciete `close()`
- Gebruik `return_exceptions=True` in `asyncio.gather()` om fouten per pagina af te handelen

## Playwright
- `headless=True` voor productie, `headless=False` voor manual login
- Stel altijd `timeout` in op `page.goto()` en `wait_for_load_state()`
- Gebruik `wait_until="networkidle"` voor dynamische JS-sites
- Gebruik `try/except` rondom click-operaties op accordions (niet alle elementen zijn klikbaar)

## Anthropic SDK
- Gebruik `AsyncAnthropic` in async context
- Begrens HTML input met `MAX_HTML_CHARS` om tokens te besparen
- Verwerk `message.content[0].text` veilig: controleer altijd op lege `content` lijst

## Type Hints
- Gebruik `from __future__ import annotations` voor forward references
- Annoteer alle publieke functies en methoden volledig
- Gebruik `X | None` (Python 3.10+) in plaats van `Optional[X]`
- Gebruik `object` in type signatures waar `Any` verleidelijk is

## Error Handling
- Log fouten per pagina met `print(f"⚠️ ...")`, stop niet de hele scrape-run
- Gebruik `# noqa: BLE001` voor bewust brede except-blokken
- Gebruik specifieke exceptions waar mogelijk (`httpx.RequestError` > `Exception`)

## Testing
- Mock Playwright met `AsyncMock` — geen echte browser in unit tests
- Mock Anthropic client — geen echte API calls in unit tests
- Gebruik `asyncio_mode = "auto"` in `pyproject.toml` voor pytest-asyncio
- Gebruik `tmp_path` fixture voor bestandssysteemtests
- Streef naar 80%+ coverage; Playwright-zware functies zijn acceptabel om te skippen
