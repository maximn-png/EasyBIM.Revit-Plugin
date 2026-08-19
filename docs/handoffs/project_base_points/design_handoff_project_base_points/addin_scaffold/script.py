# -*- coding: utf-8 -*-
"""Project Base Points — entry point.

Wires together the lib/ modules and shows the dialog (WPF via pyRevit forms, or a
custom XAML window — match whatever the extension's other multi-stage tools use,
e.g. Level Sheets / Head Height Check).

See design_handoff_project_base_points/README.md for the full screen-by-screen spec:
Setup -> Results -> Export format -> ACC issue fields -> Done.
"""
from pyrevit import revit, script  # noqa: F401

from lib import pbp_collect, pbp_match, pbp_status, pbp_report_html, pbp_report_xlsx

# from lib import pbp_ui  # the WPF/XAML dialog controller — not stubbed here;
#                          # build it against reference/ribbon-base-points.jsx +
#                          # reference/ribbon-base-points-export.jsx screen specs.


def main():
    doc = revit.doc
    host = pbp_collect.read_host_base_point(doc)
    links = pbp_collect.read_link_base_points(doc)  # directly-placed only; nested skipped
    rows = pbp_match.auto_match_references(host, links)  # README "Matching a link to its AR reference"
    # rows = pbp_ui.run_setup_and_results(host, rows)  # user edits: refs, discipline, exclusions
    # if rows is None: return  # user cancelled

    # for r in rows: r['status'] = pbp_status.status_of(r, tol_mm=1, tol_deg=0.02)
    # export_choice = pbp_ui.run_export_wizard(rows)
    # if export_choice.html: pbp_report_html.write(rows, export_choice)
    # if export_choice.pdf: pbp_report_html.write_pdf(rows, export_choice)
    # if export_choice.xlsx: pbp_report_xlsx.write_acc_issue_sheet(rows, export_choice)


if __name__ == "__main__":
    main()
