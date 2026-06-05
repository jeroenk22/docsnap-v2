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
