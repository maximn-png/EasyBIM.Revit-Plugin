# Head Height Check — running notes

Branch: `feature/head-height-check`. Last commit: `8bec8da` (see git log for
full history — the notes below cover work done *since* that commit, not yet
committed).

## What the tool does

For every structural floor inside a chosen scope box (read from a linked
structural model), extrude a clearance `DirectShape` upward from the floor's
actual top face — 2200mm default, or an MEP-Space-driven override height for
floors inside a "special zone" (e.g. a disabled-access path). Volumes go on
workset `+ Mass`, isolated in 3D view `EB_3D_9.Mass`, for Navisworks
clash-checking against MEP routing.

## Fixed so far (this round, uncommitted)

1. **Silent coverage gap at a ramp-to-flat transition** (original bug report,
   project 1, Floor 1615986). Root cause: when a floor's face is too complex
   for clean extrusion, it falls back to ~100+ individually-extruded triangle
   prisms; applying a zone clip/exclude to those via an *exact* boolean cut
   silently drops the cluster of triangles that straddle a mesh crease (e.g.
   the ramp/flat seam), with no warning unless *every* piece is lost.
   - Fix: `_apply_clip_by_containment` classifies whole triangle-prism pieces
     by their centroid (via `_point_inside_solid`, a vertical ray-cast against
     the zone solid) instead of cutting each one exactly at the boundary.
   - Generalized: `create_clearance_shape` now always triangulates for the
     clip/exclude step specifically (even when the un-clipped extrusion was
     clean), so every partial-overlap assignment goes through the same
     robust path, not just the ones that already needed the fallback.
   - Safety net: `_check_clip_area` compares the actual built footprint area
     against the ratio `resolve_height` already expected, logging
     `*** CLIP AREA MISMATCH ***` if they diverge — catches any future
     silent loss automatically instead of relying on someone spotting a
     visual gap.

2. **Bug in the fix above**: `_point_inside_solid`'s ray-cast relied on the
   *default* `SolidCurveIntersectionOptions` result mode, which turned out to
   be inverted — clip assignments were massively over-including (one floor
   came back at exactly 100% of its area when ~76% was expected), exclude
   assignments were under-including. Fixed by explicitly setting
   `opts.ResultType = DB.SolidCurveIntersectionMode.CurveSegmentsInside`.
   Confirmed via the `*** CLIP AREA MISMATCH ***` warnings this itself
   surfaced — validates that check was worth adding.

3. **Diagnostic-logging cleanup**: removed the temporary selection-based
   debug-target mechanism (`_DEBUG_TARGET_FLOOR_ID` etc.) and verbose
   per-floor `*** TRACE ***` / `*** COLLECT ***` / `*** DEBUGFLOOR ***`
   logging added while chasing bug #1. Downgraded routine/self-recovering
   messages (`*** BASE FALLBACK ***`, "zone clip left no overlapping
   geometry ... falling back") from `warning` to `info` so the pyRevit
   output window isn't flooded on every run. Kept everything that flags a
   genuine problem at `warning`: `*** COVERAGE GAP ***`,
   `*** CLIP AREA MISMATCH ***`, DirectShape-rejected, no-upward-face /
   no-loops skips, shape-creation exceptions, conflicts.

4. **Multi-project robustness issues found while testing on project 2**
   (same overall scale as project 1, per the user, but different behavior —
   confirms these are data/geometry-shape issues, not raw scale):
   - **Dialog froze on open**: it auto-selected + auto-analyzed the
     alphabetically-first scope box immediately on `_build()`, before the
     user touched anything — expensive, un-cancellable, no progress
     feedback. Fix: removed the auto-select; analysis now only starts when
     the user actively clicks a scope box (`_select_scope`). The rest of the
     UI already handled "nothing selected yet" gracefully.
   - **Dialog froze on scope-box selection** (after the fix above, selecting
     the actually-needed scope box still froze Revit): `_analyze_zones` (the
     special-zones preview table) was building an expensive shadow-column
     for *every* floor fragment in the whole scope box, then boolean-
     intersecting each against every zone space — O(all floors × zones),
     with the per-floor shadow column itself sometimes falling back to a
     per-triangle solid for complex geometry. Fix: added a cheap X/Y
     bounding-box pre-filter (`_footprint_bbox_xy` / `_bbox_xy_near`) so only
     floor fragments actually near a zone space get the expensive treatment;
     short-circuits entirely if there are no zone spaces in scope at all.
     **Confirmed fixed by the user** — scope-box selection no longer
     freezes.

## Currently open / unresolved — real floors getting zero upward-facing faces (project 2)

In project 2's scope box, floors 2332979, 2333886, 2334641, 2339963 (real
basement/parking slabs, ~1000-1600 sqft for the two largest) are skipped
with "no upward-facing face found". (17881426 is unrelated tiny junk
geometry, same category as project 1's 1639906/4514310 — left as-is.)

**Attempt 1 (did not work):** hypothesized a fragile single-UV-midpoint
normal sample against a genuinely non-planar top face; added an
area-weighted-normal fallback. Byte-for-byte identical warnings on re-test
proved this path was never reached — ruled out.

**Diagnostic round 2:** each floor's raw geometry is exactly one
`GeometryInstance` wrapping exactly one `Solid` with `Volume = 0.0000` —
and no associated Parts, no Join Geometry with anything (both checked
explicitly and came back empty).

**Attempt 2 (did not work, and reverted — see below):** hypothesized the
Solid, despite Volume==0, would still have usable boundary `Faces` (a
common non-manifold/open-shell situation) — relaxed `_iter_solids`'s filter
from `Volume > 0` to `Volume > 0 or Faces.Size > 0`. **User re-tested:
`Faces.Size` is ALSO 0** ("0 usable solids found (no positive volume, no
faces)") — so the Solid is genuinely, completely empty. Not a filter
problem for these 4 floors; ruled out.

**Reverted (2026-07-21):** with the relaxation proven to do nothing for the
floors it was meant to help, it was pure downside — it also let through any
OTHER Volume<=0-but-Faces>0 Solid anywhere else in this structural link
(which is confirmed to carry import/conversion-derived, occasionally
degenerate geometry). A non-manifold Solid that still has Faces is exactly
the kind of numerically-garbage geometry that can send Revit's kernel
(Triangulate / CreateExtrusionGeometry / Boolean ops) into an extremely
long or effectively-hung computation — which matches a real incident: right
after this change, a full Run on a 42-floor scope box in this same project
went unresponsive for 5-10+ minutes at ~3% CPU (confirmed via Task Manager
— not "still grinding," genuinely idle/stuck), with no hidden dialog and a
healthy BIM 360 connection ruled out as the cause. `_iter_solids` is back to
`Volume > 0` only. **User force-closed Revit, reopened, re-tested with only
this revert in place — hung again, identically (~3% CPU). So this wasn't
the (sole) cause.**

**Attempt 3 (did not work):** removed the *other* still-uncalled-out change
from diagnostic round 3: `_floor_geometry_diagnostic`'s retry of
`get_Geometry` with `Options.IncludeNonVisibleObjects = True` for any floor
with zero solids (this forces Revit to compute hidden/non-visible geometry,
which on an already-degenerate B-rep can be extremely slow or hang).
**User re-tested: hung again, identically (~3% CPU).** So this wasn't the
cause either — two suspects eliminated by direct test, not by reasoning
alone.

**Stopped guessing piecemeal — added real localization instead.** Rather
than keep removing suspects one at a time, added lightweight `logger.info`
progress lines that bracket every stage of `_refresh_scope_analysis`
(gathering footprints → analyzing zones → conflicts preview) and, inside
`_gather_floor_footprints`, one line *before* and one *after*
`extract_top_faces` for every single floor (`"[i/total] Extracting top face
for Floor <id>..."` / `"... N upward-facing face(s) found."`), plus one
before the zero-face diagnostic call. Since a frozen window still shows
whatever was last painted before it stopped responding, the last visible
line on screen (without needing to click/scroll) should identify the exact
floor + call the process is stuck inside — actual localization instead of
another guess. This is temporary/marked for removal once the hang is
resolved — not meant to stay long-term.

**User re-tested: only one line ever appeared in the output window** — the
"no upward-facing face found" warning for Floor 2339963 (full diagnostic
text included, so that floor's diagnostic definitely completed) — nothing
before or after it. Window-repaint timing is a real confound here (info
lines can be logged but not yet painted before a freeze), so "last visible
line" isn't fully reliable as a precise pointer.

**Huge incidental finding from that warning's text:** `Sketch check failed:
'FamilyInstance' object has no attribute 'SketchId'`. These "structural
floors" are not native Revit `Floor` elements at all — they're
**`FamilyInstance`** elements merely assigned to the Floors category
(`SketchId` only exists on real sketch-based elements like Floor/Wall/
RoofBase). This retroactively explains the "GeometryInstance=1,
Instance/Solid=1" signature from earlier rounds — FamilyInstance geometry
*always* comes wrapped in exactly one GeometryInstance, no mystery there —
and reframes the risk: FamilyInstance geometry queries can trigger an
on-demand regeneration of the family symbol's geometry, which for a
complex/deeply-nested family (very plausible for something converted from
IFC) can be genuinely expensive in a way native Floor elements never would
be. Every function in this file that assumes `DB.Floor`-specific behavior
should be treated as suspect for this project's link.

**Switched to a file-based trace, since the output window can't be trusted
for exact localization.** Added `_trace(msg)` / `_trace_reset()` — writes
straight to `trace.log` in this pushbutton's own folder (path:
`_TRACE_PATH = os.path.join(os.path.dirname(__file__), "trace.log")`),
flushed on every call, fully independent of Revit's UI thread or the
output window's repaint timing. Wired into every stage boundary
(`_gather_floor_footprints` start/per-link/per-floor/diagnostic,
`_analyze_zones` start/done, `_refresh_conflicts_preview` start/per-floor/
done) alongside the existing logger.info lines. `trace.log` can be read
directly off disk at any time — including while Revit is fully
unresponsive — so the next reproduction doesn't depend on window-paint
timing or the user relaying text at all.

**User reproduced and Claude read `trace.log` directly — surprising result:
the ENTIRE traced pipeline completed successfully.** `_gather_floor_footprints
DONE, 50 footprint(s)` → `_analyze_zones DONE, 0 zone(s)` →
`_refresh_conflicts_preview DONE` (all 50 floors, all resolve_height calls
finished) is the literal last line in the file — no exception, no partial
line, nothing stuck inside any of the code that was under suspicion. This
project has **31 structural links** total (only 2 of them actually
intersect this scope box; the other 29 correctly returned "0 floor(s)
collected" quickly).

Since every traced stage finished, the freeze must be in one of the four
UI-update calls in `_select_scope` that ran AFTER `_refresh_scope_analysis()`
returned and were never traced: `_update_scope_selection_ui`,
`_rebuild_zone_rows`, `_update_footer_summary`, `_update_run_enabled` — all
trivial WPF/property code with no geometry, so if the hang is genuinely in
there it would point to something Revit-side (WPF layout/paint) rather than
our logic. Added `_trace(...)` calls bracketing each of these four, plus
`_select_scope('{name}') START` / `_select_scope DONE` around the whole
method. Also added timestamps to every `_trace` line (`time.strftime`) since
line *order* alone can't reveal a long pause hidden between two adjacent
lines — only elapsed time can confirm whether something already "traced as
fine" actually took a very long time.

**Pivot: before pasting anything, user tested selecting an ARC (architectural)
link instead of STR for the actual "Run check" step, and it completed
successfully** (confirmed: they did click Run, not just preview). This
means the freeze most likely isn't in the scope-box-selection/preview path
at all (which the trace above already proved completes fine for the STR
link too) — it's in `run()`, the real DirectShape-creation step, which was
never traced. `run()` only runs after clicking "Run check", is wrapped in a
Transaction, and does much more expensive work per floor
(`create_clearance_shape` → base extrusion → the "always triangulate for
clip" step → `_apply_clip_by_containment`/`_apply_clip` →
`_filter_individually_valid` → `_try_set_shape`) than anything in the
preview path. ARC floors are presumably clean native geometry; STR floors
are confirmed to include FamilyInstance-based, partly-degenerate ones — a
real, current-suspicion-consistent explanation for why only STR hangs.

Added the same file-based tracing to `run()` (per-floor `resolve_height` +
`create_clearance_shape` calls, transaction commit boundaries, exception
path) and fine-grained tracing inside `create_clearance_shape` itself
(base extrusion attempt → clip-step triangulation →
clip/exclude application → `_filter_individually_valid` → `_try_set_shape`,
each bracketed with piece counts). `run()` also calls `_trace_reset()` at
its own start, so a fresh Run overwrites the file with just that run's
trace (separate from the scope-selection trace, no mixing).

## ROOT CAUSE FOUND AND FIXED (2026-07-21)

User re-ran with STR selected; `trace.log` stopped cold right after:
```
16:43:02  create_clearance_shape(Floor 2335381): base extrusion failed, triangulating...
16:43:03  create_clearance_shape(Floor 2335381): triangulated fallback OK, 39708 piece(s).
16:43:04  create_clearance_shape(Floor 2335381): _filter_individually_valid on 39708 piece(s)...
```

**Floor 2335381's top face (already known from the earlier preview trace to
have 9 upward-facing faces — a complex surface) fails clean extrusion and
falls back to per-triangle triangulation, producing 39,708 tiny prisms.**
`_filter_individually_valid` (pre-existing code from earlier in this
debugging thread, added for the ramp/flat fix — NOT part of this session's
recent changes) then validated each of the 39,708 pieces **one at a time**,
each via its own throwaway DirectShape create+delete. Tens of thousands of
sequential document-modification API calls is the actual hang — not
degenerate geometry causing Revit's kernel to choke, just a brute-force
O(N) validation loop hitting an N nobody anticipated being this large. This
also explains why swapping to the ARC link "ran smoothly" — ARC floors
apparently don't have anything on this scale (no faces needing the
triangulated fallback at 30k+ pieces).

**Fixed**: replaced the one-by-one loop in `_filter_individually_valid`
with `_filter_valid_batch`, a bisection approach — try the WHOLE batch as
one DirectShape first (one API call regardless of piece count); only split
in half and recurse if that fails. For an all-good (or mostly-good) batch —
the overwhelmingly common case — this drops 39,708 calls down to
essentially 1. Only pathologically bad batches (many individually-invalid
pieces scattered throughout) would still approach the old O(N) cost, which
is a legitimate edge case but far rarer than "one big face with lots of
triangles."

**CONFIRMED FIXED** — user re-ran twice: first re-run completed (created
floor elements, "took quite some time" but did finish, unlike the previous
indefinite hang); second re-run was noticeably faster than the first,
consistent with the bisection fix working as intended.

**Still worth a follow-up conversation with the user** (not fixed, just
flagged): 39,708 pieces for one floor's clearance volume is enormous even
once it stops hanging — worth discussing whether a DirectShape this dense
is actually desirable (model bloat, view/regen performance) or whether a
floor whose face triangulates this densely should be flagged as "needs
attention" rather than silently built, similar to how zero-face floors are
already flagged. Low priority since the actual blocking bug is resolved.

## Cleanup remaining (temporary diagnostic scaffolding, not yet removed)

Now that the hang is confirmed fixed, the following temporary
hang-localization additions are candidates for removal/simplification —
none of them fix anything on their own, they were purely diagnostic:
- `_trace` / `_trace_reset` / `_TRACE_PATH` (module-level, near the top) and
  every `_trace(...)` call site in `_gather_floor_footprints`,
  `_refresh_scope_analysis`/`_select_scope`, `_refresh_conflicts_preview`,
  `run()`, and `create_clearance_shape`.
- The per-floor `logger.info(...)` progress lines added to
  `_gather_floor_footprints` (`"[i/total] Extracting top face for Floor
  ..."` etc.) and the stage-boundary `logger.info` lines in
  `_refresh_scope_analysis` (`"Gathering floor footprints..."` /
  `"Analyzing special zones..."` / `"Refreshing conflicts preview..."`) —
  these predate the file-based trace and were the first (window-based)
  localization attempt; now redundant with `_trace`.
- The `_raw_geometry_object_counts` / extended `_floor_geometry_diagnostic`
  additions (PartUtils, JoinGeometryUtils, Sketch check, raw object
  inventory) are more debatable — they're genuinely informative for the
  zero-face-floor warning and not a performance risk on their own (only run
  for floors with zero faces, which is rare), so these could reasonably
  stay as permanent diagnostic value rather than being stripped. Ask the
  user which they'd prefer before removing.
- `trace.log` itself (the file) should be deleted or added to whatever this
  repo uses for ignored/scratch files, since it's regenerated on every run
  and isn't meant to be committed.

**Not yet asked/done** — should confirm with the user before stripping any
of this, similar to the earlier "Strip down to essentials" logging cleanup
this session.

## Housekeeping reminder

Nothing from this entire session (starting from commit `8bec8da`) has been
committed yet. Substantial, unrelated-seeming changes have accumulated:
the ramp/flat containment fix, the ResultType bug fix, both freeze fixes,
the resolve_height performance fix, the per-scope-box floor count feature,
the FamilyInstance/zero-face diagnostic work, and now the
_filter_individually_valid bisection fix (the actual resolution to this
whole "plugin hangs" thread) — plus a pile of temporary trace/logging code
still in the file. Worth discussing with the user whether to commit now
(possibly after stripping the temporary diagnostics first) or keep working
uncommitted a while longer.

This now points to the floor's *actual B-rep construction* having failed
inside Revit itself for these 4 floors specifically (their bounding box —
which Revit derives from sketch/parameter data, not the solid — still looks
like a normal ~2.7 ft-thick slab, but the real solid never got built) —
plausible causes: a self-intersecting/zero-net-area sketch, or these floors
originate from an import/conversion pipeline (e.g. IFC-derived) whose B-rep
conversion failed silently for these particular shapes.

**Diagnostic round 3, just added — not yet tested:** extended
`_floor_geometry_diagnostic` to check two more independent data sources
before concluding there's truly nothing to extract:
- `floor.SketchId` → `Sketch.Profile`: the floor's *original hand-drawn (or
  auto-generated) 2D boundary curves*, which exist independently of whether
  Revit ever successfully built a 3D solid from them. Reports loop count and
  total curve count, or explicitly "not sketch-based" if there's no Sketch
  at all (which would itself be a strong clue these floors were authored by
  something other than the normal floor-sketch tool).
- A second `get_Geometry` call with `Options.IncludeNonVisibleObjects =
  True` (Fine detail) — in case the "no visible objects" default was
  suppressing real geometry that only exists as non-visible/reference
  geometry.

**If Sketch.Profile comes back with real loops/curves**, the fix is to add
a fallback path: build the footprint directly from those 2D curves (shifted
to the floor's bounding-box top Z, since that's a value we already reliably
get) instead of depending on `Solid`/`Face` geometry at all for floors where
solid construction failed. This is a real, buildable fallback — not
guessed blindly — but only worth writing once the Sketch data confirms it
has something to work with.

**If Sketch.Profile is also empty/absent**, there is genuinely no boundary
data anywhere for these floors to extract via the Revit API, and the
realistic path becomes flagging them to the user as floors whose source
geometry needs fixing upstream (in the structural model / import), the same
class of "skip and flag" resolution as the tiny junk floors on project 1 —
except these are large real slabs, so that would need to be surfaced more
prominently than a silent skip (e.g. a distinct "geometry could not be
extracted, needs attention in the source model" category in the run
summary, not lumped in with routine skips).

**Next step: waiting on the user to re-select the same scope box in project
2 and paste the newest (round-3) warning text for floors 2332979, 2333886,
2334641, 2339963.** Do not guess further on the fix until that data is in.

## Processing-time note (2026-07-21)

User reported the scope-box selection in project 2 (85m × 128m box) "takes a
lot of time." It did eventually finish and produced the warnings above — not
a new freeze, just genuine scale cost of a large box with many real floors
(each floor pays for a Fine-detail geometry query + per-face triangulation
fallback in `extract_top_faces`). No special zones were in scope, so
`_analyze_zones` short-circuits early and isn't the cost here. Matches the
"other known things to keep an eye on" risks already listed below — not
acted on yet since it completes rather than hangs, and the user has already
said they're fine mitigating scale by testing smaller areas at a time.

## Added — per-scope-box floor count, shown before selection (2026-07-21)

User asked if there's a way to tell a scope box will be slow *before*
running the full analysis. Added `_count_floors_in_scope(scope_box,
struct_links)` — a cheap, geometry-free floor count (reuses
`collect_structural_floors`'s `BoundingBoxIntersectsFilter` collector per
loaded link, no per-floor face extraction). Wired into `_make_scope_row` /
`_populate_scope_boxes`, so every scope box in the list shows its floor
count (e.g. "85 × 128 m · 142 floor(s)") as soon as the dialog opens —
before the user selects anything and triggers the expensive
`_gather_floor_footprints` pass. Count text turns amber above
`SLOW_FLOOR_COUNT_WARNING` (60, a rough not-yet-benchmarked threshold) as a
heads-up, not a hard limit. Not yet tested in Revit.

## Fixed — resolve_height doing pointless/repeated work per floor (2026-07-21)

User shrank a scope box to 42 floors specifically to rule out raw scale, and
it was still slow — confirming this "not yet urgent" risk was real, not
hypothetical. Two distinct issues in `resolve_height` (called once per
floor, both during the actual Run *and* in the live conflicts-preview that
re-runs on every scope-box selection and every zone toggle/height edit):

1. **Did expensive work even with zero special zones.** It unconditionally
   built a full shadow-column extrusion for the floor's own footprint (can
   fall back to a per-triangle solid for messy real geometry — genuinely
   expensive) even when `candidate_spaces` was empty, i.e. there was nothing
   to test overlap against and the result was always going to be the plain
   default height anyway. Likely the dominant cost for scope boxes with no
   special zones (which both project 2 scope boxes tested so far have had).
   Fixed: early-return the plain default immediately when there are no
   candidate spaces, before touching any geometry.
2. **Rebuilt every zone-space's shadow column from scratch, per floor.** With
   N floors and M candidate zone spaces, this was an O(N×M) column rebuild
   for results that never change within one resolve pass (the same M spaces,
   over and over). Fixed: added an optional `space_col_cache` dict param —
   both call sites (`run()`, `_refresh_conflicts_preview`) now create one
   `{}` per floor-loop and pass it through, so each space's column is built
   once and reused across all floors in that pass.

Both fixes preserve identical results (same overlap math, same return
values) — purely a matter of not redoing/doing unnecessary work. **Not yet
tested in Revit — awaiting user re-run to confirm the 42-floor case is
fast now.**

## Other known things to keep an eye on (not yet urgent)

- `_filter_individually_valid` creates+deletes a throwaway DirectShape per
  triangle-prism piece to validate it individually — fine at ~100-300 pieces,
  could get slow on a much bigger/messier floor.
- The "always triangulate for clip" change (fix #1 above) makes every
  partial-overlap assignment more expensive than before by design; worth
  watching for Run-time slowness on projects with a special zone that
  crosses many floors.

## Housekeeping

- Nothing in this round has been committed yet (last commit `8bec8da`). User
  has not asked for a commit of the freeze fixes / non-planar-face fallback /
  diagnostic yet — ask before committing.
- This file is scratch/running-notes for mid-session continuity, not meant
  to be permanent repo documentation — fine to delete or fold into a commit
  message / PR description once this debugging thread is resolved.
