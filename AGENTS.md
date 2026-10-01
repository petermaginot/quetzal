# Notes for agents working on Quetzal

Lessons from adding the threaded fittings (hex bushings and plugs, threaded
ells, tees, couplings, caps, unions and olets) and testing them in a live
FreeCAD session over MCP. Read this before adding a component type or a
fitting table.

## Adding a component type

A new PType touches all of these. Missing one usually fails silently: the
part builds but won't insert from the GUI, round-trip through PCF or match
sizes.

| Where | What |
|---|---|
| `tablez/<Family>_<rating>.csv` | Dimension table (see *Tables* below) |
| `pFeatures.py` | Feature class: properties, `execute()`, `Ports`, `PortDirections` |
| `pCmd.py` | `make<Type>()` and `do<Type>()`. Straight two-port and one-port parts can reuse `_makeSocketStraight` / `_doSocketStraight` |
| `pCmd.getSelectedPortDimensions` | Add socket and threaded PTypes so a form preselects the size from the selected port |
| `pForms.py` | Insert form. Socket-style forms reuse `_port2List` / `_port2DictList` / `_uniqueSizeList`, which the base `changeSize` already understands for preview file names |
| `CPipe.py`, `InitGui.py` | Command class, `addCommand(...)`, toolbar/menu list |
| `Quetzal_tooltips.py`, `iconz/` | Tooltip and SVG icon (`ViewProvider` resolves the icon name without `.svg`) |
| `pcf/pcf_map.py` | `KEYWORDS`, `keyword_and_skey`, and `ptype_for` for import. Use `SKEY_PTYPES` when a type shares a PCF keyword with another PType |
| `pcf/pcf_export.py` | Endpoint branch in `component_for`, and the BOM description lead in `_item_code` |
| `pcf/pcf_import.py` | `_build_*` builder, `BUILDERS`, the single-port checks in `correspondences` / `solve_rotation`, and end ordering for reducer-like parts |
| `pcf/pcf_catalog.py` | Resolver in `_RESOLVERS`, for files from other programs that carry no Quetzal record |
| `Nomeclature.md` | New property letters |

A new **rating** of an existing family needs only a CSV. The forms list
`<PType>_*.csv` files as grades and treat a table as socket or threaded when
its `Conn` column is `SW` or `TH`. Threaded fittings reuse the `Socket*`
classes with `Conn = "TH"`.

## Tables

- `;`-delimited and read as `utf-8-sig`. Keys are DN sizes (`DN20`), and every
  dimension is in mm.
- `Union_*` files have **no header row**. Readers pass the field names
  themselves (`UNION_COLUMNS`, `_UNION_FIELDS`).
- Write tables with a generator script, not by hand, and keep the mapping
  rules in its comments. The script lives outside the repo, so put the rules
  where the next person will find them (e.g. a class docstring).
- Port convention: a socket or threaded port sits at the **socket or thread
  bottom**, where the pipe end rests, and points outward. A male end's port
  sits at its tip, like a pipe end.
- Thread engagement is (L1 + L2)/2 from ASME B1.20.1. The threaded tables use
  it as their socket depth.

## Getting dimension data

- WebFetch often refuses to reproduce dimension tables, or summarizes them
  with columns shifted. Download the page with `curl` and parse the `<table>`
  rows yourself. In Git Bash on Windows, set `PYTHONIOENCODING=utf-8` and read
  the HTML as `latin-1`.
- **Read what each letter means before using it.** When a page doesn't label its letters, say what you inferred.
- Check derived values against a second source, or flag them as unchecked.

## Geometry checks that caught real problems

- **Ports lining up is not enough.** Mated parts can still overlap.
  - A male end seated by its tip in a socket deeper than the thread
    engagement sank its hex 8 mm into the fitting.
  - Run `spatial_query batch_interference` on every assembly.
  - Mated parts should show zero or near-zero volume. About 0.7 mm³ per
    pipe-in-socket joint is a known artefact: the pipe table's OD is 0.001 mm
    over the fitting's socket.
- Check **what the joint looks like**, not just that it closes. A made-up
  threaded joint leaves a gap between the hex and the fitting face.
- **Build every row of a new table** in a scratch document. Check
  `Shape.isValid()`, a single solid, positive volume and the port positions.
  This catches thin walls and inverted lengths that a single test part won't.
- Where a body and its socket band have the same radius, fusing leaves seam
  rings. `cut(...).removeSplitter()` merges them without changing the volume.

## Working in the live FreeCAD session over MCP

See also the `quetzal-piping` skill in the AI_Piping_Design repo ([§2 and §10](https://github.com/petermaginot/AI_Piping_Design))
for the session preamble and verification recipe.

- **FreeCAD runs the installed copy** (`%APPDATA%\FreeCAD\v1-1\Mod\Quetzal`),
  not this checkout. To test repo code:
  1. Check that the installed modules match `HEAD`.
  2. Put the repo root first on `sys.path`.
  3. Delete `pFeatures`, `pCmd`, `pForms`, **`dodoDialogs`** and `pcf*` from
     `sys.modules`, then re-import.

  `dodoDialogs` matters: forms scan `tablez/` next to that file, so a stale
  copy hides new CSVs and leaves the grade list empty.
- New tables don't reach the installed workbench until they're copied there.
  A rating missing from a form is a deployment problem, not a code bug.
- **Autosave**: the bridge saves the active document before each call if it
  has a file path and unsaved changes.
  - Create scratch documents with `view_control create_document` before the
    first `execute_python`.
  - Check `FileName` and `Modified` on every open document before touching
    any of them.
- **Leave the user's model as you found it.** When asked to change a part in
  it, change only its properties, inside one transaction, and say what you
  changed. Report anything odd (such as a part sitting 1" off its port)
  rather than "fixing" it.
- `execute_python` keeps its namespace between calls. Redefine helpers
  rather than relying on stale ones, and keep each call under about 50 s.
- `FreeCADGui.insert` / `export` fail inside the MCP context. Call
  `pcf_export.export(...)` and `pcf_import.insert(path, docname)` directly.
- Maker signatures differ. `pCmd.makePipe(rating, propList)` takes the rating
  first. `makeOutlet` puts the rating inside the propList and takes a
  `Rotation`. `makeSocketElbow` / `makeSocketTee` take `rating=`.
- Screenshots: `ActiveView.saveImage(path, w, h, "White")`, then read the
  PNG. Rasterize iso SVGs with `PySide6.QtSvg.QSvgRenderer`.
- Open the forms from code too: set the grade and size, call `insert()`,
  and check `PRating` and `Conn` on the new object. This caught grades that
  were stored wrong.

## Testing

- `python -m unittest discover -s pcf/tests -t .` and
  `python -m unittest discover -s iso/tests -t .` run with system Python, no
  FreeCAD needed.
- `pcf_map` imports FreeCAD (through `quetzal_units`), so test PCF mapping
  live: export, then import with and without the `COMPONENT-ATTRIBUTE99`
  record. Also import a file whose end points are in the other order.
- After a PCF change, build an iso sheet from the exported file
  (`iso_build.build_sheet`) and read its BOM and warnings.

## Editing files

- The working tree uses **CRLF** (`core.autocrlf=true`). Appending LF text
  leaves files with mixed line endings. When scripting edits, read with
  `newline=None` and write with `newline="\r\n"`, and assert each `old`
  string occurs the expected number of times.
- In Git Bash, `grep -c $'\r'` reported 0 on CRLF files. Count line endings
  with Python instead.
- Large heredocs containing quotes can break the Bash tool. Write the
  content to a scratch file and append or run it from there.
