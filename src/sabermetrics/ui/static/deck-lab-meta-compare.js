(function () {
  "use strict";

  var diffToken = 0;
  var altToken = 0;
  var observer = null;
  var started = false;

  function readState() {
    if (window.DeckLabBuilder && window.DeckLabBuilder.getState) return window.DeckLabBuilder.getState();
    var data = document.getElementById("deck-document-data");
    if (!data) return null;
    try { return JSON.parse(data.textContent); } catch (error) { return null; }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function windowDays() {
    var select = document.querySelector("[data-evidence-window]");
    if (select && select.value != null && String(select.value) !== "") return String(select.value);
    return "30";
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

  function currentEntry() {
    var id = null;
    if (window.DeckLabCardPanel && window.DeckLabCardPanel.current) id = window.DeckLabCardPanel.current();
    return findEntry(id);
  }

  function addDestinationId() {
    var state = readState() || {};
    var zones = state.zones || [];
    var select = document.querySelector("[data-add-zone]");
    var current = select ? String(select.value || "") : "";
    for (var i = 0; i < zones.length; i += 1) {
      if (String(zones[i].id) === current) return current;
    }
    for (var j = 0; j < zones.length; j += 1) {
      if (String(zones[j].name || "").trim().toLowerCase() === "unsorted") return String(zones[j].id);
    }
    return zones.length ? String(zones[0].id) : "";
  }

  function cohortPhrase(data) {
    var days = Number(data.window_days) || 0;
    var when = days ? "last " + days + " days" : "all time";
    var floor = data.min_event_size == null
      ? "event size not recorded"
      : "events with ≥ " + data.min_event_size + " players";
    return when + ", " + floor;
  }

  function absence(data) {
    var days = data && Number(data.window_days);
    if (days) return "No tournament evidence for this commander in the last " + days + " days";
    return "No tournament evidence for this commander";
  }

  function basisSentence(data) {
    if (!data || !data.available || !Number(data.denominator)) return absence(data || { window_days: windowDays() });
    return "Based on " + data.denominator + " recorded lists, " + cohortPhrase(data);
  }

  function rateStatement(lists, data, commander) {
    var denominator = Number(data && data.denominator) || 0;
    var count = Number(lists) || 0;
    if (!denominator || !count) return "";
    var pct = Math.round((count / denominator) * 100);
    return pct + "% · In " + count + " of " + denominator + " recorded " + commander + " lists, " + cohortPhrase(data);
  }

  function openDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function send(commands) {
    var api = window.DeckLabBuilder;
    if (!api || !api.command || !commands.length) return;
    api.command(commands);
  }

  function addStaple(card) {
    send([{
      type: "add_card",
      card_id: card.card_id,
      zone_id: addDestinationId(),
      quantity: 1
    }]);
  }

  function replaceEntry(entry, card) {
    if (!entry || !card) return;
    send([
      { type: "remove_entry", entry_id: entry.id },
      {
        type: "add_card",
        card_id: card.card_id,
        zone_id: entry.zone_id,
        quantity: Number(entry.quantity) || 1,
        role: entry.role || ""
      }
    ]);
  }

  function renderChoices(container, entry, body) {
    container.replaceChildren();
    if (!body) {
      container.appendChild(el("p", "dl-meta-note", "Tournament evidence is loading"));
      return;
    }
    if (body.needs_role) {
      container.appendChild(el(
        "p",
        "dl-meta-note",
        "This card needs a role before replacements can be listed."
      ));
      return;
    }
    if (!body.denominator || body.available === false) {
      container.appendChild(el("p", "dl-meta-absence", absence(body)));
      return;
    }
    var alternatives = body.alternatives || [];
    if (!alternatives.length) {
      container.appendChild(el("p", "dl-meta-note", "No same-role card in the recorded lists can replace this one."));
      return;
    }
    var commander = commanderLabel(readState());
    alternatives.forEach(function (card) {
      var button = el("button", "dl-button", card.name);
      button.type = "button";
      button.setAttribute("data-replace-option", "");
      button.setAttribute("aria-label", "Replace with " + card.name);
      var statement = rateStatement(card.lists, body, commander);
      if (statement) button.setAttribute("title", statement);
      var row = el("div", "dl-meta-row");
      row.setAttribute("data-replace-row", "");
      var copy = el("p", "", statement || card.name);
      copy.setAttribute("data-meta-statement", "");
      button.addEventListener("click", function () { replaceEntry(entry, card); });
      row.append(button, copy);
      container.appendChild(row);
    });
  }

  function loadAlternatives(entry, container) {
    var state = readState();
    var deckId = state && state.id;
    var token = ++altToken;
    container.replaceChildren(el("p", "dl-meta-note", "Tournament evidence is loading"));
    if (!deckId || !entry || !entry.oracle_id) {
      renderChoices(container, entry, { needs_role: true });
      return;
    }
    var url = "/api/decks/" + encodeURIComponent(deckId) + "/alternatives/"
      + encodeURIComponent(entry.oracle_id) + "?window=" + encodeURIComponent(windowDays());
    fetch(url)
      .then(function (response) {
        if (!response.ok) throw new Error("status");
        return response.json();
      })
      .then(function (body) {
        if (token !== altToken) return;
        renderChoices(container, entry, body || { needs_role: true });
      })
      .catch(function () {
        if (token !== altToken) return;
        container.replaceChildren(el("p", "dl-meta-absence", absence({ window_days: Number(windowDays()) || 0 })));
      });
  }

  function paintReplace() {
    if (window.DeckLabBuilder && window.DeckLabBuilder.shared) return;
    var slot = document.querySelector('[data-card-panel-slot="evidence"]');
    if (!slot) return;
    var entry = currentEntry();
    var button = slot.querySelector("[data-replace-with]");
    if (!entry || entry.is_commander) {
      if (button) button.remove();
      var stale = slot.querySelector("[data-replace-list]");
      if (stale) stale.remove();
      return;
    }
    if (button) {
      button.setAttribute("aria-label", "Replace " + (entry.name || "card") + " with an alternative");
      return;
    }
    if (!button) {
      button = el("button", "dl-button", "Replace with…");
      button.type = "button";
      button.setAttribute("data-replace-with", "");
      button.setAttribute("aria-label", "Replace " + (entry.name || "card") + " with an alternative");
      button.addEventListener("click", function () {
        var list = slot.querySelector("[data-replace-list]");
        if (!list) {
          list = el("div", "dl-replace-list");
          list.setAttribute("data-replace-list", "");
          slot.appendChild(list);
        }
        loadAlternatives(currentEntry() || entry, list);
      });
      slot.appendChild(button);
    }
  }

  function section(title, attr) {
    var wrap = el("section", "dl-meta-section");
    wrap.setAttribute(attr, "");
    wrap.appendChild(el("h3", "", title));
    return wrap;
  }

  function renderDiff(body) {
    var host = document.querySelector("[data-meta-compare-body]");
    if (!host) return;
    host.replaceChildren();
    var data = body || { window_days: Number(windowDays()) || 0, denominator: 0, available: false };
    var basis = el("p", "dl-meta-basis", basisSentence(data));
    basis.setAttribute("data-meta-compare-basis", "");
    host.appendChild(basis);
    var staples = section("Staples you're missing", "data-meta-staples");
    var unplayed = section("In your list, in no recorded list", "data-meta-unplayed");
    host.append(staples, unplayed);
    if (!data.available || !Number(data.denominator)) {
      staples.appendChild(el("p", "dl-meta-absence", absence(data)));
      unplayed.appendChild(el("p", "dl-meta-absence", absence(data)));
      return;
    }
    var commander = commanderLabel(readState());
    var missing = data.missing_staples || [];
    if (!missing.length) {
      staples.appendChild(el("p", "dl-meta-note", "No staples in recorded lists are missing from your library."));
    }
    missing.forEach(function (card) {
      var row = el("div", "dl-meta-row");
      row.setAttribute("data-meta-staple-row", "");
      row.appendChild(el("strong", "", card.name || "Card"));
      var statement = rateStatement(card.lists, data, commander);
      var copy = el("p", "", statement);
      copy.setAttribute("data-meta-statement", "");
      var add = el("button", "dl-button", "Add");
      add.type = "button";
      add.setAttribute("data-meta-add", "");
      add.setAttribute("aria-label", "Add " + (card.name || "card"));
      add.addEventListener("click", function () { addStaple(card); });
      row.append(copy, add);
      staples.appendChild(row);
    });
    if (data.too_few_lists) {
      unplayed.appendChild(el(
        "p",
        "dl-meta-absence",
        "Fewer than 10 recorded lists, so unplayed cards are not shown."
      ));
      return;
    }
    var quiet = data.unplayed || [];
    if (!quiet.length) {
      unplayed.appendChild(el("p", "dl-meta-note", "Every library card appears in at least one recorded list."));
    }
    quiet.forEach(function (card) {
      var row = el("div", "dl-meta-row");
      row.setAttribute("data-meta-unplayed-row", "");
      row.appendChild(el("strong", "", card.name || "Card"));
      var find = el("button", "dl-button", "Find replacement");
      find.type = "button";
      find.setAttribute("data-meta-find", "");
      find.setAttribute("aria-label", "Find replacement for " + (card.name || "card"));
      var list = el("div", "dl-replace-list");
      list.setAttribute("data-replace-list", "");
      list.hidden = true;
      find.addEventListener("click", function () {
        list.hidden = false;
        var entry = findEntry(card.entry_id) || {
          id: card.entry_id,
          oracle_id: card.oracle_id,
          name: card.name,
          zone_id: card.zone_id,
          role: card.role,
          quantity: 1
        };
        loadAlternatives(entry, list);
      });
      row.append(find, list);
      unplayed.appendChild(row);
    });
  }

  function loadDiff() {
    var host = document.querySelector("[data-meta-compare-body]");
    if (host) host.replaceChildren(el("p", "dl-meta-note", "Tournament evidence is loading"));
    var state = readState();
    var deckId = state && state.id;
    var token = ++diffToken;
    var days = windowDays();
    if (!deckId) {
      renderDiff({ window_days: Number(days) || 0, denominator: 0, available: false, missing_staples: [], unplayed: [], too_few_lists: true });
      return;
    }
    fetch("/api/decks/" + encodeURIComponent(deckId) + "/meta-diff?window=" + encodeURIComponent(days))
      .then(function (response) {
        if (!response.ok) throw new Error("status");
        return response.json();
      })
      .then(function (body) {
        if (token !== diffToken) return;
        renderDiff(body);
      })
      .catch(function () {
        if (token !== diffToken) return;
        renderDiff({ window_days: Number(days) || 0, denominator: 0, available: false, missing_staples: [], unplayed: [], too_few_lists: true });
      });
  }

  function watchPanel() {
    var panel = document.querySelector("[data-card-panel]");
    if (!panel || typeof MutationObserver !== "function") return;
    if (observer) observer.disconnect();
    observer = new MutationObserver(function () { paintReplace(); });
    observer.observe(panel, { childList: true, subtree: true });
  }

  function onRender() {
    paintReplace();
  }

  function start() {
    if (started) return;
    if (!document.getElementById("deck-document-data") && !window.DeckLabBuilder) return;
    started = true;
    var opener = document.querySelector("[data-meta-compare-open]");
    if (opener) {
      opener.addEventListener("click", function () {
        openDialog(document.querySelector("[data-meta-compare]"));
        loadDiff();
      });
    }
    var railOpener = document.querySelector('[data-rail-pane="tools"] [data-meta-compare-open]');
    if (railOpener && railOpener !== opener) {
      railOpener.addEventListener("click", function () {
        openDialog(document.querySelector("[data-meta-compare]"));
        loadDiff();
      });
    }
    document.addEventListener("change", function (event) {
      var target = event.target;
      if (!target || !target.hasAttribute || !target.hasAttribute("data-evidence-window")) return;
      var dialog = document.querySelector("[data-meta-compare]");
      if (dialog && (dialog.open || dialog.hasAttribute("open"))) loadDiff();
    });
    watchPanel();
    paintReplace();
  }

  if (window.DeckLabBuilder) {
    start();
    if (window.DeckLabBuilder.onRender) window.DeckLabBuilder.onRender(onRender);
  } else {
    document.addEventListener("deck-lab:ready", function () {
      start();
      if (window.DeckLabBuilder && window.DeckLabBuilder.onRender) window.DeckLabBuilder.onRender(onRender);
    });
  }
})();
