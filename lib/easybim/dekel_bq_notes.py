#! python3
# -*- coding: UTF-8 -*-
"""Shared "הנחיות כתב כמויות" drafting-view builder for the Dekel BQ tools.

Every Dekel tool (Power, Cable Trays, ...) calls create_bq_drafting_view(doc)
at the end of its run. It always rebuilds the SAME view (BQ_VIEW_NAME) from
the SAME BQ_NOTES list below, so no matter which tool ran last, the view
shows every category's notes — not just the notes from the tool that ran.

Add new engineer-facing notes here only. Do not fork this list per-tool —
that is what caused the previous per-tool views to overwrite each other.
"""

BQ_VIEW_NAME = u"הנחיות כתב כמויות"
BQ_TEXT_TYPE = u"1.80mm Arial"    # ← שנה לשם TextNoteType הקיים במודל
BQ_BOLD_TYPE = u"3mm Arial Bold"  # ← שנה לשם TextNoteType Bold הקיים במודל

BQ_NOTES = [
    (
        u"שנאים",
        u"יש לשנות בתיאור השנאים את המתח בהתאם לדרישה בפרויקט.\n"
        u"לדוגמה: KV22/04 → KV33/04 (או כל מתח אחר שנדרש ע\"פ תנאי הרשת).",
    ),
    (
        u"תעלות חסינות אש",
        u"אין בדקל סעיף לתעלת כבלים חסינת אש — המחיר הוא אמדן מחושב, לא סעיף רשמי.\n"
        u"בסיס: תעלת פח דקל במידת התעלה בפועל (Width/Height) — ראה הערת \"מידות תעלה\".\n"
        u"מקדם x3.0 — אקסטרפולציה מיחס ~2.95x בדקל בין רשת ברזל לרשת פלב\"מ באותם "
        u"רוחבים (הפרוקסי הקרוב ביותר שנמצא בקטלוג לתוספת חומר/הגנה).\n"
        u"לדוגמה: תעלת 300 מ\"מ → ₪180 (08.023.0040) × 3.0 ≈ ₪540 למ\"א.\n"
        u"קוד \"אמדן-חסין אש\" בשדה מספר סעיף דקל מסמן שזה לא סעיף רשמי.\n"
        u"יש לעדכן FIRE_RESISTANT_FACTOR בקוד כשתתקבל הצעת מחיר ממתקין מיגון אש בפועל.",
    ),
    (
        u"מידות תעלה",
        u"התאמת התעלה לסעיף דקל נעשית לפי חומר (Description) וגם לפי הרוחב/העומק בפועל "
        u"(Width/Height) — לא רק לפי חומר כמו בעבר. הכלי בוחר בכל פעם את הסעיף הקרוב "
        u"ביותר מבין המידות הקיימות בדקל לאותו חומר (טבלת TRAY_CATALOG ב-script.py).\n"
        u"אם המידה בפועל לא קיימת בדיוק בדקל — נבחרת המידה הקרובה ביותר, לא ביניים "
        u"מחושב (אין אינטרפולציה).\n"
        u"אם לתעלה אין פרמטר Width/Height תקין — חוזרים לקוד קבוע כברירת מחדל (כמו "
        u"שהיה קודם). בסוף הריצה מודפס לקונסול כמה תעלות עודכנו במידה מדויקת, כמה "
        u"בקירוב וכמה בברירת מחדל.",
    ),
    # (u"קטגוריה", u"הערה..."),
]

_MM         = 1.0 / 304.8
_BQ_CAT_W   = 70  * _MM   # עמודת קטגוריה
_BQ_NOTE_W  = 210 * _MM   # עמודת הערה
_BQ_HDR_H   = 14  * _MM   # גובה שורת כותרות עמודות
_BQ_ROW_H   = 42  * _MM   # גובה שורת נתון בסיסי
_BQ_LINE_H  = 12  * _MM   # גובה שורת טקסט נוספת לכל \n
_BQ_TTL_H   = 16  * _MM   # גובה כותרת ראשית מרחפת
_BQ_PAD_X   =  6  * _MM   # ריפוד אופקי
_BQ_PAD_Y   =  4  * _MM   # ריפוד אנכי


def _tnt_name(elem):
    from Autodesk.Revit.DB import BuiltInParameter
    try:
        p = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            v = p.AsString()
            if v: return v
    except Exception: pass
    try: return elem.Name or u""
    except Exception: return u""


def _resolve_types(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, TextNoteType
    all_types = list(FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
    reg_id = bold_id = None
    for tnt in all_types:
        n = _tnt_name(tnt)
        if n == BQ_TEXT_TYPE:  reg_id  = tnt.Id
        if n == BQ_BOLD_TYPE:  bold_id = tnt.Id
    if reg_id is None:
        print(u"[!] TextNoteType '{}' לא נמצא — fallback".format(BQ_TEXT_TYPE))
        reg_id = all_types[0].Id if all_types else None
    if bold_id is None:
        print(u"[!] TextNoteType '{}' לא נמצא — ישמש הרגיל".format(BQ_BOLD_TYPE))
        bold_id = reg_id
    return reg_id, bold_id


def _del_view(doc, name):
    from Autodesk.Revit.DB import FilteredElementCollector, ViewDrafting
    for v in FilteredElementCollector(doc).OfClass(ViewDrafting).ToElements():
        try:
            if v.Name == name: doc.Delete(v.Id); return
        except Exception: pass


def _ln(doc, view, x1, y1, x2, y2, gstyle=None):
    from Autodesk.Revit.DB import Line, XYZ
    p1 = XYZ(x1, y1, 0); p2 = XYZ(x2, y2, 0)
    if p1.DistanceTo(p2) < 1e-9: return
    dc = doc.Create.NewDetailCurve(view, Line.CreateBound(p1, p2))
    if gstyle:
        try: dc.LineStyle = gstyle
        except Exception: pass


def _txt(doc, view, tid, x, y, w, text):
    """
    Place a right-aligned Hebrew TextNote.
    Revit positions Right-aligned TextNotes with the XYZ coord at the
    UPPER-RIGHT corner of the bounding box (not upper-left).
    Callers must therefore pass the RIGHT edge of the text area as x.
    """
    from Autodesk.Revit.DB import TextNote, TextNoteOptions, HorizontalTextAlignment, XYZ
    if not text:
        return
    safe_w = max(w, 10 * _MM)
    opts = TextNoteOptions(tid)
    opts.HorizontalAlignment = HorizontalTextAlignment.Right
    try:
        tn = TextNote.Create(doc, view.Id, XYZ(x, y, 0), safe_w, text, opts)
    except Exception:
        tn = TextNote.Create(doc, view.Id, XYZ(x, y, 0), text, opts)
    try:
        tn.Width = safe_w
    except Exception:
        pass


def _resolve_gstyle(doc, keywords):
    """Return a GraphicsStyle for the Lines sub-category matching any keyword (case-insensitive)."""
    from Autodesk.Revit.DB import BuiltInCategory, GraphicsStyleType
    try:
        cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
        for sc in cat.SubCategories:
            n = sc.Name.lower()
            if any(k.lower() in n for k in keywords):
                return sc.GetGraphicsStyle(GraphicsStyleType.Projection)
    except Exception:
        pass
    return None


def create_bq_drafting_view(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, ViewDrafting, ViewFamilyType, ViewFamily
    _del_view(doc, BQ_VIEW_NAME)

    vft = None
    for v in FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements():
        if v.ViewFamily == ViewFamily.Drafting: vft = v; break
    if not vft:
        print(u"[!] לא נמצא ViewFamilyType מסוג Drafting"); return None

    view = ViewDrafting.Create(doc, vft.Id)
    view.Name  = BQ_VIEW_NAME
    view.Scale = 1

    tnt_id, bold_id = _resolve_types(doc)
    if tnt_id is None:
        print(u"[!] אין TextNoteTypes — מבט לא יכיל טקסט"); return view

    border_gs = _resolve_gstyle(doc, [u"wide", u"Wide", u"bold", u"Bold",
                                       u"heavy", u"Heavy", u"thick", u"Thick"])

    tw = _BQ_CAT_W + _BQ_NOTE_W

    _txt(doc, view, bold_id,
         tw - _BQ_PAD_X, _BQ_TTL_H - _BQ_PAD_Y,
         tw - _BQ_PAD_X * 2,
         u"הנחיות לכתב "
         u"כמויות — הערות "
         u"למהנדס")

    cy = 0.0

    ch = cy - _BQ_HDR_H
    _ln(doc, view, 0,          cy, tw,          cy,  border_gs)
    _ln(doc, view, 0,          ch, tw,          ch,  border_gs)
    _ln(doc, view, 0,          cy, 0,           ch,  border_gs)
    _ln(doc, view, tw,         cy, tw,          ch,  border_gs)
    _ln(doc, view, _BQ_CAT_W,  cy, _BQ_CAT_W,   ch,  None)
    _txt(doc, view, bold_id,
         _BQ_CAT_W - _BQ_PAD_X, cy - _BQ_PAD_Y,
         _BQ_CAT_W - _BQ_PAD_X * 2,
         u"קטגוריה")
    _txt(doc, view, bold_id,
         tw - _BQ_PAD_X, cy - _BQ_PAD_Y,
         _BQ_NOTE_W - _BQ_PAD_X * 2,
         u"הערה")
    cy = ch

    note_inner_mm  = (_BQ_NOTE_W - _BQ_PAD_X * 2) * 304.8
    chars_per_line = max(1, int(note_inner_mm / 3.0))

    for idx, (cat, note) in enumerate(BQ_NOTES):
        is_last     = (idx == len(BQ_NOTES) - 1)
        explicit_nl = note.count(u"\n")
        parts       = note.split(u"\n") if note else [u""]
        max_len     = max(len(s) for s in parts)
        wrap_extra  = max(0, max_len // chars_per_line)
        rh  = _BQ_ROW_H + (explicit_nl + wrap_extra) * _BQ_LINE_H
        bot = cy - rh

        _ln(doc, view, 0,         cy, 0,          bot, border_gs)
        _ln(doc, view, tw,        cy, tw,         bot, border_gs)
        _ln(doc, view, _BQ_CAT_W, cy, _BQ_CAT_W,  bot, None)
        _ln(doc, view, 0, bot, tw, bot, border_gs if is_last else None)

        _txt(doc, view, bold_id,
             _BQ_CAT_W - _BQ_PAD_X, cy - _BQ_PAD_Y,
             _BQ_CAT_W - _BQ_PAD_X * 2, cat)
        _txt(doc, view, tnt_id,
             tw - _BQ_PAD_X, cy - _BQ_PAD_Y,
             _BQ_NOTE_W - _BQ_PAD_X * 2, note)
        cy = bot

    if not BQ_NOTES:
        _ln(doc, view, 0, cy, tw, cy, border_gs)

    return view
