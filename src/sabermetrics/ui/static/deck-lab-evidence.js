(function () {
  "use strict";

  var WINDOWS = [
    ["30", "Last 30 days"],
    ["60", "Last 60 days"],
    ["90", "Last 90 days"],
    ["180", "Last 180 days"],
    ["0", "All time"]
  ];
  var windowDays = "30";
  var payload = null;
  var fetchedOracles = null;
  var fetchToken = 0;
  var refetchTimer = null;
  var started = false;
  var panelObserver = null;

  function readState() {
    if (window.DeckLabBuilder && window.DeckLabBuilder.getState) return window.DeckLabBuilder.getState();
    var data = document.getElementById("deck-document-data");
    if (!data) return null;
    try { return JSON.parse(data.textContent); } catch (error) { return null; }
  }

  function oracleKey(state) {
    var ids = [];
    var entries = (state && state.entries) || [];
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].oracle_id) ids.push(String(entries[i].oracle_id));
    }
    ids.sort();
    return ids.join("\n");
  }

  function commanderLabel(state) {
    var names = [];
    var entries = (state && state.entries) || [];
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].is_commander && entries[i].name) names.push(entries[i].name);
    }
    return names.length ? names.join(" + ") : "this commander";
  }

  function findEntry(id) {
    if (!id) return null;
    var entries = ((readState() || {}).entries) || [];
    for (var i = 0; i < entries.length; i += 1) {
      if (String(entries[i].id) === String(id)) return entries[i];
    }
    return null;
  }

  function currentEntryId() {
    if (window.DeckLabCardPanel && window.DeckLabCardPanel.current) return window.DeckLabCardPanel.current();
    return null;
  }

  function formatPriority(value) {
    var number = Number(value);
    if (!isFinite(number)) return "0";
    if (Math.abs(number - Math.round(number)) < 1e-9) return String(Math.round(number));
    return String(number);
  }

  function cohortPhrase(data) {
    var days = Number(data.window_days) || 0;
    var when = days ? "last " + days + " days" : "all time";
    var floor = data.min_event_size == null
      ? "event size not recorded"
      : "events with ≥ " + data.min_event_size + " players";
    return when + ", " + floor;
  }

  function fullStatement(lists, data, commander) {
    return "In " + lists + " of " + data.denominator + " recorded " + commander + " lists, " + cohortPhrase(data);
  }

  function absence(data) {
    var days = data && Number(data.window_days);
    if (days) return "No tournament evidence for this commander in the last " + days + " days";
    return "No tournament evidence for this commander";
  }

  function unavailable(days) {
    return {
      commander_ids: [],
      window_days: Number(days) || 0,
      min_event_size: null,
      denominator: 0,
      available: false,
      cards: {},
      explanations: {}
    };
  }

  function explanationFor(entry) {
    if (!entry || !payload || !payload.explanations) return null;
    return payload.explanations[entry.oracle_id] || null;
  }

  function cardFact(entry) {
    if (!entry || !payload || !payload.available || !payload.cards) return null;
    var fact = payload.cards[entry.oracle_id];
    if (!fact || !fact.lists || !payload.denominator) return null;
    return fact;
  }

  function statementFor(entry) {
    if (!payload) return "Tournament evidence is loading";
    if (!payload.available) return absence(payload);
    var fact = cardFact(entry);
    var commander = commanderLabel(readState());
    if (!fact) {
      return "Not recorded in any of the " + payload.denominator + " recorded " + commander + " lists, " + cohortPhrase(payload);
    }
    return fullStatement(fact.lists, payload, commander);
  }

  function percentText(fact) {
    return Math.round((Number(fact.lists) / Number(payload.denominator)) * 100) + "%";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function updateBasis(section) {
    if (!section) return;
    var basis = section.querySelector("[data-evidence-basis]");
    if (!basis) return;
    basis.textContent = payload
      ? "Based on " + payload.denominator + " recorded lists"
      : "Tournament evidence is loading";
  }

  function ensureControls() {
    if (!window.DeckLabBuilder || !window.DeckLabBuilder.railSection) return;
    var section = window.DeckLabBuilder.railSection("evidence", "Tournament evidence", { tab: "deck" });
    if (!section) return;
    if (!section.querySelector("[data-evidence-window]")) {
      var label = el("label", "dl-evidence-window");
      label.appendChild(el("span", "dl-evidence-window-label", "Window"));
      var select = el("select", "dl-select");
      select.setAttribute("data-evidence-window", "");
      select.setAttribute("aria-label", "Tournament window");
      WINDOWS.forEach(function (item) {
        var option = el("option", "", item[1]);
        option.value = item[0];
        if (item[0] === windowDays) option.selected = true;
        select.appendChild(option);
      });
      select.value = windowDays;
      select.addEventListener("change", function () {
        windowDays = select.value || "30";
        fetchEvidence();
      });
      label.appendChild(select);
      var basis = el("p", "dl-evidence-basis");
      basis.setAttribute("data-evidence-basis", "");
      section.appendChild(label);
      section.appendChild(basis);
    }
    var existing = section.querySelector("[data-evidence-window]");
    if (existing && existing.value !== windowDays) existing.value = windowDays;
    updateBasis(section);
  }

  function paintBadges() {
    var rows = document.querySelectorAll(".dl-deck-row");
    for (var i = 0; i < rows.length; i += 1) {
      var row = rows[i];
      var stale = row.querySelectorAll("[data-evidence-badge]");
      for (var j = stale.length - 1; j >= 0; j -= 1) stale[j].remove();
      var entry = findEntry(row.getAttribute("data-entry-id"));
      var fact = cardFact(entry);
      if (!fact) continue;
      var badge = el("span", "dl-evidence-badge");
      badge.setAttribute("data-evidence-badge", "");
      badge.textContent = percentText(fact) + " · " + payload.denominator + " lists";
      badge.title = fullStatement(fact.lists, payload, commanderLabel(readState()));
      var actions = row.querySelector(".dl-row-actions");
      if (actions) actions.insertBefore(badge, actions.firstChild);
      else row.appendChild(badge);
    }
  }

  function paintPanel() {
    var slot = document.querySelector('[data-card-panel-slot="evidence"]');
    if (!slot) return;
    var entry = findEntry(currentEntryId());
    var wrap = el("div", "dl-evidence-why");
    wrap.appendChild(el("h3", "", "Why this card?"));
    var statement = el("p", "", statementFor(entry));
    statement.setAttribute("data-evidence-statement", "");
    wrap.appendChild(statement);
    var expl = explanationFor(entry);
    var role = expl && expl.role ? String(expl.role) : (entry && entry.role ? String(entry.role) : "");
    if (role) {
      var roleNode = el("p", "", role);
      roleNode.setAttribute("data-evidence-role", "");
      wrap.appendChild(roleNode);
    }
    if (expl) {
      var added = el(
        "p",
        "",
        "Added by the generator as " + expl.source + " (priority " + formatPriority(expl.priority) + ")"
      );
      added.setAttribute("data-evidence-added", "");
      wrap.appendChild(added);
      if (expl.reason) {
        var reason = el("p", "", String(expl.reason));
        reason.setAttribute("data-evidence-reason", "");
        wrap.appendChild(reason);
      }
    }
    slot.replaceChildren(wrap);
  }

  function paint() {
    ensureControls();
    paintBadges();
    paintPanel();
  }

  function fetchEvidence() {
    var state = readState();
    fetchedOracles = oracleKey(state);
    var days = windowDays;
    var token = ++fetchToken;
    var deckId = state && state.id;
    if (!deckId) {
      payload = unavailable(days);
      paint();
      return;
    }
    fetch("/api/decks/" + encodeURIComponent(deckId) + "/evidence?window=" + encodeURIComponent(days))
      .then(function (response) {
        if (!response.ok) throw new Error("status");
        return response.json();
      })
      .then(function (body) {
        if (token !== fetchToken) return;
        payload = body || unavailable(days);
        paint();
        if (oracleKey(readState()) !== fetchedOracles) scheduleRefetch();
      })
      .catch(function () {
        if (token !== fetchToken) return;
        payload = unavailable(days);
        paint();
      });
  }

  function scheduleRefetch() {
    clearTimeout(refetchTimer);
    refetchTimer = setTimeout(fetchEvidence, 500);
  }

  function onRender() {
    paint();
    if (oracleKey(readState()) === fetchedOracles) return;
    scheduleRefetch();
  }

  function watchPanel() {
    var panel = document.querySelector("[data-card-panel]");
    if (!panel || typeof MutationObserver !== "function") return;
    if (panelObserver) panelObserver.disconnect();
    panelObserver = new MutationObserver(function () { paintPanel(); });
    panelObserver.observe(panel, { childList: true });
  }

  function start() {
    if (started) return;
    if (!document.getElementById("deck-document-data") && !window.DeckLabBuilder) return;
    started = true;
    watchPanel();
    fetchEvidence();
  }

  document.addEventListener("deck-lab:ready", start);
  document.addEventListener("deck-lab:render", onRender);
  start();
})();
