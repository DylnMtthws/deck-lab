(function () {
  "use strict";

  var HOVER_MS = 60;
  var EMPTY_HINT = "Hover or focus a card to preview it";
  var ROLE_LABELS = {
    ramp: "Ramp",
    draw: "Draw",
    removal: "Removal",
    protection: "Protection",
    counter: "Counter",
    free: "Free interaction",
    tutor: "Tutor",
    combo: "Combo",
    engine: "Engine",
    board_wipe: "Board wipe",
    recursion: "Recursion",
    wincon: "Win condition",
    land: "Land",
    utility: "Utility",
    other: "Other"
  };

  var panel = document.querySelector("[data-card-panel]");
  var currentId = null;
  var lastHoverId = null;
  var pairActiveId = null;
  var seenView = null;
  var pinned = false;
  var hoverTimer = null;
  var renderCount = 0;

  function cardImage(card) {
    // Same rule as cardImage() in deck-lab-builder.js.
    if (card.image_uri) return card.image_uri;
    return card.name
      ? "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" +
        encodeURIComponent(card.name)
      : "";
  }

  function readState() {
    var api = window.DeckLabBuilder;
    if (!api || typeof api.getState !== "function") return null;
    return api.getState();
  }

  function findEntry(entryId) {
    var state = readState();
    var entries = (state && state.entries) || [];
    var wanted = String(entryId);
    for (var i = 0; i < entries.length; i += 1) {
      if (String(entries[i].id) === wanted) return entries[i];
    }
    return null;
  }

  function inactive() {
    var root = document.querySelector(".dl-builder");
    if (root && root.classList.contains("right-collapsed")) return true;
    var media = window.matchMedia ? window.matchMedia("(max-width: 767px)") : null;
    return !!(media && media.matches);
  }

  function roleLabel(role) {
    var current = String(role || "").toLowerCase();
    if (!current) return "—";
    if (ROLE_LABELS[current]) return ROLE_LABELS[current];
    return current.replace(/_/g, " ").replace(/\b\w/g, function (letter) {
      return letter.toUpperCase();
    });
  }

  function zoneLabel(entry) {
    if (entry.is_commander) return "Commander";
    var state = readState();
    var zones = (state && state.zones) || [];
    for (var i = 0; i < zones.length; i += 1) {
      if (zones[i].id === entry.zone_id) return zones[i].name;
    }
    return "Unsorted";
  }

  function issueTexts(entry) {
    var state = readState();
    var map = state && state.validation && state.validation.entry_issues;
    var items = (map && entry && map[entry.id]) || [];
    return items.map(function (item) {
      if (item && typeof item === "object") return String(item.message || item.code || "");
      return String(item || "");
    }).filter(Boolean);
  }

  function appendSymbol(container, symbol, raw) {
    var api = window.DeckLabMana;
    if (api && typeof api.symbol === "function") {
      var node = api.symbol(symbol);
      if (node) {
        container.appendChild(node);
        return;
      }
    }
    container.appendChild(document.createTextNode(raw));
  }

  function fillSymbols(container, text) {
    var raw = String(text || "");
    if (!raw) return false;
    var pattern = /\{([^}]+)\}/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(raw))) {
      if (match.index > last) {
        container.appendChild(document.createTextNode(raw.slice(last, match.index)));
      }
      appendSymbol(container, match[1], match[0]);
      last = match.index + match[0].length;
    }
    if (last < raw.length) container.appendChild(document.createTextNode(raw.slice(last)));
    return true;
  }

  function syncPin(button) {
    button.setAttribute("aria-pressed", pinned ? "true" : "false");
    button.setAttribute("aria-label", pinned ? "Unpin card preview" : "Pin card preview");
    button.setAttribute("data-dl-tip", pinned ? "Unpin this card" : "Pin this card");
    button.classList.toggle("is-on", pinned);
    button.replaceChildren();
    if (window.DeckLabIcons && typeof window.DeckLabIcons.svg === "function") {
      button.appendChild(window.DeckLabIcons.svg(pinned ? "pin-off" : "pin", { size: 16 }));
    } else {
      button.textContent = pinned ? "Pinned" : "Pin";
    }
  }

  function ensurePin() {
    var button = document.querySelector("[data-card-panel-pin]");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "dl-icon-button dl-card-panel-pin";
      button.setAttribute("data-card-panel-pin", "");
    }
    if (!button._pinBound) {
      button._pinBound = true;
      button.type = "button";
      button.addEventListener("click", function () {
        if (!pinned && pairActiveId && String(currentId) !== String(pairActiveId)) show(pairActiveId);
        pinned = !pinned;
        syncPin(button);
        if (pinned) {
          clearTimeout(hoverTimer);
          hoverTimer = null;
        }
      });
    }
    syncPin(button);
    return button;
  }

  var ROLE_OPTIONS = [
    ["", "Add role"],
    ["ramp", "Ramp"],
    ["draw", "Draw"],
    ["removal", "Removal"],
    ["protection", "Protection"],
    ["counter", "Counter"],
    ["free", "Free interaction"],
    ["tutor", "Tutor"],
    ["combo", "Combo"],
    ["engine", "Engine"],
    ["board_wipe", "Board wipe"],
    ["recursion", "Recursion"],
    ["wincon", "Win condition"],
    ["land", "Land"],
    ["utility", "Utility"],
    ["other", "Other"]
  ];

  function roleSelect(entry) {
    var select = document.createElement("select");
    select.className = "dl-select dl-role-select";
    select.setAttribute("aria-label", "Role for " + entry.name);
    var current = String(entry.role || "").toLowerCase();
    var options = ROLE_OPTIONS.slice();
    if (current && !options.some(function (item) { return item[0] === current; })) {
      options.push([current, roleLabel(current)]);
    }
    options.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      option.selected = item[0] === current;
      select.appendChild(option);
    });
    select.value = current;
    var root = document.querySelector(".dl-builder");
    if (root && root.getAttribute("data-shared") === "true") select.disabled = true;
    select.addEventListener("change", function () {
      var api = window.DeckLabBuilder;
      if (!api || typeof api.command !== "function" || select.disabled) return;
      api.command([{ type: "set_role", entry_id: entry.id, role: select.value }]);
    });
    return select;
  }

  function panelFeedback(entry) {
    var root = document.querySelector(".dl-builder");
    if (root && root.getAttribute("data-shared") === "true") return null;
    var group = labeled("span", "dl-card-feedback dl-card-panel-feedback");
    group.setAttribute("data-card-panel-feedback", "");
    [
      ["up", "thumb-up", "Good pick"],
      ["down", "thumb-down", "Bad pick"],
      ["comment", "comment", "Comment on " + entry.name]
    ].forEach(function (spec) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "dl-icon-button";
      btn.setAttribute("aria-label", spec[2]);
      btn.setAttribute("data-dl-tip", spec[2]);
      if (spec[0] === "comment") btn.setAttribute("data-card-comment", "");
      else {
        btn.setAttribute("data-vote", spec[0]);
        btn.setAttribute("aria-pressed", "false");
      }
      if (window.DeckLabIcons && typeof window.DeckLabIcons.svg === "function") {
        btn.appendChild(window.DeckLabIcons.svg(spec[1], { size: 16 }));
      }
      btn.addEventListener("click", function () {
        var id = String(entry.id).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        var row = document.querySelector('[data-entry-id="' + id + '"]');
        if (!row) return;
        var target = spec[0] === "comment"
          ? row.querySelector("[data-card-comment]")
          : row.querySelector('[data-vote="' + spec[0] + '"]');
        if (target && target !== btn && typeof target.click === "function") target.click();
      });
      group.appendChild(btn);
    });
    return group;
  }

  function ensureSlot() {
    var slot = panel.querySelector('[data-card-panel-slot="evidence"]');
    if (!slot) {
      slot = document.createElement("div");
      slot.setAttribute("data-card-panel-slot", "evidence");
    }
    return slot;
  }

  function openLikeRow(entry) {
    var id = String(entry.id).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    var opener = document.querySelector('[data-card-focus-key="preview:' + id + '"]');
    if (!opener) opener = document.querySelector('[data-card-focus-key="name:' + id + '"]');
    if (opener && typeof opener.click === "function") opener.click();
  }

  function showNameFallback(frame, name) {
    frame.classList.remove("is-loading");
    frame.classList.add("is-error");
    var skeleton = frame.querySelector("[data-card-panel-skeleton]");
    if (skeleton) skeleton.hidden = true;
    var fallback = document.createElement("p");
    fallback.className = "dl-card-panel-fallback";
    fallback.textContent = name;
    frame.appendChild(fallback);
  }

  function buildFrame(entry) {
    var frame = document.createElement("div");
    frame.className = "dl-card-panel-frame is-loading";
    var skeleton = document.createElement("div");
    skeleton.className = "dl-card-panel-skeleton";
    skeleton.setAttribute("data-card-panel-skeleton", "");
    skeleton.setAttribute("aria-hidden", "true");
    frame.appendChild(skeleton);
    var url = cardImage(entry);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "dl-card-panel-image-btn";
    button.setAttribute("aria-label", "View card image for " + entry.name);
    button.addEventListener("click", function () { openLikeRow(entry); });
    if (!url) {
      showNameFallback(frame, entry.name);
      frame.appendChild(button);
      return frame;
    }
    var img = document.createElement("img");
    img.alt = entry.name;
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("load", function () {
      frame.classList.remove("is-loading");
      skeleton.hidden = true;
    });
    img.addEventListener("error", function () {
      if (img.parentNode) img.parentNode.removeChild(img);
      showNameFallback(frame, entry.name);
    });
    img.src = url;
    button.appendChild(img);
    frame.appendChild(button);
    return frame;
  }

  function labeled(tag, className, text) {
    var el = document.createElement(tag);
    el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }

  function commanderList(entries) {
    var list = [];
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].is_commander) list.push(entries[i]);
    }
    return list;
  }

  function pairFor(entry) {
    if (!entry || !entry.is_commander) return null;
    var state = readState();
    var cmds = commanderList((state && state.entries) || []);
    if (cmds.length !== 2) return null;
    for (var i = 0; i < cmds.length; i += 1) {
      if (String(cmds[i].id) === String(entry.id)) return cmds;
    }
    return null;
  }

  function syncPairPressed(strip) {
    if (!strip) return;
    var buttons = strip.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i += 1) {
      var on = String(buttons[i].getAttribute("data-pair-entry")) === String(pairActiveId);
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
      buttons[i].classList.toggle("is-active", on);
    }
  }

  function buildPairStrip(cmds, key) {
    var strip = document.createElement("div");
    strip.className = "dl-card-panel-pair";
    strip.setAttribute("data-card-panel-pair", "");
    strip.setAttribute("data-pair-key", key);
    cmds.forEach(function (cmd) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "dl-card-panel-pair-card";
      button.setAttribute("data-pair-entry", String(cmd.id));
      button.setAttribute("aria-label", "Show " + cmd.name);
      var img = document.createElement("img");
      img.alt = "";
      var url = cardImage(cmd);
      if (url) img.src = url;
      var caption = document.createElement("span");
      caption.className = "dl-card-panel-pair-name";
      caption.textContent = cmd.name;
      button.append(img, caption);
      button.addEventListener("mouseenter", function () {
        if (inactive() || pinned) return;
        if (String(cmd.id) === String(pairActiveId)) return;
        show(cmd.id, true);
      });
      button.addEventListener("mouseleave", function () {
        if (inactive() || pinned) return;
        if (!pairActiveId || String(currentId) === String(pairActiveId)) return;
        show(pairActiveId, true);
      });
      button.addEventListener("focus", function () {
        if (inactive() || pinned) return;
        if (String(cmd.id) === String(pairActiveId)) return;
        show(cmd.id, true);
      });
      button.addEventListener("blur", function () {
        if (inactive() || pinned) return;
        if (!pairActiveId || String(currentId) === String(pairActiveId)) return;
        show(pairActiveId, true);
      });
      button.addEventListener("click", function () {
        pairActiveId = String(cmd.id);
        show(cmd.id);
      });
      strip.appendChild(button);
    });
    return strip;
  }

  function detailNodes(entry, includeFrame) {
    var name = labeled("h3", "dl-card-panel-name", entry.name);
    name.setAttribute("data-card-panel-name", "");
    var cost = labeled("div", "dl-card-panel-cost");
    cost.setAttribute("data-card-panel-cost", "");
    if (!fillSymbols(cost, entry.mana_cost)) cost.textContent = "—";
    var title = labeled("div", "dl-card-panel-title");
    title.append(name, cost);
    var typeLine = labeled("p", "dl-card-panel-type", entry.type_line || "—");
    typeLine.setAttribute("data-card-panel-type", "");
    var oracle = labeled("p", "dl-card-panel-oracle");
    oracle.setAttribute("data-card-panel-oracle", "");
    fillSymbols(oracle, entry.oracle_text || "");
    var meta = labeled("div", "dl-card-panel-meta");
    meta.setAttribute("data-card-panel-meta", "");
    var copies = Number(entry.quantity == null ? 0 : entry.quantity);
    var qtyChip = labeled("span", "dl-chip");
    var qty = labeled("span", "", String(copies));
    qty.setAttribute("data-card-panel-qty", "");
    qtyChip.append(qty, document.createTextNode(copies === 1 ? " copy" : " copies"));
    var zone = labeled("span", "dl-chip", zoneLabel(entry));
    zone.setAttribute("data-card-panel-zone", "");
    var role = labeled("span", "dl-visually-hidden", roleLabel(entry.role));
    role.setAttribute("data-card-panel-role", "");
    role.setAttribute("aria-hidden", "true");
    meta.append(qtyChip, zone, role, roleSelect(entry));
    var feedback = panelFeedback(entry);
    if (feedback) meta.appendChild(feedback);
    var nodes = [];
    if (includeFrame) nodes.push(buildFrame(entry));
    nodes.push(title, typeLine, oracle, meta);
    var issues = issueTexts(entry);
    if (issues.length) {
      var list = document.createElement("ul");
      list.className = "dl-card-panel-issues";
      list.setAttribute("role", "note");
      issues.forEach(function (text) {
        list.appendChild(labeled("li", "dl-chip is-warn", text));
      });
      nodes.push(list);
    }
    return nodes;
  }

  function mountPanel(nodes) {
    var pin = ensurePin();
    var slot = ensureSlot();
    if (!pin.parentNode || panel.contains(pin)) nodes.push(pin);
    nodes.push(slot);
    panel.replaceChildren.apply(panel, nodes);
  }

  function renderPair(cmds, entry) {
    var key = cmds.map(function (item) { return String(item.id); }).join("|");
    var strip = panel.querySelector("[data-card-panel-pair]");
    var body = panel.querySelector("[data-card-panel-body]");
    var reusable = strip && body && strip.getAttribute("data-pair-key") === key;
    if (!reusable) {
      strip = buildPairStrip(cmds, key);
      body = document.createElement("div");
      body.className = "dl-card-panel-body";
      body.setAttribute("data-card-panel-body", "");
      mountPanel([strip, body]);
    }
    syncPairPressed(strip);
    body.replaceChildren.apply(body, detailNodes(entry, false));
  }

  function renderEntry(entry) {
    renderCount += 1;
    panel.setAttribute("data-card-panel-render", String(renderCount));
    var cmds = pairFor(entry);
    if (cmds) {
      renderPair(cmds, entry);
      return;
    }
    mountPanel(detailNodes(entry, true));
  }

  function renderEmpty() {
    currentId = null;
    var hint = labeled("p", "dl-muted dl-card-panel-empty", EMPTY_HINT);
    panel.replaceChildren(hint);
  }

  function show(entryId, preview) {
    if (!panel || inactive()) return;
    if (pinned && currentId) return;
    var entry = findEntry(entryId);
    if (!entry) return;
    var cmds = pairFor(entry);
    if (!preview) {
      pairActiveId = cmds ? String(entry.id) : null;
    }
    if (String(entry.id) === String(currentId) && panel.querySelector("[data-card-panel-name]")) {
      if (cmds) syncPairPressed(panel.querySelector("[data-card-panel-pair]"));
      return;
    }
    clearTimeout(hoverTimer);
    hoverTimer = null;
    lastHoverId = String(entry.id);
    currentId = String(entry.id);
    renderEntry(entry);
  }

  function preload(entry) {
    if (!entry || !entry.id) return;
    var key = String(entry.id);
    if (preload.seen[key]) return;
    var url = cardImage(entry);
    if (!url) return;
    preload.seen[key] = true;
    var img = new Image();
    img.decoding = "async";
    img.src = url;
  }
  preload.seen = Object.create(null);

  function onHover(event) {
    if (inactive() || pinned) return;
    var id = event.detail && event.detail.entryId;
    if (!id || String(id) === lastHoverId) return;
    lastHoverId = String(id);
    var entry = findEntry(id);
    if (!entry) return;
    preload(entry);
    if (String(id) === String(currentId)) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(function () {
      hoverTimer = null;
      if (pinned || inactive()) return;
      show(id);
    }, HOVER_MS);
  }

  function onFocus(event) {
    if (inactive() || pinned) return;
    var id = event.detail && event.detail.entryId;
    if (!id) return;
    clearTimeout(hoverTimer);
    hoverTimer = null;
    if (String(id) === String(currentId)) {
      lastHoverId = String(id);
      return;
    }
    show(id);
  }

  function trackedView(state) {
    var root = document.querySelector(".dl-builder");
    var enabled = !!(root && root.getAttribute("data-playmat-enabled") === "true");
    var media = window.matchMedia ? window.matchMedia("(max-width: 767px)") : null;
    if (!enabled || (media && media.matches)) return "table";
    var prefs = (state && state.preferences) || {};
    return prefs.view_mode || "playmat";
  }

  function showDefault() {
    if (!panel || inactive()) return;
    var state = readState();
    var entries = (state && state.entries) || [];
    var cmds = commanderList(entries);
    if (!cmds.length) {
      pairActiveId = null;
      renderEmpty();
      return;
    }
    pairActiveId = String(cmds[0].id);
    currentId = null;
    show(cmds[0].id);
  }

  function onViewChange() {
    clearTimeout(hoverTimer);
    hoverTimer = null;
    lastHoverId = null;
    if (pinned && currentId) return;
    showDefault();
    lastHoverId = null;
  }

  function onBuilderRender(state) {
    var view = trackedView(state || readState());
    if (seenView === null) {
      seenView = view;
      return;
    }
    if (view === seenView) return;
    seenView = view;
    onViewChange();
  }

  function boot() {
    if (!panel) return;
    if (document.querySelector("[data-card-panel-pin]")) ensurePin();
    if (inactive()) return;
    showDefault();
  }

  function attachBuilder() {
    var api = window.DeckLabBuilder;
    if (!api || typeof api.getState !== "function") return false;
    seenView = trackedView(api.getState());
    if (typeof api.onRender === "function") api.onRender(onBuilderRender);
    boot();
    return true;
  }

  window.DeckLabCardPanel = {
    show: function (entryId) { show(entryId); },
    current: function () { return currentId; }
  };

  if (!panel) return;
  document.addEventListener("deck-lab:entry-hover", onHover);
  document.addEventListener("deck-lab:focus-entry", onFocus);
  if (!attachBuilder()) {
    boot();
    document.addEventListener("deck-lab:ready", function () {
      attachBuilder();
    });
  }
})();
