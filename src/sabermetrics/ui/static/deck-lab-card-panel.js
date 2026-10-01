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
    button.textContent = pinned ? "Pinned" : "Pin";
  }

  function ensurePin() {
    var button = panel.querySelector("[data-card-panel-pin]");
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "dl-card-panel-pin";
      button.setAttribute("data-card-panel-pin", "");
      button.addEventListener("click", function () {
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

  function renderEntry(entry) {
    renderCount += 1;
    panel.setAttribute("data-card-panel-render", String(renderCount));
    var name = labeled("h3", "dl-card-panel-name", entry.name);
    name.setAttribute("data-card-panel-name", "");
    var cost = labeled("div", "dl-card-panel-cost");
    cost.setAttribute("data-card-panel-cost", "");
    if (!fillSymbols(cost, entry.mana_cost)) cost.textContent = "—";
    var typeLine = labeled("p", "dl-card-panel-type", entry.type_line || "—");
    typeLine.setAttribute("data-card-panel-type", "");
    var oracle = labeled("p", "dl-card-panel-oracle");
    oracle.setAttribute("data-card-panel-oracle", "");
    fillSymbols(oracle, entry.oracle_text || "");
    var meta = labeled("p", "dl-card-panel-meta");
    var qty = labeled("span", "", String(entry.quantity == null ? 0 : entry.quantity));
    qty.setAttribute("data-card-panel-qty", "");
    var zone = labeled("span", "", zoneLabel(entry));
    zone.setAttribute("data-card-panel-zone", "");
    var role = labeled("span", "", roleLabel(entry.role));
    role.setAttribute("data-card-panel-role", "");
    meta.append(qty, zone, role);
    var body = labeled("div", "dl-card-panel-body");
    body.append(name, cost, typeLine, oracle, meta);
    var issues = issueTexts(entry);
    if (issues.length) {
      var list = document.createElement("ul");
      list.className = "dl-card-panel-issues";
      list.setAttribute("role", "note");
      issues.forEach(function (text) {
        list.appendChild(labeled("li", "", text));
      });
      body.appendChild(list);
    }
    panel.replaceChildren(buildFrame(entry), body, ensurePin(), ensureSlot());
  }

  function renderEmpty() {
    currentId = null;
    var hint = labeled("p", "dl-muted dl-card-panel-empty", EMPTY_HINT);
    panel.replaceChildren(hint);
  }

  function show(entryId) {
    if (!panel || inactive()) return;
    if (pinned && currentId) return;
    var entry = findEntry(entryId);
    if (!entry) return;
    if (String(entry.id) === String(currentId) && panel.querySelector("[data-card-panel-name]")) return;
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

  function firstCommander(entries) {
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].is_commander) return entries[i];
    }
    return null;
  }

  function boot() {
    if (!panel || inactive()) return;
    var state = readState();
    var entries = (state && state.entries) || [];
    if (!entries.length) {
      renderEmpty();
      return;
    }
    var commander = firstCommander(entries);
    if (commander) show(commander.id);
    else show(entries[0].id);
  }

  window.DeckLabCardPanel = {
    show: function (entryId) { show(entryId); },
    current: function () { return currentId; }
  };

  if (!panel) return;
  document.addEventListener("deck-lab:entry-hover", onHover);
  document.addEventListener("deck-lab:focus-entry", onFocus);
  boot();
})();
