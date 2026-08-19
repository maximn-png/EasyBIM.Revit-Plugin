# -*- coding: utf-8 -*-
"""Per-host-model preferences: remembered tolerances/options/destination
folder/ACC wizard state (README point 7, "Preferences"). No existing
persistence pattern exists elsewhere in this codebase to copy (Check Levels'
`_SESSION` dict is in-memory only and doesn't survive a Revit restart) — this
introduces the first one, confirmed with the requester.

*** REVISED APPROACH (v2) ***
The first version of this module used pyRevit's own per-script user config
(script.get_config() / script.save_config()). That was reported not actually
persisting across tool re-runs. Rather than keep guessing at pyRevit config
internals that can't be exercised without a live Revit session, this now
writes a plain JSON file to a fixed, inspectable path under the current
Windows user's AppData -- easy to verify directly (open the file, see the
JSON) independent of any pyRevit-specific behavior. Per-user/per-machine,
same as before; not shared across a team on a network drive.
"""
import codecs
import json
import os

_PREFS_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "EasyBIM", "ProjectBasePoints")
_PREFS_PATH = os.path.join(_PREFS_DIR, "prefs.json")


def _model_key(host_info):
    return host_info.get("path") or host_info.get("title") or "unknown"


def _load_all():
    if not os.path.isfile(_PREFS_PATH):
        return {}
    try:
        with codecs.open(_PREFS_PATH, "r", "utf-8") as f:
            data = json.load(f)
        return data or {}
    except Exception:
        return {}


def load_prefs(host_info):
    """Return the remembered prefs dict for this host model, or {} if none."""
    return _load_all().get(_model_key(host_info), {}) or {}


def save_prefs(host_info, patch):
    """Merge `patch` into this host model's remembered prefs and persist
    immediately to disk (not just in-memory) -- see prefs_file_path()."""
    all_prefs = _load_all()
    key = _model_key(host_info)
    current = all_prefs.get(key, {}) or {}
    current.update(patch)
    all_prefs[key] = current
    if not os.path.isdir(_PREFS_DIR):
        os.makedirs(_PREFS_DIR)
    with codecs.open(_PREFS_PATH, "w", "utf-8") as f:
        f.write(json.dumps(all_prefs, ensure_ascii=False, indent=2))


def prefs_file_path():
    """For diagnostics/support -- where prefs actually live on disk."""
    return _PREFS_PATH
