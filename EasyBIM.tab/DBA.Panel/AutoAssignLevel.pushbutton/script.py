# -*- coding: utf-8 -*-
"""
Auto Assign Level

Assigns model elements (selection, active view, or the whole model) to the
correct floor/level without moving their physical 3D position.

For every element, the tool finds the highest *active* level whose elevation
is at or below the element's real Z coordinate (with a small +0.01 tolerance
so an element exactly on a level's elevation still counts). This means a
ceiling-mounted lighting fixture at Z = 2.80 m gets assigned to the floor
level at Z = 0.00 m with a 2.80 m offset, instead of being pulled up to
whichever level happens to sit above it. The element's level/offset
parameters are updated; its physical position is untouched.

Auxiliary levels (parapet, top-of-*, landing, grid, reference, soffit, ...)
are excluded from the candidate baseline levels by default, since those
levels typically don't represent real occupiable floors. The set of
excluded levels is remembered per-user via script.get_config() so repeat
runs don't require re-selecting them.

Shift-click bypasses every dialog and runs immediately on the current
selection against the last-remembered (or auto-detected) non-auxiliary
levels.

All changes are wrapped in a single DB.TransactionGroup + Assimilate() so
the whole operation collapses into one Undo (Ctrl+Z) step. Elements owned
by other users (worksharing) and hosted elements (doors/windows on a wall,
etc.) are skipped, since their level is governed by their host/owner.
Updated elements are highlighted bright green in the active view and
reported in the pyRevit output console with clickable Element Id links.
"""

import re
from pyrevit import revit, DB, forms, script

doc = revit.doc
output = script.get_output()
cfg = script.get_config("AutoAssignLevelTool")

tg = DB.TransactionGroup(doc, u"שיוך אוטומטי למפלסים")
tg.Start()

try:
    levels_collector = DB.FilteredElementCollector(doc)\
                        .OfClass(DB.Level)\
                        .WhereElementIsNotElementType()\
                        .ToElements()

    all_levels = sorted(list(levels_collector), key=lambda l: l.Elevation)
    if not all_levels:
        forms.alert(u"לא נמצאו מפלסים בפרויקט.", exitscript=True)

    level_name_map = {l.Name: l for l in all_levels}
    is_shift_pressed = __shift_click__

    AUXILIARY_KEYWORDS = [r"parapet", r"top", r"landing", r"grid", r"ref", r"soffit"]

    def is_auxiliary_level(name):
        return any(re.search(pat, name, re.IGNORECASE) for pat in AUXILIARY_KEYWORDS)

    process_groups = False

    if is_shift_pressed:
        saved_ignored = cfg.get_option("ignored_levels", None)
        if saved_ignored is not None:
            active_levels = [l for l in all_levels if l.Name not in saved_ignored]
        else:
            active_levels = [l for l in all_levels if not is_auxiliary_level(l.Name)]

        selection = revit.get_selection()
        if not selection:
            forms.alert(u"מצב מהיר (Shift): אנא בחר אלמנטים לפני הלחיצה.", exitscript=True)
        raw_elements = list(selection)
    else:
        saved_ignored = cfg.get_option("ignored_levels", None)
        if saved_ignored is not None:
            default_selected = [name for name in level_name_map.keys() if name not in saved_ignored]
        else:
            default_selected = [name for name in level_name_map.keys() if not is_auxiliary_level(name)]

        selected_level_names = forms.SelectFromList.show(
            sorted(level_name_map.keys()),
            title=u"1. בחר מפלסים פעילים לשיוך",
            multiselect=True,
            default_option=default_selected,
            button_name=u"אישור מפלסים"
        )

        if not selected_level_names:
            script.exit()

        ignored_levels = [name for name in level_name_map.keys() if name not in selected_level_names]
        cfg.ignored_levels = ignored_levels
        script.save_config()

        active_levels = sorted([level_name_map[name] for name in selected_level_names], key=lambda l: l.Elevation)

        SCOPE_SELECTION = u"1. אלמנטים שנבחרו כעת (Selection)"
        SCOPE_VIEW = u"2. כל האלמנטים במבט הנוכחי (Active View)"
        SCOPE_MODEL = u"3. כל האלמנטים בכל המודל (Entire Model)"

        selected_scope = forms.SelectFromList.show(
            [SCOPE_SELECTION, SCOPE_VIEW, SCOPE_MODEL],
            title=u"2. בחר את היקף העבודה (Scope)",
            button_name=u"המשך לבחירת קטגוריות",
            multiselect=False
        )

        if not selected_scope:
            script.exit()

        if selected_scope == SCOPE_SELECTION:
            selection = revit.get_selection()
            if not selection:
                forms.alert(u"לא נבחרו אלמנטים במודל.", exitscript=True)
            raw_elements = list(selection)
        elif selected_scope == SCOPE_VIEW:
            raw_elements = DB.FilteredElementCollector(doc, doc.ActiveView.Id).WhereElementIsNotElementType().ToElements()
        else:
            raw_elements = DB.FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()

    if not active_levels:
        forms.alert(u"לא נבחרו מפלסים פעילים לשיוך.", exitscript=True)

    valid_elements = [e for e in raw_elements if e.Category and e.Category.CategoryType == DB.CategoryType.Model]

    if not valid_elements:
        forms.alert(u"לא נמצאו אלמנטים מתאימים לעדכון.", exitscript=True)

    if not is_shift_pressed:
        categories_dict = {}
        for elem in valid_elements:
            cat_name = elem.Category.Name
            categories_dict.setdefault(cat_name, []).append(elem)

        selected_categories = forms.SelectFromList.show(
            sorted(categories_dict.keys()),
            title=u"3. בחר קטגוריות לעדכון",
            multiselect=True,
            button_name=u"התחל שיוך"
        )

        if not selected_categories:
            script.exit()

        elements_to_process = []
        for cat in selected_categories:
            elements_to_process.extend(categories_dict[cat])

        grouped_elements = [e for e in elements_to_process if e.GroupId != DB.ElementId.InvalidElementId]
        if grouped_elements:
            process_groups = forms.alert(
                u"נמצאו {} אלמנטים בתוך Groups.\nהאם לעדכן אותם בכל זאת?".format(len(grouped_elements)),
                title=u"אזהרת Model Groups", yes=True, no=True
            )
    else:
        elements_to_process = valid_elements
        process_groups = True  # quick run: act on whatever is in the selection, including grouped elements

    def get_element_z(elem):
        if hasattr(elem, "Location") and elem.Location:
            if isinstance(elem.Location, DB.LocationPoint):
                return elem.Location.Point.Z
            elif isinstance(elem.Location, DB.LocationCurve):
                p0 = elem.Location.Curve.GetEndPoint(0).Z
                p1 = elem.Location.Curve.GetEndPoint(1).Z
                return min(p0, p1)
        bbox = elem.get_BoundingBox(None)
        return bbox.Min.Z if bbox else None

    def find_associated_level(z_coord):
        selected_level = active_levels[0]
        for lvl in active_levels:
            if lvl.Elevation <= z_coord + 0.01:
                selected_level = lvl
            else:
                break
        return selected_level

    updated_stats = {}
    updated_ids = []
    skipped_workshare = 0
    skipped_hosted = 0
    skipped_groups = 0

    t = DB.Transaction(doc, u"עדכון מפלסים ו-Offsets")
    t.Start()

    for elem in elements_to_process:
        if elem.GroupId != DB.ElementId.InvalidElementId and not process_groups:
            skipped_groups += 1
            continue

        if doc.IsWorkshared and DB.WorksharingUtils.GetCheckoutStatus(doc, elem.Id) == DB.CheckoutStatus.OwnedByOtherUser:
            skipped_workshare += 1
            continue

        if hasattr(elem, "Host") and elem.Host is not None:
            skipped_hosted += 1
            continue

        z_pos = get_element_z(elem)
        if z_pos is None:
            continue

        target_level = find_associated_level(z_pos)
        cat_name = elem.Category.Name

        level_param = elem.get_Parameter(DB.BuiltInParameter.WALL_BASE_CONSTRAINT) \
                   or elem.get_Parameter(DB.BuiltInParameter.FAMILY_BASE_LEVEL_PARAM) \
                   or elem.get_Parameter(DB.BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM) \
                   or elem.get_Parameter(DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM) \
                   or elem.get_Parameter(DB.BuiltInParameter.FAMILY_LEVEL_PARAM) \
                   or elem.get_Parameter(DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)

        if level_param and not level_param.IsReadOnly:
            if level_param.AsElementId() != target_level.Id:
                level_param.Set(target_level.Id)

                offset_param = elem.get_Parameter(DB.BuiltInParameter.WALL_BASE_OFFSET) \
                            or elem.get_Parameter(DB.BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM) \
                            or elem.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM) \
                            or elem.get_Parameter(DB.BuiltInParameter.LIGHTING_FIXTURE_ELEVATION)

                if offset_param and not offset_param.IsReadOnly:
                    new_offset = z_pos - target_level.Elevation
                    offset_param.Set(new_offset)

                updated_stats[cat_name] = updated_stats.get(cat_name, 0) + 1
                updated_ids.append(elem.Id)

    t.Commit()

    if updated_ids:
        t_highlight = DB.Transaction(doc, u"הבלטת אלמנטים שעודכנו")
        t_highlight.Start()

        override_settings = DB.OverrideGraphicSettings()
        green_color = DB.Color(0, 255, 100)

        fill_patterns = DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement).ToElements()
        solid_pattern = next((fp for fp in fill_patterns if fp.GetFillPattern().IsSolidFill), None)

        if solid_pattern:
            override_settings.SetSurfaceForegroundPatternId(solid_pattern.Id)
            override_settings.SetSurfaceForegroundPatternColor(green_color)
            override_settings.SetProjectionLineColor(green_color)

        active_view = doc.ActiveView
        for eid in updated_ids:
            active_view.SetElementOverrides(eid, override_settings)

        t_highlight.Commit()

    tg.Assimilate()

    output.print_md("# 🚀 סיכום שיוך מפלסים")

    total_updated = sum(updated_stats.values())
    if total_updated > 0:
        output.print_md("### ✔️ אלמנטים שעודכנו בהצלחה:")
        for cat, count in updated_stats.items():
            print(u"• {}: {} אלמנטים".format(cat, count))

        output.print_md("\n**סה\"כ עודכנו:** `{}` אלמנטים.".format(total_updated))
        output.print_md("🎨 **האלמנטים שעודכנו הובלטו בצבע ירוק במבט הנוכחי.**")

        print("\nElement IDs שהשתנו (לחץ לבחירה במודל):")
        for eid in updated_ids[:15]:
            print(output.linkify(eid))
        if len(updated_ids) > 15:
            print(u"... ועוד {} אלמנטים.".format(len(updated_ids) - 15))
    else:
        output.print_md("✨ **כל האלמנטים שנבדקו כבר משויכים למפלסים הרלוונטיים.**")

    if skipped_groups > 0:
        output.print_md("ℹ️ **דולגו {} אלמנטים בתוך Model Groups (לא אושרו לעדכון).**".format(skipped_groups))

    if skipped_workshare > 0:
        output.print_md("⚠️ **דולגו {} אלמנטים הנעולים על ידי משתמשים אחרים.**".format(skipped_workshare))

    if skipped_hosted > 0:
        output.print_md("ℹ️ **דולגו {} אלמנטים מבוססי-Host (כמו דלתות/חלונות) שנשלטים ע\"י הקיר המארח.**".format(skipped_hosted))

except Exception as ex:
    tg.RollBack()
    forms.alert(u"התרחשה שגיאה, כל השינויים בוטלו:\n{}".format(ex), title=u"שגיאה בהרצה")
