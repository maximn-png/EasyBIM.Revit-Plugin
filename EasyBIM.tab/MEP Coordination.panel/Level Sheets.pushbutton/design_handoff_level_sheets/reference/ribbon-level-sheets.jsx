// ribbon-level-sheets.jsx
// "Level Sheets" (Stage A) — MEP Coordination batch view + sheet builder.
// For every selected level: creates a floor plan and a ceiling (RCP) view,
// applies the EB_MEP_FP_1-50 / EB_MEP_CP_1-50 templates, renames both to the
// EasyBIM standard, creates one sheet numbered from 200 and places the two
// viewports stacked (RCP above FP) horizontally aligned to the model.
//
// The level code is NEVER parsed from the level name (architect-linked, often
// non-English). It is suggested from elevation order, editable per row, and
// persisted to the EB_Level_Code parameter on the Level.

(function () {
  const DS = window.EasyBIMDesignSystem_a35564 || {};
  const { Button, Badge, Input } = DS;
  const { Icon } = window;

  const NAVY = "#1e248c", CYAN = "#44b8d3";
  const OK = "var(--eb-success)", ORANGE = "#e0851e", RED = "var(--eb-error)", MUTE = "#8b93a7";
  const SHEET_START = 200;
  const CODE_PARAM = "EB_Level_Code";

  const card = { background: "#fff", border: "1px solid var(--eb-line)", borderRadius: "var(--radius-lg)" };
  const sectionLabel = { fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "#9aa0ac", margin: "0 0 8px" };

  const levels = () => (window.SHEET_LEVELS || []).slice().sort((a, b) => a.elev - b.elev);
  const tpl = () => window.PLAN_TEMPLATES || {};
  const fpName = (c) => "CD_Floor Plan_" + c;
  const cpName = (c) => "CD_Ceiling Plan_" + c;

  // §7 — auto-suggestion. Heuristic only; a stored/edited code always wins.
  function suggestions() {
    const all = levels();
    if (!all.length) return {};
    let gi = 0;
    all.forEach((l, i) => { if (Math.abs(l.elev) < Math.abs(all[gi].elev)) gi = i; });
    const out = {};
    out[all[gi].id] = "GF00";
    for (let i = gi - 1, n = 1; i >= 0; i--, n++) out[all[i].id] = "B" + String(n).padStart(2, "0");
    for (let i = gi + 1, n = 1; i < all.length; i++, n++) out[all[i].id] = "F" + String(n).padStart(2, "0");
    if (gi < all.length - 1) out[all[all.length - 1].id] = "RT";
    return out;
  }

  const codeOf = (s, l) => (s.codes[l.id] !== undefined ? s.codes[l.id] : l.stored || s.sugg[l.id] || "");
  const sourceOf = (s, l) => (s.codes[l.id] !== undefined ? "edited" : l.stored ? "stored" : "suggested");
  const included = (s) => levels().filter((l) => s.on[l.id]);

  // ---- validation (§6) ----
  function validate(s) {
    const sel = included(s);
    const errs = [];
    const dup = {};
    sel.forEach((l) => { const c = codeOf(s, l).trim(); dup[c] = (dup[c] || 0) + 1; });
    const badIds = {};
    sel.forEach((l) => {
      const c = codeOf(s, l).trim();
      if (!c) badIds[l.id] = "empty";
      else if (dup[c] > 1) badIds[l.id] = "dup";
    });
    if (!sel.length) errs.push("Select at least one level.");
    if (Object.values(badIds).indexOf("empty") >= 0) errs.push("Every selected level needs a code.");
    if (Object.values(badIds).indexOf("dup") >= 0) errs.push("Codes must be unique among selected levels.");
    if (!tpl().fpFound) errs.push("View template " + tpl().fp + " not found in this model.");
    if (!tpl().cpFound) errs.push("View template " + tpl().cp + " not found in this model.");
    if (!s.titleBlock) errs.push("Choose a title block.");
    if (!s.vftFp || !s.vftCp) errs.push("Choose both view family types.");
    return { errs: errs, badIds: badIds, ok: errs.length === 0 };
  }

  // ---- run report (§11) ----
  function report(s) {
    const suffix = s.runIndex > 0 ? "_" + (s.runIndex + 1) : "";
    const numSuffix = s.runIndex > 0 ? "-" + (s.runIndex + 1) : "";
    return included(s).map((l, i) => {
      const code = codeOf(s, l).trim();
      const warn = [];
      if (l.big) warn.push("did not fit at 1:" + tpl().scale + " — scale preserved");
      if (s.runIndex > 0) warn.push("name & number suffixed (re-run)");
      return {
        id: l.id, level: l.name, code: code,
        sheet: String(SHEET_START + i) + numSuffix,
        sheetName: code + "-MEP",
        fp: fpName(code) + suffix,
        cp: cpName(code) + suffix,
        warn: warn,
      };
    });
  }

  // ---- small controls ----
  function Check({ on, onClick }) {
    return (
      <button onClick={onClick} style={{ width: 15, height: 15, flex: "0 0 auto", borderRadius: 4, cursor: "pointer", border: "1.5px solid " + (on ? CYAN : "#c4cad9"), background: on ? CYAN : "#fff", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0 }}>
        {on && <Icon name="Check" size={10} stroke={3.4} style={{ color: "#fff" }} />}
      </button>
    );
  }
  function Picker({ label, value, options, onChange }) {
    return (
      <div style={{ flex: 1, minWidth: 0 }}>
        <p style={sectionLabel}>{label}</p>
        <select value={value} onChange={(e) => onChange(e.target.value)}
          style={{ width: "100%", height: 38, borderRadius: "var(--radius-lg)", border: "1px solid var(--eb-line)", background: "#fff", padding: "0 10px", fontFamily: "var(--font-mono)", fontSize: 11.5, color: "#374151", outline: "none", cursor: "pointer" }}
          onFocus={(e) => { e.target.style.borderColor = CYAN; }} onBlur={(e) => { e.target.style.borderColor = "var(--eb-line)"; }}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  function Locked({ icon, label, value, last }) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 11, padding: "10px 14px", background: "#fafbff", borderBottom: last ? "none" : "1px solid var(--eb-line-soft)" }}>
        <span style={{ width: 28, height: 28, borderRadius: 8, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "rgba(30,36,140,0.08)" }}>
          <Icon name={icon} size={14} stroke={1.9} style={{ color: NAVY }} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11, color: "#9aa0ac" }}>{label}</div>
          <div className="eb-mono" style={{ fontSize: 12.5, color: "#1f2937", fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{value}</div>
        </div>
        <Icon name="Lock" size={12} stroke={2} style={{ color: "#c0c6e0", flex: "0 0 auto" }} />
      </div>
    );
  }

  // ============================================================ SETUP
  const COLS = "24px minmax(0,1.35fr) 78px 112px 74px minmax(0,1.15fr)";
  const HEAD = ["", "Level (Revit name)", "Elev (m)", CODE_PARAM, "Source", "Sheet → number · name"];

  function Setup({ s, set }) {
    const all = levels();
    const v = validate(s);
    const sel = included(s);
    const allOn = all.length > 0 && all.every((l) => s.on[l.id]);
    const toggleAll = () => { const next = {}; all.forEach((l) => { next[l.id] = !allOn; }); set({ on: next }); };
    const t = tpl();
    const tplOk = t.fpFound && t.cpFound;

    return (
      <React.Fragment>
        {/* preflight — templates resolved by exact name */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 14px", marginBottom: 14, borderRadius: "var(--radius-lg)", background: tplOk ? "rgba(34,176,124,0.08)" : "rgba(214,69,69,0.07)", border: "1px solid " + (tplOk ? "rgba(34,176,124,0.25)" : "rgba(214,69,69,0.25)") }}>
          <Icon name={tplOk ? "ShieldCheck" : "TriangleAlert"} size={16} stroke={2} style={{ color: tplOk ? OK : RED, flex: "0 0 auto" }} />
          <div style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: "#374151" }}>
            View templates <span className="eb-mono" style={{ color: t.fpFound ? NAVY : RED, fontWeight: 600 }}>{t.fp}</span> · <span className="eb-mono" style={{ color: t.cpFound ? NAVY : RED, fontWeight: 600 }}>{t.cp}</span> {tplOk ? "resolved — both 1:" + t.scale + ", grids align 1:1." : "— missing, cannot run."}
          </div>
          {tplOk && <Badge tone="live" dot>Ready</Badge>}
        </div>

        {/* level grid */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <p style={{ ...sectionLabel, margin: 0 }}>Levels · {sel.length} of {all.length} selected · sorted by elevation</p>
          <button onClick={toggleAll} style={{ background: "none", border: "none", cursor: "pointer", fontFamily: "var(--font-body)", fontSize: 12, fontWeight: 600, color: CYAN, padding: 0 }}>
            {allOn ? "Deselect all" : "Select all"}
          </button>
        </div>
        <div className="eb-scroll" style={{ ...card, overflowY: "auto", overflowX: "hidden", maxHeight: 296, marginBottom: 14 }}>
          <div style={{ display: "grid", gridTemplateColumns: COLS, position: "sticky", top: 0, zIndex: 2, background: "#f4f6fd", boxShadow: "0 1px 0 var(--eb-line)" }}>
            {HEAD.map((h, i) => (
              i === 0 ? (
                <span key="c" style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "7px 0", borderRight: "1px solid var(--eb-line-soft)" }}><Check on={allOn} onClick={toggleAll} /></span>
              ) : (
                <span key={h} className="eb-mono" style={{ padding: "7px 7px", fontSize: 9, letterSpacing: "0.03em", textTransform: "uppercase", fontWeight: 700, color: "#8b93a7", textAlign: i === 2 ? "right" : "left", borderRight: i < HEAD.length - 1 ? "1px solid var(--eb-line-soft)" : "none", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h}</span>
              )
            ))}
          </div>
          {all.map((l, i) => {
            const on = !!s.on[l.id];
            const code = codeOf(s, l);
            const src = sourceOf(s, l);
            const bad = on ? v.badIds[l.id] : null;
            const idx = sel.indexOf(l);
            const cell = { padding: "5px 7px", fontSize: 11.5, color: "#4b5563", display: "flex", alignItems: "center", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
            return (
              <div key={l.id} style={{ display: "grid", gridTemplateColumns: COLS, alignItems: "center", borderBottom: i < all.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: bad ? "rgba(214,69,69,0.06)" : "transparent", opacity: on ? 1 : 0.45 }}>
                <span style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Check on={on} onClick={() => set({ on: { ...s.on, [l.id]: !on } })} />
                </span>
                <div style={cell} title={l.name}>
                  <Icon name="Rows3" size={12} stroke={1.9} style={{ color: "#b6bdd0", flex: "0 0 auto", marginRight: 6 }} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{l.name}</span>
                </div>
                <div className="eb-mono" style={{ ...cell, justifyContent: "flex-end", fontVariantNumeric: "tabular-nums", fontSize: 11, color: "#1f2937", fontWeight: 600 }}>{l.elev.toFixed(2)}</div>
                <div style={{ ...cell, padding: "4px 5px" }}>
                  <input value={code} onChange={(e) => set({ codes: { ...s.codes, [l.id]: e.target.value } })}
                    placeholder="code"
                    style={{ width: "100%", minWidth: 0, height: 24, borderRadius: 6, border: "1px solid " + (bad ? RED : src === "edited" ? CYAN : "var(--eb-line)"), background: "#fff", padding: "0 7px", fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700, color: bad ? RED : NAVY, outline: "none", boxSizing: "border-box" }}
                    onFocus={(e) => { e.target.style.borderColor = CYAN; }} onBlur={(e) => { e.target.style.borderColor = bad ? RED : src === "edited" ? CYAN : "var(--eb-line)"; }} />
                </div>
                <div style={{ ...cell, padding: "5px 6px" }}>
                  <span className="eb-mono" style={{ fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.03em", padding: "2px 6px", borderRadius: 999, color: src === "stored" ? NAVY : src === "edited" ? CYAN : MUTE, background: src === "stored" ? "rgba(30,36,140,0.08)" : src === "edited" ? "rgba(68,184,211,0.14)" : "#f1f2f7" }}>{src}</span>
                </div>
                <div className="eb-mono" style={{ ...cell, fontSize: 10.5, color: bad ? RED : on ? "#6b7280" : MUTE }}>
                  {!on ? "—" : bad === "empty" ? "code required" : bad === "dup" ? "duplicate code" : (SHEET_START + idx) + " · " + code.trim() + "-MEP"}
                </div>
              </div>
            );
          })}
        </div>

        {/* pickers */}
        <p style={sectionLabel}>Sheet & view types</p>
        <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
          <Picker label="Title block" value={s.titleBlock} options={window.TITLE_BLOCKS || []} onChange={(x) => set({ titleBlock: x })} />
          <Picker label="Floor plan type" value={s.vftFp} options={window.VFT_FP || []} onChange={(x) => set({ vftFp: x })} />
          <Picker label="Ceiling plan type" value={s.vftCp} options={window.VFT_CP || []} onChange={(x) => set({ vftCp: x })} />
        </div>

        {/* fixed output rules */}
        <p style={sectionLabel}>Output · EasyBIM standard</p>
        <div style={{ ...card, overflow: "hidden", marginBottom: v.errs.length ? 14 : 0 }}>
          <Locked icon="LayoutTemplate" label="View names" value={fpName("{code}") + "  ·  " + cpName("{code}")} />
          <Locked icon="FileText" label="Sheet number · name" value={SHEET_START + " upward (bottom level first)  ·  {code}-MEP"} />
          <Locked icon="Rows3" label="Layout" value="RCP above floor plan · horizontally aligned to the model" />
          <Locked icon="Ruler" label="Scale" value={"1:" + tpl().scale + " from template · never auto-rescaled (warn only)"} last />
        </div>

        {v.errs.length > 0 && (
          <div style={{ marginTop: 14, borderRadius: "var(--radius-lg)", background: "rgba(214,69,69,0.06)", border: "1px solid rgba(214,69,69,0.25)", padding: "10px 14px" }}>
            {v.errs.map((e) => (
              <div key={e} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#374151", padding: "2px 0" }}>
                <Icon name="TriangleAlert" size={13} stroke={2} style={{ color: RED, flex: "0 0 auto" }} />{e}
              </div>
            ))}
          </div>
        )}
      </React.Fragment>
    );
  }

  // ============================================================ REPORT
  function Report({ s }) {
    const rows = report(s);
    const warned = rows.filter((r) => r.warn.length).length;
    const RCOLS = "68px minmax(0,0.7fr) minmax(0,1.05fr) minmax(0,1.05fr) minmax(0,1.1fr)";
    const RHEAD = ["Sheet", "Name", "Floor plan", "Ceiling plan", "Warning"];

    const Stat = ({ value, label, color }) => (
      <div style={{ ...card, flex: 1, padding: "10px 12px", textAlign: "center" }}>
        <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 22, color: color, lineHeight: 1 }}>{value}</div>
        <div style={{ fontSize: 10.5, color: "#6b7280", marginTop: 4 }}>{label}</div>
      </div>
    );

    return (
      <React.Fragment>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", padding: "4px 0 16px" }}>
          <span style={{ width: 52, height: 52, borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "rgba(34,176,124,0.12)" }}>
            <Icon name="Check" size={26} stroke={2.6} style={{ color: OK }} />
          </span>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 18, color: NAVY, marginTop: 12 }}>{rows.length} level sheet{rows.length === 1 ? "" : "s"} created</div>
          <div style={{ fontSize: 12.5, color: "#6b7280", marginTop: 3 }}>{rows.length * 2} views · one undo step · codes written to <span className="eb-mono">{CODE_PARAM}</span></div>
        </div>

        <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
          <Stat value={rows.length} label="Levels processed" color={NAVY} />
          <Stat value={rows.length} label="Sheets created" color={OK} />
          <Stat value={rows.length * 2} label="Views created" color={CYAN} />
          <Stat value={warned} label="Warnings" color={warned ? ORANGE : MUTE} />
        </div>

        <p style={sectionLabel}>Per level</p>
        <div className="eb-scroll" style={{ ...card, overflowY: "auto", overflowX: "hidden", maxHeight: 300 }}>
          <div style={{ display: "grid", gridTemplateColumns: RCOLS, position: "sticky", top: 0, zIndex: 2, background: "#f4f6fd", boxShadow: "0 1px 0 var(--eb-line)" }}>
            {RHEAD.map((h, i) => (
              <span key={h} className="eb-mono" style={{ padding: "7px 8px", fontSize: 9, letterSpacing: "0.03em", textTransform: "uppercase", fontWeight: 700, color: "#8b93a7", borderRight: i < RHEAD.length - 1 ? "1px solid var(--eb-line-soft)" : "none", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{h}</span>
            ))}
          </div>
          {rows.map((r, i) => {
            const cell = { padding: "6px 8px", fontSize: 10.5, display: "flex", alignItems: "center", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
            return (
              <div key={r.id} style={{ display: "grid", gridTemplateColumns: RCOLS, alignItems: "center", borderBottom: i < rows.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: r.warn.length ? "rgba(224,133,30,0.06)" : "transparent" }}>
                <b className="eb-mono" style={{ ...cell, color: NAVY, fontSize: 11 }}>{r.sheet}</b>
                <span className="eb-mono" style={{ ...cell, color: "#1f2937", fontWeight: 600 }} title={r.sheetName}>{r.sheetName}</span>
                <span className="eb-mono" style={{ ...cell, color: "#6b7280" }} title={r.fp}>{r.fp}</span>
                <span className="eb-mono" style={{ ...cell, color: "#6b7280" }} title={r.cp}>{r.cp}</span>
                <span style={{ ...cell, gap: 6, color: r.warn.length ? "#8a6d1a" : MUTE, fontSize: 10 }} title={r.warn.join(" · ")}>
                  {r.warn.length > 0 && <Icon name="TriangleAlert" size={11} stroke={2} style={{ color: ORANGE, flex: "0 0 auto" }} />}
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{r.warn.length ? r.warn.join(" · ") : "—"}</span>
                </span>
              </div>
            );
          })}
        </div>

        <p style={{ fontSize: 11.5, color: "#9aa0ac", margin: "12px 2px 0", display: "flex", alignItems: "flex-start", gap: 6, lineHeight: 1.5 }}>
          <Icon name="Info" size={13} style={{ color: CYAN, flex: "0 0 auto", marginTop: 1 }} />
          <span>Views that don’t fit the title block stay at 1:{tpl().scale} — the scale is never changed, only reported. Re-running on the same model suffixes clashing names and numbers rather than failing.</span>
        </p>
      </React.Fragment>
    );
  }

  // ============================================================ SHELL GLUE
  function Body({ state, setState }) {
    const set = (patch) => setState({ ...state, ...patch });
    return state.stage === "done" ? <Report s={state} /> : <Setup s={state} set={set} />;
  }

  function footer(close, state, setState) {
    const set = (patch) => setState({ ...state, ...patch });
    if (state.stage === "done") {
      const rows = report(state);
      return (
        <React.Fragment>
          <span style={{ flex: 1, fontSize: 12, color: "#9aa0ac", fontFamily: "var(--font-mono)" }}>
            sheets {rows.length ? rows[0].sheet + "–" + rows[rows.length - 1].sheet : "—"}
          </span>
          <Button variant="secondary" size="md" icon={<Icon name="RotateCcw" size={15} stroke={2} />} onClick={() => set({ stage: "setup", runIndex: state.runIndex + 1 })}>Run again</Button>
          <Button variant="primary" size="md" icon={<Icon name="ExternalLink" size={15} stroke={1.9} />} onClick={close}>Open first sheet</Button>
        </React.Fragment>
      );
    }
    const v = validate(state);
    const sel = included(state);
    return (
      <React.Fragment>
        <span style={{ flex: 1, fontSize: 12, color: "#9aa0ac", fontFamily: "var(--font-mono)" }}>
          {sel.length} level{sel.length === 1 ? "" : "s"} · {sel.length * 2} views · sheets {SHEET_START}–{SHEET_START + Math.max(0, sel.length - 1)}
        </span>
        <Button variant="ghost" size="md" onClick={close}>Cancel</Button>
        <Button variant="primary" size="md" disabled={!v.ok} icon={<Icon name="Play" size={14} stroke={2.2} />} onClick={() => set({ stage: "done" })}>Create</Button>
      </React.Fragment>
    );
  }

  function initialState() {
    const on = {};
    levels().forEach((l) => { on[l.id] = true; });
    return {
      stage: "setup",
      sugg: suggestions(),
      codes: {},
      on: on,
      titleBlock: (window.TITLE_BLOCKS || [])[0] || "",
      vftFp: (window.VFT_FP || [])[0] || "",
      vftCp: (window.VFT_CP || [])[0] || "",
      runIndex: 0,
    };
  }

  function banner(state) {
    if (state.stage === "done") return null;
    return "Pick the levels to build. Each one gets a floor plan and a ceiling plan on their own sheet — the code below drives every name, so a level’s Revit name never reaches the output.";
  }

  window.LevelSheets = {
    title: "Level Sheets",
    subtitle: "Batch plan views & sheets from levels · Stage A",
    width: 880,
    Body, footer, initialState, banner,
  };
})();
