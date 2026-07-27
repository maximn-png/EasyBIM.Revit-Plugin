# -*- coding: utf-8 -*-
"""Head Height Check — EasyBIM MEP Coordination.

Generates head-height CLEARANCE VOLUMES above structural floor top faces so MEP
routing can be clash-checked against them in Navisworks. For every structural
floor inside a chosen scope box, extrudes a DirectShape upward, normal to the
floor's actual top face, sized to a project default or an MEP-Space override.
Wrapped in a TransactionGroup (fully undoable).
"""

__title__ = "Head Height\nCheck"
__author__ = "EasyBIM"
__doc__ = "Build clearance volumes above structural floors for Navisworks clash checking."

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
import System.Collections.Generic as SCG

from System.Windows.Markup import XamlReader
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import revit, script, forms

doc = revit.doc
logger = script.get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CLEARANCE = 2200            # mm, project-wide default
SHAPE_NAME        = u"HeadHeightCheck_Clearance"
WORKSET           = u"+ Mass"
VIEW_3D           = u"EB_3D_9.Mass"
CLEARANCE_PARAM   = u"Required Clearance Height"
SHARED_PARAM_FILE = os.path.join(os.path.dirname(__file__),
                                  u"RequiredClearanceHeight.shared.txt")
SHARED_PARAM_GROUP_NAME = u"EasyBIM_Coordination"

MM_PER_FT          = 304.8
# Vertical margin/height for the horizontal-overlap "shadow column" (see
# _vertical_column_solid): just enough to tolerate a Space's Base
# Offset/Upper Limit not lining up exactly with the floor's top face within
# the SAME story — must stay well under a typical story height (10-13 ft),
# or a zone on one level will incorrectly "reach" the floors of other levels
# above/below it.
COLUMN_MARGIN_FT   = 5.0     # vertical margin (below) for the shadow-column overlap test
COLUMN_HEIGHT_FT   = 10.0    # total height of the shadow column
FULL_OVERLAP_RATIO = 0.98    # >= this fraction of footprint area counts as "full" overlap

# In-plan edge tolerance between a zone outline and a floor edge. If a floor
# only pokes PAST the zone outline by less than this distance, that's treated
# as an edge mismatch (the zone drawn slightly off the floor edge) and the
# whole floor counts as inside the zone — no thin default-height sliver. If a
# floor extends past the outline by MORE than this, it's a genuine partial and
# gets split precisely at the outline. Distance-based (not a % of area), so it
# behaves the same for a tiny or a huge floor, flat or sloped. ~250 mm.
ZONE_EDGE_TOL_FT   = 250.0 / 304.8

# How far a floor's top surface may sit outside a zone Space's own height
# band and still count as "in" that zone. Wide enough to let a ramp climb
# through the zone (and to tolerate the Space's Base Offset not lining up
# exactly with the floor), but MUST stay well under a typical story height so
# a zone on one level never claims floors stacked on the level above/below —
# this is what preserves level separation when the scope box spans several
# storeys (e.g. all basement levels at once).
ZONE_ELEV_TOL_FT   = 4.0

# Above this many triangles, building one vertical-column solid PER TRIANGLE
# (see _vertical_column_solids_from_face) turns a single floor's shadow
# column into tens of thousands of geometry-kernel calls — and every one of
# those pieces then gets boolean-intersected against every zone space
# downstream (_analyze_zones/resolve_height), which is slow enough on its
# own to look like a hang even with no document I/O involved. Confirmed on
# a real project: one floor's face triangulated into ~40,000 pieces.
SHADOW_COLUMN_TRIANGLE_CAP = 500

# Hard cap on the triangulated last-resort in _build_clearance_prism: only
# reached for the rare top face that fails BOTH clean extrusion and
# plane-projected extrusion. Above this, the face is flagged and skipped
# rather than building a pathological pile of prisms (the old failure mode
# that hung/crashed Revit). Sized to allow a genuinely complex single face
# while staying far below the tens-of-thousands that caused trouble.
TRIANGULATION_LAST_RESORT_CAP = 5000

# BuiltInParameterGroup was replaced by GroupTypeId in newer Revit API versions.
try:
    from Autodesk.Revit.DB import BuiltInParameterGroup as _BPG
    _PARAM_GROUP = _BPG.INVALID
except (ImportError, AttributeError):
    from Autodesk.Revit.DB import GroupTypeId as _GTI
    _PARAM_GROUP = next(
        (getattr(_GTI, n) for n in ('Invalid', 'Other', 'General', 'Data')
         if getattr(_GTI, n, None) is not None),
        None
    )


def mm_to_ft(mm):
    return float(mm) / MM_PER_FT


def ft_to_mm(ft):
    return float(ft) * MM_PER_FT


# ─────────────────────────────────────────────────────────────────────────────
# 0. SHARED PARAMETER (bind-on-launch)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_clearance_param():
    """Ensure 'Required Clearance Height' is bound to Spaces (OST_MEPSpaces).

    Adds + binds it from the bundled shared-parameter file if missing, so the
    user is never blocked. Must run inside an open Transaction.
    """
    existing_defn = None
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == CLEARANCE_PARAM:
            existing_defn = it.Key
            break

    cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_MEPSpaces)

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
        defn = grp.Definitions.get_Item(CLEARANCE_PARAM)
        if defn is None:
            raise Exception(u"Definition '{}' not found in shared parameter file.".format(
                CLEARANCE_PARAM))
        cat_set = DB.CategorySet()
        cat_set.Insert(cat)
        doc.ParameterBindings.Insert(defn, DB.InstanceBinding(cat_set), _PARAM_GROUP)
    finally:
        doc.Application.SharedParametersFilename = old_spf


# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA GATHERING
# ─────────────────────────────────────────────────────────────────────────────

def get_scope_boxes():
    """Existing scope boxes in the HOST model (linked ones are never exposed)."""
    return sorted(
        DB.FilteredElementCollector(doc)
          .OfCategory(DB.BuiltInCategory.OST_VolumeOfInterest)
          .WhereElementIsNotElementType()
          .ToElements(),
        key=lambda b: b.Name
    )


def _link_discipline_label(name):
    """Best-effort discipline guess from the link name, for display only.
    Naming conventions vary a lot between projects/firms, so this is never
    used to filter the link list — only to pre-select likely structural
    links and label the rest."""
    cleaned = name.upper()
    for sep in (u"-", u"_", u".", u" "):
        cleaned = cleaned.replace(sep, u" ")
    parts = cleaned.split()
    for p in parts:
        if p in (u"ST", u"STR", u"STRUCT", u"STRUCTURAL"):
            return u"Structural"
        if p in (u"ME", u"MECH", u"HVAC"):
            return u"Mechanical"
        if p in (u"PL", u"PLM", u"PLUMBING"):
            return u"Plumbing"
        if p in (u"EL", u"ELEC", u"ELECTRICAL"):
            return u"Electrical"
        if p in (u"AR", u"ARC", u"ARCH", u"ARCHITECTURAL"):
            return u"Architecture"
    return u"Discipline not detected from name"


def get_available_links():
    """Every Revit link in the host model (loaded + unloaded), any discipline.

    The tool only reads OST_Floors from whichever link(s) the user selects —
    it never filters the list down to "structural-looking" names, since that
    naming heuristic varies per project. Links that look structural by name
    are pre-checked as a convenience; every other link is still listed and
    selectable, so a differently-named or alternate/cleaned-up model can be
    used instead."""
    out = []
    for li in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
        name = li.Name
        disc = _link_discipline_label(name)
        link_doc = li.GetLinkDocument()
        out.append({
            u"instance": li,
            u"name": name,
            u"disc": disc,
            u"loaded": link_doc is not None,
            u"doc": link_doc,
            u"on": (disc == u"Structural") and (link_doc is not None),
        })
    return sorted(out, key=lambda l: l[u"name"])


def _bbox_intersects_xy(a, b):
    """Horizontal-only bbox overlap. A Space's vertical extent (its Level,
    Base Offset and Upper Limit) essentially never lines up with a floor's
    actual elevation or a scope box's Z range, and the rest of this tool's
    height-resolution logic (the vertical "shadow column" technique) already
    ignores Z entirely for exactly that reason — so this pre-filter must too,
    or a perfectly valid Space gets silently dropped before it's ever
    considered."""
    return not (a.Max.X < b.Min.X or a.Min.X > b.Max.X or
                a.Max.Y < b.Min.Y or a.Min.Y > b.Max.Y)


def get_clearance_spaces_in_scope(scope_box):
    """MEP Spaces carrying a CLEARANCE_PARAM override whose footprint (in X/Y
    only — see _bbox_intersects_xy) overlaps the scope box. Returns a list of
    (space, height_mm) tuples."""
    scope_bbox = scope_box.get_BoundingBox(None)
    if scope_bbox is None:
        return []
    out = []
    spaces = (DB.FilteredElementCollector(doc)
              .OfCategory(DB.BuiltInCategory.OST_MEPSpaces)
              .WhereElementIsNotElementType())
    for sp in spaces:
        p = sp.LookupParameter(CLEARANCE_PARAM)
        if not (p and p.HasValue and p.AsDouble() > 0):
            continue
        sp_bbox = sp.get_BoundingBox(None)
        if sp_bbox is None or not _bbox_intersects_xy(scope_bbox, sp_bbox):
            continue
        out.append((sp, ft_to_mm(p.AsDouble())))
    return out


def _space_label(sp):
    try:
        name_p = sp.LookupParameter(u"Name")
        num_p = sp.LookupParameter(u"Number")
        name = name_p.AsString() if name_p and name_p.HasValue else None
        num = num_p.AsString() if num_p and num_p.HasValue else None
        if name and num:
            return u"{} — {}".format(num, name)
        return name or num or u"Space {}".format(sp.Id.IntegerValue)
    except Exception:
        return u"Space {}".format(sp.Id.IntegerValue)


def _floor_label(floor):
    try:
        mark = floor.LookupParameter(u"Mark")
        if mark and mark.HasValue:
            v = mark.AsString()
            if v:
                return v
    except Exception:
        pass
    return u"Floor {}".format(floor.Id.IntegerValue)


def _scope_extent_label(box):
    bbox = box.get_BoundingBox(None)
    if bbox is None:
        return u""
    w_m = abs(bbox.Max.X - bbox.Min.X) * 0.3048
    d_m = abs(bbox.Max.Y - bbox.Min.Y) * 0.3048
    return u"{:.0f} × {:.0f} m".format(w_m, d_m)


# ─────────────────────────────────────────────────────────────────────────────
# 2. GEOMETRY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def collect_structural_floors(link_doc, link_transform, scope_bbox):
    """Structural floors (OST_Floors) in link_doc whose bbox intersects the host
    scope box. Includes the ENTIRE floor if it intersects — no clipping."""
    try:
        inv = link_transform.Inverse
    except Exception:
        return []

    ct = scope_bbox.Transform
    mn, mx = scope_bbox.Min, scope_bbox.Max
    world_corners = [
        ct.OfPoint(DB.XYZ(x, y, z))
        for x in (mn.X, mx.X)
        for y in (mn.Y, mx.Y)
        for z in (mn.Z, mx.Z)
    ]
    lc = [inv.OfPoint(wc) for wc in world_corners]
    outline = DB.Outline(
        DB.XYZ(min(c.X for c in lc), min(c.Y for c in lc), min(c.Z for c in lc)),
        DB.XYZ(max(c.X for c in lc), max(c.Y for c in lc), max(c.Z for c in lc)),
    )
    bbox_filter = DB.BoundingBoxIntersectsFilter(outline)
    return list(
        DB.FilteredElementCollector(link_doc)
          .OfCategory(DB.BuiltInCategory.OST_Floors)
          .WherePasses(bbox_filter)
          .WhereElementIsNotElementType()
          .ToElements()
    )


def _iter_solids(geom_element):
    """Yield every Solid with positive volume, recursing into GeometryInstance
    wrappers. NOTE: this used to also accept Volume<=0 Solids that still had
    Faces (meant to rescue floors whose B-rep never closed to a valid
    volume) — reverted. The diagnostic that motivated it proved those
    specific floors have Faces.Size == 0 too, so the relaxation never helped
    them; it only let through OTHER Volume<=0-but-faced Solids elsewhere in
    a structural link sourced from an import/conversion pipeline — exactly
    the kind of non-manifold, numerically-degenerate geometry that can send
    Revit's geometry kernel (Triangulate/CreateExtrusionGeometry/Boolean
    ops) into an extremely long or effectively-hung computation. Not worth
    the risk for zero proven benefit."""
    for g in geom_element:
        if isinstance(g, DB.Solid):
            if g.Volume > 0:
                yield g
        elif isinstance(g, DB.GeometryInstance):
            for s in _iter_solids(g.GetInstanceGeometry()):
                yield s


def _face_area_weighted_normal_z(face, link_transform):
    """Area-weighted average of a face's normal Z component in host space,
    via triangulation — used as a fallback in extract_top_faces when the
    single-UV-midpoint sample doesn't read as upward. A genuinely non-planar
    top face (e.g. a floor with drainage slopes edited via sub-element point
    elevations — common on real parking-level slabs) can have a normal that
    varies enough across its surface that one unlucky sample point looks
    flat or even downward, even though the face is overwhelmingly
    upward-facing. Returns None if the face can't be triangulated at all."""
    try:
        mesh = face.Triangulate(1.0)
    except Exception:
        mesh = None
    if mesh is None or mesh.NumTriangles == 0:
        return None
    total_weight = 0.0
    weighted_z = 0.0
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = link_transform.OfPoint(tri.get_Vertex(0))
            p1 = link_transform.OfPoint(tri.get_Vertex(1))
            p2 = link_transform.OfPoint(tri.get_Vertex(2))
            cross = (p1 - p0).CrossProduct(p2 - p0)
            weight = cross.GetLength()  # twice the triangle's area
            if weight < 1e-12:
                continue
            total_weight += weight
            weighted_z += cross.Z
        except Exception:
            continue
    if total_weight <= 0:
        return None
    return weighted_z / total_weight


def extract_top_faces(floor, link_transform):
    """Return a list of (face, host_space_normal) for every upward-facing face
    on the floor's solid(s) — i.e. every face whose normal (transformed into
    host space) points up. A single floor's walking surface (a ramp, a
    stepped slab, a slab with a landing) is very often split by Revit into
    more than one planar face; returning only the single largest one silently
    drops the rest of the surface, which is why ramps previously came out
    only half-covered."""
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    geom = floor.get_Geometry(opt)
    if geom is None:
        return []

    out = []
    for solid in _iter_solids(geom):
        for face in solid.Faces:
            bbox_uv = face.GetBoundingBox()
            mid_uv = DB.UV((bbox_uv.Min.U + bbox_uv.Max.U) / 2.0,
                            (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0)
            normal_host = None
            try:
                normal_local = face.ComputeNormal(mid_uv)
                normal_host = link_transform.OfVector(normal_local).Normalize()
            except Exception:
                pass
            if normal_host is None or normal_host.Z <= 0.05:
                avg_z = _face_area_weighted_normal_z(face, link_transform)
                if avg_z is None or avg_z <= 0.05:
                    continue
                normal_host = DB.XYZ.BasisZ
            out.append((face, normal_host))
    return out


def _transform_curve_loop(curve_loop, transform):
    """CurveLoop itself has no CreateTransformed — transform each Curve and
    rebuild the loop."""
    new_loop = DB.CurveLoop()
    for curve in curve_loop:
        new_loop.Append(curve.CreateTransformed(transform))
    return new_loop


def face_boundary_loops_in_host_space(face, link_transform):
    loops = list(face.GetEdgesAsCurveLoops())
    return [_transform_curve_loop(cl, link_transform) for cl in loops]


def _mesh_to_prisms(mesh, link_transform, height_ft):
    """Extrude every triangle of `mesh` (in link space) straight up (+Z) by
    height_ft. Vertical extrusion (not along the triangle's own normal) is
    what gives a uniform VERTICAL head-height clearance above a sloped/warped
    surface — each triangle's bottom follows the ramp and its top sits exactly
    height_ft plumb above it, so there are no per-facet height steps and the
    clearance isn't foreshortened on the slope. The triangle winding is
    oriented so its normal points up, matching the +Z extrusion."""
    solids = []
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = link_transform.OfPoint(tri.get_Vertex(0))
            p1 = link_transform.OfPoint(tri.get_Vertex(1))
            p2 = link_transform.OfPoint(tri.get_Vertex(2))
            normal = (p1 - p0).CrossProduct(p2 - p0)
            if normal.GetLength() < 1e-9:
                continue
            if normal.Z < 0:      # keep the loop CCW seen from above
                p1, p2 = p2, p1
            loop = DB.CurveLoop()
            loop.Append(DB.Line.CreateBound(p0, p1))
            loop.Append(DB.Line.CreateBound(p1, p2))
            loop.Append(DB.Line.CreateBound(p2, p0))
            solids.append(DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                SCG.List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, height_ft))
        except Exception:
            continue
    return solids


def _choose_build_mesh(face):
    """Pick the COARSEST triangulation of `face` whose triangle count is
    within TRIANGULATION_LAST_RESORT_CAP — the fewest facets that still
    represent a warped surface, so the resulting clearance mass is buildable
    (not tens of thousands of prisms) and follows the ramp's curve. Sweeps
    level-of-detail from coarse to fine and takes the first usable mesh.
    Returns (mesh, count); mesh is None if even the coarsest is over the cap
    (face flagged & skipped) or nothing tessellates."""
    for lod in (0.0, 0.25, 0.6, 1.0):
        try:
            mesh = face.Triangulate(lod)
        except Exception:
            mesh = None
        if mesh is None:
            continue
        n = mesh.NumTriangles
        if n == 0:
            continue
        if n <= TRIANGULATION_LAST_RESORT_CAP:
            return mesh, n
        # The coarsest LOD already exceeds the cap; finer only adds triangles.
        return None, n
    return None, 0


def _face_best_fit_plane(face, link_transform):
    """Area-weighted best-fit plane (origin, unit normal) for a face in host
    space, via its triangulation. Works for planar faces (exact) and mildly
    non-planar ones (import noise) alike — the basis for projecting a face's
    edge loops onto a single flat plane so they extrude cleanly. Returns None
    if the face can't be triangulated."""
    try:
        mesh = face.Triangulate(1.0)
    except Exception:
        return None
    if mesh is None or mesh.NumTriangles == 0:
        return None
    nx = ny = nz = 0.0
    cx = cy = cz = 0.0
    cw = 0.0
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = link_transform.OfPoint(tri.get_Vertex(0))
            p1 = link_transform.OfPoint(tri.get_Vertex(1))
            p2 = link_transform.OfPoint(tri.get_Vertex(2))
            cross = (p1 - p0).CrossProduct(p2 - p0)
            w = cross.GetLength()
            if w < 1e-12:
                continue
            nx += cross.X
            ny += cross.Y
            nz += cross.Z
            cx += (p0.X + p1.X + p2.X) / 3.0 * w
            cy += (p0.Y + p1.Y + p2.Y) / 3.0 * w
            cz += (p0.Z + p1.Z + p2.Z) / 3.0 * w
            cw += w
        except Exception:
            continue
    if cw <= 0:
        return None
    normal = DB.XYZ(nx, ny, nz)
    if normal.GetLength() < 1e-12:
        return None
    return (DB.XYZ(cx / cw, cy / cw, cz / cw), normal.Normalize())


def _project_loops_to_plane(loops, origin, normal):
    """Project each loop's tessellated vertices onto the plane (origin,
    normal) and rebuild them as polyline CurveLoops that lie exactly on that
    plane — so CreateExtrusionGeometry accepts them even when the original
    face was slightly non-planar. Arcs become short line segments; fine for a
    clearance volume (the plan outline is what matters, not curve fidelity)."""
    out = []
    for loop in loops:
        pts = []
        for curve in loop:
            try:
                tess = curve.Tessellate()
            except Exception:
                continue
            for p in tess:
                d = (p - origin).DotProduct(normal)
                pp = DB.XYZ(p.X - normal.X * d, p.Y - normal.Y * d, p.Z - normal.Z * d)
                if not pts or pts[-1].DistanceTo(pp) > 1e-6:
                    pts.append(pp)
        if len(pts) > 1 and pts[0].DistanceTo(pts[-1]) <= 1e-6:
            pts.pop()
        if len(pts) < 3:
            continue
        cl = DB.CurveLoop()
        ok = True
        for i in range(len(pts)):
            a = pts[i]
            b = pts[(i + 1) % len(pts)]
            if a.DistanceTo(b) <= 1e-7:
                continue
            try:
                cl.Append(DB.Line.CreateBound(a, b))
            except Exception:
                ok = False
                break
        if ok:
            out.append(cl)
    return out


def _space_boundary_loops(space):
    opts = DB.SpatialElementBoundaryOptions()
    loops = []
    try:
        segments = space.GetBoundarySegments(opts)
    except Exception:
        return loops
    for loop in segments:
        cl = DB.CurveLoop()
        for seg in loop:
            try:
                cl.Append(seg.GetCurve())
            except Exception:
                pass
        try:
            if not cl.IsOpen():
                loops.append(cl)
        except Exception:
            pass
    return loops


def _vertical_column_solid(loops, margin_ft=COLUMN_MARGIN_FT, height_ft=COLUMN_HEIGHT_FT):
    """Extrude loops (translated down by margin_ft, then swept vertically by
    height_ft) into a tall vertical prism. Used purely to test horizontal
    footprint overlap between two loops regardless of their actual Z
    elevation (a floor's top face vs. a Space's own base elevation)."""
    if not loops:
        return None
    down = DB.Transform.CreateTranslation(DB.XYZ(0, 0, -margin_ft))
    try:
        shifted = [_transform_curve_loop(cl, down) for cl in loops]
        return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            SCG.List[DB.CurveLoop](shifted), DB.XYZ.BasisZ, height_ft)
    except Exception:
        return None


def _space_shadow_column(sp, loops):
    """Zone shadow column for a Space, built from its OWN real vertical
    extent (bounding box — reflecting its actual Level/Base Offset through
    Upper Limit/Limit Offset) instead of a generic fixed margin around one Z
    value. A properly configured Space already spans its whole story height,
    so this naturally and reliably covers any reasonable clearance height
    above the floor without needing to guess a tolerance — and it stays
    confined to roughly one story, since that's genuinely how tall the Space
    is, avoiding the "reaches other floors" problem a large fixed margin has.
    Falls back to the generic margin-based column if the bbox is unusable."""
    if not loops:
        return None
    bbox = sp.get_BoundingBox(None)
    if bbox is None or bbox.Max.Z <= bbox.Min.Z:
        return _vertical_column_solid(loops)
    tol_ft = 1.0
    try:
        loop_z = next(iter(loops[0])).GetEndPoint(0).Z
    except Exception:
        return _vertical_column_solid(loops)
    bottom_z = bbox.Min.Z - tol_ft
    total_height = (bbox.Max.Z - bbox.Min.Z) + 2 * tol_ft
    shift = DB.Transform.CreateTranslation(DB.XYZ(0, 0, bottom_z - loop_z))
    try:
        shifted = [_transform_curve_loop(cl, shift) for cl in loops]
        return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            SCG.List[DB.CurveLoop](shifted), DB.XYZ.BasisZ, total_height)
    except Exception:
        return _vertical_column_solid(loops)


# Vertical half-span of the zone CLIP column (see _column_from_zone_polys /
# _boolean_clip_prism) — large enough that the column fully contains any
# clearance prism it clips and shares no face with it, so the clip only ever
# cuts in plan, never in height, and avoids coincident-face boolean failures.
ZONE_CLIP_HALF_FT = 500.0


def _vertical_column_bbox_solid(mesh, link_transform, margin_ft, height_ft):
    """Cheap conservative fallback for a face whose triangulation is too
    dense to build one column per triangle (see SHADOW_COLUMN_TRIANGLE_CAP):
    a single rectangular column from the mesh's own X/Y bounding box. This
    over-approximates the real footprint (a rectangle instead of the exact
    shape), but that's fine for the "is there roughly an overlap" test this
    feeds — it never needed triangle-level precision, unlike the real
    clearance geometry."""
    xs, ys = [], []
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            for j in range(3):
                p = link_transform.OfPoint(tri.get_Vertex(j))
                xs.append(p.X)
                ys.append(p.Y)
        except Exception:
            continue
    if not xs:
        return []
    z = -margin_ft
    corners = [DB.XYZ(min(xs), min(ys), z), DB.XYZ(max(xs), min(ys), z),
               DB.XYZ(max(xs), max(ys), z), DB.XYZ(min(xs), max(ys), z)]
    try:
        loop = DB.CurveLoop()
        for k in range(4):
            loop.Append(DB.Line.CreateBound(corners[k], corners[(k + 1) % 4]))
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            SCG.List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, height_ft)
        return [solid]
    except Exception:
        return []


def _vertical_column_solids_from_face(face, link_transform, margin_ft, height_ft):
    """Fallback for _vertical_column_solid when the edge-loop version fails
    (the same fragile-geometry cases that can break the real clearance
    extrusion). Triangulates the face and builds one small vertical column
    per triangle instead of one clean solid for the whole face — always
    valid, since three points are always coplanar. Without this, a floor
    with imperfect boundary geometry silently looks like it has "no
    override Space" overlap (falls back to the default height with no
    warning) even though its clearance shape itself generates fine via the
    same triangulation fallback."""
    solids = []
    try:
        mesh = face.Triangulate(1.0)  # finest level of detail — minimizes real
        # gaps between triangles on large/complex faces (a coarse default
        # mesh can leave small uncovered slivers even though the combined
        # bounding box spans the whole face correctly)
    except Exception:
        mesh = None
    if mesh is None:
        return solids
    if mesh.NumTriangles > SHADOW_COLUMN_TRIANGLE_CAP:
        return _vertical_column_bbox_solid(mesh, link_transform, margin_ft, height_ft)
    down = DB.Transform.CreateTranslation(DB.XYZ(0, 0, -margin_ft))
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = down.OfPoint(link_transform.OfPoint(tri.get_Vertex(0)))
            p1 = down.OfPoint(link_transform.OfPoint(tri.get_Vertex(1)))
            p2 = down.OfPoint(link_transform.OfPoint(tri.get_Vertex(2)))
            loop = DB.CurveLoop()
            loop.Append(DB.Line.CreateBound(p0, p1))
            loop.Append(DB.Line.CreateBound(p1, p2))
            loop.Append(DB.Line.CreateBound(p2, p0))
            solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                SCG.List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, height_ft)
            solids.append(solid)
        except Exception:
            continue
    return solids


def _footprint_shadow_columns(loops, face, link_transform, margin_ft=COLUMN_MARGIN_FT, height_ft=COLUMN_HEIGHT_FT):
    """Robust version of _vertical_column_solid for a floor footprint. Tiers,
    cheapest/cleanest first — normally returns exactly one clean column:
      1. Extrude the loops vertically as-is (exact).
      2. If that fails (slightly non-planar imported loops), FLATTEN the loops
         onto a horizontal plane and extrude vertically — one clean plan
         footprint, no per-triangle explosion.
      3. Only if both fail, the per-triangle method (bbox-capped inside
         _vertical_column_solids_from_face) as a last resort.

    `margin_ft`/`height_ft` control how far this reaches below/above the
    floor's own top face — callers doing the real height-resolution (see
    resolve_height) should size `height_ft` to comfortably cover whatever
    clearance heights are actually in play, so this "is there overlap" test
    can never disagree with what the real boolean clip later finds."""
    col = _vertical_column_solid(loops, margin_ft, height_ft)
    if col is not None and col.Volume > 0:
        return [col]
    # Tier 2: collapse the loops to their PLAN footprint and extrude a column
    # spanning the loops' full Z extent (plus the usual margin below and
    # clearance reach above). Spanning the real Z extent is what lets a WARPED
    # face — e.g. a curved ramp that climbs many feet within one "face" — be
    # tested against the zone at every elevation it passes through, instead of
    # a fixed-height column stuck at one arbitrary elevation (which missed the
    # zone entirely for the rising part of a ramp). For a flat face zmin≈zmax,
    # so this reduces to the same column as before. Also yields one clean
    # column instead of thousands of per-triangle prisms.
    if loops:
        zs = []
        for cl in loops:
            for curve in cl:
                try:
                    for p in curve.Tessellate():
                        zs.append(p.Z)
                except Exception:
                    pass
        if zs:
            zmin, zmax = min(zs), max(zs)
            base_z = zmin - margin_ft
            total_h = (zmax - zmin) + margin_ft + height_ft
            flat = _project_loops_to_plane(loops, DB.XYZ(0, 0, base_z), DB.XYZ.BasisZ)
            if flat:
                try:
                    s = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                        SCG.List[DB.CurveLoop](flat), DB.XYZ.BasisZ, total_h)
                    if s is not None and s.Volume > 0:
                        return [s]
                except Exception:
                    pass
    if face is None or link_transform is None:
        return []
    return [s for s in _vertical_column_solids_from_face(face, link_transform, margin_ft, height_ft)
            if s.Volume > 0]


def _union_solids(solids):
    """Boolean-union a list of solids into one. Falls back to keeping the
    running result as-is if a particular union fails (e.g. numerically
    degenerate overlap) rather than losing everything."""
    result = None
    for s in solids:
        if result is None:
            result = s
            continue
        try:
            merged = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                result, s, DB.BooleanOperationsType.Union)
            if merged is not None:
                result = merged
        except Exception:
            pass
    return result


def _log_no_coverage_gap(f):
    """Loud, unmistakable warning for the rare case where NONE of a
    fragment's assignments produced any shape at all — a genuine coverage
    gap (no clearance mass whatsoever at that spot), as opposed to the
    routine fallback cases in create_clearance_shape that still end up
    producing something. Should almost never fire; if it does, it pinpoints
    exactly where to look instead of hunting through coordinates."""
    try:
        pts = []
        for cl in f[u"loops"]:
            for curve in cl:
                pts.append(curve.GetEndPoint(0))
        xs = [p.X for p in pts]
        ys = [p.Y for p in pts]
        bbox_line = u"X=[{:.2f}, {:.2f}] Y=[{:.2f}, {:.2f}] Z~{:.2f}ft".format(
            min(xs), max(xs), min(ys), max(ys), pts[0].Z)
    except Exception:
        bbox_line = u"bbox unavailable"
    logger.warning(
        u"*** COVERAGE GAP *** {}: no clearance shape at all could be built for this "
        u"fragment (every assignment failed, including fallbacks) — {}".format(f[u"label"], bbox_line))


def _loops_z_range(loops):
    """(min Z, max Z) across a list of CurveLoops via tessellation, or None."""
    zs = []
    for cl in loops or []:
        for curve in cl:
            try:
                for p in curve.Tessellate():
                    zs.append(p.Z)
            except Exception:
                pass
    if not zs:
        return None
    return (min(zs), max(zs))


# ── 2D plan-geometry helpers ──────────────────────────────────────────────
# The zone/floor "is this area under the zone?" question is fundamentally 2D.
# Answering it with plain point-in-polygon math (below) instead of Revit
# boolean SOLID operations is the key robustness change: boolean ops on real
# imported geometry fail intermittently, which was the recurring source of
# wrong zone results. These are pure arithmetic and cannot fail that way.

def _space_plan_polygon(sp):
    """Zone outline as a plan polygon: a list of rings, each a list of (x, y)
    host-space points; ring[0] is the outer boundary, any others are holes.
    Returns None if no usable boundary."""
    loops = _space_boundary_loops(sp)
    rings = []
    for cl in loops:
        pts = []
        for curve in cl:
            try:
                tess = curve.Tessellate()
            except Exception:
                continue
            for p in tess:
                if not pts or abs(pts[-1][0] - p.X) > 1e-7 or abs(pts[-1][1] - p.Y) > 1e-7:
                    pts.append((p.X, p.Y))
        if len(pts) >= 3:
            if abs(pts[0][0] - pts[-1][0]) <= 1e-7 and abs(pts[0][1] - pts[-1][1]) <= 1e-7:
                pts.pop()
            rings.append(pts)
    return rings or None


def _point_in_ring(x, y, ring):
    """Ray-cast point-in-polygon for a single ring of (x, y) points."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(x, y, rings):
    """Inside the outer ring and outside every hole ring."""
    if not rings or not _point_in_ring(x, y, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(x, y, hole):
            return False
    return True


def _point_seg_dist2(x, y, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return (x - x1) ** 2 + (y - y1) ** 2
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    px = x1 + t * dx
    py = y1 + t * dy
    return (x - px) ** 2 + (y - py) ** 2


def _point_in_zone(x, y, zone_polys, tol):
    """True if (x, y) is inside ANY zone polygon, OR within `tol` of any zone
    boundary edge (the distance-based edge tolerance). zone_polys is a list of
    polygons, each a list of rings."""
    for rings in zone_polys:
        if _point_in_polygon(x, y, rings):
            return True
    tol2 = tol * tol
    for rings in zone_polys:
        for ring in rings:
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]
                x2, y2 = ring[(i + 1) % n]
                if _point_seg_dist2(x, y, x1, y1, x2, y2) <= tol2:
                    return True
    return False


def _face_plan_samples(face, link_transform):
    """List of (centroid_x, centroid_y, plan_area) for each triangle of the
    face, in host XY. The centroids are the sample points classified against
    the zone polygon; the plan areas weight them so the in/out fraction is by
    real area, not triangle count. Uses the coarse, piece-capped mesh (same as
    the builder) — a few thousand samples is ample for an in/out fraction and
    avoids point-testing tens of thousands of finest-LOD triangles."""
    mesh, _n = _choose_build_mesh(face)
    out = []
    if mesh is None:
        return out
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = link_transform.OfPoint(tri.get_Vertex(0))
            p1 = link_transform.OfPoint(tri.get_Vertex(1))
            p2 = link_transform.OfPoint(tri.get_Vertex(2))
            area = abs((p1.X - p0.X) * (p2.Y - p0.Y) - (p2.X - p0.X) * (p1.Y - p0.Y)) / 2.0
            if area <= 0:
                continue
            out.append(((p0.X + p1.X + p2.X) / 3.0, (p0.Y + p1.Y + p2.Y) / 3.0, area))
        except Exception:
            continue
    return out


def _zone_cached(sp, cache):
    """(plan polygon, (zmin,zmax) band or None) for a Space, cached by id."""
    if cache is not None and sp.Id in cache:
        return cache[sp.Id]
    poly = _space_plan_polygon(sp)
    bb = sp.get_BoundingBox(None)
    band = ((bb.Min.Z, bb.Max.Z) if (bb is not None and bb.Max.Z > bb.Min.Z) else None)
    entry = (poly, band)
    if cache is not None:
        cache[sp.Id] = entry
    return entry


def _build_face_prisms_classified(face, link_transform, height_mm, zone_polys, keep_inside):
    """Build vertical clearance prisms for only the triangles of `face` whose
    plan centroid is (keep_inside == inside the zone). Pure 2D point-in-zone
    classification — no boolean solids — so a floor that genuinely straddles a
    zone edge is split robustly (faceted at the triangle boundary)."""
    mesh, n = _choose_build_mesh(face)
    if mesh is None:
        return []
    height_ft = mm_to_ft(height_mm)
    solids = []
    for i in range(mesh.NumTriangles):
        try:
            tri = mesh.get_Triangle(i)
            p0 = link_transform.OfPoint(tri.get_Vertex(0))
            p1 = link_transform.OfPoint(tri.get_Vertex(1))
            p2 = link_transform.OfPoint(tri.get_Vertex(2))
            cx = (p0.X + p1.X + p2.X) / 3.0
            cy = (p0.Y + p1.Y + p2.Y) / 3.0
            if _point_in_zone(cx, cy, zone_polys, ZONE_EDGE_TOL_FT) != keep_inside:
                continue
            normal = (p1 - p0).CrossProduct(p2 - p0)
            if normal.GetLength() < 1e-9:
                continue
            if normal.Z < 0:
                p1, p2 = p2, p1
            loop = DB.CurveLoop()
            loop.Append(DB.Line.CreateBound(p0, p1))
            loop.Append(DB.Line.CreateBound(p1, p2))
            loop.Append(DB.Line.CreateBound(p2, p0))
            solids.append(DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                SCG.List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, height_ft))
        except Exception:
            continue
    return solids


def _column_from_zone_polys(zone_polys, base_z, height_ft):
    """A tall vertical solid built from the zone's plan polygon(s) at base_z —
    used to boolean-clip a clean planar clearance prism. Built at a Z well
    below the prism and much taller than it, so it shares NO coincident face
    with the prism (coincident faces were what made earlier boolean tests
    fail); it therefore only ever cuts the prism in plan."""
    solids = []
    for rings in zone_polys:
        if not rings:
            continue
        loops = []
        for ring in rings:
            cl = DB.CurveLoop()
            ok = False
            n = len(ring)
            for i in range(n):
                a = DB.XYZ(ring[i][0], ring[i][1], base_z)
                b = DB.XYZ(ring[(i + 1) % n][0], ring[(i + 1) % n][1], base_z)
                if a.DistanceTo(b) < 1e-7:
                    continue
                try:
                    cl.Append(DB.Line.CreateBound(a, b))
                    ok = True
                except Exception:
                    ok = False
                    break
            if ok:
                try:
                    if not cl.IsOpen():
                        loops.append(cl)
                except Exception:
                    pass
        if not loops:
            continue
        for attempt in (loops, loops[:1]):   # full (outer+holes), then outer-only
            try:
                s = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    SCG.List[DB.CurveLoop](attempt), DB.XYZ.BasisZ, height_ft)
                if s is not None and s.Volume > 0:
                    solids.append(s)
                    break
            except Exception:
                continue
    return _union_solids(solids) if solids else None


def _boolean_clip_prism(base_solids, footprint_loops, zone_polys, keep_inside):
    """Clip a clean planar clearance prism against the zone outline with a
    single Revit boolean, giving a straight cut that follows the zone outline
    (used for genuinely-partial FLAT floors — reliable here because the tall
    zone column shares no face with the prism). Returns a list of clipped
    solids on success (possibly [] = nothing on this side, valid), or None if
    the column couldn't be built or the boolean threw — signalling the caller
    to fall back to the robust per-triangle 2D method."""
    zr = _loops_z_range(footprint_loops)
    base_z = (zr[0] if zr else 0.0) - ZONE_CLIP_HALF_FT
    col = _column_from_zone_polys(zone_polys, base_z, 2.0 * ZONE_CLIP_HALF_FT)
    if col is None:
        return None
    op = (DB.BooleanOperationsType.Intersect if keep_inside
          else DB.BooleanOperationsType.Difference)
    out = []
    for s in base_solids:
        try:
            r = DB.BooleanOperationsUtils.ExecuteBooleanOperation(s, col, op)
        except Exception:
            return None
        if r is not None and r.Volume > 1e-6:
            out.append(r)
    return out


def resolve_height(footprint_loops, candidate_spaces, default_mm, face=None, link_transform=None,
                    space_col_cache=None, label=None):
    """candidate_spaces: list of (space_element, height_mm) — the zones the
    caller has applied. `face`/`link_transform` provide the floor surface
    that gets sampled against the zone outlines.

    `space_col_cache`: optional dict kept alive across the per-floor loop so
    each Space's plan polygon + height band are built once (keyed by id).

    The zone question is answered with pure 2D geometry (point-in-polygon on
    sampled triangle centroids) — NO boolean solid operations — because those
    fail intermittently on real imported geometry and were the recurring
    source of wrong results.

    Returns (assignments, conflict). Each assignment is a dict:
      {"height_mm", "is_partial", "zone_polys", "keep_inside"}
    A "whole" assignment has zone_polys=None (build the entire face at that
    height, cleanly). A "partial" assignment carries the zone polygon(s) and
    keep_inside True/False, so create_clearance_shape keeps only the triangles
    on that side of the zone outline.

      - not under any zone            -> [{default_mm, whole}]
      - fully under one zone          -> [{zone_height, whole}]
      - straddling one zone's edge    -> [{zone_height, inside}, {default_mm, outside}]
      - under two DIFFERENT-height zones -> ([], True)  (conflict, don't guess)
    """
    def _plain(height_mm):
        return [{u"height_mm": height_mm, u"is_partial": False,
                 u"zone_polys": None, u"keep_inside": None}]

    if not candidate_spaces or face is None or link_transform is None:
        return _plain(default_mm), False

    samples = _face_plan_samples(face, link_transform)
    total_area = sum(a for _, _, a in samples)
    if total_area <= 0:
        return _plain(default_mm), False
    fzr = _loops_z_range(footprint_loops)
    fmin, fmax = (fzr if fzr else (None, None))

    # Spaces that pass the ELEVATION GATE (floor's own surface within the
    # Space's height band ± tol). This keeps a zone on one level off floors
    # stacked on other levels when the scope box spans several storeys.
    polys_by_height = {}   # height_mm -> list of zone polygons (each a list of rings)
    for sp, sp_height_mm in candidate_spaces:
        poly, band = _zone_cached(sp, space_col_cache)
        if poly is None:
            continue
        if band is not None and fmin is not None and fmax is not None:
            if fmin > band[1] + ZONE_ELEV_TOL_FT or fmax < band[0] - ZONE_ELEV_TOL_FT:
                continue
        polys_by_height.setdefault(sp_height_mm, []).append(poly)

    if not polys_by_height:
        return _plain(default_mm), False

    # Classify each sampled triangle centroid (2D point-in-zone, with the
    # distance-based edge tolerance). Area-weighted so the in/out fraction is
    # by real area, not triangle count.
    height_area = {}       # height_mm -> in-zone area
    contested_area = 0.0
    for (cx, cy, a) in samples:
        hits = [h for h in polys_by_height
                if _point_in_zone(cx, cy, polys_by_height[h], ZONE_EDGE_TOL_FT)]
        if len(hits) > 1:
            contested_area += a
        elif len(hits) == 1:
            height_area[hits[0]] = height_area.get(hits[0], 0.0) + a

    if contested_area > 0.01 * total_area:
        return [], True
    if not height_area:
        return _plain(default_mm), False
    if len(height_area) > 1:
        return [], True

    height_mm = next(iter(height_area))
    in_frac = height_area[height_mm] / total_area
    zone_polys = polys_by_height[height_mm]
    if in_frac >= 0.99:
        return _plain(height_mm), False
    if in_frac <= 0.01:
        return _plain(default_mm), False

    return [
        {u"height_mm": height_mm, u"is_partial": True, u"zone_polys": zone_polys, u"keep_inside": True},
        {u"height_mm": default_mm, u"is_partial": True, u"zone_polys": zone_polys, u"keep_inside": False},
    ], False


def _try_set_shape(solids):
    """Attempt to build a DirectShape from `solids`. DirectShape.SetShape has
    its own stricter validation than a plain Volume check — boolean clip
    results can look fine (positive volume) yet still contain a sliver at the
    cut boundary that SetShape rejects outright ("does not satisfy DirectShape
    validation criteria"). Rather than guess at a pre-check, just try it: on
    failure, clean up the half-created element and return None so the caller
    can fall back to different geometry."""
    if not solids:
        return None
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_Floors))
    try:
        ds.SetShape(SCG.List[DB.GeometryObject](solids))
        return ds
    except Exception:
        try:
            doc.Delete(ds.Id)
        except Exception:
            pass
        return None


def _filter_valid_batch(solids):
    """Bisection helper for _filter_individually_valid: try the whole batch
    as one DirectShape first; only split and recurse into halves if that
    fails. Costs a single DirectShape attempt for an all-good batch instead
    of one per piece — the difference between ~1 and ~40,000 API calls on a
    face that triangulates into tens of thousands of tiny pieces (confirmed
    to effectively hang Revit on a real project when done one-by-one)."""
    if not solids:
        return []
    ds = _try_set_shape(solids)
    if ds is not None:
        try:
            doc.Delete(ds.Id)
        except Exception:
            pass
        return solids
    if len(solids) == 1:
        return []
    mid = len(solids) // 2
    return _filter_valid_batch(solids[:mid]) + _filter_valid_batch(solids[mid:])


def _filter_individually_valid(solids):
    """SetShape validates the WHOLE batch at once — a single bad solid among
    many good ones (typical after clipping many small triangulated pieces,
    see _face_triangulated_solids) makes Revit reject the entire list, not
    just the offending piece. Narrow down to the individually-valid subset
    via bisection (see _filter_valid_batch) rather than testing every piece
    one by one, so one bad triangle doesn't take down all its neighbors —
    and, critically, so a huge piece count doesn't turn this into tens of
    thousands of sequential DirectShape create/delete calls."""
    if len(solids) <= 1:
        return solids
    return _filter_valid_batch(solids)


def _build_clearance_prism(face, footprint_loops, host_normal, link_transform, height_mm, label=None):
    """Build the base clearance solid(s) for ONE top face. The clearance is
    always extruded straight UP (+Z) by the clearance height — head height is
    a vertical (plumb) measurement, so the volume must give the same vertical
    clearance above a sloped ramp as above a flat floor (extruding along the
    tilted face normal would foreshorten it on the slope). The bottom of the
    volume still follows the face, so the slope is preserved. Tiers, cleanest
    first:
      1. Extrude the face's own edge loops (exact — arcs and all).
      2. If that fails (usually a slightly-non-planar imported loop, which
         CreateExtrusionGeometry rejects as "Non-planar CurveLoop"), fit a
         plane to the face and project the loops onto it so they're planar,
         then extrude — one clean prism.
      3. Only if BOTH fail, triangulate the face, HARD-CAPPED at
         TRIANGULATION_LAST_RESORT_CAP pieces so it can never explode; over
         the cap the face is flagged and skipped.
    `host_normal` is retained for signature compatibility but no longer sets
    the extrusion direction. Returns (solids_list, method) with method in
    'raw' | 'projected' | 'triangulated' | 'failed' ('failed' -> [])."""
    height_ft = mm_to_ft(height_mm)

    try:
        solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
            SCG.List[DB.CurveLoop](footprint_loops), DB.XYZ.BasisZ, height_ft)
        if solid is not None and solid.Volume > 0:
            return [solid], u"raw"
    except Exception:
        pass

    plane = _face_best_fit_plane(face, link_transform)
    if plane is not None:
        origin, pn = plane
        if pn.Z < 0:
            pn = pn.Negate()
        ploops = _project_loops_to_plane(footprint_loops, origin, pn)
        if ploops:
            try:
                solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    SCG.List[DB.CurveLoop](ploops), DB.XYZ.BasisZ, height_ft)
                if solid is not None and solid.Volume > 0:
                    return [solid], u"projected"
            except Exception:
                pass

    mesh, n = _choose_build_mesh(face)
    if mesh is not None:
        tri = _mesh_to_prisms(mesh, link_transform, height_ft)
        if tri:
            logger.info(u"{}: warped top face built via coarse triangulation ({} facet(s)) "
                        u"at {}mm.".format(label, len(tri), height_mm))
            return tri, u"triangulated"
    elif n > TRIANGULATION_LAST_RESORT_CAP:
        logger.warning(
            u"{}: top face too complex to build cleanly ({} facets even at the coarsest level "
            u"of detail, over the {}-piece cap) — clearance for this face skipped; needs "
            u"attention in the source model.".format(label, n, TRIANGULATION_LAST_RESORT_CAP))
    return [], u"failed"


def create_clearance_shape(face, footprint_loops, host_normal, height_mm, ws_id, link_transform,
                            zone_polys=None, keep_inside=None, label=None):
    """Build the DirectShape for one assignment.
      - WHOLE assignment (zone_polys is None): build the entire face at
        `height_mm`, cleanly (single extrusion, or coarse-triangulated only if
        the face is warped) — see _build_clearance_prism.
      - PARTIAL assignment (zone_polys given): keep only the triangles whose
        plan centroid is (keep_inside == inside the zone outline), classified
        by pure 2D point-in-polygon — no boolean solids.
    Returns None if nothing could be built for this side (a valid outcome for
    the partial case when the face is entirely on the other side)."""
    if zone_polys is None:
        solids, _method = _build_clearance_prism(
            face, footprint_loops, host_normal, link_transform, height_mm, label)
    else:
        # Partial: build the full prism, then clip to the zone outline.
        base_solids, method = _build_clearance_prism(
            face, footprint_loops, host_normal, link_transform, height_mm, label)
        if not base_solids:
            return None
        if method == u"triangulated":
            # Warped face (e.g. curved ramp) — split by per-triangle 2D
            # classification; a faceted edge is invisible on a faceted ramp.
            solids = _build_face_prisms_classified(
                face, link_transform, height_mm, zone_polys, keep_inside)
        else:
            # Clean planar face (typical flat slab) — straight boolean cut
            # along the zone outline (clean edge, few pieces). Falls back to
            # the per-triangle method only if the boolean can't run.
            solids = _boolean_clip_prism(base_solids, footprint_loops, zone_polys, keep_inside)
            if solids is None:
                solids = _build_face_prisms_classified(
                    face, link_transform, height_mm, zone_polys, keep_inside)
    if not solids:
        return None

    solids = _filter_individually_valid(solids)
    ds = _try_set_shape(solids)
    if ds is None:
        return None

    ds.Name = SHAPE_NAME
    if ws_id is not None:
        try:
            wp = ds.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
            if wp and not wp.IsReadOnly:
                wp.Set(ws_id.IntegerValue)
        except Exception:
            pass
    return ds


def get_or_create_workset(name):
    if not doc.IsWorkshared:
        return None
    for ws in DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset):
        if ws.Name == name:
            return ws.Id
    return DB.Workset.Create(doc, name).Id


def _scope_box_world_outline(scope_box):
    """Axis-aligned world-space bounding Outline of a scope box, accounting for
    a rotated scope box (its bbox carries a Transform)."""
    bbox = scope_box.get_BoundingBox(None)
    if bbox is None:
        return None
    ct = bbox.Transform
    mn, mx = bbox.Min, bbox.Max
    corners = [ct.OfPoint(DB.XYZ(x, y, z))
               for x in (mn.X, mx.X) for y in (mn.Y, mx.Y) for z in (mn.Z, mx.Z)]
    return DB.Outline(
        DB.XYZ(min(c.X for c in corners), min(c.Y for c in corners), min(c.Z for c in corners)),
        DB.XYZ(max(c.X for c in corners), max(c.Y for c in corners), max(c.Z for c in corners)))


def cleanup_previous_shapes(scope_box=None):
    """Delete previously-generated clearance DirectShapes. If `scope_box` is
    given, delete ONLY those whose bounding box intersects that scope box —
    so re-running on one scope box leaves clearance masses generated for other
    areas untouched. With no scope box, every clearance shape is removed."""
    collector = (DB.FilteredElementCollector(doc)
                 .OfClass(DB.DirectShape)
                 .WhereElementIsNotElementType())
    if scope_box is not None:
        outline = _scope_box_world_outline(scope_box)
        if outline is not None:
            collector = collector.WherePasses(DB.BoundingBoxIntersectsFilter(outline))
    to_delete = []
    for ds in collector:
        try:
            if ds.Name == SHAPE_NAME:
                to_delete.append(ds.Id)
        except Exception:
            pass
    if to_delete:
        doc.Delete(SCG.List[DB.ElementId](to_delete))
    return len(to_delete)


def find_view3d_by_name(name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
        if not v.IsTemplate and v.Name == name:
            return v
    return None


def find_3d_view_family_type():
    for vft in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if vft.ViewFamily == DB.ViewFamily.ThreeDimensional:
            return vft
    return None


def create_or_update_isolated_view(workset_name):
    view = find_view3d_by_name(VIEW_3D)
    existed = view is not None
    if view is None:
        vft = find_3d_view_family_type()
        if vft is None:
            raise Exception(u"No 3D ViewFamilyType found in the project.")
        view = DB.View3D.CreateIsometric(doc, vft.Id)
        view.Name = VIEW_3D
    if doc.IsWorkshared:
        for ws in DB.FilteredWorksetCollector(doc).OfKind(DB.WorksetKind.UserWorkset):
            visible = ws.Name == workset_name
            try:
                view.SetWorksetVisibility(
                    ws.Id, DB.WorksetVisibility.Visible if visible else DB.WorksetVisibility.Hidden)
            except Exception:
                pass
    return view, existed


def _raw_geometry_object_counts(geom_element):
    """Unfiltered inventory of a GeometryElement's top-level objects, recursing
    one level into GeometryInstance — reports every Solid's raw Volume
    (including zero/near-zero ones the >0 filter in _iter_solids discards),
    so a Join-Geometry/Parts situation that leaves a Solid object present but
    with no net volume can be told apart from there being no Solid at all."""
    counts = {}
    volumes = []
    for g in geom_element:
        type_name = type(g).__name__
        counts[type_name] = counts.get(type_name, 0) + 1
        if isinstance(g, DB.Solid):
            volumes.append(g.Volume)
        elif isinstance(g, DB.GeometryInstance):
            try:
                inst_geom = g.GetInstanceGeometry()
            except Exception:
                inst_geom = None
            if inst_geom is not None:
                for g2 in inst_geom:
                    type_name2 = u"Instance/" + type(g2).__name__
                    counts[type_name2] = counts.get(type_name2, 0) + 1
                    if isinstance(g2, DB.Solid):
                        volumes.append(g2.Volume)
    return counts, volumes


def _floor_geometry_diagnostic(floor, link_transform):
    """Best-effort diagnostic for a floor with zero detected upward-facing
    faces — reports solid/face counts and the best normal.Z seen (both the
    single-point sample and the area-weighted average across each face), plus
    Parts/Join-Geometry status, so the real cause (no solids at all vs. a
    Solid present but with no net volume because of Parts/a coincident join
    vs. faces that are genuinely never upward) can be told apart instead of
    guessed at."""
    try:
        doc = floor.Document
        extra_bits = []
        try:
            if DB.PartUtils.HasAssociatedParts(doc, floor.Id):
                extra_bits.append(u"has associated Parts")
        except Exception:
            pass
        try:
            joined_ids = list(DB.JoinGeometryUtils.GetJoinedElements(doc, floor))
            if joined_ids:
                extra_bits.append(u"joined with {} element(s) ({})".format(
                    len(joined_ids), u", ".join(str(i.IntegerValue) for i in joined_ids[:5])))
        except Exception:
            pass
        try:
            sketch_id = floor.SketchId
            if sketch_id is not None and sketch_id != DB.ElementId.InvalidElementId:
                sketch = doc.GetElement(sketch_id)
                profile = sketch.Profile if sketch is not None else None
                if profile is not None:
                    loop_count = profile.Size
                    curve_count = sum(loop.Size for loop in profile)
                    extra_bits.append(u"Sketch.Profile: {} loop(s), {} curve(s)".format(
                        loop_count, curve_count))
                else:
                    extra_bits.append(u"Sketch element found but Profile is None")
            else:
                extra_bits.append(u"no SketchId (not sketch-based)")
        except Exception as sk_ex:
            extra_bits.append(u"Sketch check failed: {}".format(sk_ex))

        opt = DB.Options()
        opt.ComputeReferences = False
        opt.DetailLevel = DB.ViewDetailLevel.Fine
        geom = floor.get_Geometry(opt)
        if geom is None:
            return u"get_Geometry returned None" + (u" — " + u"; ".join(extra_bits) if extra_bits else u"")
        raw_counts, raw_volumes = _raw_geometry_object_counts(geom)
        counts_txt = u", ".join(u"{}={}".format(k, v) for k, v in sorted(raw_counts.items()))
        volumes_txt = (u", ".join(u"{:.4f}".format(v) for v in raw_volumes)
                       if raw_volumes else u"none")
        solids = list(_iter_solids(geom))
        if not solids:
            # NOTE: this used to also retry get_Geometry with
            # IncludeNonVisibleObjects=True to see if that surfaced anything —
            # removed. That retry forces Revit to compute hidden/non-visible
            # geometry, which on a floor whose B-rep is already known to be
            # degenerate (import/conversion-derived) can be an extremely long
            # or effectively-hung recompute. This diagnostic runs during
            # _gather_floor_footprints, i.e. on scope-box selection itself —
            # a real 5-10+ minute, ~3% CPU hang was traced to exactly this
            # call. Not worth it for diagnostic-only value.
            return (u"0 usable solids found (no positive volume, no faces) — raw geometry "
                     u"objects: [{}] — raw Solid volume(s) (ft3): [{}]{}".format(
                         counts_txt, volumes_txt,
                         u" — " + u"; ".join(extra_bits) if extra_bits else u""))
        face_count = 0
        best_sample_z = None
        best_avg_z = None
        for solid in solids:
            for face in solid.Faces:
                face_count += 1
                try:
                    bbox_uv = face.GetBoundingBox()
                    mid_uv = DB.UV((bbox_uv.Min.U + bbox_uv.Max.U) / 2.0,
                                    (bbox_uv.Min.V + bbox_uv.Max.V) / 2.0)
                    normal_local = face.ComputeNormal(mid_uv)
                    z = link_transform.OfVector(normal_local).Normalize().Z
                    if best_sample_z is None or z > best_sample_z:
                        best_sample_z = z
                except Exception:
                    pass
                avg_z = _face_area_weighted_normal_z(face, link_transform)
                if avg_z is not None and (best_avg_z is None or avg_z > best_avg_z):
                    best_avg_z = avg_z
        return (u"{} solid(s), {} face(s) total; best sample normal.Z={}, "
                u"best area-weighted normal.Z={}".format(
                    len(solids), face_count,
                    u"{:.3f}".format(best_sample_z) if best_sample_z is not None else u"n/a",
                    u"{:.3f}".format(best_avg_z) if best_avg_z is not None else u"n/a"))
    except Exception as ex:
        return u"diagnostic failed: {}".format(ex)


def _count_floors_in_scope(scope_box, struct_links):
    """Cheap floor count for a scope box — a BoundingBoxIntersectsFilter
    collector per loaded link, no per-floor geometry extraction. Used to warn
    the user before they select a scope box and trigger the much more
    expensive _gather_floor_footprints (which extracts and classifies every
    floor's top face)."""
    scope_bbox = scope_box.get_BoundingBox(None)
    if scope_bbox is None:
        return 0
    total = 0
    for link_info in struct_links:
        if not link_info[u"loaded"] or link_info[u"doc"] is None:
            continue
        link_doc = link_info[u"doc"]
        link_transform = link_info[u"instance"].GetTotalTransform()
        try:
            total += len(collect_structural_floors(link_doc, link_transform, scope_bbox))
        except Exception:
            pass
    return total


def _gather_floor_footprints(scope_box, struct_links):
    """One-time (per scope box) extraction of every structural floor's top-face
    footprint from every LOADED structural link, tagged with its source link
    name. Cached by the dialog per scope-box selection and reused for both the
    zones-table preview and the actual Run (there filtered to selected links)."""
    scope_bbox = scope_box.get_BoundingBox(None)
    out = []
    for link_info in struct_links:
        if not link_info[u"loaded"] or link_info[u"doc"] is None:
            continue
        link_doc = link_info[u"doc"]
        link_transform = link_info[u"instance"].GetTotalTransform()
        collected = collect_structural_floors(link_doc, link_transform, scope_bbox)
        for floor in collected:
            label = _floor_label(floor)
            faces = extract_top_faces(floor, link_transform)
            if not faces:
                try:
                    fbbox = floor.get_BoundingBox(None)
                    bbox_txt = (u"link-local bbox X=[{:.2f}, {:.2f}] Y=[{:.2f}, {:.2f}] Z=[{:.2f}, {:.2f}]".format(
                        fbbox.Min.X, fbbox.Max.X, fbbox.Min.Y, fbbox.Max.Y, fbbox.Min.Z, fbbox.Max.Z)
                        if fbbox is not None else u"bbox unavailable")
                except Exception:
                    bbox_txt = u"bbox unavailable"
                diag = _floor_geometry_diagnostic(floor, link_transform)
                logger.warning(u"Floor {} ({}): no upward-facing face found — {} — {} — skipped.".format(
                    floor.Id.IntegerValue, label, bbox_txt, diag))
                continue
            for face, normal in faces:
                loops = face_boundary_loops_in_host_space(face, link_transform)
                if not loops:
                    logger.warning(u"Floor {} ({}): face found but boundary loops empty — skipped.".format(
                        floor.Id.IntegerValue, label))
                    continue
                out.append({
                    u"link_name": link_info[u"name"],
                    u"label": label,
                    u"loops": loops,
                    u"normal": normal,
                    u"face": face,
                    u"link_transform": link_transform,
                })
    return out


def _footprint_bbox_xy(f):
    """Cheap X/Y bounding box of a footprint fragment's own loop vertices —
    used only as a broad-phase pre-filter (see _analyze_zones), never for the
    actual overlap decision."""
    try:
        xs, ys = [], []
        for cl in f[u"loops"]:
            for curve in cl:
                p = curve.GetEndPoint(0)
                xs.append(p.X)
                ys.append(p.Y)
        if not xs:
            return None
        return (min(xs), max(xs), min(ys), max(ys))
    except Exception:
        return None


def _bbox_xy_near(fbbox, sp_bbox, margin_ft):
    fx0, fx1, fy0, fy1 = fbbox
    return (fx1 >= sp_bbox.Min.X - margin_ft and fx0 <= sp_bbox.Max.X + margin_ft and
            fy1 >= sp_bbox.Min.Y - margin_ft and fy0 <= sp_bbox.Max.Y + margin_ft)


def _analyze_zones(scope_box, footprints):
    """Classify every clearance-Space in scope as 'full' or 'partial' overlap
    against the union of all structural floors found in the scope (for the
    dialog's special-zones preview table)."""
    zone_spaces = get_clearance_spaces_in_scope(scope_box)
    if not zone_spaces:
        return []
    reach_ft = mm_to_ft(max([DEFAULT_CLEARANCE] + [h for _, h in zone_spaces])) + 1.0
    margin_ft = 1.0

    # Broad-phase pre-filter: building a footprint's shadow column is
    # expensive (can fall back to a per-triangle solid for complex real floor
    # geometry — see _footprint_shadow_columns), and most floors in a large
    # scope box are nowhere near a small override zone. Skipping those here
    # avoids real geometry work that can never affect the result — previously
    # this ran for EVERY footprint in the whole scope box regardless of
    # distance from any zone, which could freeze Revit on a large scope box.
    sp_bboxes = [b for b in (sp.get_BoundingBox(None) for sp, _h in zone_spaces) if b is not None]
    relevant_footprints = []
    for f in footprints:
        fbbox = _footprint_bbox_xy(f)
        if fbbox is not None and any(_bbox_xy_near(fbbox, b, margin_ft) for b in sp_bboxes):
            relevant_footprints.append(f)

    floor_cols = []
    for f in relevant_footprints:
        cols = _footprint_shadow_columns(f[u"loops"], f[u"face"], f[u"link_transform"], margin_ft, reach_ft)
        floor_cols.extend(cols)

    zones = []
    for sp, height_mm in zone_spaces:
        sp_col = _space_shadow_column(sp, _space_boundary_loops(sp))
        if sp_col is None or sp_col.Volume <= 0:
            continue
        covered = 0.0
        for fcol in floor_cols:
            try:
                inter = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                    sp_col, fcol, DB.BooleanOperationsType.Intersect)
            except Exception:
                inter = None
            if inter is not None:
                covered += inter.Volume
        if covered <= 1e-9:
            continue
        ratio = min(covered / sp_col.Volume, 1.0)
        zones.append({
            u"space": sp,
            u"id": sp.Id.IntegerValue,
            u"name": _space_label(sp),
            u"height_mm": height_mm,
            u"overlap": u"full" if ratio >= FULL_OVERLAP_RATIO else u"partial",
            u"on": True,
        })
    return sorted(zones, key=lambda z: z[u"name"])


def run(footprints, struct_links, candidate_spaces, default_mm, clear_previous, scope_box=None):
    """Execute the head-height check against the already-extracted `footprints`
    (filtered here to the currently-selected structural links). Returns a
    summary dict for the results panel. `scope_box` (when clearing previous
    results) confines the delete to clearance masses inside that scope box."""
    summary = {u"floors": 0, u"created": 0, u"conflicts": [], u"view_existed": False,
               u"zone_usage": {}}
    selected_link_names = set(
        li[u"name"] for li in struct_links if li[u"on"] and li[u"loaded"])
    relevant = [f for f in footprints if f[u"link_name"] in selected_link_names]

    tg = DB.TransactionGroup(doc, u"EasyBIM: Head Height Check")
    tg.Start()
    try:
        t1 = DB.Transaction(doc, u"EasyBIM: Clear previous results")
        t1.Start()
        ws_id = get_or_create_workset(WORKSET)
        if clear_previous:
            cleanup_previous_shapes(scope_box)
        t1.Commit()

        t2 = DB.Transaction(doc, u"EasyBIM: Generate clearance volumes")
        t2.Start()
        zone_usage = {}
        processed_floor_labels = set()
        space_col_cache = {}
        for f in relevant:
            processed_floor_labels.add(f[u"label"])
            assignments, conflict = resolve_height(
                f[u"loops"], candidate_spaces, default_mm, f[u"face"], f[u"link_transform"],
                space_col_cache=space_col_cache, label=f[u"label"])
            if conflict:
                summary[u"conflicts"].append(f[u"label"])
                continue
            any_created = False
            for assignment in assignments:
                height_mm = assignment[u"height_mm"]
                is_partial = assignment[u"is_partial"]
                try:
                    ds = create_clearance_shape(
                        f[u"face"], f[u"loops"], f[u"normal"], height_mm, ws_id, f[u"link_transform"],
                        zone_polys=assignment.get(u"zone_polys"),
                        keep_inside=assignment.get(u"keep_inside"), label=f[u"label"])
                except Exception as ex:
                    logger.warning(u"Shape creation failed for {}: {}".format(f[u"label"], ex))
                    continue
                if ds is None:
                    continue
                any_created = True
                summary[u"created"] += 1
                bucket = zone_usage.setdefault(height_mm, {u"count": 0, u"partial": False})
                bucket[u"count"] += 1
                bucket[u"partial"] = bucket[u"partial"] or is_partial
            if not any_created and assignments:
                _log_no_coverage_gap(f)
        summary[u"floors"] = len(processed_floor_labels)
        t2.Commit()

        t3 = DB.Transaction(doc, u"EasyBIM: Update 3D view")
        t3.Start()
        view, existed = create_or_update_isolated_view(WORKSET)
        summary[u"view_existed"] = existed
        t3.Commit()

        tg.Assimilate()
    except Exception:
        tg.RollBack()
        raise

    summary[u"zone_usage"] = zone_usage
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# WPF XAML
# ─────────────────────────────────────────────────────────────────────────────

XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Head Height Check"
  Width="640" Height="800"
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
        <Border Grid.Column="0" Width="44" Height="44" CornerRadius="10" VerticalAlignment="Center"
                Background="#151b6e">
          <Path Data="M12,20 L12,5 M6,11 L12,5 L18,11 M4,20.5 L20,20.5"
                Stroke="White" StrokeThickness="1.7" StrokeLineJoin="Round"
                StrokeStartLineCap="Round" StrokeEndLineCap="Round"
                Width="24" Height="24" Stretch="None"
                HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="Head Height Check" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="MEP clearance zones · linked structural floors"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Content="✕" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- BODY -->
    <ScrollViewer Grid.Row="2" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
      <Grid Margin="20,14,20,10">

        <!-- CONFIG -->
        <StackPanel x:Name="ConfigPanel">

          <!-- shared-parameter status -->
          <Border Background="#e9f9f2" BorderBrush="#b9e9d3" BorderThickness="1"
                  CornerRadius="8" Padding="11,9" Margin="0,0,0,16">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="&#x2713;" FontSize="13" FontWeight="Bold"
                         Foreground="#22b07c" Margin="0,0,9,0" VerticalAlignment="Center"/>
              <TextBlock Grid.Column="1" TextWrapping="Wrap" VerticalAlignment="Center" FontSize="12.5">
                <Run Text="Shared parameter " Foreground="#374151"/>
                <Run Text="Required Clearance Height" FontFamily="Consolas" Foreground="#1e248c" FontWeight="SemiBold"/>
                <Run Text=" bound to Spaces." Foreground="#374151"/>
              </TextBlock>
              <Border Grid.Column="2" Background="#22b07c" CornerRadius="10" Padding="8,3" VerticalAlignment="Center">
                <TextBlock Text="Ready" FontSize="10.5" FontWeight="SemiBold" Foreground="White"/>
              </Border>
            </Grid>
          </Border>

          <!-- structural links -->
          <Grid Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="LinksLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                       Foreground="#9aa0ac" VerticalAlignment="Center"/>
            <Button x:Name="ToggleAllLinksBtn" Grid.Column="1" Content="Select all"
                    Style="{StaticResource CyanTextBtn}"/>
          </Grid>
          <Border x:Name="NoLinksBanner" Background="#fbecec" BorderBrush="#f0c6c6" BorderThickness="1"
                  CornerRadius="8" Padding="11,9" Margin="0,0,0,16" Visibility="Collapsed">
            <TextBlock Text="No loaded links found. Load the link containing your structural floors and try again."
                       FontSize="12.5" Foreground="#374151" TextWrapping="Wrap"/>
          </Border>
          <Border x:Name="LinksCard" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                  CornerRadius="8" Margin="0,0,0,16">
            <ScrollViewer MaxHeight="150" VerticalScrollBarVisibility="Auto"
                          HorizontalScrollBarVisibility="Disabled">
              <StackPanel x:Name="LinksListPanel"/>
            </ScrollViewer>
          </Border>

          <!-- scope box -->
          <TextBlock Text="SCOPE BOX · HOST MODEL" FontFamily="Consolas" FontSize="10"
                     Foreground="#9aa0ac" Margin="0,0,0,8"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Margin="0,0,0,16">
            <StackPanel x:Name="ScopeListPanel"/>
          </Border>

          <!-- default clearance -->
          <Grid Margin="0,0,0,16">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="Auto"/>
              <ColumnDefinition Width="12"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0">
              <TextBlock Text="DEFAULT CLEARANCE" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,8"/>
              <Grid Width="150">
                <TextBox x:Name="ClearanceBox" Height="34" FontFamily="Consolas" FontSize="13" FontWeight="SemiBold"
                         Foreground="#1e248c" Padding="10,0,34,0" VerticalContentAlignment="Center"
                         BorderBrush="#e8eaff" BorderThickness="1" Background="White"/>
                <TextBlock Text="mm" FontFamily="Consolas" FontSize="12" Foreground="#9aa0ac"
                           HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,12,0" IsHitTestVisible="False"/>
              </Grid>
            </StackPanel>
            <StackPanel Grid.Column="2" VerticalAlignment="Bottom">
              <TextBlock Text="APPLIED WHERE NO SPACE OVERRIDES" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,8"/>
              <Border Background="#fafbff" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Padding="12,9" Height="42">
                <TextBlock x:Name="ClearanceHelperTB" FontSize="12.5" Foreground="#6b7280" VerticalAlignment="Center" TextWrapping="NoWrap"/>
              </Border>
            </StackPanel>
          </Grid>

          <!-- special zones -->
          <Grid x:Name="ZonesHeaderGrid" Margin="0,0,0,8">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="ZonesLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                       Foreground="#9aa0ac" VerticalAlignment="Center"/>
            <TextBlock x:Name="ZonesCountTB" Grid.Column="1" FontSize="11.5" Foreground="#9aa0ac" VerticalAlignment="Center"/>
          </Grid>
          <Border x:Name="NoZonesCard" Background="#fafbff" BorderBrush="#e8eaff" BorderThickness="1"
                  CornerRadius="8" Padding="12,10" Margin="0,0,0,16" Visibility="Collapsed">
            <TextBlock TextWrapping="Wrap" FontSize="12" Foreground="#6b7280">
              <Run Text="No override Spaces in this scope — every floor uses the default above. To raise clearance on a non-standard area, place an "/>
              <Run Text="MEP Space" FontFamily="Consolas" Foreground="#1e248c"/>
              <Run Text=" over it and set "/>
              <Run Text="Required Clearance Height" FontFamily="Consolas" Foreground="#1e248c"/>
              <Run Text="; it will appear here."/>
            </TextBlock>
          </Border>
          <Border x:Name="ZonesCard" Background="White" BorderBrush="#e8eaff" BorderThickness="1"
                  CornerRadius="8" Margin="0,0,0,8" Visibility="Collapsed">
            <StackPanel x:Name="ZonesListPanel"/>
          </Border>
          <StackPanel x:Name="ConflictsPreviewPanel" Margin="0,0,0,8"/>

          <!-- cleanup toggle -->
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8"
                  Padding="14,12" Margin="0,0,0,16">
            <Grid>
              <StackPanel>
                <TextBlock Text="Clear previous results before running" FontSize="13.5" Foreground="#374151" FontWeight="Medium"/>
                <TextBlock FontSize="11.5" Foreground="#9aa0ac" Margin="0,2,0,0" TextWrapping="Wrap">
                  <Run Text="Deletes existing "/>
                  <Run Text="HeadHeightCheck_Clearance" FontFamily="Consolas"/>
                  <Run Text=" elements on "/>
                  <Run Text="+ Mass" FontFamily="Consolas"/>
                  <Run Text=" first."/>
                </TextBlock>
              </StackPanel>
              <ToggleButton x:Name="ClearToggle" HorizontalAlignment="Right" VerticalAlignment="Center" IsChecked="True"
                            Width="38" Height="22" Cursor="Hand" BorderThickness="0">
                <ToggleButton.Template>
                  <ControlTemplate TargetType="ToggleButton">
                    <Border x:Name="Tr" Width="38" Height="22" CornerRadius="11"
                            Background="#44b8d3" Padding="2">
                      <Border x:Name="Th" Width="18" Height="18" CornerRadius="9"
                              Background="White" HorizontalAlignment="Right">
                        <Border.Effect>
                          <DropShadowEffect Color="#000000" BlurRadius="3" ShadowDepth="1" Opacity="0.18"/>
                        </Border.Effect>
                      </Border>
                    </Border>
                    <ControlTemplate.Triggers>
                      <Trigger Property="IsChecked" Value="False">
                        <Setter TargetName="Tr" Property="Background" Value="#cbd0e0"/>
                        <Setter TargetName="Th" Property="HorizontalAlignment" Value="Left"/>
                      </Trigger>
                    </ControlTemplate.Triggers>
                  </ControlTemplate>
                </ToggleButton.Template>
              </ToggleButton>
            </Grid>
          </Border>

          <!-- output preview -->
          <TextBlock Text="OUTPUT" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,8"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8">
            <StackPanel>
              <Border Padding="14,11">
                <Grid>
                  <Grid.ColumnDefinitions><ColumnDefinition Width="34"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                  <Border Grid.Column="0" Width="30" Height="30" CornerRadius="8" Background="#e8eaff" HorizontalAlignment="Left">
                    <TextBlock Text="&#x2630;" FontSize="14" Foreground="#1e248c" HorizontalAlignment="Center" VerticalAlignment="Center"/>
                  </Border>
                  <StackPanel Grid.Column="1" Margin="11,0,0,0" VerticalAlignment="Center">
                    <TextBlock Text="Workset" FontSize="11" Foreground="#9aa0ac"/>
                    <StackPanel Orientation="Horizontal">
                      <TextBlock Text="+ Mass" FontFamily="Consolas" FontSize="13" Foreground="#1f2937" FontWeight="SemiBold" Margin="0,0,8,0"/>
                      <Border Background="#ecf8fc" BorderBrush="#b8e8f2" BorderThickness="1" CornerRadius="10" Padding="7,1">
                        <TextBlock Text="auto-create" FontSize="10" Foreground="#44b8d3" FontWeight="SemiBold"/>
                      </Border>
                    </StackPanel>
                  </StackPanel>
                </Grid>
              </Border>
            </StackPanel>
          </Border>

        </StackPanel>

        <!-- RESULTS -->
        <StackPanel x:Name="ResultsPanel" Visibility="Collapsed">
          <StackPanel HorizontalAlignment="Center" Margin="0,4,0,18">
            <Border Width="52" Height="52" CornerRadius="26" Background="#e4f7f0" HorizontalAlignment="Center" Margin="0,0,0,12">
              <TextBlock Text="&#x2713;" FontSize="26" FontWeight="Bold" Foreground="#22b07c" HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <TextBlock Text="Head-height check complete" FontSize="18" FontWeight="Bold" Foreground="#1e248c" HorizontalAlignment="Center"/>
            <TextBlock x:Name="ResSummaryTB" FontSize="12.5" Foreground="#6b7280" HorizontalAlignment="Center" Margin="0,3,0,0" TextWrapping="Wrap" TextAlignment="Center"/>
          </StackPanel>

          <Grid Margin="0,0,0,16">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/><ColumnDefinition Width="8"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <Border Grid.Column="0" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatFloorsTB" FontFamily="Consolas" FontWeight="Bold" FontSize="22" Foreground="#1e248c" HorizontalAlignment="Center"/>
                <TextBlock Text="Floors processed" FontSize="10.5" Foreground="#6b7280" Margin="0,4,0,0" HorizontalAlignment="Center" TextWrapping="Wrap" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="2" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatCreatedTB" FontFamily="Consolas" FontWeight="Bold" FontSize="22" Foreground="#22b07c" HorizontalAlignment="Center"/>
                <TextBlock Text="Volumes created" FontSize="10.5" Foreground="#6b7280" Margin="0,4,0,0" HorizontalAlignment="Center" TextWrapping="Wrap" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="4" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatZonesTB" FontFamily="Consolas" FontWeight="Bold" FontSize="22" Foreground="#44b8d3" HorizontalAlignment="Center"/>
                <TextBlock Text="Special zones" FontSize="10.5" Foreground="#6b7280" Margin="0,4,0,0" HorizontalAlignment="Center" TextWrapping="Wrap" TextAlignment="Center"/>
              </StackPanel>
            </Border>
            <Border Grid.Column="6" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Padding="10,10">
              <StackPanel HorizontalAlignment="Center">
                <TextBlock x:Name="StatConflictsTB" FontFamily="Consolas" FontWeight="Bold" FontSize="22" Foreground="#9aa0ac" HorizontalAlignment="Center"/>
                <TextBlock Text="Conflicts" FontSize="10.5" Foreground="#6b7280" Margin="0,4,0,0" HorizontalAlignment="Center" TextWrapping="Wrap" TextAlignment="Center"/>
              </StackPanel>
            </Border>
          </Grid>

          <TextBlock Text="HEIGHT BREAKDOWN" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,8"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Margin="0,0,0,16">
            <StackPanel x:Name="HeightBreakdownPanel"/>
          </Border>

          <StackPanel x:Name="ResultConflictsSection" Visibility="Collapsed">
            <TextBlock Text="FLAGGED FOR REVIEW · NOT GENERATED" FontFamily="Consolas" FontSize="10" Foreground="#d9a406" Margin="0,0,0,8"/>
            <StackPanel x:Name="ResultConflictsPanel" Margin="0,0,0,16"/>
          </StackPanel>

          <Border Background="#f2fafc" BorderBrush="#c9ecf3" BorderThickness="1" CornerRadius="8" Padding="13,10">
            <Grid>
              <Grid.ColumnDefinitions><ColumnDefinition Width="30"/><ColumnDefinition Width="*"/><ColumnDefinition Width="20"/></Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="&#x25A2;" FontSize="14" Foreground="#1e248c" VerticalAlignment="Center"/>
              <TextBlock Grid.Column="1" VerticalAlignment="Center" TextWrapping="Wrap" FontSize="12.5">
                <Run Text="3D view " Foreground="#374151"/>
                <Run Text="EB_3D_9.Mass" FontFamily="Consolas" Foreground="#1e248c" FontWeight="SemiBold"/>
                <Run x:Name="ViewStatusRun" Text=" updated · isolated to + Mass." Foreground="#374151"/>
              </TextBlock>
              <TextBlock x:Name="ViewStatusTB" Grid.Column="2" Text="&#x2713;" FontSize="14" Foreground="#22b07c" VerticalAlignment="Center"/>
            </Grid>
          </Border>
        </StackPanel>

      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Border Grid.Row="3" Background="White" BorderBrush="#e8eaff" BorderThickness="0,1,0,0" Padding="20,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="FooterSummaryTB" Grid.Column="0" FontFamily="Consolas" FontSize="12"
                   Foreground="#9aa0ac" VerticalAlignment="Center"/>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <Button x:Name="CancelBtn" Content="Cancel" Style="{StaticResource GhostBtn}"/>
          <Button x:Name="RunBtn"    Content="▶  Run check"    Style="{StaticResource PrimaryBtn}" Margin="8,0,0,0"/>
          <Button x:Name="AgainBtn"  Content="↺  Run again" Style="{StaticResource GhostBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="OpenBtn"   Content="Open 3D view  ↗" Style="{StaticResource PrimaryBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
        </StackPanel>
      </Grid>
    </Border>

  </Grid>
</Window>
"""

# ─────────────────────────────────────────────────────────────────────────────
# DIALOG
# ─────────────────────────────────────────────────────────────────────────────

NAVY_COLOR  = WM.Color.FromRgb(0x1e, 0x24, 0x8c)
CYAN_COLOR  = WM.Color.FromRgb(0x44, 0xb8, 0xd3)
GRAY_COLOR  = WM.Color.FromRgb(0xc6, 0xcb, 0xe0)
GREEN_COLOR = WM.Color.FromRgb(0x22, 0xb0, 0x7c)
AMBER_COLOR = WM.Color.FromRgb(0xd9, 0xa4, 0x06)
BODY_COLOR  = WM.Color.FromRgb(0x37, 0x41, 0x51)
MUTED_COLOR = WM.Color.FromRgb(0x9a, 0xa0, 0xac)
LINE_COLOR  = WM.Color.FromRgb(0xf0, 0xf1, 0xff)
SEL_BG      = WM.Color.FromArgb(0x16, 0x44, 0xb8, 0xd3)

# Above this many structural floors in a scope box, the per-floor geometry
# analysis (_gather_floor_footprints) starts taking long enough to be worth
# flagging in the scope-box list before the user selects it — not a hard
# limit, just an early heads-up. Originally guessed at 60 before the
# vertical-extrusion/2D-zone-logic/capped-triangulation robustness work;
# real-world testing since then ran 97 floors smoothly, so this has been
# raised. Still just a rough heads-up, not benchmarked against a hard number
# — the real cost driver is how many floors need the triangulated fallback
# (warped/imported geometry), not raw floor count.
SLOW_FLOOR_COUNT_WARNING = 300


def _color(c):
    return WM.SolidColorBrush(c)


class HeadHeightCheckDialog(object):

    def __init__(self, struct_links, scope_boxes):
        self._struct_links = struct_links     # list of link-info dicts
        self._scope_boxes  = scope_boxes       # list of DB.Element (Volume of Interest)
        self._sel_scope_name = None

        self._floor_cache = {}   # scope_name -> list of footprint dicts
        self._zones_cache = {}   # scope_name -> list of zone dicts (edits persist)
        self._zones = []
        self._conflicts_preview = []

        self._done = False
        self._result_summary = None

        self._link_rows  = []
        self._scope_rows = []

        self.cancelled = True
        self._window   = None

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self):
        ctx = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        w.FindName(u"CloseBtn").Click  += lambda s, e: w.Close()
        w.FindName(u"CancelBtn").Click += self._on_cancel
        w.FindName(u"RunBtn").Click    += self._on_run
        w.FindName(u"AgainBtn").Click  += self._on_again
        w.FindName(u"OpenBtn").Click   += self._on_open_view
        w.FindName(u"ToggleAllLinksBtn").Click += self._on_toggle_all_links

        clearance_box = w.FindName(u"ClearanceBox")
        clearance_box.Text = str(DEFAULT_CLEARANCE)
        clearance_box.TextChanged += self._on_clearance_changed

        self._populate_links()
        self._populate_scope_boxes()
        self._on_clearance_changed(clearance_box, None)
        # Deliberately do NOT auto-select/auto-analyze a scope box here. Floor
        # gathering + zone analysis (_refresh_scope_analysis) can be expensive
        # on a large/complex scope box, and running it unconditionally the
        # instant the dialog opens — before the user has chosen anything, with
        # no progress feedback or chance to pick a smaller scope box first —
        # can freeze Revit long enough to look like a crash on a big project.
        # Let the user's own click into _select_scope kick it off instead.
        # _rebuild_zone_rows() itself is cheap (pure UI, no geometry) and, with
        # no scope selected yet, collapses the special-zones block entirely
        # instead of leaving an empty header row that pads out the layout.
        self._rebuild_zone_rows()
        self._update_footer_summary()
        self._update_run_enabled()
        return window

    # ── Structural links ─────────────────────────────────────────────────────

    def _populate_links(self):
        panel = self._window.FindName(u"LinksListPanel")
        panel.Children.Clear()
        self._link_rows = []
        for i, info in enumerate(self._struct_links):
            is_last = (i == len(self._struct_links) - 1)
            row_data = self._make_link_row(info, is_last)
            panel.Children.Add(row_data[u"border"])
            self._link_rows.append(row_data)
        self._update_links_label()
        self._update_no_links_banner()

    def _make_link_row(self, info, is_last):
        loaded = info[u"loaded"]

        outer = WC.Border()
        outer.Padding = System.Windows.Thickness(12, 10, 12, 10)
        outer.Background = WM.Brushes.White if loaded else _color(WM.Color.FromRgb(0xfa, 0xfb, 0xfc))
        outer.Opacity = 1.0 if loaded else 0.62
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        c1 = WC.ColumnDefinition(); c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c2 = WC.ColumnDefinition(); c2.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c1)
        grid.ColumnDefinitions.Add(c2)

        left = WC.StackPanel()
        left.Orientation = WC.Orientation.Horizontal
        WC.Grid.SetColumn(left, 0)

        cb_outer = WC.Border()
        cb_outer.Width = 18
        cb_outer.Height = 18
        cb_outer.CornerRadius = System.Windows.CornerRadius(5)
        cb_outer.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Margin = System.Windows.Thickness(0, 0, 10, 0)
        cb_outer.Cursor = WI.Cursors.Hand if loaded else WI.Cursors.No

        cb_check = WC.TextBlock()
        cb_check.Text = u"✓"
        cb_check.FontSize = 11
        cb_check.FontWeight = System.Windows.FontWeights.Bold
        cb_check.Foreground = WM.Brushes.White
        cb_check.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb_check.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Child = cb_check

        texts = WC.StackPanel()
        name_tb = WC.TextBlock()
        name_tb.Text = info[u"name"]
        name_tb.FontFamily = WM.FontFamily(u"Consolas")
        name_tb.FontSize = 12.5
        name_tb.FontWeight = System.Windows.FontWeights.SemiBold
        name_tb.Foreground = _color(BODY_COLOR) if loaded else _color(MUTED_COLOR)
        disc_tb = WC.TextBlock()
        if info[u"disc"] != u"Discipline not detected from name":
            disc_tb.Text = info[u"disc"]
            disc_tb.FontSize = 11.5
            disc_tb.Foreground = _color(MUTED_COLOR)
            disc_tb.Margin = System.Windows.Thickness(0, 1, 0, 0)
        else:
            disc_tb.Visibility = System.Windows.Visibility.Collapsed
        texts.Children.Add(name_tb)
        texts.Children.Add(disc_tb)

        left.Children.Add(cb_outer)
        left.Children.Add(texts)

        right = WC.StackPanel()
        right.Orientation = WC.Orientation.Horizontal
        right.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
        right.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(right, 1)

        status_tb = WC.TextBlock()
        status_tb.Text = u"Loaded" if loaded else u"Unloaded"
        status_tb.FontSize = 11.5
        status_tb.Foreground = _color(GREEN_COLOR) if loaded else _color(MUTED_COLOR)
        right.Children.Add(status_tb)

        grid.Children.Add(left)
        grid.Children.Add(right)
        outer.Child = grid

        row_data = {u"info": info, u"border": outer, u"cb_outer": cb_outer, u"cb_check": cb_check}

        def _refresh_cb(rd=row_data):
            on = rd[u"info"][u"on"]
            if on:
                rd[u"cb_outer"].Background = _color(NAVY_COLOR)
                rd[u"cb_outer"].BorderBrush = _color(NAVY_COLOR)
                rd[u"cb_outer"].BorderThickness = System.Windows.Thickness(1.5)
                rd[u"cb_check"].Visibility = System.Windows.Visibility.Visible
            else:
                rd[u"cb_outer"].Background = WM.Brushes.White
                rd[u"cb_outer"].BorderBrush = _color(WM.Color.FromRgb(0xc6, 0xcb, 0xe0))
                rd[u"cb_outer"].BorderThickness = System.Windows.Thickness(1.5)
                rd[u"cb_check"].Visibility = System.Windows.Visibility.Collapsed

        row_data[u"refresh_cb"] = _refresh_cb
        _refresh_cb()

        if loaded:
            def on_click(s, e, rd=row_data):
                rd[u"info"][u"on"] = not rd[u"info"][u"on"]
                rd[u"refresh_cb"]()
                self._update_links_label()
                self._update_run_enabled()
                self._update_footer_summary()
            outer.MouseLeftButtonUp += on_click

        return row_data

    def _update_links_label(self):
        loadable = [r for r in self._link_rows if r[u"info"][u"loaded"]]
        sel_count = sum(1 for r in loadable if r[u"info"][u"on"])
        self._window.FindName(u"LinksLabel").Text = (
            u"LINKED MODELS · {} OF {} SELECTED".format(sel_count, len(loadable)))
        all_on = all(r[u"info"][u"on"] for r in loadable) if loadable else False
        toggle_btn = self._window.FindName(u"ToggleAllLinksBtn")
        toggle_btn.Content = u"Deselect all" if all_on else u"Select all"
        toggle_btn.Visibility = (System.Windows.Visibility.Collapsed if not loadable
                                  else System.Windows.Visibility.Visible)

    def _update_no_links_banner(self):
        loadable = [r for r in self._link_rows if r[u"info"][u"loaded"]]
        banner = self._window.FindName(u"NoLinksBanner")
        card = self._window.FindName(u"LinksCard")
        if loadable:
            banner.Visibility = System.Windows.Visibility.Collapsed
            card.Visibility = System.Windows.Visibility.Visible
        else:
            banner.Visibility = System.Windows.Visibility.Visible
            card.Visibility = System.Windows.Visibility.Collapsed

    def _on_toggle_all_links(self, s, e):
        loadable = [r for r in self._link_rows if r[u"info"][u"loaded"]]
        all_on = all(r[u"info"][u"on"] for r in loadable) if loadable else False
        new_state = not all_on
        for r in loadable:
            r[u"info"][u"on"] = new_state
            r[u"refresh_cb"]()
        self._update_links_label()
        self._update_run_enabled()
        self._update_footer_summary()

    # ── Scope box ────────────────────────────────────────────────────────────

    def _populate_scope_boxes(self):
        panel = self._window.FindName(u"ScopeListPanel")
        panel.Children.Clear()
        self._scope_rows = []
        for i, box in enumerate(self._scope_boxes):
            is_last = (i == len(self._scope_boxes) - 1)
            row = self._make_scope_row(box, is_last)
            panel.Children.Add(row[u"border"])
            self._scope_rows.append(row)

    def _make_scope_row(self, box, is_last):
        border = WC.Border()
        border.Padding = System.Windows.Thickness(10, 9, 10, 9)
        border.Background = WM.Brushes.Transparent
        border.Cursor = WI.Cursors.Hand
        if not is_last:
            border.BorderBrush = _color(LINE_COLOR)
            border.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        row = WC.StackPanel()   # vertical: top line (icon/name/extent/count) + optional warning line

        top_row = WC.StackPanel()
        top_row.Orientation = WC.Orientation.Horizontal

        icon_tb = WC.TextBlock()
        icon_tb.Text = u"○"
        icon_tb.FontSize = 14
        icon_tb.Foreground = _color(GRAY_COLOR)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        icon_tb.Margin = System.Windows.Thickness(0, 0, 10, 0)

        name_tb = WC.TextBlock()
        name_tb.Text = box.Name
        name_tb.FontFamily = WM.FontFamily(u"Consolas")
        name_tb.FontSize = 12.5
        name_tb.FontWeight = System.Windows.FontWeights.SemiBold
        name_tb.Foreground = _color(NAVY_COLOR)
        name_tb.Width = 130
        name_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center

        extent_tb = WC.TextBlock()
        extent_tb.Text = _scope_extent_label(box)
        extent_tb.FontSize = 12.5
        extent_tb.Foreground = _color(MUTED_COLOR)
        extent_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center

        count_tb = WC.TextBlock()
        floor_count = None
        try:
            floor_count = _count_floors_in_scope(box, self._struct_links)
            count_tb.Text = u" · {} floor(s)".format(floor_count)
            count_tb.Foreground = _color(
                AMBER_COLOR if floor_count > SLOW_FLOOR_COUNT_WARNING else MUTED_COLOR)
        except Exception:
            count_tb.Text = u""
        count_tb.FontSize = 12.5
        count_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center

        top_row.Children.Add(icon_tb)
        top_row.Children.Add(name_tb)
        top_row.Children.Add(extent_tb)
        top_row.Children.Add(count_tb)
        row.Children.Add(top_row)

        if floor_count is not None and floor_count > SLOW_FLOOR_COUNT_WARNING:
            warn_tb = WC.TextBlock()
            warn_tb.Text = u"Large scope — the plugin may run slowly or behave unreliably."
            warn_tb.FontSize = 11.5
            warn_tb.Foreground = _color(AMBER_COLOR)
            warn_tb.TextWrapping = System.Windows.TextWrapping.Wrap
            warn_tb.Margin = System.Windows.Thickness(24, 2, 0, 0)
            row.Children.Add(warn_tb)

        border.Child = row

        row_data = {u"box": box, u"border": border, u"icon_tb": icon_tb}

        def on_click(s, e, rd=row_data):
            self._select_scope(rd[u"box"].Name)

        border.MouseLeftButtonUp += on_click
        return row_data

    def _update_scope_selection_ui(self):
        for rd in self._scope_rows:
            selected = (rd[u"box"].Name == self._sel_scope_name)
            if selected:
                rd[u"border"].Background = _color(SEL_BG)
                rd[u"icon_tb"].Text = u"✓"
                rd[u"icon_tb"].Foreground = _color(CYAN_COLOR)
            else:
                rd[u"border"].Background = WM.Brushes.Transparent
                rd[u"icon_tb"].Text = u"○"
                rd[u"icon_tb"].Foreground = _color(GRAY_COLOR)

    def _scope_box_by_name(self, name):
        for b in self._scope_boxes:
            if b.Name == name:
                return b
        return None

    def _select_scope(self, name):
        self._sel_scope_name = name
        self._refresh_scope_analysis()
        self._update_scope_selection_ui()
        self._rebuild_zone_rows()
        self._update_footer_summary()
        self._update_run_enabled()

    def _refresh_scope_analysis(self):
        name = self._sel_scope_name
        scope = self._scope_box_by_name(name)
        if scope is None:
            self._zones = []
            self._conflicts_preview = []
            return
        if name not in self._floor_cache:
            self._floor_cache[name] = _gather_floor_footprints(scope, self._struct_links)
        if name not in self._zones_cache:
            self._zones_cache[name] = _analyze_zones(scope, self._floor_cache[name])
        self._zones = self._zones_cache[name]
        self._refresh_conflicts_preview()

    # ── Special zones ────────────────────────────────────────────────────────

    def _rebuild_zone_rows(self):
        w = self._window
        panel = w.FindName(u"ZonesListPanel")
        panel.Children.Clear()
        card = w.FindName(u"ZonesCard")
        no_zones_card = w.FindName(u"NoZonesCard")
        header_grid = w.FindName(u"ZonesHeaderGrid")

        if not self._sel_scope_name:
            # No scope box picked yet — nothing meaningful to show here, so
            # collapse the whole block rather than leaving an empty header
            # row that just adds a gap before the next section.
            header_grid.Visibility = System.Windows.Visibility.Collapsed
            card.Visibility = System.Windows.Visibility.Collapsed
            no_zones_card.Visibility = System.Windows.Visibility.Collapsed
            self._rebuild_conflicts_preview_ui()
            return
        header_grid.Visibility = System.Windows.Visibility.Visible

        scope_suffix = u" IN {}".format(self._sel_scope_name.upper()) if self._sel_scope_name else u""
        w.FindName(u"ZonesLabel").Text = u"SPECIAL ZONES · FROM MEP SPACES{}".format(scope_suffix)

        if not self._zones:
            card.Visibility = System.Windows.Visibility.Collapsed
            no_zones_card.Visibility = System.Windows.Visibility.Visible
            w.FindName(u"ZonesCountTB").Text = u""
            self._rebuild_conflicts_preview_ui()
            return

        no_zones_card.Visibility = System.Windows.Visibility.Collapsed
        card.Visibility = System.Windows.Visibility.Visible

        for i, z in enumerate(self._zones):
            is_last = (i == len(self._zones) - 1)
            row = self._make_zone_row(z, is_last)
            panel.Children.Add(row[u"border"])

        self._update_zones_count()
        self._rebuild_conflicts_preview_ui()

    def _update_zones_count(self):
        applied = sum(1 for z in self._zones if z[u"on"])
        self._window.FindName(u"ZonesCountTB").Text = u"{} of {} applied".format(applied, len(self._zones))

    def _make_zone_row(self, zone, is_last):
        outer = WC.Border()
        outer.Padding = System.Windows.Thickness(11, 9, 11, 9)
        outer.Background = WM.Brushes.Transparent
        outer.Opacity = 1.0 if zone[u"on"] else 0.62
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        c1 = WC.ColumnDefinition(); c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        c2 = WC.ColumnDefinition(); c2.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c3 = WC.ColumnDefinition(); c3.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        grid.ColumnDefinitions.Add(c1)
        grid.ColumnDefinitions.Add(c2)
        grid.ColumnDefinitions.Add(c3)

        cb_outer = WC.Border()
        cb_outer.Width = 18
        cb_outer.Height = 18
        cb_outer.CornerRadius = System.Windows.CornerRadius(5)
        cb_outer.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Margin = System.Windows.Thickness(0, 0, 10, 0)
        cb_outer.Cursor = WI.Cursors.Hand
        WC.Grid.SetColumn(cb_outer, 0)

        cb_check = WC.TextBlock()
        cb_check.Text = u"✓"
        cb_check.FontSize = 11
        cb_check.FontWeight = System.Windows.FontWeights.Bold
        cb_check.Foreground = WM.Brushes.White
        cb_check.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb_check.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Child = cb_check

        texts = WC.StackPanel()
        WC.Grid.SetColumn(texts, 1)
        texts.VerticalAlignment = System.Windows.VerticalAlignment.Center
        name_tb = WC.TextBlock()
        name_tb.Text = zone[u"name"]
        name_tb.FontSize = 13
        name_tb.Foreground = _color(BODY_COLOR)
        name_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        texts.Children.Add(name_tb)
        if zone[u"overlap"] == u"partial":
            sub_tb = WC.TextBlock()
            sub_tb.Text = u"partial overlap · 2 shapes generated"
            sub_tb.FontSize = 11
            sub_tb.Foreground = _color(MUTED_COLOR)
            sub_tb.Margin = System.Windows.Thickness(0, 1, 0, 0)
            texts.Children.Add(sub_tb)

        height_box = WC.TextBox()
        height_box.Text = str(int(zone[u"height_mm"]))
        height_box.Width = 74
        height_box.Height = 30
        height_box.FontFamily = WM.FontFamily(u"Consolas")
        height_box.FontSize = 12.5
        height_box.FontWeight = System.Windows.FontWeights.SemiBold
        height_box.Foreground = _color(NAVY_COLOR)
        height_box.TextAlignment = System.Windows.TextAlignment.Right
        height_box.VerticalContentAlignment = System.Windows.VerticalAlignment.Center
        height_box.Padding = System.Windows.Thickness(6, 0, 6, 0)
        height_box.BorderBrush = _color(LINE_COLOR)
        height_box.BorderThickness = System.Windows.Thickness(1)
        height_box.IsEnabled = zone[u"on"]
        WC.Grid.SetColumn(height_box, 2)

        row_data = {u"zone": zone, u"border": outer, u"cb_outer": cb_outer,
                    u"cb_check": cb_check, u"height_box": height_box}

        def _refresh_cb(rd=row_data):
            on = rd[u"zone"][u"on"]
            if on:
                rd[u"cb_outer"].Background = _color(NAVY_COLOR)
                rd[u"cb_outer"].BorderBrush = _color(NAVY_COLOR)
                rd[u"cb_check"].Visibility = System.Windows.Visibility.Visible
            else:
                rd[u"cb_outer"].Background = WM.Brushes.White
                rd[u"cb_outer"].BorderBrush = _color(WM.Color.FromRgb(0xc6, 0xcb, 0xe0))
                rd[u"cb_check"].Visibility = System.Windows.Visibility.Collapsed
            rd[u"height_box"].IsEnabled = on
            rd[u"border"].Opacity = 1.0 if on else 0.62

        def on_toggle(s, e, rd=row_data):
            rd[u"zone"][u"on"] = not rd[u"zone"][u"on"]
            _refresh_cb()
            self._update_zones_count()
            self._refresh_conflicts_preview()
            self._rebuild_conflicts_preview_ui()

        def on_height_changed(s, e, rd=row_data):
            try:
                v = int(float(s.Text))
                if v > 0:
                    rd[u"zone"][u"height_mm"] = v
            except Exception:
                pass
            self._refresh_conflicts_preview()
            self._rebuild_conflicts_preview_ui()

        cb_outer.MouseLeftButtonUp += on_toggle
        height_box.LostFocus += on_height_changed
        _refresh_cb()

        grid.Children.Add(cb_outer)
        grid.Children.Add(texts)
        grid.Children.Add(height_box)
        outer.Child = grid
        return row_data

    def _refresh_conflicts_preview(self):
        footprints = self._floor_cache.get(self._sel_scope_name, [])
        candidates = [(z[u"space"], z[u"height_mm"]) for z in self._zones if z[u"on"]]
        default_mm = self._read_clearance_mm()
        conflicts = []
        space_col_cache = {}
        for f in footprints:
            _, conflict = resolve_height(
                f[u"loops"], candidates, default_mm, f[u"face"], f[u"link_transform"],
                space_col_cache=space_col_cache)
            if conflict:
                conflicts.append(f[u"label"])
        self._conflicts_preview = conflicts

    def _rebuild_conflicts_preview_ui(self):
        panel = self._window.FindName(u"ConflictsPreviewPanel")
        panel.Children.Clear()
        for label in self._conflicts_preview:
            panel.Children.Add(self._make_conflict_row(label))

    def _make_conflict_row(self, label):
        border = WC.Border()
        border.Background = _color(WM.Color.FromArgb(0x66, 0xfe, 0xf3, 0xc7))
        border.BorderBrush = _color(WM.Color.FromArgb(0x48, 0xd9, 0xa4, 0x06))
        border.BorderThickness = System.Windows.Thickness(1)
        border.CornerRadius = System.Windows.CornerRadius(8)
        border.Padding = System.Windows.Thickness(11, 8, 11, 8)
        border.Margin = System.Windows.Thickness(0, 0, 0, 6)

        row = WC.Grid()
        c1 = WC.ColumnDefinition(); c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
        c2 = WC.ColumnDefinition(); c2.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        row.ColumnDefinitions.Add(c1)
        row.ColumnDefinitions.Add(c2)

        icon_tb = WC.TextBlock()
        icon_tb.Text = u"⚠"
        icon_tb.FontSize = 13
        icon_tb.Foreground = _color(AMBER_COLOR)
        icon_tb.Margin = System.Windows.Thickness(0, 0, 9, 0)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(icon_tb, 0)

        text_tb = WC.TextBlock()
        text_tb.Text = u"{} · conflicting Space values — will be flagged".format(label)
        text_tb.FontSize = 12
        text_tb.Foreground = _color(WM.Color.FromRgb(0x8a, 0x6d, 0x1a))
        text_tb.TextWrapping = System.Windows.TextWrapping.Wrap
        WC.Grid.SetColumn(text_tb, 1)

        row.Children.Add(icon_tb)
        row.Children.Add(text_tb)
        border.Child = row
        return {u"border": border}

    # ── Default clearance ────────────────────────────────────────────────────

    def _read_clearance_mm(self):
        box = self._window.FindName(u"ClearanceBox")
        try:
            val = int(float(box.Text))
            if val > 0:
                return val
        except Exception:
            pass
        return DEFAULT_CLEARANCE

    def _on_clearance_changed(self, s, e):
        mm = self._read_clearance_mm()
        helper = self._window.FindName(u"ClearanceHelperTB")
        helper.Text = u"= {} cm measured normal to the walking surface.".format(int(mm / 10))

    # ── Footer / run gate ────────────────────────────────────────────────────

    def _update_footer_summary(self):
        if self._done:
            return
        n = sum(1 for l in self._struct_links if l[u"loaded"] and l[u"on"])
        scope_txt = self._sel_scope_name or u"no scope"
        self._window.FindName(u"FooterSummaryTB").Text = u"{} link{} · {}".format(
            n, u"" if n == 1 else u"s", scope_txt)

    def _update_run_enabled(self):
        run_btn = self._window.FindName(u"RunBtn")
        loaded_on = [l for l in self._struct_links if l[u"loaded"] and l[u"on"]]
        run_btn.IsEnabled = bool(self._sel_scope_name) and len(loaded_on) > 0

    # ── Run / results ────────────────────────────────────────────────────────

    def _on_cancel(self, s, e):
        self.cancelled = True
        self._window.Close()

    def _on_run(self, s, e):
        w = self._window
        run_btn = w.FindName(u"RunBtn")
        run_btn.IsEnabled = False
        run_btn.Content = u"Running..."

        footprints = self._floor_cache.get(self._sel_scope_name, [])
        candidate_spaces = [(z[u"space"], z[u"height_mm"]) for z in self._zones if z[u"on"]]
        clear_previous = bool(w.FindName(u"ClearToggle").IsChecked)
        default_mm = self._read_clearance_mm()
        scope_box = self._scope_box_by_name(self._sel_scope_name)

        try:
            summary = run(footprints, self._struct_links, candidate_spaces, default_mm,
                          clear_previous, scope_box)
            self._result_summary = summary
            self._show_results(summary, default_mm)
            self.cancelled = False
            try:
                self._window.Topmost = True
                self._window.Activate()
                self._window.Topmost = False
            except Exception:
                pass
        except Exception:
            run_btn.IsEnabled = True
            run_btn.Content = u"▶  Run check"
            forms.alert(
                u"Head Height Check failed:\n\n{}".format(traceback.format_exc()),
                title=u"EasyBIM — Error")

    def _show_results(self, summary, default_mm):
        self._done = True
        w = self._window
        vis = System.Windows.Visibility.Visible
        col = System.Windows.Visibility.Collapsed

        w.FindName(u"ConfigPanel").Visibility = col
        w.FindName(u"ResultsPanel").Visibility = vis

        w.FindName(u"ResSummaryTB").Text = u"{} clearance volumes on “{}” · scope {}".format(
            summary[u"created"], WORKSET, self._sel_scope_name)

        w.FindName(u"StatFloorsTB").Text = str(summary[u"floors"])
        w.FindName(u"StatCreatedTB").Text = str(summary[u"created"])
        w.FindName(u"StatZonesTB").Text = str(sum(1 for z in self._zones if z[u"on"]))
        conflicts = summary[u"conflicts"]
        conflicts_tb = w.FindName(u"StatConflictsTB")
        conflicts_tb.Text = str(len(conflicts))
        conflicts_tb.Foreground = _color(AMBER_COLOR) if conflicts else _color(MUTED_COLOR)

        hb = w.FindName(u"HeightBreakdownPanel")
        hb.Children.Clear()
        zone_usage = summary.get(u"zone_usage", {})
        default_bucket = zone_usage.get(default_mm, {u"count": 0, u"partial": False})
        applied_zones = [z for z in self._zones if z[u"on"]]
        hb.Children.Add(self._make_breakdown_row(
            u"Project default", u"{} floors".format(default_bucket[u"count"]),
            default_mm, False, is_last=(len(applied_zones) == 0)))
        for i, z in enumerate(applied_zones):
            bucket = zone_usage.get(z[u"height_mm"])
            is_partial = bool(bucket and bucket[u"partial"])
            hb.Children.Add(self._make_breakdown_row(
                z[u"name"], None, z[u"height_mm"], is_partial,
                is_last=(i == len(applied_zones) - 1)))

        section = w.FindName(u"ResultConflictsSection")
        cp = w.FindName(u"ResultConflictsPanel")
        cp.Children.Clear()
        if conflicts:
            section.Visibility = vis
            for label in conflicts:
                cp.Children.Add(self._make_conflict_row(label)[u"border"])
        else:
            section.Visibility = col

        existed = summary.get(u"view_existed", False)
        w.FindName(u"ViewStatusRun").Text = (
            u" updated · isolated to + Mass." if existed else u" created · isolated to + Mass.")

        w.FindName(u"RunBtn").Visibility = col
        w.FindName(u"CancelBtn").Visibility = col
        w.FindName(u"AgainBtn").Visibility = vis
        w.FindName(u"OpenBtn").Visibility = vis
        w.FindName(u"FooterSummaryTB").Text = u""

    def _make_breakdown_row(self, name, count_text, height_mm, is_partial, is_last):
        border = WC.Border()
        border.Padding = System.Windows.Thickness(12, 9, 12, 9)
        if not is_last:
            border.BorderBrush = _color(LINE_COLOR)
            border.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        for w_def in (System.Windows.GridUnitType.Auto, System.Windows.GridUnitType.Star,
                      System.Windows.GridUnitType.Auto, System.Windows.GridUnitType.Auto):
            cd = WC.ColumnDefinition()
            cd.Width = System.Windows.GridLength(1, w_def)
            grid.ColumnDefinitions.Add(cd)

        icon_tb = WC.TextBlock()
        icon_tb.Text = u"■"
        icon_tb.FontSize = 11
        icon_tb.Foreground = _color(NAVY_COLOR if count_text is not None else CYAN_COLOR)
        icon_tb.Margin = System.Windows.Thickness(0, 0, 10, 0)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(icon_tb, 0)

        name_tb = WC.TextBlock()
        name_tb.Text = name
        name_tb.FontSize = 13
        name_tb.Foreground = _color(BODY_COLOR)
        name_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        name_tb.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
        WC.Grid.SetColumn(name_tb, 1)

        grid.Children.Add(icon_tb)
        grid.Children.Add(name_tb)

        if count_text is not None:
            count_tb = WC.TextBlock()
            count_tb.Text = count_text
            count_tb.FontFamily = WM.FontFamily(u"Consolas")
            count_tb.FontSize = 12.5
            count_tb.Foreground = _color(MUTED_COLOR)
            count_tb.Margin = System.Windows.Thickness(0, 0, 10, 0)
            count_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
            WC.Grid.SetColumn(count_tb, 2)
            grid.Children.Add(count_tb)
        elif is_partial:
            badge = WC.Border()
            badge.Background = _color(WM.Color.FromRgb(0xec, 0xf8, 0xfc))
            badge.BorderBrush = _color(WM.Color.FromRgb(0xb8, 0xe8, 0xf2))
            badge.BorderThickness = System.Windows.Thickness(1)
            badge.CornerRadius = System.Windows.CornerRadius(10)
            badge.Padding = System.Windows.Thickness(7, 2, 7, 2)
            badge.Margin = System.Windows.Thickness(0, 0, 10, 0)
            badge_tb = WC.TextBlock()
            badge_tb.Text = u"partial · 2 shapes"
            badge_tb.FontSize = 10.5
            badge_tb.Foreground = _color(CYAN_COLOR)
            badge_tb.FontWeight = System.Windows.FontWeights.SemiBold
            badge.Child = badge_tb
            WC.Grid.SetColumn(badge, 2)
            grid.Children.Add(badge)

        height_tb = WC.TextBlock()
        height_tb.Text = u"{} mm".format(int(height_mm))
        height_tb.FontFamily = WM.FontFamily(u"Consolas")
        height_tb.FontSize = 12.5
        height_tb.FontWeight = System.Windows.FontWeights.Bold
        height_tb.Foreground = _color(NAVY_COLOR)
        height_tb.Width = 66
        height_tb.TextAlignment = System.Windows.TextAlignment.Right
        height_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(height_tb, 3)
        grid.Children.Add(height_tb)

        border.Child = grid
        return border

    def _on_again(self, s, e):
        self._done = False
        self._result_summary = None
        w = self._window
        vis = System.Windows.Visibility.Visible
        col = System.Windows.Visibility.Collapsed
        w.FindName(u"ConfigPanel").Visibility = vis
        w.FindName(u"ResultsPanel").Visibility = col
        w.FindName(u"RunBtn").Visibility = vis
        w.FindName(u"CancelBtn").Visibility = vis
        w.FindName(u"AgainBtn").Visibility = col
        w.FindName(u"OpenBtn").Visibility = col
        run_btn = w.FindName(u"RunBtn")
        run_btn.Content = u"▶  Run check"
        self._update_run_enabled()
        self._update_footer_summary()

    def _on_open_view(self, s, e):
        try:
            view = find_view3d_by_name(VIEW_3D)
            if view is not None:
                uidoc = revit.uidoc
                uidoc.ActiveView = view
        except Exception:
            pass
        self._window.Close()

    # ── Show ─────────────────────────────────────────────────────────────────

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
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
    t0 = DB.Transaction(doc, u"EasyBIM: Ensure clearance parameter")
    t0.Start()
    try:
        ensure_clearance_param()
        t0.Commit()
    except Exception:
        t0.RollBack()
        forms.alert(
            u"Could not bind the 'Required Clearance Height' shared parameter:\n\n{}".format(
                traceback.format_exc()),
            title=u"EasyBIM — Error", exitscript=True)

    struct_links = get_available_links()
    if not struct_links:
        forms.alert(
            u"No Revit links found in this model. Load the link containing your structural floors and try again.",
            title=u"EasyBIM", exitscript=True)

    scope_boxes = get_scope_boxes()
    if not scope_boxes:
        forms.alert(
            u"No scope boxes found in the host model. Create one first.",
            title=u"EasyBIM", exitscript=True)

    dlg = HeadHeightCheckDialog(struct_links, scope_boxes)
    try:
        dlg.show()
    except Exception:
        forms.alert(
            u"Head Height Check failed while building the dialog:\n\n{}".format(
                traceback.format_exc()),
            title=u"EasyBIM — Error", exitscript=True)

    if dlg.cancelled:
        return

    if dlg._result_summary:
        forms.alert(
            u"{} clearance volumes created. View '{}' updated.".format(
                dlg._result_summary.get(u"created", 0), VIEW_3D),
            title=u"EasyBIM — Done")


main()
