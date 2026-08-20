# -*- coding: utf-8 -*-
"""Reads the host model's and every directly-placed link's Project Base Point,
expressed in the HOST's shared coordinate system (N/S, E/W, Elev in meters;
angle-to-true-north in degrees) — matches the `PBP_ROWS` shape in
reference/ribbon-data.jsx.

Revit API pointers (README "What the command should actually do", step 1-2):
- FilteredElementCollector(doc).OfClass(RevitLinkInstance) for direct links.
  Nested links (a RevitLinkInstance inside another link's document) are
  detected and SKIPPED from the audit, only counted (see pbp_match.nested_count).
- Link -> host shared coords: combine the link's placement Transform with the
  host document's ProjectLocation / shared-site transform. The PBP shown in the
  UI is the linked model's own Project Base Point point, run through that
  transform chain — not the link instance's own basepoint.
- "Shared" state (r.shared: "Yes" / "No" / "?"): whether the link has been
  acquired/published into the host's shared coordinate system at all
  (SharedPositioning / ProjectPosition APIs). "No" -> reported as "Not Shared".
- Angle to true north: derive from the transform's rotation about Z relative
  to the host's true-north direction (ProjectLocation.GetProjectPosition or the
  site's angle-to-true-north setting).
- File-name parsing for discipline + building key: convention
  PROJECT-DISCIPLINE-FIRM-BUILDING-REVITVERSION (see PBP_ROWS sample data).
  Treat this as a best-effort default only — the UI lets the user override
  both discipline and reference per row.
"""


def read_host_base_point(doc):
    """Return a dict shaped like PBP_HOST in reference/ribbon-data.jsx:
    title, path, units, workshared, nested (skipped-link count), ns/ew/el/ang.
    """
    raise NotImplementedError


def read_link_base_points(doc):
    """Return a list of dicts shaped like PBP_ROWS rows (kind="Link"), one per
    directly-placed RevitLinkInstance. Each row: link (file name), inst
    (instance name/tag if multiple instances of the same link), disc (parsed
    discipline code), key (parsed building code), shared, site, workset,
    ns/ew/el/ang, placed (bool — False if the link is unloaded).
    """
    raise NotImplementedError
