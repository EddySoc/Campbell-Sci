# Campbell Sci New

Een aparte interactieve viewer voor Campbell `.dat`-bestanden. De bestaande applicatie blijft ongewijzigd.

## Starten

```powershell
cd C:\Python_W_new\Campbell_Sci_new
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m campbell_sci_new.main
```

Bij het starten kies je een `.dat`-bestand. De viewer toont een Data-tab en een tab per geconfigureerde grafiek. Voor BM/BRAS gebruikt hij `src/campbell_sci_new/graph_configs/BM.logdef`, inclusief de windroos. Als er geen configuratie is, maakt de viewer automatisch een lijngrafiek voor elke numerieke meetkolom.

Een grafiek wordt alleen op hoogte opgesplitst als `Hoogte` expliciet in de kolommen van die grafiek in het `.logdef`-bestand staat. De grafiek blijft op één tab en toont voor elke hoogte een aparte meetreeks. Voeg `@secondary` toe aan een meetkolom in de `.logdef` om die reeks op de tweede y-as te tekenen.

Op een grafiek zoomt het muiswiel horizontaal in/uit rond de cursor. Met de muis boven de linker- of rechtermarge van de plot verschuift de tijdsperiode naar links of rechts. De Data-tab toont de meetwaarden in pagina's van 1000 rijen.
