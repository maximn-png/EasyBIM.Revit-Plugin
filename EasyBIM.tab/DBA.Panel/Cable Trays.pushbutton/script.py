# -*- coding: utf-8 -*-
"""Dekel Cable Tray Update Tool"""

__title__   = "Cable Trays"
__doc__     = ("Matches Cable Tray elements to Dekel price items "
               "and updates the model parameters.\n\n"
               "How to use:\n"
               "1. Make sure Cable Trays have a Description parameter filled in\n"
               "2. Click the button and select the Dekel Excel file\n"
               "3. The tool updates Dekel code, description and unit price on each tray\n"
               "4. A schedule named Dekel_Cable Trays is created automatically")
__author__  = "Yamit Bettman"

import re
import os
import codecs
import zipfile
import xml.etree.ElementTree as ET

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("System")

from System.Windows.Forms import (
    OpenFileDialog, DialogResult,
    Form, Label, Button, ListView, ListViewItem,
    ColumnHeader, View, SortOrder, HorizontalAlignment,
    DockStyle, Padding, FormStartPosition, FormBorderStyle,
    RightToLeft as WinRTL, Panel, FlatStyle,
)
from System.Drawing import Font, FontStyle, Color, Size, Point, ContentAlignment, Pen, SolidBrush, Rectangle
import System

from Autodesk.Revit.DB import (
    Transaction,
    BuiltInCategory,
    FilteredElementCollector,
    CategorySet,
    InstanceBinding,
    ExternalDefinitionCreationOptions,
    ElementId,
)
try:
    from Autodesk.Revit.DB import BuiltInParameterGroup as _BPG
    _PARAM_GROUP = _BPG.INVALID
except (ImportError, AttributeError):
    from Autodesk.Revit.DB import GroupTypeId as _GTI
    _PARAM_GROUP = next(
        (getattr(_GTI, n) for n in ('Invalid', 'Other', 'General', 'Data')
         if getattr(_GTI, n, None) is not None),
        None
    )
from Autodesk.Revit.UI import TaskDialog
from System.Collections.Generic import List as CList
from pyrevit import revit

doc   = revit.doc
uidoc = revit.uidoc

# ============================================================================
# CONFIGURATION
# ============================================================================
DESCRIPTION_MAP = {
    u"פח":          (u"08.023.0040", u""),
    u"פח מחורץ":    (u"08.023.0665", u""),
    u"רשת":         (u"08.023.0120", u""),
    u"סולם":        (u"08.024.0013", u""),
    u"פלסטיק":      (u"08.023.0280", u"x1.304"),
    u"אלומניום":    (None,           u"BUSBAR"),
    u"אלומיניום":   (None,           u"BUSBAR"),
    # אין בדקל סעיף לתעלה חסינת אש. הקוד הבסיסי (08.023.0040, ברירת מחדל
    # כשאין Width/Height בפועל) נבחר בהתאם למידות התעלה מטבלת sheet_plain
    # (ראה TRAY_CATALOG/DESCRIPTION_CATALOG_KEY למעלה), כפול מקדם
    # אקסטרפולציה — ראה FIRE_RESISTANT_FACTOR למטה.
    u"חסין אש":     (u"08.023.0040", u"x3.0 (אמדן)"),
}
# דפוס Description של פס צבירה עם אמפר, למשל: '1000A - פ"צ'
BUSBAR_DESC_PATTERN = re.compile(r"^(\d+)\s*A?\s*[-–]\s*פ", re.UNICODE)
PLASTIC_FACTOR = 1.304

# מקדם אקסטרפולציה למחיר תעלה חסינת אש (לא קיים סעיף מקביל בדקל).
# מבוסס על היחס העקבי (~2.95x) בדקל עצמו בין תעלת רשת ברזל לתעלת רשת
# פלב"מ (נירוסטה) באותם רוחבים — 08.023.0100/0170, 0110/0172, 0120/0174,
# 0130/0176 — כפרוקסי ליחס מחיר "חומר/עיטוף משודרג" מול תעלה בסיסית.
# תואם גם לטווח כללי (150%-300% תוספת) של מיגון אש פסיבי לתעלות כבלים.
# *** זהו אמדן בלבד — יש לאמת מול הצעת מחיר ממתקין מיגון אש בפועל. ***
FIRE_RESISTANT_FACTOR = 3.0
FIRE_RESISTANT_CODE   = u"אמדן-חסין אש"
FIRE_RESISTANT_DESC   = (
    u"תעלת כבלים חסינת אש — מחיר משוער (אקסטרפולציה x{:.1f} על בסיס "
    u"תעלת פח דקל במידת התעלה בפועל), אינו סעיף דקל רשמי — טרם אומת מול ספק מיגון אש"
).format(FIRE_RESISTANT_FACTOR)

# BUSBAR_MAP ייבנה דינמית מתוך קובץ האקסל — ראה build_busbar_map

# ============================================================================
# התאמה לפי מידות (Width/Height בפועל של כל תעלה) — לא רק לפי חומר
# ============================================================================
# כל שורה: (רוחב מ"מ, עומק/גובה מ"מ, קוד דקל). נבנה פעם אחת מסריקת פרקי
# 08.023 (תעלות) ו-08.024 (סולמות) בקובץ דקל, ומכוסה בו — הקודים עצמם
# (מספור הסעיפים) יציבים בין גרסאות מחירון; המחירים בפועל מגיעים מ-dekel_data
# בזמן ריצה, לא מכאן. כשלרוחב/עומק נתון יש כמה עוביים באקסל, נבחר עובי 1 מ"מ
# (תואם לברירת המחדל הקודמת); אחרת העובי היחיד הקיים לאותו רוחב/עומק.
TRAY_CATALOG = {
    u"sheet_plain": [
        (60,  40,  u"08.023.0010"),
        (60,  60,  u"08.023.0012"),
        (100, 60,  u"08.023.0015"),
        (100, 100, u"08.023.0020"),
        (120, 60,  u"08.023.0025"),
        (200, 100, u"08.023.0030"),
        (300, 100, u"08.023.0040"),
        (400, 100, u"08.023.0050"),
        (500, 100, u"08.023.0054"),
        (600, 100, u"08.023.0056"),
    ],
    u"sheet_perforated": [
        (100, 60,  u"08.023.0600"),
        (100, 85,  u"08.023.0635"),
        (100, 100, u"08.023.0655"),
        (100, 110, u"08.023.1120"),
        (200, 60,  u"08.023.0610"),
        (200, 85,  u"08.023.0638"),
        (200, 100, u"08.023.0660"),
        (200, 110, u"08.023.1130"),
        (300, 60,  u"08.023.0615"),
        (300, 85,  u"08.023.0640"),
        (300, 100, u"08.023.0665"),
        (300, 110, u"08.023.1140"),
        (400, 60,  u"08.023.0620"),
        (400, 85,  u"08.023.0642"),
        (400, 100, u"08.023.0670"),
        (400, 110, u"08.023.1150"),
        (500, 60,  u"08.023.0625"),
        (500, 85,  u"08.023.0644"),
        (500, 100, u"08.023.0675"),
        (500, 110, u"08.023.1160"),
        (600, 60,  u"08.023.0630"),
        (600, 85,  u"08.023.0646"),
        (600, 100, u"08.023.0680"),
        (600, 110, u"08.023.1170"),
    ],
    u"mesh_steel": [
        (100, 85, u"08.023.0100"),
        (200, 85, u"08.023.0110"),
        (300, 85, u"08.023.0120"),
        (400, 85, u"08.023.0130"),
        (500, 85, u"08.023.0140"),
        (600, 85, u"08.023.0150"),
    ],
    u"plastic": [
        (15,  15,  u"08.023.0190"),
        (17,  17,  u"08.023.0200"),
        (25,  60,  u"08.023.0220"),
        (30,  17,  u"08.023.0202"),
        (30,  25,  u"08.023.0205"),
        (40,  25,  u"08.023.0210"),
        (40,  42,  u"08.023.0215"),
        (42,  60,  u"08.023.0230"),
        (60,  60,  u"08.023.0240"),
        (60,  70,  u"08.023.0250"),
        (100, 60,  u"08.023.0265"),
        (120, 60,  u"08.023.0270"),
        (200, 100, u"08.023.0280"),
    ],
    u"ladder_steel": [
        (100, 60,  u"08.024.0008"),
        (100, 70,  u"08.024.0040"),
        (200, 60,  u"08.024.0010"),
        (200, 70,  u"08.024.0050"),
        (200, 100, u"08.024.0100"),
        (300, 60,  u"08.024.0013"),
        (300, 70,  u"08.024.0060"),
        (300, 100, u"08.024.0110"),
        (400, 60,  u"08.024.0016"),
        (400, 70,  u"08.024.0070"),
        (400, 100, u"08.024.0120"),
        (500, 60,  u"08.024.0020"),
        (500, 70,  u"08.024.0080"),
        (500, 100, u"08.024.0125"),
        (600, 60,  u"08.024.0030"),
        (600, 70,  u"08.024.0090"),
        (600, 100, u"08.024.0130"),
    ],
}

# Description → מפתח בטבלה. "חסין אש" משתמש בטבלת הפח הרגיל (התעלה מתחת
# למיגון האש היא תעלת פח), עם מקדם ה-אקסטרפולציה שמופעל בנפרד למטה.
DESCRIPTION_CATALOG_KEY = {
    u"פח":       u"sheet_plain",
    u"פח מחורץ": u"sheet_perforated",
    u"רשת":      u"mesh_steel",
    u"סולם":     u"ladder_steel",
    u"פלסטיק":   u"plastic",
    u"חסין אש":  u"sheet_plain",
}

_DIM_TOLERANCE_MM = 2.0

def get_mm_param(elem, name):
    """קריאת פרמטר מידה (Width/Height) בפועל והמרה ממ' רגל למ"מ."""
    p = elem.LookupParameter(name)
    if not p:
        return None
    try:
        v = p.AsDouble()
    except Exception:
        return None
    if v is None:
        return None
    return v * 304.8

def pick_code_by_dimensions(catalog_key, width_mm, depth_mm):
    """מוצא בטבלה את הקוד הקרוב ביותר לרוחב/עומק בפועל.
    מחזיר (code, is_exact) — is_exact=False כשנבחרה המידה הקרובה ביותר
    ולא התאמה מדויקת (למ"מ) לרוחב ולעומק גם יחד."""
    rows = TRAY_CATALOG.get(catalog_key)
    if not rows or width_mm is None:
        return None, False
    if depth_mm is None:
        best = min(rows, key=lambda r: abs(r[0] - width_mm))
        exact = abs(best[0] - width_mm) <= _DIM_TOLERANCE_MM
    else:
        best = min(rows, key=lambda r: (abs(r[0] - width_mm), abs(r[1] - depth_mm)))
        exact = (abs(best[0] - width_mm) <= _DIM_TOLERANCE_MM
                  and abs(best[1] - depth_mm) <= _DIM_TOLERANCE_MM)
    return best[2], exact

PARAM_CODE  = u"מספר סעיף דקל"
PARAM_DESC  = u"תיאור סעיף דקל"
PARAM_PRICE = u"מחיר דקל"

PARAM_ADDON_CODE  = u"מספר סעיף תוספת דקל"
PARAM_ADDON_DESC  = u"תיאור סעיף תוספת דקל"
PARAM_ADDON_PRICE = u"מחיר תוספת דקל"

PARAM_TOTAL = u"Total Price Cable Trays"
PARAM_DEFS = [
    (PARAM_CODE,        u"TEXT"),
    (PARAM_DESC,        u"TEXT"),
    (PARAM_PRICE,       u"TEXT"),
    (PARAM_ADDON_CODE,  u"TEXT"),
    (PARAM_ADDON_DESC,  u"TEXT"),
    (PARAM_ADDON_PRICE, u"TEXT"),
    (PARAM_TOTAL,       u"TEXT"),
]

PRICE_PARAM_NAMES = {PARAM_PRICE, PARAM_ADDON_PRICE, PARAM_TOTAL}

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# ============================================================================
# SHARED PARAMETERS - בדיקה ויצירה אוטומטית
# ============================================================================
def get_existing_param_names():
    existing = set()
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        existing.add(it.Key.Name)
    return existing

def create_missing_shared_params(missing):
    spf_path = os.path.join(
        str(System.Environment.GetFolderPath(
            System.Environment.SpecialFolder.ApplicationData)),
        "DekelSharedParams_tmp.txt"
    )

    header = (
        u"# Revit Shared Parameters\n"
        u"*META\tVERSION\tMINVERSION\n"
        u"META\t2\t1\n"
        u"*GROUP\tID\tNAME\n"
        u"GROUP\t1\tDekel\n"
        u"*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUEISSHOWN\n"
    )
    param_lines = u""
    for name, dtype in missing:
        guid = str(System.Guid.NewGuid())
        param_lines += u"PARAM\t{}\t{}\t{}\t\t1\t1\t\t1\t0\n".format(guid, name, dtype)

    # Revit דורש קובץ Shared Parameters ב-UTF-16
    with codecs.open(spf_path, "w", encoding="utf-16") as f:
        f.write(header + param_lines)

    old_spf = doc.Application.SharedParametersFilename
    doc.Application.SharedParametersFilename = spf_path
    try:
        spf = doc.Application.OpenSharedParameterFile()
        if not spf:
            raise Exception(u"לא ניתן לפתוח קובץ Shared Parameters")
        grp = spf.Groups.get_Item("Dekel")
        if not grp:
            raise Exception(u"קבוצת Dekel לא נמצאה")

        cat_set = CategorySet()
        cat_set.Insert(
            doc.Settings.Categories.get_Item(BuiltInCategory.OST_CableTray))
        cat_set.Insert(
            doc.Settings.Categories.get_Item(BuiltInCategory.OST_CableTrayFitting))

        for name, dtype in missing:
            defn = grp.Definitions.get_Item(name)
            if not defn:
                print(u"  אזהרה: הגדרה '{}' לא נמצאה".format(name))
                continue
            doc.ParameterBindings.Insert(
                defn, InstanceBinding(cat_set),
                _PARAM_GROUP)   # "Other" group
            print(u"  נוצר: {}".format(name))
    finally:
        doc.Application.SharedParametersFilename = old_spf or ""
        try:
            os.remove(spf_path)
        except Exception:
            pass

def remove_old_number_bindings():
    """Remove NUMBER-type bindings for price params so they are recreated as TEXT."""
    to_remove = []
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        defn = it.Key
        if defn.Name not in PRICE_PARAM_NAMES:
            continue
        try:
            if u"Number" in str(defn.ParameterType):
                to_remove.append(defn)
        except Exception:
            try:
                if u"string" not in str(defn.GetDataType()).lower():
                    to_remove.append(defn)
            except Exception:
                pass
    removed = 0
    for defn in to_remove:
        try:
            doc.ParameterBindings.Remove(defn)
            removed += 1
        except Exception:
            pass
    return removed

t_migrate = Transaction(doc, u"Dekel - Migrate Price Params to TEXT")
t_migrate.Start()
try:
    n_migrated = remove_old_number_bindings()
    t_migrate.Commit()
    if n_migrated:
        print(u"  הומרו {} פרמטרי מחיר מ-NUMBER ל-TEXT (₪)".format(n_migrated))
except Exception as e:
    t_migrate.RollBack()
    print(u"  [אזהרה] לא ניתן להמיר פרמטרים: {}".format(e))

# בדוק ויצור פרמטרים חסרים
existing_names = get_existing_param_names()
missing_params  = [(n, t) for n, t in PARAM_DEFS if n not in existing_names]

if missing_params:
    print(u"יוצר {} פרמטרים חסרים...".format(len(missing_params)))
    t0 = Transaction(doc, u"Dekel - Create Shared Parameters")
    t0.Start()
    try:
        create_missing_shared_params(missing_params)
        t0.Commit()
        print(u"פרמטרים נוצרו!")
    except Exception as e:
        t0.RollBack()
        TaskDialog.Show("Dekel", u"שגיאה ביצירת פרמטרים:\n{}".format(e))
        import sys; sys.exit()
else:
    print(u"כל הפרמטרים קיימים.")

# --- ודא שהפרמטרים מקושרים גם ל-CableTrayFitting ---
def ensure_fitting_category():
    fitting_cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_CableTrayFitting)
    param_names = set(n for n, _ in PARAM_DEFS)

    # שלב 1: אסוף את כל ההגדרות — בלי לשנות כלום
    to_update = []
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        defn = it.Key
        if defn.Name in param_names:
            to_update.append(defn)

    # שלב 2: עדכן כל פרמטר — אחרי שהאיטרציה נגמרה
    added = 0
    for defn in to_update:
        binding = doc.ParameterBindings.get_Item(defn)
        if not binding:
            continue
        cats = binding.Categories
        cats.Insert(fitting_cat)
        new_binding = InstanceBinding(cats)
        doc.ParameterBindings.ReInsert(
            defn, new_binding,
            _PARAM_GROUP)
        added += 1

    print(u"  [DEBUG] ensure_fitting: found {} params, updated {}".format(
        len(to_update), added))
    return added

t_bind = Transaction(doc, u"Dekel - Bind Fitting Category")
t_bind.Start()
try:
    n_bound = ensure_fitting_category()
    t_bind.Commit()
    if n_bound > 0:
        print(u"קושרו {} פרמטרים ל-Cable Tray Fittings".format(n_bound))
except Exception as e:
    t_bind.RollBack()
    print(u"  אזהרה: לא ניתן לקשר פרמטרים ל-Fittings: {}".format(e))

# ============================================================================
# 1. בחירת קובץ Excel
# ============================================================================
dlg        = OpenFileDialog()
dlg.Title  = u"בחר קובץ טבלת דקל"
dlg.Filter = "Excel Files (*.xlsx)|*.xlsx"
if dlg.ShowDialog() != DialogResult.OK:
    TaskDialog.Show("Dekel", u"לא נבחר קובץ.")
    import sys; sys.exit()

excel_path = dlg.FileName
print(u"קובץ: {}".format(excel_path))

# ============================================================================
# 2. קריאת Excel
# ============================================================================
def col_index(ref):
    m = re.match(r"([A-Za-z]+)", ref)
    if not m:
        return 0
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1

def cell_value(c, shared):
    # Inline string (type="inlineStr") — הטקסט ישירות ב-<is><t>
    is_el = c.find("{%s}is" % NS)
    if is_el is not None:
        texts = [t.text or u"" for t in is_el.iter("{%s}t" % NS)]
        return u"".join(texts)
    # Regular value
    v_el = c.find("{%s}v" % NS)
    if v_el is None:
        return None
    t = c.get("t", "")
    if t == "s":
        try:
            return shared[int(v_el.text)]
        except Exception:
            return v_el.text
    try:
        return float(v_el.text)
    except Exception:
        return v_el.text

def read_xlsx(path):
    data = {}
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter("{%s}si" % NS):
                texts = [t.text or u"" for t in si.iter("{%s}t" % NS)]
                shared.append(u"".join(texts))

        sheet_files = sorted([
            n for n in names
            if re.match(r"xl/worksheets/sheet\d+\.xml", n)
        ])
        print(u"  [DEBUG] {} גיליונות נמצאו".format(len(sheet_files)))
        for sf in sheet_files:
            count_before = len(data)
            root = ET.fromstring(z.read(sf))
            for row in root.iter("{%s}row" % NS):
                row_dict = {}
                for c in row:
                    ref = c.get("r", "")
                    row_dict[col_index(ref)] = cell_value(c, shared)
                code  = row_dict.get(0)
                title = row_dict.get(1, u"")
                price = row_dict.get(3)
                if not code:
                    continue
                # המר לטקסט — תומך גם ב-IronPython System.String
                code = u"{}".format(code)
                if not re.match(r"\d{2}\.\d{3}\.\d{4}", code):
                    continue
                data[code] = {
                    "title": u"{}".format(title or u"")[:150],
                    "price": price,
                }
            added = len(data) - count_before
            # הדפס רק גיליונות עם תוכן
            if added > 0:
                # בדוק אם זה גיליון פסי צבירה
                has_078 = any(c.startswith(u"08.078") for c in list(data.keys())[-added:])
                flag = u" *** 08.078! ***" if has_078 else u""
                print(u"    {} -> {} סעיפים{}".format(sf, added, flag))
    return data

try:
    dekel_data = read_xlsx(excel_path)
    print(u"נטענו {} סעיפים".format(len(dekel_data)))
    # Debug: הצג כמה קודים מכל קטגוריה
    busbar_codes = [c for c in dekel_data if c.startswith(u"08.078.")]
    tray_codes   = [c for c in dekel_data if c.startswith(u"08.023.")]
    print(u"  מתוכם: {} סעיפי תעלות (08.023), {} סעיפי פסי צבירה (08.078)".format(
        len(tray_codes), len(busbar_codes)))

    # אם אין 08.078 — דיבאג מפורט
    if not busbar_codes:
        print(u"  [DEBUG] חיפוש קודים שמכילים '078':")
        for c in dekel_data:
            if u"078" in str(c):
                print(u"    found: '{}' (type={})".format(c, type(c).__name__))
        # הצג קידומות ייחודיות
        prefixes = set()
        for c in dekel_data:
            parts = str(c).split(u".")
            if len(parts) >= 2:
                prefixes.add(u"{}.{}".format(parts[0], parts[1]))
        print(u"  [DEBUG] קידומות שנמצאו: {}".format(
            u", ".join(sorted(prefixes)[:20])))
        # הצג 5 קודים לדוגמה
        sample = list(dekel_data.keys())[:5]
        for c in sample:
            print(u"    sample: '{}' type={}".format(repr(c), type(c).__name__))

except Exception as e:
    TaskDialog.Show("Dekel", u"שגיאה בקריאת Excel:\n{}".format(e))
    import sys; sys.exit()

# ============================================================================
# 2b. בניית מיפוי פסי צבירה + שושנות דינמית מתוך האקסל
# ============================================================================
def _extract_amps_plug(title):
    """מחלץ אמפר מסעיף PLUG-IN ראשי (4X1000A PLUG-IN)."""
    m = re.search(r"4[^\d](\d+)\s*A?\s*PLUG", title)
    return int(m.group(1)) if m else None

def _extract_amps_general(title):
    """מחלץ אמפר מכל סעיף 08.078 שמכיל 4X___A (שושנה, BOLT-ON וכו')."""
    m = re.search(r"4[^\d](\d+)\s*A", title)
    return int(m.group(1)) if m else None

def build_busbar_maps(data):
    """סורק 08.078.* — ראשיים = PLUG-IN, תוספות = שושנה, ברכים = ELBOW."""
    main_map    = {}   # אמפר → קוד PLUG-IN ראשי
    addon_map   = {}   # אמפר → קוד שושנה
    h_elbow_map = {}   # אמפר → קוד HORIZONTAL ELBOW
    v_elbow_map = {}   # אמפר → קוד VERTICAL ELBOW
    count_078 = 0
    for code, info in data.items():
        if not code.startswith(u"08.078."):
            continue
        count_078 += 1
        title = info.get("title", u"")

        # --- סעיף ראשי: PLUG-IN (בלי "תוספת") ---
        if u"PLUG" in title and u"תוספת" not in title:
            amps = _extract_amps_plug(title)
            if amps:
                main_map[amps] = code

        # --- תוספת שושנה ---
        elif u"שושנ" in title:
            amps = _extract_amps_general(title)
            if amps:
                addon_map[amps] = code

        # --- ברך אופקית (HORIZONTAL ELBOW) ---
        elif u"HORIZONTAL" in title:
            amps = _extract_amps_general(title)
            if amps:
                h_elbow_map[amps] = code

        # --- ברך אנכית (VERTICAL ELBOW) ---
        elif u"VERTICAL" in title:
            amps = _extract_amps_general(title)
            if amps:
                v_elbow_map[amps] = code

    print(u"  [DEBUG] סעיפי 08.078 באקסל: {}".format(count_078))
    return main_map, addon_map, h_elbow_map, v_elbow_map

BUSBAR_MAP, BUSBAR_ADDON_MAP, BUSBAR_H_ELBOW, BUSBAR_V_ELBOW = build_busbar_maps(dekel_data)

if BUSBAR_MAP:
    print(u"פסי צבירה: {} אמפרים — {}".format(
        len(BUSBAR_MAP),
        u", ".join(u"{}A".format(a) for a in sorted(BUSBAR_MAP.keys()))
    ))
if BUSBAR_ADDON_MAP:
    print(u"שושנות פסי צבירה: {} אמפרים — {}".format(
        len(BUSBAR_ADDON_MAP),
        u", ".join(u"{}A".format(a) for a in sorted(BUSBAR_ADDON_MAP.keys()))
    ))
if BUSBAR_H_ELBOW:
    print(u"ברכים אופקיות: {} אמפרים — {}".format(
        len(BUSBAR_H_ELBOW),
        u", ".join(u"{}A".format(a) for a in sorted(BUSBAR_H_ELBOW.keys()))
    ))
if BUSBAR_V_ELBOW:
    print(u"ברכים אנכיות: {} אמפרים — {}".format(
        len(BUSBAR_V_ELBOW),
        u", ".join(u"{}A".format(a) for a in sorted(BUSBAR_V_ELBOW.keys()))
    ))
if not BUSBAR_MAP:
    print(u"  [!] אזהרה: BUSBAR_MAP ריק — פסי צבירה לא ימופו")
    # Debug: הצג דוגמת סעיפי 08.078 אם יש
    samples = [(c, d["title"]) for c, d in dekel_data.items() if c.startswith(u"08.078.")][:5]
    for c, t in samples:
        print(u"    {} : {}".format(c, t[:60]))

# ============================================================================
# 3. עדכון תעלות
# ============================================================================
print(u"  [!] מחיר תעלות 'חסין אש' הוא אמדן (x{:.1f} על בסיס תעלת פח) — "
      u"אין סעיף מקביל בדקל. יש לאמת מול ספק מיגון אש.".format(FIRE_RESISTANT_FACTOR))

trays = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_CableTray)
    .WhereElementIsNotElementType()
    .ToElements()
)
print(u"נמצאו {} תעלות".format(len(trays)))

if not trays:
    TaskDialog.Show("Dekel", u"לא נמצאו תעלות בפרויקט.")
    import sys; sys.exit()

updated = skipped = failed = 0
dim_exact_count = dim_approx_count = dim_fallback_count = 0
grand_total = 0.0
skipped_details = []   # [(element_id, description, reason), ...]
failed_details  = []   # [(element_id, description, reason), ...]

t = Transaction(doc, "Dekel - Update Cable Trays")
t.Start()

for tray in trays:
    try:
        tray_id = str(tray.Id.IntegerValue)

        # קרא Description מהאינסטנס או מה-Type
        p    = tray.LookupParameter("Description")
        desc = (p.AsString() or u"").strip() if p else u""
        if not desc:
            etype = doc.GetElement(tray.GetTypeId())
            if etype:
                p    = etype.LookupParameter("Description")
                desc = (p.AsString() or u"").strip() if p else u""
        if not desc:
            skipped += 1
            skipped_details.append((tray_id, u"---", u"אין ערך ב-Description"))
            continue

        mapping = DESCRIPTION_MAP.get(desc)

        # אם לא נמצא ב-MAP, בדוק אם זה דפוס פס צבירה כמו '1000A - פ"צ'
        if not mapping:
            bp = BUSBAR_DESC_PATTERN.match(desc)
            if bp:
                mapping = (None, u"BUSBAR_FROM_DESC")
            else:
                skipped += 1
                skipped_details.append((tray_id, desc, u"Description לא מוכר במיפוי"))
                continue

        code, note = mapping
        addon_code = None

        # --- התאמה לפי מידות בפועל (Width/Height), במקום קוד קבוע לפי חומר ---
        catalog_key = DESCRIPTION_CATALOG_KEY.get(desc)
        if catalog_key:
            width_mm = get_mm_param(tray, "Width")
            depth_mm = get_mm_param(tray, "Height")
            dim_code, dim_exact = pick_code_by_dimensions(catalog_key, width_mm, depth_mm)
            if dim_code:
                code = dim_code
                if dim_exact:
                    dim_exact_count += 1
                else:
                    dim_approx_count += 1
            else:
                dim_fallback_count += 1

        # --- פסי צבירה: חלץ אמפר מ-MARK או מ-Description ---
        if note in (u"BUSBAR", u"BUSBAR_FROM_DESC"):
            amps = None

            # אם האמפר כבר ב-Description (למשל '1000A - פ"צ')
            if note == u"BUSBAR_FROM_DESC":
                bp = BUSBAR_DESC_PATTERN.match(desc)
                if bp:
                    amps = int(bp.group(1))
            else:
                # חלץ אמפר מ-Mark
                p_mark = tray.LookupParameter("Mark")
                mark_val = (p_mark.AsString() or u"").strip() if p_mark else u""
                if not mark_val:
                    skipped += 1
                    skipped_details.append((tray_id, desc, u"פס צבירה — אין ערך ב-Mark"))
                    continue
                amp_match = re.search(r"(\d+)", mark_val)
                if amp_match:
                    amps = int(amp_match.group(1))

            if amps is None:
                skipped += 1
                skipped_details.append((tray_id, desc, u"פס צבירה — לא ניתן לחלץ אמפר"))
                continue

            code = BUSBAR_MAP.get(amps)
            if not code:
                skipped += 1
                skipped_details.append((tray_id, desc, u"פס צבירה — אמפר {} לא נמצא באקסל".format(amps)))
                continue
            addon_code = BUSBAR_ADDON_MAP.get(amps)

        elif not code:
            skipped += 1
            skipped_details.append((tray_id, desc, u"{} - {}".format(desc, note) if note else u"אין קוד דקל"))
            continue

        if code not in dekel_data:
            skipped += 1
            skipped_details.append((tray_id, desc, u"קוד {} לא נמצא בקובץ האקסל".format(code)))
            continue

        price = dekel_data[code]["price"]
        title = dekel_data[code]["title"]
        if desc == u"פלסטיק" and price:
            price = round(float(price) * PLASTIC_FACTOR, 2)
        elif desc == u"חסין אש" and price:
            # אין סעיף דקל לתעלה חסינת אש — מחיר משוער, ראה FIRE_RESISTANT_FACTOR
            price = round(float(price) * FIRE_RESISTANT_FACTOR, 2)
            code  = FIRE_RESISTANT_CODE
            title = FIRE_RESISTANT_DESC

        # מחיר תוספת (פסי צבירה בלבד)
        addon_price = 0.0
        addon_title = u""
        if addon_code and addon_code in dekel_data:
            addon_price = dekel_data[addon_code]["price"] or 0.0
            addon_title = dekel_data[addon_code]["title"] or u""

        def set_param(name, value):
            p = tray.LookupParameter(name)
            if not p:
                return False
            if p.IsReadOnly:
                return False
            try:
                st = str(p.StorageType)
                if   "String"  in st: p.Set(u"{}".format(value))
                elif "Double"  in st: p.Set(float(value))
                elif "Integer" in st: p.Set(int(float(value)))
                else: p.Set(float(value))
                return True
            except Exception:
                return False

        def set_param_fuzzy(keyword, value):
            for _p in tray.Parameters:
                try:
                    if keyword in _p.Definition.Name and not _p.IsReadOnly:
                        _p.Set(value)
                        return True
                except Exception:
                    pass
            return False

        # אורך במטרים (Revit שומר ברגל)
        p_len    = tray.LookupParameter("Length")
        length_m = round(p_len.AsDouble() * 0.3048, 4) if p_len else 0.0
        main_total  = round((float(price) if price else 0.0) * length_m, 2)
        addon_total = round(float(addon_price), 2) if addon_price else 0.0
        total       = round(main_total + addon_total, 2)

        # כתוב פרמטרים ראשיים
        ok1 = set_param(PARAM_CODE,  code)
        ok2 = set_param_fuzzy(u"תיאור", title)
        ok3 = set_param(PARAM_PRICE, u"₪{:,.2f}".format(float(price or 0)))
        ok4 = set_param(PARAM_TOTAL, u"₪{:,.2f}".format(total))

        # debug: הצג מחיר לפסי צבירה
        if note in (u"BUSBAR", u"BUSBAR_FROM_DESC"):
            print(u"  [DEBUG] {} code={} price={} ok3={}".format(
                tray_id, code, price, ok3))

        # כתוב פרמטרי תוספת (פסי צבירה בלבד)
        if addon_code:
            set_param(PARAM_ADDON_CODE,  addon_code)
            set_param(PARAM_ADDON_DESC,  addon_title)
            set_param(PARAM_ADDON_PRICE, u"₪{:,.2f}".format(float(addon_price)))
        else:
            # נקה פרמטרי תוספת אם לא רלוונטי
            set_param(PARAM_ADDON_CODE,  u"")
            set_param(PARAM_ADDON_DESC,  u"")
            set_param(PARAM_ADDON_PRICE, u"")

        if ok1 and ok2:
            updated += 1
            grand_total += total
        else:
            fail_reasons = []
            if not ok1: fail_reasons.append(u"מספר סעיף")
            if not ok2: fail_reasons.append(u"תיאור סעיף")
            if not ok3: fail_reasons.append(u"מחיר")
            if not ok4: fail_reasons.append(u"מחיר כולל")
            failed += 1
            failed_details.append((tray_id, desc, u"נכשל בעדכון: {}".format(u", ".join(fail_reasons))))

    except Exception as e:
        print(u"  שגיאה {}: {}".format(tray.Id, e))
        failed += 1
        failed_details.append((str(tray.Id.IntegerValue), u"---", u"שגיאה: {}".format(e)))

t.Commit()

print(u"התאמה לפי מידות: {} מדויקות, {} מקורבות (מידה קרובה ביותר בדקל), "
      u"{} ללא Width/Height בפועל (ברירת מחדל)".format(
          dim_exact_count, dim_approx_count, dim_fallback_count))

# ============================================================================
# 3b. עדכון Fittings של פסי צבירה (ברכים אופקיות / אנכיות)
# ============================================================================
fittings = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_CableTrayFitting)
    .WhereElementIsNotElementType()
    .ToElements()
)
print(u"נמצאו {} פיטינגים".format(len(fittings)))

fit_updated = fit_skipped = fit_failed = 0

def is_vertical_elbow(fitting):
    """בודק אם ברך אנכית — לפי הפרש Z בין ה-Connectors."""
    try:
        mgr = fitting.MEPModel.ConnectorManager
        z_vals = []
        for conn in mgr.Connectors:
            z_vals.append(round(conn.Origin.Z, 4))
        if len(z_vals) >= 2 and len(set(z_vals)) > 1:
            return True   # Z שונה = אנכי
        return False       # Z זהה = אופקי
    except Exception:
        return False

if fittings and (BUSBAR_H_ELBOW or BUSBAR_V_ELBOW):
    t_fit = Transaction(doc, "Dekel - Update Busbar Fittings")
    t_fit.Start()

    for fit in fittings:
        try:
            fit_id = str(fit.Id.IntegerValue)

            # --- לפיטינגים: זיהוי לפי Mark (אמפר) ---
            # אין צורך ב-Description — אם Mark מכיל אמפר מוכר, זה פס צבירה
            p_mark = fit.LookupParameter("Mark")
            mark_val = (p_mark.AsString() or u"").strip() if p_mark else u""
            if not mark_val:
                fit_skipped += 1
                continue

            amp_m = re.search(r"(\d+)", mark_val)
            if not amp_m:
                fit_skipped += 1
                continue

            amps = int(amp_m.group(1))
            # בדוק שהאמפר מוכר לפחות באחת מהמפות
            if amps not in BUSBAR_H_ELBOW and amps not in BUSBAR_V_ELBOW:
                fit_skipped += 1
                continue

            # זיהוי כיוון: אופקי או אנכי לפי Z של Connectors
            vertical = is_vertical_elbow(fit)
            elbow_map = BUSBAR_V_ELBOW if vertical else BUSBAR_H_ELBOW
            direction = u"אנכי" if vertical else u"אופקי"

            code = elbow_map.get(amps)
            if not code:
                fit_skipped += 1
                skipped_details.append((fit_id, u"{}A".format(amps),
                    u"פיטינג {} — אמפר {} לא נמצא".format(direction, amps)))
                continue

            if code not in dekel_data:
                fit_skipped += 1
                skipped_details.append((fit_id, u"{}A".format(amps),
                    u"פיטינג — קוד {} לא באקסל".format(code)))
                continue

            price = dekel_data[code]["price"]
            title = dekel_data[code]["title"]

            # עדכן פרמטרים
            def set_fit_param(name, value):
                p = fit.LookupParameter(name)
                if not p:
                    print(u"    [!] param '{}' NOT FOUND on fitting".format(name))
                    return False
                if p.IsReadOnly:
                    print(u"    [!] param '{}' is ReadOnly".format(name))
                    return False
                try:
                    st = str(p.StorageType)
                    if   "String"  in st: p.Set(u"{}".format(value))
                    elif "Double"  in st: p.Set(float(value))
                    elif "Integer" in st: p.Set(int(float(value)))
                    else:
                        # fallback — נסה כ-double
                        p.Set(float(value))
                    return True
                except Exception as ex:
                    print(u"    [!] param '{}' Set failed: {} (type={}, value={})".format(
                        name, ex, st, value))
                    return False

            ok1 = set_fit_param(PARAM_CODE, code)
            ok2 = set_fit_param(PARAM_DESC, title)
            ok3 = set_fit_param(PARAM_PRICE, u"₪{:,.2f}".format(float(price) if price else 0.0))
            ok4 = set_fit_param(PARAM_TOTAL, u"₪{:,.2f}".format(float(price) if price else 0.0))

            print(u"  [DEBUG FIT] id={} code={} price={} dir={} ok=({},{},{})".format(
                fit_id, code, price, direction, ok1, ok2, ok3))

            if ok1 and ok2:
                fit_updated += 1
                grand_total += float(price) if price else 0.0
            else:
                fit_failed += 1
                failed_details.append((fit_id, u"{}A".format(amps), u"פיטינג — נכשל בכתיבת פרמטרים"))

        except Exception as e:
            fit_failed += 1
            failed_details.append((str(fit.Id.IntegerValue), u"---", u"פיטינג שגיאה: {}".format(e)))

    t_fit.Commit()

print(u"פיטינגים: {} עודכנו, {} דולגו, {} נכשלו".format(fit_updated, fit_skipped, fit_failed))
updated  += fit_updated
skipped  += fit_skipped
failed   += fit_failed

# ============================================================================
# 4. סיכום — עיצוב מודרני לבן (Modern Light Theme)
# ============================================================================

_zoom_element_id = [None]

# --- ערכת צבעים מודרנית ---
BG_WHITE       = Color.White
BG_SURFACE     = Color.FromArgb(248, 249, 251)
BG_LIST        = Color.FromArgb(243, 244, 246)
ACCENT         = Color.FromArgb(37, 99, 235)
ACCENT_HOVER   = Color.FromArgb(29, 78, 216)
ACCENT_LIGHT   = Color.FromArgb(219, 234, 254)
BORDER         = Color.FromArgb(229, 231, 235)
BORDER_LIGHT   = Color.FromArgb(243, 244, 246)
TEXT_DARK      = Color.FromArgb(17, 24, 39)
TEXT_MID       = Color.FromArgb(75, 85, 99)
TEXT_LIGHT     = Color.FromArgb(156, 163, 175)
CLR_SUCCESS    = Color.FromArgb(5, 150, 105)
CLR_SUCCESS_BG = Color.FromArgb(220, 252, 231)
CLR_WARNING    = Color.FromArgb(217, 119, 6)
CLR_WARNING_BG = Color.FromArgb(254, 243, 199)
CLR_ERROR      = Color.FromArgb(220, 38, 38)
CLR_ERROR_BG   = Color.FromArgb(254, 226, 226)
HEADER_BG      = Color.FromArgb(248, 250, 252)


def _make_form(title, width, height):
    frm = Form()
    frm.Text              = title
    frm.RightToLeft       = WinRTL.Yes
    frm.RightToLeftLayout = True
    frm.StartPosition     = FormStartPosition.CenterScreen
    frm.FormBorderStyle   = FormBorderStyle.FixedSingle
    frm.MaximizeBox       = False
    frm.MinimizeBox       = False
    frm.ClientSize        = Size(width, height)
    frm.BackColor         = BG_WHITE
    frm.ForeColor         = TEXT_DARK
    frm.Font              = Font(u"Segoe UI", 10)
    return frm


def _accent_stripe(parent, y, width):
    stripe = Panel()
    stripe.BackColor = ACCENT
    stripe.Location  = Point(0, y)
    stripe.Size      = Size(width, 3)
    parent.Controls.Add(stripe)


def _separator(parent, y, width, margin=20):
    sep = Panel()
    sep.BackColor = BORDER
    sep.Location  = Point(margin, y)
    sep.Size      = Size(width - margin * 2, 1)
    parent.Controls.Add(sep)


def _btn(text, x, y, w=140, h=38, primary=False):
    btn = Button()
    btn.Text      = text
    btn.Location  = Point(x, y)
    btn.Size      = Size(w, h)
    btn.FlatStyle = FlatStyle.Flat
    btn.Cursor    = System.Windows.Forms.Cursors.Hand
    if primary:
        btn.BackColor = ACCENT
        btn.ForeColor = Color.White
        btn.Font      = Font(u"Segoe UI", 10, FontStyle.Bold)
        btn.FlatAppearance.BorderSize = 0
    else:
        btn.BackColor = BG_WHITE
        btn.ForeColor = TEXT_MID
        btn.Font      = Font(u"Segoe UI", 10)
        btn.FlatAppearance.BorderColor = BORDER
        btn.FlatAppearance.BorderSize  = 1
    return btn


def _stat_badge(parent, x, y, value, label, clr, bg):
    """תיבת סטטיסטיקה מודרנית — מספר גדול + תווית קטנה."""
    box = Panel()
    box.Location  = Point(x, y)
    box.Size      = Size(145, 62)
    box.BackColor = bg

    lbl_val = Label()
    lbl_val.Text      = str(value)
    lbl_val.Font      = Font(u"Segoe UI", 20, FontStyle.Bold)
    lbl_val.ForeColor = clr
    lbl_val.BackColor = bg
    lbl_val.Location  = Point(10, 4)
    lbl_val.Size      = Size(125, 30)
    box.Controls.Add(lbl_val)

    lbl_name = Label()
    lbl_name.Text      = label
    lbl_name.Font      = Font(u"Segoe UI", 9)
    lbl_name.ForeColor = TEXT_MID
    lbl_name.BackColor = bg
    lbl_name.Location  = Point(10, 36)
    lbl_name.Size      = Size(125, 18)
    box.Controls.Add(lbl_name)

    parent.Controls.Add(box)


def show_summary_dialog(total, updated, skipped, failed,
                        skipped_details, failed_details, grand_total=0.0):

    frm = _make_form(u"Dekel Tool", 420, 365)

    # פס צבע עליון
    _accent_stripe(frm, 0, 420)

    # כותרת Header
    header = Panel()
    header.Location  = Point(0, 3)
    header.Size      = Size(420, 52)
    header.BackColor = HEADER_BG
    frm.Controls.Add(header)

    lbl_top = Label()
    lbl_top.Text      = u"Dekel Tool"
    lbl_top.Font      = Font(u"Segoe UI", 11, FontStyle.Bold)
    lbl_top.ForeColor = TEXT_DARK
    lbl_top.BackColor = HEADER_BG
    lbl_top.Location  = Point(18, 14)
    lbl_top.Size      = Size(200, 24)
    header.Controls.Add(lbl_top)

    _separator(frm, 55, 420, 0)

    # כותרת ראשית
    lbl_title = Label()
    lbl_title.Text      = u"העדכון הושלם!"
    lbl_title.Font      = Font(u"Segoe UI", 18, FontStyle.Bold)
    lbl_title.ForeColor = TEXT_DARK
    lbl_title.Location  = Point(22, 68)
    lbl_title.Size      = Size(380, 36)
    frm.Controls.Add(lbl_title)

    lbl_sub = Label()
    lbl_sub.Text      = u'סה"כ {} תעלות עובדו'.format(total)
    lbl_sub.Font      = Font(u"Segoe UI", 9)
    lbl_sub.ForeColor = TEXT_LIGHT
    lbl_sub.Location  = Point(22, 104)
    lbl_sub.Size      = Size(380, 18)
    frm.Controls.Add(lbl_sub)

    lbl_total = Label()
    lbl_total.Text      = u"סכום כולל: ₪ {:,.2f}".format(grand_total)
    lbl_total.Font      = Font(u"Segoe UI", 13, FontStyle.Bold)
    lbl_total.ForeColor = ACCENT
    lbl_total.Location  = Point(22, 126)
    lbl_total.Size      = Size(380, 28)
    frm.Controls.Add(lbl_total)

    # --- תיבות סטטיסטיקה ---
    bx = 22
    by = 168
    _stat_badge(frm, bx,       by, updated, u"עודכנו",  CLR_SUCCESS, CLR_SUCCESS_BG)
    _stat_badge(frm, bx + 155, by, skipped, u"דולגו",   CLR_WARNING, CLR_WARNING_BG)
    _stat_badge(frm, bx + 310 - 155, by, failed,  u"נכשלו",   CLR_ERROR,   CLR_ERROR_BG)

    _separator(frm, 246, 420, 22)

    # כפתורים
    btn_y = 262
    if skipped_details or failed_details:
        btn_details = _btn(
            u"הצג פרטים ({})".format(len(skipped_details) + len(failed_details)),
            22, btn_y, 180, 40, primary=True)

        def on_details(sender, args):
            show_details_dialog(skipped_details, failed_details)
            if _zoom_element_id[0] is not None:
                frm.Close()

        btn_details.Click += on_details
        frm.Controls.Add(btn_details)

    btn_close = _btn(u"סגור", 218, btn_y, 180, 40, primary=False)
    btn_close.Click += lambda s, e: frm.Close()
    frm.Controls.Add(btn_close)

    # גרסה
    lbl_ver = Label()
    lbl_ver.Text      = u"Yamit Bettman  |  v2.0"
    lbl_ver.Font      = Font(u"Segoe UI", 8)
    lbl_ver.ForeColor = TEXT_LIGHT
    lbl_ver.Location  = Point(22, 316)
    lbl_ver.Size      = Size(380, 16)
    frm.Controls.Add(lbl_ver)

    frm.ShowDialog()


def show_details_dialog(skipped_details, failed_details):

    frm = _make_form(u"פרטי תעלות שדולגו / נכשלו", 680, 530)

    # פס צבע עליון
    _accent_stripe(frm, 0, 680)

    # Header
    header = Panel()
    header.Location  = Point(0, 3)
    header.Size      = Size(680, 52)
    header.BackColor = HEADER_BG
    frm.Controls.Add(header)

    lbl_top = Label()
    lbl_top.Text      = u"Dekel Tool — פרטים"
    lbl_top.Font      = Font(u"Segoe UI", 11, FontStyle.Bold)
    lbl_top.ForeColor = TEXT_DARK
    lbl_top.BackColor = HEADER_BG
    lbl_top.Location  = Point(18, 14)
    lbl_top.Size      = Size(350, 24)
    header.Controls.Add(lbl_top)

    _separator(frm, 55, 680, 0)

    # כותרת
    lbl = Label()
    lbl.Text      = u"תעלות שדולגו: {}   |   תעלות שנכשלו: {}".format(
                        len(skipped_details), len(failed_details))
    lbl.Font      = Font(u"Segoe UI", 12, FontStyle.Bold)
    lbl.ForeColor = TEXT_DARK
    lbl.Location  = Point(18, 66)
    lbl.Size      = Size(644, 26)
    frm.Controls.Add(lbl)

    # הוראה
    lbl_hint = Label()
    lbl_hint.Text      = u"לחץ פעמיים על שורה או בחר ולחץ \"הצג במודל\" לזום על האלמנט"
    lbl_hint.Font      = Font(u"Segoe UI", 8.5)
    lbl_hint.ForeColor = TEXT_LIGHT
    lbl_hint.Location  = Point(18, 92)
    lbl_hint.Size      = Size(644, 18)
    frm.Controls.Add(lbl_hint)

    # ListView מודרני
    lv = ListView()
    lv.View               = View.Details
    lv.FullRowSelect      = True
    lv.GridLines          = False
    lv.Location           = Point(18, 118)
    lv.Size               = Size(644, 350)
    lv.RightToLeftLayout  = True
    lv.Font               = Font(u"Segoe UI", 9)
    lv.BackColor          = BG_WHITE
    lv.ForeColor          = TEXT_DARK
    lv.BorderStyle        = System.Windows.Forms.BorderStyle.FixedSingle

    col_status = ColumnHeader()
    col_status.Text  = u"סטטוס"
    col_status.Width = 80
    col_status.TextAlign = HorizontalAlignment.Right

    col_id = ColumnHeader()
    col_id.Text  = u"מזהה (ID)"
    col_id.Width = 95
    col_id.TextAlign = HorizontalAlignment.Right

    col_desc = ColumnHeader()
    col_desc.Text  = u"Description"
    col_desc.Width = 130
    col_desc.TextAlign = HorizontalAlignment.Right

    col_reason = ColumnHeader()
    col_reason.Text  = u"סיבה"
    col_reason.Width = 325
    col_reason.TextAlign = HorizontalAlignment.Right

    lv.Columns.Add(col_status)
    lv.Columns.Add(col_id)
    lv.Columns.Add(col_desc)
    lv.Columns.Add(col_reason)

    for eid, desc, reason in skipped_details:
        item = ListViewItem(u"דולג")
        item.ForeColor = CLR_WARNING
        item.SubItems.Add(eid)
        item.SubItems.Add(desc)
        item.SubItems.Add(reason)
        lv.Items.Add(item)

    for eid, desc, reason in failed_details:
        item = ListViewItem(u"נכשל")
        item.ForeColor = CLR_ERROR
        item.SubItems.Add(eid)
        item.SubItems.Add(desc)
        item.SubItems.Add(reason)
        lv.Items.Add(item)

    frm.Controls.Add(lv)

    # --- פונקציה משותפת: בחר אלמנט להצגה במודל ---
    def zoom_to_selected():
        if lv.SelectedItems.Count == 0:
            return
        eid_str = lv.SelectedItems[0].SubItems[1].Text
        try:
            _zoom_element_id[0] = int(eid_str)
            frm.Close()
        except ValueError:
            pass

    lv.ItemActivate += lambda s, e: zoom_to_selected()

    # כפתורים תחתונים
    btn_zoom = _btn(u"הצג במודל", 18, 480, 160, 38, primary=True)
    btn_zoom.Click += lambda s, e: zoom_to_selected()
    frm.Controls.Add(btn_zoom)

    btn_close = _btn(u"סגור", 502, 480, 160, 38, primary=False)
    btn_close.Click += lambda s, e: frm.Close()
    frm.Controls.Add(btn_close)

    frm.ShowDialog()


msg = (
    u"עדכון הושלם!\n\n"
    u"סה\"כ אלמנטים:  {} ({} תעלות + {} פיטינגים)\n"
    u"עודכנו:        {}\n"
    u"דולגו:         {}\n"
    u"נכשלו:         {}"
).format(len(trays) + len(fittings), len(trays), len(fittings),
         updated, skipped, failed)

print(msg)

# ============================================================================
# 5. יצירת טבלה Dekel_Cable Trays
# ============================================================================
from Autodesk.Revit.DB import ViewSchedule
SCHEDULE_TRAYS   = u"Dekel_Cable Trays"
SCHEDULE_BUSBARS = u"Dekel_Busbars"
GRAND_TOTAL_TITLE = u'סה"כ'

def _delete_schedule(doc, name):
    for s in FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements():
        if s.Name == name:
            doc.Delete(s.Id)
            return

def _add_field(sd, doc, name):
    for sf in sd.GetSchedulableFields():
        if sf.GetName(doc) == name:
            return sd.AddField(sf)
    for sf in sd.GetSchedulableFields():
        if name.lower() in sf.GetName(doc).lower():
            return sd.AddField(sf)
    return None

def _set_totals_and_sort(sd, total_field_name, sort_field_name=None):
    # Price params are TEXT type — Revit cannot sum TEXT fields.
    # Grand total is shown in the summary dialog instead.
    # ShowGrandTotal defaults to False for new schedules; do not touch it or
    # ShowGrandTotalCount, because accessing count properties while grand total
    # is disabled raises "Display of a grand total row is not enabled."
    pass
    if sort_field_name:
        try:
            from Autodesk.Revit.DB import ScheduleSortGroupField, ScheduleSortOrder
            for i in range(sd.GetFieldCount()):
                f = sd.GetField(i)
                if f.GetName() == sort_field_name:
                    sg = ScheduleSortGroupField(f.FieldId)
                    sg.SortOrder  = ScheduleSortOrder.Ascending
                    sg.ShowHeader = True
                    sg.ShowFooter = True
                    sd.AddSortGroupField(sg)
                    break
        except Exception:
            pass


def create_trays_schedule(doc):
    """טבלת תעלות רגילות בלבד (ללא פסי צבירה)."""
    _delete_schedule(doc, SCHEDULE_TRAYS)
    # מחק גם טבלאות ישנות
    _delete_schedule(doc, "Dekel_Cable Trays")
    _delete_schedule(doc, "Dekel_Cable Tray Fittings")

    cat_id = doc.Settings.Categories.get_Item(BuiltInCategory.OST_CableTray).Id
    sched  = ViewSchedule.CreateSchedule(doc, cat_id)
    sched.Name = SCHEDULE_TRAYS

    sd = sched.Definition
    sd.IsItemized = True

    _add_field(sd, doc, "Type")
    _add_field(sd, doc, "Description")
    _add_field(sd, doc, "Width")
    _add_field(sd, doc, "Height")
    _add_field(sd, doc, "Length")
    _add_field(sd, doc, PARAM_CODE)
    _add_field(sd, doc, PARAM_DESC)
    _add_field(sd, doc, PARAM_PRICE)
    _add_field(sd, doc, PARAM_TOTAL)

    # סינון: רק תעלות רגילות — קוד לא מתחיל ב-08.078
    try:
        from Autodesk.Revit.DB import ScheduleFilter, ScheduleFilterType
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if PARAM_CODE in f.GetName():
                filt = ScheduleFilter(f.FieldId,
                    ScheduleFilterType.NotContains, u"08.078")
                sd.AddFilter(filt)
                # + חייב שיש סעיף
                filt2 = ScheduleFilter(f.FieldId,
                    ScheduleFilterType.IsNotEmpty)
                sd.AddFilter(filt2)
                break
    except Exception:
        pass

    _set_totals_and_sort(sd, PARAM_TOTAL, "Description")

    # --- קיבוץ לפי סעיף דקל עם ספירה ---
    try:
        from Autodesk.Revit.DB import ScheduleSortGroupField, ScheduleSortOrder
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if PARAM_CODE in f.GetName():
                sg = ScheduleSortGroupField(f.FieldId)
                sg.SortOrder   = ScheduleSortOrder.Ascending
                sg.ShowHeader  = True
                sg.ShowFooter  = True
                sg.ShowCount   = True
                sd.AddSortGroupField(sg)
                break
    except Exception:
        pass

    print(u"טבלה נוצרה: {}".format(SCHEDULE_TRAYS))


def create_busbars_schedule(doc):
    """טבלת פסי צבירה + פיטינגים (Multi-Category)."""
    _delete_schedule(doc, SCHEDULE_BUSBARS)

    # Multi-Category — כולל גם תעלות וגם פיטינגים
    sched = ViewSchedule.CreateSchedule(doc, ElementId(-1))
    sched.Name = SCHEDULE_BUSBARS

    sd = sched.Definition
    sd.IsItemized = True

    _add_field(sd, doc, "Category")
    _add_field(sd, doc, "Family and Type")
    _add_field(sd, doc, "Mark")
    _add_field(sd, doc, "Length")
    _add_field(sd, doc, PARAM_CODE)
    _add_field(sd, doc, PARAM_DESC)
    _add_field(sd, doc, PARAM_PRICE)
    _add_field(sd, doc, PARAM_ADDON_CODE)
    _add_field(sd, doc, PARAM_ADDON_DESC)
    _add_field(sd, doc, PARAM_ADDON_PRICE)
    _add_field(sd, doc, PARAM_TOTAL)

    # סינון: רק פסי צבירה — קוד מתחיל ב-08.078
    try:
        from Autodesk.Revit.DB import ScheduleFilter, ScheduleFilterType
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if PARAM_CODE in f.GetName():
                filt = ScheduleFilter(f.FieldId,
                    ScheduleFilterType.Contains, u"08.078")
                sd.AddFilter(filt)
                break
    except Exception:
        pass

    _set_totals_and_sort(sd, PARAM_TOTAL, "Category")

    # --- קיבוץ לפי סעיף דקל עם ספירה ---
    try:
        from Autodesk.Revit.DB import ScheduleSortGroupField, ScheduleSortOrder
        for i in range(sd.GetFieldCount()):
            f = sd.GetField(i)
            if PARAM_CODE in f.GetName():
                sg = ScheduleSortGroupField(f.FieldId)
                sg.SortOrder   = ScheduleSortOrder.Ascending
                sg.ShowHeader  = True
                sg.ShowFooter  = True
                sg.ShowCount   = True
                sd.AddSortGroupField(sg)
                break
    except Exception:
        pass

    print(u"טבלה נוצרה: {}".format(SCHEDULE_BUSBARS))

try:
    t_sched = Transaction(doc, "Dekel - Create Schedules")
    t_sched.Start()
    create_trays_schedule(doc)
    create_busbars_schedule(doc)
    t_sched.Commit()
except Exception as e:
    try: t_sched.RollBack()
    except Exception: pass
    print(u"שגיאה ביצירת טבלאות: {}".format(e))

# ──────────────────────────────────────────────────────────────────────────────
# DRAFTING VIEW — הנחיות כתב כמויות (מבט משותף לכל כלי הדקל, ראה
# lib/easybim/dekel_bq_notes.py — כל כלי בונה מחדש את אותו מבט מאותה רשימת
# הערות, כדי שההרצה של כלי אחד לא תמחק את ההנחיות של כלי אחר)
# ──────────────────────────────────────────────────────────────────────────────
from easybim.dekel_bq_notes import create_bq_drafting_view, BQ_VIEW_NAME

bq_view = None
try:
    t_bq = Transaction(doc, u"Dekel Cable Trays - BQ Notes View")
    t_bq.Start()
    bq_view = create_bq_drafting_view(doc)
    t_bq.Commit()
    print(u"מבט הנחיות נוצר: {}".format(BQ_VIEW_NAME))
except Exception as e:
    try: t_bq.RollBack()
    except Exception: pass
    print(u"[!] שגיאה ביצירת מבט הנחיות: {}".format(e))

show_summary_dialog(len(trays) + len(fittings), updated, skipped, failed,
                    skipped_details, failed_details, grand_total)

# פתח מבט הנחיות אחרי סגירת הדיאלוג
if bq_view is not None:
    try: uidoc.ActiveView = bq_view
    except Exception: pass

# ============================================================================
# 6. הצגת אלמנט במודל — אם המשתמש בחר שורה מהטבלה
# ============================================================================
if _zoom_element_id[0] is not None:
    try:
        eid = ElementId(int(_zoom_element_id[0]))
        # סמן את האלמנט
        id_list = CList[ElementId]()
        id_list.Add(eid)
        uidoc.Selection.SetElementIds(id_list)
        # זום לאלמנט במבט הפעיל
        uidoc.ShowElements(eid)
        print(u"מציג אלמנט {} במודל".format(_zoom_element_id[0]))
    except Exception as e:
        print(u"שגיאה בהצגת אלמנט: {}".format(e))
