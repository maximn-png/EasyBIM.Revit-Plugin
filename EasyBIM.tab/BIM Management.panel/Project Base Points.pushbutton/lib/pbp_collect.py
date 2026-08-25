# -*- coding: utf-8 -*-
"""Reads the host model's and every directly-placed link's Project Base Point,
expressed in the HOST's shared coordinate system (N/S, E/W, Elev in meters;
angle-to-true-north in degrees) — produces rows shaped like PBP_ROWS in the
design handoff's reference/ribbon-data.jsx.

*** NEW REVIT-API GROUND, PARTIALLY VALIDATED ***
Nothing else in this extension computes angle-to-true-north or checks
acquired/shared-coordinate state, so the helpers below (_host_angle_deg /
_z_rotation_deg and _link_shared_state) had no working precedent to copy in
this codebase. _host_angle_deg's sign was checked against a real project and
fixed (see its own docstring: ProjectPosition.Angle runs opposite to Revit's
displayed "Angle to True North"). _z_rotation_deg's sign and
_link_shared_state are still only implemented against the documented API,
not independently confirmed against a real project with a meaningfully
rotated (not just offset) link or a genuinely "Not Shared" one -- please
cross-check those specifically against Revit's own Coordinates dialogs
before relying on this for a real ACC export.

PBP position: each document's actual Project Base Point element (via
DB.BasePoint.GetProjectBasePoint) is looked up and used as the point being
positioned/transformed -- NOT assumed to sit at that document's internal
origin (0,0,0). An earlier version made that (0,0,0) assumption, matching
Check Levels' get_pbp_elevation(); it was confirmed wrong on a real project
where the PBP had been deliberately relocated to match a site survey (the
Survey Point's own, distinctly non-zero elevation was the giveaway -- see
pbp_ui.py's "Survey elev (m)" warning column). Falls back to (0,0,0) only if
the BasePoint element itself can't be found, which should not happen on a
normal Revit document.
"""
import math
import os
import re

import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB

MM_PER_FT = 304.8
_REVVER_RE = re.compile(r'^[Rr]\d{2,4}$')

# Revit appends " : <id> : location <site name>" (or the literal
# "<Not Shared>") to a RevitLinkInstance's own Name when the project defines
# multiple named Sites / shared coordinate systems (Manage > Coordinates).
# This is Revit telling us the shared-coordinates state directly -- more
# reliable than reconstructing it from ProjectLocation names (see
# _link_shared_state below, kept only as a fallback for single-site projects
# that never get this suffix at all).
_LOCATION_SUFFIX_RE = re.compile(r'^(.*?)\s*:\s*\d+\s*:\s*location\s+(.+?)\s*$', re.IGNORECASE)
_NOT_SHARED_TOKENS = (u"<not shared>", u"not shared")


def _to_meters(feet):
    return feet * MM_PER_FT / 1000.0


def _host_angle_deg(doc):
    """Host's own project-north -> true-north offset, in degrees 0-360,
    matching what Revit's own Project Base Point properties show as "Angle
    to True North".

    CONFIRMED SIGN FIX: ProjectPosition.Angle (the raw Revit API value) runs
    OPPOSITE to Revit's own displayed "Angle to True North" -- verified
    against a real project where the API value read 63.90 deg while Revit's
    own Properties palette for that same point showed 296.10 deg, and
    360 - 296.10 == 63.90 exactly. Negating (mod 360) is what makes this
    match Revit's own UI. This ONLY affects the absolute angle shown to the
    user -- every OK/Not OK tolerance check compares a DIFFERENCE between
    two rows' angles, and negating both sides of a subtraction leaves its
    magnitude unchanged, so no status ever flipped because of this.
    """
    try:
        pos = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        return (-math.degrees(pos.Angle)) % 360.0
    except Exception:
        return 0.0


def _z_rotation_deg(transform):
    """Clockwise rotation (deg) of `transform`'s Y axis (a model's own project
    north) relative to the host's internal +Y (the host's own project north).
    Zero for the host's own identity transform."""
    by = transform.BasisY
    return math.degrees(math.atan2(by.X, by.Y))


def _project_base_point_position(document):
    """`document`'s own Project Base Point, in ITS OWN internal coordinate
    system -- NOT assumed to be XYZ.Zero (see module docstring). Falls back
    to XYZ.Zero (the old behavior) only if the element genuinely can't be
    found, so a lookup miss degrades gracefully instead of raising."""
    try:
        bp = DB.BasePoint.GetProjectBasePoint(document)
        if bp is not None:
            return bp.Position
    except Exception:
        pass
    return DB.XYZ.Zero


def _host_shared_position(doc, point_in_host_internal):
    """(ns, ew, el) of `point_in_host_internal` (a DB.XYZ already in the HOST
    document's internal coordinate system) in the host's shared coordinate
    system, in meters. The angle is NOT computed here -- see
    _host_angle_deg/_z_rotation_deg, which the caller combines separately.
    """
    pos = doc.ActiveProjectLocation.GetProjectPosition(point_in_host_internal)
    return _to_meters(pos.NorthSouth), _to_meters(pos.EastWest), _to_meters(pos.Elevation)


def _project_location_names(some_doc):
    names = set()
    try:
        for pl in some_doc.ProjectLocations:
            try:
                names.add(pl.Name)
            except Exception:
                pass
    except Exception:
        pass
    return names


def _split_display_name(raw_name):
    """(clean_file_name, site_name_or_None). site_name is None when the raw
    name carries no multi-site suffix at all (nothing to go on -- caller
    falls back to _link_shared_state); it's the literal parsed name
    otherwise, including the literal "<Not Shared>" token Revit itself
    writes for a link that isn't on any of the project's shared sites."""
    m = _LOCATION_SUFFIX_RE.match(raw_name)
    if not m:
        return raw_name, None
    return m.group(1).strip(), m.group(2).strip()


_CHAIN_RE = re.compile(r'\.rvt\s*:\s*', re.IGNORECASE)


def _is_chained_name(clean_name):
    """True if `clean_name` (already stripped of the " : <id> : location X"
    multi-site suffix by _split_display_name) STILL contains a
    "Parent.rvt : Child..." chain. This is Revit's own signal, visible right
    in the instance Name, that this is an ATTACHED (nested) link that its
    FilteredElementCollector(doc) still surfaces directly at the host's
    top level -- unlike OVERLAY nested links, which never appear there at
    all. Comparing GetTransform() vs GetTotalTransform() was tried first as
    a geometry-based detector and did NOT catch this in practice (Revit
    appears to already resolve the promoted instance's own GetTransform()
    to the fully-composed placement), so this name-chain check is the one
    actually relied on -- confirmed against a real project."""
    return bool(_CHAIN_RE.search(clean_name))


def _link_shared_state(doc, link_doc):
    """("Yes"/"No"/"?", site_name) — best-effort: a link counts as "shared"
    when the host and the linked document have a matching named
    ProjectLocation (i.e. coordinates were published/acquired between them at
    some point). See module docstring — flagged for validation."""
    if link_doc is None:
        return "?", ""
    try:
        host_names = _project_location_names(doc)
        link_names = _project_location_names(link_doc)
        shared = sorted(host_names & link_names)
        if not shared:
            return "No", ""
        active_name = None
        try:
            active_name = doc.ActiveProjectLocation.Name
        except Exception:
            pass
        site = active_name if active_name in shared else shared[0]
        return "Yes", site
    except Exception:
        return "?", ""


def _survey_point_elevation_m(document):
    """Elevation (m) of `document`'s OWN Survey Point instance parameter.
    NOT the same value as a row's "el" (that's the link's PBP position
    re-expressed in the HOST's shared coordinates via the link transform) --
    this is a red flag check: this whole module's PBP-position math assumes
    each document's Project Base Point sits at that document's internal
    origin (0,0,0) (see module docstring). A model whose Survey Point has a
    real-world (non-zero) elevation was very likely set up with the PBP
    relocated away from internal origin too, to match a site survey -- and
    when that's true, this tool's numbers for THAT model are wrong. Surfaced
    to the user as a warning rather than silently trusted.
    *** NEW REVIT-API GROUND, FLAGGED FOR VALIDATION *** (see module
    docstring) -- BasePoint.GetSurveyPoint + BASEPOINT_ELEVATION_PARAM have
    no other precedent in this codebase; returns None on any failure so a
    lookup miss reads as "unknown", never as a false "0"."""
    try:
        bp = DB.BasePoint.GetSurveyPoint(document)
        if bp is None:
            return None
        p = bp.get_Parameter(DB.BuiltInParameter.BASEPOINT_ELEVATION_PARAM)
        if p is None:
            return None
        return _to_meters(p.AsDouble())
    except Exception:
        return None


def _workset_name(doc, element):
    if not doc.IsWorkshared:
        return "-"
    try:
        ws = doc.GetWorksetTable().GetWorkset(element.WorksetId)
        return ws.Name
    except Exception:
        return "?"


def _units_label(doc):
    try:
        fo = doc.GetUnits().GetFormatOptions(DB.SpecTypeId.Length)
        return DB.LabelUtils.GetLabelForUnit(fo.GetUnitTypeId())
    except Exception:
        try:
            # Pre-2021 fallback (DisplayUnitType-based units API).
            fo = doc.GetUnits().GetFormatOptions(DB.UnitType.UT_Length)
            return DB.LabelUtils.GetLabelFor(fo.DisplayUnits)
        except Exception:
            return "?"


def _host_path(doc):
    try:
        if doc.IsWorkshared:
            model_path = doc.GetWorksharingCentralModelPath()
            if model_path is not None:
                return DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
    except Exception:
        pass
    return doc.PathName or "(not saved)"


def parse_link_name(filename):
    """Best-effort split of PROJECT-DISCIPLINE-FIRM-BUILDING-REVITVERSION
    (see README "Matching a link to its AR reference"). Returns
    (disc, firm, key). Never raises and never silently drops the row: on a
    parse miss, disc/key fall back to visibly-unmapped placeholders so the
    user notices and overrides them in the results table (same caution the
    README asks for, as with Check Levels' filename parsing).

    `firm` (the FIRM segment of the naming convention, e.g. "AFC" in
    "UPT-ME-AFC-Base-R25.rvt") was previously parsed and thrown away --
    surfaced now (pbp_ui.py's per-discipline table, Issues screen) as a
    hint for "who do I assign this discipline's issues to", since it's
    often the one piece of real-world identifying information already
    sitting in the file name.
    """
    stem = os.path.splitext(filename)[0]
    parts = [p for p in stem.split("-") if p != ""]

    if len(parts) >= 5:
        return parts[1].strip().upper(), parts[2].strip().upper(), parts[-2].strip().upper()

    # Loose fallback: look for a trailing "R##" revit-version token to anchor
    # the building key as the segment right before it; discipline is whatever
    # 2-letter/1-letter token appears in position 1 if present.
    disc = parts[1].strip().upper() if len(parts) > 1 else "?"
    firm = parts[2].strip().upper() if len(parts) > 2 else ""
    key = "?"
    for i, p in enumerate(parts):
        if _REVVER_RE.match(p) and i > 0:
            key = parts[i - 1].strip().upper()
            break
    if key == "?" and len(parts) >= 2:
        key = parts[-1].strip().upper()
    return disc, firm, key


def read_host_base_point(doc, links_placed_docs, extra_nested=0):
    """Return a dict shaped like PBP_HOST in reference/ribbon-data.jsx, plus
    the ns/ew/el/ang for the host's own row. `links_placed_docs` is the list
    of loaded linked Documents already collected by read_link_base_points,
    reused here to count nested (link-inside-a-link) instances. `extra_nested`
    adds in any Attachment-type nested links that read_link_base_points had
    to filter back OUT of its own top-level collector (see _is_chained_name)."""
    host_angle_deg = _host_angle_deg(doc)
    ns, ew, el = _host_shared_position(doc, _project_base_point_position(doc))

    nested = extra_nested
    for link_doc in links_placed_docs:
        try:
            nested += len(list(DB.FilteredElementCollector(link_doc).OfClass(DB.RevitLinkInstance).ToElements()))
        except Exception:
            pass

    return {
        "title": doc.Title,
        "path": _host_path(doc),
        "units": _units_label(doc),
        "workshared": bool(doc.IsWorkshared),
        "nested": nested,
        "ns": ns, "ew": ew, "el": el, "ang": host_angle_deg % 360.0,
        "host_angle_deg": host_angle_deg,
    }


def read_link_base_points(doc, host_angle_deg):
    """Return (rows, placed_docs, nested_count): rows is a list of dicts
    shaped like PBP_ROWS (kind="Link"), one per directly-placed
    RevitLinkInstance; placed_docs is the list of loaded linked Documents
    (for nested counting); nested_count is how many links this function
    itself filtered out as Attachment-type nested links surfaced by
    FilteredElementCollector(doc) despite not being true direct placements
    (see _is_chained_name) -- add to read_host_base_point's own tally.
    """
    rows = []
    placed_docs = []

    all_instances = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance).ToElements())
    instances = []
    nested_count = 0
    for li in all_instances:
        clean, _loc = _split_display_name(li.Name)
        if _is_chained_name(clean):
            nested_count += 1
        else:
            instances.append(li)

    # Disambiguate multiple placements of the same link file with "#1"/"#2",
    # keyed by the CLEAN (multi-site-suffix-stripped) name -- Revit's own
    # " : <id> :" numbering is a project-wide running id, not per-file, so it
    # can't be reused directly for this.
    name_counts = {}
    for li in instances:
        clean, _loc = _split_display_name(li.Name)
        name_counts[clean] = name_counts.get(clean, 0) + 1
    seen_so_far = {}

    for li in instances:
        raw_name = li.Name
        name, loc_name = _split_display_name(raw_name)
        link_doc = li.GetLinkDocument()
        placed = link_doc is not None
        if placed:
            placed_docs.append(link_doc)

        inst = ""
        if name_counts[name] > 1:
            seen_so_far[name] = seen_so_far.get(name, 0) + 1
            inst = "#{}".format(seen_so_far[name])

        disc, firm, key = parse_link_name(name)
        if loc_name is not None:
            # Revit told us directly (its own "location <site>" / "<Not
            # Shared>" label) -- trust this over the ProjectLocation-name
            # heuristic below, which is only a fallback for single-site
            # projects that never get this suffix at all.
            if loc_name.lower() in _NOT_SHARED_TOKENS:
                shared, site = "No", ""
            else:
                shared, site = "Yes", loc_name
        else:
            shared, site = _link_shared_state(doc, link_doc)
        workset = _workset_name(doc, li)

        if placed:
            try:
                transform = li.GetTotalTransform()
                pbp_in_link = _project_base_point_position(link_doc)
                origin_in_host = transform.OfPoint(pbp_in_link)
                ns, ew, el = _host_shared_position(doc, origin_in_host)
                ang = (host_angle_deg + _z_rotation_deg(transform)) % 360.0
            except Exception:
                ns = ew = el = ang = None
                if shared == "Yes":
                    shared = "?"
        else:
            ns = ew = el = ang = None

        survey_elev = _survey_point_elevation_m(link_doc) if placed else None

        rows.append({
            "id": None,  # assigned by read_all()
            "link": name, "inst": inst, "kind": "Link",
            "disc": disc, "key": key, "firm": firm,
            "shared": shared, "site": site, "workset": workset,
            "ns": ns, "ew": ew, "el": el, "ang": ang,
            "survey_elev": survey_elev,
            "placed": placed,
        })

    return rows, placed_docs, nested_count


def read_all(doc):
    """Convenience entry point used by script.py: collects the host row + all
    directly-placed link rows and assigns sequential ids (host = 0), matching
    the PBP_ROWS shape. Returns (host_info, rows)."""
    host_angle_deg = _host_angle_deg(doc)
    link_rows, placed_docs, nested_count = read_link_base_points(doc, host_angle_deg)
    host_info = read_host_base_point(doc, placed_docs, extra_nested=nested_count)

    host_row = {
        "id": 0, "link": host_info["title"], "inst": "", "kind": "HOST",
        "disc": "-", "key": "-", "firm": "-", "shared": "-", "site": "-", "workset": "-",
        "ns": host_info["ns"], "ew": host_info["ew"], "el": host_info["el"],
        "ang": host_info["ang"], "survey_elev": _survey_point_elevation_m(doc), "placed": True,
    }
    rows = [host_row]
    for i, r in enumerate(link_rows):
        r["id"] = i + 1
        rows.append(r)
    return host_info, rows
