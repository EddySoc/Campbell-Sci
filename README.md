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

Bij het starten kies je een `.dat`-bestand. De viewer leest Campbell TOA5- en TOACI1-bestanden. Hij toont een Data-tab en een tab per geconfigureerde grafiek. Logdef-bestanden staan in `src/Configs/Logdefs`; menuconfiguraties staan in `src/Configs/Menudefs` en helpteksten in `src/Configs/Helpdefs`. Voor BM/BRAS gebruikt hij `src/Configs/Logdefs/BM.logdef`, inclusief de windroos. Als er geen configuratie is, maakt de viewer automatisch een lijngrafiek voor elke numerieke meetkolom.

De Help-knop toont de tekst uit `src/Configs/Helpdefs/Campbell_Sci.helpdef` naast `src/Meettoren.jpg`. Pas de `.helpdef`-tekst aan om de helpinformatie te wijzigen.

Een grafiek wordt alleen op hoogte opgesplitst als `Hoogte` expliciet in de kolommen van die grafiek in het `.logdef`-bestand staat. De grafiek blijft op één tab en toont voor elke hoogte een aparte meetreeks. Voeg `@secondary` toe aan een meetkolom in de `.logdef` om die reeks op de tweede y-as te tekenen.

Op een grafiek zoomt het muiswiel horizontaal in/uit rond de cursor. Met de muis boven de linker- of rechtermarge van de plot verschuift de tijdsperiode naar links of rechts. De Data-tab toont de meetwaarden in pagina's van 1000 rijen.
