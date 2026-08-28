# Campbell Sci data processor

Dit project is een eenvoudig Python-skelet om Campbell Sci `.dat` bestanden te openen, te herkennen aan de voorvoegsels in de bestandsnaam en daarna per logger-type een andere verwerking te kiezen.

## Structuur

- `src/campbell_sci/` : Python modules
- `requirements.txt` : Python dependencies
- `.venv/` : virtuele omgeving (later gemaakt)

## Voorbeeld bestandsnamen

- `BRAS_2025_08_22_10_30.dat`
- `TDR_2025_08_22_10_30.dat`

De parser verwacht een patroon zoals:

`PREFIX_YYYY_MM_DD[_hh_mm]`

## Verwerking

- `parse_filename()` leest de prefix (bijv. BRAS, TDR)
- `select_processor()` kiest de juiste functie
- `process_file()` leest het bestand en roept de juiste processor aan

## Grafiekconfiguratie

Alle grafieken worden opgebouwd via `graph_builder.py`. Logger-specifieke
grafieken staan in `src/campbell_sci/graph_configs/<LOGGER>.logdef`, met een regel
per grafiek:

```text
naam;type;kolommen;minimum;maximum;getalnotatie;tabkleur
```

Kolommen zijn headernamen uit de `.dat`-export, gescheiden door komma's.
Gebruik `@secondary` achter een kolomnaam voor de secundaire Y-as. Een nieuwe
sensor toevoegen betekent daardoor alleen een kolomnaam in de juiste regel
opnemen. Voor een logger zonder configuratie maakt de fallback automatisch een
grafiek per meetkolom, eveneens via dezelfde builder.

### Schalen en zoomen

De VBA-functies `Rescale`, `ZoomIn`, `ZoomBack` en `Scrollen` zijn beschikbaar
als Python-functies in `graph_builder.py`:

```python
from campbell_sci.graph_builder import rescale_chart, scroll_chart, zoom_chart

zoom_chart(wb, "Droge T", start_row=100, end_row=300)
rescale_chart(wb, "Droge T", mode="split")
scroll_chart(wb, "Droge T", direction="right", step=25)
```

`auto` schaalt elke Y-as afzonderlijk met marge, `split` geeft extra ruimte
onder de primaire en boven de secundaire schaal, en `same_zero` gebruikt voor
beide assen hetzelfde bereik. Deze functies moeten vóór `wb.save(...)` worden
aangeroepen. Een gewone `.xlsx` kan deze bewerkingen niet als live VBA-knoppen
uitvoeren; daarvoor is een macro-enabled `.xlsm` en aanvullende Excel/VBA-code
nodig.

## Uitvoeren

1. maak een venv aan
2. installeer requirements
3. start de app

Voorbeeld:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m campbell_sci.main
```

## Opmerking

Dit is een skelet. De echte Campbell Sci data-formatten kunnen variëren per logger en per export. De parser en processors worden later uitgebreid met echte kolommapping, grafieklogica en spike-filtering.
