// ribbon-tools.jsx
// Branded EasyBIM tool surfaces that open when a ribbon button is clicked.
// Two shells (modal dialog / docked palette) chosen by the Tweaks panel,
// plus the live tool UIs (Check Levels, Export Excel, Import Excel) and a
// dev-handoff "command stub" for the not-yet-defined placeholder buttons.

(function () {
  const DS = window.EasyBIMDesignSystem_a35564 || {};
  const { Button, Badge, Input } = DS;
  const { Icon } = window;

  const NAVY = "#1e248c", CYAN = "#44b8d3";

  // ---------- shared small primitives ----------
  function GradTile({ btn, size = 18, box = 36, radius = 10 }) {
    return (
      <span style={{ width: box, height: box, borderRadius: radius, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: "linear-gradient(135deg,#1e248c,#44b8d3)", boxShadow: "var(--shadow-navy)" }}>
        {btn && btn.img
          ? <img src={btn.img} alt="" style={{ width: size, height: size, objectFit: "contain", filter: "brightness(0) invert(1)" }} />
          : <Icon name={(btn && btn.icon) || "Square"} size={size} stroke={1.9} style={{ color: "#fff" }} />}
      </span>
    );
  }

  function Check({ on, onClick, children }) {
    return (
      <button onClick={onClick} style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", background: "none", border: "none", padding: "7px 4px", cursor: "pointer", textAlign: "left", fontFamily: "var(--font-body)" }}>
        <span style={{ width: 17, height: 17, borderRadius: 5, flex: "0 0 auto", display: "inline-flex", alignItems: "center", justifyContent: "center", background: on ? NAVY : "#fff", border: `1.5px solid ${on ? NAVY : "#c6cbe0"}`, transition: "all 120ms" }}>
          {on && <Icon name="Check" size={12} stroke={3} style={{ color: "#fff" }} />}
        </span>
        <span style={{ flex: 1, fontSize: 13.5, color: "#374151" }}>{children}</span>
      </button>
    );
  }

  function Toggle({ on, onClick }) {
    return (
      <button onClick={onClick} style={{ width: 38, height: 22, borderRadius: 999, border: "none", padding: 2, cursor: "pointer", background: on ? CYAN : "#cbd0e0", transition: "background 160ms", flex: "0 0 auto" }}>
        <span style={{ display: "block", width: 18, height: 18, borderRadius: "50%", background: "#fff", boxShadow: "0 1px 3px rgba(0,0,0,0.2)", transform: on ? "translateX(16px)" : "none", transition: "transform 160ms var(--ease-out)" }} />
      </button>
    );
  }

  const sectionLabel = {
    fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.06em",
    textTransform: "uppercase", color: "#9aa0ac", margin: "0 0 8px",
  };
  const card = {
    background: "#fff", border: "1px solid var(--eb-line)", borderRadius: "var(--radius-lg)",
  };

  // ============================================================ SHELLS
  function ModalShell({ btn, title, subtitle, onClose, children, footer, banner, width = 560, inline, bodyFill }) {
    const dialog = (
      <div style={{ width, maxWidth: "100%", maxHeight: inline ? "none" : "100%", height: bodyFill && !inline ? "100%" : "auto", display: "flex", flexDirection: "column", background: inline ? "#fff" : "rgba(255,255,255,0.94)", backdropFilter: inline ? "none" : "blur(14px)", WebkitBackdropFilter: inline ? "none" : "blur(14px)", border: "1px solid rgba(255,255,255,0.9)", borderRadius: "var(--radius-xl)", boxShadow: "var(--shadow-xl)", overflow: "hidden", animation: inline ? "none" : "eb-modal-in 220ms var(--ease-out) both", fontFamily: "var(--font-body)", margin: inline ? "0 auto" : 0 }}>
        <ShellHeader btn={btn} title={title} subtitle={subtitle} onClose={onClose} />
        {banner && <ShellBanner>{banner}</ShellBanner>}
        <div className="eb-scroll" style={{ padding: bodyFill ? "12px 16px" : "18px 22px", overflow: bodyFill ? "hidden" : "auto", flex: inline && !bodyFill ? "none" : 1, minHeight: 0, display: bodyFill ? "flex" : "block", flexDirection: "column" }}>{children}</div>
        {footer && <ShellFooter>{footer}</ShellFooter>}
      </div>
    );
    if (inline) return dialog;
    return (
      <div style={{ position: "absolute", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", padding: 24, background: "rgba(16,20,46,0.30)", backdropFilter: "blur(2px)", WebkitBackdropFilter: "blur(2px)", animation: "eb-scrim-in 160ms ease both" }}
        onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        {dialog}
      </div>
    );
  }

  function DockShell({ btn, title, subtitle, onClose, children, footer, banner }) {
    return (
      <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 392, maxWidth: "100%", zIndex: 60, display: "flex", flexDirection: "column", background: "#fff", borderLeft: "1px solid var(--eb-line)", boxShadow: "-18px 0 40px rgba(30,36,140,0.10)", animation: "eb-dock-in 240ms var(--ease-out) both", fontFamily: "var(--font-body)" }}>
        <ShellHeader btn={btn} title={title} subtitle={subtitle} onClose={onClose} docked />
        {banner && <ShellBanner>{banner}</ShellBanner>}
        <div className="eb-scroll" style={{ padding: "16px 18px", overflow: "auto", flex: 1 }}>{children}</div>
        {footer && <ShellFooter>{footer}</ShellFooter>}
      </div>
    );
  }

  function ShellBanner({ children }) {
    return (
      <div style={{ display: "flex", gap: 9, alignItems: "flex-start", padding: "11px 20px", borderBottom: "1px solid var(--eb-line)", background: "rgba(68,184,211,0.08)" }}>
        <Icon name="Info" size={15} stroke={2} style={{ color: CYAN, flex: "0 0 auto", marginTop: 1 }} />
        <p style={{ margin: 0, fontSize: 12.5, color: "#374151", lineHeight: 1.45 }}>{children}</p>
      </div>
    );
  }

  function ShellHeader({ btn, title, subtitle, onClose, docked }) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 13, padding: docked ? "14px 16px" : "16px 20px", borderBottom: "1px solid var(--eb-line)", background: "linear-gradient(180deg, rgba(238,246,251,0.7), rgba(255,255,255,0))" }}>
        <GradTile btn={btn} size={20} box={40} radius={11} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 17, color: NAVY, lineHeight: 1.15 }}>{title}</div>
          {subtitle && <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{subtitle}</div>}
        </div>
        <button onClick={onClose} aria-label="Close" style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid transparent", background: "transparent", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#6b7280" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "#f0f2ff"; e.currentTarget.style.color = NAVY; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "#6b7280"; }}>
          <Icon name="X" size={18} stroke={2} />
        </button>
      </div>
    );
  }

  function ShellFooter({ children }) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, padding: "13px 18px", borderTop: "1px solid var(--eb-line)", background: "rgba(248,249,255,0.7)" }}>
        {children}
      </div>
    );
  }

  // ============================================================ CHECK LEVELS
  function CheckLevels({ onClose }) {
    const data = window.LEVELS;
    const counts = data.reduce((a, l) => ((a[l.status] = (a[l.status] || 0) + 1), a), {});
    const Stat = ({ label, value, color }) => (
      <div style={{ ...card, flex: 1, padding: "10px 12px" }}>
        <div style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 22, color, lineHeight: 1 }}>{value}</div>
        <div style={{ fontSize: 11, color: "#6b7280", marginTop: 4 }}>{label}</div>
      </div>
    );
    const badge = (s) =>
      s === "ok" ? <Badge tone="live" dot>OK</Badge>
        : s === "warn" ? <Badge tone="draft">Warning</Badge>
          : <Badge tone="error">Error</Badge>;
    return (
      <React.Fragment>
        <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
          <Stat label="Levels checked" value={data.length} color={NAVY} />
          <Stat label="Passed" value={counts.ok || 0} color="var(--eb-success)" />
          <Stat label="Warnings" value={counts.warn || 0} color="var(--eb-warning)" />
          <Stat label="Errors" value={counts.error || 0} color="var(--eb-error)" />
        </div>
        <div style={{ ...card, overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 88px 132px", padding: "9px 14px", borderBottom: "1px solid var(--eb-line)", background: "#fafbff", fontFamily: "var(--font-mono)", fontSize: 10.5, letterSpacing: "0.05em", textTransform: "uppercase", color: "#9aa0ac" }}>
            <span>Level</span><span style={{ textAlign: "right" }}>Elev (m)</span><span style={{ textAlign: "right" }}>Status</span>
          </div>
          <div className="eb-scroll" style={{ maxHeight: 248, overflow: "auto" }}>
            {data.map((l, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 88px 132px", alignItems: "center", padding: "9px 14px", borderBottom: i < data.length - 1 ? "1px solid var(--eb-line-soft)" : "none", background: l.status === "error" ? "rgba(255,218,214,0.28)" : l.status === "warn" ? "rgba(254,243,199,0.30)" : "transparent" }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, color: "#1f2937", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{l.name}</div>
                  {l.issue && <div style={{ fontSize: 11.5, color: l.status === "error" ? "var(--eb-error)" : "var(--eb-warning)", marginTop: 2 }}>{l.issue}</div>}
                </div>
                <span className="eb-mono" style={{ textAlign: "right", fontSize: 12.5, color: "#4b5563" }}>{l.elev}</span>
                <span style={{ display: "flex", justifyContent: "flex-end" }}>{badge(l.status)}</span>
              </div>
            ))}
          </div>
        </div>
        <p style={{ fontSize: 12, color: "#9aa0ac", margin: "12px 2px 0", display: "flex", alignItems: "center", gap: 6 }}>
          <Icon name="Info" size={13} style={{ color: CYAN }} /> Double-click a row in the model to select &amp; zoom to that level.
        </p>
      </React.Fragment>
    );
  }
  CheckLevels.title = "Check Levels";
  CheckLevels.subtitle = "9 levels scanned · 1 error, 2 warnings";
  CheckLevels.footer = (close) => (
    <React.Fragment>
      <Button variant="ghost" size="md" onClick={close}>Close</Button>
      <Button variant="primary" size="md" icon={<Icon name="RefreshCw" size={15} stroke={2} />} onClick={close}>Re-run check</Button>
    </React.Fragment>
  );

  // ============================================================ EXPORT EXCEL
  function ExportExcel({ onClose, state, setState }) {
    const list = state.schedules;
    const toggle = (i) => setState({ ...state, schedules: list.map((s, k) => k === i ? { ...s, on: !s.on } : s) });
    const sel = list.filter((s) => s.on);
    const rows = sel.reduce((a, s) => a + s.rows, 0);
    return (
      <React.Fragment>
        <p style={sectionLabel}>Sources · {sel.length} selected</p>
        <div style={{ ...card, overflow: "hidden", marginBottom: 18 }}>
          {list.map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 12px", borderBottom: i < list.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <div style={{ flex: 1 }}><Check on={s.on} onClick={() => toggle(i)}>{s.name}</Check></div>
              <Badge tone="info">{s.cat}</Badge>
              <span className="eb-mono" style={{ fontSize: 11.5, color: "#9aa0ac", width: 64, textAlign: "right" }}>{s.rows.toLocaleString()} rows</span>
            </div>
          ))}
        </div>

        <p style={sectionLabel}>Options</p>
        <div style={{ ...card, padding: "4px 14px", marginBottom: 18 }}>
          {[["headers", "Include column headers & units"], ["group", "One worksheet per source"], ["open", "Open workbook after export"]].map(([k, lbl], i, arr) => (
            <div key={k} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "9px 0", borderBottom: i < arr.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <span style={{ fontSize: 13.5, color: "#374151" }}>{lbl}</span>
              <Toggle on={state.opts[k]} onClick={() => setState({ ...state, opts: { ...state.opts, [k]: !state.opts[k] } })} />
            </div>
          ))}
        </div>

        <p style={sectionLabel}>Destination</p>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <Input value={state.path} onChange={(e) => setState({ ...state, path: e.target.value })} icon={<Icon name="FolderOpen" size={15} stroke={1.9} />} />
          </div>
          <Button variant="secondary" size="md" onClick={() => {}}>Browse…</Button>
        </div>
      </React.Fragment>
    );
  }
  ExportExcel.title = "Export Excel";
  ExportExcel.subtitle = "Schedules & take-offs → .xlsx";
  ExportExcel.footer = (close, state) => {
    const sel = state.schedules.filter((s) => s.on);
    const rows = sel.reduce((a, s) => a + s.rows, 0);
    return (
      <React.Fragment>
        <span style={{ flex: 1, fontSize: 12, color: "#9aa0ac", fontFamily: "var(--font-mono)" }}>{sel.length} sheets · {rows.toLocaleString()} rows</span>
        <Button variant="ghost" size="md" onClick={close}>Cancel</Button>
        <Button variant="primary" size="md" disabled={sel.length === 0} icon={<Icon name="Download" size={15} stroke={2} />} onClick={close}>Export</Button>
      </React.Fragment>
    );
  };

  // ============================================================ IMPORT EXCEL
  function ImportExcel({ onClose }) {
    const map = window.IMPORT_MAP;
    const matched = map.filter((m) => m.match).length;
    return (
      <React.Fragment>
        <div style={{ border: "1.5px dashed #c0c6e0", borderRadius: "var(--radius-lg)", background: "linear-gradient(135deg,#f6f9ff,#eef6fb)", padding: "18px 16px", display: "flex", alignItems: "center", gap: 13, marginBottom: 18 }}>
          <span style={{ width: 40, height: 40, borderRadius: 10, background: "#fff", border: "1px solid var(--eb-line)", display: "inline-flex", alignItems: "center", justifyContent: "center", flex: "0 0 auto" }}>
            <Icon name="FileSpreadsheet" size={20} stroke={1.7} style={{ color: "var(--eb-success)" }} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600, color: "#1f2937", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>Tower-A_MEP_export.xlsx</div>
            <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>Sheet “Mechanical Equipment” · 418 rows · loaded</div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => {}}>Change…</Button>
        </div>

        <p style={sectionLabel}>Column mapping</p>
        <div style={{ ...card, overflow: "hidden", marginBottom: 14 }}>
          {map.map((m, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 18px 1fr", alignItems: "center", gap: 8, padding: "9px 14px", borderBottom: i < map.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <span className="eb-mono" style={{ fontSize: 12, color: "#4b5563", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m.col}</span>
              <Icon name="ArrowRight" size={14} style={{ color: m.match ? CYAN : "#cbd0e0" }} />
              <span style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
                <Icon name={m.match ? "CheckCircle2" : "AlertCircle"} size={14} style={{ color: m.match ? "var(--eb-success)" : "var(--eb-warning)", flex: "0 0 auto" }} />
                <span style={{ fontSize: 13, color: m.match ? "#1f2937" : "#9aa0ac", fontStyle: m.match ? "normal" : "italic", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m.param}</span>
              </span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "#6b7280" }}>
          <Badge tone="live" dot>412 matched</Badge>
          <Badge tone="draft">1 unmapped</Badge>
          <span>by Element ID</span>
        </div>
      </React.Fragment>
    );
  }
  ImportExcel.title = "Import Excel";
  ImportExcel.subtitle = "Write workbook values back to elements";
  ImportExcel.footer = (close) => (
    <React.Fragment>
      <Button variant="ghost" size="md" onClick={close}>Cancel</Button>
      <Button variant="secondary" size="md" icon={<Icon name="Eye" size={15} stroke={1.9} />} onClick={() => {}}>Dry run</Button>
      <Button variant="primary" size="md" icon={<Icon name="Upload" size={15} stroke={2} />} onClick={close}>Import 412</Button>
    </React.Fragment>
  );

  // ============================================================ COMMAND STUB
  function CommandStub({ btn, onClose }) {
    const h = btn.handoff || {};
    const meta = [
      ["Command label", btn.label],
      ["Ribbon panel", btn.panelName],
      ["Suggested class", h.cls],
      ["Available since", h.since && h.since !== "—" ? `v${h.since}` : "Not released"],
    ];
    return (
      <React.Fragment>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", padding: "6px 0 18px" }}>
          <GradTile btn={btn} size={30} box={62} radius={16} />
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginTop: 14 }}>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 19, color: NAVY }}>{btn.label}</span>
            <Badge tone="draft">Not implemented</Badge>
          </div>
          <p style={{ fontSize: 13, color: "#6b7280", margin: "8px 0 0", maxWidth: 360, lineHeight: 1.55 }}>
            This button is registered on the EasyBIM ribbon and routes correctly, but its
            tool UI hasn’t been designed yet. Drop the spec in and it slots into this same
            shell.
          </p>
        </div>
        <p style={sectionLabel}>Handoff</p>
        <div style={{ ...card, overflow: "hidden" }}>
          {meta.map(([k, v], i) => (
            <div key={k} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "10px 14px", borderBottom: i < meta.length - 1 ? "1px solid var(--eb-line-soft)" : "none" }}>
              <span style={{ fontSize: 12.5, color: "#9aa0ac" }}>{k}</span>
              <span className={i >= 2 ? "eb-mono" : ""} style={{ fontSize: i === 2 ? 12 : 13, color: "#374151", fontWeight: i === 2 ? 400 : 500, textAlign: "right" }}>{v}</span>
            </div>
          ))}
        </div>
      </React.Fragment>
    );
  }
  CommandStub.footer = (close) => (
    <React.Fragment>
      <Button variant="ghost" size="md" onClick={close}>Close</Button>
      <Button variant="secondary" size="md" icon={<Icon name="ExternalLink" size={15} stroke={1.9} />} onClick={() => {}}>Open in backlog</Button>
    </React.Fragment>
  );

  // ============================================================ HOST
  const REGISTRY = { checkLevels: CheckLevels, exportExcel: ExportExcel, importExcel: ImportExcel };

  function ToolHost({ btn, surface, onClose, inline }) {
    // export-excel keeps interactive state
    const [xls, setXls] = React.useState(() => ({
      schedules: window.SCHEDULES.map((s) => ({ ...s })),
      opts: { headers: true, group: true, open: false },
      path: "C:\\Projects\\Tower-A\\Exports\\Tower-A_MEP.xlsx",
    }));
    // solution-section wizard keeps its own multi-step state
    const SS = window.SolutionSection;
    const [ss, setSs] = React.useState(() => (SS ? SS.initialState() : {}));
    // head-height-check tool keeps its own config/results state
    const HHC = window.HeadHeightCheck;
    const [hhc, setHhc] = React.useState(() => (HHC ? HHC.initialState() : {}));
    // base-points audit keeps its own setup/results/export state
    const BP = window.BasePoints;
    const [bp, setBp] = React.useState(() => (BP ? BP.initialState() : {}));
    // level-sheets batch builder keeps its own setup/report state
    const LSH = window.LevelSheets;
    const [lsh, setLsh] = React.useState(() => (LSH ? LSH.initialState() : {}));

    const Comp = REGISTRY[btn.kind] || null;
    // stateful tools share one interface: { title, subtitle, Body, footer, banner, initialState }
    const stateful =
      btn.kind === "solutionSection" && SS ? { def: SS, st: ss, setSt: setSs }
        : btn.kind === "headHeightCheck" && HHC ? { def: HHC, st: hhc, setSt: setHhc }
          : btn.kind === "basePoints" && BP ? { def: BP, st: bp, setSt: setBp }
            : btn.kind === "levelSheets" && LSH ? { def: LSH, st: lsh, setSt: setLsh }
              : null;
    const title = stateful ? stateful.def.title : Comp ? Comp.title : btn.label;
    const subtitle = stateful ? stateful.def.subtitle : Comp ? Comp.subtitle : "Command stub · pending UI";

    let body, footer;
    if (stateful) {
      body = <stateful.def.Body state={stateful.st} setState={stateful.setSt} />;
      footer = stateful.def.footer(onClose, stateful.st, stateful.setSt);
    } else if (btn.kind === "exportExcel") {
      body = <ExportExcel onClose={onClose} state={xls} setState={setXls} />;
      footer = ExportExcel.footer(onClose, xls);
    } else if (Comp) {
      body = <Comp onClose={onClose} />;
      footer = Comp.footer ? Comp.footer(onClose) : null;
    } else {
      body = <CommandStub btn={btn} onClose={onClose} />;
      footer = CommandStub.footer(onClose);
    }

    const Shell = surface === "dock" ? DockShell : ModalShell;
    const banner = stateful && stateful.def.banner ? stateful.def.banner(stateful.st) : null;
    return (
      <Shell btn={btn} title={title} subtitle={subtitle} onClose={onClose} footer={footer} banner={banner} inline={inline} width={stateful ? (stateful.def.width || 600) : 560}
        bodyFill={!!(stateful && stateful.def.bodyFill && stateful.def.bodyFill(stateful.st))}>
        {body}
      </Shell>
    );
  }

  Object.assign(window, { ToolHost, ModalShell, DockShell });
})();
