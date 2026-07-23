# Head Height Check — running notes

## RESOLVED (2026-07-23) — user-confirmed working; scaffolding removed

The zone/ramp/flat-floor system is confirmed correct in the real project:
ramps come out uniform full-height 2450, flat slabs the zone crosses get a
clean straight split at the outline, other levels unaffected. Final design:
vertical (+Z) extrusion for plumb head height; 2D point-in-polygon zone
decisions with a distance-based edge tolerance (ZONE_EDGE_TOL_FT) + an
elevation gate (ZONE_ELEV_TOL_FT); warped faces built by coarse-capped
triangulation and split per-triangle; clean flat faces split by one boolean
against a tall non-coincident zone column (2D per-triangle fallback). All
the temporary `_trace` scaffolding, the prototype probe, and the dead
boolean-clip/containment/area-check helpers have been removed. `_analyze_zones`
(preview badges only) still uses the older shadow-column logic — cosmetic.

The dated running log below is kept for history; it can be deleted once this
is merged.

---

Branch: `feature/head-height-check`. Committed through `ddb02e9` (see git log
for full history) — the notes below cover work done *since* that commit,
not yet committed.

## CLEAN SPLIT FOR FLAT PARTIAL FLOORS (2026-07-21) — pending test

Boolean-free logic fixed the ramp (all faces 100% in → uniform 2450), but
FLAT floors that genuinely straddle the zone (trace: 47-57% in — the path
crosses a slab) went through the per-triangle 2D split → many prisms +
jagged, triangle-stepped edge not matching the zone outline. Invisible on a
faceted ramp, ugly on a flat slab.

Fix: for a genuinely-partial CLEAN planar face, cut it with ONE Revit boolean
against a tall zone column (`_column_from_zone_polys` + `_boolean_clip_prism`)
→ straight edge following the outline, few pieces. This is reliable HERE
because the tall column shares no coincident face with the prism (coincident
faces were the cause of the earlier intermittent boolean failures, and a
zone crossing the middle of a slab has none). Warped faces (ramps) keep the
per-triangle 2D split. If the boolean can't run, it falls back to the
per-triangle method. So: ramp = uniform (2D), flat partial = clean straight
cut (boolean), and nothing depends on booleans succeeding.

**Next: user re-runs; expect flat floors that the zone crosses to be split
by a clean straight line along the zone outline (not faceted), ramp still
uniform 2450.**

## BOOLEAN-FREE ZONE LOGIC (2026-07-21) — the architectural fix

Root cause of the whole zone saga finally isolated from the trace: the
distance-tolerance logic was correct, but Revit's BooleanOperationsUtils
(intersect/difference) FAILS INTERMITTENTLY on the real imported geometry —
consecutive ramp faces gave "outside area = 0.001" (boolean ok → full 2450)
vs "outside area = whole face" (boolean threw → wrongly partial). Every zone
symptom across this whole thread traces to answering a 2D question with
fragile 3D boolean solid ops.

Rewrote the zone logic to use PURE 2D point-in-polygon math — no boolean
solids anywhere in the zone path:
- New 2D helpers: `_space_plan_polygon` (zone outline → rings of (x,y)),
  `_point_in_ring`/`_point_in_polygon`, `_point_seg_dist2`, `_point_in_zone`
  (inside OR within ZONE_EDGE_TOL_FT of an edge = the distance tolerance),
  `_face_plan_samples` (triangle centroids + plan areas, coarse-capped mesh),
  `_zone_cached`, `_build_face_prisms_classified`.
- `resolve_height` now: elevation-gate the candidate spaces (unchanged
  concept), sample the face's triangle centroids, classify each in/out via
  `_point_in_zone`, area-weight → in-fraction. ≥99% → full zone height; ≤1%
  → default; between → genuine partial. Different-height zones overlapping
  the same face → conflict. Returns assignments carrying `zone_polys` +
  `keep_inside` (not clip/exclude solids).
- `create_clearance_shape`: whole → clean `_build_clearance_prism`; partial →
  `_build_face_prisms_classified` keeps only triangles whose centroid is on
  the wanted side (2D test), extruded vertically. No booleans.

Now-dead (kept, harmless, to be cleaned up): `_apply_clip`,
`_apply_clip_by_containment`, `_point_inside_solid`, `_piece_centroid`,
`_check_clip_area`, `_zone_plan_column`, `_grow_zone_plan_prism`,
`_plan_area_prism`. `_analyze_zones` (preview badges only) still uses the old
shadow-column/boolean logic — cosmetic, doesn't affect built geometry.

**Next: user re-runs. Expect uniform 2450 on the ramp (edge strips within
250 mm tolerance absorbed), no intermittent boolean failures, other levels
unaffected. Genuine half-in/half-out floors split (faceted) at the outline.**

## DISTANCE-BASED EDGE TOLERANCE (2026-07-21) — superseded by boolean-free logic

The plan-ratio fix revealed the ramp is genuinely ~96% inside the zone: the
zone outline is drawn ~7-30 cm INSIDE the ramp edges, so a thin strip along
each edge fell outside → 2200 slivers on every face. A %-of-area threshold
is the wrong tool (10% of a big floor is a big real chunk; 10% of a tiny
floor is nothing). Agreed with the user on a DISTANCE-based tolerance
instead.

Implemented: `ZONE_EDGE_TOL_FT` (~250 mm). New `_grow_zone_plan_prism`
offsets the zone outline outward by the tolerance (via
`CurveLoop.CreateViaOffset`, sign chosen by whichever enlarges the outline,
falls back to un-grown if offset fails). In `resolve_height`, the full-vs-
partial decision is now: grow the zone by the tolerance; if the floor
footprint lies entirely within the grown outline (area outside ≤ 0.01 sqft)
→ whole floor gets the zone height (uniform, no sliver); otherwise → genuine
partial, split precisely at the REAL outline. Distance-based, so it behaves
the same for tiny/huge and flat/sloped floors. Grown prism cached per space
alongside the plan prism + Z band (`space_col_cache` entry is now a 3-tuple).
`FULL_OVERLAP_RATIO` no longer drives resolve_height (still used by the
`_analyze_zones` preview badges).

Still per-FACE (not yet the per-floor merge discussed) — the tolerance alone
should make the ramp uniform 2450 (each face pokes out only ~7 cm << 250 mm
→ treated as fully in). Fragmentation (many touching same-height masses) can
be cleaned up with per-floor merge as a follow-up if the count bothers the
user.

**Next: user re-runs; expect ramp uniform 2450 (edge strips absorbed by the
250 mm tolerance), genuine half-in/half-out floors still split precisely.**

## PLAN-RATIO + ELEVATION-GATE (2026-07-21) — the actual model for zones

Still partial 2450/2200 after the stacked-mass fix. Trace showed the ramp
faces DO sit inside the zone's height band (surfaces at Z≈−14..−6, band
[−19.4,−5.6]); they were only scored "partial" (0.89–0.97) because the
full/partial RATIO was measured on the tall clearance COLUMN, which pokes
above the zone ceiling — a measurement artifact. User rejected pure
plan-based (would bleed a zone onto other levels when the scope box spans
all basement levels).

Final model for zone membership (replaces the 3D shadow-column overlap in
resolve_height):
- ELEVATION GATE: the floor's own top-surface Z must lie within the zone
  Space's height band ± ZONE_ELEV_TOL_FT (4 ft). Keeps a zone on one level
  off floors stacked on other levels (tol << storey). This is what answers
  the "all basement floors in one scope box" concern.
- PLAN OVERLAP: footprint under the zone plan outline.
- RATIO measured in PLAN ONLY (`_plan_area_prism` flattens loops to z=0 and
  extrudes a unit prism; volume ratio == plan-area fraction, elevation
  factored out). So a face fully under the outline and within the band →
  ratio ~1.0 → `_plain(zone_height)` full uniform height, no clip, no
  slivers. Partial only when the footprint genuinely straddles the outline
  in plan.
- Clip still plan-based full-height (`_zone_plan_column`) for the genuinely
  partial case.
New helpers: `_loops_z_range`, `_plan_area_prism`. `space_col_cache` now
caches (plan prism, Z band) per space. `_analyze_zones` (the preview
full/partial badge table) still uses the older shadow-column logic — cosmetic
only, doesn't affect built geometry; can be aligned later if the preview
badges look off.

**Next: user re-runs; expect ramp uniform full-height 2450 (within band +
under outline), other basement levels unaffected.**

## STACKED-MASS FIX (2026-07-21) — real cause of "partial 2450 / partial 2200"

After the clip-height fix the ramp was STILL partly 2450 / partly 2200.
Trace showed every ramp face at ratio 0.89–0.97 (partial, because the face
column pokes just above the zone's Z ceiling — a Z effect, not a plan one),
and EACH face built BOTH a 2450 shape AND a 2200 shape (both ds=True). Root
cause in `create_clearance_shape`: when the exclude (2200 "outside zone")
side legitimately comes out EMPTY — face fully inside the zone — the old
code treated empty the same as "clip failed" and fell back to building the
FULL un-clipped 2200 prism. So a fully-in-zone face got its 2450 mass PLUS a
spurious full 2200 mass stacked under it.

Fix: an empty clip/exclude result is a valid "nothing on this side" → return
None, build nothing, no fallback. The un-clipped fallback now fires ONLY
when clipping produced geometry that Revit then rejected (a real failure),
never on a legitimately-empty result. So a face fully in the zone → only
2450; fully out → only 2200; genuinely straddling the plan edge →
complementary 2450-inside + 2200-outside. No more stacking.

**Next: user re-runs; expect the ramp within the zone to be uniform 2450
with no 2200 stacked on it.**

## CLIP-HEIGHT FIX (2026-07-21) — the real cause of "short ramp masses"

Vertical extrusion alone did NOT fix the short ramp masses. Real cause: the
partial-overlap CLIP intersected the clearance prism with the zone's
Z-BOUNDED shadow column (`_space_shadow_column`, spanning only the Space's
own modelled height band, e.g. [-18.4,-5.6]). So the clip chopped the
clearance off at the zone's ceiling → masses only as tall as the ZONE, not
the full required clearance above the floor. Flat floors looked right only
because they were FULL overlap → `_plain(2450)` with NO clip → full prism;
the ramp faces are PARTIAL → clipped → short. That asymmetry was the tell.

Fix: the clip/exclude solid is now a TALL PLAN column of the zone outline
(`_zone_plan_column`, ± ZONE_CLIP_HALF_FT = 500 ft about the Space), so it
trims a clearance prism only in PLAN, never in height. The Z-bounded
`_space_shadow_column` is still used for overlap DETECTION (deciding whether
a floor is in the zone at all — preserves the multi-level safety), but no
longer shapes the built geometry. `resolve_height`'s overlap bucket now
stores the Space elements (not their Z-bounded columns) and builds the clip
column from their plan outlines.

Consequence to verify with the user: a floor face detected as overlapping
the zone now gets the FULL 2450 clearance across the whole part of it inside
the zone's plan outline — including where a ramp rises. This is uniform
in-zone height (what the section review asked for), but it effectively means
the in-zone clearance is plan-shaped + full-height rather than clipped to
the zone's 3D volume — i.e. it softens the earlier strict-3D choice. Flag
for user confirmation on next visual check.

Also: `expected_ratio` now passed as None for partial assignments (the
Z-volume ratio no longer matches the plan-only clip), which disables the
advisory `*** CLIP AREA MISMATCH ***` warnings that were firing spuriously.
Area-based self-check to be reinstated on a volume basis later if wanted.

## VERTICAL-EXTRUSION FIX (2026-07-21) — pending test

Section through the zone showed the flat-floor clearance as a correct
uniform slab, but the ramp clearance came out SHORTER and stepped. Cause:
clearance was extruded along the face NORMAL, so on a slope it gave only
~height·cos(slope) of vertical clearance, and each coarse facet's different
normal produced steps. Head height is a vertical/plumb measure. Fixed by
extruding all clearance volumes straight UP (+Z) by the clearance height in
every tier (`_build_clearance_prism` raw + projected, `_mesh_to_prisms`
facets, with winding oriented up). Bottom still follows the ramp (slope
preserved); top is exactly `height` plumb above every point → uniform
vertical clearance, no facet steps. Flat floors unchanged (normal already
+Z). `host_normal` kept as a param but no longer drives direction.

KNOWN FOLLOW-UP: `_check_clip_area`'s area bookkeeping assumes perpendicular
extrusion (Volume/height == face area). With vertical extrusion on slopes
that no longer holds, so the advisory `*** CLIP AREA MISMATCH ***` warning
may fire spuriously on ramp faces. Advisory only — does not affect built
geometry. Quiet/rebase it on base-vs-clipped volume ratio in a later pass.

**Next: user re-runs; verify ramp clearance masses are now full 2450 height
(uniform vertical) and unstepped.**

## CURVED-RAMP FIX (2026-07-21) — pending test

After the plane-projection rewrite, result was "much better, nearly right,"
with two remaining defects on a curved ramp (floor 2323339):
- no mass where the ramp turns;
- default 2200 (not the zone's 2450) where the ramp meets the flat floor.

Diagnosis (from run trace + warnings): BOTH are the SAME face. That floor
has 9 top faces; 8 are ramp treads at Z≈−14..−16 that correctly get 2450
(partial, ratio ~0.96). The 9th is a single WARPED surface spanning Z≈−16
up to +8 (the curved turn + rise to the flat floor). It (a) tessellated to
39,644 triangles at finest LOD → over the 5000 cap → skipped (no mass), and
(b) its overlap shadow column was built flat at one elevation (Z≈[−1,8]),
which missed the zone band [−18.4,−5.6] → overlap 0 → default 2200. A flat,
fixed-height column can't represent a face that climbs 24 ft.

Fix (both parts implemented, user confirmed faceted ramps are acceptable):
1. Build: new `_choose_build_mesh` sweeps triangulation LOD coarse→fine and
   takes the coarsest mesh under the cap; `_mesh_to_prisms` extrudes it.
   `_build_clearance_prism` tier 3 now uses these, so a warped face builds
   as a few hundred large facets (buildable, follows the curve) instead of
   ~40k tiny prisms. Over-cap even at coarsest → flagged & skipped.
   (`_face_triangulated_solids` refactored away into these two.)
2. Overlap: `_footprint_shadow_columns` tier 2 now spans the loops' full
   Z extent (zmin−margin .. zmax+reach) instead of a fixed height at one
   elevation — so a warped ramp face is tested against the zone at every
   elevation it passes through. Flat faces (zmin≈zmax) are unchanged.

Under the user's chosen strict-3D-volume semantics, the ABOVE-zone part of
that face correctly stays 2200; the in-zone part now gets 2450, and the
turn now builds a (faceted) mass. Diagnostic trace (`resolve_height` DECISION
lines, per-face build outcome, `_solids_z_range`) still in place for this
test; `_trace`/probe scaffolding still to be stripped in final cleanup once
confirmed.

**Next: user re-runs Run check on the ramp scope box + zone; verify the turn
now has a (faceted) mass and the in-zone ramp gets 2450.**

## ARCHITECTURAL PIVOT (2026-07-21) — stop band-aiding the top-face B-rep

User (now on Opus) flagged the real problem: the run finished but the
clearance masses came out as **triangles that don't follow the zone
outline**, while the source structural floors have regular forms. And more
broadly: we keep band-aiding the same class of failure.

Diagnosis of the triangle look:
- In `create_clearance_shape`, any floor that partially overlaps a zone is
  ALWAYS triangulated, then clipped by keeping whole triangles whose
  centroid is inside the zone (`_apply_clip_by_containment`). So the zone
  edge can only follow triangle edges, never the real zone polygon — hence
  "triangles that don't match the outline." (We chose this to escape the
  earlier silent-gap boolean bug; now seeing its downside.)
- Floors that arrive split into many top faces (real ones seen with 249,
  245, 9 faces) also build as many separate fragments.

Root cause of the whole band-aid cycle: the algorithm is built on the
floor's exact **top-face B-rep** (find top faces → take exact edge loops →
extrude). Elegant for clean native floors; fragile for this project's
imported/IFC-derived FamilyInstance floors (split faces, non-planar,
degenerate). Every failure — ramp gap, 39k-triangle hang, crashes,
zero-face skips, jagged clip — traces to leaning on that fragile geometry
and falling back to triangulation.

Decisions taken with the user:
- **Preserve slope** (ramps must keep their sloped clearance surface — so a
  flat-footprint-straight-up approach is out; we keep per-face sloped
  extrusion but make it robust).
- **Prototype first** before rewriting the engine.

Working hypothesis for the redesign: clean extrusion fails (→ triangulation)
because imported top-face edge loops are *slightly non-planar*. Fix =
**fit a plane to the top face, project its edge loops onto that plane, and
extrude the projected loops along the plane normal** → one clean SLOPED
prism per face, slope preserved, no triangles, clean edges for zone
clipping (which then uses a clean boolean between two single solids instead
of triangle-centroid classification — the old boolean fragility came from
cutting thousands of tiny triangles, not from boolean itself).

Prototype added (read-only, behind `_PROTOTYPE_PROBE = True`, does NOT
change what the tool builds):
- `_face_best_fit_plane(face, link_transform)` — area-weighted plane fit.
- `_project_loops_to_plane(loops, origin, normal)` — tessellate + project
  loops onto the plane, rebuild as polyline CurveLoops.
- `_try_extrude_loops(...)` — attempt an extrusion, report ok/error.
- `_prototype_probe_floor(...)` — per floor, compares CURRENT raw-edge-loop
  extrusion vs. PROPOSED plane-projected extrusion, counts triangles the
  current fallback would build.
- Wired into `_gather_floor_footprints` (runs on scope-box selection):
  per-floor lines for floors that fail raw extrusion or exceed 500
  triangles, plus a `PROBE SUMMARY` block at the end reporting, across all
  floors: raw-OK vs projected-OK face counts, faces RESCUED by projection,
  faces STILL BROKEN under both, and total fallback triangles.

**PROBE RESULT (validated the redesign):** across 94 floors / 608 top faces:
- raw edge-loop extrusion clean: 522/608 (86 → triangulation)
- plane-projected extrusion clean: 583/608 (only 25 fail)
- 63 faces RESCUED by projection, 23 STILL BROKEN
- total triangles the current fallback would build: **756,526**

The 23 still-broken faces are scattered small faces on just two big
multi-face floors (5569638: 9 of 249; 4944911: 13 of 245) — non-critical.
The decisive win: the two catastrophic floors 2323339 (356,919 tris) and
2329124 (331,125 tris) — ~690k of the 756k total, and almost certainly the
Run-time crash source — are fully rescued by projection into clean prisms.
Firrst errors were "Non-planar CurveLoop" / "extrudeProjCurveLoops failed",
exactly the slightly-non-planar-loop hypothesis. Preview completed cleanly
(no crash; bbox-cap shadow-column fix held).

## THE REWRITE (agreed direction, implementation pending)

1. `create_clearance_shape` base geom: raw-loop extrusion → on failure,
   plane-fit + project → one clean sloped prism. Triangulation demoted to a
   rare HARD-CAPPED last resort (only the ~25 residual faces; cap piece
   count so it can never explode/crash).
2. Zone clip: clean boolean (intersect/difference) between the clean floor
   prism and the zone column — replaces triangle-centroid classification,
   fixes the jagged "doesn't match zone outline" masses.
3. resolve_height / _analyze_zones overlap test: use the same
   plane-projected footprint for consistency.
4. Residual still-broken faces: flag (not silently skip), optional capped
   triangulation.

Probe scaffolding (`_PROTOTYPE_PROBE`, `_prototype_probe_floor`,
`_try_extrude_loops`) to be removed once the rewrite lands; the real helpers
`_face_best_fit_plane` / `_project_loops_to_plane` get promoted into the
production path.

## REWRITE IMPLEMENTED (2026-07-21, pending user test)

All three changes landed:
1. New `_build_clearance_prism(face, loops, host_normal, link_transform,
   height_mm, label)` — tiered base geometry: raw edge-loop extrusion →
   plane-fit+project (slope preserved) → HARD-CAPPED triangulation
   (`TRIANGULATION_LAST_RESORT_CAP = 5000`; over the cap the face is flagged
   & skipped, never explodes). `create_clearance_shape` now calls it and
   returns None cleanly on total failure (no more uncaught `raise`).
2. Zone clip: clean single-prism cases (raw/projected — the vast majority)
   use an exact boolean (`_apply_clip`), so the zone edge follows the real
   polygon. Only the rare triangulated last-resort still uses centroid
   containment. Removed the old "always triangulate for clip" block that
   caused the jagged triangle masses.
3. `_footprint_shadow_columns` (overlap test) got a middle tier: flatten
   loops to a horizontal plane + extrude vertically → one clean plan-
   footprint column instead of per-triangle prisms. Consistent with the
   build path and removes the per-triangle blowup from the preview too.

`_PROTOTYPE_PROBE` set False (validated; code kept for reference). `_trace`
scaffolding still in place to validate this run; to be stripped in final
cleanup once confirmed.

Expected outcome on the problem scope box: the 2323339 / 2329124 floors
(formerly 350k+ triangles each) build as clean sloped prisms; zone-clipped
masses follow the zone outline (no triangle edges); the ~23 residual faces
on floors 5569638 / 4944911 fall to capped triangulation or a flag.

**Next step: user re-runs "Run check" with the zone on the problem scope
box and inspects the resulting masses (shape quality + zone-edge match) and
whether it completes without hang/crash.**

## NEW — silent Revit crash after the bbox-cap fix (2026-07-21, unresolved)

After the bbox-cap fix below (`SHADOW_COLUMN_TRIANGLE_CAP`), user re-tested
with a special zone in scope. This time: **Revit disappeared silently — no
crash dialog, no error message, no hang warning, it just vanished.** This
is meaningfully different from every previous incident in this thread (all
of which were slow-but-alive or unresponsive-but-present) — a message-less
disappearance points to a genuine native crash (likely deep in Revit's
geometry kernel) bypassing all catchable .NET/Python exception handling,
not a Python-level bug we could have caught with a try/except.

Confirmed via follow-up: this happened while only the scope box was
selected — the user had not clicked into the zones table or Run at all.
So the relevant code path is `_gather_floor_footprints` →
`_analyze_zones` → `resolve_height` (via `_refresh_conflicts_preview`) —
NOT `create_clearance_shape`/`run()` (those only execute after "Run
check", never reached here).

Since a silent crash leaves no exception to log, re-added the same
file-based `_trace`/`_trace_reset` mechanism (removed during the earlier
cleanup) — a crash can't corrupt lines already flushed to disk, so it's
the only forensic record available. Instrumented: `_gather_floor_footprints`
(per-floor extract_top_faces), `_analyze_zones` (zone-space count,
broad-phase filter result, per-floor shadow-column piece counts, per-1000
boolean-intersect progress), `resolve_height` (footprint_cols piece count,
per-space intersect start), and `_apply_clip_by_containment` (per-1000
progress) — the last one isn't reachable from this specific report (that's
Run-only) but is left in place since it has the same risk profile and will
matter for the next Run-time test.

**Reproduced with tracing in place — got much further this time, then
stopped.** `trace.log` shows `_gather_floor_footprints START`, then dozens
of floors processed cleanly (including Floor 5569638 with 249 faces and
Floor 4944911 with 245 faces — both completed fine; `extract_top_faces`
itself doesn't do the expensive triangulation/extrusion work, just reads
existing Faces), through the three known zero-face floors (2332979,
2333886, 2334641 — each completed including their diagnostic), then several
more real floors — the trace stops cold right after `"extract_top_faces:
Floor 2611993 done, 1 face(s)"`, with no further lines at all (no next
floor's start line, no per-link "collected" line, nothing).

Since `extract_top_faces` itself only inspects existing geometry (fast),
and the crash happens in the gap immediately after it returns, the blind
spot was in the UNTRACED code between floors: `face_boundary_loops_in_host_
space` (per face) and the per-link `collect_structural_floors` boundary
(moving to the next linked model). Added tracing to close both gaps:
`Link '{name}': collecting...` / `... N floor(s) collected` around each
link's collection, and `Floor {id}: extracting boundary loops for N
face(s)...` / `...boundary loops done` around the per-face loop-extraction
step, plus a final `_gather_floor_footprints DONE` marker.

**Not yet reproduced with this finer tracing — waiting on the user to
retry the same scope box once more.**

## Previous — second triangle-explosion hang, this time in the zone-overlap path (2026-07-21)

After committing `ddb02e9`, user started testing with a special zone (MEP
Space clearance override) actually in play, per the planned next step. New
report: **stuck again while selecting the scope box** (before clicking Run
— so this is the preview/analysis path, which had fully completed
successfully in the earlier trace when there were 0 zones in scope).

Diagnosis (no new trace needed — same mechanism, different consumer):
with a real zone now present, `_analyze_zones` and `resolve_height` no
longer take their early-return/empty-zone shortcuts, so they now actually
build a "shadow column" for every relevant floor via
`_footprint_shadow_columns` → `_vertical_column_solids_from_face`. That
function has the EXACT same per-triangle-prism pattern that caused the
Run-time hang (confirmed: Floor 2335381's face triangulates into ~40,000
pieces) — except here, all those pieces then feed into a
`BooleanOperationsUtils.ExecuteBooleanOperation` loop against every zone
space's column downstream, in both `_analyze_zones` and `resolve_height`.
Unlike the DirectShape create/delete hang, this has no document I/O, but
tens of thousands of geometry-kernel Boolean calls have their own real
per-call overhead — slow enough to look identical to the earlier hang.

**Fixed** (without needing to reproduce/re-trace, since the mechanism was
already fully understood from the Run-time fix): added
`SHADOW_COLUMN_TRIANGLE_CAP = 500` and `_vertical_column_bbox_solid(...)` —
when a face's triangulation exceeds the cap, `_vertical_column_solids_from_
face` now returns a single conservative rectangular column built from the
mesh's own X/Y bounding box instead of one prism per triangle. This is fine
for an "is there roughly an overlap" test (which never needed exact-shape
precision, unlike the real clearance geometry in `create_clearance_shape` —
that path still uses the precise `_face_triangulated_solids`/bisection
validation, untouched here). Turns the ~40,000-piece case back into 1 piece
for this specific overlap-test code path.

**Not yet tested — awaiting user retry with the same zone/scope box.**

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
