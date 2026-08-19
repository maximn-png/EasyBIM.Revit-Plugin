// ribbon-base-points-export.jsx
// Export + ACC issue-sheet wizard for the Project Base Points audit.
// Registers window.PBPExport = { Export, Issues, Done, footer }.
//
// The Excel path targets Autodesk Construction Cloud's "Import issues" template
// (Title · Status · Category · Type · Description · Assigned To · Assignee Type ·
// Location · Location Details · Due Date · Start Date · Root Cause Category ·
// Root Cause, plus project custom fields to the right). Every project names
// these differently, so every value here is editable before writing the file.

(function () {
  const DS = window.EasyBIMDesignSystem_a35564 || {};
  const { Button, Badge } = DS;
  const { Icon } = window;
  const C = window.PBPCore;
  const { rows, host, discOf, isAR, statusOf, deltasOf, ISSUE_STATUSES, HUB_ROLES, HEB_OPTIONS, autoRole, autoHeb, NAVY, CYAN, OK, ORANGE, RED, MUTE, card, sectionLabel, Toggle, Disclosure, savePrefs } = C;

  const FORMATS = [
    { id: "html", icon: "FileCode2", name: "Interactive HTML", desc: "Sortable, filterable report with the coordination chart. Opens in the browser.", ext: ".html", done: "Interactive HTML report written" },
    { id: "pdf", icon: "FileText", name: "PDF", desc: "Print-ready A4 landscape snapshot of the chart and table as filtered here — for issue packs and transmittals.", ext: ".pdf", done: "PDF report written" },
    { id: "xlsx", icon: "FileSpreadsheet", name: "Excel · ACC issue import", desc: "One row per flagged link on Format's (ACC) issue import template — imports straight into the project's issue list.", ext: ".xlsx", done: "ACC issue sheet written" },
  ];
  // ACC's own Location column is never written — it resolves against the project's
  // Locations breakdown structure, which is separate admin setup. The column stays
  // in the sheet (ACC requires every template column) but always empty.
  const ACC_COLS = ["Title", "Status", "Category", "Type", "Description", "Assigned To", "Assignee Type", "Location", "Location Details", "Due Date", "Start Date", "Root Cause Category", "Root Cause"];
  const PREV_STD = [["Title", "Title", "minmax(140px,1.5fr)"], ["Description", "Description", "minmax(190px,2.4fr)"], ["Status", "Status", "78px"], ["Assigned To", "Assigned To", "minmax(104px,1.1fr)"], ["Category", "Category", "86px"], ["Type", "Type", "94px"], ["Start Date", "Start Date", "112px"], ["Due Date", "Due Date", "112px"]];
  const PDG = "58px minmax(0,1fr) minmax(0,1fr) 60px";
  const PDG_CO = "58px minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 60px";

  const fileBase = (s) => host().title + "_PBP_Report_" + (host().generated || "").replace(" ", "_");
  const chosen = (s) => FORMATS.filter((f) => s.exp.formats[f.id]);
  const iso = (d) => d.toISOString().slice(0, 10);
  const today = () => new Date(2026, 7, 3);

  const fieldWrap = { display: "grid", gap: 3 };
  const fieldLabel = { fontFamily: "var(--font-mono)", fontSize: 9.5, letterSpacing: "0.05em", textTransform: "uppercase", color: "#9aa0ac" };
  const inp = { width: "100%", height: 30, borderRadius: 8, border: "1px solid var(--eb-line)", background: "#fff", padding: "0 9px", fontFamily: "var(--font-body)", fontSize: 12.5, color: "#1f2937", outline: "none", boxSizing: "border-box" };

  function Field({ label, value, onChange, mono, dir, w }) {
    return (
      <label style={{ ...fieldWrap, width: w }}>
        <span style={fieldLabel}>{label}</span>
        <input value={value} dir={dir} onChange={(e) => onChange(e.target.value)}
          style={{ ...inp, fontFamily: mono ? "var(--font-mono)" : "var(--font-body)", textAlign: dir === "rtl" ? "right" : "left" }}
          onFocus={(e) => { e.target.style.borderColor = CYAN; e.target.style.boxShadow = "0 0 0 3px rgba(68,184,211,0.16)"; }}
          onBlur={(e) => { e.target.style.borderColor = "var(--eb-line)"; e.target.style.boxShadow = "none"; }} />
      </label>
    );
  }
  function Area({ label, value, onChange, note }) {
    return (
      <label style={{ ...fieldWrap, marginBottom: 10 }}>
        <span style={fieldLabel}>{label}</span>
        <textarea value={value} dir="rtl" rows={2} onChange={(e) => onChange(e.target.value)}
          style={{ ...inp, height: "auto", padding: "7px 9px", lineHeight: 1.5, textAlign: "right", resize: "vertical" }}
          onFocus={(e) => { e.target.style.borderColor = CYAN; }} onBlur={(e) => { e.target.style.borderColor = "var(--eb-line)"; }} />
        {note && <span style={{ fontSize: 11, color: "#9aa0ac" }}>{note}</span>}
      </label>
    );
  }

  const ACC_LOC = "@location";
  // Custom fields a report example might expose on another hub.
  const SIMULATED_TEMPLATE_FIELDS = ["Discipline", "EAB_Level", "EAB_Location", "Work Package", "Sub-Contractor"];
  const splitList = (t) => String(t || "").split(",").map((x) => x.trim()).filter(Boolean);
  // Roles and companies are the same slot — the assignee. Only one list is ever asked for.
  const assigneeList = (a) => (a.assigneeType === "company" ? splitList(a.companiesText) : (a.rolesText ? splitList(a.rolesText) : HUB_ROLES));
  const roleList = assigneeList;
  const companyList = (a) => splitList(a.companiesText);

  // ---- custom fields ----
  const cfFields = (a) => (a.cfMode === "manual" ? splitList(a.cfText) : a.cfMode === "import" ? (a.cfList || []) : []);
  const cfTargetOf = (a, kind) => cfFields(a).filter((f) => a.cfRole[f] === kind)[0] || "";
  const cfFree = (a) => cfFields(a).filter((f) => !a.cfRole[f]);
  // Best-effort auto-map by column name so דיספלינה and Location fill themselves.
  function autoCfRole(fields) {
    const map = {};
    let gotD = false, gotB = false;
    fields.forEach((f) => {
      const n = String(f).toLowerCase();
      if (!gotD && (/disciplin/.test(n) || f.indexOf("דיספלינה") >= 0 || /trade/.test(n))) { map[f] = "discipline"; gotD = true; return; }
      if (!gotB && (/location|building|zone|area|block/.test(n) || f.indexOf("מיקום") >= 0)) { map[f] = "building"; gotB = true; return; }
      map[f] = "";
    });
    return map;
  }

  // ---- the rows that will be written into the sheet ----
  // Missing Ref is deliberately absent: no Architecture reference is a setup
  // problem to fix in Revit, not an issue to raise in Format.
  function issueRows(s) {
    const a = s.acc, tol = (Number(s.tolMm) || 1) / 1000, tolDeg = Number(s.tolDeg) || 0.02;
    const due = iso(new Date(today().getTime() + (Number(a.dueDays) || 7) * 86400000));
    return rows().filter((r) => r.kind !== "HOST" && !s.excluded[r.id] && ISSUE_STATUSES.indexOf(statusOf(r, s)) >= 0)
      .map((r) => {
        const st = statusOf(r, s), disc = discOf(r, s), d = deltasOf(r, s);
        const gaps = [];
        let badEl = false, badAng = false;
        if (st === "Not OK" && d) {
          const plan = Math.sqrt(d.ns * d.ns + d.ew * d.ew);
          badEl = Math.abs(d.el) > tol || plan > tol;
          badAng = d.ang > tolDeg;
          if (Math.abs(d.el) > tol) gaps.push("מפלס " + d.el.toFixed(3) + " מ׳");
          if (badAng) gaps.push("זווית לצפון " + d.ang.toFixed(4) + "°");
          if (plan > tol) gaps.push("מיקום במישור " + plan.toFixed(3) + " מ׳");
        }
        const reason = st === "Not Shared" ? "shared" : badEl && badAng ? "both" : badAng ? "angle" : "elev";
        const base = st === "Not Shared" ? a.descShared
          : reason === "both" ? a.descBoth : reason === "angle" ? a.descAngle : a.descElev;
        const desc = st === "Not Shared" ? base
          : base + (gaps.length ? " פערים: " + gaps.join(", ") + "." : "") + (d ? " יש לעדכן בהתאם למודל " + d.ref.link + "." : "");
        return {
          id: r.id, link: r.link, st: st, disc: disc, gaps: gaps, reason: reason,
          Title: st === "Not Shared" ? a.titleShared : a.title,
          Status: a.status, Category: a.category, Type: a.type,
          Description: desc,
          "Assigned To": a.assigneeType === "company" ? (a.companies[disc] || "") : (a.roles[disc] !== undefined ? a.roles[disc] : autoRole(disc)),
          Role: a.roles[disc] !== undefined ? a.roles[disc] : autoRole(disc),
          "Assignee Type": a.assigneeType,
          Location: "",
          "Location Details": r.link + (r.inst ? " " + r.inst : "") + (d ? " ↔ " + d.ref.link : ""),
          "Due Date": due, "Start Date": iso(today()),
          "Root Cause Category": "", "Root Cause": "",
          discipline: a.heb[disc] !== undefined ? a.heb[disc] : autoHeb(disc),
          building: r.key || "",
          extra: (a.extra && a.extra[r.id]) || {},
        };
      })
      // Anything hand-edited in the preview wins over the computed value.
      .map((x) => Object.assign(x, (a.rowEdit && a.rowEdit[x.id]) || {}));
  }

  // ============================================================ FORMAT CHOICE
  function Export({ s, set }) {
    const notOk = rows().filter((r) => !s.excluded[r.id] && statusOf(r, s) === "Not OK");
    const unshared = rows().filter((r) => !s.excluded[r.id] && statusOf(r, s) === "Not Shared");
    const unres = rows().filter((r) => !s.excluded[r.id] && statusOf(r, s) === "Missing Ref");
    const picks = chosen(s);
    const hasXlsx = !!s.exp.formats.xlsx;
    const hasDoc = !!s.exp.formats.html || !!s.exp.formats.pdf;
    const included = rows().filter((r) => r.kind === "HOST" || !s.excluded[r.id]).length;
    const docScope = s.exp.onlyIssues ? notOk.length + unshared.length + (s.exp.includeUnresolved ? unres.length : 0) : included;

    return (
      <React.Fragment>
        <p style={sectionLabel}>Formats · pick any combination</p>
        <div style={{ display: "grid", gap: 9, marginBottom: 15 }}>
          {FORMATS.map((f) => {
            const on = !!s.exp.formats[f.id];
            return (
              <button key={f.id} onClick={() => set({ exp: { ...s.exp, formats: { ...s.exp.formats, [f.id]: !on } } })}
                style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 14px", borderRadius: "var(--radius-lg)", border: "1.5px solid " + (on ? CYAN : "var(--eb-line)"), background: on ? "rgba(68,184,211,0.06)" : "#fff", cursor: "pointer", textAlign: "left", fontFamily: "var(--font-body)", boxShadow: on ? "0 2px 10px rgba(68,184,211,0.14)" : "none", transition: "all 150ms" }}>
                <span style={{ width: 36, height: 36, borderRadius: 9, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: on ? "linear-gradient(135deg,#1e248c,#44b8d3)" : "#f4f5fb" }}>
                  <Icon name={f.icon} size={17} stroke={1.8} style={{ color: on ? "#fff" : "#9aa0ac" }} />
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: on ? NAVY : "#374151" }}>{f.name}</span>
                    <span className="eb-mono" style={{ fontSize: 10.5, color: "#9aa0ac" }}>{f.ext}</span>
                    {f.id === "xlsx" && on && <Badge tone="info">needs issue fields</Badge>}
                  </span>
                  <span style={{ display: "block", fontSize: 11.5, color: "#6b7280", marginTop: 3, lineHeight: 1.45 }}>{f.desc}</span>
                </span>
                <span style={{ width: 17, height: 17, flex: "0 0 auto", marginTop: 9, borderRadius: 5, border: "1.5px solid " + (on ? CYAN : "#d5d9e6"), background: on ? CYAN : "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
                  {on && <Icon name="Check" size={11} stroke={3.4} style={{ color: "#fff" }} />}
                </span>
              </button>
            );
          })}
        </div>

        <p style={sectionLabel}>Contents of the HTML / PDF report</p>
        <div style={{ ...card, overflow: "hidden", marginBottom: 15, opacity: hasDoc ? 1 : 0.5 }}>
          {[["onlyIssues", "Only rows needing attention", notOk.length + " not coordinated · " + unshared.length + " not shared"],
            ["includeUnresolved", "Include Missing Ref rows", unres.length + " row" + (unres.length === 1 ? "" : "s") + " with no Architecture reference — never exported as issues"],
            ["chart", "Include the coordination chart", "Stacked bar per discipline"],
            ["open", "Open the files when finished", "Launches each report in its default application"]].map(([k, t, d], i, arr) => {
            const dim = !hasDoc && k !== "open" ? true : (k === "includeUnresolved" && !s.exp.onlyIssues);
            return (
              <div key={k} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, padding: "10px 14px", borderBottom: i < arr.length - 1 ? "1px solid var(--eb-line-soft)" : "none", opacity: dim ? 0.5 : 1 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, color: "#374151", fontWeight: 500 }}>{t}</div>
                  <div style={{ fontSize: 11.5, color: "#9aa0ac", marginTop: 2 }}>{d}</div>
                </div>
                <Toggle on={!dim && s.exp[k]} onClick={dim ? undefined : () => set({ exp: { ...s.exp, [k]: !s.exp[k] } })} />
              </div>
            );
          })}
        </div>
        {hasXlsx && (
          <p style={{ fontSize: 11.5, color: "#9aa0ac", margin: "-6px 2px 15px", display: "flex", alignItems: "center", gap: 6 }}>
            <Icon name="Lock" size={12} stroke={2} style={{ color: CYAN }} />
            The issue sheet always carries only the {notOk.length + unshared.length} flagged link{notOk.length + unshared.length === 1 ? "" : "s"}, whatever is set above.
          </p>
        )}

        <p style={sectionLabel}>Destination · remembered for next run</p>
        <div style={{ ...card, display: "flex", alignItems: "center", gap: 10, padding: "9px 10px 9px 14px", marginBottom: 12 }}>
          <Icon name="Folder" size={15} stroke={1.9} style={{ color: NAVY, flex: "0 0 auto" }} />
          <input value={s.exp.folder} onChange={(e) => set({ exp: { ...s.exp, folder: e.target.value } })}
            style={{ ...inp, height: 26, border: "none", padding: 0, fontFamily: "var(--font-mono)", fontSize: 11.5, color: "#4b5563" }} />
          <Button variant="secondary" size="sm" onClick={() => {}}>Browse…</Button>
        </div>
        <div style={{ display: "grid", gap: 5 }}>
          {picks.length === 0 && <span style={{ fontSize: 12, color: ORANGE, fontFamily: "var(--font-mono)" }}>pick at least one format</span>}
          {picks.map((f) => (
            <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12, color: "#6b7280" }}>
              <Icon name="File" size={14} stroke={1.9} style={{ color: "#9aa0ac" }} />
              <span className="eb-mono" style={{ color: NAVY, fontWeight: 600 }}>{fileBase(s)}{f.ext}</span>
              <span>· {f.id === "xlsx" ? notOk.length + unshared.length : docScope} row{(f.id === "xlsx" ? notOk.length + unshared.length : docScope) === 1 ? "" : "s"}</span>
            </div>
          ))}
        </div>
      </React.Fragment>
    );
  }

  // ============================================================ ACC ISSUE FIELDS
  function Issues({ s, set }) {
    const a = s.acc;
    const setA = (patch) => set({ acc: { ...a, ...patch } });
    const list = issueRows(s);
    const discsUsed = Array.from(new Set(list.map((r) => r.disc))).sort();
    const missingRole = discsUsed.filter((d) => !(a.roles[d] !== undefined ? a.roles[d] : autoRole(d)));
    const missingCo = a.assigneeType === "company" ? discsUsed.filter((d) => !a.companies[d]) : [];
    const isCompany = a.assigneeType === "company";
    const due = iso(new Date(today().getTime() + (Number(a.dueDays) || 7) * 86400000));
    const fields = cfFields(a);
    const dTarget = cfTargetOf(a, "discipline"), bTarget = cfTargetOf(a, "building");
    const free = cfFree(a);
    const aList = assigneeList(a);
    const setRole = (f, v) => {
      const next = { ...a.cfRole };
      if (v) Object.keys(next).forEach((k) => { if (next[k] === v) next[k] = ""; });
      next[f] = v;
      setA({ cfRole: next });
    };
    const setExtra = (id, f, v) => setA({ extra: { ...a.extra, [id]: { ...(a.extra[id] || {}), [f]: v } } });
    const setRowEdit = (id, k, v) => setA({ rowEdit: { ...a.rowEdit, [id]: { ...((a.rowEdit || {})[id] || {}), [k]: v } } });

    // ---- preview table: columns, selection, sort, filter, bulk edit ----
    const pcols = fields.map((f) => ({ key: "cf:" + f, label: f, kind: a.cfRole[f] || "free", field: f, w: "minmax(110px,1fr)", cf: true }))
      .concat(PREV_STD.map((c) => ({ key: c[0], label: c[1], kind: c[0] === "Start Date" || c[0] === "Due Date" ? "date" : "std", w: c[2] })));
    const valOf = (x, c) => c.kind === "discipline" ? x.discipline : c.kind === "building" ? x.building : c.kind === "free" ? ((x.extra && x.extra[c.field]) || "") : (x[c.key] || "");
    const editKeyOf = (c) => c.kind === "discipline" ? "discipline" : c.kind === "building" ? "building" : c.key;
    const setVal = (id, c, v) => c.kind === "free" ? setExtra(id, c.field, v) : setRowEdit(id, editKeyOf(c), v);

    const sel = s.prevSel || {};
    const pf = s.prevF || {};
    const psort = s.prevSort || { i: -1, dir: 1 };
    let plist = list.filter((x) => pcols.every((c, i) => !pf[i] || String(valOf(x, c)).toLowerCase().indexOf(String(pf[i]).toLowerCase()) >= 0));
    if (psort.i >= 0 && pcols[psort.i]) plist = plist.slice().sort((p, q) => String(valOf(p, pcols[psort.i])).localeCompare(String(valOf(q, pcols[psort.i])), "he") * psort.dir);
    const selIds = plist.filter((x) => sel[x.id]).map((x) => x.id);
    const allSel = plist.length > 0 && selIds.length === plist.length;
    const bulkCol = pcols[s.prevBulk === undefined || !pcols[s.prevBulk] ? 0 : s.prevBulk] || pcols[0];
    const applyBulk = () => {
      const v = s.prevBulkVal || "";
      const nextEdit = { ...(a.rowEdit || {}) }, nextExtra = { ...(a.extra || {}) };
      selIds.forEach((id) => {
        if (bulkCol.kind === "free") nextExtra[id] = { ...(nextExtra[id] || {}), [bulkCol.field]: v };
        else nextEdit[id] = { ...(nextEdit[id] || {}), [editKeyOf(bulkCol)]: v };
      });
      setA({ rowEdit: nextEdit, extra: nextExtra });
    };
    const prevFiltered = Object.keys(pf).some((k) => pf[k]);
    const CF_MODES = [["none", "No custom fields", "Ban"], ["manual", "Type them manually", "Keyboard"], ["import", "Import a report example", "Upload"]];
    const prevCols = "26px " + pcols.map((c) => c.w).join(" ");

    return (
      <React.Fragment>
        <p style={sectionLabel}>Custom fields on this hub</p>
        <div style={{ ...card, padding: "12px 14px", marginBottom: 13 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginBottom: fields.length || a.cfMode !== "none" ? 12 : 0 }}>
            {CF_MODES.map(([id, label, ic]) => {
              const on = a.cfMode === id;
              return (
                <button key={id} onClick={() => setA({ cfMode: id, cfText: "", cfList: [], cfRole: {}, extra: {} })}
                  style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 11px", borderRadius: "var(--radius-md)", border: "1.5px solid " + (on ? CYAN : "var(--eb-line)"), background: on ? "rgba(68,184,211,0.07)" : "#fff", cursor: "pointer", fontFamily: "var(--font-body)", textAlign: "left" }}>
                  <Icon name={ic} size={15} stroke={1.9} style={{ color: on ? NAVY : "#9aa0ac", flex: "0 0 auto" }} />
                  <span style={{ fontSize: 12.5, fontWeight: on ? 600 : 500, color: on ? NAVY : "#4b5563" }}>{label}</span>
                </button>
              );
            })}
          </div>

          {a.cfMode === "manual" && (
            <div style={{ marginBottom: fields.length ? 12 : 0 }}>
              <p style={{ ...sectionLabel, margin: "0 0 5px" }}>Field names · comma separated, spelled exactly as configured in the project</p>
              <textarea value={a.cfText} rows={2} placeholder="דיספלינה, Location, Work Package, …"
                onChange={(e) => { const list = splitList(e.target.value); setA({ cfText: e.target.value, cfRole: Object.assign(autoCfRole(list), Object.keys(a.cfRole).reduce((o, k) => { if (list.indexOf(k) >= 0 && a.cfRole[k]) o[k] = a.cfRole[k]; return o; }, {})) }); }}
                style={{ ...inp, height: "auto", padding: "7px 9px", fontFamily: "var(--font-mono)", fontSize: 11.5, lineHeight: 1.5, resize: "vertical" }} />
            </div>
          )}
          {a.cfMode === "import" && (
            <div style={{ marginBottom: fields.length ? 12 : 0 }}>
              <p style={{ ...sectionLabel, margin: "0 0 5px" }}>Issue report exported from the target project</p>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input value={a.template} onChange={(e) => setA({ template: e.target.value })} placeholder="…\Issue summary-XXXXXX.xlsx"
                  style={{ ...inp, flex: 1, fontFamily: "var(--font-mono)", fontSize: 11.5 }} />
                <Button variant="secondary" size="sm" icon={<Icon name="Upload" size={13} stroke={2} />}
                  onClick={() => setA({ cfList: SIMULATED_TEMPLATE_FIELDS, cfRole: autoCfRole(SIMULATED_TEMPLATE_FIELDS) })}>Read fields</Button>
              </div>
              <div style={{ fontSize: 11, color: fields.length ? OK : ORANGE, marginTop: 5, fontFamily: "var(--font-mono)" }}>
                {fields.length ? fields.length + " custom field(s) found in the report" : "no report read yet"}
              </div>
            </div>
          )}

          {fields.length > 0 && (
            <React.Fragment>
              <p style={{ ...sectionLabel, margin: "0 0 6px" }}>Map them · דיספלינה and Location are matched automatically where the name allows</p>
              <div style={{ display: "grid", gap: 5 }}>
                {fields.map((f) => (
                  <div key={f} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 16px 190px minmax(0,1fr)", alignItems: "center", gap: 9 }}>
                    <span className="eb-mono" style={{ fontSize: 11.5, fontWeight: 700, color: NAVY, background: "rgba(30,36,140,0.06)", borderRadius: 6, padding: "5px 8px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{f}</span>
                    <Icon name="ArrowRight" size={13} style={{ color: a.cfRole[f] ? CYAN : "#d5d9e6" }} />
                    <select value={a.cfRole[f] || ""} onChange={(e) => setRole(f, e.target.value)} style={{ ...inp, height: 27, fontSize: 12 }}>
                      <option value="">— fill per issue below —</option>
                      <option value="discipline">דיספלינה</option>
                      <option value="building">Location</option>
                    </select>
                    <span style={{ fontSize: 11, color: "#9aa0ac" }}>
                      {a.cfRole[f] === "discipline" ? "from the discipline code of each link" : a.cfRole[f] === "building" ? "from the Building column of the report" : "editable per issue in the preview"}
                    </span>
                  </div>
                ))}
              </div>
            </React.Fragment>
          )}
          {fields.length === 0 && a.cfMode !== "none" && (
            <p style={{ fontSize: 11.5, color: "#9aa0ac", margin: 0, lineHeight: 1.5 }}>No fields configured yet — דיספלינה and Location each need one to be written into.</p>
          )}
        </div>

        <p style={sectionLabel}>Issue defaults · must match the values configured in the project</p>
        <div style={{ ...card, padding: "12px 14px", marginBottom: 13, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(158px,1fr))", gap: 10 }}>
          <Field label="Title · base point mismatch" value={a.title} onChange={(v) => setA({ title: v })} />
          <Field label="Title · not in shared coordinates" value={a.titleShared} onChange={(v) => setA({ titleShared: v })} />
          <Field label="Status" value={a.status} onChange={(v) => setA({ status: v })} />
          <Field label="Category" value={a.category} onChange={(v) => setA({ category: v })} />
          <Field label="Type" value={a.type} onChange={(v) => setA({ type: v })} />
          <label style={fieldWrap}>
            <span style={fieldLabel}>Assignee Type</span>
            <select value={a.assigneeType} onChange={(e) => setA({ assigneeType: e.target.value })} style={inp}>
              <option value="role">role</option><option value="company">company</option>
            </select>
          </label>
          <Field label="Due in (days)" value={a.dueDays} onChange={(v) => setA({ dueDays: v })} mono />
        </div>

        <div style={{ ...card, padding: "11px 14px", marginBottom: 13 }}>
          <p style={{ ...sectionLabel, margin: "0 0 5px" }}>
            {isCompany ? "Company names on this hub" : "Role names on this hub"} · comma separated, spelled exactly as in the project — this is the Assigned To list
          </p>
          <textarea rows={2}
            value={isCompany ? a.companiesText : a.rolesText}
            placeholder={isCompany ? "NML Architects, Stierler Engineering, Maof MEP, SMO Electric, …" : "Architect, Structural Engineer, Electrical Engineer, …"}
            onChange={(e) => setA(isCompany ? { companiesText: e.target.value } : { rolesText: e.target.value })}
            style={{ ...inp, height: "auto", padding: "7px 9px", fontFamily: "var(--font-mono)", fontSize: 11.5, lineHeight: 1.5, resize: "vertical" }} />
          <div style={{ fontSize: 11, color: aList.length ? OK : ORANGE, marginTop: 5, fontFamily: "var(--font-mono)" }}>
            {isCompany
              ? (aList.length ? aList.length + " company(ies) parsed — assign each discipline below" : "required: with Assignee Type = company, Assigned To must be a company name")
              : (a.rolesText ? aList.length + " role(s) parsed" : "empty — using the EasyBIM hub role list (" + HUB_ROLES.length + " roles)")}
          </div>
        </div>
        <div style={{ display: "flex", gap: 16, fontSize: 11.5, color: "#9aa0ac", margin: "0 2px 14px", fontFamily: "var(--font-mono)" }}>
          <span>Start Date: {iso(today())} (today)</span><span>Due Date: {due}</span><span>Missing Ref rows are never exported — fix the AR link in Revit instead</span>
        </div>

        <p style={sectionLabel}>Descriptions · Hebrew, written into the Description column</p>
        <div style={{ ...card, padding: "12px 14px", marginBottom: 13 }}>
          <Area label="Not OK · wrong elevation" value={a.descElev} onChange={(v) => setA({ descElev: v })} />
          <Area label="Not OK · wrong angle to north" value={a.descAngle} onChange={(v) => setA({ descAngle: v })} />
          <Area label="Not OK · both elevation and angle" value={a.descBoth} onChange={(v) => setA({ descBoth: v })}
            note="The right one is picked per link from what actually differs; the measured gaps and the reference model name are appended automatically." />
          <Area label="Not Shared · link is not on the shared site" value={a.descShared} onChange={(v) => setA({ descShared: v })} />
        </div>

        <p style={sectionLabel}>
          Per-discipline values · {isCompany ? "דיספלינה fills itself" : "Role and דיספלינה fill themselves"} from the Code (ISO 19650 originator letters + EasyBIM two-letter codes)
          {(missingRole.length || missingCo.length) ? <span style={{ color: ORANGE, marginLeft: 8, textTransform: "none", letterSpacing: 0 }}>· {(isCompany ? missingCo : missingRole).length} value(s) still empty</span> : null}
        </p>
        <div style={{ ...card, overflow: "hidden", marginBottom: 13 }}>
          <div style={{ display: "grid", gridTemplateColumns: PDG, background: "#f4f6fd", borderBottom: "1px solid var(--eb-line)" }}>
            {["Code", isCompany ? "Company" : "Role", "דיספלינה", "Issues"].map((t) => (
              <span key={t} className="eb-mono" style={{ padding: "7px 9px", fontSize: 9.5, letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 700, color: "#8b93a7" }}>{t}</span>
            ))}
          </div>
          {discsUsed.length === 0 && <div style={{ padding: "16px 12px", textAlign: "center", fontSize: 12.5, color: MUTE }}>No flagged links — nothing to assign.</div>}
          {discsUsed.map((d, i) => {
            const n = list.filter((x) => x.disc === d).length;
            const role = a.roles[d] !== undefined ? a.roles[d] : autoRole(d);
            const heb = a.heb[d] !== undefined ? a.heb[d] : autoHeb(d);
            const autoR = a.roles[d] === undefined, autoH = a.heb[d] === undefined;
            return (
              <div key={d} style={{ display: "grid", gridTemplateColumns: PDG, alignItems: "center", gap: 0, padding: "6px 9px", borderBottom: i < discsUsed.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
                <b className="eb-mono" style={{ fontSize: 11.5, color: NAVY }}>{d}</b>
                <span style={{ paddingRight: 8 }}>
                  {isCompany ? (
                    <input value={a.companies[d] || ""} list="pbp-assignees" placeholder="company as named on the hub"
                      onChange={(e) => setA({ companies: { ...a.companies, [d]: e.target.value } })}
                      style={{ ...inp, height: 27, fontSize: 12, borderColor: a.companies[d] ? "var(--eb-line)" : "rgba(224,133,30,0.55)" }} />
                  ) : (
                    <input value={role} list="pbp-assignees" placeholder="type or pick a role"
                      title={autoR ? "Filled automatically from the code — type to override" : "Overridden manually"}
                      onChange={(e) => setA({ roles: { ...a.roles, [d]: e.target.value } })}
                      style={{ ...inp, height: 27, fontSize: 12, borderColor: role ? (autoR ? "var(--eb-line)" : CYAN) : "rgba(224,133,30,0.55)", background: autoR ? "#fafbff" : "#fff" }} />
                  )}
                </span>
                <span style={{ paddingRight: 8 }}>
                  <select value={heb} dir="rtl" title={autoH ? "Filled automatically from the code" : "Overridden manually"}
                    onChange={(e) => setA({ heb: { ...a.heb, [d]: e.target.value } })}
                    style={{ ...inp, height: 27, fontSize: 12, textAlign: "right", borderColor: autoH ? "var(--eb-line)" : CYAN, background: autoH ? "#fafbff" : "#fff", opacity: dTarget ? 1 : 0.5 }}>
                    {HEB_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </span>
                <span className="eb-mono" style={{ fontSize: 11, color: "#6b7280" }}>{n}</span>
              </div>
            );
          })}
          <datalist id="pbp-assignees">{aList.map((r) => <option key={r} value={r} />)}</datalist>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
          <p style={{ ...sectionLabel, margin: 0 }}>Preview · {plist.length}{plist.length !== list.length ? " of " + list.length : ""} issue{list.length === 1 ? "" : "s"} · every cell is editable</p>
          <span style={{ flex: 1 }} />
          {prevFiltered && <Button variant="ghost" size="sm" icon={<Icon name="FilterX" size={13} stroke={2} />} onClick={() => set({ prevF: {} })}>Clear filters</Button>}
          {psort.i >= 0 && <Button variant="ghost" size="sm" icon={<Icon name="ArrowUpDown" size={13} stroke={2} />} onClick={() => set({ prevSort: { i: -1, dir: 1 } })}>Clear sort</Button>}
        </div>

        {selIds.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 12px", marginBottom: 8, borderRadius: "var(--radius-lg)", background: "rgba(68,184,211,0.09)", border: "1px solid rgba(68,184,211,0.32)", flexWrap: "wrap" }}>
            <b style={{ fontSize: 12.5, color: NAVY }}>{selIds.length} selected</b>
            <span style={{ fontSize: 12, color: "#6b7280" }}>set</span>
            <select value={s.prevBulk === undefined ? 0 : s.prevBulk} onChange={(e) => set({ prevBulk: Number(e.target.value), prevBulkVal: "" })}
              style={{ ...inp, width: 150, height: 27, fontSize: 12 }}>
              {pcols.map((c, i) => <option key={c.key} value={i}>{c.label}</option>)}
            </select>
            <span style={{ fontSize: 12, color: "#6b7280" }}>to</span>
            {bulkCol.kind === "discipline" ? (
              <select value={s.prevBulkVal || ""} dir="rtl" onChange={(e) => set({ prevBulkVal: e.target.value })} style={{ ...inp, width: 150, height: 27, fontSize: 12, textAlign: "right" }}>
                <option value="">—</option>
                {HEB_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <input value={s.prevBulkVal || ""} type={bulkCol.kind === "date" ? "date" : "text"}
                list={bulkCol.key === "Assigned To" ? "pbp-assignees" : undefined}
                onChange={(e) => set({ prevBulkVal: e.target.value })}
                style={{ ...inp, width: 190, height: 27, fontSize: 12 }} />
            )}
            <Button variant="primary" size="sm" onClick={applyBulk}>Apply to {selIds.length}</Button>
            <span style={{ flex: 1 }} />
            <Button variant="ghost" size="sm" onClick={() => set({ prevSel: {} })}>Clear selection</Button>
          </div>
        )}

        <div className="eb-scroll" style={{ ...card, overflow: "auto", maxHeight: 240 }}>
          <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#f4f6fd", boxShadow: "0 1px 0 var(--eb-line)" }}>
            <div style={{ display: "grid", gridTemplateColumns: prevCols }}>
              <span style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "5px 0" }}>
                <C.Check on={allSel} title={allSel ? "Deselect all shown" : "Select all shown"} onClick={() => set({ prevSel: allSel ? {} : plist.reduce((o, x) => { o[x.id] = true; return o; }, {}) })} />
              </span>
              {pcols.map((c, i) => (
                <button key={c.key} onClick={() => set({ prevSort: { i: i, dir: psort.i === i ? -psort.dir : 1 } })} title={"Sort by " + c.label}
                  style={{ display: "flex", alignItems: "center", gap: 3, padding: "6px 8px", background: "none", border: "none", cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 9, letterSpacing: "0.04em", textTransform: "uppercase", fontWeight: 700, color: psort.i === i ? NAVY : (c.cf ? "#5b63b0" : "#8b93a7"), textAlign: "left", overflow: "hidden", minWidth: 0 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.label}</span>
                  {psort.i === i && <span style={{ color: CYAN }}>{psort.dir === 1 ? "▲" : "▼"}</span>}
                </button>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: prevCols, borderTop: "1px solid var(--eb-line-soft)" }}>
              <span />
              {pcols.map((c, i) => (
                <span key={c.key} style={{ padding: "4px 4px", display: "flex", minWidth: 0 }}>
                  <input value={pf[i] || ""} placeholder="Filter" onChange={(e) => set({ prevF: { ...pf, [i]: e.target.value } })}
                    style={{ ...inp, width: "100%", minWidth: 0, height: 21, fontSize: 9.5, padding: "0 4px", fontFamily: "var(--font-mono)" }} />
                </span>
              ))}
            </div>
          </div>
          {plist.length === 0 && <div style={{ padding: "20px 12px", textAlign: "center", fontSize: 12.5, color: MUTE }}>{list.length ? "No issues match this filter." : "Nothing flagged in the current selection."}</div>}
          {plist.map((x, i) => {
            const ed = (a.rowEdit && a.rowEdit[x.id]) || {};
            const on = !!sel[x.id];
            return (
              <div key={x.id} style={{ display: "grid", gridTemplateColumns: prevCols, alignItems: "center", borderBottom: i < plist.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: on ? "rgba(68,184,211,0.08)" : x.st === "Not Shared" ? "rgba(214,69,69,0.05)" : "transparent" }}>
                <span style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <C.Check on={on} title={on ? "Selected for bulk edit" : "Select for bulk edit"} onClick={() => set({ prevSel: { ...sel, [x.id]: !on } })} />
                </span>
                {pcols.map((c) => {
                  const k = editKeyOf(c);
                  const dirty = c.kind === "free" ? !!(x.extra && x.extra[c.field]) : ed[k] !== undefined;
                  const st = { ...inp, height: 24, fontSize: 11, padding: "0 6px", minWidth: 0, borderColor: dirty ? CYAN : "transparent", background: dirty ? "#fff" : "transparent" };
                  if (c.kind === "discipline") return (
                    <span key={c.key} style={{ padding: "3px 5px", minWidth: 0 }}>
                      <select value={x.discipline} dir="rtl" onChange={(e) => setVal(x.id, c, e.target.value)} style={{ ...st, textAlign: "right", background: "#fff", borderColor: dirty ? CYAN : "var(--eb-line)" }}>
                        {HEB_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </span>
                  );
                  return (
                    <span key={c.key} style={{ padding: "3px 5px", minWidth: 0 }}>
                      <input value={valOf(x, c)} type={c.kind === "date" ? "date" : "text"}
                        dir={c.key === "Description" ? "rtl" : undefined} title={c.kind === "date" ? "" : valOf(x, c)}
                        list={c.key === "Assigned To" ? "pbp-assignees" : undefined}
                        placeholder={c.kind === "free" ? c.field : c.key === "Assigned To" ? "not mapped" : ""}
                        onChange={(e) => setVal(x.id, c, e.target.value)}
                        style={Object.assign(st,
                          c.key === "Description" ? { textAlign: "right" } : {},
                          c.kind === "date" ? { fontFamily: "var(--font-mono)", fontSize: 10, borderColor: dirty ? CYAN : "var(--eb-line)", background: "#fff" } : {},
                          c.kind === "free" ? { borderColor: dirty ? CYAN : "var(--eb-line)", background: "#fff" } : {},
                          (c.key === "Assigned To" && !valOf(x, c)) ? { borderColor: "rgba(224,133,30,0.55)", background: "#fff" } : {}
                        )} />
                    </span>
                  );
                })}
              </div>
            );
          })}
        </div>
        <p style={{ fontSize: 11.5, color: "#9aa0ac", margin: "10px 2px 0", display: "flex", alignItems: "flex-start", gap: 6, lineHeight: 1.5 }}>
          <Icon name="Info" size={13} style={{ color: CYAN, flex: "0 0 auto", marginTop: 1 }} />
          <span>Columns written: {ACC_COLS.join(" · ")}{fields.map((f) => " + " + f).join("")}. Order and names follow ACC's template — don't rename them in the sheet. <b style={{ color: "#6b7280" }}>Location</b> is left empty: it resolves against the project's Locations breakdown structure, so the building goes to a custom field instead.</span>
        </p>
      </React.Fragment>
    );
  }

  // ============================================================ DONE
  function Done({ s }) {
    const picks = chosen(s);
    const list = issueRows(s);
    const included = rows().filter((r) => r.kind !== "HOST" && !s.excluded[r.id]).length;
    const items = picks.map((f) => [f.name, fileBase(s) + f.ext, f.icon])
      .concat([["Folder", s.exp.folder, "Folder"]])
      .concat(s.opts.log ? [["Run log", fileBase(s) + ".txt", "ScrollText"]] : []);
    const hasXlsx = !!s.exp.formats.xlsx;
    return (
      <React.Fragment>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", padding: "6px 0 18px" }}>
          <span style={{ width: 52, height: 52, borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "rgba(34,176,124,0.12)" }}>
            <Icon name="Check" size={26} stroke={2.6} style={{ color: OK }} />
          </span>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 18, color: NAVY, marginTop: 12 }}>
            {picks.length} file{picks.length === 1 ? "" : "s"} written
          </div>
          <div style={{ fontSize: 12.5, color: "#6b7280", marginTop: 3 }}>
            {included} link{included === 1 ? "" : "s"} included{hasXlsx ? " · " + list.length + " issue row" + (list.length === 1 ? "" : "s") + " for Format" : ""}
          </div>
        </div>
        <div style={{ ...card, overflow: "hidden", marginBottom: 14 }}>
          {items.map(([l, v, ic], i) => (
            <div key={l} style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 14px", background: "#fafbff", borderBottom: i < items.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <span style={{ width: 30, height: 30, borderRadius: 8, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "rgba(30,36,140,0.08)" }}>
                <Icon name={ic} size={15} stroke={1.9} style={{ color: NAVY }} />
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 11, color: "#9aa0ac" }}>{l}</div>
                <div className="eb-mono" style={{ fontSize: 12, color: "#1f2937", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{v}</div>
              </div>
            </div>
          ))}
        </div>
        {s.exp.formats.xlsx && (
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "12px 14px", borderRadius: "var(--radius-lg)", background: "rgba(68,184,211,0.07)", border: "1px solid rgba(68,184,211,0.22)" }}>
            <Icon name="Upload" size={15} stroke={1.9} style={{ color: NAVY, flex: "0 0 auto", marginTop: 1 }} />
            <span style={{ fontSize: 12, color: "#374151", lineHeight: 1.5 }}>
              In Format (ACC) open <span className="eb-mono" style={{ color: NAVY }}>Issues ▸ ⋯ ▸ Import issues</span> and pick this file. Any value the project doesn't recognise is highlighted red in the import preview — come back to <b>Issue fields</b> and correct it, then export again.
            </span>
          </div>
        )}
      </React.Fragment>
    );
  }

  // ============================================================ FOOTERS
  function footer(close, state, setState) {
    const set = (patch) => setState({ ...state, ...patch });
    const persist = () => {
      if (!state.opts.remember) return;
      savePrefs({ folder: state.exp.folder, acc: state.acc, tolMm: state.tolMm, tolDeg: state.tolDeg, opts: state.opts });
    };
    if (state.stage === "export") {
      const picks = chosen(state);
      const xlsx = !!state.exp.formats.xlsx;
      return (
        <React.Fragment>
          <span style={{ flex: 1, fontSize: 12, fontFamily: "var(--font-mono)", color: picks.length ? MUTE : ORANGE }}>
            {picks.length ? picks.map((f) => f.ext.slice(1)).join(" + ") : "no format selected"}
          </span>
          <Button variant="ghost" size="md" icon={<Icon name="ChevronLeft" size={15} stroke={2} />} onClick={() => set({ stage: "results" })}>Back to results</Button>
          {xlsx
            ? <Button variant="primary" size="md" icon={<Icon name="ArrowRight" size={15} stroke={2} />} onClick={() => set({ stage: "issues" })}>Next: issue fields</Button>
            : <Button variant="primary" size="md" icon={<Icon name="Download" size={15} stroke={2} />} onClick={() => { if (!picks.length) return; persist(); set({ stage: "done" }); }}>Export</Button>}
        </React.Fragment>
      );
    }
    if (state.stage === "issues") {
      const list = issueRows(state);
      const a = state.acc;
      const gaps = list.filter((x) => !x["Assigned To"] || (cfTargetOf(a, "discipline") && !x.discipline)).length;
      return (
        <React.Fragment>
          <span style={{ flex: 1, fontSize: 12, fontFamily: "var(--font-mono)", color: gaps ? ORANGE : OK }}>
            {list.length} issue{list.length === 1 ? "" : "s"}{gaps ? " · " + gaps + " missing role/discipline" : " · all fields mapped"}
          </span>
          <Button variant="ghost" size="md" icon={<Icon name="ChevronLeft" size={15} stroke={2} />} onClick={() => set({ stage: "export" })}>Back</Button>
          <Button variant="primary" size="md" icon={<Icon name="Download" size={15} stroke={2} />} onClick={() => { persist(); set({ stage: "done" }); }}>Export {chosen(state).length > 1 ? "all" : "sheet"}</Button>
        </React.Fragment>
      );
    }
    return (
      <React.Fragment>
        <span style={{ flex: 1 }} />
        <Button variant="secondary" size="md" icon={<Icon name="FolderOpen" size={15} stroke={1.9} />} onClick={() => {}}>Open folder</Button>
        <Button variant="secondary" size="md" onClick={() => set({ stage: state.exp.formats.xlsx ? "issues" : "results" })}>{state.exp.formats.xlsx ? "Issue fields" : "Back to results"}</Button>
        <Button variant="primary" size="md" onClick={close}>Done</Button>
      </React.Fragment>
    );
  }

  window.PBPExport = { Export, Issues, Done, footer, issueRows, FORMATS, ACC_COLS };
})();
