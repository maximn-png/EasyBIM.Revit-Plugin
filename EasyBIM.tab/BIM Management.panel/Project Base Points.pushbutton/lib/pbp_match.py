# -*- coding: utf-8 -*-
"""Pure logic, no Revit API — direct port of `autoRefs`/`discOf`/`isAR` in the
design handoff's reference/ribbon-base-points.jsx. Keep the building-key
matching rule identical: a non-AR link's reference is the first PLACED AR link
sharing its building `key`; no match -> every link with that key reports
Missing Ref (via pbp_status.status_of, once refs[row_id] is None/absent).
"""
from pbp_disc_map import AR_CODES
from pbp_status import status_of, mismatch_reason


def disc_of(row, disc_override):
    """The discipline currently in effect for a row: the user's hand-edited
    override if one exists, else the auto-detected value from collection."""
    if disc_override and row["id"] in disc_override:
        return disc_override[row["id"]]
    return row["disc"]


def key_of(row, key_override):
    """The building key currently in effect for a row -- same override
    pattern as disc_of, for the user-editable Bldg column."""
    if key_override and row["id"] in key_override:
        return key_override[row["id"]]
    return row["key"]


def is_ar(row, disc_override):
    if row.get("kind") == "HOST":
        return False
    return disc_of(row, disc_override) in AR_CODES


def auto_match_references(rows, disc_override=None, key_override=None):
    """Return {link_id: ar_link_id} for every non-AR, placed Link row — the
    id of the first placed AR link sharing the same building `key`. Rows with
    no AR match for their key are simply absent from the returned dict (the
    caller/status_of treats a missing/blank ref as "Missing Ref").

    Mirrors autoRefs() in reference/ribbon-base-points.jsx:
        arByKey = first placed AR row's id, keyed by building key
        map[row.id] = arByKey.get(row.key) for every non-AR placed Link row
    """
    disc_override = disc_override or {}
    key_override = key_override or {}
    ar_by_key = {}
    for r in rows:
        k = key_of(r, key_override)
        if disc_of(r, disc_override) in AR_CODES and r.get("placed") and k not in ar_by_key:
            ar_by_key[k] = r["id"]

    refs = {}
    for r in rows:
        if r.get("kind") != "Link" or not r.get("placed"):
            continue
        if disc_of(r, disc_override) in AR_CODES:
            continue
        k = key_of(r, key_override)
        if k in ar_by_key:
            refs[r["id"]] = ar_by_key[k]
    return refs


def ar_option_labels(rows, disc_override=None):
    """{row_id: label} for every row eligible to BE a reference (HOST + every
    placed AR row). Normally just the link name -- but two AR models CAN
    share an identical file name while sitting on different shared sites
    (e.g. two firms both naming their model "...-AR-...-Main-R25" on their
    own site); when that happens each of THOSE options gets its shared site
    appended in brackets so they're distinguishable in a dropdown/report."""
    disc_override = disc_override or {}
    ar_rows = [r for r in rows if r.get("placed") and r["kind"] != "HOST" and is_ar(r, disc_override)]
    name_counts = {}
    for r in ar_rows:
        name_counts[r["link"]] = name_counts.get(r["link"], 0) + 1
    labels = {}
    for r in ar_rows:
        label = r["link"]
        if name_counts[r["link"]] > 1:
            label = u"{} ({})".format(label, r.get("site") or u"?")
        labels[r["id"]] = label
    host = next((r for r in rows if r["kind"] == "HOST"), None)
    if host is not None and host.get("placed"):
        labels[host["id"]] = u"HOST: " + host["link"]
    return labels


def resolve_rows(rows, disc_override=None, refs=None, excluded=None, tol_mm=1, tol_deg=0.02,
                  key_override=None):
    """The single place status + matching + user overrides come together.
    Both pbp_ui.py (for the Results grid/chart/footer counts) and the report
    writers (pbp_report_html/pbp_report_xlsx) consume this shape rather than
    re-deriving status themselves — one source of truth. Returns a NEW list
    of dicts, each the original row plus:
        disc        effective discipline (override wins)
        key         effective building key (override wins)
        status      one of pbp_status.STATUSES
        reason      "elev" / "angle" / "both" / None -- which criterion made
                    a "Not OK" row Not OK (elevation and angle only; plan
                    position N/S, E/W is shown but does not drive status)
        ref_row     the matched reference row dict, or None
        ref_name    ref_row["link"] for display, "(baseline)" if this row
                    itself is the AR baseline, else ""
        included    False if the user unticked this row (host is always True)
    """
    disc_override = disc_override or {}
    key_override = key_override or {}
    refs = refs or {}
    excluded = excluded or {}
    by_id = dict((r["id"], r) for r in rows)
    ar_labels = ar_option_labels(rows, disc_override)

    out = []
    for r in rows:
        disc = disc_of(r, disc_override)
        key = key_of(r, key_override)
        ar = is_ar(r, disc_override)
        ref_row = by_id.get(refs.get(r["id"])) if not ar else None
        if ref_row is not None and ref_row["kind"] != "HOST" and not is_ar(ref_row, disc_override):
            # The row this used to point to has since been edited (here or
            # elsewhere) to no longer be an AR reference -- a stale pointer
            # must not keep being trusted for status/delta comparisons, or
            # the tool would silently compare against a model that no
            # longer counts as a coordination baseline at all.
            ref_row = None
        status = status_of(r, ref_row, ar, tol_mm=tol_mm, tol_deg=tol_deg)
        reason = mismatch_reason(r, ref_row, tol_mm=tol_mm, tol_deg=tol_deg) if status == "Not OK" else None
        included = True if r["kind"] == "HOST" else not excluded.get(r["id"], False)
        if ref_row:
            ref_name = ar_labels.get(ref_row["id"], ref_row["link"])
        elif ar and r.get("placed"):
            ref_name = "(baseline)"
        else:
            ref_name = ""
        resolved = dict(r)
        resolved["disc"] = disc
        resolved["key"] = key
        resolved["status"] = status
        resolved["reason"] = reason
        resolved["ref_row"] = ref_row
        resolved["ref_name"] = ref_name
        resolved["included"] = included
        out.append(resolved)
    return out
