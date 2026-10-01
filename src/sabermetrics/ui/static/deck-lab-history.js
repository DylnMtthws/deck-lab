(function () {
  "use strict";

  var UNDOABLE = {
    add_card: 1, remove_entry: 1, set_quantity: 1, adjust_quantity: 1,
    move_entry: 1, set_role: 1, rename_zone: 1, create_zone: 1, delete_zone: 1
  };
  var NON_UNDOABLE = {
    rename_deck: "rename deck",
    toggle_favorite: "toggle favorite",
    add_tag: "add tag",
    remove_tag: "remove tag",
    set_zone_layout: "change zone layout",
    set_commanders: "change commanders",
    update_view: "change view",
    update_presentation: "change playmat",
    move_zone: "move zone"
  };
  var CONFLICT = "A newer version was loaded. Reapply your last change.";
  var LIMIT = 50;

  function clone(value) {
    return JSON.parse(JSON.stringify(value == null ? null : value));
  }
  function entryById(state, id) {
    return (state.entries || []).find(function (entry) { return entry.id === id; }) || null;
  }
  function zoneById(state, id) {
    return (state.zones || []).find(function (zone) { return zone.id === id; }) || null;
  }
  function nonUndoableLabel(command) {
    var type = command && command.type;
    if (NON_UNDOABLE[type]) return NON_UNDOABLE[type];
    if (!type) return "that change";
    return String(type).replace(/_/g, " ");
  }
  function addCommand(entry) {
    var command = {
      type: "add_card",
      card_id: entry.card_id,
      quantity: Number(entry.quantity || 1)
    };
    if (entry.is_commander) command.is_commander = true;
    else command.zone_id = entry.zone_id;
    if (entry.role) command.role = entry.role;
    return command;
  }
  function findStackTarget(state, command) {
    var cardId = String(command.card_id || "");
    var commander = !!command.is_commander;
    var zoneId = commander ? "" : String(command.zone_id || "");
    return (state.entries || []).find(function (entry) {
      if (!!entry.is_commander !== commander) return false;
      if (String(entry.card_id || "") !== cardId && String(entry.oracle_id || "") !== cardId) return false;
      if (commander) return true;
      return String(entry.zone_id || "") === zoneId;
    }) || null;
  }
  function findCreated(before, after, command) {
    if (!after) return null;
    var known = {};
    (before.entries || []).forEach(function (entry) { known[entry.id] = true; });
    var commander = !!command.is_commander;
    var zoneId = commander ? "" : String(command.zone_id || "");
    var matches = (after.entries || []).filter(function (entry) {
      if (known[entry.id]) return false;
      if (String(entry.card_id || "") !== String(command.card_id || "")) return false;
      if (!!entry.is_commander !== commander) return false;
      if (commander) return true;
      return String(entry.zone_id || "") === zoneId;
    });
    return matches[0] || null;
  }
  function findCreatedZone(before, after, command) {
    if (!after) return null;
    var known = {};
    (before.zones || []).forEach(function (zone) { known[zone.id] = true; });
    var created = (after.zones || []).filter(function (zone) { return !known[zone.id]; });
    if (command && command.name) {
      var named = created.find(function (zone) { return zone.name === command.name; });
      if (named) return named;
    }
    return created.length === 1 ? created[0] : null;
  }

  function inverseOf(before, command, after) {
    var type = command.type;
    if (type === "remove_entry") {
      var removed = entryById(before, command.entry_id);
      if (!removed || !removed.card_id) return null;
      return { inverse: [addCommand(removed)], label: "remove " + removed.name };
    }
    if (type === "add_card") {
      var existing = findStackTarget(before, command);
      if (existing) {
        return {
          inverse: [{ type: "set_quantity", entry_id: existing.id, quantity: Number(existing.quantity) }],
          label: "add " + existing.name
        };
      }
      var created = findCreated(before, after, command);
      if (!created) return null;
      return { inverse: [{ type: "remove_entry", entry_id: created.id }], label: "add " + created.name };
    }
    if (type === "set_quantity") {
      var current = entryById(before, command.entry_id);
      if (!current) return null;
      var quantity = Number(command.quantity);
      if (quantity <= 0) {
        if (!current.card_id) return null;
        return { inverse: [addCommand(current)], label: "remove " + current.name };
      }
      return {
        inverse: [{ type: "set_quantity", entry_id: current.id, quantity: Number(current.quantity) }],
        label: "change " + current.name + " quantity"
      };
    }
    if (type === "adjust_quantity") {
      var adjusted = entryById(before, command.entry_id);
      if (!adjusted) return null;
      var delta = Number(command.delta);
      if (delta !== 1 && delta !== -1) return null;
      var next = Number(adjusted.quantity) + delta;
      if (next <= 0) {
        if (!adjusted.card_id) return null;
        return { inverse: [addCommand(adjusted)], label: "remove " + adjusted.name };
      }
      if (next > 99) return null;
      return {
        inverse: [{ type: "set_quantity", entry_id: adjusted.id, quantity: Number(adjusted.quantity) }],
        label: "change " + adjusted.name + " quantity"
      };
    }
    if (type === "move_entry") {
      var moved = entryById(before, command.entry_id);
      if (!moved || moved.is_commander) return null;
      return {
        inverse: [{ type: "move_entry", entry_id: moved.id, zone_id: moved.zone_id, sort_order: Number(moved.sort_order || 0) }],
        label: "move " + moved.name
      };
    }
    if (type === "set_role") {
      var roleEntry = entryById(before, command.entry_id);
      if (!roleEntry || roleEntry.is_commander) return null;
      return {
        inverse: [{ type: "set_role", entry_id: roleEntry.id, role: roleEntry.role || "" }],
        label: "set " + roleEntry.name + " role"
      };
    }
    if (type === "rename_zone") {
      var zone = zoneById(before, command.zone_id);
      if (!zone) return null;
      return {
        inverse: [{ type: "rename_zone", zone_id: zone.id, name: zone.name }],
        label: "rename " + zone.name
      };
    }
    if (type === "create_zone") {
      var zoneId = command.zone_id;
      var zoneName = String(command.name || "New zone");
      if (!zoneId) {
        var createdZone = findCreatedZone(before, after, command);
        if (!createdZone) return null;
        zoneId = createdZone.id;
        zoneName = createdZone.name || zoneName;
      }
      return { inverse: [{ type: "delete_zone", zone_id: zoneId }], label: "create " + zoneName };
    }
    if (type === "delete_zone") {
      var deleted = zoneById(before, command.zone_id);
      if (!deleted || String(deleted.name || "").toLowerCase() === "unsorted") return null;
      var inverse = [{ type: "create_zone", zone_id: deleted.id, name: deleted.name }];
      if (deleted.x != null) inverse[0].x = deleted.x;
      if (deleted.y != null) inverse[0].y = deleted.y;
      (before.entries || []).forEach(function (entry) {
        if (entry.is_commander || String(entry.zone_id || "") !== String(deleted.id)) return;
        inverse.push({
          type: "move_entry",
          entry_id: entry.id,
          zone_id: deleted.id,
          sort_order: Number(entry.sort_order || 0)
        });
      });
      return { inverse: inverse, label: "delete " + deleted.name };
    }
    return null;
  }

  function planInverse(before, commands, after) {
    var list = commands || [];
    if (!list.length) return { undoable: false, inverse: [], label: "that change" };
    var inverse = [];
    var label = "";
    for (var i = 0; i < list.length; i++) {
      var command = list[i] || {};
      if (!UNDOABLE[command.type]) return { undoable: false, inverse: [], label: nonUndoableLabel(command) };
      var planned = inverseOf(before, command, after);
      if (!planned) return { undoable: false, inverse: [], label: nonUndoableLabel(command) };
      if (!label) label = planned.label;
      inverse = planned.inverse.concat(inverse);
    }
    return { undoable: true, inverse: inverse, label: label };
  }

  window.DeckLabHistory = { planInverse: planInverse };

  function install() {
    var api = window.DeckLabBuilder;
    if (!api || api.shared || typeof api.command !== "function" || typeof api.getState !== "function") return;

    var undoStack = [];
    var redoStack = [];
    var pending = [];
    var applying = false;
    var baseline = clone(api.getState());
    var original = api.command.bind(api);

    function snapshot() { return clone(api.getState()); }
    function signature(state) {
      return JSON.stringify({
        entries: (state.entries || []).map(function (entry) {
          return [entry.id, entry.card_id, entry.name, Number(entry.quantity || 0), entry.zone_id || "", entry.role || "", !!entry.is_commander, Number(entry.sort_order || 0)];
        }),
        zones: (state.zones || []).map(function (zone) {
          return [zone.id, zone.name];
        }),
        title: state.title || "",
        tags: state.tags || [],
        preferences: state.preferences || {},
        presentation: state.presentation || {}
      });
    }
    function sawConflict() {
      var save = document.getElementById("save-state");
      return !!(save && save.title === CONFLICT);
    }
    function sayCannot(label) {
      var save = document.getElementById("save-state");
      if (save) save.title = "Can't undo " + label;
    }
    function refresh() {
      var undoBtn = document.querySelector("[data-undo]");
      var redoBtn = document.querySelector("[data-redo]");
      var undoTip = undoStack.length ? "Undo: " + undoStack[undoStack.length - 1].label : "Undo";
      var redoTip = redoStack.length ? "Redo: " + redoStack[redoStack.length - 1].label : "Redo";
      if (undoBtn) {
        undoBtn.disabled = !undoStack.length;
        if (undoStack.length) undoBtn.removeAttribute("disabled");
        else undoBtn.setAttribute("disabled", "");
        undoBtn.setAttribute("data-dl-tip", undoTip);
        undoBtn.setAttribute("aria-label", undoTip);
      }
      if (redoBtn) {
        redoBtn.disabled = !redoStack.length;
        if (redoStack.length) redoBtn.removeAttribute("disabled");
        else redoBtn.setAttribute("disabled", "");
        redoBtn.setAttribute("data-dl-tip", redoTip);
        redoBtn.setAttribute("aria-label", redoTip);
      }
    }
    function clearStacks() {
      undoStack = [];
      redoStack = [];
      refresh();
    }
    function push(entry) {
      undoStack.push(entry);
      while (undoStack.length > LIMIT) undoStack.shift();
      redoStack = [];
      refresh();
    }
    function indexBy(items) {
      var map = {};
      (items || []).forEach(function (item) { map[item.id] = item; });
      return map;
    }
    function otherLabel(before, after) {
      if ((before.title || "") !== (after.title || "")) return "rename deck";
      if (JSON.stringify(before.tags || []) !== JSON.stringify(after.tags || [])) return "change tags";
      if (JSON.stringify(before.preferences || {}) !== JSON.stringify(after.preferences || {})) return "change view";
      if (JSON.stringify(before.presentation || {}) !== JSON.stringify(after.presentation || {})) return "change playmat";
      return "that change";
    }
    function planFromDiff(before, after) {
      if (signature(before) === signature(after)) return null;
      var beforeZones = indexBy(before.zones);
      var afterZones = indexBy(after.zones);
      var removedZones = [];
      var addedZones = [];
      Object.keys(beforeZones).forEach(function (id) { if (!afterZones[id]) removedZones.push(beforeZones[id]); });
      Object.keys(afterZones).forEach(function (id) { if (!beforeZones[id]) addedZones.push(afterZones[id]); });
      if (removedZones.some(function (zone) { return String(zone.name || "").toLowerCase() === "unsorted"; })) {
        return { undoable: false, label: "delete zone" };
      }
      var forward = [];
      var inverse = [];
      var label = "";
      function note(text) { if (!label) label = text; }
      Object.keys(beforeZones).forEach(function (id) {
        if (!afterZones[id] || beforeZones[id].name === afterZones[id].name) return;
        forward.push({ type: "rename_zone", zone_id: id, name: afterZones[id].name });
        inverse.unshift({ type: "rename_zone", zone_id: id, name: beforeZones[id].name });
        note("rename " + beforeZones[id].name);
      });
      removedZones.forEach(function (zone) {
        var restore = [{ type: "create_zone", zone_id: zone.id, name: zone.name }];
        if (zone.x != null) restore[0].x = zone.x;
        if (zone.y != null) restore[0].y = zone.y;
        (before.entries || []).forEach(function (entry) {
          if (entry.is_commander || String(entry.zone_id || "") !== String(zone.id)) return;
          restore.push({ type: "move_entry", entry_id: entry.id, zone_id: zone.id, sort_order: Number(entry.sort_order || 0) });
        });
        forward.push({ type: "delete_zone", zone_id: zone.id });
        inverse = restore.concat(inverse);
        note("delete " + zone.name);
      });
      addedZones.forEach(function (zone) {
        var create = { type: "create_zone", zone_id: zone.id, name: zone.name };
        if (zone.x != null) create.x = zone.x;
        if (zone.y != null) create.y = zone.y;
        forward.push(create);
        inverse.unshift({ type: "delete_zone", zone_id: zone.id });
        note("create " + zone.name);
      });
      var gone = {};
      removedZones.forEach(function (zone) { gone[zone.id] = true; });
      var beforeEntries = indexBy(before.entries);
      var afterEntries = indexBy(after.entries);
      Object.keys(beforeEntries).forEach(function (id) {
        if (afterEntries[id]) return;
        if (!beforeEntries[id].card_id) return;
        forward.push({ type: "remove_entry", entry_id: id });
        inverse.unshift(addCommand(beforeEntries[id]));
        note("remove " + beforeEntries[id].name);
      });
      var missingCard = false;
      Object.keys(afterEntries).forEach(function (id) {
        if (beforeEntries[id]) return;
        if (!afterEntries[id].card_id) { missingCard = true; return; }
        forward.push(addCommand(afterEntries[id]));
        inverse.unshift({ type: "remove_entry", entry_id: id });
        note("add " + afterEntries[id].name);
      });
      if (missingCard) return { undoable: false, label: "add card" };
      Object.keys(beforeEntries).forEach(function (id) {
        var prev = beforeEntries[id];
        var next = afterEntries[id];
        if (!next) return;
        if (Number(prev.quantity) !== Number(next.quantity)) {
          forward.push({ type: "set_quantity", entry_id: id, quantity: Number(next.quantity) });
          inverse.unshift({ type: "set_quantity", entry_id: id, quantity: Number(prev.quantity) });
          note("change " + prev.name + " quantity");
        }
        var zoneChanged = String(prev.zone_id || "") !== String(next.zone_id || "");
        var sortChanged = Number(prev.sort_order || 0) !== Number(next.sort_order || 0);
        if ((zoneChanged || sortChanged) && !prev.is_commander && !gone[prev.zone_id]) {
          forward.push({ type: "move_entry", entry_id: id, zone_id: next.zone_id, sort_order: Number(next.sort_order || 0) });
          inverse.unshift({ type: "move_entry", entry_id: id, zone_id: prev.zone_id, sort_order: Number(prev.sort_order || 0) });
          note("move " + prev.name);
        }
        if (String(prev.role || "") !== String(next.role || "") && !prev.is_commander) {
          forward.push({ type: "set_role", entry_id: id, role: next.role || "" });
          inverse.unshift({ type: "set_role", entry_id: id, role: prev.role || "" });
          note("set " + prev.name + " role");
        }
      });
      if (!forward.length) return { undoable: false, label: otherLabel(before, after) };
      return { undoable: true, forward: forward, inverse: inverse, label: label || "edit deck" };
    }
    function remember(before, commands, after, synthesized) {
      var planned = synthesized || planInverse(before, commands, after);
      if (!planned.undoable) {
        redoStack = [];
        sayCannot(planned.label || "that change");
        refresh();
        return;
      }
      push({
        forward: clone(synthesized ? planned.forward : commands),
        inverse: clone(planned.inverse),
        label: planned.label
      });
    }
    function settle(ticket) {
      pending = pending.filter(function (item) { return item !== ticket; });
      if (!ticket.ok && sawConflict()) {
        clearStacks();
        baseline = snapshot();
        return;
      }
      if (!ticket.ok) return;
      remember(baseline, ticket.commands, ticket.after, null);
      baseline = snapshot();
    }
    function observe() {
      if (applying || pending.length) return;
      var after = snapshot();
      if (signature(baseline) === signature(after)) return;
      var planned = planFromDiff(baseline, after);
      if (planned) remember(baseline, planned.forward || [], after, planned);
      baseline = after;
    }
    function runHistory(commands, fromUndo) {
      if (applying) return Promise.resolve(false);
      var entry = fromUndo ? undoStack.pop() : redoStack.pop();
      if (!entry) { refresh(); return Promise.resolve(false); }
      applying = true;
      refresh();
      return original(commands).then(function (ok) {
        if (!ok && sawConflict()) clearStacks();
        else if (ok) {
          if (fromUndo) redoStack.push(entry);
          else undoStack.push(entry);
        } else if (fromUndo) undoStack.push(entry);
        else redoStack.push(entry);
        baseline = snapshot();
        applying = false;
        refresh();
        return ok;
      }, function (error) {
        if (fromUndo) undoStack.push(entry);
        else redoStack.push(entry);
        baseline = snapshot();
        applying = false;
        refresh();
        throw error;
      });
    }
    function undo() {
      if (!undoStack.length) return Promise.resolve(false);
      return runHistory(undoStack[undoStack.length - 1].inverse, true);
    }
    function redo() {
      if (!redoStack.length) return Promise.resolve(false);
      return runHistory(redoStack[redoStack.length - 1].forward, false);
    }

    api.command = function (commands) {
      if (applying) return original(commands);
      var ticket = { commands: clone(commands || []), ok: false, after: null };
      pending.push(ticket);
      return original(commands).then(function (ok) {
        ticket.ok = !!ok;
        ticket.after = snapshot();
        settle(ticket);
        return ok;
      }, function (error) {
        pending = pending.filter(function (item) { return item !== ticket; });
        throw error;
      });
    };
    if (typeof api.onRender === "function") api.onRender(function () { observe(); });

    var undoBtn = document.querySelector("[data-undo]");
    var redoBtn = document.querySelector("[data-redo]");
    if (undoBtn) undoBtn.addEventListener("click", function () { undo(); });
    if (redoBtn) redoBtn.addEventListener("click", function () { redo(); });
    refresh();

    window.DeckLabHistory.undo = undo;
    window.DeckLabHistory.redo = redo;
    window.DeckLabHistory.stats = function () {
      return {
        undo: undoStack.length,
        redo: redoStack.length,
        oldest: undoStack.length ? clone(undoStack[0].forward) : null,
        newest: undoStack.length ? clone(undoStack[undoStack.length - 1].forward) : null
      };
    };
  }

  install();
})();
