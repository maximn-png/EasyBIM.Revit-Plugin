// ribbon-base-points.jsx
// "Project Base Points" — BIM Management · PBP coordination audit.
// Replaces the Dynamo HTML output with an in-Revit dialog: reads the PBP of the
// host model and every DIRECTLY-placed link, matches each discipline model to
// its Architecture reference, and exports HTML / PDF / an ACC-ready issue XLSX.
//
// Setup + results live here; the export/issue wizard lives in
// ribbon-base-points-export.jsx (registers window.PBPExport).

(function () {
  const DS = window.EasyBIMDesignSystem_a35564 || {};
  const { Button, Badge, Input } = DS;
  const { Icon } = window;

  const NAVY = "#1e248c", CYAN = "#44b8d3";
  const OK = "var(--eb-success)", ORANGE = "#e0851e", RED = "var(--eb-error)", MUTE = "#8b93a7";
  const AR_CODES = ["AR", "ARC", "ARCH", "A"];
  const ENGINE = "v8";
  const PREF_KEY = "eb.pbp.prefs";

  const card = { background: "#fff", border: "1px solid var(--eb-line)", borderRadius: "var(--radius-lg)" };
  const sectionLabel = { fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "#9aa0ac", margin: "0 0 8px" };

  // ---- preferences that survive between runs (report folder, field mapping) ----
  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREF_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function savePrefs(patch) {
    try { localStorage.setItem(PREF_KEY, JSON.stringify(Object.assign(loadPrefs(), patch))); } catch (e) {}
  }

  // ---- model helpers ----
  const rows = () => window.PBP_ROWS || [];
  const host = () => window.PBP_HOST || {};
  const fmt = (v, nd = 3) => (v === null || v === undefined ? "—" : v.toFixed(nd));
  const angDiff = (a, b) => Math.abs((((a - b + 180) % 360) + 360) % 360 - 180);
  const discOf = (r, s) => (s.discOverride && s.discOverride[r.id] !== undefined ? s.discOverride[r.id] : r.disc);
  const isAR = (r, s) => AR_CODES.indexOf(discOf(r, s)) >= 0 && r.kind !== "HOST";

  function autoRefs() {
    const arByKey = {};
    rows().forEach((r) => { if (AR_CODES.indexOf(r.disc) >= 0 && r.placed && !(r.key in arByKey)) arByKey[r.key] = r.id; });
    const map = {};
    rows().forEach((r) => {
      if (r.kind === "Link" && r.placed && AR_CODES.indexOf(r.disc) < 0) map[r.id] = arByKey[r.key] !== undefined ? arByKey[r.key] : "";
    });
    return map;
  }

  // Status vocabulary — these exact four drive the report and the issue export.
  function deltasOf(r, s) {
    const ref = rows().find((x) => x.id === Number(s.refs[r.id]));
    if (!ref) return null;
    return { ns: r.ns - ref.ns, ew: r.ew - ref.ew, el: r.el - ref.el, ang: angDiff(r.ang, ref.ang), ref: ref };
  }
  function statusOf(r, s) {
    if (r.kind === "HOST") return "Host";
    if (!r.placed) return "Unloaded";
    if (r.shared === "No") return "Not Shared";
    if (isAR(r, s)) return "Reference";
    const refId = s.refs[r.id];
    if (refId === "" || refId === undefined) return "Missing Ref";
    const d = deltasOf(r, s);
    if (!d) return "Missing Ref";
    const tolM = (Number(s.tolMm) || 1) / 1000, tolDeg = Number(s.tolDeg) || 0.02;
    const near = Math.abs(d.ns) <= tolM && Math.abs(d.ew) <= tolM && Math.abs(d.el) <= tolM && d.ang <= tolDeg;
    return near ? "OK" : "Not OK";
  }
  const TONE = { OK: OK, "Not OK": ORANGE, "Not Shared": RED, "Missing Ref": MUTE, Reference: CYAN, Unloaded: MUTE, Host: MUTE };
  const BG = { OK: "rgba(34,176,124,0.12)", "Not OK": "rgba(224,133,30,0.15)", "Not Shared": "rgba(214,69,69,0.13)", "Missing Ref": "#eef0f6", Reference: "rgba(68,184,211,0.14)", Unloaded: "#f1f2f7", Host: "#f1f2f7" };
  const STATUSES = ["OK", "Not OK", "Not Shared", "Missing Ref", "Reference", "Unloaded"];
  // Missing Ref is a setup problem (no AR model linked), not a Format issue.
  const ISSUE_STATUSES = ["Not OK", "Not Shared"];

  // Discipline code → hub role + Hebrew discipline.
  // Keys cover the ISO 19650 / BS 1192 single-letter originator codes as well as
  // the two-letter codes used in EasyBIM file naming, so Role and דיספלינה fill
  // themselves from the Code column. Both stay editable per project.
  // Hebrew values are limited to the list configured on the hub (HEB_OPTIONS).
  const HEB_OPTIONS = ["אדריכלות", "קונסטרוקציה", "אינסטלציה", "חשמל", "מיזוג אוויר", "אדריכלות נוף", "כללי"];
  const DISC_MAP = {
    // local two-letter codes
    AR: ["Architect", "אדריכלות"], ARC: ["Architect", "אדריכלות"], ST: ["Structural Engineer", "קונסטרוקציה"],
    ME: ["Mechanical Engineer", "מיזוג אוויר"], EL: ["Electrical Engineer", "חשמל"], PL: ["Plumbing Engineer", "אינסטלציה"],
    FP: ["Fire Safety", "כללי"], SN: ["Sanitation", "אינסטלציה"], LS: ["Landscape", "אדריכלות נוף"],
    EV: ["Elevators", "כללי"], AC: ["Accessibility", "כללי"], SF: ["Safety", "כללי"],
    TR: ["Traffic Engineer", "כללי"], QS: ["Quantity Surveyor", "כללי"], IN: ["Interior Designer", "אדריכלות"],
    CO: ["BIM Manager", "כללי"], CE: ["Geotechnical Engineer", "קונסטרוקציה"], SV: ["Surveyor", "כללי"],
    // ISO 19650 originator / discipline letters
    A: ["Architect", "אדריכלות"], B: ["Construction Manager", "כללי"], C: ["Geotechnical Engineer", "קונסטרוקציה"],
    D: ["Plumbing Engineer", "אינסטלציה"], E: ["Electrical Engineer", "חשמל"], F: ["Project Manager", "כללי"],
    G: ["Surveyor", "כללי"], H: ["Mechanical Engineer", "מיזוג אוויר"], I: ["Interior Designer", "אדריכלות"],
    K: ["Client", "כללי"], L: ["Landscape", "אדריכלות נוף"], M: ["Mechanical Engineer", "מיזוג אוויר"],
    P: ["Plumbing Engineer", "אינסטלציה"], Q: ["Quantity Surveyor", "כללי"], S: ["Structural Engineer", "קונסטרוקציה"],
    T: ["Project Manager", "כללי"], W: ["Construction Manager", "כללי"], X: ["other", "כללי"],
    Y: ["Fire Safety", "כללי"], Z: ["BIM Manager", "כללי"],
  };
  const DISC_CODES = ["AR", "ST", "ME", "EL", "PL", "FP", "SN", "LS", "EV", "AC", "SF", "TR", "QS", "IN", "CO", "CE", "SV", "other"];
  const autoRole = (code) => (DISC_MAP[code] ? DISC_MAP[code][0] : "other");
  const autoHeb = (code) => (DISC_MAP[code] ? DISC_MAP[code][1] : "כללי");
  // Roles configured on the EasyBIM hub (Project settings ▸ Roles).
  const HUB_ROLES = ["3D Visualizer", "Accessibility", "Acoustical Advisor", "Agronomist", "Aluminium", "Architect", "BIM Manager", "Blast protection", "Client", "Communication", "Construction Manager", "Cranes & Gates", "Electrical Engineer", "Elevators", "Environmental Engineer", "Facade Designers", "Fire Safety", "Geotechnical Engineer", "Hydrologist", "Interior Designer", "Kitchens design", "Landscape", "Lighting Designer", "Mechanical Engineer", "other", "Plumbing Engineer", "Project Manager", "Quantity Surveyor", "Radiation", "Safety", "Sanitation", "Security", "Structural Engineer", "Surveyor", "Thermal advisor", "Traffic Engineer", "VDC Manager", "Waterproofing"];

  // ---- shared bits of chrome ----
  function StatusPill({ s }) {
    return <span className="eb-mono" style={{ display: "inline-block", padding: "2px 7px", borderRadius: 999, fontSize: 10, fontWeight: 700, color: TONE[s] || MUTE, background: BG[s] || "#f1f2f7", whiteSpace: "nowrap" }}>{s}</span>;
  }
  function Toggle({ on, onClick, sm }) {
    const w = sm ? 32 : 38, h = sm ? 19 : 22, k = sm ? 15 : 18;
    return (
      <button onClick={onClick} style={{ width: w, height: h, borderRadius: 999, border: "none", padding: 2, cursor: onClick ? "pointer" : "default", background: on ? CYAN : "#cbd0e0", transition: "background 160ms", flex: "0 0 auto" }}>
        <span style={{ display: "block", width: k, height: k, borderRadius: "50%", background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,0.2)", transform: on ? "translateX(" + (w - k - 4) + "px)" : "none", transition: "transform 160ms var(--ease-out)" }} />
      </button>
    );
  }
  function Check({ on, onClick, dim, title }) {
    return (
      <button onClick={onClick} title={title || (on ? "Included in graph & report" : "Excluded from graph & report")}
        style={{ width: 15, height: 15, flex: "0 0 auto", borderRadius: 4, cursor: onClick ? "pointer" : "default", border: "1.5px solid " + (on ? CYAN : "#c4cad9"), background: on ? CYAN : "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0, opacity: dim ? 0.5 : 1 }}>
        {on && <Icon name="Check" size={10} stroke={3.4} style={{ color: "#fff" }} />}
      </button>
    );
  }
  function Chip({ on, onClick, children, count }) {
    return (
      <button onClick={onClick} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 11px", borderRadius: 999, cursor: "pointer", fontFamily: "var(--font-body)", fontSize: 12, fontWeight: 600, color: on ? "#fff" : "#4b5563", background: on ? NAVY : "#fff", border: "1px solid " + (on ? NAVY : "var(--eb-line)"), transition: "all 140ms" }}>
        {children}{count !== undefined && <span className="eb-mono" style={{ fontSize: 10.5, opacity: 0.75 }}>{count}</span>}
      </button>
    );
  }
  function Disclosure({ open, onToggle, title, hint, children }) {
    return (
      <div style={{ ...card, marginBottom: 14, overflow: "hidden" }}>
        <button onClick={onToggle} style={{ width: "100%", display: "flex", alignItems: "center", gap: 9, padding: "10px 14px", background: open ? "#fafbff" : "#fff", border: "none", borderBottom: open ? "1px solid var(--eb-line-soft)" : "none", cursor: "pointer", fontFamily: "var(--font-body)", textAlign: "left" }}>
          <Icon name={open ? "ChevronDown" : "ChevronRight"} size={15} stroke={2.2} style={{ color: MUTE, flex: "0 0 auto" }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}>{title}</span>
          <span style={{ flex: 1 }} />
          <span className="eb-mono" style={{ fontSize: 10.5, color: "#9aa0ac" }}>{hint}</span>
        </button>
        {open && <div style={{ padding: "13px 14px" }}>{children}</div>}
      </div>
    );
  }

  // ============================================================ SETUP
  function Setup({ s, set }) {
    const h = host();
    const all = rows();
    const links = all.filter((r) => r.kind !== "HOST");
    const ars = links.filter((r) => isAR(r, s) && r.placed);
    const unloaded = links.filter((r) => !r.placed).length;
    const notShared = links.filter((r) => r.placed && r.shared === "No").length;

    return (
      <React.Fragment>
        <p style={sectionLabel}>Host model</p>
        <div style={{ ...card, display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", marginBottom: 14 }}>
          <span style={{ width: 38, height: 38, borderRadius: 10, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "linear-gradient(135deg,#1e248c,#44b8d3)" }}>
            <Icon name="ProjectBasePoint" size={19} stroke={1.9} style={{ color: "#fff" }} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="eb-mono" style={{ fontSize: 13, fontWeight: 700, color: NAVY, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.title}</div>
            <div style={{ fontSize: 11.5, color: "#9aa0ac", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h.path} &nbsp;·&nbsp; units: {h.units}</div>
          </div>
          {h.workshared && <Badge tone="live" dot>Workshared</Badge>}
        </div>

        <p style={sectionLabel}>Architecture reference models · the baseline every discipline is checked against</p>
        <div style={{ ...card, overflow: "hidden", marginBottom: 14 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,0.5fr) minmax(0,0.62fr) minmax(0,0.55fr) minmax(0,0.55fr)", background: "#f4f6fd", borderBottom: "1px solid var(--eb-line)" }}>
            {["Model", "Building", "Shared site", "Elev (m)", "Angle (deg)"].map((t, i) => (
              <span key={t} className="eb-mono" style={{ padding: "7px 10px", fontSize: 9.5, letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 700, color: "#8b93a7", textAlign: i >= 3 ? "right" : "left" }}>{t}</span>
            ))}
          </div>
          {ars.length === 0 && <div style={{ padding: "16px 12px", textAlign: "center", fontSize: 12.5, color: ORANGE }}>No Architecture model found — every link will report Missing Ref. Re-map disciplines in the results table.</div>}
          {ars.map((r, i) => (
            <div key={r.id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,0.5fr) minmax(0,0.62fr) minmax(0,0.55fr) minmax(0,0.55fr)", alignItems: "center", borderBottom: i < ars.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 7, padding: "8px 10px", minWidth: 0 }}>
                <Icon name="Link2" size={13} stroke={1.9} style={{ color: CYAN, flex: "0 0 auto" }} />
                <b className="eb-mono" style={{ fontSize: 11.5, color: "#1f2937", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.link}</b>
              </span>
              <span className="eb-mono" style={{ padding: "8px 10px", fontSize: 11, color: "#6b7280" }}>{r.key}</span>
              <span style={{ padding: "8px 10px", fontSize: 11.5, color: r.shared === "No" ? RED : "#4b5563", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.shared === "No" ? "Not Shared" : r.site}</span>
              <b className="eb-mono" style={{ padding: "8px 10px", fontSize: 11.5, color: NAVY, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmt(r.el)}</b>
              <b className="eb-mono" style={{ padding: "8px 10px", fontSize: 11.5, color: NAVY, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.ang === null ? "—" : r.ang.toFixed(4)}</b>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 9, marginBottom: 14 }}>
          {[[links.length, "Link instances", NAVY], [h.nested || 0, "Nested · skipped", MUTE], [ars.length, "AR references", CYAN], [notShared, "Not shared", notShared ? RED : MUTE], [unloaded, "Unloaded", unloaded ? ORANGE : MUTE]].map(([v, l, c]) => (
            <div key={l} style={{ ...card, padding: "10px 12px", textAlign: "center" }}>
              <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 22, color: c, lineHeight: 1 }}>{v}</div>
              <div style={{ fontSize: 10.5, color: "#6b7280", marginTop: 4 }}>{l}</div>
            </div>
          ))}
        </div>

        <Disclosure open={s.advOpen} onToggle={() => set({ advOpen: !s.advOpen })}
          title="Tolerances & options" hint={s.tolMm + " mm · " + s.tolDeg + "° · " + Object.keys(s.opts).filter((k) => s.opts[k]).length + " of " + Object.keys(s.opts).length + " on"}>
          <div style={{ display: "flex", gap: 12, marginBottom: 13 }}>
            <div style={{ flex: 1 }}>
              <p style={sectionLabel}>Position tolerance</p>
              <div style={{ position: "relative" }}>
                <Input type="number" value={s.tolMm} onChange={(e) => set({ tolMm: e.target.value })} />
                <span className="eb-mono" style={{ position: "absolute", right: 14, top: "50%", transform: "translateY(-50%)", fontSize: 12, color: "#9aa0ac", pointerEvents: "none" }}>mm</span>
              </div>
            </div>
            <div style={{ flex: 1 }}>
              <p style={sectionLabel}>Angle tolerance</p>
              <div style={{ position: "relative" }}>
                <Input type="number" step="0.01" value={s.tolDeg} onChange={(e) => set({ tolDeg: e.target.value })} />
                <span className="eb-mono" style={{ position: "absolute", right: 14, top: "50%", transform: "translateY(-50%)", fontSize: 12, color: "#9aa0ac", pointerEvents: "none" }}>deg</span>
              </div>
            </div>
            <div style={{ flex: 1.2, minWidth: 0 }}>
              <p style={sectionLabel}>Architecture codes</p>
              <div style={{ ...card, display: "flex", alignItems: "center", gap: 6, padding: "0 12px", height: 42, background: "#fafbff" }}>
                {AR_CODES.map((c) => <span key={c} className="eb-mono" style={{ fontSize: 11, fontWeight: 700, color: NAVY, background: "rgba(30,36,140,0.08)", borderRadius: 6, padding: "3px 7px" }}>{c}</span>)}
              </div>
            </div>
          </div>
          <div style={{ ...card, overflow: "hidden" }}>
            {[["unloaded", "List unloaded links", "Shown greyed, without coordinates — so nothing goes missing from the register."],
              ["log", "Write a run log alongside the report", "Records every link processed, its computed values and any warning."],
              ["remember", "Remember mapping & destination for this host model", "Reference mapping, discipline fixes and the report folder are restored next run."],
              ["notHub", "Not in EasyBIM Hub", "This project sits on another hub — the issue export will ask for a template issue report and its own role names."]].map(([k, t, d], i, arr) => (
              <div key={k} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, padding: "10px 13px", borderBottom: i < arr.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: k === "notHub" && s.opts.notHub ? "rgba(224,133,30,0.06)" : "transparent" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: "#374151", fontWeight: 500 }}>{t}</div>
                  <div style={{ fontSize: 11.5, color: "#9aa0ac", marginTop: 2 }}>{d}</div>
                </div>
                <Toggle on={s.opts[k]} onClick={() => set({ opts: { ...s.opts, [k]: !s.opts[k] } })} />
              </div>
            ))}
          </div>
        </Disclosure>

        <div style={{ display: "flex", alignItems: "flex-start", gap: 9, padding: "11px 14px", borderRadius: "var(--radius-lg)", background: "rgba(68,184,211,0.07)", border: "1px solid rgba(68,184,211,0.22)" }}>
          <Icon name="Info" size={14} stroke={2} style={{ color: CYAN, flex: "0 0 auto", marginTop: 2 }} />
          <span style={{ fontSize: 12, color: "#6b7280", lineHeight: 1.5 }}>
            Each discipline model is matched to the Architecture model sharing its building key (<span className="eb-mono" style={{ color: NAVY }}>ST-BLD_A</span> → <span className="eb-mono" style={{ color: NAVY }}>AR-BLD_A</span>). Matching is best-effort — discipline, reference and inclusion are all editable in the results table.
          </span>
        </div>
      </React.Fragment>
    );
  }

  // ============================================================ CHART
  function Chart({ s, bars, onToggle }) {
    const groups = {};
    rows().forEach((r) => {
      if (r.kind === "HOST" || isAR(r, s) || s.excluded[r.id]) return;
      const d = discOf(r, s) || "?";
      if (!groups[d]) groups[d] = { ok: 0, notok: 0, unshared: 0, other: 0, total: 0 };
      const st = statusOf(r, s);
      groups[d].total++;
      if (st === "OK") groups[d].ok++; else if (st === "Not OK") groups[d].notok++; else if (st === "Not Shared") groups[d].unshared++; else groups[d].other++;
    });
    const order = Object.keys(groups).sort();
    const sum = (k) => order.reduce((n, d) => n + groups[d][k], 0);
    const totNotOk = sum("notok"), totUnshared = sum("unshared"), totAll = sum("total");
    const excluded = Object.keys(s.excluded).filter((k) => s.excluded[k]).length;

    return (
      <div style={{ ...card, padding: bars ? "10px 14px" : "7px 12px", marginBottom: 9, flex: "0 0 auto" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <button onClick={onToggle} title={bars ? "Hide the graph" : "Show the graph"}
            style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "var(--font-body)", textAlign: "left", minWidth: 0 }}>
            <Icon name={bars ? "ChevronDown" : "ChevronRight"} size={14} stroke={2.2} style={{ color: MUTE, flex: "0 0 auto" }} />
            <span style={{ fontSize: 12.5, color: "#374151" }}>
              <b style={{ color: NAVY }}>{totAll}</b> link{totAll === 1 ? "" : "s"} in <b style={{ color: NAVY }}>{order.length}</b> discipline{order.length === 1 ? "" : "s"} — <b style={{ color: totNotOk ? ORANGE : OK }}>{totNotOk}</b> not coordinated, <b style={{ color: totUnshared ? RED : OK }}>{totUnshared}</b> not in shared coordinates
              {excluded > 0 && <span style={{ color: MUTE }}> · {excluded} excluded</span>}
            </span>
          </button>
          <div style={{ display: "flex", gap: 10, fontSize: 10.5, color: "#9aa0ac", flex: "0 0 auto" }}>
            {[["OK", OK], ["Not OK", ORANGE], ["Not Shared", RED], ["Missing Ref", "#c9cedb"]].map(([l, c]) => (
              <span key={l} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><i style={{ width: 9, height: 9, borderRadius: 3, background: c, display: "inline-block" }} />{l}</span>
            ))}
          </div>
        </div>
        {bars && <div className="eb-scroll" style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 9, maxHeight: 132, overflowY: "auto" }}>
          {order.map((d) => {
            const g = groups[d];
            const pct = (n) => (g.total ? (n / g.total) * 100 : 0);
            return (
              <div key={d} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="eb-mono" style={{ width: 40, flex: "0 0 auto", fontSize: 11, fontWeight: 700, color: NAVY }}>{d}</span>
                <span style={{ flex: 1, display: "flex", height: 13, borderRadius: 4, overflow: "hidden", background: "#eef0f7" }}>
                  <i style={{ width: pct(g.ok) + "%", background: OK }} />
                  <i style={{ width: pct(g.notok) + "%", background: ORANGE }} />
                  <i style={{ width: pct(g.unshared) + "%", background: RED }} />
                  <i style={{ width: pct(g.other) + "%", background: "#c9cedb" }} />
                </span>
                <span className="eb-mono" style={{ width: 48, flex: "0 0 auto", textAlign: "right", fontSize: 11, color: "#9aa0ac" }}>{g.ok} / {g.total}</span>
              </div>
            );
          })}
        </div>}
      </div>
    );
  }

  // ============================================================ RESULTS TABLE
  // Every track is fractional, so the 11 columns always fit the dialog exactly —
  // no horizontal scroll at any width. Inst rides along in the Link cell and the
  // two plan coordinates share one cell; Elev and Angle keep their own.
  const COLS = "22px minmax(0,1.5fr) 52px 54px minmax(0,0.86fr) minmax(0,0.66fr) 82px 82px 74px 62px minmax(0,0.98fr) 92px";
  const HEAD = ["", "Link", "Disc", "Bldg", "Shared Site", "Workset", "N/S (m)", "E/W (m)", "Elev (m)", "Angle", "Reference (AR)", "Check"];
  const NUMERIC = [6, 7, 8, 9];
  const SELECT_COL = { 2: "disc", 11: "status" };

  function cellsOf(r, st, s) {
    const ref = rows().find((x) => x.id === Number(s.refs[r.id]));
    return ["", r.link + (r.inst ? " " + r.inst : ""), discOf(r, s), r.key,
      r.kind === "HOST" ? "-" : r.shared === "No" ? "Not Shared" : r.shared === "?" ? "—" : r.site,
      r.workset, fmt(r.ns), fmt(r.ew), fmt(r.el), r.ang === null ? "—" : r.ang.toFixed(4),
      ref ? ref.link : isAR(r, s) && r.placed ? "(baseline)" : "—", st];
  }

  function Results({ s, set }) {
    const rootRef = React.useRef(null);
    const [tall, setTall] = React.useState(true);
    React.useEffect(() => {
      const el = rootRef.current;
      if (!el || typeof ResizeObserver === "undefined") return;
      const measure = () => setTall(el.clientHeight >= 300);
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      measure();
      return () => ro.disconnect();
    }, []);
    const bars = s.showBars === null || s.showBars === undefined ? tall : s.showBars;
    const all = rows();
    const withStatus = all.map((r) => ({ r, st: statusOf(r, s) }));
    const counts = { notok: 0, unshared: 0, ok: 0, other: 0 };
    withStatus.forEach(({ r, st }) => {
      if (r.kind === "HOST" || isAR(r, s) || s.excluded[r.id]) return;
      if (st === "OK") counts.ok++; else if (st === "Not OK") counts.notok++; else if (st === "Not Shared") counts.unshared++; else counts.other++;
    });

    let list = withStatus;
    if (s.filter === "bad") list = list.filter(({ r, st }) => st === "Not OK" && !isAR(r, s));
    else if (s.filter === "unshared") list = list.filter(({ st }) => st === "Not Shared");
    else if (s.filter === "unresolved") list = list.filter(({ st }) => st === "Missing Ref" || st === "Unloaded");

    const active = s.cf.some((v) => v !== "");
    if (active) {
      list = list.filter(({ r, st }) => {
        const cells = cellsOf(r, st, s);
        return s.cf.every((v, i) => {
          if (v === "") return true;
          const cell = String(cells[i] === undefined ? "" : cells[i]);
          return SELECT_COL[i] ? cell === v : cell.toLowerCase().indexOf(v.toLowerCase()) >= 0;
        });
      });
    }
    if (s.sort.col > 0) {
      const c = s.sort.col, dir = s.sort.dir;
      list = list.slice().sort((a, b) => {
        if (NUMERIC.indexOf(c) >= 0) {
          const nv = (r) => [0, 0, 0, 0, 0, 0, r.ns, r.ew, r.el, r.ang][c];
          const av = nv(a.r), bv = nv(b.r);
          return ((av === null ? -Infinity : av) - (bv === null ? -Infinity : bv)) * dir;
        }
        return String(cellsOf(a.r, a.st, s)[c]).localeCompare(String(cellsOf(b.r, b.st, s)[c])) * dir;
      });
    }

    const refOptions = all.filter((r) => r.placed && (r.kind === "HOST" || isAR(r, s)));
    const sortBy = (c) => c > 0 && set({ sort: { col: c, dir: s.sort.col === c ? -s.sort.dir : 1 } });
    const setCf = (i, v) => { const next = s.cf.slice(); next[i] = v; set({ cf: next }); };
    const discs = Array.from(new Set(all.filter((r) => r.kind !== "HOST").map((r) => discOf(r, s)))).sort();
    const toggleRow = (id) => set({ excluded: { ...s.excluded, [id]: !s.excluded[id] } });
    const allShownIncluded = list.every(({ r }) => r.kind === "HOST" || !s.excluded[r.id]);
    const toggleAll = () => {
      const next = { ...s.excluded };
      list.forEach(({ r }) => { if (r.kind !== "HOST") next[r.id] = allShownIncluded; });
      set({ excluded: next });
    };
    const filtStyle = { width: "100%", minWidth: 0, height: 21, borderRadius: 5, border: "1px solid var(--eb-line)", background: "#fff", padding: "0 3px", fontFamily: "var(--font-mono)", fontSize: 9, color: "#4b5563", outline: "none", boxSizing: "border-box" };

    return (
      <div ref={rootRef} style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <Chart s={s} bars={bars} onToggle={() => set({ showBars: !bars })} />
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8, flexWrap: "wrap", flex: "0 0 auto" }}>
          <Chip on={s.filter === "all"} onClick={() => set({ filter: "all" })} count={all.length}>All rows</Chip>
          <Chip on={s.filter === "bad"} onClick={() => set({ filter: "bad" })} count={counts.notok}>Not coordinated</Chip>
          <Chip on={s.filter === "unshared"} onClick={() => set({ filter: "unshared" })} count={counts.unshared}>Not shared</Chip>
          <Chip on={s.filter === "unresolved"} onClick={() => set({ filter: "unresolved" })} count={counts.other}>Unresolved</Chip>
          <span style={{ flex: 1 }} />
          {tall && <span className="eb-mono" style={{ fontSize: 11, color: "#9aa0ac" }}>units: meters</span>}
          <span title="Untick a link to drop it from the graph, the report and the issue export — it stays in the table at half tone. Discipline and Reference (AR) are editable wherever detection got it wrong; the check re-runs live." style={{ display: "inline-flex", alignItems: "center" }}>
            <Icon name="Info" size={14} stroke={2} style={{ color: CYAN }} />
          </span>
          {active && <Button variant="ghost" size="sm" icon={<Icon name="FilterX" size={13} stroke={2} />} onClick={() => set({ cf: s.cf.map(() => "") })}>Clear filters</Button>}
          <Button variant="ghost" size="sm" icon={<Icon name="RotateCcw" size={13} stroke={2} />} onClick={() => set({ refs: autoRefs(), discOverride: {}, excluded: {} })}>Reset mapping</Button>
        </div>

        <div className="eb-scroll" style={{ ...card, overflowY: "auto", overflowX: "hidden", flex: 1, minHeight: 0 }}>
          <div style={{ position: "sticky", top: 0, zIndex: 3, background: "#f4f6fd", boxShadow: "0 1px 0 var(--eb-line)" }}>
            <div style={{ display: "grid", gridTemplateColumns: COLS }}>
              {HEAD.map((h, i) => (
                i === 0 ? (
                  <span key="chk" style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "6px 0", borderRight: "1px solid var(--eb-line-soft)" }}>
                    <Check on={allShownIncluded} onClick={toggleAll} />
                  </span>
                ) : (
                  <button key={h} onClick={() => sortBy(i)} title={"Sort by " + h}
                    style={{ display: "flex", alignItems: "center", gap: 2, justifyContent: NUMERIC.indexOf(i) >= 0 ? "flex-end" : "flex-start", padding: "6px 5px", background: "none", border: "none", borderRight: i < HEAD.length - 1 ? "1px solid var(--eb-line-soft)" : "none", cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 8.5, letterSpacing: "0.02em", textTransform: "uppercase", fontWeight: 700, color: s.sort.col === i ? NAVY : "#8b93a7", textAlign: "left", overflow: "hidden", minWidth: 0 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h}</span>
                    {s.sort.col === i && <span style={{ color: CYAN }}>{s.sort.dir === 1 ? "▲" : "▼"}</span>}
                  </button>
                )
              ))}
            </div>
            <div style={{ display: tall ? "grid" : "none", gridTemplateColumns: COLS, borderTop: "1px solid var(--eb-line-soft)" }}>
              {HEAD.map((h, i) => (
                <span key={"f" + i} style={{ padding: "4px 3px", borderRight: i < HEAD.length - 1 ? "1px solid var(--eb-line-soft)" : "none", display: "flex", minWidth: 0 }}>
                  {i === 0 ? null : SELECT_COL[i] ? (
                    <select value={s.cf[i]} onChange={(e) => setCf(i, e.target.value)} style={filtStyle}>
                      <option value="">All</option>
                      {(SELECT_COL[i] === "disc" ? discs : STATUSES).map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input value={s.cf[i]} onChange={(e) => setCf(i, e.target.value)} placeholder="Filter" style={filtStyle}
                      onFocus={(e) => { e.target.style.borderColor = CYAN; }} onBlur={(e) => { e.target.style.borderColor = "var(--eb-line)"; }} />
                  )}
                </span>
              ))}
            </div>
          </div>

          {list.length === 0 && <div style={{ padding: "24px 14px", textAlign: "center", fontSize: 12.5, color: "#9aa0ac" }}>No rows match this filter.</div>}
          {list.map(({ r, st }, i) => {
            const isHost = r.kind === "HOST";
            const off = !isHost && s.excluded[r.id];
            const bad = st === "Not OK" || st === "Not Shared";
            const cell = { padding: "5px 5px", fontSize: 10.5, color: "#4b5563", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", display: "flex", alignItems: "center", minWidth: 0 };
            const num = { ...cell, justifyContent: "flex-end", fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontSize: 9.5, flexShrink: 0 };
            return (
              <div key={r.id} style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", borderBottom: i < list.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: isHost ? "rgba(30,36,140,0.05)" : bad && !off ? "rgba(224,133,30,0.06)" : "transparent", opacity: off ? 0.38 : r.placed ? 1 : 0.6 }}>
                <span style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {!isHost && <Check on={!off} onClick={() => toggleRow(r.id)} />}
                </span>
                <div style={{ ...cell, gap: 5 }} title={r.link + (r.inst ? " " + r.inst : "")}>
                  <Icon name={isHost ? "ProjectBasePoint" : r.placed ? "Link2" : "Link2Off"} size={11} stroke={1.9} style={{ color: isHost ? NAVY : isAR(r, s) ? CYAN : "#b6bdd0", flex: "0 0 auto" }} />
                  <b className="eb-mono" style={{ fontSize: 10, fontWeight: 700, color: isHost ? NAVY : "#1f2937", overflow: "hidden", textOverflow: "ellipsis" }}>{r.link}</b>
                  {r.inst && <span className="eb-mono" style={{ fontSize: 9, color: "#9aa0ac", flex: "0 0 auto" }}>{r.inst}</span>}
                </div>
                <div style={{ ...cell, padding: "3px 3px" }}>
                  {isHost ? <span className="eb-mono" style={{ fontSize: 10, color: MUTE }}>-</span> : (
                    <select value={discOf(r, s)} onChange={(e) => set({ discOverride: { ...s.discOverride, [r.id]: e.target.value } })}
                      title="Fix the discipline if it was not detected correctly"
                      style={{ width: "100%", height: 21, borderRadius: 5, border: "1px solid " + (s.discOverride[r.id] !== undefined ? CYAN : "transparent"), background: s.discOverride[r.id] !== undefined ? "rgba(68,184,211,0.08)" : "transparent", padding: 0, fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 700, color: isAR(r, s) ? CYAN : "#4b5563", outline: "none", cursor: "pointer", minWidth: 0 }}>
                      {DISC_CODES.concat(DISC_CODES.indexOf(r.disc) < 0 ? [r.disc] : []).map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  )}
                </div>
                <div className="eb-mono" style={{ ...cell, fontSize: 10 }} title={r.key}>{r.key}</div>
                <div style={{ ...cell, fontSize: 10, color: r.shared === "No" ? RED : r.shared === "?" ? "#9aa0ac" : "#4b5563" }} title={r.site}>{isHost ? "-" : r.shared === "No" ? "Not Shared" : r.shared === "?" ? "—" : r.site}</div>
                <div className="eb-mono" style={{ ...cell, fontSize: 9.5, color: "#6b7280" }} title={r.workset}>{r.workset}</div>
                <div style={num} title={"N/S " + fmt(r.ns)}>{fmt(r.ns)}</div>
                <div style={num} title={"E/W " + fmt(r.ew)}>{fmt(r.ew)}</div>
                <div style={{ ...num, fontWeight: 700, color: "#1f2937", fontSize: 10.5 }}>{fmt(r.el)}</div>
                <div style={{ ...num, fontWeight: 700, color: "#1f2937", fontSize: 10.5 }} title={r.ang === null ? "" : r.ang.toFixed(4) + "°"}>{r.ang === null ? "—" : r.ang.toFixed(3)}</div>
                <div style={{ ...cell, padding: "3px 3px" }}>
                  {r.kind === "Link" && r.placed && !isAR(r, s) ? (
                    <select value={s.refs[r.id] === undefined ? "" : s.refs[r.id]} onChange={(e) => set({ refs: { ...s.refs, [r.id]: e.target.value === "" ? "" : Number(e.target.value) } })}
                      style={{ width: "100%", height: 21, borderRadius: 5, border: "1px solid " + (st === "Missing Ref" ? "rgba(224,133,30,0.5)" : "var(--eb-line)"), background: "#fff", padding: "0 2px", fontFamily: "var(--font-mono)", fontSize: 9, color: NAVY, outline: "none", minWidth: 0 }}>
                      <option value="">— none —</option>
                      {refOptions.map((o) => <option key={o.id} value={o.id}>{(o.kind === "HOST" ? "HOST: " : "") + o.link}</option>)}
                    </select>
                  ) : <span style={{ fontSize: 10, color: "#9aa0ac", fontStyle: "italic" }}>{isAR(r, s) && r.placed ? "(baseline)" : "—"}</span>}
                </div>
                <div style={{ ...cell, padding: "5px 5px", flexShrink: 0 }}><StatusPill s={st} /></div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // ============================================================ SHELL GLUE
  function Body({ state, setState }) {
    const set = (patch) => setState({ ...state, ...patch });
    const X = window.PBPExport || {};
    if (state.stage === "setup") return <Setup s={state} set={set} />;
    if (state.stage === "export" && X.Export) return <X.Export s={state} set={set} />;
    if (state.stage === "issues" && X.Issues) return <X.Issues s={state} set={set} />;
    if (state.stage === "done" && X.Done) return <X.Done s={state} set={set} />;
    return <Results s={state} set={set} />;
  }

  function footer(close, state, setState) {
    const set = (patch) => setState({ ...state, ...patch });
    const X = window.PBPExport || {};
    const inScope = (st) => rows().filter((r) => !state.excluded[r.id] && statusOf(r, state) === st).length;
    const notOk = inScope("Not OK"), unshared = inScope("Not Shared");

    if (state.stage === "setup") {
      return (
        <React.Fragment>
          <span style={{ flex: 1, fontSize: 12, color: "#9aa0ac", fontFamily: "var(--font-mono)" }}>{state.tolMm} mm / {state.tolDeg}°{state.opts.notHub ? " · external hub" : ""}</span>
          <Button variant="ghost" size="md" onClick={close}>Cancel</Button>
          <Button variant="primary" size="md" icon={<Icon name="Play" size={14} stroke={2.2} />} onClick={() => set({ stage: "results" })}>Run audit</Button>
        </React.Fragment>
      );
    }
    if (state.stage === "results") {
      return (
        <React.Fragment>
          <span style={{ flex: 1, display: "flex", alignItems: "center", gap: 14, fontSize: 12, fontFamily: "var(--font-mono)" }}>
            <span style={{ color: notOk ? ORANGE : OK }}>{notOk} link{notOk === 1 ? "" : "s"} not coordinated</span>
            <span style={{ color: unshared ? RED : OK }}>{unshared} link{unshared === 1 ? "" : "s"} not in Shared Coordinates</span>
          </span>
          <Button variant="ghost" size="md" onClick={() => set({ stage: "setup" })}>Settings</Button>
          <Button variant="primary" size="md" icon={<Icon name="Download" size={15} stroke={2} />} onClick={() => set({ stage: "export" })}>Export report…</Button>
        </React.Fragment>
      );
    }
    return X.footer ? X.footer(close, state, setState) : null;
  }

  function initialState() {
    const p = loadPrefs();
    return {
      stage: "setup",
      tolMm: p.tolMm !== undefined ? p.tolMm : 1,
      tolDeg: p.tolDeg !== undefined ? p.tolDeg : 0.02,
      advOpen: false,
      opts: Object.assign({ unloaded: true, log: true, remember: true, notHub: false }, p.opts),
      refs: autoRefs(),
      discOverride: {},
      excluded: {},
      filter: "all",
      cf: new Array(12).fill(""),
      sort: { col: -1, dir: 1 },
      showBars: null,
      exp: { formats: { html: true, pdf: false, xlsx: false }, onlyIssues: false, includeUnresolved: true, chart: true, open: true, folder: p.folder || "C:\\Projects\\MSH\\04_Coordination\\PBP Reports" },
      acc: Object.assign({
        title: "QA - Project Base Point",
        titleShared: "QA - Acquire Coordinates",
        status: "Open", category: "Design", type: "BIM Quality", assigneeType: "role",
        dueDays: 7,
        descElev: "מפלס נקודת הבסיס אינו תואם את המודל האדריכלי.",
        descAngle: "הזווית לצפון אינה תואמת את המודל האדריכלי.",
        descBoth: "מפלס נקודת הבסיס והזווית לצפון אינם תואמים את המודל האדריכלי.",
        descShared: "יש לרכוש קורידנטות ממודל URS או מהמודל האדירלכי",
        fieldMap: { discipline: "Discipline", level: "", building: "EAB_Location" },
        cfMode: "none", cfText: "", cfList: [], cfRole: {}, extra: {}, rowEdit: {},
        roles: {}, heb: {}, companies: {},
        rolesText: "", companiesText: "", discovered: [],
        template: "", loaded: false,
      }, p.acc),
    };
  }

  function banner(state) {
    if (state.stage === "setup") return "Reads the Project Base Point of the host model and every directly-placed link, then checks each discipline model against its matching Architecture reference. Nested links are excluded.";
    if (state.stage === "results") return null;
    if (state.stage === "export") return "The report replaces the old Dynamo HTML output — pick any combination of formats. The Excel sheet is an ACC issue import for Format.";
    if (state.stage === "issues") return "Every value below is written into the ACC import sheet. Anything your project names differently — statuses, types, roles, custom fields — can be corrected here before export.";
    return null;
  }

  Object.assign(window, {
    PBPCore: { rows, host, fmt, angDiff, discOf, isAR, statusOf, deltasOf, autoRefs, cellsOf, TONE, BG, STATUSES, ISSUE_STATUSES, DISC_MAP, DISC_CODES, HEB_OPTIONS, autoRole, autoHeb, HUB_ROLES, NAVY, CYAN, OK, ORANGE, RED, MUTE, card, sectionLabel, StatusPill, Toggle, Check, Chip, Disclosure, savePrefs, loadPrefs },
  });

  window.BasePoints = {
    title: "Project Base Points",
    subtitle: "Host model & all links · coordination audit · engine " + ENGINE,
    width: 1120,
    bodyFill: (state) => state.stage === "results",
    Body, footer, initialState, banner,
  };
})();
