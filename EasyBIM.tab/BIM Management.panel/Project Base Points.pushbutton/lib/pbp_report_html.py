# -*- coding: utf-8 -*-
"""Interactive HTML report writer, and a print-ready variant used for the
export wizard's "PDF" choice.

*** SCOPE DECISION (confirmed with the requester) ***
This codebase has no PDF-generation library anywhere (no reportlab/
weasyprint/wkhtmltopdf, and IronPython 2.7 makes adding one painful), so
"PDF" does NOT produce a real .pdf file. It writes a second, print-styled
HTML file and — same as the interactive report — opens it in the user's
default browser; an on-screen banner (hidden when actually printing) tells
them to press Ctrl+P and pick Landscape themselves in the print dialog to
get an actual PDF. This is a deliberate scope simplification, not an
oversight. (An earlier version forced landscape via @page{size:A4 landscape},
which produced a rotated/sideways page in Chrome's print-to-PDF -- letting
the user pick orientation in their own print dialog is the reliable path.)

Both writers consume "resolved" rows — plain dicts already carrying the
CURRENT effective discipline/reference/status/include decisions the user
settled on in the Results screen (see pbp_ui.py's `_resolved_rows()`):
    id, link, inst, kind, disc, key, shared, site, workset,
    ns, ew, el, ang, placed, status, ref_name, included
This module does not re-derive status/matching itself — that stays the sole
responsibility of pbp_status/pbp_match, avoiding a second source of truth.
"""
import codecs

from pbp_disc_map import auto_heb

_TONE = {
    "OK": "#22b07c", "Not OK": "#e0851e", "Not Shared": "#d64545",
    "Missing Ref": "#8b93a7", "Reference": "#44b8d3", "Unloaded": "#8b93a7",
    "Host": "#8b93a7",
}
_BG = {
    "OK": "rgba(34,176,124,0.12)", "Not OK": "rgba(224,133,30,0.15)",
    "Not Shared": "rgba(214,69,69,0.13)", "Missing Ref": "#eef0f6",
    "Reference": "rgba(68,184,211,0.14)", "Unloaded": "#f1f2f7", "Host": "#f1f2f7",
}
NAVY, CYAN = "#1e248c", "#44b8d3"

_HEAD = ["Link", "Disc", "Bldg", "Shared Site", "Workset", "N/S (m)", "E/W (m)",
         "Elev (m)", "Angle", "Reference (AR)", "Check"]


def _fmt(v, nd=3):
    return "—" if v is None else ("{:.%df}" % nd).format(v)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _chart_groups(rows, heb_override=None):
    # Grouped by HEBREW LABEL, not raw code -- HEB_OPTIONS is a small, fixed
    # taxonomy several discipline codes deliberately share (e.g. "ST"
    # Structural and "C" Geotechnical both read "קונסטרוקציה"), so grouping
    # by code produced two bars with identical-looking text.
    heb_override = heb_override or {}
    groups = {}
    order = []
    codes_by_group = {}
    for r in rows:
        if r["kind"] == "HOST" or r["status"] == "Reference" or not r.get("included", True):
            continue
        code = r["disc"] or "?"
        d = heb_override.get(code) or auto_heb(code)
        if d not in groups:
            groups[d] = {"ok": 0, "notok": 0, "unshared": 0, "other": 0, "total": 0}
            order.append(d)
        g = groups[d]
        g["total"] += 1
        st = r["status"]
        if st == "OK":
            g["ok"] += 1
        elif st == "Not OK":
            g["notok"] += 1
        elif st == "Not Shared":
            g["unshared"] += 1
        else:
            g["other"] += 1
        codes_by_group.setdefault(d, set()).add(code)
    order.sort()
    return order, groups, codes_by_group


def _chart_html(rows, heb_override=None):
    order, groups, codes_by_group = _chart_groups(rows, heb_override)
    if not order:
        return ""
    bars = []
    for d in order:
        g = groups[d]
        total = float(g["total"]) or 1.0
        seg = (
            '<i style="width:{0}%;background:{4}"></i>'
            '<i style="width:{1}%;background:{5}"></i>'
            '<i style="width:{2}%;background:{6}"></i>'
            '<i style="width:{3}%;background:#c9cedb"></i>'
        ).format(g["ok"] / total * 100, g["notok"] / total * 100, g["unshared"] / total * 100,
                 g["other"] / total * 100, _TONE["OK"], _TONE["Not OK"], _TONE["Not Shared"])
        codes = u", ".join(sorted(codes_by_group.get(d, [])))
        bars.append(
            '<div class="bar-row"><span class="bar-d" title="{4}">{0}</span>'
            '<span class="bar-track">{1}</span>'
            '<span class="bar-n">{2} / {3}</span></div>'.format(
                _esc(d), seg, g["ok"], g["total"], _esc(codes))
        )
    legend = "".join(
        '<span class="lg"><i style="background:{0}"></i>{1}</span>'.format(c, l)
        for l, c in [("OK", _TONE["OK"]), ("Not OK", _TONE["Not OK"]),
                     ("Not Shared", _TONE["Not Shared"]), ("Missing Ref", "#c9cedb")]
    )
    return (
        '<div class="card chart"><div class="chart-legend">{0}</div>'
        '<div class="chart-bars">{1}</div></div>'
    ).format(legend, "".join(bars))


_AR_HEAD = ["Link", "Shared Site", "Angle (deg)", "Elevation (m)"]


def _ar_table_html(rows):
    """AR reference models table shown next to the chart -- link name,
    shared site, angle to true north, elevation. Sortable by any column
    (link name and elevation are the two the requester specifically asked
    for, but the same sortTable() already used for the main table makes
    every column sortable for free)."""
    ars = [r for r in rows if r["kind"] != "HOST" and r["status"] == "Reference" and r["placed"]]
    if not ars:
        return ""
    head_cells = "".join(
        '<th onclick="sortTable(\'arTable\',{0})">{1}</th>'.format(i, h) for i, h in enumerate(_AR_HEAD)
    )
    body_rows = "".join(
        '<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td></tr>'.format(
            _esc(r["link"]) + (" " + _esc(r["inst"]) if r["inst"] else ""),
            u"Not Shared" if r["shared"] == "No" else _esc(r["site"]),
            u"—" if r["ang"] is None else "{:.4f}".format(r["ang"]),
            _fmt(r["el"]),
        ) for r in ars
    )
    return (
        '<p class="sub" style="margin:0 0 6px;">Architecture reference models</p>'
        '<div class="card" style="overflow:auto;max-height:260px;">'
        '<table id="arTable"><thead><tr>{0}</tr></thead><tbody>{1}</tbody></table>'
        '</div>'
    ).format(head_cells, body_rows)


_REASON_LABEL = {"elev": u"Elev", "angle": u"Angle", "both": u"Both"}


def _row_html(r):
    is_host = r["kind"] == "HOST"
    off = (not is_host) and not r.get("included", True)
    st = r["status"]
    cells = [
        _esc(r["link"]) + (" " + _esc(r["inst"]) if r["inst"] else ""),
        _esc(r["disc"]), _esc(r["key"]),
        "-" if is_host else ("Not Shared" if r["shared"] == "No" else ("—" if r["shared"] == "?" else _esc(r["site"]))),
        _esc(r["workset"]), _fmt(r["ns"]), _fmt(r["ew"]), _fmt(r["el"]),
        "—" if r["ang"] is None else "{:.4f}".format(r["ang"]),
        _esc(r.get("ref_name") or "—"),
    ]
    tds = "".join('<td>{0}</td>'.format(c) for c in cells)
    reason = r.get("reason")
    label = st if not (st == "Not OK" and reason in _REASON_LABEL) else u"{} &middot; {}".format(st, _REASON_LABEL[reason])
    pill = '<td><span class="pill" style="color:{0};background:{1}">{2}</span></td>'.format(
        _TONE.get(st, "#8b93a7"), _BG.get(st, "#f1f2f7"), label)
    row_cls = "host" if is_host else ("bad" if st in ("Not OK", "Not Shared") and not off else "")
    style = ' style="opacity:.45"' if off else ""
    return '<tr class="{0}"{1}>{2}{3}</tr>'.format(row_cls, style, tds, pill)


_CSS = """
:root{--navy:#1e248c;--cyan:#44b8d3;}
*{box-sizing:border-box;}
body{font-family:'Segoe UI',Inter,Arial,sans-serif;background:#f7f8ff;color:#374151;margin:0;padding:24px;}
h1{font-size:19px;color:var(--navy);margin:0 0 2px;}
.sub{font-size:12px;color:#6b7280;margin:0 0 18px;}
.card{background:#fff;border:1px solid #e6e8f5;border-radius:12px;margin-bottom:16px;overflow:hidden;}
.hostcard{display:flex;gap:12px;align-items:center;padding:12px 14px;}
.hostcard b{color:var(--navy);font-family:Consolas,monospace;}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-bottom:16px;}
.stat{background:#fff;border:1px solid #e6e8f5;border-radius:12px;padding:10px 12px;text-align:center;}
.stat b{display:block;font-size:21px;color:var(--navy);}
.stat span{font-size:10.5px;color:#6b7280;}
table{width:100%;border-collapse:collapse;font-size:11.5px;}
th,td{padding:6px 8px;border-bottom:1px solid #eef0f7;text-align:left;white-space:nowrap;}
th{background:#f4f6fd;font-family:Consolas,monospace;font-size:9.5px;text-transform:uppercase;
   letter-spacing:.04em;color:#8b93a7;cursor:pointer;position:sticky;top:0;}
tr.host{background:rgba(30,36,140,0.05);}
tr.bad{background:rgba(224,133,30,0.06);}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700;
      font-family:Consolas,monospace;white-space:nowrap;}
.filters input{width:100%;font-size:10px;padding:3px 4px;border:1px solid #dfe2ee;border-radius:4px;}
.chart-legend{display:flex;gap:14px;font-size:10.5px;color:#9aa0ac;padding:10px 14px 0;}
.chart-legend .lg{display:inline-flex;align-items:center;gap:4px;}
.chart-legend i{width:9px;height:9px;border-radius:3px;display:inline-block;}
.chart-bars{padding:10px 14px 14px;display:flex;flex-direction:column;gap:6px;}
.bar-row{display:flex;align-items:center;gap:10px;font-size:11px;}
.bar-d{width:78px;flex:0 0 auto;font-weight:700;color:var(--navy);direction:rtl;text-align:right;}
.bar-track{flex:0 0 auto;width:220px;max-width:220px;display:flex;height:13px;border-radius:4px;overflow:hidden;background:#eef0f7;}
.bar-n{width:52px;flex:0 0 auto;text-align:right;color:#9aa0ac;font-family:Consolas,monospace;}
footer{font-size:11px;color:#9aa0ac;margin-top:18px;}
.print-instructions{display:flex;align-items:flex-start;gap:10px;padding:12px 16px;margin-bottom:16px;
  border-radius:12px;background:#fff7e6;border:1px solid #f3d9a0;color:#7a5a1a;font-size:13px;line-height:1.5;}
.print-instructions b{color:#5c4310;}
@media print{
  /* Forcing @page{size:landscape} here is what produced a sideways/rotated
     page in Chrome's print-to-PDF -- letting the user pick Landscape in
     their OWN print dialog (see .print-instructions above) is the reliable
     path, so no size/orientation is forced from CSS. */
  @page{margin:12mm;}
  body{background:#fff;padding:0;}
  .filters,.no-print,.print-instructions{display:none !important;}
  table{font-size:9.5px;}
}
"""

_JS = """
function sortTable(tableId, col){
  var table=document.getElementById(tableId);
  var tbody=table.tBodies[0];
  var rows=Array.prototype.slice.call(tbody.rows);
  var dir=table.getAttribute('data-sort-col')==String(col) && table.getAttribute('data-sort-dir')=='1' ? -1 : 1;
  rows.sort(function(a,b){
    var av=a.cells[col].innerText, bv=b.cells[col].innerText;
    var an=parseFloat(av), bn=parseFloat(bv);
    if(!isNaN(an) && !isNaN(bn)) return (an-bn)*dir;
    return av.localeCompare(bv)*dir;
  });
  rows.forEach(function(r){ tbody.appendChild(r); });
  table.setAttribute('data-sort-col', col);
  table.setAttribute('data-sort-dir', dir);
}
function filterTable(){
  var inputs=document.querySelectorAll('.filters input');
  var table=document.getElementById('pbpTable');
  var rows=table.tBodies[0].rows;
  for(var r=0;r<rows.length;r++){
    var show=true;
    for(var c=0;c<inputs.length;c++){
      var v=inputs[c].value.toLowerCase();
      if(!v) continue;
      var cell=rows[r].cells[c];
      if(!cell || cell.innerText.toLowerCase().indexOf(v)<0){ show=false; break; }
    }
    rows[r].style.display = show ? '' : 'none';
  }
}
"""


def _build_html(host_info, rows, tol_mm, tol_deg, interactive, generated_label, show_chart=True, heb_override=None):
    links = [r for r in rows if r["kind"] != "HOST"]
    ars = [r for r in links if r["status"] == "Reference"]
    unloaded = sum(1 for r in links if not r["placed"])
    not_shared = sum(1 for r in links if r["placed"] and r["shared"] == "No")

    stats = (
        '<div class="stats">'
        '<div class="stat"><b>{0}</b><span>Link instances</span></div>'
        '<div class="stat"><b>{1}</b><span>Nested &middot; skipped</span></div>'
        '<div class="stat"><b>{2}</b><span>AR references</span></div>'
        '<div class="stat"><b>{3}</b><span>Not shared</span></div>'
        '<div class="stat"><b>{4}</b><span>Unloaded</span></div>'
        '</div>'
    ).format(len(links), host_info.get("nested", 0), len(ars), not_shared, unloaded)

    # Sort (click header) and per-column filter are useful on-screen
    # regardless of which format this is -- including the print-ready one,
    # since the user typically reviews it before actually printing. The
    # filter row is marked "no-print" so it never shows up in the printed
    # page/PDF itself, only while browsing on screen.
    head_cells = "".join(
        '<th onclick="sortTable(\'pbpTable\',{0})">{1}</th>'.format(i, h) for i, h in enumerate(_HEAD)
    )
    filter_row = "<tr class=\"filters no-print\">" + "".join(
        '<th><input oninput="filterTable()"/></th>' for _ in _HEAD
    ) + "</tr>"

    body_rows = "".join(_row_html(r) for r in rows)

    script_tag = "<script>{0}</script>".format(_JS)

    # The print-ready variant needs the user to pick Landscape themselves --
    # forcing it via @page{size:landscape} is what produced a rotated page
    # in Chrome's print-to-PDF (see the @media print comment above). This
    # banner only shows on screen (hidden via @media print), never in the
    # actual PDF output.
    print_banner = u"" if interactive else (
        u'<div class="print-instructions no-print">'
        u'<span>&#9432;</span><span>Before printing/saving as PDF: press <b>Ctrl+P</b>, then in the print '
        u'dialog set <b>Layout &rarr; Landscape</b> (and Margins &rarr; None or Minimum for the full table to '
        u'fit), then Save. This page intentionally does not force landscape itself &mdash; that produced a '
        u'rotated page in Chrome\'s print-to-PDF.</span></div>'
    )

    return u"""<!doctype html>
<html><head><meta charset="utf-8"/><title>{title} &middot; Project Base Points</title>
<style>{css}</style></head><body>
{print_banner}
<h1>Project Base Points &middot; {title}</h1>
<p class="sub">{path} &middot; generated {gen} &middot; tolerance {tolmm} mm / {toldeg}&deg;</p>
<div class="card hostcard"><b>{title}</b><span>&nbsp;&middot;&nbsp;units: {units}</span></div>
{stats}
{chart}
{ar_table}
<div class="card" style="overflow:auto;max-height:640px;">
<table id="pbpTable"><thead><tr>{head}</tr>{filters}</thead><tbody>{rows}</tbody></table>
</div>
<footer>EasyBIM &middot; Project Base Points audit. Not Shared and Missing Ref rows are Revit/link setup problems, never exported as ACC issues.</footer>
{script}
</body></html>""".format(
        title=_esc(host_info["title"]), path=_esc(host_info.get("path", "")), gen=_esc(generated_label),
        tolmm=tol_mm, toldeg=tol_deg, units=_esc(host_info.get("units", "")), css=_CSS,
        stats=stats, chart=_chart_html(rows, heb_override) if show_chart else u"", ar_table=_ar_table_html(rows),
        head=head_cells, filters=filter_row, rows=body_rows, script=script_tag, print_banner=print_banner,
    )


def write_html(host_info, rows, tol_mm, tol_deg, generated_label, out_path, show_chart=True, heb_override=None):
    html = _build_html(host_info, rows, tol_mm, tol_deg, interactive=True, generated_label=generated_label,
                        show_chart=show_chart, heb_override=heb_override)
    with codecs.open(out_path, "w", "utf-8") as f:
        f.write(html)
    return out_path


def write_pdf(host_info, rows, tol_mm, tol_deg, generated_label, out_path, show_chart=True, heb_override=None):
    """Writes a print-ready static HTML file at `out_path` (a .html path —
    see module docstring on why this isn't a real .pdf) with an on-screen
    banner telling the user to pick Landscape in their own print dialog.
    Deliberately does NOT force @page{size:landscape} -- that produced a
    rotated page in Chrome's print-to-PDF. The caller opens it and the user
    prints/saves as PDF from there."""
    html = _build_html(host_info, rows, tol_mm, tol_deg, interactive=False, generated_label=generated_label,
                        show_chart=show_chart, heb_override=heb_override)
    with codecs.open(out_path, "w", "utf-8") as f:
        f.write(html)
    return out_path
