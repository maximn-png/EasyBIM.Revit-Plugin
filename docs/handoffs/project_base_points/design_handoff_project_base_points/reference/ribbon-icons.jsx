// ribbon-icons.jsx
// Lucide icon helper (matches the EasyBIM design system, which renders via
// window.lucide.icons[Name].toSvg(...)). Falls back to the icon-node array form
// if a build of lucide doesn't expose toSvg, and to a neutral box if missing.

(function () {
  // Custom glyphs not in Lucide, drawn in the same 24-grid thin-line style.
  // Inner markup only (the helper wraps it in a stroke/round-cap <svg>).
  const CUSTOM_ICONS = {
    // Architectural section marker: vertical cut line + view-direction heads
    SectionMarker:
      '<path d="M7 3.5v17"/>' +
      '<path d="M7 7h7"/><path d="M11.5 4.5 14.5 7l-3 2.5"/>' +
      '<path d="M7 17h7"/><path d="M11.5 14.5 14.5 17l-3 2.5"/>',
    // Revit Project Base Point: circle with a diagonal cross through it
    ProjectBasePoint:
      '<circle cx="12" cy="12" r="9"/>' +
      '<path d="M5.64 5.64l12.72 12.72"/><path d="M18.36 5.64L5.64 18.36"/>',
    // Level Sheets: a sheet border with two stacked plan viewports + title strip
    LevelSheets:
      '<rect x="3" y="3" width="18" height="18" rx="1.5"/>' +
      '<rect x="5.5" y="5.5" width="10" height="5"/>' +
      '<rect x="5.5" y="13.5" width="10" height="5"/>' +
      '<path d="M18 5.5v13"/>',
  };

  function buildSvg(name, { size = 24, stroke = 1.75 } = {}) {
    const custom = CUSTOM_ICONS[name];
    if (custom) {
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${custom}</svg>`;
    }
    const L = window.lucide;
    if (!L || !L.icons) return null;
    const node = L.icons[name];
    if (!node) return null;
    if (typeof node.toSvg === "function") {
      return node.toSvg({ width: size, height: size, "stroke-width": stroke });
    }
    // Resolve the child-element list across lucide UMD shapes:
    //  - 0.544: node IS an array of [tag, attrs] pairs
    //  - older: node is [tag, attrs, children[]]
    try {
      let children;
      if (Array.isArray(node) && Array.isArray(node[0])) children = node;
      else children = node[2] || node.children || [];
      const inner = children
        .map((c) => {
          const tag = c[0];
          const a = c[1] || {};
          const attrs = Object.keys(a)
            .map((k) => `${k}="${a[k]}"`)
            .join(" ");
          return `<${tag} ${attrs}/>`;
        })
        .join("");
      return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
    } catch (e) {
      return null;
    }
  }

  // React component. Inherits color via currentColor.
  function Icon({ name, size = 24, stroke = 1.75, style, className }) {
    const ref = React.useRef(null);
    React.useEffect(() => {
      const el = ref.current;
      if (!el) return;
      const svg = buildSvg(name, { size, stroke });
      el.innerHTML =
        svg ||
        `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}"><rect x="4" y="4" width="16" height="16" rx="3"/></svg>`;
    }, [name, size, stroke]);
    return (
      <span
        ref={ref}
        className={className}
        aria-hidden="true"
        style={{ display: "inline-flex", lineHeight: 0, ...style }}
      ></span>
    );
  }

  Object.assign(window, { Icon, buildSvg });
})();
