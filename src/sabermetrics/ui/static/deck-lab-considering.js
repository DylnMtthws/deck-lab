(function () {
  "use strict";

  var PRIVATE_ZONE_NAMES = {
    sideboard: 1, notes: 1, note: 1, maybeboard: 1, maybe: 1, draft: 1, considering: 1
  };

  function apiAvailable() {
    return typeof window.DeckLabBuilder !== "undefined" && window.DeckLabBuilder !== null;
  }

  function zoneName(state, zoneId) {
    var zones = (state && state.zones) || [];
    for (var i = 0; i < zones.length; i += 1) {
      if (zones[i].id === zoneId) return zones[i].name;
    }
    return "Unsorted";
  }

  function isPrivateZoneName(name) {
    return Object.prototype.hasOwnProperty.call(PRIVATE_ZONE_NAMES, String(name || "Unsorted").trim().toLowerCase());
  }

  function privateEntryQuantity(state, entry) {
    if (entry.is_commander) return 0;
    var name = zoneName(state, entry.zone_id);
    if (!isPrivateZoneName(name)) return 0;
    return Number(entry.quantity || 0);
  }

  function totalPrivateQuantity(state) {
    var total = 0;
    (state && state.entries || []).forEach(function (entry) {
      total += privateEntryQuantity(state, entry);
    });
    return total;
  }

  function findZoneIdByName(state, name) {
    var zones = (state && state.zones) || [];
    var lower = String(name).trim().toLowerCase();
    for (var i = 0; i < zones.length; i += 1) {
      if (String(zones[i].name).trim().toLowerCase() === lower) return zones[i].id;
    }
    return null;
  }

  function isAllSelectedInConsidering(api) {
    var ids = api.getSelection();
    var state = api.getState();
    var entries = (state && state.entries) || [];
    var selectedEntries = entries.filter(function (e) { return ids.indexOf(e.id) !== -1; });
    if (!selectedEntries.length) return false;
    return selectedEntries.every(function (e) {
      if (e.is_commander) return true;
      var name = zoneName(state, e.zone_id);
      return isPrivateZoneName(name);
    });
  }

  function getSelectedNonCommanderEntryIds(api) {
    var ids = api.getSelection();
    var state = api.getState();
    var entries = (state && state.entries) || [];
    return ids.filter(function (id) {
      var entry = null;
      for (var i = 0; i < entries.length; i += 1) {
        if (entries[i].id === id) { entry = entries[i]; break; }
      }
      return entry && !entry.is_commander;
    });
  }

  function insertButton(bulkControls) {
    var btn = document.createElement("button");
    btn.className = "dl-button";
    btn.type = "button";
    btn.setAttribute("data-move-considering", "");
    btn.textContent = "Move to Considering";
    btn.disabled = true;
    if (bulkControls.getAttribute && bulkControls.getAttribute("data-considering-slot") != null) {
      bulkControls.appendChild(btn);
      return btn;
    }
    var slot = bulkControls.querySelector("[data-considering-slot]");
    if (slot) {
      slot.appendChild(btn);
      return btn;
    }
    var ref = bulkControls.querySelector("[data-clear-selection]");
    if (ref) {
      bulkControls.insertBefore(btn, ref);
    } else {
      bulkControls.appendChild(btn);
    }
    return btn;
  }

  // Badge removed in D04 — now rendered by deck-lab-status.js in the status bar

  function updateButton(btn, api) {
    var sel = api.getSelection();
    if (!sel || !sel.length) {
      btn.disabled = true;
      return;
    }
    btn.disabled = false;
    if (isAllSelectedInConsidering(api)) {
      btn.textContent = "Move to deck";
    } else {
      btn.textContent = "Move to Considering";
    }
  }

  function getEntrySortOrder(entry) {
    return typeof entry.sort_order === "number" ? entry.sort_order : 0;
  }

  function init() {
    if (!apiAvailable()) {
      document.addEventListener("deck-lab:ready", init);
      return;
    }
    var api = window.DeckLabBuilder;
    if (api.shared) return;

    var bulkControls = document.querySelector("[data-considering-slot]")
      || document.querySelector("[data-selection-bar]")
      || document.querySelector("[data-bulk-controls]");
    if (!bulkControls) return;

    var btn = insertButton(bulkControls);

    function refresh() {
      var state = api.getState();
      updateButton(btn, api);
    }

    btn.addEventListener("click", function () {
      var state = api.getState();
      var allInConsidering = isAllSelectedInConsidering(api);
      var cmds = [];
      var targetZoneName;
      if (allInConsidering) {
        targetZoneName = "Unsorted";
      } else {
        targetZoneName = "Considering";
      }
      var targetZoneId = findZoneIdByName(state, targetZoneName);
      if (!targetZoneId) {
        var newZoneId = "zone-consider-" + Date.now();
        var createZone = { type: "create_zone", zone_id: newZoneId, name: targetZoneName };
        if (typeof api.freeZonePosition === "function") {
          var spot = api.freeZonePosition(360, 240);
          if (spot && spot.x != null && spot.y != null) {
            createZone.x = spot.x;
            createZone.y = spot.y;
          }
        }
        cmds.push(createZone);
        targetZoneId = newZoneId;
      }
      var ids = getSelectedNonCommanderEntryIds(api);
      ids.forEach(function (entryId, index) {
        cmds.push({ type: "move_entry", entry_id: entryId, zone_id: targetZoneId, sort_order: 999 + index });
      });
      if (cmds.length) {
        api.command(cmds);
      }
      api.setSelection([]);
    });

    api.onRender(refresh);
    document.addEventListener("deck-lab:selection", refresh);
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
