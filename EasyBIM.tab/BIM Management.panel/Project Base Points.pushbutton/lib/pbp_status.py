# -*- coding: utf-8 -*-
"""Status vocabulary — pure logic, no Revit API. Direct port of
`statusOf`/`deltasOf`/`angDiff` in the design handoff's
reference/ribbon-base-points.jsx. Do not add/rename statuses — see README
"Status vocabulary" table for the fixed four (OK / Not OK / Not Shared /
Missing Ref) plus the non-audit states Reference / Unloaded / Host.

Row shape expected throughout (matches PBP_ROWS in reference/ribbon-data.jsx):
    id, link, inst, kind ("HOST" | "Link"), disc, key, shared ("Yes"|"No"|"?"),
    site, workset, ns, ew, el, ang, placed (bool)
"""

STATUSES = ["OK", "Not OK", "Not Shared", "Missing Ref", "Reference", "Unloaded", "Host"]
ISSUE_STATUSES = ["Not OK", "Not Shared"]  # only these ever become ACC issues


def ang_diff(a, b):
    """Shortest-path angle difference in degrees, wraps at +/-180."""
    return abs(((a - b + 180.0) % 360.0 + 360.0) % 360.0 - 180.0)


def deltas_of(row, ref):
    """dict(ns, ew, el, ang, ref) deltas of `row` vs. `ref`, or None if either
    side is missing coordinates (e.g. an unloaded link)."""
    if ref is None:
        return None
    if row.get("ns") is None or ref.get("ns") is None:
        return None
    return {
        "ns": row["ns"] - ref["ns"],
        "ew": row["ew"] - ref["ew"],
        "el": row["el"] - ref["el"],
        "ang": ang_diff(row["ang"], ref["ang"]),
        "ref": ref,
    }


def _tolerances(tol_mm, tol_deg):
    # An explicit 0 is a legitimate "exact match required" tolerance -- only
    # missing/unparseable values fall back to the defaults.
    try:
        tol_m = float(tol_mm) / 1000.0
    except (TypeError, ValueError):
        tol_m = 1.0 / 1000.0
    try:
        tol_deg_val = float(tol_deg)
    except (TypeError, ValueError):
        tol_deg_val = 0.02
    return tol_m, tol_deg_val


def status_of(row, ref, is_ar, tol_mm=1, tol_deg=0.02):
    """Branch order mirrors statusOf() in reference/ribbon-base-points.jsx
    exactly: HOST -> Unloaded -> Not Shared -> Reference -> Missing Ref ->
    OK/Not OK. `ref` is the row dict this row is currently matched against
    (or None); `is_ar` is whether `row` itself is an Architecture reference.

    OK/Not OK is driven by ELEVATION AND ANGLE ONLY (explicit direction from
    the requester, overriding the design handoff's original "any of N/S,
    E/W, elevation, or angle" rule) -- plan position (N/S, E/W) is still
    shown in the results table for reference but no longer affects status.
    """
    if row.get("kind") == "HOST":
        return "Host"
    if not row.get("placed"):
        return "Unloaded"
    if row.get("shared") == "No":
        return "Not Shared"
    if is_ar:
        return "Reference"
    if ref is None:
        return "Missing Ref"
    d = deltas_of(row, ref)
    if d is None:
        return "Missing Ref"
    tol_m, tol_deg_val = _tolerances(tol_mm, tol_deg)
    near = abs(d["el"]) <= tol_m and d["ang"] <= tol_deg_val
    return "OK" if near else "Not OK"


def mismatch_reason(row, ref, tol_mm=1, tol_deg=0.02):
    """Which criterion pushed this row past tolerance -- "elev" / "angle" /
    "both" / None. None if there's nothing to compare (no ref, or already
    within tolerance). Elevation and angle only, matching status_of above."""
    if ref is None:
        return None
    d = deltas_of(row, ref)
    if d is None:
        return None
    tol_m, tol_deg_val = _tolerances(tol_mm, tol_deg)
    bad_el = abs(d["el"]) > tol_m
    bad_ang = d["ang"] > tol_deg_val
    if bad_el and bad_ang:
        return "both"
    if bad_el:
        return "elev"
    if bad_ang:
        return "angle"
    return None
