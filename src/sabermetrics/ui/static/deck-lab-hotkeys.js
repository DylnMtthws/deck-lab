(function () {
  "use strict";
  var api = window.DeckLabBuilder;
  if (!api || api.shared) return;

  var currentId = null;

  function isField(el) {
    if (!el || !el.tagName) return false;
    var tag = String(el.tagName).toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (el.isContentEditable) return true;
    var editable = el.getAttribute && el.getAttribute("contenteditable");
    return editable === "" || editable === "true";
  }
  function inField(event) {
    var el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) el = event.target;
    while (el) {
      if (isField(el)) return true;
      el = el.parentNode;
    }
    return false;
  }
  function listRows() {
    var nodes = document.querySelectorAll("#table-view .dl-deck-row[data-entry-id]");
    return Array.prototype.filter.call(nodes, function (row) {
      var node = row;
      while (node) {
        if (node.hidden || (node.hasAttribute && node.hasAttribute("hidden"))) return false;
        node = node.parentNode;
      }
      return true;
    });
  }
  function currentEntry() {
    var state = api.getState ? api.getState() : null;
    if (!state || !currentId) return null;
    return (state.entries || []).find(function (entry) { return entry.id === currentId; }) || null;
  }
  function move(delta) {
    var rows = listRows();
    if (!rows.length) return;
    var ids = rows.map(function (row) { return row.getAttribute("data-entry-id"); });
    var index = ids.indexOf(currentId);
    if (index < 0) index = delta > 0 ? -1 : ids.length;
    var next = index + delta;
    if (next < 0) next = 0;
    if (next >= ids.length) next = ids.length - 1;
    api.focusEntry(ids[next]);
  }
  function toggleSelection() {
    if (!currentId) return;
    var ids = api.getSelection().slice();
    var pos = ids.indexOf(currentId);
    if (pos >= 0) ids.splice(pos, 1);
    else ids.push(currentId);
    api.setSelection(ids);
  }
  function openZoneSelect() {
    if (!currentId) return;
    var row = document.querySelector('#table-view .dl-deck-row[data-entry-id="' + String(currentId).replace(/"/g, '\\"') + '"]');
    if (!row) return;
    var select = row.querySelector("select.dl-inline-zone-select") || row.querySelector("select");
    if (!select || !select.focus) return;
    select.focus();
    if (typeof select.showPicker === "function") {
      try { select.showPicker(); } catch (_) {}
    }
  }
  function undo() {
    if (window.DeckLabHistory && window.DeckLabHistory.undo) window.DeckLabHistory.undo();
  }
  function redo() {
    if (window.DeckLabHistory && window.DeckLabHistory.redo) window.DeckLabHistory.redo();
  }

  document.addEventListener("deck-lab:focus-entry", function (event) {
    if (event.detail && event.detail.entryId) currentId = event.detail.entryId;
  });
  document.addEventListener("deck-lab:entry-hover", function (event) {
    if (event.detail && event.detail.entryId) currentId = event.detail.entryId;
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || !event.shiftKey) return;
    var search = document.querySelector("[data-card-search]");
    if (!search || document.activeElement !== search) return;
    var first = document.querySelector("[data-card-results] [role=option]");
    if (!first) return;
    event.preventDefault();
    event.stopPropagation();
    if (first.click) first.click();
  }, true);

  document.addEventListener("keydown", function (event) {
    var key = event.key;
    var command = event.metaKey || event.ctrlKey;
    if (command && !event.altKey && (key === "z" || key === "Z")) {
      if (inField(event)) return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
      return;
    }
    if (event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey && (key === "y" || key === "Y")) {
      if (inField(event)) return;
      event.preventDefault();
      redo();
      return;
    }
    if (command || event.altKey) return;
    if (key === "Enter" && event.shiftKey) return;
    if (inField(event)) return;

    if (key === "/") {
      var search = document.querySelector("[data-card-search]");
      if (!search) return;
      event.preventDefault();
      search.focus();
      return;
    }
    if (key === "?" ) {
      var dialog = document.querySelector("[data-hotkeys-help]");
      if (!dialog || dialog.open) return;
      event.preventDefault();
      if (dialog.showModal) dialog.showModal();
      else dialog.setAttribute("open", "");
      return;
    }
    if (key === "j" || key === "J" || key === "ArrowDown") { event.preventDefault(); move(1); return; }
    if (key === "k" || key === "K" || key === "ArrowUp") { event.preventDefault(); move(-1); return; }
    if (key === "+" || key === "=") {
      var up = currentEntry();
      if (!up) return;
      event.preventDefault();
      api.command([{ type: "adjust_quantity", entry_id: up.id, delta: 1 }]);
      return;
    }
    if (key === "-") {
      var down = currentEntry();
      if (!down) return;
      event.preventDefault();
      api.command([{ type: "adjust_quantity", entry_id: down.id, delta: -1 }]);
      return;
    }
    if (key === "x" || key === "X" || key === " " || key === "Spacebar") {
      if (!currentId) return;
      event.preventDefault();
      toggleSelection();
      return;
    }
    if (key === "m" || key === "M") {
      event.preventDefault();
      openZoneSelect();
      return;
    }
    if (key === "Delete" || key === "Backspace") {
      if (!currentId) return;
      event.preventDefault();
      api.command([{ type: "remove_entry", entry_id: currentId }]);
    }
  });
})();
