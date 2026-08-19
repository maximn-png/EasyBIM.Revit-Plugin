# -*- coding: utf-8 -*-
"""Project Base Points — EasyBIM BIM Management.

Audits the host model's and every directly-placed link's Project Base Point
against Architecture reference models; flags coordinate/angle mismatches and
links not in shared coordinates; exports HTML / a print-ready HTML (see
lib/pbp_report_html.py for why "PDF" isn't a real .pdf yet) / an ACC-ready
issue Excel sheet. Full spec:
docs/handoffs/project_base_points/design_handoff_project_base_points/README.md

Read-only against the model (no Transaction — this tool never edits the
Revit document, only reads link placements and writes report files).
"""
__title__ = "Project Base\nPoints"
__author__ = "EasyBIM"
__doc__ = "Audit host & link Project Base Points against the Architecture reference model."

import traceback

from pyrevit import revit, script, forms

from lib import pbp_collect
from lib import pbp_ui

logger = script.get_logger()


def main():
    doc = revit.doc
    try:
        host_info, rows = pbp_collect.read_all(doc)
    except Exception:
        logger.error(traceback.format_exc())
        forms.alert(
            u"Could not read Project Base Points from this model:\n\n{}".format(traceback.format_exc()),
            title=u"Project Base Points", exitscript=True)
        return

    dialog = pbp_ui.ProjectBasePointsDialog(doc, host_info, rows)
    dialog.show()


if __name__ == "__main__":
    main()
