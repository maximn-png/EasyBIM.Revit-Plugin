# -*- coding: utf-8 -*-
"""
Delete Duplicate Elements

Finds elements that Revit itself already flags with the built-in warning
"There are identical instances in the same place" and deletes all but one
copy of each group. This is the same check that produces the yellow warning
dialog, so it is language- and category-independent — it works for whatever
Revit itself considers a duplicate (furniture, fixtures, framing, pipes,
ducts, rooms, etc.), not just elements that merely look alike.

Scope can be limited to:
  - Current Selection — only duplicate groups fully contained in the
    selected elements
  - Active View       — only duplicate groups fully contained in the
    elements of the active view
  - Entire Model       — every duplicate group in the project

For each group, the element with the lowest Element Id is kept (treated as
the original) and the rest are deleted. Before anything is deleted, the
candidate list is shown in a review screen so items can be unchecked.

Group instances get special treatment: if every member of a Model Group
instance is itself flagged as a duplicate of a member in one specific other
Group instance, the whole duplicate Group instance is deleted as a unit
(rather than leaving orphaned members behind). Elements that belong to a
Group but are only *partially* duplicated are skipped, since resolving that
requires the user to edit the group directly. Pinned elements are skipped
too.
"""

import clr
from collections import defaultdict

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit import DB
from pyrevit import revit, script, forms

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

SCOPE_SELECTION = "Current Selection"
SCOPE_VIEW = "Active View"
SCOPE_MODEL = "Entire Model"


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


def get_scope_ids(scope):
    """IntegerValue set of elements allowed for the chosen scope, or None
    for Entire Model (no filtering needed)."""
    if scope == SCOPE_SELECTION:
        ids = list(uidoc.Selection.GetElementIds())
        if not ids:
            forms.alert(
                "Nothing is selected. Select the elements you want to check "
                "first, or pick a different scope.",
                title="Delete Duplicates",
                exitscript=True,
            )
        return set(eid.IntegerValue for eid in ids)
    elif scope == SCOPE_VIEW:
        view_ids = DB.FilteredElementCollector(doc, revit.active_view.Id) \
            .WhereElementIsNotElementType() \
            .ToElementIds()
        return set(eid.IntegerValue for eid in view_ids)
    return None  # Entire Model


def find_duplicates_to_delete(scope_ids):
    """Scan Revit's own duplicate-instance warnings and sort the failing
    elements into what can be safely deleted.

    Returns:
        groups_in_scope  - number of duplicate-instance warnings processed
        element_deletes  - {IntegerValue: ElementId} standalone elements
        group_deletes    - {IntegerValue: ElementId} whole Group instances
                            whose every member duplicates another group
        skipped_grouped  - [(ElementId, reason)] grouped elements that are
                            only *partially* duplicated and can't be safely
                            auto-resolved
    """
    to_delete = {}
    to_keep = set()
    groups_in_scope = 0

    # dup group IntegerValue -> set of that group's member IntegerValues
    # seen as duplicates
    group_dup_members = defaultdict(set)

    for msg in doc.GetWarnings():
        if not is_duplicate_warning(msg):
            continue

        failing_ids = list(msg.GetFailingElements())
        if len(failing_ids) < 2:
            continue
        if scope_ids is not None and not all(eid.IntegerValue in scope_ids for eid in failing_ids):
            continue  # part of this duplicate group falls outside the chosen scope

        ordered = sorted(failing_ids, key=lambda eid: eid.IntegerValue)
        keep, dupes = ordered[0], ordered[1:]

        groups_in_scope += 1
        to_keep.add(keep.IntegerValue)
        for eid in dupes:
            to_delete[eid.IntegerValue] = eid
            dup_elem = doc.GetElement(eid)
            if dup_elem is not None and dup_elem.GroupId != DB.ElementId.InvalidElementId:
                group_dup_members[dup_elem.GroupId.IntegerValue].add(eid.IntegerValue)

    # An element already kept as "the original" for one group must never be
    # deleted, even if it also shows up as a duplicate in another group.
    for kept_id in to_keep:
        to_delete.pop(kept_id, None)

    # Promote a Group instance to a whole-group deletion when every one of
    # its members was independently flagged as a duplicate — that means the
    # entire group instance is a copy of another group sitting in the same
    # place, so it can be deleted as a unit instead of leaving it hollowed
    # out with dangling members.
    group_deletes = {}
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

    return groups_in_scope, to_delete, group_deletes, skipped_grouped


class PreviewItem(object):
    """One row in the pre-deletion review list. str(item) is what the user
    sees; is_group tells main() which bucket to put it back into once the
    user confirms their selection."""

    def __init__(self, eid, label, is_group=False):
        self.eid = eid
        self.label = label
        self.is_group = is_group

    def __str__(self):
        return self.label


def describe_element(eid):
    elem = doc.GetElement(eid)
    cat_name = elem.Category.Name if elem and elem.Category else "Unknown category"
    return "{}  —  Id {}".format(cat_name, eid.IntegerValue)


def describe_group(eid):
    group_elem = doc.GetElement(eid)
    member_count = len(list(group_elem.GetMemberIds()))
    type_id = group_elem.GetTypeId()
    type_name = doc.GetElement(type_id).Name if type_id != DB.ElementId.InvalidElementId else "Group"
    return "Group '{}'  —  Id {} ({} member{})".format(
        type_name, eid.IntegerValue, member_count, "" if member_count == 1 else "s"
    )


def build_preview_items(element_deletes, group_deletes):
    items = [PreviewItem(eid, describe_group(eid), is_group=True) for eid in group_deletes.values()]
    items += [PreviewItem(eid, describe_element(eid), is_group=False) for eid in element_deletes.values()]
    return items


def review_candidates(element_deletes, group_deletes):
    """Show the pending deletions and let the user uncheck anything before
    committing. Returns (selected_element_ids, selected_group_ids), both
    empty if the user cancelled."""
    preview_items = build_preview_items(element_deletes, group_deletes)
    checked_items = [forms.TemplateListItem(pi, checked=True) for pi in preview_items]

    selected = forms.SelectFromList.show(
        checked_items,
        title="Delete Duplicates — Review Before Deleting",
        multiselect=True,
        button_name="Delete Selected",
    )
    if not selected:
        return [], []

    selected_element_ids = [pi.eid for pi in selected if not pi.is_group]
    selected_group_ids = [pi.eid for pi in selected if pi.is_group]
    return selected_element_ids, selected_group_ids


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


def print_report(scope, groups_in_scope, deleted, skipped):
    output.print_md("# Delete Duplicate Elements — Report")
    output.print_md("**Scope:** {}".format(scope))
    output.print_md("**Duplicate groups processed:** {}".format(groups_in_scope))
    output.print_md("**Elements/groups deleted:** {}".format(len(deleted)))
    output.print_md("**Elements/groups skipped:** {}".format(len(skipped)))

    if deleted:
        output.print_md("### Deleted")
        for eid in deleted:
            output.print_md("- {}".format(output.linkify(eid)))

    if skipped:
        output.print_md("### Skipped")
        for eid, reason in skipped:
            output.print_md("- {} — {}".format(output.linkify(eid), reason))


def main():
    scope = forms.CommandSwitchWindow.show(
        [SCOPE_SELECTION, SCOPE_VIEW, SCOPE_MODEL],
        message="Delete duplicates in which scope?",
    )
    if not scope:
        return  # user cancelled

    scope_ids = get_scope_ids(scope)
    groups_in_scope, element_deletes, group_deletes, skipped_grouped = find_duplicates_to_delete(scope_ids)

    if not element_deletes and not group_deletes:
        forms.alert(
            "No duplicate elements were found in this scope ({}).".format(scope),
            title="Delete Duplicates",
        )
        return

    selected_element_ids, selected_group_ids = review_candidates(element_deletes, group_deletes)
    if not selected_element_ids and not selected_group_ids:
        return  # user cancelled the review or unchecked everything

    deleted, skipped = delete_elements(selected_element_ids, selected_group_ids)
    skipped = skipped + skipped_grouped

    print_report(scope, groups_in_scope, deleted, skipped)

    summary = "Deleted {} duplicate element(s)/group(s).".format(len(deleted))
    if skipped:
        summary += "\n{} item(s) were skipped — see the report window.".format(len(skipped))
    forms.alert(summary, title="Delete Duplicates")


main()
