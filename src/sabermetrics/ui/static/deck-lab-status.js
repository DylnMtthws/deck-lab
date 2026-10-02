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
    return true;
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

  function plural(n, singular) {
    return n === 1 ? singular : singular + "s";
  }

  // Track the live popover so it survives re-render. It is mounted on
  // document.body (position:fixed) so the bar's overflow cannot clip it.
  var _livePopover = null;
  var _liveTrigger = null;
  var _repositionBound = false;

  function nodeContains(node, target) {
    if (!node || !target) return false;
    if (typeof node.contains === "function") return node.contains(target);
    var current = target;
    while (current) {
      if (current === node) return true;
      current = current.parentNode;
    }
    return false;
  }

  function readSpace2() {
    var fallback = 8;
    try {
      if (typeof getComputedStyle !== "function") return fallback;
      var raw = getComputedStyle(document.documentElement).getPropertyValue("--s-2");
      var n = parseFloat(raw);
      return isFinite(n) ? n : fallback;
    } catch (_e) {
      return fallback;
    }
  }

  function popoverHost() {
    return document.body || document.documentElement;
  }

  function mountPopover(popover) {
    var host = popoverHost();
    if (host && popover.parentNode !== host) host.appendChild(popover);
  }

  function anchorIssuesPopover(popover, trigger) {
    if (!popover || !trigger || typeof trigger.getBoundingClientRect !== "function") return;
    var gap = readSpace2();
    var toggle = trigger.getBoundingClientRect();
    var bar = typeof trigger.closest === "function" ? trigger.closest("[data-status-bar]") : null;
    var barTop = toggle.top;
    if (bar && typeof bar.getBoundingClientRect === "function") {
      barTop = bar.getBoundingClientRect().top;
    }
    var measured = typeof popover.getBoundingClientRect === "function" ? popover.getBoundingClientRect() : null;
    var width = popover.offsetWidth || (measured && measured.width) || 280;
    if (!width) width = 280;
    var viewW = window.innerWidth || (document.documentElement && document.documentElement.clientWidth) || 0;
    var viewH = window.innerHeight || (document.documentElement && document.documentElement.clientHeight) || 0;
    var left = toggle.left;
    if (viewW) {
      var maxLeft = viewW - gap - width;
      if (maxLeft < gap) maxLeft = gap;
      if (left > maxLeft) left = maxLeft;
      if (left < gap) left = gap;
    }
    popover.style.position = "fixed";
    popover.style.zIndex = "75";
    popover.style.left = Math.round(left) + "px";
    popover.style.right = "auto";
    popover.style.top = "auto";
    if (viewH) popover.style.bottom = Math.round(viewH - barTop + gap) + "px";
  }

  function repositionLivePopover() {
    if (!_livePopover || !_livePopover.parentNode || !_liveTrigger || !_liveTrigger.isConnected) return;
    anchorIssuesPopover(_livePopover, _liveTrigger);
  }

  function bindReposition(bar) {
    if (!_repositionBound) {
      _repositionBound = true;
      if (window.addEventListener) {
        window.addEventListener("resize", repositionLivePopover);
        window.addEventListener("scroll", repositionLivePopover, true);
      }
      if (document.addEventListener) {
        document.addEventListener("scroll", repositionLivePopover, true);
      }
    }
    if (bar && !bar._dlIssuesScrollBound && bar.addEventListener) {
      bar._dlIssuesScrollBound = true;
      bar.addEventListener("scroll", repositionLivePopover);
    }
  }

  function renderStatusBar(state) {
    var bar = document.querySelector("[data-status-bar]");
    if (!bar) return;
    bindReposition(bar);
    var summary = summarize(state);

    // Save popover reference before wiping children
    var activePopover = _livePopover && _livePopover.parentNode ? _livePopover : null;

    bar.replaceChildren();

    // --- Count + progress track ---
    var count = document.createElement("span");
    count.className = "dl-status-count" + (summary.total !== summary.target ? " is-invalid" : "");
    var countNum = document.createTextNode(String(summary.total));
    count.appendChild(countNum);
    var sep = document.createElement("span");
    sep.className = "dl-status-sep";
    sep.textContent = "/" + summary.target;
    count.appendChild(sep);
    bar.appendChild(count);

    var progressWrap = document.createElement("span");
    progressWrap.className = "dl-status-progress";
    var track = document.createElement("span");
    track.className = "dl-status-progress-track";
    var fill = document.createElement("span");
    fill.className = "dl-status-progress-fill";
    fill.style.display = "block";
    var pct = Math.min(100, Math.round((summary.total / summary.target) * 100));
    fill.style.width = pct + "%";
    if (summary.total < summary.target) {
      fill.classList.add("is-warning");
    } else if (summary.total === summary.target) {
      fill.classList.add("is-success");
    } else {
      fill.classList.add("is-brand");
    }
    track.appendChild(fill);
    progressWrap.appendChild(track);
    bar.appendChild(progressWrap);

    // --- Legality / Issues ---
    var legality = document.createElement("span");
    legality.setAttribute("data-status-legality", "");
    if (summary.legal) {
      legality.className = "dl-chip is-ok";
      var dot = document.createElement("span");
      dot.className = "dl-dot";
      legality.appendChild(dot);
      legality.appendChild(document.createTextNode("Legal"));
    } else {
      legality.className = "dl-chip is-warn";
      var dot2 = document.createElement("span");
      dot2.className = "dl-dot";
      legality.appendChild(dot2);
      var issueBtn = document.createElement("button");
      issueBtn.type = "button";
      issueBtn.textContent = summary.issueCount === 1 ? "1 issue" : summary.issueCount + " issues";
      issueBtn.className = "dl-status-issues-toggle";
      issueBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        if (_livePopover && _livePopover.parentNode) {
          destroyPopover(_livePopover);
          _livePopover = null;
          return;
        }
        _livePopover = buildIssuesPopover(state, issueBtn);
        _liveTrigger = issueBtn;
      });
      legality.appendChild(issueBtn);
    }
    bar.appendChild(legality);

    // --- Divider ---
    var div = document.createElement("span");
    div.className = "dl-status-divider";
    bar.appendChild(div);

    // --- Type pills ---
    for (var ti = 0; ti < summary.types.length; ti += 1) {
      var t = summary.types[ti];
      var typeSpan = document.createElement("span");
      typeSpan.setAttribute("data-status-type", t.name);
      typeSpan.className = "dl-status-type";
      var labelSpan = document.createElement("span");
      labelSpan.className = "dl-status-type-label";
      labelSpan.textContent = t.name;
      typeSpan.appendChild(labelSpan);
      var numSpan = document.createElement("span");
      numSpan.className = "dl-status-type-count";
      numSpan.textContent = String(t.count);
      typeSpan.appendChild(numSpan);
      bar.appendChild(typeSpan);
    }

    // --- Considering chip (right side) ---
    var consideringQty = totalPrivateQuantity(state);
    if (consideringQty > 0) {
      var chip = document.createElement("span");
      chip.className = "dl-chip dl-status-considering";
      chip.setAttribute("data-status-considering", "");
      chip.textContent = "+" + consideringQty + " considering";
      chip.title = "Not counted toward 100 or included in exports";
      bar.appendChild(chip);
    }

    // --- Verdict chip ---
    var verdictChip = document.createElement("span");
    verdictChip.className = "dl-chip dl-status-verdict";
    verdictChip.setAttribute("data-status-verdict", "");
    var verdictText = getVerdict(state);
    verdictChip.textContent = "Verdict: " + verdictText;
    verdictChip.addEventListener("click", function () {
      var toolsTab = document.querySelector("[data-rail-tab=tools]");
      if (toolsTab) toolsTab.click();
    });
    bar.appendChild(verdictChip);

    // Keep an open popover outside the bar and re-anchor it to the new toggle.
    if (activePopover) {
      var nextBtn = legality.querySelector("button");
      if (!nextBtn) {
        destroyPopover(activePopover);
      } else {
        _liveTrigger = nextBtn;
        _livePopover = activePopover;
        mountPopover(activePopover);
        anchorIssuesPopover(activePopover, nextBtn);
      }
    }
  }

  function totalPrivateQuantity(state) {
    var PRIVATE_ZONE_NAMES = { sideboard: 1, notes: 1, note: 1, maybeboard: 1, maybe: 1, draft: 1, considering: 1 };
    function isPrivateZoneName(name) {
      return Object.prototype.hasOwnProperty.call(PRIVATE_ZONE_NAMES, String(name || "Unsorted").trim().toLowerCase());
    }
    function zoneName(state, zoneId) {
      var zones = (state && state.zones) || [];
      for (var i = 0; i < zones.length; i += 1) {
        if (zones[i].id === zoneId) return zones[i].name;
      }
      return "Unsorted";
    }
    var total = 0;
    (state && state.entries || []).forEach(function (entry) {
      if (entry.is_commander) return;
      var name = zoneName(state, entry.zone_id);
      if (!isPrivateZoneName(name)) return;
      total += Number(entry.quantity || 0);
    });
    return total;
  }

  function getVerdict(state) {
    var feedback = state.feedback || {};
    if (feedback.verdict === "good") return "Good";
    if (feedback.verdict === "mixed") return "Mixed";
    if (feedback.verdict === "bad") return "Bad";
    return "not set";
  }

  function buildIssuesPopover(state, triggerBtn) {
    var popover = document.createElement("div");
    popover.className = "dl-popover dl-status-issues-popover";
    popover.setAttribute("data-status-issues", "");
    popover.style.position = "fixed";
    popover.style.zIndex = "75";
    var list = document.createElement("ul");
    var validation = state.validation || {};
    var issues = validation.issues || [];
    var entryIssues = validation.entry_issues || {};
    var entries = state.entries || [];

    for (var ii = 0; ii < issues.length; ii += 1) {
      var item = document.createElement("li");
      var kindChip = document.createElement("span");
      kindChip.className = "dl-chip is-warn";
      kindChip.textContent = "Deck";
      item.appendChild(kindChip);
      var textSpan = document.createElement("span");
      textSpan.textContent = issues[ii];
      item.appendChild(textSpan);
      list.appendChild(item);
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
          var entryKindChip = document.createElement("span");
          entryKindChip.className = "dl-chip is-warn";
          entryKindChip.textContent = "Card";
          entryItem.appendChild(entryKindChip);
          var entryBtn = document.createElement("button");
          entryBtn.type = "button";
          entryBtn.textContent = (entryObj ? entryObj.name : "Unknown") + ": " + (eIssues[ei].message || eIssues[ei].code || "");
          entryBtn.addEventListener("click", function (fid) {
            return function () {
              if (window.DeckLabBuilder && window.DeckLabBuilder.focusEntry) window.DeckLabBuilder.focusEntry(fid);
              destroyPopover(popover);
            };
          }(eid));
          entryItem.appendChild(entryBtn);
          list.appendChild(entryItem);
        }
      }
    }
    popover.appendChild(list);
    mountPopover(popover);
    anchorIssuesPopover(popover, triggerBtn);

    function closeHandler(e) {
      if (e.key === "Escape") {
        destroyPopover(popover);
      }
    }
    function outsideClickHandler(e) {
      var trigger = _liveTrigger || triggerBtn;
      if (!nodeContains(popover, e.target) && e.target !== trigger && !nodeContains(trigger, e.target)) {
        destroyPopover(popover);
      }
    }
    popover._closeHandlers = { closeHandler: closeHandler, outsideClickHandler: outsideClickHandler };
    document.addEventListener("keydown", closeHandler);
    setTimeout(function () {
      document.addEventListener("click", outsideClickHandler);
    }, 0);

    return popover;
  }

  function destroyPopover(popover) {
    if (!popover || !popover.parentNode) return;
    if (popover === _livePopover) {
      _livePopover = null;
      _liveTrigger = null;
    }
    var handlers = popover._closeHandlers;
    if (handlers) {
      if (typeof document.removeEventListener === "function") {
        document.removeEventListener("keydown", handlers.closeHandler);
        document.removeEventListener("click", handlers.outsideClickHandler);
      }
    }
    try {
      popover.parentNode.removeChild(popover);
    } catch (_e) {
      try { popover.remove(); } catch (_e2) { /* noop */ }
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
