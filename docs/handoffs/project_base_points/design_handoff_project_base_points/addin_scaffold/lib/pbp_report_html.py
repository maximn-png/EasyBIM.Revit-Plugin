# -*- coding: utf-8 -*-
"""Interactive HTML + PDF report writer. Structure/copy spec: README "Export ->
format choice" + the Results screen in reference/ribbon-base-points.jsx
(coordination chart, filterable/sortable table, status pills, tokens in
README "Design tokens"). PDF is an A4-landscape snapshot of the same content.
"""


def write_html(rows, options, out_path):
    raise NotImplementedError


def write_pdf(rows, options, out_path):
    raise NotImplementedError
