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
the original) and the rest are deleted. Pinned elements and elements that
belong to a Group are skipped rather than force-deleted, since both cases
require the user to make an explicit decision first.
"""

import clr

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
    """Return (groups_in_scope, {IntegerValue: ElementId} to delete)."""
    to_delete = {}
    to_keep = set()
    groups_in_scope = 0

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

    # An element already kept as "the original" for one group must never be
    # deleted, even if it also shows up as a duplicate in another group.
    for kept_id in to_keep:
        to_delete.pop(kept_id, None)

    return groups_in_scope, to_delete


def delete_elements(to_delete):
    """Delete each candidate element inside a single transaction, skipping
    elements that can't be safely deleted (pinned / grouped) and recording
    anything Revit itself refuses."""
    deleted, skipped = [], []

    with revit.Transaction("Delete Duplicate Elements"):
        for eid in to_delete.values():
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
    output.print_md("**Elements deleted:** {}".format(len(deleted)))
    output.print_md("**Elements skipped:** {}".format(len(skipped)))

    if deleted:
        output.print_md("### Deleted Element IDs")
        for eid in deleted:
            output.print_md("- {}".format(output.linkify(eid)))

    if skipped:
        output.print_md("### Skipped Elements")
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
    groups_in_scope, to_delete = find_duplicates_to_delete(scope_ids)

    if not to_delete:
        forms.alert(
            "No duplicate elements were found in this scope ({}).".format(scope),
            title="Delete Duplicates",
        )
        return

    confirmed = forms.alert(
        "Found {} duplicate group(s), {} element(s) to delete.\n\nProceed?".format(
            groups_in_scope, len(to_delete)
        ),
        title="Delete Duplicates",
        yes=True,
        no=True,
    )
    if not confirmed:
        return

    deleted, skipped = delete_elements(to_delete)
    print_report(scope, groups_in_scope, deleted, skipped)

    summary = "Deleted {} duplicate element(s).".format(len(deleted))
    if skipped:
        summary += "\n{} element(s) were skipped — see the report window.".format(len(skipped))
    forms.alert(summary, title="Delete Duplicates")


main()
