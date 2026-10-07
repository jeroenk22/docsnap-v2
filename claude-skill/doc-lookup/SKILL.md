---
name: doc-lookup
description: Zoekt een vraag op in online documentatie (openbaar of achter een login, zoals help centers, Confluence, developer docs en wiki's), leest de relevante pagina's volledig (tabellen, callouts, uitklapblokken, afbeeldingen) en antwoordt alleen met wat er in de bron staat. Met blijvende login en lokale cache. Gebruik bij "/doc-lookup", of als de gebruiker vraagt iets in de documentatie van een product, systeem of API op te zoeken.
argument-hint: "<vraag> [doc-url of bronnaam]"
allowed-tools: Read, Grep, Glob, Write, Edit, Agent, Bash(docsnap-lookup *)
---

# doc-lookup

Vraag van de gebruiker: **$ARGUMENTS**

Het zware werk (browser, login, wachten op de pagina, HTML naar Markdown, volledigheidscontrole)
doet het commando `docsnap-lookup`. Jij leest alleen de schone Markdown en bekijkt afbeeldingen
die ertoe doen. Zo blijft het tokenverbruik laag en is alles controleerbaar.

In deze instructies staat `$DL` voor `docsnap-lookup --session ${CLAUDE_SESSION_ID}`. Schrijf het
elke keer voluit; shellvariabelen blijven niet bewaard tussen Bash-aanroepen.

Gegevens staan buiten het project in `~/.doc-lookup/sites/<bron>/` (config `site.yaml`, sessie,
pagina's, afbeeldingen, screenshots) en worden gedeeld tussen al je projecten en chats.

## Transparantie (verplicht)

Meld bij elke stap in één korte regel wat je doet en wat het kost, met de getallen uit de uitvoer:
- `📄 3 pagina's opgehaald → ~9.400 tokens tekst + 4 afbeeldingen (max ~5.200 als ik ze bekijk)`
- `⚠️ Pagina X: tekstdekking 91% → ik bekijk de screenshot`

Elk commando eindigt met `[tokens] deze stap ~X | hele chat via doc-lookup ~Y`. Neem die getallen
over. Sluit af met `Verbruik: ~Y tokens via doc-lookup (P pagina's, A afbeeldingen bekeken)`.
Plak geen lange logs, maar meld wel eerlijk wat mislukt of onvolledig is.

## Werkwijze

**0. Bron bepalen**
- Werkt `docsnap-lookup` niet ("command not found"): zie *Installatie* onderaan, meld het en stop.
- `$DL sites` toont de bekende bronnen. Kies de bron uit de vraag of URL.
- Onbekende bron: `$DL init <url> [--name kortenaam]`. Een domein of startpagina is genoeg
  (`wiki.example.com` → `https://wiki.example.com`). `init` herkent ook hoe de site zoekt
  ("zoeken via: ..."). Was de URL geen gewone artikelpagina, draai dan na de eerste zoekactie
  `$DL init <url van een gevonden artikel> --name <bron> --refresh`, zodat de content-container
  op een echt artikel herkend wordt (een overzicht met alleen links misleidt de herkenning).
- **Exit 3 / "Login vereist"**: `$DL login <bron>` (Bash met `timeout: 600000`). Zeg de gebruiker
  dat er een browservenster opent waarin hij zelf inlogt; het script ziet zelf wanneer dat gelukt
  is. Vraag nooit om wachtwoorden en typ ze nooit zelf. Daarna:
  `$DL init <dezelfde url> --name <bron> --refresh`.
- **Controleer na elke `init`** de gekozen container: bekijk `debug/init.png` (pad staat in de
  uitvoer) met Read en beoordeel de kandidatenlijst. Is de keuze verkeerd (navigatie, banner,
  cookie-melding, alleen een titel), laat dan de structuur uitzoeken (zie *Structuur uitzoeken*).

**1. Wat is er al?** `$DL sites` toont hoeveel pagina's een bron in de cache heeft. Kijk in het
project in `.doc-lookup/notes/` of deze vraag eerder is uitgezocht.

**2. Zoeken, breed**
- Gaf de gebruiker URL's van de juiste pagina's: sla zoeken over.
- Bedenk 3–6 zoekvarianten: synoniemen, enkelvoud/meervoud, NL én EN, het vakjargon van het
  systeem en losse kernbegrippen (bv. "gebeurtenis instellen", "gebeurtenissen", "event", "trigger").
  `$DL search <bron> "variant 1" "variant 2" ... --limit 10`
- De uitvoer is een genummerde lijst met titel, URL, fragment en of de pagina al in de cache staat.
  Pagina's die met meerdere varianten gevonden zijn staan hoger. Kies alles wat relevant *kan*
  zijn: liever een pagina te veel dan een gemiste instelling.
- Mager of off-topic? Zoek opnieuw met andere termen; titels en fragmenten uit de eerste ronde
  geven goede nieuwe termen.
- Meldt `init`/`search` "geen zoekfunctie herkend": haal de startpagina of inhoudsopgave op met
  `fetch`, lees de links en kies daaruit. Lijkt de site wél een zoekbalk te hebben, laat dan de
  structuur uitzoeken (zie hieronder).

**3. Ophalen**: `$DL fetch <bron> 1 3 5-7` (nummers uit de laatste zoekactie) of met URL's
(`--no-images` als afbeeldingen niet nodig zijn).
Het script wacht tot de content stabiel is, slaat lege pagina's niet op en controleert per pagina
de volledigheid (tekstdekking, tabellen, lijsten, code, koppen, afbeeldingen).
- Exit 3: sessie verlopen → `login`, daarna hetzelfde `fetch` opnieuw.
- Exit 4 ("structuur gewijzigd") of exit 5 ("bleef leeg"): bekijk de screenshot uit de uitvoer en
  laat de structuur uitzoeken (zie hieronder).

**4. Lezen, volledig**
- Lees elk opgehaald `.md`-bestand **helemaal** met Read (geen offset/limit, geen grep als vervanging).
- Complexe tabellen staan als HTML in de Markdown; lees ze cel voor cel (colspan/rowspan).
- `> **Waarschuwing: …**` en `**[Uitklapblok] …**` komen uit de bron: neem ze serieus.
- Bekijk met Read elke afbeelding die relevant kan zijn (schermafdrukken van instellingen,
  schema's, tabellen als plaatje). Sla decoratieve over. Noem hoeveel je er bekeken hebt.
- Volledigheidswaarschuwing → bekijk de screenshot van die pagina en vul aan, of meld precies wat
  niet gelezen kon worden.
- Verwijst een pagina naar een andere die nodig is voor het antwoord: haal die ook op.
- Veel stof (meer dan ~12 pagina's of ~50k tokens): verdeel de bestanden over subagents die per
  groep de **letterlijke** relevante passages teruggeven (met bron-URL), geen samenvatting.

**5. Antwoorden: alleen wat in de bron staat**
- **Nooit iets verzinnen.** Elke feitelijke bewering (stap, menunaam, veldnaam, waarde, limiet,
  gedrag) komt uit een gelezen pagina en krijgt een bronnummer `[n]`. Staat het er niet, zeg dan
  letterlijk: "Dit staat niet in de gelezen documentatie." Vul niets aan uit algemene kennis.
- **Citeer letterlijk** wat ertoe doet: `"exacte tekst uit de pagina" [n]`. Veldnamen, waarden en
  waarschuwingen altijd letterlijk.
- **Drie delen, nooit door elkaar:**
  1. *Uit de documentatie*: alleen feiten met citaat en bronnummer.
  2. *Afgeleid (niet letterlijk in de documentatie)*: conclusies uit het combineren van bronnen of
     het toepassen op de situatie van de gebruiker; noem waarop ze steunen ("op basis van [1] en [3]").
  3. *Niet gevonden in de documentatie*: wat ontbreekt, met de zoektermen die je probeerde. Zoek
     eerst verder voordat je iets als niet gevonden meldt.
- **Controleer elk citaat** voordat je antwoordt: Grep de letterlijke tekst in het gecachte
  `.md`-bestand. Niet gevonden of een ander getal → corrigeer het citaat of verplaats de bewering
  naar *Afgeleid* of *Niet gevonden*. Meld `Bronnencontrole: X/X citaten letterlijk gevonden`.
- **Bronnenlijst onderaan**, één regel per bron, de URL uit de kop van het `.md`-bestand:
  ```
  Bronnen:
  [1]: https://...
  ```

**6. Vastleggen in het project** (als je in een project werkt)
- Kopieer de gelezen `.md`-bestanden en hun afbeeldingen naar `.doc-lookup/docs/<bron>/` (de
  afbeeldingen naar `.doc-lookup/docs/<bron>/images/<slug>/`, zodat de relatieve paden
  `../images/...` vanuit een submap `pages/` blijven werken; houd dus `pages/` en `images/` aan).
  Schrijf er een `INDEX.md` bij met titel, bron-URL en bestandsnaam per pagina.
- Voeg in `CLAUDE.md` van het project één regel toe (alleen als die er nog niet staat):
  `Documentatie van <bron>: .doc-lookup/docs/<bron>/INDEX.md. Gebruik deze bestanden als bron; actualiseren met /doc-lookup.`
- Schrijf `.doc-lookup/notes/<bron>-<onderwerp>.md` met de vraag, de datum en het antwoord
  (alle drie de delen, met bronnenlijst).
- Zeg één keer dat `.doc-lookup/` eventueel in `.gitignore` kan.
- Code die je op basis van de documentatie schrijft krijgt een bron-commentaar:
  `# Bron: <url>`, of `# Afgeleid uit [A] + [B], bevestigd door gebruiker`. Afgeleide waarden
  gebruik je pas na bevestiging; ontbrekende informatie vraag je, je gokt niet.

## Structuur uitzoeken (Opus)

Herkennen welke container de echte tekst bevat is oordeelswerk. Kiest `init` verkeerd, meldt
`init` een selector die "afhangt van de positie op de pagina", of meldt `fetch` exit 4, exit 5 of
een lage tekstdekking, laat dan een subagent met `model: opus` de diagnose doen.

Dat is duur (al snel 50.000–100.000 tokens), dus eerst de goedkope stappen: probeer `fetch` op
een tweede artikel; is het probleem daar weg, dan lag het aan die ene pagina. Geef de subagent een
gerichte opdracht (één bron, één probleem, maximaal ~5 `fetch`-pogingen) en meld zijn verbruik
apart aan de gebruiker (het staat in het resultaat van de subagent). Geef hem:
- het pad naar `~/.doc-lookup/sites/<bron>/site.yaml`,
- de kandidatenlijst uit de `init`-uitvoer en de screenshot-paden,
- de URL van een artikelpagina,
- de opdracht: bekijk de screenshots, bepaal welke CSS-selector de documentatietekst omvat (niet
  de navigatie, header, cookie-melding of zijbalk), en pas `site.yaml` aan:
  - `content.selector`: die selector (meerdere als fallback: `"a || b"`). Kies een selector die
    op elke pagina van de site werkt: een id, een betekenisvolle class (`.article-body`) of een
    semantisch element (`main article`), geen posities (`:nth-of-type`) of gegenereerde
    classnamen (`StyledRow-sc-xjsdg1-0`, `Content_body__v5MYy`). Controleer hem met `fetch` op
    twee verschillende artikelen,
  - `content.remove`: lijst met selectors binnen de content die geen documentatie zijn
    (bv. "deze pagina delen", AI-samenvatting, "laatst bewerkt door"),
  - `content.min_chars`: lager zetten als artikelen echt kort zijn (standaard 150),
  - `login.logged_in_selector`: een element dat alleen ingelogd bestaat, als de login-muur niet
    goed herkend wordt.
  - `search.ui.page`: een pagina met een werkende zoekbalk, als `init` geen zoekfunctie vond
    terwijl de site die wel heeft; daarna `site.yaml` de regel `methods:` onder `search:`
    verwijderen, zodat de volgende `search` de zoekfunctie opnieuw herkent.
  Daarna `fetch` opnieuw draaien op die artikelpagina en het resultaat (volledigheid) melden.

Gewoon lezen en antwoorden kan op het huidige model.

Lijkt iets een fout in `docsnap-lookup` zelf (verkeerde melding, crash), pas dan de broncode niet
aan vanuit een ander project: meld het aan de gebruiker met de URL, het commando en de uitvoer.

## Installatie (eenmalig per computer)

```bash
uv tool install --editable "<pad naar docsnap-v2>[lookup]"
playwright install chromium   # als Chromium nog niet geïnstalleerd is
```
Optionele inloggegevens voor automatisch inloggen (alleen zonder MFA) horen in
`~/.doc-lookup/.env` als `DOC_LOOKUP_<BRON>_USER` en `DOC_LOOKUP_<BRON>_PASS`, met in `site.yaml`
`login.mode: form`. Nooit in de chat of in het project.
