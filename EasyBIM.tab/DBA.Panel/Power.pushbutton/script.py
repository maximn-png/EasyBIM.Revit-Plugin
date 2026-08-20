# -*- coding: utf-8 -*-
"""Dekel Power Equipment Tool — ציוד חשמלי, פיקסצ'רים, גנרטורים, שנאים"""

__title__   = "Power"
__doc__     = (u"מתאים Electrical Equipment & Fixtures לסעיפי דקל.\n"
               u"תומך בגנרטורים, שנאים יבשים, UPS, לוחות ופיקסצ'רים.")
__author__  = "Yamit Bettman"

import re, os, codecs, zipfile, glob
import xml.etree.ElementTree as ET

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("System")

from System.Windows.Forms import (
    OpenFileDialog, DialogResult,
    Form, Label, Button, ListView, ListViewItem,
    ColumnHeader, View, HorizontalAlignment,
    FormStartPosition, FormBorderStyle,
    RightToLeft as WinRTL, Panel, FlatStyle,
)
from System.Drawing import Font, FontStyle, Color, Size, Point
import System

from Autodesk.Revit.DB import (
    Transaction, BuiltInCategory, FilteredElementCollector,
    CategorySet, InstanceBinding, ElementId,
    BuiltInParameter, XYZ,
)
try:
    from Autodesk.Revit.DB import BuiltInParameterGroup as _BPG
    _PARAM_GROUP = _BPG.INVALID
except (ImportError, AttributeError):
    # Revit 2025+ removed BuiltInParameterGroup; GroupTypeId is the replacement.
    # Try attribute names in order — the available name varies by sub-version.
    from Autodesk.Revit.DB import GroupTypeId as _GTI
    _PARAM_GROUP = next(
        (getattr(_GTI, n) for n in ('Invalid', 'Other', 'General', 'Data')
         if getattr(_GTI, n, None) is not None),
        None
    )

def _insert_binding(doc, defn, binding):
    doc.ParameterBindings.Insert(defn, binding, _PARAM_GROUP)

def _reinsert_binding(doc, defn, binding):
    doc.ParameterBindings.ReInsert(defn, binding, _PARAM_GROUP)
from Autodesk.Revit.UI import TaskDialog
from System.Collections.Generic import List as CList
from pyrevit import revit
from easybim.dekel_shared_params import ensure_dekel_params

doc   = revit.doc
uidoc = revit.uidoc

# batch mode (pyrevit run): doc is None, open first model from __models__
if doc is None:
    try:
        from Autodesk.Revit.DB import OpenOptions, ModelPathUtils
        _batch_models = __models__  # injected by pyrevit run
        if _batch_models:
            _mp  = ModelPathUtils.ConvertUserVisiblePathToModelPath(_batch_models[0])
            _uid = revit.HOST_APP.uiapp.OpenAndActivateDocument(_mp, OpenOptions(), False)
            doc   = _uid.Document
            uidoc = _uid
            print(u"[batch] opened: {}".format(doc.Title))
    except Exception as _e:
        print(u"[batch] could not open model: {}".format(_e))

NS    = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# ──────────────────────────────────────────────────────────────────────────────
# PARAMETERS
# Generators use PARAM_CODE/CODE2/TOTAL (slots 1+2 + total).
# Transformers (dry only) use PARAM_TR_CODE/CODE2/CODE3/CODE4/TOTAL (slots 1-4 + total).
# ──────────────────────────────────────────────────────────────────────────────
# גנרטורים
PARAM_CODE   = u"מספר סעיף דקל"
PARAM_DESC   = u"תיאור סעיף דקל"
PARAM_PRICE  = u"מחיר דקל"
PARAM_CODE2  = u"מספר סעיף דקל 2"
PARAM_DESC2  = u"תיאור סעיף דקל 2"
PARAM_PRICE2 = u"מחיר דקל 2"
PARAM_TOTAL  = u"סה\"כ לגנרטור"

# שנאים יבשים — 4 סלוטים
PARAM_TR_CODE   = u"מספר סעיף דקל שנאי"
PARAM_TR_DESC   = u"תיאור סעיף דקל שנאי"
PARAM_TR_PRICE  = u"מחיר דקל שנאי"
PARAM_TR_CODE2  = u"מספר סעיף דקל שנאי 2"
PARAM_TR_DESC2  = u"תיאור סעיף דקל שנאי 2"
PARAM_TR_PRICE2 = u"מחיר דקל שנאי 2"
PARAM_TR_CODE3  = u"מספר סעיף דקל שנאי 3"
PARAM_TR_DESC3  = u"תיאור סעיף דקל שנאי 3"
PARAM_TR_PRICE3 = u"מחיר דקל שנאי 3"
PARAM_TR_CODE4  = u"מספר סעיף דקל שנאי 4"
PARAM_TR_DESC4  = u"תיאור סעיף דקל שנאי 4"
PARAM_TR_PRICE4 = u"מחיר דקל שנאי 4"
PARAM_TR_TOTAL  = u"סה\"כ לשנאי"

# סלוט 3 — נקודות תקשורת לתחנות עבודה
PARAM_CODE3  = u"מספר סעיף דקל 3"
PARAM_DESC3  = u"תיאור סעיף דקל 3"
PARAM_PRICE3 = u"מחיר דקל 3"
PARAM_QTY3   = u"כמות 3"

PARAM_DEFS = [
    # גנרטורים — 2 סלוטים + סה"כ
    (PARAM_CODE,  u"TEXT"), (PARAM_DESC,  u"TEXT"), (PARAM_PRICE,  u"TEXT"),
    (PARAM_CODE2, u"TEXT"), (PARAM_DESC2, u"TEXT"), (PARAM_PRICE2, u"TEXT"),
    (PARAM_TOTAL, u"TEXT"),
    # שנאים יבשים — 4 סלוטים + סה"כ
    (PARAM_TR_CODE,  u"TEXT"), (PARAM_TR_DESC,  u"TEXT"), (PARAM_TR_PRICE,  u"TEXT"),
    (PARAM_TR_CODE2, u"TEXT"), (PARAM_TR_DESC2, u"TEXT"), (PARAM_TR_PRICE2, u"TEXT"),
    (PARAM_TR_CODE3, u"TEXT"), (PARAM_TR_DESC3, u"TEXT"), (PARAM_TR_PRICE3, u"TEXT"),
    (PARAM_TR_CODE4, u"TEXT"), (PARAM_TR_DESC4, u"TEXT"), (PARAM_TR_PRICE4, u"TEXT"),
    (PARAM_TR_TOTAL, u"TEXT"),
    # סלוט 3 — תקשורת
    (PARAM_CODE3, u"TEXT"), (PARAM_DESC3, u"TEXT"),
    (PARAM_PRICE3, u"TEXT"), (PARAM_QTY3, u"TEXT"),
]

# ── ברירות מחדל לגנרטורים ────────────────────────────────────────────────────
FIRE_PANEL_CODE = u"39.070.0010"
FIRE_PANEL_DESC = u"פנל התראות בהתאם לדרישות כיבוי אש"

# ── ברירות מחדל לשנאים יבשים (סלוטים 2-4) ───────────────────────────────────
TR_SLOT2_CODE = u"08.093.0200"   # קופסת פיקוד לשנאי
TR_SLOT3_CODE = u"08.093.0130"   # תוספת אוורור מאולץ
TR_SLOT4_CODE = u"08.093.0080"   # תוספת הגנות DGPT

# ── נקודת תקשורת (סלוט 3 לתחנות with Comm) ─────────────────────────────────
# קוד קבוע — נקודת תקשורת אחודה מושלמת CAT-6A.
# המחיר/תיאור fallback משמשים כשקובץ הדקל שנטען לא כולל את סעיף 08.019.
COMM_CODE  = u"08.019.0720"
COMM_DESC  = u"נקודת תקשורת אחודה מושלמת CAT-6A"
COMM_PRICE = 371.0

# ── טיפוסים שיש להתעלם מהם (לא נכללים בכתב הכמויות) ─────────────────────────
# פתחים (Round/Square Opening) הם חורי מעבר — לא מתומחרים כסעיף חשמל נפרד.
# אלמנט שבשם המשפחה/הטיפוס שלו מופיעה אחת המילים — ידולג בשקט (לא נספר ככשל).
IGNORE_FIXTURE_KEYWORDS = (u"opening",)

# ── קודי שנאים יבשים בלבד (לסינון) ─────────────────────────────────────────
DRY_TR_CODES = frozenset([
    u"08.093.0100", u"08.093.0110", u"08.093.0120",
    u"08.093.0125", u"08.093.0126", u"08.093.0127",
])

# ──────────────────────────────────────────────────────────────────────────────
# EQUIPMENT MAP
# ──────────────────────────────────────────────────────────────────────────────
EQUIPMENT_MAP = {
    u"DBA Transformer - Dry Type - 630 kVA":  (u"08.093.0100", u"שנאי יבש 630kVA"),
    u"DBA Transformer - Dry Type - 800 kVA":  (u"08.093.0110", u"שנאי יבש 800kVA"),
    u"DBA Transformer - Dry Type - 1000 kVA": (u"08.093.0120", u"שנאי יבש 1000kVA"),
    u"DBA Transformer - Dry Type - 1250 kVA": (u"08.093.0125", u"שנאי יבש 1250kVA"),
    u"DBA Transformer - Dry Type - 1600 kVA": (u"08.093.0126", u"שנאי יבש 1600kVA"),
    u"DBA Transformer - Dry Type - 2000 kVA": (u"08.093.0127", u"שנאי יבש 2000kVA"),
    u"DBA Transformer - Oil Type - 630 kVA":  (u"08.093.0040", u"שנאי שמן 630kVA"),
    u"DBA Transformer - Oil Type - 800 kVA":  (u"08.093.0050", u"שנאי שמן 800kVA"),
    u"DBA Transformer - Oil Type - 1000 kVA": (u"08.093.0060", u"שנאי שמן 1000kVA"),
    u"DBA Transformer - Oil Type - 1250 kVA": (u"08.093.0070", u"שנאי שמן 1250kVA"),
    u"DBA Transformer - Oil Type - 1600 kVA": (u"08.093.0075", u"שנאי שמן 1600kVA"),
    u"DBA Transformer - Oil Type - 2000 kVA": (u"08.093.0077", u"שנאי שמן 2000kVA"),
    u"DBA Transformer Control Panel":         (u"08.093.0200", u"קופסת פיקוד לשנאי"),
    u"DBA Uninterruptible Power Supply - 10 kVA": (u"08.048.1100", u"מערכת אל-פסק 10kVA"),
    u"DBA Uninterruptible Power Supply - 20 kVA": (u"08.048.1120", u"מערכת אל-פסק 20kVA"),
    u"DBA Uninterruptible Power Supply - 40 kVA": (u"08.048.1140", u"מערכת אל-פסק 40kVA"),
    u"DBA Uninterruptible Power Supply - 80 kVA": (u"08.048.1160", u"מערכת אל-פסק 80kVA"),
    u"DBA HV Circuit Breaker Switchboard":    (u"08.095.0010", u"לוח מתח גבוה"),
    u"DBA Fire Alarm Control Panel":          (u"08.019.0490", u"רכזת גילוי אש"),
    u"DBA Fire Fighter Control Panel":        (u"34.012.0750", u"רכזת בפנל כבאים"),
    u"DBA Smoke Release Windows Control Unit":(u"34.025.0010", u"מערכת פיקוד פתחי עשן"),
}

SWITCHBOARD_CELLS = [
    (400,600,"08.061.0050"),(400,800,"08.061.0060"),(600,600,"08.061.0080"),
    (800,600,"08.061.0090"),(800,800,"08.061.0110"),(1000,600,"08.061.0120"),
    (1000,800,"08.061.0130"),(1200,800,"08.061.0131"),(2000,400,"08.061.0048"),
    (2000,600,"08.061.0046"),(2000,800,"08.061.0044"),(2000,1000,"08.061.0042"),
    (2200,400,"08.061.0040"),(2200,600,"08.061.0030"),(2200,800,"08.061.0020"),
    (2200,1000,"08.061.0010"),
]
PANELBOARD_MCB = {
    25:(u"08.061.0640",u"08.061.0600"), 40:(u"08.061.0650",u"08.061.0610"),
    63:(u"08.061.0660",u"08.061.0620"), 80:(u"08.061.0670",u"08.061.0630"),
    100:(u"08.061.0675",u"08.061.0632"),125:(u"08.061.0680",u"08.061.0635"),
}

# ──────────────────────────────────────────────────────────────────────────────
# FIXTURE MAP
# ──────────────────────────────────────────────────────────────────────────────
FIXTURE_MAP = {
    u"DBA Load Break Switch - 2P 16A":(u"08.073.0211",u"מפסק 2X16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Load Break Switch - 2P 25A":(u"08.073.0231",u"מפסק 2X25A",u"08.019.0320",u"נקודה 4mm²"),
    u"DBA Load Break Switch - 4P 16A":(u"08.073.0232",u"מפסק 4X16A",u"08.019.0340",u"נקודה 3P 2.5mm²"),
    u"DBA Load Break Switch - 4P 32A":(u"08.073.0233",u"מפסק 4X32A",u"08.019.0350",u"נקודה 3P 4mm²"),
    u"DBA Load Break Switch - 4P 63A":(u"08.073.0245",u"מפסק 4X63A",u"08.019.0350",u"נקודה 3P 4mm²"),
    u"DBA Mounted Socket - x1":(u"08.072.0023",u'שקע עה"ט x1',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Socket - x2":(u"08.072.0024",u'שקע עה"ט x2',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Socket - x3":(u"08.072.0025",u'שקע עה"ט x3',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Socket - x4":(u"08.072.0026",u'שקע עה"ט x4',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Socket - AC":(u"08.019.0100",u"נקודה מזגן",None,u""),
    u"DBA Recessed Socket - x1":(u"08.072.0010",u'שקע תה"ט x1',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Socket - x2":(u"08.072.0020",u'שקע תה"ט x2',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Socket - x3":(None,u'שקע תה"ט x3 — דורש החלטה',None,u""),
    u"DBA Recessed Socket - x4":(None,u'שקע תה"ט x4 — דורש החלטה',None,u""),
    u"DBA Recessed Socket - AC":(u"08.019.0100",u"נקודה מזגן",None,u""),
    u"DBA Recessed Socket - Venta":(u"08.072.0010",u'שקע תה"ט רגיל',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed 3P Socket - AC":(u"08.019.0167",u"נקודה מזגן 3P",None,u""),
    u"DBA Mounted 3P Socket":(u"08.019.0430",u"נקודה תלת-פאזי",None,u""),
    u"DBA Recessed 3P Socket":(u"08.019.0430",u"נקודה תלת-פאזי",None,u""),
    u"DBA CEE Socket - 1x16A CEE":(u"08.072.0040",u"שקע CEE 3P 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA CEE Socket - 3x16A CEE":(u"08.072.0060",u"שקע CEE 5P 16A",u"08.019.0340",u"נקודה 3P"),
    u"DBA CEE Socket / 1x16A CEE":(u"08.072.0040",u"שקע CEE 3P 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA CEE Socket / 3x16A CEE":(u"08.072.0060",u"שקע CEE 5P 16A",u"08.019.0340",u"נקודה 3P"),
    u"DBA EV Charging Station - 60 X 25 X 25 cm 11kW":(u"08.050.0210",u"עמדת טעינה 11kW",None,u""),
    u"DBA EV Charging Station - 125 x 35 x 50 cm 22kW":(u"08.050.0220",u"עמדת טעינה 22kW",None,u""),
    u"DBA EV Charging Station / 60 X 25 X 25 cm 11kW":(u"08.050.0210",u"עמדת טעינה 11kW",None,u""),
    u"DBA EV Charging Station / 125 x 35 x 50 cm 22kW":(u"08.050.0220",u"עמדת טעינה 22kW",None,u""),
    u"DBA EV Charging Station":(u"08.050.0210",u"עמדת טעינה MODE 3",None,u""),
    u"DBA Thermostat - AC Mounted":(u"08.046.0390",u"תרמוסטט",u"08.019.0475",u"נקודת תרמוסטט"),
    u"DBA Thermostat - AC Recessed":(u"08.046.0390",u"תרמוסטט",u"08.019.0475",u"נקודת תרמוסטט"),
    u"DBA Thermostat - Underfloor Heating":(u"08.046.0380",u"תרמוסטט רצפה",u"08.019.0475",u"נקודת תרמוסטט"),
    u"DBA Recessed Electric Shade-Curtain":(u"08.019.0800",u"נקודת תריס",None,u""),
    u"DBA Push Button":(u"08.073.0510",u'לחצן חירום תה"ט',None,u""),
    u"DBA Recessed Doorbell":(u"08.019.0205",u"נקודת פעמון",None,u""),
    u"DBA Grounding Recessed Junction Box":(u"08.026.0150",u"תיבת הארקת יסוד",None,u""),
    u"DBA Recessed Grounding Junction Box":(u"08.026.0150",u"תיבת הארקת יסוד",None,u""),
    u"DBA Recessed Junction Box":(u"08.026.0160",u"קופסת הסתעפות IP65",None,u""),
    u"DBA Socket Column":(u"08.072.0820",u"קופסת שקעים",None,u""),
    u"DBA Floor Sockets":(u"08.072.0820",u"קופסת שקעים",None,u""),
    u"DBA Grounding Out - Elevator":(u"08.040.0120",u'פס מגולוון 40×4 מ"מ',None,u""),
    u"DBA Grounding Out - Strip":(u"08.040.0120",u'פס מגולוון 40×4 מ"מ',None,u""),
    u"DBA Grounding Out":(u"08.040.0110",u'פס מגולוון 30×3.5 מ"מ',None,u""),
    u"DBA Grounding Down":(u"08.040.0110",u"מוליך הורדה - ברזל עגול",None,u""),
    u"DBA Grounding Up":(u"08.040.0110",u"מוליך עלייה - ברזל עגול",None,u""),
    u"DBA Grounding Pit":(u"08.040.0020",u"שוחת ביקורת בטון Ø50",None,u""),
    u"DBA Grounding Pot":(u"08.040.0025",u"שוחת ביקורת פלסטית",None,u""),
    u"DBA Grounding Flex Connection":(u"08.040.0100",u"גשר הארקה תקני",None,u""),
    u"DBA Grounding Antistatic Floor":(u"08.040.0050",u"נקודת הארקה 16mm²",None,u""),
    u"DBA Round Opening":(None,u"פתח עגול — ללא סעיף",None,u""),
    u"DBA Square Opening":(None,u"פתח מרובע — ללא סעיף",None,u""),
    u"DBA Mounted Elec Workstation":(None,u"תחנת עבודה — תמחור מיוחד",None,u""),
    u"DBA Recessed Elec WorkStation":(None,u"תחנת עבודה — תמחור מיוחד",None,u""),
    u"DBA Mounted Elec Workstation - D11": (u"08.072.0300",u"רב בתי תקע D11",None,u""),
    u"DBA Mounted Elec Workstation - D14": (u"08.072.0310",u"רב בתי תקע D14",None,u""),
    u"DBA Mounted Elec Workstation - D17": (u"08.072.0320",u"רב בתי תקע D17",None,u""),
    u"DBA Mounted Elec Workstation - D18": (u"08.072.0330",u"רב בתי תקע D18",None,u""),
    u"DBA Mounted Elec Workstation - D20": (u"08.072.0340",u"רב בתי תקע D20",None,u""),
    u"DBA Recessed Elec WorkStation - D11":(u"08.072.0300",u"רב בתי תקע D11",None,u""),
    u"DBA Recessed Elec WorkStation - D14":(u"08.072.0310",u"רב בתי תקע D14",None,u""),
    u"DBA Recessed Elec WorkStation - D17":(u"08.072.0320",u"רב בתי תקע D17",None,u""),
    u"DBA Recessed Elec WorkStation - D18":(u"08.072.0330",u"רב בתי תקע D18",None,u""),
    u"DBA Recessed Elec WorkStation - D20":(u"08.072.0340",u"רב בתי תקע D20",None,u""),
    # with Comm — same hardware codes as base type; comm accessory billed separately
    u"DBA Mounted Elec Workstation - D14 with Comm":  (u"08.072.0310",u"רב בתי תקע D14",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Elec Workstation - D17 with Comm":  (u"08.072.0320",u"רב בתי תקע D17",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Elec Workstation - D18 with Comm":  (u"08.072.0330",u"רב בתי תקע D18",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Elec Workstation - D20 with Comm":  (u"08.072.0340",u"רב בתי תקע D20",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Elec WorkStation - D14 with Comm": (u"08.072.0310",u"רב בתי תקע D14",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Elec WorkStation - D17 with Comm": (u"08.072.0320",u"רב בתי תקע D17",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Elec WorkStation - D18 with Comm": (u"08.072.0330",u"רב בתי תקע D18",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Elec WorkStation - D20 with Comm": (u"08.072.0340",u"רב בתי תקע D20",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Mounted Elec Workstation with UPS":(None,u"תחנת עבודה + UPS — דורש החלטה",None,u""),
    u"DBA Recessed Elec WorkStation with UPS":(None,u"תחנת עבודה + UPS — דורש החלטה",None,u""),
    # שקעים מוגני מים (על הטיח) — לפי כמות בתי תקע
    u"DBA Waterproof Mounted Socket - x1":(u"08.072.0030",u"שקע מוגן מים 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Waterproof Mounted Socket - x2":(u"08.072.0031",u"שקע מוגן מים כפול 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Waterproof Mounted Socket - x3":(u"08.072.0032",u"3 שקעים מוגני מים 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    # שקעים מוגני מים (תה"ט) — קוד גנרי זהה, אין סעיף מוגן-מים תה"ט נפרד בקטלוג
    u"DBA Waterproof Recessed Socket - x1":(u"08.072.0030",u"שקע מוגן מים 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Waterproof Recessed Socket - x2":(u"08.072.0031",u"שקע מוגן מים כפול 16A",u"08.019.0310",u"נקודה 2.5mm²"),
    # מכשירי חימום בחדר רטוב — נקודת חיבור מכשיר מוגנת מים (מ"ז בתיבה אטומה)
    u"DBA Waterproof Mounted Socket - Mirror Heater":(u"08.019.0410",u"נקודת מכשיר מוגנת מים 16A",None,u""),
    u"DBA Waterproof Mounted Socket - Towel Warmer":(u"08.019.0410",u"נקודת מכשיר מוגנת מים 16A",None,u""),
    u"DBA Waterproof Recessed Socket - Mirror Heater":(u"08.019.0410",u"נקודת מכשיר מוגנת מים 16A",None,u""),
    u"DBA Waterproof Recessed Socket - Towel Warmer":(u"08.019.0410",u"נקודת מכשיר מוגנת מים 16A",None,u""),
    # שקע עם מפסק מובנה — שקע דגם בריטי עם מפסק מואר
    # שקע עם מתג — סעיף מאוחד: שקע תה"ט (0010) + מ"ז דו-קוטבי (0150) = ₪66
    u"DBA Recessed Switched Socket - Remote Switch":(u"08.072.0010+08.073.0150",u'שקע תה"ט + מ"ז דו-קוטבי 16A',u"08.019.0310",u"נקודה 2.5mm²"),
    u"DBA Recessed Switched Socket - With Switch":(u"08.072.0010+08.073.0150",u'שקע תה"ט + מ"ז דו-קוטבי 16A',u"08.019.0310",u"נקודה 2.5mm²"),
}

# ──────────────────────────────────────────────────────────────────────────────
# GENERATOR MATCHING
# ──────────────────────────────────────────────────────────────────────────────
_KVA_RE = re.compile(r"KVA([\d,]+)\s+STANDBY", re.IGNORECASE)
_KW_RE  = re.compile(r"(\d+)\s*kW", re.IGNORECASE)
MAX_KVA = 2500


def build_kva_map(catalog):
    kva_map = {}
    for code, info in catalog.items():
        if not code.startswith(u"39.010."):
            continue
        m = _KVA_RE.search(info.get(u"title", u""))
        if m:
            kva = int(m.group(1).replace(",", ""))
            if kva not in kva_map:
                kva_map[kva] = (code, info.get(u"price"))
    return kva_map


def match_generator(typ, kva_map):
    m = _KW_RE.search(typ)
    if not m:
        return None, None, None, u"לא ניתן לפרש הספק גנרטור", 0
    if not kva_map:
        return None, None, None, u"קובץ GEN-39 לא נטען", 0
    kw      = int(m.group(1))
    kva_req = kw / 0.8
    closest = min(kva_map.keys(), key=lambda k: abs(k - kva_req))
    code, price = kva_map[closest]
    note = u""
    if kva_req > MAX_KVA:
        note = u"מעל 2500kVA — מחיר אינדיקטיבי ({} kW)".format(kw)
    elif abs(closest - kva_req) > kva_req * 0.15:
        note = u"הוחלף ל-{} kVA (מבוקש {:.0f} kVA)".format(closest, kva_req)
    desc = u"גנרטור דיזל {} kVA STANDBY ({} kW)".format(closest, kw)
    return code, desc, price, note, closest

# ────────────────────────────────────────────────────────────────────
# SHARED PARAMETERS — דרך המודול המשותף (GUID קבועים + מיזוג קטגוריות)
# ────────────────────────────────────────────────────────────────────
# כלי ה-Power עובד על ציוד חשמלי (שנאים/גנרטורים) + אביזרים
POWER_CATEGORIES = [
    BuiltInCategory.OST_ElectricalEquipment,
    BuiltInCategory.OST_ElectricalFixtures,
]

t_params = Transaction(doc, u"Dekel Power - Ensure Shared Parameters")
t_params.Start()
try:
    rep = ensure_dekel_params(doc, PARAM_DEFS, POWER_CATEGORIES)
    t_params.Commit()
    if rep["created"]:
        print(u"נוצרו {} פרמטרים".format(len(rep["created"])))
    if rep["extended"]:
        print(u"קושרו לקטגוריות החשמל {} פרמטרים קיימים".format(len(rep["extended"])))
    if rep["ok"]:
        print(u"{} פרמטרים כבר היו מקושרים כראוי".format(len(rep["ok"])))
    if rep["failed"]:
        print(u"  [אזהרה] לא ניתן לטפל ב-{} פרמטרים: {}".format(
            len(rep["failed"]), u", ".join(rep["failed"])))
        TaskDialog.Show("Dekel", u"חלק מהפרמטרים לא נוצרו/קושרו:\n{}".format(
            u", ".join(rep["failed"])))
except Exception as e:
    try: t_params.RollBack()
    except Exception: pass
    TaskDialog.Show("Dekel", u"שגיאה ביצירת/קישור פרמטרים:\n{}".format(e))
    import sys; sys.exit()

# ──────────────────────────────────────────────────────────────────────────────
# EXCEL READ
# ──────────────────────────────────────────────────────────────────────────────
def _col(ref):
    m = re.match(r"([A-Za-z]+)", ref)
    if not m: return 0
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _val(c, shared):
    ie = c.find("{%s}is" % NS)
    if ie is not None:
        return u"".join(t.text or u"" for t in ie.iter("{%s}t" % NS))
    v = c.find("{%s}v" % NS)
    if v is None: return None
    if c.get("t", "") == "s":
        try:    return shared[int(v.text)]
        except: return v.text
    try:    return float(v.text)
    except: return v.text


def read_xlsx(path, catalog):
    """קורא את כל הגיליונות מקובץ xlsx אחד לתוך catalog."""
    with zipfile.ZipFile(path, "r") as z:
        names  = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter("{%s}si" % NS):
                shared.append(u"".join(t.text or u"" for t in si.iter("{%s}t" % NS)))
        for sf in sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            root = ET.fromstring(z.read(sf))
            for row in root.iter("{%s}row" % NS):
                rd = {}
                for c in row:
                    rd[_col(c.get("r", ""))] = _val(c, shared)
                code  = u"{}".format(rd.get(0) or u"").strip()
                title = u"{}".format(rd.get(1) or u"")[:150]
                price = rd.get(3)
                if not re.match(r"\d{2}\.\d{3}\.\d{4}", code):
                    continue
                try:    price = float(price) if price else None
                except: price = None
                catalog[code] = {u"title": title, u"price": price}


# ──────────────────────────────────────────────────────────────────────────────
# FILE SELECTION — multi-select OpenFileDialog
# ──────────────────────────────────────────────────────────────────────────────
dlg = OpenFileDialog()
dlg.Title       = u"בחר קבצי טבלאות דקל (ניתן לבחור מספר קבצים)"
dlg.Filter      = "Excel Files (*.xlsx)|*.xlsx"
dlg.Multiselect = True

if dlg.ShowDialog() != DialogResult.OK or not dlg.FileNames:
    TaskDialog.Show("Dekel", u"לא נבחר קובץ.")
    import sys; sys.exit()

selected_paths = list(dlg.FileNames)
print(u"קבצים שנבחרו: {}".format(len(selected_paths)))

catalog = {}
for path in selected_paths:
    before = len(catalog)
    read_xlsx(path, catalog)
    added = len(catalog) - before
    print(u"  \u2713 {} \u2014 {} \u05e1\u05e2\u05d9\u05e4\u05d9\u05dd".format(os.path.basename(path), added))

print(u"\u05e1\u05d4\"כ בקטלוג: {} סעיפים".format(len(catalog)))

kva_map = build_kva_map(catalog)
if kva_map:
    print(u"מפת גנרטורים: {} ערכי kVA ({}-{})".format(
        len(kva_map), min(kva_map), max(kva_map)))
else:
    print(u"[!] לא נמצאו קודי 39.010 — גנרטורים לא יתומחרו")

# ──────────────────────────────────────────────────────────────────────────────
# MATCHING HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def get_ft(elem):
    try:
        sym = elem.Symbol
        return sym.FamilyName.strip(), sym.Name.strip()
    except Exception: pass
    try:
        p_fam = elem.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM)
        p_typ = elem.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        fam = p_fam.AsValueString().strip() if p_fam else u""
        typ = p_typ.AsValueString().strip() if p_typ else u""
        if fam: return fam, typ
    except Exception: pass
    try:
        etype = doc.GetElement(elem.GetTypeId())
        if etype:
            p = etype.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
            fam = p.AsString().strip() if p else u""
            typ = etype.Name.strip() if hasattr(etype, "Name") else u""
            return fam, typ
    except Exception: pass
    return u"", u""


def setp(elem, name, val):
    p = elem.LookupParameter(name)
    if not p or p.IsReadOnly: return False
    try:
        st = str(p.StorageType)
        if   "String"  in st: p.Set(u"{}".format(val))
        elif "Double"  in st: p.Set(float(val))
        elif "Integer" in st: p.Set(int(float(val)))
        else:                  p.Set(float(val))
        return True
    except: return False


def format_price(price):
    if price is None: return u""
    amount = int(round(float(price)))
    if amount == 0: return u""
    s = str(abs(amount))
    parts = []
    while len(s) > 3:
        parts.append(s[-3:])
        s = s[:-3]
    parts.append(s)
    return u"\u20aa" + u",".join(reversed(parts))


CODE_RE = re.compile(r"^\d{2}\.\d{3}\.\d{4}$")


def get_prefilled(elem):
    sources = [elem]
    try:
        etype = doc.GetElement(elem.GetTypeId())
        if etype: sources.append(etype)
    except Exception: pass
    for n1, n2 in [(u"DBA_\u05de\u05e1' \u05e1\u05e2\u05d9\u05e3 \u05d3\u05e7\u05dc (1)",
                    u"DBA_\u05de\u05e1' \u05e1\u05e2\u05d9\u05e3 \u05d3\u05e7\u05dc (2)")]:
        for src in sources:
            try:
                p1 = src.LookupParameter(n1); p2 = src.LookupParameter(n2)
                c1 = (p1.AsString() or u"").strip() if p1 and p1.HasValue else u""
                c2 = (p2.AsString() or u"").strip() if p2 and p2.HasValue else u""
                if CODE_RE.match(c1) or CODE_RE.match(c2):
                    return (c1 if CODE_RE.match(c1) else None,
                            c2 if CODE_RE.match(c2) else None)
            except Exception: pass
    return None, None


def _inject_tr_slots(elem, code1, desc1, price1):
    """
    כותב 4 סלוטים לשנאי יבש בלבד לפרמטרי PARAM_TR_*:
      סלוט 1 — סוג השנאי (מועבר מה-caller)
      סלוט 2 — 08.093.0200 קופסת פיקוד לשנאי
      סלוט 3 — 08.093.0130 תוספת אוורור מאולץ
      סלוט 4 — 08.093.0080 תוספת הגנות DGPT
      total  — סכום כל 4
    """
    setp(elem, PARAM_TR_CODE,  code1)
    setp(elem, PARAM_TR_DESC,  desc1 or u"")
    setp(elem, PARAM_TR_PRICE, format_price(price1))

    s2_price = catalog.get(TR_SLOT2_CODE, {}).get(u"price")
    s2_desc  = catalog.get(TR_SLOT2_CODE, {}).get(u"title", u"קופסת פיקוד לשנאי")[:150]
    setp(elem, PARAM_TR_CODE2,  TR_SLOT2_CODE)
    setp(elem, PARAM_TR_DESC2,  s2_desc)
    setp(elem, PARAM_TR_PRICE2, format_price(s2_price))

    s3_price = catalog.get(TR_SLOT3_CODE, {}).get(u"price")
    s3_desc  = catalog.get(TR_SLOT3_CODE, {}).get(u"title", u"תוספת לשנאי — אוורור מאולץ")[:150]
    setp(elem, PARAM_TR_CODE3,  TR_SLOT3_CODE)
    setp(elem, PARAM_TR_DESC3,  s3_desc)
    setp(elem, PARAM_TR_PRICE3, format_price(s3_price))

    s4_price = catalog.get(TR_SLOT4_CODE, {}).get(u"price")
    s4_desc  = catalog.get(TR_SLOT4_CODE, {}).get(u"title", u"תוספת הגנות DGPT")[:150]
    setp(elem, PARAM_TR_CODE4,  TR_SLOT4_CODE)
    setp(elem, PARAM_TR_DESC4,  s4_desc)
    setp(elem, PARAM_TR_PRICE4, format_price(s4_price))

    total = (price1 or 0) + (s2_price or 0) + (s3_price or 0) + (s4_price or 0)
    setp(elem, PARAM_TR_TOTAL, format_price(total))


def match_equip(elem):
    fam, typ = get_ft(elem)
    for key in [u"{} - {}".format(fam, typ), fam]:
        if key in EQUIPMENT_MAP:
            code, desc = EQUIPMENT_MAP[key]
            price = catalog.get(code, {}).get(u"price") if code else None
            return code, desc, price, u""
    if u"Generator" in fam or u"generator" in fam:
        code, desc, price, note, kva = match_generator(typ, kva_map)
        return code, desc, price, note
    if u"Circuit Breaker Switchboard" in fam and u"HV" not in fam:
        nums = re.findall(r"[\d.]+", typ)
        if len(nums) >= 2:
            h, w = float(nums[0]) * 10, float(nums[1]) * 10
            best = min(SWITCHBOARD_CELLS, key=lambda r: abs(r[0]-h)+abs(r[1]-w))
            code = best[2]
            return code, u"מבנה פח {}×{}".format(int(best[0]), int(best[1])), \
                   catalog.get(code, {}).get(u"price"), u""
    mcb = re.search(r"(\d+)\s*A", typ)
    if mcb and u"Panelboard" in fam:
        amps    = int(mcb.group(1))
        closest = min(PANELBOARD_MCB.keys(), key=lambda k: abs(k - amps))
        is_rec  = u"Recessed" in fam
        code    = PANELBOARD_MCB[closest][0] if is_rec else PANELBOARD_MCB[closest][1]
        return code, u"{}A".format(amps), catalog.get(code, {}).get(u"price"), u""
    return None, None, None, u""


def _price_of(code):
    # תומך בקוד מאוחד בפורמט "A+B" — מסכם את מחירי כל הסעיפים.
    # לקוד יחיד מחזיר את המחיר מהקטלוג (התנהגות רגילה).
    if not code:
        return None
    total = 0.0
    found = False
    for part in code.split(u"+"):
        pr = catalog.get(part.strip(), {}).get(u"price")
        if pr is not None:
            total += pr
            found = True
    return total if found else None


def _title_of(code, label):
    # לקוד מאוחד — משתמשים בתווית מהמיפוי (אין כותרת מאוחדת בקטלוג).
    # לקוד יחיד — כותרת הקטלוג, ואם אין אז התווית.
    if not code:
        return label
    if u"+" in code:
        return label
    return (catalog.get(code, {}).get(u"title", label) or label)[:60]


def match_fix(elem):
    fam, typ = get_ft(elem)
    c1, c2 = get_prefilled(elem)
    if c1 or c2:
        p1 = _price_of(c1)
        t1 = _title_of(c1, u"") if c1 else u""
        p2 = _price_of(c2)
        t2 = _title_of(c2, u"") if c2 else u""
        return c1, t1, p1, c2, t2, p2
    for key in [u"{} - {}".format(fam, typ), u"{} / {}".format(fam, typ),
                u"{} {}".format(fam, typ), fam]:
        if key in FIXTURE_MAP:
            fc1, l1, fc2, l2 = FIXTURE_MAP[key]
            p1 = _price_of(fc1)
            t1 = _title_of(fc1, l1)
            p2 = _price_of(fc2)
            t2 = _title_of(fc2, l2)
            return fc1, t1, p1, fc2, t2, p2
    return None, None, None, None, None, None

# ──────────────────────────────────────────────────────────────────────────────
# COLLECT ALL ELECTRICAL EQUIPMENT
# ──────────────────────────────────────────────────────────────────────────────
equipment = list(FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_ElectricalEquipment)
    .WhereElementIsNotElementType().ToElements())

fixtures = list(FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_ElectricalFixtures)
    .WhereElementIsNotElementType().ToElements())


def _fam(elem):
    return get_ft(elem)[0]


generators       = [e for e in equipment if u"Generator"   in _fam(e)]
# DRY transformers only — oil transformers are excluded from all processing
dry_transformers = [e for e in equipment
                    if u"Transformer" in _fam(e) and u"Dry" in _fam(e)]

print(u"ציוד סה\"כ: {} | גנרטורים: {} | שנאים יבשים: {} | שקעים/אביזרים: {}".format(
    len(equipment), len(generators), len(dry_transformers), len(fixtures)))

skipped_details = []
failed_details  = []
_zoom = [None]

# ──────────────────────────────────────────────────────────────────────────────
# UPDATE — גנרטורים
# Slot 1: generator code/desc/price
# Slot 2: fire alarm panel (39.070.0010) — always
# Total:  sum of slots 1+2
# ──────────────────────────────────────────────────────────────────────────────
eq_ok = eq_sk = eq_fl = 0

t = Transaction(doc, u"Dekel Power - Update Generators")
t.Start()
for elem in generators:
    eid = str(elem.Id.IntegerValue)
    fam, typ = get_ft(elem)
    try:
        code, desc, price, note, kva = match_generator(typ, kva_map)
        if not code:
            eq_sk += 1
            skipped_details.append((eid, u"{} / {}".format(fam, typ),
                                    desc or note or u"ללא קוד"))
            continue

        ok = setp(elem, PARAM_CODE,  code)
        setp(elem, PARAM_DESC,  (u"{} | {}".format(desc, note) if note else desc) or u"")
        setp(elem, PARAM_PRICE, format_price(price))

        # סלוט 2: פנל התראות כיבוי אש
        fire_price = catalog.get(FIRE_PANEL_CODE, {}).get(u"price")
        setp(elem, PARAM_CODE2,  FIRE_PANEL_CODE)
        setp(elem, PARAM_DESC2,  FIRE_PANEL_DESC)
        setp(elem, PARAM_PRICE2, format_price(fire_price))

        # סה"כ — סלוטים 1+2
        total_gen = (price or 0) + (fire_price or 0)
        setp(elem, PARAM_TOTAL, format_price(total_gen))

        print(u"  [GEN] {} \u2192 {} | {}".format(typ, code, FIRE_PANEL_CODE))
        if ok: eq_ok += 1
        else:
            eq_fl += 1
            failed_details.append((eid, u"{} / {}".format(fam, typ), u"כשל כתיבה"))
    except Exception as e:
        eq_fl += 1
        failed_details.append((eid, u"{} / {}".format(fam, typ), u"{}".format(e)))
t.Commit()
print(u"גנרטורים: {}\u2713  {}\u26a0  {}\u2717".format(eq_ok, eq_sk, eq_fl))

# ──────────────────────────────────────────────────────────────────────────────
# UPDATE — שנאים יבשים בלבד (DRY ONLY)
# Slot 1: dry transformer code/desc/price
# Slot 2: 08.093.0200 קופסת פיקוד לשנאי
# Slot 3: 08.093.0130 תוספת אוורור מאולץ
# Slot 4: 08.093.0080 תוספת הגנות DGPT
# Total:  sum of all 4
# Oil transformers are completely IGNORED — no processing, no writing.
# ──────────────────────────────────────────────────────────────────────────────
tr_ok = tr_sk = tr_fl = 0
tr_skipped = []
tr_failed  = []

t_tr = Transaction(doc, u"Dekel Power - Update Dry Transformers")
t_tr.Start()
for elem in dry_transformers:
    eid = str(elem.Id.IntegerValue)
    fam, typ = get_ft(elem)
    try:
        code, desc = None, None
        for key in [u"{} - {}".format(fam, typ), fam]:
            if key in EQUIPMENT_MAP:
                c, d = EQUIPMENT_MAP[key]
                if c in DRY_TR_CODES:
                    code, desc = c, d
                    break
        if not code:
            tr_sk += 1
            tr_skipped.append((eid, u"{} / {}".format(fam, typ), u"ללא קוד שנאי יבש"))
            continue
        price = catalog.get(code, {}).get(u"price")
        _inject_tr_slots(elem, code, desc, price)
        print(u"  [DRY] {} \u2192 {}".format(typ, code))
        tr_ok += 1
    except Exception as e:
        tr_fl += 1
        tr_failed.append((eid, u"{} / {}".format(fam, typ), u"{}".format(e)))
t_tr.Commit()
print(u"שנאים יבשים: {}\u2713  {}\u26a0  {}\u2717".format(tr_ok, tr_sk, tr_fl))

# ──────────────────────────────────────────────────────────────────────────────
# COMM CONFIGURATION UI
# For each unique comm type found in the model (case-insensitive "comm" in type
# name), use pyrevit forms to ask the engineer for a per-type point count, then
# ask once whether to include comm points in the BoQ.
# Results consumed by t_fix loop below.
# ──────────────────────────────────────────────────────────────────────────────
from pyrevit import forms as _pf

_comm_types = {}  # {type_name: instance_count}
for _ce in fixtures:
    _, _ctyp = get_ft(_ce)
    if u"comm" in _ctyp.lower():
        _comm_types[_ctyp] = _comm_types.get(_ctyp, 0) + 1

comm_count_by_type = {}  # {type_name: n_comm_points_per_unit}
include_comm = False

if _comm_types:
    print(u"נמצאו {} טיפוסי תחנה עם תקשורת".format(len(_comm_types)))
    for _typ_name in sorted(_comm_types.keys()):
        _cnt = _comm_types[_typ_name]
        _result = _pf.ask_for_string(
            default=u"1",
            prompt=u'כמה נקודות תקשורת לסוג "{}"? ({} מופעים)'.format(_typ_name, _cnt),
            title=u"נקודות תקשורת"
        )
        if _result is None:
            break
        try:
            _n = int(_result.strip())
        except (ValueError, AttributeError):
            _n = 1
        if _n > 0:
            comm_count_by_type[_typ_name] = _n

    if comm_count_by_type:
        _incl = _pf.alert(
            u"כלול נקודות תקשורת בכתב הכמויות?",
            title=u"נקודות תקשורת",
            options=[u"כן", u"לא"]
        )
        include_comm = (_incl == u"כן")

# ── קוד נקודת תקשורת ────────────────────────────────────────────────────────
# אם 08.019.0720 קיים בקטלוג — משתמשים בו אוטומטית (המשתמש לא נשאל דבר).
# רק אם הוא לא נמצא (למשל דקל עדכנו את הקוד) — נפתחת חלונית לבחירת קוד חלופי.
_comm_catalog_code = COMM_CODE
_comm_info  = catalog.get(COMM_CODE, {})
_comm_price = _comm_info.get(u"price")
_comm_title = (_comm_info.get(u"title") or u"")[:60]

if _comm_price is None and include_comm:
    # ברירת המחדל לא נמצאה בקטלוג — מבקשים קוד חלופי מהמשתמש
    print(u"[!] קוד התקשורת {} לא נמצא בקטלוג שנטען.".format(COMM_CODE))
    while True:
        _code_in = _pf.ask_for_string(
            default=COMM_CODE,
            prompt=(u'קוד התקשורת {} לא נמצא בקטלוג שנטען.\n'
                    u'ייתכן שדקל עדכנו את הקוד.\n\n'
                    u'הזיני קוד דקל חלופי לנקודת תקשורת:').format(COMM_CODE),
            title=u"קוד תקשורת לא נמצא"
        )
        if not _code_in:
            break  # ביטול — נשאר עם ברירת המחדל + fallback
        _code_in = _code_in.strip()
        if not re.match(r"\d{2}\.\d{3}\.\d{4}", _code_in):
            _pf.alert(u"פורמט קוד לא תקין. דוגמה: 08.019.0720",
                      title=u"קוד לא תקין")
            continue
        _alt = catalog.get(_code_in, {})
        if _alt.get(u"price") is not None:
            _comm_catalog_code = _code_in
            _comm_info  = _alt
            _comm_price = _alt.get(u"price")
            _comm_title = (_alt.get(u"title") or u"")[:60]
            print(u"תקשורת: קוד חלופי נבחר — {}".format(_code_in))
            break
        else:
            _retry = _pf.alert(
                u"גם הקוד {} לא נמצא בקטלוג שנטען.".format(_code_in),
                title=u"קוד לא נמצא",
                options=[u"נסי שוב", u"ביטול"]
            )
            if _retry != u"נסי שוב":
                break

# fallback סופי אם עדיין אין מחיר (המשתמש ביטל או לא נמצא קוד תקף)
if _comm_price is None:
    _comm_price = COMM_PRICE
    _comm_title = _comm_title or COMM_DESC

if include_comm:
    _src = u"מהקטלוג" if _comm_info.get(u"price") is not None else u"גיבוי"
    print(u"תקשורת: {} טיפוסים מוגדרים, יכללו בטבלה (קוד: {}, מחיר {} — {})".format(
        len(comm_count_by_type), _comm_catalog_code,
        format_price(_comm_price) if _comm_price else u"—", _src))
elif _comm_types:
    print(u"תקשורת: לא נכלל בטבלת הכמויות")

# ──────────────────────────────────────────────────────────────────────────────
# UPDATE — שקעים/אביזרים חשמליים (OST_ElectricalFixtures)
# Slot 1+2: match_fix() — קוד ראשי + נקודת חשמל
# Slot 3:   נקודת תקשורת (רק לטיפוסי with Comm שסומנו include_comm)
# פתחים (IGNORE_FIXTURE_KEYWORDS) מדולגים בשקט ומנוקים משדות דקל קיימים.
# ──────────────────────────────────────────────────────────────────────────────
fix_ok = fix_sk = fix_fl = 0
fix_ignored = 0
fix_skipped = []
fix_failed  = []

t_fix = Transaction(doc, u"Dekel Power - Update Electrical Fixtures")
t_fix.Start()
for elem in fixtures:
    eid = str(elem.Id.IntegerValue)
    fam, typ = get_ft(elem)
    # התעלמות מטיפוסים שלא נכללים בכתב הכמויות (פתחים וכו') —
    # מנקים אקטיבית את שדות הדקל כדי למחוק ערכים שנתקעו מהרצות קודמות,
    # כך שהאלמנט ייעלם מהטבלה (המסננת לפי PARAM_CODE שאינו ריק).
    _name_l = (u"{} {}".format(fam, typ)).lower()
    if any(_kw in _name_l for _kw in IGNORE_FIXTURE_KEYWORDS):
        for _p in (PARAM_CODE, PARAM_DESC, PARAM_PRICE,
                   PARAM_CODE2, PARAM_DESC2, PARAM_PRICE2, PARAM_TOTAL,
                   PARAM_CODE3, PARAM_DESC3, PARAM_PRICE3, PARAM_QTY3):
            setp(elem, _p, u"")
        fix_ignored += 1
        continue
    try:
        c1, t1, p1, c2, t2, p2 = match_fix(elem)
        if not c1 and not c2:
            fix_sk += 1
            fix_skipped.append((eid, u"{} / {}".format(fam, typ), u"לא נמצא מיפוי"))
            continue

        ok1 = setp(elem, PARAM_CODE,  c1 or u"")
        setp(elem, PARAM_DESC,  t1 or u"")
        setp(elem, PARAM_PRICE, format_price(p1) if p1 else u"")

        # For comm types: auto-fill Slot 2 with electrical point if not already set
        if u"comm" in typ.lower() and not c2:
            c2 = u"08.019.0310"
            t2 = u"נקודה 2.5mm²"
            p2 = catalog.get(c2, {}).get(u"price")

        setp(elem, PARAM_CODE2,  c2 or u"")
        setp(elem, PARAM_DESC2,  t2 or u"")
        setp(elem, PARAM_PRICE2, format_price(p2) if p2 else u"")

        total_fix = (p1 or 0.0) + (p2 or 0.0)

        # Slot 3: comm points
        if include_comm and typ in comm_count_by_type:
            _cn  = comm_count_by_type[typ]
            _cpt = (_comm_price * _cn) if _comm_price is not None else None
            setp(elem, PARAM_CODE3,  _comm_catalog_code)
            setp(elem, PARAM_DESC3,  _comm_title)
            setp(elem, PARAM_PRICE3, format_price(_cpt) if _cpt else u"")
            setp(elem, PARAM_QTY3,   u"{}".format(_cn))
            if _cpt:
                total_fix += _cpt
        elif u"comm" in typ.lower():
            setp(elem, PARAM_CODE3,  u"")
            setp(elem, PARAM_DESC3,  u"")
            setp(elem, PARAM_PRICE3, u"")
            setp(elem, PARAM_QTY3,   u"")

        setp(elem, PARAM_TOTAL, format_price(total_fix) if total_fix else u"")

        if ok1:
            fix_ok += 1
        else:
            fix_fl += 1
            fix_failed.append((eid, u"{} / {}".format(fam, typ), u"כשל כתיבה"))
    except Exception as e:
        fix_fl += 1
        fix_failed.append((eid, u"{} / {}".format(fam, typ), u"{}".format(e)))
t_fix.Commit()
print(u"שקעים/אביזרים: {}✓  {}⚠  {}✗".format(fix_ok, fix_sk, fix_fl))
if fix_ignored:
    print(u"התעלמנו: {} פתחים/אלמנטים שלא נכללים בכתב הכמויות".format(fix_ignored))

# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULES
# Creates: Dekel_Generators, Dekel_Transformers
# Does NOT create: Dekel_ElectricalEquipment (removed per requirement 4)
# Cleans up: any legacy schedules from previous versions
# ──────────────────────────────────────────────────────────────────────────────
from Autodesk.Revit.DB import ViewSchedule, ScheduleFilter, ScheduleFilterType

SCHED_NAME_GEN    = u"Dekel_Generators"
SCHED_NAME_TRANS  = u"Dekel_Transformers"
SCHED_NAME_FIX    = u"Dekel_Fixtures"
SCHED_NAME_POINTS = u"Dekel_Points"

# Legacy names to remove if present
LEGACY_SCHEDS = [
    u"Dekel_ElectricalEquipment",
    u"Dekel_DryTransformers",
    u"Dekel_OilTransformers",
    u"Dekel_ElectricalFixtures",
]


def _delete_schedule(name):
    for s in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        if s.Name == name:
            doc.Delete(s.Id)
            break


def _add_field(sd, field_name):
    for sf in sd.GetSchedulableFields():
        if sf.GetName(doc) == field_name:
            return sd.AddField(sf)
    for sf in sd.GetSchedulableFields():
        if field_name.lower() in sf.GetName(doc).lower():
            return sd.AddField(sf)
    return None


def _finalize_sched(sd, price_param_names):
    # Price params are TEXT type \u2014 Revit cannot sum TEXT fields and raises
    # "Display of a grand total row is not enabled" when accessing grand total
    # properties. Do not enable ShowGrandTotal or touch any grand total APIs.
    pass


def create_fixtures_schedule():
    """Dekel_Fixtures schedule — all processed electrical fixtures."""
    _delete_schedule(SCHED_NAME_FIX)
    cat_id = doc.Settings.Categories.get_Item(BuiltInCategory.OST_ElectricalFixtures).Id
    sched  = ViewSchedule.CreateSchedule(doc, cat_id)
    sched.Name = SCHED_NAME_FIX
    sd = sched.Definition
    sd.IsItemized = True

    for col in [u"Family and Type", u"Level",
                PARAM_CODE, PARAM_DESC, PARAM_PRICE,
                PARAM_TOTAL]:
        _add_field(sd, col)

    try:
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if f.GetName() == PARAM_CODE:
                sd.AddFilter(ScheduleFilter(f.FieldId, ScheduleFilterType.IsNotEmpty))
                break
    except Exception: pass

    _finalize_sched(sd, {PARAM_PRICE, PARAM_TOTAL})
    print(u"טבלה נוצרה: {}".format(SCHED_NAME_FIX))
    return sched


def create_points_schedule():
    """Dekel_Points — electrical and comm connection points only.
    Columns: Family/Type | Level | elec-point code/desc/price | comm code/desc/count/price.
    Filter: PARAM_CODE2 is not empty (element has an electrical connection point).
    Sorted by PARAM_CODE2 so identical point types group together visually.
    Only created and opened when fix_ok > 0 (electrical points were actually written).
    """
    _delete_schedule(SCHED_NAME_POINTS)
    cat_id = doc.Settings.Categories.get_Item(BuiltInCategory.OST_ElectricalFixtures).Id
    sched = ViewSchedule.CreateSchedule(doc, cat_id)
    sched.Name = SCHED_NAME_POINTS
    sd = sched.Definition
    sd.IsItemized = True

    for col in [u"Family and Type", u"Level",
                PARAM_CODE2, PARAM_DESC2, PARAM_PRICE2,
                PARAM_CODE3, PARAM_DESC3, PARAM_PRICE3, PARAM_QTY3]:
        _add_field(sd, col)

    # Filter: only rows with an electrical connection point (Slot 2 not empty)
    try:
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if f.GetName() == PARAM_CODE2:
                sd.AddFilter(ScheduleFilter(f.FieldId, ScheduleFilterType.IsNotEmpty))
                break
    except Exception: pass

    # Sort by PARAM_CODE2 so same point codes group visually
    try:
        from Autodesk.Revit.DB import ScheduleSortGroupField, ScheduleSortOrder
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if f.GetName() == PARAM_CODE2:
                sd.AddSortGroupField(ScheduleSortGroupField(f.FieldId, ScheduleSortOrder.Ascending))
                break
    except Exception: pass

    _finalize_sched(sd, {PARAM_PRICE2, PARAM_PRICE3})
    print(u"טבלה נוצרה: {}".format(SCHED_NAME_POINTS))
    return sched


def create_generator_schedule():
    """
    Dekel_Generators schedule.
    Columns: Family and Type | Level | Slot1 code/desc/price | Slot2 code/desc/price | Total
    No Type Mark column (removed per requirement 5).
    Filter: PARAM_CODE contains '39.010' (generator codes only).
    """
    _delete_schedule(SCHED_NAME_GEN)
    cat_id = doc.Settings.Categories.get_Item(BuiltInCategory.OST_ElectricalEquipment).Id
    sched  = ViewSchedule.CreateSchedule(doc, cat_id)
    sched.Name = SCHED_NAME_GEN
    sd = sched.Definition
    sd.IsItemized = True

    for col in [u"Family and Type", u"Level",
                PARAM_CODE,  PARAM_DESC,  PARAM_PRICE,
                PARAM_CODE2, PARAM_DESC2, PARAM_PRICE2,
                PARAM_TOTAL]:
        _add_field(sd, col)

    # Filter: only generator rows (code starts with 39.010)
    try:
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if f.GetName() == PARAM_CODE:
                sd.AddFilter(ScheduleFilter(f.FieldId, ScheduleFilterType.Contains, u"39.010"))
                break
    except Exception: pass

    _finalize_sched(sd, {PARAM_PRICE, PARAM_PRICE2, PARAM_TOTAL})
    print(u"טבלה נוצרה: {}".format(SCHED_NAME_GEN))


def create_transformer_schedule():
    """
    Dekel_Transformers schedule — dry transformers only.
    Columns: Family and Type | Level | 4x(code/desc/price) | Total
    No Type Mark column (removed per requirement 5).
    Filter 1: Family and Type contains 'DBA Transformer - Dry' — dry only
    Filter 2: PARAM_TR_CODE is not empty — only processed elements
    """
    _delete_schedule(SCHED_NAME_TRANS)
    cat_id = doc.Settings.Categories.get_Item(BuiltInCategory.OST_ElectricalEquipment).Id
    sched  = ViewSchedule.CreateSchedule(doc, cat_id)
    sched.Name = SCHED_NAME_TRANS
    sd = sched.Definition
    sd.IsItemized = True

    for col in [u"Family and Type", u"Level",
                PARAM_TR_CODE,  PARAM_TR_DESC,  PARAM_TR_PRICE,
                PARAM_TR_CODE2, PARAM_TR_DESC2, PARAM_TR_PRICE2,
                PARAM_TR_CODE3, PARAM_TR_DESC3, PARAM_TR_PRICE3,
                PARAM_TR_CODE4, PARAM_TR_DESC4, PARAM_TR_PRICE4,
                PARAM_TR_TOTAL]:
        _add_field(sd, col)

    # Filter 1: 'DBA Transformer - Dry' in Family — excludes oil and control panels
    try:
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if u"Family" in f.GetName():
                sd.AddFilter(ScheduleFilter(
                    f.FieldId, ScheduleFilterType.Contains, u"DBA Transformer - Dry"))
                break
    except Exception: pass

    # Filter 2: PARAM_TR_CODE not empty — only rows where data was injected
    try:
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if f.GetName() == PARAM_TR_CODE:
                sd.AddFilter(ScheduleFilter(f.FieldId, ScheduleFilterType.IsNotEmpty))
                break
    except Exception: pass

    _finalize_sched(sd, {PARAM_TR_PRICE, PARAM_TR_PRICE2,
                         PARAM_TR_PRICE3, PARAM_TR_PRICE4, PARAM_TR_TOTAL})
    print(u"טבלה נוצרה: {}".format(SCHED_NAME_TRANS))


fix_sched = None
points_sched = None
try:
    t_sched = Transaction(doc, u"Dekel Power - Create Schedules")
    t_sched.Start()
    # Remove legacy/unwanted schedules first
    for _ls in LEGACY_SCHEDS:
        _delete_schedule(_ls)
    create_generator_schedule()
    create_transformer_schedule()
    fix_sched = create_fixtures_schedule()
    # Dekel_Points — only when electrical points were actually written
    if fix_ok > 0:
        points_sched = create_points_schedule()
    t_sched.Commit()
except Exception as e:
    try: t_sched.RollBack()
    except: pass
    print(u"שגיאה ביצירת טבלאות: {}".format(e))

# ──────────────────────────────────────────────────────────────────────────────
# DRAFTING VIEW — הנחיות כתב כמויות (מבט משותף לכל כלי הדקל, ראה
# lib/easybim/dekel_bq_notes.py — כל כלי בונה מחדש את אותו מבט מאותה רשימת
# הערות, כדי שההרצה של כלי אחד לא תמחק את ההנחיות של כלי אחר)
# ──────────────────────────────────────────────────────────────────────────────
from easybim.dekel_bq_notes import create_bq_drafting_view, BQ_VIEW_NAME

bq_view = None
try:
    t_bq = Transaction(doc, u"Dekel Power - BQ Notes View")
    t_bq.Start()
    bq_view = create_bq_drafting_view(doc)
    t_bq.Commit()
    print(u"מבט הנחיות נוצר: {}".format(BQ_VIEW_NAME))
except Exception as e:
    try: t_bq.RollBack()
    except: pass
    print(u"[!] שגיאה ביצירת מבט הנחיות: {}".format(e))

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY DIALOG
# ──────────────────────────────────────────────────────────────────────────────
BG   = Color.White;              BG2  = Color.FromArgb(248, 249, 251)
ACC  = Color.FromArgb(37,99,235); ADIM = Color.FromArgb(42,74,138)
TDK  = Color.FromArgb(17,24,39);  TMD  = Color.FromArgb(75,85,99)
TLT  = Color.FromArgb(156,163,175)
CSUC = Color.FromArgb(5,150,105);  CSUCBG = Color.FromArgb(220,252,231)
CWRN = Color.FromArgb(217,119,6);  CWRNBG = Color.FromArgb(254,243,199)
CERR = Color.FromArgb(220,38,38);  CERRBG = Color.FromArgb(254,226,226)


def mkbtn(txt, x, y, w=140, h=38, pri=False):
    b = Button(); b.Text = txt; b.Location = Point(x,y); b.Size = Size(w,h)
    b.FlatStyle = FlatStyle.Flat; b.Cursor = System.Windows.Forms.Cursors.Hand
    if pri:
        b.BackColor = ACC; b.ForeColor = Color.White
        b.Font = Font(u"Segoe UI", 10, FontStyle.Bold); b.FlatAppearance.BorderSize = 0
    else:
        b.BackColor = BG; b.ForeColor = ACC; b.Font = Font(u"Segoe UI", 10)
        b.FlatAppearance.BorderColor = ADIM; b.FlatAppearance.BorderSize = 1
    return b


def badge(frm, x, y, val, lbl, clr, bg):
    bx = Panel(); bx.Location = Point(x,y); bx.Size = Size(130,58); bx.BackColor = bg
    lv = Label(); lv.Text = str(val); lv.Font = Font(u"Segoe UI",20,FontStyle.Bold)
    lv.ForeColor = clr; lv.BackColor = bg; lv.Location = Point(8,3); lv.Size = Size(114,30)
    ln = Label(); ln.Text = lbl; ln.Font = Font(u"Segoe UI",9)
    ln.ForeColor = TMD; ln.BackColor = bg; ln.Location = Point(8,34); ln.Size = Size(114,18)
    bx.Controls.Add(lv); bx.Controls.Add(ln); frm.Controls.Add(bx)


def sep_line(frm, y, w=446, x=22):
    s = Panel(); s.BackColor = Color.FromArgb(229,231,235)
    s.Location = Point(x,y); s.Size = Size(w,1); frm.Controls.Add(s)


def stripe(frm, w):
    p = Panel(); p.BackColor = ACC; p.Location = Point(0,0); p.Size = Size(w,3)
    frm.Controls.Add(p)


def show_details():
    frm2 = Form(); frm2.Text = u"Dekel Power \u2014 \u05e4\u05e8\u05d8\u05d9\u05dd"
    frm2.RightToLeft = WinRTL.Yes; frm2.RightToLeftLayout = True
    frm2.StartPosition = FormStartPosition.CenterScreen
    frm2.ClientSize = Size(680,520); frm2.BackColor = BG; frm2.MinimizeBox = False
    stripe(frm2, 680)
    hdr2 = Panel(); hdr2.Location = Point(0,3); hdr2.Size = Size(680,48); hdr2.BackColor = BG2
    frm2.Controls.Add(hdr2)
    lh2 = Label(); lh2.Text = u"Dekel Power \u2014 \u05e4\u05e8\u05d8\u05d9 \u05d0\u05dc\u05de\u05e0\u05d8\u05d9\u05dd"
    lh2.Font = Font(u"Segoe UI",11,FontStyle.Bold); lh2.ForeColor = TDK
    lh2.BackColor = BG2; lh2.Location = Point(18,12); lh2.Size = Size(400,24)
    hdr2.Controls.Add(lh2)
    sep_line(frm2, 51, 680, 0)
    _all_sk = skipped_details + tr_skipped + fix_skipped
    _all_fl = failed_details  + tr_failed  + fix_failed
    lbl2 = Label(); lbl2.Text = u"\u05d3\u05d5\u05dc\u05d2\u05d5: {}  |  \u05e0\u05db\u05e9\u05dc\u05d5: {}".format(
        len(_all_sk), len(_all_fl))
    lbl2.Font = Font(u"Segoe UI",12,FontStyle.Bold); lbl2.ForeColor = TDK
    lbl2.Location = Point(15,62); lbl2.Size = Size(650,26); frm2.Controls.Add(lbl2)
    hint = Label(); hint.Text = u"\u05dc\u05d7\u05e5 \u05e4\u05e2\u05de\u05d9\u05d9\u05dd \u2014 \u05d4\u05e6\u05d2 \u05d1\u05de\u05d5\u05d3\u05dc"
    hint.Font = Font(u"Segoe UI",8.5); hint.ForeColor = TLT
    hint.Location = Point(15,88); hint.Size = Size(650,18); frm2.Controls.Add(hint)
    lv2 = ListView(); lv2.View = View.Details; lv2.FullRowSelect = True; lv2.GridLines = False
    lv2.Location = Point(15,112); lv2.Size = Size(650,350); lv2.RightToLeftLayout = True
    lv2.Font = Font(u"Segoe UI",9); lv2.BackColor = BG; lv2.ForeColor = TDK
    from System.Windows.Forms import BorderStyle as BS; lv2.BorderStyle = BS.FixedSingle
    for txt, w in [(u"\u05e1\u05d8\u05d8\u05d5\u05e1",75),(u"ID",85),
                   (u"\u05d0\u05dc\u05de\u05e0\u05d8",150),(u"\u05e1\u05d9\u05d1\u05d4",325)]:
        ch2 = ColumnHeader(); ch2.Text = txt; ch2.Width = w
        ch2.TextAlign = HorizontalAlignment.Right; lv2.Columns.Add(ch2)
    for eid, name, reason in _all_sk:
        it = ListViewItem(u"\u05d3\u05d5\u05dc\u05d2"); it.ForeColor = CWRN
        it.SubItems.Add(eid); it.SubItems.Add(name); it.SubItems.Add(reason); lv2.Items.Add(it)
    for eid, name, reason in _all_fl:
        it = ListViewItem(u"\u05e0\u05db\u05e9\u05dc"); it.ForeColor = CERR
        it.SubItems.Add(eid); it.SubItems.Add(name); it.SubItems.Add(reason); lv2.Items.Add(it)
    frm2.Controls.Add(lv2)
    def zoom():
        if lv2.SelectedItems.Count == 0: return
        try: _zoom[0] = int(lv2.SelectedItems[0].SubItems[1].Text); frm2.Close()
        except: pass
    lv2.ItemActivate += lambda s, e: zoom()
    bz2 = mkbtn(u"\u05d4\u05e6\u05d2 \u05d1\u05de\u05d5\u05d3\u05dc", 15, 474, 160, 38, pri=True)
    bz2.Click += lambda s, e: zoom(); frm2.Controls.Add(bz2)
    bc2 = mkbtn(u"\u05e1\u05d2\u05d5\u05e8", 505, 474, 160, 38)
    bc2.Click += lambda s, e: frm2.Close(); frm2.Controls.Add(bc2)
    frm2.ShowDialog()


_any_issues   = any([skipped_details, failed_details, tr_skipped, tr_failed, fix_skipped, fix_failed])
total_issues  = eq_sk + eq_fl + tr_sk + tr_fl + fix_sk + fix_fl

frm = Form(); frm.Text = u"Dekel Power Tool \u2014 \u05e1\u05d9\u05db\u05d5\u05dd"
frm.RightToLeft = WinRTL.Yes; frm.RightToLeftLayout = True
frm.StartPosition = FormStartPosition.CenterScreen
frm.FormBorderStyle = FormBorderStyle.FixedSingle
frm.MaximizeBox = False; frm.MinimizeBox = False
frm.ClientSize = Size(490, 600); frm.BackColor = BG
stripe(frm, 490)
hdr = Panel(); hdr.Location = Point(0,3); hdr.Size = Size(490,48); hdr.BackColor = BG2
frm.Controls.Add(hdr)
lh = Label(); lh.Text = u"Dekel Power Tool"
lh.Font = Font(u"Segoe UI",11,FontStyle.Bold); lh.ForeColor = TDK
lh.BackColor = BG2; lh.Location = Point(18,12); lh.Size = Size(300,24); hdr.Controls.Add(lh)
sep_line(frm, 51, 490, 0)

lt = Label(); lt.Text = u"\u05d4\u05e2\u05d3\u05db\u05d5\u05df \u05d4\u05d5\u05e9\u05dc\u05dd!"
lt.Font = Font(u"Segoe UI",18,FontStyle.Bold); lt.ForeColor = TDK
lt.Location = Point(22,62); lt.Size = Size(446,36); frm.Controls.Add(lt)

lf = Label()
lf.Text = u"\u05e7\u05d1\u05e6\u05d9\u05dd \u05e9\u05e0\u05d8\u05e2\u05e0\u05d5: {} | \u05e1\u05d4\"כ \u05e1\u05e2\u05d9\u05e4\u05d9\u05dd: {}".format(
    len(selected_paths), len(catalog))
lf.Font = Font(u"Segoe UI",9); lf.ForeColor = TLT
lf.Location = Point(22,100); lf.Size = Size(446,18); frm.Controls.Add(lf)

ls = Label()
ls.Text = u"\u05d2\u05e0\u05e8\u05d8\u05d5\u05e8\u05d9\u05dd: {} | kVA map: {}".format(
    len(generators),
    u"\u2713 {}".format(len(kva_map)) if kva_map else u"\u05dc\u05d0 \u05e0\u05d8\u05e2\u05df")
ls.Font = Font(u"Segoe UI",9); ls.ForeColor = TLT
ls.Location = Point(22,118); ls.Size = Size(446,18); frm.Controls.Add(ls)
badge(frm, 22,     140, eq_ok, u"\u05d2\u05e0\u05e8\u05d8\u05d5\u05e8\u05d9\u05dd \u05e2\u05d5\u05d3\u05db\u05e0\u05d5", CSUC, CSUCBG)
badge(frm, 22+152, 140, eq_sk, u"\u05d3\u05d5\u05dc\u05d2\u05d5",
      CWRN if eq_sk else TLT, CWRNBG if eq_sk else BG2)
badge(frm, 22+304, 140, eq_fl, u"\u05e0\u05db\u05e9\u05dc\u05d5",
      CERR if eq_fl else TLT, CERRBG if eq_fl else BG2)

sep_line(frm, 210)

ltr = Label()
ltr.Text = u"\u05e9\u05e0\u05d0\u05d9\u05dd \u05d9\u05d1\u05e9\u05d9\u05dd \u05d1\u05de\u05d5\u05d3\u05dc: {}".format(len(dry_transformers))
ltr.Font = Font(u"Segoe UI",9); ltr.ForeColor = TLT
ltr.Location = Point(22,216); ltr.Size = Size(446,18); frm.Controls.Add(ltr)
badge(frm, 22,     238, tr_ok, u"\u05e9\u05e0\u05d0\u05d9\u05dd \u05e2\u05d5\u05d3\u05db\u05e0\u05d5", CSUC, CSUCBG)
badge(frm, 22+152, 238, tr_sk, u"\u05d3\u05d5\u05dc\u05d2\u05d5",
      CWRN if tr_sk else TLT, CWRNBG if tr_sk else BG2)
badge(frm, 22+304, 238, tr_fl, u"\u05e0\u05db\u05e9\u05dc\u05d5",
      CERR if tr_fl else TLT, CERRBG if tr_fl else BG2)

sep_line(frm, 308)

lfix = Label()
lfix.Text = u"שקעים ונקודות במודל: {}".format(len(fixtures))
lfix.Font = Font(u"Segoe UI",9); lfix.ForeColor = TLT
lfix.Location = Point(22,316); lfix.Size = Size(446,18); frm.Controls.Add(lfix)
badge(frm, 22,     334, fix_ok, u"שקעים עודכנו", CSUC, CSUCBG)
badge(frm, 22+152, 334, fix_sk, u"דולגו",
      CWRN if fix_sk else TLT, CWRNBG if fix_sk else BG2)
badge(frm, 22+304, 334, fix_fl, u"נכשלו",
      CERR if fix_fl else TLT, CERRBG if fix_fl else BG2)

lcomm = Label()
if comm_count_by_type:
    lcomm.Text = u"נקודות תקשורת: {} טיפוסים מוגדרים{}".format(
        len(comm_count_by_type),
        u" — נכללו בטבלה" if include_comm else u" — לא נכללו")
else:
    lcomm.Text = u"נקודות תקשורת: לא נמצאו במודל"
lcomm.Font = Font(u"Segoe UI",9)
lcomm.ForeColor = CSUC if (comm_count_by_type and include_comm) else TLT
lcomm.Location = Point(22,382); lcomm.Size = Size(446,18); frm.Controls.Add(lcomm)

sep_line(frm, 408)

lbq = Label()
lbq.Text = (u"מבט 'הנחיות כתב כמויות' — ✓ נוצר"
            if bq_view is not None else
            u"מבט 'הנחיות כתב כמויות' — שגיאה")
lbq.Font = Font(u"Segoe UI",9)
lbq.ForeColor = CSUC if bq_view is not None else CERR
lbq.Location = Point(22,416); lbq.Size = Size(446,18); frm.Controls.Add(lbq)

sep_line(frm, 438)

if _any_issues:
    bd = mkbtn(u"הצג פרטים ({})".format(total_issues),
               22, 452, 210, 40, pri=True)
    def on_d(s, e):
        show_details()
        if _zoom[0]: frm.Close()
    bd.Click += on_d; frm.Controls.Add(bd)

bc_close = mkbtn(u"סגור", 320, 452, 148, 40)
bc_close.Click += lambda s, e: frm.Close(); frm.Controls.Add(bc_close)

lver = Label(); lver.Text = u"Yamit Bettman  |  EasyBIM  |  v4.0"
lver.Font = Font(u"Segoe UI",8); lver.ForeColor = TLT
lver.Location = Point(22,500); lver.Size = Size(446,16); frm.Controls.Add(lver)
frm.ShowDialog()

# פתח מבט הנחיות אחרי סגירת הדיאלוג
if bq_view is not None:
    try: uidoc.ActiveView = bq_view
    except Exception: pass

# פתח טבלת שקעים/אביזרים
if fix_sched is not None:
    try: uidoc.ActiveView = fix_sched
    except Exception: pass

# פתח טבלת נקודות חשמל ותקשורת (נפתחת אחרונה — תהיה הטאב הפעיל)
if points_sched is not None:
    try: uidoc.ActiveView = points_sched
    except Exception: pass
if _zoom[0]:
    try:
        eid = ElementId(int(_zoom[0]))
        ids = CList[ElementId](); ids.Add(eid)
        uidoc.Selection.SetElementIds(ids)
        uidoc.ShowElements(eid)
    except Exception as e:
        print(u"\u05e9\u05d2\u05d9\u05d0\u05d4 \u05d1\u05d4\u05e6\u05d2\u05d4: {}".format(e))
