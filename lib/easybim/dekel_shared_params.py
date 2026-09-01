# -*- coding: utf-8 -*-
"""
Dekel Shared Parameters — מודול משותף לכל כלי DBA (Cable Trays, Power וכו').

מטרה: לפתור התנגשות בין כפתורים שמשתמשים באותם פרמטרים (כגון 'מספר סעיף דקל').
לפני התיקון כל כלי יצר את הפרמטר עם GUID אקראי וקובץ Shared Parameters זמני,
ולכן הכלים דרסו זה את ה-binding של זה. כאן:

  1. כל פרמטר מקבל GUID *קבוע* (ראה PARAM_GUIDS) — Revit מזהה Shared Parameter
     לפי GUID, אז GUID יציב = אותו פרמטר לוגי בכל הכלים ובכל הריצות.
  2. נעשה שימוש בקובץ Shared Parameters *קבוע* (לא זמני) משותף לכל הכלים.
  3. הקישור (binding) *ממזג* קטגוריות במקום לדרוס: אם הפרמטר כבר מקושר
     לקטגוריות של כלי אחר — הקטגוריות החדשות מתווספות אליהן.

שימוש מתוך כלי:

    from easybim.dekel_shared_params import ensure_dekel_params
    from Autodesk.Revit.DB import BuiltInCategory, Transaction

    PARAM_DEFS = [(u"מספר סעיף דקל", u"TEXT"), ...]
    CATEGORIES = [BuiltInCategory.OST_CableTray, BuiltInCategory.OST_CableTrayFitting]

    t = Transaction(doc, u"Dekel - Ensure Params")
    t.Start()
    report = ensure_dekel_params(doc, PARAM_DEFS, CATEGORIES)
    t.Commit()

הערה: יש לקרוא לפונקציה בתוך Transaction פתוח (כמו בדוגמה).
"""

import os
import codecs

import System
from Autodesk.Revit.DB import (
    CategorySet, InstanceBinding,
)

# ---------------------------------------------------------------------------
# קבוצת ברירת המחדל לפרמטר (תואם לשתי גרסאות ה-API)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# GUID-ים קבועים לכל פרמטר Dekel. *אסור לשנות* ערכים קיימים — שינוי GUID
# יוצר פרמטר חדש ומנתק את הקיים. להוספת פרמטר חדש: הוסף שורה עם GUID חדש.
# הטבלה כוללת את כל הפרמטרים של כל הכלים, כדי שכולם יחלקו מקור אמת אחד.
# ---------------------------------------------------------------------------
PARAM_GUIDS = {
    # ── משותפים (Cable Trays + Power) ─────────────────────────────────────
    u"מספר סעיף דקל":            u"c4fb5551-28ed-4a98-a5f5-df72de63ae9e",
    u"תיאור סעיף דקל":           u"1bdf7968-474f-4e98-a7fe-7cb8f18beb6a",
    u"מחיר דקל":                 u"64c61f19-e177-4e7b-9c97-2e01ee874a4c",
    # ── Cable Trays בלבד ──────────────────────────────────────────────────
    u"מספר סעיף תוספת דקל":      u"4114935e-468d-43f3-92fb-4cb4536d247d",
    u"תיאור סעיף תוספת דקל":     u"eea13f52-1465-4234-87fd-e3044f90c0a9",
    u"מחיר תוספת דקל":           u"73b036df-7f27-4135-be7f-cf42872c9419",
    u"Total Price Cable Trays":  u"36840709-1e61-419e-81ce-cfcc93deefa0",
    # ── Power: גנרטורים ───────────────────────────────────────────────────
    u"מספר סעיף דקל 2":          u"de46120f-2ed9-42e2-b4cb-fbf3b5c13930",
    u"תיאור סעיף דקל 2":         u"faaa4921-b1ef-486c-a692-b263ed46189a",
    u"מחיר דקל 2":               u"ead0b3aa-746c-4a09-a575-053a8148d792",
    u"סה\"כ לגנרטור":            u"c77a9252-49a5-4dcf-8a87-a79ddfc09626",
    # ── Power: שנאים יבשים (4 סלוטים) ────────────────────────────────────
    u"מספר סעיף דקל שנאי":       u"cb392ed6-2039-4c3d-861f-2d1e3e4b3dba",
    u"תיאור סעיף דקל שנאי":      u"77ec4b2f-ad74-4cf2-a6f0-8c161ed68959",
    u"מחיר דקל שנאי":            u"124e1755-1512-4f03-946a-44d2994df90f",
    u"מספר סעיף דקל שנאי 2":     u"6e3dac6d-57f5-4732-8319-0e821117babe",
    u"תיאור סעיף דקל שנאי 2":    u"1e03194d-2313-4ce2-a997-c74d0b31e0c6",
    u"מחיר דקל שנאי 2":          u"97806122-3278-4307-930b-bda169f17a22",
    u"מספר סעיף דקל שנאי 3":     u"068844ec-1e65-4a65-984a-f211a33c933e",
    u"תיאור סעיף דקל שנאי 3":    u"7d387be7-6ae0-4893-b524-5dd2d06fd30b",
    u"מחיר דקל שנאי 3":          u"8b88a51b-5688-4798-8b6c-afe9abbabffc",
    u"מספר סעיף דקל שנאי 4":     u"3a3e3a4b-a2fc-4a7d-bd9e-7a17cf3891ab",
    u"תיאור סעיף דקל שנאי 4":    u"bf6ecd39-e0be-49d9-babe-141907f82bfd",
    u"מחיר דקל שנאי 4":          u"87b521ab-b5d1-4327-8368-6ac65c582ad5",
    u"סה\"כ לשנאי":              u"dde5993c-edec-46aa-b56f-6c40000deb1a",
    # ── Power: סלוט 3 — נקודות תקשורת לתחנות עבודה ──────────────────────────
    u"מספר סעיף דקל 3":          u"d2d11e58-2614-4ce3-8cb7-65ddfadb08ab",
    u"תיאור סעיף דקל 3":         u"c99e54c5-261c-47b9-a5ed-1b38eac71e95",
    u"מחיר דקל 3":               u"79876361-3e7f-49dc-8f1a-8399e338132c",
    u"כמות 3":                   u"a221fdbc-f50c-4c7f-9e64-76537e24811f",
}

# מיפוי טיפוס הנתונים בקובץ ל-DATATYPE של Revit Shared Parameters
_DATATYPE_MAP = {
    u"TEXT":    u"TEXT",
    u"NUMBER":  u"NUMBER",
    u"INTEGER": u"INTEGER",
}

# נתיב קבוע לקובץ Shared Parameters המשותף (ב-AppData\Roaming של המשתמש)
def shared_param_file_path():
    appdata = str(System.Environment.GetFolderPath(
        System.Environment.SpecialFolder.ApplicationData))
    return os.path.join(appdata, u"DekelSharedParams.txt")


def _write_spf(path, specs):
    """כותב/מעדכן את קובץ ה-SPF הקבוע עם GUID-ים קבועים מ-PARAM_GUIDS."""
    header = (
        u"# Revit Shared Parameters\n"
        u"*META\tVERSION\tMINVERSION\n"
        u"META\t2\t1\n"
        u"*GROUP\tID\tNAME\n"
        u"GROUP\t1\tDekel\n"
        u"*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\t"
        u"DESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUEISSHOWN\n"
    )
    lines = u""
    for name, dtype in specs:
        guid = PARAM_GUIDS.get(name)
        if not guid:
            # אם פרמטר לא רשום בטבלה — דלג בהדפסת אזהרה (מונע GUID אקראי)
            print(u"  [אזהרה] '{}' חסר GUID קבוע ב-PARAM_GUIDS — דולג".format(name))
            continue
        dt = _DATATYPE_MAP.get(dtype, u"TEXT")
        lines += u"PARAM\t{}\t{}\t{}\t\t1\t1\t\t1\t0\n".format(guid, name, dt)
    # Revit דורש UTF-16
    with codecs.open(path, "w", encoding="utf-16") as f:
        f.write(header + lines)


def _open_group(doc, path):
    doc.Application.SharedParametersFilename = path
    spf = doc.Application.OpenSharedParameterFile()
    if not spf:
        raise Exception(u"לא ניתן לפתוח קובץ Shared Parameters: {}".format(path))
    grp = spf.Groups.get_Item(u"Dekel")
    if not grp:
        raise Exception(u"קבוצת 'Dekel' לא נמצאה בקובץ ה-SPF")
    return grp


def _current_bindings_by_name(doc):
    """מחזיר dict: שם פרמטר -> (definition, binding) עבור כל ה-bindings הקיימים."""
    out = {}
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        defn = it.Key
        out[defn.Name] = defn
    return out


def ensure_dekel_params(doc, param_specs, categories):
    """
    מוודא שכל פרמטר ב-param_specs קיים ומקושר *לפחות* לכל הקטגוריות שב-categories,
    מבלי לפגוע בקטגוריות שכבר קושרו ע"י כלים אחרים.

    param_specs : רשימת tuples (name, dtype)  כאשר dtype הוא "TEXT"/"NUMBER"/...
    categories  : רשימת BuiltInCategory לקשר אליהן

    מחזיר dict סיכום: {"created": [...], "extended": [...], "ok": [...], "failed": [...]}
    יש לקרוא בתוך Transaction פתוח.
    """
    report = {"created": [], "extended": [], "ok": [], "failed": []}

    # קבוצת הקטגוריות שכלי זה דורש
    target_cats = []
    for bic in categories:
        c = doc.Settings.Categories.get_Item(bic)
        if c is not None:
            target_cats.append(c)

    old_spf = doc.Application.SharedParametersFilename
    path = shared_param_file_path()
    try:
        _write_spf(path, param_specs)
        grp = _open_group(doc, path)

        existing = _current_bindings_by_name(doc)

        for name, dtype in param_specs:
            try:
                defn_spf = grp.Definitions.get_Item(name)
                if not defn_spf:
                    print(u"  [אזהרה] הגדרת '{}' לא נמצאה ב-SPF".format(name))
                    report["failed"].append(name)
                    continue

                if name in existing:
                    # הפרמטר כבר מקושר — מזג קטגוריות חסרות בלבד
                    defn_existing = existing[name]
                    binding = doc.ParameterBindings.get_Item(defn_existing)
                    if binding is None:
                        report["failed"].append(name)
                        continue
                    cats = binding.Categories
                    added_any = False
                    for c in target_cats:
                        if not cats.Contains(c):
                            cats.Insert(c)
                            added_any = True
                    if added_any:
                        doc.ParameterBindings.ReInsert(
                            defn_existing, InstanceBinding(cats), _PARAM_GROUP)
                        report["extended"].append(name)
                    else:
                        report["ok"].append(name)
                else:
                    # פרמטר חדש לגמרי — צור binding עם הקטגוריות הנדרשות
                    cat_set = CategorySet()
                    for c in target_cats:
                        cat_set.Insert(c)
                    doc.ParameterBindings.Insert(
                        defn_spf, InstanceBinding(cat_set), _PARAM_GROUP)
                    report["created"].append(name)
            except Exception as ex:
                print(u"  [שגיאה] '{}': {}".format(name, ex))
                report["failed"].append(name)
    finally:
        # שחזר את קובץ ה-SPF הקודם של המשתמש; אל תמחק את הקובץ הקבוע
        doc.Application.SharedParametersFilename = old_spf or path

    return report
