# -*- coding: utf-8 -*-
"""Level Sheets — EasyBIM MEP Coordination (Stage A).

For every selected level: creates a floor plan + a ceiling (RCP) plan, applies
the EB_MEP_FP_1-50 / EB_MEP_CP_1-50 view templates, renames both to the EasyBIM
standard, and places them together on one new sheet (RCP above FP, both
horizontally aligned to the model). Sheet numbers run sequentially from 200,
bottom level first. See the PRD (reference/ribbon-level-sheets.jsx +
../README.md) for full behavior + edge cases.

IMPORTANT: a level's Revit Name is NEVER parsed for its code (levels are often
inherited from an architect's link via copy/monitor and may be non-English).
The code comes from a stored EB_Level_Code parameter, an elevation-order
suggestion, or a user edit — in that priority order.

NOTE: This is a SCAFFOLD generated from the design handoff. The UI here is a
minimal stand-in. Build the real pyRevit forms / WPF dialog (level grid with
inline-editable codes + live validation) matching
reference/ribbon-level-sheets.jsx, then wire it to the helpers below.

ENGINE: Match whichever engine the existing "MEP Solution Section" and "Head
Height Check" buttons use (IronPython 2.7 or CPython3). Inspect those bundles
before writing code — do not introduce a third engine into the extension.
"""

__title__ = "Level\nSheets"
__author__ = "EasyBIM"
__doc__ = "Batch-create floor plans, ceiling plans and sheets from levels."

from pyrevit import revit, DB, forms, script

# --- Project constants -------------------------------------------------------
CODE_PARAM   = "EB_Level_Code"
FP_TEMPLATE  = "EB_MEP_FP_1-50"
CP_TEMPLATE  = "EB_MEP_CP_1-50"
SHEET_START  = 200

logger = script.get_logger()
doc = revit.doc


# --- Data gathering ----------------------------------------------------------
def get_levels():
    """All Levels in the host model, sorted by elevation (ascending)."""
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    return sorted(levels, key=lambda l: l.Elevation)


def get_level_code(level):
    """Read EB_Level_Code if already stored on this Level, else ''."""
    p = level.LookupParameter(CODE_PARAM)
    return p.AsString() if (p and p.HasValue) else ""


def suggest_codes(levels):
    """Elevation-order heuristic: ground=GF00, below=B01.., above=F01.., top=RT.

    Ground = the level with elevation closest to 0. A stored or user-edited
    code always overrides this suggestion — never used to overwrite a stored
    value.
    """
    if not levels:
        return {}
    ground_i = min(range(len(levels)), key=lambda i: abs(levels[i].Elevation))
    out = {levels[ground_i].Id: "GF00"}
    n = 1
    for i in range(ground_i - 1, -1, -1):
        out[levels[i].Id] = "B{:02d}".format(n); n += 1
    n = 1
    for i in range(ground_i + 1, len(levels)):
        out[levels[i].Id] = "F{:02d}".format(n); n += 1
    if ground_i < len(levels) - 1:
        out[levels[-1].Id] = "RT"
    return out


def find_view_template(name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if v.IsTemplate and v.Name == name:  # exact name match only
            return v
    return None


def get_title_block_types():
    return list(DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                .WhereElementIsElementType())


def get_view_family_types(family):
    return [t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
            if t.ViewFamily == family]


def unique_name(base, exists_fn):
    """Suffix _2, _3, ... until exists_fn(name) is False (re-run safety)."""
    name = base
    i = 2
    while exists_fn(name):
        name = "{}_{}".format(base, i)
        i += 1
    return name


def unique_sheet_number(base, exists_fn):
    number = base
    i = 2
    while exists_fn(number):
        number = "{}-{}".format(base, i)
        i += 1
    return number


# --- Core operation -----------------------------------------------------------
def run(levels, codes, title_block_type, fp_vft, cp_vft):
    """Create FP + RCP + sheet for each (level, code) pair. One undo step.

    levels: list of DB.Level ; codes: dict {level.Id: code string}
    Returns a list of per-level result dicts for the report panel.
    """
    fp_template = find_view_template(FP_TEMPLATE)
    cp_template = find_view_template(CP_TEMPLATE)
    if not fp_template or not cp_template:
        raise ValueError("Missing view template(s): {} / {}".format(FP_TEMPLATE, CP_TEMPLATE))

    results = []
    tg = DB.TransactionGroup(doc, "EasyBIM: Level Sheets")
    tg.Start()
    try:
        for i, level in enumerate(levels):
            code = codes[level.Id]
            # fp = DB.ViewPlan.Create(doc, fp_vft.Id, level.Id)
            # cp = DB.ViewPlan.Create(doc, cp_vft.Id, level.Id)
            # fp.ViewTemplateId = fp_template.Id ; cp.ViewTemplateId = cp_template.Id
            # fp.Name = unique_name("CD_Floor Plan_" + code, view_name_exists)
            # cp.Name = unique_name("CD_Ceiling Plan_" + code, view_name_exists)
            # sheet = DB.ViewSheet.Create(doc, title_block_type.Id)
            # sheet.SheetNumber = unique_sheet_number(str(SHEET_START + i), sheet_number_exists)
            # sheet.Name = code + "-MEP"
            # place cp Viewport ABOVE fp Viewport, both horizontally aligned to
            # the model (same grid X on every sheet) -> DB.Viewport.Create(...)
            # if plan extent doesn't fit the title block at template scale:
            #   do NOT rescale -> results[-1]["warn"].append("did not fit ...")
            # level.LookupParameter(CODE_PARAM).Set(code)  # persist for next run
            raise NotImplementedError(
                "Implement view creation, template application, renaming, "
                "sheet creation, and aligned viewport placement.")
        # tg.Assimilate()
    except Exception as ex:
        tg.RollBack()
        logger.error("Level Sheets failed: %s", ex)
        raise
    return results


# --- Entry point (minimal stand-in UI) ---------------------------------------
def main():
    levels = get_levels()
    if not levels:
        forms.alert("No levels found in this model.", exitscript=True)

    chosen = forms.SelectFromList.show(
        levels, name_attr="Name", multiselect=True,
        title="Level Sheets — Select Levels", button_name="Next")
    if not chosen:
        return

    suggested = suggest_codes(levels)
    codes = {}
    for lvl in chosen:
        stored = get_level_code(lvl)
        default = stored or suggested.get(lvl.Id, "")
        code = forms.ask_for_string(
            default=default, prompt="Code for '{}'".format(lvl.Name),
            title="EB_Level_Code")
        if not code:
            forms.alert("Every selected level needs a code.", exitscript=True)
        codes[lvl.Id] = code

    if len(set(codes.values())) != len(codes):
        forms.alert("Codes must be unique among selected levels.", exitscript=True)

    tb_types = get_title_block_types()
    title_block = forms.SelectFromList.show(
        tb_types, name_attr="Name", title="Title Block", button_name="Next")
    if not title_block:
        return

    fp_vft = next(iter(get_view_family_types(DB.ViewFamily.FloorPlan)), None)
    cp_vft = next(iter(get_view_family_types(DB.ViewFamily.CeilingPlan)), None)
    if not fp_vft or not cp_vft:
        forms.alert("Could not resolve floor/ceiling plan view family types.",
                    exitscript=True)

    results = run(chosen, codes, title_block, fp_vft, cp_vft)
    forms.alert("Done. {} sheet(s) created.".format(len(results)))


if __name__ == "__main__":
    main()
