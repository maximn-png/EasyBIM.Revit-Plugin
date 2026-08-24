# -*- coding: utf-8 -*-
"""Level Sheets — EasyBIM MEP Coordination (Stage A).

For every selected level: creates a floor plan + a ceiling (RCP) plan, applies
the EB_MEP_FP_1-50 / EB_MEP_CP_1-50 view templates, renames both views to the
EasyBIM standard, and places them on one new sheet with the RCP
overlaid directly on the floor plan — same position, model-aligned so every
grid line coincides, RCP drawn in front. Sheet numbers run
sequentially from 200, bottom level first. Wrapped in a TransactionGroup
(fully undoable as one step).

A level's Revit Name is NEVER parsed for its code: levels are commonly
inherited from an architect's link via copy/monitor and are unreliable or
non-English. The code comes from the stored EB_Level_Code parameter, an
elevation-order suggestion, or a user edit — in that priority order — and the
confirmed value is written back to EB_Level_Code for the next run.
"""

__title__ = "Level\nSheets"
__author__ = "EasyBIM"
__doc__ = "Batch-create floor plans, ceiling plans and sheets from levels."

import clr
import os
import traceback

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System')

from Autodesk.Revit import DB
import System
import System.Windows
import System.Windows.Controls as WC
import System.Windows.Media as WM
import System.Windows.Input as WI

from System.Windows.Markup import XamlReader
from System.Windows.Threading import (Dispatcher, DispatcherFrame,
                                      DispatcherPriority,
                                      DispatcherOperationCallback)
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import revit, script, forms

doc = revit.doc
logger = script.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CODE_PARAM   = u"EB_Level_Code"
# Template pairs, tried in this order; every pair found in the model becomes a
# choice in the dialog, so a plan too big for A0 at 1:50 can go out at 1:100.
# Still matched by EXACT name — never fuzzy — and a pair is only offered when
# BOTH its floor-plan and ceiling-plan template resolve.
TEMPLATE_SETS = ((u"EB_MEP_FP_1-50",  u"EB_MEP_CP_1-50",  u"1:50"),
                 (u"EB_MEP_FP_1-100", u"EB_MEP_CP_1-100", u"1:100"),
                 (u"EB_MEP_FP_1-200", u"EB_MEP_CP_1-200", u"1:200"))
SHEET_START  = 200
FP_PREFIX    = u"CD_Floor Plan_"
CP_PREFIX    = u"CD_Ceiling Plan_"
SHEET_SUFFIX = u"-MEP"

# Viewport types, by type name within the Viewport system family. The floor plan
# carries the title with the scale; the ceiling plan sits on top of it, so a
# second title would just print over the first.
FP_VIEWPORT_TYPE = u"Scale"
CP_VIEWPORT_TYPE = u"None"

SHARED_PARAM_FILE = os.path.join(os.path.dirname(__file__),
                                 u"LevelCode.shared.txt")
SHARED_PARAM_GROUP_NAME = u"EasyBIM_Coordination"

MM_PER_FT = 304.8
FT_TO_M   = 0.3048

# A0 in feet — the sheet these overlaid plans are drawn for, and the size the
# title block picker defaults to.
A0_W_FT = 1189.0 / MM_PER_FT
A0_H_FT = 841.0 / MM_PER_FT

# Sheet-space geometry, all in feet (Revit's internal sheet unit).
MARGIN_FT = 15.0 / MM_PER_FT   # inset from the title block edge
# Every viewport reserves a little height above the drawing for its own title
# label. Same allowance the Solution Section button uses.
LABEL_ALLOWANCE_FT = 0.08

# Crop pad PER SIDE, in paper millimetres, added around the scope box.
#
# The point is not margin for its own sake: a viewport's box is the hull of the
# view's visible content, so anything drawn past the crop edge inflates it — by
# different amounts in the two views, which displaces the drawings when the boxes
# are centred on one point. Pad the crop until nothing overhangs and the hull
# becomes the crop itself: same rectangle on both views, crop exactly centred.
#
# Must exceed the largest overhang; measured at ~31 mm per side. The run grows
# this automatically if it turns out to be too small, so it is a starting point
# rather than a guess that has to be right.
CROP_PAD_PAPER_MM = 40.0

# How many times a run may grow the pad before giving up and reporting. Growing
# the crop can pull further annotations into view, so this converges rather than
# being computable in one shot.
CROP_PAD_RETRIES = 2

# Prints placement numbers to the pyRevit output window during each run. Off for
# normal use; switch on to debug placement — see _diag() for what it reports.
DIAGNOSTICS = False

# BuiltInParameterGroup was replaced by GroupTypeId in newer Revit API versions.
try:
    from Autodesk.Revit.DB import BuiltInParameterGroup as _BPG
    _PARAM_GROUP = _BPG.PG_IDENTITY_DATA
except (ImportError, AttributeError):
    from Autodesk.Revit.DB import GroupTypeId as _GTI
    _PARAM_GROUP = next(
        (getattr(_GTI, n) for n in ('IdentityData', 'Identity', 'General', 'Data')
         if getattr(_GTI, n, None) is not None),
        None
    )


def _eid_int(element_id):
    """ElementId as a plain int. `.Value` is 2024+, `.IntegerValue` is older."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


def _txt(value):
    """Unicode text for display, safe on both IronPython 2 and CPython 3."""
    try:
        return unicode(value)
    except NameError:
        return str(value)


# Unicode ranges for the strong RTL scripts that show up in these models:
# Hebrew (0590–05FF), Arabic (0600–06FF, 0750–077F), Arabic presentation
# forms (FB1D–FDFF, FE70–FEFF).
_RTL_RANGES = ((0x0590, 0x05FF), (0x0600, 0x06FF), (0x0750, 0x077F),
               (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))


def _is_rtl(text):
    """True when the first strong directional character is RTL.

    Mirrors what CSS `dir="auto"` does: direction follows the text's first
    strong character, so a Hebrew level name renders RTL and an English one
    LTR with no per-model configuration.
    """
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        for low, high in _RTL_RANGES:
            if low <= code <= high:
                return True
        # A-Z / a-z are the strong LTR characters; first one wins.
        if (0x41 <= code <= 0x5A) or (0x61 <= code <= 0x7A):
            return False
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 0. SHARED PARAMETER (bind-on-launch)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_level_code_param():
    """Ensure EB_Level_Code (text, instance) is bound to Levels.

    Adds + binds it from the bundled shared-parameter file if missing, so the
    user is never blocked. If some other tool already bound a parameter of the
    same name, its categories are MERGED rather than overwritten. Must run
    inside an open Transaction.
    """
    existing_defn = None
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == CODE_PARAM:
            existing_defn = it.Key
            break

    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Levels)

    if existing_defn is not None:
        binding = doc.ParameterBindings.get_Item(existing_defn)
        if binding is not None and cat is not None and not binding.Categories.Contains(cat):
            cats = binding.Categories
            cats.Insert(cat)
            doc.ParameterBindings.ReInsert(existing_defn, DB.InstanceBinding(cats), _PARAM_GROUP)
        return

    old_spf = doc.Application.SharedParametersFilename
    try:
        doc.Application.SharedParametersFilename = SHARED_PARAM_FILE
        spf = doc.Application.OpenSharedParameterFile()
        if not spf:
            raise Exception(u"Could not open shared parameter file: {}".format(SHARED_PARAM_FILE))
        grp = spf.Groups.get_Item(SHARED_PARAM_GROUP_NAME)
        if grp is None:
            raise Exception(u"Group '{}' not found in shared parameter file.".format(
                SHARED_PARAM_GROUP_NAME))
        defn = grp.Definitions.get_Item(CODE_PARAM)
        if defn is None:
            raise Exception(u"Definition '{}' not found in shared parameter file.".format(
                CODE_PARAM))
        cat_set = DB.CategorySet()
        cat_set.Insert(cat)
        doc.ParameterBindings.Insert(defn, DB.InstanceBinding(cat_set), _PARAM_GROUP)
    finally:
        doc.Application.SharedParametersFilename = old_spf


# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def get_levels():
    """All Levels in the host model, sorted by elevation (ascending)."""
    levels = list(DB.FilteredElementCollector(doc)
                    .OfClass(DB.Level)
                    .WhereElementIsNotElementType())
    return sorted(levels, key=lambda l: l.Elevation)


def get_stored_code(level):
    """Read EB_Level_Code off a Level, or u'' when unset."""
    try:
        p = level.LookupParameter(CODE_PARAM)
    except Exception:
        return u""
    if p is None or not p.HasValue:
        return u""
    val = p.AsString()
    return val.strip() if val else u""


def suggest_codes(levels):
    """Elevation-order heuristic: ground=GF00, below=B01.., above=F01.., top=RT.

    Ground = the level whose elevation is closest to 0. Deliberately
    non-authoritative — models vary (penthouses, split levels, mezzanines) —
    so a stored or user-edited code always wins over this.
    """
    if not levels:
        return {}
    ground_i = min(range(len(levels)), key=lambda i: abs(levels[i].Elevation))
    out = {_eid_int(levels[ground_i].Id): u"GF00"}
    n = 1
    for i in range(ground_i - 1, -1, -1):
        out[_eid_int(levels[i].Id)] = u"B{:02d}".format(n)
        n += 1
    n = 1
    for i in range(ground_i + 1, len(levels)):
        out[_eid_int(levels[i].Id)] = u"F{:02d}".format(n)
        n += 1
    if ground_i < len(levels) - 1:
        out[_eid_int(levels[-1].Id)] = u"RT"
    return out


def find_view_template(name):
    """Resolve a view template by EXACT name. Never fuzzy-matched."""
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            if v.IsTemplate and _elem_name(v) == name:
                return v
        except Exception:
            continue
    return None


def resolve_template_sets():
    """Every TEMPLATE_SETS pair whose BOTH templates exist, in declared order."""
    found = []
    for fp_name, cp_name, label in TEMPLATE_SETS:
        fp_tpl = find_view_template(fp_name)
        cp_tpl = find_view_template(cp_name)
        if fp_tpl is not None and cp_tpl is not None:
            found.append({u"label": label, u"fp_name": fp_name,
                          u"cp_name": cp_name, u"fp": fp_tpl, u"cp": cp_tpl})
    return found


def title_block_size(ts):
    """(width_ft, height_ft) of a title block type's paper, or None."""
    try:
        w = ts.get_Parameter(DB.BuiltInParameter.SHEET_WIDTH).AsDouble()
        h = ts.get_Parameter(DB.BuiltInParameter.SHEET_HEIGHT).AsDouble()
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return None


def _extent_preview_bounds(extents):
    """The model rectangle an extent choice will impose, before anything runs."""
    kind = extents[u"kind"]
    if kind == u"scopebox":
        el = doc.GetElement(extents[u"id"])
        try:
            bb = el.get_BoundingBox(None) if el is not None else None
        except Exception:
            bb = None
        if bb is not None:
            return (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)
        return None
    # "auto" cannot be previewed: which box gets used is only known per level,
    # once the level code is available to match against.
    return None


def estimate_fit(extents, scale, title_block):
    """Will the overlaid plan fit the sheet? (need_w, need_h, have_w, have_h) mm.

    Answered from the extent, the scale and the title block's paper size — all
    known before the run — so an impossible combination can be flagged in the
    dialog instead of discovered as plans hanging off the sheet edge. Width is
    approximate: the title strip's own width is not knowable from the type.
    """
    bounds = _extent_preview_bounds(extents)
    size = title_block_size(title_block)
    if bounds is None or size is None:
        return None
    plan_w = model_ft_to_paper_ft(bounds[2] - bounds[0], scale)
    plan_h = model_ft_to_paper_ft(bounds[3] - bounds[1], scale) + LABEL_ALLOWANCE_FT
    need_h = plan_h
    return (plan_w * MM_PER_FT, need_h * MM_PER_FT,
            (size[0] - 2 * MARGIN_FT) * MM_PER_FT,
            (size[1] - 2 * MARGIN_FT) * MM_PER_FT)


def get_scope_boxes():
    """Host-model scope boxes, the Revit-native way to fix a plan extent."""
    return sorted(DB.FilteredElementCollector(doc)
                    .OfCategory(DB.BuiltInCategory.OST_VolumeOfInterest)
                    .WhereElementIsNotElementType()
                    .ToElements(),
                  key=lambda b: _elem_name(b).lower())


def _elem_name(element):
    """Read an element's name the way that actually works for ElementTypes.

    `element.Name` is ambiguous on ElementType subclasses under IronPython and
    raises instead of returning the string — which is why the pickers showed
    "?" for every view family type and for the title block's type name. Going
    through Element's own Name property explicitly resolves it; the built-in
    type-name parameters are a second line of defence.
    """
    try:
        return DB.Element.Name.GetValue(element)
    except Exception:
        pass
    for bip in (DB.BuiltInParameter.SYMBOL_NAME_PARAM,
                DB.BuiltInParameter.ALL_MODEL_TYPE_NAME,
                DB.BuiltInParameter.VIEW_NAME):
        try:
            p = element.get_Parameter(bip)
            if p is not None:
                val = p.AsString()
                if val:
                    return val
        except Exception:
            continue
    try:
        return element.Name
    except Exception:
        return u"?"


def title_block_label(ts):
    try:
        fam = ts.FamilyName
    except Exception:
        fam = u""
    typ = _elem_name(ts)
    return u"{} : {}".format(fam, typ) if fam else typ


def get_title_block_types():
    types = list(DB.FilteredElementCollector(doc)
                   .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                   .WhereElementIsElementType())
    return sorted(types, key=lambda t: title_block_label(t).lower())


def _closest_to_a0(title_blocks):
    """Index of the title block nearest A0 in area, or 0 if none can be read.

    A0 is the sheet these plans are drawn for, so it is the sensible default —
    alphabetical order is not, and picking the wrong size silently was how a
    530 x 317 mm title block got used for a 91 m building during testing.
    """
    best_i, best_gap = 0, None
    for i, ts in enumerate(title_blocks):
        size = title_block_size(ts)
        if size is None:
            continue
        gap = abs(size[0] * size[1] - A0_W_FT * A0_H_FT)
        if best_gap is None or gap < best_gap:
            best_i, best_gap = i, gap
    return best_i


def vft_label(t):
    return _elem_name(t)


def get_view_family_types(view_family):
    types = []
    for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        try:
            if t.ViewFamily == view_family:
                types.append(t)
        except Exception:
            continue
    return sorted(types, key=lambda t: vft_label(t).lower())


def default_view_family_type(view_family, element_type_group, candidates):
    """The view family type Revit itself would use, else the first candidate.

    The dialog no longer asks: one floor-plan type and one ceiling-plan type is
    all this tool ever needs, and the project default is a better answer than
    whichever name sorts first.
    """
    try:
        type_id = doc.GetDefaultElementTypeId(element_type_group)
        if type_id is not None and type_id != DB.ElementId.InvalidElementId:
            vft = doc.GetElement(type_id)
            if vft is not None and vft.ViewFamily == view_family:
                return vft
    except Exception:
        pass
    return candidates[0] if candidates else None


def existing_view_names():
    names = set()
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            names.add(v.Name)
        except Exception:
            continue
    return names


def existing_sheet_numbers():
    numbers = set()
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        try:
            numbers.add(s.SheetNumber)
        except Exception:
            continue
    return numbers


# ─────────────────────────────────────────────────────────────────────────────
# 2. UNIQUENESS HELPERS (re-run safety)
# ─────────────────────────────────────────────────────────────────────────────

def unique_view_name(base, taken):
    """First free variant of `base`, suffixing _2, _3, ... Reserves the result."""
    name = base
    i = 2
    while name in taken:
        name = u"{}_{}".format(base, i)
        i += 1
    taken.add(name)
    return name


def unique_sheet_number(base, taken):
    """First free variant of `base`, suffixing -2, -3, ... Reserves the result."""
    number = base
    i = 2
    while number in taken:
        number = u"{}-{}".format(base, i)
        i += 1
    taken.add(number)
    return number


# ─────────────────────────────────────────────────────────────────────────────
# 3. SHEET GEOMETRY
# ─────────────────────────────────────────────────────────────────────────────

def _usable_area(sheet):
    """(u_min, u_max, v_min, v_max) of the drawable area, in sheet feet.

    Prefers the placed title block's own extents; falls back to the sheet
    outline when no title block instance is readable.
    """
    try:
        tb = (DB.FilteredElementCollector(doc, sheet.Id)
                .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                .WhereElementIsNotElementType()
                .FirstElement())
        if tb is not None:
            bb = tb.get_BoundingBox(sheet)
            if bb is not None:
                return (bb.Min.X + MARGIN_FT, bb.Max.X - MARGIN_FT,
                        bb.Min.Y + MARGIN_FT, bb.Max.Y - MARGIN_FT)
    except Exception:
        pass
    try:
        ol = sheet.Outline
        return (ol.Min.U + MARGIN_FT, ol.Max.U - MARGIN_FT,
                ol.Min.V + MARGIN_FT, ol.Max.V - MARGIN_FT)
    except Exception:
        # Last-resort A1-ish landscape box.
        return (MARGIN_FT, 2.5, MARGIN_FT, 1.8)


def _view_scale(view, fallback=50):
    try:
        s = view.Scale
        return float(s) if s and s > 0 else float(fallback)
    except Exception:
        return float(fallback)


def _annotation_offsets(view):
    """(left, right, top, bottom) annotation crop offsets in model ft, or None.

    None means genuinely unreadable — never a zeroed stand-in. Reporting a
    fallback as though it were measured is how the wrong API name went unnoticed
    through several rounds of this.
    """
    try:
        mgr = view.GetCropRegionShapeManager()
        return (mgr.LeftAnnotationCropOffset, mgr.RightAnnotationCropOffset,
                mgr.TopAnnotationCropOffset, mgr.BottomAnnotationCropOffset)
    except Exception:
        return None


def _annotation_crop_active(view):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
        return p.AsInteger() if p is not None else None
    except Exception:
        return None



def _diag(msg):
    """Placement telemetry to the pyRevit output window.

    On while the placement logic is being proven against real models: the
    numbers below (crop extents, measured outlines, requested centres and what
    Revit actually stored) are what distinguish 'not stacked' from 'stacked but
    grids offset'. Set DIAGNOSTICS = False once placement is trusted.
    """
    if DIAGNOSTICS:
        print(msg)


def _crop_centre_world(view):
    """World/model centre of a view's crop rectangle.

    Component-wise rather than (cb.Min + cb.Max) * 0.5 — same value, without
    relying on XYZ operator overloading through IronPython.
    """
    cb = view.CropBox
    mid = DB.XYZ((cb.Min.X + cb.Max.X) * 0.5,
                 (cb.Min.Y + cb.Max.Y) * 0.5,
                 (cb.Min.Z + cb.Max.Z) * 0.5)
    return cb.Transform.OfPoint(mid)


# ── Paper vs model units ─────────────────────────────────────────────────────
# Two unit systems meet in the placement code and they are easy to confuse: a
# crop is measured in MODEL feet, everything on a sheet is PAPER feet, and the
# view scale is what converts between them. Mixing them up does not crash — at
# 1:100 a missing conversion is simply a hundred times too small, so the crop
# quietly stops growing and the misalignment creeps back.
#
# So: every PAPER-to-MODEL conversion goes through one of these, and every
# variable holding a length says which unit it is in (pad_model_ft,
# slack_paper_ft, worst_mm). A bare `* scale` or `/ scale` on a length outside
# these three functions is the smell — that is the conversion that goes wrong
# silently. Plain ft-to-mm within one system (`* MM_PER_FT`) is harmless and
# needs no helper.


def paper_mm_to_model_ft(mm, scale):
    """A distance on the sheet, in mm, as the model distance it represents."""
    return mm / MM_PER_FT * scale


def paper_ft_to_model_ft(paper_ft, scale):
    """A distance on the sheet, in feet, as the model distance it represents."""
    return paper_ft * scale


def model_ft_to_paper_ft(model_ft, scale):
    """A model distance as the distance it occupies on the sheet, in feet."""
    return model_ft / scale


def model_ft_to_paper_mm(model_ft, scale):
    """A model distance as the distance it occupies on the sheet, in mm."""
    return model_ft_to_paper_ft(model_ft, scale) * MM_PER_FT


def _scope_box_bounds(box):
    """(min_x, min_y, max_x, max_y) of a scope box in world XY, or None."""
    try:
        bb = box.get_BoundingBox(None)
    except Exception:
        return None
    if bb is None:
        return None
    return (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)


def _set_padded_crop(view, bounds, pad_model_ft):
    """Crop `view` to a world-XY rectangle plus `pad_model_ft`, active but not drawn.

    Sets the crop directly rather than assigning the scope box, and clears any
    scope-box assignment first — a scope box governs the crop and would override
    this. The scope box still decides WHERE the rectangle is; it just stops being
    the mechanism, because the mechanism has to be able to pad.

    The view keeps its own Transform and Z range; only the XY rectangle is
    replaced, expressed in that view's own coordinates.
    """
    p = view.get_Parameter(DB.BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
    if p is not None and not p.IsReadOnly:
        p.Set(DB.ElementId.InvalidElementId)

    min_x, min_y, max_x, max_y = bounds
    min_x -= pad_model_ft
    min_y -= pad_model_ft
    max_x += pad_model_ft
    max_y += pad_model_ft

    bb = view.CropBox
    inv = bb.Transform.Inverse
    plane_z = bb.Transform.OfPoint(DB.XYZ(0, 0, bb.Min.Z)).Z
    xs, ys = [], []
    for x in (min_x, max_x):
        for y in (min_y, max_y):
            p_view = inv.OfPoint(DB.XYZ(x, y, plane_z))
            xs.append(p_view.X)
            ys.append(p_view.Y)
    bb.Min = DB.XYZ(min(xs), min(ys), bb.Min.Z)
    bb.Max = DB.XYZ(max(xs), max(ys), bb.Max.Z)
    view.CropBox = bb
    view.CropBoxActive = True
    view.CropBoxVisible = False


def _crop_both(fp_view, cp_view, bounds, pad_model_ft):
    """Give both views the identical padded crop, then regenerate."""
    _set_padded_crop(fp_view, bounds, pad_model_ft)
    _set_padded_crop(cp_view, bounds, pad_model_ft)
    doc.Regenerate()


def _measure_slack_paper_ft(vp, view, scale):
    """How much the viewport box exceeds the crop, per axis, in PAPER feet.

    Zero on both axes is the condition that makes the overlay exact: it means
    nothing is drawn outside the crop, so the box IS the crop and the crop is
    centred in it.
    """
    o = vp.GetBoxOutline()
    b = _world_crop_bounds(view)
    return (abs(o.MaximumPoint.X - o.MinimumPoint.X)
            - model_ft_to_paper_ft(b[2] - b[0], scale),
            abs(o.MaximumPoint.Y - o.MinimumPoint.Y)
            - model_ft_to_paper_ft(b[3] - b[1], scale))


def resolve_scope_box(level_code, preferred_id=None):
    """The scope box to crop both views with, or None if it is ambiguous.

    Order: the box picked in the dialog, then a box whose name contains the
    level code (SB_F01 for F01), then the project's only scope box. Returning
    None rather than guessing is deliberate — cropping every plan to the wrong
    extent is worse than stopping with a message.
    """
    boxes = get_scope_boxes()
    if preferred_id is not None:
        for b in boxes:
            if _eid_int(b.Id) == _eid_int(preferred_id):
                return b
    if level_code:
        needle = level_code.strip().lower()
        if needle:
            for b in boxes:
                if needle in _elem_name(b).lower():
                    return b
    if len(boxes) == 1:
        return boxes[0]
    return None


def _set_viewport_type(vp, type_name):
    """Switch a viewport to the named Viewport type. Returns a status string.

    Matched case-insensitively against the types Revit says are valid for this
    viewport. Never raises: a missing type name is worth reporting, not worth
    losing a sheet over, so the viewport keeps whatever type it was created with.
    """
    wanted = type_name.strip().lower()
    found = {}
    try:
        for type_id in vp.GetValidTypes():
            element_type = doc.GetElement(type_id)
            if element_type is None:
                continue
            name = _elem_name(element_type)
            if name and name != u"?":
                found[name.strip().lower()] = type_id
    except Exception as ex:
        return u"could not list viewport types ({})".format(ex)

    if wanted not in found:
        return u"viewport type '{}' not in this project (available: {})".format(
            type_name, u", ".join(sorted(found.keys())) or u"none")
    try:
        vp.ChangeTypeId(found[wanted])
    except Exception as ex:
        return u"could not apply viewport type '{}' ({})".format(type_name, ex)
    return u"'{}'".format(type_name)


def place_overlaid(sheet, cp_view, fp_view, level_code, preferred_box_id=None):
    """Crop both views to one padded rectangle, then overlay them centre-to-centre.

    A viewport's box is the hull of the view's visible CONTENT, not its crop. The
    floor plan and the ceiling plan draw different annotations past the crop
    edge, so cropping them identically still leaves the crop sitting at a
    different place inside each box — and centring the boxes then displaces the
    drawings. The crop's position inside the box is not observable through the
    API, so it cannot be corrected arithmetically; it has to be made moot.

    Padding the crop until nothing overhangs does that: the hull becomes the crop
    itself, both views get the same rectangle with the crop exactly centred, and
    one shared box centre overlays the geometry exactly. The scope box still says
    WHERE that rectangle is — it just stops being the crop mechanism, because the
    mechanism has to be able to pad.

    Draw order is placement order and Revit offers no API to restack, so the
    floor plan is created first (back) and the RCP last (front).
    """
    warns = []

    # 0) Scales must match or no placement can overlay them.
    scale = _view_scale(fp_view)
    cp_scale = _view_scale(cp_view)
    if abs(scale - cp_scale) > 1e-9:
        raise Exception(u"scale mismatch FP=1:{:g} RCP=1:{:g} — check the "
                        u"templates".format(scale, cp_scale))

    # 1) Resolve the scope box.
    box = resolve_scope_box(level_code, preferred_box_id)
    if box is None:
        raise Exception(
            u"no scope box resolved for level '{}' ({} in the model). Alignment "
            u"needs one: name a scope box after the level code, leave exactly "
            u"one in the project, or pick one in the dialog. Not guessing, to "
            u"avoid cropping every plan to the wrong extent.".format(
                level_code, len(get_scope_boxes())))

    # 2) Crop both views to the scope box PADDED, not to the scope box itself.
    #    A viewport's box is the hull of the view's visible content, and the two
    #    views draw different annotations past the crop edge — so cropping them
    #    to the same rectangle still leaves the crop sitting at a different place
    #    inside each box, and centring the boxes displaces the drawings. Padding
    #    the crop until nothing overhangs makes the hull BE the crop: identical
    #    rectangles, crop exactly centred, overlay exact.
    pad_model_ft = paper_mm_to_model_ft(CROP_PAD_PAPER_MM, scale)
    bounds = _scope_box_bounds(box)
    if bounds is None:
        raise Exception(u"could not read the extents of scope box "
                        u"'{}'".format(_elem_name(box)))
    _crop_both(fp_view, cp_view, bounds, pad_model_ft)

    for view in (fp_view, cp_view):
        if not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
            raise Exception(u"view '{}' cannot be added to the sheet — it may "
                            u"already be placed elsewhere".format(_elem_name(view)))

    # 3) FP first (back), RCP last (front).
    vp_fp = DB.Viewport.Create(doc, sheet.Id, fp_view.Id, DB.XYZ.Zero)
    vp_cp = DB.Viewport.Create(doc, sheet.Id, cp_view.Id, DB.XYZ.Zero)

    # 3b) Viewport types. Only the floor plan carries a title: the RCP sits
    #     directly on top of it, so a second title would print over the first.
    #     Set before anything measures the viewports, since a title line is part
    #     of what Revit draws.
    vp_type_status = {
        u"FP ": _set_viewport_type(vp_fp, FP_VIEWPORT_TYPE),
        u"RCP": _set_viewport_type(vp_cp, CP_VIEWPORT_TYPE),
    }
    for _tag, _status in sorted(vp_type_status.items()):
        if not _status.startswith(u"'"):
            warns.append(u"{} viewport type: {}".format(_tag.strip(), _status))
    doc.Regenerate()

    # 4) Centre both on one point, then check nothing overhangs the crop. If
    #    something does, the pad was too small: grow it by the measured slack and
    #    try again. Growing a crop can pull further annotations into view, which
    #    is why this converges instead of being computed once.
    u_min, u_max, v_min, v_max = _usable_area(sheet)
    target = DB.XYZ((u_min + u_max) / 2.0, (v_min + v_max) / 2.0, 0)
    slack_paper_ft = None
    for attempt in range(CROP_PAD_RETRIES + 1):
        vp_fp.SetBoxCenter(target)
        vp_cp.SetBoxCenter(target)
        doc.Regenerate()
        try:
            s_fp = _measure_slack_paper_ft(vp_fp, fp_view, scale)
            s_cp = _measure_slack_paper_ft(vp_cp, cp_view, scale)
        except Exception as ex:
            warns.append(u"could not measure the viewport boxes ({})".format(ex))
            slack_paper_ft = None
            break
        slack_paper_ft = (max(s_fp[0], s_cp[0]), max(s_fp[1], s_cp[1]))
        worst_paper_ft = max(slack_paper_ft[0], slack_paper_ft[1])
        worst_mm = worst_paper_ft * MM_PER_FT
        _diag(u"    pad attempt {}: pad={:.4f} ft, slack FP {:+.1f} x {:+.1f} mm, "
              u"RCP {:+.1f} x {:+.1f} mm".format(
                  attempt + 1, pad_model_ft,
                  s_fp[0] * MM_PER_FT, s_fp[1] * MM_PER_FT,
                  s_cp[0] * MM_PER_FT, s_cp[1] * MM_PER_FT))
        if worst_mm <= 0.5:
            break
        if attempt == CROP_PAD_RETRIES:
            warns.append(
                u"content still reaches {:.1f} mm past the crop after {} "
                u"attempts, so the plans can sit up to {:.1f} mm out of register "
                u"— raise CROP_PAD_PAPER_MM above {:.0f} mm".format(
                    worst_mm, attempt + 1, worst_mm / 2.0,
                    CROP_PAD_PAPER_MM + worst_mm))
            break
        pad_model_ft += paper_ft_to_model_ft(worst_paper_ft, scale)
        _crop_both(fp_view, cp_view, bounds, pad_model_ft)

    # 5) Verify the premise: both centres on the target, both boxes the same size.
    got_fp = got_cp = None
    try:
        got_fp = vp_fp.GetBoxCenter()
        got_cp = vp_cp.GetBoxCenter()
        apart = got_fp.DistanceTo(got_cp)
        if apart > 1e-6:
            warns.append(u"Revit moved the viewports {:.1f} mm apart after "
                         u"placement".format(apart * MM_PER_FT))
    except Exception as ex:
        warns.append(u"could not read the viewport centres ({})".format(ex))

    o_fp = o_cp = None
    try:
        o_fp = vp_fp.GetBoxOutline()
        o_cp = vp_cp.GetBoxOutline()
        dw = abs((o_fp.MaximumPoint.X - o_fp.MinimumPoint.X)
                 - (o_cp.MaximumPoint.X - o_cp.MinimumPoint.X))
        dh = abs((o_fp.MaximumPoint.Y - o_fp.MinimumPoint.Y)
                 - (o_cp.MaximumPoint.Y - o_cp.MinimumPoint.Y))
        if dw > 1e-6 or dh > 1e-6:
            warns.append(
                u"the two viewport boxes are {:.1f} x {:.1f} mm apart in size, so "
                u"the plans can sit up to {:.1f} mm out of register".format(
                    dw * MM_PER_FT, dh * MM_PER_FT,
                    max(dw, dh) / 2.0 * MM_PER_FT))
    except Exception as ex:
        warns.append(u"could not compare the viewport boxes ({})".format(ex))

    # Fit check — the scale is NEVER changed automatically, only reported.
    plan_w = plan_h = 0.0
    for view in (fp_view, cp_view):
        b = _world_crop_bounds(view)
        plan_w = max(plan_w, model_ft_to_paper_ft(b[2] - b[0], scale))
        plan_h = max(plan_h,
                     model_ft_to_paper_ft(b[3] - b[1], scale) + LABEL_ALLOWANCE_FT)
    if plan_w > (u_max - u_min) or plan_h > (v_max - v_min):
        warns.append(u"did not fit at 1:{:g} — scale preserved".format(scale))

    if DIAGNOSTICS:
        _diag(u"  sheet {} '{}'".format(sheet.SheetNumber, sheet.Name))
        _diag(u"    crop from scope box '{}' padded by {:.4f} ft "
              u"({:.0f} mm per side on paper)".format(
                  _elem_name(box), pad_model_ft,
                  model_ft_to_paper_mm(pad_model_ft, scale)))
        if slack_paper_ft is not None:
            _diag(u"    final slack: {:+.1f} x {:+.1f} mm  (0 x 0 = box IS the "
                  u"crop, which is what makes the overlay exact)".format(
                      slack_paper_ft[0] * MM_PER_FT,
                      slack_paper_ft[1] * MM_PER_FT))
        _diag(u"    usable U={:.4f}..{:.4f}  V={:.4f}..{:.4f}  = {:.0f} x {:.0f} mm".format(
            u_min, u_max, v_min, v_max,
            (u_max - u_min) * MM_PER_FT, (v_max - v_min) * MM_PER_FT))
        _diag(u"    sheet target T = ({:.4f}, {:.4f})".format(target.X, target.Y))
        for tag, view, vp in ((u"FP ", fp_view, vp_fp), (u"RCP", cp_view, vp_cp)):
            _diag(u"    -- {} --".format(tag))
            _diag(u"      name          = {}".format(_elem_name(view)))
            _diag(u"      scale         = 1:{:g}".format(_view_scale(view)))
            try:
                _diag(u"      cropBoxActive = {}".format(view.CropBoxActive))
            except Exception as ex:
                _diag(u"      cropBoxActive unreadable: {}".format(ex))
            try:
                cb = view.CropBox
                _diag(u"      cropBox.Min   = ({:.4f}, {:.4f}, {:.4f})".format(
                    cb.Min.X, cb.Min.Y, cb.Min.Z))
                _diag(u"      cropBox.Max   = ({:.4f}, {:.4f}, {:.4f})".format(
                    cb.Max.X, cb.Max.Y, cb.Max.Z))
                _diag(u"      cropBox size  = {:.4f} x {:.4f} ft "
                      u"({:.0f} x {:.0f} mm on paper)".format(
                          cb.Max.X - cb.Min.X, cb.Max.Y - cb.Min.Y,
                          model_ft_to_paper_mm(cb.Max.X - cb.Min.X, scale),
                          model_ft_to_paper_mm(cb.Max.Y - cb.Min.Y, scale)))
                cw = _crop_centre_world(view)
                _diag(u"      CropCenterWorld = ({:.4f}, {:.4f}, {:.4f})".format(
                    cw.X, cw.Y, cw.Z))
            except Exception as ex:
                _diag(u"      cropBox unreadable: {}".format(ex))
            try:
                bc = vp.GetBoxCenter()
                _diag(u"      GetBoxCenter    = ({:.4f}, {:.4f})".format(bc.X, bc.Y))
            except Exception as ex:
                _diag(u"      GetBoxCenter unreadable: {}".format(ex))
            try:
                o = vp.GetBoxOutline()
                _diag(u"      BoxOutline.Min  = ({:.4f}, {:.4f})".format(
                    o.MinimumPoint.X, o.MinimumPoint.Y))
                _diag(u"      BoxOutline.Max  = ({:.4f}, {:.4f})".format(
                    o.MaximumPoint.X, o.MaximumPoint.Y))
                _diag(u"      BoxOutline size = {:.4f} x {:.4f} ft "
                      u"({:.0f} x {:.0f} mm)".format(
                          o.MaximumPoint.X - o.MinimumPoint.X,
                          o.MaximumPoint.Y - o.MinimumPoint.Y,
                          (o.MaximumPoint.X - o.MinimumPoint.X) * MM_PER_FT,
                          (o.MaximumPoint.Y - o.MinimumPoint.Y) * MM_PER_FT))
            except Exception as ex:
                _diag(u"      BoxOutline unreadable: {}".format(ex))
            try:
                _diag(u"      Rotation        = {}".format(vp.Rotation))
            except Exception as ex:
                _diag(u"      Rotation unreadable: {}".format(ex))
            off = _annotation_offsets(view)
            _diag(u"      annotationCrop  = {}  offsets {}".format(
                _annotation_crop_active(view),
                u"L={:.4f} R={:.4f} T={:.4f} B={:.4f}".format(*off)
                if off else u"UNREADABLE"))
        if o_fp is not None and o_cp is not None:
            _diag(u"    box size delta FP-RCP = {:+.4f} x {:+.4f} ft".format(
                (o_fp.MaximumPoint.X - o_fp.MinimumPoint.X)
                - (o_cp.MaximumPoint.X - o_cp.MinimumPoint.X),
                (o_fp.MaximumPoint.Y - o_fp.MinimumPoint.Y)
                - (o_cp.MaximumPoint.Y - o_cp.MinimumPoint.Y)))
        _diag(u"    viewport types: FP {}  RCP {}".format(
            vp_type_status.get(u"FP ", u"?"), vp_type_status.get(u"RCP", u"?")))
        _diag(u"    z-order: FP created first (back), RCP last (front)")

    return warns


# ─────────────────────────────────────────────────────────────────────────────
# 4. CORE OPERATION
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_previous_output():
    """Delete the views and sheets THIS tool made on earlier runs.

    Opt-in, and deliberately narrow: only views whose names carry the tool's own
    CD_Floor Plan_ / CD_Ceiling Plan_ prefixes and only sheets whose name ends
    in -MEP. Nothing else is touched. Without this, re-runs suffix rather than
    replace, so a test model accumulates generations of near-identical sheets
    and it stops being obvious which one you are looking at.
    """
    removed_sheets = removed_views = 0

    for s in list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet)):
        try:
            if s.Name.endswith(SHEET_SUFFIX):
                doc.Delete(s.Id)
                removed_sheets += 1
        except Exception:
            continue

    for v in list(DB.FilteredElementCollector(doc).OfClass(DB.View)):
        try:
            if v.IsTemplate:
                continue
            name = _elem_name(v)
            if name.startswith(FP_PREFIX) or name.startswith(CP_PREFIX):
                doc.Delete(v.Id)
                removed_views += 1
        except Exception:
            continue

    return removed_views, removed_sheets


def _create_views(level, code, fp_vft, cp_vft, fp_tpl, cp_tpl, view_names):
    """Create + template + rename the FP and RCP for one level."""
    fp = DB.ViewPlan.Create(doc, fp_vft.Id, level.Id)
    cp = DB.ViewPlan.Create(doc, cp_vft.Id, level.Id)

    # Template first: it drives scale, which the placement math reads.
    fp.ViewTemplateId = fp_tpl.Id
    cp.ViewTemplateId = cp_tpl.Id
    doc.Regenerate()

    fp.Name = unique_view_name(FP_PREFIX + code, view_names)
    cp.Name = unique_view_name(CP_PREFIX + code, view_names)
    return fp, cp


def _world_crop_bounds(view):
    """(min_x, min_y, max_x, max_y) of a view's crop rectangle in world XY."""
    bb = view.CropBox
    t = bb.Transform
    xs, ys = [], []
    for x in (bb.Min.X, bb.Max.X):
        for y in (bb.Min.Y, bb.Max.Y):
            p = t.OfPoint(DB.XYZ(x, y, bb.Min.Z))
            xs.append(p.X)
            ys.append(p.Y)
    return min(xs), min(ys), max(xs), max(ys)


def _build_sheet(level, code, number, title_block, fp, cp, sheet_numbers,
                 extents):
    """Make the sheet, then crop and place both views. Own transaction.

    The crop is no longer applied here: place_overlaid() owns it, because the
    scope box it assigns is what makes the two viewport boxes identical, and
    applying an extent twice from two places could only disagree.
    """
    warns = []
    sheet = DB.ViewSheet.Create(doc, title_block.Id)
    sheet.SheetNumber = unique_sheet_number(_txt(number), sheet_numbers)
    sheet.Name = code + SHEET_SUFFIX
    doc.Regenerate()

    warns.extend(place_overlaid(
        sheet, cp, fp, code,
        extents.get(u"id") if extents.get(u"kind") == u"scopebox" else None))

    # Persist the confirmed code so the next run reads it back as "stored".
    p = level.LookupParameter(CODE_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(code)
    else:
        warns.append(u"could not write {}".format(CODE_PARAM))

    return {
        u"ok": True,
        u"level": level.Name,
        u"code": code,
        u"sheet": sheet.SheetNumber,
        u"sheet_name": sheet.Name,
        u"sheet_id": sheet.Id,
        u"fp": _elem_name(fp),
        u"cp": _elem_name(cp),
        u"warns": warns,
    }


def _failed(level, code, reason):
    return {
        u"ok": False,
        u"level": level.Name,
        u"code": code,
        u"sheet": u"—",
        u"sheet_name": u"—",
        u"sheet_id": None,
        u"fp": u"—",
        u"cp": u"—",
        u"warns": [u"failed: {}".format(reason)],
    }


def run(work, title_block, fp_vft, cp_vft, fp_tpl, cp_tpl, extents,
        clean_previous=False, progress=None):
    """Create views + sheets for each (level, code) in `work`. One undo step.

    Two phases. Views are created first, because a fallback crop rectangle can
    only be sized once their natural extents are known; sheets, viewports and
    their placement follow per level. A failure on one level rolls back only
    that level's transaction and the batch continues, so one bad level can
    never abort the run.

    `progress`, if given, is called as progress(fraction, label) as work
    proceeds — on a large model this blocks Revit for long enough that a static
    label reads as frozen.
    """
    results = []
    built = []          # (level, code, fp, cp) for levels whose views exist
    cleaned = None      # (views, sheets) removed, when clean_previous is on

    total_steps = max(1, len(work) * 2)
    step = 0

    tg = DB.TransactionGroup(doc, u"EasyBIM: Level Sheets")
    tg.Start()
    try:
        if clean_previous:
            t = DB.Transaction(doc, u"EasyBIM: Remove previous Level Sheets output")
            t.Start()
            try:
                gone_v, gone_s = cleanup_previous_output()
                t.Commit()
                cleaned = (gone_v, gone_s)
                _diag(u"  removed {} view(s) and {} sheet(s) from earlier runs".format(
                    gone_v, gone_s))
            except Exception as ex:
                t.RollBack()
                logger.error(u"Level Sheets: cleanup failed: %s", ex)

        # Names freed by the cleanup must not still be treated as taken.
        view_names = existing_view_names()
        sheet_numbers = existing_sheet_numbers()

        if not title_block.IsActive:
            t = DB.Transaction(doc, u"EasyBIM: Activate title block")
            t.Start()
            title_block.Activate()
            doc.Regenerate()
            t.Commit()

        # ── Phase 1: create every view ───────────────────────────────────────
        # Sheets wait for phase 2 because the crop rectangle they all share is
        # only settled once the views exist and the grid envelope is known.
        # Fallback union is over every view rather than first-wins, so a
        # small-footprint basement cannot clip the tower floors.
        for i, (level, code) in enumerate(work):
            if progress is not None:
                progress(float(step) / total_steps,
                         u"Creating views · {} of {}".format(i + 1, len(work)))
            step += 1
            t = DB.Transaction(doc, u"EasyBIM: Views for {}".format(code))
            t.Start()
            try:
                fp, cp = _create_views(level, code, fp_vft, cp_vft,
                                       fp_tpl, cp_tpl, view_names)
                t.Commit()
                built.append((level, code, fp, cp))
            except Exception as ex:
                t.RollBack()
                logger.error(u"Level Sheets: views for '%s' failed: %s",
                             level.Name, ex)
                results.append(_failed(level, code, ex))

        # The crop comes from the scope box place_overlaid() resolves per level.
        _diag(u"EasyBIM Level Sheets — placement diagnostics")
        _diag(u"  scope box preference: {}".format(
            _elem_name(doc.GetElement(extents[u"id"]))
            if extents.get(u"kind") == u"scopebox" else u"automatic"))

        # ── Phase 2: apply the extent, sheet it, drop the viewports ──────────
        number = SHEET_START
        for i, (level, code, fp, cp) in enumerate(built):
            if progress is not None:
                progress(float(step) / total_steps,
                         u"Building {} · {} of {}".format(code, i + 1, len(built)))
            step += 1
            t = DB.Transaction(doc, u"EasyBIM: Level sheet {}".format(code))
            t.Start()
            try:
                res = _build_sheet(level, code, number, title_block, fp, cp,
                                   sheet_numbers, extents)
                t.Commit()
                results.append(res)
                number += 1
            except Exception as ex:
                t.RollBack()
                logger.error(u"Level Sheets: sheet for '%s' failed: %s",
                             level.Name, ex)
                results.append(_failed(level, code, ex))

        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise

    return results, cleaned


# ─────────────────────────────────────────────────────────────────────────────
# WPF XAML
# ─────────────────────────────────────────────────────────────────────────────

XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Level Sheets"
  Width="880" Height="830"
  WindowStartupLocation="CenterScreen"
  ResizeMode="CanMinimize"
  WindowStyle="SingleBorderWindow"
  FontFamily="Segoe UI"
  Background="#f7f8ff">
  <Window.Resources>

    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background"      Value="#1e248c"/>
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="18,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}" CornerRadius="7"
                    Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#44b8d3"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter Property="Opacity" Value="0.42"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="GhostBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#6b7280"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Height"          Value="36"/>
      <Setter Property="Padding"         Value="14,0"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush"     Value="#e8eaff"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}"
                    CornerRadius="7" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#f0f2ff"/>
              </Trigger>
              <!-- Cancel is disabled while a run is in progress. -->
              <Trigger Property="IsEnabled" Value="False">
                <Setter Property="Opacity" Value="0.5"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="CyanTextBtn" TargetType="Button">
      <Setter Property="Background"      Value="Transparent"/>
      <Setter Property="Foreground"      Value="#44b8d3"/>
      <Setter Property="FontSize"        Value="12"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="Height"          Value="22"/>
      <Setter Property="Padding"         Value="0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
    </Style>

    <Style x:Key="SectionLabel" TargetType="TextBlock">
      <Setter Property="FontFamily" Value="Consolas"/>
      <Setter Property="FontSize"   Value="10"/>
      <Setter Property="Foreground" Value="#9aa0ac"/>
      <Setter Property="Margin"     Value="0,0,0,8"/>
    </Style>

    <Style x:Key="Picker" TargetType="ComboBox">
      <Setter Property="Height"     Value="32"/>
      <Setter Property="FontFamily" Value="Consolas"/>
      <Setter Property="FontSize"   Value="11.5"/>
      <Setter Property="Foreground" Value="#374151"/>
      <Setter Property="Cursor"     Value="Hand"/>
    </Style>

  </Window.Resources>

  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="76"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- HEADER -->
    <Border Grid.Row="0">
      <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
          <GradientStop Color="#1e248c" Offset="0"/>
          <GradientStop Color="#2b5cbf" Offset="0.55"/>
          <GradientStop Color="#44b8d3" Offset="1"/>
        </LinearGradientBrush>
      </Border.Background>
      <Grid Margin="20,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="32"/>
        </Grid.ColumnDefinitions>
        <Border Grid.Column="0" Width="44" Height="44" CornerRadius="10"
                VerticalAlignment="Center" Background="#151b6e">
          <Canvas Width="24" Height="24" HorizontalAlignment="Center" VerticalAlignment="Center">
            <Rectangle Canvas.Left="3" Canvas.Top="2.5" Width="18" Height="19"
                       RadiusX="1.5" RadiusY="1.5" Stroke="White" StrokeThickness="1.6"/>
            <Rectangle Canvas.Left="6" Canvas.Top="5.5" Width="12" Height="5"
                       RadiusX="0.8" RadiusY="0.8" Stroke="#b8e8f2" StrokeThickness="1.3"/>
            <Rectangle Canvas.Left="6" Canvas.Top="12" Width="12" Height="5"
                       RadiusX="0.8" RadiusY="0.8" Stroke="#b8e8f2" StrokeThickness="1.3"/>
            <Line X1="6" Y1="19.4" X2="18" Y2="19.4" Stroke="White" StrokeThickness="1.4"
                  StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
          </Canvas>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="Level Sheets" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Batch plan views &amp; sheets from levels · Stage A"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Content="✕" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- INFO BANNER -->
    <Border x:Name="InfoBanner" Grid.Row="1" Background="#f0f2ff" BorderBrush="#e8eaff"
            BorderThickness="0,0,0,1" Padding="20,11">
      <TextBlock TextWrapping="Wrap" FontSize="12.5" Foreground="#374151"
                 Text="Pick the levels to build. Each one gets a floor plan and a ceiling plan on their own sheet — the code below drives every name, so a level's Revit name never reaches the output."/>
    </Border>

    <!-- BODY -->
    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
      <Grid Margin="20,14,20,10">

        <!-- SETUP -->
        <StackPanel x:Name="ConfigPanel">

          <!-- template preflight -->
          <Border x:Name="TplBanner" BorderThickness="1" CornerRadius="8"
                  Padding="11,9" Margin="0,0,0,16">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              <TextBlock x:Name="TplIcon" Grid.Column="0" FontSize="13" FontWeight="Bold"
                         Margin="0,0,9,0" VerticalAlignment="Center"/>
              <TextBlock x:Name="TplText" Grid.Column="1" TextWrapping="Wrap"
                         VerticalAlignment="Center" FontSize="12.5" Foreground="#374151"/>
              <Border x:Name="TplBadge" Grid.Column="2" Background="#22b07c" CornerRadius="10"
                      Padding="8,3" VerticalAlignment="Center">
                <TextBlock Text="Ready" FontSize="10.5" FontWeight="SemiBold" Foreground="White"/>
              </Border>
            </Grid>
          </Border>

          <!-- levels -->
          <Grid Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="LevelsLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                       Foreground="#9aa0ac" VerticalAlignment="Center"/>
            <Button x:Name="ToggleAllBtn" Grid.Column="1" Content="Select all"
                    Style="{StaticResource CyanTextBtn}"/>
          </Grid>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                  CornerRadius="8" Margin="0,0,0,16">
            <StackPanel>
              <Border Background="#f4f6fd" CornerRadius="8,8,0,0" BorderBrush="#e8eaff"
                      BorderThickness="0,0,0,1">
                <StackPanel x:Name="LevelHeadPanel"/>
              </Border>
              <ScrollViewer MaxHeight="286" VerticalScrollBarVisibility="Visible"
                            HorizontalScrollBarVisibility="Disabled">
                <StackPanel x:Name="LevelRowsPanel"/>
              </ScrollViewer>
            </StackPanel>
          </Border>

          <!-- pickers -->
          <TextBlock Text="SHEET SETUP" Style="{StaticResource SectionLabel}"/>
          <Grid Margin="0,0,0,16">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="10"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="10"/>
              <ColumnDefinition Width="1.4*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
              <TextBlock Text="TITLE BLOCK" Style="{StaticResource SectionLabel}" Margin="0,0,0,5"/>
              <ComboBox x:Name="TitleBlockCombo" Style="{StaticResource Picker}"/>
            </StackPanel>
            <StackPanel Grid.Column="2">
              <TextBlock Text="TEMPLATES · SCALE" Style="{StaticResource SectionLabel}" Margin="0,0,0,5"/>
              <ComboBox x:Name="TemplateCombo" Style="{StaticResource Picker}"/>
            </StackPanel>
            <StackPanel Grid.Column="4">
              <TextBlock Text="SCOPE BOX · SETS THE CROP FOR BOTH PLANS" Style="{StaticResource SectionLabel}" Margin="0,0,0,5"/>
              <ComboBox x:Name="ExtentCombo" Style="{StaticResource Picker}"/>
            </StackPanel>
          </Grid>
          <Border x:Name="FitBanner" BorderThickness="1" CornerRadius="8"
                  Padding="11,9" Margin="0,0,0,16" Visibility="Collapsed"
                  Background="#fdf6e3" BorderBrush="#e8d9a8">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="&#x26A0;" FontSize="13" Foreground="#d9a406"
                         Margin="0,0,9,0" VerticalAlignment="Center"/>
              <TextBlock x:Name="FitText" Grid.Column="1" TextWrapping="Wrap"
                         VerticalAlignment="Center" FontSize="12.5" Foreground="#374151"/>
            </Grid>
          </Border>
          <CheckBox x:Name="CleanPrevChk" Margin="0,0,0,16" Foreground="#374151"
                    FontSize="12.5" Cursor="Hand">
            <TextBlock TextWrapping="Wrap">
              <Run Text="First delete the views and sheets earlier runs created"/>
              <Run Text=" — anything named " Foreground="#9aa0ac"/>
              <Run Text="CD_Floor Plan_*" FontFamily="Consolas" Foreground="#1e248c"/>
              <Run Text=" / " Foreground="#9aa0ac"/>
              <Run Text="CD_Ceiling Plan_*" FontFamily="Consolas" Foreground="#1e248c"/>
              <Run Text=" and sheets ending " Foreground="#9aa0ac"/>
              <Run Text="-MEP" FontFamily="Consolas" Foreground="#1e248c"/>
              <Run Text=". Otherwise names are suffixed and both generations stay in the model." Foreground="#9aa0ac"/>
            </TextBlock>
          </CheckBox>

          <!-- validation errors -->
          <Border x:Name="ErrorsCard" Background="#fbecec" BorderBrush="#f0c6c6" BorderThickness="1"
                  CornerRadius="8" Padding="11,9" Margin="0,16,0,0" Visibility="Collapsed">
            <StackPanel x:Name="ErrorsPanel"/>
          </Border>

        </StackPanel>

        <!-- REPORT -->
        <StackPanel x:Name="ResultsPanel" Visibility="Collapsed">
          <StackPanel HorizontalAlignment="Center" Margin="0,4,0,18">
            <Border x:Name="ResIconCircle" Width="52" Height="52" CornerRadius="26"
                    Background="#e4f7f0" HorizontalAlignment="Center" Margin="0,0,0,12">
              <TextBlock x:Name="ResIconTB" Text="&#x2713;" FontSize="26" FontWeight="Bold"
                         Foreground="#22b07c"
                         HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <TextBlock x:Name="ResTitleTB" FontSize="18" FontWeight="Bold" Foreground="#1e248c"
                       HorizontalAlignment="Center"/>
            <TextBlock x:Name="ResSummaryTB" FontSize="12.5" Foreground="#6b7280"
                       HorizontalAlignment="Center" Margin="0,3,0,0" TextWrapping="Wrap"
                       TextAlignment="Center"/>
          </StackPanel>

          <Grid Margin="0,0,0,16">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <Border Grid.Column="0" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatLevelsTB" FontFamily="Consolas" FontWeight="Bold"
                           FontSize="22" Foreground="#1e248c" HorizontalAlignment="Center"/>
                <TextBlock Text="Levels processed" FontSize="10.5" Foreground="#6b7280"
                           Margin="0,4,0,0" HorizontalAlignment="Center" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="2" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatSheetsTB" FontFamily="Consolas" FontWeight="Bold"
                           FontSize="22" Foreground="#22b07c" HorizontalAlignment="Center"/>
                <TextBlock Text="Sheets created" FontSize="10.5" Foreground="#6b7280"
                           Margin="0,4,0,0" HorizontalAlignment="Center" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="4" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatViewsTB" FontFamily="Consolas" FontWeight="Bold"
                           FontSize="22" Foreground="#44b8d3" HorizontalAlignment="Center"/>
                <TextBlock Text="Views created" FontSize="10.5" Foreground="#6b7280"
                           Margin="0,4,0,0" HorizontalAlignment="Center" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="6" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatFailedTB" FontFamily="Consolas" FontWeight="Bold"
                           FontSize="22" Foreground="#9aa0ac" HorizontalAlignment="Center"/>
                <TextBlock Text="Failed" FontSize="10.5" Foreground="#6b7280"
                           Margin="0,4,0,0" HorizontalAlignment="Center" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="8" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                    CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatWarnTB" FontFamily="Consolas" FontWeight="Bold"
                           FontSize="22" Foreground="#9aa0ac" HorizontalAlignment="Center"/>
                <TextBlock Text="Warnings" FontSize="10.5" Foreground="#6b7280"
                           Margin="0,4,0,0" HorizontalAlignment="Center" TextAlignment="Center"/>
              </StackPanel>
            </Border>
          </Grid>

          <TextBlock Text="PER LEVEL" Style="{StaticResource SectionLabel}"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                  CornerRadius="8" Margin="0,0,0,12">
            <StackPanel>
              <Border Background="#f4f6fd" CornerRadius="8,8,0,0" BorderBrush="#e8eaff"
                      BorderThickness="0,0,0,1">
                <StackPanel x:Name="ReportHeadPanel"/>
              </Border>
              <ScrollViewer MaxHeight="300" VerticalScrollBarVisibility="Visible"
                            HorizontalScrollBarVisibility="Disabled">
                <StackPanel x:Name="ReportRowsPanel"/>
              </ScrollViewer>
            </StackPanel>
          </Border>

          <Border Background="#f2fafc" BorderBrush="#c9ecf3" BorderThickness="1"
                  CornerRadius="8" Padding="13,10">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="26"/><ColumnDefinition Width="*"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="&#x2139;" FontSize="14" Foreground="#44b8d3"
                         VerticalAlignment="Top"/>
              <TextBlock x:Name="ReportNoteTB" Grid.Column="1" VerticalAlignment="Center"
                         TextWrapping="Wrap" FontSize="12.5" Foreground="#374151"/>
            </Grid>
          </Border>
        </StackPanel>

      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Border Grid.Row="3" Background="White" BorderBrush="#e8eaff" BorderThickness="0,1,0,0"
            Padding="20,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Grid Grid.Column="0">
          <TextBlock x:Name="FooterSummaryTB" FontFamily="Consolas" FontSize="12"
                     Foreground="#9aa0ac" VerticalAlignment="Center"/>
          <TextBlock x:Name="TickerTB" FontFamily="Consolas" FontSize="12" FontWeight="SemiBold"
                     Foreground="#1e248c" VerticalAlignment="Center" Visibility="Collapsed"/>
        </Grid>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <StackPanel x:Name="ProgressPanel" Orientation="Horizontal" VerticalAlignment="Center"
                      Visibility="Collapsed" Margin="0,0,12,0">
            <Border Width="160" Height="6" CornerRadius="3" Background="#e8eaff"
                    VerticalAlignment="Center">
              <Grid HorizontalAlignment="Left">
                <Border x:Name="ProgressFill" Width="0" Height="6" CornerRadius="3"
                        HorizontalAlignment="Left">
                  <Border.Background>
                    <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                      <GradientStop Color="#1e248c" Offset="0"/>
                      <GradientStop Color="#44b8d3" Offset="1"/>
                    </LinearGradientBrush>
                  </Border.Background>
                </Border>
              </Grid>
            </Border>
            <TextBlock x:Name="ProgressPctTB" FontFamily="Consolas" FontSize="12"
                       Foreground="#1e248c" VerticalAlignment="Center" Margin="8,0,0,0"
                       Width="38" TextAlignment="Right"/>
          </StackPanel>
          <Button x:Name="CancelBtn" Content="Cancel" Style="{StaticResource GhostBtn}"/>
          <Button x:Name="RunBtn"   Content="▶  Create" Style="{StaticResource PrimaryBtn}"
                  Margin="8,0,0,0"/>
          <Button x:Name="AgainBtn" Content="↺  Run again" Style="{StaticResource GhostBtn}"
                  Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="OpenBtn"  Content="Open first sheet  ↗" Style="{StaticResource PrimaryBtn}"
                  Visibility="Collapsed" Margin="8,0,0,0"/>
        </StackPanel>
      </Grid>
    </Border>

  </Grid>
</Window>
"""


# ─────────────────────────────────────────────────────────────────────────────
# DIALOG
# ─────────────────────────────────────────────────────────────────────────────

# Palette is deliberately identical to the other MEP Coordination buttons
# (Solution Section, Head Height Check) — the three sit side by side on one
# panel, so matching them beats matching reference/tokens.md, which specifies
# darker semantic colours (#15803d / #b45309 / #ba1a1a) that the shipped
# buttons never adopted. Revisit only if all three are re-toned together.
NAVY_COLOR  = WM.Color.FromRgb(0x1e, 0x24, 0x8c)
CYAN_COLOR  = WM.Color.FromRgb(0x44, 0xb8, 0xd3)
GRAY_COLOR  = WM.Color.FromRgb(0xc4, 0xca, 0xd9)
GREEN_COLOR = WM.Color.FromRgb(0x22, 0xb0, 0x7c)
AMBER_COLOR = WM.Color.FromRgb(0xd9, 0xa4, 0x06)
RED_COLOR   = WM.Color.FromRgb(0xd6, 0x45, 0x45)
BODY_COLOR  = WM.Color.FromRgb(0x37, 0x41, 0x51)
INK_COLOR   = WM.Color.FromRgb(0x1f, 0x29, 0x37)
DIM_COLOR   = WM.Color.FromRgb(0x6b, 0x72, 0x80)
MUTED_COLOR = WM.Color.FromRgb(0x9a, 0xa0, 0xac)
HEAD_COLOR  = WM.Color.FromRgb(0x8b, 0x93, 0xa7)
LINE_COLOR  = WM.Color.FromRgb(0xf0, 0xf1, 0xff)
CARD_LINE   = WM.Color.FromRgb(0xe8, 0xea, 0xff)
SUBTLE_BG   = WM.Color.FromRgb(0xfa, 0xfb, 0xff)
PILL_BG     = WM.Color.FromRgb(0xf1, 0xf2, 0xf7)
NAVY_PILL   = WM.Color.FromArgb(0x16, 0x1e, 0x24, 0x8c)
CYAN_PILL   = WM.Color.FromArgb(0x24, 0x44, 0xb8, 0xd3)
ERR_BG      = WM.Color.FromArgb(0x12, 0xd6, 0x45, 0x45)
WARN_BG     = WM.Color.FromArgb(0x12, 0xd9, 0xa4, 0x06)
SUCCESS_BG  = WM.Color.FromRgb(0xe4, 0xf7, 0xf0)   # report success circle
BANNER_BG   = WM.Color.FromRgb(0xe9, 0xf9, 0xf2)   # preflight banner, as HHC
BANNER_LN   = WM.Color.FromRgb(0xb9, 0xe9, 0xd3)
ERRCARD_BG  = WM.Color.FromRgb(0xfb, 0xec, 0xec)
ERRCARD_LN  = WM.Color.FromRgb(0xf0, 0xc6, 0xc6)
WARN_TINT   = WM.Color.FromArgb(0x1f, 0xd9, 0xa4, 0x06)

# Level-grid column widths in device-independent pixels. Header and rows are
# both built from this one list, so they can never drift out of alignment.
GRID_COLS = [30, 244, 78, 116, 78, 210]
GRID_HEAD = [u"", u"LEVEL (REVIT NAME)", u"ELEV (M)", CODE_PARAM.upper(),
             u"SOURCE", u"SHEET → NUMBER · NAME"]

REPORT_COLS = [70, 116, 174, 174, 188]
REPORT_HEAD = [u"SHEET", u"NAME", u"FLOOR PLAN", u"CEILING PLAN", u"WARNING"]

# Must match the track Width in the footer's ProgressPanel XAML.
PROGRESS_BAR_WIDTH = 160.0

CONSOLAS = WM.FontFamily(u"Consolas")
STAR = System.Windows.GridUnitType.Star


def _color(c):
    return WM.SolidColorBrush(c)


def _thick(*a):
    return System.Windows.Thickness(*a)


def _fixed_grid(widths):
    """Grid with fixed pixel columns — the shared basis for header + rows."""
    grid = WC.Grid()
    for w in widths:
        cd = WC.ColumnDefinition()
        cd.Width = System.Windows.GridLength(w)
        grid.ColumnDefinitions.Add(cd)
    return grid


class LevelSheetsDialog(object):

    def __init__(self, levels, title_blocks, fp_vfts, cp_vfts, tpl_sets,
                 extent_options):
        self._levels = levels
        self._title_blocks = title_blocks
        # One of each is all this tool needs, so it is resolved here rather than
        # asked for: the project default if there is one, else the first found.
        self._fp_vft = default_view_family_type(
            DB.ViewFamily.FloorPlan, DB.ElementTypeGroup.ViewTypeFloorPlan, fp_vfts)
        self._cp_vft = default_view_family_type(
            DB.ViewFamily.CeilingPlan, DB.ElementTypeGroup.ViewTypeCeilingPlan,
            cp_vfts)
        self._tpl_sets = tpl_sets
        self._extent_options = extent_options

        self._sugg = suggest_codes(levels)
        self._stored = {}
        self._on = {}
        for lvl in levels:
            key = _eid_int(lvl.Id)
            self._stored[key] = get_stored_code(lvl)
            self._on[key] = True
        self._edited = {}          # key -> user-typed code

        self._rows = []
        self._results = None
        self._cleaned = None
        self._first_sheet_id = None
        self.open_first = False
        self.cancelled = True
        self._window = None
        self._suppress = False     # guards TextChanged during programmatic edits

    # ── code resolution ──────────────────────────────────────────────────────

    def _key(self, level):
        return _eid_int(level.Id)

    def _code_of(self, level):
        key = self._key(level)
        if key in self._edited:
            return self._edited[key]
        return self._stored.get(key) or self._sugg.get(key, u"")

    def _source_of(self, level):
        key = self._key(level)
        if key in self._edited:
            return u"edited"
        if self._stored.get(key):
            return u"stored"
        return u"suggested"

    def _selected(self):
        return [l for l in self._levels if self._on.get(self._key(l))]

    def _sel_tpl_set(self):
        """The currently chosen template pair, or None when none resolved."""
        if not self._tpl_sets:
            return None
        if self._window is None:
            return self._tpl_sets[0]
        i = self._window.FindName(u"TemplateCombo").SelectedIndex
        return self._tpl_sets[max(0, i)]

    def _sel_extents(self):
        """The chosen plan extent, or None while nothing is selected."""
        i = self._window.FindName(u"ExtentCombo").SelectedIndex
        if i < 0 or i >= len(self._extent_options):
            return None
        return self._extent_options[i]

    def _sel_scale(self):
        tpl = self._sel_tpl_set()
        return _view_scale(tpl[u"fp"]) if tpl else 50.0

    # ── validation ───────────────────────────────────────────────────────────

    def _validate(self):
        sel = self._selected()
        errs = []
        bad = {}

        counts = {}
        for l in sel:
            code = self._code_of(l).strip()
            counts[code] = counts.get(code, 0) + 1
        for l in sel:
            code = self._code_of(l).strip()
            if not code:
                bad[self._key(l)] = u"empty"
            elif counts[code] > 1:
                bad[self._key(l)] = u"dup"

        flaws = bad.values()
        if not sel:
            errs.append(u"Select at least one level.")
        if u"empty" in flaws:
            errs.append(u"Every selected level needs a code.")
        if u"dup" in flaws:
            errs.append(u"Codes must be unique among selected levels.")
        if not self._tpl_sets:
            errs.append(u"No EasyBIM template pair found. Expected {}.".format(
                u" or ".join(u"{} + {}".format(fp, cp)
                             for fp, cp, _ in TEMPLATE_SETS)))
        if not self._title_blocks:
            errs.append(u"No title block is loaded in this model.")
        if self._fp_vft is None:
            errs.append(u"No Floor Plan view family type found.")
        if self._cp_vft is None:
            errs.append(u"No Ceiling Plan view family type found.")
        # Deliberately unselected at startup: the scope box decides what both
        # plans are cropped to, so it is chosen rather than defaulted.
        if self._window is not None and self._sel_extents() is None:
            errs.append(u"Choose a scope box — it crops both plans, and the "
                        u"overlay needs them cropped identically.")

        return errs, bad

    # ── build ────────────────────────────────────────────────────────────────

    def _build(self):
        ctx = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        w.FindName(u"CloseBtn").Click  += lambda s, e: w.Close()
        w.FindName(u"CancelBtn").Click += self._on_cancel
        w.FindName(u"RunBtn").Click    += self._on_run
        w.FindName(u"AgainBtn").Click  += self._on_again
        w.FindName(u"OpenBtn").Click   += self._on_open_sheet
        w.FindName(u"ToggleAllBtn").Click += self._on_toggle_all

        self._build_tpl_banner()
        self._populate_pickers()
        self._build_head(u"LevelHeadPanel", GRID_COLS, GRID_HEAD, right_align=[2])
        self._populate_levels()
        self._refresh()
        return window

    def _build_tpl_banner(self):
        w = self._window
        banner = w.FindName(u"TplBanner")
        icon = w.FindName(u"TplIcon")
        text = w.FindName(u"TplText")
        badge = w.FindName(u"TplBadge")

        if self._tpl_sets:
            banner.Background = _color(BANNER_BG)
            banner.BorderBrush = _color(BANNER_LN)
            icon.Text = u"✓"
            icon.Foreground = _color(GREEN_COLOR)
            text.Text = u"{} template pair{} resolved: {}. Both plans share one scale, so grids align 1:1.".format(
                len(self._tpl_sets), u"" if len(self._tpl_sets) == 1 else u"s",
                u" · ".join(s[u"label"] for s in self._tpl_sets))
            badge.Visibility = System.Windows.Visibility.Visible
        else:
            banner.Background = _color(ERRCARD_BG)
            banner.BorderBrush = _color(ERRCARD_LN)
            icon.Text = u"!"
            icon.Foreground = _color(RED_COLOR)
            text.Text = (u"No EasyBIM template pair found — cannot run. Both halves "
                         u"of a pair must exist, matched by exact name: {}.".format(
                             u" or ".join(u"{} + {}".format(fp, cp)
                                          for fp, cp, _ in TEMPLATE_SETS)))
            badge.Visibility = System.Windows.Visibility.Collapsed

    def _populate_pickers(self):
        w = self._window
        tb = w.FindName(u"TitleBlockCombo")
        for t in self._title_blocks:
            tb.Items.Add(title_block_label(t))

        tpl = w.FindName(u"TemplateCombo")
        for s in self._tpl_sets:
            tpl.Items.Add(u"{}   ({} + {})".format(
                s[u"label"], s[u"fp_name"], s[u"cp_name"]))
        ext = w.FindName(u"ExtentCombo")
        for o in self._extent_options:
            ext.Items.Add(o[u"label"])

        if tpl.Items.Count:
            tpl.SelectedIndex = 0
        # Two plans on one A0 sheet is the normal case here, so start there
        # rather than on whichever title block happens to sort first.
        if tb.Items.Count:
            tb.SelectedIndex = _closest_to_a0(self._title_blocks)
        # The scope box is left UNSELECTED on purpose. It decides what both plans
        # are cropped to, and a wrong one crops every sheet to the wrong extent —
        # so it is worth one deliberate click rather than a default that happens
        # to be first in the list. Create stays disabled until it is chosen.
        ext.SelectedIndex = -1
        for combo in (tb, tpl, ext):
            combo.SelectionChanged += self._on_picker_changed

    def _build_head(self, panel_name, widths, headings, right_align=None):
        right_align = right_align or []
        panel = self._window.FindName(panel_name)
        panel.Children.Clear()
        grid = _fixed_grid(widths)
        grid.Margin = _thick(0, 6, 0, 6)
        for i, head in enumerate(headings):
            tb = WC.TextBlock()
            tb.Text = head
            tb.FontFamily = CONSOLAS
            tb.FontSize = 9
            tb.FontWeight = System.Windows.FontWeights.Bold
            tb.Foreground = _color(HEAD_COLOR)
            tb.Margin = _thick(8, 0, 8, 0)
            tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
            tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
            if i in right_align:
                tb.TextAlignment = System.Windows.TextAlignment.Right
            WC.Grid.SetColumn(tb, i)
            grid.Children.Add(tb)
        panel.Children.Add(grid)

    def _populate_levels(self):
        panel = self._window.FindName(u"LevelRowsPanel")
        panel.Children.Clear()
        self._rows = []
        for i, level in enumerate(self._levels):
            row = self._make_level_row(level, i == len(self._levels) - 1)
            panel.Children.Add(row[u"border"])
            self._rows.append(row)

    def _make_level_row(self, level, is_last):
        outer = WC.Border()
        outer.Padding = _thick(0, 3, 0, 3)
        outer.Background = WM.Brushes.Transparent
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = _thick(0, 0, 0, 1)

        grid = _fixed_grid(GRID_COLS)

        # 0 · checkbox
        cb = WC.Border()
        cb.Width = 16
        cb.Height = 16
        cb.CornerRadius = System.Windows.CornerRadius(4)
        cb.BorderThickness = _thick(1.5)
        cb.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb.Cursor = WI.Cursors.Hand
        check = WC.TextBlock()
        check.Text = u"✓"
        check.FontSize = 10
        check.FontWeight = System.Windows.FontWeights.Bold
        check.Foreground = WM.Brushes.White
        check.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        check.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb.Child = check
        WC.Grid.SetColumn(cb, 0)

        # 1 · level name — shown for recognition only, never parsed for the code
        name_panel = WC.StackPanel()
        name_panel.Orientation = WC.Orientation.Horizontal
        name_panel.Margin = _thick(8, 0, 8, 0)
        name_panel.VerticalAlignment = System.Windows.VerticalAlignment.Center
        name_panel.Background = WM.Brushes.Transparent
        name_panel.Cursor = WI.Cursors.Hand

        glyph = WC.TextBlock()
        glyph.Text = u"▤"
        glyph.FontSize = 10
        glyph.Foreground = _color(WM.Color.FromRgb(0xb6, 0xbd, 0xd0))
        glyph.Margin = _thick(0, 0, 6, 0)
        glyph.VerticalAlignment = System.Windows.VerticalAlignment.Center

        name_tb = WC.TextBlock()
        name_tb.Text = level.Name
        name_tb.FontSize = 11.5
        name_tb.Foreground = _color(BODY_COLOR)
        name_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        name_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        name_tb.ToolTip = level.Name
        # Hebrew/Arabic level names (common — levels are copy/monitored from the
        # architect's link) must resolve RTL so the ellipsis trims the END of the
        # string, not its beginning. Direction comes from the text itself, per row.
        name_tb.FlowDirection = (System.Windows.FlowDirection.RightToLeft
                                 if _is_rtl(level.Name)
                                 else System.Windows.FlowDirection.LeftToRight)

        name_panel.Children.Add(glyph)
        name_panel.Children.Add(name_tb)
        WC.Grid.SetColumn(name_panel, 1)

        # 2 · elevation
        elev_tb = WC.TextBlock()
        elev_tb.Text = u"{:.2f}".format(level.Elevation * FT_TO_M)
        elev_tb.FontFamily = CONSOLAS
        elev_tb.FontSize = 11
        elev_tb.FontWeight = System.Windows.FontWeights.SemiBold
        elev_tb.Foreground = _color(INK_COLOR)
        elev_tb.TextAlignment = System.Windows.TextAlignment.Right
        elev_tb.Margin = _thick(8, 0, 8, 0)
        elev_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(elev_tb, 2)

        # 3 · editable code
        code_box = WC.TextBox()
        code_box.Text = self._code_of(level)
        code_box.Height = 24
        code_box.Margin = _thick(6, 0, 6, 0)
        code_box.Padding = _thick(5, 0, 5, 0)
        code_box.FontFamily = CONSOLAS
        code_box.FontSize = 11
        code_box.FontWeight = System.Windows.FontWeights.Bold
        code_box.Foreground = _color(NAVY_COLOR)
        code_box.VerticalContentAlignment = System.Windows.VerticalAlignment.Center
        code_box.BorderThickness = _thick(1)
        code_box.ToolTip = u"EasyBIM level code — drives every view and sheet name."
        WC.Grid.SetColumn(code_box, 3)

        # 4 · source pill
        pill = WC.Border()
        pill.CornerRadius = System.Windows.CornerRadius(9)
        pill.Padding = _thick(6, 1, 6, 1)
        pill.HorizontalAlignment = System.Windows.HorizontalAlignment.Left
        pill.VerticalAlignment = System.Windows.VerticalAlignment.Center
        pill.Margin = _thick(8, 0, 6, 0)
        pill_tb = WC.TextBlock()
        pill_tb.FontFamily = CONSOLAS
        pill_tb.FontSize = 8.5
        pill_tb.FontWeight = System.Windows.FontWeights.Bold
        pill.Child = pill_tb
        WC.Grid.SetColumn(pill, 4)

        # 5 · live preview of the resulting sheet
        prev_tb = WC.TextBlock()
        prev_tb.FontFamily = CONSOLAS
        prev_tb.FontSize = 10.5
        prev_tb.Margin = _thick(8, 0, 8, 0)
        prev_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        prev_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        WC.Grid.SetColumn(prev_tb, 5)

        # Codes, elevations and sheet numbers are ASCII by construction, so they
        # stay LTR explicitly — never inheriting a row's RTL context.
        for ltr_cell in (elev_tb, code_box, prev_tb, pill_tb):
            ltr_cell.FlowDirection = System.Windows.FlowDirection.LeftToRight

        for child in (cb, name_panel, elev_tb, code_box, pill, prev_tb):
            grid.Children.Add(child)
        outer.Child = grid

        row = {
            u"key": self._key(level),
            u"level": level,
            u"border": outer,
            u"cb": cb,
            u"check": check,
            u"code_box": code_box,
            u"pill": pill,
            u"pill_tb": pill_tb,
            u"preview": prev_tb,
        }

        cb.MouseLeftButtonUp += self._make_toggle(row)
        name_panel.MouseLeftButtonUp += self._make_toggle(row)
        code_box.TextChanged += self._make_code_changed(row)
        return row

    # Handlers come from factories so each row closes over its OWN row dict —
    # a plain loop variable would late-bind to the last row only.
    def _make_toggle(self, row):
        def handler(sender, args):
            self._on[row[u"key"]] = not self._on.get(row[u"key"])
            self._refresh()
        return handler

    def _make_code_changed(self, row):
        def handler(sender, args):
            if self._suppress:
                return
            self._edited[row[u"key"]] = sender.Text
            self._refresh()
        return handler

    # ── refresh ──────────────────────────────────────────────────────────────

    def _refresh(self):
        errs, bad = self._validate()
        sel = self._selected()
        sel_keys = [self._key(l) for l in sel]

        for row in self._rows:
            level = row[u"level"]
            key = row[u"key"]
            on = bool(self._on.get(key))
            code = self._code_of(level)
            src = self._source_of(level)
            flaw = bad.get(key) if on else None

            row[u"border"].Background = _color(ERR_BG) if flaw else WM.Brushes.Transparent
            row[u"border"].Opacity = 1.0 if on else 0.45

            row[u"cb"].Background = _color(CYAN_COLOR) if on else WM.Brushes.White
            row[u"cb"].BorderBrush = _color(CYAN_COLOR) if on else _color(GRAY_COLOR)
            row[u"check"].Visibility = (System.Windows.Visibility.Visible if on
                                        else System.Windows.Visibility.Hidden)

            # Keep the box in sync without re-entering the TextChanged handler.
            if row[u"code_box"].Text != code:
                self._suppress = True
                row[u"code_box"].Text = code
                self._suppress = False

            if flaw:
                row[u"code_box"].BorderBrush = _color(RED_COLOR)
                row[u"code_box"].Foreground = _color(RED_COLOR)
            else:
                row[u"code_box"].BorderBrush = _color(
                    CYAN_COLOR if src == u"edited" else CARD_LINE)
                row[u"code_box"].Foreground = _color(NAVY_COLOR)

            row[u"pill_tb"].Text = src.upper()
            if src == u"stored":
                row[u"pill"].Background = _color(NAVY_PILL)
                row[u"pill_tb"].Foreground = _color(NAVY_COLOR)
            elif src == u"edited":
                row[u"pill"].Background = _color(CYAN_PILL)
                row[u"pill_tb"].Foreground = _color(CYAN_COLOR)
            else:
                row[u"pill"].Background = _color(PILL_BG)
                row[u"pill_tb"].Foreground = _color(MUTED_COLOR)

            if not on:
                row[u"preview"].Text = u"—"
                row[u"preview"].Foreground = _color(MUTED_COLOR)
            elif flaw == u"empty":
                row[u"preview"].Text = u"code required"
                row[u"preview"].Foreground = _color(RED_COLOR)
            elif flaw == u"dup":
                row[u"preview"].Text = u"duplicate code"
                row[u"preview"].Foreground = _color(RED_COLOR)
            else:
                row[u"preview"].Text = u"{} · {}{}".format(
                    SHEET_START + sel_keys.index(key), code.strip(), SHEET_SUFFIX)
                row[u"preview"].Foreground = _color(DIM_COLOR)

        w = self._window
        w.FindName(u"LevelsLabel").Text = \
            u"LEVELS · {} OF {} SELECTED · SORTED BY ELEVATION".format(
                len(sel), len(self._levels))
        all_on = len(self._levels) > 0 and len(sel) == len(self._levels)
        w.FindName(u"ToggleAllBtn").Content = u"Deselect all" if all_on else u"Select all"

        card = w.FindName(u"ErrorsCard")
        panel = w.FindName(u"ErrorsPanel")
        panel.Children.Clear()
        for err in errs:
            line = WC.StackPanel()
            line.Orientation = WC.Orientation.Horizontal
            line.Margin = _thick(0, 1, 0, 1)
            icon = WC.TextBlock()
            icon.Text = u"!"
            icon.FontWeight = System.Windows.FontWeights.Bold
            icon.FontSize = 12
            icon.Foreground = _color(RED_COLOR)
            icon.Margin = _thick(0, 0, 8, 0)
            txt = WC.TextBlock()
            txt.Text = err
            txt.FontSize = 12
            txt.Foreground = _color(BODY_COLOR)
            txt.TextWrapping = System.Windows.TextWrapping.Wrap
            line.Children.Add(icon)
            line.Children.Add(txt)
            panel.Children.Add(line)
        card.Visibility = (System.Windows.Visibility.Visible if errs
                           else System.Windows.Visibility.Collapsed)

        self._refresh_fit()

        n = len(sel)
        w.FindName(u"FooterSummaryTB").Text = \
            u"{} level{} · {} views · sheets {}–{}".format(
                n, u"" if n == 1 else u"s", n * 2,
                SHEET_START, SHEET_START + max(0, n - 1))
        w.FindName(u"RunBtn").IsEnabled = not errs

    def _refresh_fit(self):
        """Warn in the dialog when two stacked plans cannot fit the sheet.

        Advisory only — the spec is explicit that the scale is never changed
        automatically — but it turns 'the plans hang off the sheet' from
        something you discover afterwards into something you read beforehand.
        """
        w = self._window
        banner = w.FindName(u"FitBanner")
        if not self._title_blocks or not self._tpl_sets:
            banner.Visibility = System.Windows.Visibility.Collapsed
            return

        title_block = self._title_blocks[
            max(0, w.FindName(u"TitleBlockCombo").SelectedIndex)]
        extents = self._sel_extents()
        if extents is None:
            banner.Visibility = System.Windows.Visibility.Collapsed
            return
        fit = estimate_fit(extents, self._sel_scale(), title_block)
        if fit is None:
            banner.Visibility = System.Windows.Visibility.Collapsed
            return

        need_w, need_h, have_w, have_h = fit
        if need_w <= have_w and need_h <= have_h:
            banner.Visibility = System.Windows.Visibility.Collapsed
            return

        tighter = u""
        if need_h > have_h and need_h > 0:
            # What scale WOULD fit, rounded up to the next sane drawing scale.
            factor = need_h / have_h
            suggested = self._sel_scale() * factor
            for candidate in (100, 200, 500, 1000):
                if candidate >= suggested:
                    tighter = u" Try 1:{:d}, or a tighter plan extent.".format(candidate)
                    break
        w.FindName(u"FitText").Text = (
            u"At 1:{:g} the plan needs {:.0f} × {:.0f} mm, but this title "
            u"block gives about {:.0f} × {:.0f} mm. The plans will be placed at "
            u"this scale anyway and reported — the scale is never changed "
            u"automatically.{}".format(
                self._sel_scale(), need_w, need_h, have_w, have_h, tighter))
        banner.Visibility = System.Windows.Visibility.Visible

    # ── events ───────────────────────────────────────────────────────────────

    def _on_picker_changed(self, sender, args):
        if self._window is not None and self._rows:
            self._refresh()

    def _on_toggle_all(self, sender, args):
        all_on = len(self._selected()) == len(self._levels)
        for level in self._levels:
            self._on[self._key(level)] = not all_on
        self._refresh()

    def _on_cancel(self, sender, args):
        self.cancelled = True
        self._window.Close()

    def _on_open_sheet(self, sender, args):
        self.open_first = True
        self._window.Close()

    def _on_again(self, sender, args):
        w = self._window
        w.FindName(u"ConfigPanel").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"InfoBanner").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"ResultsPanel").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"RunBtn").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"CancelBtn").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"AgainBtn").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"OpenBtn").Visibility = System.Windows.Visibility.Collapsed

        run_btn = w.FindName(u"RunBtn")
        run_btn.Content = u"▶  Create"
        run_btn.IsEnabled = True

        # Codes just written to the model read back as "stored" from now on.
        for level in self._levels:
            self._stored[self._key(level)] = get_stored_code(level)
        self._edited = {}
        self._refresh()

    # ── progress ─────────────────────────────────────────────────────────────

    def _show_progress(self):
        w = self._window
        w.FindName(u"FooterSummaryTB").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"TickerTB").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"ProgressPanel").Visibility = System.Windows.Visibility.Visible
        w.FindName(u"ProgressFill").Width = 0
        w.FindName(u"ProgressPctTB").Text = u"0%"

        run_btn = w.FindName(u"RunBtn")
        run_btn.IsEnabled = False
        run_btn.Content = u"Working…"
        # A Revit transaction group can't be interrupted mid-run, so Cancel is
        # disabled rather than hidden — hiding a button the user expects reads
        # as a layout bug; disabling it with a tooltip states the constraint.
        cancel_btn = w.FindName(u"CancelBtn")
        cancel_btn.IsEnabled = False
        cancel_btn.ToolTip = (u"Can't cancel once running — each level commits "
                              u"as its own step.")
        self._pump()

    def _hide_progress(self):
        w = self._window
        w.FindName(u"TickerTB").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"ProgressPanel").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"FooterSummaryTB").Visibility = System.Windows.Visibility.Visible
        cancel_btn = w.FindName(u"CancelBtn")
        cancel_btn.IsEnabled = True
        cancel_btn.ToolTip = None

    def _progress(self, fraction, label):
        w = self._window
        w.FindName(u"TickerTB").Text = label
        w.FindName(u"ProgressFill").Width = PROGRESS_BAR_WIDTH * fraction
        w.FindName(u"ProgressPctTB").Text = u"{:.0f}%".format(fraction * 100.0)
        self._pump()

    def _pump(self):
        """Let WPF repaint mid-run.

        The whole batch runs on the Revit API thread inside one dispatcher
        frame, so without draining the queue here the ticker and bar would only
        appear after the run finished. Every button is disabled while this
        happens, so re-entrancy has nothing to act on.
        """
        frame = DispatcherFrame()

        def stop(arg):
            frame.Continue = False
            return None

        Dispatcher.CurrentDispatcher.BeginInvoke(
            DispatcherPriority.Background, DispatcherOperationCallback(stop), None)
        Dispatcher.PushFrame(frame)

    # ── run ──────────────────────────────────────────────────────────────────

    def _on_run(self, sender, args):
        w = self._window
        errs, _ = self._validate()
        if errs:
            return

        title_block = self._title_blocks[max(0, w.FindName(u"TitleBlockCombo").SelectedIndex)]
        fp_vft = self._fp_vft
        cp_vft = self._cp_vft
        tpl_set = self._sel_tpl_set()
        # Copied so run() can resolve a rectangle into it without mutating the
        # option the combo still points at.
        extents = dict(self._sel_extents())   # validated non-None before Create
        work = [(l, self._code_of(l).strip()) for l in self._selected()]

        clean_previous = bool(w.FindName(u"CleanPrevChk").IsChecked)

        run_btn = w.FindName(u"RunBtn")
        self._show_progress()
        try:
            results, self._cleaned = run(
                work, title_block, fp_vft, cp_vft, tpl_set[u"fp"],
                tpl_set[u"cp"], extents, clean_previous=clean_previous,
                progress=self._progress)
        except Exception:
            self._hide_progress()
            run_btn.IsEnabled = True
            run_btn.Content = u"▶  Create"
            forms.alert(u"Level Sheets failed:\n\n{}".format(traceback.format_exc()),
                        title=u"EasyBIM — Error")
            return

        # Fill to 100% and let it land before swapping to the report.
        w.FindName(u"ProgressFill").Width = PROGRESS_BAR_WIDTH
        w.FindName(u"ProgressPctTB").Text = u"100%"
        self._pump()

        self._results = results
        self.cancelled = False
        for r in results:
            if r[u"ok"] and r[u"sheet_id"] is not None:
                self._first_sheet_id = r[u"sheet_id"]
                break
        self._show_report(results)

    # ── report ───────────────────────────────────────────────────────────────

    def _show_report(self, results):
        w = self._window
        made = [r for r in results if r[u"ok"]]
        failed = [r for r in results if not r[u"ok"]]
        warned = len([r for r in made if r[u"warns"]])

        w.FindName(u"ConfigPanel").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"InfoBanner").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"ResultsPanel").Visibility = System.Windows.Visibility.Visible
        self._hide_progress()

        # Header reflects a partial run: warning-toned, not error-toned, because
        # some work did succeed. The happy-path wording is left untouched.
        icon_circle = w.FindName(u"ResIconCircle")
        icon_tb = w.FindName(u"ResIconTB")
        if failed:
            icon_circle.Background = _color(WARN_TINT)
            icon_tb.Text = u"⚠"
            icon_tb.Foreground = _color(AMBER_COLOR)
            w.FindName(u"ResTitleTB").Text = u"{} of {} level sheets created".format(
                len(made), len(results))
        else:
            icon_circle.Background = _color(SUCCESS_BG)
            icon_tb.Text = u"✓"
            icon_tb.Foreground = _color(GREEN_COLOR)
            w.FindName(u"ResTitleTB").Text = u"{} level sheet{} created".format(
                len(made), u"" if len(made) == 1 else u"s")

        summary = u"{} views · one undo step · codes written to {}".format(
            len(made) * 2, CODE_PARAM)
        if failed:
            summary += u" · {} failed".format(len(failed))
        if self._cleaned:
            summary += u" · replaced {} view{} and {} sheet{} from earlier runs".format(
                self._cleaned[0], u"" if self._cleaned[0] == 1 else u"s",
                self._cleaned[1], u"" if self._cleaned[1] == 1 else u"s")
        w.FindName(u"ResSummaryTB").Text = summary

        w.FindName(u"StatLevelsTB").Text = _txt(len(results))
        w.FindName(u"StatSheetsTB").Text = _txt(len(made))
        w.FindName(u"StatViewsTB").Text = _txt(len(made) * 2)
        failed_tb = w.FindName(u"StatFailedTB")
        failed_tb.Text = _txt(len(failed))
        failed_tb.Foreground = _color(RED_COLOR if failed else MUTED_COLOR)
        warn_tb = w.FindName(u"StatWarnTB")
        warn_tb.Text = _txt(warned)
        warn_tb.Foreground = _color(AMBER_COLOR if warned else MUTED_COLOR)

        scale = self._sel_scale()
        w.FindName(u"ReportNoteTB").Text = (
            u"Views that don't fit the title block stay at 1:{:g} — the scale is "
            u"never changed, only reported. Re-running on the same model suffixes "
            u"clashing names and numbers rather than failing.".format(scale))

        self._build_head(u"ReportHeadPanel", REPORT_COLS, REPORT_HEAD)

        # Failures first, elevation order preserved inside each group: a failure
        # needs attention now, not buried at row 14 of 40.
        ordered = failed + made
        rows_panel = w.FindName(u"ReportRowsPanel")
        rows_panel.Children.Clear()
        for i, r in enumerate(ordered):
            rows_panel.Children.Add(
                self._make_report_row(r, i == len(ordered) - 1))
            # Dashed rule marks the failure/success boundary — the fill-colour
            # break already reads, so it needs no label.
            if failed and made and i == len(failed) - 1:
                rows_panel.Children.Add(self._make_divider())

        w.FindName(u"RunBtn").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"CancelBtn").Visibility = System.Windows.Visibility.Collapsed
        w.FindName(u"AgainBtn").Visibility = System.Windows.Visibility.Visible
        open_btn = w.FindName(u"OpenBtn")
        open_btn.Visibility = System.Windows.Visibility.Visible
        open_btn.IsEnabled = self._first_sheet_id is not None

        numbers = [r[u"sheet"] for r in made]
        w.FindName(u"FooterSummaryTB").Text = u"sheets {}".format(
            u"{}–{}".format(numbers[0], numbers[-1]) if numbers else u"—")

    def _make_divider(self):
        """Dashed rule between the failure block and the success block."""
        line = WC.Border()
        line.Height = 1
        line.Margin = _thick(8, 3, 8, 3)
        line.BorderBrush = _color(CARD_LINE)
        line.BorderThickness = _thick(0, 1, 0, 0)
        return line

    def _make_report_row(self, r, is_last):
        outer = WC.Border()
        outer.Padding = _thick(0, 5, 0, 5)
        failed = not r[u"ok"]
        if failed:
            outer.Background = _color(ERR_BG)
        elif r[u"warns"]:
            outer.Background = _color(WARN_BG)
        else:
            outer.Background = WM.Brushes.Transparent
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = _thick(0, 0, 0, 1)

        grid = _fixed_grid(REPORT_COLS)

        if failed:
            # Nothing was created, so name nothing: an em dash rather than a
            # greyed-out preview, which would imply partial success.
            cells = [
                (u"✕", RED_COLOR, True, 1.0),
                (u"—", RED_COLOR, False, 0.6),
                (u"—", RED_COLOR, False, 0.6),
                (u"—", RED_COLOR, False, 0.6),
                (u" · ".join(r[u"warns"]), RED_COLOR, False, 1.0),
            ]
        else:
            warn_color = AMBER_COLOR if r[u"warns"] else MUTED_COLOR
            cells = [
                (r[u"sheet"], NAVY_COLOR, True, 1.0),
                (r[u"sheet_name"], INK_COLOR, True, 1.0),
                (r[u"fp"], DIM_COLOR, False, 1.0),
                (r[u"cp"], DIM_COLOR, False, 1.0),
                (u" · ".join(r[u"warns"]) if r[u"warns"] else u"—", warn_color, False, 1.0),
            ]

        for i, (text, color, bold, opacity) in enumerate(cells):
            tb = WC.TextBlock()
            tb.Text = text
            tb.FontFamily = CONSOLAS
            tb.FontSize = 10.5
            tb.Foreground = _color(color)
            tb.Opacity = opacity
            if bold:
                tb.FontWeight = System.Windows.FontWeights.SemiBold
            tb.Margin = _thick(8, 0, 8, 0)
            tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
            tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
            tb.FlowDirection = System.Windows.FlowDirection.LeftToRight
            tb.ToolTip = u"{} · {}".format(r[u"level"], text)
            WC.Grid.SetColumn(tb, i)
            grid.Children.Add(tb)

        outer.Child = grid
        return outer

    # ── show ─────────────────────────────────────────────────────────────────

    def show(self):
        window = self._build()
        frame = DispatcherFrame()

        def on_closed(s, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        Dispatcher.PushFrame(frame)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    levels = get_levels()
    if not levels:
        forms.alert(u"No levels found in this model.",
                    title=u"EasyBIM", exitscript=True)

    t0 = DB.Transaction(doc, u"EasyBIM: Ensure level code parameter")
    t0.Start()
    try:
        ensure_level_code_param()
        t0.Commit()
    except Exception:
        t0.RollBack()
        forms.alert(
            u"Could not bind the '{}' shared parameter:\n\n{}".format(
                CODE_PARAM, traceback.format_exc()),
            title=u"EasyBIM — Error", exitscript=True)

    title_blocks = get_title_block_types()
    if not title_blocks:
        forms.alert(u"No title block family is loaded in this model. "
                    u"Load one and try again.",
                    title=u"EasyBIM", exitscript=True)

    fp_vfts = get_view_family_types(DB.ViewFamily.FloorPlan)
    cp_vfts = get_view_family_types(DB.ViewFamily.CeilingPlan)
    if not fp_vfts or not cp_vfts:
        forms.alert(u"Could not resolve Floor Plan / Ceiling Plan view family types.",
                    title=u"EasyBIM", exitscript=True)

    # The scope box crops BOTH plans, which is what makes the overlay exact.
    # "Automatic" lets each level resolve its own box by name.
    extent_options = []
    for box in get_scope_boxes():
        extent_options.append({u"kind": u"scopebox", u"id": box.Id,
                               u"label": u"Scope box: {}".format(_elem_name(box))})
    extent_options.append({u"kind": u"auto",
                           u"label": u"Automatic — match the level code, or the only scope box"})

    dlg = LevelSheetsDialog(levels, title_blocks, fp_vfts, cp_vfts,
                            resolve_template_sets(), extent_options)
    try:
        dlg.show()
    except Exception:
        forms.alert(u"Level Sheets failed while building the dialog:\n\n{}".format(
            traceback.format_exc()), title=u"EasyBIM — Error", exitscript=True)

    if dlg.open_first and dlg._first_sheet_id is not None:
        try:
            sheet = doc.GetElement(dlg._first_sheet_id)
            if sheet is not None:
                revit.uidoc.ActiveView = sheet
        except Exception:
            logger.warning(u"Could not open the first sheet.")


main()
