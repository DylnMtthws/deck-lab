(function () {
  "use strict";

  var PATHS = {
    back: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H12"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H12"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    sliders: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="18" r="2"/>',
    more: '<circle cx="6" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="18" cy="12" r="1"/>',
    pin: '<path d="M12 21s7-5.4 7-11a7 7 0 1 0-14 0c0 5.6 7 11 7 11z"/><circle cx="12" cy="10" r="2"/>',
    "pin-off": '<path d="M9 4.5 15 9"/><path d="M8 9h8l-1 5.2A7 7 0 0 1 12 21a7 7 0 0 1-3-6.8z"/><path d="M4 4l16 16"/>',
    "thumb-up": '<path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/><path d="M7 11l3.2-6.2A2 2 0 0 1 12 3.6V8h6.2a2 2 0 0 1 2 2.3l-1 6.2A2 2 0 0 1 17.2 18H7"/>',
    "thumb-down": '<path d="M7 13V4H4a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1z"/><path d="M7 13l3.2 6.2A2 2 0 0 0 12 20.4V16h6.2a2 2 0 0 0 2-2.3l-1-6.2A2 2 0 0 0 17.2 6H7"/>',
    comment: '<path d="M5 6h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H9l-4 3v-3H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    x: '<path d="M6 6 18 18"/><path d="M18 6 6 18"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1"/>',
    stack: '<rect x="7" y="7" width="12" height="13" rx="1.5"/><path d="M5 16V5.5A1.5 1.5 0 0 1 6.5 4H16"/>',
    spread: '<path d="m7 19 2.2-13"/><rect x="9.5" y="5" width="7" height="14" rx="1"/><path d="m17 19-2.2-13"/>',
    layers: '<path d="m12 3 8 4.5-8 4.5L4 7.5z"/><path d="m4 12 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/>',
    alert: '<path d="M12 4 3 19h18z"/><path d="M12 9v5"/><path d="M12 17h.01"/>',
    filter: '<path d="M4 5h16l-6 7v6l-4 2v-8z"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/>',
    download: '<path d="M12 4v10"/><path d="m8 10 4 4 4-4"/><path d="M5 19h14"/>',
    external: '<path d="M14 5h5v5"/><path d="M19 5 10 14"/><path d="M17 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h5"/>',
    image: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m4 16 5-4 4 3 3-2 4 3"/>'
  };

  var NAMES = [
    "back", "undo", "redo", "search", "sliders", "more", "pin", "pin-off",
    "thumb-up", "thumb-down", "comment", "chevron-down", "x", "check", "plus",
    "minus", "grid", "stack", "spread", "layers", "alert", "filter", "copy",
    "download", "external", "image"
  ];

  function svg(name, opts) {
    if (!Object.prototype.hasOwnProperty.call(PATHS, name)) {
      throw new Error("Unknown Deck Lab icon: " + name);
    }
    opts = opts || {};
    var size = opts.size == null ? 16 : opts.size;
    var el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("width", String(size));
    el.setAttribute("height", String(size));
    el.setAttribute("fill", "none");
    el.setAttribute("stroke", "currentColor");
    el.setAttribute("stroke-width", "1.7");
    el.setAttribute("stroke-linecap", "round");
    el.setAttribute("stroke-linejoin", "round");
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = PATHS[name];
    return el;
  }

  window.DeckLabIcons = { svg: svg, names: NAMES.slice() };
})();
