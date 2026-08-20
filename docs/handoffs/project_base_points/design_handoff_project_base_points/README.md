# Handoff: Project Base Points (PBP) — BIM Management panel

## Overview
**Project Base Points** is a command on the **BIM Management** panel of EasyBIM's Revit
ribbon. It replaces an existing Dynamo graph: it reads the Project Base Point of the host
model and every directly-placed link, matches each discipline model to its Architecture (AR)
reference model, flags coordinate/angle mismatches and models not in shared coordinates, and
exports a report (HTML / PDF / Excel) — the Excel sheet is a ready-to-import Autodesk
Construction Cloud (Forma/ACC) issue list.

## About the design files
Everything in `reference/` is a **design reference built in HTML/React (prototype)** — it
specifies layout, copy, states and behavior. It is **not production code to copy verbatim**.
The real command runs inside the EasyBIM pyRevit extension (Python, IronPython/CPython3 via
pyRevit, Revit API). The task is to **recreate this dialog's screens and logic against the
Revit API**, following the extension's existing command conventions (see `addin_scaffold/`).

## Fidelity
**High-fidelity.** Colors, typography, spacing, copy and interaction states are final. Recreate
faithfully using EasyBIM's design tokens (navy `#1e248c` / cyan `#44b8d3` — see Design Tokens).
The exact pixel layout of a WPF/pyRevit dialog will differ somewhat from the HTML prototype —
match structure, hierarchy, states and copy, not CSS.

---

## Status vocabulary (drives everything downstream)

Every link (and the host) resolves to exactly one status. This vocabulary is fixed —
don't rename or add to it:

| Status | Color | Meaning | Exported as ACC issue? |
|---|---|---|---|
| `OK` | green | Within tolerance of its AR reference | No |
| `Not OK` | orange | Outside tolerance (elevation, angle, or plan position, or a combination) | **Yes** |
| `Not Shared` | red | Link is not published/acquired to the shared coordinate system | **Yes** |
| `Missing Ref` | grey | No AR reference model matched — a Revit/link setup problem, not a coordination issue | No |

`Not Shared` and `Missing Ref` never produce ACC issues — they flag something to fix in Revit
itself (link needs "Acquire Coordinates", or the AR model for that building isn't loaded/named
correctly).

Additional non-audit states shown in the table: `Reference` (this row IS an AR baseline),
`Unloaded` (link exists but isn't loaded), `Host` (the host model's own row).

### Tolerance check
- Position tolerance in mm (default 1mm), applied independently to N/S, E/W, and elevation
  deltas vs. the matched reference.
- Angle tolerance in degrees (default 0.02°), applied to the angle-to-true-north delta
  (shortest-path difference, i.e. wraps at ±180°).
- `Not OK` if ANY of N/S, E/W, elevation, or angle exceeds its tolerance.

### Matching a link to its AR reference
1. Every link and the host expose a `key` — the building/zone code parsed from the file name
   (naming convention: `PROJECT-DISCIPLINE-FIRM-BUILDING-REVITVERSION`, e.g.
   `MSH-ST-XYZ-BLD_A-R25` → key `BLD_A`).
2. A link is Architecture if its discipline code is one of `AR`, `ARC`, `ARCH`, `A`.
3. Auto-matching picks the **first placed AR link sharing the same key** as the reference for
   every non-AR link with that key. This is best-effort — the user can override the reference
   (and the discipline) per row in the results table; nothing here should be hard-coded as
   unchangeable.
4. If no AR link exists for a key, every link with that key reports `Missing Ref`.

---

## Discipline code → Role → Hebrew discipline mapping

Every link's discipline **code** (2-letter EasyBIM convention, or a single ISO 19650 /
BS 1192 originator letter) auto-fills a **Role** (hub role, English) and a **דיספלינה**
(Hebrew discipline, from a fixed hub-configured list) — both stay user-editable per project.
This exact table must be preserved (see `reference/ribbon-base-points.jsx`, `DISC_MAP`):

**EasyBIM two-letter codes:** `AR/ARC`→Architect·אדריכלות · `ST`→Structural Engineer·קונסטרוקציה
· `ME`→Mechanical Engineer·מיזוג אוויר · `EL`→Electrical Engineer·חשמל ·
`PL`→Plumbing Engineer·אינסטלציה · `FP`→Fire Safety·כללי · `SN`→Sanitation·אינסטלציה ·
`LS`→Landscape·אדריכלות נוף · `EV`→Elevators·כללי · `AC`→Accessibility·כללי ·
`SF`→Safety·כללי · `TR`→Traffic Engineer·כללי · `QS`→Quantity Surveyor·כללי ·
`IN`→Interior Designer·אדריכלות · `CO`→BIM Manager·כללי · `CE`→Geotechnical Engineer·קונסטרוקציה
· `SV`→Surveyor·כללי.

**ISO 19650 originator letters:** `A`→Architect·אדריכלות · `B`→Construction Manager·כללי ·
`C`→Geotechnical Engineer·קונסטרוקציה · `D`→Plumbing Engineer·אינסטלציה ·
`E`→Electrical Engineer·חשמל · `F`→Project Manager·כללי · `G`→Surveyor·כללי ·
`H`→Mechanical Engineer·מיזוג אוויר · `I`→Interior Designer·אדריכלות · `K`→Client·כללי ·
`L`→Landscape·אדריכלות נוף · `M`→Mechanical Engineer·מיזוג אוויר · `P`→Plumbing Engineer·אינסטלציה
· `Q`→Quantity Surveyor·כללי · `S`→Structural Engineer·קונסטרוקציה · `T`→Project Manager·כללי ·
`W`→Construction Manager·כללי · `X`→other·כללי · `Y`→Fire Safety·כללי · `Z`→BIM Manager·כללי.

The Hebrew (דיספלינה) values are constrained to a fixed hub list (`HEB_OPTIONS`): אדריכלות,
קונסטרוקציה, אינסטלציה, חשמל, מיזוג אוויר, אדריכלות נוף, כללי.

The hub Role list (`HUB_ROLES`, ~35 roles: Architect, Structural Engineer, Electrical
Engineer, Mechanical Engineer, Plumbing Engineer, BIM Manager, VDC Manager, Fire Safety,
Landscape, Interior Designer, Surveyor, Quantity Surveyor, Safety, Accessibility, Elevators,
other, …) is provisional — **confirm the exact role names against the real EasyBIM Hub
project settings before shipping**; it must match Format's role list character-for-character
or assignment will fail on import.

---

## The dialog: structure

Single window, ~1120px wide, with a **Setup** stage, a **Results** stage, and — reached from
Results — an **Export format**, **ACC issue fields**, and **Done** stage.

### Setup
- Host model card (name, path, units, Workshared badge).
- **Architecture reference models table**: every AR link found, with building key, shared-site
  name (or `Not Shared` in red), elevation, angle. Empty state warns every link will report
  `Missing Ref` until disciplines are re-mapped.
- Five stat tiles: Link instances, Nested·skipped, AR references, Not shared, Unloaded.
- **Tolerances & options** — collapsed disclosure: position tolerance (mm), angle tolerance
  (deg), the AR code list, and four toggles: list unloaded links, write a run log, remember
  mapping/destination for this host model, "Not in EasyBIM Hub" (project sits on another hub —
  issue export asks for a template issue report + its own role names instead of the built-in
  hub list).
- Info strip explaining the key-based matching, with a worked example (`ST-BLD_A` → `AR-BLD_A`).

### Results
- **Coordination chart**: collapsible, one horizontal stacked bar per discipline (OK / Not OK /
  Not Shared / Missing-ref segments), auto-collapses to a one-line summary when the dialog is
  short. AR rows and excluded rows never enter the chart.
- Filter chips: All rows / Not coordinated / Not shared / Unresolved, plus a live count badge
  on each.
- **The table** — 12 fractional-width columns that always fit without horizontal scroll:
  include-checkbox · Link (+instance) · Disc (editable select) · Bldg (key) · Shared Site ·
  Workset · N/S (m) · E/W (m) · Elev (m) · Angle · Reference (AR) (editable select) · Check
  (status pill). Sticky header + a per-column filter row (text inputs, or a `<select>` for
  Disc/Check). Sortable by clicking any header.
  - The header checkbox toggles include/exclude for every currently-visible row.
  - **Include/exclude checkbox per row**: unticking a link drops it from the chart, the report,
    and the issue export — but the row stays visible in the table at ~40% opacity, so the user
    can still review/re-include it. The host row has no checkbox (always included).
  - **Discipline** and **Reference (AR)** are always editable per row (dropdowns), regardless
    of the auto-detected value. A hand-edited cell gets a cyan border/tint to show it's been
    overridden. The status re-computes live off whatever discipline/reference is currently set.
  - Reset mapping button restores auto-detected refs/disciplines and clears all exclusions.

### Export → format choice
Pick any combination of **Interactive HTML**, **PDF** (A4 landscape snapshot), **Excel · ACC
issue import** (one row per flagged link on ACC's issue-import template). Toggles: only rows
needing attention vs. everything, include Missing-Ref rows in the *document* report (never in
the Excel — Missing Ref is never an issue), include the chart, open files when done. A
remembered destination folder (editable path + Browse). Filenames are built as
`{host model name}_PBP_Report_{date}` — **host link name always comes first** so files sort
and identify correctly across projects.

### Export → ACC issue fields (only if Excel/xlsx picked)
This is the most complex screen — it configures everything ACC's issue-import sheet needs
before the Excel is written:

1. **Custom fields**, three modes (radio-style buttons): **None**, **Manual** (comma-separated
   field names typed by the user, spelled exactly as configured on the hub), **Import** (read
   field names from a real issue-report export from the target ACC project — simulated in the
   prototype, a real file picker + XLSX header read in production). Any field is then mapped to
   `discipline` (fills from the link's Hebrew discipline), `building` (fills from the link's key),
   or left free — free fields become an editable column in the Preview, filled per-row.
   Discipline/Location map themselves automatically by matching common name patterns
   (`disciplin`/`trade`/`דיספלינה`, `location`/`building`/`zone`/`area`/`block`/`מיקום`).
2. **Issue defaults**: Title (base point mismatch case) and Title (not-shared case), Status,
   Category, Type, Assignee Type (`role` or `company`), Due-in-days.
3. **Assignee list**: one textarea, comma-separated, whose meaning switches with Assignee Type
   — role names or company names, both validated against what's typed (falls back to the
   built-in 35-role hub list if left empty in role mode).
4. **Descriptions** (Hebrew, RTL textareas): four pre-written, fully swappable templates — wrong
   elevation, wrong angle, both, and not-shared. The correct one is picked per row from what
   actually differs, then the tool appends the measured gaps and the reference model's name
   automatically. **Never rewrite this Hebrew copy without being asked — it's final legal/process
   language, not placeholder.**
5. **Per-discipline assignment table**: one row per discipline code actually present among
   flagged links — Code · Role (or Company, if Assignee Type = company) · דיספלינה · issue
   count. Role/דיספלינה auto-fill from the code (cyan border = hand-edited).
6. **Preview** — the full issue grid ACC will receive: every standard ACC column (Title,
   Description, Status, Assigned To, Category, Type, Start Date, Due Date) plus every custom
   field, in one scrollable, sortable, filterable, per-cell-editable grid, with bulk multi-row
   edit (select rows → pick a column → set a value → apply to all selected). Date columns use
   native date pickers. `Assigned To` is highlighted orange while empty.

### Done
Success state: file list written (name + destination), issue-row count if Excel was included,
and — if Excel was included — instructions to import via ACC's `Issues ▸ ⋯ ▸ Import issues`.

---

## What the command should actually do (Revit API mapping)

1. **Collect host + links.** `FilteredElementCollector` over `RevitLinkInstance` in the active
   document; read each link's transform to get the **linked model's Project Base Point**
   expressed in the **host's shared coordinate system** — this is the N/S, E/W, Elev, and
   angle-to-true-north the table shows (not the link's own internal PBP). Do this for
   directly-placed links only; **nested links (links inside links) are explicitly excluded**
   from the audit and only counted in the "Nested · skipped" stat.
2. **Shared coordinates check.** A link's `shared` state ("Not Shared" vs. a named shared
   site) comes from whether the link has been "Acquired"/published to shared coordinates
   (`ProjectPosition`/`SharedPositioning` APIs, or equivalently whether its PBP resolves in the
   host's shared coordinate system at all).
3. **Discipline + building key.** Parse from the **link's file name**, convention
   `PROJECT-DISCIPLINE-FIRM-BUILDING-REVITVERSION` (see `reference/ribbon-data.jsx` sample rows
   for exact examples). This is a heuristic — always user-overridable in the results table, same
   caution as Level Sheets' code parsing (don't silently trust a bad parse).
4. **Auto-reference matching, tolerance check, status** — pure logic, no Revit API needed once
   the above data is collected; port `statusOf`/`deltasOf`/`autoRefs` from
   `reference/ribbon-base-points.jsx` directly.
5. **HTML/PDF report** — render the same chart + table structure; PDF is a print/landscape
   snapshot of the filtered document scope.
6. **Excel/ACC issue sheet** — write ACC's issue-import template columns (see `ACC_COLS` in
   `reference/ribbon-base-points-export.jsx`) with one row per flagged (`Not OK` / `Not Shared`)
   *included* link, columns and values exactly as configured in the wizard. `Location` is
   **always left empty** — it resolves against the target project's Locations breakdown
   structure in ACC, which is separate admin setup; the building code goes into a custom field
   instead if the user has mapped one to `building`.
7. **Preferences.** Persist last-used tolerances, options, destination folder, and full ACC
   wizard state (roles, custom-field mapping, description templates) per **host model**, so a
   re-run doesn't ask again. `reference/ribbon-base-points.jsx`'s `loadPrefs`/`savePrefs`
   (`localStorage`) map to whatever local settings store the pyRevit extension already uses.

## Design tokens
Same EasyBIM system as other tool dialogs:
- **Navy** `#1e248c` (primary/headings), **Cyan** `#44b8d3` (accent/hover/focus/selection/edited-cell indicator).
- **Status colors:** OK = `var(--eb-success)` (green), Not OK = `#e0851e` (orange), Not Shared = `var(--eb-error)` (red), Missing Ref/muted = `#8b93a7`.
- **Brand gradient (icon tiles):** `linear-gradient(135deg, #1e248c, #44b8d3)`.
- **Body text** `#374151`; muted `#6b7280`/`#9aa0ac`.
- Card fill `#fff`; card border `var(--eb-line)`; soft divider `var(--eb-line-soft)`; radius `var(--radius-lg)`.
- Type: display `var(--font-display)` (Hanken Grotesk), body `var(--font-body)` (Inter), mono `.eb-mono` (JetBrains Mono — codes, coordinates, angles, all grid data).
- Icons: Lucide, 1.5–2px stroke. Command icon: custom `ProjectBasePoint` glyph — a circle with a diagonal cross (see `reference/ribbon-icons.jsx`), matching Revit's own PBP glyph language.

## Files (in `reference/`)
- **`ribbon-base-points.jsx`** — Setup + Results screens, status/matching logic (`statusOf`,
  `deltasOf`, `autoRefs`), the discipline/role/Hebrew mapping table (`DISC_MAP`, `HUB_ROLES`,
  `HEB_OPTIONS`). Primary spec.
- **`ribbon-base-points-export.jsx`** — Export format screen, ACC issue-fields wizard, the
  editable preview grid, Done screen. Registers `window.PBPExport`.
- **`ribbon-icons.jsx`** — Lucide `Icon` helper + the custom `ProjectBasePoint` glyph path.
- **`ribbon-data.jsx`** — sample data: `PBP_ROWS` (host + link rows with realistic file names,
  coordinates, shared/unshared/unloaded examples), `PBP_HOST`.
- **`ribbon-tools.jsx`** — shared EasyBIM dialog shell (header/banner/footer chrome) that mounts
  this tool's state; shows how `BasePoints.initialState()` / `Body` / `footer` plug into the
  common dialog frame.

The live prototype is `EasyBIM Ribbon.html` in the design project; open it and click **Project
Base Points** on the BIM Management panel to click through every screen.

## Addin scaffold (`addin_scaffold/`)
A starting **pyRevit pushbutton** file layout matching the extension's existing command
structure — empty/stubbed Python modules with docstrings pointing at the section of this README
and the reference file that specifies each piece. Not runnable as-is; it's a map for where
each piece of logic belongs, not an implementation.
