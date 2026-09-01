# -*- coding: utf-8 -*-
"""
Delete Duplicate Elements

Finds elements that Revit itself already flags with the built-in warning
"There are identical instances in the same place" and deletes all but one
copy of each group. This is the same check that produces the yellow warning
dialog, so it is language- and category-independent — it works for whatever
Revit itself considers a duplicate (furniture, fixtures, rooms, etc.), not
just elements that merely look alike.

That warning is unreliable for a lot of elements — two perfectly overlapping
ducts/pipes/walls/beams, or two point-placed instances sitting in the exact
same spot, routinely produce no Revit warning at all. Every physical model
element (not just a hardcoded category list) also gets a geometry-based
check — same category + matching position/shape (curve endpoints, insertion
point, or bounding box, whichever the element has) — instead of relying on
the warning alone (see find_geometric_duplicates).

The tool walks through a small 2-step dialog (styled to match EasyBIM's other
wizard tools, e.g. Solution Section):
  1. Scope   — Current Selection / Active View / Entire Model
  2. Review  — every element/group about to be deleted, with checkboxes to
               keep specific items, followed by a result summary

Group instances get special treatment: if every member of a Model Group
instance is itself flagged as a duplicate of a member in one specific other
Group instance, the whole duplicate Group instance is deleted as a unit
(rather than leaving orphaned members behind). Elements that belong to a
Group but are only *partially* duplicated are skipped, since resolving that
requires the user to edit the group directly. Pinned elements are skipped
too.
"""

import clr
import traceback
from collections import defaultdict

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
from System.Windows.Interop import WindowInteropHelper
from System.IO import StringReader
from System.Xml import XmlReader as SysXmlReader

from pyrevit import revit, script, forms

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

SCOPE_SELECTION = "Current Selection"
SCOPE_VIEW = "Active View"
SCOPE_MODEL = "Entire Model"

SCOPE_OPTIONS = [
    (SCOPE_SELECTION, "Only duplicate groups fully contained in your current selection"),
    (SCOPE_VIEW, "Only duplicate groups fully contained in the active view"),
    (SCOPE_MODEL, "Every duplicate group in the project"),
]


# ─────────────────────────────────────────────────────────────────────────────
# DUPLICATE DETECTION / DELETION
# ─────────────────────────────────────────────────────────────────────────────

def is_duplicate_warning(msg):
    """True if a FailureMessage is Revit's "identical instances" warning."""
    try:
        if msg.GetFailureDefinitionId() == DB.BuiltInFailures.OverlapFailures.DuplicateInstances:
            return True
    except Exception:
        pass
    # Fallback for API versions where the FailureDefinitionId above isn't
    # available — match the (English) warning text instead.
    text = msg.GetDescriptionText() or ""
    return "identical instance" in text.lower()


# Revit's own "identical instances" warning does not reliably fire for
# every kind of duplicate — two perfectly overlapping duct/pipe/wall/beam
# segments routinely produce no warning at all, and neither do some
# point-placed instances. Every physical model element gets a geometry-based
# duplicate check of its own (see find_geometric_duplicates) instead of
# relying only on doc.GetWarnings().
GEOMETRIC_DUP_TOLERANCE = 0.01  # feet (~3mm) — matches Revit's own snap tolerance


def get_scope_ids(scope):
    """IntegerValue set of elements allowed for the chosen scope, None for
    Entire Model (no filtering needed), or False if the scope is invalid
    (Current Selection with nothing selected)."""
    if scope == SCOPE_SELECTION:
        ids = list(uidoc.Selection.GetElementIds())
        if not ids:
            return False
        return set(eid.IntegerValue for eid in ids)
    elif scope == SCOPE_VIEW:
        view_ids = DB.FilteredElementCollector(doc, revit.active_view.Id) \
            .WhereElementIsNotElementType() \
            .ToElementIds()
        return set(eid.IntegerValue for eid in view_ids)
    return None  # Entire Model


def _rounded_endpoint_key(curve):
    """Curve endpoints (rounded to GEOMETRIC_DUP_TOLERANCE, sorted so the
    key doesn't depend on which end is "start" vs "end") plus the curve's
    own length. Endpoints alone are enough for a straight segment (a line
    *is* its endpoints) — but OST_FlexDuctCurves/OST_FlexPipeCurves only
    have their two connector points fixed; two different physical flex
    routes between the same two points (e.g. routed around different
    obstacles) would otherwise collide into the same bucket as false
    duplicates. Length is a cheap, effective discriminator: two distinct
    paths between the same two points essentially never happen to also
    have the identical length. This also narrows the equivalent (rarer)
    risk for curved walls/framing that share a chord's endpoints but not
    its bulge."""
    def rnd(pt):
        return (
            round(pt.X / GEOMETRIC_DUP_TOLERANCE),
            round(pt.Y / GEOMETRIC_DUP_TOLERANCE),
            round(pt.Z / GEOMETRIC_DUP_TOLERANCE),
        )
    a, b = rnd(curve.GetEndPoint(0)), rnd(curve.GetEndPoint(1))
    length_key = round(curve.Length / GEOMETRIC_DUP_TOLERANCE)
    return (tuple(sorted((a, b))), length_key)


def _rounded_point_key(pt):
    """A point's coordinates rounded to GEOMETRIC_DUP_TOLERANCE."""
    return (
        round(pt.X / GEOMETRIC_DUP_TOLERANCE),
        round(pt.Y / GEOMETRIC_DUP_TOLERANCE),
        round(pt.Z / GEOMETRIC_DUP_TOLERANCE),
    )


def _rounded_bbox_key(bbox):
    """A bounding box's Min/Max corners, each rounded to
    GEOMETRIC_DUP_TOLERANCE. This is the fallback for elements that have
    neither a LocationCurve nor a LocationPoint (e.g. some in-place
    families, imports, or link instances) — coarser than an exact
    curve/point match, but still enough to catch two literal copies
    sitting in the exact same place."""
    return (_rounded_point_key(bbox.Min), _rounded_point_key(bbox.Max))


def _geometry_signature(elem):
    """Identify an element's physical position/shape for duplicate
    matching. Returns (geometry_type, signature) or None if the element has
    nothing usable to compare (no Location and no BoundingBox) — those
    elements are skipped entirely since there is no reliable "same place"
    check for them.

    - LocationCurve elements (ducts, pipes, walls, framing, ...) key off
      endpoints + length (see _rounded_endpoint_key).
    - LocationPoint elements (furniture, fixtures, rooms, columns, ...) key
      off the rounded insertion point. Rotation/Type are deliberately not
      part of the key, same as the curve-based check — anything this flags
      still goes through the review screen before deletion, so a
      same-position/different-type (or different-rotation) pair can simply
      be unticked if it turns out not to be a real duplicate.
    - Anything else falls back to its model bounding box, rounded Min/Max.
    """
    loc = elem.Location
    if isinstance(loc, DB.LocationCurve):
        try:
            return ("curve", _rounded_endpoint_key(loc.Curve))
        except Exception:
            return None
    if isinstance(loc, DB.LocationPoint):
        try:
            return ("point", _rounded_point_key(loc.Point))
        except Exception:
            return None
    try:
        bbox = elem.get_BoundingBox(None)
    except Exception:
        bbox = None
    if bbox is None:
        return None
    try:
        return ("bbox", _rounded_bbox_key(bbox))
    except Exception:
        return None


def _scoped_model_collector(scope_ids):
    """FilteredElementCollector narrowed to scope_ids up front (Current
    Selection / Active View) via the FilteredElementCollector(doc, ids)
    constructor, instead of walking every element in the whole document
    and discarding most of them one at a time in Python — the latter turns
    even a 2-element Current Selection check into a full-model scan, which
    on a real project is slow enough to make the dialog look hung (no
    progress indicator, so a multi-second synchronous scan on the UI
    thread reads as an unresponsive Next button). Entire Model (scope_ids
    is None) still needs the full document."""
    if scope_ids is not None:
        ids = SCG.List[DB.ElementId]([DB.ElementId(iv) for iv in scope_ids])
        return DB.FilteredElementCollector(doc, ids).WhereElementIsNotElementType()
    return DB.FilteredElementCollector(doc).WhereElementIsNotElementType()


def find_geometric_duplicates(scope_ids):
    """Group every physical model element (any category — not a hardcoded
    list) that occupies the exact same position/shape (within
    GEOMETRIC_DUP_TOLERANCE, see _geometry_signature) — this is the fallback
    for whatever Revit's own "identical instances" warning doesn't reliably
    cover: line-based elements (ducts/pipes/walls/framing/...) as well as
    point-placed instances that Revit's warning happens to miss.

    Only elements whose Category.CategoryType is Model are considered —
    this excludes annotation, view-specific, and internal elements (text,
    tags, dimensions, view elements, etc.) that could never be a physical
    duplicate in the first place. Elements with no usable geometry (no
    Location, no BoundingBox — e.g. most Materials) are skipped by
    _geometry_signature returning None.

    Matching is by category + shape only, NOT by Type: two elements of the
    same category that share the same position are already an overlap
    regardless of which Type each happens to reference (a duplicate created
    via copy/paste-in-place, an import, or a family swap can easily end up
    on a different Type than the original). Anything this flags still goes
    through the same review screen before deletion, so a same-position/
    different-type pair can simply be unticked if it turns out not to be a
    real duplicate.

    Returns:
        groups_found - number of overlapping-geometry clusters found
        clusters     - list of lists of ElementId; each inner list holds
                       every element (>= 2) found occupying the same
                       position/shape. Deciding which one "survives" is left
                       to the caller (find_duplicates_to_delete), which
                       merges these clusters with the warning-based ones
                       before picking a single global survivor per physical
                       duplicate.
    """
    buckets = defaultdict(list)  # (CategoryId, geometry_type, signature) -> [ElementId]
    for elem in _scoped_model_collector(scope_ids):
        cat = elem.Category
        if cat is None or cat.CategoryType != DB.CategoryType.Model:
            continue
        sig = _geometry_signature(elem)
        if sig is None:
            continue
        geom_type, signature = sig
        key = (cat.Id.IntegerValue, geom_type, signature)
        buckets[key].append(elem.Id)

    clusters = [ids for ids in buckets.values() if len(ids) >= 2]
    return len(clusters), clusters


def debug_geometric_candidates(scope_ids):
    """Diagnostic dump of every candidate in scope — prints category, Id,
    type, and the geometry signature find_geometric_duplicates would use
    (or the reason an element was skipped), so a pair that still isn't
    grouped can be compared by eye. Only meant for a small scope (Current
    Selection)."""
    rows = []
    for elem in _scoped_model_collector(scope_ids):
        cat = elem.Category
        cat_name = cat.Name if cat else "?"
        if cat is None or cat.CategoryType != DB.CategoryType.Model:
            rows.append((elem.Id.IntegerValue, cat_name, "?", "skipped (not a Model category)"))
            continue
        type_elem = doc.GetElement(elem.GetTypeId())
        type_name = type_elem.Name if type_elem is not None else "?"
        sig = _geometry_signature(elem)
        if sig is None:
            info = "skipped (no Location/BoundingBox)"
        else:
            geom_type, signature = sig
            info = "{}: {}".format(geom_type, signature)
        rows.append((elem.Id.IntegerValue, cat_name, type_name, info))

    if not rows:
        output.print_md("### Debug — no elements found in this scope")
        return

    output.show()
    output.print_md("### Debug — geometric duplicate candidates in scope")
    for iv, cat, tname, info in rows:
        output.print_md("- Id `{}` — {} / {} — {}".format(iv, cat, tname, info))


class _DSU(object):
    """Minimal union-find so a single global survivor is chosen per
    physical duplicate cluster, even when the warning-based and
    geometric-based detectors report overlapping but not identical
    pairings for the same element. Without this, an element that is "the
    original" in one pairing but also "a duplicate" in a separate,
    unrelated pairing could end up simultaneously protected from deletion
    (via the first pairing) and promoted for whole-group deletion (via the
    second) — deleting both the duplicate AND the element that should have
    survived. Merging every pairing that touches a given element into one
    cluster first means there is exactly one keep/delete decision per
    physical duplicate, never one decision per detector per pairing."""

    def __init__(self):
        self._parent = {}

    def find(self, x):
        parent = self._parent
        if x not in parent:
            parent[x] = x
            return x
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def clusters(self):
        groups = defaultdict(set)
        for x in self._parent:
            groups[self.find(x)].add(x)
        return list(groups.values())


def find_duplicates_to_delete(scope_ids):
    """Scan Revit's own duplicate-instance warnings, plus a geometric
    fallback check across every physical model element (see
    find_geometric_duplicates), and sort the failing elements into what can
    be safely deleted.

    Every pairing/cluster either detector reports is first merged into a
    union-find (_DSU) rather than immediately deciding keep/delete per
    pairing — an element that shows up in more than one pairing (e.g. the
    survivor of one duplicate pair that is *itself* a duplicate of some
    unrelated third element) is thereby resolved as a single physical
    cluster with exactly one global survivor (its lowest Element Id), so
    the keep/delete decision can never contradict itself between pairings.

    Returns:
        groups_in_scope  - number of duplicate-instance warnings/clusters
                            processed
        element_deletes  - {IntegerValue: ElementId} standalone elements
        group_deletes    - {IntegerValue: ElementId} whole Group instances
                            whose every member duplicates another group
        skipped_grouped  - [(ElementId, reason)] grouped elements that are
                            only *partially* duplicated and can't be safely
                            auto-resolved
        kept_for         - {IntegerValue: ElementId} the surviving element
                            each deleted element/group was a duplicate of —
                            since it occupies the exact same spot, it's what
                            to point the user at afterwards to see "what
                            changed" (the deleted thing obviously can't be
                            shown once it's gone)
    """
    dsu = _DSU()
    groups_in_scope = 0

    for msg in doc.GetWarnings():
        if not is_duplicate_warning(msg):
            continue

        failing_ids = list(msg.GetFailingElements())
        if len(failing_ids) < 2:
            continue
        if scope_ids is not None and not all(eid.IntegerValue in scope_ids for eid in failing_ids):
            continue  # part of this duplicate group falls outside the chosen scope

        groups_in_scope += 1
        ivs = [eid.IntegerValue for eid in failing_ids]
        for iv in ivs[1:]:
            dsu.union(ivs[0], iv)

    # Geometric fallback across every physical model element, for whatever
    # Revit's own warning doesn't reliably cover (see
    # find_geometric_duplicates).
    geo_groups, geo_clusters = find_geometric_duplicates(scope_ids)
    groups_in_scope += geo_groups
    for cluster in geo_clusters:
        ivs = [eid.IntegerValue for eid in cluster]
        for iv in ivs[1:]:
            dsu.union(ivs[0], iv)

    # Resolve one global survivor per merged cluster — the lowest Element
    # Id — and build the same to_delete/kept_of/keep_ivs shape the rest of
    # this function (and the group-promotion logic below) expects.
    to_delete = {}
    to_keep = set()
    kept_of = {}  # dup IntegerValue -> kept ElementId (element-level)

    # dup group IntegerValue -> set of that group's member IntegerValues
    # seen as duplicates
    group_dup_members = defaultdict(set)
    # dup group IntegerValue -> kept group's ElementId
    group_kept_of = {}

    for members in dsu.clusters():
        if len(members) < 2:
            continue
        ordered = sorted(members)
        keep_iv, dupe_ivs = ordered[0], ordered[1:]
        to_keep.add(keep_iv)
        keep = DB.ElementId(keep_iv)
        keep_elem = doc.GetElement(keep)
        keep_group_id = keep_elem.GroupId if keep_elem is not None else DB.ElementId.InvalidElementId

        for iv in dupe_ivs:
            eid = DB.ElementId(iv)
            to_delete[iv] = eid
            kept_of[iv] = keep
            dup_elem = doc.GetElement(eid)
            if dup_elem is not None and dup_elem.GroupId != DB.ElementId.InvalidElementId:
                gid = dup_elem.GroupId.IntegerValue
                group_dup_members[gid].add(iv)
                if keep_group_id != DB.ElementId.InvalidElementId:
                    group_kept_of.setdefault(gid, keep_group_id)

    # Promote a Group instance to a whole-group deletion when every one of
    # its members was independently flagged as a duplicate — that means the
    # entire group instance is a copy of another group sitting in the same
    # place, so it can be deleted as a unit instead of leaving it hollowed
    # out with dangling members.
    group_deletes = {}
    kept_for = {}
    for gid, dup_member_ids in group_dup_members.items():
        if gid in to_keep:
            continue  # this instance was itself treated as "the original" elsewhere
        group_elem_id = DB.ElementId(gid)
        group_elem = doc.GetElement(group_elem_id)
        if group_elem is None or not isinstance(group_elem, DB.Group):
            continue
        member_ids = set(m.IntegerValue for m in group_elem.GetMemberIds())
        if member_ids and member_ids.issubset(dup_member_ids):
            group_deletes[gid] = group_elem_id
            if gid in group_kept_of:
                kept_for[gid] = group_kept_of[gid]
            for mid in member_ids:
                to_delete.pop(mid, None)

    # Whatever is left in to_delete but still belongs to a group is only a
    # *partial* duplicate of that group — deleting individual members would
    # corrupt the group, so leave it for the user to resolve by hand.
    skipped_grouped = []
    for iv in list(to_delete.keys()):
        eid = to_delete[iv]
        elem = doc.GetElement(eid)
        if elem is not None and elem.GroupId != DB.ElementId.InvalidElementId:
            skipped_grouped.append((eid, "part of a group that is only partially duplicated"))
            del to_delete[iv]

    for iv in to_delete:
        if iv in kept_of:
            kept_for[iv] = kept_of[iv]

    return groups_in_scope, to_delete, group_deletes, skipped_grouped, kept_for


def element_row_text(eid):
    elem = doc.GetElement(eid)
    cat_name = elem.Category.Name if elem and elem.Category else "Unknown category"
    return cat_name, "Id {}".format(eid.IntegerValue)


def group_row_text(eid):
    group_elem = doc.GetElement(eid)
    member_count = len(list(group_elem.GetMemberIds()))
    type_id = group_elem.GetTypeId()
    type_name = doc.GetElement(type_id).Name if type_id != DB.ElementId.InvalidElementId else "Group"
    subtitle = "{} member{} · Id {}".format(
        member_count, "" if member_count == 1 else "s", eid.IntegerValue
    )
    return "Group '{}'".format(type_name), subtitle


def build_review_items(element_deletes, group_deletes):
    """Return a flat list of {kind, eid, title, subtitle, on} dicts, groups
    first (they're the more consequential deletions)."""
    items = []
    for eid in group_deletes.values():
        title, subtitle = group_row_text(eid)
        items.append({"kind": "group", "eid": eid, "title": title, "subtitle": subtitle, "on": True})
    for eid in element_deletes.values():
        title, subtitle = element_row_text(eid)
        items.append({"kind": "element", "eid": eid, "title": title, "subtitle": subtitle, "on": True})
    return items


def delete_elements(element_ids, group_ids):
    """Delete the confirmed candidates inside a single transaction, skipping
    anything that can't be safely deleted and recording anything Revit
    itself refuses. Groups are deleted first so their members don't show up
    as "already gone" noise in the report."""
    deleted, skipped = [], []

    with revit.Transaction("Delete Duplicate Elements"):
        for eid in group_ids:
            group_elem = doc.GetElement(eid)
            if group_elem is None:
                continue  # already gone
            try:
                result = doc.Delete(eid)
                if result and result.Count > 0:
                    deleted.append(eid)
                else:
                    skipped.append((eid, "Revit did not delete it"))
            except Exception as ex:
                skipped.append((eid, str(ex)))

        for eid in element_ids:
            element = doc.GetElement(eid)
            if element is None:
                continue  # already gone (e.g. cascade-deleted with an earlier duplicate)

            if element.Pinned:
                skipped.append((eid, "pinned"))
                continue
            if element.GroupId != DB.ElementId.InvalidElementId:
                skipped.append((eid, "part of a group"))
                continue

            try:
                result = doc.Delete(eid)
                if result and result.Count > 0:
                    deleted.append(eid)
                else:
                    skipped.append((eid, "Revit did not delete it"))
            except Exception as ex:
                skipped.append((eid, str(ex)))

    return deleted, skipped


def print_report(scope, groups_in_scope, deleted, skipped, kept_for):
    output.print_md("# Delete Duplicate Elements — Report")
    output.print_md("**Scope:** {}".format(scope))
    output.print_md("**Duplicate groups processed:** {}".format(groups_in_scope))
    output.print_md("**Elements/groups deleted:** {}".format(len(deleted)))
    output.print_md("**Elements/groups skipped:** {}".format(len(skipped)))

    if deleted:
        output.print_md("### Deleted")
        output.print_md(
            "_A deleted element can't be shown — it's gone. Click through to the "
            "surviving copy instead; it sits in the exact same spot._"
        )
        for eid in deleted:
            kept_id = kept_for.get(eid.IntegerValue)
            kept_elem = doc.GetElement(kept_id) if kept_id else None
            if kept_elem is not None:
                output.print_md("- Id {} (deleted) — kept: {}".format(
                    eid.IntegerValue, output.linkify(kept_id)))
            else:
                output.print_md("- Id {} (deleted)".format(eid.IntegerValue))

    if skipped:
        output.print_md("### Skipped")
        for eid, reason in skipped:
            output.print_md("- {} — {}".format(output.linkify(eid), reason))


# ─────────────────────────────────────────────────────────────────────────────
# DIALOG — styled to match EasyBIM's wizard tools (e.g. Solution Section)
# ─────────────────────────────────────────────────────────────────────────────

XAML = """
<Window
  xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
  xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
  Title="Delete Duplicates"
  Width="560" Height="660"
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
          <Canvas Width="24" Height="24" HorizontalAlignment="Center" VerticalAlignment="Center">
            <Rectangle Canvas.Left="2" Canvas.Top="2" Width="11" Height="11" RadiusX="1.5" RadiusY="1.5"
                       Stroke="White" StrokeThickness="1.6"/>
            <Rectangle Canvas.Left="7" Canvas.Top="7" Width="11" Height="11" RadiusX="1.5" RadiusY="1.5"
                       Stroke="White" StrokeThickness="1.6"/>
            <Ellipse Canvas.Left="13.6" Canvas.Top="13.6" Width="8.8" Height="8.8"
                     Stroke="White" StrokeThickness="1.6"/>
            <Path Data="M16.5,16.5 L19.5,19.5 M19.5,16.5 L16.5,19.5"
                  Stroke="White" StrokeThickness="1.5" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
          </Canvas>
        </Border>
        <StackPanel Grid.Column="1" VerticalAlignment="Center" Margin="14,0,0,0">
          <TextBlock Text="Delete Duplicates" FontSize="16" FontWeight="Bold" Foreground="White"/>
          <TextBlock Text="Duplicate element cleanup · Revit warning-based detection"
                     FontSize="10" Foreground="#b8d8f0" Margin="0,3,0,0"/>
        </StackPanel>
        <Button x:Name="CloseBtn" Grid.Column="2" Content="&#x2715;" Width="28" Height="28"
                Background="Transparent" Foreground="White" BorderThickness="0"
                FontSize="13" Cursor="Hand" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- BANNER -->
    <Border x:Name="BannerBorder" Grid.Row="1"
            Background="#ecf8fc" BorderBrush="#bbe5f0" BorderThickness="0,0,0,1" Padding="20,10">
      <StackPanel Orientation="Horizontal">
        <TextBlock Text="i" FontSize="13" FontWeight="Bold" Foreground="#44b8d3"
                   Margin="0,0,9,0" VerticalAlignment="Center"/>
        <TextBlock x:Name="BannerText" FontSize="12.5" Foreground="#1e6e87"
                   TextWrapping="Wrap" VerticalAlignment="Center"/>
      </StackPanel>
    </Border>

    <!-- STEPPER -->
    <Grid Grid.Row="2" Margin="24,14,24,10">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="*"/>
      </Grid.ColumnDefinitions>
      <Border Grid.Column="0" Grid.ColumnSpan="2" Height="1.5" Background="#e0e3f8"
              VerticalAlignment="Top" Margin="70,13,70,0"/>
      <StackPanel Grid.Column="0" HorizontalAlignment="Center">
        <Border x:Name="SC1" Width="28" Height="28" CornerRadius="14" Background="#1e248c" HorizontalAlignment="Center">
          <TextBlock x:Name="SN1" Text="1" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL1" Text="Scope" FontSize="10" TextAlignment="Center"
                   Foreground="#1e248c" FontWeight="SemiBold" Margin="0,5,0,0"/>
      </StackPanel>
      <StackPanel Grid.Column="1" HorizontalAlignment="Center">
        <Border x:Name="SC2" Width="28" Height="28" CornerRadius="14" Background="#c6cbe0" HorizontalAlignment="Center">
          <TextBlock x:Name="SN2" Text="2" FontSize="12" FontWeight="SemiBold" Foreground="White"
                     HorizontalAlignment="Center" VerticalAlignment="Center"/>
        </Border>
        <TextBlock x:Name="SL2" Text="Review &amp; Delete" FontSize="10" TextAlignment="Center"
                   Foreground="#9aa0ac" Margin="0,5,0,0"/>
      </StackPanel>
    </Grid>

    <!-- BODY -->
    <ScrollViewer Grid.Row="3" VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled">
      <Grid Margin="20,6,20,10">

        <!-- STEP 1: Scope -->
        <StackPanel x:Name="Step1Panel">
          <TextBlock Text="SCOPE" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,7"/>
          <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8">
            <StackPanel x:Name="ScopeListPanel"/>
          </Border>
        </StackPanel>

        <!-- STEP 2: Review + Result -->
        <StackPanel x:Name="Step2Panel" Visibility="Collapsed">

          <StackPanel x:Name="ReviewPanel">
            <Grid Margin="0,0,0,9">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              <TextBlock x:Name="ReviewLabel" Grid.Column="0" FontFamily="Consolas" FontSize="10"
                         Foreground="#9aa0ac" VerticalAlignment="Center"/>
              <Button x:Name="ToggleAllBtn" Grid.Column="1" Content="Select all"
                      Style="{StaticResource CyanTextBtn}"/>
            </Grid>
            <Border Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" MaxHeight="300">
              <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="ReviewListPanel"/>
              </ScrollViewer>
            </Border>
            <StackPanel Orientation="Horizontal" Margin="2,10,0,0">
              <TextBlock Text="i" FontSize="12" FontWeight="Bold" Foreground="#44b8d3"
                         Margin="0,0,7,0" VerticalAlignment="Center"/>
              <TextBlock Text="Pinned elements and partially-duplicated groups are excluded automatically."
                         FontSize="11.5" Foreground="#9aa0ac" TextWrapping="Wrap"/>
            </StackPanel>
          </StackPanel>

          <!-- Result panel -->
          <StackPanel x:Name="ResultPanel" Visibility="Collapsed">
            <StackPanel HorizontalAlignment="Center" Margin="0,4,0,18">
              <Border x:Name="ResultBadge" Width="52" Height="52" CornerRadius="26" Background="#e4f7f0"
                      HorizontalAlignment="Center" Margin="0,0,0,12">
                <TextBlock x:Name="ResultBadgeIcon" Text="&#x2713;" FontSize="26" FontWeight="Bold" Foreground="#22b07c"
                           HorizontalAlignment="Center" VerticalAlignment="Center"/>
              </Border>
              <TextBlock x:Name="ResultTitle" Text="Duplicates deleted" FontSize="18" FontWeight="Bold"
                         Foreground="#1e248c" HorizontalAlignment="Center"/>
              <TextBlock x:Name="Res_Summary" FontSize="12.5" Foreground="#6b7280"
                         HorizontalAlignment="Center" Margin="0,3,0,0" TextWrapping="Wrap" TextAlignment="Center"/>
            </StackPanel>

            <TextBlock x:Name="DeletedHeader" Text="DELETED" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,7"/>
            <Border x:Name="DeletedCard" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" Margin="0,0,0,14" MaxHeight="180">
              <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="DeletedListPanel"/>
              </ScrollViewer>
            </Border>

            <TextBlock x:Name="SkippedHeader" Text="SKIPPED" FontFamily="Consolas" FontSize="10" Foreground="#9aa0ac" Margin="0,0,0,7" Visibility="Collapsed"/>
            <Border x:Name="SkippedCard" Background="White" BorderBrush="#e8eaff" BorderThickness="1" CornerRadius="8" MaxHeight="180" Visibility="Collapsed">
              <ScrollViewer VerticalScrollBarVisibility="Auto">
                <StackPanel x:Name="SkippedListPanel"/>
              </ScrollViewer>
            </Border>
          </StackPanel>

        </StackPanel>
      </Grid>
    </ScrollViewer>

    <!-- FOOTER -->
    <Border Grid.Row="4" Background="White" BorderBrush="#e8eaff" BorderThickness="0,1,0,0" Padding="20,12">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="StepLabel" Grid.Column="0" Text="Step 1 of 2"
                   FontFamily="Consolas" FontSize="12" Foreground="#9aa0ac" VerticalAlignment="Center"/>
        <StackPanel Grid.Column="1" Orientation="Horizontal">
          <Button x:Name="CancelBtn"    Content="Cancel"                Style="{StaticResource GhostBtn}"/>
          <Button x:Name="BackBtn"      Content="&#x25C0;  Back"        Style="{StaticResource GhostBtn}"   Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="NextBtn"      Content="Next  &#x25B6;"        Style="{StaticResource PrimaryBtn}" Margin="8,0,0,0"/>
          <Button x:Name="DeleteBtn"    Content="Delete Selected"       Style="{StaticResource PrimaryBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
          <Button x:Name="CloseDoneBtn" Content="Close"                 Style="{StaticResource PrimaryBtn}" Visibility="Collapsed" Margin="8,0,0,0"/>
        </StackPanel>
      </Grid>
    </Border>

  </Grid>
</Window>
"""

BANNERS = [
    "Choose which part of the model to check for duplicate elements — this uses Revit's own "
    "\"identical instances\" warning plus a geometry check across every physical model element, "
    "so it works for any category.",
    "Review what will be deleted. Untick anything you want to keep — pinned elements and "
    "partially-duplicated groups are already excluded.",
]

NAVY_COLOR  = WM.Color.FromRgb(0x1e, 0x24, 0x8c)
CYAN_COLOR  = WM.Color.FromRgb(0x44, 0xb8, 0xd3)
GRAY_COLOR  = WM.Color.FromRgb(0xc6, 0xcb, 0xe0)
GREEN_COLOR = WM.Color.FromRgb(0x22, 0xb0, 0x7c)
AMBER_COLOR = WM.Color.FromRgb(0xc8, 0x85, 0x0d)
BODY_COLOR  = WM.Color.FromRgb(0x37, 0x41, 0x51)
MUTED_COLOR = WM.Color.FromRgb(0x9a, 0xa0, 0xac)
LINE_COLOR  = WM.Color.FromRgb(0xf0, 0xf1, 0xff)
SEL_BG      = WM.Color.FromArgb(0x16, 0x44, 0xb8, 0xd3)


def _color(c):
    return WM.SolidColorBrush(c)


class DeleteDuplicatesDialog(object):

    def __init__(self):
        self._step = 1
        self._done = False

        self._sel_scope = None
        self._scope_rows = {}

        self._review_rows = []       # [{item, border, cb_outer, cb_check, refresh_cb}]
        self._review_by_iv = {}      # IntegerValue -> item (title/subtitle lookup post-delete)
        self._groups_in_scope = 0
        self._skipped_grouped = []   # [(ElementId, reason)] from find_duplicates_to_delete
        self._kept_for = {}          # IntegerValue -> ElementId, surviving element per deletion

        self._deleted = []
        self._skipped = []

        self._red_ogs_cache = None
        self._amber_ogs_cache = None
        self._solid_fill_id_cache = None
        self._paint_view = None      # the view we're painting; fixed at review time so
                                      # navigating away with _show_element doesn't retarget it

        self.cancelled = True
        self._window = None

    # ── Build ────────────────────────────────────────────────────────────────

    def _build(self):
        ctx = SysXmlReader.Create(StringReader(XAML))
        window = XamlReader.Load(ctx)
        self._window = window
        w = window

        w.FindName("CloseBtn").Click    += self._on_close_x
        w.FindName("CancelBtn").Click   += self._on_cancel
        w.FindName("BackBtn").Click     += self._on_back
        w.FindName("NextBtn").Click     += self._on_next
        w.FindName("DeleteBtn").Click   += self._on_delete
        w.FindName("CloseDoneBtn").Click += self._on_close_done
        w.FindName("ToggleAllBtn").Click += self._on_toggle_all

        self._populate_scope()
        self._go_to_step(1)
        return window

    # ── Step 1: Scope ────────────────────────────────────────────────────────

    def _populate_scope(self):
        panel = self._window.FindName("ScopeListPanel")
        panel.Children.Clear()
        self._scope_rows = {}
        for i, (name, subtitle) in enumerate(SCOPE_OPTIONS):
            is_last = (i == len(SCOPE_OPTIONS) - 1)
            border, icon_tb, title_tb = self._make_scope_row(name, subtitle, is_last)
            panel.Children.Add(border)
            self._scope_rows[name] = (border, icon_tb, title_tb)

    def _make_scope_row(self, name, subtitle, is_last):
        border = WC.Border()
        border.Padding = System.Windows.Thickness(12, 10, 12, 10)
        if not is_last:
            border.BorderBrush = _color(LINE_COLOR)
            border.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)
        border.Background = WM.Brushes.Transparent
        border.Cursor = WI.Cursors.Hand

        row = WC.StackPanel()
        row.Orientation = WC.Orientation.Horizontal

        icon_tb = WC.TextBlock()
        icon_tb.Text = "○"
        icon_tb.FontSize = 14
        icon_tb.Foreground = _color(GRAY_COLOR)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center
        icon_tb.Margin = System.Windows.Thickness(0, 0, 10, 0)

        texts = WC.StackPanel()
        title_tb = WC.TextBlock()
        title_tb.Text = name
        title_tb.FontSize = 13.5
        title_tb.Foreground = _color(BODY_COLOR)
        subtitle_tb = WC.TextBlock()
        subtitle_tb.Text = subtitle
        subtitle_tb.FontSize = 11.5
        subtitle_tb.Foreground = _color(MUTED_COLOR)
        subtitle_tb.Margin = System.Windows.Thickness(0, 2, 0, 0)
        subtitle_tb.TextWrapping = System.Windows.TextWrapping.Wrap
        texts.Children.Add(title_tb)
        texts.Children.Add(subtitle_tb)

        row.Children.Add(icon_tb)
        row.Children.Add(texts)
        border.Child = row

        def on_click(s, e, n=name):
            self._sel_scope = n
            self._update_scope_selection()
            self._update_primary_enabled()

        border.MouseLeftButtonUp += on_click
        return border, icon_tb, title_tb

    def _update_scope_selection(self):
        for name, (border, icon_tb, title_tb) in self._scope_rows.items():
            selected = (name == self._sel_scope)
            if selected:
                border.Background = _color(SEL_BG)
                icon_tb.Text = "✓"
                icon_tb.Foreground = _color(CYAN_COLOR)
                title_tb.FontWeight = System.Windows.FontWeights.SemiBold
            else:
                border.Background = WM.Brushes.Transparent
                icon_tb.Text = "○"
                icon_tb.Foreground = _color(GRAY_COLOR)
                title_tb.FontWeight = System.Windows.FontWeights.Normal

    # ── Step 2: Review ───────────────────────────────────────────────────────

    def _populate_review(self, items):
        panel = self._window.FindName("ReviewListPanel")
        panel.Children.Clear()
        self._review_rows = []
        self._review_by_iv = {}
        self._paint_view = revit.active_view
        for i, item in enumerate(items):
            self._review_by_iv[item["eid"].IntegerValue] = item
            is_last = (i == len(items) - 1)
            row_data = self._make_review_row(item, is_last)
            panel.Children.Add(row_data["border"])
            self._review_rows.append(row_data)
        self._update_review_label()
        self._highlight_checked()

    def _highlight_checked(self):
        """Select the currently-checked candidates and paint them red in the
        active view — a visual preview of exactly what is about to be
        deleted, live-updated as checkboxes are toggled."""
        ids_on = [r["item"]["eid"] for r in self._review_rows if r["item"]["on"]]
        ids_off = [r["item"]["eid"] for r in self._review_rows if not r["item"]["on"]]
        self._set_selection(ids_on)
        self._paint(ids_on, self._red_ogs())
        self._paint(ids_off, DB.OverrideGraphicSettings())

    def _set_selection(self, ids):
        try:
            uidoc.Selection.SetElementIds(SCG.List[DB.ElementId](ids))
        except Exception:
            pass

    def _paint(self, ids, ogs):
        """Apply (or clear, with an empty OverrideGraphicSettings) a graphic
        override on each element, in the view that was active when Review
        was entered (not necessarily the current active view — clicking an
        Id to jump to an element can switch the active view)."""
        if not ids:
            return
        view = self._paint_view or revit.active_view
        try:
            with revit.Transaction("Preview Duplicate Elements"):
                for eid in ids:
                    try:
                        view.SetElementOverrides(eid, ogs)
                    except Exception:
                        pass
        except Exception:
            pass

    def _clear_review_paint(self):
        """Remove every override this dialog applied to the current review
        list — called whenever the review list is abandoned or replaced."""
        all_ids = [r["item"]["eid"] for r in self._review_rows]
        self._paint(all_ids, DB.OverrideGraphicSettings())

    def _show_element(self, eid):
        """Jump Revit's UI to the given element (switching/zooming the
        active view as needed) and select it, so the user can see exactly
        what it is / what changed before deciding to delete it."""
        try:
            ids = SCG.List[DB.ElementId]([eid])
            uidoc.ShowElements(ids)
            uidoc.Selection.SetElementIds(ids)
        except Exception:
            pass

    def _solid_fill_pattern_id(self):
        if self._solid_fill_id_cache is None:
            self._solid_fill_id_cache = DB.ElementId.InvalidElementId
            for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
                if fp.GetFillPattern().IsSolidFill:
                    self._solid_fill_id_cache = fp.Id
                    break
        return self._solid_fill_id_cache

    def _solid_ogs(self, color):
        ogs = DB.OverrideGraphicSettings()
        ogs.SetProjectionLineColor(color)
        ogs.SetProjectionLineWeight(6)
        solid_id = self._solid_fill_pattern_id()
        if solid_id != DB.ElementId.InvalidElementId:
            ogs.SetSurfaceForegroundPatternColor(color)
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternColor(color)
            ogs.SetCutForegroundPatternId(solid_id)
        return ogs

    def _red_ogs(self):
        if self._red_ogs_cache is None:
            self._red_ogs_cache = self._solid_ogs(DB.Color(230, 30, 30))
        return self._red_ogs_cache

    def _amber_ogs(self):
        if self._amber_ogs_cache is None:
            self._amber_ogs_cache = self._solid_ogs(DB.Color(200, 133, 13))
        return self._amber_ogs_cache

    def _make_id_link(self, subtitle_tb, eid):
        """Style a subtitle TextBlock as a clickable link that jumps Revit's
        UI to the element, and wire the click. Stops the click from
        bubbling up to the row's own checkbox-toggle handler."""
        subtitle_tb.Foreground = _color(CYAN_COLOR)
        subtitle_tb.TextDecorations = System.Windows.TextDecorations.Underline
        subtitle_tb.Cursor = WI.Cursors.Hand

        def on_click(s, e, i=eid):
            e.Handled = True
            self._show_element(i)

        subtitle_tb.MouseLeftButtonUp += on_click

    def _make_review_row(self, item, is_last):
        outer = WC.Border()
        outer.Padding = System.Windows.Thickness(12, 10, 12, 10)
        outer.Background = WM.Brushes.White
        outer.Cursor = WI.Cursors.Hand
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        grid = WC.Grid()
        c1 = WC.ColumnDefinition()
        c2 = WC.ColumnDefinition()
        c1.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Star)
        c2.Width = System.Windows.GridLength(1, System.Windows.GridUnitType.Auto)
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
        cb_outer.Cursor = WI.Cursors.Hand

        cb_check = WC.TextBlock()
        cb_check.Text = "✓"
        cb_check.FontSize = 11
        cb_check.FontWeight = System.Windows.FontWeights.Bold
        cb_check.Foreground = WM.Brushes.White
        cb_check.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
        cb_check.VerticalAlignment = System.Windows.VerticalAlignment.Center
        cb_outer.Child = cb_check

        texts = WC.StackPanel()
        title_tb = WC.TextBlock()
        title_tb.Text = item["title"]
        title_tb.FontSize = 12.5
        title_tb.FontWeight = System.Windows.FontWeights.SemiBold
        title_tb.Foreground = _color(BODY_COLOR)
        subtitle_tb = WC.TextBlock()
        subtitle_tb.Text = item["subtitle"]
        subtitle_tb.FontFamily = WM.FontFamily("Consolas")
        subtitle_tb.FontSize = 11
        subtitle_tb.Foreground = _color(MUTED_COLOR)
        subtitle_tb.Margin = System.Windows.Thickness(0, 1, 0, 0)
        self._make_id_link(subtitle_tb, item["eid"])
        texts.Children.Add(title_tb)
        texts.Children.Add(subtitle_tb)

        left.Children.Add(cb_outer)
        left.Children.Add(texts)

        badge = WC.Border()
        is_group = (item["kind"] == "group")
        badge.Background = _color(WM.Color.FromRgb(0xec, 0xf8, 0xfc)) if is_group else _color(WM.Color.FromRgb(0xf1, 0xf2, 0xf7))
        badge.BorderBrush = _color(WM.Color.FromRgb(0xb8, 0xe8, 0xf2)) if is_group else _color(WM.Color.FromRgb(0xdc, 0xdf, 0xe8))
        badge.BorderThickness = System.Windows.Thickness(1)
        badge.CornerRadius = System.Windows.CornerRadius(10)
        badge.Padding = System.Windows.Thickness(7, 2, 7, 2)
        badge.VerticalAlignment = System.Windows.VerticalAlignment.Center
        WC.Grid.SetColumn(badge, 1)
        badge_tb = WC.TextBlock()
        badge_tb.Text = "GROUP" if is_group else "ELEMENT"
        badge_tb.FontSize = 10.5
        badge_tb.FontWeight = System.Windows.FontWeights.SemiBold
        badge_tb.Foreground = _color(CYAN_COLOR) if is_group else _color(MUTED_COLOR)
        badge.Child = badge_tb

        grid.Children.Add(left)
        grid.Children.Add(badge)
        outer.Child = grid

        row_data = {
            "item": item,
            "border": outer,
            "cb_outer": cb_outer,
            "cb_check": cb_check,
        }

        def _refresh_cb(rd=row_data):
            on = rd["item"]["on"]
            if on:
                rd["cb_outer"].Background = _color(NAVY_COLOR)
                rd["cb_outer"].BorderBrush = _color(NAVY_COLOR)
                rd["cb_outer"].BorderThickness = System.Windows.Thickness(1.5)
                rd["cb_check"].Visibility = System.Windows.Visibility.Visible
            else:
                rd["cb_outer"].Background = WM.Brushes.White
                rd["cb_outer"].BorderBrush = _color(GRAY_COLOR)
                rd["cb_outer"].BorderThickness = System.Windows.Thickness(1.5)
                rd["cb_check"].Visibility = System.Windows.Visibility.Collapsed

        row_data["refresh_cb"] = _refresh_cb
        _refresh_cb()

        def on_click(s, e, rd=row_data):
            rd["item"]["on"] = not rd["item"]["on"]
            rd["refresh_cb"]()
            self._update_review_label()
            self._update_primary_enabled()
            self._highlight_checked()

        outer.MouseLeftButtonUp += on_click
        return row_data

    def _update_review_label(self):
        label = self._window.FindName("ReviewLabel")
        total = len(self._review_rows)
        selected = sum(1 for r in self._review_rows if r["item"]["on"])
        label.Text = "{} OF {} ITEM(S) SELECTED".format(selected, total)

    def _on_toggle_all(self, s, e):
        all_on = all(r["item"]["on"] for r in self._review_rows)
        new_state = not all_on
        for r in self._review_rows:
            r["item"]["on"] = new_state
            r["refresh_cb"]()
        self._update_review_label()
        self._update_primary_enabled()
        self._highlight_checked()

    # ── Step / stepper / banner plumbing ─────────────────────────────────────

    def _go_to_step(self, step):
        self._step = step
        vis = System.Windows.Visibility.Visible
        col = System.Windows.Visibility.Collapsed
        w = self._window

        w.FindName("Step1Panel").Visibility = vis if step == 1 else col
        w.FindName("Step2Panel").Visibility = vis if step == 2 else col

        w.FindName("CancelBtn").Visibility = vis if step == 1 and not self._done else col
        w.FindName("BackBtn").Visibility = vis if step == 2 and not self._done else col
        w.FindName("NextBtn").Visibility = vis if step == 1 and not self._done else col
        w.FindName("DeleteBtn").Visibility = vis if step == 2 and not self._done else col
        w.FindName("CloseDoneBtn").Visibility = vis if self._done else col

        w.FindName("StepLabel").Text = "" if self._done else "Step {} of 2".format(step)

        self._update_primary_enabled()
        self._update_stepper(step)
        self._update_banner(step)

    def _update_stepper(self, step):
        circles = [("SC1", "SN1", "SL1"), ("SC2", "SN2", "SL2")]
        for i, (cn, nn, ln) in enumerate(circles):
            s = i + 1
            circle = self._window.FindName(cn)
            num_tb = self._window.FindName(nn)
            lbl_tb = self._window.FindName(ln)
            if self._done or s < step:
                circle.Background = _color(GREEN_COLOR)
                num_tb.Text = "✓"
                lbl_tb.Foreground = _color(GREEN_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.SemiBold
            elif s == step:
                circle.Background = _color(NAVY_COLOR)
                num_tb.Text = str(s)
                lbl_tb.Foreground = _color(NAVY_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.SemiBold
            else:
                circle.Background = _color(GRAY_COLOR)
                num_tb.Text = str(s)
                lbl_tb.Foreground = _color(MUTED_COLOR)
                lbl_tb.FontWeight = System.Windows.FontWeights.Normal

    def _update_banner(self, step):
        w = self._window
        banner_border = w.FindName("BannerBorder")
        banner_text = w.FindName("BannerText")
        if self._done:
            banner_border.Visibility = System.Windows.Visibility.Collapsed
        else:
            banner_border.Visibility = System.Windows.Visibility.Visible
            banner_text.Text = BANNERS[step - 1]

    def _update_primary_enabled(self):
        if self._step == 1:
            self._window.FindName("NextBtn").IsEnabled = self._sel_scope is not None
        elif self._step == 2 and not self._done:
            self._window.FindName("DeleteBtn").IsEnabled = any(r["item"]["on"] for r in self._review_rows)

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_close_x(self, s, e):
        # Always clear — the Result screen leaves amber overrides on
        # whatever was skipped, and those are real, transaction-committed
        # graphic overrides that would otherwise persist into the saved
        # file if left in place (not just a transient UI highlight).
        self._set_selection([])
        self._clear_review_paint()
        self._window.Close()

    def _on_close_done(self, s, e):
        self._set_selection([])
        self._clear_review_paint()
        self._window.Close()

    def _on_cancel(self, s, e):
        self.cancelled = True
        self._set_selection([])
        self._clear_review_paint()
        self._window.Close()

    def _on_back(self, s, e):
        if self._step > 1:
            self._set_selection([])
            self._clear_review_paint()
            self._go_to_step(self._step - 1)

    def _on_next(self, s, e):
        # Wrapped in try/except (unlike a plain WPF Click handler) because an
        # unhandled exception here can otherwise be silently swallowed by the
        # dispatcher — Next would just stop responding with no error shown at
        # all, indistinguishable from a hang.
        try:
            scope_ids = get_scope_ids(self._sel_scope)
            if scope_ids is False:
                forms.alert(
                    "Nothing is selected. Select the elements you want to check first, "
                    "or pick a different scope.",
                    title="Delete Duplicates",
                )
                return

            groups_in_scope, element_deletes, group_deletes, skipped_grouped, kept_for = \
                find_duplicates_to_delete(scope_ids)
            if not element_deletes and not group_deletes:
                if self._sel_scope == SCOPE_SELECTION:
                    debug_geometric_candidates(scope_ids)
                forms.alert(
                    "No duplicate elements were found in this scope ({}).".format(self._sel_scope),
                    title="Delete Duplicates",
                )
                return

            self._groups_in_scope = groups_in_scope
            self._skipped_grouped = skipped_grouped
            self._kept_for = kept_for
            self._populate_review(build_review_items(element_deletes, group_deletes))
            self._go_to_step(2)
        except Exception:
            forms.alert(
                "Delete Duplicates failed while checking for duplicates:\n\n{}".format(
                    traceback.format_exc()),
                title="EasyBIM — Error",
            )

    def _on_delete(self, s, e):
        w = self._window
        delete_btn = w.FindName("DeleteBtn")
        delete_btn.IsEnabled = False
        delete_btn.Content = "Deleting..."

        selected_element_ids = [r["item"]["eid"] for r in self._review_rows
                                 if r["item"]["on"] and r["item"]["kind"] == "element"]
        selected_group_ids = [r["item"]["eid"] for r in self._review_rows
                               if r["item"]["on"] and r["item"]["kind"] == "group"]

        try:
            deleted, skipped = delete_elements(selected_element_ids, selected_group_ids)
            skipped = skipped + self._skipped_grouped
            self._deleted, self._skipped = deleted, skipped

            print_report(self._sel_scope, self._groups_in_scope, deleted, skipped, self._kept_for)
            self._show_result(deleted, skipped)
            self.cancelled = False

            try:
                # Reclaim focus after the delete transaction (Revit's own
                # UI can steal it).
                self._window.Activate()
            except Exception:
                pass
        except Exception:
            delete_btn.IsEnabled = True
            delete_btn.Content = "Delete Selected"
            forms.alert(
                "Delete Duplicates failed:\n\n{}".format(traceback.format_exc()),
                title="EasyBIM — Error",
            )

    def _label_for(self, eid):
        item = self._review_by_iv.get(eid.IntegerValue)
        if item:
            return item["title"], item["subtitle"]
        return element_row_text(eid)  # still exists (wasn't deleted) if we get here

    def _make_result_row(self, eid, icon, icon_color, extra_text, is_last, clickable=False, nav_eid=None):
        title, subtitle = self._label_for(eid)

        outer = WC.Border()
        outer.Padding = System.Windows.Thickness(12, 9, 12, 9)
        if not is_last:
            outer.BorderBrush = _color(LINE_COLOR)
            outer.BorderThickness = System.Windows.Thickness(0, 0, 0, 1)

        row = WC.StackPanel()
        row.Orientation = WC.Orientation.Horizontal

        icon_tb = WC.TextBlock()
        icon_tb.Text = icon
        icon_tb.FontSize = 13
        icon_tb.FontWeight = System.Windows.FontWeights.Bold
        icon_tb.Foreground = _color(icon_color)
        icon_tb.Margin = System.Windows.Thickness(0, 0, 9, 0)
        icon_tb.VerticalAlignment = System.Windows.VerticalAlignment.Center

        texts = WC.StackPanel()
        title_tb = WC.TextBlock()
        title_tb.Text = title if not extra_text else "{} — {}".format(title, extra_text)
        title_tb.FontSize = 12.5
        title_tb.Foreground = _color(BODY_COLOR)
        subtitle_tb = WC.TextBlock()
        subtitle_tb.Text = subtitle
        subtitle_tb.FontFamily = WM.FontFamily("Consolas")
        subtitle_tb.FontSize = 11
        subtitle_tb.Foreground = _color(MUTED_COLOR)
        subtitle_tb.Margin = System.Windows.Thickness(0, 1, 0, 0)
        if clickable:
            self._make_id_link(subtitle_tb, nav_eid or eid)
        texts.Children.Add(title_tb)
        texts.Children.Add(subtitle_tb)

        row.Children.Add(icon_tb)
        row.Children.Add(texts)
        outer.Child = row
        return outer

    def _show_result(self, deleted, skipped):
        self._done = True
        w = self._window

        w.FindName("ReviewPanel").Visibility = System.Windows.Visibility.Collapsed
        w.FindName("ResultPanel").Visibility = System.Windows.Visibility.Visible

        badge = w.FindName("ResultBadge")
        badge_icon = w.FindName("ResultBadgeIcon")
        title_tb = w.FindName("ResultTitle")
        if deleted:
            badge.Background = _color(WM.Color.FromRgb(0xe4, 0xf7, 0xf0))
            badge_icon.Text = "✓"
            badge_icon.Foreground = _color(GREEN_COLOR)
            title_tb.Text = "Duplicates deleted"
        else:
            badge.Background = _color(WM.Color.FromRgb(0xff, 0xf8, 0xec))
            badge_icon.Text = "⚠"
            badge_icon.Foreground = _color(AMBER_COLOR)
            title_tb.Text = "Nothing was deleted"

        summary = "{} item(s) deleted".format(len(deleted))
        if skipped:
            summary += ", {} item(s) skipped".format(len(skipped))
        w.FindName("Res_Summary").Text = summary

        deleted_panel = w.FindName("DeletedListPanel")
        deleted_panel.Children.Clear()
        for i, eid in enumerate(deleted):
            is_last = (i == len(deleted) - 1)
            kept_id = self._kept_for.get(eid.IntegerValue)
            kept_exists = kept_id is not None and doc.GetElement(kept_id) is not None
            extra_text = "kept copy still in place — click Id to view it" if kept_exists else None
            deleted_panel.Children.Add(
                self._make_result_row(eid, "✓", GREEN_COLOR, extra_text, is_last,
                                       clickable=kept_exists, nav_eid=kept_id))
        w.FindName("DeletedHeader").Text = "DELETED ({})".format(len(deleted))

        skipped_header = w.FindName("SkippedHeader")
        skipped_card = w.FindName("SkippedCard")
        if skipped:
            skipped_header.Text = "SKIPPED ({})".format(len(skipped))
            skipped_header.Visibility = System.Windows.Visibility.Visible
            skipped_card.Visibility = System.Windows.Visibility.Visible
            skipped_panel = w.FindName("SkippedListPanel")
            skipped_panel.Children.Clear()
            for i, (eid, reason) in enumerate(skipped):
                is_last = (i == len(skipped) - 1)
                skipped_panel.Children.Add(
                    self._make_result_row(eid, "!", AMBER_COLOR, reason, is_last, clickable=True))
        else:
            skipped_header.Visibility = System.Windows.Visibility.Collapsed
            skipped_card.Visibility = System.Windows.Visibility.Collapsed

        # Deleted elements are gone (their overrides vanish with them); clear
        # the red preview paint from whatever is still around, then mark
        # what's left that still needs manual attention in amber.
        self._clear_review_paint()
        skipped_ids = [eid for eid, _reason in skipped]
        self._set_selection(skipped_ids)
        self._paint(skipped_ids, self._amber_ogs())

        self._go_to_step(2)

    # ── Show ─────────────────────────────────────────────────────────────────

    def show(self):
        from System.Windows.Threading import Dispatcher, DispatcherFrame
        window = self._build()
        frame = DispatcherFrame()

        # Parent the wizard to Revit's main window so it floats above Revit
        # without being forced ahead of everything else (pyRevit's own
        # output window, TaskDialogs, other apps) the way Topmost=True did.
        # UIApplication.MainWindowHandle is the documented Revit API handle
        # for exactly this (an AdWindows.ComponentManager-based Owner
        # assignment failed silently here).
        try:
            WindowInteropHelper(window).Owner = System.IntPtr(revit.uiapp.MainWindowHandle.ToInt64())
        except Exception:
            pass

        def on_closed(s, e):
            frame.Continue = False

        window.Closed += on_closed
        window.Show()
        window.Activate()
        Dispatcher.PushFrame(frame)


def main():
    dlg = DeleteDuplicatesDialog()
    dlg.show()


main()
