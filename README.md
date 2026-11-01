# Campbell Sci Viewer

A standalone Windows application for viewing and editing Campbell Scientific
data files.

## Download and start

1. Download `release/CampBell Viewer.zip` and extract it.
2. Open the extracted `CampBell Viewer` folder.
3. Run `Campbel Sci Viewer.exe`.

The viewer creates a `Configs` folder next to the executable on first start.
Bundled configuration files are copied there only when a file does not already
exist, so your changes are preserved when you update the application.

## Open and view data

Use **Open Data** to open a Campbell `.dat` file (TOA5 or TOACI1) or a general
`.csv`/`.txt` measurement file. For general files, the first line must contain
the column names. Comma, semicolon, and tab delimiters and decimal commas are
detected automatically; a date/time column is used as the time axis.

The **Data** tab shows the measurements, the **Calc** tab shows calculated
columns, and the other tabs show charts from the matching logger configuration.
If no chart configuration is found, the viewer creates a line chart for each
numeric measurement column.

Use the mouse wheel over a chart to zoom horizontally. Place the pointer over
the left or right plot margin and use the wheel to scroll through the
measurement period. Hover over a line to see its column, time, and value.

## Edit measurements and charts

Double-click a cell in the **Data** tab, or select it and press Enter, F2, or
start typing, to edit its value. Select multiple cells with the mouse or with
Shift and the arrow keys; press Delete to clear the selection. **Save** writes
the modified data to a new `.dat` file and does not overwrite the original.
The **Calc** tab is read-only, but its values can be selected and copied with
Ctrl+C.

Use **LogDef Editor** to create or edit chart configurations. They are stored
in `Configs/Logdefs`. The viewer first looks for a logger-and-location
configuration, then a logger-wide configuration. A graph is split by height
only when `Hoogte` is explicitly included in that graph's `.logdef`; add
`@secondary` to a measurement column to plot it on the second Y axis.

## Formulas and custom functions

Use **Formula Editor** to add calculated columns. Refer to a measurement by
putting its column name in square brackets, for example:

```text
[Temp_02_m_Avg] * 1.8 + 32
IF([Regen] > 0; 1; 0)
DAGGEM([Temp_02_m_Avg])
```

Formulas are evaluated for each measurement row and can use columns from the
**Data** and **Calc** tabs. Built-in functions include row-wise arithmetic
(`SUM`, `SUBTRACT`, `PRODUCT`, `DIVIDE`, `MIN`, `MAX`, `AVERAGE`), conditions
(`IF`, `AND`, `OR`, `NOT`), maths, daily summaries, and time-series functions.
Function arguments can be separated with commas or semicolons. Dutch aliases
are also available. Open the in-app Help for the full function reference and
examples.

Use **Edit custom functions...** to edit `Configs/Functiedefs/Functies.funcdef`.
Formulas and custom functions are stored in `Configs/Formuladefs` and
`Configs/Functiedefs`.

## Language and configuration

Choose the interface language with the selector on the toolbar; the choice is
saved. Language files are in `Configs/Langdefs` (`<code>.langdef`). Optional
translated help files can be placed in `Configs/Helpdefs` as
`Campbell_Sci.<code>.helpdef`. The English help text is
`Configs/Helpdefs/Campbell_Sci.en.helpdef`.

Menu configurations are in `Configs/Menudefs`. Configuration files can be
adapted directly; names of functions and charts in user configurations are not
translated.

## Run from source

Requires Python 3.11 or newer. From the repository root, install the runtime
dependencies and start the viewer:

```powershell
python -m pip install -r requirements.txt
python .\src\main.py
```
