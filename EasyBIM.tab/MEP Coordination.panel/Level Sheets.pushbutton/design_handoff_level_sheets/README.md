# Handoff: Level Sheets command (EasyBIM Revit ribbon)

> ## AMENDED — the layout changed after this document was written
>
> **The ceiling plan is overlaid directly ON TOP OF the floor plan, not stacked above
> it.** Both viewports occupy the same position on the sheet, model-aligned so every
> grid line coincides, with the RCP drawn in front.
>
> Changed at the client's request during testing. Wherever this document says
> *stacked*, *RCP on top*, *RCP above FP*, or shows the two plans one above the other,
> read it as the overlay instead.
>
> Two further changes from the same testing:
>
> - Both views are cropped to one shared rectangle, taken from a scope box the tool
>   resolves for itself and **padded** until no annotation overhangs it. The pad is not
>   cosmetic: a viewport's box is the hull of its visible content, so without it the two
>   plans do not register. See the `place_overlaid()` docstring in `script.py`.
> - The dialog asks for three things — title block, templates/scale, scope box. The
>   floor-plan and ceiling-plan type pickers were removed (the project default is used),
>   and the scope box starts unselected because it decides what both plans are cropped
>   to.
>
> Everything else here — naming standard, level codes, `EB_Level_Code`, sheet numbering
> from 200, templates, re-run suffixing, the report — is unchanged and still current.


## Overview
**Level Sheets** is a command on the **MEP Coordination** panel of EasyBIM's Revit ribbon
(EasyBIM tab), replacing the "Button3" stub. It batch-creates plan views and sheets from
selected levels: for every selected level it creates a **floor plan** and a **ceiling
(RCP) plan**, applies the `EB_MEP_FP_1-50` / `EB_MEP_CP_1-50` view templates, renames both
views to the EasyBIM standard, and places them together on **one new sheet per level** (RCP
above floor plan, horizontally aligned to the model so grids run continuously across sheets).

End-user goal: turn "create plan + RCP + sheet for every level" — normally dozens of repetitive
clicks per level — into one dialog + one Create click.

This is **Stage A only**: view/sheet creation and naming. Nothing about drawing content,
annotation, or downstream coordination is in scope.

## About the design files
The files in `reference/` are a **design reference built in HTML/React (prototype)** — they
define the look, copy, and behavior. They are **not the production implementation**. The real
command runs inside a **pyRevit** extension (Python / Revit API); recreate this dialog's
layout, copy, and flow in pyRevit forms (or WPF), wiring each control to the Revit API.

- `reference/ribbon-level-sheets.jsx` — **the primary spec.** All UI, validation, the
  code-suggestion heuristic, and the run report.
- `reference/ribbon-tools.jsx` — shared EasyBIM dialog shell (header / banner / footer) and
  the host that mounts the tool.
- `reference/ribbon-icons.jsx` — the Lucide `Icon` helper + the custom `LevelSheets` glyph.
- `reference/ribbon-data.jsx` — sample data (`SHEET_LEVELS`, `TITLE_BLOCKS`, `VFT_FP`,
  `VFT_CP`, `PLAN_TEMPLATES`) + the `levelSheets` ribbon button definition.

## Fidelity
**High-fidelity.** Colors, typography, spacing, copy, and icons are final — recreate faithfully
with EasyBIM's tokens (navy `#1e248c` / cyan `#44b8d3`; see Design Tokens). Field labels and
helper text are intended copy.

---

## The dialog: structure

A single window (no wizard steps), width ≈ 880px (wider than other EasyBIM dialogs — this one
is a data grid, not a form):
- **Header** — gradient icon tile (navy→cyan) with the `LevelSheets` glyph + title
  **"Level Sheets"** + subtitle *"Batch plan views & sheets from levels · Stage A"* + close.
- **Info banner** — *"Pick the levels to build. Each one gets a floor plan and a ceiling plan
  on their own sheet — the code below drives every name, so a level's Revit name never reaches
  the output."* Hidden on the report screen.
- **Body** — Setup panel, or after Create, the Report panel.
- **Footer** — left: live summary ("N levels · N×2 views · sheets 200–20N"). Right:
  Cancel + **Create** (primary, `Play`). **Create is disabled** until validation passes.

### Setup panel (top → bottom)

1. **Template preflight strip** — resolves `EB_MEP_FP_1-50` / `EB_MEP_CP_1-50` **by exact
   name** at open time. Green + `ShieldCheck` + `Ready` badge when both found; red +
   `TriangleAlert` and blocks Create when either is missing. **Never fuzzy-matched** — exact
   name only.
2. **Level grid** — the core UI. One row per level in the model, **sorted by elevation**
   (ascending). Columns: checkbox · Level (Revit name, as-is, may be non-English) · Elev (m,
   monospace, right-aligned) · an **editable `EB_Level_Code` field** · a **Source** pill
   (`stored` navy / `suggested` grey / `edited` cyan) · a live preview of the resulting sheet
   number + name.
   - **The level's Revit name is never parsed for the code.** Codes come from (in priority
     order): a value already stored in `EB_Level_Code` on the Level → an elevation-order
     heuristic suggestion (ground = `GF00`, below = `B01, B02, …`, above = `F01, F02, …`, top =
     `RT`) → the user's manual edit (always wins).
   - Header row has a select-all checkbox; a "Select all / Deselect all" text link sits above
     the grid.
   - **Row-level validation** highlights in red: empty code on a selected level, or a
     duplicate code among selected levels. The affected preview cell explains why
     ("code required" / "duplicate code") instead of showing a sheet number.
3. **Sheet & view types** — three dropdowns: **Title block** (`TITLE_BLOCKS`), **Floor plan
   type** (`VFT_FP`, a `ViewFamilyType` filtered to `ViewFamily.FloorPlan`), **Ceiling plan
   type** (`VFT_CP`, filtered to `ViewFamily.CeilingPlan`).
4. **Output · EasyBIM standard** — a locked 4-row reference card (not editable, just shown so
   the user knows what will happen): view names (`CD_Floor Plan_{code}` / `CD_Ceiling
   Plan_{code}`), sheet number/name rule (sequential from **200**, bottom level first; name =
   `{code}-MEP`), layout rule (RCP above floor plan, horizontally aligned to the model), scale
   rule (from template; **never auto-rescaled** — a level whose extent doesn't fit the title
   block at that scale is flagged, not rescaled).
5. **Validation error list** — appears only when something blocks Create; each line has a
   `TriangleAlert` icon + a plain-language reason.

### Report panel (after Create)
- Centered success: emerald check, **"N level sheets created"**, sub-line *"N×2 views · one
  undo step · codes written to `EB_Level_Code`"*.
- **Four stat tiles:** Levels processed (navy) · Sheets created (emerald) · Views created
  (cyan) · Warnings (amber if any, else grey).
- **Per-level table:** Sheet # · Sheet name · Floor plan view name · Ceiling plan view name ·
  Warning column (e.g. *"did not fit at 1:50 — scale preserved"*, or on a re-run, *"name &
  number suffixed (re-run)"*).
- Footer note: scale is never changed automatically, only reported; **re-running on the same
  model suffixes clashing names/numbers** rather than failing outright.
- Footer buttons: **Run again** (secondary, `RotateCcw`, returns to Setup incrementing a
  run-index used for the suffix demo) + **Open first sheet** (primary, `ExternalLink`).

---

## Interactions & behavior
- **Level grid:** per-row checkbox; per-row editable code (typing marks it `edited`, which
  always overrides `stored`/`suggested`); select-all/deselect-all.
- **Validation (gates Create):** ≥1 level selected; every selected level has a non-empty,
  unique code; both view templates resolve by exact name; a title block is chosen; both view
  family types are chosen.
- **Create → report:** instant in the prototype; in the add-in this is where the actual Revit
  transactions run (single `Transaction`/`TransactionGroup` = "one undo step" per the report
  copy) — show progress if slow.
- **Run again** resets to Setup, keeping the same levels ready to re-run (increments an
  internal counter used to demonstrate the suffix-on-collision behavior in the report).
- Motion/states follow EasyBIM: primary button navy→cyan + lift on hover, scale 0.98 on press;
  cyan focus ring on inputs/selects; row highlight on validation error.

## What the command should actually do (Revit API mapping)
1. **Collect levels** in the host model (`FilteredElementCollector(doc).OfClass(Level)`),
   sort by `Elevation`.
2. **Resolve each level's code:** read `EB_Level_Code` (a project parameter on `Level` — create
   it if missing, per project setup) → if empty, compute the elevation-order suggestion → let
   the user edit before running. **Never parse the code from `Level.Name`** — level names are
   commonly inherited from an architect's link via copy/monitor and are unreliable / may be
   non-English.
3. **Resolve templates by exact name:** `EB_MEP_FP_1-50`, `EB_MEP_CP_1-50` via
   `FilteredElementCollector(doc).OfClass(View)` where `IsTemplate` and `Name` matches exactly.
   Abort with a clear error if either is missing — do not fuzzy-match or create a fallback.
4. **Per selected level:**
   - Create a `ViewPlan.Create(doc, vftFp.Id, level.Id)` and a `ViewPlan.Create(doc,
     vftCp.Id, level.Id)` (RCP uses the Ceiling Plan `ViewFamilyType`).
   - Apply the resolved view template to each (`View.ViewTemplateId`).
   - Rename to `CD_Floor Plan_{code}` / `CD_Ceiling Plan_{code}`.
   - Create a new `ViewSheet.Create(doc, titleBlockTypeId)`, number = next sequential from
     **200** (bottom level first), name = `{code}-MEP`.
   - Place both views as `Viewport`s on the sheet: RCP viewport **above** the floor-plan
     viewport, both **horizontally aligned to the model** (so the same grid line lands at the
     same X across every level's sheet — critical for scanning a stack of sheets).
   - If the level's plan extent doesn't fit the title block at the template's scale: **do not
     rescale** — leave the template scale as-is and record a warning for the report.
5. **Re-run safety:** if a view/sheet name or number from a previous run already exists,
   **suffix** rather than fail (e.g. `_2`, sheet number `-2`) — the tool must be safely
   re-runnable on the same model.
6. Write the resolved code back to **`EB_Level_Code`** on the `Level` (only for levels
   actually processed) so a second run recognizes it as `stored`.
7. Wrap all of the above in **one transaction / transaction group** (report says "one undo
   step").
8. Return per-level results (sheet number/name, both view names, any warnings) for the report.

> `EB_Level_Code` should be a project (or shared) parameter on the `Level` category — confirm
> whether it already exists in the EasyBIM template or needs to be created/bound on first use.

## Design tokens
From the EasyBIM Design System (navy/cyan glassmorphic):
- **Navy (primary / headings / primary button):** `#1e248c`
- **Cyan (accent / hover / focus / selection / "edited" source pill):** `#44b8d3`
- **Success/emerald:** `var(--eb-success)`; **warning/amber:** `#e0851e` /
  `var(--eb-warning)`; **error:** `var(--eb-error)` (~`#d64545`).
- **Muted text:** `#8b93a7` / `#9aa0ac` / `#6b7280`; **body:** `#374151`; headings: navy.
- **Card fill:** `#fff`; **locked/subtle fill:** `#fafbff`; **grid header fill:** `#f4f6fd`;
  **card border:** `var(--eb-line)`; **soft divider:** `var(--eb-line-soft)`.
- **Radius:** cards/inputs `var(--radius-lg)`, dialog `var(--radius-xl)`, pills full.
- **Type:** display `var(--font-display)` (Hanken Grotesk), body `var(--font-body)` (Inter),
  mono `.eb-mono` (JetBrains Mono — level codes, elevations, sheet/view names). Section labels:
  mono, 10.5px, uppercase, 0.06em tracking, `#9aa0ac`. Grid column headers: mono, 9px,
  uppercase, bold, `#8b93a7`.
- **Icons:** [Lucide](https://lucide.dev), 1.5–2px stroke: `Rows3`, `ShieldCheck`,
  `TriangleAlert`, `LayoutTemplate`, `FileText`, `Ruler`, `Check`, `RotateCcw`,
  `ExternalLink`, `Play`, `Info`, `Lock`, `X`. Command icon: custom **`LevelSheets`** glyph
  (sheet border + two stacked plan rectangles + a title-strip line — see
  `reference/ribbon-icons.jsx`).

## Assets
- **Command icon:** `assets/icon.png` (32px) + `icon_16/64/96.png` — the navy `LevelSheets`
  glyph rendered to PNG for the pyRevit ribbon button.

## Files (in `reference/`)
- `ribbon-level-sheets.jsx` — the dialog design source (primary spec: Setup, Report, footer,
  validation, code-suggestion heuristic).
- `ribbon-tools.jsx` — shared dialog shell + host (`ToolHost`'s stateful-tool wiring).
- `ribbon-icons.jsx` — Lucide `Icon` helper + `LevelSheets` glyph.
- `ribbon-data.jsx` — sample data (`SHEET_LEVELS`, `TITLE_BLOCKS`, `VFT_FP`, `VFT_CP`,
  `PLAN_TEMPLATES`) + the `levelSheets` ribbon button definition
  (`EasyBIM.Commands.CreateLevelSheetsCommand`, since v2.5).

The live prototype is `EasyBIM Ribbon.html` in the project root; click **Level Sheets** on the
MEP Coordination panel to open the dialog.

---

## pyRevit scaffold (`pyrevit_scaffold/`)
A starting point for the real command — a **scaffold, not a finished feature**:

```
pyrevit_scaffold/Level Sheets.pushbutton/
├── script.py     # entry + Revit-API helper stubs (level/template gathering
│                 #   sketched; view/sheet creation = NotImplementedError)
├── bundle.yaml   # pyRevit button metadata (title / tooltip / author)
└── icon.png      # the LevelSheets ribbon icon (96px)
```

**Before writing code:** inspect the existing "MEP Solution Section" and "Head Height Check"
buttons' bundles and **match their engine** (IronPython 2.7 vs CPython3) — do not introduce a
third engine into the extension. Reuse shared helper modules where they already exist (e.g.
link/level collection, workset helpers).

**Remaining work:**
1. Create or confirm the `EB_Level_Code` parameter on `Level`.
2. Implement the elevation-order code-suggestion heuristic (see spec above).
3. Build the real Setup dialog (level grid + pickers + validation) matching
   `reference/ribbon-level-sheets.jsx` — pyRevit forms or WPF.
4. Implement `run()`: view creation, template application, renaming, sheet creation, aligned
   viewport placement (RCP above FP), the collision-suffix retry logic, and the report data.
