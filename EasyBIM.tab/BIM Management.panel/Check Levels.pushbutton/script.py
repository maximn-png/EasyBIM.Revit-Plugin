# -*- coding: utf-8 -*-
"""
Level Coordination Review — pyRevit Script
WPF UI (embedded XAML), xlsxwriter export, IronPython 2.7 compatible.

Comparison logic:
  True_Absolute_Z = Level.Elevation + LinkInstance.GetTotalTransform().Origin.Z
  PBP extracted from BasePoint element (IsSharedBasePoint == False)

Status logic:
  GREEN  (OK)                     — name match + True_Absolute_Z match
  RED    (Height Misalignment)    — name match + True_Absolute_Z differs
  ORANGE (Wrong Name / Unknown)   — consultant level has no name match in arch
  ORANGE (Missing in Consultant)  — arch level has no name match in consultant
                                     (names the missing level explicitly)
"""

import clr, os, traceback, re, datetime

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xml')
clr.AddReference('System.IO')
clr.AddReference('System')
clr.AddReference('System.Core')

from Autodesk.Revit import DB
from Autodesk.Revit.UI import TaskDialog

import System
import System.Windows
import System.Windows.Controls
import System.Windows.Media
import System.Windows.Input

from System.Windows.Markup import XamlReader
from System.IO             import StringReader
from System.Xml            import XmlReader as SysXmlReader

from pyrevit import revit, script, forms

try:
    import xlsxwriter
except ImportError:
    forms.alert("xlsxwriter is not installed.\nRun: pip install xlsxwriter",
                exitscript=True)

output = script.get_output()
doc    = revit.doc

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TOLERANCE = 0.0001   # feet — for elevation matching

CM = 30.48           # 1 ft → cm

# ── LevelStatus enum ───────────────────────────────────────────────────────────
# GREEN  — name match + elevation within tolerance          → fully valid
# RED    — name match found BUT elevation out of tolerance   → height error
# ORANGE — no valid name match (unknown / wrong name),
#          and no arch level shares its elevation either      → naming error
# ORANGE — no name match, BUT an arch level shares its exact
#          elevation → almost certainly the same physical      → S_WRONGNAME
#          level, just misnamed. The matching arch level's
#          name is recorded so the report can say what it
#          should be renamed to.
# ORANGE — arch level missing entirely in consultant         → S_MISSING (distinct
#          text so the report states exactly which level is missing, instead of
#          being lumped under the generic "Wrong Name / Unknown" label)

S_GREEN    = "OK"                          # green  — valid
S_RED      = "Height Misalignment"         # red    — elevation error
S_ORANGE   = "Wrong Name / Unknown"        # orange — naming, no elevation match either
S_WRONGNAME= "Wrong Name (elevation matches)"  # orange — naming, but elevation matches an arch level
S_MISSING  = "Missing in Consultant"       # orange — arch level absent in consultant

# Legacy aliases kept so chart/summary code that uses old names still works
S_OK      = S_GREEN
S_HEIGHT  = S_RED
S_NAME    = S_ORANGE
S_UNKNOWN = S_ORANGE

# Excel fill colors — 0xRRGGBB  (3 colors per spec)
HEX_GREEN  = 0x00C853   # vivid green  — OK
HEX_RED    = 0xD50000   # vivid red    — Height Misalignment
HEX_ORANGE = 0xFF9800   # vivid orange — Wrong Name / Unknown / Missing

# Legacy aliases
HEX_OK      = HEX_GREEN
HEX_HEIGHT  = HEX_RED
HEX_NAME    = HEX_ORANGE
HEX_UNKNOWN = HEX_ORANGE
HEX_MISSING = HEX_ORANGE

HEX_HEADER  = 0x2A4B7C   # dark blue (header background)


def normalize_name(name):
    """
    Normalize a level name for matching:
      strip whitespace, uppercase, collapse internal spaces.
    This prevents false mismatches from trailing spaces or case differences.
    """
    if name is None:
        return u""
    return u" ".join(name.strip().upper().split())


# ─────────────────────────────────────────────────────────────────────────────
# DISCIPLINE MAPPING  (ISO 19650 / BS 1192 / AIA + project-specific tokens)
# ─────────────────────────────────────────────────────────────────────────────

# Comprehensive token → Hebrew label map.
# Keys are UPPER-CASE; matching is always done on upper-cased input.
DISCIPLINE_MAP = {
    # Architecture
    "A"             : u"אדריכלות",
    "AR"            : u"אדריכלות",
    "ARC"           : u"אדריכלות",
    "ARCH"          : u"אדריכלות",
    # Structure / Structural
    "S"             : u"קונסטרוקציה",
    "ST"            : u"קונסטרוקציה",
    "STR"           : u"קונסטרוקציה",
    "STRUC"         : u"קונסטרוקציה",
    "STRUCT"        : u"קונסטרוקציה",
    "STRUCTURE"     : u"קונסטרוקציה",
    "STST"          : u"קונסטרוקציה",
    "7STST"         : u"קונסטרוקציה",
    # Electrical
    "E"             : u"חשמל",
    "EL"            : u"חשמל",
    "ELE"           : u"חשמל",
    "ELEC"          : u"חשמל",
    "ELECTRICAL"    : u"חשמל",
    # Plumbing
    "P"             : u"אינסטלציה",
    "PL"            : u"אינסטלציה",
    "PLM"           : u"אינסטלציה",
    "PLUMBING"      : u"אינסטלציה",
    "7STPL"         : u"אינסטלציה",
    # HVAC / Mechanical
    "M"             : u"מיזוג אוויר",
    "ME"            : u"מיזוג אוויר",
    "MEC"           : u"מיזוג אוויר",
    "MECH"          : u"מיזוג אוויר",
    "HVAC"          : u"מיזוג אוויר",
    "MECHANICAL"    : u"מיזוג אוויר",
    # Landscape
    "L"             : u"אדריכלות נוף",
    "LA"            : u"אדריכלות נוף",
    "LS"            : u"אדריכלות נוף",
    "LAND"          : u"אדריכלות נוף",
    "LANDSCAPE"     : u"אדריכלות נוף",
    # Traffic / Transportation
    "TR"            : u"תנועה",
    "TRAF"          : u"תנועה",
    "TRAFFIC"       : u"תנועה",
    "TRANSPORT"     : u"תנועה",
    "TRANSPORTATION": u"תנועה",
    # Fire Protection
    "F"             : u"כיבוי אש",
    "FP"            : u"כיבוי אש",
    "FIRE"          : u"כיבוי אש",
    # Gas
    "G"             : u"גז",
    "GAS"           : u"גז",
    # Surveyor
    "SV"            : u"מודד",
    "SUR"           : u"מודד",
    "SURVEY"        : u"מודד",
    # Communications / IT
    "IT"            : u"תקשורת",
    # Audio-Visual
    "AV"            : u"אודיוויזואל",
    # Kitchens (project-specific)
    "KIT"           : u"מטבחים",
    "STKIT"         : u"מטבחים",
    "7STKIT"        : u"מטבחים",
    # Geotechnical
    "GEO"           : u"גיאוטכני",
}

# Parameters to probe in ProjectInformation for Tier-1 discipline extraction.
# STRICT: only parameters explicitly named "Discipline" / "דיסציפלינה".
# We do NOT probe Project Name, Organization Name, Building Name, etc. because
# those often contain project titles ("סלע בינוי", "מגרש 16 נשר") that are
# not discipline labels and would pollute the summary and chart.
_PROJ_INFO_PARAM_NAMES = (
    "Discipline",
    u"\u05d3\u05d9\u05e1\u05e6\u05d9\u05e4\u05dc\u05d9\u05e0\u05d4",  # דיסציפלינה
)


def _discipline_from_project_info(link_doc):
    """
    Tier 1 (strict): read the discipline from ProjectInformation.

    Rules:
      • Only probes parameters named exactly "Discipline" or "דיסציפלינה".
      • The value is accepted ONLY if it maps to a known entry in DISCIPLINE_MAP
        (i.e. it is a recognised token like "ME", "EL", "מיזוג אוויר", …).
      • Any value not in DISCIPLINE_MAP is rejected so that project titles,
        organisation names, and other free-text fields never leak through.

    Returns the mapped Hebrew label string, or None if no valid hit.
    """
    try:
        proj_info = link_doc.ProjectInformation
        if proj_info is None:
            return None

        for param_name in _PROJ_INFO_PARAM_NAMES:
            try:
                p = proj_info.LookupParameter(param_name)
                if p is None:
                    continue
                val = (p.AsString() or u"").strip()
                if not val:
                    val = (p.AsValueString() or u"").strip()
                if not val:
                    continue

                # Accept ONLY values that are recognised discipline tokens
                upper = val.upper()
                if upper in DISCIPLINE_MAP:
                    return DISCIPLINE_MAP[upper]
                # Reject everything else — do NOT return raw Hebrew strings
                # because they may be project names, not discipline labels.

            except Exception:
                continue

    except Exception:
        pass

    return None


def get_discipline(filename, link_doc=None):
    """
    Two-tier discipline recognition.

    Tier 1 — Revit API (ProjectInformation):
        Attempts to read the discipline from the linked document's
        ProjectInformation parameters ("Discipline", "דיסציפלינה", etc.).
        If a clear discipline label is found it is returned immediately.

    Tier 2 — Filename parsing (fallback):
        Parses the raw filename using DISCIPLINE_MAP.
        Strategy:
          a. Try known compound prefixes (longest-match first) to handle
             project-specific naming like "7STST_…" or "7STPL_…".
          b. Split on separators (-, _, space) and match each token.
          c. If nothing matches, return the filename stem unchanged.

    Args:
        filename : str  — basename of the linked .rvt file
        link_doc : optional Revit Document object for Tier 1 lookup
    """
    # ── Tier 1: ProjectInformation ────────────────────────────────────────────
    if link_doc is not None:
        result = _discipline_from_project_info(link_doc)
        if result:
            return result

    # ── Tier 2: Filename parsing ──────────────────────────────────────────────
    stem = os.path.splitext(filename)[0]
    name = stem.upper()

    # a. Compound prefixes — longest match wins (order matters)
    compound_prefixes = sorted(
        [(k, v) for k, v in DISCIPLINE_MAP.items() if len(k) >= 4],
        key=lambda x: len(x[0]), reverse=True
    )
    for prefix, label in compound_prefixes:
        if name.startswith(prefix):
            return label

    # b. Token-by-token match
    tokens = re.split(r'[-_ ]+', name)
    for tok in tokens:
        if tok in DISCIPLINE_MAP:
            return DISCIPLINE_MAP[tok]

    # c. No match — return the raw stem so the chart still has a label
    return stem


def hex_to_rgb_str(rgb_int):
    """0xRRGGBB  →  'RRGGBB' string for xlsxwriter."""
    r = (rgb_int >> 16) & 0xFF
    g = (rgb_int >>  8) & 0xFF
    b =  rgb_int        & 0xFF
    return "{:02X}{:02X}{:02X}".format(r, g, b)


def ft_to_cm(v):
    """Convert feet to centimetres, round to 2 dp. Returns None if v is None."""
    if v is None:
        return None
    return round(v * CM, 2)


# ─────────────────────────────────────────────────────────────────────────────
# REVIT DATA EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def get_loaded_links(document):
    """
    Return consolidated link-info dicts — one entry per unique LINKED FILE,
    grouped by full path rather than just basename.

    Root-cause fix: two different linked files can share an identical
    basename (e.g. the same filename also present as a stale duplicate /
    backup copy loaded from a different folder). Grouping by basename alone
    silently merges them into a single entry and keeps whichever Document
    the collector happens to reach first — while still displaying the
    correct-looking filename — so the actual level data pulled for that
    entry can belong to the WRONG physical file. This is exactly what was
    observed: Architecture Reference showed the right filename, but its
    level list didn't match that file's real levels.

    If two different full paths share a basename, the 2nd+ occurrence gets
    a " (2)", " (3)", ... suffix in its display name so both stay visible
    and distinguishable instead of one silently shadowing the other.

    Each dict contains:
      "filename"  — display name (basename, disambiguated on collision)
      "doc"       — the linked Document object
      "instance"  — the FIRST instance (used for PBP / transform when needed)
      "instances" — list of ALL RevitLinkInstance objects for this file
    """
    # Group by full path → {"doc":, "instances": []}, preserving first-seen order
    by_path = {}
    order   = []
    for inst in DB.FilteredElementCollector(document).OfClass(DB.RevitLinkInstance):
        ld = inst.GetLinkDocument()
        if ld is None:
            continue
        path = ld.PathName or u"<unresolved:{}>".format(inst.Name)
        if path not in by_path:
            by_path[path] = {"doc": ld, "instances": []}
            order.append(path)
        by_path[path]["instances"].append(inst)

    def _base(path):
        return (os.path.basename(path)
                if path and not path.startswith(u"<unresolved:") else path)

    # Basename → all full paths that produced it (to spot/report collisions)
    base_paths = {}
    for path in order:
        base_paths.setdefault(_base(path), []).append(path)

    used_names = {}
    links = []
    for path in order:
        info = by_path[path]
        base = _base(path)
        n = used_names.get(base, 0) + 1
        used_names[base] = n
        display = base if n == 1 else u"{} ({})".format(base, n)
        links.append({
            "filename" : display,
            "doc"      : info["doc"],
            "instance" : info["instances"][0],   # primary instance
            "instances": info["instances"],       # all instances
        })

    for base, paths in base_paths.items():
        if len(paths) > 1:
            output.print_md(
                u"⚠  **{}** is linked from {} different file locations — "
                u"kept as separate entries so the wrong one is never "
                u"silently used:\n{}".format(
                    base, len(paths),
                    u"\n".join(u"  - `{}`".format(p) for p in paths)))

    return links


def get_transform_z(link_instance):
    """Z component of the link's total transform origin (ft)."""
    try:
        return link_instance.GetTotalTransform().Origin.Z
    except Exception:
        return 0.0


def get_pbp_elevation(link_doc, debug=False):
    """
    Extract Project Base Point Z elevation (ft) from a linked document —
    the "Elevation" value Revit shows when you select the Project Base
    Point and look at the Properties palette / coordinate flyout.

    CONFIRMED against live data (TZE_C-AR-ARC-MAIN-R25.rvt, real PBP
    Elevation = 4030 cm per the Properties palette, cross-checked because
    that file's "GF_BLD 5" level sits at raw elevation 0 — i.e. exactly AT
    the Project Base Point, so its true elevation must equal the PBP's own):

      GetProjectPosition(XYZ.Zero)   → 4000 cm  — WRONG (30 cm off)
      GetProjectPosition(PBP.Position) → 4030 cm — CORRECT

    The difference: GetProjectPosition(XYZ.Zero) reports the shared-
    coordinates position of the model's internal origin. But the Project
    Base Point element itself isn't necessarily located exactly at that
    origin — its own Position can be offset from (0,0,0) — so the origin's
    shared position and the PBP's own shared position are different points
    entirely. Passing the PBP's own Position into GetProjectPosition gives
    the position Revit actually means by "the Project Base Point".

    BASEPOINT_ELEVATION_PARAM does not exist on this element (confirmed:
    get_Parameter returns None every time, and a full raw dump of every
    parameter on the element — Category / Design Option / Edited by /
    Workset / Family Name / Type Name — has nothing elevation-related).
    """
    fname = os.path.basename(link_doc.PathName or "unknown")
    project_location = link_doc.ActiveProjectLocation

    try:
        cat_filter = DB.ElementCategoryFilter(DB.BuiltInCategory.OST_ProjectBasePoint)
        pbp_list = list(DB.FilteredElementCollector(link_doc)
                          .WherePasses(cat_filter).ToElements())
    except Exception as ex:
        if debug:
            output.print_md(u"  OST_ProjectBasePoint collector FAILED for `{}`: {}".format(fname, ex))
        pbp_list = []

    for bp in pbp_list:
        try:
            z = project_location.GetProjectPosition(bp.Position).Elevation
            if debug:
                output.print_md(
                    u"**PBP `{}`** via GetProjectPosition(PBP.Position) → "
                    u"**{:.4f} ft = {:.2f} cm**".format(fname, z, z * CM))
            return z
        except Exception as ex:
            if debug:
                output.print_md(u"  GetProjectPosition(PBP.Position) FAILED for `{}`: {}".format(fname, ex))

    # ── Fallback: origin-based position. Known to be wrong when the PBP's
    # own Position is offset from the internal origin, but better than 0.0
    # if no OST_ProjectBasePoint element was found at all.
    try:
        if project_location is not None:
            z = project_location.GetProjectPosition(DB.XYZ(0, 0, 0)).Elevation
            if debug:
                output.print_md(
                    u"**PBP `{}`** via GetProjectPosition(origin) [fallback — "
                    u"no PBP element found] → **{:.4f} ft = {:.2f} cm**".format(fname, z, z * CM))
            return z
    except Exception as ex:
        if debug:
            output.print_md(u"  GetProjectPosition(origin) FAILED for `{}`: {}".format(fname, ex))

    if debug:
        output.print_md(u"  **All strategies failed for `{}` → returning 0.0**".format(fname))
    return 0.0


def get_elev_base_label(level):
    """
    Read 'Elevation Base' from the Level TYPE element.

    The parameter is a Type Parameter under Constraints:
      0  =  Project Base Point
      1  =  Survey Point

    Strategy (most reliable first):
      A. Read from the level's TYPE parameters by name.
      B. Read from the level INSTANCE parameters by name.
      C. Try BuiltInParameter.LEVEL_ELEV_BASE_PARAM on instance.
    """
    PARAM_NAMES = ("Elevation Base", u"\u05d1\u05e1\u05d9\u05e1 \u05d2\u05d5\u05d1\u05d4")  # "בסיס גובה"

    # A. Level Type
    try:
        ltype = level.Document.GetElement(level.GetTypeId())
        if ltype is not None:
            for p in ltype.Parameters:
                if p.Definition is not None and p.Definition.Name in PARAM_NAMES:
                    return "Survey Point" if p.AsInteger() == 1 else "Project Base Point"
    except Exception:
        pass

    # B. Level Instance
    try:
        for p in level.Parameters:
            if p.Definition is not None and p.Definition.Name in PARAM_NAMES:
                return "Survey Point" if p.AsInteger() == 1 else "Project Base Point"
    except Exception:
        pass

    # C. BuiltInParameter (varies across Revit versions)
    for bip_name in ("LEVEL_ELEV_BASE_PARAM", "LEVEL_ELEV_BASE"):
        try:
            bip = getattr(DB.BuiltInParameter, bip_name, None)
            if bip is None:
                continue
            p = level.get_Parameter(bip)
            if p is not None:
                return "Survey Point" if p.AsInteger() == 1 else "Project Base Point"
        except Exception:
            pass

    # Default
    return "Project Base Point"


def _get_levels_for_instance(link_info, inst, debug_output=False):
    """
    Return level rows for one specific RevitLinkInstance.
    Factored out so get_levels_data can call it per-instance.
    """
    ld    = link_info["doc"]
    tz    = get_transform_z(inst)
    pbp_z = get_pbp_elevation(ld)
    fname = link_info["filename"]

    if debug_output:
        output.print_md(
            u"**DEBUG `{}`** | transform\_z = {:.4f} ft ({:.2f} cm) | "
            u"pbp\_z = {:.4f} ft ({:.2f} cm)".format(
                fname, tz, tz*CM, pbp_z, pbp_z*CM))

    levels = sorted(
        DB.FilteredElementCollector(ld)
          .OfClass(DB.Level)
          .WhereElementIsNotElementType()
          .ToElements(),
        key=lambda lvl: lvl.Elevation
    )

    rows = []
    for lvl in levels:
        base = get_elev_base_label(lvl)
        raw  = lvl.Elevation

        # ── display columns (what the user sees in UI / Excel) ────────────────
        if base == "Project Base Point":
            disp_elev = raw          # UI shows relative-to-PBP value
            disp_pbp  = pbp_z        # PBP column = PBP Z in the linked doc
            disp_abs  = raw + pbp_z  # Absolute = Elevation + PBP (for display)
        else:
            disp_elev = raw + tz     # Survey Point: show absolute in host
            disp_pbp  = None
            disp_abs  = raw + tz

        # ── true_z (used ONLY for comparison between models) ─────────────────
        # Comparison must use the Absolute elevation (Level.Elevation + PBP
        # offset / link transform), NOT the raw Level.Elevation. Two levels
        # with the same name can share the same raw elevation while sitting
        # at different real-world heights if their files have different PBP
        # placements — only the absolute value reflects the true coordinate.
        true_z = disp_abs

        if debug_output:
            output.print_md(
                u"  lvl `{}` | base={} | raw={:.2f} ft ({:.0f} cm) | "
                u"tz={:.2f} ft | true\_z={:.4f} ft ({:.0f} cm)".format(
                    lvl.Name, base, raw, raw*CM, tz, true_z, true_z*CM))

        rows.append({
            "filename"  : fname,
            "name"      : lvl.Name,
            "base"      : base,
            "disp_elev" : disp_elev,
            "disp_pbp"  : disp_pbp,
            "disp_abs"  : disp_abs,
            "true_z"    : true_z,
        })
    return rows


def get_levels_data(link_info, debug_output=False):
    """
    Return level rows for a link, processing ALL instances.

    Multiple instances of the same file can have different transforms
    (e.g. one is offset).  We collect rows from every instance and
    de-duplicate by (name, true_z) so the comparison sees each unique
    physical placement exactly once.

    De-dup key: level name + rounded true_z (to 4 decimal places).
    If two instances produce the same level at the same absolute Z,
    only one row is kept.  If they differ in Z, both rows are kept
    (the comparison logic will evaluate each independently).
    """
    instances = link_info.get("instances", [link_info["instance"]])

    seen   = set()   # (name, rounded_z)
    rows   = []

    for idx_i, inst in enumerate(instances):
        # Debug only the first instance
        do_debug = debug_output and (idx_i == 0)
        for row in _get_levels_for_instance(link_info, inst, debug_output=do_debug):
            key = (row["name"], round(row["true_z"], 4))
            if key not in seen:
                seen.add(key)
                rows.append(row)

    rows.sort(key=lambda r: r["true_z"])
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# COMPARISON LOGIC  (4 statuses)
# ─────────────────────────────────────────────────────────────────────────────

def compare_levels(arch_rows, cons_rows, level_filter=None):
    """
    Level Validation Engine.

    level_filter: list of arch level names to include (None = all).

    ┌───────────────────────────────────────────────────────────────────────┐
    │  Status       │ Name match │ Elevation match │ Color  │
    ├───────────────────────────────────────────────────────────────────────┤
    │  GREEN        │  ✓ found   │  ✓ yes          │ green  │
    │  RED          │  ✓ found   │  ✗ no           │ red    │
    │  S_WRONGNAME  │  ✗ none    │  ✓ matches an   │ orange │  ← same physical
    │               │            │    arch level    │        │    level, wrong name
    │  ORANGE       │  ✗ none    │  ✗ no match      │ orange │
    └───────────────────────────────────────────────────────────────────────┘

    Matching uses normalize_name() so "Level 01", "LEVEL 01", "level 01"
    are all treated as identical.

    S_WRONGNAME rows record which arch level's name they should match in
    "expected_name" — this is the naming-error case explicitly called out:
    a consultant level sitting at exactly an architecture level's elevation
    but filed under a different name.

    Pass 2 adds a "Missing in Consultant" row (orange) for each arch level
    that has no name match at all in the consultant, naming it explicitly.
    """
    # ── Apply optional level filter ───────────────────────────────────────────
    if level_filter:
        filter_set = {normalize_name(n) for n in level_filter}
        arch_rows  = [r for r in arch_rows
                      if normalize_name(r["name"]) in filter_set]

        # Keep a consultant row if EITHER its name matches a selected arch
        # level OR its elevation matches one — not name alone. Filtering by
        # name only would throw away exactly the wrongly-named-but-right-
        # elevation rows the S_WRONGNAME check below needs to see, since by
        # definition their name does NOT match any selected arch level.
        filtered_arch_elevs = [r["true_z"] for r in arch_rows]
        cons_rows = [
            r for r in cons_rows
            if normalize_name(r["name"]) in filter_set
            or any(abs(r["true_z"] - z) <= TOLERANCE for z in filtered_arch_elevs)
        ]

    # ── Build normalized arch lookup ──────────────────────────────────────────
    # Key = normalized name → value = true_z
    # If multiple arch levels share a normalized name, last one wins
    # (that case is flagged ORANGE per spec: "ambiguous match → ORANGE")
    arch_norm_count = {}
    for r in arch_rows:
        k = normalize_name(r["name"])
        arch_norm_count[k] = arch_norm_count.get(k, 0) + 1

    arch_by_norm = {}
    for r in arch_rows:
        k = normalize_name(r["name"])
        arch_by_norm[k] = r["true_z"]   # last wins; ambiguous = ORANGE below

    # Normalized names present in consultant
    cons_norm_names = {normalize_name(r["name"]) for r in cons_rows}

    out = []

    # ── Pass 1: classify every consultant level ───────────────────────────────
    for row in cons_rows:
        norm = normalize_name(row["name"])
        z    = row["true_z"]
        expected_name = None

        if norm in arch_by_norm:
            # Name match found — check for ambiguity first
            if arch_norm_count.get(norm, 1) > 1:
                # Ambiguous: multiple arch levels with same normalized name
                status  = S_ORANGE
                desired = arch_by_norm[norm]
                color   = HEX_ORANGE
            elif abs(z - arch_by_norm[norm]) <= TOLERANCE:
                # GREEN — name + elevation both match
                status  = S_GREEN
                desired = arch_by_norm[norm]
                color   = HEX_GREEN
            else:
                # RED — name matches but elevation is wrong
                status  = S_RED
                desired = arch_by_norm[norm]
                color   = HEX_RED
        else:
            # No name match — but if this level's elevation exactly matches an
            # arch level anyway, it's almost certainly the SAME physical level
            # under a different name. Flag it explicitly as a naming issue and
            # record which arch level it should be renamed to match.
            elev_match = None
            for arch_row in arch_rows:
                if abs(z - arch_row["true_z"]) <= TOLERANCE:
                    elev_match = arch_row
                    break

            if elev_match is not None:
                status        = S_WRONGNAME
                desired       = elev_match["true_z"]
                expected_name = elev_match["name"]
                color         = HEX_ORANGE
            else:
                # ORANGE — no valid name match, no elevation match either
                status  = S_ORANGE
                desired = None
                color   = HEX_ORANGE

        r = dict(row)
        r["status"]        = status
        r["desired"]       = desired
        r["expected_name"] = expected_name
        r["color"]         = color
        out.append(r)

    # ── Pass 2: arch levels completely absent in consultant → S_MISSING ───────
    # Distinct status text (not S_ORANGE) so the report states plainly which
    # named level is missing, rather than lumping it under "Wrong Name / Unknown".
    #
    # Skip arch levels already claimed by a Pass-1 S_WRONGNAME row — otherwise
    # the SAME arch level shows up twice with contradictory-looking rows: e.g.
    # "ST-97 should be renamed to B3" AND, separately, "B3 is missing".
    wrongname_resolved = {normalize_name(r["expected_name"]) for r in out
                          if r["status"] == S_WRONGNAME}
    fallback_fname = cons_rows[0]["filename"] if cons_rows else ""
    for arch_row in arch_rows:
        norm = normalize_name(arch_row["name"])
        if norm not in cons_norm_names and norm not in wrongname_resolved:
            out.append({
                "filename"     : fallback_fname,
                "name"         : arch_row["name"],
                "base"         : "-",
                "disp_elev"    : None,
                "disp_pbp"     : None,
                "disp_abs"     : None,
                "true_z"       : None,
                "status"       : S_MISSING,
                "desired"      : arch_row["true_z"],
                "expected_name": None,
                "color"        : HEX_ORANGE,
            })

    # Sort: matched rows first (by abs elevation), then missing (Nones last)
    out.sort(key=lambda x: (x["disp_abs"] is None, x["disp_abs"] or 0))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL EXPORT  (xlsxwriter — no COM / Interop)
# ─────────────────────────────────────────────────────────────────────────────

DETAIL_COLS = [
    "RVT Link: File Name",
    "Name",
    "Elevation Base",
    "Elevation",
    "PBP elevation",
    "Absolute elevation",
    "Status",
    "Expected Level Name",
    "Desired elevation",
]

COL_WIDTHS = [38, 22, 22, 14, 14, 20, 26, 22, 18]


# ─────────────────────────────────────────────────────────────────────────────
# CSV EXPORT  (stdlib only — no dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def build_csv(arch_info, results_map, save_path):
    """
    Write a single flat CSV file with all comparison rows.

    Structure:
      • One header row
      • One data row per level per consultant link
      • UTF-8 with BOM so Excel opens Hebrew text correctly
      • Numeric values in centimetres (same as the Excel report)

    Columns (match DETAIL_COLS exactly):
      RVT Link: File Name, Name, Elevation Base, Elevation,
      PBP elevation, Absolute elevation, Status, Desired elevation
    """
    import io

    rows_all = []

    # ── collect all rows across all links ─────────────────────────────────────
    for fname, rows in results_map.items():
        for r in rows:

            def _fmt(v):
                """None → empty string; number → '0.00' string."""
                if v is None:
                    return u""
                if isinstance(v, float) or isinstance(v, int):
                    return u"{:.2f}".format(v)
                return u"{}".format(v)

            rows_all.append([
                r.get("filename", fname),
                r.get("name", u""),
                r.get("base",  u""),
                _fmt(ft_to_cm(r.get("disp_elev"))),
                _fmt(ft_to_cm(r.get("disp_pbp"))),
                _fmt(ft_to_cm(r.get("disp_abs"))),
                r.get("status", u""),
                r.get("expected_name") or u"",
                _fmt(ft_to_cm(r.get("desired"))) if r.get("desired") != "-" else u"-",
            ])

    # ── write file ────────────────────────────────────────────────────────────
    with io.open(save_path, "w", encoding="utf-8-sig", newline="") as f:

        def csv_row(cells):
            """Quote cells that contain commas, quotes, or newlines."""
            parts = []
            for cell in cells:
                s = u"{}".format(cell)
                if u"," in s or u'"' in s or u"\n" in s:
                    s = u'"' + s.replace(u'"', u'""') + u'"'
                parts.append(s)
            f.write(u",".join(parts) + u"\n")

        # header
        csv_row(DETAIL_COLS)

        # data
        for row in rows_all:
            csv_row(row)


def build_excel(arch_info, results_map, save_path, link_info_map=None, arch_rows=None):
    """
    Build the multi-tab .xlsx file.

    Tab 1  — Reference  (levels of the architecture model everything is compared to)
    Tab 2  — Summary    (counts per link per status)
    Tab 3  — Chart
    Tab 4+ — One tab per consultant link (detail rows)
    """
    wb = xlsxwriter.Workbook(save_path, {"strings_to_numbers": False})

    # ── format factories ──────────────────────────────────────────────────────
    def mk_hdr(extra=None):
        fmt = {
            "bold": True, "font_color": "FFFFFF",
            "bg_color": hex_to_rgb_str(HEX_HEADER),
            "border": 1, "align": "center",
            "valign": "vcenter", "text_wrap": True,
        }
        if extra:
            fmt.update(extra)
        return wb.add_format(fmt)

    def mk_text(bg_hex):
        return wb.add_format({
            "bg_color": hex_to_rgb_str(bg_hex),
            "border": 1, "valign": "vcenter",
        })

    def mk_num(bg_hex):
        return wb.add_format({
            "bg_color": hex_to_rgb_str(bg_hex),
            "border": 1, "valign": "vcenter",
            "num_format": "0.00",
        })

    def mk_ctr(bg_hex):
        return wb.add_format({
            "bg_color": hex_to_rgb_str(bg_hex),
            "border": 1, "valign": "vcenter",
            "align": "center",
        })

    hdr      = mk_hdr()
    plain    = wb.add_format({"border": 1, "valign": "vcenter"})
    num_plain= wb.add_format({"border": 1, "valign": "vcenter", "num_format": "0.00"})

    # Status → (text_fmt, number_fmt, center_fmt)  — 3 colors only
    _g = (mk_text(HEX_GREEN),  mk_num(HEX_GREEN),  mk_ctr(HEX_GREEN))
    _r = (mk_text(HEX_RED),    mk_num(HEX_RED),    mk_ctr(HEX_RED))
    _o = (mk_text(HEX_ORANGE), mk_num(HEX_ORANGE), mk_ctr(HEX_ORANGE))

    STATUS_FMT = {
        S_GREEN    : _g,
        S_RED      : _r,
        S_ORANGE   : _o,
        S_WRONGNAME: _o,
        S_MISSING  : _o,
        # legacy aliases
        S_OK     : _g,
        S_HEIGHT : _r,
        S_NAME   : _o,
        S_UNKNOWN: _o,
    }

    # ── TAB 1: Reference (the architecture model everything is compared to) ──
    ref_cols   = ["Name", "Elevation Base", "Elevation", "PBP elevation", "Absolute elevation"]
    ref_widths = [22, 22, 14, 14, 20]

    ws_ref = wb.add_worksheet("Reference")
    ws_ref.activate()
    ws_ref.freeze_panes(2, 0)

    ref_title_fmt = wb.add_format({
        "bold": True, "font_size": 13, "font_color": hex_to_rgb_str(HEX_HEADER),
    })
    ws_ref.write(0, 0, u"Architecture Reference (Source of Truth): {}".format(
        arch_info.get("filename", "")), ref_title_fmt)

    for ci, (h, w) in enumerate(zip(ref_cols, ref_widths)):
        ws_ref.write(1, ci, h, hdr)
        ws_ref.set_column(ci, ci, w)

    for ri, r in enumerate(arch_rows or [], start=2):
        ws_ref.write(ri, 0, r["name"], plain)
        ws_ref.write(ri, 1, r["base"], plain)

        for ci, key in ((2, "disp_elev"), (3, "disp_pbp"), (4, "disp_abs")):
            v = ft_to_cm(r[key])
            if v is None: ws_ref.write_blank(ri, ci, None, num_plain)
            else:         ws_ref.write_number(ri, ci, v, num_plain)

    # ── TAB 2: Summary ────────────────────────────────────────────────────────
    ws_sum = wb.add_worksheet("Summary")
    ws_sum.freeze_panes(1, 0)

    sum_hdrs = [
        "RVT Link: File Name",
        S_GREEN,   # OK
        S_RED,     # Height Misalignment
        S_ORANGE,  # Wrong Name / Unknown / Missing
    ]
    sum_hdrs = [u"Status"] + sum_hdrs
    for ci, h in enumerate(sum_hdrs):
        ws_sum.write(0, ci, h, hdr)

    ws_sum.set_column(0, 0, 20)   # Status — at-a-glance OK / issues
    ws_sum.set_column(1, 1, 38)
    ws_sum.set_column(2, 2, 22)   # OK / Green
    ws_sum.set_column(3, 3, 26)   # Height Misalignment / Red
    ws_sum.set_column(4, 4, 30)   # Wrong Name / Orange

    status_ok_fmt     = mk_ctr(HEX_GREEN)
    status_issues_fmt = mk_ctr(HEX_ORANGE)

    sum_data = []
    for fname, rows in results_map.items():
        # Resolve clean discipline label — Tier-1 (strict) then Tier-2 filename
        _link_doc = link_info_map[fname]["doc"] if (link_info_map and fname in link_info_map) else None
        disc_name = get_discipline(fname, link_doc=_link_doc)

        c = {S_GREEN: 0, S_RED: 0, S_ORANGE: 0}
        for r in rows:
            s = r["status"]
            if s in (S_GREEN, S_OK):
                c[S_GREEN] += 1
            elif s in (S_RED, S_HEIGHT):
                c[S_RED] += 1
            else:
                c[S_ORANGE] += 1

        n_issues = c[S_RED] + c[S_ORANGE]
        status_text = u"✅ OK" if n_issues == 0 else u"⚠  {} issue(s)".format(n_issues)
        status_fmt  = status_ok_fmt if n_issues == 0 else status_issues_fmt

        # Summary column B = pure discipline name only (no model name)
        sum_data.append((status_text, status_fmt, disc_name, c[S_GREEN], c[S_RED], c[S_ORANGE]))

    # Issues-first ordering: at-a-glance, the links that need attention lead
    sum_data.sort(key=lambda row: (row[0] == u"✅ OK", row[2]))

    for ri, (status_text, status_fmt, disc_name, g, r_cnt, o) in enumerate(sum_data, start=1):
        ws_sum.write(ri, 0, status_text, status_fmt)
        for ci, v in enumerate((disc_name, g, r_cnt, o), start=1):
            ws_sum.write(ri, ci, v, plain)

    # ── TAB 3: Per-Model Status Chart ────────────────────────────────────────
    ws_ch = wb.add_worksheet("Chart")

    # ── Build per-model data rows ─────────────────────────────────────────────
    # Each linked model gets its own bar.  Category label format:
    #   "Discipline [Model.rvt]"   e.g.  "חשמל [N16-ELEC-MAIN.rvt]"
    #
    # NOTE ON RICH TEXT IN CHART AXIS LABELS:
    # xlsxwriter does not support mixed font styles (bold/colour) within a
    # single chart category axis label — only plain strings are accepted.
    # The combined label below is therefore a single plain string.
    # Workaround: the discipline name is written first (it is the prominent
    # part) and the model filename in brackets provides the contextual detail.
    # If richer formatting is ever needed, consider embedding an image of the
    # chart or switching to openpyxl / win32com which support rich axis labels.

    model_rows = []   # list of (category_label, disc_name, {S_GREEN, S_RED, S_ORANGE})

    for fname, rows in results_map.items():
        # Resolve clean discipline label for this model
        _link_doc = link_info_map[fname]["doc"] if (link_info_map and fname in link_info_map) else None
        disc  = get_discipline(fname, link_doc=_link_doc)
        # Chart label: "Discipline [Model.rvt]"  (full filename, including .rvt)
        label = u"{} [{}]".format(disc, fname)

        c = {S_GREEN: 0, S_RED: 0, S_ORANGE: 0}
        for r in rows:
            s = r["status"]
            if s in (S_GREEN, S_OK):
                c[S_GREEN]  += 1
            elif s in (S_RED, S_HEIGHT):
                c[S_RED]    += 1
            else:
                c[S_ORANGE] += 1

        model_rows.append((label, c))

    # Sort alphabetically by label so chart is ordered consistently
    model_rows.sort(key=lambda x: x[0])

    # ── Write data table for chart ────────────────────────────────────────────
    ch_hdrs = [u"דיסציפלינה / מודל", S_GREEN, S_RED, S_ORANGE]
    for ci, h in enumerate(ch_hdrs):
        ws_ch.write(0, ci, h, hdr)

    for ri, (label, c) in enumerate(model_rows, start=1):
        ws_ch.write(ri, 0, label, plain)
        for ci_idx, (val, fmt) in enumerate([
            (c[S_GREEN],  mk_num(HEX_GREEN)),
            (c[S_RED],    mk_num(HEX_RED)),
            (c[S_ORANGE], mk_num(HEX_ORANGE)),
        ], start=1):
            if val > 0:
                ws_ch.write_number(ri, ci_idx, val, fmt)
            else:
                ws_ch.write_blank(ri, ci_idx, None, fmt)

    # Column widths — label column wider to accommodate "Discipline [Model.rvt]"
    ws_ch.set_column(0, 0, 48)
    for ci in range(1, 4):
        ws_ch.set_column(ci, ci, 22)

    n_models = len(model_rows)

    # ── Build stacked bar chart ───────────────────────────────────────────────
    chart = wb.add_chart({"type": "bar", "subtype": "stacked"})

    series_labels_heb = [
        u"תקין",               # green
        u"גובה לא תואם",       # red
        u"שם שגוי / לא ידוע",  # orange
    ]

    series_cfg_final = [
        (1, series_labels_heb[0], hex_to_rgb_str(HEX_GREEN)),
        (2, series_labels_heb[1], hex_to_rgb_str(HEX_RED)),
        (3, series_labels_heb[2], hex_to_rgb_str(HEX_ORANGE)),
    ]

    for col_idx, label, color in series_cfg_final:
        chart.add_series({
            "name"        : label,
            "categories"  : ["Chart", 1, 0, n_models, 0],
            "values"      : ["Chart", 1, col_idx, n_models, col_idx],
            "fill"        : {"color": "#" + color},
            "border"      : {"color": "#FFFFFF", "width": 0.5},
            "data_labels" : {
                "value"     : True,
                "font"      : {"size": 12, "bold": True, "color": "#333333"},
                "position"  : "inside_end",
                "num_format": "0;-0;;",
            },
        })

    chart.set_title({
        "name"      : u"סטטוס תיאום מפלסים לפי מודל",
        "name_font" : {"size": 18, "bold": True, "color": "#2A4B7C"},
    })
    chart.set_x_axis({
        "name"           : u"מספר מפלסים",
        "name_font"      : {"bold": True, "size": 13},
        "num_font"       : {"size": 12},
        "num_format"     : "0",
        "major_gridlines": {"visible": True, "line": {"color": "#E0E0E0"}},
        "min"            : 0,
    })
    chart.set_y_axis({
        "num_font"  : {"size": 12, "bold": True},
        "text_axis" : True,
    })
    chart.set_legend({
        "position"  : "bottom",
        "font"      : {"size": 12, "bold": True},
    })
    chart.set_plotarea({
        "border"       : {"color": "#DDDDDD"},
        "fill"         : {"color": "#F9F9F9"},
    })
    chart.set_chartarea({
        "border"       : {"none": True},
        "fill"         : {"color": "#FFFFFF"},
    })
    chart.set_size({"width": 820, "height": max(340, n_models * 70 + 160)})

    ws_ch.insert_chart("H2", chart)

    # ── Legend color key table next to chart ──────────────────────────────────
    legend_row = n_models + 4
    legend_items = [
        (series_labels_heb[0], HEX_GREEN),
        (series_labels_heb[1], HEX_RED),
        (series_labels_heb[2], HEX_ORANGE),
    ]
    ws_ch.write(legend_row, 0, u"מפתח צבעים:", hdr)
    for i, (lbl, clr) in enumerate(legend_items, start=legend_row + 1):
        color_fmt = wb.add_format({
            "bg_color": hex_to_rgb_str(clr), "border": 1,
            "align": "center", "valign": "vcenter",
        })
        ws_ch.write(i, 0, lbl, color_fmt)
        ws_ch.set_row(i, 22)

    # ── TABs per consultant link ───────────────────────────────────────────────
    def safe_tab(s):
        for ch in r'/\*?[]':
            s = s.replace(ch, "-")
        return s[:31]

    for fname, rows in results_map.items():
        ws = wb.add_worksheet(safe_tab(fname))
        ws.set_default_row(18)

        # ── Status banner — at-a-glance, before any scrolling/reading ────────
        n_issues = sum(1 for r in rows if r["status"] not in (S_GREEN, S_OK))
        if n_issues == 0:
            banner_text = u"✅  All {} levels OK".format(len(rows))
            banner_bg   = HEX_GREEN
        else:
            banner_text = u"⚠  {} issue(s) out of {} levels — see highlighted rows below".format(
                n_issues, len(rows))
            banner_bg   = HEX_ORANGE
        banner_fmt = wb.add_format({
            "bold": True, "font_size": 13, "font_color": "FFFFFF",
            "bg_color": hex_to_rgb_str(banner_bg), "valign": "vcenter", "indent": 1,
        })
        ws.set_row(0, 26)
        ws.merge_range(0, 0, 0, len(DETAIL_COLS) - 1, banner_text, banner_fmt)

        HEADER_ROW = 2
        ws.freeze_panes(HEADER_ROW + 1, 0)

        for ci, (h, w) in enumerate(zip(DETAIL_COLS, COL_WIDTHS)):
            ws.write(HEADER_ROW, ci, h, hdr)
            ws.set_column(ci, ci, w)

        for ri, r in enumerate(rows, start=HEADER_ROW + 1):
            tf, nf, cf = STATUS_FMT[r["status"]]

            # col 0 — RVT Link: File Name
            ws.write(ri, 0, r["filename"], tf)

            # col 1 — Name
            ws.write(ri, 1, r["name"], tf)

            # col 2 — Elevation Base
            ws.write(ri, 2, r["base"], tf)

            # col 3 — Elevation (cm)
            v = ft_to_cm(r["disp_elev"])
            if v is None: ws.write_blank(ri, 3, None, nf)
            else:         ws.write_number(ri, 3, v, nf)

            # col 4 — PBP elevation (cm) — blank for Survey Point
            v = ft_to_cm(r["disp_pbp"])
            if v is None: ws.write_blank(ri, 4, None, nf)
            else:         ws.write_number(ri, 4, v, nf)

            # col 5 — Absolute elevation (cm)
            #   PBP → Elevation + PBP elevation
            #   SP  → same as Elevation
            v = ft_to_cm(r["disp_abs"])
            if v is None: ws.write_blank(ri, 5, None, nf)
            else:         ws.write_number(ri, 5, v, nf)

            # col 6 — Status
            ws.write(ri, 6, r["status"], cf)

            # col 7 — Expected Level Name (only set for S_WRONGNAME rows —
            # the arch level name this consultant level should be renamed to)
            ws.write(ri, 7, r.get("expected_name") or "", tf)

            # col 8 — Desired elevation (cm)
            #   None   → blank
            #   number → converted from ft
            d = r["desired"]
            if d is None:
                ws.write_blank(ri, 8, None, nf)
            else:
                ws.write_number(ri, 8, ft_to_cm(d), nf)

    wb.close()


# ─────────────────────────────────────────────────────────────────────────────
# HTML EXPORT  (self-contained: inline CSS + vanilla JS, no external assets)
# ─────────────────────────────────────────────────────────────────────────────

def _html_escape(v):
    s = u"" if v is None else u"{}".format(v)
    return (s.replace(u"&", u"&amp;")
             .replace(u"<", u"&lt;")
             .replace(u">", u"&gt;")
             .replace(u'"', u"&quot;"))


def _html_num(v):
    return u"" if v is None else u"{:.2f}".format(v)


def _status_css_class(status):
    if status in (S_GREEN, S_OK):
        return u"row-green"
    if status in (S_RED, S_HEIGHT):
        return u"row-red"
    return u"row-orange"


def _slugify(s):
    """Filename → safe HTML id fragment (letters/digits only)."""
    return u"".join(ch if ch.isalnum() else u"_" for ch in s)


# Plain-language explanation shown above each table header in the HTML
# report. Keyed on the column label with any trailing " (cm)" stripped, so
# it matches both the Reference table's and the Detail tables' headers.
_COL_NOTES = {
    u"RVT Link: File Name": u"Which consultant file this row is from",
    u"Name":                u"Level name as read from the model",
    u"Elevation Base":      u"What Revit measures Elevation from",
    u"Elevation":           u"Height relative to the Elevation Base",
    u"PBP elevation":       u"The Project Base Point's own height in this file",
    u"Absolute elevation":  u"True height in shared coordinates",
    u"Status":              u"Result of comparing to the architecture reference",
    u"Expected Level Name": u"Architecture level this elevation actually matches",
    u"Desired elevation":   u"The elevation this level should have",
}


def _th_html(label, extra_cls=u""):
    """Render a <th> with its plain-language note on the line above it."""
    key = label[:-5] if label.endswith(u" (cm)") else label
    note = _COL_NOTES.get(key, u"")
    note_html = (u"<span class=\"th-note\">{}</span>".format(_html_escape(note))
                 if note else u"")
    return u"<th{}>{}{}</th>".format(extra_cls, note_html, _html_escape(label))


_HTML_CSS = u"""
* { box-sizing: border-box; }

:root {
  --bg: #EEF1F5;
  --surface: #FFFFFF;
  --surface-2: #F7F9FB;
  --ink: #1A2433;
  --muted: #5C6B7A;
  --line: #D7DEE6;
  --accent: #2E5AA8;
  --accent-ink: #FFFFFF;
  /* Status palette — validated with scripts/validate_palette.js (lightness
     band, chroma floor, CVD separation, normal-vision floor, contrast) against
     both #EEF1F5 and #FFFFFF light surfaces. Reserved for status only, never
     reused as a categorical series color. */
  --ok: #1E7F5C;    --ok-bg: #E3F3EC;
  --warn: #B87908;  --warn-bg: #FBEEDD;
  --crit: #C1352B;  --crit-bg: #FBE7E7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10151D;
    --surface: #182029;
    --surface-2: #1E2732;
    --ink: #E7ECF2;
    --muted: #93A2B4;
    --line: #2B3541;
    --accent: #75A3E8;
    --accent-ink: #0B1420;
    /* Status palette — separately validated against the dark surfaces
       (#10151D / #182029), not an auto-flip of the light-mode values. */
    --ok: #45A878;   --ok-bg: rgba(69,168,120,.16);
    --warn: #B8860F; --warn-bg: rgba(184,134,15,.16);
    --crit: #C24545; --crit-bg: rgba(194,69,69,.16);
  }
}
html { color-scheme: light dark; }
body {
  margin: 0; padding: 0 0 56px; background: var(--bg); color: var(--ink);
  font-family: "Segoe UI", Tahoma, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}

.titleblock {
  background: var(--ink); color: var(--bg);
  padding: 20px 32px; display: flex; align-items: baseline; gap: 32px;
  flex-wrap: wrap; border-bottom: 3px solid var(--accent);
}
.titleblock h1 {
  font-family: Cambria, Georgia, serif; font-weight: 700; font-size: 21px;
  margin: 0; letter-spacing: .01em; text-wrap: balance;
}
.tb-field { display: flex; flex-direction: column; gap: 2px; }
.tb-field .tb-label {
  font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
  color: rgba(255,255,255,.55); font-weight: 600;
}
.tb-field .tb-value {
  font-family: Consolas, "Cascadia Mono", monospace; font-size: 13px;
}

section { padding: 26px 32px; }
section > h2 {
  font-family: Cambria, Georgia, serif; font-weight: 700; font-size: 13px;
  color: var(--muted); text-transform: uppercase; letter-spacing: .1em;
  margin: 0 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--line);
}

.cards { display: flex; flex-wrap: wrap; gap: 12px; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  padding: 14px 16px; min-width: 230px; flex: 1 1 230px; cursor: pointer;
  border-left: 4px solid var(--line); transition: border-color .15s ease, transform .1s ease;
}
.card:hover { transform: translateY(-1px); }
.card:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.card.ok     { border-left-color: var(--ok); }
.card.issues { border-left-color: var(--crit); }
.card-status {
  display: inline-flex; align-items: center; gap: 6px; font-size: 11px;
  font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
  padding: 3px 8px; border-radius: 3px; margin-bottom: 8px;
}
.card.ok .card-status     { color: var(--ok);   background: var(--ok-bg); }
.card.issues .card-status { color: var(--crit); background: var(--crit-bg); }
.card-title { font-weight: 600; font-size: 14.5px; margin-bottom: 2px; }
.card-file  {
  font-family: Consolas, monospace; font-size: 11px; color: var(--muted);
  margin-bottom: 10px; word-break: break-all;
}
.pills { display: flex; gap: 6px; flex-wrap: wrap; font-variant-numeric: tabular-nums; }
.pill { font-size: 11px; padding: 2px 8px; border-radius: 3px; font-weight: 600; }
.pill.green  { color: var(--ok);   background: var(--ok-bg); }
.pill.red    { color: var(--crit); background: var(--crit-bg); }
.pill.orange { color: var(--warn); background: var(--warn-bg); }

/* ── Status-by-discipline chart ─────────────────────────────────────────── */
/* A quarter of the section's width (with a floor so labels stay legible) —
   this chart shows proportions, not fine-grained magnitude, so it doesn't
   need to run the full page width like the data tables do. */
.chart-wrap { max-width: 25%; min-width: 260px; }
.chart-legend {
  display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 18px;
  font-size: 12px; color: var(--muted);
}
.chart-legend .swatch {
  display: inline-flex; align-items: center; gap: 6px;
}
.chart-legend .swatch::before {
  content: ""; width: 10px; height: 10px; border-radius: 2px;
  background: var(--sw-color);
}
.chart-rows { display: flex; flex-direction: column; gap: 10px; }
.chart-row { display: grid; grid-template-columns: 84px 1fr 28px; align-items: center; gap: 10px; }
.chart-row .chart-label {
  font-size: 11.5px; font-weight: 600; text-align: right; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.chart-row .chart-total {
  font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums;
}
.chart-track {
  display: flex; gap: 2px; height: 20px; background: var(--surface-2);
  border-radius: 4px; overflow: hidden;
}
.chart-seg {
  height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 10.5px; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums;
  min-width: 2px;
}
.chart-seg.ok    { background: var(--ok); }
.chart-seg.warn  { background: var(--warn); }
.chart-seg.crit  { background: var(--crit); }
/* Square at the baseline (left/start), rounded only at the data-end (the
   tip, i.e. the last segment's right edge) — never round the start. */
.chart-seg:last-child { border-radius: 0 4px 4px 0; }
@media (max-width: 560px) {
  .chart-wrap { max-width: 100%; min-width: 0; }
  .chart-row { grid-template-columns: 96px 1fr 32px; }
  .chart-row .chart-label { font-size: 11px; }
}

details.ref-details summary {
  cursor: pointer; font-size: 13px; color: var(--accent); font-weight: 600;
  margin-bottom: 12px; list-style: none;
}
details.ref-details summary::-webkit-details-marker { display: none; }
details.ref-details summary::before { content: "▸ "; }
details.ref-details[open] summary::before { content: "▾ "; }

.table-wrap {
  max-height: 480px; overflow: auto; border: 1px solid var(--line);
  border-radius: 6px; background: var(--surface);
}
table { border-collapse: collapse; width: 100%; }
th, td {
  border-bottom: 1px solid var(--line); padding: 7px 12px; font-size: 12.5px;
  text-align: left; white-space: nowrap;
}
td.num, th.num { font-family: Consolas, monospace; font-variant-numeric: tabular-nums; text-align: right; }
th {
  background: var(--surface-2); color: var(--muted); font-weight: 700;
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em;
  position: sticky; top: 0; z-index: 1; vertical-align: bottom;
}
th .th-note {
  display: block; font-weight: 500; text-transform: none; letter-spacing: 0;
  font-size: 9px; color: var(--muted); opacity: .75; margin-bottom: 3px;
  white-space: normal;
}
tr.row-green  td { background: var(--ok-bg); }
tr.row-red    td { background: var(--crit-bg); }
tr.row-orange td { background: var(--warn-bg); }

.chip {
  display: inline-block; padding: 2px 8px; border-radius: 3px;
  font-size: 11.5px; font-weight: 600;
}
.chip.row-green  { color: var(--ok);   background: var(--ok-bg); }
.chip.row-red    { color: var(--crit); background: var(--crit-bg); }
.chip.row-orange { color: var(--warn); background: var(--warn-bg); }

#globalSearch {
  width: 100%; max-width: 340px; padding: 9px 12px; font-size: 13px;
  border: 1px solid var(--line); border-radius: 5px; margin-bottom: 16px;
  background: var(--surface); color: var(--ink); font-family: inherit;
}
#globalSearch:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

.tabbar { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.tabbtn {
  background: var(--surface); color: var(--muted); border: 1px solid var(--line);
  border-radius: 5px; padding: 7px 14px; font-size: 12px; cursor: pointer;
  font-family: inherit; display: inline-flex; align-items: center; gap: 6px;
}
.tabbtn:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.tabbtn.active { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.tabbtn .dot { width: 7px; height: 7px; border-radius: 50%; }
.tabpanel { display: none; }

@media (max-width: 640px) {
  .titleblock { padding: 16px 18px; }
  section { padding: 18px; }
}
@media (prefers-reduced-motion: reduce) {
  .card { transition: none; }
}
"""

_HTML_JS = u"""
function showTab(id) {
  var panels = document.querySelectorAll('.tabpanel');
  for (var i = 0; i < panels.length; i++) { panels[i].style.display = 'none'; }
  var btns = document.querySelectorAll('.tabbtn');
  for (var i = 0; i < btns.length; i++) { btns[i].classList.remove('active'); }
  var panel = document.getElementById('tab-' + id);
  var btn   = document.getElementById('btn-' + id);
  if (panel) { panel.style.display = 'block'; }
  if (btn)   { btn.classList.add('active'); }
  var details = document.getElementById('details-section');
  if (details) { details.scrollIntoView({behavior: 'smooth', block: 'start'}); }
}
function filterRows() {
  var q = document.getElementById('globalSearch').value.toLowerCase();
  var rows = document.querySelectorAll('.detail-table tbody tr');
  for (var i = 0; i < rows.length; i++) {
    var name = (rows[i].getAttribute('data-name') || '').toLowerCase();
    rows[i].style.display = (q === '' || name.indexOf(q) !== -1) ? '' : 'none';
  }
}
"""


def build_html(arch_info, arch_rows, results_map, save_path, link_info_map=None):
    """
    Self-contained interactive HTML report (inline CSS + vanilla JS, no
    external assets — works fully offline). Same data as the Excel report,
    presented for a much faster "what's wrong, right now" read: status
    cards up front, click-to-jump tabs per consultant link, and a live
    search box across all detail tables.
    """
    import io

    generated_str = datetime.date.today().strftime("%Y-%m-%d")

    # ── Per-link summary (used for both cards and tab bar) ───────────────────
    overview = []
    for fname, rows in results_map.items():
        _link_doc = link_info_map[fname]["doc"] if (link_info_map and fname in link_info_map) else None
        disc = get_discipline(fname, link_doc=_link_doc)
        c = {S_GREEN: 0, S_RED: 0, S_ORANGE: 0}
        for r in rows:
            s = r["status"]
            if s in (S_GREEN, S_OK):
                c[S_GREEN] += 1
            elif s in (S_RED, S_HEIGHT):
                c[S_RED] += 1
            else:
                c[S_ORANGE] += 1
        n_issues = c[S_RED] + c[S_ORANGE]
        overview.append({
            "fname": fname, "disc": disc, "counts": c,
            "n_issues": n_issues, "slug": _slugify(fname),
        })
    # Issues-first ordering, same as the Excel Summary tab
    overview.sort(key=lambda o: (o["n_issues"] == 0, o["disc"]))

    parts = []
    parts.append(u"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">")
    parts.append(u"<title>Level Coordination Report</title>")
    parts.append(u"<style>{}</style></head><body>".format(_HTML_CSS))

    # ── Title block — Revit/CAD sheet-title-block convention ─────────────────
    parts.append(u"<div class=\"titleblock\"><h1>Level Coordination Review</h1>")
    parts.append(
        u"<div class=\"tb-field\"><span class=\"tb-label\">Reference Model</span>"
        u"<span class=\"tb-value\">{}</span></div>".format(
            _html_escape(arch_info.get("filename", u""))))
    parts.append(
        u"<div class=\"tb-field\"><span class=\"tb-label\">Generated</span>"
        u"<span class=\"tb-value\">{}</span></div>".format(_html_escape(generated_str)))
    parts.append(u"</div>")

    # ── Overview cards ────────────────────────────────────────────────────────
    parts.append(u"<section><h2>Overview</h2><div class=\"cards\">")
    for o in overview:
        cls    = u"ok" if o["n_issues"] == 0 else u"issues"
        status = u"OK" if o["n_issues"] == 0 else u"{} Issue(s)".format(o["n_issues"])
        c = o["counts"]
        parts.append(
            u"<div class=\"card {cls}\" onclick=\"showTab('{slug}')\" tabindex=\"0\" "
            u"role=\"button\">"
            u"<div class=\"card-status\">{status}</div>"
            u"<div class=\"card-title\">{disc}</div>"
            u"<div class=\"card-file\">{fname}</div>"
            u"<div class=\"pills\">"
            u"<span class=\"pill green\">{g} OK</span>"
            u"<span class=\"pill red\">{r} height</span>"
            u"<span class=\"pill orange\">{o} naming/missing</span>"
            u"</div></div>".format(
                cls=cls, slug=o["slug"], status=_html_escape(status),
                disc=_html_escape(o["disc"]), fname=_html_escape(o["fname"]),
                g=c[S_GREEN], r=c[S_RED], o=c[S_ORANGE]))
    parts.append(u"</div></section>")

    # ── Status-by-discipline chart ────────────────────────────────────────────
    # Stacked horizontal bars, one per discipline. All bars share the same
    # scale (widths are fractions of the largest discipline's total level
    # count) so bar LENGTH also encodes how many levels that discipline has,
    # not just the proportion of each status within it.
    totals = [sum(o["counts"].values()) for o in overview]
    max_total = max(totals) if totals and max(totals) > 0 else 1

    parts.append(u"<section><h2>Levels by Status per Discipline</h2>")
    parts.append(u"<div class=\"chart-wrap\">")
    parts.append(u"<div class=\"chart-legend\">")
    for tok, label in ((u"--ok", u"OK"),
                       (u"--crit", u"Height Misalignment"),
                       (u"--warn", u"Wrong Name / Missing")):
        parts.append(u"<span class=\"swatch\" style=\"--sw-color:var({tok})\">{label}</span>".format(
            tok=tok, label=_html_escape(label)))
    parts.append(u"</div>")

    parts.append(u"<div class=\"chart-rows\">")
    for o in overview:
        c = o["counts"]
        total = c[S_GREEN] + c[S_RED] + c[S_ORANGE]
        segs = []
        for key, seg_cls, seg_label in ((S_GREEN, u"ok", u"OK"),
                                         (S_RED, u"crit", u"Height Misalignment"),
                                         (S_ORANGE, u"warn", u"Wrong Name / Missing")):
            count = c[key]
            if count <= 0:
                continue
            pct = (count / float(max_total)) * 100.0
            # Only label the segment inline if it's wide enough to hold the
            # number comfortably; otherwise the count still rides the native
            # hover tooltip (title=) and the legend + Overview cards above.
            inline = u"{}".format(count) if pct >= 6.0 else u""
            segs.append(
                u"<div class=\"chart-seg {cls}\" style=\"width:{pct:.3f}%\" "
                u"title=\"{label}: {count}\">{inline}</div>".format(
                    cls=seg_cls, pct=pct, label=_html_escape(seg_label),
                    count=count, inline=inline))
        parts.append(
            u"<div class=\"chart-row\">"
            u"<div class=\"chart-label\">{disc}</div>"
            u"<div class=\"chart-track\">{segs}</div>"
            u"<div class=\"chart-total\">{total}</div>"
            u"</div>".format(disc=_html_escape(o["disc"]), segs=u"".join(segs), total=total))
    parts.append(u"</div></div></section>")

    # ── Reference table (collapsible) ─────────────────────────────────────────
    parts.append(u"<section><details class=\"ref-details\" open><summary>"
                 u"Architecture Reference levels — {}</summary>".format(
                     _html_escape(arch_info.get("filename", u""))))
    parts.append(u"<div class=\"table-wrap\"><table><thead><tr>")
    parts.append(_th_html(u"Name"))
    parts.append(_th_html(u"Elevation Base"))
    parts.append(_th_html(u"Elevation (cm)", u" class=\"num\""))
    parts.append(_th_html(u"PBP elevation (cm)", u" class=\"num\""))
    parts.append(_th_html(u"Absolute elevation (cm)", u" class=\"num\""))
    parts.append(u"</tr></thead><tbody>")
    for r in (arch_rows or []):
        parts.append(
            u"<tr><td>{}</td><td>{}</td><td class=\"num\">{}</td>"
            u"<td class=\"num\">{}</td><td class=\"num\">{}</td></tr>".format(
                _html_escape(r["name"]), _html_escape(r["base"]),
                _html_num(ft_to_cm(r["disp_elev"])), _html_num(ft_to_cm(r["disp_pbp"])),
                _html_num(ft_to_cm(r["disp_abs"]))))
    parts.append(u"</tbody></table></div></details></section>")

    # ── Details: search box + tab bar + one table per consultant link ────────
    parts.append(u"<section id=\"details-section\"><h2>Details</h2>")
    parts.append(u"<input type=\"text\" id=\"globalSearch\" placeholder=\"Search level name…\" "
                 u"oninput=\"filterRows()\">")
    parts.append(u"<div class=\"tabbar\">")
    for o in overview:
        dot = u"var(--ok)" if o["n_issues"] == 0 else u"var(--crit)"
        parts.append(
            u"<button class=\"tabbtn\" id=\"btn-{slug}\" onclick=\"showTab('{slug}')\">"
            u"<span class=\"dot\" style=\"background:{dot}\"></span>{disc}</button>".format(
                slug=o["slug"], dot=dot, disc=_html_escape(o["disc"])))
    parts.append(u"</div>")

    for i, o in enumerate(overview):
        rows = results_map[o["fname"]]
        display = u"block" if i == 0 else u"none"
        parts.append(u"<div class=\"tabpanel\" id=\"tab-{slug}\" style=\"display:{disp}\">"
                     u"<div class=\"table-wrap\"><table class=\"detail-table\"><thead><tr>".format(
                         slug=o["slug"], disp=display))
        for ci, h in enumerate(DETAIL_COLS):
            th_cls = u" class=\"num\"" if ci in (3, 4, 5, 8) else u""
            parts.append(_th_html(h, th_cls))
        parts.append(u"</tr></thead><tbody>")
        for r in rows:
            row_cls = _status_css_class(r["status"])
            d = r["desired"]
            desired_txt = u"-" if d == u"-" else _html_num(ft_to_cm(d))
            parts.append(
                u"<tr class=\"{cls}\" data-name=\"{name_attr}\">"
                u"<td>{fname}</td><td>{name}</td><td>{base}</td>"
                u"<td class=\"num\">{elev}</td><td class=\"num\">{pbp}</td>"
                u"<td class=\"num\">{abs_}</td>"
                u"<td><span class=\"chip {cls}\">{status}</span></td>"
                u"<td>{expected}</td>"
                u"<td class=\"num\">{desired}</td></tr>".format(
                    cls=row_cls, name_attr=_html_escape(r["name"]),
                    fname=_html_escape(r["filename"]), name=_html_escape(r["name"]),
                    base=_html_escape(r["base"]),
                    elev=_html_num(ft_to_cm(r["disp_elev"])),
                    pbp=_html_num(ft_to_cm(r["disp_pbp"])),
                    abs_=_html_num(ft_to_cm(r["disp_abs"])),
                    status=_html_escape(r["status"]),
                    expected=_html_escape(r.get("expected_name") or u""),
                    desired=desired_txt))
        parts.append(u"</tbody></table></div></div>")
    parts.append(u"</section>")

    parts.append(u"<script>{}</script>".format(_HTML_JS))
    parts.append(u"</body></html>")

    with io.open(save_path, u"w", encoding=u"utf-8") as f:
        f.write(u"".join(parts))


# ─────────────────────────────────────────────────────────────────────────────
# WPF DIALOG  (XAML literal string, loaded with XamlReader)
# ─────────────────────────────────────────────────────────────────────────────

XAML = u"""
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Level Coordination Review"
  Width="560" SizeToContent="Height"
  WindowStartupLocation="CenterScreen"
  ResizeMode="CanMinimize"
  WindowStyle="SingleBorderWindow"
  ShowInTaskbar="True"
  Topmost="False"
  FontFamily="Segoe UI"
  Background="#F5F6FA">

  <Window.Resources>

    <Style x:Key="PrimaryBtn" TargetType="Button">
      <Setter Property="Background"      Value="#4472C4"/>
      <Setter Property="Foreground"      Value="White"/>
      <Setter Property="FontWeight"      Value="SemiBold"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Height"          Value="40"/>
      <Setter Property="Padding"         Value="24,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    CornerRadius="5" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#2A5BAD"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter Property="Background" Value="#1E4080"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="SecBtn" TargetType="Button">
      <Setter Property="Background"      Value="#E0E0E0"/>
      <Setter Property="Foreground"      Value="#333333"/>
      <Setter Property="FontSize"        Value="13"/>
      <Setter Property="Height"          Value="40"/>
      <Setter Property="Padding"         Value="20,0"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor"          Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border Background="{TemplateBinding Background}"
                    CornerRadius="5" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter Property="Background" Value="#CACACA"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="SmallBtn" TargetType="Button">
      <Setter Property="Background"      Value="#EEF2FA"/>
      <Setter Property="Foreground"      Value="#4472C4"/>
      <Setter Property="FontSize"        Value="11"/>
      <Setter Property="Height"          Value="26"/>
      <Setter Property="Padding"         Value="10,0"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="BorderBrush"     Value="#C0CCEA"/>
      <Setter Property="Cursor"          Value="Hand"/>
    </Style>

  </Window.Resources>

  <StackPanel>

    <!-- HEADER STRIP -->
    <Border Background="#4472C4" Height="72">
      <StackPanel Orientation="Horizontal" VerticalAlignment="Center" Margin="20,0">
        <Border Background="#2A5BAD" CornerRadius="22"
                Width="44" Height="44" Margin="0,0,14,0">
          <TextBlock Text="&#x2261;" FontSize="22" FontWeight="Bold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <StackPanel VerticalAlignment="Center">
          <TextBlock Text="Level Coordination Review"
                     FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Select reference architecture model and consultant links"
                     FontSize="10" Foreground="#BED3F3" Margin="0,3,0,0"/>
        </StackPanel>
      </StackPanel>
    </Border>

    <!-- BODY -->
    <StackPanel Margin="20,16,20,8">

      <!-- Architecture reference card -->
      <Border Background="White" CornerRadius="8" Padding="16" Margin="0,0,0,12">
        <Border.Effect>
          <DropShadowEffect Color="#BBBBBB" BlurRadius="8" ShadowDepth="2" Opacity="0.2"/>
        </Border.Effect>
        <StackPanel>
          <TextBlock Text="&#x1F4CC;  ARCHITECTURE REFERENCE  (Source of Truth)"
                     FontSize="11" FontWeight="SemiBold" Foreground="#4472C4" Margin="0,0,0,8"/>

          <!-- Search: filters the ArchCombo items, never clears the selection -->
          <Grid Margin="0,0,0,8">
            <TextBox x:Name="ArchSearchBox" Height="32" FontSize="12"
                     Padding="28,0,8,0" VerticalContentAlignment="Center"
                     BorderBrush="#CCCCCC" Background="#FAFAFA"/>
            <TextBlock x:Name="ArchSearchHint" Text="&#x1F50D;  Search reference&#x2026;"
                       FontSize="12" Foreground="#AAAAAA" Margin="8,0,0,0"
                       VerticalAlignment="Center" IsHitTestVisible="False"/>
          </Grid>

          <ComboBox x:Name="ArchCombo" Height="34" FontSize="12"
                    Padding="8,4" Background="White" BorderBrush="#CCCCCC"/>
        </StackPanel>
      </Border>

      <!-- Consultant links card -->
      <Border Background="White" CornerRadius="8" Padding="16" Margin="0,0,0,12">
        <Border.Effect>
          <DropShadowEffect Color="#BBBBBB" BlurRadius="8" ShadowDepth="2" Opacity="0.2"/>
        </Border.Effect>
        <StackPanel>
          <TextBlock Text="&#x1F500;  CONSULTANT LINKS  (compare against reference)"
                     FontSize="11" FontWeight="SemiBold" Foreground="#4472C4" Margin="0,0,0,8"/>

          <!-- Search: filters visibility, never clears items -->
          <Grid Margin="0,0,0,8">
            <TextBox x:Name="SearchBox" Height="32" FontSize="12"
                     Padding="28,0,8,0" VerticalContentAlignment="Center"
                     BorderBrush="#CCCCCC" Background="#FAFAFA"/>
            <TextBlock x:Name="SearchHint" Text="&#x1F50D;  Search links&#x2026;"
                       FontSize="12" Foreground="#AAAAAA" Margin="8,0,0,0"
                       VerticalAlignment="Center" IsHitTestVisible="False"/>
          </Grid>

          <Border BorderBrush="#DDDDDD" BorderThickness="1" CornerRadius="4" Height="160">
            <ScrollViewer VerticalScrollBarVisibility="Auto">
              <!-- ConsPanel built once; search toggles Visibility only -->
              <StackPanel x:Name="ConsPanel" Margin="4"/>
            </ScrollViewer>
          </Border>

          <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
            <Button x:Name="BtnAll"   Content="Select All"
                    Style="{StaticResource SmallBtn}" Margin="0,0,8,0"/>
            <Button x:Name="BtnClear" Content="Clear All"
                    Style="{StaticResource SmallBtn}"/>
          </StackPanel>
        </StackPanel>
      </Border>

      <!-- Level filter card (optional) -->
      <Border Background="White" CornerRadius="8" Padding="16" Margin="0,0,0,12">
        <Border.Effect>
          <DropShadowEffect Color="#BBBBBB" BlurRadius="8" ShadowDepth="2" Opacity="0.2"/>
        </Border.Effect>
        <StackPanel>
          <StackPanel Orientation="Horizontal" Margin="0,0,0,8">
            <TextBlock Text="&#x1F50E;  LEVELS TO COMPARE  (from architecture reference)"
                       FontSize="11" FontWeight="SemiBold" Foreground="#4472C4"
                       VerticalAlignment="Center"/>
            <Border Background="#EEF4FF" CornerRadius="6" Padding="4,2" Margin="8,0,0,0">
              <TextBlock Text="OPTIONAL" FontSize="9" FontWeight="SemiBold" Foreground="#4472C4"/>
            </Border>
          </StackPanel>

          <!-- Level search: toggles Visibility only, never clears -->
          <Grid Margin="0,0,0,8">
            <TextBox x:Name="LevelSearchBox" Height="32" FontSize="12"
                     Padding="28,0,8,0" VerticalContentAlignment="Center"
                     BorderBrush="#CCCCCC" Background="#FAFAFA"/>
            <TextBlock x:Name="LevelSearchHint" Text="&#x1F50D;  Search levels&#x2026;"
                       FontSize="12" Foreground="#AAAAAA" Margin="8,0,0,0"
                       VerticalAlignment="Center" IsHitTestVisible="False"/>
          </Grid>

          <!--
            LevelListBox: Extended selection (Shift+Click / Ctrl+Click).

            KEY DESIGN DECISIONS to make multi-select + CheckBox play nicely:

            1. ItemContainerStyle keeps the native WPF selection machinery
               (no custom ControlTemplate) but adds a blue highlight Trigger
               so selected rows are visually obvious.

            2. The CheckBox inside each row has IsHitTestVisible="False" and
               Focusable="False".  This means mouse clicks pass THROUGH the
               CheckBox to the ListBoxItem underneath, so WPF records the
               selection BEFORE any CheckBox event fires.
               Toggling is handled in Python via PreviewMouseLeftButtonDown
               on each ListBoxItem (fires before the default selection change),
               which reads lb.SelectedItems — already up-to-date — and flips
               all highlighted CheckBoxes atomically.
          -->
          <ListBox x:Name="LevelListBox"
                   SelectionMode="Extended"
                   Height="160"
                   BorderBrush="#DDDDDD" BorderThickness="1"
                   Background="White"
                   ScrollViewer.VerticalScrollBarVisibility="Auto"
                   HorizontalContentAlignment="Stretch">
            <ListBox.ItemContainerStyle>
              <Style TargetType="ListBoxItem">
                <Setter Property="Padding"    Value="4,5"/>
                <Setter Property="Background" Value="Transparent"/>
                <Setter Property="Foreground" Value="#333333"/>
                <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                <!-- Selection highlight via Trigger — no ControlTemplate needed -->
                <Style.Triggers>
                  <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#D0E4F7"/>
                    <Setter Property="Foreground" Value="#1A3A5C"/>
                  </Trigger>
                  <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#EEF4FB"/>
                  </Trigger>
                </Style.Triggers>
              </Style>
            </ListBox.ItemContainerStyle>
          </ListBox>

          <StackPanel Orientation="Horizontal" Margin="0,8,0,0">
            <Button x:Name="BtnLevelAll"   Content="Check All"
                    Style="{StaticResource SmallBtn}" Margin="0,0,8,0"/>
            <Button x:Name="BtnLevelClear" Content="Uncheck All"
                    Style="{StaticResource SmallBtn}" Margin="0,0,8,0"/>
            <Button x:Name="BtnLevelSort"  Content="&#x2195;  Sorted: Name (A&#x2192;Z)"
                    Style="{StaticResource SmallBtn}"/>
          </StackPanel>

          <TextBlock FontSize="10" Foreground="#888888" Margin="0,6,0,0"
                     Text="&#x2139;  Shift/Ctrl+Click to highlight rows, then check one to apply to all highlighted. If nothing is checked, all levels are compared."/>
        </StackPanel>
      </Border>

      <!-- Info strip -->
      <Border Background="#EEF4FF" CornerRadius="6" Padding="12,8" Margin="0,0,0,8">
        <TextBlock FontSize="11" Foreground="#4472C4" TextWrapping="Wrap">
          <Run FontWeight="SemiBold">&#x2139;  How it works:  </Run>
          <Run>Each level is matched using its True Absolute Elevation
(Level.Elevation + Link Transform Z). Status is colour-coded:
green = OK, red = Height Misalignment, orange = Wrong Name / Unknown / Missing.</Run>
        </TextBlock>
      </Border>

    </StackPanel>

    <!-- FOOTER -->
    <Border Background="White" BorderBrush="#E0E0E0" BorderThickness="0,1,0,0"
            Padding="20,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="8"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="8"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>

        <TextBlock x:Name="ErrMsg" Grid.Column="0"
                   Foreground="#D9534F" FontSize="11"
                   VerticalAlignment="Center" TextWrapping="Wrap"/>

        <Button x:Name="BtnMinimize" Grid.Column="1"
                Content="&#x2014;"
                ToolTip="Minimize — return to Revit. All selections are preserved."
                Style="{StaticResource SecBtn}" Padding="14,0"/>

        <Button x:Name="BtnCancel" Grid.Column="3"
                Content="Cancel" Style="{StaticResource SecBtn}"/>

        <Button x:Name="BtnRun" Grid.Column="5"
                Content="&#x25B6;  Run Report" Style="{StaticResource PrimaryBtn}"/>
      </Grid>
    </Border>

  </StackPanel>
</Window>
"""


# ─────────────────────────────────────────────────────────────────────────────
# SESSION PERSISTENCE  (module-level — survives dialog close/reopen)
# ─────────────────────────────────────────────────────────────────────────────
_SESSION = {
    "arch_name"  : None,
    "cons_names" : set(),
    "lvl_checked": set(),   # level names that were checked
}


def _smart_arch_default(names):
    """
    Pick a default filename out of an already-sorted list of link filenames.

    Priority:
      1. filename contains both 'AR' and 'MAIN'
      2. filename contains 'AR'
      3. first name (alphabetically, since `names` is pre-sorted)
    """
    for n in names:
        u = n.upper()
        if "AR" in u and "MAIN" in u:
            return n
    for n in names:
        if "AR" in n.upper():
            return n
    return names[0] if names else None


def _make_level_row(name, elev_cm):
    """
    Two-column row for a level ListBox item: name (left, stretches) and its
    elevation in cm (right-aligned) — a real elevation "column" per level,
    not just a name label.
    """
    grid = System.Windows.Controls.Grid()

    col_name = System.Windows.Controls.ColumnDefinition()
    col_name.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
    col_elev = System.Windows.Controls.ColumnDefinition()
    col_elev.Width = System.Windows.GridLength(90)
    grid.ColumnDefinitions.Add(col_name)
    grid.ColumnDefinitions.Add(col_elev)

    name_tb = System.Windows.Controls.TextBlock()
    name_tb.Text = name
    System.Windows.Controls.Grid.SetColumn(name_tb, 0)
    grid.Children.Add(name_tb)

    elev_tb = System.Windows.Controls.TextBlock()
    elev_tb.Text = u"{:.1f} cm".format(elev_cm) if elev_cm is not None else u""
    elev_tb.FontSize = 11
    elev_tb.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
    elev_tb.Foreground = System.Windows.Media.SolidColorBrush(
        System.Windows.Media.Color.FromRgb(136, 136, 136))
    System.Windows.Controls.Grid.SetColumn(elev_tb, 1)
    grid.Children.Add(elev_tb)

    return grid


# ─────────────────────────────────────────────────────────────────────────────
# SELECTION DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class SelectionDialog(object):
    """
    WPF dialog.  Four key design rules:
      1. ConsPanel & LevelListBox are populated ONCE; search only toggles Visibility.
      2. _level_checked set tracks which level names are checked.
      3. Session state is loaded on __init__ and saved on Run.
      4. Arch combo uses smart default (AR+MAIN > AR > 0), then session override.
    """

    def __init__(self, links):
        self.links           = links
        self.cancelled       = True
        self.arch_name       = None
        self.cons_names      = []
        self.selected_levels = []
        self._window         = None
        self._cons_items     = []   # [(filename, CheckBox)] — all, not filtered
        self._level_cbs      = []   # [(level_name, CheckBox)] — all, not filtered
        self._all_levels     = []
        self._level_elev     = {}   # level_name → elevation (ft), for sort/display
        self._level_sort_mode= "name"   # "name" or "elev" — toggled by BtnLevelSort
        self._arch_changed_handler = None
        # Checked-level memory (persists across toggles AND sessions)
        self._lvl_checked    = set(_SESSION["lvl_checked"])
        self._last_left      = None
        self._last_top       = None

    # ─────────────────────────────────────────────────────────────────────────
    def _build(self):
        ctx    = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window

        # ── Architecture combo (alphabetical) ─────────────────────────────────
        combo = window.FindName("ArchCombo")
        sorted_names = sorted((li["filename"] for li in self.links), key=lambda n: n.upper())
        for n in sorted_names:
            combo.Items.Add(n)

        # Smart default then session override — resolved by filename, so it's
        # immune to ordering (works regardless of how `self.links` is sorted).
        if _SESSION["arch_name"] in sorted_names:
            default_name = _SESSION["arch_name"]
        else:
            default_name = _smart_arch_default(sorted_names)
        combo.SelectedItem = default_name

        self._arch_changed_handler = self._on_arch_changed
        combo.SelectionChanged += self._arch_changed_handler

        # ── Wire arch search (filters ArchCombo items, never clears selection) ─
        asb = window.FindName("ArchSearchBox")
        asb.TextChanged += self._on_arch_search

        # ── Build ConsPanel (once) ────────────────────────────────────────────
        self._build_cons_panel()

        # ── Wire search (visibility filter, NOT rebuild) ──────────────────────
        sb = window.FindName("SearchBox")
        sb.TextChanged += self._on_cons_search

        lsb = window.FindName("LevelSearchBox")
        lsb.TextChanged += self._on_level_search

        # ── Buttons ───────────────────────────────────────────────────────────
        window.FindName("BtnAll").Click       += lambda s, e: self._set_all_cons(True)
        window.FindName("BtnClear").Click     += lambda s, e: self._set_all_cons(False)
        window.FindName("BtnLevelAll").Click  += lambda s, e: self._set_all_levels(True)
        window.FindName("BtnLevelClear").Click+= lambda s, e: self._set_all_levels(False)
        window.FindName("BtnLevelSort").Click += self._on_level_sort_toggle
        window.FindName("BtnMinimize").Click  += self._on_minimize
        window.StateChanged                   += self._on_state_changed
        window.FindName("BtnCancel").Click    += lambda s, e: window.Close()
        window.FindName("BtnRun").Click       += self._on_run

        # ── Build level list for current arch ────────────────────────────────
        self._build_level_list()

        return window

    # ─────────────────────────────────────────────────────────────────────────
    # BUILD helpers (called ONCE per arch change)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_cons_panel(self):
        """Create all consultant CheckBoxes.  Search will only toggle Visibility."""
        arch  = self._window.FindName("ArchCombo").SelectedItem
        panel = self._window.FindName("ConsPanel")
        panel.Children.Clear()
        self._cons_items = []

        session_cons = _SESSION["cons_names"]

        sorted_links = sorted(self.links, key=lambda li: li["filename"].upper())
        for li in sorted_links:
            if li["filename"] == arch:
                continue

            cb = System.Windows.Controls.CheckBox()
            cb.Content  = li["filename"]
            cb.FontSize  = 12
            cb.Margin    = System.Windows.Thickness(2, 5, 2, 5)
            cb.Cursor    = System.Windows.Input.Cursors.Hand
            cb.Foreground = System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(51, 51, 51))

            # Restore session
            if li["filename"] in session_cons:
                cb.IsChecked = True

            panel.Children.Add(cb)
            self._cons_items.append((li["filename"], cb))

    def _build_level_list(self):
        """
        Populate LevelListBox with one CheckBox per arch level (built once).
        Search will only toggle item Visibility.

        Multi-select + CheckBox behaviour:
          • CheckBox has IsHitTestVisible=False and Focusable=False so clicks
            pass through to the ListBoxItem, preserving the multi-selection.
          • PreviewMouseLeftButtonDown on each ListBoxItem fires BEFORE the
            default selection change and BEFORE any CheckBox event, so
            lb.SelectedItems is still the full highlighted set at that moment.
          • The handler toggles all highlighted CheckBoxes atomically and calls
            e.Handled=True to stop further propagation (no selection collapse,
            no redundant CheckBox event).
          • Result: Shift+Click/Ctrl+Click to highlight, then click anywhere in
            a row to flip all highlighted rows together.
        """
        arch = self._window.FindName("ArchCombo").SelectedItem
        lb   = self._window.FindName("LevelListBox")
        lb.Items.Clear()
        self._level_cbs  = []
        self._all_levels = []

        # ── Collect arch levels (name → elevation, ft) ────────────────────────
        lvl_elev = {}
        for li in self.links:
            if li["filename"] == arch:
                try:
                    lvls = (DB.FilteredElementCollector(li["doc"])
                              .OfClass(DB.Level)
                              .WhereElementIsNotElementType()
                              .ToElements())
                    for l in lvls:
                        if l.Name not in lvl_elev:
                            lvl_elev[l.Name] = l.Elevation
                except Exception:
                    pass
                break

        # Sort mode toggled by BtnLevelSort: alphabetical name, or elevation low→high
        if self._level_sort_mode == "elev":
            level_names = sorted(lvl_elev.keys(), key=lambda n: lvl_elev[n])
        else:
            level_names = sorted(lvl_elev.keys(), key=lambda n: n.upper())

        self._all_levels = level_names
        self._level_elev = lvl_elev

        if not level_names:
            lbi = System.Windows.Controls.ListBoxItem()
            lbi.Content    = u"ℹ  Select the Architecture Reference to load levels"
            lbi.FontSize   = 11
            lbi.Foreground = System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(150, 150, 150))
            lbi.IsEnabled  = False
            lb.Items.Add(lbi)
            return

        # ── Helper: get checked state of a ListBoxItem's inner CheckBox ───────
        def _lbi_checkbox(lbi):
            """Return the CheckBox inside a ListBoxItem, or None."""
            inner = lbi.Content
            if isinstance(inner, System.Windows.Controls.CheckBox):
                return inner
            return None

        # ── Build items ───────────────────────────────────────────────────────
        for name in level_names:
            # CheckBox — IsHitTestVisible=False so clicks go to ListBoxItem
            cb = System.Windows.Controls.CheckBox()
            cb.Content = _make_level_row(name, ft_to_cm(lvl_elev.get(name)))
            cb.HorizontalContentAlignment = System.Windows.HorizontalAlignment.Stretch
            cb.FontSize         = 12
            cb.Margin           = System.Windows.Thickness(2, 1, 2, 1)
            cb.IsChecked        = name in self._lvl_checked
            cb.IsHitTestVisible = False   # ← key: don't steal the click
            cb.Focusable        = False   # ← key: don't steal keyboard focus

            lbi = System.Windows.Controls.ListBoxItem()
            lbi.Content = cb

            # ── PreviewMouseLeftButtonDown: fires before selection changes ────
            # We capture lb and self via the closure to avoid IronPython
            # late-binding issues with loop variables.
            def make_preview_handler(level_name, listbox, dialog):
                def on_preview_mouse_down(sender, e):
                    """
                    Toggle logic:
                      1. Is the clicked item already in the selection?
                         Yes → the new state is the OPPOSITE of its current CB.
                         No  → treat as a normal click (WPF will select it);
                               we still flip after a short yield, but since the
                               user just navigated here we default to checking.
                      2. Apply the new state to ALL items currently in
                         lb.SelectedItems (the multi-selection is still intact
                         at PreviewMouse time).
                      3. Update _lvl_checked memory.
                      4. e.Handled = True prevents default processing:
                         - WPF won't collapse the selection.
                         - The CheckBox Checked/Unchecked events won't double-fire.
                    """
                    clicked_lbi = sender   # the ListBoxItem that received the click
                    clicked_cb  = _lbi_checkbox(clicked_lbi)
                    if clicked_cb is None:
                        return

                    # Determine new state: flip the clicked item's current state
                    new_state = not bool(clicked_cb.IsChecked)

                    # Collect the target set: all currently selected items
                    # PLUS the clicked item itself (in case it wasn't selected yet)
                    targets = set()
                    for sel in listbox.SelectedItems:
                        if isinstance(sel, System.Windows.Controls.ListBoxItem):
                            targets.add(sel)
                    targets.add(clicked_lbi)   # always include the clicked row

                    # Apply new state to all targets
                    for target_lbi in targets:
                        target_cb = _lbi_checkbox(target_lbi)
                        if target_cb is not None:
                            target_cb.IsChecked = new_state

                    # Sync memory from the full level_cbs list
                    for lname, lcb in dialog._level_cbs:
                        if lcb.IsChecked:
                            dialog._lvl_checked.add(lname)
                        else:
                            dialog._lvl_checked.discard(lname)

                    # Stop propagation: keep selection intact, skip CB events
                    e.Handled = True

                return on_preview_mouse_down

            lbi.PreviewMouseLeftButtonDown += make_preview_handler(name, lb, self)

            lb.Items.Add(lbi)
            self._level_cbs.append((name, cb))

    # ─────────────────────────────────────────────────────────────────────────
    # SEARCH — Visibility toggle (never clears items, preserves checks)
    # ─────────────────────────────────────────────────────────────────────────

    def _on_cons_search(self, sender, e):
        text = sender.Text.lower()
        # Update hint
        hint = self._window.FindName("SearchHint")
        hint.Visibility = (System.Windows.Visibility.Collapsed
                           if sender.Text else System.Windows.Visibility.Visible)
        # Toggle visibility of existing items — checked state is untouched
        panel = self._window.FindName("ConsPanel")
        for i, (fname, cb) in enumerate(self._cons_items):
            cb.Visibility = (
                System.Windows.Visibility.Visible
                if not text or text in fname.lower()
                else System.Windows.Visibility.Collapsed
            )
            # The parent in ConsPanel is the cb itself
            panel.Children[i].Visibility = cb.Visibility

    def _on_level_search(self, sender, e):
        text = sender.Text.lower()
        hint = self._window.FindName("LevelSearchHint")
        hint.Visibility = (System.Windows.Visibility.Collapsed
                           if sender.Text else System.Windows.Visibility.Visible)
        lb = self._window.FindName("LevelListBox")
        for i in range(lb.Items.Count):
            lbi = lb.Items.GetItemAt(i)
            if isinstance(lbi, System.Windows.Controls.ListBoxItem):
                # Name lookup via _level_cbs (built in the same order as lb.Items)
                # rather than introspecting cb.Content, since cb.Content is now a
                # Grid (name + elevation columns), not a plain string.
                name = self._level_cbs[i][0] if i < len(self._level_cbs) else u""
                lbi.Visibility = (
                    System.Windows.Visibility.Visible
                    if not text or text in name.lower()
                    else System.Windows.Visibility.Collapsed
                )

    # ─────────────────────────────────────────────────────────────────────────
    # ARCH CHANGED — rebuild both lists
    # ─────────────────────────────────────────────────────────────────────────

    def _on_arch_changed(self, sender, e):
        # Clear search boxes so new items are all visible
        self._window.FindName("SearchBox").Text       = ""
        self._window.FindName("LevelSearchBox").Text  = ""
        self._build_cons_panel()
        self._build_level_list()

    def _on_arch_search(self, sender, e):
        """
        Filter the ArchCombo items by substring (case-insensitive), same
        search UX as the consultant list. Rebuilding Items clears selection
        transiently, so the handler is detached/reattached to avoid a spurious
        rebuild of the dependent panels; they're only rebuilt once, at the end,
        and only if the filter actually forced a different selection.
        """
        text = sender.Text.lower()
        hint = self._window.FindName("ArchSearchHint")
        hint.Visibility = (System.Windows.Visibility.Collapsed
                           if sender.Text else System.Windows.Visibility.Visible)

        combo   = self._window.FindName("ArchCombo")
        current = combo.SelectedItem

        names    = sorted((li["filename"] for li in self.links), key=lambda n: n.upper())
        filtered = [n for n in names if not text or text in n.lower()]

        combo.SelectionChanged -= self._arch_changed_handler
        combo.Items.Clear()
        for n in filtered:
            combo.Items.Add(n)

        if current in filtered:
            combo.SelectedItem = current
        elif filtered:
            combo.SelectedIndex = 0
        else:
            combo.SelectedIndex = -1
        combo.SelectionChanged += self._arch_changed_handler

        if combo.SelectedItem != current:
            self._build_cons_panel()
            self._build_level_list()

    # ─────────────────────────────────────────────────────────────────────────
    # BULK SET
    # ─────────────────────────────────────────────────────────────────────────

    def _set_all_cons(self, state):
        for _, cb in self._cons_items:
            if cb.Visibility == System.Windows.Visibility.Visible:
                cb.IsChecked = state

    def _set_all_levels(self, state):
        for name, cb in self._level_cbs:
            cb.IsChecked = state
            if state:
                self._lvl_checked.add(name)
            else:
                self._lvl_checked.discard(name)

    def _on_level_sort_toggle(self, sender, e):
        """Toggle the level list between alphabetical (name) and elevation (low→high)."""
        self._level_sort_mode = "elev" if self._level_sort_mode == "name" else "name"
        sender.Content = (u"↕  Sorted: Elevation (Low→High)"
                           if self._level_sort_mode == "elev"
                           else u"↕  Sorted: Name (A→Z)")
        self._build_level_list()
        # Re-apply any active search filter to the freshly rebuilt items
        lsb = self._window.FindName("LevelSearchBox")
        if lsb.Text:
            self._on_level_search(lsb, None)

    # ─────────────────────────────────────────────────────────────────────────
    # MINIMIZE / RESTORE
    # ─────────────────────────────────────────────────────────────────────────

    def _on_minimize(self, sender, e):
        try:
            self._last_left = self._window.Left
            self._last_top  = self._window.Top
        except Exception:
            pass
        self._window.WindowState = System.Windows.WindowState.Minimized

    def _on_state_changed(self, sender, e):
        try:
            if self._window.WindowState == System.Windows.WindowState.Normal:
                if self._last_left is not None:
                    self._window.Left = self._last_left
                    self._window.Top  = self._last_top
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # RUN
    # ─────────────────────────────────────────────────────────────────────────

    def _on_run(self, sender, e):
        err  = self._window.FindName("ErrMsg")
        arch = self._window.FindName("ArchCombo").SelectedItem

        if arch is None:
            err.Text = u"\u26a0  Please select an Architecture model."
            return

        selected_cons = [fname for fname, cb in self._cons_items if cb.IsChecked]
        if not selected_cons:
            err.Text = u"\u26a0  Please select at least one consultant link."
            return

        err.Text = ""

        # Sync checked set from UI
        self._lvl_checked = set()
        for name, cb in self._level_cbs:
            if cb.IsChecked:
                self._lvl_checked.add(name)

        self.arch_name       = arch
        self.cons_names      = selected_cons
        self.selected_levels = sorted(self._lvl_checked)

        # Persist to session store
        _SESSION["arch_name"]   = arch
        _SESSION["cons_names"]  = set(selected_cons)
        _SESSION["lvl_checked"] = set(self._lvl_checked)

        self.cancelled = False
        self._window.Close()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC
    # ─────────────────────────────────────────────────────────────────────────

    def show(self):
        """
        Show non-blocking via DispatcherFrame so Revit remains interactive.
        """
        clr.AddReference("WindowsBase")
        from System.Windows.Threading import Dispatcher, DispatcherFrame

        window = self._build()
        frame  = DispatcherFrame()

        def on_closed(sender, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        Dispatcher.PushFrame(frame)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    links = get_loaded_links(doc)
    if len(links) < 2:
        forms.alert(
            "Need at least 2 loaded Revit links.\nFound: {}".format(len(links)),
            title="Level Coordination Review"
        )
        return

    # ── Show UI ───────────────────────────────────────────────────────────────
    dlg = SelectionDialog(links)
    dlg.show()

    if dlg.cancelled:
        output.print_md(u"\u2139  **Cancelled.**")
        return

    arch_info  = next((l for l in links if l["filename"] == dlg.arch_name), None)
    cons_links = [l for l in links if l["filename"] in dlg.cons_names]

    if arch_info is None or not cons_links:
        forms.alert("Could not resolve selected links.", title="Error")
        return

    # ── Save path ─────────────────────────────────────────────────────────────
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    save_path = forms.save_file(file_ext="xlsx",
                                default_name="LevelCoordinationReport_{}".format(today_str))
    if not save_path:
        output.print_md(u"\u2139  **No save path selected. Aborted.**")
        return

    # Ensure the file always ends with .xlsx
    if not save_path.lower().endswith(".xlsx"):
        save_path = save_path + ".xlsx"

    # ── Collect & compare ─────────────────────────────────────────────────────
    output.print_md("## Level Coordination Review")
    output.print_md("**Architecture reference:** `{}`".format(dlg.arch_name))
    output.print_md("**Consultant links:** {}".format(len(cons_links)))
    if dlg.selected_levels:
        output.print_md("**Level filter active:** {} levels — {}".format(
            len(dlg.selected_levels),
            u", ".join(dlg.selected_levels[:5]) +
            (u"…" if len(dlg.selected_levels) > 5 else u"")))
    else:
        output.print_md("**Level filter:** none — comparing all levels")
    output.print_md("---")

    # ── Log PBP elevations for verification (debug=True shows all params) ──
    output.print_md("### PBP Elevation Debug")
    arch_pbp = get_pbp_elevation(arch_info["doc"], debug=True)
    output.print_md("**Arch `{}` → PBP = {:.4f} ft = {:.2f} cm**".format(
        arch_info["filename"], arch_pbp, ft_to_cm(arch_pbp)))
    output.print_md("---")
    for cli in cons_links:
        n_inst = len(cli.get("instances", [cli["instance"]]))
        pbp    = get_pbp_elevation(cli["doc"], debug=True)
        output.print_md(
            "**Consultant `{}` ({} instance{}) → PBP = {:.4f} ft = {:.2f} cm**".format(
                cli["filename"], n_inst, "s" if n_inst > 1 else "",
                pbp, ft_to_cm(pbp)))
        output.print_md("---")

    arch_rows   = get_levels_data(arch_info, debug_output=True)
    output.print_md("---")
    results_map = {}          # filename → [enriched rows]

    first_cons = True
    for cli in cons_links:
        cons_rows    = get_levels_data(cli, debug_output=first_cons)
        first_cons   = False
        level_filter = dlg.selected_levels if dlg.selected_levels else None
        rows         = compare_levels(arch_rows, cons_rows, level_filter=level_filter)
        fname     = cli["filename"]
        results_map[fname] = rows

        # Print breakdown to pyRevit output
        c = {S_GREEN: 0, S_RED: 0, S_ORANGE: 0}
        for r in rows:
            s = r["status"]
            if s in (S_GREEN, S_OK):
                c[S_GREEN] += 1
            elif s in (S_RED, S_HEIGHT):
                c[S_RED] += 1
            else:
                c[S_ORANGE] += 1
        n_inst = len(cli.get("instances", [cli["instance"]]))
        inst_label = " ({}x)".format(n_inst) if n_inst > 1 else ""
        output.print_md(
            "**{fn}{il}**  "
            "| ✅ 🟢 OK: {g}  "
            "| 🔴 Height mismatch: {r}  "
            "| 🟠 Wrong name / unknown: {o}".format(
                fn=fname, il=inst_label,
                g=c[S_GREEN], r=c[S_RED], o=c[S_ORANGE]
            )
        )

    # ── Build Excel ───────────────────────────────────────────────────────────
    output.print_md(u"---\nGenerating report\u2026")
    # link_info_map: filename → link dict (needed for Tier-1 discipline lookup)
    link_info_map = {cli["filename"]: cli for cli in cons_links}
    try:
        build_excel(arch_info, results_map, save_path,
                    link_info_map=link_info_map, arch_rows=arch_rows)
        output.print_md(u"\u2705 **Report saved:** `{}`".format(save_path))
    except Exception as ex:
        output.print_md(u"\u274c **Export error:** `{}`".format(ex))
        output.print_md("```\n{}\n```".format(traceback.format_exc()))
        return

    # ── Build interactive HTML report (same folder/basename as the .xlsx) ────
    html_path = save_path[:-len(".xlsx")] + ".html"
    try:
        build_html(arch_info, arch_rows, results_map, html_path,
                   link_info_map=link_info_map)
        output.print_md(u"✅ **HTML report saved:** `{}`".format(html_path))
    except Exception as ex:
        output.print_md(u"❌ **HTML export error:** `{}`".format(ex))
        output.print_md("```\n{}\n```".format(traceback.format_exc()))
        html_path = None

    # ── Open files ─────────────────────────────────────────────────────────────
    try:
        import subprocess
        subprocess.Popen(["explorer", save_path])
    except Exception:
        pass

    if html_path:
        try:
            os.startfile(html_path)
        except Exception:
            pass

    output.print_md(u"\u2705 **Done!**")


main()
