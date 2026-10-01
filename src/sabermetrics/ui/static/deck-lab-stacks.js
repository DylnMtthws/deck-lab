(function () {
  "use strict";

  var ROLE_SWATCH = {
    commander: "#e94560",
    ramp: "#57d39b",
    draw: "#5aa6f0",
    tutor: "#b48cf0",
    removal: "#f0bd67",
    protection: "#f0bd67",
    counter: "#f0bd67",
    free: "#f0bd67",
    wincon: "#ff8093",
    land: "#98a0bd",
    combo: "#b48cf0",
    engine: "#5aa6f0",
    board_wipe: "#f0bd67",
    recursion: "#5aa6f0",
    utility: "#98a0bd",
    other: "#6b7394",
    none: "#6b7394",
    private: "#6b7394"
  };
  var PALETTE = ["#e94560", "#57d39b", "#5aa6f0", "#b48cf0", "#f0bd67", "#ff8093", "#98a0bd", "#6b7394"];
  var feedbackMap = null;
  var feedbackLoading = false;
  var selectionBound = false;
  var booted = false;
  var dragPreview = null;
  var dragEntryId = "";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function apiOr(passed) {
    return passed || window.DeckLabBuilder || null;
  }
  function prefs(api) {
    var state = api && api.getState ? api.getState() : null;
    return (state && state.preferences) || {};
  }
  function groupMode(api) {
    return prefs(api).group_mode || "zone";
  }
  function qty(entries) {
    return (entries || []).reduce(function (sum, entry) { return sum + Number(entry.quantity || 0); }, 0);
  }
  function imageOf(entry) {
    if (entry.image_uri) return entry.image_uri;
    return entry.name ? "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + encodeURIComponent(entry.name) : "";
  }
  function swatchFor(group, mode) {
    if (group.id === "commander") return ROLE_SWATCH.commander;
    if (mode === "role") {
      var key = String(group.id).indexOf("role-") === 0 ? group.id.slice(5) : "";
      return ROLE_SWATCH[key] || "#6b7394";
    }
    var name = String(group.name || ""), n = 0, i;
    for (i = 0; i < name.length; i++) n = (n + name.charCodeAt(i) * (i + 1)) % PALETTE.length;
    return PALETTE[n];
  }
  function feedbackOf(entry) {
    if (entry.feedback && typeof entry.feedback === "object") return entry.feedback;
    if (typeof entry.feedback === "string" && entry.feedback) return { vote: entry.feedback };
    if (entry.vote || entry.comment) return { vote: entry.vote || null, comment: entry.comment || "" };
    var key = entry.oracle_id || ("card:" + (entry.card_id || ""));
    if (feedbackMap && feedbackMap[key]) return feedbackMap[key];
    return null;
  }
  function feedbackOn(fb) {
    return !!(fb && (fb.vote || fb.comment));
  }
  function icon(name) {
    if (window.DeckLabIcons && window.DeckLabIcons.svg) return window.DeckLabIcons.svg(name, { size: 13 });
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "13");
    svg.setAttribute("height", "13");
    svg.setAttribute("aria-hidden", "true");
    return svg;
  }
  function paintSelection(ids) {
    var selected = {};
    (ids || []).forEach(function (id) { selected[id] = true; });
    document.querySelectorAll(".dl-stack-card").forEach(function (card) {
      var on = !!selected[card.getAttribute("data-entry-id")];
      card.classList.toggle("is-selected", on);
      card.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function bindSelection() {
    if (selectionBound || !document.addEventListener) return;
    selectionBound = true;
    document.addEventListener("deck-lab:selection", function (event) {
      paintSelection(event.detail && event.detail.ids);
    });
  }
  function dropAction(group, mode) {
    if (mode === "type" || group.id === "commander") return { allowed: false };
    if (mode === "role") {
      if (group.id === "role-private" || String(group.id).indexOf("role-") !== 0) return { allowed: false };
      var role = group.id.slice(5);
      if (role === "none") role = "";
      return { allowed: true, kind: "set_role", role: role };
    }
    var zoneId = group.zone && group.zone.id ? group.zone.id : group.id;
    if (!zoneId || zoneId === "commander") return { allowed: false };
    return { allowed: true, kind: "move_entry", zoneId: zoneId };
  }
  function clearPreview() {
    if (dragPreview && dragPreview.parentNode) dragPreview.parentNode.removeChild(dragPreview);
    dragPreview = null;
    dragEntryId = "";
    document.querySelectorAll(".dl-stacks.is-dragging").forEach(function (board) { board.classList.remove("is-dragging"); });
    document.querySelectorAll(".drag-source,.drop-target,.is-drop-disallowed").forEach(function (node) {
      node.classList.remove("drag-source", "drop-target", "is-drop-disallowed");
    });
  }
  function positionPreview(event) {
    if (!dragPreview) return;
    var x = Number(event.clientX || 0), y = Number(event.clientY || 0);
    dragPreview.style.left = Math.round(x - 36) + "px";
    dragPreview.style.top = Math.round(y - 20) + "px";
  }
  function beginDrag(event, card, entry) {
    var transfer = event.dataTransfer;
    if (!transfer) return;
    dragEntryId = entry.id;
    transfer.effectAllowed = "move";
    if (transfer.setData) {
      transfer.setData("text/deck-entry", entry.id);
      transfer.setData("text/deck-group", card.getAttribute("data-group-id") || "");
    }
    card.classList.add("drag-source");
    var board = card.closest(".dl-stacks");
    if (board) board.classList.add("is-dragging");
    if (dragPreview && dragPreview.parentNode) dragPreview.parentNode.removeChild(dragPreview);
    dragPreview = el("div", "dl-drag-preview");
    dragPreview.setAttribute("aria-hidden", "true");
    var art = card.querySelector("img");
    var url = art && (art.currentSrc || art.src);
    if (url) {
      var image = el("img");
      image.src = url;
      image.alt = "";
      dragPreview.appendChild(image);
    } else dragPreview.appendChild(el("span", "", entry.name));
    document.body.appendChild(dragPreview);
    positionPreview(event);
    if (transfer.setDragImage) {
      try {
        var ghost = document.createElement("canvas");
        ghost.width = 1;
        ghost.height = 1;
        transfer.setDragImage(ghost, 0, 0);
      } catch (_) {}
    }
  }
  function entryById(api, id) {
    var state = api && api.getState ? api.getState() : null;
    var entries = state && state.entries || [];
    for (var i = 0; i < entries.length; i++) if (entries[i].id === id) return entries[i];
    return null;
  }
  function commandFor(api, entry, action) {
    if (!api || !api.command || !entry || entry.is_commander) return null;
    if (action.kind === "move_entry") {
      if (entry.zone_id === action.zoneId) return null;
      return { type: "move_entry", entry_id: entry.id, zone_id: action.zoneId, sort_order: 999 };
    }
    if (action.kind === "set_role") {
      if (String(entry.role || "") === String(action.role || "")) return null;
      return { type: "set_role", entry_id: entry.id, role: action.role };
    }
    return null;
  }
  function openImage(entry, opener) {
    var url = imageOf(entry);
    var dialog = document.getElementById("card-image-dialog");
    if (!dialog || !url) return;
    var title = dialog.querySelector("[data-card-image-title]");
    var img = dialog.querySelector("[data-card-image]");
    var status = dialog.querySelector("[data-card-image-status]");
    var original = dialog.querySelector("[data-card-image-original]");
    if (opener) opener.setAttribute("data-card-focus-key", "stack:" + entry.id);
    if (title) title.textContent = entry.name;
    if (original) {
      original.setAttribute("href", url);
      original.setAttribute("aria-label", "Open the original image for " + entry.name + " in a new tab");
    }
    if (status) { status.hidden = false; status.textContent = "Loading card image…"; }
    if (img) { img.hidden = true; img.alt = entry.name; img.src = url; }
    dialog.returnValue = "";
    if (dialog.showModal) dialog.showModal();
  }
  function cardsIn(col) {
    return Array.prototype.slice.call(col.querySelectorAll(".dl-stack-card"));
  }
  function onKey(event, api) {
    var card = event.target && event.target.closest ? event.target.closest(".dl-stack-card") : null;
    if (!card) return;
    var board = card.closest(".dl-stacks");
    if (!board) return;
    var col = card.closest(".dl-stack-col");
    var cols = Array.prototype.slice.call(board.querySelectorAll(".dl-stack-col"));
    var cards = cardsIn(col);
    var index = cards.indexOf(card);
    var colIndex = cols.indexOf(col);
    var next = null;
    if (event.key === "ArrowDown" && index >= 0 && index < cards.length - 1) next = cards[index + 1];
    else if (event.key === "ArrowUp" && index > 0) next = cards[index - 1];
    else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      var destCol = event.key === "ArrowRight" ? cols[colIndex + 1] : cols[colIndex - 1];
      if (!destCol) return;
      var destCards = cardsIn(destCol);
      if (!destCards.length) return;
      next = destCards[Math.min(Math.max(index, 0), destCards.length - 1)];
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (api && api.focusEntry) api.focusEntry(card.getAttribute("data-entry-id"));
      return;
    } else return;
    if (!next) return;
    event.preventDefault();
    next.focus();
  }
  function ensureFeedback(api) {
    if (feedbackMap || feedbackLoading || !api || api.shared || !api.getState) return;
    var state = api.getState();
    if (!state || !state.id || typeof fetch !== "function") return;
    feedbackLoading = true;
    fetch("/api/decks/" + encodeURIComponent(state.id) + "/feedback")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var cards = data && data.cards;
        feedbackMap = cards || {};
        feedbackLoading = false;
        if (cards && Object.keys(cards).length && prefs(api).display_mode === "stacks" && api.render) api.render();
      })
      .catch(function () { feedbackMap = {}; feedbackLoading = false; });
  }
  function markMode(api) {
    if (!document.body) return;
    document.body.classList.toggle("dl-has-stacks", prefs(api).display_mode === "stacks");
  }
  function render(groups, container, passedApi) {
    var api = apiOr(passedApi);
    if (!container) return;
    bindSelection();
    markMode(api);
    var mode = groupMode(api);
    var shared = !!(api && api.shared);
    var selected = {};
    if (api && api.getSelection) api.getSelection().forEach(function (id) { selected[id] = true; });
    container.replaceChildren();
    var board = el("div", "dl-stacks" + (mode === "type" ? " is-group-type" : ""));
    board.setAttribute("data-stacks", "");
    board.setAttribute("data-group-mode", mode);
    (groups || []).forEach(function (group) {
      var entries = group.entries || [];
      var action = dropAction(group, mode);
      var column = el("section", "dl-stack-col");
      column.setAttribute("data-group-id", group.id);
      if (!action.allowed) column.setAttribute("data-drop", "disallowed");
      else column.setAttribute("data-drop", action.kind);
      var head = el("h3", "dl-stack-head");
      var swatch = el("span", "dl-stack-swatch");
      swatch.setAttribute("aria-hidden", "true");
      swatch.setAttribute("style", "background:" + swatchFor(group, mode));
      var name = el("span", "dl-stack-name", group.name);
      var count = el("span", "dl-stack-count", String(qty(entries)));
      head.append(swatch, name, count);
      var stack = el("div", "dl-stack");
      entries.forEach(function (entry, index) {
        var card = el("div", "dl-stack-card");
        card.setAttribute("role", "button");
        card.setAttribute("data-entry-id", entry.id);
        card.setAttribute("data-group-id", group.id);
        card.setAttribute("data-stack-face", index === entries.length - 1 ? "full" : "peek");
        card.tabIndex = 0;
        var copies = Number(entry.quantity || 0);
        var fb = feedbackOf(entry);
        var label = entry.name || "Card";
        if (copies > 1) label += ", " + copies + " copies";
        if (feedbackOn(fb)) label += fb.vote === "down" ? ", thumbs down" : ", thumbs up";
        card.setAttribute("aria-label", label);
        card.setAttribute("aria-pressed", selected[entry.id] ? "true" : "false");
        if (selected[entry.id]) card.classList.add("is-selected");
        card.draggable = !shared && !entry.is_commander;
        var url = imageOf(entry);
        if (url) {
          var img = el("img");
          img.src = url;
          img.alt = "";
          img.setAttribute("alt", "");
          card.appendChild(img);
          card.style.backgroundImage = "url('" + url.replace(/'/g, "%27") + "')";
        } else card.appendChild(el("span", "dl-stack-fallback", entry.name || "Card"));
        if (copies > 1) card.appendChild(el("span", "dl-stack-qty", copies + "\u00d7"));
        if (feedbackOn(fb)) {
          var badge = el("span", "dl-stack-feedback");
          var vote = fb.vote === "down" ? "down" : (fb.vote === "up" ? "up" : "comment");
          badge.setAttribute("data-feedback", vote);
          badge.setAttribute("title", vote === "down" ? "Thumbs down" : "Thumbs up");
          badge.appendChild(icon(vote === "down" ? "thumb-down" : "thumb-up"));
          card.appendChild(badge);
        }
        card.addEventListener("mouseenter", function () { card.classList.add("is-lifted"); });
        card.addEventListener("mouseleave", function () { card.classList.remove("is-lifted"); });
        card.addEventListener("focus", function () { card.classList.add("is-lifted"); });
        card.addEventListener("blur", function () { card.classList.remove("is-lifted"); });
        card.addEventListener("click", function (event) {
          if (event.metaKey || event.ctrlKey) {
            event.preventDefault();
            if (!api || !api.getSelection || !api.setSelection || entry.is_commander) return;
            var ids = api.getSelection().slice();
            var at = ids.indexOf(entry.id);
            if (at >= 0) ids.splice(at, 1);
            else ids.push(entry.id);
            api.setSelection(ids);
            return;
          }
          if (api && api.focusEntry) api.focusEntry(entry.id);
        });
        card.addEventListener("dblclick", function (event) {
          event.preventDefault();
          openImage(entry, card);
        });
        card.addEventListener("dragstart", function (event) {
          if (shared || entry.is_commander) { event.preventDefault(); return; }
          beginDrag(event, card, entry);
        });
        card.addEventListener("dragend", clearPreview);
        card.addEventListener("keydown", function (event) { onKey(event, api); });
        stack.appendChild(card);
      });
      column.addEventListener("dragover", function (event) {
        var current = dropAction(group, groupMode(api));
        if (!current.allowed) {
          column.classList.add("is-drop-disallowed");
          if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
          return;
        }
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        column.classList.add("drop-target");
        positionPreview(event);
      });
      column.addEventListener("dragleave", function (event) {
        if (!column.contains(event.relatedTarget)) column.classList.remove("drop-target", "is-drop-disallowed");
      });
      column.addEventListener("drop", function (event) {
        var current = dropAction(group, groupMode(api));
        clearPreview();
        if (!current.allowed) return;
        event.preventDefault();
        var transfer = event.dataTransfer;
        var entryId = dragEntryId || (transfer && transfer.getData ? transfer.getData("text/deck-entry") : "");
        var entry = entryById(api, entryId);
        var command = commandFor(api, entry, current);
        if (command) api.command([command]);
      });
      column.append(head, stack);
      board.appendChild(column);
    });
    board.addEventListener("dragover", function (event) {
      if (dragPreview && !board.classList.contains("is-group-type")) positionPreview(event);
    });
    container.appendChild(board);
    ensureFeedback(api);
  }

  function boot() {
    if (booted) return;
    var api = window.DeckLabBuilder;
    if (!api) return;
    booted = true;
    bindSelection();
    if (api.onRender) api.onRender(function (state) {
      if (!document.body) return;
      var on = state && state.preferences && state.preferences.display_mode === "stacks";
      document.body.classList.toggle("dl-has-stacks", !!on);
    });
    if (prefs(api).display_mode === "stacks" && api.render) api.render();
  }

  window.DeckLabStacks = { render: render };
  if (window.DeckLabBuilder) boot();
  else document.addEventListener("deck-lab:ready", function () { boot(); });
})();
