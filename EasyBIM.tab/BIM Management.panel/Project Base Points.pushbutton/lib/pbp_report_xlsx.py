# -*- coding: utf-8 -*-
"""ACC (Autodesk Construction Cloud / Forma) issue-import Excel writer.
Column spec: ACC_COLS in the design handoff's
reference/ribbon-base-points-export.jsx + README "Export -> ACC issue fields"
and Revit-API-mapping step 6. One row per included link whose status is in
ISSUE_STATUSES ("Not OK", "Not Shared") — never "Missing Ref" (no Architecture
reference is a Revit/link setup problem to fix, not an issue to raise).

Uses xlsxwriter — the same library Check Levels already depends on elsewhere
in this codebase (no openpyxl/COM anywhere in this repo).

Columns, in order, must not be renamed (ACC's template is fixed):
Title, Status, Category, Type, Description, Assigned To, Assignee Type,
Location (always blank — resolves against ACC's own Locations breakdown
structure, separate admin setup), Location Details, Due Date, Start Date,
Root Cause Category, Root Cause, then any mapped custom fields appended.

Description text: one of four Hebrew templates configured in the wizard
(elevation / angle / both / not-shared) — README is explicit these are
user-owned, final copy and must never be silently rewritten by this module.
"""
import datetime

from pbp_disc_map import auto_role, auto_heb, guess_role
from pbp_status import ISSUE_STATUSES, deltas_of, mismatch_reason
from pbp_match import ar_option_labels

ACC_COLS = ["Title", "Status", "Category", "Type", "Description", "Assigned To",
            "Assignee Type", "Location", "Location Details", "Due Date", "Start Date",
            "Root Cause Category", "Root Cause"]


def _iso(d):
    return d.strftime("%Y-%m-%d")


def build_issue_rows(rows, acc_config, tol_mm, tol_deg, today=None):
    """Mirrors issueRows() in reference/ribbon-base-points-export.jsx.

    `rows` are RESOLVED rows (see pbp_report_html.py docstring) that also
    carry `ref_row` — the matched reference row dict, or None. Returns a list
    of plain dicts, one per flagged+included link, BEFORE any per-row hand
    edits from the Preview grid are applied (see apply_row_edits below).
    """
    a = acc_config
    today = today or datetime.date.today()
    try:
        due_days = int(a.get("dueDays") or 7)
    except (TypeError, ValueError):
        due_days = 7
    due = _iso(today + datetime.timedelta(days=due_days))
    start = _iso(today)
    # `rows` are already RESOLVED (their "disc" is already the effective,
    # override-applied value), so no disc_override needs passing here.
    ar_labels = ar_option_labels(rows)

    out = []
    for r in rows:
        if r["kind"] == "HOST" or not r.get("included", True):
            continue
        if r["status"] not in ISSUE_STATUSES:
            continue

        disc = r["disc"]
        ref = r.get("ref_row")
        d = deltas_of(r, ref) if ref else None

        # "reason" (elev/angle/both) is decided once, in pbp_status/pbp_match
        # (elevation and angle only -- plan position N/S/E/W no longer drives
        # status, per explicit direction) -- reuse it rather than re-deriving
        # a second time here.
        reason = "shared" if r["status"] == "Not Shared" else (r.get("reason") or mismatch_reason(r, ref, tol_mm, tol_deg) or "elev")

        # Actual value in the checked model vs. the value the reference
        # model says it should be -- more actionable in Revit than a bare
        # delta ("you're off by 1.2m" vs. "yours is 3.9m, should be 5.1m").
        mismatches = []
        if r["status"] == "Not OK" and d and ref:
            if reason in ("elev", "both"):
                mismatches.append(u"מפלס נקודת הבסיס במודל הנבדק: {:.3f} מ׳ (לפי המודל האדריכלי: {:.3f} מ׳)".format(
                    r["el"], ref["el"]))
            if reason in ("angle", "both"):
                mismatches.append(u"זווית לצפון במודל הנבדק: {:.4f}° (לפי המודל האדריכלי: {:.4f}°)".format(
                    r["ang"], ref["ang"]))

        base = a["descShared"] if r["status"] == "Not Shared" else {
            "both": a["descBoth"], "angle": a["descAngle"], "elev": a["descElev"]
        }[reason]
        if r["status"] == "Not Shared":
            desc = base
        else:
            desc = base
            if mismatches:
                desc += u" " + u" ".join(mismatches)
                if not desc.endswith(u"."):
                    desc += u"."
            if ref:
                desc += u" יש לעדכן בהתאם למודל {}.".format(ar_labels.get(ref["id"], ref["link"]))

        role_list = [x.strip() for x in (a.get("rolesText") or "").split(",") if x.strip()]
        role = a.get("roles", {}).get(disc, guess_role(disc, role_list))
        assigned = a.get("companies", {}).get(disc, "") if a.get("assigneeType") == "company" else role

        # Which building/zone this issue is actually about, straight from
        # the (already user-corrected) Bldg key -- so a batch of issues
        # doesn't read as N identical "QA - Project Base Point" titles.
        base_title = a["titleShared"] if r["status"] == "Not Shared" else a["title"]
        bldg = (r.get("key") or "").strip()
        title = u"{} - {}".format(base_title, bldg) if bldg and bldg != "?" else base_title

        out.append({
            "id": r["id"], "link": r["link"], "inst": r["inst"], "status": r["status"],
            "disc": disc, "mismatches": mismatches, "reason": reason,
            "Title": title,
            "Status": a["status"], "Category": a["category"], "Type": a["type"],
            "Description": desc,
            "Assigned To": assigned, "Assignee Type": a.get("assigneeType", "role"),
            "Location": "",
            "Location Details": r["link"] + (" " + r["inst"] if r["inst"] else "") +
                                 (u" ↔ " + ar_labels.get(ref["id"], ref["link"]) if ref else ""),
            "Due Date": due, "Start Date": start,
            "Root Cause Category": "", "Root Cause": "",
            "discipline": a.get("heb", {}).get(disc, auto_heb(disc)),
            "building": r.get("key", ""),
            "extra": dict((a.get("extra") or {}).get(r["id"], {})),
        })
    return out


def apply_row_edits(issue_rows, acc_config):
    """Hand-edits made in the Preview grid win over the computed value — same
    precedence as the JS `.map(x => Object.assign(x, rowEdit[x.id] || {}))`.
    Returns a NEW list; does not mutate `issue_rows`."""
    row_edit = acc_config.get("rowEdit") or {}
    result = []
    for x in issue_rows:
        merged = dict(x)
        merged.update(row_edit.get(x["id"], {}) or {})
        result.append(merged)
    return result


def write_acc_issue_sheet(issue_rows, custom_fields, out_path):
    """`issue_rows` should already have apply_row_edits() applied.
    `custom_fields` is an ordered list of (field_name, role) pairs to append
    after ACC_COLS, where role is "discipline" / "building" / None (free —
    filled per-row from acc_config.extra, i.e. x["extra"][field_name]).
    Empty if the wizard's custom-fields mode is "none"."""
    import xlsxwriter

    wb = xlsxwriter.Workbook(out_path, {"strings_to_numbers": False})
    ws = wb.add_worksheet("Issues")

    hdr_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1e248c",
                              "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True})
    cell_fmt = wb.add_format({"border": 1, "valign": "top", "text_wrap": True})
    rtl_fmt = wb.add_format({"border": 1, "valign": "top", "text_wrap": True, "reading_order": 2})

    field_names = [f for f, _role in custom_fields]
    columns = list(ACC_COLS) + field_names
    for c, name in enumerate(columns):
        ws.write(0, c, name, hdr_fmt)
    ws.freeze_panes(1, 0)

    for row_i, x in enumerate(issue_rows, start=1):
        for c, name in enumerate(ACC_COLS):
            fmt = rtl_fmt if name == "Description" else cell_fmt
            ws.write(row_i, c, x.get(name, ""), fmt)
        extra = x.get("extra") or {}
        for j, (field, role) in enumerate(custom_fields):
            if role == "discipline":
                val, fmt = x.get("discipline", ""), rtl_fmt
            elif role == "building":
                val, fmt = x.get("building", ""), cell_fmt
            else:
                val, fmt = extra.get(field, ""), cell_fmt
            ws.write(row_i, len(ACC_COLS) + j, val, fmt)

    widths = {"Title": 26, "Description": 46, "Assigned To": 20, "Location Details": 30}
    for c, name in enumerate(columns):
        ws.set_column(c, c, widths.get(name, 16))

    wb.close()
    return out_path
