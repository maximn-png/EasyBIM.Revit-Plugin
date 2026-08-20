# -*- coding: utf-8 -*-
"""Discipline code -> (hub Role, Hebrew discipline). Verbatim port of DISC_MAP in
reference/ribbon-base-points.jsx — see README "Discipline code -> Role ->
Hebrew discipline mapping" for the full table. CONFIRM HUB_ROLES against the
real EasyBIM Hub project settings before shipping (must match Format's role
names exactly for ACC assignment to succeed).
"""

HEB_OPTIONS = ["אדריכלות", "קונסטרוקציה", "אינסטלציה", "חשמל", "מיזוג אוויר", "אדריכלות נוף", "כללי"]

DISC_MAP = {
    "AR": ("Architect", "אדריכלות"), "ARC": ("Architect", "אדריכלות"),
    "ST": ("Structural Engineer", "קונסטרוקציה"),
    "ME": ("Mechanical Engineer", "מיזוג אוויר"), "EL": ("Electrical Engineer", "חשמל"),
    "PL": ("Plumbing Engineer", "אינסטלציה"), "FP": ("Fire Safety", "כללי"),
    "SN": ("Sanitation", "אינסטלציה"), "LS": ("Landscape", "אדריכלות נוף"),
    "EV": ("Elevators", "כללי"), "AC": ("Accessibility", "כללי"), "SF": ("Safety", "כללי"),
    "TR": ("Traffic Engineer", "כללי"), "QS": ("Quantity Surveyor", "כללי"),
    "IN": ("Interior Designer", "אדריכלות"), "CO": ("BIM Manager", "כללי"),
    "CE": ("Geotechnical Engineer", "קונסטרוקציה"), "SV": ("Surveyor", "כללי"),
    "A": ("Architect", "אדריכלות"), "B": ("Construction Manager", "כללי"),
    "C": ("Geotechnical Engineer", "קונסטרוקציה"), "D": ("Plumbing Engineer", "אינסטלציה"),
    "E": ("Electrical Engineer", "חשמל"), "F": ("Project Manager", "כללי"),
    "G": ("Surveyor", "כללי"), "H": ("Mechanical Engineer", "מיזוג אוויר"),
    "I": ("Interior Designer", "אדריכלות"), "K": ("Client", "כללי"),
    "L": ("Landscape", "אדריכלות נוף"), "M": ("Mechanical Engineer", "מיזוג אוויר"),
    "P": ("Plumbing Engineer", "אינסטלציה"), "Q": ("Quantity Surveyor", "כללי"),
    "S": ("Structural Engineer", "קונסטרוקציה"), "T": ("Project Manager", "כללי"),
    "W": ("Construction Manager", "כללי"), "X": ("other", "כללי"),
    "Y": ("Fire Safety", "כללי"), "Z": ("BIM Manager", "כללי"),
}

# TODO: confirm verbatim against EasyBIM Hub -> Project Settings -> Roles.
HUB_ROLES = ["3D Visualizer", "Accessibility", "Acoustical Advisor", "Agronomist", "Aluminium",
    "Architect", "BIM Manager", "Blast protection", "Client", "Communication",
    "Construction Manager", "Cranes & Gates", "Electrical Engineer", "Elevators",
    "Environmental Engineer", "Facade Designers", "Fire Safety", "Geotechnical Engineer",
    "Hydrologist", "Interior Designer", "Kitchens design", "Landscape", "Lighting Designer",
    "Mechanical Engineer", "other", "Plumbing Engineer", "Project Manager",
    "Quantity Surveyor", "Radiation", "Safety", "Sanitation", "Security",
    "Structural Engineer", "Surveyor", "Thermal advisor", "Traffic Engineer",
    "VDC Manager", "Waterproofing"]


def auto_role(code):
    return DISC_MAP.get(code, ("other", "כללי"))[0]


def auto_heb(code):
    return DISC_MAP.get(code, ("other", "כללי"))[1]
