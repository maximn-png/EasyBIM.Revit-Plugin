# -*- coding: utf-8 -*-
"""Shared config for "Coordination Graphics" / "Coordination Settings".

Two separate persistence stores, deliberately different locations:

  * Settings.json  -- keywords/colors/pattern names. This is a TEAM standard
    (e.g. the Hebrew concrete keyword), so it lives INSIDE the Coordination
    Graphics pushbutton folder, next to script.py -- tracked in git like any
    other file, edited locally via "Coordination Settings", shared with the
    team the normal way (commit it, open a PR) if someone tunes it.

  * link_memory.json -- which links were picked last time, per project. This
    is per-user/per-machine state, not a team standard, so it must NOT be
    committed. Written to %APPDATA%\\EasyBIM\\CoordinationGraphics\\, the same
    pattern "Project Base Points" already uses for this exact kind of "remember
    a choice per host model" persistence (see pbp_prefs.py) -- that module's
    docstring notes pyRevit's own script.get_config()/save_config() was tried
    first and reported not to reliably persist, so a plain inspectable JSON
    file under AppData is used instead. Reusing that proven approach here
    rather than re-guessing at pyRevit config internals again.
"""

import codecs
import json
import os

_LIB_DIR    = os.path.dirname(os.path.abspath(__file__))
_EXT_ROOT   = os.path.dirname(os.path.dirname(_LIB_DIR))
_GRAPHICS_DIR = os.path.join(_EXT_ROOT, u"EasyBIM.tab", u"BIM Management.panel",
                              u"Coordination Graphics.pushbutton")

SETTINGS_PATH = os.path.join(_GRAPHICS_DIR, u"Settings.json")

_MEMORY_DIR  = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                             "EasyBIM", "CoordinationGraphics")
_MEMORY_PATH = os.path.join(_MEMORY_DIR, "link_memory.json")

SETTINGS_DEFAULTS = {
    u"ArchLinkKeywords"   : [u"ARC"],
    u"StructLinkKeywords" : [u"STR"],
    u"TrafficLinkKeywords": [u"TRF", u"Traffic", u"תנועה"],
    # B15-B60 are standard Israeli/European concrete-strength grade
    # designations — a real element's Type/Family/Material name is very
    # often just the grade with no "concrete" substring at all (see
    # _type_is_concrete's MaterialClass fast-path for the same problem
    # solved a second, more authoritative way).
    u"ConcreteKeywords"   : [u"Concrete", u"בטון", u"ב-30", u"ב30",
                              u"B15", u"B20", u"B25", u"B30", u"B35",
                              u"B40", u"B45", u"B50", u"B60"],
    u"ExcludeKeywords"    : [u"Block", u"בלוק", u"Light", u"קל", u"מילוי"],
    u"StructPatternName"  : u"Diagonal Up - 1.5mm",
    u"ArchPatternName"    : u"Diagonal Down - 1.5mm",
    u"StructColor"        : {u"R": 200, u"G": 30, u"B": 30},
    u"ArchColor"          : {u"R": 0, u"G": 70, u"B": 200},
}


def _default_copy():
    out = {}
    for k, v in SETTINGS_DEFAULTS.items():
        if isinstance(v, list):
            out[k] = list(v)
        elif isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


def load_settings():
    """Defaults, overlaid with whatever Settings.json has (missing/invalid
    keys silently keep their default -- a hand-edited or older JSON file
    should never crash the tool)."""
    settings = _default_copy()
    if os.path.isfile(SETTINGS_PATH):
        try:
            with codecs.open(SETTINGS_PATH, "r", "utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    settings[k] = v
        except Exception:
            pass
    return settings


def save_settings(settings):
    d = os.path.dirname(SETTINGS_PATH)
    if not os.path.isdir(d):
        os.makedirs(d)
    with codecs.open(SETTINGS_PATH, "w", "utf-8") as f:
        f.write(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True))


def settings_file_path():
    return SETTINGS_PATH


def _model_key(doc):
    try:
        return doc.PathName or doc.Title or u"unknown"
    except Exception:
        return u"unknown"


def _load_all_memory():
    if not os.path.isfile(_MEMORY_PATH):
        return {}
    try:
        with codecs.open(_MEMORY_PATH, "r", "utf-8") as f:
            data = json.load(f)
        return data or {}
    except Exception:
        return {}


def load_link_memory(doc):
    """Remembered {'arch_uid':.., 'struct_uid':.., 'traffic_uid':.., 'use_traffic':..}
    for this host model, or {} if nothing is remembered yet."""
    return _load_all_memory().get(_model_key(doc), {}) or {}


def save_link_memory(doc, patch):
    all_mem = _load_all_memory()
    key = _model_key(doc)
    current = all_mem.get(key, {}) or {}
    current.update(patch)
    all_mem[key] = current
    if not os.path.isdir(_MEMORY_DIR):
        os.makedirs(_MEMORY_DIR)
    with codecs.open(_MEMORY_PATH, "w", "utf-8") as f:
        f.write(json.dumps(all_mem, ensure_ascii=False, indent=2))


def memory_file_path():
    return _MEMORY_PATH
