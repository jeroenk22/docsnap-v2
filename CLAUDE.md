# docsnap-v2

## Wat deze app doet
Een generieke CLI tool die elke online documentatiesite automatisch scrapt naar
schone Markdown. Geen YAML-config per site nodig — de tool ontdekt zelf de structuur.
Ondersteunt sites zonder login, met form-login, en met handmatige login (gebruiker logt
zelf in, tool wacht). Detecteert automatisch of een site Swagger/OpenAPI gebruikt en
haalt dan de raw JSON/YAML spec op in plaats van de UI te scrapen. Gebruikt de Claude
API om per pagina de echte documentatie-inhoud te extraheren uit de ruwe HTML (verwijdert
nav, footer, cookiebanners, etc.). Pagina's worden volledig geladen via scroll-to-bottom
en het uitklappen van collapsible elementen voordat content wordt geëxtraheerd.

## Stack

| Tool              | Versie                    |
|-------------------|---------------------------|
| Python            | ≥ 3.11                    |
| playwright        | 1.60.0                    |
| anthropic         | 0.105.2                   |
| click             | 8.3.2                     |
| python-dotenv     | 1.2.2                     |
| httpx             | ≥ 0.27.0                  |
| ruff              | 0.15.14                   |
| pytest            | 9.0.2                     |
| Claude model      | claude-haiku-4-5-20251001 |

## Commando's

```bash
# Installeer in een virtualenv
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate.bat       # Windows

# Installeer alle dependencies (inclusief dev tools)
pip install -e ".[dev]"

# Installeer Playwright browsers
playwright install chromium

# CLI gebruiken
docsnap https://docs.example.com
docsnap https://docs.example.com --login form --user admin@example.com --pass geheim
docsnap https://docs.example.com --login manual
docsnap https://docs.example.com --output files --out-dir ./mijn-output

# Tests
pytest

# Lint
ruff check .

# Format
ruff format .

# Auto-fix
ruff check . --fix
```

## Projectstructuur

```
src/
├── __init__.py      # Package init
├── main.py          # CLI entry point (Click)
├── discovery.py     # Sitemap/nav/link crawler
├── login.py         # Login strategieën (none/form/manual)
├── scraper.py       # Playwright page loading + smart waits
├── swagger.py       # OpenAPI/Swagger detectie en spec extractie
├── cleaner.py       # Claude API content cleaning
└── exporter.py      # Output naar markdown/files/pdf
tests/               # Pytest unit tests (mock voor Playwright + Claude)
skills/              # Python best-practice documentatie
```

## Omgevingsvariabelen

Kopieer `.env.example` naar `.env` en vul in:
```bash
cp .env.example .env
# Vul ANTHROPIC_API_KEY in
```

`.env` staat in `.gitignore` — **NOOIT committen.**

## Conventies
- Branch strategie: main (protected) → develop → feature/xxx, fix/xxx, chore/xxx
- Commits: Conventional Commits (feat:, fix:, chore:, docs:, test:)
- PR: nooit direct naar main, altijd via PR met passing tests
- Package manager: pip + venv

## Wat Claude NIET mag doen
- Nooit direct committen naar main
- Nooit `.env` bestand aanmaken met echte secrets
- Nooit dependencies toevoegen zonder expliciete vraag
- Nooit bestaande tests verwijderen
- Nooit `.env.example` overschrijven met echte sleutels

---

## Karpathy Gedragsregels

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

---

## Security
- Nooit API keys, passwords of secrets in code of commits
- Gebruik altijd `.env.example` voor configuratievoorbeelden
- `.env` staat in `.gitignore`

## Evaluatie & Kwaliteit
- **Code coverage:** minimaal 80% (afgedwongen in CI)
- **Linting:** Ruff — zero errors verplicht in CI
