# -*- coding: utf-8 -*-
"""Status vocabulary — port directly from `statusOf`/`deltasOf` in
reference/ribbon-base-points.jsx. Do not add/rename statuses; see README
"Status vocabulary" table for the fixed four (OK / Not OK / Not Shared /
Missing Ref) plus Reference/Unloaded/Host.
"""

STATUSES = ["OK", "Not OK", "Not Shared", "Missing Ref", "Reference", "Unloaded", "Host"]
ISSUE_STATUSES = ["Not OK", "Not Shared"]  # only these ever become ACC issues


def ang_diff(a, b):
    return abs(((a - b + 180) % 360 + 360) % 360 - 180)


def deltas_of(row, ref, tol_m, tol_deg):
    """Returns dict(ns, ew, el, ang) deltas vs. `ref`, or None if no ref."""
    raise NotImplementedError


def status_of(row, ref, is_ar, tol_mm=1, tol_deg=0.02):
    """tol_mm -> meters internally. See statusOf() in
    reference/ribbon-base-points.jsx for the exact branch order:
    HOST -> Unloaded -> Not Shared -> Reference -> Missing Ref -> OK/Not OK.
    """
    raise NotImplementedError
