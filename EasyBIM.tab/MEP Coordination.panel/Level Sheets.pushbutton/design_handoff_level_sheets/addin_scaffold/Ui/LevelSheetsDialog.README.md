# LevelSheetsDialog (WPF) — to build

This file is a placeholder — build `LevelSheetsDialog.xaml` + `.xaml.cs` (+ a
`LevelSheetsViewModel.cs`) here, matching the UI spec in the handoff `README.md`
("The dialog: structure") and spec §6.

## Bind to
- `Services.LevelCodeService.SuggestCodes` / `ReadCode` for the grid's Code column
  defaults (stored value first, else the suggestion).
- A `Level` list from `new FilteredElementCollector(doc).OfClass(typeof(Level))`,
  sorted by `Elevation` ascending.
- Title block options: `FilteredElementCollector(doc).OfCategory(OST_TitleBlocks)
  .OfClass(typeof(FamilySymbol))`.
- Floor plan / ceiling plan `ViewFamilyType` options: `FilteredElementCollector(doc)
  .OfClass(typeof(ViewFamilyType))` filtered by `.ViewFamily == ViewFamily.FloorPlan`
  / `.CeilingPlan`.

## Validation (spec §6, reproduce exactly)
Block "Create" unless: ≥1 level checked; every checked level has a non-empty code;
codes unique among checked rows; a title block is chosen; both view types are
chosen; both templates exist in the model.

## Reference
See `reference/ribbon-level-sheets.jsx` for the exact layout, copy, and inline
validation-highlighting behavior to replicate in XAML.
