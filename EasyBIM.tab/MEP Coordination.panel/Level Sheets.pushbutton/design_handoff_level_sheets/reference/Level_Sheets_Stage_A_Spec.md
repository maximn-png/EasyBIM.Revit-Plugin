# EasyBIM — "Level Sheets" Button (Stage A) — Implementation Spec

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


**Audience:** the developer/agent implementing this (Claude Code).
**Deliverable:** a Revit add-in button that batch-creates plan views and sheets from selected levels.
**Status:** Stage A only. Build and ship this, then stop for testing. **Do not implement anything beyond Stage A.**

---

## 1. Objective

Add one button to the EasyBIM ribbon. When clicked it opens a dialog; the user picks a set of levels and a few options; on confirm, for **each selected level** the add-in:

1. Creates a **floor plan** view and a **ceiling (RCP)** view.
2. Applies the standard **view template** to each.
3. **Renames** each view to the EasyBIM standard.
4. Creates **one sheet** for the level and places the two views on it, **stacked (RCP on top, floor plan below) and horizontally aligned to the model** so grid lines run continuously between them.

The output naming must always follow the EasyBIM standard, regardless of how the (architect-linked, copy/monitored) levels are named.

---

## 2. Scope

### In scope (Stage A)
- Ribbon button + `IExternalCommand` entry point.
- Modal dialog: level selection with an editable standard-code column, title-block picker, FP/RCP view-family-type pickers.
- Per-level: create FP + RCP `ViewPlan`s, apply templates by name, rename, create sheet, place two stacked viewports aligned to model.
- Persist the confirmed level code to a project parameter on the Level (`EB_Level_Code`).
- End-of-run summary (created / skipped / warnings).

### Out of scope (do **not** build now)
- Any annotation, dimensioning, tagging, legends, or schedules.
- Revisions, sheet issue data, printing, PDF/DWG export.
- Anything labelled "Stage B" or any feature not listed under *In scope*. **Stage B is undefined; do not anticipate it.**

---

## 3. Target environment & constraints

- **Platform:** Autodesk Revit desktop add-in, C# / .NET, using `Autodesk.Revit.DB` / `Autodesk.Revit.UI`.
- **Must run on multiple Revit versions.** Use only stable API surface. A few calls differ across versions — see §12. Prefer per-version conditional compilation (`#if REVIT2024 …`) or a thin per-version shim rather than reflection.
- **UI:** WPF preferred for the dialog (WinForms acceptable if it matches the existing EasyBIM stack).
- All model changes occur inside transactions (see §10).

---

## 4. Naming standard & fixed values

| Item | Value / format | Notes |
|------|----------------|-------|
| Level codes | `B01, B02, …` (basements) · `GF00` (ground) · `F01, F02, …` (floors) · `RT` (roof) | The `XXX` token used everywhere below. |
| Floor plan view name | `CD_Floor Plan_{code}` | e.g. `CD_Floor Plan_F01` |
| Ceiling plan view name | `CD_Ceiling Plan_{code}` | e.g. `CD_Ceiling Plan_F01` |
| Floor plan template | `EB_MEP_FP_1-50` | Looked up by exact name (`View.IsTemplate == true`). 1:50. |
| Ceiling plan template | `EB_MEP_CP_1-50` | Looked up by exact name. 1:50. Same scale as FP → grids match 1:1. |
| Sheet **Number** | `2ZZ` running from `200` (`200, 201, 202, …`) | Numeric string. Always starts at 200 each run. |
| Sheet **Name** | `{code}-MEP` | e.g. `F01-MEP` |
| Level-code parameter | `EB_Level_Code` (text, instance, bound to Levels) | Auto-created if missing; single source of truth for the code. |

Sheet numbers are assigned in **processing order = levels sorted bottom-to-top by elevation**, so the lowest level gets `200`.

---

## 5. Decisions log (confirmed with the client)

These are settled — implement as stated, don't re-litigate:

- **Views are created new** for every selected level (not "only where missing").
- **Level selection** is a user-picked subset in the dialog.
- **Layout:** stacked, **RCP on top, FP below**, **horizontally aligned to the model**.
- **Scale:** both templates are 1:50; rely on that for 1:1 grid alignment. If a view is too large to fit at that scale → **warn only, keep the scale** (never auto-rescale).
- **Level code source:** the arch level name is unreliable and may be non-English (e.g. Hebrew `מרתף -1`) — **do not parse the level name for the code.** The user assigns/confirms the code per level in the dialog; the add-in auto-*suggests* from elevation order.
- **Persistence:** confirmed codes are written to `EB_Level_Code` and read back first on later runs.
- **Title block & FP/RCP view-family types:** chosen by the user in the dialog.
- **Name/number collision:** append a suffix to make it unique (see §9.5).
- **View already on another sheet:** prompt the user to skip or place a duplicate/dependent (rare for brand-new views; keep the guard for safety).

---

## 6. Dialog (UX)

A single modal dialog, shown on button click. Contents:

1. **Level grid** — one row per `Level` in the model, sorted by elevation ascending. Columns:
   - ☑ *Include* (checkbox)
   - *Level name* (read-only, the real Revit name)
   - *Elevation* (read-only)
   - *Code* (editable text; pre-filled from `EB_Level_Code` if set, otherwise from the auto-suggestion in §7)
2. **Title block** — dropdown of loaded title-block types (`FamilySymbol`, category `OST_TitleBlocks`).
3. **Floor plan type** — dropdown of `ViewFamilyType` where `ViewFamily == FloorPlan`.
4. **Ceiling plan type** — dropdown of `ViewFamilyType` where `ViewFamily == CeilingPlan`.
5. **Create** / **Cancel** buttons.

Validation before running: at least one level checked; every checked level has a non-empty code; codes are unique among checked rows; a title block and both view types are chosen; both templates (`EB_MEP_FP_1-50`, `EB_MEP_CP_1-50`) exist in the model (if not, block with a clear message).

---

## 7. Level-code auto-suggestion (suggestion only — user can override)

Compute defaults to save typing; the user confirms in the grid.

1. Collect all `Level`s, sort by `Elevation` ascending.
2. Pick **ground** = the level whose elevation is closest to 0 → suggest `GF00`.
3. Levels **below** ground, going downward: `B01, B02, …`.
4. Levels **above** ground, going upward: `F01, F02, …`.
5. The **topmost** above-ground level: suggest `RT` instead of the next `F` number.

This is heuristic and deliberately non-authoritative (models vary — penthouses, split levels, mezzanines). The stored/confirmed value always wins.

---

## 8. Level-code parameter (`EB_Level_Code`)

Store the confirmed code on the Level so it is reusable, auditable, and schedulable.

- Ensure a **text, instance** project parameter named `EB_Level_Code`, bound to the **Levels** category, under the *Identity Data* group.
- Bound project parameters must be created from a **shared-parameter definition**. The add-in should manage its own shared-parameter file (stable GUID) so the definition is consistent across projects and machines.
- On each run: read the code from `EB_Level_Code` first (pre-fill the grid); after the user confirms, write the (possibly edited) code back.

Illustrative helper (adjust version-specific tokens per §12):

```csharp
// Ensure a text, instance project parameter "EB_Level_Code" bound to Levels.
static Definition EnsureLevelCodeParam(Document doc, Autodesk.Revit.ApplicationServices.Application app)
{
    const string NAME = "EB_Level_Code";

    // Already bound?
    var it = doc.ParameterBindings.ForwardIterator();
    while (it.MoveNext())
        if (it.Key is Definition d && d.Name == NAME) return d;

    // Create/find in the add-in's shared-parameter file.
    string prev = app.SharedParametersFilename;
    string spf  = Path.Combine(AddinDataDir, "EasyBIM_Shared.txt");
    if (!File.Exists(spf)) File.WriteAllText(spf, "");
    app.SharedParametersFilename = spf;
    DefinitionFile df  = app.OpenSharedParameterFile();
    DefinitionGroup dg = df.Groups.get_Item("EasyBIM") ?? df.Groups.Create("EasyBIM");
    var ext = dg.Definitions.get_Item(NAME) as ExternalDefinition
              ?? dg.Definitions.Create(
                     new ExternalDefinitionCreationOptions(NAME, SpecTypeId.String.Text)) as ExternalDefinition;
    app.SharedParametersFilename = prev;

    // Bind as INSTANCE parameter to Levels.
    var cats = app.Create.NewCategorySet();
    cats.Insert(Category.GetCategory(doc, BuiltInCategory.OST_Levels));
    var binding = app.Create.NewInstanceBinding(cats);
    doc.ParameterBindings.Insert(ext, binding, GroupTypeId.IdentityData);
    return ext;
}
```

---

## 9. Core algorithm (per run)

Wrap everything in one `TransactionGroup` (see §10). Preflight (title block, view types, template lookup, `EB_Level_Code`) may run in its own short transaction.

### 9.1 Preflight
- Resolve the two template views by name; abort with a message if either is missing.
- Resolve the chosen title-block `FamilySymbol`; if `!IsActive`, activate it (inside a transaction).
- Ensure `EB_Level_Code` (§8).
- Build the ordered work list: checked levels sorted by elevation ascending.
- Initialise a sheet counter at `200`.

### 9.2 For each level (own `Transaction`)
1. Write the confirmed code to `EB_Level_Code` on the level.
2. **Create FP view:** `ViewPlan.Create(doc, floorPlanVftId, level.Id)`.
   - `view.ViewTemplateId = fpTemplate.Id;`
   - `view.Name = UniqueName(doc, $"CD_Floor Plan_{code}");`
3. **Create RCP view:** `ViewPlan.Create(doc, ceilingVftId, level.Id)`.
   - `view.ViewTemplateId = cpTemplate.Id;`
   - `view.Name = UniqueName(doc, $"CD_Ceiling Plan_{code}");`
4. **Create sheet:** `ViewSheet.Create(doc, titleBlockId)`.
   - `sheet.SheetNumber = UniqueNumber(doc, counter++);`  // "200", "201", …, suffixed on clash
   - `sheet.Name = $"{code}-MEP";`
5. **Place viewports** stacked & model-aligned (§9.3). Guard each with `Viewport.CanAddViewToSheet`; brand-new views should always pass, but if one returns false, prompt skip vs duplicate/dependent and record the choice.
6. Record success in the run report.

Catch `Autodesk.Revit.Exceptions.*` per level; on failure, roll back that level's transaction, log the reason, continue to the next level.

### 9.3 Stacked, model-aligned placement
Both views share scale `s` (= `View.Scale`, e.g. 50). Key facts:
- `Viewport.Create(doc, sheetId, viewId, XYZ.Zero)` then reposition with `Viewport.SetBoxCenter`.
- `Viewport.GetBoxOutline()` returns the drawing's rectangle in sheet coordinates — **valid only after `doc.Regenerate()`**.
- 1 model foot → `1/s` paper feet. For an unrotated, project-north plan, model **X → sheet U** and model **Y → sheet V**, scaled by `1/s`.

Alignment requirement: the *same model X* must land on the *same sheet U* in both viewports, so vertical grids continue between them. That holds when both viewports' box centers correspond to the same model X. The offset between the two box-center U's must equal the difference of the two crop-box world-X centers divided by `s`.

Recommended, simplest robust method: **give both views the same crop region** (copy the FP crop box to the RCP, or set both to a common rectangle) so their box centers map to identical model extents; then center both at the same U. Fallback (if crops must stay as the templates set them): apply the offset formula below.

```csharp
static void PlaceStacked(Document doc, ViewSheet sheet,
                         ViewPlan rcpTop, ViewPlan fpBottom,
                         BoundingBoxUV usable, double gapFt)
{
    var vpTop = Viewport.Create(doc, sheet.Id, rcpTop.Id, XYZ.Zero);
    var vpBot = Viewport.Create(doc, sheet.Id, fpBottom.Id, XYZ.Zero);
    doc.Regenerate();                              // required before GetBoxOutline

    Outline oT = vpTop.GetBoxOutline();
    Outline oB = vpBot.GetBoxOutline();
    double hT = oT.MaximumPoint.Y - oT.MinimumPoint.Y;
    double hB = oB.MaximumPoint.Y - oB.MinimumPoint.Y;

    double cU = (usable.Min.U + usable.Max.U) / 2.0;
    double cV = (usable.Min.V + usable.Max.V) / 2.0;

    // Vertical stack, RCP above FP, centered as a group.
    double vTop = cV + gapFt / 2.0 + hT / 2.0;
    double vBot = cV - gapFt / 2.0 - hB / 2.0;

    // Horizontal model alignment.
    double s  = rcpTop.Scale;                       // == fpBottom.Scale
    double dU = (CropCenterWorldX(rcpTop) - CropCenterWorldX(fpBottom)) / s;
    double uTop = cU + dU / 2.0;
    double uBot = cU - dU / 2.0;                     // both == cU when crops match

    vpTop.SetBoxCenter(new XYZ(uTop, vTop, 0));
    vpBot.SetBoxCenter(new XYZ(uBot, vBot, 0));

    // Optional: if hT + gap + hB exceeds usable height, keep scale and add a
    // warning to the run report (do NOT rescale).
}

static double CropCenterWorldX(View v)
{
    BoundingBoxXYZ cb = v.CropBox;
    XYZ midView = (cb.Min + cb.Max) * 0.5;
    return cb.Transform.OfPoint(midView).X;         // world/model X of the crop centre
}
```

**Usable area:** prefer the placed title block's own sheet-space extents — collect the `FamilyInstance` of category `OST_TitleBlocks` whose `OwnerViewId == sheet.Id`, take its `get_BoundingBox(sheet)`, and inset a margin. Fall back to `ViewSheet.Outline` (`BoundingBoxUV`) if the title block box is unavailable.

**Assumptions for the alignment math:** views are unrotated on the sheet, share orientation (project north), and share scale. These hold for the standard 1:50 templates; if violated, the fallback is to center both viewports (grids may not overlay perfectly) and add a note to the report.

### 9.4 Uniqueness helpers
- View names must be unique per document; setting a duplicate throws. `UniqueName` checks existing view names and, on clash, appends `_2`, `_3`, … (or ` (2)`), returning the first free variant.
- Sheet numbers must be unique. `UniqueNumber("200")` returns `200` if free, else `200-2`, `200-3`, … Because numbering always restarts at 200, a re-run on the same model will legitimately produce suffixed numbers — this is expected, not an error.

---

## 10. Transactions & error handling

- One outer `TransactionGroup` named e.g. `"EasyBIM: Level Sheets"`.
- One inner `Transaction` per level, so a single failure rolls back just that level and the rest proceed. `Assimilate()` the group on completion so the whole run is a single Undo step.
- Preflight/parameter setup and title-block activation happen in their own transaction(s) before the loop.
- Wrap per-level work in try/catch on `Autodesk.Revit.Exceptions.*`; collect failures with reasons; never let one bad level abort the batch.

---

## 11. Run report

At the end, show a concise summary (modeless dialog or `TaskDialog`) listing, per level: created sheet number + name, both view names, and any warning (e.g. "did not fit at 1:50", "sheet number suffixed", "view already on a sheet — duplicated/skipped"). Include totals: created N, skipped M.

---

## 12. Version-sensitive API

Flagged spots that differ across supported Revit versions — isolate behind conditional compilation or a per-version shim:

- **Parameter spec:** `SpecTypeId.String.Text` (2022+) vs `ParameterType.Text` (pre-2022) in `ExternalDefinitionCreationOptions`.
- **Parameter group:** `GroupTypeId.IdentityData` (2022+) vs `BuiltInParameterGroup.PG_IDENTITY_DATA` (older) for `ParameterBindings.Insert`.
- **`ElementId` construction:** `new ElementId(long)` (2024+) vs `new ElementId(int)`; and `ElementId.Value` (2024+) vs `.IntegerValue`.
- Verify `Viewport.GetBoxOutline` / `SetBoxCenter` signatures are present in every target version (they are stable across 2020+; confirm).

All other calls used here (`ViewPlan.Create`, `ViewSheet.Create`, `Viewport.Create`, `FilteredElementCollector`, `View.ViewTemplateId`) are stable across the supported range.

---

## 13. Suggested structure

- `App.cs` — `IExternalApplication`; builds the ribbon panel + button.
- `CreateLevelSheetsCommand.cs` — `IExternalCommand`; opens dialog, orchestrates the run.
- `Ui/LevelSheetsDialog.xaml(.cs)` — the dialog and its view model.
- `Services/LevelCodeService.cs` — auto-suggestion + read/write `EB_Level_Code`.
- `Services/ParameterService.cs` — ensure the shared/project parameter (§8).
- `Services/ViewFactory.cs` — create + template + name FP/RCP views.
- `Services/SheetFactory.cs` — create sheet, unique number/name.
- `Services/ViewportLayout.cs` — placement math (§9.3).
- `Model/RunReport.cs` — accumulate results/warnings.

---

## 14. Acceptance criteria

1. Clicking the button opens the dialog listing every level with elevation and a pre-filled/suggested code.
2. For each checked level, running produces: an FP view named `CD_Floor Plan_{code}` with template `EB_MEP_FP_1-50`; an RCP view named `CD_Ceiling Plan_{code}` with template `EB_MEP_CP_1-50`; one sheet numbered from 200 upward with name `{code}-MEP`.
3. On each sheet the RCP is above the FP; a vertical grid line in the RCP is directly above the same grid line in the FP (within tolerance).
4. The confirmed code is stored on the level in `EB_Level_Code`; re-opening the dialog pre-fills it.
5. Editing a suggested code to a custom value (including for a Hebrew-named level) results in views/sheets using the edited code — the level's Revit name never affects the output.
6. Re-running on the same model does not throw; clashing names/numbers are suffixed and reported.
7. A view too large to fit at 1:50 is still placed at 1:50, with a warning in the report (no rescale).
8. The entire run is a single Undo step and leaves the model clean when undone.
9. The add-in builds and runs on each targeted Revit version.

---

## 15. Test checklist

- Model with basements + ground + several floors + roof; check auto-suggested codes.
- A level named in Hebrew and a level with a full-word English name — confirm code override drives the output.
- Re-run to force name/number collisions (suffix behavior).
- Very large plan vs a small title block (fit warning, scale preserved).
- Missing template (`EB_MEP_FP_1-50` absent) → clean preflight error.
- No title block loaded / multiple title blocks.
- Many levels selected at once (performance; single Undo).

---

*End of Stage A spec. Build only what is described above; stop for testing before any further stages.*
