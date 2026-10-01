(function () {
  "use strict";

  var TYPE_BUCKET_ORDER = ["Land", "Creature", "Planeswalker", "Battle", "Instant", "Sorcery", "Artifact", "Enchantment"];

  function typeBucket(typeLine) {
    var beforeDash = String(typeLine || "").split(/[—-]/)[0].trim().toLowerCase();
    for (var i = 0; i < TYPE_BUCKET_ORDER.length; i += 1) {
      if (beforeDash.indexOf(TYPE_BUCKET_ORDER[i].toLowerCase()) >= 0) {
        return TYPE_BUCKET_ORDER[i];
      }
    }
    return "Other";
  }

  function isLibraryZone(zoneId, zones) {
    for (var i = 0; i < zones.length; i += 1) {
      if (zones[i].id === zoneId) {
        var name = String(zones[i].name || "Unsorted").trim().toLowerCase();
        if (name === "sideboard" || name === "notes" || name === "note" || name === "maybeboard" || name === "maybe" || name === "draft" || name === "considering") return false;
        return true;
      }
    }
    return true;  // unknown zone -> treat as library
  }

  function summarize(state) {
    var validation = state.validation || {};
    var entries = state.entries || [];
    var zones = state.zones || [];
    var total = validation.total_count != null ? Number(validation.total_count) : 0;
    var target = 100;
    var legal = !!validation.legal;

    var deckIssueCount = (validation.issues || []).length;
    var entryIssueKeys = validation.entry_issues ? Object.keys(validation.entry_issues) : [];
    var issueCount = deckIssueCount + entryIssueKeys.length;

    var typeCounts = {};
    var roleCounts = {};
    for (var i = 0; i < entries.length; i += 1) {
      var entry = entries[i];
      if (entry.is_commander) continue;
      if (!isLibraryZone(entry.zone_id, zones)) continue;
      var q = Number(entry.quantity || 0);
      var bucket = typeBucket(entry.type_line || "");
      typeCounts[bucket] = (typeCounts[bucket] || 0) + q;
      var role = String(entry.role || "").trim() || "none";
      roleCounts[role] = (roleCounts[role] || 0) + q;
    }

    var types = [];
    for (var ti = 0; ti < TYPE_BUCKET_ORDER.length; ti += 1) {
      var name = TYPE_BUCKET_ORDER[ti];
      if (typeCounts[name]) types.push({ name: name, count: typeCounts[name] });
    }
    if (typeCounts["Other"]) types.push({ name: "Other", count: typeCounts["Other"] });

    var roleList = [];
    for (var r in roleCounts) {
      if (Object.prototype.hasOwnProperty.call(roleCounts, r)) roleList.push({ role: r, count: roleCounts[r] });
    }
    roleList.sort(function (a, b) {
      if (a.count !== b.count) return b.count - a.count;
      if (a.role < b.role) return -1;
      if (a.role > b.role) return 1;
      return 0;
    });

    return { total: total, target: target, legal: legal, issueCount: issueCount, types: types, roles: roleList };
  }

  function renderStatusBar(state) {
    var bar = document.querySelector("[data-status-bar]");
    if (!bar) return;
    var summary = summarize(state);
    bar.replaceChildren();

    var count = document.createElement("span");
    count.className = "dl-status-count" + (summary.total !== summary.target ? " is-invalid" : "");
    count.textContent = summary.total + "/" + summary.target;
    bar.appendChild(count);

    var legality = document.createElement("span");
    legality.setAttribute("data-status-legality", "");
    if (summary.legal) {
      legality.className = "is-legal";
      legality.textContent = "Legal";
    } else {
      var issueBtn = document.createElement("button");
      issueBtn.type = "button";
      issueBtn.textContent = summary.issueCount === 1 ? "1 issue" : summary.issueCount + " issues";
      issueBtn.addEventListener("click", function () {
        var list = bar.querySelector("[data-status-issues]");
        if (list) {
          list.hidden = !list.hidden;
          return;
        }
        var issuesList = document.createElement("ul");
        issuesList.setAttribute("data-status-issues", "");
        issuesList.className = "dl-status-issues";
        var validation = state.validation || {};
        var issues = validation.issues || [];
        var entryIssues = validation.entry_issues || {};
        var entries = state.entries || [];
        for (var ii = 0; ii < issues.length; ii += 1) {
          var item = document.createElement("li");
          item.textContent = issues[ii];
          issuesList.appendChild(item);
        }
        for (var eid in entryIssues) {
          if (Object.prototype.hasOwnProperty.call(entryIssues, eid)) {
            var eIssues = entryIssues[eid] || [];
            for (var ei = 0; ei < eIssues.length; ei += 1) {
              var entryObj = null;
              for (var ej = 0; ej < entries.length; ej += 1) {
                if (entries[ej].id === eid) { entryObj = entries[ej]; break; }
              }
              var entryItem = document.createElement("li");
              var entryBtn = document.createElement("button");
              entryBtn.type = "button";
              entryBtn.textContent = (entryObj ? entryObj.name : "Unknown") + ": " + (eIssues[ei].message || eIssues[ei].code || "");
              entryBtn.addEventListener("click", function (fid) {
                return function () { if (window.DeckLabBuilder && window.DeckLabBuilder.focusEntry) window.DeckLabBuilder.focusEntry(fid); };
              }(eid));
              entryItem.appendChild(entryBtn);
              issuesList.appendChild(entryItem);
            }
          }
        }
        bar.appendChild(issuesList);
        issuesList.hidden = false;
      });
      legality.appendChild(issueBtn);
    }
    bar.appendChild(legality);

    for (var ti = 0; ti < summary.types.length; ti += 1) {
      var t = summary.types[ti];
      var typeSpan = document.createElement("span");
      typeSpan.setAttribute("data-status-type", t.name);
      typeSpan.className = "dl-status-type";
      typeSpan.textContent = t.name + " " + t.count;
      bar.appendChild(typeSpan);
    }

    var topRoles = summary.roles.slice(0, 5);
    for (var ri = 0; ri < topRoles.length; ri += 1) {
      var rInfo = topRoles[ri];
      var roleSpan = document.createElement("span");
      roleSpan.setAttribute("data-status-role", rInfo.role);
      roleSpan.className = "dl-status-role";
      var label = rInfo.role === "none" ? "No role" : rInfo.role.replace(/_/g, " ").replace(/\b\w/g, function (l) { return l.toUpperCase(); });
      roleSpan.textContent = label + " " + rInfo.count;
      bar.appendChild(roleSpan);
    }
  }

  window.DeckLabStatus = { typeBucket: typeBucket, summarize: summarize };

  function onStateChange(state) {
    renderStatusBar(state);
  }

  if (window.DeckLabBuilder) {
    window.DeckLabBuilder.onRender(onStateChange);
    var state = window.DeckLabBuilder.getState();
    if (state) renderStatusBar(state);
  } else {
    document.addEventListener("deck-lab:ready", function () {
      if (window.DeckLabBuilder) {
        var state = window.DeckLabBuilder.getState();
        if (state) renderStatusBar(state);
      }
    });
  }
})();
