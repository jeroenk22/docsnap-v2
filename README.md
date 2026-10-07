# docsnap-v2

> Scrape elke documentatiesite naar schone Markdown — automatisch, zonder configuratie.

## Kenmerken

- 🔍 **Automatische ontdekking** — via `sitemap.xml` of recursieve link-crawl
- 🔐 **Login ondersteuning** — geen login, form-login, of handmatige login
- 🤖 **AI-powered cleaning** — Claude haalt de echte content eruit (geen nav/footer/banners)
- 📋 **Swagger/OpenAPI** — detecteert automatisch, haalt raw spec op
- 📄 **Flexibele output** — één groot `.md` bestand, losse bestanden per pagina, of PDF

## Installatie

```bash
git clone https://github.com/YOUR_USERNAME/docsnap-v2.git
cd docsnap-v2
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate.bat
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
# Vul ANTHROPIC_API_KEY in .env
```

## Gebruik

```bash
# Basis
docsnap https://docs.example.com

# Form-login
docsnap https://docs.example.com --login form --user admin@example.com --pass geheim

# Handmatige login (browser opent, jij logt in, druk ENTER)
docsnap https://docs.example.com --login manual

# Losse bestanden per pagina
docsnap https://docs.example.com --output files --out-dir ./output

# Swagger/OpenAPI site (spec wordt automatisch gedetecteerd)
docsnap https://petstore.swagger.io
```

## doc-lookup: documentatie opzoeken vanuit Claude Code (optioneel)

Naast het scrapen van een hele site kan Claude Code met de skill `/doc-lookup` in elk
project een vraag opzoeken in online documentatie, ook achter een login. Alleen de
relevante pagina's worden gelezen, volledig, en het antwoord bevat alleen wat er in de
bron staat. Sessies en cache staan in `~/.doc-lookup/` en worden gedeeld tussen projecten.

Eenmalig per computer:

```bash
# 1. Het commando docsnap-lookup globaal beschikbaar maken (editable: wijzigingen
#    in deze repo werken direct door)
uv tool install --editable "<pad naar docsnap-v2>[lookup]"
playwright install chromium

# 2. De skill koppelen aan je persoonlijke Claude Code-skills
# Windows (PowerShell):
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\doc-lookup" -Target "<pad naar docsnap-v2>\claude-skill\doc-lookup"
# macOS/Linux:
ln -s "<pad naar docsnap-v2>/claude-skill/doc-lookup" ~/.claude/skills/doc-lookup
```

Daarna in elk project: `/doc-lookup hoe stel ik een gebeurtenis in https://support.example.com/...`

Het commando werkt ook los: `docsnap-lookup --help` (`sites`, `init`, `login`, `fetch`).

## Omgevingsvariabelen

| Variabele           | Verplicht | Beschrijving           |
|---------------------|-----------|------------------------|
| `ANTHROPIC_API_KEY` | ✅         | Anthropic API sleutel  |

## Ontwikkeling

```bash
pytest              # tests + coverage
ruff check .        # linting
ruff format .       # formatteren
```

## Licentie

MIT
