# -*- coding: utf-8 -*-
"""ACC (Forma) issue-import Excel writer. Column spec: ACC_COLS in
reference/ribbon-base-points-export.jsx + README "Export -> ACC issue fields"
and "step 6" of the Revit API mapping. One row per included link whose status
is in ISSUE_STATUSES ("Not OK", "Not Shared") — never "Missing Ref".

Columns, in order, must not be renamed (ACC template is fixed):
Title, Status, Category, Type, Description, Assigned To, Assignee Type,
Location (always blank — see README), Location Details, Due Date, Start Date,
Root Cause Category, Root Cause, then any mapped custom fields appended after.

Description text: pick among the four Hebrew templates configured in the
wizard (elevation / angle / both / not-shared) per README "Descriptions" —
these are user-owned copy, never rewrite them.
"""


def build_issue_rows(rows, acc_config):
    """Mirrors issueRows() in reference/ribbon-base-points-export.jsx."""
    raise NotImplementedError


def write_acc_issue_sheet(rows, acc_config, out_path):
    raise NotImplementedError
