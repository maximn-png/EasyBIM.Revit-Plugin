# -*- coding: utf-8 -*-
"""Pure logic, no Revit API — port directly from reference/ribbon-base-points.jsx
(`autoRefs`, `discOf`, `isAR`). Keep the AR_CODES list and building-key matching
rule identical: a non-AR link's reference is the first placed AR link sharing
its building `key`; no match -> every link with that key reports Missing Ref.
"""

AR_CODES = ["AR", "ARC", "ARCH", "A"]


def is_ar(row, disc_override):
    disc = disc_override.get(row["id"], row["disc"])
    return disc in AR_CODES and row["kind"] != "HOST"


def auto_match_references(host, links):
    """Return {link_id: ar_link_id_or_None} — see autoRefs() in
    reference/ribbon-base-points.jsx for the exact algorithm (first placed AR
    link per building key wins).
    """
    raise NotImplementedError
