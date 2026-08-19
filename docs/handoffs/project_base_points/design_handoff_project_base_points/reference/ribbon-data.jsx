// ribbon-data.jsx
// Single source of truth for the EasyBIM Revit ribbon — panels, buttons,
// tooltips and per-tool metadata. Labels mirror the CURRENT shipped ribbon
// exactly (placeholder "ButtonN" names kept on purpose for dev handoff).

(function () {
  const ICON_IMG = "assets/easybim_icon.png";

  // Native Revit tabs shown to the left/right of the active EasyBIM tab.
  const TABS = [
    "Architecture", "Structure", "Steel", "Systems", "Insert",
    "Annotate", "Analyze", "Massing & Site", "Collaborate", "View",
    "Manage", "Add-Ins", "EasyBIM", "Modify",
  ];

  // Helper to keep tooltip authoring terse.
  const tip = (o) => o;

  const PANELS = [
    {
      id: "bim",
      name: "BIM Management",
      buttons: [
        {
          id: "basePoints",
          label: "Project Base Points",
          icon: "ProjectBasePoint",
          kind: "basePoints",
          tip: tip({
            title: "Project Base Points",
            summary: "Audit host & link base points against the AR reference",
            body: "Reads the Project Base Point of the host model and every directly-placed link — position in the host shared coordinate system, angle to true north, shared site and workset — then checks each discipline model against its matching Architecture model. Re-map any reference by hand and export to HTML, PDF or a Format-ready issue sheet.",
          }),
          handoff: { cls: "EasyBIM.Commands.BasePointsCommand", since: "2.5" },
        },
        {
          id: "scheduleGrid",
          label: "Button3",
          icon: "Table",
          kind: "stub",
          tip: tip({
            title: "Button3",
            summary: "BIM Management · command stub",
            body: "Registered ribbon command. A tabular / schedule grid tool is expected here — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.ScheduleGridCommand", since: "—" },
        },
        {
          id: "checkLevels",
          label: "Check Levels",
          icon: "ListChecks",
          kind: "checkLevels",
          tip: tip({
            title: "Check Levels",
            summary: "Validate level naming, elevation order & spacing",
            body: "Scans every Level in the model, flags duplicate elevations, non-monotonic ordering, naming-convention breaks and unhosted levels — then lets you jump straight to each issue.",
          }),
          handoff: { cls: "EasyBIM.Commands.CheckLevelsCommand", since: "1.4" },
        },
      ],
    },
    {
      id: "manage",
      name: "Manage",
      buttons: [
        {
          id: "blueprint",
          label: "Button1",
          icon: "DraftingCompass",
          kind: "stub",
          tip: tip({
            title: "Button1",
            summary: "Manage · command stub",
            body: "Registered ribbon command. Likely the EasyBIM project setup / about entry — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.SetupCommand", since: "—" },
        },
        {
          id: "settings",
          label: "Button2",
          icon: "SlidersHorizontal",
          kind: "stub",
          tip: tip({
            title: "Button2",
            summary: "Manage · command stub",
            body: "Registered ribbon command. A parameters / configuration tool is expected here — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.SettingsCommand", since: "—" },
        },
        {
          id: "exportExcel",
          label: "Export Excel",
          icon: "Sheet",
          kind: "exportExcel",
          split: true, // dropdown caret for recent targets
          tip: tip({
            title: "Export Excel",
            summary: "Push schedules & parameters out to .xlsx",
            body: "Select one or more schedules or model categories and export their data to a structured Excel workbook — one sheet per source, with headers and units preserved for round-tripping.",
          }),
          handoff: { cls: "EasyBIM.Commands.ExportExcelCommand", since: "2.0" },
        },
        {
          id: "importExcel",
          label: "Import Excel",
          icon: "FileUp",
          kind: "importExcel",
          tip: tip({
            title: "Import Excel",
            summary: "Write .xlsx values back onto model elements",
            body: "Map workbook columns to Revit parameters and push edited values back onto matching elements by Element ID — with a dry-run preview of every change before anything is committed.",
          }),
          handoff: { cls: "EasyBIM.Commands.ImportExcelCommand", since: "2.0" },
        },
      ],
    },
    {
      id: "mep",
      name: "MEP Coordination",
      buttons: [
        {
          id: "solutionSection",
          label: "Solution Section",
          icon: "SectionMarker",
          kind: "solutionSection",
          tip: tip({
            title: "Solution Section",
            summary: "Build a coordination section from linked MEP models",
            body: "Draws or reuses a section, copies every MEP element from the selected links inside its crop region into the host model on the “MEP Solution” workset, applies the EB_MEP_SOL_SE_1-50 view template and drops the view on a sheet — in under five clicks.",
          }),
          handoff: { cls: "EasyBIM.Commands.SolutionSectionCommand", since: "2.3" },
        },
        {
          id: "headHeightCheck",
          label: "Head Height Check",
          icon: "ArrowUpFromLine",
          kind: "headHeightCheck",
          tip: tip({
            title: "Head Height Check",
            summary: "Build clearance volumes above structural floors for clash checking",
            body: "Extrudes the required head-height clearance (default 2200 mm) upward from every structural-floor top face inside a scope box, reads MEP-Space overrides for special zones, drops the result on the “+ Mass” workset and isolates it in the EB_3D_9.Mass view — ready for Navisworks.",
          }),
          handoff: { cls: "EasyBIM.Commands.HeadHeightCheckCommand", since: "2.4" },
        },
        {
          id: "levelSheets",
          label: "Level Sheets",
          icon: "LevelSheets",
          kind: "levelSheets",
          tip: tip({
            title: "Level Sheets",
            summary: "Batch-create plan views and sheets from selected levels",
            body: "For every selected level: creates a floor plan and a ceiling plan, applies the EB_MEP_FP_1-50 / EB_MEP_CP_1-50 templates, renames both to the EasyBIM standard and drops them on one sheet — RCP above FP, aligned to the model so grids run continuously.",
          }),
          handoff: { cls: "EasyBIM.Commands.CreateLevelSheetsCommand", since: "2.5" },
        },
      ],
    },
    {
      id: "metro",
      name: "M3 Metro Line",
      buttons: [
        {
          id: "metroLine",
          label: "Button1",
          icon: "TramFront",
          kind: "stub",
          tip: tip({
            title: "Button1",
            summary: "M3 Metro Line · command stub",
            body: "Registered ribbon command. An M3 metro alignment / rolling-stock tool is expected here — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.MetroLineCommand", since: "—" },
        },
        {
          id: "metroRoute",
          label: "Button2",
          icon: "Route",
          kind: "stub",
          tip: tip({
            title: "Button2",
            summary: "M3 Metro Line · command stub",
            body: "Registered ribbon command. A route / track alignment tool is expected here — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.MetroRouteCommand", since: "—" },
        },
        {
          id: "metroStations",
          label: "Button3",
          icon: "Waypoints",
          kind: "stub",
          tip: tip({
            title: "Button3",
            summary: "M3 Metro Line · command stub",
            body: "Registered ribbon command. A station / waypoint placement tool is expected here — UI pending definition.",
            pending: true,
          }),
          handoff: { cls: "EasyBIM.Commands.MetroStationsCommand", since: "—" },
        },
      ],
    },
  ];

  // ---- Sample data used by the live tool dialogs ----

  const LEVELS = [
    { name: "B2 — Lower Basement", elev: "-7.200", status: "ok" },
    { name: "B1 — Basement", elev: "-3.600", status: "ok" },
    { name: "00 — Ground Floor", elev: "0.000", status: "ok" },
    { name: "Level 1", elev: "+3.600", status: "warn", issue: "Naming convention" },
    { name: "01 — First Floor", elev: "+3.600", status: "error", issue: "Duplicate elevation" },
    { name: "02 — Second Floor", elev: "+7.200", status: "ok" },
    { name: "03 — Third Floor", elev: "+10.800", status: "ok" },
    { name: "Roof", elev: "+14.400", status: "ok" },
    { name: "Parapet", elev: "+15.600", status: "warn", issue: "Unhosted (no plan view)" },
  ];

  const SCHEDULES = [
    { name: "Mechanical Equipment Schedule", cat: "Mechanical", rows: 142, on: true },
    { name: "Duct Fitting Take-off", cat: "Ducts", rows: 1280, on: true },
    { name: "Pipe Fitting Take-off", cat: "Pipes", rows: 964, on: false },
    { name: "Air Terminal Schedule", cat: "Air Terminals", rows: 318, on: true },
    { name: "Electrical Fixture Schedule", cat: "Electrical", rows: 207, on: false },
    { name: "Door Schedule", cat: "Doors", rows: 86, on: false },
  ];

  // ---- Sample data used by the Solution Section wizard ----
  // Link naming follows the project convention:
  //   SDE (project) - discipline - firm - building part - Revit version
  const LINKS = [
    { name: "SDE-ME-EBM-BLD-R24", disc: "Mechanical", tag: "MEP", loaded: true, on: true },
    { name: "SDE-PL-EBM-BLD-R24", disc: "Plumbing", tag: "MEP", loaded: true, on: true },
    { name: "SDE-EL-EBM-BLD-R24", disc: "Electrical", tag: "MEP", loaded: true, on: true },
    { name: "SDE-ST-EBM-BLD-R24", disc: "Structural", tag: "Structural", loaded: true, on: true },
    { name: "SDE-AR-EBM-BLD-R24", disc: "Architecture", tag: "Architectural", loaded: true, on: true },
    { name: "SDE-ME-EBM-BSMT-R24", disc: "Mechanical", tag: "MEP", loaded: false, on: false },
  ];

  // Existing section views the user can reuse (Step 1 · “Select existing”)
  const SECTIONS = [
    "Section 2 — Corridor C2",
    "Section 5 — Shaft S1",
    "Section 8 — Plant Room L1",
    "Section 11 — Basement Ramp",
  ];

  // Project sheets for the Step 3 sheet picker
  const SHEETS = [
    { num: "CO-201", name: "Corridor C2 — MEP Coordination" },
    { num: "CO-202", name: "Shaft S1 — MEP Coordination" },
    { num: "CO-203", name: "Plant Room — MEP Coordination" },
    { num: "ME-101", name: "Basement — HVAC Coordination" },
    { num: "ME-102", name: "Level 1 — HVAC Coordination" },
    { num: "PL-104", name: "Level 1 — Plumbing Layout" },
  ];

  // Mapping preview rows for Import Excel
  const IMPORT_MAP = [
    { col: "A · Mark", param: "Mark", type: "Text", match: true },
    { col: "B · Type Name", param: "Type Name", type: "Text", match: true },
    { col: "C · Flow (l/s)", param: "Air Flow", type: "Number", match: true },
    { col: "D · Service", param: "EB_Service", type: "Text", match: true },
    { col: "E · Notes", param: "Comments", type: "Text", match: true },
    { col: "F · Cost Code", param: "— unmapped —", type: "Text", match: false },
  ];

  // ---- Sample data used by the Head Height Check tool ----
  // Scope boxes exist in the HOST model only (Revit never exposes linked
  // scope boxes to the host document). The user creates these ahead of time.
  const SCOPE_BOXES = [
    { name: "SB_Basement", extent: "B2–B1 · 62 × 48 m", floors: 18 },
    { name: "SB_L01", extent: "Level 1 · 88 × 54 m", floors: 24 },
    { name: "SB_L02", extent: "Level 2 · 88 × 54 m", floors: 24 },
    { name: "SB_Roof_Plant", extent: "Roof · 40 × 30 m", floors: 9 },
  ];

  // Loaded/unloaded STRUCTURAL Revit links in the host model. A project may
  // have several (per building / phase / external firm).
  const STRUCT_LINKS = [
    { name: "SDE-ST-EBM-BLD-R24", disc: "Structural — Superstructure", loaded: true, on: true, floors: 34 },
    { name: "SDE-ST-EBM-BSMT-R24", disc: "Structural — Basement box", loaded: true, on: true, floors: 12 },
    { name: "SDE-ST-XYZ-PODIUM-R24", disc: "Structural — Podium (ext. firm)", loaded: false, on: false, floors: 0 },
  ];

  // MEP Spaces carrying a "Required Clearance Height" override (special zones).
  // These are placed manually by the coordinator over accessibility drawings.
  const CLEARANCE_ZONES = [
    { scope: "SB_Basement", name: "Disabled-access path — Core B", height: 2400, overlap: "full" },
    { scope: "SB_Basement", name: "Basement ramp landing", height: 2600, overlap: "partial" },
    { scope: "SB_L01", name: "Main lobby — feature zone", height: 2500, overlap: "full" },
  ];

  // Floors where two overlapping Spaces disagree on the value → flagged, never guessed.
  const CLEARANCE_CONFLICTS = [
    { scope: "SB_Basement", floor: "SL-B1-014", values: [2400, 2600] },
  ];

  // ---- Sample data used by the Project Base Points audit ----
  // Naming convention: PROJECT-DISCIPLINE-FIRM-BUILDING-REVITVERSION
  // Coordinates are the link's PBP expressed in the HOST shared coordinate
  // system, in meters. Angle = the model's own angle to true north (deg).
  const PBP_ROWS = [
    { id: 0, link: "MSH-CO-EBI-Main-R25", inst: "", kind: "HOST", disc: "-", key: "-", isAR: false, shared: "-", site: "-", workset: "-", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 1, link: "MSH-AR-NML-BLD_A-R25", inst: "", kind: "Link", disc: "AR", key: "BLD_A", isAR: true, shared: "Yes", site: "ACME Shared", workset: "Link_AR", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 2, link: "MSH-AR-NML-BLD_B-R25", inst: "", kind: "Link", disc: "AR", key: "BLD_B", isAR: true, shared: "Yes", site: "ACME Shared", workset: "Link_AR", ns: 665150.1, ew: 179900.5, el: 124.9, ang: 331.4207, placed: true },
    { id: 3, link: "MSH-AR-NML-Main-R25", inst: "", kind: "Link", disc: "AR", key: "MAIN", isAR: true, shared: "Yes", site: "ACME Shared", workset: "Link_AR", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 4, link: "MSH-ST-STIERLER-BLD_A-R25", inst: "", kind: "Link", disc: "ST", key: "BLD_A", isAR: false, shared: "Yes", site: "ACME Shared", workset: "Link_STR", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 5, link: "MSH-ST-STIERLER-BLD_B-R25", inst: "", kind: "Link", disc: "ST", key: "BLD_B", isAR: false, shared: "Yes", site: "ACME Shared", workset: "Link_STR", ns: 665150.1, ew: 179900.65, el: 124.9, ang: 331.4207, placed: true },
    { id: 6, link: "MSH-ME-MAOF-BLD_A-R25", inst: "", kind: "Link", disc: "ME", key: "BLD_A", isAR: false, shared: "Yes", site: "ACME Shared", workset: "Link_MEP", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 7, link: "MSH-ME-MAOF-Main-R25", inst: "", kind: "Link", disc: "ME", key: "MAIN", isAR: false, shared: "No", site: "", workset: "Link_MEP", ns: 0, ew: 0, el: 0, ang: 0, placed: true },
    { id: 8, link: "MSH-EL-SMO-BLD_A-R25", inst: "#1", kind: "Link", disc: "EL", key: "BLD_A", isAR: false, shared: "Yes", site: "ACME Shared", workset: "Link_ELEC", ns: 665099.262, ew: 179835.791, el: 125.4, ang: 331.4207, placed: true },
    { id: 9, link: "MSH-EL-SMO-BLD_A-R25", inst: "#2", kind: "Link", disc: "EL", key: "BLD_A", isAR: false, shared: "No", site: "", workset: "Link_ELEC", ns: 0, ew: 0, el: 0, ang: 331.4207, placed: true },
    { id: 10, link: "MSH-PL-EBI-BLD_B-R25", inst: "", kind: "Link", disc: "PL", key: "BLD_B", isAR: false, shared: "Yes", site: "ACME Shared", workset: "Link_MEP", ns: 665150.1, ew: 179900.5, el: 124.9, ang: 331.4207, placed: true },
    { id: 11, link: "MSH-FP-XYZ-BLD_C-R25", inst: "", kind: "Link", disc: "FP", key: "BLD_C", isAR: false, shared: "Yes", site: "Survey Point Site", workset: "Link_Consultants", ns: 665241.88, ew: 179962.4, el: 125.4, ang: 45.0, placed: true },
    { id: 12, link: "MSH-LS-ABL-Main-R25", inst: "", kind: "Link (unloaded)", disc: "LS", key: "MAIN", isAR: false, shared: "?", site: "", workset: "Link_Consultants", ns: null, ew: null, el: null, ang: null, placed: false },
  ];

  const PBP_HOST = {
    title: "MSH-CO-EBI-Main-R25",
    path: "BIM 360://MSH — Coordination/MSH-CO-EBI-Main-R25.rvt",
    generated: "2026-07-23 09-00",
    units: "meters",
    workshared: true,
    nested: 4,
  };

  // ---- Sample data used by the Level Sheets tool (Stage A) ----
  // Levels come from the architect's link via copy/monitor, so names are
  // unreliable and may be non-English — the code is never parsed from them.
  // `stored` = a value already written to EB_Level_Code on a previous run.
  const SHEET_LEVELS = [
    { id: 1, name: "מרתף -2", elev: -7.2, stored: "" },
    { id: 2, name: "מרתף -1", elev: -3.6, stored: "B01" },
    { id: 3, name: "Ground Floor", elev: 0, stored: "GF00", big: true },
    { id: 4, name: "Level 1", elev: 4.2, stored: "" },
    { id: 5, name: "Level 2", elev: 7.8, stored: "" },
    { id: 6, name: "Mezzanine over L2", elev: 9.9, stored: "F02M" },
    { id: 7, name: "Level 3", elev: 11.4, stored: "" },
    { id: 8, name: "Roof", elev: 15, stored: "" },
  ];

  // Loaded title-block types (FamilySymbol, OST_TitleBlocks).
  const TITLE_BLOCKS = [
    "EB_TitleBlock_A1 : A1 metric",
    "EB_TitleBlock_A0 : A0 metric",
    "E1 30 x 42 Horizontal : E1",
  ];
  // ViewFamilyType options, filtered by ViewFamily.
  const VFT_FP = ["EB_MEP_FloorPlan", "Floor Plan"];
  const VFT_CP = ["EB_MEP_CeilingPlan", "Ceiling Plan"];
  // View templates resolved by exact name at preflight.
  const PLAN_TEMPLATES = { fp: "EB_MEP_FP_1-50", cp: "EB_MEP_CP_1-50", fpFound: true, cpFound: true, scale: 50 };

  Object.assign(window, { PBP_ROWS, PBP_HOST, TABS, PANELS, LEVELS, SCHEDULES, IMPORT_MAP, LINKS, SECTIONS, SHEETS, ICON_IMG, SCOPE_BOXES, STRUCT_LINKS, CLEARANCE_ZONES, CLEARANCE_CONFLICTS, SHEET_LEVELS, TITLE_BLOCKS, VFT_FP, VFT_CP, PLAN_TEMPLATES });
})();
